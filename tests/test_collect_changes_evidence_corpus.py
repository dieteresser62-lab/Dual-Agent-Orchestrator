from __future__ import annotations

import ast
import copy
import hashlib
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Callable

import orchestrator
import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "src/orchestrator.py"
STATIC_BASELINE = ROOT / "tests/fixtures/collect-changes-static-pre-b62-v1.json"
RUNTIME_BASELINE = ROOT / "tests/fixtures/collect-changes-runtime-pre-b62-v1.json"
PRE_B63_BASELINE = ROOT / "tests/fixtures/collect-changes-pre-b63-v1.json"
HELPER_BINDINGS = ROOT / "tests/fixtures/collect-changes-helper-bindings-b63-v1.json"
FUNCTION_SIZE_BASELINE = ROOT / "tests/fixtures/function-size-baseline-v1.json"
RECORD_SEQUENCE_BASELINE = ROOT / "tests/fixtures/workflow-record-sequence-baseline-v1.json"
SOURCE_COMMIT = "2b963abd454de0142779d59d656f86684d6e11de"
SOURCE_BLOB = "ca1510789851a576c9e3bb1cbef804cea211ecc1"
PRE_B63_COMMIT = "689a0368a327dbf2c0ec076e248bfff047310341"
RECORD_SEQUENCE_BLOB = "26fb661c8fa382f90e70fb921e3d950da5cae09b"
START_COMMIT = "a" * 40
PLAN_PATH = "docs/internal/work-plan.md"
AUDIT_PATH = "docs/internal/final-review.md"
SOURCE_CHANGE_PATH = "src/runtime.py"
COLLECT_CHANGE_HELPERS = (
    "_collect_change_path_selection",
    "_reuse_final_review_evidence",
    "_collect_fresh_final_review_evidence",
    "_read_semantic_plan_artifact",
    "_build_final_review_snapshot",
    "_final_review_cache_target_digest",
)
CAUGHT_HELPERS = {
    "_read_semantic_plan_artifact": (
        "(UnicodeError, SemanticMarkdownError)",
        "OSError",
    ),
    "_build_final_review_snapshot": ("ValueError",),
    "_final_review_cache_target_digest": ("SideEffectReconciliationError",),
}


@dataclass(frozen=True)
class ScenarioSpec:
    scenario_id: str
    catcher: str | None


SCENARIOS = (
    ScenarioSpec("unicode-plan-raw-fallback", "(UnicodeError, SemanticMarkdownError)"),
    ScenarioSpec("oserror-plan-suppressed", "OSError"),
    ScenarioSpec("valueerror-snapshot-rethrown", "ValueError"),
    ScenarioSpec("side-effect-cache-target-invalid", "SideEffectReconciliationError"),
    ScenarioSpec("final-review-memory-cache-success", None),
    ScenarioSpec("no-workflow-changes-abort", None),
)


@dataclass(frozen=True)
class BuiltCorpus:
    document: dict[str, object]
    build_seconds: float


@dataclass
class _FakeSnapshot:
    changes: SimpleNamespace
    evidence_diff: str
    repository_fingerprint: str
    match_result: bool = True
    collection_elapsed_ms: int = 17
    audit_summary: object | None = None

    def matches(self, *_args: object, **_kwargs: object) -> bool:
        return self.match_result

    def repository_changes(self, _root: Path) -> SimpleNamespace:
        return self.changes


def _driver_class(tree: ast.Module) -> ast.ClassDef:
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ProductionWorkflowDriver"
    )


