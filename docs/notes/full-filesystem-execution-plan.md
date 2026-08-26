# Full filesystem execution plan

> Local planning note. Review before execution and never commit this file.

## Objective

Replace the current read-only filesystem capability with a complete,
workspace-bounded filesystem capability that can efficiently explore and edit
a codebase while routing every mutation through the existing write-tool
approval and recovery flow.

This completes the third delivery item in Milestone 1. The implementation must
remain provider-independent and use the existing capability, tool, service,
CLI, and Vox boundaries rather than introducing a parallel filesystem path.

## Final tool surface

### Read tools

#### `list_files`

List the immediate children of one directory.

- Arguments: `path` defaulting to `"."`.
- Return a sorted JSON array of workspace-relative paths.
- Retain the current trailing `/` marker for directories.
- Include files and directories, despite retaining the established tool name.
- Do not recurse or follow directory symlinks.
- Enforce the configured entry limit.

#### `find_files`

Recursively find paths by glob pattern without requiring shell approval.

- Arguments: `pattern` and optional `path` defaulting to `"."`.
- Match against workspace-relative POSIX paths beneath `path`.
- Return a sorted JSON array of workspace-relative paths.
- Mark directories with a trailing `/`.
- Do not traverse symlinked directories.
- Enforce the configured entry limit and fail rather than silently truncate.
- Use the Python standard library; add no search dependency.

#### `search_files`

Search UTF-8 text files without requiring shell approval.

- Arguments: `pattern`, optional `path` defaulting to `"."`, optional
  `include` glob, and `literal` defaulting to `false`.
- Treat `pattern` as a regular expression unless `literal` is true.
- Return bounded JSON records containing `path`, `line`, and `text`.
- Skip binary and non-UTF-8 files rather than failing the entire search.
- Do not traverse symlinked directories.
- Bound matches and total returned bytes; fail clearly when either ceiling is
  exceeded rather than returning an apparently complete partial result.
- Reject invalid regular expressions as safe tool execution errors.
- Use the Python standard library; add no search dependency.

#### `read_file`

Keep the existing bounded UTF-8 read and add ranged reading.

- Arguments: `path`, optional one-based `start_line`, and optional `end_line`.
- Require `end_line >= start_line` when both are supplied.
- Preserve the existing plain-text result.
- Stream the requested range so a large file can be read in bounded chunks.
- Enforce the configured byte limit on each returned range.
- Preserve current errors for non-files, non-UTF-8 content, oversized results,
  absolute paths, traversal, and boundary escapes.

### Write tools

Every tool in this section has `ToolEffect.WRITE` and therefore requires the
existing policy approval before execution.

#### `write_file`

Create or completely replace one UTF-8 text file.

- Arguments: `path` and `content`.
- Require the parent directory to exist; directory creation remains explicit.
- Enforce the configured encoded byte limit before writing.
- Reject directories and symlink targets.
- Write to a sibling temporary file and atomically replace the destination.
- Preserve the existing file mode when replacing a file.
- Return whether the path was created or updated.

#### `create_directory`

Create one directory path.

- Argument: `path`.
- Create missing parents, matching `mkdir -p` behaviour.
- Succeed idempotently when the target is already a directory.
- Reject an existing non-directory, symlink, workspace root, or boundary
  escape.
- Return the created workspace-relative path or that it already existed.

#### `move_path`

Move or rename one file or directory.

- Arguments: `source` and `destination`.
- Support regular files and directories.
- Require the destination parent to exist.
- Never overwrite an existing destination.
- Reject the workspace root, symlinks, moves into a source directory's own
  subtree, and all boundary escapes.
- Use the native atomic rename when possible. Treat an unexpected failure as
  indeterminate through the existing write-tool runtime path.
- Return both workspace-relative paths.

#### `delete_path`

Delete one file or directory.

- Arguments: `path` and `recursive` defaulting to `false`.
- Delete regular files and empty directories without `recursive`.
- Require `recursive=true` for non-empty directories.
- Reject the workspace root, symlinks, missing paths, and boundary escapes.
- Never follow symlinks while recursively deleting.
- Return the deleted workspace-relative path and path type.

#### `apply_patch`

Apply structured text diffs as the primary localized editing operation. Build
this last, after the simpler mutations and their shared safety helpers are
stable.

- Argument: one bounded `patch` string.
- Accept the familiar `*** Begin Patch` / `*** End Patch` envelope with
  `*** Add File`, `*** Update File`, and `*** Delete File` sections.
