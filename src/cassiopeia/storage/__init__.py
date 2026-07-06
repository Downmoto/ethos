"""Turso-backed storage for cassiopeia events."""

import json
from pathlib import Path

import turso
from turso import Connection

from cassiopeia.events.models import EventEnvelope
from cassiopeia.shared import DB_FILE_PATH


class TursoEventSink:
    """Persist emitted events to Turso in append order."""

    def __init__(self, db: Connection, flush_rate: int) -> None:
        self._db = db
        self._flush_rate = flush_rate
        self._pending: list[EventEnvelope] = []
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

    async def append(self, event: EventEnvelope) -> None:
        """Buffer an event and flush when the configured batch size is met."""

        self._pending.append(event)

        if len(self._pending) >= self._flush_rate:
            await self.flush()

    async def flush(self) -> None:
        """Persist pending events."""

        if not self._pending:
            return

        self._db.executemany(
            """
            INSERT INTO event_envelopes (
                id,
                created_at,
                event_type,
                source_name,
                source_detail,
                tags,
                payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    str(event.id),
                    event.created_at.isoformat(),
                    event.type.value,
                    event.source.name,
                    event.source.detail,
                    json.dumps(event.tags),
                    event.payload.model_dump_json(),
                )
                for event in self._pending
            ],
        )
        self._db.commit()
        self._pending.clear()


def create_event_sink(
    flush_rate: int, db_path: Path = DB_FILE_PATH
) -> TursoEventSink:
    """Create the default Turso event sink."""

    return TursoEventSink(turso.connect(str(db_path)), flush_rate)
