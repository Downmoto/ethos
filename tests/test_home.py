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
        "  name: openai\n"
        "  model_name: gpt-4.1-mini\n"
        "keys:\n"
        "  openai_api_key: null\n"
        "  google_api_key: null\n"
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
