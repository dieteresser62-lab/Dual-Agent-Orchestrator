from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping

from agent_runtime import (
    AgentInvocationError,
    AgentProcessError,
    NativeAgentCodexOutput,
    NativeAgentReviewOutput,
    QuotaWaitPolicy,
    TransientRetryPolicy,
    classify_agent_failure,
)
from artifact_models import FamilyBindingPayload, InvocationFailurePayload
from audit_trail import ReviewAuditEvent, ValidationAuditEvent
from contracts import (
    AgentRole,
    CodexStepContract,  # allowlist:provider -- typed boundary
    FindingAcceptanceMeasurement,
    FindingRecord,
    PlannedSlice,
    StepContract,
    ValidationAttestation,
    ValidationRecord,
    ValidationStatus,
)
from content_authority import ValidationCapture, validation_output_digest
from finding_order import finding_id_sort_key
from finding_convergence import SliceConvergenceEvaluation, SliceReviewPhase
from gates import TestChangeEvidence
from review_packets import ReviewPacket
from validation_matrix import (
    ValidationCommand,
    ValidationRequest,
    validation_attestation_id,
)
from workflow import (
    CodexInvocation,
    PersistedNativeReviewerReplay,
    ReviewerInvocation,
    ValidationExecutionError,
    WorkflowChanges,
    WorkflowCommitRequest,
    WorkflowContext,
    WorkflowEngine,
    WorkflowHistory,
    WorkflowRunResult,
)
from native_codex_contract import (
    canonical_native_codex_json,
    parse_bound_native_codex_contract_result,
)
from native_codex_request import validate_native_codex_provider_response
from native_review_contract import (
    canonical_native_review_json,
    parse_bound_native_contract_result,
)
from native_review_request import validate_native_review_provider_response
from workflow_state import (
    AgentFailureKind,
    GateDecisionRecord,
    GateReason,
    GateStatus,
    ProtocolBinding,
    ProtocolMode,
    WorkUnitStatus,
    WorkUnitKind,
    WorkflowState,
    WorkflowStep,
    init_workflow_state,
)


SCENARIO_VERSION = 1
GATE_RULE_PREFIX = re.compile(r"^([A-Z][A-Z0-9_-]*) \| ")
PREFIXLESS_GATE_RULES = (
    (
        GateReason.ITERATION_LIMIT,
        "PREFIXLESS:REVIEW-DENIAL",
        re.compile(r"review denied by claude after [1-9][0-9]* Codex returns"),
    ),
    (
        GateReason.ANCHOR_CHANGE,
        "PREFIXLESS:ANCHOR-CHANGE",
        re.compile(r"approved plan anchors changed and require plan review reset"),
    ),
    (
        GateReason.MANUAL_SLICE,
        "PREFIXLESS:MANUAL-SLICE",
        re.compile(r"manual slice approval is required before commit"),
    ),
    (
        GateReason.TEST_CHANGE,
        "PREFIXLESS:TEST-CHANGE",
        re.compile(r"test changes require explicit approval before review"),
    ),
    *(
        (
            reason,
            "PREFIXLESS:INVOCATION-FAILURE",
            re.compile(
                r"role=(?:codex|claude) step=[a-z_]+ invocation=[A-Za-z0-9._:-]+ "
                r"kind=[a-z_]+ resume=.+ auto=(?:true|false) continuations=[0-9]+ "
                r"provider=.+"
            ),
        )
        for reason in (GateReason.INSTANCE_FAILURE, GateReason.QUOTA)
    ),
    *(
        (
            reason,
            "PREFIXLESS:LEGACY-QUOTA-REVALIDATION",
            re.compile(
                r"legacy QUOTA-RESUME-DIFF requires fingerprint-bound "
                r"repository revalidation"
            ),
        )
        for reason in (GateReason.INSTANCE_FAILURE, GateReason.QUOTA)
    ),
)


class DryRunScenarioError(RuntimeError):
    """Raised when a scripted dry-run is missing or contradicts explicit evidence."""


class ScriptedInterruption(RuntimeError):
    """Controlled fake-process interruption used by deterministic wait scenarios."""


def _require_exact_keys(
    raw: Mapping[str, object], required: set[str], optional: set[str], label: str
) -> None:
    missing = sorted(required - set(raw))
    unknown = sorted(set(raw) - required - optional)
    if missing:
        raise DryRunScenarioError(f"{label} is missing key {missing[0]!r}")
    if unknown:
        raise DryRunScenarioError(f"{label} has unknown key {unknown[0]!r}")