- Support multiple hunks and multiple files in one call.
- Do not add patch-specific move syntax; `move_path` already covers renames.
- Require exact context matches and reject ambiguous or stale hunks.
- Reject duplicate target sections, malformed patches, empty patches,
  unsupported directives, binary/non-UTF-8 targets, symlinks, and boundary
  escapes.
- Bound patch bytes, affected file count, individual resulting file bytes, and
  result bytes.
- Parse the full patch, resolve every path, load every target, and validate
  every hunk before making any change.
- Build all resulting contents in memory and prepare sibling temporary files
  before replacing targets.
- Atomically replace each created or updated file. Cross-file atomicity is not
  promised; an unexpected mid-application failure remains indeterminate and
  uses the runtime's recoverable approval state.
- Return a bounded summary of created, updated, and deleted paths without
  echoing file contents.

## Capability and configuration changes

Rename the capability now that it is no longer read-only:

- `ReadOnlyFilesystemCapability` becomes `FilesystemCapability`.
- `CapabilityName.READ_ONLY_FILE_SYSTEM` becomes
  `CapabilityName.FILE_SYSTEM` with value `file_system`.
- `ReadOnlyFilesystemCapabilityConfig` becomes
  `FilesystemCapabilityConfig`.
- `ReadOnlyFilesystemCapabilityOverride` becomes
  `FilesystemCapabilityOverride`.
- YAML fields named `read_only_file_system` become `file_system` globally and
  per workspace.
- Update the template, service resolver, CLI choices, Vox behaviour, tests,
  and documentation together.

Do not retain a legacy alias or migration layer. Ethos is pre-beta, the old
name was introduced only in the preceding milestone step, and accepting two
canonical names would permanently complicate configuration management.

Use a small set of configurable ceilings rather than one setting per tool:

- `max_read_file_bytes`: maximum bytes returned by `read_file`.
- `max_write_file_bytes`: maximum UTF-8 bytes in a written or patched file.
- `max_file_entries`: shared result ceiling for `list_files` and `find_files`.
- `max_search_matches`: maximum records returned by `search_files`.
- `max_search_result_bytes`: maximum encoded search result size.
- `max_patch_bytes`: maximum incoming patch size.
- `max_patch_files`: maximum paths affected by one patch.

Global values remain ceilings. Workspace overrides may disable the capability
or lower numeric values, using the manager's current intersection behaviour.

## Shared filesystem safety layer

Refactor `src/ethos/capabilities/filesystem.py` only as much as necessary to
avoid implementing path security separately in every tool. Keep the feature
in one module unless its final size makes the patch parser materially easier
to test as a private sibling module.

Provide separate internal resolution paths for:

- Existing read targets, which may follow symlinks only when the fully resolved
  path remains inside the canonical workspace.
- Existing mutation targets, which must reject symlinks and the workspace
  root.
- New destinations, which resolve the existing parent, enforce workspace
  containment, reject a symlink leaf, and do not require the leaf to exist.

All operations must:

- Reject absolute paths.
- Resolve `.` and `..` before checking containment.
- Treat the canonical workspace root as the sole trust boundary.
- Reject incompatible path types with operation-specific safe errors.
- Avoid following symlinked directories during recursive traversal.
- Perform blocking filesystem work through `asyncio.to_thread`.
- Return bounded, deterministic, model-facing results.
- Avoid exposing host paths or raw exception details.

Add one shared atomic UTF-8 replacement helper using a sibling temporary file,
mode preservation, cleanup in `finally`, and `Path.replace`. Do not introduce
a filesystem abstraction, transaction framework, or new dependency.

## Capability instructions

Replace the current single sentence with concise operational guidance that
tells the model:

- Every path is relative to the workspace root and absolute paths are invalid.
- `list_files` is shallow; use `find_files` for recursive path discovery and
  `search_files` for content discovery.
- Directory entries end in `/`.
- Use ranged `read_file` calls for large files.
- Filesystem content tools support UTF-8 text only.
- Use `apply_patch` for localized edits and `write_file` for new files or full
  replacements.
- Use explicit directory, move, and delete tools for structural changes.
- Mutations require approval, moves never overwrite, and recursive deletion
  must be explicitly requested.

Keep instructions descriptive rather than embedding the patch grammar; the
`apply_patch` tool description owns that syntax.

## Execution sequence

### 1. Rename and configure the capability

- Rename the public enum, Pydantic settings and override models, aggregate
  fields, service resolver references, template section, and implementation
  class.
- Add and validate the new shared limits.
- Update capability manager tests for global configuration, narrowed workspace
  overrides, unknown legacy names, and serialization.
- Update service, CLI, and Vox tests to use `file_system`.

