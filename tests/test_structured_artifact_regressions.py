from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

import orchestrator
from audit_trail import ReviewAuditEvent, ValidationAuditEvent
from artifact_bridge import ArtifactBridge, attestation_payload
from artifact_models import RecordType, ReviewPayload, Role
from artifact_store import ArtifactStore
from contracts import (
    AgentRole,
    ContractResult,
    StopRequest,
    ValidationAttestation,
    ValidationCommandSpec,
    ValidationRecord,
    ValidationStatus,
)
from orchestrator import ProductionWorkflowDriver
from workflow import WorkflowExecutionError, WorkflowHistory
from workflow_state import (
    ProtocolBinding,
    ProtocolMode,
    init_workflow_state,
)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _repository(tmp_path: Path, branch: str) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", branch)
    _git(repository, "config", "user.name", "Structured Regression")
    _git(repository, "config", "user.email", "structured@example.invalid")
    (repository / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(repository, "add", "seed.txt")
    _git(repository, "commit", "-m", "seed")
    return repository


def _state(repository: Path, run_id: str = "structured-regression"):
    head = _git(repository, "rev-parse", "HEAD")
    return init_workflow_state(
        run_id=run_id,
        task_file=str(repository / "task.md"),
        branch="feature/structured-regression",
        branch_base=head,
        slice_count=1,
        task_digest="a" * 64,
        task_scope_patterns=("src/runtime.py",),
        target_branch="feature/structured-regression",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V1, "1"),
    )


def _driver(repository: Path) -> ProductionWorkflowDriver:
    return ProductionWorkflowDriver(
        repository_root=repository,
        state_file=repository / ".orchestrator" / "state.json",
        agents={},
        config=orchestrator.OrchestratorConfig(repo_root=repository),
        allowed_roots=(repository,),
    )


def test_first_checkpoint_bootstraps_authoritative_chain_idempotently(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/structured-regression")
    state = _state(repository)
    driver = _driver(repository)

    driver.checkpoint(state, WorkflowHistory(1))
    persisted = driver.active_state
    assert persisted is not None
    driver.checkpoint(persisted, WorkflowHistory(1))
    driver.assert_structured_decision_context()

    chain = ArtifactStore(repository, state.run_id).load_chain()
    assert tuple(record.record_type for record in chain) == (RecordType.TASK,)


def test_external_side_effect_guard_rejects_mirror_ahead_of_records(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/structured-regression")
    state = _state(repository, "structured-mirror-drift")
    driver = _driver(repository)
    driver.checkpoint(state, WorkflowHistory(1))
    assert driver.active_state is not None
    driver.active_state = replace(
        driver.active_state, task_scope_patterns=("src/foreign.py",)
    )

    with pytest.raises(WorkflowExecutionError, match="decision context is not resumable"):
        driver.assert_structured_decision_context()


def test_external_side_effect_guard_rejects_review_record_ahead_of_mirror(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/structured-regression")
    state = _state(repository, "structured-review-drift")
    driver = _driver(repository)
    driver.checkpoint(state, WorkflowHistory(1))
    ArtifactBridge(ArtifactStore(repository, state.run_id)).append(
        ReviewPayload(
            reviewer=Role.CLAUDE,
            work_unit_id="1",
            verdict="approved",
            finding_ids=(),
            evidence="complete evidence",
        ),
        logical_id="review-claude-1-1",
        idempotency_key="review-drift",
        fingerprint_sha256="b" * 64,
    )

    with pytest.raises(WorkflowExecutionError, match="reviewer decisions differ"):
        driver.assert_structured_decision_context()


def test_structured_resume_accepts_mirrored_stopped_review(tmp_path: Path) -> None:
    repository = _repository(tmp_path, "feature/structured-regression")
    state = _state(repository, "structured-stopped-review")
    driver = _driver(repository)
    driver.checkpoint(state, WorkflowHistory(1))
    attestation = ValidationAttestation(
        attestation_id="validation-stop",
        diff_fingerprint="b" * 64,
        expected_commands=("python3 -m pytest tests/ -v",),
        records=(
            ValidationRecord(
                ValidationStatus.PASS,
                "python3 -m pytest tests/ -v",
                0,
            ),
        ),
        output_digest="c" * 64,
        summary="validation completed before reviewer stop",
        command_specs=(
            ValidationCommandSpec(
                argv=("python3", "-m", "pytest", "tests/", "-v")
            ),
        ),
    )
    stopped = ContractResult(
        reviewer=AgentRole.CLAUDE,
        approval=None,
        stopped=True,
        stop_request=StopRequest("CONTRACT-UNCLEAR", "owner decision required"),
        validation=attestation,
        test_files=(),
        pre_mortem=None,
        evidence=None,
        findings=(),
        anchors=(),
    )
    bridge = ArtifactBridge(ArtifactStore(repository, state.run_id))
    bridge.append(
        attestation_payload(attestation),
        logical_id=attestation.attestation_id,
        idempotency_key="attestation:validation-stop",
        fingerprint_sha256=attestation.diff_fingerprint,
    )
    bridge.append(
        ReviewPayload(
            reviewer=Role.CLAUDE,
            work_unit_id="1",
            verdict="stop",
            finding_ids=(),
            evidence="owner decision required",
        ),
        logical_id="review-claude-1-1",
        idempotency_key="review-stop",
        fingerprint_sha256=attestation.diff_fingerprint,
    )
    history = WorkflowHistory(
        1,
        events=(
            ValidationAuditEvent(1, 1, attestation),
            ReviewAuditEvent(2, 1, 1, stopped),
        ),
        attestations=(attestation,),
    )
    assert driver.active_state is not None
    driver.checkpoint(driver.active_state, history)

    resumed = _driver(repository)
    assert driver.active_state is not None
    resumed.bind_work_unit(driver.active_state)
    resumed.assert_structured_decision_context()


def test_unbound_historical_state_keeps_legacy_resume_mode(tmp_path: Path) -> None:
    repository = _repository(tmp_path, "feature/structured-regression")
    historical = replace(_state(repository, "legacy-resume"), protocol_binding=None)
    driver = _driver(repository)

    driver.bind_work_unit(historical)
    driver.assert_structured_decision_context()

    assert historical.effective_protocol_mode is ProtocolMode.LEGACY_STATE_V3
    assert ArtifactStore(repository, historical.run_id).load_chain() == ()