def _string(raw: object, label: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise DryRunScenarioError(f"{label} must be a non-empty string")
    return raw


def _positive_int(raw: object, label: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise DryRunScenarioError(f"{label} must be a 1-based integer")
    return raw


def _request_sequence(raw: Mapping[str, object], label: str) -> int:
    """Read the B152 name while accepting the pre-B152 scenario spelling."""

    present = tuple(
        key for key in ("request_sequence", "round_number") if key in raw
    )
    if not present:
        raise DryRunScenarioError(f"{label} is missing key 'request_sequence'")
    if len(present) != 1:
        raise DryRunScenarioError(
            f"{label} must not contain both 'request_sequence' and 'round_number'"
        )
    key = present[0]
    return _positive_int(raw[key], f"{label}.{key}")


def _mapping(raw: object, label: str) -> Mapping[str, object]:
    if not isinstance(raw, dict):
        raise DryRunScenarioError(f"{label} must be an object")
    return raw


def _string_tuple(raw: object, label: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(raw, list) or (not raw and not allow_empty):
        qualifier = "an array" if allow_empty else "a non-empty array"
        raise DryRunScenarioError(f"{label} must be {qualifier}")
    result = tuple(_string(value, f"{label} entry") for value in raw)
    if result != tuple(sorted(set(result))):
        raise DryRunScenarioError(f"{label} must be sorted and unique")
    return result


def _ordered_string_tuple(raw: object, label: str) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise DryRunScenarioError(f"{label} must be an array")
    return tuple(_string(value, f"{label} entry") for value in raw)


def _timestamp(raw: object, label: str) -> datetime:
    value = _string(raw, label)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DryRunScenarioError(f"{label} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DryRunScenarioError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class ScriptedFailure:
    failure_kind: AgentFailureKind
    provider_text: str
    received_at: datetime
    provider_data: Mapping[str, object] | None = None
    process_exit_code: int | None = None

    def __post_init__(self) -> None:
        if not self.provider_text.strip():
            raise ValueError("scripted failure provider_text must not be empty")
        if self.received_at.tzinfo is None or self.received_at.utcoffset() is None:
            raise ValueError("scripted failure received_at must be timezone-aware")

    @classmethod
    def from_dict(cls, raw: Mapping[str, object], label: str) -> ScriptedFailure:
        _require_exact_keys(
            raw,
            {"failure_kind", "provider_text", "received_at"},
            {"provider_data", "process_exit_code"},
            label,
        )
        try:
            kind = AgentFailureKind(_string(raw["failure_kind"], f"{label}.failure_kind"))
        except ValueError as exc:
            raise DryRunScenarioError(f"{label}.failure_kind is unknown") from exc
        provider_data = raw.get("provider_data")
        if provider_data is not None:
            provider_data = _mapping(provider_data, f"{label}.provider_data")
        exit_code = raw.get("process_exit_code")
        if exit_code is not None and (isinstance(exit_code, bool) or not isinstance(exit_code, int)):
            raise DryRunScenarioError(f"{label}.process_exit_code must be an integer")
        return cls(
            kind,
            _string(raw["provider_text"], f"{label}.provider_text"),
            _timestamp(raw["received_at"], f"{label}.received_at"),
            provider_data,
            exit_code,
        )

    def to_exception(self, role: AgentRole, invocation_id: str) -> AgentInvocationError:
        error = AgentProcessError(
            self.provider_text,
            exit_code=self.process_exit_code,
            kind_hint=self.failure_kind,
            provider_data=self.provider_data,
        )
        classified = classify_agent_failure(
            role.value,
            error,
            invocation_id=invocation_id,
            received_at=self.received_at,
        )
        if classified.kind is not self.failure_kind:
            raise DryRunScenarioError(
                f"scripted {role.value} failure expected {self.failure_kind.value}, "
                f"classifier produced {classified.kind.value}"
            )
        return classified


@dataclass(frozen=True)
class ScriptedAgentEvent:
    role: AgentRole
    work_unit_id: int
    request_sequence: int
    step: WorkflowStep
    output: Mapping[str, object] | None = None
    failure: ScriptedFailure | None = None

    def __post_init__(self) -> None:
        if self.role not in {AgentRole.CODEX, AgentRole.CLAUDE}:
            raise ValueError("scripted event requires a workflow role")
        if self.work_unit_id < 1 or self.request_sequence < 1:
            raise ValueError("scripted event identity must be 1-based")
        if (self.output is None) == (self.failure is None):
            raise ValueError("scripted event requires exactly one of output or failure")

    @classmethod
    def from_dict(cls, raw: Mapping[str, object], index: int) -> ScriptedAgentEvent:
        label = f"agent_events[{index}]"
        _require_exact_keys(
            raw,
            {"role", "work_unit_id", "step"},
            {"request_sequence", "round_number", "output", "failure"},
            label,
        )
        try:
            role = AgentRole(_string(raw["role"], f"{label}.role"))
            step = WorkflowStep(_string(raw["step"], f"{label}.step"))
        except ValueError as exc:
            raise DryRunScenarioError(f"{label} has an unknown role or step") from exc
        output = raw.get("output")
        if output is not None:
            output = _mapping(output, f"{label}.output")
        failure_raw = raw.get("failure")
        failure = (
            ScriptedFailure.from_dict(_mapping(failure_raw, f"{label}.failure"), f"{label}.failure")
            if failure_raw is not None
            else None
        )
        try:
            return cls(
                role,
                _positive_int(raw["work_unit_id"], f"{label}.work_unit_id"),
                _request_sequence(raw, label),
                step,
                output,
                failure,
            )
        except ValueError as exc:
            raise DryRunScenarioError(str(exc)) from exc


@dataclass(frozen=True)
class ScriptedChange:
    work_unit_id: int
    request_sequence: int
    start_commit: str
    fingerprint: str
    paths: tuple[str, ...]
    full_diff: str
    gate_paths: tuple[str, ...] = ()

    @property
    def workflow_changes(self) -> WorkflowChanges:
        return WorkflowChanges(
            self.start_commit,
            self.fingerprint,
            self.paths,
            self.full_diff,
            self.gate_paths,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, object], index: int) -> ScriptedChange:
        label = f"changes[{index}]"
        _require_exact_keys(
            raw,
            {"work_unit_id", "start_commit", "fingerprint", "paths", "full_diff"},
            {"request_sequence", "round_number", "gate_paths"},
            label,
        )
        return cls(
            _positive_int(raw["work_unit_id"], f"{label}.work_unit_id"),
            _request_sequence(raw, label),
            _string(raw["start_commit"], f"{label}.start_commit"),
            _string(raw["fingerprint"], f"{label}.fingerprint"),
            _string_tuple(raw["paths"], f"{label}.paths"),
            _string(raw["full_diff"], f"{label}.full_diff"),
            _string_tuple(raw.get("gate_paths", []), f"{label}.gate_paths", allow_empty=True),
        )


@dataclass(frozen=True)
class ScriptedValidation:
    fingerprint: str
    status: str = "pass"
    attestation_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"pass", "fail", "incomplete", "missing"}:
            raise ValueError("scripted validation status must be pass, fail, incomplete, or missing")

    @classmethod
    def from_dict(cls, raw: Mapping[str, object], index: int) -> ScriptedValidation:
        label = f"validations[{index}]"
        _require_exact_keys(raw, {"fingerprint", "status"}, {"attestation_fingerprint"}, label)
        foreign = raw.get("attestation_fingerprint")
        return cls(
            _string(raw["fingerprint"], f"{label}.fingerprint"),
            _string(raw["status"], f"{label}.status"),
            _string(foreign, f"{label}.attestation_fingerprint") if foreign is not None else None,
        )


@dataclass(frozen=True)
class ScriptedCommit:
    slice_id: int
    fingerprint: str
    commit_ref: str

    @classmethod
    def from_dict(cls, raw: Mapping[str, object], index: int) -> ScriptedCommit:
        label = f"commits[{index}]"
        _require_exact_keys(
            raw, {"slice_id", "fingerprint", "commit_ref"}, set(), label
        )
        return cls(
            _positive_int(raw["slice_id"], f"{label}.slice_id"),
            _string(raw["fingerprint"], f"{label}.fingerprint"),
            _string(raw["commit_ref"], f"{label}.commit_ref"),
        )


@dataclass(frozen=True)
class ScriptedTestChange:
    diff_fingerprint: str
    paths: tuple[str, ...]
    test_fingerprint: str

    @classmethod
    def from_dict(
        cls, raw: Mapping[str, object], index: int
    ) -> ScriptedTestChange:
        label = f"test_changes[{index}]"
        _require_exact_keys(
            raw,
            {"diff_fingerprint", "paths", "test_fingerprint"},
            set(),
            label,
        )
        return cls(
            _string(raw["diff_fingerprint"], f"{label}.diff_fingerprint"),
            _string_tuple(raw["paths"], f"{label}.paths"),
            _string(raw["test_fingerprint"], f"{label}.test_fingerprint"),
        )


@dataclass(frozen=True)
class ScriptedInitialState:
    kind: WorkUnitKind = WorkUnitKind.SLICE
    branch: str = "feature/dry-run"
    slice_count: int = 1
    max_codex_returns: int = 4
    scope_paths: tuple[str, ...] = ()
    execution_mode: str = "IMPLEMENT"
    work_plan_path: str | None = None
    approved_plan_commit: str | None = None
    planned_slices: tuple[PlannedSlice, ...] = ()
    family_binding: FamilyBindingPayload | None = None
    first_slice_start_commit: str | None = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ScriptedInitialState:
        _require_exact_keys(
            raw,
            set(),
            {
                "kind", "branch", "slice_count", "max_codex_returns", "scope_paths",
                "execution_mode", "work_plan_path", "approved_plan_commit",
                "planned_slices",
            },
            "scenario.initial",
        )
        try:
            kind = WorkUnitKind(str(raw.get("kind", WorkUnitKind.SLICE.value)))
        except ValueError as exc:
            raise DryRunScenarioError("scenario.initial.kind is unknown") from exc
        if kind not in {
            WorkUnitKind.PLAN,
            WorkUnitKind.SLICE,
            WorkUnitKind.BRANCH_DISCOVERY,
        }:
            raise DryRunScenarioError(
                "scenario.initial.kind must be plan, slice, or branch discovery"
            )
        execution_mode = _string(
            raw.get("execution_mode", "IMPLEMENT"), "scenario.initial.execution_mode"
        )
        if execution_mode not in {"IMPLEMENT", "PLAN_ONLY", "BRANCH_DISCOVERY"}:
            raise DryRunScenarioError("scenario.initial.execution_mode is unknown")
        work_plan_raw = raw.get("work_plan_path")
        approved_commit_raw = raw.get("approved_plan_commit")
        if work_plan_raw is not None and not isinstance(work_plan_raw, str):
            raise DryRunScenarioError("scenario.initial.work_plan_path must be a string")
        if approved_commit_raw is not None and not isinstance(approved_commit_raw, str):
            raise DryRunScenarioError(
                "scenario.initial.approved_plan_commit must be a string"
            )
        planned_raw = raw.get("planned_slices", [])
        if not isinstance(planned_raw, list):
            raise DryRunScenarioError("scenario.initial.planned_slices must be an array")
        planned_slices: list[PlannedSlice] = []
        for index, item in enumerate(planned_raw):
            item = _mapping(item, f"scenario.initial.planned_slices[{index}]")
            _require_exact_keys(
                item,
                {"slice_id", "summary", "scope_paths"},
                set(),
                f"scenario.initial.planned_slices[{index}]",
            )
            planned_slices.append(
                PlannedSlice(
                    _positive_int(
                        item["slice_id"],
                        f"scenario.initial.planned_slices[{index}].slice_id",
                    ),
                    _string(
                        item["summary"],
                        f"scenario.initial.planned_slices[{index}].summary",
                    ),
                    _string_tuple(
                        item["scope_paths"],
                        f"scenario.initial.planned_slices[{index}].scope_paths",
                    ),
                )
            )
        return cls(
            kind=kind,
            branch=_string(raw.get("branch", "feature/dry-run"), "scenario.initial.branch"),
            slice_count=_positive_int(raw.get("slice_count", 1), "scenario.initial.slice_count"),
            max_codex_returns=_positive_int(
                raw.get("max_codex_returns", 4),
                "scenario.initial.max_codex_returns",
            ),
            scope_paths=_string_tuple(
                raw.get("scope_paths", []),
                "scenario.initial.scope_paths",
                allow_empty=True,
            ),
            execution_mode=execution_mode,
            work_plan_path=work_plan_raw,
            approved_plan_commit=approved_commit_raw,
            planned_slices=tuple(planned_slices),
        )


@dataclass(frozen=True)
class ScriptedContext:
    expected_test_files: tuple[str, ...] = ()
    test_changes_approved: bool = True
    manual_slice_gate: bool = False
    red_state_followup_slice: str | None = None
    current_branch: str | None = None
    quota_wait_policy: QuotaWaitPolicy = QuotaWaitPolicy()
    transient_retry_policy: TransientRetryPolicy = TransientRetryPolicy()

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ScriptedContext:
        _require_exact_keys(
            raw,
            set(),
            {
                "expected_test_files", "test_changes_approved", "manual_slice_gate",
                "red_state_followup_slice", "current_branch",
                "quota_wait_policy",
                "transient_retry_policy",
            },
            "scenario.context",
        )
        test_files = _string_tuple(
            raw.get("expected_test_files", []),
            "scenario.context.expected_test_files",
            allow_empty=True,
        )
        for key in ("test_changes_approved", "manual_slice_gate"):
            if key in raw and not isinstance(raw[key], bool):
                raise DryRunScenarioError(f"scenario.context.{key} must be a boolean")
        red_state = raw.get("red_state_followup_slice")
        branch = raw.get("current_branch")
        quota_raw = raw.get("quota_wait_policy", {})
        quota_table = _mapping(quota_raw, "scenario.context.quota_wait_policy")
        _require_exact_keys(
            quota_table,
            set(),
            {
                "automatic", "safety_margin_seconds", "maximum_wait_seconds",
                "maximum_auto_resumes", "heartbeat_interval_seconds",
            },
            "scenario.context.quota_wait_policy",
        )
        quota_defaults = QuotaWaitPolicy()
        try:
            quota = QuotaWaitPolicy(
                automatic=quota_table.get("automatic", quota_defaults.automatic),
                safety_margin_seconds=quota_table.get(
                    "safety_margin_seconds", quota_defaults.safety_margin_seconds
                ),
                maximum_wait_seconds=quota_table.get(
                    "maximum_wait_seconds", quota_defaults.maximum_wait_seconds
                ),
                maximum_auto_resumes=quota_table.get(
                    "maximum_auto_resumes", quota_defaults.maximum_auto_resumes
                ),
                heartbeat_interval_seconds=quota_table.get(
                    "heartbeat_interval_seconds",
                    quota_defaults.heartbeat_interval_seconds,
                ),
            )
        except ValueError as exc:
            raise DryRunScenarioError(str(exc)) from exc
        transient_raw = raw.get("transient_retry_policy", {})
        transient_table = _mapping(
            transient_raw, "scenario.context.transient_retry_policy"
        )
        _require_exact_keys(
            transient_table,
            set(),
            {
                "automatic",
                "initial_delay_seconds",
                "maximum_delay_seconds",
                "maximum_auto_resumes",
            },
            "scenario.context.transient_retry_policy",
        )
        transient_defaults = TransientRetryPolicy()
        try:
            transient = TransientRetryPolicy(
                automatic=transient_table.get(
                    "automatic", transient_defaults.automatic
                ),
                initial_delay_seconds=transient_table.get(
                    "initial_delay_seconds", transient_defaults.initial_delay_seconds
                ),
                maximum_delay_seconds=transient_table.get(
                    "maximum_delay_seconds", transient_defaults.maximum_delay_seconds
                ),
                maximum_auto_resumes=transient_table.get(
                    "maximum_auto_resumes", transient_defaults.maximum_auto_resumes
                ),
            )
        except ValueError as exc:
            raise DryRunScenarioError(str(exc)) from exc
        return cls(
            expected_test_files=test_files,
            test_changes_approved=raw.get("test_changes_approved", True),
            manual_slice_gate=raw.get("manual_slice_gate", False),
            red_state_followup_slice=(
                _string(red_state, "scenario.context.red_state_followup_slice")
                if red_state is not None
                else None
            ),
            current_branch=(
                _string(branch, "scenario.context.current_branch")
                if branch is not None
                else None
            ),
            quota_wait_policy=quota,
            transient_retry_policy=transient,
        )


@dataclass(frozen=True)
class ScenarioGateExpectation:
    status: GateStatus
    reason: GateReason
    rule_id: str
    kind: str
    fingerprint: str | None = None
    paths: tuple[str, ...] = ()
    resume_step: WorkflowStep | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"policy", "resume", "user"}:
            raise ValueError("scenario gate kind must be policy, resume, or user")
        prefixed = GATE_RULE_PREFIX.fullmatch(f"{self.rule_id} | ") is not None
        prefixless = re.fullmatch(r"PREFIXLESS:[A-Z][A-Z0-9-]*", self.rule_id)
        if not self.rule_id or (not prefixed and prefixless is None):
            raise ValueError("scenario gate rule_id must be an exact uppercase rule id")
        if self.paths != tuple(sorted(set(self.paths))):
            raise ValueError("scenario gate paths must be sorted and unique")

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ScenarioGateExpectation:
        _require_exact_keys(
            raw,
            {"status", "reason", "rule_id", "kind", "paths"},
            {"fingerprint", "resume_step"},
            "scenario.expect.gate",
        )
        try:
            status = GateStatus(
                _string(raw["status"], "scenario.expect.gate.status")
            )
            reason = GateReason(_string(raw["reason"], "scenario.expect.gate.reason"))
            resume_raw = raw.get("resume_step")
            resume_step = (
                None
                if resume_raw is None
                else WorkflowStep(
                    _string(resume_raw, "scenario.expect.gate.resume_step")
                )
            )
        except ValueError as exc:
            raise DryRunScenarioError(
                "scenario.expect.gate has an unknown status, reason, or resume step"
            ) from exc
        fingerprint_raw = raw.get("fingerprint")
        return cls(
            status=status,
            reason=reason,
            rule_id=_string(raw["rule_id"], "scenario.expect.gate.rule_id"),
            kind=_string(raw["kind"], "scenario.expect.gate.kind"),
            fingerprint=(
                None
                if fingerprint_raw is None
                else _string(fingerprint_raw, "scenario.expect.gate.fingerprint")
            ),
            paths=_string_tuple(
                raw["paths"], "scenario.expect.gate.paths", allow_empty=True
            ),
            resume_step=resume_step,
        )


@dataclass(frozen=True)
class ScenarioExpectations:
    exit_code: int
    status: str | None = None
    step: WorkflowStep | None = None
    gate_reason: GateReason | None = None
    gate: ScenarioGateExpectation | None = None
    commit_count: int | None = None
    remaining_agent_events: int | None = 0
    audit_contains: tuple[str, ...] = ()
    agent_order: tuple[str, ...] = ()
    validation_counts: Mapping[str, int] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ScenarioExpectations:
        _require_exact_keys(
            raw,
            {"exit_code"},
            {
                "status", "step", "gate_reason", "gate", "commit_count",
                "remaining_agent_events", "audit_contains",
                "agent_order", "validation_counts",
            },
            "scenario.expect",
        )
        exit_code = raw["exit_code"]
        if isinstance(exit_code, bool) or not isinstance(exit_code, int) or exit_code < 0:
            raise DryRunScenarioError("scenario.expect.exit_code must be non-negative")
        try:
            step = WorkflowStep(raw["step"]) if "step" in raw else None
            reason = GateReason(raw["gate_reason"]) if "gate_reason" in raw else None
        except ValueError as exc:
            raise DryRunScenarioError("scenario.expect has an unknown step or gate reason") from exc
        commit_count = raw.get("commit_count")
        remaining = raw.get("remaining_agent_events", 0)
        for value, label in (
            (commit_count, "commit_count"),
            (remaining, "remaining_agent_events"),
        ):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise DryRunScenarioError(
                    f"scenario.expect.{label} must be a non-negative integer"
                )
        audit = _string_tuple(
            raw.get("audit_contains", []),
            "scenario.expect.audit_contains",
            allow_empty=True,
        )
        agent_order = _ordered_string_tuple(
            raw.get("agent_order", []),
            "scenario.expect.agent_order",
        )
        validation_counts_raw = _mapping(
            raw.get("validation_counts", {}),
            "scenario.expect.validation_counts",
        )
        validation_counts: dict[str, int] = {}
        for fingerprint, count in validation_counts_raw.items():
            if not isinstance(fingerprint, str) or not fingerprint.strip():
                raise DryRunScenarioError(
                    "scenario.expect.validation_counts keys must be fingerprints"
                )
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise DryRunScenarioError(
                    "scenario.expect.validation_counts values must be non-negative integers"
                )
            validation_counts[fingerprint] = count
        return cls(
            exit_code=exit_code,
            status=(
                _string(raw["status"], "scenario.expect.status")
                if "status" in raw
                else None
            ),
            step=step,
            gate_reason=reason,
            gate=(
                ScenarioGateExpectation.from_dict(
                    _mapping(raw["gate"], "scenario.expect.gate")
                )
                if "gate" in raw
                else None
            ),
            commit_count=commit_count,
            remaining_agent_events=remaining,
            audit_contains=audit,
            agent_order=agent_order,
            validation_counts=validation_counts,
        )
@dataclass(frozen=True)
class DryRunScenario:
    name: str
    agent_events: tuple[ScriptedAgentEvent, ...]
    changes: tuple[ScriptedChange, ...]
    validations: tuple[ScriptedValidation, ...] = ()
    commits: tuple[ScriptedCommit, ...] = ()
    test_changes: tuple[ScriptedTestChange, ...] = ()
    clock_start: datetime = datetime(2026, 1, 1, tzinfo=timezone.utc)
    interrupt_on_sleep: int | None = None
    initial: ScriptedInitialState = ScriptedInitialState()
    context: ScriptedContext = ScriptedContext()
    expect: ScenarioExpectations | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("dry-run scenario name must not be empty")
        if self.interrupt_on_sleep is not None and self.interrupt_on_sleep < 1:
            raise ValueError("interrupt_on_sleep must be 1-based")

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> DryRunScenario:
        _require_exact_keys(
            raw,
            {"version", "name", "agent_events", "changes"},
            {
                "validations", "commits", "test_changes",
                "clock_start", "interrupt_on_sleep",
                "initial", "context", "expect",
            },
            "scenario",
        )
        if raw["version"] != SCENARIO_VERSION:
            raise DryRunScenarioError(
                f"scenario version must be {SCENARIO_VERSION}, got {raw['version']!r}"
            )
        agent_raw = raw["agent_events"]
        changes_raw = raw["changes"]
        validations_raw = raw.get("validations", [])
        commits_raw = raw.get("commits", [])
        test_changes_raw = raw.get("test_changes", [])
        if not isinstance(agent_raw, list) or not isinstance(changes_raw, list):
            raise DryRunScenarioError("scenario agent_events and changes must be arrays")
        if not isinstance(validations_raw, list):
            raise DryRunScenarioError("scenario validations must be an array")
        if not isinstance(commits_raw, list) or not isinstance(test_changes_raw, list):
            raise DryRunScenarioError("scenario commits and test_changes must be arrays")
        interrupt = raw.get("interrupt_on_sleep")
        if interrupt is not None:
            interrupt = _positive_int(interrupt, "scenario.interrupt_on_sleep")
        try:
            return cls(
                name=_string(raw["name"], "scenario.name"),
                agent_events=tuple(
                    ScriptedAgentEvent.from_dict(_mapping(item, f"agent_events[{index}]"), index)
                    for index, item in enumerate(agent_raw)
                ),
                changes=tuple(
                    ScriptedChange.from_dict(_mapping(item, f"changes[{index}]"), index)
                    for index, item in enumerate(changes_raw)
                ),
                validations=tuple(
                    ScriptedValidation.from_dict(
                        _mapping(item, f"validations[{index}]"), index
                    )
                    for index, item in enumerate(validations_raw)
                ),
                commits=tuple(
                    ScriptedCommit.from_dict(
                        _mapping(item, f"commits[{index}]"), index
                    )
                    for index, item in enumerate(commits_raw)
                ),
                test_changes=tuple(
                    ScriptedTestChange.from_dict(
                        _mapping(item, f"test_changes[{index}]"), index
                    )
                    for index, item in enumerate(test_changes_raw)
                ),
                clock_start=_timestamp(
                    raw.get("clock_start", "2026-01-01T00:00:00Z"),
                    "scenario.clock_start",
                ),
                interrupt_on_sleep=interrupt,
                initial=ScriptedInitialState.from_dict(
                    _mapping(raw.get("initial", {}), "scenario.initial")
                ),
                context=ScriptedContext.from_dict(
                    _mapping(raw.get("context", {}), "scenario.context")
                ),
                expect=(
                    ScenarioExpectations.from_dict(
                        _mapping(raw["expect"], "scenario.expect")
                    )
                    if "expect" in raw
                    else None
                ),
            )
        except ValueError as exc:
            raise DryRunScenarioError(str(exc)) from exc


def load_dry_run_scenario(path: Path) -> DryRunScenario:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DryRunScenarioError(f"cannot read dry-run scenario {path}: {exc}") from exc
    return DryRunScenario.from_dict(_mapping(raw, "scenario"))


@dataclass
class ScriptedClock:
    current: datetime
    interrupt_on_sleep: int | None = None
    sleeps: list[float] = field(default_factory=list)
    heartbeats: list[str] = field(default_factory=list)

    def now(self) -> datetime:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        if self.interrupt_on_sleep == len(self.sleeps):
            raise ScriptedInterruption("scripted interruption during quota wait")
        self.current += timedelta(seconds=seconds)


@dataclass
class ScriptedWorkflowDriver:
    scenario: DryRunScenario
    calls: list[str] = field(default_factory=list)
    checkpoints: list[WorkflowState] = field(default_factory=list)
    checkpoint_histories: list[WorkflowHistory] = field(default_factory=list)
    validation_counts: dict[str, int] = field(default_factory=dict)
    commit_requests: list[WorkflowCommitRequest] = field(default_factory=list)
    codex_invocations: list[CodexInvocation] = field(default_factory=list)
    reviewer_invocations: list[ReviewerInvocation] = field(default_factory=list)
    agent_invocations: list[object] = field(default_factory=list)
    durable_findings: tuple[FindingRecord, ...] = ()
    active_state: WorkflowState | None = None
    structured_events: list[tuple[str, object]] = field(default_factory=list)
    convergence_evaluations: list[SliceConvergenceEvaluation] = field(
        default_factory=list
    )
    _agent_index: int = 0
    _validation_index: int = 0
    _commit_index: int = 0
    _active_identity: tuple[int, int] | None = None
    _change_positions: dict[tuple[int, int], int] = field(default_factory=dict)

    def bind_work_unit(self, state: WorkflowState) -> None:
        self.active_state = state
        self._active_identity = (
            state.current_work_unit_id,
            state.current_work_unit.request_sequence,
        )

    def authoritative_native_findings(
        self, state: WorkflowState, findings: tuple[FindingRecord, ...]
    ) -> tuple[FindingRecord, ...]:
        """Provide the provider-free replay boundary used by native dry-runs."""
        _ = state
        return self.durable_findings or findings

    def evaluate_slice_finding_convergence(
        self,
        state: WorkflowState,
        *,
        round_number: int,
    ) -> SliceConvergenceEvaluation:
        """Consume an explicitly scripted dormant E5 decision."""

        self.structured_events.append(
            ("slice-convergence", (state.current_work_unit_id, round_number))
        )
        if not self.convergence_evaluations:
            if self.scenario.name not in {
                "progressive-correction",
                "stalled-correction",
                "s5-long-run-v1",
                "s5-long-run-independent-v1",
            }:
                raise DryRunScenarioError(
                    "scripted Slice convergence has no record-derived evaluation"
                )
            phase = (
                SliceReviewPhase.DISCOVERY
                if round_number == 1
                else SliceReviewPhase.CONVERGENCE
            )
            progress = not (
                self.scenario.name == "stalled-correction" and round_number > 1
            )
            return SliceConvergenceEvaluation(
                phase=phase,
                cohort_finding_ids=(),
                newly_opened_finding_ids=("C-01",) if round_number == 1 else (),
                closed_local_finding_ids=("C-01",) if progress and round_number > 1 else (),
                forwarded_local_finding_ids=(),
                attested_remediation_finding_ids=(),
                progress_made=progress,
                reason=(
                    "the scripted record chain contains a convergence transition"
                    if progress
                    else "the scripted record chain contains no convergence transition"
                ),
            )
        return self.convergence_evaluations.pop(0)

    def carry_forward_native_findings(
        self, state: WorkflowState, findings: tuple[FindingRecord, ...]
    ) -> tuple[FindingRecord, ...]:
        """Carry the scripted ledger unchanged across dry-run work units."""
        _ = state
        return self.durable_findings or findings

    def persist_native_codex_contract(
        self,
        output: NativeAgentCodexOutput,
        request_sequence: int,
        previous_findings: tuple[FindingRecord, ...],
    ) -> None:
        """Emulate record replay by retaining lines absent from a narrowed request."""
        ledger = {item.finding_id: item for item in self.durable_findings or previous_findings}
        ledger.update({item.finding_id: item for item in output.result.findings})
        self.durable_findings = tuple(
            ledger[key] for key in sorted(ledger, key=finding_id_sort_key)
        )
        self.structured_events.append(
            ("native-codex", (output, request_sequence, previous_findings))  # allowlist:provider
        )

    def persist_native_review_contract(
        self,
        output: NativeAgentReviewOutput,
        fingerprint: str,
        round_number: int,
        request_sequence: int,
        previous_findings: tuple[FindingRecord, ...],
    ) -> None:
        """Mirror the complete reviewer result as the scripted durable ledger."""
        _ = (fingerprint, round_number, request_sequence)
        ledger = {item.finding_id: item for item in self.durable_findings or previous_findings}
        ledger.update({item.finding_id: item for item in output.result.findings})
        self.durable_findings = tuple(
            ledger[key] for key in sorted(ledger, key=finding_id_sort_key)
        )
        self.structured_events.append(
            (
                "native-review",
                (
                    output,
                    fingerprint,
                    round_number,
                    request_sequence,
                    previous_findings,
                ),
            )
        )

    def _consume_agent(
        self,
        *,
        role: AgentRole,
        work_unit_id: int,
        request_sequence: int,
        step: WorkflowStep,
    ) -> Mapping[str, object]:
        if self._agent_index >= len(self.scenario.agent_events):
            raise DryRunScenarioError(
                "missing scripted response for "
                f"{role.value}/{work_unit_id}/{request_sequence}/{step.value}"
            )
        event = self.scenario.agent_events[self._agent_index]
        expected = (role, work_unit_id, request_sequence, step)
        actual = (
            event.role,
            event.work_unit_id,
            event.request_sequence,
            event.step,
        )
        if actual != expected:
            raise DryRunScenarioError(
                "scripted role order mismatch: expected "
                f"{role.value}/{work_unit_id}/{request_sequence}/{step.value}, got "
                f"{event.role.value}/{event.work_unit_id}/"
                f"{event.request_sequence}/{event.step.value}"
            )
        self._agent_index += 1
        if role is AgentRole.CODEX:
            self._active_identity = (work_unit_id, request_sequence)
        self.calls.append(
            f"agent:{role.value}:work-unit-{work_unit_id}:"
            f"request-{request_sequence}:{step.value}"
        )
        if event.failure is not None:
            raise event.failure.to_exception(
                role, f"dry-{self.scenario.name}-{self._agent_index:03d}"
            )
        assert event.output is not None
        return event.output

    def invoke_codex(self, invocation: CodexInvocation) -> NativeAgentCodexOutput:
        self.codex_invocations.append(invocation)
        self.agent_invocations.append(invocation)
        document = self._consume_agent(
            role=AgentRole.CODEX,
            work_unit_id=invocation.work_unit_id,
            request_sequence=invocation.request_sequence,
            step=invocation.step,
        )
        if invocation.native_request is None:
            raise DryRunScenarioError("scripted Codex event has no native request")
        document = dict(document)
        if document.get("request_id") == "$BOUND_REQUEST_ID":
            document["request_id"] = invocation.native_request.bound_context.request_id
        validate_native_codex_provider_response(document, invocation.native_request)
        canonical = canonical_native_codex_json(document)
        return NativeAgentCodexOutput(
            result=parse_bound_native_codex_contract_result(
                document, invocation.native_request.bound_context
            ),
            canonical_json=canonical,
            request_id=invocation.native_request.bound_context.request_id,
            response_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )

    def recover_pending_native_codex(  # allowlist:provider -- canonical capability
        self,
        invocation: CodexInvocation,  # allowlist:provider -- typed boundary
        contract: CodexStepContract,  # allowlist:provider -- typed boundary
        history: WorkflowHistory,
    ) -> NativeAgentCodexOutput | None:  # allowlist:provider -- typed boundary
        self.structured_events.append(
            ("recover-native-codex", (invocation, contract, history))  # allowlist:provider
        )
        return None

    def invoke_reviewer(self, invocation: ReviewerInvocation) -> NativeAgentReviewOutput:
        self.reviewer_invocations.append(invocation)
        self.agent_invocations.append(invocation)
        document = self._consume_agent(
            role=invocation.reviewer,
            work_unit_id=invocation.work_unit_id,
            request_sequence=invocation.request_sequence,
            step=invocation.step,
        )
        if invocation.native_request is None:
            raise DryRunScenarioError("scripted reviewer event has no native request")
        document = json.loads(json.dumps(document))
        if document.get("request_id") == "$BOUND_REQUEST_ID":
            document["request_id"] = invocation.native_request.bound_context.request_id
        routes = document.get("responsibility_routes")
        if isinstance(routes, list):
            for route in routes:
                if not isinstance(route, dict):
                    continue
                responsibility = route.get("responsibility")
                if (
                    isinstance(responsibility, dict)
                    and responsibility.get("target_run_id") == "$BOUND_RUN_ID"
                ):
                    responsibility["target_run_id"] = (
                        invocation.native_request.bound_context.context.run_id
                    )
        validate_native_review_provider_response(document, invocation.native_request)
        canonical = canonical_native_review_json(document)
        return NativeAgentReviewOutput(
            result=parse_bound_native_contract_result(
                document, invocation.native_request.bound_context
            ),
            canonical_json=canonical,
            request_id=invocation.native_request.bound_context.request_id,
            context=invocation.native_request.bound_context.context,
        )

    def recover_pending_native_reviewer(
        self,
        invocation: ReviewerInvocation,
        contract: StepContract,
        history: WorkflowHistory,
    ) -> NativeAgentReviewOutput | None:
        self.structured_events.append(
            ("recover-native-reviewer", (invocation, contract, history))
        )
        return None

    def recover_pending_native_reviewer_before_policy(
        self,
        state: WorkflowState,
        context: WorkflowContext,
        history: WorkflowHistory,
    ) -> PersistedNativeReviewerReplay | None:
        self.structured_events.append(
            ("recover-native-reviewer-before-policy", (state, context, history))
        )
        return None

    def collect_changes(self, start_commit: str) -> WorkflowChanges:
        if self._active_identity is None:
            raise DryRunScenarioError("scripted change collection has no active work unit")
        matches = tuple(
            item
            for item in self.scenario.changes
            if (item.work_unit_id, item.request_sequence) == self._active_identity
        )
        if not matches:
            raise DryRunScenarioError(
                f"missing scripted changes for work unit/round {self._active_identity}"
            )
        position = self._change_positions.get(self._active_identity, 0)
        match = matches[min(position, len(matches) - 1)]
        if position < len(matches) - 1:
            self._change_positions[self._active_identity] = position + 1
        if match.start_commit != start_commit:
            raise DryRunScenarioError(
                f"scripted start commit {match.start_commit} differs from {start_commit}"
            )
        self.calls.append(
            f"changes:{match.work_unit_id}:request-{match.request_sequence}"
        )
        return match.workflow_changes

    def collect_correction_delta(
        self, previous_fingerprint: str, current_fingerprint: str
    ) -> str:
        self.calls.append(f"delta:{previous_fingerprint[:8]}:{current_fingerprint[:8]}")
        if self._active_identity is None:
            raise DryRunScenarioError(
                "scripted correction delta has no active work unit"
            )
        match = next(
            (
                item
                for item in self.scenario.changes
                if (item.work_unit_id, item.request_sequence)
                == self._active_identity
                and item.fingerprint == current_fingerprint
            ),
            None,
        )
        if match is None:
            raise DryRunScenarioError(
                "scripted correction delta lacks its fingerprint-bound change"
            )
        return match.full_diff

    def detect_test_changes(
        self, changes: WorkflowChanges, patterns: tuple[str, ...]
    ) -> TestChangeEvidence | None:
        _ = patterns
        match = next(
            (
                item
                for item in self.scenario.test_changes
                if item.diff_fingerprint == changes.fingerprint
            ),
            None,
        )
        return (
            TestChangeEvidence(match.paths, match.test_fingerprint)
            if match is not None
            else None
        )

    def validate(
        self, changes: WorkflowChanges, request: ValidationRequest
    ) -> ValidationAttestation:
        if self._validation_index >= len(self.scenario.validations):
            raise ValidationExecutionError(
                f"missing scripted validation for {changes.fingerprint}"
            )
        event = self.scenario.validations[self._validation_index]
        if event.fingerprint != changes.fingerprint:
            raise DryRunScenarioError(
                f"scripted validation expected {event.fingerprint}, got {changes.fingerprint}"
            )
        self._validation_index += 1
        self.validation_counts[changes.fingerprint] = (
            self.validation_counts.get(changes.fingerprint, 0) + 1
        )
        self.calls.append(f"validation:{changes.fingerprint[:8]}:{event.status}")
        if event.status == "missing":
            raise ValidationExecutionError("scripted validation is missing")
        records = tuple(
            ValidationRecord(
                ValidationStatus.FAIL if event.status == "fail" else ValidationStatus.PASS,
                command,
                1 if event.status == "fail" else 0,
                f"scripted {event.status}",
            )
            for command in request.expected_commands
        )
        if event.status == "incomplete":
            records = records[:-1]
        recorded_commands = {record.command for record in records}
        captures = tuple(
            ValidationCapture(
                command,
                (
                    "missing"
                    if command not in recorded_commands
                    else "fail"
                    if event.status == "fail"
                    else "pass"
                ),
                (
                    -1
                    if command not in recorded_commands
                    else 1
                    if event.status == "fail"
                    else 0
                ),
                "" if command not in recorded_commands else f"scripted {event.status}",
                "scripted validation record is missing"
                if command not in recorded_commands
                else "",
                "" if command not in recorded_commands else f"scripted {event.status}",
            )
            for command in request.expected_commands
        )
        attestation_fingerprint = event.attestation_fingerprint or changes.fingerprint
        return ValidationAttestation(
            attestation_id=validation_attestation_id(request),
            diff_fingerprint=attestation_fingerprint,
            expected_commands=request.expected_commands,
            records=records,
            output_digest=validation_output_digest(captures),
            summary=f"scripted validation {event.status}",
            command_specs=tuple(command.command_spec for command in request.commands),
            content_captures=captures,
        )

    def validate_plan(
        self,
        changes: WorkflowChanges,
        *,
        work_plan_path: str | None,
        scope_patterns: tuple[str, ...],
        plan_only: bool,
    ) -> ValidationAttestation:
        _ = (work_plan_path, scope_patterns, plan_only)
        return self.validate(
            changes,
            ValidationRequest(
                diff_fingerprint=changes.fingerprint,
                commands=(ValidationCommand(argv=("internal:plan-contract",)),),
            ),
        )

    def recover_pending_validation_attestation(
        self,
        fingerprint: str,
        expected_commands: tuple[str, ...],
        attestation_id: str,
    ) -> ValidationAttestation | None:
        self.structured_events.append(
            (
                "recover-validation-attestation",
                (fingerprint, expected_commands, attestation_id),
            )
        )
        return None

    def commit_slice(self, request: WorkflowCommitRequest) -> str:
        if self._commit_index >= len(self.scenario.commits):
            raise DryRunScenarioError(
                f"missing scripted commit for slice {request.slice_id}"
            )
        event = self.scenario.commits[self._commit_index]
        if (event.slice_id, event.fingerprint) != (
            request.slice_id,
            request.fingerprint,
        ):
            raise DryRunScenarioError("scripted commit identity differs from authorization")
        self._commit_index += 1
        self.commit_requests.append(request)
        self.calls.append(f"commit:{request.slice_id}:{request.fingerprint[:8]}")
        return event.commit_ref

    def checkpoint(self, state: WorkflowState, history: WorkflowHistory) -> None:
        self.active_state = state
        self.checkpoints.append(state)
        self.checkpoint_histories.append(history)
        self.calls.append(
            f"checkpoint:{state.current_work_unit_id}:{state.current_step.value}:"
            f"{state.current_work_unit.status.value}"
        )

    def persist_gate_decision(
        self, work_unit_id: int, decision: GateDecisionRecord
    ) -> None:
        self.structured_events.append(
            ("gate-decision", (work_unit_id, decision))
        )

    def persist_gate_transition(self, state: WorkflowState) -> None:
        self.structured_events.append(("gate-transition", state))

    def persist_invocation_failure(
        self, payload: InvocationFailurePayload
    ) -> None:
        self.structured_events.append(("invocation-failure", payload))

    def persist_review_packet(self, packet: ReviewPacket) -> None:
        self.structured_events.append(("review-packet", packet))

    def persist_validation_request(self, request: ValidationRequest) -> None:
        self.structured_events.append(("validation-request", request))

    def persist_validation_attestation(
        self, attestation: ValidationAttestation
    ) -> None:
        self.structured_events.append(("validation-attestation", attestation))

    def persist_finding_acceptance_measurement(
        self,
        finding: FindingRecord,
        measurement: FindingAcceptanceMeasurement,
    ) -> None:
        self.structured_events.append(
            ("finding-acceptance-measurement", (finding, measurement))
        )

    @property
    def remaining_agent_events(self) -> int:
        return len(self.scenario.agent_events) - self._agent_index

    def audit_document(
        self, result: WorkflowRunResult, clock: ScriptedClock
    ) -> str:
        audit_events: list[dict[str, object]] = []
        for event in result.history.events:
            if isinstance(event, ValidationAuditEvent):
                audit_events.append(
                    {
                        "event_id": event.event_id,
                        "type": "validation",
                        "attestation_id": event.attestation.attestation_id,
                        "fingerprint": event.attestation.diff_fingerprint,
                        "status": event.attestation.status.value,
                    }
                )
            elif isinstance(event, ReviewAuditEvent):
                audit_events.append(
                    {
                        "event_id": event.event_id,
                        "type": "review",
                        "reviewer": event.result.reviewer.value,
                        "approval": event.result.approval,
                        "finding_ids": [
                            finding.finding_id for finding in event.result.findings
                        ],
                    }
                )
        payload = {
            "scenario": self.scenario.name,
            "exit_code": result.exit_code,
            "state": result.state.to_dict(),
            "calls": self.calls,
            "validation_counts": self.validation_counts,
            "commit_refs": [item.commit_ref for item in self.scenario.commits[: self._commit_index]],
            "heartbeats": clock.heartbeats,
            "sleeps": clock.sleeps,
            "audit_events": audit_events,
            "commit_authorized": result.completed and result.commit_ref is not None,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)


@dataclass(frozen=True)
class ScriptedRunReport:
    result: WorkflowRunResult
    calls: tuple[str, ...]
    validation_counts: Mapping[str, int]
    audit_document: str
    heartbeats: tuple[str, ...]
    sleeps: tuple[float, ...]
    remaining_agent_events: int
    checkpoints: tuple[WorkflowState, ...] = ()
    checkpoint_histories: tuple[WorkflowHistory, ...] = ()
    agent_invocations: tuple[object, ...] = ()
    reviewer_invocations: tuple[ReviewerInvocation, ...] = ()


@dataclass(frozen=True)
class ResilienceEvidenceRow:
    """Stable provider-free evidence reference used by the Slice-3 report."""

    scenario_id: str
    dimension: str
    expected_result: str
    evidence_test: str

    def __post_init__(self) -> None:
        for value, label in (
            (self.scenario_id, "scenario_id"),
            (self.expected_result, "expected_result"),
            (self.evidence_test, "evidence_test"),
        ):
            if not value.strip():
                raise ValueError(f"resilience evidence {label} must not be empty")
        if self.dimension not in {"security", "availability", "autonomy"}:
            raise ValueError(
                "resilience evidence dimension must be security, availability, or autonomy"
            )


def render_resilience_evidence(rows: tuple[ResilienceEvidenceRow, ...]) -> str:
    """Render byte-stable evidence without timing, token, cost, or provider data."""

    identities = tuple(item.scenario_id for item in rows)
    if len(identities) != len(set(identities)):
        raise DryRunScenarioError("resilience evidence scenario ids must be unique")
    order = {"security": 0, "availability": 1, "autonomy": 2}
    sorted_rows = tuple(
        sorted(rows, key=lambda item: (order[item.dimension], item.scenario_id))
    )
    lines = ["# Providerfreier Resilienznachweis", ""]
    for dimension in ("security", "availability", "autonomy"):
        lines.extend((f"## {dimension.title()}", ""))
        dimension_rows = tuple(
            item for item in sorted_rows if item.dimension == dimension
        )
        if not dimension_rows:
            lines.append("- Kein Szenario registriert.")
        else:
            for item in dimension_rows:
                lines.append(
                    f"- `{item.scenario_id}`: {item.expected_result} "
                    f"(Nachweis: `{item.evidence_test}`)"
                )
        lines.append("")
    return "\n".join(lines)


@dataclass
class ScriptedWorkflowSession:
    scenario: DryRunScenario
    driver_factory: Callable[[DryRunScenario], ScriptedWorkflowDriver] | None = None
    driver: ScriptedWorkflowDriver = field(init=False)
    clock: ScriptedClock = field(init=False)
    engine: WorkflowEngine = field(init=False)

    def __post_init__(self) -> None:
        self.driver = (
            ScriptedWorkflowDriver(self.scenario)
            if self.driver_factory is None
            else self.driver_factory(self.scenario)
        )
        self.clock = ScriptedClock(
            self.scenario.clock_start, self.scenario.interrupt_on_sleep
        )
        self.engine = WorkflowEngine(
            self.driver,
            now_fn=self.clock.now,
            sleep_fn=self.clock.sleep,
            heartbeat_fn=self.clock.heartbeats.append,
        )

    def run(
        self,
        state: WorkflowState,
        context: WorkflowContext,
        history: WorkflowHistory | None = None,
    ) -> ScriptedRunReport:
        self.driver.bind_work_unit(state)
        try:
            result = self.engine.run_current_work_unit(state, context, history)
        except ScriptedInterruption:
            if not self.driver.checkpoints:
                raise DryRunScenarioError(
                    "scripted interruption occurred before any resumable checkpoint"
                )
            result = WorkflowRunResult(
                self.driver.checkpoints[-1], self.driver.checkpoint_histories[-1]
            )
            self.driver.calls.append("interrupt:quota-wait")
        return ScriptedRunReport(
            result=result,
            calls=tuple(self.driver.calls),
            validation_counts=dict(self.driver.validation_counts),
            audit_document=self.driver.audit_document(result, self.clock),
            heartbeats=tuple(self.clock.heartbeats),
            sleeps=tuple(self.clock.sleeps),
            remaining_agent_events=self.driver.remaining_agent_events,
            checkpoints=tuple(self.driver.checkpoints),
            checkpoint_histories=tuple(self.driver.checkpoint_histories),
            agent_invocations=tuple(self.driver.agent_invocations),
            reviewer_invocations=tuple(self.driver.reviewer_invocations),
        )

    def report(self, result: WorkflowRunResult) -> ScriptedRunReport:
        """Project a result from the same scripted session without rerunning it."""
        return ScriptedRunReport(
            result=result,
            calls=tuple(self.driver.calls),
            validation_counts=dict(self.driver.validation_counts),
            audit_document=self.driver.audit_document(result, self.clock),
            heartbeats=tuple(self.clock.heartbeats),
            sleeps=tuple(self.clock.sleeps),
            remaining_agent_events=self.driver.remaining_agent_events,
            checkpoints=tuple(self.driver.checkpoints),
            checkpoint_histories=tuple(self.driver.checkpoint_histories),
            agent_invocations=tuple(self.driver.agent_invocations),
            reviewer_invocations=tuple(self.driver.reviewer_invocations),
        )


def build_scenario_state(
    scenario: DryRunScenario, *, task_file: Path
) -> WorkflowState:
    if not scenario.changes:
        raise DryRunScenarioError("scenario requires at least one scripted change set")
    first = scenario.changes[0]
    first_slice_start_commit = (
        scenario.initial.first_slice_start_commit or first.start_commit
    )
    task_digest = hashlib.sha256(
        task_file.read_text(encoding="utf-8").encode("utf-8")
    ).hexdigest()
    branch_base = (
        scenario.initial.family_binding.family_base_commit
        if scenario.initial.family_binding is not None
        else first.start_commit
    )
    state = init_workflow_state(
        run_id=f"dry-{scenario.name}",
        task_file=str(task_file.resolve()),
        branch=scenario.initial.branch,
        branch_base=branch_base,
        first_slice_start_commit=first_slice_start_commit,
        slice_count=scenario.initial.slice_count,
        task_digest=task_digest,
        execution_mode=scenario.initial.execution_mode,
        task_scope_patterns=scenario.initial.scope_paths or first.paths,
        work_plan_path=scenario.initial.work_plan_path,
        approved_plan_commit=scenario.initial.approved_plan_commit,
        target_branch=scenario.initial.branch,
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
        family_binding=scenario.initial.family_binding,
        timestamp=scenario.clock_start.isoformat(),
    )
    if scenario.initial.planned_slices:
        state = state.bind_slice_plan(
            scenario.initial.planned_slices,
            first_start_commit=first_slice_start_commit,
            updated_at=scenario.clock_start.isoformat(),
        )
    if scenario.initial.kind in {
        WorkUnitKind.PLAN,
        WorkUnitKind.BRANCH_DISCOVERY,
    }:
        return state
    state = state.complete_current_work_unit(updated_at=scenario.clock_start.isoformat())
    state = state.start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
        updated_at=scenario.clock_start.isoformat(),
    )
    if first.work_unit_id != state.current_work_unit_id:
        raise DryRunScenarioError(
            f"first scripted work unit must be {state.current_work_unit_id} for slice mode"
        )
    planned_first = next(
        (item for item in scenario.initial.planned_slices if item.slice_id == 1),
        None,
    )
    slice_scope = (
        planned_first.scope_paths
        if planned_first is not None
        else scenario.initial.scope_paths or first.paths
    )
    state = state.bind_current_slice_git_boundary(
        start_commit=first.start_commit,
        scope_paths=slice_scope,
        start_fingerprint="0" * 64,
        updated_at=scenario.clock_start.isoformat(),
    )
    if scenario.initial.max_codex_returns != state.current_work_unit.max_codex_returns:
        current = replace(
            state.current_work_unit,
            max_codex_returns=scenario.initial.max_codex_returns,
        )
        state = replace(
            state,
            work_units=tuple(
                current if unit.work_unit_id == current.work_unit_id else unit
                for unit in state.work_units
            ),
        )
    return state


