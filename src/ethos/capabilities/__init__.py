"""Run-scoped instructions and tools contributed to the model runtime."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ethos.tools import Tool


@dataclass(frozen=True)
class RunContext:
    """Trusted, run-scoped identity available to capability contributors.

    Persona fields support capability filtering and event correlation. The
    context builder intentionally exposes only workspace and session fields to
    the model; persona identity arrives through its dedicated instruction.
    """

    workspace_name: str
    workspace_path: Path
    session_id: str
    assigned_persona: str = "ethos"
    effective_persona: str = "ethos"
    persona_fallback: bool = False
    persona_capabilities: tuple[str, ...] | None = None


class Capability(Protocol):
    """Contribute transient instructions and per-run tool instances."""

    async def instructions(self, context: RunContext) -> tuple[str, ...]: ...

    async def tools(self, context: RunContext) -> tuple[Tool, ...]: ...
