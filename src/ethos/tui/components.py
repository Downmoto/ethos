"""Small reusable widgets shared by Ethos Textual screens."""

from collections.abc import Iterable

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Label, Markdown, OptionList, Static, TextArea
from textual.widgets.option_list import Option


class AppHeader(Static):
    """Show the active screen and selection context."""

    def __init__(self) -> None:
        super().__init__("Ethos", id="app-header")

    def show_context(
        self,
        workspace: str | None,
        session_id: str | None,
        *,
        busy: bool = False,
    ) -> None:
        context = ["Ethos"]
        if workspace is not None:
            context.append(workspace)
        if session_id is not None:
            context.append(session_id[:8])
        if busy:
            context.append("thinking")
        self.update("  ·  ".join(context))


class ResourceList(OptionList):
    """Render selectable resources with consistent empty and loading states."""

    def __init__(self, *, id: str) -> None:
        super().__init__(id=id)

    def show_loading(self) -> None:
        self.clear_options()
        self.add_option(Option("Loading…", disabled=True))

    def show_error(self, message: str) -> None:
        self.clear_options()
        self.add_option(Option(message, disabled=True))

    def set_resources(
        self,
        resources: Iterable[tuple[str, str, bool]],
        *,
        selected_id: str | None = None,
        empty: str = "None",
    ) -> None:
        self.clear_options()
        selected_index: int | None = None
        for resource_id, label, disabled in resources:
            if resource_id == selected_id:
                selected_index = self.option_count
            self.add_option(Option(label, id=resource_id, disabled=disabled))
        if self.option_count == 0:
            self.add_option(Option(empty, disabled=True))
        elif selected_index is not None:
            self.highlighted = selected_index


class ConversationView(VerticalScroll):
    """Display conversation messages and update one streaming response."""

    def __init__(self) -> None:
        super().__init__(id="conversation")
        self._active_response: Markdown | None = None
        self._active_text = ""

    async def set_messages(self, messages: Iterable[tuple[str, str]]) -> None:
        await self.remove_children()
        self._active_response = None
        self._active_text = ""
        found = False
        for role, text in messages:
            found = True
            await self.mount(self._message(role, text))
        if not found:
            await self.mount(
                Static(
                    "Start a conversation from the prompt below.",
                    classes="conversation-empty",
                )
            )
        self.scroll_end(animate=False)

    async def add_user(self, text: str) -> None:
        await self._remove_empty()
        await self.mount(self._message("user", text))
        self.scroll_end(animate=False)

    async def start_response(self) -> None:
        self._active_text = ""
        self._active_response = self._message("assistant", "")
        await self.mount(self._active_response)
        self.scroll_end(animate=False)

    def append_response(self, text: str) -> None:
        if self._active_response is None:
            return
        follow = self.is_vertical_scroll_end
        self._active_text += text
        self._active_response.update(
            self._content("assistant", self._active_text)
        )
        if follow:
            self.scroll_end(animate=False)

    def finish_response(self, state: str | None = None) -> None:
        if self._active_response is None:
            return
        if state is not None:
            self._active_response.update(
                self._content(
                    "assistant",
                    self._active_text or "_No response text._",
                    state,
                )
            )
        self._active_response = None
        self._active_text = ""

    async def _remove_empty(self) -> None:
        empty = self.query(".conversation-empty")
        if empty:
            await empty.remove()

    @staticmethod
    def _content(role: str, text: str, state: str | None = None) -> str:
        name = "You" if role == "user" else "Ethos"
        suffix = f" · _{state}_" if state is not None else ""
        return f"**{name}{suffix}**\n\n{text}"

    @classmethod
    def _message(cls, role: str, text: str) -> Markdown:
        return Markdown(cls._content(role, text), classes=f"message {role}")


class PromptComposer(Vertical):
    """Multiline prompt input with visible send and cancellation hints."""

    def compose(self) -> ComposeResult:
        yield TextArea(
            id="prompt",
            soft_wrap=True,
            show_line_numbers=False,
            placeholder="Ask Ethos…",
        )
        yield Label(
            "Ctrl+Enter send",
            id="composer-hint",
        )

    @property
    def text(self) -> str:
        return self.query_one(TextArea).text

    def clear(self) -> None:
        self.query_one(TextArea).text = ""

    def set_available(self, available: bool, *, busy: bool = False) -> None:
        prompt = self.query_one(TextArea)
        prompt.disabled = not available
        hint = "Esc cancel" if busy else "Ctrl+Enter send"
        self.query_one("#composer-hint", Label).update(hint)

    def focus_input(self) -> None:
        self.query_one(TextArea).focus()


class FeedbackBar(Static):
    """Present status and errors without replacing screen content."""

    def __init__(self) -> None:
        super().__init__("Ready  ·  ? help", id="feedback")

    def show_ready(self, *, usage: int | None = None) -> None:
        tokens = f"  ·  {usage:,} tokens" if usage is not None else ""
        self.update(f"Ready{tokens}  ·  ? help")
        self.remove_class("error")

    def show_busy(self, elapsed: float = 0) -> None:
        self.update(f"Thinking  ·  {elapsed:.1f}s  ·  Esc cancel")
        self.remove_class("error")

    def show_error(self, message: str) -> None:
        self.update(f"Error: {message}")
        self.add_class("error")

    def show_cancelled(self) -> None:
        self.update("Cancelled  ·  partial response may not be saved")
        self.remove_class("error")


class AdaptiveShell(Vertical):
    """Compose the stable regions used by responsive Ethos screens."""

    def __init__(
        self,
        header: AppHeader,
        workspaces: ResourceList,
        sessions: ResourceList,
        conversation: ConversationView,
        composer: PromptComposer,
        feedback: FeedbackBar,
    ) -> None:
        super().__init__(id="adaptive-shell")
        self._header = header
        self._workspaces = workspaces
        self._sessions = sessions
        self._conversation = conversation
        self._composer = composer
        self._feedback = feedback

    def compose(self) -> ComposeResult:
        yield self._header
        with Horizontal(id="body"):
            with Vertical(id="navigation"):
                yield Label("Workspaces", classes="section-title")
                yield self._workspaces
                yield Label("Sessions", classes="section-title")
                yield self._sessions
            with Vertical(id="main-pane"):
                yield self._conversation
                yield self._composer
        yield self._feedback
