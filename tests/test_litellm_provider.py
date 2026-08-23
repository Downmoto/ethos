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
    ModelFeatures,
    ModelRequest,
    ModelResponse,
    ReasoningDelta,
    ReasoningEffort,
    ReasoningPart,
    ResponseCompleted,
    Role,
    TextDelta,
    TextPart,
    ToolCallPart,
    ToolDefinition,
    ToolResultPart,
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
                parts=(
                    ReasoningPart(text="Previous reasoning."),
                    TextPart(text="Previous answer."),
                ),
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


def tool_call_delta(
    index: int,
    *,
    call_id: str | None = None,
    name: str | None = None,
    arguments: str | None = None,
) -> dict[str, object]:
    delta: dict[str, object] = {"index": index, "type": "function"}
    if call_id is not None:
        delta["id"] = call_id
    function: dict[str, str] = {}
    if name is not None:
        function["name"] = name
    if arguments is not None:
        function["arguments"] = arguments
    if function:
        delta["function"] = function
    return delta


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
            "ollama_chat/test-model",
            {
                "base_url": "http://ollama.test:11434",
                "reasoning_effort": "none",
            },
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


@pytest.mark.parametrize("tool_count", [1, 2])
def test_litellm_model_sends_tool_definitions(tool_count: int) -> None:
    calls: list[dict[str, object]] = []

    async def completion(**kwargs: object) -> object:
        calls.append(kwargs)
        return response()

    tools = tuple(
        ToolDefinition(
            name=f"tool_{index}",
            description=f"Tool {index}",
            parameters={
                "type": "object",
                "properties": {"value": {"type": "integer"}},
            },
        )
        for index in range(tool_count)
    )
    model = LiteLLMModel(
        AIProvider(ProviderName.OPENAI, SecretStr("key")),
        "model",
        completion,
    )

    asyncio.run(model.request(ModelRequest(messages=(), tools=tools)))

    assert calls[0]["tools"] == [
        {
            "type": "function",
            "function": {
                "name": f"tool_{index}",
                "description": f"Tool {index}",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "integer"}},
                },
            },
        }
        for index in range(tool_count)
    ]


def test_litellm_model_sends_configured_reasoning_effort() -> None:
    calls: list[dict[str, object]] = []

    async def completion(**kwargs: object) -> object:
        calls.append(kwargs)
        return response()

    model = LiteLLMModel(
        AIProvider(ProviderName.OPENAI, SecretStr("key")),
        "model",
        completion,
        reasoning_effort=ReasoningEffort.HIGH,
    )

    asyncio.run(model.request(ModelRequest(messages=())))

    assert calls[0]["reasoning_effort"] == "high"


def test_litellm_model_converts_complete_reasoning() -> None:
    async def completion(**_kwargs: object) -> object:
        return response(
            "answer",
            reasoning_content="thinking",
            usage={
                "prompt_tokens": 3,
                "completion_tokens": 4,
                "total_tokens": 7,
                "completion_tokens_details": {"reasoning_tokens": 2},
            },
        )

    result = asyncio.run(
        LiteLLMModel(
            AIProvider(ProviderName.OPENAI, SecretStr("key")),
            "model",
            completion,
        ).request(ModelRequest(messages=()))
    )

    assert result.parts == (
        ReasoningPart(text="thinking"),
        TextPart(text="answer"),
    )
    assert result.usage == Usage(
        input_tokens=3,
        output_tokens=4,
        reasoning_tokens=2,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reasoning_content", {"text": "invalid"}),
        ("thinking_blocks", [{"text": "opaque"}]),
        ("reasoning_items", [{"text": "opaque"}]),
    ],
)
def test_litellm_model_rejects_unsupported_reasoning(
    field: str,
    value: object,
) -> None:
    provider_response = response("answer")
    setattr(provider_response.choices[0].message, field, value)

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


