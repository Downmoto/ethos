import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest
import turso
from pydantic import BaseModel

from ethos.events.emitters import EnvelopeEventEmitter
from ethos.events.listeners import EventListenerRegistry
from ethos.events.models import EventEnvelope
from ethos.events.types import EventType
from ethos.models import (
    FinishReason,
    ModelFeatures,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolDefinition,
    Usage,
)
from ethos.provider import ModelProviderError
from ethos.runtime import (
    AgentRuntime,
    ApprovalStreamEvent,
    RuntimeEventError,
    RuntimeEventPayload,
    RuntimeStreamEvent,
)
from ethos.sessions import ApprovalStateError, Session, SessionManager
from ethos.storage import Storage
from ethos.tools import ApprovalState, ToolEffect, ToolRegistry
from ethos.workspaces import WorkspaceManager
from fakes import FakeModel


class Arguments(BaseModel):
    value: str


class RecordingTool:
    arguments_type: type[BaseModel] = Arguments

    def __init__(self, effect: ToolEffect = ToolEffect.READ) -> None:
        self.definition = ToolDefinition(
            name="echo",
            description="Echo a value",
            parameters_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
        )
        self.effect = effect
        self.values: list[str] = []

    async def execute(self, arguments: BaseModel) -> str:
        assert isinstance(arguments, Arguments)
        self.values.append(arguments.value)
        return f"secret result: {arguments.value}"


def call_response(value: str = "secret argument") -> ModelResponse:
    return ModelResponse(
        parts=(
            ToolCallPart(
                call_id="call-1",
                name="echo",
                arguments_json=f'{{"value":"{value}"}}',
            ),
        ),
        usage=Usage(input_tokens=2, output_tokens=1),
        finish_reason=FinishReason.TOOL_CALL,
        provider_response_id="tool-response",
    )


def text_response() -> ModelResponse:
    return ModelResponse(
        parts=(TextPart(text="done"),),
        usage=Usage(input_tokens=3, output_tokens=2),
        finish_reason=FinishReason.STOP,
        provider_response_id="text-response",
    )


def setup_runtime(
    tmp_path: Path,
    model: FakeModel,
    tool: RecordingTool | None = None,
    *,
    storage: Storage | None = None,
) -> tuple[AgentRuntime, Session, RecordingTool, list[EventEnvelope]]:
    workspaces = WorkspaceManager(tmp_path / "workspaces")
    workspaces.create("my-project")
    sessions = SessionManager(workspaces, tmp_path / "sessions")
    session = sessions.create("my-project")
    registered = tool or RecordingTool()
    delivered: list[EventEnvelope] = []
    listeners = EventListenerRegistry()

    async def record(event: EventEnvelope) -> None:
        delivered.append(event)

    listeners.register(record)
    emitter = EnvelopeEventEmitter(storage=storage, dispatcher=listeners)
    runtime = AgentRuntime(
        sessions,
        lambda: model,
        ToolRegistry((registered,)),
        events=emitter,
    )
    return runtime, session, registered, delivered


async def collect(
    events: AsyncIterator[RuntimeStreamEvent],
) -> list[RuntimeStreamEvent]:
    return [event async for event in events]


def event_types(events: list[EventEnvelope]) -> list[EventType]:
    return [event.type for event in events]


def runtime_payloads(
    events: list[EventEnvelope],
) -> list[RuntimeEventPayload]:
    return [cast(RuntimeEventPayload, event.payload) for event in events]


def test_text_run_emits_ordered_correlated_trace(tmp_path: Path) -> None:
    model = FakeModel(
        (text_response(),),
        stream_chunks=(("done",),),
        features=ModelFeatures(tools=True),
    )
    runtime, session, _tool, events = setup_runtime(tmp_path, model)

    asyncio.run(
        collect(
            runtime.run(
                "private prompt",
                "my-project",
                str(session.id),
                event_location="test-adapter",
            )
        )
    )

    assert event_types(events) == [
        EventType.RUN_STARTED,
        EventType.MODEL_REQUEST_STARTED,
        EventType.MODEL_REQUEST_COMPLETED,
        EventType.RUN_COMPLETED,
    ]
    assert len({payload.run_id for payload in runtime_payloads(events)}) == 1
    assert {event.source.name for event in events} == {"test-adapter"}
    assert all(event.source.detail == event.type.value for event in events)
    assert "private prompt" not in "".join(
        event.model_dump_json() for event in events
    )