### 2. Establish shared path and atomic-write helpers

- Split existing-path and destination-path resolution.
- Add explicit mutation guards for roots, symlinks, and incompatible types.
- Add atomic UTF-8 replacement with byte checking and mode preservation.
- Exercise the helpers through public tools rather than creating a large
  private-helper test suite.

### 3. Complete read and discovery tools

- Preserve `list_files` behaviour while moving it to the renamed capability.
- Add ranged `read_file` support.
- Add `find_files` with bounded, deterministic glob results.
- Add `search_files` with bounded line-oriented results.
- Confirm every read tool remains `ToolEffect.READ` and needs no approval under
  the default policy.

### 4. Add simple mutation tools

- Implement `write_file` and `create_directory` first.
- Implement `move_path` and `delete_path` after destination and recursive-path
  cases are covered.
- Confirm every mutation produces `RequireApproval` through the existing tool
  executor before direct execution tests exercise its filesystem behaviour.

### 5. Add `apply_patch`

- Implement the bounded parser and internal patch representation.
- Validate all sections and hunks before filesystem mutation.
- Reuse the shared path and atomic-write helpers.
- Cover single- and multi-file create/update/delete patches.
- Cover malformed, stale, ambiguous, oversized, escaping, symlinked, and
  partially invalid multi-file patches.

### 6. Verify runtime approval and recovery behaviour

- Exercise one representative filesystem write through normal runtime
  preparation, pending approval persistence, approval resumption, completion,
  denial, cancellation, and indeterminate failure paths.
- Reuse generic tool approval coverage where behaviour is already proven;
  avoid repeating the entire runtime suite for every filesystem tool.
- Confirm CLI and Vox expose the same pending approval and final result because
  both already use the shared service/runtime path.

### 7. Update committed documentation

- Update the architecture and workspace/runtime documents from read-only to
  full filesystem behaviour.
- Document the final tool names, arguments, result bounds, configuration
  values, approval behaviour, and workspace/symlink boundary.
- Update the capability template comments.
- Update the roadmap only if its wording no longer matches the delivered
  surface.
- Do not include or commit this execution-plan file.

### 8. Run final verification

- Run focused filesystem, capability configuration, service, CLI, Vox,
  runtime, and tool-policy tests while iterating.
- Run formatting and static analysis through the repository verification
  script.
- Run `git diff --check`.
- Inspect the final diff for leaked absolute paths, unbounded results, missed
  `read_only_file_system` references, and unrelated changes.
- Confirm this plan remains untracked and unstaged before any commit.

## Required test matrix

### Common boundaries

- Relative paths at the workspace root and in nested directories.
- Absolute paths, `..` traversal, missing parents, and incompatible types.
- Symlinks resolving both inside and outside the workspace.
- Workspace-root mutation attempts.
- Unicode names and UTF-8 content.
- Deterministic ordering and every configured limit.

### Reads and discovery

- Directory markers and shallow listing.
- Recursive glob matches and no symlink-directory traversal.
- Regex and literal searches, include filters, invalid regex, line numbers,
  binary skips, and result limits.
- Whole and ranged reads, invalid ranges, oversized ranges, and large files
  read successfully in chunks.

### Mutations

- New and replaced files, byte limits, atomic cleanup, and mode preservation.
- Nested and idempotent directory creation.
- File and directory moves, collisions, missing destination parents, and
  self-descendant moves.
- File, empty-directory, and recursive-directory deletion.
- Denied and cancelled approvals causing no filesystem change.
- Unexpected write failures surfacing as indeterminate rather than definitive
  success or failure.

### Patches

- One and multiple hunks.
- Add, update, and delete sections in single- and multi-file patches.
- Newline-at-end-of-file handling.
- Exact context matching and stale-context rejection.
- Duplicate paths, malformed headers, unsupported directives, empty patches,
  size/file-count limits, and mixed-validity patches causing no changes.

## Completion criteria

- The runtime contributes exactly the nine agreed filesystem tools:
  `list_files`, `find_files`, `search_files`, `read_file`, `write_file`,
  `create_directory`, `move_path`, `delete_path`, and `apply_patch`.
- Read tools execute without approval and all mutations require approval.
- All tools remain bounded to the active workspace and produce bounded output.
- File replacements never expose partially written content.
- Capability configuration is manageable as `file_system` globally and per
  workspace through the manager, service, CLI, and Vox.
- Destructive, approval, cancellation, and indeterminate cases are tested.
- User-facing and developer documentation matches the implementation.
- The complete repository verification suite passes.
- This local plan is neither staged nor committed.
