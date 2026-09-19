from __future__ import annotations

import ast
from collections import Counter
import hashlib
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "docs" / "internal" / "stabilisierung-s2-uebergangsmatrix.md"

MIGRATION_MISMATCH_MARKERS = (
    "expected exactly one task record, found",
    "task contract differs from state-v3",
    "work-unit record has no state-v3 counterpart",
    "work-unit round, slice, or path allowlist differs",
    "correction work-unit finding attribution differs from state-v3",
    "work-unit finding import binding differs from state-v3",
    "latest work-unit path allowlist differs from state-v3",
    "latest work-unit round differs from state-v3",
    "latest work-unit finding state differs from state-v3",
    "latest correction finding attribution differs from state-v3",
    "current work unit has no structured record",
    "current work-unit round differs from the record chain",
    "approved plan has no structured record",
    "expected exactly one immutable approved-plan record, found",
    "state-v3 mirror is missing its approved-plan commit binding",
    "approved-plan binding differs from state-v3",
    "finding transitions differ from state-v3",
    "finding status differs from state-v3",
    "imported finding status differs from state-v3",
    "validation attestations differ from state-v3",
    "quota pauses differ from state-v3",
    "transient retries differ from state-v3",
    "bootstrap checks differ from state-v3",
    "bootstrap check payload differs from state-v3",
    "resume check is not bound to its prior record head",
    "binding references an unknown, invalid, or fingerprint-mismatched validation attestation",
    "binding references an unknown, unapproved, or fingerprint-mismatched review",
    "commit binding has no state-v3 counterpart",
    "completed slice is missing a structured commit binding",
    "state-v3 mirror reports workflow completion without a structured record",
    "workflow completion differs from state-v3",
    "workflow completion references an unknown, invalid, or fingerprint-mismatched final binding",
    "validation content has no complete state-v3 counterpart",
    "validation output or digest differs from state-v3",
    "final-report content differs from state-v3",
    "final-report bytes differ from state-v3",
    "active review packets differ from state-v3",
    "active review packet bytes or metadata differ from state-v3",
    "review contract projection is ambiguous",
    "latest review mirror has no aggregate field",
    "latest review differs from its record projection",
)

RESUME_ERROR_MARKERS = (
    "run identity differs from state-v3",
    "run profile differs from state-v3",
    "structured-v2 run has no workflow transition prefix",
    "structured-v2 run has no workflow policy prefix",
    "structured-v2 run has no complete workflow event prefix",
    "workflow cursor differs from state-v3",
    "slice statuses differ from state-v3",
    "work-unit statuses or steps differ from state-v3",
    "workflow policies differ from state-v3",
    "structured-v2 run has no slice boundary prefix",
    "slice boundaries differ from state-v3",
    "structured-v2 run has no gate transition prefix",
    "gate transitions differ from state-v3",
    "gate decision bindings differ from state-v3",
    "structured-v2 run has invocation failures but no R6 failure records",
    "invocation failure identity cannot be projected into state-v3",
    "invocation failures differ from state-v3",
    "record chain has an ambiguous invocation failure suffix",
    "record-ahead invocation failure cannot rehydrate the current work unit",
    "is historical and cannot be resumed",
    "structured-v2 state lacks the complete native Codex-Claude transport binding",
    "record chain for run",
    "has no records; restore its record",
    "exc.diagnostic.message",
    "finding handoff mirror is incomplete",
    "record chain contains an unbound finding import",
    "finding handoff requires exactly one import, found",
    "finding handoff source is no longer valid",
    "finding import differs from its revalidated source",
)

RUN_BINDING_REPLAY_MARKER = (
    "record chain requires exactly one run identity and run profile"
)

BRIDGE_ERROR_MARKERS = (
    "artifact batch requires at least two entries",
    "atomic artifact batch is only partially present",
    "referenced source record is not a finding export",
    "finding export requires a non-empty accepted replay",
    "finding export requires its approved reviewer record",
    "finding export plan commit is not present in accepted replay",
    "finding export requires at least one source transition",
    "finding import requires a finding handoff export record",
    "finding export record is not in the accepted source replay",
    "finding export record belongs to another source run",
    "finding export differs from its accepted source replay",
    "finding import task bytes differ from the export binding",
    "structured artifact differs semantically from its existing record",
    "provider attempt measurement is not in the accepted chain",
    "provider attempt work unit differs from its measurement",
    "provider attempt operation instance must be non-empty",
    "provider attempt immutable binding differs from its first attempt",
    "provider attempt requires one terminal direct predecessor",
    "provider attempt start is not in the accepted chain",
    "provider attempt terminal requires a started record",
    "provider attempt terminal differs from its durable result",
    "referenced finding export record is missing",
    "finding export plan commit differs from the task",
    "branch discovery export differs from target import type",
    "finding export differs from target import type",
    "branch discovery target task is outside the repository",
    "branch discovery handoff requires a non-empty finding snapshot",
    "branch discovery handoff export requires a non-empty accepted replay",
    "branch discovery handoff export requires a family binding",
    "branch discovery handoff target execution mode is invalid",
    "branch discovery handoff export requires its BRANCH_DISCOVERY_COMPLETED record; approved is not scan completion",
    "branch discovery handoff export requires its validation attestation record",
    "branch discovery completion and validation attestation fingerprints differ",
    "branch discovery completion differs from its validation or HEAD binding",
    "BRANCH_DISCOVERY family handoff requires a completed source run",
    "branch discovery handoff export requires at least one source transition",
    "branch discovery target family predecessor differs from the source head",
    "branch discovery import requires a branch discovery export record",
    "branch discovery import requires the target RunProfile family binding",
    "branch discovery export record is not resolvable in the source run",
    "branch discovery export must be the accepted source replay head",
    "branch discovery export source run or bound source head differs",
    "branch discovery export differs from its flattened source history",
    "branch discovery import target_task_sha256 differs from task bytes",
    "branch discovery import target_task_path differs from queue position",
    "branch discovery import target_run_identity differs from target run",
    "branch discovery import family binding differs from target RunProfile",
    "family handoff does not reference a branch discovery export",
    "branch discovery source has no RunProfile family binding",
    "branch discovery export is not the source run head",
    "branch discovery target_task_sha256 differs from loaded task bytes",
    "branch discovery export requires its bound family target task",
    "branch discovery export target mode differs from the target task",
    "branch discovery import requires the target family binding",
    "branch discovery payload requires BRANCH_DISCOVERY_COMPLETED",
    "branch discovery completion cannot carry approved, denied, or stop",
    "branch discovery completion requires evidence and pre_mortem",
    "branch discovery occurrence references an unknown prior finding",
    "family acceptance may be derived only from its own BRANCH_DISCOVERY run",
    "family acceptance requires one completed scan and one terminal workflow",
    "family acceptance completion order is invalid",
    "BRANCH_DISCOVERY remediation handoff requires its cohort checkpoint",
    "remediation cohort checkpoint differs from the source run family",
    "plan assignment requires a branch discovery Finding snapshot",
    "plan assignment source snapshot must target PLAN_ONLY",
    "plan assignment records do not belong to the snapshot-bound plan run",
    "plan assignment requires a ready native implementer plan result",
    "plan assignment requires a positive plan Review record",
    "plan assignment result and Review use different work units",
    "plan assignment requires a typed family binding",
    "plan assignment family binding differs from its source snapshot",
    "plan assignment open finding lacks BRANCH_PLANNING responsibility: ",
    "plan assignment open finding has foreign or future BRANCH_PLANNING responsibility: ",
    "persisted implementer plan treatment is invalid: ",
    "plan assignment coverage is invalid: ",
    "remediation cohort checkpoint requires a PlanAssignment record",
    "remediation cohort checkpoint family differs from implementation run",
    "remediation cohort checkpoint requires an implementation record head",
    "recorded remediation round lacks PlanAssignment",
    "recorded remediation round lacks its pre-discovery cohort checkpoint",
    "recorded remediation round lacks BRANCH_DISCOVERY_COMPLETED",
    "recorded remediation round lacks its BRANCH_DISCOVERY handoff import",
    "recorded remediation round has inconsistent assignment and checkpoint",
    "BRANCH_DISCOVERY result is not bound to the recorded remediation cohort",
)

