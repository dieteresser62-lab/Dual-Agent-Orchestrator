from __future__ import annotations

import ast
import copy
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from artifact_bridge import ArtifactBridge
from artifact_models import FingerprintKind, RunIdentityPayload
from artifact_store import ArtifactStore
from workflow import WorkflowExecutionError
from workflow_baseline import (
    WorkflowBaseline,
    WorkflowBaselineDependencies,
    bootstrap_fact,
    gate_transition_payload,
    matches_baseline_initialization_prefix,
)
from workflow_state import (
    ProtocolBinding,
    ProtocolMode,
    WorkflowState,
    WorkflowStep,
    WorkUnitKind,
    init_workflow_state,
)


ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "src/workflow_baseline.py"
DRIVER_PATH = ROOT / "src/orchestrator.py"

EXPECTED_INTERNAL_IMPORTS = {
    "artifact_bridge",
    "artifact_resume",
    "artifact_models",
    "artifact_replay",
    "final_review_preflight",
    "provider_input_budget",
    "side_effects",
    "workflow",
    "workflow_persistence",
    "workflow_recovery",
    "workflow_state",
}

EXPECTED_DEPENDENCY_EDGES = {
    "active_state",
    "artifact_bridge",
    "artifact_fingerprint",
    "collect_changes",
    "persist_bootstrap_state",
    "persistence",
    "reconcile_pending_workflow_event",
    "recovery",
    "side_effect_executor",
}

EXPECTED_DRIVER_BINDINGS = {
    "active_state": "lambda: self.active_state",
    "artifact_bridge": "lambda: self._artifact_bridge",
    "artifact_fingerprint": "self._artifact_fingerprint",
    "collect_changes": "self.collect_changes",
    "persist_bootstrap_state": "self._persist_bootstrap_state",
    "persistence": "self._persistence_boundary",
    "reconcile_pending_workflow_event": "self._reconcile_pending_workflow_event",
    "recovery": "self._recovery_boundary",
    "side_effect_executor": "self._side_effect_executor",
}


def _tree(path: Path = BASELINE_PATH, source: str | None = None) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8") if source is None else source)


def _class(tree: ast.Module, name: str) -> ast.ClassDef:
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == name
    )


def _dependency_edges(source: str | None = None) -> set[str]:
    baseline = _class(_tree(source=source), "WorkflowBaseline")
    return {
        node.attr
        for node in ast.walk(baseline)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "_dependencies"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "self"
    }


def _state(tmp_path: Path) -> WorkflowState:
    task = tmp_path / "inbox" / "baseline.md"
    task.parent.mkdir(parents=True)
    task.write_text("baseline", encoding="utf-8")
    return (
        init_workflow_state(
            run_id="workflow-baseline-module",
            task_file="inbox/baseline.md",
            branch="feature/backlog-followups",
            branch_base="b" * 40,
            first_slice_start_commit="b" * 40,
            slice_count=1,
            task_digest="a" * 64,
            task_scope_patterns=("src/baseline.py",),
            target_branch="feature/backlog-followups",
            protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
            timestamp="2026-09-02T00:00:00+00:00",
        )
        .complete_current_work_unit(updated_at="2026-09-02T00:00:00+00:00")
        .start_work_unit(
            slice_id=1,
            kind=WorkUnitKind.SLICE,
            step=WorkflowStep.CODEX_IMPLEMENTATION,  # allowlist:provider
            updated_at="2026-09-02T00:00:00+00:00",
        )
        .bind_current_slice_git_boundary(
            start_commit="b" * 40,
            scope_paths=("src/baseline.py",),
            start_fingerprint="c" * 64,
            updated_at="2026-09-02T00:00:00+00:00",
        )
    )


def _identity_prefix(tmp_path: Path, state: WorkflowState):
    bridge = ArtifactBridge(ArtifactStore(tmp_path, state.run_id))
    return (
        bridge.append(
            RunIdentityPayload(
                task_file=state.task_file,
                branch=state.branch,
                branch_base=state.branch_base,
                first_slice_start_commit=state.slices[0].start_commit,
                execution_mode=state.execution_mode,
                audit_report_path=state.audit_report_path,
            ),
            logical_id="run-identity",
            idempotency_key="run-identity",
            fingerprint_sha256=cast(str, state.task_digest),
            fingerprint_kind=FingerprintKind.CONTRACT,
        ),
    )


def _with_work_unit_fact(
    state: WorkflowState, field: str, value: tuple[object, ...]
) -> WorkflowState:
    units = list(state.work_units)
    unit = copy.copy(units[-1])
    object.__setattr__(unit, field, value)
    units[-1] = unit
    changed = copy.copy(state)
    object.__setattr__(changed, "work_units", tuple(units))
    return changed


def _unexpected_dependency(*_args: object, **_kwargs: object) -> Any:
    raise AssertionError("an unavailable baseline dependency was invoked")


def _dependencies() -> WorkflowBaselineDependencies:
    return WorkflowBaselineDependencies(
        artifact_bridge=lambda: None,
        active_state=lambda: None,
        artifact_fingerprint=_unexpected_dependency,
        collect_changes=_unexpected_dependency,
        persist_bootstrap_state=_unexpected_dependency,
        persistence=_unexpected_dependency,
        recovery=_unexpected_dependency,
        reconcile_pending_workflow_event=_unexpected_dependency,
        side_effect_executor=_unexpected_dependency,
    )


