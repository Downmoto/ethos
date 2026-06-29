# cassiopeia 1.0 Scope

This document captures the current working definition of cassiopeia 1.0. It is
intended to narrow the product, architecture, and feature boundaries before major
implementation work starts.

## Project Identity

cassiopeia is a local-first agent persona and workflow runtime for workflow
automation.

Gateways such as the TUI, Telegram, Discord, and future integrations are access
points into the same core system. The CLI is an administration and debugging
surface, not a gateway. Castellan is an optional and recommended sister app that
wraps cassiopeia in a GUI and adds related workflow surfaces such as a code/text
editor and calendar, but Castellan is not required for cassiopeia 1.0.

cassiopeia owns:

- personas
- sessions and conversation history
- workspace organisation
- gateway routing
- tools
- skills
- hooks
- graph-based workflows
- task-scoped subagents
- permissions and security rings
- memory

## 1.0 Non-Goals

- Rich GUI features belong to Castellan, not cassiopeia 1.0.
- Persistent autonomous multi-agent teams are post-1.0.
- Open-ended subagent conversations are post-1.0.

## 1.0 Cut Line

Required for 1.0:

- `~/.cassiopeia/` home and config layout
- workspaces with `root_path` and workspace manager persona
- JSON personas with skills, tools, workflow, and memory policies
- Agent Skills spec indexing and assignment
- sessions with persisted history
- memory with session, workspace, persona, and user scopes
- Turso/libSQL runtime storage if the spike passes, behind a storage interface
- security rings and permission grants
- JSON workflows with core node types, including script node
- hook registries mapping events to workflows
- CLI administration/debugging surface
- robust install script suitable for curl-based installation
- OpenAI and Ollama providers through the cassiopeia provider abstraction, backed
  by Pydantic AI where it fits
- embeddings as a separate provider abstraction
- minimal TUI as the local interactive gateway
- Telegram gateway
- second external gateway, likely Discord, enough to prove the gateway abstraction

Stretch before 1.0 if time allows:

- OpenRouter provider
- richer Discord polish
- richer TUI views
- workflow import/export
- keychain secret storage
- Nix packaging/install support
- advanced memory ranking controls

Post-1.0:

- persistent autonomous agent teams
- multi-persona conversation rooms
- Castellan GUI
- project-local shared `.cassiopeia` exports as a first-class feature
- advanced gateway features such as voice, rich media, or moderation

## Implementation Milestones

Detailed milestone plans live in `docs/milestones/`. Use
`docs/milestones/template.md` when defining later milestones.
The intended 1.0+ source layout and package ownership rules live in
`docs/project-structure.md`.

1. Project foundation: package layout, config loading, `~/.cassiopeia/` init,
   JSON schema validation, and CLI skeleton. See
   `docs/milestones/01-project-foundation.md`.
2. Storage spike: prove Turso/libSQL for sessions, events, memory, vector
   search, relational relationship queries, and concurrent local writes. Decide
   pass/fail before building the real storage layer. See
   `docs/milestones/02-storage-spike.md`.
3. Event API and minimal hook listener shell: define the typed event envelope,
   event catalogue, emitter interface, persistence boundary, and enough listener
   registration plumbing that later app logic can emit events as it is built.
   See `docs/milestones/03-event-api-and-minimal-hooks.md`.
4. Core domain models: workspaces, personas, sessions, permissions, memories,
   workflows, hooks, and remaining event-adjacent records as typed models.
5. Storage layer: repository interfaces plus Turso/libSQL implementation.
6. Provider layer: OpenAI, Ollama, embeddings, Pydantic AI integration,
   structured output, and tool calling.
7. Agent runtime: persona execution, context packet building, memory retrieval,
   history windowing/summarisation.
8. Security rings: permission checks, grants, prompts, and audit records.
9. Workflow runtime: JSON workflow loading/validation, graph execution, node
   types, and script node security.
10. Hooks: complete event-to-workflow hook registry matching, filtering,
    ordering, blocking semantics, and workflow dispatch.
11. Subagents: task-scoped delegation, persona matching, and generic worker
    promotion flow.
