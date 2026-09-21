from __future__ import annotations

from prompts import NATIVE_CLAUDE_SYSTEM_POLICY, NATIVE_CODEX_SYSTEM_POLICY


def test_native_policies_require_schema_bound_json() -> None:
    for policy in (NATIVE_CODEX_SYSTEM_POLICY, NATIVE_CLAUDE_SYSTEM_POLICY):
        assert "JSON" in policy
        assert "schema" in policy
        assert "Markdown" in policy


def test_native_policies_do_not_define_result_marker_grammar() -> None:
    combined = NATIVE_CODEX_SYSTEM_POLICY + NATIVE_CLAUDE_SYSTEM_POLICY
    for marker in ("STATUS: DONE", "PLAN_APPROVAL:", "SLICE_APPROVAL:"):
        assert marker not in combined


def test_claude_policy_sets_a_soft_budget_without_weakening_required_content() -> None:
    assert "below 80 percent" in NATIVE_CLAUDE_SYSTEM_POLICY
    assert "maxLength" in NATIVE_CLAUDE_SYSTEM_POLICY
    assert "never omit" in NATIVE_CLAUDE_SYSTEM_POLICY
    for required in ("finding", "disposition", "review evidence", "pre-mortem"):
        assert required in NATIVE_CLAUDE_SYSTEM_POLICY


def test_claude_policy_announces_the_slice_commit_decision_duty() -> None:
    assert "Before a Slice commit" in NATIVE_CLAUDE_SYSTEM_POLICY
    assert "reject it with named evidence" in NATIVE_CLAUDE_SYSTEM_POLICY
    assert "route it to a named later Slice" in NATIVE_CLAUDE_SYSTEM_POLICY
    assert "slice_commit_decision_finding_ids" in NATIVE_CLAUDE_SYSTEM_POLICY
    assert "only the request-time part" in NATIVE_CLAUDE_SYSTEM_POLICY
    assert "opened and decided in that same response" in NATIVE_CLAUDE_SYSTEM_POLICY
    assert "route already implies" in NATIVE_CLAUDE_SYSTEM_POLICY
    assert "do not add a confirming status_changes entry" in NATIVE_CLAUDE_SYSTEM_POLICY


def test_claude_policy_names_each_review_opening_responsibility_kind() -> None:
    for operation, responsibility_kind in (
        ("claude_slice_review", "SLICE"),
        ("claude_plan_review", "PLAN_REVISION"),
        ("claude_branch_discovery", "BRANCH_PLANNING"),
    ):
        assert (
            f"{responsibility_kind} in {operation}"
            in NATIVE_CLAUDE_SYSTEM_POLICY
        )
    assert "opening_responsibility_kind" in NATIVE_CLAUDE_SYSTEM_POLICY
