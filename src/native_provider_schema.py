"""Shared provider-schema projection, capability, and transport-profile rules."""

from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
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
PROVIDER_VERSION_POLICY = "forward"
OPENAI_PROVIDER = "co" + "dex"
CODEX_IMPLEMENTER_PROFILE = OPENAI_PROVIDER + "-implementer"  # allowlist:provider -- transport: D1 implementer isolation binding
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
# https://developers.openai.com/api/docs/guides/structured-outputs/#objects-have-limitations-on-nesting-depth-and-size
# The same guide documents the string and enum limits below.
OPENAI_MAX_OBJECT_PROPERTIES = 5000
OPENAI_MAX_OBJECT_DEPTH = 10
OPENAI_MAX_SCHEMA_STRING_LENGTH = 120000
OPENAI_MAX_ENUM_VALUES = 1000
OPENAI_LARGE_ENUM_THRESHOLD = 250
OPENAI_MAX_LARGE_ENUM_STRING_LENGTH = 15000
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
CODEX_REVIEW_DISABLED_FEATURES = (  # allowlist:provider -- profile configuration: reviewer CLI binding
    "apps", "plugins", "multi_agent", "goals", "browser_use", "computer_use",
    "image_generation", "hooks", "skill_search", "tool_suggest", "remote_plugin",
)
CODEX_REVIEW_SEMANTIC_FLAGS = (  # allowlist:provider -- profile configuration: reviewer CLI binding
    "exec", "--skip-git-repo-check", "--ephemeral", "--color=never", "--json",
    "--output-schema=<runtime-file>", "--output-last-message=<runtime-file>",
    "-C=<container>", "--ignore-user-config", "--ignore-rules",
    *(f"--disable={feature}" for feature in CODEX_REVIEW_DISABLED_FEATURES),  # allowlist:provider -- profile configuration: reviewer CLI binding
    "web_search=disabled", "project_doc_max_bytes=0",
    "shell_environment_policy.inherit=core", "permissions=dao-reviewer",
    "default_permissions=dao-reviewer", "stdin=-",
)
CODEX_IMPLEMENTER_SEMANTIC_FLAGS = (  # allowlist:provider -- profile configuration: implementer CLI binding
    "exec", "--skip-git-repo-check", "--ephemeral", "--color=never", "--json",
    "--output-schema=<runtime-file>", "--output-last-message=<runtime-file>",
    "-C=<repository>", "--ignore-user-config", "--ignore-rules",
    *(f"--disable={feature}" for feature in CODEX_REVIEW_DISABLED_FEATURES),  # allowlist:provider -- transport: D1 implementer isolation binding
    "web_search=disabled", "shell_environment_policy.inherit=core",
    "permissions=dao-implementer", "default_permissions=dao-implementer",
    "model_catalog_json=<bound-hardened-catalog>", "project_docs=enabled", "stdin=-",
)
CLAUDE_IMPLEMENTER_SEMANTIC_FLAGS = (  # allowlist:provider -- profile configuration: implementer CLI binding
    "-p", "--output-format=stream-json", "--model=<model>", "--effort=<effort>",
    "--no-session-persistence", "--disable-slash-commands", "--strict-mcp-config",
    "--restricted", "--safe-mode", "--prompt-suggestions=false",
    "--tools=Read,Edit,Write,Glob,Grep,Bash", "--permission-mode=acceptEdits",
    "--permission-prompts=none", "--disallowedTools=<protected-edit-rules>",
    "--settings=<canonical-protected-json>",
    "--json-schema=<schema>", "--system-prompt=<implementer-policy>",
    "--verbose", "--add-dir=<private-per-invocation>",
    "directive=<start-instruction>", "stdin=<bound-request>",
)
CLAUDE_IMPLEMENTER_START_DIRECTIVE = (  # allowlist:provider -- profile configuration: implementer CLI binding
    "The exact native implementer request JSON is supplied on standard input. "
    "Read it as data and return the single request-bound result under the writer schema."
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
        if version_policy not in {PROVIDER_VERSION_POLICY, "same-major-forward", "exact"}:
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
    return capability["version_policy"] == "forward" or actual[0] == baseline[0]


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
    if not features["optional_properties"]:
        projected = _expand_reviewer_object_overlays(projected)
    if features["strict_writer"]:
        _type_literal_schemas(projected)
    assert_projected_provider_schema(projected, provider=profile)
    if features["strict_writer"]:
        assert_openai_structured_output_limits(projected)
    return projected


def _schema_children(node: Mapping[str, Any]) -> list[Any]:
    """Subschemas of one schema node; property names are never schemas."""
    children: list[Any] = []
    for key in ("properties", "$defs"):
        mapping = node.get(key)
        if isinstance(mapping, Mapping):
            children.extend(mapping.values())
    if isinstance(node.get("items"), Mapping):
        children.append(node["items"])
    for key in ("anyOf", "oneOf", "allOf"):
        branches = node.get(key)
        if isinstance(branches, list):
            children.extend(branches)
    return children


def _literal_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if value is None:
        return "null"
    raise NativeProviderSchemaError("literal schema value has no strict writer type")


def _type_literal_schemas(node: Any) -> None:
    """Give const/enum-only schemas the type their literals already imply.

    Strict Structured Outputs requires a type on every schema. The inferred
    type admits nothing the literals did not admit, so no constraint is lost.
    """
    if not isinstance(node, dict):
        return
    if ("const" in node or "enum" in node) and not {"type", "$ref", "anyOf"} & set(node):
        values = [node["const"]] if "const" in node else node["enum"]
        if not isinstance(values, list) or not values:
            raise NativeProviderSchemaError("literal schema has no values")
        types = sorted({_literal_type(value) for value in values})
        node["type"] = types[0] if len(types) == 1 else types
    for child in _schema_children(node):
        _type_literal_schemas(child)


def _untyped_schema_pointers(node: Any, pointer: str = "") -> list[str]:
    if not isinstance(node, Mapping):
        return []
    found = [] if {"type", "$ref", "anyOf"} & set(node) else [pointer or "/"]
    for key in ("properties", "$defs"):
        mapping = node.get(key)
        if isinstance(mapping, Mapping):
            for name, child in mapping.items():
                found.extend(_untyped_schema_pointers(child, _schema_pointer(pointer, key, name)))
    if isinstance(node.get("items"), Mapping):
        found.extend(_untyped_schema_pointers(node["items"], _schema_pointer(pointer, "items")))
    for key in ("anyOf", "oneOf", "allOf"):
        branches = node.get(key)
        if isinstance(branches, list):
            for index, child in enumerate(branches):
                found.extend(_untyped_schema_pointers(child, _schema_pointer(pointer, key, str(index))))
    return found


def _intersect_overlay_property(parent: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any] | None:
    """Prove an overlay is a subset; None means its branch is impossible."""
    def same(left: Any, right: Any) -> bool:
        return json.dumps(left, sort_keys=True, separators=(",", ":")) == json.dumps(
            right, sort_keys=True, separators=(",", ":")
        )

    if same(parent, overlay):
        return copy.deepcopy(parent)
    if "$ref" in parent or "$ref" in overlay:
        if "anyOf" not in parent or not any(same(overlay, choice) for choice in parent["anyOf"]):
            raise NativeProviderSchemaError("reviewer overlay $ref is not a proven subset")
    if "anyOf" in parent:
        if not any(same(overlay, choice) for choice in parent["anyOf"]):
            raise NativeProviderSchemaError("reviewer overlay is not a selected anyOf branch")
        result = copy.deepcopy(parent)
        result["anyOf"] = [copy.deepcopy(overlay)]
        return result
    allowed = {"type", "const", "enum", "minItems", "maxItems"}
    if set(overlay) - allowed:
        raise NativeProviderSchemaError("reviewer overlay constraint is not provably narrowing")
    result = copy.deepcopy(parent)
    if "type" in overlay:
        parent_types = parent.get("type")
        if parent_types is not None and overlay["type"] not in (
            parent_types if isinstance(parent_types, list) else [parent_types]
        ):
            return None
        result["type"] = overlay["type"]
    if "const" in overlay:
        value = overlay["const"]
        if "const" in parent and not same(parent["const"], value):
            return None
        if "enum" in parent and not any(same(value, item) for item in parent["enum"]):
            return None
        result.pop("enum", None)
        result["const"] = value
    if "enum" in overlay:
        values = overlay["enum"]
        if "const" in parent:
            if not any(same(parent["const"], value) for value in values):
                return None
        elif "enum" in parent:
            if not all(any(same(value, item) for item in parent["enum"]) for value in values):
                raise NativeProviderSchemaError("reviewer overlay enum widens its parent")
            result["enum"] = values
        else:
            result["enum"] = values
    for lower, upper in (("minItems", "maxItems"),):
        if lower in overlay:
            result[lower] = max(parent.get(lower, overlay[lower]), overlay[lower])
        if upper in overlay:
            result[upper] = min(parent.get(upper, overlay[upper]), overlay[upper])
        if lower in result and upper in result and result[lower] > result[upper]:
            return None
    return result


def _expand_reviewer_object_overlays(node: Any) -> Any:
    if isinstance(node, list):
        return [_expand_reviewer_object_overlays(child) for child in node]
    if not isinstance(node, dict):
        return node
    properties = node.get("properties")
    branches = node.get("anyOf")
    if isinstance(properties, dict) and isinstance(branches, list) and any(
        isinstance(branch, dict) and "properties" in branch for branch in branches
    ):
        variants = []
        for branch in branches:
            if not isinstance(branch, dict) or set(branch) != {"properties"} or not isinstance(branch["properties"], dict):
                raise NativeProviderSchemaError("reviewer object overlay has unsupported shape")
            variant = copy.deepcopy({key: value for key, value in node.items() if key != "anyOf"})
            if variant.get("type") != "object" or variant.get("additionalProperties") is not False:
                raise NativeProviderSchemaError("reviewer object overlay parent is not closed")
            for name, restriction in branch["properties"].items():
                if name not in properties or not isinstance(restriction, dict):
                    raise NativeProviderSchemaError("reviewer object overlay property is unbound")
                selected = _intersect_overlay_property(properties[name], restriction)
                if selected is None:
                    break
                variant["properties"][name] = selected
            else:
                variant["required"] = sorted(variant["properties"])
                variants.append(_expand_reviewer_object_overlays(variant))
        if not variants:
            raise NativeProviderSchemaError("reviewer object overlay has no possible variant")
        return {"anyOf": variants}
    return {
        key: value if key in {"const", "enum"} else _expand_reviewer_object_overlays(value)
        for key, value in node.items()
    }


def assert_openai_structured_output_limits(schema: Mapping[str, Any]) -> None:
    """Check the published Structured Outputs limits on the final schema."""
    definitions = schema.get("$defs", {})
    property_count = enum_count = string_length = 0
    large_enums: list[int] = []
    def measure(node: Any) -> None:
        nonlocal property_count, enum_count, string_length
        if isinstance(node, dict):
            properties = node.get("properties", {})
            if isinstance(properties, dict):
                property_count += len(properties)
                string_length += sum(len(key) for key in properties)
            defs = node.get("$defs", {})
            if isinstance(defs, dict):
                string_length += sum(len(key) for key in defs)
            enum = node.get("enum")
            if isinstance(enum, list):
                enum_count += len(enum)
                if len(enum) > OPENAI_LARGE_ENUM_THRESHOLD:
                    large_enums.append(sum(len(value) for value in enum if isinstance(value, str)))
                string_length += sum(len(value) for value in enum if isinstance(value, str))
            if isinstance(node.get("const"), str):
                string_length += len(node["const"])
            for key, value in node.items():
                if key not in {"const", "enum"}:
                    measure(value)
        elif isinstance(node, list):
            for child in node:
                measure(child)

    def depth(node: Any, level: int, active: frozenset[str]) -> int:
        if isinstance(node, list):
            return max((depth(child, level, active) for child in node), default=level)
        if not isinstance(node, dict):
            return level
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            name = ref[len("#/$defs/"):]
            if name in active or name not in definitions:
                raise NativeProviderSchemaError("reviewer schema has recursive or unknown definition")
            return depth(definitions[name], level, active | {name})
        current = level + (1 if node.get("type") == "object" else 0)
        return max((depth(value, current, active) for key, value in node.items()
                    if key not in {"$defs", "const", "enum"}), default=current)

    measure(schema)
    maximum_depth = depth(schema, 0, frozenset())
    if (property_count > OPENAI_MAX_OBJECT_PROPERTIES or maximum_depth > OPENAI_MAX_OBJECT_DEPTH
            or enum_count > OPENAI_MAX_ENUM_VALUES or string_length > OPENAI_MAX_SCHEMA_STRING_LENGTH
            or any(length > OPENAI_MAX_LARGE_ENUM_STRING_LENGTH for length in large_enums)):
        raise NativeProviderSchemaError(
            "reviewer schema exceeds OpenAI Structured Outputs limits: "
            f"properties={property_count}, depth={maximum_depth}, enum_values={enum_count}, "
            f"string_length={string_length}, large_enum_lengths={large_enums}"
        )


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
        violations.extend(
            f"{pointer}: strict writer schema must declare a type"
            for pointer in _untyped_schema_pointers(projected_schema)
        )

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
            if node.get("type") == "object" or isinstance(properties, Mapping):
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
    provider: str, command: Sequence[str], *, bound_package_root: Path | None = None,
    bound_container: Path | None = None, bound_runtime_dir: Path | None = None,
    bound_settings_json: str | None = None,
    bound_repository_root: Path | None = None,
    bound_toolchain_read_roots: tuple[str, ...] = (),
    bound_scratch: Path | None = None,
    bound_environment: Mapping[str, str] | None = None,
    bound_model_row_sha256: str | None = None,
    bound_protected_paths: tuple[Path, ...] | None = None,
) -> ProviderTransportProfile:
    if provider == "codex-implementer":  # allowlist:provider -- profile configuration: implementer transport
        return _normalize_codex_implementer(command, bound_package_root, bound_repository_root,  # allowlist:provider -- transport: D1 implementer isolation binding
            bound_container, bound_runtime_dir, bound_scratch, bound_protected_paths,
            bound_toolchain_read_roots, bound_environment, bound_model_row_sha256)
    if provider == "claude-implementer":  # allowlist:provider -- profile configuration: implementer transport
        return _normalize_claude_implementer(command, bound_settings_json, bound_repository_root, bound_toolchain_read_roots, bound_scratch, bound_environment, bound_protected_paths)  # allowlist:provider -- profile configuration: implementer transport
    if provider == "codex-reviewer":  # allowlist:provider -- profile configuration: reviewer transport
        if bound_package_root is None or bound_container is None or bound_runtime_dir is None:
            raise NativeProviderSchemaError("reviewer package, container or runtime identity is unbound")
        return _normalize_codex_reviewer(command, bound_package_root, bound_container, bound_runtime_dir, bound_model_row_sha256)  # allowlist:provider -- profile configuration: reviewer transport
    if provider == "codex":
        return _normalize_codex(command)
    if provider == "claude":
        return _normalize_claude(command)
    if provider == AGY_PROVIDER:
        return _normalize_antigravity(command)
    raise NativeProviderSchemaError(f"unsupported transport profile provider {provider}")


