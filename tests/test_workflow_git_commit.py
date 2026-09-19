from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
import subprocess
from types import SimpleNamespace
from typing import Any, cast

import pytest

from artifact_bridge import ArtifactBridge, attestation_payload, review_payload
from artifact_models import (
    BindingPayload,
    FingerprintKind,
    RoleProfilePayload,
    RunIdentityPayload,
    RunProfilePayload,
    SideEffectPayload,
)
from artifact_replay import replay_artifacts
from artifact_store import ArtifactStore
from content_authority_support import (
    append_provider_decision_authority,
    append_validation_authority,
)
from contracts import (
    AgentRole, ContractResult, FindingClass, FindingOrigin, FindingRecord,
    FindingStatus,
    ReviewEvidence,
    ValidationAttestation,
    ValidationRecord,
    ValidationStatus,
)
from orchestrator import OrchestratorConfig, ProductionWorkflowDriver
from side_effects import (
    Reconciliation,
    ReconciliationOutcome,
    SideEffectBoundary,
    SideEffectBoundaryPhase,
    SideEffectExecutor,
    SideEffectSpec,
)
from workflow import (
    WorkflowCommitApprovalRequired,
    WorkflowCommitRequest,
    WorkflowExecutionError,
)
from workflow_git_commit import WorkflowGitCommit, WorkflowGitCommitDependencies
from workflow_state import ProtocolBinding, ProtocolMode, init_workflow_state


ROOT = Path(__file__).resolve().parents[1]
GIT_COMMIT_PATH = ROOT / "src/workflow_git_commit.py"
DRIVER_PATH = ROOT / "src/orchestrator.py"
SIDE_EFFECTS_PATH = ROOT / "src/side_effects.py"
DIGEST = "a" * 64

EXPECTED_INTERNAL_IMPORTS = {
    "artifact_bridge",
    "artifact_models",
    "artifact_replay",
    "git_service",
    "repo_changes",
    "side_effects",
    "slice_exit",
    "workflow",
    "workflow_state",
}

EXPECTED_DEPENDENCY_EDGES = {
    "active_state",
    "artifact_bridge",
    "assert_structured_decision_context",
    "bound_task_control_paths",
    "mark_completed_side_effect",
    "repository_changes",
    "root",
    "side_effect_executor",
    "side_effect_spec",
}

EXPECTED_DRIVER_BINDINGS = {
    "root": "lambda: self.root",
    "active_state": "lambda: self.active_state",
    "artifact_bridge": "lambda: self._artifact_bridge",
    "assert_structured_decision_context": (
        "self.assert_structured_decision_context"
    ),
    "repository_changes": (
        "lambda fingerprint: self._repository_changes.get(fingerprint)"
    ),
    "mark_completed_side_effect": "self._mark_completed_side_effect",
    "side_effect_executor": "self._side_effect_executor",
    "side_effect_spec": "self._side_effect_spec",
    "bound_task_control_paths": "_bound_task_control_paths",
}


def _tree(path: Path, source: str | None = None) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8") if source is None else source)


def _class(tree: ast.Module, name: str) -> ast.ClassDef:
    return next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name
    )


def _methods(path: Path, class_name: str) -> dict[str, ast.FunctionDef]:
    return {
        node.name: node
        for node in _class(_tree(path), class_name).body
        if isinstance(node, ast.FunctionDef)
    }


def _dependency_edges(source: str | None = None) -> set[str]:
    boundary = _class(_tree(GIT_COMMIT_PATH, source), "WorkflowGitCommit")
    return {
        node.attr
        for node in ast.walk(boundary)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "_dependencies"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "self"
    }


def _internal_imports(path: Path) -> set[str]:
    tree = _tree(path)
    known_modules = {candidate.stem for candidate in (ROOT / "src").glob("*.py")}
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            module = node.module.split(".", 1)[0]
            if module in known_modules:
                result.add(module)
        elif isinstance(node, ast.Import):
            result.update(
                module
                for alias in node.names
                if (module := alias.name.split(".", 1)[0]) in known_modules
            )
    return result


