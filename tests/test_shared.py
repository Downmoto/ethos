from datetime import datetime

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from cassiopeia.shared import (
    AwareTimestamp,
    DefinitionScope,
    EntityId,
    ExternalId,
    MemoryScope,
    NonEmptyString,
    Slug,
    TimestampedRecord,
)


def test_non_empty_string_rejects_empty_values() -> None:
    adapter: TypeAdapter[str] = TypeAdapter(NonEmptyString)

    assert adapter.validate_python("workspace-1") == "workspace-1"

    with pytest.raises(ValidationError, match="at least 1 character"):
        adapter.validate_python("")


def test_slug_accepts_filesystem_safe_lowercase_values() -> None:
    adapter: TypeAdapter[str] = TypeAdapter(Slug)

    assert adapter.validate_python("main-workspace") == "main-workspace"

    for value in ("Main", "main_workspace", "-main", "main-"):
        with pytest.raises(ValidationError, match="String should match pattern"):
            adapter.validate_python(value)


def test_id_primitives_are_non_empty_strings() -> None:
    entity_id: TypeAdapter[str] = TypeAdapter(EntityId)
    external_id: TypeAdapter[str] = TypeAdapter(ExternalId)

    assert entity_id.validate_python("persona-1") == "persona-1"
    assert external_id.validate_python("telegram:123") == "telegram:123"

    with pytest.raises(ValidationError, match="at least 1 character"):
        entity_id.validate_python("")


def test_timestamped_record_uses_timezone_aware_defaults() -> None:
    record = TimestampedRecord()

    assert record.created_at.tzinfo is not None
    assert record.updated_at.tzinfo is not None


def test_aware_timestamp_rejects_naive_datetimes() -> None:
    class Model(BaseModel):
        value: AwareTimestamp

    with pytest.raises(ValidationError, match="timezone"):
        Model(value=datetime(2026, 6, 30, 12, 0, 0))


def test_scope_enums_match_1_0_scope_terms() -> None:
    assert [scope.value for scope in DefinitionScope] == ["global", "workspace"]
    assert [scope.value for scope in MemoryScope] == [
        "session",
        "workspace",
        "persona",
        "user",
    ]