_CLAUDE_IMPLEMENTER_DENIED_ENV_VARS = (  # allowlist:provider -- profile configuration: implementer credentials
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",  # allowlist:provider -- profile configuration: implementer credentials
)


def _validate_claude_implementer_settings(settings: object, repository_root: Path | None = None, tool_roots: tuple[str, ...] = (), scratch: Path | None = None) -> tuple[str, ...]:  # allowlist:provider -- profile configuration: implementer settings
    """Check every protective field instead of trusting the generator."""
    if not isinstance(settings, dict) or set(settings) != {"disableAllHooks", "permissions", "sandbox"}:
        raise NativeProviderSchemaError("Claude implementer settings keys differ")  # allowlist:provider -- profile configuration: implementer settings
    permissions = settings["permissions"]
    sandbox = settings["sandbox"]
    if (settings["disableAllHooks"] is not True or not isinstance(permissions, dict)
        or set(permissions) != {"deny", "blockReadsOutsideWorkingDirectories"}
        or permissions["blockReadsOutsideWorkingDirectories"] is not True):
        raise NativeProviderSchemaError("Claude implementer permission settings differ")  # allowlist:provider -- profile configuration: implementer settings
    deny = permissions["deny"]
    if not isinstance(deny, list) or not deny or len(deny) % 2 or len(set(deny)) != len(deny):
        raise NativeProviderSchemaError("Claude implementer deny rules differ")  # allowlist:provider -- profile configuration: implementer settings
    paths: list[str] = []
    for exact, recursive in zip(deny[::2], deny[1::2]):
        match = re.fullmatch(r"Edit\(([^()\s,]+)\)", exact) if isinstance(exact, str) else None
        if match is None or recursive != f"Edit({match.group(1)}/**)":
            raise NativeProviderSchemaError("Claude implementer deny rule differs")  # allowlist:provider -- profile configuration: implementer settings
        paths.append(match.group(1))
    if not {"./.git", "./.orchestrator"} <= set(paths):
        raise NativeProviderSchemaError("Claude implementer deny rules omit a control path")  # allowlist:provider -- profile configuration: implementer settings
    # A nested denial below a read-only parent stops the sandbox from starting.
    if any(inner != outer and PurePosixPath(inner).is_relative_to(PurePosixPath(outer))
           for inner in paths for outer in paths):
        raise NativeProviderSchemaError("Claude implementer deny rules are nested")  # allowlist:provider -- profile configuration: implementer settings
    expected_sandbox = {
        "enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False,
        "autoAllowBashIfSandboxed": True, "excludedCommands": [],
        "network": {"allowedDomains": [], "strictAllowlist": True},
        "credentials": {"envVars": [
            {"name": name, "mode": "deny"} for name in _CLAUDE_IMPLEMENTER_DENIED_ENV_VARS  # allowlist:provider -- profile configuration: implementer credentials
        ]},
    }
    if (not isinstance(sandbox, dict) or set(sandbox) != {*expected_sandbox, "filesystem"}
        or any(sandbox[key] != value for key, value in expected_sandbox.items())):
        raise NativeProviderSchemaError("Claude implementer sandbox settings differ")  # allowlist:provider -- profile configuration: implementer settings
    filesystem = sandbox["filesystem"]
    deny_write = filesystem.get("denyWrite") if isinstance(filesystem, dict) else None
    if (not isinstance(filesystem, dict) or set(filesystem) not in ({"denyWrite", "allowWrite"}, {"denyWrite", "allowWrite", "allowRead"})
        or not isinstance(deny_write, list) or len(deny_write) != len(paths)
        or any(not isinstance(item, str) or not Path(item).is_absolute() for item in deny_write)):
        raise NativeProviderSchemaError("Claude implementer sandbox write denials differ")  # allowlist:provider -- profile configuration: implementer settings
    from toolchain_paths import validate_private_scratch
    if scratch is None or repository_root is None or filesystem.get("allowWrite") != [str(scratch)]:
        raise NativeProviderSchemaError("private scratch write binding differs")
    try:
        validate_private_scratch(scratch, repository_root, tuple(Path(p) for p in deny_write), tool_roots)
    except ValueError as exc:
        raise NativeProviderSchemaError(str(exc)) from exc
    if "allowRead" in filesystem or tool_roots:
        from toolchain_paths import validate_toolchain_read_roots
        if repository_root is None:
            raise NativeProviderSchemaError("toolchain read roots require a bound repository")
        try:
            actual = validate_toolchain_read_roots(filesystem.get("allowRead"), repository_root, tuple(Path(p) for p in deny_write))
        except ValueError as exc:
            raise NativeProviderSchemaError(str(exc)) from exc
        if actual != tool_roots or list(actual) != filesystem["allowRead"]:
            raise NativeProviderSchemaError("toolchain read roots differ from their binding")
    return tuple(deny)


