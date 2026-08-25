# Milestone 3 — Orchestration

This document proposes the work required to complete the third milestone on
the road to beta v0.1.0. It defines product scope and completion outcomes, not
the final implementation design.

## Goal

Let Ethos coordinate repeatable work across agent runs, lifecycle events, and
time while preserving the same identity, capability, approval, and recovery
boundaries used by an interactive session.

## Delivery order

1. Shared orchestration foundation
2. Workflows
3. Multi-agent delegation
4. Hooks
5. Scheduling

Workflows establish the reusable unit of work. Delegation adds bounded child
agents to that execution model, while hooks and schedules become alternate
ways to start the same workflows.

## Shared orchestration foundation

### Outcome

Every orchestrated action has durable identity, state, lineage, and a single
execution path before multiple trigger types are introduced.

### Scope

- Give every workflow and child-agent run a stable execution identifier.
- Record pending, running, paused, completed, failed, and cancelled states.
- Link executions to their initiating session, parent run, hook, or schedule.
- Record bounded timestamps, outcomes, token usage, and failure categories.
- Reuse the existing runtime, tool policy, approval, and event boundaries.
- Expose execution status and history through the service, CLI, and Vox
  protocol.

### Complete when

- Execution state survives process restarts.
- State transitions are validated and cannot move backwards accidentally.
- Approval pauses and interrupted writes retain their existing recovery
  guarantees.
- Cancellation has defined propagation and terminal-state behaviour.
- Events contain correlation metadata without prompts, reasoning, arguments,
  results, memory content, persona instructions, or secrets.

## Workflows

### Outcome

Users can define, validate, and run reusable sequences of agent tasks and
capabilities.

### Scope

- Create, show, list, update, enable, disable, and remove workflows.
- Define named inputs and a sequence of steps.
- Run an agent task with a selected persona.
- Invoke an available capability with validated arguments.
- Pass bounded outputs from one step into a later step.
- Stop, inspect, and cancel a workflow execution.
- Retain execution status and bounded step results.

### Safety boundaries

- Workflow definitions are data and cannot execute arbitrary host code.
- Every step resolves the effective workspace, persona, and capabilities at
  execution time.
- Tool calls use the same validation and approval path as interactive runs.
- A workflow cannot grant itself capabilities or permissions.
- Updating a definition does not alter an execution already in progress.
- Branching graphs, loops, embedded scripting, and a visual editor are outside
  this milestone.

### Complete when

- Invalid definitions fail before an execution starts.
- Steps run in declared order and expose deterministic input and output rules.
- The failure policy is explicit and does not silently skip failed steps.
- Paused executions can resume after approval without repeating completed
  side effects.
- Definitions and executions remain usable after restarting Ethos.
- Service, CLI, and Vox operations expose consistent workflow behaviour.

## Multi-agent delegation

### Outcome

An agent can delegate a bounded task to a child agent and use the child's
result without sharing more context or authority than the task requires.

### Scope

- Delegate a task with explicit instructions and optional supporting context.
- Select an allowed persona for the child agent.
- Run multiple child tasks within configured count and concurrency limits.
- Return each child's status, final result, usage, and execution identifier.
- Link child runs to the parent run and active workspace.
- Cancel unfinished child runs when requested by the parent or user.

### Safety boundaries

- Child agents inherit the active workspace and cannot exceed the parent's
  effective capabilities or permissions.
- Only explicitly supplied context is passed to a child; parent history,
  reasoning, and memory are not copied implicitly.
- Child tool calls use the normal validation and approval flow.
- Delegation depth, child count, concurrency, duration, output, and token usage
  are bounded.
- Recursive delegation and open-ended autonomous agent swarms are outside this
  milestone.

### Complete when

- Parent and child execution lineage is durable and visible.
- A child result can be consumed without copying the child's full history into
  the parent session.
- Child failure, timeout, cancellation, and approval pauses have distinct
  outcomes.
- Cancelling a parent stops cancellable children without inventing terminal
  results for indeterminate writes.
- Concurrent children cannot corrupt shared session or workflow state.
- Tests demonstrate capability, persona, memory, and workspace isolation.

## Hooks

### Outcome

Users can start configured workflows in response to selected Ethos lifecycle
events.

### Scope

- Create, show, list, update, enable, disable, and remove hooks.
- Select a typed lifecycle event as the trigger.
- Filter on bounded, indexed event metadata.
- Map permitted event fields into workflow inputs.
- Record the source event and resulting workflow execution.
- Inspect hook invocation history and failures.

### Safety boundaries

- Hooks run after the source event is durable and cannot change the source
  operation's outcome.
- Hooks cannot trigger from prompt, reasoning, tool argument, or result
  content.
- Trigger depth and rate are bounded to prevent event loops and runaway work.
- Hook-started workflows retain all normal capability and approval rules.
- Disabled or invalid hooks do not prevent unrelated events from being stored.

### Complete when

- Trigger matching and filter behaviour are deterministic and tested.
- Each invocation is traceable from source event to workflow execution.
- Duplicate-delivery and process-restart behaviour are explicitly defined.
- Recursive hook chains stop at the configured boundary.
- A failing hook is isolated and produces a useful diagnostic.
- Service, CLI, and Vox operations expose consistent hook behaviour.

## Scheduling

### Outcome

Users can run workflows once or on a recurring schedule and inspect what ran,
what is next, and what failed.

### Scope

- Create one-time and recurring schedules for enabled workflows.
- Configure the schedule's timezone and workflow inputs.
- Show the next planned run and recent execution history.
- Enable, disable, update, and remove schedules.
- Define missed-run and overlapping-run behaviour.
- Start due workflows without requiring an interactive session.

### Safety boundaries

- Scheduled workflows retain the selected workspace, persona, capabilities,
  and approval rules.
- A scheduled execution that requires approval pauses for user action rather
  than bypassing policy.
- Only one scheduler instance claims a due occurrence.
- Clock changes, daylight-saving transitions, restarts, and missed occurrences
  have defined behaviour.
- Distributed scheduling and high-availability coordination are outside this
  milestone.

### Complete when

- Schedule definitions and occurrence claims survive process restarts.
- A due occurrence starts no more than one workflow execution.
- One-time schedules cannot run twice.
- Recurring schedules calculate their next run consistently in their configured
  timezone.
- Missed and overlapping runs follow the configured policy.
- Service, CLI, and Vox operations expose consistent schedule behaviour.

## Milestone completion

Milestone 3 is complete when workflows can be started manually, by a lifecycle
event, or by a schedule; agents can delegate bounded child tasks; every
execution remains observable and recoverable; the developer documentation
reflects the shipped behaviour; and the full verification suite passes.
