"""Reusable widgets for the Ethos Textual interface."""

from collections.abc import Awaitable, Callable, Iterable

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label, Markdown, OptionList, Static, TextArea
from textual.widgets.option_list import Option

type ResourceOptions = tuple[tuple[str, str], ...]
type SessionLoader = Callable[[str], Awaitable[ResourceOptions]]


class UserMessage(Markdown):
    """Render one user-authored conversation message."""

    DEFAULT_CSS = """
    UserMessage {
        width: 1fr;
        margin: 0 0 1 8;
        padding: 1;
        padding-bottom: 0;
        background: $background;
    }

    UserMessage > MarkdownParagraph {
        margin: 0;
    }
    """

    def __init__(self, text: str) -> None:
        super().__init__(text)

    def on_mount(self) -> None:
        self._set_responsive_margin()

    def on_resize(self) -> None:
        self._set_responsive_margin()

    def _set_responsive_margin(self) -> None:
        left = 2 if self.screen.size.width < 60 else 8
        self.styles.margin = (0, 0, 1, left)


class AssistantMessage(Markdown):
    """Render one assistant message, including streamed output."""

    DEFAULT_CSS = """
    AssistantMessage {
        width: 1fr;
        margin-bottom: 1;
        padding: 0;
    }
    """

    def __init__(self, text: str = "") -> None:
        self._text = text
        super().__init__(self._content())

    async def append_chunk(self, text: str) -> None:
        self._text += text
        await self.update(self._content())

    async def finish(self, state: str | None = None) -> None:
        if state is not None:
            await self.update(self._content(state))

    def _content(self, state: str | None = None) -> str:
        text = self._text or ("_No response text._" if state else "")
        suffix = f"\n\n_{state.capitalize()}._" if state is not None else ""
        return f"{text}{suffix}"


class AppHeader(Static):
    """Show the current workspace and session."""

    DEFAULT_CSS = """
    AppHeader {
        height: 1;
        background: $background;
        color: $primary;
        content-align: center middle;
        text-style: bold;
    }
    """

    def __init__(self) -> None:
        super().__init__("Ethos", id="app-header")

    def show_context(
        self,
        workspace: str | None,
        session_id: str | None,
    ) -> None:
        context = ["Ethos"]
        if workspace is not None:
            context.append(workspace)
        if session_id is not None:
            context.append(session_id[:8])
        self.update("  ·  ".join(context))


class ConversationView(VerticalScroll):
    """Display conversation messages and update a streaming response."""

    DEFAULT_CSS = """
    ConversationView {
        height: 1fr;
        padding: 1;
        background: $surface;
        scrollbar-size-vertical: 1;
    }

    ConversationView > .empty {
        width: 1fr;
        height: 1fr;
        content-align: center middle;
        color: $text-muted;
        text-align: center;
    }

    """

    def __init__(self) -> None:
        super().__init__()
        self._active_response: AssistantMessage | None = None

    def compose(self) -> ComposeResult:
        yield self._empty(
            "Open the navigator to choose a workspace and session."
        )

    async def set_messages(self, messages: Iterable[tuple[str, str]]) -> None:
        await self.remove_children()
        self._active_response = None
        found = False
        for role, text in messages:
            found = True
            await self.mount(self._message(role, text))
        if not found:
            await self.mount(
                self._empty("Start a conversation from the prompt below.")
            )
        self._scroll_end_after_refresh()

    async def show_empty(self, message: str) -> None:
        await self.remove_children()
        self._active_response = None
        await self.mount(self._empty(message))

    @staticmethod
    def _empty(message: str) -> Static:
        return Static(
            "\n".join(
                (
                    "┌─┐┌┬┐┬ ┬┌─┐┌─┐",
                    "├┤  │ ├─┤│ │└─┐",
                    "└─┘ ┴ ┴ ┴└─┘└─┘",
                    "",
                    message,
                )
            ),
            classes="empty",
        )

    async def add_user(self, text: str) -> None:
        empty = self.query(".empty")
        if empty:
            await empty.remove()
        await self.mount(self._message("user", text))
        self._scroll_end_after_refresh()

    async def start_response(self) -> None:
        self._active_response = AssistantMessage()
        await self.mount(self._active_response)
        self._scroll_end_after_refresh()

    async def append_response(self, text: str) -> None:
        if self._active_response is None:
            return
        follow = self.is_vertical_scroll_end
        await self._active_response.append_chunk(text)
        if follow:
            self._scroll_end_after_refresh()

    async def finish_response(self, state: str | None = None) -> None:
        if self._active_response is None:
            return
        await self._active_response.finish(state)
        self._active_response = None

    def _scroll_end_after_refresh(self) -> None:
        self.call_after_refresh(
            self.scroll_end,
            animate=False,
            immediate=True,
        )

    @staticmethod
    def _message(role: str, text: str) -> Markdown:
        return UserMessage(text) if role == "user" else AssistantMessage(text)


