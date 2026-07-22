"""Singleton gateway process lifecycle and local control socket."""

import asyncio
import fcntl
import os
import signal
import socket
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from ethos.gateways.base import CommandExecutor, Gateway

RUNTIME_DIR: Final = Path("runtime")
PID_FILE: Final = RUNTIME_DIR / "gateways.pid"
LOCK_FILE: Final = RUNTIME_DIR / "gateways.lock"
SOCKET_FILE: Final = RUNTIME_DIR / "gateways.sock"


class _ControlModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class _StatusRequest(_ControlModel):
    action: Literal["status"] = "status"


class _StopRequest(_ControlModel):
    action: Literal["stop"] = "stop"
    gateways: list[str] = Field(default_factory=list)


class _StatusResponse(_ControlModel):
    ok: Literal[True] = True
    pid: int
    gateways: list[str]


class _StopResponse(_ControlModel):
    ok: Literal[True] = True
    stopped: list[str]


class _ErrorResponse(_ControlModel):
    ok: Literal[False] = False
    error: str


type _ControlRequest = _StatusRequest | _StopRequest
type _ControlResponse = _StatusResponse | _StopResponse | _ErrorResponse

_REQUEST_ADAPTER: Final[TypeAdapter[_ControlRequest]] = TypeAdapter(
    _ControlRequest
)
_RESPONSE_ADAPTER: Final[TypeAdapter[_ControlResponse]] = TypeAdapter(
    _ControlResponse
)


class SupervisorAlreadyRunning(RuntimeError):
    """Raised when another Ethos gateway supervisor owns the runtime lock."""


class SupervisorNotRunning(RuntimeError):
    """Raised when the local gateway supervisor cannot be reached."""


@contextmanager
def _claim_runtime(home: Path) -> Generator[Path, None, None]:
    runtime = home / RUNTIME_DIR
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    runtime.chmod(0o700)
    lock_path = home / LOCK_FILE
    lock = lock_path.open("a+", encoding="utf-8")
    lock_path.chmod(0o600)
    flock_acquired = False
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            flock_acquired = True
        except BlockingIOError as error:
            raise SupervisorAlreadyRunning(
                "ethos gateways are already running"
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
            try:
                owner = pid_path.read_text(encoding="utf-8").strip()
            except FileNotFoundError:
                owner = ""
            if owner == str(os.getpid()):
                pid_path.unlink(missing_ok=True)
    finally:
        if flock_acquired:
            fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


class GatewaySupervisor:
    """Run and selectively stop gateways in one singleton process."""

    def __init__(self, home: Path, gateways: tuple[Gateway, ...]) -> None:
        names = [gateway.name for gateway in gateways]
        if not gateways:
            raise ValueError("at least one gateway is required")
        if len(set(names)) != len(names):
            raise ValueError("gateway names must be unique")
        self._home = home
        self._gateways = gateways
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._shutdown = asyncio.Event()

    async def run(self, execute: CommandExecutor) -> None:
        """Serve gateways and their private local control socket."""
        with _claim_runtime(self._home) as socket_path:
            server = await asyncio.start_unix_server(
                self._handle_control, path=socket_path
            )
            socket_path.chmod(0o600)
            loop = asyncio.get_running_loop()
            installed_signals: list[signal.Signals] = []
            for process_signal in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(
                        process_signal, self._request_process_shutdown
                    )
                except NotImplementedError:
                    continue
                installed_signals.append(process_signal)

            try:
                async with server, asyncio.TaskGroup() as tasks:
                    self._tasks = {
                        gateway.name: tasks.create_task(
                            gateway.run(execute),
                            name=f"gateway:{gateway.name}",
                        )
                        for gateway in self._gateways
                    }
                    for task in self._tasks.values():
                        task.add_done_callback(self._gateway_finished)
                    await self._shutdown.wait()
                    for task in self._running_tasks().values():
                        task.cancel()
            finally:
                server.close()
                await server.wait_closed()
                for process_signal in installed_signals:
                    loop.remove_signal_handler(process_signal)

    def _gateway_finished(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception() is None:
            self._shutdown.set()

    def _request_process_shutdown(self) -> None:
        for task in self._running_tasks().values():
            task.cancel()
        self._shutdown.set()

    def _running_tasks(self) -> dict[str, asyncio.Task[None]]:
        return {
            name: task for name, task in self._tasks.items() if not task.done()
        }

    async def _handle_control(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        should_exit = False
        try:
            payload = _REQUEST_ADAPTER.validate_json(await reader.readline())
            if isinstance(payload, _StatusRequest):
                response: _ControlResponse = _StatusResponse(
                    pid=os.getpid(),
                    gateways=sorted(self._running_tasks()),
                )
            else:
                running = self._running_tasks()
                names = list(dict.fromkeys(payload.gateways)) or list(running)
                missing = sorted(set(names) - running.keys())
                if missing:
                    raise ValueError(
                        f"gateways are not running: {', '.join(missing)}"
                    )
                stopping = [running[name] for name in names]
                for task in stopping:
                    task.cancel()
                await asyncio.gather(*stopping, return_exceptions=True)
                should_exit = not self._running_tasks()
                response = _StopResponse(stopped=names)
        except ValueError as error:
            response = _ErrorResponse(error=str(error))

        try:
            writer.write((response.model_dump_json() + "\n").encode())
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        finally:
            if should_exit:
                self._shutdown.set()


def _control_request(
    home: Path, request: _ControlRequest
) -> _StatusResponse | _StopResponse:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            connection.connect(str(home / SOCKET_FILE))
            connection.sendall((request.model_dump_json() + "\n").encode())
            response = b""
            while not response.endswith(b"\n"):
                chunk = connection.recv(4096)
                if not chunk:
                    break
                response += chunk
    except (FileNotFoundError, ConnectionError, TimeoutError) as error:
        raise SupervisorNotRunning("ethos gateways are not running") from error

    decoded = _RESPONSE_ADAPTER.validate_json(response)
    if isinstance(decoded, _ErrorResponse):
        raise RuntimeError(decoded.error)
    return decoded


def supervisor_status(home: Path) -> tuple[int, tuple[str, ...]]:
    """Return the PID and gateway names reported by the local supervisor."""
    response = _control_request(home, _StatusRequest())
    if not isinstance(response, _StatusResponse):
        raise RuntimeError("invalid gateway supervisor response")
    return response.pid, tuple(response.gateways)


def running_gateways(home: Path) -> tuple[str, ...]:
    """Return gateway names reported by the local supervisor."""
    return supervisor_status(home)[1]


def stop_gateways(home: Path, names: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Ask the local supervisor to stop selected or all gateways."""
    response = _control_request(home, _StopRequest(gateways=list(names)))
    if not isinstance(response, _StopResponse):
        raise RuntimeError("invalid gateway supervisor response")
    return tuple(response.stopped)
