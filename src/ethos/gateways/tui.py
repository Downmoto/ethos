"""Foreground Textual adapter for local Ethos commands."""

import getpass
from pathlib import Path

from ethos.gateways.base import CommandExecutor, Gateway
from ethos.tui import EthosTui


class TuiGateway(Gateway):
    """Run the local Textual interface until the user quits."""

    @property
    def name(self) -> str:
        return "tui"

    async def run(self, execute: CommandExecutor) -> None:
        await EthosTui(
            execute,
            owner_id=getpass.getuser(),
            cwd=Path.cwd(),
        ).run_async()
