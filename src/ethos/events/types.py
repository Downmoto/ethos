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
