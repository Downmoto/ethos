"""Shared Ethos application behaviour for the CLI and Vox protocol."""

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ethos.capabilities import Capability, RunContext
from ethos.capabilities.filesystem import FilesystemCapability
from ethos.capabilities.shell import ShellCapability
from ethos.capabilities.skills import SkillsCapability
from ethos.capability_config import (
    CAPABILITIES_FILE,
    CapabilityManager,
    CapabilityName,
    parse_capability_name,
)
from ethos.config import CONFIG_FILE, EthosSettings
from ethos.events import create_event_emitter, event_factory
from ethos.events.emitters import EnvelopeEventEmitter
from ethos.events.models import EventPayload
from ethos.events.types import EventType
from ethos.home import DB_PATH, LOGS_PATH, SKILLS_PATH
from ethos.models import Message, Model, ReasoningEffort, Usage
from ethos.personas import (
    ETHOS_PERSONA_ID,
    PERSONAS_FILE,
    Persona,
    PersonaManager,
    PersonaResolution,
)
from ethos.provider import AIProvider, ProviderName
from ethos.provider_config import ProviderManager
from ethos.runtime import (
    AgentRuntime,
    ApprovalStreamEvent,
    RuntimePersona,
    RuntimeStreamEvent,
    ToolOutputStreamEvent,
)
from ethos.sandbox import SandboxProvider, resolve_sandbox_provider
from ethos.sessions import SESSIONS_DIR, Session, SessionManager
from ethos.storage import Storage
from ethos.tools import ToolEffect, ToolOutputStream
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
    assigned_persona: str = ETHOS_PERSONA_ID
    effective_persona: str = ETHOS_PERSONA_ID
    persona_fallback: bool = False

    @classmethod
    def from_workspace(
        cls,
        workspace: Workspace,
        resolution: PersonaResolution | None = None,
    ) -> "WorkspaceView":
        return cls(
            name=workspace.name,
            path=str(workspace.path),
            assigned_persona=(
                resolution.assigned_id if resolution else ETHOS_PERSONA_ID
            ),
            effective_persona=(
                resolution.effective_id if resolution else ETHOS_PERSONA_ID
            ),
            persona_fallback=resolution.fallback if resolution else False,
        )


class CapabilityView(BaseModel):
    """Configured and effective values projected through public adapters."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: CapabilityName
    scope: Literal["global", "workspace"]
    workspace: str | None = None
    configured: dict[str, object]
    effective: dict[str, object]


class ProviderView(BaseModel):
    """The active provider configuration with its credential redacted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: ProviderName
    model_name: str
    reasoning_effort: ReasoningEffort
    ollama_base_url: str | None = None
    credential_configured: bool


class PersonaView(BaseModel):
    """Configured preferences and their effective runtime values."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str
    instructions: str
    enabled: bool
    workspace: str | None = None
    model_name: str | None
    effective_model_name: str | None
    reasoning_effort: ReasoningEffort | None
    effective_reasoning_effort: ReasoningEffort | None
    capabilities: tuple[CapabilityName, ...] | None
    effective_capabilities: tuple[CapabilityName, ...]


class PersonaAssignmentView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace: str
    assigned_persona: str
    effective_persona: str
    fallback: bool


class SessionView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    workspace: str
    created_at: str
    archived_at: str | None
    archived: bool
    message_count: int = Field(ge=0)
    assigned_persona: str = ETHOS_PERSONA_ID
    effective_persona: str = ETHOS_PERSONA_ID
    persona_fallback: bool = False

    @classmethod
    def from_session(
        cls,
        session: Session,
        resolution: PersonaResolution | None = None,
    ) -> "SessionView":
        return cls(
            id=str(session.id),
            workspace=session.workspace_name,
            created_at=session.created_at.isoformat(),
            archived_at=(
                session.archived_at.isoformat() if session.archived_at else None
            ),
            archived=session.archived,
            message_count=len(session.messages),
            assigned_persona=(
                resolution.assigned_id if resolution else ETHOS_PERSONA_ID
            ),
            effective_persona=(
                resolution.effective_id if resolution else ETHOS_PERSONA_ID
            ),
            persona_fallback=resolution.fallback if resolution else False,
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


class ToolOutputChunk(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["tool_output"] = "tool_output"
    call_id: str
    tool_name: str
    stream: ToolOutputStream
    text: str = Field(min_length=1)
    workspace: str
    session_id: str


type ChatEvent = Annotated[
    ChatChunk | ApprovalChunk | ToolOutputChunk,
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
    assigned_persona: str
    effective_persona: str
    persona_fallback: bool


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


class _CapabilityEventPayload(EventPayload):
    """Capability operation metadata that deliberately excludes values."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    owner_id: str
    external_context: dict[str, str]
    capability_names: tuple[CapabilityName, ...]
    scope: Literal["global", "workspace"]
    workspace: str | None
    changed_fields: tuple[str, ...] = ()


