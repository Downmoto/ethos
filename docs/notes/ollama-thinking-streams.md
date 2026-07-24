# Ollama thinking streams

This is a design note, not a description of current supported behaviour.

## Status

Deferred. Ethos currently streams answer deltas with `stream_text(delta=True)`.
Do not expose model thinking until the terminal presentation and configuration
are decided.

## Proposed approach

Pydantic AI's `stream_text()` excludes thinking content. Replace it with
`Agent.run_stream_events()` and distinguish these event payloads:

- `ThinkingPart` and `ThinkingPartDelta` for the reasoning trace
- `TextPart` and `TextPartDelta` for the final answer

Extend the provider-neutral `PromptStreamEvent` with a text kind so presentation
remains outside the runtime:

```python
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class PromptStreamEvent:
    text: str = ""
    text_kind: Literal["thinking", "answer"] = "answer"
    # Existing usage and completion fields remain unchanged.


async with agent.run_stream_events(prompt) as events:
    async for event in events:
        match event:
            case PartStartEvent(part=ThinkingPart(content=content)):
                yield PromptStreamEvent(text=content, text_kind="thinking")
            case PartDeltaEvent(
                delta=ThinkingPartDelta(content_delta=content)
            ) if content:
                yield PromptStreamEvent(text=content, text_kind="thinking")
            case PartStartEvent(part=TextPart(content=content)):
                yield PromptStreamEvent(text=content)
            case PartDeltaEvent(
                delta=TextPartDelta(content_delta=content)
            ):
                yield PromptStreamEvent(text=content)
```

The Click command can append `event.text` and render a new heading when
`event.text_kind` changes.

## Ollama details

Ethos uses Pydantic AI's `OllamaModel`, which communicates through Ollama's
OpenAI-compatible `/v1` endpoint. Configure thinking with
`openai_reasoning_effort` (`low`, `medium`, `high`, or `none`) rather than the
native Ollama `think` parameter.

Thinking output requires a compatible model. Ollama currently documents Qwen
3, GPT-OSS, DeepSeek v3.1, and DeepSeek R1, among others. Availability and
behaviour may change, so verify the selected model before implementation.

Treat the trace as model-emitted reasoning, not a guaranteed faithful account
of how the model produced its answer. Decide whether it should be hidden by
default, enabled through configuration, or shown only in a debug interface.

## References

- [Ollama thinking](https://docs.ollama.com/capabilities/thinking)
- [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)
- [Pydantic AI OpenAI-compatible thinking mapping](https://github.com/pydantic/pydantic-ai/blob/main/pydantic_ai_slim/pydantic_ai/models/openai.py)
