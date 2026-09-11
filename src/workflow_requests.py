"""Pure construction of provider-bound workflow requests.

The workflow engine remains the composition root for execution errors and run
control.  This module consumes only the values supplied by that engine and has
no reverse import or mutable engine dependency.
"""

from __future__ import annotations

import json
from typing import Any

from contracts import (
    ApprovalMarker,
    CodexStepContract as ImplementerStepContract,
    StepContract,
)
from finding_order import sorted_finding_ids
from finding_reducer import project_open_set, project_request_subset
from native_codex_contract import NativeCodexContext, NativeCodexRequestKind
from native_codex_request import (
    NativeCodexEvidenceInput,
    NativeCodexRequestBundle,
    NativeCodexRequestSpec,
    build_native_codex_request,
)
from native_review_contract import NativeReviewContext
from native_review_request import (
    NativeReviewEvidenceInput,
    NativeReviewKind,
    NativeReviewRequestBundle,
    NativeReviewRequestSpec,
    build_native_review_request,
)
from prompts import NATIVE_CODEX_SYSTEM_POLICY
from provider_input_efficiency import (
    ProviderInputEfficiencyError,
    build_correction_execution_package,
    build_slice_execution_package,
)
from review_packets import ReviewPacket
from workflow_state import SliceStatus, WorkflowState, WorkUnitKind


MAX_FINAL_REVIEW_DISPOSITION_ROUNDS = 4


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
    authorized_paths = tuple(
        sorted({*authorized_paths, *additional_authorized_paths})
    )
    # The policy is a separately digested evidence item. Repeating it in the
    # work context would make one logical input appear twice in the same
    # provider request and would weaken component-level accounting.
    effective_work_context = (
        context.distilled_context if work_context is None else work_context
    )
    if additional_authorized_paths:
        effective_work_context += (
            "\n\nFINGERPRINT-BOUND ORCHESTRATOR PATH AUTHORIZATION\n"
            + "\n".join(additional_authorized_paths)
            + "\nThese exact paths were approved by a user gate for the current "
            "repository fingerprint. They are part of this request's authoritative "
            "allowlist even when absent from the original plan. Their presence is "
            "not an UNEXPECTED-PATH condition."
        )
    native_findings = history.findings
    if request_kind is NativeCodexRequestKind.CORRECTION:
        affected_ids = sorted_finding_ids(state.current_work_unit.open_findings)
        try:
            request_projection = project_request_subset(
                history.findings,
                finding_ids=affected_ids,
                open_only=True,
            )
        except ValueError as exc:
            raise execution_error(
                "native correction request lacks its exact affected open finding set"
            ) from exc
        native_findings = request_projection.findings
        if request_projection.finding_ids != affected_ids:
            raise execution_error(
                "native Codex correction lacks its exact affected open finding set"
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
) -> NativeReviewRequestBundle:
    """Build the native request only from typed local workflow values."""
    review_kind = {
        ApprovalMarker.PLAN: NativeReviewKind.PLAN,
        ApprovalMarker.SLICE: NativeReviewKind.SLICE,
        ApprovalMarker.FINAL: NativeReviewKind.FINAL,
    }[contract.approval_marker]
    plan_artifact_path = (
        context.work_plan_path
        if review_kind is NativeReviewKind.PLAN and context.plan_only
        else None
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
        previous_findings=history.findings,
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
    )
    evidence: list[NativeReviewEvidenceInput] = [
        NativeReviewEvidenceInput("assignment", "assignment", context.assignment),
        NativeReviewEvidenceInput(
            "distilled-context", "workflow_context", context.distilled_context
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
            NativeReviewEvidenceInput("review-diff", evidence_kind.value, review_diff)
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
    acceptance_criteria = tuple(
        dict.fromkeys(
            criterion
            for criterion in (
                context.slice_summary.strip(),
                artifact_criterion,
                (
                    "This is final-review disposition delivery round "
                    f"{contract.round_number} of "
                    f"{MAX_FINAL_REVIEW_DISPOSITION_ROUNDS}. "
                    "review_contract.previous_findings contains exactly the "
                    "still-undispositioned finding identifiers. Approval requires "
                    "one status change or reclassification for every listed "
                    "identifier; an incomplete approved response is rejected with "
                    "all missing identifiers named. If the round delivers only a "
                    "non-empty subset, deny it as an intermediate delivery; that "
                    "subset is never an approval."
                    if review_kind is NativeReviewKind.FINAL
                    else None
                ),
                "The decision must satisfy the bound review contract and the "
                "fingerprint-matching deterministic validation attestation.",
                "The reviewed changes must remain within the exact authorized "
                "path boundary and preserve resume/idempotency invariants.",
            )
            if criterion is not None
        )
    )
    return build_native_review_request(
        NativeReviewRequestSpec(
            context=native_context,
            review_kind=review_kind,
            target_branch=context.current_branch or state.branch,
            base_commit=changes.start_commit,
            authorized_paths=tuple(sorted(set(changes.paths))),
            acceptance_criteria=acceptance_criteria,
            evidence=tuple(sorted(evidence, key=lambda item: item.evidence_id)),
        )
    )