12. CLI completion: administration commands for all core concepts.
13. Gateways: Telegram first, then Discord or another second external gateway.
14. Minimal TUI: local interactive gateway plus permission, memory, and workflow
    review.
15. 1.0 hardening: verification, docs, examples, migration/doctor commands, and
    cleanup.

## Definition of Done

cassiopeia 1.0 is done when a user can:

1. Run `cass init` and get a valid `~/.cassiopeia/` home.
2. Create a workspace with `root_path` and workspace manager persona.
3. Define at least two personas with different skills/tools/memory policies.
4. Index Agent Skills from `~/.cassiopeia/skills/`.
5. Start a persisted session through the TUI.
6. Start a persisted session through Telegram.
7. Start a persisted session through a second external gateway.
8. Send a message and have the workspace manager respond with retrieved
   memory/context.
9. Create, expose, reject/delete, and retrieve memory.
10. Run a user-triggered workflow.
11. Trigger a workflow from a hook event.
12. Execute a script node with the correct security prompt.
13. Request and store permission grants with ring-specific choices.
14. Spawn a task-scoped subagent.
15. Promote a successful generic worker to a saved persona with ring 2 approval.
16. Use OpenAI and Ollama providers for chat, tool, and structured-output flows.
17. Use embeddings for semantic memory search.
18. Manage and inspect all major concepts from the CLI.
19. Use the minimal TUI to chat, review permissions, review memories, and inspect
    workflows.
20. Install cassiopeia through a robust curl-based install script.
21. Run `scripts/verify` successfully.

## Personas

A persona is a named, configurable agent profile.

A persona includes:

- identity: name, description, role, tone, and behavioural rules
- model settings
- tool permissions
- selected skills from the global skill list
- memory policy
- gateway availability and gateway-specific display settings
- workflow permissions
- default session policy

Personas should be declarative and locally editable. They should be usable as
orchestrators, workspace managers, and templates for task-scoped subagents.

Personas should be stored as JSON files:

```text
~/.cassiopeia/personas/
~/.cassiopeia/<workspace-slug>/personas/
```

Global personas are reusable everywhere. Workspace personas are only available in
that workspace unless explicitly promoted or exported. Persona inheritance is not
required for 1.0; copying or duplicating personas is enough.

A persona JSON should include:

- id
- slug
- name
- description
- role
- tone
- behavioural rules
- model config
- selected skills
- allowed tools
- workflow permissions
- memory policy
- gateway availability and display settings
- created/updated timestamps

Tool permissions should also use a whitelist model. A persona can use only tools
explicitly available to it, plus safe internal capabilities cassiopeia requires
for normal operation. Security rings still apply on top of tool availability.

Example tool policy:

```json
{
  "tools": {
    "allowed": ["web.search", "memory.write", "workflow.start", "subagent.create"]
  }
}
```

Effective tool permission checks should consider:

1. whether the tool is available to the persona
2. whether the tool is available in the workspace
3. whether the action is allowed by security ring and grant state
4. whether the requested action scope is valid

Workflow permissions should use a whitelist model. A persona can trigger only the
workflows made available through its workflow policy. Even when a persona can
start a workflow, the workflow's internal nodes still respect security rings.

Example workflow policy:

```json
{
  "workflows": {
    "allowed": ["summarize-thread", "plan-week"],
    "allow_workspace_workflows": true,
    "allow_global_workflows": false
  }
}
```

Memory policy controls which memory scopes the persona can read/write and whether
it may infer new memories.

Example memory policy:

```json
{
  "memory": {
    "read_scopes": ["session", "workspace", "persona", "user"],
    "write_scopes": ["session", "workspace", "persona"],
    "auto_create": true,
    "expose_created": true
  }
}
```

Memory creation does not require approval, but created/updated memories must be
exposed to the user with a delete/reject affordance.

## Sessions

A session is a persistent conversation/work context.

Each session has:

- exactly one primary persona
- exactly one workspace
- one gateway origin
- one user, chat, channel, or thread identity
- message and event history
- session-scoped permission grants
- active or recent workflow and subagent runs
- optional session-local memory

Gateway mapping:

- CLI/TUI sessions are started locally.
- Telegram and Discord conversations map to sessions until an explicit command
  such as `/new` starts a new session.
