"""Progressively disclosed Agent Skills support."""

import asyncio
import html
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import cast

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ethos.capabilities import RunContext
from ethos.events import event_factory
from ethos.events.emitters import EnvelopeEventEmitter
from ethos.events.models import EventPayload, NonEmptyString
from ethos.events.types import EventType
from ethos.models import ToolDefinition
from ethos.tools import Tool, ToolEffect, ToolExecutionError

_VALID_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class SkillMetadata(BaseModel):
    """Metadata needed for tier-one skill disclosure."""

    model_config = ConfigDict(frozen=True, extra="allow")

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)


@dataclass(frozen=True)
class Skill:
    metadata: SkillMetadata
    location: Path

    @property
    def directory(self) -> Path:
        return self.location.parent


class SkillDiagnosticCode(StrEnum):
    UNREADABLE_ROOT = "unreadable_root"
    UNREADABLE_SKILL = "unreadable_skill"
    MISSING_FRONTMATTER = "missing_frontmatter"
    MISSING_CLOSING_FRONTMATTER = "missing_closing_frontmatter"
    INVALID_METADATA = "invalid_metadata"
    EMPTY_METADATA = "empty_metadata"
    NAME_DIRECTORY_MISMATCH = "name_directory_mismatch"
    INVALID_NAME = "invalid_name"
    DESCRIPTION_TOO_LONG = "description_too_long"
    FRONTMATTER_TOO_LARGE = "frontmatter_too_large"
    SKILL_LIMIT_REACHED = "skill_limit_reached"
    SHADOWED = "shadowed"


class SkillDiagnosticEventPayload(EventPayload):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_name: NonEmptyString | None = "skill.diagnostic"
    code: SkillDiagnosticCode
    path: str
    skill_name: str | None = None
    related_path: str | None = None


type DiagnosticReporter = Callable[[SkillDiagnosticEventPayload], None]


def _report(
    reporter: DiagnosticReporter | None,
    code: SkillDiagnosticCode,
    path: Path,
    *,
    skill_name: str | None = None,
    related_path: Path | None = None,
) -> None:
    if reporter is not None:
        reporter(
            SkillDiagnosticEventPayload(
                code=code,
                path=str(path),
                skill_name=skill_name,
                related_path=str(related_path) if related_path else None,
            )
        )


def discover_skills(
    *skills_roots: Path,
    reporter: DiagnosticReporter | None = None,
    max_skill_file_bytes: int = 100 * 1024,
    max_skills: int = 200,
) -> tuple[Skill, ...]:
    """Discover direct child skills, with later roots taking precedence."""
    if max_skill_file_bytes < 1 or max_skills < 1:
        raise ValueError("skill discovery limits must be positive")
    skills: dict[str, Skill] = {}
    for skills_root in reversed(skills_roots):
        try:
            if not skills_root.is_dir():
                continue
            directories = skills_root.iterdir()
            for directory in directories:
                path = directory / "SKILL.md"
                if not directory.is_dir() or not path.is_file():
                    continue
                if len(skills) == max_skills:
                    _report(
                        reporter,
                        SkillDiagnosticCode.SKILL_LIMIT_REACHED,
                        path,
                    )
                    return tuple(skills[name] for name in sorted(skills))
                skill = _parse_skill(
                    path.resolve(),
                    max_skill_file_bytes,
                    reporter,
                )
                if skill is None:
                    continue
                name = skill.metadata.name
                if name in skills:
                    _report(
                        reporter,
                        SkillDiagnosticCode.SHADOWED,
                        skill.location,
                        skill_name=name,
                        related_path=skills[name].location,
                    )
                    continue
                skills[name] = skill
        except OSError:
            _report(reporter, SkillDiagnosticCode.UNREADABLE_ROOT, skills_root)
    return tuple(skills[name] for name in sorted(skills))


