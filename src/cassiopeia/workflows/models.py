"""Workflow definition and hook registry models."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cassiopeia.events.types import EventType
from cassiopeia.permissions import SecurityRing
from cassiopeia.shared import DefinitionScope, EntityId, NonEmptyString, TimestampedRecord


class WorkflowTriggerType(StrEnum):
    """Reasons a workflow can start."""

    USER = "user"
    HOOK = "hook"
    AGENT = "agent"
    SCHEDULED = "scheduled"
    GATEWAY_EVENT = "gateway_event"


class WorkflowNodeType(StrEnum):
    """Minimum 1.0 workflow node types."""

    PROMPT = "prompt"
    TOOL_CALL = "tool_call"
    CONDITIONAL = "conditional"
    TRANSFORM = "transform"
    HUMAN_APPROVAL = "human_approval"
    SUBAGENT_DELEGATION = "subagent_delegation"
    MEMORY_WRITE = "memory_write"
    GATEWAY_RESPONSE = "gateway_response"
    SCRIPT = "script"


class WorkflowRunStatus(StrEnum):
    """Runtime summary status for a workflow run."""

    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowTrigger(BaseModel):
    """Declarative workflow trigger."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: WorkflowTriggerType
    name: NonEmptyString | None = None


class WorkflowNode(BaseModel):
    """Workflow graph node definition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    type: WorkflowNodeType
    name: NonEmptyString
    config: dict[str, Any] = Field(default_factory=dict)
    required_tools: tuple[EntityId, ...] = ()
    required_skills: tuple[EntityId, ...] = ()
    security_ring: SecurityRing | None = None


class WorkflowEdge(BaseModel):
    """Directed workflow graph edge."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    from_node_id: EntityId
    to_node_id: EntityId
    condition: NonEmptyString | None = None


class WorkflowDefinition(TimestampedRecord):
    """User-authored workflow definition."""

    id: EntityId
    name: NonEmptyString
    description: NonEmptyString
    scope: DefinitionScope
    workspace_id: EntityId | None = None
    triggers: tuple[WorkflowTrigger, ...] = ()
    nodes: tuple[WorkflowNode, ...]
    edges: tuple[WorkflowEdge, ...] = ()
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    required_tools: tuple[EntityId, ...] = ()
    required_skills: tuple[EntityId, ...] = ()
    security_ring: SecurityRing = SecurityRing.RING_3
    enabled: bool = True

    @model_validator(mode="after")
    def edge_references_existing_nodes(self) -> "WorkflowDefinition":
        node_ids = {node.id for node in self.nodes}
        for edge in self.edges:
            if edge.from_node_id not in node_ids or edge.to_node_id not in node_ids:
                raise ValueError("workflow edges must reference existing nodes")
        return self


class WorkflowRunSummary(TimestampedRecord):
    """Runtime summary for a workflow run."""

    id: EntityId
    workflow_id: EntityId
    session_id: EntityId | None = None
    status: WorkflowRunStatus
    started_by: EntityId | None = None


class HookFilter(BaseModel):
    """Optional event filter for a hook."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    gateway_id: EntityId | None = None
    workspace_id: EntityId | None = None
    persona_id: EntityId | None = None
    tags: tuple[NonEmptyString, ...] = ()
    event_fields: dict[str, Any] = Field(default_factory=dict)


class HookDefinition(TimestampedRecord):
    """User-authored hook from an event to a workflow."""

    id: EntityId
    name: NonEmptyString
    enabled: bool = True
    scope: DefinitionScope
    event_type: EventType
    filters: HookFilter = HookFilter()
    priority: int = 100
    blocking: bool = False
    workflow_id: EntityId


class HookRegistry(TimestampedRecord):
    """Consolidated hook registry file."""

    id: EntityId
    scope: DefinitionScope
    workspace_id: EntityId | None = None
    hooks: tuple[HookDefinition, ...] = ()
