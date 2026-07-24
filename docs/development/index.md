# Developer documentation

These documents explain the contracts a contributor must understand before
changing Ethos:

1. [Architecture](architecture.md) maps the system, dependency boundaries, and
   end-to-end execution paths.
2. [Workspaces and runtime](workspaces-and-runtime.md) covers configuration,
   capability policy, session persistence, and model-turn semantics.
3. [Commands, events, and gateways](commands-events-and-gateways.md) covers the
   transport boundary, event ordering, and long-running gateway lifecycle.

Design investigations and deferred proposals live separately in
[`docs/notes`](../notes/). They are not statements of current behaviour.

## Documentation ownership

Documentation should live beside the narrowest stable owner of the fact:

- Module, class, and function docstrings define local responsibilities,
  contracts, side effects, and guarantees.
- Inline comments explain non-obvious ordering or why a simpler-looking
  implementation would violate an invariant.
- Developer documents explain relationships and decisions that span modules.
- Tests are the executable specification for edge cases and failure behaviour.

Do not copy the same contract into every layer. Source documentation should
state the local guarantee; these guides should explain how guarantees compose.

## Writing source documentation

Document what a caller or maintainer cannot safely infer from the signature:

- ownership and trust boundaries;
- mutation, persistence, and event ordering;
- concurrency scope;
- failure behaviour that leaves observable state;
- reasons for validation or apparently redundant checks.

Avoid comments that merely restate code and boilerplate sections that repeat
parameter names. When behaviour changes, update its nearest source docstring,
the relevant cross-module guide, and the tests in the same change.

## Current versus proposed behaviour

Developer documentation describes the current implementation. Future designs
belong in `docs/notes` and must say that they are proposed or deferred.

If implementation and documentation disagree, treat that as a defect. Verify
the intended contract before changing either side.
