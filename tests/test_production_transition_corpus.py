from __future__ import annotations

import ast
import copy
from dataclasses import dataclass
import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace
from typing import Any

import pytest

from artifact_bridge import ArtifactBridgeError
from artifact_models import technical_text_evidence
from contracts import PlannedSlice
from workflow import (
    WorkflowExecutionError,
    WorkflowHistory,
    WorkflowRunResult,
)
import workflow_production
from workflow_state import (
    AgentFailureKind,
    InvocationFailureRecord,
    WorkflowState,
    WorkflowStateValidationError,
    WorkflowStep,
    WorkUnitKind,
)
from workflow_state import init_workflow_state


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/workflow_production.py"
STATIC_BASELINE = ROOT / "tests/fixtures/production-transition-pre-b48-v1.json"
RUNTIME_BASELINE = ROOT / "tests/fixtures/production-transition-runtime-pre-b48-v1.json"
RECORD_SEQUENCE_BASELINE = (
    ROOT / "tests/fixtures/workflow-record-sequence-baseline-v1.json"
)
SOURCE_TEXT = SOURCE.read_text(encoding="utf-8")
SOURCE_TREE = ast.parse(SOURCE_TEXT, filename=str(SOURCE))
B48_HELPERS = frozenset(
    {
        "_recover_final_review_history",
        "_prepare_plan_implementation_handoff",
        "_start_first_slice",
        "_start_pending_slice",
    }
)
TASK_TEXT = "\n".join(
    (
        "ORCHESTRATOR_MODE: IMPLEMENT",
        "TARGET_BRANCH: feature/backlog-followups",
        "TASK_SCOPE: src/workflow_production.py, tests/test_production_transition_corpus.py",
        "",
        "Exercise the provider-free B48 production transition corpus.",
    )
)
START_COMMIT = "a" * 40
BOUNDARY_FINGERPRINT = "1" * 64
PLAN_COMMIT = "b" * 40


@dataclass(frozen=True, slots=True)
class _LogicalEvent:
    kind: str
    signature: str
    owner: str
    node: ast.stmt
    enclosing_decisions: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _LogicalModel:
    loop_owner: ast.FunctionDef
    loop: ast.For
    events: tuple[_LogicalEvent, ...]
    decisions: tuple[dict[str, object], ...]
    catchers: tuple[dict[str, object], ...]
    result_sites: dict[tuple[str, int], str]
    error_sites: dict[tuple[str, int], str]


@dataclass(frozen=True, slots=True)
class BuiltTransitionCorpus:
    document: dict[str, object]
    build_seconds: float


SCENARIOS: tuple[dict[str, Any], ...] = (
    {"scenario_id": "waiting-quota", "state": "quota"},
    {"scenario_id": "resume-quota", "state": "quota", "resume": True, "actions": ("incomplete",)},
    {"scenario_id": "gate-reframed", "state": "gate", "reframe": True, "actions": ("incomplete",)},
    {"scenario_id": "gate-inherited", "state": "gate", "inherit_gate": True, "actions": ("incomplete",)},
    {"scenario_id": "gate-auto-resume", "state": "gate", "resume": True, "actions": ("incomplete",)},
    {"scenario_id": "gate-unresolved", "state": "gate"},
    {"scenario_id": "slice-missing-start-commit", "state": "slice_missing_start"},
    {"scenario_id": "slice-head-drift", "state": "slice_unbound", "repository_head": "c" * 40},
    {"scenario_id": "slice-boundary", "state": "slice_unbound", "actions": ("incomplete",)},
    {"scenario_id": "slice-round", "state": "slice_bound", "actions": ("incomplete",)},
    {"scenario_id": "plan-to-first-slice", "state": "plan", "actions": ("complete-plan", "incomplete")},
    {"scenario_id": "pending-slice", "state": "pending_slice", "actions": ("incomplete",)},
    {"scenario_id": "plan-only-handoff", "state": "plan_only", "actions": ("complete-plan-only",)},
    {"scenario_id": "plan-only-missing-commit", "state": "completed_plan_only_missing_commit"},
    {"scenario_id": "plan-only-bind-error", "state": "completed_plan_only", "bind_error": True},
    {"scenario_id": "plan-only-handoff-error", "state": "completed_plan_only", "handoff_error": True},
    {"scenario_id": "completed-plan-without-slices", "state": "completed_plan"},
)


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _read_names(node: ast.AST) -> list[str]:
    return sorted(
        {
            descendant.id
            for descendant in ast.walk(node)
            if isinstance(descendant, ast.Name)
            and isinstance(descendant.ctx, ast.Load)
        }
    )


