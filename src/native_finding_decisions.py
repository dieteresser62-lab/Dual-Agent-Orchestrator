"""Typed native-agent Finding closure decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


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
            if any(
                item is not None
                for item in (self.rejection_reason, self.evidence)
            ):
                raise ValueError("fixed closure forbids rejection fields")
            return
        if not isinstance(self.rejection_reason, NativeRejectionReason):
            raise ValueError("rejected closure requires a typed rejection reason")
        if not isinstance(self.evidence, str) or not self.evidence.strip():
            raise ValueError("rejected closure requires named evidence")


__all__ = [
    "NativeClosureKind",
    "NativeFindingClosure",
    "NativeRejectionReason",
]
