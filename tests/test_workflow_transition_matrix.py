from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass, replace
from functools import lru_cache
import hashlib
from pathlib import Path
import re
import subprocess
import pytest

import orchestrator
from artifact_bridge import ArtifactBridge
from artifact_models import (
    BindingPayload,
    CommandSpec,
    CorrectionWorkUnitPayload,
    FindingTransitionPayload,
    FingerprintKind,
    ProviderAttemptPayload,
    ProviderInputComponentPayload,
    ProviderInputMeasurementPayload,
    Role,
    RoleProfilePayload,
    ReviewEvidencePayload,
    ReviewPayload,
    RunIdentityPayload,
    RunProfilePayload,
    ValidationAttestationPayload,
    ValidationResult,
    WorkUnitPayload,
    technical_text_evidence,
)
from artifact_resume import resolve_resume_state
from artifact_replay import ArtifactReplayError, replay_artifacts, replay_findings
from artifact_store import ArtifactStore
from content_authority_support import (
    append_provider_decision_authority,
    append_validation_authority,
)
from contracts import (
    AgentRole,
    FindingClass,
    FindingOrigin,
    FindingRecord,
    FindingStatus,
    PlannedSlice,
)
from finding_order import finding_id_sort_key
from gates import BUILTIN_STOP_RULES
from orchestrator import ProductionWorkflowDriver
from state_io import (
    write_workflow_projection_checkpoint,
    write_workflow_state_projection,
)
from workflow import WorkflowExecutionError, WorkflowHistory
from workflow_state import (
    AgentFailureKind,
    GateRecord,
    GateReason,
    GateStatus,
    InvocationFailureRecord,
    ProtocolBinding,
    ProtocolMode,
    Reviewer,
    SliceStatus,
    WorkflowState,
    WorkflowStateValidationError,
    WorkflowStep,
    WorkUnitKind,
    WorkUnitStatus,
    init_workflow_state,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
PREFIX_PATTERN = re.compile(r"^([A-Z][A-Z0-9_-]*) \| ")
SYNTHETIC_TECHNICAL_TEXT = technical_text_evidence(
    "synthetic invocation failure"
)[0]


@dataclass(frozen=True)
class TransitionOracleRow:
    """Literal expectations; production transitions only produce the actual side."""

    case_id: str
    transition: str
    kind: str
    step: str
    round_number: int
    ledger: tuple[str, ...]
    provider_findings: tuple[str, ...]
    work_unit_count: int
    record_count: int
    mirror_count: int
    external_calls: int
    gate_reason: str
    gate_status: str
    checkpoint_state: str


@dataclass(frozen=True)
class TransitionEvidence:
    """Actual durable evidence collected independently of the literal oracle."""

    ledger: tuple[str, ...]
    record_count: int
    mirror_count: int
    external_calls: int
    checkpoint_state: str


# This is deliberately reviewable literal data.  Do not replace these values with
# values calculated by replay_findings(), WorkflowState, or the driver under test.
TRANSITION_ORACLE = (
    TransitionOracleRow(
        "plan-denial-round-2",
        "plan-review-denied",
        "plan",
        "codex_plan_revision",
        2,
        ("C-01:open:blocker",),
        ("C-01",),
        1,
        1,
        1,
        1,
        "none",
        "clear",
        "persisted",
    ),
    TransitionOracleRow(
        "plan-to-slice",
        "plan-approved",
        "slice",
        "codex_implementation",
        1,
        (),
        (),
        2,
        0,
        0,
        1,
        "none",
        "clear",
        "persisted",
    ),
    TransitionOracleRow(
        "slice-denial-round-2",
        "slice-review-denied",
        "slice",
        "codex_correction",
        2,
        ("C-01:open:blocker",),
        ("C-01",),
        2,
        1,
        1,
        1,
        "none",
        "clear",
        "persisted",
    ),
    TransitionOracleRow(
        "slice-to-final",
        "slice-committed",
        "final_review",
        "codex_final_review",
        1,
        ("C-01:closed:blocker",),
        (),
        3,
        2,
        1,
        1,
        "none",
        "clear",
        "persisted",
    ),
    TransitionOracleRow(
        "final-to-correction",
        "final-review-denied",
        "correction",
        "codex_final_correction",
        1,
        ("C-01:open:blocker", "C-02:closed:observation"),
        ("C-01",),
        4,
        3,
        2,
        1,
        "none",
        "clear",
        "persisted",
    ),
    TransitionOracleRow(
        "correction-denial-round-2",
        "correction-review-denied-with-new-blocker",
        "correction",
        "codex_final_correction",
        2,
        (
            "C-01:open:blocker",
            "C-02:closed:observation",
            "C-03:open:blocker",
        ),
        ("C-01", "C-03"),
        4,
        4,
        3,
        1,
        "none",
        "clear",
        "persisted",
    ),
    TransitionOracleRow(
        "correction-to-final",
        "correction-committed",
        "final_review",
        "codex_final_review",
        1,
        (
            "C-01:closed:blocker",
            "C-02:closed:observation",
            "C-03:closed:blocker",
            "C-99:open:observation",
        ),
        (),
        5,
        7,
        4,
        1,
        "none",
        "clear",
        "persisted",
    ),
    TransitionOracleRow(
        "mirror-before-checkpoint",
        "slice-review-denied-mirror-persisted",
        "slice",
        "codex_correction",
        2,
        ("C-01:open:blocker",),
        ("C-01",),
        2,
        1,
        1,
        1,
        "none",
        "clear",
        "missing",
    ),
    TransitionOracleRow(
        "head-drift-user-gate",
        "commit-head-drift",
        "slice",
        "slice_commit",
        1,
        (),
        (),
        2,
        0,
        0,
        0,
        "unexpected_file:HEAD-DRIFT:user",
        "awaiting_user_decision",
        "persisted",
    ),
    TransitionOracleRow(
        "slice-boundary-policy-gate",
        "slice-boundary-head-drift",
        "slice",
        "codex_implementation",
        1,
        (),
        (),
        2,
        0,
        0,
        0,
        "unexpected_file:SLICE-HEAD-DRIFT:policy",
        "awaiting_user_decision",
        "persisted",
    ),
    TransitionOracleRow(
        "scope-user-gate",
        "scope-violation",
        "slice",
        "claude_slice_review",
        1,
        (),
        (),
        2,
        0,
        0,
        0,
        "unexpected_file:UNEXPECTED-PATH:user",
        "awaiting_user_decision",
        "persisted",
    ),
)


# These transition fixtures model one already completed agent invocation.  This
# list is intentionally separate from TRANSITION_ORACLE so changing an expected
# external-call count cannot also change the durable input evidence.
PROVIDER_ATTEMPT_CASES = frozenset(
    {
        "plan-denial-round-2",
        "plan-to-slice",
        "slice-denial-round-2",
        "slice-to-final",
        "final-to-correction",
        "correction-denial-round-2",
        "correction-to-final",
        "mirror-before-checkpoint",
    }
)


@dataclass(frozen=True)
class GateSourceRow:
    rule_id: str
    reason: str
    kind: str
    emission: str
    producers: tuple[str, ...]
    cases: tuple[str, ...]
    prefixless_grammar: str | None = None
    direct_emission_count: int | None = None
    forwarded_exception: str | None = None


# Exact producer/emission bindings.  Prefixless families are intentional and use
# full-match grammars rather than an empty or generic "dynamic" identity.
GATE_SOURCE_MAP = (
    GateSourceRow(
        "HEAD-DRIFT",
        "unexpected_file",
        "user",
        "workflow._commit",
        ("workflow_git_commit._prepare_git_operation",),
        ("head-drift-user-gate",),
        forwarded_exception="WorkflowCommitApprovalRequired",
    ),
    GateSourceRow(
        "SLICE-HEAD-DRIFT",
        "unexpected_file",
        "policy",
        "workflow_production._run_production_transition_loop",
        ("workflow_production._run_production_transition_loop",),
        ("slice-boundary-policy-gate",),
    ),
    GateSourceRow(
        "UNEXPECTED-PATH",
        "unexpected_file",
        "user",
        "workflow.reframe_unexpected_path_stop_gate",
        (
            "gates.BUILTIN_STOP_RULES",
            "workflow.reframe_unexpected_path_stop_gate",
            "workflow._validate_plan_before_review",
            "workflow._run_review",
            "workflow._invoke_role",
            "workflow._commit",
        ),
        ("scope-user-gate",),
    ),
    GateSourceRow(
        "PROVIDER-INPUT-BUDGET",
        "bootstrap_check",
        "resume",
        "workflow._invoke_role",
        ("workflow._invoke_role", "workflow_baseline.bootstrap_fact"),
        ("provider-input-budget-resume",),
    ),
    GateSourceRow(
        "FINAL-REVIEW-PREFLIGHT",
        "bootstrap_check",
        "resume",
        "workflow._invoke_role",
        ("workflow._invoke_role",),
        ("final-review-preflight-fallback-resume",),
        direct_emission_count=1,
    ),
    *(
        GateSourceRow(
            rule_id,
            "bootstrap_check",
            "resume",
            "workflow._invoke_role",
            ("final_review_preflight.run_final_review_preflight",),
            (case_id,),
            direct_emission_count=1,
        )
        for rule_id, case_id in (
            ("MEASUREMENT-TYPE", "measurement-type-resume"),
            ("OPERATION-NOT-FINAL", "operation-not-final-resume"),
            ("STATE-TRANSITION-MISMATCH", "state-transition-mismatch-resume"),
            ("MEASUREMENT-RUN-MISMATCH", "measurement-run-mismatch-resume"),
            ("MEASUREMENT-DENIED", "measurement-denied-resume"),
            ("PREMATURE-COMPLETION", "premature-completion-resume"),
            ("FOREIGN-RUN-RECORD", "foreign-run-record-resume"),
            ("MISSING-REFERENCE", "missing-reference-resume"),
            ("FINGERPRINT-MISMATCH", "fingerprint-mismatch-resume"),
            ("UNAUTHORIZED-PATH", "unauthorized-path-resume"),
            ("ATTESTATION-MISSING", "attestation-missing-resume"),
            ("ATTESTATION-FAILED", "attestation-failed-resume"),
            ("SLICE-BINDING-MISSING", "slice-binding-missing-resume"),
            ("CODEX-FINAL-RESULT-MISSING", "codex-final-result-missing-resume"),
        )
    ),
    GateSourceRow(
        "BRANCH-MISMATCH",
        "stop_request",
        "policy",
        "workflow._apply_pre_agent_policy_gates",
        ("gates.BUILTIN_STOP_RULES", "workflow._apply_pre_agent_policy_gates"),
        ("branch-mismatch",),
    ),
    GateSourceRow(
        "VALIDATION-UNAVAILABLE",
        "stop_request",
        "policy",
        "workflow._run_review",
        ("gates.BUILTIN_STOP_RULES",),
        ("validation-unavailable",),
    ),
    GateSourceRow(
        "CONTRACT-UNCLEAR",
        "stop_request",
        "policy",
        "workflow._halt_for_stop_request",
        ("gates.BUILTIN_STOP_RULES",),
        ("contract-unclear",),
    ),
    GateSourceRow(
        "CODEX-NOT-READY",
        "stop_request",
        "policy",
        "workflow._apply_agent_output",
        ("workflow._apply_agent_output",),
        ("codex-not-ready",),
    ),
    GateSourceRow(
        "CODEX-FINAL-REPORT-NOT-READY",
        "stop_request",
        "policy",
        "workflow._run_final_codex_report",
        ("workflow._run_final_codex_report",),
        ("codex-final-report-not-ready",),
    ),
    GateSourceRow(
        "NO-IMPLEMENTATION-CHANGES",
        "stop_request",
        "policy",
        "workflow._run_review",
        ("workflow._run_review",),
        ("no-implementation-changes",),
    ),
    GateSourceRow(
        "FINAL-REVIEW-ROUNDS-EXHAUSTED",
        "stop_request",
        "policy",
        "workflow._halt_exhausted_final_review_rounds",
        ("workflow._halt_exhausted_final_review_rounds",),
        ("final-review-rounds-exhausted",),
    ),
    GateSourceRow(
        "PLAN-CONTRACT-INVALID",
        "stop_request",
        "policy",
        "workflow._validate_plan_before_review",
        (
            "workflow._validate_plan_before_review",
            "workflow.reframe_unexpected_path_stop_gate",
        ),
        ("plan-contract-invalid",),
    ),
    GateSourceRow(
        "QUOTA-RESUME-DIFF",
        "quota_resume_diff",
        "user",
        "workflow._revalidate_waiting_diff",
        ("workflow._revalidate_waiting_diff",),
        ("quota-resume-diff",),
        direct_emission_count=1,
    ),
    GateSourceRow(
        "QUOTA-RESUME-DIFF",
        "stop_request",
        "policy",
        "workflow._revalidate_waiting_diff",
        ("workflow._revalidate_waiting_diff",),
        ("quota-resume-diff-policy",),
        direct_emission_count=2,
    ),
    GateSourceRow(
        "PLAN-APPROVAL",
        "plan_approval",
        "user",
        "workflow._apply_review_result",
        ("workflow._apply_review_result",),
        ("plan-approval",),
    ),
    GateSourceRow(
        "PREFIXLESS:ANCHOR-CHANGE",
        "anchor_change",
        "user",
        "workflow._apply_anchor_gate",
        ("workflow._apply_anchor_gate",),
        ("anchor-change",),
        r"approved plan anchors changed and require plan review reset",
    ),
    GateSourceRow(
        "PREFIXLESS:MANUAL-SLICE",
        "manual_slice",
        "user",
        "workflow._commit",
        ("workflow._commit",),
        ("manual-slice",),
        r"manual slice approval is required before commit",
    ),
    GateSourceRow(
        "PREFIXLESS:TEST-CHANGE",
        "test_change",
        "user",
        "workflow._apply_test_change_gate",
        ("workflow._apply_test_change_gate",),
        ("test-change",),
        r"test changes require explicit approval before review",
    ),
    GateSourceRow(
        "PREFIXLESS:REVIEW-DENIAL",
        "iteration_limit",
        "user",
        "workflow_state.record_review_denial",
        ("workflow_state.record_review_denial",),
        ("review-denial-limit",),
        r"review denied by (?:claude) after [1-9][0-9]* Codex returns",
    ),
    GateSourceRow(
        "PREFIXLESS:INVOCATION-FAILURE",
        "instance_failure_or_quota",
        "resume",
        "workflow_state.record_invocation_failure",
        ("workflow_failure_recording.persist_invocation_failure",),
        ("invocation-failure",),
        (
            r"role=(?:codex|claude) step=[a-z_]+ invocation=[A-Za-z0-9._:-]+ "
            r"kind=[a-z_]+ resume=.+ auto=(?:true|false) continuations=[0-9]+ "
            r"provider=.+"
        ),
    ),
    GateSourceRow(
        "PREFIXLESS:LEGACY-QUOTA-REVALIDATION",
        "instance_failure_or_quota",
        "resume",
        "workflow_state.reopen_legacy_quota_resume_diff_gate",
        ("workflow_production.run_production_workflow",),
        ("legacy-quota-revalidation",),
        r"legacy QUOTA-RESUME-DIFF requires fingerprint-bound repository revalidation",
    ),
)


# Independent executable inputs for every Source-Map row.  The triplets are
# deliberately repeated rather than derived from GATE_SOURCE_MAP: changing a
# mapping row without changing the exercised state transition must turn red.
GATE_CASE_ORACLE = (
    ("head-drift-user-gate", "unexpected_file", "HEAD-DRIFT", "user"),
    ("slice-boundary-policy-gate", "unexpected_file", "SLICE-HEAD-DRIFT", "policy"),
    ("scope-user-gate", "unexpected_file", "UNEXPECTED-PATH", "user"),
    ("provider-input-budget-resume", "bootstrap_check", "PROVIDER-INPUT-BUDGET", "resume"),
    (
        "final-review-preflight-fallback-resume",
        "bootstrap_check",
        "FINAL-REVIEW-PREFLIGHT",
        "resume",
    ),
    ("measurement-type-resume", "bootstrap_check", "MEASUREMENT-TYPE", "resume"),
    (
        "operation-not-final-resume",
        "bootstrap_check",
        "OPERATION-NOT-FINAL",
        "resume",
    ),
    (
        "state-transition-mismatch-resume",
        "bootstrap_check",
        "STATE-TRANSITION-MISMATCH",
        "resume",
    ),
    (
        "measurement-run-mismatch-resume",
        "bootstrap_check",
        "MEASUREMENT-RUN-MISMATCH",
        "resume",
    ),
    (
        "measurement-denied-resume",
        "bootstrap_check",
        "MEASUREMENT-DENIED",
        "resume",
    ),
    (
        "premature-completion-resume",
        "bootstrap_check",
        "PREMATURE-COMPLETION",
        "resume",
    ),
    (
        "foreign-run-record-resume",
        "bootstrap_check",
        "FOREIGN-RUN-RECORD",
        "resume",
    ),
    ("missing-reference-resume", "bootstrap_check", "MISSING-REFERENCE", "resume"),
    (
        "fingerprint-mismatch-resume",
        "bootstrap_check",
        "FINGERPRINT-MISMATCH",
        "resume",
    ),
    ("unauthorized-path-resume", "bootstrap_check", "UNAUTHORIZED-PATH", "resume"),
    (
        "attestation-missing-resume",
        "bootstrap_check",
        "ATTESTATION-MISSING",
        "resume",
    ),
    (
        "attestation-failed-resume",
        "bootstrap_check",
        "ATTESTATION-FAILED",
        "resume",
    ),
    (
        "slice-binding-missing-resume",
        "bootstrap_check",
        "SLICE-BINDING-MISSING",
        "resume",
    ),
    (
        "codex-final-result-missing-resume",
        "bootstrap_check",
        "CODEX-FINAL-RESULT-MISSING",
        "resume",
    ),
    ("branch-mismatch", "stop_request", "BRANCH-MISMATCH", "policy"),
    ("validation-unavailable", "stop_request", "VALIDATION-UNAVAILABLE", "policy"),
    ("contract-unclear", "stop_request", "CONTRACT-UNCLEAR", "policy"),
    ("codex-not-ready", "stop_request", "CODEX-NOT-READY", "policy"),
    (
        "codex-final-report-not-ready",
        "stop_request",
        "CODEX-FINAL-REPORT-NOT-READY",
        "policy",
    ),
    ("no-implementation-changes", "stop_request", "NO-IMPLEMENTATION-CHANGES", "policy"),
    (
        "final-review-rounds-exhausted",
        "stop_request",
        "FINAL-REVIEW-ROUNDS-EXHAUSTED",
        "policy",
    ),
    ("plan-contract-invalid", "stop_request", "PLAN-CONTRACT-INVALID", "policy"),
    ("quota-resume-diff", "quota_resume_diff", "QUOTA-RESUME-DIFF", "user"),
    ("quota-resume-diff-policy", "stop_request", "QUOTA-RESUME-DIFF", "policy"),
    ("plan-approval", "plan_approval", "PLAN-APPROVAL", "user"),
    ("anchor-change", "anchor_change", "PREFIXLESS:ANCHOR-CHANGE", "user"),
    ("manual-slice", "manual_slice", "PREFIXLESS:MANUAL-SLICE", "user"),
    ("test-change", "test_change", "PREFIXLESS:TEST-CHANGE", "user"),
    ("review-denial-limit", "iteration_limit", "PREFIXLESS:REVIEW-DENIAL", "user"),
    (
        "invocation-failure",
        "instance_failure_or_quota",
        "PREFIXLESS:INVOCATION-FAILURE",
        "resume",
    ),
    (
        "legacy-quota-revalidation",
        "instance_failure_or_quota",
        "PREFIXLESS:LEGACY-QUOTA-REVALIDATION",
        "resume",
    ),
)


# There is deliberately no second, independently editable allowlist.  The
# reviewed source map is the sole classification authority for concrete gate
# identities; a newly discovered prefix therefore remains unknown until it has
# producer, emission and executable-case bindings in GATE_SOURCE_MAP.
REGISTERED_GATE_PREFIXES = frozenset(
    row.rule_id
    for row in GATE_SOURCE_MAP
    if not row.rule_id.startswith("PREFIXLESS:")
)


# Prefix-shaped product diagnostics that are deliberately not persisted gates.
# They are still inventoried so a future routing change cannot silently turn them
# into gates without updating the source map and matrix.
GATE_FOREIGN_PREFIXES = {
    "AGENT-PROFILE-DIFF": "resume profile validation raises before workflow execution",
    "TASK-SCOPE": "invalid Codex slice plans raise a workflow contract error",
}


GATE_CALLS = {
    "await_user_gate",
    "await_policy_gate",
    "record_review_denial",
    "record_invocation_failure",
    "await_bootstrap_resume",
    "reopen_legacy_quota_resume_diff_gate",
}


EXPECTED_GATE_CALL_SITES = Counter(
    {
        (
            "workflow_production.py",
            "_run_production_transition_loop",
            "await_policy_gate",
        ): 1,
        (
            "workflow_production.py",
            "run_production_workflow",
            "reopen_legacy_quota_resume_diff_gate",
        ): 1,
        ("workflow.py", "reframe_unexpected_path_stop_gate", "await_user_gate"): 1,
        ("workflow.py", "_apply_agent_output", "await_policy_gate"): 1,
        ("workflow.py", "_validate_plan_before_review", "await_user_gate"): 1,
        ("workflow.py", "_validate_plan_before_review", "await_policy_gate"): 1,
        ("workflow.py", "_run_final_codex_report", "await_policy_gate"): 2,
        ("workflow.py", "_run_review", "await_policy_gate"): 2,
        ("workflow.py", "_run_review", "await_user_gate"): 1,
        ("workflow.py", "_apply_review_result", "await_user_gate"): 1,
        ("workflow.py", "_apply_review_result", "record_review_denial"): 1,
        ("workflow.py", "_invoke_role", "await_user_gate"): 1,
        ("workflow.py", "_invoke_role", "await_bootstrap_resume"): 1,
        (
            "workflow_failure_recording.py",
            "persist_invocation_failure",
            "record_invocation_failure",
        ): 1,
        ("workflow.py", "_revalidate_waiting_diff", "await_policy_gate"): 2,
        ("workflow.py", "_revalidate_waiting_diff", "await_user_gate"): 1,
        ("workflow.py", "_commit", "await_user_gate"): 3,
        ("workflow.py", "_apply_anchor_gate", "await_user_gate"): 1,
        ("workflow.py", "_apply_pre_agent_policy_gates", "await_policy_gate"): 1,
        ("workflow.py", "_halt_for_stop_request", "await_policy_gate"): 1,
        (
            "workflow.py",
            "_halt_exhausted_final_review_rounds",
            "await_policy_gate",
        ): 1,
        ("workflow.py", "_apply_test_change_gate", "await_user_gate"): 1,
    }
)


EXPECTED_DIRECT_GATE_CONSTRUCTORS = Counter(
    {
        ("workflow_state.py", "<module>", "GateRecord"): 1,
        ("workflow_state.py", "inherit_prior_test_approval", "GateRecord"): 1,
        ("workflow_state.py", "await_user_gate", "GateRecord"): 1,
        ("workflow_state.py", "await_policy_gate", "GateRecord"): 1,
        ("workflow_state.py", "record_user_gate_decision", "GateRecord"): 1,
        ("workflow_state.py", "reopen_legacy_quota_resume_diff_gate", "GateRecord"): 1,
        ("workflow_state.py", "record_review_denial", "GateRecord"): 3,
        ("workflow_state.py", "record_invocation_failure", "GateRecord"): 1,
        ("workflow_state.py", "await_bootstrap_resume", "GateRecord"): 1,
        ("workflow_state.py", "resume_after_invocation_halt", "GateRecord"): 1,
        ("workflow_state.py", "resume_after_user_decision", "GateRecord"): 1,
    }
)


EXPECTED_GATE_REPLACEMENTS = Counter(
    {
        ("workflow_state.py", "inherit_prior_test_approval"): 1,
        ("workflow_state.py", "await_user_gate"): 1,
        ("workflow_state.py", "await_policy_gate"): 1,
        ("workflow_state.py", "record_user_gate_decision"): 1,
        ("workflow_state.py", "reopen_legacy_quota_resume_diff_gate"): 1,
        ("workflow_state.py", "record_review_denial"): 2,
        ("workflow_state.py", "record_invocation_failure"): 1,
        ("workflow_state.py", "await_bootstrap_resume"): 1,
        ("workflow_state.py", "resume_after_invocation_halt"): 1,
        ("workflow_state.py", "resume_after_user_decision"): 1,
    }
)


# The literal reachability classification prevents the matrix from suggesting
# transitions that the production state machine cannot construct.
EDGE_CLASSIFICATION = (
    ("plan", "plan", True, "denied plan review advances the same plan work unit"),
    ("plan", "slice", True, "complete plan then start first bound slice"),
    ("plan", "correction", False, "correction requires a completed final review"),
    ("plan", "final_review", False, "final review requires committed slices"),
    ("slice", "slice", True, "review denial stays in the same work unit at round >=2"),
    ("slice", "final_review", True, "all planned slices committed"),
    ("slice", "correction", False, "slice denial is an in-unit correction round"),
    ("slice", "plan", False, "implementation never returns to planning"),
    ("final_review", "final_review", False, "a final review denial starts correction"),
    ("final_review", "correction", True, "denied final review opens correction"),
    ("final_review", "slice", False, "completed slices are never reopened"),
    ("final_review", "plan", False, "final review cannot recreate planning"),
    ("correction", "correction", True, "denial advances the correction round"),
    ("correction", "final_review", True, "committed correction starts new final review"),
    ("correction", "slice", False, "correction uses its own appended slice record"),
    ("correction", "plan", False, "correction cannot recreate planning"),
)


def _parent_functions(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }


def _function_name(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
    return "<module>"


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    if isinstance(call.func, ast.Name):
        return call.func.id
    return None


def _source_trees(source_root: Path = SOURCE_ROOT) -> dict[str, ast.Module]:
    return {
        path.name: ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for path in sorted(source_root.glob("*.py"))
    }


def _gate_call_inventory(
    trees: dict[str, ast.Module],
) -> tuple[
    Counter[tuple[str, str, str]],
    Counter[tuple[str, str, str]],
    Counter[tuple[str, str]],
]:
    emissions: Counter[tuple[str, str, str]] = Counter()
    constructors: Counter[tuple[str, str, str]] = Counter()
    replacements: Counter[tuple[str, str]] = Counter()
    for filename, tree in trees.items():
        parents = _parent_functions(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            function = _function_name(node, parents)
            if name in GATE_CALLS:
                emissions[(filename, function, name)] += 1
            if name == "GateRecord":
                constructors[(filename, function, name)] += 1
            if name == "replace" and any(
                keyword.arg == "gate" for keyword in node.keywords
            ):
                replacements[(filename, function)] += 1
    return emissions, constructors, replacements


def _joined_string_prefix(node: ast.JoinedStr) -> str:
    result = ""
    for item in node.values:
        if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
            break
        result += item.value
    return result


def _literal_prefix_inventory(trees: dict[str, ast.Module]) -> set[str]:
    prefixes: set[str] = set()
    for tree in trees.values():
        for node in ast.walk(tree):
            value = (
                node.value
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
                else _joined_string_prefix(node)
                if isinstance(node, ast.JoinedStr)
                else None
            )
            if value is None:
                continue
            match = PREFIX_PATTERN.match(value)
            if match is not None:
                prefixes.add(match.group(1))
    return prefixes


def _assigned_prefixes_reaching_gate(trees: dict[str, ast.Module]) -> set[str]:
    """Follow every closed local producer into f-string gate details."""

    prefixes: set[str] = set()
    preflight_codes = _final_review_preflight_error_codes(trees)
    for tree in trees.values():
        for function in (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ):
            assignments = _assigned_rule_ids(
                function, preflight_error_codes=preflight_codes
            )
            for node in ast.walk(function):
                if not isinstance(node, ast.Call) or _call_name(node) not in GATE_CALLS:
                    continue
                detail = next(
                    (keyword.value for keyword in node.keywords if keyword.arg == "detail"),
                    None,
                )
                if not isinstance(detail, ast.JoinedStr):
                    continue
                interpolated_names = {
                    item.value.id
                    for item in detail.values
                    if isinstance(item, ast.FormattedValue)
                    and isinstance(item.value, ast.Name)
                }
                for name in interpolated_names:
                    prefixes.update(assignments.get(name, ()))
    return prefixes


def _final_review_preflight_error_codes(
    trees: dict[str, ast.Module],
) -> set[str]:
    """Derive the exact typed preflight denial codes that can feed ``error_code``."""

    return set(
        _cached_final_review_preflight_error_codes(
            trees.get("final_review_preflight.py")
        )
    )


@lru_cache(maxsize=None)
def _cached_final_review_preflight_error_codes(
    tree: ast.Module | None,
) -> frozenset[str]:
    """Cache the pure AST walk while preserving the public helper's set result."""

    if tree is None:
        return frozenset()
    function = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "run_final_review_preflight"
        ),
        None,
    )
    if function is None:
        return frozenset()
    codes: set[str] = set()
    for call in (
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and _call_name(node) == "_deny"
    ):
        code = (
            call.args[1]
            if len(call.args) > 1
            else next(
                (keyword.value for keyword in call.keywords if keyword.arg == "code"),
                None,
            )
        )
        if (
            isinstance(code, ast.Constant)
            and isinstance(code.value, str)
            and re.fullmatch(r"[A-Z][A-Z0-9_-]*", code.value)
        ):
            codes.add(code.value)
    return frozenset(codes)


def _unknown_gate_prefixes(trees: dict[str, ast.Module]) -> set[str]:
    registered_rules = {rule.id for rule in BUILTIN_STOP_RULES}
    discovered = (
        _literal_prefix_inventory(trees)
        | _assigned_prefixes_reaching_gate(trees)
        | registered_rules
    )
    return discovered - REGISTERED_GATE_PREFIXES - set(GATE_FOREIGN_PREFIXES)


GATE_API_KINDS = {
    "await_user_gate": "user",
    "await_policy_gate": "policy",
    "await_bootstrap_resume": "resume",
    "record_review_denial": "user",
    "record_invocation_failure": "resume",
    "reopen_legacy_quota_resume_diff_gate": "resume",
}


def _function_for_symbol(
    trees: dict[str, ast.Module], symbol: str
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    module, _, function = symbol.partition(".")
    tree = trees.get(f"{module}.py")
    if tree is None or not function:
        return None
    return _cached_function_for_tree(tree, function)


@lru_cache(maxsize=None)
def _cached_function_for_tree(
    tree: ast.Module, function: str
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    return next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function
        ),
        None,
    )


def _assigned_rule_ids(
    node: ast.AST, *, preflight_error_codes: set[str] | None = None
) -> dict[str, set[str]]:
    return {
        name: set(values)
        for name, values in _cached_assigned_rule_ids(
            node, frozenset(preflight_error_codes or ())
        )
    }


@lru_cache(maxsize=None)
def _cached_assigned_rule_ids(
    node: ast.AST, preflight_error_codes: frozenset[str]
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Cache immutable results for repeated walks over the same parsed nodes."""

    assigned: dict[str, set[str]] = {}
    for assignment in ast.walk(node):
        if not isinstance(assignment, (ast.Assign, ast.AnnAssign)):
            continue
        targets = (
            assignment.targets
            if isinstance(assignment, ast.Assign)
            else (assignment.target,)
        )
        if assignment.value is None:
            continue
        values = {
            value.value
            for value in ast.walk(assignment.value)
            if isinstance(value, ast.Constant)
            and isinstance(value.value, str)
            and re.fullmatch(r"[A-Z][A-Z0-9_-]*", value.value)
        }
        if preflight_error_codes and any(
            isinstance(value, ast.Attribute) and value.attr == "error_code"
            for value in ast.walk(assignment.value)
        ):
            values.update(preflight_error_codes)
        for target in targets:
            if isinstance(target, ast.Name) and values:
                assigned.setdefault(target.id, set()).update(values)
    return tuple(
        (name, tuple(sorted(values)))
        for name, values in sorted(assigned.items())
    )


def _merge_rule_ids(
    target: dict[str, set[str]], source: dict[str, set[str]]
) -> None:
    for name, values in source.items():
        target.setdefault(name, set()).update(values)


def _module_rule_ids(
    trees: dict[str, ast.Module], module: str
) -> dict[str, set[str]]:
    """Resolve module constants and explicitly imported rule constants only."""

    return {
        name: set(values)
        for name, values in _cached_module_rule_ids(
            tuple(sorted(trees.items())), module
        )
    }


@lru_cache(maxsize=None)
def _cached_module_rule_ids(
    tree_items: tuple[tuple[str, ast.Module], ...], module: str
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    trees = dict(tree_items)
    tree = trees[f"{module}.py"]
    resolved: dict[str, set[str]] = {}
    for statement in tree.body:
        if isinstance(statement, (ast.Assign, ast.AnnAssign)):
            _merge_rule_ids(resolved, _assigned_rule_ids(statement))
        elif isinstance(statement, ast.ImportFrom) and statement.module:
            imported_tree = trees.get(f"{statement.module}.py")
            if imported_tree is None:
                continue
            imported: dict[str, set[str]] = {}
            for imported_statement in imported_tree.body:
                if isinstance(imported_statement, (ast.Assign, ast.AnnAssign)):
                    _merge_rule_ids(
                        imported, _assigned_rule_ids(imported_statement)
                    )
            for alias in statement.names:
                if alias.name in imported:
                    resolved[alias.asname or alias.name] = set(imported[alias.name])
    return tuple(
        (name, tuple(sorted(values)))
        for name, values in sorted(resolved.items())
    )


def _rule_ids_in_expression(
    expression: ast.AST | None,
    assignments: dict[str, set[str]],
) -> set[str]:
    if expression is None:
        return set()
    rule_ids: set[str] = set()
    for node in ast.walk(expression):
        if isinstance(node, ast.Name):
            rule_ids.update(assignments.get(node.id, ()))
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        match = PREFIX_PATTERN.match(node.value)
        if match is not None:
            rule_ids.add(match.group(1))
        elif re.fullmatch(r"[A-Z][A-Z0-9_-]*", node.value):
            rule_ids.add(node.value)
    return rule_ids


def _kind_from_gate_record(call: ast.Call) -> str | None:
    status = next(
        (keyword.value for keyword in call.keywords if keyword.arg == "status"),
        None,
    )
    if status is None:
        return None
    values = {
        node.attr for node in ast.walk(status) if isinstance(node, ast.Attribute)
    }
    if "AWAITING_USER_DECISION" in values:
        return "user"
    if values & {"AWAITING_RESUME", "WAITING_FOR_QUOTA", "WAITING_FOR_RETRY"}:
        return "resume"
    return None


def _gate_reason_from_call(name: str | None, call: ast.Call) -> str | None:
    """Resolve the durable gate reason from the production call itself."""

    reason = next(
        (keyword.value for keyword in call.keywords if keyword.arg == "reason"),
        None,
    )
    if isinstance(reason, ast.Attribute):
        return reason.attr.lower()
    if isinstance(reason, ast.Constant) and isinstance(reason.value, str):
        return reason.value
    return {
        "await_bootstrap_resume": "bootstrap_check",
        "record_review_denial": "iteration_limit",
        "record_invocation_failure": "instance_failure_or_quota",
        "reopen_legacy_quota_resume_diff_gate": "instance_failure_or_quota",
    }.get(name)


def _gate_call_facts(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    assignments: dict[str, set[str]],
) -> tuple[tuple[str, str, frozenset[str], ast.AST | None], ...]:
    """Return reason, kind and literal rule identities for real gate calls."""

    facts: list[tuple[str, str, frozenset[str], ast.AST | None]] = []
    for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
        name = _call_name(call)
        kind = GATE_API_KINDS.get(name)
        if name == "GateRecord":
            kind = _kind_from_gate_record(call) or GATE_API_KINDS.get(function.name)
        reason = _gate_reason_from_call(name, call)
        if reason is None and name == "GateRecord":
            reason = {
                "record_invocation_failure": "instance_failure_or_quota",
                "reopen_legacy_quota_resume_diff_gate": "instance_failure_or_quota",
            }.get(function.name)
        if kind is None or reason is None:
            continue
        detail = next(
            (keyword.value for keyword in call.keywords if keyword.arg == "detail"),
            None,
        )
        facts.append(
            (reason, kind, frozenset(_rule_ids_in_expression(detail, assignments)), detail)
        )
    return tuple(facts)


def _forwarded_exception_gate_pairs(
    row: GateSourceRow,
    trees: dict[str, ast.Module],
) -> set[tuple[str, str]]:
    """Bind a forwarded ``exc.detail`` gate to its concrete exception handler."""

    if row.forwarded_exception is None:
        return set()
    function = _function_for_symbol(trees, row.emission)
    if function is None:
        return set()
    pairs: set[tuple[str, str]] = set()
    for handler in (
        node for node in ast.walk(function) if isinstance(node, ast.ExceptHandler)
    ):
        exception_name = (
            handler.type.id
            if isinstance(handler.type, ast.Name)
            else handler.type.attr
            if isinstance(handler.type, ast.Attribute)
            else None
        )
        if exception_name != row.forwarded_exception or not handler.name:
            continue
        for call in (
            node
            for statement in handler.body
            for node in ast.walk(statement)
            if isinstance(node, ast.Call)
        ):
            detail = next(
                (keyword.value for keyword in call.keywords if keyword.arg == "detail"),
                None,
            )
            if not (
                isinstance(detail, ast.Attribute)
                and detail.attr == "detail"
                and isinstance(detail.value, ast.Name)
                and detail.value.id == handler.name
            ):
                continue
            name = _call_name(call)
            kind = GATE_API_KINDS.get(name)
            reason = _gate_reason_from_call(name, call)
            if reason is not None and kind is not None:
                pairs.add((reason, kind))
    return pairs


def _forwarded_exception_has_rule_origin(
    row: GateSourceRow,
    trees: dict[str, ast.Module],
) -> bool:
    """Prove that a producer raises the forwarded exception with this rule ID."""

    if row.forwarded_exception is None:
        return True
    for producer in row.producers:
        function = _function_for_symbol(trees, producer)
        if function is None:
            continue
        module = producer.partition(".")[0]
        assignments = _module_rule_ids(trees, module)
        _merge_rule_ids(assignments, _assigned_rule_ids(function))
        for call in (
            node for node in ast.walk(function) if isinstance(node, ast.Call)
        ):
            if _call_name(call) != row.forwarded_exception:
                continue
            if row.rule_id in _rule_ids_in_expression(call, assignments):
                return True
    return False


def _production_gate_pairs(
    row: GateSourceRow,
    trees: dict[str, ast.Module],
) -> set[tuple[str, str]]:
    """Derive a row's reason/kind pairs from production, never its oracle."""

    function = _function_for_symbol(trees, row.emission)
    if function is None:
        return set()
    module = row.emission.partition(".")[0]
    assignments = _module_rule_ids(trees, module)
    _merge_rule_ids(
        assignments,
        _assigned_rule_ids(
            function,
            preflight_error_codes=_final_review_preflight_error_codes(trees),
        ),
    )
    forwarded_pairs = _forwarded_exception_gate_pairs(row, trees)
    if forwarded_pairs:
        return forwarded_pairs
    all_pairs: set[tuple[str, str]] = set()
    matched_pairs: set[tuple[str, str]] = set()
    for reason, kind, rule_ids, detail in _gate_call_facts(function, assignments):
        pair = (reason, kind)
        all_pairs.add(pair)
        if row.rule_id in rule_ids:
            matched_pairs.add(pair)
        elif row.prefixless_grammar is not None and detail is not None:
            literal_parts = "".join(
                node.value
                for node in ast.walk(detail)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
            )
            if literal_parts and re.match(row.prefixless_grammar, literal_parts):
                matched_pairs.add(pair)
    return matched_pairs or all_pairs


def _direct_gate_triples(
    trees: dict[str, ast.Module],
) -> tuple[tuple[str, str, str, str], ...]:
    """Inventory concrete triplets whose identity is visible at the gate call."""

    triples: list[tuple[str, str, str, str]] = []
    for filename, tree in trees.items():
        module = filename.removesuffix(".py")
        module_assignments = _module_rule_ids(trees, module)
        for function in (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ):
            assignments = {name: set(values) for name, values in module_assignments.items()}
            _merge_rule_ids(
                assignments,
                _assigned_rule_ids(
                    function,
                    preflight_error_codes=_final_review_preflight_error_codes(trees),
                ),
            )
            for reason, kind, rule_ids, _detail in _gate_call_facts(
                function, assignments
            ):
                for rule_id in rule_ids:
                    triples.append((reason, rule_id, kind, f"{module}.{function.name}"))
    return tuple(triples)


def _source_symbols(trees: dict[str, ast.Module]) -> set[str]:
    symbols: set[str] = set()
    for filename, tree in trees.items():
        module = filename.removesuffix(".py")
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                symbols.add(f"{module}.{node.name}")
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
                symbols.update(
                    f"{module}.{target.id}"
                    for target in targets
                    if isinstance(target, ast.Name)
                )
    return symbols


def _prefix_origins(trees: dict[str, ast.Module]) -> dict[str, set[str]]:
    """Return source locations that literally or locally produce each prefix."""

    origins: dict[str, set[str]] = {}
    for filename, tree in trees.items():
        module = filename.removesuffix(".py")
        parents = _parent_functions(tree)
        for node in ast.walk(tree):
            value = (
                node.value
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
                else _joined_string_prefix(node)
                if isinstance(node, ast.JoinedStr)
                else None
            )
            if value is None:
                continue
            match = PREFIX_PATTERN.match(value)
            prefix = (
                match.group(1)
                if match is not None
                else value
                if re.fullmatch(r"[A-Z][A-Z0-9_-]*", value)
                else None
            )
            if prefix is None:
                continue
            function = _function_name(node, parents)
            location = f"{module}.{function}" if function != "<module>" else f"{module}.BUILTIN_STOP_RULES"
            origins.setdefault(prefix, set()).add(location)
    return origins


PREFIXLESS_CASE_DETAILS = {
    "PREFIXLESS:ANCHOR-CHANGE": "approved plan anchors changed and require plan review reset",
    "PREFIXLESS:MANUAL-SLICE": "manual slice approval is required before commit",
    "PREFIXLESS:TEST-CHANGE": "test changes require explicit approval before review",
}


def _gate_identity(state: WorkflowState, kind: str) -> tuple[str, str, str]:
    gate = state.current_work_unit.gate
    assert gate.detail is not None
    match = PREFIX_PATTERN.match(gate.detail)
    if match is not None:
        rule_id = match.group(1)
    else:
        matches = tuple(
            row.rule_id
            for row in GATE_SOURCE_MAP
            if row.prefixless_grammar is not None
            and row.reason in {gate.reason.value, "instance_failure_or_quota"}
            and re.fullmatch(row.prefixless_grammar, gate.detail)
        )
        assert len(matches) == 1
        rule_id = matches[0]
    reason = (
        "instance_failure_or_quota"
        if gate.reason in {GateReason.INSTANCE_FAILURE, GateReason.QUOTA}
        else gate.reason.value
    )
    return reason, rule_id, kind


def _invocation_failure(state: WorkflowState) -> InvocationFailureRecord:
    return InvocationFailureRecord(
        invocation_id="matrix-invocation",
        idempotency_key="matrix-invocation-key",
        role="codex",
        failure_kind=AgentFailureKind.PROCESS,
        provider_text="provider process stopped",
        received_at="2026-08-27T10:00:30+00:00",
        step=state.current_step,
        slice_id=state.current_work_unit.slice_id,
        work_unit_id=state.current_work_unit_id,
        diagnostic_exit_code=3,
        process_exit_code=None,
        technical_text=SYNTHETIC_TECHNICAL_TEXT,
        diff_fingerprint="6" * 64,
    )


def _execute_gate_case(
    case: tuple[str, str, str, str],
) -> tuple[str, str, str]:
    case_id, reason, rule_id, kind = case
    state = _slice_state()
    if rule_id == "PREFIXLESS:REVIEW-DENIAL":
        while state.current_work_unit.gate.status is GateStatus.CLEAR:
            state = state.record_review_denial(
                reviewer=Reviewer.CLAUDE,
                open_findings=("C-01",),
                return_step=WorkflowStep.CODEX_CORRECTION,
            )
    elif rule_id == "PREFIXLESS:INVOCATION-FAILURE":
        state = state.record_invocation_failure(
            _invocation_failure(state), wait_automatically=False
        )
    elif rule_id == "PREFIXLESS:LEGACY-QUOTA-REVALIDATION":
        failed = state.record_invocation_failure(
            _invocation_failure(state), wait_automatically=False
        )
        legacy_unit = replace(
            failed.current_work_unit,
            status=WorkUnitStatus.AWAITING_USER_DECISION,
            gate=GateRecord(
                status=GateStatus.AWAITING_USER_DECISION,
                reason=GateReason.STOP_REQUEST,
                detail=(
                    f"QUOTA-RESUME-DIFF | repository changed while the role was waiting; "
                    f"expected {'7' * 64}, got {'8' * 64}"
                ),
            ),
        )
        state = failed._replace_current_unit(
            legacy_unit,
            slices=failed._slices_with_current_status(
                SliceStatus.AWAITING_USER_DECISION
            ),
        ).reopen_legacy_quota_resume_diff_gate()
    elif kind == "resume":
        state = state.await_bootstrap_resume(
            detail=f"{rule_id} | executable source-map evidence",
            fingerprint="6" * 64,
        )
    elif kind == "policy":
        state = state.await_policy_gate(
            reason=GateReason(reason),
            detail=f"{rule_id} | executable source-map evidence",
        )
    else:
        detail = PREFIXLESS_CASE_DETAILS.get(
            rule_id, f"{rule_id} | executable source-map evidence"
        )
        state = state.await_user_gate(
            reason=GateReason(reason),
            detail=detail,
            fingerprint="6" * 64,
            paths=("src/runtime.py",),
            resume_step=(
                state.current_step
                if reason in {"anchor_change", "quota_resume_diff"}
                else None
            ),
        )
    actual = _gate_identity(state, kind)
    assert case_id
    return actual


def _source_map_errors(
    rows: tuple[GateSourceRow, ...],
    trees: dict[str, ast.Module],
    case_results: dict[str, tuple[str, str, str]],
) -> set[str]:
    symbols = _source_symbols(trees)
    emissions, constructors, replacements = _gate_call_inventory(trees)
    emitter_locations = {
        f"{filename.removesuffix('.py')}.{function}"
        for filename, function, _name in emissions | constructors
    } | {
        f"{filename.removesuffix('.py')}.{function}"
        for filename, function in replacements
    }
    origins = _prefix_origins(trees)
    errors: set[str] = set()
    concrete_rows = {
        row.rule_id for row in rows if not row.rule_id.startswith("PREFIXLESS:")
    }
    discovered = (
        _literal_prefix_inventory(trees)
        | _assigned_prefixes_reaching_gate(trees)
        | {rule.id for rule in BUILTIN_STOP_RULES}
    )
    for rule_id in sorted(discovered - concrete_rows - set(GATE_FOREIGN_PREFIXES)):
        errors.add(f"unclassified-rule:{rule_id}")
    for rule_id in sorted(concrete_rows - discovered):
        errors.add(f"stale-rule:{rule_id}")
    declared_triples = {
        (row.reason, row.rule_id, row.kind)
        for row in rows
        if not row.rule_id.startswith("PREFIXLESS:")
    }
    direct_triples = _direct_gate_triples(trees)
    for reason, rule_id, kind, _emission in direct_triples:
        triple = (reason, rule_id, kind)
        if rule_id not in GATE_FOREIGN_PREFIXES and triple not in declared_triples:
            errors.add(f"unclassified-triplet:{reason}:{rule_id}:{kind}")
    authorized_origins: dict[str, set[str]] = {}
    for row in rows:
        if row.emission not in symbols or row.emission not in emitter_locations:
            errors.add(f"missing-emission:{row.rule_id}:{row.emission}")
        for producer in row.producers:
            if producer not in symbols:
                errors.add(f"missing-producer:{row.rule_id}:{producer}")
        if not _forwarded_exception_has_rule_origin(row, trees):
            errors.add(
                f"missing-forwarded-origin:{row.rule_id}:"
                f"{row.forwarded_exception or 'none'}"
            )
        expected = (row.reason, row.rule_id, row.kind)
        production_pairs = _production_gate_pairs(row, trees)
        if (row.reason, row.kind) not in production_pairs:
            errors.add(
                f"reason-kind-mismatch:{row.rule_id}:{row.reason}:{row.kind}:"
                f"{','.join(f'{reason}/{kind}' for reason, kind in sorted(production_pairs)) or 'none'}"
            )
        if row.direct_emission_count is not None:
            actual_count = sum(
                1
                for reason, rule_id, kind, emission in direct_triples
                if (reason, rule_id, kind, emission)
                == (row.reason, row.rule_id, row.kind, row.emission)
            )
            if actual_count != row.direct_emission_count:
                errors.add(
                    f"emission-count-mismatch:{row.rule_id}:{row.reason}:"
                    f"{row.kind}:{actual_count}:{row.direct_emission_count}"
                )
        if not row.cases:
            errors.add(f"missing-case:{row.rule_id}")
        for case_id in row.cases:
            if case_results.get(case_id) != expected:
                errors.add(f"case-mismatch:{row.rule_id}:{case_id}")
        if row.rule_id.startswith("PREFIXLESS:"):
            if row.prefixless_grammar is None:
                errors.add(f"missing-grammar:{row.rule_id}")
            continue
        locations = {row.emission, *row.producers}
        authorized_origins.setdefault(row.rule_id, set()).update(locations)
        if not origins.get(row.rule_id, set()) & locations:
            errors.add(f"unbound-origin:{row.rule_id}")
    for rule_id, locations in authorized_origins.items():
        unexpected = origins.get(rule_id, set()) - locations
        if unexpected:
            errors.add(f"unexpected-origin:{rule_id}:{','.join(sorted(unexpected))}")
    return errors


def _state() -> WorkflowState:
    return init_workflow_state(
        run_id="transition-matrix",
        task_file="/repo/task.md",
        branch="feature/transition-matrix",
        branch_base="a" * 40,
        first_slice_start_commit="a" * 40,
        slice_count=1,
        task_digest="b" * 64,
        task_scope_patterns=("src/runtime.py",),
        target_branch="feature/transition-matrix",
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            claude_review_transport="native-claude-review-v2",
            codex_result_transport="native-codex-v2",
        ),
        timestamp="2026-08-27T10:00:00+00:00",
    ).bind_slice_plan(
        (PlannedSlice(1, "implement", ("src/runtime.py",)),),
        first_start_commit="a" * 40,
        updated_at="2026-08-27T10:00:01+00:00",
    )


def _slice_state() -> WorkflowState:
    return (
        _state()
        .complete_current_work_unit(updated_at="2026-08-27T10:00:02+00:00")
        .start_work_unit(
            slice_id=1,
            kind=WorkUnitKind.SLICE,
            step=WorkflowStep.CODEX_IMPLEMENTATION,
            updated_at="2026-08-27T10:00:03+00:00",
        )
        .bind_current_slice_git_boundary(
            start_commit="a" * 40,
            scope_paths=("src/runtime.py",),
            start_fingerprint="c" * 64,
            updated_at="2026-08-27T10:00:04+00:00",
        )
    )


def _finding(
    finding_id: str,
    finding_class: FindingClass,
    status: FindingStatus,
    *,
    round_number: int = 1,
) -> FindingRecord:
    return FindingRecord(
        finding_id=finding_id,
        finding_class=finding_class,
        status=status,
        summary=f"Summary for {finding_id}",
        acceptance_test=f"Acceptance for {finding_id}",
        origin=FindingOrigin("FINAL", round_number, AgentRole.CLAUDE),
        status_rationale=("Verified closed." if status is FindingStatus.CLOSED else None),
    )


def _ledger_literal(findings: tuple[FindingRecord, ...]) -> tuple[str, ...]:
    # This normalizes actual output only. Expected values remain TRANSITION_ORACLE
    # literals and never call this helper.
    return tuple(
        f"{item.finding_id}:{item.status.value.lower()}:{item.finding_class.value.lower()}"
        for item in sorted(
            findings, key=lambda finding: finding_id_sort_key(finding.finding_id)
        )
    )


def _snapshot(
    state: WorkflowState,
    evidence: TransitionEvidence,
    *,
    transition: str,
    provider_findings: tuple[str, ...],
    gate_kind: str | None = None,
) -> TransitionOracleRow:
    unit = state.current_work_unit
    gate_reason = "none"
    if unit.gate.reason is not GateReason.NONE:
        assert unit.gate.detail is not None
        match = PREFIX_PATTERN.match(unit.gate.detail)
        rule = match.group(1) if match is not None else "PREFIXLESS"
        gate_reason = f"{unit.gate.reason.value}:{rule}:{gate_kind or 'internal'}"
    return TransitionOracleRow(
        "actual",
        transition,
        unit.kind.value,
        unit.current_step.value,
        unit.round_number,
        evidence.ledger,
        provider_findings,
        len(state.work_units),
        evidence.record_count,
        evidence.mirror_count,
        evidence.external_calls,
        gate_reason,
        unit.gate.status.value,
        evidence.checkpoint_state,
    )


def _assert_case(case_id: str, actual: TransitionOracleRow) -> None:
    expected = next(item for item in TRANSITION_ORACLE if item.case_id == case_id)
    assert replace(actual, case_id=case_id) == expected


def _exercise_transition_oracle(tmp_path: Path) -> None:
    c01_open = _finding("C-01", FindingClass.BLOCKER, FindingStatus.OPEN)
    c01_closed = replace(
        c01_open,
        status=FindingStatus.CLOSED,
        status_rationale="Verified closed.",
    )
    c02_closed = _finding("C-02", FindingClass.OBSERVATION, FindingStatus.CLOSED)
    c03_open = _finding(
        "C-03", FindingClass.BLOCKER, FindingStatus.OPEN, round_number=2
    )
    c03_closed = replace(
        c03_open,
        status=FindingStatus.CLOSED,
        status_rationale="Verified closed.",
    )
    historical = _finding("C-99", FindingClass.OBSERVATION, FindingStatus.OPEN)

    def assert_case(
        case_id: str,
        state: WorkflowState,
        findings: tuple[FindingRecord, ...],
        *,
        transition: str,
        provider_findings: tuple[str, ...],
        gate_kind: str | None = None,
        historical_findings: tuple[FindingRecord, ...] = (),
        carry_forward: bool = False,
        persist_checkpoint: bool = True,
    ) -> None:
        evidence = _transition_evidence(
            tmp_path,
            case_id,
            state,
            findings,
            historical_findings=historical_findings,
            carry_forward=carry_forward,
            persist_checkpoint=persist_checkpoint,
        )
        _assert_case(
            case_id,
            _snapshot(
                state,
                evidence,
                transition=transition,
                provider_findings=provider_findings,
                gate_kind=gate_kind,
            ),
        )

    plan_round_two = _state().with_current_step(
        WorkflowStep.CLAUDE_PLAN_REVIEW
    ).record_review_denial(
        reviewer=Reviewer.CLAUDE,
        open_findings=("C-01",),
        return_step=WorkflowStep.CODEX_PLAN_REVISION,
        updated_at="2026-08-27T10:00:01+00:00",
    )
    assert_case(
        "plan-denial-round-2",
        plan_round_two,
        (c01_open,),
        transition="plan-review-denied",
        provider_findings=plan_round_two.current_work_unit.open_findings,
    )

    slice_state = _slice_state()
    assert_case(
        "plan-to-slice",
        slice_state,
        (),
        transition="plan-approved",
        provider_findings=(),
    )

    slice_round_two = slice_state.with_current_step(
        WorkflowStep.CLAUDE_SLICE_REVIEW
    ).record_review_denial(
        reviewer=Reviewer.CLAUDE,
        open_findings=("C-01",),
        return_step=WorkflowStep.CODEX_CORRECTION,
        updated_at="2026-08-27T10:00:05+00:00",
    )
    assert_case(
        "slice-denial-round-2",
        slice_round_two,
        (c01_open,),
        transition="slice-review-denied",
        provider_findings=slice_round_two.current_work_unit.open_findings,
    )
    assert_case(
        "mirror-before-checkpoint",
        slice_round_two,
        (c01_open,),
        transition="slice-review-denied-mirror-persisted",
        provider_findings=slice_round_two.current_work_unit.open_findings,
        persist_checkpoint=False,
    )

    final_state = slice_state.complete_current_slice(
        commit_ref="d" * 40,
        updated_at="2026-08-27T10:00:06+00:00",
    ).start_final_review_work_unit(updated_at="2026-08-27T10:00:07+00:00")
    assert_case(
        "slice-to-final",
        final_state,
        (c01_closed,),
        transition="slice-committed",
        provider_findings=(),
    )

    correction = final_state.complete_current_work_unit(
        updated_at="2026-08-27T10:00:08+00:00"
    ).start_correction_work_unit(
        start_commit="d" * 40,
        scope_paths=("src/runtime.py",),
        start_fingerprint="e" * 64,
        finding_ids=("C-01",),
        updated_at="2026-08-27T10:00:09+00:00",
    )
    assert_case(
        "final-to-correction",
        correction,
        (c01_open, c02_closed),
        transition="final-review-denied",
        provider_findings=correction.current_work_unit.open_findings,
    )

    round_two = correction.with_current_step(
        WorkflowStep.CLAUDE_FINAL_REVIEW
    ).record_review_denial(
        reviewer=Reviewer.CLAUDE,
        open_findings=("C-01", "C-03"),
        return_step=WorkflowStep.CODEX_FINAL_CORRECTION,
        updated_at="2026-08-27T10:00:10+00:00",
    )
    assert_case(
        "correction-denial-round-2",
        round_two,
        (c01_open, c02_closed, c03_open),
        transition="correction-review-denied-with-new-blocker",
        provider_findings=round_two.current_work_unit.open_findings,
    )

    repeated_final = correction.complete_current_slice(
        commit_ref="f" * 40,
        updated_at="2026-08-27T10:00:11+00:00",
    ).start_final_review_work_unit(updated_at="2026-08-27T10:00:12+00:00")
    assert_case(
        "correction-to-final",
        repeated_final,
        (c01_closed, c02_closed, c03_closed),
        transition="correction-committed",
        provider_findings=(),
        historical_findings=(historical,),
        carry_forward=True,
    )

    head_gate = slice_state.with_current_step(WorkflowStep.SLICE_COMMIT).await_user_gate(
        reason=GateReason.UNEXPECTED_FILE,
        detail="HEAD-DRIFT | exact reviewed fingerprint and HEAD required",
        fingerprint="1" * 64,
        paths=("src/runtime.py",),
        resume_step=WorkflowStep.SLICE_COMMIT,
    )
    assert_case(
        "head-drift-user-gate",
        head_gate,
        (),
        transition="commit-head-drift",
        provider_findings=(),
        gate_kind="user",
    )
    approved = head_gate.record_user_gate_decision(
        approved=True,
        fingerprint="1" * 64,
        paths=("src/runtime.py",),
        rationale="Reviewed exact drift.",
    )
    assert approved.current_step is WorkflowStep.SLICE_COMMIT
    assert approved.current_work_unit.has_gate_approval(
        GateReason.UNEXPECTED_FILE, "1" * 64, ("src/runtime.py",)
    )

    boundary_gate = slice_state.await_policy_gate(
        reason=GateReason.UNEXPECTED_FILE,
        detail="SLICE-HEAD-DRIFT | persisted start HEAD differs",
    )
    assert_case(
        "slice-boundary-policy-gate",
        boundary_gate,
        (),
        transition="slice-boundary-head-drift",
        provider_findings=(),
        gate_kind="policy",
    )
    assert boundary_gate.current_work_unit.gate.paths == ()
    assert boundary_gate.current_work_unit.gate.resume_step is None

    scope_gate = slice_state.with_current_step(
        WorkflowStep.CLAUDE_SLICE_REVIEW
    ).await_user_gate(
        reason=GateReason.UNEXPECTED_FILE,
        detail="UNEXPECTED-PATH | canonical changes contain src/extra.py",
        fingerprint="2" * 64,
        paths=("src/extra.py",),
    )
    assert_case(
        "scope-user-gate",
        scope_gate,
        (),
        transition="scope-violation",
        provider_findings=(),
        gate_kind="user",
    )
    assert scope_gate.current_work_unit.gate.paths == ("src/extra.py",)

    # Prefix equality is part of the oracle; substring overlap is insufficient.
    head_evidence = _transition_evidence(
        tmp_path,
        "head-drift-prefix-swap",
        head_gate,
        (),
    )
    swapped = replace(
        _snapshot(
            head_gate,
            head_evidence,
            transition="commit-head-drift",
            provider_findings=(),
            gate_kind="user",
        ),
        gate_reason="unexpected_file:SLICE-HEAD-DRIFT:user",
    )
    with pytest.raises(AssertionError):
        _assert_case("head-drift-user-gate", swapped)


def test_literal_transition_oracle_covers_work_unit_round_and_gate_boundaries(
    tmp_path: Path,
) -> None:
    _exercise_transition_oracle(tmp_path)


def test_gate_source_map_and_structural_inventory_are_bidirectionally_closed() -> None:
    trees = _source_trees()
    emissions, constructors, replacements = _gate_call_inventory(trees)

    assert emissions == EXPECTED_GATE_CALL_SITES
    assert constructors == EXPECTED_DIRECT_GATE_CONSTRUCTORS
    assert replacements == EXPECTED_GATE_REPLACEMENTS
    assert _unknown_gate_prefixes(trees) == set()
    assert _assigned_prefixes_reaching_gate(trees) >= {"PROVIDER-INPUT-BUDGET"}

    case_results = {
        case[0]: _execute_gate_case(case) for case in GATE_CASE_ORACLE
    }
    case_ids = set(case_results)
    assert case_ids == {
        case_id for row in GATE_SOURCE_MAP for case_id in row.cases
    }
    for row in GATE_SOURCE_MAP:
        assert row.rule_id
        assert row.emission
        assert row.producers
        assert set(row.cases) <= case_ids
        if row.rule_id.startswith("PREFIXLESS:"):
            assert row.prefixless_grammar is not None
            re.compile(rf"^(?:{row.prefixless_grammar})$")
        else:
            assert row.prefixless_grammar is None

    # Every explicitly mapped concrete identity exists in the source/registry
    # inventory. Conversely the three high-risk identities have executable cases.
    concrete = {row.rule_id for row in GATE_SOURCE_MAP if not row.rule_id.startswith("PREFIXLESS:")}
    discovered = (
        _literal_prefix_inventory(trees)
        | _assigned_prefixes_reaching_gate(trees)
        | {rule.id for rule in BUILTIN_STOP_RULES}
    )
    assert REGISTERED_GATE_PREFIXES == concrete
    assert concrete == discovered - set(GATE_FOREIGN_PREFIXES)
    assert {
        "HEAD-DRIFT",
        "SLICE-HEAD-DRIFT",
        "UNEXPECTED-PATH",
        "PROVIDER-INPUT-BUDGET",
    } <= concrete
    assert set(GATE_FOREIGN_PREFIXES) <= _literal_prefix_inventory(trees)
    assert _source_map_errors(GATE_SOURCE_MAP, trees, case_results) == set()


def test_gate_source_map_rejects_orphans_and_per_emission_prefix_moves() -> None:
    trees = _source_trees()
    case_results = {
        case[0]: _execute_gate_case(case) for case in GATE_CASE_ORACLE
    }
    expected_preflight_codes = {
        "ATTESTATION-FAILED",
        "ATTESTATION-MISSING",
        "CODEX-FINAL-RESULT-MISSING",
        "FINGERPRINT-MISMATCH",
        "FOREIGN-RUN-RECORD",
        "MEASUREMENT-DENIED",
        "MEASUREMENT-RUN-MISMATCH",
        "MEASUREMENT-TYPE",
        "MISSING-REFERENCE",
        "OPERATION-NOT-FINAL",
        "PREMATURE-COMPLETION",
        "SLICE-BINDING-MISSING",
        "STATE-TRANSITION-MISMATCH",
        "UNAUTHORIZED-PATH",
    }
    assert _final_review_preflight_error_codes(trees) == expected_preflight_codes
    assert expected_preflight_codes | {"FINAL-REVIEW-PREFLIGHT"} <= (
        _assigned_prefixes_reaching_gate(trees)
    )
    invented = GateSourceRow(
        "HEAD-DRIFT",
        "unexpected_file",
        "user",
        "workflow.this_function_does_not_exist",
        ("orchestrator.also_missing",),
        ("head-drift-user-gate",),
    )
    errors = _source_map_errors((*GATE_SOURCE_MAP, invented), trees, case_results)
    assert any(item.startswith("missing-emission:HEAD-DRIFT") for item in errors)
    assert any(item.startswith("missing-producer:HEAD-DRIFT") for item in errors)

    workflow_source = (SOURCE_ROOT / "workflow.py").read_text(encoding="utf-8")
    orphaned = dict(trees)
    orphaned["workflow.py"] = ast.parse(
        workflow_source.replace(
            "def _apply_anchor_gate(",
            "def _removed_anchor_gate(",
            1,
        )
    )
    errors = _source_map_errors(GATE_SOURCE_MAP, orphaned, case_results)
    assert any(
        item.startswith("missing-emission:PREFIXLESS:ANCHOR-CHANGE")
        for item in errors
    )

    moved = dict(trees)
    moved["workflow.py"] = ast.parse(
        workflow_source.replace(
            'code = "PROVIDER-INPUT-BUDGET"',
            'code = "HEAD-DRIFT"',
            1,
        )
    )
    errors = _source_map_errors(GATE_SOURCE_MAP, moved, case_results)
    assert any(item.startswith("unexpected-origin:HEAD-DRIFT") for item in errors)

    # A newly discovered producer cannot be silenced by a standalone allowlist:
    # concrete registrations are derived from the fully bound Source-Map itself.
    dynamic = dict(trees)
    dynamic["workflow.py"] = ast.parse(
        workflow_source.replace(
            'code = "PROVIDER-INPUT-BUDGET"',
            'code = "ZZZ-DYNAMIC"',
            1,
        )
    )
    assert "ZZZ-DYNAMIC" in _unknown_gate_prefixes(dynamic)
    errors = _source_map_errors(GATE_SOURCE_MAP, dynamic, case_results)
    assert "unclassified-rule:ZZZ-DYNAMIC" in errors

    # BoolOp fallbacks at the same real producer are equally authoritative; a
    # new ``error_code or literal`` identity cannot escape the constant branch
    # mutation above.
    boolop_escape = dict(trees)
    boolop_escape["workflow.py"] = ast.parse(
        workflow_source.replace(
            'error.result.error_code or "FINAL-REVIEW-PREFLIGHT"',
            'error.result.error_code or "ZZZ-ESCAPE"',
            1,
        )
    )
    assert "ZZZ-ESCAPE" in _unknown_gate_prefixes(boolop_escape)
    errors = _source_map_errors(GATE_SOURCE_MAP, boolop_escape, case_results)
    assert "unclassified-rule:ZZZ-ESCAPE" in errors
    assert "unclassified-triplet:bootstrap_check:ZZZ-ESCAPE:resume" in errors

    without_fallback_row = tuple(
        row for row in GATE_SOURCE_MAP if row.rule_id != "FINAL-REVIEW-PREFLIGHT"
    )
    errors = _source_map_errors(without_fallback_row, trees, case_results)
    assert "unclassified-rule:FINAL-REVIEW-PREFLIGHT" in errors
    assert (
        "unclassified-triplet:bootstrap_check:FINAL-REVIEW-PREFLIGHT:resume"
        in errors
    )

    # Even if expectation and executable-case literals are changed together,
    # the kind remains bound to the production API used by the emitter.
    retyped_rows = tuple(
        replace(row, kind="user") if row.rule_id == "BRANCH-MISMATCH" else row
        for row in GATE_SOURCE_MAP
    )
    retyped_results = dict(case_results)
    retyped_results["branch-mismatch"] = (
        "stop_request",
        "BRANCH-MISMATCH",
        "user",
    )
    errors = _source_map_errors(retyped_rows, trees, retyped_results)
    assert (
        "reason-kind-mismatch:BRANCH-MISMATCH:stop_request:user:stop_request/policy"
        in errors
    )

    # The reason is independently bound to the production call as well: changing
    # both the map and executable expectation cannot manufacture a new valid
    # triplet.
    re_reasoned_rows = tuple(
        replace(row, reason="stop_request") if row.rule_id == "HEAD-DRIFT" else row
        for row in GATE_SOURCE_MAP
    )
    re_reasoned_results = dict(case_results)
    re_reasoned_results["head-drift-user-gate"] = (
        "stop_request",
        "HEAD-DRIFT",
        "user",
    )
    errors = _source_map_errors(re_reasoned_rows, trees, re_reasoned_results)
    assert any(
        item.startswith("reason-kind-mismatch:HEAD-DRIFT:stop_request:user:")
        for item in errors
    )

    policy_triplet = ("stop_request", "QUOTA-RESUME-DIFF", "policy")
    direct_quota = tuple(
        (reason, rule_id, kind, emission)
        for reason, rule_id, kind, emission in _direct_gate_triples(trees)
        if rule_id == "QUOTA-RESUME-DIFF"
    )
    assert direct_quota.count((*policy_triplet, "workflow._revalidate_waiting_diff")) == 2
    assert direct_quota.count(
        (
            "quota_resume_diff",
            "QUOTA-RESUME-DIFF",
            "user",
            "workflow._revalidate_waiting_diff",
        )
    ) == 1

    without_policy_row = tuple(
        row
        for row in GATE_SOURCE_MAP
        if (row.reason, row.rule_id, row.kind) != policy_triplet
    )
    errors = _source_map_errors(without_policy_row, trees, case_results)
    assert "unclassified-triplet:stop_request:QUOTA-RESUME-DIFF:policy" in errors

    without_policy_case = dict(case_results)
    del without_policy_case["quota-resume-diff-policy"]
    errors = _source_map_errors(GATE_SOURCE_MAP, trees, without_policy_case)
    assert "case-mismatch:QUOTA-RESUME-DIFF:quota-resume-diff-policy" in errors

    policy_call = (
        "state.await_policy_gate(\n"
        "                reason=GateReason.STOP_REQUEST,\n"
        "                detail=(\n"
        '                    "QUOTA-RESUME-DIFF | no persisted Slice fingerprint is "'
    )
    assert policy_call in workflow_source
    reason_changed = dict(trees)
    reason_changed["workflow.py"] = ast.parse(
        workflow_source.replace(
            policy_call,
            policy_call.replace(
                "reason=GateReason.STOP_REQUEST",
                "reason=GateReason.UNEXPECTED_FILE",
            ),
            1,
        )
    )
    errors = _source_map_errors(GATE_SOURCE_MAP, reason_changed, case_results)
    assert "unclassified-triplet:unexpected_file:QUOTA-RESUME-DIFF:policy" in errors
    assert any(
        item.startswith(
            "emission-count-mismatch:QUOTA-RESUME-DIFF:stop_request:policy:1:2"
        )
        for item in errors
    )

    kind_changed = dict(trees)
    kind_changed["workflow.py"] = ast.parse(
        workflow_source.replace(
            policy_call,
            policy_call.replace("await_policy_gate", "await_user_gate"),
            1,
        )
    )
    errors = _source_map_errors(GATE_SOURCE_MAP, kind_changed, case_results)
    assert "unclassified-triplet:stop_request:QUOTA-RESUME-DIFF:user" in errors
    assert any(
        item.startswith(
            "emission-count-mismatch:QUOTA-RESUME-DIFF:stop_request:policy:1:2"
        )
        for item in errors
    )


def test_transition_reachability_inventory_is_unique_and_complete() -> None:
    pairs = tuple((source, target) for source, target, _reachable, _why in EDGE_CLASSIFICATION)
    assert len(pairs) == len(set(pairs))
    assert {source for source, _target in pairs} == {
        "plan",
        "slice",
        "final_review",
        "correction",
    }
    assert {target for _source, target in pairs} == {
        "plan",
        "slice",
        "final_review",
        "correction",
    }
    assert len(pairs) == len(WorkUnitKind) ** 2
    assert all(why.strip() for _source, _target, _reachable, why in EDGE_CLASSIFICATION)
    assert {
        ("plan", "plan"),
        ("plan", "slice"),
        ("slice", "final_review"),
        ("final_review", "correction"),
        ("correction", "correction"),
        ("correction", "final_review"),
    } <= {
        (source, target)
        for source, target, reachable, _why in EDGE_CLASSIFICATION
        if reachable
    }


def test_resume_oracle_is_idempotent_and_fails_closed_on_changed_evidence() -> None:
    state = _slice_state().with_current_step(WorkflowStep.SLICE_COMMIT)
    gated = state.await_user_gate(
        reason=GateReason.UNEXPECTED_FILE,
        detail="HEAD-DRIFT | exact reviewed fingerprint and HEAD required",
        fingerprint="3" * 64,
        paths=("src/runtime.py",),
        resume_step=WorkflowStep.SLICE_COMMIT,
    )
    decided = gated.record_user_gate_decision(
        approved=True,
        fingerprint="3" * 64,
        paths=("src/runtime.py",),
        rationale="Exact evidence reviewed.",
    )
    repeated = decided.await_user_gate(
        reason=GateReason.UNEXPECTED_FILE,
        detail="HEAD-DRIFT | exact reviewed fingerprint and HEAD required",
        fingerprint="3" * 64,
        paths=("src/runtime.py",),
        resume_step=WorkflowStep.SLICE_COMMIT,
    ).record_user_gate_decision(
        approved=True,
        fingerprint="3" * 64,
        paths=("src/runtime.py",),
        rationale="Same exact evidence reviewed again.",
    )
    assert repeated.current_work_unit.gate_decisions == decided.current_work_unit.gate_decisions
    assert len(repeated.current_work_unit.gate_decisions) == 1
    with pytest.raises(WorkflowStateValidationError, match="does not match"):
        gated.record_user_gate_decision(
            approved=True,
            fingerprint="4" * 64,
            paths=("src/runtime.py",),
            rationale="Wrong fingerprint.",
        )

    policy = _slice_state().await_policy_gate(
        reason=GateReason.STOP_REQUEST,
        detail="CODEX-NOT-READY | ready=false does not document a blocker",
    )
    resumed = policy.resume_after_user_decision()
    assert resumed.current_work_unit.round_number == 2
    assert resumed.current_work_unit.gate.status is GateStatus.CLEAR

    bootstrap = _slice_state().await_bootstrap_resume(
        detail="PROVIDER-INPUT-BUDGET | request exceeds the bound limit",
        fingerprint="5" * 64,
    )
    assert bootstrap.await_bootstrap_resume(
        detail="PROVIDER-INPUT-BUDGET | request exceeds the bound limit",
        fingerprint="5" * 64,
    ) is bootstrap
    restored = bootstrap.resume_after_invocation_halt()
    assert restored.current_step is WorkflowStep.CODEX_IMPLEMENTATION
    assert restored.current_work_unit.round_number == 1


@pytest.mark.parametrize(
    "source",
    (
        """
def emit(state):
    return state.await_user_gate(
        reason=GateReason.UNEXPECTED_FILE,
        detail="ZZZ-PROBE | new static gate",
        fingerprint="a" * 64,
    )
""",
        """
def emit(state, error):
    code = "PROVIDER-INPUT-BUDGET"
    if error:
        code = "ZZZ-DYNAMIC"
    return state.await_bootstrap_resume(
        detail=f"{code} | provider input failed",
        fingerprint="a" * 64,
    )
""",
        """
def emit():
    return GateRecord(
        status=GateStatus.AWAITING_USER_DECISION,
        reason=GateReason.UNEXPECTED_FILE,
        detail="ZZZ-DIRECT | direct constructor",
    )
""",
    ),
)
def test_gate_guard_mutations_are_detected(source: str) -> None:
    trees = {"mutation.py": ast.parse(source)}
    assert _unknown_gate_prefixes(trees)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    _git(root, "init", "-b", "feature/transition-matrix")
    _git(root, "config", "user.name", "Transition Matrix")
    _git(root, "config", "user.email", "matrix@example.invalid")
    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(root, "add", "seed.txt")
    _git(root, "commit", "-m", "seed")
    return root


def _bind_record_authoritative_fixture(
    driver: ProductionWorkflowDriver,
    state: WorkflowState,
    *,
    findings: tuple[FindingRecord, ...] = (),
) -> WorkflowState:
    """Seed a synthetic endpoint with the authority production would create.

    These matrix fixtures deliberately jump across provider and Git execution.
    Completed Slices therefore need explicit validation, review, and commit
    records before the resulting prefix can be projected.
    """
    driver.active_state = state
    driver._bind_artifact_store(state)
    driver._persist_structured_baseline(state)
    bridge = driver._artifact_bridge
    assert bridge is not None
    existing_commit_slices = {
        record.logical_id.split("-", 2)[1]
        for record in bridge.store.load_chain()
        if isinstance(record.payload, BindingPayload)
        and record.payload.binding_kind == "commit"
        and record.logical_id.startswith("commit-")
    }
    for slice_record in state.slices:
        if (
            slice_record.commit_ref is None
            or str(slice_record.slice_id) in existing_commit_slices
        ):
            continue
        slice_unit = next(
            unit
            for unit in reversed(state.work_units)
            if unit.slice_id == slice_record.slice_id
            and unit.kind in {WorkUnitKind.SLICE, WorkUnitKind.CORRECTION}
        )
        fingerprint = slice_record.start_fingerprint or "e" * 64
        if not any(
            isinstance(record.payload, (WorkUnitPayload, CorrectionWorkUnitPayload))
            and record.logical_id == f"work-unit-{slice_unit.work_unit_id}"
            for record in bridge.store.load_chain()
        ):
            work_unit_payload = (
                CorrectionWorkUnitPayload(
                    str(slice_record.slice_id),
                    slice_unit.round_number,
                    slice_record.scope_paths,
                    slice_unit.open_findings,
                )
                if slice_unit.kind is WorkUnitKind.CORRECTION
                else WorkUnitPayload(
                    str(slice_record.slice_id),
                    slice_unit.round_number,
                    slice_record.scope_paths,
                )
            )
            bridge.append(
                work_unit_payload,
                logical_id=f"work-unit-{slice_unit.work_unit_id}",
                idempotency_key=(
                    f"matrix-work-unit:{slice_unit.work_unit_id}:"
                    f"{slice_unit.round_number}"
                ),
                fingerprint_sha256=state.task_digest or "b" * 64,
                fingerprint_kind=FingerprintKind.CONTRACT,
            )
        attestation = append_validation_authority(
            bridge,
            ValidationAttestationPayload(
                (
                    ValidationResult(
                        CommandSpec("pytest", ("python3", "-m", "pytest")),
                        "pass",
                        0,
                        "e" * 64,
                    ),
                ),
                Role.ORCHESTRATOR,
                "e" * 64,
                "ar1-" + "0" * 64,
            ),
            logical_id=f"matrix-validation-{slice_record.slice_id}",
            idempotency_key=f"matrix-validation:{slice_record.slice_id}",
            fingerprint_sha256=fingerprint,
        )
        review = append_provider_decision_authority(
            bridge,
            ReviewPayload(
                Role.CLAUDE,
                str(slice_unit.work_unit_id),
                "approved",
                (),
                None,
                "native-claude-review-v2",
                "native-review-request-" + hashlib.sha256(
                    f"{state.run_id}:{slice_record.slice_id}".encode("utf-8")
                ).hexdigest(),
                "d" * 64,
                review_evidence=ReviewEvidencePayload(
                    "synthetic matrix commit authority",
                    "the fixture skips the real Git transaction",
                    "the commit loses its review or attestation binding",
                ),
                pre_mortem="The synthetic binding could target the wrong Slice.",
            ),
            logical_id=f"matrix-review-{slice_record.slice_id}",
            idempotency_key=f"matrix-review:{slice_record.slice_id}",
            fingerprint_sha256=fingerprint,
            operation=WorkflowStep.CLAUDE_SLICE_REVIEW.value,
        )
        bridge.append(
            BindingPayload(
                "commit",
                slice_record.commit_ref,
                attestation.record_id,
                (review.record_id,),
            ),
            logical_id=(
                f"commit-{slice_record.slice_id}-"
                f"{slice_record.commit_ref[:12]}"
            ),
            idempotency_key=(
                f"matrix-commit:{slice_record.slice_id}:"
                f"{slice_record.commit_ref}"
            ),
            fingerprint_sha256=fingerprint,
        )
    for finding in findings:
        opening = (
            replace(finding, status=FindingStatus.OPEN, status_rationale=None)
            if finding.status is FindingStatus.CLOSED
            else finding
        )
        _append_finding(
            bridge,
            opening,
            work_unit_id=state.current_work_unit_id,
        )
        if finding.status is FindingStatus.CLOSED:
            _append_finding(
                bridge,
                finding,
                work_unit_id=state.current_work_unit_id,
                action="status_changed",
                actor=AgentRole.CLAUDE,
                rationale=finding.status_rationale,
            )
    projected = resolve_resume_state(driver.root, state.run_id).state
    driver.active_state = projected
    return projected


def _driver_state(root: Path) -> tuple[ProductionWorkflowDriver, WorkflowState]:
    head = _git(root, "rev-parse", "HEAD")
    task = root / "task.md"
    task.write_text("transition matrix\n", encoding="utf-8")
    state = init_workflow_state(
        run_id="transition-ledger",
        task_file=str(task),
        branch="feature/transition-matrix",
        branch_base=head,
        first_slice_start_commit=head,
        slice_count=1,
        task_digest=hashlib.sha256(task.read_bytes()).hexdigest(),
        task_scope_patterns=("src/runtime.py",),
        target_branch="feature/transition-matrix",
        protocol_binding=ProtocolBinding(
            ProtocolMode.STRUCTURED_V2,
            "2",
            claude_review_transport="native-claude-review-v2",
            codex_result_transport="native-codex-v2",
        ),
    ).bind_slice_plan(
        (PlannedSlice(1, "implementation", ("src/runtime.py",)),),
        first_start_commit=head,
    )
    state = (
        state.complete_current_work_unit()
        .start_work_unit(
            slice_id=1,
            kind=WorkUnitKind.SLICE,
            step=WorkflowStep.CODEX_IMPLEMENTATION,
        )
        .bind_current_slice_git_boundary(
            start_commit=head,
            scope_paths=("src/runtime.py",),
            start_fingerprint="1" * 64,
        )
        .complete_current_slice(commit_ref=head)
        .start_final_review_work_unit()
    )
    driver = ProductionWorkflowDriver(
        repository_root=root,
        state_file=root / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=root),
        allowed_roots=(root,),
    )
    return driver, _bind_record_authoritative_fixture(driver, state)


