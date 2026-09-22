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
import subprocess
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
PRE_CUT_MAP = ROOT / "tests/fixtures/watcher-catcher-map-pre-b45-v1.json"
HELPER_BINDINGS = ROOT / "tests/fixtures/watcher-catcher-helper-bindings-b45-v1.json"
B57_PRE_CUT = ROOT / "tests/fixtures/watcher-pre-b57-v1.json"
B57_HELPER_BINDINGS = (
    ROOT / "tests/fixtures/watcher-helper-bindings-b57-v1.json"
)
B61_PRE_CUT = ROOT / "tests/fixtures/watcher-pre-b61-v1.json"
B61_HELPER_BINDINGS = (
    ROOT / "tests/fixtures/watcher-helper-bindings-b61-v1.json"
)
FUNCTION_SIZE_BASELINE = ROOT / "tests/fixtures/function-size-baseline-v1.json"
RECORD_SEQUENCE_BASELINE = (
    ROOT / "tests/fixtures/workflow-record-sequence-baseline-v1.json"
)
PRE_B57_COMMIT = "8c633cc2ed006e474ff4aea3229950206d1fe066"
PRE_B57_BLOB = "99965b3e92fea8a681447359f6aa9e54a813162f"
PRE_B61_COMMIT = "496a4854c40081a81ef2a8904ac4985fd4585d1d"
PRE_B61_BLOB = "30581cdc366f48579f1f3f8c8f94ce69575948d0"
HISTORICAL_RECORD_SEQUENCE_BLOB = "26fb661c8fa382f90e70fb921e3d950da5cae09b"
RECORD_SEQUENCE_BLOB = "5f17a97e1d0fc5f56b21a22588ed6c3dc2d3a9a3"
SOURCE_TEXT = SOURCE.read_text(encoding="utf-8")
SOURCE_TREE = ast.parse(SOURCE_TEXT, filename=str(SOURCE))


@dataclass(frozen=True, slots=True)
class CatcherSpec:
    catcher_id: str
    exception: str
    protected_call: str
    helper_call: str | None = None


@dataclass(frozen=True, slots=True)
class BuiltCorpus:
    document: dict[str, object]
    build_seconds: float


