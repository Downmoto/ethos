from pathlib import Path
from typing import Annotated, Any

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class CassiopeiaSettings(BaseSettings):
    """Runtime settings loaded from environment variables and optional .env files."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="CASSIOPEIA_",
        extra="ignore",
    )

    home: Annotated[
        Path,
        Field(
            default_factory=lambda: Path.home() / ".cassiopeia",
            validation_alias=AliasChoices("CASSIOPEIA_HOME", "CASS_HOME"),
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
    """Load cassiopeia settings from the process environment and an optional .env file."""

    return CassiopeiaSettings(_env_file=env_file)  # type: ignore[call-arg]
