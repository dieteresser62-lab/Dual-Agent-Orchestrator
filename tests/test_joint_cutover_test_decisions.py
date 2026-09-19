from __future__ import annotations

import ast
from pathlib import Path

import pytest

from workflow_state import WorkflowStep, WorkUnitKind


TEST_ROOT = Path(__file__).parent


def _defined_test_functions() -> frozenset[str]:
    names: set[str] = set()
    for path in TEST_ROOT.glob("test_*.py"):
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names.update(
            node.name
            for node in module.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
        )
    return frozenset(names)


DEFINED_TEST_FUNCTIONS = _defined_test_functions()


# Every row is one of the 55 cases that was skipped during the incomplete
# joint cutover.  A replacement names the active test that now protects the
# still-valid property; None records that only the retired mechanism was
# under test.  Keeping the decisions executable prevents a blanket skip from
# returning and makes every deletion individually reviewable.
CUTOVER_TEST_DECISIONS = (
    (
        "test_final_denial_accepts_nonempty_partial_observation_delivery",
        "test_slice_denial_accepts_nonempty_sparse_blocker_delivery",
        "Sparse reviewer updates remain valid in a same-Slice denial.",
    ),
    (
        "test_final_denial_accepts_zero_progress_with_open_observations",
        "test_slice_denial_preserves_open_blockers_for_no_progress_policy",
        "A no-progress denial keeps the authoritative open set for E5.",
    ),
    (
        "test_seventy_five_finding_final_approval_names_exact_missing_dispositions",
        "test_seventy_five_finding_plan_approval_names_exact_missing_updates",
        "Large approval diagnostics still name every missing update.",
    ),
    (
        "test_production_final_context_cannot_reclassify_blocker_to_observation",
        "test_same_slice_correction_cannot_reclassify_blocker_to_observation",
        "A same-Slice correction cannot defer its blocker as an observation.",
    ),
    (
        "test_large_final_denial_accepts_thirty_closures_and_three_escalations",
        None,
        "The removed in-run final-review batching mechanism owned this case.",
    ),
    (
        "test_final_disposition_budget_rejects_combined_event_overflow",
        None,
        "The removed final-review disposition batch owned this overflow path.",
    ),
    (
        "test_final_denial_accepts_closed_offer_when_unoffered_findings_remain",
        None,
        "Partial final-review offers no longer exist in linked discovery runs.",
    ),
    (
        "test_large_final_approval_names_every_missing_disposition[31-33]",
        None,
        "The first parameter case measured removed final-review batch completion.",
    ),
    (
        "test_large_final_approval_names_every_missing_disposition[31-32]",
        None,
        "The second parameter case measured removed final-review batch completion.",
    ),
    (
        "test_final_review_rejects_new_observation_and_open_own_observation",
        None,
        "Terminal discovery reports occurrences and cannot approve a family itself.",
    ),
    (
        "test_writer_schema_keeps_nonblank_text_validation_fail_closed_locally",
        "test_writer_schema_keeps_nonblank_text_validation_fail_closed_locally",
        "Nonblank text remains locally enforced for every active Codex request kind.",
    ),
    (
        "test_final_report_roundtrips_self_check_and_finding_response",
        None,
        "The Codex final-report and self-check result type was removed.",
    ),
    (
        "test_final_report_rejects_incomplete_or_foreign_finding_dispositions",
        None,
        "Final-report finding dispositions were replaced by typed linked handoffs.",
    ),
    (
        "test_final_report_requires_nonblank_self_check",
        None,
        "The retired final-report self_check field has no active equivalent.",
    ),
    (
        "test_not_ready_final_report_resumes_without_a_final_report_mirror",
        None,
        "There is no Codex final-report step or mirror in the linked-run workflow.",
    ),
    (
        "test_legacy_final_review_keeps_budget_fact_without_structured_preflight",
        None,
        "The test exercised a legacy final-review compatibility branch.",
    ),
    (
        "test_final_preflight_missing_prerequisite_rewinds_without_manual_resume",
        "test_branch_discovery_preflight_missing_prerequisite_halts_for_resume",
        "Missing terminal prerequisites still fail closed at branch discovery.",
    ),
    (
        "test_final_review_references_committed_slice_and_appends_bounded_correction",
        None,
        "Branch discovery exports a new run instead of appending a correction unit.",
    ),
    (
        "test_correction_slice_remediation_scope_includes_current_slice_report_path",
        None,
        "The removed correction work unit owned its separate remediation scope.",
    ),
    (
        "test_final_review_requires_all_slices_committed",
        None,
        "The in-run final-review entry gate was removed with that work unit.",
    ),
    (
        "test_final_review_structured_records_use_branch_wide_fingerprint",
        "test_branch_discovery_structured_records_use_branch_wide_fingerprint",
        "Terminal review records remain bound to the branch-wide fingerprint.",
    ),
    (
        "test_final_review_recovers_latest_prior_attestation_after_transition_checkpoint",
        "test_branch_discovery_reuses_carried_attestation_in_its_audit_history",
        "An unchanged branch discovery run reuses its carried attestation.",
    ),
    (
        "test_runtime_inherits_exact_prior_test_gate_before_early_resume_return",
        "test_runtime_inherits_exact_prior_test_gate_at_linked_family_boundary",
        "An exact approved test fingerprint remains reusable across the family edge.",
    ),
    (
        "test_final_review_attestation_recovery_receives_record_authority",
        None,
        "It only inspected the removed in-run prior-history lookup plumbing.",
    ),
    (
        "test_combined_native_finding_authority_ignores_projection_drift",
        None,
        "The combined final/correction authority route no longer exists.",
    ),
    (
        "test_real_cleanup_round_two_keeps_first_record_scope_and_replays",
        None,
        "Finding-cleanup rounds were removed from the active protocol.",
    ),
    (
        "test_cleanup_is_dormant_but_logs_record_balance_when_runtime_history_is_empty",
        None,
        "Finding-cleanup dormancy was part of the retired state machine.",
    ),
    (
        "test_cleanup_review_builds_exact_record_backed_followup_correction",
        None,
        "Discovery now creates a linked ordinary plan rather than cleanup correction.",
    ),
    (
        "test_correction_work_unit_persists_correction_work_unit_payload_with_finding_ids",
        None,
        "CorrectionWorkUnit is legacy replay data, not an active work unit.",
    ),
    (
        "test_checkpoint_archives_latest_driver_history_across_work_unit_transition",
        None,
        "The asserted final-report cache transition was removed.",
    ),
    (
        "test_final_correction_rejects_audit_only_persisted_scope",
        None,
        "There is no terminal correction work unit with audit-only scope.",
    ),
    (
        "test_pending_audit_commit_is_resumable_during_final_review",
        None,
        "The in-run audit commit at final review no longer exists.",
    ),
    (
        "test_terminal_final_denial_persists_failed_completion_without_binding",
        None,
        "Terminal final denial was replaced by a discovery handoff to a new run.",
    ),
    (
        "test_final_review_recovery_rebuilds_exact_record_bound_finding_subset",
        None,
        "Final-review disposition-batch recovery was removed.",
    ),
    (
        "test_retired_iteration_gate_terminates_from_persisted_no_progress",
        "test_cutover_convergence_stall_ends_gate_free_without_slice_commit",
        "E5 still terminates a stalled convergence round negatively and gate-free.",
    ),
    (
        "test_combined_native_final_restart_rebinds_codex_and_claude_without_legacy_parsers",
        "test_complete_e9_discovery_handoff_round_trips_and_replays",
        "The native restart boundary is now the typed E9 linked-run handoff.",
    ),
    (
        "test_final_review_disposes_seventy_five_findings_over_bound_rounds",
        None,
        "Terminal disposition batching was removed; discovery never disposes findings.",
    ),
    (
        "test_final_review_with_few_findings_completes_in_one_round",
        None,
        "Final-review disposition rounds do not exist in branch discovery.",
    ),
    (
        "test_final_review_continues_beyond_the_retired_four_round_limit",
        "test_review_denials_continue_beyond_four_while_progress_is_made",
        "Progressing same-Slice convergence still has no four-round limit.",
    ),
    (
        "test_final_review_round_four_follows_three_disposition_rounds",
        None,
        "The asserted fourth disposition-batch request was removed.",
    ),
    (
        "test_over_budget_final_result_is_rejected_and_retried_with_smaller_offer",
        "test_large_plan_approval_rejects_combined_disposition_overflow",
        "The active native review contract still rejects disposition overflow.",
    ),
    (
        "test_disposition_limit_retry_does_not_depend_on_transient_retry_policy",
        None,
        "Its special smaller-offer retry belonged to final-review batching.",
    ),
    (
        "test_disposition_batches_preserve_previously_reclassified_blockers",
        None,
        "Discovery cannot reclassify or batch-dispose inherited blockers.",
    ),
    (
        "test_round_four_uses_authoritative_pending_total_after_prior_dispositions",
        None,
        "Pending totals per final-review batch are no longer protocol state.",
    ),
    (
        "test_final_review_rounds_end_immediately_when_dispositions_make_no_progress",
        "test_cutover_convergence_stall_ends_gate_free_without_slice_commit",
        "No-progress termination remains enforced by same-Slice convergence.",
    ),
    (
        "test_combined_native_post_correction_final_transition_carries_complete_ledger",
        "test_record_replay_matrix_has_independent_literal_oracle_and_failure_windows",
        "The active linked transition matrix carries the complete authoritative ledger.",
    ),
    (
        "test_native_codex_final_report_bypasses_legacy_marker_parser",
        "test_run_native_codex_agent_parses_bound_result_without_text_contract",
        "All active Codex variants still bypass the legacy marker parser.",
    ),
    (
        "test_native_codex_request_builder_covers_plan_and_final_report",
        "test_native_request_builder_covers_plan_slice_and_branch_discovery_reviews",
        "The request builder now covers the three active linked-run contexts.",
    ),
    (
        "test_native_final_review_rejects_missing_codex_report_before_request",
        None,
        "Branch discovery has no Codex final-report prerequisite.",
    ),
    (
        "test_final_review_resumes_redundant_gate_from_exact_prior_test_approval",
        "test_runtime_inherits_exact_prior_test_gate_at_linked_family_boundary",
        "Exact test approval reuse moved to the linked family boundary.",
    ),
    (
        "test_codex_final_report_not_ready_is_resumable_without_review",
        "test_codex_not_ready_persists_gate_and_resumes_same_step",
        "Codex not-ready remains resumable at the active execution step.",
    ),
    (
        "test_workflow_history_roundtrips_final_report_and_loads_legacy_shape",
        None,
        "The final-report history mirror was intentionally removed.",
    ),
    (
        "test_terminable_quota_resumes_same_final_review_for_watch_completion",
        "test_multiple_progressing_quota_windows_resume_automatically",
        "Quota progress still resumes the same semantic provider step.",
    ),
    (
        "test_large_correction_preserves_finding_binding_and_review_resume_idempotency",
        "test_native_codex_correction_binds_record_authority_before_recovery",
        "Same-Slice correction recovery remains request- and finding-bound.",
    ),
    (
        "test_recovered_final_review_uses_request_time_batch_after_dispositions_persist",
        "test_reviewer_response_uses_request_ledger_after_finding_is_closed",
        "Recovered reviewer output still compares against its request-time ledger.",
    ),
)


