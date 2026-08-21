"""Translate between Ethos model contracts and LiteLLM chat completions.

This is the only production module allowed to depend on LiteLLM types. It
normalises provider responses into strict Ethos values and treats malformed or
unsupported provider output as a protocol error.
"""

import json
import os
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

from pydantic import SecretStr

# ponytail: Ethos does not use LiteLLM pricing, so keep imports offline.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import litellm  # noqa: E402
from litellm import ModelResponse as LiteLLMResponse  # noqa: E402
from litellm import ModelResponseStream as LiteLLMStreamChunk  # noqa: E402
from litellm import (
    acompletion,  # pyright: ignore[reportUnknownVariableType]  # noqa: E402
)
from litellm.exceptions import (  # noqa: E402
    APIConnectionError as LiteLLMAPIConnectionError,
)

litellm.suppress_debug_info = True

from ethos.models import (  # noqa: E402
    FinishReason,
    Message,
    Model,
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

if TYPE_CHECKING:
    from ethos.config import EthosSettings

type CompletionCall = Callable[..., Awaitable[object]]


@runtime_checkable
class _LiteLLMUsage(Protocol):
    prompt_tokens: int
    completion_tokens: int


class ProviderName(StrEnum):
    OPENAI = "openai"
    GOOGLE = "google"
    OLLAMA = "ollama"


@dataclass(frozen=True)
class AIProvider:
    """Bind configured provider credentials and addressing to model adapters."""

    name: ProviderName
    api_key: SecretStr | None
    ollama_base_url: str = "http://localhost:11434"

    @classmethod
    def from_settings(cls, settings: "EthosSettings") -> "AIProvider":
        name = settings.provider.name
        api_key = {
            ProviderName.OPENAI: settings.keys.openai_api_key,
            ProviderName.GOOGLE: settings.keys.google_api_key,
            ProviderName.OLLAMA: settings.keys.ollama_api_key,
        }[name]
        return cls(
            name,
            api_key,
            settings.provider.ollama_base_url,
        )

    def model(
        self,
        model_name: str,
        reasoning_effort: ReasoningEffort = ReasoningEffort.NONE,
    ) -> Model:
        return LiteLLMModel(
            self,
            model_name,
            reasoning_effort=reasoning_effort,
            features=ModelFeatures(tools=True, reasoning=True),
        )


class ModelProviderError(RuntimeError):
    """A provider call failed without exposing provider details."""


class ModelProtocolError(RuntimeError):
    """A provider returned data outside the Ethos model contract."""


@dataclass
class _TextAssembly:
    """Adjacent text fragments occupying one position in response order."""

    chunks: list[str] = dataclass_field(default_factory=lambda: list[str]())


@dataclass
class _ReasoningAssembly:
    """Adjacent reasoning fragments occupying one position in response order."""

    chunks: list[str] = dataclass_field(default_factory=lambda: list[str]())


@dataclass
class _ToolCallAssembly:
    """Fragments for one indexed tool call in a streamed response."""

    index: int
    call_id: str | None = None
    name: str | None = None
    arguments: list[str] = dataclass_field(default_factory=lambda: list[str]())


@dataclass(frozen=True)
class LiteLLMModel:
    """Stateless LiteLLM adapter for complete and streamed chat responses.

    The injected completion callable keeps tests off the network. Streaming
    forwards text and reasoning immediately but buffers tool-call fragments
    until their identifiers, names, and raw JSON arguments can be validated as
    a complete Ethos response.
    """

    provider: AIProvider
    model_name: str
    completion: CompletionCall = dataclass_field(
        default=cast(CompletionCall, acompletion),
        repr=False,
        compare=False,
    )
    reasoning_effort: ReasoningEffort = ReasoningEffort.NONE
    features: ModelFeatures = ModelFeatures(tools=True, reasoning=True)

    async def request(self, request: ModelRequest) -> ModelResponse:
        kwargs = self._kwargs(request, stream=False)
        try:
            result = await self.completion(**kwargs)
        except Exception as error:
            raise _provider_error(
                "request", error, self.provider.name
            ) from error
        if not isinstance(result, LiteLLMResponse):
            raise ModelProtocolError("provider returned an invalid response")
        return _response(
            result,
            reported_reasoning_estimated=(
                self.provider.name is ProviderName.OLLAMA
            ),
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        kwargs = self._kwargs(request, stream=True)
        try:
            result = await self.completion(**kwargs)
        except Exception as error:
            raise _provider_error(
                "request", error, self.provider.name
            ) from error
        if not isinstance(result, AsyncIterable):
            raise ModelProtocolError("provider returned an invalid stream")

        # LiteLLM reports tool fragments by index, while Ethos preserves the
        # first-seen order of text, reasoning, and tool calls.
        order: list[_TextAssembly | _ReasoningAssembly | _ToolCallAssembly] = []
        tool_calls: dict[int, _ToolCallAssembly] = {}
        usage_value: object | None = None
        finish_reason: FinishReason | None = None
        response_id: str | None = None
        finished = False
        try:
            async for value in cast(AsyncIterable[object], result):
                if not isinstance(value, LiteLLMStreamChunk):
                    raise ModelProtocolError(
                        "provider returned an invalid chunk"
                    )
                raw_usage = getattr(value, "usage", None)
                chunk_usage = _usage(raw_usage)
                if chunk_usage is not None:
                    usage_value = raw_usage
                if not value.choices:
                    if chunk_usage is None:
                        raise ModelProtocolError(
                            "provider returned an empty chunk"
                        )
                    continue
                if finished:
                    # Some providers send a final usage-only chunk after the
                    # choice's finish reason. It may update usage, but never
                    # content.
                    if chunk_usage is None or len(value.choices) != 1:
                        raise ModelProtocolError(
                            "provider streamed after completion"
                        )
                    final_choice = value.choices[0]
                    _validate_delta(final_choice.delta)
                    if (
                        final_choice.index != 0
                        or final_choice.delta.content
                        or _reasoning_content(final_choice.delta)
                        or final_choice.delta.tool_calls
                        or final_choice.finish_reason is not None
                    ):
                        raise ModelProtocolError(
                            "provider streamed after completion"
                        )
                    continue
                if len(value.choices) != 1:
                    raise ModelProtocolError(
                        "provider returned multiple choices"
                    )
                choice = value.choices[0]
                if choice.index != 0:
                    raise ModelProtocolError(
                        "provider returned an invalid choice"
                    )
                delta = choice.delta
                _validate_delta(delta)
                if response_id is None:
                    response_id = value.id
                reasoning = _reasoning_content(delta)
                if reasoning:
                    if order and isinstance(order[-1], _ReasoningAssembly):
                        order[-1].chunks.append(reasoning)
                    else:
                        order.append(_ReasoningAssembly(chunks=[reasoning]))
                    yield ReasoningDelta(text=reasoning)
                content = cast(object, delta.content)
                if content is not None and not isinstance(content, str):
                    raise ModelProtocolError(
                        "provider returned unsupported content"
                    )
                if content:
                    if order and isinstance(order[-1], _TextAssembly):
                        order[-1].chunks.append(content)
                    else:
                        order.append(_TextAssembly(chunks=[content]))
                    yield TextDelta(text=content)
                _add_tool_call_deltas(delta, tool_calls, order)
                if choice.finish_reason is not None:
                    finish_reason = _finish_reason(choice.finish_reason)
                    finished = True
        except ModelProtocolError:
            raise
        except Exception as error:
            raise _provider_error(
                "stream", error, self.provider.name
            ) from error

        if not finished or finish_reason is None:
            raise ModelProtocolError("provider stream ended before completion")
        parts = _finalise_stream_parts(order)
        reasoning = "".join(
            part.text for part in parts if isinstance(part, ReasoningPart)
        )
        usage = (
            _usage(
                usage_value,
                reasoning,
                reported_reasoning_estimated=(
                    self.provider.name is ProviderName.OLLAMA
                ),
            )
            or Usage()
        )
        yield ResponseCompleted(
            response=_model_response(
                parts=parts,
                usage=usage,
                finish_reason=finish_reason,
                provider_response_id=response_id,
            )
        )

    def _kwargs(
        self, request: ModelRequest, *, stream: bool
    ) -> dict[str, object]:
        if request.tools and not self.features.tools:
            raise ModelProtocolError("model does not support tools")
        if (
            self.reasoning_effort is not ReasoningEffort.NONE
            and not self.features.reasoning
        ):
            raise ModelProtocolError("model does not support reasoning")
        prefix = {
            ProviderName.GOOGLE: "gemini",
            ProviderName.OLLAMA: "ollama_chat",
        }.get(self.provider.name, self.provider.name.value)
        model = f"{prefix}/{self.model_name}"
        kwargs: dict[str, object] = {
            "model": model,
            "messages": [_message(message) for message in request.messages],
            "stream": stream,
        }
        if request.tools:
            kwargs["tools"] = [_tool(tool) for tool in request.tools]
        if stream:
            kwargs["stream_options"] = {"include_usage": True}
        if self.provider.api_key is not None:
            kwargs["api_key"] = self.provider.api_key.get_secret_value()
        if self.provider.name is ProviderName.OLLAMA:
            kwargs["base_url"] = self.provider.ollama_base_url
        if (
            self.reasoning_effort is not ReasoningEffort.NONE
            or self.provider.name is ProviderName.OLLAMA
        ):
            kwargs["reasoning_effort"] = self.reasoning_effort.value
        return kwargs


def _message(message: Message) -> dict[str, object]:
    if message.role is Role.TOOL:
        part = message.parts[0]
        if not isinstance(part, ToolResultPart):
            raise ModelProtocolError("model received invalid tool result")
        return {
            "role": "tool",
            "tool_call_id": part.call_id,
            "name": part.name,
            "content": part.content,
        }
    text: list[str] = []
    tool_calls: list[dict[str, object]] = []
    for part in message.parts:
        if isinstance(part, TextPart):
            text.append(part.text)
        elif isinstance(part, ReasoningPart) and message.role is Role.ASSISTANT:
            # Reasoning is display history, not portable provider input.
            continue
        elif isinstance(part, ToolCallPart) and message.role is Role.ASSISTANT:
            tool_calls.append(
                {
                    "id": part.call_id,
                    "type": "function",
                    "function": {
                        "name": part.name,
                        "arguments": part.arguments_json,
                    },
                }
            )
        else:
            raise ModelProtocolError("model received unsupported message parts")
    result: dict[str, object] = {
        "role": message.role.value,
        "content": "".join(text),
    }
    if tool_calls:
        result["tool_calls"] = tool_calls
    return result


def _tool(tool: ToolDefinition) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters_schema,
        },
    }


