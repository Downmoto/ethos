from pydantic import SecretStr

from ethos.config import EthosSettings
from ethos.models import ReasoningEffort
from ethos.provider import AIProvider, LiteLLMModel, ProviderName


def test_provider_creates_litellm_model() -> None:
    provider = AIProvider(ProviderName.OPENAI, SecretStr("test-key"))

    model = provider.model("gpt-5-mini")

    assert isinstance(model, LiteLLMModel)
    assert model.provider is provider
    assert model.model_name == "gpt-5-mini"
    assert model.features.tools
    assert model.features.reasoning


def test_provider_configures_model_reasoning_effort() -> None:
    provider = AIProvider(ProviderName.OLLAMA, None)

    model = provider.model("qwen3", ReasoningEffort.HIGH)

    assert isinstance(model, LiteLLMModel)
    assert model.reasoning_effort is ReasoningEffort.HIGH


def test_ollama_provider_uses_server_root_by_default() -> None:
    provider = AIProvider(ProviderName.OLLAMA, None)

    model = provider.model("llama3.2")

    assert isinstance(model, LiteLLMModel)
    assert model.provider.ollama_base_url == "http://localhost:11434"


def test_ollama_provider_preserves_configured_base_url() -> None:
    provider = AIProvider(
        ProviderName.OLLAMA,
        SecretStr("test-key"),
        "http://ollama.test:1234/custom",
    )

    model = provider.model("llama3.2")

    assert isinstance(model, LiteLLMModel)
    assert model.provider.ollama_base_url == "http://ollama.test:1234/custom"


def test_provider_does_not_expose_api_key_in_repr() -> None:
    provider = AIProvider(ProviderName.OPENAI, SecretStr("secret-key"))

    assert "secret-key" not in repr(provider)
    assert "secret-key" not in repr(provider.model("model"))


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
