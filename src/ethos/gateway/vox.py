"""FastAPI implementation of the Vox protocol."""

# pyright: reportUnusedFunction=false
import asyncio
import getpass
import secrets
from collections.abc import AsyncIterator, Generator
from contextlib import contextmanager
from ipaddress import ip_address
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from ethos.config import VoxConfig
from ethos.models import Message
from ethos.service import (
    CapabilityView,
    ChatEvent,
    Ethos,
    PersonaAssignmentView,
    PersonaView,
    ProviderView,
    RequestContext,
    SessionView,
    WorkspaceView,
)
from ethos.sessions import ApprovalStateError


class _UvicornServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self) -> Generator[None, None, None]:
        """Leave signal handling to the Ethos process."""
        yield


class _WorkspaceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    persona: str | None = None


class _ChatBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1)


class _CapabilityBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settings: dict[str, object] = Field(min_length=1)


class _ProviderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settings: dict[str, object]


class _PersonaCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    settings: dict[str, object]


class _PersonaBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settings: dict[str, object] = Field(min_length=1)


class _PersonaAssignmentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persona: str


def _is_loopback(host: str) -> bool:
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


async def _event_stream(
    events: AsyncIterator[ChatEvent],
) -> StreamingResponse:
    """Prefetch once so pre-stream failures retain normal HTTP error mapping."""

    try:
        first = await anext(events)
    except StopAsyncIteration:
        first = None

    async def encoded() -> AsyncIterator[str]:
        try:
            if first is not None:
                yield f"data: {first.model_dump_json()}\n\n"
            async for event in events:
                yield f"data: {event.model_dump_json()}\n\n"
        finally:
            close = getattr(events, "aclose", None)
            if close is not None:
                await close()

    return StreamingResponse(encoded(), media_type="text/event-stream")