def _normalize_claude_implementer(  # allowlist:provider -- profile configuration: implementer transport
    command: Sequence[str], bound_settings_json: str | None,
    repository_root: Path | None = None, tool_roots: tuple[str, ...] = (), scratch: Path | None = None,
    environment: Mapping[str, str] | None = None,
    protected_paths: tuple[Path, ...] | None = None,
) -> ProviderTransportProfile:
    from prompts import NATIVE_IMPLEMENTER_SYSTEM_POLICY

    if len(command) != 33 or Path(command[0]).name != "claude":  # allowlist:provider -- profile configuration: implementer CLI grammar
        raise NativeProviderSchemaError("Claude implementer command length or binary differs")  # allowlist:provider -- profile configuration: implementer CLI grammar
    values = list(command[1:])
    fixed = {
        0: "-p", 1: "--output-format", 2: "stream-json", 3: "--model",
        5: "--effort", 7: "--no-session-persistence", 8: "--disable-slash-commands",
        9: "--strict-mcp-config", 10: "--restricted", 11: "--safe-mode",
        12: "--prompt-suggestions", 13: "false", 14: "--tools",
        15: "Read,Edit,Write,Glob,Grep,Bash", 16: "--permission-mode",
        17: "acceptEdits", 18: "--permission-prompts", 19: "none",
        20: "--disallowedTools", 22: "--settings", 24: "--json-schema",
        26: "--system-prompt", 27: NATIVE_IMPLEMENTER_SYSTEM_POLICY,
        28: "--verbose", 29: "--add-dir",
    }
    if any(values[index] != expected for index, expected in fixed.items()):
        raise NativeProviderSchemaError("Claude implementer command grammar differs")  # allowlist:provider -- profile configuration: implementer CLI grammar
    if (not values[4] or not values[6] or not values[25]
        or bound_settings_json is None or values[23] != bound_settings_json
        or scratch is None or values[30] != str(scratch)):
        raise NativeProviderSchemaError("Claude implementer bound values differ")  # allowlist:provider -- profile configuration: implementer CLI grammar
    try:
        settings = json.loads(values[23])
        schema = json.loads(values[25])
    except (ValueError, TypeError) as exc:
        raise NativeProviderSchemaError("Claude implementer JSON arguments are invalid") from exc  # allowlist:provider -- profile configuration: implementer CLI grammar
    deny = _validate_claude_implementer_settings(settings, repository_root, tool_roots, scratch)  # allowlist:provider -- profile configuration: implementer settings
    from claude_implementer_adapter import implementer_settings  # allowlist:provider -- profile configuration: exact bound permissions
    if protected_paths is None or repository_root is None:
        raise NativeProviderSchemaError("implementer protection paths are unbound")
    expected_settings = implementer_settings(protected_paths, repository_root, tool_roots, scratch=scratch)
    if settings != expected_settings:
        raise NativeProviderSchemaError("implementer protection paths differ from bound paths")
    if values[21] != ",".join(deny):
        raise NativeProviderSchemaError("Claude implementer CLI deny rules differ from settings")  # allowlist:provider -- profile configuration: implementer CLI grammar
    if (canonical_schema_json(settings) != values[23] or not isinstance(schema, dict)
        or values[31] != CLAUDE_IMPLEMENTER_START_DIRECTIVE):  # allowlist:provider -- profile configuration: implementer CLI grammar
        raise NativeProviderSchemaError("Claude implementer JSON arguments differ")  # allowlist:provider -- profile configuration: implementer CLI grammar
    if environment is None or environment.get("CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK") != "1":  # allowlist:provider -- profile configuration: fixed environment or sandbox placeholder
        raise NativeProviderSchemaError("implementer refusal fallback environment differs")
    return ProviderTransportProfile(
        provider="claude", binary_name="claude", model=values[4],  # allowlist:provider -- profile configuration: implementer transport
        reasoning_or_effort=values[6], schema_transport="json-schema-argument",
        semantic_flags=CLAUDE_IMPLEMENTER_SEMANTIC_FLAGS,  # allowlist:provider -- profile configuration: implementer transport
    )


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


