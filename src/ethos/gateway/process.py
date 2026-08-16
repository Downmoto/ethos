"""Lifecycle controls for the single background Vox server."""

import asyncio
import fcntl
import os
import signal
import socket
from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Final

RUNTIME_DIR: Final = Path("runtime")
PID_FILE: Final = RUNTIME_DIR / "vox.pid"
LOCK_FILE: Final = RUNTIME_DIR / "vox.lock"
SOCKET_FILE: Final = RUNTIME_DIR / "vox.sock"


class BackgroundAlreadyRunning(RuntimeError):
    """Raised when another background Vox process owns the runtime lock."""


@contextmanager
def _claim(home: Path) -> Generator[Path, None, None]:
    runtime = home / RUNTIME_DIR
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    runtime.chmod(0o700)
    lock_path = home / LOCK_FILE
    lock = lock_path.open("a+", encoding="utf-8")
    lock_path.chmod(0o600)
    acquired = False
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError as error:
            raise BackgroundAlreadyRunning(
                "ethos is already running in the background"
            ) from error

        pid_path = home / PID_FILE
        socket_path = home / SOCKET_FILE
        pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
        pid_path.chmod(0o600)
        socket_path.unlink(missing_ok=True)
        try:
            yield socket_path
        finally:
            socket_path.unlink(missing_ok=True)
            if pid_path.read_text(encoding="utf-8").strip() == str(os.getpid()):
                pid_path.unlink(missing_ok=True)
    finally:
        if acquired:
            fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


async def run_background(
    home: Path, run: Callable[[], Awaitable[None]]
) -> None:
    """Run Vox as the one tracked background service."""
    shutdown = asyncio.Event()

    async def control(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        action = (await reader.readline()).decode().strip()
        if action == "stop":
            shutdown.set()
        writer.write(f"{os.getpid()}\n".encode())
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    with _claim(home) as socket_path:
        server = await asyncio.start_unix_server(control, path=socket_path)
        socket_path.chmod(0o600)
        loop = asyncio.get_running_loop()
        installed: list[signal.Signals] = []
        for process_signal in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(process_signal, shutdown.set)
            except NotImplementedError:
                continue
            installed.append(process_signal)

        task: asyncio.Future[None] = asyncio.ensure_future(run())
        task.add_done_callback(lambda _task: shutdown.set())
        try:
            async with server:
                await shutdown.wait()
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            else:
                await task
        finally:
            server.close()
            await server.wait_closed()
            for process_signal in installed:
                loop.remove_signal_handler(process_signal)


def _control(home: Path, action: str) -> int | None:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            connection.connect(str(home / SOCKET_FILE))
            connection.sendall(f"{action}\n".encode())
            response = connection.recv(64)
    except (FileNotFoundError, ConnectionError, TimeoutError):
        return None
    try:
        return int(response.decode().strip())
    except ValueError:
        return None


def background_pid(home: Path) -> int | None:
    """Return the tracked PID, or ``None`` when Vox is not backgrounded."""
    return _control(home, "status")


def stop_background(home: Path) -> bool:
    """Request background shutdown; absence is a successful no-op."""
    return _control(home, "stop") is not None
