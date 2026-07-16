import json
from pathlib import Path

import turso

from ethos.events.models import EventEnvelope, EventPayload, EventSource
from ethos.events.types import EventType
from ethos.storage import Storage


def test_storage_persists_each_event(tmp_path: Path) -> None:
    db_path = tmp_path / "events.db"
    storage = Storage(db_path)
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

    storage.write_event(first)
    db = turso.connect(str(db_path))
    assert db.execute("SELECT COUNT(*) FROM event_envelopes").fetchone() == (1,)

    storage.write_event(second)

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
    db.close()
    storage.close()