class PromptComposer(Vertical):
    """Multiline prompt input and its send hint."""

    DEFAULT_CSS = """
    PromptComposer {
        height: 3;
        min-height: 3;
        max-height: 4;
        background: $background;
        overflow: hidden;
    }

    PromptComposer TextArea {
        height: 1fr;
        border: none !important;
        border-left: solid $panel !important;
        padding: 0 !important;
        background: $background;
        color: $text;
        scrollbar-visibility: hidden;
    }

    PromptComposer TextArea:focus {
        border-left: solid $primary !important;
    }

    PromptComposer #composer-hint {
        height: 1;
        padding: 0 1;
        color: $text-muted;
        text-align: right;
    }
    """

    def compose(self) -> ComposeResult:
        yield TextArea(
            soft_wrap=True,
            show_line_numbers=False,
            highlight_cursor_line=False,
            placeholder="Ask Ethos…",
        )
        yield Label("Ctrl+Enter send", id="composer-hint")

    @on(TextArea.Changed)
    def resize_to_content(self, event: TextArea.Changed) -> None:
        self.styles.height = (
            min(3, max(2, event.text_area.virtual_size.height)) + 1
        )

    def on_resize(self) -> None:
        self.call_after_refresh(self._resize_to_content)

    def _resize_to_content(self) -> None:
        text_area = self.query_one(TextArea)
        self.styles.height = min(3, max(2, text_area.virtual_size.height)) + 1

    @property
    def text(self) -> str:
        return self.query_one(TextArea).text

    def clear(self) -> None:
        self.query_one(TextArea).text = ""

    def set_available(self, available: bool, *, busy: bool = False) -> None:
        self.query_one(TextArea).disabled = not available
        self.query_one("#composer-hint", Label).update(
            "Esc cancel" if busy else "Ctrl+Enter send"
        )

    def focus_input(self) -> None:
        self.query_one(TextArea).focus()


class FeedbackBar(Static):
    """Show application status without replacing the conversation."""

    DEFAULT_CSS = """
    FeedbackBar {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $text-muted;
    }

    FeedbackBar.error {
        color: $error;
    }
    """

    def __init__(self) -> None:
        super().__init__("Ctrl+O browse  ·  F1 help")

    def show_ready(self, *, usage: int | None = None) -> None:
        tokens = f"  ·  {usage:,} tokens" if usage is not None else ""
        self.update(f"Ctrl+O browse  ·  F1 help{tokens}")
        self.remove_class("error")

    def show_busy(self, elapsed: float = 0) -> None:
        self.update(f"Thinking  ·  {elapsed:.1f}s")
        self.remove_class("error")

    def show_error(self, message: str) -> None:
        self.update(f"Error: {message}")
        self.add_class("error")

    def show_cancelled(self) -> None:
        self.update("Cancelled  ·  partial response may not be saved")
        self.remove_class("error")


