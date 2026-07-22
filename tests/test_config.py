from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from ethos.config import EthosSettings, load_events_config
from ethos.provider import ProviderName


def test_settings_accept_nested_api_keys() -> None:
    settings = EthosSettings.model_validate(
        {
            "provider": {"name": "openai", "model_name": "gpt-5-mini"},
            "keys": {"openai_api_key": "secret-key"},
        }
    )

    assert settings.keys.openai_api_key == SecretStr("secret-key")


def test_settings_load_provider_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ETHOS_KEYS__GOOGLE_API_KEY", "google-key")
    monkeypatch.setenv("ETHOS_PROVIDER__NAME", "google")
    monkeypatch.setenv("ETHOS_PROVIDER__MODEL_NAME", "gemini-2.5-flash")

    settings = EthosSettings.model_validate({})

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
    monkeypatch.setitem(EthosSettings.model_config, "yaml_file", config_file)
    monkeypatch.setenv("ETHOS_PROVIDER__NAME", "ollama")
    monkeypatch.setenv("ETHOS_PROVIDER__MODEL_NAME", "env-model")

    settings = EthosSettings.model_validate(
        {
            "provider": {"name": "openai"},
            "keys": {"openai_api_key": "openai-key"},
        }
    )

    assert settings.provider.name is ProviderName.OPENAI
    assert settings.provider.model_name == "env-model"


def test_settings_require_provider_and_model() -> None:
    with pytest.raises(ValidationError) as error:
        EthosSettings.model_validate(
            {"provider": {"name": None, "model_name": None}}
        )

    locations = {item["loc"] for item in error.value.errors()}
    assert locations == {("provider", "name"), ("provider", "model_name")}


def test_settings_require_selected_provider_key() -> None:
    with pytest.raises(
        ValidationError, match="ETHOS_KEYS__GOOGLE_API_KEY is required"
    ):
        EthosSettings.model_validate(
            {
                "provider": {"name": "google", "model_name": "gemini"},
                "keys": {"google_api_key": None},
            }
        )


def test_settings_allow_ollama_without_api_key() -> None:
    settings = EthosSettings.model_validate(
        {"provider": {"name": "ollama", "model_name": "llama3.2"}}
    )

    assert settings.keys.ollama_api_key is None


def test_settings_validate_discord_user_ids() -> None:
    settings = EthosSettings.model_validate(
        {
            "provider": {"name": "ollama", "model_name": "llama3.2"},
            "gateways": {"discord": {"allowed_user_ids": [123, 456]}},
        }
    )

    assert settings.gateways.discord.allowed_user_ids == frozenset({123, 456})

    with pytest.raises(ValidationError):
        EthosSettings.model_validate(
            {
                "provider": {"name": "ollama", "model_name": "llama3.2"},
                "gateways": {"discord": {"allowed_user_ids": [0]}},
            }
        )


@pytest.mark.parametrize(
    "settings",
    [
        {
            "provider": {"name": "ollama", "model_name": "llama3.2"},
            "unknown": True,
        },
        {
            "provider": {
                "name": "ollama",
                "model_name": "llama3.2",
                "unknown": True,
            }
        },
    ],
)
def test_settings_reject_unknown_fields(settings: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EthosSettings.model_validate(settings)


def test_load_events_config_does_not_require_provider(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text(
        "events:\n  enabled: false\n  print_events: true\n"
        "provider:\n  name: null\n  model_name: null\n"
    )

    config = load_events_config(tmp_path)

    assert not config.enabled
    assert config.print_events
