from __future__ import annotations

import ast
from collections import Counter
import copy
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import os
from argparse import Namespace
from pathlib import Path
import time
from types import CodeType
from typing import Callable

import pytest

from agent_runtime import AgentProcessError
from error_classification import classify_exception
import inbox_watcher
from inbox_watcher import (
    WatchTaskDisposition,
    WatchTaskIdentity,
    WatchTaskResult,
    attempt_sidecar_path,
    rejection_marker_path,
    watch_identity_path,
)
from task_contract import TaskContractError


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/inbox_watcher.py"
BASELINE = ROOT / "tests/fixtures/watcher-outcome-corpus-v1.json"
SOURCE_TEXT = SOURCE.read_text(encoding="utf-8")
SOURCE_TREE = ast.parse(SOURCE_TEXT, filename=str(SOURCE))


@dataclass(frozen=True, slots=True)
class CatcherSpec:
    catcher_id: str
    exception: str
    protected_call: str


@dataclass(frozen=True, slots=True)
class BuiltCorpus:
    document: dict[str, object]
    build_seconds: float


CATCHER_SPECS = (
    CatcherSpec("stuck-cleanup", "Exception", "task_file.rename"),
    CatcherSpec("rejection-marker-load", "ValueError", "load_rejection_marker"),
    CatcherSpec("watch-identity-load", "ValueError", "load_or_create_watch_identity"),
    CatcherSpec("task-processing", "Exception", "process_task"),
    CatcherSpec("rejection-report", "Exception", "write_rejected_failure_report"),
    CatcherSpec("rejection-archive", "Exception", "save_rejection_marker"),
    CatcherSpec("poison-report", "Exception", "write_poison_failure_report"),
    CatcherSpec("poison-archive", "Exception", "move_to_outbox"),
    CatcherSpec("legacy-success-marker", "Exception", "write_success_marker"),
    CatcherSpec("done-archive", "Exception", "delete_attempt_sidecar"),
    CatcherSpec("fallback-failed-archive", "Exception", "move_to_outbox"),
    CatcherSpec("watch-interrupt", "KeyboardInterrupt", "list_inbox_tasks"),
)

SCENARIO_IDS = (
    "stuck-cleanup-failure",
    "invalid-rejection-marker",
    "invalid-watch-identity",
    "task-processing-crash",
    "rejection-report-failure",
    "rejection-archive-failure",
    "poison-report-failure",
    "poison-archive-failure",
    "legacy-success-marker-failure",
    "done-archive-failure",
    "fallback-failed-archive-failure",
    "interrupt-then-restart",
    "clean-success",
)

_CORPUS_BUILD_COUNT = 0


class _StopScenario(BaseException):
    """End one successful/continuing watch run without touching a product catcher."""


def _watch_function(tree: ast.Module) -> ast.FunctionDef:
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "watch_inbox"
    )


def _handlers(function: ast.FunctionDef) -> tuple[ast.ExceptHandler, ...]:
    return tuple(
        sorted(
            (node for node in ast.walk(function) if isinstance(node, ast.ExceptHandler)),
            key=lambda node: node.lineno,
        )
    )


def _exception_name(handler: ast.ExceptHandler) -> str:
    assert isinstance(handler.type, ast.Name)
    return handler.type.id


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
        return f"{call.func.value.id}.{call.func.attr}"
    return None


def _protected_calls(protected_try: ast.Try) -> set[str]:
    return {
        name
        for statement in protected_try.body
        for node in ast.walk(statement)
        if isinstance(node, ast.Call)
        if (name := _call_name(node)) is not None
    }


def _body_sha256(handler: ast.ExceptHandler) -> str:
    semantic_body = ast.dump(
        ast.Module(body=handler.body, type_ignores=[]),
        annotate_fields=True,
        include_attributes=False,
    )
    return hashlib.sha256(semantic_body.encode("utf-8")).hexdigest()


def _handler_inventory(tree: ast.Module) -> list[dict[str, object]]:
    function = _watch_function(tree)
    handlers = _handlers(function)
    assert len(handlers) == len(CATCHER_SPECS)
    parent_map = {
        child: node
        for node in ast.walk(function)
        for child in ast.iter_child_nodes(node)
    }
    inventory: list[dict[str, object]] = []
    for spec, handler in zip(CATCHER_SPECS, handlers, strict=True):
        protected_try = parent_map[handler]
        assert isinstance(protected_try, ast.Try)
        assert _exception_name(handler) == spec.exception
        assert spec.protected_call in _protected_calls(protected_try)
        inventory.append(
            {
                "catcher_id": spec.catcher_id,
                "exception": spec.exception,
                "protected_call": spec.protected_call,
                "body_sha256": _body_sha256(handler),
            }
        )
    return inventory


