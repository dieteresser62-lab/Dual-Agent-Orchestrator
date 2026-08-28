"""Shared provider-schema projection, capability, and transport-profile rules."""

from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CAPABILITY_PATH = (
    PROJECT_ROOT / "schemas" / "native-provider-schema-capabilities-v1.json"
)
EXCEPTION_PATH = (
    PROJECT_ROOT / "schemas" / "native-provider-schema-exceptions-v1.json"
)
CAPABILITY_SCHEMA_VERSION = "native-provider-schema-capabilities-v1"
EXCEPTION_SCHEMA_VERSION = "native-provider-schema-exceptions-v1"
PROVIDER_VERSION_POLICY = "same-major-forward"
CLI_VERSION_PATTERNS = {
    "claude": re.compile(r"^(\d+)\.(\d+)\.(\d+) \(Claude Code\)$"),
    "codex": re.compile(r"^codex-cli (\d+)\.(\d+)\.(\d+)$"),
}


class NativeProviderSchemaError(ValueError):
    """A fail-closed provider capability or projection failure."""


@dataclass(frozen=True, slots=True)
class ProviderTransportProfile:
    provider: str
    binary_name: str
    model: str
    reasoning_or_effort: str
    schema_transport: str
    semantic_flags: tuple[str, ...]

    @property
    def document(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "binary_name": self.binary_name,
            "model": self.model,
            "reasoning_or_effort": self.reasoning_or_effort,
            "schema_transport": self.schema_transport,
            "semantic_flags": list(self.semantic_flags),
        }


def canonical_schema_json(document: Mapping[str, Any]) -> str:
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def schema_sha256(document: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_schema_json(document).encode("utf-8")).hexdigest()


def load_capability_table() -> dict[str, Any]:
    document = _load_json_object(CAPABILITY_PATH)
    if document.get("schema_version") != CAPABILITY_SCHEMA_VERSION:
        raise NativeProviderSchemaError("unknown provider capability schema version")
    providers = document.get("providers")
    if not isinstance(providers, list) or not providers:
        raise NativeProviderSchemaError("provider capability table must be non-empty")
    names: list[str] = []
    for item in providers:
        if not isinstance(item, dict):
            raise NativeProviderSchemaError("provider capability entry must be an object")
        name = _required_text(item, "provider")
        names.append(name)
        _required_text(item, "binary_name")
        cli_version = _required_text(item, "cli_version")
        version_policy = _required_text(item, "version_policy")
        if version_policy != PROVIDER_VERSION_POLICY:
            raise NativeProviderSchemaError(
                f"provider {name} must use the provider-wide version policy "
                f"{PROVIDER_VERSION_POLICY}"
            )
        _parse_cli_version(name, cli_version)
        _profile_from_document(item.get("transport_profile"))
        features = item.get("features")
        if not isinstance(features, dict) or not features or any(
            not isinstance(key, str) or not isinstance(value, bool)
            for key, value in features.items()
        ):
            raise NativeProviderSchemaError(
                f"provider {name} requires a boolean feature map"
            )
    if names != sorted(set(names)):
        raise NativeProviderSchemaError("provider capability entries must be sorted and unique")
    return document


def load_exception_table() -> dict[str, Any]:
    document = _load_json_object(EXCEPTION_PATH)
    if document.get("schema_version") != EXCEPTION_SCHEMA_VERSION:
        raise NativeProviderSchemaError("unknown provider exception schema version")
    entries = document.get("exceptions")
    if not isinstance(entries, list):
        raise NativeProviderSchemaError("provider exception table requires exceptions")
    identifiers: list[str] = []
    for item in entries:
        if not isinstance(item, dict):
            raise NativeProviderSchemaError("provider exception entry must be an object")
        identifiers.append(_required_text(item, "exception_id"))
        for key in (
            "provider",
            "error_code",
            "local_invariant",
            "missing_schema_feature",
            "provider_evidence",
            "regression_test",
        ):
            _required_text(item, key)
        operations = item.get("operations")
        if not isinstance(operations, list) or not operations or operations != sorted(set(operations)):
            raise NativeProviderSchemaError(
                f"exception {identifiers[-1]} operations must be sorted and unique"
            )
    if identifiers != sorted(set(identifiers)):
        raise NativeProviderSchemaError("provider exceptions must be sorted and unique")
    return document


