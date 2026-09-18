"""Pure construction of provider-bound workflow requests.

The workflow engine remains the composition root for execution errors and run
control.  This module consumes only the values supplied by that engine and has
no reverse import or mutable engine dependency.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from contracts import (
    ApprovalMarker,
    CodexStepContract as ImplementerStepContract,
    FindingRecord,
    StepContract,
)
from finding_order import sorted_finding_ids
from finding_reducer import project_open_set
from native_codex_contract import NativeCodexContext, NativeCodexRequestKind
from native_codex_request import (
    NativeCodexEvidenceInput,
    NativeCodexRequestBundle,
    NativeCodexRequestSpec,
    build_native_codex_request,
)
from native_review_contract import (
    MAX_NATIVE_REVIEW_DISPOSITIONS,
    NativeReviewContext,
    NativeReviewErrorCode,
    native_review_retry_guidance,
)
from native_review_request import (
    NativeReviewEvidenceInput,
    NativeReviewKind,
    NativeReviewRequestBundle,
    NativeReviewRequestSpec,
    NativeReviewRetryFeedback,
    PROVIDER_INPUT_BOUNDARY_EVIDENCE_KIND,
    build_native_review_request,
)
from prompts import NATIVE_CODEX_SYSTEM_POLICY
from provider_input_efficiency import (
    ProviderInputEfficiencyError,
    build_correction_execution_package,
    build_slice_execution_package,
)
from review_packets import (
    ReviewPacket,
    ReviewPacketError,
    derive_correction_requirements,
)
from workflow_state import (
    SliceStatus,
    WorkflowState,
    WorkUnitKind,
    project_implementer_return_policy,
)


FINAL_REVIEW_DISPOSITION_BATCH_SIZE = MAX_NATIVE_REVIEW_DISPOSITIONS
FINAL_REVIEW_ROUND_SAFETY_LIMIT = 256
FULL_BRANCH_DIFF_EVIDENCE_CEILING_CHARS = 1_000_000
_NATIVE_REVIEW_KIND_BY_APPROVAL_MARKER = {
    ApprovalMarker.PLAN: NativeReviewKind.PLAN,
    ApprovalMarker.SLICE: NativeReviewKind.SLICE,
    ApprovalMarker.FINAL: NativeReviewKind.FINAL,
}
FINDING_SIGNATURE_REVIEW_CRITERION = (
    "review_contract.known_open_finding_signatures binds every known open "
    "finding identifier to SHA-256 over canonical JSON containing its "
    "NFKC-normalized, casefolded acceptance test and its sorted mentioned "
    "repository paths; the measured executable-mode and missing-documentation "
    "patterns use narrow family markers so new paths remain occurrences of the "
    "same issue. Do not open a finding whose signature already exists. Report "
    "another occurrence through status_changes for the existing identifier, "
    "keep status OPEN, and name the additional location in a new rationale so "
    "the existing status_changed transition preserves it visibly."
)


def native_codex_request(
    *,
    state: WorkflowState,
    context: Any,
    history: Any,
    contract: ImplementerStepContract,
    request_kind: NativeCodexRequestKind,
    execution_error: type[RuntimeError],
    work_context: str | None = None,
    additional_authorized_paths: tuple[str, ...] = (),
    correction_delta: str | None = None,
    correction_fingerprint: str | None = None,
    correction_findings: tuple[FindingRecord, ...] | None = None,
) -> NativeCodexRequestBundle:
    """Build one Codex request exclusively from orchestrator-owned values."""
    if request_kind is NativeCodexRequestKind.FINAL_REPORT:
        current_fingerprint = contract.review_fingerprint
        base_commit = state.branch_base
        authorized_paths = tuple(
            sorted(
                {
                    path
                    for completed_slice in state.slices
                    if completed_slice.status is SliceStatus.COMPLETED
                    for path in completed_slice.scope_paths
                }
            )
        )
    elif state.current_work_unit.kind is WorkUnitKind.PLAN:
        current_fingerprint = state.task_digest
        base_commit = state.branch_base
        authorized_paths = state.task_scope_patterns
    else:
        current_fingerprint = (
            correction_fingerprint
            if request_kind is NativeCodexRequestKind.CORRECTION
            else state.current_slice.start_fingerprint
        )
        base_commit = state.current_slice.start_commit or state.branch_base
        authorized_paths = state.current_slice.scope_paths
    if current_fingerprint is None:
        raise execution_error(
            "native Codex request lacks an orchestrator-owned fingerprint"
        )
    if not authorized_paths:
        raise execution_error(
            "native Codex request lacks an authorized path boundary"
        )
    if (
        state.current_work_unit.kind in {WorkUnitKind.PLAN, WorkUnitKind.SLICE}
        and not context.slice_summary.strip()
    ):
        raise execution_error(
            "native non-correction implementer request requires a current-slice summary"
        )
    authorized_paths = tuple(
        sorted({*authorized_paths, *additional_authorized_paths})
    )
    native_findings = history.findings
    correction_goal: str | None = None
    if request_kind is NativeCodexRequestKind.CORRECTION:
        if correction_findings is None:
            raise execution_error(
                "native correction request lacks record-backed findings"
            )
        affected_ids = sorted_finding_ids(state.current_work_unit.open_findings)
        try:
            correction_goal, _, native_findings = derive_correction_requirements(
                correction_findings,
                affected_ids,
            )
        except ReviewPacketError as exc:
            raise execution_error(
                "native correction request lacks its exact affected open finding set"
            ) from exc
    # The policy is a separately digested evidence item. Repeating it in the
    # work context would make one logical input appear twice in the same
    # provider request and would weaken component-level accounting.
    if work_context is not None:
        effective_work_context = work_context
    elif correction_goal is not None:
        effective_work_context = context.render_distilled_context(
            unit_heading="CURRENT CORRECTION",
            unit_summary=correction_goal,
            current_scope_paths=state.current_slice.scope_paths,
        )
    else:
        effective_work_context = context.distilled_context
    if additional_authorized_paths:
        effective_work_context += (
            "\n\nFINGERPRINT-BOUND ORCHESTRATOR PATH AUTHORIZATION\n"
            + "\n".join(additional_authorized_paths)
            + "\nThese exact paths were approved by a user gate for the current "
            "repository fingerprint. They are part of this request's authoritative "
            "allowlist even when absent from the original plan. Their presence is "
            "not an UNEXPECTED-PATH condition."
        )
    native_context = NativeCodexContext(
        run_id=state.run_id,
        work_unit_id=str(state.current_work_unit_id),
        operation=state.current_step.value,
        current_fingerprint=current_fingerprint,
        request_kind=request_kind,
        contract=contract,
        known_stop_rule_ids=context.known_stop_rule_ids,
        previous_findings=native_findings,
    )
    evidence = [
        NativeCodexEvidenceInput(
            "native-policy", "system_policy", NATIVE_CODEX_SYSTEM_POLICY
        )
    ]
    request_assignment = context.assignment
    try:
        if (
            request_kind is NativeCodexRequestKind.IMPLEMENTATION
            and context.approved_plan_text is not None
        ):
            if context.work_plan_path is None:
                raise execution_error(
                    "native implementation request lacks its source plan path"
                )
            package = build_slice_execution_package(
                plan_text=context.approved_plan_text,
                source_plan_path=context.work_plan_path,
                slice_id=state.current_slice_id,
                authorized_paths=tuple(sorted(set(authorized_paths))),
                findings=native_findings,
            )
            package_findings = json.loads(package.canonical_json)["slice"][
                "open_findings"
            ]
            request_findings = [
                {
                    "finding_id": item.finding_id,
                    "finding_class": item.finding_class.value,
                    "reporter": item.origin.reporter.value,
                    "summary": item.summary,
                    "acceptance_test": item.acceptance_test,
                }
                for item in project_open_set(native_findings).findings
            ]
            if package_findings != request_findings:
                raise execution_error(
                    "slice execution package differs from the native request open finding set"
                )
            evidence.append(
                NativeCodexEvidenceInput(
                    "slice-execution-package",
                    "slice_execution_package",
                    package.canonical_json,
                    source_path=context.work_plan_path,
                )
            )
            request_assignment = "Implement only the bound Slice execution package."
        elif request_kind is NativeCodexRequestKind.CORRECTION:
            if correction_delta is None or correction_fingerprint is None:
                raise execution_error(
                    "native correction request lacks its current delta binding"
                )
            package = build_correction_execution_package(
                current_fingerprint=correction_fingerprint,
                authorized_paths=tuple(sorted(set(authorized_paths))),
                findings=native_findings,
                current_delta=correction_delta,
            )
            evidence.append(
                NativeCodexEvidenceInput(
                    "correction-execution-package",
                    "correction_execution_package",
                    package.canonical_json,
                )
            )
            request_assignment = (
                "Correct only the affected findings and current delta in the "
                "bound correction execution package."
            )
    except ProviderInputEfficiencyError as exc:
        raise execution_error(
            f"native Codex execution package is invalid: {exc}"
        ) from exc
    return build_native_codex_request(
        NativeCodexRequestSpec(
            context=native_context,
            target_branch=context.current_branch or state.branch,
            base_commit=base_commit,
            authorized_paths=tuple(sorted(set(authorized_paths))),
            assignment=request_assignment,
            work_context=effective_work_context,
            evidence=tuple(sorted(evidence, key=lambda item: item.evidence_id)),
        )
    )


def _native_review_acceptance_criteria(
    *,
    state: WorkflowState,
    context: Any,
    history: Any,
    contract: StepContract,
    review_kind: NativeReviewKind,
    plan_artifact_path: str | None,
    correction_goal: str | None,
    correction_criteria: tuple[str, ...],
) -> tuple[str, ...]:
    artifact_criterion = (
        f"The PLAN_ONLY artifact contract is active for exact path "
        f"{plan_artifact_path}; require that repository plan artifact and its "
        "mandated Slice structure."
        if plan_artifact_path is not None
        else (
            "No repository plan artifact is bound to this planning review. "
            "Review the request-bound SLICE_PLAN; the reviewer must not require "
            "PLAN_ONLY artifact structure."
            if review_kind is NativeReviewKind.PLAN
            else None
        )
    )
    unit_criteria = (
        correction_criteria
        if correction_goal is not None
        else ((context.slice_summary.strip(),) if context.slice_summary.strip() else ())
    )
    final_criterion = (
        "This is final-review disposition delivery round "
        f"{contract.round_number}. The bound disposition_budget permits "
        f"at most {len(history.findings)} total status changes or "
        "reclassifications and permits only these identifiers: "
        + (", ".join(item.finding_id for item in history.findings) or "(none)")
        + ". review_contract.previous_findings contains exactly that eligible "
        "subset. A non-empty partial disposition is a valid denied intermediate "
        "delivery and the remaining identifiers are offered in a later round. "
        "Return approved only when the request states that no undispositioned "
        "findings remain outside this offer and every offered identifier is "
        "dispositioned. A denied round with no status change and no "
        "reclassification ends the delivery sequence with the still-open "
        "findings as the verdict."
        if review_kind is NativeReviewKind.FINAL
        else None
    )
    correction_criterion = (
        "This review is part of an implementer correction sequence. A denied "
        "round continues only when it closes a finding, reclassifies one, or "
        "opens a new finding. A denied round with none of those record-derived "
        "transitions is the terminal review verdict for every still-open finding."
        if (
            state.current_work_unit.kind is WorkUnitKind.CORRECTION
            or project_implementer_return_policy(state.current_work_unit)[0] > 0
        )
        else None
    )
    return tuple(
        dict.fromkeys(
            criterion
            for criterion in (
                *unit_criteria,
                artifact_criterion,
                final_criterion,
                correction_criterion,
                FINDING_SIGNATURE_REVIEW_CRITERION,
                "The decision must satisfy the bound review contract and the "
                "fingerprint-matching deterministic validation attestation.",
                "The reviewed changes must remain within the exact authorized "
                "path boundary and preserve resume/idempotency invariants.",
            )
            if criterion is not None
        )
    )


def _review_request_finding_inputs(
    *,
    state: WorkflowState,
    history: Any,
    correction_findings: tuple[FindingRecord, ...] | None,
    execution_error: type[RuntimeError],
) -> tuple[tuple[FindingRecord, ...], str | None, tuple[str, ...]]:
    """Select review Findings from the caller's record-backed correction view."""

    correction_sequence = (
        state.current_work_unit.kind is WorkUnitKind.CORRECTION
        or project_implementer_return_policy(state.current_work_unit)[0] > 0
    )
    if correction_findings is None:
        if correction_sequence:
            raise execution_error(
                "native correction review lacks record-backed findings"
            )
        return history.findings, None, ()
    try:
        goal, criteria, selected = derive_correction_requirements(
            correction_findings,
            state.current_work_unit.open_findings,
        )
    except ReviewPacketError as exc:
        raise execution_error(
            "native correction review lacks its exact affected open finding set"
        ) from exc
    return selected, goal, criteria


