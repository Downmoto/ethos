"""Textual application backed only by transport-neutral Ethos commands."""

from __future__ import annotations

import asyncio
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Final, Literal

from pydantic import BaseModel, ConfigDict, TypeAdapter
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.worker import Worker

from ethos.commands import CommandRequest, CommandResponse
from ethos.tui.components import (
    AdaptiveShell,
    AppHeader,
    ConversationView,
    FeedbackBar,
    PromptComposer,
    ResourceList,
)

if TYPE_CHECKING:
    from ethos.gateways.base import CommandExecutor

_WIDE_MIN: Final = 100
_COMPACT_MIN: Final = 70


class _Workspace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    path: str


class _Session(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    workspace: str
    created_at: str
    archived_at: str | None
    archived: bool
    message_count: int


class _HistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    text: str


_WORKSPACES = TypeAdapter(list[_Workspace])
_SESSIONS = TypeAdapter(list[_Session])
_HISTORY = TypeAdapter(list[_HistoryMessage])


class _ShortcutHelp(ModalScreen[None]):
    BINDINGS = [Binding("escape", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        yield Static(
            "\n".join(
                (
                    "Ethos shortcuts",
                    "",
                    "Ctrl+Enter  Send prompt",
                    "Ctrl+N      New session",
                    "A           Archive selected session",
                    "Ctrl+W      Workspaces",
                    "Ctrl+S      Sessions",
                    "Ctrl+L      Conversation",
                    "Ctrl+P      Prompt",
                    "Esc         Cancel response",
                    "Ctrl+Q      Quit",
                    "",
                    "Esc         Close help",
                )
            ),
            id="shortcut-help",
        )


class _ArchiveConfirmation(ModalScreen[bool]):
    BINDINGS = [
        Binding("y", "confirm", "Archive"),
        Binding("n", "cancel", "Cancel"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def compose(self) -> ComposeResult:
        yield Static(
            "Archive the selected session?\n\nY archive  ·  N cancel",
            id="archive-confirmation",
        )

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class EthosTui(App[None]):
    """Coordinate reusable widgets through the Ethos command executor."""

    CSS_PATH = "ethos.tcss"
    BINDINGS = [
        Binding("ctrl+enter", "send", "Send", show=False, priority=True),
        Binding("ctrl+n", "new_session", "New session", show=False),
        Binding("a", "archive_session", "Archive session", show=False),
        Binding("ctrl+w", "focus_workspaces", "Workspaces", show=False),
        Binding("ctrl+s", "focus_sessions", "Sessions", show=False),
        Binding("ctrl+l", "focus_conversation", "Conversation", show=False),
        Binding("ctrl+p", "focus_prompt", "Prompt", show=False),
        Binding("escape", "cancel_response", "Cancel", show=False),
        Binding("question_mark", "help", "Help", show=False),
        Binding("ctrl+q", "quit", "Quit", show=False),
    ]

    def __init__(
        self,
        execute: CommandExecutor,
        *,
        owner_id: str,
        cwd: Path,
    ) -> None:
        super().__init__()
        self._execute = execute
        self._owner_id = owner_id
        self._cwd = cwd
        self._workspaces: list[_Workspace] = []
        self._sessions: list[_Session] = []
        self._workspace: str | None = None
        self._session: _Session | None = None
        self._chat_worker: Worker[None] | None = None
        self._started_at = 0.0
        self._usage: int | None = None
        self._too_small = False

    def compose(self) -> ComposeResult:
        yield AdaptiveShell(
            AppHeader(),
            ResourceList(id="workspaces"),
            ResourceList(id="sessions"),
            ConversationView(),
            PromptComposer(),
            FeedbackBar(),
        )

    async def on_mount(self) -> None:
        self._apply_responsive_mode(self.size.width, self.size.height)
        self._set_composer()
        await self._load_workspaces()
        if self._session is not None and not self._session.archived:
            self.action_focus_prompt()

    def on_resize(self, event: events.Resize) -> None:
        self._apply_responsive_mode(event.size.width, event.size.height)

    def _request(
        self, name: str, arguments: dict[str, object]
    ) -> CommandRequest:
        return CommandRequest.model_validate(
            {
                "name": name,
                "arguments": arguments,
                "source": "tui",
                "owner_id": self._owner_id,
                "external_context": {"cwd": str(self._cwd)},
            }
        )

    async def _execute_one(
        self, name: str, arguments: dict[str, object]
    ) -> CommandResponse:
        responses = [
            response
            async for response in self._execute(self._request(name, arguments))
        ]
        if len(responses) != 1:
            raise RuntimeError(f"{name} returned {len(responses)} responses")
        return responses[0]

    async def _load_workspaces(self) -> None:
        listing = self.query_one("#workspaces", ResourceList)
        listing.show_loading()
        try:
            response = await self._execute_one("workspace.list", {})
            self._workspaces = _WORKSPACES.validate_python(
                response.data.get("workspaces", [])
            )
        except Exception as error:
            listing.show_error(str(error))
            self._show_error(error)
            return
        names = [workspace.name for workspace in self._workspaces]
        selected = "default" if "default" in names else next(iter(names), None)
        self._workspace = selected
        listing.set_resources(
            ((item.name, item.name, False) for item in self._workspaces),
            selected_id=selected,
            empty="No workspaces",
        )
        self._update_header()
        if selected is not None:
            await self._load_sessions(selected)

    async def _load_sessions(
        self, workspace: str, *, selected_id: str | None = None
    ) -> None:
        listing = self.query_one("#sessions", ResourceList)
        listing.show_loading()
        self._session = None
        self._set_composer()
        try:
            response = await self._execute_one(
                "session.list", {"workspace": workspace}
            )
            self._sessions = _SESSIONS.validate_python(
                response.data.get("sessions", [])
            )
        except Exception as error:
            listing.show_error(str(error))
            self._show_error(error)
            return
        if selected_id is None:
            active = [
                session for session in self._sessions if not session.archived
            ]
            selected_id = active[-1].id if active else None
        self._session = next(
            (
                session
                for session in self._sessions
                if session.id == selected_id
            ),
            None,
        )
        listing.set_resources(
            (
                (
                    item.id,
                    (
                        f"{item.id[:8]}  "
                        f"{'archived' if item.archived else 'active'}"
                    ),
                    False,
                )
                for item in reversed(self._sessions)
            ),
            selected_id=selected_id,
            empty="No sessions · Ctrl+N to create",
        )
        self._update_header()
        self._set_composer()
        if self._session is None:
            await self.query_one(ConversationView).set_messages(())
        else:
            await self._load_history(self._session)

    async def _load_history(self, session: _Session) -> None:
        try:
            response = await self._execute_one(
                "session.history",
                {
                    "workspace": session.workspace,
                    "session_id": session.id,
                },
            )
            messages = _HISTORY.validate_python(
                response.data.get("messages", [])
            )
        except Exception as error:
            self._show_error(error)
            return
        await self.query_one(ConversationView).set_messages(
            (message.role, message.text) for message in messages
        )
        self.query_one(FeedbackBar).show_ready()

    @on(OptionList.OptionSelected, "#workspaces")
    async def select_workspace(self, event: OptionList.OptionSelected) -> None:
        if self._chat_worker is not None:
            self._show_error(ValueError("cancel the active response first"))
            return
        workspace = event.option.id
        if workspace is None or workspace == self._workspace:
            return
        self._workspace = workspace
        self._session = None
        self._update_header()
        await self._load_sessions(workspace)
        self._show_main()

    @on(OptionList.OptionSelected, "#sessions")
    async def select_session(self, event: OptionList.OptionSelected) -> None:
        if self._chat_worker is not None:
            self._show_error(ValueError("cancel the active response first"))
            return
        session_id = event.option.id
        session = next(
            (item for item in self._sessions if item.id == session_id),
            None,
        )
        if session is None:
            return
        self._session = session
        self._update_header()
        self._set_composer()
        await self._load_history(session)
        self._show_main()

    def action_new_session(self) -> None:
        if self._workspace is None or self._chat_worker is not None:
            return
        self.run_worker(self._create_session(), exclusive=True, group="session")

    def action_archive_session(self) -> None:
        if (
            self._session is None
            or self._session.archived
            or self._chat_worker is not None
        ):
            return
        self.run_worker(
            self._confirm_archive(),
            exclusive=True,
            group="session",
        )

    async def _confirm_archive(self) -> None:
        if not await self.push_screen_wait(_ArchiveConfirmation()):
            return
        session = self._session
        if session is None:
            return
        try:
            await self._execute_one(
                "session.archive",
                {
                    "workspace": session.workspace,
                    "session_id": session.id,
                },
            )
            await self._load_sessions(
                session.workspace,
                selected_id=session.id,
            )
        except Exception as error:
            self._show_error(error)

    async def _create_session(self) -> None:
        if self._workspace is None:
            return
        try:
            response = await self._execute_one(
                "session.create", {"workspace": self._workspace}
            )
            session = _Session.model_validate(response.data.get("session"))
            await self._load_sessions(self._workspace, selected_id=session.id)
            self.action_focus_prompt()
        except Exception as error:
            self._show_error(error)

    def action_send(self) -> None:
        if self._chat_worker is not None:
            return
        session = self._session
        composer = self.query_one(PromptComposer)
        prompt = composer.text.strip()
        if session is None:
            self._show_error(ValueError("select or create a session"))
            return
        if session.archived:
            self._show_error(ValueError("selected session is archived"))
            return
        if not prompt:
            self._show_error(ValueError("prompt must not be empty"))
            return
        composer.clear()
        self._chat_worker = self.run_worker(
            self._chat(session, prompt),
            exclusive=True,
            group="chat",
            exit_on_error=False,
        )

    async def _chat(self, session: _Session, prompt: str) -> None:
        conversation = self.query_one(ConversationView)
        await conversation.add_user(prompt)
        await conversation.start_response()
        self._started_at = monotonic()
        self._usage = None
        self._set_busy(True)
        try:
            request = self._request(
                "session.chat",
                {
                    "workspace": session.workspace,
                    "session_id": session.id,
                    "prompt": prompt,
                },
            )
            async for response in self._execute(request):
                if response.text:
                    conversation.append_response(response.text)
                if response.usage is not None:
                    self._usage = response.usage.total_tokens
                if response.done:
                    conversation.finish_response()
            conversation.finish_response()
            self.query_one(FeedbackBar).show_ready(usage=self._usage)
        except asyncio.CancelledError:
            conversation.finish_response("interrupted")
            self.query_one(FeedbackBar).show_cancelled()
            raise
        except Exception as error:
            conversation.finish_response("failed")
            self._show_error(error)
        finally:
            self._chat_worker = None
            self._set_busy(False)
            if self._session is not None and not self._session.archived:
                self.query_one(PromptComposer).focus_input()

    def action_cancel_response(self) -> None:
        if self._chat_worker is not None:
            self._chat_worker.cancel()

    def action_focus_workspaces(self) -> None:
        self._show_navigation()
        self.query_one("#workspaces", ResourceList).focus()

    def action_focus_sessions(self) -> None:
        self._show_navigation()
        self.query_one("#sessions", ResourceList).focus()

    def action_focus_conversation(self) -> None:
        self._show_main()
        self.query_one(ConversationView).focus()

    def action_focus_prompt(self) -> None:
        self._show_main()
        self.query_one(PromptComposer).focus_input()

    def action_help(self) -> None:
        self.push_screen(_ShortcutHelp())

    def _set_busy(self, busy: bool) -> None:
        self.query_one(PromptComposer).set_available(
            self._session is not None
            and not self._session.archived
            and not busy,
            busy=busy,
        )
        self._update_header()
        if busy:
            self._update_elapsed()

    def _update_elapsed(self) -> None:
        if self._chat_worker is None:
            return
        self.query_one(FeedbackBar).show_busy(monotonic() - self._started_at)
        self.set_timer(0.1, self._update_elapsed)

    def _set_composer(self) -> None:
        session = self._session
        self.query_one(PromptComposer).set_available(
            session is not None and not session.archived
        )

    def _update_header(self) -> None:
        self.query_one(AppHeader).show_context(
            self._workspace,
            self._session.id if self._session is not None else None,
            busy=self._chat_worker is not None,
        )

    def _show_error(self, error: Exception) -> None:
        self.query_one(FeedbackBar).show_error(str(error))

    def _apply_responsive_mode(self, width: int, height: int) -> None:
        self.screen.remove_class(
            "wide",
            "compact",
            "narrow",
            "main-mode",
            "navigation-mode",
        )
        if width >= _WIDE_MIN:
            self.screen.add_class("wide")
        elif width >= _COMPACT_MIN:
            self.screen.add_class("compact")
        else:
            self.screen.add_class("narrow")
        too_small = width < 50 or height < 16
        if too_small == self._too_small:
            return
        feedbacks = list(self.query(FeedbackBar))
        if not feedbacks:
            return
        self._too_small = too_small
        feedback = feedbacks[0]
        if too_small:
            feedback.show_error(
                "terminal is very small; resize for full access"
            )
        elif self._chat_worker is None:
            feedback.show_ready(usage=self._usage)

    def _show_navigation(self) -> None:
        if self.screen.has_class("compact"):
            self.screen.remove_class("main-mode")
        elif self.screen.has_class("narrow"):
            self.screen.add_class("navigation-mode")

    def _show_main(self) -> None:
        self.screen.remove_class("navigation-mode")
        if self.screen.has_class("compact"):
            self.screen.add_class("main-mode")
