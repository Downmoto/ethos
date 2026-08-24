# Ollama thinking streams

Ethos supports textual model reasoning through provider-neutral contracts.

Configure the requested effort in `~/.ethos/config.yaml`:

```yaml
provider:
  reasoning_effort: high
```

Accepted values are `none`, `low`, `medium`, and `high`; the default is
`none`. LiteLLM translates this setting for the selected model. Models that do
not support reasoning may reject a non-`none` effort.

Provider `reasoning_content` becomes `ReasoningPart` in a completed response
and `ReasoningDelta` while streaming. The runtime persists it separately from
answer text and Vox exposes it as `ChatChunk(text_kind="reasoning")`.

The CLI writes reasoning to stderr and answers to stdout. Consequently,
redirecting stdout captures only the answer.

Reasoning is model-emitted diagnostic text, not a guaranteed faithful account
of how the model produced its answer. Ethos does not replay it as conversation
context. Provider-native thinking blocks, signatures, encrypted reasoning, and
other opaque formats remain unsupported until a provider requires them for
continuation.

## Answer-now fallback

Configure the reasoning deadline separately from the requested effort:

```yaml
runtime:
  answer_now_after_seconds: 60
```

The deadline begins with the first non-empty reasoning delta, not while the
provider is connecting or silently selecting a tool. The first answer-text
delta cancels it. If reasoning reaches the deadline, Ethos cancels that model
request and retries the turn exactly once with reasoning disabled and a
run-only instruction to answer promptly. Tools remain available during the
retry, and the retry consumes another model round.

Reasoning already streamed to the caller is not persisted because the
abandoned model response never completed. Its token usage is also unavailable
unless the provider completed a usage report. The timed-out request is traced
as a model-request limit failure. If the retry also fails to produce answer
text or a tool call, the run ends with a clear agent-limit error; the fallback
cannot guarantee that a model will comply.
