import asyncio
from collections.abc import AsyncIterator, Iterable
from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel

from ethos.events.emitters import EnvelopeEventEmitter
from ethos.models import (
    FinishReason,
    Message,
    ModelEvent,
    ModelFeatures,
    ModelRequest,
    ModelResponse,
    ResponseCompleted,
    Role,
    TextPart,
    ToolCallPart,
    ToolDefinition,
    ToolResultPart,
    Usage,
)
from ethos.provider import ModelProtocolError
from ethos.runtime import (
    MAX_MODEL_ROUNDS,
    MAX_TOOL_CALLS_PER_RESPONSE,
    AgentLimitError,
    AgentRuntime,
    ApprovalStreamEvent,
    PromptStreamEvent,
)
from ethos.sessions import (
    ApprovalNotFoundError,
    ApprovalStateError,
    Session,
    SessionManager,
)
from ethos.tools import (
    ApprovalState,
    ToolEffect,
    ToolExecutionError,
    ToolRegistry,
)
from ethos.workspaces import WorkspaceManager
from fakes import FakeModel


class Arguments(BaseModel):
    value: str


class RuntimeTool:
    arguments_type: type[BaseModel] = Arguments

    def __init__(
        self,
        *,
        effect: ToolEffect = ToolEffect.READ,
        failure: Exception | None = None,
    ) -> None:
        self.definition = ToolDefinition(
            name="echo",
            description="Echo one value",
            parameters={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
        )
        self.effect = effect
        self.failure = failure
        self.values: list[str] = []

    async def execute(self, arguments: BaseModel) -> str:
        assert isinstance(arguments, Arguments)
        self.values.append(arguments.value)
        if self.failure is not None:
            raise self.failure
        return f"echo: {arguments.value}"


def tool_call(
    call_id: str = "call-1",
    *,
    name: str = "echo",
    arguments_json: str = '{"value":"one"}',
) -> ToolCallPart:
    return ToolCallPart(
        call_id=call_id,
        name=name,
        arguments_json=arguments_json,
    )


def call_response(
    *calls: ToolCallPart,
    text: str | None = None,
    finish_reason: FinishReason = FinishReason.TOOL_CALL,
    usage: Usage | None = None,
) -> ModelResponse:
    parts: tuple[TextPart | ToolCallPart, ...] = (
        *((TextPart(text=text),) if text is not None else ()),
        *calls,
    )
    return ModelResponse(
        parts=parts,
        usage=usage or Usage(input_tokens=2, output_tokens=1),
        finish_reason=finish_reason,
    )


def text_response(
    text: str = "done", usage: Usage | None = None
) -> ModelResponse:
    return ModelResponse(
        parts=(TextPart(text=text),),
        usage=usage or Usage(input_tokens=3, output_tokens=2),
        finish_reason=FinishReason.STOP,
    )


def setup_runtime(
    tmp_path: Path,
    model: FakeModel,
    tool: RuntimeTool | None = None,
) -> tuple[SessionManager, Session, AgentRuntime, RuntimeTool]:
    workspaces = WorkspaceManager(tmp_path / "workspaces")
    workspaces.create("my-project")
    sessions = SessionManager(workspaces, tmp_path / "sessions")
    session = sessions.create("my-project")
    registered = tool or RuntimeTool()
    registry = ToolRegistry((registered,))
    runtime = AgentRuntime(
        sessions,
        lambda: model,
        registry,
        events=EnvelopeEventEmitter(),
    )
    return sessions, session, runtime, registered


async def collect(
    runtime: AgentRuntime,
    session: Session,
) -> list[PromptStreamEvent]:
    return [
        cast(PromptStreamEvent, event)
        for event in [
            event
            async for event in runtime.run(
                "hello", session.workspace_name, str(session.id)
            )
        ]
    ]


def test_tool_enabled_runtime_can_answer_immediately(tmp_path: Path) -> None:
    model = FakeModel(
        (text_response(),),
        stream_chunks=(("done",),),
        features=ModelFeatures(tools=True),
    )
    sessions, session, runtime, tool = setup_runtime(tmp_path, model)

    events = asyncio.run(collect(runtime, session))

    assert events == [
        PromptStreamEvent(text="done"),
        PromptStreamEvent(
            usage=Usage(input_tokens=3, output_tokens=2), done=True
        ),
    ]
    assert model.requests[0].tools == (tool.definition,)


def test_runtime_completes_one_tool_round(tmp_path: Path) -> None:
    model = FakeModel(
        (
            call_response(
                tool_call(),
                text="Let me check. ",
                finish_reason=FinishReason.OTHER,
                usage=Usage(
                    input_tokens=2,
                    output_tokens=1,
                    reasoning_tokens=1,
                ),
            ),
            text_response(
                "It is one.",
                Usage(
                    input_tokens=3,
                    output_tokens=2,
                    reasoning_tokens=1,
                    reasoning_tokens_estimated=True,
                ),
            ),
        ),
        stream_chunks=(("Let me check. ",), ("It is ", "one.")),
        features=ModelFeatures(tools=True),
    )
    sessions, session, runtime, tool = setup_runtime(tmp_path, model)

    events = asyncio.run(collect(runtime, session))

    assert [event.text for event in events] == [
        "Let me check. ",
        "It is ",
        "one.",
        "",
    ]
    assert events[-1] == PromptStreamEvent(
        usage=Usage(
            input_tokens=5,
            output_tokens=3,
            reasoning_tokens=2,
            reasoning_tokens_estimated=True,
        ),
        done=True,
    )
    assert tool.values == ["one"]
    assert len(model.requests) == 2
    assert all(
        request.tools == (tool.definition,) for request in model.requests
    )
    assert [message.role for message in model.requests[1].messages] == [
        Role.SYSTEM,
        Role.SYSTEM,
        Role.SYSTEM,
        Role.USER,
        Role.ASSISTANT,
        Role.TOOL,
    ]
    result = model.requests[1].messages[-1].parts[0]
    assert result == ToolResultPart(
        call_id="call-1",
        name="echo",
        content="echo: one",
    )
    assert sessions.get("my-project", str(session.id)).messages == (
        *model.requests[1].messages[3:],
        Message(
            role=Role.ASSISTANT,
            parts=(TextPart(text="It is one."),),
        ),
    )


def test_runtime_completes_several_tool_rounds(tmp_path: Path) -> None:
    model = FakeModel(
        (
            call_response(tool_call("call-1")),
            call_response(
                tool_call("call-2", arguments_json='{"value":"two"}')
            ),
            text_response(),
        ),
        stream_chunks=((), (), ("done",)),
        features=ModelFeatures(tools=True),
    )
    _sessions, session, runtime, tool = setup_runtime(tmp_path, model)

    asyncio.run(collect(runtime, session))

    assert tool.values == ["one", "two"]
    assert len(model.requests) == 3
    assert [len(request.messages) for request in model.requests] == [4, 6, 8]


def test_write_tool_waits_for_durable_approval(tmp_path: Path) -> None:
    model = FakeModel(
        (call_response(tool_call()), text_response()),
        stream_chunks=((), ("done",)),
        features=ModelFeatures(tools=True),
    )
    sessions, session, runtime, tool = setup_runtime(
        tmp_path,
        model,
        RuntimeTool(effect=ToolEffect.WRITE),
    )

    pending = asyncio.run(
        _collect_runtime(runtime.run("hello", "my-project", str(session.id)))
    )

    assert len(pending) == 1
    event = pending[0]
    assert isinstance(event, ApprovalStreamEvent)
    approval = event.approval
    assert approval.state is ApprovalState.PENDING
    assert approval.call == tool_call()
    assert approval.tool_name == "echo"
    assert approval.arguments == {"value": "one"}
    assert approval.effect is ToolEffect.WRITE
    assert approval.reason == "write tool requires approval"
    assert approval.round_number == 1
    assert approval.usage == Usage(input_tokens=2, output_tokens=1)
    assert tool.values == []
    stored = sessions.get("my-project", str(session.id))
    assert stored.approvals == (approval,)
    assert [message.role for message in stored.messages] == [
        Role.USER,
        Role.ASSISTANT,
    ]

    completed = asyncio.run(
        _collect_runtime(
            runtime.resolve_approval(
                "my-project",
                str(session.id),
                approval.id,
                approved=True,
            )
        )
    )

    assert tool.values == ["one"]
    assert completed[-1] == PromptStreamEvent(
        usage=Usage(input_tokens=5, output_tokens=3), done=True
    )
    stored = sessions.get("my-project", str(session.id))
    assert stored.approvals[0].state is ApprovalState.COMPLETED
    assert stored.approvals[0].result == ToolResultPart(
        call_id="call-1",
        name="echo",
        content="echo: one",
    )

    with pytest.raises(ApprovalStateError, match="completed"):
        asyncio.run(
            _collect_runtime(
                runtime.resolve_approval(
                    "my-project",
                    str(session.id),
                    approval.id,
                    approved=True,
                )
            )
        )
    assert tool.values == ["one"]


def test_denied_write_tool_resumes_with_error_result(tmp_path: Path) -> None:
    model = FakeModel(
        (call_response(tool_call()), text_response("denied safely")),
        stream_chunks=((), ("denied safely",)),
        features=ModelFeatures(tools=True),
    )
    sessions, session, runtime, tool = setup_runtime(
        tmp_path,
        model,
        RuntimeTool(effect=ToolEffect.WRITE),
    )
    pending = asyncio.run(
        _collect_runtime(runtime.run("hello", "my-project", str(session.id)))
    )
    event = pending[0]
    assert isinstance(event, ApprovalStreamEvent)

    events = asyncio.run(
        _collect_runtime(
            runtime.resolve_approval(
                "my-project",
                str(session.id),
                event.approval.id,
                approved=False,
            )
        )
    )

    assert tool.values == []
    assert isinstance(events[-1], PromptStreamEvent)
    assert events[-1].done
    result = model.requests[1].messages[-1].parts[0]
    assert isinstance(result, ToolResultPart)
    assert result.content == "tool execution denied"
    assert result.is_error
    stored = sessions.get("my-project", str(session.id))
    assert stored.approvals[0].state is ApprovalState.DENIED
    assert stored.approvals[0].result == result


def test_pending_approval_survives_runtime_restart(tmp_path: Path) -> None:
    first_model = FakeModel(
        (call_response(tool_call()),),
        features=ModelFeatures(tools=True),
    )
    sessions, session, runtime, tool = setup_runtime(
        tmp_path,
        first_model,
        RuntimeTool(effect=ToolEffect.WRITE),
    )
    pending = asyncio.run(
        _collect_runtime(runtime.run("hello", "my-project", str(session.id)))
    )
    event = pending[0]
    assert isinstance(event, ApprovalStreamEvent)
    restarted_model = FakeModel(
        (text_response(),),
        stream_chunks=(("done",),),
        features=ModelFeatures(tools=True),
    )
    restarted = AgentRuntime(
        sessions,
        lambda: restarted_model,
        ToolRegistry((tool,)),
        events=EnvelopeEventEmitter(),
    )

    events = asyncio.run(
        _collect_runtime(
            restarted.resolve_approval(
                "my-project",
                str(session.id),
                event.approval.id,
                approved=True,
            )
        )
    )

    assert tool.values == ["one"]
    assert isinstance(events[-1], PromptStreamEvent)
    assert events[-1].done


def test_approval_is_bound_to_session_and_payload(tmp_path: Path) -> None:
    model = FakeModel(
        (call_response(tool_call()),),
        features=ModelFeatures(tools=True),
    )
    sessions, session, runtime, tool = setup_runtime(
        tmp_path,
        model,
        RuntimeTool(effect=ToolEffect.WRITE),
    )
    pending = asyncio.run(
        _collect_runtime(runtime.run("hello", "my-project", str(session.id)))
    )
    event = pending[0]
    assert isinstance(event, ApprovalStreamEvent)
    other = sessions.create("my-project")

    with pytest.raises(ApprovalNotFoundError):
        asyncio.run(
            _collect_runtime(
                runtime.resolve_approval(
                    "my-project",
                    str(other.id),
                    event.approval.id,
                    approved=True,
                )
            )
        )

    stored = sessions.get("my-project", str(session.id))
    changed = Message(
        role=Role.ASSISTANT,
        parts=(tool_call(arguments_json='{"value":"changed"}'),),
    )
    sessions.replace_messages(
        "my-project",
        str(session.id),
        (stored.messages[0], changed),
    )
    with pytest.raises(ApprovalStateError, match="payload changed"):
        asyncio.run(
            _collect_runtime(
                runtime.resolve_approval(
                    "my-project",
                    str(session.id),
                    event.approval.id,
                    approved=True,
                )
            )
        )
    assert tool.values == []


def test_interrupted_execution_becomes_indeterminate(tmp_path: Path) -> None:
    model = FakeModel(
        (call_response(tool_call()),),
        features=ModelFeatures(tools=True),
    )
    sessions, session, runtime, tool = setup_runtime(
        tmp_path,
        model,
        RuntimeTool(effect=ToolEffect.WRITE),
    )
    pending = asyncio.run(
        _collect_runtime(runtime.run("hello", "my-project", str(session.id)))
    )
    event = pending[0]
    assert isinstance(event, ApprovalStreamEvent)
    sessions.transition_approval(
        "my-project",
        str(session.id),
        event.approval.id,
        expected=ApprovalState.PENDING,
        state=ApprovalState.EXECUTING,
    )
    restarted = AgentRuntime(
        sessions,
        lambda: FakeModel(()),
        ToolRegistry((tool,)),
        events=EnvelopeEventEmitter(),
    )

    with pytest.raises(ApprovalStateError, match="indeterminate"):
        asyncio.run(
            _collect_runtime(
                restarted.resolve_approval(
                    "my-project",
                    str(session.id),
                    event.approval.id,
                    approved=True,
                )
            )
        )

    stored = sessions.get("my-project", str(session.id))
    assert stored.approvals[0].state is ApprovalState.INDETERMINATE
    assert tool.values == []


def test_session_rejects_invalid_approval_transitions(tmp_path: Path) -> None:
    model = FakeModel(
        (call_response(tool_call()),),
        features=ModelFeatures(tools=True),
    )
    sessions, session, runtime, _tool = setup_runtime(
        tmp_path,
        model,
        RuntimeTool(effect=ToolEffect.WRITE),
    )
    pending = asyncio.run(
        _collect_runtime(runtime.run("hello", "my-project", str(session.id)))
    )
    event = pending[0]
    assert isinstance(event, ApprovalStreamEvent)

    with pytest.raises(ApprovalStateError, match="pending -> completed"):
        sessions.transition_approval(
            "my-project",
            str(session.id),
            event.approval.id,
            expected=ApprovalState.PENDING,
            state=ApprovalState.COMPLETED,
            result=ToolResultPart(
                call_id="call-1",
                name="echo",
                content="result",
            ),
        )
    with pytest.raises(ApprovalStateError, match="invalid approval transition"):
        sessions.transition_approval(
            "my-project",
            str(session.id),
            event.approval.id,
            expected=ApprovalState.PENDING,
            state=ApprovalState.DENIED,
        )

    stored = sessions.get("my-project", str(session.id))
    assert stored.approvals[0].state is ApprovalState.PENDING


async def _collect_runtime(
    events: AsyncIterator[PromptStreamEvent | ApprovalStreamEvent],
) -> list[PromptStreamEvent | ApprovalStreamEvent]:
    return [event async for event in events]


def test_runtime_executes_several_calls_sequentially(tmp_path: Path) -> None:
    first = tool_call("call-1")
    second = tool_call("call-2", arguments_json='{"value":"two"}')
    model = FakeModel(
        (call_response(first, second), text_response()),
        stream_chunks=((), ("done",)),
        features=ModelFeatures(tools=True),
    )
    _sessions, session, runtime, tool = setup_runtime(tmp_path, model)

    asyncio.run(collect(runtime, session))

    assert tool.values == ["one", "two"]
    history = model.requests[1].messages
    call_ids: list[str] = []
    for message in history[5:]:
        result = message.parts[0]
        assert isinstance(result, ToolResultPart)
        call_ids.append(result.call_id)
    assert call_ids == ["call-1", "call-2"]


@pytest.mark.parametrize(
    ("case", "requested_call", "effect", "failure", "expected", "executed"),
    (
        (
            "unknown",
            tool_call(name="missing"),
            ToolEffect.READ,
            None,
            "unknown tool",
            False,
        ),
        (
            "arguments",
            tool_call(arguments_json="[]"),
            ToolEffect.READ,
            None,
            "invalid tool arguments",
            False,
        ),
        (
            "exception",
            tool_call(),
            ToolEffect.READ,
            RuntimeError("secret failure"),
            "tool execution failed",
            True,
        ),
        (
            "safe exception",
            tool_call(),
            ToolEffect.READ,
            ToolExecutionError("path must be inside the workspace"),
            "path must be inside the workspace",
            True,
        ),
    ),
)
def test_runtime_recovers_after_tool_error(
    tmp_path: Path,
    case: str,
    requested_call: ToolCallPart,
    effect: ToolEffect,
    failure: Exception | None,
    expected: str,
    executed: bool,
) -> None:
    tool = RuntimeTool(effect=effect, failure=failure)
    model = FakeModel(
        (call_response(requested_call), text_response(f"recovered {case}")),
        stream_chunks=((), ((f"recovered {case}"),)),
        features=ModelFeatures(tools=True),
    )
    _sessions, session, runtime, _tool = setup_runtime(tmp_path, model, tool)

    events = asyncio.run(collect(runtime, session))

    result = model.requests[1].messages[-1].parts[0]
    assert isinstance(result, ToolResultPart)
    assert result.content == expected
    assert result.is_error
    assert bool(tool.values) is executed
    assert "".join(event.text for event in events) == f"recovered {case}"


def test_tool_call_limit_allows_exact_boundary(tmp_path: Path) -> None:
    calls = tuple(
        tool_call(f"call-{index}", arguments_json=f'{{"value":"{index}"}}')
        for index in range(MAX_TOOL_CALLS_PER_RESPONSE)
    )
    model = FakeModel(
        (call_response(*calls), text_response()),
        stream_chunks=((), ("done",)),
        features=ModelFeatures(tools=True),
    )
    _sessions, session, runtime, tool = setup_runtime(tmp_path, model)

    asyncio.run(collect(runtime, session))

    assert tool.values == [str(index) for index in range(16)]
    assert len(model.requests) == 2


def test_tool_call_limit_persists_errors_without_execution(
    tmp_path: Path,
) -> None:
    calls = tuple(
        tool_call(f"call-{index}", arguments_json=f'{{"value":"{index}"}}')
        for index in range(MAX_TOOL_CALLS_PER_RESPONSE + 1)
    )
    model = FakeModel(
        (call_response(*calls),),
        features=ModelFeatures(tools=True),
    )
    sessions, session, runtime, tool = setup_runtime(tmp_path, model)

    with pytest.raises(AgentLimitError, match="tool call limit exceeded"):
        asyncio.run(collect(runtime, session))

    stored = sessions.get("my-project", str(session.id)).messages
    assert tool.values == []
    assert len(model.requests) == 1
    assert [message.role for message in stored] == [
        Role.USER,
        Role.ASSISTANT,
        *([Role.TOOL] * 17),
    ]
    assert all(
        isinstance(message.parts[0], ToolResultPart)
        and message.parts[0].content == "tool call limit exceeded"
        and message.parts[0].is_error
        for message in stored[2:]
    )


def test_model_round_limit_allows_final_answer_on_round_eight(
    tmp_path: Path,
) -> None:
    calls = tuple(
        call_response(
            tool_call(
                f"call-{index}",
                arguments_json=f'{{"value":"{index}"}}',
            )
        )
        for index in range(MAX_MODEL_ROUNDS - 1)
    )
    model = FakeModel(
        (*calls, text_response()),
        stream_chunks=(*(() for _call in calls), ("done",)),
        features=ModelFeatures(tools=True),
    )
    _sessions, session, runtime, tool = setup_runtime(tmp_path, model)

    events = asyncio.run(collect(runtime, session))

    assert len(model.requests) == MAX_MODEL_ROUNDS
    assert len(tool.values) == MAX_MODEL_ROUNDS - 1
    assert events[-1].done


def test_model_round_limit_never_executes_round_eight_calls(
    tmp_path: Path,
) -> None:
    calls = tuple(
        call_response(
            tool_call(
                f"call-{index}",
                arguments_json=f'{{"value":"{index}"}}',
            )
        )
        for index in range(MAX_MODEL_ROUNDS)
    )
    model = FakeModel(
        calls,
        features=ModelFeatures(tools=True),
    )
    sessions, session, runtime, tool = setup_runtime(tmp_path, model)

    with pytest.raises(AgentLimitError, match="model round limit exceeded"):
        asyncio.run(collect(runtime, session))

    stored = sessions.get("my-project", str(session.id)).messages
    result = stored[-1].parts[0]
    assert len(model.requests) == MAX_MODEL_ROUNDS
    assert len(tool.values) == MAX_MODEL_ROUNDS - 1
    assert isinstance(result, ToolResultPart)
    assert result.content == "model round limit exceeded"
    assert result.is_error


def test_runtime_rejects_contradictory_tool_finish_reason(
    tmp_path: Path,
) -> None:
    model = FakeModel(
        (call_response(tool_call(), finish_reason=FinishReason.STOP),),
        features=ModelFeatures(tools=True),
    )
    sessions, session, runtime, tool = setup_runtime(tmp_path, model)

    with pytest.raises(ModelProtocolError, match="contradictory finish"):
        asyncio.run(collect(runtime, session))

    assert tool.values == []
    assert sessions.get("my-project", str(session.id)).messages == ()


def test_cancellation_during_tool_leaves_assistant_checkpoint(
    tmp_path: Path,
) -> None:
    started: asyncio.Event

    class BlockingTool(RuntimeTool):
        async def execute(self, arguments: BaseModel) -> str:
            assert isinstance(arguments, Arguments)
            self.values.append(arguments.value)
            started.set()
            await asyncio.Event().wait()
            return "unreachable"

    model = FakeModel(
        (call_response(tool_call()),),
        features=ModelFeatures(tools=True),
    )
    sessions, session, runtime, tool = setup_runtime(
        tmp_path, model, BlockingTool()
    )

    async def cancel() -> None:
        nonlocal started
        started = asyncio.Event()
        task = asyncio.create_task(collect(runtime, session))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel())

    stored = sessions.get("my-project", str(session.id)).messages
    assert tool.values == ["one"]
    assert [message.role for message in stored] == [Role.USER, Role.ASSISTANT]


