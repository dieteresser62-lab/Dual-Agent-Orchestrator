from __future__ import annotations

import json
import os
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from inbox_watcher import (
    QueueFinalizationDisposition,
    WatchTaskDisposition,
    WatchTaskIdentity,
    WatchTaskResult,
    attempt_sidecar_path,
    finalize_queue_success,
    success_marker_path,
    save_watch_identity,
    watch_identity_path,
    watch_inbox,
)
from workflow import WorkflowHistory, WorkflowRunResult
from workflow_state import (
    GateReason,
    ProtocolBinding,
    ProtocolMode,
    WorkflowStep,
    WorkUnitKind,
    init_workflow_state,
)

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
        skip_git_check=True,
    )


def _workflow_result(run_id: str, *, final: bool) -> WorkflowRunResult:
    state = init_workflow_state(
        run_id=run_id,
        task_file="/repo/task.md",
        branch="feature/watch",
        branch_base="a" * 40,
        slice_count=2,
        timestamp="2026-08-13T10:00:00+00:00",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
    ).complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit="a" * 40,
        scope_paths=("src/engine.py",),
        start_fingerprint="0" * 64,
    ).complete_current_slice(commit_ref="b" * 40).start_work_unit(
        slice_id=2,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
        slice_start_commit="b" * 40,
    ).bind_current_slice_git_boundary(
        start_commit="b" * 40,
        scope_paths=("src/second.py",),
        start_fingerprint="1" * 64,
    ).complete_current_slice(commit_ref="c" * 40)
    if final:
        state = state.start_final_review_work_unit().complete_current_work_unit()
    return WorkflowRunResult(state, WorkflowHistory(state.current_work_unit_id))


class _InterruptingSleep:
    """Test helper that stops infinite watch loops after N sleep calls."""

    def __init__(self, interrupt_after: int) -> None:
        self.calls = 0
        self._interrupt_after = interrupt_after

    def __call__(self, _: float) -> None:
        self.calls += 1
        if self.calls >= self._interrupt_after:
            raise KeyboardInterrupt


def _bound_queue_task(tmp_path: Path) -> tuple[Path, Path, Path, WatchTaskIdentity]:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir()
    task = inbox / "bound.md"
    task.write_text("bound payload", encoding="utf-8")
    digest = __import__("hashlib").sha256(task.read_bytes()).hexdigest()
    identity = WatchTaskIdentity(
        "watch-bound", digest, True, "structured-v2", 2
    )
    save_watch_identity(task, identity)
    return inbox, outbox, task, identity


def test_bound_queue_success_reserves_once_and_recovers_after_move(tmp_path: Path) -> None:
    inbox, outbox, task, identity = _bound_queue_task(tmp_path)
    attempt_sidecar_path(task).write_text("2", encoding="utf-8")

    first = finalize_queue_success(
        task,
        inbox_dir=inbox,
        outbox_dir=outbox,
        run_id=identity.run_id,
        task_digest=identity.task_digest,
        publish=True,
    )

    assert first.disposition is QueueFinalizationDisposition.COMPLETED
    assert first.destination is not None
    assert first.destination.read_text(encoding="utf-8") == "bound payload"
    assert len(list((outbox / "done").glob("*.md"))) == 1
    assert not attempt_sidecar_path(task).exists()
    assert not success_marker_path(task).exists()
    assert not watch_identity_path(task).exists()


@pytest.mark.parametrize(
    "boundary",
    ("before_move", "after_move", "attempt_cleanup", "identity_cleanup", "marker_cleanup"),
)
def test_bound_queue_success_converges_after_each_interruption_boundary(
    tmp_path: Path, monkeypatch, boundary: str
) -> None:
    inbox, outbox, task, identity = _bound_queue_task(tmp_path)
    attempt_sidecar_path(task).write_text("1", encoding="utf-8")
    import inbox_watcher as watcher

    target_name = {
        "before_move": "move_to_reserved_outbox",
        "after_move": "move_to_reserved_outbox",
        "attempt_cleanup": "delete_attempt_sidecar",
        "identity_cleanup": "delete_watch_identity",
        "marker_cleanup": "delete_success_marker",
    }[boundary]
    original = getattr(watcher, target_name)
    interrupted = {"done": False}

    def interrupt_once(*args, **kwargs):
        if interrupted["done"]:
            return original(*args, **kwargs)
        interrupted["done"] = True
        if boundary == "after_move":
            original(*args, **kwargs)
        raise OSError(f"interrupted at {boundary}")

    monkeypatch.setattr(watcher, target_name, interrupt_once)
    first = finalize_queue_success(
        task,
        inbox_dir=inbox,
        outbox_dir=outbox,
        run_id=identity.run_id,
        task_digest=identity.task_digest,
        publish=True,
    )
    second = finalize_queue_success(task, inbox_dir=inbox, outbox_dir=outbox)

    assert first.disposition is QueueFinalizationDisposition.FAILED
    assert second.disposition is QueueFinalizationDisposition.COMPLETED
    assert len(list((outbox / "done").glob("*.md"))) == 1
    assert not task.exists()
    assert not attempt_sidecar_path(task).exists()
    assert not success_marker_path(task).exists()
    assert not watch_identity_path(task).exists()


