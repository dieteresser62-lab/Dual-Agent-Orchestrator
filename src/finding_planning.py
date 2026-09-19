"""Dormant E3/E4 contracts for finding-remediation planning and convergence.

The module deliberately separates Finding identity from treatment identity:
Finding IDs retain lifecycle ownership, while one canonical signature receives
exactly one remediation treatment.  Round convergence is cohort based.  No
cardinality comparison participates in the decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import logging
import re
from typing import Iterable, Sequence

from contracts import FindingRecord, PlannedSlice
from finding_order import sorted_finding_ids
from finding_reducer import project_open_set
from finding_signature import (
    finding_record_signature,
    finding_signature,
    mentioned_repository_paths,
)
from native_finding_decisions import (
    MAX_REMEDIATION_ROUNDS,
    PlanCompletionKind,
    PlanTreatmentDecision,
    PlanTreatmentDecisionKind,
    PlanTreatmentKind,
    PlanTreatmentProposal,
)


logger = logging.getLogger(__name__)

REMEDIATION_ROUND_LIMIT_RULE_ID = "REMEDIATION_ROUND_LIMIT"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class RemediationRoundOutcome(StrEnum):
    ROUND_FAILED = "round_failed"
    FAMILY_ACCEPTED = "family_accepted"
    NEXT_ROUND = "next_round"
    ROUND_LIMIT_STOP = "round_limit_stop"


@dataclass(frozen=True, slots=True)
class FindingSignatureGroup:
    signature: str
    finding_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_sha256(self.signature, "signature")
        _require_finding_ids(self.finding_ids, "finding_ids")


@dataclass(frozen=True, slots=True)
class RemediationRoundEvaluation:
    remediation_round_number: int
    inherited_signatures: tuple[str, ...]
    unresolved_inherited_signatures: tuple[str, ...]
    new_signatures: tuple[str, ...]
    next_round_finding_ids: tuple[str, ...]
    outcome: RemediationRoundOutcome
    absolute_round_limit: int
    stop_rule_id: str | None
    log_message: str


def canonical_open_signature_groups(
    findings: Sequence[FindingRecord],
) -> tuple[FindingSignatureGroup, ...]:
    """Deduplicate the open ledger by signature without losing Finding IDs."""

    grouped: dict[str, list[str]] = {}
    for finding in project_open_set(tuple(findings)).findings:
        grouped.setdefault(finding_record_signature(finding), []).append(
            finding.finding_id
        )
    return tuple(
        FindingSignatureGroup(signature, sorted_finding_ids(finding_ids))
        for signature, finding_ids in sorted(grouped.items())
    )


def validate_plan_treatment_coverage(
    signature_groups: Sequence[FindingSignatureGroup],
    treatments: Sequence[PlanTreatmentProposal],
    planned_slices: Sequence[PlannedSlice],
) -> tuple[str, ...]:
    """Enforce E3 and return the exact union of implementation Slice paths."""

    expected = tuple(signature_groups)
    if tuple(item.signature for item in expected) != tuple(
        sorted(item.signature for item in expected)
    ) or len(expected) != len({item.signature for item in expected}):
        raise ValueError("canonical signature groups must be sorted and unique")
    treatment_tuple = tuple(treatments)
    signatures = tuple(item.signature for item in treatment_tuple)
    if signatures != tuple(sorted(signatures)) or len(signatures) != len(
        set(signatures)
    ):
        raise ValueError(
            "plan treatments must contain exactly one sorted treatment per signature"
        )
    expected_by_signature = {item.signature: item.finding_ids for item in expected}
    missing = tuple(sorted(set(expected_by_signature) - set(signatures)))
    foreign = tuple(sorted(set(signatures) - set(expected_by_signature)))
    if missing:
        raise ValueError(
            f"plan treatment coverage is missing signature {missing[0]}"
        )
    if foreign:
        raise ValueError(
            f"plan treatment coverage contains foreign signature {foreign[0]}"
        )
    for treatment in treatment_tuple:
        if treatment.finding_ids != expected_by_signature[treatment.signature]:
            raise ValueError(
                "plan treatment Finding IDs differ from canonical signature group "
                f"{treatment.signature}"
            )
    slices = tuple(planned_slices)
    slice_ids = tuple(item.slice_id for item in slices)
    if slice_ids and slice_ids != tuple(range(1, len(slice_ids) + 1)):
        raise ValueError("implementation Slices must be contiguous and 1-based")
    available = frozenset(slice_ids)
    for treatment in treatment_tuple:
        if (
            treatment.treatment_kind is PlanTreatmentKind.IMPLEMENTATION
            and treatment.closing_slice_ids[0] not in available
        ):
            raise ValueError(
                "implementation treatment closing Slice is absent from the plan"
            )
    return tuple(
        sorted({path for planned_slice in slices for path in planned_slice.scope_paths})
    )


def validate_plan_treatment_decisions(
    treatments: Sequence[PlanTreatmentProposal],
    decisions: Sequence[PlanTreatmentDecision],
    *,
    plan_approved: bool,
) -> None:
    """Require one explicit reviewer decision for every proposed treatment."""

    treatment_signatures = tuple(item.signature for item in treatments)
    if treatment_signatures != tuple(sorted(set(treatment_signatures))):
        raise ValueError(
            "plan treatments must be sorted and unique by signature"
        )
    decision_tuple = tuple(decisions)
    decision_signatures = tuple(item.signature for item in decision_tuple)
    if decision_signatures != tuple(sorted(decision_signatures)) or len(
        decision_signatures
    ) != len(set(decision_signatures)):
        raise ValueError(
            "plan treatment decisions must be sorted and unique by signature"
        )
    missing = tuple(sorted(set(treatment_signatures) - set(decision_signatures)))
    foreign = tuple(sorted(set(decision_signatures) - set(treatment_signatures)))
    if missing:
        raise ValueError(
            f"plan review omitted an explicit decision for signature {missing[0]}"
        )
    if foreign:
        raise ValueError(
            f"plan review decided a foreign treatment signature {foreign[0]}"
        )
    if plan_approved:
        rejected = tuple(
            item.signature
            for item in decision_tuple
            if item.decision is not PlanTreatmentDecisionKind.ACCEPTED
        )
        if rejected:
            raise ValueError(
                "approved plan contains reviewer-rejected treatment signature "
                f"{rejected[0]}"
            )


def validate_plan_completion(
    treatments: Sequence[PlanTreatmentProposal],
    planned_slices: Sequence[PlannedSlice],
    completion: PlanCompletionKind,
) -> None:
    """Bind E7's explicit completion to the exact treatment partition."""

    if not isinstance(completion, PlanCompletionKind):
        raise ValueError("plan completion must be typed")
    implementation = tuple(
        item
        for item in treatments
        if item.treatment_kind is PlanTreatmentKind.IMPLEMENTATION
    )
    slices = tuple(planned_slices)
    if completion is PlanCompletionKind.NO_IMPLEMENTATION_REQUIRED:
        if implementation:
            raise ValueError(
                "NO_IMPLEMENTATION_REQUIRED forbids implementation treatments"
            )
        if slices:
            raise ValueError(
                "NO_IMPLEMENTATION_REQUIRED forbids implementation Slices"
            )
        return
    if not slices:
        raise ValueError("IMPLEMENTATION_REQUIRED requires at least one Slice")


