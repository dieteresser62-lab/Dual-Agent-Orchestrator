from __future__ import annotations

import pytest

from contracts import (
    AgentRole,
    ApprovalMarker,
    CodexStepContract,
    ContractValidationError,
    ReadinessMarker,
    StepContract,
    validate_codex_response,
    validate_review_response,
)


@pytest.mark.parametrize(
    "output",
    (
        "PLAN_READY: YES\nSTATUS: DONE",
        "SLICE_PLAN: 2 | gap | src/a.py\nPLAN_READY: YES\nSTATUS: DONE",
        "SLICE_PLAN: 1 | escape | ../a.py\nPLAN_READY: YES\nSTATUS: DONE",
        "SLICE_PLAN: 1 | internal state | .orchestrator/state.json\nPLAN_READY: YES\nSTATUS: DONE",
    ),
)
def test_plan_contract_fails_closed_on_missing_or_invalid_slice_plan(output: str) -> None:
    with pytest.raises(ContractValidationError):
        validate_codex_response(
            output,
            CodexStepContract(
                name="plan",
                readiness_marker=ReadinessMarker.PLAN,
                slice_id="01",
                round_number=1,
                require_slice_plan=True,
            ),
        )


@pytest.mark.parametrize(
    "legacy",
    ("PHASE1_APPROVAL: YES", "PHASE2_APPROVAL: YES", "CODEX_APPROVAL: YES", "OPEN_FINDINGS: NONE"),
)
def test_state_v3_rejects_every_legacy_marker(legacy: str) -> None:
    with pytest.raises(ContractValidationError, match="rejects"):
        validate_codex_response(
            f"{legacy}\nPLAN_READY: YES\nSTATUS: DONE",
            CodexStepContract(
                name="plan", readiness_marker=ReadinessMarker.PLAN,
                slice_id="01", round_number=1,
            ),
        )


def test_reviewer_cannot_claim_validation_result() -> None:
    with pytest.raises(ContractValidationError, match="cannot emit VALIDATION_RESULT"):
        validate_review_response(
            "\n".join(
                (
                    "REVIEWER: claude",
                    "VALIDATION_RESULT: PASS | pytest | 0",
                    "TEST_FILES_TOUCHED: NONE",
                    "REVIEW_EVIDENCE: five dimensions | drift | provider change",
                    "PRE_MORTEM: stale evidence",
                    "PLAN_APPROVAL: YES",
                    "STATUS: DONE",
                )
            ),
            StepContract(
                name="plan", reviewer=AgentRole.CLAUDE,
                approval_marker=ApprovalMarker.PLAN, slice_id="01", round_number=1,
            ),
        )