def _append_finding(
    bridge: ArtifactBridge,
    finding: FindingRecord,
    *,
    work_unit_id: int,
    action: str = "opened",
    actor: AgentRole | None = None,
    rationale: str | None = None,
) -> None:
    existing = tuple(
        record
        for record in bridge.store.load_chain()
        if isinstance(record.payload, FindingTransitionPayload)
        and record.payload.finding_id == finding.finding_id
    )
    bridge.append(
        orchestrator.finding_payload(
            finding,
            work_unit_id=work_unit_id,
            action=action,
            actor=actor,
            rationale=rationale,
        ),
        logical_id=f"finding-{finding.finding_id}",
        idempotency_key=(
            f"matrix:{finding.finding_id}:{action}:{work_unit_id}:{len(existing) + 1}"
        ),
        fingerprint_sha256="9" * 64,
    )


def _append_completed_provider_attempt(
    bridge: ArtifactBridge,
    *,
    work_unit_id: int,
) -> None:
    """Persist one real, completed provider call for the transition fixture."""

    measurement = bridge.append(
        ProviderInputMeasurementPayload(
            Role.CLAUDE,
            Role.CLAUDE,
            "claude_transition_matrix_review",
            str(work_unit_id),
            "9" * 64,
            "8" * 64,
            "7" * 64,
            "6" * 64,
            (ProviderInputComponentPayload("matrix_prompt", 6, 6),),
            6,
            6,
            100,
            100,
            None,
            None,
            None,
            100,
            100,
            True,
            (),
            0,
            0,
            "matrix_prompt",
        ),
        logical_id="matrix-provider-measurement",
        idempotency_key="matrix-provider-measurement",
        fingerprint_sha256="9" * 64,
    )
    started = bridge.start_provider_attempt(
        measurement_record=measurement,
        binding_fingerprint="9" * 64,
        work_unit_id=work_unit_id,
        model="sonnet",
        effort="high",
    )
    bridge.finish_provider_attempt(
        started,
        duration_seconds=1.0,
        failure_kind=None,
        usage=None,
    )


