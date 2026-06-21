# Cassiopeia Agent Instructions

Cassiopeia is a general-purpose agent persona harness for workflow automation. Its
long-term purpose is to support multiple gateways, starting with Telegram and
Discord, and to serve as the agent/persona backend for its sister app Castellan.
Castellan is planned as a GUI around Cassiopeia plus additional tools such as a
code/text editor, calendar, and related local workflow surfaces.

This repository is currently a Python CLI project built around LangGraph agent
control flow, LangChain adapters, Click commands, limited Textual TUI support, and
Pydantic Settings configuration.

Use Canadian spelling in documentation, comments, user-facing text, and commit
messages.

## Project Setup

- Use `uv` for dependency management and command execution.
- If dependencies are missing or stale, agents may run `uv sync` without asking.
- Project-local tool caches should live under `.venv/.project_cache/`; use
  `scripts/verify` as the reference for cache-related environment variables.
- Optional local configuration may live in `.env`, usually copied from `.env.example`
  when present.
- Never commit secrets, `.env` files, API keys, Codex auth files, or generated
  credentials.

## Common Commands

- Install or refresh dependencies: `uv sync`
- Run the local CLI: `uv run cass run "hello"`
- Make a Codex-backed request: `uv run cass ask "Write one short sentence about Cassiopeia."`
- Run all checks: `scripts/verify`

Agents must run `scripts/verify` before finishing any code change. If verification
cannot be run, explain why and identify the remaining risk.

## Code Quality

- Target Python 3.12 or newer within the version range declared in `pyproject.toml`.
- Keep changes consistent with the existing project structure under `src/`, `tests/`,
  and `scripts/`.
- Prefer the existing stack and local patterns before introducing new abstractions.
- Use Ruff formatting and linting conventions from `pyproject.toml`.
- Keep mypy and pyright passing.
- Add or update focused tests when behaviour changes.

## Dependencies

Avoid adding dependencies by default. If a new dependency is vital, meaning it is
needed for correctness or would save substantial implementation time compared with
maintaining local code, ask for approval before adding it.

## Git Conventions

Commit messages must use Conventional Commits with one of these types:

- `fix:`
- `feat:`
- `build:`
- `chore:`
- `ci:`
- `docs:`
- `style:`
- `refactor:`
- `perf:`
- `test:`

Branch names should use matching intent prefixes, such as `feat/`, `refactor/`, or
`docs/`. Prefer branch names like `feat/add-selection-combinators` rather than
tool-specific names.

## Agent Behaviour

- Read relevant files before editing.
- Keep edits scoped to the user's request.
- Do not revert user changes unless explicitly asked.
- Do not perform destructive Git operations unless explicitly asked.
- When changing code, update related tests or documentation where it materially
  improves maintainability.
