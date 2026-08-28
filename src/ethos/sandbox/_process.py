"""Shared subprocess supervision for native sandbox providers."""

from __future__ import annotations

import asyncio
import errno
import os
import signal
from collections.abc import AsyncIterator, Callable, Sequence

from ethos.sandbox import (
    SandboxCompletedEvent,
    SandboxEvent,
    SandboxLaunchError,
    SandboxOutputEvent,
    SandboxRequest,
    SandboxResult,
    SandboxStream,
    SandboxTerminalReason,
)

_CHUNK_BYTES = 64 * 1024
_TERMINATE_GRACE_SECONDS = 0.25
_KILL_GRACE_SECONDS = 1.0


async def start_process(
    request: SandboxRequest,
    invocation: Sequence[str],
    *,
    cleanup: Callable[[], None] | None = None,
    pass_fds: Sequence[int] = (),
) -> ProcessSandboxExecution:
    """Start a provider invocation without exposing its Process object.

    ``invocation`` includes the native provider launcher. The exact request
    environment and a new POSIX session apply to that launcher and therefore
    to the command it contains. ``pass_fds`` is reserved for provider-owned
    policy descriptors such as Bubblewrap's seccomp program.
    """

    # Cancellation can arrive after the kernel creates a child but before
    # asyncio returns it. Shield the spawn, then clean up any child that won
    # that race before propagating cancellation.
    spawn = asyncio.create_task(
        asyncio.create_subprocess_exec(
            *invocation,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=request.working_directory,
            env=dict(request.environment),
            start_new_session=True,
            pass_fds=pass_fds,
        )
    )
    try:
        process = await asyncio.shield(spawn)
    except asyncio.CancelledError:
        try:
            process = await spawn
        except (OSError, ValueError):
            _cleanup_after_failed_launch(cleanup)
        else:
            execution = ProcessSandboxExecution(
                process, request, cleanup=cleanup
            )
            await asyncio.shield(execution.aclose())
        raise
    except (OSError, ValueError) as error:
        _cleanup_after_failed_launch(cleanup)
        raise SandboxLaunchError(
            "sandbox process could not be started"
        ) from error
    return ProcessSandboxExecution(process, request, cleanup=cleanup)


