"""Event listener registry for async in-process callbacks."""

from collections.abc import Awaitable, Callable

from cassiopeia.events.models import EventEnvelope
from cassiopeia.events.types import EventType

type EventListener = Callable[[EventEnvelope], Awaitable[None]]


class EventListenerRegistry:
    """Minimal in-process listener registry with deterministic delivery order.

    Listener failures do not prevent later listeners from receiving the event.
    Any failures are collected and raised together after delivery completes.
    """

    def __init__(self) -> None:
        self._listeners: dict[EventType, list[EventListener]] = {
            event_type: [] for event_type in EventType
        }

    def register(self, listener: EventListener, event_type: list[EventType] | None = None) -> None:
        """Register a listener after previously registered listeners."""

        if event_type is not None:
            for _type in event_type:
                self._listeners[_type].append(listener)
            return

        for listeners in self._listeners.values():
            listeners.append(listener)

    async def deliver(self, event: EventEnvelope) -> None:
        """Deliver an event to listeners in registration order."""

        failures: list[Exception] = []

        for listener in self._listeners[event.type]:
            try:
                await listener(event)
            except Exception as error:
                failures.append(error)

        if failures:
            raise ExceptionGroup(
                f"{len(failures)} event listener(s) failed while handling {event.type.value}",
                failures,
            )
