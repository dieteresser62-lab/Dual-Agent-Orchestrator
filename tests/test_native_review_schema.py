from __future__ import annotations

from copy import deepcopy
import json

import pytest

from native_review_contract import (
    NativeReviewContractError,
    NativeReviewErrorCode,
    canonical_native_review_json,
    load_native_review_schema,
    validate_native_review_document,
)


REQUEST_ID = "native-review-request-" + "a" * 64


def _review() -> dict[str, object]:
    return {
        "schema_version": "native-agent-review-result-v2",
        "result_type": "review_result",
        "request_id": REQUEST_ID,
        "reviewer": "claude",
        "decision": "approved",
        "new_findings": [],
        "status_changes": [],
        "reclassifications": [],
        "anchors": [],
        "review_evidence": {
            "dimensions": "schema and lifecycle",
            "largest_residual_risk": "later adapter wiring",
            "break_condition": "closed data is accepted with drift",
        },
        "pre_mortem": "A later caller may omit context binding.",
    }


def _assert_schema_error(document: dict[str, object]) -> None:
    with pytest.raises(NativeReviewContractError) as raised:
        validate_native_review_document(document)
    assert raised.value.code is NativeReviewErrorCode.SCHEMA_INVALID


def test_bundled_native_schema_self_checks_and_accepts_review() -> None:
    assert load_native_review_schema()["$id"] == "native-agent-review-result-v2"
    validate_native_review_document(_review())


def test_schema_accepts_minimal_closed_stop_request() -> None:
    validate_native_review_document(
        {
            "schema_version": "native-agent-review-result-v2",
            "result_type": "stop_request",
            "request_id": REQUEST_ID,
            "reviewer": "claude",
            "rule_id": "UNEXPECTED-PATH",
            "rationale": "A path is outside the bound scope.",
            "remediation_paths": [],
        }
    )


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("schema_version",), "v2"),
        (("reviewer",), "codex"),
        (("request_id",), "not-bound"),
        (("pre_mortem",), "contains\x00nul"),
        (("decision",), "maybe"),
    ),
)
def test_schema_rejects_invalid_closed_values(
    path: tuple[str, ...], value: object
) -> None:
    document = _review()
    target: dict[str, object] = document
    for part in path[:-1]:
        target = target[part]  # type: ignore[assignment]
    target[path[-1]] = value
    _assert_schema_error(document)


def test_schema_rejects_unknown_root_and_nested_fields() -> None:
    root_extra = _review()
    root_extra["commentary"] = "not allowed"
    _assert_schema_error(root_extra)

    nested_extra = _review()
    evidence = nested_extra["review_evidence"]
    assert isinstance(evidence, dict)
    evidence["confidence"] = 1
    _assert_schema_error(nested_extra)


def test_schema_rejects_contentless_review_at_transport_boundary() -> None:
    document = _review()
    document["review_evidence"] = None
    _assert_schema_error(document)


def test_schema_rejects_open_shell_string_and_empty_argv() -> None:
    shell = _review()
    shell["decision"] = "denied"
    shell["new_findings"] = [
        {
            "finding_id": "C-01",
            "finding_class": "BLOCKER",
            "summary": "Unsafe command surface",
            "acceptance_test": {
                "kind": "validation_command",
                "command": "pytest -q",
            },
        }
    ]
    _assert_schema_error(shell)

    empty = deepcopy(shell)
    acceptance = empty["new_findings"][0]["acceptance_test"]  # type: ignore[index]
    acceptance.pop("command")
    acceptance["argv"] = []
    _assert_schema_error(empty)


def test_schema_rejects_stop_request_with_review_fields() -> None:
    stop = {
        "schema_version": "native-agent-review-result-v2",
        "result_type": "stop_request",
        "request_id": REQUEST_ID,
        "reviewer": "claude",
        "rule_id": "STOP",
        "rationale": "Cannot continue.",
        "decision": "denied",
    }
    _assert_schema_error(stop)


def test_canonical_transport_json_is_stable_and_utf8_preserving() -> None:
    document = _review()
    document["pre_mortem"] = "Spätere Verdrahtung könnte driften."
    first = canonical_native_review_json(document)
    second = canonical_native_review_json(json.loads(first))
    assert first == second
    assert "Spätere" in first
    assert first.startswith('{"anchors":')


def test_schema_enforces_text_and_array_limits() -> None:
    oversized_text = _review()
    oversized_text["pre_mortem"] = "x" * 3001
    _assert_schema_error(oversized_text)

    oversized_array = _review()
    oversized_array["anchors"] = [
        {
            "anchor_id": f"anchor-{index}",
            "input_fixture": "fixture",
            "expected": "value",
            "tolerance": "exact",
        }
        for index in range(65)
    ]
    _assert_schema_error(oversized_array)
