from datetime import UTC, datetime
from pathlib import Path

import turso

from ethos.events.models import EventEnvelope, EventPayload, EventSource
from ethos.events.types import EventType
from ethos.storage import Storage


def test_storage_persists_each_event(tmp_path: Path) -> None:
    db_path = tmp_path / "events.db"
    storage = Storage(db_path)
    created_at = datetime(2026, 8, 24, tzinfo=UTC)
    first = EventEnvelope(
        type=EventType.APP_STARTED,
        source=EventSource(name="test"),
        created_at=created_at,
        payload=EventPayload(schema_name="test.payload"),
    )
    second = EventEnvelope(
        type=EventType.APP_INITIALISED,
        source=EventSource(name="test"),
        created_at=created_at,
    )

    storage.write_event(first)
    db = turso.connect(str(db_path))
    assert db.execute("SELECT COUNT(*) FROM event_envelopes").fetchone() == (1,)

    storage.write_event(second)

    rows = db.execute(
        """
        SELECT sequence, id, event_type, source_name, payload
        FROM event_envelopes
        ORDER BY sequence
        """
    ).fetchall()
    assert rows == [
        (
            1,
            str(first.id),
            EventType.APP_STARTED.value,
            "test",
            first.payload.model_dump_json(),
        ),
        (
            2,
            str(second.id),
            EventType.APP_INITIALISED.value,
            "test",
            second.payload.model_dump_json(),
        ),
    ]
    db.close()
    storage.close()
