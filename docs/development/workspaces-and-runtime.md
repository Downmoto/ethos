# Workspaces and runtime

[Developer documentation](index.md)

Workspaces identify user-owned project directories. Sessions are scoped to a
workspace but stored in the Ethos home. The runtime joins them for one model
turn using the application settings.

Keeping these responsibilities separate is important:

- `WorkspaceManager` validates identity and filesystem structure.
- `SessionManager` persists conversation history.
- `AgentRuntime` performs model work and serialises in-process turns.

## Workspace layout

An Ethos home contains application state, configuration, and user-owned
workspace directories:

```text
~/.ethos/
├── config.yaml
├── tools.yaml
├── data/
│   └── ethos.db
├── skills/
│   └── <skill>/
│       └── SKILL.md
├── sessions/
│   └── <workspace>/
│       └── <canonical UUID>.json
└── workspaces/
    └── <name>/
        └── <user-owned files and directories>
```

The complete workspace root is user-owned. Ethos creates the directory but
does not add metadata, configuration, or session state inside it. Contributors
must not assume it is empty or safe to rewrite.

`default` is created during home initialisation and is reserved from explicit
workspace creation. Other names use lower-case letters, digits, and internal
hyphens, with a maximum length of 63 characters. This produces stable,
portable directory names and prevents path-like input.

## Filesystem trust boundary

Workspaces, home-level session directories, and session files must not be
symbolic links.

Rejecting symlinks prevents workspace names from redirecting Ethos outside its
injected roots.

## Resolving effective settings

`get_settings()` loads and validates settings in this precedence order, from
lowest to highest:

1. `~/.ethos/config.yaml`
2. `ETHOS_*` environment variables

Environment values are recursively merged over the YAML configuration, so an
operator can replace one nested value without copying its siblings.

All layers are validated as one `EthosSettings` value. Unknown configuration
fields are errors rather than ignored typos.

Settings are cached for the process and are not embedded in session records.

## Session records

Each session is an immutable Pydantic value containing:

- a UUID;
- its owning workspace name;
- creation and optional archival timestamps;
- the complete Ethos message history.
- durable tool approvals, including the originating runtime `run_id`.

The workspace association is permanent. Loading verifies that the requested
workspace exists, the supplied UUID is in canonical lower-case form, the
filename matches the deserialised UUID, and the stored workspace name matches
the containing workspace.

These checks stop a copied, renamed, or malformed session file from silently
crossing workspace boundaries.

### Why sessions are files

Session JSON lives under `~/.ethos/sessions/<workspace>`, keeping generated
state out of the user-owned workspace while remaining inspectable without the
application database. The Turso database has a different role: it stores
cross-cutting lifecycle events for the application.

### Atomic replacement

Creation and updates are written to a uniquely named temporary file in the
session directory, permissioned to `0600`, and replaced into the final path.
Readers therefore see the old complete record or the new complete record, not
a partially written JSON document.

Atomic replacement alone does not provide cross-process concurrency control.
Runtime turns and approval resolutions additionally hold the session's OS file
lock, preventing two processes from replacing the same runtime history.
Administrative session mutations outside that runtime boundary are not part of
the same cross-process transaction.

### Archival

Archival sets `archived_at` and preserves the full history. It is idempotent:
archiving an already archived session returns it unchanged.

Archived sessions remain listable and readable, but message replacement and
new runtime turns reject them. Archival is therefore a terminal conversation
state, not deletion.

## One runtime turn

`AgentRuntime` owns a model factory and a map of `asyncio.Lock` values keyed by
`(workspace_name, session_id)`. It also holds a non-blocking OS file lock for
the session while a turn or approval resolution is active.

For each turn it:

1. acquires the session's lock;
2. reloads the session from disk;
3. rejects an archived session;
4. rejects stored tool calls without exactly one later result;
5. resolves capability instructions and tools for the active workspace and
   session;
6. loads the application settings and constructs the selected model;
7. appends one user text message to the stored history in memory;
8. advertises the composed tools when the model supports them;
9. streams and validates responses up to the configured round limit;
10. checkpoints an assistant tool-call response;
11. executes allowed calls sequentially through the mandatory tool policy and
    checkpoints each result;
12. persists a write call as `pending` before emitting its approval event;
13. resumes an approved or denied request without accepting a new user turn;
14. atomically persists the final assistant response;
15. yields aggregate usage in one final event with `done=True`.

`AgentRuntime` accepts per-instance model-round and per-response tool-call
limits. They default to eight rounds and sixteen calls respectively, and both
must be positive.

Models do not hold conversation history. The complete history is supplied in
an Ethos `ModelRequest` for every run. `ContextBuilder` constructs that request
from its base Ethos system instruction, local date/time information, run-only
workspace and session context, capability instructions, stored messages, and
available tool definitions.
Constructed instructions are never added to canonical session history. This
keeps sessions isolated even when the same runtime object handles several
conversations.

Capabilities resolve once per turn in registration order. Each receives only
the active workspace name and path plus the session ID, and contributes
run-only instructions and tools. Their tools form a fresh registry for that
turn, so duplicate names fail before model or tool execution and one session's
tool instances cannot leak into another.

The default read-only filesystem capability contributes `list_files` and
`read_file`. Both resolve relative paths beneath the active workspace.
`list_files` returns a sorted JSON array for one directory and rejects more
than its configured entry limit; `read_file` reads UTF-8 text up to its
configured byte limit. Set `capabilities.read_only_file_system.enabled` to
`false` to omit both tools. Absolute
paths, incompatible path types, traversal, and symlinks resolving outside the
workspace are rejected. All calls still pass through the standard tool
executor and policy.

