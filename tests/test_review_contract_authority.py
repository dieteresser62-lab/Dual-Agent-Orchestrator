from __future__ import annotations

import pytest

from artifact_bridge import ArtifactBridge
from artifact_models import (
    ArtifactRecord,
    CommandSpec,
    DiagnosticPayload,
    FindingSeverity,
    FindingTransitionPayload,
    FingerprintKind,
    RecordType,
    ReviewAnchor,
    ReviewEvidencePayload,
    ReviewPayload,
    ReviewStopRequestPayload,
    Role,
    RoleProfilePayload,
    RunIdentityPayload,
    RunProfilePayload,
    ValidationAttestationPayload,
    ValidationResult,
    WorkUnitPayload,
)
from artifact_replay import (
    ArtifactReplayError,
    ReplayDiagnosticCode,
    project_latest_review,
    project_review_contracts,
    replay_artifacts,
)
from artifact_store import ArtifactStore
from contracts import AgentRole, FindingClass
from content_authority_support import (
    append_provider_decision_authority,
    append_validation_authority,
)


FINGERPRINT = "d" * 64


def _bridge(tmp_path, run_id: str) -> ArtifactBridge:  # type: ignore[no-untyped-def]
    bridge = ArtifactBridge(ArtifactStore(tmp_path, run_id))
    bridge.append(
        RunIdentityPayload("task.md", "feature/review", "b" * 40, "IMPLEMENT", None),
        logical_id="run-identity",
        idempotency_key="run-identity",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    bridge.append(
        RunProfilePayload(
            RoleProfilePayload("implementer-model", "medium"),
            RoleProfilePayload("reviewer-model", "high"),
        ),
        logical_id="run-profile",
        idempotency_key="run-profile",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    return bridge


def _attestation(bridge: ArtifactBridge, suffix: str = "1"):
    return append_validation_authority(
        bridge,
        ValidationAttestationPayload(
            (
                ValidationResult(
                    CommandSpec("pytest", ("python3", "-m", "pytest", "tests/")),
                    "pass",
                    0,
                    "0" * 64,
                ),
            ),
            Role.ORCHESTRATOR,
            "0" * 64,
            "ar1-" + "0" * 64,
        ),
        logical_id=f"validation-r7-{suffix}",
        idempotency_key=f"validation-r7-{suffix}",
        fingerprint_sha256=FINGERPRINT,
    )


def test_review_contract_projects_every_r7_fact_without_state_or_aggregate(
    tmp_path,
) -> None:
    bridge = _bridge(tmp_path, "review-authority")
    bridge.append(
        WorkUnitPayload("7", 3, ("src/review.py",)),
        logical_id="work-unit-7",
        idempotency_key="work-unit-7-round-3",
        fingerprint_sha256=FINGERPRINT,
    )
    attestation = _attestation(bridge)
    bridge.append(
        FindingTransitionPayload(
            finding_id="C-01",
            reporter=Role.CLAUDE,
            actor=Role.CLAUDE,
            action="opened",
            severity=FindingSeverity.OBSERVATION,
            finding_status="open",
            rationale="Cross-cutting follow-up remains visible.",
            work_unit_id="7",
            summary="Retain the cross-cutting review note.",
            acceptance_test="The next Slice keeps C-01 visible.",
            origin_slice_id="7",
            origin_round_number=3,
        ),
        logical_id="finding-C-01",
        idempotency_key="finding-C-01-opened",
        fingerprint_sha256=FINGERPRINT,
    )
    review = append_provider_decision_authority(
        bridge,
        ReviewPayload(
            reviewer=Role.CLAUDE,
            work_unit_id="7",
            verdict="approved",
            finding_ids=("C-01",),
            evidence=None,
            transport_schema="native-claude-review-v2",
            request_id="native-review-request-" + "a" * 64,
            response_sha256="b" * 64,
            review_evidence=ReviewEvidencePayload(
                "correctness, contracts, failure paths, and resume",
                "a future writer could omit a child binding",
                "replay accepts a review with one R7 component missing",
            ),
            test_files=("tests/test_review_contract_authority.py",),
            pre_mortem="A partial append could separate the review from its bindings.",
        ),
        logical_id="review-claude-7-3",
        idempotency_key="review-claude-7-3",
        fingerprint_sha256=FINGERPRINT,
        operation="claude_slice_review",
        anchors=(
            ReviewAnchor(
                "anchor-r7",
                "review contract assignment",
                "structured review fixture",
                "all five facts replay exactly",
                "byte-for-byte semantic equality",
            ),
        ),
    )

    chain = bridge.store.load_chain()
    replay = replay_artifacts(
        chain,
        bridge.store.run_id,
        require_content_authority=True,
        require_review_authority=True,
    )
    projected = project_review_contracts(replay, bridge.store.read_blob)

    assert len(projected) == 1
    contract = projected[0]
    assert contract.record_id == review.record_id
    assert contract.work_unit_id == "7"
    assert contract.round_number == 3
    assert contract.result.reviewer is AgentRole.CLAUDE
    assert contract.result.approval is True
    assert contract.result.test_files == ("tests/test_review_contract_authority.py",)
    assert contract.result.pre_mortem == (
        "A partial append could separate the review from its bindings."
    )
    assert contract.result.anchors[0].anchor_id == "anchor-r7"
    assert contract.result.findings[0].finding_class is FindingClass.OBSERVATION
    assert contract.result.validation is not None
    assert contract.result.validation.attestation_id == attestation.logical_id
    assert contract.result.validation.passed
    assert project_latest_review(replay, bridge.store.read_blob, "7") == contract.result
    assert project_latest_review(replay, bridge.store.read_blob, "8") is None
    assert all(record.record_type.value != "latest_claude_review" for record in chain)


def test_review_contract_stop_request_and_missing_component_are_fail_closed(
    tmp_path,
) -> None:
    bridge = _bridge(tmp_path, "review-stop-authority")
    _attestation(bridge, "stop")
    review = append_provider_decision_authority(
        bridge,
        ReviewPayload(
            reviewer=Role.CLAUDE,
            work_unit_id="8",
            verdict="stop",
            finding_ids=(),
            evidence=None,
            transport_schema="native-claude-review-v2",
            request_id="native-review-request-" + "c" * 64,
            response_sha256="e" * 64,
            stop_request=ReviewStopRequestPayload(
                "UNEXPECTED-PATH",
                "The requested repair crosses the declared scope.",
                ("src/outside.py",),
            ),
        ),
        logical_id="review-claude-8-1",
        idempotency_key="review-claude-8-1",
        fingerprint_sha256=FINGERPRINT,
        operation="claude_slice_review",
    )
    chain = bridge.store.load_chain()
    replay = replay_artifacts(
        chain,
        bridge.store.run_id,
        require_content_authority=True,
        require_review_authority=True,
    )
    result = project_latest_review(replay, bridge.store.read_blob, "8")
    assert result is not None and result.stopped
    assert result.stop_request is not None
    assert result.stop_request.remediation_paths == ("src/outside.py",)

    review_position = next(
        index for index, record in enumerate(chain) if record.record_id == review.record_id
    )
    with pytest.raises(ArtifactReplayError) as raised:
        replay_artifacts(
            chain[: review_position + 1],
            bridge.store.run_id,
            require_content_authority=True,
            require_review_authority=True,
        )
    assert raised.value.code is ReplayDiagnosticCode.RECORD_MISSING

    pending_review = replay_artifacts(
        chain[: review_position + 1],
        bridge.store.run_id,
        require_content_authority=True,
        require_review_authority=True,
        allow_incomplete_review_tail=True,
    )
    assert pending_review.pending_review_record_id == review.record_id
    assert project_review_contracts(pending_review, bridge.store.read_blob) == ()

    anchor_position = next(
        index
        for index, record in enumerate(chain)
        if record.record_type is RecordType.REVIEW_ANCHOR
        and record.payload.review_record_id == review.record_id
    )
    pending_after_anchor = replay_artifacts(
        chain[: anchor_position + 1],
        bridge.store.run_id,
        require_content_authority=True,
        require_review_authority=True,
        allow_incomplete_review_tail=True,
    )
    assert pending_after_anchor.pending_review_record_id == review.record_id

    chain_middle = ArtifactRecord.create(
        run_id=bridge.store.run_id,
        logical_id="post-review-diagnostic",
        revision=1,
        fingerprint=review.fingerprint,
        predecessor_ids=(review.record_id,),
        created_at="2026-08-31T12:00:00+00:00",
        idempotency_key="post-review-diagnostic",
        payload=DiagnosticPayload(
            Role.CLAUDE,
            "8",
            1,
            "0" * 64,
            "simulated activity after an incomplete review",
        ),
    )
    with pytest.raises(ArtifactReplayError) as middle_error:
        replay_artifacts(
            (*chain[: review_position + 1], chain_middle),
            bridge.store.run_id,
            require_content_authority=True,
            require_review_authority=True,
            allow_incomplete_review_tail=True,
        )
    assert middle_error.value.code is ReplayDiagnosticCode.RECORD_MISSING
    assert RecordType.REVIEW_ANCHOR in {record.record_type for record in chain}
    assert RecordType.REVIEW_VALIDATION_BINDING in {
        record.record_type for record in chain
    }
