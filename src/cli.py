#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Sequence

import tomllib

from agent_config import AgentConfigError, add_agent_arguments, resolve_agent_settings


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_AGENTS_FILE = (PROJECT_ROOT / "AGENTS.md").resolve()
DEFAULT_CONFIG_NAME = "orchestrator.toml"
DEFAULT_TASK_FILE = "task.md"
DEFAULT_TEST_COMMAND = ""
DEFAULT_MAX_SHARED_CHARS = 30000
DEFAULT_WATCH_STREAM_CHANNELS = "stdout"
WATCH_STREAM_CHANNELS = ("both", "stdout", "stderr")
STOP_RULE_ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9_-]*$")


class ConfigError(ValueError):
    """Raised when declarative or environment configuration is invalid."""


@dataclass(frozen=True)
class PathClasses:
    productive: tuple[str, ...] = ("src/**/*.py", "run_task", "*.toml")
    tests: tuple[str, ...] = ("tests/**",)
    documentation: tuple[str, ...] = ("docs/**", "*.md")
    generated: tuple[str, ...] = (".orchestrator/**", "**/__pycache__/**", ".pytest_cache/**")


@dataclass(frozen=True)
class StopRule:
    id: str
    description: str


@dataclass(frozen=True)
class ValidationRule:
    patterns: tuple[str, ...]
    command: str


@dataclass(frozen=True)
class ValidationConfig:
    default_command: str | None = None
    rules: tuple[ValidationRule, ...] = ()


@dataclass(frozen=True)
class WorkflowConfig:
    manual_slice_gate: bool = False


@dataclass(frozen=True)
class RepoConfig:
    paths: PathClasses = field(default_factory=PathClasses)
    stop_rules: tuple[StopRule, ...] = ()
    validation: ValidationConfig = field(default_factory=ValidationConfig)
    workflow: WorkflowConfig = field(default_factory=WorkflowConfig)
    source: Path | None = None


