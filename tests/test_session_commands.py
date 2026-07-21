import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic import JsonValue, ValidationError
from pydantic_ai.usage import RunUsage

from ethos.commands import (
    CommandDispatcher,
    CommandRequest,
    CommandResponse,
    CommandUsage,
    register_session_commands,
)
from ethos.events.emitters import EnvelopeEventEmitter
from ethos.events.listeners import EventListenerRegistry
from ethos.events.models import EventEnvelope
from ethos.events.types import EventType
from ethos.runtime import PromptStreamEvent
from ethos.sessions import SessionManager
from ethos.workspaces import WorkspaceManager


def request(name: str, arguments: dict[str, JsonValue]) -> CommandRequest:
    return CommandRequest(
        name=name,
        arguments=arguments,
        source="cli",
        owner_id="owner",
    )


def execute(
    dispatcher: CommandDispatcher, command: CommandRequest
) -> list[CommandResponse]:
    async def collect() -> list[CommandResponse]:
        return [event async for event in dispatcher.execute(command)]

    return asyncio.run(collect())


def command_context(
    tmp_path: Path,
) -> tuple[
    CommandDispatcher,
    SessionManager,
    list[EventEnvelope],
    list[tuple[str, str, str]],
]:
    workspaces = WorkspaceManager(tmp_path / "workspaces")
    workspaces.create("my-project")
    sessions = SessionManager(workspaces)
    commands = CommandDispatcher()
    events: list[EventEnvelope] = []
    runs: list[tuple[str, str, str]] = []
    listeners = EventListenerRegistry()

    async def capture(event: EventEnvelope) -> None:
        events.append(event)

    async def run_session(
        prompt: str, workspace: str, session_id: str
    ) -> AsyncIterator[PromptStreamEvent]:
        runs.append((prompt, workspace, session_id))
        yield PromptStreamEvent(text="reply")
        yield PromptStreamEvent(
            usage=RunUsage(input_tokens=3, output_tokens=1), done=True
        )

    listeners.register(capture)
    register_session_commands(
        commands,
        sessions,
        EnvelopeEventEmitter(enabled=True, dispatcher=listeners),
        run_session,
    )
    return commands, sessions, events, runs


def test_session_lifecycle_commands(tmp_path: Path) -> None:
    commands, sessions, emitted, _runs = command_context(tmp_path)

    created = execute(
        commands, request("session.create", {"workspace": "my-project"})
    )
    session_data = created[0].data["session"]
    assert isinstance(session_data, dict)
    session_id = session_data["id"]
    assert isinstance(session_id, str)

    listed = execute(
        commands, request("session.list", {"workspace": "my-project"})
    )
    shown = execute(
        commands,
        request(
            "session.show",
            {"workspace": "my-project", "session_id": session_id},
        ),
    )
    archived = execute(
        commands,
        request(
            "session.archive",
            {"workspace": "my-project", "session_id": session_id},
        ),
    )

    assert created[0].text == f"session created: {session_id}"
    assert listed[0].text == f"{session_id}\tactive"
    assert shown[0].text == f"{session_id}\tmy-project\tactive"
    assert archived[0].text == f"session archived: {session_id}"
    assert sessions.get("my-project", session_id).archived
    assert [event.type for event in emitted] == [
        EventType.SESSION_CREATE,
        EventType.SESSION_LIST,
        EventType.SESSION_SHOW,
        EventType.SESSION_ARCHIVE,
    ]


def test_session_chat_streams_transport_neutral_events(tmp_path: Path) -> None:
    commands, sessions, emitted, runs = command_context(tmp_path)
    session = sessions.create("my-project")

    events = execute(
        commands,
        request(
            "session.chat",
            {
                "workspace": "my-project",
                "session_id": str(session.id),
                "prompt": "hello",
            },
        ),
    )

    assert runs == [("hello", "my-project", str(session.id))]
    assert events == [
        CommandResponse(
            text="reply",
            data={"workspace": "my-project", "session_id": str(session.id)},
        ),
        CommandResponse(
            data={"workspace": "my-project", "session_id": str(session.id)},
            usage=CommandUsage(input_tokens=3, output_tokens=1),
            done=True,
        ),
    ]
    assert emitted[-1].type is EventType.SESSION_CHAT


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("session.create", {}),
        ("session.list", {"workspace": "my-project", "extra": True}),
        ("session.show", {"workspace": "my-project"}),
        (
            "session.archive",
            {"workspace": "my-project", "session_id": "id", "extra": True},
        ),
        (
            "session.chat",
            {"workspace": "my-project", "session_id": "id"},
        ),
    ],
)
def test_session_commands_validate_arguments(
    tmp_path: Path, name: str, arguments: dict[str, JsonValue]
) -> None:
    commands, _sessions, emitted, _runs = command_context(tmp_path)

    with pytest.raises(ValidationError):
        execute(commands, request(name, arguments))

    assert emitted == []
