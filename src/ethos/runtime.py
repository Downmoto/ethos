"""Conversation-aware ethos agent runtime."""

import asyncio
from collections.abc import AsyncIterator
from copy import copy
from dataclasses import dataclass

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from pydantic_ai.usage import RunUsage

from ethos.config import EthosSettings, get_settings
from ethos.provider import AIProvider


@dataclass(frozen=True)
class PromptStreamEvent:
    """Provider-neutral prompt text and usage update."""

    text: str = ""
    usage: RunUsage | None = None
    done: bool = False


class AgentRuntime:
    """Reuse one agent while keeping conversations isolated in memory."""

    def __init__(self, settings: EthosSettings | None = None) -> None:
        settings = settings or get_settings()
        provider = AIProvider.from_settings(settings)
        model = provider.model(settings.provider.model_name)

        self._agent = Agent(model, output_type=str)
        self._conversations: dict[str, list[ModelMessage]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def run(
        self, prompt: str, conversation_id: str
    ) -> AsyncIterator[PromptStreamEvent]:
        """Stream one serialised turn in a conversation."""
        if not conversation_id:
            raise ValueError("conversation ID must not be empty")

        # TODO: Add Event here

        lock = self._locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            async with self._agent.run_stream(
                prompt,
                message_history=self._conversations.get(conversation_id),
                conversation_id=conversation_id,
            ) as result:
                async for chunk in result.stream_text(delta=True):
                    yield PromptStreamEvent(
                        text=chunk,
                        usage=copy(result.usage),
                    )

                self._conversations[conversation_id] = result.all_messages()

                # TODO: Add Event here
                yield PromptStreamEvent(
                    usage=copy(result.usage),
                    done=True,
                )


async def run_prompt_singleton(
    prompt: str, settings: EthosSettings | None = None
) -> AsyncIterator[PromptStreamEvent]:
    """Stream one prompt from the configured provider and model."""
    runtime = AgentRuntime(settings)
    async for event in runtime.run(prompt, "ask"):
        yield event
