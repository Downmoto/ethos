# Architecture

[Developer documentation](index.md)

This document describes the implemented architecture of Ethos. It is a map of
the system and its boundaries, not a roadmap.

Ethos is a local agent harness. Its core turns input from different transports
into the same commands, runs those commands against workspace-scoped state, and
streams transport-neutral responses back to the caller.

## System shape

```text
CLI                       Vox                       Discord
 |                         |                            |
 +-------------------------+----------------------------+
                           |
                     CommandRequest
                           |
                    CommandDispatcher
                           |
             +-------------+-------------+
             |                           |
      command handlers            gateway-owned handlers
             |
    +--------+---------+------------------+
    |                  |                  |
workspaces          sessions          AgentRuntime
    |                  |                  |
filesystem         JSON files      model provider
    |                  |                  |
    +---------> workspace environment <---+
                           |
                    CommandResponse stream

Command handlers also emit lifecycle events to the event store and any
in-process listeners.
```

The important boundary is the command dispatcher, not any particular user
interface. CLI functions, HTTP routes, and Discord callbacks are adapters.
They translate native input into a `CommandRequest` and render streamed
`CommandResponse` values. Domain behaviour belongs behind that boundary so
that every transport observes the same rules.

## Core design rules

### Transports do not own universal behaviour

Workspace and session operations are universal commands. A gateway may expose
them, but it must not reimplement them. This keeps validation, persistence,
event emission, and error behaviour consistent across the CLI, Vox, and
Discord.

A gateway may own a command that only makes sense in that transport. For
example, Discord owns `discord.channel.create`. Such commands are registered
on the same dispatcher and restricted to the appropriate request source.

See [Commands, events, and gateways](commands-events-and-gateways.md).

### Agent state is workspace- and session-scoped

A workspace defines the configuration and capabilities available to a run. A
session belongs permanently to one workspace and stores one conversation's
model messages. The runtime resolves the workspace environment for every turn,
then runs with only that session's history.

This separation prevents conversation history and workspace policy from
silently leaking between runs. It also lets configuration changes take effect
on a later turn without rewriting stored sessions.

See [Workspaces and runtime](workspaces-and-runtime.md).

### Streaming crosses every layer

Model output is incremental. The runtime yields `PromptStreamEvent` values,
the session command translates them into `CommandResponse` values, and the
calling adapter renders those values.

Do not collect a streamed chat response in the core merely because one
transport wants a single value. Buffering, chunking, and framing are adapter
concerns. For example, Vox frames command responses as server-sent events,
while Discord collects or chunks text to fit its API.

### Validation happens at each boundary

Pydantic models reject unknown fields in settings, command requests, command
arguments, control messages, and write-time event payloads. Managers also
validate filesystem identities and layout.

This repetition is intentional: each boundary validates the representation it
owns. A transport authenticating a caller does not replace command argument
validation, and a valid command does not make an arbitrary filesystem path
safe.

### Dependencies are assembled explicitly

`ethos.app` is the composition root. `_build_dispatcher` constructs managers,
the event emitter, the workspace environment resolver, and the lazily created
agent runtime, then registers the universal commands.

Gateways receive only the command executor they need. Command registration
functions receive their managers and collaborators explicitly. Keep new
construction and wiring in the composition root rather than adding module
globals or allowing adapters to construct separate domain services.

## Main execution paths

### Universal command

1. An adapter creates a `CommandRequest`.
2. `CommandDispatcher` selects the registered handler and enforces any source
   restriction.
3. The handler validates its own `arguments`.
4. The handler calls a workspace or session manager.
5. The handler may yield intermediate streaming responses.
6. The handler emits a lifecycle event when the operation reaches the state
   represented by that event.
7. The handler yields its completion response.
8. The adapter renders text, structured data, usage, or completion state.

Management commands emit after their domain operation and before their single
response. Chat can yield text first, then emits after history persistence and
before its final `done` response. Those orderings have observable failure
semantics described in
[Commands, events, and gateways](commands-events-and-gateways.md).

### Conversation turn

1. `session.chat` validates the workspace, session ID, and prompt.
2. `AgentRuntime` serialises the turn with other turns for the same session.
3. It loads the session and rejects archived sessions.
4. It resolves current workspace configuration and capabilities.
5. It constructs the configured provider model.
6. It streams text and cumulative usage from Pydantic AI.
7. It atomically replaces the stored model history after the stream completes.
8. It yields a final `done` event.
9. The command handler emits `session.chat`.

The convenience `ethos ask` command creates a new session in the default
workspace for each invocation. It is therefore a one-shot interaction, unlike
`ethos session chat`, which continues a named session.

## Module ownership

| Area | Owns | Does not own |
| --- | --- | --- |
| `commands` | Transport-neutral contracts, dispatch, argument validation, domain command handlers | HTTP routes, Discord presentation, model execution |
| `gateways` | Transport translation, transport authentication, response rendering, long-running adapter lifecycle | Universal workspace or session behaviour |
| `workspaces` | Workspace identity, layout, discovery, and structural validation | Effective settings or agent execution |
| `environments` | Effective settings, tools, skills, and workspace-scoped dependencies | Session history |
| `sessions` | Conversation identity, history persistence, and archival | Model/provider execution |
| `runtime` | One model turn, streaming, and per-session in-process serialisation | Transport formatting or event persistence |
| `events` | Lifecycle event schema, storage-before-delivery, and listeners | Domain mutation transactions |
| `storage` | Application database connection and event writes | Session files or workspace layout |
| `app` | Dependency composition and CLI presentation | Reusable domain rules |

## Adding behaviour

Before adding a new path, identify which boundary owns it:

- A new operation available to several transports is a universal command.
- A new external interface is a gateway that translates to existing commands.
- An operation meaningful only to one transport may be a gateway-owned command
  with an `allowed_sources` restriction.
- A new workspace capability belongs in environment resolution.
- A new conversation rule belongs in the session manager or runtime, depending
  on whether it concerns persistence or model execution.
- A new lifecycle record needs an event type and a versionable payload schema.

Prefer extending these seams over calling across them. In particular, gateways
should not write session files, command handlers should not know about FastAPI
or Discord, and the runtime should not render output.

## Current limits

These constraints describe the current implementation and should be considered
before building on it:

- Commands and event listeners are in-process; there is no durable queue.
- Session serialisation locks are process-local.
- Session files have atomic replacement but no cross-process transaction.
- The application event database is write-only through the current `Storage`
  API.
- The composition root currently supplies an empty tool catalogue.
- Selected skills are resolved into `WorkspaceEnvironment`, but Ethos does not
  currently load their contents into the model prompt.

Document or deliberately change these constraints when implementing features
that depend on stronger guarantees.