@lru_cache(maxsize=2)
def _instrumented_code(
    swap: tuple[str, str] | None,
) -> tuple[CodeType, ast.Module]:
    tree = copy.deepcopy(SOURCE_TREE)
    function = _watch_function(tree)
    handlers = _handlers(function)
    by_id = dict(zip((spec.catcher_id for spec in CATCHER_SPECS), handlers, strict=True))
    if swap is not None:
        left, right = (by_id[catcher_id] for catcher_id in swap)
        left.body, right.body = copy.deepcopy(right.body), copy.deepcopy(left.body)
    inventory_tree = copy.deepcopy(tree)

    for spec, handler in zip(CATCHER_SPECS, handlers, strict=True):
        recorder = ast.Expr(
            ast.Call(
                func=ast.Name(id="_record_b44_catcher", ctx=ast.Load()),
                args=[ast.Constant(spec.catcher_id)],
                keywords=[],
            )
        )
        ast.copy_location(recorder, handler.body[0])
        handler.body.insert(0, recorder)

    module = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(module)
    return compile(module, "<b44-instrumented-watch>", "exec"), inventory_tree


def _instrumented_watch(
    *, swap: tuple[str, str] | None = None
) -> tuple[Callable[..., int], list[str], ast.Module]:
    code, inventory_tree = _instrumented_code(swap)
    catcher_hits: list[str] = []
    namespace = dict(vars(inbox_watcher))
    namespace["_record_b44_catcher"] = catcher_hits.append
    exec(code, namespace)
    instrumented = namespace["watch_inbox"]
    setattr(instrumented, "_b44_namespace", namespace)
    return instrumented, catcher_hits, copy.deepcopy(inventory_tree)


def _args() -> Namespace:
    return Namespace()


def _distinct_run_id_loader(run_id_prefix: str) -> Callable[[Path], WatchTaskIdentity]:
    generation = 0

    def load(task_file: Path) -> WatchTaskIdentity:
        def new_run_id(_task: Path) -> str:
            nonlocal generation
            generation += 1
            return f"{run_id_prefix}-generation-{generation}"

        return inbox_watcher.load_or_create_watch_identity(
            task_file, run_id_fn=new_run_id
        )

    return load


def _technical_result(args: Namespace) -> WatchTaskResult:
    return WatchTaskResult.from_failure(
        classify_exception(AgentProcessError("b44 provider failure")),
        run_id=args.watch_run_id,
        records_written=False,
    )


def _rejected_result(args: Namespace) -> WatchTaskResult:
    return WatchTaskResult.from_failure(
        classify_exception(TaskContractError("b44 rejected task")),
        run_id=args.watch_run_id,
        records_written=False,
    )


def _raise(error: BaseException) -> None:
    raise error


def _destination(task: Path, outbox: Path) -> str:
    done = tuple((outbox / "done").glob("*.md"))
    rejected = tuple((outbox / "failed").glob("*.rejected"))
    poison = tuple((outbox / "failed").glob("*.poison"))
    move_error = tuple((outbox / "failed").glob("*.move_error"))
    stuck = task.with_suffix(".md.stuck")
    destinations = [
        *(("outbox/done/*.md",) if done else ()),
        *(("outbox/failed/*.rejected",) if rejected else ()),
        *(("outbox/failed/*.poison",) if poison else ()),
        *(("outbox/failed/*.move_error",) if move_error else ()),
        *(("inbox/*.md.stuck",) if stuck.exists() else ()),
        *(("inbox/*.md",) if task.exists() else ()),
    ]
    assert len(destinations) == 1, destinations
    return destinations[0]


def _queue_disposition(
    *, task: Path, destination: str, watch_returns: list[int | str]
) -> str:
    if destination == "outbox/done/*.md":
        return WatchTaskDisposition.COMPLETED.value
    if destination == "inbox/*.md.stuck":
        return "stuck"
    if 4 in watch_returns:
        return WatchTaskDisposition.RESUMABLE_HALT.value
    if (
        destination == "outbox/failed/*.rejected"
        or rejection_marker_path(task).exists()
    ):
        return WatchTaskDisposition.REJECTED.value
    return WatchTaskDisposition.TECHNICAL_FAILURE.value


