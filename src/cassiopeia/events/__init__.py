"""Typed cassiopeia event APIs."""

from cassiopeia.configs import get_settings
from cassiopeia.events.emitters import EnvelopeEventEmitter
from cassiopeia.events.listeners import EventListenerRegistry
from cassiopeia.events.models import EventEnvelope, EventPayload, EventSource
from cassiopeia.events.sinks import InMemoryEventSink
from cassiopeia.events.types import EventType
from cassiopeia.shared import NonEmptyString


def event_factory(
    event_type: EventType,
    location: str,
    details: str,
    payload: EventPayload,
    tags: tuple[NonEmptyString, ...] = (),
) -> EventEnvelope:
    source = EventSource(name=location, detail=details)
    return EventEnvelope(type=event_type, source=source, tags=tags, payload=payload)


# TODO: replace with actual listener, use for manual testing
async def _global_print_event_listener(event: EventEnvelope) -> None:
    print("=== FROM PRINT LISTENER ===")
    print(f"event::<{event.type}>")
    print(f"id::<{event.id}>")
    print(f"source.name::<{event.source.name}>, source.detail::<{event.source.detail}>")
    print()


_SETTINGS = get_settings().events

_EVENT_LISTENER_REGISTRY = EventListenerRegistry()
_EVENT_TEMP_INMEM_SINK = InMemoryEventSink(flush_rate=_SETTINGS.flush_rate)

_EVENT_LISTENER_REGISTRY.register(_global_print_event_listener)


EVENT_EMITTER = EnvelopeEventEmitter(
    sink=_EVENT_TEMP_INMEM_SINK, dispatcher=_EVENT_LISTENER_REGISTRY
)
__all__ = ["event_factory", "EVENT_EMITTER"]