def codex_review_permission_config(package_root: Path) -> str:  # allowlist:provider -- profile configuration: reviewer CLI binding
    root = str(package_root)
    if not package_root.is_absolute() or any(ord(char) < 32 or char in {'"', '\\'} for char in root):
        raise NativeProviderSchemaError("unsafe Codex package root")  # allowlist:provider -- profile configuration: reviewer CLI binding
    return (
        'permissions.dao-reviewer={filesystem={":minimal"="read",'
        f'"{root}"="read"' + ',":workspace_roots"={"."="read"}}}'
    )


def codex_implementer_permission_config(package_root: Path, repository: Path, scratch: Path,  # allowlist:provider -- transport: D1 implementer isolation binding
    protected: tuple[Path, ...], tool_roots: tuple[str, ...], *, execution_root: Path,
) -> str:
    from toolchain_paths import validate_private_scratch, validate_toolchain_read_roots
    from claude_implementer_adapter import outermost_protected_paths  # allowlist:provider -- transport: D1 implementer isolation binding
    validate_codex_review_package_root(package_root)  # allowlist:provider -- transport: D1 implementer isolation binding
    if not protected or any(not path.is_absolute() for path in protected):
        raise NativeProviderSchemaError("implementer protection paths are unbound")
    if any(any(char in str(path) for char in "*?[]{}")
           for path in (package_root, repository, execution_root, scratch, *protected, *tool_roots)):
        raise NativeProviderSchemaError("implementer permission paths must not contain glob syntax")
    validate_private_scratch(scratch, repository, protected, tool_roots)
    if validate_toolchain_read_roots(tool_roots, repository, protected) != tool_roots:
        raise NativeProviderSchemaError("implementer toolchain roots changed")
    # Explicit absolute root: project workspace additions cannot grant writes.
    # Request narrower read entries; the host guard checks the effective boundary.
    fs = {":minimal": "read", str(package_root): "read", str(scratch): "write",
          str(execution_root): {".": "write" if execution_root == repository else "read"}}
    for path in outermost_protected_paths(protected):
        if path.is_relative_to(execution_root):
            fs[str(execution_root)][path.relative_to(execution_root).as_posix()] = "read"
        else:
            fs[str(path)] = "read"
    fs.update({root: "read" for root in tool_roots})
    def toml_table(table):
        return "{" + ",".join(json.dumps(key) + "=" + (toml_table(value) if isinstance(value, dict)
                              else json.dumps(value)) for key, value in table.items()) + "}"
    return "permissions.dao-implementer=" + toml_table({"filesystem": fs, "network": {"enabled": False}})