def _run_scenario(
    root: Path,
    scenario_id: str,
    *,
    swap: tuple[str, str] | None = None,
) -> dict[str, object]:  # noqa: C901, PLR0915
    scenario_root = root / scenario_id
    inbox = scenario_root / "inbox"
    outbox = scenario_root / "outbox"
    inbox.mkdir(parents=True)
    task = inbox / "task.md"
    task.write_text("b44 task", encoding="utf-8")

    watch, catcher_hits, _tree = _instrumented_watch(swap=swap)
    namespace = getattr(watch, "_b44_namespace")
    namespace["load_or_create_watch_identity"] = _distinct_run_id_loader(
        f"watch-b44-{scenario_id}"
    )
    provider_invocations: list[dict[str, object]] = []
    watch_returns: list[int | str] = []
    provider_calls = 0
    persisted_run_id_after_first: str | None = None
    max_retries = 1

    def remember(args: Namespace, force_new: bool) -> None:
        nonlocal provider_calls
        provider_calls += 1
        provider_invocations.append(
            {
                "run_id": args.watch_run_id,
                "resume": args.resume,
                "force_new": force_new,
            }
        )

    def succeed(_task: Path, args: Namespace, force_new: bool) -> int:
        remember(args, force_new)
        return 0

    process_task: Callable[[Path, Namespace, bool], int | WatchTaskResult] = succeed

    if scenario_id == "stuck-cleanup-failure":
        attempt_sidecar_path(task).write_text("3", encoding="utf-8")
        namespace["delete_attempt_sidecar"] = lambda _task: _raise(
            OSError("b44 stuck cleanup failure")
        )
    elif scenario_id == "invalid-rejection-marker":
        rejection_marker_path(task).write_text("{}\n", encoding="utf-8")
    elif scenario_id == "invalid-watch-identity":
        watch_identity_path(task).write_text("{}\n", encoding="utf-8")
    elif scenario_id == "task-processing-crash":
        def crash(_task: Path, args: Namespace, force_new: bool) -> int:
            remember(args, force_new)
            raise AgentProcessError("b44 provider crash")

        process_task = crash
    elif scenario_id == "rejection-report-failure":
        def reject(_task: Path, args: Namespace, force_new: bool) -> WatchTaskResult:
            remember(args, force_new)
            return _rejected_result(args)

        process_task = reject
        namespace["write_rejected_failure_report"] = lambda *_args, **_kwargs: _raise(
            OSError("b44 rejection report failure")
        )
    elif scenario_id == "rejection-archive-failure":
        def reject(_task: Path, args: Namespace, force_new: bool) -> WatchTaskResult:
            remember(args, force_new)
            return _rejected_result(args)

        process_task = reject
        namespace["move_to_outbox"] = lambda *_args, **_kwargs: _raise(
            OSError("b44 rejection archive failure")
        )
    elif scenario_id in {"poison-report-failure", "poison-archive-failure"}:
        def fail(_task: Path, args: Namespace, force_new: bool) -> WatchTaskResult:
            remember(args, force_new)
            return _technical_result(args)

        process_task = fail
        if scenario_id == "poison-report-failure":
            namespace["write_poison_failure_report"] = lambda *_args, **_kwargs: _raise(
                OSError("b44 poison report failure")
            )
        else:
            namespace["move_to_outbox"] = lambda *_args, **_kwargs: _raise(
                OSError("b44 poison archive failure")
            )
    elif scenario_id == "legacy-success-marker-failure":
        namespace["write_success_marker"] = lambda _task: _raise(
            OSError("b44 success marker failure")
        )
    elif scenario_id == "done-archive-failure":
        real_move = namespace["move_to_outbox"]

        def fail_done_move(
            source: Path, destination_dir: Path, *, source_name: str | None = None
        ) -> Path:
            if destination_dir.name == "done":
                raise OSError("b44 done archive failure")
            return real_move(source, destination_dir, source_name=source_name)

        namespace["move_to_outbox"] = fail_done_move
    elif scenario_id == "fallback-failed-archive-failure":
        namespace["move_to_outbox"] = lambda *_args, **_kwargs: _raise(
            OSError("b44 all archive moves fail")
        )
    elif scenario_id == "interrupt-then-restart":
        def interrupt_then_succeed(
            _task: Path, args: Namespace, force_new: bool
        ) -> int:
            remember(args, force_new)
            if provider_calls == 1:
                raise KeyboardInterrupt
            return 0

        process_task = interrupt_then_succeed
    elif scenario_id != "clean-success":
        raise AssertionError(f"missing B44 scenario {scenario_id}")

    invocation_count = 2 if scenario_id == "interrupt-then-restart" else 1
    original_cwd = Path.cwd()
    try:
        os.chdir(scenario_root)
        for invocation_index in range(invocation_count):
            list_calls = 0
            real_list = inbox_watcher.list_inbox_tasks

            def list_once(path: Path) -> list[Path]:
                nonlocal list_calls
                list_calls += 1
                if list_calls > 1:
                    raise _StopScenario
                return real_list(path)

            namespace["list_inbox_tasks"] = list_once
            try:
                result = watch(
                    inbox_dir=inbox,
                    outbox_dir=outbox,
                    poll_interval=0,
                    args=_args(),
                    process_task=process_task,
                    min_file_age_seconds=0,
                    max_retries=max_retries,
                    sleep_fn=lambda _seconds: None,
                    time_fn=lambda: 10_000_000_000.0,
                )
            except _StopScenario:
                watch_returns.append("harness-stop")
            else:
                watch_returns.append(result)
            if scenario_id == "interrupt-then-restart" and invocation_index == 0:
                persisted = WatchTaskIdentity.from_dict(
                    json.loads(watch_identity_path(task).read_text(encoding="utf-8"))
                )
                persisted_run_id_after_first = persisted.run_id
    finally:
        os.chdir(original_cwd)

    unique_run_ids = list(
        dict.fromkeys(str(item["run_id"]) for item in provider_invocations)
    )
    continuity = (
        "same-across-restart"
        if invocation_count == 2
        and len(provider_invocations) == 2
        and len(unique_run_ids) == 1
        and provider_invocations[0]["run_id"] == persisted_run_id_after_first
        and provider_invocations[1]["run_id"] == persisted_run_id_after_first
        else "changed-across-restart"
        if invocation_count == 2
        else "single-run"
        if unique_run_ids
        else "not-established-invalid-identity"
        if scenario_id == "invalid-watch-identity"
        else "not-observed-pre-provider"
    )
    destination = _destination(task, outbox)
    disposition = _queue_disposition(
        task=task, destination=destination, watch_returns=watch_returns
    )
    assert not (scenario_root / ".orchestrator").exists()
    return {
        "scenario_id": scenario_id,
        "disposition": disposition,
        "destination": destination,
        "run_ids": unique_run_ids,
        "run_id_continuity": continuity,
        "persisted_run_id_after_first": persisted_run_id_after_first,
        "provider_calls": provider_calls,
        "provider_invocations": provider_invocations,
        "catchers": catcher_hits,
        "catcher_hit_counts": dict(sorted(Counter(catcher_hits).items())),
        "watch_returns": watch_returns,
    }


