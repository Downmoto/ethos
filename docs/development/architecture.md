# Architecture

[Developer documentation](index.md)

Ethos is the brain: it owns application behaviour, workspace-scoped state,
model execution, and lifecycle events. Vox is its sole external protocol. A
separately developed body can consume that protocol without becoming part of
Ethos.

## System shape

```text
local CLI -------------------+
                             |
                             v
            --------------------------------------------
            |           Ethos service                  |
            |          /      |       \                |
            | workspaces   sessions   AgentRuntime     |
            |     |           |            |           |
            | filesystem   JSON files   provider model |
            --------------------------------------------
                             ^
                             |
Vox REST protocol -----------+
```

`ethos.service.Ethos` is the shared application boundary. The CLI calls it
directly. HTTP routes authenticate requests, translate protocol data, and call
the same methods. Neither interface owns workspace, session, event, or model
behaviour.

## Core boundaries

### Service

The service composes workspace and session managers, storage, lifecycle event
emission, and the lazily created agent runtime. Its methods represent the
currently supported application operations.

Request context records which trusted adapter initiated an operation. It is
event metadata, not a generic command envelope or an authentication system.

### Vox protocol

`ethos.gateway.vox` owns FastAPI request validation, bearer authentication,
HTTP error mapping, resource response models, and server-sent event framing.
It preserves the workspace and session endpoints while remaining ignorant of
filesystem and model implementation details.

Chat streams are a backwards-incompatible discriminated protocol. Every SSE
payload has `kind: "chunk"` for answer/reasoning text, usage, and completion,
or `kind: "approval"` with `approval_id`, `call_id`, `tool_name`, validated
`arguments`, `effect`, `reason`, `created_at`, `workspace`, and `session_id`.

Pending requests are resolved through authenticated endpoints:

```text
POST /workspaces/{workspace}/sessions/{session}/approvals/{approval}/approve
POST /workspaces/{workspace}/sessions/{session}/approvals/{approval}/deny
```

Both return the resumed chat as SSE. An approval from another session is 404;
a stale, executing, completed, denied, or indeterminate request is 409.

A bearer token is mandatory when Vox binds beyond loopback. The protocol does
not own or implement the external consumer also named Vox.

### CLI

The Click CLI is a local interface. It opens an Ethos service lifetime and
calls it directly rather than sending HTTP requests. Formatting, terminal
progress, and output-file handling remain CLI concerns.

For a write-tool approval it prints the exact tool name and validated JSON
arguments, then asks once with denial as the default. If stdin is not a
terminal, it denies without prompting and resumes the model with an error tool
result.

### Server lifecycle

`ethos start` runs Vox in the foreground. `ethos start --bg` launches a single
tracked child with a private Unix control socket. `ethos stop` requests that
child's shutdown and succeeds silently when no background child exists.
Foreground servers are deliberately not affected by `ethos stop`.

## State and execution

A workspace identifies a user-owned project directory. A session belongs
permanently to one workspace, stores one conversation's model messages, and
lives in the Ethos home. For each turn, `AgentRuntime` resolves the global
settings, streams model output, and atomically replaces history before emitting
its completion chunk.

`ContextBuilder` is the boundary between that canonical stored history and a
model request. It owns the base Ethos system instruction, appends run-only
date, time, and timezone context, appends run-only system instructions, and
attaches available tool definitions without mutating or persisting the
constructed context.

Capabilities contribute only run-scoped instructions and tools. The runtime
resolves them in registration order for each session turn, rejects duplicate
tool names before contacting the model, and gives the resulting values to the
context builder and mandatory tool executor. The runtime depends on the
capability protocol rather than naming concrete capabilities.

The first production capability exposes `list_files` and `read_file`.
`list_files` returns at most 1,000 sorted, workspace-relative entries from one
directory; `read_file` reads at most 100 KiB of UTF-8 text from one file.
Absolute paths, incompatible path types, traversal outside the workspace, and
symlinks resolving outside the workspace fail without exposing file contents.

The skills capability implements Agent Skills through progressive disclosure.
It adds only discovered names and descriptions to run-scoped context. The
model can then load one complete `SKILL.md` body with `activate_skill` and read
referenced bundled files individually with `read_skill_resource_file`. Skill
metadata is parsed without executing embedded content, and both skill tools
are bounded read operations routed through the normal tool policy. Discovery
problems are recorded as typed `skill.diagnostic` lifecycle events.

Every request also receives a run-only system instruction containing the
active workspace name and path plus the session ID. This lets the model reason
about its location without adding operational context to persisted history.

Lifecycle events are always emitted and stored before in-process listeners
run. Domain mutations and event writes are not one transaction, so an event
failure can follow a successful filesystem mutation.

The runtime records finer-grained events in the same envelope store. A single
`run_id` correlates model requests, tool preparation and execution, approval
pauses and resumptions, and the terminal run outcome. These traces contain
identifiers, bounded status values, token usage, and failure categories; they
exclude prompts, model text and reasoning, tool arguments and results,
credentials, and exception messages. They are internal events, not CLI output
or Vox server-sent events. `session.chat` remains the coarse application
operation emitted by the service.

Runtime events are awaited in execution order. In particular, an approval and
`tool.execution.started` are durable before a write tool can run, while
`tool.execution.completed` follows the durable result. A cancelled process can
therefore leave a started event without a terminal event. Ethos preserves that
incomplete trace instead of inventing a completion during cleanup.

## Deferred AI design

Personas, persona memory, cross-persona conversation, and additional
capabilities are intentionally outside this base refactor. No placeholder
persona abstractions exist until those behaviours are designed.

## Current limits

- Session turns are serialised in-process and by a per-session OS file lock
  across processes on the same filesystem.
- Session files have atomic replacement but no cross-process transaction.
- The application event database is write-only through the current API.
