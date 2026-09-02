from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
FAILURE_PATH = SRC / "workflow_failure_recording.py"
WORKFLOW_PATH = SRC / "workflow.py"

EXPECTED_INTERNAL_IMPORTS = {
    "agent_runtime",
    "artifact_models",
    "contracts",
    "error_classification",
    "workflow_state",
}
EXPECTED_FAILURE_EDGES = {
    "current_invocation_fingerprint",
    "persist_invocation_failure",
    "checkpoint",
    "now",
    "execution_error",
}


def _tree(path: Path = FAILURE_PATH, source: str | None = None) -> ast.Module:
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
    owner = _class(_tree(source=source), "WorkflowFailureRecording")
    return {
        node.attr
        for node in ast.walk(owner)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "_dependencies"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "self"
    }


def _decision_ahead_order(source: str) -> bool:
    owner = _class(_tree(source=source), "WorkflowFailureRecording")
    method = next(
        node
        for node in owner.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "persist_invocation_failure"
    )
    persist = next(
        node
        for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "persist_invocation_failure"
    )
    state_change = next(
        node
        for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "record_invocation_failure"
    )
    checkpoint = next(
        node
        for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "checkpoint"
    )
    return persist.lineno < state_change.lineno < checkpoint.lineno


def test_failure_recording_has_exact_one_way_inventory() -> None:
    assert _internal_imports(FAILURE_PATH) == EXPECTED_INTERNAL_IMPORTS
    assert "workflow" not in _internal_imports(FAILURE_PATH)
    importers: list[str] = []
    for path in sorted(SRC.glob("*.py")):
        if path == FAILURE_PATH:
            continue
        tree = _tree(path)
        if any(
            isinstance(node, ast.ImportFrom)
            and node.module == "workflow_failure_recording"
            or isinstance(node, ast.Import)
            and any(alias.name == "workflow_failure_recording" for alias in node.names)
            for node in ast.walk(tree)
        ):
            importers.append(path.relative_to(ROOT).as_posix())
    assert importers == ["src/workflow.py"]
    assert _dependency_edges() == EXPECTED_FAILURE_EDGES


@pytest.mark.parametrize(
    "edge",
    (
        "current_invocation_fingerprint",
        "persist_invocation_failure",
        "checkpoint",
        "now",
    ),
)
def test_omitted_failure_recording_edge_turns_inventory_red(edge: str) -> None:
    source = FAILURE_PATH.read_text(encoding="utf-8")
    marker = f"self._dependencies.{edge}"
    assert marker in source
    mutated = source.replace(marker, f"omitted_{edge}")
    assert edge not in _dependency_edges(mutated)
    assert _dependency_edges(mutated) != EXPECTED_FAILURE_EDGES


def test_failure_record_is_appended_before_retry_state_and_mutation_turns_red() -> None:
    source = FAILURE_PATH.read_text(encoding="utf-8")
    assert _decision_ahead_order(source)
    persist = "        self._dependencies.persist_invocation_failure(payload)\n"
    state_change = (
        "        state = state.record_invocation_failure(\n"
        "            record, wait_automatically=automatic, updated_at=decision_at\n"
        "        )\n"
    )
    assert persist in source and state_change in source
    mutated = source.replace(
        persist + state_change,
        state_change + persist,
        1,
    )
    assert not _decision_ahead_order(mutated)


def test_engine_failure_facade_is_thin_and_retry_control_stays_in_engine() -> None:
    engine = _class(_tree(WORKFLOW_PATH), "WorkflowEngine")
    methods = {
        node.name: node for node in engine.body if isinstance(node, ast.FunctionDef)
    }
    facade = ast.unparse(methods["_persist_invocation_failure"])
    assert "self._failure_recording.persist_invocation_failure" in facade
    assert "InvocationFailurePayload" not in facade

    invoke_method = methods["_invoke_role"]
    invoke = ast.unparse(invoke_method)
    assert "wait_until_quota_resume" in invoke
    assert "wait_until_transient_retry" in invoke
    assert "self._persist_invocation_failure" in invoke
    assert "WorkflowFailureRecordingDependencies" not in invoke
    failure_record = next(
        node
        for node in ast.walk(invoke_method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_persist_invocation_failure"
    )
    waits = tuple(
        node
        for node in ast.walk(invoke_method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id
        in {"wait_until_quota_resume", "wait_until_transient_retry"}
    )
    assert len(waits) == 2
    assert all(failure_record.lineno < wait.lineno for wait in waits)

    initializer = methods["__init__"]
    dependency_call = next(
        node
        for node in ast.walk(initializer)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "WorkflowFailureRecordingDependencies"
    )
    bindings = {
        keyword.arg: keyword.value
        for keyword in dependency_call.keywords
        if keyword.arg is not None
    }
    assert isinstance(bindings["current_invocation_fingerprint"], ast.Lambda)
    assert isinstance(bindings["now"], ast.Lambda)
