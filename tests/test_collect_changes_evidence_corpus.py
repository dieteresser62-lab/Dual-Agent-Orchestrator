from __future__ import annotations

import ast
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
SOURCE_COMMIT = "2b963abd454de0142779d59d656f86684d6e11de"
SOURCE_BLOB = "ca1510789851a576c9e3bb1cbef804cea211ecc1"
START_COMMIT = "a" * 40
PLAN_PATH = "docs/internal/work-plan.md"
AUDIT_PATH = "docs/internal/final-review.md"
SOURCE_CHANGE_PATH = "src/runtime.py"


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


def _method(tree: ast.Module) -> ast.FunctionDef:
    driver = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ProductionWorkflowDriver"
    )
    return next(
        node
        for node in driver.body
        if isinstance(node, ast.FunctionDef) and node.name == "collect_changes"
    )


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
    function = _method(
        ast.parse(SOURCE_PATH.read_text(encoding="utf-8") if source is None else source)
    )
    conditions = _ordered(function, ast.If)
    aborts = _ordered(function, ast.Raise)
    catchers = _ordered(function, ast.ExceptHandler)
    returns = _ordered(function, ast.Return)
    return {
        "schema_version": "collect-changes-static-pre-b62-v1",
        "source_commit": SOURCE_COMMIT,
        "source_blob": SOURCE_BLOB,
        "function": "ProductionWorkflowDriver.collect_changes",
        "line_count": function.end_lineno - function.lineno + 1,
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


def _load_mutant(transform: Callable[[ast.FunctionDef], None]) -> ModuleType:
    tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
    transform(_method(tree))
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
) -> Callable[[ast.FunctionDef], None]:
    def transform(function: ast.FunctionDef) -> None:
        handler = next(
            item
            for item in ast.walk(function)
            if isinstance(item, ast.ExceptHandler)
            and ast.unparse(item.type) == exception_type
        )
        handler.body = replacement

    return transform


def test_pre_b62_source_anchor_and_worktree_bytes_are_exact() -> None:
    anchored_blob = subprocess.check_output(
        ("git", "rev-parse", f"{SOURCE_COMMIT}:src/orchestrator.py"),
        cwd=ROOT,
        text=True,
    ).strip()
    worktree_blob = subprocess.run(
        ("git", "hash-object", "--", "src/orchestrator.py"),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert anchored_blob == SOURCE_BLOB
    assert worktree_blob == SOURCE_BLOB


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
