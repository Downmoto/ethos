import pathlib
from functools import lru_cache

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from cassiopeia.configs.events_config import EventsConfig


class CassiopeiaSettings(BaseSettings):
    """enitre cassiopeia settings class"""

    events: EventsConfig = Field(default_factory=EventsConfig)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="cassiopeia",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        yaml_file=(pathlib.Path.home() / ".cassiopeia/config.yaml"),
        yaml_file_encoding="utf-8",
        extra="ignore",
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
            env_settings,
            YamlConfigSettingsSource(settings_cls),
            init_settings,
        )


@lru_cache
def get_settings() -> CassiopeiaSettings:
    return CassiopeiaSettings()
