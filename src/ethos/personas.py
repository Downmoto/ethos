"""Typed personas, workspace assignments, and atomic YAML persistence.

Personas belong to workspaces, never sessions. Session code deliberately has
no persona field: resolving a workspace at the start of a turn makes an
explicit reassignment apply consistently to every conversation it owns.
"""

import re
from pathlib import Path
from typing import Annotated, Final, Self, cast
from uuid import uuid4

import yaml  # type: ignore[import-untyped]
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from ethos.capability_config import CapabilityName
from ethos.models import ReasoningEffort

PERSONAS_FILE: Final = "personas.yaml"
ETHOS_PERSONA_ID: Final = "ethos"
_IDENTIFIER_PATTERN: Final = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MAX_IDENTIFIER_LENGTH: Final = 63
_RESERVED_IDENTIFIERS: Final = frozenset(
    {
        "default",
        "aux",
        "con",
        "nul",
        "prn",
        *(f"com{number}" for number in range(1, 10)),
        *(f"lpt{number}" for number in range(1, 10)),
    }
)


def _validate_identifier(value: str) -> str:
    if (
        len(value) > _MAX_IDENTIFIER_LENGTH
        or _IDENTIFIER_PATTERN.fullmatch(value) is None
    ):
        raise ValueError(f"invalid persona identifier: {value!r}")
    if value in _RESERVED_IDENTIFIERS:
        raise ValueError(f"reserved persona identifier: {value}")
    return value


