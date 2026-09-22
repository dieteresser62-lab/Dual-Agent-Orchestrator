from __future__ import annotations

import json
from pathlib import Path
from time import monotonic

import pytest
import finding_reducer
import git_service
import workflow
import workflow_persistence

from contracts import ContractResult
from target_model_oracle import (
    DeviationClass,
    OracleRegression,
    TARGET_MODEL,
    TARGET_MODEL_COMMIT,
    assert_deviation_ratchet,
    frozen_document,
    load_frozen_deviations,
    run_target_model_oracle,
    target_dead_ends,
    target_reachability,
)


ROOT = Path(__file__).resolve().parents[1]
RATCHET = ROOT / "tests" / "fixtures" / "target-model-deviations-v1.json"


def _frozen():
    return load_frozen_deviations(RATCHET)


def _assert_mutation_is_new(
    deviation_id: str,
    deviation_class: DeviationClass,
) -> None:
    report = run_target_model_oracle()
    assert any(
        item.deviation_id == deviation_id
        and item.deviation_class is deviation_class
        for item in report.deviations
    )
    with pytest.raises(OracleRegression, match="deviation set grew or changed"):
        assert_deviation_ratchet(report.deviations, _frozen())


def test_target_model_is_reachable_and_current_delta_does_not_grow() -> None:
    report = run_target_model_oracle()

    assert TARGET_MODEL_COMMIT == "2f76f7f"
    assert len(TARGET_MODEL.situations) == report.situation_count == 43
    assert len(target_reachability()) == report.move_evaluation_count == 473
    assert target_dead_ends() == ()
    assert len(report.skipped_situations) == 6
    assert all("Stop ends before review contract" in reason for _, reason in report.skipped_situations)
    assert_deviation_ratchet(report.deviations, _frozen())

    frozen = json.loads(RATCHET.read_text(encoding="utf-8"))
    assert frozen_document(report) == frozen
    assert frozen["known_exclusion"] == {
        "finding": "103",
        "reason": (
            "Ein in derselben Antwort geoeffnetes Finding ist eine "
            "Mitteilungsluecke, keine Erreichbarkeitsluecke."
        ),
    }
    assert all(item.deviation_id != "103" for item in report.deviations)
    review_outcomes = tuple(
        item for item in report.probe_outcomes if item.audit_accepts is not None
    )
    assert len(review_outcomes) == 6
    assert all(item.audit_accepts for item in review_outcomes)
    canary_31 = next(
        item
        for item in review_outcomes
        if item.probe_id == "denied-new-finding-audit-parity"
    )
    assert canary_31.contract_accepts
    assert canary_31.records_accept
    persistence_outcomes = tuple(
        item
        for item in report.probe_outcomes
        if item.persistence_accepts is not None
    )
    workflow_outcomes = tuple(
        item for item in report.probe_outcomes if item.workflow_accepts is not None
    )
    commit_outcomes = tuple(
        item for item in report.probe_outcomes if item.commit_accepts is not None
    )
    assert len(persistence_outcomes) == len(workflow_outcomes) == 6
    assert all(item.persistence_accepts for item in persistence_outcomes)
    assert all(item.workflow_accepts for item in workflow_outcomes)
    assert len(commit_outcomes) == 7
    boundary = next(
        item
        for item in commit_outcomes
        if item.probe_id == "commit-boundary-open-finding"
    )
    assert not boundary.contract_accepts
    assert boundary.records_accept
    assert boundary.commit_accepts
    assert [
        (item.difference_id, item.probe_ids)
        for item in report.intentional_differences
    ] == [
        (
            "review-approval-vs-commit-blocker-boundary",
            ("commit-boundary-open-finding",),
        ),
        (
            "review-denial-vs-commit-authorization",
            (
                "automatic-rejection-escalation",
                "denied-new-finding-audit-parity",
                "retain-blocker",
            ),
        ),
    ]


def test_target_model_oracle_stays_below_two_minutes() -> None:
    started = monotonic()
    run_target_model_oracle()
    assert monotonic() - started < 120


def test_mutation_u1_automatic_rejection_escalation_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as patch:
        patch.setattr(
            finding_reducer,
            "_escalate_unclosed_findings",
            lambda findings, *, reviewer: dict(findings),
        )
        _assert_mutation_is_new(
            "automatic-rejection-escalation", DeviationClass.FEHLEND
        )


def test_mutation_u1_incomplete_implementer_dispositions_are_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as patch:
        patch.setattr(
            finding_reducer,
            "_missing_finding_response_ids",
            lambda open_ids, response_ids: (),
        )
        _assert_mutation_is_new(
            "mandatory-implementer-disposition", DeviationClass.FEHLEND
        )


def test_mutation_u7_audit_requires_an_already_escalated_blocker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as patch:
        patch.setattr(
            ContractResult,
            "own_open_findings",
            property(lambda result: result.own_open_blockers),
        )
        _assert_mutation_is_new(
            "denied-new-finding-audit-parity", DeviationClass.UNEINIG
        )


def test_mutation_u10_git_commit_rejects_an_open_ordinary_finding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = ROOT / "src" / "git_service.py"
    source_before = source_path.read_bytes()
    original = git_service._validate_authorization

    def reject_every_open_finding(authorization, current_fingerprint):  # type: ignore[no-untyped-def]
        if authorization.claude_review.own_open_findings:
            raise git_service.GitTransactionError(
                "mutation rejects every open Finding at the commit boundary"
            )
        return original(authorization, current_fingerprint)

    with monkeypatch.context() as patch:
        patch.setattr(
            git_service,
            "_validate_authorization",
            reject_every_open_finding,
        )
        _assert_mutation_is_new(
            "commit-boundary-open-finding", DeviationClass.UNEINIG
        )
    assert source_path.read_bytes() == source_before


def test_mutation_u10_persistence_omits_reviewer_transitions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = ROOT / "src" / "workflow_persistence.py"
    source_before = source_path.read_bytes()

    with monkeypatch.context() as patch:
        patch.setattr(
            workflow_persistence.WorkflowPersistence,
            "_persist_review_finding_transitions",
            lambda *_args, **_kwargs: None,
        )
        _assert_mutation_is_new(
            "close-implemented-finding", DeviationClass.UNEINIG
        )
    assert source_path.read_bytes() == source_before


def test_mutation_u10_workflow_dispatches_a_denial_to_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = ROOT / "src" / "workflow.py"
    source_before = source_path.read_bytes()
    original = workflow.WorkflowEngine._apply_review_result

    def dispatch_denial_to_commit(self, **kwargs):  # type: ignore[no-untyped-def]
        state, history = original(self, **kwargs)
        if kwargs["result"].approval is False:
            state = state.with_current_step(workflow.WorkflowStep.SLICE_COMMIT)
        return state, history

    with monkeypatch.context() as patch:
        patch.setattr(
            workflow.WorkflowEngine,
            "_apply_review_result",
            dispatch_denial_to_commit,
        )
        _assert_mutation_is_new(
            "denied-new-finding-audit-parity", DeviationClass.UNEINIG
        )
    assert source_path.read_bytes() == source_before
