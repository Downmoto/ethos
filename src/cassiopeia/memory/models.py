"""Memory record models."""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from cassiopeia.shared import EntityId, MemoryScope, NonEmptyString, TimestampedRecord


class MemoryExposureState(StrEnum):
    """User-visible state for a memory record."""

    PROPOSED = "proposed"
    EXPOSED = "exposed"
    REJECTED = "rejected"
    DELETED = "deleted"


class MemorySourceKind(StrEnum):
    """Origin of a memory record."""

    USER = "user"
    AGENT = "agent"
    WORKFLOW = "workflow"
    IMPORT = "import"


class MemorySourceReference(BaseModel):
    """Reference to the source that produced a memory."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: MemorySourceKind
    session_id: EntityId | None = None
    message_id: EntityId | None = None
    workflow_run_id: EntityId | None = None


class EmbeddingMetadata(BaseModel):
    """Embedding freshness metadata for a memory."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_id: EntityId
    model_id: EntityId
    vector_dimension: Annotated[int, Field(gt=0)]
    stale: bool = False


class MemoryRecord(TimestampedRecord):
    """Curated reusable memory."""

    id: EntityId
    scope: MemoryScope
    scope_id: EntityId
    content: NonEmptyString
    source: MemorySourceReference
    exposure_state: MemoryExposureState = MemoryExposureState.PROPOSED
    tags: tuple[NonEmptyString, ...] = ()
    importance: Annotated[float, Field(ge=0, le=1)] | None = None
    confidence: Annotated[float, Field(ge=0, le=1)] | None = None
    embedding: EmbeddingMetadata | None = None
