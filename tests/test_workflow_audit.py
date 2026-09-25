from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from orchestrator import ProductionWorkflowDriver
from workflow_audit import WorkflowAudit, WorkflowAuditDependencies
from workflow import WorkflowExecutionError, WorkflowHistory
from workflow_state import ProtocolMode, WorkUnitKind
from workflow_state import init_workflow_state


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / "src/workflow_audit.py"
GIT_COMMIT_PATH = ROOT / "src/workflow_git_commit.py"
DRIVER_PATH = ROOT / "src/orchestrator.py"


def test_archived_audit_is_not_regenerated_when_merged_run_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import workflow_audit

    replay = SimpleNamespace(side_effects=(SimpleNamespace(
        effect_class="git_commit", work_unit_id="3",
        operation=("archive_commit",),
    ),))
    monkeypatch.setattr(
        workflow_audit, "resolve_resume_state",
        lambda *_args, **_kwargs: SimpleNamespace(replay_result=replay),
    )
    dependencies = WorkflowAuditDependencies(
        root=lambda: tmp_path,
        artifact_bridge=lambda: SimpleNamespace(store=SimpleNamespace()),
        assert_structured_decision_context=lambda: None,
        mark_completed_side_effect=lambda _key: None,
        side_effect_executor=lambda _bridge: None,
        side_effect_spec=lambda *_args, **_kwargs: None,
        bound_task_control_paths=lambda *_args: (),
    )
    state = SimpleNamespace(
        protocol_binding=SimpleNamespace(mode=ProtocolMode.STRUCTURED_V2),
        run_id="completed-run", current_work_unit_id=3,
        current_work_unit=SimpleNamespace(kind=WorkUnitKind.FINAL_REVIEW),
        audit_report_path="docs/internal/audit.md",
    )
    WorkflowAudit(dependencies).project_audit(state, WorkflowHistory(3))
    assert not (tmp_path / "docs/internal/audit.md").exists()

EXPECTED_INTERNAL_IMPORTS = {
    "artifact_bridge",
    "audit_document_contract",
    "artifact_resume",
    "artifact_replay",
    "artifact_store",
    "git_service",
    "repo_changes",
    "readable_audit",
    "path_policy",
    "semantic_markdown",
    "side_effects",
    "state_io",
    "workflow",
    "workflow_state",
}