def _direct_helper_call(statement: ast.stmt) -> ast.Call | None:
    value: ast.expr | None = None
    if isinstance(statement, (ast.Assign, ast.AnnAssign, ast.Expr, ast.Return)):
        value = statement.value
    if (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id in B48_HELPERS
    ):
        return value
    return None


def _is_named_call(node: ast.AST | None, name: str) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == name
    )


def _is_checkpoint(statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Call)
        and isinstance(statement.value.func, ast.Attribute)
        and isinstance(statement.value.func.value, ast.Name)
        and statement.value.func.value.id == "driver"
        and statement.value.func.attr == "checkpoint"
    )


def _transition_loop(tree: ast.Module) -> tuple[ast.FunctionDef, ast.For]:
    matches = [
        (function, node)
        for function in tree.body
        if isinstance(function, ast.FunctionDef)
        for node in function.body
        if isinstance(node, ast.For) and ast.unparse(node.iter) == "range(100)"
    ]
    assert len(matches) == 1
    return matches[0]


def _logical_model(tree: ast.Module) -> _LogicalModel:
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    loop_owner, loop = _transition_loop(tree)
    events: list[_LogicalEvent] = []
    decisions: list[dict[str, object]] = []
    catchers: list[dict[str, object]] = []
    result_sites: dict[tuple[str, int], str] = {}
    error_sites: dict[tuple[str, int], str] = {}

    def visit(
        statements: list[ast.stmt],
        *,
        owner: str,
        enclosing: tuple[int, ...] = (),
        inlined_helper: bool = False,
    ) -> None:
        for statement in statements:
            if isinstance(statement, ast.If):
                ordinal = len(decisions) + 1
                decisions.append(
                    {
                        "ordinal": ordinal,
                        "test_expression": ast.unparse(statement.test),
                        "read_names": _read_names(statement.test),
                    }
                )
                events.append(
                    _LogicalEvent(
                        "decision",
                        f"decision-{ordinal}:{ast.unparse(statement.test)}",
                        owner,
                        statement,
                        enclosing,
                    )
                )
                visit(
                    statement.body,
                    owner=owner,
                    enclosing=(*enclosing, ordinal),
                    inlined_helper=inlined_helper,
                )
                visit(
                    statement.orelse,
                    owner=owner,
                    enclosing=(*enclosing, ordinal),
                    inlined_helper=inlined_helper,
                )
                continue
            if isinstance(statement, ast.Try):
                for handler in statement.handlers:
                    catchers.append(
                        {
                            "ordinal": len(catchers) + 1,
                            "exception": ast.unparse(handler.type),
                        }
                    )
                visit(
                    statement.body,
                    owner=owner,
                    enclosing=enclosing,
                    inlined_helper=inlined_helper,
                )
                for handler in statement.handlers:
                    visit(
                        handler.body,
                        owner=owner,
                        enclosing=enclosing,
                        inlined_helper=inlined_helper,
                    )
                visit(
                    statement.orelse,
                    owner=owner,
                    enclosing=enclosing,
                    inlined_helper=inlined_helper,
                )
                visit(
                    statement.finalbody,
                    owner=owner,
                    enclosing=enclosing,
                    inlined_helper=inlined_helper,
                )
                continue
            helper_call = _direct_helper_call(statement)
            if helper_call is not None:
                helper = functions[helper_call.func.id]
                visit(
                    helper.body,
                    owner=helper.name,
                    enclosing=enclosing,
                    inlined_helper=True,
                )
                continue
            if isinstance(statement, ast.Return):
                if inlined_helper:
                    continue
                if _is_named_call(statement.value, "WorkflowRunResult"):
                    result_sites[(owner, statement.lineno)] = (
                        f"workflow-result-{len(result_sites) + 1}"
                    )
            if (
                isinstance(statement, ast.Raise)
                and _is_named_call(statement.exc, "WorkflowExecutionError")
            ):
                error_sites[(owner, statement.lineno)] = (
                    f"workflow-error-{len(error_sites) + 1}"
                )
            kind = "checkpoint" if _is_checkpoint(statement) else "statement"
            events.append(
                _LogicalEvent(
                    kind,
                    ast.unparse(statement),
                    owner,
                    statement,
                    enclosing,
                )
            )

    visit(loop.body, owner=loop_owner.name)
    loop_position = loop_owner.body.index(loop)
    for statement in loop_owner.body[loop_position + 1 :]:
        if (
            isinstance(statement, ast.Raise)
            and _is_named_call(statement.exc, "WorkflowExecutionError")
        ):
            error_sites[(loop_owner.name, statement.lineno)] = "transition-bound-error"
    return _LogicalModel(
        loop_owner,
        loop,
        tuple(events),
        tuple(decisions),
        tuple(catchers),
        result_sites,
        error_sites,
    )