def test_litellm_model_streams_reasoning_separately() -> None:
    chunks = stream(
        chunk(reasoning_content="think"),
        chunk(reasoning_content="ing"),
        chunk("answer"),
        chunk(finish_reason="stop"),
        chunk(
            usage={
                "prompt_tokens": 3,
                "completion_tokens": 4,
                "total_tokens": 7,
                "completion_tokens_details": {"reasoning_tokens": 2},
            }
        ),
    )

    async def completion(**_kwargs: object) -> object:
        return chunks

    model = LiteLLMModel(
        AIProvider(ProviderName.OLLAMA, None),
        "qwen3",
        completion,
        reasoning_effort=ReasoningEffort.HIGH,
    )

    async def collect() -> list[ModelEvent]:
        return [
            event async for event in model.stream(ModelRequest(messages=()))
        ]

    events = asyncio.run(collect())

    assert events[:-1] == [
        ReasoningDelta(text="think"),
        ReasoningDelta(text="ing"),
        TextDelta(text="answer"),
    ]
    completed = events[-1]
    assert isinstance(completed, ResponseCompleted)
    assert completed.response.parts == (
        ReasoningPart(text="thinking"),
        TextPart(text="answer"),
    )
    assert completed.response.usage == Usage(
        input_tokens=3,
        output_tokens=4,
        reasoning_tokens=2,
        reasoning_tokens_estimated=True,
    )


def test_litellm_model_sends_tool_calls_and_results() -> None:
    calls: list[dict[str, object]] = []

    async def completion(**kwargs: object) -> object:
        calls.append(kwargs)
        return response()

    messages = (
        Message(
            role=Role.ASSISTANT,
            parts=(
                TextPart(text="Checking"),
                ToolCallPart(
                    call_id="call-1",
                    name="read_file",
                    arguments_json='{"path":"README.md"}',
                ),
            ),
        ),
        Message(
            role=Role.TOOL,
            parts=(
                ToolResultPart(
                    call_id="call-1",
                    name="read_file",
                    content="contents",
                ),
            ),
        ),
        Message(
            role=Role.TOOL,
            parts=(
                ToolResultPart(
                    call_id="call-2",
                    name="read_file",
                    content="tool failed",
                    is_error=True,
                ),
            ),
        ),
    )
    model = LiteLLMModel(
        AIProvider(ProviderName.OPENAI, SecretStr("key")),
        "model",
        completion,
    )

    asyncio.run(model.request(ModelRequest(messages=messages)))

    assert calls[0]["messages"] == [
        {
            "role": "assistant",
            "content": "Checking",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path":"README.md"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "name": "read_file",
            "content": "contents",
        },
        {
            "role": "tool",
            "tool_call_id": "call-2",
            "name": "read_file",
            "content": "tool failed",
        },
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
    ("content", "calls", "expected_parts"),
    [
        (
            None,
            [("call-1", "read_file", "{}")],
            (
                ToolCallPart(
                    call_id="call-1",
                    name="read_file",
                    arguments_json="{}",
                ),
            ),
        ),
        (
            "Checking",
            [("call-1", "read_file", '{"path":"README.md"}')],
            (
                TextPart(text="Checking"),
                ToolCallPart(
                    call_id="call-1",
                    name="read_file",
                    arguments_json='{"path":"README.md"}',
                ),
            ),
        ),
        (
            None,
            [
                ("call-1", "first_tool", "not JSON"),
                ("call-2", "second_tool", "[]"),
            ],
            (
                ToolCallPart(
                    call_id="call-1",
                    name="first_tool",
                    arguments_json="not JSON",
                ),
                ToolCallPart(
                    call_id="call-2",
                    name="second_tool",
                    arguments_json="[]",
                ),
            ),
        ),
    ],
)
def test_litellm_model_converts_complete_tool_calls(
    content: str | None,
    calls: list[tuple[str, str, str]],
    expected_parts: tuple[TextPart | ToolCallPart, ...],
) -> None:
    async def completion(**_kwargs: object) -> object:
        return response(
            content,
            finish_reason="tool_calls",
            tool_calls=[
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
                for call_id, name, arguments in calls
            ],
        )

    result = asyncio.run(
        LiteLLMModel(
            AIProvider(ProviderName.OPENAI, SecretStr("key")),
            "model",
            completion,
        ).request(ModelRequest(messages=()))
    )

    assert result.parts == expected_parts
    assert result.finish_reason is FinishReason.TOOL_CALL


