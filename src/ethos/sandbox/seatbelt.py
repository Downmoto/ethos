"""macOS Seatbelt sandbox provider."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from ethos.sandbox import (
    SandboxExecution,
    SandboxLaunchError,
    SandboxRequest,
    SandboxUnavailableError,
)
from ethos.sandbox._process import start_process

_SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")
# These roots are readable only. Writable access is added separately for the
# request workspace and private temporary directory.
_SYSTEM_READ_ROOTS = (
    Path("/bin"),
    Path("/sbin"),
    Path("/usr/bin"),
    Path("/usr/sbin"),
    Path("/usr/lib"),
    Path("/System/Library"),
    Path("/Library/Apple"),
    Path("/private/etc"),
    Path("/private/var/db/dyld"),
    Path("/private/var/db/timezone"),
)
_DEFAULT_EXECUTABLE_ROOTS = (
    Path("/opt/homebrew"),
    Path("/usr/local"),
    Path("/Library/Developer"),
    Path("/Applications/Xcode.app/Contents/Developer"),
)
_SYSTEM_READ_FILES = (
    # dyld reads the root directory itself before loading system libraries.
    Path("/"),
    Path("/dev/null"),
    Path("/dev/random"),
    Path("/dev/urandom"),
)


class SeatbeltSandboxProvider:
    """Execute requests under a generated, default-deny Seatbelt profile.

    ``executable_roots`` is constructor-injected runtime compatibility, not a
    caller-controlled expansion of writable sandbox policy.
    """

    def __init__(self, executable_roots: Sequence[Path] | None = None) -> None:
        configured_roots = (
            executable_roots
            if executable_roots is not None
            else (*_DEFAULT_EXECUTABLE_ROOTS, _python_runtime_root())
        )
        self._executable_roots = tuple(
            path.resolve()
            for path in configured_roots
            if path.is_absolute() and path.exists()
        )
        self._available = False

    async def check_available(self) -> None:
        """Prove macOS accepts the fixed profile shape without user code."""

        if self._available:
            return
        if not (_SANDBOX_EXEC.is_file() and os.access(_SANDBOX_EXEC, os.X_OK)):
            raise SandboxUnavailableError(
                "Seatbelt sandboxing requires /usr/bin/sandbox-exec on macOS"
            )
        try:
            process = await asyncio.create_subprocess_exec(
                _SANDBOX_EXEC,
                "-p",
                _probe_profile(),
                "/usr/bin/true",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env={},
                start_new_session=True,
            )
        except OSError as error:
            raise SandboxUnavailableError(
                "the Seatbelt availability probe could not start"
            ) from error
        try:
            exit_code = await asyncio.wait_for(process.wait(), timeout=3)
        except TimeoutError:
            process.kill()
            await process.wait()
            exit_code = -1
        if exit_code != 0:
            raise SandboxUnavailableError(
                "macOS rejected the required Seatbelt sandbox profile"
            )
        self._available = True

    async def start(self, request: SandboxRequest) -> SandboxExecution:
        """Write, preflight, and execute a private per-request profile."""

        await self.check_available()
        profile = _seatbelt_profile(request, self._executable_roots)
        try:
            descriptor, profile_name = tempfile.mkstemp(
                prefix="ethos-seatbelt-",
                suffix=".sb",
                dir=request.temporary_path,
            )
        except OSError as error:
            raise SandboxLaunchError(
                "Seatbelt profile could not be prepared"
            ) from error
        profile_path = Path(profile_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                file.write(profile)
        except OSError as error:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            profile_path.unlink(missing_ok=True)
            raise SandboxLaunchError(
                "Seatbelt profile could not be prepared"
            ) from error
        try:
            accepted = await _profile_accepted(profile_path)
        except BaseException:
            profile_path.unlink(missing_ok=True)
            raise
        if not accepted:
            profile_path.unlink(missing_ok=True)
            raise SandboxUnavailableError(
                "macOS rejected the required Seatbelt sandbox profile"
            )
        invocation = (
            str(_SANDBOX_EXEC),
            "-f",
            str(profile_path),
            *request.argv,
        )
        return await start_process(
            request,
            invocation,
            cleanup=lambda: profile_path.unlink(missing_ok=True),
        )


def _probe_profile() -> str:
    read_roots = tuple(path for path in _SYSTEM_READ_ROOTS if path.exists())
    return "\n".join(
        (
            "(version 1)",
            "(deny default)",
            _path_rule("process-exec", read_roots, ()),
            "(allow process-fork)",
            "(allow process-info-pidinfo)",
            "(allow sysctl-read)",
            "(allow mach-lookup",
            '  (global-name "com.apple.system.opendirectoryd.libinfo"))',
            _path_rule("file-read*", read_roots, _SYSTEM_READ_FILES),
        )
    )


async def _profile_accepted(profile_path: Path) -> bool:
    try:
        process = await asyncio.create_subprocess_exec(
            _SANDBOX_EXEC,
            "-f",
            profile_path,
            "/usr/bin/true",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env={},
            start_new_session=True,
        )
    except OSError:
        return False
    try:
        return await asyncio.wait_for(process.wait(), timeout=3) == 0
    except TimeoutError:
        process.kill()
        await process.wait()
        return False


def _seatbelt_profile(
    request: SandboxRequest, executable_roots: Sequence[Path]
) -> str:
    """Build the fixed policy from trusted, canonical request paths.

    Seatbelt starts from ``deny default``. Network, sockets, devices, Mach
    services, and unrelated files remain denied because no rule allows them.
    """

    read_roots = (
        request.workspace_path,
        request.temporary_path,
        *(_SYSTEM_READ_ROOTS),
        *executable_roots,
    )
    lines = [
        "(version 1)",
        "(deny default)",
        _path_rule("process-exec", read_roots, ()),
        "(allow process-fork)",
        "(allow process-info-pidinfo)",
        "(allow sysctl-read)",
        "(allow mach-lookup",
        '  (global-name "com.apple.system.opendirectoryd.libinfo"))',
        _path_rule("file-read*", read_roots, _SYSTEM_READ_FILES),
        _path_rule(
            "file-write*", (request.workspace_path, request.temporary_path), ()
        ),
    ]
    return "\n".join(lines) + "\n"


def _path_rule(
    operation: str, roots: Sequence[Path], files: Sequence[Path]
) -> str:
    filters = [
        *(f"  (literal {_quote_path(path)})" for path in (*roots, *files)),
        *(f"  (subpath {_quote_path(path)})" for path in roots),
    ]
    return "\n".join((f"(allow {operation}", *filters, ")"))


def _quote_path(path: Path) -> str:
    """Encode a path as a Seatbelt string without profile injection."""

    return json.dumps(os.fspath(path), ensure_ascii=False)


def _python_runtime_root() -> Path:
    """Expose the resolved interpreter behind workspace virtualenv links."""

    return Path(sys.executable).resolve().parent.parent
