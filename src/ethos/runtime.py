"""Conversation turns over stored workspace sessions.

See ``docs/development/workspaces-and-runtime.md`` for how session persistence
and runtime concurrency compose.
"""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterable
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Final, Literal, cast
from uuid import UUID, uuid4

from pydantic import ConfigDict, Field, ValidationError, field_validator

from ethos.capabilities import Capability, RunContext
from ethos.config import get_settings
from ethos.context import ContextBuilder
from ethos.events import event_factory
from ethos.events.emitters import EnvelopeEventEmitter
from ethos.events.models import EventPayload, NonEmptyString
from ethos.events.types import EventType
from ethos.models import (
    FinishReason,
    Message,
    Model,
    ModelRequest,
    ModelResponse,
    ReasoningDelta,
    ReasoningEffort,
    ReasoningPart,
    Role,
    TextDelta,
    TextPart,
    ToolCallPart,
    ToolDefinition,
    ToolResultPart,
    Usage,
)
from ethos.provider import AIProvider, ModelProtocolError, ModelProviderError
from ethos.sessions import ApprovalStateError, Session, SessionManager
from ethos.tools import (
    Allow,
    ApprovalState,
    PreparedToolCall,
    RejectedToolCall,
    RequireApproval,
    ToolApproval,
    ToolEffect,
    ToolExecutionIndeterminateError,
    ToolExecutor,
    ToolOutput,
    ToolOutputStream,
    ToolPolicyError,
    ToolPreparationOutcome,
    ToolRegistry,
    approval_request_id,
)

type ModelFactory = Callable[[], Model]
type CapabilityResolver = Callable[[RunContext], Iterable[Capability]]

MAX_MODEL_ROUNDS = 8
MAX_TOOL_CALLS_PER_RESPONSE = 16
ANSWER_NOW_INSTRUCTION: Final = (
    "Your reasoning deadline was reached. Do not produce more reasoning. "
    "Continue the task and provide the final answer as soon as possible; "
    "use a tool only if required."
)
INTERRUPTED_TOOL_RESULT: Final = (
    "interrupted tool call was not replayed; execution outcome is unknown"
)