@pytest.mark.parametrize("arguments", ["", "not JSON", "[]"])
def test_litellm_model_preserves_raw_tool_arguments(arguments: str) -> None:
    async def completion(**_kwargs: object) -> object:
        return response(
            None,
            finish_reason="tool_calls",
            tool_calls=[
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": arguments,
                    },
                }
            ],
        )

    result = asyncio.run(
        LiteLLMModel(
            AIProvider(ProviderName.OPENAI, SecretStr("key")),
            "model",
            completion,
        ).request(ModelRequest(messages=()))
    )

    assert result.parts == (
        ToolCallPart(
            call_id="call-1",
            name="read_file",
            arguments_json=arguments,
        ),
    )


@pytest.mark.parametrize("invalid", ["missing", "duplicate"])
def test_litellm_model_rejects_invalid_complete_tool_call_ids(
    invalid: str,
) -> None:
    provider_response = response(
        None,
        finish_reason="tool_calls",
        tool_calls=[
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "first_tool", "arguments": "{}"},
            },
            {
                "id": "call-2",
                "type": "function",
                "function": {"name": "second_tool", "arguments": "{}"},
            },
        ],
    )
    tool_calls = provider_response.choices[0].message.tool_calls
    assert tool_calls is not None
    if invalid == "missing":
        tool_calls[0].id = None  # pyright: ignore[reportAttributeAccessIssue]
    else:
        tool_calls[1].id = "call-1"

    async def completion(**_kwargs: object) -> object:
        return provider_response

    with pytest.raises(ModelProtocolError, match="invalid tool calls"):
        asyncio.run(
            LiteLLMModel(
                AIProvider(ProviderName.OPENAI, SecretStr("key")),
                "model",
                completion,
            ).request(ModelRequest(messages=()))
        )


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


