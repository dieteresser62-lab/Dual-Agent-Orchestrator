from __future__ import annotations

import os
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from inbox_watcher import watch_inbox

try:
    import fcntl
except ImportError:  # pragma: no cover - non-Unix fallback
    fcntl = None


def _args() -> Namespace:
    return Namespace(
        dry_run=True,
        agent_output="none",
        agent_output_max_chars=1800,
        agent_live_stream=False,
        agent_live_stream_mode="compact",
        agent_live_stream_channels="both",
        allow_fallback_to_gemini=False,
        test_command="",
        resume=False,
        no_recover=False,
        force_overwrite_state=False,
        phase1_max_cycles=1,
        phase2_max_cycles=1,
        from_phase=None,
        strict_preflight=False,
        max_agent_retries=0,
        manual_gate=False,
        max_shared_chars=1000,
        file_snapshot_max_lines=100,
        file_snapshot_max_files=5,
    )


class _InterruptingSleep:
    def __init__(self, interrupt_after: int) -> None:
        self.calls = 0
        self._interrupt_after = interrupt_after

    def __call__(self, _: float) -> None:
        self.calls += 1
        if self.calls >= self._interrupt_after:
            raise KeyboardInterrupt


def test_watch_picks_up_md_file_and_moves_to_outbox(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir()
    task = inbox / "task.md"
    task.write_text("hello", encoding="utf-8")
    calls: list[Path] = []

    def process_task(task_file: Path, _: Namespace, force_new: bool) -> int:
        calls.append(task_file)
        assert force_new is True
        return 0

    sleeper = _InterruptingSleep(interrupt_after=1)
    now = task.stat().st_mtime + 2.0
    result = watch_inbox(
        inbox_dir=inbox,
        outbox_dir=outbox,
        poll_interval=0.01,
        args=_args(),
        process_task=process_task,
        min_file_age_seconds=1.0,
        sleep_fn=sleeper,
        time_fn=lambda: now,
    )

    assert result == 0
    assert calls == [task]
    moved = list((outbox / "done").glob("*.md"))
    assert len(moved) == 1
    assert moved[0].name.endswith("_task.md")
    assert not task.exists()


def test_watch_ignores_non_md_files(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir()
    (inbox / "ignore.txt").write_text("x", encoding="utf-8")
    calls: list[Path] = []

    sleeper = _InterruptingSleep(interrupt_after=1)
    result = watch_inbox(
        inbox_dir=inbox,
        outbox_dir=outbox,
        poll_interval=0.01,
        args=_args(),
        process_task=lambda task_file, _args, _force_new: calls.append(task_file) or 0,
        sleep_fn=sleeper,
        time_fn=lambda: 10_000.0,
    )

    assert result == 0
    assert calls == []
    assert list((outbox / "done").glob("*")) == []
    assert list((outbox / "failed").glob("*")) == []


def test_watch_fifo_order_by_mtime(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir()
    first = inbox / "a.md"
    second = inbox / "b.md"
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")
    first_mtime = first.stat().st_mtime - 20
    second_mtime = second.stat().st_mtime - 10
    os.utime(first, (first_mtime, first_mtime))
    os.utime(second, (second_mtime, second_mtime))
    calls: list[str] = []

    def process_task(task_file: Path, _: Namespace, _force_new: bool) -> int:
        calls.append(task_file.name)
        return 0

    sleeper = _InterruptingSleep(interrupt_after=1)
    now = max(first.stat().st_mtime, second.stat().st_mtime) + 2.0
    result = watch_inbox(
        inbox_dir=inbox,
        outbox_dir=outbox,
        poll_interval=0.01,
        args=_args(),
        process_task=process_task,
        sleep_fn=sleeper,
        time_fn=lambda: now,
    )

    assert result == 0
    assert calls == ["a.md", "b.md"]


def test_outbox_name_collision_is_resolved(tmp_path: Path, monkeypatch) -> None:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir()
    first = inbox / "job.md"
    first.write_text("one", encoding="utf-8")
    calls = {"count": 0}

    class _FixedDateTime:
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            return datetime(2026, 2, 22, 9, 30, 0, 123000, tzinfo=timezone.utc)

    monkeypatch.setattr("inbox_watcher.datetime", _FixedDateTime)

    def process_task(task_file: Path, _: Namespace, _force_new: bool) -> int:
        calls["count"] += 1
        return 0

    sleep_state = {"count": 0}

    def sleeper(_: float) -> None:
        sleep_state["count"] += 1
        if sleep_state["count"] == 1:
            (inbox / "job.md").write_text("two", encoding="utf-8")
            return
        raise KeyboardInterrupt

    result = watch_inbox(
        inbox_dir=inbox,
        outbox_dir=outbox,
        poll_interval=0.01,
        args=_args(),
        process_task=process_task,
        sleep_fn=sleeper,
        time_fn=lambda: 10_000_000_000.0,
    )

    assert result == 0
    moved = sorted(path.name for path in (outbox / "done").glob("*.md"))
    assert len(moved) == 2
    assert moved[0] == "20260222T093000.123Z_job.md"
    assert moved[1] == "20260222T093000.123Z_job_1.md"


def test_watch_continues_after_pipeline_failure_exit_code(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir()
    (inbox / "bad.md").write_text("x", encoding="utf-8")
    calls: list[str] = []

    def process_task(task_file: Path, _: Namespace, _force_new: bool) -> int:
        calls.append(task_file.name)
        return 1

    sleeper = _InterruptingSleep(interrupt_after=1)
    result = watch_inbox(
        inbox_dir=inbox,
        outbox_dir=outbox,
        poll_interval=0.01,
        args=_args(),
        process_task=process_task,
        max_retries=1,
        sleep_fn=sleeper,
        time_fn=lambda: (inbox / "bad.md").stat().st_mtime + 2.0 if (inbox / "bad.md").exists() else 10_000.0,
    )

    assert result == 0
    assert calls == ["bad.md"]
    assert not (inbox / "bad.md").exists()
    assert len(list((outbox / "failed").glob("*.poison"))) == 1


def test_watch_creates_directories_and_exits_on_keyboard_interrupt(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    assert not inbox.exists()
    assert not outbox.exists()

    sleeper = _InterruptingSleep(interrupt_after=2)
    result = watch_inbox(
        inbox_dir=inbox,
        outbox_dir=outbox,
        poll_interval=0.01,
        args=_args(),
        process_task=lambda *_: 0,
        sleep_fn=sleeper,
        time_fn=lambda: 10_000.0,
    )

    assert result == 0
    assert inbox.exists()
    assert outbox.exists()
    assert (outbox / "done").exists()
    assert (outbox / "failed").exists()


def test_watch_skips_too_fresh_files_until_stable(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir()
    task = inbox / "fresh.md"
    task.write_text("x", encoding="utf-8")
    base_mtime = task.stat().st_mtime
    timeline = iter([base_mtime + 0.2, base_mtime + 1.5])
    calls: list[str] = []

    def process_task(task_file: Path, _: Namespace, _force_new: bool) -> int:
        calls.append(task_file.name)
        return 0

    sleeper = _InterruptingSleep(interrupt_after=2)
    current_time = {"value": base_mtime + 1.5}

    def next_time() -> float:
        try:
            current_time["value"] = next(timeline)
        except StopIteration:
            pass
        return current_time["value"]

    result = watch_inbox(
        inbox_dir=inbox,
        outbox_dir=outbox,
        poll_interval=0.01,
        args=_args(),
        process_task=process_task,
        min_file_age_seconds=1.0,
        sleep_fn=sleeper,
        time_fn=next_time,
    )

    assert result == 0
    assert calls == ["fresh.md"]


def test_failure_retries_then_poison(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir()
    task = inbox / "bad.md"
    task.write_text("x", encoding="utf-8")
    calls: list[str] = []

    def process_task(task_file: Path, _: Namespace, _force_new: bool) -> int:
        calls.append(task_file.name)
        return 1

    sleeper = _InterruptingSleep(interrupt_after=1)
    result = watch_inbox(
        inbox_dir=inbox,
        outbox_dir=outbox,
        poll_interval=0.01,
        args=_args(),
        process_task=process_task,
        max_retries=3,
        sleep_fn=sleeper,
        time_fn=lambda: task.stat().st_mtime + 2.0 if task.exists() else 10_000.0,
    )

    assert result == 0
    assert calls == ["bad.md", "bad.md", "bad.md"]
    failed = list((outbox / "failed").glob("*.poison"))
    assert len(failed) == 1
    assert not task.exists()
    assert not (inbox / "bad.md.attempts").exists()


def test_retry_count_survives_restart(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir()
    task = inbox / "restart.md"
    task.write_text("x", encoding="utf-8")
    base_mtime = task.stat().st_mtime

    first_run_calls = {"count": 0}
    first_times = iter([base_mtime + 2.0, base_mtime + 2.0, base_mtime + 0.0])

    def first_process(_: Path, __: Namespace, ___: bool) -> int:
        first_run_calls["count"] += 1
        return 1

    first_result = watch_inbox(
        inbox_dir=inbox,
        outbox_dir=outbox,
        poll_interval=0.01,
        args=_args(),
        process_task=first_process,
        max_retries=3,
        sleep_fn=_InterruptingSleep(interrupt_after=1),
        time_fn=lambda: next(first_times),
    )
    assert first_result == 0
    assert first_run_calls["count"] == 2
    assert task.exists()
    assert (inbox / "restart.md.attempts").read_text(encoding="utf-8").strip() == "2"

    second_calls: list[str] = []

    def second_process(task_file: Path, _: Namespace, _force_new: bool) -> int:
        second_calls.append(task_file.name)
        return 1

    second_result = watch_inbox(
        inbox_dir=inbox,
        outbox_dir=outbox,
        poll_interval=0.01,
        args=_args(),
        process_task=second_process,
        max_retries=3,
        sleep_fn=_InterruptingSleep(interrupt_after=1),
        time_fn=lambda: task.stat().st_mtime + 2.0 if task.exists() else 10_000.0,
    )
    assert second_result == 0
    assert second_calls == ["restart.md"]
    assert not task.exists()
    assert len(list((outbox / "failed").glob("*.poison"))) == 1
    assert not (inbox / "restart.md.attempts").exists()


def test_attempt_sidecar_is_removed_after_success(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir()
    task = inbox / "ok.md"
    task.write_text("x", encoding="utf-8")
    (inbox / "ok.md.attempts").write_text("2", encoding="utf-8")

    result = watch_inbox(
        inbox_dir=inbox,
        outbox_dir=outbox,
        poll_interval=0.01,
        args=_args(),
        process_task=lambda *_: 0,
        sleep_fn=_InterruptingSleep(interrupt_after=1),
        time_fn=lambda: task.stat().st_mtime + 2.0 if task.exists() else 10_000.0,
    )
    assert result == 0
    assert not (inbox / "ok.md.attempts").exists()
    assert len(list((outbox / "done").glob("*.md"))) == 1


def test_done_move_failure_counts_retries_and_poison_pills(tmp_path: Path, monkeypatch) -> None:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir()
    task = inbox / "ok.md"
    task.write_text("x", encoding="utf-8")
    calls: list[str] = []

    def process_task(task_file: Path, _: Namespace, _force_new: bool) -> int:
        calls.append(task_file.name)
        return 0

    def fake_move_to_outbox(task_file: Path, outbox_subdir: Path, *, source_name: str | None = None) -> Path:
        if outbox_subdir.name == "done":
            raise RuntimeError("simulated done move failure")
        destination = outbox_subdir / f"fake_{source_name or task_file.name}"
        task_file.rename(destination)
        return destination

    monkeypatch.setattr("inbox_watcher.move_to_outbox", fake_move_to_outbox)

    result = watch_inbox(
        inbox_dir=inbox,
        outbox_dir=outbox,
        poll_interval=0.01,
        args=_args(),
        process_task=process_task,
        max_retries=2,
        sleep_fn=_InterruptingSleep(interrupt_after=1),
        time_fn=lambda: task.stat().st_mtime + 2.0 if task.exists() else 10_000.0,
    )

    assert result == 0
    assert calls == ["ok.md", "ok.md"]
    assert not task.exists()
    assert len(list((outbox / "failed").glob("*.poison"))) == 1
    assert len(list((outbox / "done").glob("*.md"))) == 0
    assert not (inbox / "ok.md.attempts").exists()


def test_watch_fails_fast_when_lock_already_held(tmp_path: Path) -> None:
    if fcntl is None:
        pytest.skip("fcntl not available on this platform")

    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir(parents=True, exist_ok=True)
    lock_file = (inbox / ".lock").open("a+", encoding="utf-8")
    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        result = watch_inbox(
            inbox_dir=inbox,
            outbox_dir=outbox,
            poll_interval=0.01,
            args=_args(),
            process_task=lambda *_: 0,
            sleep_fn=_InterruptingSleep(interrupt_after=1),
            time_fn=lambda: 10_000.0,
        )
    finally:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()

    assert result == 1
