"""Agent Skill metadata and assignment models."""

from pathlib import Path

from pydantic import ConfigDict

from cassiopeia.permissions import SecurityRing
from cassiopeia.shared import DefinitionScope, EntityId, NonEmptyString, Slug, TimestampedRecord


class SkillReference(TimestampedRecord):
    """Reference to an indexed Agent Skill."""

    id: EntityId
    slug: Slug
    name: NonEmptyString
    available: bool = True


class SkillDefinition(SkillReference):
    """Indexed Agent Skill metadata without copying the skill spec."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    description: NonEmptyString
    source_path: Path
    scope: DefinitionScope = DefinitionScope.GLOBAL
    instruction_ring: SecurityRing = SecurityRing.RING_3
    script_ring: SecurityRing = SecurityRing.RING_2
    security_metadata_refs: tuple[EntityId, ...] = ()
