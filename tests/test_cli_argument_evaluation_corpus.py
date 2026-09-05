from __future__ import annotations

import argparse
import ast
import copy
import dataclasses
import hashlib
import io
import json
import subprocess
import sys
import time
from contextlib import redirect_stderr
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping

import cli
import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "src/cli.py"
STATIC_BASELINE = ROOT / "tests/fixtures/cli-argument-evaluation-pre-b64-v1.json"
RUNTIME_BASELINE = ROOT / "tests/fixtures/cli-argument-evaluation-corpus-v1.json"
SOURCE_COMMIT = "36fb8a8edb9d2c4bea861b3198c268240473f649"
SOURCE_BLOB = "366d01322ca79a3ba120f3ddb1d66f403f3c7c97"


@dataclass(frozen=True)
class RejectionSpec:
    scenario_id: str
    description: str
    argv: tuple[str, ...]
    environ: Mapping[str, str]
    source_kind: str
    source_ordinal: int
    catcher_ordinal: int | None = None


@dataclass(frozen=True)
class SuccessSpec:
    scenario_id: str
    description: str
    argv: tuple[str, ...]
    environ: Mapping[str, str]
    target_return_ordinal: int


@dataclass(frozen=True)
class BuiltCorpus:
    document: dict[str, object]
    success_namespaces: Mapping[str, dict[str, object]]
    build_seconds: float


REJECTION_SPECS = (
    RejectionSpec(
        "task-file-source-conflict",
        "A positional task file and --task-file are mutually exclusive.",
        ("first.md", "--task-file", "second.md"),
        {},
        "parser_error",
        1,
    ),
    RejectionSpec(
        "dry-run-scenario-without-dry-run",
        "--dry-run-scenario is rejected unless --dry-run is active.",
        ("--dry-run-scenario", "scenario.json"),
        {},
        "parser_error",
        2,
    ),
    RejectionSpec(
        "dry-run-report-without-scenario",
        "--dry-run-report is rejected unless a dry-run scenario is supplied.",
        ("--dry-run-report", "report.json"),
        {},
        "parser_error",
        3,
    ),
    RejectionSpec(
        "explicit-config-missing",
        "An explicitly selected missing configuration file is rejected.",
        ("--config", "missing.toml"),
        {},
        "raise",
        1,
    ),
    RejectionSpec(
        "quota-environment-not-integer",
        "A non-integer quota environment value is rethrown as ConfigError.",
        (),
        {"RUN_TASK_QUOTA_SAFETY_MARGIN": "not-an-integer"},
        "raise",
        2,
        1,
    ),
    RejectionSpec(
        "invalid-quota-policy",
        "A negative quota safety margin is translated to parser.error.",
        ("--quota-safety-margin", "-1"),
        {},
        "parser_error",
        4,
        2,
    ),
    RejectionSpec(
        "invalid-transient-retry-policy",
        "A negative transient retry delay is translated to parser.error.",
        ("--transient-retry-initial-delay", "-1"),
        {},
        "parser_error",
        5,
        3,
    ),
    RejectionSpec(
        "gate-decision-without-resume",
        "A gate decision is rejected unless --resume was explicit.",
        ("--approve-gate", "--gate-rationale", "reviewed"),
        {},
        "parser_error",
        6,
    ),
    RejectionSpec(
        "gate-decision-without-rationale",
        "An explicit gate decision requires a nonblank rationale.",
        ("--resume", "--approve-gate"),
        {},
        "parser_error",
        7,
    ),
    RejectionSpec(
        "gate-rationale-without-decision",
        "A rationale without an approve or reject decision is rejected.",
        ("--resume", "--gate-rationale", "reviewed"),
        {},
        "parser_error",
        8,
    ),
    RejectionSpec(
        "invalid-agent-setting",
        "An invalid agent setting is rethrown as ConfigError.",
        (),
        {"RUN_TASK_CLAUDE_EFFORT": "extreme"},
        "raise",
        3,
        4,
    ),
)

