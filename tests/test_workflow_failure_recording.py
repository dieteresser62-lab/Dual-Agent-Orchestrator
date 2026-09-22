from __future__ import annotations

import ast
import inspect
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import workflow_failure_recording as failure_recording_module

from agent_runtime import AgentInvocationError
from contracts import AgentRole
from orchestrator_diagnostics import STRUCTURED_OUTPUT_DIAGNOSTIC_CODE
from workflow_failure_recording import (
    CONTRACT_REJECTION_BUDGET,
    TRANSPORT_FAILURE_BUDGET,
    WorkflowFailureRecording,
    WorkflowFailureRecordingDependencies,
    _native_retry_budget,
)
from workflow_state import AgentFailureKind


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
FAILURE_PATH = SRC / "workflow_failure_recording.py"
WORKFLOW_PATH = SRC / "workflow.py"

EXPECTED_INTERNAL_IMPORTS = {
    "agent_runtime",
    "artifact_models",
    "contracts",
    "error_classification",
    "orchestrator_diagnostics",
    "workflow_state",
}
EXPECTED_FAILURE_EDGES = {
    "current_invocation_fingerprint",
    "write_invocation_failure_diagnostic",
    "persist_invocation_failure",
    "checkpoint",
    "now",
    "execution_error",
}


def _mutated_native_retry_budget(old: str, new: str):
    source = textwrap.dedent(inspect.getsource(_native_retry_budget))
    assert source.count(old) == 1
    namespace = {
        "STRUCTURED_OUTPUT_DIAGNOSTIC_CODE": STRUCTURED_OUTPUT_DIAGNOSTIC_CODE,
        "TRANSPORT_FAILURE_BUDGET": TRANSPORT_FAILURE_BUDGET,
        "CONTRACT_REJECTION_BUDGET": CONTRACT_REJECTION_BUDGET,
    }
    exec(compile(source.replace(old, new), "<retry-budget-mutant>", "exec"), namespace)
    return namespace["_native_retry_budget"]


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
    diagnostic = next(
        node
        for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "write_invocation_failure_diagnostic"
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
    return diagnostic.lineno < persist.lineno < state_change.lineno < checkpoint.lineno


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
        "write_invocation_failure_diagnostic",
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


def test_diagnostic_exception_escape_mutation_is_killed() -> None:
    source = textwrap.dedent(inspect.getsource(WorkflowFailureRecording))
    marker = "        except Exception as exc:\n"
    assert source.count(marker) == 1

    class _MutationDoesNotCatchPermissionError(Exception):
        pass

    class StubState:
        current_step = SimpleNamespace(value="codex_implementation")

        def record_invocation_failure(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            return self

    def compiled_owner(owner_source: str):  # type: ignore[no-untyped-def]
        namespace = dict(vars(failure_recording_module))
        namespace.update(
            _MutationDoesNotCatchPermissionError=(
                _MutationDoesNotCatchPermissionError
            ),
            _invocation_retry_decision=lambda **_kwargs: SimpleNamespace(
                automatic=False,
                matching_failures=(),
                exhausted_budget=None,
                transport_failures=1,
                max_transport_failures=3,
                contract_rejections=0,
                max_contract_rejections=3,
                response_diagnostics=SimpleNamespace(
                    provider_subtype="none",
                    readable_rejection=None,
                    implementer_readable_rejection=None,
                ),
            ),
            _invocation_failure_documents=lambda **_kwargs: (
                object(),
                SimpleNamespace(
                    invocation_id="diagnostic-mutation",
                    orchestrator_diagnostic=None,
                ),
                "2026-09-09T08:00:00+00:00",
            ),
            _log_invocation_failure=lambda **_kwargs: None,
        )
        exec(
            compile(
                "from __future__ import annotations\n" + owner_source,
                "<diagnostic-exception-mutant>",
                "exec",
            ),
            namespace,
        )
        return namespace["WorkflowFailureRecording"]

    dependencies = WorkflowFailureRecordingDependencies(
        current_invocation_fingerprint=lambda _state: "f" * 64,
        write_invocation_failure_diagnostic=lambda *_args: (_ for _ in ()).throw(
            PermissionError("diagnostic directory is not writable")
        ),
        persist_invocation_failure=lambda _payload: None,
        checkpoint=lambda _state, _history: None,
        now=lambda: datetime(2026, 9, 9, 8, 0, tzinfo=timezone.utc),
        execution_error=RuntimeError,
    )
    error = AgentInvocationError(
        agent_key="codex",
        kind=AgentFailureKind.NETWORK,
        invocation_id="diagnostic-mutation",
        provider_text="provider output",
        technical_text="technical output",
        received_at=datetime(2026, 9, 9, 8, 0, tzinfo=timezone.utc),
    )
    call = (
        StubState(),
        None,
        None,
        AgentRole.CODEX,
        error,
    )

    baseline_owner = compiled_owner(source)
    baseline_owner(dependencies).persist_invocation_failure(*call)

    escaped_owner = compiled_owner(
        source.replace(
            marker,
            "        except _MutationDoesNotCatchPermissionError as exc:\n",
            1,
        )
    )
    with pytest.raises(
        PermissionError, match="diagnostic directory is not writable"
    ):
        escaped_owner(dependencies).persist_invocation_failure(*call)


def test_split_counter_proof_kills_shared_counter_mutation() -> None:
    expected = _native_retry_budget("NATIVE-REVIEW-FORM", 2, 0, 3, 3)
    mutant = _mutated_native_retry_budget(
        "contract_rejections = prior_contract_rejections + int(is_contract_rejection)",
        "contract_rejections = prior_transport_failures + "
        "prior_contract_rejections + int(is_contract_rejection)",
    )

    assert expected == (2, 1, CONTRACT_REJECTION_BUDGET, True)
    assert mutant("NATIVE-REVIEW-FORM", 2, 0, 3, 3) != expected


def test_transport_limit_proof_kills_inclusive_boundary_mutation() -> None:
    expected = _native_retry_budget(
        STRUCTURED_OUTPUT_DIAGNOSTIC_CODE, 2, 0, 3, 3
    )
    mutant = _mutated_native_retry_budget(
        "transport_failures < max_transport_failures",
        "transport_failures <= max_transport_failures",
    )

    assert expected == (3, 0, TRANSPORT_FAILURE_BUDGET, False)
    assert mutant(STRUCTURED_OUTPUT_DIAGNOSTIC_CODE, 2, 0, 3, 3) != expected


def test_contract_limit_proof_kills_inclusive_boundary_mutation() -> None:
    expected = _native_retry_budget("NATIVE-REVIEW-FORM", 0, 2, 3, 3)
    mutant = _mutated_native_retry_budget(
        "contract_rejections < max_contract_rejections",
        "contract_rejections <= max_contract_rejections",
    )

    assert expected == (0, 3, CONTRACT_REJECTION_BUDGET, False)
    assert mutant("NATIVE-REVIEW-FORM", 0, 2, 3, 3) != expected


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
