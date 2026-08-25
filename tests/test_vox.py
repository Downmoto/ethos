import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator
from datetime import UTC, datetime
from typing import cast

import pytest
import uvicorn
from fastapi.testclient import TestClient
from pydantic import SecretStr

from ethos.config import VoxConfig
from ethos.gateway.vox import VoxServer, _event_stream
from ethos.models import Message, Role, ToolCallPart, ToolResultPart
from ethos.service import (
    ApprovalChunk,
    ChatChunk,
    ChatEvent,
    Ethos,
    RequestContext,
    SessionView,
    WorkspaceView,
)
from ethos.sessions import ApprovalNotFoundError, ApprovalStateError
from ethos.tools import ToolEffect

WORKSPACE = WorkspaceView(name="my-project", path="/workspaces/my-project")
SESSION = SessionView(
    id="session-id",
    workspace="my-project",
    created_at="2026-07-21T00:00:00+00:00",
    archived_at=None,
    archived=False,
    message_count=0,
)
HISTORY = (
    Message(
        role=Role.ASSISTANT,
        parts=(
            ToolCallPart(
                call_id="call-1",
                name="weather",
                arguments_json='{"location":"Toronto"}',
            ),
        ),
    ),
    Message(
        role=Role.TOOL,
        parts=(
            ToolResultPart(
                call_id="call-1",
                name="weather",
                content="23 degrees",
            ),
        ),
    ),
)


class FakeEthos:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def record(self, name: str, *arguments: object) -> None:
        self.calls.append((name, arguments))

    async def create_workspace(
        self, name: str, context: RequestContext
    ) -> WorkspaceView:
        self.record("create_workspace", name, context)
        return WORKSPACE

    async def list_workspaces(
        self, context: RequestContext
    ) -> tuple[WorkspaceView, ...]:
        self.record("list_workspaces", context)
        return (WORKSPACE,)

    async def show_workspace(
        self, name: str, context: RequestContext
    ) -> WorkspaceView:
        self.record("show_workspace", name, context)
        return WORKSPACE

    async def create_session(
        self, workspace: str, context: RequestContext
    ) -> SessionView:
        self.record("create_session", workspace, context)
        return SESSION

    async def list_sessions(
        self, workspace: str, context: RequestContext
    ) -> tuple[SessionView, ...]:
        self.record("list_sessions", workspace, context)
        return (SESSION,)

    async def show_session(
        self, workspace: str, session_id: str, context: RequestContext
    ) -> SessionView:
        self.record("show_session", workspace, session_id, context)
        return SESSION

    async def session_history(
        self, workspace: str, session_id: str, context: RequestContext
    ) -> tuple[Message, ...]:
        self.record("session_history", workspace, session_id, context)
        return HISTORY

    async def archive_session(
        self, workspace: str, session_id: str, context: RequestContext
    ) -> SessionView:
        self.record("archive_session", workspace, session_id, context)
        return SESSION.model_copy(update={"archived": True})

    async def recover_session(
        self, workspace: str, session_id: str, context: RequestContext
    ) -> SessionView:
        self.record("recover_session", workspace, session_id, context)
        return SESSION

    async def chat(
        self,
        workspace: str,
        session_id: str,
        prompt: str,
        context: RequestContext,
    ) -> AsyncIterator[ChatEvent]:
        self.record("chat", workspace, session_id, prompt, context)
        yield ChatChunk(
            text="thinking",
            text_kind="reasoning",
            workspace=workspace,
            session_id=session_id,
        )
        yield ChatChunk(
            text="hello ", workspace=workspace, session_id=session_id
        )
        yield ChatChunk(
            text="there",
            workspace=workspace,
            session_id=session_id,
            done=True,
        )

    async def resolve_approval(
        self,
        workspace: str,
        session_id: str,
        approval_id: str,
        approved: bool,
        context: RequestContext,
    ) -> AsyncIterator[ChatChunk]:
        self.record(
            "resolve_approval",
            workspace,
            session_id,
            approval_id,
            approved,
            context,
        )
        if workspace == "other":
            raise ApprovalNotFoundError("approval request does not exist")
        if approval_id == "stale":
            raise ApprovalStateError("approval request is completed")
        yield ChatChunk(
            text="resolved",
            workspace=workspace,
            session_id=session_id,
            done=True,
        )


