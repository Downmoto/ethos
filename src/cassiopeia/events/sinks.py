"""Event storage sink contracts."""

from typing import Protocol

from cassiopeia.events.models import EventEnvelope


class EventSink(Protocol):
    """Storage boundary for appending emitted events."""

    async def append(self, event: EventEnvelope) -> None:
        """Append an emitted event to the storage boundary."""
        ...


class InMemoryEventSink:
    """In-memory event sink for tests and early internal integration."""

    def __init__(self) -> None:
        self._events: list[EventEnvelope] = []

    @property
    def events(self) -> tuple[EventEnvelope, ...]:
        """Events appended to this sink in append order."""

        return tuple(self._events)

    async def append(self, event: EventEnvelope) -> None:
        """Append an emitted event to memory."""

        self._events.append(event)