def _strip_non_empty(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("value must not be empty")
    return stripped


type PersonaId = Annotated[str, AfterValidator(_validate_identifier)]
type PersonaName = Annotated[
    str, Field(max_length=100), AfterValidator(_strip_non_empty)
]
type PersonaInstructions = Annotated[
    str, Field(max_length=100_000), AfterValidator(_strip_non_empty)
]
type ModelName = Annotated[
    str, Field(max_length=500), AfterValidator(_strip_non_empty)
]


class Persona(BaseModel):
    """One configurable identity available for workspace assignment.

    ``capabilities=None`` inherits every otherwise available capability, while
    an empty tuple permits none. Numeric limits remain owned by global and
    workspace capability configuration.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: PersonaName
    instructions: PersonaInstructions
    enabled: bool = True
    model_name: ModelName | None = None
    reasoning_effort: ReasoningEffort | None = None
    capabilities: tuple[CapabilityName, ...] | None = None

    @field_validator("capabilities")
    @classmethod
    def normalise_capabilities(
        cls, value: tuple[CapabilityName, ...] | None
    ) -> tuple[CapabilityName, ...] | None:
        if value is None:
            return None
        if len(value) != len(set(value)):
            raise ValueError("persona capabilities must be unique")
        return tuple(sorted(value, key=lambda item: item.value))


class RemovedPersona(BaseModel):
    """Identity and security ceiling retained after persona removal.

    Retaining a tombstone prevents an old workspace or future memory record
    from acquiring the identity of a newly created persona with the same ID.
    Its allowlist also prevents fallback to Ethos from broadening tool access.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: PersonaName
    capabilities: tuple[CapabilityName, ...] | None = None


ETHOS_PERSONA: Final = Persona(
    name="Ethos",
    instructions="You are Ethos, a personal AI assistant.",
)


class PersonaConfiguration(BaseModel):
    """Canonical persona records, defaults, and workspace assignments.

    The global default is copied when a workspace is created. It is not a
    dynamic fallback and changing it never changes an existing assignment.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        populate_by_name=True,
    )

    global_default: PersonaId = Field(
        default=ETHOS_PERSONA_ID,
        alias="default",
    )
    personas: dict[PersonaId, Persona] = Field(
        default_factory=lambda: {ETHOS_PERSONA_ID: ETHOS_PERSONA}
    )
    removed: dict[PersonaId, RemovedPersona] = Field(default_factory=dict)
    workspaces: dict[str, PersonaId] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        if self.personas.get(ETHOS_PERSONA_ID) != ETHOS_PERSONA:
            raise ValueError("the built-in ethos persona cannot be changed")
        overlap = set(self.personas) & set(self.removed)
        if overlap:
            raise ValueError(
                f"persona is both active and removed: {min(overlap)}"
            )
        names: dict[str, str] = {}
        for identifier, record in self.personas.items():
            key = record.name.casefold()
            if key in names:
                raise ValueError(
                    "persona name must be unique: "
                    f"{record.name!r} conflicts with {names[key]!r}"
                )
            names[key] = identifier
        for identifier, removed_record in self.removed.items():
            key = removed_record.name.casefold()
            if key in names:
                raise ValueError(
                    "persona name must be unique: "
                    f"{removed_record.name!r} conflicts with {names[key]!r}"
                )
            names[key] = identifier
        default = self.personas.get(self.global_default)
        if default is None or not default.enabled:
            raise ValueError(
                f"default persona is not enabled: {self.global_default}"
            )
        return self


class PersonaResolution(BaseModel):
    """One workspace's assigned and effective runtime identities.

    ``fallback`` is derived observability metadata, not a decision input. The
    effective record and capability ceiling already contain the behaviour the
    runtime must apply.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    assigned_id: PersonaId
    effective_id: PersonaId
    effective: Persona
    capability_ceiling: tuple[CapabilityName, ...] | None

    @property
    def fallback(self) -> bool:
        return self.assigned_id != self.effective_id


class PersonaManager:
    """Manage personas and workspace assignments through one YAML file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> PersonaConfiguration:
        """Load current state without turning a legacy read into a write.

        Homes created before persona support have no file. Treating that as
        the built-in configuration preserves their Ethos behaviour while
        leaving migration to the first explicit persona mutation.
        """
        if not self.path.exists():
            return PersonaConfiguration()
        try:
            raw: object = cast(
                object,
                yaml.safe_load(self.path.read_text(encoding="utf-8")),
            )
        except yaml.YAMLError as error:
            raise ValueError(f"invalid {self.path.name}: {error}") from error
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise ValueError(f"{self.path.name} must contain a mapping")
        return PersonaConfiguration.model_validate(raw)

    def list(self) -> tuple[tuple[str, Persona], ...]:
        return tuple(sorted(self.load().personas.items()))

    def get(self, identifier: str) -> Persona:
        canonical = _validate_identifier(identifier)
        return self._active(self.load(), canonical)

    @staticmethod
    def _active(config: PersonaConfiguration, canonical: str) -> Persona:
        persona = config.personas.get(canonical)
        if persona is not None:
            return persona
        if canonical in config.removed:
            raise FileNotFoundError(f"persona was removed: {canonical}")
        raise FileNotFoundError(f"persona does not exist: {canonical}")

    def create(self, identifier: str, values: dict[str, object]) -> Persona:
        canonical = _validate_identifier(identifier)
        config = self.load()
        if canonical in config.personas or canonical in config.removed:
            raise FileExistsError(f"persona already exists: {canonical}")
        persona = Persona.model_validate(values)
        personas = dict(config.personas)
        personas[canonical] = persona
        self._save(config.model_copy(update={"personas": personas}))
        return persona

    def update(self, identifier: str, changes: dict[str, object]) -> Persona:
        canonical = self._mutable_identifier(identifier)
        if not changes:
            raise ValueError("persona changes must not be empty")
        config = self.load()
        current = self._active(config, canonical)
        updated = Persona.model_validate(current.model_dump() | changes)
        personas = dict(config.personas)
        personas[canonical] = updated
        self._save(config.model_copy(update={"personas": personas}))
        return updated

    def remove(self, identifier: str) -> RemovedPersona:
        """Replace an active persona with its non-reusable tombstone."""
        canonical = self._mutable_identifier(identifier)
        config = self.load()
        persona = self._active(config, canonical)
        removed = RemovedPersona(
            name=persona.name,
            capabilities=persona.capabilities,
        )
        personas = dict(config.personas)
        del personas[canonical]
        tombstones = dict(config.removed)
        tombstones[canonical] = removed
        self._save(
            config.model_copy(
                update={"personas": personas, "removed": tombstones}
            )
        )
        return removed

    def set_default(self, identifier: str) -> Persona:
        """Set the persona copied into subsequently created workspaces."""
        canonical = _validate_identifier(identifier)
        config = self.load()
        persona = self._active(config, canonical)
        if not persona.enabled:
            raise ValueError(f"persona is disabled: {canonical}")
        self._save(config.model_copy(update={"global_default": canonical}))
        return persona

    def default(self) -> tuple[str, Persona]:
        config = self.load()
        return config.global_default, config.personas[config.global_default]

    def assign(self, workspace: str, identifier: str) -> PersonaResolution:
        """Persist one active persona as a workspace's current assignment."""
        canonical = _validate_identifier(identifier)
        config = self.load()
        persona = self._active(config, canonical)
        if not persona.enabled:
            raise ValueError(f"persona is disabled: {canonical}")
        workspaces = dict(config.workspaces)
        workspaces[workspace] = canonical
        self._save(config.model_copy(update={"workspaces": workspaces}))
        return self.resolve(workspace)

    def assign_default(self, workspace: str) -> PersonaResolution:
        identifier, _persona = self.default()
        return self.assign(workspace, identifier)

    def resolve(self, workspace: str) -> PersonaResolution:
        """Resolve runtime identity and the assignment's security ceiling.

        Disabled and removed assignments run with Ethos behaviour but retain
        their last allowlist. An unexpectedly missing record has no reliable
        ceiling, so it fails closed with no capabilities.
        """
        config = self.load()
        assigned = config.workspaces.get(workspace, ETHOS_PERSONA_ID)
        persona = config.personas.get(assigned)
        if persona is not None and persona.enabled:
            return PersonaResolution(
                assigned_id=assigned,
                effective_id=assigned,
                effective=persona,
                capability_ceiling=persona.capabilities,
            )
        ceiling = (
            persona.capabilities
            if persona is not None
            else (
                config.removed[assigned].capabilities
                if assigned in config.removed
                else ()
            )
        )
        return PersonaResolution(
            assigned_id=assigned,
            effective_id=ETHOS_PERSONA_ID,
            effective=ETHOS_PERSONA,
            capability_ceiling=ceiling,
        )

    def _save(self, config: PersonaConfiguration) -> PersonaConfiguration:
        validated = PersonaConfiguration.model_validate(
            config.model_dump(by_alias=True, mode="json")
        )
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                yaml.safe_dump(
                    validated.model_dump(by_alias=True, mode="json"),
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            temporary.chmod(0o600)
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)
        return validated

    @staticmethod
    def _mutable_identifier(identifier: str) -> str:
        canonical = _validate_identifier(identifier)
        if canonical == ETHOS_PERSONA_ID:
            raise ValueError("the built-in ethos persona cannot be changed")
        return canonical
