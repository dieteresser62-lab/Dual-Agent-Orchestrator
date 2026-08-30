"""Agent-free, transition-specific checks immediately before final-review providers."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import asdict
import hashlib
from typing import Sequence

from artifact_models import (
    AgentResultPayload,
    ArtifactRecord,
    BindingPayload,
    FinalReviewPreflightPayload,
    FindingSeverity,
    FindingTransitionPayload,
    GatePayload,
    ProviderInputMeasurementPayload,
    RecordType,
    ReviewPayload,
    Role,
    ValidationAttestationPayload,
    WorkflowCompletionPayload,
    canonical_json,
)
from gates import matches_path_patterns
from provider_input_budget import ProviderInputBudgetExceeded
from workflow_state import (
    GateReason,
    SliceStatus,
    WorkflowState,
    WorkflowStep,
    WorkUnitKind,
    WorkUnitStatus,
)


FINAL_REVIEW_OPERATIONS = frozenset(
    {"codex_final_review", "claude_final_review"}
)
_TRANSITION_FINGERPRINT_EXCLUDED_TYPES = {
    RecordType.PROVIDER_INPUT_MEASUREMENT,
    RecordType.FINAL_REVIEW_PREFLIGHT,
    # Cursor and return-policy facts are verified by the dispatch guard. The
    # operation and immutable work-unit id already bind the provider dispatch,
    # so halt/resume of the same step must retain its deterministic local-check
    # identity instead of growing the bootstrap mirror.
    RecordType.WORKFLOW_TRANSITION,
    RecordType.WORKFLOW_POLICY,
    # Side-effect intents/results record physical execution and projection
    # progress. They do not change the semantic provider input transition.
    RecordType.SIDE_EFFECT,
}
_EXTERNAL_PATH_GATE_KINDS = {
    GateReason.UNEXPECTED_FILE: "unexpected-file",
    GateReason.QUOTA_RESUME_DIFF: "quota-resume-diff",
}


class FinalReviewPreflightDenied(ProviderInputBudgetExceeded):
    """A deterministic local denial; no provider process has started."""

    def __init__(
        self,
        result: "FinalReviewPreflightResult",
        *,
        fingerprint: str | None = None,
    ) -> None:
        self.result = result
        self.fingerprint = fingerprint
        RuntimeError.__init__(self, f"final review preflight denied: {result.error_code}: {result.remediation}")


@dataclass(frozen=True, slots=True)
class FinalReviewPreflightResult:
    outcome: str
    category: str | None = None
    error_code: str | None = None
    affected_record_ids: tuple[str, ...] = ()
    affected_paths: tuple[str, ...] = ()
    remediation: str | None = None

    @property
    def passed(self) -> bool:
        return self.outcome == "passed"


def relevant_record_head(records: Sequence[ArtifactRecord]) -> str:
    """Digest the semantic record head while excluding self-referential checks."""
    facts = [
        {
            "record_id": item.record_id,
            "record_type": item.record_type.value,
            "logical_id": item.logical_id,
            "revision": item.revision,
            "fingerprint": asdict(item.fingerprint),
            "payload": asdict(item.payload),
        }
        for item in records
        if item.record_type not in _TRANSITION_FINGERPRINT_EXCLUDED_TYPES
    ]
    return hashlib.sha256(canonical_json(facts)).hexdigest()


def transition_fingerprint(
    *, provider: str, role: str, operation: str, work_unit_id: str,
    record_head: str, repository_fingerprint: str, input_digest: str,
    policy_digest: str,
) -> str:
    return hashlib.sha256(
        canonical_json(
            {
                "provider": provider,
                "role": role,
                "operation": operation,
                "work_unit_id": work_unit_id,
                "record_head": record_head,
                "repository_fingerprint": repository_fingerprint,
                "input_digest": input_digest,
                "policy_digest": policy_digest,
            }
        )
    ).hexdigest()


def run_final_review_preflight(
    *, state: WorkflowState, records: Sequence[ArtifactRecord],
    measurement_record: ArtifactRecord, repository_paths: tuple[str, ...] = (),
) -> FinalReviewPreflightResult:
    """Validate only facts that must already exist before this exact transition."""
    payload = measurement_record.payload
    if not isinstance(payload, ProviderInputMeasurementPayload):
        return _deny("technical", "MEASUREMENT-TYPE", (measurement_record.record_id,), (), "restore the typed provider-input measurement")
    operation = payload.operation
    if operation not in FINAL_REVIEW_OPERATIONS:
        return _deny("technical", "OPERATION-NOT-FINAL", (), (), "run this preflight only for a final-review operation")
    expected_step = WorkflowStep(operation)
    if state.current_step is not expected_step or state.current_work_unit.kind is not WorkUnitKind.FINAL_REVIEW:
        return _deny("technical", "STATE-TRANSITION-MISMATCH", (), (), "restore the state-v3 final-review cursor")
    if payload.work_unit_id != str(state.current_work_unit_id) or measurement_record.run_id != state.run_id:
        return _deny("technical", "MEASUREMENT-RUN-MISMATCH", (measurement_record.record_id,), (), "recreate the measurement for the active work unit")
    if not payload.allowed:
        return _deny("technical", "MEASUREMENT-DENIED", (measurement_record.record_id,), (), "reduce the lossless provider input or raise its explicit safety budget")
    if any(isinstance(item.payload, WorkflowCompletionPayload) for item in records):
        ids = tuple(item.record_id for item in records if isinstance(item.payload, WorkflowCompletionPayload))
        return _deny("technical", "PREMATURE-COMPLETION", ids, (), "remove or repair the premature completion record")

    records_by_id = {item.record_id: item for item in records}
    record_positions = {item.record_id: index for index, item in enumerate(records)}
    for index, item in enumerate(records):
        if item.run_id != state.run_id:
            return _deny("technical", "FOREIGN-RUN-RECORD", (item.record_id,), (), "restore a single-run record chain")
        if isinstance(item.payload, BindingPayload):
            references = (item.payload.attestation_id, *item.payload.approval_ids)
            missing = tuple(ref for ref in references if ref not in records_by_id)
            if missing:
                return _deny("technical", "MISSING-REFERENCE", (item.record_id, *missing), (), "restore every referenced predecessor record")
            invalid = tuple(
                ref
                for ref, expected_type in (
                    (item.payload.attestation_id, ValidationAttestationPayload),
                    *((ref, ReviewPayload) for ref in item.payload.approval_ids),
                )
                if record_positions[ref] >= index
                or not isinstance(records_by_id[ref].payload, expected_type)
            )
            if invalid:
                return _deny(
                    "technical",
                    "MISSING-REFERENCE",
                    (item.record_id, *invalid),
                    (),
                    "restore every referenced typed predecessor record",
                )
            if any(records_by_id[ref].fingerprint != item.fingerprint for ref in references):
                return _deny("technical", "FINGERPRINT-MISMATCH", (item.record_id, *references), (), "restore fingerprint-identical binding references")

    allowed_patterns = tuple(
        dict.fromkeys(
            (
                *state.task_scope_patterns,
                *(path for item in state.slices for path in item.scope_paths),
            )
        )
    )
    approved_external_paths = set(
        _approved_external_paths(
            state,
            records,
            current_fingerprint=measurement_record.fingerprint.sha256,
        )
    )
    unexpected = tuple(
        sorted(
            path
            for path in set(repository_paths)
            if path not in approved_external_paths
            and not matches_path_patterns(path, allowed_patterns)
        )
    )
    if unexpected:
        return _deny("correction_required", "UNAUTHORIZED-PATH", (), unexpected, "move the changes into an authorized Slice or revert them")

    attestations = [
        item for item in records
        if isinstance(item.payload, ValidationAttestationPayload)
        and item.fingerprint.sha256 == measurement_record.fingerprint.sha256
    ]
    if not attestations:
        return _deny("correction_required", "ATTESTATION-MISSING", (), (), "run the authoritative validation matrix for the current fingerprint")
    attestation = attestations[-1]
    if any(result.outcome != "pass" or result.exit_code != 0 for result in attestation.payload.results):
        return _deny("correction_required", "ATTESTATION-FAILED", (attestation.record_id,), (), "repair the validation failure and attest the current fingerprint")

    if operation == "codex_final_review":
        incomplete = tuple(str(item.slice_id) for item in state.slices if item.commit_ref is None)
        if incomplete:
            return _deny("correction_required", "SLICE-BINDING-MISSING", (), (), "commit and bind every approved implementation Slice")
    elif operation == "claude_final_review":
        codex = [
            item for item in records if isinstance(item.payload, AgentResultPayload)
            and item.payload.role is Role.CODEX and item.payload.work_unit_id == payload.work_unit_id
            and item.fingerprint == measurement_record.fingerprint
        ]
        if not codex or codex[-1].payload.outcome != "ready":
            return _deny("technical", "CODEX-FINAL-RESULT-MISSING", (), (), "persist the ready Codex final report for this fingerprint")
    return FinalReviewPreflightResult("passed")


def _approved_external_paths(
    state: WorkflowState,
    records: Sequence[ArtifactRecord],
    *,
    current_fingerprint: str,
) -> frozenset[str]:
    """Recover exact path grants bound to commits or the active final review.

    Completed Slice grants require both the approved user-gate mirror and a
    commit binding with the identical fingerprint.  A final-review grant cannot
    have a Slice commit binding yet, so it is accepted only from the active
    final-review work unit, for the exact current fingerprint, with its matching
    structured user-gate record.  Historical approvals never widen later
    fingerprints.
    """
    approved_gates = {
        (item.fingerprint.sha256, item.payload.gate_kind)
        for item in records
        if isinstance(item.payload, GatePayload)
        and item.payload.authority is Role.USER
        and item.payload.decision == "approved"
    }
    commit_bindings = {
        (item.fingerprint.sha256, item.payload.target)
        for item in records
        if isinstance(item.payload, BindingPayload)
        and item.payload.binding_kind == "commit"
    }
    slices_by_id = {item.slice_id: item for item in state.slices}
    authorized: set[str] = set()
    for unit in state.work_units:
        if (
            unit.kind not in {WorkUnitKind.SLICE, WorkUnitKind.CORRECTION}
            or unit.status is not WorkUnitStatus.COMPLETED
        ):
            continue
        slice_record = slices_by_id.get(unit.slice_id)
        if (
            slice_record is None
            or slice_record.status is not SliceStatus.COMPLETED
            or slice_record.commit_ref is None
        ):
            continue
        for decision in unit.gate_decisions:
            gate_kind = _EXTERNAL_PATH_GATE_KINDS.get(decision.reason)
            if gate_kind is None or not decision.approved:
                continue
            if (decision.fingerprint, gate_kind) not in approved_gates:
                continue
            if (decision.fingerprint, slice_record.commit_ref) not in commit_bindings:
                continue
            authorized.update(decision.paths)
    if state.current_work_unit.kind is WorkUnitKind.FINAL_REVIEW:
        for decision in state.current_work_unit.gate_decisions:
            gate_kind = _EXTERNAL_PATH_GATE_KINDS.get(decision.reason)
            if (
                gate_kind is None
                or not decision.approved
                or decision.fingerprint != current_fingerprint
                or (decision.fingerprint, gate_kind) not in approved_gates
            ):
                continue
            authorized.update(decision.paths)
    return frozenset(authorized)


def preflight_payload(
    *, measurement_record: ArtifactRecord, result: FinalReviewPreflightResult,
) -> FinalReviewPreflightPayload:
    measurement = measurement_record.payload
    assert isinstance(measurement, ProviderInputMeasurementPayload)
    return FinalReviewPreflightPayload(
        provider=measurement.provider, role=measurement.role, operation=measurement.operation,
        work_unit_id=measurement.work_unit_id, transition_fingerprint=measurement.transition_fingerprint,
        relevant_record_head=measurement.relevant_record_head,
        measurement_record_id=measurement_record.record_id, outcome=result.outcome,
        category=result.category, error_code=result.error_code,
        affected_record_ids=result.affected_record_ids, affected_paths=result.affected_paths,
        remediation=result.remediation,
    )


def _deny(category: str, code: str, record_ids: tuple[str, ...], paths: tuple[str, ...], remediation: str) -> FinalReviewPreflightResult:
    return FinalReviewPreflightResult("denied", category, code, record_ids, paths, remediation)


__all__ = [
    "FINAL_REVIEW_OPERATIONS", "FinalReviewPreflightDenied", "FinalReviewPreflightResult",
    "preflight_payload", "relevant_record_head", "run_final_review_preflight",
    "transition_fingerprint",
]
