import asyncio
import logging
import re
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]
from click.testing import CliRunner
from pydantic_ai.models.test import TestModel

from ethos import app
from ethos.commands import CommandResponse, CommandUsage
from ethos.config import EthosSettings, ProviderConfig
from ethos.gateways import Gateway
from ethos.home import initialise_home
from ethos.provider import AIProvider
from ethos.sessions import SessionManager
from ethos.workspaces import DEFAULT_WORKSPACE, WorkspaceManager


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
    assert (tmp_path / ".ethos" / "config.yaml").exists()
    assert (tmp_path / ".ethos" / "data" / "ethos.db").exists()


def test_init_command_reports_existing_home_without_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / ".ethos"
    home.mkdir()
    monkeypatch.setattr(app, "HOME_PATH", home)

    result = CliRunner().invoke(app.main, ["init"])

    assert result.exit_code == 1
    assert "Error: ethos home already exists:" in result.output
    assert "Run [ethos init --reinitialise] to replace it." in result.output
    assert "Traceback" not in result.output


def test_uninit_command_removes_home_after_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / ".ethos"
    home.mkdir()
    (home / "config.yaml").touch()
    monkeypatch.setattr(app, "HOME_PATH", home)

    result = CliRunner().invoke(app.main, ["uninit"], input="y\n")

    assert result.exit_code == 0
    assert not home.exists()
    assert f".ethos removed from: {home}" in result.output


def test_uninit_command_preserves_home_without_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / ".ethos"
    home.mkdir()
    monkeypatch.setattr(app, "HOME_PATH", home)

    result = CliRunner().invoke(app.main, ["uninit"], input="n\n")

    assert result.exit_code == 0
    assert home.exists()
    assert "Aborted!" in result.output


