from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Mapping


DEFAULT_TIMEOUT_SECONDS = 1800
VALID_EFFORTS = ("low", "medium", "high", "xhigh", "max")


class AgentConfigError(ValueError):
    """Raised when role-local agent configuration is invalid."""


@dataclass(frozen=True)
class AgentSettings:
    name: str
    binary: str
    model: str
    timeout_seconds: int
    effort: str
    max_budget_usd: float | None = None


_DEFAULT_MODELS = {
    "codex": "gpt-5.6-sol",
    "claude": "sonnet",
}

_DEFAULT_EFFORTS = {
    "codex": "medium",
    "claude": "high",
}


def _add_role_arguments(parser: argparse.ArgumentParser, role: str) -> None:
    label = role.capitalize()
    parser.add_argument(
        f"--{role}-binary",
        help=f"{label} CLI binary or explicit path (default: RUN_TASK_{role.upper()}_BINARY or detection).",
    )
    parser.add_argument(
        f"--{role}-model",
        help=f"{label} model (default: RUN_TASK_{role.upper()}_MODEL or role default).",
    )
    parser.add_argument(
        f"--{role}-timeout",
        type=int,
        help=f"Hard {label} process timeout in seconds (default: RUN_TASK_{role.upper()}_TIMEOUT or 1800).",
    )
    parser.add_argument(
        f"--{role}-effort",
        choices=VALID_EFFORTS,
        help=f"{label} reasoning effort (default: RUN_TASK_{role.upper()}_EFFORT or role default).",
    )


def add_agent_arguments(parser: argparse.ArgumentParser) -> None:
    """Add local, non-repository agent configuration to the public CLI."""
    for role in ("codex", "claude"):
        _add_role_arguments(parser, role)
    parser.add_argument(
        "--claude-max-budget-usd",
        type=float,
        help=(
            "Optional Claude print-mode budget guard in USD "
            "(default: RUN_TASK_CLAUDE_MAX_BUDGET_USD or unset)."
        ),
    )


def _non_empty(value: object, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AgentConfigError(f"{location} must be a non-empty string")
    return value.strip()


def _positive_int(value: object, location: str) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise AgentConfigError(f"{location} must be a positive integer") from exc
    if parsed <= 0:
        raise AgentConfigError(f"{location} must be a positive integer")
    return parsed


def _positive_float(value: object, location: str) -> float:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise AgentConfigError(f"{location} must be a positive number") from exc
    if parsed <= 0:
        raise AgentConfigError(f"{location} must be a positive number")
    return parsed


def _resolve(
    args: argparse.Namespace,
    environ: Mapping[str, str],
    role: str,
    field: str,
    default: object,
) -> object:
    cli_value = getattr(args, f"{role}_{field}")
    if cli_value is not None:
        return cli_value
    env_name = f"RUN_TASK_{role.upper()}_{field.upper()}"
    if env_name in environ and environ[env_name].strip():
        return environ[env_name]
    return default


def resolve_agent_settings(
    args: argparse.Namespace,
    environ: Mapping[str, str],
) -> dict[str, AgentSettings]:
    """Resolve CLI > environment > role defaults without reading repository TOML."""
    default_binaries = {
        "codex": "codex",
        "claude": "claude",
    }
    settings: dict[str, AgentSettings] = {}
    for role in ("codex", "claude"):
        binary = _non_empty(
            _resolve(args, environ, role, "binary", default_binaries[role]),
            f"{role} binary",
        )
        model = _non_empty(
            _resolve(args, environ, role, "model", _DEFAULT_MODELS[role]),
            f"{role} model",
        )
        timeout_seconds = _positive_int(
            _resolve(args, environ, role, "timeout", DEFAULT_TIMEOUT_SECONDS),
            f"{role} timeout",
        )
        effort = _non_empty(
            _resolve(args, environ, role, "effort", _DEFAULT_EFFORTS[role]),
            f"{role} effort",
        ).lower()
        if effort not in VALID_EFFORTS:
            raise AgentConfigError(
                f"{role} effort must be one of {', '.join(VALID_EFFORTS)}; got {effort!r}"
            )
        settings[role] = AgentSettings(
            name=role,
            binary=binary,
            model=model,
            timeout_seconds=timeout_seconds,
            effort=effort,
        )

    budget_value = args.claude_max_budget_usd
    if budget_value is None:
        raw_budget = environ.get("RUN_TASK_CLAUDE_MAX_BUDGET_USD", "").strip()
        budget_value = raw_budget or None
    if budget_value is not None:
        budget = _positive_float(budget_value, "claude max budget USD")
        claude = settings["claude"]
        settings["claude"] = AgentSettings(
            name=claude.name,
            binary=claude.binary,
            model=claude.model,
            timeout_seconds=claude.timeout_seconds,
            effort=claude.effort,
            max_budget_usd=budget,
        )
    return settings


def default_agent_settings() -> dict[str, AgentSettings]:
    """Return deterministic defaults for compatibility imports and unit tests."""
    namespace = argparse.Namespace(
        **{
            f"{role}_{field}": None
            for role in ("codex", "claude")
            for field in ("binary", "model", "timeout", "effort")
        },
        claude_max_budget_usd=None,
    )
    return resolve_agent_settings(namespace, {})