class NavigatorModal(ModalScreen[tuple[str, str] | None]):
    """Choose a workspace and one of its sessions."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Close")]
    DEFAULT_CSS = """
    NavigatorModal {
        align: center middle;
        background: $background 70%;
    }

    NavigatorModal #navigator {
        width: 90%;
        max-width: 72;
        height: 80%;
        max-height: 24;
        padding: 1 2;
        background: $surface;
    }

    NavigatorModal #navigator-title {
        height: 2;
        color: $primary;
        text-style: bold;
    }

    NavigatorModal #navigator-lists {
        height: 1fr;
    }

    NavigatorModal .resource-pane {
        width: 1fr;
    }

    NavigatorModal .resource-title {
        height: 2;
        padding: 0 1;
        text-style: bold;
    }

    NavigatorModal OptionList {
        height: 1fr;
    }

    NavigatorModal #navigator-hint {
        padding-top: 1;
        width: 1fr;
        height: 2;
        color: $text-muted;
        text-align: center;
    }

    NavigatorModal.narrow #navigator {
        width: 95%;
        height: 90%;
        padding: 0 1;
    }

    NavigatorModal.narrow #navigator-lists {
        layout: vertical;
    }

    NavigatorModal.narrow .resource-pane {
        width: 1fr;
        height: 1fr;
    }

    NavigatorModal.narrow .resource-title {
        height: 1;
    }

    NavigatorModal.short #navigator-title {
        height: 1;
    }
    """

    def __init__(
        self,
        workspaces: ResourceOptions,
        sessions: ResourceOptions,
        load_sessions: SessionLoader,
        *,
        workspace: str,
        session_id: str | None,
    ) -> None:
        super().__init__()
        self._workspaces = workspaces
        self._sessions = sessions
        self._load_sessions = load_sessions
        self._workspace = workspace
        self._session_id = session_id

    def compose(self) -> ComposeResult:
        with Vertical(id="navigator"):
            yield Label("Browse", id="navigator-title")
            with Horizontal(id="navigator-lists"):
                with Vertical(classes="resource-pane"):
                    yield Label("Workspaces", classes="resource-title")
                    yield OptionList(id="navigator-workspaces")
                with Vertical(classes="resource-pane"):
                    yield Label("Sessions", classes="resource-title")
                    yield OptionList(id="navigator-sessions")
            yield Label(
                "Tab switch pane  ·  Enter select  ·  Esc close",
                id="navigator-hint",
            )

    def on_mount(self) -> None:
        self._apply_responsive_mode(self.size.width, self.size.height)
        self._set_options(
            self.query_one("#navigator-workspaces", OptionList),
            self._workspaces,
            selected_id=self._workspace,
        )
        self._set_options(
            self.query_one("#navigator-sessions", OptionList),
            self._sessions,
            selected_id=self._session_id,
            empty="No sessions · close, then use Ctrl+W and Ctrl+N",
        )
        self.query_one("#navigator-workspaces", OptionList).focus()

    def on_resize(self, event: events.Resize) -> None:
        self._apply_responsive_mode(event.size.width, event.size.height)

    def _apply_responsive_mode(self, width: int, height: int) -> None:
        self.set_class(width < 60, "narrow")
        self.set_class(height < 20, "short")

    @on(OptionList.OptionSelected, "#navigator-workspaces")
    async def select_workspace(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is None:
            return
        sessions = self.query_one("#navigator-sessions", OptionList)
        if event.option.id == self._workspace:
            sessions.focus()
            return
        self._workspace = event.option.id
        self._set_options(sessions, (), empty="Loading…")
        try:
            self._sessions = await self._load_sessions(self._workspace)
        except Exception as error:
            self._set_options(sessions, (), empty=str(error))
            return
        self._set_options(
            sessions,
            self._sessions,
            empty="No sessions · close, then use Ctrl+W and Ctrl+N",
        )
        sessions.focus()

    @on(OptionList.OptionSelected, "#navigator-sessions")
    def select_session(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is not None:
            self.dismiss((self._workspace, event.option.id))

    @staticmethod
    def _set_options(
        listing: OptionList,
        options: ResourceOptions,
        *,
        selected_id: str | None = None,
        empty: str = "None",
    ) -> None:
        listing.clear_options()
        selected_index: int | None = None
        for resource_id, label in options:
            if resource_id == selected_id:
                selected_index = listing.option_count
            listing.add_option(Option(label, id=resource_id))
        if listing.option_count == 0:
            listing.add_option(Option(empty, disabled=True))
        else:
            listing.highlighted = (
                selected_index if selected_index is not None else 0
            )


class ResourceModal(ModalScreen[str | None]):
    """Select one resource from a responsive single-column modal."""

    MODAL_TITLE = "Select"
    EMPTY: str = "None"
    BINDINGS = [Binding("escape", "dismiss(None)", "Close")]
    DEFAULT_CSS = """
    ResourceModal {
        align: center middle;
        background: $background 70%;
    }

    ResourceModal #resource-modal {
        width: 90%;
        max-width: 52;
        height: 70%;
        max-height: 18;
        padding: 1 2;
        background: $surface;
    }

    ResourceModal #resource-modal-title {
        height: 2;
        color: $primary;
        text-style: bold;
    }

    ResourceModal OptionList {
        height: 1fr;
        scrollbar-size-vertical: 1;
    }

    ResourceModal #resource-modal-hint {
        padding-top: 1;
        width: 1fr;
        height: 2;
        color: $text-muted;
        text-align: center;
    }

    ResourceModal.narrow #resource-modal {
        width: 95%;
        padding: 0 1;
    }

    ResourceModal.short #resource-modal {
        height: 90%;
    }

    ResourceModal.short #resource-modal-title {
        height: 1;
    }
    """

    def __init__(
        self,
        options: ResourceOptions,
        *,
        selected_id: str | None = None,
    ) -> None:
        super().__init__()
        self._options = options
        self._selected_id = selected_id

    def compose(self) -> ComposeResult:
        with Vertical(id="resource-modal"):
            yield Label(self.MODAL_TITLE, id="resource-modal-title")
            yield OptionList(id="resource-options")
            yield Label(
                "Enter select  ·  Esc close",
                id="resource-modal-hint",
            )

    def on_mount(self) -> None:
        self._apply_responsive_mode(self.size.width, self.size.height)
        listing = self.query_one("#resource-options", OptionList)
        selected_index: int | None = None
        for resource_id, label in self._options:
            if resource_id == self._selected_id:
                selected_index = listing.option_count
            listing.add_option(Option(label, id=resource_id))
        if listing.option_count == 0:
            listing.add_option(Option(self.EMPTY, disabled=True))
        else:
            listing.highlighted = (
                selected_index if selected_index is not None else 0
            )
        listing.focus()

    def on_resize(self, event: events.Resize) -> None:
        self._apply_responsive_mode(event.size.width, event.size.height)

    def _apply_responsive_mode(self, width: int, height: int) -> None:
        self.set_class(width < 60, "narrow")
        self.set_class(height < 20, "short")

    @on(OptionList.OptionSelected, "#resource-options")
    def select_resource(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is not None:
            self.dismiss(event.option.id)


class WorkspaceModal(ResourceModal):
    """Select the active workspace."""

    MODAL_TITLE = "Workspaces"
    EMPTY = "No workspaces"


class SessionModal(ResourceModal):
    """Select a session from the active workspace."""

    MODAL_TITLE = "Sessions"
    EMPTY = "No sessions · Esc, then Ctrl+N"


class ArchiveConfirmationModal(ModalScreen[bool]):
    """Confirm archival of the selected session."""

    BINDINGS = [
        Binding("y", "dismiss(True)", "Archive"),
        Binding("n", "dismiss(False)", "Cancel"),
        Binding("escape", "dismiss(False)", "Cancel"),
    ]
    DEFAULT_CSS = """
    ArchiveConfirmationModal {
        align: center middle;
        background: $background 70%;
    }

    ArchiveConfirmationModal > Static {
        width: 48;
        height: auto;
        padding: 2 3;
        background: $surface;
        text-align: center;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("Archive the selected session?\n\nY archive  ·  N cancel")


class ShortcutModal(ModalScreen[None]):
    """Show the active application shortcuts."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("f1", "dismiss", "Close"),
    ]
    DEFAULT_CSS = """
    ShortcutModal {
        align: center middle;
        background: $background 70%;
    }

    ShortcutModal > Static {
        width: auto;
        height: auto;
        padding: 2 3;
        background: $surface;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static(
            "\n".join(
                (
                    "Ethos shortcuts",
                    "",
                    "Ctrl+O      Browse workspaces and sessions",
                    "Ctrl+W      Choose workspace",
                    "Ctrl+S      Choose session",
                    "Ctrl+N      Create session",
                    "Ctrl+A      Archive selected session",
                    "Ctrl+Enter  Send prompt",
                    "Esc         Cancel response",
                    "Ctrl+Q      Quit",
                    "F1          Toggle this help",
                )
            )
        )


class AdaptiveShell(Vertical):
    """Compose the stable regions of the conversation-first layout."""

    DEFAULT_CSS = """
    AdaptiveShell {
        height: 1fr;
        background: $background;
        color: $text;
    }
    """

    def compose(self) -> ComposeResult:
        yield AppHeader()
        yield ConversationView()
        yield PromptComposer()
        yield FeedbackBar()
