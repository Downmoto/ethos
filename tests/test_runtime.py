import pytest
from pydantic_ai.models.test import TestModel

from cassiopeia.config import CassiopeiaSettings
from cassiopeia.provider import AIProvider
from cassiopeia.runtime import run_prompt


def test_run_prompt_returns_model_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = CassiopeiaSettings.model_validate(
        {
            "provider": {"name": "openai", "model_name": "gpt-5-mini"},
            "keys": {"openai_api_key": "test-key"},
        }
    )
    monkeypatch.setattr(
        AIProvider,
        "model",
        lambda _provider, _model_name: TestModel(
            custom_output_text="hello from cassiopeia"
        ),
    )

    output = run_prompt("hello", settings)

    assert output == "hello from cassiopeia"


def test_run_prompt_requires_provider_selection() -> None:
    with pytest.raises(ValueError, match="CASS_PROVIDER__NAME is required"):
        run_prompt("hello", CassiopeiaSettings.defaults())