- Future GUI sessions can map to Castellan panels or workspaces.

Workspace and persona selection are tied to session creation. A user should start
a new session to change workspace or primary persona, rather than mutating those
values inside an existing session.

## Subagents

cassiopeia 1.0 supports bounded task-scoped subagents, not persistent autonomous
agent teams.

A primary/orchestrator persona may create subagents for delegated work. When
creating a subagent, cassiopeia should first try to use an existing persona that
fits the task. If no persona fits, cassiopeia may create a temporary generic
worker. After the task completes, cassiopeia can ask whether the generic worker
should become a saved persona.

A subagent has:

- id
- name
- source: existing persona or temporary worker
- task
- allowed skills
- allowed tools
- context packet
- result
- created/completed timestamps

Subagents report back to the orchestrator. They are not independent conversation
participants in 1.0.

When creating a subagent, cassiopeia should run a persona matching step unless
the user or orchestrator explicitly requests a specific persona.

Persona matching inputs:

- task description
- workspace
- required skills
- required tools
- persona descriptions
- persona skill lists
- persona availability in the workspace

Persona matching flow:

1. filter personas available in the current workspace
2. score candidates by description, skills, and tool permissions
3. instantiate the subagent from a clear persona match
4. create a temporary generic worker if no clear match exists
5. let the orchestrator choose when multiple close matches exist, unless the
   choice has a permission or security implication
6. after a generic worker succeeds, ask whether to save it as a persona

Subagents should receive a deliberately bounded context packet, not the full
session blindly.

A subagent context packet should include:

- task statement
- expected output format
- relevant user request
- relevant session summary or selected messages
- relevant workspace/persona/user memories
- allowed skills
- allowed tools
- security and permission constraints
- handoff notes from the orchestrator

Deadlines, budgets, or similar limits can be included inside the task statement
when needed; they do not need separate 1.0 fields.

Subagent results should include:

- answer/result
- actions taken
- tools or workflows used
- assumptions
- unresolved issues
- suggested memory, persona, or workflow updates

## Skills

cassiopeia 1.0 should support the Agent Skills `SKILL.md` specification at:

<https://agentskills.io/specification>

A skill is a directory containing a required `SKILL.md` file with YAML
frontmatter and Markdown instructions. Optional directories include `scripts/`,
`references/`, and `assets/`.

cassiopeia should act as a client implementor of the Agent Skills spec:

- index skill metadata globally
- expose selected skills to personas
- load full skill instructions only when activated
- load referenced resources only as needed
- avoid forking or redefining the skill format

Open question: how should cassiopeia handle execution of skill scripts?

## Tools, Workflows, and Hooks

Tools are executable capabilities such as web access, shell access, gateway
actions, or local file operations.

Workflows are graph-based automations created by the user and cassiopeia. They
can run for several reasons, including:

- explicit user trigger
- hook trigger
- agent trigger
- scheduled or gateway event trigger

Hooks connect typed cassiopeia events to workflows.

cassiopeia should define its event API before broad application logic is built.
The early API should provide stable event names, typed payload models, an event
envelope, an emitter interface, and a clear boundary for persistence. Core
runtime, storage, gateway, memory, permission, workflow, and subagent code should
emit lifecycle events as those features are implemented instead of requiring a
large retrofit late in 1.0.

The early hook listener implementation should stay deliberately limited. It
should allow in-process listener registration and deterministic delivery for
tests and internal integration points, but full user-authored hook registry
loading, filtering, priority ordering, blocking semantics, and event-to-workflow
dispatch belong to the later hooks milestone.

A workflow should have:

- id
- name
- description
- workspace scope or global scope
- trigger definitions
- graph nodes
- graph edges
- input schema
- output schema
- required tools and skills
- security ring or per-node ring requirements
- enabled/disabled state
- created/updated timestamps

Minimum workflow node types for 1.0:

- prompt/model node
- tool call node
- conditional/router node
- transform node
- human approval node
- subagent delegation node
- memory write node
- gateway response/status node
- script node that points to a Python file or similar local script for custom
  behaviour

Script nodes must go through the same security-ring and permission system as
other executable actions. They should not bypass tool permissions simply because
they are part of a workflow.

