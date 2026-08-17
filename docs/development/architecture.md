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

A bearer token is mandatory when Vox binds beyond loopback. The protocol does
not own or implement the external consumer also named Vox.

### CLI

The Click CLI is a local interface. It opens an Ethos service lifetime and
calls it directly rather than sending HTTP requests. Formatting, terminal
progress, and output-file handling remain CLI concerns.

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

Lifecycle events are always emitted and stored before in-process listeners
run. Domain mutations and event writes are not one transaction, so an event
failure can follow a successful filesystem mutation.

## Deferred AI design

Personas, persona memory, cross-persona conversation, and expanded skill/tool
execution are intentionally outside this base refactor. No placeholder persona
abstractions exist until those behaviours are designed.

## Current limits

- Session serialisation locks are process-local.
- Session files have atomic replacement but no cross-process transaction.
- The application event database is write-only through the current API.
