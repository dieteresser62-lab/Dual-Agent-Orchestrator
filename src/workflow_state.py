from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Mapping

from contracts import PlannedSlice


STATE_VERSION = 3
DEFAULT_MAX_CODEX_RETURNS = 4
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
QUOTA_RESUME_DIFF_PATTERN = re.compile(
    r"^QUOTA-RESUME-DIFF \| .*\bgot ([0-9a-f]{64})$"
)


def quota_resume_diff_acknowledgement(
    invocation_id: str, fingerprint: str
) -> str:
    return f"quota-resume-diff:{invocation_id}:{fingerprint}"


class WorkflowStateValidationError(ValueError):
    """Raised when a version-3 workflow state is structurally inconsistent."""


class WorkUnitKind(str, Enum):
    PLAN = "plan"
    SLICE = "slice"
    CORRECTION = "correction"
    FINAL_REVIEW = "final_review"


class WorkflowStep(str, Enum):
    CODEX_PLAN = "codex_plan"
    CLAUDE_PLAN_REVIEW = "claude_plan_review"
    ANTIGRAVITY_PLAN_REVIEW = "antigravity_plan_review"
    CODEX_PLAN_REVISION = "codex_plan_revision"
    CODEX_IMPLEMENTATION = "codex_implementation"
    CLAUDE_SLICE_REVIEW = "claude_slice_review"
    CODEX_CORRECTION = "codex_correction"
    ANTIGRAVITY_SLICE_REVIEW = "antigravity_slice_review"
    SLICE_COMMIT = "slice_commit"
    CODEX_FINAL_REVIEW = "codex_final_review"
    CODEX_FINAL_CORRECTION = "codex_final_correction"
    CLAUDE_FINAL_REVIEW = "claude_final_review"
    ANTIGRAVITY_FINAL_REVIEW = "antigravity_final_review"
    COMPLETED = "completed"


class WorkUnitStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    AWAITING_USER_DECISION = "awaiting_user_decision"
    WAITING_FOR_QUOTA = "waiting_for_quota"
    AWAITING_RESUME = "awaiting_resume"
    COMPLETED = "completed"


class SliceStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    AWAITING_USER_DECISION = "awaiting_user_decision"
    WAITING_FOR_QUOTA = "waiting_for_quota"
    AWAITING_RESUME = "awaiting_resume"
    COMPLETED = "completed"


class GateStatus(str, Enum):
    CLEAR = "clear"
    AWAITING_USER_DECISION = "awaiting_user_decision"
    WAITING_FOR_QUOTA = "waiting_for_quota"
    AWAITING_RESUME = "awaiting_resume"


class GateReason(str, Enum):
    NONE = "none"
    ITERATION_LIMIT = "iteration_limit"
    TEST_CHANGE = "test_change"
    STOP_REQUEST = "stop_request"
    UNEXPECTED_FILE = "unexpected_file"
    ANCHOR_CHANGE = "anchor_change"
    MANUAL_SLICE = "manual_slice"
    PLAN_APPROVAL = "plan_approval"
    QUOTA = "quota"
    INSTANCE_FAILURE = "instance_failure"


class AgentFailureKind(str, Enum):
    QUOTA = "quota"
    AUTH = "auth"
    NETWORK = "network"
    PERMISSION = "permission"
    TIMEOUT = "timeout"
    BINARY = "binary"
    PROCESS = "process"
    OUTPUT = "output"
    RUNTIME = "runtime"


class Reviewer(str, Enum):
    CLAUDE = "claude"
    ANTIGRAVITY = "antigravity"


class ProtocolMode(str, Enum):
    LEGACY_STATE_V3 = "legacy-state-v3"
    STRUCTURED_V1 = "structured-v1"


@dataclass(frozen=True)
class ProtocolBinding:
    """Immutable selection of the persistence protocol for one workflow."""

    mode: ProtocolMode
    schema_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.mode, ProtocolMode):
            raise WorkflowStateValidationError("protocol mode is invalid")
        _require_non_empty(self.schema_version, "protocol schema_version")
        expected = {
            ProtocolMode.LEGACY_STATE_V3: "3",
            ProtocolMode.STRUCTURED_V1: "1",
        }[self.mode]
        if self.schema_version != expected:
            raise WorkflowStateValidationError(
                f"protocol mode {self.mode.value} requires schema_version {expected}"
            )

    def to_dict(self) -> dict[str, str]:
        return {"mode": self.mode.value, "schema_version": self.schema_version}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ProtocolBinding:
        _require_exact_keys(raw, {"mode", "schema_version"}, "protocol binding")
        return cls(
            mode=_enum_value(ProtocolMode, raw["mode"], "protocol mode"),
            schema_version=_string(raw["schema_version"], "protocol schema_version"),
        )