Workflows should be stored as JSON files. cassiopeia can validate, propose, and
edit workflow files, but persistent workflow creation requires explicit user
confirmation.

Workflow files can live in one of two places:

```text
~/.cassiopeia/workflows/
~/.cassiopeia/<workspace-slug>/workflows/
```

The first path is for global workflows. The second path is for project/workspace
specific workflows.

The `<workspace-slug>` path segment should be the workspace slug: a stable,
human-readable, filesystem-safe identifier. It is separate from the workspace
display name so a display rename does not necessarily change paths.

Hooks should listen to typed cassiopeia events, not random internal function
calls.

Minimum hook event types for 1.0:

- session created
- session closed
- message received
- message sent
- memory created
- memory updated
- memory deleted
- memory rejected
- permission requested
- permission granted
- permission denied
- workflow started
- workflow completed
- workflow failed
- subagent created
- subagent completed
- subagent failed
- gateway connected
- gateway disconnected
- gateway error
- workspace created
- workspace updated

Hooks should stay at meaningful lifecycle and event boundaries. Low-level model
token events and internal implementation callbacks are not required hook targets
for 1.0.

A hook should exclusively start a workflow when it triggers. Other behaviour,
such as notifying a gateway, writing memory, asking an agent to evaluate a
situation, requesting permission, or running a script, should live inside the
triggered workflow rather than on the hook itself.

A hook should include:

- id
- name
- enabled state
- scope: global or workspace
- event type
- optional filters, such as gateway, workspace, persona, tags, or event fields
- workflow id
- created/updated timestamps

Hooks should be stored in consolidated JSON registry files rather than one file
per hook:

```text
~/.cassiopeia/hooks.json
~/.cassiopeia/<workspace-slug>/hooks.json
```

The global hooks file is for global event-to-workflow bindings. Each workspace
hooks file is for workspace-specific bindings.

Multiple hooks may match the same event. Matching hooks should be executed
predictably:

- each hook has a priority number
- lower priority numbers run first
- hooks with the same priority run in deterministic id/name order
- each matching hook starts its own workflow run
- failures in one hook workflow should not prevent later hook workflows unless
  the hook is explicitly blocking
- hooks may set `blocking: true`; default is `false`

## Security Rings

cassiopeia uses security rings for autonomous decisions. Rings apply broadly to
tools, workflow actions, gateway actions, subagent creation, skill script
execution, memory-sensitive actions, and similar operations.

- Ring 0: always ask permission; no session or permanent allow.
- Ring 1: always ask permission; can be allowed for the session.
- Ring 2: always ask permission; can be allowed for the session or always allowed.
- Ring 3: permission is not needed.

Example default rings:

- shell tool: ring 0
- web tool: ring 1
- skill instructions and metadata: ring 3
- individual skill scripts: ring 2

Conservative default rings for core actions:

Ring 0:

- shell command execution
- destructive filesystem operations
- editing files outside allowed workspace roots
- installing dependencies
- changing security ring minimums
- exporting secrets or auth material
- running arbitrary local scripts with broad system access

Ring 1:

- web search/read
- external network/API calls not already covered by a configured gateway/provider
- sending outbound gateway messages outside the current session
- reading files outside the active workspace root
- starting workflows from agent initiative rather than explicit user request

Ring 2:

- running approved workflow script nodes
- running individual skill scripts
- creating persistent workflows/hooks/personas from agent proposals
- saving a temporary generic worker as a permanent persona
- granting "always allow" permissions
- changing workspace configuration
- enabling/disabling gateways

Ring 3:

- reading skill metadata/instructions
- reading active workspace config
- reading session history for the current session
- reading memory in allowed scopes
- writing normal proposed memory with user-visible delete/reject
- creating task-scoped subagents
- normal model reasoning
- responding in the current session
- starting an explicitly user-requested workflow, subject to its internal nodes

Permission grants are specific to action and scope, not broad per-ring approvals.
Persistent `always allow` grants live in a separate permissions store, not inside
persona definitions.

Ring assignments are configurable by the user, but cassiopeia enforces hard
minimum rings for high-risk actions.

