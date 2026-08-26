from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_has_no_text_review_normalizer_or_repair_provider() -> None:
    workflow = (ROOT / "src/workflow.py").read_text(encoding="utf-8")
    orchestrator = (ROOT / "src/orchestrator.py").read_text(encoding="utf-8")
    runtime = (ROOT / "src/agent_runtime.py").read_text(encoding="utf-8")
    for symbol in (
        "normalize_review_contract",
        "normalize_review_contract_output",
        "repair_review_contract",
        "recover_failed_reviewer_output",
        "claude_contract_repair",
    ):
        assert symbol not in workflow + orchestrator + runtime


def test_native_failures_are_not_rewritten_by_an_llm() -> None:
    source = (ROOT / "src/workflow.py").read_text(encoding="utf-8")
    assert "persist_native_review_contract" in source
    assert "Claude returned a non-native review result" in source
