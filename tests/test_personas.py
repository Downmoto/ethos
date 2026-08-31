from pathlib import Path
from stat import S_IMODE

import pytest
from pydantic import ValidationError

from ethos.home import initialise_home
from ethos.personas import (
    ETHOS_PERSONA,
    ETHOS_PERSONA_ID,
    PERSONAS_FILE,
    PersonaManager,
)


def _manager(tmp_path: Path) -> PersonaManager:
    home = initialise_home(tmp_path / ".ethos")
    return PersonaManager(home / PERSONAS_FILE)


def test_personas_load_builtin_configuration(tmp_path: Path) -> None:
    manager = _manager(tmp_path)

    config = manager.load()

    assert config.global_default == ETHOS_PERSONA_ID
    assert config.personas == {ETHOS_PERSONA_ID: ETHOS_PERSONA}
    assert manager.resolve("default").effective == ETHOS_PERSONA


def test_missing_file_preserves_legacy_ethos_behaviour(tmp_path: Path) -> None:
    path = tmp_path / PERSONAS_FILE
    manager = PersonaManager(path)

    resolution = manager.resolve("legacy")

    assert not path.exists()
    assert resolution.assigned_id == ETHOS_PERSONA_ID
    assert resolution.effective_id == ETHOS_PERSONA_ID
    assert not resolution.fallback


def test_personas_survive_restart_and_file_is_private(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.create(
        "reviewer",
        {
            "name": "Reviewer",
            "instructions": "Review changes carefully.",
            "capabilities": ["file_system"],
        },
    )
    manager.assign("project", "reviewer")

    restarted = PersonaManager(manager.path)

    assert restarted.get("reviewer").name == "Reviewer"
    assert restarted.resolve("project").assigned_id == "reviewer"
    assert S_IMODE(manager.path.stat().st_mode) == 0o600


def test_persona_changes_apply_to_assigned_workspace(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.create(
        "reviewer",
        {"name": "Reviewer", "instructions": "Review changes."},
    )
    manager.assign("project", "reviewer")

    manager.update("reviewer", {"instructions": "Review tests too."})

    assert (
        manager.resolve("project").effective.instructions == "Review tests too."
    )


def test_disabled_and_removed_personas_fall_back_without_broadening(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    manager.create(
        "writer",
        {
            "name": "Writer",
            "instructions": "Write files.",
            "capabilities": ["file_system"],
        },
    )
    manager.assign("project", "writer")

    manager.update("writer", {"enabled": False})
    disabled = manager.resolve("project")
    manager.remove("writer")
    removed = manager.resolve("project")

    for resolution in (disabled, removed):
        assert resolution.assigned_id == "writer"
        assert resolution.effective_id == ETHOS_PERSONA_ID
        assert resolution.fallback
        assert tuple(
            item.value for item in resolution.capability_ceiling or ()
        ) == ("file_system",)

    with pytest.raises(FileExistsError, match="persona already exists: writer"):
        manager.create(
            "writer",
            {"name": "Other Writer", "instructions": "Write again."},
        )


def test_unknown_assigned_persona_falls_back_with_no_capabilities(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    manager.path.write_text(
        manager.path.read_text().replace(
            "workspaces:\n  default: ethos",
            "workspaces:\n  default: missing",
        )
    )

    resolution = manager.resolve("default")

    assert resolution.assigned_id == "missing"
    assert resolution.effective_id == ETHOS_PERSONA_ID
    assert resolution.capability_ceiling == ()


def test_global_default_is_copied_to_new_workspace_assignment(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    manager.create(
        "reviewer",
        {"name": "Reviewer", "instructions": "Review changes."},
    )
    manager.set_default("reviewer")

    manager.assign_default("new-project")
    manager.set_default(ETHOS_PERSONA_ID)

    assert manager.resolve("new-project").assigned_id == "reviewer"


def test_invalid_change_does_not_replace_persona_configuration(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    manager.create(
        "reviewer",
        {"name": "Reviewer", "instructions": "Review changes."},
    )
    original = manager.path.read_bytes()

    with pytest.raises(ValidationError):
        manager.update("reviewer", {"instructions": " "})

    assert manager.path.read_bytes() == original


def test_persona_identity_and_builtin_are_protected(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.create(
        "reviewer",
        {"name": "Reviewer", "instructions": "Review changes."},
    )

    with pytest.raises(FileExistsError):
        manager.create(
            "reviewer",
            {"name": "Second", "instructions": "Duplicate ID."},
        )
    with pytest.raises(ValidationError, match="persona name must be unique"):
        manager.create(
            "other",
            {"name": "reviewer", "instructions": "Duplicate name."},
        )
    with pytest.raises(ValueError, match="built-in ethos"):
        manager.update(ETHOS_PERSONA_ID, {"name": "Changed"})
    with pytest.raises(ValueError, match="built-in ethos"):
        manager.remove(ETHOS_PERSONA_ID)
    with pytest.raises(ValueError, match="reserved persona identifier"):
        manager.create(
            "default",
            {"name": "Default", "instructions": "Reserved ID."},
        )


def test_default_cannot_be_disabled_or_removed(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.create(
        "reviewer",
        {"name": "Reviewer", "instructions": "Review changes."},
    )
    manager.set_default("reviewer")
    original = manager.path.read_bytes()

    with pytest.raises(ValidationError, match="default persona is not enabled"):
        manager.update("reviewer", {"enabled": False})
    with pytest.raises(ValidationError, match="default persona is not enabled"):
        manager.remove("reviewer")

    assert manager.path.read_bytes() == original
