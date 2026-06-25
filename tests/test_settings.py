from pathlib import Path

from pytest import MonkeyPatch

from cassiopeia.config import load_settings


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
