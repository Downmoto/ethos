import pytest
from pydantic import ValidationError

from cassiopeia.subagents import (
    PromotionProposal,
    SubagentContextPacket,
    SubagentResult,
    SubagentSource,
    SubagentSourceType,
    SubagentStatus,
    SubagentTask,
)


def test_subagent_task_captures_context_result_and_promotion_metadata() -> None:
    task = SubagentTask(
        id="subagent-task-1",
        name="Review docs",
        session_id="session-1",
        workspace_id="workspace-main",
        orchestrator_persona_id="persona-manager",
        source=SubagentSource(type=SubagentSourceType.TEMPORARY_WORKER, temporary_name="Reviewer"),
        task="Review milestone notes",
        allowed_skills=("skill-review",),
        allowed_tools=("tool-read-files",),
        context_packet=SubagentContextPacket(
            task_statement="Review milestone notes",
            selected_message_ids=("message-1",),
            memory_ids=("memory-1",),
            allowed_skills=("skill-review",),
            allowed_tools=("tool-read-files",),
        ),
        status=SubagentStatus.COMPLETED,
        result=SubagentResult(answer="Looks good", actions_taken=("Read docs",)),
        promotion=PromotionProposal(
            proposed_persona_id="persona-reviewer",
            reason="Repeated review work",
            approval_permission_id="grant-1",
        ),
    )

    assert SubagentTask.model_validate_json(task.model_dump_json()) == task


def test_subagent_task_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SubagentTask.model_validate(
            {
                "id": "subagent-task-1",
                "name": "Review docs",
                "session_id": "session-1",
                "workspace_id": "workspace-main",
                "orchestrator_persona_id": "persona-manager",
                "source": {"type": "existing_persona", "persona_id": "persona-reviewer"},
                "task": "Review milestone notes",
                "context_packet": {"task_statement": "Review milestone notes"},
                "raw_context": "too much",
            }
        )
