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
    FinalReviewPreflightPayload,
    FindingHandoffExportPayload,
    FindingHandoffImportPayload,
    FindingSeverity,
    FindingTransitionPayload,
    Fingerprint,
    FingerprintKind,
    GateDecisionPayload,
    GatePayload,
    GateTransitionPayload,
    ImportedFindingTransition,
    InvocationFailurePayload,
    PlanPayload,
    ProviderAttemptPayload,
    ProviderContentPayload,
    ProviderInputComponentPayload,
    ProviderInputMeasurementPayload,
    ProviderUsagePayload,
    QuotaPausePayload,
    RecordType,
    ResumeCheckPayload,
    ReviewAnchorPayload,
    ReviewPacketPayload,
    ReviewPayload,
    ReviewValidationBindingPayload,
    Role,
    RunIdentityPayload,
    SideEffectPayload,
    SliceBoundaryPayload,
    SliceSpec,
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
    finding_transition_sequence_sha256,
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
REQUEST_ID = "native-codex-request-" + "c" * 64
RESPONSE_SHA = "d" * 64


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
    step: str = "codex_implementation",
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
        Role.CODEX,
        work_unit_id,
        "ready",
        (),
        "native-codex-v2",
        REQUEST_ID,
        RESPONSE_SHA,
    )


def _review(
    work_unit_id: str = "1", *, finding_ids: tuple[str, ...] = ()
) -> ReviewPayload:
    return ReviewPayload(
        Role.CLAUDE,
        work_unit_id,
        "approved",
        finding_ids,
        "checked",
        "native-claude-review-v2",
        "native-review-request-" + "c" * 64,
        RESPONSE_SHA,
    )


def _failure(
    *,
    invocation_id: str = "invocation-1",
    work_unit_id: str = "1",
    step: str = "codex_implementation",
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
        Role.CODEX,
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
    records: list[ArtifactRecord], *, fingerprint: Fingerprint = FP_A
) -> ArtifactRecord:
    _, result = _validation_parts()
    return _append(
        records,
        ValidationAttestationPayload(
            (result,), Role.ORCHESTRATOR, "e" * 64, "ar1-" + "f" * 64
        ),
        logical_id="validation-1",
        fingerprint=fingerprint,
    )


