from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest

import agent_runtime
import provider_process
from provider_process import (
    ProcessStatus,
    observe_process,
    process_evidence_path,
    record_attempt_baseline,
    record_process_start,
)


@pytest.mark.parametrize("live_stream", (False, True))
@pytest.mark.parametrize("unreadable", ("boot_id", "stat"))
def test_unmeasurable_identity_does_not_abort_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    live_stream: bool, unreadable: str,
) -> None:
    response = tmp_path / "response.json"
    record_attempt_baseline(response, "effect-1", {"src/example.py": {"kind": "file"}})
    evidence = process_evidence_path(response)
    baseline = evidence.read_bytes()
    original_read = Path.read_text

    def read_proc(path: Path, *args: object, **kwargs: object) -> str:
        name = str(path)
        if name == "/proc/sys/kernel/random/boot_id":
            if unreadable == "boot_id":
                raise FileNotFoundError("boot ID unavailable")
            return "boot-1\n"
        if name.startswith("/proc/") and name.endswith("/stat"):
            raise PermissionError("process stat denied")
        return original_read(path, *args, **kwargs)

    config = agent_runtime.OrchestratorConfig(
        repo_root=tmp_path, agent_live_stream=live_stream, agent_live_stream_mode="full",
    )
    command = [sys.executable, "-c", "print('completed')"]
    with monkeypatch.context() as measured_proc:
        measured_proc.setattr(provider_process, "_boot_id", lambda: "boot-1")
        measured_proc.setattr(provider_process, "_proc_stat", lambda _pid: ("R", 17))
        expected = agent_runtime._run_agent_process(
            object(), command, None, config=config, env=os.environ.copy(),
            execution_root=tmp_path, timeout_seconds=None, agent_key="implementer",
            process_started=lambda pid: record_process_start(
                tmp_path / "measured.json", "effect-2", pid,
            ),
        )
    monkeypatch.setattr(Path, "read_text", read_proc)
    actual = agent_runtime._run_agent_process(
        object(), command, None, config=config, env=os.environ.copy(),
        execution_root=tmp_path, timeout_seconds=None, agent_key="implementer",
        process_started=lambda pid: record_process_start(response, "effect-1", pid),
    )

    assert (actual.returncode, actual.stdout, actual.stderr) == (
        expected.returncode, expected.stdout, expected.stderr,
    )
    assert actual.stdout == "completed\n"
    assert evidence.read_bytes() == baseline
    assert "process identity unavailable" in caplog.text
    assert ("boot ID" if unreadable == "boot_id" else "stat denied") in caplog.text


@pytest.mark.parametrize("invalid", ("directory", "symlink", "foreign", "corrupt", "bad_before"))
def test_invalid_baseline_remains_fatal_when_proc_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid: str,
) -> None:
    response = tmp_path / "response.json"
    evidence = process_evidence_path(response)
    if invalid == "directory":
        evidence.mkdir()
    elif invalid == "symlink":
        evidence.symlink_to(tmp_path / "missing")
    elif invalid == "foreign":
        evidence.write_text(json.dumps({"effect_key": "other", "before": {}}))
    elif invalid == "corrupt":
        evidence.write_text("{")
    else:
        evidence.write_text(json.dumps({"effect_key": "effect-1", "before": []}))
    baseline = evidence.read_bytes() if evidence.is_file() else None
    probes = 0

    def unavailable_boot() -> None:
        nonlocal probes
        probes += 1
        return None

    monkeypatch.setattr(provider_process, "_boot_id", unavailable_boot)

    expected = json.JSONDecodeError if invalid == "corrupt" else RuntimeError
    with pytest.raises(expected):
        record_process_start(response, "effect-1", os.getpid())
    assert probes == 0
    if baseline is not None:
        assert evidence.read_bytes() == baseline


@pytest.mark.parametrize(
    ("boot", "stat", "expected"),
    (
        ("old-boot", ("S", 41), ProcessStatus.ENDED),
        ("current-boot", None, ProcessStatus.ENDED),
        ("current-boot", ("S", 42), ProcessStatus.ENDED),
        ("current-boot", ("Z", 41), ProcessStatus.ENDED),
        ("current-boot", ("S", 41), ProcessStatus.RUNNING),
    ),
)
def test_process_identity_requires_matching_boot_pid_and_start_ticks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    boot: str, stat: tuple[str, int] | None, expected: ProcessStatus,
) -> None:
    response = tmp_path / "response.json"
    monkeypatch.setattr(provider_process, "_boot_id", lambda: "current-boot")
    monkeypatch.setattr(provider_process, "_proc_stat", lambda _pid: ("S", 41))
    record_process_start(response, "effect-1", 711)
    monkeypatch.setattr(provider_process, "_boot_id", lambda: boot)
    monkeypatch.setattr(provider_process, "_proc_stat", lambda _pid: stat)
    assert observe_process(response, "effect-1").status is expected


def test_unreadable_process_state_and_foreign_identity_remain_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = tmp_path / "response.json"
    monkeypatch.setattr(provider_process, "_boot_id", lambda: "current-boot")
    monkeypatch.setattr(provider_process, "_proc_stat", lambda _pid: ("S", 41))
    record_process_start(response, "effect-1", 711)
    assert observe_process(response, "another-effect").status is ProcessStatus.UNKNOWN

    def unreadable(_pid: int) -> None:
        raise PermissionError("proc stat denied")

    monkeypatch.setattr(provider_process, "_proc_stat", unreadable)
    assert observe_process(response, "effect-1").status is ProcessStatus.UNKNOWN
