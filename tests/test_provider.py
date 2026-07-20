from pydantic import SecretStr
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openai import OpenAIResponsesModel

from ethos.config import EthosSettings
from ethos.provider import AIProvider, ProviderName


def test_openai_provider_creates_responses_model() -> None:
    provider = AIProvider(ProviderName.OPENAI, SecretStr("test-key"))

    model = provider.model("gpt-5-mini")

    assert isinstance(model, OpenAIResponsesModel)
    assert model.model_name == "gpt-5-mini"


def test_google_provider_creates_google_model() -> None:
    provider = AIProvider(ProviderName.GOOGLE, SecretStr("test-key"))

    model = provider.model("gemini-2.5-flash")

    assert isinstance(model, GoogleModel)
    assert model.model_name == "gemini-2.5-flash"


def test_ollama_provider_creates_ollama_model_without_api_key() -> None:
    provider = AIProvider(ProviderName.OLLAMA, None)

    model = provider.model("llama3.2")

    assert isinstance(model, OllamaModel)
    assert model.model_name == "llama3.2"


def test_provider_does_not_expose_api_key_in_repr() -> None:
    provider = AIProvider(ProviderName.OPENAI, SecretStr("secret-key"))

    assert "secret-key" not in repr(provider)


def test_provider_uses_selected_key_from_settings() -> None:
    settings = EthosSettings.model_validate(
        {
            "provider": {"name": "google", "model_name": "gemini"},
            "keys": {"google_api_key": "google-key"},
        }
    )

    provider = AIProvider.from_settings(settings)

    assert provider.name is ProviderName.GOOGLE
    assert provider.api_key is not None
    assert provider.api_key.get_secret_value() == "google-key"