def _transition_evidence(
    tmp_path: Path,
    case_id: str,
    state: WorkflowState,
    current_findings: tuple[FindingRecord, ...],
    *,
    historical_findings: tuple[FindingRecord, ...] = (),
    carry_forward: bool = False,
    persist_checkpoint: bool = True,
) -> TransitionEvidence:
    """Persist one endpoint and read every evidence column from production stores."""

    case_root = tmp_path / case_id
    case_root.mkdir(parents=True)
    root = _repository(case_root)
    task = root / "task.md"
    task.write_text("transition matrix\n", encoding="utf-8")
    state = replace(state, task_file=str(task))
    state_file = root / ".orchestrator" / "state.json"
    driver = ProductionWorkflowDriver(
        repository_root=root,
        state_file=state_file,
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=root),
        allowed_roots=(root,),
    )
    state = _bind_record_authoritative_fixture(
        driver,
        state,
        findings=(*current_findings, *historical_findings),
    )
    bridge = driver._artifact_bridge
    assert bridge is not None

    if case_id in PROVIDER_ATTEMPT_CASES:
        _append_completed_provider_attempt(
            bridge,
            work_unit_id=state.current_work_unit_id,
        )

    projected_findings = (*current_findings, *historical_findings)
    if carry_forward:
        projected_findings = driver.carry_forward_native_findings(
            state, current_findings
        )
    history = WorkflowHistory(
        state.current_work_unit_id, findings=projected_findings
    )
    checkpoint_path = None
    if persist_checkpoint:
        resolution = resolve_resume_state(root, state.run_id)
        write_workflow_state_projection(
            state_file,
            resolution,
            allowed_roots=(root,),
        )
        checkpoint_root = root / ".orchestrator" / "checkpoints" / state.run_id
        checkpoint_path = write_workflow_projection_checkpoint(
            checkpoint_root,
            resolution,
            allowed_roots=(root,),
        )

    replay = replay_artifacts(bridge.store.load_chain(), state.run_id)
    finding_records = tuple(
        record
        for record in replay.records
        if isinstance(record.payload, FindingTransitionPayload)
    )
    external_calls = sum(
        isinstance(record.payload, ProviderAttemptPayload)
        and record.payload.phase == "started"
        for record in replay.records
    )
    return TransitionEvidence(
        ledger=_ledger_literal(replay_findings(replay)),
        record_count=len(finding_records),
        mirror_count=len(projected_findings),
        external_calls=external_calls,
        checkpoint_state=(
            "persisted"
            if state_file.is_file()
            and checkpoint_path is not None
            and checkpoint_path.is_file()
            else "missing"
        ),
    )