def test_cancellation_after_result_checkpoint_preserves_result(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        blocked = asyncio.Event()

        class BlockingModel:
            features = ModelFeatures(tools=True)

            def __init__(self) -> None:
                self.requests: list[ModelRequest] = []

            async def request(self, request: ModelRequest) -> ModelResponse:
                raise AssertionError

            async def stream(
                self, request: ModelRequest
            ) -> AsyncIterator[ModelEvent]:
                self.requests.append(request)
                if len(self.requests) == 1:
                    yield ResponseCompleted(response=call_response(tool_call()))
                    return
                blocked.set()
                await asyncio.Event().wait()

        model = BlockingModel()
        workspaces = WorkspaceManager(tmp_path / "workspaces")
        workspaces.create("my-project")
        sessions = SessionManager(workspaces, tmp_path / "sessions")
        session = sessions.create("my-project")
        tool = RuntimeTool()
        runtime = AgentRuntime(
            sessions,
            lambda: model,
            ToolRegistry((tool,)),
            events=EnvelopeEventEmitter(),
        )
        task = asyncio.create_task(collect(runtime, session))
        await blocked.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        stored = sessions.get("my-project", str(session.id)).messages
        assert [message.role for message in stored] == [
            Role.USER,
            Role.ASSISTANT,
            Role.TOOL,
        ]

    asyncio.run(run())


@pytest.mark.parametrize("failed_replacement", (1, 2, 3))
def test_persistence_failure_stops_at_last_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_replacement: int,
) -> None:
    calls = (
        tool_call("call-1"),
        tool_call("call-2", arguments_json='{"value":"two"}'),
    )
    model = FakeModel(
        (call_response(*calls), text_response()),
        stream_chunks=((), ("done",)),
        features=ModelFeatures(tools=True),
    )
    sessions, session, runtime, tool = setup_runtime(tmp_path, model)
    replace_messages = sessions.replace_messages
    replacements = 0

    def fail_selected_replacement(
        workspace_name: str,
        session_id: str,
        messages: Iterable[Message],
    ) -> Session:
        nonlocal replacements
        replacements += 1
        if replacements == failed_replacement:
            raise OSError("persistence failed")
        return replace_messages(workspace_name, session_id, messages)

    monkeypatch.setattr(sessions, "replace_messages", fail_selected_replacement)

    with pytest.raises(OSError, match="persistence failed"):
        asyncio.run(collect(runtime, session))

    stored = sessions.get("my-project", str(session.id)).messages
    assert len(stored) == (0 if failed_replacement == 1 else failed_replacement)
    assert len(tool.values) == max(0, failed_replacement - 1)


