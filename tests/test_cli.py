from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import cli
import orchestrator
from cli import (
    ConfigError,
    build_parser,
    detect_test_command,
    load_repo_config,
    main,
    parse_args,
    run_cli,
)
from agent_runtime import QuotaWaitPolicy, TransientRetryPolicy


def _write_config(repo: Path, text: str) -> Path:
    path = repo / "orchestrator.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_repository_config_loads_complete_provider_input_budget_table() -> None:
    config = load_repo_config(Path(__file__).resolve().parents[1] / "orchestrator.toml")

    rule = config.provider_input_budget.select(
        "claude", "claude", "claude_final_review"
    )
    assert rule.max_chars == 4_000_000
    assert rule.max_bytes == 16_000_000


def test_provider_input_budget_config_is_closed_and_complete(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
[[provider_input_budget]]
provider = "codex"
role = "codex"
operation = "codex_implementation"
max_chars = 10
max_bytes = 20
unexpected = true
""",
    )
    with pytest.raises(ConfigError, match="Unknown key"):
        load_repo_config(path)

    path.write_text(
        """
[[provider_input_budget]]
provider = "codex"
role = "codex"
operation = "codex_implementation"
max_chars = 10
max_bytes = 20
""",
        encoding="utf-8",
    )
    config = load_repo_config(path)
    assert config.provider_input_budget.select(
        "codex", "codex", "codex_implementation"
    ).max_chars == 10
    assert config.provider_input_budget.select(
        "claude", "claude", "claude_final_review"
    ).max_chars == 4_000_000

    path.write_text(path.read_text(encoding="utf-8") * 2, encoding="utf-8")
    with pytest.raises(ConfigError, match="duplicate"):
        load_repo_config(path)


@pytest.fixture(autouse=True)
def _isolate_process_environment(monkeypatch) -> None:
    for name in (
        "RUN_TASK_CONFIG",
        "RUN_TASK_SKIP_GIT_CHECK",
        "RUN_TASK_TEST_CMD",
        "RUN_TASK_WATCH_STREAM_CHANNELS",
        "RUN_TASK_CODEX_BINARY",
        "RUN_TASK_CODEX_MODEL",
        "RUN_TASK_CODEX_TIMEOUT",
        "RUN_TASK_CODEX_EFFORT",
        "RUN_TASK_CLAUDE_BINARY",
        "RUN_TASK_CLAUDE_MODEL",
        "RUN_TASK_CLAUDE_TIMEOUT",
        "RUN_TASK_CLAUDE_EFFORT",
        "RUN_TASK_CLAUDE_MAX_BUDGET_USD",
        "RUN_TASK_ANTIGRAVITY_BINARY",
        "RUN_TASK_ANTIGRAVITY_MODEL",
        "RUN_TASK_ANTIGRAVITY_TIMEOUT",
        "RUN_TASK_ANTIGRAVITY_EFFORT",
        "RUN_TASK_QUOTA_AUTO_RESUME",
        "RUN_TASK_QUOTA_SAFETY_MARGIN",
        "RUN_TASK_QUOTA_MAX_WAIT",
        "RUN_TASK_QUOTA_MAX_AUTO_RESUMES",
        "RUN_TASK_QUOTA_HEARTBEAT_INTERVAL",
        "RUN_TASK_TRANSIENT_RETRY_AUTO",
        "RUN_TASK_TRANSIENT_RETRY_INITIAL_DELAY",
        "RUN_TASK_TRANSIENT_RETRY_MAX_DELAY",
        "RUN_TASK_TRANSIENT_RETRY_MAX_AUTO_RESUMES",
    ):
        monkeypatch.delenv(name, raising=False)


def test_task_file_accepts_positional_compatibility_path(tmp_path: Path) -> None:
    args = parse_args(["work.md"], cwd=tmp_path, environ={})

    assert args.task_file == "work.md"


def test_validation_retries_require_explicit_cli_flags(tmp_path: Path) -> None:
    default = parse_args([], cwd=tmp_path, environ={})
    requested = parse_args(
        ["--retry-incomplete-validation", "--retry-failed-validation"],
        cwd=tmp_path,
        environ={},
    )

    assert default.retry_incomplete_validation is False
    assert default.retry_failed_validation is False
    assert requested.retry_incomplete_validation is True
    assert requested.retry_failed_validation is True


def test_native_claude_review_flag_is_explicit_and_watch_default_is_legacy(
    tmp_path: Path,
) -> None:
    default = parse_args([], cwd=tmp_path, environ={})
    enabled = parse_args(
        ["--native-claude-reviews"], cwd=tmp_path, environ={}
    )
    disabled = parse_args(
        ["--no-native-claude-reviews"], cwd=tmp_path, environ={}
    )
    watch = parse_args(["--watch"], cwd=tmp_path, environ={})

    assert default.native_claude_reviews is None
    assert enabled.native_claude_reviews is True
    assert disabled.native_claude_reviews is False
    assert watch.native_claude_reviews is None


def test_quota_wait_policy_defaults_and_explicit_disable(tmp_path: Path) -> None:
    default = parse_args([], cwd=tmp_path, environ={})
    disabled = parse_args(
        [
            "--no-quota-auto-resume",
            "--quota-safety-margin",
            "15",
            "--quota-max-wait",
            "120",
            "--quota-max-auto-resumes",
            "0",
            "--quota-heartbeat-interval",
            "9",
        ],
        cwd=tmp_path,
        environ={},
    )

    assert default.quota_wait_policy == QuotaWaitPolicy()
    assert default.quota_wait_policy.maximum_wait_seconds == 604_800
    assert default.quota_wait_policy.heartbeat_interval_seconds == 3_600
    assert disabled.quota_wait_policy == QuotaWaitPolicy(
        automatic=False,
        safety_margin_seconds=15,
        maximum_wait_seconds=120,
        maximum_auto_resumes=0,
        heartbeat_interval_seconds=9,
    )


def test_quota_wait_policy_uses_cli_then_environment_then_defaults(tmp_path: Path) -> None:
    environment = {
        "RUN_TASK_QUOTA_AUTO_RESUME": "0",
        "RUN_TASK_QUOTA_SAFETY_MARGIN": "12",
        "RUN_TASK_QUOTA_MAX_WAIT": "900",
        "RUN_TASK_QUOTA_MAX_AUTO_RESUMES": "2",
        "RUN_TASK_QUOTA_HEARTBEAT_INTERVAL": "7",
    }
    from_environment = parse_args([], cwd=tmp_path, environ=environment)
    overridden = parse_args(
        ["--quota-auto-resume", "--quota-max-wait", "60"],
        cwd=tmp_path,
        environ=environment,
    )

    assert from_environment.quota_wait_policy == QuotaWaitPolicy(
        automatic=False,
        safety_margin_seconds=12,
        maximum_wait_seconds=900,
        maximum_auto_resumes=2,
        heartbeat_interval_seconds=7,
    )
    assert overridden.quota_wait_policy.automatic is True
    assert overridden.quota_wait_policy.maximum_wait_seconds == 60
    assert overridden.quota_wait_policy.safety_margin_seconds == 12


def test_transient_retry_policy_defaults_and_overrides(tmp_path: Path) -> None:
    default = parse_args([], cwd=tmp_path, environ={})
    environment = {
        "RUN_TASK_TRANSIENT_RETRY_AUTO": "0",
        "RUN_TASK_TRANSIENT_RETRY_INITIAL_DELAY": "7",
        "RUN_TASK_TRANSIENT_RETRY_MAX_DELAY": "40",
        "RUN_TASK_TRANSIENT_RETRY_MAX_AUTO_RESUMES": "3",
    }
    configured = parse_args(
        ["--transient-retry-auto", "--transient-retry-max-delay", "20"],
        cwd=tmp_path,
        environ=environment,
    )

    assert default.transient_retry_policy == TransientRetryPolicy()
    assert configured.transient_retry_policy == TransientRetryPolicy(
        automatic=True,
        initial_delay_seconds=7,
        maximum_delay_seconds=20,
        maximum_auto_resumes=3,
    )


@pytest.mark.parametrize(
    "args",
    (
        ["--quota-safety-margin", "-1"],
        ["--quota-max-wait", "0"],
        ["--quota-max-auto-resumes", "-1"],
        ["--quota-heartbeat-interval", "0"],
    ),
)
def test_invalid_quota_wait_policy_is_rejected(
    tmp_path: Path, args: list[str], capsys
) -> None:
    with pytest.raises(SystemExit):
        parse_args(args, cwd=tmp_path, environ={})

    assert "quota" in capsys.readouterr().err.lower()


def test_task_file_rejects_positional_and_explicit_paths(tmp_path: Path, capsys) -> None:
    with pytest.raises(SystemExit):
        parse_args(["first.md", "--task-file", "second.md"], cwd=tmp_path, environ={})

    assert "either positionally or with --task-file" in capsys.readouterr().err


def test_test_command_precedence_cli_environment_repo_and_detection(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        '[validation]\ndefault_command = ["from-repo"]\n',
    )

    cli = parse_args(
        ["--test-command", "from-cli"],
        cwd=tmp_path,
        environ={"RUN_TASK_TEST_CMD": "from-env"},
    )
    environment = parse_args([], cwd=tmp_path, environ={"RUN_TASK_TEST_CMD": "from-env"})
    repository = parse_args([], cwd=tmp_path, environ={})
    (tmp_path / "orchestrator.toml").unlink()
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8"
    )
    detected = parse_args([], cwd=tmp_path, environ={})

    assert cli.test_command == "from-cli"
    assert environment.test_command == "from-env"
    assert repository.test_command == "from-repo"
    assert detected.test_command == "python3 -m pytest tests/ -v"


def test_config_path_precedence_cli_over_environment(tmp_path: Path) -> None:
    cli_config = tmp_path / "cli.toml"
    env_config = tmp_path / "env.toml"
    cli_config.write_text('[validation]\ndefault_command = ["from-cli-config"]\n', encoding="utf-8")
    env_config.write_text('[validation]\ndefault_command = ["from-env-config"]\n', encoding="utf-8")

    args = parse_args(
        ["--config", str(cli_config)],
        cwd=tmp_path,
        environ={"RUN_TASK_CONFIG": str(env_config)},
    )

    assert args.config_file == cli_config.resolve()
    assert args.test_command == "from-cli-config"


def test_explicit_missing_config_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Explicit configuration file does not exist"):
        parse_args(["--config", "missing.toml"], cwd=tmp_path, environ={})


@pytest.mark.parametrize("arguments", [[], ["--config", ""]])
def test_empty_config_value_falls_through_to_optional_default(
    arguments: list[str], tmp_path: Path
) -> None:
    args = parse_args(arguments, cwd=tmp_path, environ={"RUN_TASK_CONFIG": ""})

    assert args.config_file is None


def test_empty_cli_config_value_falls_through_to_environment(tmp_path: Path) -> None:
    env_config = tmp_path / "environment.toml"
    env_config.write_text('[validation]\ndefault_command = ["environment"]\n', encoding="utf-8")

    args = parse_args(
        ["--config", ""],
        cwd=tmp_path,
        environ={"RUN_TASK_CONFIG": str(env_config)},
    )

    assert args.config_file == env_config.resolve()
    assert args.test_command == "environment"


def test_every_public_parser_action_has_help_text() -> None:
    parser = build_parser()

    for action in parser._actions:
        if action.dest in {"help", "auto"}:
            continue
        assert action.help not in {None, ""}, action.dest
    formatted = parser.format_help()
    assert "--dry-run" in formatted
    assert "Simulate agent responses" in formatted


def test_agent_setting_precedence_cli_over_environment_and_defaults(tmp_path: Path) -> None:
    args = parse_args(
        [
            "--claude-binary",
            "/cli/claude",
            "--claude-model",
            "opus",
            "--claude-timeout",
            "321",
            "--claude-effort",
            "high",
            "--claude-max-budget-usd",
            "2.5",
        ],
        cwd=tmp_path,
        environ={
            "RUN_TASK_CLAUDE_BINARY": "/env/claude",
            "RUN_TASK_CLAUDE_MODEL": "sonnet",
            "RUN_TASK_CLAUDE_TIMEOUT": "999",
            "RUN_TASK_CLAUDE_EFFORT": "low",
            "RUN_TASK_CLAUDE_MAX_BUDGET_USD": "1.0",
            "RUN_TASK_ANTIGRAVITY_BINARY": "agy.exe",
        },
    )

    claude = args.agent_settings["claude"]
    assert claude.binary == "/cli/claude"
    assert claude.model == "opus"
    assert claude.timeout_seconds == 321
    assert claude.effort == "high"
    assert claude.max_budget_usd == 2.5
    assert args.agent_settings["antigravity"].binary == "agy.exe"
    assert args.agent_settings["codex"].model == "gpt-5.6-sol"


def test_quota_conscious_reviewer_defaults_are_explicit(tmp_path: Path) -> None:
    args = parse_args([], cwd=tmp_path, environ={})

    assert args.agent_settings["claude"].model == "sonnet"
    assert args.agent_settings["claude"].effort == "high"
    assert args.agent_settings["claude"].timeout_seconds == 1800
    assert args.agent_settings["claude"].max_budget_usd is None
    assert args.agent_settings["antigravity"].model == "gemini-3.7-flash-high"
    assert args.agent_settings["antigravity"].effort == "high"


def test_manual_slice_gate_cli_overrides_repository_default(tmp_path: Path) -> None:
    _write_config(tmp_path, "[workflow]\nmanual_slice_gate = true\n")

    configured = parse_args([], cwd=tmp_path, environ={})
    overridden = parse_args(["--no-manual-slice-gate"], cwd=tmp_path, environ={})

    assert configured.manual_slice_gate is True
    assert overridden.manual_slice_gate is False


def test_workflow_gates_default_to_automatic_and_allow_explicit_overrides(
    tmp_path: Path,
) -> None:
    default = parse_args([], cwd=tmp_path, environ={})
    overridden = parse_args(
        [
            "--plan-gate",
            "--test-change-gate",
            "--plan-only",
            "--work-plan",
            "docs/internal/plan.md",
            "--target-branch",
            "feature/plan",
        ],
        cwd=tmp_path,
        environ={},
    )

    assert default.plan_gate is False
    assert default.test_change_gate is False
    assert default.plan_only is None
    assert overridden.plan_gate is True
    assert overridden.test_change_gate is True
    assert overridden.plan_only is True
    assert overridden.work_plan == "docs/internal/plan.md"
    assert overridden.target_branch == "feature/plan"


@pytest.mark.parametrize(
    "arguments",
    [
        ["--approve-gate", "--gate-actor", "user", "--gate-rationale", "ok"],
        ["--resume", "--approve-gate", "--gate-rationale", "ok"],
        ["--resume", "--approve-gate", "--gate-actor", "user"],
        ["--resume", "--gate-actor", "user"],
    ],
)
def test_gate_cli_decision_requires_explicit_resume_actor_and_rationale(
    arguments: list[str], tmp_path: Path
) -> None:
    with pytest.raises(SystemExit):
        parse_args(arguments, cwd=tmp_path, environ={})


def test_gate_cli_records_explicit_approval_intent(tmp_path: Path) -> None:
    args = parse_args(
        [
            "--resume",
            "--approve-gate",
            "--gate-actor",
            "domain-owner",
            "--gate-rationale",
            "reviewed exact persisted evidence",
        ],
        cwd=tmp_path,
        environ={},
    )

    assert args.gate_decision is True
    assert args.gate_actor == "domain-owner"
    assert args.gate_rationale == "reviewed exact persisted evidence"


@pytest.mark.parametrize(
    ("environment", "message"),
    [
        ({"RUN_TASK_CLAUDE_TIMEOUT": "0"}, "claude timeout"),
        ({"RUN_TASK_CLAUDE_EFFORT": "extreme"}, "claude effort"),
        ({"RUN_TASK_CLAUDE_MAX_BUDGET_USD": "free"}, "claude max budget"),
        ({"RUN_TASK_ANTIGRAVITY_TIMEOUT": "nope"}, "antigravity timeout"),
    ],
)
def test_invalid_agent_environment_is_a_configuration_error(
    environment: dict[str, str], message: str, tmp_path: Path
) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_args([], cwd=tmp_path, environ=environment)


@pytest.mark.parametrize(
    ("source_name", "content", "expected"),
    [
        ("pyproject.toml", "[tool.pytest.ini_options]\n", "python3 -m pytest tests/ -v"),
        ("package.json", '{"scripts": {"test": "vitest"}}', "npm test"),
        ("Makefile", "  test: lint\n\tpytest\n", "make test"),
    ],
)
def test_detect_test_command(source_name: str, content: str, expected: str, tmp_path: Path) -> None:
    (tmp_path / source_name).write_text(content, encoding="utf-8")

    assert detect_test_command(tmp_path) == expected


def test_detect_test_command_returns_empty_without_supported_layout(tmp_path: Path) -> None:
    assert detect_test_command(tmp_path) == ""


def test_explicit_empty_test_commands_are_not_auto_detected(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts": {"test": "vitest"}}', encoding="utf-8"
    )

    cli = parse_args(["--test-command", ""], cwd=tmp_path, environ={})
    environment = parse_args([], cwd=tmp_path, environ={"RUN_TASK_TEST_CMD": ""})
    _write_config(tmp_path, '[validation]\ndefault_shell_command = ""\n')
    repository = parse_args([], cwd=tmp_path, environ={})

    assert cli.test_command == ""
    assert environment.test_command == ""
    assert repository.test_command == ""


def test_load_repo_config_accepts_portable_policy_schema(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
[paths]
productive = ["src/**/*.py"]
tests = ["tests/**"]
documentation = ["docs/**"]
generated = ["build/**"]

[[stop_rules]]
id = "DOMAIN-001"
description = "Stop on invariant changes."

[validation]
default_command = ["pytest"]

[[validation.rules]]
patterns = ["engine/**"]
command = ["npm", "run", "build:engine"]

[workflow]
manual_slice_gate = true
plan_gate = false
test_change_gate = true
""".strip(),
    )

    config = load_repo_config(path)

    assert config.source == path.resolve()
    assert config.paths.productive == ("src/**/*.py",)
    assert config.stop_rules[0].id == "DOMAIN-001"
    assert config.validation.default_command is not None
    assert config.validation.default_command.argv == ("pytest",)
    assert config.validation.rules[0].patterns == ("engine/**",)
    assert config.validation.rules[0].command.argv == (
        "npm",
        "run",
        "build:engine",
    )
    assert config.workflow.manual_slice_gate is True
    assert config.workflow.plan_gate is False
    assert config.workflow.test_change_gate is True


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("unknown = true\n", "Unknown key(s) in root: unknown"),
        ("[workflow]\nmanual_slice_gate = \"yes\"\n", "must be a boolean"),
        ("[workflow]\nplan_gate = \"yes\"\n", "must be a boolean"),
        ("[workflow]\ntest_change_gate = \"yes\"\n", "must be a boolean"),
        ("[paths]\nproductive = [\"../outside/**\"]\n", "must not escape"),
        ("[paths]\nproductive = [\"C:/outside/**\"]\n", "must be relative"),
        ("[paths]\nproductive = [\"src\\\\**\"]\n", "platform-neutral separator"),
        ("[paths]\nproductive = []\n", "must contain at least one path pattern"),
        (
            "[paths]\nproductive = [\"src/**\", \"src/**\"]\n",
            "Invalid [paths] configuration: productive path patterns must be unique",
        ),
        (
            "[[stop_rules]]\nid = \"S-001\"\ndescription = \"one\"\n"
            "[[stop_rules]]\nid = \"S-001\"\ndescription = \"two\"\n",
            "Duplicate stop rule id: S-001",
        ),
        (
            '[validation]\ndefault_command = "pytest"\n',
            "validation default.default_command must be a non-empty string array",
        ),
        (
            '[validation]\ndefault_command = ["pytest"]\n'
            'default_shell_command = "pytest"\n',
            "must declare only one",
        ),
    ],
)
def test_load_repo_config_rejects_invalid_configuration(
    content: str, message: str, tmp_path: Path
) -> None:
    path = _write_config(tmp_path, content)

    with pytest.raises(ConfigError, match=re.escape(message)):
        load_repo_config(path)