def _ledger_case(
    tmp_path: Path,
) -> tuple[
    ProductionWorkflowDriver,
    WorkflowState,
    tuple[FindingRecord, ...],
    tuple[FindingRecord, ...],
]:
    root = _repository(tmp_path)
    driver, final_state = _driver_state(root)
    bridge = driver._artifact_bridge
    assert bridge is not None

    c01 = _finding("C-01", FindingClass.BLOCKER, FindingStatus.OPEN)
    c02_open = _finding("C-02", FindingClass.OBSERVATION, FindingStatus.OPEN)
    c02_closed = replace(
        c02_open,
        status=FindingStatus.CLOSED,
        status_rationale="Verified closed.",
    )
    historical = _finding("C-99", FindingClass.OBSERVATION, FindingStatus.OPEN)
    for finding in (c01, c02_open, historical):
        _append_finding(
            bridge,
            finding,
            work_unit_id=final_state.current_work_unit_id,
        )
    _append_finding(
        bridge,
        c02_closed,
        work_unit_id=final_state.current_work_unit_id,
        action="status_changed",
        actor=AgentRole.CLAUDE,
        rationale="Verified closed.",
    )

    correction = final_state.complete_current_work_unit().start_correction_work_unit(
        start_commit=_git(root, "rev-parse", "HEAD"),
        scope_paths=("src/runtime.py",),
        start_fingerprint="2" * 64,
        finding_ids=("C-01", "C-02"),
    )
    correction = _bind_record_authoritative_fixture(driver, correction)
    c03 = _finding(
        "C-03", FindingClass.BLOCKER, FindingStatus.OPEN, round_number=2
    )
    _append_finding(
        bridge,
        c03,
        work_unit_id=correction.current_work_unit_id,
    )
    round_two = correction.record_review_denial(
        reviewer=Reviewer.CLAUDE,
        open_findings=("C-01", "C-03"),
        return_step=WorkflowStep.CODEX_FINAL_CORRECTION,
    )
    round_two = _bind_record_authoritative_fixture(driver, round_two)
    correction_mirror = (c01, c02_closed, c03)
    full_ledger = (c01, c02_closed, c03, historical)
    return driver, round_two, correction_mirror, full_ledger


