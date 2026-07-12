from pathlib import Path

import pytest
from pydantic import SecretStr
from pydantic_ai.models.google import GoogleModel
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


def test_provider_does_not_expose_api_key_in_repr() -> None:
    provider = AIProvider(ProviderName.OPENAI, SecretStr("secret-key"))

    assert "secret-key" not in repr(provider)


def test_provider_uses_selected_key_from_settings() -> None:
    settings = CassiopeiaSettings.model_validate(
        {
            "provider": {"name": "google"},
            "keys": {"GOOGLE_API_KEY": "google-key"},
        }
    )

    provider = AIProvider.from_settings(settings)

    assert provider.name is ProviderName.GOOGLE
    assert provider.api_key.get_secret_value() == "google-key"


def test_provider_requires_key_for_selected_provider() -> None:
    settings = CassiopeiaSettings.model_validate(
        {"provider": {"name": "google"}}
    )

    with pytest.raises(
        ValueError, match="CASS_KEYS__GOOGLE_API_KEY is required"
    ):
        AIProvider.from_settings(settings)


def test_settings_exclude_api_keys_from_serialisation() -> None:
    settings = CassiopeiaSettings.model_validate(
        {"keys": {"OPENAI_API_KEY": "secret-key"}}
    )

    assert "keys" not in settings.model_dump()
    assert "secret-key" not in settings.model_dump_json()


def test_settings_load_provider_from_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "CASS_KEYS__GOOGLE_API_KEY=google-key\n"
        "CASS_PROVIDER__NAME=google\n"
        "CASS_PROVIDER__MODEL_NAME=gemini-2.5-flash\n"
    )

    settings = CassiopeiaSettings()

    assert settings.provider.name is ProviderName.GOOGLE
    assert settings.provider.model_name == "gemini-2.5-flash"
    assert settings.keys.google_api_key == SecretStr("google-key")
