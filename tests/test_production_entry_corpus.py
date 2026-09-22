from __future__ import annotations

import ast
import copy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from state_io import ActiveV2StateError, StateSchemaError
from task_contract import parse_task_contract
from workflow import WorkflowHistory
import workflow_production
from workflow_state import init_workflow_state


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/workflow_production.py"
STATIC_BASELINE = ROOT / "tests/fixtures/production-entry-pre-b46-v1.json"
RUNTIME_BASELINE = ROOT / "tests/fixtures/production-entry-runtime-pre-b46-v1.json"
RUNTIME_B47_EXTENSION = (
    ROOT / "tests/fixtures/production-entry-runtime-b47-extension-v1.json"
)
DERIVED_TERMS_BASELINE = (
    ROOT / "tests/fixtures/production-entry-derived-terms-b47-v1.json"
)
SOURCE_TEXT = SOURCE.read_text(encoding="utf-8")
SOURCE_TREE = ast.parse(SOURCE_TEXT, filename=str(SOURCE))
ENTRY_HELPERS = frozenset(
    {
        "_read_production_task",
        "_prepare_new_watch_task",
        "_validate_resumed_state",
        "_create_production_state",
    }
)
TRANSITION_HELPERS = frozenset(
    {
        "_prepare_plan_implementation_handoff",
    }
)
TASK_TEXT = "\n".join(
    (
        "ORCHESTRATOR_MODE: IMPLEMENT",
        "TARGET_BRANCH: feature/backlog-followups",
        "TASK_SCOPE: src/workflow_production.py, tests/test_production_entry_corpus.py",
        "",
        "Exercise the provider-free B46 production-entry corpus.",
    )
)


class _EntryCaptured(BaseException):
    """Stop after the first checkpoint, before the untouched transition loop."""


@dataclass(frozen=True, slots=True)
class BuiltEntryCorpus:
    document: dict[str, object]
    build_seconds: float


SCENARIOS: tuple[dict[str, Any], ...] = (
    {"scenario_id": "new-no-state", "classification": "new"},
    {
        "scenario_id": "new-watch-task",
        "classification": "new",
        "watch_run_id": "watch-new",
    },
    {
        "scenario_id": "resume",
        "classification": "resuming",
        "state_exists": True,
        "resume": True,
    },
    {
        "scenario_id": "forced-replacement",
        "classification": "replacing",
        "state_exists": True,
        "force_new": True,
    },
    {
        "scenario_id": "replacement-state-schema-error",
        "classification": "replacing",
        "state_exists": True,
        "force_new": True,
        "load_error": "StateSchemaError",
    },
    {
        "scenario_id": "active-v2-without-overwrite",
        "classification": "resuming",
        "state_exists": True,
        "resume": True,
        "resume_error": "ActiveV2StateError",
    },
    {
        "scenario_id": "active-v2-with-overwrite",
        "classification": "resuming",
        "state_exists": True,
        "resume": True,
        "force_overwrite_state": True,
        "resume_error": "ActiveV2StateError",
    },
    {
        "scenario_id": "resume-task-file-mismatch",
        "classification": "resuming",
        "state_exists": True,
        "resume": True,
        "mutation": ("task_file", "/different/task.md"),
    },
    {
        "scenario_id": "resume-missing-task-digest",
        "classification": "resuming",
        "state_exists": True,
        "resume": True,
        "mutation": ("task_digest", None),
    },
    {
        "scenario_id": "resume-task-digest-mismatch",
        "classification": "resuming",
        "state_exists": True,
        "resume": True,
        "mutation": ("task_digest", "f" * 64),
    },
    {
        "scenario_id": "resume-execution-mode-mismatch",
        "classification": "resuming",
        "state_exists": True,
        "resume": True,
        "mutation": ("execution_mode", "PLAN_ONLY"),
    },
    {
        "scenario_id": "resume-scope-mismatch",
        "classification": "resuming",
        "state_exists": True,
        "resume": True,
        "mutation": ("task_scope_patterns", ("src/other.py",)),
    },
    {
        "scenario_id": "resume-work-plan-mismatch",
        "classification": "resuming",
        "state_exists": True,
        "resume": True,
        "mutation": ("work_plan_path", "docs/internal/other.md"),
    },
    {
        "scenario_id": "resume-target-branch-mismatch",
        "classification": "resuming",
        "state_exists": True,
        "resume": True,
        "mutation": ("target_branch", "feature/other"),
    },
    {
        "scenario_id": "resume-watch-run-mismatch",
        "classification": "resuming",
        "state_exists": True,
        "resume": True,
        "watch_run_id": "different-watch-run",
    },
    {
        "scenario_id": "existing-state-without-selection",
        "classification": "new",
        "state_exists": True,
    },
    {
        "scenario_id": "watch-records-without-state",
        "classification": "resuming",
        "watch_run_id": "watch-records-without-state",
        "persisted_run_id": "watch-records-without-state",
        "resume": True,
        "records_exist": True,
        "bind_branch_prepared": True,
    },
    {
        "scenario_id": "watch-records-without-state-force-new",
        "classification": "new",
        "watch_run_id": "watch-records-without-state-force-new",
        "persisted_run_id": "watch-records-without-state-force-new",
        "resume": True,
        "records_exist": True,
        "force_new": True,
        "bind_branch_prepared": True,
    },
    {
        "scenario_id": "watch-records-with-state",
        "classification": "resuming",
        "watch_run_id": "watch-records-with-state",
        "persisted_run_id": "watch-records-with-state",
        "state_exists": True,
        "resume": True,
        "records_exist": True,
        "bind_branch_prepared": True,
    },
    {
        "scenario_id": "watch-without-records-or-state",
        "classification": "new",
        "watch_run_id": "watch-without-records-or-state",
        "persisted_run_id": "watch-without-records-or-state",
        "resume": True,
        "bind_branch_prepared": True,
    },
)

