"""Workspace definition models."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from cassiopeia.shared import EntityId, Slug, TimestampedRecord


class WorkspaceAvailabilityPolicy(BaseModel):
    """Workspace-level availability for user-authored definitions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    available_personas: tuple[EntityId, ...] = ()
    available_gateways: tuple[EntityId, ...] = ()
    available_tools: tuple[EntityId, ...] = ()
    available_skills: tuple[EntityId, ...] = ()
    available_workflows: tuple[EntityId, ...] = ()


class WorkspaceDefinition(TimestampedRecord):
    """User-authored workspace definition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    slug: Slug
    display_name: EntityId
    root_path: Path
    manager_persona_id: EntityId
    availability: WorkspaceAvailabilityPolicy = Field(default_factory=WorkspaceAvailabilityPolicy)
