"""Immutable, strictly typed responsibility targets for Finding routing.

The types in this module are deliberately independent from artifact records.
They define identity only; record-chain context validation remains with the
Finding reducer, where run, plan, Slice, and (after the joint cutover) family
facts are available.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
import re
from typing import Any, ClassVar, Mapping, TypeAlias
import copy


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ACCEPTANCE_CRITERION_ID_RE = re.compile(r"^ac-[0-9a-f]{64}$")


class ResponsibilityKind(StrEnum):
    SLICE = "SLICE"
    BRANCH_PLANNING = "BRANCH_PLANNING"
    PLAN_REVISION = "PLAN_REVISION"


@dataclass(frozen=True, slots=True)
class SliceResponsibility:
    target_run_id: str
    approved_plan_commit: str
    slice_id: str
    acceptance_criterion_id: str | None = None
    responsibility_kind: ClassVar[ResponsibilityKind] = ResponsibilityKind.SLICE

    def __post_init__(self) -> None:
        _require_identifier(self.target_run_id, "SLICE.target_run_id")
        if not isinstance(self.approved_plan_commit, str) or _GIT_SHA_RE.fullmatch(
            self.approved_plan_commit
        ) is None:
            raise ValueError(
                "SLICE.approved_plan_commit must be a lowercase 40-character Git SHA"
            )
        _require_identifier(self.slice_id, "SLICE.slice_id")
        if (
            self.acceptance_criterion_id is not None
            and (
                not isinstance(self.acceptance_criterion_id, str)
                or _ACCEPTANCE_CRITERION_ID_RE.fullmatch(
                    self.acceptance_criterion_id
                ) is None
            )
        ):
            raise ValueError(
                "SLICE.acceptance_criterion_id must be ac- followed by a lowercase SHA-256 digest"
            )


@dataclass(frozen=True, slots=True)
class BranchPlanningResponsibility:
    family_id: str
    cycle_number: int
    responsibility_kind: ClassVar[ResponsibilityKind] = (
        ResponsibilityKind.BRANCH_PLANNING
    )

    def __post_init__(self) -> None:
        _require_identifier(self.family_id, "BRANCH_PLANNING.family_id")
        _require_positive(self.cycle_number, "BRANCH_PLANNING.cycle_number")


@dataclass(frozen=True, slots=True)
class PlanRevisionResponsibility:
    run_id: str
    plan_path: str
    plan_digest: str
    revision: int
    responsibility_kind: ClassVar[ResponsibilityKind] = (
        ResponsibilityKind.PLAN_REVISION
    )

    def __post_init__(self) -> None:
        _require_identifier(self.run_id, "PLAN_REVISION.run_id")
        _require_path(self.plan_path, "PLAN_REVISION.plan_path")
        if not isinstance(self.plan_digest, str) or _SHA256_RE.fullmatch(
            self.plan_digest
        ) is None:
            raise ValueError(
                "PLAN_REVISION.plan_digest must be a lowercase SHA-256 digest"
            )
        _require_positive(self.revision, "PLAN_REVISION.revision")


FindingResponsibility: TypeAlias = (
    SliceResponsibility
    | BranchPlanningResponsibility
    | PlanRevisionResponsibility
)


_FIELDS_BY_KIND = {
    ResponsibilityKind.SLICE: frozenset(
        {"responsibility_kind", "target_run_id", "approved_plan_commit", "slice_id"}
    ),
    ResponsibilityKind.BRANCH_PLANNING: frozenset(
        {"responsibility_kind", "family_id", "cycle_number"}
    ),
    ResponsibilityKind.PLAN_REVISION: frozenset(
        {"responsibility_kind", "run_id", "plan_path", "plan_digest", "revision"}
    ),
}


def responsibility_document(
    responsibility: FindingResponsibility,
) -> dict[str, str | int]:
    """Return the deterministic discriminator-first wire representation."""

    if isinstance(responsibility, SliceResponsibility):
        document = {
            "responsibility_kind": ResponsibilityKind.SLICE.value,
            "target_run_id": responsibility.target_run_id,
            "approved_plan_commit": responsibility.approved_plan_commit,
            "slice_id": responsibility.slice_id,
        }
        if responsibility.acceptance_criterion_id is not None:
            document["acceptance_criterion_id"] = (
                responsibility.acceptance_criterion_id
            )
        return document
    if isinstance(responsibility, BranchPlanningResponsibility):
        return {
            "responsibility_kind": ResponsibilityKind.BRANCH_PLANNING.value,
            "family_id": responsibility.family_id,
            "cycle_number": responsibility.cycle_number,
        }
    if isinstance(responsibility, PlanRevisionResponsibility):
        return {
            "responsibility_kind": ResponsibilityKind.PLAN_REVISION.value,
            "run_id": responsibility.run_id,
            "plan_path": responsibility.plan_path,
            "plan_digest": responsibility.plan_digest,
            "revision": responsibility.revision,
        }
    raise ValueError(
        f"responsibility has unsupported value type {type(responsibility).__name__}"
    )


def responsibility_json_schema(*, union_keyword: str = "anyOf") -> dict[str, Any]:
    """Return the closed native wire schema for the three responsibility kinds.

    Keeping this schema beside the parser and document projection prevents native
    agent contracts from growing a second, independently maintained definition of
    responsibility targets.
    """

    if union_keyword not in {"anyOf", "oneOf"}:
        raise ValueError("responsibility schema requires anyOf or oneOf")

    return copy.deepcopy(
        {
            union_keyword: [
                {
                    "type": "object",
                    "properties": {
                        "responsibility_kind": {
                            "type": "string",
                            "const": ResponsibilityKind.SLICE.value,
                        },
                        "target_run_id": {"type": "string", "minLength": 1},
                        "approved_plan_commit": {
                            "type": "string",
                            "pattern": "^[0-9a-f]{40}$",
                        },
                        "slice_id": {"type": "string", "minLength": 1},
                        "acceptance_criterion_id": {
                            "type": "string",
                            "pattern": "^ac-[0-9a-f]{64}$",
                        },
                    },
                    "required": [
                        "responsibility_kind",
                        "target_run_id",
                        "approved_plan_commit",
                        "slice_id",
                    ],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {
                        "responsibility_kind": {
                            "type": "string",
                            "const": ResponsibilityKind.BRANCH_PLANNING.value,
                        },
                        "family_id": {"type": "string", "minLength": 1},
                        "cycle_number": {"type": "integer", "minimum": 1},
                    },
                    "required": [
                        "responsibility_kind",
                        "family_id",
                        "cycle_number",
                    ],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {
                        "responsibility_kind": {
                            "type": "string",
                            "const": ResponsibilityKind.PLAN_REVISION.value,
                        },
                        "run_id": {"type": "string", "minLength": 1},
                        "plan_path": {
                            "type": "string",
                            "pattern": (
                                "^(?!/)(?!.*(?:^|/)\\.{1,2}(?:/|$))"
                                "(?!.*\\\\)[^/]+(?:/[^/]+)*$"
                            ),
                        },
                        "plan_digest": {
                            "type": "string",
                            "pattern": "^[0-9a-f]{64}$",
                        },
                        "revision": {"type": "integer", "minimum": 1},
                    },
                    "required": [
                        "responsibility_kind",
                        "run_id",
                        "plan_path",
                        "plan_digest",
                        "revision",
                    ],
                    "additionalProperties": False,
                },
            ]
        }
    )


def parse_responsibility(raw: Mapping[str, Any]) -> FindingResponsibility:
    """Parse one closed responsibility document, rejecting unknown fields."""

    if not isinstance(raw, Mapping):
        raise ValueError("responsibility must be an object")
    if "responsibility_kind" not in raw:
        raise ValueError(
            "responsibility is missing required field responsibility_kind"
        )
    raw_kind = raw["responsibility_kind"]
    try:
        kind = ResponsibilityKind(raw_kind)
    except (TypeError, ValueError):
        raise ValueError(
            f"responsibility_kind is unknown: {raw_kind!r}"
        ) from None
    _require_exact_fields(raw, kind)
    if kind is ResponsibilityKind.SLICE:
        return SliceResponsibility(
            target_run_id=raw["target_run_id"],
            approved_plan_commit=raw["approved_plan_commit"],
            slice_id=raw["slice_id"],
            acceptance_criterion_id=raw.get("acceptance_criterion_id"),
        )
    if kind is ResponsibilityKind.BRANCH_PLANNING:
        return BranchPlanningResponsibility(
            family_id=raw["family_id"],
            cycle_number=raw["cycle_number"],
        )
    return PlanRevisionResponsibility(
        run_id=raw["run_id"],
        plan_path=raw["plan_path"],
        plan_digest=raw["plan_digest"],
        revision=raw["revision"],
    )


def _require_exact_fields(raw: Mapping[str, Any], kind: ResponsibilityKind) -> None:
    expected = _FIELDS_BY_KIND[kind]
    observed = frozenset(raw)
    missing = sorted(expected - observed)
    if missing:
        raise ValueError(
            f"{kind.value} responsibility is missing required field {missing[0]}"
        )
    allowed = (
        expected | {"acceptance_criterion_id"}
        if kind is ResponsibilityKind.SLICE
        else expected
    )
    foreign = sorted(observed - allowed)
    if foreign:
        raise ValueError(
            f"{kind.value} responsibility contains foreign field {foreign[0]}"
        )


def _require_identifier(value: str, name: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError(
            f"{name} must be a non-empty canonical identifier"
        )


def _require_positive(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _require_path(value: str, name: str) -> None:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(
            f"{name} must be a non-empty repository-relative POSIX path"
        )
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or value != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(
            f"{name} must be a canonical repository-relative POSIX path"
        )


__all__ = [
    "BranchPlanningResponsibility",
    "FindingResponsibility",
    "PlanRevisionResponsibility",
    "ResponsibilityKind",
    "SliceResponsibility",
    "parse_responsibility",
    "responsibility_document",
    "responsibility_json_schema",
]
