import asyncio
import json
import sys
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest

from ethos.capabilities import RunContext
from ethos.capabilities.shell import COMMAND_PATH, ShellCapability
from ethos.models import ToolCallPart
from ethos.sandbox import (
    SandboxCompletedEvent,
    SandboxExecution,
    SandboxLaunchError,
    SandboxOutputEvent,
    SandboxProvider,
    SandboxRequest,
    SandboxResult,
    SandboxStream,
    SandboxTerminalReason,
    SandboxUnavailableError,
    resolve_sandbox_provider,
)
from ethos.tools import (
    PreparedToolCall,
    RejectedToolCall,
    RequireApproval,
    ToolExecutionCompleted,
    ToolExecutionEvent,
    ToolExecutionIndeterminateError,
    ToolExecutor,
    ToolOutput,
    ToolOutputStream,
    ToolRegistry,
)
from fakes import FakeSandboxExecution, FakeSandboxProvider


def _execution(
    events: tuple[SandboxOutputEvent | SandboxCompletedEvent, ...],
    *,
    cancel_reason: SandboxTerminalReason = SandboxTerminalReason.CANCELLED,
) -> FakeSandboxExecution:
    return FakeSandboxExecution(
        events,
        cancel_result=SandboxResult(cancel_reason),
    )


async def _tool(
    workspace: Path,
    provider: FakeSandboxProvider,
    **limits: int,
) -> tuple[ToolExecutor, ToolCallPart]:
    async def factory() -> FakeSandboxProvider:
        return provider

    capability = ShellCapability(factory, **limits)
    tools = await capability.tools(
        RunContext("project", workspace.resolve(), "session-1")
    )
    call = ToolCallPart(
        call_id="call-1",
        name="run_command",
        arguments_json=json.dumps(
            {"command": "printf hello", "working_directory": "."}
        ),
    )
    return ToolExecutor(ToolRegistry(tools)), call


async def _events(
    events: AsyncIterator[ToolExecutionEvent],
) -> list[ToolExecutionEvent]:
    return [event async for event in events]


def test_run_command_streams_incremental_utf8_and_returns_bounded_json(
    tmp_path: Path,
) -> None:
    raw = _execution(
        (
            SandboxOutputEvent(SandboxStream.STDOUT, b"caf\xc3"),
            SandboxOutputEvent(SandboxStream.STDERR, b"warning\n"),
            SandboxOutputEvent(SandboxStream.STDOUT, b"\xa9\n"),
            SandboxCompletedEvent(
                SandboxResult(SandboxTerminalReason.EXITED, 0)
            ),
        )
    )
    provider = FakeSandboxProvider((raw,))

    async def scenario() -> tuple[PreparedToolCall, list[ToolExecutionEvent]]:
        executor, call = await _tool(tmp_path, provider)
        prepared = await executor.prepare(call)
        assert isinstance(prepared, PreparedToolCall)
        assert isinstance(prepared.decision, RequireApproval)
        execution = await executor.start(prepared)
        return prepared, await _events(execution.events())

    prepared, events = asyncio.run(scenario())

    assert prepared.arguments.model_dump() == {
        "command": "printf hello",
        "working_directory": ".",
    }
    assert events[:-1] == [
        ToolOutput(ToolOutputStream.STDOUT, "caf"),
        ToolOutput(ToolOutputStream.STDERR, "warning\n"),
        ToolOutput(ToolOutputStream.STDOUT, "é\n"),
    ]
    completed = events[-1]
    assert isinstance(completed, ToolExecutionCompleted)
    assert json.loads(completed.result.content) == {
        "outcome": "completed",
        "stdout": "café\n",
        "stderr": "warning\n",
        "exit_code": 0,
    }
    assert not completed.result.is_error
    request = provider.requests[0]
    assert request.argv == ("/bin/sh", "-c", "printf hello")
    assert request.working_directory == tmp_path.resolve()
    assert request.environment["HOME"] == str(request.temporary_path)
    assert request.environment["TMPDIR"] == str(request.temporary_path)
    assert request.environment["PATH"] == COMMAND_PATH
    assert str(Path.home()) not in request.environment["PATH"]
    assert request.environment["TERM"] == "dumb"
    assert request.environment["CI"] == "1"
    assert "AWS_SECRET_ACCESS_KEY" not in request.environment
    assert not request.temporary_path.exists()
    assert raw.close_calls == 1


