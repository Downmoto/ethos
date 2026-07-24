# Commands, events, and gateways

[Developer documentation](index.md)

The command layer is the stable seam between Ethos behaviour and its
transports. Gateways translate external protocols into commands. Events record
what commands did and provide in-process extension hooks.

## Command contract

`CommandRequest` describes an invocation without referring to Click, HTTP, or
Discord:

| Field | Meaning |
| --- | --- |
| `name` | Namespaced operation such as `session.chat` |
| `arguments` | JSON-compatible values validated by the handler |
| `source` | Stable adapter identity such as `cli`, `vox`, or `discord` |
| `owner_id` | Adapter-supplied identity associated with the request |
| `external_context` | Adapter-supplied string metadata such as channel or client IDs |

Command names contain at least one namespace separator and use lower-case
letters and digits. Namespacing distinguishes universal operations from
adapter-owned operations and makes accidental collisions less likely.

`source`, `owner_id`, and `external_context` are claims supplied by the
adapter. The dispatcher does not authenticate them. Authentication and
authorisation must happen before an adapter constructs the request:

- Vox verifies its configured bearer token. It may run without a token only
  on a loopback host.
- Discord rejects users outside its configured allow-list and performs
  Discord permission checks for privileged Discord operations.
- The local CLI uses the operating-system username as `owner_id`.

`allowed_sources` is a command-routing restriction, not user authentication.
It prevents, for example, a CLI request from invoking a Discord-owned command,
but it does not prove who sent a Discord request.

## Dispatch and registration

`CommandDispatcher` stores one handler for each command name. Registration
rejects invalid names, duplicates, and invalid source allow-lists. Execution
rejects unknown commands and sources not permitted by the registration.

Handlers are asynchronous iterators. This single contract supports both
one-response management operations and incremental model output without a
second dispatch mechanism.

Universal commands are registered by feature-level functions such as
`register_workspace_commands` and `register_session_commands`. A gateway calls
`register_commands` only for operations it owns.

### Handler responsibilities

A command handler should:

1. validate `request.arguments` with a feature-specific model;
2. invoke a manager, runtime, or injected collaborator;
3. yield intermediate responses when the operation is streamed;
4. emit the lifecycle event when its represented state is reached;
5. yield the completion response.

Handlers should not parse HTTP requests, call Discord presentation APIs, or
print terminal output.

Arguments use `extra="forbid"` so an outdated or misspelled caller fails
instead of silently dropping input. Adding an argument is therefore an
explicit contract change across every adapter that uses the command.

## Response streams

`CommandResponse` has four independent channels:

| Field | Purpose |
| --- | --- |
| `text` | Human-readable output or one streamed text chunk |
| `data` | JSON-compatible structured output for adapters |
| `usage` | Provider-neutral input and output token counts |
| `done` | Marks successful completion of a streaming operation |

Adapters should consume every response unless cancellation is intentional.
Stopping consumption of `session.chat` early can cancel the runtime before it
persists the updated history.

For chat, usage snapshots are cumulative values copied from the active
Pydantic AI run. They are not per-chunk deltas. The final response contains
`done=True` only after session history has been persisted.

Management commands currently yield exactly one response. Vox's non-streaming
routes enforce that expectation. Chat is exposed separately as a server-sent
event stream.

## Error ownership

Managers and handlers raise domain-facing Python errors:

- `FileExistsError` for identity conflicts;
- `FileNotFoundError` for missing workspaces or sessions;
- `ValueError` for invalid state or arguments;
- dispatcher-specific errors for registration, command names, and sources.

Adapters translate errors into their native presentation. Vox maps common
domain errors to HTTP status codes, Discord sends selected errors as messages,
and the CLI renders them as Click errors.

Do not move transport-specific error types into the managers or command
handlers.

## Event contract

An `EventEnvelope` contains stable cross-cutting metadata:

- generated UUID and timezone-aware creation time;
- canonical `EventType`;
- source name and optional detail;
- filter and debugging tags;
- a versionable `EventPayload`.

Command events use the request source as the event source, the command name as
its detail, and include request ownership/context in their payload. Workspace
and session identities are repeated as tags so later filtering need not parse
feature-specific payload fields.

### Payload evolution

The generic `EventPayload` allows unknown fields. Stored records should remain
readable through this model when newer writers add fields.

Feature-specific payload models are strict at write time. They catch mistakes
while producing a known schema. Their `schema_name` identifies the payload
family and `schema_version` starts at one.

