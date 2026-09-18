from __future__ import annotations

import ast
from collections import Counter
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace

import pytest
import workflow_run_setup
import native_finding_decisions
from agent_runtime import QuotaWaitPolicy, TransientRetryPolicy
from artifact_models import FamilyBindingPayload
from contracts import PlannedSlice
from gates import PathClasses
from validation_matrix import ValidationCommand, ValidationMatrix
from task_contract import TaskContract, TaskMode
from workflow import WorkflowContext
from workflow_state import ProtocolBinding, ProtocolMode, WorkUnitKind, init_workflow_state


ROOT = Path(__file__).resolve().parents[1]
SETUP_PATH = ROOT / "src/workflow_run_setup.py"
DRIVER_PATH = ROOT / "src/orchestrator.py"

EXPECTED_INTERNAL_IMPORTS = {
    "artifact_bridge",
    "artifact_models",
    "artifact_replay",
    "artifact_store",
    "finding_reducer",
    "git_service",
    "inbox_watcher",
    "native_finding_decisions",
    "repo_changes",
    "state_io",
    "task_contract",
    "validation_matrix",
    "workflow",
    "workflow_state",
}
EXPECTED_SETUP_FUNCTIONS = {
    "_apply_resumed_agent_profiles",
    "_context",
    "_current_gate_approval",
    "_fresh_state",
    "_initialize_finding_handoff",
    "_new_watch_task_control_paths",
    "_new_watch_task_preserved_paths",
    "_plan_only_step_boundary",
    "_recover_legacy_plan_only_post_gate",
}
EXPECTED_SETUP_EDGES = Counter({("_context", "_plan_only_step_boundary"): 1})
EXPECTED_PRODUCTION_BINDINGS = {
    "apply_resumed_agent_profiles": "_apply_resumed_agent_profiles",
    "context": "_context",
    "current_gate_approval": "_current_gate_approval",
    "fresh_state": "_fresh_state",
    "initialize_finding_handoff": "_initialize_finding_handoff",
    "new_watch_task_control_paths": "_new_watch_task_control_paths",
    "new_watch_task_preserved_paths": "_new_watch_task_preserved_paths",
    "recover_legacy_plan_only_post_gate": "_recover_legacy_plan_only_post_gate",
}


def _tree(path: Path = SETUP_PATH, source: str | None = None) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8") if source is None else source)


def _internal_imports(path: Path) -> set[str]:
    known_modules = {candidate.stem for candidate in (ROOT / "src").glob("*.py")}
    result: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            module = node.module.split(".", 1)[0]
            if module in known_modules:
                result.add(module)
        elif isinstance(node, ast.Import):
            result.update(
                module
                for alias in node.names
                if (module := alias.name.split(".", 1)[0]) in known_modules
            )
    return result


def _function_names(tree: ast.Module) -> set[str]:
    return {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef)
    }


