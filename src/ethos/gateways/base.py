"""Transport adapter lifecycle and shared command-executor contract.

See ``docs/development/commands-events-and-gateways.md`` for gateway ownership
and process behaviour.
"""

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable, Iterable

from ethos.commands import CommandDispatcher, CommandRequest, CommandResponse

type CommandExecutor = Callable[
    [CommandRequest], AsyncIterator[CommandResponse]
]


class Gateway(ABC):
    """Translate native I/O without reimplementing universal commands."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return this gateway's stable name."""

    def register_commands(self, dispatcher: CommandDispatcher) -> None:
        """Register commands owned by this gateway."""
        return None

    @abstractmethod
    async def run(self, execute: CommandExecutor) -> None:
        """Run until shutdown, using execute for Ethos commands."""


async def run_gateways(
    gateways: Iterable[Gateway], execute: CommandExecutor
) -> None:
    """Run gateways concurrently and cancel siblings when one fails.

    Task-group exit waits for sibling cleanup before propagating failures.
    """
    async with asyncio.TaskGroup() as tasks:
        for gateway in gateways:
            tasks.create_task(
                gateway.run(execute), name=f"gateway:{gateway.name}"
            )
