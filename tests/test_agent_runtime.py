from __future__ import annotations

import os
from pathlib import Path

import pytest

import agent_runtime
from agent_adapters import (
    AGENT_REGISTRY,
    AgentBudgetError,
    AgentPermissionError,
    CapabilitySpec,
    ClaudeAdapter,
    CodexAdapter,
)
from agent_config import AgentSettings
from agent_runtime import (
    AgentCompatibilityError,
    AgentInvocationError,
    OrchestratorConfig,
    QuotaReachedError,
    check_git_clean,
    collect_file_snapshots,
    compute_retry_backoff_seconds,
    create_read_only_reviewer_workspace,
    preflight,
    repo_snapshot,
    run_agent,
    run_agent_checked,
    run_tests_snapshot,
    run_validation_matrix,
    verify_agent_capabilities,
)
from validation_matrix import ValidationCommand, ValidationRequest
from repo_changes import ChangedPath, RepositoryChanges


def test_compute_retry_backoff_seconds_exponential() -> None:
    assert compute_retry_backoff_seconds("generic error", 1) == 2
    assert compute_retry_backoff_seconds("generic error", 2) == 4
    assert compute_retry_backoff_seconds("generic error", 5) == 30


def test_compute_retry_backoff_seconds_rate_limit_floor() -> None:
    assert compute_retry_backoff_seconds("HTTP 429 too many requests", 1) == 10
    assert compute_retry_backoff_seconds("rate limit", 2) == 10
    assert compute_retry_backoff_seconds("rate limit", 5) == 30


def test_orchestrator_config_has_no_agent_substitution_state() -> None:
    config = OrchestratorConfig()

    assert not hasattr(config, "allow_fallback_to_gemini")
    assert not hasattr(config, "claude_quota_reached")


def test_v3_dry_run_never_invents_an_approval_without_scenario() -> None:
    with pytest.raises(agent_runtime.AgentProcessError, match="--dry-run-scenario"):
        agent_runtime.build_dry_run_agent_output(
            "claude",
            "STATE-V3 CONTRACT (mandatory for step slice-15):\n"
            "SLICE_APPROVAL: 15 | YES|NO",
        )


def test_v3_dry_run_uses_the_last_real_contract_after_embedded_v2_text() -> None:
    with pytest.raises(agent_runtime.AgentProcessError, match="--dry-run-scenario"):
        agent_runtime.build_dry_run_agent_output(
            "claude",
            "repository diff contains CONTRACT (mandatory):\n"
            "STATE-V3 CONTRACT (mandatory for step slice-15):\n"
            "SLICE_APPROVAL: 15 | YES|NO",
        )


def test_legacy_v2_dry_run_remains_available_until_cutover() -> None:
    output = agent_runtime.build_dry_run_agent_output(
        "claude",
        "repository diff contains STATE-V3 CONTRACT\n"
        "CONTRACT (mandatory):\nPHASE2_APPROVAL: YES|NO\nOPEN_FINDINGS: NONE",
    )

    assert "PHASE2_APPROVAL: YES" in output
    assert output.endswith("STATUS: DONE")


def test_runtime_executes_structured_validation_request(tmp_path: Path) -> None:
    request = ValidationRequest(
        "a" * 64,
        (ValidationCommand(argv=("python3", "-c", "print('matrix-ok')")),),
    )

    attestation = run_validation_matrix(
        config=OrchestratorConfig(repo_root=tmp_path), request=request
    )

    assert attestation.passed
    assert attestation.records[0].output == "matrix-ok"