def _dependencies(root: Path) -> WorkflowGitCommitDependencies:
    def unavailable(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("a commit dependency ran before active-state validation")

    return WorkflowGitCommitDependencies(
        root=lambda: root,
        active_state=lambda: None,
        artifact_bridge=unavailable,
        assert_structured_decision_context=unavailable,
        repository_changes=unavailable,
        mark_completed_side_effect=unavailable,
        side_effect_executor=unavailable,
        side_effect_spec=unavailable,
        bound_task_control_paths=unavailable,
    )


def _bridge(root: Path, run_id: str) -> ArtifactBridge:
    root.mkdir(parents=True)
    store = ArtifactStore(root, run_id)
    bridge = ArtifactBridge(store, now=lambda: "2026-09-02T09:00:00+00:00")
    bridge.append(
        RunIdentityPayload(
            "inbox/backlog/commit.md",
            "feature/backlog-followups",
            "b" * 40,
            "b" * 40,
            "IMPLEMENT",
            None,
        ),
        logical_id="run-identity",
        idempotency_key="run-identity",
        fingerprint_sha256=DIGEST,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    bridge.append(
        RunProfilePayload(
            RoleProfilePayload("implementer-model", "medium"),
            RoleProfilePayload("reviewer-model", "high"),
        ),
        logical_id="run-profile",
        idempotency_key="run-profile",
        fingerprint_sha256=DIGEST,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    operation = ("structured-v2-side-effect-ledger",)
    bridge.record_side_effect_intent(
        effect_class="ledger",
        work_unit_id="run",
        operation=operation,
        fingerprint_sha256=DIGEST,
    )
    bridge.record_side_effect_result(
        effect_class="ledger",
        work_unit_id="run",
        operation=operation,
        result="initialized",
        fingerprint_sha256=DIGEST,
    )
    return ArtifactBridge(store)


def _git_records(bridge: ArtifactBridge) -> tuple[SideEffectPayload, ...]:
    return tuple(
        record.payload
        for record in bridge.store.load_chain()
        if isinstance(record.payload, SideEffectPayload)
        and record.payload.effect_class == "git_commit"
    )


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _repository(root: Path, branch: str) -> Path:
    repository = root / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", "master")
    _git(repository, "config", "user.name", "B30 Commit Test")
    _git(repository, "config", "user.email", "b30@example.invalid")
    (repository / "seed.txt").write_text("seed\n", encoding="utf-8")
    (repository / ".gitignore").write_text(".orchestrator/\n", encoding="utf-8")
    _git(repository, "add", "seed.txt", ".gitignore")
    _git(repository, "commit", "-m", "seed")
    _git(repository, "switch", "-c", branch)
    return repository


def _mutated_executor(source: str) -> type[SideEffectExecutor]:
    namespace: dict[str, Any] = {"__name__": "side_effects"}
    exec(compile(source, "<mutated-side-effects>", "exec"), namespace)
    return cast(type[SideEffectExecutor], namespace["SideEffectExecutor"])


def _assert_complete_git_pair(bridge: ArtifactBridge) -> None:
    records = _git_records(bridge)
    assert tuple(record.phase for record in records) == (
        "intent",
        "result",
    ), "Git effect must have one ordered intent/result pair"
    replay = replay_artifacts(bridge.store.load_chain(), bridge.store.run_id)
    git_effects = tuple(
        effect for effect in replay.side_effects if effect.effect_class == "git_commit"
    )
    assert len(git_effects) == 1 and git_effects[0].result is not None


def test_git_commit_module_has_complete_inventory_and_one_way_layering() -> None:
    assert _internal_imports(GIT_COMMIT_PATH) == EXPECTED_INTERNAL_IMPORTS
    assert "orchestrator" not in _internal_imports(GIT_COMMIT_PATH)

    importers = []
    for path in sorted((ROOT / "src").glob("*.py")):
        if path == GIT_COMMIT_PATH:
            continue
        if any(
            isinstance(node, ast.ImportFrom)
            and node.module == "workflow_git_commit"
            for node in ast.walk(_tree(path))
        ):
            importers.append(path.relative_to(ROOT).as_posix())
    assert importers == ["src/orchestrator.py"]
    assert _dependency_edges() == EXPECTED_DEPENDENCY_EDGES

    for lower_layer in (
        "workflow_persistence.py",
        "workflow_recovery.py",
        "workflow_baseline.py",
        "workflow_validation.py",
        "workflow_audit.py",
    ):
        assert "workflow_git_commit" not in _internal_imports(ROOT / "src" / lower_layer)


def test_omitted_commit_executor_edge_turns_inventory_red() -> None:
    source = GIT_COMMIT_PATH.read_text(encoding="utf-8")
    marker = "self._dependencies.side_effect_executor"
    assert marker in source
    mutated = source.replace(marker, "omitted_side_effect_executor")
    assert "side_effect_executor" not in _dependency_edges(mutated)
    assert _dependency_edges(mutated) != EXPECTED_DEPENDENCY_EDGES


def test_driver_binds_exact_edges_and_delegates_unchanged_public_surface() -> None:
    methods = _methods(DRIVER_PATH, "ProductionWorkflowDriver")
    boundary = methods["_git_commit_boundary"]
    dependency_call = next(
        node
        for node in ast.walk(boundary)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "WorkflowGitCommitDependencies"
    )
    actual = {
        keyword.arg: ast.unparse(keyword.value)
        for keyword in dependency_call.keywords
        if keyword.arg is not None
    }
    assert actual == EXPECTED_DRIVER_BINDINGS

    facade = methods["commit_slice"]
    assert [argument.arg for argument in facade.args.args] == ["self", "request"]
    assert ast.unparse(facade.body[0]) == (
        "return self._git_commit_boundary().commit_slice(request)"
    )

    owner = _methods(GIT_COMMIT_PATH, "WorkflowGitCommit")["commit_slice"]
    execute_calls = [
        node
        for node in ast.walk(owner)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
    ]
    assert len(execute_calls) == 1
    rendered = ast.dump(owner, include_attributes=False)
    assert "record_side_effect_intent" not in rendered
    assert "record_side_effect_result" not in rendered


def test_commit_without_driver_owned_active_state_fails_closed(tmp_path: Path) -> None:
    boundary = WorkflowGitCommit(_dependencies(tmp_path))
    with pytest.raises(WorkflowExecutionError, match="no active state"):
        boundary.commit_slice(cast(WorkflowCommitRequest, object()))


def test_git_commit_intent_result_bracket_and_open_intent_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path, "feature/b30-commit-bracket")
    start_commit = _git(repository, "rev-parse", "HEAD")
    changed_path = "runtime.py"
    (repository / changed_path).write_text("VALUE = 1\n", encoding="utf-8")
    run_id = "b30-module-commit"
    state = init_workflow_state(
        run_id=run_id,
        task_file="inbox/backlog/b30.md",
        branch="feature/b30-commit-bracket",
        branch_base=start_commit,
        first_slice_start_commit=start_commit,
        slice_count=1,
        task_digest=DIGEST,
        task_scope_patterns=(changed_path,),
        target_branch="feature/b30-commit-bracket",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
    ).bind_current_slice_git_boundary(
        start_commit=start_commit,
        scope_paths=(changed_path,),
        start_fingerprint=DIGEST,
    )

    def interrupt_before_result(boundary: SideEffectBoundary) -> None:
        if (
            boundary.effect_class == "git_commit"
            and boundary.phase is SideEffectBoundaryPhase.BEFORE_RESULT
        ):
            raise RuntimeError("injected crash before commit result")

    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator/state.json",
        agents={},
        config=OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
        side_effect_boundary_observer=interrupt_before_result,
    )
    driver.bind_work_unit(state)
    changes = driver.collect_changes(start_commit)
    bridge = driver._artifact_bridge
    assert bridge is not None
    monkeypatch.setattr(driver, "assert_structured_decision_context", lambda: None)

    attestation = ValidationAttestation(
        "validation-b30",
        changes.fingerprint,
        ("pytest",),
        (ValidationRecord(ValidationStatus.PASS, "pytest", 0, "passed"),),
        "b" * 64,
        "passed",
    )
    stored_attestation = append_validation_authority(
        bridge,
        attestation_payload(attestation, "ar1-" + "0" * 64),
        logical_id=attestation.attestation_id,
        idempotency_key="attestation:b30",
        fingerprint_sha256=changes.fingerprint,
    )
    attestation = replace(
        attestation,
        records=(ValidationRecord(ValidationStatus.PASS, "pytest", 0, "pass:0"),),
        output_digest=stored_attestation.payload.output_digest,
    )
    request_bound_review = ContractResult(
        reviewer=AgentRole.CLAUDE,
        approval=True,
        stopped=False,
        stop_request=None,
        validation=attestation,
        test_files=(),
        pre_mortem="The Git result record could be interrupted after commit.",
        evidence=ReviewEvidence(
            "commit authorization and ledger bracket",
            "post-commit result interruption",
            "resume repeats the physical commit",
        ),
        findings=(),
        anchors=(),
    )
    append_provider_decision_authority(
        bridge,
        review_payload(
            request_bound_review,
            work_unit_id=state.current_work_unit_id,
            transport_schema="native-claude-review-v2",
            request_id="native-review-request-" + "c" * 64,
            response_sha256="d" * 64,
        ),
        logical_id="review-claude-b30",
        idempotency_key="review:b30",
        fingerprint_sha256=changes.fingerprint,
        operation="claude_slice_review",
    )
    carried_finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.OBSERVATION,
        status=FindingStatus.CLOSED,
        summary="A finding outside the compact request remains in the ledger.",
        acceptance_test="The request-bound review still authorizes the commit.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
        status_rationale="Closed before this compact review.",
    )
    complete_review = replace(
        request_bound_review,
        findings=(carried_finding,),
    )
    request = WorkflowCommitRequest(
        slice_id=1,
        fingerprint=changes.fingerprint,
        attestation=attestation,
        claude_review=complete_review,
        findings=(carried_finding,),
    )

    with pytest.raises(RuntimeError, match="injected crash"):
        driver._git_commit_boundary().commit_slice(request)
    committed_head = _git(repository, "rev-parse", "HEAD")
    assert committed_head != start_commit
    assert _git(repository, "rev-list", "--count", f"{start_commit}..HEAD") == "1"
    assert tuple(record.phase for record in _git_records(bridge)) == ("intent",)

    resumed_driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator/state.json",
        agents={},
        config=OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    resumed_driver.active_state = state
    resumed_driver._artifact_bridge = ArtifactBridge(ArtifactStore(repository, run_id))
    resumed_driver._repository_changes[changes.fingerprint] = changes
    monkeypatch.setattr(
        resumed_driver, "assert_structured_decision_context", lambda: None
    )
    resumed_hash = resumed_driver._git_commit_boundary().commit_slice(request)

    assert resumed_hash == committed_head
    assert _git(repository, "rev-parse", "HEAD") == committed_head
    assert _git(repository, "rev-list", "--count", f"{start_commit}..HEAD") == "1"
    resumed_bridge = resumed_driver._artifact_bridge
    assert resumed_bridge is not None
    _assert_complete_git_pair(resumed_bridge)
    commit_bindings = tuple(
        record.payload
        for record in resumed_bridge.store.load_chain()
        if isinstance(record.payload, BindingPayload)
        and record.payload.binding_kind == "commit"
    )
    assert len(commit_bindings) == 1
    assert commit_bindings[0].target == committed_head


