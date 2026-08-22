"""Policy-guarded model tool registration and execution."""

import asyncio
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Protocol, Self, cast
from uuid import NAMESPACE_URL, uuid5

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from ethos.models import ToolCallPart, ToolDefinition, ToolResultPart, Usage

TOOL_TIMEOUT_SECONDS: Final = 30.0
MAX_DENIAL_REASON_LENGTH: Final = 500


class ToolEffect(StrEnum):
    READ = "read"
    WRITE = "write"


class Tool(Protocol):
    definition: ToolDefinition
    effect: ToolEffect
    arguments_type: type[BaseModel]

    async def execute(self, arguments: BaseModel) -> str: ...


@dataclass(frozen=True)
class Allow:
    pass


@dataclass(frozen=True)
class Deny:
    reason: str

    def __post_init__(self) -> None:
        if not self.reason or len(self.reason) > MAX_DENIAL_REASON_LENGTH:
            raise ValueError(
                "denial reason must be between 1 and 500 characters"
            )


@dataclass(frozen=True)
class RequireApproval:
    reason: str

    def __post_init__(self) -> None:
        if not self.reason or len(self.reason) > MAX_DENIAL_REASON_LENGTH:
            raise ValueError(
                "approval reason must be between 1 and 500 characters"
            )


class ToolPolicy(Protocol):
    async def decide(
        self, call: ToolCallPart, tool: Tool
    ) -> Allow | Deny | RequireApproval: ...


class DefaultToolPolicy:
    async def decide(
        self, call: ToolCallPart, tool: Tool
    ) -> Allow | Deny | RequireApproval:
        if tool.effect is ToolEffect.READ:
            return Allow()
        return RequireApproval(reason="write tool requires approval")


class ApprovalState(StrEnum):
    PENDING = "pending"
    EXECUTING = "executing"
    COMPLETED = "completed"
    DENIED = "denied"
    INDETERMINATE = "indeterminate"


class ToolApproval(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    call: ToolCallPart
    tool_name: str = Field(min_length=1)
    arguments: dict[str, object]
    effect: ToolEffect
    reason: str = Field(min_length=1, max_length=MAX_DENIAL_REASON_LENGTH)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    state: ApprovalState = ApprovalState.PENDING
    round_number: int = Field(ge=1)
    usage: Usage = Field(default_factory=Usage)
    result: ToolResultPart | None = None

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if self.call.name != self.tool_name:
            raise ValueError("approval tool name does not match call")
        finished = self.state in (ApprovalState.COMPLETED, ApprovalState.DENIED)
        if finished != (self.result is not None):
            raise ValueError("approval state and result do not match")
        return self


@dataclass(frozen=True)
class PreparedToolCall:
    call: ToolCallPart
    tool: Tool
    arguments: BaseModel
    decision: Allow | RequireApproval


def approval_request_id(session_id: str, call_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"ethos:{session_id}:{call_id}"))


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    @property
    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(tool.definition for tool in self._tools.values())

    def register(self, tool: Tool) -> None:
        name = tool.definition.name
        if name in self._tools:
            raise ValueError(f"tool is already registered: {name}")
        self._tools[name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        policy: ToolPolicy | None = None,
    ) -> None:
        self._registry = registry
        self._policy = policy if policy is not None else DefaultToolPolicy()

    async def prepare(
        self, call: ToolCallPart
    ) -> PreparedToolCall | ToolResultPart:
        tool = self._registry.get(call.name)
        if tool is None:
            return _error(call, "unknown tool")

        try:
            value = json.loads(
                call.arguments_json,
                parse_constant=_reject_json_constant,
            )
            if not isinstance(value, dict):
                raise ValueError
            arguments = tool.arguments_type.model_validate(value)
        except (json.JSONDecodeError, ValidationError, ValueError):
            return _error(call, "invalid tool arguments")

        decision = cast(object, await self._policy.decide(call, tool))
        if isinstance(decision, Deny):
            return _error(call, decision.reason)
        if not isinstance(decision, (Allow, RequireApproval)):
            raise TypeError("tool policy returned an invalid decision")
        return PreparedToolCall(call, tool, arguments, decision)

    async def run(self, prepared: PreparedToolCall) -> ToolResultPart:
        try:
            async with asyncio.timeout(TOOL_TIMEOUT_SECONDS):
                content = cast(
                    object,
                    await prepared.tool.execute(prepared.arguments),
                )
        except TimeoutError:
            return _error(prepared.call, "tool execution timed out")
        except Exception:
            return _error(prepared.call, "tool execution failed")
        if not isinstance(content, str):
            raise TypeError("tool returned a non-string result")
        return ToolResultPart(
            call_id=prepared.call.call_id,
            name=prepared.call.name,
            content=content,
        )

    async def execute(self, call: ToolCallPart) -> ToolResultPart:
        prepared = await self.prepare(call)
        if isinstance(prepared, ToolResultPart):
            return prepared
        if isinstance(prepared.decision, RequireApproval):
            return _error(call, prepared.decision.reason)
        return await self.run(prepared)


def _error(call: ToolCallPart, content: str) -> ToolResultPart:
    return ToolResultPart(
        call_id=call.call_id,
        name=call.name,
        content=content,
        is_error=True,
    )


def _reject_json_constant(_value: str) -> None:
    raise ValueError
