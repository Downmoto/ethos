import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]
from click.testing import CliRunner
from pydantic_ai.usage import RunUsage

from ethos import app
from ethos.config import EthosSettings, ProviderConfig
from ethos.home import initialise_home
from ethos.runtime import PromptStreamEvent


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


def test_ask_command_prints_model_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / ".ethos"
    home.mkdir()
    monkeypatch.setattr(app, "HOME_PATH", home)

    async def stream_prompt(prompt: str) -> AsyncIterator[PromptStreamEvent]:
        yield PromptStreamEvent(text="reply: ")
        yield PromptStreamEvent(text=prompt)
        yield PromptStreamEvent(done=True)

    monkeypatch.setattr(app, "run_prompt_singleton", stream_prompt)

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
    ) -> AsyncIterator[PromptStreamEvent]:
        await asyncio.sleep(0.15)
        yield PromptStreamEvent(text="reply")

    monkeypatch.setattr(app, "run_prompt_singleton", stream_prompt)

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

    async def stream_prompt(_prompt: str) -> AsyncIterator[PromptStreamEvent]:
        yield PromptStreamEvent(text="streamed ")
        yield PromptStreamEvent(text="response")
        yield PromptStreamEvent(
            usage=RunUsage(input_tokens=10, output_tokens=2),
            done=True,
        )

    monkeypatch.setattr(app, "run_prompt_singleton", stream_prompt)

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

    async def fail(_prompt: str) -> AsyncIterator[PromptStreamEvent]:
        yield PromptStreamEvent(text="partial response")
        raise ValueError("model context window exceeded")

    monkeypatch.setattr(app, "run_prompt_singleton", fail)

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

    async def fail(_prompt: str) -> AsyncIterator[PromptStreamEvent]:
        raise ValueError("ETHOS_KEYS__OPENAI_API_KEY is required")
        yield  # required for return type, runtime error without

    monkeypatch.setattr(app, "run_prompt_singleton", fail)

    result = CliRunner().invoke(app.main, ["ask", "hello"])

    assert result.exit_code == 1
    assert "Error: ETHOS_KEYS__OPENAI_API_KEY is required" in result.stderr
    assert "Traceback" not in result.output


def test_ask_command_requires_onboarding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / ".ethos"
    home.mkdir()
    monkeypatch.setattr(app, "HOME_PATH", home)

    async def fail(_prompt: str) -> AsyncIterator[PromptStreamEvent]:
        EthosSettings.model_validate(
            {"provider": {"name": None, "model_name": None}}
        )
        yield PromptStreamEvent()

    monkeypatch.setattr(app, "run_prompt_singleton", fail)

    result = CliRunner().invoke(app.main, ["ask", "hello"])

    assert result.exit_code == 1
    assert (
        "Error: ethos is not configured. Run [ethos onboard] first."
        in result.output
    )
    assert "Traceback" not in result.output


def test_ask_command_preserves_other_validation_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / ".ethos"
    home.mkdir()
    monkeypatch.setattr(app, "HOME_PATH", home)

    async def fail(_prompt: str) -> AsyncIterator[PromptStreamEvent]:
        ProviderConfig.model_validate({})
        yield PromptStreamEvent()

    monkeypatch.setattr(app, "run_prompt_singleton", fail)

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
