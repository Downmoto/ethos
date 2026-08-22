"""Event type primitives."""

from enum import StrEnum


class EventType(StrEnum):
    """Canonical event type names for ethos lifecycle events."""

    APP_STARTED = "app.started"
    APP_INITIALISED = "app.initialised"

    # workspace event
    WORKSPACE_CREATE = "workspace.create"
    WORKSPACE_LIST = "workspace.list"
    WORKSPACE_SHOW = "workspace.show"

    # session events
    SESSION_CREATE = "session.create"
    SESSION_LIST = "session.list"
    SESSION_SHOW = "session.show"
    SESSION_HISTORY = "session.history"
    SESSION_ARCHIVE = "session.archive"
    SESSION_CHAT = "session.chat"

    # runtime trace events
    RUN_STARTED = "run.started"
    RUN_PAUSED = "run.paused"
    RUN_RESUMED = "run.resumed"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    MODEL_REQUEST_STARTED = "model.request.started"
    MODEL_REQUEST_COMPLETED = "model.request.completed"
    MODEL_REQUEST_FAILED = "model.request.failed"
    TOOL_CALL_REQUESTED = "tool.call.requested"
    TOOL_CALL_PREPARED = "tool.call.prepared"
    TOOL_EXECUTION_STARTED = "tool.execution.started"
    TOOL_EXECUTION_COMPLETED = "tool.execution.completed"
    TOOL_APPROVAL_REQUESTED = "tool.approval.requested"
    TOOL_APPROVAL_APPROVED = "tool.approval.approved"
    TOOL_APPROVAL_DENIED = "tool.approval.denied"
    TOOL_APPROVAL_INDETERMINATE = "tool.approval.indeterminate"
