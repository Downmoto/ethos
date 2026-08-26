import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from ethos.home import initialise_home
from ethos.models import ModelRequest, ModelResponse, TextPart
from ethos.provider import AIProvider, ProviderName
from ethos.provider_config import ProviderManager


def test_provider_manager_configures_and_updates_selected_provider(
    tmp_path: Path,
) -> None:
    home = initialise_home(tmp_path / ".ethos")
    manager = ProviderManager(home / "config.yaml")

    configured = manager.configure(
        {
            "name": "openai",
            "model_name": "gpt-5-mini",
            "api_key": "secret-key",
        }
    )
    updated = manager.configure(
        {"model_name": "gpt-5", "reasoning_effort": "high"}
    )

    assert configured.provider.name is ProviderName.OPENAI
    assert updated.provider.model_name == "gpt-5"
    assert updated.provider.reasoning_effort.value == "high"
    assert updated.keys.openai_api_key is not None
    assert updated.keys.openai_api_key.get_secret_value() == "secret-key"


def test_invalid_provider_change_does_not_replace_working_config(
    tmp_path: Path,
) -> None:
    home = initialise_home(tmp_path / ".ethos")
    manager = ProviderManager(home / "config.yaml")
    manager.configure(
        {
            "name": "ollama",
            "model_name": "llama3.2",
        }
    )
    before = (home / "config.yaml").read_bytes()

    with pytest.raises(ValidationError):
        manager.configure({"ollama_base_url": "ftp://localhost"})

    assert (home / "config.yaml").read_bytes() == before


def test_provider_check_uses_candidate_without_saving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = initialise_home(tmp_path / ".ethos")
    manager = ProviderManager(home / "config.yaml")
    manager.configure({"name": "ollama", "model_name": "old-model"})
    before = (home / "config.yaml").read_bytes()
    requests: list[ModelRequest] = []

    class CheckModel:
        async def request(self, request: ModelRequest) -> ModelResponse:
            requests.append(request)
            return ModelResponse(parts=(TextPart(text="OK"),))

    monkeypatch.setattr(
        AIProvider, "model", lambda *_args, **_kwargs: CheckModel()
    )

    checked = asyncio.run(manager.check({"model_name": "candidate-model"}))

    assert checked.provider.model_name == "candidate-model"
    assert requests[0].messages[0].parts == (TextPart(text="Reply with OK."),)
    assert (home / "config.yaml").read_bytes() == before


def test_provider_manager_rejects_unknown_and_null_fields(
    tmp_path: Path,
) -> None:
    home = initialise_home(tmp_path / ".ethos")
    manager = ProviderManager(home / "config.yaml")

    with pytest.raises(ValidationError, match="Extra inputs"):
        manager.configure(
            {
                "name": "ollama",
                "model_name": "llama3.2",
                "unknown": True,
            }
        )
    with pytest.raises(ValueError, match="model_name"):
        manager.configure({"name": "ollama", "model_name": None})


def test_provider_manager_uses_referenced_environment_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = initialise_home(tmp_path / ".ethos")
    monkeypatch.setenv("ETHOS_KEYS__OPENAI_API_KEY", "environment-key")

    settings = ProviderManager(home / "config.yaml").configure(
        {"name": "openai", "model_name": "gpt-5-mini"}
    )

    assert settings.keys.openai_api_key is not None
    assert settings.keys.openai_api_key.get_secret_value() == "environment-key"
    assert "environment-key" not in (home / "config.yaml").read_text()
