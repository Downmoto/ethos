"""Construct model requests without changing stored conversation state."""

import datetime
import json
from typing import Final

from ethos.capabilities import RunContext
from ethos.models import (
    Message,
    ModelRequest,
    Role,
    TextPart,
    ToolDefinition,
)

# Stable identity precedes ephemeral run context and capability instructions.
SYSTEM_INSTRUCTION: Final = (
    "You are Ethos, a personal AI assistant. When a task requires an "
    "available tool, call it before responding; never describe a tool call "
    "you intend to make."
)


class ContextBuilder:
    """Build one model request from canonical history and run-only context.

    Constructed system messages are request-only: they precede stored history
    in a deterministic order and are never returned for persistence.
    """

    def _build_run_context(self, context: RunContext) -> str:
        return "Run context: " + json.dumps(
            {
                "workspace_name": context.workspace_name,
                "workspace_path": str(context.workspace_path),
                "session_id": context.session_id,
            },
            sort_keys=True,
        )

    def _build_date_time_context(self) -> str:
        now = datetime.datetime.now().astimezone()
        offset = now.strftime("%z")
        return (
            f"Current date: {now:%A, %d %B %Y}\n"
            f"Current time: {now:%H:%M}\n"
            f"Timezone: {now.tzname()} (UTC{offset[:3]}:{offset[3:]})"
        )

    def build(
        self,
        stored_messages: tuple[Message, ...],
        run_instructions: tuple[str, ...] = (),
        tool_definitions: tuple[ToolDefinition, ...] = (),
        *,
        run_context: RunContext | None = None,
    ) -> ModelRequest:
        """Prepend transient context and tools without mutating history."""

        context_instruction = (
            (self._build_run_context(run_context),)
            if run_context is not None
            else ()
        )
        instructions = tuple(
            Message(role=Role.SYSTEM, parts=(TextPart(text=text),))
            for text in (
                SYSTEM_INSTRUCTION,
                self._build_date_time_context(),
                *context_instruction,
                *run_instructions,
            )
        )
        return ModelRequest(
            messages=(*instructions, *stored_messages),
            tools=tool_definitions,
        )