def _response(
    value: LiteLLMResponse,
    *,
    reported_reasoning_estimated: bool = False,
) -> ModelResponse:
    if len(value.choices) != 1:
        raise ModelProtocolError("provider returned multiple choices")
    choice = value.choices[0]
    if choice.index != 0:
        raise ModelProtocolError("provider returned an invalid choice")
    message = choice.message
    if message.role != "assistant":
        raise ModelProtocolError("provider returned an invalid role")
    _validate_content(message)
    parts: list[TextPart | ReasoningPart | ToolCallPart] = []
    reasoning = _reasoning_content(message)
    if reasoning:
        parts.append(ReasoningPart(text=reasoning))
    content = cast(object, message.content)
    if content is not None and not isinstance(content, str):
        raise ModelProtocolError("provider returned unsupported content")
    if content:
        parts.append(TextPart(text=content))
    parts.extend(_tool_calls(message))
    if not parts:
        raise ModelProtocolError("provider returned an empty response")
    finish_reason = cast(object, choice.finish_reason)
    if not isinstance(finish_reason, str):
        raise ModelProtocolError("provider omitted finish reason")
    fields: object = getattr(choice, "provider_specific_fields", None)
    if isinstance(fields, dict):
        native_finish_reason = cast(dict[str, object], fields).get(
            "native_finish_reason"
        )
        if isinstance(native_finish_reason, str):
            finish_reason = native_finish_reason
    usage = (
        _usage(
            getattr(value, "usage", None),
            reasoning or "",
            reported_reasoning_estimated=reported_reasoning_estimated,
        )
        or Usage()
    )
    return _model_response(
        parts=tuple(parts),
        usage=usage,
        finish_reason=_finish_reason(finish_reason),
        provider_response_id=value.id,
    )


