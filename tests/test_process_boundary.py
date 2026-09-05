from __future__ import annotations

import ast
from collections import deque
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

import agent_runtime
from agent_adapters import AgentOutputError
from agent_runtime import AgentProcessError, OrchestratorConfig, run_agent
from provider_input_budget import (
    PreparedProviderInput,
    ProviderInputBudgetPolicy,
    ProviderInputBudgetRule,
    ProviderInputComponent,
    default_provider_input_budget_policy,
)


ROOT = Path(__file__).resolve().parents[1]
PRE_CUT = ROOT / "tests/fixtures/process-boundary-pre-b59-v1.json"
CORPUS = ROOT / "tests/fixtures/process-boundary-corpus-v1.json"
AGENT_SOURCE = ROOT / "src/agent_runtime.py"


def _load_json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


PRE_CUT_DOCUMENT = _load_json(PRE_CUT)
CORPUS_DOCUMENT = _load_json(CORPUS)
SCENARIOS = tuple(CORPUS_DOCUMENT["scenarios"])
SCENARIO_IDS = tuple(case["scenario_id"] for case in SCENARIOS)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _run_agent_function(source: str) -> ast.FunctionDef:
    tree = ast.parse(source)
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "run_agent"
    ]
    assert len(functions) == 1
    return functions[0]


def _source_inventory(source: str) -> dict[str, object]:
    function = _run_agent_function(source)

    def ordered(node_type: type[ast.AST]) -> list[ast.AST]:
        return sorted(
            (node for node in ast.walk(function) if isinstance(node, node_type)),
            key=lambda node: (node.lineno, node.col_offset),
        )

    conditions = [
        {
            "line": node.lineno,
            "expression": ast.unparse(node.test),
            "read_names": sorted(
                {
                    name.id
                    for name in ast.walk(node.test)
                    if isinstance(name, ast.Name)
                }
            ),
        }
        for node in ordered(ast.If)
        if isinstance(node, ast.If)
    ]
    aborts = [
        {
            "line": node.lineno,
            "expression": None if node.exc is None else ast.unparse(node.exc),
        }
        for node in ordered(ast.Raise)
        if isinstance(node, ast.Raise)
    ]
    returns = [
        {
            "line": node.lineno,
            "expression": None if node.value is None else ast.unparse(node.value),
        }
        for node in ordered(ast.Return)
        if isinstance(node, ast.Return)
    ]
    catchers = [
        {
            "line": node.lineno,
            "type": None if node.type is None else ast.unparse(node.type),
        }
        for node in ordered(ast.ExceptHandler)
        if isinstance(node, ast.ExceptHandler)
    ]

    process_calls: list[dict[str, object]] = []
    for node in ordered(ast.Call):
        assert isinstance(node, ast.Call)
        target = node.func
        if not isinstance(target, ast.Attribute) or not isinstance(target.value, ast.Name):
            continue
        call = f"{target.value.id}.{target.attr}"
        if call in {"subprocess.Popen", "process.kill", "process.wait", "subprocess.run"}:
            process_calls.append({"line": node.lineno, "call": call})

    assert function.end_lineno is not None
    return {
        "function_span_lines": function.end_lineno - function.lineno + 1,
        "conditions": conditions,
        "aborts": aborts,
        "returns": returns,
        "catchers": catchers,
        "process_calls": process_calls,
    }


def _compile_run_agent_mutation(*, removed_call: str | None = None, drop_stderr: bool = False) -> Any:
    function = _run_agent_function(AGENT_SOURCE.read_text(encoding="utf-8"))

    class Mutation(ast.NodeTransformer):
        replacements = 0

        def visit_Expr(self, node: ast.Expr) -> ast.stmt:
            self.generic_visit(node)
            call = node.value
            if (
                removed_call is not None
                and isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "process"
                and call.func.attr == removed_call
            ):
                self.replacements += 1
                return ast.copy_location(ast.Pass(), node)
            return node

        def visit_Call(self, node: ast.Call) -> ast.expr:
            self.generic_visit(node)
            if (
                drop_stderr
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Constant)
                and node.func.value.value == ""
                and node.func.attr == "join"
                and len(node.args) == 1
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "stderr_chunks"
            ):
                self.replacements += 1
                return ast.copy_location(ast.Constant(value=""), node)
            return node

    mutation = Mutation()
    mutated = mutation.visit(function)
    assert isinstance(mutated, ast.FunctionDef)
    assert mutation.replacements == 1
    module = ast.fix_missing_locations(ast.Module(body=[mutated], type_ignores=[]))
    namespace = dict(vars(agent_runtime))
    exec(compile(module, str(AGENT_SOURCE), "exec"), namespace)
    return namespace["run_agent"]