def test_litellm_model_rejects_tools_when_feature_is_disabled() -> None:
    called = False

    async def completion(**_kwargs: object) -> object:
        nonlocal called
        called = True
        return response()

    model = LiteLLMModel(
        AIProvider(ProviderName.OPENAI, SecretStr("key")),
        "model",
        completion,
        features=ModelFeatures(tools=False),
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


def test_litellm_model_exposes_safe_unsupported_thinking_error() -> None:
    class LiteLLMError(RuntimeError):
        message = (
            "litellm.APIConnectionError: Ollama_chatException - "
            '{"error":"\\"llama3.1:8b\\" does not support thinking"}'
        )

    async def completion(**_kwargs: object) -> object:
        raise LiteLLMError

    model = LiteLLMModel(
        AIProvider(ProviderName.OLLAMA, None),
        "llama3.1:8b",
        completion,
        reasoning_effort=ReasoningEffort.MEDIUM,
    )

    with pytest.raises(
        ModelProviderError,
        match='model provider request failed: "llama3.1:8b" '
        "does not support thinking",
    ):
        asyncio.run(model.request(ModelRequest(messages=())))


def test_litellm_model_streams_text_and_completes_once() -> None:
    calls: list[dict[str, object]] = []
    chunks = stream(
        chunk("h", response_id="first-id"),
        chunk("", response_id="second-id"),
        chunk("ello", response_id="third-id"),
        chunk(finish_reason="stop", response_id="fourth-id"),
        chunk(
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
    assert completed.response.provider_response_id == "first-id"
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
    ("chunks", "expected_deltas", "expected_parts"),
    [
        (
            stream(
                chunk(
                    tool_calls=[
                        tool_call_delta(
                            0,
                            call_id="call-1",
                            name="read_file",
                            arguments="{}",
                        )
                    ]
                ),
                chunk(finish_reason="tool_calls"),
            ),
            [],
            (
                ToolCallPart(
                    call_id="call-1",
                    name="read_file",
                    arguments_json="{}",
                ),
            ),
        ),
        (
            stream(
                chunk("Checking"),
                chunk(
                    tool_calls=[
                        tool_call_delta(
                            0,
                            call_id="call-1",
                            name="read_file",
                            arguments='{"path":"README.md"}',
                        )
                    ]
                ),
                chunk(finish_reason="tool_calls"),
            ),
            [TextDelta(text="Checking")],
            (
                TextPart(text="Checking"),
                ToolCallPart(
                    call_id="call-1",
                    name="read_file",
                    arguments_json='{"path":"README.md"}',
                ),
            ),
        ),
        (
            stream(
                chunk(
                    tool_calls=[
                        tool_call_delta(
                            0,
                            call_id="call-1",
                            name="first_tool",
                            arguments='{"value":',
                        )
                    ]
                ),
                chunk(
                    tool_calls=[
                        tool_call_delta(
                            1,
                            call_id="call-2",
                            name="second_tool",
                            arguments='{"value":',
                        )
                    ]
                ),
                chunk(tool_calls=[tool_call_delta(0, arguments="1}")]),
                chunk(tool_calls=[tool_call_delta(1, arguments="2}")]),
                chunk(finish_reason="tool_calls"),
            ),
            [],
            (
                ToolCallPart(
                    call_id="call-1",
                    name="first_tool",
                    arguments_json='{"value":1}',
                ),
                ToolCallPart(
                    call_id="call-2",
                    name="second_tool",
                    arguments_json='{"value":2}',
                ),
            ),
        ),
        (
            stream(
                chunk(
                    tool_calls=[
                        tool_call_delta(
                            0,
                            call_id="call-1",
                            name="read_file",
                            arguments="{}",
                        )
                    ]
                ),
                chunk(finish_reason="stop"),
            ),
            [],
            (
                ToolCallPart(
                    call_id="call-1",
                    name="read_file",
                    arguments_json="{}",
                ),
            ),
        ),
    ],
)
def test_litellm_model_assembles_streamed_tool_calls(
    chunks: AsyncIterator[object],
    expected_deltas: list[TextDelta],
    expected_parts: tuple[TextPart | ToolCallPart, ...],
) -> None:
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

    events = asyncio.run(collect())

    assert events[:-1] == expected_deltas
    completed = events[-1]
    assert isinstance(completed, ResponseCompleted)
    assert completed.response.parts == expected_parts
    assert completed.response.finish_reason is FinishReason.TOOL_CALL


@pytest.mark.parametrize("arguments", ["", "not JSON", "[]"])
def test_litellm_model_preserves_streamed_raw_tool_arguments(
    arguments: str,
) -> None:
    chunks = stream(
        chunk(
            tool_calls=[
                tool_call_delta(
                    0,
                    call_id="call-1",
                    name="read_file",
                    arguments=arguments,
                )
            ]
        ),
        chunk(finish_reason="tool_calls"),
    )

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

    events = asyncio.run(collect())

    completed = events[-1]
    assert isinstance(completed, ResponseCompleted)
    assert completed.response.parts == (
        ToolCallPart(
            call_id="call-1",
            name="read_file",
            arguments_json=arguments,
        ),
    )


@pytest.mark.parametrize(
    "chunks",
    [
        stream(chunk("text")),
        stream(chunk("text", finish_reason="stop"), chunk("late")),
        stream(chunk("text", choices=2, finish_reason="stop")),
        stream(chunk("", finish_reason="stop")),
        stream(
            chunk(
                tool_calls=[
                    tool_call_delta(0, name="read_file", arguments="{}")
                ],
            ),
            chunk(finish_reason="tool_calls"),
        ),
        stream(
            chunk(
                tool_calls=[
                    tool_call_delta(
                        0,
                        call_id="duplicate",
                        name="first_tool",
                        arguments="{}",
                    ),
                    tool_call_delta(
                        1,
                        call_id="duplicate",
                        name="second_tool",
                        arguments="{}",
                    ),
                ]
            ),
            chunk(finish_reason="tool_calls"),
        ),
        stream(
            chunk(
                tool_calls=[
                    tool_call_delta(
                        0,
                        call_id="call-1",
                        name="read_file",
                    )
                ]
            ),
            chunk(
                tool_calls=[
                    tool_call_delta(
                        0,
                        call_id="call-2",
                        arguments="{}",
                    )
                ]
            ),
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