@pytest.mark.parametrize(
    ("method", "path", "body", "call", "status_code", "data"),
    [
        (
            "POST",
            "/workspaces",
            {"name": "my-project"},
            "create_workspace",
            201,
            WORKSPACE.model_dump(),
        ),
        (
            "GET",
            "/workspaces",
            None,
            "list_workspaces",
            200,
            [WORKSPACE.model_dump()],
        ),
        (
            "GET",
            "/workspaces/my-project",
            None,
            "show_workspace",
            200,
            WORKSPACE.model_dump(),
        ),
        (
            "POST",
            "/workspaces/my-project/sessions",
            None,
            "create_session",
            201,
            SESSION.model_dump(),
        ),
        (
            "GET",
            "/workspaces/my-project/sessions",
            None,
            "list_sessions",
            200,
            [SESSION.model_dump()],
        ),
        (
            "GET",
            "/workspaces/my-project/sessions/session-id",
            None,
            "show_session",
            200,
            SESSION.model_dump(),
        ),
        (
            "GET",
            "/workspaces/my-project/sessions/session-id/history",
            None,
            "session_history",
            200,
            [message.model_dump(mode="json") for message in HISTORY],
        ),
        (
            "POST",
            "/workspaces/my-project/sessions/session-id/archive",
            None,
            "archive_session",
            200,
            SESSION.model_copy(update={"archived": True}).model_dump(),
        ),
        (
            "POST",
            "/workspaces/my-project/sessions/session-id/recover",
            None,
            "recover_session",
            200,
            SESSION.model_dump(),
        ),
    ],
)
def test_vox_preserves_resource_endpoints(
    method: str,
    path: str,
    body: dict[str, str] | None,
    call: str,
    status_code: int,
    data: object,
) -> None:
    ethos = FakeEthos()
    app = VoxServer(VoxConfig()).create_app(cast(Ethos, ethos))

    response = TestClient(app).request(method, path, json=body)

    assert response.status_code == status_code
    assert response.json() == data
    assert ethos.calls[0][0] == call
    context = cast(RequestContext, ethos.calls[0][1][-1])
    assert context.source == "vox"
    assert context.owner_id
    assert context.external_context == {"client_host": "testclient"}


def test_vox_streams_chat_as_server_sent_events() -> None:
    ethos = FakeEthos()
    app = VoxServer(VoxConfig()).create_app(cast(Ethos, ethos))

    with TestClient(app).stream(
        "POST",
        "/workspaces/my-project/sessions/session-id/messages",
        json={"prompt": "hi"},
    ) as response:
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]

    assert [event["text"] for event in events] == [
        "thinking",
        "hello ",
        "there",
    ]
    assert [event["text_kind"] for event in events] == [
        "reasoning",
        "answer",
        "answer",
    ]
    assert events[-1]["done"]
    assert ethos.calls[0][0] == "chat"


def test_vox_frames_approval_event() -> None:
    class ApprovalEthos(FakeEthos):
        async def chat(
            self,
            workspace: str,
            session_id: str,
            prompt: str,
            context: RequestContext,
        ) -> AsyncIterator[ChatEvent]:
            self.record("chat", workspace, session_id, prompt, context)
            yield ApprovalChunk(
                approval_id="approval-1",
                call_id="call-1",
                tool_name="write_file",
                arguments={"path": "README.md"},
                effect=ToolEffect.WRITE,
                reason="write tool requires approval",
                created_at=datetime(2026, 8, 21, tzinfo=UTC),
                workspace=workspace,
                session_id=session_id,
            )

    app = VoxServer(VoxConfig()).create_app(cast(Ethos, ApprovalEthos()))

    with TestClient(app).stream(
        "POST",
        "/workspaces/my-project/sessions/session-id/messages",
        json={"prompt": "hi"},
    ) as response:
        event = next(
            json.loads(line.removeprefix("data: "))
            for line in response.iter_lines()
            if line.startswith("data: ")
        )

    assert event["kind"] == "approval"
    assert event["approval_id"] == "approval-1"
    assert event["tool_name"] == "write_file"
    assert event["arguments"] == {"path": "README.md"}


