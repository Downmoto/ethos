import asyncio
from collections.abc import Callable

import pytest
from pydantic import BaseModel

import ethos.tools as tools_module
from ethos.models import ToolCallPart, ToolDefinition, ToolResultPart
from ethos.tools import (
    Allow,
    DefaultToolPolicy,
    Deny,
    PreparedToolCall,
    RejectedToolCall,
    RequireApproval,
    Tool,
    ToolEffect,
    ToolExecutionError,
    ToolExecutor,
    ToolPolicyError,
    ToolPreparationOutcome,
    ToolRegistry,
    approval_request_id,
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
    def __init__(self, decision: Allow | Deny | RequireApproval) -> None:
        self.decision = decision
        self.calls: list[tuple[ToolCallPart, Tool]] = []

    async def decide(
        self, call: ToolCallPart, tool: Tool
    ) -> Allow | Deny | RequireApproval:
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


async def run_allowed(
    executor: ToolExecutor, requested_call: ToolCallPart
) -> ToolResultPart:
    prepared = await executor.prepare(requested_call)
    assert isinstance(prepared, PreparedToolCall)
    assert isinstance(prepared.decision, Allow)
    return await executor.run(prepared)


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
    executor = ToolExecutor(ToolRegistry((tool,)), OrderedPolicy())
    prepared = asyncio.run(executor.prepare(call()))

    assert isinstance(prepared, PreparedToolCall)
    assert order == ["policy"]
    result = asyncio.run(executor.run(prepared))

    assert order == ["policy", "execute"]
    assert result.call_id == "call-1"
    assert result.name == "weather"
    assert result.content == "23 degrees"
    assert not result.is_error


def test_default_policy_allows_read_tools() -> None:
    tool = FakeTool("weather")

    result = asyncio.run(
        run_allowed(ToolExecutor(ToolRegistry((tool,))), call())
    )

    assert result.content == "23 degrees"
    assert not result.is_error
    assert tool.arguments == [WeatherArguments(location="Toronto")]


def test_executor_prepares_validated_approval_without_execution() -> None:
    tool = FakeTool("weather", effect=ToolEffect.WRITE)
    prepared = asyncio.run(ToolExecutor(ToolRegistry((tool,))).prepare(call()))

    assert isinstance(prepared, PreparedToolCall)
    assert isinstance(prepared.decision, RequireApproval)
    assert prepared.arguments == WeatherArguments(location="Toronto")
    assert tool.arguments == []


def test_approval_request_id_is_stable_and_bound_to_session_and_call() -> None:
    approval_id = approval_request_id("session-1", "call-1")

    assert approval_request_id("session-1", "call-1") == approval_id
    assert approval_request_id("session-2", "call-1") != approval_id
    assert approval_request_id("session-1", "call-2") != approval_id


def test_falsey_custom_policy_is_not_replaced() -> None:
    class FalseyPolicy(RecordingPolicy):
        def __bool__(self) -> bool:
            return False

    tool = FakeTool("weather")
    policy = FalseyPolicy(Deny(reason="custom denial"))

    result = asyncio.run(
        ToolExecutor(ToolRegistry((tool,)), policy).prepare(call())
    )

    assert isinstance(result, RejectedToolCall)
    assert result.outcome is ToolPreparationOutcome.DENY
    assert result.result.content == "custom denial"
    assert result.result.is_error
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
        ToolExecutor(ToolRegistry((tool,)), policy).prepare(
            call(arguments_json=arguments_json)
        )
    )

    assert isinstance(result, RejectedToolCall)
    assert result.outcome is ToolPreparationOutcome.INVALID
    assert result.result.content == "invalid tool arguments"
    assert result.result.is_error
    assert policy.calls == []
    assert tool.arguments == []


def test_unknown_tool_returns_safe_error() -> None:
    policy = RecordingPolicy(Allow())

    result = asyncio.run(
        ToolExecutor(ToolRegistry(), policy).prepare(call(name="missing"))
    )

    assert isinstance(result, RejectedToolCall)
    assert result.outcome is ToolPreparationOutcome.UNKNOWN
    assert result.result.content == "unknown tool"
    assert result.result.is_error
    assert policy.calls == []


def test_policy_denial_returns_bounded_reason_without_execution() -> None:
    tool = FakeTool("weather")
    policy = RecordingPolicy(Deny(reason="not permitted"))

    result = asyncio.run(
        ToolExecutor(ToolRegistry((tool,)), policy).prepare(call())
    )

    assert isinstance(result, RejectedToolCall)
    assert result.outcome is ToolPreparationOutcome.DENY
    assert result.result.content == "not permitted"
    assert result.result.is_error
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
        run_allowed(
            ToolExecutor(ToolRegistry((FakeTool("weather", execute=fail),))),
            call(),
        )
    )

    assert result.content == "tool execution failed"
    assert "Toronto" not in result.content
    assert result.is_error


def test_safe_tool_error_is_returned_to_model() -> None:
    def fail(_arguments: BaseModel) -> str:
        raise ToolExecutionError("path must be inside the workspace")

    result = asyncio.run(
        run_allowed(
            ToolExecutor(ToolRegistry((FakeTool("files", execute=fail),))),
            call(name="files"),
        )
    )

    assert result.content == "path must be inside the workspace"
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

        result = await run_allowed(
            ToolExecutor(ToolRegistry((SlowTool("weather"),))), call()
        )

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
            await run_allowed(
                ToolExecutor(ToolRegistry((CancelledTool("weather"),))),
                call(),
            )

    asyncio.run(run())


def test_policy_failure_propagates_as_runtime_bug() -> None:
    class BrokenPolicy:
        async def decide(
            self, requested_call: ToolCallPart, tool: Tool
        ) -> Allow | Deny:
            raise RuntimeError("policy bug")

    async def run() -> None:
        with pytest.raises(ToolPolicyError, match="tool policy failed"):
            await ToolExecutor(
                ToolRegistry((FakeTool("weather"),)), BrokenPolicy()
            ).prepare(call())

    asyncio.run(run())


def test_default_policy_decides_from_effect() -> None:
    policy = DefaultToolPolicy()
    read = FakeTool("read", effect=ToolEffect.READ)
    write = FakeTool("write", effect=ToolEffect.WRITE)

    assert isinstance(asyncio.run(policy.decide(call(), read)), Allow)
    assert asyncio.run(policy.decide(call(), write)) == RequireApproval(
        reason="write tool requires approval"
    )
