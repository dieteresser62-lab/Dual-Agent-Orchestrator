"""Byte reference for the human form of all three audit documents."""
from __future__ import annotations

import json
from pathlib import Path

from readable_audit import render_overall, render_plan_appendix, render_slice
from test_readable_audit import _facts


BASELINE = Path(__file__).parent / "fixtures/readable-audit-baseline-v1.json"


def test_readable_audit_matches_complete_byte_reference() -> None:
    expected = json.loads(BASELINE.read_text(encoding="utf-8"))
    facts = _facts()
    assert expected == {
        "slice": render_slice(facts, 1),
        "overall": render_overall(facts, task="Task", branch="feature/test"),
        "plan_appendix": render_plan_appendix(_facts(plan_only=True)),
    }