def _build_corpus(root: Path) -> BuiltCorpus:
    global _CORPUS_BUILD_COUNT
    _CORPUS_BUILD_COUNT += 1
    started = time.perf_counter()
    tree = copy.deepcopy(SOURCE_TREE)
    document: dict[str, object] = {
        "schema_version": "watcher-outcome-corpus-v1",
        "handlers": _handler_inventory(tree),
        "scenarios": [_run_scenario(root, scenario_id) for scenario_id in SCENARIO_IDS],
    }
    return BuiltCorpus(document, time.perf_counter() - started)


def _load_baseline() -> dict[str, object]:
    document = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    assert set(document) == {"schema_version", "handlers", "scenarios"}
    assert document["schema_version"] == "watcher-outcome-corpus-v1"
    return document


@pytest.fixture(scope="session")
def watcher_outcome_corpus(tmp_path_factory: pytest.TempPathFactory) -> BuiltCorpus:
    return _build_corpus(tmp_path_factory.mktemp("b44-watcher-outcome-corpus"))


def test_watcher_outcome_corpus_matches_pre_cut_baseline(
    watcher_outcome_corpus: BuiltCorpus,
) -> None:
    assert watcher_outcome_corpus.document == _load_baseline()


def test_all_twelve_catchers_are_runtime_bound_exactly_once_or_explicitly_shared(
    watcher_outcome_corpus: BuiltCorpus,
) -> None:
    scenarios = watcher_outcome_corpus.document["scenarios"]
    assert isinstance(scenarios, list)
    reached: Counter[str] = Counter()
    for scenario in scenarios:
        reached.update(scenario["catcher_hit_counts"])
    expected_hits = {spec.catcher_id: 1 for spec in CATCHER_SPECS}
    expected_hits["done-archive"] = 3
    assert reached == expected_hits
    assert len(CATCHER_SPECS) == 12
    assert Counter(spec.exception for spec in CATCHER_SPECS) == {
        "Exception": 9,
        "ValueError": 2,
        "KeyboardInterrupt": 1,
    }


