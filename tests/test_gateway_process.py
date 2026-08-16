import asyncio
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from ethos.gateway.process import (
    PID_FILE,
    SOCKET_FILE,
    background_pid,
    run_background,
    stop_background,
)


@pytest.fixture
def short_home() -> Iterator[Path]:
    with TemporaryDirectory(prefix="ethos-", dir="/tmp") as directory:
        yield Path(directory)


def test_background_server_is_discoverable_and_stoppable(
    short_home: Path,
) -> None:
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def serve() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async def exercise() -> None:
        running = asyncio.create_task(run_background(short_home, serve))
        await started.wait()

        pid = await asyncio.to_thread(background_pid, short_home)
        assert pid is not None
        assert await asyncio.to_thread(stop_background, short_home)
        await running

    asyncio.run(exercise())

    assert stopped.is_set()
    assert not (short_home / PID_FILE).exists()
    assert not (short_home / SOCKET_FILE).exists()


def test_stop_is_noop_without_background_server(short_home: Path) -> None:
    assert not stop_background(short_home)
