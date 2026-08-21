"""Ethos command-line interface and application composition root."""

import asyncio
import getpass
import logging
import math
import shutil
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from functools import wraps
from pathlib import Path
from time import monotonic
from typing import TypeGuard

import click
from pydantic import ValidationError

from ethos.config import HOME_PATH, EthosSettings, get_settings
from ethos.gateway import (
    BackgroundAlreadyRunning,
    VoxServer,
    background_pid,
    run_background,
    stop_background,
)
from ethos.home import initialise_home
from ethos.onboarding import run_onboarding
from ethos.service import ChatChunk, Ethos, RequestContext
from ethos.workspaces import DEFAULT_WORKSPACE


class _IgnoreOtelDetachContextError(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.getMessage() != "Failed to detach context"


logging.getLogger("opentelemetry.context").addFilter(
    _IgnoreOtelDetachContextError()
)


class _ThinkingStatus:
    def __init__(self) -> None:
        self._started_at = monotonic()
        self._line_width = 0

    async def show(self) -> None:
        while True:
            await asyncio.sleep(0.1)
            self.render()

    def render(self) -> None:
        status = f"Thinking · {monotonic() - self._started_at:.1f}s"
        self._line_width = max(self._line_width, len(status))
        click.echo(f"\r{status}", nl=False, err=True)

    def clear(self) -> None:
        click.echo(f"\r{' ' * self._line_width}\r", nl=False, err=True)


class _TokenTracker:
    def __init__(self, output_path: Path) -> None:
        self._output_path = output_path
        self._characters = 0
        self._line_width = 0
        self._started_at = monotonic()

    def update(self, chunk: ChatChunk) -> None:
        if chunk.text_kind == "answer":
            self._characters += len(chunk.text)
        usage = (
            chunk.usage if chunk.usage and chunk.usage.total_tokens else None
        )
        action = "Wrote" if chunk.done else "Writing"
        tokens = (
            f"~{math.ceil(self._characters / 4):,} output tokens"
            if usage is None
            else f"{usage.input_tokens:,} input + {usage.output_tokens:,} "
            f"output = {usage.total_tokens:,} tokens"
        )
        self._render(
            f"{action} {self._output_path} · {tokens} · "
            f"{monotonic() - self._started_at:.1f}s",
            done=chunk.done,
        )

    def fail(self) -> None:
        self._render(
            f"Stopped {self._output_path} · partial output retained",
            done=True,
        )

    def _render(self, status: str, *, done: bool) -> None:
        self._line_width = max(self._line_width, len(status))
        click.echo(f"\r{status.ljust(self._line_width)}", nl=done, err=True)


async def _stream_response(
    chunks: AsyncIterator[ChatChunk],
) -> AsyncIterator[ChatChunk]:
    status = _ThinkingStatus()
    status.render()
    status_task: asyncio.Task[None] | None = asyncio.create_task(status.show())
    try:
        async for chunk in chunks:
            if status_task is not None and (chunk.text or chunk.done):
                status_task.cancel()
                with suppress(asyncio.CancelledError):
                    await status_task
                status.clear()
                status_task = None
            yield chunk
    finally:
        if status_task is not None:
            status_task.cancel()
            with suppress(asyncio.CancelledError):
                await status_task
            status.clear()


async def _print_response(chunks: AsyncIterator[ChatChunk]) -> None:
    wrote_output = False
    wrote_reasoning = False
    try:
        async for chunk in _stream_response(chunks):
            if chunk.text and chunk.text_kind == "reasoning":
                if not wrote_reasoning:
                    click.echo("Reasoning", err=True)
                click.secho(chunk.text, nl=False, dim=True, err=True)
                wrote_reasoning = True
            elif chunk.text:
                if wrote_reasoning and not wrote_output:
                    click.echo(err=True)
                click.echo(chunk.text, nl=False)
                wrote_output = True
    finally:
        if wrote_output:
            click.echo()
        elif wrote_reasoning:
            click.echo(err=True)


async def _write_response(
    chunks: AsyncIterator[ChatChunk], output_path: Path
) -> None:
    output = output_path.open("x", encoding="utf-8")
    tracker = _TokenTracker(output_path)
    try:
        with output:
            async for chunk in _stream_response(chunks):
                if chunk.text and chunk.text_kind == "answer":
                    output.write(chunk.text)
                    output.flush()
                tracker.update(chunk)
    except Exception:
        tracker.fail()
        raise


def _cli_context() -> RequestContext:
    return RequestContext(
        source="cli",
        owner_id=getpass.getuser(),
        external_context={"cwd": str(Path.cwd())},
    )


async def _call[Result](
    operation: Callable[[Ethos, RequestContext], Awaitable[Result]],
) -> Result:
    with Ethos(HOME_PATH) as ethos:
        return await operation(ethos, _cli_context())


def _run[Result](
    operation: Callable[[Ethos, RequestContext], Awaitable[Result]],
) -> Result:
    try:
        return asyncio.run(_call(operation))
    except Exception as error:
        raise click.ClickException(str(error)) from error


async def _chat_requests(
    workspace: str, session_id: str, prompt: str
) -> AsyncIterator[ChatChunk]:
    with Ethos(HOME_PATH) as ethos:
        async for chunk in ethos.chat(
            workspace, session_id, prompt, _cli_context()
        ):
            yield chunk


async def _ask_requests(prompt: str) -> AsyncIterator[ChatChunk]:
    with Ethos(HOME_PATH) as ethos:
        context = _cli_context()
        created = await ethos.create_session(DEFAULT_WORKSPACE, context)
        async for chunk in ethos.chat(
            DEFAULT_WORKSPACE, created.id, prompt, context
        ):
            yield chunk


async def _serve(*, tracked: bool) -> None:
    settings = get_settings()
    with Ethos(HOME_PATH) as ethos:
        server = VoxServer(settings.gateway)
        if tracked:
            await run_background(HOME_PATH, lambda: server.run(ethos))
        else:
            await server.run(ethos)


def _launch_background() -> int:
    if background_pid(HOME_PATH) is not None:
        raise BackgroundAlreadyRunning(
            "ethos is already running in the background"
        )

    logs = HOME_PATH / "logs"
    logs.mkdir(mode=0o700, exist_ok=True)
    logs.chmod(0o700)
    log_path = logs / "vox.log"
    log_path.touch(mode=0o600, exist_ok=True)
    log_path.chmod(0o600)
    with log_path.open("a", encoding="utf-8") as output:
        process = subprocess.Popen(
            [sys.executable, "-m", "ethos.app", "start", "--background-child"],
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"ethos failed to start; see log: {log_path}")
        if background_pid(HOME_PATH) == process.pid:
            return process.pid
        time.sleep(0.05)

    process.terminate()
    raise RuntimeError(f"ethos start timed out; see log: {log_path}")


def _is_exception_group(
    error: BaseException,
) -> TypeGuard[BaseExceptionGroup[BaseException]]:
    return isinstance(error, BaseExceptionGroup)


def _error_message(error: BaseException) -> str:
    while _is_exception_group(error) and len(error.exceptions) == 1:
        error = error.exceptions[0]
    return str(error)


def requires_home[**P, Result](
    command: Callable[P, Result],
) -> Callable[P, Result]:
    @wraps(command)
    def guarded(*args: P.args, **kwargs: P.kwargs) -> Result:
        if not HOME_PATH.is_dir():
            raise click.ClickException(
                "ethos is not initialised. Run [ethos init] first."
            )
        return command(*args, **kwargs)

    return guarded


@click.group()
def main() -> None:
    """Personal agent harness."""


@main.command()
@click.option("-r", "--reinitialise", is_flag=True)
def init(reinitialise: bool) -> None:
    """Initialise the Ethos home directory."""
    try:
        if reinitialise:
            if not click.confirm(
                "Are you sure you want to reinitialise ethos?\n"
                f"This will permanently delete {HOME_PATH}"
            ):
                click.echo("Aborted!")
                return
            initialise_home(HOME_PATH, reinitialise=True)
        else:
            initialise_home(HOME_PATH)
        click.echo(f".ethos initialised at: {HOME_PATH}")
    except FileExistsError as error:
        raise click.ClickException(
            f"{error}.\nRun [ethos init --reinitialise] to replace it."
        ) from error


@main.command()
def uninit() -> None:
    """Remove the Ethos home directory."""
    if not HOME_PATH.is_dir():
        raise click.ClickException(f"ethos home does not exist at: {HOME_PATH}")
    if click.confirm(
        "Are you sure you want to uninitialise ethos?\n"
        f"This will permanently delete {HOME_PATH}"
    ):
        shutil.rmtree(HOME_PATH)
        click.echo(f".ethos removed from: {HOME_PATH}")
    else:
        click.echo("Aborted!")


@main.command()
@requires_home
def onboard() -> None:
    """Configure the model provider."""
    run_onboarding(HOME_PATH)
    click.echo(f"ethos configured at: {HOME_PATH}")


@main.command()
@click.option("--bg", is_flag=True, help="Run Vox in the background.")
@click.option("--background-child", is_flag=True, hidden=True)
@requires_home
def start(bg: bool, background_child: bool) -> None:
    """Start the Vox REST server."""
    try:
        if bg and background_child:
            raise ValueError("invalid background process options")
        if bg:
            pid = _launch_background()
            click.echo(f"ethos started in background (pid {pid})")
        else:
            asyncio.run(_serve(tracked=background_child))
    except ValidationError as error:
        message = (
            "ethos is not configured. Run [ethos onboard] first."
            if error.title == EthosSettings.__name__
            else str(error)
        )
        raise click.ClickException(message) from error
    except Exception as error:
        raise click.ClickException(_error_message(error)) from error


@main.command()
@requires_home
def stop() -> None:
    """Stop the background Vox server, if present."""
    if stop_background(HOME_PATH):
        click.echo("ethos stopped")


@main.group()
def workspace() -> None:
    """Manage Ethos workspaces."""


@workspace.command("create")
@click.argument("name")
@requires_home
def workspace_create(name: str) -> None:
    view = _run(lambda ethos, context: ethos.create_workspace(name, context))
    click.echo(f"workspace created: {view.name}")


@workspace.command("list")
@requires_home
def workspace_list() -> None:
    views = _run(lambda ethos, context: ethos.list_workspaces(context))
    click.echo("\n".join(view.name for view in views))


@workspace.command("show")
@click.argument("name")
@requires_home
def workspace_show(name: str) -> None:
    view = _run(lambda ethos, context: ethos.show_workspace(name, context))
    click.echo(f"{view.name}\t{view.path}")


@main.group()
def session() -> None:
    """Manage workspace sessions."""


@session.command("create")
@click.argument("workspace_name", metavar="WORKSPACE")
@requires_home
def session_create(workspace_name: str) -> None:
    view = _run(
        lambda ethos, context: ethos.create_session(workspace_name, context)
    )
    click.echo(f"session created: {view.id}")


@session.command("list")
@click.argument("workspace_name", metavar="WORKSPACE")
@requires_home
def session_list(workspace_name: str) -> None:
    views = _run(
        lambda ethos, context: ethos.list_sessions(workspace_name, context)
    )
    click.echo(
        "\n".join(
            f"{view.id}\t{'archived' if view.archived else 'active'}"
            for view in views
        )
    )


@session.command("show")
@click.argument("workspace_name", metavar="WORKSPACE")
@click.argument("session_id", metavar="SESSION")
@requires_home
def session_show(workspace_name: str, session_id: str) -> None:
    view = _run(
        lambda ethos, context: ethos.show_session(
            workspace_name, session_id, context
        )
    )
    status = "archived" if view.archived else "active"
    click.echo(f"{view.id}\t{view.workspace}\t{status}")


@session.command("history")
@click.argument("workspace_name", metavar="WORKSPACE")
@click.argument("session_id", metavar="SESSION")
@requires_home
def session_history(workspace_name: str, session_id: str) -> None:
    messages = _run(
        lambda ethos, context: ethos.session_history(
            workspace_name, session_id, context
        )
    )
    click.echo(
        "\n\n".join(f"{message.role}: {message.text}" for message in messages)
    )


@session.command("archive")
@click.argument("workspace_name", metavar="WORKSPACE")
@click.argument("session_id", metavar="SESSION")
@requires_home
def session_archive(workspace_name: str, session_id: str) -> None:
    view = _run(
        lambda ethos, context: ethos.archive_session(
            workspace_name, session_id, context
        )
    )
    click.echo(f"session archived: {view.id}")


@session.command("chat")
@click.argument("workspace_name", metavar="WORKSPACE")
@click.argument("session_id", metavar="SESSION")
@click.argument("prompt")
@requires_home
def session_chat(workspace_name: str, session_id: str, prompt: str) -> None:
    try:
        asyncio.run(
            _print_response(_chat_requests(workspace_name, session_id, prompt))
        )
    except Exception as error:
        raise click.ClickException(str(error)) from error


@main.command()
@click.argument("prompt")
@click.option(
    "-o",
    "--to",
    "output_path",
    type=click.Path(path_type=Path, dir_okay=False),
)
@requires_home
def ask(prompt: str, output_path: Path | None) -> None:
    """Send one prompt in a fresh default-workspace session."""
    try:
        if output_path is None:
            asyncio.run(_print_response(_ask_requests(prompt)))
        else:
            asyncio.run(_write_response(_ask_requests(prompt), output_path))
    except FileExistsError as error:
        raise click.ClickException(
            f"output file already exists: {output_path}"
        ) from error
    except ValidationError as error:
        message = (
            "ethos is not configured. Run [ethos onboard] first."
            if error.title == EthosSettings.__name__
            else str(error)
        )
        raise click.ClickException(message) from error
    except Exception as error:
        retained = (
            f"\nOutput retained at: {output_path}"
            if output_path is not None and output_path.exists()
            else ""
        )
        raise click.ClickException(f"{error}{retained}") from error


if __name__ == "__main__":
    main()
