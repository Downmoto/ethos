from collections.abc import Iterator
from contextlib import contextmanager

from openai_codex import TurnResult
from openai_codex.generated.v2_all import TurnStatus

from providers import CodexProvider


class FakeThread:
    def run(self, input: str, **kwargs: object) -> TurnResult:
        return TurnResult(
            id="turn-id",
            status=TurnStatus.completed,
            error=None,
            started_at=None,
            completed_at=None,
            duration_ms=None,
            final_response=f"response to {input}",
            items=[],
            usage=None,
        )


class FakeCodex:
    def thread_start(self, **kwargs: object) -> FakeThread:
        return FakeThread()


@contextmanager
def fake_codex_factory() -> Iterator[FakeCodex]:
    yield FakeCodex()


def test_codex_provider_returns_final_response() -> None:
    provider = CodexProvider(fake_codex_factory)

    assert provider.invoke("hello") == "response to hello"
