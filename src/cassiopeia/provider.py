"""Model providers supported by Cassiopeia."""

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import SecretStr
from pydantic_ai.models import Model
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openai import OpenAIProvider

if TYPE_CHECKING:
    from cassiopeia.config import CassiopeiaSettings


class ProviderName(StrEnum):
    OPENAI = "openai"
    GOOGLE = "google"
    OLLAMA = "ollama"


@dataclass(frozen=True)
class AIProvider:
    """Create Pydantic AI models using one provider credential."""

    name: ProviderName
    api_key: SecretStr | None
    ollama_base_url: str = "http://localhost:11434/v1"

    @classmethod
    def from_settings(cls, settings: "CassiopeiaSettings") -> "AIProvider":
        name = settings.provider.name
        if name is None:
            raise ValueError("CASS_PROVIDER__NAME is required")

        api_key = {
            ProviderName.OPENAI: settings.keys.openai_api_key,
            ProviderName.GOOGLE: settings.keys.google_api_key,
            ProviderName.OLLAMA: settings.keys.ollama_api_key,
        }[name]
        if api_key is None and name is not ProviderName.OLLAMA:
            variable = f"CASS_KEYS__{name.value.upper()}_API_KEY"
            raise ValueError(f"{variable} is required")
        return cls(
            name,
            api_key,
            settings.provider.ollama_base_url,
        )

    def model(self, model_name: str) -> Model:
        match self.name:
            case ProviderName.OPENAI:
                assert self.api_key is not None
                return OpenAIResponsesModel(
                    model_name,
                    provider=OpenAIProvider(
                        api_key=self.api_key.get_secret_value()
                    ),
                )
            case ProviderName.GOOGLE:
                assert self.api_key is not None
                return GoogleModel(
                    model_name,
                    provider=GoogleProvider(
                        api_key=self.api_key.get_secret_value()
                    ),
                )
            case ProviderName.OLLAMA:
                return OllamaModel(
                    model_name,
                    provider=OllamaProvider(
                        base_url=self.ollama_base_url,
                        api_key=(
                            self.api_key.get_secret_value()
                            if self.api_key
                            else None
                        ),
                    ),
                )