Permission prompts are decided by cassiopeia core and rendered by gateways. A
prompt includes deterministic action metadata and may include an optional
agent-provided reason.

## Memory and History

History and memory are separate.

History is the persisted message and event log for sessions. It supports audit,
replay, debugging, and context reconstruction.

Memory is curated reusable context. Memory writes do not normally require
permission prompts. Instead, memory changes should be exposed to the user
immediately with an easy way to delete or reject them. If the user does nothing,
the memory remains.

Memory scopes:

- session memory
- persona memory
- workspace memory
- user memory

Memory events should include:

- memory created
- memory updated
- memory deleted
- memory rejected

Memory can be created through several paths:

- explicit user requests, such as "remember that..."
- conservative agent inference when something appears durable and useful
- workflow memory-write nodes
- manual or imported records through CLI/TUI commands or files

Agent-inferred memory should be conservative. cassiopeia should prefer durable
preferences, project facts, and settled decisions over incidental details. New or
updated memories should be exposed to the user immediately with a delete/reject
affordance.

When an agent receives a message, cassiopeia should build a context packet rather
than injecting all history or all memory. Memory retrieval should be scoped,
ranked, and compact.

For a normal turn, retrieve memory in this order:

1. session memory
2. workspace memory
3. persona memory
4. user memory

Memory selection should combine semantic/vector search, recency, tags, scope,
and optional importance/confidence scoring. Retrieved memory should be presented
to the agent as a concise context section with scope labels.

History should be summarized or windowed separately from curated memory.

Open question: what memory storage and retrieval strategy should 1.0 use?

## Storage

cassiopeia should use JSON files for user-authored definitions such as personas,
workflows, workspace configuration, and gateway bindings.

Turso/libSQL is the preferred candidate for runtime state because cassiopeia
needs local-first durable records, SQLite-compatible querying, semantic/vector
search, and safe concurrent writes when multiple local sessions or gateways use
cassiopeia at the same time. Runtime state includes sessions, event history,
memory records, permission grants, workflow runs, subagent runs, indexes, and
relationship tables.

Before committing fully to Turso, cassiopeia should include an early storage
spike to prove that local persistent Turso/libSQL works cleanly from Python for
the 1.0 use case. The spike should treat Turso's beta status as a reliability
risk to prove rather than an assumption.

The storage spike should verify:

- creating and opening a persistent embedded database under
  `~/.cassiopeia/data/`
- defining basic workspace, session, event, and memory records
- appending and querying session history
- modelling relationships between workspaces, sessions, personas, and memories
- storing embeddings and running semantic/vector search with Turso vector
  functions
- enabling MVCC and using `BEGIN CONCURRENT` where appropriate
- handling multi-write and multi-session operations reliably enough for event
  processing
- detecting retryable conflict/busy errors and retrying safely
- startup and shutdown behaviour
- dependency and install behaviour with `uv`

Open question: should Turso/libSQL be required for 1.0, or should storage be
abstracted with plain SQLite plus a secondary vector strategy as a fallback?

cassiopeia core should use a storage/repository interface rather than calling
Turso/libSQL directly from orchestration, gateway, workflow, or memory code. For
1.0, Turso can be the only implemented runtime storage backend if the spike
passes. The abstraction exists to keep SQL, transaction/retry logic, and
database-specific details contained inside the storage layer, not to require
multiple backends in 1.0.

## Model Providers

cassiopeia should use a provider abstraction for model calls. Personas,
workspaces, and global config can choose provider/model settings, with sensible
fallback defaults.

Priority providers for 1.0:

- OpenAI
- Ollama

Other providers are secondary and can be implemented as time allows. Large
providers or provider routers such as OpenRouter may be added before 1.0, but
they should not block the core 1.0 scope unless explicitly promoted to required.

Required provider capabilities for cassiopeia 1.0:

- chat
- tool calling
- structured output

Pydantic AI should handle most provider adaptation, so cassiopeia should not
build provider-specific orchestration unless necessary. Providers and models may
still need capability metadata so cassiopeia can fail clearly or degrade
gracefully when a selected model does not support a required capability well.

Embeddings should use a separate provider abstraction from chat/model calls.
Embedding providers vary by provider and model, and the best chat model is not
necessarily the best embedding model.

