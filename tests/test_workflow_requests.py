from __future__ import annotations

import ast
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from contracts import (
    AgentRole,
    ApprovalMarker,
    CodexStepContract,
    FindingClass,
    FindingOrigin,
    FindingRecord,
    FindingStatus,
    ReadinessMarker,
    StepContract,
    ValidationAttestation,
    ValidationRecord,
    ValidationStatus,
    PlannedSlice,
)
from artifact_models import technical_text_evidence
from gates import StopRule
from native_codex_contract import NativeCodexRequestKind
from orchestrator_diagnostics import OrchestratorDiagnostic
from workflow import (
    EvidenceKind,
    WorkflowChanges,
    WorkflowContext,
    WorkflowExecutionError,
    WorkflowHistory,
)
import workflow_requests
from task_contract import TaskMode, parse_task_contract
from workflow_state import (
    AgentFailureKind,
    InvocationFailureRecord,
    WorkflowState,
    WorkflowStep,
    init_workflow_state,
)


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PRE_CUT_CODEX_REQUEST_SHA256 = (
    "ae33d70f3575b9d63db88e899ef297747fc79063b6d4e844fdb316da85c76cdc"
)
PRE_CUT_REVIEW_REQUEST_SHA256 = (
    "1b0dc0c9479a690055eea95445dd8e6813096020085be3c78d6cfac352085b26"
)


def _canonical_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _context() -> WorkflowContext:
    return WorkflowContext(
        assignment="Implement the bound request-builder Slice.",
        distilled_plan="Preserve every provider request field and ordering rule.",
        slice_summary="Extract the two pure request builders.",
        current_branch="feature/backlog-followups",
        expected_test_files=("tests/test_workflow_requests.py",),
        test_changes_approved=True,
    )


def _codex_bundle(
    *, context: WorkflowContext | None = None
) -> workflow_requests.NativeCodexRequestBundle:
    state = init_workflow_state(
        run_id="b31-request-builder",
        task_file="/repo/inbox/backlog/00-b31.md",
        branch="feature/backlog-followups",
        branch_base="a" * 40,
        first_slice_start_commit="a" * 40,
        slice_count=1,
        task_digest="b" * 64,
        task_scope_patterns=("docs/internal/b31-plan.md", "src/workflow_requests.py"),
        target_branch="feature/backlog-followups",
        timestamp="2026-09-02T10:00:00+00:00",
    )
    contract = CodexStepContract(
        name="b31-plan",
        readiness_marker=ReadinessMarker.PLAN,
        slice_id="01",
        round_number=1,
        require_slice_plan=True,
        plan_artifact_path="docs/internal/b31-plan.md",
    )
    return workflow_requests.native_codex_request(
        state=state,
        context=_context() if context is None else context,
        history=WorkflowHistory(state.current_work_unit_id),
        contract=contract,
        request_kind=NativeCodexRequestKind.PLAN,
        execution_error=WorkflowExecutionError,
    )


