"""Runtime audit event identities and stable Slice document paths.

Human documents are rendered from accepted records by readable_audit.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Sequence, TypeAlias

from contracts import AgentRole, ContractResult, FindingRecord, ValidationAttestation
from semantic_markdown import canonical_semantic_markdown, parse_semantic_markdown, SemanticMarkdownKind


class AuditTrailError(ValueError):
    """A runtime audit event or document path is invalid."""


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
            origin not in {"FINAL", "DISCOVERY"} and not re.fullmatch(r"0*[1-9][0-9]*", origin)
            for origin in self.allowed_finding_origins
        ):
            raise AuditTrailError(
                "review audit finding origins must be FINAL, DISCOVERY, or 1-based Slice ids"
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
        if (
            not self.result.stopped
            and self.result.approval is None
            and self.result.delivery_kind != "final_review_completed"
        ):
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
            and not self.result.own_open_findings
            and not self.is_final_review
        ):
            raise AuditTrailError(
                "a denied review requires a reviewer-owned open Finding or BLOCKER"
            )


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
            | ({"DISCOVERY"} if is_final_review else set())
        )
    )


AuditEvent: TypeAlias = ValidationAuditEvent | ReviewAuditEvent


@dataclass(frozen=True)
class AuditEventSequence:
    """Validate a work unit's ordered runtime history without rendering it."""

    slice_id: int
    events: tuple[AuditEvent, ...] = ()

    def __post_init__(self) -> None:
        _require_positive_int(self.slice_id, "slice_id")
        if tuple(event.event_id for event in self.events) != tuple(range(1, len(self.events) + 1)):
            raise AuditTrailError("audit event ids must be ordered, unique, contiguous, and 1-based")
        if any(event.slice_id != self.slice_id for event in self.events):
            raise AuditTrailError("all audit events must belong to the sequence slice")
        attestations = {
            event.attestation.attestation_id: event.attestation
            for event in self.events if isinstance(event, ValidationAuditEvent)
        }
        for event in self.events:
            if isinstance(event, ReviewAuditEvent) and event.result.validation is not None:
                if attestations.get(event.result.validation.attestation_id) != event.result.validation:
                    raise AuditTrailError("review validation binding differs from the preceding attestation")

    def latest_review(self, reviewer: AgentRole) -> ReviewAuditEvent | None:
        return next((event for event in reversed(self.events) if isinstance(event, ReviewAuditEvent) and event.result.reviewer is reviewer), None)


def _require_event_identity(event_id: int, slice_id: int) -> None:
    _require_positive_int(event_id, "event_id")
    _require_positive_int(slice_id, "slice_id")


def _require_positive_int(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise AuditTrailError(f"{label} must be a 1-based integer")


def strip_managed_audit_sections(markdown: str) -> str:
    return canonical_semantic_markdown(markdown)


def strip_managed_work_plan_audit_appendix(markdown: str) -> str:
    document = parse_semantic_markdown(markdown)
    if not document.sections:
        return markdown.rstrip("\r\n") + "\n"
    if document.kind not in {SemanticMarkdownKind.AUDIT_APPENDIX, SemanticMarkdownKind.READABLE_APPENDIX}:
        raise AuditTrailError("work plan has no managed appendix")
    return document.semantic_text(remove_appendix=True)


def semantic_audit_fingerprint(markdown: str) -> str:
    return hashlib.sha256(strip_managed_audit_sections(markdown).encode("utf-8")).hexdigest()
