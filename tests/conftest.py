import pytest

from ethos.config import EthosSettings, get_settings


@pytest.fixture(autouse=True)
def isolate_settings_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unit tests independent from the user's real Ethos home."""
    monkeypatch.setitem(EthosSettings.model_config, "yaml_file", None)
    get_settings.cache_clear()
