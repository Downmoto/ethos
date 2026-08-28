"""Provider-independent, bounded sandbox process execution."""

from __future__ import annotations

import math
import os
import sys
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, cast, runtime_checkable


class SandboxError(RuntimeError):
    """A sandbox could not safely prepare or execute a process."""


class SandboxUnavailableError(SandboxError):
    """The platform sandbox cannot establish the required isolation."""


class SandboxLaunchError(SandboxError):
    """The sandbox failed before a child process was started."""


class SandboxStream(StrEnum):
    """The child pipe that produced an output fragment."""

    STDOUT = "stdout"
    STDERR = "stderr"


class SandboxTerminalReason(StrEnum):
    """Why supervision ended, independent of command success semantics."""

    EXITED = "exited"
    TIMED_OUT = "timed_out"
    OUTPUT_LIMIT_EXCEEDED = "output_limit_exceeded"
    CANCELLED = "cancelled"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class SandboxResult:
    """The one terminal outcome for an execution."""

    reason: SandboxTerminalReason
    exit_code: int | None = None

    def __post_init__(self) -> None:
        has_exit_code = self.exit_code is not None
        if has_exit_code != (self.reason is SandboxTerminalReason.EXITED):
            raise ValueError("only an exited result has an exit code")


@dataclass(frozen=True)
class SandboxOutputEvent:
    """A non-empty raw fragment from one child output pipe."""

    stream: SandboxStream
    data: bytes

    def __post_init__(self) -> None:
        if not self.data:
            raise ValueError("sandbox output must not be empty")


@dataclass(frozen=True)
class SandboxCompletedEvent:
    """The final stream event for ordinary event consumption."""

    result: SandboxResult


type SandboxEvent = SandboxOutputEvent | SandboxCompletedEvent


@dataclass(frozen=True)
class SandboxRequest:
    """A validated request for one bounded, non-interactive process.

    Paths must already be canonical so providers can safely interpolate or
    mount them without reinterpreting symlinks. ``environment`` is the entire
    child environment, not an overlay on the Ethos process environment.
    """

    argv: tuple[str, ...]
    working_directory: Path
    workspace_path: Path
    temporary_path: Path
    environment: Mapping[str, str] = field(compare=False)
    timeout_seconds: float
    max_output_bytes: int

    def __post_init__(self) -> None:
        _validate_argv(self.argv)
        environment = _validate_environment(self.environment)
        # Frozen dataclasses do not make a caller-owned mapping immutable.
        object.__setattr__(
            self, "environment", MappingProxyType(dict(environment))
        )

        workspace = _canonical_directory(self.workspace_path, "workspace")
        working = _canonical_directory(
            self.working_directory, "working directory"
        )
        temporary = _canonical_directory(
            self.temporary_path, "temporary directory"
        )
        if not working.is_relative_to(workspace):
            raise ValueError("working directory must be inside the workspace")
        if temporary.is_relative_to(workspace):
            raise ValueError(
                "temporary directory must be outside the workspace"
            )
        if not os.access(workspace, os.W_OK) or not os.access(
            temporary, os.W_OK
        ):
            raise ValueError(
                "workspace and temporary directory must be writable"
            )
        _validate_limits(self.timeout_seconds, self.max_output_bytes)


@runtime_checkable
class SandboxExecution(Protocol):
    """A started process whose output and cleanup remain caller-controlled."""

    def events(self) -> AsyncIterator[SandboxEvent]: ...

    async def cancel(self) -> SandboxResult: ...

    async def aclose(self) -> None: ...


@runtime_checkable
class SandboxProvider(Protocol):
    """Prepare native isolation and start one sandboxed process."""

    async def start(self, request: SandboxRequest) -> SandboxExecution: ...


async def resolve_sandbox_provider() -> SandboxProvider:
    """Return the usable native sandbox provider for this platform."""

    if sys.platform == "darwin":
        from ethos.sandbox.seatbelt import SeatbeltSandboxProvider

        provider = SeatbeltSandboxProvider()
    elif sys.platform.startswith("linux"):
        from ethos.sandbox.bubblewrap import BubblewrapSandboxProvider

        provider = BubblewrapSandboxProvider()
    else:
        raise SandboxUnavailableError(
            f"sandbox execution is unsupported on {sys.platform}"
        )
    await provider.check_available()
    return provider


def _canonical_directory(path: object, label: str) -> Path:
    if not isinstance(path, Path):
        raise ValueError(f"{label} must be a path")
    if not path.is_absolute():
        raise ValueError(f"{label} must be absolute")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError(f"{label} must exist") from error
    if resolved != path:
        raise ValueError(f"{label} must be canonical and contain no symlinks")
    if not path.is_dir():
        raise ValueError(f"{label} must be a directory")
    return resolved


def _validate_argv(value: object) -> None:
    if not isinstance(value, tuple) or not value:
        raise ValueError("argv must not be empty")
    for argument in cast(tuple[object, ...], value):
        if not isinstance(argument, str):
            raise ValueError("argv must contain only strings")
        if "\0" in argument:
            raise ValueError("argv must not contain NUL bytes")


def _validate_environment(value: object) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError("environment must be a mapping")
    mapping = cast(Mapping[object, object], value)
    for name, item in mapping.items():
        if not isinstance(name, str) or not isinstance(item, str):
            raise ValueError("environment must contain only strings")
        if not name or "=" in name or "\0" in name:
            raise ValueError("environment contains an invalid name")
        if "\0" in item:
            raise ValueError("environment contains an invalid value")
    return cast(Mapping[str, str], mapping)


def _validate_limits(timeout: object, output_limit: object) -> None:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeout_seconds must be positive")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout_seconds must be positive")
    if (
        isinstance(output_limit, bool)
        or not isinstance(output_limit, int)
        or output_limit <= 0
    ):
        raise ValueError("max_output_bytes must be positive")


__all__ = [
    "SandboxCompletedEvent",
    "SandboxError",
    "SandboxEvent",
    "SandboxExecution",
    "SandboxLaunchError",
    "SandboxOutputEvent",
    "SandboxProvider",
    "SandboxRequest",
    "SandboxResult",
    "SandboxStream",
    "SandboxTerminalReason",
    "SandboxUnavailableError",
    "resolve_sandbox_provider",
]
