from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
from types import SimpleNamespace

import inbox_watcher
import pytest

from artifact_bridge import ArtifactBridge, provider_input_measurement_payload
from artifact_resume import ArtifactResumeError, require_side_effect_ledger_prefix
from artifact_models import (
    ArtifactRecord,
    ArtifactValidationError,
    Fingerprint,
    FingerprintKind,
    RoleProfilePayload,
    RunIdentityPayload,
    RunProfilePayload,
    SideEffectPayload,
    stable_side_effect_key,
)
from artifact_replay import ArtifactReplayError, replay_artifacts
from artifact_store import ArtifactStore
from git_service import inspect_repository, preview_commit_tree
from inbox_watcher import (
    QueueFinalizationDisposition,
    WatchTaskIdentity,
    finalize_queue_success,
    move_poison_to_outbox_recoverably,
    move_to_outbox_with_ledger,
    save_watch_identity,
)
from orchestrator import ProductionWorkflowDriver
from provider_input_budget import ProviderInputComponentSize, ProviderInputMeasurement
from repo_changes import collect_repository_changes
from side_effects import (
    encode_file_write_content,
    ReconciliationOutcome,
    SideEffectExecutor,
    SideEffectReconciliationError,
    SideEffectSpec,
    reconcile_file_write,
    reconcile_git_commit,
    reconcile_provider_start,
    reconcile_queue_move,
    sha256_bytes,
)
from workflow import WorkflowExecutionError
from workflow_state import WorkflowStep


DIGEST = "a" * 64


