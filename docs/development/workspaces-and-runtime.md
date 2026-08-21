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

Atomic replacement does not provide cross-process concurrency control. Two
processes can both read the same history and later replace one another's
updates. Avoid mutating one session concurrently from separate Ethos
processes unless cross-process locking is added.

### Archival

Archival sets `archived_at` and preserves the full history. It is idempotent:
archiving an already archived session returns it unchanged.

Archived sessions remain listable and readable, but message replacement and
new runtime turns reject them. Archival is therefore a terminal conversation
state, not deletion.

## One runtime turn

`AgentRuntime` owns a model factory and a map of `asyncio.Lock` values keyed by
`(workspace_name, session_id)`.

For each turn it:

1. acquires the session's lock;
2. reloads the session from disk;
3. rejects an archived session;
4. rejects stored tool calls without exactly one later result;
5. loads the application settings and constructs the selected model;
6. appends one user text message to the stored history in memory;
7. advertises registered tools when the model supports them;
8. streams and validates responses up to the configured round limit;
9. checkpoints an assistant tool-call response;
10. executes its calls sequentially through the mandatory tool policy and
    checkpoints each result;
11. atomically persists the final assistant response;
12. yields aggregate usage in one final event with `done=True`.

`AgentRuntime` accepts per-instance model-round and per-response tool-call
limits. They default to eight rounds and sixteen calls respectively, and both
must be positive.

Models do not hold conversation history. The complete history is supplied in
an Ethos `ModelRequest` for every run, which keeps sessions isolated even when
the same runtime object handles several conversations.

### Concurrency guarantee

Turns for the same workspace and session are serialised. The second turn
reloads history only after the first turn has persisted it. Different sessions
may run concurrently.

The locks belong to one `AgentRuntime` instance and one process. They do not
coordinate a CLI process with a running Vox process, or two separately
constructed runtimes.

### Completion and failure

A response without tool calls is persisted only after its provider stream
finishes normally. A response with tool calls is checkpointed before tool
execution, and every result is a separate checkpoint. If execution fails or
is cancelled, the latest successful checkpoint remains durable. A later user
turn rejects an assistant tool call without exactly one stored result.

Text may already have reached a caller before completion or a later
persistence failure. Streamed output therefore does not by itself prove that
the turn was committed. The final `done=True` event is emitted only after the
history replacement succeeds.

Under the normal `session.chat` path, the lifecycle event is emitted after
that final runtime event. A completed `session.chat` event therefore describes
the newly persisted session state.

## Contributor invariants

Changes in this area must preserve these rules unless deliberately redesigning
and documenting them:

- A session never changes its owning workspace.
- Archived session history is readable but not mutable.
- One process never overlaps two turns for the same session.
- Different sessions are allowed to run concurrently.
- Invalid or redirected filesystem state fails closed.
- A final runtime completion event follows successful history persistence.
- Atomic file replacement is not represented as a cross-process transaction.
