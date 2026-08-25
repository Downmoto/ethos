import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from pydantic import BaseModel

from ethos.capabilities import RunContext
from ethos.capabilities.filesystem import ReadOnlyFilesystemCapability
from ethos.events.emitters import EnvelopeEventEmitter
from ethos.models import (
    FinishReason,
    ModelFeatures,
    ModelResponse,
    Role,
    TextPart,
    ToolCallPart,
    ToolDefinition,
)
from ethos.runtime import AgentRuntime
from ethos.sessions import SessionManager
from ethos.tools import (
    PreparedToolCall,
    Tool,
    ToolEffect,
    ToolExecutor,
    ToolRegistry,
)
from ethos.workspaces import WorkspaceManager
from fakes import FakeModel


class _Arguments(BaseModel):
    value: str


@dataclass
class _Tool:
    name: str
    definition: ToolDefinition = field(init=False)
    effect: ToolEffect = ToolEffect.READ
    arguments_type: type[BaseModel] = _Arguments

    def __post_init__(self) -> None:
        self.definition = ToolDefinition(
            name=self.name,
            description=f"Run {self.name}",
            parameters_schema=_Arguments.model_json_schema(),
        )

    async def execute(self, arguments: BaseModel) -> str:
        assert isinstance(arguments, _Arguments)
        return arguments.value


@dataclass
class _Capability:
    name: str
    contexts: list[RunContext] = field(default_factory=list)

    async def instructions(self, context: RunContext) -> tuple[str, ...]:
        self.contexts.append(context)
        return (f"{self.name} instruction",)

    async def tools(self, context: RunContext) -> tuple[Tool, ...]:
        assert self.contexts[-1] == context
        return (_Tool(self.name),)


def _response() -> ModelResponse:
    return ModelResponse(
        parts=(TextPart(text="done"),),
        finish_reason=FinishReason.STOP,
    )


def _runtime(
    tmp_path: Path,
    model: FakeModel,
    *capabilities: _Capability,
    base_tools: tuple[Tool, ...] = (),
) -> tuple[AgentRuntime, SessionManager, str]:
    workspaces = WorkspaceManager(tmp_path / "workspaces")
    workspace = workspaces.create("project")
    sessions = SessionManager(workspaces, tmp_path / "sessions")
    session = sessions.create(workspace.name)
    runtime = AgentRuntime(
        sessions,
        lambda: model,
        ToolRegistry(base_tools),
        capabilities=capabilities,
        events=EnvelopeEventEmitter(),
    )
    return runtime, sessions, str(session.id)


async def _collect(
    runtime: AgentRuntime,
    workspace: str,
    session_id: str,
) -> None:
    async for _event in runtime.run("hello", workspace, session_id):
        pass


def test_runtime_composes_capabilities_in_registration_order(
    tmp_path: Path,
) -> None:
    model = FakeModel(
        (_response(),),
        stream_chunks=(("done",),),
        features=ModelFeatures(tools=True),
    )
    first = _Capability("first")
    second = _Capability("second")
    runtime, _sessions, session_id = _runtime(
        tmp_path,
        model,
        first,
        second,
        base_tools=(_Tool("base"),),
    )

    asyncio.run(_collect(runtime, "project", session_id))

    request = model.requests[0]
    assert [tool.name for tool in request.tools] == ["base", "first", "second"]
    expected_context = RunContext(
        workspace_name="project",
        workspace_path=tmp_path / "workspaces" / "project",
        session_id=session_id,
    )
    context_instruction = "Run context: " + json.dumps(
        {
            "workspace_name": "project",
            "workspace_path": str(tmp_path / "workspaces" / "project"),
            "session_id": session_id,
        },
        sort_keys=True,
    )
    assert [
        message.parts[0].text
        for message in request.messages
        if message.role is Role.SYSTEM
        and isinstance(message.parts[0], TextPart)
    ][-3:] == [
        context_instruction,
        "first instruction",
        "second instruction",
    ]
    assert first.contexts == [expected_context]
    assert second.contexts == first.contexts


def test_capability_context_is_resolved_per_session(tmp_path: Path) -> None:
    workspaces = WorkspaceManager(tmp_path / "workspaces")
    workspaces.create("first")
    workspaces.create("second")
    sessions = SessionManager(workspaces, tmp_path / "sessions")
    first_session = sessions.create("first")
    second_session = sessions.create("second")
    capability = _Capability("lookup")
    model = FakeModel(
        (_response(), _response()),
        stream_chunks=(("done",), ("done",)),
        features=ModelFeatures(tools=True),
    )
    runtime = AgentRuntime(
        sessions,
        lambda: model,
        capabilities=(capability,),
        events=EnvelopeEventEmitter(),
    )

    asyncio.run(_collect(runtime, "first", str(first_session.id)))
    asyncio.run(_collect(runtime, "second", str(second_session.id)))

    assert capability.contexts == [
        RunContext(
            "first",
            tmp_path / "workspaces" / "first",
            str(first_session.id),
        ),
        RunContext(
            "second",
            tmp_path / "workspaces" / "second",
            str(second_session.id),
        ),
    ]


