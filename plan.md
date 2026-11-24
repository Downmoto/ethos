# Ethos 0.1.0–1.0 milestone map

Ethos is a free, open-source, self-hosted agent and automation platform.

Versions after 0.1.0 may change as detailed plans are written. Each version closes only when its exit gate passes; there are no target dates.

| Version | Milestone | Exit gate |
|---|---|---|
| [0.1.0](plan/0.1.0-instance-foundation.md) | Instance foundation | A Linux daemon initialises, owns, configures, monitors, shuts down, and recovers its self-contained instance state. No agent is required. |
| [0.2.0](plan/0.2.0-core-domain-and-interface.md) | Core domain and interface | Workspaces live under the instance home and contain projects and agents. Every session belongs to a project, including a hidden default. Definitions and session state persist; a host-issued credential protects the network API. |
| [0.3.0](plan/0.3.0-model-are-and-context.md) | Model, ARE, and context | OpenAI and local Ollama work through a configurable Chat Completions client with streaming and tool-call parsing. The ARE runs zero-capability conversations. The context manager reduces redundant material before summarising older conversation. A reviewed model catalogue supplies limits and prices; a ledger tracks tokens and estimated cost. Vox integrates through its backend. |
| [0.4.0](plan/0.4.0-coding-agent.md) | Coding agent | An OpenAI Responses adapter supports tool-heavy reasoning, while Chat Completions remains available for Ollama and compatible endpoints. Filesystem, sandboxed shell, skills, and first-party patch editing work through explicit capabilities and emit compact tool receipts. Tool calls pass one policy path for allow, approval, or deny; approvals survive restart. A project-scoped coding session can edit, test, and resume. |
| 0.5.0 | Memory | Explicit, extracted, and project knowledge can be stored at project or workspace scope. Relevant memory enters context automatically and can be retrieved through model tools; owners can inspect and correct it. |
| 0.6.0 | Native extension proof | A versioned C ABI and safe Rust SDK load an independently compiled capability. A fork of Vercel Labs' `agent-browser` supplies reading and navigation after a feasibility check. A PII-redaction middleware example runs before a user prompt enters context, preserving original and transformed prompt records. |
| 0.7.0 | Durable delegation | Agents delegate bounded work to subagents without gaining authority. Dynamic definitions, long-running runs, checkpoints, cancellation, and recovery work across restarts. |
| 0.8.0 | AAE core | The separate Agent Automation Engine runs user- and agent-authored graphs with deterministic actions, agent calls, branches, loops, parallel paths, subworkflows, approvals, and durable checkpoints. Cron and event triggers work. |
| 0.9.0 | Scheduled research | A scheduled graph uses web access and delegation to gather sources, compile a report, and save it in a project. Recovery and approval waits work without constant supervision. |
| 0.10.0 | MCP integration | Configured MCP integrations expose authorised functionality through Ethos policy and workspace/project boundaries. |
| 0.11.0 | Email triage | An MCP-backed workflow retrieves email, ranks it, identifies replies, and creates drafts in a chosen service. Sending has a separate authorisation path. |
| 0.12.0 | Extension completion | The SDK supports third-party AAE actions and triggers plus prompt, tool, and output middleware. Webhook triggers work. Transformed calls still pass validation and authorisation. |
| 0.13.0 | Release hardening | Fault injection, migration, backup and restore, operational diagnostics, security testing, and a sustained unattended pilot pass on Linux. |
| 1.0 | Release gate | All accepted requirements and the coding, research, and email reference workloads pass through the public interface. Vox is usable for personal launch; Ethos remains client-independent. |

## Accepted product decisions

The owner accepted D001–D019 in the product baseline on 2026-11-21. These IDs remain stable as their detailed contracts are assigned to milestones. A milestone may refine a decision, but changing one requires an explicit owner decision.