def _limited_budget_policy(char_limit: int, byte_limit: int) -> ProviderInputBudgetPolicy:
    defaults = default_provider_input_budget_policy()
    return ProviderInputBudgetPolicy(
        tuple(
            ProviderInputBudgetRule(
                rule.provider,
                rule.role,
                rule.operation,
                char_limit
                if rule.key == ("codex", "codex", "codex_implementation")
                else rule.max_chars,
                byte_limit
                if rule.key == ("codex", "codex", "codex_implementation")
                else rule.max_bytes,
            )
            for rule in defaults.rules
        )
    )


@dataclass(frozen=True)
class ScenarioResult:
    output: str | None
    error_chain: tuple[tuple[str, str], ...]
    error_exit_code: int | None
    process_lifecycle: tuple[str, ...]
    open_processes: int
    stdout_bytes_hex: str
    stderr_bytes_hex: str
    validated_stderr_bytes_hex: str
    catcher_ids: tuple[str, ...]
    thread_errors: tuple[str, ...]
    cleanup_calls: int
    double_process_starts: int
    real_process_starts: int
    executed_source_lines: frozenset[int]


@dataclass(frozen=True)
class ProcessBoundaryCorpus:
    results: Mapping[str, ScenarioResult]
    build_elapsed_seconds: float


def _exception_chain(error: BaseException | None) -> tuple[tuple[str, str], ...]:
    chain: list[tuple[str, str]] = []
    current = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append((type(current).__name__, str(current)))
        current = current.__cause__
    return tuple(chain)


