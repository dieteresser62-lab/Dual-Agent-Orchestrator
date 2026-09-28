from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
from types import TracebackType
from typing import Callable

import pytest

import artifact_replay
from artifact_models import (
    AgentResultPayload,
    ArtifactRecord,
    BindingPayload,
    BlobReference,
    CommandSpec,
    FinalReviewCompletedPayload,
    FinalReviewPreflightPayload,
    Fingerprint,
    FingerprintKind,
    GateDecisionPayload,
    GatePayload,
    GateTransitionPayload,
    InvocationFailurePayload,
    ProviderAttemptPayload,
    ProviderContentPayload,
    ProviderInputComponentPayload,
    ProviderInputMeasurementPayload,
    ProviderUsagePayload,
    QuotaPausePayload,
    RecordType,
    ResumeCheckPayload,
    ReviewAnchorPayload,
    ReviewEvidencePayload,
    ReviewPacketPayload,
    ReviewPayload,
    ReviewValidationBindingPayload,
    Role,
    RunIdentityPayload,
    SideEffectPayload,
    SliceBoundaryPayload,
    ScopeExtensionPathPayload,
    ScopeExtensionPayload,
    TransientRetryPayload,
    ValidationAttestationPayload,
    ValidationContentPayload,
    ValidationOutputContent,
    ValidationResult,
    WorkflowCompletionPayload,
    WorkflowEventPayload,
    WorkflowPolicyPayload,
    WorkflowTransitionPayload,
    WorkUnitPayload,
    provider_text_evidence,
    stable_record_id,
    stable_side_effect_key,
    technical_text_evidence,
)
from artifact_replay import ArtifactReplayError, ReplayDiagnosticCode


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/artifact_replay.py"
BASELINE = ROOT / "tests/fixtures/replay-rejection-corpus-v1.json"
RUN_ID = "b40-rejection-corpus"
FP_A = Fingerprint(FingerprintKind.IMPLEMENTATION, "a" * 64)
FP_B = Fingerprint(FingerprintKind.IMPLEMENTATION, "b" * 64)
REQUEST_ID = "native-implementer-request-" + "c" * 64
RESPONSE_SHA = "d" * 64
VALIDATION_PASSES = (
    "_validate_workflow_transitions_and_events",
    "_validate_invocation_failures_and_retries",
    "_validate_gate_transitions_and_decisions",
    "_index_validation_content",
    "_validate_attestation_content_bindings",
    "_validate_unbound_validation_content",
    "_validate_provider_decision_content",
    "_validate_unbound_provider_content",
    "_validate_scope_extensions",
    "_validate_review_anchors",
    "_validate_review_validation_bindings",
    "_validate_required_review_authority",
    "_validate_review_packet_bindings",
    "_validate_work_unit_revisions",
    "_validate_chain_record_references",
    "_validate_provider_attempt_sequences",
    "_validate_side_effect_sequences",
)


@dataclass(frozen=True, slots=True)
class RejectionInput:
    records: tuple[ArtifactRecord, ...]
    require_content_authority: bool = False
    require_review_authority: bool = False
    allow_incomplete_review_tail: bool = False


def _append(
    records: list[ArtifactRecord],
    payload: object,
    *,
    logical_id: str | None = None,
    revision: int = 1,
    fingerprint: Fingerprint = FP_A,
    idempotency_key: str | None = None,
    predecessor_ids: tuple[str, ...] | None = None,
    run_id: str = RUN_ID,
) -> ArtifactRecord:
    index = len(records) + 1
    logical_id = logical_id or f"b40-{payload.record_type.value}-{index}"
    record = ArtifactRecord.create(
        run_id=run_id,
        logical_id=logical_id,
        revision=revision,
        fingerprint=fingerprint,
        predecessor_ids=(
            predecessor_ids
            if predecessor_ids is not None
            else ((records[-1].record_id,) if records else ())
        ),
        created_at=f"2026-09-03T10:00:{index:02d}+00:00",
        idempotency_key=idempotency_key or f"b40:{logical_id}:{revision}:{index}",
        payload=payload,  # type: ignore[arg-type]
    )
    records.append(record)
    return record


def _transition(
    work_unit_id: str = "1",
    *,
    slice_id: str = "1",
    step: str = "implementer_implementation",
) -> WorkflowTransitionPayload:
    return WorkflowTransitionPayload(
        slice_id, "in_progress", work_unit_id, step, "in_progress"
    )


def _event(
    kind: str,
    referenced: ArtifactRecord,
    *,
    work_unit_id: str | None,
    slice_id: str = "1",
    round_number: int | None = None,
    fingerprint: Fingerprint = FP_A,
    records: list[ArtifactRecord],
) -> ArtifactRecord:
    return _append(
        records,
        WorkflowEventPayload(
            kind, work_unit_id, slice_id, round_number, (referenced.record_id,)
        ),
        fingerprint=fingerprint,
    )


def _agent(work_unit_id: str = "1") -> AgentResultPayload:
    return AgentResultPayload(
        Role.IMPLEMENTER,
        work_unit_id,
        "ready",
        (),
        "native-codex-v3",
        REQUEST_ID,
        RESPONSE_SHA,
    )


def _review(
    work_unit_id: str = "1", *, finding_ids: tuple[str, ...] = ()
) -> ReviewPayload:
    return ReviewPayload(
        Role.REVIEWER,
        work_unit_id,
        "approved",
        finding_ids,
        "checked",
        "native-claude-review-v3",
        "native-review-request-" + "c" * 64,
        RESPONSE_SHA,
    )


