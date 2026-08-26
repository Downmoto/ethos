"""Typed capability settings, workspace overrides, and YAML persistence.

The file model deliberately keeps complete global ceilings separate from
sparse workspace overrides. Runtime callers ask the manager for effective
settings rather than merging configuration themselves.
"""

from enum import StrEnum
from pathlib import Path
from typing import Final, cast
from uuid import uuid4

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field

CAPABILITIES_FILE: Final = "capabilities.yaml"


class CapabilityName(StrEnum):
    """Stable names accepted by configuration, CLI, and Vox boundaries."""

    SKILLS = "skills"
    FILE_SYSTEM = "file_system"


class SkillsCapabilityConfig(BaseModel):
    """Complete global settings for Agent Skills discovery and reads."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True
    max_skill_file_bytes: int = Field(default=100 * 1024, ge=1)
    max_skills: int = Field(default=200, ge=1)
    max_resource_file_bytes: int = Field(default=100 * 1024, ge=1)
    max_resources: int = Field(default=200, ge=1)


class FilesystemCapabilityConfig(BaseModel):
    """Complete global settings for workspace-bounded filesystem tools."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True
    max_read_file_bytes: int = Field(default=100 * 1024, ge=1)
    max_write_file_bytes: int = Field(default=100 * 1024, ge=1)
    max_file_entries: int = Field(default=1_000, ge=1)
    max_search_matches: int = Field(default=1_000, ge=1)
    max_search_result_bytes: int = Field(default=100 * 1024, ge=1)
    max_patch_bytes: int = Field(default=100 * 1024, ge=1)
    max_patch_files: int = Field(default=20, ge=1)


class GlobalCapabilitiesConfig(BaseModel):
    """Complete settings that bound every workspace."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    skills: SkillsCapabilityConfig = Field(
        default_factory=SkillsCapabilityConfig
    )
    file_system: FilesystemCapabilityConfig = Field(
        default_factory=FilesystemCapabilityConfig
    )


class SkillsCapabilityOverride(BaseModel):
    """Optional workspace values that may only narrow skill settings."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool | None = None
    max_skill_file_bytes: int | None = Field(default=None, ge=1)
    max_skills: int | None = Field(default=None, ge=1)
    max_resource_file_bytes: int | None = Field(default=None, ge=1)
    max_resources: int | None = Field(default=None, ge=1)


class FilesystemCapabilityOverride(BaseModel):
    """Optional workspace values that may only narrow filesystem settings."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool | None = None
    max_read_file_bytes: int | None = Field(default=None, ge=1)
    max_write_file_bytes: int | None = Field(default=None, ge=1)
    max_file_entries: int | None = Field(default=None, ge=1)
    max_search_matches: int | None = Field(default=None, ge=1)
    max_search_result_bytes: int | None = Field(default=None, ge=1)
    max_patch_bytes: int | None = Field(default=None, ge=1)
    max_patch_files: int | None = Field(default=None, ge=1)


class WorkspaceCapabilityOverrides(BaseModel):
    """Sparse capability overrides stored only for configured workspaces."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    skills: SkillsCapabilityOverride | None = None
    file_system: FilesystemCapabilityOverride | None = None


