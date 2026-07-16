"""Event emitter interfaces and implementations."""

from ethos.events.listeners import EventListenerRegistry
from ethos.events.models import EventEnvelope
from ethos.storage import Storage


class EnvelopeEventEmitter:
    """Store and dispatch validated event envelopes when enabled."""

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
        """Return a new envelope for an already validated event request."""

        if not self.enabled:
            return None

        if self._storage is not None:
            self._storage.write_event(event)

        if self._dispatcher is not None:
            await self._dispatcher.deliver(event)

        return event