def _review_bundle(
    *,
    context: WorkflowContext | None = None,
    review_diff: str | None = None,
    evidence_kind: EvidenceKind = EvidenceKind.FULL_SLICE,
    findings: tuple[FindingRecord, ...] = (),
    bound_open_finding_ids: tuple[str, ...] = (),
    state: WorkflowState | None = None,
    approval_marker: ApprovalMarker = ApprovalMarker.SLICE,
) -> workflow_requests.NativeReviewRequestBundle:
    state = state or init_workflow_state(
        run_id="b31-request-review",
        task_file="/repo/inbox/backlog/00-b31.md",
        branch="feature/backlog-followups",
        branch_base="a" * 40,
        first_slice_start_commit="a" * 40,
        slice_count=1,
        timestamp="2026-09-02T10:00:00+00:00",
    ).with_current_step(WorkflowStep.CLAUDE_SLICE_REVIEW)
    if bound_open_finding_ids:
        state = replace(
            state,
            work_units=tuple(
                replace(item, open_findings=bound_open_finding_ids)
                if item.work_unit_id == state.current_work_unit_id
                else item
                for item in state.work_units
            ),
        )
    changes = WorkflowChanges(
        start_commit="a" * 40,
        fingerprint="c" * 64,
        paths=("src/workflow_requests.py", "tests/test_workflow_requests.py"),
        full_diff=(
            review_diff
            if review_diff is not None
            else "diff --git a/src/workflow.py b/src/workflow.py\n-old\n+new\n"
        ),
    )
    command = "python3 -m pytest tests/ -v -m not crash_harness"
    attestation = ValidationAttestation(
        attestation_id="validation-b31-request-review",
        diff_fingerprint=changes.fingerprint,
        expected_commands=(command,),
        records=(ValidationRecord(ValidationStatus.PASS, command, 0),),
        output_digest="d" * 64,
        summary="B31 request tests passed.",
    )
    contract = StepContract(
        name="b31-slice-review",
        reviewer=AgentRole.CLAUDE,
        approval_marker=approval_marker,
        slice_id="01",
        round_number=1,
        review_fingerprint=changes.fingerprint,
        validation_attestation=attestation,
        test_changes_approved=True,
        existing_finding_ids=tuple(item.finding_id for item in findings),
        request_sequence=state.current_work_unit.request_sequence,
    )
    return workflow_requests.native_review_request(
        state=state,
        context=_context() if context is None else context,
        history=WorkflowHistory(state.current_work_unit_id, findings=findings),
        contract=contract,
        changes=changes,
        evidence_kind=evidence_kind,
        review_diff=changes.full_diff,
        review_packet=None,
        expected_test_files=("tests/test_workflow_requests.py",),
        execution_error=WorkflowExecutionError,
        full_branch_evidence_kind=EvidenceKind.FULL_BRANCH,
    )

def test_request_builders_are_free_functions_with_one_way_imports() -> None:
    tree = ast.parse((SRC / "workflow_requests.py").read_text(encoding="utf-8"))
    declarations = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert declarations == {
        "_native_implementer_retry_feedback",
        "_native_review_acceptance_criteria",
        "_native_review_retry_feedback",
        "_review_request_finding_inputs",
        "native_codex_request",
        "native_review_request",
    }
    assert not any(isinstance(node, ast.ClassDef) for node in tree.body)
    assert not any(
        isinstance(node, ast.ImportFrom) and node.module == "workflow"
        or isinstance(node, ast.Import)
        and any(alias.name == "workflow" for alias in node.names)
        for node in ast.walk(tree)
    )
    importers = []
    for path in sorted(SRC.glob("*.py")):
        candidate = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            isinstance(node, ast.ImportFrom) and node.module == "workflow_requests"
            or isinstance(node, ast.Import)
            and any(alias.name == "workflow_requests" for alias in node.names)
            for node in ast.walk(candidate)
        ):
            importers.append(path.relative_to(ROOT).as_posix())
    assert importers == ["src/workflow.py"]

    workflow_tree = ast.parse((SRC / "workflow.py").read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(workflow_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "workflow_requests"
    ]
    codex_calls = [node for node in calls if node.func.attr == "native_codex_request"]
    review_calls = [node for node in calls if node.func.attr == "native_review_request"]
    assert len(codex_calls) == 2
    assert len(review_calls) == 1
    assert all(
        any(
            keyword.arg == "execution_error"
            and isinstance(keyword.value, ast.Name)
            and keyword.value.id == "WorkflowExecutionError"
            for keyword in node.keywords
        )
        for node in calls
    )
    assert any(
        keyword.arg == "full_branch_evidence_kind"
        and isinstance(keyword.value, ast.Attribute)
        and isinstance(keyword.value.value, ast.Name)
        and keyword.value.value.id == "EvidenceKind"
        and keyword.value.attr == "FULL_BRANCH"
        for keyword in review_calls[0].keywords
    )


def test_non_correction_requests_still_reject_a_missing_slice_summary() -> None:
    context = replace(_context(), slice_summary="")

    with pytest.raises(
        WorkflowExecutionError,
        match="non-correction implementer request requires a current-slice summary",
    ):
        _codex_bundle(context=context)
    with pytest.raises(
        WorkflowExecutionError,
        match="non-correction review requires a current-slice summary",
    ):
        _review_bundle(context=context)


def test_canonical_requests_match_the_cutover_bytes() -> None:
    assert _canonical_digest(_codex_bundle().canonical_json) == (
        PRE_CUT_CODEX_REQUEST_SHA256
    )
    assert _canonical_digest(_review_bundle().canonical_json) == (
        PRE_CUT_REVIEW_REQUEST_SHA256
    )


def test_slice_review_announces_the_exact_exit_decision_source_union() -> None:
    finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.FINDING,
        status=FindingStatus.OPEN,
        summary="Current Slice finding",
        acceptance_test="The finding receives a valid exit decision.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
    )

    empty = _review_bundle()
    populated = _review_bundle(
        findings=(finding,),
        bound_open_finding_ids=("C-01", "C-02"),
    )

    assert empty.document["review_contract"][
        "slice_commit_decision_finding_ids"
    ] == []
    assert populated.document["review_contract"][
        "slice_commit_decision_finding_ids"
    ] == ["C-01", "C-02"]






