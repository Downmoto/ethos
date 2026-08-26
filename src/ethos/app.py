"""Ethos command-line interface and application composition root."""

import asyncio
import getpass
import json
import logging
import shutil
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from functools import wraps
from pathlib import Path
from typing import TypeGuard, cast

import click
from pydantic import ValidationError

from ethos.capability_config import CapabilityName
from ethos.config import HOME_PATH, EthosSettings, get_settings
from ethos.gateway import (
    BackgroundAlreadyRunning,
    VoxServer,
    background_pid,
    run_background,
    stop_background,
)
from ethos.home import initialise_home
from ethos.models import (
    Message,
    ReasoningPart,
    TextPart,
    ToolCallPart,
)
from ethos.onboarding import run_onboarding
from ethos.service import (
    ApprovalChunk,
    CapabilityView,
    ChatEvent,
    Ethos,
    RequestContext,
)
from ethos.workspaces import DEFAULT_WORKSPACE

ETHOS_EXIT_LOGO: str = """
       █▄ █▄
      ▄██▄██
 ▄█▀█▄ ██ ████▄ ▄███▄ ▄██▀█
 ██▄█▀ ██ ██ ██ ██ ██ ▀███▄
▄▀█▄▄▄▄██▄██ ██▄▀███▀█▄▄██▀
"""


class _IgnoreOtelDetachContextError(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.getMessage() != "Failed to detach context"


logging.getLogger("opentelemetry.context").addFilter(
    _IgnoreOtelDetachContextError()
)


async def _print_response(
    chunks: AsyncIterator[ChatEvent], *, print_usage: bool = False
) -> None:
    wrote_output = False
    wrote_reasoning = False
    usage = None
    session_id = None
    try:
        async for chunk in chunks:
            if isinstance(chunk, ApprovalChunk):
                continue
            if chunk.usage is not None:
                usage = chunk.usage
            if session_id is None:
                session_id = chunk.session_id
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
    if print_usage and usage is not None:
        reasoning_tokens = (
            f"~{usage.reasoning_tokens}"
            if usage.reasoning_tokens_estimated
            else str(usage.reasoning_tokens)
        )
        click.secho(ETHOS_EXIT_LOGO, fg="green")
        click.echo(
            f"Usage: {usage.input_tokens} input tokens, "
            f"{usage.output_tokens} output tokens, "
            f"{reasoning_tokens} reasoning tokens, "
            f"{usage.total_tokens} total tokens\n"
            f"Session ID: {session_id}"
        )


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
) -> AsyncIterator[ChatEvent]:
    with Ethos(HOME_PATH) as ethos:
        context = _cli_context()
        async for chunk in _resolve_cli_approvals(
            ethos,
            ethos.chat(workspace, session_id, prompt, context),
            context,
        ):
            yield chunk


async def _ask_requests(prompt: str) -> AsyncIterator[ChatEvent]:
    with Ethos(HOME_PATH) as ethos:
        context = _cli_context()
        created = await ethos.create_session(DEFAULT_WORKSPACE, context)
        async for chunk in _resolve_cli_approvals(
            ethos,
            ethos.chat(DEFAULT_WORKSPACE, created.id, prompt, context),
            context,
        ):
            yield chunk


async def _resolve_cli_approvals(
    ethos: Ethos,
    events: AsyncIterator[ChatEvent],
    context: RequestContext,
) -> AsyncIterator[ChatEvent]:
    """Drain and resume each approval pause until the chat actually finishes."""

    while True:
        approval: ApprovalChunk | None = None
        async for event in events:
            yield event
            if isinstance(event, ApprovalChunk):
                approval = event
        if approval is None:
            return
        events = ethos.resolve_approval(
            approval.workspace,
            approval.session_id,
            approval.approval_id,
            _approval_decision(approval),
            context,
        )