def _checkpoint_facts(model: _LogicalModel) -> list[dict[str, object]]:
    facts: list[dict[str, object]] = []
    for index, event in enumerate(model.events):
        if event.kind != "checkpoint":
            continue
        facts.append(
            {
                "ordinal": len(facts) + 1,
                "enclosing_decision_ordinals": list(event.enclosing_decisions),
                "previous_event": (
                    model.events[index - 1].signature if index else None
                ),
                "next_event": (
                    model.events[index + 1].signature
                    if index + 1 < len(model.events)
                    else None
                ),
            }
        )
    return facts


def _control_transfer_facts(model: _LogicalModel) -> list[dict[str, str]]:
    controls: list[dict[str, str]] = []
    for event in model.events:
        node = event.node
        if isinstance(node, ast.Continue):
            controls.append({"kind": "continue", "expression": "continue"})
        elif isinstance(node, ast.Return) and _is_named_call(
            node.value, "WorkflowRunResult"
        ):
            controls.append({"kind": "return", "expression": ast.unparse(node.value)})
        elif isinstance(node, ast.Raise) and _is_named_call(
            node.exc, "WorkflowExecutionError"
        ):
            controls.append({"kind": "raise", "expression": ast.unparse(node.exc)})
    return controls


def _static_facts(tree: ast.Module) -> dict[str, object]:
    model = _logical_model(tree)
    return {
        "loop_expression": ast.unparse(model.loop.iter),
        "decisions": list(model.decisions),
        "catchers": list(model.catchers),
        "checkpoints": _checkpoint_facts(model),
        "control_transfers": _control_transfer_facts(model),
        "transition_bound_error": next(
            site
            for site in model.error_sites.values()
            if site == "transition-bound-error"
        ),
    }


