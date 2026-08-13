from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath

from gates import matches_path_patterns, normalize_path_patterns


FEATURE_BRANCH_PATTERN = re.compile(r"^(?:feature|codex)/[A-Za-z0-9._-]+$")
MARKER_PATTERN = re.compile(
    r"^[ \t]*(ORCHESTRATOR_MODE|WORK_PLAN_PATH|TARGET_BRANCH|TASK_SCOPE)"
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

    def __post_init__(self) -> None:
        if re.fullmatch(r"[0-9a-f]{64}", self.digest) is None:
            raise TaskContractError("task digest must be a lowercase SHA-256 value")
        if not self.scope_patterns:
            raise TaskContractError("task contract requires at least one scope path")
        if not FEATURE_BRANCH_PATTERN.fullmatch(self.target_branch):
            raise TaskContractError(
                "TARGET_BRANCH must use feature/<name> or codex/<name>"
            )
        if self.mode is TaskMode.PLAN_ONLY:
            if self.work_plan_path is None:
                raise TaskContractError("PLAN_ONLY requires WORK_PLAN_PATH")
            if not matches_path_patterns(self.work_plan_path, self.scope_patterns):
                raise TaskContractError(
                    "WORK_PLAN_PATH must be covered by the declared task scope"
                )


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
) -> TaskContract:
    """Parse and validate the safety-critical task metadata and scope."""
    if not text.strip():
        raise TaskContractError("task file must not be empty")
    markers: dict[str, list[str]] = {}
    for match in MARKER_PATTERN.finditer(text):
        markers.setdefault(match.group(1).upper(), []).append(match.group(2).strip())

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
    mode = override_mode or declared_mode or TaskMode.IMPLEMENT

    marker_scope = _single_marker(markers, "TASK_SCOPE")
    raw_scope = (
        tuple(part.strip() for part in marker_scope.split(",") if part.strip())
        if marker_scope is not None
        else _scope_from_heading(text)
    )
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
    work_plan_path = _normalize_work_plan_path(work_plan_override or declared_plan)

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

    return TaskContract(
        digest=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        mode=mode,
        scope_patterns=scope_patterns,
        target_branch=target_branch,
        work_plan_path=work_plan_path,
    )
