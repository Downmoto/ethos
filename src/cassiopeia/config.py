"""Cassiopeia paths and settings."""

from functools import lru_cache
from pathlib import Path
from typing import Final

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from cassiopeia.provider import ProviderName

HOME_PATH: Final = Path.home() / ".cassiopeia"
CONFIG_FILE: Final = "config.yaml"
DB_PATH: Final = HOME_PATH / "data" / "cass.db"


class EventsConfig(BaseModel):
    enabled: bool = True
    print_events: bool = False


class ProviderConfig(BaseModel):
    name: ProviderName = ProviderName.OPENAI
    model_name: str = "gpt-4.1-mini"


class KeysConfig(BaseModel):
    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="OPENAI_API_KEY",
        exclude=True,
    )
    google_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="GOOGLE_API_KEY",
        exclude=True,
    )


class CassiopeiaSettings(BaseSettings):
    events: EventsConfig = Field(default_factory=EventsConfig)
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    keys: KeysConfig = Field(default_factory=KeysConfig, exclude=True)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="CASS_",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        yaml_file=HOME_PATH / CONFIG_FILE,
        yaml_file_encoding="utf-8",
        extra="ignore",
    )

    @classmethod
    def defaults(cls) -> "CassiopeiaSettings":
        return cls.model_construct(
            **{
                name: field.get_default(call_default_factory=True)
                for name, field in cls.model_fields.items()
            }
        )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls),
        )


@lru_cache
def get_settings() -> CassiopeiaSettings:
    return CassiopeiaSettings()
