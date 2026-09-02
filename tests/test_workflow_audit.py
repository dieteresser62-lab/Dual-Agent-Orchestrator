from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, cast

from audit_trail import AuditProjection, OverallAuditEntry
from orchestrator import ProductionWorkflowDriver
from workflow import WorkflowHistory
from workflow_audit import WorkflowAudit, WorkflowAuditDependencies
from workflow_state import init_workflow_state


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / "src/workflow_audit.py"
DRIVER_PATH = ROOT / "src/orchestrator.py"

EXPECTED_INTERNAL_IMPORTS = {
    "artifact_bridge",
    "artifact_migration",
    "artifact_replay",
    "audit_trail",
    "git_service",
    "repo_changes",
    "side_effects",
    "task_contract",
    "workflow",
    "workflow_state",
}

EXPECTED_DEPENDENCY_EDGES = {
    "artifact_bridge",
    "assert_structured_decision_context",
    "audit_projection",
    "authorized_test_approval",
    "bound_task_control_paths",
    "mark_completed_side_effect",
    "overall_audit_entries",
    "root",
    "side_effect_executor",
    "side_effect_spec",
}

EXPECTED_DRIVER_BINDINGS = {
    "root": "lambda: self.root",
    "artifact_bridge": "lambda: self._artifact_bridge",
    "assert_structured_decision_context": (
        "self.assert_structured_decision_context"
    ),
    "mark_completed_side_effect": "self._mark_completed_side_effect",
    "side_effect_executor": "self._side_effect_executor",
    "side_effect_spec": "self._side_effect_spec",
    "bound_task_control_paths": "_bound_task_control_paths",
    "overall_audit_entries": "_overall_audit_entries",
    "authorized_test_approval": "_authorized_test_approval",
    "audit_projection": "_audit_projection",
}


def _tree(path: Path = AUDIT_PATH, source: str | None = None) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8") if source is None else source)


def _class(tree: ast.Module, name: str) -> ast.ClassDef:
    return next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name
    )


def _dependency_edges(source: str | None = None) -> set[str]:
    boundary = _class(_tree(source=source), "WorkflowAudit")
    return {
        node.attr
        for node in ast.walk(boundary)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "_dependencies"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "self"
    }


def _internal_imports(path: Path) -> set[str]:
    tree = _tree(path)
    known_modules = {candidate.stem for candidate in (ROOT / "src").glob("*.py")}
    result: set[str] = set()
    for node in ast.walk(tree):
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


def _unexpected_dependency(*_args: object, **_kwargs: object) -> Any:
    raise AssertionError("an unavailable audit dependency was invoked")


def _dependencies(root: Path) -> WorkflowAuditDependencies:
    return WorkflowAuditDependencies(
        root=lambda: root,
        artifact_bridge=lambda: None,
        assert_structured_decision_context=_unexpected_dependency,
        mark_completed_side_effect=_unexpected_dependency,
        side_effect_executor=_unexpected_dependency,
        side_effect_spec=_unexpected_dependency,
        bound_task_control_paths=lambda _root, _state: (),
        overall_audit_entries=lambda _state, _replay, _reader: (
            OverallAuditEntry(
                label="Work Unit 01 - Audit",
                summary="Identische Projektion pruefen",
                scope_paths=("src/orchestrator.py",),
                projection=AuditProjection(slice_id=1),
            ),
        ),
        authorized_test_approval=lambda _unit, _replay: None,
        audit_projection=lambda *_args, **_kwargs: cast(AuditProjection, object()),
    )


def test_audit_module_has_complete_inventory_and_one_way_layering() -> None:
    assert _internal_imports(AUDIT_PATH) == EXPECTED_INTERNAL_IMPORTS
    assert "orchestrator" not in _internal_imports(AUDIT_PATH)
    assert "workflow_validation" not in _internal_imports(AUDIT_PATH)

    importers = []
    for path in sorted((ROOT / "src").glob("*.py")):
        if path == AUDIT_PATH:
            continue
        if any(
            isinstance(node, ast.ImportFrom)
            and node.module == "workflow_audit"
            for node in ast.walk(_tree(path))
        ):
            importers.append(path.relative_to(ROOT).as_posix())
    assert importers == ["src/orchestrator.py"]
    assert _dependency_edges() == EXPECTED_DEPENDENCY_EDGES

    for lower_layer in (
        "workflow_persistence.py",
        "workflow_recovery.py",
        "workflow_baseline.py",
    ):
        assert "workflow_audit" not in _internal_imports(ROOT / "src" / lower_layer)