def build_scenario_context(scenario: DryRunScenario) -> WorkflowContext:
    configured = scenario.context
    planned = scenario.initial.planned_slices
    approved_plan_text = None
    slice_summary = "Exercise configured positive and negative workflow gates."
    if planned:
        slice_summary = planned[0].summary
        sections = ["# Provider-free approved implementation plan"]
        for item in planned:
            sections.extend(
                (
                    f"### Slice {item.slice_id} - {item.summary}",
                    "",
                    "**Ziel**",
                    "",
                    item.summary,
                    "",
                    "**\u0041kzeptanzkriterien**",
                    "",
                    f"- The provider-free workflow verifies {item.summary}.",
                    "",
                    "**Exakter Änderungspfad**",
                    "",
                    *(f"- `{path}`" for path in item.scope_paths),
                )
            )
        approved_plan_text = "\n".join(sections) + "\n"
    return WorkflowContext(
        assignment=f"Scripted dry-run scenario: {scenario.name}",
        distilled_plan="Use the real v3 state machine with scripted backends only.",
        slice_summary=slice_summary,
        expected_test_files=configured.expected_test_files,
        test_changes_approved=configured.test_changes_approved,
        manual_slice_gate=configured.manual_slice_gate,
        current_branch=configured.current_branch or scenario.initial.branch,
        red_state_followup_slice=configured.red_state_followup_slice,
        quota_wait_policy=configured.quota_wait_policy,
        transient_retry_policy=configured.transient_retry_policy,
        task_scope_patterns=scenario.initial.scope_paths or scenario.changes[0].paths,
        require_slice_plan=scenario.initial.kind is WorkUnitKind.PLAN,
        plan_only=scenario.initial.execution_mode == "PLAN_ONLY",
        work_plan_path=scenario.initial.work_plan_path,
        approved_plan_text=approved_plan_text,
    )