def test_run_agent_checked_does_not_retry_instance_failure(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []
    sleeps: list[int] = []

    def fake_run_agent(adapter, *args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(adapter.name)
        if len(calls) == 1:
            raise RuntimeError("temporary network glitch")
        return "CODEX_APPROVAL: YES\nOPEN_FINDINGS: NONE\nSTATUS: DONE"

    monkeypatch.setattr(agent_runtime, "run_agent", fake_run_agent)
    monkeypatch.setattr(agent_runtime.time, "sleep", lambda sec: sleeps.append(sec))

    with pytest.raises(AgentInvocationError) as exc_info:
        run_agent_checked(
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

    assert exc_info.value.kind.value == "network"
    assert calls == ["codex"]
    assert sleeps == []
    failure_logs = tuple(tmp_path.glob("unit.attempt-1.failure.json"))
    assert len(failure_logs) == 1
    assert exc_info.value.invocation_id in failure_logs[0].read_text(encoding="utf-8")


def test_run_agent_checked_validation_error_backoff(monkeypatch, tmp_path: Path) -> None:
    sleeps: list[int] = []
    prompts: list[str] = []

    def fake_run_agent(_adapter, prompt, **kwargs):  # type: ignore[no-untyped-def]
        prompts.append(prompt)
        return "CODEX_APPROVAL: YES\nSTATUS: DONE"

    monkeypatch.setattr(agent_runtime, "run_agent", fake_run_agent)
    monkeypatch.setattr(agent_runtime.time, "sleep", lambda sec: sleeps.append(sec))

    output = run_agent_checked(
        agent_key="codex",
        prompt=(
            "SENSITIVE_FULL_REVIEW_EVIDENCE\n\n"
            "Output format (Markdown):\n"
            "- Marker line: CODEX_APPROVAL: YES|NO\n"
            "- Final line: STATUS: DONE"
        ),
        log_prefix="unit",
        max_retries=1,
        required_flags=[],
        output_validator=lambda _output: "not valid" if len(prompts) == 1 else None,
        config=OrchestratorConfig(dry_run=False),
        agents={"codex": AGENT_REGISTRY["codex"]},
        log_dir=tmp_path,
        write_file=lambda path, content: path.write_text(content, encoding="utf-8"),
        shorten=lambda text, limit=1800: (text or "")[:limit],
        parse_flag=lambda text, key: "YES",
        validate_done_marker=lambda text: True,
    )

    assert "STATUS: DONE" in output
    assert len(prompts) == 2
    assert sleeps == [2]
    assert "SENSITIVE_FULL_REVIEW_EVIDENCE" in prompts[0]
    assert "SENSITIVE_FULL_REVIEW_EVIDENCE" not in prompts[1]
    assert "not valid" in prompts[1]
    assert "Rejected answer to repair" in prompts[1]
    assert "CODEX_APPROVAL: YES\nSTATUS: DONE" in prompts[1]
    assert "Output format (Markdown)" in prompts[1]


@pytest.mark.parametrize(
    "failure",
    [
        AgentPermissionError("denied Bash(find /)"),
        AgentBudgetError("budget guard stopped the call"),
    ],
)
def test_run_agent_checked_does_not_retry_policy_failure(
    monkeypatch, tmp_path: Path, failure: Exception
) -> None:
    calls: list[int] = []

    def fake_run_agent(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(1)
        raise failure

    monkeypatch.setattr(agent_runtime, "run_agent", fake_run_agent)

    with pytest.raises(AgentInvocationError) as exc_info:
        run_agent_checked(
            agent_key="claude",
            prompt="prompt",
            log_prefix="unit",
            max_retries=3,
            required_flags=[],
            output_validator=None,
            config=OrchestratorConfig(dry_run=False),
            agents={"claude": AGENT_REGISTRY["claude"]},
            log_dir=tmp_path,
            write_file=lambda path, content: path.write_text(content, encoding="utf-8"),
            shorten=lambda text, limit=1800: (text or "")[:limit],
            parse_flag=lambda text, key: None,
            validate_done_marker=lambda text: True,
        )

    assert calls == [1]
    assert exc_info.value.kind.value in {"permission", "runtime"}


def test_run_agent_checked_accepts_alternative_required_flags(monkeypatch, tmp_path: Path) -> None:
    def fake_run_agent(*args, **kwargs):  # type: ignore[no-untyped-def]
        _ = args
        _ = kwargs
        return "CODEX_APPROVAL: YES\nOPEN_FINDINGS: NONE\nSTATUS: DONE"

    monkeypatch.setattr(agent_runtime, "run_agent", fake_run_agent)

    output = run_agent_checked(
        agent_key="codex",
        prompt="prompt",
        log_prefix="unit",
        max_retries=0,
        required_flags=["PHASE1_APPROVAL|CODEX_APPROVAL"],
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


@pytest.mark.parametrize("agent_key", ["claude", "codex"])
def test_run_agent_checked_quota_errors_stop_requested_agent_without_substitution(
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
    assert agent_key in str(exc_info.value)
    assert calls == [agent_key]
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
            ("git", "status", "--porcelain", "--untracked-files=normal"): (0, " M run_task\n", ""),
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
    assert "run_task" in message
    assert "git status --porcelain" in message


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
    assert "?? task.md" in message
    assert "git status --porcelain" in message


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


def test_agent_capability_check_is_lazy_and_cached(monkeypatch) -> None:
    adapter = CodexAdapter(
        AgentSettings("codex", "codex", "gpt-5.6-sol", 1800, "medium")
    )
    calls: list[list[str]] = []

    def fake_local(args, timeout=20):  # type: ignore[no-untyped-def]
        _ = timeout
        calls.append(args)
        if args[-1] == "--version":
            return 0, "codex-cli 0.147.0\n", ""
        return 0, " ".join(adapter.capability.required_help_flags), ""

    monkeypatch.setattr(agent_runtime, "_resolve_agent_binary", lambda _binary: "/bin/codex")
    monkeypatch.setattr(agent_runtime, "run_local_command", fake_local)

    verify_agent_capabilities(adapter)
    verify_agent_capabilities(adapter)

    assert adapter.capability_verified is True
    assert calls == [
        ["/bin/codex", "--version"],
        ["/bin/codex", "exec", "--help"],
    ]


def test_collect_file_snapshots_truncates_limits_and_handles_missing(tmp_path: Path) -> None:
    file_a = tmp_path / "a.py"
    file_b = tmp_path / "b.py"
    file_a.write_text("1\n2\n3\n4\n", encoding="utf-8")
    file_b.write_text("ok\n", encoding="utf-8")

    output = collect_file_snapshots(
        changed_files=["a.py", "# ignored heading", "missing.py", "b.py"],
        max_lines=2,
        max_files=2,
        repository_root=tmp_path,
    )

    assert "<<<FILES_BEGIN>>>" in output
    assert "### a.py" in output
    assert "1\n2" in output
    assert "...[truncated to 2 lines]" in output
    assert "### missing.py" in output or "### b.py" in output
    assert "<<<FILES_END>>>" in output


def test_collect_file_snapshots_rejects_paths_outside_repository(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository with spaces"
    repository.mkdir()
    safe_file = repository / "safe.txt"
    safe_file.write_text("SAFE CONTENT\n", encoding="utf-8")
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("FOREIGN SECRET CONTENT\n", encoding="utf-8")
    (repository / "escape.txt").symlink_to(secret_file)

    with caplog.at_level("WARNING", logger="agent_runtime"):
        output = collect_file_snapshots(
            changed_files=[
                "../secret.txt",
                r"..\secret.txt",
                str(secret_file),
                "escape.txt",
                str(safe_file),
            ],
            max_lines=20,
            max_files=1,
            repository_root=repository,
        )

    assert "SAFE CONTENT" in output
    assert "### safe.txt" in output
    assert "FOREIGN SECRET CONTENT" not in output
    assert "secret.txt" not in output
    assert "escape.txt" not in output
    assert caplog.text.count("Rejected file snapshot path") == 4


def test_collect_file_snapshots_normalizes_duplicates_and_skips_directory(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source_file = source_dir / "module.py"
    source_file.write_text("pass\n", encoding="utf-8")

    output = collect_file_snapshots(
        changed_files=[r"source\module.py", "source/module.py", "source"],
        max_lines=20,
        max_files=3,
        repository_root=tmp_path,
    )

    assert output.count("### source/module.py") == 1
    assert "### source\n[skip] Path is a directory." in output


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO creation requires POSIX")
def test_collect_file_snapshots_skips_non_regular_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fifo = tmp_path / "review-pipe"
    os.mkfifo(fifo)
    original_read_text = Path.read_text

    def guarded_read_text(path: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
        if path == fifo:
            raise AssertionError("snapshot collector attempted to read a FIFO")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)

    output = collect_file_snapshots(
        changed_files=[fifo.name],
        max_lines=20,
        max_files=1,
        repository_root=tmp_path,
    )

    assert "### review-pipe\n[skip] Path is not a regular file." in output


def test_repo_snapshot_renders_precollected_canonical_changes(tmp_path: Path) -> None:
    changes = RepositoryChanges(
        repository_root=tmp_path,
        merge_base="a" * 40,
        entries=(ChangedPath("src/new.py", "added", "A"),),
        diff_text="diff body that is deliberately longer than the limit",
        fingerprint="b" * 64,
    )

    snapshot = repo_snapshot(changes, max_diff_chars=12)

    assert f"since {'a' * 40}" in snapshot
    assert "A src/new.py" in snapshot
    assert "b" * 64 in snapshot
    assert "...[truncated]" in snapshot


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
        model = "model"
        effort = "medium"
        timeout = 1
        reviewer = False
        env: dict[str, str] = {}
        required_hosts: tuple[str, ...] = ()
        capability = CapabilitySpec((), (), (r".*",), ())
        capability_verified = True
        metadata: dict[str, object] = {}

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

        def validate_process_output(self, stderr: str) -> None:
            _ = stderr

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


def test_read_only_reviewer_workspace_blocks_writes_and_preserves_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    tracked = source / "tracked.txt"
    tracked.write_text("original\n", encoding="utf-8")

    workspace = create_read_only_reviewer_workspace(source)
    try:
        copied = workspace.root / "tracked.txt"
        with pytest.raises(PermissionError):
            copied.write_text("changed\n", encoding="utf-8")
        assert copied.read_text(encoding="utf-8") == "original\n"
        assert tracked.read_text(encoding="utf-8") == "original\n"
        with pytest.raises(PermissionError):
            (workspace.container / "outside-repo.txt").write_text(
                "unexpected\n", encoding="utf-8"
            )
    finally:
        workspace.cleanup()

    assert not workspace.container.exists()


def test_reviewer_process_pwd_matches_disposable_working_directory(
    monkeypatch, tmp_path: Path
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "tracked.txt").write_text("original\n", encoding="utf-8")
    captured: dict[str, object] = {}

    class ReviewerAdapter:
        name = "reviewer"
        cli_binary = "reviewer"
        model = "model"
        effort = "medium"
        timeout = 30
        reviewer = True
        env: dict[str, str] = {}
        required_hosts: tuple[str, ...] = ()
        capability = CapabilitySpec((), (), (r".*",), ())
        capability_verified = True
        metadata: dict[str, object] = {}

        def bind_reviewer_workspace(self, source_root: Path, snapshot_root: Path) -> None:
            captured["bound_source"] = source_root
            captured["bound_snapshot"] = snapshot_root

        def build_command(self, prompt: str) -> tuple[list[str], bool]:
            return ["reviewer"], True

        def extract_output(self, stdout: str, stderr: str, extra_files: dict[str, str]) -> str:
            return stdout

        def stream_filter(self, channel: str, line: str, state: dict[str, str | bool]) -> bool:
            return False

        def validate_process_output(self, stderr: str) -> None:
            return None

        def cleanup(self) -> None:
            return None

    class Result:
        returncode = 0
        stdout = "STATUS: DONE"
        stderr = ""

    def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return Result()

    monkeypatch.setattr(agent_runtime.subprocess, "run", fake_run)
    monkeypatch.setenv("RUN_TASK_REVIEW_TEST_COMMAND", "python3 -m pytest tests/ -v")
    monkeypatch.setenv("RUN_TASK_REVIEW_PROBE_PATH", "README.md")
    monkeypatch.setenv("RUN_TASK_REVIEW_TIMEOUT", "1800")
    output = run_agent(
        ReviewerAdapter(),
        "prompt",
        config=OrchestratorConfig(repo_root=source, agent_live_stream=False),
        shorten=lambda text, limit: (text or "")[:limit],
    )

    working_directory = captured["cwd"]
    process_environment = captured["env"]
    assert isinstance(working_directory, Path)
    assert isinstance(process_environment, dict)
    assert process_environment["PWD"] == str(working_directory)
    assert process_environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "RUN_TASK_REVIEW_TEST_COMMAND" not in process_environment
    assert "RUN_TASK_REVIEW_PROBE_PATH" not in process_environment
    assert "RUN_TASK_REVIEW_TIMEOUT" not in process_environment
    assert captured["bound_snapshot"] == working_directory
    assert not working_directory.exists()
    assert output == "STATUS: DONE"


def test_unknown_agent_version_is_a_non_retryable_gate(monkeypatch, tmp_path: Path) -> None:
    adapter = ClaudeAdapter(
        AgentSettings("claude", "claude", "sonnet", 1800, "medium")
    )
    calls: list[list[str]] = []

    def fake_local(args, timeout=20):  # type: ignore[no-untyped-def]
        _ = timeout
        calls.append(args)
        return 0, "9.9.9 (Claude Code)\n", ""

    monkeypatch.setattr(agent_runtime, "_resolve_agent_binary", lambda _binary: "/bin/claude")
    monkeypatch.setattr(agent_runtime, "run_local_command", fake_local)

    with pytest.raises(AgentInvocationError, match="Unsupported claude CLI version") as exc_info:
        run_agent_checked(
            agent_key="claude",
            prompt="prompt",
            log_prefix="unit",
            max_retries=3,
            required_flags=[],
            output_validator=None,
            config=OrchestratorConfig(dry_run=False),
            agents={"claude": adapter},
            log_dir=tmp_path,
            write_file=lambda path, content: path.write_text(content, encoding="utf-8"),
            shorten=lambda text, limit=1800: (text or "")[:limit],
            parse_flag=lambda text, key: None,
            validate_done_marker=lambda text: True,
        )

    assert calls == [["/bin/claude", "--version"]]
    assert exc_info.value.kind.value == "runtime"
