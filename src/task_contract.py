from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath

from contracts import PlannedSlice
from gates import matches_path_patterns, normalize_path_patterns


FEATURE_BRANCH_PATTERN = re.compile(r"^(?:feature|codex)/[A-Za-z0-9._-]+$")
MARKER_PATTERN = re.compile(
    r"^[ \t]*(ORCHESTRATOR_MODE|WORK_PLAN_PATH|APPROVED_PLAN_COMMIT|TARGET_BRANCH|TASK_SCOPE|"
    r"FINDING_HANDOFF_SOURCE_RUN|FINDING_HANDOFF_EXPORT)"
    r"[ \t]*:[ \t]*(.+?)[ \t]*$",
    re.MULTILINE | re.IGNORECASE,
)
SCOPE_HEADING_PATTERN = re.compile(
    r"^##[ \t]+(?:Erlaubter[ \t]+Scope|Allowed[ \t]+Scope)[ \t]*$",
    re.MULTILINE | re.IGNORECASE,
)


class TaskContractError(ValueError):
    """Raised when a task cannot be bound to a deterministic execution boundary."""


class TaskMode(str, Enum):
    IMPLEMENT = "IMPLEMENT"
    PLAN_ONLY = "PLAN_ONLY"


@dataclass(frozen=True)
class TaskContract:
    digest: str
    mode: TaskMode
    scope_patterns: tuple[str, ...]
    target_branch: str
    work_plan_path: str | None = None
    approved_plan_commit: str | None = None
    approved_slices: tuple[PlannedSlice, ...] = ()
    finding_handoff_source_run_id: str | None = None
    finding_handoff_export_record_id: str | None = None
    informal_intake: bool = False

    def __post_init__(self) -> None:
        if re.fullmatch(r"[0-9a-f]{64}", self.digest) is None:
            raise TaskContractError("task digest must be a lowercase SHA-256 value")
        if not self.scope_patterns:
            raise TaskContractError("task contract requires at least one scope path")
        if not FEATURE_BRANCH_PATTERN.fullmatch(self.target_branch):
            raise TaskContractError(
                "TARGET_BRANCH must use feature/<name> or codex/<name>"
            )
        if not isinstance(self.informal_intake, bool):
            raise TaskContractError("informal_intake must be a boolean")
        if self.informal_intake and self.mode is not TaskMode.PLAN_ONLY:
            raise TaskContractError("informal intake must be bound to PLAN_ONLY")
        if self.mode is TaskMode.PLAN_ONLY:
            if self.work_plan_path is None:
                raise TaskContractError("PLAN_ONLY requires WORK_PLAN_PATH")
            if not matches_path_patterns(self.work_plan_path, self.scope_patterns):
                raise TaskContractError(
                    "WORK_PLAN_PATH must be covered by the declared task scope"
                )
            if self.approved_plan_commit is not None or self.approved_slices:
                raise TaskContractError("PLAN_ONLY cannot consume an approved-plan handoff")
            if self.finding_handoff_source_run_id is not None:
                raise TaskContractError("PLAN_ONLY cannot consume a finding handoff")
        handoff_values = (
            self.finding_handoff_source_run_id,
            self.finding_handoff_export_record_id,
        )
        if any(value is None for value in handoff_values) != all(
            value is None for value in handoff_values
        ):
            raise TaskContractError("finding handoff requires both source run and export record")
        if self.finding_handoff_source_run_id is not None:
            if self.approved_plan_commit is None:
                raise TaskContractError("finding handoff requires an approved-plan IMPLEMENT task")
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", self.finding_handoff_source_run_id) is None:
                raise TaskContractError("FINDING_HANDOFF_SOURCE_RUN is invalid")
            if re.fullmatch(r"ar1-[0-9a-f]{64}", self.finding_handoff_export_record_id or "") is None:
                raise TaskContractError("FINDING_HANDOFF_EXPORT must be an artifact record ID")
        if self.approved_plan_commit is not None:
            if self.mode is not TaskMode.IMPLEMENT or self.work_plan_path is None:
                raise TaskContractError(
                    "APPROVED_PLAN_COMMIT requires IMPLEMENT and WORK_PLAN_PATH"
                )
            if re.fullmatch(r"[0-9a-f]{40,64}", self.approved_plan_commit) is None:
                raise TaskContractError("APPROVED_PLAN_COMMIT must be a Git object id")
            if not self.approved_slices:
                raise TaskContractError(
                    "APPROVED_PLAN_COMMIT requires embedded SLICE_PLAN records"
                )
            unexpected = tuple(
                path
                for item in self.approved_slices
                for path in item.scope_paths
                if not matches_path_patterns(path, self.scope_patterns)
            )
            if unexpected:
                raise TaskContractError(
                    "approved Slice paths are outside TASK_SCOPE: "
                    + ", ".join(sorted(set(unexpected)))
                )
        elif self.approved_slices:
            raise TaskContractError(
                "embedded SLICE_PLAN records require APPROVED_PLAN_COMMIT"
            )


