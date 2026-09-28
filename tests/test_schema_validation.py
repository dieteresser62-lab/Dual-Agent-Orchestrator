from __future__ import annotations

import pytest

from schema_validation import (
    SchemaDefinitionError,
    SchemaMismatch,
    check_schema,
    validate_schema_document,
)


def test_public_schema_validator_rejects_unknown_keywords() -> None:
    with pytest.raises(SchemaDefinitionError, match="unsupported schema keyword"):
        check_schema({"type": "object", "surprise": True})


@pytest.mark.parametrize("zero", (0, 0.0))
def test_artifact_budget_schema_rejects_zero_for_integer_and_float(zero: float) -> None:
    from artifact_models import load_schema

    rule = load_schema()["$defs"]["role_profile"]["properties"]["max_budget_usd"]
    assert rule["exclusiveMinimum"] == 0
    check_schema(rule)
    with pytest.raises(SchemaMismatch, match="greater than 0"):
        validate_schema_document(zero, rule)
    validate_schema_document(0.01, rule)


def test_public_schema_validator_preserves_closed_object_error_path() -> None:
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
        "additionalProperties": False,
    }
    check_schema(schema)

    with pytest.raises(SchemaMismatch) as raised:
        validate_schema_document({"name": "ok", "extra": True}, schema)

    assert raised.value.path == ("extra",)
    assert raised.value.message == "is not an allowed property"


def test_public_schema_validator_enforces_writer_max_length() -> None:
    schema = {"type": "string", "minLength": 1, "maxLength": 3}
    check_schema(schema)
    validate_schema_document("abc", schema)

    with pytest.raises(SchemaMismatch) as raised:
        validate_schema_document("abcd", schema)

    assert raised.value.path == ()
    assert raised.value.message == "must contain at most 3 character(s)"


def test_public_schema_validator_enforces_integer_maximum() -> None:
    schema = {"type": "integer", "minimum": 1, "maximum": 512}
    check_schema(schema)
    validate_schema_document(512, schema)

    with pytest.raises(SchemaMismatch) as raised:
        validate_schema_document(513, schema)

    assert raised.value.path == ()
    assert raised.value.message == "must be at most 512"


@pytest.mark.parametrize(
    ("value", "valid"),
    (("2026-08-22T10:00:00+02:00", True), ("2026-08-22T10:00:00", False)),
)
def test_public_schema_validator_requires_timezone_for_date_time(
    value: str, valid: bool
) -> None:
    schema = {"type": "string", "format": "date-time"}
    check_schema(schema)
    if valid:
        validate_schema_document(value, schema)
    else:
        with pytest.raises(SchemaMismatch, match="timezone"):
            validate_schema_document(value, schema)