def verify_scenario_expectations(
    report: ScriptedRunReport, expected: ScenarioExpectations
) -> None:
    failures: list[str] = []
    unit = report.result.state.current_work_unit
    if report.result.exit_code != expected.exit_code:
        failures.append(
            f"exit_code expected {expected.exit_code}, got {report.result.exit_code}"
        )
    if expected.status is not None and unit.status.value != expected.status:
        failures.append(f"status expected {expected.status}, got {unit.status.value}")
    if expected.step is not None and report.result.state.current_step is not expected.step:
        failures.append(
            f"step expected {expected.step.value}, got {report.result.state.current_step.value}"
        )
    if expected.gate_reason is not None and unit.gate.reason is not expected.gate_reason:
        failures.append(
            f"gate expected {expected.gate_reason.value}, got {unit.gate.reason.value}"
        )
    if expected.gate is not None:
        gate = unit.gate
        actual_rule_id = _gate_rule_id(gate.reason, gate.detail)
        actual_kind = _gate_kind(gate.status, gate.reason, gate.fingerprint)
        actual = (
            gate.status.value,
            gate.reason,
            actual_rule_id,
            actual_kind,
            gate.fingerprint,
            gate.paths,
            gate.resume_step,
        )
        wanted = (
            expected.gate.status.value,
            expected.gate.reason,
            expected.gate.rule_id,
            expected.gate.kind,
            expected.gate.fingerprint,
            expected.gate.paths,
            expected.gate.resume_step,
        )
        if actual != wanted:
            failures.append(f"gate identity expected {wanted!r}, got {actual!r}")
    if expected.commit_count is not None:
        actual_commits = sum(call.startswith("commit:") for call in report.calls)
        if actual_commits != expected.commit_count:
            failures.append(
                f"commit_count expected {expected.commit_count}, got {actual_commits}"
            )
    if (
        expected.remaining_agent_events is not None
        and report.remaining_agent_events != expected.remaining_agent_events
    ):
        failures.append(
            "remaining_agent_events expected "
            f"{expected.remaining_agent_events}, got {report.remaining_agent_events}"
        )
    for needle in expected.audit_contains:
        if needle not in report.audit_document:
            failures.append(f"audit document does not contain {needle!r}")
    actual_agent_order = tuple(
        call for call in report.calls if call.startswith("agent:")
    )
    if expected.agent_order and actual_agent_order != expected.agent_order:
        failures.append(
            f"agent_order expected {expected.agent_order!r}, got {actual_agent_order!r}"
        )
    if expected.validation_counts and dict(report.validation_counts) != dict(
        expected.validation_counts
    ):
        failures.append(
            "validation_counts expected "
            f"{dict(expected.validation_counts)!r}, got {dict(report.validation_counts)!r}"
        )
    if failures:
        raise DryRunScenarioError(
            f"scenario expectation failed: {'; '.join(failures)}"
        )


