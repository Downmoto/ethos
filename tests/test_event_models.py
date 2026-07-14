from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from cassiopeia.events import event_factory
from cassiopeia.events.models import EventEnvelope, EventPayload, EventSource
from cassiopeia.events.types import EventType


def test_event_factory_returns_correct_event_envelope() -> None:
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
