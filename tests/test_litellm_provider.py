"""Set ETHOS_TEST_OPENAI_API_KEY and ETHOS_TEST_OPENAI_MODEL to run smoke."""

import asyncio
import os
from collections.abc import AsyncIterator

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import pytest  # noqa: E402
from litellm import ModelResponse as LiteLLMResponse  # noqa: E402
from litellm import ModelResponseStream as LiteLLMStreamChunk  # noqa: E402
from pydantic import SecretStr  # noqa: E402

from ethos.models import (  # noqa: E402
    FinishReason,
    Message,
    ModelEvent,
    ModelRequest,
    ModelResponse,
    ResponseCompleted,
    Role,
    TextDelta,
    TextPart,
    ToolDefinition,
    Usage,
)
from ethos.provider import (  # noqa: E402
    AIProvider,
    LiteLLMModel,
    ModelProtocolError,
    ModelProviderError,
    ProviderName,
)


def request() -> ModelRequest:
    return ModelRequest(
        messages=(
            Message(role=Role.SYSTEM, parts=(TextPart(text="Be brief."),)),
            Message(
                role=Role.USER,
                parts=(TextPart(text="Say "), TextPart(text="hello.")),
            ),
            Message(
                role=Role.ASSISTANT,
                parts=(TextPart(text="Previous answer."),),
            ),
        )
    )


def response(
    content: str | None = "hello",
    *,
    finish_reason: str | None = "stop",
    response_id: str = "response-1",
    choices: int = 1,
    usage: object = None,
    **message: object,
) -> LiteLLMResponse:
    return LiteLLMResponse(
        id=response_id,
        choices=[
            {
                "index": index,
                "message": {
                    "role": "assistant",
                    "content": content,
                    **message,
                },
                "finish_reason": finish_reason,
            }
            for index in range(choices)
        ],
        usage=usage,
    )


def chunk(
    content: object = None,
    *,
    finish_reason: str | None = None,
    response_id: str = "response-1",
    choices: int = 1,
    usage: object = None,
    **delta: object,
) -> LiteLLMStreamChunk:
    return LiteLLMStreamChunk(
        id=response_id,
        choices=[
            {
                "index": index,
                "delta": {"content": content, **delta},
                "finish_reason": finish_reason,
            }
            for index in range(choices)
        ],
        usage=usage,
    )


def stream(*chunks: object) -> AsyncIterator[object]:
    async def iterate() -> AsyncIterator[object]:
        for item in chunks:
            yield item

    return iterate()


@pytest.mark.parametrize(
    ("provider", "expected_model", "expected_auth"),
    [
        (
            AIProvider(ProviderName.OPENAI, SecretStr("openai-key")),
            "openai/test-model",
            {"api_key": "openai-key"},
        ),
        (
            AIProvider(ProviderName.GOOGLE, SecretStr("google-key")),
            "gemini/test-model",
            {"api_key": "google-key"},
        ),
        (
            AIProvider(
                ProviderName.OLLAMA,
                None,
                "http://ollama.test:11434",
            ),
            "ollama/test-model",
            {"base_url": "http://ollama.test:11434"},
        ),
    ],
)
def test_litellm_model_sends_exact_provider_request(
    provider: AIProvider,
    expected_model: str,
    expected_auth: dict[str, object],
) -> None:
    calls: list[dict[str, object]] = []

    async def completion(**kwargs: object) -> object:
        calls.append(kwargs)
        return response()

    model = LiteLLMModel(provider, "test-model", completion)

    result = asyncio.run(model.request(request()))

    assert result.parts == (TextPart(text="hello"),)
    assert calls == [
        {
            "model": expected_model,
            "messages": [
                {"role": "system", "content": "Be brief."},
                {"role": "user", "content": "Say hello."},
                {"role": "assistant", "content": "Previous answer."},
            ],
            "stream": False,
            **expected_auth,
        }
    ]