RECOVERABLE_FUNCTIONS: dict[str, set[str]] = {}

DRIVER_DIVERGENCE_MESSAGES = Counter(
    {
        "structured commit review differs from the commit request": 1,
        "structured commit attestation differs from the commit request": 1,
        "provider attempt measurement context diverged": 1,
        "native agent request immutable binding differs: field=binding_fingerprint previous=": 1,
        "native Codex raw response differs from its persisted artifact": 2,
        "content-addressed review packet cache differs from canonical bytes": 1,
        "native agent recovery has divergent agent-result records": 1,
        "native agent recovery result idempotency binding differs": 1,
        "native provider content digest differs from its record": 1,
        "native implementer recovery raw response digest differs from its record": 1,
        "native Codex recovery record differs from its durable binding": 1,
        "native Codex recovery result differs from its durable record": 1,
        "native reviewer recovery record differs from the rebuilt request": 1,
        "native reviewer recovery result differs from its decision record": 1,
        "pre-policy native reviewer result differs from its decision record": 1,
        "pre-policy native reviewer recovery tail differs from the active work unit": 1,
        "native agent result logical binding differs": 1,
        "native agent content digest differs from its result binding": 1,
        "native reviewer content digest differs from its review binding": 1,
        "native review persistence differs from its exact review context": 1,
        "validation recovery result differs from its content": 1,
        "persisted finding handoff export differs from the prepared task": 1,
        "file side-effect target differs before result completion": 1,
        "invocation failure work unit differs from the active workflow": 1,
        "structured audit dual-write mismatch: ": 2,
        "workflow projection checkpoint path differs from its cursor": 2,
    }
)

WATCH_DIVERGENCE_MESSAGES = Counter(
    {
        "queue success evidence binding digest differs": 1,
        "rejection marker task digest differs": 1,
        "rejection marker evidence digest differs": 1,
        "watch identity task digest differs from queue task": 1,
        "queue success evidence source binding differs": 1,
        "queue success evidence run id differs": 1,
        "queue success evidence task digest differs": 1,
        "queue success evidence protocol mode differs": 1,
        "queue success evidence differs from watch identity": 1,
        "terminal workflow result differs from watch identity": 1,
        "queue source digest differs from success evidence": 1,
        "bound queue destination digest differs from success evidence": 1,
        "ledgered queue destination differs from the task binding": 1,
        "ledgered queue destination differs before result completion": 1,
        "bound queue destination differs before ledger completion": 1,
        "Workflow result run id %s differs from persisted watch identity %s for %s.": 1,
        "Workflow protocol mode %s differs from persisted watch identity %s for %s.": 1,
    }
)

PREFLIGHT_DENIAL_CODES = {
    "MEASUREMENT-TYPE",
    "OPERATION-NOT-FINAL",
    "STATE-TRANSITION-MISMATCH",
    "MEASUREMENT-RUN-MISMATCH",
    "MEASUREMENT-DENIED",
    "PREMATURE-COMPLETION",
    "FOREIGN-RUN-RECORD",
    "MISSING-REFERENCE",
    "FINGERPRINT-MISMATCH",
    "UNAUTHORIZED-PATH",
    "ATTESTATION-MISSING",
    "ATTESTATION-FAILED",
}

ADDITIONAL_BOUNDARY_MARKERS = {
    "src/orchestrator.py": {
        "bound success evidence differs from terminal workflow state",
        "Terminal workflow result differs from bound watch task identity",
    },
    "src/workflow_run_setup.py": {
        "differs from the immutable persisted profile",
    },
    "src/workflow_production.py": {
        "persisted task identity differs from --resume task",
        "persisted task contract differs from --resume task",
        "persisted watch run identity differs from inbox task",
    },
    "src/workflow.py": {
        "workflow history review packet cache differs from canonical bytes",
    },
}

WORKFLOW_STATE_FIELD_INVENTORY = {
    "ProtocolBinding": {
        "mode",
        "schema_version",
        "claude_review_transport",
        "codex_result_transport",
        "codex_profile",
        "claude_profile",
    },
    "InvocationFailureRecord": {
        "invocation_id",
        "idempotency_key",
        "role",
        "failure_kind",
        "provider_text",
        "received_at",
        "step",
        "slice_id",
        "work_unit_id",
        "diagnostic_exit_code",
        "process_exit_code",
        "technical_text",
        "parse_path",
        "source_timezone",
        "reset_at_utc",
        "resume_at_utc",
        "safety_margin_seconds",
        "auto_resume_count",
        "automatic_resume",
        "diff_fingerprint",
        "native_review_rejection",
        "native_review_retry_round",
    },
    "GateRecord": {"status", "reason", "detail", "fingerprint", "paths", "resume_step"},
    "GateDecisionRecord": {
        "approved",
        "reason",
        "fingerprint",
        "paths",
        "rationale",
        "resume_step",
    },
    "SliceRecord": {
        "slice_id",
        "status",
        "start_commit",
        "scope_paths",
        "scope_change_groups",
        "start_fingerprint",
        "commit_ref",
    },
    "WorkUnitRecord": {
        "work_unit_id",
        "slice_id",
        "kind",
        "status",
        "current_step",
        "round_number",
        "codex_return_count",
        "max_codex_returns",
        "gate",
        "reviewer",
        "open_findings",
        "completed_side_effects",
        "gate_decisions",
        "active_test_fingerprint",
        "active_test_paths",
        "invocation_failures",
    },
    "ResumeCursor": {
        "work_unit_id",
        "slice_id",
        "step",
        "round_number",
        "completed_side_effects",
    },
    "BootstrapCheckFact": {
        "check_kind",
        "transition_fingerprint",
        "provider",
        "role",
        "operation",
        "work_unit_id",
        "semantic_digest",
        "decision",
        "error_code",
    },
    "WorkflowState": {
        "version",
        "run_id",
        "task_file",
        "branch",
        "branch_base",
        "created_at",
        "updated_at",
        "current_slice_id",
        "current_work_unit_id",
        "current_step",
        "slices",
        "work_units",
        "planned_slices",
        "runtime_history",
        "task_digest",
        "execution_mode",
        "task_scope_patterns",
        "work_plan_path",
        "approved_plan_commit",
        "finding_handoff_source_run_id",
        "finding_handoff_export_record_id",
        "audit_report_path",
        "target_branch",
        "protocol_binding",
        "bootstrap_checks",
        "finding_responsibilities",
        "family_binding",
    },
}