CATCHER_SPECS = (
    CatcherSpec(
        "stuck-cleanup", "Exception", "task_file.rename", "_rename_stuck_task"
    ),
    CatcherSpec("rejection-marker-load", "ValueError", "load_rejection_marker"),
    CatcherSpec("watch-identity-load", "ValueError", "load_or_create_watch_identity"),
    CatcherSpec("task-processing", "Exception", "process_task", "_process_watch_task"),
    CatcherSpec("rejection-report", "Exception", "write_rejected_failure_report"),
    CatcherSpec(
        "rejection-archive",
        "Exception",
        "save_rejection_marker",
        "_begin_rejected_archive",
    ),
    CatcherSpec("poison-report", "Exception", "write_poison_failure_report"),
    CatcherSpec("poison-archive", "Exception", "move_to_outbox", "_move_poison_task"),
    CatcherSpec("legacy-success-marker", "Exception", "write_success_marker"),
    CatcherSpec(
        "done-archive",
        "Exception",
        "delete_attempt_sidecar",
        "_archive_completed_task",
    ),
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

B45_HELPERS = frozenset(
    binding["helper"]
    for binding in json.loads(HELPER_BINDINGS.read_text(encoding="utf-8"))["helpers"]
)
B57_HELPERS = tuple(
    binding["helper"]
    for binding in json.loads(B57_HELPER_BINDINGS.read_text(encoding="utf-8"))[
        "helpers"
    ]
)
B61_BINDINGS_DOCUMENT = json.loads(B61_HELPER_BINDINGS.read_text(encoding="utf-8"))
B61_HELPERS = tuple(
    binding["helper"] for binding in B61_BINDINGS_DOCUMENT["helpers"]
)
B61_OWNED_CATCHERS = {
    binding["helper"]: tuple(binding["owned_catchers"])
    for binding in B61_BINDINGS_DOCUMENT["helpers"]
}
EXTRACTED_HELPERS = B45_HELPERS | frozenset(B57_HELPERS)


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


def _function_calls(function: ast.FunctionDef) -> set[str]:
    return {
        name
        for node in ast.walk(function)
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


def _direct_b57_helper_call(
    statement: ast.stmt,
) -> tuple[str, ast.Call] | None:
    value: ast.expr | None = None
    if isinstance(statement, ast.Expr):
        value = statement.value
    elif isinstance(statement, ast.Assign):
        value = statement.value
    if (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id in B57_HELPERS
    ):
        return value.func.id, value
    return None


def _direct_b61_helper_call(
    statement: ast.stmt,
) -> tuple[str, ast.Call] | None:
    if (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Call)
        and isinstance(statement.value.func, ast.Name)
        and statement.value.func.id in B61_HELPERS
    ):
        return statement.value.func.id, statement.value
    return None


def _logical_pre_b61_watch_function(tree: ast.Module) -> ast.FunctionDef:
    function = copy.deepcopy(_watch_function(tree))
    helpers = {
        node.name: copy.deepcopy(node)
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in B61_HELPERS
    }
    assert set(helpers) == set(B61_HELPERS)
    observed_calls = [
        node.func.id
        for node in sorted(
            (item for item in ast.walk(function) if isinstance(item, ast.Call)),
            key=lambda item: (item.lineno, item.col_offset),
        )
        if isinstance(node.func, ast.Name) and node.func.id in B61_HELPERS
    ]
    if not observed_calls:
        return function
    assert observed_calls == list(B61_HELPERS)

    def expand_body(body: list[ast.stmt]) -> list[ast.stmt]:
        expanded: list[ast.stmt] = []
        for statement in body:
            direct = _direct_b61_helper_call(statement)
            if direct is not None:
                helper_name, call = direct
                helper = helpers[helper_name]
                parameters = [argument.arg for argument in helper.args.args]
                assert not call.keywords
                assert len(call.args) == len(parameters)
                assert not any(
                    isinstance(item, ast.Return) for item in ast.walk(helper)
                )
                arguments = dict(zip(parameters, call.args, strict=True))

                class SubstituteArguments(ast.NodeTransformer):
                    def visit_Name(self, node: ast.Name) -> ast.expr:
                        if isinstance(node.ctx, ast.Load) and node.id in arguments:
                            return ast.copy_location(
                                copy.deepcopy(arguments[node.id]), node
                            )
                        return node

                substituted = [
                    SubstituteArguments().visit(copy.deepcopy(item))
                    for item in helper.body
                ]
                assert all(isinstance(item, ast.stmt) for item in substituted)
                expanded.extend(expand_body(substituted))
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


def _logical_watch_function(tree: ast.Module) -> ast.FunctionDef:
    function = _logical_pre_b61_watch_function(tree)
    helpers = {
        node.name: copy.deepcopy(node)
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in B57_HELPERS
    }
    assert set(helpers) == set(B57_HELPERS)
    observed_calls = [
        node.func.id
        for node in sorted(
            (item for item in ast.walk(function) if isinstance(item, ast.Call)),
            key=lambda item: (item.lineno, item.col_offset),
        )
        if isinstance(node.func, ast.Name) and node.func.id in B57_HELPERS
    ]
    if not observed_calls:
        return function
    assert observed_calls == list(B57_HELPERS)

    def expand_body(body: list[ast.stmt]) -> list[ast.stmt]:
        expanded: list[ast.stmt] = []
        for statement in body:
            direct = _direct_b57_helper_call(statement)
            if direct is not None:
                helper_name, call = direct
                helper = helpers[helper_name]
                parameters = [argument.arg for argument in helper.args.args]
                assert not call.keywords
                assert [ast.unparse(argument) for argument in call.args] == parameters
                helper_body = copy.deepcopy(helper.body)
                if isinstance(statement, ast.Assign):
                    terminal = helper_body[-1]
                    assert isinstance(terminal, ast.Return)
                    assert terminal.value is not None
                    if (
                        len(statement.targets) == 1
                        and ast.unparse(statement.targets[0])
                        == ast.unparse(terminal.value)
                    ):
                        helper_body.pop()
                    else:
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


@lru_cache(maxsize=1)
def _pre_b57_watch_function() -> ast.FunctionDef:
    source = subprocess.run(
        ["git", "show", f"{PRE_B57_COMMIT}:src/inbox_watcher.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return _watch_function(ast.parse(source))


@lru_cache(maxsize=1)
def _pre_b61_watch_function() -> ast.FunctionDef:
    source = subprocess.run(
        ["git", "show", f"{PRE_B61_COMMIT}:src/inbox_watcher.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return _watch_function(ast.parse(source))


def _handler_inventory(tree: ast.Module) -> list[dict[str, object]]:
    function = _logical_watch_function(tree)
    handlers = _handlers(function)
    assert len(handlers) == len(CATCHER_SPECS)
    parent_map = {
        child: node
        for node in ast.walk(function)
        for child in ast.iter_child_nodes(node)
    }
    module_functions = {
        node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    inventory: list[dict[str, object]] = []
    for spec, handler in zip(CATCHER_SPECS, handlers, strict=True):
        protected_try = parent_map[handler]
        assert isinstance(protected_try, ast.Try)
        assert _exception_name(handler) == spec.exception
        protected_calls = _protected_calls(protected_try)
        if spec.helper_call is not None:
            assert spec.helper_call in protected_calls
            protected_calls.update(_function_calls(module_functions[spec.helper_call]))
        assert spec.protected_call in protected_calls
        inventory.append(
            {
                "catcher_id": spec.catcher_id,
                "exception": spec.exception,
                "protected_call": spec.protected_call,
                "body_sha256": _body_sha256(handler),
            }
        )
    return inventory


def _physical_handlers_by_id(tree: ast.Module) -> dict[str, ast.ExceptHandler]:
    handlers: dict[str, ast.ExceptHandler] = {}
    functions = {
        node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    moved_catchers: set[str] = set()
    for helper_name, catcher_ids in B61_OWNED_CATCHERS.items():
        owned = _handlers(functions[helper_name])
        assert len(owned) == len(catcher_ids), helper_name
        handlers.update(dict(zip(catcher_ids, owned, strict=True)))
        moved_catchers.update(catcher_ids)

    remaining_specs = [
        spec for spec in CATCHER_SPECS if spec.catcher_id not in moved_catchers
    ]
    remaining = _handlers(_watch_function(tree))
    assert len(remaining) == len(remaining_specs)
    handlers.update(
        {
            spec.catcher_id: handler
            for spec, handler in zip(remaining_specs, remaining, strict=True)
        }
    )
    assert set(handlers) == {spec.catcher_id for spec in CATCHER_SPECS}
    return handlers


def _helper_catcher_binding(tree: ast.Module, helper_name: str) -> tuple[str, str]:
    if helper_name in B61_HELPERS:
        function = _watch_function(tree)
        catcher_by_handler = {
            handler: catcher_id
            for catcher_id, handler in _physical_handlers_by_id(tree).items()
            if handler in set(_handlers(function))
        }
    else:
        function = _logical_pre_b61_watch_function(tree)
        handlers = _handlers(function)
        catcher_by_handler = dict(
            zip(handlers, (spec.catcher_id for spec in CATCHER_SPECS), strict=True)
        )
    parent_map = {
        child: node
        for node in ast.walk(function)
        for child in ast.iter_child_nodes(node)
    }
    calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and _call_name(node) == helper_name
    ]
    assert len(calls) == 1, helper_name
    current: ast.AST = calls[0]
    while current in parent_map:
        parent = parent_map[current]
        if isinstance(parent, ast.ExceptHandler):
            return catcher_by_handler[parent], "handler_body"
        if isinstance(parent, ast.Try) and current in parent.body:
            assert len(parent.handlers) == 1
            return catcher_by_handler[parent.handlers[0]], "protected_body"
        current = parent
    raise AssertionError(f"helper call has no catcher binding: {helper_name}")


def _loop_control_signature(tree: ast.Module) -> tuple[int, str, str]:
    function = _watch_function(tree)
    loops = [node for node in ast.walk(function) if isinstance(node, ast.While)]
    assert len(loops) == 1
    loop = loops[0]
    controls = [
        type(node).__name__
        + ":"
        + (
            ast.dump(node.value, include_attributes=False)
            if isinstance(node, ast.Return) and node.value is not None
            else ""
        )
        for node in sorted(
            (
                item
                for item in ast.walk(loop)
                if isinstance(item, (ast.Continue, ast.Break, ast.Return))
            ),
            key=lambda item: item.lineno,
        )
    ]
    digest = hashlib.sha256("\n".join(controls).encode("utf-8")).hexdigest()
    return len(controls), digest, ast.dump(loop.test, include_attributes=False)


def _all_control_sequence(function: ast.FunctionDef) -> tuple[str, ...]:
    return tuple(
        type(node).__name__
        + ":"
        + (
            ast.dump(node.value, include_attributes=False)
            if isinstance(node, ast.Return) and node.value is not None
            else ""
        )
        for node in sorted(
            (
                item
                for item in ast.walk(function)
                if isinstance(item, (ast.Continue, ast.Return))
            ),
            key=lambda item: item.lineno,
        )
    )


def _assert_logical_watch_matches_pre_b57(
    tree: ast.Module, *, failure_label: str = "B57 logical watch drift"
) -> None:
    actual = ast.dump(_logical_watch_function(tree), include_attributes=False)
    expected = ast.dump(_pre_b57_watch_function(), include_attributes=False)
    if actual != expected:
        raise AssertionError(failure_label)


def _move_helper_call_before_catcher(
    tree: ast.Module, helper_name: str
) -> ast.Module:
    if helper_name not in B61_HELPERS:
        logical = _logical_pre_b61_watch_function(tree)
        index = tree.body.index(_watch_function(tree))
        tree.body[index] = logical
    function = _watch_function(tree)
    parent_map = {
        child: node
        for node in ast.walk(function)
        for child in ast.iter_child_nodes(node)
    }
    call = next(
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and _call_name(node) == helper_name
    )
    statement: ast.AST = call
    while not (
        isinstance(parent_map.get(statement), ast.Try)
        and statement in parent_map[statement].body
    ):
        statement = parent_map[statement]
    protected_try = parent_map[statement]
    assert isinstance(protected_try, ast.Try)
    container = parent_map[protected_try]
    statement_list = next(
        candidate
        for _field, candidate in ast.iter_fields(container)
        if isinstance(candidate, list) and protected_try in candidate
    )
    protected_try.body.remove(statement)
    if not protected_try.body:
        protected_try.body.append(ast.Pass())
    statement_list.insert(statement_list.index(protected_try), statement)
    ast.fix_missing_locations(tree)
    return tree


def _move_product_call_between_helpers(
    tree: ast.Module,
    call_name: str,
    *,
    source_helper: str,
    destination_helper: str,
) -> ast.Module:
    functions = {
        node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    source = functions[source_helper]
    destination = functions[destination_helper]
    parent_map = {
        child: node
        for node in ast.walk(source)
        for child in ast.iter_child_nodes(node)
    }
    call = next(
        node
        for node in ast.walk(source)
        if isinstance(node, ast.Call) and _call_name(node) == call_name
    )
    statement: ast.AST = call
    while parent_map[statement] is not source:
        statement = parent_map[statement]
    source.body.remove(statement)
    destination.body.insert(-1, statement)
    ast.fix_missing_locations(tree)
    return tree


@lru_cache(maxsize=2)
def _instrumented_code(
    swap: tuple[str, str] | None,
) -> tuple[CodeType, ast.Module]:
    tree = copy.deepcopy(SOURCE_TREE)
    function = _logical_watch_function(tree)
    handlers = _handlers(function)
    by_id = dict(zip((spec.catcher_id for spec in CATCHER_SPECS), handlers, strict=True))
    if swap is not None:
        left, right = (by_id[catcher_id] for catcher_id in swap)
        left.body, right.body = copy.deepcopy(right.body), copy.deepcopy(left.body)
    tree.body[tree.body.index(_watch_function(tree))] = copy.deepcopy(function)
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

    helper_functions = [
        copy.deepcopy(node)
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in B45_HELPERS
    ]
    assert {helper.name for helper in helper_functions} == B45_HELPERS
    module = ast.Module(body=[*helper_functions, function], type_ignores=[])
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


@pytest.mark.parametrize(
    ("protocol_mode", "expected_destination", "expected_attempts", "logs_failure"),
    (
        ("structured-v2", "inbox/*.md", 1, True),
        (None, "outbox/failed/*.poison", 0, False),
    ),
)
def test_real_watcher_preserves_future_unbound_identity_poison_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    protocol_mode: str | None,
    expected_destination: str,
    expected_attempts: int,
    logs_failure: bool,
) -> None:
    inbox = tmp_path / "future-technical-rejection" / "inbox"
    outbox = tmp_path / "future-technical-rejection" / "outbox"
    inbox.mkdir(parents=True)
    task = inbox / "task.md"
    task.write_text("future technical rejection", encoding="utf-8")
    rejection_marker_path(task).write_text("{}\n", encoding="utf-8")
    result = WatchTaskResult.from_failure(
        classify_exception(AgentProcessError("future transient marker")),
        run_id="watch-b61-future-transient-marker",
        records_written=False,
        protocol_mode=protocol_mode,
    )
    list_calls = 0
    real_list = inbox_watcher.list_inbox_tasks

    def list_once(path: Path) -> list[Path]:
        nonlocal list_calls
        list_calls += 1
        if list_calls > 1:
            raise _StopScenario
        return real_list(path)

    monkeypatch.setattr(inbox_watcher, "list_inbox_tasks", list_once)
    monkeypatch.setattr(inbox_watcher, "load_rejection_marker", lambda _task: result)
    with pytest.raises(_StopScenario):
        inbox_watcher.watch_inbox(
            inbox_dir=inbox,
            outbox_dir=outbox,
            poll_interval=0,
            args=_args(),
            process_task=lambda *_args: pytest.fail("provider must not run"),
            min_file_age_seconds=0,
            max_retries=1,
            sleep_fn=lambda _seconds: None,
            time_fn=lambda: 10_000_000_000.0,
        )

    assert _destination(task, outbox) == expected_destination
    assert inbox_watcher.read_attempt_count(task) == expected_attempts
    assert ("Failed to move poison task" in caplog.text) is logs_failure
    assert ("UnboundLocalError" in caplog.text) is logs_failure


def test_scenarios_are_built_once(
    watcher_outcome_corpus: BuiltCorpus,
) -> None:
    assert _CORPUS_BUILD_COUNT == 1
    assert watcher_outcome_corpus.build_seconds > 0


def test_b45_helpers_remain_inside_their_pre_cut_catchers() -> None:
    pre_cut = json.loads(PRE_CUT_MAP.read_text(encoding="utf-8"))
    bindings = json.loads(HELPER_BINDINGS.read_text(encoding="utf-8"))
    assert pre_cut["schema_version"] == "watcher-catcher-map-pre-b45-v1"
    assert pre_cut["source_commit"] == "16033429628a164c95c808830f74c3139faaca52"
    assert pre_cut["source_blob"] == "34e6c3a384e1c2dfaabe521b2261496a7800df6b"
    assert bindings["schema_version"] == "watcher-catcher-helper-bindings-b45-v1"
    assert bindings["pre_cut_map"] == PRE_CUT_MAP.relative_to(ROOT).as_posix()

    pre_cut_catchers = {
        item["catcher_id"]: item for item in pre_cut["catchers"]
    }
    assert list(pre_cut_catchers) == [spec.catcher_id for spec in CATCHER_SPECS]
    assert [item["exception"] for item in pre_cut_catchers.values()] == [
        spec.exception for spec in CATCHER_SPECS
    ]

    actual_bindings = {
        item["helper"]: _helper_catcher_binding(SOURCE_TREE, item["helper"])
        for item in bindings["helpers"]
    }
    expected_bindings = {
        item["helper"]: (item["catcher_id"], item["region"])
        for item in bindings["helpers"]
    }
    assert actual_bindings == expected_bindings
    assert set(actual_bindings) == B45_HELPERS
    assert all(item["pre_cut_block"] for item in bindings["helpers"])

    helper_nodes = {
        node.name: node
        for node in SOURCE_TREE.body
        if isinstance(node, ast.FunctionDef) and node.name in B45_HELPERS
    }
    assert set(helper_nodes) == B45_HELPERS
    assert not any(
        isinstance(descendant, ast.ExceptHandler)
        for helper in helper_nodes.values()
        for descendant in ast.walk(helper)
    )
    module_private_functions = {
        node.name
        for node in SOURCE_TREE.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("_")
    }
    called_private_functions = {
        name
        for node in ast.walk(_logical_pre_b61_watch_function(SOURCE_TREE))
        if isinstance(node, ast.Call)
        if (name := _call_name(node)) in module_private_functions
    }
    assert called_private_functions == EXTRACTED_HELPERS


def test_b45_catcher_bodies_and_loop_control_match_the_pre_cut_map() -> None:
    pre_cut = json.loads(PRE_CUT_MAP.read_text(encoding="utf-8"))
    bindings = json.loads(HELPER_BINDINGS.read_text(encoding="utf-8"))
    current_handlers = {
        item["catcher_id"]: item for item in _handler_inventory(SOURCE_TREE)
    }
    pre_cut_sha = {
        item["catcher_id"]: item["handler_body_sha256"]
        for item in pre_cut["catchers"]
    }
    changed = {
        catcher_id
        for catcher_id, item in current_handlers.items()
        if item["body_sha256"] != pre_cut_sha[catcher_id]
    }
    assert changed == set(bindings["handler_body_sha_change_reasons"])
    assert all(bindings["handler_body_sha_change_reasons"].values())

    count, digest, test_ast = _loop_control_signature(SOURCE_TREE)
    assert count == pre_cut["loop_control_count"]
    assert digest == pre_cut["loop_control_sha256"]
    assert test_ast == pre_cut["while_test_ast"]


def test_b45_moving_a_helper_call_outside_its_catcher_turns_binding_red() -> None:
    bindings = json.loads(HELPER_BINDINGS.read_text(encoding="utf-8"))
    expected = {
        item["helper"]: (item["catcher_id"], item["region"])
        for item in bindings["helpers"]
    }
    mutant = _move_helper_call_before_catcher(
        copy.deepcopy(SOURCE_TREE), "_move_poison_task"
    )
    compile(mutant, "<b45-helper-outside-catcher>", "exec")
    actual = {
        helper: _helper_catcher_binding(mutant, helper) for helper in expected
    }
    assert actual["_move_poison_task"] == ("watch-interrupt", "protected_body")
    assert actual != expected


def test_b45_moving_a_product_call_between_helpers_turns_inventory_red() -> None:
    mutant = _move_product_call_between_helpers(
        copy.deepcopy(SOURCE_TREE),
        "process_task",
        source_helper="_process_watch_task",
        destination_helper="_prepare_watch_invocation",
    )
    compile(mutant, "<b45-product-call-in-wrong-helper>", "exec")
    assert _helper_catcher_binding(mutant, "_process_watch_task") == (
        "task-processing",
        "protected_body",
    )
    with pytest.raises(AssertionError):
        _handler_inventory(mutant)


def test_b57_pre_cut_anchor_binds_the_immediate_b56_source() -> None:
    anchor = json.loads(B57_PRE_CUT.read_text(encoding="utf-8"))
    assert anchor == {
        "schema_version": "watcher-pre-b57-v1",
        "source_commit": PRE_B57_COMMIT,
        "source_blob": PRE_B57_BLOB,
    }
    anchored_blob = subprocess.run(
        ["git", "rev-parse", f"{PRE_B57_COMMIT}:src/inbox_watcher.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert anchored_blob == PRE_B57_BLOB


def test_b57_helpers_inline_to_the_exact_pre_cut_watch_function() -> None:
    _assert_logical_watch_matches_pre_b57(SOURCE_TREE)


def test_b57_helpers_remain_inside_their_pre_cut_catchers() -> None:
    document = json.loads(B57_HELPER_BINDINGS.read_text(encoding="utf-8"))
    assert document["schema_version"] == "watcher-helper-bindings-b57-v1"
    assert document["pre_cut"] == B57_PRE_CUT.relative_to(ROOT).as_posix()
    expected = {
        item["helper"]: (item["catcher_id"], item["region"])
        for item in document["helpers"]
    }
    actual = {
        helper: _helper_catcher_binding(SOURCE_TREE, helper) for helper in expected
    }
    assert actual == expected
    assert tuple(expected) == B57_HELPERS

    helper_nodes = {
        node.name: node
        for node in SOURCE_TREE.body
        if isinstance(node, ast.FunctionDef) and node.name in B57_HELPERS
    }
    assert set(helper_nodes) == set(B57_HELPERS)
    assert not any(
        isinstance(descendant, ast.ExceptHandler)
        for helper in helper_nodes.values()
        for descendant in ast.walk(helper)
    )


def test_b57_documents_each_physically_changed_handler_body() -> None:
    document = json.loads(B57_HELPER_BINDINGS.read_text(encoding="utf-8"))
    pre_handlers = dict(
        zip(CATCHER_SPECS, _handlers(_pre_b57_watch_function()), strict=True)
    )
    current_handlers = dict(
        zip(
            CATCHER_SPECS,
            _handlers(_logical_pre_b61_watch_function(SOURCE_TREE)),
            strict=True,
        )
    )
    changed = {
        spec.catcher_id
        for spec in CATCHER_SPECS
        if _body_sha256(current_handlers[spec]) != _body_sha256(pre_handlers[spec])
    }
    assert changed == set(document["handler_body_sha_change_reasons"])
    assert all(document["handler_body_sha_change_reasons"].values())


def test_b57_keeps_while_and_all_returns_and_continues_in_source_order() -> None:
    current = _watch_function(SOURCE_TREE)
    pre_cut = _pre_b57_watch_function()
    current_while = next(node for node in ast.walk(current) if isinstance(node, ast.While))
    pre_cut_while = next(node for node in ast.walk(pre_cut) if isinstance(node, ast.While))
    assert ast.dump(current_while.test, include_attributes=False) == ast.dump(
        pre_cut_while.test, include_attributes=False
    )
    assert _all_control_sequence(current) == _all_control_sequence(pre_cut)
    assert sum(isinstance(node, ast.Return) for node in ast.walk(current)) == 10
    assert sum(isinstance(node, ast.Continue) for node in ast.walk(current)) == 7


def test_b57_moving_a_helper_out_of_its_catcher_names_the_helper() -> None:
    helper_name = "_archive_failed_task"
    mutant = _move_helper_call_before_catcher(copy.deepcopy(SOURCE_TREE), helper_name)
    compile(mutant, "<b57-helper-outside-catcher>", "exec")
    with pytest.raises(AssertionError, match=helper_name):
        expected = json.loads(
            B57_HELPER_BINDINGS.read_text(encoding="utf-8")
        )["helpers"]
        for item in expected:
            actual = _helper_catcher_binding(mutant, item["helper"])
            if actual != (item["catcher_id"], item["region"]):
                raise AssertionError(item["helper"])


def test_b57_swapping_two_queue_decisions_names_the_ordering() -> None:
    tree = copy.deepcopy(SOURCE_TREE)
    function = _watch_function(tree)
    loop = next(node for node in ast.walk(function) if isinstance(node, ast.While))
    rejected = next(
        node
        for node in loop.body
        if isinstance(node, ast.If)
        and "WatchTaskDisposition.REJECTED" in ast.unparse(node.test)
        and any(isinstance(item, ast.Try) for item in ast.walk(node))
    )
    resumable = next(
        node
        for node in loop.body
        if isinstance(node, ast.If)
        and "WatchTaskDisposition.RESUMABLE_HALT" in ast.unparse(node.test)
    )
    left = loop.body.index(rejected)
    right = loop.body.index(resumable)
    loop.body[left], loop.body[right] = loop.body[right], loop.body[left]
    ast.fix_missing_locations(tree)
    compile(tree, "<b57-swapped-queue-decisions>", "exec")
    with pytest.raises(AssertionError, match="rejection-archive-before-resumable-halt"):
        _assert_logical_watch_matches_pre_b57(
            tree, failure_label="rejection-archive-before-resumable-halt"
        )


def test_b57_watch_entry_shrinks_and_helpers_stay_below_threshold() -> None:
    size_baseline = json.loads(FUNCTION_SIZE_BASELINE.read_text(encoding="utf-8"))
    watch = _watch_function(SOURCE_TREE)
    watch_span = watch.end_lineno - watch.lineno + 1
    assert watch_span < 407
    assert size_baseline["functions"]["src/inbox_watcher.py::watch_inbox"] == watch_span
    helper_spans = {
        node.name: node.end_lineno - node.lineno + 1
        for node in SOURCE_TREE.body
        if isinstance(node, ast.FunctionDef) and node.name in B57_HELPERS
    }
    assert set(helper_spans) == set(B57_HELPERS)
    assert all(span < 200 for span in helper_spans.values()), helper_spans


def test_target_record_sequence_baseline_is_exact() -> None:
    actual_blob = subprocess.run(
        ["git", "hash-object", str(RECORD_SEQUENCE_BASELINE)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert actual_blob == RECORD_SEQUENCE_BLOB


def test_b61_pre_cut_anchor_binds_b60_and_all_protected_fixtures() -> None:
    anchor = json.loads(B61_PRE_CUT.read_text(encoding="utf-8"))
    assert anchor == {
        "schema_version": "watcher-pre-b61-v1",
        "source_commit": PRE_B61_COMMIT,
        "source_blob": PRE_B61_BLOB,
        "source_path": "src/inbox_watcher.py",
        "function_size_baseline_pre_blob": (
            "63082a085a8f43af923a02efaf06b2f42724c870"
        ),
        "protected_blobs": {
            "tests/fixtures/watcher-outcome-corpus-v1.json": (
                "7b41aa497b56bfb9533fdc985bc1f3e688623573"
            ),
            "tests/fixtures/watcher-catcher-map-pre-b45-v1.json": (
                "ca668ea2c4d68b088db763cd0a4699dd1b87e598"
            ),
            "tests/fixtures/watcher-catcher-helper-bindings-b45-v1.json": (
                "af0ca31deb7ec8c91d75611d71a60d33d57f6f36"
            ),
            "tests/fixtures/watcher-helper-bindings-b57-v1.json": (
                "f9a4ef7d83a456ad8b4f4a08e059d2a6e0b4f53d"
            ),
            "tests/fixtures/watcher-pre-b57-v1.json": (
                "5aeb38509ce2fed914477a4ec2f4f393a70820d9"
            ),
            "tests/fixtures/workflow-record-sequence-baseline-v1.json": (
                HISTORICAL_RECORD_SEQUENCE_BLOB
            ),
        },
        "watch_inbox_lines": 329,
        "return_count": 10,
        "continue_count": 7,
        "catcher_count": 12,
        "exception_catcher_count": 9,
    }
    assert subprocess.run(
        ["git", "rev-parse", f"{PRE_B61_COMMIT}:src/inbox_watcher.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == PRE_B61_BLOB
    assert subprocess.run(
        [
            "git",
            "rev-parse",
            f"{PRE_B61_COMMIT}:tests/fixtures/function-size-baseline-v1.json",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == anchor["function_size_baseline_pre_blob"]
    for path, expected_blob in anchor["protected_blobs"].items():
        assert subprocess.run(
            ["git", "rev-parse", f"{PRE_B61_COMMIT}:{path}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip() == expected_blob
        current_blob = subprocess.run(
            ["git", "hash-object", str(ROOT / path)],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if path == RECORD_SEQUENCE_BASELINE.relative_to(ROOT).as_posix():
            assert current_blob == RECORD_SEQUENCE_BLOB
        else:
            assert current_blob == expected_blob


def test_b61_helpers_inline_to_the_exact_b60_watch_function() -> None:
    actual = ast.dump(
        _logical_pre_b61_watch_function(SOURCE_TREE), include_attributes=False
    )
    expected = ast.dump(_pre_b61_watch_function(), include_attributes=False)
    assert actual == expected
    assert _handler_inventory(SOURCE_TREE) == _load_baseline()["handlers"]


def test_b61_helpers_remain_inside_their_pre_cut_catchers() -> None:
    document = json.loads(B61_HELPER_BINDINGS.read_text(encoding="utf-8"))
    assert document["schema_version"] == "watcher-helper-bindings-b61-v1"
    assert document["pre_cut"] == B61_PRE_CUT.relative_to(ROOT).as_posix()
    expected = {
        item["helper"]: (item["catcher_id"], item["region"])
        for item in document["helpers"]
    }
    actual = {
        helper: _helper_catcher_binding(SOURCE_TREE, helper) for helper in expected
    }
    assert actual == expected
    assert tuple(expected) == B61_HELPERS

    helper_nodes = {
        node.name: node
        for node in SOURCE_TREE.body
        if isinstance(node, ast.FunctionDef) and node.name in B61_HELPERS
    }
    assert set(helper_nodes) == set(B61_HELPERS)
    assert {
        item["helper"]: tuple(item["owned_catchers"])
        for item in document["helpers"]
    } == B61_OWNED_CATCHERS


def test_b61_preserves_all_twelve_physical_catchers_without_narrowing() -> None:
    document = json.loads(B61_HELPER_BINDINGS.read_text(encoding="utf-8"))
    pre_handlers = dict(
        zip(CATCHER_SPECS, _handlers(_pre_b61_watch_function()), strict=True)
    )
    current_handlers = _physical_handlers_by_id(SOURCE_TREE)
    assert len(current_handlers) == 12
    assert Counter(_exception_name(handler) for handler in current_handlers.values()) == {
        "Exception": 9,
        "ValueError": 2,
        "KeyboardInterrupt": 1,
    }
    changed = {
        spec.catcher_id
        for spec in CATCHER_SPECS
        if _body_sha256(current_handlers[spec.catcher_id])
        != _body_sha256(pre_handlers[spec])
    }
    assert changed == set(document["handler_body_sha_change_reasons"])
    assert all(document["handler_body_sha_change_reasons"].values())


def test_b61_keeps_while_and_all_returns_and_continues_in_source_order() -> None:
    anchor = json.loads(B61_PRE_CUT.read_text(encoding="utf-8"))
    current = _watch_function(SOURCE_TREE)
    pre_cut = _pre_b61_watch_function()
    current_while = next(node for node in ast.walk(current) if isinstance(node, ast.While))
    pre_cut_while = next(node for node in ast.walk(pre_cut) if isinstance(node, ast.While))
    assert ast.dump(current_while.test, include_attributes=False) == ast.dump(
        pre_cut_while.test, include_attributes=False
    )
    assert _all_control_sequence(current) == _all_control_sequence(pre_cut)
    assert sum(isinstance(node, ast.Return) for node in ast.walk(current)) == anchor[
        "return_count"
    ]
    assert sum(isinstance(node, ast.Continue) for node in ast.walk(current)) == anchor[
        "continue_count"
    ]


def test_b61_moving_a_helper_out_of_its_catcher_names_the_helper() -> None:
    helper_name = "_archive_rejected_watch_task"
    mutant = _move_helper_call_before_catcher(copy.deepcopy(SOURCE_TREE), helper_name)
    compile(mutant, "<b61-helper-outside-catcher>", "exec")
    with pytest.raises(AssertionError, match=helper_name):
        expected = json.loads(
            B61_HELPER_BINDINGS.read_text(encoding="utf-8")
        )["helpers"]
        for item in expected:
            actual = _helper_catcher_binding(mutant, item["helper"])
            if actual != (item["catcher_id"], item["region"]):
                raise AssertionError(item["helper"])


def test_b61_watch_entry_shrinks_and_helpers_stay_below_threshold() -> None:
    anchor = json.loads(B61_PRE_CUT.read_text(encoding="utf-8"))
    size_baseline = json.loads(FUNCTION_SIZE_BASELINE.read_text(encoding="utf-8"))
    watch = _watch_function(SOURCE_TREE)
    watch_span = watch.end_lineno - watch.lineno + 1
    threshold = size_baseline["threshold_lines"]
    baseline_key = "src/inbox_watcher.py::watch_inbox"
    assert watch_span < anchor["watch_inbox_lines"]
    if watch_span < threshold:
        assert baseline_key not in size_baseline["functions"]
    else:
        assert size_baseline["functions"][baseline_key] == watch_span
    helper_spans = {
        node.name: node.end_lineno - node.lineno + 1
        for node in SOURCE_TREE.body
        if isinstance(node, ast.FunctionDef) and node.name in B61_HELPERS
    }
    assert set(helper_spans) == set(B61_HELPERS)
    assert all(span < threshold for span in helper_spans.values()), helper_spans