def test_litellm_model_converts_complete_response() -> None:
    async def completion(**_kwargs: object) -> object:
        return response(
            "answer",
            finish_reason="length",
            response_id="provider-id",
            usage={
                "prompt_tokens": 7,
                "completion_tokens": 3,
                "total_tokens": 10,
            },
        )

    result = asyncio.run(
        LiteLLMModel(
            AIProvider(ProviderName.OPENAI, SecretStr("key")),
            "model",
            completion,
        ).request(ModelRequest(messages=()))
    )

    assert result.parts == (TextPart(text="answer"),)
    assert result.usage == Usage(input_tokens=7, output_tokens=3)
    assert result.finish_reason is FinishReason.LENGTH
    assert result.provider_response_id == "provider-id"


@pytest.mark.parametrize(
    ("provider_reason", "ethos_reason"),
    [
        ("stop", FinishReason.STOP),
        ("length", FinishReason.LENGTH),
        ("tool_calls", FinishReason.TOOL_CALL),
        ("content_filter", FinishReason.CONTENT_FILTER),
        ("finish_reason_unspecified", FinishReason.OTHER),
    ],
)
def test_litellm_model_maps_finish_reasons(
    provider_reason: str, ethos_reason: FinishReason
) -> None:
    async def completion(**_kwargs: object) -> object:
        return response(finish_reason=provider_reason)

    result = asyncio.run(
        LiteLLMModel(
            AIProvider(ProviderName.OPENAI, SecretStr("key")),
            "model",
            completion,
        ).request(ModelRequest(messages=()))
    )

    assert result.finish_reason is ethos_reason


@pytest.mark.parametrize(
    "provider_response",
    [
        response(""),
        response(None),
        response(choices=0),
        response(choices=2),
        response(reasoning_content="reasoning"),
        response(
            tool_calls=[
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ]
        ),
        object(),
    ],
)
def test_litellm_model_rejects_malformed_complete_responses(
    provider_response: object,
) -> None:
    async def completion(**_kwargs: object) -> object:
        return provider_response

    with pytest.raises(ModelProtocolError):
        asyncio.run(
            LiteLLMModel(
                AIProvider(ProviderName.OPENAI, SecretStr("key")),
                "model",
                completion,
            ).request(ModelRequest(messages=()))
        )


def test_litellm_model_rejects_tools_before_tool_milestone() -> None:
    called = False

    async def completion(**_kwargs: object) -> object:
        nonlocal called
        called = True
        return response()

    model = LiteLLMModel(
        AIProvider(ProviderName.OPENAI, SecretStr("key")),
        "model",
        completion,
    )
    request_with_tools = ModelRequest(
        messages=(),
        tools=(
            ToolDefinition(
                name="read_file",
                description="Read a file",
                parameters={"type": "object"},
            ),
        ),
    )

    with pytest.raises(ModelProtocolError, match="does not support tools"):
        asyncio.run(model.request(request_with_tools))

    assert not called


def test_litellm_model_wraps_provider_error_without_secret() -> None:
    async def completion(**_kwargs: object) -> object:
        raise RuntimeError("provider included secret-key")

    model = LiteLLMModel(
        AIProvider(ProviderName.OPENAI, SecretStr("secret-key")),
        "model",
        completion,
    )

    with pytest.raises(ModelProviderError) as caught:
        asyncio.run(model.request(ModelRequest(messages=())))

    assert "secret-key" not in str(caught.value)
    assert "secret-key" not in repr(model)
    assert isinstance(caught.value.__cause__, RuntimeError)


