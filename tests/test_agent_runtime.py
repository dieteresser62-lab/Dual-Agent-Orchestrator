from __future__ import annotations

from pathlib import Path

import agent_runtime
from agent_runtime import OrchestratorConfig, compute_retry_backoff_seconds, run_agent_checked


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
            raise RuntimeError("HTTP 429 too many requests")
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
        agents={"codex": {"command": ["codex"], "timeout": 1, "env": {}}},
        log_dir=tmp_path,
        write_file=lambda path, content: path.write_text(content, encoding="utf-8"),
        shorten=lambda text, limit=1800: (text or "")[:limit],
        parse_flag=lambda text, key: "YES" if f"{key}: YES" in text else None,
        validate_done_marker=lambda text: text.strip().endswith("STATUS: DONE"),
    )

    assert "STATUS: DONE" in output
    assert len(calls) == 2
    assert sleeps == [10]


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
        agents={"codex": {"command": ["codex"], "timeout": 1, "env": {}}},
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

    def fake_run_agent(agent_key, *args, **kwargs):  # type: ignore[no-untyped-def]
        called_agents.append(agent_key)
        if agent_key == "claude":
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
        agents={
            "claude": {"command": ["claude"], "timeout": 1, "env": {}},
            "gemini": {"command": ["gemini"], "timeout": 1, "env": {}},
        },
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

    def fake_run_agent(agent_key, *args, **kwargs):  # type: ignore[no-untyped-def]
        called_agents.append(agent_key)
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
        agents={
            "claude": {"command": ["claude"], "timeout": 1, "env": {}},
            "gemini": {"command": ["gemini"], "timeout": 1, "env": {}},
        },
        log_dir=tmp_path,
        write_file=lambda path, content: path.write_text(content, encoding="utf-8"),
        shorten=lambda text, limit=1800: (text or "")[:limit],
        parse_flag=lambda text, key: "YES" if f"{key}: YES" in text else None,
        validate_done_marker=lambda text: text.strip().endswith("STATUS: DONE"),
    )

    assert "STATUS: DONE" in output
    assert called_agents == ["gemini"]
