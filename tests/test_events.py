import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import turso
from pydantic import ValidationError

from cassiopeia.events import EVENT_EMITTER, event_factory
from cassiopeia.events.emitters import EnvelopeEventEmitter
from cassiopeia.events.listeners import EventListenerRegistry
from cassiopeia.events.models import EventEnvelope, EventPayload, EventSource
from cassiopeia.events.sinks import InMemoryEventSink
from cassiopeia.events.types import EventType
from cassiopeia.storage import TursoEventSink


def test_event_factory_returns_correct_EventEnvelope() -> None:
    envelope = event_factory(
        event_type=EventType.APP_STARTED,
        location=__name__,
        details="test_event_factory",
        payload=EventPayload(),
        tags=("test",),
    )

    assert envelope.type == EventType.APP_STARTED
    assert envelope.source.name == __name__
    assert envelope.source.detail == "test_event_factory"
    assert envelope.payload == EventPayload()
    assert envelope.tags[0] == "test"


def test_event_envelope_rejects_naive_created_at() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        EventEnvelope(
            type=EventType.APP_STARTED,
            source=EventSource(name="test"),
            created_at=datetime.now(),
        )


def test_event_envelope_accepts_timezone_aware_created_at() -> None:
    created_at = datetime.now(UTC)

    event = EventEnvelope(
        type=EventType.APP_STARTED,
        source=EventSource(name="test"),
        created_at=created_at,
    )

    assert event.created_at == created_at


def test_event_source_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        EventSource.model_validate(
            {"name": "test", "extra_field": "not allowed"}
        )


def test_event_payload_allows_extra_fields() -> None:
    payload = EventPayload.model_validate(
        {"schema_name": "test.payload", "value": 42}
    )

    assert payload.model_extra == {"value": 42}


def test_event_payload_rejects_invalid_schema_version() -> None:
    with pytest.raises(ValidationError):
        EventPayload(schema_version=0)


def test_emitter_appends_event_to_sink() -> None:
    sink = InMemoryEventSink(flush_rate=5)
    emitter = EnvelopeEventEmitter(enabled=True, sink=sink)
    event = EventEnvelope(
        type=EventType.APP_STARTED,
        source=EventSource(name="test"),
    )

    emitted = asyncio.run(emitter.emit(event))

    assert emitted == event
    assert emitted is event
    assert sink.events == (emitted,)


def test_global_emitter_uses_storage_sink() -> None:
    assert isinstance(EVENT_EMITTER._sink, TursoEventSink)


def test_emitter_works_without_sink_or_dispatcher() -> None:
    emitter = EnvelopeEventEmitter(enabled=True)
    event = EventEnvelope(
        type=EventType.APP_STARTED,
        source=EventSource(name="test"),
    )

    emitted = asyncio.run(emitter.emit(event))

    assert emitted == event
    assert emitted is event


def test_emitter_dispatches_event_to_listener() -> None:
    registry = EventListenerRegistry()
    emitter = EnvelopeEventEmitter(enabled=True, dispatcher=registry)
    delivered: list[EventEnvelope] = []
    event = EventEnvelope(
        type=EventType.APP_STARTED,
        source=EventSource(name="test"),
    )

    async def listener(event: EventEnvelope) -> None:
        delivered.append(event)

    registry.register(listener)

    emitted = asyncio.run(emitter.emit(event))

    assert delivered == [emitted]


def test_emitter_appends_before_dispatching() -> None:
    sink = InMemoryEventSink(flush_rate=5)
    registry = EventListenerRegistry()
    emitter = EnvelopeEventEmitter(enabled=True, sink=sink, dispatcher=registry)
    sink_events_seen_by_listener: list[tuple[EventEnvelope, ...]] = []
    event = EventEnvelope(
        type=EventType.APP_STARTED,
        source=EventSource(name="test"),
    )

    async def listener(_event: EventEnvelope) -> None:
        sink_events_seen_by_listener.append(sink.events)

    registry.register(listener)

    emitted = asyncio.run(emitter.emit(event))

    assert sink_events_seen_by_listener == [(emitted,)]


def test_in_memory_event_sink_preserves_append_order() -> None:
    sink = InMemoryEventSink(flush_rate=3)
    first = EventEnvelope(
        type=EventType.APP_STARTED, source=EventSource(name="test")
    )
    second = EventEnvelope(
        type=EventType.APP_INITIALISED, source=EventSource(name="test")
    )

    asyncio.run(sink.append(first))
    asyncio.run(sink.append(second))

    assert sink.events == (first, second)


