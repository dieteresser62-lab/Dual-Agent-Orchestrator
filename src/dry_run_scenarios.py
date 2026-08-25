from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

from agent_runtime import (
    AgentInvocationError,
    AgentProcessError,
    QuotaWaitPolicy,
    TransientRetryPolicy,
    classify_agent_failure,
)
from audit_trail import ReviewAuditEvent, ValidationAuditEvent
from contracts import AgentRole, ValidationAttestation, ValidationRecord, ValidationStatus
from gates import TestChangeEvidence
from validation_matrix import ValidationCommand, ValidationRequest
from workflow import (
    CodexInvocation,
    ContractRepairInvocation,
    ReviewerInvocation,
    ValidationExecutionError,
    WorkflowChanges,
    WorkflowCommitRequest,
    WorkflowCorrectionBoundary,
    WorkflowContext,
    WorkflowEngine,
    WorkflowHistory,
    WorkflowRunResult,
)
from workflow_state import (
    AgentFailureKind,
    GateReason,
    ProtocolBinding,
    ProtocolMode,
    WorkUnitKind,
    WorkflowState,
    WorkflowStep,
    init_workflow_state,
)


SCENARIO_VERSION = 1


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
    round_number: int
    step: WorkflowStep
    output: str | None = None
    failure: ScriptedFailure | None = None

    def __post_init__(self) -> None:
        if self.role not in {AgentRole.CODEX, AgentRole.CLAUDE}:
            raise ValueError("scripted event requires a workflow role")
        if self.work_unit_id < 1 or self.round_number < 1:
            raise ValueError("scripted event identity must be 1-based")
        if (self.output is None) == (self.failure is None):
            raise ValueError("scripted event requires exactly one of output or failure")

    @classmethod
    def from_dict(cls, raw: Mapping[str, object], index: int) -> ScriptedAgentEvent:
        label = f"agent_events[{index}]"
        _require_exact_keys(
            raw,
            {"role", "work_unit_id", "round_number", "step"},
            {"output", "failure"},
            label,
        )
        try:
            role = AgentRole(_string(raw["role"], f"{label}.role"))
            step = WorkflowStep(_string(raw["step"], f"{label}.step"))
        except ValueError as exc:
            raise DryRunScenarioError(f"{label} has an unknown role or step") from exc
        output = raw.get("output")
        if output is not None:
            output = _string(output, f"{label}.output")
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
                _positive_int(raw["round_number"], f"{label}.round_number"),
                step,
                output,
                failure,
            )
        except ValueError as exc:
            raise DryRunScenarioError(str(exc)) from exc


@dataclass(frozen=True)
class ScriptedChange:
    work_unit_id: int
    round_number: int
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
            {"work_unit_id", "round_number", "start_commit", "fingerprint", "paths", "full_diff"},
            {"gate_paths"},
            label,
        )
        return cls(
            _positive_int(raw["work_unit_id"], f"{label}.work_unit_id"),
            _positive_int(raw["round_number"], f"{label}.round_number"),
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

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ScriptedInitialState:
        _require_exact_keys(
            raw,
            set(),
            {"kind", "branch", "slice_count", "max_codex_returns", "scope_paths"},
            "scenario.initial",
        )
        try:
            kind = WorkUnitKind(str(raw.get("kind", WorkUnitKind.SLICE.value)))
        except ValueError as exc:
            raise DryRunScenarioError("scenario.initial.kind is unknown") from exc
        if kind not in {WorkUnitKind.PLAN, WorkUnitKind.SLICE}:
            raise DryRunScenarioError("scenario.initial.kind must be plan or slice")
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
        )


