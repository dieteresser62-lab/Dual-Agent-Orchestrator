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
from workflow_state import (
    GateReason,
    SliceStatus,
    WorkflowState,
    WorkflowStep,
    WorkUnitKind,
    WorkUnitStatus,
)
from provider_input_budget import ProviderInputBudgetExceeded


FINAL_REVIEW_OPERATIONS = frozenset(
    {"codex_final_review", "claude_final_review", "antigravity_final_review"}
)
_BOOTSTRAP_TYPES = {
    RecordType.PROVIDER_INPUT_MEASUREMENT,
    RecordType.FINAL_REVIEW_PREFLIGHT,
}
_EXTERNAL_PATH_GATE_KINDS = {
    GateReason.UNEXPECTED_FILE: "unexpected-file",
    GateReason.QUOTA_RESUME_DIFF: "quota-resume-diff",
}


class FinalReviewPreflightDenied(ProviderInputBudgetExceeded):
    """A deterministic local denial; no provider process has started."""

    def __init__(self, result: "FinalReviewPreflightResult") -> None:
        self.result = result
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
        if item.record_type not in _BOOTSTRAP_TYPES
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
    for item in records:
        if item.run_id != state.run_id:
            return _deny("technical", "FOREIGN-RUN-RECORD", (item.record_id,), (), "restore a single-run record chain")
        if isinstance(item.payload, BindingPayload):
            references = (item.payload.attestation_id, *item.payload.approval_ids)
            missing = tuple(ref for ref in references if ref not in records_by_id)
            if missing:
                return _deny("technical", "MISSING-REFERENCE", (item.record_id, *missing), (), "restore every referenced predecessor record")
            if any(records_by_id[ref].fingerprint != item.fingerprint for ref in references):
                return _deny("technical", "FINGERPRINT-MISMATCH", (item.record_id, *references), (), "restore fingerprint-identical binding references")

    allowed_paths = set(state.task_scope_patterns)
    allowed_paths.update(path for item in state.slices for path in item.scope_paths)
    allowed_paths.update(_approved_committed_external_paths(state, records))
    unexpected = tuple(sorted(set(repository_paths) - allowed_paths))
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
    else:
        claude = [
            item for item in records if isinstance(item.payload, ReviewPayload)
            and item.payload.reviewer is Role.CLAUDE and item.payload.work_unit_id == payload.work_unit_id
            and item.fingerprint == measurement_record.fingerprint
        ]
        if not claude or claude[-1].payload.verdict != "approved":
            return _deny("technical", "CLAUDE-FINAL-APPROVAL-MISSING", (), (), "obtain Claude approval for this exact fingerprint")
        latest_findings: dict[str, FindingTransitionPayload] = {}
        for item in records:
            if isinstance(item.payload, FindingTransitionPayload):
                latest_findings[item.payload.finding_id] = item.payload
        blockers = tuple(sorted(
            finding_id for finding_id, finding in latest_findings.items()
            if finding.reporter is Role.CLAUDE and finding.severity is FindingSeverity.BLOCKER and finding.finding_status == "open"
        ))
        if blockers:
            return _deny("correction_required", "CLAUDE-BLOCKER-OPEN", blockers, (), "close or escalate every Claude blocker before Antigravity")
    return FinalReviewPreflightResult("passed")


def _approved_committed_external_paths(
    state: WorkflowState,
    records: Sequence[ArtifactRecord],
) -> frozenset[str]:
    """Recover exact path grants that were bound into completed Slice commits.

    A user decision alone is insufficient.  The structured chain must contain
    both its approved user-gate mirror and a commit binding with the identical
    fingerprint whose target is the completed Slice commit.  This keeps the
    final-review scope check symmetric with the earlier Slice commit
    authorization without turning a historical path approval into a general
    task-scope expansion.
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
        if unit.kind is not WorkUnitKind.SLICE or unit.status is not WorkUnitStatus.COMPLETED:
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
