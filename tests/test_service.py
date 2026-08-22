import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest

from ethos.events.types import EventType
from ethos.home import initialise_home
from ethos.models import (
    Message,
    ReasoningPart,
    Role,
    TextPart,
    ToolCallPart,
    Usage,
)
from ethos.runtime import AgentRuntime, ApprovalStreamEvent, PromptStreamEvent
from ethos.service import (
    ApprovalChunk,
    ChatChunk,
    Ethos,
    HistoryMessage,
    RequestContext,
)
from ethos.sessions import Session
from ethos.tools import ToolApproval, ToolEffect


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


def test_service_projects_ethos_messages_into_history(tmp_path: Path) -> None:
    home = initialise_home(tmp_path / ".ethos")

    async def exercise() -> None:
        with Ethos(home) as ethos:
            session = await ethos.create_session("default", context())
            ethos.sessions.replace_messages(
                "default",
                session.id,
                (
                    Message(
                        role=Role.USER,
                        parts=(
                            TextPart(text="first"),
                            TextPart(text="second"),
                        ),
                    ),
                    Message(
                        role=Role.ASSISTANT,
                        parts=(
                            ReasoningPart(text="thinking"),
                            TextPart(text="answer"),
                        ),
                    ),
                ),
            )

            history = await ethos.session_history(
                "default", session.id, context()
            )

            assert history == (
                HistoryMessage(role="user", text="first\nsecond"),
                HistoryMessage(
                    role="assistant",
                    text="answer",
                    reasoning="thinking",
                ),
            )

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

            assert [
                chunk.text for chunk in chunks if isinstance(chunk, ChatChunk)
            ] == ["reply"]
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
                        (
                            Message(
                                role=Role.USER,
                                parts=(TextPart(text=prompt),),
                            ),
                        ),
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

            assert isinstance(chunks[-1], ChatChunk)
            assert chunks[-1].done
            assert observed_message_counts == [1]

    asyncio.run(exercise())


def test_service_projects_and_resolves_approval_events(tmp_path: Path) -> None:
    home = initialise_home(tmp_path / ".ethos")

    async def exercise() -> None:
        with Ethos(home) as ethos:
            session = await ethos.create_session("default", context())
            approval = ToolApproval(
                id="approval-1",
                call=ToolCallPart(
                    call_id="call-1",
                    name="write_file",
                    arguments_json='{"path":"README.md"}',
                ),
                tool_name="write_file",
                arguments={"path": "README.md"},
                effect=ToolEffect.WRITE,
                reason="write tool requires approval",
                round_number=1,
                usage=Usage(input_tokens=2, output_tokens=1),
            )

            class FakeRuntime:
                async def run(
                    self, prompt: str, workspace: str, session_id: str
                ) -> AsyncIterator[ApprovalStreamEvent]:
                    del prompt, workspace, session_id
                    yield ApprovalStreamEvent(approval)

                async def resolve_approval(
                    self,
                    workspace: str,
                    session_id: str,
                    approval_id: str,
                    *,
                    approved: bool,
                ) -> AsyncIterator[PromptStreamEvent]:
                    assert (workspace, session_id, approval_id, approved) == (
                        "default",
                        session.id,
                        "approval-1",
                        True,
                    )
                    yield PromptStreamEvent(usage=Usage(), done=True)

            ethos._agent = cast(AgentRuntime, FakeRuntime())

            requested = [
                event
                async for event in ethos.chat(
                    "default", session.id, "hello", context()
                )
            ]
            resumed = [
                event
                async for event in ethos.resolve_approval(
                    "default",
                    session.id,
                    "approval-1",
                    True,
                    context(),
                )
            ]

            assert len(requested) == 1
            event = requested[0]
            assert isinstance(event, ApprovalChunk)
            assert event.approval_id == "approval-1"
            assert event.tool_name == "write_file"
            assert event.arguments == {"path": "README.md"}
            assert isinstance(resumed[-1], ChatChunk)
            assert resumed[-1].done

    asyncio.run(exercise())
