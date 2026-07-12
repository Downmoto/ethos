"""Typed cassiopeia event APIs."""

from pathlib import Path

from cassiopeia.config import EventsConfig
from cassiopeia.events.emitters import EnvelopeEventEmitter
from cassiopeia.events.listeners import EventListenerRegistry
from cassiopeia.events.models import (
    EventEnvelope,
    EventPayload,
    EventSource,
    NonEmptyString,
)
from cassiopeia.events.types import EventType
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


def create_event_emitter(
    db_path: Path, config: EventsConfig
) -> EnvelopeEventEmitter:
    """Create an event emitter without performing work during import."""
    listeners = EventListenerRegistry()
    if config.print_events:
        listeners.register(_global_print_event_listener)

    return EnvelopeEventEmitter(
        enabled=config.enabled,
        sink=create_event_sink(db_path),
        dispatcher=listeners,
    )


__all__ = ["create_event_emitter", "event_factory"]
