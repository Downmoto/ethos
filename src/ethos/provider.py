"""Model providers supported by ethos."""

import os
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

from pydantic import SecretStr

# ponytail: Ethos does not use LiteLLM pricing, so keep imports offline.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from litellm import ModelResponse as LiteLLMResponse  # noqa: E402
from litellm import ModelResponseStream as LiteLLMStreamChunk  # noqa: E402
from litellm import (
    acompletion,  # pyright: ignore[reportUnknownVariableType]  # noqa: E402
)

from ethos.models import (  # noqa: E402
    FinishReason,
    Message,
    Model,
    ModelEvent,
    ModelRequest,
    ModelResponse,
    ResponseCompleted,
    Role,
    TextDelta,
    TextPart,
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
    """Create Ethos models using one provider credential."""

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

    def model(self, model_name: str) -> Model:
        return LiteLLMModel(self, model_name)


class ModelProviderError(RuntimeError):
    """A provider call failed without exposing provider details."""


class ModelProtocolError(RuntimeError):
    """A provider returned data outside the Ethos model contract."""


@dataclass(frozen=True)
class LiteLLMModel:
    """Translate between Ethos text models and LiteLLM chat completions."""

    provider: AIProvider
    model_name: str
    completion: CompletionCall = dataclass_field(
        default=cast(CompletionCall, acompletion),
        repr=False,
        compare=False,
    )

    async def request(self, request: ModelRequest) -> ModelResponse:
        kwargs = self._kwargs(request, stream=False)
        try:
            result = await self.completion(**kwargs)
        except Exception as error:
            raise ModelProviderError("model provider request failed") from error
        if not isinstance(result, LiteLLMResponse):
            raise ModelProtocolError("provider returned an invalid response")
        return _response(result)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        kwargs = self._kwargs(request, stream=True)
        try:
            result = await self.completion(**kwargs)
        except Exception as error:
            raise ModelProviderError("model provider request failed") from error
        if not isinstance(result, AsyncIterable):
            raise ModelProtocolError("provider returned an invalid stream")

        text = ""
        usage = Usage()
        finish_reason: FinishReason | None = None
        response_id: str | None = None
        finished = False
        try:
            async for value in cast(AsyncIterable[object], result):
                if not isinstance(value, LiteLLMStreamChunk):
                    raise ModelProtocolError(
                        "provider returned an invalid chunk"
                    )
                chunk_usage = _usage(getattr(value, "usage", None))
                if chunk_usage is not None:
                    usage = chunk_usage
                if not value.choices:
                    if chunk_usage is None:
                        raise ModelProtocolError(
                            "provider returned an empty chunk"
                        )
                    continue
                if finished:
                    if chunk_usage is None or len(value.choices) != 1:
                        raise ModelProtocolError(
                            "provider streamed after completion"
                        )
                    final_choice = value.choices[0]
                    _validate_delta(final_choice.delta)
                    if (
                        final_choice.index != 0
                        or final_choice.delta.content
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
                content = cast(object, delta.content)
                if content is not None and not isinstance(content, str):
                    raise ModelProtocolError(
                        "provider returned unsupported content"
                    )
                if content:
                    text += content
                    yield TextDelta(text=content)
                if choice.finish_reason is not None:
                    finish_reason = _finish_reason(choice.finish_reason)
                    finished = True
        except ModelProtocolError:
            raise
        except Exception as error:
            raise ModelProviderError("model provider stream failed") from error

        if not finished or finish_reason is None:
            raise ModelProtocolError("provider stream ended before completion")
        if not text:
            raise ModelProtocolError("provider returned empty text")
        yield ResponseCompleted(
            response=ModelResponse(
                parts=(TextPart(text=text),),
                usage=usage,
                finish_reason=finish_reason,
                provider_response_id=response_id,
            )
        )

    def _kwargs(
        self, request: ModelRequest, *, stream: bool
    ) -> dict[str, object]:
        if request.tools:
            raise ModelProtocolError("text model does not support tools")
        model = f"{_provider_prefix(self.provider.name)}/{self.model_name}"
        kwargs: dict[str, object] = {
            "model": model,
            "messages": [_message(message) for message in request.messages],
            "stream": stream,
        }
        if stream:
            kwargs["stream_options"] = {"include_usage": True}
        if self.provider.api_key is not None:
            kwargs["api_key"] = self.provider.api_key.get_secret_value()
        if self.provider.name is ProviderName.OLLAMA:
            kwargs["base_url"] = self.provider.ollama_base_url
        return kwargs


def _provider_prefix(provider: ProviderName) -> str:
    return {
        ProviderName.OPENAI: "openai",
        ProviderName.GOOGLE: "gemini",
        ProviderName.OLLAMA: "ollama",
    }[provider]


def _message(message: Message) -> dict[str, str]:
    if message.role is Role.TOOL:
        raise ModelProtocolError(
            "text model received unsupported message parts"
        )
    text: list[str] = []
    for part in message.parts:
        if not isinstance(part, TextPart):
            raise ModelProtocolError(
                "text model received unsupported message parts"
            )
        text.append(part.text)
    return {
        "role": message.role.value,
        "content": "".join(text),
    }


def _response(value: LiteLLMResponse) -> ModelResponse:
    if len(value.choices) != 1:
        raise ModelProtocolError("provider returned multiple choices")
    choice = value.choices[0]
    if choice.index != 0:
        raise ModelProtocolError("provider returned an invalid choice")
    message = choice.message
    if message.role != "assistant":
        raise ModelProtocolError("provider returned an invalid role")
    _validate_content(message)
    if not isinstance(message.content, str) or not message.content:
        raise ModelProtocolError("provider returned empty text")
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
    usage = _usage(getattr(value, "usage", None)) or Usage()
    return ModelResponse(
        parts=(TextPart(text=message.content),),
        usage=usage,
        finish_reason=_finish_reason(finish_reason),
        provider_response_id=value.id,
    )


def _validate_content(value: object) -> None:
    unsupported = (
        "tool_calls",
        "function_call",
        "audio",
        "images",
        "reasoning_content",
        "thinking_blocks",
        "reasoning_items",
        "annotations",
    )
    if any(getattr(value, name, None) for name in unsupported):
        raise ModelProtocolError("provider returned unsupported content")


def _validate_delta(value: object) -> None:
    role = getattr(value, "role", None)
    if role not in (None, "assistant"):
        raise ModelProtocolError("provider returned an invalid role")
    _validate_content(value)


def _usage(value: object) -> Usage | None:
    if value is None:
        return None
    if not isinstance(value, _LiteLLMUsage):
        raise ModelProtocolError("provider returned invalid usage")
    try:
        return Usage(
            input_tokens=value.prompt_tokens,
            output_tokens=value.completion_tokens,
        )
    except ValueError as error:
        raise ModelProtocolError("provider returned invalid usage") from error


def _finish_reason(value: str) -> FinishReason:
    return {
        "stop": FinishReason.STOP,
        "eos": FinishReason.STOP,
        "length": FinishReason.LENGTH,
        "tool_calls": FinishReason.TOOL_CALL,
        "function_call": FinishReason.TOOL_CALL,
        "content_filter": FinishReason.CONTENT_FILTER,
        "guardrail_intervened": FinishReason.CONTENT_FILTER,
    }.get(value, FinishReason.OTHER)
