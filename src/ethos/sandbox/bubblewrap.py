"""Linux Bubblewrap sandbox provider (Bubblewrap 0.8.0 or newer)."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import stat
import struct
from collections.abc import Callable, Sequence
from pathlib import Path, PurePath
from typing import cast

from ethos.sandbox import (
    SandboxExecution,
    SandboxLaunchError,
    SandboxRequest,
    SandboxUnavailableError,
)
from ethos.sandbox._process import start_process

_MINIMUM_VERSION = (0, 8, 0)
# Bubblewrap begins with an empty root; only these host runtime paths are
# mounted back into it, and each is read-only.
_RUNTIME_PATHS = (
    Path("/usr"),
    Path("/bin"),
    Path("/sbin"),
    Path("/lib"),
    Path("/lib64"),
    Path("/etc/ld.so.cache"),
    Path("/etc/alternatives"),
    Path("/etc/ssl/certs"),
    Path("/nix/store"),
)


class BubblewrapSandboxProvider:
    """Execute requests in a minimal Bubblewrap namespace and mount tree."""

    def __init__(self, binary: Path | None = None) -> None:
        self._binary = binary
        self._available = False

    async def check_available(self) -> None:
        """Validate the binary, version, and required kernel namespaces."""

        if self._available:
            return
        binary = self._binary or _find_bubblewrap()
        if binary is None:
            raise SandboxUnavailableError(
                "Bubblewrap 0.8.0 or newer is required; install bwrap"
            )
        binary = binary.resolve()
        try:
            mode = binary.stat().st_mode
        except OSError as error:
            raise SandboxUnavailableError(
                "the configured Bubblewrap executable is unavailable"
            ) from error
        if not stat.S_ISREG(mode) or not os.access(binary, os.X_OK):
            raise SandboxUnavailableError(
                "the configured Bubblewrap path is not an executable file"
            )
        if await _bubblewrap_version(binary) < _MINIMUM_VERSION:
            raise SandboxUnavailableError(
                "Bubblewrap 0.8.0 or newer is required"
            )
        try:
            process = await asyncio.create_subprocess_exec(
                *_probe_invocation(binary),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                env={},
                start_new_session=True,
            )
        except OSError as error:
            raise SandboxUnavailableError(
                "the Bubblewrap namespace probe could not start"
            ) from error
        try:
            _stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=5
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            raise SandboxUnavailableError(
                "Bubblewrap namespace probing timed out"
            ) from None
        if process.returncode != 0:
            diagnostic = stderr[:512].decode("utf-8", errors="replace").lower()
            if "apparmor" in diagnostic:
                detail = "AppArmor denied Bubblewrap namespace creation"
            elif (
                "user namespace" in diagnostic
                or "operation not permitted" in diagnostic
            ):
                detail = "unprivileged user namespaces are unavailable"
            else:
                detail = "the required Bubblewrap namespace probe failed"
            raise SandboxUnavailableError(detail)
        self._binary = binary
        self._available = True

    async def start(self, request: SandboxRequest) -> SandboxExecution:
        """Install socket filtering and start the isolated invocation."""

        await self.check_available()
        if self._binary is None:
            raise SandboxUnavailableError("Bubblewrap is unavailable")
        try:
            seccomp_fd = _socket_filter_fd()
        except OSError as error:
            raise SandboxLaunchError(
                "Bubblewrap socket isolation could not be prepared"
            ) from error
        return await start_process(
            request,
            _bubblewrap_invocation(self._binary, request, seccomp_fd),
            cleanup=lambda: os.close(seccomp_fd),
            pass_fds=(seccomp_fd,),
        )


def _find_bubblewrap() -> Path | None:
    found = shutil.which("bwrap", path=os.environ.get("PATH", ""))
    return Path(found) if found is not None else None


async def _bubblewrap_version(binary: Path) -> tuple[int, ...]:
    try:
        process = await asyncio.create_subprocess_exec(
            binary,
            "--version",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={},
        )
    except OSError as error:
        raise SandboxUnavailableError(
            "Bubblewrap version could not be determined"
        ) from error
    try:
        stdout, _stderr = await asyncio.wait_for(
            process.communicate(), timeout=3
        )
    except TimeoutError as error:
        process.kill()
        await process.wait()
        raise SandboxUnavailableError(
            "Bubblewrap version could not be determined"
        ) from error
    match = re.search(rb"(\d+)\.(\d+)\.(\d+)", stdout[:128])
    if process.returncode != 0 or match is None:
        raise SandboxUnavailableError(
            "Bubblewrap version could not be determined"
        )
    return tuple(int(part) for part in match.groups())


def _base_invocation(binary: Path) -> list[str]:
    """Return isolation required by every probe and real invocation.

    The private tmpfs root reveals nothing until later arguments mount a
    resource into it. Capabilities and nested user namespaces are disabled so
    the child cannot relax the surrounding mount and namespace policy.
    """

    return [
        str(binary),
        "--unshare-user",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--unshare-cgroup",
        "--unshare-net",
        "--die-with-parent",
        "--new-session",
        "--disable-userns",
        "--cap-drop",
        "ALL",
        "--clearenv",
        "--tmpfs",
        "/",
    ]


def _probe_invocation(binary: Path) -> list[str]:
    invocation = _base_invocation(binary)
    invocation.extend(
        [
            "--dir",
            "/usr",
            "--ro-bind",
            "/usr",
            "/usr",
            "--proc",
            "/proc",
            "--dir",
            "/dev",
            "--remount-ro",
            "/",
            "--",
            "/usr/bin/true",
        ]
    )
    return invocation


def _bubblewrap_invocation(
    binary: Path, request: SandboxRequest, seccomp_fd: int
) -> tuple[str, ...]:
    """Build the fixed mount, device, environment, and command policy."""

    invocation = _base_invocation(binary)
    for directory in _mount_parents(
        (request.workspace_path, request.temporary_path)
    ):
        invocation.extend(("--dir", str(directory)))
    for path in _RUNTIME_PATHS:
        if path.is_symlink():
            invocation.extend(("--symlink", os.readlink(path), str(path)))
        elif path.exists():
            invocation.extend(("--ro-bind", str(path), str(path)))
    invocation.extend(("--proc", "/proc", "--dir", "/dev"))
    for device in ("null", "zero", "random", "urandom"):
        device_path = f"/dev/{device}"
        if Path(device_path).exists():
            invocation.extend(("--dev-bind", device_path, device_path))
    invocation.extend(("--seccomp", str(seccomp_fd)))
    invocation.extend(
        ("--bind", str(request.workspace_path), str(request.workspace_path))
    )
    invocation.extend(
        ("--bind", str(request.temporary_path), str(request.temporary_path))
    )
    for name, value in request.environment.items():
        invocation.extend(("--setenv", name, value))
    invocation.extend(
        (
            "--chdir",
            str(request.working_directory),
            "--remount-ro",
            "/",
            "--",
            *request.argv,
        )
    )
    return tuple(invocation)


def _mount_parents(paths: Sequence[Path]) -> tuple[PurePath, ...]:
    """Create empty destinations required before absolute bind mounts."""

    parents: set[PurePath] = set()
    for path in paths:
        parents.update(parent for parent in path.parents if parent != Path("/"))
        parents.add(path)
    return tuple(sorted(parents, key=lambda path: (len(path.parts), str(path))))


def _socket_filter_fd() -> int:
    """Return a bwrap seccomp FD denying socket and socketpair syscalls.

    A network namespace isolates IP interfaces but not filesystem-addressed
    Unix sockets in writable bind mounts. Denying socket creation closes that
    separate host-IPC path.
    """

    architecture = os.uname().machine
    syscalls: tuple[int, ...]
    if architecture in {"x86_64", "amd64"}:
        audit_arch = 0xC000003E
        syscalls = (41, 53, 0x40000029, 0x40000035)
    elif architecture in {"aarch64", "arm64"}:
        audit_arch = 0xC00000B7
        syscalls = (198, 199)
    else:
        raise SandboxUnavailableError(
            f"Bubblewrap socket isolation is unsupported on {architecture}"
        )
    if not hasattr(os, "memfd_create"):
        raise SandboxUnavailableError(
            "Bubblewrap socket isolation requires Linux memfd support"
        )

    # Classic BPF over seccomp_data: load and verify the audit architecture,
    # load the syscall number, return EPERM for socket calls, allow the rest.
    instructions = [
        (0x20, 0, 0, 4),
        (0x15, 1, 0, audit_arch),
        (0x06, 0, 0, 0x80000000),
        (0x20, 0, 0, 0),
    ]
    for syscall in syscalls:
        instructions.extend(((0x15, 0, 1, syscall), (0x06, 0, 0, 0x00050001)))
    instructions.append((0x06, 0, 0, 0x7FFF0000))
    program = b"".join(
        struct.pack("=HBBI", *instruction) for instruction in instructions
    )
    create_memfd = cast(Callable[[str, int], int], os.__dict__["memfd_create"])
    # Keep provider policy anonymous and pass it directly to bwrap; numeric 1
    # is Linux MFD_CLOEXEC, unavailable in type stubs when checked on macOS.
    descriptor = create_memfd("ethos-seccomp", 1)  # MFD_CLOEXEC
    os.write(descriptor, program)
    os.lseek(descriptor, 0, os.SEEK_SET)
    return descriptor
