from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TypeAlias

from contracts import (
    AgentRole,
    ContractResult,
    FindingRecord,
    FindingResponseDecision,
    FindingStatus,
    ValidationAttestation,
)
from path_policy import PathPolicyError, resolve_repository_path
from state_io import atomic_write_file


class AuditTrailError(ValueError):
    """Raised when an audit target or structured projection is unsafe."""


REQUIRED_SLICE_HEADINGS = (
    "Ziel des Slice",
    "Akzeptanzkriterien",  # allowlist:german
    "Scope und Nicht-Scope",
    "Diff-Risiko inklusive Branch- und Statuscheck",
    "Geplante Tests",
    "Durchgeführte Änderungen",
    "Ausgeführte Validierung mit Ergebnis",  # allowlist:german
    "Abweichungen vom Plan",
    "Offene Risiken",  # allowlist:german
    "Review-Feedback von Claude",
    "Review-Feedback von Antigravity",
    "Review-Antworten von Codex",
    "Validierungsattestierung",
    "Testfreigabe und Pre-Mortem",  # allowlist:german
    "Findings-Lebenszyklus",  # allowlist:german
    "Entscheidungstabelle",  # allowlist:german
    "Rückdokumentation in die Arbeitsplan-MD",
    "Freigabestatus",  # allowlist:german
)

REQUIRED_WORK_PLAN_HEADINGS = (
    "Zielbild in eigenen Worten",
    "Branch-, Status- und Scope-Festlegung",
    "Verbindliche Entscheidungen und Umsetzungskonsequenzen",  # allowlist:german
    "Slice-Liste",
    "Reihenfolge und Abhängigkeitsgraph",
    "Abdeckungsmatrix R-1 bis R-18",
    "Migration und Rollback",
    "Test- und Validierungsplan",
    "Offene Fragen",  # allowlist:german
    "Review-Feedback von Claude",
    "Review-Feedback von Antigravity",
    "Review-Antworten von Codex",
    "Planstatus und formale Marker",
)

MANAGED_SECTION_KEYS = (
    "claude-review",
    "antigravity-review",
    "codex-responses",
    "validation-attestation",
    "test-approval-premortem",
    "findings",
    "decision-table",
    "approval-status",
)

SLICE_MANAGED_SECTION_HEADINGS = {
    "claude-review": "Review-Feedback von Claude",
    "antigravity-review": "Review-Feedback von Antigravity",
    "codex-responses": "Review-Antworten von Codex",
    "validation-attestation": "Validierungsattestierung",
    "test-approval-premortem": "Testfreigabe und Pre-Mortem",  # allowlist:german
    "findings": "Findings-Lebenszyklus",  # allowlist:german
    "decision-table": "Entscheidungstabelle",  # allowlist:german
    "approval-status": "Freigabestatus",  # allowlist:german
}

WORK_PLAN_MANAGED_SECTION_HEADINGS = {
    "claude-review": "Review-Feedback von Claude",
    "antigravity-review": "Review-Feedback von Antigravity",
    "codex-responses": "Review-Antworten von Codex",
    "validation-attestation": "Planstatus und formale Marker",
    "test-approval-premortem": "Planstatus und formale Marker",
    "findings": "Planstatus und formale Marker",
    "decision-table": "Planstatus und formale Marker",
    "approval-status": "Planstatus und formale Marker",
}

_SLICE_FILE_PATTERN = re.compile(
    r"^slice-[a-z0-9]+(?:-[a-z0-9]+)*-(?P<slice_id>\d{2})-"
    r"[a-z0-9]+(?:-[a-z0-9]+)*\.md$"
)
_MARKER_PATTERN = re.compile(
    r"^<!-- audit:(?P<key>[a-z0-9-]+):(?P<edge>begin|end) -->[ \t]*$"
)


@dataclass(frozen=True)
class SliceDocument:
    slice_id: int
    repository_root: Path
    work_plan_path: Path
    slice_path: Path
    relative_path: str
    markdown: str


@dataclass(frozen=True)
class WorkPlanDocument:
    repository_root: Path
    work_plan_path: Path
    markdown: str


@dataclass(frozen=True)
class _ManagedMarker:
    key: str
    edge: str
    start: int
    end: int


@dataclass(frozen=True)
class ValidationAuditEvent:
    event_id: int
    slice_id: int
    attestation: ValidationAttestation

    def __post_init__(self) -> None:
        _require_event_identity(self.event_id, self.slice_id)


