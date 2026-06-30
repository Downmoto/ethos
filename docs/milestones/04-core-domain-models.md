# Milestone 4: Core Domain Models

## Purpose

Define the typed domain models that later storage, runtime, workflow, gateway,
permission, memory, and CLI work will depend on. This milestone turns the 1.0
scope into concrete Pydantic models and package homes without building the
services that load, store, or execute them.

Milestone 4 covers a broad surface area, so the work should proceed from shared
primitives and ownership boundaries into feature-owned models. The goal is to
make cross-module references explicit while avoiding circular imports and
premature service abstractions.

## Scope

### In Scope

- Shared id, slug, timestamp, and scope primitives that are genuinely
  cross-cutting.
- Workspace definition and workspace policy models.
- Persona definition models, including model, tool, workflow, memory, skill,
  gateway, and session policy fields.
- Session and message models, including gateway origin and session lifecycle
  state.
- Permission ring, action, prompt, grant, and audit record models.
- Memory record models for session, workspace, persona, and user scopes.
- Workflow definition, node, edge, trigger, run summary, and hook registry
  models.
- Tool and skill reference models needed by persona, workflow, permission, and
  subagent definitions.
- Gateway identity and binding models needed by sessions and persona
  availability.
- Subagent task, context packet, result, and promotion proposal models.
- Event-adjacent records not already covered by milestone 3, such as event
  scope references and lifecycle record links.
- Focused tests for validation rules, JSON round-tripping, and package
  dependency direction.

### Out of Scope

- Storage repositories, migrations, SQL schemas, or Turso/libSQL implementation.
- Runtime execution, model calls, agent turns, context construction, or memory
  retrieval.
- Workflow graph execution, hook matching, node implementations, or script
  execution.
- Permission decision services or gateway prompt rendering.
- CLI commands beyond updating imports or placeholders if required by model
  moves.
- JSON file loading/discovery services beyond small model round-trip tests.
- Adding new dependencies unless a model requirement cannot be met with the
  existing stack.

## Deliverables

- Feature-owned model modules under the target package homes defined in
  `docs/project-structure.md`.
- Shared primitives in `src/cassiopeia/shared.py` only where multiple feature
  packages genuinely need them.
- Typed models for workspaces, personas, sessions, permissions, memory,
  workflows, hooks, tools, skills, gateways, and subagents.
- Validation tests covering required fields, enum values, scope constraints,
  timestamps, JSON serialisation, and unknown-field behaviour.
- Import-boundary tests or focused assertions that feature models do not import
  CLI, TUI, gateway implementations, storage backends, or provider SDKs.
- Documentation updates if the implemented model shapes refine the 1.0 scope or
  package ownership.

## Tasks

- [x] Review `docs/cassiopeia-1.0-scope.md` and `docs/project-structure.md`
      before editing model packages; update either document only if model work
      uncovers a real scope or ownership change.
- [x] Define shared primitive types for ids, slugs, timestamps, scopes, and
      definition/runtime record metadata, keeping feature-specific fields out of
      the shared module.
- [x] Create package homes for the feature model families that do not exist yet:
      `workspaces`, `personas`, `sessions`, `permissions`, `memory`,
      `workflows`, `tools`, `skills`, `gateways`, and `subagents`.
- [x] Define workspace models first, including workspace id, slug, display name,
      root path, workspace manager persona reference, availability policy, and
      created/updated timestamps.
- [x] Define gateway identity and binding models needed by sessions and persona
      availability, without implementing Telegram, Discord, or TUI behaviour.
- [x] Define tool and skill reference models, including ids/names, availability,
      selected skills, allowed tools, and security metadata references.
- [x] Define persona models, including identity, tone, behavioural rules, model
      configuration, selected skills, allowed tools, workflow policy, memory
      policy, gateway availability, and default session policy.
- [x] Define session and message models, including workspace/persona/gateway
      references, origin identity, lifecycle status, message role/direction,
      timestamps, and links to recent workflow/subagent activity.
