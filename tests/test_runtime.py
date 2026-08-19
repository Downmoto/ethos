import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Iterable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

import pytest
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from ethos.config import EthosSettings
from ethos.provider import AIProvider
from ethos.runtime import AgentRuntime, PromptStreamEvent
from ethos.sessions import Session, SessionManager
from ethos.workspaces import WorkspaceManager


def settings() -> EthosSettings:
    return EthosSettings.model_validate(
        {
            "provider": {"name": "openai", "model_name": "gpt-5-mini"},
            "keys": {"openai_api_key": "test-key"},
        }
    )


@pytest.fixture(autouse=True)
def configured_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ethos.runtime.get_settings", settings)


def test_runtime_returns_model_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        AIProvider,
        "model",
        lambda _provider, _model_name: TestModel(  # pyright: ignore
            custom_output_text="hello from ethos"
        ),  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    )

    workspaces = WorkspaceManager(tmp_path / "workspaces")
    workspaces.create("my-project")
    sessions = SessionManager(workspaces, tmp_path / "sessions")
    session = sessions.create("my-project")
    runtime = AgentRuntime(sessions)

    async def collect_events() -> list[PromptStreamEvent]:
        return [
            event
            async for event in runtime.run(
                "hello", "my-project", str(session.id)
            )
        ]

    events = asyncio.run(collect_events())

    assert "".join(event.text for event in events) == "hello from ethos"
    assert events[-1].done
    assert events[-1].usage is not None
    assert events[-1].usage.output_tokens > 0


def test_runtime_yields_non_overlapping_provider_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def respond(
        _messages: list[ModelMessage], _info: AgentInfo
    ) -> AsyncIterator[str]:
        yield "first"
        await asyncio.sleep(0.15)
        yield " second"

    monkeypatch.setattr(
        AIProvider,
        "model",
        lambda _provider, _model_name: FunctionModel(  # pyright: ignore
            stream_function=respond
        ),  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    )
    workspaces = WorkspaceManager(tmp_path / "workspaces")
    workspaces.create("my-project")
    sessions = SessionManager(workspaces, tmp_path / "sessions")
    session = sessions.create("my-project")

    async def collect() -> list[PromptStreamEvent]:
        return [
            event
            async for event in AgentRuntime(sessions).run(
                "hello", "my-project", str(session.id)
            )
        ]

    events = asyncio.run(collect())

    assert [event.text for event in events] == ["first", " second", ""]
    assert [event.done for event in events] == [False, False, True]


def test_runtime_does_not_persist_provider_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail(
        _messages: list[ModelMessage], _info: AgentInfo
    ) -> AsyncIterator[str]:
        if False:
            yield ""
        raise RuntimeError("provider failed")

    monkeypatch.setattr(
        AIProvider,
        "model",
        lambda _provider, _model_name: FunctionModel(  # pyright: ignore
            stream_function=fail
        ),  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    )
    workspaces = WorkspaceManager(tmp_path / "workspaces")
    workspaces.create("my-project")
    sessions = SessionManager(workspaces, tmp_path / "sessions")
    session = sessions.create("my-project")

    async def collect() -> None:
        _ = [
            event
            async for event in AgentRuntime(sessions).run(
                "hello", "my-project", str(session.id)
            )
        ]

    with pytest.raises(RuntimeError, match="provider failed"):
        asyncio.run(collect())

    assert sessions.get("my-project", str(session.id)).messages == ()