def _reject_unknown_keys(data: Mapping[str, object], allowed: set[str], location: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        names = ", ".join(unknown)
        raise ConfigError(f"Unknown key(s) in {location}: {names}")


def _require_table(value: object, location: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ConfigError(f"{location} must be a TOML table")
    return value


def _require_non_empty_string(value: object, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{location} must be a non-empty string")
    return value.strip()


def _validate_pattern(value: object, location: str) -> str:
    pattern = _require_non_empty_string(value, location)
    if "\x00" in pattern:
        raise ConfigError(f"{location} must not contain NUL bytes")
    if "\\" in pattern:
        raise ConfigError(f"{location} must use '/' as the platform-neutral separator")
    if PurePosixPath(pattern).is_absolute() or re.match(r"^[A-Za-z]:", pattern):
        raise ConfigError(f"{location} must be relative to the repository root")
    if ".." in PurePosixPath(pattern).parts:
        raise ConfigError(f"{location} must not escape the repository root")
    if pattern.count("[") != pattern.count("]"):
        raise ConfigError(f"{location} contains an unbalanced character class")
    return pattern


def _pattern_list(value: object, location: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ConfigError(f"{location} must be an array of path patterns")
    if not value and not allow_empty:
        raise ConfigError(f"{location} must contain at least one path pattern")
    return tuple(_validate_pattern(item, f"{location}[{index}]") for index, item in enumerate(value))


def _load_path_classes(data: object) -> PathClasses:
    table = _require_table(data, "[paths]")
    allowed = {"productive", "tests", "documentation", "generated"}
    _reject_unknown_keys(table, allowed, "[paths]")
    defaults = PathClasses()
    return PathClasses(
        productive=_pattern_list(
            table["productive"], "paths.productive", allow_empty=False
        )
        if "productive" in table
        else defaults.productive,
        tests=_pattern_list(table["tests"], "paths.tests") if "tests" in table else defaults.tests,
        documentation=_pattern_list(table["documentation"], "paths.documentation")
        if "documentation" in table
        else defaults.documentation,
        generated=_pattern_list(table["generated"], "paths.generated")
        if "generated" in table
        else defaults.generated,
    )


def _load_stop_rules(data: object) -> tuple[StopRule, ...]:
    if not isinstance(data, list):
        raise ConfigError("stop_rules must be an array of tables")
    rules: list[StopRule] = []
    seen: set[str] = set()
    for index, raw_rule in enumerate(data):
        location = f"stop_rules[{index}]"
        rule = _require_table(raw_rule, location)
        _reject_unknown_keys(rule, {"id", "description"}, location)
        rule_id = _require_non_empty_string(rule.get("id"), f"{location}.id")
        if not STOP_RULE_ID_PATTERN.fullmatch(rule_id):
            raise ConfigError(
                f"{location}.id must match {STOP_RULE_ID_PATTERN.pattern!r}"
            )
        if rule_id in seen:
            raise ConfigError(f"Duplicate stop rule id: {rule_id}")
        seen.add(rule_id)
        rules.append(
            StopRule(
                id=rule_id,
                description=_require_non_empty_string(
                    rule.get("description"), f"{location}.description"
                ),
            )
        )
    return tuple(rules)


def _load_validation(data: object) -> ValidationConfig:
    table = _require_table(data, "[validation]")
    _reject_unknown_keys(table, {"default_command", "rules"}, "[validation]")
    default_command: str | None = None
    if "default_command" in table:
        if not isinstance(table["default_command"], str):
            raise ConfigError("validation.default_command must be a string")
        default_command = table["default_command"]

    raw_rules = table.get("rules", [])
    if not isinstance(raw_rules, list):
        raise ConfigError("validation.rules must be an array of tables")
    rules: list[ValidationRule] = []
    for index, raw_rule in enumerate(raw_rules):
        location = f"validation.rules[{index}]"
        rule = _require_table(raw_rule, location)
        _reject_unknown_keys(rule, {"patterns", "command"}, location)
        rules.append(
            ValidationRule(
                patterns=_pattern_list(
                    rule.get("patterns"), f"{location}.patterns", allow_empty=False
                ),
                command=_require_non_empty_string(
                    rule.get("command"), f"{location}.command"
                ),
            )
        )
    return ValidationConfig(default_command=default_command, rules=tuple(rules))


def _load_workflow(data: object) -> WorkflowConfig:
    table = _require_table(data, "[workflow]")
    _reject_unknown_keys(table, {"manual_slice_gate"}, "[workflow]")
    value = table.get("manual_slice_gate", False)
    if not isinstance(value, bool):
        raise ConfigError("workflow.manual_slice_gate must be a boolean")
    return WorkflowConfig(manual_slice_gate=value)


def load_repo_config(path: Path) -> RepoConfig:
    """Load a strict optional repository configuration."""
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        return RepoConfig(source=None)
    if not resolved.is_file():
        raise ConfigError(f"Configuration path is not a file: {resolved}")
    try:
        with resolved.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Cannot read {resolved}: {exc}") from exc
    if not isinstance(raw, dict):  # pragma: no cover - TOML roots are tables
        raise ConfigError(f"Configuration root must be a TOML table: {resolved}")
    _reject_unknown_keys(raw, {"paths", "stop_rules", "validation", "workflow"}, "root")
    return RepoConfig(
        paths=_load_path_classes(raw["paths"]) if "paths" in raw else PathClasses(),
        stop_rules=_load_stop_rules(raw["stop_rules"]) if "stop_rules" in raw else (),
        validation=_load_validation(raw["validation"])
        if "validation" in raw
        else ValidationConfig(),
        workflow=_load_workflow(raw["workflow"])
        if "workflow" in raw
        else WorkflowConfig(),
        source=resolved,
    )


def _pyproject_uses_pytest(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return False
    tool = data.get("tool", {})
    return isinstance(tool, dict) and isinstance(tool.get("pytest"), dict)


def _package_has_test_script(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    scripts = data.get("scripts", {}) if isinstance(data, dict) else {}
    return isinstance(scripts, dict) and "test" in scripts


def _makefile_has_test_target(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    return re.search(r"^[ \t]*test[ \t]*:", text, re.MULTILINE) is not None


def detect_test_command(repo_root: Path) -> str:
    """Detect a conventional test command without invoking a shell."""
    if _pyproject_uses_pytest(repo_root / "pyproject.toml"):
        return "python3 -m pytest tests/ -v"
    if _package_has_test_script(repo_root / "package.json"):
        return "npm test"
    if _makefile_has_test_target(repo_root / "Makefile"):
        return "make test"
    return DEFAULT_TEST_COMMAND


def _parse_env_bool(name: str, environ: Mapping[str, str]) -> bool | None:
    if name not in environ:
        return None
    raw = environ[name].strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off", ""}:
        return False
    raise ConfigError(
        f"{name} must be one of 1/0, true/false, yes/no, or on/off; got {environ[name]!r}"
    )


def _read_state_for_auto_resume(state_file: Path) -> tuple[bool, bool, bool]:
    """Return (exists, completed, frozen) without mutating runtime state."""
    if not state_file.is_file():
        return False, False, False
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return True, False, False
    if not isinstance(data, dict):
        return True, False, False
    completed = str(data.get("phase", "")).strip().lower() == "done"
    frozen = any(
        str((data.get(phase) or {}).get("status", "")).strip().lower() == "frozen"
        for phase in ("phase1", "phase2")
        if isinstance(data.get(phase), dict)
    )
    return True, completed, frozen


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Orchestrates a dual-agent workflow with two phases and shared Markdown artifacts. "
            "Agent failures stop the run; agents are never substituted."
        )
    )
    parser.add_argument("task_path", nargs="?", help="Task file (compatibility shorthand).")
    parser.add_argument("--task-file", help=f"Path to task file (default: {DEFAULT_TASK_FILE}).")
    parser.add_argument(
        "--config",
        help=(
            "Path to repository configuration "
            f"(default: $RUN_TASK_CONFIG or ./{DEFAULT_CONFIG_NAME})."
        ),
    )
    parser.add_argument(
        "--agents-file",
        default=str(DEFAULT_AGENTS_FILE),
        help=(
            "Path to AGENTS instructions injected into all agent prompts "
            f"(default: {DEFAULT_AGENTS_FILE})."
        ),
    )
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Resume existing state; by default unfinished state is resumed automatically.",
    )
    parser.add_argument(
        "--force-overwrite-state",
        action="store_true",
        help="Overwrite existing orchestrator state without confirmation prompt.",
    )
    parser.add_argument(
        "--from-phase",
        choices=["phase1", "phase2"],
        help="Force the starting phase instead of using persisted state.",
    )
    parser.add_argument(
        "--max-agent-retries",
        type=int,
        default=1,
        help="Retries per agent call after the first failure (default: 1).",
    )
    parser.add_argument(
        "--phase1-max-cycles",
        type=int,
        default=4,
        help="Maximum planning cycles in phase 1 (default: 4).",
    )
    parser.add_argument(
        "--phase2-max-cycles",
        type=int,
        default=6,
        help="Maximum implementation/review cycles in phase 2 (default: 6).",
    )
    parser.add_argument(
        "--strict-preflight",
        action="store_true",
        help="Fail preflight when provider DNS resolution fails.",
    )
    parser.add_argument(
        "--skip-git-check",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Skip git cleanliness checks; enabled by default only in watch mode.",
    )
    parser.add_argument("--auto", action="store_true", default=True, help=argparse.SUPPRESS)
    parser.add_argument(
        "--manual-gate",
        action="store_true",
        help="Require confirmation before starting phase 2.",
    )
    parser.add_argument(
        "--manual-slice-gate",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Require a resumable explicit user approval before each v3 slice commit.",
    )
    gate_group = parser.add_mutually_exclusive_group()
    gate_group.add_argument(
        "--approve-gate",
        action="store_true",
        help="With --resume, approve the exact fingerprint-bound v3 user gate in state.",
    )
    gate_group.add_argument(
        "--reject-gate",
        action="store_true",
        help="With --resume, reject the exact fingerprint-bound v3 user gate in state.",
    )
    parser.add_argument(
        "--gate-actor",
        help="User or authority recorded for an explicit v3 gate decision.",
    )
    parser.add_argument(
        "--gate-rationale",
        help="Rationale recorded for an explicit v3 gate decision.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate agent responses and tests to validate workflow wiring.",
    )
    parser.add_argument(
        "--test-command",
        default=None,
        help="Shell command for tests. An explicit empty value skips tests.",
    )
    parser.add_argument(
        "--max-shared-chars",
        type=int,
        default=DEFAULT_MAX_SHARED_CHARS,
        help=f"Maximum shared-history characters in prompts (default: {DEFAULT_MAX_SHARED_CHARS}).",
    )
    parser.add_argument(
        "--file-snapshot-max-lines",
        type=int,
        default=500,
        help="Maximum lines per changed-file review snapshot (default: 500).",
    )
    parser.add_argument(
        "--file-snapshot-max-files",
        type=int,
        default=10,
        help="Maximum changed files included in a review snapshot (default: 10).",
    )
    parser.add_argument(
        "--no-recover",
        action="store_true",
        help="Disable recovery from the latest cycle checkpoint.",
    )
    parser.add_argument(
        "--agent-output",
        choices=["none", "summary", "full"],
        default="none",
        help="Amount of completed agent replies to print (default: none).",
    )
    parser.add_argument(
        "--agent-output-max-chars",
        type=int,
        default=1800,
        help="Maximum reply characters in summary mode (default: 1800).",
    )
    parser.add_argument(
        "--agent-live-stream",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Stream agent stdout/stderr while a process runs (default: on).",
    )
    parser.add_argument(
        "--agent-live-stream-mode",
        choices=["compact", "full"],
        default="compact",
        help="Live-stream verbosity (default: compact).",
    )
    parser.add_argument(
        "--agent-live-stream-channels",
        choices=WATCH_STREAM_CHANNELS,
        default=None,
        help="Live-stream channels (default: RUN_TASK_WATCH_STREAM_CHANNELS or stdout).",
    )
    add_agent_arguments(parser)
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Watch an inbox directory and process Markdown tasks continuously.",
    )
    parser.add_argument(
        "--inbox-dir",
        default="inbox",
        help="Inbox directory used by watch mode (default: inbox).",
    )
    parser.add_argument(
        "--outbox-dir",
        default="outbox",
        help="Outbox directory used by watch mode (default: outbox).",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=5.0,
        help="Watch polling interval in seconds (default: 5.0).",
    )
    parser.add_argument(
        "--watch-max-retries",
        dest="watch_max_retries",
        type=int,
        default=3,
        help="Retries per inbox task before poison-pill handling (default: 3).",
    )
    level_group = parser.add_mutually_exclusive_group()
    level_group.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    level_group.add_argument(
        "--quiet", action="store_true", help="Show warnings and errors only."
    )
    return parser


def parse_args(
    argv: Sequence[str] | None = None,
    *,
    cwd: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> argparse.Namespace:
    """Parse CLI arguments and resolve all wrapper-era defaults in Python."""
    repo_root = (cwd or Path.cwd()).resolve()
    env = os.environ if environ is None else environ
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.task_path and args.task_file:
        parser.error("task file must be provided either positionally or with --task-file, not both")
    args.task_file = args.task_file or args.task_path or DEFAULT_TASK_FILE
    del args.task_path

    cli_config_value = args.config if args.config not in {None, ""} else None
    env_config_value = env.get("RUN_TASK_CONFIG") or None
    config_value = cli_config_value or env_config_value
    config_path = Path(config_value).expanduser() if config_value else repo_root / DEFAULT_CONFIG_NAME
    if not config_path.is_absolute():
        config_path = repo_root / config_path
    if config_value is not None and not config_path.is_file():
        raise ConfigError(f"Explicit configuration file does not exist: {config_path.resolve()}")
    repo_config = load_repo_config(config_path)
    args.repo_config = repo_config
    args.config_file = repo_config.source
    if args.manual_slice_gate is None:
        args.manual_slice_gate = repo_config.workflow.manual_slice_gate

    gate_decision = True if args.approve_gate else False if args.reject_gate else None
    if gate_decision is not None:
        if args.resume is not True:
            parser.error("--approve-gate/--reject-gate requires explicit --resume")
        if not (args.gate_actor or "").strip():
            parser.error("an explicit gate decision requires --gate-actor")
        if not (args.gate_rationale or "").strip():
            parser.error("an explicit gate decision requires --gate-rationale")
    elif args.gate_actor is not None or args.gate_rationale is not None:
        parser.error("--gate-actor/--gate-rationale require --approve-gate or --reject-gate")
    args.gate_decision = gate_decision

    if args.test_command is None:
        if "RUN_TASK_TEST_CMD" in env:
            args.test_command = env["RUN_TASK_TEST_CMD"]
        elif repo_config.validation.default_command is not None:
            args.test_command = repo_config.validation.default_command
        else:
            args.test_command = detect_test_command(repo_root)

    try:
        args.agent_settings = resolve_agent_settings(args, env)
    except AgentConfigError as exc:
        raise ConfigError(str(exc)) from exc

    if args.skip_git_check is None:
        env_skip = _parse_env_bool("RUN_TASK_SKIP_GIT_CHECK", env)
        if env_skip is not None:
            args.skip_git_check = env_skip
            args.skip_git_check_source = "environment"
        elif args.watch:
            args.skip_git_check = True
            args.skip_git_check_source = "watch-default"
        else:
            args.skip_git_check = False
            args.skip_git_check_source = "default"
    else:
        args.skip_git_check_source = "cli"

    args.configuration_warnings = []
    if args.agent_live_stream_channels is None:
        channels = env.get(
            "RUN_TASK_WATCH_STREAM_CHANNELS", DEFAULT_WATCH_STREAM_CHANNELS
        )
        if channels not in WATCH_STREAM_CHANNELS:
            args.configuration_warnings.append(
                "Invalid RUN_TASK_WATCH_STREAM_CHANNELS="
                f"{channels!r}; using {DEFAULT_WATCH_STREAM_CHANNELS!r}."
            )
            channels = DEFAULT_WATCH_STREAM_CHANNELS
        args.agent_live_stream_channels = channels

    state_exists, state_completed, state_frozen = _read_state_for_auto_resume(
        repo_root / ".orchestrator" / "state.json"
    )
    args.auto_resume = False
    args.state_was_frozen = False
    args.completed_state_replaced = False
    if args.resume is None:
        args.resume = bool(state_exists and not state_completed and not args.watch)
        args.auto_resume = args.resume
    if args.resume:
        args.state_was_frozen = state_frozen
    elif state_completed and not args.watch:
        args.force_overwrite_state = True
        args.completed_state_replaced = True

    return args


def run_cli(
    args: argparse.Namespace,
    *,
    run_pipeline_fn: Callable[..., int],
    watch_inbox_fn: Callable[..., int],
    find_task_file_fn: Callable[[str | None], Path],
) -> int:
    logger = logging.getLogger(__name__)
    if args.auto_resume:
        logger.info("Existing unfinished state detected; resuming current run.")
    if args.state_was_frozen:
        logger.warning(
            "Previous run was frozen due to API quota. Ensure quota is available before resuming."
        )
    if args.completed_state_replaced:
        logger.info("Existing state is completed (phase=done); starting a new run.")
    if args.watch:
        logger.info("Watch mode: live stream channels = %s.", args.agent_live_stream_channels)
        if args.skip_git_check_source == "watch-default":
            logger.info(
                "Watch mode: enabling --skip-git-check by default "
                "(override with RUN_TASK_SKIP_GIT_CHECK=0 or --no-skip-git-check)."
            )
        if args.task_file != DEFAULT_TASK_FILE:
            logger.warning("Task file is ignored in --watch mode.")
        return watch_inbox_fn(
            inbox_dir=Path(args.inbox_dir),
            outbox_dir=Path(args.outbox_dir),
            poll_interval=max(0.1, float(args.poll_interval)),
            max_retries=max(0, int(args.watch_max_retries)),
            args=args,
            process_task=run_pipeline_fn,
        )
    if args.watch_max_retries != 3:
        logger.warning("--watch-max-retries is only used in --watch mode.")
    task_file = find_task_file_fn(args.task_file)
    return run_pipeline_fn(task_file, args)


def main(
    argv: Sequence[str] | None = None,
    *,
    run_pipeline_fn: Callable[..., int] | None = None,
    watch_inbox_fn: Callable[..., int] | None = None,
    find_task_file_fn: Callable[[str | None], Path] | None = None,
) -> int:
    try:
        args = parse_args(argv)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    log_level = logging.DEBUG if args.verbose else logging.WARNING if args.quiet else logging.INFO
    logging.basicConfig(level=log_level, format="[%(levelname)s] %(message)s")
    logger = logging.getLogger(__name__)
    for warning in args.configuration_warnings:
        logger.warning("%s", warning)

    if run_pipeline_fn is None or watch_inbox_fn is None or find_task_file_fn is None:
        import orchestrator

        run_pipeline_fn = orchestrator.run_pipeline
        watch_inbox_fn = orchestrator.watch_inbox
        find_task_file_fn = orchestrator.find_task_file
    return run_cli(
        args,
        run_pipeline_fn=run_pipeline_fn,
        watch_inbox_fn=watch_inbox_fn,
        find_task_file_fn=find_task_file_fn,
    )


if __name__ == "__main__":
    raise SystemExit(main())