def _approval_decision(approval: ApprovalChunk) -> bool:
    click.echo(f"Tool approval required: {approval.tool_name}", err=True)
    click.echo(
        f"Arguments: {json.dumps(approval.arguments, sort_keys=True)}",
        err=True,
    )
    click.echo(f"Reason: {approval.reason}", err=True)
    if not _is_interactive():
        click.echo("Denied: input is not interactive", err=True)
        return False
    return click.confirm("Approve this tool call?", default=False, err=True)


def _is_interactive() -> bool:
    return sys.stdin.isatty()


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
    """Create a workspace."""
    view = _run(lambda ethos, context: ethos.create_workspace(name, context))
    click.echo(f"workspace created: {view.name}")


@workspace.command("list")
@requires_home
def workspace_list() -> None:
    """List all workspaces."""
    views = _run(lambda ethos, context: ethos.list_workspaces(context))
    click.echo("\n".join(view.name for view in views))


@workspace.command("show")
@click.argument("name")
@requires_home
def workspace_show(name: str) -> None:
    """Show a workspace and its path."""
    view = _run(lambda ethos, context: ethos.show_workspace(name, context))
    click.echo(f"{view.name}\t{view.path}")


@main.group()
def config() -> None:
    """Manage Ethos configuration."""


@config.group()
def capability() -> None:
    """Manage global and workspace capabilities."""


@capability.command("list")
@click.option(
    "--workspace",
    metavar="NAME",
    help="Show effective settings for this workspace instead of globally.",
)
@requires_home
def capability_list(workspace: str | None) -> None:
    """List registered capabilities and their effective settings."""
    views = _run(
        lambda ethos, context: ethos.list_capabilities(context, workspace)
    )
    click.echo("\n\n".join(_format_capability(view) for view in views))


@capability.command("show")
@click.argument(
    "capability_name",
    metavar="CAPABILITY",
    type=click.Choice([name.value for name in CapabilityName]),
)
@click.option(
    "--workspace",
    metavar="NAME",
    help="Show effective settings for this workspace instead of globally.",
)
@requires_home
def capability_show(capability_name: str, workspace: str | None) -> None:
    """Show configured and effective settings for CAPABILITY.

    CAPABILITY is a registered capability name shown by ``list``.
    """
    view = _run(
        lambda ethos, context: ethos.show_capability(
            capability_name, context, workspace
        )
    )
    click.echo(_format_capability(view))


@capability.command("set")
@click.argument(
    "capability_name",
    metavar="CAPABILITY",
    type=click.Choice([name.value for name in CapabilityName]),
)
@click.argument("settings", metavar="SETTINGS_JSON")
@click.option(
    "--workspace",
    metavar="NAME",
    help="Apply changes as sparse overrides for this workspace.",
)
@requires_home
def capability_set(
    capability_name: str,
    settings: str,
    workspace: str | None,
) -> None:
    """Apply SETTINGS_JSON changes to CAPABILITY.

    CAPABILITY is a registered capability name shown by ``list``.
    SETTINGS_JSON must be a JSON object containing fields to change.
    """
    try:
        decoded = json.loads(settings)
    except json.JSONDecodeError as error:
        raise click.ClickException(
            f"invalid settings JSON: {error.msg}"
        ) from error
    if not isinstance(decoded, dict):
        raise click.ClickException("capability settings must be a JSON object")
    changes = cast(dict[str, object], decoded)
    view = _run(
        lambda ethos, context: ethos.configure_capability(
            capability_name,
            changes,
            context,
            workspace,
        )
    )
    click.echo(_format_capability(view))


@capability.command("reset")
@click.argument(
    "capability_name",
    metavar="CAPABILITY",
    type=click.Choice([name.value for name in CapabilityName]),
)
@click.option(
    "--workspace",
    metavar="NAME",
    required=True,
    help="Workspace whose capability override should be removed.",
)
@requires_home
def capability_reset(capability_name: str, workspace: str) -> None:
    """Reset CAPABILITY to global inheritance for a workspace.

    CAPABILITY is a registered capability name shown by ``list``.
    """
    view = _run(
        lambda ethos, context: ethos.reset_capability_override(
            workspace, capability_name, context
        )
    )
    click.echo(_format_capability(view))


