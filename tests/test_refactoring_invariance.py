from __future__ import annotations

import ast
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Mapping

import pytest

import orchestrator
from agent_runtime import NativeAgentCodexOutput, NativeAgentReviewOutput
from artifact_models import RecordType
from artifact_store import ArtifactStore
from cli import parse_args
from native_codex_contract import (
    canonical_native_codex_json,
    parse_bound_native_codex_contract_result,
)
from native_review_contract import (
    canonical_native_review_json,
    parse_bound_native_contract_result,
)
from orchestrator import ProductionWorkflowDriver, run_production_workflow
from workflow import CodexInvocation, ReviewerInvocation


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
RECORD_BASELINE = ROOT / "tests/fixtures/workflow-record-sequence-baseline-v1.json"
LAYER_EDGE_BASELINE = ROOT / "tests/fixtures/src-layer-edges-baseline-v1.json"

RECORD_BASELINE_SCHEMA = "workflow-record-sequence-baseline-v1"
LAYER_EDGE_BASELINE_SCHEMA = "src-layer-edges-baseline-v1"
LAYER_ANCHORS: Mapping[str, str] = {
    "audit": "ReviewAuditEvent",
    "driver": "ProductionWorkflowDriver",
    "engine": "WorkflowEngine",
    "git": "GitTransactionError",
    "recordstore": "ArtifactStore",
}


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_record_baseline(path: Path = RECORD_BASELINE) -> tuple[str, ...]:
    document = _load_json(path)
    assert isinstance(document, dict)
    assert set(document) == {"schema_version", "record_types"}
    assert document["schema_version"] == RECORD_BASELINE_SCHEMA
    entries = document["record_types"]
    assert isinstance(entries, list) and entries
    known_types = {item.value for item in RecordType}
    assert all(isinstance(item, str) and item in known_types for item in entries)
    return tuple(entries)


def _load_layer_edge_baseline(
    path: Path = LAYER_EDGE_BASELINE,
) -> frozenset[tuple[str, str]]:
    document = _load_json(path)
    assert isinstance(document, dict)
    assert set(document) == {"schema_version", "edges"}
    assert document["schema_version"] == LAYER_EDGE_BASELINE_SCHEMA
    entries = document["edges"]
    assert isinstance(entries, list)
    assert all(
        isinstance(item, dict)
        and set(item) == {"source", "target"}
        and item["source"] in LAYER_ANCHORS
        and item["target"] in LAYER_ANCHORS
        and item["source"] != item["target"]
        for item in entries
    )
    edges = tuple((item["source"], item["target"]) for item in entries)
    assert edges == tuple(sorted(set(edges)))
    return frozenset(edges)


def _record_sequence_mismatch(
    expected: tuple[str, ...], actual: tuple[str, ...]
) -> str | None:
    shared = min(len(expected), len(actual))
    for index in range(shared):
        if expected[index] != actual[index]:
            return (
                f"record {index + 1}: expected {expected[index]!r}, "
                f"got {actual[index]!r}"
            )
    if len(expected) != len(actual):
        index = shared + 1
        expected_value = expected[shared] if shared < len(expected) else "<end>"
        actual_value = actual[shared] if shared < len(actual) else "<end>"
        return (
            f"record {index}: expected {expected_value!r}, got {actual_value!r}; "
            f"expected {len(expected)} records, got {len(actual)}"
        )
    return None


def _assert_record_sequence(actual: tuple[str, ...]) -> None:
    mismatch = _record_sequence_mismatch(_load_record_baseline(), actual)
    assert mismatch is None, (
        f"workflow record sequence changed at {mismatch}; actual sequence={actual!r}"
    )