DERIVED_TERM_NAMES = (
    "new_watch_task",
    "replacement_requested",
    "effective_resume",
)
TERM_MUTATION_SCENARIOS = {
    "new_watch_task": "watch-records-without-state",
    "replacement_requested": "existing-state-without-selection",
    "effective_resume": "watch-without-records-or-state",
}
TERM_MUTATION_EXPECTATIONS = {
    "new_watch_task": {
        "classification": "new",
        "new_watch_task": True,
        "branch_prepared": True,
        "has_loop_boundary": True,
        "driver_factory_calls": [{"replace_existing_run_id": None}],
        "has_first_checkpoint": True,
        "error_type": None,
    },
    "replacement_requested": {
        "classification": "replacing",
        "new_watch_task": False,
        "branch_prepared": None,
        "has_loop_boundary": True,
        "driver_factory_calls": [
            {"replace_existing_run_id": "persisted-run"}
        ],
        "has_first_checkpoint": True,
        "error_type": None,
    },
    "effective_resume": {
        "classification": "new",
        "new_watch_task": True,
        "branch_prepared": True,
        "has_loop_boundary": False,
        "driver_factory_calls": [],
        "has_first_checkpoint": False,
        "error_type": "StateSchemaError",
    },
}


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _body_sha256(body: list[ast.stmt]) -> str:
    semantic = ast.dump(
        ast.Module(body=body, type_ignores=[]),
        annotate_fields=True,
        include_attributes=False,
    )
    return hashlib.sha256(semantic.encode("utf-8")).hexdigest()


def _decision_fact(node: ast.If, ordinal: int) -> dict[str, object]:
    return {
        "ordinal": ordinal,
        "test_expression": ast.unparse(node.test),
        "read_names": sorted(
            {
                descendant.id
                for descendant in ast.walk(node.test)
                if isinstance(descendant, ast.Name)
                and isinstance(descendant.ctx, ast.Load)
            }
        ),
    }


