import pytest
from pydantic import ValidationError

from cassiopeia.events.types import EventType
from cassiopeia.permissions import SecurityRing
from cassiopeia.shared import DefinitionScope
from cassiopeia.workflows import (
    HookDefinition,
    HookRegistry,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
    WorkflowNodeType,
    WorkflowRunStatus,
    WorkflowRunSummary,
    WorkflowTrigger,
    WorkflowTriggerType,
)


def make_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        id="workflow-triage",
        name="Triage",
        description="Triage an inbound request",
        scope=DefinitionScope.WORKSPACE,
        workspace_id="workspace-main",
        triggers=(WorkflowTrigger(type=WorkflowTriggerType.USER),),
        nodes=(
            WorkflowNode(
                id="start",
                type=WorkflowNodeType.PROMPT,
                name="Summarise request",
                security_ring=SecurityRing.RING_3,
            ),
            WorkflowNode(
                id="respond",
                type=WorkflowNodeType.GATEWAY_RESPONSE,
                name="Respond",
            ),
        ),
        edges=(WorkflowEdge(from_node_id="start", to_node_id="respond"),),
        required_tools=("tool-memory-write",),
        required_skills=("skill-review",),
    )


def test_workflow_and_hook_registry_round_trip_as_user_authored_json() -> None:
    workflow = make_workflow()
    hook = HookDefinition(
        id="hook-message-received",
        name="Message received",
        scope=DefinitionScope.WORKSPACE,
        event_type=EventType.MESSAGE_RECEIVED,
        workflow_id=workflow.id,
        priority=10,
        blocking=True,
    )
    registry = HookRegistry(
        id="hooks-workspace-main",
        scope=DefinitionScope.WORKSPACE,
        workspace_id="workspace-main",
        hooks=(hook,),
    )

    assert WorkflowDefinition.model_validate_json(workflow.model_dump_json()) == workflow
    assert HookRegistry.model_validate_json(registry.model_dump_json()) == registry


def test_workflow_node_types_cover_minimum_1_0_nodes() -> None:
    assert {node_type.value for node_type in WorkflowNodeType} == {
        "prompt",
        "tool_call",
        "conditional",
        "transform",
        "human_approval",
        "subagent_delegation",
        "memory_write",
        "gateway_response",
        "script",
    }


def test_workflow_edges_and_hook_event_types_are_validated() -> None:
    with pytest.raises(ValidationError, match="workflow edges must reference existing nodes"):
        WorkflowDefinition(
            id="workflow-broken",
            name="Broken",
            description="Broken workflow",
            scope=DefinitionScope.GLOBAL,
            nodes=(WorkflowNode(id="start", type=WorkflowNodeType.PROMPT, name="Start"),),
            edges=(WorkflowEdge(from_node_id="start", to_node_id="missing"),),
        )

    with pytest.raises(ValidationError):
        HookDefinition.model_validate(
            {
                "id": "hook-bad",
                "name": "Bad hook",
                "scope": "global",
                "event_type": "not.real",
                "workflow_id": "workflow-triage",
            }
        )


def test_workflow_run_summary_references_runtime_state_by_id() -> None:
    summary = WorkflowRunSummary(
        id="workflow-run-1",
        workflow_id="workflow-triage",
        session_id="session-1",
        status=WorkflowRunStatus.STARTED,
        started_by="persona-manager",
    )

    assert summary.workflow_id == "workflow-triage"