def codex_implementer_command(binary: str, model: str, effort: str, package: Path,  # allowlist:provider -- transport: D1 implementer isolation binding
    repository: Path, execution: Path, runtime: Path, scratch: Path,
    protected: tuple[Path, ...], tool_roots: tuple[str, ...],
) -> tuple[str, ...]:
    return (
        binary, "exec", "--model", model, "--config", f'model_reasoning_effort="{effort}"',
        "--skip-git-repo-check", "--ephemeral", "--color", "never", "--json",
        "--output-schema", str(runtime / "response-schema.json"),
        "--output-last-message", str(runtime / "last-message.json"),
        "-C", str(execution), "--ignore-user-config", "--ignore-rules",
        *(item for feature in CODEX_REVIEW_DISABLED_FEATURES for item in ("--disable", feature)),  # allowlist:provider -- transport: D1 implementer isolation binding
        "-c", 'web_search="disabled"', "-c", 'shell_environment_policy.inherit="core"',
        "-c", codex_implementer_permission_config(package, repository, scratch, protected, tool_roots, execution_root=execution),  # allowlist:provider -- transport: D1 implementer isolation binding
        "-c", 'default_permissions="dao-implementer"',
        "-c", "model_catalog_json=" + json.dumps(str(runtime / "model-catalog.json")), "-",
    )