def _assignment(tree: ast.Module, name: str) -> ast.Assign:
    owner_name = (
        "_read_production_task"
        if name == "new_watch_task"
        else "run_production_workflow"
    )
    owner = _function(tree, owner_name)
    matches = [
        node
        for node in ast.walk(owner)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == name
    ]
    assert len(matches) == 1
    return matches[0]


def _assignment_fact(tree: ast.Module, name: str) -> dict[str, object]:
    assignment = _assignment(tree, name)
    return {
        "name": name,
        "assignment_expression": ast.unparse(assignment.value),
        "read_names": sorted(
            {
                node.id
                for node in ast.walk(assignment.value)
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
            }
        ),
    }


def _ordered_entry_decisions(tree: ast.Module) -> list[dict[str, object]]:
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    decisions: list[ast.If] = []

    def visit(statements: list[ast.stmt]) -> None:
        for statement in statements:
            if isinstance(statement, ast.For):
                continue
            if isinstance(statement, ast.If):
                decisions.append(statement)
                visit(statement.body)
                visit(statement.orelse)
                continue
            if isinstance(statement, ast.Try):
                visit(statement.body)
                for handler in statement.handlers:
                    visit(handler.body)
                visit(statement.orelse)
                visit(statement.finalbody)
                continue
            helper_calls = sorted(
                (
                    call
                    for call in ast.walk(statement)
                    if isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id in ENTRY_HELPERS
                ),
                key=lambda call: (call.lineno, call.col_offset),
            )
            for call in helper_calls:
                visit(functions[call.func.id].body)

    entry = _function(tree, "run_production_workflow")
    visit(entry.body)
    return [_decision_fact(node, index) for index, node in enumerate(decisions, 1)]


def _catcher_inventory(tree: ast.Module) -> list[dict[str, object]]:
    entry = _function(tree, "run_production_workflow")
    transition_loop = _function(tree, "_run_production_transition_loop")
    parents = {
        child: parent
        for owner in (entry, transition_loop)
        for parent in ast.walk(owner)
        for child in ast.iter_child_nodes(parent)
    }
    handlers = sorted(
        (
            node
            for owner in (entry, transition_loop)
            for node in ast.walk(owner)
            if isinstance(node, ast.ExceptHandler)
        ),
        key=lambda node: node.lineno,
    )

    def expanded_body(body: list[ast.stmt]) -> list[ast.stmt]:
        expanded: list[ast.stmt] = []
        for statement in body:
            calls = [
                node
                for node in ast.walk(statement)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in TRANSITION_HELPERS
            ]
            if len(calls) == 1:
                helper = _function(tree, calls[0].func.id)
                expanded.extend(
                    item for item in helper.body if not isinstance(item, ast.Return)
                )
            else:
                expanded.append(statement)
        return expanded

    return [
        {
            "ordinal": index,
            "exception": ast.unparse(handler.type),
            "protected_body_sha256": _body_sha256(
                expanded_body(parents[handler].body)
            ),
            "handler_body_sha256": _body_sha256(handler.body),
        }
        for index, handler in enumerate(handlers, 1)
    ]


def _transition_loop_sha256(tree: ast.Module, source: str) -> str:
    entry = _function(tree, "run_production_workflow")
    loop = next(node for node in entry.body if isinstance(node, ast.For))
    segment = ast.get_source_segment(source, loop)
    assert segment is not None
    return hashlib.sha256(segment.encode("utf-8")).hexdigest()


