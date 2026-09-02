from __future__ import annotations

import ast
from pathlib import Path

import pytest

from workflow_production import (
    PRODUCTION_LOOP_INTERNAL_DRIVER_METHODS,
    require_production_workflow_loop_driver,
)
from workflow import (
    MANDATORY_WORKFLOW_DRIVER_METHODS,
    MANDATORY_WORKFLOW_DRIVER_STATE_ATTRIBUTES,
    OPTIONAL_WORKFLOW_DRIVER_CAPABILITIES,
    WorkflowDriverContractError,
    WorkflowEngine,
)
from workflow_state import init_workflow_state


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / "src" / "workflow.py"
ORCHESTRATOR_PATH = ROOT / "src" / "orchestrator.py"
PRODUCTION_PATH = ROOT / "src" / "workflow_production.py"
DRY_RUN_PATH = ROOT / "src" / "dry_run_scenarios.py"
FAKE_PATH = ROOT / "tests" / "test_workflow.py"

MANDATORY_SAFETY_CAPABILITIES = frozenset(
    name
    for name in MANDATORY_WORKFLOW_DRIVER_METHODS
    if name.startswith(("persist_", "recover_"))
) | frozenset(
    {
        "active_state",
        "authoritative_native_findings",
        "bind_work_unit",
        "carry_forward_native_findings",
    }
)


def _class_node(tree: ast.Module, name: str) -> ast.ClassDef:
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == name
    )