- [x] Define permission models, including security rings, action identifiers,
      action scope, grant duration, prompt request metadata, decisions, and
      audit records.
- [x] Define memory models, including memory scope, source references,
      exposure/rejection state, tags or labels if needed, embedding freshness
      metadata, and created/updated timestamps.
- [x] Define workflow definition models, including global/workspace scope,
      triggers, nodes, edges, input/output schemas, required tools/skills,
      security requirements, enabled state, and timestamps.
- [x] Define workflow node models for the minimum 1.0 node types without
      implementing execution behaviour.
- [x] Define hook registry models, including global/workspace scope, event type,
      optional filters, priority, blocking flag, workflow reference, enabled
      state, and timestamps.
- [x] Define subagent models, including task description, selected or temporary
      persona source, bounded context packet, allowed skills/tools, result,
      lifecycle timestamps, and promotion proposal metadata.
- [x] Add model tests in the package-aligned test layout where practical,
      keeping small model suites focused and avoiding broad fixture frameworks.
- [x] Add JSON round-trip tests for representative user-authored definitions:
      workspace, persona, workflow, and hook registry.
- [x] Add tests that runtime-state models can reference one another by id
      without importing storage implementations or creating object cycles.
- [x] Update milestone 4 decisions and open questions as model boundaries are
      settled.

## Acceptance Criteria

- [x] Each 1.0 core concept named in this milestone has a feature-owned typed
      model in the intended package home.
- [x] Shared primitives exist only for genuinely cross-cutting concerns and do
      not become a dumping ground for feature models.
- [x] Persona, workspace, session, permission, memory, workflow, hook, and
      subagent models represent the required fields from the 1.0 scope.
- [x] User-authored definition models reject unknown fields unless a documented
      extension point requires permissive loading.
- [x] Runtime-state models use id references for cross-feature links rather than
      nested object graphs that would complicate storage.
- [x] Feature model packages do not import CLI, TUI, concrete gateway
      implementations, storage backends, provider SDKs, or runtime execution
      code.
- [x] Tests cover validation failure paths for invalid ids/slugs/scopes, invalid
      permission rings, invalid workflow/hook references, and invalid session or
      memory states.
- [x] Full service behaviour remains deferred to later milestones.
- [x] `scripts/verify` passes, or any failure is documented with the remaining
      risk.

## Verification

```sh
scripts/verify
```

Optional focused checks during implementation:

```sh
uv run pytest tests/workspaces tests/personas tests/sessions tests/permissions
uv run pytest tests/memory tests/workflows tests/subagents
```

## Decisions

- Feature packages own their own models; the shared module is reserved for ids,
  timestamps, slugs, scopes, and other primitives used by multiple packages.
- Cross-feature relationships should be stored as typed id references, not
  nested domain objects.
- User-authored definitions and runtime state are separate model concerns even
  when they share some fields.
- This milestone defines model shape and validation only. Loading, persistence,
  execution, ranking, matching, permission evaluation, and dispatch belong to
  later milestones.
- Package-specific id aliases are deferred until a second constraint appears;
  models use shared `EntityId` references for now.
- User-authored definition models do not include schema version fields yet.
  Add them with the first real definition loader or migration path.
- Workflow input and output schemas are stored as raw JSON-compatible
  dictionaries for now. A cassiopeia-owned schema subset is deferred until
  workflow loading or editing needs it.
- Memory embedding freshness metadata belongs in the memory model as shallow
  provider/model/dimension/stale fields; reindex behaviour remains deferred to
  storage and provider milestones.

## Open Questions

- How much permissive loading is needed for future user-authored JSON
  compatibility once real loaders exist.

## Notes

This milestone is intentionally broad but should stay shallow. Build the model
surface needed by later milestones, then stop before implementing services.
When in doubt, prefer a small feature-owned Pydantic model with explicit id
references over a shared abstraction.