@dataclass(frozen=True)
class ReviewAuditEvent:
    event_id: int
    slice_id: int
    round_number: int
    result: ContractResult

    def __post_init__(self) -> None:
        _require_event_identity(self.event_id, self.slice_id)
        _require_positive_int(self.round_number, "round_number")
        if self.result.reviewer not in (AgentRole.CLAUDE, AgentRole.ANTIGRAVITY):
            raise AuditTrailError("review audit event requires claude or antigravity")
        expected_slice = f"{self.slice_id:02d}"
        for finding in self.result.findings:
            if finding.origin.slice_id != expected_slice:
                raise AuditTrailError(
                    f"finding {finding.finding_id} belongs to slice "
                    f"{finding.origin.slice_id}, expected {expected_slice}"
                )
        if self.result.stopped != (self.result.stop_request is not None):
            raise AuditTrailError("review stop state and stop request are inconsistent")
        if self.result.stopped and self.result.approval is not None:
            raise AuditTrailError("a stopped review cannot carry an approval")
        if not self.result.stopped and self.result.approval is None:
            raise AuditTrailError("a completed review requires an approval decision")
        if self.result.approval is True and self.result.own_open_blockers:
            raise AuditTrailError(
                "an approving review cannot carry reviewer-owned open blockers"
            )
        if (
            self.result.approval is True
            and self.result.validation is not None
            and not self.result.validation.passed
        ):
            raise AuditTrailError(
                "an approving review requires its validation attestation to pass"
            )
        if self.result.approval is False and not self.result.own_open_blockers:
            raise AuditTrailError("a denied review requires a reviewer-owned open blocker")


AuditEvent: TypeAlias = ValidationAuditEvent | ReviewAuditEvent


@dataclass(frozen=True)
class AuthorizedTestChanges:
    approved: bool
    paths: tuple[str, ...]
    approved_by: str
    rationale: str

    def __post_init__(self) -> None:
        if not self.approved_by.strip():
            raise AuditTrailError("test approval requires an approver")
        if not self.rationale.strip():
            raise AuditTrailError("test approval requires a rationale")
        normalized: list[str] = []
        for raw_path in self.paths:
            if not isinstance(raw_path, str) or not raw_path.strip():
                raise AuditTrailError("test approval paths must be non-empty strings")
            path = PurePosixPath(raw_path)
            if path.is_absolute() or ".." in path.parts or path.parts[:1] != ("tests",):
                raise AuditTrailError(
                    f"test approval path must be repository-relative below tests/: {raw_path!r}"
                )
            normalized.append(path.as_posix())
        if len(set(normalized)) != len(normalized):
            raise AuditTrailError("test approval paths must be unique")
        object.__setattr__(self, "paths", tuple(sorted(normalized)))


@dataclass(frozen=True)
class AuditProjection:
    slice_id: int
    events: tuple[AuditEvent, ...] = ()
    test_approval: AuthorizedTestChanges | None = None
    implementation_ready: bool | None = None
    commit_authorized: bool | None = None

    def __post_init__(self) -> None:
        _require_positive_int(self.slice_id, "slice_id")
        event_ids = tuple(event.event_id for event in self.events)
        if event_ids != tuple(range(1, len(event_ids) + 1)):
            raise AuditTrailError(
                "audit event ids must be ordered, unique, contiguous, and 1-based"
            )
        if any(event.slice_id != self.slice_id for event in self.events):
            raise AuditTrailError("all audit events must belong to the projection slice")

        attestations: dict[str, ValidationAttestation] = {}
        for event in self.events:
            if isinstance(event, ValidationAuditEvent):
                attestation_id = event.attestation.attestation_id
                if attestation_id in attestations:
                    raise AuditTrailError(f"duplicate validation event {attestation_id}")
                attestations[attestation_id] = event.attestation
                continue
            validation = event.result.validation
            if validation is None:
                continue
            projected = attestations.get(validation.attestation_id)
            if projected is None:
                raise AuditTrailError(
                    "review references a validation attestation that was not projected first"
                )
            if projected != validation:
                raise AuditTrailError(
                    "review validation binding differs from the projected attestation"
                )

        if self.commit_authorized is True:
            antigravity = self.latest_review(AgentRole.ANTIGRAVITY)
            if antigravity is None or antigravity.result.approval is not True:
                raise AuditTrailError(
                    "commit authorization requires an approving Antigravity review"
                )
            if (
                antigravity.result.validation is None
                or not antigravity.result.validation.passed
            ):
                raise AuditTrailError(
                    "commit authorization requires Antigravity to be bound to a passing "
                    "validation attestation"
                )

    def latest_review(self, reviewer: AgentRole) -> ReviewAuditEvent | None:
        matches = (
            event
            for event in reversed(self.events)
            if isinstance(event, ReviewAuditEvent) and event.result.reviewer is reviewer
        )
        return next(matches, None)


