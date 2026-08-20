import asyncio
from collections.abc import Callable

import pytest
from pydantic import BaseModel

import ethos.tools as tools_module
from ethos.models import ToolCallPart, ToolDefinition
from ethos.tools import (
    Allow,
    DefaultToolPolicy,
    Deny,
    Tool,
    ToolEffect,
    ToolExecutor,
    ToolRegistry,
)


class WeatherArguments(BaseModel):
    location: str


class FakeTool:
    arguments_type: type[BaseModel] = WeatherArguments

    def __init__(
        self,
        name: str,
        *,
        effect: ToolEffect = ToolEffect.READ,
        execute: Callable[[BaseModel], str] | None = None,
    ) -> None:
        self.definition = ToolDefinition(
            name=name,
            description=f"Run {name}",
            parameters={
                "type": "object",
                "properties": {"location": {"type": "string"}},
                "required": ["location"],
            },
        )
        self.effect = effect
        self._execute = execute or (lambda _arguments: "23 degrees")
        self.arguments: list[BaseModel] = []

    async def execute(self, arguments: BaseModel) -> str:
        self.arguments.append(arguments)
        return self._execute(arguments)


class RecordingPolicy:
    def __init__(self, decision: Allow | Deny) -> None:
        self.decision = decision
        self.calls: list[tuple[ToolCallPart, Tool]] = []

    async def decide(self, call: ToolCallPart, tool: Tool) -> Allow | Deny:
        self.calls.append((call, tool))
        return self.decision


def call(
    *, name: str = "weather", arguments_json: str = '{"location":"Toronto"}'
) -> ToolCallPart:
    return ToolCallPart(
        call_id="call-1",
        name=name,
        arguments_json=arguments_json,
    )


def test_registry_preserves_registration_order_and_lookup() -> None:
    first = FakeTool("first")
    second = FakeTool("second")
    registry = ToolRegistry((first, second))

    assert registry.definitions == (first.definition, second.definition)
    assert registry.get("first") is first
    assert registry.get("missing") is None


def test_registry_rejects_duplicate_names() -> None:
    registry = ToolRegistry((FakeTool("weather"),))

    with pytest.raises(ValueError, match="already registered: weather"):
        registry.register(FakeTool("weather"))


@pytest.mark.parametrize("effect", tuple(ToolEffect))
def test_executor_validates_then_asks_policy_then_executes(
    effect: ToolEffect,
) -> None:
    order: list[str] = []

    class OrderedPolicy:
        async def decide(
            self, requested_call: ToolCallPart, tool: Tool
        ) -> Allow | Deny:
            assert requested_call == call()
            assert tool.effect is effect
            order.append("policy")
            return Allow()

    def execute(arguments: BaseModel) -> str:
        assert arguments == WeatherArguments(location="Toronto")
        order.append("execute")
        return "23 degrees"

    tool = FakeTool("weather", effect=effect, execute=execute)
    result = asyncio.run(
        ToolExecutor(ToolRegistry((tool,)), OrderedPolicy()).execute(call())
    )

    assert order == ["policy", "execute"]
    assert result.call_id == "call-1"
    assert result.name == "weather"
    assert result.content == "23 degrees"
    assert not result.is_error


def test_default_policy_allows_read_tools() -> None:
    tool = FakeTool("weather")

    result = asyncio.run(ToolExecutor(ToolRegistry((tool,))).execute(call()))

    assert result.content == "23 degrees"
    assert not result.is_error
    assert tool.arguments == [WeatherArguments(location="Toronto")]


def test_default_policy_denies_write_tools_before_execution() -> None:
    tool = FakeTool("weather", effect=ToolEffect.WRITE)

    result = asyncio.run(ToolExecutor(ToolRegistry((tool,))).execute(call()))

    assert result.content == "write tools are not allowed"
    assert result.is_error
    assert tool.arguments == []


def test_falsey_custom_policy_is_not_replaced() -> None:
    class FalseyPolicy(RecordingPolicy):
        def __bool__(self) -> bool:
            return False

    tool = FakeTool("weather")
    policy = FalseyPolicy(Deny(reason="custom denial"))

    result = asyncio.run(
        ToolExecutor(ToolRegistry((tool,)), policy).execute(call())
    )

    assert result.content == "custom denial"
    assert result.is_error
    assert len(policy.calls) == 1
    assert tool.arguments == []


