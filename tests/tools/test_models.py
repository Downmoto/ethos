import pytest
from pydantic import ValidationError

from cassiopeia.permissions import SecurityRing
from cassiopeia.tools import ToolDefinition, ToolReference


def test_tool_reference_and_definition_validate_security_metadata() -> None:
    reference = ToolReference(id="tool-web-search", slug="web-search", name="Web search")
    definition = ToolDefinition(
        id=reference.id,
        slug=reference.slug,
        name=reference.name,
        description="Search the web",
        minimum_ring=SecurityRing.RING_1,
        action_id="action-web-search",
        security_metadata_refs=("security-web",),
    )

    assert ToolDefinition.model_validate_json(definition.model_dump_json()) == definition


def test_tool_definition_rejects_invalid_security_ring() -> None:
    with pytest.raises(ValidationError):
        ToolDefinition.model_validate(
            {
                "id": "tool-shell",
                "slug": "shell",
                "name": "Shell",
                "description": "Run shell commands",
                "minimum_ring": 4,
                "action_id": "action-shell",
            }
        )
