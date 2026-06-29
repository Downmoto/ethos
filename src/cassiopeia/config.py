"""Configuration models and loaders for cassiopeia."""

from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class CassiopeiaSettings(BaseSettings):
    """Settings shared by config files, environment variables, and .env files."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="CASSIOPEIA_",
        extra="forbid",
    )

    version: Annotated[Literal[1], Field(default=1)]
    home: Annotated[
        Path,
        Field(
            default_factory=lambda: Path.home() / ".cassiopeia",
        ),
    ]

    @field_validator("home", mode="before")
    @classmethod
    def expand_path(cls, value: Any) -> Any:
        if isinstance(value, str):
            return Path(value).expanduser()
        if isinstance(value, Path):
            return value.expanduser()
        return value


def load_settings(*, env_file: str | Path | None = ".env") -> CassiopeiaSettings:
    """Load runtime settings from the process environment and an optional .env file."""

    return CassiopeiaSettings(_env_file=env_file)  # type: ignore[call-arg]


def load_settings_file(path: Path) -> CassiopeiaSettings:
    """Load and validate a user-authored settings JSON file."""

    return CassiopeiaSettings.model_validate_json(path.read_text(encoding="utf-8"))