COMPARISON_TARGETS = (
    ("src/artifact_resume.py", None, "resolve_resume_state"),
    ("src/artifact_resume.py", None, "require_workflow_status_prefix"),
    ("src/artifact_resume.py", None, "require_workflow_event_prefix"),
    ("src/artifact_resume.py", None, "require_gate_prefix"),
    ("src/artifact_resume.py", None, "require_side_effect_ledger_prefix"),
    ("src/artifact_replay.py", None, "_validate_workflow_transitions_and_events"),
    ("src/artifact_replay.py", None, "_validate_invocation_failures_and_retries"),
    ("src/artifact_replay.py", None, "_validate_gate_transitions_and_decisions"),
    ("src/artifact_replay.py", None, "_index_validation_content"),
    ("src/artifact_replay.py", None, "_validate_attestation_content_bindings"),
    ("src/artifact_replay.py", None, "_validate_unbound_validation_content"),
    ("src/artifact_replay.py", None, "_validate_provider_decision_content"),
    ("src/artifact_replay.py", None, "_validate_unbound_provider_content"),
    ("src/artifact_replay.py", None, "_validate_review_anchors"),
    ("src/artifact_replay.py", None, "_validate_review_validation_bindings"),
    ("src/artifact_replay.py", None, "_validate_required_review_authority"),
    ("src/artifact_replay.py", None, "_validate_review_packet_bindings"),
    ("src/artifact_replay.py", None, "_validate_work_unit_revisions"),
    ("src/artifact_replay.py", None, "_validate_single_finding_import"),
    ("src/artifact_replay.py", None, "_validate_finding_handoff_record"),
    ("src/artifact_replay.py", None, "_validate_branch_discovery_handoff_record"),
    ("src/artifact_replay.py", None, "_validate_branch_discovery_export"),
    ("src/artifact_replay.py", None, "_validate_branch_discovery_import"),
    ("src/artifact_replay.py", None, "_validate_work_unit_finding_import"),
    ("src/artifact_replay.py", None, "_validate_work_unit_activity_reference"),
    ("src/artifact_replay.py", None, "_validate_bound_record_references"),
    ("src/artifact_replay.py", None, "_validate_chain_record_references"),
    ("src/artifact_replay.py", None, "_validate_provider_attempt_sequences"),
    ("src/artifact_replay.py", None, "_validate_side_effect_sequences"),
    ("src/artifact_replay.py", None, "_validate_payload_references"),
    ("src/artifact_bridge.py", None, "finding_handoff_export_payload"),
    ("src/artifact_bridge.py", None, "finding_handoff_import_payload"),
    ("src/artifact_bridge.py", None, "_finding_snapshot"),
    ("src/artifact_bridge.py", None, "branch_discovery_handoff_export_payload"),
    ("src/artifact_bridge.py", None, "branch_discovery_handoff_import_payload"),
    ("src/artifact_bridge.py", None, "review_payload_matches_result"),
    ("src/artifact_bridge.py", "ArtifactBridge", "append"),
    ("src/artifact_bridge.py", "ArtifactBridge", "record_side_effect_intent"),
    ("src/artifact_bridge.py", "ArtifactBridge", "record_side_effect_result"),
    ("src/artifact_store.py", "ArtifactStore", "append_context"),
    ("src/artifact_store.py", "ArtifactStore", "put"),
    ("src/artifact_store.py", "ArtifactStore", "_ensure_append_index"),
    ("src/artifact_store.py", "ArtifactStore", "_head_cache_matches"),
    ("src/artifact_store.py", "ArtifactStore", "_refresh_append_head_cache"),
    ("src/artifact_bridge.py", "ArtifactBridge", "start_provider_attempt"),
    ("src/artifact_bridge.py", "ArtifactBridge", "finish_provider_attempt"),
    ("src/artifact_bridge.py", "ArtifactBridge", "side_effect_result"),
    ("src/final_review_preflight.py", None, "run_final_review_preflight"),
    ("src/final_review_preflight.py", None, "_approved_external_paths"),
    ("src/workflow_audit_projection.py", None, "_persisted_histories"),
    ("src/workflow_audit_projection.py", None, "_attach_record_events"),
    ("src/orchestrator.py", None, "_load_bound_queue_terminal"),
    ("src/orchestrator.py", None, "run_pipeline"),
    ("src/workflow_run_setup.py", None, "_apply_resumed_agent_profiles"),
    ("src/workflow_run_setup.py", None, "_branch_discovery_family_binding"),
    ("src/workflow_run_setup.py", None, "_initialize_finding_handoff"),
    ("src/artifact_resume.py", None, "_validate_finding_handoff"),
    ("src/workflow_production.py", None, "_read_production_task"),
    ("src/workflow_production.py", None, "_prepare_new_watch_task"),
    ("src/workflow_production.py", None, "_validate_resumed_state"),
    ("src/workflow_production.py", None, "_create_production_state"),
    ("src/workflow_production.py", None, "_recover_final_review_history"),
    ("src/workflow_production.py", None, "run_production_workflow"),
    ("src/workflow_production.py", None, "_run_production_transition_loop"),
    (
        "src/workflow_baseline.py",
        None,
        "matches_baseline_initialization_prefix",
    ),
    (
        "src/workflow_baseline.py",
        "WorkflowBaseline",
        "_persist_structured_baseline",
    ),
    ("src/orchestrator.py", "ProductionWorkflowDriver", "assert_structured_decision_context"),
    ("src/workflow_recovery.py", "WorkflowRecovery", "_start_provider_attempt"),
    ("src/orchestrator.py", "ProductionWorkflowDriver", "_reconcile_provider_effect"),
    ("src/orchestrator.py", "ProductionWorkflowDriver", "_write_side_effect_file"),
    ("src/workflow_recovery.py", "WorkflowRecovery", "_reconcile_pending_side_effects"),
    ("src/orchestrator.py", "ProductionWorkflowDriver", "authoritative_native_findings"),
    ("src/orchestrator.py", "ProductionWorkflowDriver", "carry_forward_native_findings"),
    (
        "src/workflow_persistence.py",
        "WorkflowPersistence",
        "_persist_native_agent_request_bundle",
    ),
    ("src/orchestrator.py", "ProductionWorkflowDriver", "_write_immutable_file"),
    ("src/orchestrator.py", "ProductionWorkflowDriver", "_write_native_codex_raw_response"),
    ("src/orchestrator.py", "ProductionWorkflowDriver", "_materialize_review_packet"),
    ("src/orchestrator.py", "ProductionWorkflowDriver", "_canonical_native_agent_result"),
    ("src/workflow_recovery.py", "WorkflowRecovery", "recover_pending_native_implementer"),
    ("src/workflow_recovery.py", "WorkflowRecovery", "recover_pending_native_reviewer"),
    ("src/workflow_recovery.py", "WorkflowRecovery", "recover_pending_native_reviewer_before_policy"),
    (
        "src/workflow_persistence.py",
        "WorkflowPersistence",
        "persist_native_implementer_contract",
    ),
    ("src/orchestrator.py", "ProductionWorkflowDriver", "prepare_finding_handoff"),
    ("src/orchestrator.py", "ProductionWorkflowDriver", "checkpoint"),
    ("src/workflow_audit.py", "WorkflowAudit", "project_audit"),
    ("src/workflow_audit.py", "WorkflowAudit", "finalize_audit"),
    ("src/workflow_git_commit.py", "WorkflowGitCommit", "_prepare_commit_context"),
    ("src/workflow_git_commit.py", "WorkflowGitCommit", "_prepare_git_operation"),
    ("src/workflow_git_commit.py", "WorkflowGitCommit", "_resolve_structured_binding"),
    ("src/workflow_git_commit.py", "WorkflowGitCommit", "commit_slice"),
    ("src/inbox_watcher.py", "QueueSuccessEvidence", "__post_init__"),
    ("src/inbox_watcher.py", None, "load_rejection_marker"),
    ("src/inbox_watcher.py", None, "load_watch_identity"),
    ("src/inbox_watcher.py", None, "load_queue_success_evidence"),
    ("src/inbox_watcher.py", None, "finalize_queue_success"),
    ("src/inbox_watcher.py", None, "move_to_outbox_with_ledger"),
    ("src/inbox_watcher.py", None, "move_poison_to_outbox_recoverably"),
    ("src/inbox_watcher.py", None, "_rename_stuck_task"),
    ("src/inbox_watcher.py", None, "_prepare_watch_invocation"),
    ("src/inbox_watcher.py", None, "_process_watch_task"),
    ("src/inbox_watcher.py", None, "_strengthen_rejected_result"),
    ("src/inbox_watcher.py", None, "_begin_rejected_archive"),
    ("src/inbox_watcher.py", None, "_finish_rejected_archive"),
    ("src/inbox_watcher.py", None, "_move_poison_task"),
    ("src/inbox_watcher.py", None, "_finish_poison_archive"),
    ("src/inbox_watcher.py", None, "_finalize_new_bound_success"),
    ("src/inbox_watcher.py", None, "_recover_bound_success"),
    ("src/inbox_watcher.py", None, "_archive_completed_task"),
    ("src/inbox_watcher.py", None, "_processing_crash_result"),
    ("src/inbox_watcher.py", None, "_log_resumable_pause"),
    ("src/inbox_watcher.py", None, "_log_technical_failure_result"),
    ("src/inbox_watcher.py", None, "_log_bound_completion"),
    ("src/inbox_watcher.py", None, "_bound_finalization_failure"),
    ("src/inbox_watcher.py", None, "_log_bound_recovery"),
    ("src/inbox_watcher.py", None, "_bound_recovery_failure"),
    ("src/inbox_watcher.py", None, "_log_bound_success_pause"),
    ("src/inbox_watcher.py", None, "_archive_failed_task"),
    ("src/inbox_watcher.py", None, "_log_failed_archive_error"),
    ("src/inbox_watcher.py", None, "_log_archive_retry"),
    ("src/inbox_watcher.py", None, "_log_watch_task_completion"),
    ("src/inbox_watcher.py", None, "watch_inbox"),
    ("src/workflow.py", "WorkflowHistory", "from_dict"),
    ("src/git_service.py", None, "commit_managed_audit_report"),
    ("src/git_service.py", None, "preview_commit_tree"),
    ("src/git_service.py", None, "inspect_commit_tree"),
    ("src/side_effects.py", "SideEffectExecutor", "execute"),
    ("src/side_effects.py", "SideEffectExecutor", "begin"),
    ("src/side_effects.py", None, "reconcile_file_write"),
    ("src/side_effects.py", None, "reconcile_provider_start"),
    ("src/side_effects.py", None, "reconcile_queue_move"),
    ("src/side_effects.py", None, "reconcile_git_commit"),
)
COMPARISON_HELPERS = {
    (
        "src/artifact_replay.py",
        None,
        "_validate_work_unit_revisions",
    ): ("_validate_work_unit_revision",),
    (
        "src/inbox_watcher.py",
        None,
        "watch_inbox",
    ): (
        "_handle_stuck_task",
        "_archive_rejected_watch_task",
        "_archive_poisoned_watch_task",
        "_handle_completed_archive_failure",
    ),
    (
        "src/workflow_baseline.py",
        None,
        "matches_baseline_initialization_prefix",
    ): (
        "_append_baseline_identity_expectations",
        "_append_baseline_transition_expectations",
        "_append_baseline_contract_expectations",
    ),
    (
        "src/workflow_baseline.py",
        "WorkflowBaseline",
        "_persist_structured_baseline",
    ): (
        "_replay_existing_baseline_chain",
        "_require_existing_baseline_prefix",
        "_append_baseline_identity_and_ledger",
        "_append_completed_internal_effects",
        "_append_baseline_state_facts",
    ),
    (
        "src/workflow_recovery.py",
        "WorkflowRecovery",
        "recover_pending_native_implementer",
    ): (
        "_bind_native_implementer_request",
        "_replay_native_implementer_request_findings",
        "_bind_native_implementer_request_findings",
        "_parse_native_implementer_recovery",
    ),
    (
        "src/workflow_recovery.py",
        "WorkflowRecovery",
        "recover_pending_native_reviewer_before_policy",
    ): (
        "_replay_pending_native_reviewer",
        "_build_pending_native_reviewer_context",
        "_parse_pending_native_reviewer_response",
    ),
}