def _normalize_codex_implementer(command, package, repository, execution, runtime, scratch,  # allowlist:provider -- transport: D1 implementer isolation binding
    protected, tool_roots, environment, row_digest,
) -> ProviderTransportProfile:
    from agent_config import REVIEWER_ENVIRONMENT_POLICY
    from model_catalog import reviewer_model_row_sha256
    if any(value is None for value in (package, repository, execution, runtime, scratch, protected, environment, row_digest)):
        raise NativeProviderSchemaError("implementer isolation identity is unbound")
    if len(command) < 6 or not command[3] or not re.fullmatch(r'model_reasoning_effort="[a-z]+"', command[5]):
        raise NativeProviderSchemaError("implementer model or effort differs")
    model, effort = command[3], command[5][len('model_reasoning_effort="'):-1]
    try:
        expected = codex_implementer_command(command[0], model, effort, package, repository,  # allowlist:provider -- transport: D1 implementer isolation binding
                                            execution, runtime, scratch, protected, tool_roots)
        catalog_path = runtime / "model-catalog.json"
        if catalog_path.is_symlink() or reviewer_model_row_sha256(catalog_path.read_text(encoding="utf-8"), model) != row_digest:
            raise ValueError("bound catalog changed")
    except (OSError, TypeError, ValueError) as exc:
        raise NativeProviderSchemaError("implementer isolation binding differs: " + str(exc)) from exc
    if tuple(command) != expected:
        raise NativeProviderSchemaError("Codex implementer command differs from bound grammar")  # allowlist:provider -- profile configuration: implementer grammar
    if environment.get("TMPDIR") != str(scratch) or any(name not in REVIEWER_ENVIRONMENT_POLICY and not name.startswith("LC_") for name in environment):
        raise NativeProviderSchemaError("implementer process environment differs from allowlist")
    return ProviderTransportProfile(provider="codex", binary_name="codex", model=model,  # allowlist:provider -- profile configuration: implementer transport
        reasoning_or_effort=effort, schema_transport="output-schema-file", semantic_flags=CODEX_IMPLEMENTER_SEMANTIC_FLAGS)  # allowlist:provider -- transport: D1 implementer isolation binding


