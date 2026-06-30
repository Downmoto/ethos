from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from cassiopeia.workspaces import WorkspaceAvailabilityPolicy, WorkspaceDefinition


def test_workspace_definition_accepts_required_fields() -> None:
    workspace = WorkspaceDefinition(
        id="workspace-main",
        slug="main-workspace",
        display_name="Main workspace",
        root_path=Path("/tmp/main-workspace"),
        manager_persona_id="persona-manager",
        created_at=datetime(2026, 6, 30, 12, 0, tzinfo=UTC),
        updated_at=datetime(2026, 6, 30, 12, 1, tzinfo=UTC),
    )

    assert workspace.slug == "main-workspace"
    assert workspace.root_path == Path("/tmp/main-workspace")
    assert workspace.manager_persona_id == "persona-manager"
    assert workspace.availability == WorkspaceAvailabilityPolicy()


def test_workspace_definition_rejects_unknown_fields_and_invalid_slug() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        WorkspaceDefinition.model_validate(
            {
                "id": "workspace-main",
                "slug": "main-workspace",
                "display_name": "Main workspace",
                "root_path": "/tmp/main-workspace",
                "manager_persona_id": "persona-manager",
                "unexpected": True,
            }
        )

    with pytest.raises(ValidationError, match="String should match pattern"):
        WorkspaceDefinition(
            id="workspace-main",
            slug="Main Workspace",
            display_name="Main workspace",
            root_path=Path("/tmp/main-workspace"),
            manager_persona_id="persona-manager",
        )


def test_workspace_availability_policy_is_json_round_trippable() -> None:
    workspace = WorkspaceDefinition(
        id="workspace-main",
        slug="main-workspace",
        display_name="Main workspace",
        root_path=Path("/tmp/main-workspace"),
        manager_persona_id="persona-manager",
        availability=WorkspaceAvailabilityPolicy(
            available_personas=("persona-manager",),
            available_gateways=("gateway-tui",),
            available_tools=("tool-memory-write",),
            available_skills=("skill-review",),
            available_workflows=("workflow-triage",),
        ),
    )

    loaded = WorkspaceDefinition.model_validate_json(workspace.model_dump_json())

    assert loaded == workspace
