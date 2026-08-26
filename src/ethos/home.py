"""Bootstrap the ethos home directory layout."""

import shutil
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path
from typing import Final

from ethos.capability_config import CAPABILITIES_FILE
from ethos.config import CONFIG_FILE
from ethos.sessions import SESSIONS_DIR
from ethos.storage import Storage
from ethos.workspaces import WORKSPACES_DIR, WorkspaceManager

TOOLS_CONFIG_FILE: Final = "tools.yaml"

DB_PATH: Final = Path("data/ethos.db")
WORKFLOWS_PATH: Final = Path("workflows")
SKILLS_PATH: Final = Path("skills")


def _read_config_template() -> str:
    return (files("ethos") / "templates" / CONFIG_FILE).read_text()


def _read_tools_template() -> str:
    return "tools: {}\ntoolsets: {}\n"


def _read_capabilities_template() -> str:
    return (files("ethos") / "templates" / CAPABILITIES_FILE).read_text()


_FILES: Final[tuple[tuple[Path, Callable[[], str]], ...]] = (
    (Path(CONFIG_FILE), _read_config_template),
    (Path(CAPABILITIES_FILE), _read_capabilities_template),
    (Path(TOOLS_CONFIG_FILE), _read_tools_template),
)

_EMPTY_DIRS: Final[tuple[Path, ...]] = (
    WORKFLOWS_PATH,
    SKILLS_PATH,
    Path(SESSIONS_DIR),
)


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
        (resolved_home / directory).mkdir(parents=True, mode=0o700)

    for file, contents in _FILES:
        target = resolved_home / file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents())
        target.chmod(0o600)

    Storage(resolved_home / DB_PATH).close()
    WorkspaceManager(resolved_home / WORKSPACES_DIR).ensure_default()

    return resolved_home
