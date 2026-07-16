"""Turso-backed application storage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import turso

if TYPE_CHECKING:
    from ethos.events.models import EventEnvelope


class Storage:
    """Own the application database connection and its reads and writes."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = turso.connect(str(db_path))
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS event_envelopes (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_detail TEXT,
                tags TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        self._db.commit()

    def write_event(self, event: EventEnvelope) -> None:
        """Persist an emitted event immediately."""
        self._db.execute(
            """
            INSERT INTO event_envelopes (
                id, created_at, event_type, source_name,
                source_detail, tags, payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(event.id),
                event.created_at.isoformat(),
                event.type.value,
                event.source.name,
                event.source.detail,
                json.dumps(event.tags),
                event.payload.model_dump_json(),
            ),
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()
