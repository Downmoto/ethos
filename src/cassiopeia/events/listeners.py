"""In-process event listener registry."""

from typing import Protocol

from cassiopeia.events.models import EventEnvelope


class EventListener(Protocol):
    """Async callable that receives emitted event envelopes."""

    async def __call__(self, event: EventEnvelope) -> None:
        """Handle an emitted event envelope."""
        ...


class EventDispatcher(Protocol):
    """Boundary for delivering emitted events to in-process listeners."""

    async def deliver(self, event: EventEnvelope) -> None:
        """Deliver an event to registered listeners."""
        ...


class InProcessEventListenerRegistry:
    """Minimal in-process listener registry with deterministic delivery order."""

    def __init__(self) -> None:
        self._listeners: list[EventListener] = []

    @property
    def listeners(self) -> tuple[EventListener, ...]:
        """Registered listeners in delivery order."""

        return tuple(self._listeners)

    def register(self, listener: EventListener) -> None:
        """Register a listener after previously registered listeners."""

        self._listeners.append(listener)

    async def deliver(self, event: EventEnvelope) -> None:
        """Deliver an event to listeners in registration order."""

        for listener in self._listeners:
            await listener(event)
