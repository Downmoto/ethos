# Milestone 2 — Personalisation

This document proposes the work required to complete the second milestone on
the road to beta v0.1.0. It defines product scope and completion outcomes, not
the final implementation design.

## Goal

Give Ethos durable, user-controlled context and named identities that can work
consistently across sessions without weakening workspace, capability, or tool
security boundaries.

## Delivery order

1. Persona model and management
2. Persona runtime
3. Memory model and management
4. Memory retrieval and mutation

Persona identity and ownership come first so memory can be scoped correctly
from its first stored record. This avoids adding persona ownership to an
already-populated memory store later.

## Persona management

### Outcome

Users can create and manage named personas with clear, validated settings and
choose which persona starts a session.

### Scope

- Create, show, list, update, and remove personas.
- Configure a persona's name and behavioural instructions.
- Configure optional model and reasoning preferences.
- Configure which capabilities a persona may use.
- Choose a default persona and select a persona for a new session.
- Show the effective persona configuration with credentials redacted.

### Safety boundaries

- Persona instructions cannot override tool policy or workspace containment.
- Persona capability settings can only narrow permissions unless the user
  changes the underlying workspace or global configuration directly.
- Persona names and identifiers are unique and validated.
- Removing a persona does not corrupt or silently reassign existing sessions
  or memories.
- Configuration and lifecycle events do not contain persona instructions or
  secrets.

### Complete when

- Persona configuration has one canonical model and persistence path.
- The service, CLI, and Vox protocol expose the same management behaviour.
- Invalid changes fail without partially modifying the persona.
- Selection precedence between explicit, workspace-default, and global-default
  personas is documented and tested.
- Existing sessions remain readable when a persona is disabled or removed.

## Personas

### Outcome

Each session runs with one stable persona that contributes its identity,
behaviour, preferences, capabilities, and memory scope to every turn.

### Scope

- Bind a persona to a session when the session is created.
- Add persona identity and instructions to run context without persisting them
  as conversation messages.
- Resolve persona model preferences through the provider configuration from
  Milestone 1.
- Resolve persona capability settings through the capability configuration
  from Milestone 1.
- Make the active persona visible in session metadata and runtime events.
- Preserve a default Ethos persona for users who do not create one.

### Safety boundaries

- A session's persona does not change implicitly between turns.
- Persona instructions remain subordinate to Ethos security and tool-policy
  instructions.
- Persona configuration cannot grant unavailable workspace or global
  capabilities.
- Prompt, reasoning, persona instructions, and memory contents remain absent
  from lifecycle events.
- Cross-persona conversations and persona-to-persona delegation are outside
  this milestone.

### Complete when

- New and resumed sessions resolve the same persona consistently.
- Provider, capability, and instruction precedence is deterministic and
  tested.
- Missing, disabled, or removed personas produce defined session behaviour.
- CLI and Vox session representations identify the active persona.
- The default persona preserves current behaviour for existing users.

## Memory management

### Outcome

Users can inspect and correct everything Ethos may remember about them or their
work.

### Scope

- List and inspect stored memories.
- Add memories directly without starting an agent run.
- Edit inaccurate or outdated memories.
- Delete memories and prevent them from being retrieved again.
- Filter memories by workspace and persona scope.
- Show each memory's source, creation time, and last update time.

### Safety boundaries

- Memory remains local application state unless selected for model context.
- Memory content is treated as data, not as trusted system instruction.
- Deletion removes the retrievable content even if bounded lifecycle metadata
  is retained.
- Removing a workspace or persona cannot silently broaden a memory's scope.
- Management output makes clear which persona and workspace can use a memory.

### Complete when

- Memory management uses one canonical service and persistence boundary.
- The CLI and Vox protocol expose consistent memory records and operations.
- Updates and deletions are atomic and immediately affect later retrieval.
- Invalid ownership or scope changes leave the existing memory intact.
- Lifecycle events identify the operation without recording memory content.

## Memory

### Outcome

Ethos can carry relevant, durable knowledge across sessions while keeping the
user in control of what is stored and recalled.

### Scope

- Store explicit memories with stable identifiers and ownership metadata.
- Scope memories globally, to a workspace, or to a persona within a workspace.
- Retrieve a bounded set of relevant memories for each run.
- Add retrieved memories to run context without copying them into session
  history.
- Let the agent propose creating, updating, or deleting a memory through tools.
- Record enough provenance for the user to understand where a memory came
  from.

### Safety boundaries

- Agent-initiated memory mutations use the shared write-tool approval flow.
- Retrieval includes global memory but never includes workspace- or
  persona-scoped memory owned by another context.
- Retrieved content cannot override Ethos instructions, tool policy, or
  capability limits.
- Context contribution, individual memory size, and retrieval count are
  bounded.
- Raw transcripts, reasoning, and tool results are not remembered
  automatically.
- Memory content does not appear in lifecycle events or diagnostics.

### Complete when

- Relevant memories are selected deterministically within the active scope and
  configured limits.
- Approved mutations survive process restarts and are available to later
  sessions.
- Denied, cancelled, or invalid mutations do not change stored memory.
- Deleted memories cannot re-enter context through stale indexes or caches.
- Runs remain functional when no memories exist or retrieval fails safely.
- Tests demonstrate isolation between workspaces and personas.

## Milestone completion

Milestone 2 is complete when personas remain stable across session lifetimes,
memory is durable and correctly isolated, users can inspect and correct all
personalisation state, the developer documentation reflects the shipped
behaviour, and the full verification suite passes.