def _informal_plan_slug(source_name: str, digest: str) -> str:
    stem = PurePosixPath(source_name.replace("\\", "/")).stem
    normalized = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9]+", "-", normalized).strip("-").lower()
    if not slug:
        slug = f"task-{digest[:12]}"
    return slug


def _is_informal_intake(
    markers: dict[str, list[str]], text: str, *, source_name: str | None
) -> bool:
    if source_name is None:
        return False
    formal_markers = {
        "ORCHESTRATOR_MODE",
        "WORK_PLAN_PATH",
        "APPROVED_PLAN_COMMIT",
        "TASK_SCOPE",
    }
    return not formal_markers.intersection(markers) and not _scope_from_heading(text)


def _clean_scope_entry(raw: str) -> str:
    value = raw.strip()
    code_match = re.search(r"`([^`]+)`", value)
    if code_match:
        value = code_match.group(1)
    value = value.replace(r"\_", "_").strip().rstrip(".;")
    return value


def _scope_from_heading(text: str) -> tuple[str, ...]:
    match = SCOPE_HEADING_PATTERN.search(text)
    if match is None:
        return ()
    section = text[match.end() :]
    next_heading = re.search(r"^##[ \t]+", section, re.MULTILINE)
    if next_heading is not None:
        section = section[: next_heading.start()]
    entries = []
    for line in section.splitlines():
        bullet = re.match(r"^[ \t]*[-*][ \t]+(.+?)[ \t]*$", line)
        if bullet is None:
            continue
        value = _clean_scope_entry(bullet.group(1))
        if value:
            entries.append(value)
    return tuple(entries)


def _single_marker(markers: dict[str, list[str]], name: str) -> str | None:
    values = markers.get(name, [])
    if len(values) > 1:
        raise TaskContractError(f"{name} must occur at most once")
    return values[0] if values else None


def _normalize_work_plan_path(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = _clean_scope_entry(raw)
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or ".." in path.parts
        or any(character in value for character in "*?[")
    ):
        raise TaskContractError(
            "WORK_PLAN_PATH must be one exact repository-relative POSIX path"
        )
    if path.suffix.lower() != ".md":
        raise TaskContractError("WORK_PLAN_PATH must reference a Markdown file")
    return path.as_posix()