def validate_slice_document(
    *,
    repository_root: Path,
    work_plan_path: str | Path,
    slice_id: int,
    expected_relative_path: str,
) -> SliceDocument:
    """Validate the exact Slice-MD target, active plan links, and §9.2 structure."""
    _require_positive_int(slice_id, "slice_id")
    root = Path(repository_root).resolve()
    if not root.is_dir():
        raise AuditTrailError("repository root must be an existing directory")

    relative = _validate_expected_relative_path(expected_relative_path, slice_id)
    plan_path = _resolve_inside_repository(work_plan_path, root, "work plan")
    lexical_target_path = root / PurePosixPath(relative)
    if lexical_target_path.is_symlink():
        raise AuditTrailError("slice document must not be a symbolic link")
    target_path = _resolve_inside_repository(lexical_target_path, root, "slice document")
    docs_root = (root / "docs" / "internal").resolve()
    if not target_path.is_relative_to(docs_root):
        raise AuditTrailError("slice document must be below docs/internal")
    if not plan_path.is_file():
        raise AuditTrailError("work plan does not exist or is not a regular file")
    if not target_path.is_file():
        raise AuditTrailError("slice document does not exist or is not a regular file")

    try:
        plan_markdown = plan_path.read_text(encoding="utf-8")
        markdown = target_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise AuditTrailError(f"audit document could not be read: {exc}") from exc

    _validate_plan_links(
        plan_markdown=plan_markdown,
        plan_path=plan_path,
        target_path=target_path,
        relative_path=relative,
        slice_id=slice_id,
        repository_root=root,
    )
    _validate_slice_structure(markdown)
    _managed_ranges(markdown, require_all=True)
    _validate_marker_ownership(markdown, SLICE_MANAGED_SECTION_HEADINGS)
    return SliceDocument(
        slice_id=slice_id,
        repository_root=root,
        work_plan_path=plan_path,
        slice_path=target_path,
        relative_path=relative,
        markdown=markdown,
    )


def validate_work_plan_document(
    *,
    repository_root: Path,
    work_plan_path: str | Path,
) -> WorkPlanDocument:
    """Validate a root-bound work plan prepared with managed audit blocks."""
    root = Path(repository_root).resolve()
    if not root.is_dir():
        raise AuditTrailError("repository root must be an existing directory")
    lexical_path = Path(work_plan_path)
    if not lexical_path.is_absolute():
        lexical_path = root / lexical_path
    if lexical_path.is_symlink():
        raise AuditTrailError("work plan must not be a symbolic link")
    plan_path = _resolve_inside_repository(lexical_path, root, "work plan")
    docs_root = (root / "docs" / "internal").resolve()
    if not plan_path.is_relative_to(docs_root):
        raise AuditTrailError("work plan must be below docs/internal")
    if not plan_path.is_file():
        raise AuditTrailError("work plan does not exist or is not a regular file")
    try:
        markdown = plan_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise AuditTrailError(f"work plan could not be read: {exc}") from exc
    _validate_work_plan_structure(markdown)
    _managed_ranges(markdown, require_all=True)
    _validate_marker_ownership(markdown, WORK_PLAN_MANAGED_SECTION_HEADINGS)
    return WorkPlanDocument(
        repository_root=root,
        work_plan_path=plan_path,
        markdown=markdown,
    )


def project_slice_audit(
    document: SliceDocument,
    projection: AuditProjection,
) -> str:
    """Render structured v3 records into all managed blocks in one atomic update."""
    if projection.slice_id != document.slice_id:
        raise AuditTrailError("projection slice does not match the validated document")
    for event in projection.events:
        if (
            isinstance(event, ReviewAuditEvent)
            and event.result.approval is True
            and event.result.validation is None
        ):
            raise AuditTrailError(
                "an approving slice review requires a bound validation attestation"
            )
    current = validate_slice_document(
        repository_root=document.repository_root,
        work_plan_path=document.work_plan_path,
        slice_id=document.slice_id,
        expected_relative_path=document.relative_path,
    )
    rendered = _render_projection(current.markdown, projection)
    if rendered != current.markdown:
        atomic_write_file(current.slice_path, rendered)
    return rendered


