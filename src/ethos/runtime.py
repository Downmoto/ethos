"""Conversation turns over stored workspace sessions.

See ``docs/development/workspaces-and-runtime.md`` for how session persistence
and runtime concurrency compose.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from ethos.config import get_settings
from ethos.models import (
    Message,
    Model,
    ModelRequest,
    ModelResponse,
    Role,
    TextDelta,
    TextPart,
    Usage,
)
from ethos.provider import AIProvider, ModelProtocolError
from ethos.sessions import SessionManager

type ModelFactory = Callable[[], Model]


@dataclass(frozen=True)
class PromptStreamEvent:
    """Provider-neutral prompt text and usage update."""

    text: str = ""
    usage: Usage | None = None
    done: bool = False


class AgentRuntime:
    """Run isolated conversations through Ethos model contracts.

    Conversation history belongs to sessions, not models. Locks serialise
    turns per session within this runtime instance; they do not coordinate
    separate processes or runtimes.
    """

    def __init__(
        self,
        sessions: SessionManager,
        model_factory: ModelFactory | None = None,
    ) -> None:
        self._sessions = sessions
        self._model_factory = model_factory or _model_from_settings
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def run(
        self,
        prompt: str,
        workspace_name: str,
        session_id: str,
    ) -> AsyncIterator[PromptStreamEvent]:
        """Stream and commit one turn for an active session.

        Text may be yielded before the turn is durable. History is replaced
        only after the model stream finishes normally and validates, and
        ``done=True`` is yielded only after that replacement succeeds.
        Cancelling or abandoning the iterator before then leaves the previous
        history intact.
        """
        key = (workspace_name, session_id)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            session = self._sessions.get(workspace_name, session_id)
            if session.archived:
                raise ValueError(f"session is archived: {session_id}")

            user_message = Message(
                role=Role.USER,
                parts=(TextPart(text=prompt),),
            )
            request = ModelRequest(
                messages=(*session.messages, user_message),
            )
            streamed_text = ""
            completed: ModelResponse | None = None

            async for event in self._model_factory().stream(request):
                if completed is not None:
                    raise ModelProtocolError("model streamed after completion")
                if isinstance(event, TextDelta):
                    streamed_text += event.text
                    if event.text:
                        yield PromptStreamEvent(text=event.text)
                else:
                    completed = event.response

            if completed is None:
                raise ModelProtocolError("model stream ended before completion")
            assistant_message = _assistant_message(completed, streamed_text)

            self._sessions.replace_messages(
                workspace_name,
                session_id,
                (*session.messages, user_message, assistant_message),
            )

            yield PromptStreamEvent(usage=completed.usage, done=True)


def _model_from_settings() -> Model:
    settings = get_settings()
    provider = AIProvider.from_settings(settings)
    return provider.model(settings.provider.model_name)


def _assistant_message(response: ModelResponse, streamed_text: str) -> Message:
    parts = tuple(part for part in response.parts if isinstance(part, TextPart))
    if len(parts) != len(response.parts):
        raise ModelProtocolError("text runtime received unsupported parts")
    if "".join(part.text for part in parts) != streamed_text:
        raise ModelProtocolError("model completion did not match stream")
    return Message(role=Role.ASSISTANT, parts=parts)
