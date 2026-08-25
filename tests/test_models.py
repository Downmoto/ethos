import asyncio

import pytest
from pydantic import TypeAdapter, ValidationError

from ethos.models import (
    FinishReason,
    Message,
    MessagePart,
    Model,
    ModelEvent,
    ModelRequest,
    ModelResponse,
    ReasoningDelta,
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
from fakes import FakeModel


def text_response(text: str = "hello") -> ModelResponse:
    return ModelResponse(
        parts=(TextPart(text=text),),
        usage=Usage(input_tokens=2, output_tokens=1),
        finish_reason=FinishReason.STOP,
        provider_response_id="response-1",
    )


def test_model_request_json_round_trip_covers_every_message_part() -> None:
    request = ModelRequest(
        messages=(
            Message(role=Role.SYSTEM, parts=(TextPart(text="system"),)),
            Message(role=Role.USER, parts=(TextPart(text="question"),)),
            Message(
                role=Role.ASSISTANT,
                parts=(
                    ReasoningPart(text="I should inspect the file."),
                    TextPart(text="checking"),
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
        ),
        tools=(
            ToolDefinition(
                name="read_file",
                description="Read one file",
                parameters_schema={"type": "object", "properties": {}},
            ),
        ),
    )

    assert (
        ModelRequest.model_validate_json(request.model_dump_json()) == request
    )


def test_model_response_json_round_trip_covers_response_parts() -> None:
    response = ModelResponse(
        parts=(
            ReasoningPart(text="I should inspect the file."),
            TextPart(text="checking"),
            ToolCallPart(
                call_id="call-1",
                name="read_file",
                arguments_json="not valid JSON",
            ),
        ),
        usage=Usage(input_tokens=3, output_tokens=2),
        finish_reason=FinishReason.TOOL_CALL,
        provider_response_id="response-1",
    )

    assert (
        ModelResponse.model_validate_json(response.model_dump_json())
        == response
    )


def test_usage_rejects_reasoning_tokens_outside_output_total() -> None:
    with pytest.raises(ValidationError, match="cannot exceed output tokens"):
        Usage(output_tokens=1, reasoning_tokens=2)


@pytest.mark.parametrize(
    "event",
    [
        TextDelta(text="hel"),
        ReasoningDelta(text="think"),
        ResponseCompleted(response=text_response()),
    ],
)
def test_model_event_json_round_trip(event: ModelEvent) -> None:
    adapter: TypeAdapter[ModelEvent] = TypeAdapter(ModelEvent)

    assert adapter.validate_json(adapter.dump_json(event)) == event


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({"kind": "text", "text": ""}, "at least 1 character"),
        ({"kind": "reasoning", "text": ""}, "at least 1 character"),
        (
            {
                "kind": "tool_call",
                "call_id": "",
                "name": "read_file",
                "arguments_json": "{}",
            },
            "at least 1 character",
        ),
        (
            {
                "kind": "tool_call",
                "call_id": "call-1",
                "name": "1invalid",
                "arguments_json": "{}",
            },
            "string_pattern_mismatch",
        ),
        (
            {
                "kind": "tool_result",
                "call_id": "call-1",
                "name": "invalid name",
                "content": "",
            },
            "string_pattern_mismatch",
        ),
    ],
)
def test_message_parts_reject_empty_or_invalid_identifiers(
    value: object, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        TypeAdapter(MessagePart).validate_python(value)


@pytest.mark.parametrize(
    "value",
    [
        {"role": "unknown", "parts": [{"kind": "text", "text": "x"}]},
        {"role": "user", "parts": []},
        {
            "role": "user",
            "parts": [
                {
                    "kind": "tool_call",
                    "call_id": "call-1",
                    "name": "read_file",
                    "arguments_json": "{}",
                }
            ],
        },
        {
            "role": "assistant",
            "parts": [
                {
                    "kind": "tool_result",
                    "call_id": "call-1",
                    "name": "read_file",
                    "content": "result",
                }
            ],
        },
        {
            "role": "tool",
            "parts": [
                {
                    "kind": "tool_result",
                    "call_id": "call-1",
                    "name": "read_file",
                    "content": "first",
                },
                {
                    "kind": "tool_result",
                    "call_id": "call-2",
                    "name": "read_file",
                    "content": "second",
                },
            ],
        },
    ],
)
def test_message_rejects_invalid_role_or_part_combinations(
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        Message.model_validate(value)


@pytest.mark.parametrize(
    "value",
    [
        {
            "name": "",
            "description": "Read",
            "parameters_schema": {"type": "object"},
        },
        {
            "name": "read_file",
            "description": "",
            "parameters_schema": {"type": "object"},
        },
        {
            "name": "read_file",
            "description": "Read",
            "parameters_schema": {},
        },
        {
            "name": "read_file",
            "description": "Read",
            "parameters_schema": {"type": "array"},
        },
    ],
)
def test_tool_definition_rejects_invalid_names_descriptions_and_schemas(
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        ToolDefinition.model_validate(value)


def test_model_response_rejects_empty_parts() -> None:
    with pytest.raises(ValidationError, match="at least 1 item"):
        ModelResponse(parts=())


def test_usage_rejects_negative_tokens() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        Usage(input_tokens=-1)


def test_model_values_are_frozen_and_reject_extra_fields() -> None:
    part = TextPart(text="hello")

    with pytest.raises(ValidationError, match="frozen"):
        part.text = "changed"
    with pytest.raises(ValidationError, match="Extra inputs"):
        TextPart.model_validate({"text": "hello", "extra": True})


def test_fake_model_records_requests_and_returns_queued_responses() -> None:
    first = text_response("first")
    second = text_response("second")
    model = FakeModel([first, second])
    model_contract: Model = model
    request = ModelRequest(messages=())

    async def request_twice() -> tuple[ModelResponse, ModelResponse]:
        return (
            await model_contract.request(request),
            await model_contract.request(request),
        )

    assert asyncio.run(request_twice()) == (first, second)
    assert model.requests == [request, request]


def test_fake_model_raises_queued_errors() -> None:
    model = FakeModel([RuntimeError("provider failed")])

    with pytest.raises(RuntimeError, match="provider failed"):
        asyncio.run(model.request(ModelRequest(messages=())))


def test_fake_model_streams_supplied_chunks_then_one_completion() -> None:
    response = text_response("hello")
    model = FakeModel([response], stream_chunks=[("hel", "lo")])
    request = ModelRequest(messages=())

    async def collect() -> list[ModelEvent]:
        return [event async for event in model.stream(request)]

    events = asyncio.run(collect())

    assert events == [
        TextDelta(text="hel"),
        TextDelta(text="lo"),
        ResponseCompleted(response=response),
    ]
    assert model.requests == [request]


def test_fake_model_rejects_inconsistent_stream_chunks() -> None:
    model = FakeModel(
        [text_response("hello")],
        stream_chunks=[("does not match",)],
    )

    async def collect() -> list[ModelEvent]:
        return [
            event async for event in model.stream(ModelRequest(messages=()))
        ]

    with pytest.raises(AssertionError, match="do not match"):
        asyncio.run(collect())


def test_fake_model_rejects_exhausted_or_misaligned_queues() -> None:
    with pytest.raises(ValueError, match="each fake outcome"):
        FakeModel([text_response()], stream_chunks=[])

    model = FakeModel([])
    with pytest.raises(AssertionError, match="no queued outcomes"):
        asyncio.run(model.request(ModelRequest(messages=())))
