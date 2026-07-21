import asyncio
import json
from collections.abc import AsyncIterator
from typing import cast

import pytest
import uvicorn
from fastapi.testclient import TestClient
from pydantic import JsonValue, SecretStr

from ethos.commands import CommandRequest, CommandResponse
from ethos.config import VoxConfig
from ethos.gateways import CommandExecutor
from ethos.gateways.vox import VoxGateway

WORKSPACE = {"name": "my-project", "path": "/workspaces/my-project"}
SESSION = {
    "id": "session-id",
    "workspace": "my-project",
    "created_at": "2026-07-21T00:00:00+00:00",
    "archived_at": None,
    "archived": False,
    "message_count": 0,
}


def executor(
    requests: list[CommandRequest],
) -> CommandExecutor:
    async def execute(
        request: CommandRequest,
    ) -> AsyncIterator[CommandResponse]:
        requests.append(request)
        data = cast(
            dict[str, JsonValue],
            {
                "workspace.create": {"workspace": WORKSPACE},
                "workspace.list": {"workspaces": [WORKSPACE]},
                "workspace.show": {"workspace": WORKSPACE},
                "session.create": {"session": SESSION},
                "session.list": {"sessions": [SESSION]},
                "session.show": {"session": SESSION},
                "session.archive": {"session": SESSION | {"archived": True}},
            }[request.name],
        )
        yield CommandResponse(data=data)

    return execute


@pytest.mark.parametrize(
    (
        "method",
        "path",
        "body",
        "command_name",
        "arguments",
        "status_code",
        "data",
    ),
    [
        (
            "POST",
            "/workspaces",
            {"name": "my-project"},
            "workspace.create",
            {"name": "my-project"},
            201,
            WORKSPACE,
        ),
        ("GET", "/workspaces", None, "workspace.list", {}, 200, [WORKSPACE]),
        (
            "GET",
            "/workspaces/my-project",
            None,
            "workspace.show",
            {"name": "my-project"},
            200,
            WORKSPACE,
        ),
        (
            "POST",
            "/workspaces/my-project/sessions",
            None,
            "session.create",
            {"workspace": "my-project"},
            201,
            SESSION,
        ),
        (
            "GET",
            "/workspaces/my-project/sessions",
            None,
            "session.list",
            {"workspace": "my-project"},
            200,
            [SESSION],
        ),
        (
            "GET",
            "/workspaces/my-project/sessions/session-id",
            None,
            "session.show",
            {"workspace": "my-project", "session_id": "session-id"},
            200,
            SESSION,
        ),
        (
            "POST",
            "/workspaces/my-project/sessions/session-id/archive",
            None,
            "session.archive",
            {"workspace": "my-project", "session_id": "session-id"},
            200,
            SESSION | {"archived": True},
        ),
    ],
)
def test_vox_maps_rest_resources_to_commands(
    method: str,
    path: str,
    body: dict[str, str] | None,
    command_name: str,
    arguments: dict[str, str],
    status_code: int,
    data: object,
) -> None:
    requests: list[CommandRequest] = []
    app = VoxGateway(VoxConfig()).create_app(executor(requests))

    response = TestClient(app).request(method, path, json=body)

    assert response.status_code == status_code
    assert response.json() == data
    assert len(requests) == 1
    assert requests[0].name == command_name
    assert requests[0].arguments == arguments
    assert requests[0].source == "vox"
    assert requests[0].owner_id
    assert requests[0].external_context == {"client_host": "testclient"}


def test_vox_streams_chat_responses_as_server_sent_events() -> None:
    requests: list[CommandRequest] = []

    async def execute(
        request: CommandRequest,
    ) -> AsyncIterator[CommandResponse]:
        requests.append(request)
        yield CommandResponse(text="hello ")
        yield CommandResponse(text="there", done=True)

    app = VoxGateway(VoxConfig()).create_app(execute)

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

    assert response.status_code == 200
    assert [event["text"] for event in events] == ["hello ", "there"]
    assert events[-1]["done"]
    assert requests[0].name == "session.chat"
    assert requests[0].arguments == {
        "workspace": "my-project",
        "session_id": "session-id",
        "prompt": "hi",
    }


def test_vox_enforces_configured_bearer_token() -> None:
    requests: list[CommandRequest] = []
    app = VoxGateway(VoxConfig(bearer_token=SecretStr("secret"))).create_app(
        executor(requests)
    )
    client = TestClient(app)

    missing = client.get("/workspaces")
    incorrect = client.get(
        "/workspaces", headers={"Authorization": "Bearer wrong"}
    )
    accepted = client.get(
        "/workspaces", headers={"Authorization": "Bearer secret"}
    )

    assert missing.status_code == 401
    assert incorrect.status_code == 401
    assert accepted.status_code == 200
    assert len(requests) == 1


def test_vox_requires_authentication_beyond_loopback() -> None:
    with pytest.raises(ValueError, match="requires a bearer token"):
        VoxGateway(VoxConfig(host="0.0.0.0"))


def test_vox_runs_uvicorn_with_configured_bind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configs: list[uvicorn.Config] = []

    class TestServer:
        def __init__(self, config: uvicorn.Config) -> None:
            configs.append(config)

        async def serve(self) -> None:
            return None

    async def execute(
        _request: CommandRequest,
    ) -> AsyncIterator[CommandResponse]:
        yield CommandResponse()

    monkeypatch.setattr("ethos.gateways.vox._VoxServer", TestServer)
    gateway = VoxGateway(VoxConfig(host="localhost", port=9000))

    asyncio.run(gateway.run(execute))

    assert configs[0].host == "localhost"
    assert configs[0].port == 9000


def test_vox_cancellation_waits_for_graceful_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    stopped = asyncio.Event()

    class TestServer:
        def __init__(self, _config: uvicorn.Config) -> None:
            self.should_exit = False

        async def serve(self) -> None:
            started.set()
            while not self.should_exit:
                await asyncio.sleep(0)
            stopped.set()

    async def execute(
        _request: CommandRequest,
    ) -> AsyncIterator[CommandResponse]:
        yield CommandResponse()

    async def cancel_gateway() -> None:
        running = asyncio.create_task(VoxGateway(VoxConfig()).run(execute))
        await started.wait()
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running

    monkeypatch.setattr("ethos.gateways.vox._VoxServer", TestServer)

    asyncio.run(cancel_gateway())

    assert stopped.is_set()
