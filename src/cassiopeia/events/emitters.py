"""Event emitter interfaces and implementations."""

from typing import Protocol

from cassiopeia.events.listeners import EventDispatcher
from cassiopeia.events.models import EventCreate, EventEnvelope
from cassiopeia.events.sinks import EventSink


class EventEmitter(Protocol):
    """Async event emitter interface for application code."""

    async def emit(self, event: EventCreate) -> EventEnvelope:
        """Validate, emit, and return the resulting event envelope."""
        ...


class EnvelopeEventEmitter:
    """Emitter that builds validated event envelopes and optionally stores them.

    This is the smallest useful emitter implementation for early integration and
    tests. Later milestone tasks can wrap or replace it with storage and
    listener delivery without changing the public `emit` call shape.
    """

    def __init__(
        self,
        sink: EventSink | None = None,
        dispatcher: EventDispatcher | None = None,
    ) -> None:
        self._sink = sink
        self._dispatcher = dispatcher

    async def emit(self, event: EventCreate) -> EventEnvelope:
        """Return a new envelope for an already validated event request."""

        envelope = EventEnvelope(
            type=event.type,
            source=event.source,
            workspace_id=event.workspace_id,
            session_id=event.session_id,
            persona_id=event.persona_id,
            gateway_id=event.gateway_id,
            correlation_id=event.correlation_id,
            causation_id=event.causation_id,
            tags=event.tags,
            payload=event.payload,
        )

        if self._sink is not None:
            await self._sink.append(envelope)

        if self._dispatcher is not None:
            await self._dispatcher.deliver(envelope)

        return envelope
