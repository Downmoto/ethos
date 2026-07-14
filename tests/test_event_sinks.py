import asyncio
import json
from pathlib import Path

import turso

from cassiopeia.events.models import EventEnvelope, EventPayload, EventSource
from cassiopeia.events.sinks import InMemoryEventSink
from cassiopeia.events.types import EventType
from cassiopeia.storage import StorageEventSink, initialise_database


def test_in_memory_event_sink_preserves_append_order() -> None:
    sink = InMemoryEventSink()
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
    sink = InMemoryEventSink()
    event = EventEnvelope(
        type=EventType.APP_STARTED, source=EventSource(name="test")
    )

    asyncio.run(sink.append(event))

    assert isinstance(sink.events, tuple)


def test_turso_event_sink_persists_each_event(tmp_path: Path) -> None:
    initialise_database(tmp_path / "events.db")
    db = turso.connect(str(tmp_path / "events.db"))
    sink = StorageEventSink(db)
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
    assert db.execute("SELECT COUNT(*) FROM event_envelopes").fetchone() == (1,)

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