class _ProviderEventPayload(EventPayload):
    """Provider operation metadata that deliberately excludes values."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    owner_id: str
    external_context: dict[str, str]
    provider_name: ProviderName
    model_name: str
    changed_fields: tuple[str, ...] = ()


class _PersonaEventPayload(EventPayload):
    """Persona operation metadata that deliberately excludes instructions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    owner_id: str
    external_context: dict[str, str]
    persona_ids: tuple[str, ...]
    workspace: str | None = None
    effective_persona: str | None = None
    fallback: bool = False
    changed_fields: tuple[str, ...] = ()


class Ethos:
    """One application lifetime shared by local and HTTP callers."""

    def __init__(
        self,
        home: Path,
        *,
        sandbox_provider_factory: (
            Callable[[], Awaitable[SandboxProvider]] | None
        ) = None,
    ) -> None:
        self.home = home
        self.storage = Storage(home / DB_PATH)
        self.workspaces = WorkspaceManager(home / WORKSPACES_DIR)
        self.sessions = SessionManager(self.workspaces, home / SESSIONS_DIR)
        self.capabilities = CapabilityManager(home / CAPABILITIES_FILE)
        self.personas = PersonaManager(home / PERSONAS_FILE)
        self.providers = ProviderManager(home / CONFIG_FILE)
        self.events = create_event_emitter(self.storage)
        self._sandbox_provider_factory = (
            sandbox_provider_factory
            if sandbox_provider_factory is not None
            else resolve_sandbox_provider
        )
        self._agent: AgentRuntime | None = None

    def close(self) -> None:
        self.storage.close()

    def __enter__(self) -> "Ethos":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _runtime(self) -> AgentRuntime:
        if self._agent is None:
            settings = self.providers.load()
            self._agent = AgentRuntime(
                self.sessions,
                model_factory=lambda: self._provider_model(),
                answer_model_factory=lambda: self._provider_model(
                    answer_only=True
                ),
                capability_resolver=self._resolve_capabilities,
                persona_resolver=self._resolve_runtime_persona,
                persona_identity_resolver=self._resolve_persona_identity,
                events=self.events,
                answer_now_after_seconds=(
                    settings.runtime.answer_now_after_seconds
                ),
                context_diagnostic_path=(
                    self.home / LOGS_PATH / "context.json"
                    if settings.runtime.context_diagnostics
                    else None
                ),
            )
        return self._agent

    def _resolve_persona_identity(
        self, workspace: str
    ) -> tuple[str, str, bool]:
        """Resolve event identity without loading provider configuration."""
        resolution = self.personas.resolve(workspace)
        return (
            resolution.assigned_id,
            resolution.effective_id,
            resolution.fallback,
        )

    def _resolve_runtime_persona(self, workspace: str) -> RuntimePersona:
        """Freeze persona, provider, and capability choices for one turn."""
        resolution = self.personas.resolve(workspace)
        settings = self.providers.load()
        provider = AIProvider.from_settings(settings)
        model_name = (
            resolution.effective.model_name or settings.provider.model_name
        )
        reasoning_effort = (
            resolution.effective.reasoning_effort
            or settings.provider.reasoning_effort
        )
        return RuntimePersona(
            assigned_id=resolution.assigned_id,
            effective_id=resolution.effective_id,
            fallback=resolution.fallback,
            instructions=(
                "Persona identity: "
                f"{resolution.effective.name} ({resolution.effective_id})\n"
                "Persona instructions:\n"
                f"{resolution.effective.instructions}"
            ),
            capability_ceiling=(
                tuple(item.value for item in resolution.capability_ceiling)
                if resolution.capability_ceiling is not None
                else None
            ),
            model=provider.model(model_name, reasoning_effort),
            answer_model_factory=lambda: provider.model(
                model_name, ReasoningEffort.NONE
            ),
        )

    def _provider_model(self, *, answer_only: bool = False) -> Model:
        settings = self.providers.load()
        return AIProvider.from_settings(settings).model(
            settings.provider.model_name,
            ReasoningEffort.NONE
            if answer_only
            else settings.provider.reasoning_effort,
        )

    async def show_provider(self, context: RequestContext) -> ProviderView:
        settings = self.providers.load()
        view = _provider_view(settings)
        await self._emit_provider(context, EventType.PROVIDER_SHOW, view)
        return view

    async def check_provider(
        self, changes: dict[str, object], context: RequestContext
    ) -> ProviderView:
        settings = await self.providers.check(changes)
        view = _provider_view(settings)
        await self._emit_provider(context, EventType.PROVIDER_CHECK, view)
        return view

    async def configure_provider(
        self, changes: dict[str, object], context: RequestContext
    ) -> ProviderView:
        if not changes:
            raise ValueError("provider changes must not be empty")
        settings = self.providers.configure(changes)
        view = _provider_view(settings)
        await self._emit_provider(
            context,
            EventType.PROVIDER_CONFIGURE,
            view,
            tuple(sorted(changes)),
        )
        return view

    async def create_persona(
        self,
        identifier: str,
        settings: dict[str, object],
        context: RequestContext,
    ) -> PersonaView:
        persona = self.personas.create(identifier, settings)
        view = self._persona_view(identifier, persona)
        await self._emit_personas(
            context,
            EventType.PERSONA_CREATE,
            (identifier,),
            changed_fields=tuple(sorted(settings)),
        )
        return view

    async def list_personas(
        self,
        context: RequestContext,
        workspace: str | None = None,
    ) -> tuple[PersonaView, ...]:
        if workspace is not None:
            self.workspaces.get(workspace)
        views = tuple(
            self._persona_view(identifier, persona, workspace)
            for identifier, persona in self.personas.list()
        )
        await self._emit_personas(
            context,
            EventType.PERSONA_LIST,
            tuple(view.id for view in views),
            workspace=workspace,
        )
        return views

    async def show_persona(
        self,
        identifier: str,
        context: RequestContext,
        workspace: str | None = None,
    ) -> PersonaView:
        if workspace is not None:
            self.workspaces.get(workspace)
        persona = self.personas.get(identifier)
        view = self._persona_view(identifier, persona, workspace)
        await self._emit_personas(
            context,
            EventType.PERSONA_SHOW,
            (identifier,),
            workspace=workspace,
        )
        return view

    async def configure_persona(
        self,
        identifier: str,
        changes: dict[str, object],
        context: RequestContext,
    ) -> PersonaView:
        persona = self.personas.update(identifier, changes)
        view = self._persona_view(identifier, persona)
        await self._emit_personas(
            context,
            EventType.PERSONA_CONFIGURE,
            (identifier,),
            changed_fields=tuple(sorted(changes)),
        )
        return view

    async def remove_persona(
        self,
        identifier: str,
        context: RequestContext,
    ) -> None:
        self.personas.remove(identifier)
        await self._emit_personas(
            context,
            EventType.PERSONA_REMOVE,
            (identifier,),
        )

    async def show_default_persona(
        self, context: RequestContext
    ) -> PersonaView:
        identifier, persona = self.personas.default()
        view = self._persona_view(identifier, persona)
        await self._emit_personas(
            context,
            EventType.PERSONA_DEFAULT_SHOW,
            (identifier,),
        )
        return view

    async def configure_default_persona(
        self,
        identifier: str,
        context: RequestContext,
    ) -> PersonaView:
        persona = self.personas.set_default(identifier)
        view = self._persona_view(identifier, persona)
        await self._emit_personas(
            context,
            EventType.PERSONA_DEFAULT_CONFIGURE,
            (identifier,),
        )
        return view

    async def show_workspace_persona(
        self,
        workspace: str,
        context: RequestContext,
    ) -> PersonaAssignmentView:
        self.workspaces.get(workspace)
        resolution = self.personas.resolve(workspace)
        view = _persona_assignment_view(workspace, resolution)
        await self._emit_personas(
            context,
            EventType.PERSONA_SHOW,
            (resolution.assigned_id,),
            workspace=workspace,
            resolution=resolution,
        )
        return view

    async def assign_workspace_persona(
        self,
        workspace: str,
        identifier: str,
        context: RequestContext,
    ) -> PersonaAssignmentView:
        self.workspaces.get(workspace)
        resolution = self.personas.assign(workspace, identifier)
        view = _persona_assignment_view(workspace, resolution)
        await self._emit_personas(
            context,
            EventType.PERSONA_ASSIGN,
            (identifier,),
            workspace=workspace,
            resolution=resolution,
        )
        return view

    def _persona_view(
        self,
        identifier: str,
        persona: Persona,
        workspace: str | None = None,
    ) -> PersonaView:
        try:
            provider = self.providers.load().provider
        except ValidationError:
            provider = None
        return PersonaView(
            id=identifier,
            name=persona.name,
            instructions=persona.instructions,
            enabled=persona.enabled,
            workspace=workspace,
            model_name=persona.model_name,
            effective_model_name=(
                persona.model_name
                or (provider.model_name if provider is not None else None)
            ),
            reasoning_effort=persona.reasoning_effort,
            effective_reasoning_effort=(
                persona.reasoning_effort
                or (provider.reasoning_effort if provider is not None else None)
            ),
            capabilities=persona.capabilities,
            effective_capabilities=self._effective_capability_names(
                workspace, persona.capabilities
            ),
        )

    def _effective_capability_names(
        self,
        workspace: str | None,
        ceiling: tuple[CapabilityName, ...] | None,
    ) -> tuple[CapabilityName, ...]:
        settings = self.capabilities.effective(workspace)
        enabled = {
            CapabilityName.SKILLS: settings.skills.enabled,
            CapabilityName.FILE_SYSTEM: settings.file_system.enabled,
            CapabilityName.SHELL: settings.shell.enabled,
        }
        allowed = set(ceiling) if ceiling is not None else set(CapabilityName)
        return tuple(
            name for name in CapabilityName if enabled[name] and name in allowed
        )

    def _resolve_capabilities(
        self, context: RunContext
    ) -> tuple[Capability, ...]:
        """Build enabled capabilities from fresh settings for one run."""

        settings = self.capabilities.effective(context.workspace_name)
        allowed = (
            set(context.persona_capabilities)
            if context.persona_capabilities is not None
            else {name.value for name in CapabilityName}
        )
        capabilities: list[Capability] = []
        filesystem = settings.file_system
        if filesystem.enabled and CapabilityName.FILE_SYSTEM.value in allowed:
            capabilities.append(
                FilesystemCapability(
                    max_read_file_bytes=filesystem.max_read_file_bytes,
                    max_write_file_bytes=filesystem.max_write_file_bytes,
                    max_file_entries=filesystem.max_file_entries,
                    max_search_matches=filesystem.max_search_matches,
                    max_search_result_bytes=(
                        filesystem.max_search_result_bytes
                    ),
                    max_patch_bytes=filesystem.max_patch_bytes,
                    max_patch_files=filesystem.max_patch_files,
                )
            )
        skills = settings.skills
        if skills.enabled and CapabilityName.SKILLS.value in allowed:
            capabilities.append(
                SkillsCapability(
                    self.home / SKILLS_PATH,
                    self.home.parent / ".agents" / "skills",
                    events=self.events,
                    max_skill_file_bytes=skills.max_skill_file_bytes,
                    max_skills=skills.max_skills,
                    max_resource_file_bytes=skills.max_resource_file_bytes,
                    max_resources=skills.max_resources,
                )
            )
        shell = settings.shell
        if shell.enabled and CapabilityName.SHELL.value in allowed:
            capabilities.append(
                ShellCapability(
                    self._sandbox_provider_factory,
                    max_command_bytes=shell.max_command_bytes,
                    max_command_seconds=shell.max_command_seconds,
                    max_output_bytes=shell.max_output_bytes,
                )
            )
        return tuple(capabilities)

    async def list_capabilities(
        self,
        context: RequestContext,
        workspace: str | None = None,
    ) -> tuple[CapabilityView, ...]:
        """List capabilities at global or effective workspace scope."""

        if workspace is not None:
            self.workspaces.get(workspace)
        views = tuple(
            self._capability_view(name, workspace) for name in CapabilityName
        )
        await self._emit_capabilities(
            context,
            EventType.CAPABILITY_LIST,
            tuple(item.name for item in views),
            workspace,
        )
        return views

    async def show_capability(
        self,
        capability: str,
        context: RequestContext,
        workspace: str | None = None,
    ) -> CapabilityView:
        """Show configured and effective values for one capability."""

        if workspace is not None:
            self.workspaces.get(workspace)
        name = parse_capability_name(capability)
        view = self._capability_view(name, workspace)
        await self._emit_capabilities(
            context,
            EventType.CAPABILITY_SHOW,
            (name,),
            workspace,
        )
        return view

    async def configure_capability(
        self,
        capability: str,
        changes: dict[str, object],
        context: RequestContext,
        workspace: str | None = None,
    ) -> CapabilityView:
        """Validate and persist global changes or a workspace override."""

        if not changes:
            raise ValueError("capability changes must not be empty")
        name = parse_capability_name(capability)
        if workspace is None:
            self.capabilities.configure_global(name, changes)
        else:
            self.workspaces.get(workspace)
            self.capabilities.configure_workspace(workspace, name, changes)
        view = self._capability_view(name, workspace)
        await self._emit_capabilities(
            context,
            EventType.CAPABILITY_CONFIGURE,
            (name,),
            workspace,
            tuple(sorted(changes)),
        )
        return view

    async def reset_capability_override(
        self,
        workspace: str,
        capability: str,
        context: RequestContext,
    ) -> CapabilityView:
        """Remove a workspace override so it inherits global settings."""

        self.workspaces.get(workspace)
        name = parse_capability_name(capability)
        self.capabilities.reset_workspace(workspace, name)
        view = self._capability_view(name, workspace)
        await self._emit_capabilities(
            context,
            EventType.CAPABILITY_RESET,
            (name,),
            workspace,
        )
        return view

    def _capability_view(
        self,
        name: CapabilityName,
        workspace: str | None,
    ) -> CapabilityView:
        """Project persistence models without exposing internal model types."""

        effective = self.capabilities.effective(workspace)
        settings = {
            CapabilityName.SKILLS: effective.skills,
            CapabilityName.FILE_SYSTEM: effective.file_system,
            CapabilityName.SHELL: effective.shell,
        }[name]
        return CapabilityView(
            name=name,
            scope="workspace" if workspace is not None else "global",
            workspace=workspace,
            configured=self.capabilities.configured(name, workspace),
            effective=settings.model_dump(),
        )

    async def create_workspace(
        self,
        name: str,
        context: RequestContext,
        persona: str | None = None,
    ) -> WorkspaceView:
        identifier, selected = (
            (persona, self.personas.get(persona))
            if persona is not None
            else self.personas.default()
        )
        if not selected.enabled:
            raise ValueError(f"persona is disabled: {identifier}")
        workspace = self.workspaces.create(name)
        resolution = self.personas.assign(workspace.name, identifier)
        await self._emit_workspaces(
            context, EventType.WORKSPACE_CREATE, (workspace,)
        )
        await self._emit_personas(
            context,
            EventType.PERSONA_ASSIGN,
            (resolution.assigned_id,),
            workspace=workspace.name,
            resolution=resolution,
        )
        return WorkspaceView.from_workspace(workspace, resolution)

    async def list_workspaces(
        self, context: RequestContext
    ) -> tuple[WorkspaceView, ...]:
        workspaces = self.workspaces.list()
        await self._emit_workspaces(
            context, EventType.WORKSPACE_LIST, workspaces
        )
        return tuple(
            WorkspaceView.from_workspace(item, self.personas.resolve(item.name))
            for item in workspaces
        )

    async def show_workspace(
        self, name: str, context: RequestContext
    ) -> WorkspaceView:
        workspace = self.workspaces.get(name)
        await self._emit_workspaces(
            context, EventType.WORKSPACE_SHOW, (workspace,)
        )
        return WorkspaceView.from_workspace(
            workspace, self.personas.resolve(workspace.name)
        )

    async def create_session(
        self, workspace: str, context: RequestContext
    ) -> SessionView:
        session = self.sessions.create(workspace)
        await self._emit_sessions(context, EventType.SESSION_CREATE, (session,))
        return SessionView.from_session(
            session, self.personas.resolve(session.workspace_name)
        )

    async def list_sessions(
        self, workspace: str, context: RequestContext
    ) -> tuple[SessionView, ...]:
        sessions = self.sessions.list(workspace)
        await self._emit_sessions(context, EventType.SESSION_LIST, sessions)
        resolution = self.personas.resolve(workspace)
        return tuple(
            SessionView.from_session(item, resolution) for item in sessions
        )

    async def show_session(
        self, workspace: str, session_id: str, context: RequestContext
    ) -> SessionView:
        session = self.sessions.get(workspace, session_id)
        await self._emit_sessions(context, EventType.SESSION_SHOW, (session,))
        return SessionView.from_session(
            session, self.personas.resolve(session.workspace_name)
        )

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
        return SessionView.from_session(
            session, self.personas.resolve(session.workspace_name)
        )

    async def recover_session(
        self, workspace: str, session_id: str, context: RequestContext
    ) -> SessionView:
        session = await self._runtime().recover(
            workspace,
            session_id,
            event_location=context.source,
        )
        await self._emit_sessions(
            context, EventType.SESSION_RECOVER, (session,)
        )
        return SessionView.from_session(
            session, self.personas.resolve(session.workspace_name)
        )

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
        """Translate runtime events and always record the resulting session.

        A paused, abandoned, or failed stream has no final ``done`` event, but
        may still have durable checkpoints. The fallback emission preserves
        the service-level contract that ``SESSION_CHAT`` reflects that state.
        """

        emitted = False
        async for event in events:
            if isinstance(event, ToolOutputStreamEvent):
                yield ToolOutputChunk(
                    call_id=event.call_id,
                    tool_name=event.tool_name,
                    stream=event.stream,
                    text=event.text,
                    workspace=workspace,
                    session_id=session_id,
                )
                continue
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
                    _session_event_item(
                        item,
                        self.personas.resolve(item.workspace_name),
                    )
                    for item in sessions
                ),
            ),
        )

    async def _emit_personas(
        self,
        context: RequestContext,
        event_type: EventType,
        identifiers: tuple[str, ...],
        *,
        workspace: str | None = None,
        resolution: PersonaResolution | None = None,
        changed_fields: tuple[str, ...] = (),
    ) -> None:
        await _emit(
            self.events,
            event_type,
            context,
            _PersonaEventPayload(
                schema_name="persona.operation",
                owner_id=context.owner_id,
                external_context=context.external_context,
                persona_ids=identifiers,
                workspace=workspace,
                effective_persona=(
                    resolution.effective_id if resolution else None
                ),
                fallback=resolution.fallback if resolution else False,
                changed_fields=changed_fields,
            ),
        )

    async def _emit_capabilities(
        self,
        context: RequestContext,
        event_type: EventType,
        names: tuple[CapabilityName, ...],
        workspace: str | None,
        changed_fields: tuple[str, ...] = (),
    ) -> None:
        """Emit operation identity and changed field names, never values."""

        await _emit(
            self.events,
            event_type,
            context,
            _CapabilityEventPayload(
                schema_name="capability.operation",
                owner_id=context.owner_id,
                external_context=context.external_context,
                capability_names=names,
                scope="workspace" if workspace is not None else "global",
                workspace=workspace,
                changed_fields=changed_fields,
            ),
        )

    async def _emit_provider(
        self,
        context: RequestContext,
        event_type: EventType,
        view: ProviderView,
        changed_fields: tuple[str, ...] = (),
    ) -> None:
        await _emit(
            self.events,
            event_type,
            context,
            _ProviderEventPayload(
                schema_name="provider.operation",
                owner_id=context.owner_id,
                external_context=context.external_context,
                provider_name=view.name,
                model_name=view.model_name,
                changed_fields=changed_fields,
            ),
        )


