import pytest
from pydantic import ValidationError

from cassiopeia.gateways import GatewayOrigin
from cassiopeia.sessions import (
    MessageDirection,
    MessageLink,
    MessageRecord,
    MessageRole,
    SessionRecord,
    SessionStatus,
)


def make_origin() -> GatewayOrigin:
    return GatewayOrigin(
        gateway_id="gateway-tui",
        external_user_id="local-user",
        external_conversation_id="local-session",
    )


def test_session_and_message_models_reference_related_records_by_id() -> None:
    session = SessionRecord(
        id="session-1",
        workspace_id="workspace-main",
        persona_id="persona-manager",
        gateway_id="gateway-tui",
        origin=make_origin(),
        recent_workflow_run_ids=("workflow-run-1",),
        recent_subagent_task_ids=("subagent-task-1",),
    )
    message = MessageRecord(
        id="message-1",
        session_id=session.id,
        role=MessageRole.USER,
        direction=MessageDirection.INBOUND,
        content="Hello",
        origin=make_origin(),
        links=(MessageLink(workflow_run_id="workflow-run-1"),),
    )

    assert session.status is SessionStatus.ACTIVE
    assert message.links[0].workflow_run_id == "workflow-run-1"


def test_session_rejects_invalid_lifecycle_status() -> None:
    with pytest.raises(ValidationError):
        SessionRecord.model_validate(
            {
                "id": "session-1",
                "workspace_id": "workspace-main",
                "persona_id": "persona-manager",
                "gateway_id": "gateway-tui",
                "origin": make_origin().model_dump(mode="json"),
                "status": "paused",
            }
        )
