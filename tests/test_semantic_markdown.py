from __future__ import annotations

from pathlib import Path

import pytest

from semantic_markdown import (
    MANAGED_SECTION_HEADINGS,
    MANAGED_SECTION_KEYS,
    SemanticMarkdownError,
    SemanticMarkdownKind,
    canonical_semantic_markdown,
    parse_semantic_markdown,
)


ROOT = Path(__file__).resolve().parents[1]


def _slice_document(body: str = "green validation output") -> str:
    lines = ["# Slice 01 – Semantic boundary", "", "authored text", ""]
    for key in MANAGED_SECTION_KEYS:
        lines.extend(
            (
                f"## {MANAGED_SECTION_HEADINGS[key]}",
                "",
                f"<!-- audit:{key}:begin -->",
                body,
                f"<!-- audit:{key}:end -->",
                "",
            )
        )
    return "\n".join(lines)


def _appendix_document(body: str = "green validation output") -> str:
    lines = ["# Work plan", "", "authored plan", "", "## Orchestrator-Prüfprotokoll", ""]
    for key in MANAGED_SECTION_KEYS:
        lines.extend(
            (
                f"### {MANAGED_SECTION_HEADINGS[key]}",
                "",
                f"<!-- audit:{key}:begin -->",
                body,
                f"<!-- audit:{key}:end -->",
                "",
            )
        )
    return "\n".join(lines)


def test_complete_slice_projection_is_semantic_and_idempotent() -> None:
    first = _slice_document("old output")
    second = _slice_document("new output with retired roles and provider names")

    assert canonical_semantic_markdown(first) == canonical_semantic_markdown(second)
    assert canonical_semantic_markdown(canonical_semantic_markdown(first)) == canonical_semantic_markdown(first)
    assert "authored text" in canonical_semantic_markdown(first)
    assert "old output" not in canonical_semantic_markdown(first)


def test_appendix_classification_removes_only_the_complete_appendix() -> None:
    document = parse_semantic_markdown(_appendix_document(), path="docs/internal/plan.md")

    assert document.kind is SemanticMarkdownKind.AUDIT_APPENDIX
    assert document.semantic_text(remove_appendix=True) == "# Work plan\n\nauthored plan\n"


@pytest.mark.parametrize(
    ("name", "mutate"),
    (
        ("missing-end", lambda text: text.replace("<!-- audit:findings:end -->", "")),
        (
            "single-pair",
            lambda _text: "## Findings-Lebenszyklus\n<!-- audit:findings:begin -->\nx\n<!-- audit:findings:end -->",  # allowlist:german
        ),
        (
            "duplicate",
            lambda text: text.replace(
                "<!-- audit:findings:end -->",
                "<!-- audit:findings:end -->\n<!-- audit:findings:end -->",
            ),
        ),
        (
            "wrong-order",
            lambda text: text.replace("claude-review", "temporary", 2).replace(
                "codex-responses", "claude-review", 2
            ).replace("temporary", "codex-responses", 2),
        ),
        (
            "nested",
            lambda text: text.replace(
                "<!-- audit:claude-review:end -->",
                "<!-- audit:findings:begin -->\n<!-- audit:claude-review:end -->",
            ),
        ),
        (
            "wrong-heading",
            lambda text: text.replace(
                "## Findings-Lebenszyklus", "## Incorrect heading"  # allowlist:german
            ),
        ),
        (
            "unknown-key",
            lambda text: text.replace("audit:findings", "audit:unknown"),
        ),
        (
            "body-injection",
            lambda text: text.replace(
                "green validation output",
                "<!-- audit:findings:end -->",
                1,
            ),
        ),
    ),
)
def test_invalid_control_structures_fail_closed(name: str, mutate) -> None:
    with pytest.raises(SemanticMarkdownError) as caught:
        canonical_semantic_markdown(
            mutate(_slice_document()), path=f"docs/internal/{name}.md"
        )

    assert caught.value.path.endswith(f"{name}.md")
    assert caught.value.marker_kind
    assert caught.value.cause


def test_fenced_blockquoted_indented_and_inline_examples_remain_visible() -> None:
    examples = "\n".join(
        (
            "```markdown",
            "<!-- audit:findings:begin -->",
            "```",
            "> <!-- audit:findings:end -->",
            "    <!-- audit:findings:begin -->",
            "inline <!-- audit:findings:end -->",
        )
    )

    assert canonical_semantic_markdown(examples) == examples


def test_current_plan_stale_attestation_bytes_are_not_semantic_authority() -> None:
    plan = (
        ROOT
        / "docs/internal/01-validierungsevidenz-darf-aktive-pruefungen-nicht-selbst-vergiften-arbeitsplan.md"  # allowlist:german
    ).read_text(encoding="utf-8")
    stale = "028a4a6e346a"
    current = "219134f218b1"
    assert stale in plan and current in plan

    rerendered = plan.replace(stale, "f" * len(stale)).replace(
        "3b1bb7f924c1", "e" * 12
    )
    assert canonical_semantic_markdown(plan) == canonical_semantic_markdown(rerendered)
