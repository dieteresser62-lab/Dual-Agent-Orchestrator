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


def _write_config(repo: Path, text: str) -> Path:
    path = repo / "orchestrator.toml"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _isolate_process_environment(monkeypatch) -> None:
    for name in (
        "RUN_TASK_CONFIG",
        "RUN_TASK_SKIP_GIT_CHECK",
        "RUN_TASK_TEST_CMD",
        "RUN_TASK_WATCH_STREAM_CHANNELS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_task_file_accepts_positional_compatibility_path(tmp_path: Path) -> None:
    args = parse_args(["work.md"], cwd=tmp_path, environ={})

    assert args.task_file == "work.md"


def test_task_file_rejects_positional_and_explicit_paths(tmp_path: Path, capsys) -> None:
    with pytest.raises(SystemExit):
        parse_args(["first.md", "--task-file", "second.md"], cwd=tmp_path, environ={})

    assert "either positionally or with --task-file" in capsys.readouterr().err


def test_test_command_precedence_cli_environment_repo_and_detection(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        '[validation]\ndefault_command = "from-repo"\n',
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
    cli_config.write_text('[validation]\ndefault_command = "from-cli-config"\n', encoding="utf-8")
    env_config.write_text('[validation]\ndefault_command = "from-env-config"\n', encoding="utf-8")

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
    env_config.write_text('[validation]\ndefault_command = "environment"\n', encoding="utf-8")

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
    _write_config(tmp_path, '[validation]\ndefault_command = ""\n')
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
default_command = "pytest"

[[validation.rules]]
patterns = ["engine/**"]
command = "npm run build:engine"

[workflow]
manual_slice_gate = true
""".strip(),
    )

    config = load_repo_config(path)

    assert config.source == path.resolve()
    assert config.paths.productive == ("src/**/*.py",)
    assert config.stop_rules[0].id == "DOMAIN-001"
    assert config.validation.default_command == "pytest"
    assert config.validation.rules[0].patterns == ("engine/**",)
    assert config.workflow.manual_slice_gate is True


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("unknown = true\n", "Unknown key(s) in root: unknown"),
        ("[workflow]\nmanual_slice_gate = \"yes\"\n", "must be a boolean"),
        ("[paths]\nproductive = [\"../outside/**\"]\n", "must not escape"),
        ("[paths]\nproductive = [\"C:/outside/**\"]\n", "must be relative"),
        ("[paths]\nproductive = [\"src\\\\**\"]\n", "platform-neutral separator"),
        ("[paths]\nproductive = []\n", "must contain at least one path pattern"),
        (
            "[[stop_rules]]\nid = \"S-001\"\ndescription = \"one\"\n"
            "[[stop_rules]]\nid = \"S-001\"\ndescription = \"two\"\n",
            "Duplicate stop rule id: S-001",
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


def test_cli_and_runtime_defaults_cannot_drift() -> None:
    assert cli.DEFAULT_TASK_FILE == orchestrator.DEFAULT_TASK_FILE
    assert cli.DEFAULT_AGENTS_FILE == orchestrator.DEFAULT_AGENTS_FILE
    assert build_parser().get_default("max_shared_chars") == orchestrator.MAX_SHARED_CHARS


def test_declared_python_floor_matches_standard_library_toml_requirement() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    with (repo_root / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]

    assert project["requires-python"] == ">=3.11"
    assert project["dependencies"] == []


def test_readme_cli_defaults_match_resolved_parser_contract() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    readme = (repo_root / "README.md").read_text(encoding="utf-8")

    assert "| `--resume` / `--no-resume` | auto |" in readme
    assert "| `--agent-output <none\\|summary\\|full>` | `none` |" in readme
    assert "| `--agent-live-stream` / `--no-agent-live-stream` | on |" in readme
    assert "| `--skip-git-check` / `--no-skip-git-check` | off; on in watch mode |" in readme
    assert build_parser().get_default("agent_output") == "none"
    assert build_parser().get_default("agent_live_stream") is True
