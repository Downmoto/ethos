import asyncio
from pathlib import Path

import pytest
from pydantic import JsonValue, ValidationError

from ethos.commands import (
    CommandDispatcher,
    CommandEvent,
    CommandRequest,
    register_workspace_commands,
)
from ethos.events.emitters import EnvelopeEventEmitter
from ethos.events.listeners import EventListenerRegistry
from ethos.events.models import EventEnvelope
from ethos.events.types import EventType
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
) -> list[CommandEvent]:
    async def collect() -> list[CommandEvent]:
        return [event async for event in dispatcher.execute(command)]

    return asyncio.run(collect())


def dispatcher(
    tmp_path: Path,
) -> tuple[CommandDispatcher, WorkspaceManager, list[EventEnvelope]]:
    manager = WorkspaceManager(tmp_path / "workspaces")
    commands = CommandDispatcher()
    events: list[EventEnvelope] = []
    listeners = EventListenerRegistry()

    async def capture(event: EventEnvelope) -> None:
        events.append(event)

    listeners.register(capture)
    emitter = EnvelopeEventEmitter(enabled=True, dispatcher=listeners)
    register_workspace_commands(commands, manager, emitter)
    return commands, manager, events


def test_workspace_create_command(tmp_path: Path) -> None:
    commands, manager, emitted = dispatcher(tmp_path)

    events = execute(
        commands, request("workspace.create", {"name": "my-project"})
    )

    workspace = manager.get("my-project")
    assert events == [
        CommandEvent(
            text="workspace created: my-project",
            data={
                "workspace": {
                    "name": "my-project",
                    "path": str(workspace.path),
                }
            },
        )
    ]
    assert len(emitted) == 1
    assert emitted[0].type is EventType.WORKSPACE_CREATE
    assert emitted[0].source.name == "cli"
    assert emitted[0].source.detail == "workspace.create"
    assert emitted[0].tags == ("my-project",)
    assert emitted[0].payload.model_dump() == {
        "schema_name": "workspace.command",
        "schema_version": 1,
        "owner_id": "owner",
        "external_context": {},
        "workspaces": ({"name": "my-project", "path": str(workspace.path)},),
    }


def test_workspace_list_command(tmp_path: Path) -> None:
    commands, manager, emitted = dispatcher(tmp_path)
    manager.create("zeta")
    manager.create("alpha")

    events = execute(commands, request("workspace.list", {}))

    assert events[0].text == "alpha\nzeta"
    assert events[0].data["workspaces"] == [
        {"name": "alpha", "path": str(manager.get("alpha").path)},
        {"name": "zeta", "path": str(manager.get("zeta").path)},
    ]
    assert [event.type for event in emitted] == [EventType.WORKSPACE_LIST]
    assert emitted[0].tags == ("alpha", "zeta")


def test_workspace_show_command(tmp_path: Path) -> None:
    commands, manager, emitted = dispatcher(tmp_path)
    workspace = manager.create("my-project")

    events = execute(
        commands, request("workspace.show", {"name": "my-project"})
    )

    assert events == [
        CommandEvent(
            text=f"my-project\t{workspace.path}",
            data={
                "workspace": {
                    "name": "my-project",
                    "path": str(workspace.path),
                }
            },
        )
    ]
    assert [event.type for event in emitted] == [EventType.WORKSPACE_SHOW]


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("workspace.create", {}),
        ("workspace.show", {"name": "my-project", "extra": "value"}),
        ("workspace.list", {"name": "my-project"}),
    ],
)
def test_workspace_commands_validate_arguments(
    tmp_path: Path, name: str, arguments: dict[str, JsonValue]
) -> None:
    commands, _manager, emitted = dispatcher(tmp_path)

    with pytest.raises(ValidationError):
        execute(commands, request(name, arguments))

    assert emitted == []
