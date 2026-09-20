"""Versioned, strictly validated records for structured workflow artifacts.

The module deliberately keeps persistence concerns out of the domain model.  A
record is immutable, has a closed typed payload, and can be serialized to stable
canonical JSON.  JSON Schema validation is performed from the bundled schema;
no network resolver is used.
"""

from __future__ import annotations

import base64
import binascii
import copy
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
from functools import lru_cache
from typing import Any, Callable, ClassVar, Mapping, Sequence, TypeAlias

from acceptance_criteria import (
    AcceptanceCriterion,
    acceptance_criteria_from_documents,
    validate_acceptance_criteria,
)
from schema_validation import (
    SchemaDefinitionError,
    SchemaMismatch,
    check_schema,
    validate_schema_document,
)
from finding_order import sorted_finding_ids
from finding_responsibility import (
    FindingResponsibility,
    parse_responsibility,
    responsibility_document,
)
from orchestrator_diagnostics import (
    ORCHESTRATOR_DIAGNOSTIC_TEXTS,
    STRUCTURED_OUTPUT_DIAGNOSTIC_CODE,
)
import native_finding_decisions

SCHEMA_VERSION = "2"
STATE_PROJECTION_REDUCER_VERSION = (
    "structured-v2-schema-2-state-v3-joint-67-68-affected-paths-v1"
)
PRE_AFFECTED_PATHS_REDUCER_VERSION = (
    "structured-v2-schema-2-state-v3-joint-67-68-v1"
)
PRE_JOINT_67_68_REDUCER_VERSION = "structured-v2-schema-2-state-v3-v1"
LEGACY_CHAIN_VERIFIER = "scripts/verify_legacy_chain.py"
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
    WORKFLOW_EVENT = "workflow_event"
    WORKFLOW_POLICY = "workflow_policy"
    SLICE_BOUNDARY = "slice_boundary"
    TASK = "task"
    PLAN = "plan"
    WORK_UNIT = "work_unit"
    CORRECTION_WORK_UNIT = "correction_work_unit"
    AGENT_RESULT = "agent_result"
    DIAGNOSTIC = "diagnostic"
    REVIEW = "review"
    BRANCH_DISCOVERY_COMPLETED = "branch_discovery_completed"
    REVIEW_ANCHOR = "review_anchor"
    REVIEW_VALIDATION_BINDING = "review_validation_binding"
    FINDING_TRANSITION = "finding_transition"
    FINDING_HANDOFF_EXPORT = "finding_handoff_export"
    FINDING_HANDOFF_IMPORT = "finding_handoff_import"
    BRANCH_DISCOVERY_HANDOFF_EXPORT = "branch_discovery_handoff_export"
    BRANCH_DISCOVERY_HANDOFF_IMPORT = "branch_discovery_handoff_import"
    PLAN_ASSIGNMENT = "plan_assignment"
    REMEDIATION_COHORT_CHECKPOINT = "remediation_cohort_checkpoint"
    NO_IMPLEMENTATION_REQUIRED = "no_implementation_required"
    CLOSED_FINDING_OCCURRENCE = "closed_finding_occurrence"
    VALIDATION_REQUEST = "validation_request"
    VALIDATION_CONTENT = "validation_content"
    VALIDATION_ATTESTATION = "validation_attestation"
    PROVIDER_CONTENT = "provider_content"
    REVIEW_PACKET = "review_packet"
    GATE = "gate"
    GATE_TRANSITION = "gate_transition"
    GATE_DECISION = "gate_decision"
    BINDING = "binding"
    INVOCATION_FAILURE = "invocation_failure"
    QUOTA_PAUSE = "quota_pause"
    TRANSIENT_RETRY = "transient_retry"
    RESUME_CHECK = "resume_check"
    WORKFLOW_COMPLETION = "workflow_completion"
    PROVIDER_INPUT_MEASUREMENT = "provider_input_measurement"
    PROVIDER_ATTEMPT = "provider_attempt"
    FINAL_REVIEW_PREFLIGHT = "final_review_preflight"
    SIDE_EFFECT = "side_effect"


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
    acceptance_criteria: tuple[AcceptanceCriterion, ...] = ()

    def __post_init__(self) -> None:
        _require_identifier(self.slice_id, "slice_id")
        _require_text(self.summary, "summary")
        _require_paths(self.paths)
        try:
            validate_acceptance_criteria(self.slice_id, self.acceptance_criteria)
        except ValueError as exc:
            raise ArtifactValidationError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class RunIdentityPayload:
    task_file: str
    branch: str
    branch_base: str
    first_slice_start_commit: str
    execution_mode: str
    audit_report_path: str | None
    status: ClassVar[str] = "bound"
    record_type: ClassVar[RecordType] = RecordType.RUN_IDENTITY

    def __post_init__(self) -> None:
        _require_text(self.task_file, "task_file")
        _require_text(self.branch, "branch")
        _require_text(self.branch_base, "branch_base")
        if not re.fullmatch(r"[0-9a-f]{40}", self.first_slice_start_commit):
            raise ArtifactValidationError(
                "first_slice_start_commit must be a lowercase 40-character Git SHA"
            )
        if self.execution_mode not in {
            "IMPLEMENT",
            "PLAN_ONLY",
            "BRANCH_DISCOVERY",
        }:
            raise ArtifactValidationError("execution_mode is invalid")
        if self.audit_report_path is not None:
            _require_path(self.audit_report_path)


@dataclass(frozen=True, slots=True)
class RoleProfilePayload:
    model: str
    effort: str

    def __post_init__(self) -> None:
        _require_text(self.model, "model")
        if self.model != self.model.strip():
            raise ArtifactValidationError("model must be canonical")
        if self.effort not in {"low", "medium", "high", "xhigh", "max"}:
            raise ArtifactValidationError("effort is unsupported")


@dataclass(frozen=True, slots=True)
class FamilyBindingPayload:
    """Immutable cross-run identity and authorization facts for one run family."""

    family_id: str
    family_base_commit: str
    family_authorized_change_set: tuple[str, ...]
    predecessor_run_id: str | None
    predecessor_head_record_id: str | None
    cycle_number: int
    current_plan_commit: str | None
    current_implementation_commit: str | None

    def __post_init__(self) -> None:
        _require_identifier(self.family_id, "family_id")
        _require_git_sha(self.family_base_commit, "family_base_commit")
        _require_paths(self.family_authorized_change_set)
        if tuple(sorted(self.family_authorized_change_set)) != (
            self.family_authorized_change_set
        ):
            raise ArtifactValidationError(
                "family_authorized_change_set must be sorted"
            )
        predecessor_values = (
            self.predecessor_run_id,
            self.predecessor_head_record_id,
        )
        if any(value is None for value in predecessor_values) != all(
            value is None for value in predecessor_values
        ):
            missing = (
                "predecessor_run_id"
                if self.predecessor_run_id is None
                else "predecessor_head_record_id"
            )
            raise ArtifactValidationError(
                f"family binding is missing required field {missing}"
            )
        if self.predecessor_run_id is not None:
            _require_identifier(self.predecessor_run_id, "predecessor_run_id")
            if re.fullmatch(
                r"ar1-[0-9a-f]{64}", self.predecessor_head_record_id or ""
            ) is None:
                raise ArtifactValidationError(
                    "predecessor_head_record_id must be an artifact record id"
                )
        _require_positive(self.cycle_number, "cycle_number")
        for value, name in (
            (self.current_plan_commit, "current_plan_commit"),
            (
                self.current_implementation_commit,
                "current_implementation_commit",
            ),
        ):
            if value is not None:
                _require_git_sha(value, name)


def family_binding_document(binding: FamilyBindingPayload) -> dict[str, Any]:
    """Return the canonical JSON-compatible document for a family fact."""

    if not isinstance(binding, FamilyBindingPayload):
        raise ArtifactValidationError("family binding is invalid")
    return _json_value(asdict(binding))


@dataclass(frozen=True, slots=True)
class RunProfilePayload:
    implementer: RoleProfilePayload
    reviewer: RoleProfilePayload
    orchestrator_code_version: str = "0" * 64
    reducer_version: str = STATE_PROJECTION_REDUCER_VERSION
    family_binding: FamilyBindingPayload | None = None
    status: ClassVar[str] = "bound"
    record_type: ClassVar[RecordType] = RecordType.RUN_PROFILE

    def __post_init__(self) -> None:
        if not isinstance(self.implementer, RoleProfilePayload):
            raise ArtifactValidationError("implementer profile is invalid")
        if not isinstance(self.reviewer, RoleProfilePayload):
            raise ArtifactValidationError("reviewer profile is invalid")
        _require_sha256(
            self.orchestrator_code_version, "orchestrator_code_version"
        )
        if self.reducer_version != STATE_PROJECTION_REDUCER_VERSION:
            raise ArtifactValidationError(
                "run profile reducer_version is unsupported for resume; "
                f"inspect historical chains with {LEGACY_CHAIN_VERIFIER}"
            )
        if self.family_binding is not None and not isinstance(
            self.family_binding, FamilyBindingPayload
        ):
            raise ArtifactValidationError("run profile family_binding is invalid")


