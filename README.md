# ethos

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
uv run ethos init
uv run ethos onboard
```

## Usage

```sh
uv run ethos ask "hello"
uv run ethos ask "write a report" --to report.md
```

File output is streamed incrementally and includes a token tracker on stderr.
Existing output files are never overwritten.

## Checks

```sh
scripts/verify.sh
```

## Development

Start with the [developer documentation](docs/development/index.md) for the
architecture, core contracts, and contribution guidance.