async def _emit(
    emitter: EnvelopeEventEmitter,
    event_type: EventType,
    context: RequestContext,
    payload: EventPayload,
) -> None:
    await emitter.emit(
        event_factory(
            event_type,
            location=context.source,
            payload=payload,
        )
    )


def _provider_view(settings: EthosSettings) -> ProviderView:
    """Project validated settings without serialising any secret value."""

    provider = settings.provider
    key = {
        ProviderName.OPENAI: settings.keys.openai_api_key,
        ProviderName.GOOGLE: settings.keys.google_api_key,
        ProviderName.OLLAMA: settings.keys.ollama_api_key,
    }[provider.name]
    return ProviderView(
        name=provider.name,
        model_name=provider.model_name,
        reasoning_effort=provider.reasoning_effort,
        ollama_base_url=(
            provider.ollama_base_url
            if provider.name is ProviderName.OLLAMA
            else None
        ),
        credential_configured=key is not None,
    )


def _persona_assignment_view(
    workspace: str,
    resolution: PersonaResolution,
) -> PersonaAssignmentView:
    return PersonaAssignmentView(
        workspace=workspace,
        assigned_persona=resolution.assigned_id,
        effective_persona=resolution.effective_id,
        fallback=resolution.fallback,
    )


def _session_event_item(
    session: Session,
    resolution: PersonaResolution,
) -> _SessionEventItem:
    return _SessionEventItem(
        id=str(session.id),
        workspace=session.workspace_name,
        archived=session.archived,
        assigned_persona=resolution.assigned_id,
        effective_persona=resolution.effective_id,
        persona_fallback=resolution.fallback,
    )
