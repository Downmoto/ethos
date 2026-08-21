"""Conversation turns over stored workspace sessions.

See ``docs/development/workspaces-and-runtime.md`` for how session persistence
and runtime concurrency compose.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from ethos.config import get_settings
from ethos.models import (
    FinishReason,
    Message,
    Model,
    ModelRequest,
    ModelResponse,
    Role,
    TextDelta,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    Usage,
)
from ethos.provider import AIProvider, ModelProtocolError
from ethos.sessions import SessionManager
from ethos.tools import ToolExecutor, ToolRegistry

type ModelFactory = Callable[[], Model]

MAX_MODEL_ROUNDS = 8
MAX_TOOL_CALLS_PER_RESPONSE = 16


class AgentLimitError(RuntimeError):
    """The model exceeded a bounded agent-loop limit."""


@dataclass(frozen=True)
class PromptStreamEvent:
    """Provider-neutral prompt text and usage update."""

    text: str = ""
    usage: Usage | None = None
    done: bool = False


class AgentRuntime:
    """Run isolated conversations through Ethos model contracts.

    Conversation history belongs to sessions, not models. Locks serialise
    turns per session within this runtime instance; they do not coordinate
    separate processes or runtimes.
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
    ) -> AsyncIterator[PromptStreamEvent]:
        """Stream and commit one turn for an active session.

        Text may be yielded before its response is durable. Assistant tool
        calls and individual results are checkpointed as the loop advances.
        Cancelling or abandoning the iterator preserves the latest completed
        checkpoint. ``done=True`` follows the durable final response.
        """
        key = (workspace_name, session_id)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            session = self._sessions.get(workspace_name, session_id)
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
                self._tool_registry.definitions if model.features.tools else ()
            )
            usage = Usage()

            for round_number in range(1, self._max_model_rounds + 1):
                request = ModelRequest(messages=messages, tools=tools)
                streamed_text = ""
                completed: ModelResponse | None = None

                async for event in model.stream(request):
                    if completed is not None:
                        raise ModelProtocolError(
                            "model streamed after completion"
                        )
                    if isinstance(event, TextDelta):
                        streamed_text += event.text
                        if event.text:
                            yield PromptStreamEvent(text=event.text)
                    else:
                        completed = event.response

                if completed is None:
                    raise ModelProtocolError(
                        "model stream ended before completion"
                    )
                assistant_message = _assistant_message(completed, streamed_text)
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

                for call in calls:
                    result = await self._tool_executor.execute(call)
                    messages = (
                        *messages,
                        Message(role=Role.TOOL, parts=(result,)),
                    )
                    self._sessions.replace_messages(
                        workspace_name,
                        session_id,
                        messages,
                    )

            raise AssertionError("unreachable model round")


def _model_from_settings() -> Model:
    settings = get_settings()
    provider = AIProvider.from_settings(settings)
    return provider.model(settings.provider.model_name)


def _assistant_message(response: ModelResponse, streamed_text: str) -> Message:
    parts = tuple(part for part in response.parts if isinstance(part, TextPart))
    if "".join(part.text for part in parts) != streamed_text:
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
    for index, message in enumerate(messages):
        if message.role is not Role.ASSISTANT:
            continue
        for part in message.parts:
            if not isinstance(part, ToolCallPart):
                continue
            results = sum(
                isinstance(later.parts[0], ToolResultPart)
                and later.parts[0].call_id == part.call_id
                for later in messages[index + 1 :]
                if later.role is Role.TOOL
            )
            if results != 1:
                raise ModelProtocolError(
                    "session contains unresolved tool call history"
                )