def project_work_plan_audit(
    document: WorkPlanDocument,
    projection: AuditProjection,
) -> str:
    """Project planning-work-unit events into one prepared work-plan audit target."""
    current = validate_work_plan_document(
        repository_root=document.repository_root,
        work_plan_path=document.work_plan_path,
    )
    rendered = _render_projection(current.markdown, projection)
    if rendered != current.markdown:
        atomic_write_file(current.work_plan_path, rendered)
    return rendered


def strip_managed_audit_sections(markdown: str) -> str:
    """Return stable semantic text with managed projection bodies removed."""
    ranges = _managed_ranges(markdown, require_all=False)
    if not ranges:
        return markdown
    stripped = markdown
    for key in sorted(ranges, key=lambda item: ranges[item][0], reverse=True):
        start, end = ranges[key]
        replacement = (
            f"<!-- audit:{key}:begin -->\n"
            f"<!-- audit:{key}:end -->"
        )
        stripped = stripped[:start] + replacement + stripped[end:]
    return stripped


def semantic_audit_fingerprint(markdown: str) -> str:
    semantic = strip_managed_audit_sections(markdown)
    return hashlib.sha256(semantic.encode("utf-8")).hexdigest()


def _render_projection(markdown: str, projection: AuditProjection) -> str:
    section_bodies = _render_managed_sections(projection)
    rendered = markdown
    for key in MANAGED_SECTION_KEYS:
        rendered = _replace_managed_body(rendered, key, section_bodies[key])
    return rendered


def _validate_expected_relative_path(raw: str, slice_id: int) -> str:
    if not isinstance(raw, str) or not raw.strip() or "\\" in raw:
        raise AuditTrailError("expected slice path must be a non-empty POSIX path")
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts:
        raise AuditTrailError("expected slice path must be repository-relative without traversal")
    relative = path.as_posix()
    if path.parts[:2] != ("docs", "internal") or len(path.parts) != 3:
        raise AuditTrailError("expected slice path must be directly below docs/internal")
    match = _SLICE_FILE_PATTERN.fullmatch(path.name)
    if match is None:
        raise AuditTrailError("slice filename does not match the planned naming contract")
    if int(match.group("slice_id")) != slice_id:
        raise AuditTrailError("slice filename id does not match the 1-based slice id")
    return relative


def _resolve_inside_repository(
    raw_path: str | Path,
    repository_root: Path,
    label: str,
) -> Path:
    try:
        return resolve_repository_path(raw_path, repository_root)
    except PathPolicyError as exc:
        raise AuditTrailError(f"{label} path is outside the repository: {exc}") from exc


def _validate_plan_links(
    *,
    plan_markdown: str,
    plan_path: Path,
    target_path: Path,
    relative_path: str,
    slice_id: int,
    repository_root: Path,
) -> None:
    visible_plan = "\n".join(_lines_outside_code_fences(plan_markdown))
    list_match = re.search(
        r"^### 5\.1 Geplante Slice-MD-Dateien\s*$"
        r"(?P<body>.*?)"
        r"(?=^### |^## )",
        visible_plan,
        re.MULTILINE | re.DOTALL,
    )
    if list_match is None:
        raise AuditTrailError("work plan is missing §5.1 planned Slice-MD files")
    rows = re.findall(
        rf"^\|\s*{slice_id}\s*\|\s*(?P<cell>.+?)\s*\|\s*$",
        list_match.group("body"),
        re.MULTILINE,
    )
    if len(rows) != 1:
        raise AuditTrailError("work plan must contain exactly one §5.1 row for the slice")
    cell_match = re.fullmatch(r"\[(?P<label>.+?)\]\((?P<href>[^)]+)\)", rows[0])
    if cell_match is None:
        raise AuditTrailError("§5.1 slice target must be an active Markdown link")
    label = cell_match.group("label").strip()
    if label not in (relative_path, f"`{relative_path}`"):
        raise AuditTrailError("§5.1 link label does not match the expected slice path")
    _validate_relative_link(
        cell_match.group("href"), plan_path, target_path, repository_root, "§5.1"
    )

    headings = re.findall(
        rf"^### \[Slice {slice_id}\b[^\]]*\]\((?P<href>[^)]+)\)\s*$",
        visible_plan,
        re.MULTILINE,
    )
    if len(headings) != 1:
        raise AuditTrailError("slice heading must contain exactly one active Markdown link")
    _validate_relative_link(
        headings[0], plan_path, target_path, repository_root, "slice heading"
    )


