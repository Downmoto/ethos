"""Turso-backed event storage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import turso
from turso import Connection

if TYPE_CHECKING:
    from cassiopeia.events.models import EventEnvelope


def initialise_database(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = turso.connect(str(db_path))
    db.execute(
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
    db.commit()
    db.close()


class StorageEventSink:
    """Persist emitted events immediately in append order."""

    def __init__(self, db: Connection) -> None:
        self._db = db

    async def append(self, event: EventEnvelope) -> None:
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


def create_event_sink(db_path: Path) -> StorageEventSink:
    initialise_database(db_path)
    return StorageEventSink(turso.connect(str(db_path)))
