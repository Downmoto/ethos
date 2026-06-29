"""Typed cassiopeia event names and catalogue definitions."""

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Final, Literal, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

EVENT_TYPE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)+$")
NonEmptyString = Annotated[str, Field(min_length=1)]


class EventType(StrEnum):
    """Canonical event type names for cassiopeia lifecycle events."""

    SESSION_CREATED = "session.created"
    SESSION_CLOSED = "session.closed"
    MESSAGE_RECEIVED = "message.received"
    MESSAGE_SENT = "message.sent"
    MEMORY_CREATED = "memory.created"
    MEMORY_UPDATED = "memory.updated"
    MEMORY_DELETED = "memory.deleted"
    MEMORY_REJECTED = "memory.rejected"
    PERMISSION_REQUESTED = "permission.requested"
    PERMISSION_GRANTED = "permission.granted"
    PERMISSION_DENIED = "permission.denied"
    WORKFLOW_STARTED = "workflow.started"
    WORKFLOW_COMPLETED = "workflow.completed"
    WORKFLOW_FAILED = "workflow.failed"
    SUBAGENT_CREATED = "subagent.created"
    SUBAGENT_COMPLETED = "subagent.completed"
    SUBAGENT_FAILED = "subagent.failed"
    GATEWAY_CONNECTED = "gateway.connected"
    GATEWAY_DISCONNECTED = "gateway.disconnected"
    GATEWAY_ERROR = "gateway.error"
    WORKSPACE_CREATED = "workspace.created"
    WORKSPACE_UPDATED = "workspace.updated"


class EventTypeDefinition(BaseModel):
    """Pydantic-validated event catalogue entry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: EventType
    description: str = Field(min_length=1)

    @field_validator("type")
    @classmethod
    def type_uses_dot_format(cls, value: EventType) -> EventType:
        if not EVENT_TYPE_PATTERN.fullmatch(value.value):
            raise ValueError(f"event type must use dot format: {value.value}")
        return value


class EventSource(BaseModel):
    """Source that emitted an event."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: NonEmptyString
    detail: NonEmptyString | None = None


class EventPayload(BaseModel):
    """Flexible event payload container for evolving event schemas.

    Stored events should always be readable through this generic payload model.
    More specific payload classes are for write-time and feature-level
    validation, not for deciding whether an old event record can be loaded.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    schema_name: NonEmptyString | None = None
    schema_version: Annotated[int, Field(ge=1)] = 1
    data: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_stored(cls, value: Any) -> "EventPayload":
        """Load stored payload data without requiring a known schema class."""

        return cls.model_validate(value)


class SessionEventPayload(EventPayload):
    """Payload for session lifecycle events."""

    session_id: NonEmptyString | None = None
    workspace_id: NonEmptyString | None = None
    persona_id: NonEmptyString | None = None
    gateway_id: NonEmptyString | None = None
    reason: NonEmptyString | None = None


class MessageEventPayload(EventPayload):
    """Payload for message lifecycle events."""

    message_id: NonEmptyString | None = None
    direction: Literal["received", "sent"]
    text: str | None = None
    attachments: tuple[dict[str, Any], ...] = ()


class MemoryEventPayload(EventPayload):
    """Payload for memory lifecycle events."""

    memory_id: NonEmptyString | None = None
    scope: Literal["session", "workspace", "persona", "user"]
    action: Literal["created", "updated", "deleted", "rejected"]
    tags: tuple[NonEmptyString, ...] = ()


class PermissionEventPayload(EventPayload):
    """Payload for permission lifecycle events."""

    request_id: NonEmptyString | None = None
    action: NonEmptyString
    ring: Annotated[int, Field(ge=0, le=3)]
    decision: Literal["requested", "granted", "denied"]


class WorkflowEventPayload(EventPayload):
    """Payload for workflow lifecycle events."""

    workflow_id: NonEmptyString | None = None
    run_id: NonEmptyString | None = None
    status: Literal["started", "completed", "failed"]
    error: NonEmptyString | None = None


class SubagentEventPayload(EventPayload):
    """Payload for subagent lifecycle events."""

    subagent_id: NonEmptyString | None = None
    task_id: NonEmptyString | None = None
    persona_id: NonEmptyString | None = None
    status: Literal["created", "completed", "failed"]
    error: NonEmptyString | None = None


class GatewayEventPayload(EventPayload):
    """Payload for gateway lifecycle events."""

    gateway_id: NonEmptyString | None = None
    gateway_type: NonEmptyString | None = None
    status: Literal["connected", "disconnected", "error"]
    error: NonEmptyString | None = None


class WorkspaceEventPayload(EventPayload):
    """Payload for workspace lifecycle events."""

    workspace_id: NonEmptyString | None = None
    slug: NonEmptyString | None = None
    root_path: NonEmptyString | None = None
    action: Literal["created", "updated"]


class EventEnvelope(BaseModel):
    """Common envelope for every cassiopeia lifecycle event.

    The envelope is the stable part of an event record. It identifies what
    happened, when it happened, where it came from, and which workspace/session
    context it belongs to. Event-specific details belong in `payload`, whose
    schema can evolve independently from this wrapper.

    `correlation_id` groups events produced by the same larger operation, such
    as one inbound message turn. `causation_id` points at the direct parent event
    that caused this event, which lets later storage and debugging code rebuild
    event chains without making every payload understand orchestration details.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    type: EventType
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: EventSource
    workspace_id: NonEmptyString | None = None
    session_id: NonEmptyString | None = None
    persona_id: NonEmptyString | None = None
    gateway_id: NonEmptyString | None = None
    correlation_id: UUID | None = None
    causation_id: UUID | None = None
    tags: tuple[NonEmptyString, ...] = ()
    payload: EventPayload = Field(default_factory=EventPayload)

    @field_validator("created_at")
    @classmethod
    def created_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("event timestamp must be timezone-aware")
        return value


