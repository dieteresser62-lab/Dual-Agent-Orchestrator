"""Dormant native-agent decision types for the joint 67/68 cutover."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

from finding_order import sorted_finding_ids
from finding_responsibility import FindingResponsibility


# This is intentionally the only switch for offering and accepting the native
# finding-decision additions.  The joint cutover changes this one value.
JOINT_67_68_NATIVE_CONTRACT_CUTOVER = False
MAX_REMEDIATION_ROUNDS = 64


def native_finding_decisions_enabled() -> bool:
    if not isinstance(JOINT_67_68_NATIVE_CONTRACT_CUTOVER, bool):
        raise RuntimeError("joint 67/68 native contract cutover must be boolean")
    return JOINT_67_68_NATIVE_CONTRACT_CUTOVER


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
            if self.no_code_reason is not None or self.evidence is not None:
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
    "PlanTreatmentDecision",
    "PlanTreatmentDecisionKind",
    "PlanTreatmentKind",
    "PlanTreatmentProposal",
    "native_finding_decisions_enabled",
]