def _execute_scenario(
    case: Mapping[str, Any],
    root: Path,
    *,
    runner: Any = run_agent,
) -> ScenarioResult:
    scenario_id = str(case["scenario_id"])
    trigger = case["trigger"]
    lifecycle: list[str] = []
    open_processes: list[object] = []
    observed_stdout: list[str] = []
    observed_stderr: list[str] = []
    validated_stderr: list[str] = []
    thread_errors: list[str] = []
    cleanup_calls = 0
    double_process_starts = 0
    real_start_attempts: list[tuple[object, ...]] = []
    traced_lines: set[int] = set()

    stdout_chunks = list(trigger.get("stdout_chunks", []))
    stderr_chunks = list(trigger.get("stderr_chunks", []))
    returncode = int(trigger.get("returncode", 0))
    expected_command = ["provider-double", "--scenario", scenario_id]

    class FakeInput:
        def write(self, text: str) -> int:
            return len(text)

        def close(self) -> None:
            return None

    class FakeStream:
        def __init__(self, channel: str, chunks: list[str]) -> None:
            self.channel = channel
            self.chunks = deque(chunks)
            self.read_error = (
                trigger.get("stdout_read_error")
                if channel == "stdout"
                else trigger.get("stderr_read_error")
            )
            self.close_error = (
                trigger.get("stdout_close_error")
                if channel == "stdout"
                else trigger.get("stderr_close_error")
            )
            self.error_raised = False

        def readline(self) -> str:
            if self.chunks:
                chunk = self.chunks.popleft()
                (observed_stdout if self.channel == "stdout" else observed_stderr).append(
                    chunk
                )
                return chunk
            if self.read_error is not None and not self.error_raised:
                self.error_raised = True
                raise RuntimeError(str(self.read_error))
            return ""

        def close(self) -> None:
            if self.close_error is not None:
                raise RuntimeError(str(self.close_error))

    class FakeProcess:
        def __init__(self) -> None:
            self.stdin = FakeInput()
            self.stdout = FakeStream("stdout", stdout_chunks)
            self.stderr = FakeStream("stderr", stderr_chunks)
            self.returncode: int | None = None
            self.open = True
            open_processes.append(self)

        def wait(self, timeout: int) -> int:
            assert timeout == 5
            lifecycle.append("wait")
            self.returncode = returncode
            self.open = False
            return returncode

        def kill(self) -> None:
            lifecycle.append("kill")
            self.returncode = -9
            self.open = False

    class FakeCompleted:
        def __init__(self) -> None:
            self.returncode = returncode

        @property
        def stdout(self) -> str:
            text = "".join(stdout_chunks)
            observed_stdout[:] = [text]
            return text

        @property
        def stderr(self) -> str:
            text = "".join(stderr_chunks)
            observed_stderr[:] = [text]
            return text

    class FakeQueue:
        def __init__(self) -> None:
            self.items: deque[tuple[str, str | None]] = deque()
            self.empty_count = int(trigger.get("queue_empty_count", 0))

        @classmethod
        def __class_getitem__(cls, _item: object) -> type[FakeQueue]:
            return cls

        def put(self, item: tuple[str, str | None]) -> None:
            self.items.append(item)

        def get(self, *, timeout: float) -> tuple[str, str | None]:
            assert timeout == 0.2
            if self.empty_count:
                self.empty_count -= 1
                raise agent_runtime.queue.Empty
            assert self.items, f"{scenario_id}: fake queue unexpectedly empty"
            return self.items.popleft()

    class FakeThread:
        def __init__(
            self,
            *,
            target: Any,
            args: tuple[object, ...],
            daemon: bool,
        ) -> None:
            assert daemon is True
            self.target = target
            self.args = args

        def start(self) -> None:
            if trigger.get("stall_stream_threads"):
                return
            try:
                self.target(*self.args)
            except BaseException as error:  # a real worker also retains its failure locally
                thread_errors.append(f"{type(error).__name__}: {error}")

        def join(self, *, timeout: int) -> None:
            assert timeout == 1

    class FakeAdapter:
        def __init__(self) -> None:
            self.name = str(trigger.get("adapter_name", "codex"))
            self.timeout = 1
            self.reviewer = bool(trigger.get("reviewer", False))
            self.env: dict[str, str] = {}
            self.metadata: dict[str, object] = {}

        def validate_process_output(self, stderr: str) -> None:
            validated_stderr.append(stderr)
            if trigger.get("validate_error") is not None:
                raise AgentOutputError(str(trigger["validate_error"]))

        def extract_output(
            self, stdout: str, stderr: str, extra_files: dict[str, str]
        ) -> str:
            _ = (stderr, extra_files)
            if trigger.get("extract_error") is not None:
                raise AgentOutputError(str(trigger["extract_error"]))
            return stdout

        def cleanup(self) -> None:
            nonlocal cleanup_calls
            cleanup_calls += 1
            if trigger.get("cleanup_error") is not None:
                raise RuntimeError(str(trigger["cleanup_error"]))

        def bind_reviewer_workspace(self, source: Path, snapshot: Path) -> None:
            _ = (source, snapshot)

    def fake_popen(command: list[str], **_kwargs: object) -> FakeProcess:
        nonlocal double_process_starts
        assert command == expected_command
        double_process_starts += 1
        lifecycle.append("Popen")
        return FakeProcess()

    def fake_run(command: list[str], **_kwargs: object) -> FakeCompleted:
        nonlocal double_process_starts
        assert command == expected_command
        double_process_starts += 1
        lifecycle.append("run")
        return FakeCompleted()

    ticks = iter((0.0, 0.0, 2.0))

    def monotonic() -> float:
        if trigger.get("stall_stream_threads"):
            return next(ticks)
        return 0.0

    agent_file = runner.__code__.co_filename

    def trace(frame: Any, event: str, _arg: object) -> Any:
        if event == "line" and frame.f_code.co_filename == agent_file:
            traced_lines.add(frame.f_lineno)
        return trace

    adapter = FakeAdapter()
    scenario_root = root / scenario_id
    if not trigger.get("missing_execution_root"):
        scenario_root.mkdir(parents=True, exist_ok=True)
    prompt = str(trigger.get("prompt", "prozessgrenze"))
    prepared = PreparedProviderInput(
        tuple(expected_command),
        prompt,
        (ProviderInputComponent("stdin_prompt", prompt),),
    )
    policy = (
        _limited_budget_policy(
            int(trigger["budget_chars"]), int(trigger["budget_bytes"])
        )
        if trigger.get("budget_chars") is not None
        else default_provider_input_budget_policy()
    )
    config = OrchestratorConfig(
        dry_run=trigger.get("mode") == "dry-run",
        agent_live_stream=trigger.get("mode") == "live",
        agent_live_stream_mode="full",
        repo_root=scenario_root,
        provider_input_budget=policy,
    )
    execution_root_override = (
        scenario_root if trigger.get("execution_root_override") == "existing" else None
    )

    monkeypatch = pytest.MonkeyPatch()
    original_trace = sys.gettrace()
    caught: BaseException | None = None
    output: str | None = None
    runtime_globals = runner.__globals__

    def forbid_real_start(*args: object, **_kwargs: object) -> None:
        real_start_attempts.append(args)
        raise AssertionError(f"{scenario_id}: attempted a real provider process start")

    try:
        monkeypatch.setattr(subprocess, "Popen", forbid_real_start)
        monkeypatch.setattr(subprocess, "run", forbid_real_start)
        monkeypatch.setitem(
            runtime_globals,
            "subprocess",
            SimpleNamespace(
                Popen=fake_popen,
                run=fake_run,
                PIPE=object(),
                TimeoutExpired=subprocess.TimeoutExpired,
            ),
        )
        monkeypatch.setitem(
            runtime_globals,
            "threading",
            SimpleNamespace(Thread=FakeThread),
        )
        import queue as stdlib_queue

        monkeypatch.setitem(
            runtime_globals,
            "queue",
            SimpleNamespace(Queue=FakeQueue, Empty=stdlib_queue.Empty),
        )
        monkeypatch.setitem(
            runtime_globals,
            "time",
            SimpleNamespace(monotonic=monotonic),
        )
        monkeypatch.setitem(
            runtime_globals,
            "verify_agent_capabilities",
            lambda *_args, **_kwargs: None,
        )
        if trigger.get("register_without_operation"):
            monkeypatch.setitem(
                runtime_globals,
                "PROVIDER_OPERATIONS",
                {**runtime_globals["PROVIDER_OPERATIONS"], adapter.name: frozenset()},
            )
        sys.settrace(trace)
        try:
            output = runner(
                adapter,  # type: ignore[arg-type]
                prompt,
                config=config,
                shorten=lambda text, limit: (text or "")[:limit],
                prepared_provider_input=prepared,
                execution_root_override=execution_root_override,
            )
        except BaseException as error:
            caught = error
    finally:
        sys.settrace(original_trace)
        monkeypatch.undo()

    catcher_ids = tuple(
        item["catcher_id"]
        for item in PRE_CUT_DOCUMENT["catchers"]
        if traced_lines.intersection(item["body_lines"])
    )
    return ScenarioResult(
        output=output,
        error_chain=_exception_chain(caught),
        error_exit_code=getattr(caught, "exit_code", None),
        process_lifecycle=tuple(lifecycle),
        open_processes=sum(
            bool(getattr(process, "open", False)) for process in open_processes
        ),
        stdout_bytes_hex="".join(observed_stdout).encode("utf-8").hex(),
        stderr_bytes_hex="".join(observed_stderr).encode("utf-8").hex(),
        validated_stderr_bytes_hex="".join(validated_stderr).encode("utf-8").hex(),
        catcher_ids=catcher_ids,
        thread_errors=tuple(thread_errors),
        cleanup_calls=cleanup_calls,
        double_process_starts=double_process_starts,
        real_process_starts=len(real_start_attempts),
        executed_source_lines=frozenset(traced_lines),
    )


