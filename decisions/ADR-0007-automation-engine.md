# ADR-0007: Separate graph automation engine

- Status: Accepted
- Supersedes: None

## Context

Durable automations need explicit orchestration semantics beyond conversational agent behaviour.

## Decision

Provide a separate graph-based automation engine. Agent execution may be a graph operation, but the automation engine retains its own lifecycle and durable state.

## Consequences

Workflow validation, scheduling, persistence, and recovery are explicit subsystem responsibilities.

## Deferred details

Graph representation, node catalogue, and execution algorithms remain undecided.

