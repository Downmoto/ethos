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
redirecting stdout or using `ethos ask --to` captures only the answer.

Reasoning is model-emitted diagnostic text, not a guaranteed faithful account
of how the model produced its answer. Ethos does not replay it as conversation
context. Provider-native thinking blocks, signatures, encrypted reasoning, and
other opaque formats remain unsupported until a provider requires them for
continuation.