def run_dry_run_scenario_file(
    path: Path, *, task_file: Path
) -> ScriptedRunReport:
    scenario = load_dry_run_scenario(path)
    report = run_scripted_work_unit(
        scenario=scenario,
        state=build_scenario_state(scenario, task_file=task_file),
        context=build_scenario_context(scenario),
    )
    if scenario.expect is not None:
        verify_scenario_expectations(report, scenario.expect)
    return report


def run_scripted_workflow(
    *, scenario: DryRunScenario, task_file: Path
) -> ScriptedRunReport:
    """Run plan/slices/final review as one provider-free workflow journey."""

    return _run_scripted_workflow(
        scenario=scenario,
        task_file=task_file,
        resume_scripted_interruptions=False,
    )


def run_scripted_workflow_resumable(
    *,
    scenario: DryRunScenario,
    task_file: Path,
    driver_factory: Callable[[DryRunScenario], ScriptedWorkflowDriver] | None = None,
    run_id: str | None = None,
) -> ScriptedRunReport:
    """Run one full journey and automatically resume scripted crash checkpoints.

    Only ``ScriptedInterruption`` is resumed. Policy and user gates still
    terminate normally, so this helper cannot turn a denied decision into an
    implicit approval.
    """

    return _run_scripted_workflow(
        scenario=scenario,
        task_file=task_file,
        resume_scripted_interruptions=True,
        driver_factory=driver_factory,
        run_id=run_id,
    )


