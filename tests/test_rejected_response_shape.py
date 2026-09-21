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
                "responsibility_proposal": {
                    "responsibility_kind": "BRANCH_PLANNING",
                    "family_id": "family-17",
                    "cycle_number": 2,
                },
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
        "reclassifications": [
            {
                "finding_id": "C-04",
                "finding_class": "OBSERVATION",
                "rationale": "PROVIDER RECLASSIFICATION MUST NOT SURVIVE",
            }
        ],
        "responsibility_routes": [
            {
                "finding_id": "C-01",
                "responsibility": {
                    "responsibility_kind": "SLICE",
                    "target_run_id": "run-22",
                    "approved_plan_commit": "b" * 40,
                    "slice_id": "4",
                },
                "rationale": "PROVIDER CURRENT ROUTE MUST NOT SURVIVE",
            },
            {
                "finding_id": "C-02",
                "responsibility": {
                    "responsibility_kind": "SLICE",
                    "target_run_id": "run-22",
                    "approved_plan_commit": "b" * 40,
                    "slice_id": "5",
                },
                "rationale": "PROVIDER LATER ROUTE MUST NOT SURVIVE",
            },
        ],
        "plan_treatment_decisions": [
            {
                "signature": "c" * 64,
                "decision": "accepted",
                "rationale": "PROVIDER PLAN RATIONALE MUST NOT SURVIVE",
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


def test_rejected_review_shape_distinguishes_routes_dispositions_and_statuses() -> None:
    shape = extract_rejected_native_response_shape(_rejected_review_document())

    assert shape is not None
    assert shape.result_type == "review_result"
    assert shape.release_decision == "approved"
    assert shape.unknown_field_count == 1
    assert [route.finding_id for route in shape.responsibility_routes] == [
        "C-01",
        "C-02",
    ]
    assert [route.target.slice_id for route in shape.responsibility_routes] == [
        "4",
        "5",
    ]
    assert shape.finding_dispositions[0].finding_id == "C-03"
    assert shape.finding_dispositions[0].decision == "accepted"
    assert shape.status_changes[0].finding_id == "C-01"
    assert shape.status_changes[0].status == "CLOSED"
    assert shape.status_changes[0].closure_kind == "rejected"
    assert shape.status_changes[0].rejection_reason == "no_defect"
    assert shape.reclassifications[0].finding_class == "OBSERVATION"


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
        "PROVIDER CURRENT ROUTE MUST NOT SURVIVE",
        "PROVIDER LATER ROUTE MUST NOT SURVIVE",
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


def test_rejected_implementer_shape_keeps_closed_disposition_and_target_only() -> None:
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
                    "responsibility_proposal": {
                        "responsibility_kind": "SLICE",
                        "target_run_id": "run-implementer",
                        "approved_plan_commit": "e" * 40,
                        "slice_id": "6",
                    },
                }
            ],
        }
    )

    assert shape is not None
    assert shape.release_decision == "ready"
    disposition = shape.finding_dispositions[0]
    assert disposition.finding_id == "C-01"
    assert disposition.decision == "accepted"
    assert disposition.responsibility_proposal is not None
    assert disposition.responsibility_proposal.slice_id == "6"
    encoded = json.dumps(rejected_native_response_shape_document(shape))
    assert "PROVIDER/PATH/MUST/NOT/SURVIVE" not in encoded
    assert "PROVIDER RATIONALE MUST NOT SURVIVE" not in encoded
