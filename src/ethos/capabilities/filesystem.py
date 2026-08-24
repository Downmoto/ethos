"""Read-only filesystem capability bounded to a workspace."""

import asyncio
import json
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from ethos.capabilities import RunContext
from ethos.models import ToolDefinition
from ethos.tools import Tool, ToolEffect, ToolExecutionError

MAX_READ_FILE_BYTES: Final = 100 * 1024
MAX_LIST_FILES_ENTRIES: Final = 1_000


class _ReadFileArguments(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(min_length=1)


class _ListFilesArguments(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(default=".", min_length=1)


@dataclass
class _ReadFileTool:
    workspace_path: Path
    definition: ToolDefinition = ToolDefinition(
        name="read_file",
        description=(
            "Read one UTF-8 text file relative to the workspace root."
        ),
        parameters_schema=_ReadFileArguments.model_json_schema(),
    )
    effect: ToolEffect = ToolEffect.READ
    arguments_type: type[BaseModel] = _ReadFileArguments

    async def execute(self, arguments: BaseModel) -> str:
        if not isinstance(arguments, _ReadFileArguments):
            raise TypeError("invalid read_file arguments")
        return await asyncio.to_thread(self._read, arguments.path)

    def _read(self, requested_path: str) -> str:
        _root, path = _resolve_workspace_path(
            self.workspace_path,
            requested_path,
        )
        if not path.is_file():
            raise ToolExecutionError("read_file path must be a workspace file")
        with path.open("rb") as file:
            content = file.read(MAX_READ_FILE_BYTES + 1)
        if len(content) > MAX_READ_FILE_BYTES:
            raise ToolExecutionError("read_file exceeds size limit")
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ToolExecutionError(
                "read_file only supports UTF-8 text files"
            ) from error


@dataclass
class _ListFilesTool:
    workspace_path: Path
    definition: ToolDefinition = ToolDefinition(
        name="list_files",
        description=(
            "List one workspace directory as a JSON array of relative paths."
        ),
        parameters_schema=_ListFilesArguments.model_json_schema(),
    )
    effect: ToolEffect = ToolEffect.READ
    arguments_type: type[BaseModel] = _ListFilesArguments

    async def execute(self, arguments: BaseModel) -> str:
        if not isinstance(arguments, _ListFilesArguments):
            raise TypeError("invalid list_files arguments")
        return await asyncio.to_thread(self._list, arguments.path)

    def _list(self, requested_path: str) -> str:
        root, path = _resolve_workspace_path(
            self.workspace_path,
            requested_path,
        )
        if not path.is_dir():
            raise ToolExecutionError(
                "list_files path must be a workspace directory"
            )
        entries = list(islice(path.iterdir(), MAX_LIST_FILES_ENTRIES + 1))
        if len(entries) > MAX_LIST_FILES_ENTRIES:
            raise ToolExecutionError("list_files exceeds entry limit")
        paths = [
            f"{entry.relative_to(root).as_posix()}"
            f"{'/' if entry.is_dir() and not entry.is_symlink() else ''}"
            for entry in sorted(entries, key=lambda item: item.name)
        ]
        return json.dumps(paths, ensure_ascii=False)


def _resolve_workspace_path(
    workspace_path: Path,
    requested_path: str,
) -> tuple[Path, Path]:
    relative_path = Path(requested_path)
    if relative_path.is_absolute():
        raise ToolExecutionError(
            "tool path must be relative to the workspace root"
        )
    root = workspace_path.resolve(strict=True)
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root):
        raise ToolExecutionError("tool path must be inside the workspace")
    if not path.exists():
        raise ToolExecutionError("tool path does not exist")
    return root, path


class ReadOnlyFilesystemCapability:
    async def instructions(self, context: RunContext) -> tuple[str, ...]:
        return (
            "Paths passed to filesystem tools are relative to the "
            "workspace root.",
        )

    async def tools(self, context: RunContext) -> tuple[Tool, ...]:
        return (
            _ReadFileTool(context.workspace_path),
            _ListFilesTool(context.workspace_path),
        )
