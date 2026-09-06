from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

from contracts import (
    AgentRole,
    ApprovalMarker,
    CodexStepContract,
    ReadinessMarker,
    StepContract,
    ValidationAttestation,
    ValidationRecord,
    ValidationStatus,
)
from native_codex_contract import NativeCodexRequestKind
from workflow import (
    EvidenceKind,
    WorkflowChanges,
    WorkflowContext,
    WorkflowExecutionError,
    WorkflowHistory,
)
import workflow_requests
from workflow_state import WorkflowStep, init_workflow_state


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PRE_CUT_CODEX_REQUEST_SHA256 = (
    "2c55523800b88fddd4b58dd4cd8be72837d98f636c7a7c9c65210e1a3693dade"
)
PRE_CUT_REVIEW_REQUEST_SHA256 = (
    "20b26dedb26a8d3affe2657458876531dfc46c3b95aa821ef356958c1ecbee0d"
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


def _codex_bundle() -> workflow_requests.NativeCodexRequestBundle:
    state = init_workflow_state(
        run_id="b31-request-builder",
        task_file="/repo/inbox/backlog/00-b31.md",
        branch="feature/backlog-followups",
        branch_base="a" * 40,
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
        context=_context(),
        history=WorkflowHistory(state.current_work_unit_id),
        contract=contract,
        request_kind=NativeCodexRequestKind.PLAN,
        execution_error=WorkflowExecutionError,
    )


def _review_bundle() -> workflow_requests.NativeReviewRequestBundle:
    state = init_workflow_state(
        run_id="b31-request-review",
        task_file="/repo/inbox/backlog/00-b31.md",
        branch="feature/backlog-followups",
        branch_base="a" * 40,
        slice_count=1,
        timestamp="2026-09-02T10:00:00+00:00",
    ).with_current_step(WorkflowStep.CLAUDE_SLICE_REVIEW)
    changes = WorkflowChanges(
        start_commit="a" * 40,
        fingerprint="c" * 64,
        paths=("src/workflow_requests.py", "tests/test_workflow_requests.py"),
        full_diff="diff --git a/src/workflow.py b/src/workflow.py\n-old\n+new\n",
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
        approval_marker=ApprovalMarker.SLICE,
        slice_id="01",
        round_number=1,
        review_fingerprint=changes.fingerprint,
        validation_attestation=attestation,
        test_changes_approved=True,
    )
    return workflow_requests.native_review_request(
        state=state,
        context=_context(),
        history=WorkflowHistory(state.current_work_unit_id),
        contract=contract,
        changes=changes,
        evidence_kind=EvidenceKind.FULL_SLICE,
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
    assert declarations == {"native_codex_request", "native_review_request"}
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
    assert len(codex_calls) == 3
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


def test_canonical_requests_match_the_pre_cut_bytes() -> None:
    assert _canonical_digest(_codex_bundle().canonical_json) == (
        PRE_CUT_CODEX_REQUEST_SHA256
    )
    assert _canonical_digest(_review_bundle().canonical_json) == (
        PRE_CUT_REVIEW_REQUEST_SHA256
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