def test_runtime_keeps_partial_output_but_not_failed_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_after_output(
        _messages: list[ModelMessage], _info: AgentInfo
    ) -> AsyncIterator[str]:
        yield "partial"
        await asyncio.sleep(0.15)
        raise RuntimeError("stream failed")

    monkeypatch.setattr(
        AIProvider,
        "model",
        lambda _provider, _model_name: FunctionModel(  # pyright: ignore
            stream_function=fail_after_output
        ),  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    )
    workspaces = WorkspaceManager(tmp_path / "workspaces")
    workspaces.create("my-project")
    sessions = SessionManager(workspaces, tmp_path / "sessions")
    session = sessions.create("my-project")
    events: list[PromptStreamEvent] = []

    async def collect() -> None:
        async for event in AgentRuntime(sessions).run(
            "hello", "my-project", str(session.id)
        ):
            events.append(event)

    with pytest.raises(RuntimeError, match="stream failed"):
        asyncio.run(collect())

    assert [event.text for event in events] == ["partial"]
    assert not any(event.done for event in events)
    assert sessions.get("my-project", str(session.id)).messages == ()


def test_runtime_does_not_persist_cancelled_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocked = asyncio.Event()

    async def respond(
        _messages: list[ModelMessage], _info: AgentInfo
    ) -> AsyncIterator[str]:
        yield "partial"
        await blocked.wait()
        yield "unreachable"

    monkeypatch.setattr(
        AIProvider,
        "model",
        lambda _provider, _model_name: FunctionModel(  # pyright: ignore
            stream_function=respond
        ),  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    )
    workspaces = WorkspaceManager(tmp_path / "workspaces")
    workspaces.create("my-project")
    sessions = SessionManager(workspaces, tmp_path / "sessions")
    session = sessions.create("my-project")

    async def cancel_turn() -> None:
        received = asyncio.Event()

        async def consume() -> None:
            async for event in AgentRuntime(sessions).run(
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


def test_runtime_does_not_persist_abandoned_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        AIProvider,
        "model",
        lambda _provider, _model_name: TestModel(  # pyright: ignore
            custom_output_text="partial"
        ),  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    )
    workspaces = WorkspaceManager(tmp_path / "workspaces")
    workspaces.create("my-project")
    sessions = SessionManager(workspaces, tmp_path / "sessions")
    session = sessions.create("my-project")
    runtime = AgentRuntime(sessions)

    class FakeResult:
        usage = RunUsage()

        async def stream_text(self) -> AsyncIterator[str]:
            yield "partial"
            await asyncio.Event().wait()

    @asynccontextmanager
    async def run_stream(
        *_args: object, **_kwargs: object
    ) -> AsyncIterator[FakeResult]:
        yield FakeResult()

    monkeypatch.setattr(runtime._agent, "run_stream", run_stream)

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
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        AIProvider,
        "model",
        lambda _provider, _model_name: TestModel(  # pyright: ignore
            custom_output_text="response"
        ),  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    )
    workspaces = WorkspaceManager(tmp_path / "workspaces")
    workspaces.create("my-project")
    sessions = SessionManager(workspaces, tmp_path / "sessions")
    session = sessions.create("my-project")
    replace_messages = sessions.replace_messages
    observed: list[str] = []

    def record_replacement(
        workspace_name: str,
        session_id: str,
        messages: Iterable[ModelMessage],
    ) -> Session:
        observed.append("persisted")
        return replace_messages(workspace_name, session_id, messages)

    monkeypatch.setattr(sessions, "replace_messages", record_replacement)

    async def collect() -> None:
        async for event in AgentRuntime(sessions).run(
            "hello", "my-project", str(session.id)
        ):
            observed.append("done" if event.done else "text")

    asyncio.run(collect())

    assert observed == ["text", "persisted", "done"]


def test_runtime_does_not_complete_when_persistence_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        AIProvider,
        "model",
        lambda _provider, _model_name: TestModel(  # pyright: ignore
            custom_output_text="response"
        ),  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    )
    workspaces = WorkspaceManager(tmp_path / "workspaces")
    workspaces.create("my-project")
    sessions = SessionManager(workspaces, tmp_path / "sessions")
    session = sessions.create("my-project")
    events: list[PromptStreamEvent] = []

    def fail_replacement(*_args: object) -> None:
        raise OSError("persistence failed")

    monkeypatch.setattr(sessions, "replace_messages", fail_replacement)

    async def collect() -> None:
        async for event in AgentRuntime(sessions).run(
            "hello", "my-project", str(session.id)
        ):
            events.append(event)

    with pytest.raises(OSError, match="persistence failed"):
        asyncio.run(collect())

    assert events
    assert not any(event.done for event in events)
    assert sessions.get("my-project", str(session.id)).messages == ()