def _parse_skill(
    path: Path,
    max_skill_file_bytes: int,
    reporter: DiagnosticReporter | None = None,
) -> Skill | None:
    try:
        with path.open("rb") as file:
            first = file.readline(max_skill_file_bytes + 1)
            bytes_read = len(first)
            if bytes_read > max_skill_file_bytes:
                _report(
                    reporter,
                    SkillDiagnosticCode.FRONTMATTER_TOO_LARGE,
                    path,
                )
                return None
            if first.strip() != b"---":
                _report(
                    reporter,
                    SkillDiagnosticCode.MISSING_FRONTMATTER,
                    path,
                )
                return None

            frontmatter_lines: list[bytes] = []
            while bytes_read <= max_skill_file_bytes:
                line = file.readline(max_skill_file_bytes - bytes_read + 1)
                if not line:
                    _report(
                        reporter,
                        SkillDiagnosticCode.MISSING_CLOSING_FRONTMATTER,
                        path,
                    )
                    return None
                bytes_read += len(line)
                if bytes_read > max_skill_file_bytes:
                    _report(
                        reporter,
                        SkillDiagnosticCode.FRONTMATTER_TOO_LARGE,
                        path,
                    )
                    return None
                if line.strip() == b"---":
                    break
                frontmatter_lines.append(line)
    except OSError:
        _report(reporter, SkillDiagnosticCode.UNREADABLE_SKILL, path)
        return None

    try:
        frontmatter = b"".join(frontmatter_lines).decode("utf-8")
    except UnicodeDecodeError:
        _report(reporter, SkillDiagnosticCode.UNREADABLE_SKILL, path)
        return None

    try:
        value = _load_frontmatter(frontmatter)
        metadata = SkillMetadata.model_validate(value)
    except (ValueError, yaml.YAMLError, ValidationError):
        _report(reporter, SkillDiagnosticCode.INVALID_METADATA, path)
        return None

    name = metadata.name.strip()
    description = metadata.description.strip()
    if not name or not description:
        _report(reporter, SkillDiagnosticCode.EMPTY_METADATA, path)
        return None
    metadata = metadata.model_copy(
        update={"name": name, "description": description}
    )
    if name != path.parent.name:
        _report(
            reporter,
            SkillDiagnosticCode.NAME_DIRECTORY_MISMATCH,
            path,
            skill_name=name,
        )
    if len(name) > 64:
        _report(
            reporter,
            SkillDiagnosticCode.INVALID_NAME,
            path,
            skill_name=name,
        )
        return None
    if _VALID_SKILL_NAME.fullmatch(name) is None:
        _report(
            reporter,
            SkillDiagnosticCode.INVALID_NAME,
            path,
            skill_name=name,
        )
    if len(description) > 1024:
        _report(
            reporter,
            SkillDiagnosticCode.DESCRIPTION_TOO_LONG,
            path,
            skill_name=name,
        )
        return None
    return Skill(metadata, path)


def _load_frontmatter(frontmatter: str) -> object:
    try:
        return cast(object, yaml.safe_load(frontmatter))
    except yaml.YAMLError as original_error:
        repaired: list[str] = []
        for line in frontmatter.splitlines():
            match = re.fullmatch(
                r"(description|license|compatibility|allowed-tools):\s*(.+)",
                line,
            )
            if match and ": " in match.group(2):
                line = f"{match.group(1)}: {json.dumps(match.group(2))}"
            repaired.append(line)
        try:
            return cast(object, yaml.safe_load("\n".join(repaired)))
        except yaml.YAMLError:
            raise original_error from None


def _catalogue(skills: tuple[Skill, ...]) -> str:
    entries = "\n".join(
        "  <skill>\n"
        f"    <name>{html.escape(skill.metadata.name)}</name>\n"
        "    <description>"
        f"{html.escape(skill.metadata.description)}"
        "</description>\n"
        "  </skill>"
        for skill in skills
    )
    return (
        "The following skills provide specialised instructions for specific "
        "tasks. When a task matches a skill's description, call "
        "activate_skill with its name before proceeding. Use "
        "read_skill_resource_file only when the activated instructions "
        "reference a bundled resource.\n\n"
        f"<available_skills>\n{entries}\n</available_skills>"
    )


