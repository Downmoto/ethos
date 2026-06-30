"""Event envelope and payload models."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from cassiopeia.events.types import EventType
from cassiopeia.shared import NonEmptyString


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


class _TypedEventPayload(EventPayload):
    """Base for write-time payload models with known fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class SessionEventPayload(_TypedEventPayload):
    """Payload for session lifecycle events."""

    reason: NonEmptyString | None = None


class MessageEventPayload(_TypedEventPayload):
    """Payload for message lifecycle events."""

    message_id: NonEmptyString | None = None
    text: str | None = None
    attachments: tuple[dict[str, object], ...] = ()


class MemoryEventPayload(_TypedEventPayload):
    """Payload for memory lifecycle events."""

    memory_id: NonEmptyString | None = None
    scope: Literal["session", "workspace", "persona", "user"]


class PermissionEventPayload(_TypedEventPayload):
    """Payload for permission lifecycle events."""

    request_id: NonEmptyString | None = None
    action: NonEmptyString
    ring: Annotated[int, Field(ge=0, le=3)]


class WorkflowEventPayload(_TypedEventPayload):
    """Payload for workflow lifecycle events."""

    workflow_id: NonEmptyString | None = None
    run_id: NonEmptyString | None = None
    error: NonEmptyString | None = None


class SubagentEventPayload(_TypedEventPayload):
    """Payload for subagent lifecycle events."""

    subagent_id: NonEmptyString | None = None
    task_id: NonEmptyString | None = None
    error: NonEmptyString | None = None


class GatewayEventPayload(_TypedEventPayload):
    """Payload for gateway lifecycle events."""

    gateway_type: NonEmptyString | None = None
    error: NonEmptyString | None = None


class WorkspaceEventPayload(_TypedEventPayload):
    """Payload for workspace lifecycle events."""

    slug: NonEmptyString | None = None
    root_path: NonEmptyString | None = None


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
    tags: tuple[NonEmptyString, ...] = Field(
        default=(),
        description="Optional labels for later hook filtering and debugging.",
    )
    payload: EventPayload = Field(default_factory=EventPayload)

    @field_validator("created_at")
    @classmethod
    def created_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("event timestamp must be timezone-aware")
        return value


class EventCreate(BaseModel):
    """Validated request to emit a new cassiopeia event.

    Runtime code should construct this request and pass it to an emitter. The
    emitter owns envelope creation, including event id and timestamp generation,
    so callers cannot accidentally pre-write persisted identity fields.
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
    tags: tuple[NonEmptyString, ...] = Field(
        default=(),
        description="Optional labels for later hook filtering and debugging.",
    )
    payload: EventPayload = Field(default_factory=EventPayload)
