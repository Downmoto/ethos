import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterable
from pathlib import Path
from typing import cast

import pytest

from ethos.config import EthosSettings
from ethos.models import (
    FinishReason,
    Message,
    ModelEvent,
    ModelFeatures,
    ModelRequest,
    ModelResponse,
    ResponseCompleted,
    Role,
    TextDelta,
    TextPart,
    ToolCallPart,
    Usage,
)
from ethos.provider import AIProvider, ModelProtocolError
from ethos.runtime import AgentRuntime, PromptStreamEvent
from ethos.sessions import Session, SessionManager
from ethos.workspaces import WorkspaceManager
from fakes import FakeModel

type StreamFunction = Callable[[ModelRequest], AsyncIterator[ModelEvent]]


class StreamModel:
    features = ModelFeatures(tools=False)

    def __init__(self, stream: StreamFunction) -> None:
        self._stream = stream
        self.requests: list[ModelRequest] = []

    async def request(self, request: ModelRequest) -> ModelResponse:
        raise AssertionError("stream model does not support request()")

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        return self._stream(request)


def response(text: str = "response") -> ModelResponse:
    return ModelResponse(
        parts=(TextPart(text=text),),
        usage=Usage(input_tokens=2, output_tokens=1),
        finish_reason=FinishReason.STOP,
        provider_response_id="response-id",
    )


def setup_runtime(
    tmp_path: Path, model: FakeModel | StreamModel
) -> tuple[SessionManager, Session, AgentRuntime]:
    workspaces = WorkspaceManager(tmp_path / "workspaces")
    workspaces.create("my-project")
    sessions = SessionManager(workspaces, tmp_path / "sessions")
    session = sessions.create("my-project")
    return sessions, session, AgentRuntime(sessions, lambda: model)


async def collect(
    runtime: AgentRuntime, session: Session, prompt: str = "hello"
) -> list[PromptStreamEvent]:
    return [
        event
        async for event in runtime.run(
            prompt, session.workspace_name, str(session.id)
        )
    ]


def test_runtime_returns_model_output(tmp_path: Path) -> None:
    model = FakeModel(
        [response("hello from ethos")],
        stream_chunks=[("hello from ethos",)],
    )
    sessions, session, runtime = setup_runtime(tmp_path, model)

    events = asyncio.run(collect(runtime, session))

    assert "".join(event.text for event in events) == "hello from ethos"
    assert events[-1] == PromptStreamEvent(
        usage=Usage(input_tokens=2, output_tokens=1), done=True
    )
    assert sessions.get("my-project", str(session.id)).messages == (
        Message(role=Role.USER, parts=(TextPart(text="hello"),)),
        Message(
            role=Role.ASSISTANT,
            parts=(TextPart(text="hello from ethos"),),
        ),
    )


def test_runtime_default_factory_resolves_settings_once_per_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0
    model = FakeModel(
        [response(), response()],
        stream_chunks=[("response",), ("response",)],
    )

    def load_settings() -> EthosSettings:
        nonlocal calls
        calls += 1
        return EthosSettings.model_validate(
            {
                "provider": {"name": "openai", "model_name": "model"},
                "keys": {"openai_api_key": "key"},
            }
        )

    def create_model(_provider: AIProvider, model_name: str) -> FakeModel:
        assert model_name == "model"
        return model

    monkeypatch.setattr("ethos.runtime.get_settings", load_settings)
    monkeypatch.setattr(AIProvider, "model", create_model)
    sessions, session, _runtime = setup_runtime(tmp_path, model)
    runtime = AgentRuntime(sessions)

    async def run_turns() -> None:
        await collect(runtime, session, "first")
        await collect(runtime, session, "second")

    asyncio.run(run_turns())

    assert calls == 2
    assert [len(request.messages) for request in model.requests] == [1, 3]


def test_runtime_yields_and_stores_non_overlapping_provider_chunks(
    tmp_path: Path,
) -> None:
    model = FakeModel(
        [response("first second")],
        stream_chunks=[("first", " second")],
    )
    sessions, session, runtime = setup_runtime(tmp_path, model)

    events = asyncio.run(collect(runtime, session))

    assert [event.text for event in events] == ["first", " second", ""]
    assert [event.done for event in events] == [False, False, True]
    stored = sessions.get("my-project", str(session.id)).messages
    assert [message.role for message in stored] == [Role.USER, Role.ASSISTANT]
    assert stored[-1].parts == (TextPart(text="first second"),)


