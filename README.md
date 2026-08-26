# Ethos

Ethos is an early-stage personal AI runtime. It is intended to become the
shared brain behind agents and the interfaces that use them.

The goal is broader than wrapping model inference: models, tools, workflows,
hooks, personas, and interfaces should be able to work together around durable
context.

> [!IMPORTANT]
> Ethos is in alpha. It is not ready for general use, breaking changes are
> expected, and stored data or protocol compatibility is not guaranteed yet.

## Current state

The current repository is foundational. It contains an in-progress agent loop
and enough provider, session, workspace, capability, CLI, and REST support to
build and exercise that loop. These pieces are infrastructure, not the
finished product or its defining feature set.

Ethos can currently connect to OpenAI, Google, or Ollama models and run locally
through its CLI or Vox REST interface. The implementation is still changing
quickly as the core runtime takes shape.

## Try it

Ethos currently requires Python 3.12 or 3.13 and `uv`.

```sh
git clone https://github.com/Downmoto/ethos.git
cd ethos
uv sync

uv run ethos init
uv run ethos onboard
uv run ethos ask "Hello"
```

`ask` starts a fresh session and prints its ID with the response. Run
`uv run ethos --help` to see the available workspace, capability, session, and
server commands.

To run the Vox API:

```sh
uv run ethos start
```

## Configuration and data

Ethos keeps its configuration, workspaces, sessions, skills, and local event
data under `~/.ethos`. Provider and runtime settings live in
`~/.ethos/config.yaml`; global capability settings and sparse workspace
overrides live in `~/.ethos/capabilities.yaml`.

For example, these commands lower the global skill limit and disable skills in
one workspace:

```sh
uv run ethos config capability set skills '{"max_skills": 50}'
uv run ethos config capability set skills '{"enabled": false}' \
  --workspace my-project
```

Application state is stored locally, but model requests are sent to the
provider you select. Session history can contain reasoning, tool arguments,
tool results, and file contents, so treat it as sensitive data.

## Direction

The immediate work is still completing and refining the agent loop. The
broader roadmap includes workflows, hooks, personas, and the systems needed to
compose them without turning Ethos into a collection of unrelated features.

That direction will become more concrete as the underlying contracts settle.

## Development

Contributor architecture and runtime contracts live in the
[developer documentation](docs/development/index.md).

```sh
./scripts/verify.sh
```
