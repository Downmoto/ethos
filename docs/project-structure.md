# cassiopeia project structure

This document defines the intended source layout for cassiopeia 1.0 and later
features. It is a working architecture reference, not a permanent constraint: if
product needs change, update this document alongside the code so the project does
not drift by accident.

The 1.0 product boundary is defined in `docs/cassiopeia-1.0-scope.md`. This
document translates that scope into package boundaries, dependency direction, and
where new code should live.

## Goals

- Keep gateways, workflows, providers, storage, memory, permissions, and agent
  execution behind clear interfaces.
- Keep user-authored JSON definitions separate from runtime state and storage
  implementation details.
- Make the TUI, Telegram, Discord, and future gateways access points into the
  same core runtime.
- Keep the CLI as an administration and debugging surface, not a gateway.
- Make feature ownership obvious enough that new 1.0 work has a natural home.
- Avoid circular imports by enforcing simple dependency direction.

## Package boundary

All long-term application code should live under `src/cassiopeia/`.

CLI code lives under `cassiopeia.cli`, and the console script points at
`cassiopeia.cli:main`.

## Dependency direction

Code should depend inward, from delivery surfaces into application services and
feature-owned models:

```text
cli / gateways / tui
  -> app services
    -> runtime orchestration
      -> feature packages, shared types, and ports
        -> storage/provider/tool/skill implementations through interfaces
```

Rules:

- Feature models should not import gateways, CLI, TUI, storage backends, or model
  provider implementations.
- Application services coordinate feature packages and ports. They should not embed
  Click, Textual, Telegram, Discord, or SQL-specific concerns.
- Gateways render prompts, memory notices, workflow status, and responses. They
  should not decide core permissions, memory ranking, persona selection, or
  workflow semantics.
- Storage implementation details stay inside the storage package. Other packages
  depend on repository protocols or service interfaces.
- Provider implementation details stay inside the provider package. Runtime code
  depends on provider capabilities and ports, not vendor SDKs directly.
- Workflow script execution, shell access, web access, and other risky actions
  pass through the permissions/security layer rather than bypassing it.

## Target `src/cassiopeia/` layout

```text
src/cassiopeia/
  __init__.py
  app/
  cli/
  config/
  events/
  gateways/
  home/
  memory/
  personas/
  permissions/
  providers/
  runtime/
  shared.py
  sessions/
  skills/
  storage/
  subagents/
  tools/
  tui/
  workspaces/
  workflows/
```

The names above are stable architectural homes. A package should start as a
single module when that is enough, then become a directory package when the
feature needs multiple files.

## Package responsibilities

### `app/`

Application service layer and use cases. This package coordinates operations
such as creating a session, handling an inbound message, running a workflow, or
promoting a temporary worker to a persona.

`app/` may depend on feature packages, shared types, runtime orchestration, and
port interfaces.
It should not contain Click commands, Textual widgets, Telegram handlers, Discord
handlers, SQL statements, or provider SDK calls.

### `cli/`

Click commands for administration and debugging.

The CLI may call application services and inspect storage through approved
interfaces. It is not a gateway and should not grow independent chat/session
semantics that bypass the shared runtime. User-facing command text should use
Canadian spelling.

### `config/`

Settings models, config loading, config file validation, environment variable
handling, and typed access to global and workspace configuration.

Configuration models define what can be loaded. They should not perform runtime
actions such as creating sessions, connecting gateways, or running providers.

### `events/`

The typed event catalogue, event envelopes, event payload models, emitter
interfaces, listener shell, and event persistence boundary.

Feature packages emit meaningful lifecycle events through this package. Full
user-authored hook matching and workflow dispatch belong in `workflows/` and
`workflows/hooks` once that milestone is reached.

### `shared.py`

Small shared primitives that are genuinely cross-cutting, such as common id
types, timestamp helpers, slugs, scopes, pagination values, or result types.

This file is not a dumping ground for feature models. If a type belongs to a
specific concept, keep it with that feature package:

- workspace records and workspace policy live in `workspaces/`
- persona definitions live in `personas/`
- memory models live in `memory/`
- permission models live in `permissions/`
- workflow and hook models live in `workflows/`
- gateway identity and binding models live in `gateways/`
- provider profile and capability models live in `providers/`
- session and message records live in `sessions/`

### `gateways/`

Gateway-neutral contracts plus concrete external gateway implementations.

Suggested internal layout:

```text
gateways/
  base.py
  sessions.py
  telegram.py
  discord.py
```

Gateway code normalises inbound platform events, resolves or creates sessions
through app services, and renders outbound responses, permission prompts, memory
notices, workflow status, and errors.

### `home/`

Creation, inspection, migration, and repair of the `~/.cassiopeia/` home layout.

This includes directory and starter-file definitions for global personas,
skills, workflows, gateway bindings, hooks, permissions, runtime data, and
workspace-local configuration.

### `memory/`

Memory creation, exposure/rejection flows, retrieval policy, ranking,
summarisation support, embedding coordination, and memory-specific services.

Memory storage access should go through repository interfaces. Embedding calls
should go through the embedding provider abstraction, not directly to a vendor
SDK.

### `personas/`

Persona definition models, JSON loading/validation, persona availability,
persona selection helpers, persona tool/workflow/memory policies, and promotion
support for saved temporary workers.