def _validate_relative_link(
    href: str,
    plan_path: Path,
    target_path: Path,
    repository_root: Path,
    label: str,
) -> None:
    href = href.strip()
    path = PurePosixPath(href)
    if (
        not href
        or "\\" in href
        or not path.parts
        or path.is_absolute()
        or ".." in path.parts
        or ":" in path.parts[0]
        or "?" in href
        or "#" in href
    ):
        raise AuditTrailError(f"{label} must use a plain relative repository link")
    linked = _resolve_inside_repository(plan_path.parent / path, repository_root, label)
    if linked != target_path:
        raise AuditTrailError(f"{label} resolves to a different slice document")


def _validate_slice_structure(markdown: str) -> None:
    visible_lines = _lines_outside_code_fences(markdown)
    headings = [line[3:].strip() for line in visible_lines if line.startswith("## ")]
    positions: list[int] = []
    for required in REQUIRED_SLICE_HEADINGS:
        matches = [index for index, heading in enumerate(headings) if heading == required]
        if len(matches) != 1:
            raise AuditTrailError(
                f"slice document requires exactly one '## {required}' heading"
            )
        positions.append(matches[0])
    if positions != sorted(positions):
        raise AuditTrailError("required Slice-MD headings are out of order")
    for metadata in ("**Feature-Branch:**", "**GitHub-Status:**"):
        if sum(line.startswith(metadata) for line in visible_lines) != 1:
            raise AuditTrailError(f"slice document requires exactly one {metadata} field")


def _validate_work_plan_structure(markdown: str) -> None:
    visible_lines = _lines_outside_code_fences(markdown)
    headings = [
        re.sub(r"^\d+\.\s+", "", line[3:].strip())
        for line in visible_lines
        if line.startswith("## ")
    ]
    positions: list[int] = []
    for required in REQUIRED_WORK_PLAN_HEADINGS:
        matches = [index for index, heading in enumerate(headings) if heading == required]
        if len(matches) != 1:
            raise AuditTrailError(
                f"work plan requires exactly one level-two '{required}' heading"
            )
        positions.append(matches[0])
    if positions != sorted(positions):
        raise AuditTrailError("required work-plan headings are out of order")
    for metadata in ("**Feature-Branch:**", "**GitHub-Status:**"):
        if sum(line.startswith(metadata) for line in visible_lines) != 1:
            raise AuditTrailError(f"work plan requires exactly one {metadata} field")


def _lines_outside_code_fences(markdown: str) -> list[str]:
    visible: list[str] = []
    fence: tuple[str, int] | None = None
    for line in markdown.splitlines():
        fence, boundary = _advance_fence_state(line, fence)
        if boundary:
            continue
        if fence is None:
            visible.append(line)
    if fence is not None:
        raise AuditTrailError("slice document contains an unterminated code fence")
    return visible


def _validate_marker_ownership(
    markdown: str,
    expected_headings: dict[str, str],
) -> None:
    heading: str | None = None
    fence: tuple[str, int] | None = None
    for line in markdown.splitlines():
        fence, boundary = _advance_fence_state(line, fence)
        if boundary:
            continue
        if fence is not None:
            continue
        if line.startswith("## "):
            heading = re.sub(r"^\d+\.\s+", "", line[3:].strip())
        for marker in _MARKER_PATTERN.finditer(line):
            key = marker.group("key")
            expected = expected_headings.get(key)
            if expected is not None and heading != expected:
                raise AuditTrailError(
                    f"managed audit section {key} belongs below '## {expected}'"
                )


