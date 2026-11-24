# ADR-0010: Configuration and administration

- Status: Accepted
- Decision source: Questions 23–25
- Supersedes: None

## Context

Configuration errors may affect security and availability, while some settings need safe runtime changes. Administrative access has several trust levels.

## Decision

Use TOML for human-editable instance configuration. Validate configuration strictly, including rejection of unknown fields. Resolve settings through documented layers with deterministic precedence.

Support selective live reload. Validate a candidate configuration completely and apply it atomically. Settings that cannot be changed safely require restart. Running executions retain identifiable definition snapshots, while current security policy is enforced again at later authorisation boundaries.

Expose basic administration to authenticated, authorised external consumers. Restrict high-trust host operations, including native plugin installation and instance-level infrastructure changes, to direct host administration.

## Consequences

Every setting needs a reload classification. Administrative interfaces must distinguish ordinary administration from host-trust operations. Validation failures leave the active configuration unchanged.

## Deferred details

Exact precedence layers, schema, reload matrix, API endpoints, and authentication mechanisms remain undecided.

