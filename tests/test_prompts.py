from __future__ import annotations

from prompts import NATIVE_CLAUDE_SYSTEM_POLICY, NATIVE_CODEX_SYSTEM_POLICY


def test_native_policies_require_schema_bound_json() -> None:
    for policy in (NATIVE_CODEX_SYSTEM_POLICY, NATIVE_CLAUDE_SYSTEM_POLICY):
        assert "JSON" in policy
        assert "schema" in policy
        assert "Markdown" in policy


def test_native_policies_do_not_define_result_marker_grammar() -> None:
    combined = NATIVE_CODEX_SYSTEM_POLICY + NATIVE_CLAUDE_SYSTEM_POLICY
    for marker in ("STATUS: DONE", "PLAN_APPROVAL:", "SLICE_APPROVAL:"):
        assert marker not in combined