_CORPUS_BUILD_COUNT = 0


@pytest.fixture(scope="session")
def process_boundary_corpus(tmp_path_factory: pytest.TempPathFactory) -> ProcessBoundaryCorpus:
    global _CORPUS_BUILD_COUNT
    _CORPUS_BUILD_COUNT += 1
    started = perf_counter()
    root = tmp_path_factory.mktemp("process-boundary-b59")
    results = {
        case["scenario_id"]: _execute_scenario(case, root) for case in SCENARIOS
    }
    return ProcessBoundaryCorpus(results, perf_counter() - started)


def _assert_scenario(case: Mapping[str, Any], actual: ScenarioResult) -> None:
    scenario_id = str(case["scenario_id"])
    expected = case["expected"]
    failures: list[str] = []

    expected_chain = tuple(
        (item["type"], item["message"]) for item in expected["error_chain"]
    )
    comparisons = {
        "output": (expected["output"], actual.output),
        "error_chain": (expected_chain, actual.error_chain),
        "process_lifecycle": (
            tuple(expected["process_lifecycle"]),
            actual.process_lifecycle,
        ),
        "open_processes": (expected["open_processes"], actual.open_processes),
        "stdout_bytes_hex": (expected["stdout_bytes_hex"], actual.stdout_bytes_hex),
        "stderr_bytes_hex": (expected["stderr_bytes_hex"], actual.stderr_bytes_hex),
        "validated_stderr_bytes_hex": (
            expected["validated_stderr_bytes_hex"],
            actual.validated_stderr_bytes_hex,
        ),
        "catcher_ids": (tuple(expected["catcher_ids"]), actual.catcher_ids),
        "thread_errors": (tuple(expected["thread_errors"]), actual.thread_errors),
        "cleanup_calls": (expected["cleanup_calls"], actual.cleanup_calls),
        "double_process_starts": (
            expected["double_process_starts"],
            actual.double_process_starts,
        ),
        "real_process_starts": (
            expected["real_process_starts"],
            actual.real_process_starts,
        ),
    }
    if "error_exit_code" in expected:
        comparisons["error_exit_code"] = (
            expected["error_exit_code"],
            actual.error_exit_code,
        )
    for field, (wanted, observed) in comparisons.items():
        if wanted != observed:
            if field == "process_lifecycle":
                missing = [call for call in wanted if call not in observed]
                failures.append(
                    f"{field} expected={wanted!r} actual={observed!r} missing={missing!r}"
                )
            else:
                failures.append(f"{field} expected={wanted!r} actual={observed!r}")
    assert not failures, f"{scenario_id}: " + "; ".join(failures)