def _run_scripted_workflow(
    *,
    scenario: DryRunScenario,
    task_file: Path,
    resume_scripted_interruptions: bool,
    driver_factory: Callable[[DryRunScenario], ScriptedWorkflowDriver] | None = None,
    run_id: str | None = None,
) -> ScriptedRunReport:
    session = ScriptedWorkflowSession(scenario, driver_factory)
    context = build_scenario_context(scenario)
    state = build_scenario_state(scenario, task_file=task_file)
    if run_id is not None:
        state = replace(state, run_id=run_id)

    def run_unit(
        current: WorkflowState, history: WorkflowHistory | None = None
    ) -> ScriptedRunReport:
        while True:
            before = sum(call.startswith("interrupt:") for call in session.driver.calls)
            report = session.run(current, context, history)
            after = sum(call.startswith("interrupt:") for call in session.driver.calls)
            if after == before or not resume_scripted_interruptions:
                return report
            current, history = report.result.state, report.result.history
            if current.current_work_unit.status not in {
                WorkUnitStatus.WAITING_FOR_QUOTA,
                WorkUnitStatus.WAITING_FOR_RETRY,
            }:
                raise DryRunScenarioError(
                    "scripted interruption did not preserve an automatic resume gate"
                )
            resume_at = current.current_work_unit.invocation_failures[-1].resume_at_utc
            if resume_at is None:
                raise DryRunScenarioError(
                    "automatic scripted interruption has no durable resume time"
                )
            session.clock.current = _timestamp(resume_at, "invocation resume_at_utc")
            current = current.resume_after_invocation_halt(
                updated_at=session.clock.current.isoformat()
            )
            session.driver.checkpoint(current, history)
            current = session.driver.active_state or current

    first = run_unit(state)
    if not first.result.completed:
        return first
    state = first.result.state
    if state.execution_mode in {"PLAN_ONLY", "BRANCH_DISCOVERY"}:
        return first
    planned_slices = state.planned_slices
    completed_slice = first if state.current_work_unit.kind is WorkUnitKind.SLICE else None
    completed_ids = {
        item.slice_id for item in state.slices if item.commit_ref is not None
    }
    for planned in (
        item for item in planned_slices if item.slice_id not in completed_ids
    ):
        next_work_unit_id = len(state.work_units) + 1
        change = next(
            item
            for item in scenario.changes
            if item.work_unit_id == next_work_unit_id
            and item.request_sequence == 1
        )
        state = state.start_work_unit(
            slice_id=planned.slice_id,
            kind=WorkUnitKind.SLICE,
            step=WorkflowStep.CODEX_IMPLEMENTATION,  # allowlist:provider
            slice_start_commit=None if planned.slice_id == 1 else change.start_commit,
        ).bind_current_slice_git_boundary(
            start_commit=change.start_commit,
            scope_paths=planned.scope_paths,
            start_fingerprint="0" * 64,
        )
        completed_slice = run_unit(state)
        if not completed_slice.result.completed:
            return completed_slice
        state = completed_slice.result.state
    if completed_slice is None:
        raise DryRunScenarioError("scripted workflow has no completed Slice")
    return completed_slice