def test_bound_queue_success_rejects_source_swapped_to_symlink_before_move(
    tmp_path: Path, monkeypatch
) -> None:
    inbox, outbox, task, identity = _bound_queue_task(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("attacker-controlled payload", encoding="utf-8")
    import inbox_watcher as watcher

    original_move = watcher.move_to_reserved_outbox

    def swap_then_move(source: Path, destination: Path) -> Path:
        source.unlink()
        source.symlink_to(outside)
        return original_move(source, destination)

    monkeypatch.setattr(watcher, "move_to_reserved_outbox", swap_then_move)

    result = finalize_queue_success(
        task,
        inbox_dir=inbox,
        outbox_dir=outbox,
        run_id=identity.run_id,
        task_digest=identity.task_digest,
        publish=True,
    )

    assert result.disposition is QueueFinalizationDisposition.FAILED
    assert "regular non-symlink file" in (result.detail or "")
    assert task.is_symlink()
    assert outside.read_text(encoding="utf-8") == "attacker-controlled payload"
    assert not list((outbox / "done").glob("*.md"))


@pytest.mark.parametrize("field", ("run_id", "task_digest", "protocol_mode", "source", "destination"))
def test_bound_queue_success_rejects_tampered_evidence(
    tmp_path: Path, field: str, monkeypatch
) -> None:
    inbox, outbox, task, identity = _bound_queue_task(tmp_path)
    monkeypatch.setattr(
        "inbox_watcher.delete_attempt_sidecar",
        lambda _task: (_ for _ in ()).throw(OSError("interrupt cleanup")),
    )
    published = finalize_queue_success(
        task,
        inbox_dir=inbox,
        outbox_dir=outbox,
        run_id=identity.run_id,
        task_digest=identity.task_digest,
        publish=True,
    )
    assert published.disposition is QueueFinalizationDisposition.FAILED
    destination = published.destination
    document = json.loads(success_marker_path(task).read_text(encoding="utf-8"))
    destination = Path(document["destination"])
    assert destination.exists()

    marker = success_marker_path(task)
    replacements = {
        "run_id": "watch-other",
        "task_digest": "f" * 64,
        "protocol_mode": "legacy",
        "source": str((tmp_path / "outside.md").resolve()),
        "destination": str((tmp_path / "outside-done.md").resolve()),
    }
    document[field] = replacements[field]
    marker.write_text(json.dumps(document), encoding="utf-8")

    recovered = finalize_queue_success(task, inbox_dir=inbox, outbox_dir=outbox)

    assert recovered.disposition is QueueFinalizationDisposition.FAILED
    assert marker.exists()
    assert destination.exists()


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
        """Freeze timestamp generation so outbox-name collision behavior is deterministic."""

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
    report = Path(str(failed[0]) + ".error.json")
    assert report.is_file()
    report_data = json.loads(report.read_text(encoding="utf-8"))
    assert report_data["attempts"] == 3
    assert report_data["exit_code"] == 1
    assert report_data["failure_detail"] == "legacy exit code 1"
    assert not task.exists()
    assert not (inbox / "bad.md.attempts").exists()


def test_retry_count_survives_restart(tmp_path: Path) -> None:
    # Sidecar retry counter must survive process restart and continue from prior attempts.
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


def test_typed_technical_failure_detail_is_preserved_in_poison_report(
    tmp_path: Path,
) -> None:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir()
    task = inbox / "diagnostic.md"
    task.write_text("x", encoding="utf-8")

    def process_task(
        _task: Path, args: Namespace, _force_new: bool
    ) -> WatchTaskResult:
        return WatchTaskResult(
            exit_code=1,
            run_id=args.watch_run_id,
            disposition=WatchTaskDisposition.TECHNICAL_FAILURE,
            status="technical_failure",
            step="claude_plan_review",
            work_unit_id=1,
            gate_reason="technical_failure",
            failure_detail="WorkflowExecutionError: invalid plan contract",
        )

    result = watch_inbox(
        inbox_dir=inbox,
        outbox_dir=outbox,
        poll_interval=0.01,
        args=_args(),
        process_task=process_task,
        max_retries=1,
        sleep_fn=_InterruptingSleep(interrupt_after=1),
        time_fn=lambda: 10_000_000_000.0,
    )

    assert result == 0
    report = next((outbox / "failed").glob("*.poison.error.json"))
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["run_id"]
    assert data["step"] == "claude_plan_review"
    assert data["failure_detail"] == (
        "WorkflowExecutionError: invalid plan contract"
    )


def test_legacy_watch_identity_roundtrip_does_not_add_protocol_binding() -> None:
    raw = {
        "version": 1,
        "run_id": "watch-old",
        "task_digest": "a" * 64,
        "started": True,
    }

    identity = WatchTaskIdentity.from_dict(raw)

    assert identity.protocol_mode is None
    assert identity.sidecar_version == 1
    assert identity.to_dict() == raw


def test_structured_watch_identity_roundtrip_binds_protocol_mode() -> None:
    identity = WatchTaskIdentity(
        "watch-new", "a" * 64, True, "structured-v2"
    )

    assert WatchTaskIdentity.from_dict(identity.to_dict()) == identity
    assert identity.to_dict()["version"] == 2


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
    assert calls == ["ok.md"]
    assert not task.exists()
    assert len(list((outbox / "failed").glob("*.move_error"))) == 1
    assert len(list((outbox / "done").glob("*.md"))) == 0
    assert not (inbox / "ok.md.attempts").exists()
    assert not (inbox / "ok.md.success").exists()


def test_success_marker_skips_reexecution(tmp_path: Path, monkeypatch) -> None:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir()
    task = inbox / "ok.md"
    task.write_text("x", encoding="utf-8")
    (inbox / "ok.md.success").write_text("already-done", encoding="utf-8")
    process_calls: list[str] = []
    move_attempts = {"done": 0}

    def process_task(task_file: Path, _: Namespace, _force_new: bool) -> int:
        process_calls.append(task_file.name)
        return 0

    def fake_move_to_outbox(task_file: Path, outbox_subdir: Path, *, source_name: str | None = None) -> Path:
        if outbox_subdir.name == "done":
            move_attempts["done"] += 1
            if move_attempts["done"] == 1:
                raise RuntimeError("transient move failure")
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
        max_retries=3,
        sleep_fn=_InterruptingSleep(interrupt_after=1),
        time_fn=lambda: task.stat().st_mtime + 2.0 if task.exists() else 10_000.0,
    )

    assert result == 0
    assert process_calls == []
    assert len(list((outbox / "done").glob("*.md"))) == 1
    assert not task.exists()
    assert not (inbox / "ok.md.success").exists()
    assert not (inbox / "ok.md.attempts").exists()


