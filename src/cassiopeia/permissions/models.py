"""Permission and security ring models."""

from datetime import timedelta
from enum import IntEnum, StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from cassiopeia.shared import EntityId, NonEmptyString, TimestampedRecord


class SecurityRing(IntEnum):
    """Autonomy ring for permission decisions."""

    RING_0 = 0
    RING_1 = 1
    RING_2 = 2
    RING_3 = 3


class GrantDuration(StrEnum):
    """How long an approved permission grant lasts."""

    ONCE = "once"
    SESSION = "session"
    ALWAYS = "always"


class PermissionDecision(StrEnum):
    """Recorded decision for a permission request."""

    APPROVED = "approved"
    DENIED = "denied"


class ActionScope(BaseModel):
    """Specific scope a permission action applies to."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: NonEmptyString
    id: EntityId | None = None


class PermissionAction(BaseModel):
    """Permission-controlled action metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    name: NonEmptyString
    minimum_ring: SecurityRing
    scope: ActionScope


class PermissionPromptRequest(TimestampedRecord):
    """Runtime request asking a user to approve or deny an action."""

    id: EntityId
    session_id: EntityId
    persona_id: EntityId
    action: PermissionAction
    reason: NonEmptyString | None = None
    requested_duration: GrantDuration = GrantDuration.ONCE


class PermissionGrant(TimestampedRecord):
    """Stored permission grant for an action and scope."""

    id: EntityId
    action_id: EntityId
    scope: ActionScope
    decision: PermissionDecision
    duration: GrantDuration
    session_id: EntityId | None = None
    persona_id: EntityId | None = None
    expires_after_seconds: Annotated[int, Field(gt=0)] | None = None


class PermissionAuditRecord(TimestampedRecord):
    """Audit record for a permission request outcome."""

    id: EntityId
    prompt_request_id: EntityId
    action_id: EntityId
    scope: ActionScope
    ring: SecurityRing
    decision: PermissionDecision
    duration: GrantDuration
    decided_by: EntityId | None = None


def grant_ttl(duration: GrantDuration) -> timedelta | None:
    """Return the default grant lifetime when one exists."""

    if duration is GrantDuration.ONCE:
        return timedelta(seconds=0)
    return None
