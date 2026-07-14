from pathlib import Path

import pytest

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
