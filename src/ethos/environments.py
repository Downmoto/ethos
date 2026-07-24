"""Resolve configuration and capabilities within a workspace policy boundary.

See ``docs/development/workspaces-and-runtime.md`` for layer precedence and
the security properties of tool and skill selection.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import AbstractToolset, FunctionToolset, RunContext
from pydantic_ai.tools import ToolDefinition
from pydantic_settings import EnvSettingsSource, YamlConfigSettingsSource

from ethos.config import CONFIG_FILE, EthosSettings
from ethos.storage import Storage
from ethos.workspaces import (
    TOOLS_CONFIG_FILE,
    Workspace,
    WorkspaceManager,
)


class ToolSelection(BaseModel):
    """Explicit tool decisions in one global or workspace policy layer."""

    model_config = ConfigDict(extra="forbid")

    tools: dict[str, bool] = Field(default_factory=dict)
    toolsets: dict[str, bool] = Field(default_factory=dict)


class SkillSelection(BaseModel):
    """Skills explicitly enabled by one workspace."""

    model_config = ConfigDict(extra="forbid")

    skills: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Skill:
    """One globally installed skill."""

    name: str
    path: Path


@dataclass(frozen=True)
class WorkspaceMemory:
    """Database access paired with the workspace identity it must remain in."""

    workspace_name: str
    storage: Storage


@dataclass(frozen=True)
class WorkspaceEnvironment:
    """One turn's validated, workspace-scoped dependencies and capabilities."""

    workspace: Workspace
    settings: EthosSettings
    toolsets: tuple[AbstractToolset[object], ...]
    skills: tuple[Skill, ...]
    memory: WorkspaceMemory


def _load_yaml[Model: BaseModel](path: Path, model: type[Model]) -> Model:
    raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    return model.model_validate({} if raw is None else raw)


def _merge(
    base: dict[str, object], override: dict[str, object]
) -> dict[str, object]:
    merged = base.copy()
    for name, value in override.items():
        current = merged.get(name)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[name] = _merge(
                cast(dict[str, object], current),
                cast(dict[str, object], value),
            )
        else:
            merged[name] = value
    return merged


def _resolve_settings(home: Path, workspace: Workspace) -> EthosSettings:
    """Merge global YAML, workspace YAML, then operator environment values."""
    yaml_values: dict[str, object] = YamlConfigSettingsSource(
        EthosSettings,
        yaml_file=(home / CONFIG_FILE, workspace.config_path),
        yaml_file_encoding="utf-8",
        deep_merge=True,
    )()
    environment_values: dict[str, object] = EnvSettingsSource(EthosSettings)()
    return EthosSettings.model_validate(_merge(yaml_values, environment_values))


def _validate_tool_selection(
    selection: ToolSelection,
    catalogue: Mapping[str, FunctionToolset[object]],
    source: Path,
) -> None:
    names = {name for toolset in catalogue.values() for name in toolset.tools}
    unknown_tools = sorted(selection.tools.keys() - names)
    unknown_toolsets = sorted(selection.toolsets.keys() - catalogue.keys())
    unknown = (*unknown_tools, *unknown_toolsets)
    if unknown:
        raise ValueError(
            f"unknown tools or toolsets in {source}: {', '.join(unknown)}"
        )


def _validate_tool_catalogue(
    catalogue: Mapping[str, FunctionToolset[object]],
) -> None:
    seen: set[str] = set()
    for toolset_name, toolset in catalogue.items():
        if not toolset_name:
            raise ValueError("toolset name must not be empty")
        duplicates = seen.intersection(toolset.tools)
        if duplicates:
            names = ", ".join(sorted(duplicates))
            raise ValueError(f"tools belong to multiple toolsets: {names}")
        seen.update(toolset.tools)


def _selected(
    tool_name: str,
    toolset_name: str,
    selection: ToolSelection,
) -> bool:
    """Resolve one layer, where a tool decision overrides its toolset."""
    if tool_name in selection.tools:
        return selection.tools[tool_name]
    return selection.toolsets.get(toolset_name, False)


def _include_tools(
    names: frozenset[str],
) -> Callable[[RunContext[object], ToolDefinition], bool]:
    return lambda _ctx, definition: definition.name in names


def _resolve_tools(
    home: Path,
    workspace: Workspace,
    catalogue: Mapping[str, FunctionToolset[object]],
) -> tuple[AbstractToolset[object], ...]:
    """Apply global and workspace policy as an intersection over the catalogue.

    Global policy is a ceiling: workspace configuration may narrow it but
    cannot restore a tool denied globally.
    """
    _validate_tool_catalogue(catalogue)
    global_path = home / TOOLS_CONFIG_FILE
    global_selection = _load_yaml(global_path, ToolSelection)
    workspace_selection = _load_yaml(workspace.tools_config_path, ToolSelection)
    _validate_tool_selection(global_selection, catalogue, global_path)
    _validate_tool_selection(
        workspace_selection, catalogue, workspace.tools_config_path
    )
    resolved: list[AbstractToolset[object]] = []
    for toolset_name in sorted(catalogue):
        toolset = catalogue[toolset_name]
        names = frozenset(
            tool_name
            for tool_name in toolset.tools
            if _selected(tool_name, toolset_name, global_selection)
            and _selected(tool_name, toolset_name, workspace_selection)
        )
        if names:
            resolved.append(toolset.filtered(_include_tools(names)))
    return tuple(resolved)


def _installed_skills(root: Path) -> dict[str, Skill]:
    """Discover regular skill directories without following symlinks."""
    if root.is_symlink():
        raise ValueError(f"skills directory must not be a symlink: {root}")
    if not root.exists():
        return {}
    if not root.is_dir():
        raise ValueError(f"skills path must be a directory: {root}")

    installed: dict[str, Skill] = {}
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        if path.is_symlink():
            raise ValueError(f"skill must not be a symlink: {path}")
        if not path.is_dir():
            continue
        definition = path / "SKILL.md"
        if definition.is_symlink() or not definition.is_file():
            raise ValueError(
                f"skill is missing a regular SKILL.md: {path.name}"
            )
        installed[path.name] = Skill(name=path.name, path=path)
    return installed


def _resolve_skills(home: Path, workspace: Workspace) -> tuple[Skill, ...]:
    selection = _load_yaml(workspace.skills_config_path, SkillSelection)
    installed = _installed_skills(home / "skills")
    missing = sorted(selection.skills - installed.keys())
    if missing:
        raise ValueError(f"skills are not installed: {', '.join(missing)}")
    return tuple(installed[name] for name in sorted(selection.skills))


def resolve_workspace_environment(
    home: Path,
    manager: WorkspaceManager,
    workspace_name: str,
    tool_catalogue: Mapping[str, FunctionToolset[object]],
    storage: Storage,
) -> WorkspaceEnvironment:
    """Resolve current settings and capabilities from one workspace identity.

    Resolution is intentionally performed per turn so configuration changes
    can take effect without being copied into persistent session records.
    Invalid capability policy fails the turn rather than silently producing a
    partial environment.
    """
    workspace = manager.get(workspace_name)
    return WorkspaceEnvironment(
        workspace=workspace,
        settings=_resolve_settings(home, workspace),
        toolsets=_resolve_tools(home, workspace, tool_catalogue),
        skills=_resolve_skills(home, workspace),
        memory=WorkspaceMemory(
            workspace_name=workspace.name,
            storage=storage,
        ),
    )
