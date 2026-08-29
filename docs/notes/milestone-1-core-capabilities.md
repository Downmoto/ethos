# Milestone 1 — Core capabilities

This document proposes the work required to complete the first milestone on
the road to beta v0.1.0. It defines product scope and completion outcomes, not
the final implementation design.

## Goal

Give Ethos the core capabilities needed to work safely inside a workspace and
let users configure the models and tools available to each run.

## Delivery order

1. Capability management
2. Provider management
3. Full filesystem
4. Shell

## Capability management

### Outcome

Users can control which capabilities are available globally and in each
workspace, and can understand the effective configuration before starting a
run.

### Scope

- Enable or disable registered capabilities globally.
- Override global capability settings per workspace.
- Configure capability-specific limits and permissions.
- Show the effective capabilities and settings for a workspace.
- Validate changes before saving them.
- Apply configuration changes to subsequent runs without reinitialising Ethos.

### Complete when

- Capability configuration has one canonical model and persistence path.
- The service, CLI, and Vox protocol expose the same management behaviour.
- Invalid or unknown configuration fails without partially changing state.
- Runtime capability resolution honours the effective workspace settings.
- Configuration changes emit lifecycle events without recording secrets.

## Provider management

### Outcome

Users can configure, validate, and select a model provider without manually
editing Ethos configuration files.

### Scope

- Add and update supported provider configuration.
- Configure model name, reasoning effort, and provider-specific settings.
- Store or reference credentials without displaying them after entry.
- Validate provider configuration and report actionable failures.
- Select the default provider and model used by new runs.
- Show the active provider and model with credentials redacted.

### Complete when

- Onboarding and later provider changes use the same configuration path.
- Configuration can be changed without reinitialising Ethos.
- A provider can be checked before it becomes the default.
- Failed validation leaves the previous working configuration intact.
- Provider changes are reflected in subsequent runs and lifecycle events.

## Full filesystem

### Outcome

The agent can complete ordinary file-management and editing tasks within its
active workspace while preserving the existing workspace boundary.

### Scope

- Keep the existing bounded file and directory reads.
- Create and update UTF-8 text files.
- Create directories.
- Move and rename files and directories.
- Delete files and directories.
- Return clear, bounded results for successful and failed operations.

### Safety boundaries

- All paths remain relative to the active workspace.
- Resolved paths and symbolic links cannot escape the workspace.
- Mutating operations pass through the existing write-tool approval flow.
- File sizes, directory listings, and tool results remain bounded.
- File replacement avoids leaving partially written content.

### Complete when

- Each supported operation works through the shared capability and tool-policy
  path.
- Invalid paths, incompatible path types, and boundary escapes fail safely.
- Denied, cancelled, and interrupted writes preserve recoverable session state.
- CLI and Vox consumers receive consistent approvals and results.
- Tests cover successful operations and the destructive boundary cases.

## Shell

### Outcome

The agent can run non-interactive commands inside the active workspace and
reliably report their output and exit status.

### Scope

- Run commands with the workspace as the working directory.
- Capture standard output, standard error, and exit status.
- Stream output while a command is running.
- Enforce execution time and output limits.
- Support cancellation and process cleanup.
- Provide a controlled set of environment variables without exposing secrets.

### Safety boundaries

- Shell execution requires explicit approval.
- The approval shows the exact command and working directory.
- Ethos does not infer that a command is safe from its text.
- The execution boundary must be defined before implementation; using the
  workspace as the working directory alone does not restrict host access.
- Output returned to the model and stored in history is bounded.
- Persistent terminals, interactive prompts, and unrestricted host access are
  outside this milestone.

### Complete when

- Commands execute only after approval through the shared tool path.
- Completion, failure, timeout, cancellation, and indeterminate outcomes are
  distinguishable.
- Child processes do not remain running after a definitive timeout or
  cancellation.
- Runtime events record bounded execution metadata without command output or
  secrets.
- Behaviour is consistent through CLI and Vox sessions.

## Milestone completion

Milestone 1 is complete when all four features meet their completion criteria,
the overview and developer documentation reflect the shipped behaviour, and
the full verification suite passes.