This package owns what a persona is. `runtime/` owns turning a selected persona
into an executable agent turn.

### `permissions/`

Security rings, action definitions, grant evaluation, prompt requests, audit
records, and policy enforcement helpers.

Gateways display permission prompts, but this package decides whether an action
requires a prompt and what choices are valid.

### `providers/`

Model and embedding provider abstractions, provider capability metadata, and
concrete OpenAI, Ollama, and optional future provider adapters.

Pydantic AI integration belongs here unless it becomes part of the broader agent
runtime. Runtime orchestration should call provider interfaces and capability
checks rather than vendor SDKs directly.

### `runtime/`

Agent execution, persona instantiation, context packet construction, session
turn handling, history windowing, memory retrieval integration, tool calling,
structured output handling, and Pydantic Graph/Pydantic AI orchestration.

`runtime/` is where a selected persona becomes an executable agent turn. It
should call tools, memory, permissions, providers, workflows, and subagents
through explicit interfaces.

### `sessions/`

Session definition models, message records, session lifecycle operations,
history windowing inputs, gateway-origin mapping, and session-scoped state such
as active grants or recent workflow/subagent runs.

This package owns persistent conversation/work context. Gateways ask app
services to resolve or create sessions instead of constructing them directly.

### `skills/`

Agent Skills spec indexing, metadata parsing, skill assignment to personas, lazy
loading of full skill instructions, and access to referenced skill resources.

This package should treat the Agent Skills format as external. It should avoid
forking or redefining that specification.

### `storage/`

Runtime state persistence interfaces and implementations.

Suggested internal layout:

```text
storage/
  ports.py
  models.py
  migrations.py
  libsql/
```

Application and runtime code should depend on storage ports, not concrete
Turso/libSQL classes. SQL, transaction retry logic, migrations, vector index
details, and backend-specific errors stay here.

### `subagents/`

Task-scoped subagent creation, persona matching, temporary worker definitions,
bounded context packet creation, result handling, and promotion proposals.

Subagents are not independent conversation participants in 1.0. They report back
to the orchestrating persona through runtime/app services.

### `tools/`

Tool definitions, tool registry, tool execution contracts, built-in tool
implementations, and security metadata for executable capabilities.

Tools should expose enough metadata for persona whitelists, workspace
availability, workflow requirements, and security ring checks.

### `tui/`

The minimal Textual local interactive gateway. It should use the same app
services and runtime contracts as external gateways.

TUI widgets and screens stay here. Core session, memory, permission, workflow,
and persona decisions stay outside the TUI package.

### `workspaces/`

Workspace definition models, workspace configuration loading/validation,
workspace manager selection, workspace-specific feature availability, root path
handling, and workspace-local definition discovery.

This package owns workspace boundaries and policy. It should not run agents,
gateways, storage backends, or workflows directly.

### `workflows/`

Workflow definition models, validation, graph execution, node implementations,
script node handling, workflow runs, hook registry loading, hook matching, and
event-to-workflow dispatch.

Suggested internal layout:

```text
workflows/
  definitions.py
  engine.py
  hooks.py
  nodes/
```

Workflow node execution must respect tool availability, provider capabilities,
memory policy, and security rings.

## Test layout

Tests should mirror the package layout where practical:

```text
tests/
  app/
  cli/
  config/
  events/
  gateways/
  home/
  memory/
  personas/
  permissions/
  providers/
  runtime/
  sessions/
  skills/
  storage/
  subagents/
  tools/
  tui/
  workspaces/
  workflows/
```

Small early tests may remain as flat `tests/test_*.py` files. When a feature
grows beyond one or two tests, move it into the matching package directory.

## Definition files and runtime data

User-authored definitions live as JSON files under `~/.cassiopeia/` and
workspace-specific subdirectories. Runtime state belongs under
`~/.cassiopeia/data/` and should be accessed through storage interfaces.

Use JSON files for:

- global and workspace personas
- global and workspace workflows
- global and workspace hook registries
- workspace configuration
- gateway bindings
- user-editable permission policy or grant configuration where appropriate

Use runtime storage for:

- sessions and message history
- event history
- memory records and embeddings
- permission grant audit records
- workflow runs
- subagent runs
- indexes and relationship tables

## Migration guidance

- Prefer adding new code in the target package even if an older flat module
  exists.
- Move a flat module into a package when a second closely related module is
  needed.
- Keep public imports stable with temporary re-export modules when a move would
  otherwise create noisy churn.
- Do not introduce broad abstraction layers before a real second implementation
  or clear ownership boundary exists.
- Update this document when a product decision changes package ownership.

## Initial refactor candidates

The current early code can migrate toward this layout incrementally:

- `src/cassiopeia/config.py` -> `src/cassiopeia/config/__init__.py` or
  `src/cassiopeia/config/settings.py`
- `src/cassiopeia/home.py` -> `src/cassiopeia/home/__init__.py` or
  `src/cassiopeia/home/layout.py`
- `src/cassiopeia/events.py` -> `src/cassiopeia/events/__init__.py` plus
  focused modules when the emitter/listener shell is added

These moves are not required before milestone 3 work can continue. They should
be done when they reduce friction, not as a standalone reshuffle.
