"""Persona definition models."""

from pydantic import BaseModel, ConfigDict

from cassiopeia.shared import (
    DefinitionScope,
    EntityId,
    MemoryScope,
    NonEmptyString,
    Slug,
    TimestampedRecord,
)


class ModelConfiguration(BaseModel):
    """Declarative model settings for a persona."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: NonEmptyString
    model: NonEmptyString
    temperature: float | None = None
    max_output_tokens: int | None = None


class PersonaSkillPolicy(BaseModel):
    """Selected skills available to a persona."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    selected: tuple[EntityId, ...] = ()


class PersonaToolPolicy(BaseModel):
    """Allowed tools available to a persona."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed: tuple[EntityId, ...] = ()


class PersonaWorkflowPolicy(BaseModel):
    """Workflows a persona may trigger."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed: tuple[EntityId, ...] = ()
    allow_workspace_workflows: bool = True
    allow_global_workflows: bool = False


class PersonaMemoryPolicy(BaseModel):
    """Memory scopes a persona may read and write."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    read_scopes: tuple[MemoryScope, ...] = (MemoryScope.SESSION, MemoryScope.WORKSPACE)
    write_scopes: tuple[MemoryScope, ...] = (MemoryScope.SESSION,)
    auto_create: bool = False
    expose_created: bool = True


class PersonaGatewayAvailability(BaseModel):
    """Gateway display and availability settings for a persona."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    gateway_ids: tuple[EntityId, ...] = ()
    display_name: NonEmptyString | None = None


class PersonaSessionPolicy(BaseModel):
    """Default session settings for a persona."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_history_messages: int | None = None
    summarise_history: bool = True


class PersonaDefinition(TimestampedRecord):
    """Declarative persona definition."""

    id: EntityId
    slug: Slug
    name: NonEmptyString
    description: NonEmptyString
    role: NonEmptyString
    tone: NonEmptyString
    behavioural_rules: tuple[NonEmptyString, ...] = ()
    scope: DefinitionScope = DefinitionScope.GLOBAL
    workspace_id: EntityId | None = None
    model: ModelConfiguration
    skills: PersonaSkillPolicy = PersonaSkillPolicy()
    tools: PersonaToolPolicy = PersonaToolPolicy()
    workflows: PersonaWorkflowPolicy = PersonaWorkflowPolicy()
    memory: PersonaMemoryPolicy = PersonaMemoryPolicy()
    gateway_availability: PersonaGatewayAvailability = PersonaGatewayAvailability()
    default_session: PersonaSessionPolicy = PersonaSessionPolicy()
