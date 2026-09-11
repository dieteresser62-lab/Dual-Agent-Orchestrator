from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from contracts import (
    AgentRole, FindingClass, FindingOrigin, FindingRecord, FindingResponse,
    FindingResponseDecision, FindingStatus, ValidationAttestation,
    ValidationRecord, ValidationStatus,
)
from dry_run_scenarios import (
    ScriptedWorkflowDriver,
    build_s5_long_run_scenario,
    build_s5_plan_only_scenario,
)
from review_packets import ReviewPacket, ReviewPacketError, build_review_packet, extract_slice_requirements
from workflow_state import WorkUnitKind


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


def _diff(path: str = "src/core.py", content: str = "+line", context: str = "") -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        "index 1111111..2222222 100644\n"
        f"--- a/{path}\n+++ b/{path}\n"
        f"@@ -1 +1 @@{context}\n-old\n{content}\n"
    )


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
        "C-02", FindingClass.BLOCKER, FindingStatus.CLOSED,
        "old defect", "old acceptance",
        FindingOrigin("02", 1, AgentRole.CLAUDE),
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


def test_slice_packet_is_canonical_compact_and_binds_diff_coverage() -> None:
    diff = _diff()
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
    assert "long response excluded" not in first.text
    assert "implementation detail" not in first.text
    assert payload["open_findings"][0]["id"] == "C-01"
    assert payload["closure_references"][0]["summary"] == "fixed by deterministic delta"
    assert "ignored output" not in first.text
    assert payload["schema"] == "review-packet-v2"
    assert payload["manifest"]["diff_coverage"][0]["path"] == "src/core.py"
    assert first.manifest.diff_coverage_digest == payload["manifest"]["diff_coverage_digest"]
    assert ReviewPacket.restore(first.canonical_bytes, first.digest) == first


def test_correction_packet_selects_only_affected_findings_and_binds_fingerprint() -> None:
    packet = build_review_packet(
        purpose="correction", fingerprint="a" * 64, start_fingerprint="c" * 64,
        paths=("src/core.py",), review_diff=_diff(content="+corrected"), plan_text=PLAN,
        slice_id=2, attestation=_attestation(), findings=_findings(),
        affected_finding_ids=("C-01",),
    )
    payload = json.loads(packet.canonical_bytes)

    assert payload["purpose"] == "correction"
    assert payload["start_fingerprint"] == "c" * 64
    assert [item["id"] for item in payload["open_findings"]] == ["C-01"]
    assert payload["closure_references"] == []


def test_s5_correction_diff_starts_with_marker_and_builds_review_packet() -> None:
    scenario = build_s5_long_run_scenario()
    generated_changes = (
        *build_s5_plan_only_scenario().changes,
        *scenario.changes,
    )
    for change in generated_changes:
        normalized = change.full_diff.replace("\r\n", "\n").replace("\r", "\n")
        lines = normalized.splitlines(keepends=True)
        starts = [
            index
            for index, line in enumerate(lines)
            if line.rstrip("\n").startswith("diff --git ")
        ]
        assert starts and starts[0] == 0

    correction = next(
        item
        for item in scenario.changes
        if (item.work_unit_id, item.round_number) == (3, 3)
    )
    driver = ScriptedWorkflowDriver(scenario)
    driver.bind_work_unit(
        SimpleNamespace(
            current_work_unit_id=3,
            current_work_unit=SimpleNamespace(
                round_number=3,
                kind=WorkUnitKind.SLICE,
            ),
        )
    )
    review_diff = driver.collect_correction_delta("3" * 64, correction.fingerprint)

    packet = build_review_packet(
        purpose="correction",
        fingerprint=correction.fingerprint,
        start_fingerprint="3" * 64,
        paths=correction.paths,
        review_diff=review_diff,
        plan_text=PLAN,
        slice_id=2,
        attestation=_attestation(correction.fingerprint),
        findings=_findings(),
        affected_finding_ids=("C-01",),
    )

    assert json.loads(packet.canonical_bytes)["diff"] == review_diff


