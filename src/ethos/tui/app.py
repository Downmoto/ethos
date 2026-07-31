"""Textual application backed only by transport-neutral Ethos commands."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, TypeAdapter
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.theme import Theme
from textual.worker import Worker

from ethos.commands import CommandRequest, CommandResponse
from ethos.tui.components import (
    AdaptiveShell,
    AppHeader,
    ArchiveConfirmationModal,
    ConversationView,
    FeedbackBar,
    NavigatorModal,
    PromptComposer,
    ResourceOptions,
    SessionModal,
    ShortcutModal,
    WorkspaceModal,
)

if TYPE_CHECKING:
    from ethos.gateways.base import CommandExecutor


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

ETHOS_THEME = Theme(
    name="ethos",
    primary="#88c0d0",
    secondary="#81a1c1",
    accent="#b48ead",
    foreground="#d8dee9",
    background="#2e3440",
    success="#a3be8c",
    warning="#ebcb8b",
    error="#bf616a",
    surface="#3b4252",
    panel="#434c5e",
)


class EthosTui(App[None]):
    """Run the conversation-first interface for the local TUI gateway."""

    ENABLE_COMMAND_PALETTE = False
    DEFAULT_CSS = """
    Screen.narrow ConversationView {
        padding: 0 1;
    }
    """
    BINDINGS = [
        Binding("ctrl+o", "browse", "Browse", show=False, priority=True),
        Binding(
            "ctrl+w", "workspaces", "Workspaces", show=False, priority=True
        ),
        Binding("ctrl+s", "sessions", "Sessions", show=False, priority=True),
        Binding("ctrl+enter", "send", "Send", show=False, priority=True),
        Binding(
            "ctrl+n", "new_session", "New session", show=False, priority=True
        ),
        Binding(
            "ctrl+a",
            "archive_session",
            "Archive session",
            show=False,
            priority=True,
        ),
        Binding("escape", "cancel_response", "Cancel", show=False),
        Binding("f1", "help", "Help", show=False, priority=True),
        Binding("ctrl+q", "quit", "Quit", show=False, priority=True),
    ]

    def __init__(
        self,
        execute: CommandExecutor,
        *,
        owner_id: str,
        cwd: Path,
    ) -> None:
        super().__init__()
        self.register_theme(ETHOS_THEME)
        self.theme = ETHOS_THEME.name
        self._execute = execute
        self._owner_id = owner_id
        self._cwd = cwd
        self._workspaces: list[_Workspace] = []
        self._sessions: list[_Session] = []
        self._workspace: str | None = None
        self._session: _Session | None = None
        self._chat_worker: Worker[None] | None = None
        self._state_worker: Worker[None] | None = None
        self._started_at = 0.0
        self._usage: int | None = None

    def compose(self) -> ComposeResult:
        yield AdaptiveShell()

    def on_mount(self) -> None:
        self._apply_responsive_mode(self.size.width)
        self.query_one(PromptComposer).set_available(False)

    def on_resize(self, event: events.Resize) -> None:
        self._apply_responsive_mode(event.size.width)

    def _apply_responsive_mode(self, width: int) -> None:
        screen = self.screen_stack[0]
        screen.set_class(width < 60, "narrow")

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

    def action_browse(self) -> None:
        if self._state_change_blocked():
            return
        self._run_state_worker(self._browse())

    def action_workspaces(self) -> None:
        if self._state_change_blocked():
            return
        self._run_state_worker(self._choose_workspace())

    async def _choose_workspace(self) -> None:
        try:
            response = await self._execute_one("workspace.list", {})
            self._workspaces = _WORKSPACES.validate_python(
                response.data.get("workspaces", [])
            )
            workspace = await self.push_screen_wait(
                WorkspaceModal(
                    tuple((item.name, item.name) for item in self._workspaces),
                    selected_id=self._workspace,
                )
            )
            if workspace is None or workspace == self._workspace:
                return
            self._workspace = workspace
            self._session = None
            self._sessions = []
            self._update_context()
            await self.query_one(ConversationView).show_empty(
                "Choose a session with Ctrl+S."
            )
            self.query_one(PromptComposer).set_available(False)
            self.query_one(FeedbackBar).show_ready()
        except Exception as error:
            self._show_error(str(error))

    def action_sessions(self) -> None:
        if self._state_change_blocked():
            return
        if self._workspace is None:
            self._show_error("choose a workspace with Ctrl+W")
            return
        self._run_state_worker(self._choose_session())

    async def _choose_session(self) -> None:
        workspace = self._workspace
        if workspace is None:
            return
        try:
            sessions = await self._load_sessions(workspace)
            session_id = await self.push_screen_wait(
                SessionModal(
                    self._session_options(sessions),
                    selected_id=(
                        self._session.id if self._session is not None else None
                    ),
                )
            )
            if session_id is None:
                return
            session = next(
                (item for item in sessions if item.id == session_id),
                None,
            )
            if session is None:
                raise RuntimeError("selected session is no longer available")
            await self._load_history(session)
            self._sessions = sessions
            self._session = session
            self._update_context()
            self.query_one(PromptComposer).set_available(not session.archived)
            if not session.archived:
                self.query_one(PromptComposer).focus_input()
        except Exception as error:
            self._show_error(str(error))

    async def _browse(self) -> None:
        try:
            response = await self._execute_one("workspace.list", {})
            self._workspaces = _WORKSPACES.validate_python(
                response.data.get("workspaces", [])
            )
            names = [workspace.name for workspace in self._workspaces]
            workspace = (
                self._workspace
                if self._workspace in names
                else "default"
                if "default" in names
                else next(iter(names), None)
            )
            if workspace is None:
                self._show_error("no workspaces available")
                return
            session_lists = {workspace: await self._load_sessions(workspace)}

            async def load_options(selected_workspace: str) -> ResourceOptions:
                selected_sessions = await self._load_sessions(
                    selected_workspace
                )
                session_lists[selected_workspace] = selected_sessions
                return self._session_options(selected_sessions)

            selection = await self.push_screen_wait(
                NavigatorModal(
                    tuple((item.name, item.name) for item in self._workspaces),
                    self._session_options(session_lists[workspace]),
                    load_options,
                    workspace=workspace,
                    session_id=(
                        self._session.id if self._session is not None else None
                    ),
                )
            )
            if selection is None:
                return
            workspace, session_id = selection
            sessions = session_lists.get(workspace)
            if sessions is None:
                sessions = await self._load_sessions(workspace)
            session = next(
                (item for item in sessions if item.id == session_id),
                None,
            )
            if session is None:
                raise RuntimeError("selected session is no longer available")
            await self._load_history(session)
            self._workspace = workspace
            self._sessions = sessions
            self._session = session
            self._update_context()
            self.query_one(PromptComposer).set_available(not session.archived)
            if not session.archived:
                self.query_one(PromptComposer).focus_input()
        except Exception as error:
            self._show_error(str(error))

    async def _load_sessions(self, workspace: str) -> list[_Session]:
        response = await self._execute_one(
            "session.list", {"workspace": workspace}
        )
        return _SESSIONS.validate_python(response.data.get("sessions", []))

    @staticmethod
    def _session_options(sessions: list[_Session]) -> ResourceOptions:
        return tuple(
            (
                session.id,
                f"{session.id[:8]}  "
                f"{'archived' if session.archived else 'active'}",
            )
            for session in reversed(sessions)
        )

    async def _load_history(self, session: _Session) -> None:
        response = await self._execute_one(
            "session.history",
            {
                "workspace": session.workspace,
                "session_id": session.id,
            },
        )
        messages = _HISTORY.validate_python(response.data.get("messages", []))
        await self.query_one(ConversationView).set_messages(
            (message.role, message.text) for message in messages
        )
        self.query_one(FeedbackBar).show_ready()

    def action_new_session(self) -> None:
        if self._state_change_blocked():
            return
        if self._workspace is None:
            self._show_error("choose a workspace with Ctrl+W")
            return
        self._run_state_worker(self._create_session())

    async def _create_session(self) -> None:
        workspace = self._workspace
        if workspace is None:
            return
        try:
            response = await self._execute_one(
                "session.create", {"workspace": workspace}
            )
            session = _Session.model_validate(response.data.get("session"))
            self._sessions.append(session)
            self._session = session
            self._update_context()
            await self.query_one(ConversationView).set_messages(())
            self.query_one(PromptComposer).set_available(True)
            self.query_one(PromptComposer).focus_input()
            self.query_one(FeedbackBar).show_ready()
        except Exception as error:
            self._show_error(str(error))

    def action_archive_session(self) -> None:
        if self._state_change_blocked():
            return
        if self._session is None or self._session.archived:
            return
        self._run_state_worker(self._confirm_archive())

    async def _confirm_archive(self) -> None:
        if not await self.push_screen_wait(ArchiveConfirmationModal()):
            return
        session = self._session
        if session is None:
            return
        try:
            response = await self._execute_one(
                "session.archive",
                {
                    "workspace": session.workspace,
                    "session_id": session.id,
                },
            )
            archived = _Session.model_validate(response.data.get("session"))
            self._sessions = [
                archived if item.id == archived.id else item
                for item in self._sessions
            ]
            self._session = archived
            self._update_context()
            self.query_one(PromptComposer).set_available(False)
            self.query_one(FeedbackBar).show_ready()
        except Exception as error:
            self._show_error(str(error))

    def action_send(self) -> None:
        if self._state_worker is not None:
            self._show_error("wait for the active session operation")
            return
        if self._chat_worker is not None:
            return
        session = self._session
        composer = self.query_one(PromptComposer)
        prompt = composer.text.strip()
        if session is None:
            self._show_error("choose a session with Ctrl+O")
            return
        if session.archived:
            self._show_error("selected session is archived")
            return
        if not prompt:
            self._show_error("prompt must not be empty")
            return
        composer.clear()
        self._chat_worker = self.run_worker(
            self._chat(session, prompt),
            exclusive=True,
            group="chat",
            exit_on_error=False,
        )

    # we have token tracker and think status classes defined (as private) in
    # src/ethos/app.py, maybe we can repurpose them in multiple locations,
    # including this gateway
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
                    await conversation.append_response(response.text)
                if response.usage is not None:
                    self._usage = response.usage.total_tokens
                if response.done:
                    await conversation.finish_response()
            await conversation.finish_response()
            self.query_one(FeedbackBar).show_ready(usage=self._usage)
        except asyncio.CancelledError:
            await conversation.finish_response("interrupted")
            self.query_one(FeedbackBar).show_cancelled()
            raise
        except Exception as error:
            await conversation.finish_response("failed")
            self._show_error(str(error))
        finally:
            self._chat_worker = None
            self._set_busy(False)
            if self._session is not None and not self._session.archived:
                self.query_one(PromptComposer).focus_input()

    def action_cancel_response(self) -> None:
        if self._chat_worker is not None:
            self._chat_worker.cancel()

    def action_help(self) -> None:
        if isinstance(self.screen, ShortcutModal):
            self.screen.dismiss()
        elif self._state_worker is None and len(self.screen_stack) == 1:
            self.push_screen(ShortcutModal())

    def _state_change_blocked(self) -> bool:
        if len(self.screen_stack) > 1:
            return True
        if self._chat_worker is not None:
            self._show_error("cancel the active response first")
            return True
        if self._state_worker is not None:
            self._show_error("wait for the active session operation")
            return True
        return False

    def _run_state_worker(self, operation: Awaitable[None]) -> None:
        self._state_worker = self.run_worker(
            self._run_state_operation(operation),
            group="state",
            exit_on_error=False,
        )

    async def _run_state_operation(self, operation: Awaitable[None]) -> None:
        try:
            await operation
        finally:
            self._state_worker = None

    def _set_busy(self, busy: bool) -> None:
        session = self._session
        self.query_one(PromptComposer).set_available(
            session is not None and not session.archived and not busy,
            busy=busy,
        )
        self._update_context()
        if busy:
            self._update_elapsed()

    def _update_elapsed(self) -> None:
        if self._chat_worker is None:
            return
        self.query_one(FeedbackBar).show_busy(monotonic() - self._started_at)
        self.set_timer(0.1, self._update_elapsed)

    def _update_context(self) -> None:
        self.query_one(AppHeader).show_context(
            self._workspace,
            self._session.id if self._session is not None else None,
        )

    def _show_error(self, message: str) -> None:
        self.query_one(FeedbackBar).show_error(message)
