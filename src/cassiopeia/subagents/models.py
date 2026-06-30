"""Task-scoped subagent models."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from cassiopeia.shared import EntityId, NonEmptyString, TimestampedRecord


class SubagentSourceType(StrEnum):
    """How a subagent persona was chosen."""

    EXISTING_PERSONA = "existing_persona"
    TEMPORARY_WORKER = "temporary_worker"


class SubagentStatus(StrEnum):
    """Lifecycle state for a subagent task."""

    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SubagentSource(BaseModel):
    """Selected or temporary persona source."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: SubagentSourceType
    persona_id: EntityId | None = None
    temporary_name: NonEmptyString | None = None


class SubagentContextPacket(BaseModel):
    """Bounded context passed to a task-scoped subagent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_statement: NonEmptyString
    expected_output: NonEmptyString | None = None
    relevant_user_request: NonEmptyString | None = None
    session_summary: NonEmptyString | None = None
    selected_message_ids: tuple[EntityId, ...] = ()
    memory_ids: tuple[EntityId, ...] = ()
    allowed_skills: tuple[EntityId, ...] = ()
    allowed_tools: tuple[EntityId, ...] = ()
    security_constraints: tuple[NonEmptyString, ...] = ()
    handoff_notes: NonEmptyString | None = None


class SubagentResult(BaseModel):
    """Reported result from a subagent task."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    answer: NonEmptyString
    actions_taken: tuple[NonEmptyString, ...] = ()
    tools_or_workflows_used: tuple[EntityId, ...] = ()
    assumptions: tuple[NonEmptyString, ...] = ()
    unresolved_issues: tuple[NonEmptyString, ...] = ()
    suggested_updates: tuple[NonEmptyString, ...] = ()


class PromotionProposal(BaseModel):
    """Proposal to save a temporary worker as a persona."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposed_persona_id: EntityId
    reason: NonEmptyString
    approval_permission_id: EntityId | None = None


class SubagentTask(TimestampedRecord):
    """Task-scoped subagent run record."""

    id: EntityId
    name: NonEmptyString
    session_id: EntityId
    workspace_id: EntityId
    orchestrator_persona_id: EntityId
    source: SubagentSource
    task: NonEmptyString
    allowed_skills: tuple[EntityId, ...] = ()
    allowed_tools: tuple[EntityId, ...] = ()
    context_packet: SubagentContextPacket
    status: SubagentStatus = SubagentStatus.CREATED
    result: SubagentResult | None = None
    promotion: PromotionProposal | None = None