def test_baseline_module_has_complete_inventory_and_one_way_layering() -> None:
    tree = _tree()
    known_modules = {path.stem for path in (ROOT / "src").glob("*.py")}
    internal_imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            module = node.module.split(".", 1)[0]
            if module in known_modules:
                internal_imports.add(module)
        elif isinstance(node, ast.Import):
            internal_imports.update(
                module
                for alias in node.names
                if (module := alias.name.split(".", 1)[0]) in known_modules
            )
    assert internal_imports == EXPECTED_INTERNAL_IMPORTS
    assert "orchestrator" not in internal_imports
    assert {"workflow_persistence", "workflow_recovery"} <= internal_imports

    importers = []
    for path in sorted((ROOT / "src").glob("*.py")):
        if path == BASELINE_PATH:
            continue
        candidate = _tree(path)
        if any(
            isinstance(node, ast.ImportFrom)
            and node.module == "workflow_baseline"
            for node in ast.walk(candidate)
        ):
            importers.append(path.relative_to(ROOT).as_posix())
    assert importers == ["src/orchestrator.py"]
    assert _dependency_edges() == EXPECTED_DEPENDENCY_EDGES


@pytest.mark.parametrize("edge", ("persistence", "recovery", "persist_bootstrap_state"))
def test_omitted_baseline_or_bootstrap_edge_turns_inventory_red(edge: str) -> None:
    source = BASELINE_PATH.read_text(encoding="utf-8")
    marker = f"self._dependencies.{edge}"
    assert marker in source
    mutated = source.replace(marker, f"omitted_{edge}")
    assert edge not in _dependency_edges(mutated)
    assert _dependency_edges(mutated) != EXPECTED_DEPENDENCY_EDGES


def test_driver_binds_exact_baseline_edges_and_keeps_bootstrap_state_root() -> None:
    driver = _class(_tree(DRIVER_PATH), "ProductionWorkflowDriver")
    methods = {
        node.name: node for node in driver.body if isinstance(node, ast.FunctionDef)
    }
    boundary = methods["_baseline_boundary"]
    dependency_call = next(
        node
        for node in ast.walk(boundary)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "WorkflowBaselineDependencies"
    )
    actual = {
        keyword.arg: ast.unparse(keyword.value)
        for keyword in dependency_call.keywords
        if keyword.arg is not None
    }
    assert actual == EXPECTED_DRIVER_BINDINGS

    for facade, target in (
        ("_persist_structured_baseline", "_persist_structured_baseline"),
        ("_persist_provider_bootstrap", "_persist_provider_bootstrap"),
    ):
        rendered = ast.dump(methods[facade], include_attributes=False)
        assert "_baseline_boundary" in rendered
        assert target in rendered

    assert "matches_baseline_initialization_prefix" in ast.dump(
        methods["_matches_baseline_initialization_prefix"], include_attributes=False
    )
    assert "bootstrap_fact" in ast.dump(
        methods["_bootstrap_fact"], include_attributes=False
    )
    bootstrap_root = ast.dump(
        methods["_persist_bootstrap_state"], include_attributes=False
    )
    assert "save_workflow_state" in bootstrap_root
    assert "write_workflow_state_projection" in bootstrap_root
    assert "_baseline_boundary" not in bootstrap_root
    baseline_methods = {
        node.name
        for node in _class(_tree(), "WorkflowBaseline").body
        if isinstance(node, ast.FunctionDef)
    }
    assert "_persist_bootstrap_state" not in baseline_methods


def test_exact_prefix_admitted_and_every_named_prior_fact_rejected(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    prefix = _identity_prefix(tmp_path, state)
    assert matches_baseline_initialization_prefix(prefix, state)

    non_prefix = (replace(prefix[0], idempotency_key="not-run-identity"),)
    assert not matches_baseline_initialization_prefix(non_prefix, state)
    assert not matches_baseline_initialization_prefix(
        prefix, replace(state, runtime_history={})
    )
    assert not matches_baseline_initialization_prefix(
        prefix,
        _with_work_unit_fact(state, "invocation_failures", (cast(Any, object()),)),
    )
    assert not matches_baseline_initialization_prefix(
        prefix,
        _with_work_unit_fact(state, "gate_decisions", (cast(Any, object()),)),
    )
    assert not matches_baseline_initialization_prefix(
        prefix,
        _with_work_unit_fact(state, "completed_side_effects", ("completed",)),
    )


def test_provider_bootstrap_without_driver_state_fails_closed() -> None:
    baseline = WorkflowBaseline(_dependencies())
    with pytest.raises(WorkflowExecutionError, match="has no active state"):
        baseline._persist_provider_bootstrap(cast(Any, object()))


def test_pure_helpers_are_driver_free() -> None:
    assert callable(matches_baseline_initialization_prefix)
    assert callable(bootstrap_fact)
    assert callable(gate_transition_payload)
    helper_names = {
        node.name
        for node in _tree().body
        if isinstance(node, ast.FunctionDef)
        and node.name
        in {
            "matches_baseline_initialization_prefix",
            "bootstrap_fact",
            "gate_transition_payload",
        }
    }
    assert helper_names == {
        "matches_baseline_initialization_prefix",
        "bootstrap_fact",
        "gate_transition_payload",
    }
