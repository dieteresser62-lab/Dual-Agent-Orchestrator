from __future__ import annotations

from prompts import (
    build_phase1_claude_confirm_prompt,
    build_phase1_claude_plan_prompt,
    build_phase1_codex_review_prompt,
    build_phase2_claude_review_prompt,
    build_phase2_codex_implement_prompt,
    build_test_failure_block,
)


def test_phase1_claude_plan_prompt_includes_markers_and_delimiters() -> None:
    prompt = build_phase1_claude_plan_prompt(
        task_text="Implement A",
        shared_text="",
        cycle=1,
        open_block="NONE",
    )
    assert "<<<TASK_BEGIN>>>" in prompt
    assert "<<<SHARED_BEGIN>>>" in prompt
    assert "PHASE1_APPROVAL: YES or PHASE1_APPROVAL: NO" in prompt
    assert prompt.endswith("STATUS: DONE")


def test_phase1_codex_review_prompt_handles_special_characters() -> None:
    task_text = "Value with markers <<<INJECT_BEGIN>>> and pipes | and colons :"
    prompt = build_phase1_codex_review_prompt(
        task_text=task_text,
        shared_text="previous",
        cycle=2,
        previous_open_block="F-001",
    )
    assert task_text in prompt
    assert "PHASE1_APPROVAL: YES or PHASE1_APPROVAL: NO" in prompt
    assert prompt.endswith("STATUS: DONE")


def test_phase1_claude_confirm_prompt_contains_codex_contract() -> None:
    prompt = build_phase1_claude_confirm_prompt(
        task_text="Task",
        shared_text="Shared",
        cycle=3,
        open_block="F-010",
        codex_approval="NO",
    )
    assert "PHASE1_APPROVAL: NO" in prompt
    assert "OPEN_FINDINGS: F-010" in prompt
    assert prompt.endswith("STATUS: DONE")


def test_phase2_codex_implement_prompt_uses_empty_shared_fallback() -> None:
    prompt = build_phase2_codex_implement_prompt(
        task_text="Task",
        plan_text="Plan",
        shared_text="",
        cycle=1,
        open_block="NONE",
        test_failure_context="",
    )
    assert "(empty)" in prompt
    assert "IMPLEMENTATION_READY: YES or IMPLEMENTATION_READY: NO" in prompt
    assert prompt.endswith("STATUS: DONE")


def test_phase2_claude_review_prompt_embeds_all_sections() -> None:
    prompt = build_phase2_claude_review_prompt(
        task_text="Task",
        plan_text="Plan",
        shared_text="Shared",
        file_snapshots="<<<FILES_BEGIN>>>\n### src/a.py\nx\n<<<FILES_END>>>",
        test_snapshot="tests ok",
        cycle=1,
        previous_open_block="NONE",
        snapshot="repo snapshot",
    )
    assert "<<<PLAN_BEGIN>>>" in prompt
    assert "<<<TEST_SNAPSHOT_BEGIN>>>" in prompt
    assert "<<<FILES_BEGIN>>>" in prompt
    assert "<<<SNAPSHOT_BEGIN>>>" in prompt
    assert "PHASE2_APPROVAL: YES only when OPEN_FINDINGS: NONE" in prompt
    assert prompt.endswith("STATUS: DONE")


def test_build_test_failure_block_truncates_and_includes_command() -> None:
    block = build_test_failure_block("x" * 20, "pytest -q", max_chars=10)
    assert "<<<TEST_FAILURE_PRIORITY_BEGIN>>>" in block
    assert "Fix the failing tests before any other work." in block
    assert "Re-run locally with: pytest -q" in block
    assert "...[truncated]" in block
    assert block.endswith("<<<TEST_FAILURE_PRIORITY_END>>>")


def test_phase2_codex_implement_prompt_includes_failure_context_when_present() -> None:
    prompt = build_phase2_codex_implement_prompt(
        task_text="Task",
        plan_text="Plan",
        shared_text="Shared",
        cycle=2,
        open_block="F-001",
        test_failure_context="<<<TEST_FAILURE_PRIORITY_BEGIN>>>\nX\n<<<TEST_FAILURE_PRIORITY_END>>>",
    )
    assert "<<<TEST_FAILURE_PRIORITY_BEGIN>>>" in prompt