class _ActivateSkillArguments(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)


class _ReadSkillResourceFileArguments(_ActivateSkillArguments):
    path: str = Field(min_length=1)


class _ActivateSkillTool:
    definition: ToolDefinition
    effect: ToolEffect = ToolEffect.READ
    arguments_type: type[BaseModel] = _ActivateSkillArguments

    def __init__(
        self,
        skills: dict[str, Skill],
        max_skill_file_bytes: int,
        max_resources: int,
    ) -> None:
        self._skills = skills
        self._max_skill_file_bytes = max_skill_file_bytes
        self._max_resources = max_resources
        self.definition = ToolDefinition(
            name="activate_skill",
            description=(
                "Load the complete instructions for one available skill."
            ),
            parameters_schema=_skill_arguments_schema(
                _ActivateSkillArguments,
                tuple(skills),
            ),
        )

    async def execute(self, arguments: BaseModel) -> str:
        if not isinstance(arguments, _ActivateSkillArguments):
            raise TypeError("invalid activate_skill arguments")
        skill = self._skills.get(arguments.name)
        if skill is None:
            raise ToolExecutionError("unknown skill")
        return await asyncio.to_thread(
            _skill_content,
            skill,
            self._max_skill_file_bytes,
            self._max_resources,
        )


class _ReadSkillResourceFileTool:
    definition: ToolDefinition
    effect: ToolEffect = ToolEffect.READ
    arguments_type: type[BaseModel] = _ReadSkillResourceFileArguments

    def __init__(
        self,
        skills: dict[str, Skill],
        max_resource_file_bytes: int,
    ) -> None:
        self._skills = skills
        self._max_resource_file_bytes = max_resource_file_bytes
        self.definition = ToolDefinition(
            name="read_skill_resource_file",
            description=(
                "Read one UTF-8 resource referenced by an activated skill."
            ),
            parameters_schema=_skill_arguments_schema(
                _ReadSkillResourceFileArguments,
                tuple(skills),
            ),
        )

    async def execute(self, arguments: BaseModel) -> str:
        if not isinstance(arguments, _ReadSkillResourceFileArguments):
            raise TypeError("invalid read_skill_resource_file arguments")
        skill = self._skills.get(arguments.name)
        if skill is None:
            raise ToolExecutionError("unknown skill")
        return await asyncio.to_thread(
            _read_skill_resource_file,
            skill,
            arguments.path,
            self._max_resource_file_bytes,
        )


def _skill_arguments_schema(
    arguments_type: type[BaseModel],
    names: tuple[str, ...],
) -> dict[str, object]:
    """Constrain the model-visible name argument to discovered skills."""
    schema = arguments_type.model_json_schema()
    properties = cast(dict[str, object], schema["properties"])
    name = cast(dict[str, object], properties["name"])
    name["enum"] = list(names)
    return schema


def _skill_content(
    skill: Skill,
    max_skill_file_bytes: int,
    max_resources: int,
) -> str:
    try:
        with skill.location.open("rb") as file:
            content = file.read(max_skill_file_bytes + 1)
    except OSError as error:
        raise ToolExecutionError("skill is no longer readable") from error
    if len(content) > max_skill_file_bytes:
        raise ToolExecutionError("skill file exceeds size limit")
    try:
        contents = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ToolExecutionError("skill is not UTF-8 text") from error

    lines = contents.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ToolExecutionError("skill is no longer readable")
    try:
        end = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        )
    except StopIteration:
        raise ToolExecutionError("skill is no longer readable") from None
    body = "\n".join(lines[end + 1 :]).strip()
    resources = _get_skill_resources(skill, max_resources)
    resource_block = ""
    if resources:
        files = "\n".join(
            f"    <file>{html.escape(path)}</file>" for path in resources
        )
        resource_block = f"\n  <skill_resources>\n{files}\n  </skill_resources>"
    return (
        '<skill_content name="'
        f'{html.escape(skill.metadata.name, quote=True)}">\n'
        f"{body}\n\n"
        f"Skill directory: {html.escape(str(skill.directory))}\n"
        "Relative paths in this skill are relative to the skill directory."
        f"{resource_block}\n"
        "</skill_content>"
    )