@pytest.mark.parametrize(
    ("retired_case", "replacement", "rationale"),
    CUTOVER_TEST_DECISIONS,
    ids=[case[0].removeprefix("test_") for case in CUTOVER_TEST_DECISIONS],
)
def test_each_cutover_skip_has_an_executable_individual_decision(
    retired_case: str,
    replacement: str | None,
    rationale: str,
) -> None:
    retired_function = retired_case.split("[", 1)[0]
    assert rationale.strip()
    if replacement == retired_function:
        assert retired_function in DEFINED_TEST_FUNCTIONS
    else:
        assert retired_function not in DEFINED_TEST_FUNCTIONS
    if replacement is not None:
        assert replacement in DEFINED_TEST_FUNCTIONS


def test_cutover_skip_reasons_cannot_return() -> None:
    forbidden_reasons = (
        "legacy final-correction state machine removed by joint cutover",
        "joint cutover removed the in-run final review",
        "cutover pending",
    )
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in TEST_ROOT.glob("test_*.py")
        if path != Path(__file__)
    )
    assert all(reason not in sources for reason in forbidden_reasons)


@pytest.mark.parametrize(
    ("enum_type", "member_name", "wire_value"),
    (
        (WorkUnitKind, "PLAN", "plan"),
        (WorkUnitKind, "SLICE", "slice"),
        (WorkUnitKind, "BRANCH_DISCOVERY", "branch_discovery"),
        (WorkflowStep, "CODEX_PLAN", "codex_plan"),
        (WorkflowStep, "CLAUDE_PLAN_REVIEW", "claude_plan_review"),
        (WorkflowStep, "CODEX_PLAN_REVISION", "codex_plan_revision"),
        (WorkflowStep, "CODEX_IMPLEMENTATION", "codex_implementation"),
        (WorkflowStep, "CLAUDE_SLICE_REVIEW", "claude_slice_review"),
        (WorkflowStep, "CODEX_CORRECTION", "codex_correction"),
        (WorkflowStep, "SLICE_COMMIT", "slice_commit"),
        (WorkflowStep, "CLAUDE_BRANCH_DISCOVERY", "claude_branch_discovery"),
        (WorkflowStep, "COMPLETED", "completed"),
    ),
)
def test_joint_cutover_state_vocabulary_is_closed_and_round_trips(
    enum_type: type[WorkUnitKind] | type[WorkflowStep],
    member_name: str,
    wire_value: str,
) -> None:
    member = enum_type[member_name]
    assert member.value == wire_value
    assert enum_type(wire_value) is member
    assert len(enum_type) == (3 if enum_type is WorkUnitKind else 9)
