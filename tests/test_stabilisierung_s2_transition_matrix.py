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

BRIDGE_ERROR_MARKERS = (
    "referenced source record is not a finding export",
    "source export plan commit differs from state-v3",
    "finding export requires a non-empty accepted replay",
    "finding export requires its approved reviewer record",
    "finding export plan commit is not present in accepted replay",
    "finding export requires at least one source transition",
    "finding import requires a finding handoff export record",
    "finding export record is not in the accepted source replay",
    "finding export record belongs to another source run",
    "finding export differs from its accepted source replay",
    "finding import task bytes differ from the export binding",
    "structured artifact differs semantically from the state-v3 statement",
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
)

RECOVERABLE_FUNCTIONS = {
    "src/artifact_migration.py": {
        "_recoverable_pending_review_finding_gap",
        "_recoverable_pending_correction_record",
        "_recoverable_pending_slice_denial_record",
        "_recoverable_pending_work_record",
    },
    "src/orchestrator.py": {"_recoverable_final_denial_mirror_gap"},
}

DRIVER_DIVERGENCE_MESSAGES = Counter(
    {
        "structured reviewer decisions differ from the state-v3 mirror": 1,
        "structured commit review record differs from its state-v3 mirror": 1,
        "structured commit attestation record differs from its state-v3 mirror": 1,
        "provider attempt measurement context diverged": 1,
        "authoritative finding replay differs from the state-v3 mirror": 1,
        "record-native finding carry-forward differs from the state-v3 mirror": 1,
        "native agent request differs from its persisted recovery artifact": 2,
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
        "native agent result logical binding differs": 1,
        "native agent content digest differs from its result binding": 1,
        "native reviewer content digest differs from its review binding": 1,
        "native review persistence differs from its exact review context": 1,
        "validation recovery result differs from its content": 1,
        "persisted finding handoff export differs from the prepared task": 1,
        "file side-effect target differs before result completion": 1,
        "projection target differs before result completion": 1,
        "invocation failure work unit differs from the active workflow": 1,
        "structured audit dual-write mismatch: ": 2,
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
    "SLICE-BINDING-MISSING",
    "CODEX-FINAL-RESULT-MISSING",
}

ADDITIONAL_BOUNDARY_MARKERS = {
    "src/orchestrator.py": {
        "bound success evidence differs from terminal workflow state",
        "Terminal workflow result differs from bound watch task identity",
        "differs from the immutable persisted profile",
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
        "parse_path",
        "source_timezone",
        "reset_at_utc",
        "resume_at_utc",
        "safety_margin_seconds",
        "auto_resume_count",
        "automatic_resume",
        "diff_fingerprint",
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
    },
}

COMPARISON_TARGETS = (
    ("src/artifact_migration.py", None, "resolve_resume_state"),
    ("src/artifact_migration.py", None, "assert_run_binding_mirror"),
    ("src/artifact_migration.py", None, "require_workflow_status_prefix"),
    ("src/artifact_migration.py", None, "require_workflow_event_prefix"),
    ("src/artifact_migration.py", None, "assert_workflow_status_mirror"),
    ("src/artifact_migration.py", None, "assert_slice_boundary_mirror"),
    ("src/artifact_migration.py", None, "require_gate_prefix"),
    ("src/artifact_migration.py", None, "assert_gate_mirror"),
    ("src/artifact_migration.py", None, "require_side_effect_ledger_prefix"),
    ("src/artifact_migration.py", None, "assert_invocation_failure_mirror"),
    ("src/artifact_migration.py", None, "project_transition_mirror_before_failure"),
    ("src/artifact_migration.py", None, "assert_side_effect_mirror"),
    ("src/artifact_migration.py", None, "_mirror_difference_code"),
    ("src/artifact_migration.py", None, "_finding_statuses"),
    ("src/artifact_migration.py", None, "_recoverable_pending_review_finding_gap"),
    ("src/artifact_migration.py", None, "_recoverable_pending_correction_record"),
    ("src/artifact_migration.py", None, "_recoverable_pending_slice_denial_record"),
    ("src/artifact_migration.py", None, "_recoverable_pending_work_record"),
    ("src/artifact_migration.py", None, "_pending_denied_review"),
    ("src/artifact_migration.py", None, "_state_has_review_projection"),
    ("src/artifact_migration.py", None, "_attestation_facts"),
    ("src/artifact_bridge.py", None, "finding_handoff_export_payload"),
    ("src/artifact_bridge.py", None, "finding_handoff_import_payload"),
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
    ("src/orchestrator.py", None, "_persisted_histories"),
    ("src/orchestrator.py", None, "_attach_record_events"),
    ("src/orchestrator.py", None, "_recoverable_final_denial_mirror_gap"),
    ("src/orchestrator.py", None, "_historical_correction_attribution_matches"),
    ("src/orchestrator.py", None, "run_pipeline"),
    ("src/orchestrator.py", None, "_apply_resumed_agent_profiles"),
    ("src/orchestrator.py", None, "run_production_workflow"),
    ("src/orchestrator.py", "ProductionWorkflowDriver", "assert_structured_decision_context"),
    ("src/orchestrator.py", "ProductionWorkflowDriver", "_start_provider_attempt"),
    ("src/orchestrator.py", "ProductionWorkflowDriver", "_reconcile_provider_effect"),
    ("src/orchestrator.py", "ProductionWorkflowDriver", "_write_side_effect_file"),
    ("src/orchestrator.py", "ProductionWorkflowDriver", "_execute_projection_write"),
    ("src/orchestrator.py", "ProductionWorkflowDriver", "_reconcile_pending_side_effects"),
    ("src/orchestrator.py", "ProductionWorkflowDriver", "authoritative_native_findings"),
    ("src/orchestrator.py", "ProductionWorkflowDriver", "carry_forward_native_findings"),
    ("src/orchestrator.py", "ProductionWorkflowDriver", "_persist_native_agent_request_bundle"),
    ("src/orchestrator.py", "ProductionWorkflowDriver", "_write_immutable_file"),
    ("src/orchestrator.py", "ProductionWorkflowDriver", "_write_native_codex_raw_response"),
    ("src/orchestrator.py", "ProductionWorkflowDriver", "_materialize_review_packet"),
    ("src/orchestrator.py", "ProductionWorkflowDriver", "_canonical_native_agent_result"),
    ("src/orchestrator.py", "ProductionWorkflowDriver", "recover_pending_native_codex"),
    ("src/orchestrator.py", "ProductionWorkflowDriver", "recover_pending_native_reviewer"),
    ("src/orchestrator.py", "ProductionWorkflowDriver", "recover_pending_native_reviewer_before_policy"),
    ("src/orchestrator.py", "ProductionWorkflowDriver", "persist_native_codex_contract"),
    ("src/orchestrator.py", "ProductionWorkflowDriver", "prepare_finding_handoff"),
    ("src/orchestrator.py", "ProductionWorkflowDriver", "checkpoint"),
    ("src/orchestrator.py", "ProductionWorkflowDriver", "_project_audit"),
    ("src/orchestrator.py", "ProductionWorkflowDriver", "finalize_audit"),
    ("src/orchestrator.py", "ProductionWorkflowDriver", "commit_slice"),
    ("src/inbox_watcher.py", "QueueSuccessEvidence", "__post_init__"),
    ("src/inbox_watcher.py", None, "load_rejection_marker"),
    ("src/inbox_watcher.py", None, "load_watch_identity"),
    ("src/inbox_watcher.py", None, "load_queue_success_evidence"),
    ("src/inbox_watcher.py", None, "finalize_queue_success"),
    ("src/inbox_watcher.py", None, "move_to_outbox_with_ledger"),
    ("src/inbox_watcher.py", None, "move_poison_to_outbox_recoverably"),
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

STRICT_BODY_TARGETS = tuple(
    target
    for target in COMPARISON_TARGETS
    if (
        target[0] in {
            "src/artifact_migration.py",
            "src/final_review_preflight.py",
        }
        and target[2] != "resolve_resume_state"
    )
    or target[2] == "review_payload_matches_result"
    or target[2]
    in {
        "_persisted_histories",
        "_attach_record_events",
        "_recoverable_final_denial_mirror_gap",
        "_historical_correction_attribution_matches",
        "finalize_audit",
        "commit_managed_audit_report",
    }
)

# Counts all explicit comparison expressions in functions which implement the
# matrix's comparison boundaries. This is deliberately conservative: even a
# non-divergence comparison added inside one of these boundaries forces S2's
# inventory to be reviewed instead of silently aging.
EXPECTED_COMPARISON_COUNTS = {
    "src/artifact_migration.py:resolve_resume_state": 117,
    "src/artifact_migration.py:assert_run_binding_mirror": 6,
    "src/artifact_migration.py:require_workflow_status_prefix": 3,
    "src/artifact_migration.py:require_workflow_event_prefix": 6,
    "src/artifact_migration.py:assert_workflow_status_mirror": 4,
    "src/artifact_migration.py:assert_slice_boundary_mirror": 4,
    "src/artifact_migration.py:require_gate_prefix": 1,
    "src/artifact_migration.py:assert_gate_mirror": 4,
    "src/artifact_migration.py:require_side_effect_ledger_prefix": 4,
    "src/artifact_migration.py:assert_invocation_failure_mirror": 4,
    "src/artifact_migration.py:project_transition_mirror_before_failure": 3,
    "src/artifact_migration.py:assert_side_effect_mirror": 5,
    "src/artifact_migration.py:_mirror_difference_code": 0,
    "src/artifact_migration.py:_finding_statuses": 2,
    "src/artifact_migration.py:_recoverable_pending_review_finding_gap": 16,
    "src/artifact_migration.py:_recoverable_pending_correction_record": 6,
    "src/artifact_migration.py:_recoverable_pending_slice_denial_record": 7,
    "src/artifact_migration.py:_recoverable_pending_work_record": 0,
    "src/artifact_migration.py:_pending_denied_review": 7,
    "src/artifact_migration.py:_state_has_review_projection": 5,
    "src/artifact_migration.py:_attestation_facts": 2,
    "src/artifact_bridge.py:finding_handoff_export_payload": 5,
    "src/artifact_bridge.py:finding_handoff_import_payload": 8,
    "src/artifact_bridge.py:review_payload_matches_result": 13,
    "src/artifact_bridge.py:ArtifactBridge.append": 3,
    "src/artifact_bridge.py:ArtifactBridge.record_side_effect_intent": 1,
    "src/artifact_bridge.py:ArtifactBridge.record_side_effect_result": 4,
    "src/artifact_store.py:ArtifactStore.append_context": 2,
    "src/artifact_store.py:ArtifactStore.put": 13,
    "src/artifact_store.py:ArtifactStore._ensure_append_index": 4,
    "src/artifact_store.py:ArtifactStore._head_cache_matches": 3,
    "src/artifact_store.py:ArtifactStore._refresh_append_head_cache": 2,
    "src/artifact_bridge.py:ArtifactBridge.start_provider_attempt": 21,
    "src/artifact_bridge.py:ArtifactBridge.finish_provider_attempt": 10,
    "src/artifact_bridge.py:ArtifactBridge.side_effect_result": 7,
    "src/final_review_preflight.py:run_final_review_preflight": 20,
    "src/final_review_preflight.py:_approved_external_paths": 15,
    "src/orchestrator.py:_persisted_histories": 3,
    "src/orchestrator.py:_attach_record_events": 16,
    "src/orchestrator.py:_recoverable_final_denial_mirror_gap": 10,
    "src/orchestrator.py:_historical_correction_attribution_matches": 7,
    "src/orchestrator.py:run_pipeline": 20,
    "src/orchestrator.py:_apply_resumed_agent_profiles": 3,
    "src/orchestrator.py:run_production_workflow": 38,
    "src/orchestrator.py:ProductionWorkflowDriver.assert_structured_decision_context": 11,
    "src/orchestrator.py:ProductionWorkflowDriver._start_provider_attempt": 19,
    "src/orchestrator.py:ProductionWorkflowDriver._reconcile_provider_effect": 13,
    "src/orchestrator.py:ProductionWorkflowDriver._write_side_effect_file": 3,
    "src/orchestrator.py:ProductionWorkflowDriver._execute_projection_write": 5,
    "src/orchestrator.py:ProductionWorkflowDriver._reconcile_pending_side_effects": 34,
    "src/orchestrator.py:ProductionWorkflowDriver.authoritative_native_findings": 11,
    "src/orchestrator.py:ProductionWorkflowDriver.carry_forward_native_findings": 6,
    "src/orchestrator.py:ProductionWorkflowDriver._persist_native_agent_request_bundle": 4,
    "src/orchestrator.py:ProductionWorkflowDriver._write_immutable_file": 3,
    "src/orchestrator.py:ProductionWorkflowDriver._write_native_codex_raw_response": 0,
    "src/orchestrator.py:ProductionWorkflowDriver._materialize_review_packet": 3,
    "src/orchestrator.py:ProductionWorkflowDriver._canonical_native_agent_result": 4,
    "src/orchestrator.py:ProductionWorkflowDriver.recover_pending_native_codex": 42,
    "src/orchestrator.py:ProductionWorkflowDriver.recover_pending_native_reviewer": 34,
    "src/orchestrator.py:ProductionWorkflowDriver.recover_pending_native_reviewer_before_policy": 31,
    "src/orchestrator.py:ProductionWorkflowDriver.persist_native_codex_contract": 11,
    "src/orchestrator.py:ProductionWorkflowDriver.prepare_finding_handoff": 13,
    "src/orchestrator.py:ProductionWorkflowDriver.checkpoint": 6,
    "src/orchestrator.py:ProductionWorkflowDriver._project_audit": 22,
    "src/orchestrator.py:ProductionWorkflowDriver.finalize_audit": 9,
    "src/orchestrator.py:ProductionWorkflowDriver.commit_slice": 45,
    "src/inbox_watcher.py:QueueSuccessEvidence.__post_init__": 5,
    "src/inbox_watcher.py:load_rejection_marker": 9,
    "src/inbox_watcher.py:load_watch_identity": 4,
    "src/inbox_watcher.py:load_queue_success_evidence": 11,
    "src/inbox_watcher.py:finalize_queue_success": 8,
    "src/inbox_watcher.py:move_to_outbox_with_ledger": 11,
    "src/inbox_watcher.py:move_poison_to_outbox_recoverably": 3,
    "src/inbox_watcher.py:watch_inbox": 43,
    "src/workflow.py:WorkflowHistory.from_dict": 7,
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
    "src/artifact_bridge.py:review_payload_matches_result": "b3233be38c3e4729058eba7ffd325fc94d29d4f08bd5ccfd08de0e3557eaf612",
    "src/artifact_migration.py:assert_run_binding_mirror": "e8febdf4104e65855caa2196ec8fad6f9e6ec5a81b3bdfc9a2ac1475daea9498",
    "src/artifact_migration.py:require_workflow_status_prefix": "964d356480288034c6dc52de377c2326c06d2db50d6aae52fd2b3d5dbcc5bdec",
    "src/artifact_migration.py:require_workflow_event_prefix": "2e9c88f52f65aeb0edfa9352f123f05c12c5a88ccdcfa590d813d5466bca1ddc",
    "src/artifact_migration.py:assert_workflow_status_mirror": "0d6ac0eec3998504048ccf74ee978930a4e2dfa3cd256e71268e143a774b7eae",
    "src/artifact_migration.py:assert_slice_boundary_mirror": "e0f6199f8d92c8f1d141822a0cbb2d6b80238cf247cc2eaacc83310d158c5d5f",
    "src/artifact_migration.py:require_gate_prefix": "980ecc54d5b6c651d9937728b37a9e7cdfc2d27d8d4100eee15fde0d85249774",
    "src/artifact_migration.py:assert_gate_mirror": "cc1214a7684e5c95ae519fac33f0624739167083440bbd58725888ce3a46e581",
    "src/artifact_migration.py:require_side_effect_ledger_prefix": "75a389d1d2170ebd424810be772b2050c6b3951591530505cd7ba480138103c6",
    "src/artifact_migration.py:assert_invocation_failure_mirror": "0a6caf71c272a876202ef11434386e304cd206f774f97e2f0ac996b92e5ddce0",
    "src/artifact_migration.py:project_transition_mirror_before_failure": "ffea068886e435d3c2b63d67f329625b9a0c00a511d88f084e1ea22a1aef89d4",
    "src/artifact_migration.py:assert_side_effect_mirror": "a987bcfdd6e57add8c76d0f45bcb005c7f560dcb2c682411eed66d9f67c76a54",
    "src/artifact_migration.py:_mirror_difference_code": "9433c6d83367347145eebab39e8fc4e3a989062ff9864bec6752710065ffbbc7",
    "src/artifact_migration.py:_finding_statuses": "cc4a0460cf13d1fbeba70deb2ae66dd19771bb31e8c56907139506ba3421c758",
    "src/artifact_migration.py:_recoverable_pending_review_finding_gap": "ad6728a16432bbf82d6973402ac25f2766c42513287c0530d8aa2a7a8eea8145",
    "src/artifact_migration.py:_recoverable_pending_correction_record": "2ccc8845cec0e65d669837cb5d64a4dbe5877b1e68d814ee255e57575032a08a",
    "src/artifact_migration.py:_recoverable_pending_slice_denial_record": "dbc94b37591451b1ea7d668cb54df91e945f957b4752a205e23cc8178acc8629",
    "src/artifact_migration.py:_recoverable_pending_work_record": "b9359c5e9abeac81d260e58a1e4f0d74c44ccaf740a772d06d71e32e6a333934",
    "src/artifact_migration.py:_pending_denied_review": "b91272ae23c736309c6e7f5784cd95a549b5229fd3033a365c00c81e96fcc447",
    "src/artifact_migration.py:_state_has_review_projection": "f6036ea4a3170f103a275fef04b949f4803d31a5d986ae15c1db2b1ae00ee83d",
    "src/artifact_migration.py:_attestation_facts": "482ceda433b65606b715f3433cbbc2c1b320ffb0b706e672ba2521d83a112923",
    "src/final_review_preflight.py:run_final_review_preflight": "8ea919e177653eee0f5c6ecac64ee1598f58b33126dfa1df6ab1eb0a04caac61",
    "src/final_review_preflight.py:_approved_external_paths": "24a9647addbf8df21fba7ecd0e25164e7237b00eb9c831bd7ee7b9ce1b8f5439",
    "src/orchestrator.py:_persisted_histories": "9a392ebaac497327344362a78b1a9dd5580c1e7d046fb6e0aa3b585db0a2a978",
    "src/orchestrator.py:_attach_record_events": "7065cd5a4554100a800dd581702c9738d89e6134736908703b715e18c9885d6b",
    "src/orchestrator.py:_recoverable_final_denial_mirror_gap": "3b78ce8c3861bfd62f8fd8728f7346b5cff5e1b6c12ab8b3b6c0f78bdc394331",
    "src/orchestrator.py:_historical_correction_attribution_matches": "9ca739a787d60da279c0bbd10a22d29f6a1c583d05434e704b616e5925afec35",
    "src/orchestrator.py:ProductionWorkflowDriver.finalize_audit": "ab243e85a4b66bc06e1025a688c87c651443ac32baa5b8018bb92f421b99bd69",
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
        node = _function_node(relative_path, class_name, function_name)
        label = ".".join(part for part in (class_name, function_name) if part)
        result[f"{relative_path}:{label}"] = sum(
            isinstance(item, ast.Compare)
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
    driver = _class_node(_tree("src/orchestrator.py"), "ProductionWorkflowDriver")
    values = (
        node.value
        for node in ast.walk(driver)
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
    source = _source("src/artifact_migration.py")
    source_strings = _string_constants("src/artifact_migration.py")
    document = MATRIX_PATH.read_text(encoding="utf-8")
    assert _raise_count("src/artifact_migration.py", "mismatch") == 43
    assert source.count("differs from state-v3") == 20
    for marker in MIGRATION_MISMATCH_MARKERS:
        assert marker in source_strings
        assert marker in document

    assert _raise_count("src/artifact_migration.py", "ArtifactResumeError") == 32
    for marker in RESUME_ERROR_MARKERS:
        assert marker in (source if marker == "exc.diagnostic.message" else source_strings)
        assert marker in document


def test_bridge_error_inventory_is_source_bound() -> None:
    paths = (
        "src/artifact_migration.py",
        "src/artifact_bridge.py",
        "src/orchestrator.py",
    )
    combined_source = "\n".join(_string_constants(path) for path in paths)
    document = MATRIX_PATH.read_text(encoding="utf-8")
    assert sum(_raise_count(path, "ArtifactBridgeError") for path in paths) == 24
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
    assert call_count == 15
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
        "src/orchestrator.py",
        "ProductionWorkflowDriver",
        "persist_validation_attestation",
    )
    canonical = ast.dump(node, annotate_fields=True, include_attributes=False)
    assert "logical_id" in canonical
    assert canonical.count("attestation_id") >= 2
    document = MATRIX_PATH.read_text(encoding="utf-8")
    assert "ArtifactRecord.logical_id" in document


def test_managed_audit_commit_boundary_is_source_and_document_bound() -> None:
    finalize = ast.dump(
        _function_node("src/orchestrator.py", "ProductionWorkflowDriver", "finalize_audit"),
        include_attributes=False,
    )
    workflow = ast.dump(
        _function_node("src/orchestrator.py", None, "run_production_workflow"),
        include_attributes=False,
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
    assert actual == EXPECTED_COMPARISON_COUNTS, actual


def test_recovery_and_preflight_predicate_bodies_are_frozen() -> None:
    actual = _body_digest_inventory()
    assert actual == EXPECTED_STRICT_BODY_DIGESTS, actual
