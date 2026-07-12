"""Event envelope and payload models."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from cassiopeia.events.types import EventType

type NonEmptyString = Annotated[str, Field(min_length=1)]


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


class EventEnvelope(BaseModel):
    """Common envelope for every cassiopeia lifecycle event."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Handled and not invoked manually
    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    type: EventType
    source: EventSource
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
