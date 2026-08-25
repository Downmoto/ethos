import asyncio
from pathlib import Path

import pytest

from ethos.capabilities import RunContext
from ethos.capabilities.skills import (
    SkillDiagnosticCode,
    SkillDiagnosticEventPayload,
    SkillsCapability,
    discover_skills,
)
from ethos.events.emitters import EnvelopeEventEmitter
from ethos.events.listeners import EventListenerRegistry
from ethos.events.models import EventEnvelope
from ethos.events.types import EventType
from ethos.models import (
    FinishReason,
    ModelFeatures,
    ModelResponse,
    Role,
    TextPart,
    ToolCallPart,
    ToolResultPart,
)
from ethos.runtime import AgentRuntime
from ethos.sessions import SessionManager
from ethos.tools import ToolExecutionError
from ethos.workspaces import WorkspaceManager
from fakes import FakeModel


def _write_skill(
    root: Path,
    directory: str,
    name: str | None = None,
    instructions: str = "Follow the skill.",
    *,
    description: str | None = "A test skill",
) -> Path:
    path = root / directory
    path.mkdir(parents=True)
    skill_file = path / "SKILL.md"
    frontmatter = ["---"]
    if name is not None:
        frontmatter.append(f"name: {name}")
    if description is not None:
        frontmatter.append(f"description: {description}")
    frontmatter.extend(("---", instructions))
    skill_file.write_text("\n".join(frontmatter) + "\n", encoding="utf-8")
    return skill_file


def _context(tmp_path: Path) -> RunContext:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return RunContext("default", workspace, "session")


def _call(name: str, arguments: str, call_id: str) -> ModelResponse:
    return ModelResponse(
        parts=(
            ToolCallPart(
                call_id=call_id,
                name=name,
                arguments_json=arguments,
            ),
        ),
        finish_reason=FinishReason.TOOL_CALL,
    )


def test_discovers_metadata_without_loading_instructions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "skills"
    skill_file = _write_skill(
        root,
        "review",
        "review",
        "Secret instructions",
        description="Review code",
    )

    skills = discover_skills(root)

    assert len(skills) == 1
    assert skills[0].metadata.name == "review"
    assert skills[0].metadata.description == "Review code"
    assert skills[0].location == skill_file.resolve()
    assert not hasattr(skills[0], "instructions")


def test_discovery_is_lenient_and_skips_unusable_skills(
    tmp_path: Path,
) -> None:
    root = tmp_path / "skills"
    _write_skill(
        root,
        "different-directory",
        "Review Skill",
        description="Use this skill when: reviewing code",
    )
    _write_skill(root, "missing-description", "missing", description=None)
    _write_skill(
        root,
        "long-description",
        "long-description",
        description="x" * 1025,
    )
    malformed = root / "malformed"
    malformed.mkdir(parents=True)
    (malformed / "SKILL.md").write_text("not frontmatter", encoding="utf-8")

    diagnostics: list[SkillDiagnosticEventPayload] = []
    skills = discover_skills(root, reporter=diagnostics.append)

    assert [skill.metadata.name for skill in skills] == ["Review Skill"]
    assert skills[0].metadata.description == (
        "Use this skill when: reviewing code"
    )
    assert {diagnostic.code for diagnostic in diagnostics} == {
        SkillDiagnosticCode.NAME_DIRECTORY_MISMATCH,
        SkillDiagnosticCode.INVALID_NAME,
        SkillDiagnosticCode.DESCRIPTION_TOO_LONG,
        SkillDiagnosticCode.INVALID_METADATA,
        SkillDiagnosticCode.MISSING_FRONTMATTER,
    }


def test_later_skill_roots_override_earlier_roots(
    tmp_path: Path,
) -> None:
    user = tmp_path / "user"
    project = tmp_path / "project"
    _write_skill(user, "review", "review", description="User version")
    project_file = _write_skill(
        project,
        "review",
        "review",
        description="Project version",
    )

    diagnostics: list[SkillDiagnosticEventPayload] = []
    skills = discover_skills(user, project, reporter=diagnostics.append)

    assert len(skills) == 1
    assert skills[0].metadata.description == "Project version"
    assert skills[0].location == project_file.resolve()
    assert diagnostics[0].code is SkillDiagnosticCode.SHADOWED
    assert diagnostics[0].path == str(user / "review" / "SKILL.md")
    assert diagnostics[0].related_path == str(project_file)


def test_discovery_rejects_frontmatter_over_its_byte_limit(
    tmp_path: Path,
) -> None:
    root = tmp_path / "skills"
    skill_file = _write_skill(
        root,
        "review",
        "review",
        description="A description that exceeds the configured prefix",
    )
    diagnostics: list[SkillDiagnosticEventPayload] = []

    skills = discover_skills(
        root,
        reporter=diagnostics.append,
        max_skill_file_bytes=32,
    )

    assert skills == ()
    assert diagnostics == [
        SkillDiagnosticEventPayload(
            code=SkillDiagnosticCode.FRONTMATTER_TOO_LARGE,
            path=str(skill_file.resolve()),
        )
    ]