def _managed_ranges(markdown: str, *, require_all: bool) -> dict[str, tuple[int, int]]:
    edges: dict[str, dict[str, _ManagedMarker]] = {}
    for marker in _managed_markers_outside_code_fences(markdown):
        key = marker.key
        edge = marker.edge
        if key not in MANAGED_SECTION_KEYS:
            raise AuditTrailError(f"unknown managed audit section {key!r}")
        bucket = edges.setdefault(key, {})
        if edge in bucket:
            raise AuditTrailError(f"duplicate {edge} marker for audit section {key}")
        bucket[edge] = marker
    if require_all and set(edges) != set(MANAGED_SECTION_KEYS):
        missing = sorted(set(MANAGED_SECTION_KEYS) - set(edges))
        raise AuditTrailError(f"slice document is missing managed sections: {missing}")
    ranges: dict[str, tuple[int, int]] = {}
    for key, pair in edges.items():
        if set(pair) != {"begin", "end"}:
            raise AuditTrailError(f"incomplete managed audit section {key}")
        begin = pair["begin"]
        end = pair["end"]
        if begin.start >= end.start:
            raise AuditTrailError(f"reversed managed audit section {key}")
        ranges[key] = (begin.start, end.end)
    ordered = sorted((start, end, key) for key, (start, end) in ranges.items())
    for (_, previous_end, previous_key), (start, _, key) in zip(ordered, ordered[1:]):
        if start < previous_end:
            raise AuditTrailError(
                f"managed audit sections {previous_key} and {key} overlap"
            )
    return ranges


def _managed_markers_outside_code_fences(markdown: str) -> tuple[_ManagedMarker, ...]:
    records: list[_ManagedMarker] = []
    fence: tuple[str, int] | None = None
    offset = 0
    for line in markdown.splitlines(keepends=True):
        logical_line = line.rstrip("\r\n")
        fence, boundary = _advance_fence_state(logical_line, fence)
        if boundary:
            offset += len(line)
            continue
        if fence is None:
            records.extend(
                _ManagedMarker(
                    key=match.group("key"),
                    edge=match.group("edge"),
                    start=offset + match.start(),
                    end=offset + match.end(),
                )
                for match in _MARKER_PATTERN.finditer(logical_line)
            )
        offset += len(line)
    return tuple(records)


def _advance_fence_state(
    line: str,
    fence: tuple[str, int] | None,
) -> tuple[tuple[str, int] | None, bool]:
    match = re.match(r"^ {0,3}(?P<run>`{3,}|~{3,})(?P<tail>.*)$", line)
    if match is None:
        return fence, False
    run = match.group("run")
    if fence is None:
        return (run[0], len(run)), True
    character, minimum_length = fence
    if (
        run[0] == character
        and len(run) >= minimum_length
        and not match.group("tail").strip()
    ):
        return None, True
    return fence, False


def _replace_managed_body(markdown: str, key: str, body: str) -> str:
    ranges = _managed_ranges(markdown, require_all=True)
    start, end = ranges[key]
    begin = f"<!-- audit:{key}:begin -->"
    finish = f"<!-- audit:{key}:end -->"
    replacement = f"{begin}\n{body.rstrip()}\n{finish}"
    return markdown[:start] + replacement + markdown[end:]


def _render_managed_sections(projection: AuditProjection) -> dict[str, str]:
    findings = _latest_findings(projection.events)
    return {
        "claude-review": _render_reviews(projection, AgentRole.CLAUDE),
        "antigravity-review": _render_reviews(projection, AgentRole.ANTIGRAVITY),
        "codex-responses": _render_codex_responses(findings),
        "validation-attestation": _render_validations(projection.events),
        "test-approval-premortem": _render_test_approval(projection),
        "findings": _render_findings(findings),
        "decision-table": _render_decision_table(findings),
        "approval-status": _render_approval_status(projection),
    }


