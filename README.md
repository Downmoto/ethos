# cassiopeia

A general purpose agent harness for local workflow automation.

## Stack

- Python managed by `uv`
- Pydantic AI for agent runtime primitives and model/tool adapters
- Pydantic Graph for control flow
- Pydantic Evals for LLM testing
- Pydantic AI Harness for shell execution and filesystem traversal utilities
- Click for the CLI
- Textual for limited TUI convenience screens
- Pydantic Settings and `.env` for configuration
- Ruff, mypy, pyright, and pytest for quality checks

## Setup

```sh
uv sync
```

Optional model configuration can be copied from `.env.example`:

```sh
cp .env.example .env
```

## Usage

```sh
uv run cass run "hello"
```

To make a real Codex SDK call with your Codex auth:

```sh
uv run cass ask "Write one short sentence about cassiopeia."
```

## Checks

```sh
scripts/verify
```