| ID | Accepted commitment | Primary milestone |
|---|---|---|
| D001 | Implement in Rust and support Linux only for 1.0. | 0.1.0 |
| D002 | Run a persistent, headless, single-owner service for external clients. | 0.1.0–0.2.0 |
| D003 | Keep the CLI for configuration, administration, and daemon lifecycle; user conversation belongs in external applications. | 0.1.0–0.3.0 |
| D004 | Own agent, workflow, capability, permission, context, session, and event semantics; use established infrastructure for commodity concerns. | All milestones |
| D005 | Define an agent by its model, options, and explicitly configured capabilities; support ordinary conversation and history with no capabilities. | 0.2.0–0.3.0 |
| D006 | Capabilities contribute instructions, tools, and optional configuration; optional functionality requires explicit enablement. | 0.4.0 onward |
| D007 | Load independently compiled, trusted native libraries through an Ethos-owned, versioned C-compatible ABI and safe Rust SDK. Installation is a host-trust action. | 0.6.0 |
| D008 | Allow third-party prompt, tool, and output middleware without bypassing mandatory validation or authorisation. | 0.12.0 |
| D009 | Let plugins provide workflow actions and triggers as well as capabilities and middleware. | 0.12.0 |
| D010 | Keep the agent runtime and graph automation engine separate and interoperable; workflows can run without a model. | 0.8.0 |
| D011 | Make workspaces explicit boundaries for agents, automations, memory, files, tools, and permissions, with no implicit cross-workspace access. | 0.2.0 onward |
| D012 | Nest projects in workspaces to organise and further isolate sessions, automations, and files. | 0.2.0 onward |
| D013 | Support reusable agent definitions and disposable subagents or inline workflow agents. | 0.7.0–0.8.0 |
| D014 | Keep human-editable definitions and user files in workspace directories, with transactional runtime state and history in databases. | 0.2.0 onward |
| D015 | Make agent and workflow execution durable, with checkpoints, suspension, recovery, and approval waits across restarts. Do not assume arbitrary external effects execute exactly once. | 0.3.0–0.8.0 |
| D016 | Enforce permissions and human approvals in code, and communicate relevant restrictions through instructions. | 0.4.0 onward |
| D017 | Support MCP as an integration mechanism distinct from native extensions. | 0.10.0 |
| D018 | Keep Vox on a separate roadmap and Ethos client-independent; Vox catches up at integration milestones. | 0.3.0 onward |
| D019 | Let users and authorised agents author graph workflows. | 0.8.0 |

## Cross-cutting rules

- Rust and Linux only for 1.0. Own Ethos semantics; use established infrastructure for commodity concerns.
- Remote clients are a 1.0 requirement. Tailscale is one deployment path, not a required Ethos dependency; an authenticated API behind a standard HTTPS proxy is sufficient for 1.0. Direct public exposure and built-in TLS are outside the initial scope.
- One instance-wide API credential is sufficient for ordinary remote access in 1.0; high-trust host operations remain host-only. Agent and AAE runs continue after a client disconnects, with status and history available after reconnecting. Vox's backend holds the credential and relays requests to Ethos.
- Native plugins are trusted code. Agent, MCP, browser, and workflow operations still pass application policy; workspace and project boundaries remain explicit.
- A native plugin runs with daemon privileges, so workspace isolation does not protect against malicious in-process plugin code. MCP and separately executed processes have different trust boundaries; process separation alone is not a sandbox.
- Store human-editable definitions and user files in workspace directories, and transactional execution state in databases. 0.2.0 fixes the first workspace layout and per-workspace SQLite foundation; later storage needs are resolved in their milestones.
- A workspace directory must be portable with its projects, agents, memory, sessions, automations, and history. One instance owns it at a time; the instance must coordinate concurrent clients. Credentials remain instance-wide. Filesystem permissions and disk encryption cover data at rest for 1.0.
- Backups may pause new work until active writes finish or suspend. A concurrent turn on one session returns `session busy`. File mutations reject stale content rather than silently overwriting a newer edit.
- Context is rebuilt for every model request from current instructions and stored session data. Full raw history remains available. Deterministic removal of redundant reasoning, stale instructions, and aged tool output precedes model-written summaries. Summaries record the raw source range they cover. Keep active security and agent instructions, enabled capability instructions, the latest request, and active tool exchanges together; budget older history, memory, and project material around them.
- Every model call records provider-reported token usage when available, separate from preflight estimates. Estimated cost uses a price snapshot and identifies missing or estimated inputs; summarisation calls count too.
- Never report success for an incomplete or uncertain external effect. Retry only when repetition is safe or explicitly controlled.

## Working Rust stack

One Cargo monorepo produces the `ethos` executable. [0.1.0](plan/0.1.0-instance-foundation.md) starts with `ethos` and `ethos-core` crates. Separate `are`, `aae`, and `model_provider` crates join when implemented. A `context` crate is proposed. Plugin ABI and SDK crates join when the native extension contract is implemented.

Use Tokio for async execution, Axum for the network API, Reqwest with rustls for outbound model requests, Serde with JSON and TOML for data and configuration, and SQLite through rusqlite for portable workspace databases. The host CLI uses clap; tracing handles diagnostics. Use libloading for native plugins and evaluate the official Rust MCP SDK at the MCP milestone. Bubblewrap is the Linux sandbox dependency when shell capability is enabled. Pin versions and feature flags in each detailed milestone plan after compatibility checks.

## Decisions still needed for detailed plans

Later crate feature sets and database schema; stream replay; exact capability and operation contracts; shell grant limits; context selection and token-budget algorithms; memory promotion default; browser fork feasibility; MCP scope and reference email service; AAE retry and missed-schedule rules; 1.0 pilot length and performance targets.