def _bridge(tmp_path: Path, run_id: str = "side-effect-run") -> ArtifactBridge:
    store = ArtifactStore(tmp_path, run_id)
    bridge = ArtifactBridge(store, now=lambda: "2026-08-30T09:00:00+00:00")
    bridge.append(
        RunIdentityPayload(
            "inbox/backlog/side-effect.md",
            "feature/side-effect-ledger",
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
    return ArtifactBridge(store)


def _spec(effect_class: str, operation: tuple[str, ...]) -> SideEffectSpec:
    return SideEffectSpec(effect_class, "2", operation, DIGEST)


def _initialize_ledger(bridge: ArtifactBridge, fingerprint: str = DIGEST) -> None:
    operation = ("structured-v2-side-effect-ledger",)
    bridge.record_side_effect_intent(
        effect_class="ledger",
        work_unit_id="run",
        operation=operation,
        fingerprint_sha256=fingerprint,
    )
    bridge.record_side_effect_result(
        effect_class="ledger",
        work_unit_id="run",
        operation=operation,
        result="initialized",
        fingerprint_sha256=fingerprint,
    )


def test_side_effect_key_is_bound_to_immutable_operation() -> None:
    operation = ("result.json", "b" * 64)
    with pytest.raises(ArtifactValidationError, match="key differs"):
        SideEffectPayload(
            "side-effect:file_write:" + "0" * 32,
            "file_write",
            "2",
            operation,
            "intent",
            None,
        )
    assert stable_side_effect_key("file_write", "2", operation).startswith(
        "side-effect:file_write:"
    )
    assert stable_side_effect_key("internal", "2", ("same-marker",)) != (
        stable_side_effect_key("internal", "5", ("same-marker",))
    )
    assert stable_side_effect_key(
        "queue_move", "queue", ("source", "old-target", "c" * 64)
    ) == stable_side_effect_key(
        "queue_move", "queue", ("source", "new-target", "c" * 64)
    )


def test_internal_marker_is_distinct_per_work_unit_but_projects_legacy_marker(
    tmp_path: Path,
) -> None:
    bridge = _bridge(tmp_path)
    _initialize_ledger(bridge)
    for unit in ("2", "5"):
        bridge.record_side_effect_intent(
            effect_class="internal",
            work_unit_id=unit,
            operation=("agent-sandbox-validation-handoff",),
            fingerprint_sha256=DIGEST,
        )
        bridge.record_side_effect_result(
            effect_class="internal",
            work_unit_id=unit,
            operation=("agent-sandbox-validation-handoff",),
            result="completed",
            fingerprint_sha256=DIGEST,
        )
    replay = replay_artifacts(bridge.store.load_chain(), bridge.store.run_id)
    internal = tuple(item for item in replay.side_effects if item.effect_class == "internal")
    assert len({item.effect_key for item in internal}) == 2
    assert replay.completed_side_effects("2") == (
        "agent-sandbox-validation-handoff",
    )
    assert replay.completed_side_effects("5") == (
        "agent-sandbox-validation-handoff",
    )


def test_replay_rejects_result_without_intent(tmp_path: Path) -> None:
    operation = ("result.json", "b" * 64)
    key = stable_side_effect_key("file_write", "2", operation)
    payload = SideEffectPayload(
        key, "file_write", "2", operation, "result", "b" * 64
    )
    bridge = _bridge(tmp_path)
    record = bridge.append(
        payload,
        logical_id="side-effect-" + hashlib.sha256(key.encode()).hexdigest()[:32],
        idempotency_key="orphan-side-effect-result",
        fingerprint_sha256=DIGEST,
    )
    with pytest.raises(ArtifactReplayError, match="begin with revision 1 intent"):
        replay_artifacts(bridge.store.load_chain(), "side-effect-run")


def test_bridge_rejects_result_without_authoritative_intent(tmp_path: Path) -> None:
    bridge = _bridge(tmp_path)
    with pytest.raises(RuntimeError, match="no authoritative intent"):
        bridge.record_side_effect_result(
            effect_class="file_write",
            work_unit_id="2",
            operation=("result.json", "b" * 64),
            result="b" * 64,
            fingerprint_sha256=DIGEST,
        )


def test_chain_without_initialized_ledger_is_rejected(tmp_path: Path) -> None:
    bridge = _bridge(tmp_path)
    bridge.record_side_effect_intent(
        effect_class="file_write",
        work_unit_id="2",
        operation=("result.json", "b" * 64),
        fingerprint_sha256=DIGEST,
    )
    replay = replay_artifacts(bridge.store.load_chain(), bridge.store.run_id)
    with pytest.raises(ArtifactResumeError, match="no unique initialized"):
        require_side_effect_ledger_prefix(replay)


def test_file_intent_crash_executes_only_when_absence_proves_not_occurred(
    tmp_path: Path,
) -> None:
    bridge = _bridge(tmp_path)
    target = tmp_path / "result.json"
    content = b"expected"
    spec = _spec("file_write", ("result.json", sha256_bytes(content)))
    bridge.record_side_effect_intent(
        effect_class=spec.effect_class,
        work_unit_id=spec.work_unit_id,
        operation=spec.operation,
        fingerprint_sha256=spec.fingerprint_sha256,
    )
    calls = 0

    def perform() -> tuple[str, str]:
        nonlocal calls
        calls += 1
        target.write_bytes(content)
        return "written", sha256_bytes(content)

    executor = SideEffectExecutor(bridge)
    assert executor.execute(
        spec,
        reconcile=lambda: reconcile_file_write(target, sha256_bytes(content)),
        perform=perform,
    ) == "written"
    assert executor.execute(
        spec,
        reconcile=lambda: reconcile_file_write(target, sha256_bytes(content)),
        perform=perform,
    ) == sha256_bytes(content)
    assert calls == 1
    assert len(replay_artifacts(bridge.store.load_chain(), bridge.store.run_id).side_effects) == 1


def test_resume_allows_proven_absent_file_intent_to_reenter_its_writer(
    tmp_path: Path,
) -> None:
    bridge = _bridge(tmp_path)
    _initialize_ledger(bridge)
    target = tmp_path / "checkpoint.json"
    content = b"checkpoint"
    spec = _spec("file_write", ("checkpoint.json", sha256_bytes(content)))
    bridge.record_side_effect_intent(
        effect_class=spec.effect_class,
        work_unit_id=spec.work_unit_id,
        operation=spec.operation,
        fingerprint_sha256=spec.fingerprint_sha256,
    )
    driver = object.__new__(ProductionWorkflowDriver)
    driver.root = tmp_path
    driver._artifact_bridge = bridge

    driver._reconcile_pending_side_effects(
        SimpleNamespace(run_id=bridge.store.run_id)
    )
    assert not target.exists()

    SideEffectExecutor(bridge).execute(
        spec,
        reconcile=lambda: reconcile_file_write(target, sha256_bytes(content)),
        perform=lambda: (target.write_bytes(content), sha256_bytes(content)),
    )
    replay = replay_artifacts(bridge.store.load_chain(), bridge.store.run_id)
    assert next(item for item in replay.side_effects if item.effect_key == spec.effect_key).result == sha256_bytes(content)


def test_resume_replays_overwriting_projection_from_intent_bytes(
    tmp_path: Path,
) -> None:
    bridge = _bridge(tmp_path)
    _initialize_ledger(bridge)
    target = tmp_path / "state.json"
    previous = b'{"updated_at":"before"}\n'
    expected = b'{"updated_at":"intent-time"}\n'
    target.write_bytes(previous)
    operation = (
        "state.json",
        sha256_bytes(expected),
        sha256_bytes(previous),
        encode_file_write_content(expected),
    )
    spec = _spec("file_write", operation)
    bridge.record_side_effect_intent(
        effect_class=spec.effect_class,
        work_unit_id=spec.work_unit_id,
        operation=spec.operation,
        fingerprint_sha256=spec.fingerprint_sha256,
    )
    driver = object.__new__(ProductionWorkflowDriver)
    driver.root = tmp_path
    driver._artifact_bridge = bridge

    driver._reconcile_pending_side_effects(
        SimpleNamespace(run_id=bridge.store.run_id)
    )

    assert target.read_bytes() == expected
    replay = replay_artifacts(bridge.store.load_chain(), bridge.store.run_id)
    recovered = next(
        item for item in replay.side_effects if item.effect_key == spec.effect_key
    )
    assert recovered.result == sha256_bytes(expected)


def test_overwriting_projection_stops_when_target_matches_neither_digest(
    tmp_path: Path,
) -> None:
    bridge = _bridge(tmp_path)
    _initialize_ledger(bridge)
    target = tmp_path / "state.json"
    expected = b"expected\n"
    target.write_bytes(b"unrelated\n")
    operation = (
        "state.json",
        sha256_bytes(expected),
        sha256_bytes(b"previous\n"),
        encode_file_write_content(expected),
    )
    bridge.record_side_effect_intent(
        effect_class="file_write",
        work_unit_id="2",
        operation=operation,
        fingerprint_sha256=DIGEST,
    )
    driver = object.__new__(ProductionWorkflowDriver)
    driver.root = tmp_path
    driver._artifact_bridge = bridge

    with pytest.raises(SideEffectReconciliationError, match="durable identical"):
        driver._reconcile_pending_side_effects(
            SimpleNamespace(run_id=bridge.store.run_id)
        )


def test_file_intent_reconciles_identical_target_without_rewriting(
    tmp_path: Path,
) -> None:
    bridge = _bridge(tmp_path)
    target = tmp_path / "result.json"
    content = b"expected"
    target.write_bytes(content)
    spec = _spec("file_write", ("result.json", sha256_bytes(content)))
    bridge.record_side_effect_intent(
        effect_class=spec.effect_class,
        work_unit_id=spec.work_unit_id,
        operation=spec.operation,
        fingerprint_sha256=spec.fingerprint_sha256,
    )
    assert SideEffectExecutor(bridge).execute(
        spec,
        reconcile=lambda: reconcile_file_write(target, sha256_bytes(content)),
        perform=lambda: pytest.fail("identical durable file must not be rewritten"),
    ) == sha256_bytes(content)


def test_unknown_file_and_provider_windows_fail_closed(tmp_path: Path) -> None:
    bridge = _bridge(tmp_path)
    target = tmp_path / "result.json"
    target.write_bytes(b"other")
    spec = _spec("file_write", ("result.json", sha256_bytes(b"expected")))
    bridge.record_side_effect_intent(
        effect_class=spec.effect_class,
        work_unit_id=spec.work_unit_id,
        operation=spec.operation,
        fingerprint_sha256=spec.fingerprint_sha256,
    )
    with pytest.raises(SideEffectReconciliationError, match="unknown physical outcome"):
        SideEffectExecutor(bridge).execute(
            spec,
            reconcile=lambda: reconcile_file_write(target, sha256_bytes(b"expected")),
            perform=lambda: pytest.fail("unknown file state must not be overwritten"),
        )

    response = tmp_path / "missing-provider-response.json"
    provider_spec = _spec(
        "provider_start",
        ("claude", "review", "c" * 64, "d" * 64, "round:1", "1", "response"),
    )
    bridge.record_side_effect_intent(
        effect_class=provider_spec.effect_class,
        work_unit_id=provider_spec.work_unit_id,
        operation=provider_spec.operation,
        fingerprint_sha256=provider_spec.fingerprint_sha256,
    )
    with pytest.raises(SideEffectReconciliationError):
        SideEffectExecutor(bridge).begin(
            provider_spec,
            reconcile=lambda: reconcile_provider_start(response),
        )


def test_provider_reconciliation_uses_attempt_specific_response_evidence(
    tmp_path: Path,
) -> None:
    bridge = _bridge(tmp_path)
    _initialize_ledger(bridge)
    driver = object.__new__(ProductionWorkflowDriver)
    driver.root = tmp_path
    driver._artifact_bridge = bridge
    base = tmp_path / "response.json"
    first = driver._provider_attempt_response_path(base, 1)
    second = driver._provider_attempt_response_path(base, 2)
    first.write_bytes(b"first-attempt")
    spec = SideEffectSpec(
        "provider_start",
        "2",
        ("codex", "review", "c" * 64, "d" * 64, "round:1", "2", second.name),
        DIGEST,
    )

    outcome = driver._reconcile_provider_effect(spec, second)

    assert first != second
    assert outcome.outcome is ReconciliationOutcome.NOT_OCCURRED


def test_provider_start_guard_uses_the_attempt_specific_response_path(
    tmp_path: Path,
) -> None:
    bridge = _bridge(tmp_path)
    _initialize_ledger(bridge)
    measurement = ProviderInputMeasurement(
        provider="claude",
        role="claude",
        operation="claude_slice_review",
        binding_fingerprint="b" * 64,
        input_digest="c" * 64,
        policy_digest="d" * 64,
        components=(ProviderInputComponentSize("stdin_prompt", 3, 3),),
        total_chars=3,
        total_bytes=3,
        safety_limit_chars=10,
        safety_limit_bytes=10,
        technical_limit_chars=None,
        technical_limit_bytes=None,
        technical_limit_source=None,
        effective_limit_chars=10,
        effective_limit_bytes=10,
        allowed=True,
        violated_dimensions=(),
        char_overage=0,
        byte_overage=0,
        largest_component="stdin_prompt",
    )
    payload = provider_input_measurement_payload(
        measurement,
        work_unit_id="2",
        transition_fingerprint="e" * 64,
        relevant_record_head="f" * 64,
    )
    bootstrap = bridge.append(
        payload,
        logical_id="provider-input-2-claude_slice_review",
        idempotency_key="provider-input:test",
        fingerprint_sha256=DIGEST,
    )

    class ActiveState(SimpleNamespace):
        def mark_side_effect_completed(self, _key: str):
            return self

    driver = object.__new__(ProductionWorkflowDriver)
    driver.root = tmp_path
    driver.active_state = ActiveState(
        run_id=bridge.store.run_id,
        current_work_unit_id=2,
    )
    driver._artifact_bridge = bridge
    driver.agents = {"claude": SimpleNamespace(model="sonnet", effort="high")}
    base = (
        tmp_path
        / ".orchestrator"
        / "artifacts"
        / bridge.store.run_id
        / "review.log"
    )

    _, spec, attempt_path = driver._start_provider_attempt(
        measurement,
        bootstrap,
        operation_instance="round:1",
        durable_response_path=base,
    )
    base.parent.mkdir(parents=True, exist_ok=True)
    base.write_text("foreign base response", encoding="utf-8")
    with pytest.raises(SideEffectReconciliationError, match="unknown physical outcome"):
        driver._start_provider_attempt(
            measurement,
            bootstrap,
            operation_instance="round:1",
            durable_response_path=base,
        )

    attempt_path.write_text("exact attempt response", encoding="utf-8")
    with pytest.raises(WorkflowExecutionError, match="already occurred"):
        driver._start_provider_attempt(
            measurement,
            bootstrap,
            operation_instance="round:1",
            durable_response_path=base,
        )
    replay = replay_artifacts(bridge.store.load_chain(), bridge.store.run_id)
    completed = next(item for item in replay.side_effects if item.effect_key == spec.effect_key)
    assert completed.result == sha256_bytes(attempt_path.read_bytes())


def test_queue_finalization_never_records_result_before_destination_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir()
    outbox.mkdir()
    task = inbox / "task.md"
    task.write_bytes(b"task")
    digest = sha256_bytes(b"task")
    run_id = "queue-finalize"
    save_watch_identity(
        task,
        WatchTaskIdentity(run_id, digest, True, "structured-v2", 2),
    )
    bridge = _bridge(tmp_path, run_id)
    _initialize_ledger(bridge, digest)
    real_move = inbox_watcher.move_to_reserved_outbox

    def move_then_corrupt(source: Path, destination: Path) -> Path:
        moved = real_move(source, destination)
        destination.write_bytes(b"corrupt after move")
        return moved

    monkeypatch.setattr(inbox_watcher, "move_to_reserved_outbox", move_then_corrupt)

    result = finalize_queue_success(
        task,
        inbox_dir=inbox,
        outbox_dir=outbox,
        run_id=run_id,
        task_digest=digest,
        publish=True,
        repository_root=tmp_path,
    )

    assert result.disposition is QueueFinalizationDisposition.FAILED
    replay = replay_artifacts(bridge.store.load_chain(), run_id)
    queue_effect = next(
        item for item in replay.side_effects if item.effect_class == "queue_move"
    )
    assert queue_effect.result is None


def test_queue_and_git_reconciliation_classify_exact_physical_state(
    tmp_path: Path,
) -> None:
    source = tmp_path / "inbox.md"
    destination = tmp_path / "done.md"
    content = b"task"
    source.write_bytes(content)
    assert reconcile_queue_move(
        source, destination, sha256_bytes(content)
    ).outcome is ReconciliationOutcome.NOT_OCCURRED
    source.replace(destination)
    queue_result = reconcile_queue_move(
        source, destination, sha256_bytes(content)
    )
    assert queue_result.outcome is ReconciliationOutcome.OCCURRED
    assert queue_result.result == sha256_bytes(content)

    assert reconcile_git_commit(
        prior_head="a" * 40,
        current_head="b" * 40,
        current_parent="a" * 40,
        expected_tree="c" * 40,
        current_tree="c" * 40,
    ).outcome is ReconciliationOutcome.OCCURRED
    assert reconcile_git_commit(
        prior_head="a" * 40,
        current_head="b" * 40,
        current_parent="d" * 40,
        expected_tree="c" * 40,
        current_tree="c" * 40,
    ).outcome is ReconciliationOutcome.UNKNOWN


def test_queue_reconciliation_rejects_symlink_destination(tmp_path: Path) -> None:
    destination = tmp_path / "done.md"
    durable_target = tmp_path / "durable.md"
    durable_target.write_bytes(b"task")
    try:
        destination.symlink_to(durable_target)
    except OSError as exc:  # pragma: no cover - host policy may forbid symlinks
        pytest.skip(f"symlink creation is unavailable: {exc}")

    assert reconcile_queue_move(
        tmp_path / "missing.md", destination, sha256_bytes(b"task")
    ).outcome is ReconciliationOutcome.UNKNOWN


def test_generic_resume_reconciles_pending_queue_move(tmp_path: Path) -> None:
    bridge = _bridge(tmp_path)
    _initialize_ledger(bridge)
    source = tmp_path / "inbox.md"
    destination = tmp_path / "done.md"
    source.write_bytes(b"task")
    digest = sha256_bytes(b"task")
    operation = (str(source), str(destination), digest)
    bridge.record_side_effect_intent(
        effect_class="queue_move",
        work_unit_id="queue",
        operation=operation,
        fingerprint_sha256=digest,
    )
    source.replace(destination)
    driver = object.__new__(ProductionWorkflowDriver)
    driver.root = tmp_path
    driver._artifact_bridge = bridge

    driver._reconcile_pending_side_effects(
        SimpleNamespace(run_id=bridge.store.run_id)
    )

    replay = replay_artifacts(bridge.store.load_chain(), bridge.store.run_id)
    queue_effect = next(
        item for item in replay.side_effects if item.effect_class == "queue_move"
    )
    assert queue_effect.result == digest


def test_failed_outbox_move_recovers_twice_without_a_second_destination(
    tmp_path: Path,
) -> None:
    inbox = tmp_path / "inbox"
    failed = tmp_path / "failed"
    inbox.mkdir()
    failed.mkdir()
    source = inbox / "task.md"
    source.write_bytes(b"task")
    digest = sha256_bytes(b"task")
    destination = failed / "bound-task.md.poison"
    bridge = _bridge(tmp_path, "queue-run")
    _initialize_ledger(bridge, digest)
    operation = (str(source.resolve()), str(destination.resolve()), digest)
    bridge.record_side_effect_intent(
        effect_class="queue_move",
        work_unit_id="queue",
        operation=operation,
        fingerprint_sha256=digest,
    )
    source.replace(destination)

    first = move_to_outbox_with_ledger(
        source,
        failed,
        repository_root=tmp_path,
        run_id="queue-run",
        task_digest=digest,
        source_name="task.md.poison",
    )
    second = move_to_outbox_with_ledger(
        source,
        failed,
        repository_root=tmp_path,
        run_id="queue-run",
        task_digest=digest,
        source_name="task.md.poison",
    )

    assert first == second == destination.resolve()
    assert tuple(failed.iterdir()) == (destination,)


def test_failed_outbox_move_never_records_result_before_destination_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    inbox = tmp_path / "inbox"
    failed = tmp_path / "failed"
    inbox.mkdir()
    failed.mkdir()
    source = inbox / "task.md"
    source.write_bytes(b"task")
    digest = sha256_bytes(b"task")
    bridge = _bridge(tmp_path, "queue-digest-run")
    _initialize_ledger(bridge, digest)
    real_move = inbox_watcher.move_to_reserved_outbox

    def move_then_corrupt(source_path: Path, destination: Path) -> Path:
        moved = real_move(source_path, destination)
        destination.write_bytes(b"wrong")
        return moved

    monkeypatch.setattr(inbox_watcher, "move_to_reserved_outbox", move_then_corrupt)
    with pytest.raises(ValueError, match="before result completion"):
        move_to_outbox_with_ledger(
            source,
            failed,
            repository_root=tmp_path,
            run_id="queue-digest-run",
            task_digest=digest,
            source_name="task.md.poison",
        )

    replay = replay_artifacts(bridge.store.load_chain(), "queue-digest-run")
    queue_effect = next(
        item for item in replay.side_effects if item.effect_class == "queue_move"
    )
    assert queue_effect.result is None


def test_empty_chain_is_rejected_before_queue_move(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    failed = tmp_path / "failed"
    inbox.mkdir()
    failed.mkdir()
    source = inbox / "task.md"
    source.write_bytes(b"task")
    digest = sha256_bytes(b"task")

    with pytest.raises(ArtifactReplayError, match="RECORD-MISSING"):
        move_to_outbox_with_ledger(
            source,
            failed,
            repository_root=tmp_path,
            run_id="pre-baseline-run",
            task_digest=digest,
            source_name="task.md.poison",
        )

    assert source.is_file()
    assert tuple(failed.iterdir()) == ()
    assert ArtifactStore(tmp_path, "pre-baseline-run").load_chain() == ()


def test_empty_chain_uses_deterministic_poison_quarantine_without_records(
    tmp_path: Path,
) -> None:
    inbox = tmp_path / "inbox"
    failed = tmp_path / "failed"
    inbox.mkdir()
    failed.mkdir()
    source = inbox / "task.md"
    source.write_bytes(b"task")
    digest = sha256_bytes(b"task")
    diagnostics: list[str] = []

    destination = move_poison_to_outbox_recoverably(
        source,
        failed,
        repository_root=tmp_path,
        run_id="pre-baseline-run",
        task_digest=digest,
        source_name="task.md.poison",
        quarantine_diagnostic=diagnostics.append,
    )

    assert destination.name == (
        f"quarantine-pre-baseline-run-{digest[:16]}-task.md.poison"
    )
    assert destination.read_bytes() == b"task"
    assert not source.exists()
    assert len(diagnostics) == 1
    assert "RECORD-MISSING" in diagnostics[0]
    assert ArtifactStore(tmp_path, "pre-baseline-run").load_chain() == ()


def test_corrupt_chain_uses_deterministic_poison_quarantine(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    failed = tmp_path / "failed"
    inbox.mkdir()
    failed.mkdir()
    source = inbox / "task.md"
    source.write_bytes(b"task")
    digest = sha256_bytes(b"task")
    records = tmp_path / ".orchestrator" / "artifacts" / "corrupt-run" / "records"
    records.mkdir(parents=True)
    (records / "unexpected.txt").write_text("corrupt", encoding="utf-8")

    first = move_poison_to_outbox_recoverably(
        source,
        failed,
        repository_root=tmp_path,
        run_id="corrupt-run",
        task_digest=digest,
        source_name="task.md.poison",
    )
    second = move_poison_to_outbox_recoverably(
        source,
        failed,
        repository_root=tmp_path,
        run_id="corrupt-run",
        task_digest=digest,
        source_name="task.md.poison",
    )
    assert first == second
    assert first.name == f"quarantine-corrupt-run-{digest[:16]}-task.md.poison"


def test_unknown_ledger_queue_state_falls_back_to_poison_quarantine(
    tmp_path: Path,
) -> None:
    inbox = tmp_path / "inbox"
    failed = tmp_path / "failed"
    inbox.mkdir()
    failed.mkdir()
    source = inbox / "task.md"
    source.write_bytes(b"task")
    digest = sha256_bytes(b"task")
    conflicting = failed / "ledger-bound.poison"
    conflicting.write_bytes(b"task")
    bridge = _bridge(tmp_path, "ambiguous-queue-run")
    _initialize_ledger(bridge, digest)
    bridge.record_side_effect_intent(
        effect_class="queue_move",
        work_unit_id="queue",
        operation=(str(source.resolve()), str(conflicting.resolve()), digest),
        fingerprint_sha256=digest,
    )
    diagnostics: list[str] = []

    destination = move_poison_to_outbox_recoverably(
        source,
        failed,
        repository_root=tmp_path,
        run_id="ambiguous-queue-run",
        task_digest=digest,
        source_name="task.md.poison",
        quarantine_diagnostic=diagnostics.append,
    )

    assert destination.name == (
        f"quarantine-ambiguous-queue-run-{digest[:16]}-task.md.poison"
    )
    assert destination.read_bytes() == b"task"
    assert not source.exists()
    assert len(diagnostics) == 1
    assert "SideEffectReconciliationError" in diagnostics[0]


def test_pre_ledger_chain_uses_deterministic_poison_quarantine(
    tmp_path: Path,
) -> None:
    inbox = tmp_path / "inbox"
    failed = tmp_path / "failed"
    inbox.mkdir()
    failed.mkdir()
    source = inbox / "task.md"
    source.write_bytes(b"task")
    digest = sha256_bytes(b"task")
    bridge = _bridge(tmp_path, "pre-ledger-run")
    bridge.record_side_effect_intent(
        effect_class="internal",
        work_unit_id="1",
        operation=("legacy-marker",),
        fingerprint_sha256=digest,
    )
    bridge.record_side_effect_result(
        effect_class="internal",
        work_unit_id="1",
        operation=("legacy-marker",),
        result="completed",
        fingerprint_sha256=digest,
    )
    diagnostics: list[str] = []

    first = move_poison_to_outbox_recoverably(
        source,
        failed,
        repository_root=tmp_path,
        run_id="pre-ledger-run",
        task_digest=digest,
        source_name="task.md.poison",
        quarantine_diagnostic=diagnostics.append,
    )
    second = move_poison_to_outbox_recoverably(
        source,
        failed,
        repository_root=tmp_path,
        run_id="pre-ledger-run",
        task_digest=digest,
        source_name="task.md.poison",
        quarantine_diagnostic=diagnostics.append,
    )

    assert first == second
    assert len(diagnostics) == 2
    assert all("initialized side-effect ledger" in item for item in diagnostics)


def test_completed_audit_commit_is_not_reused_for_a_new_projection(
    tmp_path: Path,
) -> None:
    subprocess.run(
        ["git", "init", "-b", "feature/r4"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        check=True,
    )
    audit = tmp_path / "docs" / "internal" / "audit.md"
    audit.parent.mkdir(parents=True)
    audit.write_text("audit\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".orchestrator/\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "docs/internal/audit.md", ".gitignore"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "audit"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    current = inspect_repository(tmp_path).head
    tree = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    bridge = _bridge(tmp_path, "audit-resume-run")
    _initialize_ledger(bridge)
    operation = (
        "audit_commit",
        "docs/internal/audit.md",
        current,
        tree,
        "b" * 64,
        "c" * 64,
    )
    bridge.record_side_effect_intent(
        effect_class="git_commit",
        work_unit_id="2",
        operation=operation,
        fingerprint_sha256="b" * 64,
    )
    bridge.record_side_effect_result(
        effect_class="git_commit",
        work_unit_id="2",
        operation=operation,
        result="f" * 40,
        fingerprint_sha256="b" * 64,
    )
    driver = object.__new__(ProductionWorkflowDriver)
    driver.root = tmp_path
    driver._artifact_bridge = bridge
    driver.assert_structured_decision_context = lambda: None
    state = SimpleNamespace(
        audit_report_path="docs/internal/audit.md",
        run_id="audit-resume-run",
        current_work_unit_id=2,
        branch="feature/r4",
        task_digest=None,
    )

    assert driver.finalize_audit(state) == current


def test_post_commit_intent_is_reconciled_from_exact_parent_and_tree(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-b", "feature/r4"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=tmp_path, check=True, capture_output=True)
    prior = inspect_repository(tmp_path).head
    tracked.write_text("changed\n", encoding="utf-8")
    changes = collect_repository_changes(tmp_path, prior)
    expected_tree = preview_commit_tree(tmp_path, changes)
    bridge = _bridge(tmp_path, "git-crash-run")
    _initialize_ledger(bridge)
    operation = (
        "slice_commit",
        "1",
        prior,
        expected_tree,
        changes.fingerprint,
        "f" * 64,
    )
    bridge.record_side_effect_intent(
        effect_class="git_commit",
        work_unit_id="2",
        operation=operation,
        fingerprint_sha256=changes.fingerprint,
    )
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "Slice 01: test"], cwd=tmp_path, check=True, capture_output=True)
    committed = inspect_repository(tmp_path).head

    driver = object.__new__(ProductionWorkflowDriver)
    driver.root = tmp_path
    driver._artifact_bridge = bridge
    driver._reconcile_pending_side_effects(
        SimpleNamespace(
            run_id="git-crash-run",
            current_work_unit_id=2,
            current_step=WorkflowStep.SLICE_COMMIT,
        )
    )

    replay = replay_artifacts(bridge.store.load_chain(), "git-crash-run")
    git_effect = next(item for item in replay.side_effects if item.effect_class == "git_commit")
    assert git_effect.result == committed
