from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from agent_runtime import OrchestratorConfig
from workflow import (
    PlanContractFailureKind,
    PlanContractValidationError,
    WorkflowChanges,
)
from workflow_validation import WorkflowValidation, WorkflowValidationDependencies


ROOT = Path(__file__).resolve().parents[1]
VALIDATION_PATH = ROOT / "src/workflow_validation.py"
DRIVER_PATH = ROOT / "src/orchestrator.py"

EXPECTED_INTERNAL_IMPORTS = {
    "agent_runtime",
    "artifact_bridge",
    "artifact_models",
    "content_authority",
    "contracts",
    "gates",
    "plan_handoff",
    "semantic_markdown",
    "workflow",
    "workflow_persistence",
    "workflow_state",
}

EXPECTED_DEPENDENCY_EDGES = {
    "active_state",
    "artifact_bridge",
    "assert_structured_decision_context",
    "config",
    "persistence",
    "root",
}

EXPECTED_DRIVER_BINDINGS = {
    "root": "lambda: self.root",
    "active_state": "lambda: self.active_state",
    "artifact_bridge": "lambda: self._artifact_bridge",
    "assert_structured_decision_context": (
        "self.assert_structured_decision_context"
    ),
    "config": "lambda: self.config",
    "persistence": "self._persistence_boundary",
}


def _tree(path: Path = VALIDATION_PATH, source: str | None = None) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8") if source is None else source)


def _class(tree: ast.Module, name: str) -> ast.ClassDef:
    return next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name
    )


def _dependency_edges(source: str | None = None) -> set[str]:
    boundary = _class(_tree(source=source), "WorkflowValidation")
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
    raise AssertionError("an unavailable validation dependency was invoked")


def _dependencies(root: Path) -> WorkflowValidationDependencies:
    return WorkflowValidationDependencies(
        root=lambda: root,
        active_state=lambda: None,
        artifact_bridge=lambda: None,
        assert_structured_decision_context=_unexpected_dependency,
        config=lambda: OrchestratorConfig(repo_root=root),
        persistence=_unexpected_dependency,
    )


def test_validation_module_has_complete_inventory_and_one_way_layering() -> None:
    assert _internal_imports(VALIDATION_PATH) == EXPECTED_INTERNAL_IMPORTS
    assert "orchestrator" not in _internal_imports(VALIDATION_PATH)
    assert "workflow_audit" not in _internal_imports(VALIDATION_PATH)

    importers = []
    for path in sorted((ROOT / "src").glob("*.py")):
        if path == VALIDATION_PATH:
            continue
        if any(
            isinstance(node, ast.ImportFrom)
            and node.module == "workflow_validation"
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
        assert "workflow_validation" not in _internal_imports(ROOT / "src" / lower_layer)


def test_omitted_validation_persistence_edge_turns_inventory_red() -> None:
    source = VALIDATION_PATH.read_text(encoding="utf-8")
    marker = "self._dependencies.persistence"
    assert marker in source
    mutated = source.replace(marker, "omitted_persistence")
    assert "persistence" not in _dependency_edges(mutated)
    assert _dependency_edges(mutated) != EXPECTED_DEPENDENCY_EDGES


def test_driver_binds_exact_validation_edges_and_keeps_public_facades() -> None:
    driver = _class(_tree(DRIVER_PATH), "ProductionWorkflowDriver")
    methods = {
        node.name: node for node in driver.body if isinstance(node, ast.FunctionDef)
    }
    boundary = methods["_validation_boundary"]
    dependency_call = next(
        node
        for node in ast.walk(boundary)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "WorkflowValidationDependencies"
    )
    actual = {
        keyword.arg: ast.unparse(keyword.value)
        for keyword in dependency_call.keywords
        if keyword.arg is not None
    }
    assert actual == EXPECTED_DRIVER_BINDINGS

    for facade, target in (
        ("recover_pending_validation_attestation", "recover_pending_validation_attestation"),
        ("validate", "validate"),
        ("validate_plan", "validate_plan"),
    ):
        rendered = ast.dump(methods[facade], include_attributes=False)
        assert "_validation_boundary" in rendered
        assert target in rendered

    for persistence_facade in (
        "persist_validation_attestation",
        "persist_validation_request",
    ):
        rendered = ast.dump(methods[persistence_facade], include_attributes=False)
        assert "_persistence_boundary" in rendered
        assert "_validation_boundary" not in rendered


def test_validate_moves_with_config_as_an_explicit_dependency() -> None:
    driver = _class(_tree(DRIVER_PATH), "ProductionWorkflowDriver")
    driver_validate = next(
        node
        for node in driver.body
        if isinstance(node, ast.FunctionDef) and node.name == "validate"
    )
    rendered_driver = ast.dump(driver_validate, include_attributes=False)
    assert "run_validation_matrix" not in rendered_driver
    assert "_validation_boundary" in rendered_driver

    validation = _class(_tree(), "WorkflowValidation")
    moved = next(
        node
        for node in validation.body
        if isinstance(node, ast.FunctionDef) and node.name == "validate"
    )
    rendered_moved = ast.dump(moved, include_attributes=False)
    assert "run_validation_matrix" in rendered_moved
    assert "config" in rendered_moved


def test_plan_validation_without_driver_owned_state_fails_closed(
    tmp_path: Path,
) -> None:
    validation = WorkflowValidation(_dependencies(tmp_path))
    changes = WorkflowChanges(
        start_commit="b" * 40,
        fingerprint="a" * 64,
        paths=("src/example.py",),
        full_diff="diff",
    )
    with pytest.raises(PlanContractValidationError) as caught:
        validation.validate_plan(
            changes,
            work_plan_path=None,
            scope_patterns=("src/*.py",),
            plan_only=False,
        )
    assert caught.value.kind is PlanContractFailureKind.PERSISTED_SLICE_PLAN_MISSING