def test_correction_packet_orders_affected_findings_naturally() -> None:
    base = _findings()[0]
    findings = tuple(
        replace(base, finding_id=finding_id)
        for finding_id in ("C-62", "C-101", "C-1000")
    )
    packet = build_review_packet(
        purpose="correction",
        fingerprint="a" * 64,
        start_fingerprint="c" * 64,
        paths=("src/core.py",),
        review_diff=_diff(content="+corrected"),
        plan_text=PLAN,
        slice_id=3,
        attestation=_attestation(),
        findings=findings,
        affected_finding_ids=("C-1000", "C-62", "C-101"),
    )

    payload = json.loads(packet.canonical_bytes)
    assert payload["slice"]["goal"] == (
        "Resolve reviewer findings C-62, C-101, C-1000"
    )
    assert [item["id"] for item in payload["open_findings"]] == [
        "C-62",
        "C-101",
        "C-1000",
    ]


def test_correction_packet_derives_requirements_when_slice_is_not_in_approved_plan() -> None:
    packet = build_review_packet(
        purpose="correction",
        fingerprint="a" * 64,
        start_fingerprint="c" * 64,
        paths=("src/core.py",),
        review_diff=_diff(content="+corrected"),
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
            paths=("src/core.py",), review_diff=_diff(), plan_text=PLAN, slice_id=2,
            attestation=_attestation(), findings=(),
        )


def test_slice_packet_accepts_complete_red_attestation_for_mandatory_denial() -> None:
    packet = build_review_packet(
        purpose="slice", fingerprint="a" * 64, start_fingerprint="0" * 64,
        paths=("src/core.py",), review_diff=_diff(), plan_text=PLAN, slice_id=2,
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
            paths=("src/core.py",), review_diff=_diff(), plan_text=PLAN, slice_id=2,
            attestation=_attestation(complete=False), findings=(),
        )


def test_correction_packet_rejects_missing_affected_finding_scope() -> None:
    with pytest.raises(ReviewPacketError, match="requires affected findings"):
        build_review_packet(
            purpose="correction", fingerprint="a" * 64, start_fingerprint="c" * 64,
            paths=("src/core.py",), review_diff=_diff(), plan_text=PLAN,
            slice_id=2, attestation=_attestation(), findings=_findings(),
        )


def test_packet_rejects_duplicate_manifest_and_ambiguous_plan_section() -> None:
    with pytest.raises(ReviewPacketError, match="sorted, unique"):
        build_review_packet(
            purpose="slice", fingerprint="a" * 64, start_fingerprint="0" * 64,
            paths=("src/core.py", "src/core.py"), review_diff=_diff(), plan_text=PLAN,
            slice_id=2, attestation=_attestation(), findings=(),
        )
    with pytest.raises(ReviewPacketError, match="exactly one"):
        extract_slice_requirements(PLAN + PLAN, 2)


def test_diff_manifest_is_path_sorted_and_preserves_complete_hunk_headers() -> None:
    alpha = _diff("src/a.py", "+alpha", " function context")
    beta = _diff("src/b.py", "+beta")
    kwargs = dict(
        purpose="slice", fingerprint="a" * 64, start_fingerprint="0" * 64,
        plan_text=PLAN, slice_id=2, attestation=_attestation(), findings=(),
    )
    first = build_review_packet(paths=("src/a.py", "src/b.py"), review_diff=beta + alpha, **kwargs)
    second = build_review_packet(paths=("src/a.py", "src/b.py"), review_diff=alpha + beta, **kwargs)

    assert first.canonical_bytes == second.canonical_bytes
    assert tuple(item.path for item in first.manifest.diff_coverage) == ("src/a.py", "src/b.py")
    assert first.manifest.diff_coverage[0].hunk_headers == ("@@ -1 +1 @@ function context",)
    assert len({item.section_sha256 for item in first.manifest.diff_coverage}) == 2


def test_diff_content_change_changes_section_and_packet_digest() -> None:
    common = dict(
        purpose="slice", fingerprint="a" * 64, start_fingerprint="0" * 64,
        paths=("src/core.py",), plan_text=PLAN, slice_id=2,
        attestation=_attestation(), findings=(),
    )
    first = build_review_packet(review_diff=_diff(content="+one"), **common)
    second = build_review_packet(review_diff=_diff(content="+two"), **common)
    assert first.manifest.diff_coverage[0].section_sha256 != second.manifest.diff_coverage[0].section_sha256
    assert first.digest != second.digest