def _get_skill_resources(skill: Skill, max_resources: int) -> tuple[str, ...]:
    root = skill.directory.resolve()
    resources: list[str] = []
    for path in root.rglob("*"):
        if path == skill.location or not path.is_file():
            continue
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_relative_to(root):
            resources.append(path.relative_to(root).as_posix())
        if len(resources) == max_resources:
            break
    return tuple(sorted(resources))


def _read_skill_resource_file(
    skill: Skill,
    requested_path: str,
    max_resource_file_bytes: int,
) -> str:
    relative_path = Path(requested_path)
    if relative_path.is_absolute():
        raise ToolExecutionError("skill file path must be relative")
    root = skill.directory.resolve(strict=True)
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root):
        raise ToolExecutionError("skill file path must be inside the skill")
    if not path.is_file():
        raise ToolExecutionError("skill file does not exist")
    with path.open("rb") as file:
        content = file.read(max_resource_file_bytes + 1)
    if len(content) > max_resource_file_bytes:
        raise ToolExecutionError("skill file exceeds size limit")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ToolExecutionError("skill file is not UTF-8 text") from error


class SkillsCapability:
    """Discover, disclose, and activate user and project Agent Skills."""

    def __init__(
        self,
        user_skills_root: Path,
        shared_user_skills_root: Path | None = None,
        *,
        events: EnvelopeEventEmitter,
        max_skill_file_bytes: int = 100 * 1024,
        max_skills: int = 200,
        max_resource_file_bytes: int = 100 * 1024,
        max_resources: int = 200,
    ) -> None:
        if (
            min(
                max_skill_file_bytes,
                max_skills,
                max_resource_file_bytes,
                max_resources,
            )
            < 1
        ):
            raise ValueError("skill limits must be positive")
        self._user_skills_root = user_skills_root
        self._shared_user_skills_root = shared_user_skills_root
        self._events = events
        self._max_skill_file_bytes = max_skill_file_bytes
        self._max_skills = max_skills
        self._max_resource_file_bytes = max_resource_file_bytes
        self._max_resources = max_resources
        self._resolved: dict[RunContext, tuple[Skill, ...]] = {}

    async def instructions(self, context: RunContext) -> tuple[str, ...]:
        diagnostics: list[SkillDiagnosticEventPayload] = []
        skills = await asyncio.to_thread(
            self._discover,
            context,
            diagnostics.append,
        )
        for diagnostic in diagnostics:
            await self._events.emit(
                event_factory(
                    EventType.SKILL_DIAGNOSTIC,
                    location="skills",
                    payload=diagnostic,
                )
            )
        self._resolved[context] = skills
        return (_catalogue(skills),) if skills else ()

    async def tools(self, context: RunContext) -> tuple[Tool, ...]:
        skills = self._resolved.pop(context, None)
        if skills is None:
            skills = await asyncio.to_thread(self._discover, context)
        if not skills:
            return ()
        skills_by_name = {skill.metadata.name: skill for skill in skills}
        return (
            _ActivateSkillTool(
                skills_by_name,
                self._max_skill_file_bytes,
                self._max_resources,
            ),
            _ReadSkillResourceFileTool(
                skills_by_name,
                self._max_resource_file_bytes,
            ),
        )

    def _discover(
        self,
        context: RunContext,
        reporter: DiagnosticReporter | None = None,
    ) -> tuple[Skill, ...]:
        roots = (
            *(
                (self._shared_user_skills_root,)
                if self._shared_user_skills_root
                else ()
            ),
            self._user_skills_root,
            context.workspace_path / ".agents" / "skills",
            context.workspace_path / ".ethos" / "skills",
        )
        return discover_skills(
            *roots,
            reporter=reporter,
            max_skill_file_bytes=self._max_skill_file_bytes,
            max_skills=self._max_skills,
        )