def _args(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "plan_only": None,
        "work_plan": None,
        "target_branch": None,
        "watch_run_id": "",
        "outbox_dir": "outbox",
        "force_overwrite_state": False,
        "resume": False,
        "gate_decision": None,
        "gate_rationale": "b48 corpus",
        "auto_resume": False,
        "agent_settings": {
            "codex": SimpleNamespace(model="codex-anchor", effort="medium"),
            "claude": SimpleNamespace(model="claude-anchor", effort="high"),
        },
        "agent_output": "none",
        "agent_output_max_chars": 1000,
        "agent_live_stream": False,
        "agent_live_stream_mode": "summary",
        "agent_live_stream_channels": (),
        "strict_preflight": True,
        "repo_config": SimpleNamespace(provider_input_budget=12345),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _base_state(task: Path, *, plan_only: bool = False) -> WorkflowState:
    return init_workflow_state(
        run_id="transition-run",
        task_file=str(task.resolve()),
        branch="feature/backlog-followups",
        branch_base=START_COMMIT,
        first_slice_start_commit=START_COMMIT,
        slice_count=1,
        task_digest="a" * 64,
        execution_mode="PLAN_ONLY" if plan_only else "IMPLEMENT",
        task_scope_patterns=(
            ("docs/internal/transition-plan.md",)
            if plan_only
            else ("src/*.py", "tests/test_production_transition_corpus.py")
        ),
        work_plan_path=("docs/internal/transition-plan.md" if plan_only else None),
        target_branch="feature/backlog-followups",
        timestamp="2026-09-04T00:00:00+00:00",
    )


def _planned_state(task: Path, *, count: int = 1) -> WorkflowState:
    planned = tuple(
        PlannedSlice(index, f"slice {index}", (f"src/slice_{index}.py",))
        for index in range(1, count + 1)
    )
    return _base_state(task).bind_slice_plan(
        planned, first_start_commit=START_COMMIT
    ).complete_current_work_unit()


def _slice_state(task: Path, *, count: int = 1, bound: bool = True) -> WorkflowState:
    state = _planned_state(task, count=count).start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    )
    return (
        state.bind_current_slice_git_boundary(
            start_commit=START_COMMIT,
            scope_paths=("src/slice_1.py",),
            start_fingerprint=BOUNDARY_FINGERPRINT,
        )
        if bound
        else state
    )


def _completed_plan_only_state(task: Path) -> WorkflowState:
    planned = (PlannedSlice(1, "plan artifact", ("docs/internal/transition-plan.md",)),)
    return _base_state(task, plan_only=True).bind_slice_plan(
        planned, first_start_commit=START_COMMIT
    ).bind_current_slice_git_boundary(
        start_commit=START_COMMIT,
        scope_paths=("docs/internal/transition-plan.md",),
        start_fingerprint=BOUNDARY_FINGERPRINT,
    ).complete_current_slice(
        commit_ref=PLAN_COMMIT
    )


def _initial_state(task: Path, kind: str) -> WorkflowState:
    if kind == "plan":
        return _base_state(task)
    if kind == "plan_only":
        return _base_state(task, plan_only=True)
    if kind == "completed_plan":
        return _base_state(task).complete_current_work_unit()
    if kind in {"completed_plan_only", "completed_plan_only_missing_commit"}:
        state = _completed_plan_only_state(task)
        if kind.endswith("missing_commit"):
            object.__setattr__(state.current_slice, "commit_ref", None)
        return state
    if kind in {"slice_unbound", "slice_missing_start"}:
        state = _slice_state(task, bound=False)
        if kind.endswith("missing_start"):
            object.__setattr__(state.current_slice, "start_commit", None)
        return state
    if kind == "slice_bound":
        return _slice_state(task)
    if kind in {"completed_slice", "final", "correction"}:
        completed = _slice_state(task).complete_current_slice(commit_ref=PLAN_COMMIT)
        if kind == "completed_slice":
            return completed
        final = completed.start_final_review_work_unit()
        if kind == "final":
            return final
        return final.complete_current_work_unit().start_correction_work_unit(
            start_commit="c" * 40,
            scope_paths=("src/correction.py",),
            start_fingerprint="2" * 64,
            finding_ids=("C-01",),
        )
    if kind == "pending_slice":
        return _slice_state(task, count=2).complete_current_slice(
            commit_ref=PLAN_COMMIT
        )
    if kind == "gate":
        return _base_state(task).await_policy_gate(
            reason=workflow_production.GateReason.UNEXPECTED_FILE,
            detail="B48-GATE | provider-free transition corpus",
        )
    if kind == "quota":
        state = _base_state(task)
        failure = InvocationFailureRecord(
            invocation_id="b48-quota",
            idempotency_key="transition-run:1:codex_plan:codex",
            role="codex",
            failure_kind=AgentFailureKind.QUOTA,
            provider_text="usage cap reached",
            received_at="2026-09-04T00:00:00+00:00",
            step=WorkflowStep.CODEX_PLAN,
            slice_id=1,
            work_unit_id=1,
            diagnostic_exit_code=2,
            process_exit_code=None,
            technical_text=technical_text_evidence("provider-free quota boundary")[0],
            reset_at_utc="2026-09-04T00:01:00+00:00",
            resume_at_utc="2026-09-04T00:01:00+00:00",
            automatic_resume=True,
        )
        return state.record_invocation_failure(failure, wait_automatically=True)
    raise AssertionError(f"unknown scenario state: {kind}")


