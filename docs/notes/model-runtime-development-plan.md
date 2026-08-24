# Ethos-owned model runtime development plan

Status: proposed. This document is an implementation plan, not a description
of current behaviour.

## Outcome

Replace Pydantic AI's `Agent` loop with an Ethos-owned runtime. Preserve only
the intentional runtime behaviours listed in this plan; persisted-data and
API compatibility are not requirements during alpha.

The target dependency direction is:

```text
CLI / Vox
    -> Ethos service
        -> Ethos runtime
            -> Ethos model protocol
                -> LiteLLM adapter
                    -> provider API
            -> Ethos tool executor
                -> permission policy
                -> registered tool
```

Only an adapter may import LiteLLM types. No provider adapter may execute a
tool or mutate conversation state.

## Fixed decisions

These decisions remove choices that would otherwise block implementation.

1. Use Pydantic models for persisted and boundary values. Models are frozen,
   reject extra fields, and use discriminated unions for parts and events.
   Pydantic remains an Ethos dependency.
2. Use the LiteLLM Python SDK as the first and only production inference
   adapter. Do not use its proxy, router, retries, fallbacks, callbacks, or
   agent features.
3. Preserve all three configured provider names: `openai`, `google`, and
   `ollama`. `AIProvider` becomes a factory for `LiteLLMModel`; it stops
   returning Pydantic AI models.
4. Build and test the new path beside the current runtime. Do not switch the
   service to it until text streaming, usage, persistence, failure, and
   concurrency parity pass.
5. Streaming is part of the first production cutover, not a later feature.
   `request()` and `stream()` must produce the same final `ModelResponse`.
6. A response may contain text and tool calls in provider order. Tool calls
   carry their raw JSON argument string; argument parsing and validation occur
   only in the tool executor.
7. Execute multiple tool calls sequentially in response order. “Parallel tool
   calls” means the model may request several calls in one response; it does
   not mean concurrent side effects.
8. Every tool declares `READ` or `WRITE`. Every call passes through a policy.
   The first policy allows reads and denies writes. Approval is a separate
   milestone because it changes the public protocol.
9. Do not add a generic capability hook system. Add only the two extension
   points needed by the first capabilities: contributed instructions and
   contributed tools.
10. Do not add a second production adapter merely to prove the protocol. The
    fake model proves substitution. Add a direct provider adapter only when a
    real LiteLLM limitation justifies it.
11. Make a clean storage break. Existing session files are disposable and
    receive no compatibility reader, migration, backup, schema version, or
    deprecation period. Reinitialise the development Ethos home after cutover.
12. Remove Pydantic AI completely in the text-runtime cutover commit. Do not
    retain it as a migration dependency.

LiteLLM's SDK documents a common completion shape and streaming for OpenAI,
Vertex AI/Gemini, and Ollama. Treat that as adapter input, never as an Ethos
domain contract: <https://docs.litellm.ai/>.

## Non-goals

Do not add any of the following while executing this plan:

- provider routing, fallback, or automatic retry;
- structured output;
- images, audio, or other multimodal parts;
- exposed reasoning or chain-of-thought;
- concurrent tool execution;
- MCP or subagents;
- prompt caching or cost accounting;
- cross-process session locking;
- a direct OpenAI, Google, Anthropic, or Ollama adapter;
- speculative lifecycle hooks;
- legacy session migration or backward compatibility;

## Contracts that must not regress

The current repository already guarantees the following. Every cutover phase
must preserve them.

- CLI and Vox receive non-overlapping text chunks and a final `done=True`
  chunk only after persistence succeeds.
- Usage exposed by `ChatChunk` contains non-negative input and output tokens.
- A cancelled, abandoned, or failed text-only turn does not replace stored
  history.
- Turns for one session are serialised within a runtime instance; distinct
  sessions may run concurrently.
- Archived sessions are readable but reject new turns and history changes.
- Session writes remain atomic and permissioned `0600`.
- Provider credentials never appear in representations, stored messages,
  events, or raised error text.
- The service emits `SESSION_CHAT` with the persisted session state after a
  completed turn.
- The CLI and Vox response schemas do not change before the approval
  milestone explicitly changes them.

## Target domain contracts

Create these contracts in `src/ethos/models.py`. Keep them together until the
file becomes difficult to navigate; do not pre-create a model package.