def test_read_tool_trace_is_ordered_and_private(tmp_path: Path) -> None:
    model = FakeModel(
        (call_response(), text_response()),
        stream_chunks=((), ("done",)),
        features=ModelFeatures(tools=True),
    )
    runtime, session, tool, events = setup_runtime(tmp_path, model)

    asyncio.run(collect(runtime.run("hello", "my-project", str(session.id))))

    assert tool.values == ["secret argument"]
    assert event_types(events) == [
        EventType.RUN_STARTED,
        EventType.MODEL_REQUEST_STARTED,
        EventType.MODEL_REQUEST_COMPLETED,
        EventType.TOOL_CALL_REQUESTED,
        EventType.TOOL_CALL_PREPARED,
        EventType.TOOL_EXECUTION_STARTED,
        EventType.TOOL_EXECUTION_COMPLETED,
        EventType.MODEL_REQUEST_STARTED,
        EventType.MODEL_REQUEST_COMPLETED,
        EventType.RUN_COMPLETED,
    ]
    serialised = "".join(event.model_dump_json() for event in events)
    assert "secret argument" not in serialised
    assert "secret result" not in serialised
    assert [payload.round_number for payload in runtime_payloads(events)] == [
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        2,
        2,
        2,
    ]


@pytest.mark.parametrize(
    ("approved", "resolution_events"),
    (
        (
            True,
            (
                EventType.RUN_RESUMED,
                EventType.TOOL_APPROVAL_APPROVED,
                EventType.TOOL_EXECUTION_STARTED,
                EventType.TOOL_EXECUTION_COMPLETED,
            ),
        ),
        (
            False,
            (
                EventType.RUN_RESUMED,
                EventType.TOOL_APPROVAL_DENIED,
            ),
        ),
    ),
)
def test_approval_resume_preserves_run_trace(
    tmp_path: Path,
    approved: bool,
    resolution_events: tuple[EventType, ...],
) -> None:
    model = FakeModel(
        (call_response(), text_response()),
        stream_chunks=((), ("done",)),
        features=ModelFeatures(tools=True),
    )
    runtime, session, tool, events = setup_runtime(
        tmp_path, model, RecordingTool(ToolEffect.WRITE)
    )
    paused = asyncio.run(
        collect(runtime.run("hello", "my-project", str(session.id)))
    )
    approval_event = paused[-1]
    assert isinstance(approval_event, ApprovalStreamEvent)
    pause_types = event_types(events)
    assert pause_types == [
        EventType.RUN_STARTED,
        EventType.MODEL_REQUEST_STARTED,
        EventType.MODEL_REQUEST_COMPLETED,
        EventType.TOOL_CALL_REQUESTED,
        EventType.TOOL_CALL_PREPARED,
        EventType.TOOL_APPROVAL_REQUESTED,
        EventType.RUN_PAUSED,
    ]

    asyncio.run(
        collect(
            runtime.resolve_approval(
                "my-project",
                str(session.id),
                approval_event.approval.id,
                approved=approved,
            )
        )
    )

    assert event_types(events)[len(pause_types) :] == [
        *resolution_events,
        EventType.MODEL_REQUEST_STARTED,
        EventType.MODEL_REQUEST_COMPLETED,
        EventType.RUN_COMPLETED,
    ]
    assert len({payload.run_id for payload in runtime_payloads(events)}) == 1
    assert tool.values == (["secret argument"] if approved else [])


def test_durable_start_event_precedes_tool_side_effect(tmp_path: Path) -> None:
    db_path = tmp_path / "events.db"
    storage = Storage(db_path)

    class InspectingTool(RecordingTool):
        async def execute(self, arguments: BaseModel) -> str:
            db = turso.connect(str(db_path))
            count = db.execute(
                "SELECT COUNT(*) FROM event_envelopes WHERE event_type = ?",
                (EventType.TOOL_EXECUTION_STARTED.value,),
            ).fetchone()[0]
            db.close()
            assert count == 1
            return await super().execute(arguments)

    model = FakeModel(
        (call_response("safe"), text_response()),
        stream_chunks=((), ("done",)),
        features=ModelFeatures(tools=True),
    )
    runtime, session, tool, _events = setup_runtime(
        tmp_path, model, InspectingTool(), storage=storage
    )

    asyncio.run(collect(runtime.run("hello", "my-project", str(session.id))))

    assert tool.values == ["safe"]
    db = turso.connect(str(db_path))
    rows = db.execute(
        "SELECT source_name, tags, payload FROM event_envelopes "
        "ORDER BY created_at"
    ).fetchall()
    db.close()
    assert len(rows) == 10
    assert {row[0] for row in rows} == {"runtime"}
    payload = json.loads(rows[0][2])
    assert payload["schema_name"] == "runtime.trace"
    assert payload["schema_version"] == 1
    tags = json.loads(rows[0][1])
    assert "workspace:my-project" in tags
    assert f"session:{session.id}" in tags
    assert any(tag.startswith("run:") for tag in tags)
    storage.close()


