# ADR-0011: Startup, shutdown, and recovery

- Status: Accepted
- Decision source: Questions 26–27
- Supersedes: None

## Context

Local resource failures should not unnecessarily disable unrelated work, but security and transactional correctness require fail-closed behaviour. Durable work must survive interrupted shutdowns.

## Decision

Classify startup and runtime failures as critical or recoverable. Refuse startup, or suspend affected work, when safe authorisation or transactional correctness cannot be guaranteed. Isolate recoverable failures and report degraded health when the failure boundary is reliable.

Perform graceful shutdown through an ordered lifecycle: stop admitting new executions, ask active work to finish or reach a durable suspension point, flush authoritative state, stop services in dependency order, and release instance ownership. Enforce a configurable deadline without falsely recording incomplete operations as complete. Recover unfinished durable work at the next startup.

Provide maintenance mode so the instance can remain available for administration while preventing new executions.

## Consequences

Services require health states, dependency-aware lifecycle hooks, and recovery contracts. Abrupt termination remains a normal recovery case rather than an exceptional assumption.

## Deferred details

Health taxonomy, shutdown timeout, signal handling, recovery ordering, and maintenance-mode interface remain undecided.

