import asyncio
import json
import sys
from collections.abc import AsyncGenerator, Callable, Mapping
from pathlib import Path
from typing import cast

import pytest

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
from ethos.sandbox._process import start_process


def request(
    tmp_path: Path,
    argv: tuple[str, ...] = (sys.executable, "-c", "pass"),
    *,
    environment: Mapping[str, str] | None = None,
    timeout: float = 2,
    output_limit: int = 1024,
) -> SandboxRequest:
    workspace = (tmp_path / "workspace").resolve()
    temporary = (tmp_path / "temporary").resolve()
    workspace.mkdir(exist_ok=True)
    temporary.mkdir(exist_ok=True)
    return SandboxRequest(
        argv=argv,
        working_directory=workspace,
        workspace_path=workspace,
        temporary_path=temporary,
        environment=environment or {},
        timeout_seconds=timeout,
        max_output_bytes=output_limit,
    )


async def execute(
    sandbox_request: SandboxRequest,
    invocation: tuple[str, ...] | None = None,
    *,
    cleanup: Callable[[], None] | None = None,
) -> list[SandboxEvent]:
    execution = await start_process(
        sandbox_request,
        invocation or sandbox_request.argv,
        cleanup=cleanup,
    )
    return [event async for event in execution.events()]


def test_request_is_immutable_and_accepts_nested_working_directory(
    tmp_path: Path,
) -> None:
    sandbox_request = request(tmp_path, environment={"ONLY": "value"})
    nested = sandbox_request.workspace_path / "nested"
    nested.mkdir()
    sandbox_request = SandboxRequest(
        argv=("command", ""),
        working_directory=nested,
        workspace_path=sandbox_request.workspace_path,
        temporary_path=sandbox_request.temporary_path,
        environment={"ONLY": "value"},
        timeout_seconds=1,
        max_output_bytes=1,
    )

    assert sandbox_request.environment == {"ONLY": "value"}
    with pytest.raises(TypeError):
        sandbox_request.environment["OTHER"] = "value"  # type: ignore[index]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"argv": ()}, "argv"),
        ({"argv": ["command"]}, "argv"),
        ({"argv": ("command", "bad\0argument")}, "NUL"),
        ({"environment": {"": "value"}}, "invalid name"),
        ({"environment": {"A=B": "value"}}, "invalid name"),
        ({"environment": {"A\0": "value"}}, "invalid name"),
        ({"environment": {"A": "value\0"}}, "invalid value"),
        ({"environment": {"A": 1}}, "only strings"),
        ({"timeout_seconds": 0}, "positive"),
        ({"timeout_seconds": -1}, "positive"),
        ({"timeout_seconds": float("nan")}, "positive"),
        ({"timeout_seconds": True}, "positive"),
        ({"max_output_bytes": 0}, "positive"),
        ({"max_output_bytes": -1}, "positive"),
        ({"max_output_bytes": True}, "positive"),
    ],
)
def test_request_rejects_invalid_values(
    tmp_path: Path, changes: dict[str, object], message: str
) -> None:
    values: dict[str, object] = {
        "argv": ("command",),
        "working_directory": (tmp_path / "workspace").resolve(),
        "workspace_path": (tmp_path / "workspace").resolve(),
        "temporary_path": (tmp_path / "temporary").resolve(),
        "environment": {},
        "timeout_seconds": 1,
        "max_output_bytes": 1,
    }
    cast(Path, values["workspace_path"]).mkdir()
    cast(Path, values["temporary_path"]).mkdir()
    values.update(changes)

    with pytest.raises(ValueError, match=message):
        SandboxRequest(**values)  # type: ignore[arg-type]


def test_request_rejects_noncanonical_or_escaping_paths(tmp_path: Path) -> None:
    sandbox_request = request(tmp_path)
    outside = (tmp_path / "outside").resolve()
    outside.mkdir()
    symlink = tmp_path / "workspace-link"
    symlink.symlink_to(sandbox_request.workspace_path)
    regular_file = (tmp_path / "regular-file").resolve()
    regular_file.touch()

    for changes in (
        {"workspace_path": Path("relative")},
        {"workspace_path": tmp_path / "missing"},
        {"workspace_path": regular_file},
        {"workspace_path": symlink, "working_directory": symlink},
        {"working_directory": outside},
        {"temporary_path": sandbox_request.workspace_path},
    ):
        values = {
            **sandbox_request.__dict__,
            **changes,
            "environment": {},
        }
        with pytest.raises(ValueError):
            SandboxRequest(**values)


def test_process_streams_raw_stdout_and_stderr_once(tmp_path: Path) -> None:
    script = (
        "import os;"
        "os.write(1, b'\\xf0\\x9f');"
        "os.write(2, b'err');"
        "os.write(1, b'\\x98\\x80')"
    )
    events = asyncio.run(
        execute(request(tmp_path, (sys.executable, "-c", script)))
    )

    assert (
        b"".join(
            event.data
            for event in events
            if isinstance(event, SandboxOutputEvent)
            and event.stream is SandboxStream.STDOUT
        )
        == b"\xf0\x9f\x98\x80"
    )
    assert (
        b"".join(
            event.data
            for event in events
            if isinstance(event, SandboxOutputEvent)
            and event.stream is SandboxStream.STDERR
        )
        == b"err"
    )
    assert events[-1] == SandboxCompletedEvent(
        SandboxResult(SandboxTerminalReason.EXITED, 0)
    )
    assert (
        sum(isinstance(event, SandboxCompletedEvent) for event in events) == 1
    )