def test_omitted_audit_projection_edge_turns_inventory_red() -> None:
    source = AUDIT_PATH.read_text(encoding="utf-8")
    marker = "self._dependencies.overall_audit_entries"
    assert marker in source
    mutated = source.replace(marker, "omitted_overall_audit_entries")
    assert "overall_audit_entries" not in _dependency_edges(mutated)
    assert _dependency_edges(mutated) != EXPECTED_DEPENDENCY_EDGES


def test_driver_binds_exact_audit_edges_and_keeps_public_facades() -> None:
    driver = _class(_tree(DRIVER_PATH), "ProductionWorkflowDriver")
    methods = {
        node.name: node for node in driver.body if isinstance(node, ast.FunctionDef)
    }
    boundary = methods["_audit_boundary"]
    dependency_call = next(
        node
        for node in ast.walk(boundary)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "WorkflowAuditDependencies"
    )
    actual = {
        keyword.arg: ast.unparse(keyword.value)
        for keyword in dependency_call.keywords
        if keyword.arg is not None
    }
    assert actual == EXPECTED_DRIVER_BINDINGS

    for facade, target in (
        ("finalize_audit", "finalize_audit"),
        ("_project_audit", "project_audit"),
    ):
        rendered = ast.dump(methods[facade], include_attributes=False)
        assert "_audit_boundary" in rendered
        assert target in rendered


def test_commit_slice_remains_at_the_driver_composition_root() -> None:
    driver_methods = {
        node.name
        for node in _class(_tree(DRIVER_PATH), "ProductionWorkflowDriver").body
        if isinstance(node, ast.FunctionDef)
    }
    audit_methods = {
        node.name
        for node in _class(_tree(), "WorkflowAudit").body
        if isinstance(node, ast.FunctionDef)
    }
    assert "commit_slice" in driver_methods
    assert "commit_slice" not in audit_methods


def test_finalize_without_audit_preserves_the_driver_early_return() -> None:
    state = init_workflow_state(
        run_id="workflow-audit-early-return",
        task_file="inbox/audit.md",
        branch="feature/backlog-followups",
        branch_base="b" * 40,
        slice_count=1,
        task_digest="a" * 64,
        task_scope_patterns=("src/orchestrator.py",),
        target_branch="feature/backlog-followups",
        timestamp="2026-09-02T00:00:00+00:00",
    )
    partial_driver = cast(
        ProductionWorkflowDriver,
        object.__new__(ProductionWorkflowDriver),
    )

    assert partial_driver.finalize_audit(state) is None


def test_audit_projection_is_byte_identical_for_identical_inputs(
    tmp_path: Path,
) -> None:
    task = tmp_path / "inbox" / "audit.md"
    task.parent.mkdir(parents=True)
    task.write_text("audit task", encoding="utf-8")
    state = init_workflow_state(
        run_id="workflow-audit-module",
        task_file=str(task),
        branch="feature/backlog-followups",
        branch_base="b" * 40,
        slice_count=1,
        task_digest="a" * 64,
        task_scope_patterns=("src/orchestrator.py",),
        audit_report_path="docs/internal/audit-review-12345678.md",
        target_branch="feature/backlog-followups",
        timestamp="2026-09-02T00:00:00+00:00",
    )
    audit = WorkflowAudit(_dependencies(tmp_path))
    history = WorkflowHistory(state.current_work_unit_id)
    target = tmp_path / cast(str, state.audit_report_path)

    audit.project_audit(state, history)
    first = target.read_bytes()
    assert b"Work Unit 01 - Audit" in first
    audit.project_audit(state, history)

    assert target.read_bytes() == first
