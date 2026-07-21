"""FastAPI gateway for Ethos workspace and session commands."""

# pyright: reportUnusedFunction=false
import asyncio
import getpass
import secrets
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from ipaddress import ip_address
from typing import Annotated, Final

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ethos.commands import CommandRequest, CommandResponse
from ethos.config import VoxConfig
from ethos.gateways.base import CommandExecutor, Gateway

_SOURCE: Final = "vox"


class _VoxServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self) -> Iterator[None]:
        """Leave process signal handling to the Ethos gateway runner."""
        yield


class _WorkspaceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str


class _ChatBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1)


def _is_loopback(host: str) -> bool:
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


class VoxGateway(Gateway):
    """Expose universal Ethos commands as REST resources."""

    def __init__(self, config: VoxConfig) -> None:
        if not _is_loopback(config.host) and config.bearer_token is None:
            raise ValueError(
                "vox requires a bearer token when exposed beyond loopback"
            )
        self.config = config

    @property
    def name(self) -> str:
        return _SOURCE

    def create_app(self, execute: CommandExecutor) -> FastAPI:
        """Create the ASGI application backed by an Ethos executor."""
        security = HTTPBearer(auto_error=False)

        async def authenticate(
            credentials: Annotated[
                HTTPAuthorizationCredentials | None, Depends(security)
            ],
        ) -> None:
            configured = self.config.bearer_token
            if configured is None:
                return
            if credentials is None or not secrets.compare_digest(
                credentials.credentials, configured.get_secret_value()
            ):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="invalid bearer token",
                    headers={"WWW-Authenticate": "Bearer"},
                )

        app = FastAPI(
            title="Ethos Vox",
            dependencies=[Depends(authenticate)],
        )

        def command(
            request: Request,
            name: str,
            arguments: dict[str, JsonValue],
        ) -> CommandRequest:
            context = (
                {"client_host": request.client.host}
                if request.client is not None
                else {}
            )
            return CommandRequest(
                name=name,
                arguments=arguments,
                source=_SOURCE,
                owner_id=getpass.getuser(),
                external_context=context,
            )

        async def execute_one(request: CommandRequest) -> CommandResponse:
            try:
                responses = [response async for response in execute(request)]
            except FileExistsError as error:
                raise HTTPException(
                    status_code=409, detail=str(error)
                ) from error
            except FileNotFoundError as error:
                raise HTTPException(
                    status_code=404, detail=str(error)
                ) from error
            except ValueError as error:
                raise HTTPException(
                    status_code=422, detail=str(error)
                ) from error
            if len(responses) != 1:
                raise RuntimeError(
                    f"{request.name} returned {len(responses)} responses"
                )
            return responses[0]

        @app.post("/workspaces", status_code=status.HTTP_201_CREATED)
        async def create_workspace(
            body: _WorkspaceBody, request: Request
        ) -> JsonValue:
            response = await execute_one(
                command(request, "workspace.create", {"name": body.name})
            )
            return response.data["workspace"]

        @app.get("/workspaces")
        async def list_workspaces(request: Request) -> JsonValue:
            response = await execute_one(command(request, "workspace.list", {}))
            return response.data["workspaces"]

        @app.get("/workspaces/{workspace}")
        async def show_workspace(workspace: str, request: Request) -> JsonValue:
            response = await execute_one(
                command(request, "workspace.show", {"name": workspace})
            )
            return response.data["workspace"]

        @app.post(
            "/workspaces/{workspace}/sessions",
            status_code=status.HTTP_201_CREATED,
        )
        async def create_session(workspace: str, request: Request) -> JsonValue:
            response = await execute_one(
                command(request, "session.create", {"workspace": workspace})
            )
            return response.data["session"]

        @app.get("/workspaces/{workspace}/sessions")
        async def list_sessions(workspace: str, request: Request) -> JsonValue:
            response = await execute_one(
                command(request, "session.list", {"workspace": workspace})
            )
            return response.data["sessions"]

        @app.get("/workspaces/{workspace}/sessions/{session_id}")
        async def show_session(
            workspace: str, session_id: str, request: Request
        ) -> JsonValue:
            response = await execute_one(
                command(
                    request,
                    "session.show",
                    {"workspace": workspace, "session_id": session_id},
                )
            )
            return response.data["session"]

        @app.post("/workspaces/{workspace}/sessions/{session_id}/archive")
        async def archive_session(
            workspace: str, session_id: str, request: Request
        ) -> JsonValue:
            response = await execute_one(
                command(
                    request,
                    "session.archive",
                    {"workspace": workspace, "session_id": session_id},
                )
            )
            return response.data["session"]

        @app.post("/workspaces/{workspace}/sessions/{session_id}/messages")
        async def chat(
            workspace: str,
            session_id: str,
            body: _ChatBody,
            request: Request,
        ) -> StreamingResponse:
            issued = command(
                request,
                "session.chat",
                {
                    "workspace": workspace,
                    "session_id": session_id,
                    "prompt": body.prompt,
                },
            )

            async def events() -> AsyncIterator[str]:
                async for response in execute(issued):
                    yield f"data: {response.model_dump_json()}\n\n"

            return StreamingResponse(events(), media_type="text/event-stream")

        return app

    async def run(self, execute: CommandExecutor) -> None:
        """Serve Vox until stopped or cancelled by the gateway runner."""
        server = _VoxServer(
            uvicorn.Config(
                self.create_app(execute),
                host=self.config.host,
                port=self.config.port,
            )
        )
        serving = asyncio.create_task(server.serve())
        try:
            await asyncio.shield(serving)
        except asyncio.CancelledError:
            server.should_exit = True
            await serving
            raise