def test_final_persistence_failure_preserves_tool_result_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = FakeModel(
        (call_response(tool_call()), text_response()),
        stream_chunks=((), ("done",)),
        features=ModelFeatures(tools=True),
    )
    sessions, session, runtime, _tool = setup_runtime(tmp_path, model)
    replace_messages = sessions.replace_messages
    replacements = 0

    def fail_final_replacement(
        workspace_name: str,
        session_id: str,
        messages: Iterable[Message],
    ) -> Session:
        nonlocal replacements
        replacements += 1
        if replacements == 3:
            raise OSError("persistence failed")
        return replace_messages(workspace_name, session_id, messages)

    monkeypatch.setattr(sessions, "replace_messages", fail_final_replacement)

    with pytest.raises(OSError, match="persistence failed"):
        asyncio.run(collect(runtime, session))

    stored = sessions.get("my-project", str(session.id)).messages
    assert [message.role for message in stored] == [
        Role.USER,
        Role.ASSISTANT,
        Role.TOOL,
    ]


@pytest.mark.parametrize("result_count", (0, 2))
def test_restarted_runtime_rejects_unresolved_tool_history(
    tmp_path: Path,
    result_count: int,
) -> None:
    model = FakeModel(())
    sessions, session, _runtime, _tool = setup_runtime(tmp_path, model)
    call = tool_call()
    messages: tuple[Message, ...] = (
        Message(role=Role.USER, parts=(TextPart(text="previous"),)),
        Message(role=Role.ASSISTANT, parts=(call,)),
        *(
            Message(
                role=Role.TOOL,
                parts=(
                    ToolResultPart(
                        call_id=call.call_id,
                        name=call.name,
                        content="result",
                    ),
                ),
            )
            for _index in range(result_count)
        ),
    )
    sessions.replace_messages("my-project", str(session.id), messages)
    restarted = AgentRuntime(
        sessions,
        lambda: model,
        ToolRegistry((RuntimeTool(),)),
        events=EnvelopeEventEmitter(),
    )

    with pytest.raises(ModelProtocolError, match="unresolved tool call"):
        asyncio.run(collect(restarted, session))

    assert model.requests == []


