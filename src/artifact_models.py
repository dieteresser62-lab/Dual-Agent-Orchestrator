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

SCHEMA_VERSION = "1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "orchestrator-artifact-v1.schema.json"


class ArtifactValidationError(ValueError):
    """Raised when an artifact violates the v1 domain contract."""


class RecordType(StrEnum):
    TASK = "task"
    PLAN = "plan"
    WORK_UNIT = "work_unit"
    CORRECTION_WORK_UNIT = "correction_work_unit"
    AGENT_RESULT = "agent_result"
    DIAGNOSTIC = "diagnostic"
    REVIEW = "review"
    FINDING_TRANSITION = "finding_transition"
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
    ANTIGRAVITY = "antigravity"
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
    status: ClassVar[str] = "active"
    record_type: ClassVar[RecordType] = RecordType.WORK_UNIT

    def __post_init__(self) -> None:
        _require_identifier(self.slice_id, "slice_id")
        _require_positive(self.round_number, "round_number")
        _require_paths(self.paths)


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
        _require_unique_identifiers(self.finding_ids, "finding_ids")


@dataclass(frozen=True, slots=True)
class AgentResultPayload:
    role: Role
    work_unit_id: str
    outcome: str
    test_files: tuple[str, ...]
    status: ClassVar[str] = "ready"
    record_type: ClassVar[RecordType] = RecordType.AGENT_RESULT

    def __post_init__(self) -> None:
        _require_identifier(self.work_unit_id, "work_unit_id")
        if self.outcome not in {"ready", "not_ready", "stopped"}:
            raise ArtifactValidationError("agent result outcome is invalid")
        _require_paths(self.test_files, allow_empty=True)


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
class ReviewPayload:
    reviewer: Role
    work_unit_id: str
    verdict: str
    finding_ids: tuple[str, ...]
    evidence: str | None
    transport_schema: str | None = None
    request_id: str | None = None
    response_sha256: str | None = None
    status: ClassVar[str] = "decided"
    record_type: ClassVar[RecordType] = RecordType.REVIEW

    def __post_init__(self) -> None:
        if self.reviewer not in {Role.CLAUDE, Role.ANTIGRAVITY}:
            raise ArtifactValidationError("reviewer must be claude or antigravity")
        _require_identifier(self.work_unit_id, "work_unit_id")
        if self.verdict not in {"approved", "denied", "stop"}:
            raise ArtifactValidationError("review verdict is invalid")
        _require_unique_identifiers(self.finding_ids, "finding_ids", allow_empty=True)
        if self.evidence is not None:
            _require_text(self.evidence, "evidence")
        if self.verdict == "approved" and not self.finding_ids and self.evidence is None:
            raise ArtifactValidationError("an approval requires findings or review evidence")
        native_fields = (
            self.transport_schema,
            self.request_id,
            self.response_sha256,
        )
        if any(value is not None for value in native_fields) and not all(
            value is not None for value in native_fields
        ):
            raise ArtifactValidationError(
                "native review transport fields must be present together"
            )
        if self.transport_schema is not None:
            if self.transport_schema != "native-claude-review-v1":
                raise ArtifactValidationError("review transport_schema is unsupported")
            if self.reviewer is not Role.CLAUDE:
                raise ArtifactValidationError(
                    "native Claude review transport requires reviewer=claude"
                )
            assert self.request_id is not None
            assert self.response_sha256 is not None
            if re.fullmatch(r"native-review-request-[0-9a-f]{64}", self.request_id) is None:
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
    status: ClassVar[str] = "recorded"
    record_type: ClassVar[RecordType] = RecordType.FINDING_TRANSITION

    def __post_init__(self) -> None:
        _require_identifier(self.finding_id, "finding_id")
        if self.reporter not in {Role.CLAUDE, Role.ANTIGRAVITY}:
            raise ArtifactValidationError("finding reporter must be claude or antigravity")
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
        if self.provider not in {Role.CODEX, Role.CLAUDE, Role.ANTIGRAVITY} or self.role is not self.provider:
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
    record_type: ClassVar[RecordType] = RecordType.PROVIDER_ATTEMPT

    @property
    def status(self) -> str:
        return self.phase

    def __post_init__(self) -> None:
        if self.provider not in {Role.CODEX, Role.CLAUDE, Role.ANTIGRAVITY} or self.role is not self.provider:
            raise ArtifactValidationError("attempt provider and role must identify one agent")
        _require_identifier(self.operation, "attempt operation")
        _require_identifier(self.work_unit_id, "attempt work_unit_id")
        _require_identifier(self.logical_operation_id, "logical_operation_id")
        _require_sha256(self.binding_fingerprint, "binding_fingerprint")
        _require_identifier(self.measurement_record_id, "measurement_record_id")
        _require_sha256(self.input_digest, "input_digest")
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
                "output", "process", "runtime", "antigravity_tool_schema",
            }:
                raise ArtifactValidationError("failed provider attempt requires a classified failure_kind")
            if (
                self.failure_kind == "antigravity_tool_schema"
                and self.provider is not Role.ANTIGRAVITY
            ):
                raise ArtifactValidationError(
                    "Antigravity tool-schema failure requires the antigravity provider"
                )


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
        if self.provider not in {Role.CODEX, Role.CLAUDE, Role.ANTIGRAVITY} or self.role is not self.provider:
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
    TaskPayload | PlanPayload | WorkUnitPayload | CorrectionWorkUnitPayload
    | AgentResultPayload | DiagnosticPayload | ReviewPayload | FindingTransitionPayload
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
    """Validate a document against the bundled, closed v1 schema offline.

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
    if record_type is RecordType.TASK:
        return TaskPayload(data["target_branch"], tuple(data["scope_paths"]), data["assignment_sha256"])
    if record_type is RecordType.PLAN:
        slices = tuple(SliceSpec(item["slice_id"], item["summary"], tuple(item["paths"])) for item in data["slices"])
        return PlanPayload(data["work_plan_path"], data["approved_plan_commit"], slices)
    if record_type is RecordType.WORK_UNIT:
        return WorkUnitPayload(data["slice_id"], data["round_number"], tuple(data["paths"]))
    if record_type is RecordType.CORRECTION_WORK_UNIT:
        return CorrectionWorkUnitPayload(data["slice_id"], data["round_number"], tuple(data["paths"]), tuple(data["finding_ids"]))
    if record_type is RecordType.AGENT_RESULT:
        return AgentResultPayload(Role(data["role"]), data["work_unit_id"], data["outcome"], tuple(data["test_files"]))
    if record_type is RecordType.DIAGNOSTIC:
        return DiagnosticPayload(Role(data["role"]), data["work_unit_id"], data["attempt"], data["output_sha256"], data["reason"])
    if record_type is RecordType.REVIEW:
        return ReviewPayload(
            Role(data["reviewer"]),
            data["work_unit_id"],
            data["verdict"],
            tuple(data["finding_ids"]),
            data["evidence"],
            data.get("transport_schema"),
            data.get("request_id"),
            data.get("response_sha256"),
        )
    if record_type is RecordType.FINDING_TRANSITION:
        return FindingTransitionPayload(data["finding_id"], Role(data["reporter"]), Role(data["actor"]), data["action"], FindingSeverity(data["severity"]), data["finding_status"], data["rationale"])
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
