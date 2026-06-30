"""Shared primitives for cassiopeia domain models."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

type NonEmptyString = Annotated[str, Field(min_length=1)]
type Slug = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
type AwareTimestamp = AwareDatetime
type EntityId = NonEmptyString
type ExternalId = NonEmptyString


class DefinitionScope(StrEnum):
    """Scope for user-authored definitions."""

    GLOBAL = "global"
    WORKSPACE = "workspace"


class MemoryScope(StrEnum):
    """Reusable memory scopes supported by cassiopeia 1.0."""

    SESSION = "session"
    WORKSPACE = "workspace"
    PERSONA = "persona"
    USER = "user"


class TimestampedRecord(BaseModel):
    """Created/updated metadata shared by definition and runtime records."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    created_at: AwareTimestamp = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: AwareTimestamp = Field(default_factory=lambda: datetime.now(UTC))
