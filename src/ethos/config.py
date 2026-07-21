"""ethos paths and settings."""

from functools import lru_cache
from pathlib import Path
from typing import Final, Self

import yaml  # type: ignore[import-untyped]
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    TypeAdapter,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from ethos.provider import ProviderName

HOME_PATH: Final = Path.home() / ".ethos"
CONFIG_FILE: Final = "config.yaml"
DB_PATH: Final = HOME_PATH / "data" / "ethos.db"


class EventsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    print_events: bool = False


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ProviderName
    model_name: str
    ollama_base_url: str = "http://localhost:11434/v1"


class KeysConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    openai_api_key: SecretStr | None = None
    google_api_key: SecretStr | None = None
    ollama_api_key: SecretStr | None = None


class EthosSettings(BaseSettings):
    events: EventsConfig = Field(default_factory=EventsConfig)
    provider: ProviderConfig
    keys: KeysConfig = Field(default_factory=KeysConfig)

    model_config = SettingsConfigDict(
        env_prefix="ETHOS_",
        env_nested_delimiter="__",
        yaml_file=HOME_PATH / CONFIG_FILE,
        yaml_file_encoding="utf-8",
        extra="forbid",
    )

    @model_validator(mode="after")
    def require_provider_key(self) -> Self:
        api_key = {
            ProviderName.OPENAI: self.keys.openai_api_key,
            ProviderName.GOOGLE: self.keys.google_api_key,
            ProviderName.OLLAMA: self.keys.ollama_api_key,
        }[self.provider.name]
        if api_key is None and self.provider.name is not ProviderName.OLLAMA:
            variable = f"ETHOS_KEYS__{self.provider.name.value.upper()}_API_KEY"
            raise ValueError(f"{variable} is required")
        return self

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
            YamlConfigSettingsSource(settings_cls),
        )


def load_events_config(home: Path) -> EventsConfig:
    """Load event settings without requiring model onboarding."""
    config = TypeAdapter(dict[str, object]).validate_python(
        yaml.safe_load((home / CONFIG_FILE).read_text(encoding="utf-8"))
    )
    return EventsConfig.model_validate(config.get("events", {}))


@lru_cache
def get_settings() -> EthosSettings:
    return EthosSettings.model_validate({})