@pytest.mark.parametrize(
    ("reason", "exit_code", "outcome"),
    (
        (SandboxTerminalReason.EXITED, 7, "failed"),
        (SandboxTerminalReason.TIMED_OUT, None, "timed_out"),
        (
            SandboxTerminalReason.OUTPUT_LIMIT_EXCEEDED,
            None,
            "output_limit_exceeded",
        ),
        (SandboxTerminalReason.CANCELLED, None, "cancelled"),
    ),
)
def test_run_command_maps_definitive_terminal_results(
    tmp_path: Path,
    reason: SandboxTerminalReason,
    exit_code: int | None,
    outcome: str,
) -> None:
    raw = _execution((SandboxCompletedEvent(SandboxResult(reason, exit_code)),))
    provider = FakeSandboxProvider((raw,))

    async def scenario() -> ToolExecutionCompleted:
        executor, call = await _tool(tmp_path, provider)
        prepared = await executor.prepare(call)
        assert isinstance(prepared, PreparedToolCall)
        events = await _events((await executor.start(prepared)).events())
        assert isinstance(events[-1], ToolExecutionCompleted)
        return events[-1]

    completed = asyncio.run(scenario())
    content = json.loads(completed.result.content)
    assert content["outcome"] == outcome
    assert content.get("exit_code") == exit_code
    assert completed.result.is_error


@pytest.mark.parametrize(
    "arguments",
    (
        {"command": "   "},
        {"command": "ééé", "working_directory": "."},
        {"command": "pwd", "working_directory": "/tmp"},
        {"command": "pwd", "working_directory": "../outside"},
        {"command": "pwd", "working_directory": "missing"},
    ),
)
def test_run_command_rejects_invalid_arguments_before_launch(
    tmp_path: Path,
    arguments: dict[str, str],
) -> None:
    provider = FakeSandboxProvider(())

    async def scenario() -> object:
        executor, call = await _tool(
            tmp_path,
            provider,
            max_command_bytes=5,
        )
        return await executor.prepare(
            call.model_copy(update={"arguments_json": json.dumps(arguments)})
        )

    result = asyncio.run(scenario())
    assert isinstance(result, RejectedToolCall)
    assert result.result.is_error
    assert provider.requests == []


def test_run_command_normalises_an_in_workspace_symlink_before_approval(
    tmp_path: Path,
) -> None:
    target = tmp_path / "real"
    target.mkdir()
    (tmp_path / "alias").symlink_to(target, target_is_directory=True)
    provider = FakeSandboxProvider(())

    async def scenario() -> PreparedToolCall:
        executor, call = await _tool(tmp_path, provider)
        prepared = await executor.prepare(
            call.model_copy(
                update={
                    "arguments_json": json.dumps(
                        {
                            "command": "pwd",
                            "working_directory": "./alias",
                        }
                    )
                }
            )
        )
        assert isinstance(prepared, PreparedToolCall)
        return prepared

    prepared = asyncio.run(scenario())
    assert prepared.arguments.model_dump()["working_directory"] == "real"


def test_run_command_rejects_a_working_directory_symlink_escape(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "escape").symlink_to(outside, target_is_directory=True)
    provider = FakeSandboxProvider(())

    async def scenario() -> object:
        executor, call = await _tool(workspace, provider)
        return await executor.prepare(
            call.model_copy(
                update={
                    "arguments_json": json.dumps(
                        {
                            "command": "pwd",
                            "working_directory": "escape",
                        }
                    )
                }
            )
        )

    result = asyncio.run(scenario())
    assert isinstance(result, RejectedToolCall)
    assert provider.requests == []


def test_run_command_cancellation_returns_and_cleans_definitive_result(
    tmp_path: Path,
) -> None:
    raw = _execution(())
    provider = FakeSandboxProvider((raw,))

    async def scenario() -> tuple[dict[str, object], Path]:
        executor, call = await _tool(tmp_path, provider)
        prepared = await executor.prepare(call)
        assert isinstance(prepared, PreparedToolCall)
        execution = await executor.start(prepared)
        result = await execution.cancel()
        return (
            cast(dict[str, object], json.loads(result.content)),
            provider.requests[0].temporary_path,
        )

    content, temporary_path = asyncio.run(scenario())
    assert content["outcome"] == "cancelled"
    assert raw.cancel_calls == 1
    assert raw.close_calls == 1
    assert not temporary_path.exists()