def _model_response(
    *,
    parts: tuple[TextPart | ReasoningPart | ToolCallPart, ...],
    usage: Usage,
    finish_reason: FinishReason,
    provider_response_id: str | None,
) -> ModelResponse:
    """Normalise native tool calls whose provider reports a stop reason."""

    if finish_reason is FinishReason.STOP and any(
        isinstance(part, ToolCallPart) for part in parts
    ):
        finish_reason = FinishReason.TOOL_CALL
    return ModelResponse(
        parts=parts,
        usage=usage,
        finish_reason=finish_reason,
        provider_response_id=provider_response_id,
    )


def _validate_content(value: object) -> None:
    unsupported = (
        "function_call",
        "audio",
        "images",
        "thinking_blocks",
        "reasoning_items",
        "annotations",
    )
    if any(getattr(value, name, None) for name in unsupported):
        raise ModelProtocolError("provider returned unsupported content")


def _reasoning_content(value: object) -> str | None:
    reasoning = getattr(value, "reasoning_content", None)
    if reasoning is not None and not isinstance(reasoning, str):
        raise ModelProtocolError("provider returned unsupported reasoning")
    return reasoning


def _tool_calls(value: object) -> tuple[ToolCallPart, ...]:
    calls = getattr(value, "tool_calls", None)
    if calls is None:
        return ()
    if not isinstance(calls, list):
        raise ModelProtocolError("provider returned invalid tool calls")
    parts: list[ToolCallPart] = []
    call_ids: set[str] = set()
    for call in cast(list[object], calls):
        call_id = getattr(call, "id", None)
        call_type = getattr(call, "type", None)
        function = getattr(call, "function", None)
        name = getattr(function, "name", None)
        arguments = getattr(function, "arguments", None)
        if (
            not isinstance(call_id, str)
            or not call_id
            or call_type != "function"
            or not isinstance(name, str)
            or not isinstance(arguments, str)
            or call_id in call_ids
        ):
            raise ModelProtocolError("provider returned invalid tool calls")
        try:
            part = ToolCallPart(
                call_id=call_id,
                name=name,
                arguments_json=arguments,
            )
        except ValueError as error:
            raise ModelProtocolError(
                "provider returned invalid tool calls"
            ) from error
        call_ids.add(call_id)
        parts.append(part)
    return tuple(parts)


