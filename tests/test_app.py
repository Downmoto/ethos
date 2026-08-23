import asyncio
import logging
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import click
import pytest
import yaml  # type: ignore[import-untyped]
from click.testing import CliRunner

from ethos import app
from ethos.home import initialise_home
from ethos.models import Usage
from ethos.service import ApprovalChunk, ChatChunk, Ethos, RequestContext
from ethos.tools import ToolEffect


def test_otel_detach_context_error_is_suppressed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("opentelemetry.context")
    with caplog.at_level(logging.ERROR, logger=logger.name):
        logger.error("Failed to detach context")
        logger.error("another telemetry failure")

    assert "Failed to detach context" not in caplog.text
    assert "another telemetry failure" in caplog.text


def test_init_command_initialises_default_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app, "HOME_PATH", tmp_path / ".ethos")

    result = CliRunner().invoke(app.main, ["init"])

    assert result.exit_code == 0
    assert (tmp_path / ".ethos/config.yaml").exists()
    assert (tmp_path / ".ethos/data/ethos.db").exists()


def test_init_reports_existing_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / ".ethos"
    home.mkdir()
    monkeypatch.setattr(app, "HOME_PATH", home)

    result = CliRunner().invoke(app.main, ["init"])

    assert result.exit_code == 1
    assert "ethos home already exists" in result.output
    assert "Traceback" not in result.output


def test_onboarding_configures_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = initialise_home(tmp_path / ".ethos")
    monkeypatch.setattr(app, "HOME_PATH", home)

    result = CliRunner().invoke(
        app.main,
        ["onboard"],
        input="openai\ngpt-5-mini\nnone\ntest-key\n",
    )

    config = yaml.safe_load((home / "config.yaml").read_text())
    assert result.exit_code == 0
    assert config["provider"]["name"] == "openai"
    assert config["provider"]["reasoning_effort"] == "none"


