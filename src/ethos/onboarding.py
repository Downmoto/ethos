"""Interactive setup steps for ethos."""

from collections.abc import Callable
from pathlib import Path
from typing import Any, Final, cast

import click
import yaml  # type: ignore[import-untyped]

from ethos.config import CONFIG_FILE, EthosSettings
from ethos.provider import ProviderName

type Config = dict[str, Any]
type OnboardingStep = Callable[[Config], None]


def _section(config: Config, name: str) -> Config:
    section = config.setdefault(name, {})
    if not isinstance(section, dict):
        raise click.ClickException(f"invalid {name} section in config.yaml")
    return cast(Config, section)


def configure_provider(config: Config) -> None:
    """Select the model provider."""
    _section(config, "provider")["name"] = click.prompt(
        "Provider",
        type=click.Choice([provider.value for provider in ProviderName]),
    )


def configure_model(config: Config) -> None:
    """Select the model exposed by the provider."""
    _section(config, "provider")["model_name"] = click.prompt("Model")


def configure_credentials(config: Config) -> None:
    """Collect the connection details required by the selected provider."""
    provider = _section(config, "provider")["name"]
    keys = _section(config, "keys")

    if provider == ProviderName.OLLAMA:
        provider_config = _section(config, "provider")
        provider_config["ollama_base_url"] = click.prompt(
            "Ollama base URL",
            default=provider_config.get(
                "ollama_base_url", "http://localhost:11434/v1"
            ),
        )
        api_key = click.prompt(
            "Ollama API key for Ollama Cloud (optional)",
            default="",
            hide_input=True,
            show_default=False,
        )
        keys["ollama_api_key"] = api_key or None
        return

    keys[f"{provider}_api_key"] = click.prompt(
        f"{str(provider).title()} API key",
        hide_input=True,
    )


# Reorder, add, or remove functions here to change the onboarding sequence.
ONBOARDING_STEPS: Final[tuple[OnboardingStep, ...]] = (
    configure_provider,
    configure_model,
    configure_credentials,
)


def run_onboarding(home: Path) -> None:
    """Run the configured onboarding steps and save their settings."""
    config_path = home / CONFIG_FILE
    config = yaml.safe_load(config_path.read_text()) or {}  # pyright: ignore[reportUnknownVariableType]
    if not isinstance(config, dict):
        raise click.ClickException("config.yaml must contain a mapping")

    for step in ONBOARDING_STEPS:
        step(config)  # pyright: ignore[reportUnknownArgumentType]

    EthosSettings.model_validate(config)
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
