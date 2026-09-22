from __future__ import annotations

import finding_cleanup

from finding_cleanup import SliceFindingBalance, positive_balance_streak
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
