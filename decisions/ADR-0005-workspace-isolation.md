# ADR-0005: Workspace isolation

- Status: Accepted
- Supersedes: None

## Context

Workspaces contain resources and data that require clear security boundaries.

## Decision

Treat workspaces as explicit isolation boundaries. Access across a workspace boundary requires an authorised mechanism and cannot arise through ambient process access.

## Consequences

Resource ownership, references, storage paths, and authorisation must preserve the workspace boundary.

## Deferred details

The workspace and project hierarchy and detailed filesystem semantics belong to Stage 2 Part B.

