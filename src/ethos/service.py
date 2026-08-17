"""Shared Ethos application behaviour for the CLI and Vox protocol."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.messages import (
    ModelRequest,
    TextContent,
    TextPart,
    UserPromptPart,
)

from ethos.events import create_event_emitter, event_factory
from ethos.events.emitters import EnvelopeEventEmitter
from ethos.events.models import EventPayload
from ethos.events.types import EventType
from ethos.home import DB_PATH
from ethos.runtime import AgentRuntime
from ethos.sessions import SESSIONS_DIR, Session, SessionManager
from ethos.storage import Storage
from ethos.workspaces import WORKSPACES_DIR, Workspace, WorkspaceManager


@dataclass(frozen=True)
class RequestContext:
    """Trusted adapter metadata attached to lifecycle events."""

    source: str
    owner_id: str
    external_context: dict[str, str]


class WorkspaceView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    path: str

    @classmethod
    def from_workspace(cls, workspace: Workspace) -> "WorkspaceView":
        return cls(name=workspace.name, path=str(workspace.path))


class SessionView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    workspace: str
    created_at: str
    archived_at: str | None
    archived: bool
    message_count: int = Field(ge=0)

    @classmethod
    def from_session(cls, session: Session) -> "SessionView":
        return cls(
            id=str(session.id),
            workspace=session.workspace_name,
            created_at=session.created_at.isoformat(),
            archived_at=(
                session.archived_at.isoformat() if session.archived_at else None
            ),
            archived=session.archived,
            message_count=len(session.messages),
        )


class HistoryMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["user", "assistant"]
    text: str


class Usage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class ChatChunk(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = ""
    workspace: str
    session_id: str
    usage: Usage | None = None
    done: bool = False


class _WorkspaceEventItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    path: str


class _SessionEventItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    workspace: str
    archived: bool


class _WorkspaceEventPayload(EventPayload):
    model_config = ConfigDict(frozen=True, extra="forbid")

    owner_id: str
    external_context: dict[str, str]
    workspaces: tuple[_WorkspaceEventItem, ...]


class _SessionEventPayload(EventPayload):
    model_config = ConfigDict(frozen=True, extra="forbid")

    owner_id: str
    external_context: dict[str, str]
    sessions: tuple[_SessionEventItem, ...]


class Ethos:
    """One application lifetime shared by local and HTTP callers."""

    def __init__(
        self,
        home: Path,
    ) -> None:
        self.home = home
        self.storage = Storage(home / DB_PATH)
        self.workspaces = WorkspaceManager(home / WORKSPACES_DIR)
        self.sessions = SessionManager(self.workspaces, home / SESSIONS_DIR)
        self.events = create_event_emitter(self.storage)
        self._agent: AgentRuntime | None = None

    def close(self) -> None:
        self.storage.close()

    def __enter__(self) -> "Ethos":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _runtime(self) -> AgentRuntime:
        if self._agent is None:
            self._agent = AgentRuntime(self.sessions)
        return self._agent

    async def create_workspace(
        self, name: str, context: RequestContext
    ) -> WorkspaceView:
        workspace = self.workspaces.create(name)
        await self._emit_workspaces(
            context, EventType.WORKSPACE_CREATE, (workspace,)
        )
        return WorkspaceView.from_workspace(workspace)

    async def list_workspaces(
        self, context: RequestContext
    ) -> tuple[WorkspaceView, ...]:
        workspaces = self.workspaces.list()
        await self._emit_workspaces(
            context, EventType.WORKSPACE_LIST, workspaces
        )
        return tuple(WorkspaceView.from_workspace(item) for item in workspaces)

    async def show_workspace(
        self, name: str, context: RequestContext
    ) -> WorkspaceView:
        workspace = self.workspaces.get(name)
        await self._emit_workspaces(
            context, EventType.WORKSPACE_SHOW, (workspace,)
        )
        return WorkspaceView.from_workspace(workspace)

    async def create_session(
        self, workspace: str, context: RequestContext
    ) -> SessionView:
        session = self.sessions.create(workspace)
        await self._emit_sessions(context, EventType.SESSION_CREATE, (session,))
        return SessionView.from_session(session)

    async def list_sessions(
        self, workspace: str, context: RequestContext
    ) -> tuple[SessionView, ...]:
        sessions = self.sessions.list(workspace)
        await self._emit_sessions(context, EventType.SESSION_LIST, sessions)
        return tuple(SessionView.from_session(item) for item in sessions)

    async def show_session(
        self, workspace: str, session_id: str, context: RequestContext
    ) -> SessionView:
        session = self.sessions.get(workspace, session_id)
        await self._emit_sessions(context, EventType.SESSION_SHOW, (session,))
        return SessionView.from_session(session)

    async def session_history(
        self, workspace: str, session_id: str, context: RequestContext
    ) -> tuple[HistoryMessage, ...]:
        session = self.sessions.get(workspace, session_id)
        await self._emit_sessions(
            context, EventType.SESSION_HISTORY, (session,)
        )
        return _history(session)

    async def archive_session(
        self, workspace: str, session_id: str, context: RequestContext
    ) -> SessionView:
        session = self.sessions.archive(workspace, session_id)
        await self._emit_sessions(
            context, EventType.SESSION_ARCHIVE, (session,)
        )
        return SessionView.from_session(session)

    async def chat(
        self,
        workspace: str,
        session_id: str,
        prompt: str,
        context: RequestContext,
    ) -> AsyncIterator[ChatChunk]:
        if not prompt:
            raise ValueError("prompt must not be empty")
        emitted = False
        async for event in self._runtime().run(prompt, workspace, session_id):
            usage = (
                Usage(
                    input_tokens=event.usage.input_tokens,
                    output_tokens=event.usage.output_tokens,
                )
                if event.usage is not None
                else None
            )
            if event.done:
                session = self.sessions.get(workspace, session_id)
                await self._emit_sessions(
                    context, EventType.SESSION_CHAT, (session,)
                )
                emitted = True
            yield ChatChunk(
                text=event.text,
                workspace=workspace,
                session_id=session_id,
                usage=usage,
                done=event.done,
            )
        if not emitted:
            session = self.sessions.get(workspace, session_id)
            await self._emit_sessions(
                context, EventType.SESSION_CHAT, (session,)
            )

    async def _emit_workspaces(
        self,
        context: RequestContext,
        event_type: EventType,
        workspaces: tuple[Workspace, ...],
    ) -> None:
        await _emit(
            self.events,
            event_type,
            context,
            _WorkspaceEventPayload(
                schema_name="workspace.operation",
                owner_id=context.owner_id,
                external_context=context.external_context,
                workspaces=tuple(
                    _WorkspaceEventItem(name=item.name, path=str(item.path))
                    for item in workspaces
                ),
            ),
            tuple(item.name for item in workspaces),
        )

    async def _emit_sessions(
        self,
        context: RequestContext,
        event_type: EventType,
        sessions: tuple[Session, ...],
    ) -> None:
        await _emit(
            self.events,
            event_type,
            context,
            _SessionEventPayload(
                schema_name="session.operation",
                owner_id=context.owner_id,
                external_context=context.external_context,
                sessions=tuple(
                    _SessionEventItem(
                        id=str(item.id),
                        workspace=item.workspace_name,
                        archived=item.archived,
                    )
                    for item in sessions
                ),
            ),
            tuple(
                tag
                for item in sessions
                for tag in (item.workspace_name, str(item.id))
            ),
        )


async def _emit(
    emitter: EnvelopeEventEmitter,
    event_type: EventType,
    context: RequestContext,
    payload: EventPayload,
    tags: tuple[str, ...],
) -> None:
    await emitter.emit(
        event_factory(
            event_type,
            location=context.source,
            details=event_type.value,
            payload=payload,
            tags=tags,
        )
    )


def _history(session: Session) -> tuple[HistoryMessage, ...]:
    messages: list[HistoryMessage] = []
    for message in session.messages:
        role: Literal["user", "assistant"]
        parts: list[str]
        if isinstance(message, ModelRequest):
            role = "user"
            parts = []
            for part in message.parts:
                if not isinstance(part, UserPromptPart):
                    continue
                if isinstance(part.content, str):
                    parts.append(part.content)
                else:
                    parts.extend(
                        item if isinstance(item, str) else item.content
                        for item in part.content
                        if isinstance(item, (str, TextContent))
                    )
        else:
            role = "assistant"
            parts = [
                part.content
                for part in message.parts
                if isinstance(part, TextPart)
            ]
        if parts:
            messages.append(HistoryMessage(role=role, text="\n".join(parts)))
    return tuple(messages)
