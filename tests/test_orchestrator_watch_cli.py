from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import orchestrator
from cli import parse_args
from inbox_watcher import WatchTaskDisposition, WatchTaskResult
from orchestrator import run_pipeline
from workflow import WorkflowExecutionError


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
    assert result.disposition is WatchTaskDisposition.TECHNICAL_FAILURE
    assert result.failure_detail == (
        "WorkflowExecutionError: plan parser rejected heading"
    )


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
