from importlib.resources import files
from pathlib import Path
from stat import S_IMODE

import pytest
import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel

from ethos.config import CONFIG_FILE, EthosSettings
from ethos.home import initialise_home
from ethos.workspaces import DEFAULT_WORKSPACE, WORKSPACES_DIR, WorkspaceManager


def _model_field_paths(model: type[BaseModel], prefix: str = "") -> set[str]:
    paths: set[str] = set()
    for name, field in model.model_fields.items():
        path = f"{prefix}.{name}" if prefix else name
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            paths.update(_model_field_paths(annotation, path))
        else:
            paths.add(path)
    return paths


def _config_field_paths(
    config: dict[str, object], prefix: str = ""
) -> set[str]:
    paths: set[str] = set()
    for name, value in config.items():
        path = f"{prefix}.{name}" if prefix else name
        if isinstance(value, dict):
            paths.update(_config_field_paths(value, path))  # pyright: ignore[reportUnknownArgumentType]
        else:
            paths.add(path)
    return paths


def test_initialise_home_creates_config_file(tmp_path: Path) -> None:
    home = initialise_home(tmp_path / ".ethos")
    template = files("ethos") / "templates" / CONFIG_FILE

    assert home == tmp_path / ".ethos"
    assert (home / CONFIG_FILE).read_text() == template.read_text()


def test_initialise_home_restricts_config_access(tmp_path: Path) -> None:
    home = initialise_home(tmp_path / ".ethos")

    assert S_IMODE(home.stat().st_mode) == 0o700
    assert S_IMODE((home / CONFIG_FILE).stat().st_mode) == 0o600


def test_config_template_matches_settings_fields() -> None:
    template = files("ethos") / "templates" / CONFIG_FILE
    config = yaml.safe_load(template.read_text())

    assert isinstance(config, dict)
    assert _config_field_paths(config) == _model_field_paths(EthosSettings)  # pyright: ignore[reportUnknownArgumentType]


def test_initialise_home_creates_database(tmp_path: Path) -> None:
    home = initialise_home(tmp_path / ".ethos")

    db = home / "data" / "ethos.db"
    assert db.exists()
    assert db.stat().st_size > 0


def test_initialise_home_creates_default_workspace(tmp_path: Path) -> None:
    home = initialise_home(tmp_path / ".ethos")

    workspace = WorkspaceManager(home / WORKSPACES_DIR).get(DEFAULT_WORKSPACE)

    assert workspace.name == DEFAULT_WORKSPACE


def test_initialise_home_rejects_existing_home(tmp_path: Path) -> None:
    home = tmp_path / ".ethos"
    home.mkdir()

    with pytest.raises(FileExistsError):
        initialise_home(home)


def test_initialise_home_reinitialises_existing_home(tmp_path: Path) -> None:
    home = tmp_path / ".ethos"
    home.mkdir()
    (home / "old.txt").write_text("remove me")

    initialise_home(home, reinitialise=True)

    assert not (home / "old.txt").exists()
    assert (home / "config.yaml").exists()
