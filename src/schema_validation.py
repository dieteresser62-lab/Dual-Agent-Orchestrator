"""Public offline validator for the repository's JSON Schema subset.

The project intentionally has no runtime JSON-Schema dependency.  This module
implements only the Draft 2020-12 keywords used by bundled schemas and rejects
unknown keywords during schema self-checking.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import re
from typing import Any, Mapping


class SchemaDefinitionError(ValueError):
    """A bundled schema uses an unsupported or invalid definition."""


@dataclass(frozen=True, slots=True)
class SchemaMismatch(Exception):
    """A JSON value does not conform to a checked schema."""

    path: tuple[str | int, ...]
    message: str


_SCHEMA_ANNOTATIONS = {"$schema", "$id", "title", "description"}
_SCHEMA_KEYWORDS = {
    "$ref", "$defs", "type", "enum", "const", "pattern", "format",
    "minLength", "maxLength", "minimum", "required", "properties",
    "additionalProperties", "items", "minItems", "maxItems", "uniqueItems",
    "allOf", "anyOf", "oneOf", "if", "then", "else",
}


def check_schema(schema: Mapping[str, Any], *, location: str = "<schema>") -> None:
    """Fail when a schema uses anything outside the supported closed subset."""
    _check_schema_node(schema, schema, location)


def validate_schema_document(value: Any, schema: Mapping[str, Any]) -> None:
    """Validate a JSON-compatible value against a previously checked schema."""
    _validate_schema_node(value, schema, schema, ())


def _check_schema_node(node: Any, root: Mapping[str, Any], location: str) -> None:
    if not isinstance(node, dict):
        raise SchemaDefinitionError(
            f"schema definition at {location} must be an object"
        )
    unknown = set(node) - _SCHEMA_ANNOTATIONS - _SCHEMA_KEYWORDS
    if unknown:
        raise SchemaDefinitionError(
            f"unsupported schema keyword at {location}: {sorted(unknown)[0]}"
        )
    reference = node.get("$ref")
    if reference is not None:
        if not isinstance(reference, str) or not reference.startswith("#/"):
            raise SchemaDefinitionError(
                f"schema reference at {location} must be local"
            )
        _resolve_schema_reference(root, reference)
    definitions = node.get("$defs", {})
    if not isinstance(definitions, dict):
        raise SchemaDefinitionError(f"$defs at {location} must be an object")
    for name, child in definitions.items():
        _check_schema_node(child, root, f"{location}.$defs.{name}")
    properties = node.get("properties", {})
    if not isinstance(properties, dict):
        raise SchemaDefinitionError(
            f"properties at {location} must be an object"
        )
    for name, child in properties.items():
        _check_schema_node(child, root, f"{location}.properties.{name}")
    items = node.get("items")
    if items is not None:
        _check_schema_node(items, root, f"{location}.items")
    additional = node.get("additionalProperties")
    if additional is not None and not isinstance(additional, bool):
        _check_schema_node(additional, root, f"{location}.additionalProperties")
    for keyword in ("allOf", "anyOf", "oneOf"):
        branches = node.get(keyword, [])
        if not isinstance(branches, list):
            raise SchemaDefinitionError(
                f"{keyword} at {location} must be an array"
            )
        for index, child in enumerate(branches):
            _check_schema_node(child, root, f"{location}.{keyword}[{index}]")
    for keyword in ("if", "then", "else"):
        child = node.get(keyword)
        if child is not None:
            _check_schema_node(child, root, f"{location}.{keyword}")


def _resolve_schema_reference(
    root: Mapping[str, Any], reference: str
) -> Mapping[str, Any]:
    current: Any = root
    for encoded_part in reference[2:].split("/"):
        part = encoded_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            raise SchemaDefinitionError(
                f"unresolved local schema reference: {reference}"
            )
        current = current[part]
    if not isinstance(current, dict):
        raise SchemaDefinitionError(
            f"schema reference does not target an object: {reference}"
        )
    return current


def _validate_schema_node(
    value: Any,
    schema: Mapping[str, Any],
    root: Mapping[str, Any],
    path: tuple[str | int, ...],
) -> None:
    reference = schema.get("$ref")
    if reference is not None:
        _validate_schema_node(
            value, _resolve_schema_reference(root, reference), root, path
        )

    if "const" in schema and not _json_equal(value, schema["const"]):
        raise SchemaMismatch(path, f"must equal {schema['const']!r}")
    if "enum" in schema and not any(
        _json_equal(value, item) for item in schema["enum"]
    ):
        raise SchemaMismatch(path, f"must be one of {schema['enum']!r}")

    expected = schema.get("type")
    if expected is not None:
        expected_types = expected if isinstance(expected, list) else [expected]
        if not any(_matches_json_type(value, name) for name in expected_types):
            raise SchemaMismatch(
                path, f"must have JSON type {' or '.join(expected_types)}"
            )

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise SchemaMismatch(path, "must not be empty")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise SchemaMismatch(
                path, f"must contain at most {schema['maxLength']} character(s)"
            )
        pattern = schema.get("pattern")
        if pattern is not None and re.search(pattern, value) is None:
            raise SchemaMismatch(path, f"does not match pattern {pattern!r}")
        if schema.get("format") == "date-time":
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                raise SchemaMismatch(path, "timestamp must be an ISO-8601 timestamp") from None
            if parsed.tzinfo is None:
                raise SchemaMismatch(path, "timestamp must include a timezone")

    if isinstance(value, int) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise SchemaMismatch(path, f"must be at least {schema['minimum']}")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise SchemaMismatch(
                path, f"must contain at least {schema['minItems']} item(s)"
            )
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise SchemaMismatch(
                path, f"must contain at most {schema['maxItems']} item(s)"
            )
        if schema.get("uniqueItems"):
            encoded = [_canonical_json(item) for item in value]
            if len(encoded) != len(set(encoded)):
                raise SchemaMismatch(path, "must contain unique items")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(value):
                _validate_schema_node(item, item_schema, root, (*path, index))

    if isinstance(value, dict):
        required = schema.get("required", [])
        for name in required:
            if name not in value:
                raise SchemaMismatch((*path, name), "is required")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extras = set(value) - set(properties)
            if extras:
                name = sorted(extras)[0]
                raise SchemaMismatch((*path, name), "is not an allowed property")
        additional = schema.get("additionalProperties")
        for name, child in value.items():
            if name in properties:
                _validate_schema_node(child, properties[name], root, (*path, name))
            elif isinstance(additional, dict):
                _validate_schema_node(child, additional, root, (*path, name))

    for branch in schema.get("allOf", []):
        _validate_schema_node(value, branch, root, path)
    if "anyOf" in schema and not any(
        _schema_branch_matches(value, branch, root, path)
        for branch in schema["anyOf"]
    ):
        raise SchemaMismatch(path, "does not match any allowed schema")
    if "oneOf" in schema:
        matches = sum(
            _schema_branch_matches(value, branch, root, path)
            for branch in schema["oneOf"]
        )
        if matches != 1:
            raise SchemaMismatch(path, "must match exactly one allowed schema")
    condition = schema.get("if")
    if condition is not None:
        keyword = (
            "then"
            if _schema_branch_matches(value, condition, root, path)
            else "else"
        )
        if keyword in schema:
            _validate_schema_node(value, schema[keyword], root, path)


def _schema_branch_matches(
    value: Any,
    schema: Mapping[str, Any],
    root: Mapping[str, Any],
    path: tuple[str | int, ...],
) -> bool:
    try:
        _validate_schema_node(value, schema, root, path)
    except SchemaMismatch:
        return False
    return True


def _matches_json_type(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, False)


def _json_equal(left: Any, right: Any) -> bool:
    return type(left) is type(right) and left == right


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