STRICT_BODY_TARGETS = tuple(
    target
    for target in COMPARISON_TARGETS
    if (
        target[0] in {
            "src/artifact_resume.py",
            "src/final_review_preflight.py",
        }
        and target[2] != "resolve_resume_state"
    )
    or target[2] == "review_payload_matches_result"
    or target[2]
    in {
        "_prepare_commit_context",
        "_prepare_git_operation",
        "_resolve_structured_binding",
        "commit_slice",
    }
    or target[2]
    in {
        "_persisted_histories",
        "_attach_record_events",
        "finalize_audit",
        "commit_managed_audit_report",
    }
)

# Counts all explicit comparison expressions in functions which implement the
# matrix's comparison boundaries. This is deliberately conservative: even a
# non-divergence comparison added inside one of these boundaries forces S2's
# inventory to be reviewed instead of silently aging.
EXPECTED_COMPARISON_COUNTS = {
    "src/artifact_resume.py:resolve_resume_state": 4,
    "src/artifact_resume.py:require_workflow_status_prefix": 3,
    "src/artifact_resume.py:require_workflow_event_prefix": 6,
    "src/artifact_resume.py:require_gate_prefix": 1,
    "src/artifact_resume.py:require_side_effect_ledger_prefix": 4,
    "src/artifact_replay.py:_validate_workflow_transitions_and_events": 19,
    "src/artifact_replay.py:_validate_invocation_failures_and_retries": 18,
    "src/artifact_replay.py:_validate_gate_transitions_and_decisions": 12,
    "src/artifact_replay.py:_index_validation_content": 1,
    "src/artifact_replay.py:_validate_attestation_content_bindings": 14,
    "src/artifact_replay.py:_validate_unbound_validation_content": 2,
    "src/artifact_replay.py:_validate_provider_decision_content": 18,
    "src/artifact_replay.py:_validate_unbound_provider_content": 2,
    "src/artifact_replay.py:_validate_review_anchors": 5,
    "src/artifact_replay.py:_validate_review_validation_bindings": 8,
    "src/artifact_replay.py:_validate_required_review_authority": 18,
    "src/artifact_replay.py:_validate_review_packet_bindings": 2,
    "src/artifact_replay.py:_validate_work_unit_revisions": 6,
    "src/artifact_replay.py:_validate_single_finding_import": 1,
    "src/artifact_replay.py:_validate_finding_handoff_record": 11,
    "src/artifact_replay.py:_validate_branch_discovery_handoff_record": 0,
    "src/artifact_replay.py:_validate_branch_discovery_export": 24,
    "src/artifact_replay.py:_validate_branch_discovery_import": 14,
    "src/artifact_replay.py:_validate_work_unit_finding_import": 4,
    "src/artifact_replay.py:_validate_work_unit_activity_reference": 4,
    "src/artifact_replay.py:_validate_bound_record_references": 26,
    "src/artifact_replay.py:_validate_chain_record_references": 0,
    "src/artifact_replay.py:_validate_provider_attempt_sequences": 11,
    "src/artifact_replay.py:_validate_side_effect_sequences": 13,
    "src/artifact_replay.py:_validate_payload_references": 0,
    "src/artifact_bridge.py:finding_handoff_export_payload": 5,
    "src/artifact_bridge.py:finding_handoff_import_payload": 9,
    "src/artifact_bridge.py:_finding_snapshot": 0,
    "src/artifact_bridge.py:branch_discovery_handoff_export_payload": 16,
    "src/artifact_bridge.py:branch_discovery_handoff_import_payload": 13,
    "src/artifact_bridge.py:review_payload_matches_result": 13,
    "src/artifact_bridge.py:ArtifactBridge.append": 3,
    "src/artifact_bridge.py:ArtifactBridge.record_side_effect_intent": 1,
    "src/artifact_bridge.py:ArtifactBridge.record_side_effect_result": 4,
    "src/artifact_store.py:ArtifactStore.append_context": 2,
    "src/artifact_store.py:ArtifactStore.put": 14,
    "src/artifact_store.py:ArtifactStore._ensure_append_index": 3,
    "src/artifact_store.py:ArtifactStore._head_cache_matches": 3,
    "src/artifact_store.py:ArtifactStore._refresh_append_head_cache": 2,
    "src/artifact_bridge.py:ArtifactBridge.start_provider_attempt": 17,
    "src/artifact_bridge.py:ArtifactBridge.finish_provider_attempt": 10,
    "src/artifact_bridge.py:ArtifactBridge.side_effect_result": 7,
    "src/final_review_preflight.py:run_final_review_preflight": 14,
    "src/final_review_preflight.py:_approved_external_paths": 15,
    "src/workflow_audit_projection.py:_persisted_histories": 6,
    "src/workflow_audit_projection.py:_attach_record_events": 13,
    "src/orchestrator.py:_load_bound_queue_terminal": 4,
    "src/orchestrator.py:run_pipeline": 16,
    "src/workflow_run_setup.py:_apply_resumed_agent_profiles": 3,
    "src/workflow_run_setup.py:_branch_discovery_family_binding": 10,
    "src/workflow_run_setup.py:_initialize_finding_handoff": 9,
    "src/artifact_resume.py:_validate_finding_handoff": 6,
    "src/workflow_production.py:_read_production_task": 2,
    "src/workflow_production.py:_prepare_new_watch_task": 1,
    "src/workflow_production.py:_validate_resumed_state": 10,
    "src/workflow_production.py:_create_production_state": 0,
    "src/workflow_production.py:_recover_final_review_history": 2,
    "src/workflow_production.py:run_production_workflow": 4,
    "src/workflow_production.py:_run_production_transition_loop": 21,
    "src/workflow_baseline.py:matches_baseline_initialization_prefix": 22,
    "src/workflow_baseline.py:WorkflowBaseline._persist_structured_baseline": 24,
    "src/orchestrator.py:ProductionWorkflowDriver.assert_structured_decision_context": 5,
    "src/workflow_recovery.py:WorkflowRecovery._start_provider_attempt": 19,
    "src/orchestrator.py:ProductionWorkflowDriver._reconcile_provider_effect": 13,
    "src/orchestrator.py:ProductionWorkflowDriver._write_side_effect_file": 3,
    "src/workflow_recovery.py:WorkflowRecovery._reconcile_pending_side_effects": 34,
    "src/orchestrator.py:ProductionWorkflowDriver.authoritative_native_findings": 7,
    "src/orchestrator.py:ProductionWorkflowDriver.carry_forward_native_findings": 4,
    "src/workflow_persistence.py:WorkflowPersistence._persist_native_agent_request_bundle": 4,
    "src/orchestrator.py:ProductionWorkflowDriver._write_immutable_file": 3,
    "src/orchestrator.py:ProductionWorkflowDriver._write_native_codex_raw_response": 0,
    "src/orchestrator.py:ProductionWorkflowDriver._materialize_review_packet": 3,
    "src/orchestrator.py:ProductionWorkflowDriver._canonical_native_agent_result": 4,
    "src/workflow_recovery.py:WorkflowRecovery.recover_pending_native_implementer": 39,
    "src/workflow_recovery.py:WorkflowRecovery.recover_pending_native_reviewer": 30,
    "src/workflow_recovery.py:WorkflowRecovery.recover_pending_native_reviewer_before_policy": 33,
    "src/workflow_persistence.py:WorkflowPersistence.persist_native_implementer_contract": 9,
    "src/orchestrator.py:ProductionWorkflowDriver.prepare_finding_handoff": 13,
    "src/orchestrator.py:ProductionWorkflowDriver.checkpoint": 9,
    "src/workflow_audit.py:WorkflowAudit.project_audit": 23,
    "src/workflow_audit.py:WorkflowAudit.finalize_audit": 9,
    "src/workflow_git_commit.py:WorkflowGitCommit._prepare_commit_context": 9,
    "src/workflow_git_commit.py:WorkflowGitCommit._prepare_git_operation": 17,
    "src/workflow_git_commit.py:WorkflowGitCommit._resolve_structured_binding": 11,
    "src/workflow_git_commit.py:WorkflowGitCommit.commit_slice": 10,
    "src/inbox_watcher.py:QueueSuccessEvidence.__post_init__": 5,
    "src/inbox_watcher.py:load_rejection_marker": 9,
    "src/inbox_watcher.py:load_watch_identity": 4,
    "src/inbox_watcher.py:load_queue_success_evidence": 11,
    "src/inbox_watcher.py:finalize_queue_success": 8,
    "src/inbox_watcher.py:move_to_outbox_with_ledger": 11,
    "src/inbox_watcher.py:move_poison_to_outbox_recoverably": 3,
    "src/inbox_watcher.py:_rename_stuck_task": 0,
    "src/inbox_watcher.py:_prepare_watch_invocation": 0,
    "src/inbox_watcher.py:_process_watch_task": 12,
    "src/inbox_watcher.py:_strengthen_rejected_result": 1,
    "src/inbox_watcher.py:_begin_rejected_archive": 0,
    "src/inbox_watcher.py:_finish_rejected_archive": 1,
    "src/inbox_watcher.py:_move_poison_task": 1,
    "src/inbox_watcher.py:_finish_poison_archive": 1,
    "src/inbox_watcher.py:_finalize_new_bound_success": 0,
    "src/inbox_watcher.py:_recover_bound_success": 0,
    "src/inbox_watcher.py:_archive_completed_task": 1,
    "src/inbox_watcher.py:_processing_crash_result": 0,
    "src/inbox_watcher.py:_log_resumable_pause": 0,
    "src/inbox_watcher.py:_log_technical_failure_result": 0,
    "src/inbox_watcher.py:_log_bound_completion": 0,
    "src/inbox_watcher.py:_bound_finalization_failure": 0,
    "src/inbox_watcher.py:_log_bound_recovery": 0,
    "src/inbox_watcher.py:_bound_recovery_failure": 0,
    "src/inbox_watcher.py:_log_bound_success_pause": 0,
    "src/inbox_watcher.py:_archive_failed_task": 0,
    "src/inbox_watcher.py:_log_failed_archive_error": 0,
    "src/inbox_watcher.py:_log_archive_retry": 0,
    "src/inbox_watcher.py:_log_watch_task_completion": 1,
    "src/inbox_watcher.py:watch_inbox": 25,
    "src/workflow.py:WorkflowHistory.from_dict": 6,
    "src/git_service.py:commit_managed_audit_report": 5,
    "src/git_service.py:preview_commit_tree": 9,
    "src/git_service.py:inspect_commit_tree": 1,
    "src/side_effects.py:SideEffectExecutor.execute": 4,
    "src/side_effects.py:SideEffectExecutor.begin": 4,
    "src/side_effects.py:reconcile_file_write": 6,
    "src/side_effects.py:reconcile_provider_start": 2,
    "src/side_effects.py:reconcile_queue_move": 2,
    "src/side_effects.py:reconcile_git_commit": 3,
}

