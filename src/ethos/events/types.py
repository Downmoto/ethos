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

    # capability events
    CAPABILITY_LIST = "capability.list"
    CAPABILITY_SHOW = "capability.show"
    CAPABILITY_CONFIGURE = "capability.configure"
    CAPABILITY_RESET = "capability.reset"

    # provider events
    PROVIDER_SHOW = "provider.show"
    PROVIDER_CHECK = "provider.check"
    PROVIDER_CONFIGURE = "provider.configure"

    # persona events
    PERSONA_CREATE = "persona.create"
    PERSONA_LIST = "persona.list"
    PERSONA_SHOW = "persona.show"
    PERSONA_CONFIGURE = "persona.configure"
    PERSONA_REMOVE = "persona.remove"
    PERSONA_DEFAULT_SHOW = "persona.default.show"
    PERSONA_DEFAULT_CONFIGURE = "persona.default.configure"
    PERSONA_ASSIGN = "persona.assign"

    # session events
    SESSION_CREATE = "session.create"
    SESSION_LIST = "session.list"
    SESSION_SHOW = "session.show"
    SESSION_HISTORY = "session.history"
    SESSION_ARCHIVE = "session.archive"
    SESSION_RECOVER = "session.recover"
    SESSION_CHAT = "session.chat"

    # skill events
    SKILL_DIAGNOSTIC = "skill.diagnostic"

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
