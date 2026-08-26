from pathlib import Path

import pytest
from pydantic import ValidationError

from ethos.capability_config import (
    CAPABILITIES_FILE,
    CapabilityManager,
    CapabilityName,
)
from ethos.home import initialise_home


def _manager(tmp_path: Path) -> CapabilityManager:
    home = initialise_home(tmp_path / ".ethos")
    return CapabilityManager(home / CAPABILITIES_FILE)


def test_capability_configuration_loads_registered_defaults(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)

    config = manager.load()

    assert config.global_settings.skills.enabled
    assert config.global_settings.skills.max_skills == 200
    assert config.global_settings.read_only_file_system.enabled
    assert config.workspaces == {}


def test_workspace_configuration_is_sparse_and_globally_bounded(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    manager.configure_global(
        CapabilityName.SKILLS,
        {"enabled": False, "max_skills": 100},
    )

    manager.configure_workspace(
        "project",
        CapabilityName.SKILLS,
        {"enabled": True, "max_skills": 250},
    )

    assert manager.configured("skills", "project") == {
        "enabled": True,
        "max_skills": 250,
    }
    effective = manager.effective("project").skills
    assert not effective.enabled
    assert effective.max_skills == 100


def test_workspace_configuration_can_clear_fields_and_reset(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    manager.configure_workspace(
        "project",
        "read_only_file_system",
        {"enabled": False, "max_read_file_bytes": 2048},
    )

    manager.configure_workspace(
        "project",
        "read_only_file_system",
        {"max_read_file_bytes": None},
    )

    assert manager.configured("read_only_file_system", "project") == {
        "enabled": False
    }
    manager.reset_workspace("project", "read_only_file_system")
    assert manager.configured("read_only_file_system", "project") == {}
    assert manager.load().workspaces == {}


def test_invalid_change_does_not_replace_configuration(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    original = manager.path.read_text()

    with pytest.raises(ValidationError):
        manager.configure_global("skills", {"max_skills": 0})

    assert manager.path.read_text() == original


def test_unknown_capability_and_configuration_fail_closed(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)

    with pytest.raises(ValueError, match="unknown capability: shell"):
        manager.configured("shell")

    manager.path.write_text("unknown: true\n")
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        manager.load()
