"""Command request and output models."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

COMMAND_NAME_PATTERN = r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)+$"

type CommandName = Annotated[str, Field(pattern=COMMAND_NAME_PATTERN)]
type Identifier = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1)
]


class CommandRequest(BaseModel):
    """A command invocation independent of its transport."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: CommandName
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    source: Identifier
    owner_id: Identifier
    external_context: dict[Identifier, Identifier] = Field(default_factory=dict)


class CommandEvent(BaseModel):
    """One transport-neutral output from a command execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = ""
    data: dict[str, JsonValue] = Field(default_factory=dict)