def test_duplicate_capability_tool_fails_before_model_request(
    tmp_path: Path,
) -> None:
    model = FakeModel((_response(),), stream_chunks=(("done",),))
    runtime, _sessions, session_id = _runtime(
        tmp_path,
        model,
        _Capability("duplicate"),
        _Capability("duplicate"),
    )

    with pytest.raises(ValueError, match="already registered: duplicate"):
        asyncio.run(_collect(runtime, "project", session_id))

    assert model.requests == []


def test_capability_failure_occurs_before_model_request(tmp_path: Path) -> None:
    class FailingCapability(_Capability):
        async def instructions(self, context: RunContext) -> tuple[str, ...]:
            del context
            raise RuntimeError("failed")

    model = FakeModel((_response(),), stream_chunks=(("done",),))
    runtime, _sessions, session_id = _runtime(
        tmp_path,
        model,
        FailingCapability("failure"),
    )

    with pytest.raises(RuntimeError, match="failed"):
        asyncio.run(_collect(runtime, "project", session_id))

    assert model.requests == []


def test_read_file_is_bounded_to_utf8_workspace_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "inside.txt").write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (workspace / "escape.txt").symlink_to(outside)
    context = RunContext("project", workspace, "session")
    tool = asyncio.run(ReadOnlyFilesystemCapability().tools(context))[0]
    executor = ToolExecutor(ToolRegistry((tool,)))

    async def read(path: str) -> str:
        prepared = await executor.prepare(
            ToolCallPart(
                call_id="call-1",
                name="read_file",
                arguments_json=f'{{"path": "{path}"}}',
            )
        )
        assert isinstance(prepared, PreparedToolCall)
        return (await executor.run(prepared)).content

    assert asyncio.run(read("inside.txt")) == "inside"
    assert asyncio.run(read("../outside.txt")) == (
        "tool path must be inside the workspace"
    )
    assert asyncio.run(read(str(outside))) == (
        "tool path must be relative to the workspace root"
    )
    assert asyncio.run(read("escape.txt")) == (
        "tool path must be inside the workspace"
    )


def test_read_file_rejects_oversized_content(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "large.txt").write_bytes(b"xxxx")
    context = RunContext("project", workspace, "session")
    tool = asyncio.run(
        ReadOnlyFilesystemCapability(max_read_file_bytes=3).tools(context)
    )[0]
    executor = ToolExecutor(ToolRegistry((tool,)))

    async def read() -> str:
        prepared = await executor.prepare(
            ToolCallPart(
                call_id="call-1",
                name="read_file",
                arguments_json='{"path": "large.txt"}',
            )
        )
        assert isinstance(prepared, PreparedToolCall)
        return (await executor.run(prepared)).content

    assert asyncio.run(read()) == "read_file exceeds size limit"


def test_list_files_returns_bounded_workspace_relative_paths(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "b.txt").write_text("b", encoding="utf-8")
    (workspace / "a.txt").write_text("a", encoding="utf-8")
    directory = workspace / "nested"
    directory.mkdir()
    (directory / "child.txt").write_text("child", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "escape").symlink_to(outside)
    context = RunContext("project", workspace, "session")
    tools = asyncio.run(ReadOnlyFilesystemCapability().tools(context))
    assert [tool.definition.name for tool in tools] == [
        "read_file",
        "list_files",
    ]
    tool = next(tool for tool in tools if tool.definition.name == "list_files")
    assert 'Use \\".\\" for the workspace root' in json.dumps(
        tool.definition.parameters_schema
    )
    executor = ToolExecutor(ToolRegistry((tool,)))

    async def list_files(path: str) -> str:
        prepared = await executor.prepare(
            ToolCallPart(
                call_id="call-1",
                name="list_files",
                arguments_json=json.dumps({"path": path}),
            )
        )
        assert isinstance(prepared, PreparedToolCall)
        return (await executor.run(prepared)).content

    assert json.loads(asyncio.run(list_files("."))) == [
        "a.txt",
        "b.txt",
        "escape",
        "nested/",
    ]
    assert json.loads(asyncio.run(list_files("nested"))) == ["nested/child.txt"]
    assert asyncio.run(list_files("../outside")) == (
        "tool path must be inside the workspace"
    )
    assert asyncio.run(list_files("escape")) == (
        "tool path must be inside the workspace"
    )


def test_list_files_rejects_oversized_directories(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for name in ("one", "two", "three"):
        (workspace / name).touch()
    context = RunContext("project", workspace, "session")
    tools = asyncio.run(
        ReadOnlyFilesystemCapability(max_list_file_entries=2).tools(context)
    )
    tool = next(tool for tool in tools if tool.definition.name == "list_files")
    executor = ToolExecutor(ToolRegistry((tool,)))

    async def list_files() -> str:
        prepared = await executor.prepare(
            ToolCallPart(
                call_id="call-1",
                name="list_files",
                arguments_json="{}",
            )
        )
        assert isinstance(prepared, PreparedToolCall)
        return (await executor.run(prepared)).content

    assert asyncio.run(list_files()) == "list_files exceeds entry limit"