When changing a payload incompatibly:

1. increment its schema version;
2. keep generic stored-event loading tolerant;
3. make consumers branch on schema name and version;
4. do not require old records to validate as the newest feature model.

### Emit ordering

When events are enabled, `EnvelopeEventEmitter`:

1. writes the event to Turso and commits it;
2. delivers it to matching in-process listeners in registration order;
3. returns the emitted envelope.

Storage-before-delivery lets a listener observe an event that is already
durable. Listener failures do not prevent later listeners from running; all
failures are raised together as an `ExceptionGroup`.

When events are disabled, emission returns immediately without storage or
listener delivery.

### Failure is not transactional

Command handlers mutate domain state before emitting their event. Management
commands emit before their single response. Chat may expose text chunks first,
then emits after session persistence and before its final `done` response.
Consequently:

- a workspace or session operation may have succeeded even if event storage or
  a listener then fails;
- the event may already be committed before a listener failure is reported;
- retrying a failed-looking create command may encounter an existing object.

The event system is not a transaction coordinator or rollback mechanism.
Contributors must not promise all-or-nothing behaviour across domain files,
event storage, and listeners without adding an explicit transaction design.

Read commands such as list and show also emit events. Events currently
represent command activity, not only state changes.

## Gateway contract

A `Gateway` has:

- a stable, unique `name`;
- an optional `register_commands` hook for gateway-owned operations;
- a long-running `run(execute)` method.

The gateway receives a `CommandExecutor`, not the concrete dispatcher or
domain managers. It translates native input into requests and renders the
resulting response stream.

Universal behaviour stays behind the executor. This makes a gateway an
adapter rather than an alternative application core.

## Running gateways

Selected gateways run concurrently in one supervisor process. They share the
same dispatcher, managers, storage connection, and lazily created runtime.
Gateway-owned commands are registered before serving begins.

The supervisor uses an `asyncio.TaskGroup`. A gateway failure therefore
cancels sibling gateways and waits for their cleanup before the failure leaves
the process. A gateway that returns normally also requests supervisor
shutdown; long-running gateways are expected to run until stopped.

Vox disables Uvicorn's own signal capture so the Ethos supervisor remains the
single owner of process shutdown. On cancellation, Vox requests a graceful
server exit and waits for it.

## Singleton process and control socket

`GatewaySupervisor` owns these paths beneath the Ethos home:

```text
runtime/
├── gateways.lock
├── gateways.pid
└── gateways.sock
```

An exclusive, non-blocking `flock` on `gateways.lock` is the authority for
singleton ownership. PID and socket files are discovery and control data; they
are not treated as proof that a process is alive. After acquiring the lock,
the supervisor replaces stale PID/socket state.

The runtime directory is permissioned to `0700`; the lock, PID, and Unix
socket are `0600`. The local control protocol is newline-delimited, strictly
validated JSON with `status` and `stop` actions.

`stop` may cancel selected gateways. An empty selection means all currently
running gateways. The supervisor exits and removes its PID and socket when no
gateways remain.

SIGINT and SIGTERM cancel all running gateway tasks and request supervisor
shutdown.

### Background startup

`ethos start --bg` starts a detached Python process with standard output and
errors appended to `~/.ethos/logs/gateways.log`. The parent polls the control
socket for up to five seconds and verifies that the reported supervisor PID is
the child it launched.

The filesystem lock, rather than this polling check, remains the final defence
against two supervisors.

## Adding a universal command

1. Choose a namespaced command name.
2. Define a strict argument model near the feature handler.
3. Add manager/runtime behaviour at the layer that owns the invariant.
4. Register the handler with injected dependencies.
5. Return both useful human text and stable structured data where applicable.
6. Add an event type and versionable payload if the operation is observable.
7. Expose the command from each relevant adapter without duplicating it.
8. Test dispatch, source restrictions, streaming, and relevant failure order.

## Adding a gateway

1. Implement `Gateway` with a stable name.
2. Authenticate native callers before constructing `CommandRequest`.
3. Populate ownership and external context from the native protocol.
4. Translate native operations to existing universal commands.
5. Register only genuinely gateway-owned commands.
6. Consume and render streamed responses without changing their semantics.
7. Clean up native resources when `run` is cancelled.
8. Add the gateway to application configuration and `_make_gateways`.

Do not give a new gateway direct access to workspace or session persistence
when the command executor already provides the required operation.