def _state_projection(state: WorkflowState) -> str:
    current = state.current_work_unit
    return "|".join(
        (
            f"work_unit={current.work_unit_id}",
            f"slice={current.slice_id}",
            f"kind={current.kind.value}",
            f"status={current.status.value}",
            f"step={current.current_step.value}",
        )
    )


def _grouped_sequence(items: list[object]) -> list[dict[str, object]]:
    groups: list[dict[str, object]] = []
    for item in items:
        if groups and groups[-1]["value"] == item:
            groups[-1]["count"] += 1
        else:
            groups.append({"count": 1, "value": item})
    return groups


def _expanded_sequence(groups: list[dict[str, object]]) -> list[object]:
    return [
        group["value"]
        for group in groups
        for _ in range(group["count"])
    ]


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class _CorpusDriver:
    def __init__(
        self,
        spec: dict[str, Any],
        checkpoints: list[dict[str, object]],
    ):
        self.spec = spec
        self.checkpoints = checkpoints
        self.active_state: WorkflowState | None = None

    def checkpoint(self, state: WorkflowState, history: WorkflowHistory) -> None:
        self.active_state = state
        self.checkpoints.append(
            {
                "state": _state_projection(state),
                "history_work_unit_id": history.work_unit_id,
            }
        )

    def finalize_audit(self, _state: WorkflowState) -> str:
        return "audit-commit"

    def assert_structured_decision_context(self) -> None:
        pass

    def prepare_finding_handoff(self, **_kwargs):
        if self.spec.get("handoff_error"):
            raise ArtifactBridgeError("provider-free handoff failure")
        return None

    def prepare_finding_cleanup(self):
        return None

    def persist_implementation_handoff(self, *_args) -> None:
        pass

    def _write_side_effect_file(self, *_args, **_kwargs) -> None:
        pass

    def carry_forward_native_findings(self, _state, findings):
        return findings


class _CorpusEngine:
    def __init__(self, driver: _CorpusDriver, spec: dict[str, Any]):
        self.driver = driver
        self.spec = spec
        self.actions = list(spec.get("actions", ()))

    def reframe_unexpected_path_stop_gate(self, state: WorkflowState) -> WorkflowState:
        return state.resume_after_user_decision() if self.spec.get("reframe") else state

    def decide_current_gate(self, *_args, **_kwargs):
        raise AssertionError("B48 corpus does not use an explicit gate decision")

    def run_current_work_unit(
        self, state: WorkflowState, _context: object, history: WorkflowHistory
    ) -> WorkflowRunResult:
        assert self.actions, f"unexpected engine call in {self.spec['scenario_id']}"
        action = self.actions.pop(0)
        if action == "incomplete":
            advanced = state
        elif action == "complete-current":
            advanced = state.complete_current_work_unit()
        elif action == "complete-plan":
            advanced = state.bind_slice_plan(
                (PlannedSlice(1, "slice 1", ("src/slice_1.py",)),),
                first_start_commit=START_COMMIT,
            ).complete_current_work_unit()
        elif action == "complete-plan-only":
            advanced = state.bind_slice_plan(
                (
                    PlannedSlice(
                        1,
                        "plan artifact",
                        ("docs/internal/transition-plan.md",),
                    ),
                ),
                first_start_commit=START_COMMIT,
            ).bind_current_slice_git_boundary(
                start_commit=START_COMMIT,
                scope_paths=("docs/internal/transition-plan.md",),
                start_fingerprint=BOUNDARY_FINGERPRINT,
            ).complete_current_slice(
                commit_ref=PLAN_COMMIT
            )
        else:
            raise AssertionError(f"unknown engine action: {action}")
        self.driver.active_state = advanced
        return WorkflowRunResult(advanced, history)