```python
class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class TextPart(BaseModel):
    kind: Literal["text"] = "text"
    text: str


class ToolCallPart(BaseModel):
    kind: Literal["tool_call"] = "tool_call"
    call_id: str
    name: str
    arguments_json: str


class ToolResultPart(BaseModel):
    kind: Literal["tool_result"] = "tool_result"
    call_id: str
    name: str
    content: str
    is_error: bool = False


MessagePart = Annotated[
    TextPart | ToolCallPart | ToolResultPart,
    Field(discriminator="kind"),
]


class Message(BaseModel):
    role: Role
    parts: tuple[MessagePart, ...]


class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters_schema: dict[str, object]


class ModelRequest(BaseModel):
    messages: tuple[Message, ...]
    tools: tuple[ToolDefinition, ...] = ()


class Usage(BaseModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class FinishReason(StrEnum):
    STOP = "stop"
    LENGTH = "length"
    TOOL_CALL = "tool_call"
    CONTENT_FILTER = "content_filter"
    ERROR = "error"
    OTHER = "other"


class ModelResponse(BaseModel):
    parts: tuple[TextPart | ToolCallPart, ...]
    usage: Usage = Field(default_factory=Usage)
    finish_reason: FinishReason = FinishReason.OTHER
    provider_response_id: str | None = None


class TextDelta(BaseModel):
    kind: Literal["text_delta"] = "text_delta"
    text: str


class ResponseCompleted(BaseModel):
    kind: Literal["response_completed"] = "response_completed"
    response: ModelResponse


ModelEvent = Annotated[
    TextDelta | ResponseCompleted,
    Field(discriminator="kind"),
]


class Model(Protocol):
    async def request(self, request: ModelRequest) -> ModelResponse: ...

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]: ...
```

All models above use `ConfigDict(frozen=True, extra="forbid")`. Apply these
validation rules:

- `Message.parts` is non-empty.
- `ModelResponse.parts` is non-empty.
- `TextPart.text`, tool names, tool descriptions, and call IDs are non-empty.
- Tool names match `^[A-Za-z][A-Za-z0-9_-]{0,63}$`.
- `ToolDefinition.parameters_schema` is a JSON Schema object with
  `type == "object"`. Reject other top-level schema types.
- Assistant messages contain only text and tool-call parts.
- Tool messages contain exactly one tool-result part.
- User and system messages contain only text parts.
- `ResponseCompleted` occurs exactly once and is the last stream event.
- Concatenating all `TextDelta.text` values equals the concatenated text in
  its completed response.

Do not add reasoning, image, audio, or provider-metadata parts to make the
union “future-proof”.

## Session storage and durability

Change `Session.messages` directly to `tuple[Message, ...]`. Do not add a
schema version or accept the old Pydantic AI message shape. Update fixtures
and tests in place. Before manually running the cut-over application, reset
the development home with the existing reinitialisation command.

An old session file failing validation after cutover is expected. Do not add
special-case parsing, translation, backup, or recovery code for it.

For text-only turns, preserve today's commit rule: build the new history in
memory and replace the session once, after the completed model response.

For tool turns, use checkpoints:

1. Finish and validate the entire model stream.
2. Persist the user message and assistant response before executing any tool.
3. Execute one permitted tool call.
4. Persist its tool-result message before executing the next call.
5. After all calls have results, request the next model response.

If the process dies after a tool side effect but before its result is
persisted, the session contains an assistant tool call without a matching tool
result. On the next turn, raise `UnresolvedToolCall` and do not retry it. This
is an explicit indeterminate state; automatic retry could duplicate a side
effect. Recovery UI is outside this plan.

## Blocking milestone rules

Milestones are executed in order. A milestone is complete only when:

1. every listed implementation task is complete;
2. every listed test exists and passes;
3. `./scripts/verify.sh` passes;
4. `git diff --check` passes;
5. source and developer documentation describe the resulting current
   behaviour; and
6. no task from a later milestone was pulled forward except a type or helper
   strictly required to compile the current one.

Each milestone is one Conventional Commit using the stated commit subject.

## Milestone 0 — Freeze intentional runtime invariants

Commit: `test: freeze agent runtime contracts`

Implementation:

- Add characterisation tests for streaming chunk boundaries, final completion
  ordering, provider failure, cancellation, abandoned iteration, persistence
  failure, archived sessions, same-session serialisation, and distinct-session
  concurrency.
