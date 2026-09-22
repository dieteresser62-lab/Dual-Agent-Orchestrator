from __future__ import annotations

from final_review_preflight import FINAL_REVIEW_OPERATIONS, transition_fingerprint
from workflow_state import WorkflowStep, WorkUnitKind


def test_preflight_dispatch_is_final_review_only() -> None:
    assert FINAL_REVIEW_OPERATIONS == frozenset(
        {WorkflowStep.CLAUDE_FINAL_REVIEW.value}
    )
    assert WorkUnitKind.FINAL_REVIEW.value == "final_review"


def test_transition_fingerprint_binds_every_dispatch_dimension() -> None:
    common = dict(
        provider="claude",
        role="reviewer",
        operation=WorkflowStep.CLAUDE_FINAL_REVIEW.value,
        work_unit_id="1",
        record_head="a" * 64,
        repository_fingerprint="b" * 64,
        input_digest="c" * 64,
        policy_digest="d" * 64,
    )
    baseline = transition_fingerprint(**common)

    for key, replacement in (
        ("operation", "claude_slice_review"),
        ("work_unit_id", "2"),
        ("record_head", "e" * 64),
        ("repository_fingerprint", "f" * 64),
        ("input_digest", "1" * 64),
        ("policy_digest", "2" * 64),
    ):
        changed = dict(common)
        changed[key] = replacement
        assert transition_fingerprint(**changed) != baseline