def _add_tool_call_deltas(
    value: object,
    calls: dict[int, _ToolCallAssembly],
    order: list[_TextAssembly | _ReasoningAssembly | _ToolCallAssembly],
) -> None:
    fragments = getattr(value, "tool_calls", None)
    if fragments is None:
        return
    if not isinstance(fragments, list):
        raise ModelProtocolError("provider returned invalid tool calls")
    for fragment in cast(list[object], fragments):
        index = getattr(fragment, "index", None)
        call_type = getattr(fragment, "type", None)
        if (
            not isinstance(index, int)
            or index < 0
            or call_type not in (None, "function")
        ):
            raise ModelProtocolError("provider returned invalid tool calls")
        call = calls.get(index)
        if call is None:
            call = _ToolCallAssembly(index=index)
            calls[index] = call
            order.append(call)

        call_id = getattr(fragment, "id", None)
        if call_id is not None:
            if (
                not isinstance(call_id, str)
                or not call_id
                or call.call_id not in (None, call_id)
            ):
                raise ModelProtocolError("provider changed tool call ID")
            call.call_id = call_id

        function = getattr(fragment, "function", None)
        if function is None:
            continue
        name = getattr(function, "name", None)
        if name is not None:
            if (
                not isinstance(name, str)
                or not name
                or call.name not in (None, name)
            ):
                raise ModelProtocolError("provider changed tool call name")
            call.name = name
        arguments = getattr(function, "arguments", None)
        if arguments is not None:
            if not isinstance(arguments, str):
                raise ModelProtocolError(
                    "provider returned invalid tool arguments"
                )
            call.arguments.append(arguments)