- Add service tests proving `SESSION_CHAT` is emitted after persistence and is
  still emitted on an incomplete stream according to current service
  behaviour. If that current fallback emission is unintended, resolve it here
  and document the chosen contract before continuing.
- Record the current provider-name/model-name/base-URL mapping in provider
  tests for OpenAI, Google, and Ollama.

Exit criteria:

- Tests fail if any current contract listed above changes.
- No production behaviour changes.

## Milestone 1 — Add Ethos model values and fake model

Commit: `feat: add ethos model contracts`

Implementation:

- Add the target domain contracts and validators to `src/ethos/models.py`.
- Add `FakeModel` under `tests/fakes.py`. It accepts a queue of complete
  responses or exceptions and records every request. Its stream emits caller-
  supplied text chunk boundaries followed by one completion event.
- Do not touch `AgentRuntime`, `AIProvider`, or persisted sessions.

Tests:

- JSON round trips for every value and every union variant.
- Rejection of empty/invalid parts, roles, call IDs, tool names, and schemas.
- Fake request recording, queued responses, queued errors, and stream
  completion invariants.

Exit criteria:

- No file outside `models.py` and test support imports these contracts.
- No LiteLLM or Pydantic AI type appears in a public annotation in
  `models.py`.

## Milestone 2 — Implement the LiteLLM text adapter

Commit: `feat: add litellm model adapter`

Implementation:

- Add the LiteLLM SDK as a direct dependency with the minimum extras required
  by the three existing providers. Commit the lockfile.
- Add `LiteLLMModel` beside the current provider factory without routing
  production calls to it yet.
- Use `litellm.acompletion()` for both calls: `stream=False` for `request()`
  and `stream=True` for `stream()`. Do not call the synchronous SDK from the
  event loop.
- Inject the callable used to invoke LiteLLM into `LiteLLMModel`; tests replace
  it without network calls or monkeypatching LiteLLM internals.
- Map configured providers as follows:
  - OpenAI: `openai/<model_name>` and the configured key.
  - Google: `gemini/<model_name>` using Google AI Studio and the configured
    API key. Do not add Vertex AI in this milestone.
  - Ollama: `ollama/<model_name>`, configured base URL, and optional key.
    LiteLLM's native Ollama route expects the server root, so change the
    default and generated template during cutover to
    `http://localhost:11434`. Do not add a compatibility rewrite for the old
    `/v1` default.
- Convert only system, user, and assistant text messages.
- Implement both non-streaming and streaming text calls. The adapter assembles
  all chunks into one final Ethos response and maps usage and finish reason.
- Reject missing choices, multiple choices, invalid chunk order, tool calls,
  and unsupported content with `ModelProtocolError`.
- Wrap provider exceptions as `ModelProviderError` with a safe message and
  chained cause. Never include request headers or credentials.

Tests:

- Exact outbound payloads for all three provider mappings.
- Text, empty text, usage, finish-reason, and response-ID conversion.
- One-character, multi-chunk, and empty-delta streams.
- Provider errors before streaming and midway through streaming.
- Protocol errors for multiple choices and malformed responses.
- Credential redaction in `repr()` and exception strings.
- One opt-in real OpenAI text/stream smoke test marked `integration` and
  skipped unless its documented environment variable is present.

Exit criteria:

- Unit tests perform no network calls.
- `provider.py` is the only production module importing LiteLLM.
- The old runtime still serves production calls.

## Milestone 3 — Cut over storage and the text runtime

Commit: `refactor: replace pydantic ai agent runtime`

Implementation:

- Change `Session.messages` directly to `tuple[Message, ...]` and update
  `SessionManager.replace_messages()`. Do not accept the old shape.
- Update service history projection and session tests to use Ethos parts.
- Change the Ollama configuration default and generated template to
  `http://localhost:11434`.
- Refactor `AIProvider.model()` to return `LiteLLMModel` behind the Ethos
  `Model` protocol.
- Inject a model factory into `AgentRuntime`; the default factory resolves
  current settings once per turn as it does today.
- Replace `Agent.run_stream()` with `Model.stream()`.
- Append one user text message in memory, consume model events, yield the same
  `PromptStreamEvent` contract, and atomically persist user plus assistant
  messages only after `ResponseCompleted` validates.