def test_discovery_bounds_skill_count_and_prefers_project_skills(
    tmp_path: Path,
) -> None:
    user = tmp_path / "user"
    project = tmp_path / "project"
    _write_skill(user, "alpha", "alpha", description="User skill")
    project_file = _write_skill(
        project,
        "beta",
        "beta",
        description="Project skill",
    )
    diagnostics: list[SkillDiagnosticEventPayload] = []

    skills = discover_skills(
        user,
        project,
        reporter=diagnostics.append,
        max_skills=1,
    )

    assert [skill.location for skill in skills] == [project_file.resolve()]
    assert diagnostics == [
        SkillDiagnosticEventPayload(
            code=SkillDiagnosticCode.SKILL_LIMIT_REACHED,
            path=str(user / "alpha" / "SKILL.md"),
        )
    ]


def test_capability_emits_discovery_diagnostics_as_ethos_events(
    tmp_path: Path,
) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "wrong-directory", "review")
    listeners = EventListenerRegistry()
    emitted: list[EventEnvelope] = []

    async def record(event: EventEnvelope) -> None:
        emitted.append(event)

    listeners.register(record, event_type=[EventType.SKILL_DIAGNOSTIC])
    capability = SkillsCapability(
        root,
        events=EnvelopeEventEmitter(dispatcher=listeners),
    )

    asyncio.run(capability.instructions(_context(tmp_path)))

    assert [event.type for event in emitted] == [EventType.SKILL_DIAGNOSTIC]
    assert emitted[0].source.name == "skills"
    assert emitted[0].payload == SkillDiagnosticEventPayload(
        code=SkillDiagnosticCode.NAME_DIRECTORY_MISMATCH,
        path=str(root / "wrong-directory" / "SKILL.md"),
        skill_name="review",
    )


def test_capability_discloses_catalogue_and_constrained_tools(
    tmp_path: Path,
) -> None:
    user = tmp_path / "user-skills"
    _write_skill(user, "zebra", "zebra", "Zebra body")
    _write_skill(user, "alpha", "alpha", "Alpha body")
    capability = SkillsCapability(user, events=EnvelopeEventEmitter())
    context = _context(tmp_path)

    instructions = asyncio.run(capability.instructions(context))
    tools = asyncio.run(capability.tools(context))

    assert len(instructions) == 1
    assert instructions[0].index("alpha") < instructions[0].index("zebra")
    assert "Alpha body" not in instructions[0]
    assert "Zebra body" not in instructions[0]
    assert [tool.definition.name for tool in tools] == [
        "activate_skill",
        "read_skill_resource_file",
    ]
    name_schema = tools[0].definition.parameters_schema["properties"]
    assert isinstance(name_schema, dict)
    assert isinstance(name_schema["name"], dict)
    assert name_schema["name"]["enum"] == ["alpha", "zebra"]


def test_capability_omits_catalogue_and_tools_when_empty(
    tmp_path: Path,
) -> None:
    capability = SkillsCapability(
        tmp_path / "missing", events=EnvelopeEventEmitter()
    )
    context = _context(tmp_path)

    assert asyncio.run(capability.instructions(context)) == ()
    assert asyncio.run(capability.tools(context)) == ()


def test_skill_resource_cannot_escape_its_directory(tmp_path: Path) -> None:
    user = tmp_path / "skills"
    _write_skill(user, "review", "review")
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    capability = SkillsCapability(user, events=EnvelopeEventEmitter())
    context = _context(tmp_path)
    asyncio.run(capability.instructions(context))
    resource_tool = asyncio.run(capability.tools(context))[1]
    arguments = resource_tool.arguments_type.model_validate(
        {"name": "review", "path": "../../outside.txt"}
    )

    with pytest.raises(ToolExecutionError, match="inside the skill"):
        asyncio.run(resource_tool.execute(arguments))


def test_configured_skill_resource_limits_are_enforced(tmp_path: Path) -> None:
    user = tmp_path / "skills"
    skill_file = _write_skill(user, "review", "review")
    (skill_file.parent / "first.txt").write_text("first", encoding="utf-8")
    (skill_file.parent / "second.txt").write_text("second", encoding="utf-8")
    capability = SkillsCapability(
        user,
        events=EnvelopeEventEmitter(),
        max_resource_file_bytes=4,
        max_resources=1,
    )
    context = _context(tmp_path)
    asyncio.run(capability.instructions(context))
    activation_tool, resource_tool = asyncio.run(capability.tools(context))
    activation = activation_tool.arguments_type.model_validate(
        {"name": "review"}
    )
    resource = resource_tool.arguments_type.model_validate(
        {"name": "review", "path": "first.txt"}
    )

    content = asyncio.run(activation_tool.execute(activation))

    assert content.count("<file>") == 1
    with pytest.raises(ToolExecutionError, match="exceeds size limit"):
        asyncio.run(resource_tool.execute(resource))