def test_main_reports_config_error_before_calling_workflow(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _write_config(tmp_path, "unknown = true\n")
    called = False

    def fail_if_called(*_args, **_kwargs):  # noqa: ANN002, ANN003
        nonlocal called
        called = True
        return 0

    monkeypatch.chdir(tmp_path)
    result = main(
        [],
        run_pipeline_fn=fail_if_called,
        watch_inbox_fn=fail_if_called,
        find_task_file_fn=lambda _path: tmp_path / "task.md",
    )

    assert result == 1
    assert called is False
    assert "Configuration error:" in capsys.readouterr().err
    assert not (tmp_path / ".orchestrator").exists()


def test_auto_resume_detects_unfinished_and_frozen_state(tmp_path: Path) -> None:
    state_dir = tmp_path / ".orchestrator"
    state_dir.mkdir()
    (state_dir / "state.json").write_text(
        json.dumps({"phase": "phase2", "phase2": {"status": "frozen"}}),
        encoding="utf-8",
    )

    args = parse_args([], cwd=tmp_path, environ={})

    assert args.resume is True
    assert args.auto_resume is True
    assert args.state_was_frozen is True


def test_completed_state_starts_new_run_with_notice(
    tmp_path: Path, caplog
) -> None:
    state_dir = tmp_path / ".orchestrator"
    state_dir.mkdir()
    (state_dir / "state.json").write_text(
        json.dumps({"phase": "done"}), encoding="utf-8"
    )

    args = parse_args([], cwd=tmp_path, environ={})

    assert args.resume is False
    assert args.force_overwrite_state is True
    assert args.completed_state_replaced is True

    with caplog.at_level(logging.INFO):
        result = run_cli(
            args,
            run_pipeline_fn=lambda *_args, **_kwargs: 0,
            watch_inbox_fn=lambda **_kwargs: 0,
            find_task_file_fn=lambda _path: tmp_path / "task.md",
        )

    assert result == 0
    assert "Existing state is completed (phase=done); starting a new run." in caplog.text


def test_watch_defaults_do_not_auto_resume_and_skip_git_check(tmp_path: Path) -> None:
    state_dir = tmp_path / ".orchestrator"
    state_dir.mkdir()
    (state_dir / "state.json").write_text(
        json.dumps({"phase": "phase1"}), encoding="utf-8"
    )

    args = parse_args(["--watch"], cwd=tmp_path, environ={})

    assert args.resume is False
    assert args.auto_resume is False
    assert args.skip_git_check is True
    assert args.plan_gate is False
    assert args.plan_gate_source == "repository-default"
    assert args.test_change_gate is False


def test_watch_plan_gate_can_be_enabled_explicitly(tmp_path: Path) -> None:
    cli_enabled = parse_args(["--watch", "--plan-gate"], cwd=tmp_path, environ={})
    _write_config(tmp_path, "[workflow]\nplan_gate = true\n")
    config_enabled = parse_args(["--watch"], cwd=tmp_path, environ={})

    assert cli_enabled.plan_gate is True
    assert cli_enabled.plan_gate_source == "cli"
    assert config_enabled.plan_gate is True
    assert config_enabled.plan_gate_source == "repository-default"


def test_cli_overrides_watch_environment_defaults(tmp_path: Path) -> None:
    args = parse_args(
        ["--watch", "--no-skip-git-check", "--agent-live-stream-channels", "stderr"],
        cwd=tmp_path,
        environ={
            "RUN_TASK_SKIP_GIT_CHECK": "1",
            "RUN_TASK_WATCH_STREAM_CHANNELS": "both",
        },
    )

    assert args.skip_git_check is False
    assert args.agent_live_stream_channels == "stderr"


def test_invalid_environment_boolean_is_a_configuration_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="RUN_TASK_SKIP_GIT_CHECK"):
        parse_args([], cwd=tmp_path, environ={"RUN_TASK_SKIP_GIT_CHECK": "sometimes"})