SUCCESS_SPECS = (
    SuccessSpec(
        "valid-explicit-quota-arguments",
        "All quota integers use the explicit argument return path.",
        (
            "--quota-safety-margin",
            "17",
            "--quota-max-wait",
            "120",
            "--quota-max-auto-resumes",
            "2",
            "--quota-heartbeat-interval",
            "15",
        ),
        {},
        1,
    ),
    SuccessSpec(
        "valid-default-arguments",
        "Absent quota and retry settings use their default return path.",
        (),
        {},
        2,
    ),
    SuccessSpec(
        "valid-quota-environment-integer",
        "A valid quota environment value uses the parsed integer return path.",
        (),
        {"RUN_TASK_QUOTA_SAFETY_MARGIN": "17"},
        3,
    ),
    SuccessSpec(
        "valid-explicit-gate-decision",
        "A complete gate decision reaches the outer Namespace return path.",
        (
            "--resume",
            "--approve-gate",
            "--gate-rationale",
            "reviewed exact persisted evidence",
        ),
        {},
        4,
    ),
)

RAISE_SCENARIOS = {
    spec.source_ordinal: spec.scenario_id
    for spec in REJECTION_SPECS
    if spec.source_kind == "raise"
}
PARSER_ERROR_SCENARIOS = {
    spec.source_ordinal: spec.scenario_id
    for spec in REJECTION_SPECS
    if spec.source_kind == "parser_error"
}
CATCHER_SCENARIOS = {
    spec.catcher_ordinal: spec.scenario_id
    for spec in REJECTION_SPECS
    if spec.catcher_ordinal is not None
}


def _git(*args: str) -> str:
    return subprocess.check_output(("git", *args), cwd=ROOT, text=True).strip()


def _load_json(path: Path) -> dict[str, object]:
    document = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _parse_args_function(tree: ast.Module) -> ast.FunctionDef:
    matches = tuple(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "parse_args"
    )
    assert len(matches) == 1
    return matches[0]


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
        "message_expression": "" if message is None else ast.unparse(message),
    }


def _is_parser_error_call(node: ast.Call) -> bool:
    return (
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "parser"
        and node.func.attr == "error"
    )


def _handler_classification(handler: ast.ExceptHandler) -> str:
    return (
        "rethrows"
        if any(isinstance(node, ast.Raise) for node in ast.walk(handler))
        else "mitigates"
    )


def _static_document() -> dict[str, object]:
    function = _parse_args_function(ast.parse(SOURCE_PATH.read_text(encoding="utf-8")))
    conditions = _ordered(function, ast.If)
    raises = _ordered(function, ast.Raise)
    parser_errors = [
        node
        for node in _ordered(function, ast.Call)
        if isinstance(node, ast.Call) and _is_parser_error_call(node)
    ]
    returns = _ordered(function, ast.Return)
    catchers = _ordered(function, ast.ExceptHandler)
    assert function.end_lineno is not None
    return {
        "schema_version": "cli-argument-evaluation-pre-b64-v1",
        "source_commit": SOURCE_COMMIT,
        "source_path": "src/cli.py",
        "source_blob": SOURCE_BLOB,
        "function": "parse_args",
        "function_span_lines": function.end_lineno - function.lineno + 1,
        "conditions": [
            {
                "ordinal": ordinal,
                "expression": ast.unparse(node.test),
                "read_names": _read_names(node.test),
            }
            for ordinal, node in enumerate(conditions, 1)
        ],
        "raises": [
            {
                "ordinal": ordinal,
                "scenario_id": RAISE_SCENARIOS[ordinal],
                **_raise_fact(node),
            }
            for ordinal, node in enumerate(raises, 1)
        ],
        "parser_errors": [
            {
                "ordinal": ordinal,
                "scenario_id": PARSER_ERROR_SCENARIOS[ordinal],
                "message_expression": ast.unparse(node.args[0]),
            }
            for ordinal, node in enumerate(parser_errors, 1)
        ],
        "returns": [
            {
                "ordinal": ordinal,
                "scenario_id": SUCCESS_SPECS[ordinal - 1].scenario_id,
                "expression": "" if node.value is None else ast.unparse(node.value),
            }
            for ordinal, node in enumerate(returns, 1)
        ],
        "catchers": [
            {
                "ordinal": ordinal,
                "scenario_id": CATCHER_SCENARIOS[ordinal],
                "exception_type": ast.unparse(node.type),
                "classification": _handler_classification(node),
                "body": [ast.unparse(statement) for statement in node.body],
            }
            for ordinal, node in enumerate(catchers, 1)
        ],
        "unreachable_rejections": [],
        "function_size_baseline_path": "tests/fixtures/function-size-baseline-v1.json",
        "function_size_baseline_blob": "4d23a25bfa81e00f19d5089570284ace36fbb641",
        "protected_blobs": {
            "tests/conftest.py": "1545d657329ccd409afce0501d7a4f53a12dac26",
            "tests/test_test_run_isolation.py": "6bd1c9e1c23a1600fd628ada54c62153d8cd6051",
            "tests/fixtures/gitignored-read-exemptions-baseline-v1.json": "d36f7ec5dc62ff70991156b8d02a4d45fcae8930",
            "tests/fixtures/provider-name-coupling-baseline-v1.json": "05d503f957d385d7630a7aa5697463dd23cc9a5c",
            "tests/fixtures/workflow-record-sequence-baseline-v1.json": "26fb661c8fa382f90e70fb921e3d950da5cae09b",
        },
    }


