import pytest
from pydantic import ValidationError

from cassiopeia.permissions import (
    ActionScope,
    GrantDuration,
    PermissionAction,
    PermissionAuditRecord,
    PermissionDecision,
    PermissionGrant,
    PermissionPromptRequest,
    SecurityRing,
)


def make_action() -> PermissionAction:
    return PermissionAction(
        id="action-shell-command",
        name="Run shell command",
        minimum_ring=SecurityRing.RING_0,
        scope=ActionScope(kind="workspace", id="workspace-main"),
    )


def test_permission_prompt_grant_and_audit_records_round_trip() -> None:
    action = make_action()
    prompt = PermissionPromptRequest(
        id="prompt-1",
        session_id="session-1",
        persona_id="persona-manager",
        action=action,
        requested_duration=GrantDuration.SESSION,
    )
    grant = PermissionGrant(
        id="grant-1",
        action_id=action.id,
        scope=action.scope,
        decision=PermissionDecision.APPROVED,
        duration=GrantDuration.SESSION,
        session_id=prompt.session_id,
    )
    audit = PermissionAuditRecord(
        id="audit-1",
        prompt_request_id=prompt.id,
        action_id=action.id,
        scope=action.scope,
        ring=SecurityRing.RING_0,
        decision=PermissionDecision.APPROVED,
        duration=GrantDuration.SESSION,
    )

    assert PermissionGrant.model_validate_json(grant.model_dump_json()) == grant
    assert audit.prompt_request_id == "prompt-1"


def test_permission_action_rejects_invalid_ring() -> None:
    with pytest.raises(ValidationError):
        PermissionAction.model_validate(
            {
                "id": "action-risky",
                "name": "Risky action",
                "minimum_ring": 9,
                "scope": {"kind": "global"},
            }
        )
