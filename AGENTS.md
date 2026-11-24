# Ethos contributor instructions

Use Canadian spelling in documentation, comments, commit messages, and user-facing text. 
## Source of truth

- `plan.md` is the milestone map. `plan/<version>-*.md` contains the implementation contract and exit gate for each milestone.
- `design/` and `decisions/` preserve accepted requirements and decisions. Consult the relevant files when a plan refers to them.
- Implement the current milestone in its stated order. Do not build later milestones early or create empty crates for future features.
- If a plan conflicts with an accepted decision, identify the conflict before changing either one.
- Keep plans current when implementation changes a contract. Do not add document status fields or create extra planning documents for routine choices.

## Implementation

- Keep dependencies few. Use the Rust standard library and existing dependencies where they fit; add a dependency when it clearly reduces risk or substantial code.
- Own Ethos-specific behaviour: runtime, context, capabilities, permissions, sessions, and workflows. Use established libraries for infrastructure such as HTTP, TLS, serialisation, and SQLite.
- Preserve workspace and project boundaries at every operation.
- Make uncertain external effects and interrupted work visible. Never report an operation as complete without evidence.
- Add focused tests for meaningful behaviour and failure paths. Run the checks required by the current milestone before declaring it complete.

## Git

- Use Conventional Commits with these types: `fix:`, `feat:`, `build:`, `chore:`, `ci:`, `docs:`, `style:`, `refactor:`, `perf:`, or `test:`.
- Name branches by intent, usually with `feat/`, `refactor/`, or `docs/`. Prefer names such as `feat/add-selection-combinators` over `codex/...`.
- Keep commits scoped to one coherent change.