@pytest.mark.parametrize("case", ("result-before-call", "wrong-tool-name"))
def test_restarted_runtime_rejects_malformed_tool_history(
    tmp_path: Path,
    case: str,
) -> None:
    model = FakeModel(())
    sessions, session, _runtime, _tool = setup_runtime(tmp_path, model)
    call = tool_call()
    result = Message(
        role=Role.TOOL,
        parts=(
            ToolResultPart(
                call_id=call.call_id,
                name="other" if case == "wrong-tool-name" else call.name,
                content="result",
            ),
        ),
    )
    assistant = Message(role=Role.ASSISTANT, parts=(call,))
    messages = (
        (result, assistant)
        if case == "result-before-call"
        else (assistant, result)
    )
    sessions.replace_messages("my-project", str(session.id), messages)
    restarted = AgentRuntime(
        sessions,
        lambda: model,
        ToolRegistry((RuntimeTool(),)),
        events=EnvelopeEventEmitter(),
    )

    with pytest.raises(ModelProtocolError, match="unresolved tool call"):
        asyncio.run(collect(restarted, session))

    assert model.requests == []


def test_model_without_tool_feature_receives_no_definitions(
    tmp_path: Path,
) -> None:
    model = FakeModel(
        (text_response(),),
        stream_chunks=(("done",),),
        features=ModelFeatures(tools=False),
    )
    _sessions, session, runtime, _tool = setup_runtime(tmp_path, model)

    asyncio.run(collect(runtime, session))

    assert model.requests[0].tools == ()