def _finalise_stream_parts(
    order: list[_TextAssembly | _ReasoningAssembly | _ToolCallAssembly],
) -> tuple[TextPart | ReasoningPart | ToolCallPart, ...]:
    parts: list[TextPart | ReasoningPart | ToolCallPart] = []
    call_ids: set[str] = set()
    for item in order:
        if isinstance(item, _TextAssembly):
            parts.append(TextPart(text="".join(item.chunks)))
            continue
        if isinstance(item, _ReasoningAssembly):
            parts.append(ReasoningPart(text="".join(item.chunks)))
            continue
        if item.call_id is None or item.name is None:
            raise ModelProtocolError("provider omitted tool call fields")
        if item.call_id in call_ids:
            raise ModelProtocolError("provider duplicated tool call ID")
        try:
            part = ToolCallPart(
                call_id=item.call_id,
                name=item.name,
                arguments_json="".join(item.arguments),
            )
        except ValueError as error:
            raise ModelProtocolError(
                "provider returned invalid tool calls"
            ) from error
        call_ids.add(item.call_id)
        parts.append(part)
    if not parts:
        raise ModelProtocolError("provider returned an empty response")
    return tuple(parts)


def _validate_delta(value: object) -> None:
    role = getattr(value, "role", None)
    if role not in (None, "assistant"):
        raise ModelProtocolError("provider returned an invalid role")
    _validate_content(value)
    _reasoning_content(value)


def _usage(
    value: object,
    reasoning: str = "",
    *,
    reported_reasoning_estimated: bool = False,
) -> Usage | None:
    if value is None:
        return None
    if not isinstance(value, _LiteLLMUsage):
        raise ModelProtocolError("provider returned invalid usage")
    try:
        reasoning_tokens, estimated = _reasoning_usage(
            value,
            reasoning,
            value.completion_tokens,
            reported_reasoning_estimated,
        )
        return Usage(
            input_tokens=value.prompt_tokens,
            output_tokens=value.completion_tokens,
            reasoning_tokens=reasoning_tokens,
            reasoning_tokens_estimated=estimated,
        )
    except ValueError as error:
        raise ModelProtocolError("provider returned invalid usage") from error


def _reasoning_usage(
    value: object,
    reasoning: str,
    output_tokens: int,
    reported_reasoning_estimated: bool,
) -> tuple[int, bool]:
    details = getattr(value, "completion_tokens_details", None)
    reported = getattr(details, "reasoning_tokens", None)
    if reported is not None:
        if (
            isinstance(reported, bool)
            or not isinstance(reported, int)
            or reported < 0
            or reported > output_tokens
        ):
            raise ValueError("invalid reasoning token usage")
        return reported, reported_reasoning_estimated
    if not reasoning:
        return 0, False
    # Four UTF-8 bytes per token is a provider-neutral display estimate.
    estimated = min(output_tokens, (len(reasoning.encode("utf-8")) + 3) // 4)
    return estimated, True


def _finish_reason(value: str) -> FinishReason:
    """Collapse provider-specific terminal reasons into the Ethos vocabulary."""

    return {
        "stop": FinishReason.STOP,
        "eos": FinishReason.STOP,
        "length": FinishReason.LENGTH,
        "tool_calls": FinishReason.TOOL_CALL,
        "function_call": FinishReason.TOOL_CALL,
        "content_filter": FinishReason.CONTENT_FILTER,
        "guardrail_intervened": FinishReason.CONTENT_FILTER,
    }.get(value, FinishReason.OTHER)


def _provider_error(
    action: str,
    error: Exception,
    provider: ProviderName,
) -> ModelProviderError:
    if isinstance(error, LiteLLMAPIConnectionError):
        name = {
            ProviderName.OPENAI: "OpenAI",
            ProviderName.GOOGLE: "Google",
            ProviderName.OLLAMA: "Ollama",
        }[provider]
        connection_reason = f"could not connect to {name}"
        if provider is ProviderName.OLLAMA:
            connection_reason += "; make sure Ollama is running"
        return ModelProviderError(
            f"model provider {action} failed: {connection_reason}"
        )
    reason = _safe_capability_error(error)
    detail = f": {reason}" if reason is not None else ""
    return ModelProviderError(f"model provider {action} failed{detail}")


def _safe_capability_error(error: Exception) -> str | None:
    """Allow only one bounded provider detail known not to contain secrets."""

    message = getattr(error, "message", None)
    if not isinstance(message, str):
        return None
    _prefix, separator, encoded = message.rpartition(" - ")
    if not separator:
        return None
    try:
        payload = cast(object, json.loads(encoded))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    reason = cast(dict[str, object], payload).get("error")
    if (
        not isinstance(reason, str)
        or len(reason) > 200
        or "\n" in reason
        or "\r" in reason
        or not reason.endswith(" does not support thinking")
    ):
        return None
    return reason