def test_oversized_branch_diff_is_replaced_by_an_explicit_digest_bound_notice() -> None:
    sentinel = "complete-diff-sentinel-"
    review_diff = sentinel + ("ä" * workflow_requests.FULL_BRANCH_DIFF_EVIDENCE_CEILING_CHARS)

    bundle = _review_bundle(
        review_diff=review_diff,
        evidence_kind=EvidenceKind.FULL_BRANCH,
    )
    item = next(
        item
        for item in bundle.document["evidence_manifest"]
        if item["evidence_id"] == "review-diff"
    )
    notice = json.loads(item["content"])

    assert item["kind"] == "provider_input_boundary_notice"
    assert notice == {
        "available_evidence": "The complete current repository snapshot is mounted read-only.",
        "boundary": "provider_input",
        "changed_paths": "See the request-level authorized_paths array.",
        "evidence_complete": False,
        "omitted_chars": len(review_diff),
        "omitted_evidence": "full_branch_diff",
        "omitted_sha256": hashlib.sha256(review_diff.encode("utf-8")).hexdigest(),
        "omitted_utf8_bytes": len(review_diff.encode("utf-8")),
        "repository_fingerprint": "c" * 64,
        "required_reviewer_action": (
            "Inspect the current files needed for every review dimension with Read. "
            "Do not infer that the omitted full diff was supplied. Deny with a "
            "BLOCKER if a safe verdict requires unavailable baseline content."
        ),
    }
    assert sentinel not in bundle.canonical_json
    assert len(bundle.canonical_json) < 100_000
    assert any(
        "Never treat the request as complete diff evidence" in criterion
        for criterion in bundle.document["acceptance_criteria"]
    )


