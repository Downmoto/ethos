"""Session and message record models."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from cassiopeia.gateways import GatewayOrigin
from cassiopeia.shared import EntityId, NonEmptyString, TimestampedRecord


class SessionStatus(StrEnum):
    """Lifecycle state for a session."""

    ACTIVE = "active"
    CLOSED = "closed"
    ARCHIVED = "archived"


class MessageRole(StrEnum):
    """Author role for a session message."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class MessageDirection(StrEnum):
    """Gateway direction for a message."""

    INBOUND = "inbound"
    OUTBOUND = "outbound"
    INTERNAL = "internal"


class MessageLink(BaseModel):
    """Reference to nearby workflow or subagent activity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    workflow_run_id: EntityId | None = None
    subagent_task_id: EntityId | None = None


class MessageRecord(TimestampedRecord):
    """Persisted message metadata and content."""

    id: EntityId
    session_id: EntityId
    role: MessageRole
    direction: MessageDirection
    content: NonEmptyString
    origin: GatewayOrigin | None = None
    links: tuple[MessageLink, ...] = ()


class SessionRecord(TimestampedRecord):
    """Persistent conversation/work context."""

    id: EntityId
    workspace_id: EntityId
    persona_id: EntityId
    gateway_id: EntityId
    origin: GatewayOrigin
    status: SessionStatus = SessionStatus.ACTIVE
    title: NonEmptyString | None = None
    recent_workflow_run_ids: tuple[EntityId, ...] = ()
    recent_subagent_task_ids: tuple[EntityId, ...] = ()
