import asyncio
import getpass
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from textual.widgets import Markdown, OptionList, TextArea

import ethos.gateways.tui as tui_gateway
from ethos.commands import CommandRequest, CommandResponse, CommandUsage
from ethos.gateways import TuiGateway
from ethos.tui import EthosTui
from ethos.tui.components import FeedbackBar, PromptComposer

SESSION_ID = "00000000-0000-4000-8000-000000000001"
NEW_SESSION_ID = "00000000-0000-4000-8000-000000000002"
OTHER_SESSION_ID = "00000000-0000-4000-8000-000000000003"


def session_data(
    session_id: str, *, archived: bool = False
) -> dict[str, JsonValue]:
    return {
        "id": session_id,
        "workspace": "default",
        "created_at": "2026-07-25T12:00:00+00:00",
        "archived_at": ("2026-07-25T13:00:00+00:00" if archived else None),
        "archived": archived,
        "message_count": 2,
    }


class FakeExecutor:
    def __init__(
        self,
        *,
        blocking_chat: bool = False,
        chat_error: bool = False,
    ) -> None:
        self.requests: list[CommandRequest] = []
        self.workspaces: list[dict[str, JsonValue]] = [
            {"name": "default", "path": "/tmp/default"}
        ]
        self.sessions: list[dict[str, JsonValue]] = [session_data(SESSION_ID)]
        self.other_sessions: list[dict[str, JsonValue]] = []
        self.blocking_chat = blocking_chat
        self.chat_error = chat_error
        self.chat_started = asyncio.Event()
        self.chat_closed = False

    async def __call__(
        self, request: CommandRequest
    ) -> AsyncIterator[CommandResponse]:
        self.requests.append(request)
        if request.name == "workspace.list":
            yield CommandResponse(
                data={"workspaces": cast(JsonValue, self.workspaces)}
            )
        elif request.name == "session.list":
            sessions = (
                self.other_sessions
                if request.arguments["workspace"] == "other"
                else self.sessions
            )
            yield CommandResponse(data={"sessions": cast(JsonValue, sessions)})
        elif request.name == "session.history":
            yield CommandResponse(
                data={
                    "workspace": request.arguments["workspace"],
                    "session_id": request.arguments["session_id"],
                    "messages": [
                        {"role": "user", "text": "Earlier question"},
                        {"role": "assistant", "text": "Earlier answer"},
                    ],
                }
            )
        elif request.name == "session.create":
            created = session_data(NEW_SESSION_ID)
            self.sessions.append(created)
            yield CommandResponse(data={"session": created})
        elif request.name == "session.archive":
            archived = session_data(
                str(request.arguments["session_id"]), archived=True
            )
            self.sessions = [
                archived if item["id"] == archived["id"] else item
                for item in self.sessions
            ]
            yield CommandResponse(data={"session": archived})
        elif request.name == "session.chat":
            self.chat_started.set()
            try:
                yield CommandResponse(text="Streamed ")
                if self.blocking_chat:
                    await asyncio.Event().wait()
                if self.chat_error:
                    raise ValueError("provider unavailable")
                yield CommandResponse(
                    text="answer",
                    usage=CommandUsage(input_tokens=4, output_tokens=2),
                    done=True,
                )
            finally:
                self.chat_closed = True
        else:
            raise AssertionError(f"unexpected command: {request.name}")


def tui(execute: FakeExecutor) -> EthosTui:
    return EthosTui(execute, owner_id="tester", cwd=Path("/tmp/project"))


def test_tui_loads_default_session_history_and_resizes() -> None:
    async def run() -> None:
        execute = FakeExecutor()
        app = tui(execute)

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()

            assert [request.name for request in execute.requests] == [
                "workspace.list",
                "session.list",
                "session.history",
            ]
            assert all(request.source == "tui" for request in execute.requests)
            assert all(
                request.owner_id == "tester" for request in execute.requests
            )
            assert execute.requests[0].external_context == {
                "cwd": "/tmp/project"
            }
            assert app.screen.has_class("wide")
            assert [message.source for message in app.query(Markdown)] == [
                "**You**\n\nEarlier question",
                "**Ethos**\n\nEarlier answer",
            ]
            assert (
                not app.query_one(PromptComposer).query_one(TextArea).disabled
            )

            await pilot.resize_terminal(80, 40)
            assert app.screen.has_class("compact")
            app.action_focus_prompt()
            assert app.screen.has_class("main-mode")
            app.action_focus_workspaces()
            assert not app.screen.has_class("main-mode")
            await pilot.resize_terminal(60, 40)
            assert app.screen.has_class("narrow")
            app.action_focus_workspaces()
            assert app.screen.has_class("navigation-mode")
            app.action_focus_prompt()
            assert not app.screen.has_class("navigation-mode")
            await pilot.resize_terminal(40, 12)
            assert app.is_running
            assert "terminal is very small" in str(
                app.query_one(FeedbackBar).content
            )

    asyncio.run(run())


def test_tui_creates_session_and_streams_chat() -> None:
    async def run() -> None:
        execute = FakeExecutor()
        app = tui(execute)

        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+n")
            await app.workers.wait_for_complete()

            assert any(
                request.name == "session.create" for request in execute.requests
            )

            prompt = app.query_one(TextArea)
            prompt.text = "New question"
            await pilot.press("ctrl+enter")
            await app.workers.wait_for_complete()

            chat = [
                request
                for request in execute.requests
                if request.name == "session.chat"
            ]
            assert len(chat) == 1
            assert chat[0].arguments == {
                "workspace": "default",
                "session_id": NEW_SESSION_ID,
                "prompt": "New question",
            }
            assert app.query(Markdown).last().source == (
                "**Ethos**\n\nStreamed answer"
            )
            assert "6 tokens" in str(app.query_one(FeedbackBar).content)
            assert execute.chat_closed

    asyncio.run(run())