def test_in_memory_event_sink_events_are_returned_as_tuple() -> None:
    sink = InMemoryEventSink(flush_rate=2)
    event = EventEnvelope(
        type=EventType.APP_STARTED, source=EventSource(name="test")
    )

    asyncio.run(sink.append(event))

    assert isinstance(sink.events, tuple)


def test_in_memory_event_sink_flushes_at_flush_rate() -> None:
    sink = InMemoryEventSink(flush_rate=2)
    first = EventEnvelope(
        type=EventType.APP_STARTED, source=EventSource(name="test")
    )
    second = EventEnvelope(
        type=EventType.APP_INITIALISED, source=EventSource(name="test")
    )

    asyncio.run(sink.append(first))
    asyncio.run(sink.append(second))

    assert sink.events == ()


def test_turso_event_sink_persists_events_at_flush_rate(
    tmp_path: Path,
) -> None:
    db = turso.connect(str(tmp_path / "events.db"))
    sink = TursoEventSink(db, flush_rate=2)
    first = EventEnvelope(
        type=EventType.APP_STARTED,
        source=EventSource(name="test", detail="first"),
        tags=("storage",),
        payload=EventPayload(schema_name="test.payload"),
    )
    second = EventEnvelope(
        type=EventType.APP_INITIALISED,
        source=EventSource(name="test", detail="second"),
    )

    asyncio.run(sink.append(first))
    assert db.execute("SELECT COUNT(*) FROM event_envelopes").fetchone() == (0,)

    asyncio.run(sink.append(second))

    rows = db.execute(
        """
        SELECT id, event_type, source_detail, tags, payload
        FROM event_envelopes
        ORDER BY created_at
        """
    ).fetchall()
    assert rows == [
        (
            str(first.id),
            EventType.APP_STARTED.value,
            "first",
            json.dumps(first.tags),
            first.payload.model_dump_json(),
        ),
        (
            str(second.id),
            EventType.APP_INITIALISED.value,
            "second",
            json.dumps(second.tags),
            second.payload.model_dump_json(),
        ),
    ]


def test_listener_registry_delivers_events_in_registration_order() -> None:
    registry = EventListenerRegistry()
    delivered_to: list[str] = []
    event = EventEnvelope(
        type=EventType.APP_INITIALISED,
        source=EventSource(name="test"),
    )

    async def first_listener(_event: EventEnvelope) -> None:
        delivered_to.append("first")

    async def second_listener(_event: EventEnvelope) -> None:
        delivered_to.append("second")

    registry.register(first_listener)
    registry.register(second_listener)

    asyncio.run(registry.deliver(event))

    assert delivered_to == ["first", "second"]


def test_listener_registry_delivers_only_matching_event_type() -> None:
    registry = EventListenerRegistry()
    delivered_to: list[str] = []
    event = EventEnvelope(
        type=EventType.APP_STARTED,
        source=EventSource(name="test"),
    )

    async def started_listener(_event: EventEnvelope) -> None:
        delivered_to.append("started")

    async def initialised_listener(_event: EventEnvelope) -> None:
        delivered_to.append("initialised")

    registry.register(started_listener, event_type=[EventType.APP_STARTED])
    registry.register(
        initialised_listener, event_type=[EventType.APP_INITIALISED]
    )

    asyncio.run(registry.deliver(event))

    assert delivered_to == ["started"]


def test_listener_registry_continues_after_listener_failure() -> None:
    registry = EventListenerRegistry()
    delivered_to: list[str] = []
    event = EventEnvelope(
        type=EventType.APP_STARTED,
        source=EventSource(name="test"),
    )

    async def failing_listener(_event: EventEnvelope) -> None:
        delivered_to.append("failing")
        raise RuntimeError("listener failed")

    async def successful_listener(_event: EventEnvelope) -> None:
        delivered_to.append("successful")

    registry.register(failing_listener)
    registry.register(successful_listener)

    with pytest.raises(ExceptionGroup) as error:
        asyncio.run(registry.deliver(event))

    assert delivered_to == ["failing", "successful"]
    assert len(error.value.exceptions) == 1


def test_listener_registry_collects_multiple_failures() -> None:
    registry = EventListenerRegistry()
    event = EventEnvelope(
        type=EventType.APP_STARTED,
        source=EventSource(name="test"),
    )

    async def first_listener(_event: EventEnvelope) -> None:
        raise RuntimeError("first failed")

    async def second_listener(_event: EventEnvelope) -> None:
        raise ValueError("second failed")

    registry.register(first_listener)
    registry.register(second_listener)

    with pytest.raises(ExceptionGroup) as error:
        asyncio.run(registry.deliver(event))

    assert len(error.value.exceptions) == 2