_WORKFLOW_STEPS = {
    "codex_plan",  # allowlist:provider -- persisted protocol vocabulary
    "claude_plan_review",  # allowlist:provider -- persisted protocol vocabulary
    "codex_plan_revision",  # allowlist:provider -- persisted protocol vocabulary
    "codex_implementation",  # allowlist:provider -- persisted protocol vocabulary
    "claude_slice_review",  # allowlist:provider -- persisted protocol vocabulary
    "codex_correction",  # allowlist:provider -- persisted protocol vocabulary
    "slice_commit",
    "claude_branch_discovery",  # allowlist:provider -- persisted protocol vocabulary
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
_GATE_STATUSES = {
    "clear",
    "awaiting_user_decision",
    "waiting_for_quota",
    "waiting_for_retry",
    "awaiting_resume",
}
_GATE_REASONS = {
    "none",
    "iteration_limit",
    "test_change",
    "stop_request",
    "unexpected_file",
    "anchor_change",
    "manual_slice",
    "plan_approval",
    "quota",
    "instance_failure",
    "bootstrap_check",
    "quota_resume_diff",
}


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
class WorkflowEventPayload:
    """Presentation-neutral ordering metadata for an already recorded fact.

    Event records deliberately carry no review, validation, or transition
    content.  ``record_refs`` points backwards to the typed records which own
    that content, keeping the event stream useful for audit ordering without
    creating a second semantic authority.
    """

    event_kind: str
    work_unit_id: str | None
    slice_id: str
    round_number: int | None
    record_refs: tuple[str, ...]
    status: ClassVar[str] = "recorded"
    record_type: ClassVar[RecordType] = RecordType.WORKFLOW_EVENT

    def __post_init__(self) -> None:
        if self.event_kind not in {"run", "transition", "validation", "review"}:
            raise ArtifactValidationError("workflow event kind is invalid")
        _require_identifier(self.slice_id, "workflow event slice_id")
        _require_unique_identifiers(self.record_refs, "workflow event record_refs")
        if not self.record_refs:
            raise ArtifactValidationError("workflow event record_refs must be non-empty")
        if self.work_unit_id is not None:
            _require_identifier(self.work_unit_id, "workflow event work_unit_id")
        if self.round_number is not None:
            _require_positive(self.round_number, "workflow event round_number")
        if self.event_kind in {"validation", "review"} and (
            self.work_unit_id is None or self.round_number is None
        ):
            raise ArtifactValidationError(
                "validation and review events require work-unit and round identity"
            )
        if self.event_kind == "transition" and self.round_number is not None:
            raise ArtifactValidationError(
                "transition events do not carry a redundant round number"
            )
        if self.event_kind == "run" and (
            self.work_unit_id is not None or self.round_number is not None
        ):
            raise ArtifactValidationError(
                "run events do not carry work-unit or round identity"
            )


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
class SliceBoundaryPayload:
    slice_id: str
    start_commit: str
    scope_change_groups: tuple[tuple[str, ...], ...]
    start_fingerprint: str
    status: ClassVar[str] = "bound"
    record_type: ClassVar[RecordType] = RecordType.SLICE_BOUNDARY

    def __post_init__(self) -> None:
        _require_identifier(self.slice_id, "slice boundary slice_id")
        if not re.fullmatch(r"[0-9a-f]{40}", self.start_commit):
            raise ArtifactValidationError(
                "slice boundary start_commit must be a lowercase 40-character Git SHA"
            )
        if (
            not self.scope_change_groups
            or any(not group for group in self.scope_change_groups)
        ):
            raise ArtifactValidationError(
                "slice boundary scope_change_groups must be non-empty"
            )
        for group in self.scope_change_groups:
            _require_paths(group)
            if tuple(sorted(group)) != group:
                raise ArtifactValidationError(
                    "slice boundary scope_change_groups entries must be sorted"
                )
        if (
            tuple(sorted(self.scope_change_groups)) != self.scope_change_groups
            or len(set(self.scope_change_groups)) != len(self.scope_change_groups)
        ):
            raise ArtifactValidationError(
                "slice boundary scope_change_groups must be sorted and unique"
            )
        flattened = tuple(
            path for group in self.scope_change_groups for path in group
        )
        if len(flattened) != len(set(flattened)):
            raise ArtifactValidationError(
                "slice boundary scope_change_groups must partition unique paths"
            )
        _require_sha256(self.start_fingerprint, "slice boundary start_fingerprint")


def build_family_authorized_change_set(
    *,
    inherited_change_set: Sequence[str] = (),
    slice_boundaries: Sequence[SliceBoundaryPayload] = (),
    work_plan_paths: Sequence[str] = (),
    commit_authorized_control_artifacts: Sequence[str] = (),
) -> tuple[str, ...]:
    """Build the exact cumulative family allowlist from record-owned inputs."""

    for paths in (
        inherited_change_set,
        work_plan_paths,
        commit_authorized_control_artifacts,
    ):
        _require_paths(paths, allow_empty=True)
    if any(not isinstance(item, SliceBoundaryPayload) for item in slice_boundaries):
        raise ArtifactValidationError(
            "slice_boundaries must contain only SliceBoundaryPayload values"
        )
    return tuple(
        sorted(
            {
                *inherited_change_set,
                *work_plan_paths,
                *commit_authorized_control_artifacts,
                *(
                    path
                    for boundary in slice_boundaries
                    for group in boundary.scope_change_groups
                    for path in group
                ),
            }
        )
    )


@dataclass(frozen=True, slots=True)
class TaskPayload:
    target_branch: str
    scope_paths: tuple[str, ...]
    assignment_sha256: str
    work_plan_path: str | None = None
    status: ClassVar[str] = "accepted"
    record_type: ClassVar[RecordType] = RecordType.TASK

    def __post_init__(self) -> None:
        _require_text(self.target_branch, "target_branch")
        _require_paths(self.scope_paths)
        _require_sha256(self.assignment_sha256, "assignment_sha256")
        if self.work_plan_path is not None:
            _require_path(self.work_plan_path)


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
        if sorted_finding_ids(self.open_finding_ids) != self.open_finding_ids:
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
class PlanTreatmentProposalPayload:
    signature: str
    finding_ids: tuple[str, ...]
    treatment_kind: str
    closing_slice_ids: tuple[str, ...] = ()
    no_code_reason: str | None = None
    evidence: str | None = None
    evidence_paths: tuple[str, ...] = ()
    affected_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_sha256(self.signature, "plan treatment proposal signature")
        _require_unique_finding_ids(
            self.finding_ids,
            "plan treatment proposal finding_ids",
            allow_empty=False,
        )
        if self.finding_ids != sorted_finding_ids(self.finding_ids):
            raise ArtifactValidationError(
                "plan treatment proposal finding_ids must be ordered by finding_id"
            )
        if self.treatment_kind == "implementation":
            _require_unique_identifiers(
                self.closing_slice_ids,
                "plan treatment proposal closing_slice_ids",
                allow_empty=False,
            )
            if len(self.closing_slice_ids) != 1:
                raise ArtifactValidationError(
                    "implementation treatment requires exactly one closing Slice"
                )
            if (
                self.no_code_reason is not None
                or self.evidence is not None
                or self.evidence_paths
                or self.affected_paths
            ):
                raise ArtifactValidationError(
                    "implementation treatment forbids No-Code disposition fields"
                )
            return
        if self.treatment_kind != "no_code":
            raise ArtifactValidationError("plan treatment proposal kind is invalid")
        if self.closing_slice_ids:
            raise ArtifactValidationError(
                "No-Code disposition forbids closing Slice ids"
            )
        if self.no_code_reason not in {
            "no_defect",
            "out_of_scope",
            "already_fixed",
        }:
            raise ArtifactValidationError("No-Code disposition reason is invalid")
        _require_text(self.evidence, "No-Code disposition evidence")
        _require_paths(self.evidence_paths)
        if self.no_code_reason == "already_fixed":
            _require_paths(self.affected_paths)
        elif self.affected_paths:
            raise ArtifactValidationError(
                "only an already-fixed disposition may name affected_paths"
            )


@dataclass(frozen=True, slots=True)
class AgentResultPayload:
    role: Role
    work_unit_id: str
    outcome: str
    test_files: tuple[str, ...]
    transport_schema: str
    request_id: str
    response_sha256: str
    slice_plan: tuple[SliceSpec, ...] = ()
    plan_treatments: tuple[PlanTreatmentProposalPayload, ...] = ()
    plan_completion: str | None = None
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
        if not isinstance(self.slice_plan, tuple) or any(
            not isinstance(item, SliceSpec) for item in self.slice_plan
        ):
            raise ArtifactValidationError("agent result slice_plan is invalid")
        slice_ids = tuple(item.slice_id for item in self.slice_plan)
        if len(slice_ids) != len(set(slice_ids)):
            raise ArtifactValidationError("agent result slice_plan ids must be unique")
        if any(
            not isinstance(item, PlanTreatmentProposalPayload)
            for item in self.plan_treatments
        ):
            raise ArtifactValidationError(
                "agent result plan treatments must be typed"
            )
        signatures = tuple(item.signature for item in self.plan_treatments)
        if signatures != tuple(sorted(set(signatures))):
            raise ArtifactValidationError(
                "agent result plan treatments must be sorted and unique by signature"
            )
        if self.plan_completion is not None:
            try:
                completion = native_finding_decisions.PlanCompletionKind(
                    self.plan_completion
                )
            except ValueError as exc:
                raise ArtifactValidationError(
                    "agent result plan completion is invalid"
                ) from exc
            has_implementation = any(
                item.treatment_kind == "implementation"
                for item in self.plan_treatments
            )
            if (
                completion
                is native_finding_decisions.PlanCompletionKind.NO_IMPLEMENTATION_REQUIRED
                and (has_implementation or self.slice_plan)
            ):
                raise ArtifactValidationError(
                    "NO_IMPLEMENTATION_REQUIRED forbids implementation work"
                )
            if (
                completion
                is native_finding_decisions.PlanCompletionKind.IMPLEMENTATION_REQUIRED
                and not self.slice_plan
            ):
                raise ArtifactValidationError(
                    "IMPLEMENTATION_REQUIRED requires at least one Slice"
                )


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
class ReviewStopRequestPayload:
    rule_id: str
    rationale: str
    remediation_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text(self.rule_id, "stop_request.rule_id")
        _require_text(self.rationale, "stop_request.rationale")
        _require_paths(self.remediation_paths, allow_empty=True)


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
    test_files: tuple[str, ...] = ()
    pre_mortem: str | None = None
    stop_request: ReviewStopRequestPayload | None = None
    plan_treatment_decisions: tuple[PlanTreatmentDecisionPayload, ...] = ()
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
        _require_paths(self.test_files, allow_empty=True)
        if self.pre_mortem is not None:
            _require_text(self.pre_mortem, "pre_mortem")
        if self.stop_request is not None and not isinstance(
            self.stop_request, ReviewStopRequestPayload
        ):
            raise ArtifactValidationError(
                "stop_request must be a ReviewStopRequestPayload"
            )
        if (self.verdict == "stop") != (self.stop_request is not None):
            raise ArtifactValidationError(
                "review stop verdict and structured stop request differ"
            )
        if any(
            not isinstance(item, PlanTreatmentDecisionPayload)
            for item in self.plan_treatment_decisions
        ):
            raise ArtifactValidationError(
                "plan treatment decisions must be typed"
            )
        decision_signatures = tuple(
            item.signature for item in self.plan_treatment_decisions
        )
        if decision_signatures != tuple(sorted(set(decision_signatures))):
            raise ArtifactValidationError(
                "plan treatment decisions must be sorted and unique by signature"
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
class BranchDiscoveryFindingPayload:
    finding_id: str
    severity: FindingSeverity
    summary: str
    acceptance_test: str
    predecessor_finding_ref: str | None = None
    evidence_anchor_sha256: str | None = None
    affected_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_finding_id(self.finding_id, "branch discovery finding_id")
        if not isinstance(self.severity, FindingSeverity):
            raise ArtifactValidationError(
                "branch discovery finding severity is invalid"
            )
        _require_text(self.summary, "branch discovery finding summary")
        _require_text(
            self.acceptance_test, "branch discovery finding acceptance_test"
        )
        _require_paths(self.affected_paths, allow_empty=True)
        if self.predecessor_finding_ref is not None:
            _require_finding_id(
                self.predecessor_finding_ref,
                "branch discovery predecessor_finding_ref",
            )
            if self.predecessor_finding_ref == self.finding_id:
                raise ArtifactValidationError(
                    "branch discovery Finding cannot be its own predecessor"
                )
            _require_sha256(
                self.evidence_anchor_sha256,
                "branch discovery generation evidence anchor",
            )
        elif self.evidence_anchor_sha256 is not None:
            raise ArtifactValidationError(
                "branch discovery evidence anchor requires a predecessor"
            )


@dataclass(frozen=True, slots=True)
class BranchDiscoveryOccurrencePayload:
    finding_id: str
    rationale: str
    evidence_anchor_sha256: str | None = None

    def __post_init__(self) -> None:
        _require_finding_id(self.finding_id, "branch discovery occurrence finding_id")
        _require_text(self.rationale, "branch discovery occurrence rationale")
        if self.evidence_anchor_sha256 is not None:
            _require_sha256(
                self.evidence_anchor_sha256,
                "branch discovery occurrence evidence anchor",
            )


@dataclass(frozen=True, slots=True)
class BranchDiscoveryCompletedPayload:
    """Record-native E6 delivery; completion is intentionally not approval."""

    reviewer: Role
    work_unit_id: str
    new_findings: tuple[BranchDiscoveryFindingPayload, ...]
    occurrences: tuple[BranchDiscoveryOccurrencePayload, ...]
    review_evidence: ReviewEvidencePayload
    pre_mortem: str
    validation_attestation_record_id: str
    reviewed_head_commit: str
    transport_schema: str
    request_id: str
    response_sha256: str
    scan_complete: bool | None = None
    status: ClassVar[str] = "completed"
    record_type: ClassVar[RecordType] = RecordType.BRANCH_DISCOVERY_COMPLETED

    def __post_init__(self) -> None:
        if self.reviewer is not Role.CLAUDE:  # allowlist:provider -- reviewer authority
            raise ArtifactValidationError(
                "branch discovery completion reviewer must be claude"  # allowlist:provider -- diagnostic role
            )
        if self.scan_complete is not True:
            raise ArtifactValidationError(
                "branch discovery completion requires scan_complete=true"
            )
        _require_identifier(self.work_unit_id, "work_unit_id")
        new_ids = tuple(item.finding_id for item in self.new_findings)
        if new_ids != tuple(sorted_finding_ids(new_ids)) or len(new_ids) != len(
            set(new_ids)
        ):
            raise ArtifactValidationError(
                "branch discovery new findings must be sorted and unique"
            )
        occurrence_ids = tuple(item.finding_id for item in self.occurrences)
        if occurrence_ids != tuple(sorted_finding_ids(occurrence_ids)) or len(
            occurrence_ids
        ) != len(set(occurrence_ids)):
            raise ArtifactValidationError(
                "branch discovery occurrences must be sorted and unique"
            )
        if set(new_ids).intersection(occurrence_ids):
            raise ArtifactValidationError(
                "branch discovery finding cannot also be an occurrence"
            )
        if not isinstance(self.review_evidence, ReviewEvidencePayload):
            raise ArtifactValidationError(
                "branch discovery completion requires review_evidence"
            )
        _require_text(self.pre_mortem, "pre_mortem")
        _require_record_id(
            self.validation_attestation_record_id,
            "validation_attestation_record_id",
        )
        _require_git_sha(self.reviewed_head_commit, "reviewed_head_commit")
        if self.transport_schema != "native-claude-review-v2":  # allowlist:provider -- persisted protocol vocabulary
            raise ArtifactValidationError(
                "branch discovery completion transport_schema is unsupported"
            )
        if (
            not isinstance(self.request_id, str)
            or re.fullmatch(r"native-review-request-[0-9a-f]{64}", self.request_id)
            is None
        ):
            raise ArtifactValidationError(
                "branch discovery completion request_id is invalid"
            )
        _require_sha256(self.response_sha256, "response_sha256")


@dataclass(frozen=True, slots=True)
class ReviewAnchor:
    anchor_id: str
    origin: str
    input_fixture: str
    expected: str
    tolerance: str

    def __post_init__(self) -> None:
        _require_identifier(self.anchor_id, "review anchor_id")
        _require_text(self.origin, "review anchor origin")
        _require_text(self.input_fixture, "review anchor input_fixture")
        _require_text(self.expected, "review anchor expected")
        _require_text(self.tolerance, "review anchor tolerance")


@dataclass(frozen=True, slots=True)
class ReviewAnchorPayload:
    review_record_id: str
    anchors: tuple[ReviewAnchor, ...]
    status: ClassVar[str] = "bound"
    record_type: ClassVar[RecordType] = RecordType.REVIEW_ANCHOR

    def __post_init__(self) -> None:
        _require_record_id(self.review_record_id, "review anchor review_record_id")
        ids = tuple(item.anchor_id for item in self.anchors)
        if ids != tuple(sorted(set(ids))):
            raise ArtifactValidationError(
                "review anchors must be sorted and unique by anchor_id"
            )


@dataclass(frozen=True, slots=True)
class ReviewValidationBindingPayload:
    review_record_id: str
    attestation_record_id: str
    status: ClassVar[str] = "bound"
    record_type: ClassVar[RecordType] = RecordType.REVIEW_VALIDATION_BINDING

    def __post_init__(self) -> None:
        _require_record_id(
            self.review_record_id,
            "review validation binding review_record_id",
        )
        _require_record_id(
            self.attestation_record_id,
            "review validation binding attestation_record_id",
        )


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
    # Older structured-v2 chains may omit responsibility on historical openings.
    responsibility: FindingResponsibility | None = None
    # Preserve the reviewer's active typed decision instead of collapsing it
    # into prose; partial is intentionally an open intermediate state.
    closure_kind: str | None = None
    rejection_reason: str | None = None
    closure_evidence: str | None = None
    remaining_work: str | None = None
    acceptance_command: CommandSpec | None = None
    acceptance_outcome: str | None = None
    acceptance_exit_code: int | None = None
    acceptance_output_sha256: str | None = None
    acceptance_attestation_id: str | None = None
    acceptance_fingerprint: str | None = None
    predecessor_finding_ref: str | None = None
    evidence_anchor_sha256: str | None = None
    # ``None`` is retained only by the private reader for byte-exact inspection
    # of pre-cut records. The active artifact schema requires this field on
    # every opening, so such a payload cannot enter a current record chain.
    affected_paths: tuple[str, ...] | None = ()
    status: ClassVar[str] = "recorded"
    record_type: ClassVar[RecordType] = RecordType.FINDING_TRANSITION

    def __post_init__(self) -> None:
        _require_finding_id(self.finding_id, "finding_id")
        if self.reporter is not Role.CLAUDE:
            raise ArtifactValidationError("finding reporter must be claude")
        if self.action not in {
            "opened", "responded", "status_changed", "reclassified", "routed",
            "acceptance_measured",
        }:
            raise ArtifactValidationError("finding action is invalid")
        if self.finding_status not in {"open", "closed"}:
            raise ArtifactValidationError("finding_status is invalid")
        if self.action in {"opened", "status_changed", "reclassified"} and self.actor != self.reporter:
            raise ArtifactValidationError("only the reporting reviewer may mutate a finding")
        if self.action == "acceptance_measured" and self.actor is not Role.ORCHESTRATOR:
            raise ArtifactValidationError(
                "only the orchestrator may record a finding acceptance measurement"
            )
        if self.action == "acceptance_measured" and self.finding_status != "open":
            raise ArtifactValidationError(
                "a finding acceptance measurement must retain finding_status open"
            )
        if self.action == "responded" and self.actor is not Role.CODEX:
            raise ArtifactValidationError("only codex may record a finding response")
        if self.action == "responded" and self.finding_status != "open":
            raise ArtifactValidationError("a codex response cannot close a finding")
        if self.action == "routed":
            if self.actor is not Role.CLAUDE:  # allowlist:provider -- reviewer authority
                raise ArtifactValidationError(
                    f"finding routing actor must be claude; got {self.actor.value}"  # allowlist:provider -- diagnostic role
                )
            if self.finding_status != "open":
                raise ArtifactValidationError(
                    "a routed finding must retain finding_status open"
                )
            if self.responsibility is None:
                raise ArtifactValidationError(
                    "a routed finding requires a new responsibility"
                )
        if self.responsibility is not None:
            try:
                responsibility_document(self.responsibility)
            except ValueError as exc:
                raise ArtifactValidationError(
                    f"finding responsibility is invalid: {exc}"
                ) from exc
            if self.action not in {"opened", "routed"}:
                raise ArtifactValidationError(
                    "finding responsibility is limited to opened and routed transitions"
                )
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
        if self.action == "opened" and self.responsibility is not None and (
            self.work_unit_id is None or any(item is None for item in opening_metadata)
        ):
            raise ArtifactValidationError(
                "a responsibility-bearing opening requires complete review context metadata"
            )
        if self.action == "opened" and self.affected_paths is not None:
            _require_paths(self.affected_paths, allow_empty=True)
        elif self.affected_paths:
            raise ArtifactValidationError(
                "finding affected_paths are limited to opened transitions"
            )
        generation_fields = (
            self.predecessor_finding_ref,
            self.evidence_anchor_sha256,
        )
        if any(item is not None for item in generation_fields):
            if self.action != "opened" or any(
                item is None for item in generation_fields
            ):
                raise ArtifactValidationError(
                    "Finding generation metadata must be complete and limited to openings"
                )
            _require_finding_id(
                self.predecessor_finding_ref or "",
                "predecessor_finding_ref",
            )
            if self.predecessor_finding_ref == self.finding_id:
                raise ArtifactValidationError(
                    "Finding cannot be its own predecessor"
                )
            _require_sha256(
                self.evidence_anchor_sha256,
                "Finding generation evidence anchor",
            )
        if self.response_decision is not None:
            if self.action != "responded" or self.response_decision not in {
                "accepted",
                "rejected",
            }:
                raise ArtifactValidationError(
                    "response_decision must describe a responded transition"
                )
        _validate_finding_transition_decision(self)
        _validate_finding_acceptance_measurement(self)


def _validate_finding_transition_decision(
    payload: FindingTransitionPayload,
) -> None:
    fields = (
        payload.closure_kind,
        payload.rejection_reason,
        payload.closure_evidence,
        payload.remaining_work,
    )
    if not any(item is not None for item in fields):
        return
    if payload.action != "status_changed":
        raise ArtifactValidationError(
            "finding closure fields require a status_changed transition"
        )
    if payload.closure_kind not in {"fixed", "partial", "rejected"}:
        raise ArtifactValidationError("finding closure_kind is invalid")
    if payload.closure_kind == "fixed":
        if any(item is not None for item in fields[1:]):
            raise ArtifactValidationError(
                "fixed finding closure forbids rejection fields"
            )
        if payload.finding_status != "closed":
            raise ArtifactValidationError(
                "fixed finding closure requires closed status"
            )
        return
    if payload.closure_kind == "partial":
        if payload.finding_status != "open":
            raise ArtifactValidationError(
                "partial finding decision must retain open status"
            )
        if payload.rejection_reason is not None:
            raise ArtifactValidationError(
                "partial finding decision forbids rejection_reason"
            )
        for value, label in (
            (payload.closure_evidence, "partial finding evidence"),
            (payload.remaining_work, "partial finding remaining_work"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ArtifactValidationError(f"{label} must not be empty")
        return
    if payload.finding_status != "closed":
        raise ArtifactValidationError(
            "rejected finding closure requires closed status"
        )
    if payload.rejection_reason not in {
        "no_defect", "out_of_scope", "already_fixed",
    }:
        raise ArtifactValidationError(
            "rejected finding closure requires a valid rejection_reason"
        )
    if not isinstance(payload.closure_evidence, str) or not payload.closure_evidence.strip():
        raise ArtifactValidationError(
            "rejected finding closure requires named evidence"
        )
    if payload.remaining_work is not None:
        raise ArtifactValidationError(
            "rejected finding closure forbids remaining_work"
        )


def _validate_finding_acceptance_measurement(
    payload: FindingTransitionPayload,
) -> None:
    fields = (
        payload.acceptance_command,
        payload.acceptance_outcome,
        payload.acceptance_exit_code,
        payload.acceptance_output_sha256,
        payload.acceptance_attestation_id,
        payload.acceptance_fingerprint,
    )
    if not any(item is not None for item in fields):
        if payload.action == "acceptance_measured":
            raise ArtifactValidationError(
                "acceptance_measured transition requires complete measurement fields"
            )
        return
    if payload.action != "acceptance_measured" or any(item is None for item in fields):
        raise ArtifactValidationError(
            "finding acceptance measurement fields must be complete and limited to acceptance_measured"
        )
    if payload.severity is not FindingSeverity.BLOCKER:
        raise ArtifactValidationError(
            "only a BLOCKER may carry a typed acceptance measurement"
        )
    if not isinstance(payload.acceptance_command, CommandSpec) or payload.acceptance_command.mode != "argv":
        raise ArtifactValidationError(
            "finding acceptance measurement requires a typed argv command"
        )
    if payload.acceptance_outcome not in {"pass", "fail"}:
        raise ArtifactValidationError(
            "finding acceptance measurement outcome is invalid"
        )
    if isinstance(payload.acceptance_exit_code, bool) or not isinstance(
        payload.acceptance_exit_code, int
    ):
        raise ArtifactValidationError(
            "finding acceptance measurement exit_code must be an integer"
        )
    if (payload.acceptance_outcome == "pass") != (payload.acceptance_exit_code == 0):
        raise ArtifactValidationError(
            "finding acceptance measurement outcome differs from exit_code"
        )
    _require_sha256(payload.acceptance_output_sha256, "finding acceptance measurement output_sha256")
    _require_identifier(payload.acceptance_attestation_id, "finding acceptance measurement attestation_id")
    _require_sha256(payload.acceptance_fingerprint, "finding acceptance measurement fingerprint")


@dataclass(frozen=True, slots=True)
class ImportedFindingTransition:
    """One source transition with its immutable source-record identity."""

    record_id: str
    payload: FindingTransitionPayload
    source_run_id: str | None = None
    source_record_id: str | None = None

    def __post_init__(self) -> None:
        _require_record_id(self.record_id, "record_id")
        if self.payload.work_unit_id is None:
            raise ArtifactValidationError("imported transitions must be structured")
        provenance = (self.source_run_id, self.source_record_id)
        if any(value is None for value in provenance) != all(
            value is None for value in provenance
        ):
            missing = (
                "source_run_id"
                if self.source_run_id is None
                else "source_record_id"
            )
            raise ArtifactValidationError(
                f"imported transition is missing required field {missing}"
            )
        if self.source_run_id is not None:
            _require_identifier(self.source_run_id, "source_run_id")
            _require_record_id(self.source_record_id or "", "source_record_id")
            if self.source_record_id != self.record_id:
                raise ArtifactValidationError(
                    "imported transition source_record_id must equal record_id"
                )


def imported_finding_transition_document(
    transition: ImportedFindingTransition,
) -> dict[str, Any]:
    """Preserve the legacy wire form unless explicit provenance is present."""

    document: dict[str, Any] = {
        "record_id": transition.record_id,
        "payload": artifact_payload_document(transition.payload),
    }
    if transition.source_run_id is not None:
        document["source_run_id"] = transition.source_run_id
        document["source_record_id"] = transition.source_record_id
    return document


def finding_transition_sequence_sha256(
    transitions: Sequence[ImportedFindingTransition],
) -> str:
    """Digest the ordered canonical source documents, including their IDs."""
    documents = tuple(
        imported_finding_transition_document(item) for item in transitions
    )
    return hashlib.sha256(canonical_json(documents)).hexdigest()


def _validate_transitive_transition_sequence(
    transitions: Sequence[ImportedFindingTransition],
    *,
    label: str,
) -> None:
    if not transitions:
        raise ArtifactValidationError(f"{label} transitions must not be empty")
    identities: list[tuple[str, str]] = []
    for transition in transitions:
        if transition.source_run_id is None or transition.source_record_id is None:
            missing = (
                "source_run_id"
                if transition.source_run_id is None
                else "source_record_id"
            )
            raise ArtifactValidationError(
                f"{label} transition is missing required field {missing}"
            )
        identities.append(
            (transition.source_run_id, transition.source_record_id)
        )
    if len(identities) != len(set(identities)):
        raise ArtifactValidationError(
            f"{label} transition provenance must be unique"
        )


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
class FindingSnapshotItem:
    finding_id: str
    signature: str
    finding_status: str
    severity: FindingSeverity

    def __post_init__(self) -> None:
        if not isinstance(self.finding_id, str) or _FINDING_ID_RE.fullmatch(
            self.finding_id
        ) is None:
            raise ArtifactValidationError("finding_snapshot finding_id is invalid")
        _require_sha256(self.signature, "finding_snapshot signature")
        if self.finding_status not in {"open", "closed"}:
            raise ArtifactValidationError("finding_snapshot finding_status is invalid")
        if not isinstance(self.severity, FindingSeverity):
            raise ArtifactValidationError("finding_snapshot severity is invalid")

    @property
    def is_open(self) -> bool:
        """Expose state without duplicating reducer vocabulary in consumers."""

        return self.finding_status == "open"


@dataclass(frozen=True, slots=True)
class PlanTreatmentAssignment:
    """Reviewer-authoritative treatment for one canonical Finding signature."""

    signature: str
    finding_ids: tuple[str, ...]
    treatment_kind: str
    closing_slice_ids: tuple[str, ...] = ()
    no_code_reason: str | None = None
    evidence: str | None = None
    authoritative_fingerprint: str | None = None
    evidence_anchor: native_finding_decisions.NoCodeEvidenceAnchor | None = None

    def __post_init__(self) -> None:
        _require_sha256(self.signature, "plan treatment signature")
        _require_unique_finding_ids(
            self.finding_ids, "plan treatment finding_ids", allow_empty=False
        )
        if self.finding_ids != sorted_finding_ids(self.finding_ids):
            raise ArtifactValidationError(
                "plan treatment finding_ids must be ordered by finding_id"
            )
        if self.treatment_kind == "implementation":
            _require_unique_identifiers(
                self.closing_slice_ids,
                "plan treatment closing_slice_ids",
                allow_empty=False,
            )
            if len(self.closing_slice_ids) != 1:
                raise ArtifactValidationError(
                    "implementation treatment requires exactly one closing Slice"
                )
            if any(
                value is not None
                for value in (
                    self.no_code_reason,
                    self.evidence,
                    self.authoritative_fingerprint,
                    self.evidence_anchor,
                )
            ):
                raise ArtifactValidationError(
                    "implementation treatment forbids No-Code disposition fields"
                )
            return
        if self.treatment_kind != "no_code":
            raise ArtifactValidationError("plan treatment kind is invalid")
        if self.closing_slice_ids:
            raise ArtifactValidationError(
                "No-Code disposition forbids closing Slice ids"
            )
        if self.no_code_reason not in {
            "no_defect",
            "out_of_scope",
            "already_fixed",
        }:
            raise ArtifactValidationError("No-Code disposition reason is invalid")
        _require_text(self.evidence, "No-Code disposition evidence")
        _require_sha256(
            self.authoritative_fingerprint,
            "No-Code disposition authoritative_fingerprint",
        )
        if self.evidence_anchor is not None:
            if not isinstance(
                self.evidence_anchor,
                native_finding_decisions.NoCodeEvidenceAnchor,
            ):
                raise ArtifactValidationError(
                    "No-Code disposition evidence_anchor is invalid"
                )
            if (
                self.evidence_anchor.rejection_reason.value
                != self.no_code_reason
            ):
                raise ArtifactValidationError(
                    "No-Code disposition evidence anchor reason differs"
                )
            if (
                self.evidence_anchor.provenance_fingerprint
                != self.authoritative_fingerprint
            ):
                raise ArtifactValidationError(
                    "No-Code disposition evidence provenance differs from review"
                )


@dataclass(frozen=True, slots=True)
class PlanTreatmentDecisionPayload:
    signature: str
    decision: str
    rationale: str

    def __post_init__(self) -> None:
        _require_sha256(self.signature, "plan treatment decision signature")
        if self.decision not in {"accepted", "rejected"}:
            raise ArtifactValidationError("plan treatment decision is invalid")
        _require_text(self.rationale, "plan treatment decision rationale")


@dataclass(frozen=True, slots=True)
class PlanAssignmentPayload:
    """E3 authority created only from one positive, explicit plan review."""

    family_id: str
    cycle_number: int
    remediation_round_number: int
    source_snapshot_record_id: str
    finding_snapshot_sha256: str
    plan_result_record_id: str
    review_record_id: str
    review_fingerprint: str
    treatments: tuple[PlanTreatmentAssignment, ...]
    slices: tuple[SliceSpec, ...]
    implementation_scope: tuple[str, ...]
    authority: Role
    plan_completion: str | None = None
    status: ClassVar[str] = "assigned"
    record_type: ClassVar[RecordType] = RecordType.PLAN_ASSIGNMENT

    def __post_init__(self) -> None:
        _require_identifier(self.family_id, "plan assignment family_id")
        _require_positive(self.cycle_number, "plan assignment cycle_number")
        _require_positive(
            self.remediation_round_number,
            "plan assignment remediation_round_number",
        )
        if self.remediation_round_number > (
            native_finding_decisions.MAX_REMEDIATION_ROUNDS
        ):
            raise ArtifactValidationError(
                "plan assignment exceeds the absolute remediation round limit"
            )
        _require_record_id(
            self.source_snapshot_record_id,
            "plan assignment source_snapshot_record_id",
        )
        _require_sha256(
            self.finding_snapshot_sha256,
            "plan assignment finding_snapshot_sha256",
        )
        _require_record_id(
            self.plan_result_record_id,
            "plan assignment plan_result_record_id",
        )
        _require_record_id(self.review_record_id, "plan assignment review_record_id")
        _require_sha256(self.review_fingerprint, "plan assignment review_fingerprint")
        if self.authority is not Role.CLAUDE:  # allowlist:provider -- reviewer authority
            raise ArtifactValidationError(
                "plan assignment authority must be the reviewer"
            )
        if any(
            not isinstance(item, PlanTreatmentAssignment)
            for item in self.treatments
        ):
            raise ArtifactValidationError(
                "plan assignment treatments must be typed"
            )
        signatures = tuple(item.signature for item in self.treatments)
        if not signatures or signatures != tuple(sorted(set(signatures))):
            raise ArtifactValidationError(
                "plan assignment treatments must be non-empty, sorted, and unique"
            )
        finding_ids = tuple(
            finding_id
            for treatment in self.treatments
            for finding_id in treatment.finding_ids
        )
        if len(finding_ids) != len(set(finding_ids)):
            raise ArtifactValidationError(
                "plan assignment Finding IDs must belong to exactly one signature"
            )
        if any(
            treatment.treatment_kind == "no_code"
            and treatment.authoritative_fingerprint != self.review_fingerprint
            for treatment in self.treatments
        ):
            raise ArtifactValidationError(
                "No-Code disposition fingerprint differs from its plan review"
            )
        slice_ids = tuple(item.slice_id for item in self.slices)
        if slice_ids and slice_ids != tuple(str(index) for index in range(1, len(slice_ids) + 1)):
            raise ArtifactValidationError(
                "plan assignment Slice ids must be contiguous and 1-based"
            )
        available_slices = frozenset(slice_ids)
        if any(
            treatment.treatment_kind == "implementation"
            and treatment.closing_slice_ids[0] not in available_slices
            for treatment in self.treatments
        ):
            raise ArtifactValidationError(
                "plan assignment closing Slice is absent from the implementation plan"
            )
        expected_scope = tuple(
            sorted({path for item in self.slices for path in item.paths})
        )
        if self.implementation_scope != expected_scope:
            raise ArtifactValidationError(
                "plan assignment implementation_scope differs from the union of Slice paths"
            )
        if self.plan_completion is not None:
            try:
                completion = native_finding_decisions.PlanCompletionKind(
                    self.plan_completion
                )
            except ValueError as exc:
                raise ArtifactValidationError(
                    "plan assignment completion is invalid"
                ) from exc
            implementation = any(
                item.treatment_kind == "implementation"
                for item in self.treatments
            )
            if (
                completion
                is native_finding_decisions.PlanCompletionKind.NO_IMPLEMENTATION_REQUIRED
                and (implementation or self.slices or self.implementation_scope)
            ):
                raise ArtifactValidationError(
                    "NO_IMPLEMENTATION_REQUIRED plan assignment contains implementation work"
                )
            if (
                completion
                is native_finding_decisions.PlanCompletionKind.IMPLEMENTATION_REQUIRED
                and (not implementation or not self.slices)
            ):
                raise ArtifactValidationError(
                    "IMPLEMENTATION_REQUIRED plan assignment lacks implementation work"
                )


@dataclass(frozen=True, slots=True)
class RemediationCohortCheckpointPayload:
    """Pre-discovery E4 fact separating inherited S_r from later N_r."""

    family_id: str
    remediation_round_number: int
    plan_assignment_record_id: str
    implementation_run_id: str
    implementation_head_record_id: str
    inherited_signatures: tuple[str, ...]
    unresolved_inherited_signatures: tuple[str, ...]
    authority: Role
    status: ClassVar[str] = "checked"
    record_type: ClassVar[RecordType] = RecordType.REMEDIATION_COHORT_CHECKPOINT

    def __post_init__(self) -> None:
        _require_identifier(self.family_id, "remediation cohort family_id")
        _require_positive(
            self.remediation_round_number,
            "remediation cohort round number",
        )
        if self.remediation_round_number > (
            native_finding_decisions.MAX_REMEDIATION_ROUNDS
        ):
            raise ArtifactValidationError(
                "remediation cohort exceeds the absolute remediation round limit"
            )
        _require_record_id(
            self.plan_assignment_record_id,
            "remediation cohort plan_assignment_record_id",
        )
        _require_identifier(
            self.implementation_run_id,
            "remediation cohort implementation_run_id",
        )
        _require_record_id(
            self.implementation_head_record_id,
            "remediation cohort implementation_head_record_id",
        )
        _require_signature_sequence(
            self.inherited_signatures,
            "remediation cohort inherited_signatures",
            allow_empty=False,
        )
        _require_signature_sequence(
            self.unresolved_inherited_signatures,
            "remediation cohort unresolved_inherited_signatures",
            allow_empty=True,
        )
        if not set(self.unresolved_inherited_signatures).issubset(
            self.inherited_signatures
        ):
            raise ArtifactValidationError(
                "unresolved inherited signatures are not a subset of S_r"
            )
        if self.authority is not Role.ORCHESTRATOR:
            raise ArtifactValidationError(
                "remediation cohort checkpoint authority must be orchestrator"
            )


@dataclass(frozen=True, slots=True)
class ClosedFindingDispositionSnapshot:
    finding_id: str
    signature: str
    source_plan_assignment_record_id: str
    evidence_anchor: native_finding_decisions.NoCodeEvidenceAnchor

    def __post_init__(self) -> None:
        _require_finding_id(self.finding_id, "closed disposition finding_id")
        _require_sha256(self.signature, "closed disposition signature")
        _require_record_id(
            self.source_plan_assignment_record_id,
            "closed disposition PlanAssignment record",
        )
        if not isinstance(
            self.evidence_anchor,
            native_finding_decisions.NoCodeEvidenceAnchor,
        ):
            raise ArtifactValidationError(
                "closed disposition requires a typed evidence anchor"
            )


@dataclass(frozen=True, slots=True)
class NoImplementationRequiredPayload:
    family_id: str
    cycle_number: int
    remediation_round_number: int
    plan_assignment_record_id: str
    review_record_id: str
    reviewed_plan_commit: str
    closed_finding_ids: tuple[str, ...]
    authority: Role
    status: ClassVar[str] = "completed"
    record_type: ClassVar[RecordType] = RecordType.NO_IMPLEMENTATION_REQUIRED

    def __post_init__(self) -> None:
        _require_identifier(self.family_id, "No-implementation family_id")
        _require_positive(self.cycle_number, "No-implementation cycle_number")
        _require_positive(
            self.remediation_round_number,
            "No-implementation remediation_round_number",
        )
        _require_record_id(
            self.plan_assignment_record_id,
            "No-implementation PlanAssignment record",
        )
        _require_record_id(
            self.review_record_id,
            "No-implementation review record",
        )
        _require_git_sha(self.reviewed_plan_commit, "reviewed_plan_commit")
        _require_unique_finding_ids(
            self.closed_finding_ids,
            "No-implementation closed_finding_ids",
            allow_empty=False,
        )
        if self.closed_finding_ids != sorted_finding_ids(
            self.closed_finding_ids
        ):
            raise ArtifactValidationError(
                "No-implementation closed Finding ids must be ordered"
            )
        if self.authority is not Role.ORCHESTRATOR:
            raise ArtifactValidationError(
                "No-implementation completion authority must be orchestrator"
            )


@dataclass(frozen=True, slots=True)
class ClosedFindingOccurrencePayload:
    finding_id: str
    signature: str
    source_plan_assignment_record_id: str
    discovery_completion_record_id: str
    evidence_anchor_sha256: str
    rationale: str
    reviewer: Role
    status: ClassVar[str] = "recorded"
    record_type: ClassVar[RecordType] = RecordType.CLOSED_FINDING_OCCURRENCE

    def __post_init__(self) -> None:
        _require_finding_id(self.finding_id, "closed occurrence finding_id")
        _require_sha256(self.signature, "closed occurrence signature")
        _require_record_id(
            self.source_plan_assignment_record_id,
            "closed occurrence PlanAssignment record",
        )
        _require_record_id(
            self.discovery_completion_record_id,
            "closed occurrence discovery completion record",
        )
        _require_sha256(
            self.evidence_anchor_sha256,
            "closed occurrence evidence anchor",
        )
        _require_text(self.rationale, "closed occurrence rationale")
        if self.reviewer is not Role.CLAUDE:  # allowlist:provider -- reviewer authority
            raise ArtifactValidationError(
                "closed Finding occurrence reviewer must be claude"  # allowlist:provider -- diagnostic role
            )


def _validate_finding_snapshot(
    snapshot: Sequence[FindingSnapshotItem], *, allow_empty: bool = False
) -> None:
    if not snapshot and not allow_empty:
        raise ArtifactValidationError("finding_snapshot must not be empty")
    ids = tuple(item.finding_id for item in snapshot)
    if ids != tuple(sorted_finding_ids(ids)):
        raise ArtifactValidationError("finding_snapshot must be ordered by finding_id")
    if len(ids) != len(set(ids)):
        raise ArtifactValidationError("finding_snapshot finding_id values must be unique")


@dataclass(frozen=True, slots=True)
class BranchDiscoveryHandoffExportPayload:
    source_run_id: str
    source_head_record_id: str
    discovery_review_record_id: str | None
    validation_attestation_record_id: str
    reviewed_head_commit: str
    family_id: str
    family_base_commit: str
    cycle_number: int
    predecessor_run_id: str
    predecessor_head_record_id: str
    finding_transition_record_ids: tuple[str, ...]
    finding_transitions_sha256: str
    target_task_path: str
    target_task_sha256: str
    target_run_identity: str
    authority: Role
    target_execution_mode: str = "PLAN_ONLY"
    source_completion_record_id: str | None = None
    remediation_cohort_checkpoint_record_id: str | None = None
    closed_finding_dispositions: tuple[ClosedFindingDispositionSnapshot, ...] = ()
    status: ClassVar[str] = "exported"
    record_type: ClassVar[RecordType] = RecordType.BRANCH_DISCOVERY_HANDOFF_EXPORT

    def __post_init__(self) -> None:
        for value, name in (
            (self.source_run_id, "source_run_id"),
            (self.family_id, "family_id"),
            (self.predecessor_run_id, "predecessor_run_id"),
            (self.target_run_identity, "target_run_identity"),
        ):
            _require_identifier(value, name)
        for value, name in (
            (self.source_head_record_id, "source_head_record_id"),
            (
                self.validation_attestation_record_id,
                "validation_attestation_record_id",
            ),
            (
                self.predecessor_head_record_id,
                "predecessor_head_record_id",
            ),
        ):
            _require_record_id(value, name)
        if self.target_execution_mode not in {"PLAN_ONLY", "BRANCH_DISCOVERY"}:
            raise ArtifactValidationError(
                "branch discovery handoff target_execution_mode is invalid"
            )
        if self.target_execution_mode == "PLAN_ONLY":
            if self.discovery_review_record_id is None:
                raise ArtifactValidationError(
                    "PLAN_ONLY branch discovery handoff requires discovery_review_record_id"
                )
            _require_record_id(
                self.discovery_review_record_id, "discovery_review_record_id"
            )
            if self.source_completion_record_id is not None:
                raise ArtifactValidationError(
                    "PLAN_ONLY branch discovery handoff forbids source_completion_record_id"
                )
            if self.remediation_cohort_checkpoint_record_id is not None:
                raise ArtifactValidationError(
                    "PLAN_ONLY branch discovery handoff forbids a remediation cohort checkpoint"
                )
        else:
            if self.discovery_review_record_id is not None:
                raise ArtifactValidationError(
                    "BRANCH_DISCOVERY handoff forbids discovery_review_record_id"
                )
            if self.source_completion_record_id is None:
                raise ArtifactValidationError(
                    "BRANCH_DISCOVERY handoff requires source_completion_record_id"
                )
            _require_record_id(
                self.source_completion_record_id, "source_completion_record_id"
            )
            if self.remediation_cohort_checkpoint_record_id is not None:
                _require_record_id(
                    self.remediation_cohort_checkpoint_record_id,
                    "remediation_cohort_checkpoint_record_id",
                )
        _require_git_sha(self.reviewed_head_commit, "reviewed_head_commit")
        _require_git_sha(self.family_base_commit, "family_base_commit")
        _require_positive(self.cycle_number, "cycle_number")
        _require_unique_identifiers(
            self.finding_transition_record_ids,
            "finding_transition_record_ids",
            allow_empty=self.target_execution_mode == "BRANCH_DISCOVERY",
        )
        _require_sha256(
            self.finding_transitions_sha256, "finding_transitions_sha256"
        )
        _require_path(self.target_task_path)
        _require_sha256(self.target_task_sha256, "target_task_sha256")
        if self.authority is not Role.ORCHESTRATOR:
            raise ArtifactValidationError(
                "branch discovery handoff export authority must be orchestrator"
            )
        _validate_closed_finding_dispositions(
            self.closed_finding_dispositions
        )


@dataclass(frozen=True, slots=True)
class BranchDiscoveryHandoffImportPayload:
    source_run_id: str
    source_head_record_id: str
    discovery_review_record_id: str | None
    validation_attestation_record_id: str
    reviewed_head_commit: str
    family_id: str
    family_base_commit: str
    cycle_number: int
    predecessor_run_id: str
    predecessor_head_record_id: str
    export_record_id: str
    target_run_id: str
    target_task_path: str
    target_task_sha256: str
    target_run_identity: str
    finding_transitions_sha256: str
    transitions: tuple[ImportedFindingTransition, ...]
    finding_snapshot: tuple[FindingSnapshotItem, ...]
    authority: Role
    target_execution_mode: str = "PLAN_ONLY"
    source_completion_record_id: str | None = None
    remediation_cohort_checkpoint_record_id: str | None = None
    closed_finding_dispositions: tuple[ClosedFindingDispositionSnapshot, ...] = ()
    status: ClassVar[str] = "imported"
    record_type: ClassVar[RecordType] = RecordType.BRANCH_DISCOVERY_HANDOFF_IMPORT

    def __post_init__(self) -> None:
        for value, name in (
            (self.source_run_id, "source_run_id"),
            (self.family_id, "family_id"),
            (self.predecessor_run_id, "predecessor_run_id"),
            (self.target_run_id, "target_run_id"),
            (self.target_run_identity, "target_run_identity"),
        ):
            _require_identifier(value, name)
        for value, name in (
            (self.source_head_record_id, "source_head_record_id"),
            (
                self.validation_attestation_record_id,
                "validation_attestation_record_id",
            ),
            (
                self.predecessor_head_record_id,
                "predecessor_head_record_id",
            ),
            (self.export_record_id, "export_record_id"),
        ):
            _require_record_id(value, name)
        if self.target_execution_mode not in {"PLAN_ONLY", "BRANCH_DISCOVERY"}:
            raise ArtifactValidationError(
                "branch discovery handoff target_execution_mode is invalid"
            )
        if self.target_execution_mode == "PLAN_ONLY":
            if self.discovery_review_record_id is None:
                raise ArtifactValidationError(
                    "PLAN_ONLY branch discovery import requires discovery_review_record_id"
                )
            _require_record_id(
                self.discovery_review_record_id, "discovery_review_record_id"
            )
            if self.source_completion_record_id is not None:
                raise ArtifactValidationError(
                    "PLAN_ONLY branch discovery import forbids source_completion_record_id"
                )
            if self.remediation_cohort_checkpoint_record_id is not None:
                raise ArtifactValidationError(
                    "PLAN_ONLY branch discovery import forbids a remediation cohort checkpoint"
                )
        else:
            if self.discovery_review_record_id is not None:
                raise ArtifactValidationError(
                    "BRANCH_DISCOVERY import forbids discovery_review_record_id"
                )
            if self.source_completion_record_id is None:
                raise ArtifactValidationError(
                    "BRANCH_DISCOVERY import requires source_completion_record_id"
                )
            _require_record_id(
                self.source_completion_record_id, "source_completion_record_id"
            )
            if self.remediation_cohort_checkpoint_record_id is not None:
                _require_record_id(
                    self.remediation_cohort_checkpoint_record_id,
                    "remediation_cohort_checkpoint_record_id",
                )
        _require_git_sha(self.reviewed_head_commit, "reviewed_head_commit")
        _require_git_sha(self.family_base_commit, "family_base_commit")
        _require_positive(self.cycle_number, "cycle_number")
        _require_path(self.target_task_path)
        _require_sha256(self.target_task_sha256, "target_task_sha256")
        _require_sha256(
            self.finding_transitions_sha256, "finding_transitions_sha256"
        )
        if self.target_run_id != self.target_run_identity:
            raise ArtifactValidationError(
                "branch discovery handoff target_run_id differs from target_run_identity"
            )
        if self.transitions or self.target_execution_mode != "BRANCH_DISCOVERY":
            _validate_transitive_transition_sequence(
                self.transitions, label="branch discovery handoff import"
            )
        if finding_transition_sequence_sha256(self.transitions) != (
            self.finding_transitions_sha256
        ):
            raise ArtifactValidationError(
                "branch discovery handoff import transition digest does not match"
            )
        _validate_finding_snapshot(
            self.finding_snapshot,
            allow_empty=self.target_execution_mode == "BRANCH_DISCOVERY",
        )
        if self.authority is not Role.ORCHESTRATOR:
            raise ArtifactValidationError(
                "branch discovery handoff import authority must be orchestrator"
            )
        _validate_closed_finding_dispositions(
            self.closed_finding_dispositions
        )


def _validate_closed_finding_dispositions(
    dispositions: Sequence[ClosedFindingDispositionSnapshot],
) -> None:
    if any(
        not isinstance(item, ClosedFindingDispositionSnapshot)
        for item in dispositions
    ):
        raise ArtifactValidationError(
            "closed finding dispositions must be typed"
        )
    ids = tuple(item.finding_id for item in dispositions)
    if ids != tuple(sorted_finding_ids(ids)) or len(ids) != len(set(ids)):
        raise ArtifactValidationError(
            "closed finding dispositions must be sorted and unique"
        )


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
class BlobReference:
    sha256: str
    bytes: int

    def __post_init__(self) -> None:
        _require_sha256(self.sha256, "blob sha256")
        if isinstance(self.bytes, bool) or not isinstance(self.bytes, int) or self.bytes < 0:
            raise ArtifactValidationError("blob bytes must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class ValidationOutputContent:
    command: CommandSpec
    digest_outcome: str
    exit_code: int
    raw_stdout: BlobReference
    raw_stderr: BlobReference
    compact_output: BlobReference
    output_bytes: int

    def __post_init__(self) -> None:
        if self.digest_outcome not in {
            "pass", "fail", "timeout", "missing", "unavailable",
        }:
            raise ArtifactValidationError("validation content outcome is invalid")
        if isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int):
            raise ArtifactValidationError("validation content exit_code must be an integer")
        expected_bytes = self.raw_stdout.bytes + self.raw_stderr.bytes
        if self.output_bytes != expected_bytes:
            raise ArtifactValidationError(
                "validation content output_bytes differs from its streams"
            )


@dataclass(frozen=True, slots=True)
class ValidationContentPayload:
    attestation_id: str
    result_record_id: str
    digest_format: str
    output_digest: str
    summary: str
    outputs: tuple[ValidationOutputContent, ...]
    status: ClassVar[str] = "captured"
    record_type: ClassVar[RecordType] = RecordType.VALIDATION_CONTENT

    def __post_init__(self) -> None:
        _require_identifier(self.attestation_id, "validation content attestation_id")
        _require_record_id(self.result_record_id, "validation content result_record_id")
        if self.digest_format not in {"validation-matrix-v1", "raw-output-v1"}:
            raise ArtifactValidationError("validation content digest_format is invalid")
        _require_sha256(self.output_digest, "validation content output_digest")
        _require_text(self.summary, "validation content summary")
        if not self.outputs:
            raise ArtifactValidationError("validation content outputs must not be empty")
        commands = tuple(item.command for item in self.outputs)
        if len(commands) != len(set(commands)):
            raise ArtifactValidationError("validation content commands must be unique")


@dataclass(frozen=True, slots=True)
class ValidationAttestationPayload:
    results: tuple[ValidationResult, ...]
    attested_by: Role
    output_digest: str
    content_record_id: str
    status: ClassVar[str] = "attested"
    record_type: ClassVar[RecordType] = RecordType.VALIDATION_ATTESTATION

    def __post_init__(self) -> None:
        if not self.results:
            raise ArtifactValidationError("validation attestation results must not be empty")
        _require_sha256(self.output_digest, "validation attestation output_digest")
        _require_record_id(
            self.content_record_id,
            "validation attestation content_record_id",
        )


@dataclass(frozen=True, slots=True)
class ProviderContentPayload:
    role: Role
    work_unit_id: str
    round_number: int
    operation: str
    request_id: str
    response_sha256: str
    content_kind: str
    content_bytes: int
    blob: BlobReference
    status: ClassVar[str] = "captured"
    record_type: ClassVar[RecordType] = RecordType.PROVIDER_CONTENT

    def __post_init__(self) -> None:
        if not isinstance(self.role, Role) or self.role in {
            Role.ORCHESTRATOR,
            Role.USER,
        }:
            raise ArtifactValidationError("provider content role must identify one agent")
        _require_identifier(self.work_unit_id, "provider content work_unit_id")
        if (
            isinstance(self.round_number, bool)
            or not isinstance(self.round_number, int)
            or self.round_number < 1
        ):
            raise ArtifactValidationError(
                "provider content round_number must be positive"
            )
        _require_identifier(self.operation, "provider content operation")
        _require_identifier(self.request_id, "provider content request_id")
        _require_sha256(self.response_sha256, "provider content response_sha256")
        if self.content_kind not in {"agent_result", "review_result", "final_report"}:
            raise ArtifactValidationError("provider content kind is invalid")
        if self.content_bytes != self.blob.bytes:
            raise ArtifactValidationError("provider content bytes differ from its blob")
        if self.response_sha256 != self.blob.sha256:
            raise ArtifactValidationError("provider content digest differs from its blob")


@dataclass(frozen=True, slots=True)
class ReviewPacketPayload:
    work_unit_id: str
    fingerprint: str
    purpose: str
    manifest: tuple[str, ...]
    diff_coverage_sha256: str
    content_bytes: int
    blob: BlobReference
    status: ClassVar[str] = "captured"
    record_type: ClassVar[RecordType] = RecordType.REVIEW_PACKET

    def __post_init__(self) -> None:
        _require_identifier(self.work_unit_id, "review packet work_unit_id")
        _require_sha256(self.fingerprint, "review packet fingerprint")
        if self.purpose not in {"slice", "correction"}:
            raise ArtifactValidationError("review packet purpose is invalid")
        _require_paths(self.manifest)
        _require_sha256(
            self.diff_coverage_sha256,
            "review packet diff_coverage_sha256",
        )
        if self.content_bytes != self.blob.bytes:
            raise ArtifactValidationError("review packet bytes differ from its blob")


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


SIDE_EFFECT_CLASSES = frozenset({
    "git_commit",
    "provider_start",
    "file_write",
    "queue_move",
    "internal",
    "ledger",
})


def stable_side_effect_key(
    effect_class: str,
    work_unit_id: str,
    operation: Sequence[str],
) -> str:
    """Derive the immutable idempotency key for one physical operation."""
    if effect_class == "internal":
        if len(operation) != 1:
            raise ArtifactValidationError("internal side effect requires one marker")
        _require_identifier(operation[0], "internal side effect marker")
    if effect_class == "queue_move" and len(operation) == 3:
        key_operation = (operation[0], operation[2])
    elif effect_class == "file_write" and len(operation) == 4:
        # The expected digest already binds the durable bytes.  Excluding their
        # base64 representation keeps key derivation bounded while the prior
        # digest distinguishes successive overwrite generations.
        key_operation = operation[:3]
    else:
        key_operation = tuple(operation)
    digest = hashlib.sha256(
        canonical_json([effect_class, work_unit_id, list(key_operation)])
    ).hexdigest()
    return f"side-effect:{effect_class}:{digest[:32]}"


@dataclass(frozen=True, slots=True)
class SideEffectPayload:
    effect_key: str
    effect_class: str
    work_unit_id: str
    operation: tuple[str, ...]
    phase: str
    result: str | None
    record_type: ClassVar[RecordType] = RecordType.SIDE_EFFECT

    @property
    def status(self) -> str:
        return self.phase

    def __post_init__(self) -> None:
        _require_identifier(self.effect_key, "side effect key")
        if self.effect_class not in SIDE_EFFECT_CLASSES:
            raise ArtifactValidationError("side effect class is invalid")
        _require_identifier(self.work_unit_id, "side effect work_unit_id")
        if not self.operation:
            raise ArtifactValidationError("side effect operation must not be empty")
        for index, item in enumerate(self.operation):
            _require_text(item, f"side effect operation[{index}]")
            if any(character in item for character in ("\x00", "\r", "\n")):
                raise ArtifactValidationError("side effect operation contains a control separator")
        if self.effect_key != stable_side_effect_key(
            self.effect_class, self.work_unit_id, self.operation
        ):
            raise ArtifactValidationError("side effect key differs from its immutable operation")
        if self.effect_class == "git_commit":
            if (
                len(self.operation) != 6
                or self.operation[0] not in {"slice_commit", "audit_commit"}
                or re.fullmatch(r"[0-9a-f]{40}", self.operation[2]) is None
                or re.fullmatch(r"[0-9a-f]{40}", self.operation[3]) is None
            ):
                raise ArtifactValidationError("Git side effect operation is invalid")
            _require_sha256(self.operation[4], "Git side effect fingerprint")
            _require_sha256(self.operation[5], "Git side effect message digest")
        elif self.effect_class == "provider_start":
            if (
                len(self.operation) != 7
                or not self.operation[5].isdigit()
                or int(self.operation[5]) < 1
            ):
                raise ArtifactValidationError("provider side effect operation is invalid")
            _require_sha256(self.operation[2], "provider side effect input digest")
            _require_sha256(self.operation[3], "provider side effect binding")
        elif self.effect_class == "file_write":
            if len(self.operation) not in {2, 4}:
                raise ArtifactValidationError("file side effect operation is invalid")
            _require_sha256(self.operation[1], "file side effect content digest")
            if len(self.operation) == 4:
                if self.operation[2] != "absent":
                    _require_sha256(
                        self.operation[2], "file side effect prior content digest"
                    )
                try:
                    durable_content = base64.b64decode(
                        self.operation[3], validate=True
                    )
                except (ValueError, binascii.Error) as exc:
                    raise ArtifactValidationError(
                        "file side effect durable content is invalid"
                    ) from exc
                if hashlib.sha256(durable_content).hexdigest() != self.operation[1]:
                    raise ArtifactValidationError(
                        "file side effect durable content differs from its digest"
                    )
        elif self.effect_class == "queue_move":
            if len(self.operation) != 3:
                raise ArtifactValidationError("queue side effect operation is invalid")
            _require_sha256(self.operation[2], "queue side effect content digest")
        elif self.effect_class == "ledger" and (
            self.work_unit_id != "run"
            or self.operation != ("structured-v2-side-effect-ledger",)
        ):
            raise ArtifactValidationError("ledger initializer operation is invalid")
        if self.phase not in {"intent", "result"}:
            raise ArtifactValidationError("side effect phase is invalid")
        if self.phase == "intent":
            if self.result is not None:
                raise ArtifactValidationError("side effect intent cannot carry a result")
        elif self.result is None:
            raise ArtifactValidationError("side effect result phase requires a result")
        else:
            _require_text(self.result, "side effect result")
            if any(character in self.result for character in ("\x00", "\r", "\n")):
                raise ArtifactValidationError("side effect result contains a control separator")
            if self.effect_class == "git_commit" and re.fullmatch(
                r"[0-9a-f]{40}", self.result
            ) is None:
                raise ArtifactValidationError("Git side effect result must be a commit")
            if self.effect_class == "provider_start" and not (
                _SHA256_RE.fullmatch(self.result)
                or re.fullmatch(
                    r"failed:(?:quota|network|timeout|permission|auth|binary|output|process|runtime)",
                    self.result,
                )
            ):
                raise ArtifactValidationError("provider side effect result is invalid")
            if self.effect_class in {"file_write", "queue_move"}:
                _require_sha256(self.result, "side effect content result")
            if self.effect_class == "internal" and self.result != "completed":
                raise ArtifactValidationError("internal side effect result is invalid")
            if self.effect_class == "ledger" and self.result != "initialized":
                raise ArtifactValidationError("ledger initializer result is invalid")


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
class GateTransitionPayload:
    work_unit_id: str
    gate_status: str
    reason: str
    detail: str | None
    fingerprint: str | None
    paths: tuple[str, ...]
    resume_step: str | None
    active_test_fingerprint: str | None
    active_test_paths: tuple[str, ...]
    status: ClassVar[str] = "transitioned"
    record_type: ClassVar[RecordType] = RecordType.GATE_TRANSITION

    def __post_init__(self) -> None:
        _require_identifier(self.work_unit_id, "gate transition work_unit_id")
        if self.gate_status not in _GATE_STATUSES:
            raise ArtifactValidationError("gate transition status is invalid")
        if self.reason not in _GATE_REASONS:
            raise ArtifactValidationError("gate transition reason is invalid")
        if self.detail is not None:
            _require_text(self.detail, "gate transition detail")
        if self.fingerprint is not None:
            _require_sha256(self.fingerprint, "gate transition fingerprint")
        _require_paths(self.paths, allow_empty=True)
        if self.paths != tuple(sorted(self.paths)):
            raise ArtifactValidationError("gate transition paths must be sorted")
        if self.resume_step is not None and self.resume_step not in _WORKFLOW_STEPS:
            raise ArtifactValidationError("gate transition resume_step is invalid")
        if self.gate_status == "clear":
            if (
                self.reason != "none"
                or self.detail is not None
                or self.fingerprint is not None
                or self.paths
                or self.resume_step is not None
            ):
                raise ArtifactValidationError(
                    "clear gate transition cannot carry gate evidence"
                )
        elif self.reason == "none":
            raise ArtifactValidationError(
                "non-clear gate transition requires a reason"
            )
        if (self.active_test_fingerprint is None) != (not self.active_test_paths):
            raise ArtifactValidationError(
                "active test fingerprint and paths must be bound together"
            )
        if self.active_test_fingerprint is not None:
            _require_sha256(
                self.active_test_fingerprint, "active test fingerprint"
            )
            _require_paths(self.active_test_paths)
            if self.active_test_paths != tuple(sorted(self.active_test_paths)):
                raise ArtifactValidationError("active test paths must be sorted")


@dataclass(frozen=True, slots=True)
class GateDecisionPayload:
    work_unit_id: str
    gate_record_id: str
    paths: tuple[str, ...]
    resume_step: str | None
    invocation_id: str | None = None
    status: ClassVar[str] = "bound"
    record_type: ClassVar[RecordType] = RecordType.GATE_DECISION

    def __post_init__(self) -> None:
        _require_identifier(self.work_unit_id, "gate decision work_unit_id")
        _require_identifier(self.gate_record_id, "gate decision gate_record_id")
        _require_paths(self.paths, allow_empty=True)
        if self.paths != tuple(sorted(self.paths)):
            raise ArtifactValidationError("gate decision paths must be sorted")
        if self.resume_step is not None and self.resume_step not in _WORKFLOW_STEPS:
            raise ArtifactValidationError("gate decision resume_step is invalid")
        if self.invocation_id is not None:
            _require_identifier(self.invocation_id, "gate decision invocation_id")


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


_AGENT_FAILURE_KINDS = {
    "quota",
    "network",
    "timeout",
    "permission",
    "auth",
    "binary",
    "output",
    "process",
    "runtime",
}
_OPERATIONAL_FAILURE_CLASSES = {
    "transient",
    "resumable_halt",
    "terminal_rejection",
}
_NATIVE_REVIEW_RESPONSE_REJECTION_CODES = {
    "schema-invalid",
    "request-mismatch",
    "reviewer-mismatch",
    "finding-id-invalid",
    "finding-reference-unknown",
    "finding-reference-not-open",
    "finding-event-conflict",
    "missing-own-finding-update",
    "finding-content-invalid",
    "finding-signature-duplicate",
    "acceptance-invalid",
    "anchor-invalid",
    "review-content-missing",
    "stop-content-invalid",
    "approval-invalid",
    "dormant-finding-decision-field",
}
_NATIVE_IMPLEMENTER_RESPONSE_REJECTION_CODES = {
    "schema-invalid",
    "request-mismatch",
    "result-kind-mismatch",
    "result-content-invalid",
    "finding-reference-invalid",
    "test-files-invalid",
    "slice-plan-invalid",
    "stop-content-invalid",
    "dormant-finding-decision-field",
}
_PROVIDER_TEXT_MARKER_RE = re.compile(
    r"^\[provider text redacted; sha256=([0-9a-f]{64}); utf8_bytes=([1-9][0-9]*)\]$"
)
_TECHNICAL_TEXT_MARKER_RE = re.compile(
    r"^\[technical text redacted; sha256=([0-9a-f]{64}); utf8_bytes=([1-9][0-9]*)\]$"
)


def provider_text_evidence(provider_text: str) -> tuple[str, str, int]:
    """Return bounded immutable evidence without retaining provider bytes.

    Calling the helper again with an existing marker is idempotent.  That lets
    a record-ahead resume rebuild the state mirror without inventing the
    unavailable raw diagnostic.
    """
    _require_text(provider_text, "provider_text")
    existing = _PROVIDER_TEXT_MARKER_RE.fullmatch(provider_text)
    if existing is not None:
        return provider_text, existing.group(1), int(existing.group(2))
    encoded = provider_text.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    marker = (
        f"[provider text redacted; sha256={digest}; "
        f"utf8_bytes={len(encoded)}]"
    )
    return marker, digest, len(encoded)


def technical_text_evidence(technical_text: str) -> tuple[str, str, int]:
    """Return bounded immutable evidence without retaining technical bytes."""
    _require_text(technical_text, "technical_text")
    existing = _TECHNICAL_TEXT_MARKER_RE.fullmatch(technical_text)
    if existing is not None:
        return technical_text, existing.group(1), int(existing.group(2))
    encoded = technical_text.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    marker = (
        f"[technical text redacted; sha256={digest}; "
        f"utf8_bytes={len(encoded)}]"
    )
    return marker, digest, len(encoded)


@dataclass(frozen=True, slots=True)
class InvocationFailurePayload:
    invocation_id: str
    idempotency_key: str
    role: Role
    failure_kind: str
    failure_class: str
    diagnostic_code: str
    provider_text: str
    provider_text_sha256: str
    provider_text_bytes: int
    technical_text: str
    technical_text_sha256: str
    technical_text_bytes: int
    received_at: str
    decision_at_utc: str
    step: str
    slice_id: str
    work_unit_id: str
    diagnostic_exit_code: int
    process_exit_code: int | None
    parse_path: str | None
    source_timezone: str | None
    reset_at_utc: str | None
    resume_at_utc: str | None
    safety_margin_seconds: int
    retry_delay_seconds: int
    auto_resume_count: int
    automatic_resume: bool
    diff_fingerprint: str | None
    orchestrator_diagnostic: str | None = None
    native_review_rejection: str | None = None
    native_review_retry_round: int | None = None
    native_implementer_rejection: str | None = None
    native_implementer_retry_round: int | None = None
    status: ClassVar[str] = "classified"
    record_type: ClassVar[RecordType] = RecordType.INVOCATION_FAILURE

    @property
    def native_response_feedback_document(self) -> dict[str, object]:
        document: dict[str, object] = {}
        if (
            self.orchestrator_diagnostic is not None
            and self.native_implementer_rejection is not None
        ):
            document["orchestrator_diagnostic"] = self.orchestrator_diagnostic
        if self.native_review_rejection is not None:
            document.update(
                native_review_rejection=self.native_review_rejection,
                native_review_retry_round=self.native_review_retry_round,
            )
        if self.native_implementer_rejection is not None:
            document.update(
                native_implementer_rejection=self.native_implementer_rejection,
                native_implementer_retry_round=self.native_implementer_retry_round,
            )
        return document

    @property
    def native_review_feedback_document(self) -> dict[str, object]:
        """Compatibility alias for callers predating implementer feedback."""

        return self.native_response_feedback_document

    def __post_init__(self) -> None:
        _require_identifier(self.invocation_id, "invocation failure invocation_id")
        _require_text(self.idempotency_key, "invocation failure idempotency_key")
        if not isinstance(self.role, Role) or self.role in {
            Role.ORCHESTRATOR,
            Role.USER,
        }:
            raise ArtifactValidationError(
                "invocation failure role must identify one agent"
            )
        if self.failure_kind not in _AGENT_FAILURE_KINDS:
            raise ArtifactValidationError("invocation failure kind is invalid")
        if self.failure_class not in _OPERATIONAL_FAILURE_CLASSES:
            raise ArtifactValidationError("invocation failure class is invalid")
        _require_identifier(self.diagnostic_code, "invocation failure diagnostic_code")
        _require_sha256(
            self.provider_text_sha256,
            "invocation failure provider_text_sha256",
        )
        _require_positive(
            self.provider_text_bytes,
            "invocation failure provider_text_bytes",
        )
        marker, digest, byte_count = provider_text_evidence(self.provider_text)
        if (
            marker != self.provider_text
            or digest != self.provider_text_sha256
            or byte_count != self.provider_text_bytes
        ):
            raise ArtifactValidationError(
                "invocation failure provider text evidence is inconsistent"
            )
        _require_sha256(
            self.technical_text_sha256,
            "invocation failure technical_text_sha256",
        )
        _require_positive(
            self.technical_text_bytes,
            "invocation failure technical_text_bytes",
        )
        technical_marker, technical_digest, technical_byte_count = (
            technical_text_evidence(self.technical_text)
        )
        if (
            technical_marker != self.technical_text
            or technical_digest != self.technical_text_sha256
            or technical_byte_count != self.technical_text_bytes
        ):
            raise ArtifactValidationError(
                "invocation failure technical text evidence is inconsistent"
            )
        if (
            self.orchestrator_diagnostic is not None
            and self.orchestrator_diagnostic not in ORCHESTRATOR_DIAGNOSTIC_TEXTS
        ):
            raise ArtifactValidationError(
                "invocation failure orchestrator diagnostic is not allowlisted"
            )
        _validate_native_response_failure_feedback(self)
        for value, label in (
            (self.received_at, "invocation failure received_at"),
            (self.decision_at_utc, "invocation failure decision_at_utc"),
        ):
            _require_utc_timestamp(value, label)
        _require_identifier(self.step, "invocation failure step")
        _require_identifier(self.slice_id, "invocation failure slice_id")
        _require_identifier(self.work_unit_id, "invocation failure work_unit_id")
        if self.diagnostic_exit_code != (
            2 if self.failure_kind == "quota" else 3
        ):
            raise ArtifactValidationError(
                "invocation failure diagnostic exit code differs from its kind"
            )
        if self.process_exit_code is not None and (
            isinstance(self.process_exit_code, bool)
            or not isinstance(self.process_exit_code, int)
        ):
            raise ArtifactValidationError(
                "invocation failure process_exit_code must be an integer or null"
            )
        for value, label in (
            (self.safety_margin_seconds, "invocation failure safety margin"),
            (self.retry_delay_seconds, "invocation failure retry delay"),
            (self.auto_resume_count, "invocation failure auto-resume count"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ArtifactValidationError(f"{label} must be non-negative")
        if not isinstance(self.automatic_resume, bool):
            raise ArtifactValidationError(
                "invocation failure automatic_resume must be a boolean"
            )
        if self.diff_fingerprint is not None:
            _require_sha256(
                self.diff_fingerprint,
                "invocation failure diff_fingerprint",
            )
        for value, label in (
            (self.reset_at_utc, "invocation failure reset_at_utc"),
            (self.resume_at_utc, "invocation failure resume_at_utc"),
        ):
            if value is not None:
                _require_utc_timestamp(value, label)
        quota_evidence = (
            self.parse_path,
            self.source_timezone,
            self.reset_at_utc,
        )
        if any(value is not None for value in quota_evidence) and not all(
            value is not None for value in quota_evidence
        ):
            raise ArtifactValidationError(
                "invocation failure quota source evidence is partial"
            )
        if self.failure_kind != "quota" and any(
            value is not None for value in quota_evidence
        ):
            raise ArtifactValidationError(
                "non-quota invocation failure carries quota source evidence"
            )
        if self.reset_at_utc is not None:
            assert self.resume_at_utc is not None
            if self.retry_delay_seconds != self.safety_margin_seconds:
                raise ArtifactValidationError(
                    "quota retry delay must equal its safety margin"
                )
            reset = datetime.fromisoformat(
                self.reset_at_utc.replace("Z", "+00:00")
            )
            resume = datetime.fromisoformat(
                self.resume_at_utc.replace("Z", "+00:00")
            )
            if resume != reset + timedelta(seconds=self.safety_margin_seconds):
                raise ArtifactValidationError(
                    "quota resume target is not derived from reset plus margin"
                )
        elif self.safety_margin_seconds != 0:
            raise ArtifactValidationError(
                "invocation failure without quota reset cannot carry a margin"
            )
        automatic_review_form = (
            self.failure_kind == "output"
            and self.role is Role.CLAUDE  # allowlist:provider -- bound reviewer role
            and self.diagnostic_code in {
                "NATIVE-REVIEW-FORM",
                STRUCTURED_OUTPUT_DIAGNOSTIC_CODE,
            }
            and self.step.startswith(f"{self.role.value}_")
            and self.step.endswith("_review")
        )
        automatic_implementer_form = (
            self.failure_kind == "output"
            and self.role is Role.CODEX  # allowlist:provider -- implementer role
            and self.diagnostic_code == "NATIVE-IMPLEMENTER-FORM"
            and self.step.startswith(f"{self.role.value}_")
        )
        automatic_transient = self.automatic_resume and (
            self.failure_kind in {"network", "timeout"}
            or automatic_review_form
            or automatic_implementer_form
        )
        if automatic_transient:
            if self.resume_at_utc is None or self.retry_delay_seconds < 1:
                raise ArtifactValidationError(
                    "automatic transient retry requires target and positive delay"
                )
            decision = datetime.fromisoformat(
                self.decision_at_utc.replace("Z", "+00:00")
            )
            resume = datetime.fromisoformat(
                self.resume_at_utc.replace("Z", "+00:00")
            )
            if resume != decision + timedelta(seconds=self.retry_delay_seconds):
                raise ArtifactValidationError(
                    "transient retry target is not derived from decision plus delay"
                )
        elif self.failure_kind != "quota" and (
            self.resume_at_utc is not None or self.retry_delay_seconds != 0
        ):
            raise ArtifactValidationError(
                "unscheduled invocation failure carries retry timing"
            )
        if self.automatic_resume:
            if (
                self.failure_kind not in {"quota", "network", "timeout"}
                and not automatic_review_form
                and not automatic_implementer_form
            ):
                raise ArtifactValidationError(
                    "automatic resume is limited to quota, network, timeout, and native response form failures"
                )
            if self.resume_at_utc is None or self.auto_resume_count < 1:
                raise ArtifactValidationError(
                    "automatic resume requires target and positive continuation count"
                )


def _validate_native_response_failure_feedback(
    payload: InvocationFailurePayload,
) -> None:
    if payload.native_review_rejection is not None:
        if payload.native_review_rejection not in _NATIVE_REVIEW_RESPONSE_REJECTION_CODES:
            raise ArtifactValidationError(
                "invocation failure native review rejection is invalid"
            )
        if not (
            payload.role not in {Role.ORCHESTRATOR, Role.USER}
            and payload.failure_kind == "output"
            and payload.diagnostic_code == "NATIVE-REVIEW-FORM"
            and payload.step.startswith(f"{payload.role.value}_")
            and payload.step.endswith("_review")
        ):
            raise ArtifactValidationError(
                "native review rejection requires a reviewer output failure"
            )
    if (payload.native_review_rejection is None) != (
        payload.native_review_retry_round is None
    ):
        raise ArtifactValidationError(
            "native review rejection and retry round must be bound together"
        )
    if payload.native_review_retry_round is not None:
        _require_positive(
            payload.native_review_retry_round,
            "invocation failure native review retry round",
        )
    if payload.native_implementer_rejection is not None:
        if (
            payload.native_implementer_rejection
            not in _NATIVE_IMPLEMENTER_RESPONSE_REJECTION_CODES
        ):
            raise ArtifactValidationError(
                "invocation failure native implementer rejection is invalid"
            )
        if not (
            payload.role is Role.CODEX  # allowlist:provider -- implementer role
            and payload.failure_kind == "output"
            and payload.diagnostic_code == "NATIVE-IMPLEMENTER-FORM"
            and payload.step.startswith(f"{payload.role.value}_")
        ):
            raise ArtifactValidationError(
                "native implementer rejection requires an implementer output failure"
            )
        if payload.orchestrator_diagnostic is None or not (
            payload.orchestrator_diagnostic.startswith(
                f"{payload.native_implementer_rejection}: "
            )
        ):
            raise ArtifactValidationError(
                "native implementer rejection requires matching closed guidance"
            )
    if (payload.native_implementer_rejection is None) != (
        payload.native_implementer_retry_round is None
    ):
        raise ArtifactValidationError(
            "native implementer rejection and retry round must be bound together"
        )
    if payload.native_implementer_retry_round is not None:
        _require_positive(
            payload.native_implementer_retry_round,
            "invocation failure native implementer retry round",
        )
    if (
        payload.native_review_rejection is not None
        and payload.native_implementer_rejection is not None
    ):
        raise ArtifactValidationError(
            "invocation failure cannot contain two native rejection roles"
        )


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
    | WorkflowTransitionPayload | WorkflowEventPayload
    | WorkflowPolicyPayload | SliceBoundaryPayload
    | TaskPayload | PlanPayload | WorkUnitPayload | CorrectionWorkUnitPayload
    | AgentResultPayload | DiagnosticPayload | ReviewPayload
    | BranchDiscoveryCompletedPayload
    | ReviewAnchorPayload | ReviewValidationBindingPayload
    | FindingTransitionPayload
    | FindingHandoffExportPayload | FindingHandoffImportPayload
    | BranchDiscoveryHandoffExportPayload | BranchDiscoveryHandoffImportPayload
    | PlanAssignmentPayload | RemediationCohortCheckpointPayload
    | NoImplementationRequiredPayload | ClosedFindingOccurrencePayload
    | ValidationRequestPayload | ValidationContentPayload
    | ValidationAttestationPayload | ProviderContentPayload
    | ReviewPacketPayload | GatePayload
    | GateTransitionPayload | GateDecisionPayload | BindingPayload
    | InvocationFailurePayload | QuotaPausePayload
    | TransientRetryPayload | ResumeCheckPayload
    | WorkflowCompletionPayload
    | ProviderInputMeasurementPayload | ProviderAttemptPayload | FinalReviewPreflightPayload
    | SideEffectPayload
)


def artifact_payload_document(payload: ArtifactPayload) -> dict[str, Any]:
    """Serialize one payload while preserving its optional-field wire shape."""
    raw = asdict(payload)
    if isinstance(payload, RunProfilePayload) and payload.family_binding is None:
        raw.pop("family_binding", None)
    elif isinstance(payload, RunProfilePayload):
        raw["family_binding"] = family_binding_document(payload.family_binding)
    if isinstance(payload, PlanPayload):
        raw["slices"] = [_slice_spec_document(item) for item in payload.slices]
    if isinstance(payload, AgentResultPayload):
        raw["slice_plan"] = [
            _slice_spec_document(item) for item in payload.slice_plan
        ]
        if not payload.plan_treatments:
            raw.pop("plan_treatments", None)
        else:
            treatments: list[dict[str, Any]] = []
            for treatment in payload.plan_treatments:
                item = asdict(treatment)
                if treatment.treatment_kind == "implementation":
                    item.pop("no_code_reason", None)
                    item.pop("evidence", None)
                    item.pop("evidence_paths", None)
                    item.pop("affected_paths", None)
                else:
                    item.pop("closing_slice_ids", None)
                treatments.append(item)
            raw["plan_treatments"] = treatments
        if payload.plan_completion is None:
            raw.pop("plan_completion", None)
    if isinstance(payload, ReviewPayload):
        if payload.review_evidence is None:
            raw.pop("review_evidence", None)
        if payload.red_state_followup_slice is None:
            raw.pop("red_state_followup_slice", None)
        if not payload.plan_treatment_decisions:
            raw.pop("plan_treatment_decisions", None)
    if (
        isinstance(payload, BranchDiscoveryCompletedPayload)
        and payload.scan_complete is None
    ):
        raw.pop("scan_complete", None)
    if isinstance(payload, BranchDiscoveryCompletedPayload):
        findings: list[dict[str, Any]] = []
        for finding in payload.new_findings:
            item = asdict(finding)
            if finding.predecessor_finding_ref is None:
                item.pop("predecessor_finding_ref", None)
                item.pop("evidence_anchor_sha256", None)
            findings.append(item)
        raw["new_findings"] = findings
        occurrences: list[dict[str, Any]] = []
        for occurrence in payload.occurrences:
            item = asdict(occurrence)
            if occurrence.evidence_anchor_sha256 is None:
                item.pop("evidence_anchor_sha256", None)
            occurrences.append(item)
        raw["occurrences"] = occurrences
    if isinstance(payload, FindingTransitionPayload):
        if payload.responsibility is None:
            raw.pop("responsibility", None)
        else:
            raw["responsibility"] = responsibility_document(payload.responsibility)
        if payload.closure_kind is None:
            raw.pop("closure_kind", None)
            raw.pop("rejection_reason", None)
            raw.pop("closure_evidence", None)
            raw.pop("remaining_work", None)
        elif payload.closure_kind == "fixed":
            raw.pop("rejection_reason", None)
            raw.pop("closure_evidence", None)
            raw.pop("remaining_work", None)
        elif payload.closure_kind == "partial":
            raw.pop("rejection_reason", None)
        elif payload.closure_kind == "rejected":
            raw.pop("remaining_work", None)
        if payload.acceptance_command is None:
            raw.pop("acceptance_command", None)
            raw.pop("acceptance_outcome", None)
            raw.pop("acceptance_exit_code", None)
            raw.pop("acceptance_output_sha256", None)
            raw.pop("acceptance_attestation_id", None)
            raw.pop("acceptance_fingerprint", None)
        if payload.predecessor_finding_ref is None:
            raw.pop("predecessor_finding_ref", None)
            raw.pop("evidence_anchor_sha256", None)
        if payload.action != "opened" or payload.affected_paths is None:
            raw.pop("affected_paths", None)
    if isinstance(
        payload,
        (FindingHandoffImportPayload, BranchDiscoveryHandoffImportPayload),
    ):
        raw["transitions"] = [
            imported_finding_transition_document(item)
            for item in payload.transitions
        ]
    if isinstance(
        payload,
        (BranchDiscoveryHandoffExportPayload, BranchDiscoveryHandoffImportPayload),
    ):
        if payload.target_execution_mode == "PLAN_ONLY":
            raw.pop("target_execution_mode", None)
        if payload.source_completion_record_id is None:
            raw.pop("source_completion_record_id", None)
        if payload.remediation_cohort_checkpoint_record_id is None:
            raw.pop("remediation_cohort_checkpoint_record_id", None)
        if not payload.closed_finding_dispositions:
            raw.pop("closed_finding_dispositions", None)
    if isinstance(payload, PlanAssignmentPayload):
        raw["slices"] = [_slice_spec_document(item) for item in payload.slices]
        treatments: list[dict[str, Any]] = []
        for treatment in payload.treatments:
            item = asdict(treatment)
            if treatment.treatment_kind == "implementation":
                item.pop("no_code_reason", None)
                item.pop("evidence", None)
                item.pop("authoritative_fingerprint", None)
                item.pop("evidence_anchor", None)
            else:
                item.pop("closing_slice_ids", None)
                if treatment.evidence_anchor is None:
                    item.pop("evidence_anchor", None)
            treatments.append(item)
        raw["treatments"] = treatments
        if payload.plan_completion is None:
            raw.pop("plan_completion", None)
    if (
        isinstance(payload, InvocationFailurePayload)
        and payload.orchestrator_diagnostic is None
    ):
        raw.pop("orchestrator_diagnostic", None)
    if (
        isinstance(payload, InvocationFailurePayload)
        and payload.native_review_rejection is None
    ):
        raw.pop("native_review_rejection", None)
        raw.pop("native_review_retry_round", None)
    if (
        isinstance(payload, InvocationFailurePayload)
        and payload.native_implementer_rejection is None
    ):
        raw.pop("native_implementer_rejection", None)
        raw.pop("native_implementer_retry_round", None)
    if isinstance(payload, GateDecisionPayload) and payload.invocation_id is None:
        raw.pop("invocation_id", None)
    return _json_value(raw)


def _slice_spec_document(spec: SliceSpec) -> dict[str, Any]:
    document: dict[str, Any] = {
        "slice_id": spec.slice_id,
        "summary": spec.summary,
        "paths": list(spec.paths),
    }
    if spec.acceptance_criteria:
        document["acceptance_criteria"] = [
            {
                "criterion_id": item.criterion_id,
                "text": item.text,
                "measured_against": item.measured_against.value,
            }
            for item in spec.acceptance_criteria
        ]
    return document


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
        raw["payload"] = artifact_payload_document(self.payload)
        return _json_value(raw)

    def canonical_json(self) -> bytes:
        return canonical_json(self.to_dict())

    def validate_schema(self) -> None:
        validate_artifact_document(self.to_dict())


def flatten_finding_transition_history(
    records: Sequence[ArtifactRecord],
) -> tuple[ImportedFindingTransition, ...]:
    """Flatten imported history plus local facts with stable provenance."""

    flattened: list[ImportedFindingTransition] = []
    by_identity: dict[tuple[str, str], ImportedFindingTransition] = {}
    for record in records:
        payload = record.payload
        if isinstance(
            payload,
            (FindingHandoffImportPayload, BranchDiscoveryHandoffImportPayload),
        ):
            candidates = tuple(
                ImportedFindingTransition(
                    item.record_id,
                    item.payload,
                    item.source_run_id or payload.source_run_id,
                    item.source_record_id or item.record_id,
                )
                for item in payload.transitions
            )
        elif isinstance(payload, FindingTransitionPayload):
            candidates = (
                ImportedFindingTransition(
                    record.record_id,
                    payload,
                    record.run_id,
                    record.record_id,
                ),
            )
        else:
            continue
        for candidate in candidates:
            assert candidate.source_run_id is not None
            assert candidate.source_record_id is not None
            identity = (
                candidate.source_run_id,
                candidate.source_record_id,
            )
            prior = by_identity.get(identity)
            if prior is not None:
                if prior.payload != candidate.payload:
                    raise ArtifactValidationError(
                        "duplicate finding transition provenance has different payload"
                    )
                continue
            by_identity[identity] = candidate
            flattened.append(candidate)
    return tuple(flattened)


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


@lru_cache(maxsize=1)
def _validated_schema() -> dict[str, Any]:
    """Load and self-check the private immutable-at-runtime schema source."""
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    if not isinstance(schema, dict):
        raise ArtifactValidationError("bundled artifact schema must be a JSON object")
    try:
        check_schema(schema)
    except SchemaDefinitionError as error:
        raise ArtifactValidationError(str(error)) from error
    return schema


def load_schema() -> dict[str, Any]:
    """Return an isolated copy of the checked bundled schema."""
    return copy.deepcopy(_validated_schema())


def validate_artifact_document(document: Mapping[str, Any]) -> None:
    """Validate a document against the bundled, closed v2 schema offline.

    The repository intentionally has no runtime dependencies.  This validator
    implements the Draft 2020-12 keywords used by the bundled schema and the
    schema self-check rejects unknown keywords, preventing an unsupported
    extension from being accepted silently.
    """
    payload = document.get("payload")
    if (
        document.get("record_type") == RecordType.RUN_PROFILE.value
        and isinstance(payload, Mapping)
        and isinstance(payload.get("reducer_version"), str)
        and payload["reducer_version"] != STATE_PROJECTION_REDUCER_VERSION
    ):
        raise ArtifactValidationError(
            "run profile reducer_version is unsupported for resume; "
            f"inspect historical chains with {LEGACY_CHAIN_VERIFIER}"
        )
    schema = _validated_schema()
    try:
        validate_schema_document(document, schema)
    except SchemaMismatch as error:
        location = ".".join(str(part) for part in error.path) or "<record>"
        raise ArtifactValidationError(
            f"schema validation failed at {location}: {error.message}"
        ) from None


def _slice_spec_from_dict(data: Mapping[str, Any]) -> SliceSpec:
    try:
        criteria = acceptance_criteria_from_documents(
            data["slice_id"], data.get("acceptance_criteria", ())
        )
        return SliceSpec(
            data["slice_id"], data["summary"], tuple(data["paths"]), criteria
        )
    except ValueError as exc:
        raise ArtifactValidationError(str(exc)) from exc


def _imported_finding_transition_from_dict(
    data: Mapping[str, Any],
) -> ImportedFindingTransition:
    return ImportedFindingTransition(
        data["record_id"],
        _payload_from_dict(RecordType.FINDING_TRANSITION, data["payload"]),
        data.get("source_run_id"),
        data.get("source_record_id"),
    )


def _finding_snapshot_item_from_dict(
    data: Mapping[str, Any],
) -> FindingSnapshotItem:
    return FindingSnapshotItem(
        data["finding_id"],
        data["signature"],
        data["finding_status"],
        FindingSeverity(data["severity"]),
    )


def _plan_treatment_assignment_from_dict(
    data: Mapping[str, Any],
) -> PlanTreatmentAssignment:
    return PlanTreatmentAssignment(
        signature=data["signature"],
        finding_ids=tuple(data["finding_ids"]),
        treatment_kind=data["treatment_kind"],
        closing_slice_ids=tuple(data.get("closing_slice_ids", ())),
        no_code_reason=data.get("no_code_reason"),
        evidence=data.get("evidence"),
        authoritative_fingerprint=data.get("authoritative_fingerprint"),
        evidence_anchor=(
            None
            if data.get("evidence_anchor") is None
            else _no_code_evidence_anchor_from_dict(data["evidence_anchor"])
        ),
    )


def _no_code_evidence_anchor_from_dict(
    data: Mapping[str, Any],
) -> native_finding_decisions.NoCodeEvidenceAnchor:
    return native_finding_decisions.NoCodeEvidenceAnchor(
        rejection_reason=native_finding_decisions.NativeRejectionReason(
            data["rejection_reason"]
        ),
        provenance_fingerprint=data["provenance_fingerprint"],
        evidence_paths=tuple(data["evidence_paths"]),
        evidence_content_sha256=data["evidence_content_sha256"],
        task_sha256=data.get("task_sha256"),
        scope_sha256=data.get("scope_sha256"),
        affected_paths=tuple(data.get("affected_paths", ())),
        affected_content_sha256=data.get("affected_content_sha256"),
    )


def _closed_finding_disposition_from_dict(
    data: Mapping[str, Any],
) -> ClosedFindingDispositionSnapshot:
    return ClosedFindingDispositionSnapshot(
        finding_id=data["finding_id"],
        signature=data["signature"],
        source_plan_assignment_record_id=data[
            "source_plan_assignment_record_id"
        ],
        evidence_anchor=_no_code_evidence_anchor_from_dict(
            data["evidence_anchor"]
        ),
    )


_PAYLOAD_READERS: dict[
    RecordType, Callable[[Mapping[str, Any]], ArtifactPayload]
] = {
    RecordType.RUN_IDENTITY: lambda data: RunIdentityPayload(
            data["task_file"], data["branch"], data["branch_base"],
            data["first_slice_start_commit"], data["execution_mode"],
            data["audit_report_path"],
        ),
    RecordType.RUN_PROFILE: lambda data: RunProfilePayload(
            RoleProfilePayload(**data["implementer"]),
            RoleProfilePayload(**data["reviewer"]),
            data["orchestrator_code_version"],
            data["reducer_version"],
            (
                None
                if data.get("family_binding") is None
                else FamilyBindingPayload(
                    family_id=data["family_binding"]["family_id"],
                    family_base_commit=data["family_binding"]["family_base_commit"],
                    family_authorized_change_set=tuple(
                        data["family_binding"]["family_authorized_change_set"]
                    ),
                    predecessor_run_id=data["family_binding"]["predecessor_run_id"],
                    predecessor_head_record_id=data["family_binding"][
                        "predecessor_head_record_id"
                    ],
                    cycle_number=data["family_binding"]["cycle_number"],
                    current_plan_commit=data["family_binding"]["current_plan_commit"],
                    current_implementation_commit=data["family_binding"][
                        "current_implementation_commit"
                    ],
                )
            ),
        ),
    RecordType.WORKFLOW_TRANSITION: lambda data: WorkflowTransitionPayload(
            data["slice_id"], data["slice_status"], data["work_unit_id"],
            data["step"], data["work_unit_status"],
        ),
    RecordType.WORKFLOW_EVENT: lambda data: WorkflowEventPayload(
            data["event_kind"], data["work_unit_id"], data["slice_id"],
            data["round_number"], tuple(data["record_refs"]),
        ),
    RecordType.WORKFLOW_POLICY: lambda data: WorkflowPolicyPayload(
            data["work_unit_id"], data["implementer_return_count"],
            data["max_implementer_returns"],
        ),
    RecordType.SLICE_BOUNDARY: lambda data: SliceBoundaryPayload(
            data["slice_id"],
            data["start_commit"],
            tuple(tuple(group) for group in data["scope_change_groups"]),
            data["start_fingerprint"],
        ),
    RecordType.TASK: lambda data: TaskPayload(
            data["target_branch"],
            tuple(data["scope_paths"]),
            data["assignment_sha256"],
            data["work_plan_path"],
        ),
    RecordType.PLAN: lambda data: PlanPayload(
        data["work_plan_path"],
        data["approved_plan_commit"],
        tuple(_slice_spec_from_dict(item) for item in data["slices"]),
    ),
    RecordType.WORK_UNIT: lambda data: WorkUnitPayload(
            data["slice_id"], data["round_number"], tuple(data["paths"]),
            tuple(data.get("open_finding_ids", ())), data.get("finding_import_record_id"),
        ),
    RecordType.CORRECTION_WORK_UNIT: lambda data: CorrectionWorkUnitPayload(
        data["slice_id"], data["round_number"], tuple(data["paths"]),
        tuple(data["finding_ids"]),
    ),
    RecordType.AGENT_RESULT: lambda data: AgentResultPayload(
            Role(data["role"]),
            data["work_unit_id"],
            data["outcome"],
            tuple(data["test_files"]),
            data["transport_schema"],
            data["request_id"],
            data["response_sha256"],
            tuple(_slice_spec_from_dict(item) for item in data["slice_plan"]),
            tuple(
                PlanTreatmentProposalPayload(
                    item["signature"],
                    tuple(item["finding_ids"]),
                    item["treatment_kind"],
                    tuple(item.get("closing_slice_ids", ())),
                    item.get("no_code_reason"),
                    item.get("evidence"),
                    tuple(item.get("evidence_paths", ())),
                    tuple(item.get("affected_paths", ())),
                )
                for item in data.get("plan_treatments", ())
            ),
            data.get("plan_completion"),
        ),
    RecordType.DIAGNOSTIC: lambda data: DiagnosticPayload(
        Role(data["role"]), data["work_unit_id"], data["attempt"],
        data["output_sha256"], data["reason"],
    ),
    RecordType.REVIEW: lambda data: ReviewPayload(
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
                if data.get("review_evidence") is None
                else ReviewEvidencePayload(
                    data["review_evidence"]["dimensions"],
                    data["review_evidence"]["largest_residual_risk"],
                    data["review_evidence"]["break_condition"],
                )
            ),
            data.get("red_state_followup_slice"),
            tuple(data["test_files"]),
            data["pre_mortem"],
            (
                None
                if data.get("stop_request") is None
                else ReviewStopRequestPayload(
                    data["stop_request"]["rule_id"],
                    data["stop_request"]["rationale"],
                    tuple(data["stop_request"]["remediation_paths"]),
                )
            ),
            tuple(
                PlanTreatmentDecisionPayload(
                    item["signature"], item["decision"], item["rationale"]
                )
                for item in data.get("plan_treatment_decisions", ())
            ),
        ),
    RecordType.BRANCH_DISCOVERY_COMPLETED: lambda data: BranchDiscoveryCompletedPayload(
            reviewer=Role(data["reviewer"]),
            work_unit_id=data["work_unit_id"],
            new_findings=tuple(
                BranchDiscoveryFindingPayload(
                    item["finding_id"],
                    FindingSeverity(item["severity"]),
                    item["summary"],
                    item["acceptance_test"],
                    item.get("predecessor_finding_ref"),
                    item.get("evidence_anchor_sha256"),
                    tuple(item["affected_paths"]),
                )
                for item in data["new_findings"]
            ),
            occurrences=tuple(
                BranchDiscoveryOccurrencePayload(
                    item["finding_id"],
                    item["rationale"],
                    item.get("evidence_anchor_sha256"),
                )
                for item in data["occurrences"]
            ),
            review_evidence=ReviewEvidencePayload(
                data["review_evidence"]["dimensions"],
                data["review_evidence"]["largest_residual_risk"],
                data["review_evidence"]["break_condition"],
            ),
            pre_mortem=data["pre_mortem"],
            validation_attestation_record_id=data[
                "validation_attestation_record_id"
            ],
            reviewed_head_commit=data["reviewed_head_commit"],
            transport_schema=data["transport_schema"],
            request_id=data["request_id"],
            response_sha256=data["response_sha256"],
            scan_complete=data.get("scan_complete"),
        ),
    RecordType.REVIEW_ANCHOR: lambda data: ReviewAnchorPayload(
            data["review_record_id"],
            tuple(
                ReviewAnchor(
                    item["anchor_id"],
                    item["origin"],
                    item["input_fixture"],
                    item["expected"],
                    item["tolerance"],
                )
                for item in data["anchors"]
            ),
        ),
    RecordType.REVIEW_VALIDATION_BINDING: lambda data: ReviewValidationBindingPayload(
            data["review_record_id"],
            data["attestation_record_id"],
        ),
    RecordType.FINDING_TRANSITION: lambda data: FindingTransitionPayload(
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
            responsibility=(
                None
                if "responsibility" not in data
                else parse_responsibility(data["responsibility"])
            ),
            closure_kind=data.get("closure_kind"),
            rejection_reason=data.get("rejection_reason"),
            closure_evidence=data.get("closure_evidence"),
            remaining_work=data.get("remaining_work"),
            acceptance_command=(
                None
                if data.get("acceptance_command") is None
                else CommandSpec(
                    data["acceptance_command"]["family"],
                    tuple(data["acceptance_command"]["argv"]),
                    data["acceptance_command"]["mode"],
                )
            ),
            acceptance_outcome=data.get("acceptance_outcome"),
            acceptance_exit_code=data.get("acceptance_exit_code"),
            acceptance_output_sha256=data.get("acceptance_output_sha256"),
            acceptance_attestation_id=data.get("acceptance_attestation_id"),
            acceptance_fingerprint=data.get("acceptance_fingerprint"),
            predecessor_finding_ref=data.get("predecessor_finding_ref"),
            evidence_anchor_sha256=data.get("evidence_anchor_sha256"),
            affected_paths=(
                None
                if data["action"] == "opened" and "affected_paths" not in data
                else tuple(data.get("affected_paths", ()))
            ),
        ),
    RecordType.FINDING_HANDOFF_EXPORT: lambda data: FindingHandoffExportPayload(
            data["source_run_id"], data["source_head_record_id"],
            data["approved_plan_commit"], data["approval_review_record_id"],
            tuple(data["finding_transition_record_ids"]),
            data["finding_transitions_sha256"], data["target_task_path"],
            data["target_task_sha256"], Role(data["authority"]),
        ),
    RecordType.FINDING_HANDOFF_IMPORT: lambda data: FindingHandoffImportPayload(
            data["source_run_id"], data["source_head_record_id"],
            data["approved_plan_commit"], data["approval_review_record_id"],
            data["export_record_id"], data["target_run_id"],
            data["target_task_sha256"], data["finding_transitions_sha256"],
            tuple(
                _imported_finding_transition_from_dict(item)
                for item in data["transitions"]
            ),
            Role(data["authority"]),
        ),
    RecordType.BRANCH_DISCOVERY_HANDOFF_EXPORT: lambda data: BranchDiscoveryHandoffExportPayload(
            data["source_run_id"], data["source_head_record_id"],
            data.get("discovery_review_record_id"),
            data["validation_attestation_record_id"],
            data["reviewed_head_commit"], data["family_id"],
            data["family_base_commit"], data["cycle_number"],
            data["predecessor_run_id"], data["predecessor_head_record_id"],
            tuple(data["finding_transition_record_ids"]),
            data["finding_transitions_sha256"], data["target_task_path"],
            data["target_task_sha256"], data["target_run_identity"],
            Role(data["authority"]),
            data.get("target_execution_mode", "PLAN_ONLY"),
            data.get("source_completion_record_id"),
            data.get("remediation_cohort_checkpoint_record_id"),
            tuple(
                _closed_finding_disposition_from_dict(item)
                for item in data.get("closed_finding_dispositions", ())
            ),
        ),
    RecordType.BRANCH_DISCOVERY_HANDOFF_IMPORT: lambda data: BranchDiscoveryHandoffImportPayload(
            data["source_run_id"], data["source_head_record_id"],
            data.get("discovery_review_record_id"),
            data["validation_attestation_record_id"],
            data["reviewed_head_commit"], data["family_id"],
            data["family_base_commit"], data["cycle_number"],
            data["predecessor_run_id"], data["predecessor_head_record_id"],
            data["export_record_id"], data["target_run_id"],
            data["target_task_path"], data["target_task_sha256"],
            data["target_run_identity"], data["finding_transitions_sha256"],
            tuple(
                _imported_finding_transition_from_dict(item)
                for item in data["transitions"]
            ),
            tuple(
                _finding_snapshot_item_from_dict(item)
                for item in data["finding_snapshot"]
            ),
            Role(data["authority"]),
            data.get("target_execution_mode", "PLAN_ONLY"),
            data.get("source_completion_record_id"),
            data.get("remediation_cohort_checkpoint_record_id"),
            tuple(
                _closed_finding_disposition_from_dict(item)
                for item in data.get("closed_finding_dispositions", ())
            ),
        ),
    RecordType.PLAN_ASSIGNMENT: lambda data: PlanAssignmentPayload(
            data["family_id"], data["cycle_number"],
            data["remediation_round_number"], data["source_snapshot_record_id"],
            data["finding_snapshot_sha256"], data["plan_result_record_id"],
            data["review_record_id"],
            data["review_fingerprint"],
            tuple(_plan_treatment_assignment_from_dict(item) for item in data["treatments"]),
            tuple(_slice_spec_from_dict(item) for item in data["slices"]),
            tuple(data["implementation_scope"]), Role(data["authority"]),
            data.get("plan_completion"),
        ),
    RecordType.REMEDIATION_COHORT_CHECKPOINT: lambda data: RemediationCohortCheckpointPayload(
            data["family_id"], data["remediation_round_number"],
            data["plan_assignment_record_id"], data["implementation_run_id"],
            data["implementation_head_record_id"],
            tuple(data["inherited_signatures"]),
            tuple(data["unresolved_inherited_signatures"]),
            Role(data["authority"]),
        ),
    RecordType.NO_IMPLEMENTATION_REQUIRED: lambda data: NoImplementationRequiredPayload(
            data["family_id"], data["cycle_number"],
            data["remediation_round_number"],
            data["plan_assignment_record_id"], data["review_record_id"],
            data["reviewed_plan_commit"], tuple(data["closed_finding_ids"]),
            Role(data["authority"]),
        ),
    RecordType.CLOSED_FINDING_OCCURRENCE: lambda data: ClosedFindingOccurrencePayload(
            data["finding_id"], data["signature"],
            data["source_plan_assignment_record_id"],
            data["discovery_completion_record_id"],
            data["evidence_anchor_sha256"], data["rationale"],
            Role(data["reviewer"]),
        ),
    RecordType.VALIDATION_REQUEST: lambda data: ValidationRequestPayload(
        tuple(
            CommandSpec(item["family"], tuple(item["argv"]), item["mode"])
            for item in data["commands"]
        ),
        Role(data["requested_by"]),
    ),
    RecordType.VALIDATION_CONTENT: lambda data: ValidationContentPayload(
            data["attestation_id"],
            data["result_record_id"],
            data["digest_format"],
            data["output_digest"],
            data["summary"],
            tuple(
                ValidationOutputContent(
                    CommandSpec(
                        item["command"]["family"],
                        tuple(item["command"]["argv"]),
                        item["command"]["mode"],
                    ),
                    item["digest_outcome"],
                    item["exit_code"],
                    BlobReference(**item["raw_stdout"]),
                    BlobReference(**item["raw_stderr"]),
                    BlobReference(**item["compact_output"]),
                    item["output_bytes"],
                )
                for item in data["outputs"]
            ),
        ),
    RecordType.VALIDATION_ATTESTATION: lambda data: ValidationAttestationPayload(
            tuple(
                ValidationResult(
                    CommandSpec(
                        item["command"]["family"],
                        tuple(item["command"]["argv"]),
                        item["command"]["mode"],
                    ),
                    item["outcome"],
                    item["exit_code"],
                    item["output_sha256"],
                )
                for item in data["results"]
            ),
            Role(data["attested_by"]),
            data["output_digest"],
            data["content_record_id"],
        ),
    RecordType.PROVIDER_CONTENT: lambda data: ProviderContentPayload(
            Role(data["role"]),
            data["work_unit_id"],
            data["round_number"],
            data["operation"],
            data["request_id"],
            data["response_sha256"],
            data["content_kind"],
            data["content_bytes"],
            BlobReference(**data["blob"]),
        ),
    RecordType.REVIEW_PACKET: lambda data: ReviewPacketPayload(
            data["work_unit_id"],
            data["fingerprint"],
            data["purpose"],
            tuple(data["manifest"]),
            data["diff_coverage_sha256"],
            data["content_bytes"],
            BlobReference(**data["blob"]),
        ),
    RecordType.GATE: lambda data: GatePayload(
        data["gate_kind"], data["decision"], Role(data["authority"]),
        data["rationale"],
    ),
    RecordType.GATE_TRANSITION: lambda data: GateTransitionPayload(
            data["work_unit_id"], data["gate_status"], data["reason"],
            data["detail"], data["fingerprint"], tuple(data["paths"]),
            data["resume_step"], data["active_test_fingerprint"],
            tuple(data["active_test_paths"]),
        ),
    RecordType.GATE_DECISION: lambda data: GateDecisionPayload(
            data["work_unit_id"], data["gate_record_id"], tuple(data["paths"]),
            data["resume_step"], data.get("invocation_id"),
        ),
    RecordType.BINDING: lambda data: BindingPayload(
        data["binding_kind"], data["target"], data["attestation_id"],
        tuple(data["approval_ids"]),
    ),
    RecordType.INVOCATION_FAILURE: lambda data: InvocationFailurePayload(
            data["invocation_id"], data["idempotency_key"], Role(data["role"]),
            data["failure_kind"], data["failure_class"], data["diagnostic_code"],
            data["provider_text"], data["provider_text_sha256"],
            data["provider_text_bytes"], data["technical_text"],
            data["technical_text_sha256"], data["technical_text_bytes"],
            data["received_at"],
            data["decision_at_utc"], data["step"], data["slice_id"],
            data["work_unit_id"], data["diagnostic_exit_code"],
            data["process_exit_code"],
            data["parse_path"], data["source_timezone"], data["reset_at_utc"],
            data["resume_at_utc"], data["safety_margin_seconds"],
            data["retry_delay_seconds"], data["auto_resume_count"],
            data["automatic_resume"], data["diff_fingerprint"],
            data.get("orchestrator_diagnostic"),
            data.get("native_review_rejection"),
            data.get("native_review_retry_round"),
            data.get("native_implementer_rejection"),
            data.get("native_implementer_retry_round"),
        ),
    RecordType.QUOTA_PAUSE: lambda data: QuotaPausePayload(
        Role(data["role"]), data["repository_fingerprint"], data["retry_at"],
    ),
    RecordType.TRANSIENT_RETRY: lambda data: TransientRetryPayload(
            Role(data["role"]),
            data["repository_fingerprint"],
            data["retry_at"],
            data["attempt"],
        ),
    RecordType.RESUME_CHECK: lambda data: ResumeCheckPayload(
        data["expected_head_id"], data["repository_fingerprint"], data["outcome"],
    ),
    RecordType.WORKFLOW_COMPLETION: lambda data: WorkflowCompletionPayload(
        data["outcome"], data["final_binding_id"],
    ),
    RecordType.PROVIDER_INPUT_MEASUREMENT: lambda data: ProviderInputMeasurementPayload(
            Role(data["provider"]), Role(data["role"]), data["operation"], data["work_unit_id"],
            data["transition_fingerprint"], data["relevant_record_head"], data["input_digest"], data["policy_digest"],
            tuple(ProviderInputComponentPayload(item["name"], item["chars"], item["bytes"]) for item in data["components"]),
            data["total_chars"], data["total_bytes"], data["safety_limit_chars"], data["safety_limit_bytes"],
            data["technical_limit_chars"], data["technical_limit_bytes"], data["technical_limit_source"],
            data["effective_limit_chars"], data["effective_limit_bytes"], data["allowed"],
            tuple(data["violated_dimensions"]), data["char_overage"], data["byte_overage"], data["largest_component"],
        ),
    RecordType.PROVIDER_ATTEMPT: lambda data: ProviderAttemptPayload(
            Role(data["provider"]), Role(data["role"]), data["operation"], data["work_unit_id"],
            data["logical_operation_id"], data["binding_fingerprint"], data["measurement_record_id"],
            data["input_digest"], data["attempt_number"], data["phase"], data["started_at"],
            data["ended_at"], data["duration_seconds"], data["failure_kind"],
            ProviderUsagePayload(**data["usage"]) if data["usage"] is not None else None,
            data["model"], data["effort"],
        ),
    RecordType.SIDE_EFFECT: lambda data: SideEffectPayload(
            data["effect_key"], data["effect_class"], data["work_unit_id"],
            tuple(data["operation"]), data["phase"], data["result"],
        ),
    RecordType.FINAL_REVIEW_PREFLIGHT: lambda data: FinalReviewPreflightPayload(
            Role(data["provider"]), Role(data["role"]), data["operation"], data["work_unit_id"],
            data["transition_fingerprint"], data["relevant_record_head"], data["measurement_record_id"],
            data["outcome"], data["category"], data["error_code"], tuple(data["affected_record_ids"]),
            tuple(data["affected_paths"]), data["remediation"],
        ),
}


def _payload_from_dict(record_type: RecordType, raw: Mapping[str, Any]) -> ArtifactPayload:
    data = dict(raw)
    lookup_type = record_type if isinstance(record_type, RecordType) else object()
    try:
        reader = _PAYLOAD_READERS[lookup_type]  # type: ignore[index]
    except (KeyError, TypeError):
        raise ArtifactValidationError(f"unsupported record_type: {record_type}") from None
    return reader(data)


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


def _require_record_id(value: str, name: str) -> None:
    _require_identifier(value, name)
    if not value.startswith("ar1-") or len(value) != 68:
        raise ArtifactValidationError(f"{name} must identify an artifact record")


def _require_finding_id(value: str, name: str) -> None:
    if not isinstance(value, str) or _FINDING_ID_RE.fullmatch(value) is None:
        raise ArtifactValidationError(f"{name} must be a canonical C-* finding ID")


def _require_sha256(value: str, name: str) -> None:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ArtifactValidationError(f"{name} must be a lowercase SHA-256 digest")


def _require_signature_sequence(
    values: Sequence[str], name: str, *, allow_empty: bool
) -> None:
    if isinstance(values, (str, bytes)) or (not values and not allow_empty):
        raise ArtifactValidationError(f"{name} must be a canonical list")
    if tuple(values) != tuple(sorted(set(values))):
        raise ArtifactValidationError(f"{name} must be sorted and unique")
    for value in values:
        _require_sha256(value, name)


def _require_positive(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ArtifactValidationError(f"{name} must be a positive integer")


def _require_git_sha(value: str, name: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise ArtifactValidationError(
            f"{name} must be a lowercase 40-character Git SHA"
        )


def _require_timestamp(value: str, name: str) -> None:
    _require_text(value, name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ArtifactValidationError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ArtifactValidationError(f"{name} must include a timezone")


def _require_utc_timestamp(value: str, name: str) -> None:
    _require_timestamp(value, name)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ArtifactValidationError(f"{name} must be normalized to UTC")


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