def _method(tree: ast.Module, name: str = "collect_changes") -> ast.FunctionDef:
    driver = _driver_class(tree)
    return next(
        node
        for node in driver.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _direct_helper_call(
    statement: ast.stmt, helper_names: frozenset[str]
) -> tuple[str, ast.Call] | None:
    value: ast.expr | None = None
    if isinstance(statement, ast.Expr):
        value = statement.value
    elif isinstance(statement, ast.Assign):
        value = statement.value
    if (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and isinstance(value.func.value, ast.Name)
        and value.func.value.id == "self"
        and value.func.attr in helper_names
    ):
        return value.func.attr, value
    return None


def _logical_collect_changes(tree: ast.Module) -> ast.FunctionDef:
    function = copy.deepcopy(_method(tree))
    helper_names = frozenset(COLLECT_CHANGE_HELPERS)
    helpers = {
        name: copy.deepcopy(_method(tree, name)) for name in COLLECT_CHANGE_HELPERS
    }

    def expand_body(body: list[ast.stmt]) -> list[ast.stmt]:
        expanded: list[ast.stmt] = []
        for statement in body:
            direct = _direct_helper_call(statement, helper_names)
            if direct is not None:
                helper_name, call = direct
                helper = helpers[helper_name]
                parameters = [argument.arg for argument in helper.args.args]
                assert parameters[0] == "self"
                assert not call.keywords
                assert [ast.unparse(argument) for argument in call.args] == parameters[1:]
                helper_body = copy.deepcopy(helper.body)
                if (
                    helper_body
                    and isinstance(helper_body[0], ast.Expr)
                    and isinstance(helper_body[0].value, ast.Constant)
                    and isinstance(helper_body[0].value.value, str)
                ):
                    helper_body.pop(0)
                helper_body = expand_body(helper_body)
                if isinstance(statement, ast.Assign):
                    terminal = helper_body[-1]
                    assert isinstance(terminal, ast.Return)
                    assert terminal.value is not None
                    assert len(statement.targets) == 1
                    if ast.unparse(statement.targets[0]) == ast.unparse(terminal.value):
                        helper_body.pop()
                    else:
                        helper_body[-1] = ast.Assign(
                            targets=copy.deepcopy(statement.targets),
                            value=terminal.value,
                        )
                else:
                    assert not any(
                        isinstance(node, ast.Return) for node in ast.walk(helper)
                    )
                expanded.extend(helper_body)
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


def _pre_b62_method() -> ast.FunctionDef:
    source = subprocess.check_output(
        ("git", "show", f"{SOURCE_COMMIT}:src/orchestrator.py"),
        cwd=ROOT,
        text=True,
    )
    return _method(ast.parse(source))


def _parent_map(node: ast.AST) -> dict[ast.AST, ast.AST]:
    return {
        child: parent
        for parent in ast.walk(node)
        for child in ast.iter_child_nodes(parent)
    }


def _caught_helper_context(
    tree: ast.Module, helper_name: str
) -> tuple[str, ast.Try]:
    expected = CAUGHT_HELPERS[helper_name]
    owners = [
        name
        for name in COLLECT_CHANGE_HELPERS
        if name != helper_name
        and any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == helper_name
            for node in ast.walk(_method(tree, name))
        )
    ]
    assert len(owners) == 1, helper_name
    owner = _method(tree, owners[0])
    parents = _parent_map(owner)
    call = next(
        node
        for node in ast.walk(owner)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == helper_name
    )
    current: ast.AST = call
    while current in parents:
        child = current
        current = parents[current]
        if isinstance(current, ast.Try) and any(
            child in tuple(ast.walk(statement)) for statement in current.body
        ):
            return owners[0], current
    raise AssertionError(f"{helper_name} left its catcher {expected}")


def _caught_helper_bindings(tree: ast.Module) -> dict[str, tuple[str, ...]]:
    return {
        helper_name: tuple(
            ast.unparse(handler.type)
            for handler in _caught_helper_context(tree, helper_name)[1].handlers
        )
        for helper_name in CAUGHT_HELPERS
    }


def _helper_binding_document(tree: ast.Module) -> dict[str, object]:
    method_names = ("collect_changes", *COLLECT_CHANGE_HELPERS)
    call_graph = {
        name: [
            node.func.attr
            for node in _ordered(_method(tree, name), ast.Call)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
            and node.func.attr in COLLECT_CHANGE_HELPERS
        ]
        for name in method_names
    }
    caught_helpers: list[dict[str, object]] = []
    for helper_name in CAUGHT_HELPERS:
        owner_name, protecting_try = _caught_helper_context(tree, helper_name)
        caught_helpers.append(
            {
                "helper": helper_name,
                "owner": owner_name,
                "catchers": [
                    {
                        "type": ast.unparse(handler.type),
                        "classification": _handler_behavior(handler),
                        "effect": "\n".join(
                            ast.unparse(statement) for statement in handler.body
                        ),
                    }
                    for handler in protecting_try.handlers
                ],
            }
        )
    return {
        "schema_version": "collect-changes-helper-bindings-b63-v1",
        "call_graph": call_graph,
        "caught_helpers": caught_helpers,
    }