def _failure(
    *,
    invocation_id: str = "invocation-1",
    work_unit_id: str = "1",
    step: str = "implementer_implementation",
    slice_id: str = "1",
    failure_kind: str = "quota",
    resume_at: str = "2026-09-03T10:01:00+00:00",
    fingerprint: str | None = "a" * 64,
) -> InvocationFailurePayload:
    provider, provider_sha, provider_bytes = provider_text_evidence("provider")
    technical, technical_sha, technical_bytes = technical_text_evidence("technical")
    return InvocationFailurePayload(
        invocation_id,
        f"invoke:{invocation_id}",
        Role.IMPLEMENTER,
        failure_kind,
        "transient",
        "AGENT-INVOCATION",
        provider,
        provider_sha,
        provider_bytes,
        technical,
        technical_sha,
        technical_bytes,
        "2026-09-03T10:00:00+00:00",
        "2026-09-03T10:00:01+00:00",
        step,
        slice_id,
        work_unit_id,
        2 if failure_kind == "quota" else 3,
        None,
        None,
        None,
        None,
        resume_at,
        0,
        59,
        1,
        True,
        fingerprint,
    )


def _append_failure(
    records: list[ArtifactRecord],
    payload: InvocationFailurePayload,
    *,
    logical_id: str | None = None,
    revision: int = 1,
    fingerprint: Fingerprint = FP_A,
) -> ArtifactRecord:
    invocation_id = payload.invocation_id
    return _append(
        records,
        payload,
        logical_id=logical_id or f"invocation-failure-{invocation_id}",
        revision=revision,
        fingerprint=fingerprint,
        idempotency_key=f"invocation-failure:{invocation_id}",
    )


def _gate_transition(work_unit_id: str = "1") -> GateTransitionPayload:
    return GateTransitionPayload(
        work_unit_id, "clear", "none", None, None, (), None, None, ()
    )


def _validation_parts() -> tuple[
    ValidationOutputContent, ValidationResult
]:
    command = CommandSpec("pytest", ("pytest", "tests/test_b40.py"))
    empty = BlobReference(hashlib.sha256(b"").hexdigest(), 0)
    output = ValidationOutputContent(command, "pass", 0, empty, empty, empty, 0)
    result = ValidationResult(command, "pass", 0, empty.sha256)
    return output, result


def _append_validation_pair(
    records: list[ArtifactRecord],
    *,
    fingerprint: Fingerprint = FP_A,
) -> tuple[ArtifactRecord, ArtifactRecord]:
    logical_id = "validation-1"
    output, result = _validation_parts()
    result_record_id = stable_record_id(
        RUN_ID, RecordType.VALIDATION_ATTESTATION, logical_id, 1
    )
    content = _append(
        records,
        ValidationContentPayload(
            logical_id,
            result_record_id,
            "validation-matrix-v1",
            "e" * 64,
            "B40 validation",
            (output,),
        ),
        logical_id=f"validation-content-{logical_id}",
        fingerprint=fingerprint,
    )
    attestation = _append(
        records,
        ValidationAttestationPayload(
            (result,), Role.ORCHESTRATOR, "e" * 64, content.record_id
        ),
        logical_id=logical_id,
        fingerprint=fingerprint,
    )
    assert attestation.record_id == result_record_id
    return content, attestation


def _append_attestation(
    records: list[ArtifactRecord],
    *,
    fingerprint: Fingerprint = FP_A,
    logical_id: str = "validation-1",
) -> ArtifactRecord:
    _, result = _validation_parts()
    return _append(
        records,
        ValidationAttestationPayload(
            (result,), Role.ORCHESTRATOR, "e" * 64, "ar1-" + "f" * 64
        ),
        logical_id=logical_id,
        fingerprint=fingerprint,
    )


def _measurement(
    *, operation: str = "implementer_implementation", input_digest: str = "1" * 64
) -> ProviderInputMeasurementPayload:
    return ProviderInputMeasurementPayload("codex",
        Role.IMPLEMENTER,
        operation,
        "1",
        "a" * 64,
        "b" * 64,
        input_digest,
        "2" * 64,
        (ProviderInputComponentPayload("prompt", 3, 3),),
        3,
        3,
        10,
        10,
        None,
        None,
        None,
        10,
        10,
        True,
        (),
        0,
        0,
        "prompt",
    )


def _attempt(
    measurement: ArtifactRecord,
    *,
    operation: str = "implementer_implementation",
    logical_operation_id: str = "provider-operation-1",
    attempt_number: int = 1,
    phase: str = "started",
) -> ProviderAttemptPayload:
    assert isinstance(measurement.payload, ProviderInputMeasurementPayload)
    terminal = phase != "started"
    return ProviderAttemptPayload("codex",
        Role.IMPLEMENTER,
        operation,
        "1",
        logical_operation_id,
        "a" * 64,
        measurement.record_id,
        measurement.payload.input_digest,
        attempt_number,
        phase,
        "2026-09-03T10:00:00+00:00",
        "2026-09-03T10:00:01+00:00" if terminal else None,
        1.0 if terminal else None,
        None,
        ProviderUsagePayload(output_tokens=1) if phase == "succeeded" else None,
    )


def _provider_pair(
    records: list[ArtifactRecord], *, content_kind: str = "agent_result"
) -> tuple[ArtifactRecord, ArtifactRecord]:
    blob = BlobReference(RESPONSE_SHA, 3)
    content = _append(
        records,
        ProviderContentPayload(
            Role.IMPLEMENTER,
            "1",
            1,
            "implementer_implementation",
            REQUEST_ID,
            RESPONSE_SHA,
            content_kind,
            3,
            blob,
        ),
    )
    decision = _append(
        records,
        _agent(),
        logical_id="agent-1-implementer_implementation-1",
    )
    return content, decision


