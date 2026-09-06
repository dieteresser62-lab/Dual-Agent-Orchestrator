from __future__ import annotations

import subprocess
import sys
import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

import orchestrator
from artifact_resume import ArtifactResumeError
from cli import parse_args
from inbox_watcher import (
    WatchTaskDisposition,
    WatchTaskIdentity,
    WatchTaskResult,
    save_watch_identity,
    success_marker_path,
    watch_identity_path,
)
from orchestrator import run_pipeline
from task_contract import TaskContractError
from workflow import WorkflowExecutionError, WorkflowHistory, WorkflowRunResult
from workflow_state import GateReason, ProtocolBinding, ProtocolMode, init_workflow_state


@pytest.mark.parametrize(
    "flag",
    (
        "--development-mode", "--from-phase", "--phase1-max-cycles",
        "--phase2-max-cycles", "--max-agent-retries", "--manual-gate",
        "--max-shared-chars", "--file-snapshot-max-lines", "--no-recover",
    ),
)
def test_removed_phase_options_are_unknown(flag: str, tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        parse_args([flag], cwd=tmp_path, environ={})


def test_default_dry_run_completes_full_workflow_without_development_flag(
    tmp_path: Path, monkeypatch
) -> None:
    task = tmp_path / "task.md"
    task.write_text("Implement the bounded task", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    args = parse_args(["--dry-run", "--task-file", str(task)], cwd=tmp_path, environ={})
    assert run_pipeline(task, args) == 0
    assert not (tmp_path / ".orchestrator" / "state.json").exists()


def test_watch_dry_run_returns_typed_terminal_result(tmp_path: Path, monkeypatch) -> None:
    task = tmp_path / "task.md"
    task.write_text("Implement the bounded task", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    args = parse_args(["--dry-run", "--task-file", str(task)], cwd=tmp_path, environ={})
    args.watch_run_id = "watch-slice-18"
    result = run_pipeline(task, args)
    assert isinstance(result, WatchTaskResult)
    assert result.disposition is WatchTaskDisposition.COMPLETED
    assert result.run_id == "watch-slice-18"


def test_explicit_direct_resume_finalizes_bound_watch_task(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir()
    task = inbox / "resume.md"
    task.write_text("bound resume", encoding="utf-8")
    digest = hashlib.sha256(task.read_bytes()).hexdigest()
    identity = WatchTaskIdentity(
        "watch-direct-resume", digest, True, "structured-v2", 2
    )
    save_watch_identity(task, identity)
    completed, _, _ = orchestrator.run_default_dry_run(
        task, run_id=identity.run_id
    )
    completed = WorkflowRunResult(
        replace(completed.state, task_digest=digest), completed.history
    )
    monkeypatch.setattr(
        orchestrator, "run_production_workflow", lambda *_args, **_kwargs: completed
    )
    args = parse_args(
        [
            "--resume",
            "--task-file",
            str(task),
            "--inbox-dir",
            str(inbox),
            "--outbox-dir",
            str(outbox),
        ],
        cwd=tmp_path,
        environ={},
    )

    assert run_pipeline(task, args) == 0
    moved = list((outbox / "done").glob("*.md"))
    assert len(moved) == 1
    assert moved[0].read_text(encoding="utf-8") == "bound resume"
    assert not task.exists()
    assert not success_marker_path(task).exists()
    assert not watch_identity_path(task).exists()


def test_nonterminal_direct_resume_keeps_bound_watch_task_in_inbox(
    tmp_path: Path, monkeypatch
) -> None:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir()
    task = inbox / "resume.md"
    task.write_text("bound resume", encoding="utf-8")
    digest = hashlib.sha256(task.read_bytes()).hexdigest()
    identity = WatchTaskIdentity("watch-halt", digest, True, "structured-v2", 2)
    save_watch_identity(task, identity)
    state = init_workflow_state(
        run_id=identity.run_id,
        task_file=str(task.resolve()),
        branch="feature/resume",
        branch_base="a" * 40,
        first_slice_start_commit="a" * 40,
        slice_count=1,
        task_digest=digest,
        task_scope_patterns=("src/**",),
        target_branch="feature/resume",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
    ).await_bootstrap_resume(detail="repair", fingerprint="b" * 64)
    monkeypatch.setattr(
        orchestrator,
        "run_production_workflow",
        lambda *_args, **_kwargs: WorkflowRunResult(state, WorkflowHistory(1)),
    )
    args = parse_args(
        [
            "--resume", "--task-file", str(task),
            "--inbox-dir", str(inbox), "--outbox-dir", str(outbox),
        ],
        cwd=tmp_path,
        environ={},
    )

    assert run_pipeline(task, args) == 4
    assert task.exists()
    assert not success_marker_path(task).exists()
    assert list((outbox / "done").glob("*")) == []


def test_watch_pipeline_failure_returns_diagnostic_typed_result(
    tmp_path: Path, monkeypatch
) -> None:
    task = tmp_path / "task.md"
    task.write_text("Implement the bounded task", encoding="utf-8")
    args = parse_args(["--task-file", str(task)], cwd=tmp_path, environ={})
    args.watch_run_id = "watch-failure"

    def fail(*_args, **_kwargs):
        raise WorkflowExecutionError("plan parser rejected heading")

    monkeypatch.setattr(orchestrator, "run_production_workflow", fail)

    result = run_pipeline(task, args)

    assert isinstance(result, WatchTaskResult)
    assert result.disposition is WatchTaskDisposition.RESUMABLE_HALT
    assert result.exit_code == 4
    assert result.gate_reason == "WORKFLOW-EXECUTION"
    assert result.classified_failure is not None
    assert result.classified_failure.explicitly_mapped is True
    assert result.failure_detail == (
        "WorkflowExecutionError: plan parser rejected heading"
    )


def test_invalid_task_contract_is_terminally_rejected_before_run_start(
    tmp_path: Path, monkeypatch
) -> None:
    task = tmp_path / "invalid.md"
    task.write_text(
        "\n".join(
            (
                "ORCHESTRATOR_MODE: IMPLEMENT",
                "TARGET_BRANCH: main",
                "TASK_SCOPE: src/**",
            )
        ),
        encoding="utf-8",
    )
    args = parse_args(["--task-file", str(task)], cwd=tmp_path, environ={})
    args.watch_run_id = "watch-invalid-contract"
    monkeypatch.chdir(tmp_path)

    result = run_pipeline(task, args)

    assert isinstance(result, WatchTaskResult)
    assert result.disposition is WatchTaskDisposition.REJECTED
    assert result.exit_code == 5
    assert result.gate_reason == "TASK-CONTRACT"
    assert result.resume_available is False
    assert not (tmp_path / ".orchestrator").exists()


def test_terminal_error_after_record_start_is_promoted_to_resumable_halt(
    tmp_path: Path, monkeypatch
) -> None:
    task = tmp_path / "task.md"
    task.write_text("Implement the bounded task", encoding="utf-8")
    args = parse_args(["--task-file", str(task)], cwd=tmp_path, environ={})
    args.watch_run_id = "watch-record-started"
    records = (
        tmp_path
        / ".orchestrator"
        / "artifacts"
        / args.watch_run_id
        / "records"
    )
    records.mkdir(parents=True)
    sentinel = records / ("ar1-" + "a" * 64 + ".json")
    sentinel.write_text("persisted record bytes", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    def fail(*_args, **_kwargs):
        raise TaskContractError("late contract conflict")

    monkeypatch.setattr(orchestrator, "run_production_workflow", fail)

    result = run_pipeline(task, args)

    assert isinstance(result, WatchTaskResult)
    assert result.disposition is WatchTaskDisposition.RESUMABLE_HALT
    assert result.exit_code == 4
    assert result.resume_available is True
    assert result.gate_reason == "TASK-CONTRACT-AFTER-RECORD-START"
    assert sentinel.read_text(encoding="utf-8") == "persisted record bytes"


def test_structured_resume_mismatch_is_resumable_and_not_a_technical_retry(
    tmp_path: Path, monkeypatch
) -> None:
    task = tmp_path / "task.md"
    task.write_text("Implement the bounded task", encoding="utf-8")
    args = parse_args(["--task-file", str(task)], cwd=tmp_path, environ={})
    args.watch_run_id = "watch-structured-resume"

    def fail(*_args, **_kwargs):
        raise ArtifactResumeError("record ar1-deadbeef differs; repair the mirror")

    monkeypatch.setattr(orchestrator, "run_production_workflow", fail)

    result = run_pipeline(task, args)

    assert isinstance(result, WatchTaskResult)
    assert result.disposition is WatchTaskDisposition.RESUMABLE_HALT
    assert result.exit_code == 4
    assert result.gate_reason == "ARTIFACT-RESUME"
    assert result.classified_failure is not None
    assert result.classified_failure.exception_type == "ArtifactResumeError"


@pytest.mark.parametrize("watch_mode", (False, True))
def test_pipeline_exposes_bootstrap_denial_as_resumable_exit_four(
    tmp_path: Path, monkeypatch, watch_mode: bool
) -> None:
    task = tmp_path / "task.md"
    task.write_text("Implement the bounded task", encoding="utf-8")
    args = parse_args(["--task-file", str(task)], cwd=tmp_path, environ={})
    if watch_mode:
        args.watch_run_id = "watch-bootstrap"
    state = init_workflow_state(
        run_id="watch-bootstrap" if watch_mode else "formal-bootstrap",
        task_file=str(task),
        branch="feature/bootstrap",
        branch_base="a" * 40,
        first_slice_start_commit="a" * 40,
        slice_count=1,
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
    ).await_bootstrap_resume(
        detail="FINAL-REVIEW-PREFLIGHT | restore the record mirror",
        fingerprint="b" * 64,
    )
    monkeypatch.setattr(
        orchestrator,
        "run_production_workflow",
        lambda *_args, **_kwargs: WorkflowRunResult(state, WorkflowHistory(1)),
    )

    result = run_pipeline(task, args)

    if watch_mode:
        assert isinstance(result, WatchTaskResult)
        assert result.disposition is WatchTaskDisposition.RESUMABLE_HALT
        assert result.exit_code == 4
        assert result.gate_reason == GateReason.BOOTSTRAP_CHECK.value
    else:
        assert result == 4


def test_help_contains_only_slice_v3_vocabulary() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "src" / "cli.py"), "--help"],
        cwd=root, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    assert "state-v3 slice workflow" in result.stdout
    for term in ("development-mode", "from-phase", "phase1", "phase2"):
        assert term not in result.stdout.lower()
