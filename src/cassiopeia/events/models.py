"""Event envelope and payload models."""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from cassiopeia.events.types import EventType, NonEmptyString


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
