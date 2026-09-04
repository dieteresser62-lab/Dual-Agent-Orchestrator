from __future__ import annotations

import ast
import copy
import hashlib
import inspect
import json
import subprocess
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import agent_runtime
import pytest
import workflow as production

from agent_runtime import NativeAgentCodexOutput
from contracts import AgentRole, CodexContractResult, PlannedSlice
from test_workflow import FakeDriver, TEST_FILE, _changes, _context, _slice_state
from workflow import CodexInvocation, ReviewerInvocation, WorkflowHistory
from workflow_state import WorkflowStep, init_workflow_state


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / "src/workflow.py"
STATIC_BASELINE = ROOT / "tests/fixtures/engine-dispatch-static-pre-b51-v1.json"
RUNTIME_BASELINE = ROOT / "tests/fixtures/engine-dispatch-runtime-pre-b51-v1.json"
SOURCE_COMMIT = "b762dde73296b94a61f028328d09409a07922f2e"
SOURCE_BLOB = "6eef6884f82c4ae0b184f40bc4222384562d0351"


SCENARIOS: tuple[dict[str, str], ...] = (
    {
        "scenario_id": "codex-correction-missing-start-fingerprint",
        "path": "codex",
        "trigger_mode": "state-contract-injection",
        "error_type": "WorkflowExecutionError",
        "message": "native Codex correction requires a persisted start fingerprint",
    },
    {
        "scenario_id": "codex-non-native-result",
        "path": "codex",
        "trigger_mode": "provider-contract-injection",
        "error_type": "WorkflowContractError",
        "message": "Codex returned a non-native result",
    },
    {
        "scenario_id": "codex-stop-missing-request",
        "path": "codex",
        "trigger_mode": "provider-contract-injection",
        "error_type": "WorkflowExecutionError",
        "message": "Codex stop has no structured stop request",
    },
    {
        "scenario_id": "codex-plan-outside-task-scope",
        "path": "codex",
        "trigger_mode": "production-input",
        "error_type": "WorkflowExecutionError",
        "message": "TASK-SCOPE | SLICE_PLAN contains paths outside the declared task scope: src/outside.py",
    },
    {
        "scenario_id": "codex-plan-only-multiple-slices",
        "path": "codex",
        "trigger_mode": "provider-contract-injection",
        "error_type": "WorkflowExecutionError",
        "message": "PLAN_ONLY requires exactly one executable Slice for the plan artifact",
    },
    {
        "scenario_id": "codex-plan-only-missing-work-plan-path",
        "path": "codex",
        "trigger_mode": "provider-contract-injection",
        "error_type": "WorkflowExecutionError",
        "message": "PLAN_ONLY Slice must include the declared WORK_PLAN_PATH",
    },
    {
        "scenario_id": "review-non-claude",
        "path": "review",
        "trigger_mode": "caller-contract-injection",
        "error_type": "WorkflowExecutionError",
        "message": "only Claude may execute review steps",
    },
    {
        "scenario_id": "review-incomplete-attestation",
        "path": "review",
        "trigger_mode": "driver-contract-injection",
        "error_type": "WorkflowExecutionError",
        "message": "validation attestation is incomplete and cannot be overridden",
    },
    {
        "scenario_id": "review-correction-missing-start-fingerprint",
        "path": "review",
        "trigger_mode": "state-contract-injection",
        "error_type": "WorkflowExecutionError",
        "message": "correction review requires a persisted start fingerprint",
    },
    {
        "scenario_id": "review-slice-packet-missing-start-fingerprint",
        "path": "review",
        "trigger_mode": "state-contract-injection",
        "error_type": "WorkflowExecutionError",
        "message": "Slice review packet requires a persisted start fingerprint",
    },
    {
        "scenario_id": "review-packet-builder-error",
        "path": "review",
        "trigger_mode": "packet-builder-failure",
        "error_type": "WorkflowExecutionError",
        "message": "canonical review packet could not be built: packet probe",
    },
    {
        "scenario_id": "review-invalid-recovery-contract",
        "path": "review",
        "trigger_mode": "recovery-contract-injection",
        "error_type": "WorkflowExecutionError",
        "message": "native reviewer recovery returned an invalid result contract",
    },
    {
        "scenario_id": "review-non-native-result",
        "path": "review",
        "trigger_mode": "provider-contract-injection",
        "error_type": "WorkflowContractError",
        "message": "Claude returned a non-native review result",
    },
)


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    owner = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "WorkflowEngine"
    )
    return next(
        node
        for node in owner.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _ordered(node: ast.AST, kind: type[ast.AST]) -> list[Any]:
    return sorted(
        (item for item in ast.walk(node) if isinstance(item, kind)),
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


def _raise_type(node: ast.Raise) -> str:
    assert isinstance(node.exc, ast.Call)
    return ast.unparse(node.exc.func)


def _raise_message(node: ast.Raise) -> str | None:
    assert isinstance(node.exc, ast.Call)
    return ast.unparse(node.exc.args[0]) if node.exc.args else None


def _is_checkpoint(node: ast.Call) -> bool:
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "checkpoint"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "driver"
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "self"
    )


def _parent_map(function: ast.FunctionDef) -> dict[ast.AST, ast.AST]:
    return {
        child: parent
        for parent in ast.walk(function)
        for child in ast.iter_child_nodes(parent)
    }


def _statement_paths(function: ast.FunctionDef) -> dict[ast.stmt, str]:
    result: dict[ast.stmt, str] = {}

    def visit_body(body: list[ast.stmt], prefix: str) -> None:
        for index, statement in enumerate(body):
            path = f"{prefix}[{index}]"
            result[statement] = path
            for field in ("body", "orelse", "finalbody"):
                child = getattr(statement, field, None)
                if isinstance(child, list) and child:
                    visit_body(child, f"{path}.{field}")
            if isinstance(statement, ast.Try):
                for handler_index, handler in enumerate(statement.handlers):
                    visit_body(handler.body, f"{path}.handlers[{handler_index}].body")

    visit_body(function.body, "body")
    return result


def _enclosing_statement(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> ast.stmt:
    current = node
    while current not in parents or not isinstance(current, ast.stmt):
        current = parents[current]
    return current


def _static_layer(tree: ast.Module, name: str) -> dict[str, object]:
    function = _function(tree, name)
    conditions = _ordered(function, ast.If)
    raises = _ordered(function, ast.Raise)
    returns = _ordered(function, ast.Return)
    catchers = _ordered(function, ast.ExceptHandler)
    checkpoints = [
        item for item in _ordered(function, ast.Call) if _is_checkpoint(item)
    ]
    condition_ordinals = {id(item): index + 1 for index, item in enumerate(conditions)}
    parents = _parent_map(function)
    statement_paths = _statement_paths(function)

    checkpoint_records = []
    for ordinal, call in enumerate(checkpoints, 1):
        ancestors: list[int] = []
        current: ast.AST = call
        while current in parents:
            current = parents[current]
            if isinstance(current, ast.If):
                ancestors.append(condition_ordinals[id(current)])
        statement = _enclosing_statement(call, parents)
        checkpoint_records.append(
            {
                "ordinal": ordinal,
                "state_expression": ast.unparse(call.args[0]),
                "history_expression": ast.unparse(call.args[1]),
                "statement_path": statement_paths[statement],
                "after_condition_count": sum(
                    condition.lineno < call.lineno for condition in conditions
                ),
                "enclosing_condition_ordinals": sorted(ancestors),
            }
        )

    return {
        "function": f"workflow.py::WorkflowEngine.{name}",
        "conditions": [
            {
                "ordinal": ordinal,
                "expression": ast.unparse(node.test),
                "read_names": _read_names(node.test),
            }
            for ordinal, node in enumerate(conditions, 1)
        ],
        "aborts": [
            {
                "ordinal": ordinal,
                "exception_type": _raise_type(node),
                "message_expression": _raise_message(node),
            }
            for ordinal, node in enumerate(raises, 1)
        ],
        "returns": [
            {
                "ordinal": ordinal,
                "expression": ast.unparse(node.value) if node.value else None,
            }
            for ordinal, node in enumerate(returns, 1)
        ],
        "catchers": [
            {
                "ordinal": ordinal,
                "exception_type": ast.unparse(node.type) if node.type else None,
            }
            for ordinal, node in enumerate(catchers, 1)
        ],
        "checkpoints": checkpoint_records,
    }


def _static_document(source: str | None = None) -> dict[str, object]:
    tree = ast.parse(source if source is not None else WORKFLOW_PATH.read_text("utf-8"))
    return {
        "schema_version": "engine-dispatch-static-pre-b51-v1",
        "source_commit": SOURCE_COMMIT,
        "source_blob": SOURCE_BLOB,
        "layers": [
            _static_layer(tree, "_run_codex"),
            _static_layer(tree, "_run_review"),
        ],
    }


def _checkpoint_site_map() -> dict[tuple[str, int], str]:
    tree = ast.parse(WORKFLOW_PATH.read_text("utf-8"))
    result: dict[tuple[str, int], str] = {}
    for function_name in ("_run_codex", "_run_review"):
        function = _function(tree, function_name)
        checkpoints = [
            item for item in _ordered(function, ast.Call) if _is_checkpoint(item)
        ]
        for ordinal, call in enumerate(checkpoints, 1):
            result[(function_name, call.lineno)] = f"{function_name}#{ordinal}"
    return result


CHECKPOINT_SITE_BY_LINE = _checkpoint_site_map()


@dataclass
class ProbeDriver(FakeDriver):
    codex_factory: Callable[[CodexInvocation], object] | None = None
    reviewer_factory: Callable[[ReviewerInvocation], object] | None = None
    recovered_reviewer: object | None = None
    provider_dispatch_count: int = 0
    checkpoint_sites: list[str] = field(default_factory=list)

    def checkpoint(self, state: object, history: WorkflowHistory) -> None:
        frame = inspect.currentframe()
        caller = frame.f_back if frame is not None else None
        try:
            while caller is not None:
                key = (caller.f_code.co_name, caller.f_lineno)
                if key in CHECKPOINT_SITE_BY_LINE:
                    self.checkpoint_sites.append(CHECKPOINT_SITE_BY_LINE[key])
                    break
                caller = caller.f_back
            else:
                raise AssertionError("checkpoint call is outside the bound dispatch sites")
        finally:
            del frame
            del caller
        super().checkpoint(state, history)

    def invoke_codex(self, invocation: CodexInvocation) -> object:
        self.provider_dispatch_count += 1
        self.codex_calls.append(invocation)
        assert self.codex_factory is not None
        return self.codex_factory(invocation)

    def invoke_reviewer(self, invocation: ReviewerInvocation) -> object:
        self.provider_dispatch_count += 1
        self.reviewer_calls.append(invocation)
        assert self.reviewer_factory is not None
        return self.reviewer_factory(invocation)

    def recover_pending_native_reviewer(
        self,
        invocation: ReviewerInvocation,
        contract: object,
        history: WorkflowHistory,
    ) -> object | None:
        self.structured_events.append(
            ("recover-native-reviewer", (invocation, contract, history))
        )
        return self.recovered_reviewer


def _plan_state(scope: tuple[str, ...] = ("docs/internal/plan.md",)) -> object:
    return init_workflow_state(
        run_id="b51-plan-run",
        task_file="/repo/task.md",
        branch="feature/workflow",
        branch_base="a" * 40,
        slice_count=1,
        task_digest="d" * 64,
        task_scope_patterns=scope,
        target_branch="feature/workflow",
    )


def _codex_output(
    invocation: CodexInvocation,
    result: CodexContractResult,
) -> NativeAgentCodexOutput:
    assert invocation.native_request is not None
    request_id = invocation.native_request.bound_context.request_id
    canonical = json.dumps(
        {"request_id": request_id, "result_type": "b51-probe"},
        sort_keys=True,
        separators=(",", ":"),
    )
    return NativeAgentCodexOutput(
        result=result,
        canonical_json=canonical,
        request_id=request_id,
        response_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def _native_request_json(driver: ProbeDriver) -> str | None:
    requests: list[str] = []
    for name, payload in driver.structured_events:
        if name not in {"recover-native-codex", "recover-native-reviewer"}:
            continue
        invocation = payload[0]
        if invocation.native_request is not None:
            requests.append(invocation.native_request.canonical_json)
    for invocation in (*driver.codex_calls, *driver.reviewer_calls):
        if invocation.native_request is not None:
            requests.append(invocation.native_request.canonical_json)
    unique = tuple(dict.fromkeys(requests))
    assert len(unique) <= 1
    return unique[0] if unique else None


def _provider_input(canonical: str | None) -> dict[str, object] | None:
    if canonical is None:
        return None
    encoded = canonical.encode("utf-8")
    return {
        "canonical_json": canonical,
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "utf8_bytes": len(encoded),
    }


def _checkpoint_sequence(driver: ProbeDriver) -> list[dict[str, str]]:
    return [
        {
            "checkpoint_site": site,
            "step": state.current_step.value,
            "work_unit_status": state.current_work_unit.status.value,
            "work_unit_kind": state.current_work_unit.kind.value,
        }
        for site, state in zip(driver.checkpoint_sites, driver.checkpoints, strict=True)
    ]


def _run_codex_scenario(
    scenario_id: str,
) -> tuple[ProbeDriver, object, object, AgentRole]:
    state = _slice_state()
    context = _context()
    driver = ProbeDriver(
        snapshots=[_changes("b", "src/early.py", TEST_FILE)],
        codex_outputs=[],
        reviewer_outputs=[],
    )

    if scenario_id == "codex-correction-missing-start-fingerprint":
        state = _plan_state().with_current_step(WorkflowStep.CODEX_CORRECTION)
    elif scenario_id == "codex-non-native-result":
        driver.codex_factory = lambda _invocation: object()
    elif scenario_id == "codex-stop-missing-request":
        driver.codex_factory = lambda invocation: _codex_output(
            invocation,
            CodexContractResult(False, True, None, None, (), ()),
        )
    elif scenario_id == "codex-plan-outside-task-scope":
        state = _plan_state()
        context = replace(
            context,
            require_slice_plan=True,
            task_scope_patterns=("docs/internal/plan.md",),
        )
        driver.codex_factory = lambda invocation: _codex_output(
            invocation,
            CodexContractResult(
                True,
                False,
                None,
                None,
                (),
                (),
                (PlannedSlice(1, "outside task scope", ("src/outside.py",)),),
            ),
        )
    elif scenario_id == "codex-plan-only-multiple-slices":
        state = _plan_state()
        context = replace(
            context,
            require_slice_plan=True,
            plan_only=True,
            work_plan_path="docs/internal/plan.md",
            task_scope_patterns=("docs/internal/plan.md",),
        )
        driver.codex_factory = lambda invocation: _codex_output(
            invocation,
            CodexContractResult(
                True,
                False,
                None,
                None,
                (),
                (),
                (
                    PlannedSlice(1, "first", ("docs/internal/plan.md",)),
                    PlannedSlice(2, "second", ("docs/internal/plan.md",)),
                ),
            ),
        )
    elif scenario_id == "codex-plan-only-missing-work-plan-path":
        scope = ("docs/internal/other.md", "docs/internal/plan.md")
        state = _plan_state(scope)
        context = replace(
            context,
            require_slice_plan=True,
            plan_only=True,
            work_plan_path="docs/internal/plan.md",
            task_scope_patterns=scope,
        )
        driver.codex_factory = lambda invocation: _codex_output(
            invocation,
            CodexContractResult(
                True,
                False,
                None,
                None,
                (),
                (),
                (PlannedSlice(1, "wrong artifact", ("docs/internal/other.md",)),),
            ),
        )
    else:  # pragma: no cover - closed by SCENARIOS
        raise AssertionError(f"unknown Codex scenario {scenario_id}")
    return driver, state, context, AgentRole.CODEX


def _run_review_scenario(
    scenario_id: str,
) -> tuple[ProbeDriver, object, object, AgentRole]:
    state = _slice_state().with_current_step(WorkflowStep.CLAUDE_SLICE_REVIEW)
    context = _context()
    reviewer = AgentRole.CLAUDE
    driver = ProbeDriver(
        snapshots=[_changes("b", "src/early.py", TEST_FILE)],
        codex_outputs=[],
        reviewer_outputs=[],
    )
    if scenario_id == "review-non-claude":
        reviewer = AgentRole.CODEX
    elif scenario_id == "review-incomplete-attestation":
        driver.invalid_attestation = "incomplete"
    elif scenario_id == "review-correction-missing-start-fingerprint":
        state = _plan_state().with_current_step(WorkflowStep.CLAUDE_SLICE_REVIEW)
        unit = replace(state.current_work_unit, round_number=2)
        state = replace(state, work_units=(*state.work_units[:-1], unit))
        context = replace(
            context,
            approved_plan_text="# Approved plan",
            task_scope_patterns=("docs/internal/plan.md",),
        )
        driver.snapshots = [_changes("b", "docs/internal/plan.md")]
    elif scenario_id == "review-slice-packet-missing-start-fingerprint":
        state = _plan_state().with_current_step(WorkflowStep.CLAUDE_SLICE_REVIEW)
        context = replace(
            context,
            approved_plan_text="# Approved plan",
            task_scope_patterns=("docs/internal/plan.md",),
        )
        driver.snapshots = [_changes("b", "docs/internal/plan.md")]
    elif scenario_id == "review-packet-builder-error":
        context = replace(context, approved_plan_text="# Approved plan")
    elif scenario_id == "review-invalid-recovery-contract":
        driver.recovered_reviewer = object()
    elif scenario_id == "review-non-native-result":
        driver.reviewer_factory = lambda _invocation: object()
    else:  # pragma: no cover - closed by SCENARIOS
        raise AssertionError(f"unknown review scenario {scenario_id}")
    return driver, state, context, reviewer


def _run_scenario(
    spec: dict[str, str],
    module: ModuleType = production,
    *,
    real_provider_counter: dict[str, int] | None = None,
) -> dict[str, object]:
    if spec["path"] == "codex":
        driver, state, context, reviewer = _run_codex_scenario(spec["scenario_id"])
    else:
        driver, state, context, reviewer = _run_review_scenario(spec["scenario_id"])
    before_real = real_provider_counter["count"] if real_provider_counter else 0
    error: BaseException | None = None
    with pytest.MonkeyPatch.context() as patch:
        if spec["scenario_id"] == "review-packet-builder-error":
            def fail_packet(**_kwargs: object) -> object:
                raise module.ReviewPacketError("packet probe")

            patch.setattr(module, "build_review_packet", fail_packet)
        try:
            engine = module.WorkflowEngine(driver)
            history = WorkflowHistory(state.current_work_unit_id)
            if spec["path"] == "codex":
                engine._run_codex(state, context, history)
            else:
                engine._run_review(state, context, history, reviewer)
        except BaseException as caught:  # corpus records mutant mismatches too
            error = caught
    after_real = real_provider_counter["count"] if real_provider_counter else 0
    canonical = _native_request_json(driver)
    return {
        "scenario_id": spec["scenario_id"],
        "path": spec["path"],
        "trigger_mode": spec["trigger_mode"],
        "reachable": True,
        "structural_reason": None,
        "expected_error": {
            "type": spec["error_type"],
            "message": spec["message"],
        },
        "actual_error": (
            {"type": type(error).__name__, "message": str(error)} if error else None
        ),
        "checkpoints": _checkpoint_sequence(driver),
        "provider_input": _provider_input(canonical),
        "provider_dispatch_count": driver.provider_dispatch_count,
        "real_provider_start_count": after_real - before_real,
    }


_RUNTIME_BUILD_COUNT = 0


@pytest.fixture(scope="session")
def runtime_corpus() -> dict[str, object]:
    global _RUNTIME_BUILD_COUNT
    _RUNTIME_BUILD_COUNT += 1
    real_provider_counter = {"count": 0}

    def forbidden_real_provider(*_args: object, **_kwargs: object) -> object:
        real_provider_counter["count"] += 1
        raise AssertionError("B51 must not start a real provider")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(agent_runtime, "run_agent", forbidden_real_provider)
        scenarios = [
            _run_scenario(spec, real_provider_counter=real_provider_counter)
            for spec in SCENARIOS
        ]
    assert real_provider_counter["count"] == 0
    return {
        "schema_version": "engine-dispatch-runtime-pre-b51-v1",
        "source_commit": SOURCE_COMMIT,
        "source_blob": SOURCE_BLOB,
        "scenarios": scenarios,
    }


def _load_mutant(transform: Callable[[ast.Module], None]) -> ModuleType:
    tree = ast.parse(WORKFLOW_PATH.read_text("utf-8"))
    transform(tree)
    ast.fix_missing_locations(tree)
    name = f"_b51_workflow_mutant_{id(tree)}"
    module = ModuleType(name)
    module.__file__ = str(WORKFLOW_PATH)
    sys.modules[name] = module
    try:
        exec(compile(tree, str(WORKFLOW_PATH), "exec"), module.__dict__)
    finally:
        sys.modules.pop(name, None)
    return module


def _remove_first_condition(function_name: str) -> Callable[[ast.Module], None]:
    def transform(tree: ast.Module) -> None:
        function = _function(tree, function_name)
        target = _ordered(function, ast.If)[0]
        for node in ast.walk(function):
            for field in ("body", "orelse", "finalbody"):
                body = getattr(node, field, None)
                if isinstance(body, list) and target in body:
                    body.remove(target)
                    return
        raise AssertionError("condition parent not found")

    return transform


def _shift_first_checkpoint(tree: ast.Module) -> None:
    function = _function(tree, "_run_codex")
    checkpoint = next(
        node for node in _ordered(function, ast.Call) if _is_checkpoint(node)
    )
    statement = checkpoint
    parents = _parent_map(function)
    while not isinstance(statement, ast.stmt):
        statement = parents[statement]
    for node in ast.walk(function):
        for field in ("body", "orelse", "finalbody"):
            body = getattr(node, field, None)
            if not isinstance(body, list) or statement not in body:
                continue
            index = body.index(statement)
            assert index + 1 < len(body)
            body[index], body[index + 1] = body[index + 1], body[index]
            return
    raise AssertionError("checkpoint parent not found")


def _change_first_codex_provider_request_field(tree: ast.Module) -> None:
    function = _function(tree, "_run_codex")
    request_call = next(
        node
        for node in _ordered(function, ast.Call)
        if isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "workflow_requests"
        and node.func.attr == "native_codex_request"
    )
    context_keyword = next(
        keyword for keyword in request_call.keywords if keyword.arg == "context"
    )
    context_keyword.value = ast.Call(
        func=ast.Name(id="replace", ctx=ast.Load()),
        args=[ast.Name(id="context", ctx=ast.Load())],
        keywords=[
            ast.keyword(
                arg="current_branch",
                value=ast.Constant(value="feature/b51-request-mutant"),
            )
        ],
    )


def _assert_runtime_matches(
    actual: dict[str, object], expected: dict[str, object]
) -> None:
    for key in ("schema_version", "source_commit", "source_blob"):
        assert actual[key] == expected[key]
    actual_by_id = {item["scenario_id"]: item for item in actual["scenarios"]}
    expected_by_id = {item["scenario_id"]: item for item in expected["scenarios"]}
    assert actual_by_id.keys() == expected_by_id.keys()
    for scenario_id, expected_scenario in expected_by_id.items():
        assert actual_by_id[scenario_id] == expected_scenario, scenario_id


def test_static_dispatch_corpus_is_cleartext_complete_and_source_bound() -> None:
    baseline = json.loads(STATIC_BASELINE.read_text("utf-8"))
    assert _static_document() == baseline
    codex, review = baseline["layers"]
    assert (len(codex["conditions"]), len(codex["aborts"])) == (20, 6)
    assert (len(review["conditions"]), len(review["aborts"])) == (15, 7)
    assert len(codex["checkpoints"]) == 5
    assert len(review["checkpoints"]) == 6
    assert review["catchers"] == [
        {"ordinal": 1, "exception_type": "NoWorkflowChangesError"},
        {"ordinal": 2, "exception_type": "ValidationExecutionError"},
        {"ordinal": 3, "exception_type": "ReviewPacketError"},
    ]
    assert all(item["expression"] for layer in (codex, review) for item in layer["conditions"])
    assert all("read_names" in item for layer in (codex, review) for item in layer["conditions"])


def test_workflow_source_is_byte_identical_to_the_b50_prestate() -> None:
    anchored_blob = subprocess.run(
        ["git", "rev-parse", f"{SOURCE_COMMIT}:src/workflow.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    actual_blob = subprocess.run(
        ["git", "hash-object", str(WORKFLOW_PATH)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert anchored_blob == SOURCE_BLOB
    assert actual_blob == SOURCE_BLOB


def test_runtime_corpus_is_built_once_and_each_rejection_matches(
    runtime_corpus: dict[str, object],
) -> None:
    baseline = json.loads(RUNTIME_BASELINE.read_text("utf-8"))
    _assert_runtime_matches(runtime_corpus, baseline)
    assert _RUNTIME_BUILD_COUNT == 1
    assert len(runtime_corpus["scenarios"]) == 13
    assert [item["scenario_id"] for item in runtime_corpus["scenarios"]] == [
        item["scenario_id"] for item in SCENARIOS
    ]
    assert sum(item["path"] == "codex" for item in runtime_corpus["scenarios"]) == 6
    assert sum(item["path"] == "review" for item in runtime_corpus["scenarios"]) == 7
    for scenario in runtime_corpus["scenarios"]:
        assert scenario["reachable"] is True
        assert scenario["structural_reason"] is None
        assert scenario["actual_error"] == scenario["expected_error"]
        assert scenario["real_provider_start_count"] == 0
        provider_input = scenario["provider_input"]
        if provider_input is not None:
            encoded = provider_input["canonical_json"].encode("utf-8")
            assert provider_input["sha256"] == hashlib.sha256(encoded).hexdigest()
            assert provider_input["utf8_bytes"] == len(encoded)


def test_runtime_rejections_are_one_to_one_with_static_abort_order() -> None:
    layers = _static_document()["layers"]
    dynamic_message_expressions = {
        "codex-plan-outside-task-scope": (
            "f'TASK-SCOPE | SLICE_PLAN contains paths outside the declared task "
            "scope: {', '.join(unexpected_plan_paths)}'"
        ),
        "review-packet-builder-error": (
            "f'canonical review packet could not be built: {exc}'"
        ),
    }
    for path, layer in zip(("codex", "review"), layers, strict=True):
        scenarios = [item for item in SCENARIOS if item["path"] == path]
        aborts = layer["aborts"]
        assert len(scenarios) == len(aborts)
        for ordinal, (scenario, abort) in enumerate(
            zip(scenarios, aborts, strict=True), 1
        ):
            assert abort["ordinal"] == ordinal
            assert scenario["error_type"] == abort["exception_type"]
            message_expression = abort["message_expression"]
            message_node = ast.parse(message_expression, mode="eval").body
            if isinstance(message_node, ast.Constant):
                assert scenario["message"] == message_node.value
            else:
                assert (
                    message_expression
                    == dynamic_message_expressions[scenario["scenario_id"]]
                )


def test_checkpoint_and_provider_dispatch_evidence_is_scenario_local(
    runtime_corpus: dict[str, object],
) -> None:
    assert _RUNTIME_BUILD_COUNT == 1
    for scenario in runtime_corpus["scenarios"]:
        assert scenario["provider_dispatch_count"] in {0, 1}
        assert all(
            set(checkpoint)
            == {
                "checkpoint_site",
                "step",
                "work_unit_status",
                "work_unit_kind",
            }
            for checkpoint in scenario["checkpoints"]
        )
        assert len({item["checkpoint_site"] for item in scenario["checkpoints"]}) == len(
            scenario["checkpoints"]
        )


@pytest.mark.parametrize(
    ("function_name", "scenario_id"),
    (
        ("_run_codex", "codex-correction-missing-start-fingerprint"),
        ("_run_review", "review-non-claude"),
    ),
)
def test_removing_one_condition_per_path_turns_its_scenario_red(
    function_name: str,
    scenario_id: str,
) -> None:
    mutant = _load_mutant(_remove_first_condition(function_name))
    spec = next(item for item in SCENARIOS if item["scenario_id"] == scenario_id)
    observed = _run_scenario(spec, mutant)
    assert observed["actual_error"] != observed["expected_error"]


def test_moving_checkpoint_by_one_statement_turns_static_binding_red() -> None:
    tree = ast.parse(WORKFLOW_PATH.read_text("utf-8"))
    _shift_first_checkpoint(tree)
    mutant = _static_document(ast.unparse(tree))
    baseline = json.loads(STATIC_BASELINE.read_text("utf-8"))
    assert mutant["layers"][0]["checkpoints"] != baseline["layers"][0]["checkpoints"]


def test_changing_one_provider_request_builder_field_names_the_scenario() -> None:
    baseline = json.loads(RUNTIME_BASELINE.read_text("utf-8"))
    mutant_module = _load_mutant(_change_first_codex_provider_request_field)
    scenario_id = "codex-non-native-result"
    spec = next(item for item in SCENARIOS if item["scenario_id"] == scenario_id)
    observed = _run_scenario(spec, mutant_module)
    assert observed["actual_error"] == observed["expected_error"]
    assert (
        json.loads(observed["provider_input"]["canonical_json"])["target_branch"]
        == "feature/b51-request-mutant"
    )
    mutant = copy.deepcopy(baseline)
    index = next(
        index
        for index, item in enumerate(mutant["scenarios"])
        if item["scenario_id"] == scenario_id
    )
    mutant["scenarios"][index] = observed
    with pytest.raises(AssertionError, match=scenario_id):
        _assert_runtime_matches(mutant, baseline)
