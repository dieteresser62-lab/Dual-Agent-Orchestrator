from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import tomllib
from dataclasses import dataclass, InitVar
from pathlib import Path
from typing import Mapping

from agent_roles import AgentSlot
import role_occupancy


DEFAULT_TIMEOUT_SECONDS: int | None = None
VALID_EFFORTS = ("low", "medium", "high", "xhigh", "max")


class AgentConfigError(ValueError):
    """Raised when role-local agent configuration is invalid."""


@dataclass(frozen=True)
class AgentSettings:
    name: str
    binary: str
    model: str
    timeout_seconds: int | None
    effort: str
    max_budget_usd: float | None = None
    profile_name: str = "scripted"
    antigravity_home: str | None = None
    antigravity_run_root: str | None = None
    toolchain_read_roots: tuple[str, ...] = ()
    reviewer_model_catalog_json: InitVar[str | None] = None
    fixed_environment: InitVar[tuple[tuple[str, str], ...]] = ()

    def __post_init__(self, reviewer_model_catalog_json, fixed_environment) -> None:
        # Runtime bindings are not public configuration or CLI Namespace fields.
        # InitVar keeps the frozen configuration projection byte-compatible;
        # replace() preserves their initialized values for each runtime slot.
        object.__setattr__(self, "reviewer_model_catalog_json", reviewer_model_catalog_json)
        object.__setattr__(self, "fixed_environment", fixed_environment)


@dataclass(frozen=True)
class AgentProfileConfig:
    provider: str
    binary: str
    model: str
    effort: str
    timeout_seconds: int | None
    max_budget_usd: float | None = None
    antigravity_home: str | None = None
    antigravity_run_root: str | None = None
    toolchain_read_roots: tuple[str, ...] = ()


def default_antigravity_home() -> str:
    return str(Path.home().resolve() / ".dao-antigravity-reviewer")


def default_antigravity_run_root() -> str:
    return f"/var/tmp/dao-agy-review-{os.getuid()}"


