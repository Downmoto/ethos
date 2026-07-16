import asyncio
from pathlib import Path

import turso

from ethos.config import EventsConfig
from ethos.events import create_event_emitter
from ethos.events.emitters import EnvelopeEventEmitter
from ethos.events.listeners import EventListenerRegistry
from ethos.events.models import EventEnvelope, EventSource
from ethos.events.types import EventType
from ethos.storage import Storage


def test_emitter_writes_event_to_storage(tmp_path: Path) -> None:
    db_path = tmp_path / "events.db"
    storage = Storage(db_path)
    emitter = EnvelopeEventEmitter(enabled=True, storage=storage)
    event = EventEnvelope(
        type=EventType.APP_STARTED,
        source=EventSource(name="test"),
    )

    emitted = asyncio.run(emitter.emit(event))

    assert emitted == event
    assert emitted is event
    db = turso.connect(str(db_path))
    assert db.execute("SELECT id FROM event_envelopes").fetchone() == (
        str(event.id),
    )
    db.close()
    storage.close()


def test_event_emitter_is_created_with_explicit_storage(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "events.db")
    emitter = create_event_emitter(storage, EventsConfig())

    assert emitter._storage is storage  # pyright: ignore[reportPrivateUsage]
    storage.close()


def test_emitter_works_without_storage_or_dispatcher() -> None:
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


def test_emitter_writes_before_dispatching(tmp_path: Path) -> None:
    db_path = tmp_path / "events.db"
    storage = Storage(db_path)
    registry = EventListenerRegistry()
    emitter = EnvelopeEventEmitter(
        enabled=True, storage=storage, dispatcher=registry
    )
    stored_event_counts_seen_by_listener: list[int] = []
    event = EventEnvelope(
        type=EventType.APP_STARTED,
        source=EventSource(name="test"),
    )

    async def listener(_event: EventEnvelope) -> None:
        db = turso.connect(str(db_path))
        count = db.execute("SELECT COUNT(*) FROM event_envelopes").fetchone()[0]
        stored_event_counts_seen_by_listener.append(count)
        db.close()

    registry.register(listener)

    asyncio.run(emitter.emit(event))

    assert stored_event_counts_seen_by_listener == [1]
    storage.close()
