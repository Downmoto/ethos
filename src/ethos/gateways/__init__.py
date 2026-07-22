"""Long-running adapters for Ethos commands."""

from ethos.gateways.base import CommandExecutor, Gateway, run_gateways
from ethos.gateways.discord import DiscordGateway
from ethos.gateways.supervisor import (
    GatewaySupervisor,
    SupervisorAlreadyRunning,
    SupervisorNotRunning,
    running_gateways,
    stop_gateways,
    supervisor_status,
)
from ethos.gateways.vox import VoxGateway

__all__ = [
    "CommandExecutor",
    "DiscordGateway",
    "Gateway",
    "GatewaySupervisor",
    "SupervisorAlreadyRunning",
    "SupervisorNotRunning",
    "VoxGateway",
    "run_gateways",
    "running_gateways",
    "stop_gateways",
    "supervisor_status",
]
