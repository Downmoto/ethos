# ADR-0008: Hybrid persistence and durable execution

- Status: Accepted
- Supersedes: None

## Context

Ethos combines human-maintained definitions with transactional runtime state and executions that must survive interruption.

## Decision

Use hybrid persistence: keep suitable declarative resources in inspectable files and authoritative transactional runtime state in a database. Durable executions must support recovery after process or host failure.

## Consequences

Each data type requires a clear authority and consistency model. File and database state cannot be treated as interchangeable.

## Deferred details

Database engine, topology, migrations, and exact file-to-database boundaries remain undecided.

