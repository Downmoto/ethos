import asyncio
from collections.abc import AsyncIterator, Iterable
from pathlib import Path

import pytest
from pydantic import BaseModel

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
    PromptStreamEvent,
)
from ethos.sessions import Session, SessionManager
from ethos.tools import ToolEffect, ToolRegistry
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
) -> ModelResponse:
    parts: tuple[TextPart | ToolCallPart, ...] = (
        *((TextPart(text=text),) if text is not None else ()),
        *calls,
    )
    return ModelResponse(
        parts=parts,
        usage=Usage(input_tokens=2, output_tokens=1),
        finish_reason=finish_reason,
    )


def text_response(text: str = "done") -> ModelResponse:
    return ModelResponse(
        parts=(TextPart(text=text),),
        usage=Usage(input_tokens=3, output_tokens=2),
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
    runtime = AgentRuntime(sessions, lambda: model, registry)
    return sessions, session, runtime, registered


async def collect(
    runtime: AgentRuntime,
    session: Session,
) -> list[PromptStreamEvent]:
    return [
        event
        async for event in runtime.run(
            "hello", session.workspace_name, str(session.id)
        )
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
            ),
            text_response("It is one."),
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
        usage=Usage(input_tokens=5, output_tokens=3), done=True
    )
    assert tool.values == ["one"]
    assert len(model.requests) == 2
    assert all(
        request.tools == (tool.definition,) for request in model.requests
    )
    assert [message.role for message in model.requests[1].messages] == [
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
        *model.requests[1].messages,
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
    assert [len(request.messages) for request in model.requests] == [1, 3, 5]


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
    for message in history[2:]:
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
            "denial",
            tool_call(),
            ToolEffect.WRITE,
            None,
            "write tools are not allowed",
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
            max_model_rounds=max_model_rounds,
            max_tool_calls_per_response=max_tool_calls,
        )