EXPECTED_STRICT_BODY_DIGESTS = {
    "src/artifact_bridge.py:review_payload_matches_result": "4ad048b9ff2fdd56f813abe6f8b8b72114f3fc0a3d59426185d74031e7b65506",
    "src/artifact_resume.py:_validate_finding_handoff": "40f91b2e0127b7fc16403a8b79bc12462b3a3dfa1cbecef1fe9a65b73f029e1f",
    "src/artifact_resume.py:require_workflow_status_prefix": "964d356480288034c6dc52de377c2326c06d2db50d6aae52fd2b3d5dbcc5bdec",
    "src/artifact_resume.py:require_workflow_event_prefix": "daa17cb027ef984f4264dda460c5987b76cf311ba22290e2a1857e4158f90537",
    "src/artifact_resume.py:require_gate_prefix": "67196c4e07c9c428033a8bf93726a93adc22a66929cbf975019913bf60979b82",
    "src/artifact_resume.py:require_side_effect_ledger_prefix": "7803832a9e825309cbbecf6b49d54d9dad15f2eeb0a973d803d90ae324acb7fd",
    "src/final_review_preflight.py:run_final_review_preflight": "3210bc1214206467e0c28f7c7650a625c1bae06d08ae65aaa3aeda8fc4274775",
    "src/final_review_preflight.py:_approved_external_paths": "3044e90229861522d4a84b72083cf55e262b5cdca8f6b1620fce8cd3031ba200",
    "src/workflow_audit_projection.py:_persisted_histories": "46d16ba2f5f168dbb9f86da548b7c370305003fa27f39d3979423f76f53b8d86",
    "src/workflow_audit_projection.py:_attach_record_events": "92515ba921f04792a4e8a91c9a6015ab922cd738d33c051e2ab83ad7124076a6",
    "src/workflow_audit.py:WorkflowAudit.finalize_audit": "60ecf8b15bc913fec75e34aa8006d0e65110218afea19ac34956d01e93c8b593",
    "src/workflow_git_commit.py:WorkflowGitCommit._prepare_commit_context": "4206352e640b15e4d4b11a03b8abf1834b6ed7e8338cc139829d2a760d27b16c",
    "src/workflow_git_commit.py:WorkflowGitCommit._prepare_git_operation": "b8623a4c7006d06638f9f703c489494c770ba9ec4f75ad76fc56672538b81bb5",
    "src/workflow_git_commit.py:WorkflowGitCommit._resolve_structured_binding": "7c7ab0e20733005877e51a1a4f86e847456fc6fe5e230c0b57f601bbb036f833",
    "src/workflow_git_commit.py:WorkflowGitCommit.commit_slice": "530fcd5f1229e0e46d35d8e0ca8a665898468ffb2c555e42bdfcf9a2f4a28b59",
    "src/git_service.py:commit_managed_audit_report": "ec161c2eafd7369d9eb9ab30b1724ca01815f08ce669e94c4b322770556bc717",
}


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _tree(relative_path: str) -> ast.Module:
    return ast.parse(_source(relative_path), filename=relative_path)


