# Stage 2 Part A: Instance lifecycle and configuration

- Status: Accepted
- Discovery: Complete
- Decisions: Accepted
- Requirements: `LIF-001` through `LIF-021`
- Last updated: 2026-11-22

## 1. Purpose

This subsystem defines how an Ethos instance is initialised, configured, started, observed, maintained, recovered, and stopped. It coordinates service lifecycles without owning their domain logic.

Ethos must be able to start and maintain its internal services without any agent or automation being configured.

## 2. Scope

### 2.1 Responsibilities

- initialise an instance directory
- identify an instance and acquire exclusive ownership
- load, layer, and validate instance configuration
- start and stop internal services in dependency order
- expose readiness, health, status, and diagnostic information
- coordinate selective configuration reload
- provide administrative CLI operations
- enforce host-only boundaries for high-trust administration
- enter and leave maintenance mode
- coordinate graceful shutdown and startup recovery

### 2.2 Excluded responsibilities

This subsystem does not own agent execution, automation execution, permission decisions, workspace persistence, plugin business logic, model-provider behaviour, or domain-specific recovery. It invokes lifecycle contracts supplied by the services that own those concerns.

## 3. Accepted decisions

The following decisions were explicitly accepted during discovery:

1. Ethos runs as a foreground daemon and may be supervised externally. Systemd support is optional.
2. Each instance has a configurable, self-contained directory, defaulting to `~/.ethos`.
3. A host may run several independent instances, but only one process may own an instance directory.
4. Human-editable instance configuration uses TOML, deterministic precedence, and strict validation.
5. Safe settings may reload live through validated, atomic replacement. Other settings require restart.
6. Basic administration may be exposed to authorised external consumers. High-trust actions remain host-only.
7. Recoverable failures produce isolated degraded operation. Security or transactional-integrity failures fail closed.
8. Shutdown is graceful and deadline-bound, preserves durable execution truth, and supports maintenance mode.

ADRs 0009–0011 contain the architectural rationale.

## 4. Conceptual lifecycle

An instance progresses through these conceptual states:

`uninitialised → starting → ready/degraded → maintenance → stopping → stopped`

A critical startup failure moves the attempted start to `failed` without reporting readiness. An unexpected process or host failure may occur from any running state; the next start performs recovery before admitting new work.

These are architectural states, not a required enum or public protocol.

## 5. Requirements

### Initialization and ownership

**LIF-001**: Ethos shall provide an idempotent initialisation operation that creates a valid instance directory without starting the daemon.

**LIF-002**: The instance directory shall be configurable and shall default to `~/.ethos` when no explicit location is supplied.

**LIF-003**: All instance-owned configuration, secrets, installed plugins, workspace registry data, durable state, logs, and runtime metadata shall have defined locations within, or defined references from, the instance directory.

**LIF-004**: Before activating mutable services, the daemon shall acquire exclusive ownership of the instance directory and shall refuse to start when another live process owns it.

**LIF-005**: Separately configured instance directories and listening endpoints shall permit independent instances on one host without shared mutable state by default.

### Configuration

**LIF-006**: Human-editable instance configuration shall use TOML and a documented schema.

**LIF-007**: Configuration resolution shall use documented layers with deterministic precedence, and diagnostics shall identify the effective source of a value where practical.

**LIF-008**: Validation shall reject unknown fields, invalid types, invalid values, and inconsistent combinations before an affected configuration becomes active.

**LIF-009**: Instance, workspace, agent, automation, and capability configuration shall remain distinct scoped domains rather than one undifferentiated configuration object.

**LIF-010**: Every configurable setting shall declare whether it supports live reload, requires restart, or is immutable after initialisation.

**LIF-011**: A live reload shall validate the complete candidate change and apply it atomically. Failure shall leave the previous active configuration unchanged and produce actionable diagnostics.

**LIF-012**: Running executions shall retain identifiable snapshots of definitions that determine their behaviour, while current security policy shall be evaluated again at subsequent authorisation boundaries.

### Service lifecycle and health

**LIF-013**: A lifecycle coordinator shall start and stop internal services in dependency order through explicit lifecycle contracts without depending on their domain internals.

**LIF-014**: The instance shall expose distinct liveness, readiness, and degraded-health information with machine-readable causes suitable for administration and supervision.

