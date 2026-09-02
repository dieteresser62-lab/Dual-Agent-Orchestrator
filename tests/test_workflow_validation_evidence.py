from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
EVIDENCE_PATH = SRC / "workflow_validation_evidence.py"
WORKFLOW_PATH = SRC / "workflow.py"

EXPECTED_INTERNAL_IMPORTS = {
    "audit_trail",
    "contracts",
    "gates",
    "validation_matrix",
    "workflow_state",
}
EXPECTED_ATTESTATION_EDGES = {
    "validate_plan",
    "persist_validation_attestation",
    "persist_validation_request",
    "recover_pending_validation_attestation",
    "validate",
    "retried_failed_validation_fingerprints",
    "execution_error",
    "validation_execution_error",
}


def _tree(path: Path = EVIDENCE_PATH, source: str | None = None) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8") if source is None else source)


def _class(tree: ast.Module, name: str) -> ast.ClassDef:
    return next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name
    )


def _internal_imports(path: Path) -> set[str]:
    tree = _tree(path)
    known = {candidate.stem for candidate in SRC.glob("*.py")}
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            module = node.module.split(".", 1)[0]
            if module in known:
                result.add(module)
        elif isinstance(node, ast.Import):
            result.update(
                module
                for alias in node.names
                if (module := alias.name.split(".", 1)[0]) in known
            )
    return result


def _dependency_edges(source: str | None = None) -> set[str]:
    owner = _class(_tree(source=source), "WorkflowValidationEvidence")
    return {
        node.attr
        for node in ast.walk(owner)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "_dependencies"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "self"
    }


def _importers(module_name: str) -> list[str]:
    result: list[str] = []
    for path in sorted(SRC.glob("*.py")):
        if path == EVIDENCE_PATH:
            continue
        tree = _tree(path)
        if any(
            isinstance(node, ast.ImportFrom) and node.module == module_name
            or isinstance(node, ast.Import)
            and any(alias.name == module_name for alias in node.names)
            for node in ast.walk(tree)
        ):
            result.append(path.relative_to(ROOT).as_posix())
    return result


def test_validation_evidence_has_exact_one_way_inventory() -> None:
    tree = _tree()
    declarations = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef))
    }
    assert "validate_change_boundary" in declarations
    assert _internal_imports(EVIDENCE_PATH) == EXPECTED_INTERNAL_IMPORTS
    assert "workflow" not in _internal_imports(EVIDENCE_PATH)
    assert _importers("workflow_validation_evidence") == ["src/workflow.py"]
    assert _dependency_edges() == EXPECTED_ATTESTATION_EDGES

    boundary = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "validate_change_boundary"
    )
    assert "_dependencies" not in ast.dump(boundary, include_attributes=False)
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "WorkflowValidationEvidenceDependencies"
        for node in ast.walk(boundary)
    )


@pytest.mark.parametrize(
    "edge",
    (
        "validate_plan",
        "persist_validation_attestation",
        "persist_validation_request",
        "recover_pending_validation_attestation",
        "validate",
    ),
)
def test_omitted_validation_or_attestation_edge_turns_inventory_red(edge: str) -> None:
    source = EVIDENCE_PATH.read_text(encoding="utf-8")
    marker = f"self._dependencies.{edge}("
    assert marker in source
    mutated = source.replace(marker, f"omitted_{edge}(")
    assert edge not in _dependency_edges(mutated)
    assert _dependency_edges(mutated) != EXPECTED_ATTESTATION_EDGES


def test_engine_facades_delegate_without_owning_leaf_decisions() -> None:
    workflow = _tree(WORKFLOW_PATH)
    engine = _class(workflow, "WorkflowEngine")
    methods = {
        node.name: node for node in engine.body if isinstance(node, ast.FunctionDef)
    }
    boundary_facade = ast.unparse(methods["_validate_change_boundary"])
    attestation_facade = ast.unparse(methods["_attestation"])
    assert "workflow_validation_evidence.validate_change_boundary" in boundary_facade
    assert "self._validation_evidence.attestation" in attestation_facade
    assert "select_validation_request" not in attestation_facade
    assert "matches_path_patterns" not in boundary_facade

    run_control = {
        name: ast.unparse(methods[name])
        for name in (
            "run_current_work_unit",
            "_invoke_role",
            "_run_codex",
            "_run_review",
            "_run_final_codex_report",
        )
    }
    assert all("WorkflowValidationEvidenceDependencies" not in source for source in run_control.values())
