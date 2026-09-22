from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path

import pytest

import orchestrator
import workflow_dry_run
import workflow_production
from cli import parse_args


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
ORCHESTRATOR_PATH = SRC / "orchestrator.py"
PRODUCTION_PATH = SRC / "workflow_production.py"
DRY_RUN_PATH = SRC / "workflow_dry_run.py"

PRODUCTION_DEPENDENCY_INVENTORY = (
    "driver_factory",
    "apply_resumed_agent_profiles",
    "archive_stale_untracked_audit_reports",
    "attach_managed_audit_paths",
    "bound_task_control_paths",
    "context",
    "current_gate_approval",
    "fresh_state",
    "history",
    "inherit_redundant_test_gate",
    "managed_audit_path",
    "new_watch_task_control_paths",
    "new_watch_task_preserved_paths",
    "recover_final_review_attestation",
    "recover_legacy_plan_only_post_gate",
    "unused_run_id",
)

PRODUCTION_CONSTRUCTION_EDGES = (
    "OrchestratorConfig",
    "dependencies.driver_factory",
    "require_production_workflow_loop_driver",
    "WorkflowEngine",
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _imported_modules(path: Path) -> frozenset[str]:
    modules: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return frozenset(modules)


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if (
        isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
    ):
        return f"{call.func.value.id}.{call.func.attr}"
    return None


def _assert_production_construction_edges(source: str) -> None:
    loop = _function(ast.parse(source), "run_production_workflow")
    calls = tuple(
        name
        for _, name in sorted(
            (
                (node.lineno, _call_name(node))
                for node in ast.walk(loop)
                if isinstance(node, ast.Call) and _call_name(node) is not None
            ),
            key=lambda item: item[0],
        )
        if name in PRODUCTION_CONSTRUCTION_EDGES
    )
    assert calls == PRODUCTION_CONSTRUCTION_EDGES, (
        f"production construction edges changed: {calls!r}"
    )


def test_runtime_modules_have_single_importer_and_no_cross_edge() -> None:
    importers = {
        module: {
            path.stem
            for path in SRC.glob("*.py")
            if module in _imported_modules(path)
        }
        for module in ("workflow_production", "workflow_dry_run")
    }

    assert importers == {
        "workflow_production": {"orchestrator"},
        "workflow_dry_run": {"orchestrator"},
    }
    assert "orchestrator" not in _imported_modules(PRODUCTION_PATH)
    assert "orchestrator" not in _imported_modules(DRY_RUN_PATH)
    assert "workflow_dry_run" not in _imported_modules(PRODUCTION_PATH)
    assert "workflow_production" not in _imported_modules(DRY_RUN_PATH)


def test_production_dependency_inventory_is_exact_and_fully_consumed() -> None:
    declared = tuple(
        field.name for field in fields(workflow_production.ProductionWorkflowDependencies)
    )
    production_tree = _tree(PRODUCTION_PATH)
    dependency_consumers = (
        _function(production_tree, "run_production_workflow"),
        _function(production_tree, "_run_production_transition_loop"),
        _function(production_tree, "_recover_final_review_history"),
    )
    consumed = {
        node.attr
        for consumer in dependency_consumers
        for node in ast.walk(consumer)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "dependencies"
    }
    builder = _function(_tree(ORCHESTRATOR_PATH), "_production_workflow_dependencies")
    wired = {
        keyword.arg
        for node in ast.walk(builder)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg is not None
    }

    assert declared == PRODUCTION_DEPENDENCY_INVENTORY
    assert consumed == set(PRODUCTION_DEPENDENCY_INVENTORY)
    assert wired == set(PRODUCTION_DEPENDENCY_INVENTORY)


def test_orchestrator_entrypoints_delegate_to_runtime_modules(monkeypatch) -> None:
    task = Path("inbox/b35.md")
    marker = object()
    calls: list[tuple[object, ...]] = []

    def production(
        received_task: Path,
        received_args: object,
        dependencies: workflow_production.ProductionWorkflowDependencies,
        *,
        force_new: bool = False,
    ) -> object:
        calls.append(("production", received_task, received_args, force_new))
        assert isinstance(
            dependencies, workflow_production.ProductionWorkflowDependencies
        )
        return marker

    def dry_run(received_task: Path, *, run_id: str | None = None) -> object:
        calls.append(("dry-run", received_task, run_id))
        return marker

    monkeypatch.setattr(workflow_production, "run_production_workflow", production)
    monkeypatch.setattr(workflow_dry_run, "run_default_dry_run", dry_run)
    args = object()

    assert orchestrator.run_production_workflow(task, args, force_new=True) is marker
    assert orchestrator.run_default_dry_run(task, run_id="b35-run") is marker
    assert calls == [
        ("production", task, args, True),
        ("dry-run", task, "b35-run"),
    ]


def test_run_pipeline_remains_public_and_routes_both_branches(
    tmp_path: Path, monkeypatch
) -> None:
    task = tmp_path / "task.md"
    task.write_text("B35 provider-free entrypoint test", encoding="utf-8")
    terminal, scenario_calls, validations = workflow_dry_run.run_default_dry_run(task)
    reached: list[str] = []

    def dry_run(*_args, **_kwargs):
        reached.append("dry-run")
        return terminal, scenario_calls, validations

    def production(*_args, **_kwargs):
        reached.append("production")
        return terminal

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(orchestrator, "run_default_dry_run", dry_run)
    dry_args = parse_args(
        ["--dry-run", "--task-file", str(task)], cwd=tmp_path, environ={}
    )
    assert orchestrator.run_pipeline(task, dry_args) == 0

    monkeypatch.setattr(orchestrator, "run_production_workflow", production)
    production_args = parse_args(
        ["--task-file", str(task)], cwd=tmp_path, environ={}
    )
    assert orchestrator.run_pipeline(task, production_args) == 0
    assert callable(orchestrator.run_pipeline)
    assert reached == ["dry-run", "production"]


def test_omitted_production_construction_edge_breaks_guard() -> None:
    source = PRODUCTION_PATH.read_text(encoding="utf-8")
    _assert_production_construction_edges(source)
    mutated = source.replace(
        "    require_production_workflow_loop_driver(driver)\n",
        "    # mutation: production driver contract edge omitted\n",
        1,
    )
    assert mutated != source

    with pytest.raises(AssertionError, match="production construction edges changed"):
        _assert_production_construction_edges(mutated)
