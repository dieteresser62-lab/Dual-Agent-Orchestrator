from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence, TypeAlias

from artifact_projection import (
    ArtifactAuditProjection,
    ArtifactProjectionError,
    SECTION_KEYS,
    finalize_projection_document,
)
from artifact_replay import ArtifactReplayResult
from artifact_bridge import review_payload_matches_complete_result
from artifact_models import ArtifactRecord, ReviewPayload

from contracts import (
    AgentRole,
    ContractResult,
    FindingRecord,
    FindingResponseDecision,
    ValidationAttestation,
)
from finding_reducer import merge_history_snapshots, project_open_set
from path_policy import PathPolicyError, resolve_repository_path
from state_io import atomic_write_file
from semantic_markdown import (
    SemanticMarkdownError,
    SemanticMarkdownKind,
    canonical_semantic_markdown,
    parse_semantic_markdown,
)


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
    "Review-Antworten von Codex",
    "Planstatus und formale Marker",
)

MANAGED_SECTION_KEYS = (
    "claude-review",
    "codex-responses",
    "validation-attestation",
    "test-approval-premortem",
    "findings",
    "decision-table",
    "approval-status",
)

SLICE_MANAGED_SECTION_HEADINGS = {
    "claude-review": "Review-Feedback von Claude",
    "codex-responses": "Review-Antworten von Codex",
    "validation-attestation": "Validierungsattestierung",
    "test-approval-premortem": "Testfreigabe und Pre-Mortem",  # allowlist:german
    "findings": "Findings-Lebenszyklus",  # allowlist:german
    "decision-table": "Entscheidungstabelle",  # allowlist:german
    "approval-status": "Freigabestatus",  # allowlist:german
}

_SLICE_FILE_PATTERN = re.compile(
    r"^slice-[a-z0-9]+(?:-[a-z0-9]+)*-(?P<slice_id>\d{2})-"
    r"[a-z0-9]+(?:-[a-z0-9]+)*\.md$"
)


def managed_slice_document_path(
    audit_path: str, slice_id: int, summary: str
) -> str:
    """Derive the stable per-Slice audit path without creating the file."""
    _require_positive_int(slice_id, "slice_id")
    task_slug = re.sub(
        r"-(?:gesamtpruefung|review)-[0-9a-f]{8}$",  # allowlist:german
        "",
        PurePosixPath(audit_path).stem.lower(),
    )
    task_slug = re.sub(r"[^a-z0-9]+", "-", task_slug).strip("-") or "task"
    summary_slug = re.sub(r"[^a-z0-9]+", "-", summary.lower()).strip("-")
    summary_slug = summary_slug[:48].rstrip("-") or "umsetzung"
    return f"docs/internal/slice-{task_slug}-{slice_id:02d}-{summary_slug}.md"


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
class OverallAuditEntry:
    """One work-unit projection in the consolidated task audit."""

    label: str
    summary: str
    scope_paths: tuple[str, ...]
    projection: AuditProjection

    def __post_init__(self) -> None:
        if not self.label.strip() or not self.summary.strip():
            raise AuditTrailError("overall audit entries require label and summary")
        if self.scope_paths != tuple(sorted(set(self.scope_paths))):
            raise AuditTrailError("overall audit entry scope must be sorted and unique")


