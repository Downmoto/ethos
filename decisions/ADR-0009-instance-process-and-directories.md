# ADR-0009: Instance process and directories

- Status: Accepted
- Decision source: Questions 20–22
- Supersedes: None

## Context

Ethos needs a predictable process lifecycle, relocatable instance state, and safe support for development, testing, and production instances on one host.

## Considered alternatives

- A self-managed background daemon with built-in process supervision.
- A system-managed foreground process.
- A hybrid model with a foreground process plus administrative conveniences and optional service integration.
- XDG-split storage versus a self-contained instance directory.

## Decision

Ethos runs as a foreground daemon and may be supervised by systemd or another external supervisor. Systemd is optional so terminal and container operation remain supported.

Each instance uses a configurable, self-contained instance directory, defaulting to `~/.ethos`. Multiple independent instances may run on one machine, but only one process may own a given instance directory at a time.

## Consequences

Ethos does not implement its own general process supervisor. The instance directory is the unit of relocation, backup, and ownership. Startup must acquire exclusive ownership before mutable services become active.

## Deferred details

Directory layout, locking primitive, stale-lock recovery, and service installation commands remain undecided.

