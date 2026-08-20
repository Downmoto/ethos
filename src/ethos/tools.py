"""Policy-guarded model tool registration and execution."""

import asyncio
import json
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol, cast

from pydantic import BaseModel, ValidationError

from ethos.models import ToolCallPart, ToolDefinition, ToolResultPart

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


class ToolPolicy(Protocol):
    async def decide(self, call: ToolCallPart, tool: Tool) -> Allow | Deny: ...


class DefaultToolPolicy:
    async def decide(self, call: ToolCallPart, tool: Tool) -> Allow | Deny:
        if tool.effect is ToolEffect.READ:
            return Allow()
        return Deny(reason="write tools are not allowed")


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

    async def execute(self, call: ToolCallPart) -> ToolResultPart:
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
        if not isinstance(decision, Allow):
            raise TypeError("tool policy returned an invalid decision")

        try:
            async with asyncio.timeout(TOOL_TIMEOUT_SECONDS):
                content = cast(object, await tool.execute(arguments))
        except TimeoutError:
            return _error(call, "tool execution timed out")
        except Exception:
            return _error(call, "tool execution failed")
        if not isinstance(content, str):
            raise TypeError("tool returned a non-string result")
        return ToolResultPart(
            call_id=call.call_id,
            name=call.name,
            content=content,
        )


def _error(call: ToolCallPart, content: str) -> ToolResultPart:
    return ToolResultPart(
        call_id=call.call_id,
        name=call.name,
        content=content,
        is_error=True,
    )


def _reject_json_constant(_value: str) -> None:
    raise ValueError
