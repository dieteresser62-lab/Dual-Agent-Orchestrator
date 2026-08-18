from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from artifact_bridge import ArtifactBridge
from artifact_migration import ArtifactResumeError, resolve_resume_state
from artifact_models import FingerprintKind, TaskPayload, WorkUnitPayload
from artifact_store import ArtifactStore
from workflow_state import (
    AgentFailureKind,
    GateReason,
    InvocationFailureRecord,
    ProtocolBinding,
    ProtocolMode,
    WorkflowStep,
    WorkUnitKind,
    init_workflow_state,
)


def _state(repository: Path, *, structured: bool = True):
    task = repository / "task.md"
    task.write_text("task", encoding="utf-8")
    state = init_workflow_state(
        run_id="resume-run",
        task_file=str(task),
        branch="feature/resume",
        branch_base="b" * 40,
        slice_count=1,
        task_digest="a" * 64,
        task_scope_patterns=("src/resume.py",),
        target_branch="feature/resume",
        protocol_binding=(
            ProtocolBinding(ProtocolMode.STRUCTURED_V1, "1") if structured else None
        ),
    ).complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit="b" * 40,
        scope_paths=("src/resume.py",),
        start_fingerprint="c" * 64,
    )
    return state


def _records(repository: Path, state) -> None:
    bridge = ArtifactBridge(ArtifactStore(repository, state.run_id))
    bridge.append(
        TaskPayload("feature/resume", ("src/resume.py",), "a" * 64),
        logical_id="task-contract",
        idempotency_key="task-contract",
        fingerprint_sha256="a" * 64,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    bridge.append(
        WorkUnitPayload("1", 1, ("src/resume.py",)),
        logical_id="work-unit-2",
        idempotency_key="work-unit:2:round:1",
        fingerprint_sha256="a" * 64,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )


def test_legacy_state_without_records_remains_on_legacy_path(tmp_path: Path) -> None:
    state = _state(tmp_path, structured=False)

    resolved = resolve_resume_state(tmp_path, state)

    assert resolved.state == state
    assert resolved.mode is ProtocolMode.LEGACY_STATE_V3
    assert resolved.record_head_id is None
    assert not (tmp_path / ".orchestrator" / "artifacts").exists()


def test_structured_state_rehydrates_from_matching_complete_chain(tmp_path: Path) -> None:
    state = _state(tmp_path)
    _records(tmp_path, state)

    resolved = resolve_resume_state(tmp_path, state)

    assert resolved.state == state
    assert resolved.mode is ProtocolMode.STRUCTURED_V1
    assert resolved.record_head_id is not None


def test_structured_state_without_records_halts_with_repair_hint(tmp_path: Path) -> None:
    with pytest.raises(ArtifactResumeError, match="restore its record directory"):
        resolve_resume_state(tmp_path, _state(tmp_path))


def test_structured_state_rejects_a_stale_round_with_record_id(tmp_path: Path) -> None:
    state = _state(tmp_path)
    _records(tmp_path, state)
    stale = replace(
        state,
        work_units=(state.work_units[0], replace(state.work_units[1], round_number=2)),
    )

    with pytest.raises(ArtifactResumeError, match=r"record ar1-[0-9a-f]{64}.*round"):
        resolve_resume_state(tmp_path, stale)


def test_structured_resume_halts_when_mirror_gate_decision_has_no_chain_record(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    _records(tmp_path, state)
    state = state.await_user_gate(
        reason=GateReason.TEST_CHANGE,
        detail="changed test requires approval",
        fingerprint="d" * 64,
        paths=("tests/test_resume.py",),
    ).record_user_gate_decision(
        approved=True,
        fingerprint="d" * 64,
        paths=("tests/test_resume.py",),
        decided_by="user",
        decided_at="2026-08-18T12:00:00+00:00",
        rationale="approve changed resume test",
    )

    with pytest.raises(ArtifactResumeError, match="gate decisions differ from state-v3"):
        resolve_resume_state(tmp_path, state)


def test_structured_resume_halts_when_mirror_finding_transition_has_no_chain_record(
    tmp_path: Path,
) -> None:
    state = replace(
        _state(tmp_path),
        runtime_history={
            "findings": [
                {
                    "finding_id": "C-05",
                    "status": "OPEN",
                }
            ]
        },
    )
    _records(tmp_path, state)

    with pytest.raises(
        ArtifactResumeError,
        match="finding transitions differ from state-v3",
    ):
        resolve_resume_state(tmp_path, state)


def test_structured_resume_halts_when_mirror_quota_pause_has_no_chain_record(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    _records(tmp_path, state)
    failure = InvocationFailureRecord(
        invocation_id="inv-quota-mirror-only",
        idempotency_key="resume-run:2:codex_implementation:claude",
        role="claude",
        failure_kind=AgentFailureKind.QUOTA,
        provider_text="usage cap reached; retry later",
        received_at="2026-08-18T12:00:00+00:00",
        step=WorkflowStep.CODEX_IMPLEMENTATION,
        slice_id=1,
        work_unit_id=2,
        diagnostic_exit_code=2,
        parse_path="claude:text:absolute",
        source_timezone="UTC",
        reset_at_utc="2026-08-18T12:05:00+00:00",
        resume_at_utc="2026-08-18T12:05:30+00:00",
        safety_margin_seconds=30,
        automatic_resume=True,
        diff_fingerprint="d" * 64,
    )
    state = state.record_invocation_failure(
        failure,
        wait_automatically=True,
    ).resume_after_invocation_halt(updated_at="2026-08-18T12:05:30+00:00")

    with pytest.raises(ArtifactResumeError, match="quota pauses differ from state-v3"):
        resolve_resume_state(tmp_path, state)