def test_runtime_does_not_persist_provider_failure(tmp_path: Path) -> None:
    model = FakeModel([RuntimeError("provider failed")])
    sessions, session, runtime = setup_runtime(tmp_path, model)

    with pytest.raises(RuntimeError, match="provider failed"):
        asyncio.run(collect(runtime, session))

    assert sessions.get("my-project", str(session.id)).messages == ()


def test_runtime_keeps_partial_output_but_not_failed_history(
    tmp_path: Path,
) -> None:
    async def fail_after_output(
        _request: ModelRequest,
    ) -> AsyncIterator[ModelEvent]:
        yield TextDelta(text="partial")
        await asyncio.sleep(0)
        raise RuntimeError("stream failed")

    sessions, session, runtime = setup_runtime(
        tmp_path, StreamModel(fail_after_output)
    )
    events: list[PromptStreamEvent] = []

    async def consume() -> None:
        async for event in runtime.run("hello", "my-project", str(session.id)):
            events.append(event)

    with pytest.raises(RuntimeError, match="stream failed"):
        asyncio.run(consume())

    assert [event.text for event in events] == ["partial"]
    assert not any(event.done for event in events)
    assert sessions.get("my-project", str(session.id)).messages == ()


def test_runtime_does_not_persist_cancelled_turn(tmp_path: Path) -> None:
    blocked = asyncio.Event()

    async def respond(_request: ModelRequest) -> AsyncIterator[ModelEvent]:
        yield TextDelta(text="partial")
        await blocked.wait()
        yield TextDelta(text="unreachable")

    sessions, session, runtime = setup_runtime(tmp_path, StreamModel(respond))

    async def cancel_turn() -> None:
        received = asyncio.Event()

        async def consume() -> None:
            async for event in runtime.run(
                "hello", "my-project", str(session.id)
            ):
                if event.text:
                    received.set()

        task = asyncio.create_task(consume())
        await received.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_turn())

    assert sessions.get("my-project", str(session.id)).messages == ()


def test_runtime_does_not_persist_abandoned_turn(tmp_path: Path) -> None:
    async def respond(_request: ModelRequest) -> AsyncIterator[ModelEvent]:
        yield TextDelta(text="partial")
        await asyncio.Event().wait()

    sessions, session, runtime = setup_runtime(tmp_path, StreamModel(respond))

    async def abandon() -> PromptStreamEvent:
        stream = cast(
            AsyncGenerator[PromptStreamEvent, None],
            runtime.run("hello", "my-project", str(session.id)),
        )
        event = await anext(stream)
        await stream.aclose()
        return event

    event = asyncio.run(abandon())

    assert event.text == "partial"
    assert sessions.get("my-project", str(session.id)).messages == ()


def test_runtime_persists_before_completion_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = FakeModel([response()], stream_chunks=[("response",)])
    sessions, session, runtime = setup_runtime(tmp_path, model)
    replace_messages = sessions.replace_messages
    observed: list[str] = []

    def record_replacement(
        workspace_name: str,
        session_id: str,
        messages: Iterable[Message],
    ) -> Session:
        observed.append("persisted")
        return replace_messages(workspace_name, session_id, messages)

    monkeypatch.setattr(sessions, "replace_messages", record_replacement)

    async def consume() -> None:
        async for event in runtime.run("hello", "my-project", str(session.id)):
            observed.append("done" if event.done else "text")

    asyncio.run(consume())

    assert observed == ["text", "persisted", "done"]


def test_runtime_does_not_complete_when_persistence_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = FakeModel([response()], stream_chunks=[("response",)])
    sessions, session, runtime = setup_runtime(tmp_path, model)
    events: list[PromptStreamEvent] = []

    def fail_replacement(
        workspace_name: str,
        session_id: str,
        messages: Iterable[Message],
    ) -> Session:
        del workspace_name, session_id, messages
        raise OSError("persistence failed")

    monkeypatch.setattr(sessions, "replace_messages", fail_replacement)

    async def consume() -> None:
        async for event in runtime.run("hello", "my-project", str(session.id)):
            events.append(event)

    with pytest.raises(OSError, match="persistence failed"):
        asyncio.run(consume())

    assert events
    assert not any(event.done for event in events)
    assert sessions.get("my-project", str(session.id)).messages == ()


