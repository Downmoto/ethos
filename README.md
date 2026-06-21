# cassiopeia

A general purpose agent harness for local workflow automation.

## Stack

- Python managed by `uv`
- LangGraph for agent control flow
- LangChain integrations for model/tool adapters
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
uv run cass ask "Write one short sentence about Cassiopeia."
```

## Checks

```sh
scripts/verify
```