def _format_capability(view: CapabilityView) -> str:
    scope = (
        "global" if view.workspace is None else f"workspace: {view.workspace}"
    )
    lines = [f"{view.name.value} ({scope})"]
    for heading, settings in (
        ("Configured", view.configured),
        ("Effective", view.effective),
    ):
        lines.append(f"{heading}:")
        lines.extend(
            (
                f"  {name}: {json.dumps(value)}"
                for name, value in settings.items()
            )
            if settings
            else ("  (inherited)",)
        )
    return "\n".join(lines)


@main.group()
def session() -> None:
    """Manage workspace sessions."""


@session.command("create")
@click.argument("workspace_name", metavar="WORKSPACE")
@requires_home
def session_create(workspace_name: str) -> None:
    """Create a session in a workspace."""
    view = _run(
        lambda ethos, context: ethos.create_session(workspace_name, context)
    )
    click.echo(f"session created: {view.id}")


@session.command("list")
@click.argument("workspace_name", metavar="WORKSPACE")
@requires_home
def session_list(workspace_name: str) -> None:
    """List a workspace's sessions."""
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
    """Show a session and its status."""
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
    """Print a session's complete message history."""
    messages = _run(
        lambda ethos, context: ethos.session_history(
            workspace_name, session_id, context
        )
    )
    click.echo("\n\n".join(_format_history_message(item) for item in messages))


def _format_history_message(message: Message) -> str:
    parts: list[str] = []
    for part in message.parts:
        if isinstance(part, TextPart):
            parts.append(part.text)
        elif isinstance(part, ReasoningPart):
            parts.append(f"reasoning: {part.text}")
        elif isinstance(part, ToolCallPart):
            parts.append(
                f"tool call {part.name} ({part.call_id}): {part.arguments_json}"
            )
        else:
            outcome = " error" if part.is_error else ""
            parts.append(
                f"tool result{outcome} {part.name} ({part.call_id}): "
                f"{part.content}"
            )
    return f"{message.role.value}: " + "\n".join(parts)


@session.command("archive")
@click.argument("workspace_name", metavar="WORKSPACE")
@click.argument("session_id", metavar="SESSION")
@requires_home
def session_archive(workspace_name: str, session_id: str) -> None:
    """Archive a session."""
    view = _run(
        lambda ethos, context: ethos.archive_session(
            workspace_name, session_id, context
        )
    )
    click.echo(f"session archived: {view.id}")


@session.command("recover")
@click.argument("workspace_name", metavar="WORKSPACE")
@click.argument("session_id", metavar="SESSION")
@requires_home
def session_recover(workspace_name: str, session_id: str) -> None:
    """Close interrupted tool calls without replaying them."""
    view = _run(
        lambda ethos, context: ethos.recover_session(
            workspace_name, session_id, context
        )
    )
    click.echo(f"session recovered: {view.id}")


@session.command("chat")
@click.argument("workspace_name", metavar="WORKSPACE")
@click.argument("session_id", metavar="SESSION")
@click.argument("prompt")
@requires_home
def session_chat(workspace_name: str, session_id: str, prompt: str) -> None:
    """Continue a session with one prompt."""
    try:
        asyncio.run(
            _print_response(_chat_requests(workspace_name, session_id, prompt))
        )
    except Exception as error:
        raise click.ClickException(str(error)) from error


@main.command()
@click.argument("prompt")
@requires_home
def ask(prompt: str) -> None:
    """Send one prompt in a fresh default-workspace session."""
    try:
        asyncio.run(_print_response(_ask_requests(prompt), print_usage=True))
    except ValidationError as error:
        message = (
            "ethos is not configured. Run [ethos onboard] first."
            if error.title == EthosSettings.__name__
            else str(error)
        )
        raise click.ClickException(message) from error
    except Exception as error:
        raise click.ClickException(str(error)) from error


if __name__ == "__main__":
    main()