@dataclass(frozen=True)
class InvocationFailureRecord:
    invocation_id: str
    idempotency_key: str
    role: str
    failure_kind: AgentFailureKind
    provider_text: str
    received_at: str
    step: WorkflowStep
    slice_id: int
    work_unit_id: int
    diagnostic_exit_code: int
    parse_path: str | None = None
    source_timezone: str | None = None
    reset_at_utc: str | None = None
    resume_at_utc: str | None = None
    safety_margin_seconds: int = 0
    auto_resume_count: int = 0
    automatic_resume: bool = False
    diff_fingerprint: str | None = None

    def __post_init__(self) -> None:
        for value, label in (
            (self.invocation_id, "invocation failure invocation_id"),
            (self.idempotency_key, "invocation failure idempotency_key"),
            (self.provider_text, "invocation failure provider_text"),
        ):
            _require_non_empty(value, label)
        if self.role not in {"codex", "claude", "antigravity"}:
            raise WorkflowStateValidationError(
                "invocation failure role must be codex, claude, or antigravity"
            )
        _require_timestamp(self.received_at, "invocation failure received_at")
        _require_positive_int(self.slice_id, "invocation failure slice_id")
        _require_positive_int(self.work_unit_id, "invocation failure work_unit_id")
        if self.failure_kind is AgentFailureKind.QUOTA:
            if self.diagnostic_exit_code != 2:
                raise WorkflowStateValidationError("quota failure requires exit code 2")
        elif self.diagnostic_exit_code != 3:
            raise WorkflowStateValidationError("instance failure requires exit code 3")
        for value, label in (
            (self.safety_margin_seconds, "invocation failure safety margin"),
            (self.auto_resume_count, "invocation failure auto-resume count"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise WorkflowStateValidationError(f"{label} must be a non-negative integer")
        if not isinstance(self.automatic_resume, bool):
            raise WorkflowStateValidationError(
                "invocation failure automatic_resume must be a boolean"
            )
        timestamp_fields = (
            (self.reset_at_utc, "invocation failure reset_at_utc"),
            (self.resume_at_utc, "invocation failure resume_at_utc"),
        )
        for value, label in timestamp_fields:
            if value is not None:
                _require_timestamp(value, label)
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
                    raise WorkflowStateValidationError(f"{label} must be normalized to UTC")
        has_reset = self.reset_at_utc is not None
        if has_reset != (self.resume_at_utc is not None):
            raise WorkflowStateValidationError(
                "invocation failure reset and resume timestamps must be present together"
            )
        if self.automatic_resume and (
            self.failure_kind is not AgentFailureKind.QUOTA or not has_reset
        ):
            raise WorkflowStateValidationError(
                "automatic resume requires a quota failure with a reset timestamp"
            )
        if self.failure_kind is not AgentFailureKind.QUOTA and any(
            value is not None
            for value in (self.parse_path, self.source_timezone, self.reset_at_utc)
        ):
            raise WorkflowStateValidationError(
                "non-quota failure cannot carry quota reset evidence"
            )
        if self.diff_fingerprint is not None and not SHA256_PATTERN.fullmatch(
            self.diff_fingerprint
        ):
            raise WorkflowStateValidationError(
                "invocation failure diff fingerprint must be a lowercase SHA-256 digest"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "invocation_id": self.invocation_id,
            "idempotency_key": self.idempotency_key,
            "role": self.role,
            "failure_kind": self.failure_kind.value,
            "provider_text": self.provider_text,
            "received_at": self.received_at,
            "step": self.step.value,
            "slice_id": self.slice_id,
            "work_unit_id": self.work_unit_id,
            "diagnostic_exit_code": self.diagnostic_exit_code,
            "parse_path": self.parse_path,
            "source_timezone": self.source_timezone,
            "reset_at_utc": self.reset_at_utc,
            "resume_at_utc": self.resume_at_utc,
            "safety_margin_seconds": self.safety_margin_seconds,
            "auto_resume_count": self.auto_resume_count,
            "automatic_resume": self.automatic_resume,
            "diff_fingerprint": self.diff_fingerprint,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> InvocationFailureRecord:
        _require_exact_keys(
            raw,
            {
                "invocation_id", "idempotency_key", "role", "failure_kind",
                "provider_text", "received_at", "step", "slice_id",
                "work_unit_id", "diagnostic_exit_code", "parse_path",
                "source_timezone", "reset_at_utc", "resume_at_utc",
                "safety_margin_seconds", "auto_resume_count", "automatic_resume",
                "diff_fingerprint",
            },
            "invocation failure",
        )
        if not isinstance(raw["automatic_resume"], bool):
            raise WorkflowStateValidationError(
                "invocation failure automatic_resume must be a boolean"
            )
        return cls(
            invocation_id=_string(raw["invocation_id"], "invocation failure invocation_id"),
            idempotency_key=_string(raw["idempotency_key"], "invocation failure idempotency_key"),
            role=_string(raw["role"], "invocation failure role"),
            failure_kind=_enum_value(AgentFailureKind, raw["failure_kind"], "invocation failure kind"),
            provider_text=_string(raw["provider_text"], "invocation failure provider_text"),
            received_at=_string(raw["received_at"], "invocation failure received_at"),
            step=_enum_value(WorkflowStep, raw["step"], "invocation failure step"),
            slice_id=_positive_int(raw["slice_id"], "invocation failure slice_id"),
            work_unit_id=_positive_int(raw["work_unit_id"], "invocation failure work_unit_id"),
            diagnostic_exit_code=_positive_int(raw["diagnostic_exit_code"], "invocation failure diagnostic_exit_code"),
            parse_path=_optional_string(raw["parse_path"], "invocation failure parse_path"),
            source_timezone=_optional_string(raw["source_timezone"], "invocation failure source_timezone"),
            reset_at_utc=_optional_string(raw["reset_at_utc"], "invocation failure reset_at_utc"),
            resume_at_utc=_optional_string(raw["resume_at_utc"], "invocation failure resume_at_utc"),
            safety_margin_seconds=_non_negative_int(raw["safety_margin_seconds"], "invocation failure safety margin"),
            auto_resume_count=_non_negative_int(raw["auto_resume_count"], "invocation failure auto-resume count"),
            automatic_resume=raw["automatic_resume"],
            diff_fingerprint=_optional_string(raw["diff_fingerprint"], "invocation failure diff fingerprint"),
        )


@dataclass(frozen=True)
class GateRecord:
    status: GateStatus = GateStatus.CLEAR
    reason: GateReason = GateReason.NONE
    detail: str | None = None
    fingerprint: str | None = None
    paths: tuple[str, ...] = ()
    resume_step: WorkflowStep | None = None

    def __post_init__(self) -> None:
        if self.status is GateStatus.CLEAR:
            if (
                self.reason is not GateReason.NONE
                or self.detail is not None
                or self.fingerprint is not None
                or self.paths
                or self.resume_step is not None
            ):
                raise WorkflowStateValidationError("a clear gate cannot carry gate evidence")
        elif self.reason is GateReason.NONE:
            raise WorkflowStateValidationError("a non-clear gate requires a reason")
        if self.detail is not None and not self.detail.strip():
            raise WorkflowStateValidationError("gate detail must be non-empty when present")
        if any(not isinstance(path, str) or not path.strip() for path in self.paths):
            raise WorkflowStateValidationError(
                "gate.paths entries must be non-empty strings"
            )
        if len(set(self.paths)) != len(self.paths):
            raise WorkflowStateValidationError("gate.paths entries must be unique")
        if self.paths != tuple(sorted(self.paths)):
            raise WorkflowStateValidationError("gate.paths must be sorted")
        if self.fingerprint is not None and not SHA256_PATTERN.fullmatch(self.fingerprint):
            raise WorkflowStateValidationError(
                "gate fingerprint must be a lowercase SHA-256 digest"
            )
        if (
            self.fingerprint is not None
            and self.reason is GateReason.TEST_CHANGE
            and not self.paths
        ):
            raise WorkflowStateValidationError("test-change gate requires changed test paths")
        if (
            self.fingerprint is not None
            and self.reason in {GateReason.MANUAL_SLICE, GateReason.PLAN_APPROVAL}
            and not self.paths
        ):
            raise WorkflowStateValidationError(
                "manual-slice and plan-approval gates require bound paths"
            )
        if self.reason in {
            GateReason.TEST_CHANGE,
            GateReason.MANUAL_SLICE,
            GateReason.PLAN_APPROVAL,
            GateReason.UNEXPECTED_FILE,
            GateReason.STOP_REQUEST,
        }:
            for raw_path in self.paths:
                path = PurePosixPath(raw_path)
                if (
                    path.is_absolute()
                    or "\\" in raw_path
                    or not path.parts
                    or ".." in path.parts
                ):
                    raise WorkflowStateValidationError(
                        "file-bound gate paths must be repository-relative POSIX paths"
                    )
        if (
            self.fingerprint is not None
            and self.reason is GateReason.ANCHOR_CHANGE
            and self.resume_step is None
        ):
            raise WorkflowStateValidationError("anchor-change gate requires a resume step")

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "reason": self.reason.value,
            "detail": self.detail,
            "fingerprint": self.fingerprint,
            "paths": list(self.paths),
            "resume_step": self.resume_step.value if self.resume_step is not None else None,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> GateRecord:
        legacy_keys = {"status", "reason", "detail"}
        current_keys = {*legacy_keys, "fingerprint", "paths", "resume_step"}
        if set(raw) == legacy_keys:
            fingerprint = None
            paths: tuple[str, ...] = ()
            resume_step = None
        else:
            _require_exact_keys(raw, current_keys, "gate")
            fingerprint = _optional_string(raw["fingerprint"], "gate.fingerprint")
            paths = _string_tuple(raw["paths"], "gate.paths")
            resume_raw = raw["resume_step"]
            resume_step = (
                None
                if resume_raw is None
                else _enum_value(WorkflowStep, resume_raw, "gate.resume_step")
            )
        return cls(
            status=_enum_value(GateStatus, raw["status"], "gate.status"),
            reason=_enum_value(GateReason, raw["reason"], "gate.reason"),
            detail=_optional_string(raw["detail"], "gate.detail"),
            fingerprint=fingerprint,
            paths=paths,
            resume_step=resume_step,
        )


@dataclass(frozen=True)
class GateDecisionRecord:
    approved: bool
    reason: GateReason
    fingerprint: str
    paths: tuple[str, ...]
    decided_by: str
    decided_at: str
    rationale: str
    resume_step: WorkflowStep | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.approved, bool):
            raise WorkflowStateValidationError("gate decision approved must be a boolean")
        if self.reason not in {
            GateReason.TEST_CHANGE,
            GateReason.ANCHOR_CHANGE,
            GateReason.MANUAL_SLICE,
            GateReason.PLAN_APPROVAL,
        }:
            raise WorkflowStateValidationError(
                "a fingerprint-bound decision requires a user-gate reason"
            )
        if not SHA256_PATTERN.fullmatch(self.fingerprint):
            raise WorkflowStateValidationError(
                "gate decision fingerprint must be a lowercase SHA-256 digest"
            )
        if any(not isinstance(path, str) or not path.strip() for path in self.paths):
            raise WorkflowStateValidationError(
                "gate decision paths entries must be non-empty strings"
            )
        if len(set(self.paths)) != len(self.paths):
            raise WorkflowStateValidationError(
                "gate decision paths entries must be unique"
            )
        if self.paths != tuple(sorted(self.paths)):
            raise WorkflowStateValidationError("gate decision paths must be sorted")
        _require_non_empty(self.decided_by, "gate decision decided_by")
        _require_timestamp(self.decided_at, "gate decision decided_at")
        _require_non_empty(self.rationale, "gate decision rationale")
        if self.reason is GateReason.TEST_CHANGE and not self.paths:
            raise WorkflowStateValidationError(
                "test-change decision requires changed test paths"
            )
        if self.reason in {GateReason.MANUAL_SLICE, GateReason.PLAN_APPROVAL} and not self.paths:
            raise WorkflowStateValidationError(
                "manual-slice and plan-approval decisions require bound paths"
            )
        if self.reason in {
            GateReason.TEST_CHANGE,
            GateReason.MANUAL_SLICE,
            GateReason.PLAN_APPROVAL,
            GateReason.UNEXPECTED_FILE,
            GateReason.STOP_REQUEST,
        }:
            for raw_path in self.paths:
                path = PurePosixPath(raw_path)
                if (
                    path.is_absolute()
                    or "\\" in raw_path
                    or not path.parts
                    or ".." in path.parts
                ):
                    raise WorkflowStateValidationError(
                        "file-bound decision paths must be repository-relative POSIX paths"
                    )
        if self.reason is GateReason.ANCHOR_CHANGE and self.resume_step is None:
            raise WorkflowStateValidationError(
                "anchor-change decision requires a resume step"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "approved": self.approved,
            "reason": self.reason.value,
            "fingerprint": self.fingerprint,
            "paths": list(self.paths),
            "decided_by": self.decided_by,
            "decided_at": self.decided_at,
            "rationale": self.rationale,
            "resume_step": self.resume_step.value if self.resume_step is not None else None,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> GateDecisionRecord:
        _require_exact_keys(
            raw,
            {
                "approved",
                "reason",
                "fingerprint",
                "paths",
                "decided_by",
                "decided_at",
                "rationale",
                "resume_step",
            },
            "gate decision",
        )
        if not isinstance(raw["approved"], bool):
            raise WorkflowStateValidationError("gate decision approved must be a boolean")
        resume_raw = raw["resume_step"]
        return cls(
            approved=raw["approved"],
            reason=_enum_value(GateReason, raw["reason"], "gate decision reason"),
            fingerprint=_string(raw["fingerprint"], "gate decision fingerprint"),
            paths=_string_tuple(raw["paths"], "gate decision paths"),
            decided_by=_string(raw["decided_by"], "gate decision decided_by"),
            decided_at=_string(raw["decided_at"], "gate decision decided_at"),
            rationale=_string(raw["rationale"], "gate decision rationale"),
            resume_step=(
                None
                if resume_raw is None
                else _enum_value(WorkflowStep, resume_raw, "gate decision resume_step")
            ),
        )


@dataclass(frozen=True)
class SliceRecord:
    slice_id: int
    status: SliceStatus
    start_commit: str | None = None
    scope_paths: tuple[str, ...] = ()
    scope_change_groups: tuple[tuple[str, ...], ...] = ()
    start_fingerprint: str | None = None
    commit_ref: str | None = None

    def __post_init__(self) -> None:
        _require_positive_int(self.slice_id, "slice_id")
        if self.start_commit is not None:
            _require_non_empty(self.start_commit, "slice start_commit")
        if self.status is not SliceStatus.PENDING and self.start_commit is None:
            raise WorkflowStateValidationError("a started slice requires start_commit")
        _require_canonical_scope(self.scope_paths)
        if self.scope_paths:
            _require_scope_change_groups(
                self.scope_change_groups, self.scope_paths
            )
        elif self.scope_change_groups:
            raise WorkflowStateValidationError(
                "slice scope change groups require scope paths"
            )
        if (self.start_fingerprint is None) != (not self.scope_paths):
            raise WorkflowStateValidationError(
                "slice scope_paths and start_fingerprint must be persisted together"
            )
        if self.start_fingerprint is not None and not SHA256_PATTERN.fullmatch(
            self.start_fingerprint
        ):
            raise WorkflowStateValidationError(
                "slice start_fingerprint must be a lowercase SHA-256 digest"
            )
        if self.commit_ref is not None:
            _require_non_empty(self.commit_ref, "slice commit_ref")
        if self.status is SliceStatus.COMPLETED and self.commit_ref is None:
            raise WorkflowStateValidationError("a completed slice requires commit_ref")

    def to_dict(self) -> dict[str, object]:
        return {
            "slice_id": self.slice_id,
            "status": self.status.value,
            "start_commit": self.start_commit,
            "scope_paths": list(self.scope_paths),
            "scope_change_groups": [list(group) for group in self.scope_change_groups],
            "start_fingerprint": self.start_fingerprint,
            "commit_ref": self.commit_ref,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> SliceRecord:
        legacy_keys = {"slice_id", "status", "start_commit", "commit_ref"}
        boundary_keys = {*legacy_keys, "scope_paths", "start_fingerprint"}
        current_keys = {*boundary_keys, "scope_change_groups"}
        if set(raw) == legacy_keys:
            scope_paths: tuple[str, ...] = ()
            scope_change_groups: tuple[tuple[str, ...], ...] = ()
            start_fingerprint = None
        elif set(raw) == boundary_keys:
            scope_paths = _string_tuple(raw["scope_paths"], "slice.scope_paths")
            scope_change_groups = tuple((path,) for path in scope_paths)
            start_fingerprint = _optional_string(
                raw["start_fingerprint"], "slice.start_fingerprint"
            )
        else:
            _require_exact_keys(raw, current_keys, "slice")
            scope_paths = _string_tuple(raw["scope_paths"], "slice.scope_paths")
            raw_groups = _list(
                raw["scope_change_groups"], "slice.scope_change_groups"
            )
            scope_change_groups = tuple(
                _string_tuple(group, f"slice.scope_change_groups[{index}]")
                for index, group in enumerate(raw_groups)
            )
            start_fingerprint = _optional_string(
                raw["start_fingerprint"], "slice.start_fingerprint"
            )
        return cls(
            slice_id=_positive_int(raw["slice_id"], "slice.slice_id"),
            status=_enum_value(SliceStatus, raw["status"], "slice.status"),
            start_commit=_optional_string(raw["start_commit"], "slice.start_commit"),
            scope_paths=scope_paths,
            scope_change_groups=scope_change_groups,
            start_fingerprint=start_fingerprint,
            commit_ref=_optional_string(raw["commit_ref"], "slice.commit_ref"),
        )


@dataclass(frozen=True)
class WorkUnitRecord:
    work_unit_id: int
    slice_id: int
    kind: WorkUnitKind
    status: WorkUnitStatus
    current_step: WorkflowStep
    round_number: int = 1
    codex_return_count: int = 0
    max_codex_returns: int = DEFAULT_MAX_CODEX_RETURNS
    gate: GateRecord = GateRecord()
    reviewer: Reviewer | None = None
    open_findings: tuple[str, ...] = ()
    completed_side_effects: tuple[str, ...] = ()
    gate_decisions: tuple[GateDecisionRecord, ...] = ()
    active_test_fingerprint: str | None = None
    active_test_paths: tuple[str, ...] = ()
    invocation_failures: tuple[InvocationFailureRecord, ...] = ()

    def __post_init__(self) -> None:
        _require_positive_int(self.work_unit_id, "work_unit_id")
        _require_positive_int(self.slice_id, "work unit slice_id")
        _require_positive_int(self.round_number, "round_number")
        _require_positive_int(self.max_codex_returns, "max_codex_returns")
        if isinstance(self.codex_return_count, bool) or not isinstance(self.codex_return_count, int):
            raise WorkflowStateValidationError("codex_return_count must be an integer")
        if not 0 <= self.codex_return_count <= self.max_codex_returns:
            raise WorkflowStateValidationError("codex_return_count is outside its configured limit")
        if self.round_number > self.max_codex_returns:
            raise WorkflowStateValidationError("round_number exceeds max_codex_returns")
        _require_unique_non_empty(self.open_findings, "open_findings")
        _require_unique_non_empty(self.completed_side_effects, "completed_side_effects")
        expected_gate_status = {
            WorkUnitStatus.IN_PROGRESS: GateStatus.CLEAR,
            WorkUnitStatus.PENDING: GateStatus.CLEAR,
            WorkUnitStatus.COMPLETED: GateStatus.CLEAR,
            WorkUnitStatus.AWAITING_USER_DECISION: GateStatus.AWAITING_USER_DECISION,
            WorkUnitStatus.WAITING_FOR_QUOTA: GateStatus.WAITING_FOR_QUOTA,
            WorkUnitStatus.AWAITING_RESUME: GateStatus.AWAITING_RESUME,
        }[self.status]
        if self.gate.status is not expected_gate_status:
            raise WorkflowStateValidationError(
                "work-unit status and gate status must change together"
            )
        invocation_ids = tuple(item.invocation_id for item in self.invocation_failures)
        if len(set(invocation_ids)) != len(invocation_ids):
            raise WorkflowStateValidationError(
                "invocation failure ids must be unique within a work unit"
            )
        if any(
            item.work_unit_id != self.work_unit_id or item.slice_id != self.slice_id
            for item in self.invocation_failures
        ):
            raise WorkflowStateValidationError(
                "invocation failures must belong to their containing work unit and slice"
            )
        if self.status in {WorkUnitStatus.WAITING_FOR_QUOTA, WorkUnitStatus.AWAITING_RESUME}:
            if not self.invocation_failures:
                raise WorkflowStateValidationError(
                    "an invocation halt requires persisted failure evidence"
                )
            latest = self.invocation_failures[-1]
            if latest.step is not self.current_step:
                raise WorkflowStateValidationError(
                    "invocation halt must preserve the failed workflow step"
                )
            if self.gate.reason is GateReason.QUOTA:
                if latest.failure_kind is not AgentFailureKind.QUOTA:
                    raise WorkflowStateValidationError(
                        "quota gate requires a quota invocation failure"
                    )
            elif self.gate.reason is GateReason.INSTANCE_FAILURE:
                if latest.failure_kind is AgentFailureKind.QUOTA:
                    raise WorkflowStateValidationError(
                        "instance-failure gate cannot carry a quota failure"
                    )
            else:
                raise WorkflowStateValidationError(
                    "invocation halt requires quota or instance_failure reason"
                )
        if self.gate.reason is GateReason.ITERATION_LIMIT:
            if self.codex_return_count != self.max_codex_returns:
                raise WorkflowStateValidationError(
                    "iteration-limit gate requires the configured Codex return limit"
                )
            if self.reviewer is None:
                raise WorkflowStateValidationError("iteration-limit gate requires a reviewer")
        if (self.active_test_fingerprint is None) != (not self.active_test_paths):
            raise WorkflowStateValidationError(
                "active test fingerprint and paths must be persisted together"
            )
        if self.active_test_fingerprint is not None:
            if not SHA256_PATTERN.fullmatch(self.active_test_fingerprint):
                raise WorkflowStateValidationError(
                    "active test fingerprint must be a lowercase SHA-256 digest"
                )
            if self.active_test_paths != tuple(sorted(set(self.active_test_paths))):
                raise WorkflowStateValidationError(
                    "active test paths must be sorted and unique"
                )
            if not self.has_gate_approval(
                GateReason.TEST_CHANGE,
                self.active_test_fingerprint,
                self.active_test_paths,
            ):
                raise WorkflowStateValidationError(
                    "active test evidence requires an exact approved test gate decision"
                )

    def has_completed_side_effect(self, key: str) -> bool:
        _require_non_empty(key, "side-effect key")
        return key in self.completed_side_effects

    def has_gate_approval(
        self,
        reason: GateReason,
        fingerprint: str,
        paths: tuple[str, ...],
    ) -> bool:
        expected_paths = tuple(sorted(set(paths)))
        return any(
            decision.approved
            and decision.reason is reason
            and decision.fingerprint == fingerprint
            and decision.paths == expected_paths
            for decision in self.gate_decisions
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "work_unit_id": self.work_unit_id,
            "slice_id": self.slice_id,
            "kind": self.kind.value,
            "status": self.status.value,
            "current_step": self.current_step.value,
            "round_number": self.round_number,
            "codex_return_count": self.codex_return_count,
            "max_codex_returns": self.max_codex_returns,
            "gate": self.gate.to_dict(),
            "reviewer": self.reviewer.value if self.reviewer is not None else None,
            "open_findings": list(self.open_findings),
            "completed_side_effects": list(self.completed_side_effects),
            "gate_decisions": [item.to_dict() for item in self.gate_decisions],
            "active_test_fingerprint": self.active_test_fingerprint,
            "active_test_paths": list(self.active_test_paths),
            "invocation_failures": [item.to_dict() for item in self.invocation_failures],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> WorkUnitRecord:
        legacy_keys = {
                "work_unit_id",
                "slice_id",
                "kind",
                "status",
                "current_step",
                "round_number",
                "codex_return_count",
                "max_codex_returns",
                "gate",
                "reviewer",
                "open_findings",
                "completed_side_effects",
            }
        decision_keys = {*legacy_keys, "gate_decisions"}
        current_keys = {
            *decision_keys,
            "active_test_fingerprint",
            "active_test_paths",
        }
        quota_keys = {*current_keys, "invocation_failures"}
        invocation_failures: tuple[InvocationFailureRecord, ...] = ()
        shape_keys = set(raw) - {"invocation_failures"}
        if shape_keys == legacy_keys:
            gate_decisions: tuple[GateDecisionRecord, ...] = ()
            active_test_fingerprint = None
            active_test_paths: tuple[str, ...] = ()
        elif shape_keys == decision_keys:
            raw_decisions = raw["gate_decisions"]
            if not isinstance(raw_decisions, list):
                raise WorkflowStateValidationError("work_unit.gate_decisions must be a list")
            gate_decisions = tuple(
                GateDecisionRecord.from_dict(
                    _mapping(item, f"work_unit.gate_decisions[{index}]")
                )
                for index, item in enumerate(raw_decisions)
            )
            active_test_fingerprint = None
            active_test_paths = ()
        elif shape_keys == current_keys:
            raw_decisions = raw["gate_decisions"]
            if not isinstance(raw_decisions, list):
                raise WorkflowStateValidationError("work_unit.gate_decisions must be a list")
            gate_decisions = tuple(
                GateDecisionRecord.from_dict(
                    _mapping(item, f"work_unit.gate_decisions[{index}]")
                )
                for index, item in enumerate(raw_decisions)
            )
            active_test_fingerprint = _optional_string(
                raw["active_test_fingerprint"],
                "work_unit.active_test_fingerprint",
            )
            active_test_paths = _string_tuple(
                raw["active_test_paths"], "work_unit.active_test_paths"
            )
        else:
            _require_exact_keys(raw, quota_keys, "work unit")
        if "invocation_failures" in raw:
            raw_failures = _list(
                raw["invocation_failures"], "work_unit.invocation_failures"
            )
            invocation_failures = tuple(
                InvocationFailureRecord.from_dict(
                    _mapping(item, f"work_unit.invocation_failures[{index}]")
                )
                for index, item in enumerate(raw_failures)
            )
        reviewer_raw = raw["reviewer"]
        return cls(
            work_unit_id=_positive_int(raw["work_unit_id"], "work_unit.work_unit_id"),
            slice_id=_positive_int(raw["slice_id"], "work_unit.slice_id"),
            kind=_enum_value(WorkUnitKind, raw["kind"], "work_unit.kind"),
            status=_enum_value(WorkUnitStatus, raw["status"], "work_unit.status"),
            current_step=_enum_value(WorkflowStep, raw["current_step"], "work_unit.current_step"),
            round_number=_positive_int(raw["round_number"], "work_unit.round_number"),
            codex_return_count=_non_negative_int(
                raw["codex_return_count"], "work_unit.codex_return_count"
            ),
            max_codex_returns=_positive_int(
                raw["max_codex_returns"], "work_unit.max_codex_returns"
            ),
            gate=GateRecord.from_dict(_mapping(raw["gate"], "work_unit.gate")),
            reviewer=(
                None
                if reviewer_raw is None
                else _enum_value(Reviewer, reviewer_raw, "work_unit.reviewer")
            ),
            open_findings=_string_tuple(raw["open_findings"], "work_unit.open_findings"),
            completed_side_effects=_string_tuple(
                raw["completed_side_effects"], "work_unit.completed_side_effects"
            ),
            gate_decisions=gate_decisions,
            active_test_fingerprint=active_test_fingerprint,
            active_test_paths=active_test_paths,
            invocation_failures=invocation_failures,
        )


@dataclass(frozen=True)
class ResumeCursor:
    work_unit_id: int
    slice_id: int
    step: WorkflowStep
    round_number: int
    completed_side_effects: tuple[str, ...]

    def should_execute(self, side_effect_key: str) -> bool:
        _require_non_empty(side_effect_key, "side-effect key")
        return side_effect_key not in self.completed_side_effects


@dataclass(frozen=True)
class WorkflowState:
    version: int
    run_id: str
    task_file: str
    branch: str
    branch_base: str
    created_at: str
    updated_at: str
    current_slice_id: int
    current_work_unit_id: int
    current_step: WorkflowStep
    slices: tuple[SliceRecord, ...]
    work_units: tuple[WorkUnitRecord, ...]
    planned_slices: tuple[PlannedSlice, ...] = ()
    runtime_history: Mapping[str, Any] | None = None
    task_digest: str | None = None
    execution_mode: str = "IMPLEMENT"
    task_scope_patterns: tuple[str, ...] = ()
    work_plan_path: str | None = None
    audit_report_path: str | None = None
    target_branch: str | None = None
    protocol_binding: ProtocolBinding | None = None

    def __post_init__(self) -> None:
        if self.version != STATE_VERSION:
            raise WorkflowStateValidationError(
                f"WorkflowState requires version {STATE_VERSION}, got {self.version!r}"
            )
        for value, label in (
            (self.run_id, "run_id"),
            (self.task_file, "task_file"),
            (self.branch, "branch"),
            (self.branch_base, "branch_base"),
            (self.created_at, "created_at"),
            (self.updated_at, "updated_at"),
        ):
            _require_non_empty(value, label)
        _require_positive_int(self.current_slice_id, "current_slice_id")
        _require_positive_int(self.current_work_unit_id, "current_work_unit_id")
        if not self.slices or not self.work_units:
            raise WorkflowStateValidationError("workflow state requires slices and work units")
        slice_ids = tuple(item.slice_id for item in self.slices)
        work_unit_ids = tuple(item.work_unit_id for item in self.work_units)
        _require_contiguous_ids(slice_ids, "slice")
        _require_contiguous_ids(work_unit_ids, "work-unit")
        if self.current_slice_id not in slice_ids:
            raise WorkflowStateValidationError("current_slice_id does not reference a slice")
        if self.current_work_unit_id not in work_unit_ids:
            raise WorkflowStateValidationError(
                "current_work_unit_id does not reference a work unit"
            )
        for unit in self.work_units:
            if unit.slice_id not in slice_ids:
                raise WorkflowStateValidationError(
                    f"work unit {unit.work_unit_id} references unknown slice {unit.slice_id}"
                )
        current = self.current_work_unit
        if current.slice_id != self.current_slice_id:
            raise WorkflowStateValidationError(
                "current work unit does not belong to current slice"
            )
        if current.current_step is not self.current_step:
            raise WorkflowStateValidationError(
                "top-level current_step must match the current work unit"
            )
        if self.planned_slices:
            plan_ids = tuple(item.slice_id for item in self.planned_slices)
            if plan_ids != tuple(range(1, len(plan_ids) + 1)):
                raise WorkflowStateValidationError(
                    "planned slice ids must be contiguous and 1-based"
                )
            if len(self.planned_slices) > len(self.slices):
                raise WorkflowStateValidationError(
                    "planned slice count cannot exceed persisted slices"
                )
            extra_slice_ids = {
                item.slice_id for item in self.slices[len(self.planned_slices):]
            }
            allowed_extra_slice_work_units = {
                WorkUnitKind.CORRECTION,
                WorkUnitKind.FINAL_REVIEW,
            }
            if any(
                unit.slice_id in extra_slice_ids
                and unit.kind not in allowed_extra_slice_work_units
                for unit in self.work_units
            ):
                raise WorkflowStateValidationError(
                    "only correction and subsequent final-review work units may "
                    "extend the persisted Slice plan"
                )
        if self.runtime_history is not None and not isinstance(
            self.runtime_history, Mapping
        ):
            raise WorkflowStateValidationError("runtime_history must be an object")
        if self.execution_mode not in {"IMPLEMENT", "PLAN_ONLY"}:
            raise WorkflowStateValidationError(
                "execution_mode must be IMPLEMENT or PLAN_ONLY"
            )
        if self.task_digest is not None:
            if not SHA256_PATTERN.fullmatch(self.task_digest):
                raise WorkflowStateValidationError(
                    "task_digest must be a lowercase SHA-256 digest"
                )
            if not self.task_scope_patterns:
                raise WorkflowStateValidationError(
                    "a bound task contract requires task_scope_patterns"
                )
            if self.target_branch != self.branch:
                raise WorkflowStateValidationError(
                    "target_branch must match the persisted workflow branch"
                )
            if self.execution_mode == "PLAN_ONLY" and self.work_plan_path is None:
                raise WorkflowStateValidationError(
                    "PLAN_ONLY state requires work_plan_path"
                )
        if self.audit_report_path is not None:
            path = PurePosixPath(self.audit_report_path)
            if (
                path.is_absolute()
                or ".." in path.parts
                or path.parts[:2] != ("docs", "internal")
                or len(path.parts) != 3
                or path.suffix.lower() != ".md"
                or path.as_posix() != self.audit_report_path
            ):
                raise WorkflowStateValidationError(
                    "audit_report_path must be a canonical Markdown path directly below docs/internal"
                )

    @property
    def current_work_unit(self) -> WorkUnitRecord:
        return next(
            unit for unit in self.work_units if unit.work_unit_id == self.current_work_unit_id
        )

    @property
    def current_slice(self) -> SliceRecord:
        return next(item for item in self.slices if item.slice_id == self.current_slice_id)

    @property
    def effective_protocol_mode(self) -> ProtocolMode:
        """Return the resume mode; missing bindings identify historical v3 state."""
        if self.protocol_binding is None:
            return ProtocolMode.LEGACY_STATE_V3
        return self.protocol_binding.mode

    @property
    def protocol_mode(self) -> ProtocolMode:
        """Compatibility-friendly name for the effective persisted mode."""
        return self.effective_protocol_mode

    def resume_cursor(self) -> ResumeCursor:
        current = self.current_work_unit
        return ResumeCursor(
            work_unit_id=current.work_unit_id,
            slice_id=current.slice_id,
            step=current.current_step,
            round_number=current.round_number,
            completed_side_effects=current.completed_side_effects,
        )

    def with_current_step(
        self,
        step: WorkflowStep,
        *,
        updated_at: str | None = None,
    ) -> WorkflowState:
        current = self.current_work_unit
        if current.status is not WorkUnitStatus.IN_PROGRESS:
            raise WorkflowStateValidationError("only an in-progress work unit can advance")
        return self._replace_current_unit(
            replace(current, current_step=step),
            updated_at=updated_at,
        )

    def bind_slice_plan(
        self,
        planned_slices: tuple[PlannedSlice, ...],
        *,
        first_start_commit: str,
        updated_at: str | None = None,
    ) -> WorkflowState:
        """Persist Codex's ordered allowlists before any implementation step."""
        if self.current_work_unit.kind is not WorkUnitKind.PLAN:
            raise WorkflowStateValidationError("slice plan can only be bound during planning")
        if not planned_slices:
            raise WorkflowStateValidationError("slice plan must not be empty")
        _require_non_empty(first_start_commit, "first_start_commit")
        expected_ids = tuple(range(1, len(planned_slices) + 1))
        if tuple(item.slice_id for item in planned_slices) != expected_ids:
            raise WorkflowStateValidationError(
                "planned slice ids must be contiguous and 1-based"
            )
        if self.planned_slices and self.planned_slices != planned_slices:
            if any(item.status is SliceStatus.COMPLETED for item in self.slices):
                raise WorkflowStateValidationError(
                    "cannot revise the slice plan after implementation commits"
                )
        slices = tuple(
            SliceRecord(
                slice_id=item.slice_id,
                status=(SliceStatus.IN_PROGRESS if item.slice_id == 1 else SliceStatus.PENDING),
                start_commit=(first_start_commit if item.slice_id == 1 else None),
            )
            for item in planned_slices
        )
        return replace(
            self,
            slices=slices,
            planned_slices=planned_slices,
            updated_at=updated_at or _now_iso(),
        )

    def complete_current_work_unit(self, *, updated_at: str | None = None) -> WorkflowState:
        current = self.current_work_unit
        if current.status is not WorkUnitStatus.IN_PROGRESS:
            raise WorkflowStateValidationError("only an in-progress work unit can complete")
        return self._replace_current_unit(
            replace(
                current,
                status=WorkUnitStatus.COMPLETED,
                current_step=WorkflowStep.COMPLETED,
            ),
            updated_at=updated_at,
        )

    def start_work_unit(
        self,
        *,
        slice_id: int,
        kind: WorkUnitKind,
        step: WorkflowStep,
        slice_start_commit: str | None = None,
        updated_at: str | None = None,
    ) -> WorkflowState:
        _require_positive_int(slice_id, "slice_id")
        if self.current_work_unit.status is not WorkUnitStatus.COMPLETED:
            raise WorkflowStateValidationError(
                "the current work unit must be completed before starting another"
            )
        try:
            target_slice = next(item for item in self.slices if item.slice_id == slice_id)
        except StopIteration as exc:
            raise WorkflowStateValidationError(f"unknown slice_id {slice_id}") from exc
        if target_slice.status is SliceStatus.COMPLETED:
            raise WorkflowStateValidationError("cannot start a work unit for a completed slice")
        if target_slice.status is SliceStatus.PENDING:
            if slice_start_commit is None:
                raise WorkflowStateValidationError("a new slice requires slice_start_commit")
            _require_non_empty(slice_start_commit, "slice_start_commit")
            target_slice = replace(
                target_slice,
                status=SliceStatus.IN_PROGRESS,
                start_commit=slice_start_commit,
            )
        elif slice_start_commit is not None and slice_start_commit != target_slice.start_commit:
            raise WorkflowStateValidationError("cannot change a persisted slice start commit")
        new_unit = WorkUnitRecord(
            work_unit_id=len(self.work_units) + 1,
            slice_id=slice_id,
            kind=kind,
            status=WorkUnitStatus.IN_PROGRESS,
            current_step=step,
        )
        slices = tuple(
            target_slice if item.slice_id == slice_id else item for item in self.slices
        )
        return replace(
            self,
            current_slice_id=slice_id,
            current_work_unit_id=new_unit.work_unit_id,
            current_step=step,
            slices=slices,
            work_units=(*self.work_units, new_unit),
            updated_at=updated_at or _now_iso(),
        )

    def start_final_review_work_unit(
        self, *, updated_at: str | None = None
    ) -> WorkflowState:
        """Start branch-wide final review without reopening a committed slice."""
        if self.current_work_unit.status is not WorkUnitStatus.COMPLETED:
            raise WorkflowStateValidationError(
                "the current work unit must be completed before final review"
            )
        if any(item.status is not SliceStatus.COMPLETED for item in self.slices):
            raise WorkflowStateValidationError(
                "final review requires every current slice to be committed"
            )
        new_unit = WorkUnitRecord(
            work_unit_id=len(self.work_units) + 1,
            slice_id=self.current_slice_id,
            kind=WorkUnitKind.FINAL_REVIEW,
            status=WorkUnitStatus.IN_PROGRESS,
            current_step=WorkflowStep.CODEX_FINAL_REVIEW,
        )
        return replace(
            self,
            current_work_unit_id=new_unit.work_unit_id,
            current_step=new_unit.current_step,
            work_units=(*self.work_units, new_unit),
            updated_at=updated_at or _now_iso(),
        )

    def start_correction_work_unit(
        self,
        *,
        start_commit: str,
        scope_paths: tuple[str, ...],
        scope_change_groups: tuple[tuple[str, ...], ...] | None = None,
        start_fingerprint: str,
        updated_at: str | None = None,
    ) -> WorkflowState:
        """Append one regular, commit-backed correction after a failed final review."""
        _require_non_empty(start_commit, "correction start_commit")
        normalized_scope = _normalize_scope_paths(scope_paths)
        normalized_groups = (
            tuple((path,) for path in normalized_scope)
            if scope_change_groups is None
            else _normalize_scope_change_groups(scope_change_groups, normalized_scope)
        )
        if not SHA256_PATTERN.fullmatch(start_fingerprint):
            raise WorkflowStateValidationError(
                "correction start_fingerprint must be a lowercase SHA-256 digest"
            )
        if (
            self.current_work_unit.kind is not WorkUnitKind.FINAL_REVIEW
            or self.current_work_unit.status is not WorkUnitStatus.COMPLETED
        ):
            raise WorkflowStateValidationError(
                "a correction work unit requires a completed final review attempt"
            )
        correction_slice = SliceRecord(
            slice_id=len(self.slices) + 1,
            status=SliceStatus.IN_PROGRESS,
            start_commit=start_commit,
            scope_paths=normalized_scope,
            scope_change_groups=normalized_groups,
            start_fingerprint=start_fingerprint,
        )
        correction_unit = WorkUnitRecord(
            work_unit_id=len(self.work_units) + 1,
            slice_id=correction_slice.slice_id,
            kind=WorkUnitKind.CORRECTION,
            status=WorkUnitStatus.IN_PROGRESS,
            current_step=WorkflowStep.CODEX_FINAL_CORRECTION,
        )
        return replace(
            self,
            current_slice_id=correction_slice.slice_id,
            current_work_unit_id=correction_unit.work_unit_id,
            current_step=correction_unit.current_step,
            slices=(*self.slices, correction_slice),
            work_units=(*self.work_units, correction_unit),
            updated_at=updated_at or _now_iso(),
        )

    def complete_current_slice(
        self,
        *,
        commit_ref: str,
        updated_at: str | None = None,
    ) -> WorkflowState:
        _require_non_empty(commit_ref, "commit_ref")
        current = self.current_work_unit
        if current.status is not WorkUnitStatus.IN_PROGRESS:
            raise WorkflowStateValidationError("only an in-progress work unit can complete a slice")
        if not self.current_slice.scope_paths:
            raise WorkflowStateValidationError(
                "cannot complete a slice without a persisted Git boundary"
            )
        completed_unit = replace(
            current,
            status=WorkUnitStatus.COMPLETED,
            current_step=WorkflowStep.COMPLETED,
        )
        slices = tuple(
            replace(item, status=SliceStatus.COMPLETED, commit_ref=commit_ref)
            if item.slice_id == self.current_slice_id
            else item
            for item in self.slices
        )
        return self._replace_current_unit(
            completed_unit,
            slices=slices,
            updated_at=updated_at,
        )

    def bind_current_slice_git_boundary(
        self,
        *,
        start_commit: str,
        scope_paths: tuple[str, ...],
        scope_change_groups: tuple[tuple[str, ...], ...] | None = None,
        start_fingerprint: str,
        updated_at: str | None = None,
    ) -> WorkflowState:
        """Persist the immutable Git boundary before the first slice edit."""
        _require_non_empty(start_commit, "start_commit")
        normalized_scope = _normalize_scope_paths(scope_paths)
        normalized_groups = (
            tuple((path,) for path in normalized_scope)
            if scope_change_groups is None
            else _normalize_scope_change_groups(scope_change_groups, normalized_scope)
        )
        if not SHA256_PATTERN.fullmatch(start_fingerprint):
            raise WorkflowStateValidationError(
                "start_fingerprint must be a lowercase SHA-256 digest"
            )
        current_slice = self.current_slice
        if current_slice.status is SliceStatus.COMPLETED:
            raise WorkflowStateValidationError("cannot bind a completed slice")
        if current_slice.start_commit != start_commit:
            raise WorkflowStateValidationError(
                "Git boundary start_commit must match the persisted slice start commit"
            )
        if current_slice.scope_paths:
            if (
                current_slice.scope_paths == normalized_scope
                and current_slice.scope_change_groups == normalized_groups
                and current_slice.start_fingerprint == start_fingerprint
            ):
                return self
            raise WorkflowStateValidationError("cannot change a persisted slice Git boundary")
        bound = replace(
            current_slice,
            scope_paths=normalized_scope,
            scope_change_groups=normalized_groups,
            start_fingerprint=start_fingerprint,
        )
        slices = tuple(
            bound if item.slice_id == current_slice.slice_id else item for item in self.slices
        )
        return replace(
            self,
            slices=slices,
            updated_at=updated_at or _now_iso(),
        )

    def extend_current_slice_scope(
        self,
        additions: tuple[str, ...],
        *,
        updated_at: str | None = None,
    ) -> WorkflowState:
        """Extend an in-progress Slice with a minimal approved remediation allowlist."""
        current = self.current_work_unit
        current_slice = self.current_slice
        if (
            current.kind is not WorkUnitKind.SLICE
            or current.status is not WorkUnitStatus.IN_PROGRESS
            or current_slice.status is not SliceStatus.IN_PROGRESS
        ):
            raise WorkflowStateValidationError(
                "only an in-progress regular Slice can extend its remediation scope"
            )
        if not current_slice.scope_paths or current_slice.start_fingerprint is None:
            raise WorkflowStateValidationError(
                "remediation scope extension requires a persisted Git boundary"
            )
        normalized_additions = _normalize_scope_paths(additions)
        if set(normalized_additions).intersection(current_slice.scope_paths):
            raise WorkflowStateValidationError(
                "remediation additions must not repeat current Slice paths"
            )
        expanded_scope = tuple(
            sorted({*current_slice.scope_paths, *normalized_additions})
        )
        expanded_groups = tuple(
            sorted(
                {
                    *current_slice.scope_change_groups,
                    *((path,) for path in normalized_additions),
                }
            )
        )
        expanded_slice = replace(
            current_slice,
            scope_paths=expanded_scope,
            scope_change_groups=expanded_groups,
        )
        slices = tuple(
            expanded_slice if item.slice_id == current_slice.slice_id else item
            for item in self.slices
        )
        return replace(
            self,
            slices=slices,
            updated_at=updated_at or _now_iso(),
        )

    def mark_side_effect_completed(self, key: str, *, updated_at: str | None = None) -> WorkflowState:
        _require_non_empty(key, "side-effect key")
        current = self.current_work_unit
        if current.has_completed_side_effect(key):
            return self
        updated_unit = replace(
            current,
            completed_side_effects=(*current.completed_side_effects, key),
        )
        return self._replace_current_unit(updated_unit, updated_at=updated_at)

    def record_active_test_approval(
        self,
        fingerprint: str | None,
        paths: tuple[str, ...] = (),
        *,
        updated_at: str | None = None,
    ) -> WorkflowState:
        """Bind audit projection to the exact test approval used by the active review."""
        normalized_paths = tuple(sorted(set(paths)))
        if fingerprint is None:
            normalized_paths = ()
        current = self.current_work_unit
        if (
            current.active_test_fingerprint == fingerprint
            and current.active_test_paths == normalized_paths
        ):
            return self
        updated_unit = replace(
            current,
            active_test_fingerprint=fingerprint,
            active_test_paths=normalized_paths,
        )
        return self._replace_current_unit(updated_unit, updated_at=updated_at)

    def inherit_prior_test_approval(
        self,
        fingerprint: str,
        paths: tuple[str, ...],
        *,
        updated_at: str | None = None,
    ) -> WorkflowState:
        """Reuse an exact earlier test decision without weakening changed-test gates."""
        normalized_paths = tuple(sorted(set(paths)))
        prior_decision = next(
            (
                decision
                for unit in reversed(self.work_units)
                if unit.work_unit_id != self.current_work_unit_id
                for decision in reversed(unit.gate_decisions)
                if decision.approved
                and decision.reason is GateReason.TEST_CHANGE
                and decision.fingerprint == fingerprint
                and decision.paths == normalized_paths
            ),
            None,
        )
        if prior_decision is None:
            return self
        current = self.current_work_unit
        if current.status is WorkUnitStatus.AWAITING_USER_DECISION:
            gate = current.gate
            if (
                gate.reason is not GateReason.TEST_CHANGE
                or gate.fingerprint != fingerprint
                or gate.paths != normalized_paths
            ):
                return self
        elif current.status is not WorkUnitStatus.IN_PROGRESS:
            return self
        decisions = current.gate_decisions
        if prior_decision not in decisions:
            decisions = (*decisions, prior_decision)
        updated_unit = replace(
            current,
            status=WorkUnitStatus.IN_PROGRESS,
            gate=GateRecord(),
            gate_decisions=decisions,
            active_test_fingerprint=fingerprint,
            active_test_paths=normalized_paths,
        )
        slices = self._slices_with_current_status(SliceStatus.IN_PROGRESS)
        return self._replace_current_unit(
            updated_unit,
            slices=slices,
            updated_at=updated_at,
        )

    def await_user_gate(
        self,
        *,
        reason: GateReason,
        detail: str,
        fingerprint: str,
        paths: tuple[str, ...] = (),
        gate_step: WorkflowStep | None = None,
        resume_step: WorkflowStep | None = None,
        updated_at: str | None = None,
    ) -> WorkflowState:
        current = self.current_work_unit
        normalized_paths = tuple(sorted(set(paths)))
        gate = GateRecord(
            status=GateStatus.AWAITING_USER_DECISION,
            reason=reason,
            detail=detail,
            fingerprint=fingerprint,
            paths=normalized_paths,
            resume_step=resume_step,
        )
        next_step = gate_step or current.current_step
        if current.status is WorkUnitStatus.AWAITING_USER_DECISION:
            if current.gate == gate and current.current_step is next_step:
                return self
            raise WorkflowStateValidationError(
                "cannot replace an unresolved user gate with different evidence"
            )
        if current.status is not WorkUnitStatus.IN_PROGRESS:
            raise WorkflowStateValidationError(
                "only an in-progress work unit can enter a user gate"
            )
        updated_unit = replace(
            current,
            status=WorkUnitStatus.AWAITING_USER_DECISION,
            current_step=next_step,
            gate=gate,
        )
        slices = self._slices_with_current_status(SliceStatus.AWAITING_USER_DECISION)
        return self._replace_current_unit(
            updated_unit, slices=slices, updated_at=updated_at
        )

    def await_policy_gate(
        self,
        *,
        reason: GateReason,
        detail: str,
        paths: tuple[str, ...] = (),
        updated_at: str | None = None,
    ) -> WorkflowState:
        """Persist a machine- or agent-triggered halt that must be resolved, not approved."""
        if reason not in {GateReason.STOP_REQUEST, GateReason.UNEXPECTED_FILE}:
            raise WorkflowStateValidationError(
                "policy gate reason must be stop_request or unexpected_file"
            )
        current = self.current_work_unit
        normalized_paths = tuple(sorted(set(paths)))
        gate = GateRecord(
            status=GateStatus.AWAITING_USER_DECISION,
            reason=reason,
            detail=detail,
            paths=normalized_paths,
        )
        if current.status is WorkUnitStatus.AWAITING_USER_DECISION:
            if current.gate == gate:
                return self
            raise WorkflowStateValidationError(
                "cannot replace an unresolved policy gate with different evidence"
            )
        if current.status is not WorkUnitStatus.IN_PROGRESS:
            raise WorkflowStateValidationError(
                "only an in-progress work unit can enter a policy gate"
            )
        updated_unit = replace(
            current,
            status=WorkUnitStatus.AWAITING_USER_DECISION,
            gate=gate,
        )
        slices = self._slices_with_current_status(SliceStatus.AWAITING_USER_DECISION)
        return self._replace_current_unit(
            updated_unit, slices=slices, updated_at=updated_at
        )

    def record_user_gate_decision(
        self,
        *,
        approved: bool,
        fingerprint: str,
        paths: tuple[str, ...],
        decided_by: str,
        decided_at: str,
        rationale: str,
        updated_at: str | None = None,
    ) -> WorkflowState:
        current = self.current_work_unit
        if current.status is not WorkUnitStatus.AWAITING_USER_DECISION:
            raise WorkflowStateValidationError("workflow is not awaiting a user decision")
        gate = current.gate
        if gate.fingerprint is None:
            raise WorkflowStateValidationError(
                "this gate is not fingerprint-bound; use the legacy resume transition"
            )
        normalized_paths = tuple(sorted(set(paths)))
        if fingerprint != gate.fingerprint or normalized_paths != gate.paths:
            raise WorkflowStateValidationError(
                "user decision does not match the persisted gate fingerprint and paths"
            )
        decision = GateDecisionRecord(
            approved=approved,
            reason=gate.reason,
            fingerprint=fingerprint,
            paths=normalized_paths,
            decided_by=decided_by,
            decided_at=decided_at,
            rationale=rationale,
            resume_step=gate.resume_step,
        )
        updated_unit = replace(
            current,
            gate_decisions=(*current.gate_decisions, decision),
            status=(
                WorkUnitStatus.IN_PROGRESS
                if approved
                else WorkUnitStatus.AWAITING_USER_DECISION
            ),
            gate=GateRecord() if approved else gate,
        )
        slices = self.slices
        if approved:
            slices = self._slices_with_current_status(SliceStatus.IN_PROGRESS)
        return self._replace_current_unit(
            updated_unit,
            slices=slices,
            updated_at=updated_at or decided_at,
        )

    def record_review_denial(
        self,
        *,
        reviewer: Reviewer,
        open_findings: tuple[str, ...],
        return_step: WorkflowStep,
        updated_at: str | None = None,
    ) -> WorkflowState:
        _require_unique_non_empty(open_findings, "open_findings")
        if not open_findings:
            raise WorkflowStateValidationError("a review denial requires open findings")
        current = self.current_work_unit
        next_count = current.codex_return_count + 1
        if next_count > current.max_codex_returns:
            # State files produced before iteration-limit continuation extended the
            # budget could have a cleared gate while still carrying an exhausted
            # counter.  The user already acknowledged that limit by resuming, so
            # recover into the next bounded block without replaying a round number
            # or counting the same Codex return twice.
            extended_unit = replace(
                current,
                status=WorkUnitStatus.IN_PROGRESS,
                current_step=return_step,
                round_number=current.round_number + 1,
                max_codex_returns=(
                    current.max_codex_returns + DEFAULT_MAX_CODEX_RETURNS
                ),
                gate=GateRecord(),
                reviewer=reviewer,
                open_findings=open_findings,
            )
            return self._replace_current_unit(
                extended_unit,
                slices=self._slices_with_current_status(SliceStatus.IN_PROGRESS),
                updated_at=updated_at,
            )
        limit_reached = next_count == current.max_codex_returns
        gate = (
            GateRecord(
                status=GateStatus.AWAITING_USER_DECISION,
                reason=GateReason.ITERATION_LIMIT,
                detail=f"review denied by {reviewer.value} after {next_count} Codex returns",
            )
            if limit_reached
            else GateRecord()
        )
        updated_unit = replace(
            current,
            status=(
                WorkUnitStatus.AWAITING_USER_DECISION
                if limit_reached
                else WorkUnitStatus.IN_PROGRESS
            ),
            current_step=return_step,
            round_number=(current.round_number if limit_reached else current.round_number + 1),
            codex_return_count=next_count,
            gate=gate,
            reviewer=reviewer,
            open_findings=open_findings,
        )
        slices = self.slices
        if limit_reached:
            slices = self._slices_with_current_status(SliceStatus.AWAITING_USER_DECISION)
        return self._replace_current_unit(updated_unit, slices=slices, updated_at=updated_at)

    def record_invocation_failure(
        self,
        failure: InvocationFailureRecord,
        *,
        wait_automatically: bool,
        updated_at: str | None = None,
    ) -> WorkflowState:
        """Persist one failed role invocation without advancing its logical step."""
        current = self.current_work_unit
        if current.status is not WorkUnitStatus.IN_PROGRESS:
            raise WorkflowStateValidationError(
                "only an in-progress work unit can record an invocation failure"
            )
        if (
            failure.work_unit_id != current.work_unit_id
            or failure.slice_id != current.slice_id
            or failure.step is not current.current_step
        ):
            raise WorkflowStateValidationError(
                "invocation failure does not match the current work-unit identity"
            )
        if any(
            item.invocation_id == failure.invocation_id
            for item in current.invocation_failures
        ):
            raise WorkflowStateValidationError(
                "invocation failure id was already persisted"
            )
        if wait_automatically != failure.automatic_resume:
            raise WorkflowStateValidationError(
                "invocation wait mode differs from persisted failure evidence"
            )
        if wait_automatically and failure.failure_kind is not AgentFailureKind.QUOTA:
            raise WorkflowStateValidationError(
                "only quota failures may wait automatically"
            )
        status = (
            WorkUnitStatus.WAITING_FOR_QUOTA
            if wait_automatically
            else WorkUnitStatus.AWAITING_RESUME
        )
        slice_status = (
            SliceStatus.WAITING_FOR_QUOTA
            if wait_automatically
            else SliceStatus.AWAITING_RESUME
        )
        gate_status = (
            GateStatus.WAITING_FOR_QUOTA
            if wait_automatically
            else GateStatus.AWAITING_RESUME
        )
        reason = (
            GateReason.QUOTA
            if failure.failure_kind is AgentFailureKind.QUOTA
            else GateReason.INSTANCE_FAILURE
        )
        reset = failure.resume_at_utc or "manual resume required"
        detail = (
            f"role={failure.role} step={failure.step.value} "
            f"invocation={failure.invocation_id} kind={failure.failure_kind.value} "
            f"resume={reset} auto={str(failure.automatic_resume).lower()} "
            f"continuations={failure.auto_resume_count} provider={failure.provider_text}"
        )
        updated_unit = replace(
            current,
            status=status,
            gate=GateRecord(
                status=gate_status,
                reason=reason,
                detail=detail,
                fingerprint=failure.diff_fingerprint,
                resume_step=failure.step,
            ),
            invocation_failures=(*current.invocation_failures, failure),
        )
        slices = self._slices_with_current_status(slice_status)
        return self._replace_current_unit(
            updated_unit, slices=slices, updated_at=updated_at
        )

    def resume_after_invocation_halt(
        self, *, updated_at: str | None = None
    ) -> WorkflowState:
        """Resume exactly the failed role step after an automatic or manual halt."""
        current = self.current_work_unit
        if current.status not in {
            WorkUnitStatus.WAITING_FOR_QUOTA,
            WorkUnitStatus.AWAITING_RESUME,
        }:
            raise WorkflowStateValidationError(
                "workflow is not halted on an agent invocation"
            )
        if current.gate.resume_step is not current.current_step:
            raise WorkflowStateValidationError(
                "invocation halt does not preserve the current resume step"
            )
        updated_unit = replace(
            current,
            status=WorkUnitStatus.IN_PROGRESS,
            gate=GateRecord(),
        )
        slices = self._slices_with_current_status(SliceStatus.IN_PROGRESS)
        return self._replace_current_unit(
            updated_unit, slices=slices, updated_at=updated_at
        )

    def resume_after_user_decision(self, *, updated_at: str | None = None) -> WorkflowState:
        current = self.current_work_unit
        if current.status is not WorkUnitStatus.AWAITING_USER_DECISION:
            raise WorkflowStateValidationError("workflow is not awaiting a user decision")
        if current.gate.fingerprint is not None:
            raise WorkflowStateValidationError(
                "fingerprint-bound gate requires an explicit recorded user decision"
            )
        continuing_iteration_limit = current.gate.reason is GateReason.ITERATION_LIMIT
        completed_side_effects = current.completed_side_effects
        if (
            current.gate.reason is GateReason.STOP_REQUEST
            and current.gate.detail is not None
            and current.invocation_failures
        ):
            match = QUOTA_RESUME_DIFF_PATTERN.fullmatch(current.gate.detail)
            if match is not None:
                acknowledgement = quota_resume_diff_acknowledgement(
                    current.invocation_failures[-1].invocation_id,
                    match.group(1),
                )
                if acknowledgement not in completed_side_effects:
                    completed_side_effects = (
                        *completed_side_effects,
                        acknowledgement,
                    )
        updated_unit = replace(
            current,
            status=WorkUnitStatus.IN_PROGRESS,
            round_number=(
                current.round_number + 1
                if continuing_iteration_limit
                else current.round_number
            ),
            max_codex_returns=(
                current.max_codex_returns + DEFAULT_MAX_CODEX_RETURNS
                if continuing_iteration_limit
                else current.max_codex_returns
            ),
            completed_side_effects=completed_side_effects,
            gate=GateRecord(),
        )
        slices = self._slices_with_current_status(SliceStatus.IN_PROGRESS)
        return self._replace_current_unit(updated_unit, slices=slices, updated_at=updated_at)

    def _slices_with_current_status(
        self, status: SliceStatus
    ) -> tuple[SliceRecord, ...]:
        if self.current_work_unit.kind is WorkUnitKind.FINAL_REVIEW:
            return self.slices
        return tuple(
            replace(item, status=status)
            if item.slice_id == self.current_slice_id
            else item
            for item in self.slices
        )

    def _replace_current_unit(
        self,
        updated_unit: WorkUnitRecord,
        *,
        slices: tuple[SliceRecord, ...] | None = None,
        updated_at: str | None = None,
    ) -> WorkflowState:
        units = tuple(
            updated_unit if item.work_unit_id == self.current_work_unit_id else item
            for item in self.work_units
        )
        return replace(
            self,
            current_step=updated_unit.current_step,
            work_units=units,
            slices=self.slices if slices is None else slices,
            updated_at=updated_at or _now_iso(),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "run_id": self.run_id,
            "task_file": self.task_file,
            "branch": self.branch,
            "branch_base": self.branch_base,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "current_slice_id": self.current_slice_id,
            "current_work_unit_id": self.current_work_unit_id,
            "current_step": self.current_step.value,
            "slices": [item.to_dict() for item in self.slices],
            "work_units": [item.to_dict() for item in self.work_units],
            "planned_slices": [
                {
                    "slice_id": item.slice_id,
                    "summary": item.summary,
                    "scope_paths": list(item.scope_paths),
                }
                for item in self.planned_slices
            ],
            "runtime_history": self.runtime_history,
            "task_digest": self.task_digest,
            "execution_mode": self.execution_mode,
            "task_scope_patterns": list(self.task_scope_patterns),
            "work_plan_path": self.work_plan_path,
            "audit_report_path": self.audit_report_path,
            "target_branch": self.target_branch,
            "protocol_binding": (
                None if self.protocol_binding is None else self.protocol_binding.to_dict()
            ),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> WorkflowState:
        legacy_keys = {
                "version",
                "run_id",
                "task_file",
                "branch",
                "branch_base",
                "created_at",
                "updated_at",
                "current_slice_id",
                "current_work_unit_id",
                "current_step",
                "slices",
                "work_units",
            }
        previous_keys = {*legacy_keys, "planned_slices", "runtime_history"}
        current_keys = {
            *previous_keys,
            "task_digest",
            "execution_mode",
            "task_scope_patterns",
            "work_plan_path",
            "target_branch",
        }
        audit_keys = {*current_keys, "audit_report_path"}
        protocol_keys = {*audit_keys, "protocol_binding"}
        if set(raw) == legacy_keys:
            planned_slices: tuple[PlannedSlice, ...] = ()
            runtime_history = None
            task_digest = None
            execution_mode = "IMPLEMENT"
            task_scope_patterns: tuple[str, ...] = ()
            work_plan_path = None
            audit_report_path = None
            target_branch = None
            protocol_binding = None
        else:
            raw_keys = frozenset(raw)
            if raw_keys not in {
                frozenset(previous_keys),
                frozenset(current_keys),
                frozenset(audit_keys),
            }:
                if raw_keys != frozenset(protocol_keys):
                    _require_exact_keys(raw, protocol_keys, "workflow state")
            raw_plan = _list(raw["planned_slices"], "planned_slices")
            planned: list[PlannedSlice] = []
            for index, item in enumerate(raw_plan):
                plan_item = _mapping(item, f"planned_slices[{index}]")
                _require_exact_keys(
                    plan_item,
                    {"slice_id", "summary", "scope_paths"},
                    f"planned_slices[{index}]",
                )
                try:
                    planned.append(
                        PlannedSlice(
                            slice_id=_positive_int(
                                plan_item["slice_id"], f"planned_slices[{index}].slice_id"
                            ),
                            summary=_string(
                                plan_item["summary"], f"planned_slices[{index}].summary"
                            ),
                            scope_paths=_string_tuple(
                                plan_item["scope_paths"],
                                f"planned_slices[{index}].scope_paths",
                            ),
                        )
                    )
                except ValueError as exc:
                    raise WorkflowStateValidationError(str(exc)) from exc
            planned_slices = tuple(planned)
            history_raw = raw["runtime_history"]
            runtime_history = (
                None if history_raw is None else _mapping(history_raw, "runtime_history")
            )
            if raw_keys == frozenset(previous_keys):
                task_digest = None
                execution_mode = "IMPLEMENT"
                task_scope_patterns = ()
                work_plan_path = None
                audit_report_path = None
                target_branch = None
            else:
                task_digest = _optional_string(raw["task_digest"], "task_digest")
                execution_mode = _string(raw["execution_mode"], "execution_mode")
                task_scope_patterns = _string_tuple(
                    raw["task_scope_patterns"], "task_scope_patterns"
                )
                work_plan_path = _optional_string(
                    raw["work_plan_path"], "work_plan_path"
                )
                audit_report_path = (
                    _optional_string(raw["audit_report_path"], "audit_report_path")
                    if raw_keys in {frozenset(audit_keys), frozenset(protocol_keys)}
                    else None
                )
                target_branch = _optional_string(raw["target_branch"], "target_branch")
            binding_raw = raw.get("protocol_binding")
            protocol_binding = (
                None
                if binding_raw is None
                else ProtocolBinding.from_dict(
                    _mapping(binding_raw, "protocol_binding")
                )
            )
        slices_raw = _list(raw["slices"], "slices")
        units_raw = _list(raw["work_units"], "work_units")
        return cls(
            version=_positive_int(raw["version"], "version"),
            run_id=_string(raw["run_id"], "run_id"),
            task_file=_string(raw["task_file"], "task_file"),
            branch=_string(raw["branch"], "branch"),
            branch_base=_string(raw["branch_base"], "branch_base"),
            created_at=_string(raw["created_at"], "created_at"),
            updated_at=_string(raw["updated_at"], "updated_at"),
            current_slice_id=_positive_int(raw["current_slice_id"], "current_slice_id"),
            current_work_unit_id=_positive_int(
                raw["current_work_unit_id"], "current_work_unit_id"
            ),
            current_step=_enum_value(WorkflowStep, raw["current_step"], "current_step"),
            slices=tuple(SliceRecord.from_dict(_mapping(item, "slice")) for item in slices_raw),
            work_units=tuple(
                WorkUnitRecord.from_dict(_mapping(item, "work unit")) for item in units_raw
            ),
            planned_slices=planned_slices,
            runtime_history=runtime_history,
            task_digest=task_digest,
            execution_mode=execution_mode,
            task_scope_patterns=task_scope_patterns,
            work_plan_path=work_plan_path,
            audit_report_path=audit_report_path,
            target_branch=target_branch,
            protocol_binding=protocol_binding,
        )


def init_workflow_state(
    *,
    run_id: str,
    task_file: str,
    branch: str,
    branch_base: str,
    slice_count: int,
    first_slice_start_commit: str | None = None,
    task_digest: str | None = None,
    execution_mode: str = "IMPLEMENT",
    task_scope_patterns: tuple[str, ...] = (),
    work_plan_path: str | None = None,
    audit_report_path: str | None = None,
    target_branch: str | None = None,
    protocol_binding: ProtocolBinding | None = None,
    timestamp: str | None = None,
) -> WorkflowState:
    _require_positive_int(slice_count, "slice_count")
    stamp = timestamp or _now_iso()
    slices = tuple(
        SliceRecord(
            slice_id=slice_id,
            status=SliceStatus.IN_PROGRESS if slice_id == 1 else SliceStatus.PENDING,
            start_commit=(first_slice_start_commit or branch_base) if slice_id == 1 else None,
        )
        for slice_id in range(1, slice_count + 1)
    )
    work_unit = WorkUnitRecord(
        work_unit_id=1,
        slice_id=1,
        kind=WorkUnitKind.PLAN,
        status=WorkUnitStatus.IN_PROGRESS,
        current_step=WorkflowStep.CODEX_PLAN,
    )
    return WorkflowState(
        version=STATE_VERSION,
        run_id=run_id,
        task_file=task_file,
        branch=branch,
        branch_base=branch_base,
        created_at=stamp,
        updated_at=stamp,
        current_slice_id=1,
        current_work_unit_id=1,
        current_step=work_unit.current_step,
        slices=slices,
        work_units=(work_unit,),
        task_digest=task_digest,
        execution_mode=execution_mode,
        task_scope_patterns=task_scope_patterns,
        work_plan_path=work_plan_path,
        audit_report_path=audit_report_path,
        target_branch=target_branch,
        protocol_binding=protocol_binding,
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_non_empty(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowStateValidationError(f"{label} must be a non-empty string")


def _require_timestamp(value: str, label: str) -> None:
    _require_non_empty(value, label)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WorkflowStateValidationError(
            f"{label} must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WorkflowStateValidationError(
            f"{label} must include an explicit timezone offset"
        )


def _require_positive_int(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise WorkflowStateValidationError(f"{label} must be a 1-based integer")


def _require_unique_non_empty(values: tuple[str, ...], label: str) -> None:
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise WorkflowStateValidationError(f"{label} entries must be non-empty strings")
    if len(set(values)) != len(values):
        raise WorkflowStateValidationError(f"{label} entries must be unique")


def _normalize_scope_paths(values: tuple[str, ...]) -> tuple[str, ...]:
    if not values:
        raise WorkflowStateValidationError("slice scope_paths must not be empty")
    normalized = tuple(sorted(set(values)))
    _require_canonical_scope(normalized)
    return normalized


def _normalize_scope_change_groups(
    groups: tuple[tuple[str, ...], ...],
    scope_paths: tuple[str, ...],
) -> tuple[tuple[str, ...], ...]:
    normalized = tuple(tuple(sorted(set(group))) for group in groups)
    _require_scope_change_groups(normalized, scope_paths)
    return normalized


def _require_scope_change_groups(
    groups: tuple[tuple[str, ...], ...],
    scope_paths: tuple[str, ...],
) -> None:
    if not groups or any(not group for group in groups):
        raise WorkflowStateValidationError(
            "slice scope change groups must be non-empty"
        )
    if groups != tuple(sorted(groups)) or len(set(groups)) != len(groups):
        raise WorkflowStateValidationError(
            "slice scope change groups must be sorted and unique"
        )
    flattened = tuple(path for group in groups for path in group)
    if len(set(flattened)) != len(flattened) or tuple(sorted(flattened)) != scope_paths:
        raise WorkflowStateValidationError(
            "slice scope change groups must partition the exact scope paths"
        )


def _require_canonical_scope(values: tuple[str, ...]) -> None:
    if values != tuple(sorted(set(values))):
        raise WorkflowStateValidationError(
            "slice scope_paths must be sorted, unique, relative POSIX paths"
        )
    for value in values:
        if not isinstance(value, str) or not value or "\\" in value:
            raise WorkflowStateValidationError(
                "slice scope_paths must be sorted, unique, relative POSIX paths"
            )
        path = PurePosixPath(value)
        if path.is_absolute() or not path.parts or any(
            part in ("", ".", "..") for part in path.parts
        ) or path.parts[0] == ".orchestrator":
            raise WorkflowStateValidationError(
                "slice scope_paths must be sorted, unique, relative POSIX paths outside .orchestrator"
            )


def _require_contiguous_ids(values: tuple[int, ...], label: str) -> None:
    expected = tuple(range(1, len(values) + 1))
    if values != expected:
        raise WorkflowStateValidationError(
            f"{label} ids must be ordered, unique, contiguous, and 1-based"
        )


def _require_exact_keys(raw: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(raw)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise WorkflowStateValidationError(
            f"invalid {label} fields: missing={missing}, unexpected={extra}"
        )


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise WorkflowStateValidationError(f"{label} must be an object")
    return value


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise WorkflowStateValidationError(f"{label} must be a list")
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise WorkflowStateValidationError(f"{label} must be a string")
    return value


def _optional_string(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _string(value, label)


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise WorkflowStateValidationError(f"{label} must be a 1-based integer")
    return value


def _non_negative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WorkflowStateValidationError(f"{label} must be a non-negative integer")
    return value


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    return tuple(_string(item, label) for item in _list(value, label))


def _enum_value(enum_type: type[Enum], value: object, label: str):
    if not isinstance(value, str):
        raise WorkflowStateValidationError(f"{label} must be a string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise WorkflowStateValidationError(f"unknown {label}: {value!r}") from exc