def validate_codex_review_package_root(package_root: Path) -> None:  # allowlist:provider -- profile configuration: reviewer package boundary
    if (not package_root.is_absolute() or package_root.parts[-3:] != ("node_modules", "@openai", "codex")  # allowlist:provider -- profile configuration: npm package root
        or not package_root.is_dir() or package_root.resolve() != package_root
        or not (package_root / "bin" / "codex.js").is_file()):  # allowlist:provider -- profile configuration: npm package entry
        raise NativeProviderSchemaError("Codex reviewer package root differs")  # allowlist:provider -- profile configuration: reviewer package boundary
    candidates = (
        *package_root.glob("node_modules/@openai/codex-*/vendor/*/bin/codex"),  # allowlist:provider -- profile configuration: npm native binary
        *package_root.glob("vendor/*/bin/codex"),  # allowlist:provider -- profile configuration: legacy native binary
    )
    if len(candidates) != 1:
        raise NativeProviderSchemaError("Codex reviewer package has no unique native binary")  # allowlist:provider -- profile configuration: reviewer package boundary
    try:
        binary = candidates[0]
        target = binary.resolve(strict=True)
        metadata = binary.stat()
        within_root = all(
            part.resolve(strict=True).is_relative_to(package_root)
            for part in (binary, *binary.parents)
            if part != package_root and part.is_relative_to(package_root)
        )
    except (OSError, RuntimeError) as exc:
        raise NativeProviderSchemaError("Codex reviewer native binary is missing") from exc  # allowlist:provider -- profile configuration: reviewer package boundary
    if (not within_root or not target.is_relative_to(package_root) or not stat.S_ISREG(metadata.st_mode)
        or not metadata.st_mode & 0o111 or not os.access(binary, os.X_OK)):
        raise NativeProviderSchemaError("Codex reviewer native binary is unsafe")  # allowlist:provider -- profile configuration: reviewer package boundary