def test_skill_resource_limit_counts_only_valid_files(tmp_path: Path) -> None:
    user = tmp_path / "skills"
    skill_file = _write_skill(user, "review", "review")
    (skill_file.parent / "broken.txt").symlink_to("missing.txt")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (skill_file.parent / "outside.txt").symlink_to(outside)
    resource = skill_file.parent / "first" / "second" / "third" / "guide.md"
    resource.parent.mkdir(parents=True)
    resource.write_text("guide", encoding="utf-8")
    capability = SkillsCapability(
        user,
        events=EnvelopeEventEmitter(),
        max_resources=1,
    )
    context = _context(tmp_path)
    asyncio.run(capability.instructions(context))
    activation_tool = asyncio.run(capability.tools(context))[0]
    activation = activation_tool.arguments_type.model_validate(
        {"name": "review"}
    )

    content = asyncio.run(activation_tool.execute(activation))

    assert content.count("<file>") == 1
    assert "<file>first/second/third/guide.md</file>" in content
    assert "broken.txt" not in content
    assert "outside.txt" not in content


def test_skill_activation_rejects_an_oversized_instruction_body(
    tmp_path: Path,
) -> None:
    user = tmp_path / "skills"
    _write_skill(user, "review", "review", instructions="x" * 200)
    capability = SkillsCapability(
        user,
        events=EnvelopeEventEmitter(),
        max_skill_file_bytes=64,
    )
    context = _context(tmp_path)

    assert asyncio.run(capability.instructions(context))
    activation_tool = asyncio.run(capability.tools(context))[0]
    activation = activation_tool.arguments_type.model_validate(
        {"name": "review"}
    )

    with pytest.raises(
        ToolExecutionError,
        match="^skill file exceeds size limit$",
    ):
        asyncio.run(activation_tool.execute(activation))


def test_project_native_skill_has_highest_precedence(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    user = tmp_path / "user"
    context = _context(tmp_path)
    project_shared = context.workspace_path / ".agents" / "skills"
    project_native = context.workspace_path / ".ethos" / "skills"
    for root, description in (
        (shared, "shared user"),
        (user, "native user"),
        (project_shared, "shared project"),
        (project_native, "native project"),
    ):
        _write_skill(root, "review", "review", description=description)
    capability = SkillsCapability(
        user,
        shared,
        events=EnvelopeEventEmitter(),
    )

    catalogue = asyncio.run(capability.instructions(context))[0]

    assert "native project" in catalogue
    assert "shared user" not in catalogue
    assert "native user" not in catalogue
    assert "shared project" not in catalogue


def test_model_activates_skill_and_reads_resource_on_demand(
    tmp_path: Path,
) -> None:
    user = tmp_path / "skills"
    skill_file = _write_skill(
        user,
        "review",
        "review",
        "Review carefully.",
        description="Review code",
    )
    references = skill_file.parent / "references"
    references.mkdir()
    (references / "checklist.md").write_text(
        "Check correctness.", encoding="utf-8"
    )
    workspaces = WorkspaceManager(tmp_path / "workspaces")
    workspace = workspaces.ensure_default()
    sessions = SessionManager(workspaces, tmp_path / "sessions")
    session = sessions.create(workspace.name)
    model = FakeModel(
        (
            _call("activate_skill", '{"name":"review"}', "activate"),
            _call(
                "read_skill_resource_file",
                '{"name":"review","path":"references/checklist.md"}',
                "resource",
            ),
            ModelResponse(
                parts=(TextPart(text="done"),),
                finish_reason=FinishReason.STOP,
            ),
        ),
        stream_chunks=((), (), ("done",)),
        features=ModelFeatures(tools=True),
    )
    runtime = AgentRuntime(
        sessions,
        lambda: model,
        capabilities=(SkillsCapability(user, events=EnvelopeEventEmitter()),),
        events=EnvelopeEventEmitter(),
    )

    async def run() -> None:
        async for _event in runtime.run(
            "Review this", workspace.name, str(session.id)
        ):
            pass

    asyncio.run(run())

    first_request = model.requests[0]
    catalogue = first_request.messages[-2].parts[0]
    assert isinstance(catalogue, TextPart)
    assert "Review code" in catalogue.text
    assert "Review carefully." not in catalogue.text
    activation = model.requests[1].messages[-1].parts[0]
    assert isinstance(activation, ToolResultPart)
    assert '<skill_content name="review">' in activation.content
    assert "Review carefully." in activation.content
    assert "references/checklist.md" in activation.content
    resource = model.requests[2].messages[-1].parts[0]
    assert isinstance(resource, ToolResultPart)
    assert resource.content == "Check correctness."
    stored = sessions.get(workspace.name, str(session.id)).messages
    assert [message.role for message in stored] == [
        Role.USER,
        Role.ASSISTANT,
        Role.TOOL,
        Role.ASSISTANT,
        Role.TOOL,
        Role.ASSISTANT,
    ]
