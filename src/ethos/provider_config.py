"""Provider configuration validation and atomic YAML persistence."""

import os
from copy import deepcopy
from pathlib import Path
from typing import cast
from uuid import uuid4

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, SecretStr

from ethos.config import EthosSettings
from ethos.models import Message, ModelRequest, ReasoningEffort, Role, TextPart
from ethos.provider import (
    AIProvider,
    ModelProtocolError,
    ModelProviderError,
    ProviderName,
)


class ProviderChanges(BaseModel):
    """Sparse provider changes accepted by public management boundaries."""

    model_config = ConfigDict(extra="forbid")

    name: ProviderName | None = None
    model_name: str | None = None
    reasoning_effort: ReasoningEffort | None = None
    ollama_base_url: str | None = None
    api_key: SecretStr | None = None


class ProviderManager:
    """Manage the selected provider without exposing stored credentials."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> EthosSettings:
        return self._validate(self._read())

    def candidate(self, changes: dict[str, object]) -> EthosSettings:
        settings, _raw = self._prepare(changes)
        return settings

    def configure(self, changes: dict[str, object]) -> EthosSettings:
        settings, raw = self._prepare(changes)
        self._save(raw)
        return settings

    def replace(self, raw: dict[str, object]) -> EthosSettings:
        """Validate and save a complete config, primarily for onboarding."""

        settings = self._validate(raw)
        self._save(raw)
        return settings

    async def check(self, changes: dict[str, object]) -> EthosSettings:
        """Make a minimal request using a candidate without persisting it."""

        settings = self.candidate(changes)
        model = AIProvider.from_settings(settings).model(
            settings.provider.model_name,
            settings.provider.reasoning_effort,
        )
        try:
            await model.request(
                ModelRequest(
                    messages=(
                        Message(
                            role=Role.USER,
                            parts=(TextPart(text="Reply with OK."),),
                        ),
                    )
                )
            )
        except (ModelProviderError, ModelProtocolError) as error:
            raise ValueError(f"provider check failed: {error}") from error
        return settings

    def _prepare(
        self, changes: dict[str, object]
    ) -> tuple[EthosSettings, dict[str, object]]:
        invalid_nulls = {
            name for name, value in changes.items() if value is None
        } - {"api_key"}
        if invalid_nulls:
            raise ValueError(
                f"provider field must not be null: {min(invalid_nulls)}"
            )
        raw = self._read()
        update = ProviderChanges.model_validate(changes)
        provider = self._section(raw, "provider")
        provider.update(
            update.model_dump(
                exclude={"api_key"}, exclude_none=True, mode="json"
            )
        )
        if "api_key" in update.model_fields_set:
            selected = cast(str, provider.get("name"))
            self._section(raw, "keys")[f"{selected}_api_key"] = (
                update.api_key.get_secret_value() if update.api_key else None
            )
        return self._validate(raw), raw

    @staticmethod
    def _validate(raw: dict[str, object]) -> EthosSettings:
        """Apply the documented environment overrides before validation."""

        effective = deepcopy(raw)
        for variable, value in os.environ.items():
            if not variable.startswith("ETHOS_") or "__" not in variable:
                continue
            path = variable.removeprefix("ETHOS_").lower().split("__")
            section = effective
            for name in path[:-1]:
                section = ProviderManager._section(section, name)
            section[path[-1]] = value
        return EthosSettings.model_validate(effective)

    def _read(self) -> dict[str, object]:
        try:
            raw: object = cast(
                object,
                yaml.safe_load(self.path.read_text(encoding="utf-8")),
            )
        except yaml.YAMLError as error:
            raise ValueError(f"invalid {self.path.name}: {error}") from error
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise ValueError(f"{self.path.name} must contain a mapping")
        return cast(dict[str, object], raw)

    @staticmethod
    def _section(raw: dict[str, object], name: str) -> dict[str, object]:
        section = raw.setdefault(name, {})
        if not isinstance(section, dict):
            raise ValueError(f"invalid {name} section in config.yaml")
        return cast(dict[str, object], section)

    def _save(self, raw: dict[str, object]) -> None:
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                yaml.safe_dump(raw, sort_keys=False), encoding="utf-8"
            )
            temporary.chmod(0o600)
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)