class EventCreate(BaseModel):
    """Validated request to emit a new cassiopeia event.

    Runtime code should construct this request and pass it to an `EventEmitter`.
    The emitter owns envelope creation, including event id and timestamp
    generation, so callers cannot accidentally pre-write persisted identity
    fields.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: EventType
    source: EventSource
    workspace_id: NonEmptyString | None = None
    session_id: NonEmptyString | None = None
    persona_id: NonEmptyString | None = None
    gateway_id: NonEmptyString | None = None
    correlation_id: UUID | None = None
    causation_id: UUID | None = None
    tags: tuple[NonEmptyString, ...] = ()
    payload: EventPayload = Field(default_factory=EventPayload)


class EventEmitter(Protocol):
    """Async event emitter interface for application code."""

    async def emit(self, event: EventCreate) -> EventEnvelope:
        """Validate, emit, and return the resulting event envelope."""
        ...


class EnvelopeEventEmitter:
    """Emitter that only builds validated event envelopes.

    This is the smallest useful emitter implementation for early integration and
    tests. Later milestone tasks can wrap or replace it with storage and
    listener delivery without changing the public `emit` call shape.
    """

    async def emit(self, event: EventCreate) -> EventEnvelope:
        """Return a new envelope for an already validated event request."""

        return EventEnvelope(
            type=event.type,
            source=event.source,
            workspace_id=event.workspace_id,
            session_id=event.session_id,
            persona_id=event.persona_id,
            gateway_id=event.gateway_id,
            correlation_id=event.correlation_id,
            causation_id=event.causation_id,
            tags=event.tags,
            payload=event.payload,
        )


def _definition(event_type: EventType, description: str) -> EventTypeDefinition:
    return EventTypeDefinition(type=event_type, description=description)


EVENT_TYPE_CATALOGUE: Final[tuple[EventTypeDefinition, ...]] = (
    _definition(EventType.SESSION_CREATED, "A session was created."),
    _definition(EventType.SESSION_CLOSED, "A session was closed."),
    _definition(EventType.MESSAGE_RECEIVED, "A message was received from a user or gateway."),
    _definition(EventType.MESSAGE_SENT, "A message was sent by cassiopeia."),
    _definition(EventType.MEMORY_CREATED, "A memory record was created."),
    _definition(EventType.MEMORY_UPDATED, "A memory record was updated."),
    _definition(EventType.MEMORY_DELETED, "A memory record was deleted."),
    _definition(EventType.MEMORY_REJECTED, "A memory record was rejected."),
    _definition(EventType.PERMISSION_REQUESTED, "A permission prompt was requested."),
    _definition(EventType.PERMISSION_GRANTED, "A permission request was granted."),
    _definition(EventType.PERMISSION_DENIED, "A permission request was denied."),
    _definition(EventType.WORKFLOW_STARTED, "A workflow run was started."),
    _definition(EventType.WORKFLOW_COMPLETED, "A workflow run completed successfully."),
    _definition(EventType.WORKFLOW_FAILED, "A workflow run failed."),
    _definition(EventType.SUBAGENT_CREATED, "A task-scoped subagent was created."),
    _definition(EventType.SUBAGENT_COMPLETED, "A task-scoped subagent completed successfully."),
    _definition(EventType.SUBAGENT_FAILED, "A task-scoped subagent failed."),
    _definition(EventType.GATEWAY_CONNECTED, "A gateway connected."),
    _definition(EventType.GATEWAY_DISCONNECTED, "A gateway disconnected."),
    _definition(EventType.GATEWAY_ERROR, "A gateway reported an error."),
    _definition(EventType.WORKSPACE_CREATED, "A workspace was created."),
    _definition(EventType.WORKSPACE_UPDATED, "A workspace was updated."),
)

_catalogue_types = frozenset(definition.type for definition in EVENT_TYPE_CATALOGUE)

if _catalogue_types != set(EventType):
    missing = set(EventType) - _catalogue_types
    extra = _catalogue_types - set(EventType)
    raise ValueError(f"event catalogue mismatch; missing={missing!r}; extra={extra!r}")

if len(EVENT_TYPE_CATALOGUE) != len(_catalogue_types):
    raise ValueError("event catalogue contains duplicate event types")
