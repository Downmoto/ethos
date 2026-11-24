# ADR-0001: Rust implementation language

- Status: Accepted
- Supersedes: None

## Context

Ethos requires a reliable systems implementation with strong safety and concurrency properties.

## Decision

Implement the Ethos core in Rust. Prefer a restrained dependency set and evaluate dependencies for maintenance, security, and architectural fit.

## Consequences

The implementation benefits from memory safety and explicit concurrency semantics. The team accepts Rust's learning and compile-time costs.

## Deferred details

Crate boundaries, async runtime, and individual libraries remain undecided.

