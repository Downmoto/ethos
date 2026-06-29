import asyncio

import pytest
from pydantic import ValidationError

from cassiopeia.events import (
    EVENT_TYPE_CATALOGUE,
    EVENT_TYPE_PATTERN,
    EnvelopeEventEmitter,
    EventCreate,
    EventEmitter,
    EventEnvelope,
    EventPayload,
    EventSource,
    EventType,
    GatewayEventPayload,
    MemoryEventPayload,
    MessageEventPayload,
    PermissionEventPayload,
    SessionEventPayload,
    SubagentEventPayload,
    WorkflowEventPayload,
    WorkspaceEventPayload,
)


def test_event_types_use_dot_format() -> None:
    for event_type in EventType:
        assert EVENT_TYPE_PATTERN.fullmatch(event_type.value)
        assert "_" not in event_type.value


def test_event_catalogue_contains_each_event_type_once() -> None:
    assert [definition.type for definition in EVENT_TYPE_CATALOGUE] == list(EventType)


def test_event_catalogue_entries_are_immutable() -> None:
    definition = EVENT_TYPE_CATALOGUE[0]

    try:
        definition.description = "changed"
    except ValidationError as error:
        assert "frozen" in str(error)
    else:
        raise AssertionError("event catalogue entries should be immutable")


def test_event_envelope_uses_consistent_defaults() -> None:
    event = EventEnvelope(
        type=EventType.SESSION_CREATED,
        source=EventSource(name="test"),
    )

    assert event.id.version == 4
    assert event.created_at.tzinfo is not None
    assert event.source.name == "test"
    assert event.workspace_id is None
    assert event.session_id is None
    assert event.persona_id is None
    assert event.gateway_id is None
    assert event.correlation_id is None
    assert event.causation_id is None
    assert event.tags == ()
    assert event.payload == EventPayload()


def test_event_envelope_accepts_scope_correlation_tags_and_payload() -> None:
    parent = EventEnvelope(type=EventType.SESSION_CREATED, source=EventSource(name="test"))
    child = EventEnvelope(
        type=EventType.MESSAGE_RECEIVED,
        source=EventSource(name="gateway", detail="telegram"),
        workspace_id="workspace-1",
        session_id="session-1",
        persona_id="persona-1",
        gateway_id="telegram-main",
        correlation_id=parent.id,
        causation_id=parent.id,
        tags=("inbound", "telegram"),
        payload=EventPayload(
            schema_name="message.received",
            schema_version=1,
            data={"text": "hello"},
        ),
    )

    assert child.correlation_id == parent.id
    assert child.causation_id == parent.id
    assert child.tags == ("inbound", "telegram")
    assert child.payload.data == {"text": "hello"}


def test_event_envelope_rejects_naive_timestamp() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        EventEnvelope.model_validate(
            {
                "type": "session.created",
                "created_at": "2026-06-28T12:00:00",
                "source": {"name": "test"},
            }
        )


def test_event_envelope_rejects_empty_scope_ids_and_tags() -> None:
    with pytest.raises(ValidationError, match="String should have at least 1 character"):
        EventEnvelope(
            type=EventType.MESSAGE_SENT,
            source=EventSource(name="test"),
            session_id="",
        )

    with pytest.raises(ValidationError, match="String should have at least 1 character"):
        EventEnvelope(
            type=EventType.MESSAGE_SENT,
            source=EventSource(name="test"),
            tags=("valid", ""),
        )


def test_generic_payload_loads_unknown_schema_names_and_future_versions() -> None:
    payload = EventPayload.from_stored(
        {
            "schema_name": "future.gateway.payload",
            "schema_version": 99,
            "data": {"new_field": "kept"},
            "unknown_top_level": {"also": "kept"},
        }
    )

    assert payload.schema_name == "future.gateway.payload"
    assert payload.schema_version == 99
    assert payload.data == {"new_field": "kept"}
    assert payload.model_extra == {"unknown_top_level": {"also": "kept"}}


def test_event_envelope_loads_historical_payload_without_typed_family_validation() -> None:
    event = EventEnvelope.model_validate(
        {
            "type": "message.received",
            "created_at": "2026-06-28T12:00:00Z",
            "source": {"name": "storage"},
            "payload": {
                "schema_name": "message.received",
                "schema_version": 1,
                "data": {"legacy_direction": "inbound"},
            },
        }
    )

    assert event.payload.schema_name == "message.received"
    assert event.payload.data == {"legacy_direction": "inbound"}


def test_event_family_payloads_accept_minimum_valid_shapes() -> None:
    payloads = (
        SessionEventPayload(session_id="session-1"),
        MessageEventPayload(direction="received", message_id="message-1", text="hello"),
        MemoryEventPayload(scope="workspace", action="created", memory_id="memory-1"),
        PermissionEventPayload(action="shell.run", ring=1, decision="requested"),
        WorkflowEventPayload(status="started", workflow_id="summarise-thread"),
        SubagentEventPayload(status="created", subagent_id="worker-1"),
        GatewayEventPayload(status="connected", gateway_id="telegram"),
        WorkspaceEventPayload(action="created", workspace_id="workspace-1"),
    )

    for payload in payloads:
        assert payload.schema_version == 1
        assert payload.data == {}


def test_event_family_payloads_reject_invalid_lifecycle_values() -> None:
    with pytest.raises(ValidationError, match="Input should be"):
        MessageEventPayload.model_validate({"direction": "inbound"})

    with pytest.raises(ValidationError, match="less than or equal to 3"):
        PermissionEventPayload(action="shell.run", ring=4, decision="requested")

    with pytest.raises(ValidationError, match="String should have at least 1 character"):
        WorkflowEventPayload(status="failed", error="")


def test_event_create_rejects_persisted_identity_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EventCreate.model_validate(
            {
                "id": "00000000-0000-4000-8000-000000000000",
                "type": "session.created",
                "source": {"name": "test"},
            }
        )


def test_envelope_event_emitter_returns_validated_envelope() -> None:
    async def emit_event(emitter: EventEmitter) -> EventEnvelope:
        parent = EventEnvelope(type=EventType.SESSION_CREATED, source=EventSource(name="test"))
        return await emitter.emit(
            EventCreate(
                type=EventType.MESSAGE_RECEIVED,
                source=EventSource(name="gateway", detail="telegram"),
                workspace_id="workspace-1",
                session_id="session-1",
                persona_id="persona-1",
                gateway_id="gateway-1",
                correlation_id=parent.id,
                causation_id=parent.id,
                tags=("inbound",),
                payload=MessageEventPayload(direction="received", text="hello"),
            )
        )

    emitted = asyncio.run(emit_event(EnvelopeEventEmitter()))

    assert emitted.id.version == 4
    assert emitted.type is EventType.MESSAGE_RECEIVED
    assert emitted.source == EventSource(name="gateway", detail="telegram")
    assert emitted.workspace_id == "workspace-1"
    assert emitted.session_id == "session-1"
    assert emitted.persona_id == "persona-1"
    assert emitted.gateway_id == "gateway-1"
    assert emitted.correlation_id == emitted.causation_id
    assert emitted.tags == ("inbound",)
    assert emitted.payload == MessageEventPayload(direction="received", text="hello")