def test_runtime_uses_configured_tool_call_limit(tmp_path: Path) -> None:
    model = FakeModel(
        (call_response(tool_call("call-1"), tool_call("call-2")),),
        features=ModelFeatures(tools=True),
    )
    sessions, session, _runtime, tool = setup_runtime(tmp_path, model)
    runtime = AgentRuntime(
        sessions,
        lambda: model,
        ToolRegistry((tool,)),
        events=EnvelopeEventEmitter(),
        max_tool_calls_per_response=1,
    )

    with pytest.raises(AgentLimitError, match="tool call limit exceeded"):
        asyncio.run(collect(runtime, session))

    assert tool.values == []


def test_runtime_uses_configured_model_round_limit(tmp_path: Path) -> None:
    model = FakeModel(
        (call_response(tool_call()),),
        features=ModelFeatures(tools=True),
    )
    sessions, session, _runtime, tool = setup_runtime(tmp_path, model)
    runtime = AgentRuntime(
        sessions,
        lambda: model,
        ToolRegistry((tool,)),
        events=EnvelopeEventEmitter(),
        max_model_rounds=1,
    )

    with pytest.raises(AgentLimitError, match="model round limit exceeded"):
        asyncio.run(collect(runtime, session))

    assert tool.values == []


@pytest.mark.parametrize(
    ("max_model_rounds", "max_tool_calls", "message"),
    (
        (0, MAX_TOOL_CALLS_PER_RESPONSE, "max_model_rounds must be positive"),
        (
            MAX_MODEL_ROUNDS,
            0,
            "max_tool_calls_per_response must be positive",
        ),
    ),
)
def test_runtime_rejects_non_positive_limits(
    tmp_path: Path,
    max_model_rounds: int,
    max_tool_calls: int,
    message: str,
) -> None:
    model = FakeModel(())
    sessions, _session, _runtime, tool = setup_runtime(tmp_path, model)

    with pytest.raises(ValueError, match=message):
        AgentRuntime(
            sessions,
            lambda: model,
            ToolRegistry((tool,)),
            events=EnvelopeEventEmitter(),
            max_model_rounds=max_model_rounds,
            max_tool_calls_per_response=max_tool_calls,
        )


