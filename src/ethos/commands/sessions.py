"""Session command handlers."""

from collections.abc import AsyncIterator, Callable

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ethos.commands.dispatcher import CommandDispatcher
from ethos.commands.models import (
    CommandRequest,
    CommandResponse,
    CommandUsage,
)
from ethos.events import event_factory
from ethos.events.emitters import EnvelopeEventEmitter
from ethos.events.models import EventPayload
from ethos.events.types import EventType
from ethos.runtime import PromptStreamEvent
from ethos.sessions import Session, SessionManager

type SessionRunner = Callable[[str, str, str], AsyncIterator[PromptStreamEvent]]


class _WorkspaceArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace: str


class _SessionArguments(_WorkspaceArguments):
    session_id: str


class _ChatArguments(_SessionArguments):
    prompt: str = Field(min_length=1)


class _SessionEventItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    workspace: str
    archived: bool


class _SessionCommandPayload(EventPayload):
    model_config = ConfigDict(frozen=True, extra="forbid")

    owner_id: str
    external_context: dict[str, str]
    sessions: tuple[_SessionEventItem, ...]


def _session_data(session: Session) -> dict[str, JsonValue]:
    return {
        "id": str(session.id),
        "workspace": session.workspace_name,
        "created_at": session.created_at.isoformat(),
        "archived_at": (
            session.archived_at.isoformat() if session.archived_at else None
        ),
        "archived": session.archived,
        "message_count": len(session.messages),
    }


async def _emit_session_event(
    emitter: EnvelopeEventEmitter,
    request: CommandRequest,
    event_type: EventType,
    sessions: tuple[Session, ...],
) -> None:
    await emitter.emit(
        event_factory(
            event_type,
            location=request.source,
            details=request.name,
            payload=_SessionCommandPayload(
                schema_name="session.command",
                owner_id=request.owner_id,
                external_context=request.external_context,
                sessions=tuple(
                    _SessionEventItem(
                        id=str(session.id),
                        workspace=session.workspace_name,
                        archived=session.archived,
                    )
                    for session in sessions
                ),
            ),
            tags=tuple(
                tag
                for session in sessions
                for tag in (session.workspace_name, str(session.id))
            ),
        )
    )


def register_session_commands(
    dispatcher: CommandDispatcher,
    manager: SessionManager,
    emitter: EnvelopeEventEmitter,
    run_session: SessionRunner,
) -> None:
    """Register universal session commands on a dispatcher."""

    async def create(request: CommandRequest) -> AsyncIterator[CommandResponse]:
        arguments = _WorkspaceArguments.model_validate(request.arguments)
        session = manager.create(arguments.workspace)
        await _emit_session_event(
            emitter, request, EventType.SESSION_CREATE, (session,)
        )
        yield CommandResponse(
            text=f"session created: {session.id}",
            data={"session": _session_data(session)},
        )

    async def list_sessions(
        request: CommandRequest,
    ) -> AsyncIterator[CommandResponse]:
        arguments = _WorkspaceArguments.model_validate(request.arguments)
        sessions = manager.list(arguments.workspace)
        await _emit_session_event(
            emitter, request, EventType.SESSION_LIST, sessions
        )
        yield CommandResponse(
            text="\n".join(
                f"{session.id}\t{'archived' if session.archived else 'active'}"
                for session in sessions
            ),
            data={"sessions": [_session_data(session) for session in sessions]},
        )

    async def show(request: CommandRequest) -> AsyncIterator[CommandResponse]:
        arguments = _SessionArguments.model_validate(request.arguments)
        session = manager.get(arguments.workspace, arguments.session_id)
        await _emit_session_event(
            emitter, request, EventType.SESSION_SHOW, (session,)
        )
        status = "archived" if session.archived else "active"
        yield CommandResponse(
            text=f"{session.id}\t{session.workspace_name}\t{status}",
            data={"session": _session_data(session)},
        )

    async def archive(
        request: CommandRequest,
    ) -> AsyncIterator[CommandResponse]:
        arguments = _SessionArguments.model_validate(request.arguments)
        session = manager.archive(arguments.workspace, arguments.session_id)
        await _emit_session_event(
            emitter, request, EventType.SESSION_ARCHIVE, (session,)
        )
        yield CommandResponse(
            text=f"session archived: {session.id}",
            data={"session": _session_data(session)},
        )

    async def chat(request: CommandRequest) -> AsyncIterator[CommandResponse]:
        arguments = _ChatArguments.model_validate(request.arguments)
        emitted = False
        async for event in run_session(
            arguments.prompt,
            arguments.workspace,
            arguments.session_id,
        ):
            usage = (
                CommandUsage(
                    input_tokens=event.usage.input_tokens,
                    output_tokens=event.usage.output_tokens,
                )
                if event.usage is not None
                else None
            )
            command_response = CommandResponse(
                text=event.text,
                data={
                    "workspace": arguments.workspace,
                    "session_id": arguments.session_id,
                },
                usage=usage,
                done=event.done,
            )
            if event.done:
                session = manager.get(arguments.workspace, arguments.session_id)
                await _emit_session_event(
                    emitter, request, EventType.SESSION_CHAT, (session,)
                )
                emitted = True
            yield command_response
        if not emitted:
            session = manager.get(arguments.workspace, arguments.session_id)
            await _emit_session_event(
                emitter, request, EventType.SESSION_CHAT, (session,)
            )

    dispatcher.register("session.create", create)
    dispatcher.register("session.list", list_sessions)
    dispatcher.register("session.show", show)
    dispatcher.register("session.archive", archive)
    dispatcher.register("session.chat", chat)
