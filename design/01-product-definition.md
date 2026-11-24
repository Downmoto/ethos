# Ethos — Stage 1: Product baseline

Status: Accepted product direction, 2026-11-21. This is the durable product baseline. The [milestone map](../plan.md) and its linked plans contain the current implementation contracts; the planning sequence and continuation checkpoint below record the state at the time of acceptance.

## Product and release scope

Ethos is a free, open-source, self-hosted, headless agent and automation platform written in Rust. A persistent instance provides shared agents, workspaces, projects, sessions and automations to external applications. An instance has a single owner. Linux is the only supported host OS for 1.0. Keep platform-specific boundaries clean. The CLI is exclusively for configuration, administration and daemon lifecycle. User interaction occurs through external applications.

Dependency policy: minimal abstraction, maximum ownership. Own platform semantics (agent loop, provider abstraction, workflows, capabilities, permissions, context, sessions and events); use established infrastructure for async execution, HTTP/TLS, cryptography, serialisation and database engines. Specific dependencies were not selected at this stage.

Ethos 1.0 must be complete, reliable and usable by its owner, not a throwaway MVP. Vox is developed alongside Ethos at integration milestones, with its own roadmap. Ethos requirements and architectural decisions must remain client-independent; Vox adapts to the public Ethos interface. Personal launch readiness includes a working Vox, but Ethos completion is independently verifiable.

## Reference workloads

- Scheduled research: cron-triggered web research, delegation, compilation and persistence of reports.
- Email triage: retrieve emails, rank by importance, identify replies needed and create drafts. Sending is a distinct operation governed by authorisation.
- Coding: long-running agents, project files, skills, sandboxed shell execution and subagents.

These workloads are end-to-end acceptance scenarios. Exact integrations, providers and credentials remain to be specified.

## Accepted decisions

| ID | Decision | Consequence |
|---|---|---|
| D001 | Rust, Linux only for 1.0 | No second host sandbox implementation required for release. |
| D002 | Headless, persistent, single-owner platform | External clients use the public application interface. |
| D003 | Administrative CLI only | No conversational CLI in the release scope. |
| D004 | Minimal abstraction, maximum ownership | Own business semantics; reuse commodity infrastructure. |
| D005 | Minimal agents | Agent definition consists of model, options and explicitly configured capabilities; zero-capability agents offer ordinary model conversation and session history. |
| D006 | Capability composition | Capabilities contribute instructions, tools and optional configuration. Agent access to optional platform functionality requires an enabled capability. |
| D007 | Native plugins | Dynamically load independently compiled native shared libraries using an Ethos-owned, versioned C-compatible ABI and safe Rust SDK. Installed native code is trusted by the owner. |
| D008 | Middleware extensibility | Third parties can extend runtime interception and transformation behaviour, including prompt, tool and output processing. |
| D009 | Automation extensibility | Plugins can provide workflow actions and triggers in addition to capabilities and middleware. |
| D010 | Separate execution engines | Agent runtime and graph automation engine are distinct but interoperable. Workflows may run without a model. |
| D011 | Workspace boundary | Workspaces own their agents, automations, memory, files, tools and permissions. No implicit cross-workspace access. |
| D012 | Projects | Nested in workspaces to organise and further isolate sessions, automations and files. |
| D013 | Persistent and dynamic agents | Reusable definitions coexist with disposable subagents and inline agents for workflows. |
| D014 | Hybrid persistence | Human-editable definitions and user files reside in workspace directories; databases manage execution history and transactional runtime state. |
| D015 | Durable execution | Agent and workflow executions support recovery policies, durable checkpoints, suspension and resumption, including approval waits across restarts. |
| D016 | Security ring | Permissions and human approvals are enforced programmatically, and relevant restrictions are communicated through system instructions. |
| D017 | MCP support | MCP is a supported integration mechanism distinct from native extensions. |
| D018 | Vox independence | Separate roadmap; Vox catches up at Ethos milestones without driving Ethos architecture. |
| D019 | Graph workflow authoring | Users and authorised agents can author workflows. |

## Agent and extension semantics