def _string_constants(relative_path: str) -> str:
    return "\n".join(
        node.value
        for node in ast.walk(_tree(relative_path))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


def _raise_count(relative_path: str, exception_name: str) -> int:
    return sum(
        1
        for node in ast.walk(_tree(relative_path))
        if isinstance(node, ast.Raise)
        and isinstance(node.exc, ast.Call)
        and (
            isinstance(node.exc.func, ast.Name)
            and node.exc.func.id == exception_name
        )
    )


def _function_names(relative_path: str, prefix: str) -> set[str]:
    return {
        node.name
        for node in ast.walk(_tree(relative_path))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith(prefix)
    }


def _class_node(tree: ast.Module, name: str) -> ast.ClassDef:
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == name
    )


def _function_node(relative_path: str, class_name: str | None, name: str) -> ast.AST:
    tree = _tree(relative_path)
    candidates = tree.body if class_name is None else _class_node(tree, class_name).body
    return next(
        node
        for node in candidates
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )


def _comparison_inventory() -> dict[str, int]:
    result: dict[str, int] = {}
    for relative_path, class_name, function_name in COMPARISON_TARGETS:
        nodes = [
            _function_node(relative_path, class_name, name)
            for name in (
                function_name,
                *COMPARISON_HELPERS.get(
                    (relative_path, class_name, function_name),
                    (),
                ),
            )
        ]
        label = ".".join(part for part in (class_name, function_name) if part)
        result[f"{relative_path}:{label}"] = sum(
            isinstance(item, ast.Compare)
            for node in nodes
            for item in ast.walk(node)
        )
    return result


def _body_digest_inventory() -> dict[str, str]:
    result: dict[str, str] = {}
    for relative_path, class_name, function_name in STRICT_BODY_TARGETS:
        node = _function_node(relative_path, class_name, function_name)
        label = ".".join(part for part in (class_name, function_name) if part)
        try:
            canonical = ast.dump(
                node,
                annotate_fields=True,
                include_attributes=False,
                show_empty=True,
            )
        except TypeError:  # Python 3.12 has no show_empty parameter and includes empty fields.
            canonical = ast.dump(node, annotate_fields=True, include_attributes=False)
        result[f"{relative_path}:{label}"] = hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()
    return result


def _class_field_names(relative_path: str, class_name: str) -> set[str]:
    node = _class_node(_tree(relative_path), class_name)
    return {
        item.target.id
        for item in node.body
        if isinstance(item, ast.AnnAssign)
        and isinstance(item.target, ast.Name)
        and not (
            isinstance(item.annotation, ast.Subscript)
            and isinstance(item.annotation.value, ast.Name)
            and item.annotation.value.id == "ClassVar"
        )
    }


def _driver_divergence_messages() -> Counter[str]:
    boundaries = (
        _class_node(_tree("src/orchestrator.py"), "ProductionWorkflowDriver"),
        _class_node(_tree("src/workflow_baseline.py"), "WorkflowBaseline"),
        _class_node(_tree("src/workflow_persistence.py"), "WorkflowPersistence"),
        _class_node(_tree("src/workflow_recovery.py"), "WorkflowRecovery"),
        _class_node(_tree("src/workflow_validation.py"), "WorkflowValidation"),
        _class_node(_tree("src/workflow_audit.py"), "WorkflowAudit"),
        _class_node(_tree("src/workflow_git_commit.py"), "WorkflowGitCommit"),
    )
    values = (
        node.value
        for boundary in boundaries
        for node in ast.walk(boundary)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )
    return Counter(
        value
        for value in values
        if any(marker in value.lower() for marker in ("differ", "diverg", "mismatch"))
    )


def _preflight_denial_codes() -> tuple[set[str], int]:
    node = _function_node(
        "src/final_review_preflight.py", None, "run_final_review_preflight"
    )
    calls = [
        item
        for item in ast.walk(node)
        if isinstance(item, ast.Call)
        and isinstance(item.func, ast.Name)
        and item.func.id == "_deny"
    ]
    return (
        {
            item.args[1].value
            for item in calls
            if len(item.args) > 1
            and isinstance(item.args[1], ast.Constant)
            and isinstance(item.args[1].value, str)
        },
        len(calls),
    )


def test_every_matrix_edge_has_all_fields_and_exactly_one_classification() -> None:
    document = MATRIX_PATH.read_text(encoding="utf-8")
    sections = {
        match.group(1): match.group(2)
        for match in re.finditer(
            r"(?ms)^### ([AB][0-9]{2}) — .*?\n(.*?)(?=^### |^## |\Z)",
            document,
        )
    }
    expected_ids = {
        *(f"A{index:02d}" for index in range(1, 17)),
        *(f"B{index:02d}" for index in range(1, 11)),
    }
    assert set(sections) == expected_ids
    required_fields = (
        "1. **Autoritativer Eingaberecord:",
        "2. **State-/Cachefelder:",
        "3. **Schreibreihenfolge und Crashpunkte:",
        "4. **Idempotenz:",
        "5. **Recoverable-Sonderfall:",
        "6. **Semantik-/Protokollversion:",
        "7. **Externe Side Effects:",
    )
    for edge_id, section in sections.items():
        assert all(field in section for field in required_fields), edge_id
        classifications = re.findall(
            r"^Bewertung: \*\*(entfällt|wird generisch|bleibt bewusst)\*\*",
            section,
            flags=re.MULTILINE,
        )
        assert len(classifications) == 1, (edge_id, classifications)


def test_migration_comparison_inventory_is_source_bound() -> None:
    source = _source("src/artifact_resume.py")
    source_strings = _string_constants("src/artifact_resume.py")
    document = MATRIX_PATH.read_text(encoding="utf-8")
    assert _raise_count("src/artifact_resume.py", "mismatch") == 0
    assert source.count("differs from state-v3") == 0
    assert source.count("_recoverable_") == 0
    assert _raise_count("src/artifact_resume.py", "ArtifactResumeError") > 0
    for marker in (
        "has no records; restore its record directory before resuming",
        "structured-v2 record chain for run",
        "finding handoff source is no longer valid",
    ):
        assert marker in source_strings
    for marker in (
        "`_recoverable_*`: **5 → 0**",
        "`differs from state-v3`: **20 → 0**",
        "`mismatch(...)` in `artifact_resume.py`: **44 → 0**",
    ):
        assert marker in document
    assert RUN_BINDING_REPLAY_MARKER in _string_constants("src/artifact_replay.py")
    assert RUN_BINDING_REPLAY_MARKER in document