def _site_wrappers(model: _LogicalModel, captured: list[str]):
    real_result = WorkflowRunResult
    real_error = WorkflowExecutionError

    def result_wrapper(*args, **kwargs):
        frame = inspect.currentframe()
        assert frame is not None and frame.f_back is not None
        caller = frame.f_back
        captured.append(model.result_sites[(caller.f_code.co_name, caller.f_lineno)])
        return real_result(*args, **kwargs)

    def error_wrapper(*args, **kwargs):
        frame = inspect.currentframe()
        assert frame is not None and frame.f_back is not None
        caller = frame.f_back
        captured.append(model.error_sites[(caller.f_code.co_name, caller.f_lineno)])
        return real_error(*args, **kwargs)

    return result_wrapper, error_wrapper


def _run_scenario(base: Path, spec: dict[str, Any]) -> dict[str, object]:
    scenario_root = base / spec["scenario_id"]
    inbox = scenario_root / "inbox"
    inbox.mkdir(parents=True)
    task = inbox / "task.md"
    task.write_text(TASK_TEXT, encoding="utf-8")
    initial = _initial_state(task, spec["state"])
    rounds: list[object] = []
    checkpoints: list[dict[str, object]] = []
    current_round = [0]
    sites: list[str] = []
    driver = _CorpusDriver(spec, checkpoints)
    engine = _CorpusEngine(driver, spec)
    model = _logical_model(SOURCE_TREE)
    result_wrapper, error_wrapper = _site_wrappers(model, sites)

    def recover_history(state, history, *_args):
        current_round[0] += 1
        rounds.append(_state_projection(state))
        if spec.get("recover_history") and current_round[0] == 1:
            return WorkflowHistory(history.work_unit_id + 100)
        return history

    def inherited_gate(state):
        return state.resume_after_user_decision() if spec.get("inherit_gate") else state

    dependencies = workflow_production.ProductionWorkflowDependencies(
        driver_factory=lambda **_kwargs: driver,
        apply_resumed_agent_profiles=lambda *_args: None,
        archive_stale_untracked_audit_reports=lambda *_args, **_kwargs: None,
        attach_managed_audit_paths=lambda state: state,
        bound_task_control_paths=lambda *_args: (),
        context=lambda **_kwargs: None,
        current_gate_approval=lambda _state: None,
        fresh_state=lambda **_kwargs: initial,
        history=lambda state, _root: WorkflowHistory(state.current_work_unit_id),
        inherit_redundant_test_gate=inherited_gate,
        initialize_finding_handoff=lambda _root, state, _contract, _bytes: state,
        managed_audit_path=lambda *_args: None,
        new_watch_task_control_paths=lambda *_args: (),
        new_watch_task_preserved_paths=lambda *_args: (),
        recover_final_review_attestation=recover_history,
        recover_legacy_plan_only_post_gate=lambda state: state,
        unused_run_id=lambda *_args: "transition-run",
    )
    args = _args(resume=spec.get("resume", False))
    outcome: dict[str, object]

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.chdir(scenario_root)
        monkeypatch.setattr(workflow_production, "new_run_id", lambda: "candidate")
        monkeypatch.setattr(workflow_production, "watch_run_has_records", lambda *_args: False)
        monkeypatch.setattr(workflow_production, "build_agent_registry", lambda _settings: {})
        monkeypatch.setattr(workflow_production, "require_production_workflow_loop_driver", lambda _driver: None)
        monkeypatch.setattr(workflow_production, "WorkflowEngine", lambda _driver: engine)
        monkeypatch.setattr(workflow_production, "_create_production_state", lambda **_kwargs: initial)
        monkeypatch.setattr(workflow_production, "_validate_resumed_state", lambda *_args: initial)
        monkeypatch.setattr(workflow_production, "load_resumable_workflow_state", lambda *_args, **_kwargs: initial)
        monkeypatch.setattr(workflow_production, "resolve_resume_state", lambda *_args: SimpleNamespace(replay_result=None))
        monkeypatch.setattr(workflow_production, "inspect_repository", lambda _root: SimpleNamespace(head=spec.get("repository_head", START_COMMIT)))
        monkeypatch.setattr(workflow_production, "collect_repository_changes", lambda *_args, **_kwargs: SimpleNamespace(fingerprint=BOUNDARY_FINGERPRINT))
        monkeypatch.setattr(workflow_production, "write_implementation_handoff", lambda **_kwargs: inbox / "handoff.md")
        monkeypatch.setattr(workflow_production, "WorkflowRunResult", result_wrapper)
        monkeypatch.setattr(workflow_production, "WorkflowExecutionError", error_wrapper)
        if spec.get("bind_error"):
            monkeypatch.setattr(
                WorkflowState,
                "bind_completed_plan_commit",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    WorkflowStateValidationError("provider-free bind failure")
                ),
            )
        if spec.get("overflow"):
            monkeypatch.setattr(WorkflowState, "start_final_review_work_unit", lambda state: state)
        try:
            result = workflow_production.run_production_workflow(
                task, args, dependencies
            )
            assert len(sites) == 1
            outcome = {
                "type": "WorkflowRunResult",
                "site": sites[0],
                "state": _state_projection(result.state),
                "commit_ref": result.commit_ref,
            }
        except WorkflowExecutionError as exc:
            assert len(sites) == 1
            outcome = {
                "type": "WorkflowExecutionError",
                "site": sites[0],
                "message": str(exc),
            }

    return {
        "scenario_id": spec["scenario_id"],
        "rounds": _grouped_sequence(rounds),
        "initial_checkpoint": checkpoints[0],
        "round_checkpoints": _grouped_sequence(checkpoints[1:]),
        "outcome": outcome,
    }


