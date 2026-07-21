"""Gateway selection and graceful process lifetime."""

import asyncio
import signal
from collections.abc import Iterable, Sequence
from contextlib import suppress
from ipaddress import ip_address
from typing import Literal

from ethos.config import GatewaysConfig
from ethos.gateways.base import CommandExecutor, Gateway, run_gateways

type GatewayName = Literal["vox", "discord"]


class GatewayConfigurationError(ValueError):
    """Raised when no runnable gateways are configured."""


def _vox_is_configured(config: GatewaysConfig) -> bool:
    try:
        loopback = ip_address(config.vox.host).is_loopback
    except ValueError:
        loopback = config.vox.host == "localhost"
    return loopback or config.vox.bearer_token is not None


def resolve_gateway_selection(
    config: GatewaysConfig,
    requested: Sequence[GatewayName] = (),
) -> tuple[GatewayName, ...]:
    """Select explicit gateways or every enabled gateway, then preflight."""
    selected = tuple(requested)
    if not selected:
        enabled: list[GatewayName] = []
        if config.vox.enabled:
            enabled.append("vox")
        if config.discord.enabled:
            enabled.append("discord")
        selected = tuple(enabled)
    if not selected:
        raise GatewayConfigurationError(
            "no gateways are enabled; select --vox or --discord"
        )
    if "vox" in selected and not _vox_is_configured(config):
        raise GatewayConfigurationError(
            "vox requires a bearer token when exposed beyond loopback"
        )
    if "discord" in selected and config.discord.token is None:
        raise GatewayConfigurationError("discord requires a bot token")
    return selected


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