def _module_name(source_root: Path, path: Path) -> str:
    parts = list(path.relative_to(source_root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_internal_module(
    name: str, known_modules: frozenset[str]
) -> str | None:
    parts = name.split(".")
    for length in range(len(parts), 0, -1):
        candidate = ".".join(parts[:length])
        if candidate in known_modules:
            return candidate
    return None


def _import_targets(
    tree: ast.AST,
    *,
    current_module: str,
    known_modules: frozenset[str],
) -> frozenset[str]:
    targets: set[str] = set()
    current_package = current_module.split(".")[:-1]

    class ModuleImportVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.nodes: list[ast.Import | ast.ImportFrom] = []

        def visit_Import(self, node: ast.Import) -> None:
            self.nodes.append(node)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            self.nodes.append(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            _ = node

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            _ = node

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            _ = node

        def visit_Lambda(self, node: ast.Lambda) -> None:
            _ = node

    visitor = ModuleImportVisitor()
    visitor.visit(tree)
    for node in visitor.nodes:
        candidates: list[str] = []
        if isinstance(node, ast.Import):
            candidates.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                keep = max(0, len(current_package) - (node.level - 1))
                base_parts = current_package[:keep]
                if node.module:
                    base_parts.extend(node.module.split("."))
                base = ".".join(base_parts)
            else:
                base = node.module or ""
            if base:
                candidates.append(base)
            candidates.extend(
                f"{base}.{alias.name}" if base else alias.name
                for alias in node.names
                if alias.name != "*"
            )
        for candidate in candidates:
            resolved = _resolve_internal_module(candidate, known_modules)
            if resolved is not None and resolved != current_module:
                targets.add(resolved)
    return frozenset(targets)


def _src_import_graph(source_root: Path) -> dict[str, frozenset[str]]:
    paths = tuple(sorted(source_root.rglob("*.py")))
    modules = {_module_name(source_root, path): path for path in paths}
    modules.pop("", None)
    known_modules = frozenset(modules)
    graph: dict[str, frozenset[str]] = {}
    for module, path in sorted(modules.items()):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        graph[module] = _import_targets(
            tree, current_module=module, known_modules=known_modules
        )
    return graph


def _find_import_cycle(
    graph: Mapping[str, frozenset[str]],
) -> tuple[str, ...] | None:
    visited: set[str] = set()
    active: dict[str, int] = {}
    stack: list[str] = []

    def visit(module: str) -> tuple[str, ...] | None:
        active[module] = len(stack)
        stack.append(module)
        for dependency in sorted(graph[module]):
            if dependency in active:
                return tuple((*stack[active[dependency] :], dependency))
            if dependency not in visited:
                cycle = visit(dependency)
                if cycle is not None:
                    return cycle
        stack.pop()
        active.pop(module)
        visited.add(module)
        return None

    for module in sorted(graph):
        if module not in visited:
            cycle = visit(module)
            if cycle is not None:
                return cycle
    return None


def _assert_acyclic(graph: Mapping[str, frozenset[str]]) -> None:
    cycle = _find_import_cycle(graph)
    assert cycle is None, "src import cycle: " + " -> ".join(cycle or ())


def _top_level_declarations(path: Path) -> frozenset[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return frozenset(
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    )


def _layer_modules(source_root: Path) -> dict[str, str]:
    declarations = {
        _module_name(source_root, path): _top_level_declarations(path)
        for path in sorted(source_root.rglob("*.py"))
        if _module_name(source_root, path)
    }
    result: dict[str, str] = {}
    for layer, anchor in sorted(LAYER_ANCHORS.items()):
        matches = sorted(
            module for module, names in declarations.items() if anchor in names
        )
        assert len(matches) == 1, (
            f"layer {layer!r} anchor {anchor!r} must identify exactly one module, "
            f"got {matches!r}"
        )
        result[layer] = matches[0]
    return result


def _layer_edges(
    source_root: Path, graph: Mapping[str, frozenset[str]]
) -> frozenset[tuple[str, str]]:
    modules = _layer_modules(source_root)
    layer_by_module = {module: layer for layer, module in modules.items()}
    edges: set[tuple[str, str]] = set()
    for source_layer, source_module in modules.items():
        reachable: set[str] = set()
        pending = list(graph[source_module])
        while pending:
            target = pending.pop()
            if target in reachable:
                continue
            reachable.add(target)
            pending.extend(graph[target] - reachable)
        edges.update(
            (source_layer, layer_by_module[target])
            for target in reachable
            if target in layer_by_module and target != source_module
        )
    return frozenset(edges)


def _assert_layer_edge_ratchet(
    source_root: Path,
    graph: Mapping[str, frozenset[str]],
    *,
    baseline: frozenset[tuple[str, str]] | None = None,
) -> None:
    baseline = _load_layer_edge_baseline() if baseline is None else baseline
    unexpected = sorted(_layer_edges(source_root, graph) - baseline)
    assert not unexpected, "new src layer edges: " + ", ".join(
        f"{source} -> {target}" for source, target in unexpected
    )


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _native_plan_output(invocation: CodexInvocation) -> NativeAgentCodexOutput:
    bundle = invocation.native_request
    assert bundle is not None
    document = {
        "schema_version": "native-agent-codex-result-v2",
        "result_type": "plan_result",
        "request_id": bundle.bound_context.request_id,
        "ready": True,
        "finding_dispositions": [],
        "slice_plan": [
            {
                "slice_id": 1,
                "summary": "Implement the provider-free follow-up.",
                "scope_paths": ["docs/internal/work-plan.md"],
                "acceptance_criteria": [
                    {
                        "text": "The provider-free follow-up remains reproducible.",
                        "measured_against": "SOURCE",
                    }
                ],
            }
        ],
    }
    canonical = canonical_native_codex_json(document)
    return NativeAgentCodexOutput(
        result=parse_bound_native_codex_contract_result(
            document, bundle.bound_context
        ),
        canonical_json=canonical,
        request_id=bundle.bound_context.request_id,
        response_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def _native_review_approval(
    invocation: ReviewerInvocation,
) -> NativeAgentReviewOutput:
    bundle = invocation.native_request
    assert bundle is not None
    document = {
        "schema_version": "native-agent-review-result-v2",
        "result_type": "review_result",
        "request_id": bundle.bound_context.request_id,
        "reviewer": "claude",
        "decision": "approved",
        "new_findings": [],
        "status_changes": [],
        "anchors": [],
        "review_evidence": {
            "dimensions": "record order, contracts, failure paths, and resume",
            "largest_residual_risk": "a new durable transition",
            "break_condition": "the canonical record sequence changes",
        },
        "pre_mortem": "A moved persistence boundary drops a record.",
    }
    canonical = canonical_native_review_json(document)
    return NativeAgentReviewOutput(
        result=parse_bound_native_contract_result(document, bundle.bound_context),
        canonical_json=canonical,
        request_id=bundle.bound_context.request_id,
        context=bundle.bound_context.context,
    )


@pytest.fixture(scope="module")
def provider_free_record_types(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[str, ...]:
    run_root = tmp_path_factory.mktemp("record-journey")
    repository = run_root / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", "master")
    _git(repository, "config", "user.name", "Invariant Test")
    _git(repository, "config", "user.email", "invariant@example.invalid")
    (repository / "seed.txt").write_text("seed\n", encoding="utf-8")
    (repository / ".gitignore").write_text(".orchestrator/\n", encoding="utf-8")
    _git(repository, "add", "seed.txt", ".gitignore")
    _git(repository, "commit", "-m", "seed")
    _git(repository, "switch", "-c", "feature/invariant-journey")

    task = run_root / "task.md"
    task.write_text(
        "\n".join(
            (
                "ORCHESTRATOR_MODE: PLAN_ONLY",
                "WORK_PLAN_PATH: docs/internal/work-plan.md",
                "TARGET_BRANCH: feature/invariant-journey",
                "TASK_SCOPE: docs/internal/work-plan.md",
            )
        ),
        encoding="utf-8",
    )
    args = parse_args(
        [
            "--task-file",
            str(task),
            "--test-command",
            "python3 -c 'print(\"ok\")'",
            "--agent-output",
            "none",
            "--no-agent-live-stream",
            "--no-plan-gate",
        ],
        cwd=repository,
        environ={},
    )

    patch = pytest.MonkeyPatch()

    def codex(
        _driver: ProductionWorkflowDriver, invocation: CodexInvocation
    ) -> NativeAgentCodexOutput:
        plan = repository / "docs/internal/work-plan.md"
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text(
            "# Work plan\n\n### Slice 1 - Provider-free follow-up\n\n"
            "**Exakter Änderungspfad**\n\n- `src/future.py`\n\n"
            "#### \u0041kzeptanzkriterien\n\n- The behavior is covered.\n",
            encoding="utf-8",
        )
        output = _native_plan_output(invocation)
        _driver.last_codex_output = output.canonical_json
        return output

    patch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    patch.setattr(
        ProductionWorkflowDriver,
        "invoke_reviewer",
        lambda _driver, invocation: _native_review_approval(invocation),
    )
    patch.setattr(
        ProductionWorkflowDriver,
        "assert_structured_decision_context",
        lambda _driver: None,
    )
    patch.chdir(repository)
    try:
        result = run_production_workflow(task, args)
    finally:
        patch.undo()

    assert result.workflow_completed
    chain = ArtifactStore(repository, result.state.run_id).load_chain()
    assert chain
    assert all(
        record.predecessor_ids == (() if index == 0 else (chain[index - 1].record_id,))
        for index, record in enumerate(chain)
    )
    return tuple(record.record_type.value for record in chain)


def test_provider_free_journey_matches_canonical_record_sequence(
    provider_free_record_types: tuple[str, ...],
) -> None:
    _assert_record_sequence(provider_free_record_types)


@pytest.mark.parametrize(
    "mutation",
    (
        lambda values: values[:1] + values[2:],
        lambda values: values[:-1],
        lambda values: (*values, "task"),
        lambda values: (values[1], values[0], *values[2:]),
        lambda values: ("task", *values[1:]),
    ),
    ids=(
        "omitted-middle",
        "omitted-tail",
        "added-tail",
        "reordered",
        "changed-type",
    ),
)
def test_record_sequence_anchor_names_first_changed_position(
    mutation: Callable[[tuple[str, ...]], tuple[str, ...]],
) -> None:
    expected = ("run_identity", "workflow_event", "run_profile")
    mismatch = _record_sequence_mismatch(expected, mutation(expected))
    assert mismatch is not None
    assert re.match(r"record [1-4]: expected ", mismatch)


def test_src_import_graph_is_acyclic_and_layer_edges_do_not_grow() -> None:
    graph = _src_import_graph(SRC)
    _assert_acyclic(graph)
    _assert_layer_edge_ratchet(SRC, graph)


def test_import_cycle_failure_names_the_complete_path(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "a.py").write_text("from b import value\n", encoding="utf-8")
    (source / "b.py").write_text("from c import value\n", encoding="utf-8")
    (source / "c.py").write_text("from a import value\n", encoding="utf-8")

    with pytest.raises(AssertionError, match=r"a -> b -> c -> a"):
        _assert_acyclic(_src_import_graph(source))


def test_layer_edge_ratchet_rejects_growth_and_allows_shrinkage(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    documents = {
        "audit.py": "class ReviewAuditEvent:\n    pass\n",
        "driver.py": (
            "from audit import ReviewAuditEvent\n"
            "from engine import WorkflowEngine\n"
            "from git import GitTransactionError\n"
            "from recordstore import ArtifactStore\n"
            "class ProductionWorkflowDriver:\n    pass\n"
        ),
        "engine.py": (
            "from audit import ReviewAuditEvent\n"
            "class WorkflowEngine:\n    pass\n"
        ),
        "git.py": "class GitTransactionError:\n    pass\n",
        "recordstore.py": "class ArtifactStore:\n    pass\n",
    }
    for name, content in documents.items():
        (source / name).write_text(content, encoding="utf-8")
    baseline = _layer_edges(source, _src_import_graph(source))

    driver = source / "driver.py"
    (source / "driver_records.py").write_text(
        "from recordstore import ArtifactStore\n", encoding="utf-8"
    )
    driver.write_text(
        driver.read_text(encoding="utf-8").replace(
            "from recordstore import ArtifactStore\n",
            "from driver_records import ArtifactStore\n",
        ),
        encoding="utf-8",
    )
    extracted_graph = _src_import_graph(source)
    assert _layer_edges(source, extracted_graph) == baseline
    _assert_layer_edge_ratchet(source, extracted_graph, baseline=baseline)

    driver.write_text(
        driver.read_text(encoding="utf-8").replace(
            "from git import GitTransactionError\n", ""
        ),
        encoding="utf-8",
    )
    _assert_layer_edge_ratchet(
        source, _src_import_graph(source), baseline=baseline
    )

    engine = source / "engine.py"
    engine.write_text(
        "from recordstore import ArtifactStore\n"
        + engine.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    with pytest.raises(
        AssertionError, match=r"new src layer edges: engine -> recordstore"
    ):
        _assert_layer_edge_ratchet(
            source, _src_import_graph(source), baseline=baseline
        )


def test_real_module_move_preserves_both_invariance_baselines(
    tmp_path: Path, provider_free_record_types: tuple[str, ...]
) -> None:
    relocated_src = tmp_path / "src"
    shutil.copytree(SRC, relocated_src)
    (relocated_src / "workflow.py").rename(
        relocated_src / "relocated_engine.py"
    )
    for path in relocated_src.rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        content = re.sub(
            r"(?m)^from workflow import ",
            "from relocated_engine import ",
            content,
        )
        content = re.sub(
            r"(?m)^import workflow(?=\s*(?:#.*)?$)",
            "import relocated_engine",
            content,
        )
        path.write_text(content, encoding="utf-8")

    graph = _src_import_graph(relocated_src)
    _assert_acyclic(graph)
    _assert_layer_edge_ratchet(relocated_src, graph)
    _assert_record_sequence(provider_free_record_types)