def test_start_event_failure_prevents_tool_execution(tmp_path: Path) -> None:
    model = FakeModel(
        (call_response("must not run"),),
        features=ModelFeatures(tools=True),
    )
    workspaces = WorkspaceManager(tmp_path / "workspaces")
    workspaces.create("my-project")
    sessions = SessionManager(workspaces, tmp_path / "sessions")
    session = sessions.create("my-project")
    tool = RecordingTool()
    listeners = EventListenerRegistry()

    async def fail(event: EventEnvelope) -> None:
        if event.type is EventType.TOOL_EXECUTION_STARTED:
            raise RuntimeError("listener secret")

    listeners.register(fail)
    runtime = AgentRuntime(
        sessions,
        lambda: model,
        ToolRegistry((tool,)),
        events=EnvelopeEventEmitter(dispatcher=listeners),
    )

    with pytest.raises(
        RuntimeEventError, match="runtime event emission failed"
    ):
        asyncio.run(
            collect(runtime.run("hello", "my-project", str(session.id)))
        )

    assert tool.values == []


def test_provider_failure_trace_uses_safe_category(tmp_path: Path) -> None:
    model = FakeModel((ModelProviderError("secret API failure"),))
    runtime, session, _tool, events = setup_runtime(tmp_path, model)

    with pytest.raises(ModelProviderError, match="secret API failure"):
        asyncio.run(
            collect(
                runtime.run("private prompt", "my-project", str(session.id))
            )
        )

    assert event_types(events) == [
        EventType.RUN_STARTED,
        EventType.MODEL_REQUEST_STARTED,
        EventType.MODEL_REQUEST_FAILED,
        EventType.RUN_FAILED,
    ]
    serialised = "".join(event.model_dump_json() for event in events)
    assert '"failure":"provider"' in serialised
    assert "private prompt" not in serialised
    assert "secret API failure" not in serialised


def test_indeterminate_recovery_continues_original_trace(
    tmp_path: Path,
) -> None:
    model = FakeModel(
        (call_response(),),
        features=ModelFeatures(tools=True),
    )
    runtime, session, tool, events = setup_runtime(
        tmp_path, model, RecordingTool(ToolEffect.WRITE)
    )
    paused = asyncio.run(
        collect(runtime.run("hello", "my-project", str(session.id)))
    )
    approval_event = paused[-1]
    assert isinstance(approval_event, ApprovalStreamEvent)
    approval = approval_event.approval

    sessions = SessionManager(
        WorkspaceManager(tmp_path / "workspaces"), tmp_path / "sessions"
    )
    sessions.transition_approval(
        "my-project",
        str(session.id),
        approval.id,
        expected=ApprovalState.PENDING,
        state=ApprovalState.EXECUTING,
    )
    listeners = EventListenerRegistry()

    async def record(event: EventEnvelope) -> None:
        events.append(event)

    listeners.register(record)
    restarted = AgentRuntime(
        sessions,
        lambda: FakeModel(()),
        ToolRegistry((tool,)),
        events=EnvelopeEventEmitter(dispatcher=listeners),
    )

    with pytest.raises(ApprovalStateError, match="indeterminate"):
        asyncio.run(
            collect(
                restarted.resolve_approval(
                    "my-project",
                    str(session.id),
                    approval.id,
                    approved=True,
                )
            )
        )

    assert events[-1].type is EventType.TOOL_APPROVAL_INDETERMINATE
    recovered_payload = cast(RuntimeEventPayload, events[-1].payload)
    assert recovered_payload.run_id == approval.run_id
    assert tool.values == []
