from __future__ import annotations

import subprocess
import sys
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

import orchestrator
from agent_adapters import AgentOutputError
from artifact_bridge import ArtifactBridge
from artifact_models import (
    FingerprintKind,
    RoleProfilePayload,
    RunIdentityPayload,
    RunProfilePayload,
    TaskPayload,
)
from artifact_replay import ArtifactReplayError, ReplayDiagnostic, ReplayDiagnosticCode
from artifact_resume import ArtifactResumeError
from artifact_store import ArtifactStore
from cli import parse_args
from inbox_watcher import (
    WatchTaskDisposition,
    WatchTaskIdentity,
    WatchTaskResult,
    pre_baseline_halt_diagnostic_path,
    save_watch_identity,
    success_marker_path,
    watch_inbox,
    watch_identity_path,
)
from orchestrator import run_pipeline
from orchestrator_diagnostics import OrchestratorDiagnostic
from task_contract import TaskContractError
from workflow import WorkflowExecutionError, WorkflowHistory, WorkflowRunResult
from workflow_state import GateReason, ProtocolBinding, ProtocolMode, init_workflow_state


def _append_test_record(
    repository: Path,
    run_id: str,
    payload: object,
    logical_id: str,
) -> None:
    ArtifactBridge(ArtifactStore(repository, run_id)).append(
        payload,  # type: ignore[arg-type]
        logical_id=logical_id,
        idempotency_key=f"test:{logical_id}",
        fingerprint_sha256="a" * 64,
        fingerprint_kind=FingerprintKind.IMPLEMENTATION,
    )


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
    monkeypatch.chdir(tmp_path)
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


