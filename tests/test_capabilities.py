import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from pydantic import BaseModel

from ethos.capabilities import Capability, RunContext
from ethos.capabilities.filesystem import FilesystemCapability
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
from ethos.runtime import AgentRuntime, ApprovalStreamEvent, PromptStreamEvent
from ethos.sessions import SessionManager
from ethos.tools import (
    PreparedToolCall,
    RequireApproval,
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


def _filesystem_tool(
    workspace: Path,
    name: str,
    **limits: int,
) -> Tool:
    context = RunContext("project", workspace, "session")
    tools = asyncio.run(FilesystemCapability(**limits).tools(context))
    return next(tool for tool in tools if tool.definition.name == name)


def _execute_tool(tool: Tool, arguments: dict[str, object]) -> str:
    async def execute() -> str:
        executor = ToolExecutor(ToolRegistry((tool,)))
        prepared = await executor.prepare(
            ToolCallPart(
                call_id="call-1",
                name=tool.definition.name,
                arguments_json=json.dumps(arguments),
            )
        )
        assert isinstance(prepared, PreparedToolCall)
        return (await executor.run(prepared)).content

    return asyncio.run(execute())


def _runtime(
    tmp_path: Path,
    model: FakeModel,
    *capabilities: Capability,
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


def test_runtime_resolves_managed_capabilities_for_each_turn(
    tmp_path: Path,
) -> None:
    workspaces = WorkspaceManager(tmp_path / "workspaces")
    workspaces.create("project")
    sessions = SessionManager(workspaces, tmp_path / "sessions")
    session = sessions.create("project")
    model = FakeModel(
        (_response(), _response()),
        stream_chunks=(("done",), ("done",)),
        features=ModelFeatures(tools=True),
    )
    names = iter(("first", "second"))

    def resolve(_context: RunContext) -> tuple[_Capability, ...]:
        return (_Capability(next(names)),)

    runtime = AgentRuntime(
        sessions,
        lambda: model,
        capability_resolver=resolve,
        events=EnvelopeEventEmitter(),
    )

    asyncio.run(_collect(runtime, "project", str(session.id)))
    asyncio.run(_collect(runtime, "project", str(session.id)))

    assert [
        [tool.name for tool in request.tools] for request in model.requests
    ] == [
        ["first"],
        ["second"],
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


def test_filesystem_write_resumes_through_durable_runtime_approval(
    tmp_path: Path,
) -> None:
    call = ToolCallPart(
        call_id="call-write",
        name="write_file",
        arguments_json='{"path":"approved.txt","content":"approved"}',
    )
    model = FakeModel(
        (
            ModelResponse(
                parts=(call,),
                finish_reason=FinishReason.TOOL_CALL,
            ),
            _response(),
        ),
        stream_chunks=((), ("done",)),
        features=ModelFeatures(tools=True),
    )
    runtime, sessions, session_id = _runtime(
        tmp_path,
        model,
        FilesystemCapability(),
    )

    async def collect_events(
        events: AsyncIterator[PromptStreamEvent | ApprovalStreamEvent],
    ) -> list[PromptStreamEvent | ApprovalStreamEvent]:
        return [event async for event in events]

    pending = asyncio.run(
        collect_events(runtime.run("hello", "project", session_id))
    )
    assert len(pending) == 1
    event = pending[0]
    assert isinstance(event, ApprovalStreamEvent)
    workspace = tmp_path / "workspaces" / "project"
    assert not (workspace / "approved.txt").exists()
    assert sessions.get("project", session_id).approvals == (event.approval,)

    completed = asyncio.run(
        collect_events(
            runtime.resolve_approval(
                "project",
                session_id,
                event.approval.id,
                approved=True,
            )
        )
    )

    final = completed[-1]
    assert isinstance(final, PromptStreamEvent)
    assert final.done
    assert (workspace / "approved.txt").read_text(encoding="utf-8") == (
        "approved"
    )


def test_read_file_is_bounded_to_utf8_workspace_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "inside.txt").write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (workspace / "escape.txt").symlink_to(outside)
    context = RunContext("project", workspace, "session")
    tools = asyncio.run(FilesystemCapability().tools(context))
    tool = next(tool for tool in tools if tool.definition.name == "read_file")
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
    tools = asyncio.run(
        FilesystemCapability(max_read_file_bytes=3).tools(context)
    )
    tool = next(tool for tool in tools if tool.definition.name == "read_file")
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
    tools = asyncio.run(FilesystemCapability().tools(context))
    assert [tool.definition.name for tool in tools] == [
        "list_files",
        "find_files",
        "search_files",
        "read_file",
        "write_file",
        "create_directory",
        "move_path",
        "delete_path",
        "apply_patch",
    ]
    assert [tool.effect for tool in tools] == [
        ToolEffect.READ,
        ToolEffect.READ,
        ToolEffect.READ,
        ToolEffect.READ,
        ToolEffect.WRITE,
        ToolEffect.WRITE,
        ToolEffect.WRITE,
        ToolEffect.WRITE,
        ToolEffect.WRITE,
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
    tools = asyncio.run(FilesystemCapability(max_file_entries=2).tools(context))
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


def test_read_file_supports_bounded_line_ranges(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "large.txt").write_text(
        "one\ntwo\nthree\n",
        encoding="utf-8",
    )
    tool = _filesystem_tool(
        workspace,
        "read_file",
        max_read_file_bytes=6,
    )

    assert _execute_tool(tool, {"path": "large.txt"}) == (
        "read_file exceeds size limit"
    )
    assert (
        _execute_tool(
            tool,
            {"path": "large.txt", "start_line": 2, "end_line": 2},
        )
        == "two\n"
    )
    assert (
        _execute_tool(
            tool,
            {"path": "large.txt", "start_line": 3},
        )
        == "three\n"
    )

    (workspace / "selected.txt").write_bytes(b"\xff\nok\n")
    assert (
        _execute_tool(
            tool,
            {"path": "selected.txt", "start_line": 2, "end_line": 2},
        )
        == "ok\n"
    )


def test_find_and_search_files_are_recursive_bounded_reads(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "root.py").write_text("needle = 1\n", encoding="utf-8")
    (workspace / "ignored.txt").write_text("needle\n", encoding="utf-8")
    (workspace / "binary.py").write_bytes(b"needle\x00data")
    nested = workspace / "nested"
    nested.mkdir()
    (nested / "child.py").write_text(
        "first\nneedle = 2\n",
        encoding="utf-8",
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "escape.py").write_text("needle\n", encoding="utf-8")
    (workspace / "escape").symlink_to(outside)

    find = _filesystem_tool(workspace, "find_files")
    assert json.loads(_execute_tool(find, {"pattern": "**/*.py"})) == [
        "binary.py",
        "nested/child.py",
        "root.py",
    ]

    search = _filesystem_tool(workspace, "search_files")
    assert json.loads(
        _execute_tool(
            search,
            {"pattern": "needle", "include": "*.py", "literal": True},
        )
    ) == [
        {"path": "root.py", "line": 1, "text": "needle = 1"},
        {"path": "nested/child.py", "line": 2, "text": "needle = 2"},
    ]
    assert _execute_tool(search, {"pattern": "["}) == (
        "search_files pattern is invalid"
    )


def test_filesystem_discovery_and_write_limits_fail_clearly(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "one.py").write_text("match\n", encoding="utf-8")
    (workspace / "two.py").write_text("match\n", encoding="utf-8")

    find = _filesystem_tool(workspace, "find_files", max_file_entries=1)
    assert _execute_tool(find, {"pattern": "**/*.py"}) == (
        "find_files exceeds entry limit"
    )
    search = _filesystem_tool(
        workspace,
        "search_files",
        max_search_matches=1,
    )
    assert _execute_tool(search, {"pattern": "match"}) == (
        "search_files exceeds match limit"
    )
    small_search = _filesystem_tool(
        workspace,
        "search_files",
        max_search_result_bytes=2,
    )
    assert _execute_tool(small_search, {"pattern": "match"}) == (
        "search_files exceeds result size limit"
    )
    write = _filesystem_tool(
        workspace,
        "write_file",
        max_write_file_bytes=3,
    )
    assert (
        _execute_tool(
            write,
            {"path": "large.txt", "content": "four"},
        )
        == "write exceeds file size limit"
    )
    assert not (workspace / "large.txt").exists()

    patch = _filesystem_tool(
        workspace,
        "apply_patch",
        max_patch_files=1,
    )
    assert (
        _execute_tool(
            patch,
            {
                "patch": "\n".join(
                    (
                        "*** Begin Patch",
                        "*** Add File: first.txt",
                        "+first",
                        "*** Add File: second.txt",
                        "+second",
                        "*** End Patch",
                    )
                )
            },
        )
        == "apply_patch exceeds file limit"
    )
    assert not (workspace / "first.txt").exists()
    assert not (workspace / "second.txt").exists()
    small_patch = _filesystem_tool(
        workspace,
        "apply_patch",
        max_patch_bytes=10,
    )
    assert (
        _execute_tool(
            small_patch,
            {"patch": "*** Begin Patch\n*** End Patch"},
        )
        == "apply_patch exceeds patch size limit"
    )


def test_filesystem_mutations_require_approval_and_preserve_boundaries(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "escape").symlink_to(outside, target_is_directory=True)

    write = _filesystem_tool(workspace, "write_file")

    async def prepare_write() -> PreparedToolCall:
        prepared = await ToolExecutor(ToolRegistry((write,))).prepare(
            ToolCallPart(
                call_id="call-approval",
                name="write_file",
                arguments_json='{"path":"note.txt","content":"hello"}',
            )
        )
        assert isinstance(prepared, PreparedToolCall)
        return prepared

    prepared = asyncio.run(prepare_write())
    assert isinstance(prepared.decision, RequireApproval)
    assert (
        _execute_tool(
            write,
            {"path": "note.txt", "content": "hello"},
        )
        == "created note.txt"
    )
    (workspace / "note.txt").chmod(0o640)
    assert (
        _execute_tool(
            write,
            {"path": "note.txt", "content": "updated"},
        )
        == "updated note.txt"
    )
    assert (workspace / "note.txt").read_text(encoding="utf-8") == "updated"
    assert (workspace / "note.txt").stat().st_mode & 0o777 == 0o640
    assert (
        _execute_tool(
            write,
            {"path": "escape/no.txt", "content": "outside"},
        )
        == "tool path must be inside the workspace"
    )
    assert not (outside / "no.txt").exists()

    create = _filesystem_tool(workspace, "create_directory")
    assert _execute_tool(create, {"path": "one/two"}) == (
        "created directory one/two"
    )
    assert (workspace / "one" / "two").is_dir()

    move = _filesystem_tool(workspace, "move_path")
    assert (
        _execute_tool(
            move,
            {"source": "note.txt", "destination": "one/moved.txt"},
        )
        == "moved note.txt to one/moved.txt"
    )
    assert not (workspace / "note.txt").exists()
    assert (workspace / "one" / "moved.txt").is_file()
    (workspace / "collision.txt").touch()
    assert (
        _execute_tool(
            move,
            {"source": "one", "destination": "one/two/inside"},
        )
        == "move_path cannot move a directory into itself"
    )
    assert (
        _execute_tool(
            move,
            {
                "source": "one/moved.txt",
                "destination": "collision.txt",
            },
        )
        == "move_path destination already exists"
    )

    delete = _filesystem_tool(workspace, "delete_path")
    assert _execute_tool(delete, {"path": "one"}) == (
        "delete_path directory is not empty; set recursive to true"
    )
    assert (
        _execute_tool(
            delete,
            {"path": "one", "recursive": True},
        )
        == "deleted directory one"
    )
    assert not (workspace / "one").exists()
    assert (
        _execute_tool(
            delete,
            {"path": ".", "recursive": True},
        )
        == "tool path must not be the workspace root"
    )
    assert _execute_tool(delete, {"path": "escape", "recursive": True}) == (
        "tool path must be inside the workspace"
    )
    assert outside.is_dir()


def test_apply_patch_validates_all_files_before_atomic_replacements(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    original = workspace / "original.txt"
    original.write_text("one\ntwo\nthree\n", encoding="utf-8")
    obsolete = workspace / "obsolete.txt"
    obsolete.write_text("old\n", encoding="utf-8")
    tool = _filesystem_tool(workspace, "apply_patch")

    result = json.loads(
        _execute_tool(
            tool,
            {
                "patch": "\n".join(
                    (
                        "*** Begin Patch",
                        "*** Update File: original.txt",
                        "@@",
                        " one",
                        "-two",
                        "+changed",
                        "@@",
                        " three",
                        "+four",
                        "*** Add File: created.txt",
                        "+hello",
                        "+world",
                        "*** Delete File: obsolete.txt",
                        "*** End Patch",
                    )
                )
            },
        )
    )
    assert result == {
        "created": ["created.txt"],
        "updated": ["original.txt"],
        "deleted": ["obsolete.txt"],
    }
    assert original.read_text(encoding="utf-8") == (
        "one\nchanged\nthree\nfour\n"
    )
    assert (workspace / "created.txt").read_text(encoding="utf-8") == (
        "hello\nworld\n"
    )
    assert not obsolete.exists()

    failed = _execute_tool(
        tool,
        {
            "patch": "\n".join(
                (
                    "*** Begin Patch",
                    "*** Add File: should-not-exist.txt",
                    "+content",
                    "*** Update File: original.txt",
                    "@@",
                    "-stale",
                    "+wrong",
                    "*** End Patch",
                )
            )
        },
    )
    assert failed == "apply_patch context does not match"
    assert not (workspace / "should-not-exist.txt").exists()
    assert original.read_text(encoding="utf-8") == (
        "one\nchanged\nthree\nfour\n"
    )


def test_apply_patch_rejects_ambiguous_duplicate_and_symlink_targets(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "target.txt"
    target.write_text("same\nmiddle\nsame\n", encoding="utf-8")
    (workspace / "link.txt").symlink_to(target)
    tool = _filesystem_tool(workspace, "apply_patch")

    ambiguous = "\n".join(
        (
            "*** Begin Patch",
            "*** Update File: target.txt",
            "@@",
            "-same",
            "+changed",
            "*** End Patch",
        )
    )
    assert _execute_tool(tool, {"patch": ambiguous}) == (
        "apply_patch context is ambiguous"
    )
    assert target.read_text(encoding="utf-8") == "same\nmiddle\nsame\n"

    duplicate = "\n".join(
        (
            "*** Begin Patch",
            "*** Update File: target.txt",
            "@@",
            " middle",
            "+first",
            "*** Update File: ./target.txt",
            "@@",
            " middle",
            "+second",
            "*** End Patch",
        )
    )
    assert _execute_tool(tool, {"patch": duplicate}) == (
        "apply_patch contains a duplicate path"
    )
    assert target.read_text(encoding="utf-8") == "same\nmiddle\nsame\n"

    symlink = "\n".join(
        (
            "*** Begin Patch",
            "*** Update File: link.txt",
            "@@",
            " middle",
            "+linked",
            "*** End Patch",
        )
    )
    assert _execute_tool(tool, {"patch": symlink}) == (
        "write tool paths must not use symlinks"
    )
    assert target.read_text(encoding="utf-8") == "same\nmiddle\nsame\n"

    emptied = workspace / "emptied.txt"
    emptied.write_text("only\n", encoding="utf-8")
    delete_all = "\n".join(
        (
            "*** Begin Patch",
            "*** Update File: emptied.txt",
            "@@",
            "-only",
            "*** End Patch",
        )
    )
    assert json.loads(_execute_tool(tool, {"patch": delete_all}))[
        "updated"
    ] == ["emptied.txt"]
    assert emptied.read_text(encoding="utf-8") == ""
