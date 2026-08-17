from pathlib import Path
from stat import S_IMODE

import pytest

from ethos.workspaces import DEFAULT_WORKSPACE, WorkspaceManager


def test_create_workspace_builds_empty_user_owned_directory(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceManager(tmp_path / "workspaces").create("my-project")

    assert workspace.name == "my-project"
    assert workspace.path == tmp_path / "workspaces" / "my-project"
    assert list(workspace.path.iterdir()) == []
    assert S_IMODE(workspace.path.parent.stat().st_mode) == 0o700
    assert S_IMODE(workspace.path.stat().st_mode) == 0o700


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


def test_get_accepts_any_user_defined_contents(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "workspaces")
    workspace = manager.create("my-project")
    (workspace.path / ".ethos_workspace").mkdir()
    (workspace.path / "README.md").write_text("user content\n")

    assert manager.get("my-project") == workspace
