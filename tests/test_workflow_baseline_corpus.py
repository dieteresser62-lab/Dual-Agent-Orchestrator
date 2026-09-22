from __future__ import annotations

import ast
import copy
import json
import subprocess
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, cast

import pytest

import orchestrator
import workflow_baseline as baseline_module
from artifact_models import ArtifactRecord
from artifact_replay import (
    ArtifactReplayError,
    ReplayDiagnostic,
    ReplayDiagnosticCode,
)
from artifact_resume import ArtifactResumeError
from workflow import WorkflowExecutionError
from workflow_baseline import WorkflowBaseline, WorkflowBaselineDependencies
from workflow_state import (
    PlannedSlice,
    ProtocolBinding,
    ProtocolMode,
    WorkflowState,
    WorkflowStep,
    WorkUnitKind,
    init_workflow_state,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "src/workflow_baseline.py"
STATIC_BASELINE = ROOT / "tests/fixtures/workflow-baseline-static-pre-b55-v1.json"
RUNTIME_BASELINE = ROOT / "tests/fixtures/workflow-baseline-runtime-pre-b55-v1.json"
PRE_B56_BASELINE = ROOT / "tests/fixtures/workflow-baseline-pre-b56-v1.json"
HELPER_BINDINGS = (
    ROOT / "tests/fixtures/workflow-baseline-helper-bindings-b56-v1.json"
)
FUNCTION_SIZE_BASELINE = ROOT / "tests/fixtures/function-size-baseline-v1.json"
RECORD_SEQUENCE_BASELINE = (
    ROOT / "tests/fixtures/workflow-record-sequence-baseline-v1.json"
)
SOURCE_COMMIT = "444f5188200814373b7dfcdee1c114e1a9fda8a0"
SOURCE_BLOB = "1b82c80f9840a2193adb5c3ef241a4ecc08f5b42"
PRE_B56_COMMIT = "cb00bc0c4270147c417d4d89c011ca6cb24e6ed1"
RECORD_SEQUENCE_BLOB = "5c135a8002a03359ef097164fe4b6d3ac32f3664"
STAMP = "2026-09-05T00:00:00+00:00"
BRANCH_BASE = "b" * 40
TASK_DIGEST = "a" * 64

BASELINE_HELPERS = {
    "matches_baseline_initialization_prefix": (
        "_append_baseline_identity_expectations",
        "_append_baseline_transition_expectations",
        "_append_baseline_contract_expectations",
    ),
    "_persist_structured_baseline": (
        "_replay_existing_baseline_chain",
        "_require_existing_baseline_prefix",
        "_append_baseline_identity_and_ledger",
        "_append_completed_internal_effects",
        "_append_baseline_state_facts",
    ),
}
CAUGHT_HELPERS = {
    "_replay_existing_baseline_chain": "ArtifactReplayError",
    "_require_existing_baseline_prefix": "ArtifactResumeError",
}


@dataclass(frozen=True)
class DecisionInput:
    scenario_id: str
    target_condition: int | None
    reference_state: WorkflowState
    reference_records: tuple[ArtifactRecord, ...]
    state: WorkflowState
    records: tuple[ArtifactRecord, ...]
    expected: bool
    rationale: str


@dataclass(frozen=True)
class RuntimeCorpus:
    document: dict[str, object]
    decisions: dict[str, DecisionInput]


def _function(
    tree: ast.Module, name: str, *, class_name: str | None = None
) -> ast.FunctionDef:
    body: list[ast.stmt] = tree.body
    if class_name is not None:
        owner = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        )
        body = owner.body
    return next(
        node for node in body if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _ordered(node: ast.AST, node_type: type[ast.AST]) -> list[Any]:
    return sorted(
        (item for item in ast.walk(node) if isinstance(item, node_type)),
        key=lambda item: (item.lineno, item.col_offset),
    )


def _read_names(node: ast.AST) -> list[str]:
    return sorted(
        {
            item.id
            for item in ast.walk(node)
            if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load)
        }
    )


def _parent_map(node: ast.AST) -> dict[ast.AST, ast.AST]:
    return {
        child: parent
        for parent in ast.walk(node)
        for child in ast.iter_child_nodes(parent)
    }


def _caught_type(
    node: ast.Raise, parents: dict[ast.AST, ast.AST]
) -> str | None:
    parent: ast.AST | None = node
    while parent in parents:
        parent = parents[parent]
        if isinstance(parent, ast.ExceptHandler):
            return ast.unparse(parent.type)
    return None


def _raise_detail(
    node: ast.Raise, parents: dict[ast.AST, ast.AST]
) -> tuple[str, str]:
    if node.exc is None:
        return cast(str, _caught_type(node, parents)), "re-raise caught exception unchanged"
    if isinstance(node.exc, ast.Call):
        exception_type = ast.unparse(node.exc.func)
        message = ast.unparse(node.exc.args[0]) if node.exc.args else ""
        return exception_type, message
    return ast.unparse(node.exc), ""