def _args(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "plan_only": None,
        "work_plan": None,
        "target_branch": None,
        "watch_run_id": "",
        "outbox_dir": "outbox",
        "force_overwrite_state": False,
        "resume": False,
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


def _state(task: Path, run_id: str):
    contract = parse_task_contract(TASK_TEXT, source_name=task.name)
    return init_workflow_state(
        run_id=run_id,
        task_file=str(task),
        branch=contract.target_branch,
        branch_base="a" * 40,
        first_slice_start_commit="a" * 40,
        slice_count=1,
        task_digest=contract.digest,
        execution_mode=contract.mode.value,
        task_scope_patterns=contract.scope_patterns,
        work_plan_path=contract.work_plan_path,
        audit_report_path=None,
        target_branch=contract.target_branch,
        timestamp="2026-09-04T00:00:00+00:00",
    )


def _state_projection(state: object, scenario_root: Path) -> dict[str, object]:
    task_file = Path(state.task_file)
    try:
        normalized_task = task_file.relative_to(scenario_root).as_posix()
    except ValueError:
        normalized_task = task_file.as_posix()
    return {
        "run_id": state.run_id,
        "task_file": normalized_task,
        "task_digest": state.task_digest,
        "execution_mode": state.execution_mode,
        "task_scope_patterns": list(state.task_scope_patterns),
        "audit_report_path": state.audit_report_path,
    }


def _run_scenario(
    base: Path,
    spec: dict[str, Any],
    *,
    production_module: ModuleType = workflow_production,
) -> dict[str, object]:
    scenario_root = base / spec["scenario_id"]
    inbox = scenario_root / "inbox"
    inbox.mkdir(parents=True)
    task = inbox / "task.md"
    task.write_text(TASK_TEXT, encoding="utf-8")
    state_file = scenario_root / ".orchestrator" / "state.json"
    if spec.get("state_exists"):
        state_file.parent.mkdir(parents=True)
        state_file.write_text("pre-cut cache sentinel\n", encoding="utf-8")

    loaded_state = _state(
        task.resolve(), spec.get("persisted_run_id", "persisted-run")
    )
    mutation = spec.get("mutation")
    if mutation is not None:
        object.__setattr__(loaded_state, mutation[0], mutation[1])

    checkpoint_records: list[dict[str, object]] = []
    boundary_states: list[dict[str, object]] = []
    factory_calls: list[dict[str, object]] = []
    fresh_state_calls: list[None] = []
    load_workflow_calls: list[None] = []
    resumed_profile_calls: list[None] = []
    prepared_branch_calls: list[None] = []
    observed_new_watch_task: list[bool] = []

    class CapturingDriver:
        def checkpoint(self, state, history) -> None:
            boundary_states.append(_state_projection(state, scenario_root))
            checkpoint_records.append(
                {
                    "checkpoint_ordinal": len(checkpoint_records) + 1,
                    "state_run_id": state.run_id,
                    "history_work_unit_id": history.work_unit_id,
                }
            )
            raise _EntryCaptured

    def driver_factory(**kwargs):
        factory_calls.append(
            {"replace_existing_run_id": kwargs["replace_existing_run_id"]}
        )
        return CapturingDriver()

    def fresh_state(**kwargs):
        fresh_state_calls.append(None)
        contract = kwargs["task_contract"]
        return init_workflow_state(
            run_id=kwargs["run_id"],
            task_file=str(kwargs["task_file"]),
            branch=contract.target_branch,
            branch_base=kwargs["branch_base_override"] or "a" * 40,
            first_slice_start_commit=kwargs["branch_base_override"] or "a" * 40,
            slice_count=1,
            task_digest=contract.digest,
            execution_mode=contract.mode.value,
            task_scope_patterns=contract.scope_patterns,
            work_plan_path=contract.work_plan_path,
            audit_report_path=kwargs["audit_report_path"],
            target_branch=contract.target_branch,
            timestamp="2026-09-04T00:00:00+00:00",
        )

    def apply_resumed_agent_profiles(_args, _state) -> None:
        resumed_profile_calls.append(None)

    dependencies = production_module.ProductionWorkflowDependencies(
        driver_factory=driver_factory,
        apply_resumed_agent_profiles=apply_resumed_agent_profiles,
        archive_stale_untracked_audit_reports=lambda *_args, **_kwargs: None,
        attach_managed_audit_paths=lambda state: state,
        bound_task_control_paths=lambda *_args: (),
        context=lambda **_kwargs: None,
        current_gate_approval=lambda _state: None,
        fresh_state=fresh_state,
        history=lambda state, _root: WorkflowHistory(state.current_work_unit_id),
        inherit_redundant_test_gate=lambda state: state,
        managed_audit_path=lambda _task, _digest: "docs/internal/task-audit.md",
        new_watch_task_control_paths=lambda *_args: (),
        new_watch_task_preserved_paths=lambda *_args: (),
        recover_final_review_attestation=lambda state, history, *_args: history,
        recover_legacy_plan_only_post_gate=lambda state: state,
        unused_run_id=lambda _root, _candidate: "new-run",
    )
    args = _args(
        watch_run_id=spec.get("watch_run_id", ""),
        resume=spec.get("resume", False),
        force_overwrite_state=spec.get("force_overwrite_state", False),
    )

    def load_workflow(*_args, **_kwargs):
        load_workflow_calls.append(None)
        if spec.get("load_error") == "StateSchemaError":
            raise StateSchemaError("replacement cache is malformed")
        return loaded_state

    def load_resumable(*_args, **_kwargs):
        if spec.get("resume_error") == "ActiveV2StateError":
            raise ActiveV2StateError("active v2 state requires a choice")
        return loaded_state

    prepared = SimpleNamespace(
        action="prepared",
        previous_branch="feature/source",
        identity=SimpleNamespace(
            branch="feature/backlog-followups",
            head="b" * 40,
        ),
    )

    def prepare_branch(*_args, **_kwargs):
        prepared_branch_calls.append(None)
        return prepared

    error: dict[str, str] | None = None
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.chdir(scenario_root)
        original_prepare_new_watch_task = production_module._prepare_new_watch_task

        def capture_new_watch_task(**kwargs):
            observed_new_watch_task.append(kwargs["new_watch_task"])
            return original_prepare_new_watch_task(**kwargs)

        monkeypatch.setattr(
            production_module,
            "_prepare_new_watch_task",
            capture_new_watch_task,
        )
        monkeypatch.setattr(production_module, "new_run_id", lambda: "candidate-run")
        monkeypatch.setattr(
            production_module,
            "watch_run_has_records",
            lambda *_args: spec.get("records_exist", False),
        )
        monkeypatch.setattr(
            production_module,
            "prepare_new_watch_task_branch",
            prepare_branch,
        )
        monkeypatch.setattr(production_module, "load_workflow_state", load_workflow)
        monkeypatch.setattr(
            production_module, "load_resumable_workflow_state", load_resumable
        )
        monkeypatch.setattr(
            production_module, "build_agent_registry", lambda _settings: {}
        )
        monkeypatch.setattr(
            production_module,
            "require_production_workflow_loop_driver",
            lambda _driver: None,
        )
        monkeypatch.setattr(
            production_module, "WorkflowEngine", lambda _driver: object()
        )
        try:
            production_module.run_production_workflow(
                task,
                args,
                dependencies,
                force_new=spec.get("force_new", False),
            )
        except _EntryCaptured:
            pass
        except (ActiveV2StateError, StateSchemaError) as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}

    if resumed_profile_calls:
        classification = "resuming"
    elif fresh_state_calls:
        classification = "replacing" if load_workflow_calls else "new"
    else:
        classification = spec["classification"]
    assert len(observed_new_watch_task) == 1
    new_watch_task = observed_new_watch_task[0]
    state_projection = boundary_states[0] if boundary_states else None
    result = {
        "scenario_id": spec["scenario_id"],
        "classification": classification,
        "new_watch_task": new_watch_task,
        "state_at_loop_boundary": state_projection,
        "driver_factory_calls": factory_calls,
        "first_checkpoint_records": checkpoint_records,
        "error": error,
    }
    if spec.get("bind_branch_prepared"):
        result["branch_prepared"] = bool(prepared_branch_calls)
    return result