def _normalize_value(value: object) -> object:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            "__type__": type(value).__name__,
            **{
                field.name: _normalize_value(getattr(value, field.name))
                for field in dataclasses.fields(value)
            },
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Mapping):
        return {
            str(key): _normalize_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (set, frozenset)):
        normalized = [_normalize_value(item) for item in value]
        return sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True))
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    return value


def _normalize_namespace(namespace: argparse.Namespace) -> dict[str, object]:
    normalized = {
        key: _normalize_value(value) for key, value in sorted(vars(namespace).items())
    }
    agents_file = Path(str(normalized["agents_file"])).resolve()
    if agents_file == (ROOT / "AGENTS.md").resolve():
        normalized["agents_file"] = "<ROOT>/AGENTS.md"
    return normalized


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_message(message: str, scenario_root: Path) -> str:
    normalized = message.replace(
        str(scenario_root.resolve()), "<SCENARIO_ROOT>"
    )
    return normalized.replace("<SCENARIO_ROOT>\\", "<SCENARIO_ROOT>/")


def _parser_error_message(stderr_text: str, scenario_root: Path) -> str:
    lines = stderr_text.rstrip().splitlines()
    final_line = lines[-1] if lines else ""
    _program, separator, message = final_line.partition(": error: ")
    assert separator, final_line
    return _normalize_message(message, scenario_root)


def _exception_chain(
    error: BaseException, scenario_root: Path
) -> list[dict[str, str]]:
    chain: list[dict[str, str]] = []
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(
            {
                "type": type(current).__name__,
                "message": _normalize_message(str(current), scenario_root),
            }
        )
        next_error = current.__cause__
        if next_error is None and not current.__suppress_context__:
            next_error = current.__context__
        current = next_error
    return chain


def _namespace_patch(base: object, actual: object) -> object:
    if isinstance(base, dict) and isinstance(actual, dict):
        return {
            key: _namespace_patch(base.get(key), value)
            for key, value in actual.items()
            if key not in base or base[key] != value
        }
    return actual


def _apply_namespace_patch(base: object, patch: object) -> object:
    if isinstance(base, dict) and isinstance(patch, dict):
        merged = copy.deepcopy(base)
        for key, value in patch.items():
            merged[key] = _apply_namespace_patch(merged.get(key), value)
        return merged
    return copy.deepcopy(patch)


RETURN_LINE_TO_ORDINAL = {
    node.lineno: ordinal
    for ordinal, node in enumerate(
        _ordered(
            _parse_args_function(ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))),
            ast.Return,
        ),
        1,
    )
}