def evaluate_remediation_round(
    *,
    remediation_round_number: int,
    inherited_signatures: Sequence[str],
    unresolved_inherited_signatures: Sequence[str],
    new_findings: Sequence[tuple[str, str, str]],
) -> RemediationRoundEvaluation:
    """Evaluate E4 solely from the inherited and newly discovered cohorts.

    ``new_findings`` contains ``(finding_id, summary, acceptance_test)`` tuples
    copied from one completed branch-discovery record.  Total Finding counts are
    intentionally never compared.
    """

    if (
        isinstance(remediation_round_number, bool)
        or not isinstance(remediation_round_number, int)
        or remediation_round_number < 1
        or remediation_round_number > MAX_REMEDIATION_ROUNDS
    ):
        raise ValueError(
            "remediation round number must be between 1 and "
            f"{MAX_REMEDIATION_ROUNDS}"
        )
    inherited = _canonical_signatures(inherited_signatures, "inherited signatures")
    unresolved = _canonical_signatures(
        unresolved_inherited_signatures,
        "unresolved inherited signatures",
        allow_empty=True,
    )
    if not set(unresolved).issubset(inherited):
        raise ValueError("unresolved inherited signatures are not a subset of S_r")
    new_by_signature: dict[str, str] = {}
    for finding_id, summary, acceptance_test in new_findings:
        if not isinstance(finding_id, str) or not finding_id:
            raise ValueError("new cohort Finding ID must not be empty")
        signature = finding_signature(
            acceptance_test,
            mentioned_repository_paths(summary, acceptance_test),
        )
        if signature in new_by_signature:
            raise ValueError(
                "branch discovery new cohort contains a duplicate signature"
            )
        new_by_signature[signature] = finding_id
    new_signatures = tuple(sorted(new_by_signature))
    new_ids = sorted_finding_ids(new_by_signature.values())

    if unresolved:
        outcome = RemediationRoundOutcome.ROUND_FAILED
        next_ids: tuple[str, ...] = ()
        stop_rule = None
        message = (
            f"remediation round {remediation_round_number} failed: "
            f"unresolved_S_r={len(unresolved)}; absolute_round_limit="
            f"{MAX_REMEDIATION_ROUNDS}; branch discovery must not advance"
        )
    elif not new_signatures:
        outcome = RemediationRoundOutcome.FAMILY_ACCEPTED
        next_ids = ()
        stop_rule = None
        message = (
            f"remediation round {remediation_round_number} completed: S_r=0 N_r=0; "
            f"family accepted; absolute_round_limit={MAX_REMEDIATION_ROUNDS}"
        )
    elif remediation_round_number >= MAX_REMEDIATION_ROUNDS:
        outcome = RemediationRoundOutcome.ROUND_LIMIT_STOP
        next_ids = ()
        stop_rule = REMEDIATION_ROUND_LIMIT_RULE_ID
        message = (
            f"{REMEDIATION_ROUND_LIMIT_RULE_ID}: remediation round "
            f"{remediation_round_number} reached absolute_round_limit="
            f"{MAX_REMEDIATION_ROUNDS}; S_r=0 N_r={len(new_signatures)}"
        )
    else:
        outcome = RemediationRoundOutcome.NEXT_ROUND
        next_ids = new_ids
        stop_rule = None
        message = (
            f"remediation round {remediation_round_number} completed: S_r=0 "
            f"N_r={len(new_signatures)}; N_r becomes round "
            f"{remediation_round_number + 1} input; absolute_round_limit="
            f"{MAX_REMEDIATION_ROUNDS}"
        )
    log = logger.warning if stop_rule is not None else logger.info
    log(message)
    return RemediationRoundEvaluation(
        remediation_round_number=remediation_round_number,
        inherited_signatures=inherited,
        unresolved_inherited_signatures=unresolved,
        new_signatures=new_signatures,
        next_round_finding_ids=next_ids,
        outcome=outcome,
        absolute_round_limit=MAX_REMEDIATION_ROUNDS,
        stop_rule_id=stop_rule,
        log_message=message,
    )