_CORPUS_BUILD_COUNT = 0


def _build_runtime_corpus(base: Path) -> BuiltTransitionCorpus:
    global _CORPUS_BUILD_COUNT
    _CORPUS_BUILD_COUNT += 1
    started = time.perf_counter()
    document = {
        "schema_version": "production-transition-runtime-pre-b48-v1",
        "source_commit": "d3f30ef075909fbc93c73cb8e5ea378dcfd893ec",
        "source_blob": "c3e83c1f014b84e142a1ac439b05c758086bf0bf",
        "scenarios": [_run_scenario(base, spec) for spec in SCENARIOS],
    }
    return BuiltTransitionCorpus(document, time.perf_counter() - started)


def _load_json(path: Path) -> dict[str, object]:
    document = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


@pytest.fixture(scope="session")
def production_transition_corpus(
    tmp_path_factory: pytest.TempPathFactory,
) -> BuiltTransitionCorpus:
    return _build_runtime_corpus(tmp_path_factory.mktemp("b48-production-transition"))


def test_pre_b48_transition_anchor_is_bound_to_git_and_logical_loop() -> None:
    baseline = _load_json(STATIC_BASELINE)
    anchored_blob = subprocess.check_output(
        ("git", "rev-parse", f"{baseline['source_commit']}:src/workflow_production.py"),
        cwd=ROOT,
        text=True,
    ).strip()
    assert anchored_blob == baseline["source_blob"]
    facts = _static_facts(SOURCE_TREE)
    assert _canonical_sha256(facts) == baseline["facts_sha256"]
    assert len(facts["decisions"]) == 24
    assert len(facts["catchers"]) == 2
    assert len(facts["checkpoints"]) == 10


def test_provider_free_transition_corpus_matches_pre_cut_baseline(
    production_transition_corpus: BuiltTransitionCorpus,
) -> None:
    baseline = _load_json(RUNTIME_BASELINE)
    actual = production_transition_corpus.document
    assert actual["schema_version"] == baseline["schema_version"]
    assert actual["source_commit"] == baseline["source_commit"]
    assert actual["source_blob"] == baseline["source_blob"]
    scenarios = actual["scenarios"]
    assert [item["scenario_id"] for item in scenarios] == baseline["scenario_order"]
    assert {
        item["scenario_id"]: _canonical_sha256(item) for item in scenarios
    } == baseline["scenario_sha256"]