def _run_scenario(
    module: ModuleType,
    spec: RejectionSpec | SuccessSpec,
    scenario_root: Path,
) -> tuple[dict[str, object], dict[str, object] | None]:
    assert not scenario_root.exists()
    scenario_root.mkdir(parents=True)
    process_starts = 0
    return_ordinals: list[int] = []
    exception_events: list[dict[str, object]] = []

    def forbidden_process(*_args: object, **_kwargs: object) -> None:
        nonlocal process_starts
        process_starts += 1
        raise AssertionError("B64 scenario attempted to start a process")

    def trace(frame: Any, event: str, argument: object) -> Any:
        if frame.f_code.co_name not in {"parse_args", "quota_int"}:
            return None
        if frame.f_code.co_filename != str(SOURCE_PATH):
            return None
        if event == "return" and frame.f_lineno in RETURN_LINE_TO_ORDINAL:
            return_ordinals.append(RETURN_LINE_TO_ORDINAL[frame.f_lineno])
        elif event == "exception":
            exception_type, exception, _traceback = argument  # type: ignore[misc]
            exception_events.append(
                {
                    "function": frame.f_code.co_name,
                    "line": frame.f_lineno,
                    "type": exception_type.__name__,
                    "message": _normalize_message(str(exception), scenario_root),
                }
            )
        return trace

    stderr = io.StringIO()
    namespace: dict[str, object] | None = None
    outcome: dict[str, object]
    previous_trace = sys.gettrace()
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(subprocess, "Popen", forbidden_process)
        monkeypatch.setattr(subprocess, "run", forbidden_process)
        sys.settrace(trace)
        try:
            with redirect_stderr(stderr):
                parsed = module.parse_args(
                    list(spec.argv),
                    cwd=scenario_root,
                    environ=dict(spec.environ),
                )
        except SystemExit as exc:
            stderr_text = stderr.getvalue()
            outcome = {
                "kind": "parser_error",
                "exit_code": exc.code,
                "stderr_message": _parser_error_message(
                    stderr_text, scenario_root
                ),
            }
        except Exception as exc:
            outcome = {
                "kind": "raise",
                "error_chain": _exception_chain(exc, scenario_root),
                "stderr": stderr.getvalue(),
            }
        else:
            namespace = _normalize_namespace(parsed)
            outcome = {
                "kind": "success",
                "namespace_field_count": len(namespace),
                "namespace_sha256": _canonical_digest(namespace),
                "return_ordinals": return_ordinals,
                "stderr": stderr.getvalue(),
            }
        finally:
            sys.settrace(previous_trace)

    assert process_starts == 0, spec.scenario_id
    assert tuple(scenario_root.rglob("*")) == (), spec.scenario_id
    document = {
        "scenario_id": spec.scenario_id,
        "description": spec.description,
        "arguments": list(spec.argv),
        "environment": dict(spec.environ),
        "source_kind": (
            spec.source_kind if isinstance(spec, RejectionSpec) else "return"
        ),
        "source_ordinal": (
            spec.source_ordinal
            if isinstance(spec, RejectionSpec)
            else spec.target_return_ordinal
        ),
        "catcher_ordinal": (
            spec.catcher_ordinal if isinstance(spec, RejectionSpec) else None
        ),
        "exception_events": exception_events,
        "process_starts": process_starts,
        "outcome": outcome,
    }
    return document, namespace


_CORPUS_BUILD_COUNT = 0


def _build_runtime_corpus(root: Path, module: ModuleType = cli) -> BuiltCorpus:
    global _CORPUS_BUILD_COUNT
    _CORPUS_BUILD_COUNT += 1
    started = time.perf_counter()
    rejection_documents = [
        _run_scenario(module, spec, root / spec.scenario_id)[0]
        for spec in REJECTION_SPECS
    ]
    success_runs = [
        _run_scenario(module, spec, root / spec.scenario_id)
        for spec in SUCCESS_SPECS
    ]
    success_namespaces = {
        spec.scenario_id: namespace
        for spec, (_document, namespace) in zip(
            SUCCESS_SPECS, success_runs, strict=True
        )
        if namespace is not None
    }
    base = success_namespaces["valid-default-arguments"]
    success_documents: list[dict[str, object]] = []
    for spec, (document, namespace) in zip(SUCCESS_SPECS, success_runs, strict=True):
        assert namespace is not None
        document["namespace_patch"] = _namespace_patch(base, namespace)
        success_documents.append(document)
    return BuiltCorpus(
        document={
            "schema_version": "cli-argument-evaluation-corpus-v1",
            "source_commit": SOURCE_COMMIT,
            "source_blob": SOURCE_BLOB,
            "namespace_base": base,
            "rejections": rejection_documents,
            "successes": success_documents,
        },
        success_namespaces=success_namespaces,
        build_seconds=time.perf_counter() - started,
    )


