"""Event storage sink contracts."""

from typing import Protocol

from cassiopeia.events.models import EventEnvelope


class EventSink(Protocol):
    """Storage boundary for appending emitted events."""

    async def append(self, event: EventEnvelope) -> None:
        """Append an emitted event to the storage boundary."""
        ...

    async def flush(self) -> None:
        """flush sink out"""


# TODO: Remove this once the storage module provides a real event sink.
class InMemoryEventSink:
    """In-memory event sink for tests and early internal integration."""

    def __init__(self, flush_rate: int) -> None:
        self.flush_rate = flush_rate
        self._events: list[EventEnvelope] = []

    @property
    def events(self) -> tuple[EventEnvelope, ...]:
        """Events appended to this sink in append order."""

        return tuple(self._events)

    async def append(self, event: EventEnvelope) -> None:
        """Append an emitted event to memory."""

        self._events.append(event)

        if len(self._events) == self.flush_rate:
            await self.flush()

    async def flush(self) -> None:
        self._events.clear()
