import pytest
from pydantic import ValidationError

from cassiopeia.memory import (
    EmbeddingMetadata,
    MemoryExposureState,
    MemoryRecord,
    MemorySourceKind,
    MemorySourceReference,
)
from cassiopeia.shared import MemoryScope


def test_memory_record_captures_scope_source_and_embedding_freshness() -> None:
    memory = MemoryRecord(
        id="memory-1",
        scope=MemoryScope.WORKSPACE,
        scope_id="workspace-main",
        content="Use Canadian spelling in docs.",
        source=MemorySourceReference(
            kind=MemorySourceKind.AGENT,
            session_id="session-1",
            message_id="message-1",
        ),
        exposure_state=MemoryExposureState.EXPOSED,
        tags=("docs",),
        importance=0.8,
        confidence=0.9,
        embedding=EmbeddingMetadata(
            provider_id="provider-openai",
            model_id="embedding-model",
            vector_dimension=1536,
        ),
    )

    assert MemoryRecord.model_validate_json(memory.model_dump_json()) == memory


def test_memory_record_rejects_invalid_state_and_scores() -> None:
    with pytest.raises(ValidationError):
        MemoryRecord.model_validate(
            {
                "id": "memory-1",
                "scope": "workspace",
                "scope_id": "workspace-main",
                "content": "Fact",
                "source": {"kind": "agent"},
                "exposure_state": "hidden",
            }
        )

    with pytest.raises(ValidationError):
        MemoryRecord(
            id="memory-1",
            scope=MemoryScope.WORKSPACE,
            scope_id="workspace-main",
            content="Fact",
            source=MemorySourceReference(kind=MemorySourceKind.USER),
            confidence=2,
        )
