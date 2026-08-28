"""Approval-gated shell commands isolated by the native sandbox provider.

The capability validates and canonicalises the complete invocation before the
runtime asks for approval. The execution wrapper owns UTF-8 decoding, bounded
result construction, cancellation, and temporary-directory cleanup; native
providers only supervise raw process bytes and terminal certainty.
"""

from __future__ import annotations

import asyncio
import codecs
import json
import os
import shutil
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path, PurePosixPath
from typing import Annotated, Final

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, create_model

from ethos.capabilities import RunContext
from ethos.models import ToolCallPart, ToolDefinition, ToolResultPart
from ethos.sandbox import (
    SandboxError,
    SandboxExecution,
    SandboxOutputEvent,
    SandboxProvider,
    SandboxRequest,
    SandboxStream,
    SandboxTerminalReason,
)
from ethos.tools import (
    Tool,
    ToolEffect,
    ToolExecution,
    ToolExecutionCompleted,
    ToolExecutionError,
    ToolExecutionEvent,
    ToolExecutionIndeterminateError,
    ToolOutput,
    ToolOutputStream,
)

MAX_COMMAND_BYTES: Final = 16 * 1024
MAX_COMMAND_SECONDS: Final = 120
MAX_OUTPUT_BYTES: Final = 100 * 1024
COMMAND_PATH: Final = (
    "/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/local/sbin:"
    "/usr/bin:/bin:/usr/sbin:/sbin"
)

type SandboxProviderFactory = Callable[[], Awaitable[SandboxProvider]]


