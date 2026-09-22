"""Executable reference and provider-free delta oracle for the target workflow.

The target axes and moves deliberately live in this module only.  The second
half of the module does not restate the current implementation: it constructs
native review results and sends them through ``native_review_contract`` and
the authoritative Finding record reducer.

The model is intentionally finite.  It enumerates every semantically distinct
truth condition from the operator's target document; identifiers, path counts,
and the number of Slices do not change any move verdict and are therefore not
combinatorial axes.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Iterable, Mapping, Sequence

import native_finding_decisions
import native_review_contract
from artifact_bridge import finding_payload
from artifact_models import (
    ArtifactRecord,
    FindingSeverity,
    FindingTransitionPayload,
    Fingerprint,
    FingerprintKind,
    Role,
    RoleProfilePayload,
    RunProfilePayload,
)
from contracts import (
    AgentRole,
    ApprovalMarker,
    FindingClass as CurrentFindingClass,
    FindingOrigin,
    FindingRecord,
    FindingResponseDecision,
    FindingStatus,
    PlannedSlice,
    ValidationAttestation,
    ValidationCommandSpec,
    ValidationRecord,
    ValidationStatus,
)
from finding_reducer import (
    FindingResponseEvent,
    ReviewerReclassification,
    ReviewerStatusChange,
    apply_finding_responses,
    apply_reviewer_events,
    project_reviewer_persistence_transitions,
    reduce_finding_records,
)
from native_codex_contract import NativeFindingDisposition, _apply_dispositions  # allowlist:provider -- exercised production contract
from native_finding_decisions import (
    NativeClosureKind,
    NativeFindingClosure,
    NativeRejectionReason,
    PlanTreatmentDecision,
    PlanTreatmentDecisionKind,
    PlanTreatmentKind,
    PlanTreatmentProposal,
)
from native_review_contract import (
    NativeFinding,
    NativeProseAcceptance,
    NativeReclassification,
    NativeReviewContext,
    NativeReviewResult,
    NativeStatusChange,
    parse_native_contract_result,
)
from workflow import EvidenceKind, WorkflowEngine, WorkflowHistory
from finding_signature import finding_record_signature
from slice_exit import evaluate_slice_exit
from workflow_state import (
    Reviewer,
    WorkflowStep,
    WorkUnitStatus,
    init_workflow_state,
)


TARGET_MODEL_SOURCE = "docs/internal/zielmodell-vereinfachter-orchestrator.md"
TARGET_MODEL_COMMIT = "2f76f7f"
DEFAULT_LOOP_ROUND_LIMIT = 6
DEFAULT_ACCEPTANCE_CYCLE_LIMIT = 6


class Phase(StrEnum):
    PLANNING = "PLANUNG"
    IMPLEMENTATION = "IMPLEMENTIERUNG"
    ACCEPTANCE = "ABNAHME"


class TargetFindingClass(StrEnum):
    BLOCKER = "BLOCKER"
    FINDING = "FINDING"


class Turn(StrEnum):
    IMPLEMENTER = "UMSETZENDER"
    REVIEWER = "REVIEWER"
    LOOP_EXIT = "SCHLEIFENENDE"
    ACCEPTANCE = "ACCEPTANCE-CYCLE"


class Move(StrEnum):
    IMPLEMENT = "UMSETZEN"
    REJECT = "ABLEHNEN"
    STOP_CONTRACT_UNCLEAR = "STOP:CONTRACT-UNCLEAR"
    STOP_PREREQUISITE = "STOP:OPERATOR-PREREQUISITE-MISSING"
    STOP_SCOPE_EXTENSION = "STOP:SCOPE-EXTENSION-REQUESTED"
    CLOSE = "SCHLIESSEN"
    ESCALATE = "ZUM-BLOCKER-MACHEN"
    COMMIT_OR_ACCEPT = "COMMIT-ODER-PLANABNAHME"
    ACCEPTANCE_COMPLETE = "ABNAHME-ABSCHLIESSEN"
    RESTART_CYCLE = "NEW-CYCLE-VIA-INBOX"
    EXHAUST_CYCLES = "ACCEPTANCE-CYCLE-LIMIT"


class BlockedBy(StrEnum):
    NONE = "NONE"
    CONTRACT_UNCLEAR = "CONTRACT-UNCLEAR"
    PREREQUISITE = "OPERATOR-PREREQUISITE-MISSING"
    SCOPE_EXTENSION = "SCOPE-EXTENSION-REQUESTED"


class ReviewFact(StrEnum):
    NONE = "NONE"
    FIXED = "FIXED"
    REJECTION_VALID = "REJECTION_VALID"
    REJECTION_INVALID = "REJECTION_INVALID"
    UNRESOLVED = "UNRESOLVED"


class ConvergenceFact(StrEnum):
    FIRST_REVIEW = "FIRST_REVIEW"
    KNOWN_FINDING_CLOSED = "KNOWN_FINDING_CLOSED"
    FINGERPRINT_CHANGED = "FINGERPRINT_CHANGED"
    STALLED = "STALLED"


@dataclass(frozen=True, slots=True)
class TargetParameters:
    rounds_per_loop: int = DEFAULT_LOOP_ROUND_LIMIT
    acceptance_cycles: int = DEFAULT_ACCEPTANCE_CYCLE_LIMIT
    automatic_restart: bool = True


@dataclass(frozen=True, slots=True)
class TargetSituation:
    situation_id: str
    phase: Phase
    turn: Turn
    finding_class: TargetFindingClass | None = None
    blocked_by: BlockedBy = BlockedBy.NONE
    review_fact: ReviewFact = ReviewFact.NONE
    convergence: ConvergenceFact = ConvergenceFact.FIRST_REVIEW
    round_number: int = 1
    acceptance_cycle: int = 1
    has_acceptance_findings: bool = False


@dataclass(frozen=True, slots=True)
class MoveEvaluation:
    situation_id: str
    move: Move
    admissible: bool
    advances: bool
    truthful: bool
    outcome: str

    @property
    def viable(self) -> bool:
        return self.admissible and self.advances and self.truthful


@dataclass(frozen=True, slots=True)
class TargetModel:
    phases: tuple[Phase, ...]
    finding_classes: tuple[TargetFindingClass, ...]
    reviewer_moves: tuple[Move, ...]
    implementer_moves: Mapping[TargetFindingClass, tuple[Move, ...]]
    stop_reasons: tuple[BlockedBy, ...]
    parameters: TargetParameters
    situations: tuple[TargetSituation, ...]


ALL_MOVES = tuple(Move)


def _loop_situations(phase: Phase) -> tuple[TargetSituation, ...]:
    prefix = phase.value.lower()
    base = (
        TargetSituation(f"{prefix}.clean", phase, Turn.LOOP_EXIT),
        TargetSituation(
            f"{prefix}.finding.implementer",
            phase,
            Turn.IMPLEMENTER,
            TargetFindingClass.FINDING,
        ),
        TargetSituation(
            f"{prefix}.blocker.implementer",
            phase,
            Turn.IMPLEMENTER,
            TargetFindingClass.BLOCKER,
        ),
    )
    blocked = tuple(
        TargetSituation(
            f"{prefix}.blocker.stop.{reason.value.lower()}",
            phase,
            Turn.IMPLEMENTER,
            TargetFindingClass.BLOCKER,
            blocked_by=reason,
        )
        for reason in (
            BlockedBy.CONTRACT_UNCLEAR,
            BlockedBy.PREREQUISITE,
            BlockedBy.SCOPE_EXTENSION,
        )
    )
    finding_review = tuple(
        TargetSituation(
            f"{prefix}.finding.review.{fact.value.lower()}.{convergence.value.lower()}",
            phase,
            Turn.REVIEWER,
            TargetFindingClass.FINDING,
            review_fact=fact,
            convergence=convergence,
            round_number=1 if convergence is ConvergenceFact.FIRST_REVIEW else 2,
        )
        for fact, convergence in (
            (ReviewFact.FIXED, ConvergenceFact.FIRST_REVIEW),
            (ReviewFact.REJECTION_VALID, ConvergenceFact.FIRST_REVIEW),
            (ReviewFact.REJECTION_INVALID, ConvergenceFact.FIRST_REVIEW),
            (ReviewFact.UNRESOLVED, ConvergenceFact.FIRST_REVIEW),
            (ReviewFact.UNRESOLVED, ConvergenceFact.KNOWN_FINDING_CLOSED),
            (ReviewFact.UNRESOLVED, ConvergenceFact.FINGERPRINT_CHANGED),
            (ReviewFact.UNRESOLVED, ConvergenceFact.STALLED),
        )
    )
    blocker_review = tuple(
        TargetSituation(
            f"{prefix}.blocker.review.{fact.value.lower()}.{convergence.value.lower()}",
            phase,
            Turn.REVIEWER,
            TargetFindingClass.BLOCKER,
            review_fact=fact,
            convergence=convergence,
            round_number=1 if convergence is ConvergenceFact.FIRST_REVIEW else 2,
        )
        for fact, convergence in (
            (ReviewFact.FIXED, ConvergenceFact.FIRST_REVIEW),
            (ReviewFact.UNRESOLVED, ConvergenceFact.FIRST_REVIEW),
            (ReviewFact.UNRESOLVED, ConvergenceFact.KNOWN_FINDING_CLOSED),
            (ReviewFact.UNRESOLVED, ConvergenceFact.FINGERPRINT_CHANGED),
            (ReviewFact.UNRESOLVED, ConvergenceFact.STALLED),
        )
    )
    limits = (
        TargetSituation(
            f"{prefix}.finding.review.unresolved.round-limit",
            phase,
            Turn.REVIEWER,
            TargetFindingClass.FINDING,
            review_fact=ReviewFact.UNRESOLVED,
            convergence=ConvergenceFact.FINGERPRINT_CHANGED,
            round_number=DEFAULT_LOOP_ROUND_LIMIT,
        ),
        TargetSituation(
            f"{prefix}.blocker.review.unresolved.round-limit",
            phase,
            Turn.REVIEWER,
            TargetFindingClass.BLOCKER,
            review_fact=ReviewFact.UNRESOLVED,
            convergence=ConvergenceFact.FINGERPRINT_CHANGED,
            round_number=DEFAULT_LOOP_ROUND_LIMIT,
        ),
    )
    return (*base, *blocked, *finding_review, *blocker_review, *limits)


TARGET_MODEL = TargetModel(
    phases=(Phase.PLANNING, Phase.IMPLEMENTATION, Phase.ACCEPTANCE),
    finding_classes=(TargetFindingClass.BLOCKER, TargetFindingClass.FINDING),
    reviewer_moves=(Move.CLOSE, Move.ESCALATE),
    implementer_moves={
        TargetFindingClass.FINDING: (Move.IMPLEMENT, Move.REJECT),
        TargetFindingClass.BLOCKER: (Move.IMPLEMENT,),
    },
    stop_reasons=(
        BlockedBy.CONTRACT_UNCLEAR,
        BlockedBy.PREREQUISITE,
        BlockedBy.SCOPE_EXTENSION,
    ),
    parameters=TargetParameters(),
    situations=(
        *_loop_situations(Phase.PLANNING),
        *_loop_situations(Phase.IMPLEMENTATION),
        TargetSituation(
            "abnahme.clean",
            Phase.ACCEPTANCE,
            Turn.ACCEPTANCE,
            has_acceptance_findings=False,
        ),
        TargetSituation(
            "abnahme.findings.restart",
            Phase.ACCEPTANCE,
            Turn.ACCEPTANCE,
            acceptance_cycle=1,
            has_acceptance_findings=True,
        ),
        TargetSituation(
            "abnahme.findings.cycle-limit",
            Phase.ACCEPTANCE,
            Turn.ACCEPTANCE,
            acceptance_cycle=DEFAULT_ACCEPTANCE_CYCLE_LIMIT,
            has_acceptance_findings=True,
        ),
    ),
)


def evaluate_target_move(
    situation: TargetSituation,
    move: Move,
    *,
    parameters: TargetParameters | None = None,
) -> MoveEvaluation:
    """Evaluate admissibility, progress, and truth independently."""

    policy = parameters or TARGET_MODEL.parameters
    admissible = False
    advances = False
    truthful = False
    outcome = "move belongs to another turn"

    if situation.turn is Turn.IMPLEMENTER:
        assert situation.finding_class is not None
        ordinary = TARGET_MODEL.implementer_moves[situation.finding_class]
        matching_stop = {
            BlockedBy.CONTRACT_UNCLEAR: Move.STOP_CONTRACT_UNCLEAR,
            BlockedBy.PREREQUISITE: Move.STOP_PREREQUISITE,
            BlockedBy.SCOPE_EXTENSION: Move.STOP_SCOPE_EXTENSION,
        }.get(situation.blocked_by)
        admissible = move in ordinary or move is matching_stop
        truthful = admissible and (
            move is matching_stop
            or situation.blocked_by is BlockedBy.NONE
            or move is Move.REJECT
        )
        advances = truthful
        outcome = (
            "operator stop"
            if move is matching_stop
            else "reviewer turn"
            if truthful
            else "no truthful implementer transition"
        )
    elif situation.turn is Turn.REVIEWER:
        admissible = move in TARGET_MODEL.reviewer_moves
        may_close = situation.review_fact in {
            ReviewFact.FIXED,
            ReviewFact.REJECTION_VALID,
        }
        truthful = admissible and (
            (move is Move.CLOSE and may_close)
            or (move is Move.ESCALATE and not may_close)
        )
        if truthful:
            if move is Move.CLOSE:
                outcome = "finding closed"
            elif situation.round_number >= policy.rounds_per_loop:
                outcome = "loop ends negatively at round limit"
            elif (
                situation.round_number > 1
                and situation.convergence is ConvergenceFact.STALLED
            ):
                outcome = "loop ends negatively for non-convergence"
            else:
                outcome = "blocker returned to implementer"
            advances = True
        else:
            outcome = "review assertion contradicts the bound facts"
    elif situation.turn is Turn.LOOP_EXIT:
        admissible = move is Move.COMMIT_OR_ACCEPT
        truthful = admissible and situation.finding_class is None
        advances = truthful
        outcome = "plan accepted" if situation.phase is Phase.PLANNING else "slice committed"
    else:
        if not situation.has_acceptance_findings:
            admissible = move is Move.ACCEPTANCE_COMPLETE
            truthful = admissible
            outcome = "implementation document moved to done"
        elif situation.acceptance_cycle < policy.acceptance_cycles:
            admissible = move is Move.RESTART_CYCLE
            truthful = admissible
            outcome = "review findings written to inbox"
        else:
            admissible = move is Move.EXHAUST_CYCLES
            truthful = admissible
            outcome = "acceptance cycle limit reached"
        advances = truthful
    return MoveEvaluation(
        situation.situation_id,
        move,
        admissible,
        advances,
        truthful,
        outcome,
    )


def target_reachability() -> tuple[MoveEvaluation, ...]:
    return tuple(
        evaluate_target_move(situation, move)
        for situation in TARGET_MODEL.situations
        for move in ALL_MOVES
    )


def target_dead_ends() -> tuple[TargetSituation, ...]:
    by_situation: dict[str, list[MoveEvaluation]] = {}
    for evaluation in target_reachability():
        by_situation.setdefault(evaluation.situation_id, []).append(evaluation)
    return tuple(
        situation
        for situation in TARGET_MODEL.situations
        if not any(item.viable for item in by_situation[situation.situation_id])
    )


class DeviationClass(StrEnum):
    UEBERZAEHLIG = "UEBERZAEHLIG"
    FEHLEND = "FEHLEND"
    UNEINIG = "UNEINIG"


@dataclass(frozen=True, slots=True)
class ProbeOutcome:
    probe_id: str
    situation_id: str
    move: str
    target_viable: bool
    contract_accepts: bool
    records_accept: bool
    contract_detail: str
    records_detail: str
    locations: tuple[str, ...]
    forced_class: DeviationClass | None = None


@dataclass(frozen=True, slots=True)
class Deviation:
    deviation_id: str
    deviation_class: DeviationClass
    situation: str
    move: str
    implementation_result: str
    locations: tuple[str, ...]

    def to_document(self) -> dict[str, object]:
        return {
            "id": self.deviation_id,
            "klasse": self.deviation_class.value,
            "lage": self.situation,
            "zug": self.move,
            "istzustand": self.implementation_result,
            "codestellen": list(self.locations),
        }


@dataclass(frozen=True, slots=True)
class OracleReport:
    situation_count: int
    move_evaluation_count: int
    skipped_situations: tuple[tuple[str, str], ...]
    deviations: tuple[Deviation, ...]
    probe_outcomes: tuple[ProbeOutcome, ...]


class OracleRegression(AssertionError):
    pass


FINGERPRINT = "a" * 64
POST_FINGERPRINT = "c" * 64
RUN_ID = "target-model-oracle"
WORK_UNIT_ID = "1"
@dataclass(frozen=True, slots=True)
class ReviewProbe:
    probe_id: str
    situation_id: str
    move: Move | str
    context: NativeReviewContext
    document: Mapping[str, object]
    typed_response: NativeReviewResult
    target_viable: bool
    expected_status: FindingStatus | None
    expected_class: CurrentFindingClass | None
    locations: tuple[str, ...]


def _attestation(
    *,
    fingerprint: str = FINGERPRINT,
) -> ValidationAttestation:
    default = ValidationCommandSpec(
        argv=("python3", "-m", "pytest", "tests/", "-v")
    )
    specs = (default,)
    records = (
        ValidationRecord(ValidationStatus.PASS, default.display, 0, "passed"),
    )
    return ValidationAttestation(
        attestation_id=f"oracle-{fingerprint[:8]}-plain",
        diff_fingerprint=fingerprint,
        expected_commands=tuple(item.display for item in specs),
        records=records,
        output_digest=hashlib.sha256(
            f"{fingerprint}:plain".encode("utf-8")
        ).hexdigest(),
        summary="provider-free oracle attestation",
        command_specs=specs,
    )


def _finding(
    finding_class: CurrentFindingClass,
    *,
    decision: FindingResponseDecision | None = None,
) -> FindingRecord:
    finding = FindingRecord(
        finding_id="C-01",
        finding_class=finding_class,
        status=FindingStatus.OPEN,
        summary="Executable target-model probe",
        acceptance_test="The reported behavior is corrected.",
        origin=FindingOrigin("1", 1, AgentRole.CLAUDE),  # allowlist:provider -- current typed ownership
        affected_paths=("src/native_review_contract.py",),
    )
    if decision is None:
        return finding
    return apply_finding_responses(
        (finding,),
        (
            FindingResponseEvent(
                "C-01", decision, "Implementer response for the oracle probe."
            ),
        ),
    )[0]


def _context(
    previous: tuple[FindingRecord, ...] = (),
    *,
    approval: ApprovalMarker = ApprovalMarker.SLICE,
    operation: str = "claude_slice_review",  # allowlist:provider -- persisted operation vocabulary
    round_number: int = 2,
    attestation: ValidationAttestation | None = None,
    planned_slices: tuple[PlannedSlice, ...] = (),
    fingerprint: str = FINGERPRINT,
    plan_treatments: tuple[PlanTreatmentProposal, ...] = (),
) -> NativeReviewContext:
    return NativeReviewContext(
        run_id=RUN_ID,
        work_unit_id=WORK_UNIT_ID,
        operation=operation,
        diff_fingerprint=fingerprint,
        reviewer=AgentRole.CLAUDE,  # allowlist:provider -- current typed ownership
        approval_marker=approval,
        slice_id="PLAN" if approval is ApprovalMarker.PLAN else "1",
        round_number=round_number,
        previous_findings=previous,
        validation_attestation=attestation or _attestation(fingerprint=fingerprint),
        allow_new_observations=True,
        anchor_origin=(None if approval is ApprovalMarker.PLAN else "approved-plan"),
        planned_slices=planned_slices,
        plan_treatments=plan_treatments,
    )


def _base_document(context: NativeReviewContext, *, approved: bool) -> dict[str, object]:
    return {
        "schema_version": "native-agent-review-result-v2",
        "result_type": "review_result",
        "request_id": context.request_id,
        "reviewer": "claude",  # allowlist:provider -- native wire vocabulary
        "decision": "approved" if approved else "denied",
        "new_findings": [],
        "status_changes": [],
        "reclassifications": [],
        "plan_treatment_decisions": [],
        "anchors": [],
        "review_evidence": {
            "dimensions": "target reachability and record agreement",
            "largest_residual_risk": "the intentionally excluded communication gap",
            "break_condition": "contract and record outcomes diverge",
        },
        "pre_mortem": "A later rule change could create a newly unreachable state.",
    }


def _typed_response(
    context: NativeReviewContext,
    *,
    approved: bool,
    new_findings: tuple[NativeFinding, ...] = (),
    status_changes: tuple[NativeStatusChange, ...] = (),
    reclassifications: tuple[NativeReclassification, ...] = (),
    plan_treatment_decisions: tuple[PlanTreatmentDecision, ...] = (),
) -> NativeReviewResult:
    return NativeReviewResult(
        request_id=context.request_id,
        reviewer=AgentRole.CLAUDE,  # allowlist:provider -- current typed ownership
        approved=approved,
        new_findings=new_findings,
        status_changes=status_changes,
        reclassifications=reclassifications,
        plan_treatment_decisions=plan_treatment_decisions,
        anchors=(),
        evidence=None,
        pre_mortem="A later rule change could create a newly unreachable state.",
    )


def _target_viable(situation_id: str, move: Move) -> bool:
    situation = next(
        item for item in TARGET_MODEL.situations if item.situation_id == situation_id
    )
    return evaluate_target_move(situation, move).viable


class _ReviewProbeCollector:
    def __init__(self, probes: list[ReviewProbe]) -> None:
        self.probes = probes

    def __call__(
        self,
        probe_id: str,
        situation_id: str,
        move: Move | str,
        context: NativeReviewContext,
        document: dict[str, object],
        response: NativeReviewResult,
        *,
        target_viable: bool | None = None,
        expected_status: FindingStatus | None = None,
        expected_class: CurrentFindingClass | None = None,
        locations: tuple[str, ...] = ("src/native_review_contract.py", "src/finding_reducer.py"),  # allowlist:provider -- measured code location
    ) -> None:
        self.probes.append(
            ReviewProbe(
                probe_id,
                situation_id,
                move,
                context,
                document,
                response,
                (
                    _target_viable(situation_id, move)
                    if target_viable is None and isinstance(move, Move)
                    else bool(target_viable)
                ),
                expected_status,
                expected_class,
                locations,
            )
        )


def _core_review_probes() -> tuple[ReviewProbe, ...]:
    probes: list[ReviewProbe] = []
    add = _ReviewProbeCollector(probes)

    # Target moves: closing implemented/rejected Findings, escalating an
    # unresolved Finding, and closing/retaining a Blocker.
    accepted = _finding(
        CurrentFindingClass.OBSERVATION,
        decision=FindingResponseDecision.ACCEPTED,
    )
    context = _context((accepted,))
    closure = NativeFindingClosure(NativeClosureKind.FIXED)
    status = NativeStatusChange(
        "C-01", FindingStatus.CLOSED, "The implementation resolves the finding.", closure
    )
    document = _base_document(context, approved=True)
    document["status_changes"] = [
        {
            "finding_id": "C-01",
            "status": "CLOSED",
            "rationale": status.rationale,
            "closure": {"kind": "fixed"},
        }
    ]
    add(
        "close-implemented-finding",
        "implementierung.finding.review.fixed.first_review",
        Move.CLOSE,
        context,
        document,
        _typed_response(context, approved=True, status_changes=(status,)),
        expected_status=FindingStatus.CLOSED,
        expected_class=CurrentFindingClass.OBSERVATION,
    )

    rejected = _finding(
        CurrentFindingClass.OBSERVATION,
        decision=FindingResponseDecision.REJECTED,
    )
    context = _context((rejected,))
    rejected_closure = NativeFindingClosure(
        NativeClosureKind.REJECTED,
        rejection_reason=NativeRejectionReason.NO_DEFECT,
        evidence="The named record evidence disproves the report.",
    )
    status = NativeStatusChange(
        "C-01", FindingStatus.CLOSED, "The reasoned rejection is accepted.", rejected_closure
    )
    document = _base_document(context, approved=True)
    document["status_changes"] = [
        {
            "finding_id": "C-01",
            "status": "CLOSED",
            "rationale": status.rationale,
            "closure": {
                "kind": "rejected",
                "rejection_reason": "no_defect",
                "evidence": rejected_closure.evidence,
            },
        }
    ]
    add(
        "close-rejected-finding",
        "implementierung.finding.review.rejection_valid.first_review",
        Move.CLOSE,
        context,
        document,
        _typed_response(context, approved=True, status_changes=(status,)),
        expected_status=FindingStatus.CLOSED,
        expected_class=CurrentFindingClass.OBSERVATION,
    )

    context = _context((rejected,))
    reclassification = NativeReclassification(
        "C-01", CurrentFindingClass.BLOCKER, "The rejection does not answer the defect."
    )
    document = _base_document(context, approved=False)
    document["reclassifications"] = [
        {
            "finding_id": "C-01",
            "finding_class": "BLOCKER",
            "rationale": reclassification.rationale,
        }
    ]
    add(
        "explicit-escalation",
        "implementierung.finding.review.rejection_invalid.first_review",
        Move.ESCALATE,
        context,
        document,
        _typed_response(context, approved=False, reclassifications=(reclassification,)),
        expected_status=FindingStatus.OPEN,
        expected_class=CurrentFindingClass.BLOCKER,
    )

    # The target requires this escalation by construction.  Today's contract
    # cannot derive it from a rejected disposition when the reviewer does not
    # send an explicit reclassification.
    context = _context((rejected,))
    document = _base_document(context, approved=False)
    add(
        "automatic-rejection-escalation",
        "implementierung.finding.review.rejection_invalid.first_review",
        Move.ESCALATE,
        context,
        document,
        _typed_response(context, approved=False),
        expected_status=FindingStatus.OPEN,
        expected_class=CurrentFindingClass.BLOCKER,
        locations=("src/native_review_contract.py", "src/finding_reducer.py"),
    )

    blocker = _finding(
        CurrentFindingClass.BLOCKER,
        decision=FindingResponseDecision.ACCEPTED,
    )
    context = _context((blocker,))
    status = NativeStatusChange(
        "C-01", FindingStatus.CLOSED, "The blocker is fixed.", closure
    )
    document = _base_document(context, approved=True)
    document["status_changes"] = [
        {
            "finding_id": "C-01",
            "status": "CLOSED",
            "rationale": status.rationale,
            "closure": {"kind": "fixed"},
        }
    ]
    add(
        "close-prose-blocker",
        "implementierung.blocker.review.fixed.first_review",
        Move.CLOSE,
        context,
        document,
        _typed_response(context, approved=True, status_changes=(status,)),
        expected_status=FindingStatus.CLOSED,
        expected_class=CurrentFindingClass.BLOCKER,
    )

    context = _context((blocker,))
    status = NativeStatusChange(
        "C-01", FindingStatus.OPEN, "The blocker remains reproducible."
    )
    document = _base_document(context, approved=False)
    document["status_changes"] = [
        {
            "finding_id": "C-01",
            "status": "OPEN",
            "rationale": status.rationale,
            "closure": None,
        }
    ]
    add(
        "retain-blocker",
        "implementierung.blocker.review.unresolved.first_review",
        Move.ESCALATE,
        context,
        document,
        _typed_response(context, approved=False, status_changes=(status,)),
        expected_status=FindingStatus.OPEN,
        expected_class=CurrentFindingClass.BLOCKER,
    )
    return tuple(probes)


def _plan_review_probes() -> tuple[ReviewProbe, ...]:
    probes: list[ReviewProbe] = []
    add = _ReviewProbeCollector(probes)

    # Today's plan review may approve while carrying its open cohort into a
    # planned implementation Slice.  The target explicitly ends planning with
    # no open Finding.
    plan_finding = _finding(CurrentFindingClass.BLOCKER)
    signature = finding_record_signature(plan_finding)
    treatment = PlanTreatmentProposal(
        signature,
        ("C-01",),
        PlanTreatmentKind.IMPLEMENTATION,
        closing_slice_ids=(1,),
    )
    plan_slice = PlannedSlice(
        1,
        "Carry the open plan finding into implementation.",
        ("src/native_review_contract.py",),  # allowlist:provider -- measured code location
    )
    context = _context(
        (plan_finding,),
        approval=ApprovalMarker.PLAN,
        operation="claude_plan_review",  # allowlist:provider -- persisted operation vocabulary
        planned_slices=(plan_slice,),
        plan_treatments=(treatment,),
    )
    treatment_decision = PlanTreatmentDecision(
        signature,
        PlanTreatmentDecisionKind.ACCEPTED,
        "The implementation Slice is assigned the open plan finding.",
    )
    document = _base_document(context, approved=True)
    document["plan_treatment_decisions"] = [
        {
            "signature": signature,
            "decision": "accepted",
            "rationale": treatment_decision.rationale,
        }
    ]
    add(
        "open-plan-finding-handoff",
        "planung.clean",
        "OFFENEN-PLANBEFUND-UEBERGEBEN",
        context,
        document,
        _typed_response(
            context,
            approved=True,
            plan_treatment_decisions=(treatment_decision,),
        ),
        target_viable=False,
        expected_status=FindingStatus.OPEN,
        expected_class=CurrentFindingClass.BLOCKER,
        locations=(
            "src/native_review_contract.py",  # allowlist:provider -- measured code location
            "src/native_finding_decisions.py",
            "src/plan_handoff.py",
        ),
    )
    return tuple(probes)


def _legacy_finding_review_probes() -> tuple[ReviewProbe, ...]:
    probes: list[ReviewProbe] = []
    add = _ReviewProbeCollector(probes)
    closure = NativeFindingClosure(NativeClosureKind.FIXED)

    # OBSERVATION itself is an implementation class absent from the target.
    context = _context((), round_number=1)
    native = NativeFinding(
        "C-01",
        CurrentFindingClass.OBSERVATION,
        "The legacy non-blocking class remains available.",
        NativeProseAcceptance("The response-local observation is checked."),
    )
    status = NativeStatusChange(
        "C-01", FindingStatus.CLOSED, "Checked in the same response.", closure
    )
    document = _base_document(context, approved=True)
    document["new_findings"] = [
        {
            "finding_id": "C-01",
            "finding_class": "OBSERVATION",
            "summary": native.summary,
            "acceptance_test": {
                "kind": "prose",
                "text": native.acceptance_test.text,
            },
            "affected_paths": [],
        }
    ]
    document["status_changes"] = [
        {
            "finding_id": "C-01",
            "status": "CLOSED",
            "rationale": status.rationale,
            "closure": {"kind": "fixed"},
        }
    ]
    add(
        "observation-class",
        "implementierung.finding.review.fixed.first_review",
        "OBSERVATION",
        context,
        document,
        _typed_response(
            context,
            approved=True,
            new_findings=(native,),
            status_changes=(status,),
        ),
        target_viable=False,
        expected_status=FindingStatus.CLOSED,
        expected_class=CurrentFindingClass.OBSERVATION,
        locations=("src/contracts.py", "src/native_review_contract.py"),  # allowlist:provider -- measured code location
    )
    return tuple(probes)


def _review_probes() -> tuple[ReviewProbe, ...]:
    return (
        *_core_review_probes(),
        *_plan_review_probes(),
        *_legacy_finding_review_probes(),
    )


def _new_record(
    records: list[ArtifactRecord],
    logical_id: str,
    payload: object,
) -> None:
    same = [record for record in records if record.logical_id == logical_id]
    records.append(
        ArtifactRecord.create(
            run_id=RUN_ID,
            logical_id=logical_id,
            revision=len(same) + 1,
            fingerprint=Fingerprint(FingerprintKind.IMPLEMENTATION, FINGERPRINT),
            predecessor_ids=((records[-1].record_id,) if records else ()),
            created_at=f"2026-09-21T12:{len(records):02d}:00+02:00",
            idempotency_key=f"oracle:{logical_id}:{len(same) + 1}",
            payload=payload,  # type: ignore[arg-type]
        )
    )


def _native_opening_record(finding: NativeFinding) -> FindingRecord:
    return FindingRecord(
        finding_id=finding.finding_id,
        finding_class=finding.finding_class,
        status=FindingStatus.OPEN,
        summary=finding.summary,
        acceptance_test=finding.acceptance_test.text,
        origin=FindingOrigin("1", 1, AgentRole.CLAUDE),  # allowlist:provider -- current typed ownership
        affected_paths=finding.affected_paths,
    )


def _semantic_match(
    findings: Sequence[FindingRecord],
    probe: ReviewProbe,
) -> bool:
    if probe.expected_status is None and probe.expected_class is None:
        return True
    finding = next((item for item in findings if item.finding_id == "C-01"), None)
    if finding is None:
        return False
    if probe.expected_status is not None and finding.status is not probe.expected_status:
        return False
    if probe.expected_class is not None and finding.finding_class is not probe.expected_class:
        return False
    return True


def _record_probe(probe: ReviewProbe) -> tuple[bool, str]:
    records: list[ArtifactRecord] = []
    _new_record(
        records,
        "run-profile",
        RunProfilePayload(
            RoleProfilePayload("oracle-implementer", "medium"),
            RoleProfilePayload("oracle-reviewer", "high"),
        ),
    )
    for prior in probe.context.previous_findings:
        bare = replace(prior, responses=())
        _new_record(
            records,
            f"finding-{prior.finding_id}",
            finding_payload(
                bare,
                action="opened",
                work_unit_id=WORK_UNIT_ID,
            ),
        )
        for response in prior.responses:
            _new_record(
                records,
                f"finding-{prior.finding_id}",
                finding_payload(
                    prior,
                    actor=AgentRole.CODEX,  # allowlist:provider -- current typed ownership
                    action="responded",
                    rationale=response.rationale,
                    work_unit_id=WORK_UNIT_ID,
                    response_decision=response.decision,
                ),
            )
    opened = tuple(_native_opening_record(item) for item in probe.typed_response.new_findings)
    statuses = tuple(
        ReviewerStatusChange(item.finding_id, item.status, item.rationale)
        for item in probe.typed_response.status_changes
    )
    reclassifications = tuple(
        ReviewerReclassification(
            item.finding_id, item.finding_class, item.rationale
        )
        for item in probe.typed_response.reclassifications
    )
    try:
        current = apply_reviewer_events(
            probe.context.previous_findings,
            reviewer=AgentRole.CLAUDE,  # allowlist:provider -- current typed ownership
            opened=opened,
            status_changes=statuses,
            reclassifications=reclassifications,
            escalate_unclosed_rejections=not probe.typed_response.approved,
        )
        closures = {
            item.finding_id: item.closure
            for item in probe.typed_response.status_changes
            if item.closure is not None
        }
        for transition in project_reviewer_persistence_transitions(
            probe.context.previous_findings,
            current,
            work_unit_id=WORK_UNIT_ID,
        ):
            _new_record(
                records,
                f"finding-{transition.finding.finding_id}",
                finding_payload(
                    transition.finding,
                    action=transition.action,
                    rationale=transition.rationale,
                    work_unit_id=WORK_UNIT_ID,
                    closure=(
                        closures.get(transition.finding.finding_id)
                        if transition.action == "status_changed"
                        else None
                    ),
                ),
            )
        reduction = reduce_finding_records(records)
        matched = _semantic_match(reduction.ledger.findings, probe)
        return matched, (
            "record reduction reached the requested semantic state"
            if matched
            else "record reduction accepted bytes but missed the requested semantic state"
        )
    except (TypeError, ValueError) as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _contract_probe(probe: ReviewProbe) -> tuple[bool, str]:
    try:
        result = parse_native_contract_result(probe.document, probe.context)
        matched = _semantic_match(result.findings, probe)
        return matched, (
            "review contract reached the requested semantic state"
            if matched
            else "review contract accepted bytes but missed the requested semantic state"
        )
    except (TypeError, ValueError) as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _review_probe_outcome(probe: ReviewProbe) -> ProbeOutcome:
    contract_accepts, contract_detail = _contract_probe(probe)
    records_accept, records_detail = _record_probe(probe)
    return ProbeOutcome(
        probe.probe_id,
        probe.situation_id,
        probe.move.value if isinstance(probe.move, Move) else probe.move,
        probe.target_viable,
        contract_accepts,
        records_accept,
        contract_detail,
        records_detail,
        probe.locations,
    )


def _policy_probe_outcomes() -> tuple[ProbeOutcome, ...]:
    outcomes: list[ProbeOutcome] = []

    # Complete dispositions: call the same domain helper used by the native
    # Codex result path and observe whether it rejects an omitted open Finding.
    prior = (_finding(CurrentFindingClass.OBSERVATION),)
    try:
        _apply_dispositions(
            prior,
            (),
            work_unit_id=WORK_UNIT_ID,
            round_number=1,
        )
    except ValueError as exc:
        complete_dispositions_enforced = True
        contract_detail = f"native Codex domain path rejects omission: {exc}"
        record_detail = "incomplete response batch emits no transition records"
    else:
        complete_dispositions_enforced = False
        contract_detail = "native Codex domain path accepts an omitted disposition"
        record_detail = "no response transition is emitted"
    outcomes.append(
        ProbeOutcome(
            "mandatory-implementer-disposition",
            "implementierung.finding.implementer",
            "JEDES-FINDING-DISPONIEREN",
            True,
            complete_dispositions_enforced,
            complete_dispositions_enforced,
            contract_detail,
            record_detail,
            ("src/native_codex_contract.py", "src/finding_reducer.py"),  # allowlist:provider -- measured code location
        )
    )

    blocker = (_finding(CurrentFindingClass.BLOCKER),)
    rejected = _apply_dispositions(
        blocker,
        (
            NativeFindingDisposition(
                "C-01",
                FindingResponseDecision.REJECTED,
                "The implementation disputes the blocker.",
            ),
        ),
        work_unit_id=WORK_UNIT_ID,
        round_number=1,
    )
    outcomes.append(
        ProbeOutcome(
            "blocker-rejection",
            "implementierung.blocker.implementer",
            Move.REJECT.value,
            False,
            rejected[0].responses[-1].decision is FindingResponseDecision.REJECTED,
            True,
            "native Codex domain path accepts REJECTED for a BLOCKER",  # allowlist:provider -- measured result
            "Finding response records accept the rejection",
            ("src/native_codex_contract.py", "src/finding_reducer.py"),  # allowlist:provider -- measured code location
        )
    )

    # The real WorkflowState transition continues after six progressing
    # implementer/reviewer pairs and grows its mutable allowance.
    state = init_workflow_state(
        run_id="oracle-round-limit",
        task_file="/repo/task.md",
        branch="feature/oracle",
        branch_base="a" * 40,
        first_slice_start_commit="a" * 40,
        slice_count=1,
    )
    for index in range(DEFAULT_LOOP_ROUND_LIMIT):
        state = state.record_review_denial(
            reviewer=Reviewer.CLAUDE,  # allowlist:provider -- current typed ownership
            open_findings=("C-01",),
            return_step=WorkflowStep.CODEX_PLAN_REVISION,  # allowlist:provider -- persisted step vocabulary
            progress_made=True,
            updated_at=f"oracle-round-{index + 1}",
        )
    continues = state.current_work_unit.status is WorkUnitStatus.IN_PROGRESS
    outcomes.append(
        ProbeOutcome(
            "loop-round-seven",
            "planung.blocker.review.unresolved.round-limit",
            "RUNDE-7",
            False,
            continues,
            continues,
            f"WorkflowState advances to round {state.current_work_unit.round_number}",
            "state projection retains the continuing work unit",
            ("src/workflow_state.py",),
        )
    )

    # The installed remediation-family evaluator is called at target cycle six.
    remediation = native_finding_decisions.MAX_REMEDIATION_ROUNDS
    outcomes.append(
        ProbeOutcome(
            "acceptance-cycle-beyond-six",
            "abnahme.findings.cycle-limit",
            "ACCEPTANCE-CYCLE-7",
            False,
            remediation > DEFAULT_ACCEPTANCE_CYCLE_LIMIT,
            remediation > DEFAULT_ACCEPTANCE_CYCLE_LIMIT,
            f"installed absolute remediation limit is {remediation}",
            "the persisted planning payload accepts the same installed limit",
            ("src/native_finding_decisions.py", "src/finding_planning.py"),
        )
    )

    # Later review requests bind the preceding reviewer fingerprint rather than
    # the immutable Slice start.
    previous = "b" * 64
    slice_start = "d" * 64
    collected: list[tuple[str, str]] = []
    engine = SimpleNamespace(
        driver=SimpleNamespace(
            collect_correction_delta=lambda start, end: (
                collected.append((start, end)) or "correction delta"
            )
        )
    )
    evidence_kind, _ = WorkflowEngine._select_review_evidence(
        engine,
        SimpleNamespace(current_slice=SimpleNamespace(start_fingerprint=slice_start)),
        SimpleNamespace(approved_plan_text=None),
        WorkflowHistory(1, last_claude_fingerprint=previous),  # allowlist:provider -- current persisted field
        AgentRole.CLAUDE,  # allowlist:provider -- current typed ownership
        SimpleNamespace(),
        SimpleNamespace(fingerprint=POST_FINGERPRINT, full_diff="full diff"),
        False,
        False,
    )
    selected = collected[0][0] if collected else None
    outcomes.append(
        ProbeOutcome(
            "correction-review-baseline",
            "implementierung.blocker.review.unresolved.fingerprint_changed",
            "KORREKTURDELTA-ALS-AUSGANGSSTAND",
            False,
            evidence_kind is EvidenceKind.CORRECTION_DELTA and selected == previous,
            evidence_kind is EvidenceKind.CORRECTION_DELTA and selected == previous,
            f"review context selected prior-review fingerprint {selected}",
            "the request binding persists that selected fingerprint",
            ("src/workflow_recovery.py", "src/workflow_requests.py", "src/workflow.py"),
        )
    )

    # A BRANCH_DISCOVERY work unit is still constructible as its own run.
    discovery = init_workflow_state(
        run_id="oracle-branch-discovery",
        task_file="/repo/discovery.md",
        branch="feature/oracle",
        branch_base="a" * 40,
        first_slice_start_commit="b" * 40,
        slice_count=1,
        execution_mode="BRANCH_DISCOVERY",
    )
    separate_run = discovery.current_step is WorkflowStep.CLAUDE_BRANCH_DISCOVERY  # allowlist:provider -- persisted step vocabulary
    outcomes.append(
        ProbeOutcome(
            "separate-branch-discovery-run",
            "abnahme.findings.restart",
            "BRANCH_DISCOVERY-LAUF",
            False,
            separate_run,
            separate_run,
            "workflow initializer creates a dedicated discovery work unit",
            "state projection persists that work-unit kind",
            ("src/workflow_state.py", "src/workflow.py"),
        )
    )

    # Call the actual Slice-exit evaluator.  Even an empty prefix produces the
    # complete six-condition contract; the target has only the blocker test.
    exit_evaluation = evaluate_slice_exit((), run_id=RUN_ID, slice_id="1")
    six_conditions = tuple(item.number for item in exit_evaluation.conditions)
    outcomes.append(
        ProbeOutcome(
            "six-part-slice-exit",
            "implementierung.clean",
            "SECHS-COMMITBEDINGUNGEN",
            False,
            six_conditions == (1, 2, 3, 4, 5, 6),
            six_conditions == (1, 2, 3, 4, 5, 6),
            f"evaluate_slice_exit returned conditions {six_conditions}",
            "the same record projection supplies all six conditions",
            ("src/slice_exit.py", "src/finding_reducer.py"),
        )
    )
    return tuple(outcomes)


def _deviation_for(outcome: ProbeOutcome) -> Deviation | None:
    deviation_class = outcome.forced_class
    if deviation_class is None:
        if outcome.contract_accepts != outcome.records_accept:
            deviation_class = DeviationClass.UNEINIG
        elif outcome.target_viable and not (
            outcome.contract_accepts and outcome.records_accept
        ):
            deviation_class = DeviationClass.FEHLEND
        elif not outcome.target_viable and (
            outcome.contract_accepts and outcome.records_accept
        ):
            deviation_class = DeviationClass.UEBERZAEHLIG
    if deviation_class is None:
        return None
    return Deviation(
        outcome.probe_id,
        deviation_class,
        outcome.situation_id,
        outcome.move,
        f"Vertrag: {outcome.contract_detail}; Recordschicht: {outcome.records_detail}",
        outcome.locations,
    )


def run_target_model_oracle() -> OracleReport:
    if dead_ends := target_dead_ends():
        raise OracleRegression(
            "target model has no admissible, advancing, truthful move for: "
            + ", ".join(item.situation_id for item in dead_ends)
        )
    review_outcomes = tuple(_review_probe_outcome(item) for item in _review_probes())
    outcomes = (
        *review_outcomes,
        *_policy_probe_outcomes(),
    )
    deviations = tuple(
        sorted(
            (item for item in (_deviation_for(outcome) for outcome in outcomes) if item),
            key=lambda item: (item.deviation_class.value, item.deviation_id),
        )
    )
    # Implementation comparison is deliberately limited to response-bearing
    # review states plus the named policy boundaries.  Stop states do not have
    # a reviewer response by construction and are counted explicitly here.
    skipped = tuple(
        (item.situation_id, "Stop ends before review contract and record decision")
        for item in TARGET_MODEL.situations
        if item.turn is Turn.IMPLEMENTER and item.blocked_by is not BlockedBy.NONE
    )
    return OracleReport(
        situation_count=len(TARGET_MODEL.situations),
        move_evaluation_count=len(target_reachability()),
        skipped_situations=skipped,
        deviations=deviations,
        probe_outcomes=outcomes,
    )


def load_frozen_deviations(path: Path) -> tuple[Deviation, ...]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema") != "target-model-deviation-ratchet-v1":
        raise OracleRegression("unknown target-model deviation ratchet schema")
    return tuple(
        Deviation(
            item["id"],
            DeviationClass(item["klasse"]),
            item["lage"],
            item["zug"],
            item["istzustand"],
            tuple(item["codestellen"]),
        )
        for item in document["abweichungen"]
    )


def assert_deviation_ratchet(
    current: Sequence[Deviation],
    frozen: Sequence[Deviation],
) -> None:
    frozen_by_key = {
        (item.deviation_id, item.deviation_class): item for item in frozen
    }
    unexpected = tuple(
        item
        for item in current
        if (item.deviation_id, item.deviation_class) not in frozen_by_key
    )
    changed = tuple(
        item
        for item in current
        if (baseline := frozen_by_key.get((item.deviation_id, item.deviation_class)))
        is not None
        and (
            item.situation != baseline.situation
            or item.move != baseline.move
            or item.locations != baseline.locations
        )
    )
    if unexpected or changed:
        rows = [
            f"{item.deviation_class.value}:{item.deviation_id}"
            for item in (*unexpected, *changed)
        ]
        raise OracleRegression("deviation set grew or changed: " + ", ".join(rows))


def frozen_document(report: OracleReport) -> dict[str, object]:
    return {
        "schema": "target-model-deviation-ratchet-v1",
        "target_model": TARGET_MODEL_SOURCE,
        "target_model_commit": TARGET_MODEL_COMMIT,
        "situation_count": report.situation_count,
        "move_evaluation_count": report.move_evaluation_count,
        "skipped": [
            {"lage": situation, "grund": reason}
            for situation, reason in report.skipped_situations
        ],
        "known_exclusion": {
            "finding": "103",
            "reason": (
                "Ein in derselben Antwort geoeffnetes Finding ist eine "
                "Mitteilungsluecke, keine Erreichbarkeitsluecke."
            ),
        },
        "abweichungen": [item.to_document() for item in report.deviations],
    }


__all__ = [
    "Deviation",
    "DeviationClass",
    "Move",
    "OracleRegression",
    "TARGET_MODEL",
    "TARGET_MODEL_COMMIT",
    "TARGET_MODEL_SOURCE",
    "assert_deviation_ratchet",
    "evaluate_target_move",
    "frozen_document",
    "load_frozen_deviations",
    "run_target_model_oracle",
    "target_dead_ends",
    "target_reachability",
]
