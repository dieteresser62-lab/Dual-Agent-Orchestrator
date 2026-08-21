from __future__ import annotations

import json

import pytest

from contracts import (
    AgentRole, FindingClass, FindingOrigin, FindingRecord, FindingResponse,
    FindingResponseDecision, FindingStatus, ValidationAttestation,
    ValidationRecord, ValidationStatus,
)
from review_packets import ReviewPacketError, build_review_packet, extract_slice_requirements


PLAN = """# Plan

### Slice 1 - First

**Ziel**

Do something else.

#### \u0041kzeptanzkriterien

- old criterion

### Slice 2 - Minimal packets

**Ziel**

Build one role-neutral packet without audit prose.

#### Integrationsschritte

1. implementation detail that must not be embedded

#### \u0041kzeptanzkriterien

- Diff and criteria are present.
- Audit text and old responses are absent.
  This continuation belongs to the second criterion.

#### Geplante fokussierte Tests

- not an acceptance criterion
"""


def _attestation(
    fingerprint: str = "a" * 64,
    *,
    status: ValidationStatus = ValidationStatus.PASS,
    complete: bool = True,
) -> ValidationAttestation:
    records = (
        (ValidationRecord(status, "pytest", 0 if status is ValidationStatus.PASS else 1, "ignored output"),)
        if complete
        else ()
    )
    return ValidationAttestation(
        "validation-a", fingerprint, ("pytest",),
        records, "b" * 64,
        "947 passed" if status is ValidationStatus.PASS else "validation failed",
    )


def _findings() -> tuple[FindingRecord, ...]:
    open_finding = FindingRecord(
        "C-01", FindingClass.BLOCKER, FindingStatus.OPEN,
        "active defect", "run focused test",
        FindingOrigin("02", 1, AgentRole.CLAUDE),
        responses=(FindingResponse(FindingResponseDecision.ACCEPTED, "long response excluded"),),
    )
    closed = FindingRecord(
        "A-01", FindingClass.BLOCKER, FindingStatus.CLOSED,
        "old defect", "old acceptance",
        FindingOrigin("02", 1, AgentRole.ANTIGRAVITY),
        status_rationale="  fixed by deterministic   delta  ",
    )
    return open_finding, closed


def test_extracts_only_selected_slice_goal_and_acceptance_criteria() -> None:
    goal, criteria = extract_slice_requirements(PLAN, 2)

    assert goal == "Build one role-neutral packet without audit prose."
    assert criteria == (
        "Diff and criteria are present.",
        "Audit text and old responses are absent. This continuation belongs to the second criterion.",
    )


def test_slice_packet_is_canonical_compact_and_filters_non_manifest_diff() -> None:
    diff = (
        "diff --git a/src/core.py b/src/core.py\n+line\n"
        "diff --git a/docs/internal/audit.md b/docs/internal/audit.md\n+managed audit prose\n"
    )
    first = build_review_packet(
        purpose="slice", fingerprint="a" * 64, start_fingerprint="0" * 64,
        paths=("src/core.py",), review_diff=diff, plan_text=PLAN, slice_id=2,
        attestation=_attestation(), findings=_findings(),
    )
    second = build_review_packet(
        purpose="slice", fingerprint="a" * 64, start_fingerprint="0" * 64,
        paths=("src/core.py",), review_diff=diff, plan_text=PLAN, slice_id=2,
        attestation=_attestation(), findings=_findings(),
    )
    payload = json.loads(first.canonical_bytes)

    assert first.canonical_bytes == second.canonical_bytes
    assert first.digest == second.digest
    assert "managed audit prose" not in first.text
    assert "long response excluded" not in first.text
    assert "implementation detail" not in first.text
    assert payload["open_findings"][0]["id"] == "C-01"
    assert payload["closure_references"][0]["summary"] == "fixed by deterministic delta"
    assert "ignored output" not in first.text