def _move_helper_out_of_catcher(tree: ast.Module, helper_name: str) -> None:
    owner_name, protecting_try = _caught_helper_context(tree, helper_name)
    owner = _method(tree, owner_name)
    call_statement = next(
        statement
        for statement in ast.walk(owner)
        if isinstance(statement, (ast.Expr, ast.Assign))
        and _direct_helper_call(statement, frozenset({helper_name})) is not None
    )
    parent = _parent_map(owner)[protecting_try]
    body = next(
        getattr(parent, field)
        for field in ("body", "orelse", "finalbody")
        if isinstance(getattr(parent, field, None), list)
        and protecting_try in getattr(parent, field)
    )
    body.insert(body.index(protecting_try), call_statement)
    for node in ast.walk(protecting_try):
        for field in ("body", "orelse", "finalbody"):
            statements = getattr(node, field, None)
            if isinstance(statements, list) and call_statement in statements:
                statements.remove(call_statement)
                if not statements:
                    statements.append(ast.Pass())
                return
    raise AssertionError(helper_name)


def _ordered(node: ast.AST, kind: type[ast.AST]) -> list[ast.AST]:
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


def _raise_fact(node: ast.Raise) -> dict[str, object]:
    assert isinstance(node.exc, ast.Call)
    message = node.exc.args[0] if node.exc.args else None
    return {
        "exception_type": ast.unparse(node.exc.func),
        "message": "" if message is None else ast.unparse(message),
    }


def _handler_behavior(handler: ast.ExceptHandler) -> str:
    return "rethrows" if any(isinstance(node, ast.Raise) for node in ast.walk(handler)) else "mitigates"


def _static_document(source: str | None = None) -> dict[str, object]:
    function = _logical_collect_changes(
        ast.parse(SOURCE_PATH.read_text(encoding="utf-8") if source is None else source)
    )
    pre_b62 = _pre_b62_method()
    conditions = _ordered(function, ast.If)
    aborts = _ordered(function, ast.Raise)
    catchers = _ordered(function, ast.ExceptHandler)
    returns = _ordered(function, ast.Return)
    return {
        "schema_version": "collect-changes-static-pre-b62-v1",
        "source_commit": SOURCE_COMMIT,
        "source_blob": SOURCE_BLOB,
        "function": "ProductionWorkflowDriver.collect_changes",
        # This is frozen pre-cut inventory metadata. Worktree equivalence is
        # enforced independently by the logical AST comparison below.
        "line_count": pre_b62.end_lineno - pre_b62.lineno + 1,
        "conditions": [
            {
                "ordinal": ordinal,
                "expression": ast.unparse(node.test),
                "read_names": _read_names(node.test),
            }
            for ordinal, node in enumerate(conditions, 1)
        ],
        "aborts": [
            {"ordinal": ordinal, **_raise_fact(node)}
            for ordinal, node in enumerate(aborts, 1)
        ],
        "catchers": [
            {
                "ordinal": ordinal,
                "exception_type": ast.unparse(node.type),
                "behavior": _handler_behavior(node),
                "body": [ast.unparse(statement) for statement in node.body],
            }
            for ordinal, node in enumerate(catchers, 1)
        ],
        "returns": [
            {
                "ordinal": ordinal,
                "expression": "" if node.value is None else ast.unparse(node.value),
            }
            for ordinal, node in enumerate(returns, 1)
        ],
    }


def _state(module: ModuleType, *, final_review: bool) -> SimpleNamespace:
    return SimpleNamespace(
        execution_mode=module.TaskMode.IMPLEMENT.value if final_review else module.TaskMode.PLAN_ONLY.value,
        current_work_unit=SimpleNamespace(
            kind=(
                module.WorkUnitKind.FINAL_REVIEW
                if final_review
                else module.WorkUnitKind.PLAN
            )
        ),
        current_slice=SimpleNamespace(scope_paths=(SOURCE_CHANGE_PATH,)),
        work_plan_path=PLAN_PATH,
        audit_report_path=AUDIT_PATH if final_review else None,
        task_digest=None,
        task_file="task.md",
    )


