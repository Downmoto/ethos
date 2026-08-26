"""Workspace-bounded filesystem tools.

Reads may follow links that resolve inside the workspace. Mutations reject
every symlink component so validation and the eventual write address the same
path. All blocking filesystem work runs in a worker thread; the tool executor
owns approval, cancellation, and indeterminate-write handling.
"""

import asyncio
import json
import os
import re
import shutil
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from fnmatch import fnmatchcase
from itertools import islice
from pathlib import Path
from typing import Final, Literal, Self, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ethos.capabilities import RunContext
from ethos.capabilities._files import FileTooLargeError, read_bounded_utf8
from ethos.models import ToolDefinition
from ethos.tools import Tool, ToolEffect, ToolExecutionError

MAX_READ_FILE_BYTES: Final = 100 * 1024
MAX_WRITE_FILE_BYTES: Final = 100 * 1024
MAX_FILE_ENTRIES: Final = 1_000
MAX_SEARCH_MATCHES: Final = 1_000
MAX_SEARCH_RESULT_BYTES: Final = 100 * 1024
MAX_PATCH_BYTES: Final = 100 * 1024
MAX_PATCH_FILES: Final = 20


class _ReadFileArguments(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(min_length=1)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if (
            self.start_line is not None
            and self.end_line is not None
            and self.end_line < self.start_line
        ):
            raise ValueError("end_line must not precede start_line")
        return self


class _ListFilesArguments(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(
        default=".",
        min_length=1,
        description=(
            'Workspace-relative directory. Use "." for the workspace root; '
            "never pass an absolute path."
        ),
    )


class _FindFilesArguments(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pattern: str = Field(min_length=1)
    path: str = Field(default=".", min_length=1)


class _SearchFilesArguments(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pattern: str = Field(min_length=1)
    path: str = Field(default=".", min_length=1)
    include: str | None = Field(default=None, min_length=1)
    literal: bool = False


class _WriteFileArguments(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(min_length=1)
    content: str


class _PathArguments(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(min_length=1)


class _MovePathArguments(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(min_length=1)
    destination: str = Field(min_length=1)


class _DeletePathArguments(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(min_length=1)
    recursive: bool = False


class _ApplyPatchArguments(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    patch: str = Field(min_length=1)


@dataclass
class _ReadFileTool:
    workspace_path: Path
    max_file_bytes: int
    definition: ToolDefinition = ToolDefinition(
        name="read_file",
        description=(
            "Read a UTF-8 text file relative to the workspace root. Optional "
            "one-based start_line and end_line select a bounded range."
        ),
        parameters_schema=_ReadFileArguments.model_json_schema(),
    )
    effect: ToolEffect = ToolEffect.READ
    arguments_type: type[BaseModel] = _ReadFileArguments

    async def execute(self, arguments: BaseModel) -> str:
        if not isinstance(arguments, _ReadFileArguments):
            raise TypeError("invalid read_file arguments")
        return await asyncio.to_thread(
            self._read,
            arguments.path,
            arguments.start_line,
            arguments.end_line,
        )

    def _read(
        self,
        requested_path: str,
        start_line: int | None,
        end_line: int | None,
    ) -> str:
        _root, path = _resolve_existing_path(
            self.workspace_path,
            requested_path,
        )
        if not path.is_file():
            raise ToolExecutionError("read_file path must be a workspace file")
        try:
            if start_line is None and end_line is None:
                return read_bounded_utf8(path, self.max_file_bytes)
            return _read_utf8_range(
                path,
                start_line or 1,
                end_line,
                self.max_file_bytes,
            )
        except FileTooLargeError:
            raise ToolExecutionError("read_file exceeds size limit") from None
        except UnicodeDecodeError as error:
            raise ToolExecutionError(
                "read_file only supports UTF-8 text files"
            ) from error


@dataclass
class _ListFilesTool:
    workspace_path: Path
    max_entries: int
    definition: ToolDefinition = ToolDefinition(
        name="list_files",
        description=(
            "List the immediate files and directories in one workspace "
            "directory as a JSON array. Directory paths end in '/'."
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
        root, path = _resolve_existing_path(
            self.workspace_path,
            requested_path,
        )
        if not path.is_dir():
            raise ToolExecutionError(
                "list_files path must be a workspace directory"
            )
        entries = list(islice(path.iterdir(), self.max_entries + 1))
        if len(entries) > self.max_entries:
            raise ToolExecutionError("list_files exceeds entry limit")
        paths = [_display_path(root, entry) for entry in entries]
        return json.dumps(sorted(paths), ensure_ascii=False)


@dataclass
class _FindFilesTool:
    workspace_path: Path
    max_entries: int
    definition: ToolDefinition = ToolDefinition(
        name="find_files",
        description=(
            "Recursively find workspace files and directories matching a "
            "glob. Results are relative paths; directories end in '/'."
        ),
        parameters_schema=_FindFilesArguments.model_json_schema(),
    )
    effect: ToolEffect = ToolEffect.READ
    arguments_type: type[BaseModel] = _FindFilesArguments

    async def execute(self, arguments: BaseModel) -> str:
        if not isinstance(arguments, _FindFilesArguments):
            raise TypeError("invalid find_files arguments")
        return await asyncio.to_thread(
            self._find,
            arguments.pattern,
            arguments.path,
        )

    def _find(self, pattern: str, requested_path: str) -> str:
        root, path = _resolve_existing_path(
            self.workspace_path,
            requested_path,
        )
        if not path.is_dir():
            raise ToolExecutionError(
                "find_files path must be a workspace directory"
            )
        matches: list[str] = []
        for entry in _walk(path):
            relative = entry.relative_to(path).as_posix()
            if _glob_matches(relative, pattern):
                matches.append(_display_path(root, entry))
                if len(matches) > self.max_entries:
                    raise ToolExecutionError("find_files exceeds entry limit")
        return json.dumps(sorted(matches), ensure_ascii=False)


@dataclass
class _SearchFilesTool:
    workspace_path: Path
    max_matches: int
    max_result_bytes: int
    definition: ToolDefinition = ToolDefinition(
        name="search_files",
        description=(
            "Search UTF-8 workspace files. pattern is a regular expression "
            "unless literal is true. Results contain path, line, and text."
        ),
        parameters_schema=_SearchFilesArguments.model_json_schema(),
    )
    effect: ToolEffect = ToolEffect.READ
    arguments_type: type[BaseModel] = _SearchFilesArguments

    async def execute(self, arguments: BaseModel) -> str:
        if not isinstance(arguments, _SearchFilesArguments):
            raise TypeError("invalid search_files arguments")
        return await asyncio.to_thread(
            self._search,
            arguments.pattern,
            arguments.path,
            arguments.include,
            arguments.literal,
        )

    def _search(
        self,
        pattern: str,
        requested_path: str,
        include: str | None,
        literal: bool,
    ) -> str:
        root, path = _resolve_existing_path(
            self.workspace_path,
            requested_path,
        )
        if not path.is_dir():
            raise ToolExecutionError(
                "search_files path must be a workspace directory"
            )
        try:
            expression = re.compile(re.escape(pattern) if literal else pattern)
        except re.error as error:
            raise ToolExecutionError(
                "search_files pattern is invalid"
            ) from error

        matches: list[dict[str, str | int]] = []
        for entry in _walk(path, files_only=True):
            relative_to_search = entry.relative_to(path).as_posix()
            if include is not None and not _glob_matches(
                relative_to_search,
                include,
                basename_when_unqualified=True,
            ):
                continue
            # Keep one file isolated until it decodes completely. A late
            # decoding error must discard its earlier matches as well.
            file_matches: list[dict[str, str | int]] = []
            try:
                with entry.open(encoding="utf-8") as file:
                    for line_number, line in enumerate(file, start=1):
                        if "\0" in line:
                            file_matches.clear()
                            break
                        if expression.search(line):
                            record: dict[str, str | int] = {
                                "path": entry.relative_to(root).as_posix(),
                                "line": line_number,
                                "text": line.rstrip("\r\n"),
                            }
                            proposed = [*matches, *file_matches, record]
                            if len(proposed) > self.max_matches:
                                raise ToolExecutionError(
                                    "search_files exceeds match limit"
                                )
                            if (
                                len(
                                    json.dumps(
                                        proposed,
                                        ensure_ascii=False,
                                    ).encode("utf-8")
                                )
                                > self.max_result_bytes
                            ):
                                raise ToolExecutionError(
                                    "search_files exceeds result size limit"
                                )
                            file_matches.append(record)
            except (UnicodeDecodeError, OSError):
                continue
            matches.extend(file_matches)
        result = json.dumps(matches, ensure_ascii=False)
        if len(result.encode("utf-8")) > self.max_result_bytes:
            raise ToolExecutionError("search_files exceeds result size limit")
        return result


@dataclass
class _WriteFileTool:
    workspace_path: Path
    max_file_bytes: int
    definition: ToolDefinition = ToolDefinition(
        name="write_file",
        description=(
            "Create or atomically replace one UTF-8 text file relative to "
            "the workspace root. The parent directory must exist."
        ),
        parameters_schema=_WriteFileArguments.model_json_schema(),
    )
    effect: ToolEffect = ToolEffect.WRITE
    arguments_type: type[BaseModel] = _WriteFileArguments

    async def execute(self, arguments: BaseModel) -> str:
        if not isinstance(arguments, _WriteFileArguments):
            raise TypeError("invalid write_file arguments")
        return await asyncio.to_thread(
            self._write,
            arguments.path,
            arguments.content,
        )

    def _write(self, requested_path: str, content: str) -> str:
        root, path = _resolve_mutation_path(
            self.workspace_path,
            requested_path,
        )
        if not path.parent.is_dir():
            raise ToolExecutionError("write_file parent directory must exist")
        if path.exists() and not path.is_file():
            raise ToolExecutionError("write_file path must be a workspace file")
        outcome = "updated" if path.exists() else "created"
        _atomic_write_utf8(path, content, self.max_file_bytes)
        return f"{outcome} {path.relative_to(root).as_posix()}"


@dataclass
class _CreateDirectoryTool:
    workspace_path: Path
    definition: ToolDefinition = ToolDefinition(
        name="create_directory",
        description=(
            "Create a workspace directory and any missing parent directories."
        ),
        parameters_schema=_PathArguments.model_json_schema(),
    )
    effect: ToolEffect = ToolEffect.WRITE
    arguments_type: type[BaseModel] = _PathArguments

    async def execute(self, arguments: BaseModel) -> str:
        if not isinstance(arguments, _PathArguments):
            raise TypeError("invalid create_directory arguments")
        return await asyncio.to_thread(self._create, arguments.path)

    def _create(self, requested_path: str) -> str:
        root, path = _resolve_mutation_path(
            self.workspace_path,
            requested_path,
        )
        if path.exists():
            if not path.is_dir():
                raise ToolExecutionError(
                    "create_directory path exists and is not a directory"
                )
            return (
                f"directory already exists: {path.relative_to(root).as_posix()}"
            )
        try:
            path.mkdir(parents=True)
        except OSError as error:
            raise ToolExecutionError("create_directory failed") from error
        return f"created directory {path.relative_to(root).as_posix()}"


@dataclass
class _MovePathTool:
    workspace_path: Path
    definition: ToolDefinition = ToolDefinition(
        name="move_path",
        description=(
            "Move or rename a workspace file or directory without "
            "overwriting an existing destination."
        ),
        parameters_schema=_MovePathArguments.model_json_schema(),
    )
    effect: ToolEffect = ToolEffect.WRITE
    arguments_type: type[BaseModel] = _MovePathArguments

    async def execute(self, arguments: BaseModel) -> str:
        if not isinstance(arguments, _MovePathArguments):
            raise TypeError("invalid move_path arguments")
        return await asyncio.to_thread(
            self._move,
            arguments.source,
            arguments.destination,
        )

    def _move(self, source: str, destination: str) -> str:
        root, source_path = _resolve_mutation_path(
            self.workspace_path,
            source,
            must_exist=True,
        )
        _destination_root, destination_path = _resolve_mutation_path(
            self.workspace_path,
            destination,
        )
        if destination_path.exists():
            raise ToolExecutionError("move_path destination already exists")
        if not destination_path.parent.is_dir():
            raise ToolExecutionError("move_path destination parent must exist")
        if source_path.is_dir() and destination_path.is_relative_to(
            source_path
        ):
            raise ToolExecutionError(
                "move_path cannot move a directory into itself"
            )
        try:
            source_path.rename(destination_path)
        except OSError as error:
            raise ToolExecutionError("move_path failed") from error
        return (
            f"moved {source_path.relative_to(root).as_posix()} to "
            f"{destination_path.relative_to(root).as_posix()}"
        )


@dataclass
class _DeletePathTool:
    workspace_path: Path
    definition: ToolDefinition = ToolDefinition(
        name="delete_path",
        description=(
            "Delete a workspace file or directory. recursive=true is required "
            "for a non-empty directory."
        ),
        parameters_schema=_DeletePathArguments.model_json_schema(),
    )
    effect: ToolEffect = ToolEffect.WRITE
    arguments_type: type[BaseModel] = _DeletePathArguments

    async def execute(self, arguments: BaseModel) -> str:
        if not isinstance(arguments, _DeletePathArguments):
            raise TypeError("invalid delete_path arguments")
        return await asyncio.to_thread(
            self._delete,
            arguments.path,
            arguments.recursive,
        )

    def _delete(self, requested_path: str, recursive: bool) -> str:
        root, path = _resolve_mutation_path(
            self.workspace_path,
            requested_path,
            must_exist=True,
        )
        relative = path.relative_to(root).as_posix()
        try:
            if path.is_file():
                path.unlink()
                kind = "file"
            elif path.is_dir():
                if recursive:
                    shutil.rmtree(path)
                else:
                    path.rmdir()
                kind = "directory"
            else:
                raise ToolExecutionError(
                    "delete_path path must be a workspace file or directory"
                )
        except OSError as error:
            if path.is_dir() and not recursive:
                raise ToolExecutionError(
                    "delete_path directory is not empty; set recursive to true"
                ) from error
            raise ToolExecutionError("delete_path failed") from error
        return f"deleted {kind} {relative}"


@dataclass(frozen=True)
class _PatchOperation:
    kind: Literal["add", "update", "delete"]
    path: str
    body: tuple[str, ...]


@dataclass
class _ApplyPatchTool:
    workspace_path: Path
    max_patch_bytes: int
    max_patch_files: int
    max_file_bytes: int
    definition: ToolDefinition = ToolDefinition(
        name="apply_patch",
        description=(
            "Apply a structured UTF-8 patch. Use a '*** Begin Patch' and "
            "'*** End Patch' envelope with '*** Add File: path', "
            "'*** Update File: path', or '*** Delete File: path' sections. "
            "Added lines start '+'. Update hunks start '@@' and use space, "
            "'-', and '+' prefixes."
        ),
        parameters_schema=_ApplyPatchArguments.model_json_schema(),
    )
    effect: ToolEffect = ToolEffect.WRITE
    arguments_type: type[BaseModel] = _ApplyPatchArguments

    async def execute(self, arguments: BaseModel) -> str:
        if not isinstance(arguments, _ApplyPatchArguments):
            raise TypeError("invalid apply_patch arguments")
        return await asyncio.to_thread(self._apply, arguments.patch)

    def _apply(self, patch: str) -> str:
        """Validate the complete patch before staging or replacing any file.

        Individual replacements are atomic, but a multi-file patch is not a
        filesystem transaction. An unexpected failure after replacement starts
        therefore propagates through the runtime as an indeterminate write.
        """

        if len(patch.encode("utf-8")) > self.max_patch_bytes:
            raise ToolExecutionError("apply_patch exceeds patch size limit")
        operations = _parse_patch(patch)
        if len(operations) > self.max_patch_files:
            raise ToolExecutionError("apply_patch exceeds file limit")

        root = self.workspace_path.resolve(strict=True)
        changes: list[tuple[_PatchOperation, Path, str | None]] = []
        resolved_paths: set[Path] = set()
        for operation in operations:
            _operation_root, path = _resolve_mutation_path(
                self.workspace_path,
                operation.path,
            )
            if path in resolved_paths:
                raise ToolExecutionError(
                    "apply_patch contains a duplicate path"
                )
            resolved_paths.add(path)
            if operation.kind == "add":
                if path.exists():
                    raise ToolExecutionError(
                        "apply_patch add path already exists"
                    )
                if not path.parent.is_dir():
                    raise ToolExecutionError(
                        "apply_patch add parent directory must exist"
                    )
                result_content: str | None = _added_file_content(operation.body)
            else:
                if not path.is_file():
                    raise ToolExecutionError(
                        "apply_patch path must be a workspace file"
                    )
                try:
                    current = read_bounded_utf8(path, self.max_file_bytes)
                except FileTooLargeError:
                    raise ToolExecutionError(
                        "apply_patch file exceeds size limit"
                    ) from None
                except UnicodeDecodeError as error:
                    raise ToolExecutionError(
                        "apply_patch only supports UTF-8 text files"
                    ) from error
                result_content = (
                    None
                    if operation.kind == "delete"
                    else _apply_update(current, operation.body)
                )
            if result_content is not None and (
                len(result_content.encode("utf-8")) > self.max_file_bytes
            ):
                raise ToolExecutionError(
                    "apply_patch resulting file exceeds size limit"
                )
            changes.append((operation, path, result_content))

        # Prepare every new content file first so ordinary validation and
        # staging failures leave all targets untouched.
        temporary_files: dict[Path, Path] = {}
        try:
            for _operation, change_path, change_content in changes:
                if change_content is not None:
                    temporary_files[change_path] = _prepare_utf8_file(
                        change_path,
                        change_content,
                        self.max_file_bytes,
                    )
            # Only atomic replacements and deletions remain beyond this point.
            for _operation, change_path, change_content in changes:
                if change_content is None:
                    change_path.unlink()
                else:
                    temporary_files[change_path].replace(change_path)
                temporary_files.pop(change_path, None)
        finally:
            for temporary in temporary_files.values():
                temporary.unlink(missing_ok=True)

        summary: dict[str, list[str]] = {
            "created": [],
            "updated": [],
            "deleted": [],
        }
        for operation, path, _content in changes:
            key = {
                "add": "created",
                "update": "updated",
                "delete": "deleted",
            }[operation.kind]
            summary[key].append(path.relative_to(root).as_posix())
        return json.dumps(summary, ensure_ascii=False)


def _read_utf8_range(
    path: Path,
    start_line: int,
    end_line: int | None,
    max_bytes: int,
) -> str:
    """Read and decode only the requested lines, bounding returned raw bytes.

    Lines before ``start_line`` are located by iterating raw lines but are not
    decoded or retained. This also permits reading a valid range without
    validating skipped content.
    """

    content: list[str] = []
    size = 0
    with path.open("rb") as file:
        for line_number, raw_line in enumerate(file, start=1):
            if line_number < start_line:
                continue
            if end_line is not None and line_number > end_line:
                break
            line = raw_line.decode("utf-8")
            size += len(raw_line)
            if size > max_bytes:
                raise FileTooLargeError
            content.append(line)
    return "".join(content)


def _walk(path: Path, *, files_only: bool = False) -> Iterator[Path]:
    """Yield a deterministic recursive walk without following symlinks."""

    for current, directories, files in os.walk(path, followlinks=False):
        current_path = Path(current)
        directories[:] = sorted(
            directory
            for directory in directories
            if not (current_path / directory).is_symlink()
        )
        if not files_only:
            for directory in directories:
                yield current_path / directory
        for file in sorted(files):
            entry = current_path / file
            if not entry.is_symlink():
                yield entry


def _display_path(root: Path, path: Path) -> str:
    """Render one workspace-relative path with a directory marker."""

    relative = path.relative_to(root).as_posix()
    return (
        f"{relative}/" if path.is_dir() and not path.is_symlink() else relative
    )


def _glob_matches(
    path: str,
    pattern: str,
    *,
    basename_when_unqualified: bool = False,
) -> bool:
    """Match POSIX paths with ``**`` consuming zero or more segments.

    Search include filters treat an unqualified pattern as a basename match;
    discovery globs always match the complete path relative to their root.
    """

    path_parts = tuple(path.split("/"))
    if basename_when_unqualified and "/" not in pattern:
        return fnmatchcase(path_parts[-1], pattern)
    pattern_parts = tuple(pattern.split("/"))
    memo: dict[tuple[int, int], bool] = {}

    def match(pattern_index: int, path_index: int) -> bool:
        key = (pattern_index, path_index)
        if key in memo:
            return memo[key]
        if pattern_index == len(pattern_parts):
            result = path_index == len(path_parts)
        elif pattern_parts[pattern_index] == "**":
            result = match(pattern_index + 1, path_index) or (
                path_index < len(path_parts)
                and match(pattern_index, path_index + 1)
            )
        else:
            result = (
                path_index < len(path_parts)
                and fnmatchcase(
                    path_parts[path_index],
                    pattern_parts[pattern_index],
                )
                and match(pattern_index + 1, path_index + 1)
            )
        memo[key] = result
        return result

    return match(0, 0)


def _workspace_relative_path(requested_path: str) -> Path:
    """Parse a tool path without allowing an absolute host path."""

    relative = Path(requested_path)
    if relative.is_absolute():
        raise ToolExecutionError(
            "tool path must be relative to the workspace root"
        )
    return relative


def _resolve_existing_path(
    workspace_path: Path,
    requested_path: str,
) -> tuple[Path, Path]:
    """Resolve a read path, allowing only links contained by the workspace."""

    relative = _workspace_relative_path(requested_path)
    root = workspace_path.resolve(strict=True)
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ToolExecutionError("tool path must be inside the workspace")
    if not path.exists():
        raise ToolExecutionError("tool path does not exist")
    return root, path


def _resolve_mutation_path(
    workspace_path: Path,
    requested_path: str,
    *,
    must_exist: bool = False,
) -> tuple[Path, Path]:
    """Resolve a mutation path while rejecting every symlink component.

    Containment is checked on the resolved path, then each unresolved component
    is checked separately. The latter prevents a write from addressing a link
    even when that link happens to point back inside the workspace.
    """

    relative = _workspace_relative_path(requested_path)
    root = workspace_path.resolve(strict=True)
    unresolved = root / relative
    path = unresolved.resolve()
    if not path.is_relative_to(root):
        raise ToolExecutionError("tool path must be inside the workspace")
    if path == root:
        raise ToolExecutionError("tool path must not be the workspace root")

    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ToolExecutionError("write tool paths must not use symlinks")
    if must_exist and not path.exists():
        raise ToolExecutionError("tool path does not exist")
    return root, path


def _prepare_utf8_file(path: Path, content: str, max_bytes: int) -> Path:
    """Write bounded UTF-8 content to a sibling temporary file.

    A sibling keeps the later replacement on the target filesystem. Existing
    permission bits are copied because replacing a path installs the temporary
    file's metadata as well as its content.
    """

    encoded = content.encode("utf-8")
    if len(encoded) > max_bytes:
        raise ToolExecutionError("write exceeds file size limit")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(encoded)
        if path.exists():
            temporary.chmod(stat.S_IMODE(path.stat().st_mode))
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _atomic_write_utf8(path: Path, content: str, max_bytes: int) -> None:
    """Prepare then atomically replace one file, cleaning abandoned staging."""

    temporary = _prepare_utf8_file(path, content, max_bytes)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _parse_patch(patch: str) -> tuple[_PatchOperation, ...]:
    """Parse the supported patch envelope without accessing the filesystem."""

    lines = patch.splitlines()
    if len(lines) < 3 or lines[0] != "*** Begin Patch":
        raise ToolExecutionError("apply_patch has an invalid envelope")
    if lines[-1] != "*** End Patch":
        raise ToolExecutionError("apply_patch has an invalid envelope")

    operations: list[_PatchOperation] = []
    seen_paths: set[str] = set()
    index = 1
    while index < len(lines) - 1:
        header = lines[index]
        kinds = {
            "*** Add File: ": "add",
            "*** Update File: ": "update",
            "*** Delete File: ": "delete",
        }
        kind: Literal["add", "update", "delete"] | None = None
        path = ""
        for prefix, candidate in kinds.items():
            if header.startswith(prefix):
                kind = cast(Literal["add", "update", "delete"], candidate)
                path = header.removeprefix(prefix)
                break
        if kind is None or not path:
            raise ToolExecutionError("apply_patch has an invalid file header")
        if path in seen_paths:
            raise ToolExecutionError("apply_patch contains a duplicate path")
        seen_paths.add(path)
        index += 1
        body: list[str] = []
        while index < len(lines) - 1 and not lines[index].startswith("*** "):
            body.append(lines[index])
            index += 1
        if kind == "add" and (
            not body or any(not line.startswith("+") for line in body)
        ):
            raise ToolExecutionError("apply_patch add section is invalid")
        if kind == "delete" and body:
            raise ToolExecutionError("apply_patch delete section is invalid")
        if kind == "update" and not body:
            raise ToolExecutionError("apply_patch update section is empty")
        operations.append(_PatchOperation(kind, path, tuple(body)))

    if not operations:
        raise ToolExecutionError("apply_patch is empty")
    return tuple(operations)


def _added_file_content(body: tuple[str, ...]) -> str:
    """Strip add markers and give newly patched files a final newline."""

    return "\n".join(line[1:] for line in body) + "\n"


def _apply_update(content: str, body: tuple[str, ...]) -> str:
    """Apply ordered hunks whose old text has one exact remaining match.

    The cursor prevents a later hunk from matching text before an earlier one.
    Existing final-newline state is preserved unless the update empties the
    file.
    """

    lines = content.splitlines()
    ends_with_newline = content.endswith("\n")
    hunks: list[list[str]] = []
    current: list[str] | None = None
    for line in body:
        if line == "@@" or line.startswith("@@ "):
            current = []
            hunks.append(current)
        elif current is None or not line or line[0] not in " +-":
            raise ToolExecutionError("apply_patch update hunk is invalid")
        else:
            current.append(line)
    if not hunks or any(not hunk for hunk in hunks):
        raise ToolExecutionError("apply_patch update hunk is invalid")

    cursor = 0
    for hunk in hunks:
        old = [line[1:] for line in hunk if line[0] in " -"]
        new = [line[1:] for line in hunk if line[0] in " +"]
        if old == new:
            raise ToolExecutionError("apply_patch update hunk has no changes")
        positions = [
            position
            for position in range(cursor, len(lines) - len(old) + 1)
            if lines[position : position + len(old)] == old
        ]
        if not positions:
            raise ToolExecutionError("apply_patch context does not match")
        if len(positions) > 1:
            raise ToolExecutionError("apply_patch context is ambiguous")
        position = positions[0]
        lines[position : position + len(old)] = new
        cursor = position + len(new)
    result = "\n".join(lines)
    return result + "\n" if ends_with_newline and lines else result


class FilesystemCapability:
    """Contribute bounded filesystem tools scoped to the active workspace."""

    def __init__(
        self,
        *,
        max_read_file_bytes: int = MAX_READ_FILE_BYTES,
        max_write_file_bytes: int = MAX_WRITE_FILE_BYTES,
        max_file_entries: int = MAX_FILE_ENTRIES,
        max_search_matches: int = MAX_SEARCH_MATCHES,
        max_search_result_bytes: int = MAX_SEARCH_RESULT_BYTES,
        max_patch_bytes: int = MAX_PATCH_BYTES,
        max_patch_files: int = MAX_PATCH_FILES,
    ) -> None:
        limits = (
            max_read_file_bytes,
            max_write_file_bytes,
            max_file_entries,
            max_search_matches,
            max_search_result_bytes,
            max_patch_bytes,
            max_patch_files,
        )
        if any(limit < 1 for limit in limits):
            raise ValueError("filesystem limits must be positive")
        self._max_read_file_bytes = max_read_file_bytes
        self._max_write_file_bytes = max_write_file_bytes
        self._max_file_entries = max_file_entries
        self._max_search_matches = max_search_matches
        self._max_search_result_bytes = max_search_result_bytes
        self._max_patch_bytes = max_patch_bytes
        self._max_patch_files = max_patch_files

    async def instructions(self, context: RunContext) -> tuple[str, ...]:
        del context
        return (
            "Filesystem tool paths are relative to the workspace root; "
            "absolute paths are invalid. list_files is shallow and marks "
            "directories with '/'; use find_files for recursive path discovery "
            "and search_files for content discovery. Use ranged read_file "
            "calls "
            "for large UTF-8 text files. Use apply_patch for localized edits, "
            "write_file for new or complete files, and the explicit directory, "
            "move, and delete tools for structural changes. Mutations require "
            "approval, moves never overwrite, and recursive deletion must be "
            "explicit.",
        )

    async def tools(self, context: RunContext) -> tuple[Tool, ...]:
        workspace = context.workspace_path
        return (
            _ListFilesTool(workspace, self._max_file_entries),
            _FindFilesTool(workspace, self._max_file_entries),
            _SearchFilesTool(
                workspace,
                self._max_search_matches,
                self._max_search_result_bytes,
            ),
            _ReadFileTool(workspace, self._max_read_file_bytes),
            _WriteFileTool(workspace, self._max_write_file_bytes),
            _CreateDirectoryTool(workspace),
            _MovePathTool(workspace),
            _DeletePathTool(workspace),
            _ApplyPatchTool(
                workspace,
                self._max_patch_bytes,
                self._max_patch_files,
                self._max_write_file_bytes,
            ),
        )