def provider_capability(provider: str) -> Mapping[str, Any]:
    for item in load_capability_table()["providers"]:
        if item["provider"] == provider:
            return item
    raise NativeProviderSchemaError(f"no capability entry for provider {provider}")


def exact_cli_version_pattern(provider: str) -> str:
    """Return the sole accepted runtime-version regex from probed evidence."""
    version = _required_text(provider_capability(provider), "cli_version")
    return rf"^{re.escape(version)}$"


def compatible_cli_version(provider: str, cli_version: str) -> bool:
    """Return whether a runtime is forward-compatible with the probe baseline."""
    capability = provider_capability(provider)
    baseline = _parse_cli_version(provider, capability["cli_version"])
    actual = _parse_cli_version(provider, cli_version)
    if actual < baseline:
        return False
    return actual[0] == baseline[0]


def _parse_cli_version(provider: str, cli_version: str) -> tuple[int, int, int]:
    pattern = CLI_VERSION_PATTERNS.get(provider)
    if pattern is None:
        raise NativeProviderSchemaError(
            f"no CLI version grammar for provider {provider}"
        )
    matched = pattern.fullmatch(cli_version)
    if matched is None:
        raise NativeProviderSchemaError(
            f"{provider} CLI version has an unsupported format"
        )
    major, minor, patch = (int(part) for part in matched.groups())
    return major, minor, patch


def assert_provider_capabilities(
    provider: str,
    required_features: Iterable[str],
    *,
    cli_version: str | None = None,
    profile: ProviderTransportProfile | None = None,
) -> None:
    capability = provider_capability(provider)
    if cli_version is not None:
        try:
            compatible = compatible_cli_version(provider, cli_version)
        except NativeProviderSchemaError as exc:
            raise NativeProviderSchemaError(
                f"{provider} CLI version differs from the probed capability: {exc}"
            ) from exc
        if not compatible:
            raise NativeProviderSchemaError(
                f"{provider} CLI version differs from the probed capability policy"
            )
    if profile is not None and profile.document != capability["transport_profile"]:
        raise NativeProviderSchemaError(
            f"{provider} transport profile differs from the probed capability"
        )
    features = capability["features"]
    missing = sorted(
        feature for feature in set(required_features) if features.get(feature) is not True
    )
    if missing:
        raise NativeProviderSchemaError(
            f"{provider} schema features are not positively probed: {', '.join(missing)}"
        )


def registered_exceptions(provider: str) -> tuple[Mapping[str, Any], ...]:
    return tuple(
        item
        for item in load_exception_table()["exceptions"]
        if item["provider"] == provider
    )


def defensive_provider_projection(
    base_schema: Mapping[str, Any],
    *,
    provider: str,
    required_features: Iterable[str],
) -> dict[str, Any]:
    assert_provider_capabilities(provider, required_features)
    projected = copy.deepcopy(dict(base_schema))
    if provider == "codex":
        pending: list[object] = [projected]
        while pending:
            node = pending.pop()
            if isinstance(node, dict):
                node.pop("uniqueItems", None)
                if "(?" in str(node.get("pattern", "")):
                    node.pop("pattern")
                pending.extend(node.values())
            elif isinstance(node, list):
                pending.extend(node)
    return projected


def normalize_transport_profile(
    provider: str, command: Sequence[str]
) -> ProviderTransportProfile:
    if provider == "codex":
        return _normalize_codex(command)
    if provider == "claude":
        return _normalize_claude(command)
    raise NativeProviderSchemaError(f"unsupported transport profile provider {provider}")


def _normalize_codex(command: Sequence[str]) -> ProviderTransportProfile:
    if not command:
        raise NativeProviderSchemaError("empty Codex command")
    values = list(command[1:])
    model = _take_pair(values, "--model")
    effort_raw = _take_pair(values, "--config")
    prefix = 'model_reasoning_effort="'
    if not effort_raw.startswith(prefix) or not effort_raw.endswith('"'):
        raise NativeProviderSchemaError("unknown Codex --config value")
    effort = effort_raw[len(prefix) : -1]
    _discard_pair(values, "--sandbox")
    _discard_pair(values, "--output-schema")
    _discard_pair(values, "--output-last-message")
    color = _take_pair(values, "--color")
    expected = ["exec", "--skip-git-repo-check", "--ephemeral", "--json", "-"]
    if values != expected or color != "never":
        raise NativeProviderSchemaError(
            f"unclassified Codex command arguments: {values!r}"
        )
    return ProviderTransportProfile(
        provider="codex",
        binary_name=Path(command[0]).name,
        model=model,
        reasoning_or_effort=effort,
        schema_transport="output-schema-file",
        semantic_flags=(
            "exec",
            "--skip-git-repo-check",
            "--ephemeral",
            "--color=never",
            "--json",
            "--output-last-message=<runtime-file>",
            "stdin=-",
        ),
    )