def test_structured_decision_paths_do_not_read_the_state_cache() -> None:
    resolver = _function_node(
        "src/artifact_resume.py", None, "resolve_resume_state"
    )
    locator_attributes = {
        node.attr
        for node in ast.walk(resolver)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "state_or_run_id"
    }
    assert locator_attributes == {"run_id", "strip"}

    orchestrator_source = _source("src/orchestrator.py")
    production_source = _source("src/workflow_production.py")
    assert orchestrator_source.count("load_workflow_state(") == 0
    assert production_source.count("load_workflow_state(") == 1
    replacement = _function_node(
        "src/workflow_production.py", None, "run_production_workflow"
    )
    replacement_dump = ast.dump(replacement, include_attributes=False)
    assert "replacement_requested" in replacement_dump
    assert "load_resumable_workflow_state" in replacement_dump

    decision_guard = _function_node(
        "src/orchestrator.py",
        "ProductionWorkflowDriver",
        "assert_structured_decision_context",
    )
    guard_dump = ast.dump(decision_guard, include_attributes=False)
    assert "resolve_resume_state" in guard_dump
    assert "load_workflow_state" not in guard_dump


def test_bridge_error_inventory_is_source_bound() -> None:
    paths = (
        "src/artifact_resume.py",
        "src/artifact_bridge.py",
        "src/orchestrator.py",
        "src/workflow_production.py",
        "src/workflow_run_setup.py",
    )
    combined_source = "\n".join(_string_constants(path) for path in paths)
    document = MATRIX_PATH.read_text(encoding="utf-8")
    assert sum(_raise_count(path, "ArtifactBridgeError") for path in paths) == 92
    for marker in BRIDGE_ERROR_MARKERS:
        assert marker in combined_source
        assert marker in document


def test_recoverable_function_inventory_is_source_bound() -> None:
    document = MATRIX_PATH.read_text(encoding="utf-8")
    actual: dict[str, set[str]] = {}
    for path in sorted((ROOT / "src").rglob("*.py")):
        relative_path = path.relative_to(ROOT).as_posix()
        names = _function_names(relative_path, "_recoverable_")
        if names:
            actual[relative_path] = names
    assert actual == RECOVERABLE_FUNCTIONS
    for names in actual.values():
        for name in names:
            assert name in document


def test_runtime_divergence_inventory_is_source_bound() -> None:
    actual = _driver_divergence_messages()
    assert actual == DRIVER_DIVERGENCE_MESSAGES
    document = MATRIX_PATH.read_text(encoding="utf-8")
    for message in actual:
        assert message.rstrip(": ") in document


def test_watch_cache_divergence_inventory_is_source_bound() -> None:
    actual = Counter(
        value
        for value in _string_constants("src/inbox_watcher.py").splitlines()
        if any(marker in value.lower() for marker in ("differ", "diverg", "mismatch"))
    )
    assert actual == WATCH_DIVERGENCE_MESSAGES
    document = MATRIX_PATH.read_text(encoding="utf-8")
    for message in actual:
        assert message in document


def test_preflight_denial_inventory_is_source_and_document_bound() -> None:
    codes, call_count = _preflight_denial_codes()
    assert call_count == 13
    assert codes == PREFLIGHT_DENIAL_CODES
    document = MATRIX_PATH.read_text(encoding="utf-8")
    for code in codes:
        assert code in document


def test_additional_cache_and_terminal_boundaries_are_document_bound() -> None:
    document = MATRIX_PATH.read_text(encoding="utf-8")
    for relative_path, markers in ADDITIONAL_BOUNDARY_MARKERS.items():
        source_strings = _string_constants(relative_path)
        for marker in markers:
            assert marker in source_strings
            assert marker in document


def test_recordless_review_and_attestation_fields_are_source_bound() -> None:
    expected_fields = {
        ("src/contracts.py", "ValidationAttestation"): {
            "attestation_id",
            "diff_fingerprint",
            "expected_commands",
            "records",
            "output_digest",
            "summary",
            "command_specs",
            "content_captures",
            "content_digest_format",
        },
        ("src/artifact_models.py", "ValidationAttestationPayload"): {
            "results",
            "attested_by",
            "output_digest",
            "content_record_id",
        },
            ("src/contracts.py", "ContractResult"): {
            "reviewer",
            "approval",
            "stopped",
            "stop_request",
            "validation",
            "test_files",
            "pre_mortem",
            "evidence",
            "findings",
            "anchors",
                "red_state_followup_slice",
                    "delivery_kind",
                    "occurrences",
                    "scan_complete",
                    "plan_treatment_decisions",
                    "finding_closures",
        },
        ("src/artifact_models.py", "ReviewPayload"): {
            "reviewer",
            "work_unit_id",
            "verdict",
            "finding_ids",
            "evidence",
            "transport_schema",
            "request_id",
            "response_sha256",
            "review_evidence",
            "red_state_followup_slice",
            "test_files",
                "pre_mortem",
                "stop_request",
                "plan_treatment_decisions",
        },
        ("src/artifact_models.py", "ReviewEvidencePayload"): {
            "dimensions",
            "largest_residual_risk",
            "break_condition",
        },
        ("src/artifact_models.py", "SideEffectPayload"): {
            "effect_key",
            "effect_class",
            "work_unit_id",
            "operation",
            "phase",
            "result",
        },
        ("src/artifact_models.py", "WorkflowEventPayload"): {
            "event_kind",
            "work_unit_id",
            "slice_id",
            "round_number",
            "record_refs",
        },
    }
    actual = {
        key: _class_field_names(*key)
        for key in expected_fields
    }
    assert actual == expected_fields
    document = MATRIX_PATH.read_text(encoding="utf-8")
    for marker in (
        "ValidationAttestation.attestation_id",
        "ValidationAttestation.expected_commands",
        "ValidationAttestation.output_digest",
        "ValidationAttestation.summary",
        "ValidationRecord.output",
        "ContractResult.red_state_followup_slice",
        "ContractResult.test_files",
        "ContractResult.pre_mortem",
        "ContractResult.anchors",
        "ContractResult.stop_request",
        "ContractResult.validation",
        "ReviewPayload.review_evidence",
    ):
        assert marker in document


