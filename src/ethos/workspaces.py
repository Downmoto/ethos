"""Workspace identities and the Ethos-owned metadata inside their roots.

See ``docs/development/workspaces-and-runtime.md`` for layout and trust
boundaries.
"""

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Final

DEFAULT_WORKSPACE: Final = "default"
WORKSPACES_DIR: Final = "workspaces"
WORKSPACE_META_DIR: Final = ".ethos_workspace"
WORKSPACE_CONFIG_FILE: Final = "ws_config.yaml"
TOOLS_CONFIG_FILE: Final = "tools.yaml"
SKILLS_CONFIG_FILE: Final = "skills.yaml"
SESSIONS_DIR: Final = "sessions"

_WORKSPACE_FILES: Final = {
    WORKSPACE_CONFIG_FILE: "{}\n",
    TOOLS_CONFIG_FILE: "tools: {}\ntoolsets: {}\n",
    SKILLS_CONFIG_FILE: "skills: []\n",
}

_WORKSPACE_NAME_PATTERN: Final = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MAX_WORKSPACE_NAME_LENGTH: Final = 63
_RESERVED_NAMES: Final = frozenset(
    {
        DEFAULT_WORKSPACE,
        "aux",
        "con",
        "nul",
        "prn",
        *(f"com{number}" for number in range(1, 10)),
        *(f"lpt{number}" for number in range(1, 10)),
    }
)


@dataclass(frozen=True)
class Workspace:
    """A validated root whose ``.ethos_workspace`` metadata Ethos owns."""

    name: str
    path: Path

    @property
    def ethos_path(self) -> Path:
        return self.path / WORKSPACE_META_DIR

    @property
    def config_path(self) -> Path:
        return self.ethos_path / WORKSPACE_CONFIG_FILE

    @property
    def tools_config_path(self) -> Path:
        return self.ethos_path / TOOLS_CONFIG_FILE

    @property
    def skills_config_path(self) -> Path:
        return self.ethos_path / SKILLS_CONFIG_FILE

    @property
    def sessions_path(self) -> Path:
        return self.ethos_path / SESSIONS_DIR


class WorkspaceManager:
    """Create and discover workspaces beneath one injected root.

    User content outside ``.ethos_workspace`` is not managed by Ethos.
    Structural and symlink validation fails closed so a workspace name cannot
    redirect configuration or session access outside the injected root.
    """

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser()

    def create(self, name: str) -> Workspace:
        """Create a named workspace, rejecting conflicts."""
        self._validate_name(name)
        return self._create(name)

    def ensure_default(self) -> Workspace:
        """Create the reserved default workspace when it is absent."""
        try:
            return self.get(DEFAULT_WORKSPACE)
        except FileNotFoundError:
            try:
                return self._create(DEFAULT_WORKSPACE)
            except FileExistsError:
                return self.get(DEFAULT_WORKSPACE)

    def get(self, name: str) -> Workspace:
        """Load a complete workspace without repairing or following it."""
        self._validate_name(name, allow_default=True)
        workspace = Workspace(name=name, path=self.root / name)

        if not workspace.path.is_dir():
            raise FileNotFoundError(f"workspace does not exist: {name}")
        if workspace.path.is_symlink():
            raise ValueError(f"workspace must not be a symlink: {name}")

        required_paths = (
            workspace.ethos_path,
            *(workspace.ethos_path / name for name in _WORKSPACE_FILES),
            workspace.sessions_path,
        )
        symlinks = [path.name for path in required_paths if path.is_symlink()]
        if symlinks:
            raise ValueError(
                f"workspace contains symlinks: {name} ({', '.join(symlinks)})"
            )
        missing = [
            path.name
            for path in required_paths
            if not (
                path.is_dir()
                if path in (workspace.ethos_path, workspace.sessions_path)
                else path.is_file()
            )
        ]
        if missing:
            raise ValueError(
                f"workspace is incomplete: {name} "
                f"(missing: {', '.join(missing)})"
            )

        return workspace

    def list(self) -> tuple[Workspace, ...]:
        """Return validly named workspaces in name order."""
        if not self.root.is_dir():
            return ()

        workspaces: list[Workspace] = []
        for path in self.root.iterdir():
            if not path.is_dir():
                continue
            try:
                self._validate_name(path.name, allow_default=True)
            except ValueError:
                continue
            workspaces.append(self.get(path.name))

        return tuple(sorted(workspaces, key=lambda workspace: workspace.name))

    def _create(self, name: str) -> Workspace:
        """Create all owned metadata or remove the incomplete workspace."""
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        path = self.root / name

        try:
            path.mkdir(mode=0o700)
        except FileExistsError as error:
            raise FileExistsError(
                f"workspace already exists: {name}"
            ) from error

        try:
            ethos_path = path / WORKSPACE_META_DIR
            ethos_path.mkdir(mode=0o700)
            (ethos_path / SESSIONS_DIR).mkdir(mode=0o700)
            for filename, contents in _WORKSPACE_FILES.items():
                file = ethos_path / filename
                file.write_text(contents, encoding="utf-8")
                file.chmod(0o600)
        except Exception:
            shutil.rmtree(path)
            raise

        return self.get(name)

    @staticmethod
    def _validate_name(name: str, *, allow_default: bool = False) -> None:
        if (
            len(name) > _MAX_WORKSPACE_NAME_LENGTH
            or _WORKSPACE_NAME_PATTERN.fullmatch(name) is None
        ):
            raise ValueError(f"invalid workspace name: {name!r}")
        if name in _RESERVED_NAMES and not (
            allow_default and name == DEFAULT_WORKSPACE
        ):
            raise ValueError(f"reserved workspace name: {name}")
