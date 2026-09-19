from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from artifact_bridge import ArtifactBridge
from artifact_models import (
    FingerprintKind,
    RoleProfilePayload,
    ReviewPayload,
    Role,
    RunIdentityPayload,
    RunProfilePayload,
)
from artifact_replay import replay_artifacts
from artifact_store import ArtifactStore
from contracts import AgentRole, FindingClass, FindingOrigin, FindingRecord, FindingStatus
from test_workflow import _attestation, _changes, _completed_single_slice_state, _context
from workflow import WorkflowExecutionError, WorkflowHistory
from workflow_recovery import WorkflowRecovery, WorkflowRecoveryDependencies
from workflow_state import WorkflowStep


ROOT = Path(__file__).resolve().parents[1]
RECOVERY_PATH = ROOT / "src/workflow_recovery.py"

EXPECTED_INTERNAL_IMPORTS = {
    "agent_runtime",
    "artifact_bridge",
    "artifact_models",
    "artifact_replay",
    "contracts",
    "final_review_preflight",
    "finding_order",
    "finding_reducer",
    "gates",
    "git_service",
    "native_codex_contract",
    "native_codex_request",
    "native_review_contract",
    "native_review_request",
    "provider_input_budget",
    "side_effects",
    "state_io",
    "workflow",
    "workflow_state",
}

EXPECTED_RECOVERY_EDGES = {
    "_reconcile_pending_side_effects": {
        "artifact_bridge",
        "reconcile_external_attempt",
        "root",
    },
    "_start_provider_attempt": {
        "active_state",
        "agent_profile",
        "artifact_bridge",
        "attempt_response_path",
        "mark_side_effect_completed",
        "reconcile_external_attempt",
        "root",
        "side_effect_executor",
        "side_effect_spec",
    },
    "recover_pending_native_implementer": {
        "active_state",
        "artifact_bridge",
        "canonical_agent_result",
        "content_text",
        "load_agent_request_bundle",
        "persist_implementer_contract",
        "store_implementer_output",
    },
    "recover_pending_native_reviewer": {
        "active_state",
        "artifact_bridge",
        "content_text",
        "persist_review_contract",
    },
    "recover_pending_native_reviewer_before_policy": {
        "active_state",
        "artifact_bridge",
        "content_text",
        "persist_review_contract",
    },
}
RECOVERY_EDGE_HELPERS = {
    "recover_pending_native_implementer": (
        "_bind_native_implementer_request",
        "_load_original_implementer_request_bundle",
        "_replay_native_implementer_request_findings",
        "_bind_native_implementer_request_findings",
        "_parse_native_implementer_recovery",
    ),
    "recover_pending_native_reviewer_before_policy": (
        "_replay_pending_native_reviewer",
        "_build_pending_native_reviewer_context",
        "_parse_pending_native_reviewer_response",
    ),
}


def _recovery_tree(source: str | None = None) -> ast.Module:
    return ast.parse(
        RECOVERY_PATH.read_text(encoding="utf-8") if source is None else source
    )


def _recovery_dependency_edges(source: str | None = None) -> dict[str, set[str]]:
    tree = _recovery_tree(source)
    recovery = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "WorkflowRecovery"
    )
    functions = {
        node.name: node
        for node in recovery.body
        if isinstance(node, ast.FunctionDef)
    }
    result: dict[str, set[str]] = {}
    for method in EXPECTED_RECOVERY_EDGES:
        nodes = [
            functions[name]
            for name in (method, *RECOVERY_EDGE_HELPERS.get(method, ()))
        ]
        result[method] = {
            item.attr
            for node in nodes
            for item in ast.walk(node)
            if isinstance(item, ast.Attribute)
            and isinstance(item.value, ast.Attribute)
            and item.value.attr == "_dependencies"
            and isinstance(item.value.value, ast.Name)
            and item.value.value.id == "self"
        }
    return result


def _assert_recovery_edges(source: str | None = None) -> None:
    actual = _recovery_dependency_edges(source)
    assert set(actual) == set(EXPECTED_RECOVERY_EDGES)
    for method, expected in EXPECTED_RECOVERY_EDGES.items():
        missing = expected - actual[method]
        unexpected = actual[method] - expected
        assert not missing and not unexpected, (
            f"{method}: missing recovery edges {sorted(missing)!r}; "
            f"unexpected edges {sorted(unexpected)!r}"
        )


def _unexpected_dependency(*_args: object, **_kwargs: object) -> Any:
    raise AssertionError("an unavailable recovery dependency was invoked")


