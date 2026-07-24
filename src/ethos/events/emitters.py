"""Durable-first event emission followed by in-process delivery.

See ``docs/development/commands-events-and-gateways.md`` for payload evolution
and the non-transactional relationship between commands and events.
"""

from ethos.events.listeners import EventListenerRegistry
from ethos.events.models import EventEnvelope
from ethos.storage import Storage


class EnvelopeEventEmitter:
    """Store and dispatch validated event envelopes when enabled.

    Storage commits before listener delivery. Listener failure is therefore
    observable after an event is durable and does not roll back the domain
    operation that caused it.
    """

    def __init__(
        self,
        enabled: bool,
        storage: Storage | None = None,
        dispatcher: EventListenerRegistry | None = None,
    ) -> None:
        self.enabled = enabled
        self._storage = storage
        self._dispatcher = dispatcher

    async def emit(self, event: EventEnvelope) -> EventEnvelope | None:
        """Persist, then deliver an envelope, or do neither when disabled."""

        if not self.enabled:
            return None

        # Listeners may query or react to the event, so it must be durable
        # before any listener observes it.
        if self._storage is not None:
            self._storage.write_event(event)

        if self._dispatcher is not None:
            await self._dispatcher.deliver(event)

        return event