def test_runtime_rejects_malformed_stream_without_completing(
    tmp_path: Path,
) -> None:
    async def malformed(_request: ModelRequest) -> AsyncIterator[ModelEvent]:
        yield TextDelta(text="partial")

    sessions, session, runtime = setup_runtime(tmp_path, StreamModel(malformed))
    events: list[PromptStreamEvent] = []

    async def consume() -> None:
        async for event in runtime.run("hello", "my-project", str(session.id)):
            events.append(event)

    with pytest.raises(ModelProtocolError, match="before completion"):
        asyncio.run(consume())

    assert events == [PromptStreamEvent(text="partial")]
    assert not any(event.done for event in events)
    assert sessions.get("my-project", str(session.id)).messages == ()


def test_runtime_remains_text_only_without_tool_registry(
    tmp_path: Path,
) -> None:
    tool_response = ModelResponse(
        parts=(
            ToolCallPart(
                call_id="call-1",
                name="read_file",
                arguments_json="{}",
            ),
        ),
        finish_reason=FinishReason.TOOL_CALL,
    )
    model = FakeModel([tool_response], stream_chunks=[()])
    sessions, session, runtime = setup_runtime(tmp_path, model)
    events: list[PromptStreamEvent] = []

    async def consume() -> None:
        async for event in runtime.run("hello", "my-project", str(session.id)):
            events.append(event)

    with pytest.raises(ModelProtocolError, match="unsupported parts"):
        asyncio.run(consume())

    assert events == []
    assert sessions.get("my-project", str(session.id)).messages == ()


def test_runtime_keeps_conversation_history_isolated(tmp_path: Path) -> None:
    model = FakeModel(
        [response(), response(), response()],
        stream_chunks=[("response",), ("response",), ("response",)],
    )
    workspace_root = tmp_path / "workspaces"
    workspaces = WorkspaceManager(workspace_root)
    workspaces.create("my-project")
    sessions_root = tmp_path / "sessions"
    sessions = SessionManager(workspaces, sessions_root)
    first = sessions.create("my-project")
    second = sessions.create("my-project")

    async def run_turns() -> None:
        runtime = AgentRuntime(sessions, lambda: model)
        await collect(runtime, first, "first")

        restarted = AgentRuntime(
            SessionManager(WorkspaceManager(workspace_root), sessions_root),
            lambda: model,
        )
        await collect(restarted, first, "second")
        await collect(restarted, second, "separate")

    asyncio.run(run_turns())

    assert [len(request.messages) for request in model.requests] == [1, 3, 1]
    prompts = [
        part.text
        for message in model.requests[1].messages
        if message.role is Role.USER
        for part in message.parts
        if isinstance(part, TextPart)
    ]
    assert prompts == ["first", "second"]


def test_runtime_serialises_each_conversation(tmp_path: Path) -> None:
    active = 0
    most_active = 0

    async def respond(_request: ModelRequest) -> AsyncIterator[ModelEvent]:
        nonlocal active, most_active
        active += 1
        most_active = max(most_active, active)
        await asyncio.sleep(0.01)
        yield TextDelta(text="response")
        yield ResponseCompleted(response=response())
        active -= 1

    model = StreamModel(respond)
    workspaces = WorkspaceManager(tmp_path / "workspaces")
    workspaces.create("my-project")
    sessions = SessionManager(workspaces, tmp_path / "sessions")
    first = sessions.create("my-project")
    second = sessions.create("my-project")
    runtime = AgentRuntime(sessions, lambda: model)

    async def run_concurrently() -> None:
        nonlocal most_active
        await asyncio.gather(
            collect(runtime, first, "first"),
            collect(runtime, first, "second"),
        )
        assert most_active == 1

        most_active = 0
        await asyncio.gather(
            collect(runtime, first, "third"),
            collect(runtime, second, "separate"),
        )
        assert most_active == 2

    asyncio.run(run_concurrently())

    stored = sessions.get("my-project", str(first.id)).messages
    assert [message.role for message in stored] == [
        Role.USER,
        Role.ASSISTANT,
        Role.USER,
        Role.ASSISTANT,
        Role.USER,
        Role.ASSISTANT,
    ]
    prompts = [
        part.text
        for message in stored
        if message.role is Role.USER
        for part in message.parts
        if isinstance(part, TextPart)
    ]
    assert prompts == ["first", "second", "third"]


def test_runtime_rejects_archived_session(tmp_path: Path) -> None:
    model = FakeModel([])
    sessions, session, runtime = setup_runtime(tmp_path, model)
    sessions.archive("my-project", str(session.id))

    with pytest.raises(ValueError, match=f"session is archived: {session.id}"):
        asyncio.run(collect(runtime, session))

    assert model.requests == []
