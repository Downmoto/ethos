"""Canonical capability configuration and persistence."""

from enum import StrEnum
from pathlib import Path
from typing import Final, cast
from uuid import uuid4

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field

CAPABILITIES_FILE: Final = "capabilities.yaml"


class CapabilityName(StrEnum):
    SKILLS = "skills"
    READ_ONLY_FILE_SYSTEM = "read_only_file_system"


class SkillsCapabilityConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True
    max_skill_file_bytes: int = Field(default=100 * 1024, ge=1)
    max_skills: int = Field(default=200, ge=1)
    max_resource_file_bytes: int = Field(default=100 * 1024, ge=1)
    max_resources: int = Field(default=200, ge=1)


class ReadOnlyFilesystemCapabilityConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True
    max_read_file_bytes: int = Field(default=100 * 1024, ge=1)
    max_list_file_entries: int = Field(default=1_000, ge=1)


class GlobalCapabilitiesConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    skills: SkillsCapabilityConfig = Field(
        default_factory=SkillsCapabilityConfig
    )
    read_only_file_system: ReadOnlyFilesystemCapabilityConfig = Field(
        default_factory=ReadOnlyFilesystemCapabilityConfig
    )


class SkillsCapabilityOverride(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool | None = None
    max_skill_file_bytes: int | None = Field(default=None, ge=1)
    max_skills: int | None = Field(default=None, ge=1)
    max_resource_file_bytes: int | None = Field(default=None, ge=1)
    max_resources: int | None = Field(default=None, ge=1)


class ReadOnlyFilesystemCapabilityOverride(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool | None = None
    max_read_file_bytes: int | None = Field(default=None, ge=1)
    max_list_file_entries: int | None = Field(default=None, ge=1)


class WorkspaceCapabilityOverrides(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    skills: SkillsCapabilityOverride | None = None
    read_only_file_system: ReadOnlyFilesystemCapabilityOverride | None = None


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


type CapabilitySettings = (
    SkillsCapabilityConfig | ReadOnlyFilesystemCapabilityConfig
)
type CapabilityOverride = (
    SkillsCapabilityOverride | ReadOnlyFilesystemCapabilityOverride
)


def parse_capability_name(value: str | CapabilityName) -> CapabilityName:
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
            read_only_file_system=_effective_settings(
                config.global_settings.read_only_file_system,
                overrides.read_only_file_system,
            ),
        )

    def configure_global(
        self,
        capability: str | CapabilityName,
        changes: dict[str, object],
    ) -> CapabilityConfiguration:
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
    return config.global_settings.read_only_file_system


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
    return overrides.read_only_file_system


def _override_type(
    name: CapabilityName,
) -> type[CapabilityOverride]:
    if name is CapabilityName.SKILLS:
        return SkillsCapabilityOverride
    return ReadOnlyFilesystemCapabilityOverride


def _effective_settings[Settings: CapabilitySettings](
    global_settings: Settings,
    override: CapabilityOverride | None,
) -> Settings:
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
