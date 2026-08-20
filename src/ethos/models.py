"""Provider-independent model request, response, and streaming values."""

from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Annotated, Literal, Protocol, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

type NonEmptyString = Annotated[str, Field(min_length=1)]
type ToolName = Annotated[
    str,
    Field(min_length=1, pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$"),
]


class _ModelValue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class TextPart(_ModelValue):
    kind: Literal["text"] = "text"
    text: NonEmptyString


class ToolCallPart(_ModelValue):
    kind: Literal["tool_call"] = "tool_call"
    call_id: NonEmptyString
    name: ToolName
    arguments_json: str


class ToolResultPart(_ModelValue):
    kind: Literal["tool_result"] = "tool_result"
    call_id: NonEmptyString
    name: ToolName
    content: str
    is_error: bool = False


type MessagePart = Annotated[
    TextPart | ToolCallPart | ToolResultPart,
    Field(discriminator="kind"),
]


class Message(_ModelValue):
    role: Role
    parts: Annotated[tuple[MessagePart, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_parts_for_role(self) -> Self:
        allowed: dict[Role, tuple[type[_ModelValue], ...]] = {
            Role.SYSTEM: (TextPart,),
            Role.USER: (TextPart,),
            Role.ASSISTANT: (TextPart, ToolCallPart),
            Role.TOOL: (ToolResultPart,),
        }
        if not all(isinstance(part, allowed[self.role]) for part in self.parts):
            raise ValueError(f"invalid part for {self.role.value} message")
        if self.role is Role.TOOL and len(self.parts) != 1:
            raise ValueError("tool message must contain exactly one result")
        return self


class ToolDefinition(_ModelValue):
    name: ToolName
    description: NonEmptyString
    parameters: dict[str, object]

    @field_validator("parameters")
    @classmethod
    def parameters_must_describe_an_object(
        cls, value: dict[str, object]
    ) -> dict[str, object]:
        if value.get("type") != "object":
            raise ValueError("tool parameters must describe a JSON object")
        return value


class ModelRequest(_ModelValue):
    messages: tuple[Message, ...]
    tools: tuple[ToolDefinition, ...] = ()


class ModelFeatures(_ModelValue):
    tools: bool


class Usage(_ModelValue):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class FinishReason(StrEnum):
    STOP = "stop"
    LENGTH = "length"
    TOOL_CALL = "tool_call"
    CONTENT_FILTER = "content_filter"
    ERROR = "error"
    OTHER = "other"


type ResponsePart = Annotated[
    TextPart | ToolCallPart,
    Field(discriminator="kind"),
]


class ModelResponse(_ModelValue):
    parts: Annotated[tuple[ResponsePart, ...], Field(min_length=1)]
    usage: Usage = Field(default_factory=Usage)
    finish_reason: FinishReason = FinishReason.OTHER
    provider_response_id: str | None = None


class TextDelta(_ModelValue):
    kind: Literal["text_delta"] = "text_delta"
    text: str


class ResponseCompleted(_ModelValue):
    kind: Literal["response_completed"] = "response_completed"
    response: ModelResponse


type ModelEvent = Annotated[
    TextDelta | ResponseCompleted,
    Field(discriminator="kind"),
]


class Model(Protocol):
    @property
    def features(self) -> ModelFeatures: ...

    async def request(self, request: ModelRequest) -> ModelResponse: ...

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]: ...