def test_every_s4a_stop_entry_has_exactly_one_reasoned_classification() -> None:
    document = MATRIX_PATH.read_text(encoding="utf-8")
    inventory = document.split("## State-Fakten ohne vollständigen Record", 1)[1].split(
        "### S4a-Sortierung der STOP-Einträge", 1
    )[0]
    classification = document.split(
        "### S4a-Sortierung der STOP-Einträge", 1
    )[1].split(
        "#### Entscheidung zu Schema 2 und Bestandsrecords",  # allowlist:german
        1,
    )[0]
    stop_fields = {
        line.split("|", 2)[1].strip()
        for line in inventory.splitlines()
        if line.startswith("|")
        and "| **STOP**" in line
    }
    rows = tuple(
        (match.group(1).strip(), match.group(2), match.group(3).strip())
        for match in re.finditer(
            r"^\|\s*(.+?)\s*\|\s*([ABC])\s*\|\s*(.+?)\s*\|$",
            classification,
            re.MULTILINE,
        )
    )
    fields = tuple(field for field, _group, _reason in rows)
    groups = {field: group for field, group, _reason in rows}

    r1_covered_fields = {
        "`task_file`",
        "`branch`",
        "`branch_base`",
        "`execution_mode`",
        "`audit_report_path`",
        "`protocol_binding.codex_profile/claude_profile`",
    }
    r2_covered_fields = {
        "`current_slice_id`, `current_work_unit_id`, `current_step`",
        "`slices[*].status`",
        "`work_units[*].status`",
        "`work_units[*].current_step`",
        "`work_units[*].codex_return_count`",
        "`work_units[*].max_codex_returns`",
        "`work_units[*].reviewer`",
    }
    r3_covered_fields = {
        "`slices[*].start_commit`",
        "`slices[*].scope_change_groups`",
        "`slices[*].start_fingerprint`",
    }
    r4_covered_fields = {"`work_units[*].completed_side_effects`"}
    r5_covered_fields = {
        "`work_units[*].active_test_fingerprint`, `active_test_paths`",
        "aktueller `gate.status/reason/detail/fingerprint/paths/resume_step`",
        "`gate_decisions[*].paths` und `resume_step`",
        "`gate_decisions[*].decided_by`",
        "`gate_decisions[*].decided_at`",
    }
    r6_covered_fields = {
        "`invocation_failures[*].invocation_id`, `idempotency_key`",
        "`invocation_failures[*].provider_text/received_at/step/slice_id/work_unit_id/diagnostic_exit_code`",
        "`invocation_failures[*].parse_path/source_timezone/reset_at_utc/safety_margin_seconds`",
        "`invocation_failures[*].auto_resume_count/automatic_resume/diff_fingerprint`",
    }
    r8_covered_fields = {
        "`ValidationRecord.output`",
        "`ValidationAttestation.output_digest`",
        "`runtime_history.codex_final_report` und weitere rohe Agenttexte",
        "`runtime_history.active_review_packet`",
    }
    r7_covered_fields = {
        "`ContractResult.red_state_followup_slice` in `runtime_history.reviews/latest_claude_review`",
        "`ContractResult.test_files`",
        "`ContractResult.pre_mortem`",
        "`ContractResult.anchors`",
        "`ContractResult.stop_request`",
        "`ContractResult.validation`",
        "`runtime_history.latest_claude_review` als Aggregat",
    }
    r9_covered_fields = {"sonstige `runtime_history`-Event-/Auditfelder"}
    closed_stop_fields = {
        "`protocol_binding.mode/schema/transports`",
        "`ContractResult.evidence.dimensions/largest_residual_risk/break_condition`",
        *r9_covered_fields,
    }
    assert len(stop_fields) == 0
    assert len(fields) == len(set(fields)) == 41
    assert set(fields) == (
        stop_fields
        | r1_covered_fields
        | r2_covered_fields
        | r3_covered_fields
        | r4_covered_fields
        | r5_covered_fields
        | r6_covered_fields
        | r7_covered_fields
        | r8_covered_fields
        | closed_stop_fields
        | {"`created_at`, `updated_at`"}
    )
    assert groups["`work_units[*].codex_return_count`"] == "A"
    assert groups["`ContractResult.test_files`"] == "A"
    assert groups[
        "`ContractResult.red_state_followup_slice` in "
        "`runtime_history.reviews/latest_claude_review`"
    ] == "A"
    assert groups[
        "`ContractResult.evidence.dimensions/largest_residual_risk/break_condition`"
    ] == "A"
    for field in r1_covered_fields:
        reason = next(reason for name, _group, reason in rows if name == field)
        assert "In R1 geschlossen" in reason
        assert "RunIdentityPayload" in reason or "RunProfilePayload" in reason
    for field in r2_covered_fields:
        reason = next(reason for name, _group, reason in rows if name == field)
        assert "In R2 geschlossen" in reason
    for field in r4_covered_fields:
        reason = next(reason for name, _group, reason in rows if name == field)
        assert "In R4 geschlossen" in reason
    for field in r5_covered_fields:
        reason = next(reason for name, _group, reason in rows if name == field)
        assert "In R5" in reason
    for field in r6_covered_fields:
        reason = next(reason for name, _group, reason in rows if name == field)
        assert "In R6 geschlossen" in reason
        assert "InvocationFailurePayload" in reason or "Gleichnamige Felder" in reason
    for field in r8_covered_fields:
        reason = next(reason for name, _group, reason in rows if name == field)
        assert "In R8 geschlossen" in reason
    for field in r7_covered_fields:
        reason = next(reason for name, _group, reason in rows if name == field)
        assert "R7" in reason
    for field in r9_covered_fields:
        reason = next(reason for name, _group, reason in rows if name == field)
        assert "In R9 geschlossen" in reason
        assert "WorkflowEventPayload" in reason
    for field in r3_covered_fields:
        reason = next(reason for name, _group, reason in rows if name == field)
        assert "In R3 geschlossen" in reason
        assert "SliceBoundaryPayload" in reason
    policy_reason = next(
        reason
        for name, _group, reason in rows
        if name == "`work_units[*].codex_return_count`"
    )
    assert "implementer_return_count" in policy_reason
    assert "WorkflowPolicyPayload.codex_return_count" not in policy_reason
    assert groups["`work_units[*].reviewer`"] == "C"
    for field, group, reason in rows:
        if group == "A":
            assert "Payload" in reason, field
            assert "Schreiber" in reason or "Schreiber sind" in reason, field
        elif group == "B":
            assert "Leser" in reason, field
        else:
            assert any(
                marker in reason
                for marker in (
                    "ReviewPayload",
                    "AgentResultPayload",
                    "ArtifactRecord.schema_version",
                )
            ), field

    assert "32 persistierte Review-Records" in document
    assert "ar1-c24d40d2bcf557f302bf3b64414ea5142bd8e11f3814315e1013547ab73bbfe3" in document
    assert "niemals heuristisch geteilt" in document


def test_state_schema_field_inventory_is_source_bound() -> None:
    actual = {
        class_name: _class_field_names("src/workflow_state.py", class_name)
        for class_name in WORKFLOW_STATE_FIELD_INVENTORY
    }
    assert actual == WORKFLOW_STATE_FIELD_INVENTORY


def test_attestation_identity_uses_the_record_envelope() -> None:
    node = _function_node(
        "src/workflow_persistence.py",
        "WorkflowPersistence",
        "persist_validation_attestation",
    )
    canonical = ast.dump(node, annotate_fields=True, include_attributes=False)
    assert "logical_id" in canonical
    assert canonical.count("attestation_id") >= 2
    document = MATRIX_PATH.read_text(encoding="utf-8")
    assert "ArtifactRecord.logical_id" in document


def test_managed_audit_commit_boundary_is_source_and_document_bound() -> None:
    finalize = ast.dump(
        _function_node("src/workflow_audit.py", "WorkflowAudit", "finalize_audit"),
        include_attributes=False,
    )
    workflow = "\n".join(
        ast.dump(
            _function_node("src/workflow_production.py", None, function_name),
            include_attributes=False,
        )
        for function_name in (
            "run_production_workflow",
            "_run_production_transition_loop",
        )
    )
    commit = ast.dump(
        _function_node("src/git_service.py", None, "commit_managed_audit_report"),
        include_attributes=False,
    )
    assert "commit_managed_audit_report" in finalize
    assert "finalize_audit" in workflow
    assert "docs: finalize orchestrator audit" in commit
    document = MATRIX_PATH.read_text(encoding="utf-8")
    for marker in (
        "commit_managed_audit_report()",
        "docs: finalize orchestrator audit",
    ):
        assert marker in document


def test_comparison_expression_inventory_has_not_grown() -> None:
    actual = _comparison_inventory()
    assert actual == EXPECTED_COMPARISON_COUNTS
    assert sum(
        count
        for label, count in actual.items()
        if label.startswith("src/artifact_replay.py:")
    ) == 233
    document = MATRIX_PATH.read_text(encoding="utf-8")
    assert (
        "`WorkflowPersistence.persist_native_implementer_contract()` 11 "
        "inventarisierte\nVergleichsausdrücke"
    ) in document
    assert "`persist_native_codex_contract()` 11" not in document


def test_recovery_and_preflight_predicate_bodies_are_frozen() -> None:
    assert _body_digest_inventory() == EXPECTED_STRICT_BODY_DIGESTS