- Preserve locks, archived-session checks, cancellation behaviour, abandoned-
  iterator behaviour, usage translation, and final completion ordering.
- Keep `PromptStreamEvent` and `ChatChunk` public shapes unchanged.
- Update runtime and service documentation to describe the Ethos runtime.
- Remove `pydantic-ai-slim` and its provider extras from `pyproject.toml`, then
  update the lockfile.
- Update README stack wording: LiteLLM is the provider adapter and Ethos owns
  the loop.

Tests:

- Preserve every Milestone 0 behavioural assertion while replacing its
  Pydantic AI models, messages, usage values, and stream fixtures with Ethos
  values and `FakeModel`.
- Test session creation, replacement, restart, archival, listing, and file
  permissions using Ethos messages.
- Verify every stored turn is exactly user then assistant, with no duplicate
  text caused by cumulative provider chunks.
- Verify a malformed stream and a persistence failure never yield
  `done=True`.

Exit criteria:

- `rg -n "pydantic_ai|pydantic-ai|Agent\(" src tests pyproject.toml README.md`
  returns no matches.
- CLI and Vox tests pass without schema changes.
- A manual `ethos ask` smoke test succeeds with one configured provider.
- New session files contain only Ethos message discriminators.

This is the first production cutover. Do not begin tools until it is stable.

## Milestone 4 — Add tool wire support to the adapter

Commit: `feat: add model tool call support`

Implementation:

- Enable `ModelRequest.tools` conversion to LiteLLM's tool schema.
- Convert provider tool calls to `ToolCallPart` without parsing
  `arguments_json`.
- Convert `ToolResultPart` messages back to provider tool-result messages.
- Extend streaming with internal tool-call delta assembly. Do not expose
  partial tool arguments above the model adapter. `ResponseCompleted` contains
  complete calls or the stream fails with `ModelProtocolError`.
- Preserve response order and provider call IDs exactly.
- Add `ModelFeatures` with one field, `tools: bool`. It is supplied by the
  model factory. Reject a request containing tools when false. Add other
  feature fields only with their implementation milestone.

Tests:

- No tools, one definition, and multiple definition payloads.
- One call, text plus call, and multiple calls in complete and streamed
  responses.
- Interleaved streamed argument fragments for multiple indexed calls.
- Empty, malformed, and non-object JSON argument strings remain representable.
- Tool results and error results map to the matching call ID.
- Missing, duplicate, or changing call IDs fail at the adapter boundary.

Exit criteria:

- Runtime behaviour remains text-only because no registry is connected.
- No natural-language tool-call parsing exists.

## Milestone 5 — Add registry, validation, and mandatory policy

Commit: `feat: add policy guarded tool execution`

Implementation in `src/ethos/tools.py`:

```python
class ToolEffect(StrEnum):
    READ = "read"
    WRITE = "write"


class Tool(Protocol):
    definition: ToolDefinition
    effect: ToolEffect
    arguments_type: type[BaseModel]

    async def execute(self, arguments: BaseModel) -> str: ...


class ToolPolicy(Protocol):
    async def decide(self, call: ToolCallPart, tool: Tool) -> Allow | Deny: ...
```

- `ToolRegistry` rejects duplicate names and returns definitions in
  registration order.
- `ToolExecutor` resolves the name, parses `arguments_json` as one JSON object,
  validates it with `arguments_type`, asks policy, and only then calls the
  tool.
- Unknown tools, JSON errors, validation errors, denials, and tool exceptions
  become bounded, non-secret `ToolResultPart(is_error=True)` values. Runtime
  bugs and cancellation are not converted to tool results.
- Run each tool under `asyncio.timeout(30.0)`. A timeout becomes the stable
  error content `tool execution timed out`; do not include exception text or
  argument values in model-facing errors.
- The default policy allows `READ` and denies `WRITE` with a stable reason.
- Do not add decorators, discovery, dependency injection containers, or a
  registry hierarchy.

Tests:

- Registration order, duplicates, lookup, and unknown names.
- JSON object enforcement and Pydantic argument validation.
- Policy runs before execute for both effects.
- Denied and invalid calls never invoke the tool.
- Success and safe error conversion.
- `CancelledError` propagates.

Exit criteria:

- There is no code path from `ToolCallPart` to `Tool.execute()` that bypasses
  `ToolPolicy`.
