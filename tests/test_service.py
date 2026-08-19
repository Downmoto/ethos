import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest
from pydantic_ai.messages import ModelRequest, UserPromptPart

from ethos.events.types import EventType
from ethos.home import initialise_home
from ethos.runtime import AgentRuntime, PromptStreamEvent
from ethos.service import Ethos, RequestContext
from ethos.sessions import Session


def context() -> RequestContext:
    return RequestContext("test", "owner", {"client": "pytest"})


def test_service_shares_workspace_and_session_behaviour(tmp_path: Path) -> None:
    home = initialise_home(tmp_path / ".ethos")

    async def exercise() -> None:
        with Ethos(home) as ethos:
            workspace = await ethos.create_workspace("health", context())
            assert workspace.name == "health"
            assert [
                item.name for item in await ethos.list_workspaces(context())
            ] == [
                "default",
                "health",
            ]

            session = await ethos.create_session("health", context())
            assert (
                await ethos.show_session("health", session.id, context())
            ).id == session.id
            archived = await ethos.archive_session(
                "health", session.id, context()
            )
            assert archived.archived

    asyncio.run(exercise())


def test_service_emits_chat_event_for_incomplete_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = initialise_home(tmp_path / ".ethos")

    async def exercise() -> None:
        with Ethos(home) as ethos:
            session = await ethos.create_session("default", context())

            class FakeRuntime:
                async def run(
                    self, prompt: str, workspace: str, session_id: str
                ) -> AsyncIterator[PromptStreamEvent]:
                    assert (prompt, workspace, session_id) == (
                        "hello",
                        "default",
                        session.id,
                    )
                    yield PromptStreamEvent(text="reply")

            emitted: list[EventType] = []

            async def record_event(
                _context: RequestContext,
                event_type: EventType,
                _sessions: tuple[Session, ...],
            ) -> None:
                emitted.append(event_type)

            ethos._agent = cast(AgentRuntime, FakeRuntime())
            monkeypatch.setattr(ethos, "_emit_sessions", record_event)
            chunks = [
                chunk
                async for chunk in ethos.chat(
                    "default", session.id, "hello", context()
                )
            ]

            assert [chunk.text for chunk in chunks] == ["reply"]
            assert chunks[0].workspace == "default"
            assert chunks[0].session_id == session.id
            assert emitted == [EventType.SESSION_CHAT]

    asyncio.run(exercise())


def test_service_emits_chat_event_after_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = initialise_home(tmp_path / ".ethos")

    async def exercise() -> None:
        with Ethos(home) as ethos:
            session = await ethos.create_session("default", context())
            observed_message_counts: list[int] = []

            class FakeRuntime:
                async def run(
                    self, prompt: str, workspace: str, session_id: str
                ) -> AsyncIterator[PromptStreamEvent]:
                    ethos.sessions.replace_messages(
                        workspace,
                        session_id,
                        (ModelRequest(parts=[UserPromptPart(content=prompt)]),),
                    )
                    yield PromptStreamEvent(done=True)

            async def record_event(
                _context: RequestContext,
                event_type: EventType,
                sessions: tuple[Session, ...],
            ) -> None:
                assert event_type is EventType.SESSION_CHAT
                observed_message_counts.append(len(sessions[0].messages))

            ethos._agent = cast(AgentRuntime, FakeRuntime())
            monkeypatch.setattr(ethos, "_emit_sessions", record_event)

            chunks = [
                chunk
                async for chunk in ethos.chat(
                    "default", session.id, "hello", context()
                )
            ]

            assert chunks[-1].done
            assert observed_message_counts == [1]

    asyncio.run(exercise())
