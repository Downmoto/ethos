from pathlib import Path

import pytest
from pydantic import SecretStr
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openai import OpenAIResponsesModel

from cassiopeia.config import CassiopeiaSettings
from cassiopeia.provider import AIProvider, ProviderName


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
    settings = CassiopeiaSettings.model_validate(
        {
            "provider": {"name": "google"},
            "keys": {"google_api_key": "google-key"},
        }
    )

    provider = AIProvider.from_settings(settings)

    assert provider.name is ProviderName.GOOGLE
    assert provider.api_key is not None
    assert provider.api_key.get_secret_value() == "google-key"


def test_provider_requires_key_for_selected_provider() -> None:
    settings = CassiopeiaSettings.model_validate(
        {"provider": {"name": "google"}}
    )

    with pytest.raises(
        ValueError, match="CASS_KEYS__GOOGLE_API_KEY is required"
    ):
        AIProvider.from_settings(settings)


def test_provider_requires_selection() -> None:
    with pytest.raises(ValueError, match="CASS_PROVIDER__NAME is required"):
        AIProvider.from_settings(CassiopeiaSettings.defaults())


def test_settings_accept_nested_api_keys() -> None:
    settings = CassiopeiaSettings.model_validate(
        {"keys": {"openai_api_key": "secret-key"}}
    )

    assert settings.keys.openai_api_key == SecretStr("secret-key")


def test_settings_load_provider_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CASS_KEYS__GOOGLE_API_KEY", "google-key")
    monkeypatch.setenv("CASS_PROVIDER__NAME", "google")
    monkeypatch.setenv("CASS_PROVIDER__MODEL_NAME", "gemini-2.5-flash")

    settings = CassiopeiaSettings()

    assert settings.provider.name is ProviderName.GOOGLE
    assert settings.provider.model_name == "gemini-2.5-flash"
    assert settings.keys.google_api_key == SecretStr("google-key")


def test_settings_load_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "provider:\n  name: google\n  model_name: yaml-model\n"
    )
    monkeypatch.setitem(
        CassiopeiaSettings.model_config, "yaml_file", config_file
    )
    monkeypatch.setenv("CASS_PROVIDER__NAME", "ollama")
    monkeypatch.setenv("CASS_PROVIDER__MODEL_NAME", "env-model")

    settings = CassiopeiaSettings.model_validate(
        {"provider": {"name": "openai"}}
    )

    assert settings.provider.name is ProviderName.OPENAI
    assert settings.provider.model_name == "env-model"