def _remove_derived_term_conjunct(tree: ast.Module, name: str) -> None:
    expression = _assignment(tree, name).value
    if name == "new_watch_task":
        assert isinstance(expression, ast.BoolOp) and isinstance(expression.op, ast.And)
        choice = expression.values[1]
        assert isinstance(choice, ast.BoolOp) and isinstance(choice.op, ast.Or)
        record_guard = choice.values[1]
        assert isinstance(record_guard, ast.BoolOp) and isinstance(
            record_guard.op, ast.And
        )
        assert ast.unparse(record_guard.values[1]) == (
            "not watch_run_has_records(root, run_id)"
        )
        choice.values[1] = record_guard.values[0]
        return
    if name == "replacement_requested":
        assert isinstance(expression, ast.BoolOp) and isinstance(expression.op, ast.Or)
        overwrite_guard = expression.values[1]
        assert isinstance(overwrite_guard, ast.BoolOp) and isinstance(
            overwrite_guard.op, ast.And
        )
        assert ast.unparse(overwrite_guard.values[0]) == (
            "bool(args.force_overwrite_state)"
        )
        expression.values[1] = overwrite_guard.values[1]
        return
    if name == "effective_resume":
        assert isinstance(expression, ast.Call) and len(expression.args) == 1
        resume_guard = expression.args[0]
        assert isinstance(resume_guard, ast.BoolOp) and isinstance(
            resume_guard.op, ast.And
        )
        assert ast.unparse(resume_guard.values[1]) == "not new_watch_task"
        expression.args[0] = resume_guard.values[0]
        return
    raise AssertionError(f"unbound derived term: {name}")