def _write_context_diagnostic(path: Path | None, request: ModelRequest) -> None:
    if path is None:
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(request.model_dump_json(indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


class AgentLimitError(RuntimeError):
    """The runtime exhausted a bounded reasoning, model, or tool-call limit."""


class RuntimeEventError(RuntimeError):
    """A runtime trace event could not be emitted."""


class RuntimeFailure(StrEnum):
    PROVIDER = "provider"
    PROTOCOL = "protocol"
    PERSISTENCE = "persistence"
    LIMIT = "limit"
    POLICY = "policy"
    INTERNAL = "internal"


class RuntimeEventPayload(EventPayload):
    """Common correlation fields for one agent run event."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_name: NonEmptyString | None = "runtime.trace"
    schema_version: int = 1
    run_id: UUID
    workspace_name: NonEmptyString
    session_id: UUID
    round_number: Annotated[int, Field(ge=1)]

    @field_validator("schema_name")
    @classmethod
    def schema_name_must_be_runtime_trace(cls, value: str | None) -> str:
        if value != "runtime.trace":
            raise ValueError("runtime event schema name must be runtime.trace")
        return value

    @field_validator("schema_version")
    @classmethod
    def schema_version_must_be_one(cls, value: int) -> int:
        if value != 1:
            raise ValueError("runtime event schema version must be 1")
        return value


class RunFailedEventPayload(RuntimeEventPayload):
    failure: RuntimeFailure


class ModelCompletedEventPayload(RuntimeEventPayload):
    finish_reason: FinishReason
    usage: Usage
    provider_response_id: str | None = None


class ModelFailedEventPayload(RuntimeEventPayload):
    failure: RuntimeFailure


class ToolEventPayload(RuntimeEventPayload):
    call_id: NonEmptyString
    tool_name: NonEmptyString


class ToolPreparedEventPayload(ToolEventPayload):
    outcome: ToolPreparationOutcome
    effect: ToolEffect | None = None


class ToolExecutionEventPayload(ToolEventPayload):
    effect: ToolEffect


class ToolCompletedEventPayload(ToolExecutionEventPayload):
    is_error: bool


class ApprovalEventPayload(ToolExecutionEventPayload):
    approval_id: UUID


class ApprovalRequestedEventPayload(ApprovalEventPayload):
    reason: NonEmptyString


@dataclass(frozen=True)
class PromptStreamEvent:
    """Provider-neutral prompt text and usage update."""

    text: str = ""
    text_kind: Literal["answer", "reasoning"] = "answer"
    usage: Usage | None = None
    done: bool = False


@dataclass(frozen=True)
class ApprovalStreamEvent:
    """Pause the stream after its approval request is durable."""

    approval: ToolApproval


@dataclass(frozen=True)
class ToolOutputStreamEvent:
    """Transient output from one executing tool call."""

    call_id: str
    tool_name: str
    stream: ToolOutputStream
    text: str


@dataclass
class _RunProgress:
    round_number: int


type RuntimeStreamEvent = (
    PromptStreamEvent | ApprovalStreamEvent | ToolOutputStreamEvent
)


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
        capabilities: Iterable[Capability] = (),
        capability_resolver: CapabilityResolver | None = None,
        *,
        events: EnvelopeEventEmitter,
        max_model_rounds: int = MAX_MODEL_ROUNDS,
        max_tool_calls_per_response: int = MAX_TOOL_CALLS_PER_RESPONSE,
        answer_model_factory: ModelFactory | None = None,
        answer_now_after_seconds: float = 60.0,
        context_diagnostic_path: Path | None = None,
    ) -> None:
        if max_model_rounds < 1:
            raise ValueError("max_model_rounds must be positive")
        if max_tool_calls_per_response < 1:
            raise ValueError("max_tool_calls_per_response must be positive")
        if answer_now_after_seconds <= 0:
            raise ValueError("answer_now_after_seconds must be positive")
        self._sessions = sessions
        self._events = events
        self._context_builder = ContextBuilder()
        self._model_factory = (
            model_factory if model_factory is not None else _model_from_settings
        )
        self._answer_model_factory = (
            answer_model_factory
            if answer_model_factory is not None
            else (
                model_factory
                if model_factory is not None
                else _answer_model_from_settings
            )
        )
        self._tool_registry = (
            tool_registry if tool_registry is not None else ToolRegistry()
        )
        self._tool_executor = (
            tool_executor
            if tool_executor is not None
            else ToolExecutor(self._tool_registry)
        )
        self._capabilities = tuple(capabilities)
        self._capability_resolver = capability_resolver
        self._max_model_rounds = max_model_rounds
        self._max_tool_calls_per_response = max_tool_calls_per_response
        self._answer_now_after_seconds = answer_now_after_seconds
        self._context_diagnostic_path = context_diagnostic_path
        self._locks: dict[tuple[str, str], tuple[asyncio.Lock, int]] = {}

    @asynccontextmanager
    async def _session_lock(
        self, workspace_name: str, session_id: str
    ) -> AsyncGenerator[None]:
        """Serialise local waiters and discard idle per-session locks."""

        key = (workspace_name, session_id)
        entry = self._locks.get(key)
        lock, users = entry if entry is not None else (asyncio.Lock(), 0)
        self._locks[key] = (lock, users + 1)
        try:
            async with lock:
                yield
        finally:
            users = self._locks[key][1]
            if users == 1:
                del self._locks[key]
            else:
                self._locks[key] = (lock, users - 1)

    async def run(
        self,
        prompt: str,
        workspace_name: str,
        session_id: str,
        *,
        event_location: str = "runtime",
    ) -> AsyncIterator[RuntimeStreamEvent]:
        """Stream and commit one turn for an active session.

        Text may be yielded before its response is durable. Assistant tool
        calls and individual results are checkpointed as the loop advances.
        Cancelling or abandoning the iterator preserves the latest completed
        checkpoint. ``done=True`` follows the durable final response.

        Usage is accumulated across model rounds. Tool-call and model-round
        limits close pending calls with durable error results before raising.
        A reasoning-only response switches to the bounded, no-reasoning
        fallback path rather than leaving the turn without an answer.
        """
        async with self._session_lock(workspace_name, session_id):
            with self._sessions.runtime_lock(workspace_name, session_id):
                session = await self._recover_executing_approvals(
                    workspace_name,
                    session_id,
                    event_location,
                )
                if session.archived:
                    raise ValueError(f"session is archived: {session_id}")
                _validate_tool_history(session.messages)

                run_id = uuid4()
                await self._emit(
                    EventType.RUN_STARTED,
                    RuntimeEventPayload(
                        run_id=run_id,
                        workspace_name=workspace_name,
                        session_id=UUID(session_id),
                        round_number=1,
                    ),
                    event_location,
                )

                user_message = Message(
                    role=Role.USER,
                    parts=(TextPart(text=prompt),),
                )
                messages: tuple[Message, ...] = session.messages + (
                    user_message,
                )
                progress = _RunProgress(round_number=1)
                try:
                    (
                        run_context,
                        instructions,
                        registry,
                        executor,
                    ) = await self._resolve_capabilities(
                        workspace_name,
                        session_id,
                    )
                    model = self._model_factory()
                    tools = registry.definitions if model.features.tools else ()
                    async for event in self._continue(
                        workspace_name,
                        session_id,
                        messages,
                        model,
                        tools,
                        run_context,
                        instructions,
                        executor,
                        Usage(),
                        run_id=run_id,
                        event_location=event_location,
                        round_number=1,
                        progress=progress,
                    ):
                        yield event
                except (asyncio.CancelledError, RuntimeEventError):
                    raise
                except Exception as error:
                    await self._emit_run_failed(
                        run_id,
                        workspace_name,
                        session_id,
                        progress.round_number,
                        event_location,
                        error,
                    )
                    raise

    async def resolve_approval(
        self,
        workspace_name: str,
        session_id: str,
        approval_id: str,
        *,
        approved: bool,
        event_location: str = "runtime",
    ) -> AsyncIterator[RuntimeStreamEvent]:
        """Consume one pending approval and resume its interrupted turn."""
        async with self._session_lock(workspace_name, session_id):
            with self._sessions.runtime_lock(workspace_name, session_id):
                stream = self._resolve_approval_locked(
                    workspace_name,
                    session_id,
                    approval_id,
                    approved=approved,
                    event_location=event_location,
                )
                async with aclosing(stream):
                    async for event in stream:
                        yield event

    async def recover(
        self,
        workspace_name: str,
        session_id: str,
        *,
        event_location: str = "runtime",
    ) -> Session:
        """Close interrupted tool calls without replaying their side effects."""
        async with self._session_lock(workspace_name, session_id):
            with self._sessions.runtime_lock(workspace_name, session_id):
                session = await self._recover_executing_approvals(
                    workspace_name,
                    session_id,
                    event_location,
                )
                if session.archived:
                    raise ValueError(f"session is archived: {session_id}")
                calls = _unresolved_tool_calls(session.messages)
                if not calls:
                    raise ApprovalStateError(
                        "session has no unresolved tool calls"
                    )

                messages = session.messages
                for call in calls:
                    result = ToolResultPart(
                        call_id=call.call_id,
                        name=call.name,
                        content=INTERRUPTED_TOOL_RESULT,
                        is_error=True,
                    )
                    messages += (Message(role=Role.TOOL, parts=(result,)),)
                    approval = next(
                        (
                            item
                            for item in session.approvals
                            if item.call == call
                            and item.state is ApprovalState.PENDING
                        ),
                        None,
                    )
                    if approval is None:
                        session = self._sessions.replace_messages(
                            workspace_name,
                            session_id,
                            messages,
                        )
                        continue
                    session = self._sessions.transition_approval(
                        workspace_name,
                        session_id,
                        approval.id,
                        expected=ApprovalState.PENDING,
                        state=ApprovalState.DENIED,
                        result=result,
                        messages=messages,
                    )
                    await self._emit(
                        EventType.TOOL_APPROVAL_DENIED,
                        _approval_payload(approval, workspace_name, session_id),
                        event_location,
                    )
                return session

    async def _resolve_approval_locked(
        self,
        workspace_name: str,
        session_id: str,
        approval_id: str,
        *,
        approved: bool,
        event_location: str,
    ) -> AsyncGenerator[RuntimeStreamEvent, None]:
        session = await self._recover_executing_approvals(
            workspace_name,
            session_id,
            event_location,
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
        progress = _RunProgress(round_number=approval.round_number)

        await self._emit(
            EventType.RUN_RESUMED,
            RuntimeEventPayload(
                run_id=approval.run_id,
                workspace_name=workspace_name,
                session_id=UUID(session_id),
                round_number=approval.round_number,
            ),
            event_location,
        )

        try:
            (
                run_context,
                instructions,
                registry,
                executor,
            ) = await self._resolve_capabilities(workspace_name, session_id)
            if approved:
                prepared = _restore_prepared_call(registry, approval, call)
                # Persist execution ownership before emitting or invoking the
                # write. A restart can then make the outcome indeterminate.
                self._sessions.transition_approval(
                    workspace_name,
                    session_id,
                    approval_id,
                    expected=ApprovalState.PENDING,
                    state=ApprovalState.EXECUTING,
                )
                await self._emit(
                    EventType.TOOL_APPROVAL_APPROVED,
                    _approval_payload(approval, workspace_name, session_id),
                    event_location,
                )
                await self._emit(
                    EventType.TOOL_EXECUTION_STARTED,
                    _tool_execution_payload(
                        approval, workspace_name, session_id
                    ),
                    event_location,
                )
                execution = await executor.start(prepared)
                result: ToolResultPart | None = None
                try:
                    async for execution_event in execution.events():
                        if isinstance(execution_event, ToolOutput):
                            yield ToolOutputStreamEvent(
                                call_id=call.call_id,
                                tool_name=call.name,
                                stream=execution_event.stream,
                                text=execution_event.text,
                            )
                        else:
                            result = execution_event.result
                except BaseException:
                    try:
                        cancelled = await asyncio.shield(execution.cancel())
                    except ToolExecutionIndeterminateError:
                        self._sessions.transition_approval(
                            workspace_name,
                            session_id,
                            approval_id,
                            expected=ApprovalState.EXECUTING,
                            state=ApprovalState.INDETERMINATE,
                        )
                        await self._emit(
                            EventType.TOOL_APPROVAL_INDETERMINATE,
                            _approval_payload(
                                approval, workspace_name, session_id
                            ),
                            event_location,
                        )
                    else:
                        cancelled_messages = messages + (
                            Message(role=Role.TOOL, parts=(cancelled,)),
                        )
                        self._sessions.transition_approval(
                            workspace_name,
                            session_id,
                            approval_id,
                            expected=ApprovalState.EXECUTING,
                            state=ApprovalState.COMPLETED,
                            result=cancelled,
                            messages=cancelled_messages,
                        )
                        await self._emit(
                            EventType.TOOL_EXECUTION_COMPLETED,
                            ToolCompletedEventPayload(
                                **_tool_execution_payload(
                                    approval, workspace_name, session_id
                                ).model_dump(),
                                is_error=cancelled.is_error,
                            ),
                            event_location,
                        )
                    raise
                if result is None:
                    raise ToolExecutionIndeterminateError(
                        "tool execution ended without a result"
                    )
                state = ApprovalState.COMPLETED
            else:
                result = ToolResultPart(
                    call_id=call.call_id,
                    name=call.name,
                    content="tool execution denied",
                    is_error=True,
                )
                state = ApprovalState.DENIED

            messages = messages + (Message(role=Role.TOOL, parts=(result,)),)
            # Consume the approval and its matching result in one session
            # replacement so it can never be approved a second time.
            self._sessions.transition_approval(
                workspace_name,
                session_id,
                approval_id,
                expected=(
                    ApprovalState.EXECUTING
                    if approved
                    else ApprovalState.PENDING
                ),
                state=state,
                result=result,
                messages=messages,
            )
            if approved:
                await self._emit(
                    EventType.TOOL_EXECUTION_COMPLETED,
                    ToolCompletedEventPayload(
                        **_tool_execution_payload(
                            approval, workspace_name, session_id
                        ).model_dump(),
                        is_error=result.is_error,
                    ),
                    event_location,
                )
            else:
                await self._emit(
                    EventType.TOOL_APPROVAL_DENIED,
                    _approval_payload(approval, workspace_name, session_id),
                    event_location,
                )

            model = (
                self._answer_model_factory()
                if approval.answer_now
                else self._model_factory()
            )
            tools = registry.definitions if model.features.tools else ()
            pending_calls = _unresolved_tool_calls(messages)
            async for event in self._continue(
                workspace_name,
                session_id,
                messages,
                model,
                tools,
                run_context,
                instructions,
                executor,
                approval.usage,
                run_id=approval.run_id,
                event_location=event_location,
                round_number=(
                    approval.round_number
                    if pending_calls
                    else approval.round_number + 1
                ),
                progress=progress,
                pending_calls=pending_calls,
                answer_now=approval.answer_now,
            ):
                yield event
        except (asyncio.CancelledError, RuntimeEventError):
            raise
        except Exception as error:
            await self._emit_run_failed(
                approval.run_id,
                workspace_name,
                session_id,
                progress.round_number,
                event_location,
                error,
            )
            raise

    async def _continue(
        self,
        workspace_name: str,
        session_id: str,
        messages: tuple[Message, ...],
        model: Model,
        tools: tuple[ToolDefinition, ...],
        run_context: RunContext,
        instructions: tuple[str, ...],
        executor: ToolExecutor,
        usage: Usage,
        *,
        run_id: UUID,
        event_location: str,
        round_number: int,
        progress: _RunProgress,
        pending_calls: tuple[ToolCallPart, ...] = (),
        answer_now: bool = False,
    ) -> AsyncIterator[RuntimeStreamEvent]:
        """Advance a durable run until completion, approval, or failure.

        Pending calls are consumed before another model request. An approval
        pauses after persisting all resumption state; otherwise each result is
        checkpointed before the next call or model round.
        """

        while round_number <= self._max_model_rounds:
            progress.round_number = round_number
            if pending_calls:
                for call in pending_calls:
                    prepared = await executor.prepare(call)
                    if isinstance(prepared, RejectedToolCall):
                        await self._emit(
                            EventType.TOOL_CALL_PREPARED,
                            _tool_prepared_payload(
                                run_id,
                                workspace_name,
                                session_id,
                                round_number,
                                call,
                                prepared.outcome,
                                prepared.effect,
                            ),
                            event_location,
                        )
                        result = prepared.result
                    elif isinstance(prepared.decision, RequireApproval):
                        arguments = cast(
                            dict[str, object],
                            prepared.arguments.model_dump(mode="json"),
                        )
                        approval = ToolApproval(
                            id=approval_request_id(session_id, call.call_id),
                            run_id=run_id,
                            call=call,
                            tool_name=prepared.tool.definition.name,
                            arguments=arguments,
                            effect=prepared.tool.effect,
                            reason=prepared.decision.reason,
                            round_number=round_number,
                            usage=usage,
                            answer_now=answer_now,
                        )
                        # Persist the exact call, validated arguments, and run
                        # position before exposing the approval to a caller.
                        self._sessions.add_approval(
                            workspace_name, session_id, approval
                        )
                        await self._emit(
                            EventType.TOOL_CALL_PREPARED,
                            _tool_prepared_payload(
                                run_id,
                                workspace_name,
                                session_id,
                                round_number,
                                call,
                                ToolPreparationOutcome.REQUIRE_APPROVAL,
                                prepared.tool.effect,
                            ),
                            event_location,
                        )
                        await self._emit(
                            EventType.TOOL_APPROVAL_REQUESTED,
                            ApprovalRequestedEventPayload(
                                **_approval_payload(
                                    approval,
                                    workspace_name,
                                    session_id,
                                ).model_dump(),
                                reason=approval.reason,
                            ),
                            event_location,
                        )
                        await self._emit(
                            EventType.RUN_PAUSED,
                            RuntimeEventPayload(
                                run_id=approval.run_id,
                                workspace_name=workspace_name,
                                session_id=UUID(session_id),
                                round_number=approval.round_number,
                            ),
                            event_location,
                        )
                        yield ApprovalStreamEvent(approval)
                        return
                    else:
                        await self._emit(
                            EventType.TOOL_CALL_PREPARED,
                            _tool_prepared_payload(
                                run_id,
                                workspace_name,
                                session_id,
                                round_number,
                                call,
                                ToolPreparationOutcome.ALLOW,
                                prepared.tool.effect,
                            ),
                            event_location,
                        )
                        await self._emit(
                            EventType.TOOL_EXECUTION_STARTED,
                            _tool_execution_payload_from_call(
                                run_id,
                                workspace_name,
                                session_id,
                                round_number,
                                call,
                                prepared.tool.effect,
                            ),
                            event_location,
                        )
                        execution = await executor.start(prepared)
                        streaming_result: ToolResultPart | None = None
                        try:
                            async for execution_event in execution.events():
                                if isinstance(execution_event, ToolOutput):
                                    yield ToolOutputStreamEvent(
                                        call_id=call.call_id,
                                        tool_name=call.name,
                                        stream=execution_event.stream,
                                        text=execution_event.text,
                                    )
                                else:
                                    streaming_result = execution_event.result
                        except BaseException:
                            await asyncio.shield(execution.cancel())
                            raise
                        if streaming_result is None:
                            raise ToolExecutionIndeterminateError(
                                "tool execution ended without a result"
                            )
                        result = streaming_result
                    messages = messages + (
                        Message(role=Role.TOOL, parts=(result,)),
                    )
                    # Make each result durable before the next call can cause a
                    # side effect. Cancellation therefore leaves a known prefix.
                    self._sessions.replace_messages(
                        workspace_name,
                        session_id,
                        messages,
                    )
                    if isinstance(prepared, PreparedToolCall) and isinstance(
                        prepared.decision, Allow
                    ):
                        await self._emit(
                            EventType.TOOL_EXECUTION_COMPLETED,
                            ToolCompletedEventPayload(
                                **_tool_execution_payload_from_call(
                                    run_id,
                                    workspace_name,
                                    session_id,
                                    round_number,
                                    call,
                                    prepared.tool.effect,
                                ).model_dump(),
                                is_error=result.is_error,
                            ),
                            event_location,
                        )
                pending_calls = ()
                round_number += 1
                continue

            request = self._context_builder.build(
                messages,
                (
                    (*instructions, ANSWER_NOW_INSTRUCTION)
                    if answer_now
                    else instructions
                ),
                tool_definitions=tools,
                run_context=run_context,
            )
            _write_context_diagnostic(self._context_diagnostic_path, request)
            streamed_text = ""
            streamed_reasoning = ""
            completed: ModelResponse | None = None
            reasoning_deadline: asyncio.Timeout | None = None

            await self._emit(
                EventType.MODEL_REQUEST_STARTED,
                RuntimeEventPayload(
                    run_id=run_id,
                    workspace_name=workspace_name,
                    session_id=UUID(session_id),
                    round_number=round_number,
                ),
                event_location,
            )
            try:
                async with asyncio.timeout(None) as reasoning_deadline:
                    async for event in model.stream(request):
                        if completed is not None:
                            raise ModelProtocolError(
                                "model streamed after completion"
                            )
                        if isinstance(event, TextDelta):
                            streamed_text += event.text
                            if event.text:
                                reasoning_deadline.reschedule(None)
                                yield PromptStreamEvent(text=event.text)
                        elif isinstance(event, ReasoningDelta):
                            streamed_reasoning += event.text
                            if event.text:
                                # Start the deadline only when reasoning begins;
                                # visible answer text permanently disarms it.
                                if (
                                    not streamed_text
                                    and reasoning_deadline.when() is None
                                ):
                                    reasoning_deadline.reschedule(
                                        asyncio.get_running_loop().time()
                                        + self._answer_now_after_seconds
                                    )
                                yield PromptStreamEvent(
                                    text=event.text,
                                    text_kind="reasoning",
                                )
                        else:
                            completed = event.response

                if completed is None:
                    raise ModelProtocolError(
                        "model stream ended before completion"
                    )
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
                if calls:
                    _unresolved_tool_calls((*messages, assistant_message))
                if calls and not tools:
                    raise ModelProtocolError(
                        "text runtime received unsupported parts"
                    )
                if calls and completed.finish_reason not in (
                    FinishReason.TOOL_CALL,
                    FinishReason.OTHER,
                ):
                    raise ModelProtocolError(
                        "tool response has contradictory finish reason"
                    )
            except (asyncio.CancelledError, RuntimeEventError):
                raise
            except Exception as error:
                reasoning_timed_out = (
                    isinstance(error, TimeoutError)
                    and reasoning_deadline is not None
                    and reasoning_deadline.expired()
                )
                failure = (
                    AgentLimitError("model reasoning deadline exceeded")
                    if reasoning_timed_out
                    else error
                )
                await self._emit(
                    EventType.MODEL_REQUEST_FAILED,
                    ModelFailedEventPayload(
                        run_id=run_id,
                        workspace_name=workspace_name,
                        session_id=UUID(session_id),
                        round_number=round_number,
                        failure=_failure_kind(failure),
                    ),
                    event_location,
                )
                if not reasoning_timed_out:
                    raise
                if answer_now or round_number == self._max_model_rounds:
                    raise AgentLimitError(
                        "model did not produce a final answer"
                    ) from error
                model = self._answer_model_factory()
                answer_now = True
                round_number += 1
                continue

            usage = _add_usage(usage, completed.usage)
            await self._emit(
                EventType.MODEL_REQUEST_COMPLETED,
                ModelCompletedEventPayload(
                    run_id=run_id,
                    workspace_name=workspace_name,
                    session_id=UUID(session_id),
                    round_number=round_number,
                    finish_reason=completed.finish_reason,
                    usage=completed.usage,
                    provider_response_id=completed.provider_response_id,
                ),
                event_location,
            )

            if not calls and not streamed_text:
                if answer_now or round_number == self._max_model_rounds:
                    raise AgentLimitError(
                        "model did not produce a final answer"
                    )
                model = self._answer_model_factory()
                answer_now = True
                round_number += 1
                continue

            if not calls:
                messages = messages + (assistant_message,)
                self._sessions.replace_messages(
                    workspace_name,
                    session_id,
                    messages,
                )
                await self._emit(
                    EventType.RUN_COMPLETED,
                    RuntimeEventPayload(
                        run_id=run_id,
                        workspace_name=workspace_name,
                        session_id=UUID(session_id),
                        round_number=round_number,
                    ),
                    event_location,
                )
                yield PromptStreamEvent(usage=usage, done=True)
                return

            messages = messages + (assistant_message,)
            self._sessions.replace_messages(
                workspace_name,
                session_id,
                messages,
            )
            for call in calls:
                await self._emit(
                    EventType.TOOL_CALL_REQUESTED,
                    _tool_payload(
                        run_id,
                        workspace_name,
                        session_id,
                        round_number,
                        call,
                    ),
                    event_location,
                )

            limit_error: str | None = None
            if len(calls) > self._max_tool_calls_per_response:
                limit_error = "tool call limit exceeded"
            elif round_number == self._max_model_rounds:
                limit_error = "model round limit exceeded"
            if limit_error is not None:
                for call in calls:
                    messages = messages + (_tool_error(call, limit_error),)
                    self._sessions.replace_messages(
                        workspace_name,
                        session_id,
                        messages,
                    )
                raise AgentLimitError(limit_error)

            pending_calls = calls

        raise AssertionError("unreachable model round")

    async def _resolve_capabilities(
        self,
        workspace_name: str,
        session_id: str,
    ) -> tuple[RunContext, tuple[str, ...], ToolRegistry, ToolExecutor]:
        """Compose one run in registration order before contacting the model.

        Rebuilding the registry rejects duplicate names and keeps contributed
        tool instances isolated to this workspace and session. The optional
        resolver runs once here so configuration changes affect the next run,
        never one already in progress.
        """

        context = RunContext(
            workspace_name=workspace_name,
            workspace_path=self._sessions.workspaces.get(workspace_name).path,
            session_id=session_id,
        )
        instructions: list[str] = []
        tools = list(self._tool_registry.tools)
        capabilities = self._capabilities
        if self._capability_resolver is not None:
            capabilities += tuple(self._capability_resolver(context))
        for capability in capabilities:
            instructions.extend(await capability.instructions(context))
            tools.extend(await capability.tools(context))
        registry = ToolRegistry(tools)
        return (
            context,
            tuple(instructions),
            registry,
            self._tool_executor.for_registry(registry),
        )

    async def _recover_executing_approvals(
        self,
        workspace_name: str,
        session_id: str,
        event_location: str,
    ) -> Session:
        """Make interrupted writes non-replayable and emit trace events."""

        session = self._sessions.get(workspace_name, session_id)
        interrupted = tuple(
            approval
            for approval in session.approvals
            if approval.state is ApprovalState.EXECUTING
        )
        recovered = self._sessions.recover_executing_approvals(
            workspace_name, session_id
        )
        for approval in interrupted:
            await self._emit(
                EventType.TOOL_APPROVAL_INDETERMINATE,
                _approval_payload(approval, workspace_name, session_id),
                event_location,
            )
        return recovered

    async def _emit(
        self,
        event_type: EventType,
        payload: RuntimeEventPayload,
        event_location: str,
    ) -> None:
        try:
            await self._events.emit(
                event_factory(
                    event_type,
                    location=event_location,
                    payload=payload,
                )
            )
        except Exception as error:
            raise RuntimeEventError("runtime event emission failed") from error

    async def _emit_run_failed(
        self,
        run_id: UUID,
        workspace_name: str,
        session_id: str,
        round_number: int,
        event_location: str,
        error: Exception,
    ) -> None:
        await self._emit(
            EventType.RUN_FAILED,
            RunFailedEventPayload(
                run_id=run_id,
                workspace_name=workspace_name,
                session_id=UUID(session_id),
                round_number=round_number,
                failure=_failure_kind(error),
            ),
            event_location,
        )


def _model_from_settings() -> Model:
    settings = get_settings()
    provider = AIProvider.from_settings(settings)
    return provider.model(
        settings.provider.model_name,
        settings.provider.reasoning_effort,
    )


def _answer_model_from_settings() -> Model:
    settings = get_settings()
    provider = AIProvider.from_settings(settings)
    return provider.model(
        settings.provider.model_name,
        ReasoningEffort.NONE,
    )


def _failure_kind(error: Exception) -> RuntimeFailure:
    if isinstance(error, ModelProviderError):
        return RuntimeFailure.PROVIDER
    if isinstance(error, ModelProtocolError):
        return RuntimeFailure.PROTOCOL
    if isinstance(error, AgentLimitError):
        return RuntimeFailure.LIMIT
    if isinstance(error, ToolPolicyError):
        return RuntimeFailure.POLICY
    if isinstance(error, OSError):
        return RuntimeFailure.PERSISTENCE
    return RuntimeFailure.INTERNAL


def _tool_payload(
    run_id: UUID,
    workspace_name: str,
    session_id: str,
    round_number: int,
    call: ToolCallPart,
) -> ToolEventPayload:
    return ToolEventPayload(
        run_id=run_id,
        workspace_name=workspace_name,
        session_id=UUID(session_id),
        round_number=round_number,
        call_id=call.call_id,
        tool_name=call.name,
    )


def _tool_prepared_payload(
    run_id: UUID,
    workspace_name: str,
    session_id: str,
    round_number: int,
    call: ToolCallPart,
    outcome: ToolPreparationOutcome,
    effect: ToolEffect | None,
) -> ToolPreparedEventPayload:
    return ToolPreparedEventPayload(
        **_tool_payload(
            run_id,
            workspace_name,
            session_id,
            round_number,
            call,
        ).model_dump(),
        outcome=outcome,
        effect=effect,
    )


def _tool_execution_payload_from_call(
    run_id: UUID,
    workspace_name: str,
    session_id: str,
    round_number: int,
    call: ToolCallPart,
    effect: ToolEffect,
) -> ToolExecutionEventPayload:
    return ToolExecutionEventPayload(
        **_tool_payload(
            run_id,
            workspace_name,
            session_id,
            round_number,
            call,
        ).model_dump(),
        effect=effect,
    )


def _approval_payload(
    approval: ToolApproval,
    workspace_name: str,
    session_id: str,
) -> ApprovalEventPayload:
    return ApprovalEventPayload(
        run_id=approval.run_id,
        workspace_name=workspace_name,
        session_id=UUID(session_id),
        round_number=approval.round_number,
        call_id=approval.call.call_id,
        tool_name=approval.tool_name,
        effect=approval.effect,
        approval_id=UUID(approval.id),
    )


def _tool_execution_payload(
    approval: ToolApproval,
    workspace_name: str,
    session_id: str,
) -> ToolExecutionEventPayload:
    return ToolExecutionEventPayload(
        run_id=approval.run_id,
        workspace_name=workspace_name,
        session_id=UUID(session_id),
        round_number=approval.round_number,
        call_id=approval.call.call_id,
        tool_name=approval.tool_name,
        effect=approval.effect,
    )


def _assistant_message(
    response: ModelResponse,
    streamed_text: str,
    streamed_reasoning: str,
) -> Message:
    """Validate streamed output while preserving completed response order."""

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
        reasoning_tokens=first.reasoning_tokens + second.reasoning_tokens,
        reasoning_tokens_estimated=(
            first.reasoning_tokens_estimated
            or second.reasoning_tokens_estimated
        ),
    )


def _validate_tool_history(messages: tuple[Message, ...]) -> None:
    """Reject calls whose execution state cannot be determined from history."""

    if _unresolved_tool_calls(messages):
        raise ModelProtocolError(
            "session contains unresolved tool call history"
        )


def _unresolved_tool_calls(
    messages: tuple[Message, ...],
) -> tuple[ToolCallPart, ...]:
    """Return the valid unresolved suffix without guessing side-effect state."""

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
                    raise ModelProtocolError("tool call ID was reused")
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
    """Revalidate durable approval data against the current tool registry."""

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