class CapabilityConfiguration(BaseModel):
    """Global capability ceilings and sparse workspace overrides."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        populate_by_name=True,
    )

    global_settings: GlobalCapabilitiesConfig = Field(
        default_factory=GlobalCapabilitiesConfig,
        alias="global",
    )
    workspaces: dict[str, WorkspaceCapabilityOverrides] = Field(
        default_factory=dict
    )


type CapabilitySettings = SkillsCapabilityConfig | FilesystemCapabilityConfig
type CapabilityOverride = (
    SkillsCapabilityOverride | FilesystemCapabilityOverride
)


def parse_capability_name(value: str | CapabilityName) -> CapabilityName:
    """Normalise public input and give unknown names a domain-level error."""

    if isinstance(value, CapabilityName):
        return value
    try:
        return CapabilityName(value)
    except ValueError:
        raise ValueError(f"unknown capability: {value}") from None


class CapabilityManager:
    """Manage capability configuration and its atomic persistence."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> CapabilityConfiguration:
        """Load and validate the complete file without caching it."""

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
        return CapabilityConfiguration.model_validate(raw)

    def configured(
        self,
        capability: str | CapabilityName,
        workspace: str | None = None,
    ) -> dict[str, object]:
        """Return complete global values or one sparse workspace override."""

        name = parse_capability_name(capability)
        config = self.load()
        if workspace is None:
            return _global_settings(config, name).model_dump()
        override = _workspace_override(config, workspace, name)
        return (
            override.model_dump(exclude_none=True)
            if override is not None
            else {}
        )

    def effective(
        self, workspace: str | None = None
    ) -> GlobalCapabilitiesConfig:
        """Resolve settings with global values as non-expandable ceilings."""

        config = self.load()
        if workspace is None:
            return config.global_settings
        overrides = config.workspaces.get(workspace)
        if overrides is None:
            return config.global_settings
        return GlobalCapabilitiesConfig(
            skills=_effective_settings(
                config.global_settings.skills,
                overrides.skills,
            ),
            file_system=_effective_settings(
                config.global_settings.file_system,
                overrides.file_system,
            ),
        )

    def configure_global(
        self,
        capability: str | CapabilityName,
        changes: dict[str, object],
    ) -> CapabilityConfiguration:
        """Validate and merge one global capability change atomically."""

        name = parse_capability_name(capability)
        config = self.load()
        current = _global_settings(config, name)
        updated = type(current).model_validate(current.model_dump() | changes)
        global_settings = config.global_settings.model_copy(
            update={name.value: updated}
        )
        return self._save(
            config.model_copy(update={"global_settings": global_settings})
        )

    def configure_workspace(
        self,
        workspace: str,
        capability: str | CapabilityName,
        changes: dict[str, object],
    ) -> CapabilityConfiguration:
        """Validate and merge one sparse workspace override atomically."""

        name = parse_capability_name(capability)
        config = self.load()
        overrides = config.workspaces.get(
            workspace, WorkspaceCapabilityOverrides()
        )
        current = _workspace_override(config, workspace, name)
        override_type = _override_type(name)
        configured = (
            current.model_dump(exclude_none=True) if current is not None else {}
        )
        updated = override_type.model_validate(configured | changes)
        workspace_overrides = overrides.model_copy(
            update={
                name.value: (
                    updated if updated.model_dump(exclude_none=True) else None
                )
            }
        )
        workspaces = dict(config.workspaces)
        if workspace_overrides.model_dump(exclude_none=True):
            workspaces[workspace] = workspace_overrides
        else:
            workspaces.pop(workspace, None)
        return self._save(config.model_copy(update={"workspaces": workspaces}))

    def reset_workspace(
        self,
        workspace: str,
        capability: str | CapabilityName,
    ) -> CapabilityConfiguration:
        """Remove one override, pruning an empty workspace entry."""

        name = parse_capability_name(capability)
        config = self.load()
        overrides = config.workspaces.get(workspace)
        if (
            overrides is None
            or _workspace_override(config, workspace, name) is None
        ):
            return config
        updated = overrides.model_copy(update={name.value: None})
        workspaces = dict(config.workspaces)
        if updated.model_dump(exclude_none=True):
            workspaces[workspace] = updated
        else:
            del workspaces[workspace]
        return self._save(config.model_copy(update={"workspaces": workspaces}))

    def _save(self, config: CapabilityConfiguration) -> CapabilityConfiguration:
        """Validate the whole model before atomically replacing its YAML."""

        validated = CapabilityConfiguration.model_validate(
            config.model_dump(by_alias=True, exclude_none=True)
        )
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                yaml.safe_dump(
                    validated.model_dump(by_alias=True, exclude_none=True),
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            temporary.chmod(0o600)
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)
        return validated


def _global_settings(
    config: CapabilityConfiguration,
    name: CapabilityName,
) -> CapabilitySettings:
    if name is CapabilityName.SKILLS:
        return config.global_settings.skills
    return config.global_settings.file_system


def _workspace_override(
    config: CapabilityConfiguration,
    workspace: str,
    name: CapabilityName,
) -> CapabilityOverride | None:
    overrides = config.workspaces.get(workspace)
    if overrides is None:
        return None
    if name is CapabilityName.SKILLS:
        return overrides.skills
    return overrides.file_system


def _override_type(
    name: CapabilityName,
) -> type[CapabilityOverride]:
    if name is CapabilityName.SKILLS:
        return SkillsCapabilityOverride
    return FilesystemCapabilityOverride


def _effective_settings[Settings: CapabilitySettings](
    global_settings: Settings,
    override: CapabilityOverride | None,
) -> Settings:
    """Intersect enablement and choose the lower configured numeric limits."""

    if override is None:
        return global_settings
    values = global_settings.model_dump()
    for field, value in override.model_dump(exclude_none=True).items():
        ceiling = values[field]
        values[field] = (
            ceiling and value
            if field == "enabled"
            else min(cast(int, ceiling), cast(int, value))
        )
    return cast(Settings, type(global_settings).model_validate(values))
