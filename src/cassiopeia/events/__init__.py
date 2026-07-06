"""Typed cassiopeia event APIs."""

from cassiopeia.configs import get_settings
from cassiopeia.events.emitters import EnvelopeEventEmitter
from cassiopeia.events.listeners import EventListenerRegistry
from cassiopeia.events.models import EventEnvelope, EventPayload, EventSource
from cassiopeia.events.types import EventType
from cassiopeia.shared import NonEmptyString
from cassiopeia.storage import create_event_sink


def event_factory(
    event_type: EventType,
    location: str,
    details: str,
    payload: EventPayload,
    tags: tuple[NonEmptyString, ...] = (),
) -> EventEnvelope:
    source = EventSource(name=location, detail=details)
    return EventEnvelope(
        type=event_type, source=source, tags=tags, payload=payload
    )


async def _global_print_event_listener(event: EventEnvelope) -> None:
    print(
        f"event type={event.type.value} source={event.source.name} "
        f"detail={event.source.detail or '-'} tags={','.join(event.tags)}"
    )


_SETTINGS = get_settings().events

_EVENT_LISTENER_REGISTRY = EventListenerRegistry()
_EVENT_SINK = create_event_sink(flush_rate=_SETTINGS.flush_rate)

if _SETTINGS.print_events:
    _EVENT_LISTENER_REGISTRY.register(_global_print_event_listener)


EVENT_EMITTER = EnvelopeEventEmitter(
    enabled=_SETTINGS.enabled,
    sink=_EVENT_SINK,
    dispatcher=_EVENT_LISTENER_REGISTRY,
)
__all__ = ["event_factory", "EVENT_EMITTER"]
