"""Gateway lifecycle contract and concurrent runner."""

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable, Iterable

from ethos.commands import CommandRequest, CommandResponse

type CommandExecutor = Callable[
    [CommandRequest], AsyncIterator[CommandResponse]
]


class Gateway(ABC):
    """Translate native input and render command responses."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return this gateway's stable name."""

    @abstractmethod
    async def run(self, execute: CommandExecutor) -> None:
        """Run until shutdown, using execute for Ethos commands."""


async def run_gateways(
    gateways: Iterable[Gateway], execute: CommandExecutor
) -> None:
    """Run gateways concurrently and cancel siblings when one fails."""
    async with asyncio.TaskGroup() as tasks:
        for gateway in gateways:
            tasks.create_task(
                gateway.run(execute), name=f"gateway:{gateway.name}"
            )
