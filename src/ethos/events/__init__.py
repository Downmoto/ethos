"""Typed ethos event APIs."""

from ethos.config import EventsConfig
from ethos.events.emitters import EnvelopeEventEmitter
from ethos.events.listeners import EventListenerRegistry
from ethos.events.models import (
    EventEnvelope,
    EventPayload,
    EventSource,
    NonEmptyString,
)
from ethos.events.types import EventType
from ethos.storage import Storage


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


def create_event_emitter(
    storage: Storage, config: EventsConfig
) -> EnvelopeEventEmitter:
    """Create an event emitter without performing work during import."""
    listeners = EventListenerRegistry()
    if config.print_events:
        listeners.register(_global_print_event_listener)

    return EnvelopeEventEmitter(
        enabled=config.enabled,
        storage=storage,
        dispatcher=listeners,
    )


__all__ = ["create_event_emitter", "event_factory"]
