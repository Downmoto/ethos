"""Transport-neutral command request and streamed response contracts.

See ``docs/development/commands-events-and-gateways.md`` for how adapters
establish request identity and consume response streams.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

COMMAND_NAME_PATTERN = r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)+$"

type CommandName = Annotated[str, Field(pattern=COMMAND_NAME_PATTERN)]
type Identifier = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1)
]


class CommandRequest(BaseModel):
    """A command invocation independent of its transport.

    ``source``, ``owner_id``, and ``external_context`` are adapter-supplied
    claims. This model validates their representation, not their authenticity;
    a gateway must authenticate and authorise callers before constructing it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: CommandName
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    source: Identifier
    owner_id: Identifier
    external_context: dict[Identifier, Identifier] = Field(default_factory=dict)


class CommandUsage(BaseModel):
    """Transport-neutral token usage for streamed command output."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class CommandResponse(BaseModel):
    """One transport-neutral output from a command execution.

    A handler may yield several responses. For chat, usage snapshots are
    cumulative and ``done`` marks completion after session persistence.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = ""
    data: dict[str, JsonValue] = Field(default_factory=dict)
    usage: CommandUsage | None = None
    done: bool = False
