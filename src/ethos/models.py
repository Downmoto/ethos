"""Provider-independent model request, response, and streaming values.

These strict, frozen values are the contract between persistence, the runtime,
and provider adapters. Provider-specific shapes must be translated at the
adapter boundary rather than leaking into this module.
"""

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


class ReasoningPart(_ModelValue):
    """Provider-reported reasoning retained for display, but not replayed."""

    kind: Literal["reasoning"] = "reasoning"
    text: NonEmptyString


class ToolCallPart(_ModelValue):
    """A provider-requested call whose JSON remains unparsed until execution."""

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
    TextPart | ReasoningPart | ToolCallPart | ToolResultPart,
    Field(discriminator="kind"),
]


class Message(_ModelValue):
    """One canonical conversation message stored in session history.

    The role validator keeps invalid provider combinations out of persistence;
    adapters may accept broader wire formats but must reduce them to these
    combinations before returning them to Ethos.
    """

    role: Role
    parts: Annotated[tuple[MessagePart, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_parts_for_role(self) -> Self:
        allowed: dict[Role, tuple[type[_ModelValue], ...]] = {
            Role.SYSTEM: (TextPart,),
            Role.USER: (TextPart,),
            Role.ASSISTANT: (TextPart, ReasoningPart, ToolCallPart),
            Role.TOOL: (ToolResultPart,),
        }
        if not all(isinstance(part, allowed[self.role]) for part in self.parts):
            raise ValueError(f"invalid part for {self.role.value} message")
        if self.role is Role.TOOL and len(self.parts) != 1:
            raise ValueError("tool message must contain exactly one result")
        return self


class ToolDefinition(_ModelValue):
    """Provider-neutral function metadata advertised to a model."""

    name: ToolName
    description: NonEmptyString
    parameters_schema: dict[str, object]

    @field_validator("parameters_schema")
    @classmethod
    def parameters_schema_must_describe_an_object(
        cls, value: dict[str, object]
    ) -> dict[str, object]:
        if value.get("type") != "object":
            raise ValueError("tool parameters must describe a JSON object")
        return value


class ModelRequest(_ModelValue):
    """Complete conversation context for one stateless model invocation."""

    messages: tuple[Message, ...]
    tools: tuple[ToolDefinition, ...] = ()


class ModelFeatures(_ModelValue):
    """Adapter capabilities that callers must check before making a request."""

    tools: bool
    reasoning: bool = False


class ReasoningEffort(StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Usage(_ModelValue):
    """Token accounting with reasoning represented as an output-token subset.

    ``reasoning_tokens_estimated`` marks counts that are adapter-estimated or
    originate from a provider route with approximate reasoning accounting.
    """

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    reasoning_tokens_estimated: bool = False

    @model_validator(mode="after")
    def reasoning_must_be_part_of_output(self) -> Self:
        if self.reasoning_tokens > self.output_tokens:
            raise ValueError("reasoning tokens cannot exceed output tokens")
        return self

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class FinishReason(StrEnum):
    STOP = "stop"
    LENGTH = "length"
    TOOL_CALL = "tool_call"
    CONTENT_FILTER = "content_filter"
    ERROR = "error"
    OTHER = "other"


type ResponsePart = Annotated[
    TextPart | ReasoningPart | ToolCallPart,
    Field(discriminator="kind"),
]


class ModelResponse(_ModelValue):
    """A complete response assembled and validated by a model adapter."""

    parts: Annotated[tuple[ResponsePart, ...], Field(min_length=1)]
    usage: Usage = Field(default_factory=Usage)
    finish_reason: FinishReason = FinishReason.OTHER
    provider_response_id: str | None = None


class TextDelta(_ModelValue):
    """One non-overlapping text fragment emitted before stream completion."""

    kind: Literal["text_delta"] = "text_delta"
    text: str


class ReasoningDelta(_ModelValue):
    """One non-overlapping reasoning fragment emitted before completion."""

    kind: Literal["reasoning_delta"] = "reasoning_delta"
    text: str


class ResponseCompleted(_ModelValue):
    kind: Literal["response_completed"] = "response_completed"
    response: ModelResponse


type ModelEvent = Annotated[
    TextDelta | ReasoningDelta | ResponseCompleted,
    Field(discriminator="kind"),
]


class Model(Protocol):
    """Stateless model boundary implemented by production and fake adapters.

    ``stream`` yields zero or more text or reasoning deltas followed by exactly
    one ``ResponseCompleted`` event. Its completed response is authoritative
    and contains every response part, including content emitted as deltas.
    """

    @property
    def features(self) -> ModelFeatures: ...

    async def request(self, request: ModelRequest) -> ModelResponse: ...

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]: ...
