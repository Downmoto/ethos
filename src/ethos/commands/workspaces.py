"""Transport-neutral workspace operations and their lifecycle events."""

from collections.abc import AsyncIterator

from pydantic import BaseModel, ConfigDict, JsonValue

from ethos.commands.dispatcher import CommandDispatcher
from ethos.commands.models import CommandRequest, CommandResponse
from ethos.events import event_factory
from ethos.events.emitters import EnvelopeEventEmitter
from ethos.events.models import EventPayload
from ethos.events.types import EventType
from ethos.workspaces import Workspace, WorkspaceManager


class _WorkspaceNameArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str


class _NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _WorkspaceEventItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    path: str


class _WorkspaceCommandPayload(EventPayload):
    model_config = ConfigDict(frozen=True, extra="forbid")

    owner_id: str
    external_context: dict[str, str]
    workspaces: tuple[_WorkspaceEventItem, ...]


def _workspace_data(workspace: Workspace) -> dict[str, JsonValue]:
    return {"name": workspace.name, "path": str(workspace.path)}


async def _emit_workspace_event(
    emitter: EnvelopeEventEmitter,
    request: CommandRequest,
    event_type: EventType,
    workspaces: tuple[Workspace, ...],
) -> None:
    payload = _WorkspaceCommandPayload(
        schema_name="workspace.command",
        owner_id=request.owner_id,
        external_context=request.external_context,
        workspaces=tuple(
            _WorkspaceEventItem(name=workspace.name, path=str(workspace.path))
            for workspace in workspaces
        ),
    )
    await emitter.emit(
        event_factory(
            event_type,
            location=request.source,
            details=request.name,
            payload=payload,
            tags=tuple(workspace.name for workspace in workspaces),
        )
    )


def register_workspace_commands(
    dispatcher: CommandDispatcher,
    manager: WorkspaceManager,
    emitter: EnvelopeEventEmitter,
) -> None:
    """Register universal workspace commands on a dispatcher.

    Handlers mutate or read workspace state, emit the corresponding event, and
    only then yield a response. Event failure can therefore follow a completed
    domain operation.
    """

    async def create(
        request: CommandRequest,
    ) -> AsyncIterator[CommandResponse]:
        arguments = _WorkspaceNameArguments.model_validate(request.arguments)
        workspace = manager.create(arguments.name)
        await _emit_workspace_event(
            emitter, request, EventType.WORKSPACE_CREATE, (workspace,)
        )
        yield CommandResponse(
            text=f"workspace created: {workspace.name}",
            data={"workspace": _workspace_data(workspace)},
        )

    async def list_workspaces(
        request: CommandRequest,
    ) -> AsyncIterator[CommandResponse]:
        _NoArguments.model_validate(request.arguments)
        workspaces = manager.list()
        await _emit_workspace_event(
            emitter, request, EventType.WORKSPACE_LIST, workspaces
        )
        yield CommandResponse(
            text="\n".join(workspace.name for workspace in workspaces),
            data={
                "workspaces": [
                    _workspace_data(workspace) for workspace in workspaces
                ]
            },
        )

    async def show(
        request: CommandRequest,
    ) -> AsyncIterator[CommandResponse]:
        arguments = _WorkspaceNameArguments.model_validate(request.arguments)
        workspace = manager.get(arguments.name)
        await _emit_workspace_event(
            emitter, request, EventType.WORKSPACE_SHOW, (workspace,)
        )
        yield CommandResponse(
            text=f"{workspace.name}\t{workspace.path}",
            data={"workspace": _workspace_data(workspace)},
        )

    dispatcher.register("workspace.create", create)
    dispatcher.register("workspace.list", list_workspaces)
    dispatcher.register("workspace.show", show)
