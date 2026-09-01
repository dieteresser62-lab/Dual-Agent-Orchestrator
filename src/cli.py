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
from agent_runtime import QuotaWaitPolicy, TransientRetryPolicy
from gates import PathClasses, STOP_RULE_ID_PATTERN, StopRule
from validation_matrix import (
    DEFAULT_VALIDATION_TIMEOUT_SECONDS,
    ValidationCommand,
    ValidationMatrix as ValidationConfig,
    ValidationMatrixError,
    ValidationRule,
)
from provider_input_budget import (
    ProviderInputBudgetError,
    ProviderInputBudgetPolicy,
    ProviderInputBudgetRule,
    default_provider_input_budget_policy,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_AGENTS_FILE = (PROJECT_ROOT / "AGENTS.md").resolve()
DEFAULT_CONFIG_NAME = "orchestrator.toml"
DEFAULT_TASK_FILE = "task.md"
DEFAULT_TEST_COMMAND = ""
DEFAULT_WATCH_STREAM_CHANNELS = "stdout"
WATCH_STREAM_CHANNELS = ("both", "stdout", "stderr")


class ConfigError(ValueError):
    """Raised when declarative or environment configuration is invalid."""


@dataclass(frozen=True)
class WorkflowConfig:
    manual_slice_gate: bool = False
    plan_gate: bool = False
    test_change_gate: bool = False


@dataclass(frozen=True)
class RepoConfig:
    paths: PathClasses = field(default_factory=PathClasses)
    stop_rules: tuple[StopRule, ...] = ()
    validation: ValidationConfig = field(default_factory=ValidationConfig)
    workflow: WorkflowConfig = field(default_factory=WorkflowConfig)
    validation_declared: bool = False
    source: Path | None = None
    provider_input_budget: ProviderInputBudgetPolicy = field(
        default_factory=default_provider_input_budget_policy
    )


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
    try:
        return PathClasses(
            productive=_pattern_list(
                table["productive"], "paths.productive", allow_empty=False
            )
            if "productive" in table
            else defaults.productive,
            tests=_pattern_list(table["tests"], "paths.tests")
            if "tests" in table
            else defaults.tests,
            documentation=_pattern_list(table["documentation"], "paths.documentation")
            if "documentation" in table
            else defaults.documentation,
            generated=_pattern_list(table["generated"], "paths.generated")
            if "generated" in table
            else defaults.generated,
        )
    except ValueError as exc:
        raise ConfigError(f"Invalid [paths] configuration: {exc}") from exc


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
    _reject_unknown_keys(
        table,
        {
            "default_command",
            "default_shell_command",
            "default_timeout_seconds",
            "rules",
        },
        "[validation]",
    )
    default_timeout = _positive_config_int(
        table.get("default_timeout_seconds", DEFAULT_VALIDATION_TIMEOUT_SECONDS),
        "validation.default_timeout_seconds",
    )
    default_command = _load_validation_command(
        table,
        argv_key="default_command",
        shell_key="default_shell_command",
        location="validation default",
        timeout_seconds=default_timeout,
        required=False,
    )

    raw_rules = table.get("rules", [])
    if not isinstance(raw_rules, list):
        raise ConfigError("validation.rules must be an array of tables")
    rules: list[ValidationRule] = []
    for index, raw_rule in enumerate(raw_rules):
        location = f"validation.rules[{index}]"
        rule = _require_table(raw_rule, location)
        _reject_unknown_keys(
            rule, {"patterns", "command", "shell_command", "timeout_seconds"}, location
        )
        timeout = _positive_config_int(
            rule.get("timeout_seconds", default_timeout), f"{location}.timeout_seconds"
        )
        try:
            rules.append(
                ValidationRule(
                    patterns=_pattern_list(
                        rule.get("patterns"), f"{location}.patterns", allow_empty=False
                    ),
                    command=_load_validation_command(
                        rule,
                        argv_key="command",
                        shell_key="shell_command",
                        location=location,
                        timeout_seconds=timeout,
                        required=True,
                    ),
                )
            )
        except ValidationMatrixError as exc:
            raise ConfigError(f"Invalid {location}: {exc}") from exc
    try:
        return ValidationConfig(default_command=default_command, rules=tuple(rules))
    except ValidationMatrixError as exc:
        raise ConfigError(f"Invalid [validation] configuration: {exc}") from exc


def _positive_config_int(value: object, location: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConfigError(f"{location} must be a positive integer")
    return value


def _load_validation_command(
    table: Mapping[str, object],
    *,
    argv_key: str,
    shell_key: str,
    location: str,
    timeout_seconds: int,
    required: bool,
) -> ValidationCommand | None:
    present = tuple(key for key in (argv_key, shell_key) if key in table)
    if len(present) > 1:
        raise ConfigError(
            f"{location} must declare only one of {argv_key} or {shell_key}"
        )
    if not present:
        if required:
            raise ConfigError(
                f"{location} must declare {argv_key} or {shell_key}"
            )
        return None
    try:
        if present[0] == argv_key:
            raw_argv = table[argv_key]
            if not isinstance(raw_argv, list) or not raw_argv:
                raise ConfigError(f"{location}.{argv_key} must be a non-empty string array")
            if any(not isinstance(item, str) for item in raw_argv):
                raise ConfigError(f"{location}.{argv_key} must contain only strings")
            return ValidationCommand(argv=tuple(raw_argv), timeout_seconds=timeout_seconds)
        if not required and isinstance(table[shell_key], str) and not table[shell_key].strip():
            return None
        return ValidationCommand(
            shell_command=_require_non_empty_string(
                table[shell_key], f"{location}.{shell_key}"
            ),
            timeout_seconds=timeout_seconds,
        )
    except ValidationMatrixError as exc:
        raise ConfigError(f"Invalid {location}: {exc}") from exc


def _load_workflow(data: object) -> WorkflowConfig:
    table = _require_table(data, "[workflow]")
    _reject_unknown_keys(
        table,
        {"manual_slice_gate", "plan_gate", "test_change_gate"},
        "[workflow]",
    )
    manual_slice_gate = table.get("manual_slice_gate", False)
    plan_gate = table.get("plan_gate", False)
    test_change_gate = table.get("test_change_gate", False)
    if not isinstance(manual_slice_gate, bool):
        raise ConfigError("workflow.manual_slice_gate must be a boolean")
    if not isinstance(plan_gate, bool):
        raise ConfigError("workflow.plan_gate must be a boolean")
    if not isinstance(test_change_gate, bool):
        raise ConfigError("workflow.test_change_gate must be a boolean")
    return WorkflowConfig(
        manual_slice_gate=manual_slice_gate,
        plan_gate=plan_gate,
        test_change_gate=test_change_gate,
    )


def _load_provider_input_budget(data: object) -> ProviderInputBudgetPolicy:
    if not isinstance(data, list):
        raise ConfigError("provider_input_budget must be an array of tables")
    rules: list[ProviderInputBudgetRule] = []
    for index, raw_rule in enumerate(data):
        location = f"provider_input_budget[{index}]"
        rule = _require_table(raw_rule, location)
        _reject_unknown_keys(
            rule, {"provider", "role", "operation", "max_chars", "max_bytes"}, location
        )
        try:
            rules.append(
                ProviderInputBudgetRule(
                    provider=_require_non_empty_string(
                        rule.get("provider"), f"{location}.provider"
                    ),
                    role=_require_non_empty_string(rule.get("role"), f"{location}.role"),
                    operation=_require_non_empty_string(
                        rule.get("operation"), f"{location}.operation"
                    ),
                    max_chars=_positive_config_int(
                        rule.get("max_chars"), f"{location}.max_chars"
                    ),
                    max_bytes=_positive_config_int(
                        rule.get("max_bytes"), f"{location}.max_bytes"
                    ),
                )
            )
        except ProviderInputBudgetError as exc:
            raise ConfigError(f"Invalid {location}: {exc}") from exc
    explicit_keys = tuple(rule.key for rule in rules)
    if len(explicit_keys) != len(set(explicit_keys)):
        raise ConfigError("Invalid provider_input_budget: duplicate rules are not allowed")
    merged = {rule.key: rule for rule in default_provider_input_budget_policy().rules}
    merged.update({rule.key: rule for rule in rules})
    return ProviderInputBudgetPolicy(tuple(merged.values()))


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
    _reject_unknown_keys(
        raw,
        {"paths", "stop_rules", "validation", "workflow", "provider_input_budget"},
        "root",
    )
    return RepoConfig(
        paths=_load_path_classes(raw["paths"]) if "paths" in raw else PathClasses(),
        stop_rules=_load_stop_rules(raw["stop_rules"]) if "stop_rules" in raw else (),
        validation=_load_validation(raw["validation"])
        if "validation" in raw
        else ValidationConfig(),
        workflow=_load_workflow(raw["workflow"])
        if "workflow" in raw
        else WorkflowConfig(),
        validation_declared="validation" in raw,
        source=resolved,
        provider_input_budget=(
            _load_provider_input_budget(raw["provider_input_budget"])
            if "provider_input_budget" in raw
            else default_provider_input_budget_policy()
        ),
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
    if data.get("cache_format") == "workflow-state-projection-v1":
        projected = data.get("state")
        if not isinstance(projected, dict):
            return True, False, False
        data = projected
    if data.get("version") == 3:
        units = data.get("work_units")
        slices = data.get("slices")
        current_id = data.get("current_work_unit_id")
        current = next(
            (
                item for item in units
                if isinstance(item, dict) and item.get("work_unit_id") == current_id
            ),
            {},
        ) if isinstance(units, list) else {}
        completed = (
            current.get("kind") == "final_review"
            and current.get("status") == "completed"
            and isinstance(slices, list)
            and bool(slices)
            and all(isinstance(item, dict) and item.get("status") == "completed" for item in slices)
        )
        frozen = current.get("status") in {
            "waiting_for_quota",
            "waiting_for_retry",
            "awaiting_resume",
        }
    else:
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
            "Orchestrates the resumable state-v3 slice workflow with bounded reviews. "
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
    parser.add_argument(
        "--manual-slice-gate",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Require a resumable explicit user approval before each v3 slice commit.",
    )
    parser.add_argument(
        "--plan-gate",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Require explicit fingerprint-bound approval after Claude approves the "
            "plan (default: off)."
        ),
    )
    parser.add_argument(
        "--test-change-gate",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Require explicit fingerprint-bound approval for planned test-file changes "
            "before review (default: off)."
        ),
    )
    parser.add_argument(
        "--plan-only",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Create, review, and commit only the declared work-plan artifact; future "
            "implementation Slices stay inside that document."
        ),
    )
    parser.add_argument(
        "--work-plan",
        help="Repository-relative WORK_PLAN_PATH override for a PLAN_ONLY task.",
    )
    parser.add_argument(
        "--target-branch",
        help="Exact feature/<name> or codex/<name> branch required by the task.",
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
        "--gate-rationale",
        help="Rationale recorded for an explicit v3 gate decision.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate agent responses and tests to validate workflow wiring.",
    )
    parser.add_argument(
        "--dry-run-scenario",
        help="JSON scenario for a deterministic state-v3 dry-run without agent/API calls.",
    )
    parser.add_argument(
        "--dry-run-report",
        help="Optional path for the scripted dry-run JSON audit report.",
    )
    parser.add_argument(
        "--test-command",
        default=None,
        help="Shell command for tests. An explicit empty value skips tests.",
    )
    parser.add_argument(
        "--retry-incomplete-validation",
        action="store_true",
        help=(
            "Explicitly re-run a cached INCOMPLETE v3 validation for the same diff "
            "fingerprint after its environment was repaired."
        ),
    )
    parser.add_argument(
        "--retry-failed-validation",
        action="store_true",
        help=(
            "Explicitly re-run a cached FAIL v3 validation once per process for the "
            "same diff fingerprint after investigating or repairing the failure."
        ),
    )
    parser.add_argument(
        "--quota-auto-resume",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Automatically resume one quota-blocked role step when its reset is unambiguous.",
    )
    parser.add_argument(
        "--quota-safety-margin",
        type=int,
        default=None,
        help="Seconds added after a recognized quota reset (default: 60).",
    )
    parser.add_argument(
        "--quota-max-wait",
        type=int,
        default=None,
        help="Maximum automatic provider reset span in seconds (default: 604800).",
    )
    parser.add_argument(
        "--quota-max-auto-resumes",
        type=int,
        default=None,
        help="Maximum automatic continuations per blocked role step (default: 1).",
    )
    parser.add_argument(
        "--quota-heartbeat-interval",
        type=int,
        default=None,
        help="Quota-wait heartbeat interval in seconds (default: 3600).",
    )
    parser.add_argument(
        "--transient-retry-auto",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Automatically retry proven transient network failures (default: on).",
    )
    parser.add_argument(
        "--transient-retry-initial-delay",
        type=int,
        default=None,
        help="Initial transient retry delay in seconds (default: 5).",
    )
    parser.add_argument(
        "--transient-retry-max-delay",
        type=int,
        default=None,
        help="Maximum transient retry delay in seconds (default: 30).",
    )
    parser.add_argument(
        "--transient-retry-max-auto-resumes",
        type=int,
        default=None,
        help="Maximum automatic transient retries per role step (default: 2).",
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
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(raw_argv)
    args.resume_explicit = "--resume" in raw_argv
    args.task_file_explicit = any(
        token == "--task-file" or token.startswith("--task-file=")
        for token in raw_argv
    )

    if args.task_path and args.task_file:
        parser.error("task file must be provided either positionally or with --task-file, not both")
    args.task_file = args.task_file or args.task_path or DEFAULT_TASK_FILE
    del args.task_path

    if args.dry_run_scenario and not args.dry_run:
        parser.error("--dry-run-scenario requires --dry-run")
    if args.dry_run_report and not args.dry_run_scenario:
        parser.error("--dry-run-report requires --dry-run-scenario")

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
    if args.test_change_gate is None:
        args.test_change_gate = repo_config.workflow.test_change_gate
    if args.plan_gate is None:
        args.plan_gate = repo_config.workflow.plan_gate
        args.plan_gate_source = "repository-default"
    else:
        args.plan_gate_source = "cli"
    quota_defaults = QuotaWaitPolicy()
    quota_automatic = args.quota_auto_resume
    if quota_automatic is None:
        quota_automatic = _parse_env_bool("RUN_TASK_QUOTA_AUTO_RESUME", env)
    if quota_automatic is None:
        quota_automatic = quota_defaults.automatic
    args.quota_auto_resume = quota_automatic

    def quota_int(argument: int | None, environment_name: str, default: int) -> int:
        if argument is not None:
            return argument
        raw = env.get(environment_name)
        if raw is None or not raw.strip():
            return default
        try:
            return int(raw.strip())
        except ValueError as exc:
            raise ConfigError(f"{environment_name} must be an integer") from exc

    try:
        args.quota_wait_policy = QuotaWaitPolicy(
            automatic=quota_automatic,
            safety_margin_seconds=quota_int(
                args.quota_safety_margin,
                "RUN_TASK_QUOTA_SAFETY_MARGIN",
                quota_defaults.safety_margin_seconds,
            ),
            maximum_wait_seconds=quota_int(
                args.quota_max_wait,
                "RUN_TASK_QUOTA_MAX_WAIT",
                quota_defaults.maximum_wait_seconds,
            ),
            maximum_auto_resumes=quota_int(
                args.quota_max_auto_resumes,
                "RUN_TASK_QUOTA_MAX_AUTO_RESUMES",
                quota_defaults.maximum_auto_resumes,
            ),
            heartbeat_interval_seconds=quota_int(
                args.quota_heartbeat_interval,
                "RUN_TASK_QUOTA_HEARTBEAT_INTERVAL",
                quota_defaults.heartbeat_interval_seconds,
            ),
        )
    except ValueError as exc:
        parser.error(str(exc))

    transient_defaults = TransientRetryPolicy()
    transient_automatic = args.transient_retry_auto
    if transient_automatic is None:
        transient_automatic = _parse_env_bool("RUN_TASK_TRANSIENT_RETRY_AUTO", env)
    if transient_automatic is None:
        transient_automatic = transient_defaults.automatic
    try:
        args.transient_retry_policy = TransientRetryPolicy(
            automatic=transient_automatic,
            initial_delay_seconds=quota_int(
                args.transient_retry_initial_delay,
                "RUN_TASK_TRANSIENT_RETRY_INITIAL_DELAY",
                transient_defaults.initial_delay_seconds,
            ),
            maximum_delay_seconds=quota_int(
                args.transient_retry_max_delay,
                "RUN_TASK_TRANSIENT_RETRY_MAX_DELAY",
                transient_defaults.maximum_delay_seconds,
            ),
            maximum_auto_resumes=quota_int(
                args.transient_retry_max_auto_resumes,
                "RUN_TASK_TRANSIENT_RETRY_MAX_AUTO_RESUMES",
                transient_defaults.maximum_auto_resumes,
            ),
        )
    except ValueError as exc:
        parser.error(str(exc))

    gate_decision = True if args.approve_gate else False if args.reject_gate else None
    if gate_decision is not None:
        if args.resume is not True:
            parser.error("--approve-gate/--reject-gate requires explicit --resume")
        if not (args.gate_rationale or "").strip():
            parser.error("an explicit gate decision requires --gate-rationale")
    elif args.gate_rationale is not None:
        parser.error("--gate-rationale requires --approve-gate or --reject-gate")
    args.gate_decision = gate_decision

    if args.test_command is None:
        if "RUN_TASK_TEST_CMD" in env:
            args.test_command = env["RUN_TASK_TEST_CMD"]
        elif repo_config.validation.default_command is not None:
            command = repo_config.validation.default_command
            args.test_command = (
                command.shell_command
                if command.shell_command is not None
                else command.display
            )
        elif repo_config.validation_declared:
            args.test_command = ""
        else:
            args.test_command = detect_test_command(repo_root)

    args.agent_profile_overrides = frozenset(
        (role, field)
        for role in ("codex", "claude")
        for field in ("model", "effort")
        if getattr(args, f"{role}_{field}") is not None
        or bool(env.get(f"RUN_TASK_{role.upper()}_{field.upper()}", "").strip())
    )
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
            "Previous run was interrupted. Resuming from its persisted workflow step; "
            "the exact prior reason remains recorded in .orchestrator/state.json."
        )
    if args.completed_state_replaced:
        logger.info("Existing state is completed (phase=done); starting a new run.")
    if args.dry_run_scenario:
        from dry_run_scenarios import DryRunScenarioError, run_dry_run_scenario_file
        from state_io import write_file
        from workflow import WorkflowExecutionError

        task_file = find_task_file_fn(args.task_file)
        scenario_path = Path(args.dry_run_scenario).expanduser().resolve()
        try:
            report = run_dry_run_scenario_file(scenario_path, task_file=task_file)
        except (DryRunScenarioError, WorkflowExecutionError) as exc:
            logger.error("Scripted dry-run failed: %s", exc)
            return 1
        if args.dry_run_report:
            report_path = Path(args.dry_run_report).expanduser().resolve()
            write_file(report_path, report.audit_document + "\n")
            logger.info("Scripted dry-run report: %s", report_path)
        logger.info(
            "Scripted dry-run %s: exit=%s state=%s step=%s validations=%s commits=%s",
            scenario_path.name,
            report.result.exit_code,
            report.result.state.current_work_unit.status.value,
            report.result.state.current_step.value,
            sum(report.validation_counts.values()),
            sum(call.startswith("commit:") for call in report.calls),
        )
        return report.result.exit_code
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
    task_path = Path(args.task_file)
    missing_bound_recovery = bool(
        args.resume
        and args.resume_explicit
        and args.task_file_explicit
        and not task_path.exists()
        and task_path.with_name(f"{task_path.name}.success").exists()
    )
    task_file = task_path if missing_bound_recovery else find_task_file_fn(args.task_file)
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
    logging.basicConfig(
        level=log_level,
        format="[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
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
