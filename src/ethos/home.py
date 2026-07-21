"""Bootstrap the ethos home directory layout."""

import shutil
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path
from typing import Final

from ethos.config import CONFIG_FILE
from ethos.storage import Storage
from ethos.workspaces import WORKSPACES_DIR, WorkspaceManager

DB_PATH: Final = Path("data/ethos.db")
WORKFLOWS_PATH: Final = Path("workflows")


def _read_config_template() -> str:
    return (files("ethos") / "templates" / CONFIG_FILE).read_text()


_FILES: Final[tuple[tuple[Path, Callable[[], str]], ...]] = (
    (Path(CONFIG_FILE), _read_config_template),
)

_EMPTY_DIRS: Final[tuple[Path, ...]] = (WORKFLOWS_PATH,)


def initialise_home(home: Path, reinitialise: bool = False) -> Path:
    """Create a new ethos home directory and starter definition files.

    This is a bootstrap operation, not a repair or migration operation. Existing
    homes are rejected so user-authored files are never silently interpreted or
    rewritten by `ethos init`.
    """
    resolved_home = home.expanduser()

    if resolved_home.exists():
        if not reinitialise:
            raise FileExistsError(f"ethos home already exists: {resolved_home}")
        shutil.rmtree(resolved_home)

    resolved_home.mkdir(parents=True, mode=0o700)
    resolved_home.chmod(0o700)

    for directory in _EMPTY_DIRS:
        (resolved_home / directory).mkdir(parents=True)

    for file, contents in _FILES:
        target = resolved_home / file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents())
        target.chmod(0o600)

    Storage(resolved_home / DB_PATH).close()
    WorkspaceManager(resolved_home / WORKSPACES_DIR).ensure_default()

    return resolved_home
