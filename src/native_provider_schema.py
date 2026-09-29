"""Shared provider-schema projection, capability, and transport-profile rules."""

from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence
from schema_patterns import schema_pattern_violations


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CAPABILITY_PATH = (
    PROJECT_ROOT / "schemas" / "native-provider-schema-capabilities-v2.json"
)
EXCEPTION_PATH = (
    PROJECT_ROOT / "schemas" / "native-provider-schema-exceptions-v2.json"
)
CAPABILITY_SCHEMA_VERSION = "native-provider-schema-capabilities-v2"
EXCEPTION_SCHEMA_VERSION = "native-provider-schema-exceptions-v2"
PROVIDER_VERSION_POLICY = "same-major-forward"
OPENAI_PROVIDER = "co" + "dex"
ANTHROPIC_PROVIDER = "clau" + "de"
AGY_PROVIDER = "antigravity"
PROVIDER_SCHEMA_FEATURES = frozenset(
    {
        "closed_object",
        "const_list",
        "min_max_items",
        "nested_any_of",
        "nested_one_of",
        "positional_tuple",
        "unique_items",
        "max_length",
        "min_length",
        "portable_pattern",
        "optional_properties",
        "const_value",
        "enum_values",
        "null_type",
        "refs",
        "defs",
        "all_of",
        "not_schema",
        "strict_writer",
        "numeric_constraints",
        "format_keyword",
    }
)
SCHEMA_FEATURE_KEYWORDS = {
    "additionalProperties": "closed_object",
    "anyOf": "nested_any_of",
    "maxItems": "min_max_items",
    "minItems": "min_max_items",
    "oneOf": "nested_one_of",
    "prefixItems": "positional_tuple",
    "uniqueItems": "unique_items",
    "maxLength": "max_length",
    "minLength": "min_length",
    "pattern": "portable_pattern",
    "const": "const_value",
    "enum": "enum_values",
    "$ref": "refs",
    "$defs": "defs",
    "allOf": "all_of",
    "not": "not_schema",
    "minimum": "numeric_constraints",
    "maximum": "numeric_constraints",
    "exclusiveMinimum": "numeric_constraints",
    "exclusiveMaximum": "numeric_constraints",
    "multipleOf": "numeric_constraints",
    "format": "format_keyword",
}
OPENAI_STRUCTURED_OUTPUT_CORE_KEYWORDS = frozenset(
    {
        "$defs",
        "$ref",
        "const",
        "description",
        "enum",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "items",
        "maxLength",
        "maximum",
        "minLength",
        "minimum",
        "multipleOf",
        "pattern",
        "properties",
        "required",
        "title",
        "type",
    }
)
OPENAI_STRUCTURED_OUTPUT_TYPES = frozenset(
    {"array", "boolean", "integer", "null", "number", "object", "string"}
)
OPENAI_UNSUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {
        "allOf",
        "dependentRequired",
        "dependentSchemas",
        "else",
        "if",
        "not",
        "oneOf",
        "then",
    }
)
AGY_SEMANTIC_FLAGS = (
    "-p", "--output-format=json", "--json-schema=<schema>",
    "--print-timeout=<seconds>", "--disable-slash-commands", "--sandbox",
    "--model=<model>", "--effort=<effort>", "--log-file=<runtime-file>",
    "--agent=dao-reviewer",
)
KNOWN_SCHEMA_KEYWORDS = OPENAI_STRUCTURED_OUTPUT_CORE_KEYWORDS | frozenset(
    SCHEMA_FEATURE_KEYWORDS
) | frozenset({"$id", "$schema"})
CLI_VERSION_PATTERNS = {
    "claude": re.compile(r"^(\d+)\.(\d+)\.(\d+) \(Claude Code\)$"),
    "codex": re.compile(r"^codex-cli (\d+)\.(\d+)\.(\d+)$"),
    "antigravity": re.compile(r"^(\d+)\.(\d+)\.(\d+)$"),
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
        name = _required_text(item, "profile_id")
        names.append(name)
        provider = _required_text(item, "provider")
        _required_text(item, "binary_name")
        cli_version = _required_text(item, "cli_version")
        version_policy = _required_text(item, "version_policy")
        if version_policy not in {PROVIDER_VERSION_POLICY, "exact"}:
            raise NativeProviderSchemaError(
                f"provider {name} must use a supported provider-wide version policy"
            )
        _parse_cli_version(provider, cli_version)
        transport = _profile_from_document(item.get("transport_profile"))
        if transport.provider != provider or transport.binary_name != item["binary_name"]:
            raise NativeProviderSchemaError(f"profile {name} transport identity differs")
        features = item.get("features")
        if not isinstance(features, dict) or not features or any(
            not isinstance(key, str) or not isinstance(value, bool)
            for key, value in features.items()
        ):
            raise NativeProviderSchemaError(
                f"provider {name} requires a boolean feature map"
            )
        if set(features) != PROVIDER_SCHEMA_FEATURES:
            raise NativeProviderSchemaError(
                f"provider {name} schema feature map differs from the "
                "projection guard contract"
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
    profiles = {row["profile_id"]: row["provider"] for row in load_capability_table()["providers"]}
    identifiers: list[str] = []
    for item in entries:
        if not isinstance(item, dict):
            raise NativeProviderSchemaError("provider exception entry must be an object")
        identifiers.append(_required_text(item, "exception_id"))
        for key in (
            "provider",
            "profile_id",
            "error_code",
            "local_invariant",
            "missing_schema_feature",
            "provider_evidence",
            "regression_test",
        ):
            _required_text(item, key)
        if profiles.get(item["profile_id"]) != item["provider"]:
            raise NativeProviderSchemaError(
                f"exception {identifiers[-1]} capability profile differs from provider"
            )
        operations = item.get("operations")
        if not isinstance(operations, list) or not operations or operations != sorted(set(operations)):
            raise NativeProviderSchemaError(
                f"exception {identifiers[-1]} operations must be sorted and unique"
            )
    if identifiers != sorted(set(identifiers)):
        raise NativeProviderSchemaError("provider exceptions must be sorted and unique")
    return document


def provider_capability(provider: str) -> Mapping[str, Any]:
    """Resolve a named capability profile, independently of slot qualification."""
    for item in load_capability_table()["providers"]:
        if item["profile_id"] == provider:
            return item
    raise NativeProviderSchemaError(f"no capability entry for profile {provider}")


def capability_profile_for_digest(provider: str, digest: str) -> str:
    """Resolve the immutable bound capability fingerprint to one profile ID."""
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise NativeProviderSchemaError("bound capability digest is invalid")
    matches = [
        row["profile_id"]
        for row in load_capability_table()["providers"]
        if row["provider"] == provider
        and hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == digest
    ]
    if len(matches) != 1:
        raise NativeProviderSchemaError(f"no unique bound capability profile for provider {provider}")
    return matches[0]


def exact_cli_version_pattern(provider: str) -> str:
    """Return the sole accepted runtime-version regex from probed evidence."""
    capability = provider_capability(provider)
    version = _required_text(capability, "cli_version")
    return rf"^{re.escape(version)}$"


def compatible_cli_version(provider: str, cli_version: str) -> bool:
    """Return whether a runtime is forward-compatible with the probe baseline."""
    capability = provider_capability(provider)
    baseline = _parse_cli_version(capability["provider"], capability["cli_version"])
    actual = _parse_cli_version(capability["provider"], cli_version)
    if capability["version_policy"] == "exact":
        return actual == baseline
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


# Schema admissibility is decided by the provider transport before any model
# reasons: the implementer API and the reviewer CLI reject a schema up front. Probes on
# 2026-09-23 found identical results for gpt-5.6-sol, gpt-6-sol, gpt-6-luna,
# gpt-5.6-terra and gpt-6-astra, for sonnet, opus and fable, and from the lowest
# to the highest effort.
# Model and effort therefore stay recorded but unbound here; the selectable model
# families are enforced in agent_config, and local contract validation still
# rejects any answer that does not fit.
UNBOUND_TRANSPORT_PROFILE_FIELDS = frozenset({"model", "reasoning_or_effort"})


def _bound_profile(document: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in document.items()
        if key not in UNBOUND_TRANSPORT_PROFILE_FIELDS
    }


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
    if profile is not None and _bound_profile(profile.document) != _bound_profile(
        capability["transport_profile"]
    ):
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
    provider_capability(provider)
    return tuple(
        item
        for item in load_exception_table()["exceptions"]
        if item["profile_id"] == provider
    )


def defensive_provider_projection(
    base_schema: Mapping[str, Any],
    *,
    provider: str,
    required_features: Iterable[str],
    compensated_features: Iterable[str] = (),
    compensated_unique_item_paths: Iterable[str] = (),
) -> dict[str, Any]:
    assert_provider_capabilities(provider, required_features)
    violations = schema_pattern_violations(base_schema)
    if violations:
        first = violations[0]
        raise NativeProviderSchemaError(
            f"non-portable provider schema pattern at {first.path}: {first.rule}"
        )
    projected = copy.deepcopy(dict(base_schema))
    compensated = frozenset(compensated_features)
    unique_item_paths = frozenset(compensated_unique_item_paths)
    if not provider_capability(provider)["features"]["unique_items"]:
        pending: list[tuple[str, object]] = [("", projected)]
        while pending:
            pointer, node = pending.pop()
            if isinstance(node, dict):
                if "uniqueItems" in node:
                    if (
                        "uniqueItems" not in compensated
                        or f"{pointer}/uniqueItems" not in unique_item_paths
                    ):
                        raise NativeProviderSchemaError(
                            "unsupported uniqueItems has no local compensation"
                        )
                    node.pop("uniqueItems")
                pending.extend(
                    (f"{pointer}/{key}", child)
                    for key, child in node.items()
                    if key not in {"const", "enum"}
                )
            elif isinstance(node, list):
                pending.extend((f"{pointer}/{index}", child) for index, child in enumerate(node))
    return projected


def lower_reviewer_writer_for_profile(schema: Mapping[str, Any], *, profile: str) -> dict[str, Any]:
    """Lower only locally checked constraints missing from the selected profile.

    Unreachable reader definitions are removed first.  A new unsupported
    feature therefore fails the provider guard instead of being silently lost.
    """
    projected = copy.deepcopy(dict(schema))
    features = provider_capability(profile)["features"]
    definitions = projected.get("$defs", {})
    if not isinstance(definitions, dict):
        raise NativeProviderSchemaError("reviewer writer definitions must be an object")
    reachable: set[str] = set()

    def references(value: Any) -> None:
        if isinstance(value, dict):
            ref = value.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                name = ref[len("#/$defs/"):].split("/", 1)[0]
                if name not in definitions:
                    raise NativeProviderSchemaError(f"unknown reviewer writer definition {name}")
                if name not in reachable:
                    reachable.add(name)
                    references(definitions[name])
            for key, child in value.items():
                if key not in {"const", "enum"}:
                    references(child)
        elif isinstance(value, list):
            for child in value:
                references(child)

    references({key: value for key, value in projected.items() if key != "$defs"})
    projected["$defs"] = {key: definitions[key] for key in definitions if key in reachable}

    def nullable(node: Any) -> bool:
        if not isinstance(node, dict):
            return False
        if node.get("type") == "null":
            return True
        return any(nullable(branch) for branch in node.get("oneOf", node.get("anyOf", [])))

    def lower(node: Any) -> None:
        if isinstance(node, dict):
            if "uniqueItems" in node and not features["unique_items"]:
                raise NativeProviderSchemaError(
                    "generated reviewer uniqueItems has no local compensation"
                )
            if not features["const_list"] and node.get("const") == [] and "const" in node:
                node.pop("const")
                node["minItems"] = node["maxItems"] = 0
            if not features["nested_one_of"] and "oneOf" in node:
                # Native review parsing still checks the exclusive union.
                node["anyOf"] = node.pop("oneOf")
            properties = node.get("properties")
            if not features["optional_properties"] and node.get("type") == "object" and isinstance(properties, dict):
                optional = set(properties) - set(node.get("required", []))
                if optional and not all(nullable(properties[key]) for key in optional):
                    raise NativeProviderSchemaError(
                        "reviewer writer has an optional non-nullable property without compensation"
                    )
                if node.get("additionalProperties") is not False:
                    raise NativeProviderSchemaError("reviewer writer object is not closed")
                node["required"] = sorted(properties)
            for key, child in node.items():
                if key not in {"const", "enum"}:
                    lower(child)
        elif isinstance(node, list):
            for child in node:
                lower(child)

    lower(projected)
    assert_projected_provider_schema(projected, provider=profile)
    return projected


def lower_reviewer_writer_for_openai(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Compatibility API for existing callers; behavior is profile driven."""
    return lower_reviewer_writer_for_profile(schema, profile=OPENAI_PROVIDER)


def bind_required_empty_array(
    schema: dict[str, Any], *, provider: str
) -> None:
    """Bind an exact empty array using only positively probed features."""
    features = provider_capability(provider)["features"]
    schema.pop("const", None)
    schema.pop("minItems", None)
    schema.pop("maxItems", None)
    if features["const_list"]:
        schema["const"] = []
        return
    if features["min_max_items"]:
        schema["minItems"] = 0
        schema["maxItems"] = 0
        return
    raise NativeProviderSchemaError(
        f"{provider} has no positively probed exact-empty-array expression"
    )


def assert_projected_provider_schema(
    projected_schema: Mapping[str, Any], *, provider: str
) -> None:
    """Fail closed when a final writer schema violates provider rules."""
    pattern_violations = schema_pattern_violations(projected_schema)
    if pattern_violations:
        first = pattern_violations[0]
        raise NativeProviderSchemaError(
            f"non-portable writer pattern at {first.path}: {first.rule}"
        )
    features = provider_capability(provider)["features"]
    strict = features["strict_writer"]
    openai_keywords = OPENAI_STRUCTURED_OUTPUT_CORE_KEYWORDS | frozenset(
        keyword
        for keyword, feature in SCHEMA_FEATURE_KEYWORDS.items()
        if features[feature]
    )
    violations: list[str] = []
    if strict:
        if projected_schema.get("type") != "object":
            violations.append("/type: root must have type object")
        if "anyOf" in projected_schema:
            violations.append("/anyOf: root must not use anyOf")

    pending: list[tuple[str, Mapping[str, Any]]] = [("", projected_schema)]
    while pending:
        pointer, node = pending.pop()
        enum = node.get("enum")
        if isinstance(enum, list) and not enum:
            violations.append(
                f"{_schema_pointer(pointer, 'enum')}: must not be empty"
            )
        declared_types = node.get("type")
        if isinstance(declared_types, list) and not declared_types:
            violations.append(
                f"{_schema_pointer(pointer, 'type')}: must not be empty"
            )
        for keyword in ("allOf", "anyOf", "oneOf"):
            branches = node.get(keyword)
            if isinstance(branches, list) and not branches:
                violations.append(
                    f"{_schema_pointer(pointer, keyword)}: must not be empty"
                )

        const_value = node.get("const")
        if isinstance(const_value, list) and not features["const_list"]:
            violations.append(
                f"{_schema_pointer(pointer, 'const')}: array-valued const requires "
                "the positively probed const_list feature"
            )
        items = node.get("items")
        if (
            isinstance(items, (bool, list))
            and not features["positional_tuple"]
        ):
            violations.append(
                f"{_schema_pointer(pointer, 'items')}: positional array schemas "
                "require the positively probed positional_tuple feature"
            )

        for keyword, feature in SCHEMA_FEATURE_KEYWORDS.items():
            if keyword in node and not features[feature]:
                if strict and keyword == "oneOf":
                    continue  # Preserve the historical strict-writer diagnostic below.
                violations.append(
                    f"{_schema_pointer(pointer, keyword)}: keyword {keyword!r} "
                    f"requires the positively probed {feature} feature"
                )
        if not features["null_type"] and (
            declared_types == "null" or isinstance(declared_types, list) and "null" in declared_types
        ):
            violations.append(f"{_schema_pointer(pointer, 'type')}: null requires the positively probed null_type feature")

        properties = node.get("properties")
        required = node.get("required")
        if isinstance(properties, Mapping) and isinstance(required, list):
            for name in required:
                property_schema = properties.get(name)
                if (
                    isinstance(name, str)
                    and isinstance(property_schema, Mapping)
                    and property_schema.get("maxItems") == 0
                    and features["const_list"]
                ):
                    violations.append(
                        f"{_schema_pointer(pointer, 'properties', name, 'maxItems')}: "
                        "required array property must not force an empty collection"
                    )

        if not strict:
            for keyword in sorted(set(node) - KNOWN_SCHEMA_KEYWORDS):
                violations.append(
                    f"{_schema_pointer(pointer, keyword)}: keyword {keyword!r} is not a supported schema keyword"
                )
        if strict:
            unsupported = sorted(set(node) - openai_keywords)
            for keyword in unsupported:
                keyword_path = _schema_pointer(pointer, keyword)
                rule = (
                    "is not permitted"
                    if keyword == "oneOf"
                    else "is not supported"
                    if keyword in OPENAI_UNSUPPORTED_SCHEMA_KEYWORDS
                    else "is not a supported schema keyword"
                )
                violations.append(f"{keyword_path}: keyword {keyword!r} {rule}")
            if "$ref" in node and set(node) != {"$ref"}:
                siblings = sorted(set(node) - {"$ref"})
                violations.append(
                    f"{pointer or '/'}: $ref must not have sibling keys {siblings!r}"
                )
            type_names = (
                declared_types
                if isinstance(declared_types, list)
                else [declared_types]
            )
            unsupported_types = sorted(
                repr(item)
                for item in type_names
                if item is not None
                and (
                    not isinstance(item, str)
                    or item not in OPENAI_STRUCTURED_OUTPUT_TYPES
                )
            )
            if unsupported_types:
                violations.append(
                    f"{_schema_pointer(pointer, 'type')}: unsupported JSON type(s) "
                    f"{unsupported_types!r}"
                )
            if node.get("type") == "object":
                if node.get("additionalProperties") is not False:
                    violations.append(
                        f"{_schema_pointer(pointer, 'additionalProperties')}: "
                        "object must set additionalProperties false"
                    )
                if isinstance(properties, dict) and (
                    not isinstance(required, list)
                    or set(required) != set(properties)
                ):
                    violations.append(
                        f"{_schema_pointer(pointer, 'required')}: every object "
                        "property must be required"
                    )
        for container in ("$defs", "properties"):
            children = node.get(container)
            if isinstance(children, Mapping):
                for name, child in children.items():
                    if isinstance(child, Mapping):
                        pending.append(
                            (_schema_pointer(pointer, container, str(name)), child)
                        )
        if isinstance(items, Mapping):
            pending.append((_schema_pointer(pointer, "items"), items))
        additional = node.get("additionalProperties")
        if isinstance(additional, Mapping):
            pending.append(
                (_schema_pointer(pointer, "additionalProperties"), additional)
            )
        for keyword in ("allOf", "anyOf", "oneOf"):
            branches = node.get(keyword)
            if isinstance(branches, list):
                for index, child in enumerate(branches):
                    if isinstance(child, Mapping):
                        pending.append(
                            (
                                _schema_pointer(pointer, keyword, str(index)),
                                child,
                            )
                        )
        for keyword in ("if", "then", "else", "not"):
            child = node.get(keyword)
            if isinstance(child, Mapping):
                pending.append((_schema_pointer(pointer, keyword), child))

    if violations:
        raise NativeProviderSchemaError(
            "projected schema violates provider acceptance rules: "
            + "; ".join(sorted(violations))
        )


def _schema_pointer(pointer: str, *parts: str) -> str:
    encoded = [part.replace("~", "~0").replace("/", "~1") for part in parts]
    return pointer + "".join(f"/{part}" for part in encoded)


def normalize_transport_profile(
    provider: str, command: Sequence[str]
) -> ProviderTransportProfile:
    if provider == "codex":
        return _normalize_codex(command)
    if provider == "claude":
        return _normalize_claude(command)
    if provider == AGY_PROVIDER:
        return _normalize_antigravity(command)
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
        "--prompt-suggestions": "false",
    }
    for flag, expected in expected_pairs.items():
        if _take_pair(values, flag) != expected:
            raise NativeProviderSchemaError(f"unexpected Claude {flag} value")
    for ignored in ("--add-dir", "--system-prompt"):
        if ignored in values:
            _discard_pair(values, ignored)
    if "--max-budget-usd" in values:
        # Cost policy is intentionally not a schema-capability input (R-25).
        _discard_pair(values, "--max-budget-usd")
    _discard_pair(values, "--json-schema")
    flags = {
        "-p",
        "--restricted",
        "--safe-mode",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--disable-slash-commands",
    }
    positional = [item for item in values if item not in flags]
    seen_flags = {item for item in values if item in flags}
    if seen_flags != flags or values.count("--restricted") != 1 or len(positional) != 1:
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
            "--restricted",
            "--safe-mode",
            "--strict-mcp-config",
            "--prompt-suggestions=false",
            "--no-session-persistence",
            "--disable-slash-commands",
        ),
    )


def _normalize_antigravity(command: Sequence[str]) -> ProviderTransportProfile:
    """Classify only arguments used in the measured Phase-0 configuration."""
    if not command:
        raise NativeProviderSchemaError("empty Antigravity command")
    if any(not isinstance(item, str) or "\x00" in item for item in command):
        raise NativeProviderSchemaError("Antigravity command contains invalid text")
    values = list(command[1:])
    prompt = _take_pair(values, "-p")
    schema = _take_pair(values, "--json-schema")
    timeout = _take_pair(values, "--print-timeout")
    log_path = _take_pair(values, "--log-file")
    model = _take_pair(values, "--model")
    effort = _take_pair(values, "--effort")
    output_format = _take_pair(values, "--output-format")
    agent = _take_pair(values, "--agent")
    if (not prompt or not schema or not log_path or not model or not effort
        or re.fullmatch(r"(?:0|[1-9][0-9]*)s", timeout) is None
        or output_format != "json" or agent != "dao-reviewer"
        or sorted(values) != sorted(("--disable-slash-commands", "--sandbox"))):
        raise NativeProviderSchemaError("unclassified Antigravity command arguments")
    return ProviderTransportProfile(
        provider=AGY_PROVIDER,
        binary_name=Path(command[0]).name,
        model=model,
        reasoning_or_effort=effort,
        schema_transport="json-schema-argument",
        semantic_flags=AGY_SEMANTIC_FLAGS,
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
