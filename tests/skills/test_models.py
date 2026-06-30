from pathlib import Path

from cassiopeia.permissions import SecurityRing
from cassiopeia.skills import SkillDefinition, SkillReference


def test_skill_definition_keeps_agent_skill_metadata_shallow() -> None:
    reference = SkillReference(id="skill-review", slug="review", name="Review")
    definition = SkillDefinition(
        id=reference.id,
        slug=reference.slug,
        name=reference.name,
        description="Review code",
        source_path=Path("/tmp/skills/review"),
        instruction_ring=SecurityRing.RING_3,
        script_ring=SecurityRing.RING_2,
    )

    assert SkillDefinition.model_validate_json(definition.model_dump_json()) == definition