def _normalize_codex_reviewer(  # allowlist:provider -- profile configuration: reviewer CLI binding
    command: Sequence[str], bound_package_root: Path,
    bound_container: Path, bound_runtime_dir: Path,
    bound_model_row_sha256: str | None,
) -> ProviderTransportProfile:
    if not command or Path(command[0]).name != "codex":  # allowlist:provider -- profile configuration: reviewer CLI binding
        raise NativeProviderSchemaError("Codex reviewer binary differs")  # allowlist:provider -- profile configuration: reviewer CLI binding
    values = list(command[1:])
    if any(flag.startswith(("--sandbox", "--dangerously-", "--add-dir")) or flag == "--full-auto" for flag in values):
        raise NativeProviderSchemaError("Codex reviewer command broadens permissions")  # allowlist:provider -- profile configuration: reviewer CLI binding
    # Exact ordered grammar rejects duplicate flags, unknown settings and wider roots.
    if len(values) != 53:
        raise NativeProviderSchemaError("Codex reviewer command length differs")  # allowlist:provider -- profile configuration: reviewer CLI binding
    expected_fixed = [
        "exec", "--model", values[2], "--config", values[4],
        "--skip-git-repo-check", "--ephemeral", "--color", "never", "--json",
        "--output-schema", values[11], "--output-last-message", values[13],
        "-C", values[15], "--ignore-user-config", "--ignore-rules",
    ]
    if values[:18] != expected_fixed or not values[2] or not re.fullmatch(r'model_reasoning_effort="[a-z]+"', values[4]):
        raise NativeProviderSchemaError("Codex reviewer core flags differ")  # allowlist:provider -- profile configuration: reviewer CLI binding
    for path in (values[11], values[13], values[15]):
        if not Path(path).is_absolute():
            raise NativeProviderSchemaError("Codex reviewer paths must be absolute")  # allowlist:provider -- profile configuration: reviewer CLI binding
    container = Path(values[15])
    if not container.is_dir() or any(Path(path).is_relative_to(container) for path in (values[11], values[13])):
        raise NativeProviderSchemaError("Codex reviewer runtime files must be outside container")  # allowlist:provider -- profile configuration: reviewer CLI binding
    if (container != bound_container or Path(values[11]) != bound_runtime_dir / "response-schema.json"
        or Path(values[13]) != bound_runtime_dir / "last-message.json"):
        raise NativeProviderSchemaError("reviewer container or runtime path differs from bound workspace")
    disabled = [item for feature in CODEX_REVIEW_DISABLED_FEATURES for item in ("--disable", feature)]  # allowlist:provider -- profile configuration: reviewer CLI binding
    if values[18:40] != disabled:
        raise NativeProviderSchemaError("Codex reviewer disabled features differ")  # allowlist:provider -- profile configuration: reviewer CLI binding
    if values[40:52:2] != ["-c"] * 6 or values[52] != "-":
        raise NativeProviderSchemaError("Codex reviewer config flags differ")  # allowlist:provider -- profile configuration: reviewer CLI binding
    config = values[41:52:2]
    expected_catalog = bound_runtime_dir / "model-catalog.json"
    if config[5] != "model_catalog_json=" + json.dumps(str(expected_catalog)) or expected_catalog.is_symlink():
        raise NativeProviderSchemaError("reviewer model catalog path differs from runtime binding")
    from model_catalog import reviewer_model_row_sha256
    try:
        catalog_text = expected_catalog.read_text(encoding="utf-8")
        if reviewer_model_row_sha256(catalog_text, values[2]) != bound_model_row_sha256:
            raise ValueError("reviewer model entry changed or digest is unbound")
    except (OSError, TypeError, ValueError) as exc:
        raise NativeProviderSchemaError("reviewer model catalog is missing or unsafe") from exc
    if config[:3] != [
        'web_search="disabled"', "project_doc_max_bytes=0",
        'shell_environment_policy.inherit="core"',
    ] or config[4] != 'default_permissions="dao-reviewer"':
        raise NativeProviderSchemaError("Codex reviewer hardening differs")  # allowlist:provider -- profile configuration: reviewer CLI binding
    match = re.fullmatch(
        r'permissions\.dao-reviewer=\{filesystem=\{":minimal"="read","([^"\r\n]+)"="read",":workspace_roots"=\{"\."="read"\}\}\}',
        config[3],
    )
    if match is None:
        raise NativeProviderSchemaError("Codex reviewer filesystem profile differs")  # allowlist:provider -- profile configuration: reviewer CLI binding
    package_root = Path(match.group(1))
    validate_codex_review_package_root(package_root)  # allowlist:provider -- profile configuration: reviewer package boundary
    if codex_review_permission_config(package_root) != config[3]:  # allowlist:provider -- profile configuration: canonical permission grammar
        raise NativeProviderSchemaError("Codex reviewer package path is unsafe")  # allowlist:provider -- profile configuration: canonical permission grammar
    if package_root != bound_package_root:
        raise NativeProviderSchemaError("reviewer package root differs from bound identity")
    return ProviderTransportProfile(
        provider="codex", binary_name="codex", model=values[2],  # allowlist:provider -- profile configuration: reviewer CLI binding
        reasoning_or_effort=values[4].removeprefix('model_reasoning_effort="').removesuffix('"'),
        schema_transport="output-schema-file",
        semantic_flags=CODEX_REVIEW_SEMANTIC_FLAGS,  # allowlist:provider -- profile configuration: reviewer CLI binding
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