An embedding provider should expose:

- embed text
- embed batch
- vector dimension
- provider id
- model id

Global config and workspace config can define an active embedding profile. For
1.0, each workspace should use one active embedding profile. Memory embeddings
must record the provider, model, vector dimension, and creation timestamp. If a
workspace changes embedding profile, existing memory embeddings should be marked
stale and cassiopeia should provide a reindex path.

## Workspaces

A workspace is a named local context boundary for organising sessions, memory,
workflows, hooks, permissions, and gateway mappings around a project or life
area.

Every session belongs to exactly one workspace. If the user does not choose one,
cassiopeia uses a default workspace.

A workspace has:

- id
- name
- description
- optional local root path
- enabled personas
- enabled workflows and hooks
- workspace memory
- gateway bindings
- workspace-specific config overrides
- created/updated timestamps

Workspaces can define a workspace manager persona. When a session belongs to a
workspace, the workspace manager becomes the default orchestrator for that
session unless the user explicitly chooses another persona.

The global orchestrator is used when:

- no workspace is selected
- the workspace has no manager configured
- the user is doing global cassiopeia administration
- a gateway event cannot be mapped to a workspace yet

The workspace manager is the default persona, not the only persona allowed in the
workspace.

Minimum workspace manager behaviour for 1.0:

- receive new sessions for its workspace by default
- read workspace memory
- write proposed workspace memory, surfaced to the user after creation
- route and delegate tasks to existing personas as task-scoped subagents
- create temporary generic workers for unmatched subtasks
- suggest new personas, workflows, hooks, or memories
- trigger approved workspace workflows
- explain what workspace and session it is operating in

The workspace manager may propose persistent changes, but should not
automatically create permanent personas, workflows, hooks, or major workspace
configuration changes without explicit user confirmation.

## Open Questions

- How should skill scripts be executed or gated?
- What is the minimum workflow and hook system for 1.0?
- What storage backend should cassiopeia use?
- What should the CLI/TUI surface include for 1.0?
- What is the minimum viable workspace manager behaviour?

## Gateways

Gateways are access points into cassiopeia, not separate agent brains. Gateway
implementations should map inbound platform events into cassiopeia sessions and
render outbound responses, approval prompts, memory notices, and workflow status
updates in platform-appropriate ways.

cassiopeia 1.0 should include:

- CLI commands for local development, administration, and debugging
- minimal TUI as the local interactive gateway
- two external messaging gateways to validate reusable gateway abstractions,
  virtual methods, and shared data structures

Telegram is a strong candidate for the first external gateway. Discord is not
strictly mandatory, but is a useful second external gateway because it exercises
different platform concepts such as guilds, channels, threads, interactions, and
permissions.

The CLI is not considered a gateway. It is an administration/debugging surface.
The TUI is the local interactive gateway.

Every external gateway should support the same core contract.

Inbound:

- identify gateway name and type
- normalise inbound messages and events
- identify sender/user
- identify conversation location, such as chat, channel, or thread
- resolve or create a cassiopeia session
- support an explicit new-session command with workspace/persona selection
- pass attachments or links as structured event data, even if processing is
  limited

Outbound:

- send normal text responses
- send structured status messages, such as workflow started/completed
- render permission prompts with valid ring-specific choices
- render memory-created notices with delete/reject affordance
- send errors in a user-readable way

Operational:

- start and stop cleanly
- reconnect or fail visibly
- log gateway events
- expose configuration through cassiopeia settings
- avoid gateway-specific logic leaking into core

Rich media sending, voice, slash-command polish, and complex moderation features
are not required for 1.0.

New sessions should use a gateway-neutral request shape:

- workspace: optional; default if omitted
- persona: optional; workspace manager if omitted
- title/topic: optional
- initial message: optional
- visibility/context: platform-specific, such as DM, thread, or channel

Example CLI forms:

```sh
cass new --workspace cassiopeia --persona architect
cass ask --workspace personal --persona assistant "help me plan my week"
```

Example external gateway forms:

```text
/new
/new workspace:cassiopeia
/new workspace:cassiopeia persona:architect
/new workspace:personal persona:calendar-assistant Plan next week
```