The skills capability follows the Agent Skills progressive-disclosure model.
It scans direct children of the native and cross-client locations at both user
and project scope:

1. `~/.agents/skills/`
2. `~/.ethos/skills/`
3. `<workspace>/.agents/skills/`
4. `<workspace>/.ethos/skills/`

Later locations take precedence, so project skills override user skills;
collisions and cosmetic specification violations emit `skill.diagnostic`
events. Files with unparseable YAML or missing names or descriptions are
skipped without blocking other valid skills. Discovery retains only each
skill's name, description, and absolute `SKILL.md` location. It reads only a
bounded frontmatter prefix and discloses at most the configured number of
skills, preferring later locations when the limit is reached.

When skills exist, `ContextBuilder` receives a name-and-description catalogue.
The capability also contributes the read-only `activate_skill` and
`read_skill_resource_file` tools. The first loads one complete instruction
body and lists a configured maximum number of bundled resources without
reading them. The second reads one referenced UTF-8 file up to the configured
byte limit while rejecting absolute paths and escapes from the skill
directory. The limits are `capabilities.skills.max_resources` and
`capabilities.skills.max_resource_file_bytes` in `config.yaml`. Activation is
also limited by `capabilities.skills.max_skill_file_bytes`, and discovery by
that same frontmatter bound plus `capabilities.skills.max_skills`. No
catalogue or skill tools are added when discovery finds nothing or
`capabilities.skills.enabled` is `false`.

### Concurrency guarantee

Turns for the same workspace and session are serialised across runtime
instances and processes. A competing runtime fails closed with `session runtime
is busy`; it never waits while holding the event loop. Different sessions may
run concurrently.

The in-process lock provides orderly waiting within one runtime. The
per-session file lock prevents a CLI process, Vox process, or separately
constructed runtime from claiming the same approval concurrently.

### Completion and failure

A response without tool calls is persisted only after its provider stream
finishes normally. A response with tool calls is checkpointed before tool
execution, and every result is a separate checkpoint. If execution fails or
is cancelled, the latest successful checkpoint remains durable. A later user
turn rejects an assistant tool call without exactly one stored result until an
operator explicitly repairs it with the CLI command
`ethos session recover <workspace> <session>` or Vox endpoint
`POST /workspaces/{workspace}/sessions/{session}/recover`.

Write calls persist the original call, tool name, validated arguments, effect,
reason, creation time, round, and usage. Approval atomically changes `pending`
to `executing` before the tool runs. Completion atomically stores `completed`
and its result; denial stores `denied` and an error result. If the process dies
while `executing`, the OS releases its lock and the next runtime load persists
`indeterminate`. That state can never execute automatically. Pending requests
survive restart, and every approval is single-use and bound to its exact call
payload.

Session recovery never runs or replays a tool. It closes each unresolved call
with a durable error result stating that the execution outcome is unknown,
marks pending approvals denied, and preserves interrupted executions as
`indeterminate`. The repaired history can then accept a new user turn. Running
recovery on a session without unresolved calls fails without changing it.

Text may already have reached a caller before completion or a later
persistence failure. Streamed output therefore does not by itself prove that
the turn was committed. The final `done=True` event is emitted only after the
history replacement succeeds.

Reasoning has a configurable answer-now deadline. It starts on the first
reasoning delta and is removed when answer text begins. On expiry, the runtime
cancels that request and retries once with reasoning disabled and a temporary
system instruction to finish promptly. Abandoned reasoning is visible to the
current stream but is not persisted or replayed. The retry retains available
tools and counts as a model round. If the retry pauses for tool approval, its
answer-now phase is persisted with the approval and restored on either approval
or denial, including after a process restart.

Under the normal `session.chat` path, the lifecycle event is emitted after
that final runtime event. A completed `session.chat` event therefore describes
the newly persisted session state.

### Runtime traces

Every user turn has one UUID `run_id`. The same identifier is stored with a
pending approval, so a later approval resolution or indeterminate recovery
continues the original trace even after a process restart. Model rounds are
one-based, and tool events add call, tool, and approval identifiers as
applicable.

The event database uses the existing envelope and emitter for these ordered
runtime events:

- run start, pause, resume, completion, and failure;
- model-request start, completion, and failure;
- tool-call request and preparation;
- tool-execution start and completion;
- approval request, approval, denial, and indeterminate recovery.

Runtime payloads use schema `runtime.trace` version 1. They deliberately omit
prompts, answers, reasoning, tool arguments and result content, credentials,
headers, and exception messages. Failures use stable categories instead.
These events are internal tracing records and do not appear in CLI or Vox
streams.

Each stored envelope receives a database-generated sequence number. Diagnostic
queries reconstruct the trace with `ORDER BY sequence`; timestamps are not an
ordering guarantee. Event UUIDs remain unique identities, while workspace,
session, run, call, tool, and approval correlation fields live only in the
typed payload. The envelope has no duplicate source-detail or tag fields.

Emission is synchronous and durable-first. A tool cannot run unless its
execution-start event was stored successfully, and an execution-completed
event follows the durable result. If event delivery fails after a side effect,
the persisted result or consumed approval is retained and never replayed
automatically. Cancellation or process death may leave a trace without a
terminal event; that accurately represents incomplete work.

## Contributor invariants

Changes in this area must preserve these rules unless deliberately redesigning
and documenting them:

- A session never changes its owning workspace.
- Archived session history is readable but not mutable.
- No process overlaps another runtime turn for the same session.
- Different sessions are allowed to run concurrently.
- Invalid or redirected filesystem state fails closed.
- A final runtime completion event follows successful history persistence.
- Answer-now fallback is attempted at most once per run.
- Interrupted tool recovery never executes or replays a tool.
- Atomic file replacement is not represented as a cross-process transaction.
