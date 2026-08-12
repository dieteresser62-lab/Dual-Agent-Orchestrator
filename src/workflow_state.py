from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Mapping


STATE_VERSION = 3
DEFAULT_MAX_CODEX_RETURNS = 4
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class WorkflowStateValidationError(ValueError):
    """Raised when a version-3 workflow state is structurally inconsistent."""


class WorkUnitKind(str, Enum):
    PLAN = "plan"
    SLICE = "slice"
    CORRECTION = "correction"


class WorkflowStep(str, Enum):
    CODEX_PLAN = "codex_plan"
    CLAUDE_PLAN_REVIEW = "claude_plan_review"
    CODEX_PLAN_REVISION = "codex_plan_revision"
    CODEX_IMPLEMENTATION = "codex_implementation"
    CLAUDE_SLICE_REVIEW = "claude_slice_review"
    CODEX_CORRECTION = "codex_correction"
    ANTIGRAVITY_SLICE_REVIEW = "antigravity_slice_review"
    SLICE_COMMIT = "slice_commit"
    CODEX_FINAL_CORRECTION = "codex_final_correction"
    CLAUDE_FINAL_REVIEW = "claude_final_review"
    ANTIGRAVITY_FINAL_REVIEW = "antigravity_final_review"
    COMPLETED = "completed"


class WorkUnitStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    AWAITING_USER_DECISION = "awaiting_user_decision"
    COMPLETED = "completed"


class SliceStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    AWAITING_USER_DECISION = "awaiting_user_decision"
    COMPLETED = "completed"


class GateStatus(str, Enum):
    CLEAR = "clear"
    AWAITING_USER_DECISION = "awaiting_user_decision"
    WAITING_FOR_QUOTA = "waiting_for_quota"


class GateReason(str, Enum):
    NONE = "none"
    ITERATION_LIMIT = "iteration_limit"
    TEST_CHANGE = "test_change"
    STOP_REQUEST = "stop_request"
    UNEXPECTED_FILE = "unexpected_file"
    ANCHOR_CHANGE = "anchor_change"
    MANUAL_SLICE = "manual_slice"
    QUOTA = "quota"


class Reviewer(str, Enum):
    CLAUDE = "claude"
    ANTIGRAVITY = "antigravity"