def _function_node(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _declared_surface(
    tree: ast.Module, class_name: str
) -> tuple[frozenset[str], frozenset[str]]:
    class_node = _class_node(tree, class_name)
    methods = frozenset(
        node.name
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    attributes = frozenset(
        node.target.id
        for node in class_node.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    )
    return methods, attributes


def _self_driver_capabilities(node: ast.AST) -> frozenset[str]:
    return frozenset(
        item.attr
        for item in ast.walk(node)
        if isinstance(item, ast.Attribute)
        and isinstance(item.ctx, ast.Load)
        and isinstance(item.value, ast.Attribute)
        and item.value.attr == "driver"
        and isinstance(item.value.value, ast.Name)
        and item.value.value.id == "self"
    )


def _named_driver_capabilities(node: ast.AST) -> frozenset[str]:
    return frozenset(
        item.attr
        for item in ast.walk(node)
        if isinstance(item, ast.Attribute)
        and isinstance(item.ctx, ast.Load)
        and isinstance(item.value, ast.Name)
        and item.value.id == "driver"
    )


def _dynamic_self_driver_lookups(node: ast.AST) -> tuple[ast.Call, ...]:
    return tuple(
        item
        for item in ast.walk(node)
        if isinstance(item, ast.Call)
        and isinstance(item.func, ast.Name)
        and item.func.id == "getattr"
        and item.args
        and isinstance(item.args[0], ast.Attribute)
        and isinstance(item.args[0].value, ast.Name)
        and item.args[0].value.id == "self"
        and item.args[0].attr == "driver"
    )


def _unbound_self_driver_reads(node: ast.AST) -> tuple[ast.Attribute, ...]:
    parents = {
        id(child): parent
        for parent in ast.walk(node)
        for child in ast.iter_child_nodes(parent)
    }
    allowed_validators = {"require_driver_capabilities", "require_workflow_driver"}
    unbound: list[ast.Attribute] = []
    for item in ast.walk(node):
        if not (
            isinstance(item, ast.Attribute)
            and isinstance(item.ctx, ast.Load)
            and item.attr == "driver"
            and isinstance(item.value, ast.Name)
            and item.value.id == "self"
        ):
            continue
        parent = parents[id(item)]
        capability_access = isinstance(parent, ast.Attribute) and parent.value is item
        validator_call = (
            isinstance(parent, ast.Call)
            and isinstance(parent.func, ast.Name)
            and parent.func.id in allowed_validators
        )
        if not capability_access and not validator_call:
            unbound.append(item)
    return tuple(unbound)


def test_workflow_engine_driver_inventory_exactly_matches_protocol() -> None:
    tree = ast.parse(WORKFLOW_PATH.read_text(encoding="utf-8"))
    engine = _class_node(tree, "WorkflowEngine")
    protocol_methods, protocol_attributes = _declared_surface(tree, "WorkflowDriver")

    assert not _dynamic_self_driver_lookups(engine)
    assert not _unbound_self_driver_reads(engine)
    assert protocol_methods == MANDATORY_WORKFLOW_DRIVER_METHODS
    assert protocol_attributes == MANDATORY_WORKFLOW_DRIVER_STATE_ATTRIBUTES
    assert OPTIONAL_WORKFLOW_DRIVER_CAPABILITIES == frozenset()
    assert _self_driver_capabilities(engine) == (
        protocol_methods | protocol_attributes
    )


def test_production_loop_driver_inventory_is_protocol_or_explicitly_internal() -> None:
    tree = ast.parse(PRODUCTION_PATH.read_text(encoding="utf-8"))
    loop = _function_node(tree, "run_production_workflow")
    loop_capabilities = _named_driver_capabilities(loop)
    workflow_capabilities = (
        MANDATORY_WORKFLOW_DRIVER_METHODS
        | MANDATORY_WORKFLOW_DRIVER_STATE_ATTRIBUTES
    )
    internal_methods, internal_attributes = _declared_surface(
        tree, "ProductionWorkflowLoopDriver"
    )

    assert not internal_attributes
    assert internal_methods == PRODUCTION_LOOP_INTERNAL_DRIVER_METHODS
    assert loop_capabilities - workflow_capabilities == internal_methods
    assert loop_capabilities <= workflow_capabilities | internal_methods


@pytest.mark.parametrize(
    ("path", "class_name"),
    (
        (ORCHESTRATOR_PATH, "ProductionWorkflowDriver"),
        (DRY_RUN_PATH, "ScriptedWorkflowDriver"),
        (FAKE_PATH, "FakeDriver"),
    ),
)
def test_concrete_drivers_declare_the_complete_workflow_surface(
    path: Path, class_name: str
) -> None:
    methods, attributes = _declared_surface(
        ast.parse(path.read_text(encoding="utf-8")), class_name
    )

    assert MANDATORY_WORKFLOW_DRIVER_METHODS <= methods
    assert MANDATORY_WORKFLOW_DRIVER_STATE_ATTRIBUTES <= attributes


def test_incomplete_driver_fails_before_first_bound_use() -> None:
    class IncompleteDriver:
        active_state = None

        def invoke_codex(self, _invocation: object) -> object:
            raise AssertionError("provider must not be called")

    state = init_workflow_state(
        run_id="s4c-contract",
        task_file="inbox/s4c.md",
        branch="feature/state-authority-consolidation",
        branch_base="a" * 40,
        slice_count=1,
        timestamp="2026-09-01T00:00:00+00:00",
    )
    original_state = state

    with pytest.raises(WorkflowDriverContractError) as exc_info:
        WorkflowEngine(IncompleteDriver()).run_current_work_unit(state, None)  # type: ignore[arg-type]

    assert "persist_gate_transition" in str(exc_info.value)
    assert state == original_state


def test_gate_reframe_rejects_missing_collector_before_state_transition() -> None:
    state = init_workflow_state(
        run_id="s4c-gate-reframe",
        task_file="inbox/s4c.md",
        branch="feature/state-authority-consolidation",
        branch_base="a" * 40,
        slice_count=1,
        timestamp="2026-09-01T00:00:00+00:00",
    )

    with pytest.raises(WorkflowDriverContractError) as exc_info:
        WorkflowEngine(object()).reframe_unexpected_path_stop_gate(state)  # type: ignore[arg-type]

    assert "gate-reframing driver" in str(exc_info.value)
    assert "missing=collect_changes" in str(exc_info.value)
    assert state.current_work_unit_id == 1


def test_production_internal_driver_contract_fails_before_loop_use() -> None:
    members = {
        name: (lambda _self, *_args, **_kwargs: None)
        for name in MANDATORY_WORKFLOW_DRIVER_METHODS
    }
    members["active_state"] = None
    CoreOnlyDriver = type("CoreOnlyDriver", (), members)

    with pytest.raises(WorkflowDriverContractError) as exc_info:
        require_production_workflow_loop_driver(CoreOnlyDriver())

    assert "internal surface" in str(exc_info.value)
    assert "finalize_audit" in str(exc_info.value)


def test_driver_alias_mutation_breaks_static_inventory_guard() -> None:
    source = WORKFLOW_PATH.read_text(encoding="utf-8")
    mutated = source.replace(
        "        current = state.current_work_unit\n",
        "        driver_alias = self.driver\n"
        "        current = state.current_work_unit\n",
        1,
    )
    mutated_engine = _class_node(ast.parse(mutated), "WorkflowEngine")

    assert _unbound_self_driver_reads(mutated_engine)


@pytest.mark.parametrize("capability", sorted(MANDATORY_SAFETY_CAPABILITIES))
def test_mandatory_capability_name_mutation_breaks_static_inventory(
    capability: str,
) -> None:
    source = WORKFLOW_PATH.read_text(encoding="utf-8")
    original = f"self.driver.{capability}"
    assert original in source
    mutated = source.replace(
        original, f"self.driver.s4c_undeclared_{capability}", 1
    )
    mutated_engine = _class_node(ast.parse(mutated), "WorkflowEngine")

    assert _self_driver_capabilities(mutated_engine) != (
        MANDATORY_WORKFLOW_DRIVER_METHODS
        | MANDATORY_WORKFLOW_DRIVER_STATE_ATTRIBUTES
    )
