"""Test doubles shared by model and runtime tests."""

from collections import deque
from collections.abc import AsyncIterator, Sequence

from ethos.models import (
    ModelEvent,
    ModelFeatures,
    ModelRequest,
    ModelResponse,
    ReasoningDelta,
    ReasoningPart,
    ResponseCompleted,
    TextDelta,
    TextPart,
)
from ethos.sandbox import (
    SandboxEvent,
    SandboxExecution,
    SandboxRequest,
    SandboxResult,
)


class FakeModel:
    """Return queued outcomes while recording requests."""

    def __init__(
        self,
        outcomes: Sequence[ModelResponse | Exception],
        *,
        stream_chunks: Sequence[tuple[str, ...]] | None = None,
        reasoning_chunks: Sequence[tuple[str, ...]] | None = None,
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
        reasoning = (
            reasoning_chunks
            if reasoning_chunks is not None
            else [() for _ in outcomes]
        )
        if len(reasoning) != len(outcomes):
            raise ValueError("each fake outcome requires reasoning chunks")
        self._outcomes = deque(zip(outcomes, chunks, reasoning, strict=True))
        self.requests: list[ModelRequest] = []

    async def request(self, request: ModelRequest) -> ModelResponse:
        outcome, _chunks, _reasoning_chunks = self._next(request)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        outcome, chunks, reasoning_chunks = self._next(request)
        if isinstance(outcome, Exception):
            raise outcome
        expected = "".join(
            part.text for part in outcome.parts if isinstance(part, TextPart)
        )
        if "".join(chunks) != expected:
            raise AssertionError(
                "fake stream chunks do not match response text"
            )
        expected_reasoning = "".join(
            part.text
            for part in outcome.parts
            if isinstance(part, ReasoningPart)
        )
        if "".join(reasoning_chunks) != expected_reasoning:
            raise AssertionError(
                "fake reasoning chunks do not match response reasoning"
            )
        for chunk in reasoning_chunks:
            yield ReasoningDelta(text=chunk)
        for chunk in chunks:
            yield TextDelta(text=chunk)
        yield ResponseCompleted(response=outcome)

    def _next(
        self, request: ModelRequest
    ) -> tuple[
        ModelResponse | Exception,
        tuple[str, ...],
        tuple[str, ...],
    ]:
        self.requests.append(request)
        if not self._outcomes:
            raise AssertionError("fake model has no queued outcomes")
        return self._outcomes.popleft()


class FakeSandboxProvider:
    """Return queued sandbox executions while recording requests."""

    def __init__(self, executions: Sequence[SandboxExecution]) -> None:
        self._executions = deque(executions)
        self.requests: list[SandboxRequest] = []

    async def start(self, request: SandboxRequest) -> SandboxExecution:
        self.requests.append(request)
        if not self._executions:
            raise AssertionError("fake sandbox has no queued execution")
        return self._executions.popleft()


class FakeSandboxExecution:
    """Replay raw events with an independently configurable cancellation."""

    def __init__(
        self,
        events: Sequence[SandboxEvent | Exception],
        *,
        cancel_result: SandboxResult,
    ) -> None:
        self._events = events
        self._cancel_result = cancel_result
        self.cancel_calls = 0
        self.close_calls = 0

    async def events(self) -> AsyncIterator[SandboxEvent]:
        for event in self._events:
            if isinstance(event, Exception):
                raise event
            yield event

    async def cancel(self) -> SandboxResult:
        self.cancel_calls += 1
        return self._cancel_result

    async def aclose(self) -> None:
        self.close_calls += 1