@dataclass(frozen=True)
class ScriptedContext:
    expected_test_files: tuple[str, ...] = ()
    test_changes_approved: bool = True
    manual_slice_gate: bool = False
    red_state_followup_slice: str | None = None
    current_branch: str | None = None
    max_productive_files: int = 10
    quota_wait_policy: QuotaWaitPolicy = QuotaWaitPolicy()
    transient_retry_policy: TransientRetryPolicy = TransientRetryPolicy()

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ScriptedContext:
        _require_exact_keys(
            raw,
            set(),
            {
                "expected_test_files", "test_changes_approved", "manual_slice_gate",
                "red_state_followup_slice", "current_branch", "max_productive_files",
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
        maximum = raw.get("max_productive_files", 10)
        maximum = _positive_int(maximum, "scenario.context.max_productive_files")
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
            max_productive_files=maximum,
            quota_wait_policy=quota,
            transient_retry_policy=transient,
        )


@dataclass(frozen=True)
class ScenarioExpectations:
    exit_code: int
    status: str | None = None
    step: WorkflowStep | None = None
    gate_reason: GateReason | None = None
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
                "status", "step", "gate_reason", "commit_count",
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
    repair_outputs: tuple[str, ...] = ()
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
                "validations", "commits", "test_changes", "repair_outputs",
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
        repair_raw = raw.get("repair_outputs", [])
        if not isinstance(repair_raw, list):
            raise DryRunScenarioError("scenario repair_outputs must be an array")
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
                repair_outputs=tuple(
                    _string(item, f"repair_outputs[{index}]")
                    for index, item in enumerate(repair_raw)
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
    _agent_index: int = 0
    _validation_index: int = 0
    _repair_index: int = 0
    _commit_index: int = 0
    _active_identity: tuple[int, int] | None = None
    _change_positions: dict[tuple[int, int], int] = field(default_factory=dict)

    def bind_work_unit(self, state: WorkflowState) -> None:
        self._active_identity = (
            state.current_work_unit_id,
            state.current_work_unit.round_number,
        )

    def _consume_agent(
        self,
        *,
        role: AgentRole,
        work_unit_id: int,
        round_number: int,
        step: WorkflowStep,
    ) -> str:
        if self._agent_index >= len(self.scenario.agent_events):
            raise DryRunScenarioError(
                f"missing scripted response for {role.value}/{work_unit_id}/{round_number}/{step.value}"
            )
        event = self.scenario.agent_events[self._agent_index]
        expected = (role, work_unit_id, round_number, step)
        actual = (event.role, event.work_unit_id, event.round_number, event.step)
        if actual != expected:
            raise DryRunScenarioError(
                "scripted role order mismatch: expected "
                f"{role.value}/{work_unit_id}/{round_number}/{step.value}, got "
                f"{event.role.value}/{event.work_unit_id}/{event.round_number}/{event.step.value}"
            )
        self._agent_index += 1
        if role is AgentRole.CODEX:
            self._active_identity = (work_unit_id, round_number)
        self.calls.append(
            f"agent:{role.value}:work-unit-{work_unit_id}:round-{round_number}:{step.value}"
        )
        if event.failure is not None:
            raise event.failure.to_exception(
                role, f"dry-{self.scenario.name}-{self._agent_index:03d}"
            )
        assert event.output is not None
        return event.output

    def invoke_codex(self, invocation: CodexInvocation) -> str:
        self.codex_invocations.append(invocation)
        return self._consume_agent(
            role=AgentRole.CODEX,
            work_unit_id=invocation.work_unit_id,
            round_number=invocation.round_number,
            step=invocation.step,
        )

    def invoke_reviewer(self, invocation: ReviewerInvocation) -> str:
        self.reviewer_invocations.append(invocation)
        return self._consume_agent(
            role=invocation.reviewer,
            work_unit_id=invocation.work_unit_id,
            round_number=invocation.round_number,
            step=invocation.step,
        )

    def collect_changes(self, start_commit: str) -> WorkflowChanges:
        if self._active_identity is None:
            raise DryRunScenarioError("scripted change collection has no active work unit")
        matches = tuple(
            item
            for item in self.scenario.changes
            if (item.work_unit_id, item.round_number) == self._active_identity
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
        self.calls.append(f"changes:{match.work_unit_id}:round-{match.round_number}")
        return match.workflow_changes

    def collect_correction_delta(
        self, previous_fingerprint: str, current_fingerprint: str
    ) -> str:
        self.calls.append(f"delta:{previous_fingerprint[:8]}:{current_fingerprint[:8]}")
        return f"scripted correction {previous_fingerprint} -> {current_fingerprint}"

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
        attestation_fingerprint = event.attestation_fingerprint or changes.fingerprint
        digest_payload = json.dumps(
            {
                "fingerprint": attestation_fingerprint,
                "status": event.status,
                "commands": request.expected_commands,
                "attempt": request.attempt_number,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return ValidationAttestation(
            attestation_id=(
                f"dry-{self.scenario.name}-{changes.fingerprint[:8]}-"
                f"{request.attempt_number}"
            ),
            diff_fingerprint=attestation_fingerprint,
            expected_commands=request.expected_commands,
            records=records,
            output_digest=hashlib.sha256(digest_payload.encode()).hexdigest(),
            summary=f"scripted validation {event.status}",
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

    def repair_review_contract(self, invocation: ContractRepairInvocation) -> str:
        self.calls.append(f"repair:{invocation.reviewer.value}")
        if self._repair_index >= len(self.scenario.repair_outputs):
            raise DryRunScenarioError(
                f"missing scripted contract repair for {invocation.reviewer.value}"
            )
        output = self.scenario.repair_outputs[self._repair_index]
        self._repair_index += 1
        return output

    def prepare_correction(
        self, findings
    ) -> WorkflowCorrectionBoundary:
        if not any(item.status.value == "OPEN" for item in findings):
            raise DryRunScenarioError(
                "scripted final-review correction requires an open finding"
            )
        if self._active_identity is None:
            raise DryRunScenarioError(
                "scripted final-review correction has no active work unit"
            )
        correction_id = self._active_identity[0] + 1
        match = next(
            (
                item
                for item in self.scenario.changes
                if item.work_unit_id == correction_id and item.round_number == 1
            ),
            None,
        )
        if match is None:
            raise DryRunScenarioError(
                f"missing scripted correction boundary for work unit {correction_id}"
            )
        self.calls.append(f"prepare-correction:{correction_id}")
        return WorkflowCorrectionBoundary(
            start_commit=match.start_commit,
            scope_paths=match.paths,
            start_fingerprint="0" * 64,
        )

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
        self.checkpoints.append(state)
        self.checkpoint_histories.append(history)
        self.calls.append(
            f"checkpoint:{state.current_work_unit_id}:{state.current_step.value}:"
            f"{state.current_work_unit.status.value}"
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


@dataclass
class ScriptedWorkflowSession:
    scenario: DryRunScenario
    driver: ScriptedWorkflowDriver = field(init=False)
    clock: ScriptedClock = field(init=False)
    engine: WorkflowEngine = field(init=False)

    def __post_init__(self) -> None:
        self.driver = ScriptedWorkflowDriver(self.scenario)
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
        )


def build_scenario_state(
    scenario: DryRunScenario, *, task_file: Path
) -> WorkflowState:
    if not scenario.changes:
        raise DryRunScenarioError("scenario requires at least one scripted change set")
    first = scenario.changes[0]
    state = init_workflow_state(
        run_id=f"dry-{scenario.name}",
        task_file=str(task_file.resolve()),
        branch=scenario.initial.branch,
        branch_base=first.start_commit,
        slice_count=scenario.initial.slice_count,
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
        timestamp=scenario.clock_start.isoformat(),
    )
    if scenario.initial.kind is WorkUnitKind.PLAN:
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
    state = state.bind_current_slice_git_boundary(
        start_commit=first.start_commit,
        scope_paths=scenario.initial.scope_paths or first.paths,
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
    return WorkflowContext(
        assignment=f"Scripted dry-run scenario: {scenario.name}",
        distilled_plan="Use the real v3 state machine with scripted backends only.",
        slice_summary="Exercise configured positive and negative workflow gates.",
        expected_test_files=configured.expected_test_files,
        test_changes_approved=configured.test_changes_approved,
        manual_slice_gate=configured.manual_slice_gate,
        current_branch=configured.current_branch or scenario.initial.branch,
        max_productive_files=configured.max_productive_files,
        red_state_followup_slice=configured.red_state_followup_slice,
        quota_wait_policy=configured.quota_wait_policy,
        transient_retry_policy=configured.transient_retry_policy,
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


def run_scripted_work_unit(
    *,
    scenario: DryRunScenario,
    state: WorkflowState,
    context: WorkflowContext,
    history: WorkflowHistory | None = None,
) -> ScriptedRunReport:
    """Run the real v3 state machine with only agent/command/time backends replaced."""
    return ScriptedWorkflowSession(scenario).run(state, context, history)
