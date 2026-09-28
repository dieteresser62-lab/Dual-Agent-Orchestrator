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
    "orchestrator-artifact-v2.schema.json",
    "native-agent-codex-request-v2.schema.json",
    "native-agent-codex-result-v2.schema.json",
    "native-agent-review-request-v2.schema.json",
    "native-agent-review-result-v2.schema.json",
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
        "C-01", "C-09", "C-10", "C-100"
    ]
    for invalid in (0, -1, True, 1.0):
        with pytest.raises(ValueError):
            format_finding_id(invalid)


def test_all_bundled_and_generated_schema_id_patterns_match_the_canonical_id() -> None:
    portable_end = FINDING_ID_PATTERN_TEXT.removesuffix("$") + r"(?![\s\S])"
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
    expected_counts = (1, 1, 1, 4, 2, 4, 5, 7, 5)
    samples = ("C-01", "C-10", "C-100", "C-00", "C-1", "C-01x", "R-01")
    expected = tuple(FINDING_ID_PATTERN.fullmatch(sample) is not None for sample in samples)
    for document, count in zip(documents, expected_counts, strict=True):
        patterns = _finding_patterns(document)
        assert len(patterns) == count
        for pattern in patterns:
            assert pattern in (FINDING_ID_PATTERN_TEXT, portable_end)
            assert tuple(re.fullmatch(pattern, sample) is not None for sample in samples) == expected