- No write tool can execute under the default policy.

## Milestone 6 — Add the bounded tool loop

Commit: `feat: add bounded ethos tool loop`

Implementation:

- Inject a `ToolRegistry` and `ToolExecutor` into `AgentRuntime`.
- When the registry is non-empty and `model.features.tools` is true, include
  its definitions in every request.
- After a completed response:
  - if it has no calls, persist and return;
  - if it has calls, checkpoint the user and assistant messages;
  - execute calls sequentially in response order;
  - checkpoint each result;
  - request the model again with the updated history.
- Set module constants `MAX_MODEL_ROUNDS = 8` and
  `MAX_TOOL_CALLS_PER_RESPONSE = 16`. Each model request consumes one round.
  If a response has more than 16 calls, execute none: persist the assistant
  response, append and persist one limit error result per call, then raise
  `AgentLimitError`. If round 8 returns calls, follow the same procedure for
  those calls and raise; never make a ninth model request.
- A response containing tool calls must have finish reason `TOOL_CALL` or
  `OTHER`; reject contradictory terminal reasons.
- Before accepting a new user turn, reject any stored assistant tool call that
  lacks exactly one later result with the same ID.
- Stream assistant text from every round through the existing chat stream.
  Yield exactly one final `done=True`, after the final assistant response is
  durable.

Tests:

- Immediate answer, one tool round, several rounds, and several calls in one
  response.
- Sequential execution order.
- Unknown tool, bad arguments, denial, and tool exception followed by a model
  recovery response.
- Both limits and off-by-one boundaries.
- Cancellation before a model checkpoint, during a tool, and after a result
  checkpoint.
- Persistence failure at each checkpoint.
- Detection of an unresolved call after simulated process death.

Exit criteria:

- A fake end-to-end conversation completes
  `user -> assistant call -> tool result -> assistant text` after a runtime
  restart between any two durable checkpoints.
- No write side effect occurs in this milestone.

## Milestone 6.5 — Add model reasoning streams

Commit: `feat: add model reasoning streams`

Implementation:

- Add `ReasoningEffort` with `none`, `low`, `medium`, and `high`; configure it
  under `provider.reasoning_effort`, defaulting to `none`.
- Add provider-neutral `ReasoningPart` and `ReasoningDelta` contracts. Allow
  reasoning only in assistant messages and model responses.
- Translate configured effort to LiteLLM's `reasoning_effort`. Continue sending
  `none` explicitly to Ollama so thinking is disabled by default.
- Convert LiteLLM textual `reasoning_content` in complete and streamed
  responses. Preserve its order relative to answer text and tool calls.
- Persist reasoning in assistant messages, but omit it from replayed provider
  messages. Textual reasoning is diagnostic output, not conversation context.
- Add `text_kind` to `PromptStreamEvent` and `ChatChunk` so reasoning and answer
  text remain distinct through Vox.
- Render reasoning on CLI stderr and answer text on stdout.
- Expose stored reasoning separately in history projections.
- Reject provider-native thinking blocks, reasoning items, signatures, and
  encrypted or opaque reasoning. Add an opaque contract only when a supported
  provider requires round-tripping it.

Tests:

- Configuration defaults, validation, onboarding, and provider translation.
- Complete and streamed reasoning conversion and event ordering.
- Stream/completion mismatch rejection for both reasoning and answer text.
- Persistence, history projection, Vox serialization, and CLI display.
- Malformed textual reasoning and unsupported opaque reasoning rejection.

Exit criteria:

- A compatible Ollama model can stream reasoning followed by an answer without
  `provider returned unsupported content`.
- Reasoning never appears in answer stdout.
- A completed assistant message durably retains its reasoning separately from
  answer text.

## Milestone 7 — Add explicit write approval to Vox and CLI

Commit: `feat: add tool approval workflow`

Approval changes the public application protocol and therefore cannot be a
hidden `ToolPolicy` return value.

Implementation:

- Extend decisions with `RequireApproval(reason)` and add a stable approval
  request ID derived from session ID plus tool call ID.
- Persist pending approval state before exposing it to a caller. Store the
  call, tool name, validated arguments, effect, reason, and creation time;
  never store credentials or executable callables.
- Extend the runtime/service stream with a discriminated approval event while
  keeping text, usage, and completion events.
