"""Event catalogue definitions."""

from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from cassiopeia.events.types import EVENT_TYPE_PATTERN, EventType


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