class VoxServer:
    """Expose Ethos through its sole external application protocol."""

    def __init__(self, config: VoxConfig) -> None:
        if not _is_loopback(config.host) and config.bearer_token is None:
            raise ValueError(
                "vox requires a bearer token when exposed beyond loopback"
            )
        self.config = config

    def create_app(self, ethos: Ethos) -> FastAPI:
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

        def context(request: Request) -> RequestContext:
            external = (
                {"client_host": request.client.host}
                if request.client is not None
                else {}
            )
            return RequestContext("vox", getpass.getuser(), external)

        @app.exception_handler(FileExistsError)
        async def conflict(
            _request: Request, error: FileExistsError
        ) -> JSONResponse:
            return JSONResponse(status_code=409, content={"detail": str(error)})

        @app.exception_handler(FileNotFoundError)
        async def not_found(
            _request: Request, error: FileNotFoundError
        ) -> JSONResponse:
            return JSONResponse(status_code=404, content={"detail": str(error)})

        @app.exception_handler(ValueError)
        async def invalid(_request: Request, error: ValueError) -> JSONResponse:
            return JSONResponse(status_code=422, content={"detail": str(error)})

        @app.exception_handler(ApprovalStateError)
        async def invalid_approval(
            _request: Request, error: ApprovalStateError
        ) -> JSONResponse:
            return JSONResponse(status_code=409, content={"detail": str(error)})

        @app.post("/workspaces", status_code=status.HTTP_201_CREATED)
        async def create_workspace(
            body: _WorkspaceBody, request: Request
        ) -> WorkspaceView:
            return await ethos.create_workspace(
                body.name, context(request), body.persona
            )

        @app.get("/workspaces")
        async def list_workspaces(
            request: Request,
        ) -> tuple[WorkspaceView, ...]:
            return await ethos.list_workspaces(context(request))

        @app.get("/workspaces/{workspace}")
        async def show_workspace(
            workspace: str, request: Request
        ) -> WorkspaceView:
            return await ethos.show_workspace(workspace, context(request))

        @app.get("/personas/default")
        async def show_default_persona(request: Request) -> PersonaView:
            return await ethos.show_default_persona(context(request))

        @app.put("/personas/default")
        async def configure_default_persona(
            body: _PersonaAssignmentBody,
            request: Request,
        ) -> PersonaView:
            return await ethos.configure_default_persona(
                body.persona, context(request)
            )

        @app.post("/personas", status_code=status.HTTP_201_CREATED)
        async def create_persona(
            body: _PersonaCreateBody,
            request: Request,
        ) -> PersonaView:
            return await ethos.create_persona(
                body.id, body.settings, context(request)
            )

        @app.get("/personas")
        async def list_personas(
            request: Request,
            workspace: str | None = None,
        ) -> tuple[PersonaView, ...]:
            return await ethos.list_personas(context(request), workspace)

        @app.get("/personas/{persona}")
        async def show_persona(
            persona: str,
            request: Request,
            workspace: str | None = None,
        ) -> PersonaView:
            return await ethos.show_persona(
                persona, context(request), workspace
            )

        @app.put("/personas/{persona}")
        async def configure_persona(
            persona: str,
            body: _PersonaBody,
            request: Request,
        ) -> PersonaView:
            return await ethos.configure_persona(
                persona, body.settings, context(request)
            )

        @app.delete(
            "/personas/{persona}", status_code=status.HTTP_204_NO_CONTENT
        )
        async def remove_persona(
            persona: str,
            request: Request,
        ) -> Response:
            await ethos.remove_persona(persona, context(request))
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        @app.get("/workspaces/{workspace}/persona")
        async def show_workspace_persona(
            workspace: str,
            request: Request,
        ) -> PersonaAssignmentView:
            return await ethos.show_workspace_persona(
                workspace, context(request)
            )

        @app.put("/workspaces/{workspace}/persona")
        async def assign_workspace_persona(
            workspace: str,
            body: _PersonaAssignmentBody,
            request: Request,
        ) -> PersonaAssignmentView:
            return await ethos.assign_workspace_persona(
                workspace, body.persona, context(request)
            )

        @app.get("/provider")
        async def show_provider(request: Request) -> ProviderView:
            return await ethos.show_provider(context(request))

        @app.post("/provider/check")
        async def check_provider(
            body: _ProviderBody, request: Request
        ) -> ProviderView:
            return await ethos.check_provider(body.settings, context(request))

        @app.put("/provider")
        async def configure_provider(
            body: _ProviderBody, request: Request
        ) -> ProviderView:
            return await ethos.configure_provider(
                body.settings, context(request)
            )

        @app.get("/capabilities")
        async def list_capabilities(
            request: Request,
        ) -> tuple[CapabilityView, ...]:
            return await ethos.list_capabilities(context(request))

        @app.get("/capabilities/{capability}")
        async def show_capability(
            capability: str, request: Request
        ) -> CapabilityView:
            return await ethos.show_capability(capability, context(request))

        @app.put("/capabilities/{capability}")
        async def configure_capability(
            capability: str,
            body: _CapabilityBody,
            request: Request,
        ) -> CapabilityView:
            return await ethos.configure_capability(
                capability, body.settings, context(request)
            )

        @app.get("/workspaces/{workspace}/capabilities")
        async def list_workspace_capabilities(
            workspace: str,
            request: Request,
        ) -> tuple[CapabilityView, ...]:
            return await ethos.list_capabilities(context(request), workspace)

        @app.get("/workspaces/{workspace}/capabilities/{capability}")
        async def show_workspace_capability(
            workspace: str,
            capability: str,
            request: Request,
        ) -> CapabilityView:
            return await ethos.show_capability(
                capability, context(request), workspace
            )

        @app.put("/workspaces/{workspace}/capabilities/{capability}")
        async def configure_workspace_capability(
            workspace: str,
            capability: str,
            body: _CapabilityBody,
            request: Request,
        ) -> CapabilityView:
            return await ethos.configure_capability(
                capability,
                body.settings,
                context(request),
                workspace,
            )

        @app.delete("/workspaces/{workspace}/capabilities/{capability}")
        async def reset_workspace_capability(
            workspace: str,
            capability: str,
            request: Request,
        ) -> CapabilityView:
            return await ethos.reset_capability_override(
                workspace, capability, context(request)
            )

        @app.post(
            "/workspaces/{workspace}/sessions",
            status_code=status.HTTP_201_CREATED,
        )
        async def create_session(
            workspace: str, request: Request
        ) -> SessionView:
            return await ethos.create_session(workspace, context(request))

        @app.get("/workspaces/{workspace}/sessions")
        async def list_sessions(
            workspace: str, request: Request
        ) -> tuple[SessionView, ...]:
            return await ethos.list_sessions(workspace, context(request))

        @app.get("/workspaces/{workspace}/sessions/{session_id}")
        async def show_session(
            workspace: str, session_id: str, request: Request
        ) -> SessionView:
            return await ethos.show_session(
                workspace, session_id, context(request)
            )

        @app.get("/workspaces/{workspace}/sessions/{session_id}/history")
        async def session_history(
            workspace: str, session_id: str, request: Request
        ) -> tuple[Message, ...]:
            return await ethos.session_history(
                workspace, session_id, context(request)
            )

        @app.post("/workspaces/{workspace}/sessions/{session_id}/archive")
        async def archive_session(
            workspace: str, session_id: str, request: Request
        ) -> SessionView:
            return await ethos.archive_session(
                workspace, session_id, context(request)
            )

        @app.post("/workspaces/{workspace}/sessions/{session_id}/recover")
        async def recover_session(
            workspace: str, session_id: str, request: Request
        ) -> SessionView:
            return await ethos.recover_session(
                workspace, session_id, context(request)
            )

        @app.post("/workspaces/{workspace}/sessions/{session_id}/messages")
        async def chat(
            workspace: str,
            session_id: str,
            body: _ChatBody,
            request: Request,
        ) -> StreamingResponse:
            request_context = context(request)
            return await _event_stream(
                ethos.chat(workspace, session_id, body.prompt, request_context)
            )

        @app.post(
            "/workspaces/{workspace}/sessions/{session_id}/"
            "approvals/{approval_id}/approve"
        )
        async def approve(
            workspace: str,
            session_id: str,
            approval_id: str,
            request: Request,
        ) -> StreamingResponse:
            return await _event_stream(
                ethos.resolve_approval(
                    workspace,
                    session_id,
                    approval_id,
                    True,
                    context(request),
                )
            )

        @app.post(
            "/workspaces/{workspace}/sessions/{session_id}/"
            "approvals/{approval_id}/deny"
        )
        async def deny(
            workspace: str,
            session_id: str,
            approval_id: str,
            request: Request,
        ) -> StreamingResponse:
            return await _event_stream(
                ethos.resolve_approval(
                    workspace,
                    session_id,
                    approval_id,
                    False,
                    context(request),
                )
            )

        return app

    async def run(self, ethos: Ethos) -> None:
        server = _UvicornServer(
            uvicorn.Config(
                self.create_app(ethos),
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