def _append_review_anchor(
    records: list[ArtifactRecord], review: ArtifactRecord, *, revision: int = 1
) -> ArtifactRecord:
    digest = hashlib.sha256(review.record_id.encode("utf-8")).hexdigest()[:16]
    return _append(
        records,
        ReviewAnchorPayload(review.record_id, ()),
        logical_id=f"review-anchors-{digest}",
        revision=revision,
    )


def _append_review_validation(
    records: list[ArtifactRecord],
    review: ArtifactRecord,
    attestation: ArtifactRecord,
    *,
    revision: int = 1,
    fingerprint: Fingerprint = FP_A,
) -> ArtifactRecord:
    digest = hashlib.sha256(review.record_id.encode("utf-8")).hexdigest()[:16]
    return _append(
        records,
        ReviewValidationBindingPayload(review.record_id, attestation.record_id),
        logical_id=f"review-validation-{digest}",
        revision=revision,
        fingerprint=fingerprint,
    )


def _side_effect(*, phase: str = "intent") -> SideEffectPayload:
    operation = ("b40-marker",)
    key = stable_side_effect_key("internal", "1", operation)
    return SideEffectPayload(
        key,
        "internal",
        "1",
        operation,
        phase,
        None if phase == "intent" else "completed",
    )


def _case(case_id: str) -> RejectionInput:  # noqa: C901, PLR0912, PLR0915
    records: list[ArtifactRecord] = []

    if case_id in {
        "scope-extension-has-no-prior-stopped-implementer-result",
        "scope-extension-has-no-prior-slice-boundary",
        "scope-extension-is-not-followed-atomically-by-a-slice-boundary",
        "scope-extension-boundary-omits-the-approved-addition",
    }:
        _append(records, _transition())
        if case_id != "scope-extension-has-no-prior-slice-boundary":
            _append(
                records,
                SliceBoundaryPayload(
                    "1", "1" * 40, (("src/a.py",),), "2" * 64
                ),
                logical_id="slice-boundary-1",
            )
        if case_id != "scope-extension-has-no-prior-stopped-implementer-result":
            _append(
                records,
                replace(_agent(), outcome="stopped"),
                logical_id="agent-1-implementer_implementation-1",
            )
        _append(
            records,
            ScopeExtensionPayload(
                "1",
                "1",
                REQUEST_ID,
                "SCOPE-EXTENSION-REQUESTED",
                "Required paths: docs/a.md\nWhy required for current Slice: needed",
                (ScopeExtensionPathPayload("docs/a.md", "documentation"),),
            ),
            logical_id="scope-extension-1",
        )
        if case_id != "scope-extension-is-not-followed-atomically-by-a-slice-boundary":
            groups = (
                (("src/a.py",),)
                if case_id == "scope-extension-boundary-omits-the-approved-addition"
                else (("docs/a.md",), ("src/a.py",))
            )
            _append(
                records,
                SliceBoundaryPayload("1", "1" * 40, groups, "2" * 64),
                logical_id="slice-boundary-1",
                revision=2,
            )
    elif case_id in {
        "final-review-completion-references-a-missing-validation-attestation",
        "final-review-completion-fingerprint-differs-from-its-attestation",
        "final-review-completion-is-recorded-outside-an-implement-run",
    }:
        _append(
            records,
            RunIdentityPayload(
                "inbox/b40.md",
                "feature/b40",
                "1" * 40,
                "2" * 40,
                (
                    "PLAN_ONLY"
                    if case_id == "final-review-completion-is-recorded-outside-an-implement-run"
                    else "IMPLEMENT"
                ),
                None,
            ),
        )
        attestation = _append_attestation(
            records,
            fingerprint=FP_B if case_id == "final-review-completion-fingerprint-differs-from-its-attestation" else FP_A,
        )
        _append(
            records,
            FinalReviewCompletedPayload(
                reviewer=Role.REVIEWER,
                work_unit_id="1",
                new_findings=(),
                occurrences=(),
                review_evidence=ReviewEvidencePayload(
                    "all dimensions",
                    "residual risk",
                    "break condition",
                ),
                pre_mortem="pre mortem",
                validation_attestation_record_id=(
                    "ar1-" + "9" * 64
                    if case_id == "final-review-completion-references-a-missing-validation-attestation"
                    else attestation.record_id
                ),
                reviewed_head_commit="2" * 40,
                transport_schema="native-claude-review-v3",
                request_id="native-review-request-" + "c" * 64,
                response_sha256=RESPONSE_SHA,
                scan_complete=True,
            ),
        )
    elif case_id == "two-transitions-move-work-unit-1-from-slice-1-to-slice-2":
        _append(records, _transition(slice_id="1"))
        _append(records, _transition(slice_id="2"))
    elif case_id == "workflow-policy-without-an-earlier-work-unit-transition":
        _append(records, WorkflowPolicyPayload("1", 0, 4))
    elif case_id == "slice-boundary-without-an-earlier-transition-for-its-slice":
        _append(records, SliceBoundaryPayload("1", "1" * 40, (("src/a.py",),), "2" * 64))
    elif case_id == "second-slice-boundary-changes-the-immutable-start-commit":
        _append(records, _transition())
        _append(records, SliceBoundaryPayload("1", "1" * 40, (("src/a.py",),), "2" * 64))
        _append(records, SliceBoundaryPayload("1", "3" * 40, (("src/a.py",),), "2" * 64))
    elif case_id == "two-workflow-events-reference-the-same-transition":
        domain = _append(records, _transition())
        _event("transition", domain, work_unit_id="1", records=records)
        _event("transition", domain, work_unit_id="1", records=records)
    elif case_id == "workflow-event-references-a-record-outside-the-supplied-chain":
        missing = _append(records, _transition())
        records.clear()
        _event("transition", missing, work_unit_id="1", records=records)
    elif case_id == "run-event-references-a-workflow-transition":
        domain = _append(records, _transition())
        _event("run", domain, work_unit_id=None, records=records)
    elif case_id == "run-event-fingerprint-differs-from-the-referenced-run-identity":
        domain = _append(records, RunIdentityPayload("inbox/b40.md", "feature/b40", "1" * 40, "1" * 40, "IMPLEMENT", None))
        _event("run", domain, work_unit_id=None, fingerprint=FP_B, records=records)
    elif case_id == "run-event-carries-a-work-unit-identity":
        domain = _append(records, RunIdentityPayload("inbox/b40.md", "feature/b40", "1" * 40, "1" * 40, "IMPLEMENT", None))
        event = _event("run", domain, work_unit_id=None, records=records)
        object.__setattr__(event.payload, "work_unit_id", "1")
    elif case_id == "transition-event-carries-another-slice-than-its-transition":
        domain = _append(records, _transition())
        _event("transition", domain, work_unit_id="1", slice_id="2", records=records)
    elif case_id == "review-event-carries-another-work-unit-than-its-review":
        domain = _append(records, _review("1"), logical_id="review-claude-1-1")
        _event("review", domain, work_unit_id="2", round_number=1, records=records)
    elif case_id == "invocation-failure-record-has-a-malformed-logical-identity":
        _append(records, _transition())
        _append_failure(records, _failure(), logical_id="malformed")
    elif case_id == "invocation-failure-has-no-earlier-work-unit-transition":
        _append_failure(records, _failure())
    elif case_id == "invocation-failure-step-differs-from-the-active-transition":
        _append(records, _transition(step="implementer_plan"))
        _append_failure(records, _failure())
    elif case_id == "invocation-failure-record-fingerprint-differs-from-retry-binding":
        _append(records, _transition())
        _append_failure(records, _failure(fingerprint="b" * 64))
    elif case_id == "two-invocation-failures-carry-the-same-invocation-id":
        _append(records, _transition())
        failure = _failure()
        _append_failure(records, failure)
        _append_failure(records, failure, revision=2)
    elif case_id == "quota-transition-has-a-malformed-logical-identity":
        _append(records, QuotaPausePayload(Role.IMPLEMENTER, "a" * 64, "2026-09-03T10:01:00+00:00"), logical_id="malformed")
    elif case_id == "quota-transition-has-no-earlier-invocation-failure":
        _append(records, QuotaPausePayload(Role.IMPLEMENTER, "a" * 64, "2026-09-03T10:01:00+00:00"), logical_id="quota-pause-missing")
    elif case_id == "quota-transition-retry-time-differs-from-invocation-decision":
        _append(records, _transition())
        _append_failure(records, _failure())
        _append(records, QuotaPausePayload(Role.IMPLEMENTER, "a" * 64, "2026-09-03T10:02:00+00:00"), logical_id="quota-pause-invocation-1")
    elif case_id in {
        "gate-transition-logical-identity-differs-from-its-work-unit",
        "gate-transition-carries-paths-without-active-test-fingerprint",
        "gate-transition-has-no-earlier-work-unit-transition",
    }:
        gate = _gate_transition()
        logical_id = "gate-transition-1"
        if case_id == "gate-transition-logical-identity-differs-from-its-work-unit":
            logical_id = "gate-transition-wrong"
        gate_record = _append(records, gate, logical_id=logical_id)
        if case_id == "gate-transition-carries-paths-without-active-test-fingerprint":
            object.__setattr__(
                gate_record.payload,
                "active_test_paths",
                ("tests/test_b40.py",),
            )
    elif case_id in {
        "gate-decision-has-no-transition-for-its-work-unit",
        "gate-decision-references-a-missing-gate-record",
        "gate-decision-references-a-pending-gate-record",
        "gate-decision-fingerprint-differs-from-gate-record",
        "two-gate-decisions-bind-the-same-work-unit-and-gate",
    }:
        transition = _append(records, _transition())
        _ = transition
        gate_record: ArtifactRecord | None = None
        if case_id != "gate-decision-has-no-transition-for-its-work-unit":
            gate_record = _append(
                records,
                GatePayload(
                    "b40",
                    "pending"
                    if case_id == "gate-decision-references-a-pending-gate-record"
                    else "approved",
                    Role.USER,
                    "reviewed",
                ),
            )
        gate_id = gate_record.record_id if gate_record is not None else "ar1-" + "4" * 64
        if case_id == "gate-decision-references-a-missing-gate-record":
            gate_id = "ar1-" + "4" * 64
        decision_fp = FP_B if case_id == "gate-decision-fingerprint-differs-from-gate-record" else FP_A
        _append(
            records,
            GateDecisionPayload(
                "404" if case_id == "gate-decision-has-no-transition-for-its-work-unit" else "1",
                gate_id,
                (),
                None,
            ),
            fingerprint=decision_fp,
        )
        if case_id == "two-gate-decisions-bind-the-same-work-unit-and-gate":
            _append(records, GateDecisionPayload("1", gate_id, (), None), revision=2)
    elif case_id == "two-validation-content-records-bind-the-same-result":
        target = "ar1-" + "4" * 64
        output, _ = _validation_parts()
        for index in range(2):
            _append(records, ValidationContentPayload(f"validation-{index}", target, "validation-matrix-v1", "e" * 64, "duplicate result", (output,)))
    elif case_id == "attestation-lacks-authoritative-validation-content":
        _append_attestation(records)
        return RejectionInput(tuple(records), require_content_authority=True)
    elif case_id in {
        "validation-content-and-attestation-disagree-on-content-record",
        "attestation-result-digest-differs-from-exact-output-content",
    }:
        content, attestation = _append_validation_pair(records)
        if case_id == "validation-content-and-attestation-disagree-on-content-record":
            object.__setattr__(attestation.payload, "content_record_id", "ar1-" + "4" * 64)
        else:
            result = attestation.payload.results[0]
            object.__setattr__(attestation.payload, "results", (replace(result, output_sha256="4" * 64),))
        return RejectionInput(tuple(records), require_content_authority=True)
    elif case_id == "non-tail-validation-content-has-no-attestation":
        output, _ = _validation_parts()
        _append(records, ValidationContentPayload("validation-1", "ar1-" + "4" * 64, "validation-matrix-v1", "e" * 64, "orphan", (output,)))
        _append(records, RunIdentityPayload("inbox/b40.md", "feature/b40", "1" * 40, "1" * 40, "IMPLEMENT", None))
    elif case_id == "native-agent-decision-lacks-request-binding":
        payload = _agent()
        decision = _append(records, payload)
        object.__setattr__(decision.payload, "request_id", None)
    elif case_id == "native-decision-logical-id-has-no-numeric-round":
        _append(records, _agent(), logical_id="agent-without-round")
        return RejectionInput(tuple(records), require_content_authority=True)
    elif case_id == "native-decision-has-no-matching-provider-content-record":
        _append(records, _agent(), logical_id="agent-1-implementer_implementation-1")
        return RejectionInput(tuple(records), require_content_authority=True)
    elif case_id == "provider-content-kind-differs-from-its-native-decision":
        _provider_pair(records, content_kind="final_report")
    elif case_id == "non-tail-provider-content-has-no-native-decision":
        blob = BlobReference(RESPONSE_SHA, 3)
        _append(records, ProviderContentPayload(Role.IMPLEMENTER, "1", 1, "implementer_implementation", REQUEST_ID, RESPONSE_SHA, "agent_result", 3, blob))
        _append(records, RunIdentityPayload("inbox/b40.md", "feature/b40", "1" * 40, "1" * 40, "IMPLEMENT", None))
    elif case_id == "review-anchor-references-a-missing-review":
        _append(records, ReviewAnchorPayload("ar1-" + "4" * 64, ()), logical_id="review-anchors-missing")
    elif case_id == "two-anchor-records-bind-the-same-review":
        review = _append(records, _review(), logical_id="review-claude-1-1")
        _append_review_anchor(records, review)
        _append_review_anchor(records, review, revision=2)
    elif case_id == "review-validation-binding-references-missing-facts":
        _append(records, ReviewValidationBindingPayload("ar1-" + "4" * 64, "ar1-" + "5" * 64), logical_id="review-validation-missing")
    elif case_id in {
        "review-validation-binding-fingerprint-differs-from-both-facts",
        "two-validation-bindings-reference-the-same-review",
    }:
        attestation = _append_attestation(records)
        review = _append(records, _review(), logical_id="review-claude-1-1")
        _append_review_validation(
            records,
            review,
            attestation,
            fingerprint=(
                FP_B
                if case_id == "review-validation-binding-fingerprint-differs-from-both-facts"
                else FP_A
            ),
        )
        if case_id == "two-validation-bindings-reference-the-same-review":
            _append_review_validation(records, review, attestation, revision=2)
    elif case_id in {
        "authoritative-review-lacks-its-anchor-list-record",
        "authoritative-review-lacks-its-validation-binding",
        "review-finding-ids-differ-from-the-transition-prefix",
    }:
        attestation: ArtifactRecord | None = None
        if case_id == "review-finding-ids-differ-from-the-transition-prefix":
            attestation = _append_attestation(records)
        finding_ids = (
            ("R-01",)
            if case_id == "review-finding-ids-differ-from-the-transition-prefix"
            else ()
        )
        review = _append(
            records, _review(finding_ids=finding_ids), logical_id="review-claude-1-1"
        )
        if case_id in {
            "authoritative-review-lacks-its-validation-binding",
            "review-finding-ids-differ-from-the-transition-prefix",
        }:
            _append_review_anchor(records, review)
        if case_id == "review-finding-ids-differ-from-the-transition-prefix":
            assert attestation is not None
            _append_review_validation(records, review, attestation)
        return RejectionInput(tuple(records), require_review_authority=True)
    elif case_id == "review-packet-logical-identity-differs-from-content-fingerprint":
        blob = BlobReference("6" * 64, 3)
        _append(records, ReviewPacketPayload("1", "a" * 64, "slice", ("src/a.py",), "7" * 64, 3, blob), logical_id="review-packet-wrong", fingerprint=FP_A)
    elif case_id == "work-unit-revision-changes-slice-and-rewinds-round":
        _append(records, WorkUnitPayload("1", 2, ("src/a.py",)), logical_id="work-unit-1")
        _append(records, WorkUnitPayload("2", 1, ("src/a.py",)), logical_id="work-unit-1", revision=2)
    elif case_id == "diagnostic-after-first-work-unit-references-work-unit-404":
        _append(records, WorkUnitPayload("1", 1, ("src/a.py",)), logical_id="work-unit-1")
        from artifact_models import DiagnosticPayload
        _append(records, DiagnosticPayload(Role.IMPLEMENTER, "404", 1, "a" * 64, "missing work unit"))
    elif case_id == "commit-binding-references-a-missing-validation-attestation":
        _append(records, BindingPayload("commit", "deadbeef", "ar1-" + "4" * 64, ("ar1-" + "5" * 64,)))
    elif case_id == "commit-binding-fingerprint-differs-from-validation-attestation":
        attestation = _append_attestation(records)
        _append(
            records,
            BindingPayload(
                "commit",
                "deadbeef",
                attestation.record_id,
                ("ar1-" + "5" * 64,),
            ),
            fingerprint=FP_B,
        )
    elif case_id == "commit-binding-references-a-missing-approval-review":
        attestation = _append_attestation(records)
        _append(records, BindingPayload("commit", "deadbeef", attestation.record_id, ("ar1-" + "4" * 64,)))
    elif case_id == "commit-binding-fingerprint-differs-from-approval-review":
        attestation = _append_attestation(records)
        review = _append(
            records,
            _review(),
            logical_id="review-claude-1-1",
            fingerprint=FP_B,
        )
        _append(
            records,
            BindingPayload(
                "commit", "deadbeef", attestation.record_id, (review.record_id,)
            ),
        )
    elif case_id == "workflow-completion-references-a-missing-final-binding":
        _append(records, WorkflowCompletionPayload("completed", "ar1-" + "4" * 64))
    elif case_id == "workflow-completion-fingerprint-differs-from-final-binding":
        attestation = _append_attestation(records)
        review = _append(records, _review(), logical_id="review-claude-1-1")
        binding = _append(
            records,
            BindingPayload(
                "commit", "deadbeef", attestation.record_id, (review.record_id,)
            ),
        )
        _append(
            records,
            WorkflowCompletionPayload("completed", binding.record_id),
            fingerprint=FP_B,
        )
    elif case_id == "final-preflight-references-a-missing-measurement":
        _append(records, FinalReviewPreflightPayload("claude", Role.REVIEWER, "reviewer_final_review", "1", "a" * 64, "b" * 64, "missing-measurement", "passed", None, None, (), (), None))
    elif case_id == "final-preflight-fingerprint-differs-from-its-measurement":
        measurement = _append(records, _measurement())
        _append(
            records,
            FinalReviewPreflightPayload("claude",
                Role.REVIEWER,
                "reviewer_final_review",
                "1",
                "a" * 64,
                "b" * 64,
                measurement.record_id,
                "passed",
                None,
                None,
                (),
                (),
                None,
            ),
            fingerprint=FP_B,
        )
    elif case_id == "provider-attempt-fingerprint-differs-from-its-measurement":
        measurement = _append(records, _measurement())
        _append(records, _attempt(measurement), fingerprint=FP_B)
    elif case_id in {
        "provider-attempt-references-a-measurement-outside-the-chain",
        "provider-attempt-operation-differs-from-bound-measurement",
        "provider-attempt-numbering-starts-at-two",
        "provider-attempt-has-three-physical-revisions",
        "provider-attempt-begins-with-terminal-phase",
        "second-provider-attempt-changes-immutable-operation",
        "second-provider-attempt-revision-remains-in-started-phase",
        "provider-terminal-revision-changes-its-measurement-and-operation",
    }:
        if case_id == "provider-attempt-references-a-measurement-outside-the-chain":
            fake = ArtifactRecord.create(run_id=RUN_ID, logical_id="measurement-missing", revision=1, fingerprint=FP_A, predecessor_ids=(), created_at="2026-09-03T09:00:00+00:00", idempotency_key="missing", payload=_measurement())
            _append(records, _attempt(fake))
        elif case_id == "provider-attempt-operation-differs-from-bound-measurement":
            measurement = _append(records, _measurement())
            _append(records, _attempt(measurement, operation="implementer_plan"))
        elif case_id == "provider-attempt-numbering-starts-at-two":
            measurement = _append(records, _measurement())
            _append(records, _attempt(measurement, attempt_number=2))
        elif case_id == "provider-attempt-has-three-physical-revisions":
            measurement = _append(records, _measurement())
            for index in range(3):
                _append(records, _attempt(measurement), logical_id=f"attempt-{index}", revision=index + 1)
        elif case_id == "provider-attempt-begins-with-terminal-phase":
            measurement = _append(records, _measurement())
            _append(records, _attempt(measurement, phase="succeeded"), revision=1)
        elif case_id == "second-provider-attempt-changes-immutable-operation":
            first_measurement = _append(records, _measurement(operation="implementer_implementation"))
            _append(records, _attempt(first_measurement, operation="implementer_implementation", attempt_number=1))
            second_measurement = _append(records, _measurement(operation="implementer_plan", input_digest="2" * 64))
            _append(records, _attempt(second_measurement, operation="implementer_plan", attempt_number=2))
        elif case_id == "second-provider-attempt-revision-remains-in-started-phase":
            measurement = _append(records, _measurement())
            _append(records, _attempt(measurement), logical_id="attempt-start", revision=1)
            _append(records, _attempt(measurement), logical_id="attempt-terminal", revision=2)
        else:
            first_measurement = _append(records, _measurement(operation="implementer_implementation"))
            _append(records, _attempt(first_measurement), logical_id="attempt-start", revision=1)
            second_measurement = _append(records, _measurement(operation="implementer_plan", input_digest="2" * 64))
            _append(records, _attempt(second_measurement, operation="implementer_plan", phase="succeeded"), logical_id="attempt-terminal", revision=2)
    elif case_id in {
        "side-effect-has-three-physical-phases",
        "side-effect-begins-with-a-result-phase",
        "side-effect-intent-logical-identity-differs-from-its-key",
        "second-side-effect-revision-remains-an-intent",
        "side-effect-result-changes-the-intent-logical-identity",
    }:
        intent_payload = _side_effect()
        expected_logical = "side-effect-" + hashlib.sha256(intent_payload.effect_key.encode("utf-8")).hexdigest()[:32]
        if case_id == "side-effect-has-three-physical-phases":
            for index in range(3):
                _append(records, intent_payload, logical_id=f"effect-{index}", revision=index + 1)
        elif case_id == "side-effect-begins-with-a-result-phase":
            _append(records, _side_effect(phase="result"), logical_id=expected_logical)
        elif case_id == "side-effect-intent-logical-identity-differs-from-its-key":
            _append(records, intent_payload, logical_id="side-effect-wrong")
        else:
            _append(records, intent_payload, logical_id=expected_logical)
            if case_id == "second-side-effect-revision-remains-an-intent":
                _append(records, intent_payload, logical_id=expected_logical, revision=2)
            else:
                _append(records, _side_effect(phase="result"), logical_id="side-effect-other", revision=2)
    elif case_id == "resume-check-expected-head-differs-from-immediate-predecessor":
        _append(records, ResumeCheckPayload("ar1-" + "4" * 64, "a" * 64, "matched"))
    else:
        raise AssertionError(f"missing B40 rejection input for case {case_id}")

    return RejectionInput(tuple(records))

