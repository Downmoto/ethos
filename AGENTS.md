# Ethos contributor instructions

Use Canadian spelling in documentation, comments, commit messages, and user-facing text. Avoid em dashes and “it’s not X, it’s Y” style prose.

## Source of truth

- `plan.md` is the milestone map and preserves accepted product decisions D001–D019.
- `plan/0.1.0-instance-foundation.md` preserves accepted lifecycle requirements LIF-001–021.
- Each file in `plan/` owns its milestone’s implementation contract and exit gate.
- Implement the current milestone in its stated order. Do not build later milestones early or create empty crates for future features.
- Identify conflicts with accepted commitments before changing them. An assistant proposal becomes accepted only when the owner approves it.
- Keep the relevant milestone plan current when implementation changes a contract. Do not add document status fields or create extra planning documents for routine choices.

## Implementation

- Keep dependencies few. Prefer the Rust standard library and existing dependencies when they fit. Add a dependency when it clearly reduces risk or substantial code.
- Own Ethos-specific behaviour: runtime, context, capabilities, permissions, sessions, and workflows. Use established libraries for infrastructure such as HTTP, TLS, serialisation, and SQLite.
- Preserve workspace and project boundaries at every operation.
- Make interrupted work and uncertain external effects visible. Never report an operation as complete without evidence.
- Add focused tests for meaningful behaviour and failure paths. Run the checks required by the current milestone before declaring it complete.

## Git

- Use Conventional Commits with these types: `fix:`, `feat:`, `build:`, `chore:`, `ci:`, `docs:`, `style:`, `refactor:`, `perf:`, or `test:`.
- Name branches by intent, usually with `feat/`, `refactor/`, or `docs/`. Prefer names such as `feat/add-selection-combinators` over `codex/...`.
- Keep commits scoped to one coherent change.