**LIF-015**: A recoverable resource or integration failure shall be isolated to the narrowest reliable boundary and shall not prevent unrelated services from operating.

**LIF-016**: The instance shall fail closed when a failure prevents reliable authorisation, isolation, durable-state integrity, or transactional correctness.

### Administration

**LIF-017**: A single `ethos` executable shall support foreground service operation and host administration. The exact command surface is deferred.

**LIF-018**: Authorised external consumers may perform basic administrative operations, but installation or activation of native plugins and instance-level infrastructure changes shall require a host-trust path.

**LIF-019**: Maintenance mode shall keep administrative and diagnostic functions available while preventing admission of new executions. Transition semantics for already-running work shall be explicit.

### Shutdown and recovery

**LIF-020**: Graceful shutdown shall stop new admissions, allow active work to finish or reach a durable suspension point, persist authoritative state, stop services in dependency order, release ownership, and obey a configurable deadline. Work that misses the deadline shall not be recorded as completed.

**LIF-021**: Startup shall detect and recover or isolate incomplete work left by crashes, power loss, forced termination, or expired shutdown deadlines before the instance reports readiness for affected operations.

## 6. Invariants

1. At most one live daemon owns an instance directory.
2. The instance never reports ready before critical configuration, ownership, security, and persistence checks succeed.
3. A rejected reload cannot partially alter active configuration.
4. Degraded operation cannot bypass authorisation or compromise authoritative state.
5. Shutdown and recovery never manufacture successful completion records.
6. Host-only operations cannot be reached solely by possessing ordinary external administrative access.
7. Editing a definition does not silently mutate the behavioural snapshot of an execution already in progress.

## 7. Failure policy

| Failure | Required class | Expected behaviour |
| --- | --- | --- |
| Invalid instance configuration | Critical | Refuse startup or reject reload |
| Instance directory owned by another live process | Critical | Refuse startup |
| Incompatible authoritative database | Critical | Refuse startup |
| Mandatory security service unavailable | Critical | Refuse readiness or suspend affected work |
| Invalid or incompatible optional plugin | Recoverable when isolated | Disable plugin and report degraded health |
| Resource required by one agent unavailable | Recoverable when isolated | Mark affected agent unavailable |
| Corrupted workspace | Recoverable only with a reliable boundary | Isolate workspace and report degraded health |
| Model provider unavailable | Recoverable | Report provider failure without stopping unrelated work |
| Invalid automation definition | Recoverable | Disable the affected definition and report diagnostics |

The table establishes policy boundaries and does not prescribe implementation mechanisms.

## 8. Security boundaries

- Instance ownership gates all mutable service activation.
- Strict validation treats misspelled or unknown configuration as errors.
- Native plugin installation grants executable code access and remains host-only.
- Infrastructure and secret-management operations require a host-trust path.
- External administration requires authentication and authorisation, to be designed with the external API subsystem.
- Permission revocation applies at subsequent authorisation boundaries even when an execution retains an older behavioural definition snapshot.

## 9. Acceptance criteria

Part A implementation is complete when tests demonstrate that:

1. a new instance can be initialised and started from an explicit self-contained directory;
2. two independent instances can run concurrently, while a second owner of one directory is rejected;
3. invalid and unknown configuration is rejected with useful diagnostics;
4. a supported reload is atomic and a failed reload preserves the previous configuration;
5. restart-required changes are identified without partial application;
6. isolated failures produce degraded health while unrelated functions remain available;
7. security-critical and integrity-critical failures prevent unsafe readiness;
8. maintenance mode prevents new execution admission while preserving administration;
9. graceful shutdown preserves durable execution truth before and after its deadline; and
10. restart after abrupt termination invokes recovery before affected work resumes.

## 10. Deferred design details

These items remain deliberately unresolved and are not accepted requirements:

- exact directory layout
- exact TOML schema and precedence layers
- locking implementation and stale-owner detection
- service lifecycle trait or interface design
- public administrative endpoints and transport
- command names beyond the conceptual single-executable model
- default shutdown deadline
- detailed health status vocabulary
- recovery orchestration algorithm
- systemd unit generation or installation workflow

## 11. Review outcome

The owner approved this specification on 2026-11-22. ADRs 0009–0011 record the accepted architectural decisions. Section 10 remains deferred for later subsystem and architecture work.

