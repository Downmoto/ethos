from pathlib import Path
from stat import S_IMODE

import pytest

from ethos.workspaces import DEFAULT_WORKSPACE, WorkspaceManager


def test_create_workspace_builds_self_contained_layout(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceManager(tmp_path / "workspaces").create("my-project")

    assert workspace.name == "my-project"
    assert workspace.path == tmp_path / "workspaces" / "my-project"
    assert workspace.config_path.read_text() == "{}\n"
    assert workspace.ethos_path == workspace.path / ".ethos_workspace"
    assert workspace.tools_config_path.read_text() == (
        "tools: {}\ntoolsets: {}\n"
    )
    assert workspace.skills_config_path.read_text() == "skills: []\n"
    assert not (workspace.ethos_path / "tools").exists()
    assert not (workspace.ethos_path / "skills").exists()
    assert not (workspace.ethos_path / "memory").exists()
    assert not (workspace.path / "data").exists()
    assert not (workspace.path / "files").exists()
    assert S_IMODE(workspace.path.parent.stat().st_mode) == 0o700
    assert S_IMODE(workspace.path.stat().st_mode) == 0o700
    assert S_IMODE(workspace.ethos_path.stat().st_mode) == 0o700
    assert S_IMODE(workspace.config_path.stat().st_mode) == 0o600
    assert S_IMODE(workspace.tools_config_path.stat().st_mode) == 0o600
    assert S_IMODE(workspace.skills_config_path.stat().st_mode) == 0o600


def test_workspace_root_allows_user_defined_structure(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "workspaces")
    workspace = manager.create("my-project")
    user_directory = workspace.path / "src"
    user_directory.mkdir()

    assert manager.get("my-project") == workspace
    assert user_directory.is_dir()


def test_create_workspace_rejects_conflict_without_suffix(
    tmp_path: Path,
) -> None:
    manager = WorkspaceManager(tmp_path / "workspaces")
    manager.create("my-project")

    with pytest.raises(
        FileExistsError, match="workspace already exists: my-project"
    ):
        manager.create("my-project")

    assert not (manager.root / "my-project2").exists()


@pytest.mark.parametrize(
    "name",
    [
        "",
        "My-Project",
        "my_project",
        "-my-project",
        "my-project-",
        "my--project",
        "../project",
        "project/name",
        "default",
        "con",
    ],
)
def test_create_workspace_rejects_unsafe_or_reserved_name(
    tmp_path: Path, name: str
) -> None:
    with pytest.raises(ValueError):
        WorkspaceManager(tmp_path / "workspaces").create(name)


def test_ensure_default_is_idempotent(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "workspaces")

    first = manager.ensure_default()
    second = manager.ensure_default()

    assert first == second
    assert first.name == DEFAULT_WORKSPACE


def test_list_returns_workspaces_in_name_order(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "workspaces")
    manager.create("zeta")
    manager.ensure_default()
    manager.create("alpha")

    assert [workspace.name for workspace in manager.list()] == [
        "alpha",
        "default",
        "zeta",
    ]


def test_get_rejects_incomplete_workspace(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "workspaces")
    workspace = manager.create("incomplete")
    workspace.config_path.unlink()

    with pytest.raises(
        ValueError,
        match=r"workspace is incomplete: incomplete "
        r"\(missing: ws_config.yaml\)",
    ):
        manager.get("incomplete")