def test_equal_failed_destination_catcher_body_swap_turns_corpus_red(
    tmp_path: Path,
) -> None:
    swap = ("rejection-report", "poison-report")
    mutant = _run_scenario(
        tmp_path, "rejection-report-failure", swap=swap
    )
    expected = next(
        scenario
        for scenario in _load_baseline()["scenarios"]
        if scenario["scenario_id"] == "rejection-report-failure"
    )
    assert mutant["destination"] == expected["destination"]
    assert mutant["disposition"] == expected["disposition"]
    assert mutant["provider_calls"] == expected["provider_calls"]
    assert mutant["catchers"] == ["rejection-report", "rejection-archive"]
    assert mutant["watch_returns"] == [1]
    assert mutant != expected

    _watch, _hits, mutant_tree = _instrumented_watch(swap=swap)
    assert _handler_inventory(mutant_tree) != _load_baseline()["handlers"]


def test_restart_keeps_run_id_and_clean_success_touches_no_catcher(
    watcher_outcome_corpus: BuiltCorpus,
) -> None:
    scenarios = {
        scenario["scenario_id"]: scenario
        for scenario in watcher_outcome_corpus.document["scenarios"]
    }
    restarted = scenarios["interrupt-then-restart"]
    assert restarted["run_id_continuity"] == "same-across-restart"
    assert len(restarted["run_ids"]) == 1
    assert restarted["run_ids"] == [restarted["persisted_run_id_after_first"]]
    assert restarted["provider_calls"] == 2
    assert restarted["provider_invocations"] == [
        {
            "run_id": restarted["persisted_run_id_after_first"],
            "resume": False,
            "force_new": True,
        },
        {
            "run_id": restarted["persisted_run_id_after_first"],
            "resume": True,
            "force_new": False,
        },
    ]
    assert restarted["destination"] == "outbox/done/*.md"

    success = scenarios["clean-success"]
    assert success["disposition"] == WatchTaskDisposition.COMPLETED.value
    assert success["destination"] == "outbox/done/*.md"
    assert success["provider_calls"] == 1
    assert success["catchers"] == []


def test_instrumented_clean_success_matches_production_function(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    watcher_outcome_corpus: BuiltCorpus,
) -> None:
    inbox = tmp_path / "real-inbox"
    outbox = tmp_path / "real-outbox"
    inbox.mkdir()
    task = inbox / "task.md"
    task.write_text("b44 real watcher parity", encoding="utf-8")
    provider_calls = 0
    list_calls = 0
    real_list = inbox_watcher.list_inbox_tasks

    def process(_task: Path, _args: Namespace, _force_new: bool) -> int:
        nonlocal provider_calls
        provider_calls += 1
        return 0

    def list_once(path: Path) -> list[Path]:
        nonlocal list_calls
        list_calls += 1
        if list_calls > 1:
            raise _StopScenario
        return real_list(path)

    monkeypatch.setattr(inbox_watcher, "list_inbox_tasks", list_once)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(_StopScenario):
        inbox_watcher.watch_inbox(
            inbox_dir=inbox,
            outbox_dir=outbox,
            poll_interval=0,
            args=_args(),
            process_task=process,
            min_file_age_seconds=0,
            max_retries=1,
            sleep_fn=lambda _seconds: None,
            time_fn=lambda: 10_000_000_000.0,
        )

    expected = next(
        scenario
        for scenario in watcher_outcome_corpus.document["scenarios"]
        if scenario["scenario_id"] == "clean-success"
    )
    assert _destination(task, outbox) == expected["destination"]
    assert provider_calls == expected["provider_calls"]
    assert not (tmp_path / ".orchestrator").exists()


def test_scenarios_are_built_once(
    watcher_outcome_corpus: BuiltCorpus,
) -> None:
    assert _CORPUS_BUILD_COUNT == 1
    assert watcher_outcome_corpus.build_seconds > 0
