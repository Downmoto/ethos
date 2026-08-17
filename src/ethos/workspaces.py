"""Workspace identities and their user-owned roots.

See ``docs/development/workspaces-and-runtime.md`` for layout and trust
boundaries.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

DEFAULT_WORKSPACE: Final = "default"
WORKSPACES_DIR: Final = "workspaces"

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
    """A validated user-owned workspace root."""

    name: str
    path: Path


class WorkspaceManager:
    """Create and discover workspaces beneath one injected root.

    Workspace contents are user-owned. Name and symlink validation fails
    closed so a workspace cannot redirect access outside the injected root.
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
        """Create an empty workspace root."""
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        path = self.root / name

        try:
            path.mkdir(mode=0o700)
        except FileExistsError as error:
            raise FileExistsError(
                f"workspace already exists: {name}"
            ) from error

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