def test_b59_anchor_binds_the_byte_identical_pre_cut_source_and_static_inventory() -> None:
    assert PRE_CUT_DOCUMENT["schema_version"] == "process-boundary-pre-b59-v1"
    assert PRE_CUT_DOCUMENT["source_commit"] == (
        "610fc3170788f641a074a5c3ed1a3a623d472b67"
    )
    assert PRE_CUT_DOCUMENT["source_blob"] == (
        "1f8914ae1e28ef0a3d981013e8731ec3a2e46fcd"
    )
    assert (
        _git(
            "rev-parse",
            f"{PRE_CUT_DOCUMENT['source_commit']}:{PRE_CUT_DOCUMENT['source_path']}",
        )
        == PRE_CUT_DOCUMENT["source_blob"]
    )
    assert _git("hash-object", str(AGENT_SOURCE)) == PRE_CUT_DOCUMENT["source_blob"]
    assert _git("diff", "--", PRE_CUT_DOCUMENT["source_path"]) == ""

    source = _git(
        "show",
        f"{PRE_CUT_DOCUMENT['source_commit']}:{PRE_CUT_DOCUMENT['source_path']}",
    )
    inventory = _source_inventory(source)
    assert inventory["function_span_lines"] == PRE_CUT_DOCUMENT["function_span_lines"]
    assert inventory["conditions"] == PRE_CUT_DOCUMENT["conditions"]
    assert inventory["aborts"] == [
        {"line": item["line"], "expression": item["expression"]}
        for item in PRE_CUT_DOCUMENT["aborts"]
    ]
    assert inventory["returns"] == [
        {"line": item["line"], "expression": item["expression"]}
        for item in PRE_CUT_DOCUMENT["returns"]
    ]
    assert inventory["catchers"] == [
        {"line": item["line"], "type": item["type"]}
        for item in PRE_CUT_DOCUMENT["catchers"]
    ]
    assert inventory["process_calls"] == PRE_CUT_DOCUMENT["process_calls"]
    assert len(PRE_CUT_DOCUMENT["conditions"]) == PRE_CUT_DOCUMENT["condition_count"]
    assert len(PRE_CUT_DOCUMENT["aborts"]) == PRE_CUT_DOCUMENT["abort_count"]
    assert len(PRE_CUT_DOCUMENT["returns"]) == PRE_CUT_DOCUMENT["return_count"]
    assert len(PRE_CUT_DOCUMENT["catchers"]) == PRE_CUT_DOCUMENT["catcher_count"]


def test_every_abort_is_reachable_and_bound_to_its_exact_type_and_message() -> None:
    assert PRE_CUT_DOCUMENT["unreachable_aborts"] == []
    cases = {case["scenario_id"]: case for case in SCENARIOS}
    assert len(PRE_CUT_DOCUMENT["aborts"]) == PRE_CUT_DOCUMENT["abort_count"]
    for abort in PRE_CUT_DOCUMENT["aborts"]:
        scenario = cases[abort["scenario_id"]]
        error = scenario["expected"]["error_chain"][abort["error_chain_index"]]
        assert error == {
            "type": abort["exception_type"],
            "message": abort["message"],
        }, abort["scenario_id"]