@pytest.fixture(scope="session")
def argument_evaluation_corpus(
    tmp_path_factory: pytest.TempPathFactory,
) -> BuiltCorpus:
    return _build_runtime_corpus(tmp_path_factory.mktemp("b64-argument-evaluation"))


def _scenario(
    document: dict[str, object], group: str, scenario_id: str
) -> dict[str, object]:
    scenarios = document[group]
    assert isinstance(scenarios, list)
    return next(item for item in scenarios if item["scenario_id"] == scenario_id)


def _assert_scenario_matches(
    actual: dict[str, object], expected: dict[str, object]
) -> None:
    if actual != expected:
        raise AssertionError(
            f"{expected['scenario_id']}: expected={expected!r}; actual={actual!r}"
        )


def _load_mutant_without_dry_run_dependency() -> ModuleType:
    tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
    function = _parse_args_function(tree)
    candidates = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.If)
        and ast.unparse(node.test)
        == "args.dry_run_scenario and (not args.dry_run)"
    ]
    assert len(candidates) == 1
    candidates[0].test = ast.Constant(value=False)
    ast.fix_missing_locations(tree)
    module = ModuleType("_b64_cli_mutant")
    module.__file__ = str(SOURCE_PATH)
    sys.modules[module.__name__] = module
    try:
        exec(compile(tree, str(SOURCE_PATH), "exec"), module.__dict__)
    finally:
        sys.modules.pop(module.__name__, None)
    return module


def test_static_parse_args_contract_is_complete_and_source_ordered() -> None:
    actual = _static_document()
    expected = _load_json(STATIC_BASELINE)
    assert actual == expected
    assert actual["function_span_lines"] == 202
    assert len(actual["conditions"]) == 30
    assert len(actual["raises"]) == 3
    assert len(actual["parser_errors"]) == 8
    assert len(actual["returns"]) == 4
    assert [item["classification"] for item in actual["catchers"]] == [
        "rethrows",
        "mitigates",
        "mitigates",
        "rethrows",
    ]
    assert actual["unreachable_rejections"] == []


def test_b64_anchor_and_b21_b23_b32_baselines_are_byte_identical() -> None:
    baseline = _load_json(STATIC_BASELINE)
    assert _git("rev-parse", f"{SOURCE_COMMIT}:src/cli.py") == SOURCE_BLOB
    assert _git("hash-object", str(SOURCE_PATH)) == SOURCE_BLOB
    assert _git("diff", "--", "src/cli.py") == ""
    guarded = {
        str(baseline["function_size_baseline_path"]): baseline[
            "function_size_baseline_blob"
        ],
        **baseline["protected_blobs"],
    }
    for path, blob in guarded.items():
        assert _git("rev-parse", f"{SOURCE_COMMIT}:{path}") == blob, path
        assert _git("hash-object", str(ROOT / path)) == blob, path


@pytest.mark.parametrize("scenario_id", [spec.scenario_id for spec in REJECTION_SPECS])
def test_each_rejection_matches_cleartext_exit_or_exception(
    argument_evaluation_corpus: BuiltCorpus,
    scenario_id: str,
) -> None:
    expected_document = _load_json(RUNTIME_BASELINE)
    actual = _scenario(argument_evaluation_corpus.document, "rejections", scenario_id)
    expected = _scenario(expected_document, "rejections", scenario_id)
    _assert_scenario_matches(actual, expected)