def _native_review_retry_feedback(
    state: WorkflowState, contract: StepContract
) -> NativeReviewRetryFeedback | None:
    failure = next(
        (
            item
            for item in reversed(state.current_work_unit.invocation_failures)
            if item.step is state.current_step
            and item.native_review_rejection is not None
            and item.native_review_retry_round == contract.round_number
        ),
        None,
    )
    if failure is None or failure.native_review_rejection is None:
        return None
    code = NativeReviewErrorCode(failure.native_review_rejection)
    return NativeReviewRetryFeedback(
        prior_invocation_id=failure.invocation_id,
        rejection_code=code,
        correction_instruction=native_review_retry_guidance(code),
    )


def native_review_request(
    *,
    state: WorkflowState,
    context: Any,
    history: Any,
    contract: StepContract,
    changes: Any,
    evidence_kind: Any,
    review_diff: str,
    review_packet: ReviewPacket | None,
    expected_test_files: tuple[str, ...],
    execution_error: type[RuntimeError],
    full_branch_evidence_kind: object,
    final_review_pending_count: int | None = None,
    known_open_findings: tuple[FindingRecord, ...] | None = None,
    correction_findings: tuple[FindingRecord, ...] | None = None,
) -> NativeReviewRequestBundle:
    """Build the native request only from typed local workflow values."""
    review_kind = _NATIVE_REVIEW_KIND_BY_APPROVAL_MARKER[contract.approval_marker]
    plan_artifact_path = (
        context.work_plan_path
        if review_kind is NativeReviewKind.PLAN and context.plan_only
        else None
    )
    request_findings, correction_goal, correction_criteria = (
        _review_request_finding_inputs(
            state=state,
            history=history,
            correction_findings=correction_findings,
            execution_error=execution_error,
        )
    )
    if (
        state.current_work_unit.kind is not WorkUnitKind.CORRECTION
        and review_kind is not NativeReviewKind.FINAL
        and not context.slice_summary.strip()
    ):
        raise execution_error(
            "native non-correction review requires a current-slice summary"
        )
    offered_open_findings = project_open_set(request_findings).findings
    known_open_by_id = {
        item.finding_id: item
        for item in (
            *(() if known_open_findings is None else known_open_findings),
            *offered_open_findings,
        )
    }
    effective_known_open_findings = tuple(
        known_open_by_id[finding_id]
        for finding_id in sorted_finding_ids(known_open_by_id)
    )
    native_context = NativeReviewContext(
        run_id=state.run_id,
        work_unit_id=str(state.current_work_unit_id),
        operation=state.current_step.value,
        diff_fingerprint=changes.fingerprint,
        reviewer=contract.reviewer,
        approval_marker=contract.approval_marker,
        slice_id=contract.slice_id,
        round_number=contract.round_number,
        previous_findings=request_findings,
        known_open_findings=effective_known_open_findings or None,
        authoritative_finding_ids=contract.existing_finding_ids,
        validation_attestation=contract.validation_attestation,
        test_files=expected_test_files,
        test_changes_approved=contract.test_changes_approved,
        allow_new_observations=contract.allow_new_observations,
        anchor_origin=contract.anchor_origin,
        validation_command_prefixes=(
            context.validation_matrix.finding_command_prefixes
        ),
        red_state_followup_slice=contract.red_state_followup_slice,
        plan_artifact_path=plan_artifact_path,
        final_review_pending_count=(
            final_review_pending_count
            if review_kind is NativeReviewKind.FINAL
            else None
        ),
        planned_slices=state.planned_slices,
    )
    workflow_context = (
        context.render_distilled_context(
            unit_heading="CURRENT CORRECTION",
            unit_summary=correction_goal,
            current_scope_paths=(
                context.current_scope_paths or state.current_slice.scope_paths
            ),
        )
        if correction_goal is not None
        else context.distilled_context
    )
    oversized_full_branch_diff = (
        evidence_kind is full_branch_evidence_kind
        and review_packet is None
        and len(review_diff) > FULL_BRANCH_DIFF_EVIDENCE_CEILING_CHARS
    )
    review_evidence_kind = (
        PROVIDER_INPUT_BOUNDARY_EVIDENCE_KIND
        if oversized_full_branch_diff
        else evidence_kind.value
    )
    if oversized_full_branch_diff:
        encoded_review_diff = review_diff.encode("utf-8")
        review_evidence_content = json.dumps(
            {
                "boundary": "provider_input",
                "evidence_complete": False,
                "omitted_evidence": "full_branch_diff",
                "omitted_chars": len(review_diff),
                "omitted_utf8_bytes": len(encoded_review_diff),
                "omitted_sha256": hashlib.sha256(encoded_review_diff).hexdigest(),
                "repository_fingerprint": changes.fingerprint,
                "changed_paths": "See the request-level authorized_paths array.",
                "available_evidence": (
                    "The complete current repository snapshot is mounted read-only."
                ),
                "required_reviewer_action": (
                    "Inspect the current files needed for every review dimension with "
                    "Read. Do not infer that the omitted full diff was supplied. Deny "
                    "with a BLOCKER if a safe verdict requires unavailable baseline "
                    "content."
                ),
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    else:
        review_evidence_content = review_diff
    evidence: list[NativeReviewEvidenceInput] = [
        NativeReviewEvidenceInput("assignment", "assignment", context.assignment),
        NativeReviewEvidenceInput(
            "distilled-context", "workflow_context", workflow_context
        ),
    ]
    if review_packet is not None:
        evidence.append(
            NativeReviewEvidenceInput(
                "review-packet",
                "canonical_review_packet",
                review_packet.text,
                semantic_digest=review_packet.manifest.diff_coverage_digest,
            )
        )
    else:
        evidence.append(
            NativeReviewEvidenceInput(
                "review-diff", review_evidence_kind, review_evidence_content
            )
        )
    if evidence_kind is full_branch_evidence_kind:
        if history.codex_final_report is None:
            raise execution_error(
                "native Claude final review requires a persisted Codex final "
                "report before request construction"
            )
        evidence.append(
            NativeReviewEvidenceInput(
                "codex-final-report",
                "codex_final_report",
                history.codex_final_report,
            )
        )
    acceptance_criteria = _native_review_acceptance_criteria(
        state=state,
        context=context,
        history=history,
        contract=contract,
        review_kind=review_kind,
        plan_artifact_path=plan_artifact_path,
        correction_goal=correction_goal,
        correction_criteria=correction_criteria,
    )
    if oversized_full_branch_diff:
        acceptance_criteria = (
            *acceptance_criteria,
            "The evidence manifest explicitly marks the oversized full branch diff "
            "as withheld at the provider-input boundary. Inspect the required current "
            "files in the complete read-only repository snapshot. Never treat the "
            "request as complete diff evidence; deny with a BLOCKER when unavailable "
            "baseline content is required for a safe verdict.",
        )
    retry_feedback = _native_review_retry_feedback(state, contract)
    return build_native_review_request(
        NativeReviewRequestSpec(
            context=native_context,
            review_kind=review_kind,
            target_branch=context.current_branch or state.branch,
            base_commit=changes.start_commit,
            authorized_paths=tuple(sorted(set(changes.paths))),
            acceptance_criteria=acceptance_criteria,
            evidence=tuple(sorted(evidence, key=lambda item: item.evidence_id)),
            retry_feedback=retry_feedback,
        )
    )