def _direct_baseline_helper_call(
    statement: ast.stmt, helper_names: frozenset[str]
) -> tuple[str, ast.Call] | None:
    value: ast.expr | None = None
    if isinstance(statement, ast.Expr):
        value = statement.value
    elif isinstance(statement, ast.Assign):
        value = statement.value
    if not isinstance(value, ast.Call):
        return None
    if isinstance(value.func, ast.Name) and value.func.id in helper_names:
        return value.func.id, value
    if (
        isinstance(value.func, ast.Attribute)
        and isinstance(value.func.value, ast.Name)
        and value.func.value.id == "self"
        and value.func.attr in helper_names
    ):
        return value.func.attr, value
    return None


def _logical_baseline_function(
    tree: ast.Module, name: str, *, class_name: str | None = None
) -> ast.FunctionDef:
    function = copy.deepcopy(_function(tree, name, class_name=class_name))
    helper_names = BASELINE_HELPERS[name]
    helpers = {
        helper_name: copy.deepcopy(
            _function(
                tree,
                helper_name,
                class_name=(
                    "WorkflowBaseline"
                    if name == "_persist_structured_baseline"
                    else None
                ),
            )
        )
        for helper_name in helper_names
    }
    observed_calls = [
        (
            call.func.id
            if isinstance(call.func, ast.Name)
            else cast(ast.Attribute, call.func).attr
        )
        for call in _ordered(function, ast.Call)
        if (
            isinstance(call.func, ast.Name)
            and call.func.id in helper_names
        )
        or (
            isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "self"
            and call.func.attr in helper_names
        )
    ]
    assert observed_calls == list(helper_names)

    def expand_body(body: list[ast.stmt]) -> list[ast.stmt]:
        expanded: list[ast.stmt] = []
        for statement in body:
            direct = _direct_baseline_helper_call(
                statement, frozenset(helper_names)
            )
            if direct is not None:
                helper_name, call = direct
                helper = helpers[helper_name]
                parameters = [argument.arg for argument in helper.args.args]
                if class_name is not None:
                    assert parameters[0] == "self"
                    parameters = parameters[1:]
                assert not call.keywords
                assert [ast.unparse(argument) for argument in call.args] == parameters
                helper_body = copy.deepcopy(helper.body)
                if isinstance(statement, ast.Assign):
                    terminal = helper_body[-1]
                    assert isinstance(terminal, ast.Return)
                    assert terminal.value is not None
                    helper_body[-1] = ast.Assign(
                        targets=copy.deepcopy(statement.targets),
                        value=terminal.value,
                    )
                else:
                    assert not any(
                        isinstance(item, ast.Return) for item in ast.walk(helper)
                    )
                expanded.extend(expand_body(helper_body))
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

    function.body = expand_body(function.body)
    ast.fix_missing_locations(function)
    logical = ast.parse(ast.unparse(function)).body[0]
    assert isinstance(logical, ast.FunctionDef)
    return logical