def _absolute_isolation_path(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value or value != value.strip() or not Path(value).is_absolute():
        raise AgentConfigError(f"{label} must be an absolute path")
    raw = Path(value)
    cursor = raw
    while cursor != cursor.parent:
        if cursor.is_symlink():
            raise AgentConfigError(f"{label} must not traverse a symlink")
        cursor = cursor.parent
    return raw.resolve(strict=False)


def _antigravity_paths(
    home: object, run_root: object, *, repository_root: Path, label: str,
) -> tuple[str, str]:
    selected_home = _absolute_isolation_path(home, f"{label}.home")
    selected_root = _absolute_isolation_path(run_root, f"{label}.run_root")
    personal = Path.home().resolve()
    repository = repository_root.resolve()
    if (selected_home == personal or selected_home.is_relative_to(personal / ".gemini")
        or selected_home.is_relative_to(repository) or repository.is_relative_to(selected_home)):
        raise AgentConfigError(f"{label}.home must be isolated from the personal HOME and repository")
    if not selected_root.is_relative_to(Path("/var/tmp")) or selected_root == Path("/var/tmp"):
        raise AgentConfigError(f"{label}.run_root must be below /var/tmp, outside /tmp and /home")
    if (selected_root.is_relative_to(repository) or repository.is_relative_to(selected_root)
        or selected_root.is_relative_to(selected_home) or selected_home.is_relative_to(selected_root)):
        raise AgentConfigError(f"{label}.run_root must not overlap HOME or repository")
    return str(selected_home), str(selected_root)


def isolation_options_digest(settings: AgentSettings) -> str | None:
    """Bind runtime catalog, fixed environment and paths; never credentials."""
    if settings.reviewer_model_catalog_json is not None or settings.fixed_environment:
        from model_catalog import reviewer_model_row_sha256
        catalog_binding = ({"reviewer_model_row_sha256": reviewer_model_row_sha256(settings.reviewer_model_catalog_json, settings.model)}
                           if settings.reviewer_model_catalog_json is not None else {"reviewer_model_catalog": None})
        payload = json.dumps({**catalog_binding,
                              "fixed_environment": settings.fixed_environment,
                              "toolchain_read_roots": settings.toolchain_read_roots}, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if settings.toolchain_read_roots:
        payload = json.dumps({"toolchain_read_roots": settings.toolchain_read_roots}, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if settings.name != "antigravity":
        return None
    home = settings.antigravity_home or default_antigravity_home()
    root = settings.antigravity_run_root or default_antigravity_run_root()
    payload = json.dumps({"home": home, "run_root": root}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_SHIPPED_TOML = tomllib.loads((Path(__file__).resolve().parents[1] / "orchestrator.toml").read_text(encoding="utf-8"))
DEFAULT_ROLE_PROFILES = {AgentSlot(name): profile for name, profile in _SHIPPED_TOML["roles"].items()}


def default_profiles() -> dict[str, AgentProfileConfig]:
    profiles = {}
    for name, raw in _SHIPPED_TOML["agent_profiles"].items():
        agy = raw.get("provider_options", {}).get("antigravity", {})
        home, root = (None, None)
        if raw["provider"] == "antigravity":
            home, root = _antigravity_paths(
                agy.get("home", default_antigravity_home()),
                agy.get("run_root", default_antigravity_run_root()),
                repository_root=Path(__file__).resolve().parents[1],
                label=f"shipped agent_profiles.{name}.provider_options.antigravity",
            )
        profiles[name] = AgentProfileConfig(
            raw["provider"], raw.get("binary", raw["provider"]), raw["model"],
            raw["effort"], _process_timeout(raw.get("timeout_seconds"), f"shipped agent_profiles.{name}.timeout_seconds"),
            raw.get("provider_options", {}).get("claude", {}).get("max_budget_usd"),  # allowlist:provider -- profile configuration: shipped USD option
            home, root,
        )
    return profiles


def parse_profile_tables(
    roles_raw: object | None, profiles_raw: object | None,
    *, repository_root: Path | None = None,
) -> tuple[dict[AgentSlot, str], dict[str, AgentProfileConfig]]:
    """Validate every named profile, including profiles unused by the three slots."""
    if roles_raw is not None and not isinstance(roles_raw, dict):
        raise AgentConfigError("roles must be a TOML table")
    if profiles_raw is not None and not isinstance(profiles_raw, dict):
        raise AgentConfigError("agent_profiles must be a TOML table")
    roles = dict(DEFAULT_ROLE_PROFILES)
    for name, value in (roles_raw or {}).items():
        try:
            slot = AgentSlot(name)
        except ValueError as exc:
            raise AgentConfigError(f"unknown role slot: {name}") from exc
        roles[slot] = _non_empty(value, f"roles.{name}")
    profiles = default_profiles()
    repository = repository_root or Path.cwd()
    for name, raw in (profiles_raw or {}).items():
        if not isinstance(name, str) or not name.strip() or not isinstance(raw, dict):
            raise AgentConfigError(f"agent_profiles.{name} must be a TOML table")
        unknown = set(raw) - {"provider", "binary", "model", "effort", "timeout_seconds", "provider_options"}
        if unknown:
            raise AgentConfigError(f"unknown agent_profiles.{name} keys: {sorted(unknown)}")
        base = profiles.get(name)
        if base is None and not {"provider", "model", "effort"}.issubset(raw):
            raise AgentConfigError(f"agent_profiles.{name} needs provider, model and effort")
        provider = _non_empty(raw.get("provider", base.provider if base else None), f"agent_profiles.{name}.provider")
        if provider not in MODEL_FAMILIES:
            raise AgentConfigError(f"agent_profiles.{name}.provider is unsupported: {provider}")
        binary = _non_empty(raw.get("binary", base.binary if base else provider), f"agent_profiles.{name}.binary")
        model = _selectable_model(provider, _non_empty(raw.get("model", base.model if base else None), f"agent_profiles.{name}.model"))
        effort = _non_empty(raw.get("effort", base.effort if base else None), f"agent_profiles.{name}.effort").lower()
        if effort not in VALID_EFFORTS:
            raise AgentConfigError(f"agent_profiles.{name}.effort is unsupported: {effort}")
        if provider == "antigravity" and effort == "xhigh":
            raise AgentConfigError(f"agent_profiles.{name}.effort is unsupported for antigravity: {effort}")
        timeout_raw = raw.get("timeout_seconds", base.timeout_seconds if base else None)
        if timeout_raw is not None and (isinstance(timeout_raw, bool) or not isinstance(timeout_raw, int) or timeout_raw < 0):
            raise AgentConfigError(f"agent_profiles.{name}.timeout_seconds must be a non-negative integer")
        timeout = _process_timeout(timeout_raw, f"agent_profiles.{name}.timeout_seconds")
        options = raw.get("provider_options", {})
        if not isinstance(options, dict) or set(options) - {"claude", "antigravity"}:  # allowlist:provider -- profile configuration: provider option table
            raise AgentConfigError(f"agent_profiles.{name}.provider_options is invalid")
        claude_options = options.get("claude", {})  # allowlist:provider -- profile configuration: provider option table
        if not isinstance(claude_options, dict) or set(claude_options) - {"max_budget_usd", "toolchain_read_roots"}:  # allowlist:provider -- profile configuration: provider option table
            raise AgentConfigError(f"agent_profiles.{name}.provider_options.claude is invalid")  # allowlist:provider -- profile configuration: provider option table
        if claude_options and provider != "claude":  # allowlist:provider -- profile configuration: provider option validation
            raise AgentConfigError(f"agent_profiles.{name}: Claude options require provider claude")  # allowlist:provider -- profile configuration: provider option validation
        if "max_budget_usd" in claude_options and (isinstance(claude_options["max_budget_usd"], bool) or not isinstance(claude_options["max_budget_usd"], (int, float))):  # allowlist:provider -- profile configuration: USD option
            raise AgentConfigError(f"agent_profiles.{name}.provider_options.claude.max_budget_usd must be numeric")  # allowlist:provider -- profile configuration: USD option
        budget = _positive_float(claude_options["max_budget_usd"], f"agent_profiles.{name}.provider_options.claude.max_budget_usd") if "max_budget_usd" in claude_options else (base.max_budget_usd if base and provider == base.provider else None)  # allowlist:provider -- profile configuration: USD option
        agy_options = options.get("antigravity", {})
        if not isinstance(agy_options, dict) or set(agy_options) - {"home", "run_root"}:
            raise AgentConfigError(f"agent_profiles.{name}.provider_options.antigravity has unknown keys or is invalid")
        if "antigravity" in options and provider != "antigravity":
            raise AgentConfigError(f"agent_profiles.{name}: Antigravity options require provider antigravity")
        home, root = (None, None)
        if provider == "antigravity":
            home, root = _antigravity_paths(
                agy_options.get("home", (base.antigravity_home if base and base.provider == provider else None) or default_antigravity_home()),
                agy_options.get("run_root", (base.antigravity_run_root if base and base.provider == provider else None) or default_antigravity_run_root()),
                repository_root=repository,
                label=f"agent_profiles.{name}.provider_options.antigravity",
            )
        tool_roots = _profile_toolchain_roots(claude_options, repository, name)  # allowlist:provider -- profile configuration: implementer toolchain
        profiles[name] = AgentProfileConfig(provider, binary, model, effort, timeout, budget, home, root, tool_roots)
    for slot, name in roles.items():
        if name not in profiles:
            raise AgentConfigError(f"roles.{slot.value} refers to missing agent profile {name!r}")
        selected_options = (profiles_raw or {}).get(name, {}).get("provider_options", {}).get("claude", {})  # allowlist:provider -- profile configuration: implementer-only options
        if (profiles[name].toolchain_read_roots or "toolchain_read_roots" in selected_options) and slot is not AgentSlot.IMPLEMENTER:
            raise AgentConfigError(f"roles.{slot.value}: toolchain_read_roots is implementer-only")
    return roles, profiles


def _profile_toolchain_roots(options: dict, repository: Path, name: str) -> tuple[str, ...]:
    from toolchain_paths import validate_toolchain_read_roots

    if "toolchain_read_roots" not in options:
        return ()
    protected = tuple(repository / path for path in (".git", ".orchestrator", "inbox", "outbox"))
    from agent_adapters import AgentOutputError
    try:
        if (repository / ".git").exists():
            from claude_implementer_adapter import protected_implementer_paths  # allowlist:provider -- profile configuration: toolchain protection roots
            protected = protected_implementer_paths(repository, repository / "inbox", repository / "outbox", "profile-load")
        return validate_toolchain_read_roots(options["toolchain_read_roots"], repository, protected)
    except (ValueError, AgentOutputError) as exc:
        raise AgentConfigError(f"agent_profiles.{name}: {exc}") from exc


def current_pre_toml_occupancy() -> dict[AgentSlot, str]:
    """Compatibility view of the shipped TOML slot defaults."""
    return role_occupancy.current_pre_toml_occupancy()


def current_provider_for_role(role: str) -> str:
    """Return the shipped default provider for a slot."""
    return current_pre_toml_occupancy()[AgentSlot(role)]


# Families remain aliases until the identity-bound runtime catalog is read.
MODEL_FAMILIES = {
    "codex": ("sol", "terra", "luna", "astra"),
    "claude": {"opus": "opus", "sonnet": "sonnet", "fable": "fable"},
    "antigravity": {"gemini-3.1-pro-high": "gemini-3.1-pro-high"},
}
_DEFAULT_MODELS = {provider: next(iter(families)) for provider, families in MODEL_FAMILIES.items()}

_DEFAULT_EFFORTS = {
    "codex": "high",
    "claude": "high",
}


def _add_role_arguments(parser: argparse.ArgumentParser, role: str) -> None:
    label = role.capitalize()
    flag = role.replace("_", "-")
    parser.add_argument(
        f"--{flag}-binary", dest=f"{role}_binary",
        help=f"{label} CLI binary or explicit path (default: RUN_TASK_{role.upper()}_BINARY or detection).",
    )
    parser.add_argument(
        f"--{flag}-model", dest=f"{role}_model",
        help=(
            f"{label} model family (default: RUN_TASK_{role.upper()}_MODEL or TOML profile)."
        ),
    )
    parser.add_argument(
        f"--{flag}-timeout", dest=f"{role}_timeout",
        help=(f"Hard {label} process timeout in seconds; 0 disables it "
              f"(default: RUN_TASK_{role.upper()}_TIMEOUT or no limit)."),
    )
    parser.add_argument(
        f"--{flag}-effort", dest=f"{role}_effort",
        choices=VALID_EFFORTS,
        help=f"{label} reasoning effort (default: RUN_TASK_{role.upper()}_EFFORT or role default).",
    )


def add_agent_arguments(parser: argparse.ArgumentParser) -> None:
    """Add local, non-repository agent configuration to the public CLI."""
    for role in ("implementer", "reviewer", "final_reviewer"):
        _add_role_arguments(parser, role)


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


def _process_timeout(value: object, location: str) -> int | None:
    if value is None or str(value).strip().lower() in {"0", "none"}:
        return None
    return _positive_int(value, location)


def _positive_float(value: object, location: str) -> float:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise AgentConfigError(f"{location} must be a positive number") from exc
    if not math.isfinite(parsed) or parsed <= 0:
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


def _selectable_model(role: str, value: str) -> str:
    if role == "antigravity" and re.fullmatch(r"gemini-[a-z0-9.-]+", value):
        return value
    if role == "codex":  # allowlist:provider -- profile configuration: catalog-resolved models
        if value.lower() in MODEL_FAMILIES[role]:
            return value.lower()
        if re.fullmatch(r"[a-z0-9][a-z0-9.-]*", value):
            return value
        raise AgentConfigError("model must be a family or full catalog model ID")
    families = MODEL_FAMILIES[role]
    model = families.get(value.lower(), value)
    if model not in families.values():
        allowed = ", ".join(f"{name} ({slug})" for name, slug in families.items())
        raise AgentConfigError(
            f"{role} model must be one of {allowed}; got {value!r}"
        )
    return model


def resolve_agent_settings(
    args: argparse.Namespace,
    environ: Mapping[str, str],
    *,
    roles: Mapping[AgentSlot, str] | None = None,
    profiles: Mapping[str, AgentProfileConfig] | None = None,
) -> dict[str, AgentSettings]:
    """Resolve CLI > nonempty role environment > profile > shipped defaults."""
    roles = roles or DEFAULT_ROLE_PROFILES
    profiles = profiles or default_profiles()
    settings: dict[str, AgentSettings] = {}
    for slot in AgentSlot:
        role = slot.value
        profile = profiles[roles[slot]]
        provider = profile.provider
        binary = _non_empty(
            _resolve(args, environ, role, "binary", profile.binary),
            f"{role} binary",
        )
        model = _selectable_model(
            provider,
            _non_empty(
                _resolve(args, environ, role, "model", profile.model),
                f"{role} model",
            ),
        )
        timeout_seconds = _process_timeout(
            _resolve(args, environ, role, "timeout", profile.timeout_seconds),
            f"{role} timeout",
        )
        effort = _non_empty(
            _resolve(args, environ, role, "effort", profile.effort),
            f"{role} effort",
        ).lower()
        if effort not in VALID_EFFORTS:
            raise AgentConfigError(
                f"{role} effort must be one of {', '.join(VALID_EFFORTS)}; got {effort!r}"
            )
        if provider == "antigravity" and effort == "xhigh":
            raise AgentConfigError(f"{role} effort is unsupported for antigravity: {effort}")
        settings[role] = AgentSettings(
            name=provider,
            binary=binary,
            model=model,
            timeout_seconds=timeout_seconds,
            effort=effort,
            max_budget_usd=profile.max_budget_usd,
            profile_name=roles[slot],
            antigravity_home=profile.antigravity_home,
            antigravity_run_root=profile.antigravity_run_root,
            toolchain_read_roots=profile.toolchain_read_roots,
        )
    if roles[AgentSlot.FINAL_REVIEWER] == roles[AgentSlot.REVIEWER]:
        inherited = settings["reviewer"]
        current = settings["final_reviewer"]
        settings["final_reviewer"] = AgentSettings(
            name=current.name,
            binary=_resolve(args, environ, "final_reviewer", "binary", inherited.binary),
            model=_selectable_model(current.name, _non_empty(_resolve(args, environ, "final_reviewer", "model", inherited.model), "final_reviewer model")),
            timeout_seconds=_process_timeout(_resolve(args, environ, "final_reviewer", "timeout", inherited.timeout_seconds), "final_reviewer timeout"),
            effort=_non_empty(_resolve(args, environ, "final_reviewer", "effort", inherited.effort), "final_reviewer effort").lower(),
            max_budget_usd=inherited.max_budget_usd,
            profile_name=current.profile_name,
            antigravity_home=inherited.antigravity_home,
            antigravity_run_root=inherited.antigravity_run_root,
            toolchain_read_roots=inherited.toolchain_read_roots,
        )
    return settings


def default_agent_settings() -> dict[str, AgentSettings]:
    """Return deterministic settings for all three independent slots."""
    namespace = argparse.Namespace(
        **{
            f"{role}_{field}": None
            for role in ("implementer", "reviewer", "final_reviewer")
            for field in ("binary", "model", "timeout", "effort")
        },
    )
    return resolve_agent_settings(namespace, {})
