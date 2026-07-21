import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from ethos.config import EthosSettings
from ethos.environments import WorkspaceEnvironment, WorkspaceMemory
from ethos.provider import AIProvider
from ethos.runtime import AgentRuntime, PromptStreamEvent
from ethos.sessions import SessionManager
from ethos.storage import Storage
from ethos.workspaces import WorkspaceManager


def settings() -> EthosSettings:
    return EthosSettings.model_validate(
        {
            "provider": {"name": "openai", "model_name": "gpt-5-mini"},
            "keys": {"openai_api_key": "test-key"},
        }
    )


def environment(
    workspaces: WorkspaceManager, storage: Storage
) -> WorkspaceEnvironment:
    workspace = workspaces.get("my-project")
    return WorkspaceEnvironment(
        workspace=workspace,
        settings=settings(),
        toolsets=(),
        skills=(),
        memory=WorkspaceMemory(
            workspace_name=workspace.name,
            storage=storage,
        ),
    )


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
    sessions = SessionManager(workspaces)
    session = sessions.create("my-project")
    storage = Storage(tmp_path / "ethos.db")
    runtime = AgentRuntime(
        sessions, lambda _workspace: environment(workspaces, storage)
    )

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
    storage.close()


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
    sessions = SessionManager(workspaces)
    first = sessions.create("my-project")
    second = sessions.create("my-project")
    storage = Storage(tmp_path / "ethos.db")

    async def run_turns() -> None:
        runtime = AgentRuntime(
            sessions, lambda _workspace: environment(workspaces, storage)
        )
        _ = [
            event
            async for event in runtime.run("first", "my-project", str(first.id))
        ]

        restarted = AgentRuntime(
            SessionManager(WorkspaceManager(workspace_root)),
            lambda _workspace: environment(workspaces, storage),
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
    storage.close()


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
    sessions = SessionManager(workspaces)
    first = sessions.create("my-project")
    second = sessions.create("my-project")
    storage = Storage(tmp_path / "ethos.db")
    runtime = AgentRuntime(
        sessions, lambda _workspace: environment(workspaces, storage)
    )

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
    storage.close()


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
    sessions = SessionManager(workspaces)
    session = sessions.create("my-project")
    sessions.archive("my-project", str(session.id))
    storage = Storage(tmp_path / "ethos.db")
    runtime = AgentRuntime(
        sessions, lambda _workspace: environment(workspaces, storage)
    )

    async def collect() -> None:
        _ = [
            event
            async for event in runtime.run(
                "hello", "my-project", str(session.id)
            )
        ]

    with pytest.raises(ValueError, match=f"session is archived: {session.id}"):
        asyncio.run(collect())
    storage.close()
