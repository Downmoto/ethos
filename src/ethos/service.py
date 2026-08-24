"""Shared Ethos application behaviour for the CLI and Vox protocol."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from ethos.capabilities.filesystem import ReadOnlyFilesystemCapability
from ethos.capabilities.skills import SkillsCapability
from ethos.config import get_settings
from ethos.events import create_event_emitter, event_factory
from ethos.events.emitters import EnvelopeEventEmitter
from ethos.events.models import EventPayload
from ethos.events.types import EventType
from ethos.home import DB_PATH, SKILLS_PATH
from ethos.models import Message, Usage
from ethos.runtime import (
    AgentRuntime,
    ApprovalStreamEvent,
    RuntimeStreamEvent,
)
from ethos.sessions import SESSIONS_DIR, Session, SessionManager
from ethos.storage import Storage
from ethos.tools import ToolEffect
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


class ChatChunk(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["chunk"] = "chunk"
    text: str = ""
    text_kind: Literal["answer", "reasoning"] = "answer"
    workspace: str
    session_id: str
    usage: Usage | None = None
    done: bool = False


class ApprovalChunk(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["approval"] = "approval"
    approval_id: str
    call_id: str
    tool_name: str
    arguments: dict[str, object]
    effect: ToolEffect
    reason: str
    created_at: datetime
    workspace: str
    session_id: str


type ChatEvent = Annotated[
    ChatChunk | ApprovalChunk,
    Field(discriminator="kind"),
]


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
            skills_config = get_settings().capabilities.skills
            self._agent = AgentRuntime(
                self.sessions,
                capabilities=(
                    ReadOnlyFilesystemCapability(),
                    SkillsCapability(
                        self.home / SKILLS_PATH,
                        self.home.parent / ".agents" / "skills",
                        events=self.events,
                        max_resource_file_bytes=(
                            skills_config.max_resource_file_bytes
                        ),
                        max_resources=skills_config.max_resources,
                    ),
                ),
                events=self.events,
            )
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
    ) -> tuple[Message, ...]:
        session = self.sessions.get(workspace, session_id)
        await self._emit_sessions(
            context, EventType.SESSION_HISTORY, (session,)
        )
        return session.messages

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
    ) -> AsyncIterator[ChatEvent]:
        if not prompt:
            raise ValueError("prompt must not be empty")
        async for event in self._chat_events(
            self._runtime().run(
                prompt,
                workspace,
                session_id,
                event_location=context.source,
            ),
            workspace,
            session_id,
            context,
        ):
            yield event

    async def resolve_approval(
        self,
        workspace: str,
        session_id: str,
        approval_id: str,
        approved: bool,
        context: RequestContext,
    ) -> AsyncIterator[ChatEvent]:
        async for event in self._chat_events(
            self._runtime().resolve_approval(
                workspace,
                session_id,
                approval_id,
                approved=approved,
                event_location=context.source,
            ),
            workspace,
            session_id,
            context,
        ):
            yield event

    async def _chat_events(
        self,
        events: AsyncIterator[RuntimeStreamEvent],
        workspace: str,
        session_id: str,
        context: RequestContext,
    ) -> AsyncIterator[ChatEvent]:
        emitted = False
        async for event in events:
            if isinstance(event, ApprovalStreamEvent):
                approval = event.approval
                yield ApprovalChunk(
                    approval_id=approval.id,
                    call_id=approval.call.call_id,
                    tool_name=approval.tool_name,
                    arguments=approval.arguments,
                    effect=approval.effect,
                    reason=approval.reason,
                    created_at=approval.created_at,
                    workspace=workspace,
                    session_id=session_id,
                )
                continue
            if event.done:
                session = self.sessions.get(workspace, session_id)
                await self._emit_sessions(
                    context, EventType.SESSION_CHAT, (session,)
                )
                emitted = True
            yield ChatChunk(
                text=event.text,
                text_kind=event.text_kind,
                workspace=workspace,
                session_id=session_id,
                usage=event.usage,
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
