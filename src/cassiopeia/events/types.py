"""Event type primitives."""

from enum import StrEnum


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
