"""Inventory today's v2 definitions; enforcement begins with the v3 cutover."""

from __future__ import annotations

import json
from pathlib import Path

from artifact_models import _SCHEMA_PATH as ARTIFACT_SCHEMA_PATH
from contracts import AgentRole, ApprovalMarker, ImplementerStepContract, ReadinessMarker
from native_implementer_contract import (
    NativeImplementerContext,
    NativeImplementerRequestKind,
    SCHEMA_PATH as IMPLEMENTER_RESULT_SCHEMA_PATH,
    native_implementer_provider_response_schema,
)
from native_implementer_request import REQUEST_SCHEMA_PATH as IMPLEMENTER_REQUEST_SCHEMA_PATH
from native_review_contract import (
    NativeReviewContext,
    SCHEMA_PATH as REVIEW_RESULT_SCHEMA_PATH,
    native_review_provider_response_schema,
)
from native_review_request import REQUEST_SCHEMA_PATH as REVIEW_REQUEST_SCHEMA_PATH
from schema_patterns import schema_pattern_violations


ROOT = Path(__file__).resolve().parents[1]
V2_SCHEMAS = tuple(
    path.name for path in (
        IMPLEMENTER_REQUEST_SCHEMA_PATH,
        IMPLEMENTER_RESULT_SCHEMA_PATH,
        REVIEW_REQUEST_SCHEMA_PATH,
        REVIEW_RESULT_SCHEMA_PATH,
        ARTIFACT_SCHEMA_PATH,
    )
)


def _writer_documents() -> dict[str, dict]:
    documents: dict[str, dict] = {}
    for kind in NativeImplementerRequestKind:
        readiness = (
            ReadinessMarker.PLAN
            if kind is NativeImplementerRequestKind.PLAN
            else ReadinessMarker.IMPLEMENTATION
        )
        context = NativeImplementerContext(
            "inventory", "1", f"implementer_{kind.value}", "a" * 64, kind,
            ImplementerStepContract(
                name="inventory", readiness_marker=readiness, slice_id="01",
                round_number=1, require_slice_plan=kind is NativeImplementerRequestKind.PLAN,
                require_test_files_record=kind is not NativeImplementerRequestKind.PLAN,
            ),
        )
        documents[f"implementer-writer:{kind.value}"] = native_implementer_provider_response_schema(context)
    for marker, operation, slice_id, round_number in (
        (ApprovalMarker.PLAN, "reviewer_plan_review", "PLAN", 1),
        (ApprovalMarker.SLICE, "reviewer_slice_review", "01", 1),
        (ApprovalMarker.SLICE, "reviewer_slice_review", "01", 2),
        (ApprovalMarker.FINAL_REVIEW, "reviewer_final_review", "FINAL", 1),
    ):
        context = NativeReviewContext(
            "inventory", "1", operation, "a" * 64, AgentRole.REVIEWER,
            marker, slice_id, round_number,
        )
        documents[f"reviewer-writer:{marker.value}:round-{round_number}"] = (
            native_review_provider_response_schema(context)
        )
    return documents


def test_inventory_covers_all_active_v2_schemas_and_writer_variants() -> None:
    documents = {
        name: json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
        for name in V2_SCHEMAS
    }
    documents.update(_writer_documents())
    assert len(documents) == 12
    inventories = {name: schema_pattern_violations(doc) for name, doc in documents.items()}
    assert {name for name, issues in inventories.items() if not issues} == {
        f"implementer-writer:{kind.value}" for kind in NativeImplementerRequestKind
    }  # A clean writer is part of the inventory, not a failed coverage check.
    assert {issue.rule for issues in inventories.values() for issue in issues} >= {
        "lookaround-or-flags", "regex-unicode-escape",
        "quantifier-bound-exceeds-portable-limit",
    }
    assert all(
        issue.rule != "length-in-pattern"
        for issues in inventories.values() for issue in issues
    )
    assert all(issue.path.startswith("/") for issues in inventories.values() for issue in issues)