def _compile_derived_term_mutant(name: str) -> ModuleType:
    tree = copy.deepcopy(SOURCE_TREE)
    _remove_derived_term_conjunct(tree, name)
    ast.fix_missing_locations(tree)
    module_name = f"_b47_workflow_production_mutant_{name}"
    mutant = ModuleType(module_name)
    mutant.__file__ = str(SOURCE)
    mutant.__package__ = ""
    sys.modules[module_name] = mutant
    try:
        exec(compile(tree, str(SOURCE), "exec"), mutant.__dict__)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return mutant


_CORPUS_BUILD_COUNT = 0


def _build_runtime_corpus(base: Path) -> BuiltEntryCorpus:
    global _CORPUS_BUILD_COUNT
    _CORPUS_BUILD_COUNT += 1
    started = time.perf_counter()
    document = {
        "schema_version": "production-entry-runtime-pre-b46-v1",
        "source_commit": "cdc5f334186b41b45e4642d62066d111cdc3009c",
        "source_blob": "447221b54cb2901d84da5cf19d6ff3cd77eb88f9",
        "scenarios": [_run_scenario(base, spec) for spec in SCENARIOS],
    }
    return BuiltEntryCorpus(document, time.perf_counter() - started)


def _load_json(path: Path) -> dict[str, object]:
    document = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _expected_runtime_corpus() -> dict[str, object]:
    baseline = _load_json(RUNTIME_BASELINE)
    extension = _load_json(RUNTIME_B47_EXTENSION)
    assert extension["schema_version"] == "production-entry-runtime-b47-extension-v1"
    assert extension["extends"] == RUNTIME_BASELINE.name
    return {
        **baseline,
        "scenarios": [*baseline["scenarios"], *extension["scenarios"]],
    }


@pytest.fixture(scope="session")
def production_entry_corpus(
    tmp_path_factory: pytest.TempPathFactory,
) -> BuiltEntryCorpus:
    return _build_runtime_corpus(tmp_path_factory.mktemp("b46-production-entry"))