def test_hunk_content_cannot_impersonate_file_headers_or_diff_sections() -> None:
    diff = _diff(content="+++ markdown heading")
    packet = build_review_packet(
        purpose="slice", fingerprint="a" * 64, start_fingerprint="0" * 64,
        paths=("src/core.py",), review_diff=diff, plan_text=PLAN, slice_id=2,
        attestation=_attestation(), findings=(),
    )
    assert packet.manifest.diff_coverage[0].path == "src/core.py"


def test_internal_markdown_diff_accepts_empty_managed_projection_body() -> None:
    path = "docs/internal/slice-example-01-audit.md"
    diff = (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n+++ b/{path}\n"
        "@@ -1,2 +1,3 @@\n"
        " <!-- audit:findings:begin -->\n"
        " <!-- audit:findings:end -->\n"
        "+authored text\n"
    )

    packet = build_review_packet(
        purpose="slice", fingerprint="a" * 64, start_fingerprint="0" * 64,
        paths=(path,), review_diff=diff, plan_text=PLAN, slice_id=2,
        attestation=_attestation(), findings=(),
    )
    assert packet.manifest.diff_coverage[0].path == path


def test_internal_markdown_diff_rejects_managed_projection_bytes() -> None:
    path = "docs/internal/slice-example-01-audit.md"
    diff = (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n+++ b/{path}\n"
        "@@ -1,3 +1,3 @@\n"
        " <!-- audit:findings:begin -->\n"
        "-old projected output\n"
        "+new projected output\n"
        " <!-- audit:findings:end -->\n"
    )

    with pytest.raises(ReviewPacketError, match="non-semantic managed audit"):
        build_review_packet(
            purpose="slice", fingerprint="a" * 64, start_fingerprint="0" * 64,
            paths=(path,), review_diff=diff, plan_text=PLAN, slice_id=2,
            attestation=_attestation(), findings=(),
        )


@pytest.mark.parametrize(
    ("metadata", "old_header", "new_header", "expected"),
    (
        ("new file mode 100644\n", "--- /dev/null", "+++ b/src/core.py", "added"),
        ("deleted file mode 100644\n", "--- a/src/core.py", "+++ /dev/null", "deleted"),
    ),
)
def test_diff_manifest_classifies_added_and_deleted_files(
    metadata: str, old_header: str, new_header: str, expected: str
) -> None:
    diff = (
        "diff --git a/src/core.py b/src/core.py\n"
        f"{metadata}{old_header}\n{new_header}\n"
        "@@ -0,0 +1 @@\n+line\n"
    )
    packet = build_review_packet(
        purpose="slice", fingerprint="a" * 64, start_fingerprint="0" * 64,
        paths=("src/core.py",), review_diff=diff, plan_text=PLAN, slice_id=2,
        attestation=_attestation(), findings=(),
    )
    assert packet.manifest.diff_coverage[0].change_type == expected


@pytest.mark.parametrize(
    ("diff", "paths"),
    (
        ("not a git diff", ("src/core.py",)),
        (_diff(), ("src/core.py", "src/missing.py")),
        (_diff() + _diff(), ("src/core.py",)),
        (_diff("src/other.py"), ("src/core.py",)),
        (_diff("../unsafe.py"), ("../unsafe.py",)),
        ("diff --git a/src/old.py b/src/new.py\n--- a/src/old.py\n+++ b/src/new.py\n@@ -1 +1 @@\n-a\n+b\n", ("src/new.py",)),
        ("diff --git a/src/core.py b/src/core.py\nrename from src/old.py\nrename to src/core.py\n--- a/src/core.py\n+++ b/src/core.py\n@@ -1 +1 @@\n-a\n+b\n", ("src/core.py",)),
        ("diff --git a/src/core.py b/src/core.py\nBinary files a/src/core.py and b/src/core.py differ\n", ("src/core.py",)),
    ),
)
def test_diff_manifest_rejects_incomplete_ambiguous_or_unsafe_sections(
    diff: str, paths: tuple[str, ...]
) -> None:
    with pytest.raises(ReviewPacketError):
        build_review_packet(
            purpose="slice", fingerprint="a" * 64, start_fingerprint="0" * 64,
            paths=paths, review_diff=diff, plan_text=PLAN, slice_id=2,
            attestation=_attestation(), findings=(),
        )