@pytest.mark.parametrize("scenario_id", [spec.scenario_id for spec in SUCCESS_SPECS])
def test_each_return_path_matches_the_complete_namespace(
    argument_evaluation_corpus: BuiltCorpus,
    scenario_id: str,
) -> None:
    expected_document = _load_json(RUNTIME_BASELINE)
    actual = _scenario(argument_evaluation_corpus.document, "successes", scenario_id)
    expected = _scenario(expected_document, "successes", scenario_id)
    _assert_scenario_matches(actual, expected)
    base = expected_document["namespace_base"]
    expected_namespace = _apply_namespace_patch(base, expected["namespace_patch"])
    actual_namespace = argument_evaluation_corpus.success_namespaces[scenario_id]
    assert actual_namespace == expected_namespace, scenario_id
    assert set(actual_namespace) == set(base), scenario_id
    assert len(actual_namespace) == actual["outcome"]["namespace_field_count"]
    assert _canonical_digest(actual_namespace) == actual["outcome"]["namespace_sha256"]
    assert actual["source_ordinal"] in actual["outcome"]["return_ordinals"]


def test_all_rejections_and_catchers_have_one_reachable_scenario() -> None:
    assert set(RAISE_SCENARIOS) == {1, 2, 3}
    assert set(PARSER_ERROR_SCENARIOS) == set(range(1, 9))
    assert set(CATCHER_SCENARIOS) == {1, 2, 3, 4}
    scenario_ids = [spec.scenario_id for spec in REJECTION_SPECS]
    assert len(scenario_ids) == len(set(scenario_ids)) == 11


def test_each_static_rejection_and_catcher_is_observed_at_runtime(
    argument_evaluation_corpus: BuiltCorpus,
) -> None:
    static = _load_json(STATIC_BASELINE)
    runtime = argument_evaluation_corpus.document

    for group in ("raises", "parser_errors"):
        for source in static[group]:
            scenario = _scenario(runtime, "rejections", source["scenario_id"])
            assert scenario["source_kind"] == (
                "raise" if group == "raises" else "parser_error"
            )
            assert scenario["source_ordinal"] == source["ordinal"]
            if group == "raises":
                assert any(
                    event["type"] == source["exception_type"]
                    and event["message"]
                    for event in scenario["exception_events"]
                ), source["scenario_id"]
            else:
                assert scenario["outcome"]["kind"] == "parser_error"
                assert scenario["outcome"]["exit_code"] == 2
                assert scenario["outcome"]["stderr_message"]

    for catcher in static["catchers"]:
        scenario = _scenario(runtime, "rejections", catcher["scenario_id"])
        assert scenario["catcher_ordinal"] == catcher["ordinal"]
        assert any(
            event["type"] == catcher["exception_type"]
            for event in scenario["exception_events"]
        ), catcher["scenario_id"]


def test_removing_a_flag_dependency_names_its_rejection_scenario(
    tmp_path: Path,
) -> None:
    spec = next(
        item
        for item in REJECTION_SPECS
        if item.scenario_id == "dry-run-scenario-without-dry-run"
    )
    expected = _scenario(
        _load_json(RUNTIME_BASELINE), "rejections", spec.scenario_id
    )
    control, _namespace = _run_scenario(cli, spec, tmp_path / "control")
    _assert_scenario_matches(control, expected)
    actual, _namespace = _run_scenario(
        _load_mutant_without_dry_run_dependency(), spec, tmp_path / "mutant"
    )
    with pytest.raises(AssertionError, match=spec.scenario_id):
        _assert_scenario_matches(actual, expected)


def test_argument_corpus_builds_exactly_once_without_processes_or_writes(
    argument_evaluation_corpus: BuiltCorpus,
) -> None:
    assert _CORPUS_BUILD_COUNT == 1
    assert 0 < argument_evaluation_corpus.build_seconds < 10
    scenarios = (
        argument_evaluation_corpus.document["rejections"]
        + argument_evaluation_corpus.document["successes"]
    )
    assert all(item["process_starts"] == 0 for item in scenarios)
