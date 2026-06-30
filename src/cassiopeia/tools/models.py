"""Tool definition and reference models."""

from pydantic import ConfigDict

from cassiopeia.permissions import SecurityRing
from cassiopeia.shared import DefinitionScope, EntityId, NonEmptyString, Slug, TimestampedRecord


class ToolReference(TimestampedRecord):
    """Reference to an available tool."""

    id: EntityId
    slug: Slug
    name: NonEmptyString
    available: bool = True


class ToolDefinition(ToolReference):
    """User-visible metadata for an executable tool."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    description: NonEmptyString
    scope: DefinitionScope = DefinitionScope.GLOBAL
    minimum_ring: SecurityRing
    action_id: EntityId
    security_metadata_refs: tuple[EntityId, ...] = ()