def test_corpus_is_cleartext_complete_and_covers_required_lifecycle_paths() -> None:
    assert CORPUS_DOCUMENT["schema_version"] == "process-boundary-corpus-v1"
    assert CORPUS_DOCUMENT["pre_cut"] == PRE_CUT.relative_to(ROOT).as_posix()
    assert len(SCENARIO_IDS) == len(set(SCENARIO_IDS))
    required = {
        "normal-live",
        "timeout-live",
        "agent-output-error-run",
        "queue-empty-live",
        "stream-read-abort-live",
    }
    assert required <= set(SCENARIO_IDS)
    assert all(case["description"].strip() for case in SCENARIOS)
    assert all("trigger" in case and "expected" in case for case in SCENARIOS)
    assert {
        catcher["scenario_id"] for catcher in PRE_CUT_DOCUMENT["catchers"]
    } <= set(SCENARIO_IDS)


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_each_process_boundary_scenario_matches_lifecycle_and_complete_output(
    scenario_id: str,
    process_boundary_corpus: ProcessBoundaryCorpus,
) -> None:
    case = next(case for case in SCENARIOS if case["scenario_id"] == scenario_id)
    _assert_scenario(case, process_boundary_corpus.results[scenario_id])


def test_every_scenario_ends_without_an_open_or_real_process(
    process_boundary_corpus: ProcessBoundaryCorpus,
) -> None:
    failures = [
        scenario_id
        for scenario_id, result in process_boundary_corpus.results.items()
        if result.open_processes != 0 or result.real_process_starts != 0
    ]
    assert not failures, "open or real provider process: " + ", ".join(failures)


def test_all_five_catchers_execute_in_their_bound_scenarios(
    process_boundary_corpus: ProcessBoundaryCorpus,
) -> None:
    for catcher in PRE_CUT_DOCUMENT["catchers"]:
        result = process_boundary_corpus.results[catcher["scenario_id"]]
        assert catcher["catcher_id"] in result.catcher_ids, catcher["catcher_id"]


def test_both_return_sites_execute_in_their_bound_scenarios(
    process_boundary_corpus: ProcessBoundaryCorpus,
) -> None:
    for return_site in PRE_CUT_DOCUMENT["returns"]:
        assert any(
            return_site["line"]
            in process_boundary_corpus.results[scenario_id].executed_source_lines
            for scenario_id in return_site["scenario_ids"]
        ), return_site["line"]


@pytest.mark.parametrize(
    ("scenario_id", "removed_call"),
    (("timeout-live", "kill"), ("stream-read-abort-live", "wait")),
)
def test_removed_error_path_cleanup_call_names_the_scenario_and_call(
    scenario_id: str,
    removed_call: str,
    tmp_path: Path,
) -> None:
    case = next(case for case in SCENARIOS if case["scenario_id"] == scenario_id)
    mutated = _execute_scenario(
        case,
        tmp_path,
        runner=_compile_run_agent_mutation(removed_call=removed_call),
    )
    with pytest.raises(AssertionError) as captured:
        _assert_scenario(case, mutated)
    message = str(captured.value)
    assert scenario_id in message and removed_call in message


def test_dropping_live_stderr_join_turns_the_consumer_boundary_red(
    tmp_path: Path,
) -> None:
    scenario_id = "normal-live"
    case = next(case for case in SCENARIOS if case["scenario_id"] == scenario_id)
    mutated = _execute_scenario(
        case,
        tmp_path,
        runner=_compile_run_agent_mutation(drop_stderr=True),
    )
    with pytest.raises(AssertionError) as captured:
        _assert_scenario(case, mutated)
    message = str(captured.value)
    assert scenario_id in message and "validated_stderr_bytes_hex" in message


def test_process_boundary_corpus_builds_once_per_session_and_is_fast(
    process_boundary_corpus: ProcessBoundaryCorpus,
) -> None:
    assert _CORPUS_BUILD_COUNT == 1
    assert set(process_boundary_corpus.results) == set(SCENARIO_IDS)
    assert 0 <= process_boundary_corpus.build_elapsed_seconds < 10


def test_b59_keeps_b21_b23_b25_and_b32_guards_and_baselines_byte_identical() -> None:
    for path, blob in PRE_CUT_DOCUMENT["protected_blobs"].items():
        assert _git("hash-object", str(ROOT / path)) == blob, path
        assert (
            _git("rev-parse", f"{PRE_CUT_DOCUMENT['source_commit']}:{path}") == blob
        ), path
    assert _git("status", "--porcelain", "--", ".orchestrator") == ""
