from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR = ROOT / "src/orchestrator.py"

# B38 decision inventory. Moving collect_changes behind a service boundary would
# have to expose all twelve bindings: four driver-state reads, five mutations of
# driver-owned state, and three driver operations. That exceeds the three-edge
# stop condition and would split final-review cache lifetime from its append-only
# authority, so the cohesive unit intentionally remains on the driver.
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


def _production_driver(tree: ast.Module) -> ast.ClassDef:
    matches = tuple(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ProductionWorkflowDriver"
    )
    assert len(matches) == 1
    return matches[0]


def _collect_changes_method(driver: ast.ClassDef) -> ast.FunctionDef:
    matches = tuple(
        node
        for node in driver.body
        if isinstance(node, ast.FunctionDef) and node.name == "collect_changes"
    )
    assert len(matches) == 1
    return matches[0]


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
    method = _collect_changes_method(_production_driver(ast.parse(source)))
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