def _scripted_unified_diff(paths: tuple[str, ...], change: str) -> str:
    """Render deterministic text changes in the Git diff form review packets require."""
    return "".join(
        f"diff --git a/{path} b/{path}\n"
        "index 1111111..2222222 100644\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1 @@\n"
        f"-before {change} in {path}\n"
        f"+after {change} in {path}\n"
        for path in paths
    )


def _scripted_change(
    work_unit_id: int,
    request_sequence: int,
    start_commit: str,
    fingerprint: str,
    paths: tuple[str, ...],
    change: str,
) -> ScriptedChange:
    return ScriptedChange(
        work_unit_id,
        request_sequence,
        start_commit,
        fingerprint,
        paths,
        _scripted_unified_diff(paths, change),
    )


def build_s5_plan_only_scenario() -> DryRunScenario:
    """Return S5's reviewed, commit-bound PLAN_ONLY half of the journey."""

    base, plan_commit, plan_fp = "a" * 40, "b" * 40, "1" * 64
    return DryRunScenario(
        name="s5-plan-only-v1",
        initial=ScriptedInitialState(
            kind=WorkUnitKind.PLAN,
            branch="feature/dry-run",
            slice_count=1,
            scope_paths=("docs/internal/s5-work-plan.md",),
            execution_mode="PLAN_ONLY",
            work_plan_path="docs/internal/s5-work-plan.md",
        ),
        agent_events=(
            ScriptedAgentEvent(
                AgentRole.CODEX,  # allowlist:provider
                1,
                1,
                WorkflowStep.CODEX_PLAN,  # allowlist:provider
                {
                    "schema_version": "native-agent-codex-result-v2",  # allowlist:provider
                    "request_id": "$BOUND_REQUEST_ID",
                    "result_type": "plan_result",
                    "ready": True,
                    "finding_dispositions": [],
                    "plan_treatments": [],
                    "plan_completion": "IMPLEMENTATION_REQUIRED",
                    "slice_plan": [
                        {
                            "slice_id": 1,
                            "summary": "Create the executable S5 work-plan artifact.",
                            "scope_paths": ["docs/internal/s5-work-plan.md"],
                            "acceptance_criteria": [
                                {
                                    "text": (
                                        "The executable S5 work-plan artifact is "
                                        "committed."
                                    ),
                                    "measured_against": "SOURCE",
                                }
                            ],
                        }
                    ],
                },
            ),
            ScriptedAgentEvent(
                AgentRole.CLAUDE,  # allowlist:provider
                1,
                1,
                WorkflowStep.CLAUDE_PLAN_REVIEW,  # allowlist:provider
                {
                    "schema_version": "native-agent-review-result-v2",
                    "result_type": "review_result",
                    "request_id": "$BOUND_REQUEST_ID",
                    "reviewer": "claude",  # allowlist:provider
                    "decision": "approved",
                    "new_findings": [],
                    "status_changes": [],
                    "reclassifications": [],
                    "responsibility_routes": [],
                    "plan_treatment_decisions": [],
                    "anchors": [],
                    "review_evidence": {
                        "dimensions": "plan contract, scope, failure paths, handoff",
                        "largest_residual_risk": "the commit binding changes before handoff",
                        "break_condition": "IMPLEMENT receives a foreign plan commit",
                    },
                    "pre_mortem": "A crash after the plan commit could duplicate the handoff.",
                },
            ),
        ),
        changes=(
            _scripted_change(
                1, 1, base, plan_fp,
                ("docs/internal/s5-work-plan.md",),
                "S5 work-plan artifact",
            ),
        ),
        validations=(ScriptedValidation(plan_fp),),
        commits=(ScriptedCommit(1, plan_fp, plan_commit),),
        clock_start=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )


def build_s5_long_run_scenario() -> DryRunScenario:
    """Return S5's IMPLEMENT correction, observation and resume journey."""

    base, commit_one, commit_two = "b" * 40, "c" * 40, "d" * 40
    slice_one_fp, slice_two_fp, correction_fp = (value * 64 for value in "234")

    def codex(  # allowlist:provider -- scripted native result factory
        result_type: str, *, dispositions: tuple[str, ...] = (), **fields: object
    ) -> dict[str, object]:
        return {
            "schema_version": "native-agent-codex-result-v2",  # allowlist:provider
            "request_id": "$BOUND_REQUEST_ID",
            "result_type": result_type,
            "ready": True,
            "finding_dispositions": [
                {
                    "finding_id": finding_id,
                    "decision": "accepted",
                    "rationale": f"The provider-free correction addresses {finding_id}.",
                    "responsibility_proposal": None,
                }
                for finding_id in dispositions
            ],
            **fields,
        }

    def review(
        *,
        approved: bool,
        observations: tuple[str, ...] = (),
        blockers: tuple[str, ...] = (),
        closed: tuple[str, ...] = (),
        routed: tuple[str, ...] = (),
    ) -> dict[str, object]:
        findings = [
            {
                "finding_id": finding_id,
                "finding_class": finding_class,
                "affected_paths": ["src/orchestrator.py"],
                "summary": f"Provider-free finding {finding_id}.",
                "acceptance_test": {
                    "kind": "prose",
                    "text": f"The long-run evidence closes {finding_id}.",
                },
            }
            for finding_class, identities in (
                ("OBSERVATION", observations),
                ("BLOCKER", blockers),
            )
            for finding_id in identities
        ]
        return {
            "schema_version": "native-agent-review-result-v2",
            "result_type": "review_result",
            "request_id": "$BOUND_REQUEST_ID",
            "reviewer": "claude",  # allowlist:provider -- persisted reviewer role
            "decision": "approved" if approved else "denied",
            "new_findings": findings,
            "status_changes": [
                {
                    "finding_id": finding_id,
                    "status": "CLOSED",
                    "rationale": f"The long-run evidence closes {finding_id}.",
                    "closure": {"kind": "fixed"},
                }
                for finding_id in closed
            ],
            "reclassifications": [],
            "responsibility_routes": [
                {
                    "finding_id": finding_id,
                    "responsibility": {
                        "responsibility_kind": "SLICE",
                        "target_run_id": "$BOUND_RUN_ID",
                        "approved_plan_commit": base,
                        "slice_id": "2",
                    },
                    "rationale": "The second planned Slice owns this finding.",
                }
                for finding_id in routed
            ],
            "plan_treatment_decisions": [],
            "anchors": [],
            "review_evidence": {
                "dimensions": "correctness, contracts, failure paths, security, resume",
                "largest_residual_risk": "a later transition drops carried findings",
                "break_condition": "resume changes the canonical finding ledger",
            },
            "pre_mortem": "A crash after a denied review repeats one physical effect.",
        }

    quota_failure = ScriptedFailure(
        AgentFailureKind.QUOTA,
        "usage cap reached; reset 2026-09-01T00:00:05+00:00",
        datetime(2026, 9, 1, tzinfo=timezone.utc),
        provider_data={
            "provider": "codex",  # allowlist:provider -- structured quota evidence
            "error_type": "usage_limit",
            "resets_at": "2026-09-01T00:00:05+00:00",
        },
    )
    return DryRunScenario(
        name="s5-long-run-v1",
        initial=ScriptedInitialState(
            kind=WorkUnitKind.SLICE,
            slice_count=2,
            scope_paths=("src/first.py", "src/second.py"),
            work_plan_path="docs/internal/s5-work-plan.md",
            approved_plan_commit=base,
            planned_slices=(
                PlannedSlice(1, "Implement the first provider-free Slice.", ("src/first.py",)),
                PlannedSlice(2, "Correct and close the carried finding.", ("src/second.py",)),
            ),
        ),
        agent_events=(
            ScriptedAgentEvent(
                AgentRole.CODEX, 2, 1, WorkflowStep.CODEX_IMPLEMENTATION,  # allowlist:provider
                codex("implementation_result", test_files=[]),  # allowlist:provider
            ),
            ScriptedAgentEvent(
                AgentRole.CLAUDE, 2, 1, WorkflowStep.CLAUDE_SLICE_REVIEW,  # allowlist:provider
                review(
                    approved=True,
                    observations=("C-01",),
                    routed=("C-01",),
                ),
            ),
            ScriptedAgentEvent(
                AgentRole.CODEX, 3, 1, WorkflowStep.CODEX_IMPLEMENTATION,  # allowlist:provider
                failure=quota_failure,
            ),
            ScriptedAgentEvent(
                AgentRole.CODEX, 3, 2, WorkflowStep.CODEX_IMPLEMENTATION,  # allowlist:provider
                output=codex(  # allowlist:provider
                    "implementation_result", dispositions=("C-01",), test_files=[]
                ),
            ),
            ScriptedAgentEvent(
                AgentRole.CLAUDE, 3, 2, WorkflowStep.CLAUDE_SLICE_REVIEW,  # allowlist:provider
                review(approved=False, blockers=("C-02",), closed=("C-01",)),
            ),
            ScriptedAgentEvent(
                AgentRole.CODEX, 3, 3, WorkflowStep.CODEX_CORRECTION,  # allowlist:provider
                codex("correction_result", dispositions=("C-02",), test_files=[]),  # allowlist:provider
            ),
            ScriptedAgentEvent(
                AgentRole.CLAUDE, 3, 3, WorkflowStep.CLAUDE_SLICE_REVIEW,  # allowlist:provider
                review(approved=True, closed=("C-02",)),
            ),
        ),
        changes=(
            _scripted_change(
                2, 1, base, slice_one_fp, ("src/first.py",), "slice one"
            ),
            _scripted_change(
                3, 1, commit_one, slice_two_fp, ("src/second.py",), "slice two"
            ),
            # The resumed implementation is collected once for its Codex return
            # and once for review before the correction delta becomes visible.
            _scripted_change(
                3, 2, commit_one, slice_two_fp, ("src/second.py",), "slice two"
            ),
            _scripted_change(
                3, 2, commit_one, slice_two_fp, ("src/second.py",), "slice two"
            ),
            _scripted_change(
                3, 3, commit_one, correction_fp, ("src/second.py",), "correction"
            ),
        ),
        validations=tuple(
            ScriptedValidation(fingerprint)
            for fingerprint in (
                slice_one_fp,
                slice_two_fp,
                correction_fp,
            )
        ),
        commits=(
            ScriptedCommit(1, slice_one_fp, commit_one),
            ScriptedCommit(2, correction_fp, commit_two),
        ),
        clock_start=datetime(2026, 9, 1, tzinfo=timezone.utc),
        interrupt_on_sleep=1,
        context=ScriptedContext(
            quota_wait_policy=QuotaWaitPolicy(
                automatic=True,
                safety_margin_seconds=0,
                maximum_wait_seconds=60,
                maximum_auto_resumes=2,
                heartbeat_interval_seconds=1,
            ),
            transient_retry_policy=TransientRetryPolicy(
                automatic=True,
                initial_delay_seconds=1,
                maximum_delay_seconds=5,
                maximum_auto_resumes=2,
            ),
        ),
    )


