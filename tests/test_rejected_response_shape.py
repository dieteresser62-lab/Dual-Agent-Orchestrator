from __future__ import annotations

import json

from rejected_response_shape import (
    extract_rejected_native_response_shape,
    rejected_native_response_shape_document,
    rejected_native_response_shape_from_document,
)


def _rejected_review_document() -> dict[str, object]:
    return {
        "schema_version": "native-agent-review-result-v2",
        "result_type": "review_result",
        "request_id": "native-review-request-" + "a" * 64,
        "reviewer": "claude",
        "decision": "approved",
        "new_findings": [
            {
                "finding_id": "C-09",
                "finding_class": "BLOCKER",
                "summary": "PROVIDER SUMMARY MUST NOT SURVIVE",
            }
        ],
        "finding_dispositions": [
            {
                "finding_id": "C-03",
                "decision": "accepted",
                "rationale": "PROVIDER DISPOSITION RATIONALE MUST NOT SURVIVE",
            }
        ],
        "status_changes": [
            {
                "finding_id": "C-01",
                "status": "CLOSED",
                "rationale": "PROVIDER STATUS RATIONALE MUST NOT SURVIVE",
                "closure": {
                    "kind": "rejected",
                    "rejection_reason": "no_defect",
                    "evidence": "PROVIDER EVIDENCE MUST NOT SURVIVE",
                },
            }
        ],
        "anchors": [],
        "review_evidence": {
            "dimensions": "PROVIDER DIMENSIONS MUST NOT SURVIVE",
            "largest_residual_risk": "PROVIDER RISK MUST NOT SURVIVE",
            "break_condition": "PROVIDER BREAK CONDITION MUST NOT SURVIVE",
        },
        "pre_mortem": "PROVIDER PRE-MORTEM MUST NOT SURVIVE",
        "provider_secret_field": "PROVIDER SECRET MUST NOT SURVIVE",
    }


def test_rejected_review_shape_distinguishes_dispositions_and_statuses() -> None:
    shape = extract_rejected_native_response_shape(_rejected_review_document())

    assert shape is not None
    assert shape.result_type == "review_result"
    assert shape.release_decision == "approved"
    assert shape.unknown_field_count == 1
    assert shape.finding_dispositions[0].finding_id == "C-03"
    assert shape.finding_dispositions[0].decision == "accepted"
    assert shape.status_changes[0].finding_id == "C-01"
    assert shape.status_changes[0].status == "CLOSED"
    assert shape.status_changes[0].closure_kind == "rejected"
    assert shape.status_changes[0].rejection_reason == "no_defect"


def test_rejected_review_shape_roundtrip_contains_no_provider_prose_or_unknown_key() -> None:
    shape = extract_rejected_native_response_shape(_rejected_review_document())
    assert shape is not None

    document = rejected_native_response_shape_document(shape)
    encoded = json.dumps(document, sort_keys=True)
    for forbidden in (
        "PROVIDER SUMMARY MUST NOT SURVIVE",
        "PROVIDER DISPOSITION RATIONALE MUST NOT SURVIVE",
        "PROVIDER STATUS RATIONALE MUST NOT SURVIVE",
        "PROVIDER EVIDENCE MUST NOT SURVIVE",
        "PROVIDER PLAN RATIONALE MUST NOT SURVIVE",
        "PROVIDER DIMENSIONS MUST NOT SURVIVE",
        "PROVIDER RISK MUST NOT SURVIVE",
        "PROVIDER BREAK CONDITION MUST NOT SURVIVE",
        "PROVIDER PRE-MORTEM MUST NOT SURVIVE",
        "PROVIDER SECRET MUST NOT SURVIVE",
        "provider_secret_field",
    ):
        assert forbidden not in encoded
    assert rejected_native_response_shape_from_document(document) == shape


def test_rejected_implementer_shape_keeps_closed_disposition_only() -> None:
    shape = extract_rejected_native_response_shape(
        {
            "schema_version": "native-agent-codex-result-v2",
            "result_type": "implementation_result",
            "request_id": "native-codex-request-" + "d" * 64,
            "ready": True,
            "test_files": ["PROVIDER/PATH/MUST/NOT/SURVIVE"],
            "finding_dispositions": [
                {
                    "finding_id": "C-01",
                    "decision": "accepted",
                    "rationale": "PROVIDER RATIONALE MUST NOT SURVIVE",
                }
            ],
        }
    )

    assert shape is not None
    assert shape.release_decision == "ready"
    disposition = shape.finding_dispositions[0]
    assert disposition.finding_id == "C-01"
    assert disposition.decision == "accepted"
    encoded = json.dumps(rejected_native_response_shape_document(shape))
    assert "PROVIDER/PATH/MUST/NOT/SURVIVE" not in encoded
    assert "PROVIDER RATIONALE MUST NOT SURVIVE" not in encoded