def test_execution_starts_only_after_durable_state_transition(
    tmp_path: Path,
) -> None:
    class InspectingTool(RuntimeTool):
        sessions: SessionManager
        session: Session
        approval_id: str

        async def execute(self, arguments: BaseModel) -> str:
            approval = self.sessions.get_approval(
                "my-project", str(self.session.id), self.approval_id
            )
            assert approval.state is ApprovalState.EXECUTING
            return await super().execute(arguments)

    tool = InspectingTool(effect=ToolEffect.WRITE)
    model = FakeModel(
        (call_response(tool_call()), text_response()),
        stream_chunks=((), ("done",)),
        features=ModelFeatures(tools=True),
    )
    sessions, session, runtime, _tool = setup_runtime(tmp_path, model, tool)
    pending = asyncio.run(
        _collect_runtime(runtime.run("hello", "my-project", str(session.id)))
    )
    event = pending[0]
    assert isinstance(event, ApprovalStreamEvent)
    tool.sessions = sessions
    tool.session = session
    tool.approval_id = event.approval.id

    asyncio.run(
        _collect_runtime(
            runtime.resolve_approval(
                "my-project",
                str(session.id),
                event.approval.id,
                approved=True,
            )
        )
    )

    assert tool.values == ["one"]


def test_multiple_write_calls_require_separate_single_use_approvals(
    tmp_path: Path,
) -> None:
    model = FakeModel(
        (
            call_response(
                tool_call("call-1"),
                tool_call("call-2", arguments_json='{"value":"two"}'),
            ),
            text_response(),
        ),
        stream_chunks=((), ("done",)),
        features=ModelFeatures(tools=True),
    )
    sessions, session, runtime, tool = setup_runtime(
        tmp_path,
        model,
        RuntimeTool(effect=ToolEffect.WRITE),
    )
    first_events = asyncio.run(
        _collect_runtime(runtime.run("hello", "my-project", str(session.id)))
    )
    first = first_events[0]
    assert isinstance(first, ApprovalStreamEvent)

    second_events = asyncio.run(
        _collect_runtime(
            runtime.resolve_approval(
                "my-project",
                str(session.id),
                first.approval.id,
                approved=True,
            )
        )
    )
    second = second_events[0]
    assert isinstance(second, ApprovalStreamEvent)
    assert first.approval.id != second.approval.id
    assert tool.values == ["one"]

    final = asyncio.run(
        _collect_runtime(
            runtime.resolve_approval(
                "my-project",
                str(session.id),
                second.approval.id,
                approved=True,
            )
        )
    )

    assert tool.values == ["one", "two"]
    assert isinstance(final[-1], PromptStreamEvent)
    assert final[-1].done
    assert [
        approval.state
        for approval in sessions.get("my-project", str(session.id)).approvals
    ] == [ApprovalState.COMPLETED, ApprovalState.COMPLETED]


