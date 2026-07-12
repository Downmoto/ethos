"""Model providers supported by Cassiopeia."""

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import SecretStr
from pydantic_ai.models import Model
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.openai import OpenAIProvider

if TYPE_CHECKING:
    from cassiopeia.config import CassiopeiaSettings


class ProviderName(StrEnum):
    OPENAI = "openai"
    GOOGLE = "google"


@dataclass(frozen=True)
class AIProvider:
    """Create Pydantic AI models using one provider credential."""

    name: ProviderName
    api_key: SecretStr

    @classmethod
    def from_settings(cls, settings: "CassiopeiaSettings") -> "AIProvider":
        api_key = {
            ProviderName.OPENAI: settings.keys.openai_api_key,
            ProviderName.GOOGLE: settings.keys.google_api_key,
        }[settings.provider.name]

        if api_key is None:
            variable = (
                f"CASS_KEYS__{settings.provider.name.value.upper()}_API_KEY"
            )
            raise ValueError(f"{variable} is required")

        return cls(settings.provider.name, api_key)

    def model(self, model_name: str) -> Model:
        key = self.api_key.get_secret_value()

        match self.name:
            case ProviderName.OPENAI:
                return OpenAIResponsesModel(
                    model_name,
                    provider=OpenAIProvider(api_key=key),
                )
            case ProviderName.GOOGLE:
                return GoogleModel(
                    model_name,
                    provider=GoogleProvider(api_key=key),
                )