def _load_baseline() -> dict[str, object]:
    document = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    assert set(document) == {"schema_version", "entries", "unreachable"}
    assert document["schema_version"] == "replay-rejection-corpus-v1"
    entries = document["entries"]
    assert isinstance(entries, list) and len(entries) == 78
    fields = {"case_id", "input", "function", "guard", "code", "message_expression", "message"}
    assert all(isinstance(entry, dict) and set(entry) == fields
               and all(isinstance(value, str) and value for value in entry.values())
               for entry in entries)
    case_ids = [entry["case_id"] for entry in entries]
    assert len(set(case_ids)) == len(case_ids)
    unreachable = document["unreachable"]
    assert isinstance(unreachable, list) and len(unreachable) == 3
    assert all(isinstance(entry, dict)
               and set(entry) == {"case_id", "barrier", "reason"}
               and all(isinstance(value, str) and value for value in entry.values())
               for entry in unreachable)
    assert {entry["case_id"] for entry in unreachable} <= set(case_ids)
    assert len({entry["case_id"] for entry in unreachable}) == 3
    return document


def _entries() -> tuple[dict[str, object], ...]:
    return tuple(_load_baseline()["entries"])


def _anchor(entry: dict[str, object]) -> tuple[str, str, str, str]:
    return (str(entry["function"]), str(entry["guard"]),
            str(entry["code"]), str(entry["message_expression"]))


