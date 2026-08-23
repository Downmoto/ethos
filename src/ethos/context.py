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

# diagnostic sys instructions, not final
SYSTEM_INSTRUCTION: Final = "You are Ethos, a personal AI assistant."


class ContextBuilder:
    """Build one model request from canonical history and run-only context."""

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

        current_date = now.strftime("%A, %d %B %Y")
        current_time = now.strftime("%H:%M")

        tz_name = now.tzname()

        offset = now.strftime("%z")
        formatted_offset = f"{offset[:3]}:{offset[3:]}"

        message = (
            f"Current date: {current_date}\n"
            f"Current time: {current_time}\n"
            f"Timezone: {tz_name} (UTC{formatted_offset})"
        )

        return message

    def build(
        self,
        stored_messages: tuple[Message, ...],
        run_instructions: tuple[str, ...] = (),
        tool_definitions: tuple[ToolDefinition, ...] = (),
        *,
        run_context: RunContext | None = None,
    ) -> ModelRequest:
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
