"""Construct model requests without changing stored conversation state."""

import datetime
from typing import Final

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
    ) -> ModelRequest:
        instructions = tuple(
            Message(role=Role.SYSTEM, parts=(TextPart(text=text),))
            for text in (
                SYSTEM_INSTRUCTION,
                self._build_date_time_context(),
                *run_instructions,
            )
        )
        return ModelRequest(
            messages=(*instructions, *stored_messages),
            tools=tool_definitions,
        )