- Add authenticated Vox endpoints to approve or deny one pending request.
- Add CLI rendering that prints the exact tool name and validated arguments
  and asks once. Non-interactive CLI use denies by default.
- Approval is single-use and bound to the exact persisted call ID and argument
  payload. Changed arguments require a new decision.
- A denial is persisted as an error tool result. An approval first atomically
  changes the request from `pending` to `executing`; only then may the tool
  run. Success atomically records `completed` plus the tool result. A crash
  while `executing` changes the effective state to `indeterminate` on the next
  load and can never be approved or executed automatically again.
- On restart, pending approvals remain pending. They never execute
  automatically.

Tests:

- Allow, deny, approve, and pending-across-restart paths.
- Authentication and cross-session approval rejection.
- Duplicate, stale, changed-payload, and already-consumed approvals.
- Non-interactive denial and interactive CLI rendering.
- No execution before the durable approval decision.
- Vox event framing and backwards-incompatible schema documentation.

Exit criteria:

- A write tool cannot execute without a matching persisted approval.
- Replaying the same approval cannot execute the tool twice.
- CLI and Vox documentation identify the protocol change.

## Milestone 7.5 — Add durable runtime tracing

Commit: `feat: add durable runtime trace events`

This milestone extends the existing event envelope and emitter. It does not
introduce a second log, tracing backend, or public stream protocol.

Contracts:

- Extend the existing `EventType` enum with these exact durable event types:
  - `run.started`, `run.paused`, `run.resumed`, `run.completed`, `run.failed`;
  - `model.request.started`, `model.request.completed`,
    `model.request.failed`;
  - `tool.call.requested`, `tool.call.prepared`,
    `tool.execution.started`, `tool.execution.completed`;
  - `tool.approval.requested`, `tool.approval.approved`,
    `tool.approval.denied`, `tool.approval.indeterminate`.
- Add strict write-time payload models beside the existing event contracts in
  `src/ethos/events/models.py`. Reuse `EventPayload`, `EventEnvelope`,
  `event_factory`, and `EnvelopeEventEmitter`; do not add a tracing module or
  another event abstraction. The envelope's `EventType` is the discriminator.
- Every runtime payload contains a UUID `run_id`, workspace name, canonical
  session ID, and one-based model round. Tool payloads also contain call ID and
  tool name. Approval payloads additionally contain approval ID and effect.
- A `model.request.completed` event contains finish reason, round usage, and
  optional provider response ID. A `tool.call.prepared` event contains exactly
  one outcome:
  `allow`, `deny`, `require_approval`, `invalid`, or `unknown`. A
  `tool.execution.completed` event contains only whether its stored result is
  an error.
- Failure traces contain a stable category such as `provider`, `protocol`,
  `persistence`, `limit`, `policy`, or `internal`; never copy exception text.
- Runtime payloads use schema name `runtime.trace` and schema version 1. Tag
  envelopes with prefixed workspace, session, and run IDs, plus call, tool,
  and approval IDs when present. Use the invocation's trusted CLI or Vox source
  as the existing event-envelope source and the event type as its detail.

Correlation and privacy:

- Generate one `run_id` when a user turn starts and carry it through every
  model round and tool call belonging to that turn.
- Persist the `run_id` in `ToolApproval` so approval, denial, restart, and
  indeterminate recovery continue the original trace rather than starting a
  new one. Never derive correlation from provider response IDs.
- Do not store prompts, answer text, reasoning, raw or validated arguments,
  tool-result content, credentials, headers, or exception messages in trace
  payloads. Those values remain in their existing canonical stores where
  applicable; traces identify them by session, call, and approval IDs.
- Runtime trace events are internal. `ChatEvent`, CLI output, and Vox SSE must
  never expose them.

Wiring:

- Give `AgentRuntime` the existing `EnvelopeEventEmitter` as a required
  constructor dependency. `Ethos._runtime()` supplies its application emitter.
  `run()` and `resolve_approval()` receive the trusted invocation source from
  the service, construct typed payloads and envelopes with `event_factory`, and
  await `EnvelopeEventEmitter.emit()` at each trace point.
- Do not schedule background event tasks. Event ordering must match runtime
  ordering, durable-first listener delivery remains unchanged, and event
  persistence failure must be observable.
- Refine tool preparation to return a tagged prepared or rejected outcome so
  tracing never infers `deny`, `invalid`, or `unknown` from human-readable tool
  result text.
