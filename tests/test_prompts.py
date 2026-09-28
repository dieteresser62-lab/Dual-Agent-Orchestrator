from __future__ import annotations

import hashlib

from prompts import (
    GERMAN_DOCUMENT_LANGUAGE_RULE,
    NATIVE_REVIEWER_SYSTEM_POLICY,
    NATIVE_IMPLEMENTER_SYSTEM_POLICY,
)


def test_native_policies_require_schema_bound_json() -> None:
    for policy in (NATIVE_IMPLEMENTER_SYSTEM_POLICY, NATIVE_REVIEWER_SYSTEM_POLICY):
        assert "JSON" in policy
        assert "schema" in policy
        assert "Markdown" in policy


def test_role_policy_bytes_match_the_starting_head() -> None:
    assert hashlib.sha256(NATIVE_IMPLEMENTER_SYSTEM_POLICY.encode("utf-8")).hexdigest() == (
        "101b8b8f71aca3178e9607c4fe66214ca49675aa85cb1f54f1358050e74a9dda"
    )
    assert hashlib.sha256(NATIVE_REVIEWER_SYSTEM_POLICY.encode("utf-8")).hexdigest() == (
        "a1f002614be324619583ed6058d1f902c203a1b1779f2ae6967df33de0551a6d"
    )


def test_both_roles_receive_the_same_complete_language_rule() -> None:
    for policy in (NATIVE_IMPLEMENTER_SYSTEM_POLICY, NATIVE_REVIEWER_SYSTEM_POLICY):
        assert policy.count(GERMAN_DOCUMENT_LANGUAGE_RULE) == 1
    for required in (
        "jedes Freitextfeld",
        "Arbeitsplan",
        "Slice-Dokumente",
        "Dispositionsbegründungen",
        "Stoppbegründungen",
        "Befunde",
        "Abnahmekriterien",
        "Statusänderungen",
        "Prüfevidenz",
        "Pre-Mortem",
        "Schlüssel und Enumwerte",
        "Kennungen wie R-01",
        "Code, Pfade, Befehle",
        "Commit-Betreffe",
        "wörtliche Zitate",
        "kein Prüfkriterium",
        "Sprache fremder Texte",
    ):
        assert required in GERMAN_DOCUMENT_LANGUAGE_RULE


def test_native_policies_do_not_define_result_marker_grammar() -> None:
    combined = NATIVE_IMPLEMENTER_SYSTEM_POLICY + NATIVE_REVIEWER_SYSTEM_POLICY
    for marker in ("STATUS: DONE", "PLAN_APPROVAL:", "SLICE_APPROVAL:"):
        assert marker not in combined


def test_claude_policy_sets_a_soft_budget_without_weakening_required_content() -> None:
    assert "below 80 percent" in NATIVE_REVIEWER_SYSTEM_POLICY
    assert "maxLength" in NATIVE_REVIEWER_SYSTEM_POLICY
    assert "never omit" in NATIVE_REVIEWER_SYSTEM_POLICY
    for required in ("finding", "disposition", "review evidence", "pre-mortem"):
        assert required in NATIVE_REVIEWER_SYSTEM_POLICY


def test_claude_policy_announces_the_slice_commit_decision_duty() -> None:
    assert "Before a Slice commit" in NATIVE_REVIEWER_SYSTEM_POLICY
    assert "leave it open and deny the review" in NATIVE_REVIEWER_SYSTEM_POLICY
    assert (
        "the orchestrator then records the escalation to BLOCKER"
        in NATIVE_REVIEWER_SYSTEM_POLICY
    )
    assert "not a reviewer-authored output field" in NATIVE_REVIEWER_SYSTEM_POLICY
    assert "slice_commit_decision_finding_ids" in NATIVE_REVIEWER_SYSTEM_POLICY
    assert "request-time part" in NATIVE_REVIEWER_SYSTEM_POLICY
    assert "opened and decided in that same response" in NATIVE_REVIEWER_SYSTEM_POLICY


def test_claude_policy_requires_source_checks_for_risks_and_test_coverage() -> None:
    policy = NATIVE_REVIEWER_SYSTEM_POLICY
    for required in (
        "For plan, Slice, and final full-branch reviews measured against SOURCE",
        "inspect the files and diff actually present in the provided snapshot",
        "If a residual risk is decidable from that source, verify it against the source",
        "when a defect is confirmed, open a Finding instead of recording only a residual-risk note",
        "Use 'not verifiable' only with a concrete reason why the actual provided snapshot cannot decide the claim",
        "after checking its available files and diff",
        "If you claim a test covers an acceptance criterion, identify the assertion that establishes coverage",
        "connect it to the affected repository path; otherwise open a Finding",
    ):
        assert required in policy