def test_tui_switches_workspaces_and_archived_sessions() -> None:
    async def run() -> None:
        execute = FakeExecutor()
        execute.workspaces.append({"name": "other", "path": "/tmp/other"})
        execute.other_sessions = [
            {
                **session_data(OTHER_SESSION_ID, archived=True),
                "workspace": "other",
            }
        ]
        app = tui(execute)

        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            workspaces = app.query_one("#workspaces", OptionList)
            workspaces.highlighted = 1
            app.action_focus_workspaces()
            await pilot.press("enter")
            await pilot.pause()

            assert execute.requests[-1].name == "session.list"
            assert execute.requests[-1].arguments == {"workspace": "other"}

            sessions = app.query_one("#sessions", OptionList)
            sessions.highlighted = 0
            app.action_focus_sessions()
            await pilot.press("enter")
            await pilot.pause()

            assert execute.requests[-1].name == "session.history"
            assert execute.requests[-1].arguments == {
                "workspace": "other",
                "session_id": OTHER_SESSION_ID,
            }
            assert app.query_one(TextArea).disabled

    asyncio.run(run())


def test_tui_cancels_stream_and_marks_partial_response() -> None:
    async def run() -> None:
        execute = FakeExecutor(blocking_chat=True)
        app = tui(execute)

        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            app.query_one(TextArea).text = "Long request"
            await pilot.press("ctrl+enter")
            await execute.chat_started.wait()
            await pilot.pause()
            await pilot.press("ctrl+enter")
            assert (
                sum(
                    request.name == "session.chat"
                    for request in execute.requests
                )
                == 1
            )
            await pilot.press("escape")
            await app.workers.wait_for_complete()

            assert execute.chat_closed
            assert app.query(Markdown).last().source == (
                "**Ethos · _interrupted_**\n\nStreamed "
            )
            assert "partial response may not be saved" in str(
                app.query_one(FeedbackBar).content
            )
            assert not app.query_one(TextArea).disabled

    asyncio.run(run())


def test_tui_keeps_running_after_stream_error() -> None:
    async def run() -> None:
        execute = FakeExecutor(chat_error=True)
        app = tui(execute)

        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            app.query_one(TextArea).text = "Failing request"
            await pilot.press("ctrl+enter")
            await app.workers.wait_for_complete()

            assert app.query(Markdown).last().source == (
                "**Ethos · _failed_**\n\nStreamed "
            )
            assert "provider unavailable" in str(
                app.query_one(FeedbackBar).content
            )
            assert not app.query_one(TextArea).disabled
            assert app.is_running

    asyncio.run(run())


def test_tui_archives_selected_session_after_confirmation() -> None:
    async def run() -> None:
        execute = FakeExecutor()
        app = tui(execute)

        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            app.action_archive_session()
            await pilot.pause()
            await pilot.press("y")
            await app.workers.wait_for_complete()

            assert any(
                request.name == "session.archive"
                for request in execute.requests
            )
            assert app.query_one(TextArea).disabled

    asyncio.run(run())


def test_tui_rejects_empty_prompt_without_dispatching() -> None:
    async def run() -> None:
        execute = FakeExecutor()
        app = tui(execute)

        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            requests_before = len(execute.requests)
            app.query_one(TextArea).text = "   "
            await pilot.press("ctrl+enter")

            assert len(execute.requests) == requests_before
            assert "prompt must not be empty" in str(
                app.query_one(FeedbackBar).content
            )

    asyncio.run(run())


def test_tui_empty_workspace_and_session_states() -> None:
    async def run() -> None:
        empty = FakeExecutor()
        empty.workspaces = []
        empty.sessions = []
        empty_app = tui(empty)

        async with empty_app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            assert [request.name for request in empty.requests] == [
                "workspace.list"
            ]
            assert empty_app.query_one(TextArea).disabled

        execute = FakeExecutor()
        execute.sessions = []
        app = tui(execute)

        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()

            assert [request.name for request in execute.requests] == [
                "workspace.list",
                "session.list",
            ]
            assert app.query_one(TextArea).disabled

            await pilot.press("ctrl+n")
            await app.workers.wait_for_complete()

            assert any(
                request.name == "session.create" for request in execute.requests
            )
            assert not app.query_one(TextArea).disabled

    asyncio.run(run())


def test_tui_gateway_runs_app_with_local_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeApp:
        def __init__(
            self,
            execute: object,
            *,
            owner_id: str,
            cwd: Path,
        ) -> None:
            captured.update(
                execute=execute,
                owner_id=owner_id,
                cwd=cwd,
            )

        async def run_async(self) -> None:
            captured["ran"] = True

    monkeypatch.setattr(tui_gateway, "EthosTui", FakeApp)

    execute = FakeExecutor()
    asyncio.run(TuiGateway().run(execute))

    assert TuiGateway().name == "tui"
    assert captured == {
        "execute": execute,
        "owner_id": getpass.getuser(),
        "cwd": Path.cwd(),
        "ran": True,
    }