@pytest.mark.parametrize(
    "arguments_json",
    (
        "{",
        "[]",
        "null",
        '"Toronto"',
        '{"location": 23}',
        '{"location": "Toronto", "extra": NaN}',
    ),
)
def test_invalid_arguments_never_reach_policy_or_tool(
    arguments_json: str,
) -> None:
    tool = FakeTool("weather")
    policy = RecordingPolicy(Allow())

    result = asyncio.run(
        ToolExecutor(ToolRegistry((tool,)), policy).execute(
            call(arguments_json=arguments_json)
        )
    )

    assert result.content == "invalid tool arguments"
    assert result.is_error
    assert policy.calls == []
    assert tool.arguments == []


def test_unknown_tool_returns_safe_error() -> None:
    policy = RecordingPolicy(Allow())

    result = asyncio.run(
        ToolExecutor(ToolRegistry(), policy).execute(call(name="missing"))
    )

    assert result.content == "unknown tool"
    assert result.is_error
    assert policy.calls == []


def test_policy_denial_returns_bounded_reason_without_execution() -> None:
    tool = FakeTool("weather")
    policy = RecordingPolicy(Deny(reason="not permitted"))

    result = asyncio.run(
        ToolExecutor(ToolRegistry((tool,)), policy).execute(call())
    )

    assert result.content == "not permitted"
    assert result.is_error
    assert len(policy.calls) == 1
    assert tool.arguments == []


@pytest.mark.parametrize("reason", ("", "x" * 501))
def test_denial_reason_is_bounded(reason: str) -> None:
    with pytest.raises(ValueError, match="between 1 and 500"):
        Deny(reason=reason)


def test_tool_exception_does_not_expose_exception_text() -> None:
    def fail(_arguments: BaseModel) -> str:
        raise RuntimeError("secret argument was Toronto")

    result = asyncio.run(
        ToolExecutor(
            ToolRegistry((FakeTool("weather", execute=fail),))
        ).execute(call())
    )

    assert result.content == "tool execution failed"
    assert "Toronto" not in result.content
    assert result.is_error


def test_tool_timeout_returns_stable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tools_module, "TOOL_TIMEOUT_SECONDS", 0.0)

    async def run() -> None:
        class SlowTool(FakeTool):
            async def execute(self, arguments: BaseModel) -> str:
                self.arguments.append(arguments)
                await asyncio.Event().wait()
                return "unreachable"

        result = await ToolExecutor(
            ToolRegistry((SlowTool("weather"),))
        ).execute(call())

        assert result.content == "tool execution timed out"
        assert result.is_error

    asyncio.run(run())


def test_cancelled_tool_execution_propagates() -> None:
    class CancelledTool(FakeTool):
        async def execute(self, arguments: BaseModel) -> str:
            self.arguments.append(arguments)
            raise asyncio.CancelledError

    async def run() -> None:
        with pytest.raises(asyncio.CancelledError):
            await ToolExecutor(
                ToolRegistry((CancelledTool("weather"),))
            ).execute(call())

    asyncio.run(run())


def test_policy_failure_propagates_as_runtime_bug() -> None:
    class BrokenPolicy:
        async def decide(
            self, requested_call: ToolCallPart, tool: Tool
        ) -> Allow | Deny:
            raise RuntimeError("policy bug")

    async def run() -> None:
        with pytest.raises(RuntimeError, match="policy bug"):
            await ToolExecutor(
                ToolRegistry((FakeTool("weather"),)), BrokenPolicy()
            ).execute(call())

    asyncio.run(run())


def test_default_policy_decides_from_effect() -> None:
    policy = DefaultToolPolicy()
    read = FakeTool("read", effect=ToolEffect.READ)
    write = FakeTool("write", effect=ToolEffect.WRITE)

    assert isinstance(asyncio.run(policy.decide(call(), read)), Allow)
    assert asyncio.run(policy.decide(call(), write)) == Deny(
        reason="write tools are not allowed"
    )
