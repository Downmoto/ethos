import logging
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]
from click.testing import CliRunner

from ethos import app
from ethos.home import initialise_home
from ethos.service import ChatChunk


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
        input="openai\ngpt-5-mini\ntest-key\n",
    )

    config = yaml.safe_load((home / "config.yaml").read_text())
    assert result.exit_code == 0
    assert config["provider"]["name"] == "openai"


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
            done=True,
        )

    monkeypatch.setattr(app, "_ask_requests", chunks)

    result = CliRunner().invoke(app.main, ["ask", "hi"])

    assert result.exit_code == 0
    assert "hello" in result.output