def _render_reviews(projection: AuditProjection, reviewer: AgentRole) -> str:
    events = [
        event
        for event in projection.events
        if isinstance(event, ReviewAuditEvent) and event.result.reviewer is reviewer
    ]
    if not events:
        return "Noch kein strukturiertes Reviewereignis."
    blocks: list[str] = []
    for event in events:
        result = event.result
        decision = "STOPPED" if result.stopped else "YES" if result.approval else "NO"
        validation_id = (
            _safe(result.validation.attestation_id) if result.validation is not None else "–"
        )
        blocks.extend(
            (
                f"### Ereignis {event.event_id}: Runde {event.round_number}",
                "",
                f"- Reviewer: `{reviewer.value}`",
                f"- Freigabe: `{decision}`",  # allowlist:german
                f"- Validierungsbindung: `{validation_id}`",
                "- Testdateien: "
                + (
                    ", ".join(f"`{_safe(path)}`" for path in result.test_files)
                    if result.test_files
                    else "keine"
                ),
            )
        )
        if result.stop_request is not None:
            blocks.append(
                f"- Stop-Regel: `{_safe(result.stop_request.rule_id)}` — "
                f"{_safe(result.stop_request.rationale)}"
            )
        if result.evidence is not None:
            blocks.extend(
                (
                    f"- Prüfdimensionen: {_safe(result.evidence.dimensions)}",
                    f"- Größtes Restrisiko: {_safe(result.evidence.largest_residual_risk)}",
                    f"- Realistische Bruchbedingung: {_safe(result.evidence.break_condition)}",
                )
            )
        own_findings = [
            finding for finding in result.findings if finding.origin.reporter is reviewer
        ]
        blocks.append(
            "- Eigene Findings: "
            + (", ".join(f"`{item.finding_id}`" for item in own_findings) or "keine")
        )
        blocks.append("")
    return "\n".join(blocks).rstrip()


def _render_validations(events: tuple[AuditEvent, ...]) -> str:
    validations = [event for event in events if isinstance(event, ValidationAuditEvent)]
    if not validations:
        return "Noch keine strukturierte Validierungsattestierung."
    blocks: list[str] = []
    for event in validations:
        item = event.attestation
        blocks.extend(
            (
                f"### Ereignis {event.event_id}: `{_safe(item.attestation_id)}`",
                "",
                f"- Diff-Fingerprint: `{item.diff_fingerprint}`",
                f"- Status: `{item.status.value}`",
                f"- Vollständig: `{'YES' if item.complete else 'NO'}`",
                f"- Kurzresultat: {_safe(item.summary)}",
                f"- Ausgabedigest: `{item.output_digest}`",
                "",
                "| Matrixbefehl | Status | Exitcode |",
                "|---|---|---:|",
            )
        )
        by_command = {record.command: record for record in item.records}
        for command in item.expected_commands:
            record = by_command.get(command)
            status = record.status.value if record is not None else "MISSING"
            exit_code = str(record.exit_code) if record is not None else "–"
            blocks.append(f"| {_safe(command)} | {status} | {exit_code} |")
        blocks.append("")
    return "\n".join(blocks).rstrip()


def _render_test_approval(projection: AuditProjection) -> str:
    blocks: list[str] = []
    approval = projection.test_approval
    if approval is None:
        blocks.append("- Teständerungsfreigabe: nicht erfasst.")  # allowlist:german
    else:
        blocks.extend(
            (
                f"- Teständerungsfreigabe: `{'YES' if approval.approved else 'NO'}`",  # allowlist:german
                f"- Freigebende Stelle: {_safe(approval.approved_by)}",
                f"- Begründung: {_safe(approval.rationale)}",
                "- Pfade: "
                + (
                    ", ".join(f"`{_safe(path)}`" for path in approval.paths)
                    if approval.paths
                    else "keine"
                ),
            )
        )
    pre_mortems = [
        (event.event_id, event.result.pre_mortem)
        for event in projection.events
        if isinstance(event, ReviewAuditEvent) and event.result.pre_mortem is not None
    ]
    if pre_mortems:
        blocks.append("- Pre-Mortems:")
        blocks.extend(
            f"  - Ereignis {event_id}: {_safe(text)}"
            for event_id, text in pre_mortems
            if text is not None
        )
    else:
        blocks.append("- Pre-Mortems: keine erfasst.")
    return "\n".join(blocks)


def _latest_findings(events: tuple[AuditEvent, ...]) -> tuple[FindingRecord, ...]:
    latest: dict[str, FindingRecord] = {}
    for event in events:
        if not isinstance(event, ReviewAuditEvent):
            continue
        for finding in event.result.findings:
            previous = latest.get(finding.finding_id)
            if previous is not None:
                if (
                    previous.origin != finding.origin
                    or previous.summary != finding.summary
                    or previous.acceptance_test != finding.acceptance_test
                ):
                    raise AuditTrailError(
                        f"finding identity changed during lifecycle: {finding.finding_id}"
                    )
                if finding.responses[: len(previous.responses)] != previous.responses:
                    raise AuditTrailError(
                        f"finding response history regressed: {finding.finding_id}"
                    )
                if finding.class_history[: len(previous.class_history)] != previous.class_history:
                    raise AuditTrailError(
                        f"finding class history regressed: {finding.finding_id}"
                    )
            latest[finding.finding_id] = finding
    return tuple(latest[key] for key in sorted(latest))


