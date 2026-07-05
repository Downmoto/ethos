"""Event emitter interfaces and implementations."""

from cassiopeia.events.listeners import EventListenerRegistry
from cassiopeia.events.models import EventEnvelope
from cassiopeia.events.sinks import EventSink


class EnvelopeEventEmitter:
    """Emitter that builds validated event envelopes and optionally stores them.

    This is the smallest useful emitter implementation for early integration and
    tests. Later milestone tasks can wrap or replace it with storage and
    listener delivery without changing the public `emit` call shape.
    """

    def __init__(
        self,
        enabled: bool,
        sink: EventSink | None = None,
        dispatcher: EventListenerRegistry | None = None,
    ) -> None:
        self.enabled = enabled
        self._sink = sink
        self._dispatcher = dispatcher

    async def emit(self, event: EventEnvelope) -> EventEnvelope | None:
        """Return a new envelope for an already validated event request."""

        if not self.enabled:
            return None
        if self._sink is not None:
            await self._sink.append(event)

        if self._dispatcher is not None:
            await self._dispatcher.deliver(event)

        return event