def test_litellm_model_streams_text_and_completes_once() -> None:
    calls: list[dict[str, object]] = []
    chunks = stream(
        chunk("h"),
        chunk(""),
        chunk("ello"),
        chunk(finish_reason="stop"),
        chunk(
            choices=0,
            usage={
                "prompt_tokens": 4,
                "completion_tokens": 2,
                "total_tokens": 6,
            },
        ),
    )

    async def completion(**kwargs: object) -> object:
        calls.append(kwargs)
        return chunks

    model = LiteLLMModel(
        AIProvider(ProviderName.OPENAI, SecretStr("key")),
        "model",
        completion,
    )

    async def collect() -> list[ModelEvent]:
        return [
            event async for event in model.stream(ModelRequest(messages=()))
        ]

    events = asyncio.run(collect())

    assert events[:2] == [TextDelta(text="h"), TextDelta(text="ello")]
    assert len(events) == 3
    completed = events[-1]
    assert isinstance(completed, ResponseCompleted)
    assert completed.response.parts == (TextPart(text="hello"),)
    assert completed.response.usage == Usage(input_tokens=4, output_tokens=2)
    assert calls == [
        {
            "model": "openai/model",
            "messages": [],
            "stream": True,
            "stream_options": {"include_usage": True},
            "api_key": "key",
        }
    ]


@pytest.mark.parametrize(
    "chunks",
    [
        stream(chunk("text")),
        stream(chunk("text", finish_reason="stop"), chunk("late")),
        stream(chunk("text", choices=2, finish_reason="stop")),
        stream(chunk("text", response_id="one"), chunk("x", response_id="two")),
        stream(chunk("", finish_reason="stop")),
        stream(
            chunk(
                "text",
                tool_calls=[
                    {
                        "index": 0,
                        "id": "call-1",
                        "function": {
                            "name": "read_file",
                            "arguments": "{}",
                        },
                    }
                ],
            )
        ),
        stream(object()),
        response(),
    ],
)
def test_litellm_model_rejects_malformed_streams(chunks: object) -> None:
    async def completion(**_kwargs: object) -> object:
        return chunks

    model = LiteLLMModel(
        AIProvider(ProviderName.OPENAI, SecretStr("key")),
        "model",
        completion,
    )

    async def collect() -> list[ModelEvent]:
        return [
            event async for event in model.stream(ModelRequest(messages=()))
        ]

    with pytest.raises(ModelProtocolError):
        asyncio.run(collect())


def test_litellm_model_wraps_midstream_provider_error() -> None:
    async def failing_stream() -> AsyncIterator[object]:
        yield chunk("partial")
        raise RuntimeError("provider included secret-key")

    async def completion(**_kwargs: object) -> object:
        return failing_stream()

    model = LiteLLMModel(
        AIProvider(ProviderName.OPENAI, SecretStr("secret-key")),
        "model",
        completion,
    )
    events: list[ModelEvent] = []

    async def collect() -> None:
        async for event in model.stream(ModelRequest(messages=())):
            events.append(event)

    with pytest.raises(ModelProviderError) as caught:
        asyncio.run(collect())

    assert events == [TextDelta(text="partial")]
    assert "secret-key" not in str(caught.value)


_integration_key = os.getenv("ETHOS_TEST_OPENAI_API_KEY")
_integration_model = os.getenv("ETHOS_TEST_OPENAI_MODEL")


@pytest.mark.integration
@pytest.mark.skipif(
    not _integration_key or not _integration_model,
    reason="set ETHOS_TEST_OPENAI_API_KEY and ETHOS_TEST_OPENAI_MODEL",
)
def test_litellm_model_openai_request_and_stream_integration() -> None:
    assert _integration_key is not None
    assert _integration_model is not None
    model = LiteLLMModel(
        AIProvider(ProviderName.OPENAI, SecretStr(_integration_key)),
        _integration_model,
    )
    prompt = ModelRequest(
        messages=(
            Message(role=Role.USER, parts=(TextPart(text="Reply briefly."),)),
        )
    )

    async def smoke_test() -> tuple[ModelResponse, list[ModelEvent]]:
        complete = await model.request(prompt)
        streamed = [event async for event in model.stream(prompt)]
        return complete, streamed

    complete, streamed = asyncio.run(smoke_test())

    assert complete.parts
    assert isinstance(streamed[-1], ResponseCompleted)
