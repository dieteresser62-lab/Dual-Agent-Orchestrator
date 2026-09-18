"""Dormant native-agent decision types for the joint 67/68 cutover."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from finding_responsibility import FindingResponsibility


# This is intentionally the only switch for offering and accepting the native
# finding-decision additions.  The joint cutover changes this one value.
JOINT_67_68_NATIVE_CONTRACT_CUTOVER = False


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
    "NativeClosureKind",
    "NativeFindingClosure",
    "NativeRejectionReason",
    "NativeResponsibilityProposal",
    "NativeResponsibilityRoute",
    "native_finding_decisions_enabled",
]
