"""Conversation turns over stored workspace sessions.

See ``docs/development/workspaces-and-runtime.md`` for how session persistence
and runtime concurrency compose.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Literal, cast

from pydantic import ValidationError

from ethos.config import get_settings
from ethos.models import (
    FinishReason,
    Message,
    Model,
    ModelRequest,
    ModelResponse,
    ReasoningDelta,
    ReasoningPart,
    Role,
    TextDelta,
    TextPart,
    ToolCallPart,
    ToolDefinition,
    ToolResultPart,
    Usage,
)
from ethos.provider import AIProvider, ModelProtocolError
from ethos.sessions import ApprovalStateError, SessionManager
from ethos.tools import (
    Allow,
    ApprovalState,
    PreparedToolCall,
    RequireApproval,
    ToolApproval,
    ToolExecutor,
    ToolRegistry,
    approval_request_id,
)

type ModelFactory = Callable[[], Model]

MAX_MODEL_ROUNDS = 8
MAX_TOOL_CALLS_PER_RESPONSE = 16


class AgentLimitError(RuntimeError):
    """The model exceeded a bounded agent-loop limit."""


@dataclass(frozen=True)
class PromptStreamEvent:
    """Provider-neutral prompt text and usage update."""

    text: str = ""
    text_kind: Literal["answer", "reasoning"] = "answer"
    usage: Usage | None = None
    done: bool = False


@dataclass(frozen=True)
class ApprovalStreamEvent:
    approval: ToolApproval


type RuntimeStreamEvent = PromptStreamEvent | ApprovalStreamEvent


class AgentRuntime:
    """Run isolated conversations through Ethos model contracts.

    Conversation history belongs to sessions, not models. In-process and file
    locks serialise turns per session across runtime instances and processes.
    """

    def __init__(
        self,
        sessions: SessionManager,
        model_factory: ModelFactory | None = None,
        tool_registry: ToolRegistry | None = None,
        tool_executor: ToolExecutor | None = None,
        *,
        max_model_rounds: int = MAX_MODEL_ROUNDS,
        max_tool_calls_per_response: int = MAX_TOOL_CALLS_PER_RESPONSE,
    ) -> None:
        if max_model_rounds < 1:
            raise ValueError("max_model_rounds must be positive")
        if max_tool_calls_per_response < 1:
            raise ValueError("max_tool_calls_per_response must be positive")
        self._sessions = sessions
        self._model_factory = (
            model_factory if model_factory is not None else _model_from_settings
        )
        self._tool_registry = (
            tool_registry if tool_registry is not None else ToolRegistry()
        )
        self._tool_executor = (
            tool_executor
            if tool_executor is not None
            else ToolExecutor(self._tool_registry)
        )
        self._max_model_rounds = max_model_rounds
        self._max_tool_calls_per_response = max_tool_calls_per_response
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def run(
        self,
        prompt: str,
        workspace_name: str,
        session_id: str,
    ) -> AsyncIterator[RuntimeStreamEvent]:
        """Stream and commit one turn for an active session.

        Text may be yielded before its response is durable. Assistant tool
        calls and individual results are checkpointed as the loop advances.
        Cancelling or abandoning the iterator preserves the latest completed
        checkpoint. ``done=True`` follows the durable final response.
        """
        key = (workspace_name, session_id)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            with self._sessions.runtime_lock(workspace_name, session_id):
                session = self._sessions.recover_executing_approvals(
                    workspace_name, session_id
                )
                if session.archived:
                    raise ValueError(f"session is archived: {session_id}")
                _validate_tool_history(session.messages)

                user_message = Message(
                    role=Role.USER,
                    parts=(TextPart(text=prompt),),
                )
                messages = (*session.messages, user_message)
                model = self._model_factory()
                tools = (
                    self._tool_registry.definitions
                    if model.features.tools
                    else ()
                )
                async for event in self._continue(
                    workspace_name,
                    session_id,
                    messages,
                    model,
                    tools,
                    Usage(),
                    round_number=1,
                ):
                    yield event

    async def resolve_approval(
        self,
        workspace_name: str,
        session_id: str,
        approval_id: str,
        *,
        approved: bool,
    ) -> AsyncIterator[RuntimeStreamEvent]:
        """Consume one pending approval and resume its interrupted turn."""
        key = (workspace_name, session_id)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            with self._sessions.runtime_lock(workspace_name, session_id):
                async for event in self._resolve_approval_locked(
                    workspace_name,
                    session_id,
                    approval_id,
                    approved=approved,
                ):
                    yield event

    async def _resolve_approval_locked(
        self,
        workspace_name: str,
        session_id: str,
        approval_id: str,
        *,
        approved: bool,
    ) -> AsyncIterator[RuntimeStreamEvent]:
        session = self._sessions.recover_executing_approvals(
            workspace_name, session_id
        )
        if session.archived:
            raise ValueError(f"session is archived: {session_id}")
        approval = self._sessions.get_approval(
            workspace_name, session_id, approval_id
        )
        if approval.state is not ApprovalState.PENDING:
            raise ApprovalStateError(
                f"approval request is {approval.state.value}: {approval_id}"
            )
        call = _approval_call(session.messages, approval)
        messages = session.messages

        if approved:
            prepared = _restore_prepared_call(
                self._tool_registry, approval, call
            )
            self._sessions.transition_approval(
                workspace_name,
                session_id,
                approval_id,
                expected=ApprovalState.PENDING,
                state=ApprovalState.EXECUTING,
            )
            result = await self._tool_executor.run(prepared)
            state = ApprovalState.COMPLETED
        else:
            result = ToolResultPart(
                call_id=call.call_id,
                name=call.name,
                content="tool execution denied",
                is_error=True,
            )
            state = ApprovalState.DENIED

        messages = (*messages, Message(role=Role.TOOL, parts=(result,)))
        self._sessions.transition_approval(
            workspace_name,
            session_id,
            approval_id,
            expected=(
                ApprovalState.EXECUTING if approved else ApprovalState.PENDING
            ),
            state=state,
            result=result,
            messages=messages,
        )

        model = self._model_factory()
        tools = self._tool_registry.definitions if model.features.tools else ()
        pending_calls = _unresolved_tool_calls(messages)
        async for event in self._continue(
            workspace_name,
            session_id,
            messages,
            model,
            tools,
            approval.usage,
            round_number=(
                approval.round_number
                if pending_calls
                else approval.round_number + 1
            ),
            pending_calls=pending_calls,
        ):
            yield event

    async def _continue(
        self,
        workspace_name: str,
        session_id: str,
        messages: tuple[Message, ...],
        model: Model,
        tools: tuple[ToolDefinition, ...],
        usage: Usage,
        *,
        round_number: int,
        pending_calls: tuple[ToolCallPart, ...] = (),
    ) -> AsyncIterator[RuntimeStreamEvent]:
        while round_number <= self._max_model_rounds:
            if pending_calls:
                for call in pending_calls:
                    prepared = await self._tool_executor.prepare(call)
                    if isinstance(prepared, ToolResultPart):
                        result = prepared
                    elif isinstance(prepared.decision, RequireApproval):
                        arguments = cast(
                            dict[str, object],
                            prepared.arguments.model_dump(mode="json"),
                        )
                        approval = ToolApproval(
                            id=approval_request_id(session_id, call.call_id),
                            call=call,
                            tool_name=prepared.tool.definition.name,
                            arguments=arguments,
                            effect=prepared.tool.effect,
                            reason=prepared.decision.reason,
                            round_number=round_number,
                            usage=usage,
                        )
                        self._sessions.add_approval(
                            workspace_name, session_id, approval
                        )
                        yield ApprovalStreamEvent(approval)
                        return
                    else:
                        result = await self._tool_executor.run(prepared)
                    messages = (
                        *messages,
                        Message(role=Role.TOOL, parts=(result,)),
                    )
                    self._sessions.replace_messages(
                        workspace_name,
                        session_id,
                        messages,
                    )
                pending_calls = ()
                round_number += 1
                continue

            request = ModelRequest(messages=messages, tools=tools)
            streamed_text = ""
            streamed_reasoning = ""
            completed: ModelResponse | None = None

            async for event in model.stream(request):
                if completed is not None:
                    raise ModelProtocolError("model streamed after completion")
                if isinstance(event, TextDelta):
                    streamed_text += event.text
                    if event.text:
                        yield PromptStreamEvent(text=event.text)
                elif isinstance(event, ReasoningDelta):
                    streamed_reasoning += event.text
                    if event.text:
                        yield PromptStreamEvent(
                            text=event.text,
                            text_kind="reasoning",
                        )
                else:
                    completed = event.response

            if completed is None:
                raise ModelProtocolError("model stream ended before completion")
            assistant_message = _assistant_message(
                completed,
                streamed_text,
                streamed_reasoning,
            )
            calls = tuple(
                part
                for part in completed.parts
                if isinstance(part, ToolCallPart)
            )
            usage = _add_usage(usage, completed.usage)

            if not calls:
                messages = (*messages, assistant_message)
                self._sessions.replace_messages(
                    workspace_name,
                    session_id,
                    messages,
                )
                yield PromptStreamEvent(usage=usage, done=True)
                return

            if not tools:
                raise ModelProtocolError(
                    "text runtime received unsupported parts"
                )
            if completed.finish_reason not in (
                FinishReason.TOOL_CALL,
                FinishReason.OTHER,
            ):
                raise ModelProtocolError(
                    "tool response has contradictory finish reason"
                )

            messages = (*messages, assistant_message)
            self._sessions.replace_messages(
                workspace_name,
                session_id,
                messages,
            )

            limit_error: str | None = None
            if len(calls) > self._max_tool_calls_per_response:
                limit_error = "tool call limit exceeded"
            elif round_number == self._max_model_rounds:
                limit_error = "model round limit exceeded"
            if limit_error is not None:
                for call in calls:
                    messages = (*messages, _tool_error(call, limit_error))
                    self._sessions.replace_messages(
                        workspace_name,
                        session_id,
                        messages,
                    )
                raise AgentLimitError(limit_error)

            pending_calls = calls

        raise AssertionError("unreachable model round")


def _model_from_settings() -> Model:
    settings = get_settings()
    provider = AIProvider.from_settings(settings)
    return provider.model(
        settings.provider.model_name,
        settings.provider.reasoning_effort,
    )


def _assistant_message(
    response: ModelResponse,
    streamed_text: str,
    streamed_reasoning: str,
) -> Message:
    text = "".join(
        part.text for part in response.parts if isinstance(part, TextPart)
    )
    reasoning = "".join(
        part.text for part in response.parts if isinstance(part, ReasoningPart)
    )
    if text != streamed_text or reasoning != streamed_reasoning:
        raise ModelProtocolError("model completion did not match stream")
    return Message(role=Role.ASSISTANT, parts=response.parts)


def _tool_error(call: ToolCallPart, content: str) -> Message:
    return Message(
        role=Role.TOOL,
        parts=(
            ToolResultPart(
                call_id=call.call_id,
                name=call.name,
                content=content,
                is_error=True,
            ),
        ),
    )


def _add_usage(first: Usage, second: Usage) -> Usage:
    return Usage(
        input_tokens=first.input_tokens + second.input_tokens,
        output_tokens=first.output_tokens + second.output_tokens,
    )


def _validate_tool_history(messages: tuple[Message, ...]) -> None:
    if _unresolved_tool_calls(messages):
        raise ModelProtocolError(
            "session contains unresolved tool call history"
        )


def _unresolved_tool_calls(
    messages: tuple[Message, ...],
) -> tuple[ToolCallPart, ...]:
    unresolved: dict[str, ToolCallPart] = {}
    call_ids: set[str] = set()
    for message in messages:
        if unresolved and message.role is not Role.TOOL:
            raise ModelProtocolError(
                "session contains unresolved tool call history"
            )
        if message.role is Role.ASSISTANT:
            for part in message.parts:
                if not isinstance(part, ToolCallPart):
                    continue
                if part.call_id in call_ids:
                    raise ModelProtocolError(
                        "session contains unresolved tool call history"
                    )
                call_ids.add(part.call_id)
                unresolved[part.call_id] = part
        elif message.role is Role.TOOL:
            part = message.parts[0]
            if not isinstance(part, ToolResultPart):
                raise AssertionError("tool message without tool result")
            call = unresolved.get(part.call_id)
            if call is None or call.name != part.name:
                raise ModelProtocolError(
                    "session contains unresolved tool call history"
                )
            del unresolved[part.call_id]
    return tuple(unresolved.values())


def _approval_call(
    messages: tuple[Message, ...], approval: ToolApproval
) -> ToolCallPart:
    unresolved = {
        call.call_id: call for call in _unresolved_tool_calls(messages)
    }
    call = unresolved.get(approval.call.call_id)
    if call is None:
        raise ApprovalStateError(f"approval call is stale: {approval.id}")
    if call != approval.call:
        raise ApprovalStateError(
            f"approval call payload changed: {approval.id}"
        )
    return call


def _restore_prepared_call(
    registry: ToolRegistry,
    approval: ToolApproval,
    call: ToolCallPart,
) -> PreparedToolCall:
    tool = registry.get(approval.tool_name)
    if tool is None or tool.effect is not approval.effect:
        raise ApprovalStateError(f"approval tool changed: {approval.id}")
    try:
        arguments = tool.arguments_type.model_validate(approval.arguments)
    except ValidationError as error:
        raise ApprovalStateError(
            f"approval arguments changed: {approval.id}"
        ) from error
    validated = cast(dict[str, object], arguments.model_dump(mode="json"))
    if validated != approval.arguments:
        raise ApprovalStateError(f"approval arguments changed: {approval.id}")
    return PreparedToolCall(call, tool, arguments, Allow())