- Emit traces in this exact order relative to work:
  1. `run.started` before the first model action;
  2. `model.request.started` immediately before provider streaming;
  3. `model.request.completed` after stream/completion validation and before
     response checkpointing, or `model.request.failed` before propagating a
     provider/protocol failure;
  4. `tool.call.requested` only after its assistant call is durable;
  5. `tool.call.prepared` after registry, schema, and policy evaluation;
  6. `tool.approval.requested` only after pending approval persistence,
     followed by `run.paused` before the public approval event;
  7. `run.resumed` after a pending approval and exact payload are validated;
  8. `tool.approval.approved` after `pending -> executing`, then
     `tool.execution.started` immediately before execution;
  9. `tool.approval.denied` only after denial and its error result are durable;
  10. `tool.execution.completed` only after its result and corresponding
      history or approval completion are durable;
  11. `tool.approval.indeterminate` only after crash recovery is durable;
  12. `run.completed` after the final assistant history checkpoint and before
      public `done=True`, or `run.failed` before propagating a handled failure.
- A cancellation or process death may leave a started trace without a terminal
  trace. Never fabricate completion or failure during async-generator cleanup.
- Event persistence failure before tool execution must prevent execution. If a
  post-side-effect trace write fails, retain the already-durable tool result
  and consumed approval state; tracing must never cause an automatic replay.

Tests:

- Exact ordered trace sequences for text-only, read-tool, approved write-tool,
  denied write-tool, rejected/invalid/unknown tool, and multi-round runs.
- One `run_id` across approval pause, service restart, resolution, and final
  completion; distinct turns receive distinct IDs.
- Approved execution has a durable `approval.approved` and `tool.started`
  trace before the tool observes execution.
- Indeterminate recovery emits once and retains the original run ID.
- Provider failure, protocol failure, model/tool limits, policy failure, tool
  error result, persistence failure, and event-emission failure.
- Event-database round trip, schema version, request source, and durable
  database ordering.
- Payload inspection proving forbidden content and credentials are absent.
- Event-emission failure releases session locks and cannot execute or replay a
  write tool.
- CLI and Vox streams contain no runtime trace events.

Documentation:

- Document the runtime trace taxonomy, correlation rules, privacy exclusions,
  persistence ordering, incomplete-span semantics, and the fact that
  `session.chat` remains the coarse application-operation event.
- Correct any concurrency documentation that still describes session locking
  as process-local.

Exit criteria:

- Stored events reconstruct one ordered model/tool/approval run across a
  process restart using `run_id` without inspecting prompt or result content.
- Every write execution is preceded by durable approval and execution-started
  traces, and trace failure cannot produce an unapproved or duplicate effect.
- Runtime tracing adds no new module, database, event abstraction, background
  worker, public SSE variant, or dependency.

## Milestone 8 — Separate conversation context from persistence

Commit: `refactor: add model context builder`

Implementation in `src/ethos/context.py`:

- Add a `ContextBuilder` that takes stored messages, run instructions, and
  available tool definitions and returns one `ModelRequest`.
- The first implementation includes all stored messages unchanged, prepends
  supplied system instructions in supplied order, and attaches tool
  definitions. It does not compact, summarise, or inject memory.
- Route every runtime request through it and remove direct `ModelRequest`
  construction from the loop.

Tests:

- Empty and non-empty histories, deterministic instruction order, tool order,
  and no mutation of stored history.
- Tool-call/result relationships survive context construction.

Exit criteria:

- Changing request context no longer requires editing the agent loop.
- Persistence still stores canonical conversation messages, not constructed
  system instructions.

## Milestone 9 — Add the minimal capability composition

Commit: `feat: add runtime capabilities`

Implementation in `src/ethos/capabilities/`:

```python
class Capability(Protocol):
    async def instructions(self, context: RunContext) -> tuple[str, ...]: ...
    async def tools(self, context: RunContext) -> tuple[Tool, ...]: ...
```

- `RunContext` contains only workspace name, workspace path, session ID, and
  trusted request metadata required by the first capability.
- Resolve capabilities once at the start of a turn, in registration order.
- Reject duplicate contributed tool names before the first model request.
- Feed instructions to `ContextBuilder` and tools to a per-turn registry.
- Implement exactly one first capability: a read-only filesystem capability
  with bounded `list_files` and `read_file` tools. `list_files` lists one
  directory without recursion; `read_file` reads one UTF-8 text file. Both
  reject paths outside the active workspace.

