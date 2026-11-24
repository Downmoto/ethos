# ADR-0003: Explicit capability composition

- Status: Accepted
- Supersedes: None

## Context

Agents and automations need controlled access to models, tools, data, and actions.

## Decision

Compose behaviour from explicit capabilities with declared configuration and authorisation boundaries. Do not grant ambient access by default.

## Consequences

Capability membership and permission evaluation must be inspectable and enforceable at operation boundaries.

## Deferred details

Capability schemas, registries, and policy language remain undecided.

