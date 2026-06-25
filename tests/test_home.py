import json
from pathlib import Path

from cassiopeia.home import DIRECTORIES, JSON_FILES, initialise_home


def test_initialise_home_creates_expected_layout(tmp_path: Path) -> None:
    home = tmp_path / "cassiopeia"

    result = initialise_home(home)

    assert result == home
    for directory in DIRECTORIES:
        assert (home / directory).is_dir()
    for filename, default_content in JSON_FILES.items():
        path = home / filename
        assert path.is_file()
        assert json.loads(path.read_text(encoding="utf-8")) == default_content


def test_initialise_home_rejects_existing_home(tmp_path: Path) -> None:
    home = tmp_path / "cassiopeia"
    config_path = home / "config.json"
    user_config = {"version": 1, "user_value": "keep"}

    initialise_home(home)
    config_path.write_text(json.dumps(user_config), encoding="utf-8")

    try:
        initialise_home(home)
    except FileExistsError as error:
        assert str(home) in str(error)
    else:
        raise AssertionError("initialise_home should reject an existing home")

    assert json.loads(config_path.read_text(encoding="utf-8")) == user_config
