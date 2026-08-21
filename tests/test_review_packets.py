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


def _attestation(fingerprint: str = "a" * 64) -> ValidationAttestation:
    return ValidationAttestation(
        "validation-a", fingerprint, ("pytest",),
        (ValidationRecord(ValidationStatus.PASS, "pytest", 0, "ignored output"),),
        "b" * 64, "947 passed",
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

    with pytest.raises(ReviewPacketError, match="fingerprint-bound PASS"):
        build_review_packet(
            purpose="correction", fingerprint="d" * 64, start_fingerprint="c" * 64,
            paths=("src/core.py",), review_diff="delta", plan_text=PLAN, slice_id=2,
            attestation=_attestation(), findings=(),
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