def test_start_runs_vox_in_foreground(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = initialise_home(tmp_path / ".ethos")
    monkeypatch.setattr(app, "HOME_PATH", home)
    tracked: list[bool] = []

    async def serve(*, tracked: bool) -> None:
        tracked_values.append(tracked)

    tracked_values = tracked
    monkeypatch.setattr(app, "_serve", serve)

    result = CliRunner().invoke(app.main, ["start"])

    assert result.exit_code == 0
    assert tracked == [False]


def test_start_launches_background_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = initialise_home(tmp_path / ".ethos")
    monkeypatch.setattr(app, "HOME_PATH", home)
    monkeypatch.setattr(app, "_launch_background", lambda: 1234)

    result = CliRunner().invoke(app.main, ["start", "--bg"])

    assert result.exit_code == 0
    assert result.output == "ethos started in background (pid 1234)\n"


def test_background_launcher_waits_for_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = initialise_home(tmp_path / ".ethos")
    monkeypatch.setattr(app, "HOME_PATH", home)
    pids = iter((None, 4321))
    monkeypatch.setattr(app, "background_pid", lambda _home: next(pids))
    calls: list[list[str]] = []

    class TestProcess:
        pid = 4321

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            raise AssertionError("ready process must not be terminated")

    def launch(arguments: list[str], **_options: object) -> TestProcess:
        calls.append(arguments)
        return TestProcess()

    monkeypatch.setattr(subprocess, "Popen", launch)

    assert app._launch_background() == 4321
    assert calls == [
        [sys.executable, "-m", "ethos.app", "start", "--background-child"]
    ]
    assert (home / "logs/vox.log").exists()


def test_stop_is_quiet_when_no_background_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = initialise_home(tmp_path / ".ethos")
    monkeypatch.setattr(app, "HOME_PATH", home)
    monkeypatch.setattr(app, "stop_background", lambda _home: False)

    result = CliRunner().invoke(app.main, ["stop"])

    assert result.exit_code == 0
    assert result.output == ""


def test_cli_uses_shared_service_for_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = initialise_home(tmp_path / ".ethos")
    monkeypatch.setattr(app, "HOME_PATH", home)

    created = CliRunner().invoke(app.main, ["workspace", "create", "health"])
    listed = CliRunner().invoke(app.main, ["workspace", "list"])

    assert created.exit_code == 0
    assert created.output == "workspace created: health\n"
    assert listed.output == "default\nhealth\n"


def test_ask_streams_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = initialise_home(tmp_path / ".ethos")
    monkeypatch.setattr(app, "HOME_PATH", home)

    async def chunks(_prompt: str) -> AsyncIterator[ChatChunk]:
        yield ChatChunk(
            text="hello",
            workspace="default",
            session_id="session",
            usage=Usage(
                input_tokens=2,
                output_tokens=1,
                reasoning_tokens=1,
                reasoning_tokens_estimated=True,
            ),
            done=True,
        )

    monkeypatch.setattr(app, "_ask_requests", chunks)

    result = CliRunner().invoke(app.main, ["ask", "hi"])

    assert result.exit_code == 0
    assert result.output == (
        "hello\nUsage: 2 input tokens, 1 output tokens, "
        "~1 reasoning tokens, 3 total tokens\n"
    )


def test_ask_renders_reasoning_separately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = initialise_home(tmp_path / ".ethos")
    monkeypatch.setattr(app, "HOME_PATH", home)

    async def chunks(_prompt: str) -> AsyncIterator[ChatChunk]:
        yield ChatChunk(
            text="thinking",
            text_kind="reasoning",
            workspace="default",
            session_id="session",
        )
        yield ChatChunk(
            text="answer",
            workspace="default",
            session_id="session",
            done=True,
        )

    monkeypatch.setattr(app, "_ask_requests", chunks)

    result = CliRunner().invoke(app.main, ["ask", "hi"])

    assert result.exit_code == 0
    assert result.stdout == "answer\n"
    assert "Reasoning\nthinking" in result.stderr


def approval_chunk() -> ApprovalChunk:
    return ApprovalChunk(
        approval_id="approval-1",
        call_id="call-1",
        tool_name="write_file",
        arguments={"path": "README.md", "content": "hello"},
        effect=ToolEffect.WRITE,
        reason="write tool requires approval",
        created_at=datetime(2026, 8, 21, tzinfo=UTC),
        workspace="default",
        session_id="session-1",
    )


def test_cli_asks_once_with_exact_tool_and_arguments(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    prompts: list[str] = []
    monkeypatch.setattr(app, "_is_interactive", lambda: True)

    def confirm(prompt: str, **_kwargs: object) -> bool:
        prompts.append(prompt)
        return True

    monkeypatch.setattr(click, "confirm", confirm)

    assert app._approval_decision(approval_chunk())

    assert prompts == ["Approve this tool call?"]
    error = capsys.readouterr().err
    assert "Tool approval required: write_file" in error
    assert 'Arguments: {"content": "hello", "path": "README.md"}' in error


def test_noninteractive_cli_denies_without_prompt(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(app, "_is_interactive", lambda: False)
    monkeypatch.setattr(
        click,
        "confirm",
        lambda *_args, **_kwargs: pytest.fail("must not prompt"),
    )

    assert not app._approval_decision(approval_chunk())

    assert "Denied: input is not interactive" in capsys.readouterr().err


def test_cli_resumes_stream_with_denial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decisions: list[bool] = []

    class FakeEthos:
        async def resolve_approval(
            self,
            workspace: str,
            session_id: str,
            approval_id: str,
            approved: bool,
            context: RequestContext,
        ) -> AsyncIterator[ChatChunk]:
            del workspace, session_id, approval_id, context
            decisions.append(approved)
            yield ChatChunk(
                text="denied safely",
                workspace="default",
                session_id="session-1",
                done=True,
            )

    async def initial() -> AsyncIterator[ApprovalChunk]:
        yield approval_chunk()

    monkeypatch.setattr(app, "_approval_decision", lambda _approval: False)

    async def collect() -> list[object]:
        return [
            event
            async for event in app._resolve_cli_approvals(
                cast(Ethos, FakeEthos()),
                initial(),
                RequestContext("test", "owner", {}),
            )
        ]

    events = asyncio.run(collect())

    assert decisions == [False]
    assert isinstance(events[0], ApprovalChunk)
    assert isinstance(events[1], ChatChunk)
    assert events[1].text == "denied safely"
