from __future__ import annotations

import ast
from pathlib import Path

from contracts import AgentRole, ApprovalMarker
from finding_reducer import REVIEW_OPENING_RESPONSIBILITY_KINDS
from native_review_contract import NativeReviewContext
from native_review_request import _review_context_request_projection
from prompts import NATIVE_CLAUDE_SYSTEM_POLICY
from workflow_state import WorkflowStep


ROOT = Path(__file__).resolve().parents[1]


def _reachable_review_steps(source: str | None = None) -> frozenset[str]:
    """Read the engine dispatch inventory instead of guessing from names."""

    workflow_path = ROOT / "src" / "workflow.py"
    tree = ast.parse(
        workflow_path.read_text(encoding="utf-8") if source is None else source,
        filename=str(workflow_path),
    )
    matches: list[frozenset[str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        comparison = node.test
        if not (
            isinstance(comparison.left, ast.Name)
            and comparison.left.id == "step"
            and len(comparison.ops) == 1
            and isinstance(comparison.ops[0], ast.In)
            and len(comparison.comparators) == 1
            and isinstance(comparison.comparators[0], (ast.Tuple, ast.Set))
        ):
            continue
        calls_review = any(
            isinstance(candidate, ast.Call)
            and isinstance(candidate.func, ast.Attribute)
            and candidate.func.attr == "_run_review"
            for statement in node.body
            for candidate in ast.walk(statement)
        )
        if not calls_review:
            continue
        members = comparison.comparators[0].elts
        names = tuple(
            member.attr
            for member in members
            if isinstance(member, ast.Attribute)
            and isinstance(member.value, ast.Name)
            and member.value.id == "WorkflowStep"
        )
        assert len(names) == len(members)
        matches.append(frozenset(WorkflowStep[name].value for name in names))
    assert len(matches) == 1, "expected one explicit native review dispatch inventory"
    return matches[0]


def _coverage_gaps(
    reachable: frozenset[str],
    reducer_rules: dict[str, str],
    policy: str,
) -> tuple[frozenset[str], frozenset[str]]:
    missing_reducer = reachable - reducer_rules.keys()
    missing_policy = frozenset(
        step
        for step in reachable & reducer_rules.keys()
        if f"{reducer_rules[step]} in {step}" not in policy
    )
    return frozenset(missing_reducer), missing_policy


def _context(operation: str) -> NativeReviewContext:
    marker = {
        "claude_plan_review": ApprovalMarker.PLAN,
        "claude_slice_review": ApprovalMarker.SLICE,
        "claude_branch_discovery": ApprovalMarker.BRANCH_DISCOVERY,
    }[operation]
    return NativeReviewContext(
        run_id="responsibility-coverage",
        work_unit_id="1",
        operation=operation,
        diff_fingerprint="a" * 64,
        reviewer=AgentRole.CLAUDE,
        approval_marker=marker,
        slice_id="1",
        round_number=1,
    )


def test_every_reachable_review_has_a_reducer_rule_and_both_announcements() -> None:
    reachable = _reachable_review_steps()
    rules = {
        step: kind.value
        for step, kind in REVIEW_OPENING_RESPONSIBILITY_KINDS.items()
    }

    assert reachable == rules.keys()
    assert _coverage_gaps(reachable, rules, NATIVE_CLAUDE_SYSTEM_POLICY) == (
        frozenset(),
        frozenset(),
    )
    for step in sorted(reachable):
        request = _review_context_request_projection(_context(step))
        assert request["review_contract"]["opening_responsibility_kind"] == rules[step]


def test_coverage_detects_a_new_step_without_a_rule_or_without_policy() -> None:
    existing_rules = {
        step: kind.value
        for step, kind in REVIEW_OPENING_RESPONSIBILITY_KINDS.items()
    }
    reachable = frozenset((*existing_rules, "claude_future_review"))

    missing_rule, missing_policy = _coverage_gaps(
        reachable, existing_rules, NATIVE_CLAUDE_SYSTEM_POLICY
    )
    assert missing_rule == frozenset({"claude_future_review"})
    assert missing_policy == frozenset()

    future_rules = {**existing_rules, "claude_future_review": "PLAN_REVISION"}
    missing_rule, missing_policy = _coverage_gaps(
        reachable, future_rules, NATIVE_CLAUDE_SYSTEM_POLICY
    )
    assert missing_rule == frozenset()
    assert missing_policy == frozenset({"claude_future_review"})