def test_onboarding_command_configures_openai(
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
    assert config["provider"]["model_name"] == "gpt-5-mini"
    assert config["keys"]["openai_api_key"] == "test-key"


def test_onboarding_command_configures_ollama(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = initialise_home(tmp_path / ".ethos")
    monkeypatch.setattr(app, "HOME_PATH", home)

    result = CliRunner().invoke(
        app.main,
        ["onboard"],
        input="ollama\nllama3.2\n\n\n",
    )

    config = yaml.safe_load((home / "config.yaml").read_text())
    assert result.exit_code == 0
    assert config["provider"]["name"] == "ollama"
    assert config["provider"]["model_name"] == "llama3.2"
    assert config["provider"]["ollama_base_url"] == (
        "http://localhost:11434/v1"
    )
    assert config["keys"]["ollama_api_key"] is None


def test_start_uses_explicit_gateway_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = initialise_home(tmp_path / ".ethos")
    monkeypatch.setattr(app, "HOME_PATH", home)
    monkeypatch.setattr(
        app,
        "get_settings",
        lambda: EthosSettings.model_validate(
            {
                "provider": {"name": "ollama", "model_name": "test"},
                "gateways": {"vox": {"enabled": False}},
            }
        ),
    )
    selected: list[tuple[str, ...]] = []

    async def capture_start(gateways: tuple[Gateway, ...]) -> None:
        selected.append(tuple(gateway.name for gateway in gateways))

    monkeypatch.setattr(app, "_start_gateways", capture_start)

    result = CliRunner().invoke(app.main, ["start", "--vox"])

    assert result.exit_code == 0
    assert selected == [("vox",)]


def test_start_uses_enabled_gateways_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = initialise_home(tmp_path / ".ethos")
    monkeypatch.setattr(app, "HOME_PATH", home)
    monkeypatch.setattr(
        app,
        "get_settings",
        lambda: EthosSettings.model_validate(
            {
                "provider": {"name": "ollama", "model_name": "test"},
                "gateways": {"vox": {"enabled": True}},
            }
        ),
    )
    selected: list[tuple[str, ...]] = []

    async def capture_start(gateways: tuple[Gateway, ...]) -> None:
        selected.append(tuple(gateway.name for gateway in gateways))

    monkeypatch.setattr(app, "_start_gateways", capture_start)

    result = CliRunner().invoke(app.main, ["start"])

    assert result.exit_code == 0
    assert selected == [("vox",)]


def test_start_rejects_unconfigured_explicit_gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = initialise_home(tmp_path / ".ethos")
    monkeypatch.setattr(app, "HOME_PATH", home)
    monkeypatch.setattr(
        app,
        "get_settings",
        lambda: EthosSettings.model_validate(
            {"provider": {"name": "ollama", "model_name": "test"}}
        ),
    )

    result = CliRunner().invoke(app.main, ["start", "--discord"])

    assert result.exit_code == 1
    assert result.output == "Error: discord requires a bot token\n"


def test_ask_command_prints_model_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / ".ethos"
    home.mkdir()
    monkeypatch.setattr(app, "HOME_PATH", home)

    async def stream_prompt(prompt: str) -> AsyncIterator[CommandResponse]:
        yield CommandResponse(text="reply: ")
        yield CommandResponse(text=prompt)
        yield CommandResponse(done=True)

    monkeypatch.setattr(app, "_ask_requests", stream_prompt)

    result = CliRunner().invoke(app.main, ["ask", "hello"])

    assert result.exit_code == 0
    assert result.stdout == "reply: hello\n"
    assert "Thinking · 0.0s" in result.stderr


def test_ask_command_updates_thinking_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / ".ethos"
    home.mkdir()
    monkeypatch.setattr(app, "HOME_PATH", home)

    async def stream_prompt(
        _prompt: str,
    ) -> AsyncIterator[CommandResponse]:
        await asyncio.sleep(0.15)
        yield CommandResponse(text="reply")

    monkeypatch.setattr(app, "_ask_requests", stream_prompt)

    result = CliRunner().invoke(app.main, ["ask", "hello"])

    assert result.exit_code == 0
    assert "Thinking · 0.0s" in result.stderr
    assert "Thinking · 0.1s" in result.stderr


def test_ask_command_writes_model_output_to_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / ".ethos"
    home.mkdir()
    output_path = tmp_path / "response.md"
    monkeypatch.setattr(app, "HOME_PATH", home)

    async def stream_prompt(_prompt: str) -> AsyncIterator[CommandResponse]:
        yield CommandResponse(text="streamed ")
        yield CommandResponse(text="response")
        yield CommandResponse(
            usage=CommandUsage(input_tokens=10, output_tokens=2),
            done=True,
        )

    monkeypatch.setattr(app, "_ask_requests", stream_prompt)

    result = CliRunner().invoke(
        app.main, ["ask", "hello", "--to", str(output_path)]
    )

    assert result.exit_code == 0
    assert output_path.read_text() == "streamed response"
    assert "streamed response" not in result.output
    assert "10 input + 2 output = 12 tokens" in result.stderr


def test_ask_command_retains_partial_file_on_stream_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / ".ethos"
    home.mkdir()
    output_path = tmp_path / "response.md"
    monkeypatch.setattr(app, "HOME_PATH", home)

    async def fail(_prompt: str) -> AsyncIterator[CommandResponse]:
        yield CommandResponse(text="partial response")
        raise ValueError("model context window exceeded")

    monkeypatch.setattr(app, "_ask_requests", fail)

    result = CliRunner().invoke(
        app.main, ["ask", "hello", "--to", str(output_path)]
    )

    assert result.exit_code == 1
    assert output_path.read_text() == "partial response"
    assert "Error: model context window exceeded" in result.output
    assert f"Output retained at: {output_path}" in result.output
    assert "Traceback" not in result.output


def test_ask_command_does_not_overwrite_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / ".ethos"
    home.mkdir()
    output_path = tmp_path / "response.md"
    output_path.write_text("keep me")
    monkeypatch.setattr(app, "HOME_PATH", home)

    result = CliRunner().invoke(
        app.main, ["ask", "hello", "--to", str(output_path)]
    )

    assert result.exit_code == 1
    assert output_path.read_text() == "keep me"
    assert (
        result.output == f"Error: output file already exists: {output_path}\n"
    )


def test_ask_command_reports_runtime_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / ".ethos"
    home.mkdir()
    monkeypatch.setattr(app, "HOME_PATH", home)

    async def fail(_prompt: str) -> AsyncIterator[CommandResponse]:
        raise ValueError("ETHOS_KEYS__OPENAI_API_KEY is required")
        yield  # required for return type, runtime error without

    monkeypatch.setattr(app, "_ask_requests", fail)

    result = CliRunner().invoke(app.main, ["ask", "hello"])

    assert result.exit_code == 1
    assert "Error: ETHOS_KEYS__OPENAI_API_KEY is required" in result.stderr
    assert "Traceback" not in result.output


def test_ask_command_requires_onboarding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = initialise_home(tmp_path / ".ethos")
    monkeypatch.setattr(app, "HOME_PATH", home)

    result = CliRunner().invoke(app.main, ["ask", "hello"])

    assert result.exit_code == 1
    assert (
        "Error: ethos is not configured. Run [ethos onboard] first."
        in result.output
    )
    assert "Traceback" not in result.output
    sessions = SessionManager(WorkspaceManager(home / "workspaces"))
    assert sessions.list(DEFAULT_WORKSPACE) == ()


def test_ask_command_preserves_other_validation_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / ".ethos"
    home.mkdir()
    monkeypatch.setattr(app, "HOME_PATH", home)

    async def fail(_prompt: str) -> AsyncIterator[CommandResponse]:
        ProviderConfig.model_validate({})
        yield CommandResponse()

    monkeypatch.setattr(app, "_ask_requests", fail)

    result = CliRunner().invoke(app.main, ["ask", "hello"])

    assert result.exit_code == 1
    assert "validation errors for ProviderConfig" in result.output
    assert "Run [ethos onboard]" not in result.output


def test_ask_command_requires_initialised_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app, "HOME_PATH", tmp_path / ".ethos")

    result = CliRunner().invoke(app.main, ["ask", "hello"])

    assert result.exit_code == 1
    assert result.output == (
        "Error: ethos is not initialised. Run [ethos init] first.\n"
    )


def test_workspace_create_command_uses_dispatcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = initialise_home(tmp_path / ".ethos")
    monkeypatch.setattr(app, "HOME_PATH", home)

    result = CliRunner().invoke(app.main, ["workspace", "create", "my-project"])

    assert result.exit_code == 0
    assert result.output == "workspace created: my-project\n"
    assert (home / "workspaces" / "my-project").is_dir()


def test_workspace_list_command_renders_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = initialise_home(tmp_path / ".ethos")
    monkeypatch.setattr(app, "HOME_PATH", home)
    CliRunner().invoke(app.main, ["workspace", "create", "my-project"])

    result = CliRunner().invoke(app.main, ["workspace", "list"])

    assert result.exit_code == 0
    assert result.output == "default\nmy-project\n"


def test_workspace_show_command_renders_name_and_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = initialise_home(tmp_path / ".ethos")
    monkeypatch.setattr(app, "HOME_PATH", home)

    result = CliRunner().invoke(app.main, ["workspace", "show", "default"])

    assert result.exit_code == 0
    assert result.output == f"default\t{home / 'workspaces/default'}\n"


def test_workspace_create_command_reports_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = initialise_home(tmp_path / ".ethos")
    monkeypatch.setattr(app, "HOME_PATH", home)
    runner = CliRunner()
    runner.invoke(app.main, ["workspace", "create", "my-project"])

    result = runner.invoke(app.main, ["workspace", "create", "my-project"])

    assert result.exit_code == 1
    assert result.output == "Error: workspace already exists: my-project\n"


def test_workspace_commands_require_initialised_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app, "HOME_PATH", tmp_path / ".ethos")

    result = CliRunner().invoke(app.main, ["workspace", "list"])

    assert result.exit_code == 1
    assert result.output == (
        "Error: ethos is not initialised. Run [ethos init] first.\n"
    )