def test_process_receives_exact_environment_closed_stdin_and_no_terminal(
    tmp_path: Path,
) -> None:
    environment_events = asyncio.run(
        execute(
            request(
                tmp_path,
                ("/usr/bin/env",),
                environment={"ONLY": "supplied"},
            )
        )
    )
    environment_output = b"".join(
        event.data
        for event in environment_events
        if isinstance(event, SandboxOutputEvent)
    )
    assert environment_output == b"ONLY=supplied\n"

    script = (
        "import json,sys;"
        "print(json.dumps([sys.stdin.buffer.read(),sys.stdin.isatty(),"
        "sys.stdout.isatty()],default=str))"
    )
    sandbox_request = request(
        tmp_path,
        (sys.executable, "-c", script),
    )
    events = asyncio.run(execute(sandbox_request))
    output = b"".join(
        event.data for event in events if isinstance(event, SandboxOutputEvent)
    )

    stdin, stdin_tty, stdout_tty = json.loads(output)
    assert stdin == "b''"
    assert not stdin_tty
    assert not stdout_tty


@pytest.mark.parametrize("exit_code", [7, -15])
def test_process_reports_signed_exit_codes(
    tmp_path: Path, exit_code: int
) -> None:
    if exit_code < 0:
        script = "import os,signal; os.kill(os.getpid(), signal.SIGTERM)"
    else:
        script = f"raise SystemExit({exit_code})"
    events = asyncio.run(
        execute(request(tmp_path, (sys.executable, "-c", script)))
    )

    assert events[-1] == SandboxCompletedEvent(
        SandboxResult(SandboxTerminalReason.EXITED, exit_code)
    )


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"1234", SandboxTerminalReason.EXITED),
        (b"12345", SandboxTerminalReason.OUTPUT_LIMIT_EXCEEDED),
    ],
)
def test_process_enforces_combined_output_limit(
    tmp_path: Path, content: bytes, reason: SandboxTerminalReason
) -> None:
    script = f"import os; os.write(1, {content!r})"
    events = asyncio.run(
        execute(
            request(
                tmp_path,
                (sys.executable, "-c", script),
                output_limit=4,
            )
        )
    )

    assert (
        b"".join(
            event.data
            for event in events
            if isinstance(event, SandboxOutputEvent)
        )
        == b"1234"
    )
    completed = cast(SandboxCompletedEvent, events[-1])
    assert completed.result.reason is reason


def test_process_times_out_after_partial_output(tmp_path: Path) -> None:
    script = "import os,time; os.write(1,b'before'); time.sleep(10)"
    events = asyncio.run(
        execute(
            request(
                tmp_path,
                (sys.executable, "-c", script),
                timeout=0.1,
            )
        )
    )

    assert events[0] == SandboxOutputEvent(SandboxStream.STDOUT, b"before")
    assert cast(SandboxCompletedEvent, events[-1]).result.reason is (
        SandboxTerminalReason.TIMED_OUT
    )


def test_process_forces_termination_and_stops_descendants(
    tmp_path: Path,
) -> None:
    marker = (tmp_path / "outlived").resolve()
    child = (
        "import time,pathlib;time.sleep(0.5);"
        f"pathlib.Path({str(marker)!r}).touch()"
    )
    script = (
        "import signal,subprocess,time;"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        f"subprocess.Popen([{sys.executable!r},'-c',{child!r}]);"
        "time.sleep(10)"
    )
    events = asyncio.run(
        execute(
            request(
                tmp_path,
                (sys.executable, "-c", script),
                timeout=0.1,
            )
        )
    )

    assert cast(SandboxCompletedEvent, events[-1]).result.reason is (
        SandboxTerminalReason.TIMED_OUT
    )
    asyncio.run(asyncio.sleep(0.6))
    assert not marker.exists()


def test_process_cancellation_closes_stream_without_completion(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        sandbox_request = request(
            tmp_path,
            (sys.executable, "-c", "import time; time.sleep(10)"),
        )
        execution = await start_process(sandbox_request, sandbox_request.argv)
        result = await execution.cancel()
        remaining = [event async for event in execution.events()]

        assert result.reason is SandboxTerminalReason.CANCELLED
        assert remaining == []
        assert await execution.cancel() is result

    asyncio.run(exercise())


def test_closing_stream_cleans_up_process(tmp_path: Path) -> None:
    async def exercise() -> None:
        sandbox_request = request(
            tmp_path,
            (
                sys.executable,
                "-c",
                "import os,time; os.write(1,b'go'); time.sleep(10)",
            ),
        )
        execution = await start_process(sandbox_request, sandbox_request.argv)
        events = cast(AsyncGenerator[SandboxEvent, None], execution.events())
        assert await anext(events) == SandboxOutputEvent(
            SandboxStream.STDOUT, b"go"
        )
        await events.aclose()
        assert (await execution.cancel()).reason is (
            SandboxTerminalReason.CANCELLED
        )

    asyncio.run(exercise())


def test_spawn_and_cleanup_failures_are_bounded(tmp_path: Path) -> None:
    sandbox_request = request(tmp_path)
    with pytest.raises(SandboxLaunchError, match="could not be started"):
        asyncio.run(execute(sandbox_request, ("/definitely/missing",)))

    def fail_cleanup() -> None:
        raise OSError("secret raw error")

    events = asyncio.run(execute(sandbox_request, cleanup=fail_cleanup))
    completed = cast(SandboxCompletedEvent, events[-1])
    assert completed.result.reason is SandboxTerminalReason.INDETERMINATE