GENERIC_WORK_PLAN_AUDIT_HEADING = "Orchestrator-Prüfprotokoll"


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
    allowed_finding_origins: tuple[str, ...] = ()
    is_final_review: bool = False

    def __post_init__(self) -> None:
        _require_event_identity(self.event_id, self.slice_id)
        _require_positive_int(self.round_number, "round_number")
        if any(
            origin != "FINAL" and not re.fullmatch(r"0*[1-9][0-9]*", origin)
            for origin in self.allowed_finding_origins
        ):
            raise AuditTrailError(
                "review audit finding origins must be FINAL or 1-based Slice ids"
            )
        if self.result.reviewer is not AgentRole.CLAUDE:
            raise AuditTrailError("review audit event requires claude")
        expected_slice = f"{self.slice_id:02d}"
        allowed_origins = {expected_slice, *self.allowed_finding_origins}
        for finding in self.result.findings:
            if finding.origin.slice_id not in allowed_origins:
                raise AuditTrailError(
                    f"finding {finding.finding_id} belongs to slice "
                    f"{finding.origin.slice_id}, expected one of "
                    f"{', '.join(sorted(allowed_origins))}"
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
            if (
                not self.result.validation.complete
                or self.result.red_state_followup_slice is None
            ):
                raise AuditTrailError(
                    "an approving review requires its validation attestation to pass "
                    "or use a named complete red-state exception"
                )
        if (
            self.result.approval is False
            and not self.result.own_open_blockers
            and not self.is_final_review
        ):
            raise AuditTrailError("a denied review requires a reviewer-owned open blocker")


def allowed_review_finding_origins(
    finding_ledger: Sequence[FindingRecord],
    *,
    current_slice_id: int,
    is_final_review: bool,
) -> tuple[str, ...]:
    """Derive audit origins only from the complete pre-review Finding ledger."""

    _require_positive_int(current_slice_id, "current_slice_id")
    current_origin = f"{current_slice_id:02d}"
    return tuple(
        sorted(
            {
                finding.origin.slice_id
                for finding in finding_ledger
                if finding.origin.slice_id != current_origin
            }
            | ({"FINAL"} if is_final_review else set())
        )
    )


AuditEvent: TypeAlias = ValidationAuditEvent | ReviewAuditEvent


@dataclass(frozen=True)
class AuthorizedTestChanges:
    approved: bool
    paths: tuple[str, ...]
    approved_by: str
    rationale: str
    approved_at: str | None = None
    diff_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if not self.approved_by.strip():
            raise AuditTrailError("test approval requires an approver")
        if not self.rationale.strip():
            raise AuditTrailError("test approval requires a rationale")
        if self.approved_at is not None and not self.approved_at.strip():
            raise AuditTrailError("test approval timestamp must be non-empty")
        if self.diff_fingerprint is not None and not re.fullmatch(
            r"[0-9a-f]{64}", self.diff_fingerprint
        ):
            raise AuditTrailError("test approval fingerprint must be a SHA-256 digest")
        normalized: list[str] = []
        for raw_path in self.paths:
            if not isinstance(raw_path, str) or not raw_path.strip():
                raise AuditTrailError("test approval paths must be non-empty strings")
            path = PurePosixPath(raw_path)
            if path.is_absolute() or ".." in path.parts or "\\" in raw_path:
                raise AuditTrailError(
                    f"test approval path must be repository-relative POSIX: {raw_path!r}"
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
    red_state_followup_slice: str | None = None
    review_record: ArtifactRecord | None = None
    review_work_unit_id: str | None = None

    def __post_init__(self) -> None:
        _require_positive_int(self.slice_id, "slice_id")
        event_ids = tuple(event.event_id for event in self.events)
        if event_ids != tuple(range(1, len(event_ids) + 1)):
            raise AuditTrailError(
                "audit event ids must be ordered, unique, contiguous, and 1-based"
            )
        if any(event.slice_id != self.slice_id for event in self.events):
            raise AuditTrailError("all audit events must belong to the projection slice")
        if (
            self.red_state_followup_slice is not None
            and not self.red_state_followup_slice.strip()
        ):
            raise AuditTrailError(
                "red-state follow-up slice must be non-empty when provided"
            )
        if (
            self.review_work_unit_id is not None
            and not self.review_work_unit_id.strip()
        ):
            raise AuditTrailError(
                "review work-unit id must be non-empty when provided"
            )

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
            review_event = self.latest_review(AgentRole.CLAUDE)
            if review_event is None or review_event.result.approval is not True:
                raise AuditTrailError(
                    "commit authorization requires an approving Claude review"
                )
            review_result = review_event.result
            if (
                review_result.validation is None
                or not (
                    review_result.validation.passed
                    or (
                        review_result.validation.complete
                        and self.red_state_followup_slice is not None
                    )
                )
            ):
                raise AuditTrailError(
                    "commit authorization requires Claude to be bound to a passing "
                    "attestation or named complete red-state exception"
            )
            if (
                not review_result.validation.passed
                and review_result.red_state_followup_slice
                != self.red_state_followup_slice
            ):
                raise AuditTrailError(
                    "commit authorization red-state follow-up differs from Claude review"
                )
            if not review_result.validation.passed:
                review_record = self.review_record
                if (
                    review_record is None
                    or not isinstance(review_record.payload, ReviewPayload)
                    or review_record.payload.verdict != "approved"
                    or self.review_work_unit_id is None
                    or review_record.payload.work_unit_id
                    != self.review_work_unit_id
                    or review_record.fingerprint.sha256
                    != review_result.validation.diff_fingerprint
                    or not review_payload_matches_complete_result(
                        review_record.payload,
                        review_result,
                    )
                    or review_record.payload.red_state_followup_slice
                    != self.red_state_followup_slice
                ):
                    raise AuditTrailError(
                        "red-state audit authorization requires its named exception "
                        "in an approved fingerprint-bound review record"
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
    return WorkPlanDocument(
        repository_root=root,
        work_plan_path=plan_path,
        markdown=markdown,
    )


def validate_managed_work_plan_document(
    *,
    repository_root: Path,
    work_plan_path: str | Path,
) -> WorkPlanDocument:
    """Validate any root-bound docs/internal work plan with complete managed blocks."""
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
    _lines_outside_code_fences(markdown)
    _managed_ranges(markdown, require_all=True)
    return WorkPlanDocument(root, plan_path, markdown)


def prepare_managed_work_plan_document(
    *,
    repository_root: Path,
    work_plan_path: str | Path,
) -> WorkPlanDocument:
    """Append an empty managed audit appendix to an arbitrary work plan once."""
    root = Path(repository_root).resolve()
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
    existing = _managed_ranges(markdown, require_all=False)
    if existing:
        if set(existing) != set(MANAGED_SECTION_KEYS):
            missing = sorted(set(MANAGED_SECTION_KEYS) - set(existing))
            raise AuditTrailError(
                f"work plan has a partial managed audit appendix: {missing}"
            )
        return validate_managed_work_plan_document(
            repository_root=root, work_plan_path=plan_path
        )
    appendix_lines = ["", f"## {GENERIC_WORK_PLAN_AUDIT_HEADING}", ""]
    for key in MANAGED_SECTION_KEYS:
        heading = SLICE_MANAGED_SECTION_HEADINGS[key]
        appendix_lines.extend(
            (
                f"### {heading}",
                "",
                f"<!-- audit:{key}:begin -->",
                f"<!-- audit:{key}:end -->",
                "",
            )
        )
    separator = "" if markdown.endswith(("\n", "\r")) else "\n"
    rendered = markdown + separator + "\n".join(appendix_lines).rstrip() + "\n"
    atomic_write_file(plan_path, rendered)
    return validate_managed_work_plan_document(
        repository_root=root, work_plan_path=plan_path
    )


def prepare_managed_overall_document(
    *,
    repository_root: Path,
    audit_path: str | Path,
    task_name: str,
    task_file: str,
    run_id: str,
    branch: str,
    task_scope: tuple[str, ...],
) -> WorkPlanDocument:
    """Create the consolidated task audit before planning starts, once."""
    root = Path(repository_root).resolve()
    lexical_path = Path(audit_path)
    if not lexical_path.is_absolute():
        lexical_path = root / lexical_path
    if lexical_path.is_symlink():
        raise AuditTrailError("overall audit document must not be a symbolic link")
    target = _resolve_inside_repository(lexical_path, root, "overall audit")
    docs_root = (root / "docs" / "internal").resolve()
    if not target.is_relative_to(docs_root) or target.parent != docs_root:
        raise AuditTrailError("overall audit must be directly below docs/internal")
    if target.suffix.lower() != ".md":
        raise AuditTrailError("overall audit must be a Markdown document")
    if target.exists():
        return validate_managed_work_plan_document(
            repository_root=root, work_plan_path=target
        )
    scope = ", ".join(f"`{item}`" for item in task_scope)
    markdown = "\n".join(
        (
            f"# Gesamtaudit – {task_name}",
            "",
            "Dieses Dokument wird vom Orchestrator geführt. Slice-Dokumente entstehen "
            "erst beim tatsächlichen Beginn ihrer Implementierung.",
            "",
            f"- Task-Datei: `{task_file}`",
            f"- Run-ID: `{run_id}`",
            f"- Zielbranch: `{branch}`",
            f"- Deklarierter Produktscope: {scope}",
            "",
        )
    )
    atomic_write_file(target, markdown)
    return prepare_managed_work_plan_document(
        repository_root=root, work_plan_path=target
    )


def prepare_managed_slice_document(
    *,
    repository_root: Path,
    work_plan_path: str | Path,
    slice_id: int,
    slice_path: str,
    title: str,
    scope_paths: tuple[str, ...],
    branch: str,
) -> SliceDocument:
    """Create one generic Slice report with all managed audit sections."""
    _require_positive_int(slice_id, "slice_id")
    root = Path(repository_root).resolve()
    relative = _validate_expected_relative_path(slice_path, slice_id)
    target = root / PurePosixPath(relative)
    if target.exists():
        return validate_managed_slice_document(
            repository_root=root,
            work_plan_path=work_plan_path,
            slice_id=slice_id,
            expected_relative_path=relative,
        )
    scope = ", ".join(f"`{path}`" for path in scope_paths)
    managed_by_heading = {
        heading: key for key, heading in SLICE_MANAGED_SECTION_HEADINGS.items()
    }
    lines = [
        f"# Slice {slice_id:02d} – {title}",
        "",
        f"**Feature-Branch:** `{branch}`",
        "**GitHub-Status:** nur lokal",
        "",
    ]
    default_bodies = {
        "Ziel des Slice": title,
        "Akzeptanzkriterien": "Siehe freigegebenen Arbeitsplan.",  # allowlist:german
        "Scope und Nicht-Scope": f"Erlaubter Scope: {scope}",
        "Diff-Risiko inklusive Branch- und Statuscheck": "Vor Umsetzung zu prüfen.",
        "Geplante Tests": "Gemäß Arbeitsplan und Orchestrator-Validierungsmatrix.",
        "Durchgeführte Änderungen": "Wird während der Umsetzung ergänzt.",
        "Ausgeführte Validierung mit Ergebnis": "Wird durch den Orchestrator projiziert.",  # allowlist:german
        "Abweichungen vom Plan": "Keine erfasst.",
        "Offene Risiken": "Siehe Findings-Lebenszyklus.",  # allowlist:german
        "Rückdokumentation in die Arbeitsplan-MD": (
            f"Arbeitsplan: `{PurePosixPath(str(work_plan_path)).as_posix()}`"
        ),
    }
    for heading in REQUIRED_SLICE_HEADINGS:
        lines.extend((f"## {heading}", ""))
        key = managed_by_heading.get(heading)
        if key is not None:
            lines.extend(
                (
                    f"<!-- audit:{key}:begin -->",
                    f"<!-- audit:{key}:end -->",
                    "",
                )
            )
        else:
            lines.extend((default_bodies.get(heading, "Wird ergänzt."), ""))
    atomic_write_file(target, "\n".join(lines).rstrip() + "\n")
    return validate_managed_slice_document(
        repository_root=root,
        work_plan_path=work_plan_path,
        slice_id=slice_id,
        expected_relative_path=relative,
    )


def validate_managed_slice_document(
    *,
    repository_root: Path,
    work_plan_path: str | Path,
    slice_id: int,
    expected_relative_path: str,
) -> SliceDocument:
    """Validate a generic generated Slice report without legacy plan-link rules."""
    root = Path(repository_root).resolve()
    relative = _validate_expected_relative_path(expected_relative_path, slice_id)
    target = _resolve_inside_repository(root / PurePosixPath(relative), root, "slice document")
    plan = _resolve_inside_repository(work_plan_path, root, "work plan")
    if not target.is_file() or not plan.is_file():
        raise AuditTrailError("managed Slice report or work plan is missing")
    try:
        markdown = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise AuditTrailError(f"slice document could not be read: {exc}") from exc
    _validate_slice_structure(markdown)
    _managed_ranges(markdown, require_all=True)
    return SliceDocument(slice_id, root, plan, target, relative, markdown)


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


def project_managed_slice_audit(
    document: SliceDocument,
    projection: AuditProjection,
) -> str:
    """Project evidence into a generic generated Slice report."""
    if projection.slice_id != document.slice_id:
        raise AuditTrailError("projection slice does not match the managed Slice report")
    current = validate_managed_slice_document(
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
    current = validate_managed_work_plan_document(
        repository_root=document.repository_root,
        work_plan_path=document.work_plan_path,
    )
    rendered = _render_projection(current.markdown, projection)
    if rendered != current.markdown:
        atomic_write_file(current.work_plan_path, rendered)
    return rendered


def project_overall_audit(
    document: WorkPlanDocument,
    entries: tuple[OverallAuditEntry, ...],
) -> str:
    """Project every persisted work unit into one consolidated task document."""
    current = validate_managed_work_plan_document(
        repository_root=document.repository_root,
        work_plan_path=document.work_plan_path,
    )
    section_parts = {key: [] for key in MANAGED_SECTION_KEYS}
    for entry in entries:
        rendered = _render_managed_sections(entry.projection)
        scope = ", ".join(f"`{_safe(path)}`" for path in entry.scope_paths) or "–"
        header = (
            f"#### {_safe(entry.label)}\n\n"
            f"- Auftrag: {_safe(entry.summary)}\n"
            f"- Scope: {scope}\n"
        )
        for key in MANAGED_SECTION_KEYS:
            section_parts[key].append(f"{header}\n{rendered[key]}")
    rendered_markdown = current.markdown
    for key in MANAGED_SECTION_KEYS:
        body = "\n\n".join(section_parts[key])
        if not body:
            body = "Noch kein persistiertes Orchestratorereignis."
        rendered_markdown = _replace_managed_body(rendered_markdown, key, body)
    if rendered_markdown != current.markdown:
        atomic_write_file(current.work_plan_path, rendered_markdown)
    return rendered_markdown


def project_structured_slice_audit(
    document: SliceDocument,
    projection: ArtifactAuditProjection | ArtifactReplayResult,
) -> str:
    """Append the record-native view to a validated Slice audit atomically."""
    if isinstance(projection, ArtifactReplayResult):
        projection = ArtifactAuditProjection.from_replay(
            projection, slice_id=str(document.slice_id)
        )
    if projection.slice_id is not None and int(projection.slice_id) != document.slice_id:
        raise AuditTrailError("structured projection slice does not match its document")
    current = validate_managed_slice_document(
        repository_root=document.repository_root,
        work_plan_path=document.work_plan_path,
        slice_id=document.slice_id,
        expected_relative_path=document.relative_path,
    )
    rendered = merge_structured_record_sections(
        current.markdown, projection.render_sections()
    )
    if rendered != current.markdown:
        atomic_write_file(current.slice_path, rendered)
    return rendered


def project_structured_work_plan_audit(
    document: WorkPlanDocument,
    projection: ArtifactAuditProjection | ArtifactReplayResult,
) -> str:
    """Append the record-native view to a work-plan or overall audit."""
    if isinstance(projection, ArtifactReplayResult):
        projection = ArtifactAuditProjection.from_replay(projection)
    current = validate_managed_work_plan_document(
        repository_root=document.repository_root,
        work_plan_path=document.work_plan_path,
    )
    rendered = merge_structured_record_sections(
        current.markdown, projection.render_sections()
    )
    if rendered != current.markdown:
        atomic_write_file(current.work_plan_path, rendered)
    return rendered


def merge_structured_record_sections(
    markdown: str,
    sections: Mapping[str, str],
) -> str:
    """Replace only the nested record projection inside every managed block.

    The surrounding State-v3 projection remains present during dual-write.  A
    complete prior record block is overwritten, while partial/duplicate marker
    edits are diagnosed instead of being interpreted as workflow facts.
    """
    if set(sections) != set(SECTION_KEYS):
        raise AuditTrailError("structured projection does not cover every managed section")
    rendered = markdown
    for key in MANAGED_SECTION_KEYS:
        ranges = _managed_ranges(rendered, require_all=True)
        start, end = ranges[key]
        begin = f"<!-- audit:{key}:begin -->"
        finish = f"<!-- audit:{key}:end -->"
        block = rendered[start:end]
        body_start = block.index(begin) + len(begin)
        body_end = block.rindex(finish)
        body = block[body_start:body_end].strip("\r\n")
        record_begin = f"<!-- artifact-records:{key}:begin -->"
        record_end = f"<!-- artifact-records:{key}:end -->"
        begin_count = body.count(record_begin)
        end_count = body.count(record_end)
        if begin_count != end_count or begin_count > 1:
            raise AuditTrailError(
                f"structured record projection markers are incomplete for {key}"
            )
        if begin_count:
            first = body.index(record_begin)
            last = body.index(record_end, first) + len(record_end)
            body = (body[:first] + body[last:]).strip("\r\n")
        projected = "\n".join(
            (
                record_begin,
                str(sections[key]).rstrip("\r\n"),
                record_end,
            )
        )
        replacement = "\n\n".join(part for part in (body, projected) if part)
        rendered = _replace_managed_body(rendered, key, replacement)
    try:
        return finalize_projection_document(rendered)
    except ArtifactProjectionError as exc:
        raise AuditTrailError(str(exc)) from exc


def strip_managed_audit_sections(markdown: str) -> str:
    """Return stable semantic text with managed projection bodies removed."""
    try:
        return canonical_semantic_markdown(markdown)
    except SemanticMarkdownError as exc:
        raise AuditTrailError(str(exc)) from exc


def strip_managed_work_plan_audit_appendix(markdown: str) -> str:
    """Remove the generic orchestrator appendix while preserving plan semantics."""
    try:
        document = parse_semantic_markdown(markdown)
        if not document.sections:
            return markdown.rstrip("\r\n") + "\n"
        if document.kind is not SemanticMarkdownKind.AUDIT_APPENDIX:
            raise SemanticMarkdownError(
                "<markdown>", "classification", "work plan has no managed appendix"
            )
        return document.semantic_text(remove_appendix=True)
    except SemanticMarkdownError as exc:
        raise AuditTrailError(str(exc)) from exc


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


def _managed_ranges(markdown: str, *, require_all: bool) -> dict[str, tuple[int, int]]:
    try:
        document = parse_semantic_markdown(
            markdown, require_managed=require_all
        )
    except SemanticMarkdownError as exc:
        raise AuditTrailError(str(exc)) from exc
    return {section.key: (section.start, section.end) for section in document.sections}


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
                f"### {reviewer.value.title()} · Runde {event.round_number} · "
                f"{'stopped' if result.stopped else 'approved' if result.approval else 'denied'} "
                f"(Ereignis {event.event_id})",
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
            blocks.extend((
                f"- Stop-Regel: `{_safe(result.stop_request.rule_id)}` — "
                f"{_prose_safe(result.stop_request.rationale)}",
                "- Remediation-Pfade: "
                + (
                    ", ".join(
                        f"`{_safe(path)}`"
                        for path in result.stop_request.remediation_paths
                    )
                    if result.stop_request.remediation_paths
                    else "keine"
                ),
            ))
        if result.evidence is not None:
            blocks.extend(
                (
                    f"- Prüfdimensionen: {_prose_safe(result.evidence.dimensions)}",
                    f"- Größtes Restrisiko: {_prose_safe(result.evidence.largest_residual_risk)}",
                    f"- Realistische Bruchbedingung: {_prose_safe(result.evidence.break_condition)}",
                )
            )
        own_findings = [
            finding for finding in result.findings if finding.origin.reporter is reviewer
        ]
        blocks.append(
            "- Eigene Findings: "
            + (", ".join(f"`{item.finding_id}`" for item in own_findings) or "keine")
        )
        blocks.append(
            "- Anker: "
            + (
                "; ".join(
                    f"`{_safe(anchor.anchor_id)}` ({_prose_safe(anchor.origin)}; "
                    f"Fixture: {_prose_safe(anchor.input_fixture)}; "
                    f"Erwartung: {_prose_safe(anchor.expected)}; "
                    f"Toleranz: {_prose_safe(anchor.tolerance)})"
                    for anchor in result.anchors
                )
                if result.anchors
                else "keine"
            )
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
                f"- Kurzresultat: {_prose_safe(item.summary)}",
                f"- Ausgabedigest: `{item.output_digest}`",
                "",
                "| Matrixbefehl | Status | Exitcode | Kompaktausgabe |",
                "|---|---|---:|---|",
            )
        )
        by_command = {record.command: record for record in item.records}
        for command in item.expected_commands:
            record = by_command.get(command)
            status = record.status.value if record is not None else "MISSING"
            exit_code = str(record.exit_code) if record is not None else "–"
            output = record.output if record is not None else "nicht ausgeführt"
            blocks.append(
                f"| {_safe(command)} | {status} | {exit_code} | {_safe(output)} |"
            )
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
                f"- Freigabezeitpunkt: {_safe(approval.approved_at or 'nicht erfasst')}",  # allowlist:german
                f"- Test-Diff-Fingerprint: `{_safe(approval.diff_fingerprint or 'nicht erfasst')}`",
                f"- Begründung: {_prose_safe(approval.rationale)}",
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
            f"  - Ereignis {event_id}: {_prose_safe(text)}"
            for event_id, text in pre_mortems
            if text is not None
        )
    else:
        blocks.append("- Pre-Mortems: keine erfasst.")
    return "\n".join(blocks)


def _latest_findings(events: tuple[AuditEvent, ...]) -> tuple[FindingRecord, ...]:
    try:
        return merge_history_snapshots(
            tuple(
                event.result.findings
                for event in events
                if isinstance(event, ReviewAuditEvent)
            )
        )
    except ValueError as exc:
        raise AuditTrailError(str(exc)) from exc


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
                f"- Finding: {_prose_safe(finding.summary)}",
                f"- Akzeptanztest: {_prose_safe(finding.acceptance_test)}",
                "- Statusbegründung: "
                + (_prose_safe(finding.status_rationale) if finding.status_rationale else "–"),
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
                f"{_prose_safe(response.rationale)}"
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
    open_ids = frozenset(project_open_set(findings).finding_ids)
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
            "erledigt: " + _prose_safe(finding.status_rationale)
            if finding.finding_id not in open_ids and finding.status_rationale
            else "offen"
        )
        rows.append(
            "| "
            + " | ".join(
                (
                    finding.finding_id,
                    finding.origin.reporter.value,
                    _prose_safe(finding.summary),
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
    validations = [
        event for event in projection.events if isinstance(event, ValidationAuditEvent)
    ]
    validation_status = validations[-1].attestation.status.value if validations else "NOT_RECORDED"
    return "\n".join(
        (
            f"- Implementierung bereit: `{_tri_state(projection.implementation_ready)}`",
            f"- Validierung: `{validation_status}`",
            f"- Claude-Freigabe: `{_review_state(claude)}`",  # allowlist:german
            f"- Red-State-Folgeslice: `{_safe(projection.red_state_followup_slice or 'NONE')}`",
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


_INLINE_PROSE_BOUNDARY = re.compile(
    r"(?<=[.!?])[ \t]+|(?<=\S)[ \t]+(?=(?:[-*+\u2022]|[0-9]+[.)])[ \t]+\S)"
)


def _prose_safe(value: str) -> str:
    normalized = str(value).replace("\r\n", "\n").replace("\r", "\n")
    structured = "\n".join(
        _INLINE_PROSE_BOUNDARY.sub("\n", line) for line in normalized.split("\n")
    )
    return _safe(structured)


def _require_event_identity(event_id: int, slice_id: int) -> None:
    _require_positive_int(event_id, "event_id")
    _require_positive_int(slice_id, "slice_id")


def _require_positive_int(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise AuditTrailError(f"{label} must be a 1-based integer")
