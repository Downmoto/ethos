from pathlib import Path

import pytest
from pydantic import ValidationError
from pytest import MonkeyPatch

from cassiopeia.config import CassiopeiaSettings, load_settings, load_settings_file


def test_settings_default_home(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("CASSIOPEIA_HOME", raising=False)
    monkeypatch.delenv("CASS_HOME", raising=False)

    settings = load_settings(env_file=None)

    assert settings.home == Path.home() / ".cassiopeia"


def test_settings_supports_home_override(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "cass-home"
    monkeypatch.setenv("CASSIOPEIA_HOME", str(home))
    monkeypatch.delenv("CASS_HOME", raising=False)

    settings = load_settings(env_file=None)

    assert settings.home == home


def test_settings_supports_legacy_home_override(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "legacy-cass-home"
    monkeypatch.delenv("CASSIOPEIA_HOME", raising=False)
    monkeypatch.setenv("CASS_HOME", str(home))

    settings = load_settings(env_file=None)

    assert settings.home == home


def test_settings_loads_dotenv(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    home = tmp_path / "dotenv-cass-home"
    env_file = tmp_path / ".env"
    env_file.write_text(f"CASSIOPEIA_HOME={home}\n", encoding="utf-8")
    monkeypatch.delenv("CASSIOPEIA_HOME", raising=False)
    monkeypatch.delenv("CASS_HOME", raising=False)

    settings = load_settings(env_file=env_file)

    assert settings.home == home


def test_settings_file_accepts_initial_shape(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text('{"version": 1}\n', encoding="utf-8")

    settings = load_settings_file(config_path)

    assert settings.version == 1


def test_settings_rejects_invalid_version() -> None:
    with pytest.raises(ValidationError, match="Input should be 1"):
        CassiopeiaSettings.model_validate({"version": 2})


def test_load_settings_file_rejects_extra_keys(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text('{"version": 1, "secret": "nope"}\n', encoding="utf-8")

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        load_settings_file(config_path)
