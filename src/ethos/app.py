import asyncio
import getpass
import logging
import math
import shutil
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from functools import wraps
from pathlib import Path
from time import monotonic

import click
from pydantic import JsonValue, ValidationError

from ethos.commands import (
    CommandDispatcher,
    CommandRequest,
    register_workspace_commands,
)
from ethos.config import (
    HOME_PATH,
    EthosSettings,
    load_events_config,
)
from ethos.events import create_event_emitter
from ethos.home import DB_PATH as HOME_DB_PATH
from ethos.home import initialise_home
from ethos.onboarding import run_onboarding
from ethos.runtime import PromptStreamEvent, run_prompt_singleton
from ethos.storage import Storage
from ethos.workspaces import WORKSPACES_DIR, WorkspaceManager


class _IgnoreOtelDetachContextError(logging.Filter):
    """Hide OpenTelemetry's harmless cross-context cleanup traceback."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.getMessage() != "Failed to detach context"


# OpenTelemetry catches this ContextVar cleanup failure internally, and the
# completed model response remains valid. Suppress only this known noisy record.
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

    def update(self, event: PromptStreamEvent) -> None:
        self._characters += len(event.text)
        usage = (
            event.usage if event.usage and event.usage.total_tokens else None
        )
        action = "Wrote" if event.done else "Writing"

        if usage is None:
            tokens = f"~{math.ceil(self._characters / 4):,} output tokens"
        else:
            total = usage.total_tokens
            tokens = (
                f"{usage.input_tokens:,} input + {usage.output_tokens:,} "
                f"output = {total:,} tokens"
            )

        self._render(
            f"{action} {self._output_path} · {tokens} · "
            f"{monotonic() - self._started_at:.1f}s",
            done=event.done,
        )

    def fail(self) -> None:
        self._render(
            f"Stopped {self._output_path} · partial output retained",
            done=True,
        )

    def _render(self, status: str, *, done: bool) -> None:
        self._line_width = max(self._line_width, len(status))
        click.echo(
            f"\r{status.ljust(self._line_width)}",
            nl=done,
            err=True,
        )


async def _stream_response(prompt: str) -> AsyncIterator[PromptStreamEvent]:
    status = _ThinkingStatus()
    status.render()
    status_task: asyncio.Task[None] | None = asyncio.create_task(status.show())
    try:
        async for event in run_prompt_singleton(prompt):
            if status_task is not None and (event.text or event.done):
                status_task.cancel()
                with suppress(asyncio.CancelledError):
                    await status_task
                status.clear()
                status_task = None
            yield event
    finally:
        if status_task is not None:
            status_task.cancel()
            with suppress(asyncio.CancelledError):
                await status_task
            status.clear()


async def _print_response(prompt: str) -> None:
    wrote_output = False
    try:
        async for event in _stream_response(prompt):
            if event.text:
                click.echo(event.text, nl=False)
                wrote_output = True
    finally:
        if wrote_output:
            click.echo()


async def _write_response(prompt: str, output_path: Path) -> None:
    output = output_path.open("x", encoding="utf-8")
    tracker = _TokenTracker(output_path)
    try:
        with output:
            async for event in _stream_response(prompt):
                if event.text:
                    output.write(event.text)
                    output.flush()
                tracker.update(event)
    except Exception:
        tracker.fail()
        raise


async def _execute_and_print_command_events(
    dispatcher: CommandDispatcher, request: CommandRequest
) -> None:
    wrote_output = False
    async for event in dispatcher.execute(request):
        if event.text:
            click.echo(event.text, nl=False)
            wrote_output = True
    if wrote_output:
        click.echo()


def _run_cli_command(name: str, arguments: dict[str, JsonValue]) -> None:
    try:
        storage = Storage(HOME_PATH / HOME_DB_PATH)
        try:
            dispatcher = CommandDispatcher()
            register_workspace_commands(
                dispatcher,
                WorkspaceManager(HOME_PATH / WORKSPACES_DIR),
                create_event_emitter(storage, load_events_config(HOME_PATH)),
            )
            request = CommandRequest(
                name=name,
                arguments=arguments,
                source="cli",
                owner_id=getpass.getuser(),
                external_context={"cwd": str(Path.cwd())},
            )
            asyncio.run(_execute_and_print_command_events(dispatcher, request))
        finally:
            storage.close()
    except Exception as error:
        raise click.ClickException(str(error)) from error


####### CLI #######


def requires_home[**P, R](command: Callable[P, R]) -> Callable[P, R]:
    """Require an initialised ethos home for a command."""

    @wraps(command)
    def guarded(*args: P.args, **kwargs: P.kwargs) -> R:
        if not HOME_PATH.is_dir():
            raise click.ClickException(
                "ethos is not initialised. Run [ethos init] first."
            )
        return command(*args, **kwargs)

    return guarded


@click.group()
def main() -> None:
    """agent harness"""


@main.command()
@click.option(
    "-r",
    "--reinitialise",
    is_flag=True,
    help="reinitialise a fresh app dir",
)
def init(reinitialise: bool) -> None:
    """(re)initialise ethos app directory at ~/"""

    try:
        if reinitialise:
            if click.confirm(
                "Are you sure you want to reinitialise ethos?\n"
                f"This will permanently delete {HOME_PATH}"
            ):
                initialise_home(HOME_PATH, reinitialise=True)
                click.echo(f".ethos initialised at: {HOME_PATH}")
            else:
                click.echo("Aborted!")
            return

        initialise_home(HOME_PATH)
        click.echo(f".ethos initialised at: {HOME_PATH}")
    except FileExistsError as error:
        raise click.ClickException(
            f"{error}.\nRun [ethos init --reinitialise] to replace it."
        ) from error


@main.command()
def uninit() -> None:
    """Remove the ethos app directory."""
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
    """Configure the settings required to run ethos."""
    run_onboarding(HOME_PATH)
    click.echo(f"ethos configured at: {HOME_PATH}")


@main.group()
def workspace() -> None:
    """Manage ethos workspaces."""


@workspace.command("create")
@click.argument("name")
@requires_home
def workspace_create(name: str) -> None:
    """Create a workspace."""
    _run_cli_command("workspace.create", {"name": name})


@workspace.command("list")
@requires_home
def workspace_list() -> None:
    """List workspaces."""
    _run_cli_command("workspace.list", {})


@workspace.command("show")
@click.argument("name")
@requires_home
def workspace_show(name: str) -> None:
    """Show a workspace."""
    _run_cli_command("workspace.show", {"name": name})


@main.command()
@click.argument("prompt")
@click.option(
    "-o",
    "--to",
    "output_path",
    type=click.Path(path_type=Path, dir_okay=False),
    help="Write the streamed response to a new file.",
)
@requires_home
def ask(prompt: str, output_path: Path | None) -> None:
    """Send one prompt to the configured model."""
    try:
        if output_path is None:
            asyncio.run(_print_response(prompt))
        else:
            asyncio.run(_write_response(prompt, output_path))
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


@main.command(hidden=True, add_help_option=False)
def debug() -> None:
    """development manual debug command, end users should not be seeing this"""
    click.echo("DEBUG")


if __name__ == "__main__":
    main()
