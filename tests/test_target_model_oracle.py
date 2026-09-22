from __future__ import annotations

import json
from pathlib import Path

import pytest
import finding_reducer

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


def test_mutation_u1_automatic_rejection_escalation_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as patch:
        patch.setattr(
            finding_reducer,
            "_escalate_unclosed_rejected_findings",
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
