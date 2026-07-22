import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from ethos.commands import CommandRequest, CommandResponse
from ethos.gateways import (
    CommandExecutor,
    Gateway,
    GatewaySupervisor,
    SupervisorAlreadyRunning,
    running_gateways,
    stop_gateways,
)
from ethos.gateways.supervisor import PID_FILE, SOCKET_FILE


@pytest.fixture
def short_home() -> Iterator[Path]:
    with TemporaryDirectory(prefix="ethos-", dir="/tmp") as directory:
        yield Path(directory)


class WaitingGateway(Gateway):
    def __init__(
        self, name: str, started: asyncio.Event, stopped: asyncio.Event
    ) -> None:
        self._name = name
        self._started = started
        self._stopped = stopped

    @property
    def name(self) -> str:
        return self._name

    async def run(self, execute: CommandExecutor) -> None:
        self._started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self._stopped.set()


async def execute(
    _request: CommandRequest,
) -> AsyncIterator[CommandResponse]:
    yield CommandResponse()


def test_supervisor_selectively_stops_gateways(short_home: Path) -> None:
    first_started = asyncio.Event()
    first_stopped = asyncio.Event()
    second_started = asyncio.Event()
    second_stopped = asyncio.Event()
    supervisor = GatewaySupervisor(
        short_home,
        (
            WaitingGateway("first", first_started, first_stopped),
            WaitingGateway("second", second_started, second_stopped),
        ),
    )

    async def run_and_stop() -> None:
        running = asyncio.create_task(supervisor.run(execute))
        await asyncio.gather(first_started.wait(), second_started.wait())

        assert await asyncio.to_thread(running_gateways, short_home) == (
            "first",
            "second",
        )
        assert await asyncio.to_thread(
            stop_gateways, short_home, ("first",)
        ) == ("first",)
        assert first_stopped.is_set()
        assert not second_stopped.is_set()
        assert not running.done()

        assert await asyncio.to_thread(stop_gateways, short_home) == ("second",)
        await running

    asyncio.run(run_and_stop())

    assert second_stopped.is_set()
    assert not (short_home / PID_FILE).exists()
    assert not (short_home / SOCKET_FILE).exists()


def test_supervisor_rejects_second_process(short_home: Path) -> None:
    started = asyncio.Event()
    stopped = asyncio.Event()
    first = GatewaySupervisor(
        short_home, (WaitingGateway("first", started, stopped),)
    )
    second = GatewaySupervisor(
        short_home,
        (WaitingGateway("second", asyncio.Event(), asyncio.Event()),),
    )

    async def run_both() -> None:
        running = asyncio.create_task(first.run(execute))
        await started.wait()

        with pytest.raises(SupervisorAlreadyRunning, match="already running"):
            await second.run(execute)

        await asyncio.to_thread(stop_gateways, short_home)
        await running

    asyncio.run(run_both())

    assert stopped.is_set()


def test_supervisor_replaces_stale_runtime_files(short_home: Path) -> None:
    runtime = short_home / "runtime"
    runtime.mkdir()
    (short_home / PID_FILE).write_text("999999\n")
    (short_home / SOCKET_FILE).touch()
    started = asyncio.Event()
    supervisor = GatewaySupervisor(
        short_home, (WaitingGateway("test", started, asyncio.Event()),)
    )

    async def run_and_stop() -> None:
        running = asyncio.create_task(supervisor.run(execute))
        await started.wait()
        assert await asyncio.to_thread(running_gateways, short_home) == (
            "test",
        )
        await asyncio.to_thread(stop_gateways, short_home)
        await running

    asyncio.run(run_and_stop())
