"""Versioned, strictly validated records for structured workflow artifacts.

The module deliberately keeps persistence concerns out of the domain model.  A
record is immutable, has a closed typed payload, and can be serialized to stable
canonical JSON.  JSON Schema validation is performed from the bundled schema;
no network resolver is used.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
from typing import Any, ClassVar, Mapping, Sequence, TypeAlias

from schema_validation import (
    SchemaDefinitionError,
    SchemaMismatch,
    check_schema,
    validate_schema_document,
)

SCHEMA_VERSION = "2"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_FINDING_ID_RE = re.compile(r"^C-(0[1-9]|[1-9][0-9]*)$")
_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "orchestrator-artifact-v2.schema.json"


class ArtifactValidationError(ValueError):
    """Raised when an artifact violates the v2 domain contract."""


class RecordType(StrEnum):
    RUN_IDENTITY = "run_identity"
    RUN_PROFILE = "run_profile"
    WORKFLOW_TRANSITION = "workflow_transition"
    WORKFLOW_POLICY = "workflow_policy"
    TASK = "task"
    PLAN = "plan"
    WORK_UNIT = "work_unit"
    CORRECTION_WORK_UNIT = "correction_work_unit"
    AGENT_RESULT = "agent_result"
    DIAGNOSTIC = "diagnostic"
    REVIEW = "review"
    FINDING_TRANSITION = "finding_transition"
    FINDING_HANDOFF_EXPORT = "finding_handoff_export"
    FINDING_HANDOFF_IMPORT = "finding_handoff_import"
    VALIDATION_REQUEST = "validation_request"
    VALIDATION_ATTESTATION = "validation_attestation"
    GATE = "gate"
    BINDING = "binding"
    QUOTA_PAUSE = "quota_pause"
    TRANSIENT_RETRY = "transient_retry"
    RESUME_CHECK = "resume_check"
    WORKFLOW_COMPLETION = "workflow_completion"
    PROVIDER_INPUT_MEASUREMENT = "provider_input_measurement"
    PROVIDER_ATTEMPT = "provider_attempt"
    FINAL_REVIEW_PREFLIGHT = "final_review_preflight"


class FingerprintKind(StrEnum):
    CONTRACT = "contract"
    IMPLEMENTATION = "implementation"


class Role(StrEnum):
    CODEX = "codex"
    CLAUDE = "claude"
    ORCHESTRATOR = "orchestrator"
    USER = "user"


class FindingSeverity(StrEnum):
    BLOCKER = "BLOCKER"
    OBSERVATION = "OBSERVATION"


@dataclass(frozen=True, slots=True)
class Fingerprint:
    kind: FingerprintKind
    sha256: str

    def __post_init__(self) -> None:
        _require_sha256(self.sha256, "fingerprint.sha256")


@dataclass(frozen=True, slots=True)
class CommandSpec:
    family: str
    argv: tuple[str, ...]
    mode: str = "argv"

    def __post_init__(self) -> None:
        _require_identifier(self.family, "command.family")
        if (
            isinstance(self.argv, (str, bytes))
            or not self.argv
            or any(not isinstance(arg, str) or not arg for arg in self.argv)
        ):
            raise ArtifactValidationError("command.argv must be a non-empty list of non-empty strings")
        if self.mode not in {"argv", "legacy_shell"}:
            raise ArtifactValidationError("command.mode must be argv or legacy_shell")
        if any(any(character in arg for character in ("\x00", "\r", "\n")) for arg in self.argv):
            raise ArtifactValidationError("command.argv entries must not contain control separators")
        if self.mode == "legacy_shell" and len(self.argv) != 1:
            raise ArtifactValidationError("legacy_shell command must preserve exactly one unparsed value")


@dataclass(frozen=True, slots=True)
class SliceSpec:
    slice_id: str
    summary: str
    paths: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_identifier(self.slice_id, "slice_id")
        _require_text(self.summary, "summary")
        _require_paths(self.paths)


@dataclass(frozen=True, slots=True)
class RunIdentityPayload:
    task_file: str
    branch: str
    branch_base: str
    execution_mode: str
    audit_report_path: str | None
    status: ClassVar[str] = "bound"
    record_type: ClassVar[RecordType] = RecordType.RUN_IDENTITY

    def __post_init__(self) -> None:
        _require_text(self.task_file, "task_file")
        _require_text(self.branch, "branch")
        _require_text(self.branch_base, "branch_base")
        if self.execution_mode not in {"IMPLEMENT", "PLAN_ONLY"}:
            raise ArtifactValidationError("execution_mode is invalid")
        if self.audit_report_path is not None:
            _require_path(self.audit_report_path)


@dataclass(frozen=True, slots=True)
class RunProfilePayload:
    codex_model: str
    codex_effort: str
    claude_model: str
    claude_effort: str
    status: ClassVar[str] = "bound"
    record_type: ClassVar[RecordType] = RecordType.RUN_PROFILE

    def __post_init__(self) -> None:
        for value, name in (
            (self.codex_model, "codex_model"),
            (self.claude_model, "claude_model"),
        ):
            _require_text(value, name)
            if value != value.strip():
                raise ArtifactValidationError(f"{name} must be canonical")
        for value, name in (
            (self.codex_effort, "codex_effort"),
            (self.claude_effort, "claude_effort"),
        ):
            if value not in {"low", "medium", "high", "xhigh", "max"}:
                raise ArtifactValidationError(f"{name} is unsupported")


_WORKFLOW_STEPS = {
    "codex_plan",  # allowlist:provider -- persisted protocol vocabulary
    "claude_plan_review",  # allowlist:provider -- persisted protocol vocabulary
    "codex_plan_revision",  # allowlist:provider -- persisted protocol vocabulary
    "codex_implementation",  # allowlist:provider -- persisted protocol vocabulary
    "claude_slice_review",  # allowlist:provider -- persisted protocol vocabulary
    "codex_correction",  # allowlist:provider -- persisted protocol vocabulary
    "slice_commit",
    "codex_final_review",  # allowlist:provider -- persisted protocol vocabulary
    "codex_final_correction",  # allowlist:provider -- persisted protocol vocabulary
    "claude_final_review",  # allowlist:provider -- persisted protocol vocabulary
    "completed",
}
_SLICE_STATUSES = {
    "pending",
    "in_progress",
    "awaiting_user_decision",
    "waiting_for_quota",
    "waiting_for_retry",
    "awaiting_resume",
    "completed",
}
_WORK_UNIT_STATUSES = _SLICE_STATUSES


@dataclass(frozen=True, slots=True)
class WorkflowTransitionPayload:
    slice_id: str
    slice_status: str
    work_unit_id: str | None
    step: str | None
    work_unit_status: str | None
    status: ClassVar[str] = "transitioned"
    record_type: ClassVar[RecordType] = RecordType.WORKFLOW_TRANSITION

    def __post_init__(self) -> None:
        _require_identifier(self.slice_id, "transition slice_id")
        if self.slice_status not in _SLICE_STATUSES:
            raise ArtifactValidationError("transition slice_status is invalid")
        work_unit_values = (self.work_unit_id, self.step, self.work_unit_status)
        if all(value is None for value in work_unit_values):
            return
        if any(value is None for value in work_unit_values):
            raise ArtifactValidationError(
                "transition work-unit cursor fields must be all present or all null"
            )
        assert self.work_unit_id is not None
        assert self.step is not None
        assert self.work_unit_status is not None
        _require_identifier(self.work_unit_id, "transition work_unit_id")
        if self.step not in _WORKFLOW_STEPS:
            raise ArtifactValidationError("transition step is invalid")
        if self.work_unit_status not in _WORK_UNIT_STATUSES:
            raise ArtifactValidationError("transition work_unit_status is invalid")


@dataclass(frozen=True, slots=True)
class WorkflowPolicyPayload:
    work_unit_id: str
    implementer_return_count: int
    max_implementer_returns: int
    status: ClassVar[str] = "bound"
    record_type: ClassVar[RecordType] = RecordType.WORKFLOW_POLICY

    def __post_init__(self) -> None:
        _require_identifier(self.work_unit_id, "policy work_unit_id")
        if (
            isinstance(self.implementer_return_count, bool)
            or not isinstance(self.implementer_return_count, int)
            or self.implementer_return_count < 0
        ):
            raise ArtifactValidationError(
                "implementer_return_count must be a non-negative integer"
            )
        _require_positive(self.max_implementer_returns, "max_implementer_returns")
        if self.implementer_return_count > self.max_implementer_returns:
            raise ArtifactValidationError(
                "implementer_return_count exceeds max_implementer_returns"
            )


@dataclass(frozen=True, slots=True)
class TaskPayload:
    target_branch: str
    scope_paths: tuple[str, ...]
    assignment_sha256: str
    status: ClassVar[str] = "accepted"
    record_type: ClassVar[RecordType] = RecordType.TASK

    def __post_init__(self) -> None:
        _require_text(self.target_branch, "target_branch")
        _require_paths(self.scope_paths)
        _require_sha256(self.assignment_sha256, "assignment_sha256")


@dataclass(frozen=True, slots=True)
class PlanPayload:
    work_plan_path: str
    approved_plan_commit: str
    slices: tuple[SliceSpec, ...]
    status: ClassVar[str] = "approved"
    record_type: ClassVar[RecordType] = RecordType.PLAN

    def __post_init__(self) -> None:
        _require_path(self.work_plan_path)
        if not re.fullmatch(r"[0-9a-f]{40}", self.approved_plan_commit):
            raise ArtifactValidationError("approved_plan_commit must be a lowercase 40-character Git SHA")
        if not self.slices:
            raise ArtifactValidationError("plan.slices must not be empty")
        ids = tuple(item.slice_id for item in self.slices)
        if len(ids) != len(set(ids)):
            raise ArtifactValidationError("plan.slices must have unique slice_id values")


@dataclass(frozen=True, slots=True)
class WorkUnitPayload:
    slice_id: str
    round_number: int
    paths: tuple[str, ...]
    open_finding_ids: tuple[str, ...] = ()
    finding_import_record_id: str | None = None
    status: ClassVar[str] = "active"
    record_type: ClassVar[RecordType] = RecordType.WORK_UNIT

    def __post_init__(self) -> None:
        _require_identifier(self.slice_id, "slice_id")
        _require_positive(self.round_number, "round_number")
        _require_paths(self.paths)
        _require_unique_finding_ids(
            self.open_finding_ids, "open_finding_ids", allow_empty=True
        )
        if tuple(sorted(self.open_finding_ids)) != self.open_finding_ids:
            raise ArtifactValidationError("open_finding_ids must be sorted")
        if self.finding_import_record_id is not None:
            _require_identifier(
                self.finding_import_record_id, "finding_import_record_id"
            )


@dataclass(frozen=True, slots=True)
class CorrectionWorkUnitPayload:
    slice_id: str
    round_number: int
    paths: tuple[str, ...]
    finding_ids: tuple[str, ...]
    status: ClassVar[str] = "active"
    record_type: ClassVar[RecordType] = RecordType.CORRECTION_WORK_UNIT

    def __post_init__(self) -> None:
        _require_identifier(self.slice_id, "slice_id")
        _require_positive(self.round_number, "round_number")
        _require_paths(self.paths)
        _require_unique_finding_ids(self.finding_ids, "finding_ids")


@dataclass(frozen=True, slots=True)
class AgentResultPayload:
    role: Role
    work_unit_id: str
    outcome: str
    test_files: tuple[str, ...]
    transport_schema: str
    request_id: str
    response_sha256: str
    status: ClassVar[str] = "ready"
    record_type: ClassVar[RecordType] = RecordType.AGENT_RESULT

    def __post_init__(self) -> None:
        _require_identifier(self.work_unit_id, "work_unit_id")
        if self.outcome not in {"ready", "not_ready", "stopped"}:
            raise ArtifactValidationError("agent result outcome is invalid")
        _require_paths(self.test_files, allow_empty=True)
        if self.transport_schema != "native-codex-v2":
            raise ArtifactValidationError("agent result transport_schema is unsupported")
        if self.role is not Role.CODEX:
            raise ArtifactValidationError(
                "native Codex result transport requires role=codex"
            )
        if (
            not isinstance(self.request_id, str)
            or re.fullmatch(r"native-codex-request-[0-9a-f]{64}", self.request_id)
            is None
        ):
            raise ArtifactValidationError("native Codex result request_id is invalid")
        _require_sha256(self.response_sha256, "response_sha256")


@dataclass(frozen=True, slots=True)
class DiagnosticPayload:
    role: Role
    work_unit_id: str
    attempt: int
    output_sha256: str
    reason: str
    status: ClassVar[str] = "failed"
    record_type: ClassVar[RecordType] = RecordType.DIAGNOSTIC

    def __post_init__(self) -> None:
        _require_identifier(self.work_unit_id, "work_unit_id")
        _require_positive(self.attempt, "attempt")
        _require_sha256(self.output_sha256, "output_sha256")
        _require_text(self.reason, "reason")


@dataclass(frozen=True, slots=True)
class ReviewEvidencePayload:
    dimensions: str
    largest_residual_risk: str
    break_condition: str

    def __post_init__(self) -> None:
        _require_text(self.dimensions, "review_evidence.dimensions")
        _require_text(
            self.largest_residual_risk,
            "review_evidence.largest_residual_risk",
        )
        _require_text(self.break_condition, "review_evidence.break_condition")


@dataclass(frozen=True, slots=True)
class ReviewPayload:
    reviewer: Role
    work_unit_id: str
    verdict: str
    finding_ids: tuple[str, ...]
    evidence: str | None
    transport_schema: str
    request_id: str
    response_sha256: str
    review_evidence: ReviewEvidencePayload | None = None
    red_state_followup_slice: str | None = None
    status: ClassVar[str] = "decided"
    record_type: ClassVar[RecordType] = RecordType.REVIEW

    def __post_init__(self) -> None:
        if self.reviewer is not Role.CLAUDE:
            raise ArtifactValidationError("reviewer must be claude")
        _require_identifier(self.work_unit_id, "work_unit_id")
        if self.verdict not in {"approved", "denied", "stop"}:
            raise ArtifactValidationError("review verdict is invalid")
        _require_unique_finding_ids(self.finding_ids, "finding_ids", allow_empty=True)
        if self.evidence is not None:
            _require_text(self.evidence, "evidence")
        if (
            self.review_evidence is not None
            and not isinstance(self.review_evidence, ReviewEvidencePayload)
        ):
            raise ArtifactValidationError(
                "review_evidence must be a ReviewEvidencePayload"
            )
        if self.evidence is not None and self.review_evidence is not None:
            raise ArtifactValidationError(
                "review cannot combine legacy and structured review evidence"
            )
        if (
            self.verdict == "approved"
            and not self.finding_ids
            and self.evidence is None
            and self.review_evidence is None
        ):
            raise ArtifactValidationError("an approval requires findings or review evidence")
        if self.red_state_followup_slice is not None:
            _require_text(
                self.red_state_followup_slice,
                "red_state_followup_slice",
            )
            if self.verdict != "approved":
                raise ArtifactValidationError(
                    "red-state follow-up authorization requires an approved review"
                )
        if self.transport_schema != "native-claude-review-v2":
            raise ArtifactValidationError("review transport_schema is unsupported")
        if (
            not isinstance(self.request_id, str)
            or re.fullmatch(r"native-review-request-[0-9a-f]{64}", self.request_id)
            is None
        ):
            raise ArtifactValidationError("native review request_id is invalid")
        _require_sha256(self.response_sha256, "response_sha256")


@dataclass(frozen=True, slots=True)
class FindingTransitionPayload:
    finding_id: str
    reporter: Role
    actor: Role
    action: str
    severity: FindingSeverity
    finding_status: str
    rationale: str
    work_unit_id: str | None = None
    summary: str | None = None
    acceptance_test: str | None = None
    origin_slice_id: str | None = None
    origin_round_number: int | None = None
    response_decision: str | None = None
    status: ClassVar[str] = "recorded"
    record_type: ClassVar[RecordType] = RecordType.FINDING_TRANSITION

    def __post_init__(self) -> None:
        _require_finding_id(self.finding_id, "finding_id")
        if self.reporter is not Role.CLAUDE:
            raise ArtifactValidationError("finding reporter must be claude")
        if self.action not in {"opened", "responded", "status_changed", "reclassified"}:
            raise ArtifactValidationError("finding action is invalid")
        if self.finding_status not in {"open", "closed"}:
            raise ArtifactValidationError("finding_status is invalid")
        if self.action in {"opened", "status_changed", "reclassified"} and self.actor != self.reporter:
            raise ArtifactValidationError("only the reporting reviewer may mutate a finding")
        if self.action == "responded" and self.actor is not Role.CODEX:
            raise ArtifactValidationError("only codex may record a finding response")
        if self.action == "responded" and self.finding_status != "open":
            raise ArtifactValidationError("a codex response cannot close a finding")
        _require_text(self.rationale, "rationale")
        if self.work_unit_id is not None:
            _require_identifier(self.work_unit_id, "work_unit_id")
        opening_metadata = (
            self.summary,
            self.acceptance_test,
            self.origin_slice_id,
            self.origin_round_number,
        )
        if any(item is not None for item in opening_metadata):
            if self.action != "opened" or any(item is None for item in opening_metadata):
                raise ArtifactValidationError(
                    "finding opening metadata must be complete and limited to opened transitions"
                )
            assert self.summary is not None
            assert self.acceptance_test is not None
            assert self.origin_slice_id is not None
            assert self.origin_round_number is not None
            _require_text(self.summary, "summary")
            _require_text(self.acceptance_test, "acceptance_test")
            _require_identifier(self.origin_slice_id, "origin_slice_id")
            _require_positive(self.origin_round_number, "origin_round_number")
        if self.response_decision is not None:
            if self.action != "responded" or self.response_decision not in {
                "accepted",
                "rejected",
            }:
                raise ArtifactValidationError(
                    "response_decision must describe a responded transition"
                )


@dataclass(frozen=True, slots=True)
class ImportedFindingTransition:
    """One source transition with its immutable source-record identity."""

    record_id: str
    payload: FindingTransitionPayload

    def __post_init__(self) -> None:
        _require_identifier(self.record_id, "record_id")
        if not self.record_id.startswith("ar1-"):
            raise ArtifactValidationError("imported transition record_id must be an artifact ID")
        if self.payload.work_unit_id is None:
            raise ArtifactValidationError("imported transitions must be structured")


def finding_transition_sequence_sha256(
    transitions: Sequence[ImportedFindingTransition],
) -> str:
    """Digest the ordered canonical source documents, including their IDs."""
    return hashlib.sha256(canonical_json(tuple(transitions))).hexdigest()


@dataclass(frozen=True, slots=True)
class FindingHandoffExportPayload:
    source_run_id: str
    source_head_record_id: str
    approved_plan_commit: str
    approval_review_record_id: str
    finding_transition_record_ids: tuple[str, ...]
    finding_transitions_sha256: str
    target_task_path: str
    target_task_sha256: str
    authority: Role
    status: ClassVar[str] = "exported"
    record_type: ClassVar[RecordType] = RecordType.FINDING_HANDOFF_EXPORT

    def __post_init__(self) -> None:
        _require_identifier(self.source_run_id, "source_run_id")
        _require_identifier(self.source_head_record_id, "source_head_record_id")
        if not re.fullmatch(r"[0-9a-f]{40}", self.approved_plan_commit):
            raise ArtifactValidationError("approved_plan_commit must be a lowercase 40-character Git SHA")
        _require_identifier(self.approval_review_record_id, "approval_review_record_id")
        _require_unique_identifiers(
            self.finding_transition_record_ids,
            "finding_transition_record_ids",
        )
        _require_sha256(self.finding_transitions_sha256, "finding_transitions_sha256")
        _require_path(self.target_task_path)
        _require_sha256(self.target_task_sha256, "target_task_sha256")
        if self.authority is not Role.ORCHESTRATOR:
            raise ArtifactValidationError("finding handoff export authority must be orchestrator")


@dataclass(frozen=True, slots=True)
class FindingHandoffImportPayload:
    source_run_id: str
    source_head_record_id: str
    approved_plan_commit: str
    approval_review_record_id: str
    export_record_id: str
    target_run_id: str
    target_task_sha256: str
    finding_transitions_sha256: str
    transitions: tuple[ImportedFindingTransition, ...]
    authority: Role
    status: ClassVar[str] = "imported"
    record_type: ClassVar[RecordType] = RecordType.FINDING_HANDOFF_IMPORT

    def __post_init__(self) -> None:
        _require_identifier(self.source_run_id, "source_run_id")
        _require_identifier(self.source_head_record_id, "source_head_record_id")
        if not re.fullmatch(r"[0-9a-f]{40}", self.approved_plan_commit):
            raise ArtifactValidationError("approved_plan_commit must be a lowercase 40-character Git SHA")
        _require_identifier(self.approval_review_record_id, "approval_review_record_id")
        _require_identifier(self.export_record_id, "export_record_id")
        _require_identifier(self.target_run_id, "target_run_id")
        _require_sha256(self.target_task_sha256, "target_task_sha256")
        _require_sha256(self.finding_transitions_sha256, "finding_transitions_sha256")
        if not self.transitions:
            raise ArtifactValidationError("finding handoff import transitions must not be empty")
        ids = tuple(item.record_id for item in self.transitions)
        if len(ids) != len(set(ids)):
            raise ArtifactValidationError("imported transition record IDs must be unique")
        if finding_transition_sequence_sha256(self.transitions) != self.finding_transitions_sha256:
            raise ArtifactValidationError("imported finding transition digest does not match")
        if self.authority is not Role.ORCHESTRATOR:
            raise ArtifactValidationError("finding handoff import authority must be orchestrator")


@dataclass(frozen=True, slots=True)
class ValidationRequestPayload:
    commands: tuple[CommandSpec, ...]
    requested_by: Role
    status: ClassVar[str] = "requested"
    record_type: ClassVar[RecordType] = RecordType.VALIDATION_REQUEST

    def __post_init__(self) -> None:
        if not self.commands:
            raise ArtifactValidationError("validation request commands must not be empty")


@dataclass(frozen=True, slots=True)
class ValidationResult:
    command: CommandSpec
    outcome: str
    exit_code: int
    output_sha256: str

    def __post_init__(self) -> None:
        if self.outcome not in {"pass", "fail", "unavailable"}:
            raise ArtifactValidationError("validation outcome is invalid")
        if isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int):
            raise ArtifactValidationError("exit_code must be an integer")
        _require_sha256(self.output_sha256, "output_sha256")


@dataclass(frozen=True, slots=True)
class ValidationAttestationPayload:
    results: tuple[ValidationResult, ...]
    attested_by: Role
    status: ClassVar[str] = "attested"
    record_type: ClassVar[RecordType] = RecordType.VALIDATION_ATTESTATION

    def __post_init__(self) -> None:
        if not self.results:
            raise ArtifactValidationError("validation attestation results must not be empty")


@dataclass(frozen=True, slots=True)
class ProviderInputComponentPayload:
    name: str
    chars: int
    bytes: int

    def __post_init__(self) -> None:
        _require_identifier(self.name, "provider input component name")
        for value, label in ((self.chars, "component chars"), (self.bytes, "component bytes")):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ArtifactValidationError(f"{label} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class ProviderInputMeasurementPayload:
    provider: Role
    role: Role
    operation: str
    work_unit_id: str
    transition_fingerprint: str
    relevant_record_head: str
    input_digest: str
    policy_digest: str
    components: tuple[ProviderInputComponentPayload, ...]
    total_chars: int
    total_bytes: int
    safety_limit_chars: int
    safety_limit_bytes: int
    technical_limit_chars: int | None
    technical_limit_bytes: int | None
    technical_limit_source: str | None
    effective_limit_chars: int
    effective_limit_bytes: int
    allowed: bool
    violated_dimensions: tuple[str, ...]
    char_overage: int
    byte_overage: int
    largest_component: str
    status: ClassVar[str] = "measured"
    record_type: ClassVar[RecordType] = RecordType.PROVIDER_INPUT_MEASUREMENT

    def __post_init__(self) -> None:
        if self.provider not in {Role.CODEX, Role.CLAUDE} or self.role is not self.provider:
            raise ArtifactValidationError("measurement provider and role must identify one agent")
        _require_identifier(self.operation, "measurement operation")
        _require_identifier(self.work_unit_id, "measurement work_unit_id")
        for value, label in (
            (self.transition_fingerprint, "transition_fingerprint"),
            (self.relevant_record_head, "relevant_record_head"),
            (self.input_digest, "input_digest"),
            (self.policy_digest, "policy_digest"),
        ):
            _require_sha256(value, label)
        if not self.components or len({item.name for item in self.components}) != len(self.components):
            raise ArtifactValidationError("measurement components must be non-empty and unique")
        for value, label, positive in (
            (self.total_chars, "total_chars", False), (self.total_bytes, "total_bytes", False),
            (self.safety_limit_chars, "safety_limit_chars", True), (self.safety_limit_bytes, "safety_limit_bytes", True),
            (self.effective_limit_chars, "effective_limit_chars", True), (self.effective_limit_bytes, "effective_limit_bytes", True),
            (self.char_overage, "char_overage", False), (self.byte_overage, "byte_overage", False),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < (1 if positive else 0):
                raise ArtifactValidationError(f"{label} has an invalid size")
        for value in (self.technical_limit_chars, self.technical_limit_bytes):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
                raise ArtifactValidationError("technical limits must be positive or null")
        if (self.technical_limit_chars is None) != (self.technical_limit_bytes is None):
            raise ArtifactValidationError("technical limits must be supplied together")
        if (self.technical_limit_chars is None) != (self.technical_limit_source is None):
            raise ArtifactValidationError("technical limit source must match technical limits")
        if self.technical_limit_source is not None:
            _require_text(self.technical_limit_source, "technical_limit_source")
        if not isinstance(self.allowed, bool):
            raise ArtifactValidationError("allowed must be boolean")
        if any(item not in {"chars", "bytes"} for item in self.violated_dimensions):
            raise ArtifactValidationError("violated_dimensions is invalid")
        _require_identifier(self.largest_component, "largest_component")
        if sum(item.chars for item in self.components) != self.total_chars or sum(item.bytes for item in self.components) != self.total_bytes:
            raise ArtifactValidationError("measurement totals differ from component sizes")
        if self.allowed != (not self.violated_dimensions):
            raise ArtifactValidationError("measurement decision differs from violations")


@dataclass(frozen=True, slots=True)
class ProviderUsagePayload:
    input_tokens: int | None = None
    tool_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    thinking_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    turns: int | None = None
    cost_usd: float | None = None

    def __post_init__(self) -> None:
        for value, label in (
            (self.input_tokens, "input_tokens"),
            (self.tool_input_tokens, "tool_input_tokens"),
            (self.cache_read_input_tokens, "cache_read_input_tokens"),
            (self.cache_creation_input_tokens, "cache_creation_input_tokens"),
            (self.thinking_tokens, "thinking_tokens"),
            (self.output_tokens, "output_tokens"),
            (self.total_tokens, "total_tokens"),
            (self.turns, "turns"),
        ):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ArtifactValidationError(f"provider usage {label} must be non-negative or null")
        if self.cost_usd is not None and (
            isinstance(self.cost_usd, bool)
            or not isinstance(self.cost_usd, (int, float))
            or not math.isfinite(float(self.cost_usd))
            or self.cost_usd < 0
        ):
            raise ArtifactValidationError("provider usage cost_usd must be non-negative or null")


@dataclass(frozen=True, slots=True)
class ProviderAttemptPayload:
    provider: Role
    role: Role
    operation: str
    work_unit_id: str
    logical_operation_id: str
    binding_fingerprint: str
    measurement_record_id: str
    input_digest: str
    attempt_number: int
    phase: str
    started_at: str
    ended_at: str | None
    duration_seconds: float | None
    failure_kind: str | None
    usage: ProviderUsagePayload | None
    model: str = "unknown"
    effort: str = "unknown"
    record_type: ClassVar[RecordType] = RecordType.PROVIDER_ATTEMPT

    @property
    def status(self) -> str:
        return self.phase

    def __post_init__(self) -> None:
        if self.provider not in {Role.CODEX, Role.CLAUDE} or self.role is not self.provider:
            raise ArtifactValidationError("attempt provider and role must identify one agent")
        _require_identifier(self.operation, "attempt operation")
        _require_identifier(self.work_unit_id, "attempt work_unit_id")
        _require_identifier(self.logical_operation_id, "logical_operation_id")
        _require_sha256(self.binding_fingerprint, "binding_fingerprint")
        _require_identifier(self.measurement_record_id, "measurement_record_id")
        _require_sha256(self.input_digest, "input_digest")
        _require_text(self.model, "provider attempt model")
        _require_text(self.effort, "provider attempt effort")
        _require_positive(self.attempt_number, "attempt_number")
        if self.phase not in {"started", "succeeded", "failed"}:
            raise ArtifactValidationError("provider attempt phase is invalid")
        _require_timestamp(self.started_at, "started_at")
        if self.phase == "started":
            if any(value is not None for value in (self.ended_at, self.duration_seconds, self.failure_kind, self.usage)):
                raise ArtifactValidationError("started provider attempt cannot carry terminal fields")
            return
        if self.ended_at is None or self.duration_seconds is None:
            raise ArtifactValidationError("terminal provider attempt requires end and duration")
        _require_timestamp(self.ended_at, "ended_at")
        if (
            isinstance(self.duration_seconds, bool)
            or not isinstance(self.duration_seconds, (int, float))
            or not math.isfinite(float(self.duration_seconds))
            or self.duration_seconds < 0
        ):
            raise ArtifactValidationError("duration_seconds must be non-negative")
        if datetime.fromisoformat(self.ended_at.replace("Z", "+00:00")) < datetime.fromisoformat(self.started_at.replace("Z", "+00:00")):
            raise ArtifactValidationError("provider attempt end cannot precede start")
        if self.phase == "succeeded" and self.failure_kind is not None:
            raise ArtifactValidationError("successful provider attempt cannot carry failure_kind")
        if self.phase == "failed":
            if self.failure_kind not in {
                "quota", "network", "timeout", "permission", "auth", "binary",
                "output", "process", "runtime",
            }:
                raise ArtifactValidationError("failed provider attempt requires a classified failure_kind")


@dataclass(frozen=True, slots=True)
class FinalReviewPreflightPayload:
    provider: Role
    role: Role
    operation: str
    work_unit_id: str
    transition_fingerprint: str
    relevant_record_head: str
    measurement_record_id: str
    outcome: str
    category: str | None
    error_code: str | None
    affected_record_ids: tuple[str, ...]
    affected_paths: tuple[str, ...]
    remediation: str | None
    status: ClassVar[str] = "checked"
    record_type: ClassVar[RecordType] = RecordType.FINAL_REVIEW_PREFLIGHT

    def __post_init__(self) -> None:
        if self.provider not in {Role.CODEX, Role.CLAUDE} or self.role is not self.provider:
            raise ArtifactValidationError("preflight provider and role must identify one agent")
        _require_identifier(self.operation, "preflight operation")
        _require_identifier(self.work_unit_id, "preflight work_unit_id")
        _require_sha256(self.transition_fingerprint, "transition_fingerprint")
        _require_sha256(self.relevant_record_head, "relevant_record_head")
        _require_identifier(self.measurement_record_id, "measurement_record_id")
        if self.outcome not in {"passed", "denied"}:
            raise ArtifactValidationError("preflight outcome is invalid")
        if self.outcome == "passed":
            if any(value is not None for value in (self.category, self.error_code, self.remediation)) or self.affected_record_ids or self.affected_paths:
                raise ArtifactValidationError("passed preflight cannot carry denial details")
        else:
            if self.category not in {"technical", "correction_required"}:
                raise ArtifactValidationError("denied preflight requires a category")
            if self.error_code is None or self.remediation is None:
                raise ArtifactValidationError("denied preflight requires code and remediation")
            _require_identifier(self.error_code, "preflight error_code")
            _require_text(self.remediation, "preflight remediation")
        _require_unique_identifiers(self.affected_record_ids, "affected_record_ids", allow_empty=True)
        _require_paths(self.affected_paths, allow_empty=True)


@dataclass(frozen=True, slots=True)
class GatePayload:
    gate_kind: str
    decision: str
    authority: Role
    rationale: str
    status: ClassVar[str] = "decided"
    record_type: ClassVar[RecordType] = RecordType.GATE

    def __post_init__(self) -> None:
        _require_identifier(self.gate_kind, "gate_kind")
        if self.decision not in {"approved", "rejected", "pending"}:
            raise ArtifactValidationError("gate decision is invalid")
        _require_text(self.rationale, "rationale")


@dataclass(frozen=True, slots=True)
class BindingPayload:
    binding_kind: str
    target: str
    attestation_id: str
    approval_ids: tuple[str, ...]
    status: ClassVar[str] = "bound"
    record_type: ClassVar[RecordType] = RecordType.BINDING

    def __post_init__(self) -> None:
        if self.binding_kind not in {"commit", "plan_commit", "implementation_handoff"}:
            raise ArtifactValidationError("binding_kind is invalid")
        _require_text(self.target, "target")
        _require_identifier(self.attestation_id, "attestation_id")
        _require_unique_identifiers(self.approval_ids, "approval_ids")


@dataclass(frozen=True, slots=True)
class QuotaPausePayload:
    role: Role
    repository_fingerprint: str
    retry_at: str
    status: ClassVar[str] = "paused"
    record_type: ClassVar[RecordType] = RecordType.QUOTA_PAUSE

    def __post_init__(self) -> None:
        _require_sha256(self.repository_fingerprint, "repository_fingerprint")
        _require_timestamp(self.retry_at, "retry_at")


@dataclass(frozen=True, slots=True)
class TransientRetryPayload:
    role: Role
    repository_fingerprint: str
    retry_at: str
    attempt: int
    status: ClassVar[str] = "waiting"
    record_type: ClassVar[RecordType] = RecordType.TRANSIENT_RETRY

    def __post_init__(self) -> None:
        _require_sha256(self.repository_fingerprint, "repository_fingerprint")
        _require_timestamp(self.retry_at, "retry_at")
        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int) or self.attempt < 1:
            raise ArtifactValidationError("transient retry attempt must be positive")


@dataclass(frozen=True, slots=True)
class ResumeCheckPayload:
    expected_head_id: str
    repository_fingerprint: str
    outcome: str
    status: ClassVar[str] = "checked"
    record_type: ClassVar[RecordType] = RecordType.RESUME_CHECK

    def __post_init__(self) -> None:
        _require_identifier(self.expected_head_id, "expected_head_id")
        _require_sha256(self.repository_fingerprint, "repository_fingerprint")
        if self.outcome not in {"matched", "mismatch", "incomplete"}:
            raise ArtifactValidationError("resume outcome is invalid")


@dataclass(frozen=True, slots=True)
class WorkflowCompletionPayload:
    outcome: str
    final_binding_id: str | None
    status: ClassVar[str] = "completed"
    record_type: ClassVar[RecordType] = RecordType.WORKFLOW_COMPLETION

    def __post_init__(self) -> None:
        if self.outcome not in {"completed", "failed", "stopped"}:
            raise ArtifactValidationError("workflow completion outcome is invalid")
        if self.final_binding_id is not None:
            _require_identifier(self.final_binding_id, "final_binding_id")
        if self.outcome == "completed" and self.final_binding_id is None:
            raise ArtifactValidationError("completed workflow requires final_binding_id")


ArtifactPayload: TypeAlias = (
    RunIdentityPayload | RunProfilePayload
    | WorkflowTransitionPayload | WorkflowPolicyPayload
    | TaskPayload | PlanPayload | WorkUnitPayload | CorrectionWorkUnitPayload
    | AgentResultPayload | DiagnosticPayload | ReviewPayload | FindingTransitionPayload
    | FindingHandoffExportPayload | FindingHandoffImportPayload
    | ValidationRequestPayload | ValidationAttestationPayload | GatePayload | BindingPayload
    | QuotaPausePayload | TransientRetryPayload | ResumeCheckPayload
    | WorkflowCompletionPayload
    | ProviderInputMeasurementPayload | ProviderAttemptPayload | FinalReviewPreflightPayload
)


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    record_id: str
    record_type: RecordType
    run_id: str
    logical_id: str
    revision: int
    status: str
    fingerprint: Fingerprint
    predecessor_ids: tuple[str, ...]
    created_at: str
    idempotency_key: str
    payload: ArtifactPayload
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ArtifactValidationError(f"unsupported schema_version: {self.schema_version!r}")
        _require_identifier(self.run_id, "run_id")
        _require_identifier(self.logical_id, "logical_id")
        _require_positive(self.revision, "revision")
        _require_unique_identifiers(self.predecessor_ids, "predecessor_ids", allow_empty=True)
        _require_timestamp(self.created_at, "created_at")
        _require_identifier(self.idempotency_key, "idempotency_key")
        if self.record_type != self.payload.record_type:
            raise ArtifactValidationError("record_type does not match payload type")
        if self.status != self.payload.status:
            raise ArtifactValidationError("record status does not match payload status")
        expected = stable_record_id(self.run_id, self.record_type, self.logical_id, self.revision)
        if self.record_id != expected:
            raise ArtifactValidationError(f"record_id is not stable; expected {expected!r}")
        self.validate_schema()

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        logical_id: str,
        revision: int,
        fingerprint: Fingerprint,
        predecessor_ids: Sequence[str],
        created_at: str,
        idempotency_key: str,
        payload: ArtifactPayload,
    ) -> "ArtifactRecord":
        return cls(
            record_id=stable_record_id(run_id, payload.record_type, logical_id, revision),
            record_type=payload.record_type,
            run_id=run_id,
            logical_id=logical_id,
            revision=revision,
            status=payload.status,
            fingerprint=fingerprint,
            predecessor_ids=tuple(predecessor_ids),
            created_at=created_at,
            idempotency_key=idempotency_key,
            payload=payload,
        )

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> "ArtifactRecord":
        """Rehydrate a record only after the closed JSON contract accepts it."""
        validate_artifact_document(document)
        record_type = RecordType(document["record_type"])
        payload = _payload_from_dict(record_type, document["payload"])
        fingerprint = document["fingerprint"]
        return cls(
            schema_version=document["schema_version"],
            record_id=document["record_id"],
            record_type=record_type,
            run_id=document["run_id"],
            logical_id=document["logical_id"],
            revision=document["revision"],
            status=document["status"],
            fingerprint=Fingerprint(FingerprintKind(fingerprint["kind"]), fingerprint["sha256"]),
            predecessor_ids=tuple(document["predecessor_ids"]),
            created_at=document["created_at"],
            idempotency_key=document["idempotency_key"],
            payload=payload,
        )

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        if self.record_type is RecordType.REVIEW:
            payload = raw["payload"]
            if isinstance(payload, dict):
                if payload.get("review_evidence") is None:
                    payload.pop("review_evidence", None)
                if payload.get("red_state_followup_slice") is None:
                    payload.pop("red_state_followup_slice", None)
        return _json_value(raw)

    def canonical_json(self) -> bytes:
        return canonical_json(self.to_dict())

    def validate_schema(self) -> None:
        validate_artifact_document(self.to_dict())


def stable_record_id(run_id: str, record_type: RecordType | str, logical_id: str, revision: int) -> str:
    """Return a deterministic opaque ID for one logical record revision."""
    kind = record_type.value if isinstance(record_type, RecordType) else record_type
    material = canonical_json([run_id, kind, logical_id, revision])
    return f"ar1-{hashlib.sha256(material).hexdigest()}"


def canonical_json(value: Any) -> bytes:
    """Serialize JSON using the RFC 8785-compatible subset used by our models."""
    return json.dumps(
        _json_value(value), ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def load_schema() -> dict[str, Any]:
    """Load and self-check the bundled schema without remote resolution."""
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    if not isinstance(schema, dict):
        raise ArtifactValidationError("bundled artifact schema must be a JSON object")
    try:
        check_schema(schema)
    except SchemaDefinitionError as error:
        raise ArtifactValidationError(str(error)) from error
    return schema


def validate_artifact_document(document: Mapping[str, Any]) -> None:
    """Validate a document against the bundled, closed v2 schema offline.

    The repository intentionally has no runtime dependencies.  This validator
    implements the Draft 2020-12 keywords used by the bundled schema and the
    schema self-check rejects unknown keywords, preventing an unsupported
    extension from being accepted silently.
    """
    schema = load_schema()
    try:
        validate_schema_document(document, schema)
    except SchemaMismatch as error:
        location = ".".join(str(part) for part in error.path) or "<record>"
        raise ArtifactValidationError(
            f"schema validation failed at {location}: {error.message}"
        ) from None


def _payload_from_dict(record_type: RecordType, raw: Mapping[str, Any]) -> ArtifactPayload:
    data = dict(raw)
    if record_type is RecordType.RUN_IDENTITY:
        return RunIdentityPayload(
            data["task_file"], data["branch"], data["branch_base"],
            data["execution_mode"], data["audit_report_path"],
        )
    if record_type is RecordType.RUN_PROFILE:
        return RunProfilePayload(
            data["codex_model"], data["codex_effort"],
            data["claude_model"], data["claude_effort"],
        )
    if record_type is RecordType.WORKFLOW_TRANSITION:
        return WorkflowTransitionPayload(
            data["slice_id"], data["slice_status"], data["work_unit_id"],
            data["step"], data["work_unit_status"],
        )
    if record_type is RecordType.WORKFLOW_POLICY:
        return WorkflowPolicyPayload(
            data["work_unit_id"], data["implementer_return_count"],
            data["max_implementer_returns"],
        )
    if record_type is RecordType.TASK:
        return TaskPayload(data["target_branch"], tuple(data["scope_paths"]), data["assignment_sha256"])
    if record_type is RecordType.PLAN:
        slices = tuple(SliceSpec(item["slice_id"], item["summary"], tuple(item["paths"])) for item in data["slices"])
        return PlanPayload(data["work_plan_path"], data["approved_plan_commit"], slices)
    if record_type is RecordType.WORK_UNIT:
        return WorkUnitPayload(
            data["slice_id"], data["round_number"], tuple(data["paths"]),
            tuple(data.get("open_finding_ids", ())), data.get("finding_import_record_id"),
        )
    if record_type is RecordType.CORRECTION_WORK_UNIT:
        return CorrectionWorkUnitPayload(data["slice_id"], data["round_number"], tuple(data["paths"]), tuple(data["finding_ids"]))
    if record_type is RecordType.AGENT_RESULT:
        return AgentResultPayload(
            Role(data["role"]),
            data["work_unit_id"],
            data["outcome"],
            tuple(data["test_files"]),
            data["transport_schema"],
            data["request_id"],
            data["response_sha256"],
        )
    if record_type is RecordType.DIAGNOSTIC:
        return DiagnosticPayload(Role(data["role"]), data["work_unit_id"], data["attempt"], data["output_sha256"], data["reason"])
    if record_type is RecordType.REVIEW:
        structured_evidence = data.get("review_evidence")
        return ReviewPayload(
            Role(data["reviewer"]),
            data["work_unit_id"],
            data["verdict"],
            tuple(data["finding_ids"]),
            data["evidence"],
            data["transport_schema"],
            data["request_id"],
            data["response_sha256"],
            (
                None
                if structured_evidence is None
                else ReviewEvidencePayload(
                    structured_evidence["dimensions"],
                    structured_evidence["largest_residual_risk"],
                    structured_evidence["break_condition"],
                )
            ),
            data.get("red_state_followup_slice"),
        )
    if record_type is RecordType.FINDING_TRANSITION:
        return FindingTransitionPayload(
            finding_id=data["finding_id"],
            reporter=Role(data["reporter"]),
            actor=Role(data["actor"]),
            action=data["action"],
            severity=FindingSeverity(data["severity"]),
            finding_status=data["finding_status"],
            rationale=data["rationale"],
            work_unit_id=data.get("work_unit_id"),
            summary=data.get("summary"),
            acceptance_test=data.get("acceptance_test"),
            origin_slice_id=data.get("origin_slice_id"),
            origin_round_number=data.get("origin_round_number"),
            response_decision=data.get("response_decision"),
        )
    if record_type is RecordType.FINDING_HANDOFF_EXPORT:
        return FindingHandoffExportPayload(
            data["source_run_id"], data["source_head_record_id"],
            data["approved_plan_commit"], data["approval_review_record_id"],
            tuple(data["finding_transition_record_ids"]),
            data["finding_transitions_sha256"], data["target_task_path"],
            data["target_task_sha256"], Role(data["authority"]),
        )
    if record_type is RecordType.FINDING_HANDOFF_IMPORT:
        return FindingHandoffImportPayload(
            data["source_run_id"], data["source_head_record_id"],
            data["approved_plan_commit"], data["approval_review_record_id"],
            data["export_record_id"], data["target_run_id"],
            data["target_task_sha256"], data["finding_transitions_sha256"],
            tuple(
                ImportedFindingTransition(
                    item["record_id"],
                    _payload_from_dict(RecordType.FINDING_TRANSITION, item["payload"]),
                )
                for item in data["transitions"]
            ),
            Role(data["authority"]),
        )
    if record_type is RecordType.VALIDATION_REQUEST:
        commands = tuple(CommandSpec(item["family"], tuple(item["argv"]), item["mode"]) for item in data["commands"])
        return ValidationRequestPayload(commands, Role(data["requested_by"]))
    if record_type is RecordType.VALIDATION_ATTESTATION:
        results = tuple(
            ValidationResult(CommandSpec(item["command"]["family"], tuple(item["command"]["argv"]), item["command"]["mode"]), item["outcome"], item["exit_code"], item["output_sha256"])
            for item in data["results"]
        )
        return ValidationAttestationPayload(results, Role(data["attested_by"]))
    if record_type is RecordType.GATE:
        return GatePayload(data["gate_kind"], data["decision"], Role(data["authority"]), data["rationale"])
    if record_type is RecordType.BINDING:
        return BindingPayload(data["binding_kind"], data["target"], data["attestation_id"], tuple(data["approval_ids"]))
    if record_type is RecordType.QUOTA_PAUSE:
        return QuotaPausePayload(Role(data["role"]), data["repository_fingerprint"], data["retry_at"])
    if record_type is RecordType.TRANSIENT_RETRY:
        return TransientRetryPayload(
            Role(data["role"]),
            data["repository_fingerprint"],
            data["retry_at"],
            data["attempt"],
        )
    if record_type is RecordType.RESUME_CHECK:
        return ResumeCheckPayload(data["expected_head_id"], data["repository_fingerprint"], data["outcome"])
    if record_type is RecordType.WORKFLOW_COMPLETION:
        return WorkflowCompletionPayload(data["outcome"], data["final_binding_id"])
    if record_type is RecordType.PROVIDER_INPUT_MEASUREMENT:
        return ProviderInputMeasurementPayload(
            Role(data["provider"]), Role(data["role"]), data["operation"], data["work_unit_id"],
            data["transition_fingerprint"], data["relevant_record_head"], data["input_digest"], data["policy_digest"],
            tuple(ProviderInputComponentPayload(item["name"], item["chars"], item["bytes"]) for item in data["components"]),
            data["total_chars"], data["total_bytes"], data["safety_limit_chars"], data["safety_limit_bytes"],
            data["technical_limit_chars"], data["technical_limit_bytes"], data["technical_limit_source"],
            data["effective_limit_chars"], data["effective_limit_bytes"], data["allowed"],
            tuple(data["violated_dimensions"]), data["char_overage"], data["byte_overage"], data["largest_component"],
        )
    if record_type is RecordType.PROVIDER_ATTEMPT:
        usage = data["usage"]
        return ProviderAttemptPayload(
            Role(data["provider"]), Role(data["role"]), data["operation"], data["work_unit_id"],
            data["logical_operation_id"], data["binding_fingerprint"], data["measurement_record_id"],
            data["input_digest"], data["attempt_number"], data["phase"], data["started_at"],
            data["ended_at"], data["duration_seconds"], data["failure_kind"],
            ProviderUsagePayload(**usage) if usage is not None else None,
            data["model"], data["effort"],
        )
    if record_type is RecordType.FINAL_REVIEW_PREFLIGHT:
        return FinalReviewPreflightPayload(
            Role(data["provider"]), Role(data["role"]), data["operation"], data["work_unit_id"],
            data["transition_fingerprint"], data["relevant_record_head"], data["measurement_record_id"],
            data["outcome"], data["category"], data["error_code"], tuple(data["affected_record_ids"]),
            tuple(data["affected_paths"]), data["remediation"],
        )
    raise ArtifactValidationError(f"unsupported record_type: {record_type}")


def _json_value(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if hasattr(value, "__dataclass_fields__"):
        return _json_value(asdict(value))
    return value


def _require_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ArtifactValidationError(f"{name} must be a non-empty string")


def _require_identifier(value: str, name: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise ArtifactValidationError(f"{name} is not a canonical identifier")


def _require_finding_id(value: str, name: str) -> None:
    if not isinstance(value, str) or _FINDING_ID_RE.fullmatch(value) is None:
        raise ArtifactValidationError(f"{name} must be a canonical C-* finding ID")


def _require_sha256(value: str, name: str) -> None:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ArtifactValidationError(f"{name} must be a lowercase SHA-256 digest")


def _require_positive(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ArtifactValidationError(f"{name} must be a positive integer")


def _require_timestamp(value: str, name: str) -> None:
    _require_text(value, name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ArtifactValidationError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ArtifactValidationError(f"{name} must include a timezone")


def _require_path(value: str) -> None:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ArtifactValidationError("paths must be non-empty POSIX paths")
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix() or any(part in {"", ".", ".."} for part in path.parts):
        raise ArtifactValidationError(f"path is not canonical repository-relative POSIX: {value!r}")


def _require_paths(paths: Sequence[str], *, allow_empty: bool = False) -> None:
    if isinstance(paths, (str, bytes)) or (not paths and not allow_empty):
        raise ArtifactValidationError("paths must be a JSON-style list, not Markdown or text")
    for path in paths:
        _require_path(path)
    if len(paths) != len(set(paths)):
        raise ArtifactValidationError("paths must be unique")


def _require_unique_identifiers(
    values: Sequence[str], name: str, *, allow_empty: bool = False,
) -> None:
    if isinstance(values, (str, bytes)) or (not values and not allow_empty):
        raise ArtifactValidationError(f"{name} must be a non-empty list")
    for value in values:
        _require_identifier(value, name)
    if len(values) != len(set(values)):
        raise ArtifactValidationError(f"{name} must contain unique values")


def _require_unique_finding_ids(
    values: Sequence[str], name: str, *, allow_empty: bool = False,
) -> None:
    if isinstance(values, (str, bytes)) or (not values and not allow_empty):
        raise ArtifactValidationError(f"{name} must be a non-empty list")
    for value in values:
        _require_finding_id(value, name)
    if len(values) != len(set(values)):
        raise ArtifactValidationError(f"{name} must contain unique values")
