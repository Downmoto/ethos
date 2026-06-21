import json
from pathlib import Path

from cassiopeia.config import Settings, load_codex_openai_credential
from pydantic import SecretStr


def test_load_codex_openai_credential_prefers_api_key(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps(
            {
                "OPENAI_API_KEY": "api-key",
                "tokens": {"access_token": "access-token"},
            }
        )
    )

    credential = load_codex_openai_credential(auth_path)

    assert credential is not None
    assert credential.get_secret_value() == "api-key"


def test_load_codex_openai_credential_falls_back_to_access_token(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps({"OPENAI_API_KEY": None, "tokens": {"access_token": "token"}}))

    credential = load_codex_openai_credential(auth_path)

    assert credential is not None
    assert credential.get_secret_value() == "token"


def test_settings_openai_credential_prefers_explicit_key(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps({"tokens": {"access_token": "codex-token"}}))

    settings = Settings(openai_api_key=SecretStr("explicit-key"), codex_auth_path=auth_path)

    credential = settings.openai_credential()

    assert credential is not None
    assert credential.get_secret_value() == "explicit-key"


def test_settings_openai_credential_can_use_codex_auth(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps({"tokens": {"access_token": "codex-token"}}))

    settings = Settings(codex_auth_path=auth_path)

    credential = settings.openai_credential()

    assert credential is not None
    assert credential.get_secret_value() == "codex-token"