def test_correction_packet_selects_only_affected_findings_and_binds_fingerprint() -> None:
    packet = build_review_packet(
        purpose="correction", fingerprint="a" * 64, start_fingerprint="c" * 64,
        paths=("src/core.py",), review_diff="CORRECTION DELTA", plan_text=PLAN,
        slice_id=2, attestation=_attestation(), findings=_findings(),
        affected_finding_ids=("C-01",),
    )
    payload = json.loads(packet.canonical_bytes)

    assert payload["purpose"] == "correction"
    assert payload["start_fingerprint"] == "c" * 64
    assert [item["id"] for item in payload["open_findings"]] == ["C-01"]
    assert payload["closure_references"] == []


def test_correction_packet_derives_requirements_when_slice_is_not_in_approved_plan() -> None:
    packet = build_review_packet(
        purpose="correction",
        fingerprint="a" * 64,
        start_fingerprint="c" * 64,
        paths=("src/core.py",),
        review_diff="CORRECTION DELTA",
        plan_text=PLAN,
        slice_id=3,
        attestation=_attestation(),
        findings=_findings(),
        affected_finding_ids=("C-01",),
    )

    payload = json.loads(packet.canonical_bytes)

    assert payload["slice"] == {
        "id": 3,
        "goal": "Resolve reviewer findings C-01",
        "acceptance_criteria": [
            "C-01: run focused test",
        ],
    }

    with pytest.raises(ReviewPacketError, match="fingerprint-bound complete"):
        build_review_packet(
            purpose="correction", fingerprint="d" * 64, start_fingerprint="c" * 64,
            paths=("src/core.py",), review_diff="delta", plan_text=PLAN, slice_id=2,
            attestation=_attestation(), findings=(),
        )


def test_slice_packet_accepts_complete_red_attestation_for_mandatory_denial() -> None:
    packet = build_review_packet(
        purpose="slice", fingerprint="a" * 64, start_fingerprint="0" * 64,
        paths=("src/core.py",), review_diff="diff", plan_text=PLAN, slice_id=2,
        attestation=_attestation(status=ValidationStatus.FAIL), findings=(),
    )

    assert json.loads(packet.canonical_bytes)["attestation"]["status"] == "FAIL"


def test_extracts_generated_plan_shape_with_title_goal_and_focused_tests() -> None:
    plan = """# Plan

### Slice 1 - Providerattempt core

#### Integrationspunkte und Umsetzung

- Implement the record lifecycle.

**Exakter Änderungspfad**

- `src/artifact_models.py`

#### Fokussierte synthetische Akzeptanztests

- `tests/test_artifact_models.py`: Roundtrip provider attempts.
- Resume retains an open attempt.

#### Stopbedingungen

- Do not expand scope.
"""

    goal, criteria = extract_slice_requirements(plan, 1)

    assert goal == "Providerattempt core"
    assert criteria == (
        "`tests/test_artifact_models.py`: Roundtrip provider attempts.",
        "Resume retains an open attempt.",
    )

def test_slice_packet_rejects_incomplete_attestation() -> None:
    with pytest.raises(ReviewPacketError, match="fingerprint-bound complete"):
        build_review_packet(
            purpose="slice", fingerprint="a" * 64, start_fingerprint="0" * 64,
            paths=("src/core.py",), review_diff="diff", plan_text=PLAN, slice_id=2,
            attestation=_attestation(complete=False), findings=(),
        )


def test_correction_packet_rejects_missing_affected_finding_scope() -> None:
    with pytest.raises(ReviewPacketError, match="requires affected findings"):
        build_review_packet(
            purpose="correction", fingerprint="a" * 64, start_fingerprint="c" * 64,
            paths=("src/core.py",), review_diff="CORRECTION DELTA", plan_text=PLAN,
            slice_id=2, attestation=_attestation(), findings=_findings(),
        )


def test_packet_rejects_duplicate_manifest_and_ambiguous_plan_section() -> None:
    with pytest.raises(ReviewPacketError, match="sorted, unique"):
        build_review_packet(
            purpose="slice", fingerprint="a" * 64, start_fingerprint="0" * 64,
            paths=("src/core.py", "src/core.py"), review_diff="diff", plan_text=PLAN,
            slice_id=2, attestation=_attestation(), findings=(),
        )
    with pytest.raises(ReviewPacketError, match="exactly one"):
        extract_slice_requirements(PLAN + PLAN, 2)
