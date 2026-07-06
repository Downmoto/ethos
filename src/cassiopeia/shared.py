"""Shared primitives for cassiopeia domain models."""

from pathlib import Path
from typing import Annotated, Final

from pydantic import AwareDatetime, Field

type NonEmptyString = Annotated[str, Field(min_length=1)]
type Slug = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
type AwareTimestamp = AwareDatetime
type EntityId = NonEmptyString
type ExternalId = NonEmptyString

CONFIG_FILE: Final[str] = "config.yaml"
DB_FILE: Final[str] = "cass.db"
WORKSPACES_PATH: Final[Path] = Path("workspaces")
DEFAULT_WORKSPACE_PATH: Final[Path] = WORKSPACES_PATH / "default"
WORKFLOWS_PATH: Final[Path] = Path("workflows")
DATA_PATH: Final[Path] = Path("data")
HOME_PATH: Final[Path] = Path.home() / ".cassiopeia"
CONFIG_FILE_PATH: Final[Path] = HOME_PATH / CONFIG_FILE
DB_FILE_PATH: Final[Path] = HOME_PATH / DATA_PATH / DB_FILE