def _pre_cut_line_count(name: str, *, class_name: str | None = None) -> int:
    source = subprocess.run(
        ["git", "show", f"{PRE_B56_COMMIT}:src/workflow_baseline.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    function = _function(ast.parse(source), name, class_name=class_name)
    return function.end_lineno - function.lineno + 1


def _static_function(
    tree: ast.Module, name: str, *, class_name: str | None = None
) -> dict[str, object]:
    function = _logical_baseline_function(tree, name, class_name=class_name)
    parents = _parent_map(function)
    conditions = _ordered(function, ast.If)
    returns = _ordered(function, ast.Return)
    raises = _ordered(function, ast.Raise)
    catchers = _ordered(function, ast.ExceptHandler)
    qualified = name if class_name is None else f"{class_name}.{name}"
    return {
        "function": qualified,
        "line_count": _pre_cut_line_count(name, class_name=class_name),
        "conditions": [
            {
                "ordinal": index,
                "expression": ast.unparse(item.test),
                "read_names": _read_names(item.test),
            }
            for index, item in enumerate(conditions, 1)
        ],
        "returns": [
            {
                "ordinal": index,
                "expression": "None" if item.value is None else ast.unparse(item.value),
            }
            for index, item in enumerate(returns, 1)
        ],
        "aborts": [
            {
                "ordinal": index,
                "exception_type": _raise_detail(item, parents)[0],
                "message": _raise_detail(item, parents)[1],
                "source_expression": ast.unparse(item),
            }
            for index, item in enumerate(raises, 1)
        ],
        "catchers": [
            {"ordinal": index, "exception_type": ast.unparse(item.type)}
            for index, item in enumerate(catchers, 1)
        ],
    }


def _static_document() -> dict[str, object]:
    tree = ast.parse(SOURCE_PATH.read_text("utf-8"))
    return {
        "schema_version": "workflow-baseline-static-pre-b55-v1",
        "source_commit": SOURCE_COMMIT,
        "source_blob": SOURCE_BLOB,
        "functions": [
            _static_function(tree, "matches_baseline_initialization_prefix"),
            _static_function(
                tree,
                "_persist_structured_baseline",
                class_name="WorkflowBaseline",
            ),
        ],
    }


def _state(
    run_id: str,
    *,
    slice_count: int = 1,
    bind_plan: bool = False,
) -> WorkflowState:
    state = init_workflow_state(
        run_id=run_id,
        task_file="inbox/baseline.md",
        branch="feature/backlog-followups",
        branch_base=BRANCH_BASE,
        first_slice_start_commit=BRANCH_BASE,
        slice_count=slice_count,
        task_digest=TASK_DIGEST,
        task_scope_patterns=("src/baseline.py",),
        work_plan_path="docs/work-plan.md" if bind_plan else None,
        target_branch="feature/backlog-followups",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
        timestamp=STAMP,
    )
    if bind_plan:
        state = state.bind_slice_plan(
            (PlannedSlice(1, "baseline", ("src/baseline.py",)),),
            first_start_commit=BRANCH_BASE,
            updated_at=STAMP,
        )
    state = (
        state.complete_current_work_unit(updated_at=STAMP)
        .start_work_unit(
            slice_id=1,
            kind=WorkUnitKind.SLICE,
            step=WorkflowStep.CODEX_IMPLEMENTATION,  # allowlist:provider
            updated_at=STAMP,
        )
        .bind_current_slice_git_boundary(
            start_commit=BRANCH_BASE,
            scope_paths=("src/baseline.py",),
            start_fingerprint="c" * 64,
            updated_at=STAMP,
        )
    )
    if bind_plan:
        state = replace(state, approved_plan_commit="d" * 40)
    return state


def _persist_chain(root: Path, state: WorkflowState) -> tuple[ArtifactRecord, ...]:
    root.mkdir(parents=True)
    driver = orchestrator.ProductionWorkflowDriver(
        repository_root=root,
        state_file=root / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=root),
        allowed_roots=(root,),
    )
    driver.active_state = state
    driver._bind_artifact_store(state)
    driver._persist_structured_baseline(state)
    bridge = driver._artifact_bridge
    assert bridge is not None
    return bridge.store.load_chain()


def _copy_state(state: WorkflowState, field: str, value: object) -> WorkflowState:
    changed = copy.copy(state)
    object.__setattr__(changed, field, value)
    return changed


def _copy_work_unit(
    state: WorkflowState, index: int, field: str, value: object
) -> WorkflowState:
    units = list(state.work_units)
    changed_unit = copy.copy(units[index])
    object.__setattr__(changed_unit, field, value)
    units[index] = changed_unit
    return _copy_state(state, "work_units", tuple(units))


def _copy_slice(
    state: WorkflowState, index: int, field: str, value: object
) -> WorkflowState:
    slices = list(state.slices)
    changed_slice = copy.copy(slices[index])
    object.__setattr__(changed_slice, field, value)
    slices[index] = changed_slice
    return _copy_state(state, "slices", tuple(slices))


def _record_binding(record: ArtifactRecord) -> dict[str, object]:
    raw = record.to_dict()
    return {
        "record_type": record.record_type.value,
        "logical_id": record.logical_id,
        "idempotency_key": record.idempotency_key,
        "revision": record.revision,
        "fingerprint": raw["fingerprint"],
        "payload": raw["payload"],
    }


def _state_binding(state: WorkflowState) -> dict[str, object]:
    binding = state.protocol_binding
    return {
        "task_digest": state.task_digest,
        "protocol_binding": (
            None
            if binding is None
            else {
                "mode": binding.mode.value,
                "codex_profile": {
                    "model": binding.codex_profile.model,
                    "effort": binding.codex_profile.effort,
                },
                "claude_profile": {
                    "model": binding.claude_profile.model,
                    "effort": binding.claude_profile.effort,
                },
            }
        ),
        "runtime_history_present": state.runtime_history is not None,
        "current_work_unit_id": state.current_work_unit_id,
        "task_scope_patterns": list(state.task_scope_patterns),
        "work_plan_path": state.work_plan_path,
        "approved_plan_commit": state.approved_plan_commit,
        "planned_slice_ids": [item.slice_id for item in state.planned_slices],
        "slices": [
            {
                "slice_id": item.slice_id,
                "start_commit": item.start_commit,
                "start_fingerprint": item.start_fingerprint,
            }
            for item in state.slices
        ],
        "work_units": [
            {
                "work_unit_id": item.work_unit_id,
                "slice_id": item.slice_id,
                "kind": item.kind.value,
                "invocation_failures": len(item.invocation_failures),
                "completed_side_effects": list(item.completed_side_effects),
                "gate_decisions": len(item.gate_decisions),
            }
            for item in state.work_units
        ],
    }


def _leaf_differences(
    expected: object, actual: object, path: str = "state"
) -> list[dict[str, object]]:
    if isinstance(expected, dict) and isinstance(actual, dict):
        differences: list[dict[str, object]] = []
        for key in sorted(expected.keys() | actual.keys()):
            differences.extend(
                _leaf_differences(expected.get(key), actual.get(key), f"{path}.{key}")
            )
        return differences
    if isinstance(expected, list) and isinstance(actual, list):
        differences = []
        for index, (left, right) in enumerate(zip(expected, actual, strict=False)):
            differences.extend(_leaf_differences(left, right, f"{path}[{index}]"))
        if len(expected) != len(actual):
            differences.append(
                {
                    "path": f"{path}.length",
                    "reference": len(expected),
                    "input": len(actual),
                }
            )
        return differences
    if expected != actual:
        return [{"path": path, "reference": expected, "input": actual}]
    return []


def _single_difference(case: DecisionInput) -> dict[str, object]:
    state_differences = _leaf_differences(
        case.reference_state.to_dict(), case.state.to_dict()
    )
    if case.reference_records == case.records:
        record_differences: list[dict[str, object]] = []
    elif case.reference_records[: len(case.records)] == case.records:
        record_differences = [
            {
                "path": "records.removed_suffix",
                "reference": [
                    item.record_type.value
                    for item in case.reference_records[len(case.records) :]
                ],
                "input": [],
            }
        ]
    elif case.records[: len(case.reference_records)] == case.reference_records:
        record_differences = [
            {
                "path": "records.added_suffix",
                "reference": [],
                "input": [
                    item.record_type.value
                    for item in case.records[len(case.reference_records) :]
                ],
            }
        ]
    else:
        record_differences = _leaf_differences(
            [_record_binding(item) for item in case.reference_records],
            [_record_binding(item) for item in case.records],
            "records",
        )
    differences = [*state_differences, *record_differences]
    assert len(differences) == 1, case.scenario_id
    return differences[0]


def _chain_name(
    records: tuple[ArtifactRecord, ...],
    chains: dict[str, tuple[ArtifactRecord, ...]],
) -> str:
    return next(name for name, candidate in chains.items() if candidate == records)


def _decision_document(
    case: DecisionInput,
    chains: dict[str, tuple[ArtifactRecord, ...]],
) -> dict[str, object]:
    actual = baseline_module.matches_baseline_initialization_prefix(
        case.records, case.state
    )
    reference_actual = (
        None
        if case.target_condition is None
        else baseline_module.matches_baseline_initialization_prefix(
            case.reference_records, case.reference_state
        )
    )
    return {
        "scenario_id": case.scenario_id,
        "target_condition": case.target_condition,
        "rationale": case.rationale,
        "input_state": _state_binding(case.state),
        "reference_chain": _chain_name(case.reference_records, chains),
        "input_chain": _chain_name(case.records, chains),
        "reference_expected": None if case.target_condition is None else True,
        "reference_actual": reference_actual,
        "single_difference": (
            None if case.target_condition is None else _single_difference(case)
        ),
        "expected": case.expected,
        "actual": actual,
    }


def _unexpected(*_args: object, **_kwargs: object) -> Any:
    raise AssertionError("B55 abort scenario escaped its intended boundary")


class _Store:
    def __init__(self, chain: tuple[object, ...]) -> None:
        self.chain = chain

    def load_chain(self) -> tuple[object, ...]:
        return self.chain

    def current_chain(self) -> tuple[object, ...]:
        return self.chain


class _Bridge:
    def __init__(self, chain: tuple[object, ...]) -> None:
        self.store = _Store(chain)


def _abort_dependencies(bridge: _Bridge) -> WorkflowBaselineDependencies:
    return WorkflowBaselineDependencies(
        artifact_bridge=lambda: cast(Any, bridge),
        active_state=lambda: None,
        artifact_fingerprint=_unexpected,
        collect_changes=_unexpected,
        persist_bootstrap_state=_unexpected,
        persistence=_unexpected,
        recovery=_unexpected,
        reconcile_pending_workflow_event=_unexpected,
        side_effect_executor=_unexpected,
    )


def _capture_abort(
    scenario_id: str,
    state: WorkflowState,
    chain: tuple[object, ...],
    expected_type: str,
    expected_message: str,
    configure: Callable[[pytest.MonkeyPatch], None] | None = None,
) -> dict[str, object]:
    baseline = WorkflowBaseline(_abort_dependencies(_Bridge(chain)))
    with pytest.MonkeyPatch.context() as patch:
        if configure is not None:
            configure(patch)
        try:
            baseline._persist_structured_baseline(state)
        except Exception as exc:  # noqa: BLE001 - corpus binds exact abort type
            result = {
                "scenario_id": scenario_id,
                "outcome": "raised",
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
            assert result["error_type"] == expected_type, scenario_id
            assert result["message"] == expected_message, scenario_id
            return result
    raise AssertionError(f"{scenario_id} did not trigger its source abort")


def _abort_scenarios(
    base_state: WorkflowState,
    base_records: tuple[ArtifactRecord, ...],
) -> list[dict[str, object]]:
    missing_binding = _copy_state(base_state, "protocol_binding", None)

    def replay_rejected(patch: pytest.MonkeyPatch) -> None:
        error = ArtifactReplayError(
            ReplayDiagnostic(
                ReplayDiagnosticCode.RECORD_MISSING,
                "B55 injected replay rejection",
            )
        )
        patch.setattr(baseline_module, "replay_artifacts", lambda *_a, **_k: (_ for _ in ()).throw(error))
        patch.setattr(
            baseline_module, "matches_baseline_initialization_prefix", lambda *_a: False
        )

    def resume_rejected(patch: pytest.MonkeyPatch) -> None:
        replay = SimpleNamespace(
            pending_workflow_event_record_id=None,
            pending_review_record_id=None,
            records=(cast(Any, object()),),
            side_effects=(),
        )
        error = ArtifactResumeError(
            "B55 injected resume rejection",
            code=ReplayDiagnosticCode.RECORD_MISSING,
        )
        patch.setattr(baseline_module, "replay_artifacts", lambda *_a, **_k: replay)
        patch.setattr(baseline_module, "require_workflow_event_prefix", lambda *_a: None)
        patch.setattr(
            baseline_module,
            "require_workflow_status_prefix",
            lambda *_a: (_ for _ in ()).throw(error),
        )
        patch.setattr(
            baseline_module, "matches_baseline_initialization_prefix", lambda *_a: False
        )

    return [
        _capture_abort(
            "missing-protocol-binding",
            missing_binding,
            (),
            "WorkflowExecutionError",
            "structured baseline requires the immutable protocol binding",
        ),
        _capture_abort(
            "artifact-replay-rejected",
            base_state,
            (base_records[0],),
            "ArtifactReplayError",
            "RECORD-MISSING: B55 injected replay rejection",
            replay_rejected,
        ),
        _capture_abort(
            "artifact-resume-rejected",
            base_state,
            (base_records[0],),
            "ArtifactResumeError",
            "RECORD-MISSING: B55 injected resume rejection",
            resume_rejected,
        ),
    ]


def _build_runtime(root: Path) -> RuntimeCorpus:
    base_state = _state("b55-base")
    base_records = _persist_chain(root / "base", base_state)
    orphan_state = _state("b55-orphan", slice_count=2)
    orphan_records = _persist_chain(root / "orphan", orphan_state)
    planned_state = _state("b55-planned", bind_plan=True)
    planned_records = _persist_chain(root / "planned", planned_state)
    active_state = _copy_work_unit(
        _state("b55-active"), 1, "completed_side_effects", ("baseline-active-marker",)
    )
    active_records = _persist_chain(root / "active", active_state)

    identity_prefix = base_records[:1]
    cases = (
        DecisionInput(
            "valid-identity-prefix",
            None,
            base_state,
            identity_prefix,
            base_state,
            identity_prefix,
            True,
            "The first canonical domain record is an executable baseline prefix.",
        ),
        DecisionInput(
            "valid-complete-baseline",
            None,
            base_state,
            base_records,
            base_state,
            base_records,
            True,
            "The complete canonical pre-work append sequence remains admissible.",
        ),
        DecisionInput(
            "condition-01-missing-protocol-binding",
            1,
            base_state,
            identity_prefix,
            _copy_state(base_state, "protocol_binding", None),
            identity_prefix,
            False,
            "Only state.protocol_binding differs from the valid identity prefix.",
        ),
        DecisionInput(
            "condition-02-prior-runtime-history",
            2,
            base_state,
            identity_prefix,
            _copy_state(base_state, "runtime_history", {}),
            identity_prefix,
            False,
            "Only runtime_history changes from absent to present.",
        ),
        DecisionInput(
            "condition-03-empty-domain-prefix",
            3,
            base_state,
            identity_prefix,
            base_state,
            (),
            False,
            "Only the canonical identity record is removed.",
        ),
        DecisionInput(
            "condition-04-orphan-slice-now-owned",
            4,
            orphan_state,
            orphan_records,
            _copy_work_unit(orphan_state, 0, "slice_id", 2),
            orphan_records,
            False,
            "Only the prior work unit slice_id changes, so slice 2 is no longer orphaned.",
        ),
        DecisionInput(
            "condition-05-current-unit-selection",
            5,
            base_state,
            base_records,
            _copy_state(base_state, "current_work_unit_id", 1),
            base_records,
            False,
            "Only current_work_unit_id changes, reversing the skipped unit.",
        ),
        DecisionInput(
            "condition-06-missing-slice-start-commit",
            6,
            base_state,
            base_records,
            _copy_slice(base_state, 0, "start_commit", None),
            base_records,
            False,
            "Only the bound Slice start_commit is removed.",
        ),
        DecisionInput(
            "condition-07-task-scope-disabled",
            7,
            base_state,
            base_records,
            _copy_state(base_state, "task_scope_patterns", ()),
            base_records,
            False,
            "Only task_scope_patterns changes, suppressing the task-contract expectation.",
        ),
        DecisionInput(
            "condition-08-current-unit-kind-plan",
            8,
            base_state,
            base_records,
            _copy_work_unit(base_state, 1, "kind", WorkUnitKind.PLAN),
            base_records,
            False,
            "Only the current work-unit kind changes from Slice to Plan.",
        ),
        DecisionInput(
            "condition-09-approved-plan-commit-missing",
            9,
            planned_state,
            planned_records,
            _copy_state(planned_state, "approved_plan_commit", None),
            planned_records,
            False,
            "Only approved_plan_commit is removed from an otherwise bound plan.",
        ),
        DecisionInput(
            "condition-10-overlong-prefix",
            10,
            base_state,
            base_records,
            base_state,
            (*base_records, base_records[-1]),
            False,
            "Only one record is appended beyond the complete canonical baseline.",
        ),
        DecisionInput(
            "already-active-chain",
            None,
            active_state,
            active_records,
            active_state,
            active_records,
            False,
            "A completed non-ledger internal side effect makes the chain active.",
        ),
    )
    decisions = {item.scenario_id: item for item in cases}
    chains = {
        "empty": (),
        "identity-prefix": identity_prefix,
        "base-complete": base_records,
        "orphan-complete": orphan_records,
        "planned-complete": planned_records,
        "overlong-base": (*base_records, base_records[-1]),
        "already-active": active_records,
    }
    document = {
        "schema_version": "workflow-baseline-runtime-pre-b55-v1",
        "source_commit": SOURCE_COMMIT,
        "source_blob": SOURCE_BLOB,
        "chains": {
            name: [_record_binding(item) for item in records]
            for name, records in chains.items()
        },
        "decision_table": [_decision_document(item, chains) for item in cases],
        "unreachable_conditions": [],
        "persistence_aborts": _abort_scenarios(base_state, base_records),
        "persistence_success": {
            "written_records": [
                {"record_type": item.record_type.value, "record_id": item.record_id}
                for item in base_records
            ]
        },
    }
    return RuntimeCorpus(document, decisions)


_RUNTIME_BUILD_COUNT = 0


@pytest.fixture(scope="session")
def runtime_corpus(tmp_path_factory: pytest.TempPathFactory) -> RuntimeCorpus:
    global _RUNTIME_BUILD_COUNT
    _RUNTIME_BUILD_COUNT += 1
    return _build_runtime(tmp_path_factory.mktemp("b55-baseline-corpus"))


@lru_cache(maxsize=1)
def _expected_runtime() -> dict[str, object]:
    return json.loads(RUNTIME_BASELINE.read_text("utf-8"))


def _helper_catcher_bindings(tree: ast.Module) -> dict[str, str]:
    function = _function(
        tree, "_persist_structured_baseline", class_name="WorkflowBaseline"
    )
    parents = _parent_map(function)
    bindings: dict[str, str] = {}
    for helper_name in CAUGHT_HELPERS:
        calls = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
            and node.func.attr == helper_name
        ]
        assert len(calls) == 1, helper_name
        current: ast.AST = calls[0]
        while current in parents:
            child = current
            current = parents[current]
            if isinstance(current, ast.Try) and child in current.body:
                assert len(current.handlers) == 1, helper_name
                bindings[helper_name] = ast.unparse(current.handlers[0].type)
                break
        else:
            raise AssertionError(f"helper call left its catcher: {helper_name}")
    return bindings


def _move_helper_call_out_of_catcher(tree: ast.Module, helper_name: str) -> None:
    function = _function(
        tree, "_persist_structured_baseline", class_name="WorkflowBaseline"
    )
    parents = _parent_map(function)
    call = next(
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == helper_name
    )
    current: ast.AST = call
    while current in parents:
        child = current
        current = parents[current]
        if isinstance(current, ast.Try) and child in current.body:
            catcher = current
            statement = child
            break
    else:
        raise AssertionError(helper_name)
    for node in ast.walk(function):
        for field in ("body", "orelse", "finalbody"):
            body = getattr(node, field, None)
            if isinstance(body, list) and catcher in body:
                body.insert(body.index(catcher), statement)
                catcher.body.remove(statement)
                if not catcher.body:
                    catcher.body.append(ast.Pass())
                return
    raise AssertionError(f"catcher parent not found: {helper_name}")


def _strict_neighborhood(tree: ast.Module) -> list[dict[str, str]]:
    function = _function(tree, "matches_baseline_initialization_prefix")
    statements = function.body[-3:]
    assert isinstance(statements[0], ast.If)
    assert isinstance(statements[1], ast.If)
    assert isinstance(statements[2], ast.Return)
    return [
        {"kind": "If", "expression": ast.unparse(statements[0].test)},
        {"kind": "If", "expression": ast.unparse(statements[1].test)},
        {"kind": "Return", "expression": ast.unparse(statements[2].value)},
    ]


def _assert_named_entry(
    actual_entries: list[dict[str, object]],
    expected_entries: list[dict[str, object]],
    scenario_id: str,
) -> None:
    actual = next(item for item in actual_entries if item["scenario_id"] == scenario_id)
    expected = next(item for item in expected_entries if item["scenario_id"] == scenario_id)
    if actual != expected:
        raise AssertionError(scenario_id)


def test_static_inventory_is_complete_cleartext_and_source_ordered() -> None:
    actual = _static_document()
    expected = json.loads(STATIC_BASELINE.read_text("utf-8"))
    assert actual == expected
    predicate, persistence = actual["functions"]
    assert (len(predicate["conditions"]), len(predicate["returns"])) == (10, 5)
    assert (len(predicate["aborts"]), len(predicate["catchers"])) == (0, 0)
    assert (len(persistence["conditions"]), len(persistence["returns"])) == (17, 2)
    assert len(persistence["aborts"]) == 3
    assert [item["exception_type"] for item in persistence["catchers"]] == [
        "ArtifactReplayError",
        "ArtifactResumeError",
    ]
    assert all(
        item["read_names"]
        for function in actual["functions"]
        for item in function["conditions"]
    )


def test_source_anchor_binds_pre_b55_bytes() -> None:
    anchored_blob = subprocess.run(
        ["git", "rev-parse", f"{SOURCE_COMMIT}:src/workflow_baseline.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert anchored_blob == SOURCE_BLOB


def test_b56_pre_cut_anchor_binds_the_immediate_b55_source() -> None:
    pre_cut = json.loads(PRE_B56_BASELINE.read_text("utf-8"))
    assert pre_cut == {
        "schema_version": "workflow-baseline-pre-b56-v1",
        "source_commit": PRE_B56_COMMIT,
        "source_blob": SOURCE_BLOB,
    }
    anchored_blob = subprocess.run(
        ["git", "rev-parse", f"{PRE_B56_COMMIT}:src/workflow_baseline.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert anchored_blob == SOURCE_BLOB


def test_b56_helpers_and_previous_catchers_are_explicitly_bound() -> None:
    binding = json.loads(HELPER_BINDINGS.read_text("utf-8"))
    assert binding["schema_version"] == "workflow-baseline-helper-bindings-b56-v1"
    expected_helpers = [
        helper for helpers in BASELINE_HELPERS.values() for helper in helpers
    ]
    assert [item["helper"] for item in binding["helpers"]] == expected_helpers
    assert {
        item["helper"]: item["catcher"]
        for item in binding["helpers"]
        if item["catcher"] is not None
    } == CAUGHT_HELPERS
    tree = ast.parse(SOURCE_PATH.read_text("utf-8"))
    assert _helper_catcher_bindings(tree) == CAUGHT_HELPERS


def test_strict_zip_and_both_adjacent_checks_remain_in_one_neighborhood() -> None:
    binding = json.loads(HELPER_BINDINGS.read_text("utf-8"))
    neighborhood = binding["strict_neighborhood"]
    assert neighborhood["status"] == "preserved_contiguous_in_entry"
    tree = ast.parse(SOURCE_PATH.read_text("utf-8"))
    actual = _strict_neighborhood(tree)
    assert actual == neighborhood["statements"]
    assert "state.approved_plan_commit is not None" in actual[0]["expression"]
    assert "len(prefix) > len(expectations)" in actual[1]["expression"]
    assert "strict=True" in actual[2]["expression"]


DECISION_IDS = (
    "valid-identity-prefix",
    "valid-complete-baseline",
    *(f"condition-{index:02d}-{suffix}" for index, suffix in (
        (1, "missing-protocol-binding"),
        (2, "prior-runtime-history"),
        (3, "empty-domain-prefix"),
        (4, "orphan-slice-now-owned"),
        (5, "current-unit-selection"),
        (6, "missing-slice-start-commit"),
        (7, "task-scope-disabled"),
        (8, "current-unit-kind-plan"),
        (9, "approved-plan-commit-missing"),
        (10, "overlong-prefix"),
    )),
    "already-active-chain",
)


@pytest.mark.parametrize("scenario_id", DECISION_IDS)
def test_each_predicate_decision_matches_its_cleartext_case(
    runtime_corpus: RuntimeCorpus, scenario_id: str
) -> None:
    actual = cast(list[dict[str, object]], runtime_corpus.document["decision_table"])
    expected = cast(list[dict[str, object]], _expected_runtime()["decision_table"])
    _assert_named_entry(actual, expected, scenario_id)
    entry = next(item for item in actual if item["scenario_id"] == scenario_id)
    assert entry["actual"] is entry["expected"]


def test_all_ten_conditions_have_one_exact_near_miss(
    runtime_corpus: RuntimeCorpus,
) -> None:
    table = cast(list[dict[str, object]], runtime_corpus.document["decision_table"])
    near_misses = [item for item in table if item["target_condition"] is not None]
    assert [item["target_condition"] for item in near_misses] == list(range(1, 11))
    assert all(item["single_difference"] is not None for item in near_misses)
    assert all(item["expected"] is False and item["actual"] is False for item in near_misses)
    assert all(
        item["reference_expected"] is True and item["reference_actual"] is True
        for item in near_misses
    )
    assert runtime_corpus.document["unreachable_conditions"] == []


@pytest.mark.parametrize(
    "scenario_id",
    ("missing-protocol-binding", "artifact-replay-rejected", "artifact-resume-rejected"),
)
def test_each_persistence_abort_matches_its_cleartext_case(
    runtime_corpus: RuntimeCorpus, scenario_id: str
) -> None:
    actual = cast(list[dict[str, object]], runtime_corpus.document["persistence_aborts"])
    expected = cast(list[dict[str, object]], _expected_runtime()["persistence_aborts"])
    _assert_named_entry(actual, expected, scenario_id)


def test_success_persistence_binds_record_types_and_stable_ids(
    runtime_corpus: RuntimeCorpus,
) -> None:
    expected = _expected_runtime()["persistence_success"]
    assert runtime_corpus.document["persistence_success"] == expected
    written = cast(dict[str, list[dict[str, str]]], expected)["written_records"]
    assert len(written) == 16
    assert all(item["record_id"].startswith("ar1-") for item in written)


def _predicate_without_prior_activity_guard() -> Callable[..., bool]:
    tree = ast.parse(SOURCE_PATH.read_text("utf-8"))
    function = _function(tree, "matches_baseline_initialization_prefix")
    target = next(
        item
        for item in function.body
        if isinstance(item, ast.If)
        and "state.runtime_history is not None" in ast.unparse(item.test)
    )
    function.body.remove(target)
    ast.fix_missing_locations(function)
    namespace = dict(vars(baseline_module))
    exec(compile(ast.Module(body=[function], type_ignores=[]), "<b55-mutation>", "exec"), namespace)
    return cast(Callable[..., bool], namespace[function.name])


def _assert_decision(scenario_id: str, actual: bool, expected: bool) -> None:
    if actual is not expected:
        raise AssertionError(f"{scenario_id}: expected {expected}, got {actual}")


def test_removing_prior_activity_condition_names_and_turns_its_near_miss_red(
    runtime_corpus: RuntimeCorpus,
) -> None:
    scenario_id = "condition-02-prior-runtime-history"
    case = runtime_corpus.decisions[scenario_id]
    assert not baseline_module.matches_baseline_initialization_prefix(
        case.records, case.state
    )
    mutated = _predicate_without_prior_activity_guard()
    with pytest.raises(AssertionError, match=scenario_id):
        _assert_decision(
            scenario_id,
            mutated(case.records, case.state),
            case.expected,
        )


def test_moving_a_baseline_helper_out_of_its_catcher_turns_binding_red() -> None:
    tree = ast.parse(SOURCE_PATH.read_text("utf-8"))
    helper_name = "_require_existing_baseline_prefix"
    _move_helper_call_out_of_catcher(tree, helper_name)
    ast.fix_missing_locations(tree)
    compile(tree, "<b56-helper-outside-catcher>", "exec")
    with pytest.raises(AssertionError, match=helper_name):
        _helper_catcher_bindings(tree)


def test_b56_entries_and_helpers_are_below_the_b32_threshold() -> None:
    tree = ast.parse(SOURCE_PATH.read_text("utf-8"))
    spans: dict[str, int] = {}
    for entry, helpers in BASELINE_HELPERS.items():
        owner = "WorkflowBaseline" if entry == "_persist_structured_baseline" else None
        for name in (entry, *helpers):
            function = _function(tree, name, class_name=owner)
            spans[name] = function.end_lineno - function.lineno + 1
    assert all(span < 200 for span in spans.values()), spans
    size_baseline = json.loads(FUNCTION_SIZE_BASELINE.read_text("utf-8"))
    assert (
        "src/workflow_baseline.py::matches_baseline_initialization_prefix"
        not in size_baseline["functions"]
    )
    assert (
        "src/workflow_baseline.py::WorkflowBaseline._persist_structured_baseline"
        not in size_baseline["functions"]
    )


def test_target_record_sequence_baseline_is_exact() -> None:
    actual_blob = subprocess.run(
        ["git", "hash-object", str(RECORD_SEQUENCE_BASELINE)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert actual_blob == RECORD_SEQUENCE_BLOB


def test_runtime_scenarios_are_built_exactly_once(
    runtime_corpus: RuntimeCorpus,
) -> None:
    assert runtime_corpus.document["source_blob"] == SOURCE_BLOB
    assert _RUNTIME_BUILD_COUNT == 1
