# Workspaces and runtime

[Developer documentation](index.md)

Workspaces are the unit of agent configuration and capability policy. Sessions
are workspace-owned conversations. The runtime joins them for one model turn.

Keeping these responsibilities separate is important:

- `WorkspaceManager` validates identity and filesystem structure.
- `resolve_workspace_environment` computes current configuration and
  capabilities.
- `SessionManager` persists conversation history.
- `AgentRuntime` performs model work and serialises in-process turns.

## Workspace layout

An Ethos home contains global configuration and installed capabilities:

```text
~/.ethos/
├── config.yaml
├── tools.yaml
├── data/
│   └── ethos.db
├── skills/
│   └── <skill>/
│       └── SKILL.md
└── workspaces/
    └── <name>/
        ├── <user-owned files and directories>
        └── .ethos_workspace/
            ├── ws_config.yaml
            ├── tools.yaml
            ├── skills.yaml
            └── sessions/
                └── <canonical UUID>.json
```

Only `.ethos_workspace` is owned by Ethos. The rest of a workspace root may
contain arbitrary user-defined project content. Contributors must not assume
the workspace root is otherwise empty or safe to rewrite.

`default` is created during home initialisation and is reserved from explicit
workspace creation. Other names use lower-case letters, digits, and internal
hyphens, with a maximum length of 63 characters. This produces stable,
portable directory names and prevents path-like input.

## Filesystem trust boundary

Workspaces, required metadata, session files, installed skill directories, and
skill definitions must not be symbolic links.

This is more than layout validation. Workspace and skill names eventually
select configuration, prompt capabilities, and persisted conversation state.
Rejecting symlinks prevents those names from redirecting Ethos outside their
injected roots.

`WorkspaceManager.get` also rejects incomplete workspace metadata rather than
silently repairing it. Initialisation and creation are explicit operations;
loading existing state must not reinterpret or overwrite user-authored files.

Workspace creation is cleaned up if any metadata file cannot be created. The
manager does not leave a partially initialised workspace that could later be
mistaken for valid state.

## Resolving effective settings

Settings are resolved for every model turn in this precedence order, from
lowest to highest:

1. `~/.ethos/config.yaml`
2. `<workspace>/.ethos_workspace/ws_config.yaml`
3. `ETHOS_*` environment variables

The two YAML files are recursively merged, so a workspace can replace one
nested value without copying its siblings. Environment values are merged last
and therefore remain an operator-level override.

All layers are validated as one `EthosSettings` value. Unknown configuration
fields are errors rather than ignored typos.

This per-turn resolution means an existing session can use updated workspace
settings on its next turn. Settings are not embedded in the session record.

## Tool policy

The tool catalogue is supplied by the application. Each tool belongs to
exactly one named toolset; duplicate tool names across toolsets are rejected
because their policy would otherwise be ambiguous.

Tool selection has two policy layers:

- Global policy in `~/.ethos/tools.yaml`
- Workspace policy in `.ethos_workspace/tools.yaml`

A tool is available only when both layers select it:

```text
effective(tool) = global(tool) AND workspace(tool)
```

The global layer is therefore a ceiling. A workspace may narrow globally
available tools, but it cannot grant itself a tool denied globally.

Within one layer, a tool-specific setting overrides its toolset setting:

```yaml
tools:
  web.fetch: false
toolsets:
  web: true
```

This enables the `web` toolset except for `web.fetch`. A tool or toolset that
is not selected defaults to disabled. Unknown names are errors so a misspelled
policy cannot silently produce unexpected access.

Resolved toolsets are filtered views of the supplied catalogue. The original
catalogue is not mutated.

The current application composition passes an empty catalogue. The policy
machinery is implemented and tested, but adding real tools also requires
registering their toolsets in the composition root.

## Skill selection

Skills are installed globally under `~/.ethos/skills` and selected per
workspace in `.ethos_workspace/skills.yaml`.

An installed skill is a regular directory containing a regular `SKILL.md`.
The skills root, skill directory, and definition must not be symlinks. A
workspace selecting a missing or malformed skill fails environment resolution
instead of running with a partial capability set.

Resolved skills are sorted by name for deterministic behaviour.

At present, selected `Skill` values are carried in `WorkspaceEnvironment`, but
the runtime does not read `SKILL.md` or automatically add it to the model
prompt. Contributors must not assume selection alone activates skill
instructions.

## Workspace-scoped memory

`WorkspaceMemory` pairs the shared application `Storage` connection with the
validated workspace name. It gives future tools and dependencies an explicit
scope instead of passing an unqualified database handle.

The current storage API only writes event envelopes, so workspace memory is a
boundary prepared for scoped access rather than a complete memory feature.
Any future reads or writes through it must retain the workspace predicate.

## Session records

Each session is an immutable Pydantic value containing:

- a UUID;
- its owning workspace name;
- creation and optional archival timestamps;
- the complete Pydantic AI model-message history.

The workspace association is permanent. Loading verifies that the requested
workspace exists, the supplied UUID is in canonical lower-case form, the
filename matches the deserialised UUID, and the stored workspace name matches
the containing workspace.

These checks stop a copied, renamed, or malformed session file from silently
crossing workspace boundaries.

### Why sessions are files

Session JSON lives inside the workspace's Ethos metadata, keeping
conversation state self-contained with the workspace and inspectable without
the application database. The Turso database has a different role: it stores
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

`AgentRuntime` owns one reusable Pydantic AI `Agent` and a map of
`asyncio.Lock` values keyed by `(workspace_name, session_id)`.

For each turn it:

1. acquires the session's lock;
2. reloads the session from disk;
3. rejects an archived session;
4. resolves the current workspace environment;
5. constructs the model selected by the effective settings;
6. starts Pydantic AI with stored message history and the session UUID as the
   provider conversation ID;
7. converts cumulative streamed text into non-overlapping text chunks;
8. yields chunks with copied usage snapshots;
9. replaces the stored history with `result.all_messages()`;
10. yields a final event with `done=True`.

The shared `Agent` does not hold the conversation history. History is supplied
explicitly for every run, which is why sessions remain isolated even when the
same runtime object handles several conversations.

### Concurrency guarantee

Turns for the same workspace and session are serialised. The second turn
reloads history only after the first turn has persisted it. Different sessions
may run concurrently.

The locks belong to one `AgentRuntime` instance and one process. They do not
coordinate a CLI process with a running gateway process, or two separately
constructed runtimes.

### Completion and failure

History is replaced only after the provider's text stream finishes normally.
If provider execution raises, the stream is cancelled, or the caller stops
consuming early, the new history is not persisted.

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
- Workspace settings and capabilities are resolved for each turn.
- Global tool policy cannot be widened by a workspace.
- Invalid or redirected filesystem state fails closed.
- A final runtime completion event follows successful history persistence.
- Atomic file replacement is not represented as a cross-process transaction.