def test_vox_event_stream_closes_source_when_consumer_stops() -> None:
    closed = False

    async def events() -> AsyncIterator[ChatEvent]:
        nonlocal closed
        try:
            yield ChatChunk(
                text="first",
                workspace="my-project",
                session_id="session-id",
            )
            await asyncio.Event().wait()
        finally:
            closed = True

    async def stop_after_first() -> None:
        response = await _event_stream(events())
        iterator = cast(AsyncGenerator[str, None], response.body_iterator)
        assert "first" in await anext(iterator)
        await iterator.aclose()

    asyncio.run(stop_after_first())

    assert closed


@pytest.mark.parametrize(
    ("decision", "approved"),
    (("approve", True), ("deny", False)),
)
def test_vox_resolves_approval_as_server_sent_events(
    decision: str,
    approved: bool,
) -> None:
    ethos = FakeEthos()
    app = VoxServer(VoxConfig()).create_app(cast(Ethos, ethos))
    path = (
        "/workspaces/my-project/sessions/session-id/"
        f"approvals/approval-1/{decision}"
    )

    with TestClient(app).stream("POST", path) as response:
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]

    assert response.status_code == 200
    assert events[-1]["done"]
    assert ethos.calls[0][0] == "resolve_approval"
    assert ethos.calls[0][1][3] is approved


@pytest.mark.parametrize(
    ("workspace", "approval_id", "status_code"),
    (("other", "approval-1", 404), ("my-project", "stale", 409)),
)
def test_vox_rejects_cross_session_or_stale_approval(
    workspace: str,
    approval_id: str,
    status_code: int,
) -> None:
    app = VoxServer(VoxConfig()).create_app(cast(Ethos, FakeEthos()))
    path = (
        f"/workspaces/{workspace}/sessions/session-id/"
        f"approvals/{approval_id}/approve"
    )

    response = TestClient(app).post(path)

    assert response.status_code == status_code


def test_vox_enforces_configured_bearer_token() -> None:
    ethos = FakeEthos()
    app = VoxServer(VoxConfig(bearer_token=SecretStr("secret"))).create_app(
        cast(Ethos, ethos)
    )
    client = TestClient(app)

    assert client.get("/workspaces").status_code == 401
    assert (
        client.post(
            "/workspaces/my-project/sessions/session-id/"
            "approvals/approval-1/approve"
        ).status_code
        == 401
    )
    assert (
        client.get(
            "/workspaces", headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 401
    )
    assert (
        client.get(
            "/workspaces", headers={"Authorization": "Bearer secret"}
        ).status_code
        == 200
    )


def test_vox_requires_authentication_beyond_loopback() -> None:
    with pytest.raises(ValueError, match="requires a bearer token"):
        VoxServer(VoxConfig(host="0.0.0.0"))


def test_vox_runs_uvicorn_with_configured_bind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configs: list[uvicorn.Config] = []

    class TestServer:
        def __init__(self, config: uvicorn.Config) -> None:
            configs.append(config)

        async def serve(self) -> None:
            return None

    monkeypatch.setattr("ethos.gateway.vox._UvicornServer", TestServer)
    server = VoxServer(VoxConfig(host="localhost", port=9000))

    asyncio.run(server.run(cast(Ethos, FakeEthos())))

    assert configs[0].host == "localhost"
    assert configs[0].port == 9000