def _measurement(
    *, operation: str = "codex_implementation", input_digest: str = "1" * 64
) -> ProviderInputMeasurementPayload:
    return ProviderInputMeasurementPayload(
        Role.CODEX,
        Role.CODEX,
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
    operation: str = "codex_implementation",
    logical_operation_id: str = "provider-operation-1",
    attempt_number: int = 1,
    phase: str = "started",
) -> ProviderAttemptPayload:
    assert isinstance(measurement.payload, ProviderInputMeasurementPayload)
    terminal = phase != "started"
    return ProviderAttemptPayload(
        Role.CODEX,
        Role.CODEX,
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
            Role.CODEX,
            "1",
            1,
            "codex_implementation",
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
        logical_id="agent-1-codex_implementation-1",
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


def _finding_transition() -> FindingTransitionPayload:
    return FindingTransitionPayload(
        "C-01",
        Role.CLAUDE,
        Role.CLAUDE,
        "opened",
        FindingSeverity.OBSERVATION,
        "open",
        "Carry the finding.",
        "source-work-unit",
        "Imported observation.",
        "The observation remains visible.",
        "1",
        1,
    )


def _import_payload(*, target_run_id: str = RUN_ID, source_run_id: str = "source-run") -> FindingHandoffImportPayload:
    imported = ImportedFindingTransition("ar1-" + "3" * 64, _finding_transition())
    digest = finding_transition_sequence_sha256((imported,))
    return FindingHandoffImportPayload(
        source_run_id,
        "ar1-" + "4" * 64,
        "5" * 40,
        "ar1-" + "6" * 64,
        "ar1-" + "7" * 64,
        target_run_id,
        "8" * 64,
        digest,
        (imported,),
        Role.ORCHESTRATOR,
    )


def _export_payload(
    *,
    source_head: str,
    approval_review_id: str,
    transition_ids: tuple[str, ...] = ("ar1-" + "0" * 64,),
    transition_digest: str = "9" * 64,
    source_run_id: str = RUN_ID,
) -> FindingHandoffExportPayload:
    return FindingHandoffExportPayload(
        source_run_id,
        source_head,
        "5" * 40,
        approval_review_id,
        transition_ids,
        transition_digest,
        "inbox/backlog/b40-followup.md",
        "8" * 64,
        Role.ORCHESTRATOR,
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


def _case(line: int) -> RejectionInput:  # noqa: C901, PLR0912, PLR0915
    records: list[ArtifactRecord] = []

    if line == 1416:
        _append(records, _transition(slice_id="1"))
        _append(records, _transition(slice_id="2"))
    elif line == 1424:
        _append(records, WorkflowPolicyPayload("1", 0, 4))
    elif line == 1431:
        _append(records, SliceBoundaryPayload("1", "1" * 40, (("src/a.py",),), "2" * 64))
    elif line == 1445:
        _append(records, _transition())
        _append(records, SliceBoundaryPayload("1", "1" * 40, (("src/a.py",),), "2" * 64))
        _append(records, SliceBoundaryPayload("1", "3" * 40, (("src/a.py",),), "2" * 64))
    elif line == 1454:
        domain = _append(records, _transition())
        _event("transition", domain, work_unit_id="1", records=records)
        _event("transition", domain, work_unit_id="1", records=records)
    elif line == 1466:
        missing = _append(records, _transition())
        records.clear()
        _event("transition", missing, work_unit_id="1", records=records)
    elif line == 1483:
        domain = _append(records, _transition())
        _event("run", domain, work_unit_id=None, records=records)
    elif line == 1490:
        domain = _append(records, RunIdentityPayload("inbox/b40.md", "feature/b40", "1" * 40, "IMPLEMENT", None))
        _event("run", domain, work_unit_id=None, fingerprint=FP_B, records=records)
    elif line == 1498:
        domain = _append(records, RunIdentityPayload("inbox/b40.md", "feature/b40", "1" * 40, "IMPLEMENT", None))
        event = _event("run", domain, work_unit_id=None, records=records)
        object.__setattr__(event.payload, "work_unit_id", "1")
    elif line == 1508:
        domain = _append(records, _transition())
        _event("transition", domain, work_unit_id="1", slice_id="2", records=records)
    elif line == 1515:
        domain = _append(records, _review("1"), logical_id="review-claude-1-1")
        _event("review", domain, work_unit_id="2", round_number=1, records=records)
    elif line == 1530:
        _append(records, _transition())
        _append_failure(records, _failure(), logical_id="malformed")
    elif line == 1540:
        _append_failure(records, _failure())
    elif line == 1557:
        _append(records, _transition(step="codex_plan"))
        _append_failure(records, _failure())
    elif line == 1566:
        _append(records, _transition())
        _append_failure(records, _failure(fingerprint="b" * 64))
    elif line == 1572:
        _append(records, _transition())
        failure = _failure()
        _append_failure(records, failure)
        _append_failure(records, failure, revision=2)
    elif line == 1585:
        _append(records, QuotaPausePayload(Role.CODEX, "a" * 64, "2026-09-03T10:01:00+00:00"), logical_id="malformed")
    elif line == 1593:
        _append(records, QuotaPausePayload(Role.CODEX, "a" * 64, "2026-09-03T10:01:00+00:00"), logical_id="quota-pause-missing")
    elif line == 1616:
        _append(records, _transition())
        _append_failure(records, _failure())
        _append(records, QuotaPausePayload(Role.CODEX, "a" * 64, "2026-09-03T10:02:00+00:00"), logical_id="quota-pause-invocation-1")
    elif line in {1627, 1635, 1645}:
        gate = _gate_transition()
        logical_id = "gate-transition-1"
        if line == 1627:
            logical_id = "gate-transition-wrong"
        gate_record = _append(records, gate, logical_id=logical_id)
        if line == 1635:
            object.__setattr__(
                gate_record.payload,
                "active_test_paths",
                ("tests/test_b40.py",),
            )
    elif line in {1657, 1667, 1673, 1679, 1686}:
        transition = _append(records, _transition())
        _ = transition
        gate_record: ArtifactRecord | None = None
        if line != 1657:
            gate_record = _append(
                records,
                GatePayload("b40", "pending" if line == 1673 else "approved", Role.USER, "reviewed"),
            )
        gate_id = gate_record.record_id if gate_record is not None else "ar1-" + "4" * 64
        if line == 1667:
            gate_id = "ar1-" + "4" * 64
        decision_fp = FP_B if line == 1679 else FP_A
        _append(records, GateDecisionPayload("404" if line == 1657 else "1", gate_id, (), None), fingerprint=decision_fp)
        if line == 1686:
            _append(records, GateDecisionPayload("1", gate_id, (), None), revision=2)
    elif line == 1706:
        target = "ar1-" + "4" * 64
        output, _ = _validation_parts()
        for index in range(2):
            _append(records, ValidationContentPayload(f"validation-{index}", target, "validation-matrix-v1", "e" * 64, "duplicate result", (output,)))
    elif line == 1716:
        _append_attestation(records)
        return RejectionInput(tuple(records), require_content_authority=True)
    elif line in {1733, 1751}:
        content, attestation = _append_validation_pair(records)
        if line == 1733:
            object.__setattr__(attestation.payload, "content_record_id", "ar1-" + "4" * 64)
        else:
            result = attestation.payload.results[0]
            object.__setattr__(attestation.payload, "results", (replace(result, output_sha256="4" * 64),))
        return RejectionInput(tuple(records), require_content_authority=True)
    elif line == 1761:
        output, _ = _validation_parts()
        _append(records, ValidationContentPayload("validation-1", "ar1-" + "4" * 64, "validation-matrix-v1", "e" * 64, "orphan", (output,)))
        _append(records, RunIdentityPayload("inbox/b40.md", "feature/b40", "1" * 40, "IMPLEMENT", None))
    elif line == 1781:
        payload = _agent()
        decision = _append(records, payload)
        object.__setattr__(decision.payload, "request_id", None)
    elif line == 1814:
        _append(records, _agent(), logical_id="agent-without-round")
        return RejectionInput(tuple(records), require_content_authority=True)
    elif line == 1834:
        _append(records, _agent(), logical_id="agent-1-codex_implementation-1")
        return RejectionInput(tuple(records), require_content_authority=True)
    elif line == 1864:
        _provider_pair(records, content_kind="final_report")
    elif line == 1874:
        blob = BlobReference(RESPONSE_SHA, 3)
        _append(records, ProviderContentPayload(Role.CODEX, "1", 1, "codex_implementation", REQUEST_ID, RESPONSE_SHA, "agent_result", 3, blob))
        _append(records, RunIdentityPayload("inbox/b40.md", "feature/b40", "1" * 40, "IMPLEMENT", None))
    elif line == 1906:
        _append(records, ReviewAnchorPayload("ar1-" + "4" * 64, ()), logical_id="review-anchors-missing")
    elif line == 1912:
        review = _append(records, _review(), logical_id="review-claude-1-1")
        _append_review_anchor(records, review)
        _append_review_anchor(records, review, revision=2)
    elif line == 1937:
        _append(records, ReviewValidationBindingPayload("ar1-" + "4" * 64, "ar1-" + "5" * 64), logical_id="review-validation-missing")
    elif line in {1946, 1952}:
        attestation = _append_attestation(records)
        review = _append(records, _review(), logical_id="review-claude-1-1")
        _append_review_validation(records, review, attestation, fingerprint=FP_B if line == 1946 else FP_A)
        if line == 1952:
            _append_review_validation(records, review, attestation, revision=2)
    elif line in {2003, 2009, 2014}:
        attestation: ArtifactRecord | None = None
        if line == 2014:
            attestation = _append_attestation(records)
        review = _append(records, _review(finding_ids=("C-01",) if line == 2014 else ()), logical_id="review-claude-1-1")
        if line in {2009, 2014}:
            _append_review_anchor(records, review)
        if line == 2014:
            assert attestation is not None
            _append_review_validation(records, review, attestation)
        return RejectionInput(tuple(records), require_review_authority=True)
    elif line == 2029:
        blob = BlobReference("6" * 64, 3)
        _append(records, ReviewPacketPayload("1", "a" * 64, "slice", ("src/a.py",), "7" * 64, 3, blob), logical_id="review-packet-wrong", fingerprint=FP_A)
    elif line == 2059:
        _append(records, WorkUnitPayload("1", 2, ("src/a.py",)), logical_id="work-unit-1")
        _append(records, WorkUnitPayload("2", 1, ("src/a.py",)), logical_id="work-unit-1", revision=2)
    elif line == 2080:
        _append(records, _import_payload())
        _append(records, _import_payload(source_run_id="source-run-2"))
    elif line == 2093:
        previous = _append(records, RunIdentityPayload("inbox/b40.md", "feature/b40", "1" * 40, "IMPLEMENT", None))
        _append(records, _export_payload(source_head=previous.record_id, approval_review_id="ar1-" + "4" * 64, source_run_id="wrong-run"))
    elif line == 2105:
        previous = _append(records, RunIdentityPayload("inbox/b40.md", "feature/b40", "1" * 40, "IMPLEMENT", None))
        _append(records, _export_payload(source_head=previous.record_id, approval_review_id="ar1-" + "4" * 64))
    elif line == 2120:
        review = _append(records, _review(), logical_id="review-claude-1-1")
        _append(records, _export_payload(source_head=review.record_id, approval_review_id=review.record_id, transition_ids=("ar1-" + "4" * 64,)))
    elif line in {2139, 2149}:
        finding = _append(records, _finding_transition())
        review = _append(records, _review(), logical_id="review-claude-1-1")
        imported = ImportedFindingTransition(finding.record_id, finding.payload)
        digest = finding_transition_sequence_sha256((imported,))
        _append(records, _export_payload(source_head=review.record_id, approval_review_id=review.record_id, transition_ids=(finding.record_id,), transition_digest="9" * 64 if line == 2139 else digest))
    elif line == 2156:
        _append(records, _import_payload(target_run_id="another-run"))
    elif line == 2162:
        _append(records, _import_payload(source_run_id=RUN_ID))
    elif line == 2177:
        _append(records, WorkUnitPayload("1", 1, ("src/a.py",), (), "ar1-" + "4" * 64), logical_id="work-unit-1")
    elif line == 2199:
        imported = _append(records, _import_payload())
        _append(records, WorkUnitPayload("1", 1, ("src/a.py",), (), imported.record_id), logical_id="work-unit-1")
    elif line == 2228:
        _append(records, WorkUnitPayload("1", 1, ("src/a.py",)), logical_id="work-unit-1")
        from artifact_models import DiagnosticPayload
        _append(records, DiagnosticPayload(Role.CODEX, "404", 1, "a" * 64, "missing work unit"))
    elif line == 2240:
        _append(records, BindingPayload("commit", "deadbeef", "ar1-" + "4" * 64, ("ar1-" + "5" * 64,)))
    elif line == 2245:
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
    elif line == 2254:
        attestation = _append_attestation(records)
        _append(records, BindingPayload("commit", "deadbeef", attestation.record_id, ("ar1-" + "4" * 64,)))
    elif line == 2259:
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
    elif line == 2267:
        _append(records, WorkflowCompletionPayload("completed", "ar1-" + "4" * 64))
    elif line == 2272:
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
    elif line == 2280:
        _append(records, FinalReviewPreflightPayload(Role.CODEX, Role.CODEX, "codex_final_review", "1", "a" * 64, "b" * 64, "missing-measurement", "passed", None, None, (), (), None))
    elif line == 2285:
        measurement = _append(records, _measurement())
        _append(
            records,
            FinalReviewPreflightPayload(
                Role.CODEX,
                Role.CODEX,
                "codex_final_review",
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
    elif line == 2298:
        measurement = _append(records, _measurement())
        _append(records, _attempt(measurement), fingerprint=FP_B)
    elif line in {2293, 2307, 2331, 2340, 2345, 2354, 2360, 2371}:
        if line == 2293:
            fake = ArtifactRecord.create(run_id=RUN_ID, logical_id="measurement-missing", revision=1, fingerprint=FP_A, predecessor_ids=(), created_at="2026-09-03T09:00:00+00:00", idempotency_key="missing", payload=_measurement())
            _append(records, _attempt(fake))
        elif line == 2307:
            measurement = _append(records, _measurement())
            _append(records, _attempt(measurement, operation="codex_plan"))
        elif line == 2331:
            measurement = _append(records, _measurement())
            _append(records, _attempt(measurement, attempt_number=2))
        elif line == 2340:
            measurement = _append(records, _measurement())
            for index in range(3):
                _append(records, _attempt(measurement), logical_id=f"attempt-{index}", revision=index + 1)
        elif line == 2345:
            measurement = _append(records, _measurement())
            _append(records, _attempt(measurement, phase="succeeded"), revision=1)
        elif line == 2354:
            first_measurement = _append(records, _measurement(operation="codex_implementation"))
            _append(records, _attempt(first_measurement, operation="codex_implementation", attempt_number=1))
            second_measurement = _append(records, _measurement(operation="codex_plan", input_digest="2" * 64))
            _append(records, _attempt(second_measurement, operation="codex_plan", attempt_number=2))
        elif line == 2360:
            measurement = _append(records, _measurement())
            _append(records, _attempt(measurement), logical_id="attempt-start", revision=1)
            _append(records, _attempt(measurement), logical_id="attempt-terminal", revision=2)
        else:
            first_measurement = _append(records, _measurement(operation="codex_implementation"))
            _append(records, _attempt(first_measurement), logical_id="attempt-start", revision=1)
            second_measurement = _append(records, _measurement(operation="codex_plan", input_digest="2" * 64))
            _append(records, _attempt(second_measurement, operation="codex_plan", phase="succeeded"), logical_id="attempt-terminal", revision=2)
    elif line in {2379, 2388, 2395, 2405, 2418}:
        intent_payload = _side_effect()
        expected_logical = "side-effect-" + hashlib.sha256(intent_payload.effect_key.encode("utf-8")).hexdigest()[:32]
        if line == 2379:
            for index in range(3):
                _append(records, intent_payload, logical_id=f"effect-{index}", revision=index + 1)
        elif line == 2388:
            _append(records, _side_effect(phase="result"), logical_id=expected_logical)
        elif line == 2395:
            _append(records, intent_payload, logical_id="side-effect-wrong")
        else:
            _append(records, intent_payload, logical_id=expected_logical)
            if line == 2405:
                _append(records, intent_payload, logical_id=expected_logical, revision=2)
            else:
                _append(records, _side_effect(phase="result"), logical_id="side-effect-other", revision=2)
    elif line == 2315:
        _append(records, ResumeCheckPayload("ar1-" + "4" * 64, "a" * 64, "matched"))
    else:
        raise AssertionError(f"missing B40 rejection input for source line {line}")

    return RejectionInput(tuple(records))


def _load_baseline() -> dict[str, object]:
    document = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    assert set(document) == {"schema_version", "entries", "unreachable"}
    assert document["schema_version"] == "replay-rejection-corpus-v1"
    unreachable = document["unreachable"]
    assert isinstance(unreachable, list)
    assert all(
        isinstance(entry, dict)
        and set(entry) == {"line", "barrier", "reason"}
        and isinstance(entry["line"], int)
        and isinstance(entry["barrier"], str)
        and isinstance(entry["reason"], str)
        for entry in unreachable
    )
    assert {entry["line"] for entry in unreachable} == {1498, 1635, 1781}
    return document


def _entries() -> tuple[dict[str, object], ...]:
    entries = _load_baseline()["entries"]
    assert isinstance(entries, list) and len(entries) == 81
    assert all(
        isinstance(entry, dict)
        and set(entry) == {"case_id", "input", "code", "line", "message"}
        and isinstance(entry["case_id"], str)
        and isinstance(entry["input"], str)
        and isinstance(entry["code"], str)
        and isinstance(entry["line"], int)
        and isinstance(entry["message"], str)
        for entry in entries
    )
    return tuple(entries)


def _capture(
    validator: Callable[..., str | None], case: RejectionInput
) -> tuple[ArtifactReplayError, int]:
    try:
        validator(
            case.records,
            {record.record_id: record for record in case.records},
            require_content_authority=case.require_content_authority,
            require_review_authority=case.require_review_authority,
            allow_incomplete_review_tail=case.allow_incomplete_review_tail,
        )
    except ArtifactReplayError as error:
        traceback: TracebackType | None = error.__traceback__
        call_lines: list[int] = []
        while traceback is not None:
            if traceback.tb_frame.f_code.co_name == "_validate_payload_references":
                call_lines.append(traceback.tb_lineno)
            traceback = traceback.tb_next
        assert len(call_lines) == 1
        return error, call_lines[0]
    raise AssertionError("rejection corpus input was unexpectedly accepted")


def _assert_entry(
    entry: dict[str, object],
    *,
    validator: Callable[..., str | None] = artifact_replay._validate_payload_references,
) -> None:
    error, line = _capture(validator, _case(int(entry["line"])))
    assert error.code.value == entry["code"]
    assert error.diagnostic.message == entry["message"]
    assert line == entry["line"]


def _source_emissions() -> tuple[tuple[int, str], ...]:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_validate_payload_references"
    )
    emissions = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id == "_fail":
            code = node.args[0]
            assert isinstance(code, ast.Attribute)
            emissions.append((node.lineno, code.attr.replace("_", "-")))
        elif node.func.id == "_same_fingerprint":
            emissions.append((node.lineno, "RECORD-FINGERPRINT-MISMATCH"))
    return tuple(sorted(emissions))


def test_rejection_corpus_baseline_is_complete_and_source_bound() -> None:
    entries = _entries()
    expected = tuple((int(entry["line"]), str(entry["code"])) for entry in entries)
    assert _source_emissions() == expected
    assert len({entry["case_id"] for entry in entries}) == 81
    assert len({entry["line"] for entry in entries}) == 81
    assert Counter(entry["code"] for entry in entries) == {
        "RECORD-FINGERPRINT-MISMATCH": 35,
        "RECORD-REFERENCE-MISSING": 26,
        "RECORD-DUPLICATE": 9,
        "RECORD-MISSING": 9,
        "RECORD-RUN-MISMATCH": 1,
        "RECORD-TYPE-MISMATCH": 1,
    }
    # All 81 rejection sites are reducer-reachable.  Three deliberately duplicated
    # defences cannot be produced by schema-valid persisted bytes; their cases
    # mutate an already validated object so the independent reducer check stays
    # executable and their ingress unreachability remains explicit.
    assert len(_load_baseline()["unreachable"]) == 3


@pytest.mark.parametrize("entry", _entries(), ids=lambda entry: entry["case_id"])
def test_every_reachable_rejection_emission_has_a_bound_input(
    entry: dict[str, object],
) -> None:
    _assert_entry(entry)


def test_equal_code_site_swap_is_detected_by_the_corpus() -> None:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_validate_payload_references"
    )
    calls = {
        node.lineno: node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_fail"
    }
    left, right = calls[1424], calls[1431]
    left.args[1], right.args[1] = right.args[1], left.args[1]
    module = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = dict(vars(artifact_replay))
    exec(compile(module, "<b40-equal-code-site-swap>", "exec"), namespace)
    mutant = namespace["_validate_payload_references"]

    entries = {int(entry["line"]): entry for entry in _entries()}
    for line, other_line in ((1424, 1431), (1431, 1424)):
        error, actual_line = _capture(mutant, _case(line))
        assert error.code.value == entries[line]["code"]
        assert actual_line == line
        assert error.diagnostic.message == entries[other_line]["message"]
        assert error.diagnostic.message != entries[line]["message"]


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
