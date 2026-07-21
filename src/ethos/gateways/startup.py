"""Gateway selection and graceful process lifetime."""

import asyncio
import signal
from collections.abc import Iterable
from contextlib import suppress

from ethos.gateways.base import CommandExecutor, Gateway, run_gateways


async def run_until_shutdown(
    gateways: Iterable[Gateway],
    execute: CommandExecutor,
    *,
    shutdown: asyncio.Event | None = None,
) -> None:
    """Run gateways until they finish, fail, or receive a shutdown signal."""
    loop = asyncio.get_running_loop()
    stop = shutdown or asyncio.Event()
    installed_signals: list[signal.Signals] = []
    if shutdown is None:
        for process_signal in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(process_signal, stop.set)
            except NotImplementedError:
                continue
            installed_signals.append(process_signal)

    running = asyncio.create_task(run_gateways(gateways, execute))
    stopping = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait(
            (running, stopping), return_when=asyncio.FIRST_COMPLETED
        )
        if running in done:
            await running
    finally:
        stopping.cancel()
        running.cancel()
        with suppress(asyncio.CancelledError):
            await stopping
        with suppress(asyncio.CancelledError):
            await running
        for process_signal in installed_signals:
            loop.remove_signal_handler(process_signal)
