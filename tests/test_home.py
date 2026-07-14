from pathlib import Path

import pytest
from click.testing import CliRunner

from cassiopeia import app
from cassiopeia.home import initialise_home


def test_initialise_home_creates_config_file(tmp_path: Path) -> None:
    home = initialise_home(tmp_path / ".cassiopeia")

    assert home == tmp_path / ".cassiopeia"
    assert (home / "config.yaml").read_text() == (
        "events:\n"
        "  enabled: true\n"
        "  print_events: false\n"
        "provider:\n"
        "  name: null\n"
        "  model_name: null\n"
        "  ollama_base_url: http://localhost:11434/v1\n"
        "keys:\n"
        "  openai_api_key: null\n"
        "  google_api_key: null\n"
        "  ollama_api_key: null\n"
    )


def test_initialise_home_creates_database(tmp_path: Path) -> None:
    home = initialise_home(tmp_path / ".cassiopeia")

    db = home / "data" / "cass.db"
    assert db.exists()
    assert db.stat().st_size > 0


def test_initialise_home_rejects_existing_home(tmp_path: Path) -> None:
    home = tmp_path / ".cassiopeia"
    home.mkdir()

    with pytest.raises(FileExistsError):
        initialise_home(home)


def test_initialise_home_reinitialises_existing_home(tmp_path: Path) -> None:
    home = tmp_path / ".cassiopeia"
    home.mkdir()
    (home / "old.txt").write_text("remove me")

    initialise_home(home, reinitialise=True)

    assert not (home / "old.txt").exists()
    assert (home / "config.yaml").exists()


def test_init_command_initialises_default_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app, "HOME_PATH", tmp_path / ".cassiopeia")

    result = CliRunner().invoke(app.main, ["init"])

    assert result.exit_code == 0
    assert (tmp_path / ".cassiopeia" / "config.yaml").exists()
    assert (tmp_path / ".cassiopeia" / "data" / "cass.db").exists()


def test_init_command_reports_existing_home_without_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / ".cassiopeia"
    home.mkdir()
    monkeypatch.setattr(app, "HOME_PATH", home)

    result = CliRunner().invoke(app.main, ["init"])

    assert result.exit_code == 1
    assert "Error: cassiopeia home already exists:" in result.output
    assert "Run [cass init --reinitialise] to replace it." in result.output
    assert "Traceback" not in result.output


def test_uninit_command_removes_home_after_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / ".cassiopeia"
    home.mkdir()
    (home / "config.yaml").touch()
    monkeypatch.setattr(app, "HOME_PATH", home)

    result = CliRunner().invoke(app.main, ["uninit"], input="y\n")

    assert result.exit_code == 0
    assert not home.exists()
    assert f".cassiopeia removed from: {home}" in result.output


def test_uninit_command_preserves_home_without_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / ".cassiopeia"
    home.mkdir()
    monkeypatch.setattr(app, "HOME_PATH", home)

    result = CliRunner().invoke(app.main, ["uninit"], input="n\n")

    assert result.exit_code == 0
    assert home.exists()
    assert "Aborted!" in result.output


def test_ask_command_prints_model_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / ".cassiopeia"
    home.mkdir()
    monkeypatch.setattr(app, "HOME_PATH", home)
    monkeypatch.setattr(app, "run_prompt", lambda prompt: f"reply: {prompt}")

    result = CliRunner().invoke(app.main, ["ask", "hello"])

    assert result.exit_code == 0
    assert result.output == "reply: hello\n"


def test_ask_command_reports_runtime_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / ".cassiopeia"
    home.mkdir()
    monkeypatch.setattr(app, "HOME_PATH", home)

    def fail(_prompt: str) -> str:
        raise ValueError("CASS_KEYS__OPENAI_API_KEY is required")

    monkeypatch.setattr(app, "run_prompt", fail)

    result = CliRunner().invoke(app.main, ["ask", "hello"])

    assert result.exit_code == 1
    assert result.output == "Error: CASS_KEYS__OPENAI_API_KEY is required\n"
    assert "Traceback" not in result.output


def test_ask_command_requires_initialised_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app, "HOME_PATH", tmp_path / ".cassiopeia")

    result = CliRunner().invoke(app.main, ["ask", "hello"])

    assert result.exit_code == 1
    assert result.output == (
        "Error: cassiopeia is not initialised. Run [cass init] first.\n"
    )
