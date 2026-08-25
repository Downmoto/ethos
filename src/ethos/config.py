"""ethos paths and settings."""

from functools import lru_cache
from pathlib import Path
from typing import Final, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from ethos.models import ReasoningEffort
from ethos.provider import ProviderName

HOME_PATH: Final = Path.home() / ".ethos"
CONFIG_FILE: Final = "config.yaml"
DB_PATH: Final = HOME_PATH / "data" / "ethos.db"


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ProviderName
    model_name: str
    reasoning_effort: ReasoningEffort = ReasoningEffort.NONE
    ollama_base_url: str = "http://localhost:11434"


class KeysConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    openai_api_key: SecretStr | None = None
    google_api_key: SecretStr | None = None
    ollama_api_key: SecretStr | None = None


class VoxConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(default="127.0.0.1", min_length=1)
    port: int = Field(default=8000, ge=1, le=65535)
    bearer_token: SecretStr | None = None


class SkillsCapabilityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_skill_file_bytes: int = Field(default=100 * 1024, ge=1)
    max_skills: int = Field(default=200, ge=1)
    max_resource_file_bytes: int = Field(default=100 * 1024, ge=1)
    max_resources: int = Field(default=200, ge=1)


class ReadOnlyFilesystemCapabilityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_read_file_bytes: int = Field(default=100 * 1024, ge=1)
    max_list_file_entries: int = Field(default=1_000, ge=1)


class CapabilitiesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skills: SkillsCapabilityConfig = Field(
        default_factory=SkillsCapabilityConfig
    )
    read_only_file_system: ReadOnlyFilesystemCapabilityConfig = Field(
        default_factory=ReadOnlyFilesystemCapabilityConfig
    )


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_now_after_seconds: float = Field(default=60.0, gt=0)


class EthosSettings(BaseSettings):
    gateway: VoxConfig = Field(default_factory=VoxConfig)
    provider: ProviderConfig
    keys: KeysConfig = Field(default_factory=KeysConfig)
    capabilities: CapabilitiesConfig = Field(default_factory=CapabilitiesConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)

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


@lru_cache
def get_settings() -> EthosSettings:
    return EthosSettings.model_validate({})