class _RunCommandArguments(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    command: str
    working_directory: str = "."


class _RunCommandTool:
    effect = ToolEffect.WRITE

    def __init__(
        self,
        workspace_path: Path,
        provider: SandboxProvider,
        *,
        max_command_bytes: int,
        max_command_seconds: int,
        max_output_bytes: int,
    ) -> None:
        self._workspace_path = workspace_path.resolve(strict=True)
        self._provider = provider
        self._max_command_seconds = max_command_seconds
        self._max_output_bytes = max_output_bytes
        self.arguments_type = _bound_arguments_type(
            self._workspace_path,
            max_command_bytes,
        )
        self.definition = ToolDefinition(
            name="run_command",
            description=(
                "Run one non-interactive /bin/sh command in the workspace "
                "under the native OS sandbox. Streams stdout and stderr."
                "Relative paths in the command start from working_directory, "
                "so do NOT repeat that directory in the command path."
                "i.e. If working_directory is 'test', reference test/script.py "
                "as script.py, not test/script.py."
            ),
            parameters_schema=self.arguments_type.model_json_schema(),
        )

    async def start(
        self, arguments: BaseModel, call: ToolCallPart
    ) -> ToolExecution:
        if not isinstance(arguments, _RunCommandArguments):
            raise TypeError("invalid run_command arguments")
        command = arguments.command
        relative_directory = arguments.working_directory
        working_directory = (self._workspace_path / relative_directory).resolve(
            strict=True
        )
        temporary_path = Path(tempfile.mkdtemp(prefix="ethos-shell-")).resolve()
        try:
            temporary_path.chmod(0o700)
            request = SandboxRequest(
                argv=("/bin/sh", "-c", command),
                working_directory=working_directory,
                workspace_path=self._workspace_path,
                temporary_path=temporary_path,
                environment=_environment(temporary_path),
                timeout_seconds=self._max_command_seconds,
                max_output_bytes=self._max_output_bytes,
            )
            execution = await self._provider.start(request)
        except (OSError, ValueError, SandboxError) as error:
            shutil.rmtree(temporary_path, ignore_errors=True)
            raise ToolExecutionError("run_command could not start") from error
        return _ShellExecution(call, execution, temporary_path)

    async def execute(self, arguments: BaseModel) -> str:
        """Support the base Tool protocol; ToolExecutor uses start instead."""

        call = ToolCallPart(
            call_id="direct-run-command",
            name="run_command",
            arguments_json="{}",
        )
        execution = await self.start(arguments, call)
        async for event in execution.events():
            if isinstance(event, ToolExecutionCompleted):
                return event.result.content
        raise ToolExecutionIndeterminateError(
            "shell execution ended without a result"
        )


class _ShellExecution:
    def __init__(
        self,
        call: ToolCallPart,
        execution: SandboxExecution,
        temporary_path: Path,
    ) -> None:
        self._call = call
        self._execution = execution
        self._temporary_path = temporary_path
        self._decoders = {
            stream: codecs.getincrementaldecoder("utf-8")(errors="replace")
            for stream in SandboxStream
        }
        self._output: dict[SandboxStream, list[str]] = {
            stream: [] for stream in SandboxStream
        }
        self._decoders_finished = False
        self._finished = False

    async def events(self) -> AsyncIterator[ToolExecutionEvent]:
        try:
            async for event in self._execution.events():
                if isinstance(event, SandboxOutputEvent):
                    text = self._decode(event.stream, event.data)
                    if text:
                        yield ToolOutput(_tool_stream(event.stream), text)
                    continue
                for output in self._finish_decoders():
                    yield output
                result = self._result(
                    event.result.reason,
                    event.result.exit_code,
                )
                self._finished = True
                await self._cleanup()
                yield ToolExecutionCompleted(result)
                return
        except ToolExecutionIndeterminateError:
            raise
        except Exception as error:
            raise ToolExecutionIndeterminateError(
                "shell execution outcome is unknown"
            ) from error
        raise ToolExecutionIndeterminateError(
            "shell execution ended without a terminal result"
        )

    async def cancel(self) -> ToolResultPart:
        if self._finished:
            raise ToolExecutionIndeterminateError(
                "completed shell result is no longer available"
            )
        try:
            terminal = await self._execution.cancel()
        except Exception as error:
            raise ToolExecutionIndeterminateError(
                "shell cancellation outcome is unknown"
            ) from error
        if terminal.reason is SandboxTerminalReason.INDETERMINATE:
            raise ToolExecutionIndeterminateError(
                "shell cancellation outcome is unknown"
            )
        self._finish_decoders()
        result = self._result(terminal.reason, terminal.exit_code)
        self._finished = True
        await self._cleanup()
        return result

    def _decode(self, stream: SandboxStream, data: bytes) -> str:
        text = self._decoders[stream].decode(data, final=False)
        if text:
            self._output[stream].append(text)
        return text

    def _finish_decoders(self) -> tuple[ToolOutput, ...]:
        if self._decoders_finished:
            return ()
        self._decoders_finished = True
        fragments: list[ToolOutput] = []
        for stream, decoder in self._decoders.items():
            text = decoder.decode(b"", final=True)
            if text:
                self._output[stream].append(text)
                fragments.append(ToolOutput(_tool_stream(stream), text))
        return tuple(fragments)

    def _result(
        self,
        reason: SandboxTerminalReason,
        exit_code: int | None,
    ) -> ToolResultPart:
        if reason is SandboxTerminalReason.INDETERMINATE:
            raise ToolExecutionIndeterminateError(
                "shell execution outcome is unknown"
            )
        outcome = (
            "completed"
            if reason is SandboxTerminalReason.EXITED and exit_code == 0
            else "failed"
            if reason is SandboxTerminalReason.EXITED
            else reason.value
        )
        content: dict[str, object] = {
            "outcome": outcome,
            "stdout": "".join(self._output[SandboxStream.STDOUT]),
            "stderr": "".join(self._output[SandboxStream.STDERR]),
        }
        if exit_code is not None:
            content["exit_code"] = exit_code
        return ToolResultPart(
            call_id=self._call.call_id,
            name=self._call.name,
            content=json.dumps(content, ensure_ascii=False),
            is_error=outcome != "completed",
        )

    async def _cleanup(self) -> None:
        await self._execution.aclose()
        await asyncio.to_thread(shutil.rmtree, self._temporary_path, True)


def _bound_arguments_type(
    workspace_path: Path,
    max_command_bytes: int,
) -> type[BaseModel]:
    def command(value: str) -> str:
        if not value.strip() or len(value.encode("utf-8")) > max_command_bytes:
            raise ValueError("command is empty or exceeds its byte limit")
        return value

    def working_directory(value: str) -> str:
        if not value:
            raise ValueError("working directory must not be empty")
        requested = PurePosixPath(value)
        if requested.is_absolute() or ".." in requested.parts:
            raise ValueError("working directory must be workspace-relative")
        try:
            resolved = (workspace_path / requested).resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise ValueError("working directory must exist") from error
        if not resolved.is_relative_to(workspace_path) or not resolved.is_dir():
            raise ValueError("working directory must be a workspace directory")
        relative = resolved.relative_to(workspace_path).as_posix()
        return relative or "."

    command_type = Annotated[str, AfterValidator(command)]
    directory_type = Annotated[str, AfterValidator(working_directory)]
    return create_model(
        "RunCommandArguments",
        __base__=_RunCommandArguments,
        command=(command_type, Field(description="Exact /bin/sh command.")),
        working_directory=(
            directory_type,
            Field(default=".", description="Workspace-relative directory."),
        ),
    )


def _environment(temporary_path: Path) -> dict[str, str]:
    environment = {
        name: os.environ[name]
        for name in ("LANG", "LC_ALL", "LC_CTYPE")
        if name in os.environ
    }
    environment.update(
        {
            "PATH": COMMAND_PATH,
            "HOME": str(temporary_path),
            "TMPDIR": str(temporary_path),
            "TERM": "dumb",
            "CI": "1",
        }
    )
    return environment


def _tool_stream(stream: SandboxStream) -> ToolOutputStream:
    return ToolOutputStream(stream.value)


class ShellCapability:
    """Contribute one native-sandboxed shell tool for the active workspace."""

    def __init__(
        self,
        provider_factory: SandboxProviderFactory,
        *,
        max_command_bytes: int = MAX_COMMAND_BYTES,
        max_command_seconds: int = MAX_COMMAND_SECONDS,
        max_output_bytes: int = MAX_OUTPUT_BYTES,
    ) -> None:
        if min(max_command_bytes, max_command_seconds, max_output_bytes) < 1:
            raise ValueError("shell limits must be positive")
        self._provider_factory = provider_factory
        self._max_command_bytes = max_command_bytes
        self._max_command_seconds = max_command_seconds
        self._max_output_bytes = max_output_bytes

    async def instructions(self, context: RunContext) -> tuple[str, ...]:
        del context
        return (
            "Use run_command for non-interactive shell work inside the "
            "workspace. Commands run through /bin/sh under the native sandbox, "
            "have no stdin or terminal, and always require approval.",
        )

    async def tools(self, context: RunContext) -> tuple[Tool, ...]:
        provider = await self._provider_factory()
        return (
            _RunCommandTool(
                context.workspace_path,
                provider,
                max_command_bytes=self._max_command_bytes,
                max_command_seconds=self._max_command_seconds,
                max_output_bytes=self._max_output_bytes,
            ),
        )
