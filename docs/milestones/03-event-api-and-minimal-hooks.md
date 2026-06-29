# Milestone 3: Event API and Minimal Hooks

## Purpose

Define cassiopeia's event contract before most application logic is built, so
future milestones can emit lifecycle events as features are implemented instead
of requiring a broad retrofit later.

This milestone should make events stable enough for storage, runtime, gateway,
memory, permission, workflow, and subagent code to depend on. It should also
provide a deliberately small in-process listener shell for tests and internal
integration points, while leaving full user-authored hook registries and
event-to-workflow dispatch to the later hooks milestone.

## Scope

### In Scope

- Typed event names and payload models for the 1.0 lifecycle event catalogue.
- A common event envelope with identity, timestamp, source, scope, correlation,
  causation, and payload metadata.
- An emitter interface that application code can depend on without knowing the
  storage backend or hook registry implementation.
- A persistence boundary that defines how emitted events are handed to the
  future storage layer.
- A minimal in-process listener registry with deterministic delivery order.
- Tests that prove event validation, emission, listener delivery, and failure
  handling behaviour.
- Documentation updates if implementation details refine the 1.0 scope.

### Out of Scope

- Production Turso/libSQL repository implementation.
- User-authored `hooks.json` loading or validation.
- Event filtering by gateway, workspace, persona, tags, or payload fields.
- Hook priority, blocking semantics, and event-to-workflow dispatch.
- Workflow runtime integration.
- Gateway integration beyond event source modelling.
- Long-term event replay, migration, or analytics tooling.

## Deliverables

- Event model module, likely under `src/cassiopeia/events.py` or an
  `src/cassiopeia/events/` package if the implementation needs multiple files.
- Event name catalogue covering the minimum 1.0 hook event types from the scope
  document.
- Event envelope and payload types that use the project's existing Pydantic
  validation conventions.
- Event emitter protocol or base class for runtime code to call.
- Minimal in-memory emitter/listener implementation for tests and early
  integration.
- Repository or sink protocol that records the contract expected from the
  future storage layer.
- Focused tests under `tests/` for event construction, validation, emission,
  listener ordering, and listener failure behaviour.

## Tasks

- [x] Define the canonical event type naming convention and map the minimum 1.0
      hook event catalogue to it.
- [x] Define the event envelope fields, including event id, type, timestamp,
      source, optional workspace/session/persona/gateway ids, correlation id,
      causation id, tags, and typed payload.
- [x] Define payload models for session, message, memory, permission, workflow,
      subagent, gateway, and workspace lifecycle events.
- [x] Decide how unknown or future event payloads are represented without
      breaking validation for stored historical events.
- [x] Add an emitter interface that accepts validated event creation requests
      and returns the emitted envelope.
- [ ] Add a storage sink or repository protocol for appending events without
      depending on the production storage implementation.
- [ ] Add a minimal in-process listener registry with deterministic registration
      and delivery order.
- [ ] Define listener failure behaviour for this early shell, including whether
      one failed listener prevents later listeners from running.
- [ ] Add tests for valid events, invalid payloads, correlation/causation
      fields, listener ordering, and listener failures.
- [ ] Update documentation if the implemented event model changes terminology
      or scope assumptions.

## Acceptance Criteria

- [ ] Application code has a stable emitter API it can call from later
      milestones without importing storage or workflow internals.
- [ ] The minimum 1.0 lifecycle event catalogue is represented by typed event
      names and payload models.
- [ ] Events can be created with consistent ids, timestamps, source metadata,
      scope metadata, and payload validation.
- [ ] Emitted events are passed to a storage boundary that can later be backed by
      Turso/libSQL.
- [ ] In-process listeners receive emitted events in deterministic order.
- [ ] Listener failure behaviour is explicit and covered by tests.
- [ ] Full user-authored hook matching and workflow dispatch remain deferred to
      the hooks milestone.
- [ ] `scripts/verify` passes, or any failure is documented with the remaining
      risk.

## Verification

```sh
scripts/verify
```

## Decisions

- Event names should describe meaningful lifecycle boundaries, not low-level
  implementation callbacks.
- Event names use dot format, such as `app.start` and `session.deleted`.
- Event ids should use Python's standard-library `uuid` module unless a later
  requirement proves a need for a non-stdlib sortable id package.
- Event models should use Pydantic validation. The event envelope should stay
  stable, while payload schemas should be editable over time so historical
  event records can still be interpreted if schemas change.
- Stored events load payloads through generic `EventPayload`; family-specific
  payload models are opt-in validation for write-time and feature-level logic,
  not a requirement for reading historical records.
- Async listener support should be included from the start.
- The initial listener shell is for internal integration and tests only.
- Full hook registry loading, filtering, ordering, blocking semantics, and
  workflow dispatch belong to the later hooks milestone.
- The storage layer should receive events through a narrow append boundary
  rather than being called directly from feature code.

## Open Questions

- Whether a later schema registry should map known `schema_name` values to
  family-specific payload models for optional typed reads.

## Notes

This milestone exists to prevent event wiring from becoming late-stage cleanup.
Keep it focused on the event contract and the smallest useful listener shell;
do not let it grow into the full hook or workflow runtime.
