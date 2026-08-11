from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from orchestrator import RunContext, main, parse_args, run_pipeline


def _require_git_worktree(repo_root: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or result.stdout.strip() != "true":
        pytest.skip("test requires a git worktree")


def test_watch_mode_forwards_max_retries(monkeypatch, tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    captured: dict = {}

    def fake_watch_inbox(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return 0

    monkeypatch.setattr("orchestrator.watch_inbox", fake_watch_inbox)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "orchestrator",
            "--watch",
            "--inbox-dir",
            str(inbox),
            "--outbox-dir",
            str(outbox),
            "--watch-max-retries",
            "5",
        ],
    )

    result = main()

    assert result == 0
    assert captured["max_retries"] == 5
    assert captured["inbox_dir"] == inbox
    assert captured["outbox_dir"] == outbox


def test_watch_mode_rejects_legacy_max_retries_flag(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["orchestrator", "--watch", "--max-retries", "3"])

    with pytest.raises(SystemExit):
        parse_args()


def test_warns_when_watch_max_retries_used_without_watch(monkeypatch, caplog, tmp_path: Path) -> None:
    task_file = tmp_path / "task.md"
    task_file.write_text("x", encoding="utf-8")
    monkeypatch.setattr("orchestrator.find_task_file", lambda path: task_file)
    monkeypatch.setattr("orchestrator.run_pipeline", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(sys, "argv", ["orchestrator", "--watch-max-retries", "5"])

    with caplog.at_level(logging.WARNING):
        result = main()

    assert result == 0
    assert "--watch-max-retries is only used in --watch mode." in caplog.text


def test_parse_args_skip_git_check_defaults_false(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["orchestrator"])

    args = parse_args()

    assert args.skip_git_check is False


def test_parse_args_skip_git_check_flag(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["orchestrator", "--skip-git-check"])

    args = parse_args()

    assert args.skip_git_check is True


def test_parse_args_rejects_removed_gemini_fallback_flag(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["orchestrator", "--allow-fallback-to-gemini"])

    with pytest.raises(SystemExit) as exc_info:
        parse_args()

    assert exc_info.value.code != 0
    assert (
        "unrecognized arguments: --allow-fallback-to-gemini"
        in capsys.readouterr().err
    )


def test_run_task_never_forwards_removed_gemini_fallback_flag() -> None:
    wrapper = Path(__file__).resolve().parents[1] / "run_task"

    assert "--allow-fallback-to-gemini" not in wrapper.read_text(encoding="utf-8")


def test_run_task_is_checked_out_with_lf_endings() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    _require_git_worktree(repo_root)

    tracked_attributes = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", ".gitattributes"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert tracked_attributes.returncode == 0

    result = subprocess.run(
        ["git", "check-attr", "--cached", "eol", "--", "run_task"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "run_task: eol: lf"


def test_run_task_is_tracked_as_executable() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    _require_git_worktree(repo_root)

    result = subprocess.run(
        ["git", "ls-files", "-s", "--", "run_task"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    if not result.stdout.strip():
        pytest.skip("run_task is not tracked in this worktree")
    assert result.stdout.startswith("100755 ")


def test_run_pipeline_preflight_requires_only_active_workflow_agents(
    monkeypatch, tmp_path: Path
) -> None:
    task_file = tmp_path / "task.md"
    task_file.write_text("Test task", encoding="utf-8")
    captured: dict[str, object] = {}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "orchestrator",
            "--dry-run",
            "--force-overwrite-state",
            "--task-file",
            str(task_file),
        ],
    )

    def fake_preflight(
        self: RunContext,
        required_agents: list[str],
        strict: bool,
        *,
        skip_git_check: bool = False,
    ) -> bool:
        _ = self
        captured["required_agents"] = required_agents
        captured["strict"] = strict
        captured["skip_git_check"] = skip_git_check
        return False

    monkeypatch.setattr(RunContext, "preflight", fake_preflight)

    assert run_pipeline(task_file, parse_args()) == 1
    assert captured["required_agents"] == ["claude", "codex"]
