from __future__ import annotations

import finding_cleanup

from artifact_models import (
    ArtifactRecord,
    Fingerprint,
    FingerprintKind,
    FindingSeverity,
    FindingTransitionPayload,
    PlanPayload,
    Role,
    SliceSpec,
    WorkUnitPayload,
)
from finding_cleanup import (
    SliceFindingBalance,
    derive_slice_finding_balances,
    positive_balance_streak,
)
from workflow_state import WorkUnitKind


def test_removed_cleanup_machine_has_no_public_entrypoint() -> None:
    assert not hasattr(finding_cleanup, "is_finding_cleanup_work_unit")
    assert not hasattr(finding_cleanup, "plan_finding_cleanup")
    assert not hasattr(WorkUnitKind, "CORRECTION")
    assert not hasattr(WorkUnitKind, "FINAL_REVIEW")


def test_positive_balance_streak_uses_only_trailing_open_balance() -> None:
    balances = (
        SliceFindingBalance(1, opened=2, closed=2),
        SliceFindingBalance(2, opened=2, closed=1),
        SliceFindingBalance(3, opened=3, closed=1),
    )

    assert positive_balance_streak(balances) == 2


def test_positive_balance_streak_stops_at_latest_nonpositive_slice() -> None:
    balances = (
        SliceFindingBalance(1, opened=3, closed=1),
        SliceFindingBalance(2, opened=1, closed=1),
    )

    assert positive_balance_streak(balances) == 0


def test_partial_and_fixed_are_distinct_in_record_derived_balance() -> None:
    run_id = "finding-balance"
    fingerprint = "f" * 64
    records: list[ArtifactRecord] = []

    def append(payload: object, logical_id: str) -> None:
        records.append(
            ArtifactRecord.create(
                run_id=run_id,
                logical_id=logical_id,
                revision=1,
                fingerprint=Fingerprint(
                    FingerprintKind.IMPLEMENTATION, fingerprint
                ),
                predecessor_ids=(records[-1].record_id,) if records else (),
                created_at=f"2026-09-19T10:00:{len(records):02d}+00:00",
                idempotency_key=f"balance:{len(records)}",
                payload=payload,  # type: ignore[arg-type]
            )
        )

    append(
        PlanPayload(
            "docs/plan.md",
            "a" * 40,
            (SliceSpec("1", "Repair findings", ("src/fix.py",)),),
        ),
        "plan",
    )
    append(WorkUnitPayload("1", 1, ("src/fix.py",)), "work-unit-1")
    for finding_id in ("C-01", "C-02"):
        append(
            FindingTransitionPayload(
                finding_id=finding_id,
                reporter=Role.CLAUDE,
                actor=Role.CLAUDE,
                action="opened",
                severity=FindingSeverity.BLOCKER,
                finding_status="open",
                rationale="Repair the defect.",
                work_unit_id="1",
                summary="Repair the defect.",
                acceptance_test="The focused regression passes.",
                origin_slice_id="1",
                origin_round_number=1,
            ),
            f"finding-{finding_id}",
        )
    append(
        FindingTransitionPayload(
            finding_id="C-01",
            reporter=Role.CLAUDE,
            actor=Role.CLAUDE,
            action="status_changed",
            severity=FindingSeverity.BLOCKER,
            finding_status="open",
            rationale="One path remains broken.",
            work_unit_id="1",
            closure_kind="partial",
            closure_evidence="Two paths now pass.",
            remaining_work="Repair the third path.",
        ),
        "finding-C-01-partial",
    )
    append(
        FindingTransitionPayload(
            finding_id="C-02",
            reporter=Role.CLAUDE,
            actor=Role.CLAUDE,
            action="status_changed",
            severity=FindingSeverity.BLOCKER,
            finding_status="closed",
            rationale="The regression passes.",
            work_unit_id="1",
            closure_kind="fixed",
        ),
        "finding-C-02-fixed",
    )

    balance = derive_slice_finding_balances(records)[0]

    assert balance.opened == 2
    assert balance.closed == 1
    assert balance.locally_fixed == 1
    assert balance.partially_fixed == 1
    assert balance.rejected == 0
