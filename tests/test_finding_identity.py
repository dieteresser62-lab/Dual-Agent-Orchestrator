from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from artifact_models import _FINDING_ID_RE as ARTIFACT_FINDING_ID_RE
from contracts import AgentRole, ApprovalMarker, SOURCE_FINDING_ID_PATTERN
from finding_identity import (
    FINDING_ID_EXAMPLE,
    FINDING_ID_PATTERN,
    FINDING_ID_PATTERN_TEXT,
    FINDING_ID_PREFIX,
    format_finding_id,
    parse_finding_number,
)
from native_review_contract import (
    NativeReviewContext,
    load_native_review_schema,
    native_review_provider_response_schema,
)
from native_review_request import load_native_review_request_schema
from rejected_response_shape import _FINDING_ID_RE as REJECTED_FINDING_ID_RE


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = (
    "orchestrator-artifact-v3.schema.json",
    "native-agent-implementer-request-v3.schema.json",
    "native-agent-implementer-result-v3.schema.json",
    "native-agent-review-request-v3.schema.json",
    "native-agent-review-result-v3.schema.json",
)


def _finding_patterns(value: object) -> list[str]:
    if isinstance(value, dict):
        pattern = value.get("pattern")
        result = [pattern] if isinstance(pattern, str) and re.match(r"^\^[A-Z]-", pattern) else []
        for child in value.values():
            result.extend(_finding_patterns(child))
        return result
    if isinstance(value, list):
        return [pattern for child in value for pattern in _finding_patterns(child)]
    return []


def test_finding_id_producers_and_validators_share_one_definition() -> None:
    assert SOURCE_FINDING_ID_PATTERN is FINDING_ID_PATTERN
    assert ARTIFACT_FINDING_ID_RE is FINDING_ID_PATTERN
    assert REJECTED_FINDING_ID_RE is FINDING_ID_PATTERN
    assert FINDING_ID_EXAMPLE == format_finding_id(1)
    assert FINDING_ID_EXAMPLE.startswith(FINDING_ID_PREFIX)
    assert [format_finding_id(number) for number in (1, 9, 10, 100)] == [
        "R-01", "R-09", "R-10", "R-100"
    ]
    for invalid in (0, -1, True, 1.0):
        with pytest.raises(ValueError):
            format_finding_id(invalid)
    for number in (1, 2, 10, 100):
        assert parse_finding_number(format_finding_id(number)) == number
    assert parse_finding_number("R-1") == 1  # Existing domain pattern accepts unpadded IDs.
    for invalid_id in ("R-00", "R-001", "C-01", "R-01\n", "R-01x", 1):
        with pytest.raises(ValueError, match="invalid finding id"):
            parse_finding_number(invalid_id)


def test_all_bundled_and_generated_schema_id_patterns_match_the_canonical_id() -> None:
    documents = [
        json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
        for name in SCHEMAS
    ]
    documents.extend((load_native_review_schema(), load_native_review_request_schema()))
    for marker, operation, slice_id in (
        (ApprovalMarker.SLICE, "reviewer_slice_review", "01"),
        (ApprovalMarker.FINAL_REVIEW, "reviewer_final_review", "FINAL"),
    ):
        context = NativeReviewContext(
            "test-run", "1", operation, "a" * 64, AgentRole.REVIEWER,
            marker, slice_id, 1,
        )
        documents.append(native_review_provider_response_schema(context))
    expected_counts = (1, 1, 1, 4, 2, 4, 5, 9, 5)
    samples = ("R-01", "R-10", "R-100", "R-00", "R-1", "R-01x", "C-01")
    expected = tuple(FINDING_ID_PATTERN.fullmatch(sample) is not None for sample in samples)
    for document, count in zip(documents, expected_counts, strict=True):
        patterns = _finding_patterns(document)
        assert len(patterns) == count
        for pattern in patterns:
            assert pattern == FINDING_ID_PATTERN_TEXT
            assert tuple(re.fullmatch(pattern, sample) is not None for sample in samples) == expected
