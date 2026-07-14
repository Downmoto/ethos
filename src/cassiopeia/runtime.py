"""Single-turn Cassiopeia agent runtime."""

import asyncio

from pydantic_ai import Agent

from cassiopeia.config import CassiopeiaSettings, get_settings
from cassiopeia.provider import AIProvider


def run_prompt(prompt: str, settings: CassiopeiaSettings | None = None) -> str:
    """Run one prompt with the configured provider and model."""
    settings = settings or get_settings()
    if settings.provider.name is None:
        raise ValueError("CASS_PROVIDER__NAME is required")
    if settings.provider.model_name is None:
        raise ValueError("CASS_PROVIDER__MODEL_NAME is required")

    provider = AIProvider.from_settings(settings)
    model = provider.model(settings.provider.model_name)

    return asyncio.run(Agent(model, output_type=str).run(prompt)).output