def test_head_drift_still_requires_exact_commit_approval(tmp_path: Path) -> None:
    repository = _repository(tmp_path, "feature/b30-head-drift")
    start_commit = _git(repository, "rev-parse", "HEAD")
    changed_path = "runtime.py"
    (repository / changed_path).write_text("VALUE = 1\n", encoding="utf-8")
    state = init_workflow_state(
        run_id="b30-head-drift",
        task_file="inbox/backlog/b30.md",
        branch="feature/b30-head-drift",
        branch_base=start_commit,
        first_slice_start_commit=start_commit,
        slice_count=1,
    ).bind_current_slice_git_boundary(
        start_commit=start_commit,
        scope_paths=(changed_path,),
        start_fingerprint=DIGEST,
    )
    driver = ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator/state.json",
        agents={},
        config=OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )
    driver.active_state = state
    changes = driver.collect_changes(start_commit)
    (repository / "drift.txt").write_text("drift\n", encoding="utf-8")
    _git(repository, "add", "drift.txt")
    _git(repository, "commit", "-m", "independent head drift")
    drifted_head = _git(repository, "rev-parse", "HEAD")
    request = cast(
        WorkflowCommitRequest,
        SimpleNamespace(slice_id=1, fingerprint=changes.fingerprint),
    )

    with pytest.raises(WorkflowCommitApprovalRequired, match="HEAD-DRIFT") as caught:
        driver._git_commit_boundary().commit_slice(request)

    assert caught.value.paths == changes.paths
    assert _git(repository, "rev-parse", "HEAD") == drifted_head


