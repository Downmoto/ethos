from pathlib import Path

import pytest
from pydantic import SecretStr

from cassiopeia.config import CassiopeiaSettings
from cassiopeia.provider import ProviderName


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
