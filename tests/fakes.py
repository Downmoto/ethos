"""Test doubles shared by model and runtime tests."""

from collections import deque
from collections.abc import AsyncIterator, Sequence

from ethos.models import (
    ModelEvent,
    ModelFeatures,
    ModelRequest,
    ModelResponse,
    ResponseCompleted,
    TextDelta,
    TextPart,
)


class FakeModel:
    """Return queued outcomes while recording requests."""

    def __init__(
        self,
        outcomes: Sequence[ModelResponse | Exception],
        *,
        stream_chunks: Sequence[tuple[str, ...]] | None = None,
        features: ModelFeatures | None = None,
    ) -> None:
        self.features = features or ModelFeatures(tools=False)
        chunks = (
            stream_chunks
            if stream_chunks is not None
            else [() for _ in outcomes]
        )
        if len(chunks) != len(outcomes):
            raise ValueError("each fake outcome requires one chunk sequence")
        self._outcomes = deque(zip(outcomes, chunks, strict=True))
        self.requests: list[ModelRequest] = []

    async def request(self, request: ModelRequest) -> ModelResponse:
        outcome, _chunks = self._next(request)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        outcome, chunks = self._next(request)
        if isinstance(outcome, Exception):
            raise outcome
        expected = "".join(
            part.text for part in outcome.parts if isinstance(part, TextPart)
        )
        if "".join(chunks) != expected:
            raise AssertionError(
                "fake stream chunks do not match response text"
            )
        for chunk in chunks:
            yield TextDelta(text=chunk)
        yield ResponseCompleted(response=outcome)

    def _next(
        self, request: ModelRequest
    ) -> tuple[ModelResponse | Exception, tuple[str, ...]]:
        self.requests.append(request)
        if not self._outcomes:
            raise AssertionError("fake model has no queued outcomes")
        return self._outcomes.popleft()