def _render_findings(findings: tuple[FindingRecord, ...]) -> str:
    if not findings:
        return "Noch keine strukturierten Findings."
    blocks: list[str] = []
    for finding in findings:
        blocks.extend(
            (
                f"### `{finding.finding_id}` — `{finding.status.value}`",
                "",
                f"- Quelle: `{finding.origin.reporter.value}`; Runde {finding.origin.round_number}",
                f"- Klasse: `{finding.finding_class.value}`",
                f"- Finding: {_safe(finding.summary)}",
                f"- Akzeptanztest: {_safe(finding.acceptance_test)}",
                "- Statusbegründung: "
                + (_safe(finding.status_rationale) if finding.status_rationale else "–"),
                "",
            )
        )
    return "\n".join(blocks).rstrip()


def _render_codex_responses(findings: tuple[FindingRecord, ...]) -> str:
    rows: list[str] = []
    for finding in findings:
        for index, response in enumerate(finding.responses, start=1):
            decision = (
                "angenommen"
                if response.decision is FindingResponseDecision.ACCEPTED
                else "bestritten"
            )
            rows.append(
                f"- `{finding.finding_id}` Antwort {index}: **{decision}** — "
                f"{_safe(response.rationale)}"
            )
    return "\n".join(rows) if rows else "Noch keine strukturierten Codex-Antworten."


def _render_decision_table(findings: tuple[FindingRecord, ...]) -> str:
    lines = (
        "| ID | Quelle | Finding | Klasse | Entscheidung | Umsetzung |",  # allowlist:german
        "|---|---|---|---|---|---|",
    )
    rows = list(lines)
    if not findings:
        rows.append("| – | – | Noch keine Findings | – | – | – |")
        return "\n".join(rows)
    for finding in findings:
        if finding.responses:
            decision = (
                "angenommen"
                if finding.responses[-1].decision is FindingResponseDecision.ACCEPTED
                else "bestritten"
            )
        else:
            decision = "offen"
        implementation = (
            "erledigt: " + _safe(finding.status_rationale)
            if finding.status is FindingStatus.CLOSED and finding.status_rationale
            else "offen"
        )
        rows.append(
            "| "
            + " | ".join(
                (
                    finding.finding_id,
                    finding.origin.reporter.value,
                    _safe(finding.summary),
                    finding.finding_class.value,
                    decision,
                    implementation,
                )
            )
            + " |"
        )
    return "\n".join(rows)


def _render_approval_status(projection: AuditProjection) -> str:
    claude = projection.latest_review(AgentRole.CLAUDE)
    antigravity = projection.latest_review(AgentRole.ANTIGRAVITY)
    validations = [
        event for event in projection.events if isinstance(event, ValidationAuditEvent)
    ]
    validation_status = validations[-1].attestation.status.value if validations else "NOT_RECORDED"
    return "\n".join(
        (
            f"- Implementierung bereit: `{_tri_state(projection.implementation_ready)}`",
            f"- Validierung: `{validation_status}`",
            f"- Claude-Freigabe: `{_review_state(claude)}`",  # allowlist:german
            f"- Antigravity-Freigabe: `{_review_state(antigravity)}`",  # allowlist:german
            f"- Commit autorisiert: `{_tri_state(projection.commit_authorized)}`",
        )
    )


def _review_state(event: ReviewAuditEvent | None) -> str:
    if event is None:
        return "NOT_RECORDED"
    if event.result.stopped:
        return "STOPPED"
    return "YES" if event.result.approval else "NO"


def _tri_state(value: bool | None) -> str:
    return "NOT_RECORDED" if value is None else "YES" if value else "NO"


def _safe(value: str) -> str:
    escaped = html.escape(str(value), quote=False)
    return (
        escaped.replace("|", "&#124;")
        .replace("`", "&#96;")
        .replace("\r\n", "<br>")
        .replace("\r", "<br>")
        .replace("\n", "<br>")
    )


def _require_event_identity(event_id: int, slice_id: int) -> None:
    _require_positive_int(event_id, "event_id")
    _require_positive_int(slice_id, "slice_id")


def _require_positive_int(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise AuditTrailError(f"{label} must be a 1-based integer")
