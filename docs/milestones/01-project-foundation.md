# Milestone 1: Project Foundation

## Purpose

Establish cassiopeia's basic project foundation so future milestones can build on
stable configuration, file layout, validation, and CLI conventions. This
milestone should make `~/.cassiopeia/` real enough to initialise and inspect, but
should not implement the agent runtime.

## Scope

### In Scope

- Package layout for core cassiopeia modules.
- Config loading for global settings and environment variables.
- `~/.cassiopeia/` home directory initialisation.
- Initial JSON definition files and directories.
- Basic JSON schema or typed validation approach for user-authored definitions.
- CLI skeleton for administration/debugging commands.
- Documentation updates that keep implementation aligned with the 1.0 scope.

### Out of Scope

- SurrealDB runtime storage implementation.
- Agent execution, model calls, and Pydantic AI/Pydantic Graph orchestration.
- Full workspace/persona/workflow semantics.
- Telegram, Discord, and TUI gateways.
- Permission prompt UX beyond any placeholder types required for later work.
- Installer implementation, except for reserving the eventual command/doc shape.

## Deliverables

- `cass init` creates a valid `~/.cassiopeia/` structure.
- Initial config loading supports the cassiopeia home path and `.env` use.
- CLI command groups exist for future admin surfaces, even if most commands are
  placeholders.
- Definition/config validation pattern is chosen and represented in code.
- Tests cover initialisation, config loading, and validation basics.

## Tasks

- [ ] Define the initial module layout under `src/cassiopeia/`.
- [x] Add settings for cassiopeia home, environment loading, and cache-safe local
      development.
- [x] Implement `cass init`.
- [x] Create the initial `~/.cassiopeia/` directory layout:
      `config.json`, `workspaces.json`, `hooks.json`, `permissions.json`,
      `personas/`, `skills/`, `workflows/`, `gateways/`, and `data/`.
- [x] Add validation for the initial global config shape.
- [ ] Add CLI command groups for `session`, `workspace`, `persona`, `skill`,
      `workflow`, `hook`, `memory`, `permission`, `gateway`, and `storage`.
- [ ] Ensure commands that are not implemented yet fail clearly instead of
      pretending to work.
- [x] Add focused tests for `cass init`, config loading, and validation.
- [ ] Update documentation if implementation details diverge from the scope.

## Acceptance Criteria

- [ ] Running `cass init` on a clean machine creates the expected
      `~/.cassiopeia/` structure without storing secrets.
- [ ] Running `cass init` against an existing cassiopeia home fails clearly and
      does not destroy existing user files.
- [ ] The cassiopeia home path can be overridden for tests/development.
- [ ] CLI help lists the intended administration command groups.
- [ ] Invalid initial config fails with a clear error.
- [ ] Tests cover the foundation behaviours.
- [ ] `scripts/verify` passes, or any failure is documented with the remaining
      risk.

## Verification

```sh
uv run cass init --home /tmp/cassiopeia-test-home
uv run cass --help
scripts/verify
```

## Decisions

- CLI is an administration/debugging surface, not a gateway.
- `~/.cassiopeia/` is the default cassiopeia home.
- User-authored definitions are JSON files.
- Secrets are referenced through environment variable names, not stored directly
  in JSON config.

## Open Questions

- Exact JSON schema tooling and schema file layout.
- Exact installer implementation and hosting path for curl-based installation.

## Notes

This milestone should stay deliberately small. Its job is to make the project
shape stable, not to prove storage, agents, workflows, or gateways.
