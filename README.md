# ethos

A personal AI brain with one replaceable REST body protocol.

## Stack

- Python managed by `uv`
- Pydantic AI for agent runtime primitives and model/tool adapters
- Click for the CLI
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
uv run ethos start
uv run ethos start --bg
uv run ethos stop
```

File output is streamed incrementally and includes a token tracker on stderr.
Existing output files are never overwritten.

`ethos start` runs the Vox REST server in the foreground. `--bg` runs the same
server as a tracked background process; `ethos stop` is a no-op when no
background process is running.

## Checks

```sh
scripts/verify.sh
```

## Development

Start with the [developer documentation](docs/development/index.md) for the
architecture, core contracts, and contribution guidance.
