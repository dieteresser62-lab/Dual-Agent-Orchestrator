"""Native-agent finding decisions installed by the joint 67/68 cutover."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from pathlib import PurePosixPath
import re

from finding_order import sorted_finding_ids
from finding_responsibility import FindingResponsibility


# Historical marker for the single joint 67/68 cutover.  Production no longer
# branches on this value: the old producer contract is unsupported.
JOINT_67_68_NATIVE_CONTRACT_CUTOVER = True
MAX_REMEDIATION_ROUNDS = 64


class NativeClosureKind(StrEnum):
    FIXED = "fixed"
    REJECTED = "rejected"


class NativeRejectionReason(StrEnum):
    NO_DEFECT = "no_defect"
    OUT_OF_SCOPE = "out_of_scope"
    ALREADY_FIXED = "already_fixed"


class PlanTreatmentDecisionKind(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class PlanCompletionKind(StrEnum):
    IMPLEMENTATION_REQUIRED = "IMPLEMENTATION_REQUIRED"
    NO_IMPLEMENTATION_REQUIRED = "NO_IMPLEMENTATION_REQUIRED"


@dataclass(frozen=True, slots=True)
class NoCodeEvidenceAnchor:
    """Content-stable authority for one reviewer-accepted No-Code decision.

    ``provenance_fingerprint`` records where the decision was made, but is
    intentionally excluded from ``stability_sha256``.  The remaining fields
    decide whether a later observation is the same closed occurrence or a new
    Finding generation.
    """

    rejection_reason: NativeRejectionReason
    provenance_fingerprint: str
    evidence_paths: tuple[str, ...]
    evidence_content_sha256: str
    task_sha256: str | None = None
    scope_sha256: str | None = None
    affected_paths: tuple[str, ...] = ()
    affected_content_sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.rejection_reason, NativeRejectionReason):
            raise ValueError("No-Code evidence anchor requires a typed reason")
        _require_sha256(
            self.provenance_fingerprint,
            "No-Code evidence provenance fingerprint",
        )
        _require_paths(self.evidence_paths, "No-Code evidence paths")
        _require_sha256(
            self.evidence_content_sha256,
            "No-Code evidence content digest",
        )
        if self.rejection_reason is NativeRejectionReason.OUT_OF_SCOPE:
            _require_sha256(self.task_sha256, "out-of-scope task digest")
            _require_sha256(self.scope_sha256, "out-of-scope scope digest")
            if self.affected_paths or self.affected_content_sha256 is not None:
                raise ValueError(
                    "out-of-scope evidence anchor forbids affected-file fields"
                )
        elif self.rejection_reason is NativeRejectionReason.ALREADY_FIXED:
            if self.task_sha256 is not None or self.scope_sha256 is not None:
                raise ValueError(
                    "already-fixed evidence anchor forbids task and scope digests"
                )
            _require_paths(self.affected_paths, "already-fixed affected paths")
            _require_sha256(
                self.affected_content_sha256,
                "already-fixed affected content digest",
            )
        else:
            if any(
                value is not None
                for value in (
                    self.task_sha256,
                    self.scope_sha256,
                    self.affected_content_sha256,
                )
            ) or self.affected_paths:
                raise ValueError(
                    "no-defect evidence anchor forbids reason-specific digests"
                )

    @property
    def stability_sha256(self) -> str:
        document = {
            "rejection_reason": self.rejection_reason.value,
            "evidence_paths": list(self.evidence_paths),
            "evidence_content_sha256": self.evidence_content_sha256,
            "task_sha256": self.task_sha256,
            "scope_sha256": self.scope_sha256,
            "affected_paths": list(self.affected_paths),
            "affected_content_sha256": self.affected_content_sha256,
        }
        return hashlib.sha256(
            json.dumps(
                document,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class ClosedFindingReviewBinding:
    finding_id: str
    signature: str
    source_plan_assignment_record_id: str
    original_anchor: NoCodeEvidenceAnchor
    current_anchor: NoCodeEvidenceAnchor

    def __post_init__(self) -> None:
        if re.fullmatch(r"C-(0[1-9]|[1-9][0-9]*)", self.finding_id) is None:
            raise ValueError("closed Finding binding requires a canonical Finding ID")
        _require_sha256(self.signature, "closed Finding signature")
        if re.fullmatch(
            r"ar1-[0-9a-f]{64}", self.source_plan_assignment_record_id
        ) is None:
            raise ValueError(
                "closed Finding binding requires its PlanAssignment record"
            )
        if not isinstance(self.original_anchor, NoCodeEvidenceAnchor) or not isinstance(
            self.current_anchor, NoCodeEvidenceAnchor
        ):
            raise ValueError("closed Finding binding requires typed evidence anchors")
        if (
            self.original_anchor.rejection_reason
            is not self.current_anchor.rejection_reason
        ):
            raise ValueError("closed Finding evidence reason changed")

    @property
    def anchor_unchanged(self) -> bool:
        return (
            self.original_anchor.stability_sha256
            == self.current_anchor.stability_sha256
        )


@dataclass(frozen=True, slots=True)
class PlanTreatmentDecision:
    signature: str
    decision: PlanTreatmentDecisionKind
    rationale: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.signature, str)
            or len(self.signature) != 64
            or any(character not in "0123456789abcdef" for character in self.signature)
        ):
            raise ValueError(
                "plan treatment decision signature must be a lowercase SHA-256 digest"
            )
        if not isinstance(self.decision, PlanTreatmentDecisionKind):
            raise ValueError("plan treatment decision must be typed")
        if not isinstance(self.rationale, str) or not self.rationale.strip():
            raise ValueError("plan treatment decision rationale must not be empty")


class PlanTreatmentKind(StrEnum):
    IMPLEMENTATION = "implementation"
    NO_CODE = "no_code"


@dataclass(frozen=True, slots=True)
class PlanTreatmentProposal:
    """One implementer-proposed treatment for one canonical open signature."""

    signature: str
    finding_ids: tuple[str, ...]
    treatment_kind: PlanTreatmentKind
    closing_slice_ids: tuple[int, ...] = ()
    no_code_reason: NativeRejectionReason | None = None
    evidence: str | None = None
    evidence_paths: tuple[str, ...] = ()
    affected_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.signature, str) or re.fullmatch(
            r"[0-9a-f]{64}", self.signature
        ) is None:
            raise ValueError(
                "plan treatment signature must be a lowercase SHA-256 digest"
            )
        if not self.finding_ids or self.finding_ids != sorted_finding_ids(
            self.finding_ids
        ):
            raise ValueError(
                "plan treatment finding_ids must be non-empty, sorted, and unique"
            )
        if not isinstance(self.treatment_kind, PlanTreatmentKind):
            raise ValueError("plan treatment kind must be typed")
        if self.treatment_kind is PlanTreatmentKind.IMPLEMENTATION:
            if len(self.closing_slice_ids) != 1:
                raise ValueError(
                    "implementation treatment requires exactly one closing Slice"
                )
            closing_slice_id = self.closing_slice_ids[0]
            if (
                isinstance(closing_slice_id, bool)
                or not isinstance(closing_slice_id, int)
                or closing_slice_id < 1
            ):
                raise ValueError("closing Slice id must be a positive integer")
            if (
                self.no_code_reason is not None
                or self.evidence is not None
                or self.evidence_paths
                or self.affected_paths
            ):
                raise ValueError(
                    "implementation treatment forbids No-Code disposition fields"
                )
            return
        if self.closing_slice_ids:
            raise ValueError("No-Code disposition forbids closing Slice ids")
        if not isinstance(self.no_code_reason, NativeRejectionReason):
            raise ValueError("No-Code disposition requires a typed reason")
        if not isinstance(self.evidence, str) or not self.evidence.strip():
            raise ValueError("No-Code disposition evidence must not be empty")
        _require_paths(self.evidence_paths, "No-Code disposition evidence_paths")
        if self.no_code_reason is NativeRejectionReason.ALREADY_FIXED:
            _require_paths(
                self.affected_paths,
                "already-fixed disposition affected_paths",
            )
        elif self.affected_paths:
            raise ValueError(
                "only an already-fixed disposition may name affected_paths"
            )


def content_path_digest(
    paths: tuple[str, ...], content_sha256_by_path: dict[str, str]
) -> str:
    """Digest exact path/content-digest pairs without observing Git identity."""

    _require_paths(paths, "content digest paths")
    if set(content_sha256_by_path) != set(paths):
        raise ValueError("content digest inputs differ from their exact path set")
    document = []
    for path in paths:
        digest = content_sha256_by_path[path]
        _require_sha256(digest, f"content digest for {path}")
        document.append({"path": path, "sha256": digest})
    return hashlib.sha256(
        json.dumps(
            document,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _require_sha256(value: object, label: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")


def _require_paths(values: tuple[str, ...], label: str) -> None:
    if not values or values != tuple(sorted(set(values))):
        raise ValueError(f"{label} must be non-empty, sorted, and unique")
    for value in values:
        path = PurePosixPath(value)
        if (
            not isinstance(value, str)
            or not value
            or path.is_absolute()
            or ".." in path.parts
            or path.as_posix() != value
        ):
            raise ValueError(f"{label} contains a non-canonical repository path")


@dataclass(frozen=True, slots=True)
class NativeFindingClosure:
    kind: NativeClosureKind
    rejection_reason: NativeRejectionReason | None = None
    evidence: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, NativeClosureKind):
            raise TypeError("native finding closure kind must be typed")
        if self.kind is NativeClosureKind.FIXED:
            if self.rejection_reason is not None or self.evidence is not None:
                raise ValueError("fixed closure forbids rejection fields")
            return
        if not isinstance(self.rejection_reason, NativeRejectionReason):
            raise ValueError("rejected closure requires a typed rejection reason")
        if not isinstance(self.evidence, str) or not self.evidence.strip():
            raise ValueError("rejected closure requires named evidence")


@dataclass(frozen=True, slots=True)
class NativeResponsibilityRoute:
    finding_id: str
    responsibility: FindingResponsibility
    rationale: str


@dataclass(frozen=True, slots=True)
class NativeResponsibilityProposal:
    finding_id: str
    responsibility: FindingResponsibility
    rationale: str


__all__ = [
    "JOINT_67_68_NATIVE_CONTRACT_CUTOVER",
    "MAX_REMEDIATION_ROUNDS",
    "NativeClosureKind",
    "NativeFindingClosure",
    "NativeRejectionReason",
    "NativeResponsibilityProposal",
    "NativeResponsibilityRoute",
    "ClosedFindingReviewBinding",
    "NoCodeEvidenceAnchor",
    "PlanCompletionKind",
    "PlanTreatmentDecision",
    "PlanTreatmentDecisionKind",
    "PlanTreatmentKind",
    "PlanTreatmentProposal",
    "content_path_digest",
]