def test_canonical_request_anchor_detects_omitted_and_reordered_fields() -> None:
    bundle = _codex_bundle()
    original_digest = _canonical_digest(bundle.canonical_json)
    document = json.loads(bundle.canonical_json)
    roundtrip = json.dumps(
        document, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    assert roundtrip.encode("utf-8") == bundle.canonical_json.encode("utf-8")

    omitted = dict(document)
    omitted.pop("assignment")
    omitted_bytes = json.dumps(
        omitted, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    assert _canonical_digest(omitted_bytes) != original_digest

    reordered = dict(document)
    reordered["authorized_paths"] = list(reversed(document["authorized_paths"]))
    reordered_bytes = json.dumps(
        reordered, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    assert _canonical_digest(reordered_bytes) != original_digest


def test_b78_codex_request_projects_the_exact_runtime_stop_rule_set() -> None:
    context = replace(
        _context(),
        stop_rules=(StopRule("DOMAIN-001", "A configured domain decision is required."),),
    )
    bundle = _codex_bundle(context=context)
    rule_id = bundle.provider_response_schema["$defs"]["stop_result"][
        "properties"
    ]["rule_id"]

    assert rule_id["enum"] == sorted(context.known_stop_rule_ids)
    assert "DOMAIN-001" in rule_id["enum"]
    assert "DOMAIN-001 | A configured domain decision is required." in (
        bundle.document["work_context"]
    )


def test_plan_review_request_binds_whether_a_repository_plan_artifact_exists() -> None:
    informal_scoped_text = """# Documentation end state

TARGET_BRANCH: feature/documentation-consistency-overhaul
TASK_SCOPE: README.md, Quickstart.md, workflow.puml, docs/reference/architecture-and-domain-concept.md, docs/reference/market-comparison.md, docs/internal/ORCHESTRATOR_ROADMAP_PHASE_2_PLUS.md

Bring the six explicitly scoped documents to one consistent end state.
"""
    informal_scoped = parse_task_contract(
        informal_scoped_text,
        source_name="canary-dokumentation-endzustand.md",
    )
    assert informal_scoped.mode is TaskMode.IMPLEMENT
    assert informal_scoped.work_plan_path is None

    def plan_review_request(
        *,
        execution_mode: TaskMode,
        scope_paths: tuple[str, ...],
        work_plan_path: str | None,
        assignment: str,
    ) -> dict[str, object]:
        fingerprint = "e" * 64
        change_paths = tuple(sorted(scope_paths))
        state = init_workflow_state(
            run_id=f"b72-{execution_mode.value.lower()}",
            task_file="/repo/inbox/b72.md",
            branch="feature/backlog-followups",
            branch_base="a" * 40,
            first_slice_start_commit="a" * 40,
            slice_count=1,
            task_digest="b" * 64,
            task_scope_patterns=scope_paths,
            target_branch="feature/backlog-followups",
            execution_mode=execution_mode.value,
            work_plan_path=work_plan_path,
            timestamp="2026-09-06T10:00:00+00:00",
        ).with_current_step(WorkflowStep.CLAUDE_PLAN_REVIEW)
        context = replace(
            _context(),
            assignment=assignment,
            slice_summary="Review the request-bound executable Slice plan.",
            plan_only=execution_mode is TaskMode.PLAN_ONLY,
            task_scope_patterns=scope_paths,
            work_plan_path=work_plan_path,
        )
        changes = WorkflowChanges(
            start_commit="a" * 40,
            fingerprint=fingerprint,
            paths=change_paths,
            full_diff="request-bound planning evidence",
        )
        contract = StepContract(
            name="b72-plan-review",
            reviewer=AgentRole.CLAUDE,
            approval_marker=ApprovalMarker.PLAN,
            slice_id="01",
            round_number=1,
            review_fingerprint=fingerprint,
            test_changes_approved=True,
        )
        return workflow_requests.native_review_request(
            state=state,
            context=context,
            history=WorkflowHistory(state.current_work_unit_id),
            contract=contract,
            changes=changes,
            evidence_kind=EvidenceKind.FULL_SLICE,
            review_diff=changes.full_diff,
            review_packet=None,
            expected_test_files=(),
            execution_error=WorkflowExecutionError,
            full_branch_evidence_kind=EvidenceKind.FULL_BRANCH,
        ).document

    direct_request = plan_review_request(
        execution_mode=informal_scoped.mode,
        scope_paths=informal_scoped.scope_patterns,
        work_plan_path=informal_scoped.work_plan_path,
        assignment=informal_scoped_text,
    )
    direct_contract = direct_request["review_contract"]
    assert isinstance(direct_contract, dict)
    assert direct_contract["plan_artifact_path"] is None
    assert any(
        "No repository plan artifact is bound" in criterion
        and "must not require PLAN_ONLY artifact structure" in criterion
        for criterion in direct_request["acceptance_criteria"]
    )
    assert direct_request["authorized_paths"] == sorted(informal_scoped.scope_patterns)
    assert not any(
        path.startswith(".orchestrator/")
        for path in direct_request["authorized_paths"]
    )

    explicit_text = """ORCHESTRATOR_MODE: PLAN_ONLY
WORK_PLAN_PATH: docs/internal/explicit-work-plan.MD
TARGET_BRANCH: feature/backlog-followups
TASK_SCOPE: docs/internal/explicit-work-plan.MD
"""
    explicit = parse_task_contract(explicit_text, source_name="explicit.md")
    explicit_request = plan_review_request(
        execution_mode=explicit.mode,
        scope_paths=explicit.scope_patterns,
        work_plan_path=explicit.work_plan_path,
        assignment=explicit_text,
    )
    explicit_contract = explicit_request["review_contract"]
    assert isinstance(explicit_contract, dict)
    assert explicit_contract["plan_artifact_path"] == explicit.work_plan_path
    assert any(
        "PLAN_ONLY artifact contract is active" in criterion
        and explicit.work_plan_path in criterion
        for criterion in explicit_request["acceptance_criteria"]
    )