def test_pre_baseline_replay_halt_writes_readable_diagnostic_and_keeps_task(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    monkeypatch.chdir(tmp_path)
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir()
    task = inbox / "handoff.md"
    task.write_text("Implement the imported handoff", encoding="utf-8")
    args = parse_args(
        [
            "--task-file", str(task),
            "--inbox-dir", str(inbox),
            "--outbox-dir", str(outbox),
        ],
        cwd=tmp_path,
        environ={},
    )

    def fail_before_baseline(_task, run_args, **_kwargs):
        _append_test_record(
            tmp_path,
            run_args.watch_run_id,
            TaskPayload("feature/handoff", ("src/handoff.py",), "b" * 64),
            "handoff-import",
        )
        raise ArtifactReplayError(
            ReplayDiagnostic(
                ReplayDiagnosticCode.RECORD_MISSING,
                "record chain requires exactly one run identity and run profile",
            )
        )

    monkeypatch.setattr(orchestrator, "run_production_workflow", fail_before_baseline)
    caplog.set_level("INFO")

    assert watch_inbox(
        inbox_dir=inbox,
        outbox_dir=outbox,
        poll_interval=0.01,
        args=args,
        process_task=run_pipeline,
        min_file_age_seconds=0,
        time_fn=lambda: 10_000_000_000.0,
    ) == 4

    identity = WatchTaskIdentity.from_dict(
        json.loads(watch_identity_path(task).read_text(encoding="utf-8"))
    )
    report_path = pre_baseline_halt_diagnostic_path(tmp_path, identity.run_id)
    report_text = report_path.read_text(encoding="utf-8")
    report = json.loads(report_text)
    readable_cause = (
        "ArtifactReplayError: RECORD-MISSING: record chain requires exactly one "
        "run identity and run profile"
    )

    assert report == {
        "version": 1,
        "recorded_at": report["recorded_at"],
        "failure_class": "resumable_halt",
        "diagnostic_code": "ARTIFACT-REPLAY",
        "run_id": identity.run_id,
        "step": "pipeline",
        "last_cause": readable_cause,
        "provider_text": None,
        "provider_text_sha256": None,
        "provider_text_bytes": 0,
    }
    assert report["recorded_at"].endswith("+00:00")
    assert (
        "RECORD-MISSING: record chain requires exactly one run identity and run profile"
        in report_text
    )
    assert task.exists()
    assert len(ArtifactStore(tmp_path, identity.run_id).load_chain()) == 1
    assert not (tmp_path / ".orchestrator" / "state.json").exists()
    assert list((outbox / "failed").iterdir()) == []
    assert "State-v3 workflow failed: class=resumable_halt diagnostic=ARTIFACT-REPLAY" in caplog.text
    assert "Pausing watch queue for resumable task handoff.md" in caplog.text


def test_pre_baseline_halt_separates_readable_orchestrator_cause_from_provider_text(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    task = tmp_path / "task.md"
    task.write_text("Implement the bounded task", encoding="utf-8")
    args = parse_args(["--task-file", str(task)], cwd=tmp_path, environ={})
    args.watch_run_id = "watch-provider-halt"
    provider_text = "provider-secret-must-stay-out-of-the-diagnostic"
    diagnostic = OrchestratorDiagnostic.SLICE_PLAN_PATHS_INVALID

    def fail(*_args, **_kwargs):
        raise AgentOutputError(
            "slice plan failed local validation",
            provider_text=provider_text,
            orchestrator_diagnostic=diagnostic,
        )

    monkeypatch.setattr(orchestrator, "run_production_workflow", fail)

    result = run_pipeline(task, args)

    assert isinstance(result, WatchTaskResult)
    assert result.disposition is WatchTaskDisposition.RESUMABLE_HALT
    report_path = pre_baseline_halt_diagnostic_path(tmp_path, args.watch_run_id)
    report_text = report_path.read_text(encoding="utf-8")
    report = json.loads(report_text)
    provider_bytes = provider_text.encode("utf-8")
    provider_sha256 = hashlib.sha256(provider_bytes).hexdigest()
    assert report["last_cause"] == diagnostic.text
    assert report["provider_text"] == (
        f"[provider text redacted; sha256={provider_sha256}; "
        f"utf8_bytes={len(provider_bytes)}]"
    )
    assert report["provider_text_sha256"] == provider_sha256
    assert report["provider_text_bytes"] == len(provider_bytes)
    assert provider_text not in report_text


def test_pre_baseline_halt_redacts_text_with_unknown_source(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    task = tmp_path / "task.md"
    task.write_text("Implement the bounded task", encoding="utf-8")
    args = parse_args(["--task-file", str(task)], cwd=tmp_path, environ={})
    args.watch_run_id = "watch-unknown-halt"
    unknown_text = "text-without-a-trustworthy-source-boundary"

    def fail(*_args, **_kwargs):
        raise RuntimeError(unknown_text)

    monkeypatch.setattr(orchestrator, "run_production_workflow", fail)

    result = run_pipeline(task, args)

    assert isinstance(result, WatchTaskResult)
    assert result.disposition is WatchTaskDisposition.RESUMABLE_HALT
    report_path = pre_baseline_halt_diagnostic_path(tmp_path, args.watch_run_id)
    report_text = report_path.read_text(encoding="utf-8")
    report = json.loads(report_text)
    provider_source = f"RuntimeError: {unknown_text}"
    provider_bytes = provider_source.encode("utf-8")
    provider_sha256 = hashlib.sha256(provider_bytes).hexdigest()
    assert report["last_cause"] == (
        "RuntimeError: unclassified detail redacted (UNCLASSIFIED-ERROR)"
    )
    assert report["provider_text"] == (
        f"[provider text redacted; sha256={provider_sha256}; "
        f"utf8_bytes={len(provider_bytes)}]"
    )
    assert report["provider_text_sha256"] == provider_sha256
    assert report["provider_text_bytes"] == len(provider_bytes)
    assert unknown_text not in report_text


def test_halt_with_identity_profile_baseline_has_no_second_diagnostic(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    task = tmp_path / "task.md"
    task.write_text("Implement the bounded task", encoding="utf-8")
    run_id = "watch-baseline-halt"
    _append_test_record(
        tmp_path,
        run_id,
        RunIdentityPayload(
            str(task), "feature/baseline", "b" * 40, "b" * 40, "IMPLEMENT", None
        ),
        "run-identity",
    )
    _append_test_record(
        tmp_path,
        run_id,
        RunProfilePayload(
            RoleProfilePayload("implementer-model", "medium"),
            RoleProfilePayload("reviewer-model", "high"),
        ),
        "run-profile",
    )
    args = parse_args(["--task-file", str(task)], cwd=tmp_path, environ={})
    args.watch_run_id = run_id

    def fail(*_args, **_kwargs):
        raise WorkflowExecutionError("baseline-bound halt")

    monkeypatch.setattr(orchestrator, "run_production_workflow", fail)

    result = run_pipeline(task, args)

    assert isinstance(result, WatchTaskResult)
    assert result.disposition is WatchTaskDisposition.RESUMABLE_HALT
    assert result.resume_available is True
    assert len(ArtifactStore(tmp_path, run_id).load_chain()) == 2
    assert not pre_baseline_halt_diagnostic_path(tmp_path, run_id).exists()


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
    monkeypatch.chdir(tmp_path)
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