def test_session_cli_commands_use_dispatcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = initialise_home(tmp_path / ".ethos")
    monkeypatch.setattr(app, "HOME_PATH", home)
    runner = CliRunner()

    created = runner.invoke(app.main, ["session", "create", "default"])
    match = re.fullmatch(r"session created: ([0-9a-f-]+)\n", created.output)
    assert created.exit_code == 0
    assert match is not None
    session_id = match.group(1)

    listed = runner.invoke(app.main, ["session", "list", "default"])
    shown = runner.invoke(app.main, ["session", "show", "default", session_id])
    archived = runner.invoke(
        app.main, ["session", "archive", "default", session_id]
    )

    assert listed.output == f"{session_id}\tactive\n"
    assert shown.output == f"{session_id}\tdefault\tactive\n"
    assert archived.output == f"session archived: {session_id}\n"


def test_ask_creates_one_shot_default_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = initialise_home(tmp_path / ".ethos")
    (home / "config.yaml").write_text(
        "events:\n  enabled: false\n  print_events: false\n"
        "provider:\n  name: ollama\n  model_name: test\n"
        "keys: {}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(app, "HOME_PATH", home)
    monkeypatch.setattr(
        AIProvider,
        "model",
        lambda _provider, _model_name: TestModel(  # pyright: ignore
            custom_output_text="reply"
        ),  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    )

    result = CliRunner().invoke(app.main, ["ask", "hello"])

    sessions = SessionManager(WorkspaceManager(home / "workspaces")).list(
        DEFAULT_WORKSPACE
    )
    assert result.exit_code == 0
    assert result.stdout == "reply\n"
    assert len(sessions) == 1
    assert sessions[0].messages


def test_session_chat_cli_streams_persistent_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = initialise_home(tmp_path / ".ethos")
    (home / "config.yaml").write_text(
        "events:\n  enabled: false\n  print_events: false\n"
        "provider:\n  name: ollama\n  model_name: test\n"
        "keys: {}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(app, "HOME_PATH", home)
    monkeypatch.setattr(
        AIProvider,
        "model",
        lambda _provider, _model_name: TestModel(  # pyright: ignore
            custom_output_text="reply"
        ),  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    )
    sessions = SessionManager(WorkspaceManager(home / "workspaces"))
    session = sessions.create(DEFAULT_WORKSPACE)

    result = CliRunner().invoke(
        app.main,
        ["session", "chat", "default", str(session.id), "hello"],
    )

    assert result.exit_code == 0
    assert result.output == "reply\n"
    assert sessions.get(DEFAULT_WORKSPACE, str(session.id)).messages