def _dependencies(
    root: Path,
    *,
    bridge: ArtifactBridge | None = None,
) -> WorkflowRecoveryDependencies:
    return WorkflowRecoveryDependencies(
        root=root,
        artifact_bridge=lambda: bridge,
        active_state=lambda: None,
        reconcile_external_attempt=_unexpected_dependency,
        side_effect_executor=_unexpected_dependency,
        side_effect_spec=_unexpected_dependency,
        mark_side_effect_completed=_unexpected_dependency,
        attempt_response_path=_unexpected_dependency,
        canonical_agent_result=_unexpected_dependency,
        load_agent_request_bundle=_unexpected_dependency,
        content_text=_unexpected_dependency,
        persist_implementer_contract=_unexpected_dependency,
        persist_review_contract=_unexpected_dependency,
        store_implementer_output=_unexpected_dependency,
        agent_profile=_unexpected_dependency,
    )


def test_recovery_module_has_one_way_imports_and_complete_dependency_inventory() -> None:
    tree = _recovery_tree()
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

    importers = []
    for path in sorted((ROOT / "src").glob("*.py")):
        if path == RECOVERY_PATH:
            continue
        candidate = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            isinstance(node, ast.ImportFrom) and node.module == "workflow_recovery"
            for node in ast.walk(candidate)
        ):
            importers.append(path.relative_to(ROOT).as_posix())
    assert importers == ["src/orchestrator.py", "src/workflow_baseline.py"]
    _assert_recovery_edges()


@pytest.mark.parametrize(
    ("edge", "method"),
    (
        ("persist_implementer_contract", "recover_pending_native_implementer"),
        ("persist_review_contract", "recover_pending_native_reviewer"),
        ("reconcile_external_attempt", "_reconcile_pending_side_effects"),
    ),
)
def test_omitted_recovery_or_persistence_edge_turns_inventory_red(
    edge: str,
    method: str,
) -> None:
    source = RECOVERY_PATH.read_text(encoding="utf-8")
    marker = f"self._dependencies.{edge}"
    assert marker in source
    mutated = source.replace(marker, f"omitted_{edge}")
    with pytest.raises(AssertionError, match=rf"{method}: missing recovery edges"):
        _assert_recovery_edges(mutated)


def test_recovery_fails_closed_without_driver_owned_durable_context(
    tmp_path: Path,
) -> None:
    recovery = WorkflowRecovery(_dependencies(tmp_path))
    assert not recovery._reconcile_pending_side_effects(cast(Any, object()))
    with pytest.raises(
        WorkflowExecutionError,
        match="provider attempt start requires its durable measurement",
    ):
        recovery._start_provider_attempt(
            cast(Any, object()),
            None,
            durable_response_path=tmp_path / "response.json",
        )


def test_recovery_directly_completes_a_durable_internal_intent(
    tmp_path: Path,
) -> None:
    run_id = "workflow-recovery-module"
    digest = "a" * 64
    store = ArtifactStore(tmp_path, run_id)
    writer = ArtifactBridge(store, now=lambda: "2026-09-02T10:00:00+00:00")
    writer.append(
        RunIdentityPayload(
            "inbox/backlog/recovery.md",
            "feature/recovery",
            "b" * 40,
            "b" * 40,
            "IMPLEMENT",
            None,
        ),
        logical_id="run-identity",
        idempotency_key="run-identity",
        fingerprint_sha256=digest,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    writer.append(
        RunProfilePayload(
            RoleProfilePayload("implementer-model", "medium"),
            RoleProfilePayload("reviewer-model", "high"),
        ),
        logical_id="run-profile",
        idempotency_key="run-profile",
        fingerprint_sha256=digest,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    bridge = ArtifactBridge(store)
    ledger_operation = ("structured-v2-side-effect-ledger",)
    bridge.record_side_effect_intent(
        effect_class="ledger",
        work_unit_id="run",
        operation=ledger_operation,
        fingerprint_sha256=digest,
    )
    bridge.record_side_effect_result(
        effect_class="ledger",
        work_unit_id="run",
        operation=ledger_operation,
        result="initialized",
        fingerprint_sha256=digest,
    )
    operation = ("checkpoint-boundary",)
    bridge.record_side_effect_intent(
        effect_class="internal",
        work_unit_id="2",
        operation=operation,
        fingerprint_sha256=digest,
    )

    recovery = WorkflowRecovery(_dependencies(tmp_path, bridge=bridge))
    changed = recovery._reconcile_pending_side_effects(
        cast(Any, SimpleNamespace(run_id=run_id))
    )

    assert changed
    replay = replay_artifacts(bridge.store.load_chain(), run_id)
    recovered = next(
        item
        for item in replay.side_effects
        if item.effect_class == "internal" and item.operation == operation
    )
    assert recovered.result == "completed"