Agent definitions and executions are distinct; a session is persistent conversational state. Dynamic agent definitions may be disposable; retaining execution history is the proposed audit approach. Runtime mechanisms (model communication, session management, tool dispatch, context assembly and policy enforcement) are infrastructure, not optional grants to agents. Delegation, memory, skills, filesystem, shell, web, automation management, and project management are agent-facing capabilities requiring explicit enablement. A capability is not a plugin: a plugin is a distribution and registration mechanism and can export multiple extension types.

First-party and third-party extensions should use the same public extension contracts. Shared underlying operations may be exposed both as model tools and deterministic workflow actions; the exact operation abstraction is pending. Middleware must not override mandatory policy checks; transformed requests require validation and authorisation. Ordering, failure policies, data access and interception points need specification.

Native plugins execute with daemon privileges. Workspace isolation cannot be guaranteed against malicious in-process native code. Treat plugin installation as trusting executable software; runtime capability permissions constrain legitimate invocation, not arbitrary native code. MCP and separately executed processes are distinct trust boundaries; process separation alone does not provide sandboxing.

## Automation 1.0 requirements

- Graph-based workflows, user and agent authoring.
- Cron scheduling, webhooks and event-based triggers.
- Conditional branching, loops, parallel execution and reusable subworkflows.
- Deterministic actions and both persistent and dynamic agent invocation.
- Human approval suspension and durable recovery.

Reliability requirements to specify: retries, timeouts, concurrency limits, cancellation, execution history, missed schedules, idempotency, checkpoint boundaries, uncertain external side effects and restart recovery. Do not assume exactly-once execution of arbitrary external operations.

## Proposed implementation directions (not locked)

- Per-workspace database rather than one global runtime database; consistent live backup and portable workspace handling need design.
- Instance-level plugin installation with workspace-level enablement and configuration; inheritance rules must prevent unauthorised expansion of privileges.
- Native plugin discovery through local manifests and precompiled libraries; startup loading, restart for updates and no hot unloading for 1.0. ABI lifecycle, compatibility, ownership, async bridging and distribution are to be specified. Early proof of concept should test separately compiled capability, middleware and async operation.
- Shared dispatch/policy enforcement for underlying operations, exposed via tools or workflow nodes where appropriate.
- Unified execution machinery for saved and inline agent definitions; persist dynamic execution history and constrain delegation by parent authority and budgets.
- Precise filesystem layout, configuration format, storage engine, API transport, crate structure and built-in extension catalogue are not decided.

## Expected Ethos release deliverables

Daemon, administrative CLI, agent runtime, automation engine, workspace/project management, security/approval enforcement, native plugin host and Rust SDK, MCP integration, public application interface and integration documentation, essential first-party extensions, tests and reference workflows. Exact feature details and release gates follow subsystem design.

## Planning sequence

1. Product definition and scope — complete.
2. Features and subsystems — next.
3. Architecture and boundaries.
4. Rust stack and monorepo.
5. Specification and architecture review.
6. Production roadmap: root `PLAN.md` and hyper-detailed individual phase files under `plan/`, including prerequisites, interfaces, tasks, tests, acceptance criteria and verification gates.

### Stage 2 discussion order (not implementation order)

A. Instance lifecycle and configuration
B. Workspaces and projects
C. Model and provider system
D. Operations and tools
E. Capabilities
F. Native plugin system
G. Security and permissions
H. Context management
I. Sessions and persistence
J. Agent runtime
K. Delegation and subagents
L. Middleware
M. Memory and skills
N. Event system
O. Automation engine
P. MCP integration
Q. Application interface
R. First-party extensions
S. Observability and administration

For each subsystem establish responsibilities, 1.0 requirements, entities, invariants, configuration, security, errors/failure behaviour, acceptance criteria and open decisions. Revisit earlier decisions explicitly if necessary; do not silently replace them.

## Continuation checkpoint

Questions 18 and 19 were both answered yes: retain Rust with Ethos-owned versioned C ABI, safe Rust SDK and trusted native plugins; Linux exclusively for 1.0. Stage 1 is complete. Resume at Stage 2 Part A, instance lifecycle and configuration. No implementation roadmap or final technology selections have been made yet.