def test_failed_completion_checkpoint_leaves_execution_indeterminate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = FakeModel(
        (call_response(tool_call()),),
        features=ModelFeatures(tools=True),
    )
    sessions, session, runtime, tool = setup_runtime(
        tmp_path,
        model,
        RuntimeTool(effect=ToolEffect.WRITE),
    )
    pending = asyncio.run(
        _collect_runtime(runtime.run("hello", "my-project", str(session.id)))
    )
    event = pending[0]
    assert isinstance(event, ApprovalStreamEvent)
    transition = sessions.transition_approval

    def fail_completion(
        workspace_name: str,
        session_id: str,
        approval_id: str,
        *,
        expected: ApprovalState,
        state: ApprovalState,
        result: ToolResultPart | None = None,
        messages: Iterable[Message] | None = None,
    ) -> Session:
        if state is ApprovalState.COMPLETED:
            raise OSError("persistence failed")
        return transition(
            workspace_name,
            session_id,
            approval_id,
            expected=expected,
            state=state,
            result=result,
            messages=messages,
        )

    monkeypatch.setattr(sessions, "transition_approval", fail_completion)

    with pytest.raises(OSError, match="persistence failed"):
        asyncio.run(
            _collect_runtime(
                runtime.resolve_approval(
                    "my-project",
                    str(session.id),
                    event.approval.id,
                    approved=True,
                )
            )
        )

    assert tool.values == ["one"]
    assert (
        sessions.get_approval(
            "my-project", str(session.id), event.approval.id
        ).state
        is ApprovalState.EXECUTING
    )
    monkeypatch.setattr(sessions, "transition_approval", transition)
    sessions.recover_executing_approvals("my-project", str(session.id))
    assert (
        sessions.get_approval(
            "my-project", str(session.id), event.approval.id
        ).state
        is ApprovalState.INDETERMINATE
    )