def test_watch_default_logs_git_check_and_stream_configuration(
    tmp_path: Path, caplog
) -> None:
    args = parse_args(["--watch"], cwd=tmp_path, environ={})

    with caplog.at_level(logging.INFO):
        result = run_cli(
            args,
            run_pipeline_fn=lambda *_args, **_kwargs: 0,
            watch_inbox_fn=lambda **_kwargs: 0,
            find_task_file_fn=lambda _path: tmp_path / "task.md",
        )

    assert result == 0
    assert "live stream channels = stdout" in caplog.text
    assert "enabling --skip-git-check by default" in caplog.text
    assert "RUN_TASK_SKIP_GIT_CHECK" in caplog.text
    assert "disabling --plan-gate by default" not in caplog.text


def test_invalid_stream_environment_warning_uses_configured_log_format(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    (tmp_path / "task.md").write_text("# task\n", encoding="utf-8")
    env = os.environ.copy()
    env["RUN_TASK_WATCH_STREAM_CHANNELS"] = "invalid"

    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "src" / "cli.py"),
            "--dry-run",
            "--skip-git-check",
            "--test-command",
            "",
            "--no-agent-live-stream",
            "--agent-output",
            "none",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "[WARNING] Invalid RUN_TASK_WATCH_STREAM_CHANNELS='invalid'" in result.stderr
    assert re.search(
        r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] \[WARNING\] ",
        result.stderr,
        re.MULTILINE,
    )


