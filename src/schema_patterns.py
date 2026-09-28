"""Audit the portable, deliberately small grammar for schema ``pattern`` values.

This module is an inventory tool until the v3 schema cutover.  Runtime schema
loading continues to use the existing v2 rules in Slice 9a.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


_HEX = frozenset("0123456789abcdefABCDEF")
_ESCAPABLE = frozenset(r"\.^$|?*+()[]{}-/")
_BOUNDED_QUANTIFIER = re.compile(r"\{([0-9]+)(?:,([0-9]*))?\}")
_PORTABLE_QUANTIFIER_LIMIT = 1000
_PORTABLE_QUANTIFIER_LIMIT_KEY = (4, str(_PORTABLE_QUANTIFIER_LIMIT))
_INVISIBLE_WHITESPACE = frozenset("\u00a0\u3000\ufeff\u0085")


@dataclass(frozen=True, slots=True)
class PatternViolation:
    path: str
    pattern: str
    rule: str


def portable_pattern_violations(pattern: object) -> tuple[str, ...]:
    """Report grammar violations without treating Python regex as the grammar."""
    if not isinstance(pattern, str):
        return ("pattern-must-be-string",)
    issues: set[str] = set()
    if len(pattern) < 2 or not pattern.startswith("^") or not pattern.endswith("$"):
        issues.add("outer-anchors-required")
    body = pattern[1:-1] if pattern.startswith("^") and pattern.endswith("$") else pattern
    index = 0
    depth = 0
    can_quantify = False
    while index < len(body):
        char = body[index]
        if char == "\\":
            index = _scan_escape(body, index, issues)
            can_quantify = True
            continue
        if char == "[":
            index = _scan_class(body, index, issues)
            can_quantify = True
            continue
        if char == "(":
            depth += 1
            if body.startswith("(?:", index):
                index += 3
            elif body.startswith("(?", index):
                issues.add("lookaround-or-flags")
                index += 2
            else:
                index += 1
            can_quantify = False
            continue
        if char == ")":
            if depth == 0:
                issues.add("unbalanced-group")
            else:
                depth -= 1
            index += 1
            can_quantify = True
            continue
        if char == "|":
            can_quantify = False
            index += 1
            continue
        if char in "*+?":
            if not can_quantify:
                issues.add("quantifier-without-atom")
            can_quantify = False
            index += 1
            continue
        if char == "{":
            matched = _BOUNDED_QUANTIFIER.match(body, index)
            if matched is None:
                issues.add("invalid-quantifier")
                index += 1
            else:
                if not can_quantify:
                    issues.add("quantifier-without-atom")
                lower = _quantifier_count_key(matched.group(1))
                raw_upper = matched.group(2)
                upper = _quantifier_count_key(raw_upper) if raw_upper else None
                if upper is not None and lower > upper:
                    issues.add("invalid-quantifier")
                if lower > _PORTABLE_QUANTIFIER_LIMIT_KEY or (
                    upper is not None and upper > _PORTABLE_QUANTIFIER_LIMIT_KEY
                ):
                    issues.add("quantifier-bound-exceeds-portable-limit")
                index = matched.end()
            can_quantify = False
            continue
        if char in "^$":
            issues.add("inner-anchor")
        elif char == ".":
            issues.add("wildcard-dot")
        elif char == "}" or char == "]":
            issues.add("unescaped-metacharacter")
        elif char == "\x00" or char in _INVISIBLE_WHITESPACE or (char.isspace() and char != " "):
            issues.add("implicit-whitespace-or-nul")
        index += 1
        can_quantify = True
    if depth:
        issues.add("unbalanced-group")
    try:
        re.compile(pattern)
    except (re.error, OverflowError, ValueError):
        issues.add("invalid-regex")
    return tuple(sorted(issues))


def _quantifier_count_key(raw: str) -> tuple[int, str]:
    """Compare arbitrarily long decimal bounds without integer conversion limits."""
    normalized = raw.lstrip("0") or "0"
    return (len(normalized), normalized)


def _scan_escape(body: str, index: int, issues: set[str]) -> int:
    if index + 1 >= len(body):
        issues.add("invalid-escape")
        return index + 1
    escaped = body[index + 1]
    if escaped == "x":
        if index + 3 < len(body) and all(char in _HEX for char in body[index + 2:index + 4]):
            return index + 4
        issues.add("invalid-hex-escape")
        return index + 2
    if escaped == "u":
        issues.add("regex-unicode-escape")
    elif escaped in "sSdDwW":
        issues.add("shorthand-character-class")
    elif escaped.isdecimal() or escaped in "gk":
        issues.add("backreference")
    elif escaped not in _ESCAPABLE:
        issues.add("unsupported-escape")
    return index + 2


def _scan_class(body: str, index: int, issues: set[str]) -> int:
    start = index
    index += 1
    if index < len(body) and body[index] == "^":
        index += 1
    content_start = index
    while index < len(body):
        if body[index] == "\\":
            index = _scan_escape(body, index, issues)
        elif body[index] == "]":
            if index == content_start:
                issues.add("empty-character-class")
            return index + 1
        else:
            if body[index] == "\x00":
                issues.add("implicit-whitespace-or-nul")
            index += 1
    issues.add("unclosed-character-class")
    return max(start + 1, index)


def matches_portable_pattern(pattern: str, value: str) -> bool:
    """Match the entire string; ``$`` alone admits a trailing LF in Python."""
    issues = portable_pattern_violations(pattern)
    if issues:
        raise ValueError(f"non-portable pattern: {', '.join(issues)}")
    return re.fullmatch(pattern, value) is not None


def schema_pattern_violations(document: Any) -> tuple[PatternViolation, ...]:
    """Inspect every schema pattern and patternProperties key, ignoring data values."""
    result: list[PatternViolation] = []

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}/{_pointer_token(str(key))}"
                if key in ("const", "enum"):
                    continue
                if key == "pattern":
                    for rule in portable_pattern_violations(child):
                        result.append(PatternViolation(child_path, str(child), rule))
                elif key == "patternProperties" and isinstance(child, dict):
                    for expression, nested in child.items():
                        expression_path = f"{child_path}/{_pointer_token(str(expression))}"
                        for rule in portable_pattern_violations(expression):
                            result.append(PatternViolation(expression_path, str(expression), rule))
                        visit(nested, expression_path)
                else:
                    visit(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}/{index}")

    visit(document, "")
    return tuple(result)


def _pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")