def test_pre_cut_anchor_is_bound_to_git_and_current_logical_entry() -> None:
    baseline = _load_json(STATIC_BASELINE)
    assert baseline["schema_version"] == "production-entry-pre-b46-v1"
    anchored_blob = subprocess.check_output(
        (
            "git",
            "rev-parse",
            f"{baseline['source_commit']}:src/workflow_production.py",
        ),
        cwd=ROOT,
        text=True,
    ).strip()
    assert anchored_blob == baseline["source_blob"]
    expected = [
        {key: value for key, value in item.items() if key != "pre_cut_line"}
        for item in baseline["entry_decisions"]
    ]
    assert len(expected) == 18
    assert _ordered_entry_decisions(SOURCE_TREE) == expected


def test_four_catchers_and_transition_loop_match_pre_cut_anchor() -> None:
    baseline = _load_json(STATIC_BASELINE)
    expected = [
        {key: value for key, value in item.items() if key != "pre_cut_line"}
        for item in baseline["catchers"]
    ]
    assert len(expected) == 4
    assert _catcher_inventory(SOURCE_TREE) == expected
    anchored_source = subprocess.check_output(
        (
            "git",
            "show",
            f"{baseline['source_commit']}:src/workflow_production.py",
        ),
        cwd=ROOT,
        text=True,
    )
    assert _transition_loop_sha256(
        ast.parse(anchored_source), anchored_source
    ) == baseline[
        "transition_loop_sha256"
    ]


def test_b47_derived_term_assignments_are_anchored() -> None:
    baseline = _load_json(DERIVED_TERMS_BASELINE)
    assert [
        _assignment_fact(SOURCE_TREE, name) for name in DERIVED_TERM_NAMES
    ] == baseline["derived_terms"]


def test_provider_free_runtime_corpus_matches_pre_cut_baseline(
    production_entry_corpus: BuiltEntryCorpus,
) -> None:
    assert production_entry_corpus.document == _expected_runtime_corpus()


def test_runtime_scenarios_are_built_once_and_cover_all_entry_contract_guards(
    production_entry_corpus: BuiltEntryCorpus,
) -> None:
    assert _CORPUS_BUILD_COUNT == 1
    assert production_entry_corpus.build_seconds > 0
    scenarios = {
        item["scenario_id"]: item
        for item in production_entry_corpus.document["scenarios"]
    }
    assert set(scenarios) == {spec["scenario_id"] for spec in SCENARIOS}
    assert scenarios["new-no-state"]["state_at_loop_boundary"] is not None
    assert scenarios["new-watch-task"]["new_watch_task"] is True
    assert scenarios["resume"]["classification"] == "resuming"
    assert scenarios["forced-replacement"]["driver_factory_calls"] == [
        {"replace_existing_run_id": "persisted-run"}
    ]
    assert scenarios["replacement-state-schema-error"]["driver_factory_calls"] == [
        {"replace_existing_run_id": None}
    ]
    for item in scenarios.values():
        if item["error"] is None:
            assert len(item["first_checkpoint_records"]) == 1
        else:
            assert item["first_checkpoint_records"] == []


def test_watch_record_authority_scenarios_bind_entry_outcomes(
    production_entry_corpus: BuiltEntryCorpus,
) -> None:
    scenarios = {
        item["scenario_id"]: item
        for item in production_entry_corpus.document["scenarios"]
    }
    expected = {
        "watch-records-without-state": ("resuming", False),
        "watch-records-without-state-force-new": ("new", True),
        "watch-records-with-state": ("resuming", False),
        "watch-without-records-or-state": ("new", True),
    }
    assert list(scenarios)[-4:] == list(expected)
    for scenario_id, (classification, branch_prepared) in expected.items():
        scenario = scenarios[scenario_id]
        assert scenario["classification"] == classification
        assert scenario["new_watch_task"] is branch_prepared
        assert scenario["branch_prepared"] is branch_prepared
        assert scenario["state_at_loop_boundary"] is not None
        assert scenario["state_at_loop_boundary"]["run_id"] == scenario_id
        assert scenario["driver_factory_calls"] == [
            {"replace_existing_run_id": None}
        ]
        assert scenario["first_checkpoint_records"] == [
            {
                "checkpoint_ordinal": 1,
                "state_run_id": scenario_id,
                "history_work_unit_id": 1,
            }
        ]