EXPECTED_DEPENDENCY_EDGES = {
    "artifact_bridge",
    "assert_structured_decision_context",
    "bound_task_control_paths",
    "mark_completed_side_effect",
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


def test_omitted_record_source_edge_turns_inventory_red() -> None:
    source = AUDIT_PATH.read_text(encoding="utf-8")
    marker = "self._dependencies.artifact_bridge"
    assert marker in source
    mutated = source.replace(marker, "omitted_artifact_bridge")
    assert "artifact_bridge" not in _dependency_edges(mutated)
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
    driver = _class(_tree(DRIVER_PATH), "ProductionWorkflowDriver")
    driver_methods = {
        node.name: node for node in driver.body if isinstance(node, ast.FunctionDef)
    }
    audit_methods = {
        node.name
        for node in _class(_tree(), "WorkflowAudit").body
        if isinstance(node, ast.FunctionDef)
    }
    git_commit_methods = {
        node.name
        for node in _class(_tree(GIT_COMMIT_PATH), "WorkflowGitCommit").body
        if isinstance(node, ast.FunctionDef)
    }
    assert "commit_slice" in driver_methods
    rendered = ast.dump(driver_methods["commit_slice"], include_attributes=False)
    assert "_git_commit_boundary" in rendered
    assert "commit_slice" in git_commit_methods
    assert "commit_slice" not in audit_methods


def test_finalize_without_audit_preserves_the_driver_early_return() -> None:
    state = init_workflow_state(
        run_id="workflow-audit-early-return",
        task_file="inbox/audit.md",
        branch="feature/backlog-followups",
        branch_base="b" * 40,
        first_slice_start_commit="b" * 40,
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


def test_audit_projection_is_byte_identical_for_identical_inputs() -> None:
    from readable_audit import render_overall
    from test_readable_audit import _facts

    facts = _facts()
    assert render_overall(facts, task="Task", branch="feature/test") == render_overall(
        facts, task="Task", branch="feature/test"
    )


@pytest.mark.parametrize("kind", ("overall", "slice", "plan"))
def test_audit_projection_rejects_lexical_symlink_without_touching_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    import readable_audit
    import workflow_audit
    from test_readable_audit import _facts

    root = tmp_path
    code = root / "src/app.py"
    code.parent.mkdir()
    code.write_bytes(b"original source bytes\n")
    relative = {
        "overall": "docs/internal/overall.md",
        "slice": "docs/internal/slice-test-01-test.md",
        "plan": "docs/internal/plan.md",
    }[kind]
    link = root / relative
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(code)
    facts = _facts(plan_only=kind == "plan")
    monkeypatch.setattr(readable_audit, "AuditFacts", lambda *_args, **_kwargs: facts)
    monkeypatch.setattr(workflow_audit, "resolve_resume_state", lambda *_args, **_kwargs: SimpleNamespace(replay_result=facts.replay))
    dependencies = WorkflowAuditDependencies(
        root=lambda: root,
        artifact_bridge=lambda: SimpleNamespace(store=SimpleNamespace(read_blob=lambda _ref: b"")),
        assert_structured_decision_context=lambda: None,
        mark_completed_side_effect=lambda _key: None,
        side_effect_executor=lambda _bridge: None,
        side_effect_spec=lambda *_args, **_kwargs: None,
        bound_task_control_paths=lambda *_args: (),
    )
    state = SimpleNamespace(
        protocol_binding=SimpleNamespace(mode=ProtocolMode.STRUCTURED_V2),
        run_id="readable-test-run", audit_report_path=relative if kind == "overall" else None,
        task_file="task.md", branch="feature/test",
        current_work_unit=SimpleNamespace(kind=WorkUnitKind.PLAN if kind == "plan" else WorkUnitKind.SLICE),
        current_slice=SimpleNamespace(commit_ref=None, scope_paths=(relative,)),
        current_slice_id=1, work_plan_path=relative if kind == "plan" else None,
        planned_slices=(),
    )
    if kind == "plan":
        # The plan-contract gate diagnoses this target after the projection skips it.
        WorkflowAudit(dependencies).project_audit(state, WorkflowHistory(1))
    else:
        with pytest.raises(WorkflowExecutionError, match="symlink"):
            WorkflowAudit(dependencies).project_audit(state, WorkflowHistory(1))
    assert link.is_symlink()
    assert code.read_bytes() == b"original source bytes\n"


def test_audit_projection_rejects_symlink_parent_without_touching_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import readable_audit
    import workflow_audit
    from test_readable_audit import _facts

    destination = tmp_path / "audit-destination"
    destination.mkdir()
    target = destination / "overall.md"
    original = b"original audit bytes\n"
    target.write_bytes(original)
    docs = tmp_path / "docs"
    docs.mkdir()
    link = docs / "internal"
    link.symlink_to(destination, target_is_directory=True)
    relative = "docs/internal/overall.md"
    assert not (tmp_path / relative).is_symlink()

    facts = _facts()
    monkeypatch.setattr(readable_audit, "AuditFacts", lambda *_args, **_kwargs: facts)
    monkeypatch.setattr(
        workflow_audit, "resolve_resume_state",
        lambda *_args, **_kwargs: SimpleNamespace(replay_result=facts.replay),
    )
    dependencies = WorkflowAuditDependencies(
        root=lambda: tmp_path,
        artifact_bridge=lambda: SimpleNamespace(store=SimpleNamespace(read_blob=lambda _ref: b"")),
        assert_structured_decision_context=lambda: None,
        mark_completed_side_effect=lambda _key: None,
        side_effect_executor=lambda _bridge: None,
        side_effect_spec=lambda *_args, **_kwargs: None,
        bound_task_control_paths=lambda *_args: (),
    )
    state = SimpleNamespace(
        protocol_binding=SimpleNamespace(mode=ProtocolMode.STRUCTURED_V2),
        run_id="readable-test-run", audit_report_path=relative,
        task_file="task.md", branch="feature/test",
        current_work_unit=SimpleNamespace(kind=WorkUnitKind.FINAL_REVIEW),
    )

    with pytest.raises(WorkflowExecutionError, match="symlink"):
        WorkflowAudit(dependencies).project_audit(state, WorkflowHistory(1))

    assert link.is_symlink()
    assert sorted(path.name for path in destination.iterdir()) == ["overall.md"]
    assert target.read_bytes() == original
