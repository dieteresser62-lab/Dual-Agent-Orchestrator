from __future__ import annotations

import ast
import copy
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR = ROOT / "src/orchestrator.py"

# B38 decision inventory, evaluated over the B63 helpers inlined at their call
# sites. The cut reduces the physical method size without changing the logical
# driver-owned final-review cache unit or the twelve service-boundary edges.
COLLECT_CHANGES_COUPLINGS = {
    "read_only": frozenset(
        {
            "_artifact_bridge",
            "active_state",
            "last_codex_output",
            "root",
        }
    ),
    "mutated": frozenset(
        {
            "_final_review_evidence_collections",
            "_final_review_evidence_reuses",
            "_final_review_evidence_snapshot",
            "_rendered_changes",
            "_repository_changes",
        }
    ),
    "delegated": frozenset(
        {
            "_final_review_cache_authorized_digest",
            "_final_review_evidence_cache_path",
            "_write_side_effect_file",
        }
    ),
}
FINAL_REVIEW_CACHE_UNIT = frozenset(
    {
        "_final_review_cache_authorized_digest",
        "_final_review_evidence_cache_path",
        "_final_review_evidence_collections",
        "_final_review_evidence_reuses",
        "_final_review_evidence_snapshot",
    }
)
COLLECT_CHANGE_HELPERS = frozenset(
    {
        "_collect_change_path_selection",
        "_reuse_final_review_evidence",
        "_collect_fresh_final_review_evidence",
        "_read_semantic_plan_artifact",
        "_build_final_review_snapshot",
        "_final_review_cache_target_digest",
    }
)


def _production_driver(tree: ast.Module) -> ast.ClassDef:
    matches = tuple(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ProductionWorkflowDriver"
    )
    assert len(matches) == 1
    return matches[0]


def _driver_method(driver: ast.ClassDef, name: str) -> ast.FunctionDef:
    matches = tuple(
        node
        for node in driver.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    assert len(matches) == 1
    return matches[0]


def _direct_helper_call(statement: ast.stmt) -> tuple[str, ast.Call] | None:
    value: ast.expr | None = None
    if isinstance(statement, ast.Expr):
        value = statement.value
    elif isinstance(statement, ast.Assign):
        value = statement.value
    if (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and isinstance(value.func.value, ast.Name)
        and value.func.value.id == "self"
        and value.func.attr in COLLECT_CHANGE_HELPERS
    ):
        return value.func.attr, value
    return None


def _logical_collect_changes(driver: ast.ClassDef) -> ast.FunctionDef:
    method = copy.deepcopy(_driver_method(driver, "collect_changes"))
    helpers = {
        name: copy.deepcopy(_driver_method(driver, name))
        for name in COLLECT_CHANGE_HELPERS
    }

    def expand_body(body: list[ast.stmt]) -> list[ast.stmt]:
        expanded: list[ast.stmt] = []
        for statement in body:
            direct = _direct_helper_call(statement)
            if direct is not None:
                helper_name, call = direct
                helper = helpers[helper_name]
                parameters = [argument.arg for argument in helper.args.args]
                assert parameters[0] == "self"
                assert not call.keywords
                assert [ast.unparse(argument) for argument in call.args] == parameters[1:]
                helper_body = copy.deepcopy(helper.body)
                if (
                    helper_body
                    and isinstance(helper_body[0], ast.Expr)
                    and isinstance(helper_body[0].value, ast.Constant)
                    and isinstance(helper_body[0].value.value, str)
                ):
                    helper_body.pop(0)
                helper_body = expand_body(helper_body)
                if isinstance(statement, ast.Assign):
                    terminal = helper_body[-1]
                    assert isinstance(terminal, ast.Return)
                    assert terminal.value is not None
                    assert len(statement.targets) == 1
                    if ast.unparse(statement.targets[0]) == ast.unparse(terminal.value):
                        helper_body.pop()
                    else:
                        helper_body[-1] = ast.Assign(
                            targets=copy.deepcopy(statement.targets),
                            value=terminal.value,
                        )
                else:
                    assert not any(
                        isinstance(node, ast.Return) for node in ast.walk(helper)
                    )
                expanded.extend(helper_body)
                continue
            for field in ("body", "orelse", "finalbody"):
                child = getattr(statement, field, None)
                if isinstance(child, list) and child:
                    setattr(statement, field, expand_body(child))
            if isinstance(statement, ast.Try):
                for handler in statement.handlers:
                    handler.body = expand_body(handler.body)
            expanded.append(statement)
        return expanded

    method.body = expand_body(method.body)
    ast.fix_missing_locations(method)
    return method


def _self_member(node: ast.AST) -> str | None:
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    ):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _self_member(node.value)
    return None


def _collect_changes_inventory(source: str) -> tuple[
    frozenset[str], frozenset[str], frozenset[str]
]:
    method = _logical_collect_changes(_production_driver(ast.parse(source)))
    couplings = frozenset(
        node.attr
        for node in ast.walk(method)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )
    mutated: set[str] = set()
    for node in ast.walk(method):
        targets: tuple[ast.AST, ...] = ()
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = (
                tuple(node.targets)
                if isinstance(node, ast.Assign)
                else (node.target,)
            )
        for target in targets:
            member = _self_member(target)
            if member is not None:
                mutated.add(member)
    delegated = frozenset(
        member
        for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and (member := _self_member(node.func)) is not None
    )
    return couplings, frozenset(mutated), delegated


def _assert_collect_changes_cohesion(source: str) -> None:
    couplings, mutated, delegated = _collect_changes_inventory(source)
    expected = frozenset().union(*COLLECT_CHANGES_COUPLINGS.values())
    missing_cache_bindings = FINAL_REVIEW_CACHE_UNIT - couplings

    assert not missing_cache_bindings, (
        "final-review cache binding left the driver: "
        + ", ".join(sorted(missing_cache_bindings))
    )
    assert couplings == expected, "collect_changes driver coupling inventory changed"
    assert mutated == COLLECT_CHANGES_COUPLINGS["mutated"]
    assert delegated == COLLECT_CHANGES_COUPLINGS["delegated"]
    assert couplings - mutated - delegated == COLLECT_CHANGES_COUPLINGS["read_only"]


def test_collect_changes_remains_with_its_driver_owned_final_review_cache() -> None:
    _assert_collect_changes_cohesion(ORCHESTRATOR.read_text(encoding="utf-8"))


@pytest.mark.parametrize("binding", sorted(FINAL_REVIEW_CACHE_UNIT))
def test_collect_changes_cohesion_guard_detects_a_detached_cache_binding(
    binding: str,
) -> None:
    source = ORCHESTRATOR.read_text(encoding="utf-8")
    mutated = source.replace(f"self.{binding}", f"detached.{binding}")

    assert mutated != source
    with pytest.raises(AssertionError, match=rf"cache binding left the driver: .*{binding}"):
        _assert_collect_changes_cohesion(mutated)
