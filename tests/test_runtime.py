import asyncio
from collections.abc import AsyncIterator

import pytest
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from ethos.config import EthosSettings
from ethos.provider import AIProvider
from ethos.runtime import AgentRuntime, PromptStreamEvent, run_prompt_singleton


def settings() -> EthosSettings:
    return EthosSettings.model_validate(
        {
            "provider": {"name": "openai", "model_name": "gpt-5-mini"},
            "keys": {"openai_api_key": "test-key"},
        }
    )


def test_run_prompt_returns_model_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        AIProvider,
        "model",
        lambda _provider, _model_name: TestModel(  # pyright: ignore
            custom_output_text="hello from ethos"
        ),  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    )

    async def collect_events() -> list[PromptStreamEvent]:
        return [
            event async for event in run_prompt_singleton("hello", settings())
        ]

    events = asyncio.run(collect_events())

    assert "".join(event.text for event in events) == "hello from ethos"
    assert events[-1].done
    assert events[-1].usage is not None
    assert events[-1].usage.output_tokens > 0


def test_runtime_keeps_conversation_history_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[list[ModelMessage]] = []

    async def respond(
        messages: list[ModelMessage], _info: AgentInfo
    ) -> AsyncIterator[str]:
        requests.append(messages)
        yield "response"

    monkeypatch.setattr(
        AIProvider,
        "model",
        lambda _provider, _model_name: FunctionModel(  # pyright: ignore
            stream_function=respond
        ),  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    )
    runtime = AgentRuntime(settings())

    async def run_turns() -> None:
        for prompt, conversation_id in (
            ("first", "one"),
            ("second", "one"),
            ("separate", "two"),
        ):
            _ = [event async for event in runtime.run(prompt, conversation_id)]

    asyncio.run(run_turns())

    assert [len(messages) for messages in requests] == [1, 3, 1]
    prompts = [
        part.content
        for message in requests[1]
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]
    assert prompts == ["first", "second"]


def test_runtime_serialises_each_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = 0
    most_active = 0

    async def respond(
        _messages: list[ModelMessage], _info: AgentInfo
    ) -> AsyncIterator[str]:
        nonlocal active, most_active
        active += 1
        most_active = max(most_active, active)
        await asyncio.sleep(0.01)
        yield "response"
        active -= 1

    monkeypatch.setattr(
        AIProvider,
        "model",
        lambda _provider, _model_name: FunctionModel(  # pyright: ignore
            stream_function=respond
        ),  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    )
    runtime = AgentRuntime(settings())

    async def collect(conversation_id: str) -> None:
        _ = [event async for event in runtime.run("hello", conversation_id)]

    async def run_concurrently() -> None:
        nonlocal most_active
        await asyncio.gather(collect("same"), collect("same"))
        assert most_active == 1

        most_active = 0
        await asyncio.gather(collect("one"), collect("two"))
        assert most_active == 2

    asyncio.run(run_concurrently())