def test_run_command_preserves_private_directory_when_outcome_is_indeterminate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    temporary_path = tmp_path / "private"
    temporary_path.mkdir()

    def fake_mkdtemp(*, prefix: str) -> str:
        del prefix
        return str(temporary_path)

    monkeypatch.setattr(tempfile, "mkdtemp", fake_mkdtemp)
    raw = _execution((), cancel_reason=SandboxTerminalReason.INDETERMINATE)
    provider = FakeSandboxProvider((raw,))

    async def scenario() -> Path:
        executor, call = await _tool(workspace, provider)
        prepared = await executor.prepare(call)
        assert isinstance(prepared, PreparedToolCall)
        execution = await executor.start(prepared)
        with pytest.raises(ToolExecutionIndeterminateError):
            await execution.cancel()
        return provider.requests[0].temporary_path

    preserved_path = asyncio.run(scenario())
    assert preserved_path == temporary_path
    assert preserved_path.exists()


def test_shell_capability_fails_resolution_when_native_sandbox_is_unavailable(
    tmp_path: Path,
) -> None:
    async def unavailable() -> FakeSandboxProvider:
        raise SandboxUnavailableError("native sandbox unavailable")

    capability = ShellCapability(unavailable)

    async def scenario() -> None:
        with pytest.raises(SandboxUnavailableError, match="unavailable"):
            await capability.tools(
                RunContext("project", tmp_path.resolve(), "session-1")
            )

    asyncio.run(scenario())


def test_run_command_cleans_private_directory_after_definitive_launch_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    temporary_path = tmp_path / "private"
    temporary_path.mkdir()

    def fake_mkdtemp(*, prefix: str) -> str:
        del prefix
        return str(temporary_path)

    monkeypatch.setattr(tempfile, "mkdtemp", fake_mkdtemp)

    class FailingProvider:
        async def start(self, request: SandboxRequest) -> SandboxExecution:
            del request
            raise SandboxLaunchError("launch failed")

    async def factory() -> FailingProvider:
        return FailingProvider()

    async def scenario() -> ToolExecutionCompleted:
        tools = await ShellCapability(factory).tools(
            RunContext("project", workspace.resolve(), "session-1")
        )
        executor = ToolExecutor(ToolRegistry(tools))
        call = ToolCallPart(
            call_id="call-1",
            name="run_command",
            arguments_json='{"command":"true"}',
        )
        prepared = await executor.prepare(call)
        assert isinstance(prepared, PreparedToolCall)
        events = await _events((await executor.start(prepared)).events())
        assert isinstance(events[-1], ToolExecutionCompleted)
        return events[-1]

    completed = asyncio.run(scenario())
    assert completed.result.is_error
    assert completed.result.content == "run_command could not start"
    assert not temporary_path.exists()


@pytest.mark.skipif(
    sys.platform != "darwin" and not sys.platform.startswith("linux"),
    reason="no native provider",
)
def test_run_command_executes_through_the_native_provider(
    tmp_path: Path,
) -> None:
    try:
        provider = asyncio.run(resolve_sandbox_provider())
    except SandboxUnavailableError as error:
        pytest.skip(str(error))

    async def factory() -> SandboxProvider:
        return provider

    script = tmp_path / "script.py"
    script.write_text(
        "import sys\nprint('stdout')\nprint('stderr', file=sys.stderr)\n"
        "open('result.txt', 'w').write('written')\n",
        encoding="utf-8",
    )

    async def scenario() -> ToolExecutionCompleted:
        tools = await ShellCapability(factory).tools(
            RunContext("project", tmp_path.resolve(), "session-1")
        )
        executor = ToolExecutor(ToolRegistry(tools))
        call = ToolCallPart(
            call_id="call-1",
            name="run_command",
            arguments_json=json.dumps({"command": "python3 script.py"}),
        )
        prepared = await executor.prepare(call)
        assert isinstance(prepared, PreparedToolCall)
        events = await _events((await executor.start(prepared)).events())
        assert isinstance(events[-1], ToolExecutionCompleted)
        return events[-1]

    completed = asyncio.run(scenario())
    assert json.loads(completed.result.content) == {
        "outcome": "completed",
        "stdout": "stdout\n",
        "stderr": "stderr\n",
        "exit_code": 0,
    }
    assert (tmp_path / "result.txt").read_text() == "written"
