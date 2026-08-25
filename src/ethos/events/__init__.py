"""Typed ethos event APIs."""

from ethos.events.emitters import EnvelopeEventEmitter
from ethos.events.models import (
    EventEnvelope,
    EventPayload,
    EventSource,
)
from ethos.events.types import EventType
from ethos.storage import Storage


def event_factory(
    event_type: EventType,
    location: str,
    payload: EventPayload,
) -> EventEnvelope:
    return EventEnvelope(
        type=event_type,
        source=EventSource(name=location),
        payload=payload,
    )


def create_event_emitter(storage: Storage) -> EnvelopeEventEmitter:
    """Create the always-on application event emitter."""
    return EnvelopeEventEmitter(storage=storage)


__all__ = ["create_event_emitter", "event_factory"]