def test_move_error_suffix_for_succeeded_task(tmp_path: Path, monkeypatch) -> None:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir()
    task = inbox / "ok.md"
    task.write_text("x", encoding="utf-8")

    def fake_move_to_outbox(task_file: Path, outbox_subdir: Path, *, source_name: str | None = None) -> Path:
        if outbox_subdir.name == "done":
            raise RuntimeError("done move always fails")
        destination = outbox_subdir / f"fake_{source_name or task_file.name}"
        task_file.rename(destination)
        return destination

    monkeypatch.setattr("inbox_watcher.move_to_outbox", fake_move_to_outbox)

    result = watch_inbox(
        inbox_dir=inbox,
        outbox_dir=outbox,
        poll_interval=0.01,
        args=_args(),
        process_task=lambda *_: 0,
        max_retries=1,
        sleep_fn=_InterruptingSleep(interrupt_after=1),
        time_fn=lambda: task.stat().st_mtime + 2.0 if task.exists() else 10_000.0,
    )

    assert result == 0
    assert len(list((outbox / "failed").glob("*.move_error"))) == 1
    assert len(list((outbox / "failed").glob("*.poison"))) == 0
    assert not (inbox / "ok.md.success").exists()


def test_stuck_task_safety_net_renames_to_stuck(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir()
    task = inbox / "stuck.md"
    task.write_text("x", encoding="utf-8")
    (inbox / "stuck.md.attempts").write_text("6", encoding="utf-8")
    (inbox / "stuck.md.success").write_text("old", encoding="utf-8")
    calls: list[str] = []

    result = watch_inbox(
        inbox_dir=inbox,
        outbox_dir=outbox,
        poll_interval=0.01,
        args=_args(),
        process_task=lambda task_file, _args, _force_new: calls.append(task_file.name) or 0,
        max_retries=2,
        sleep_fn=_InterruptingSleep(interrupt_after=1),
        time_fn=lambda: task.stat().st_mtime + 2.0 if task.exists() else 10_000.0,
    )

    assert result == 0
    assert calls == []
    assert not task.exists()
    assert (inbox / "stuck.md.stuck").exists()
    assert not (inbox / "stuck.md.attempts").exists()
    assert not (inbox / "stuck.md.success").exists()


def test_workflow_result_requires_commits_and_completed_final_review() -> None:
    slice_only = WatchTaskResult.from_workflow(
        _workflow_result("watch-slice-only", final=False)
    )
    completed = WatchTaskResult.from_workflow(
        _workflow_result("watch-final", final=True)
    )
    gate_state = init_workflow_state(
        run_id="watch-gate",
        task_file="/repo/task.md",
        branch="feature/watch",
        branch_base="a" * 40,
        slice_count=1,
        timestamp="2026-08-13T10:00:00+00:00",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
    ).await_policy_gate(
        reason=GateReason.STOP_REQUEST,
        detail="S-001 | operator decision required",
    )
    halted = WatchTaskResult.from_workflow(
        WorkflowRunResult(gate_state, WorkflowHistory(1))
    )

    assert slice_only.disposition is WatchTaskDisposition.TECHNICAL_FAILURE
    assert slice_only.exit_code == 1
    assert completed.disposition is WatchTaskDisposition.COMPLETED
    assert completed.exit_code == 0
    assert halted.disposition is WatchTaskDisposition.RESUMABLE_HALT
    assert halted.exit_code == 4
    assert halted.failure_detail == "S-001 | operator decision required"


def test_bootstrap_denial_maps_to_resumable_watch_halt_before_provider_retry() -> None:
    state = init_workflow_state(
        run_id="watch-bootstrap",
        task_file="/repo/task.md",
        branch="feature/watch",
        branch_base="a" * 40,
        slice_count=1,
        timestamp="2026-08-13T10:00:00+00:00",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
    ).await_bootstrap_resume(
        detail="PROVIDER-INPUT-BUDGET | chars=101/100",
        fingerprint="b" * 64,
    )

    halted = WatchTaskResult.from_workflow(
        WorkflowRunResult(state, WorkflowHistory(1))
    )

    assert halted.disposition is WatchTaskDisposition.RESUMABLE_HALT
    assert halted.exit_code == 4
    assert halted.status == "awaiting_resume"
    assert halted.step == WorkflowStep.CODEX_PLAN.value
    assert halted.gate_reason == GateReason.BOOTSTRAP_CHECK.value
    assert halted.failure_detail == "PROVIDER-INPUT-BUDGET | chars=101/100"


@pytest.mark.parametrize(
    ("exit_code", "status", "gate_reason"),
    (
        (2, "awaiting_resume", "quota"),
        (3, "awaiting_resume", "instance_failure"),
        (4, "awaiting_user_decision", "stop_request"),
    ),
)
def test_resumable_v3_halt_stops_queue_without_retry_or_poison(
    tmp_path: Path,
    exit_code: int,
    status: str,
    gate_reason: str,
) -> None:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir()
    first = inbox / "first.md"
    second = inbox / "second.md"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    first_mtime = first.stat().st_mtime - 10
    os.utime(first, (first_mtime, first_mtime))
    calls: list[str] = []

    def process_task(task_file: Path, args: Namespace, force_new: bool) -> WatchTaskResult:
        calls.append(task_file.name)
        assert force_new is True
        assert args.resume is False
        return WatchTaskResult(
            exit_code=exit_code,
            run_id=args.watch_run_id,
            disposition=WatchTaskDisposition.RESUMABLE_HALT,
            status=status,
            step="claude_slice_review",
            work_unit_id=2,
            gate_reason=gate_reason,
        )

    result = watch_inbox(
        inbox_dir=inbox,
        outbox_dir=outbox,
        poll_interval=0.01,
        args=_args(),
        process_task=process_task,
        max_retries=1,
        time_fn=lambda: 10_000_000_000.0,
    )

    assert result == exit_code
    assert calls == ["first.md"]
    assert first.exists() and second.exists()
    assert not (inbox / "first.md.attempts").exists()
    assert watch_identity_path(first).exists()
    assert list((outbox / "failed").glob("*")) == []


def test_watch_restart_resumes_same_run_id_and_moves_only_final_workflow(
    tmp_path: Path,
) -> None:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir()
    task = inbox / "resume.md"
    task.write_text("resume", encoding="utf-8")
    run_ids: list[str] = []

    def pause(_task: Path, args: Namespace, force_new: bool) -> WatchTaskResult:
        run_ids.append(args.watch_run_id)
        assert force_new is True
        assert args.resume is False
        return WatchTaskResult(
            4,
            args.watch_run_id,
            WatchTaskDisposition.RESUMABLE_HALT,
            "awaiting_user_decision",
            "codex_implementation",
            2,
            "test_change",
        )

    first = watch_inbox(
        inbox_dir=inbox,
        outbox_dir=outbox,
        poll_interval=0.01,
        args=_args(),
        process_task=pause,
        time_fn=lambda: 10_000_000_000.0,
    )
    assert first == 4

    def finish(_task: Path, args: Namespace, force_new: bool) -> WatchTaskResult:
        run_ids.append(args.watch_run_id)
        assert force_new is False
        assert args.resume is True
        assert args.skip_git_check is True
        return WatchTaskResult.from_workflow(
            _workflow_result(args.watch_run_id, final=True)
        )

    second = watch_inbox(
        inbox_dir=inbox,
        outbox_dir=outbox,
        poll_interval=0.01,
        args=_args(),
        process_task=finish,
        sleep_fn=_InterruptingSleep(interrupt_after=1),
        time_fn=lambda: 10_000_000_000.0,
    )

    assert second == 0
    assert run_ids[0] == run_ids[1]
    assert not task.exists()
    assert not watch_identity_path(task).exists()
    assert len(list((outbox / "done").glob("*.md"))) == 1


def test_watch_keeps_unchanged_bootstrap_denial_resumable_until_external_repair(
    tmp_path: Path,
) -> None:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir()
    task = inbox / "bootstrap.md"
    task.write_text("bootstrap", encoding="utf-8")
    calls: list[tuple[str, bool, bool]] = []

    def deny(_task: Path, args: Namespace, force_new: bool) -> WatchTaskResult:
        calls.append((args.watch_run_id, args.resume, force_new))
        return WatchTaskResult(
            exit_code=4,
            run_id=args.watch_run_id,
            disposition=WatchTaskDisposition.RESUMABLE_HALT,
            status="awaiting_resume",
            step="codex_final_review",
            work_unit_id=4,
            gate_reason=GateReason.BOOTSTRAP_CHECK.value,
            failure_detail="FINAL-REVIEW-PREFLIGHT | restore matching records",
        )

    for _ in range(2):
        assert watch_inbox(
            inbox_dir=inbox,
            outbox_dir=outbox,
            poll_interval=0.01,
            args=_args(),
            process_task=deny,
            max_retries=1,
            time_fn=lambda: 10_000_000_000.0,
        ) == 4
        assert task.exists()
        assert not (inbox / "bootstrap.md.attempts").exists()
        assert list((outbox / "failed").glob("*")) == []

    def finish(_task: Path, args: Namespace, force_new: bool) -> WatchTaskResult:
        calls.append((args.watch_run_id, args.resume, force_new))
        return WatchTaskResult.from_workflow(
            _workflow_result(args.watch_run_id, final=True)
        )

    assert watch_inbox(
        inbox_dir=inbox,
        outbox_dir=outbox,
        poll_interval=0.01,
        args=_args(),
        process_task=finish,
        max_retries=1,
        sleep_fn=_InterruptingSleep(interrupt_after=1),
        time_fn=lambda: 10_000_000_000.0,
    ) == 0

    assert len({run_id for run_id, _, _ in calls}) == 1
    assert calls[0][1:] == (False, True)
    assert calls[1][1:] == (True, False)
    assert calls[2][1:] == (True, False)
    assert not task.exists()
    assert not watch_identity_path(task).exists()
    assert not (inbox / "bootstrap.md.attempts").exists()
    assert list((outbox / "failed").glob("*")) == []
    assert len(list((outbox / "done").glob("*.md"))) == 1


def test_non_resumable_policy_halt_stops_once_without_retry_or_poison(
    tmp_path: Path,
) -> None:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir()
    task = inbox / "state-contract.md"
    task.write_text("state conflict", encoding="utf-8")
    calls: list[tuple[bool, bool]] = []

    def halt(_task: Path, args: Namespace, force_new: bool) -> WatchTaskResult:
        calls.append((args.resume, force_new))
        return WatchTaskResult(
            exit_code=4,
            run_id=args.watch_run_id,
            disposition=WatchTaskDisposition.RESUMABLE_HALT,
            status="awaiting_user_decision",
            step="pipeline",
            work_unit_id=1,
            gate_reason="state_contract",
            failure_detail="StateSchemaError: deterministic conflict",
            resume_available=False,
        )

    result = watch_inbox(
        inbox_dir=inbox,
        outbox_dir=outbox,
        poll_interval=0.01,
        args=_args(),
        process_task=halt,
        max_retries=3,
        time_fn=lambda: 10_000_000_000.0,
    )

    assert result == 4
    assert calls == [(False, True)]
    assert task.exists()
    assert not (inbox / "state-contract.md.attempts").exists()
    assert list((outbox / "failed").glob("*")) == []
    identity = WatchTaskIdentity.from_dict(
        json.loads(watch_identity_path(task).read_text(encoding="utf-8"))
    )
    assert identity.started is False


def test_watch_processes_generated_implementation_handoff_without_restart(
    tmp_path: Path,
) -> None:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir()
    plan = inbox / "feature-plan.md"
    plan.write_text("plan", encoding="utf-8")
    calls: list[str] = []

    def process(task: Path, args: Namespace, force_new: bool) -> WatchTaskResult:
        calls.append(task.name)
        if task.name == "feature-plan.md":
            (inbox / "feature-implement.md").write_text(
                "implementation handoff", encoding="utf-8"
            )
        return WatchTaskResult.from_workflow(
            _workflow_result(args.watch_run_id, final=True)
        )

    result = watch_inbox(
        inbox_dir=inbox,
        outbox_dir=outbox,
        poll_interval=0.01,
        args=_args(),
        process_task=process,
        sleep_fn=_InterruptingSleep(interrupt_after=1),
        time_fn=lambda: 10_000_000_000.0,
    )

    assert result == 0
    assert calls == ["feature-plan.md", "feature-implement.md"]
    assert len(list((outbox / "done").glob("*.md"))) == 2


def test_process_interruption_preserves_identity_for_next_watch_process(
    tmp_path: Path,
) -> None:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir()
    task = inbox / "interrupt.md"
    task.write_text("interrupt", encoding="utf-8")
    first_run_id: list[str] = []

    def interrupt(_task: Path, args: Namespace, force_new: bool) -> int:
        first_run_id.append(args.watch_run_id)
        assert force_new is True
        raise KeyboardInterrupt

    assert watch_inbox(
        inbox_dir=inbox,
        outbox_dir=outbox,
        poll_interval=0.01,
        args=_args(),
        process_task=interrupt,
        time_fn=lambda: 10_000_000_000.0,
    ) == 0
    assert watch_identity_path(task).exists()

    def resume(_task: Path, args: Namespace, force_new: bool) -> WatchTaskResult:
        assert args.watch_run_id == first_run_id[0]
        assert args.resume is True
        assert force_new is False
        return WatchTaskResult.from_workflow(
            _workflow_result(args.watch_run_id, final=True)
        )

    assert watch_inbox(
        inbox_dir=inbox,
        outbox_dir=outbox,
        poll_interval=0.01,
        args=_args(),
        process_task=resume,
        sleep_fn=_InterruptingSleep(interrupt_after=1),
        time_fn=lambda: 10_000_000_000.0,
    ) == 0
    assert len(list((outbox / "done").glob("*.md"))) == 1


def test_technical_retry_uses_stable_run_id_and_resume_context(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir()
    task = inbox / "retry.md"
    task.write_text("retry", encoding="utf-8")
    calls: list[tuple[str, bool, bool]] = []

    def process(_task: Path, args: Namespace, force_new: bool) -> int:
        calls.append((args.watch_run_id, args.resume, force_new))
        return 1 if len(calls) == 1 else 0

    assert watch_inbox(
        inbox_dir=inbox,
        outbox_dir=outbox,
        poll_interval=0.01,
        args=_args(),
        process_task=process,
        max_retries=3,
        sleep_fn=_InterruptingSleep(interrupt_after=1),
        time_fn=lambda: 10_000_000_000.0,
    ) == 0

    assert calls[0][0] == calls[1][0]
    assert calls[0][1:] == (False, True)
    assert calls[1][1:] == (True, False)
    assert not (inbox / "retry.md.attempts").exists()


def test_pre_state_technical_retry_restarts_fresh_with_same_run_id(
    tmp_path: Path,
) -> None:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir()
    task = inbox / "preflight-retry.md"
    task.write_text("retry", encoding="utf-8")
    calls: list[tuple[str, bool, bool]] = []

    def process(
        _task: Path, args: Namespace, force_new: bool
    ) -> WatchTaskResult:
        calls.append((args.watch_run_id, args.resume, force_new))
        if len(calls) == 1:
            return WatchTaskResult(
                exit_code=1,
                run_id=args.watch_run_id,
                disposition=WatchTaskDisposition.TECHNICAL_FAILURE,
                status="technical_failure",
                step="pipeline",
                work_unit_id=1,
                gate_reason="technical_failure",
                failure_detail="GitTransactionError: preflight failed",
                resume_available=False,
            )
        return WatchTaskResult.from_workflow(
            _workflow_result(args.watch_run_id, final=True)
        )

    assert watch_inbox(
        inbox_dir=inbox,
        outbox_dir=outbox,
        poll_interval=0.01,
        args=_args(),
        process_task=process,
        max_retries=3,
        sleep_fn=_InterruptingSleep(interrupt_after=1),
        time_fn=lambda: 10_000_000_000.0,
    ) == 0

    assert calls[0][0] == calls[1][0]
    assert calls[0][1:] == (False, True)
    assert calls[1][1:] == (False, True)
    assert len(list((outbox / "done").glob("*.md"))) == 1


def test_fifo_tasks_receive_distinct_isolated_run_ids(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir()
    first = inbox / "first.md"
    second = inbox / "second.md"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    first_mtime = first.stat().st_mtime - 10
    os.utime(first, (first_mtime, first_mtime))
    run_ids: list[str] = []

    def process(_task: Path, args: Namespace, force_new: bool) -> int:
        assert force_new is True
        run_ids.append(args.watch_run_id)
        return 0

    assert watch_inbox(
        inbox_dir=inbox,
        outbox_dir=outbox,
        poll_interval=0.01,
        args=_args(),
        process_task=process,
        sleep_fn=_InterruptingSleep(interrupt_after=1),
        time_fn=lambda: 10_000_000_000.0,
    ) == 0

    assert len(run_ids) == 2
    assert run_ids[0] != run_ids[1]
    assert len(list((outbox / "done").glob("*.md"))) == 2


def test_changed_paused_task_halts_without_retry_or_poison(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir()
    task = inbox / "changed.md"
    task.write_text("original", encoding="utf-8")

    def pause(_task: Path, args: Namespace, _force_new: bool) -> WatchTaskResult:
        return WatchTaskResult(
            4,
            args.watch_run_id,
            WatchTaskDisposition.RESUMABLE_HALT,
            "awaiting_user_decision",
            "codex_implementation",
            2,
            "test_change",
        )

    assert watch_inbox(
        inbox_dir=inbox,
        outbox_dir=outbox,
        poll_interval=0.01,
        args=_args(),
        process_task=pause,
        time_fn=lambda: 10_000_000_000.0,
    ) == 4
    task.write_text("changed after pause", encoding="utf-8")
    calls: list[str] = []

    assert watch_inbox(
        inbox_dir=inbox,
        outbox_dir=outbox,
        poll_interval=0.01,
        args=_args(),
        process_task=lambda *_: calls.append("called") or 0,
        max_retries=1,
        time_fn=lambda: 10_000_000_000.0,
    ) == 1

    assert calls == []
    assert task.exists()
    assert watch_identity_path(task).exists()
    assert not (inbox / "changed.md.attempts").exists()
    assert list((outbox / "failed").glob("*")) == []


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