def _source_emissions() -> tuple[dict[str, object], ...]:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    emissions: list[dict[str, object]] = []
    for function in tree.body:
        if not isinstance(function, ast.FunctionDef) or not (
            function.name.startswith("_validate_")
            or function.name == "_index_validation_content"
        ):
            continue
        parents = {
            child: parent
            for parent in ast.walk(function)
            for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(function):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id not in {"_fail", "_same_fingerprint"}:
                continue
            current: ast.AST = node
            guards: list[str] = []
            while current in parents:
                current = parents[current]
                if isinstance(current, ast.If):
                    guards.append(ast.unparse(current.test))
            guard = next((item for item in guards if "isinstance(payload," in item), None)
            guard = guard or (guards[0] if guards else "")
            if node.func.id == "_fail":
                code = node.args[0]
                assert isinstance(code, ast.Attribute)
                diagnostic = code.attr.replace("_", "-")
                expression = ast.unparse(node.args[1])
            else:
                diagnostic = "RECORD-FINGERPRINT-MISMATCH"
                expression = "fingerprint of " + ast.unparse(node.args[1])
            emissions.append({
                "function": function.name,
                "guard": guard,
                "code": diagnostic,
                "message_expression": expression,
                "line": node.lineno,
            })
    return tuple(emissions)


def _source_line(entry: dict[str, object]) -> int:
    matches = [
        int(source["line"])
        for source in _source_emissions()
        if _anchor(source) == _anchor(entry)
    ]
    assert len(matches) == 1, entry["case_id"]
    return matches[0]


def _capture(
    validator: Callable[..., str | None], case: RejectionInput
) -> tuple[ArtifactReplayError, dict[str, object]]:
    try:
        validator(
            case.records,
            {record.record_id: record for record in case.records},
            require_content_authority=case.require_content_authority,
            require_review_authority=case.require_review_authority,
            allow_incomplete_review_tail=case.allow_incomplete_review_tail,
        )
    except ArtifactReplayError as error:
        sites = {(str(site["function"]), int(site["line"])): site
                 for site in _source_emissions()}
        matches: list[dict[str, object]] = []
        traceback: TracebackType | None = error.__traceback__
        while traceback is not None:
            key = (traceback.tb_frame.f_code.co_name, traceback.tb_lineno)
            if key in sites:
                matches.append(sites[key])
            traceback = traceback.tb_next
        assert len(matches) == 1, matches
        return error, matches[0]
    raise AssertionError("rejection corpus input was unexpectedly accepted")


def _assert_entry(
    entry: dict[str, object],
    *,
    validator: Callable[..., str | None] = artifact_replay._validate_payload_references,
) -> None:
    error, source = _capture(validator, _case(str(entry["case_id"])))
    assert error.code.value == entry["code"]
    assert error.diagnostic.message == entry["message"]
    assert _anchor(source) == _anchor(entry), entry["case_id"]


def test_rejection_corpus_baseline_is_complete_and_source_bound() -> None:
    entries = _entries()
    sources = _source_emissions()
    source_anchors = [_anchor(source) for source in sources]
    fixture_anchors = [_anchor(entry) for entry in entries]
    assert len(sources) == len(entries) == 78
    assert len(set(source_anchors)) == len(source_anchors), "ambiguous source emission"
    assert len(set(fixture_anchors)) == len(fixture_anchors), "ambiguous corpus anchor"
    assert set(source_anchors) == set(fixture_anchors)
    assert len({entry["case_id"] for entry in entries}) == 78
    assert Counter(entry["code"] for entry in entries) == {
        "RECORD-FINGERPRINT-MISMATCH": 33,
        "RECORD-REFERENCE-MISSING": 26,
        "RECORD-DUPLICATE": 8,
        "RECORD-MISSING": 9,
        "RECORD-TYPE-MISMATCH": 2,
    }
    # Three schema-unreachable defences remain exercised by mutated objects.
    assert len(_load_baseline()["unreachable"]) == 3


def test_replay_validator_has_exact_named_pass_order_and_split_chain_pass() -> None:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    validator = functions["_validate_payload_references"]
    pass_calls = tuple(
        node.func.id
        for node in sorted(
            (
                child
                for child in ast.walk(validator)
                if isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id in VALIDATION_PASSES
            ),
            key=lambda child: child.lineno,
        )
    )
    assert pass_calls == VALIDATION_PASSES

    chain_pass = functions["_validate_chain_record_references"]
    split_calls = tuple(
        node.func.id
        for node in sorted(
            (
                child
                for child in ast.walk(chain_pass)
                if isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id.startswith("_validate_")
            ),
            key=lambda child: child.lineno,
        )
    )
    assert split_calls == (
        "_validate_work_unit_activity_reference",
        "_validate_bound_record_references",
    )


@pytest.mark.parametrize("entry", _entries(), ids=lambda entry: entry["case_id"])
def test_every_reachable_rejection_emission_has_a_bound_input(
    entry: dict[str, object],
) -> None:
    _assert_entry(entry)


def test_equal_code_site_swap_is_detected_by_the_corpus() -> None:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    entries = {str(entry["case_id"]): entry for entry in _entries()}
    left_entry = entries["workflow-policy-without-an-earlier-work-unit-transition"]
    right_entry = entries["slice-boundary-without-an-earlier-transition-for-its-slice"]
    left_line = _source_line(left_entry)
    right_line = _source_line(right_entry)
    target_lines = {left_line, right_line}
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and target_lines.issubset(
            {
                child.lineno
                for child in ast.walk(node)
                if isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id == "_fail"
            }
        )
    )
    calls = {
        node.lineno: node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_fail"
    }
    left, right = calls[left_line], calls[right_line]
    left.args[1], right.args[1] = right.args[1], left.args[1]
    validator_function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_validate_payload_references"
    )
    module = ast.Module(body=[function, validator_function], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = dict(vars(artifact_replay))
    exec(compile(module, "<b40-equal-code-site-swap>", "exec"), namespace)
    mutant = namespace["_validate_payload_references"]

    for entry, other_entry in (
        (left_entry, right_entry),
        (right_entry, left_entry),
    ):
        error, actual_source = _capture(mutant, _case(str(entry["case_id"])))
        assert error.code.value == entry["code"]
        assert actual_source["function"] == entry["function"]
        assert error.diagnostic.message == other_entry["message"]
        assert error.diagnostic.message != entry["message"]


def test_validation_pass_order_swap_is_detected() -> None:
    records: list[ArtifactRecord] = []
    _append(
        records,
        ReviewPacketPayload(
            "1",
            "a" * 64,
            "slice",
            ("src/a.py",),
            "7" * 64,
            3,
            BlobReference("6" * 64, 3),
        ),
        logical_id="review-packet-wrong",
    )
    intent = _side_effect()
    for index in range(3):
        _append(
            records,
            intent,
            logical_id=f"effect-{index}",
            revision=index + 1,
        )
    case = RejectionInput(tuple(records))

    original_error, _ = _capture(
        artifact_replay._validate_payload_references, case
    )
    assert (
        original_error.diagnostic.message
        == "review packet record identity differs from its content binding"
    )

    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    validator_function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_validate_payload_references"
    )
    pass_calls = {
        node.value.func.id: index
        for index, node in enumerate(validator_function.body)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
    }
    packet_index = pass_calls["_validate_review_packet_bindings"]
    side_effect_index = pass_calls["_validate_side_effect_sequences"]
    validator_function.body[packet_index], validator_function.body[side_effect_index] = (
        validator_function.body[side_effect_index],
        validator_function.body[packet_index],
    )
    module = ast.Module(body=[validator_function], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = dict(vars(artifact_replay))
    exec(compile(module, "<b41-validation-pass-order-swap>", "exec"), namespace)
    mutant_error, _ = _capture(namespace["_validate_payload_references"], case)
    assert mutant_error.code is ReplayDiagnosticCode.RECORD_DUPLICATE
    assert (
        mutant_error.diagnostic.message
        == "side effect has too many phases"
    )
    assert mutant_error.diagnostic.message != original_error.diagnostic.message


def test_valid_reference_chain_remains_accepted() -> None:
    records: list[ArtifactRecord] = []
    _append(records, _transition())
    assert artifact_replay._validate_payload_references(
        tuple(records),
        {record.record_id: record for record in records},
        require_content_authority=False,
        require_review_authority=False,
        allow_incomplete_review_tail=False,
    ) is None