def test_concurrent_approval_cannot_execute_twice(tmp_path: Path) -> None:
    async def exercise() -> None:
        started = asyncio.Event()

        class BlockingWriteTool(RuntimeTool):
            async def execute(self, arguments: BaseModel) -> str:
                assert isinstance(arguments, Arguments)
                self.values.append(arguments.value)
                started.set()
                await asyncio.Event().wait()
                return "unreachable"

        model = FakeModel(
            (call_response(tool_call()),),
            features=ModelFeatures(tools=True),
        )
        sessions, session, first_runtime, tool = setup_runtime(
            tmp_path,
            model,
            BlockingWriteTool(effect=ToolEffect.WRITE),
        )
        pending = await _collect_runtime(
            first_runtime.run("hello", "my-project", str(session.id))
        )
        event = pending[0]
        assert isinstance(event, ApprovalStreamEvent)
        second_runtime = AgentRuntime(
            sessions,
            lambda: FakeModel(()),
            ToolRegistry((tool,)),
            events=EnvelopeEventEmitter(),
        )
        executing = asyncio.create_task(
            _collect_runtime(
                first_runtime.resolve_approval(
                    "my-project",
                    str(session.id),
                    event.approval.id,
                    approved=True,
                )
            )
        )
        await started.wait()

        with pytest.raises(ApprovalStateError, match="runtime is busy"):
            await _collect_runtime(
                second_runtime.resolve_approval(
                    "my-project",
                    str(session.id),
                    event.approval.id,
                    approved=True,
                )
            )

        executing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await executing
        assert tool.values == ["one"]

    asyncio.run(exercise())
