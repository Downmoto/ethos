"""Single-turn ethos agent runtime."""

from collections.abc import AsyncIterator
from copy import copy
from dataclasses import dataclass

from pydantic_ai import Agent
from pydantic_ai.usage import RunUsage

from ethos.config import EthosSettings, get_settings
from ethos.provider import AIProvider


@dataclass(frozen=True)
class PromptStreamEvent:
    """Provider-neutral prompt text and usage update."""

    text: str = ""
    usage: RunUsage | None = None
    done: bool = False


async def run_prompt_singleton(
    prompt: str, settings: EthosSettings | None = None
) -> AsyncIterator[PromptStreamEvent]:
    """Stream one prompt from the configured provider and model."""
    settings = settings or get_settings()
    provider = AIProvider.from_settings(settings)
    model = provider.model(settings.provider.model_name)

    async with Agent(model, output_type=str).run_stream(prompt) as result:
        async for chunk in result.stream_text(delta=True):
            yield PromptStreamEvent(
                text=chunk,
                usage=copy(result.usage),
            )

        yield PromptStreamEvent(
            usage=copy(result.usage),
            done=True,
        )