def build_joint_branch_discovery_scenario(
    family_binding: FamilyBindingPayload,
) -> DryRunScenario:
    """Return the linked standalone branch-discovery run after IMPLEMENT."""

    reviewed_head = family_binding.current_implementation_commit
    if reviewed_head is None:
        raise DryRunScenarioError(
            "branch discovery scenario requires the implementation HEAD"
        )
    fingerprint = "6" * 64
    return DryRunScenario(
        name="joint-branch-discovery-v1",
        initial=ScriptedInitialState(
            kind=WorkUnitKind.BRANCH_DISCOVERY,
            branch="feature/dry-run",
            slice_count=1,
            scope_paths=("src/first.py", "src/second.py"),
            execution_mode="BRANCH_DISCOVERY",
            family_binding=family_binding,
            first_slice_start_commit=reviewed_head,
        ),
        agent_events=(
            ScriptedAgentEvent(
                AgentRole.CLAUDE,  # allowlist:provider
                1,
                1,
                WorkflowStep.CLAUDE_BRANCH_DISCOVERY,  # allowlist:provider
                {
                    "schema_version": "native-agent-review-result-v2",
                    "result_type": "branch_discovery_completed",
                    "request_id": "$BOUND_REQUEST_ID",
                    "reviewer": "claude",  # allowlist:provider
                    "scan_complete": True,
                    "new_findings": [
                        {
                            "finding_id": "C-03",
                            "finding_class": "OBSERVATION",
                            "affected_paths": ["src/orchestrator.py"],
                            "summary": (
                                "The linked discovery run found a remediation item."
                            ),
                            "predecessor_finding_ref": None,
                            "evidence_anchor_sha256": None,
                            "acceptance_test": {
                                "kind": "prose",
                                "text": (
                                    "A linked PLAN_ONLY run receives the complete "
                                    "finding history."
                                ),
                            },
                        }
                    ],
                    "occurrences": [],
                    "review_evidence": {
                        "dimensions": (
                            "correctness, contracts, failure paths, security, resume"
                        ),
                        "largest_residual_risk": (
                            "the remediation handoff loses imported finding history"
                        ),
                        "break_condition": (
                            "the linked PLAN_ONLY import omits C-03"
                        ),
                    },
                    "pre_mortem": (
                        "A crash after discovery could publish an unbound family task."
                    ),
                },
            ),
        ),
        changes=(
            _scripted_change(
                1,
                1,
                family_binding.family_base_commit,
                fingerprint,
                ("src/first.py", "src/second.py"),
                "branch discovery",
            ),
        ),
        validations=(ScriptedValidation(fingerprint),),
        clock_start=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )


def build_progressive_correction_scenario(
    *, stalled: bool = False
) -> DryRunScenario:
    """Exercise same-Slice convergence beyond four returns or at a fixed point."""

    base, slice_commit = "a" * 40, "b" * 40
    scope = ("src/runtime.py",)
    implementer_role = AgentRole.CODEX  # allowlist:provider -- scripted role boundary
    reviewer_role = AgentRole.CLAUDE  # allowlist:provider -- scripted role boundary
    implementation_step = WorkflowStep.CODEX_IMPLEMENTATION  # allowlist:provider -- scripted step boundary
    slice_review_step = WorkflowStep.CLAUDE_SLICE_REVIEW  # allowlist:provider -- scripted step boundary
    correction_step = WorkflowStep.CODEX_CORRECTION  # allowlist:provider -- scripted step boundary

    def implementer_result(
        result_type: str, findings: tuple[str, ...] = ()
    ) -> dict[str, object]:
        return {
            "schema_version": "native-agent-codex-result-v2",  # allowlist:provider
            "request_id": "$BOUND_REQUEST_ID",
            "result_type": result_type,
            "ready": True,
            "finding_dispositions": [
                {
                    "finding_id": finding_id,
                    "decision": "accepted",
                    "rationale": f"The scripted correction addresses {finding_id}.",
                    "responsibility_proposal": None,
                }
                for finding_id in findings
            ],
            "test_files": [],
        }

    def review(
        *,
        approved: bool,
        opened: tuple[str, ...] = (),
        closed: tuple[str, ...] = (),
    ) -> dict[str, object]:
        return {
            "schema_version": "native-agent-review-result-v2",
            "result_type": "review_result",
            "request_id": "$BOUND_REQUEST_ID",
            "reviewer": "claude",  # allowlist:provider
            "decision": "approved" if approved else "denied",
            "new_findings": [
                {
                    "finding_id": finding_id,
                    "finding_class": "BLOCKER",
                    "affected_paths": ["src/orchestrator.py"],
                    "summary": f"Scripted correction finding {finding_id}.",
                    "acceptance_test": {
                        "kind": "prose",
                        "text": f"The scripted correction closes {finding_id}.",
                    },
                }
                for finding_id in opened
            ],
            "status_changes": [
                {
                    "finding_id": finding_id,
                    "status": "CLOSED",
                    "rationale": f"The scripted correction closes {finding_id}.",
                    "closure": {"kind": "fixed"},
                }
                for finding_id in closed
            ],
            "reclassifications": [],
            "responsibility_routes": [],
            "plan_treatment_decisions": [],
            "anchors": [],
            "review_evidence": {
                "dimensions": "progress, terminal verdict, persistence, resume",
                "largest_residual_risk": "the return policy stops before convergence",
                "break_condition": "a progressing fifth return creates a user gate",
            },
            "pre_mortem": "A counter could replace the record-derived progress test.",
        }

    events: list[ScriptedAgentEvent] = [
        ScriptedAgentEvent(
            implementer_role, 2, 1, implementation_step,
            implementer_result("implementation_result"),
        ),
        ScriptedAgentEvent(
            reviewer_role, 2, 1, slice_review_step,
            review(approved=False, opened=("C-01",)),
        ),
        ScriptedAgentEvent(
            implementer_role, 2, 2, correction_step,
            implementer_result("correction_result", ("C-01",)),
        ),
    ]
    correction_rounds = 1 if stalled else 6
    for round_number in range(1, correction_rounds + 1):
        if stalled:
            events.append(
                ScriptedAgentEvent(
                    reviewer_role, 2, round_number + 1,
                    slice_review_step,
                    review(approved=False),
                )
            )
            continue
        current_id = f"C-{round_number:02d}"
        if round_number < correction_rounds:
            next_id = f"C-{round_number + 1:02d}"
            events.extend(
                (
                    ScriptedAgentEvent(
                        reviewer_role, 2, round_number + 1,
                        slice_review_step,
                        review(
                            approved=False,
                            opened=(next_id,),
                            closed=(current_id,),
                        ),
                    ),
                    ScriptedAgentEvent(
                        implementer_role, 2, round_number + 2,
                        correction_step,
                        implementer_result("correction_result", (next_id,)),
                    ),
                )
            )
        else:
            events.append(
                ScriptedAgentEvent(
                    reviewer_role, 2, round_number + 1,
                    slice_review_step,
                    review(approved=True, closed=(current_id,)),
                )
            )
    changes = [
        _scripted_change(2, 1, base, "1" * 64, scope, "implementation"),
    ]
    for round_number in range(1, correction_rounds + 1):
        fingerprint = str(round_number + 2) * 64
        changes.append(
            _scripted_change(
                2,
                round_number + 1,
                base,
                fingerprint,
                scope,
                f"correction round {round_number}",
            )
        )
    validation_fingerprints = tuple(item.fingerprint for item in changes)
    return DryRunScenario(
        name=("stalled-correction" if stalled else "progressive-correction"),
        initial=ScriptedInitialState(
            kind=WorkUnitKind.SLICE,
            slice_count=1,
            scope_paths=scope,
        ),
        agent_events=tuple(events),
        changes=tuple(changes),
        validations=tuple(
            ScriptedValidation(fingerprint)
            for fingerprint in validation_fingerprints
        ),
        commits=(
            *((ScriptedCommit(1, "8" * 64, slice_commit),) if not stalled else ()),
        ),
    )


def run_scripted_work_unit(
    *,
    scenario: DryRunScenario,
    state: WorkflowState,
    context: WorkflowContext,
    history: WorkflowHistory | None = None,
) -> ScriptedRunReport:
    """Run the real v3 state machine with only agent/command/time backends replaced."""
    return ScriptedWorkflowSession(scenario).run(state, context, history)


def _gate_rule_id(reason: GateReason, detail: str | None) -> str | None:
    if detail is None:
        return None
    match = GATE_RULE_PREFIX.match(detail)
    if match is not None:
        return match.group(1)
    matches = tuple(
        rule_id
        for expected_reason, rule_id, grammar in PREFIXLESS_GATE_RULES
        if expected_reason is reason and grammar.fullmatch(detail)
    )
    return matches[0] if len(matches) == 1 else None


def _gate_kind(
    status: GateStatus, reason: GateReason, fingerprint: str | None
) -> str:
    if status in {
        GateStatus.AWAITING_RESUME,
        GateStatus.WAITING_FOR_QUOTA,
        GateStatus.WAITING_FOR_RETRY,
    }:
        return "resume"
    if status is not GateStatus.AWAITING_USER_DECISION:
        raise DryRunScenarioError(
            f"gate status {status.value} has no non-clear gate kind"
        )
    if reason in {
        GateReason.ITERATION_LIMIT,
        GateReason.TEST_CHANGE,
        GateReason.ANCHOR_CHANGE,
        GateReason.MANUAL_SLICE,
        GateReason.PLAN_APPROVAL,
        GateReason.QUOTA_RESUME_DIFF,
    }:
        return "user"
    if reason is GateReason.UNEXPECTED_FILE:
        return "user" if fingerprint is not None else "policy"
    if reason is GateReason.STOP_REQUEST:
        return "policy"
    raise DryRunScenarioError(
        f"gate reason {reason.value} has no awaiting-user gate kind"
    )
