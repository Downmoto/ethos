import asyncio

import pytest
from pydantic_ai.models.test import TestModel

from ethos.config import EthosSettings
from ethos.provider import AIProvider
from ethos.runtime import PromptStreamEvent, run_prompt_singleton


def test_run_prompt_returns_model_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = EthosSettings.model_validate(
        {
            "provider": {"name": "openai", "model_name": "gpt-5-mini"},
            "keys": {"openai_api_key": "test-key"},
        }
    )
    monkeypatch.setattr(
        AIProvider,
        "model",
        lambda _provider, _model_name: TestModel(  # pyright: ignore
            custom_output_text="hello from ethos"
        ),  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    )

    async def collect_events() -> list[PromptStreamEvent]:
        return [
            event async for event in run_prompt_singleton("hello", settings)
        ]

    events = asyncio.run(collect_events())

    assert "".join(event.text for event in events) == "hello from ethos"
    assert events[-1].done
    assert events[-1].usage is not None
    assert events[-1].usage.output_tokens > 0