If workspace or persona is omitted, cassiopeia should resolve the workspace from
the gateway binding or global default, then resolve the persona from the workspace
manager or global orchestrator fallback.

cassiopeia 1.0 should avoid mid-session workspace/persona mutation commands. If
the user asks to switch workspace or persona, cassiopeia should start a new
session or prompt to do so.

Gateway bindings should map external platform locations to default workspaces and
optional default personas.

Example binding config:

```json
{
  "bindings": [
    {
      "gateway": "telegram",
      "scope": "chat",
      "external_id": "123456789",
      "workspace": "personal",
      "persona": null
    },
    {
      "gateway": "discord",
      "scope": "channel",
      "guild_id": "987",
      "external_id": "456",
      "workspace": "cassiopeia",
      "persona": "workspace-manager"
    }
  ]
}
```

Bindings should live in global gateway config, such as:

```text
~/.cassiopeia/gateways/bindings.json
```

When `/new` omits workspace/persona:

1. resolve workspace from gateway binding
2. otherwise use global default workspace
3. resolve persona from explicit `/new` argument
4. otherwise use binding persona if present
5. otherwise use workspace manager
6. otherwise use global orchestrator

## CLI

The CLI is the primary administration and debugging surface for cassiopeia 1.0.
It should be capable of bootstrapping, inspecting, repairing, and exercising the
core system even when external gateways are not running.

Required CLI command groups:

- `cass init`: create the `~/.cassiopeia/` structure
- `cass chat` or `cass ask`: send a message or start a local session
- `cass session`: list, show, create, and close sessions
- `cass workspace`: list, create, show, update, and set default workspaces
- `cass persona`: list, create, show, edit, and validate personas
- `cass skill`: list, index, show, and assign skills
- `cass workflow`: list, show, validate, and run workflows
- `cass hook`: list, show, enable, disable, and validate hooks
- `cass memory`: list, search, show, delete, reject, and reindex memory
- `cass permission`: list, show, and revoke permission grants
- `cass gateway`: list, configure, start, stop, and test gateways
- `cass storage`: run storage diagnostics, migrations, and spike/doctor checks

Editing commands can be simple in 1.0. They may open or print the relevant JSON
file path rather than providing rich interactive editors.

## TUI

A minimal TUI is required for 1.0 as the local interactive gateway, but should be
implemented near the end after the core runtime, storage, workflows, permissions,
and external gateways are in place.

Minimum TUI scope:

- view active sessions
- open a local chat/session
- review permission prompts
- review newly created memories
- inspect running/recent workflows
- select workspace/persona when creating a new local session

The TUI should not include rich editors for personas, workflows, hooks, or other
configuration in 1.0. Those should be handled through JSON/config editing, CLI
commands, and eventually Castellan.

## File Layout

cassiopeia should use a global `~/.cassiopeia/` directory for definitions,
configuration, and runtime data.

Initial layout:

```text
~/.cassiopeia/
  config.json
  workspaces.json
  hooks.json
  permissions.json
  personas/
  skills/
  workflows/
  gateways/
  data/
    turso/
  <workspace-slug>/
    workspace.json
    hooks.json
    personas/
    workflows/
```

Runtime state should live in the storage backend under `data/`, assuming the
Turso spike passes. User-authored definitions and configuration should live in
JSON files and directories.

Skills are stored globally under `~/.cassiopeia/skills/`. Workspaces do not have
their own skills directory. Users assign skills from the global skill list to
personas.

Each workspace has a `workspace.json` file. That file should include a
`root_path` config item pointing to the project folder, repository, or local
directory that the workspace represents.

Secrets should not be stored directly in JSON config by default. Provider and
gateway config should reference environment variable names instead.

Example:

```json
{
  "telegram": {
    "bot_token_env": "CASSIOPEIA_TELEGRAM_BOT_TOKEN"
  },
  "openai": {
    "api_key_env": "OPENAI_API_KEY"
  }
}
```

cassiopeia can load `.env` for local development. Raw tokens should not appear in
persona, workspace, workflow, hook, or gateway definition files. Validation
should warn if config appears to contain raw secrets. OS keychain/keyring support
can be added later.