def test_omitted_commit_result_mutation_fails_closed(tmp_path: Path) -> None:
    source = SIDE_EFFECTS_PATH.read_text(encoding="utf-8")
    marker = "        self._complete(spec, result)\n        return value"
    assert source.count(marker) == 1
    mutated = source.replace(marker, "        _ = result\n        return value")
    executor = _mutated_executor(mutated)
    bridge = _bridge(tmp_path / "ledger", "omitted-result")
    operation = ("slice_commit", "1", "b" * 40, "c" * 40, DIGEST, "d" * 64)
    spec = SideEffectSpec("git_commit", "2", operation, DIGEST)

    result = executor(bridge).execute(
        spec,
        reconcile=lambda: Reconciliation(ReconciliationOutcome.NOT_OCCURRED),
        perform=lambda: ("e" * 40, "e" * 40),
    )

    assert result == "e" * 40
    with pytest.raises(AssertionError, match="intent/result pair"):
        _assert_complete_git_pair(bridge)


def test_swapped_commit_intent_result_mutation_fails_closed(tmp_path: Path) -> None:
    source = SIDE_EFFECTS_PATH.read_text(encoding="utf-8")
    marker = "created = self._record_intent(spec)"
    assert marker in source
    mutated = source.replace(
        marker,
        'self._complete(spec, "e" * 40)\n        created = self._record_intent(spec)',
        1,
    )
    executor = _mutated_executor(mutated)
    bridge = _bridge(tmp_path / "ledger", "swapped-pair")
    operation = ("slice_commit", "1", "b" * 40, "c" * 40, DIGEST, "d" * 64)
    spec = SideEffectSpec("git_commit", "2", operation, DIGEST)

    with pytest.raises(RuntimeError, match="no authoritative intent"):
        executor(bridge).execute(
            spec,
            reconcile=lambda: Reconciliation(ReconciliationOutcome.NOT_OCCURRED),
            perform=lambda: ("e" * 40, "e" * 40),
        )