@pytest.mark.parametrize("term_name", DERIVED_TERM_NAMES)
def test_removing_a_derived_term_conjunct_turns_runtime_corpus_red(
    term_name: str, tmp_path: Path
) -> None:
    scenario_id = TERM_MUTATION_SCENARIOS[term_name]
    spec = next(spec for spec in SCENARIOS if spec["scenario_id"] == scenario_id)
    expected = next(
        scenario
        for scenario in _expected_runtime_corpus()["scenarios"]
        if scenario["scenario_id"] == scenario_id
    )
    control = _run_scenario(tmp_path / term_name / "control", spec)
    assert control == expected
    mutant = _compile_derived_term_mutant(term_name)
    try:
        actual = _run_scenario(
            tmp_path / term_name / "mutant", spec, production_module=mutant
        )
    finally:
        sys.modules.pop(mutant.__name__, None)
    assert actual != expected
    actual_projection = {
        "classification": actual["classification"],
        "new_watch_task": actual["new_watch_task"],
        "branch_prepared": actual.get("branch_prepared"),
        "has_loop_boundary": actual["state_at_loop_boundary"] is not None,
        "driver_factory_calls": actual["driver_factory_calls"],
        "has_first_checkpoint": bool(actual["first_checkpoint_records"]),
        "error_type": actual["error"]["type"] if actual["error"] else None,
    }
    assert actual_projection == TERM_MUTATION_EXPECTATIONS[term_name]


def test_swapping_two_entry_decisions_turns_static_anchor_red() -> None:
    mutant = copy.deepcopy(SOURCE_TREE)
    validation = _function(mutant, "_validate_resumed_state")
    positions = [
        index
        for index, statement in enumerate(validation.body)
        if isinstance(statement, ast.If)
    ]
    assert len(positions) == 6
    left, right = positions[2:4]
    validation.body[left], validation.body[right] = (
        validation.body[right],
        validation.body[left],
    )
    baseline = _load_json(STATIC_BASELINE)
    expected = [
        {key: value for key, value in item.items() if key != "pre_cut_line"}
        for item in baseline["entry_decisions"]
    ]
    assert _ordered_entry_decisions(mutant) != expected


def test_moving_state_load_out_of_its_catcher_turns_anchor_red() -> None:
    mutant = copy.deepcopy(SOURCE_TREE)
    entry = _function(mutant, "run_production_workflow")
    protected = next(
        node
        for node in ast.walk(entry)
        if isinstance(node, ast.Try)
        and any(
            isinstance(handler.type, ast.Name)
            and handler.type.id == "ActiveV2StateError"
            for handler in node.handlers
        )
    )
    parent_body = next(
        body
        for node in ast.walk(entry)
        for body in (getattr(node, "body", None), getattr(node, "orelse", None))
        if isinstance(body, list) and protected in body
    )
    position = parent_body.index(protected)
    parent_body.insert(position, protected.body.pop(0))
    protected.body.append(ast.Pass())
    baseline = _load_json(STATIC_BASELINE)
    expected = [
        {key: value for key, value in item.items() if key != "pre_cut_line"}
        for item in baseline["catchers"]
    ]
    assert _catcher_inventory(mutant) != expected


def test_entry_helpers_are_narrow_and_bounded() -> None:
    functions = {
        node.name: node
        for node in SOURCE_TREE.body
        if isinstance(node, ast.FunctionDef)
    }
    assert ENTRY_HELPERS.issubset(functions)
    assert "args" not in {
        node.id
        for node in ast.walk(functions["_validate_resumed_state"])
        if isinstance(node, ast.Name)
    }
    for helper in ENTRY_HELPERS:
        function = functions[helper]
        assert function.end_lineno is not None
        assert function.end_lineno - function.lineno + 1 < 200