class ProcessSandboxExecution:
    """Coordinate one process tree, its output, and its terminal result."""

    def __init__(
        self,
        process: asyncio.subprocess.Process,
        request: SandboxRequest,
        *,
        cleanup: Callable[[], None] | None,
    ) -> None:
        self._process = process
        self._request = request
        self._cleanup = cleanup
        self._queue: asyncio.Queue[SandboxEvent | None] = asyncio.Queue()
        self._result: asyncio.Future[SandboxResult] = (
            asyncio.get_running_loop().create_future()
        )
        self._cancel_requested = asyncio.Event()
        self._suppress_completion = False
        self._output_lock = asyncio.Lock()
        self._output_bytes = 0
        self._output_exceeded = asyncio.Event()
        self._task = asyncio.create_task(self._run())

    async def events(self) -> AsyncIterator[SandboxEvent]:
        """Yield output followed by exactly one completion event."""

        try:
            while True:
                event = await self._queue.get()
                if event is None:
                    return
                yield event
                if isinstance(event, SandboxCompletedEvent):
                    return
        finally:
            if not self._result.done():
                await self.aclose()

    async def cancel(self) -> SandboxResult:
        """Request idempotent cleanup and return the shared terminal result."""

        if not self._result.done():
            # Explicit cancellation returns the result directly; do not also
            # deliver it through the event stream.
            self._suppress_completion = True
            self._cancel_requested.set()
        return await asyncio.shield(self._result)

    async def aclose(self) -> None:
        await self.cancel()

    async def _run(self) -> None:
        reason = SandboxTerminalReason.INDETERMINATE
        result = SandboxResult(SandboxTerminalReason.INDETERMINATE)
        readers: list[asyncio.Task[None]] = []
        try:
            if self._process.stdout is None or self._process.stderr is None:
                raise RuntimeError("sandbox pipes were not created")
            readers = [
                asyncio.create_task(
                    self._read(self._process.stdout, SandboxStream.STDOUT)
                ),
                asyncio.create_task(
                    self._read(self._process.stderr, SandboxStream.STDERR)
                ),
            ]
            exit_wait = asyncio.create_task(self._process.wait())
            timeout_wait = asyncio.create_task(
                asyncio.sleep(self._request.timeout_seconds)
            )
            output_wait = asyncio.create_task(self._output_exceeded.wait())
            cancel_wait = asyncio.create_task(self._cancel_requested.wait())
            waiters = [exit_wait, timeout_wait, output_wait, cancel_wait]
            # Every terminal path resolves through this single race, keeping
            # cancellation and natural exit from publishing two outcomes.
            done, _pending = await asyncio.wait(
                waiters, return_when=asyncio.FIRST_COMPLETED
            )

            if cancel_wait in done and self._cancel_requested.is_set():
                reason = SandboxTerminalReason.CANCELLED
            elif output_wait in done and self._output_exceeded.is_set():
                reason = SandboxTerminalReason.OUTPUT_LIMIT_EXCEEDED
            elif timeout_wait in done:
                reason = SandboxTerminalReason.TIMED_OUT
            else:
                reason = SandboxTerminalReason.EXITED

            cleaned = await asyncio.shield(self._stop_tree())
            if not cleaned:
                reason = SandboxTerminalReason.INDETERMINATE
            if not await _bounded_gather(readers):
                reason = SandboxTerminalReason.INDETERMINATE
            if (
                self._output_exceeded.is_set()
                and reason is SandboxTerminalReason.EXITED
            ):
                # The process can exit before the reader observes its final
                # buffered bytes. Overflow still wins once pipes are drained.
                reason = SandboxTerminalReason.OUTPUT_LIMIT_EXCEEDED
            for waiter in waiters:
                if not waiter.done():
                    waiter.cancel()
            for waiter in waiters:
                try:
                    await waiter
                except (asyncio.CancelledError, Exception):
                    pass

            result = SandboxResult(
                reason=reason,
                exit_code=(
                    self._process.returncode
                    if reason is SandboxTerminalReason.EXITED
                    else None
                ),
            )
        except BaseException:
            cleaned = await asyncio.shield(self._stop_tree())
            await _bounded_gather(readers)
            result = SandboxResult(
                reason=(
                    SandboxTerminalReason.INDETERMINATE
                    if not cleaned
                    else reason
                ),
                exit_code=None,
            )
            if result.reason is SandboxTerminalReason.EXITED:
                result = SandboxResult(SandboxTerminalReason.INDETERMINATE)
        finally:
            if self._cleanup is not None:
                try:
                    self._cleanup()
                except Exception:
                    result = SandboxResult(SandboxTerminalReason.INDETERMINATE)
            if not self._result.done():
                self._result.set_result(result)
            if not self._suppress_completion:
                await self._queue.put(SandboxCompletedEvent(result))
            await self._queue.put(None)

    async def _read(
        self, reader: asyncio.StreamReader, stream: SandboxStream
    ) -> None:
        """Drain one pipe while enforcing the shared raw-byte allowance."""

        while chunk := await reader.read(_CHUNK_BYTES):
            # stdout and stderr spend from one combined limit.
            async with self._output_lock:
                if self._output_exceeded.is_set():
                    continue
                remaining = self._request.max_output_bytes - self._output_bytes
                kept = chunk[:remaining]
                if kept:
                    self._output_bytes += len(kept)
                    await self._queue.put(SandboxOutputEvent(stream, kept))
                if len(chunk) > remaining:
                    self._output_exceeded.set()

    async def _stop_tree(self) -> bool:
        """Stop the process group and prove the direct child was reaped.

        This also runs after normal parent exit because a command may leave a
        descendant behind. Failure to prove that the group disappeared makes
        the caller report ``indeterminate``.
        """

        try:
            os.killpg(self._process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError:
            return False
        try:
            await asyncio.wait_for(
                asyncio.shield(self._process.wait()),
                timeout=_TERMINATE_GRACE_SECONDS,
            )
        except TimeoutError:
            try:
                os.killpg(self._process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                return False
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._process.wait()),
                    timeout=_KILL_GRACE_SECONDS,
                )
            except TimeoutError:
                return False
        if not _group_exists(self._process.pid):
            return True
        try:
            os.killpg(self._process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _KILL_GRACE_SECONDS
        while loop.time() < deadline:
            if not _group_exists(self._process.pid):
                return True
            await asyncio.sleep(0.01)
        return False


async def _bounded_gather(tasks: Sequence[asyncio.Task[None]]) -> bool:
    """Finish pipe readers within the cleanup budget."""

    if not tasks:
        return True
    try:
        outcomes = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=_KILL_GRACE_SECONDS,
        )
        return not any(
            isinstance(outcome, BaseException) for outcome in outcomes
        )
    except TimeoutError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        return False


def _group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except OSError as error:
        return error.errno != errno.ESRCH
    return True


def _cleanup_after_failed_launch(cleanup: Callable[[], None] | None) -> None:
    if cleanup is None:
        return
    try:
        cleanup()
    except Exception:
        pass
