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
- Pydantic Settings and YAML for configuration
- Ruff, mypy, pyright, and pytest for quality checks

## Setup

```sh
uv sync
```

## Usage

```sh
uv run cass run "hello"
```

## Checks

```sh
scripts/verify
```