def test_cli_and_runtime_defaults_cannot_drift() -> None:
    assert cli.DEFAULT_TASK_FILE == orchestrator.DEFAULT_TASK_FILE
    assert cli.DEFAULT_AGENTS_FILE == orchestrator.DEFAULT_AGENTS_FILE
    assert "--development-mode" not in build_parser().format_help()
    assert "--from-phase" not in build_parser().format_help()


def test_declared_python_floor_matches_standard_library_toml_requirement() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    with (repo_root / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]

    assert project["requires-python"] == ">=3.11"
    assert project["dependencies"] == []


def test_readme_cli_defaults_match_resolved_parser_contract() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    readme = (repo_root / "README.md").read_text(encoding="utf-8")

    assert "| `--resume` / `--no-resume` | automatisch |" in readme
    assert "| `--agent-output <none\\|summary\\|full>` | `none` |" in readme
    assert "| `--agent-live-stream` / `--no-agent-live-stream` | an |" in readme
    assert "| `--skip-git-check` / `--no-skip-git-check` | aus; im Watch-Modus an |" in readme
    assert "| Claude | `--claude-binary`, `--claude-model`" in readme
    assert "`claude`, `sonnet`, 1800s, `high`" in readme
    assert "Opus ist nicht der Standard" in readme
    assert "`gpt-5.6-sol`" in readme
    assert build_parser().get_default("agent_output") == "none"
    assert build_parser().get_default("agent_live_stream") is True