def _changes(*, digest: str, paths: tuple[str, ...], diff: str) -> SimpleNamespace:
    return SimpleNamespace(
        entries=tuple(SimpleNamespace(path=path) for path in paths),
        fingerprint=digest,
        paths=paths,
        diff_text=diff,
        review_paths=paths,
    )


def _driver(module: ModuleType, root: Path, state: SimpleNamespace | None) -> object:
    driver = module.ProductionWorkflowDriver(
        repository_root=root,
        state_file=root / ".orchestrator/state.json",
        agents={},
        config=SimpleNamespace(),
        allowed_roots=(root,),
    )
    driver.active_state = state
    return driver


def _evidence(
    rendered: object,
    *,
    semantic_paths: tuple[str, ...],
    raw_plan_paths: tuple[str, ...],
) -> dict[str, object]:
    return {
        "paths": list(rendered.paths),
        "digest": rendered.fingerprint,
        "raw_plan_artifact": raw_plan_paths[0] if raw_plan_paths else None,
        "semantic_paths": list(semantic_paths),
        "full_diff": rendered.full_diff,
        "gate_paths": list(rendered.gate_paths),
    }


def _run_scenario(
    module: ModuleType,
    spec: ScenarioSpec,
    root: Path,
    provider_counter: dict[str, int],
) -> dict[str, object]:
    root.mkdir(parents=True, exist_ok=True)
    plan = root / PLAN_PATH
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text("# Work plan\n\nBound plan evidence.\n", encoding="utf-8")
    control_path = root / ".orchestrator"
    assert not control_path.exists()
    capture: dict[str, object] = {
        "semantic_paths": (),
        "raw_plan_paths": (),
        "cache_writes": [],
        "collection_count": 0,
    }

    def forbidden_provider(*_args: object, **_kwargs: object) -> object:
        provider_counter["count"] += 1
        raise AssertionError("B62 must not start a real provider")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(module, "run_native_codex_agent_checked", forbidden_provider)
        patch.setattr(module, "run_native_review_agent_checked", forbidden_provider)

        if spec.scenario_id in {
            "unicode-plan-raw-fallback",
            "oserror-plan-suppressed",
        }:
            state = _state(module, final_review=False)
            driver = _driver(module, root, state)
            digest = "1" * 64 if spec.scenario_id.startswith("unicode") else "2" * 64
            collected = _changes(
                digest=digest,
                paths=(PLAN_PATH,),
                diff=f"{spec.scenario_id} evidence",
            )

            def collect(*_args: object, **kwargs: object) -> SimpleNamespace:
                capture["collection_count"] = int(capture["collection_count"]) + 1
                capture["semantic_paths"] = tuple(kwargs["semantic_markdown_paths"])
                capture["raw_plan_paths"] = tuple(kwargs["raw_fingerprint_paths"])
                return collected

            def reject_semantics(*_args: object, **_kwargs: object) -> object:
                if spec.scenario_id.startswith("unicode"):
                    raise UnicodeError("B62 invalid UTF-8 plan")
                raise OSError("B62 unreadable plan")

            patch.setattr(module, "collect_repository_changes", collect)
            patch.setattr(module, "canonical_semantic_markdown", reject_semantics)
            try:
                rendered = driver.collect_changes(START_COMMIT)
            except Exception as exc:  # noqa: BLE001 - the corpus binds exact failures
                outcome: dict[str, object] = {
                    "kind": "raised",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            else:
                outcome = {
                    "kind": "returned",
                    "evidence": _evidence(
                        rendered,
                        semantic_paths=tuple(capture["semantic_paths"]),
                        raw_plan_paths=tuple(capture["raw_plan_paths"]),
                    ),
                    "collection_count": capture["collection_count"],
                }

        elif spec.scenario_id == "valueerror-snapshot-rethrown":
            driver = _driver(module, root, _state(module, final_review=True))
            collected = _changes(
                digest="3" * 64,
                paths=(SOURCE_CHANGE_PATH,),
                diff="pre-snapshot evidence",
            )
            probe = SimpleNamespace(identity_digest="8" * 64, fingerprint="3" * 64)
            patch.setattr(module, "probe_repository_snapshot", lambda *_args, **_kwargs: probe)
            patch.setattr(module, "collect_repository_changes", lambda *_args, **_kwargs: collected)
            patch.setattr(
                module,
                "build_final_review_evidence_snapshot",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    ValueError("B62 concurrent repository mutation")
                ),
            )
            driver._final_review_evidence_cache_path = lambda **_kwargs: root / "cache.json"
            driver._final_review_cache_authorized_digest = lambda *_args, **_kwargs: None
            try:
                driver.collect_changes(START_COMMIT)
            except Exception as exc:  # noqa: BLE001 - the corpus binds exact failures
                outcome = {
                    "kind": "raised",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "cause_type": type(exc.__cause__).__name__ if exc.__cause__ else None,
                }
            else:
                outcome = {"kind": "returned-unexpectedly"}

        elif spec.scenario_id == "side-effect-cache-target-invalid":
            driver = _driver(module, root, _state(module, final_review=True))
            driver._artifact_bridge = object()
            collected = _changes(
                digest="4" * 64,
                paths=(AUDIT_PATH, SOURCE_CHANGE_PATH),
                diff="canonical final-review evidence",
            )
            snapshot = _FakeSnapshot(
                changes=collected,
                evidence_diff="compacted final-review evidence",
                repository_fingerprint=collected.fingerprint,
            )
            probe = SimpleNamespace(identity_digest="8" * 64, fingerprint="4" * 64)

            class CacheDigestProbe(str):
                def __hash__(self) -> int:
                    return hash("invalid")

                def __eq__(self, other: object) -> bool:
                    if isinstance(other, str):
                        capture.setdefault("cache_target_digests", []).append(other)
                    return super().__eq__(other)

            def collect(*_args: object, **kwargs: object) -> SimpleNamespace:
                capture["collection_count"] = int(capture["collection_count"]) + 1
                capture["semantic_paths"] = tuple(kwargs["semantic_markdown_paths"])
                capture["raw_plan_paths"] = tuple(kwargs["raw_fingerprint_paths"])
                return collected

            def cache_digest(content: bytes) -> CacheDigestProbe:
                digest = hashlib.sha256(content).hexdigest()
                capture["cache_content_digest"] = digest
                return CacheDigestProbe(digest)

            patch.setattr(module, "probe_repository_snapshot", lambda *_args, **_kwargs: probe)
            patch.setattr(module, "collect_repository_changes", collect)
            patch.setattr(module, "build_final_review_evidence_snapshot", lambda *_args, **_kwargs: snapshot)
            patch.setattr(module, "render_final_review_evidence_cache", lambda _snapshot: "bound cache content\n")
            patch.setattr(module, "sha256_bytes", cache_digest)
            patch.setattr(
                module,
                "file_state_digest",
                lambda _path: (_ for _ in ()).throw(
                    module.SideEffectReconciliationError("B62 unstable cache target")
                ),
            )
            cache_path = root / "cache.json"

            def evidence_cache_path(**_kwargs: object) -> Path:
                capture["cache_target"] = cache_path.name
                return cache_path

            driver._final_review_evidence_cache_path = evidence_cache_path
            driver._final_review_cache_authorized_digest = lambda *_args, **_kwargs: None
            driver._write_side_effect_file = (
                lambda path, content, **_kwargs: capture["cache_writes"].append(
                    (path.name, content)
                )
            )
            try:
                rendered = driver.collect_changes(START_COMMIT)
            except Exception as exc:  # noqa: BLE001 - mutant output is compared by name
                outcome = {
                    "kind": "raised",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            else:
                outcome = {
                    "kind": "returned",
                    "evidence": _evidence(
                        rendered,
                        semantic_paths=tuple(capture["semantic_paths"]),
                        raw_plan_paths=tuple(capture["raw_plan_paths"]),
                    ),
                    "cache": {
                        "target": capture.get("cache_target"),
                        "content_digest": capture.get("cache_content_digest"),
                        "target_digest_after_catch": next(
                            iter(capture.get("cache_target_digests", ())), None
                        ),
                        "write_count": len(capture["cache_writes"]),
                    },
                }

        elif spec.scenario_id == "final-review-memory-cache-success":
            driver = _driver(module, root, _state(module, final_review=True))
            collected = _changes(
                digest="5" * 64,
                paths=(PLAN_PATH, SOURCE_CHANGE_PATH),
                diff="canonical cached evidence",
            )
            snapshot = _FakeSnapshot(
                changes=collected,
                evidence_diff="cache-derived compact evidence",
                repository_fingerprint=collected.fingerprint,
                collection_elapsed_ms=23,
            )
            driver._final_review_evidence_snapshot = snapshot
            probe = SimpleNamespace(identity_digest="9" * 64, fingerprint="5" * 64)

            def probe_snapshot(*_args: object, **kwargs: object) -> SimpleNamespace:
                capture["semantic_paths"] = tuple(kwargs["semantic_markdown_paths"])
                return probe

            def forbidden_collection(*_args: object, **_kwargs: object) -> object:
                capture["collection_count"] = int(capture["collection_count"]) + 1
                raise AssertionError("matching memory evidence must avoid recollection")

            patch.setattr(module, "probe_repository_snapshot", probe_snapshot)
            patch.setattr(module, "collect_repository_changes", forbidden_collection)
            driver._final_review_evidence_cache_path = lambda **_kwargs: root / "cache.json"
            rendered = driver.collect_changes(START_COMMIT)
            semantic_paths = tuple(capture["semantic_paths"])
            observed_plan_artifact = next(
                (
                    path
                    for path in semantic_paths
                    if path == driver.active_state.work_plan_path
                ),
                None,
            )
            outcome = {
                "kind": "returned",
                "evidence": _evidence(
                    rendered,
                    semantic_paths=semantic_paths,
                    raw_plan_paths=(),
                ),
                "plan_artifact": observed_plan_artifact,
                "cache": {
                    "source": "memory_snapshot",
                    "repository_digest": rendered.fingerprint,
                    "derived_content": rendered.full_diff,
                    "reuse_count": driver._final_review_evidence_reuses,
                    "collection_count": capture["collection_count"],
                },
            }

        else:
            driver = _driver(module, root, None)
            empty = _changes(digest="6" * 64, paths=(), diff="")
            patch.setattr(module, "collect_repository_changes", lambda *_args, **_kwargs: empty)
            try:
                driver.collect_changes(START_COMMIT)
            except Exception as exc:  # noqa: BLE001 - the corpus binds exact failures
                outcome = {
                    "kind": "raised",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            else:
                outcome = {"kind": "returned-unexpectedly"}

    assert provider_counter["count"] == 0
    assert not control_path.exists()
    return {
        "scenario_id": spec.scenario_id,
        "catcher": spec.catcher,
        "outcome": outcome,
        "provider_start_count": 0,
        "orchestrator_control_created": False,
    }


_RUNTIME_BUILD_COUNT = 0


def _build_runtime_corpus(module: ModuleType, base: Path) -> BuiltCorpus:
    global _RUNTIME_BUILD_COUNT
    _RUNTIME_BUILD_COUNT += 1
    started = time.perf_counter()
    provider_counter = {"count": 0}
    scenarios = [
        _run_scenario(module, spec, base / spec.scenario_id, provider_counter)
        for spec in SCENARIOS
    ]
    assert provider_counter["count"] == 0
    return BuiltCorpus(
        {
            "schema_version": "collect-changes-runtime-pre-b62-v1",
            "source_commit": SOURCE_COMMIT,
            "source_blob": SOURCE_BLOB,
            "scenarios": scenarios,
        },
        time.perf_counter() - started,
    )


@pytest.fixture(scope="session")
def collect_changes_corpus(tmp_path_factory: pytest.TempPathFactory) -> BuiltCorpus:
    return _build_runtime_corpus(
        orchestrator,
        tmp_path_factory.mktemp("b62-collect-changes"),
    )


def _load_baseline(path: Path) -> dict[str, object]:
    document = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _git(*args: str) -> str:
    return subprocess.check_output(("git", *args), cwd=ROOT, text=True).strip()


def _scenario(document: dict[str, object], scenario_id: str) -> dict[str, object]:
    scenarios = document["scenarios"]
    assert isinstance(scenarios, list)
    return next(item for item in scenarios if item["scenario_id"] == scenario_id)


def _assert_scenario_matches(
    actual: dict[str, object], expected: dict[str, object]
) -> None:
    scenario_id = str(expected["scenario_id"])
    if actual != expected:
        raise AssertionError(scenario_id)


def _load_mutant(transform: Callable[[ast.Module], None]) -> ModuleType:
    tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
    transform(tree)
    ast.fix_missing_locations(tree)
    module = ModuleType(f"_b62_collect_changes_mutant_{id(tree)}")
    module.__file__ = str(SOURCE_PATH)
    sys.modules[module.__name__] = module
    try:
        exec(compile(tree, str(SOURCE_PATH), "exec"), module.__dict__)
    finally:
        sys.modules.pop(module.__name__, None)
    return module


def _replace_catcher_body(
    exception_type: str, replacement: list[ast.stmt]
) -> Callable[[ast.Module], None]:
    def transform(tree: ast.Module) -> None:
        handlers = [
            item
            for method_name in ("collect_changes", *COLLECT_CHANGE_HELPERS)
            for item in ast.walk(_method(tree, method_name))
            if isinstance(item, ast.ExceptHandler)
            and ast.unparse(item.type) == exception_type
        ]
        assert len(handlers) == 1, exception_type
        handlers[0].body = replacement

    return transform


def test_b63_anchor_binds_the_b62_source_corpus_and_guards() -> None:
    anchor = _load_baseline(PRE_B63_BASELINE)
    assert anchor["schema_version"] == "collect-changes-pre-b63-v1"
    assert anchor["source_commit"] == PRE_B63_COMMIT
    for path_key, blob_key in (
        ("source_path", "source_blob"),
        ("b62_test_path", "b62_test_blob"),
        ("b62_static_path", "b62_static_blob"),
        ("b62_runtime_path", "b62_runtime_blob"),
        ("cohesion_guard_path", "cohesion_guard_blob"),
        ("function_size_baseline_path", "function_size_baseline_pre_blob"),
    ):
        path = str(anchor[path_key])
        assert _git("rev-parse", f"{PRE_B63_COMMIT}:{path}") == anchor[blob_key], path

    for path_key, blob_key in (
        ("b62_static_path", "b62_static_blob"),
        ("b62_runtime_path", "b62_runtime_blob"),
    ):
        path = str(anchor[path_key])
        assert _git("hash-object", str(ROOT / path)) == anchor[blob_key], path
        assert _git("diff", "--", path) == "", path


def test_pre_b62_source_anchor_and_logical_collect_changes_are_exact() -> None:
    anchored_blob = subprocess.check_output(
        ("git", "rev-parse", f"{SOURCE_COMMIT}:src/orchestrator.py"),
        cwd=ROOT,
        text=True,
    ).strip()
    assert anchored_blob == SOURCE_BLOB
    logical = _logical_collect_changes(
        ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
    )
    assert ast.dump(logical, include_attributes=False) == ast.dump(
        _pre_b62_method(), include_attributes=False
    )


def test_b63_helper_call_graph_catchers_and_mitigations_are_exact() -> None:
    tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
    assert _helper_binding_document(tree) == _load_baseline(HELPER_BINDINGS)
    assert _caught_helper_bindings(tree) == CAUGHT_HELPERS
    driver = _driver_class(tree)
    for helper_name in COLLECT_CHANGE_HELPERS:
        calls = [
            node
            for node in ast.walk(driver)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
            and node.func.attr == helper_name
        ]
        assert len(calls) == 1, helper_name


@pytest.mark.parametrize("helper_name", tuple(CAUGHT_HELPERS))
def test_moving_a_helper_outside_its_catcher_names_the_helper(
    helper_name: str,
) -> None:
    tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
    _move_helper_out_of_catcher(tree, helper_name)
    with pytest.raises(AssertionError, match=helper_name):
        _caught_helper_bindings(tree)


def test_b63_removes_collect_changes_from_the_b32_size_ratchet() -> None:
    anchor = _load_baseline(PRE_B63_BASELINE)
    baseline = _load_baseline(FUNCTION_SIZE_BASELINE)
    threshold = int(anchor["function_size_threshold"])
    assert baseline["threshold_lines"] == threshold
    functions = baseline["functions"]
    assert isinstance(functions, dict)
    assert "src/orchestrator.py::ProductionWorkflowDriver.collect_changes" not in functions
    tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
    for name in ("collect_changes", *COLLECT_CHANGE_HELPERS):
        method = _method(tree, name)
        assert method.end_lineno is not None
        assert method.end_lineno - method.lineno + 1 < threshold, name


def test_b63_keeps_b21_b23_and_b25_guards_and_baselines_byte_identical() -> None:
    anchor = _load_baseline(PRE_B63_BASELINE)
    protected = anchor["protected_blobs"]
    assert isinstance(protected, dict)
    assert protected[RECORD_SEQUENCE_BASELINE.relative_to(ROOT).as_posix()] == (
        RECORD_SEQUENCE_BLOB
    )
    for path, blob in protected.items():
        assert _git("hash-object", str(ROOT / path)) == blob, path
        assert _git("rev-parse", f"{PRE_B63_COMMIT}:{path}") == blob, path


def test_static_collect_changes_contract_is_complete_and_source_ordered() -> None:
    baseline = _load_baseline(STATIC_BASELINE)
    assert _static_document() == baseline
    assert len(baseline["conditions"]) == 15
    assert len(baseline["aborts"]) == 2
    assert len(baseline["returns"]) == 1
    assert [item["exception_type"] for item in baseline["catchers"]] == [
        "(UnicodeError, SemanticMarkdownError)",
        "OSError",
        "ValueError",
        "SideEffectReconciliationError",
    ]
    assert [item["behavior"] for item in baseline["catchers"]] == [
        "mitigates",
        "mitigates",
        "rethrows",
        "mitigates",
    ]
    assert all(item["read_names"] for item in baseline["conditions"])
    assert all(item["message"] for item in baseline["aborts"])


@pytest.mark.parametrize("scenario_id", [spec.scenario_id for spec in SCENARIOS])
def test_provider_free_collect_changes_scenario_matches_bound_output(
    collect_changes_corpus: BuiltCorpus,
    scenario_id: str,
) -> None:
    expected = _scenario(_load_baseline(RUNTIME_BASELINE), scenario_id)
    actual = _scenario(collect_changes_corpus.document, scenario_id)
    _assert_scenario_matches(actual, expected)


def test_collect_changes_corpus_builds_once_and_records_runtime(
    collect_changes_corpus: BuiltCorpus,
) -> None:
    assert _RUNTIME_BUILD_COUNT == 1
    assert collect_changes_corpus.build_seconds > 0
    assert [item["scenario_id"] for item in collect_changes_corpus.document["scenarios"]] == [
        spec.scenario_id for spec in SCENARIOS
    ]


def test_oserror_raise_mutation_names_the_suppression_scenario(tmp_path: Path) -> None:
    mutant = _load_mutant(
        _replace_catcher_body("OSError", [ast.Raise(exc=None, cause=None)])
    )
    spec = next(item for item in SCENARIOS if item.scenario_id == "oserror-plan-suppressed")
    actual = _run_scenario(mutant, spec, tmp_path / "oserror-mutant", {"count": 0})
    expected = _scenario(_load_baseline(RUNTIME_BASELINE), spec.scenario_id)

    with pytest.raises(AssertionError, match="oserror-plan-suppressed"):
        _assert_scenario_matches(actual, expected)


def test_invalid_digest_replacement_removal_names_cache_scenario(tmp_path: Path) -> None:
    mutant = _load_mutant(
        _replace_catcher_body(
            "SideEffectReconciliationError",
            [ast.Pass()],
        )
    )
    spec = next(
        item
        for item in SCENARIOS
        if item.scenario_id == "side-effect-cache-target-invalid"
    )
    actual = _run_scenario(mutant, spec, tmp_path / "digest-mutant", {"count": 0})
    expected = _scenario(_load_baseline(RUNTIME_BASELINE), spec.scenario_id)

    with pytest.raises(AssertionError, match="side-effect-cache-target-invalid"):
        _assert_scenario_matches(actual, expected)