def test_runtime_keeps_conversation_history_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[list[ModelMessage]] = []

    async def respond(
        messages: list[ModelMessage], _info: AgentInfo
    ) -> AsyncIterator[str]:
        requests.append(messages)
        yield "response"

    monkeypatch.setattr(
        AIProvider,
        "model",
        lambda _provider, _model_name: FunctionModel(  # pyright: ignore
            stream_function=respond
        ),  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    )
    workspace_root = tmp_path / "workspaces"
    workspaces = WorkspaceManager(workspace_root)
    workspaces.create("my-project")
    sessions_root = tmp_path / "sessions"
    sessions = SessionManager(workspaces, sessions_root)
    first = sessions.create("my-project")
    second = sessions.create("my-project")

    async def run_turns() -> None:
        runtime = AgentRuntime(sessions)
        _ = [
            event
            async for event in runtime.run("first", "my-project", str(first.id))
        ]

        restarted = AgentRuntime(
            SessionManager(WorkspaceManager(workspace_root), sessions_root)
        )
        _ = [
            event
            async for event in restarted.run(
                "second", "my-project", str(first.id)
            )
        ]
        _ = [
            event
            async for event in restarted.run(
                "separate", "my-project", str(second.id)
            )
        ]

    asyncio.run(run_turns())

    assert [len(messages) for messages in requests] == [1, 3, 1]
    prompts = [
        part.content
        for message in requests[1]
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]
    assert prompts == ["first", "second"]


def test_runtime_serialises_each_conversation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = 0
    most_active = 0

    async def respond(
        _messages: list[ModelMessage], _info: AgentInfo
    ) -> AsyncIterator[str]:
        nonlocal active, most_active
        active += 1
        most_active = max(most_active, active)
        await asyncio.sleep(0.01)
        yield "response"
        active -= 1

    monkeypatch.setattr(
        AIProvider,
        "model",
        lambda _provider, _model_name: FunctionModel(  # pyright: ignore
            stream_function=respond
        ),  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    )
    workspaces = WorkspaceManager(tmp_path / "workspaces")
    workspaces.create("my-project")
    sessions = SessionManager(workspaces, tmp_path / "sessions")
    first = sessions.create("my-project")
    second = sessions.create("my-project")
    runtime = AgentRuntime(sessions)

    async def collect(prompt: str, session_id: str) -> None:
        _ = [
            event
            async for event in runtime.run(prompt, "my-project", session_id)
        ]

    async def run_concurrently() -> None:
        nonlocal most_active
        await asyncio.gather(
            collect("first", str(first.id)),
            collect("second", str(first.id)),
        )
        assert most_active == 1

        most_active = 0
        await asyncio.gather(
            collect("third", str(first.id)),
            collect("separate", str(second.id)),
        )
        assert most_active == 2

    asyncio.run(run_concurrently())

    prompts = [
        part.content
        for message in sessions.get("my-project", str(first.id)).messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]
    assert prompts == ["first", "second", "third"]


def test_runtime_rejects_archived_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        AIProvider,
        "model",
        lambda _provider, _model_name: TestModel(),  # pyright: ignore
    )
    workspaces = WorkspaceManager(tmp_path / "workspaces")
    workspaces.create("my-project")
    sessions = SessionManager(workspaces, tmp_path / "sessions")
    session = sessions.create("my-project")
    sessions.archive("my-project", str(session.id))
    runtime = AgentRuntime(sessions)

    async def collect() -> None:
        _ = [
            event
            async for event in runtime.run(
                "hello", "my-project", str(session.id)
            )
        ]

    with pytest.raises(ValueError, match=f"session is archived: {session.id}"):
        asyncio.run(collect())