def _normalize_claude(command: Sequence[str]) -> ProviderTransportProfile:
    if not command:
        raise NativeProviderSchemaError("empty Claude command")
    values = list(command[1:])
    model = _take_pair(values, "--model")
    effort = _take_pair(values, "--effort")
    expected_pairs = {
        "--output-format": "json",
        "--tools": "Read",
        "--allowedTools": "Read",
        "--disallowedTools": "Bash,Edit,Write,NotebookEdit,Grep,Glob",
        "--permission-mode": "dontAsk",
        "--setting-sources": "user",
        "--prompt-suggestions": "false",
    }
    for flag, expected in expected_pairs.items():
        if _take_pair(values, flag) != expected:
            raise NativeProviderSchemaError(f"unexpected Claude {flag} value")
    for ignored in ("--add-dir", "--system-prompt"):
        if ignored in values:
            _discard_pair(values, ignored)
    if "--max-budget-usd" in values:
        # Cost policy is intentionally not a schema-capability input (C-25).
        _discard_pair(values, "--max-budget-usd")
    _discard_pair(values, "--json-schema")
    flags = {
        "-p",
        "--safe-mode",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--disable-slash-commands",
    }
    positional = [item for item in values if item not in flags]
    seen_flags = {item for item in values if item in flags}
    if seen_flags != flags or len(positional) != 1:
        raise NativeProviderSchemaError(
            f"unclassified Claude command arguments: {values!r}"
        )
    return ProviderTransportProfile(
        provider="claude",
        binary_name=Path(command[0]).name,
        model=model,
        reasoning_or_effort=effort,
        schema_transport="json-schema-argument",
        semantic_flags=(
            "-p",
            "--output-format=json",
            "--tools=Read",
            "--allowedTools=Read",
            "--disallowedTools=Bash,Edit,Write,NotebookEdit,Grep,Glob",
            "--permission-mode=dontAsk",
            "--setting-sources=user",
            "--safe-mode",
            "--strict-mcp-config",
            "--prompt-suggestions=false",
            "--no-session-persistence",
            "--disable-slash-commands",
        ),
    )


def _take_pair(values: list[str], flag: str) -> str:
    try:
        index = values.index(flag)
    except ValueError as exc:
        raise NativeProviderSchemaError(f"missing command argument {flag}") from exc
    if index + 1 >= len(values):
        raise NativeProviderSchemaError(f"missing value for command argument {flag}")
    value = values[index + 1]
    del values[index : index + 2]
    return value


def _discard_pair(values: list[str], flag: str) -> None:
    _take_pair(values, flag)


def _profile_from_document(value: object) -> ProviderTransportProfile:
    if not isinstance(value, dict):
        raise NativeProviderSchemaError("transport_profile must be an object")
    semantic_flags = value.get("semantic_flags")
    if not isinstance(semantic_flags, list) or any(
        not isinstance(item, str) or not item for item in semantic_flags
    ):
        raise NativeProviderSchemaError("semantic_flags must be a string list")
    return ProviderTransportProfile(
        provider=_required_text(value, "provider"),
        binary_name=_required_text(value, "binary_name"),
        model=_required_text(value, "model"),
        reasoning_or_effort=_required_text(value, "reasoning_or_effort"),
        schema_transport=_required_text(value, "schema_transport"),
        semantic_flags=tuple(semantic_flags),
    )


def _required_text(document: Mapping[str, Any], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise NativeProviderSchemaError(f"{key} must be non-blank text")
    return value


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NativeProviderSchemaError(f"cannot load {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise NativeProviderSchemaError(f"{path.name} must contain an object")
    return value
