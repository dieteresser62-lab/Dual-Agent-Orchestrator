from __future__ import annotations

import re

import pytest

from schema_patterns import (
    matches_portable_pattern,
    portable_pattern_violations,
    schema_pattern_violations,
)


@pytest.mark.parametrize(
    "pattern, accepted, rejected",
    (
        (r"^abc$", "abc", "ab"),
        (r"^[A-Za-z0-9_-]+$", "Ab_2-", "Ab 2"),
        (r"^(foo|bar)$", "bar", "baz"),
        (r"^(?:ab|cd)?[0-9]*$", "ab42", "ef42"),
        (r"^\x00$", "\x00", "x"),
        (r"^[ \x09\x0A\x0D]+$", " \t\n\r", "x"),
        (r"^[^\x00]+$", "hello", "\x00"),
        (r"^a\.b$", "a.b", "axb"),
        (r"^a{64}$", "a" * 64, "a" * 63),
        (r"^a{1,200}$", "a" * 200, "a" * 201),
        (r"^a{0,}$", "", "b"),
        (r"^a{1000}$", "a" * 1000, "a" * 999),
    ),
)
def test_allowed_grammar_matches_entire_body(
    pattern: str, accepted: str, rejected: str
) -> None:
    assert portable_pattern_violations(pattern) == ()
    assert matches_portable_pattern(pattern, accepted)
    assert not matches_portable_pattern(pattern, rejected)


@pytest.mark.parametrize(
    "pattern, rule",
    (
        ("abc", "outer-anchors-required"),
        (r"^a|b$", "top-level-alternation"),
        (r"^a^b$", "inner-anchor"),
        (r"^a$b$", "inner-anchor"),
        (r"^(?i:a)$", "lookaround-or-flags"),
        (r"^(?=a)a$", "lookaround-or-flags"),
        (r"^(?!a)b$", "lookaround-or-flags"),
        (r"^(?<=a)b$", "lookaround-or-flags"),
        (r"^(a)\1$", "backreference"),
        (r"^\g<1>$", "backreference"),
        (r"^\u0000$", "regex-unicode-escape"),
        (r"^\s$", "shorthand-character-class"),
        (r"^\d$", "shorthand-character-class"),
        (r"^\w$", "shorthand-character-class"),
        (r"^.$", "wildcard-dot"),
        (r"^a{1001}$", "quantifier-bound-exceeds-portable-limit"),
        (r"^a{0,1001}$", "quantifier-bound-exceeds-portable-limit"),
        (r"^a{1001,}$", "quantifier-bound-exceeds-portable-limit"),
        (r"^a{1001,1000}$", "invalid-quantifier"),
        ("^a{" + "9" * 5000 + "}$", "quantifier-bound-exceeds-portable-limit"),
        (r"^a{3,2}$", "invalid-quantifier"),
        (r"^a{,5}$", "invalid-quantifier"),
        (r"^{2}a$", "quantifier-without-atom"),
        (r"^a\n$", "unsupported-escape"),
        ("^a\x00$", "implicit-whitespace-or-nul"),
        ("^a\u00a0$", "implicit-whitespace-or-nul"),
        ("^a\u3000$", "implicit-whitespace-or-nul"),
        ("^a\ufeff$", "implicit-whitespace-or-nul"),
        ("^a\u0085$", "implicit-whitespace-or-nul"),
        ("^[a\t]$", "implicit-whitespace-or-nul"),
        ("^[a\u3000]$", "implicit-whitespace-or-nul"),
        (r"^a\x0$", "invalid-hex-escape"),
        (r"^[a-z$", "unclosed-character-class"),
        (r"^(a$", "unbalanced-group"),
        (r"^*a$", "quantifier-without-atom"),
    ),
)
def test_forbidden_grammar_is_reported(pattern: str, rule: str) -> None:
    assert rule in portable_pattern_violations(pattern)
    with pytest.raises(ValueError, match="non-portable pattern"):
        matches_portable_pattern(pattern, "a")


def test_dollar_anchor_does_not_admit_terminal_lf() -> None:
    pattern = r"^body$"
    assert re.match(pattern, "body\n") is not None
    assert not matches_portable_pattern(pattern, "body\n")
    assert not matches_portable_pattern(pattern, "body\r\n")
    assert matches_portable_pattern(pattern, "body")


def test_explicit_control_class_and_domain_checks_have_distinct_jobs() -> None:
    no_controls = r"^[^\x00\x0A\x0D]+$"
    for invalid in ("a\x00b", "a\nb", "a\r\nb"):
        assert not matches_portable_pattern(no_controls, invalid)
    for accepted_by_pattern in ("a\u00a0b", "a\u3000b", "a\ufeffb", "a\u0085b", "../a"):
        assert matches_portable_pattern(no_controls, accepted_by_pattern)
    assert not matches_portable_pattern(no_controls, "")
    # Path traversal and nonblank text need domain validation in the v3 cutover.


def test_schema_walker_checks_pattern_properties_and_skips_data_values() -> None:
    document = {
        "const": {"pattern": r"(?=ignored)"},
        "enum": [{"pattern": r"(?=ignored)"}],
        "pattern": r"^ok$",
        "patternProperties": {
            r"(?=bad)": {"pattern": r"^\s+$"},
        },
        "$defs": {"x": {"pattern": r"^[a-z]+$"}},
    }
    issues = schema_pattern_violations(document)
    assert [(item.path, item.rule) for item in issues] == [
        ("/patternProperties/(?=bad)", "lookaround-or-flags"),
        ("/patternProperties/(?=bad)", "outer-anchors-required"),
        ("/patternProperties/(?=bad)/pattern", "shorthand-character-class"),
    ]