def parse_task_contract(
    text: str,
    *,
    mode_override: bool | None = None,
    work_plan_override: str | None = None,
    target_branch_override: str | None = None,
    source_name: str | None = None,
) -> TaskContract:
    """Parse and validate the safety-critical task metadata and scope."""
    if not text.strip():
        raise TaskContractError("task file must not be empty")
    markers: dict[str, list[str]] = {}
    for match in MARKER_PATTERN.finditer(text):
        markers.setdefault(match.group(1).upper(), []).append(match.group(2).strip())

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    informal_intake = _is_informal_intake(markers, text, source_name=source_name)
    if informal_intake and mode_override is False:
        raise TaskContractError(
            "an informal task is planning input; --no-plan-only requires a formal "
            "IMPLEMENT contract"
        )

    raw_mode = _single_marker(markers, "ORCHESTRATOR_MODE")
    try:
        declared_mode = TaskMode(raw_mode.upper()) if raw_mode is not None else None
    except ValueError as exc:
        raise TaskContractError(
            "ORCHESTRATOR_MODE must be PLAN_ONLY or IMPLEMENT"
        ) from exc
    override_mode = (
        TaskMode.PLAN_ONLY if mode_override is True
        else TaskMode.IMPLEMENT if mode_override is False
        else None
    )
    if declared_mode is not None and override_mode is not None and declared_mode is not override_mode:
        raise TaskContractError(
            "CLI plan mode conflicts with ORCHESTRATOR_MODE in the task"
        )
    mode = (
        override_mode
        or declared_mode
        or (TaskMode.PLAN_ONLY if informal_intake else TaskMode.IMPLEMENT)
    )

    marker_scope = _single_marker(markers, "TASK_SCOPE")
    derived_plan = (
        f"docs/internal/{_informal_plan_slug(source_name or '', digest)}-arbeitsplan.md"
        if informal_intake
        else None
    )
    if marker_scope is not None:
        raw_scope = tuple(
            part.strip() for part in marker_scope.split(",") if part.strip()
        )
    elif informal_intake:
        raw_scope = (work_plan_override or derived_plan,)
    else:
        raw_scope = _scope_from_heading(text)
    try:
        scope_patterns = normalize_path_patterns(raw_scope, "task scope")
    except ValueError as exc:
        raise TaskContractError(str(exc)) from exc
    if not scope_patterns:
        raise TaskContractError(
            "task requires TASK_SCOPE or an 'Erlaubter Scope'/'Allowed Scope' section"
        )

    declared_plan = _single_marker(markers, "WORK_PLAN_PATH")
    if declared_plan is not None and work_plan_override is not None:
        if _normalize_work_plan_path(declared_plan) != _normalize_work_plan_path(work_plan_override):
            raise TaskContractError(
                "--work-plan conflicts with WORK_PLAN_PATH in the task"
            )
    work_plan_path = _normalize_work_plan_path(
        work_plan_override or declared_plan or derived_plan
    )

    declared_branch = _single_marker(markers, "TARGET_BRANCH")
    if declared_branch is not None and target_branch_override is not None:
        if declared_branch.strip() != target_branch_override.strip():
            raise TaskContractError(
                "--target-branch conflicts with TARGET_BRANCH in the task"
            )
    target_branch = (target_branch_override or declared_branch or "").strip()
    if not target_branch:
        raise TaskContractError(
            "task requires TARGET_BRANCH or the --target-branch option"
        )

    approved_plan_commit = _single_marker(markers, "APPROVED_PLAN_COMMIT")
    approved_slices = _parse_embedded_slice_plan(text)
    finding_handoff_source_run_id = _single_marker(
        markers, "FINDING_HANDOFF_SOURCE_RUN"
    )
    finding_handoff_export_record_id = _single_marker(
        markers, "FINDING_HANDOFF_EXPORT"
    )

    return TaskContract(
        digest=digest,
        mode=mode,
        scope_patterns=scope_patterns,
        target_branch=target_branch,
        work_plan_path=work_plan_path,
        approved_plan_commit=approved_plan_commit,
        approved_slices=approved_slices,
        finding_handoff_source_run_id=finding_handoff_source_run_id,
        finding_handoff_export_record_id=finding_handoff_export_record_id,
        informal_intake=informal_intake,
    )


def _parse_embedded_slice_plan(text: str) -> tuple[PlannedSlice, ...]:
    records: list[PlannedSlice] = []
    pattern = re.compile(
        r"^[ \t]*SLICE_PLAN[ \t]*:[ \t]*(\d+)[ \t]*\|[ \t]*(.+?)[ \t]*\|[ \t]*(.+?)[ \t]*$",
        re.MULTILINE | re.IGNORECASE,
    )
    for match in pattern.finditer(text):
        try:
            records.append(
                PlannedSlice(
                    slice_id=int(match.group(1)),
                    summary=match.group(2).strip(),
                    scope_paths=tuple(
                        sorted(
                            set(
                                part.strip()
                                for part in match.group(3).split(",")
                                if part.strip()
                            )
                        )
                    ),
                )
            )
        except ValueError as exc:
            raise TaskContractError(str(exc)) from exc
    ids = tuple(item.slice_id for item in records)
    if ids and ids != tuple(range(1, len(ids) + 1)):
        raise TaskContractError("embedded Slice ids must be contiguous and 1-based")
    return tuple(records)