def test_record_replay_matrix_has_independent_literal_oracle_and_failure_windows(
    tmp_path: Path,
) -> None:
    driver, state, correction_mirror, full_ledger = _ledger_case(tmp_path)
    bridge = driver._artifact_bridge
    assert bridge is not None
    replay = replay_artifacts(bridge.store.load_chain(), state.run_id)
    finding_record_count = sum(
        isinstance(record.payload, FindingTransitionPayload)
        for record in replay.records
    )
    assert finding_record_count == 5

    assert _ledger_literal(replay_findings(replay)) == (
        "C-01:open:blocker",
        "C-02:closed:observation",
        "C-03:open:blocker",
        "C-99:open:observation",
    )
    assert _ledger_literal(
        driver.authoritative_native_findings(state, correction_mirror)
    ) == (
        "C-01:open:blocker",
        "C-02:closed:observation",
        "C-03:open:blocker",
    )
    assert _ledger_literal(
        driver.carry_forward_native_findings(state, correction_mirror)
    ) == (
        "C-01:open:blocker",
        "C-02:closed:observation",
        "C-03:open:blocker",
        "C-99:open:observation",
    )

    # Rebinding a manipulated projection is cache-only: it neither duplicates
    # nor mutates finding authority and it never starts another provider call.
    mirrored = replace(
        state,
        runtime_history={
            "current": WorkflowHistory(
                state.current_work_unit_id, findings=full_ledger
            ).to_dict(),
            "archive": [],
        },
    )
    driver.bind_work_unit(mirrored)
    after_checkpoint = replay_artifacts(bridge.store.load_chain(), state.run_id)
    assert sum(
        isinstance(record.payload, FindingTransitionPayload)
        for record in after_checkpoint.records
    ) == 5
    assert _ledger_literal(
        driver.authoritative_native_findings(mirrored, full_ledger)
    ) == (
        "C-01:open:blocker",
        "C-02:closed:observation",
        "C-03:open:blocker",
    )

    # Projection arguments cannot add or hide findings: records stay decisive.
    missing_record = _finding("C-04", FindingClass.BLOCKER, FindingStatus.OPEN)
    assert _ledger_literal(
        driver.carry_forward_native_findings(
            state, (*correction_mirror, missing_record)
        )
    ) == _ledger_literal(full_ledger)
    assert _ledger_literal(
        driver.authoritative_native_findings(state, correction_mirror[:-1])
    ) == _ledger_literal(correction_mirror)

    # A damaged lineage fails at replay rather than being healed by the mirror.
    orphan = ArtifactBridge(ArtifactStore(driver.root, "orphan-transition"))
    orphan.append(
        RunIdentityPayload("task.md", "feature/test", "b" * 40, "b" * 40, "IMPLEMENT", None),
        logical_id="run-identity",
        idempotency_key="run-identity",
        fingerprint_sha256="9" * 64,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    orphan.append(
        RunProfilePayload(
            RoleProfilePayload("implementer-model", "medium"),
            RoleProfilePayload("reviewer-model", "high"),
        ),
        logical_id="run-profile",
        idempotency_key="run-profile",
        fingerprint_sha256="9" * 64,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    orphan_finding = replace(
        correction_mirror[0],
        status=FindingStatus.CLOSED,
        status_rationale="Closed without an opening record.",
    )
    _append_finding(
        orphan,
        orphan_finding,
        work_unit_id=state.current_work_unit_id,
        action="status_changed",
        actor=AgentRole.CLAUDE,
        rationale="Closed without an opening record.",
    )
    with pytest.raises(ArtifactReplayError, match="RECORD-REFERENCE-MISSING"):
        replay_findings(
            replay_artifacts(orphan.store.load_chain(), "orphan-transition")
        )

    # Keep the independently expected full ledger visibly bound to this case.
    assert _ledger_literal(full_ledger) == (
        "C-01:open:blocker",
        "C-02:closed:observation",
        "C-03:open:blocker",
        "C-99:open:observation",
    )


def test_replay_and_carry_forward_mutations_turn_matrix_cases_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_reduce_findings = orchestrator.reduce_findings

    def empty_ledger(replay):  # type: ignore[no-untyped-def]
        reduction = real_reduce_findings(replay)
        return replace(
            reduction,
            ledger=replace(reduction.ledger, findings=()),
        )

    monkeypatch.setattr(orchestrator, "reduce_findings", empty_ledger)
    with pytest.raises(AssertionError):
        _exercise_transition_oracle(tmp_path / "replay-empty")

    monkeypatch.undo()
    monkeypatch.setattr(
        ProductionWorkflowDriver,
        "carry_forward_native_findings",
        lambda _self, _state, current_findings: current_findings,
    )
    with pytest.raises(AssertionError):
        _exercise_transition_oracle(tmp_path / "carry-through")
