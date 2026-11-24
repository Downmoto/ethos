# ADR-0004: Versioned native plugin ABI

- Status: Accepted
- Supersedes: None

## Context

Ethos needs extensibility without requiring every native extension to be statically compiled into the core.

## Decision

Support native plugins through a versioned C-compatible ABI.

## Consequences

The ABI requires compatibility rules, validation, and careful unsafe-code boundaries. Loading a native plugin grants executable code access and is therefore a high-trust host operation.

## Deferred details

ABI surface, version negotiation, packaging, signing, and sandboxing remain undecided.

