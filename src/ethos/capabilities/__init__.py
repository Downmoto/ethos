"""Run-scoped instructions and tools contributed to the model runtime."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ethos.tools import Tool


@dataclass(frozen=True)
class RunContext:
    workspace_name: str
    workspace_path: Path
    session_id: str


class Capability(Protocol):
    async def instructions(self, context: RunContext) -> tuple[str, ...]: ...

    async def tools(self, context: RunContext) -> tuple[Tool, ...]: ...
