# Milestone 2 — Personas execution plan

This document turns the persona management and persona runtime outcomes in
[`milestone-2-personalisation.md`](milestone-2-personalisation.md) into an
ordered implementation plan. It deliberately excludes memory, delegation,
cross-persona conversations, and persona-specific credentials.

## Agreed behaviour

### Persona identity

- Every persona has an immutable, validated identifier and an editable display
  name.
- Identifiers and display names are unique. Identifiers use the same portable
  lower-case slug rules as workspaces.
- `ethos` is reserved for the built-in orchestrator persona.
- A persona contains behavioural instructions, an enabled state, optional
  model and reasoning preferences, and an optional capability allowlist.
- Model preferences select a model and reasoning effort under the globally
  selected provider. Provider selection and credentials remain global.

### Workspace assignment

Personas are assigned to workspaces, not sessions. Every session resolves the
current persona assignment of its owning workspace at the start of each turn.
Changing a workspace assignment therefore changes all of that workspace's
sessions on their next turn without rewriting session records.

An optional persona supplied when a workspace is created becomes that
workspace's assignment. Otherwise the current global default is selected and
persisted as the assignment. Changing the global default affects only
workspaces created later. Existing workspaces without persona state, including
workspaces from pre-persona homes, resolve to the built-in `ethos` persona
rather than inheriting later default changes.

### Disabled, removed, and missing personas

If a workspace's assigned persona is disabled, removed, or unexpectedly
missing, turns run as the built-in `ethos` orchestrator. The workspace retains
its assigned persona identifier, and public workspace, session, and runtime
representations expose both assigned and effective identifiers when they
differ. Re-enabling an assigned persona therefore restores it without another
workspace mutation.

Removal creates a minimal tombstone instead of making the identifier reusable.
The tombstone retains the last capability allowlist. A fallback turn uses the
`ethos` identity, instructions, and model selection but remains subject to the
removed or disabled persona's capability ceiling. Removal therefore cannot
broaden a workspace's tool access or silently reassign future memory ownership.

### Persona edits

Persona edits apply to every assigned workspace on its next turn. A turn
resolves one immutable effective configuration and keeps it for all model
rounds in that turn. No persona configuration is snapshotted into a session.

### Security and context

- Persona capability settings are an allowlist intersected with effective
  global and workspace settings. They can omit capabilities but cannot enable
  or enlarge unavailable ones.
- Ethos core security and tool-policy instructions precede persona
  instructions and explicitly remain authoritative.
- Persona identity and instructions are run-only context. They are not copied
  into session messages or lifecycle events.
- Events contain assigned and effective persona identifiers, fallback state,
  and changed field names, but never behavioural instructions, prompts,
  reasoning, credentials, or model output.
- The existing opt-in context diagnostic may contain persona instructions
  because it records the complete model request and is already documented as
  sensitive diagnostic output.

## Canonical state

Use one atomically replaced `personas.yaml` file in the Ethos home for persona
records, tombstones, the global creation default, and workspace assignments. A
missing file is treated as built-in `ethos` state so existing homes continue to
work without a migration command. Fresh homes create the file during
initialisation.

The canonical models should represent:

- active persona records;
- removed persona tombstones;
- one global default identifier for new workspaces;
- workspace-to-persona assignments;
- optional `model_name` and `reasoning_effort` preferences;
- an optional allowlist of registered capability names.

The persona manager owns loading, validation, assignment resolution, mutation,
and atomic persistence. CLI, Vox, the service, and the runtime must not merge
or edit the file independently.

Sessions continue to persist only their permanent workspace association. A
session's active persona is derived application state, not session state.

## Delivery sequence

### 1. Persona model and persistence

- Add the typed persona configuration and `PersonaManager`.
- Add the built-in `ethos` persona and missing-file compatibility behaviour.
- Implement create, read, list, update, enable or disable, remove, global
  default selection, and workspace assignment mutations.
- Validate complete candidate state before replacement so invalid mutations
  leave the existing file unchanged.
- Reject duplicate identifiers and names, invalid defaults or assignments,
  unknown capabilities, attempts to modify or remove `ethos`, and reuse of
  tombstoned identifiers.
- Add the persona template and home initialisation entry.

Verification:

- persistence survives manager restarts;
- files use mode `0600` and atomic replacement;
- failed validation preserves the previous bytes;
- missing files resolve existing workspaces to `ethos` without being written
  as a read side effect;
- removed identifiers cannot be recreated.

### 2. Shared service management

- Compose the persona manager in `Ethos`.
- Add public configured and effective persona views with no credential fields.
- Add service methods for create, list, show, update, enable or disable,
  remove, global default management, and workspace assignment.
- Validate workspace names through `WorkspaceManager` before reading or
  changing assignments.
- Extend workspace and session views with assigned persona, effective persona,
  and fallback state. Session values are resolved through their workspace and
  are not read from session JSON.
- Add lifecycle event types and redacted payloads for persona operations.

Verification:

- each operation uses the service as its only application boundary;
- configured and effective views distinguish preference fallback and
  capability intersection;
- events identify the operation and changed fields without containing
  instructions;
- event failure behaviour remains consistent with existing service mutations.

### 3. CLI and Vox management

- Add a top-level `ethos persona` command group exposing persona lifecycle and
  global-default operations.
- Add workspace persona assignment to the existing workspace command group.
- Use the existing field/value decoding convention for sparse updates.
- Add matching Vox persona resources and a workspace persona-assignment route.
- Allow workspace creation to accept an optional persona assignment; omission
  uses and persists the global creation default.
- Keep request parsing and response formatting in the adapters while all
  validation and persistence remain in the service.

Verification:

- CLI and Vox cover the same operations and return the same effective data;
- session creation has no persona parameter in either adapter;
- unknown fields and malformed values receive the existing adapter-specific
  error treatment;
- instructions can be managed but never appear in operation events.

### 4. Workspace assignment integration

- Resolve and persist a persona assignment when a workspace is created.
- Keep existing workspace behaviour compatible by resolving a missing
  assignment to `ethos`.
- Ensure changing the global default does not change existing workspace
  assignments.
- Ensure changing a workspace assignment does not rewrite any session file.
- Surface assigned and effective persona identifiers in workspace and session
  representations.

Verification:

- explicit assignment, global creation default, and legacy fallback branches
  are covered;
- every session in one workspace reports the same current persona;
- reassignment affects existing and new sessions on their next resolution;
- legacy and archived sessions remain readable;
- workspace assignment changes never modify session history or metadata.

### 5. Runtime resolution

- Resolve the workspace's assigned and effective personas once at the start of
  a turn or approval resumption.
- Construct the model from the effective persona's optional model and
  reasoning preferences, falling back field-by-field to global provider
  settings.
- Add the effective persona identity and instructions to run-only context
  after the Ethos core instruction and before capability instructions.
- Filter resolved workspace capabilities through the assigned persona's
  retained allowlist, including during fallback.
- Include assigned persona, effective persona, and fallback state in runtime
  event payloads without changing their content-redaction boundary.
- Keep one resolved persona configuration for all model rounds in that run.

Verification:

- persona instructions appear in requests but never stored messages;
- edits and workspace reassignment affect the next turn, not a turn already
  running;
- disabled, removed, and missing records visibly fall back to `ethos`;
- fallback cannot broaden capabilities;
- persona model preferences do not change the selected provider or expose its
  credentials;
- approval resumption applies the same resolution and safety rules as a new
  turn.

### 6. Public documentation and final verification

- Update the architecture and workspaces/runtime documentation with persona
  state, workspace assignment, fallback, prompt ordering, and capability
  intersection.
- Document the CLI commands and Vox resources.
- Update the Milestone 2 completion checklist with the shipped behaviour.
- Run formatting, linting, static type checking, the complete test suite, and
  `scripts/verify.sh`.

## Suggested delivery units

Use branch `feat/add-personas` and keep the implementation in reviewable
Conventional Commit slices:

1. `feat: add persona configuration management`
2. `feat: expose persona management`
3. `feat: assign personas to workspaces`
4. `feat: apply personas to runtime turns`
5. `docs: document persona behaviour`

Each slice should leave the full verification suite passing. No new dependency
is required.

## Deferred work

- persona-owned memory and retrieval;
- persona-specific providers or credentials;
- per-persona numeric capability settings;
- persona version history or session snapshots;
- session-level persona selection;
- cross-persona conversation and delegation;
- hard deletion or identifier reuse.
