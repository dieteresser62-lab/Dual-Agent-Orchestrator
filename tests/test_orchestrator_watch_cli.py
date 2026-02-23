from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest
from orchestrator import main, parse_args


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