def _setup_edges(source: str | None = None) -> Counter[tuple[str, str]]:
    tree = _tree(source=source)
    functions = {
        node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    return Counter(
        (name, call.func.id)
        for name, function in functions.items()
        for call in ast.walk(function)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id in functions
    )


def _production_bindings() -> dict[str, str]:
    function = next(
        node
        for node in _tree(DRIVER_PATH).body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_production_workflow_dependencies"
    )
    dependency_call = next(
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "ProductionWorkflowDependencies"
    )
    return {
        keyword.arg: ast.unparse(keyword.value)
        for keyword in dependency_call.keywords
        if keyword.arg is not None
    }


def test_run_setup_module_has_complete_inventory_and_one_way_layering() -> None:
    assert _internal_imports(SETUP_PATH) == EXPECTED_INTERNAL_IMPORTS
    assert "orchestrator" not in _internal_imports(SETUP_PATH)
    assert _function_names(_tree()) == EXPECTED_SETUP_FUNCTIONS

    importers = []
    for path in sorted((ROOT / "src").glob("*.py")):
        if path == SETUP_PATH:
            continue
        if any(
            (
                isinstance(node, ast.ImportFrom)
                and node.module == "workflow_run_setup"
            )
            or (
                isinstance(node, ast.Import)
                and any(alias.name == "workflow_run_setup" for alias in node.names)
            )
            for node in ast.walk(_tree(path))
        ):
            importers.append(path.relative_to(ROOT).as_posix())
    assert importers == ["src/orchestrator.py"]

    for path in sorted((ROOT / "src").glob("workflow_*.py")):
        if path == SETUP_PATH:
            continue
        assert "workflow_run_setup" not in _internal_imports(path)


def test_orchestrator_keeps_composition_and_history_responsibilities() -> None:
    driver_functions = _function_names(_tree(DRIVER_PATH))
    setup_functions = _function_names(_tree())

    assert {
        "_bound_task_control_paths",
        "_history",
        "_history_payload",
        "_production_workflow_dependencies",
        "run_pipeline",
    } <= driver_functions
    assert not EXPECTED_SETUP_FUNCTIONS & driver_functions
    assert not {
        "_bound_task_control_paths",
        "_history",
        "_history_payload",
        "_production_workflow_dependencies",
        "run_pipeline",
    } & setup_functions


def test_setup_edge_inventory_fails_closed_on_omission() -> None:
    assert _setup_edges() == EXPECTED_SETUP_EDGES

    source = SETUP_PATH.read_text(encoding="utf-8")
    call = "effective_assignment += _plan_only_step_boundary(state)"
    assert source.count(call) == 1
    mutated = source.replace(call, 'effective_assignment += ""')

    assert _setup_edges(mutated) != EXPECTED_SETUP_EDGES
    assert ("_context", "_plan_only_step_boundary") not in _setup_edges(mutated)


def test_existing_setup_handoffs_and_history_dependency_remain_exact() -> None:
    bindings = _production_bindings()
    assert {
        name: bindings[name] for name in EXPECTED_PRODUCTION_BINDINGS
    } == EXPECTED_PRODUCTION_BINDINGS

    assignments = {
        target.id: node
        for node in _tree(DRIVER_PATH).body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
        and target.id in {"_context", "_initialize_finding_handoff"}
    }
    context_assignment = assignments["_context"]
    assert isinstance(context_assignment.value, ast.Call)
    assert ast.unparse(context_assignment.value.func) == "partial"
    assert [ast.unparse(argument) for argument in context_assignment.value.args] == [
        "_context_unbound"
    ]
    assert {
        keyword.arg: ast.unparse(keyword.value)
        for keyword in context_assignment.value.keywords
    } == {"_managed_slice_scope_pattern": "_managed_slice_scope_pattern"}

    assignment = assignments["_initialize_finding_handoff"]
    assert isinstance(assignment.value, ast.Call)
    assert ast.unparse(assignment.value.func) == "partial"
    assert [ast.unparse(argument) for argument in assignment.value.args] == [
        "_initialize_finding_handoff_unbound"
    ]
    assert {
        keyword.arg: ast.unparse(keyword.value)
        for keyword in assignment.value.keywords
    } == {"_history_payload": "_history_payload"}


def test_context_matches_every_workflow_context_field(
    tmp_path: Path, monkeypatch
) -> None:
    path_classes = PathClasses(
        productive=("src/**",),
        tests=("tests/**",),
        documentation=("docs/**",),
        generated=(".orchestrator/**",),
    )
    base_matrix = ValidationMatrix()
    quota_policy = QuotaWaitPolicy()
    transient_policy = TransientRetryPolicy()
    args = SimpleNamespace(
        repo_config=SimpleNamespace(
            validation=base_matrix,
            paths=path_classes,
            stop_rules=(),
        ),
        test_command="python3 -m pytest 'tests/a b.py'",
        agents_file=str(tmp_path / "missing-AGENTS.md"),
        agents_file_explicit=False,
        test_change_gate=True,
        manual_slice_gate=True,
        retry_incomplete_validation=True,
        retry_failed_validation=False,
        quota_wait_policy=quota_policy,
        transient_retry_policy=transient_policy,
        plan_gate=True,
    )
    state = init_workflow_state(
        run_id="context-field-equality",
        task_file=str(tmp_path / "task.md"),
        branch="feature/context-field-equality",
        branch_base="a" * 40,
        first_slice_start_commit="a" * 40,
        slice_count=1,
        execution_mode="IMPLEMENT",
        task_scope_patterns=("src/core.py",),
        audit_report_path="docs/internal/context-review-12345678.md",
        target_branch="feature/context-field-equality",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        workflow_run_setup,
        "inspect_repository",
        lambda _root: SimpleNamespace(branch="feature/context-field-equality"),
    )

    managed_scope_calls: list[str] = []

    def managed_scope_pattern(path: str) -> str:
        managed_scope_calls.append(path)
        return "docs/internal/slice-context-*.md"

    actual = workflow_run_setup._context(
        args=args,
        assignment="Implement the bounded setup refactor.",
        state=state,
        _managed_slice_scope_pattern=managed_scope_pattern,
    )
    expected_assignment = (
        "Implement the bounded setup refactor."
        "\n\nOrchestrator execution boundary (authoritative):\n"
        "- Mode: IMPLEMENT\n"
        "- Persisted target branch: feature/context-field-equality\n"
        "- Do not create, switch, rename, delete, merge, or publish branches.\n"
        "- Do not stage or commit. Git branch and commit transactions belong only to "
        "the orchestrator and the user.\n"
        "- Every emitted SLICE_PLAN path and every workspace change must remain within "
        "the declared task scope: src/core.py"
        "\n- The orchestrator owns the consolidated audit report and managed audit "
        "blocks below docs/internal. Do not edit managed audit blocks. A Slice report "
        "may be updated only after the orchestrator creates it at implementation start."
    )
    expected = WorkflowContext(
        assignment=expected_assignment,
        distilled_plan=(
            "Follow the ordered, persisted slice plan and exact path allowlists."
        ),
        slice_summary="Plan the requested work.",
        expected_test_files=(),
        test_changes_approved=False,
        test_path_patterns=("tests/**",),
        manual_slice_gate=True,
        approved_anchors=(),
        current_anchors=(),
        path_classes=path_classes,
        stop_rules=(),
        current_branch="feature/context-field-equality",
        validation_matrix=ValidationMatrix(
            default_command=ValidationCommand(
                argv=("python3", "-m", "pytest", "tests/a b.py")
            )
        ),
        red_state_followup_slice=None,
        retry_incomplete_validation=True,
        retry_failed_validation=False,
        quota_wait_policy=quota_policy,
        transient_retry_policy=transient_policy,
        require_slice_plan=state.current_work_unit.kind is WorkUnitKind.PLAN,
        dynamic_test_scope=True,
        plan_gate=True,
        plan_only=False,
        task_scope_patterns=(
            "docs/internal/context-review-12345678.md",
            "docs/internal/slice-context-*.md",
            "src/core.py",
        ),
        work_plan_path=None,
        approved_plan_text=None,
        audit_report_path="docs/internal/context-review-12345678.md",
        current_scope_paths=state.current_slice.scope_paths,
    )

    assert managed_scope_calls == ["docs/internal/context-review-12345678.md"]
    for field in fields(WorkflowContext):
        assert getattr(actual, field.name) == getattr(expected, field.name), field.name


def test_fresh_approved_plan_run_keeps_current_head_as_all_branch_bases_without_family(
    tmp_path: Path, monkeypatch
) -> None:
    head = "a" * 40
    contract = TaskContract(
        digest="b" * 64,
        mode=TaskMode.IMPLEMENT,
        scope_patterns=("docs/internal/plan.md", "src/a.py"),
        target_branch="feature/family-dormancy",
        work_plan_path="docs/internal/plan.md",
        approved_plan_commit=head,
        approved_slices=(PlannedSlice(1, "Implement", ("src/a.py",)),),
    )
    monkeypatch.setattr(
        workflow_run_setup,
        "inspect_repository",
        lambda _root: SimpleNamespace(
            branch="feature/family-dormancy", head=head
        ),
    )
    monkeypatch.setattr(
        workflow_run_setup, "require_committed_file_at_head", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        native_finding_decisions,
        "JOINT_67_68_NATIVE_CONTRACT_CUTOVER",
        False,
    )

    state = workflow_run_setup._fresh_state(
        task_file=tmp_path / "task.md",
        run_id="family-dormancy",
        repository_root=tmp_path,
        task_contract=contract,
    )

    assert state.family_binding is None
    assert state.branch_base == head
    assert state.branch_review_base_commit == head
    assert state.current_slice.start_commit == head


def test_fresh_state_rejects_family_binding_while_joint_cutover_is_off(
    tmp_path: Path, monkeypatch
) -> None:
    head = "a" * 40
    contract = TaskContract(
        digest="b" * 64,
        mode=TaskMode.IMPLEMENT,
        scope_patterns=("src/a.py",),
        target_branch="feature/family-dormant",
    )
    binding = FamilyBindingPayload(
        "family-1",
        "c" * 40,
        ("src/a.py",),
        None,
        None,
        1,
        None,
        None,
    )
    monkeypatch.setattr(
        workflow_run_setup,
        "inspect_repository",
        lambda _root: SimpleNamespace(
            branch="feature/family-dormant", head=head
        ),
    )
    monkeypatch.setattr(
        native_finding_decisions,
        "JOINT_67_68_NATIVE_CONTRACT_CUTOVER",
        False,
    )

    with pytest.raises(
        workflow_run_setup.StateSchemaError,
        match="JOINT_67_68_NATIVE_CONTRACT_CUTOVER",
    ):
        workflow_run_setup._fresh_state(
            task_file=tmp_path / "task.md",
            run_id="family-dormant",
            repository_root=tmp_path,
            task_contract=contract,
            family_binding=binding,
        )


def test_fresh_family_run_uses_family_base_but_slice_keeps_current_head(
    tmp_path: Path, monkeypatch
) -> None:
    head = "a" * 40
    family_base = "c" * 40
    plan_path = "docs/internal/plan.md"
    contract = TaskContract(
        digest="b" * 64,
        mode=TaskMode.IMPLEMENT,
        scope_patterns=(plan_path, "src/a.py"),
        target_branch="feature/family-active",
        work_plan_path=plan_path,
        approved_plan_commit=head,
        approved_slices=(PlannedSlice(1, "Implement", ("src/a.py",)),),
    )
    binding = FamilyBindingPayload(
        "family-1",
        family_base,
        (plan_path, "src/a.py"),
        "predecessor-run",
        "ar1-" + "d" * 64,
        2,
        head,
        "e" * 40,
    )
    monkeypatch.setattr(
        workflow_run_setup,
        "inspect_repository",
        lambda _root: SimpleNamespace(
            branch="feature/family-active", head=head
        ),
    )
    monkeypatch.setattr(
        workflow_run_setup, "require_committed_file_at_head", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        native_finding_decisions,
        "JOINT_67_68_NATIVE_CONTRACT_CUTOVER",
        True,
    )

    state = workflow_run_setup._fresh_state(
        task_file=tmp_path / "task.md",
        run_id="family-active",
        repository_root=tmp_path,
        task_contract=contract,
        family_binding=binding,
    )

    assert state.branch_base == family_base
    assert state.branch_review_base_commit == family_base
    assert state.current_slice.start_commit == head
    assert state.branch_review_authorized_change_set == (
        plan_path,
        "src/a.py",
    )