Tests:

- Deterministic composition, duplicate names, instruction order, and
  per-session isolation.
- Capability failure occurs before model or tool execution.
- The first real capability cannot escape its workspace trust boundary.

Exit criteria:

- The runtime contains no branch naming a concrete capability.
- The capability protocol has only the two methods above.

## Milestone 10 — Add Agent Skills through progressive disclosure

Commit: `feat: add skills capability`

Implementation:

- Discover project and user skills from native Ethos and cross-client
  `.agents/skills/` locations, with deterministic project-over-user
  precedence.
- Parse metadata leniently without executing embedded content. Skip unusable
  skills without blocking valid neighbours and emit diagnostics for collisions
  or cosmetic specification violations.
- Disclose only skill names and descriptions through `ContextBuilder`.
- Add a read-only, enum-constrained activation tool that loads one complete
  skill body on model selection, plus bounded on-demand access to bundled
  resources.
- Omit both the catalogue and skill tools when no valid skills are available.

Tests:

- Discovery scopes, precedence, lenient validation, malformed metadata, and
  collisions.
- Catalogue-only initial disclosure, model-driven activation, lazy bundled
  resource reads, and path escape rejection.

Exit criteria:

- Skill bodies are absent from initial context and arrive only after
  activation.
- No skill code imports the provider adapter or runtime loop.

## Final acceptance scenario

The plan is complete when one test and one manual smoke run demonstrate this
exact path without Pydantic AI:

```text
fresh Ethos session
    -> user text through CLI or Vox
    -> streamed LiteLLM text and tool call
    -> durable assistant-call checkpoint
    -> registry lookup and argument validation
    -> policy decision
    -> durable tool result
    -> second streamed model response
    -> durable final assistant message
    -> one final done event
    -> session reload after process restart
```

The automated suite must also prove that provider failure, cancellation,
malformed output, permission denial, persistence failure, limit exhaustion,
and an indeterminate tool call fail without silently losing history or
duplicating a write side effect.

## Milestone 11 — Simplify durable event storage

Commit: `refactor: simplify event storage`

Implementation:

- Add a database-generated, monotonic `sequence` primary key. Retain the event
  UUID as a unique identity and retain `created_at` as observation time, not
  ordering authority.
- Remove `EventSource.detail` and the `source_detail` column because every
  producer duplicates information already present in the event type or typed
  payload.
- Remove envelope tags and their JSON column. Typed payloads are the sole
  source of workspace, session, run, call, tool, approval, and skill
  correlation values.
- Treat the storage schema change as an alpha-breaking reset. Do not add a
  migration or compatibility branch; reinitialise the Ethos home before the
  next manual run.

Tests:

- Database-generated ordering remains deterministic when timestamps are
  equal.
- Stored rows retain UUID, timestamp, event type, source name, schema version,
  and typed correlation fields.
- Event producers and listener delivery require no detail or tag metadata.

Exit criteria:

- Every diagnostic query can reconstruct exact storage order with
  `ORDER BY sequence`.
- No event correlation value is duplicated outside its typed payload.

## Milestone 12 — Add answer-now fallback

Commit: `feat: add answer now fallback`

Implementation:

- Add a positive `runtime.answer_now_after_seconds` setting, defaulting to 60
  seconds.
- Start the deadline at the first non-empty reasoning delta and cancel it when
  the first answer-text delta arrives.
- On expiry, cancel the incomplete request and retry exactly once with
  reasoning disabled and a run-only answer-now system instruction. Preserve
  tool availability and count the retry as another model round.
- Do not persist abandoned reasoning or invent usage for an incomplete
  provider response.
- Fail with an agent-limit error when the retry still produces neither answer
  text nor a tool call.

Tests:

- Prolonged reasoning streams once, then yields the fallback answer without
  persisting the abandoned reasoning.
- The production fallback requests `ReasoningEffort.NONE` even when normal
  reasoning is enabled.
- A second prolonged or empty response fails without a third attempt.

Exit criteria:

- A reasoning loop cannot consume the run indefinitely.
- Provider connection time and silent tool selection do not consume the
  reasoning deadline.
- Every timed-out request is traced as a model-request limit failure.