def test_transition_corpus_builds_once_and_covers_required_boundaries(
    production_transition_corpus: BuiltTransitionCorpus,
) -> None:
    assert _CORPUS_BUILD_COUNT == 1
    assert production_transition_corpus.build_seconds > 0
    scenarios = {
        item["scenario_id"]: item
        for item in production_transition_corpus.document["scenarios"]
    }
    assert set(scenarios) == {spec["scenario_id"] for spec in SCENARIOS}
    assert len({item["outcome"]["site"] for item in scenarios.values()}) == 10
    waiting_rounds = _expanded_sequence(scenarios["waiting-quota"]["rounds"])
    assert "status=waiting_for_quota" in waiting_rounds[0]
    plan_rounds = _expanded_sequence(scenarios["plan-to-first-slice"]["rounds"])
    assert ["kind=plan" in item for item in plan_rounds] == [True, False]
    assert ["kind=slice" in item for item in plan_rounds] == [False, True]
    assert not ({"correction-round", "final-review-entry"} & set(scenarios))


def test_checkpoint_shift_by_one_instruction_turns_anchor_red() -> None:
    mutant = copy.deepcopy(SOURCE_TREE)
    loop_owner, _loop = _transition_loop(mutant)
    helper_functions = [
        node
        for node in mutant.body
        if isinstance(node, ast.FunctionDef) and node.name in B48_HELPERS
    ]
    bodies = [
        body
        for function in (loop_owner, *helper_functions)
        for node in ast.walk(function)
        for body in (getattr(node, "body", None), getattr(node, "orelse", None))
        if isinstance(body, list)
    ]
    target = next(
        (body, index)
        for body in bodies
        for index, statement in enumerate(body)
        if index > 0
        and _is_checkpoint(statement)
        and "resume_after_invocation_halt" in ast.unparse(body[index - 1])
    )
    body, index = target
    body[index - 1], body[index] = body[index], body[index - 1]
    assert _canonical_sha256(_static_facts(mutant)) != _load_json(STATIC_BASELINE)[
        "facts_sha256"
    ]


def test_swapping_two_status_checks_turns_anchor_red() -> None:
    mutant = copy.deepcopy(SOURCE_TREE)
    model = _logical_model(mutant)
    status_checks = [
        event.node
        for event in model.events
        if event.kind == "decision"
        and isinstance(event.node, ast.If)
        and "current.status" in ast.unparse(event.node.test)
    ]
    assert len(status_checks) >= 2
    status_checks[0].test, status_checks[1].test = (
        status_checks[1].test,
        status_checks[0].test,
    )
    assert _canonical_sha256(_static_facts(mutant)) != _load_json(STATIC_BASELINE)[
        "facts_sha256"
    ]


def test_b48_helpers_are_bounded_and_production_entry_shrinks() -> None:
    functions = {
        node.name: node
        for node in SOURCE_TREE.body
        if isinstance(node, ast.FunctionDef)
    }
    entry = functions["run_production_workflow"]
    assert entry.end_lineno is not None
    assert entry.end_lineno - entry.lineno + 1 < 200
    for helper_name in (*B48_HELPERS, "_run_production_transition_loop"):
        helper = functions[helper_name]
        assert helper.end_lineno is not None
        assert helper.end_lineno - helper.lineno + 1 < 200


def test_b25_record_sequence_baseline_remains_byte_identical() -> None:
    working_blob = subprocess.check_output(
        ("git", "hash-object", str(RECORD_SEQUENCE_BASELINE)),
        cwd=ROOT,
        text=True,
    ).strip()
    head_blob = subprocess.check_output(
        ("git", "rev-parse", f"HEAD:{RECORD_SEQUENCE_BASELINE.relative_to(ROOT).as_posix()}"),
        cwd=ROOT,
        text=True,
    ).strip()
    assert working_blob == head_blob
