from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROJECTION_PATH = ROOT / "src/workflow_audit_projection.py"
DRIVER_PATH = ROOT / "src/orchestrator.py"

EXPECTED_INTERNAL_IMPORTS = {
    "artifact_bridge",
    "artifact_models",
    "artifact_replay",
    "audit_trail",
    "contracts",
    "finding_reducer",
    "gates",
    "git_service",
    "inbox_watcher",
    "repo_changes",
    "review_packets",
    "workflow",
    "workflow_state",
}
EXPECTED_PROJECTION_FUNCTIONS = {
    "_archive_stale_untracked_audit_reports",
    "_attach_managed_audit_paths",
    "_attach_record_events",
    "_hydrate_record_history",
    "_audit_projection",
    "_authorized_test_approval",
    "_is_managed_audit_path",
    "_is_planned_slice_document",
    "_managed_audit_path",
    "_managed_slice_scope_pattern",
    "_overall_audit_entries",
    "_persisted_histories",
    "_recover_final_review_attestation",
}
EXPECTED_PROJECTION_EDGES = Counter(
    {
        ("_audit_projection", "_authorized_test_approval"): 1,
        ("_persisted_histories", "_attach_record_events"): 2,
        ("_hydrate_record_history", "_attach_record_events"): 1,
        ("_recover_final_review_attestation", "_persisted_histories"): 1,
        ("_overall_audit_entries", "_persisted_histories"): 1,
        ("_overall_audit_entries", "_audit_projection"): 1,
        ("_is_managed_audit_path", "_is_planned_slice_document"): 1,
        ("_is_managed_audit_path", "_managed_slice_scope_pattern"): 1,
    }
)


def _tree(path: Path = PROJECTION_PATH, source: str | None = None) -> ast.Module:
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


def _projection_edges(source: str | None = None) -> Counter[tuple[str, str]]:
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


def _keyword_bindings(function_name: str) -> dict[str, str]:
    function = next(
        node
        for node in ast.walk(_tree(DRIVER_PATH))
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    dependency_call = next(
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and (
            isinstance(node.func, ast.Name)
            and node.func.id.endswith("Dependencies")
            or isinstance(node.func, ast.Attribute)
            and node.func.attr.endswith("Dependencies")
        )
    )
    return {
        keyword.arg: ast.unparse(keyword.value)
        for keyword in dependency_call.keywords
        if keyword.arg is not None
    }


def test_audit_projection_module_has_complete_inventory_and_one_way_layering() -> None:
    assert _internal_imports(PROJECTION_PATH) == EXPECTED_INTERNAL_IMPORTS
    assert "orchestrator" not in _internal_imports(PROJECTION_PATH)
    assert "workflow_audit" not in _internal_imports(PROJECTION_PATH)
    assert _function_names(_tree()) == EXPECTED_PROJECTION_FUNCTIONS

    importers = []
    for path in sorted((ROOT / "src").glob("*.py")):
        if path == PROJECTION_PATH:
            continue
        if any(
            (
                isinstance(node, ast.ImportFrom)
                and node.module == "workflow_audit_projection"
            )
            or (
                isinstance(node, ast.Import)
                and any(
                    alias.name == "workflow_audit_projection"
                    for alias in node.names
                )
            )
            for node in ast.walk(_tree(path))
        ):
            importers.append(path.relative_to(ROOT).as_posix())
    assert importers == ["src/orchestrator.py"]

    for path in sorted((ROOT / "src").glob("workflow_*.py")):
        if path == PROJECTION_PATH:
            continue
        assert "workflow_audit_projection" not in _internal_imports(path)


def test_history_helpers_stay_at_their_exclusive_responsibility_boundary() -> None:
    projection_functions = _function_names(_tree())
    driver_functions = _function_names(_tree(DRIVER_PATH))

    assert "_persisted_histories" in projection_functions
    assert "_persisted_histories" not in driver_functions
    assert {"_history", "_history_payload"} <= driver_functions
    assert not {"_history", "_history_payload"} & projection_functions
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_persisted_histories"
        for node in ast.walk(_tree(DRIVER_PATH))
    )


def test_projection_and_history_edge_inventory_fails_closed_on_omission() -> None:
    assert _projection_edges() == EXPECTED_PROJECTION_EDGES

    source = PROJECTION_PATH.read_text(encoding="utf-8")
    call = "_attach_record_events(histories, structured_replay, read_blob)"
    assert source.count(call) == 2
    mutated = source.replace(call, "histories")

    assert _projection_edges(mutated) != EXPECTED_PROJECTION_EDGES
    assert (
        "_persisted_histories",
        "_attach_record_events",
    ) not in _projection_edges(mutated)


def test_existing_projection_handoffs_remain_exactly_bound() -> None:
    audit_bindings = _keyword_bindings("_audit_boundary")
    production_bindings = _keyword_bindings("_production_workflow_dependencies")

    assert audit_bindings["overall_audit_entries"] == "_overall_audit_entries"
    assert production_bindings["archive_stale_untracked_audit_reports"] == (
        "_archive_stale_untracked_audit_reports"
    )
    assert production_bindings["recover_final_review_attestation"] == (
        "_recover_final_review_attestation"
    )