def _canonical_signatures(
    values: Iterable[str], label: str, *, allow_empty: bool = False
) -> tuple[str, ...]:
    normalized = tuple(values)
    if not normalized and not allow_empty:
        raise ValueError(f"{label} must not be empty")
    if normalized != tuple(sorted(set(normalized))):
        raise ValueError(f"{label} must be sorted and unique")
    for value in normalized:
        _require_sha256(value, label)
    return normalized


def _require_sha256(value: str, label: str) -> None:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")


def _require_finding_ids(values: tuple[str, ...], label: str) -> None:
    if not values or values != sorted_finding_ids(values):
        raise ValueError(f"{label} must be non-empty, sorted, and unique")


__all__ = [
    "FindingSignatureGroup",
    "MAX_REMEDIATION_ROUNDS",
    "PlanTreatmentDecision",
    "PlanTreatmentDecisionKind",
    "PlanTreatmentKind",
    "PlanTreatmentProposal",
    "REMEDIATION_ROUND_LIMIT_RULE_ID",
    "RemediationRoundEvaluation",
    "RemediationRoundOutcome",
    "canonical_open_signature_groups",
    "evaluate_remediation_round",
    "validate_plan_treatment_coverage",
    "validate_plan_treatment_decisions",
    "validate_plan_completion",
]