@dataclass(frozen=True)
class GateRecord:
    status: GateStatus = GateStatus.CLEAR
    reason: GateReason = GateReason.NONE
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.status is GateStatus.CLEAR:
            if self.reason is not GateReason.NONE or self.detail is not None:
                raise WorkflowStateValidationError("a clear gate cannot carry a reason or detail")
        elif self.reason is GateReason.NONE:
            raise WorkflowStateValidationError("a non-clear gate requires a reason")
        if self.detail is not None and not self.detail.strip():
            raise WorkflowStateValidationError("gate detail must be non-empty when present")

    def to_dict(self) -> dict[str, str | None]:
        return {
            "status": self.status.value,
            "reason": self.reason.value,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> GateRecord:
        _require_exact_keys(raw, {"status", "reason", "detail"}, "gate")
        return cls(
            status=_enum_value(GateStatus, raw["status"], "gate.status"),
            reason=_enum_value(GateReason, raw["reason"], "gate.reason"),
            detail=_optional_string(raw["detail"], "gate.detail"),
        )


@dataclass(frozen=True)
class SliceRecord:
    slice_id: int
    status: SliceStatus
    start_commit: str | None = None
    scope_paths: tuple[str, ...] = ()
    start_fingerprint: str | None = None
    commit_ref: str | None = None

    def __post_init__(self) -> None:
        _require_positive_int(self.slice_id, "slice_id")
        if self.start_commit is not None:
            _require_non_empty(self.start_commit, "slice start_commit")
        if self.status is not SliceStatus.PENDING and self.start_commit is None:
            raise WorkflowStateValidationError("a started slice requires start_commit")
        _require_canonical_scope(self.scope_paths)
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
            "start_fingerprint": self.start_fingerprint,
            "commit_ref": self.commit_ref,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> SliceRecord:
        legacy_keys = {"slice_id", "status", "start_commit", "commit_ref"}
        boundary_keys = {*legacy_keys, "scope_paths", "start_fingerprint"}
        if set(raw) == legacy_keys:
            scope_paths: tuple[str, ...] = ()
            start_fingerprint = None
        else:
            _require_exact_keys(raw, boundary_keys, "slice")
            scope_paths = _string_tuple(raw["scope_paths"], "slice.scope_paths")
            start_fingerprint = _optional_string(
                raw["start_fingerprint"], "slice.start_fingerprint"
            )
        return cls(
            slice_id=_positive_int(raw["slice_id"], "slice.slice_id"),
            status=_enum_value(SliceStatus, raw["status"], "slice.status"),
            start_commit=_optional_string(raw["start_commit"], "slice.start_commit"),
            scope_paths=scope_paths,
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
        awaiting = self.status is WorkUnitStatus.AWAITING_USER_DECISION
        if awaiting != (self.gate.status is GateStatus.AWAITING_USER_DECISION):
            raise WorkflowStateValidationError(
                "work-unit status and awaiting-user gate must change together"
            )
        if self.gate.reason is GateReason.ITERATION_LIMIT:
            if self.codex_return_count != self.max_codex_returns:
                raise WorkflowStateValidationError(
                    "iteration-limit gate requires the configured Codex return limit"
                )
            if self.reviewer is None:
                raise WorkflowStateValidationError("iteration-limit gate requires a reviewer")

    def has_completed_side_effect(self, key: str) -> bool:
        _require_non_empty(key, "side-effect key")
        return key in self.completed_side_effects

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
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> WorkUnitRecord:
        _require_exact_keys(
            raw,
            {
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
            },
            "work unit",
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

    @property
    def current_work_unit(self) -> WorkUnitRecord:
        return next(
            unit for unit in self.work_units if unit.work_unit_id == self.current_work_unit_id
        )

    @property
    def current_slice(self) -> SliceRecord:
        return next(item for item in self.slices if item.slice_id == self.current_slice_id)

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
        start_fingerprint: str,
        updated_at: str | None = None,
    ) -> WorkflowState:
        """Persist the immutable Git boundary before the first slice edit."""
        _require_non_empty(start_commit, "start_commit")
        normalized_scope = _normalize_scope_paths(scope_paths)
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
                and current_slice.start_fingerprint == start_fingerprint
            ):
                return self
            raise WorkflowStateValidationError("cannot change a persisted slice Git boundary")
        bound = replace(
            current_slice,
            scope_paths=normalized_scope,
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
            raise WorkflowStateValidationError("Codex return limit was already reached")
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
            slices = tuple(
                replace(item, status=SliceStatus.AWAITING_USER_DECISION)
                if item.slice_id == self.current_slice_id
                else item
                for item in self.slices
            )
        return self._replace_current_unit(updated_unit, slices=slices, updated_at=updated_at)

    def resume_after_user_decision(self, *, updated_at: str | None = None) -> WorkflowState:
        current = self.current_work_unit
        if current.status is not WorkUnitStatus.AWAITING_USER_DECISION:
            raise WorkflowStateValidationError("workflow is not awaiting a user decision")
        updated_unit = replace(
            current,
            status=WorkUnitStatus.IN_PROGRESS,
            gate=GateRecord(),
        )
        slices = tuple(
            replace(item, status=SliceStatus.IN_PROGRESS)
            if item.slice_id == self.current_slice_id
            else item
            for item in self.slices
        )
        return self._replace_current_unit(updated_unit, slices=slices, updated_at=updated_at)

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
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> WorkflowState:
        _require_exact_keys(
            raw,
            {
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
            },
            "workflow state",
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
        )


def init_workflow_state(
    *,
    run_id: str,
    task_file: str,
    branch: str,
    branch_base: str,
    slice_count: int,
    timestamp: str | None = None,
) -> WorkflowState:
    _require_positive_int(slice_count, "slice_count")
    stamp = timestamp or _now_iso()
    slices = tuple(
        SliceRecord(
            slice_id=slice_id,
            status=SliceStatus.IN_PROGRESS if slice_id == 1 else SliceStatus.PENDING,
            start_commit=branch_base if slice_id == 1 else None,
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
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_non_empty(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowStateValidationError(f"{label} must be a non-empty string")


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
        ):
            raise WorkflowStateValidationError(
                "slice scope_paths must be sorted, unique, relative POSIX paths"
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
