from __future__ import annotations

from pathlib import Path

import pytest

import agent_runtime
from agent_adapters import AGENT_REGISTRY, CodexAdapter, GeminiAdapter
from agent_runtime import (
    OrchestratorConfig,
    QuotaReachedError,
    check_git_clean,
    collect_file_snapshots,
    compute_retry_backoff_seconds,
    preflight,
    run_agent,
    run_agent_checked,
    run_tests_snapshot,
)


def test_compute_retry_backoff_seconds_exponential() -> None:
    assert compute_retry_backoff_seconds("generic error", 1) == 2
    assert compute_retry_backoff_seconds("generic error", 2) == 4
    assert compute_retry_backoff_seconds("generic error", 5) == 30


def test_compute_retry_backoff_seconds_rate_limit_floor() -> None:
    assert compute_retry_backoff_seconds("HTTP 429 too many requests", 1) == 10
    assert compute_retry_backoff_seconds("rate limit", 2) == 10
    assert compute_retry_backoff_seconds("rate limit", 5) == 30


def test_run_agent_checked_retries_with_backoff(monkeypatch, tmp_path: Path) -> None:
    calls: list[int] = []
    sleeps: list[int] = []

    def fake_run_agent(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("temporary network glitch")
        return "CODEX_APPROVAL: YES\nOPEN_FINDINGS: NONE\nSTATUS: DONE"

    monkeypatch.setattr(agent_runtime, "run_agent", fake_run_agent)
    monkeypatch.setattr(agent_runtime.time, "sleep", lambda sec: sleeps.append(sec))

    output = run_agent_checked(
        agent_key="codex",
        prompt="prompt",
        log_prefix="unit",
        max_retries=1,
        required_flags=["CODEX_APPROVAL"],
        output_validator=None,
        config=OrchestratorConfig(dry_run=False),
        agents={"codex": AGENT_REGISTRY["codex"]},
        log_dir=tmp_path,
        write_file=lambda path, content: path.write_text(content, encoding="utf-8"),
        shorten=lambda text, limit=1800: (text or "")[:limit],
        parse_flag=lambda text, key: "YES" if f"{key}: YES" in text else None,
        validate_done_marker=lambda text: text.strip().endswith("STATUS: DONE"),
    )

    assert "STATUS: DONE" in output
    assert len(calls) == 2
    assert sleeps == [2]


def test_run_agent_checked_validation_error_backoff(monkeypatch, tmp_path: Path) -> None:
    sleeps: list[int] = []
    calls: list[int] = []

    def fake_run_agent(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(1)
        return "CODEX_APPROVAL: YES\nSTATUS: DONE"

    monkeypatch.setattr(agent_runtime, "run_agent", fake_run_agent)
    monkeypatch.setattr(agent_runtime.time, "sleep", lambda sec: sleeps.append(sec))

    output = run_agent_checked(
        agent_key="codex",
        prompt="prompt",
        log_prefix="unit",
        max_retries=1,
        required_flags=[],
        output_validator=lambda _output: "not valid" if len(calls) == 1 else None,
        config=OrchestratorConfig(dry_run=False),
        agents={"codex": AGENT_REGISTRY["codex"]},
        log_dir=tmp_path,
        write_file=lambda path, content: path.write_text(content, encoding="utf-8"),
        shorten=lambda text, limit=1800: (text or "")[:limit],
        parse_flag=lambda text, key: "YES",
        validate_done_marker=lambda text: True,
    )

    assert "STATUS: DONE" in output
    assert len(calls) == 2
    assert sleeps == [2]


def test_run_agent_checked_sets_sticky_quota_flag_after_gemini_fallback(
    monkeypatch, tmp_path: Path
) -> None:
    called_agents: list[str] = []

    def fake_run_agent(adapter, *args, **kwargs):  # type: ignore[no-untyped-def]
        called_agents.append(adapter.name)
        if adapter.name == "claude":
            raise RuntimeError("HTTP 429 rate limit")
        return "CLAUDE_APPROVAL: YES\nOPEN_FINDINGS: NONE\nSTATUS: DONE"

    config = OrchestratorConfig(dry_run=False, allow_fallback_to_gemini=True)
    monkeypatch.setattr(agent_runtime, "run_agent", fake_run_agent)
    monkeypatch.setattr(agent_runtime.time, "sleep", lambda _sec: None)

    output = run_agent_checked(
        agent_key="claude",
        prompt="prompt",
        log_prefix="unit",
        max_retries=0,
        required_flags=["CLAUDE_APPROVAL"],
        output_validator=None,
        config=config,
        agents={"claude": AGENT_REGISTRY["claude"], "gemini": AGENT_REGISTRY["gemini"]},
        log_dir=tmp_path,
        write_file=lambda path, content: path.write_text(content, encoding="utf-8"),
        shorten=lambda text, limit=1800: (text or "")[:limit],
        parse_flag=lambda text, key: "YES" if f"{key}: YES" in text else None,
        validate_done_marker=lambda text: text.strip().endswith("STATUS: DONE"),
    )

    assert "STATUS: DONE" in output
    assert called_agents == ["claude", "gemini"]
    assert config.claude_quota_reached is True


def test_run_agent_checked_uses_gemini_directly_after_quota_flag(
    monkeypatch, tmp_path: Path
) -> None:
    called_agents: list[str] = []

    def fake_run_agent(adapter, *args, **kwargs):  # type: ignore[no-untyped-def]
        called_agents.append(adapter.name)
        return "CLAUDE_APPROVAL: YES\nOPEN_FINDINGS: NONE\nSTATUS: DONE"

    config = OrchestratorConfig(
        dry_run=False,
        allow_fallback_to_gemini=True,
        claude_quota_reached=True,
    )
    monkeypatch.setattr(agent_runtime, "run_agent", fake_run_agent)
    monkeypatch.setattr(agent_runtime.time, "sleep", lambda _sec: None)

    output = run_agent_checked(
        agent_key="claude",
        prompt="prompt",
        log_prefix="unit",
        max_retries=0,
        required_flags=["CLAUDE_APPROVAL"],
        output_validator=None,
        config=config,
        agents={"claude": AGENT_REGISTRY["claude"], "gemini": AGENT_REGISTRY["gemini"]},
        log_dir=tmp_path,
        write_file=lambda path, content: path.write_text(content, encoding="utf-8"),
        shorten=lambda text, limit=1800: (text or "")[:limit],
        parse_flag=lambda text, key: "YES" if f"{key}: YES" in text else None,
        validate_done_marker=lambda text: text.strip().endswith("STATUS: DONE"),
    )

    assert "STATUS: DONE" in output
    assert called_agents == ["gemini"]


@pytest.mark.parametrize("agent_key", ["codex", "gemini"])
def test_run_agent_checked_quota_errors_fail_fast_without_retries(
    monkeypatch, tmp_path: Path, agent_key: str
) -> None:
    calls: list[str] = []
    sleeps: list[int] = []

    def fake_run_agent(adapter, *args, **kwargs):  # type: ignore[no-untyped-def]
        _ = args
        _ = kwargs
        calls.append(adapter.name)
        raise RuntimeError("usage cap exceeded")

    monkeypatch.setattr(agent_runtime, "run_agent", fake_run_agent)
    monkeypatch.setattr(agent_runtime.time, "sleep", lambda sec: sleeps.append(sec))

    with pytest.raises(QuotaReachedError) as exc_info:
        run_agent_checked(
            agent_key=agent_key,
            prompt="prompt",
            log_prefix="unit",
            max_retries=3,
            required_flags=[],
            output_validator=None,
            config=OrchestratorConfig(dry_run=False),
            agents={agent_key: AGENT_REGISTRY[agent_key]},
            log_dir=tmp_path,
            write_file=lambda path, content: path.write_text(content, encoding="utf-8"),
            shorten=lambda text, limit=1800: (text or "")[:limit],
            parse_flag=lambda text, key: "YES",
            validate_done_marker=lambda text: True,
        )

    assert exc_info.value.agent_key == agent_key
    assert calls == [agent_key]
    assert sleeps == []


def test_run_agent_checked_dual_quota_claude_and_gemini_fail_fast(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[str] = []
    sleeps: list[int] = []

    def fake_run_agent(adapter, *args, **kwargs):  # type: ignore[no-untyped-def]
        _ = args
        _ = kwargs
        calls.append(adapter.name)
        if adapter.name == "claude":
            raise RuntimeError("HTTP 429 rate limit")
        raise RuntimeError("too many requests")

    monkeypatch.setattr(agent_runtime, "run_agent", fake_run_agent)
    monkeypatch.setattr(agent_runtime.time, "sleep", lambda sec: sleeps.append(sec))

    with pytest.raises(QuotaReachedError) as exc_info:
        run_agent_checked(
            agent_key="claude",
            prompt="prompt",
            log_prefix="unit",
            max_retries=3,
            required_flags=[],
            output_validator=None,
            config=OrchestratorConfig(dry_run=False, allow_fallback_to_gemini=True),
            agents={"claude": AGENT_REGISTRY["claude"], "gemini": AGENT_REGISTRY["gemini"]},
            log_dir=tmp_path,
            write_file=lambda path, content: path.write_text(content, encoding="utf-8"),
            shorten=lambda text, limit=1800: (text or "")[:limit],
            parse_flag=lambda text, key: "YES",
            validate_done_marker=lambda text: True,
        )

    assert exc_info.value.agent_key == "gemini"
    assert calls == ["claude", "gemini"]
    assert sleeps == []


def test_check_git_clean_skips_when_git_missing(monkeypatch) -> None:
    monkeypatch.setattr(agent_runtime.shutil, "which", lambda _name: None)

    ok, message = check_git_clean()

    assert ok is True
    assert "skipping git cleanliness check" in message.lower()


def test_check_git_clean_skips_when_not_in_git_repo(monkeypatch) -> None:
    monkeypatch.setattr(agent_runtime.shutil, "which", lambda _name: "/usr/bin/git")

    def fake_run_local_command(args, timeout=20):  # type: ignore[no-untyped-def]
        _ = timeout
        if args == ["git", "rev-parse", "--is-inside-work-tree"]:
            return 128, "", "fatal: not a git repository"
        raise AssertionError(f"Unexpected command: {args}")

    monkeypatch.setattr(agent_runtime, "run_local_command", fake_run_local_command)

    ok, message = check_git_clean()

    assert ok is True
    assert "skipped" in message.lower()


def test_check_git_clean_fails_on_tracked_changes(monkeypatch) -> None:
    monkeypatch.setattr(agent_runtime.shutil, "which", lambda _name: "/usr/bin/git")

    def fake_run_local_command(args, timeout=20):  # type: ignore[no-untyped-def]
        _ = timeout
        table = {
            ("git", "rev-parse", "--is-inside-work-tree"): (0, "true\n", ""),
            ("git", "rev-parse", "--verify", "HEAD"): (0, "abc123\n", ""),
            ("git", "status", "--porcelain", "--untracked-files=normal"): (0, "", ""),
            ("git", "update-index", "-q", "--refresh"): (0, "", ""),
            ("git", "diff-index", "--quiet", "HEAD", "--"): (1, "", ""),
        }
        try:
            return table[tuple(args)]
        except KeyError as exc:  # pragma: no cover - defensive
            raise AssertionError(f"Unexpected command: {args}") from exc

    monkeypatch.setattr(agent_runtime, "run_local_command", fake_run_local_command)

    ok, message = check_git_clean()

    assert ok is False
    assert "tracked changes" in message.lower()


def test_check_git_clean_fails_on_untracked_files(monkeypatch) -> None:
    monkeypatch.setattr(agent_runtime.shutil, "which", lambda _name: "/usr/bin/git")

    def fake_run_local_command(args, timeout=20):  # type: ignore[no-untyped-def]
        _ = timeout
        table = {
            ("git", "rev-parse", "--is-inside-work-tree"): (0, "true\n", ""),
            ("git", "rev-parse", "--verify", "HEAD"): (0, "abc123\n", ""),
            ("git", "status", "--porcelain", "--untracked-files=normal"): (0, "?? task.md\n", ""),
            ("git", "update-index", "-q", "--refresh"): (0, "", ""),
            ("git", "diff-index", "--quiet", "HEAD", "--"): (0, "", ""),
        }
        try:
            return table[tuple(args)]
        except KeyError as exc:  # pragma: no cover - defensive
            raise AssertionError(f"Unexpected command: {args}") from exc

    monkeypatch.setattr(agent_runtime, "run_local_command", fake_run_local_command)

    ok, message = check_git_clean()

    assert ok is False
    assert "not clean" in message.lower()


def test_preflight_skip_git_check_bypasses_dirty_repo(monkeypatch) -> None:
    calls = {"count": 0}
    monkeypatch.setattr(agent_runtime.shutil, "which", lambda _name: "/usr/bin/tool")
    monkeypatch.setattr(agent_runtime, "can_resolve_host", lambda _host: True)

    def fake_check_git_clean() -> tuple[bool, str]:
        calls["count"] += 1
        return False, "dirty"

    monkeypatch.setattr(agent_runtime, "check_git_clean", fake_check_git_clean)

    ok = preflight(
        required_agents=["codex"],
        strict=False,
        agents={"codex": AGENT_REGISTRY["codex"]},
        skip_git_check=True,
    )

    assert ok is True
    assert calls["count"] == 0


def test_preflight_fails_when_git_not_clean(monkeypatch) -> None:
    monkeypatch.setattr(agent_runtime.shutil, "which", lambda _name: "/usr/bin/tool")
    monkeypatch.setattr(agent_runtime, "can_resolve_host", lambda _host: True)
    monkeypatch.setattr(
        agent_runtime,
        "check_git_clean",
        lambda: (False, "dirty"),
    )

    ok = preflight(
        required_agents=["codex"],
        strict=False,
        agents={"codex": AGENT_REGISTRY["codex"]},
    )

    assert ok is False


def test_collect_file_snapshots_truncates_limits_and_handles_missing(tmp_path: Path) -> None:
    import os

    file_a = tmp_path / "a.py"
    file_b = tmp_path / "b.py"
    file_a.write_text("1\n2\n3\n4\n", encoding="utf-8")
    file_b.write_text("ok\n", encoding="utf-8")

    cwd = Path.cwd()
    try:
        # Function resolves paths from current working directory.
        os.chdir(tmp_path)
        output = collect_file_snapshots(
            changed_files=["a.py", "not a path line", "missing.py", "b.py"],
            max_lines=2,
            max_files=2,
        )
    finally:
        os.chdir(cwd)

    assert "<<<FILES_BEGIN>>>" in output
    assert "### a.py" in output
    assert "1\n2" in output
    assert "...[truncated to 2 lines]" in output
    assert "### missing.py" in output or "### b.py" in output
    assert "<<<FILES_END>>>" in output


def test_run_tests_snapshot_uses_shell_true_and_raw_command(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Result()

    monkeypatch.setattr(agent_runtime.subprocess, "run", fake_run)

    rc, snapshot = run_tests_snapshot(
        config=OrchestratorConfig(dry_run=False),
        test_command="npm test",
        test_timeout_seconds=30,
        shorten=lambda text, _limit: text or "",
    )

    assert rc == 0
    assert "Exit code: 0" in snapshot
    assert captured["command"] == "npm test"
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["shell"] is True


def test_run_agent_calls_adapter_cleanup_on_timeout(monkeypatch) -> None:
    class TimeoutAdapter:
        name = "timeout"
        cli_binary = "timeout"
        timeout = 1
        env: dict[str, str] = {}
        required_hosts: tuple[str, ...] = ()

        def __init__(self) -> None:
            self.cleaned = False

        def build_command(self, prompt: str) -> tuple[list[str], bool]:
            _ = prompt
            return ["timeout-cli"], True

        def extract_output(self, stdout: str, stderr: str, extra_files: dict[str, str]) -> str:
            _ = stdout
            _ = stderr
            _ = extra_files
            return ""

        def stream_filter(self, channel: str, line: str, state: dict[str, str | bool]) -> bool:
            _ = channel
            _ = line
            _ = state
            return False

        def cleanup(self) -> None:
            self.cleaned = True

    adapter = TimeoutAdapter()

    def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        _ = args
        raise agent_runtime.subprocess.TimeoutExpired("timeout-cli", kwargs["timeout"])

    monkeypatch.setattr(agent_runtime.subprocess, "run", fake_run)

    try:
        run_agent(
            adapter,
            "prompt",
            config=OrchestratorConfig(dry_run=False, agent_live_stream=False),
            shorten=lambda text, _limit: text or "",
        )
    except RuntimeError:
        pass
    else:  # pragma: no cover - defensive
        assert False, "expected RuntimeError"

    assert adapter.cleaned is True


def test_gemini_adapter_uses_stdin_prompt() -> None:
    adapter = GeminiAdapter()
    command, use_stdin_prompt = adapter.build_command("long prompt")

    assert command == ["gemini"]
    assert use_stdin_prompt is True


def test_codex_adapter_cleanup_deletes_temp_message_file() -> None:
    adapter = CodexAdapter()
    command, use_stdin_prompt = adapter.build_command("prompt")

    assert "--output-last-message" in command
    assert use_stdin_prompt is True
    temp_path = Path(command[-1])
    assert temp_path.exists()

    adapter.cleanup()
    assert not temp_path.exists()
