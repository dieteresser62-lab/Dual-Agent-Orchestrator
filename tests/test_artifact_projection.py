"""Record-native human projection contracts."""
from __future__ import annotations

from readable_audit import render_overall, render_slice
from semantic_markdown import canonical_semantic_markdown, parse_semantic_markdown
from test_readable_audit import _facts


def test_projection_is_deterministic_and_semantically_bounded() -> None:
    facts = _facts()
    first = render_slice(facts, 1)
    assert first == render_slice(facts, 1)
    assert parse_semantic_markdown(first, require_managed=True).sections
    assert "Befund nur in Slice 2" not in first
    assert "ar1-" not in first


def test_overall_projection_contains_each_finding_once() -> None:
    overall = render_overall(_facts(), task="Task", branch="feature/test")
    assert overall.count("| C-01 |") == 1
    assert overall.count("| C-02 |") == 1
    assert "### C-01" not in overall
    assert len(canonical_semantic_markdown(overall)) < len(overall)
