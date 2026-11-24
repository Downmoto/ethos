# ADR-0002: Headless, single-owner architecture

- Status: Accepted
- Supersedes: None

## Context

Ethos must support multiple external experiences without coupling its core to a particular interface.

## Decision

Build Ethos as a headless service that owns authoritative state and exposes interfaces to external consumers. The administrative CLI is separate from conversational and authoring experiences.

## Consequences

External applications share a consistent system of record. Service interfaces and compatibility become first-class architectural concerns.

## Deferred details

Transport protocols and API schemas remain undecided.

