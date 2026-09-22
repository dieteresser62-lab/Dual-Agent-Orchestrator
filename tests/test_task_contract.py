from __future__ import annotations

import pytest

from task_contract import (
    FEATURE_BRANCH_PATTERN,
    TaskContractError,
    TaskMode,
    parse_task_contract,
)


def test_plan_only_contract_parses_german_scope_and_markdown_escape() -> None:
    contract = parse_task_contract(
        r"""
ORCHESTRATOR_MODE: PLAN_ONLY
WORK_PLAN_PATH: docs/internal/handbuch.md
TARGET_BRANCH: codex/handbuch-ueberarbeitung

## Erlaubter Scope

- docs/internal/handbuch.md
- docs/internal/handbuch\_slice--*.md
"""
    )

    assert contract.mode is TaskMode.PLAN_ONLY
    assert contract.work_plan_path == "docs/internal/handbuch.md"
    assert contract.target_branch == "codex/handbuch-ueberarbeitung"
    assert contract.scope_patterns == (
        "docs/internal/handbuch.md",
        "docs/internal/handbuch_slice--*.md",
    )


def test_implement_contract_accepts_compact_markers() -> None:
    contract = parse_task_contract(
        """
ORCHESTRATOR_MODE: IMPLEMENT
TARGET_BRANCH: feature/example
TASK_SCOPE: src/app.py, tests/test_app.py, docs/internal/plan.md
"""
    )

    assert contract.mode is TaskMode.IMPLEMENT
    assert contract.scope_patterns == (
        "src/app.py",
        "tests/test_app.py",
        "docs/internal/plan.md",
    )


def test_informal_task_derives_safe_plan_only_contract_from_file_name() -> None:
    contract = parse_task_contract(
        """
# Neue Idee: Strategien vergleichen

TARGET_BRANCH: codex/strategie-vergleich

Ich möchte zwei Strategien verständlich miteinander vergleichen können.
Bitte frage nur bei echten fachlichen Alternativen nach.
""",
        source_name="Strategie Vergleich.md",
    )

    assert contract.mode is TaskMode.PLAN_ONLY
    assert contract.work_plan_path == (
        "docs/internal/strategie-vergleich-arbeitsplan.md"
    )
    assert contract.scope_patterns == (
        "docs/internal/strategie-vergleich-arbeitsplan.md",
    )
    assert contract.target_branch == "codex/strategie-vergleich"
    assert contract.target_branch_generated is False
    assert contract.informal_intake is True


def test_task_uses_one_unique_branch_reference_from_free_text() -> None:
    contract = parse_task_contract(
        """# Export stabilisieren

Die Umsetzung soll auf `feature/export-stabilisieren` erfolgen. Der Branch
feature/export-stabilisieren wird in the acceptance criteria erneut genannt.
""",
        source_name="export.md",
    )

    assert contract.target_branch == "feature/export-stabilisieren"
    assert contract.target_branch_generated is False


def test_task_rejects_distinct_free_text_branch_references_as_ambiguous() -> None:
    with pytest.raises(TaskContractError, match="ambiguous target branches"):
        parse_task_contract(
            """# Export stabilisieren

Als Möglichkeiten werden feature/export-v1 und codex/export-v2 genannt.
""",
            source_name="export.md",
        )


def test_task_generates_readable_deterministic_branch_from_task_content() -> None:
    text = """# Beitragsgrenzen für Ärzte: Check!

Die Berechnung soll nachvollziehbar werden.
"""

    first = parse_task_contract(text, source_name="first-name.md")
    second = parse_task_contract(text, source_name="another-name.md")

    assert first.target_branch == second.target_branch
    assert first.target_branch.startswith(
        "feature/beitragsgrenzen-fur-arzte-check-"
    )
    assert FEATURE_BRANCH_PATTERN.fullmatch(first.target_branch)
    assert first.target_branch_generated is True


def test_formal_task_without_branch_generates_from_its_heading() -> None:
    contract = parse_task_contract(
        """# Audit-Export absichern
ORCHESTRATOR_MODE: IMPLEMENT
TASK_SCOPE: src/audit.py
"""
    )

    assert contract.mode is TaskMode.IMPLEMENT
    assert contract.target_branch.startswith("feature/audit-export-absichern-")
    assert contract.target_branch_generated is True


def test_informal_task_slug_is_ascii_and_has_digest_fallback() -> None:
    umlaut = parse_task_contract(
        "# Idee\nTARGET_BRANCH: feature/idee\n",
        source_name="Überblick für März.md",
    )
    fallback = parse_task_contract(
        "# Idee\nTARGET_BRANCH: feature/symbol\n",
        source_name="🎯.md",
    )

    assert umlaut.work_plan_path == "docs/internal/uberblick-fur-marz-arbeitsplan.md"
    assert fallback.work_plan_path is not None
    assert fallback.work_plan_path.startswith("docs/internal/task-")
    assert fallback.work_plan_path.endswith("-arbeitsplan.md")


def test_partially_formal_task_still_fails_closed() -> None:
    with pytest.raises(TaskContractError, match="WORK_PLAN_PATH"):
        parse_task_contract(
            "ORCHESTRATOR_MODE: PLAN_ONLY\nTARGET_BRANCH: feature/x\n"
            "TASK_SCOPE: docs/internal/x.md\n",
            source_name="idea.md",
        )


def test_informal_task_cannot_be_forced_directly_to_implement() -> None:
    with pytest.raises(TaskContractError, match="informal task is planning input"):
        parse_task_contract(
            "# Idee\nTARGET_BRANCH: feature/x\n",
            source_name="idea.md",
            mode_override=False,
        )


def test_approved_plan_handoff_binds_embedded_slices() -> None:
    contract = parse_task_contract(
        """
ORCHESTRATOR_MODE: IMPLEMENT
WORK_PLAN_PATH: docs/internal/plan.md
APPROVED_PLAN_COMMIT: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
TARGET_BRANCH: feature/example
TASK_SCOPE: src/app.py, docs/internal/slice-plan-01-app.md

SLICE_PLAN: 1 | App umsetzen | src/app.py, docs/internal/slice-plan-01-app.md
"""
    )

    assert contract.approved_plan_commit == "a" * 40
    assert len(contract.approved_slices) == 1
    assert contract.approved_slices[0].scope_paths == (
        "docs/internal/slice-plan-01-app.md",
        "src/app.py",
    )


@pytest.mark.parametrize(
    ("text", "message"),
    (
        (
            "ORCHESTRATOR_MODE: PLAN_ONLY\nTARGET_BRANCH: feature/x\n"
            "TASK_SCOPE: docs/plan.md\n",
            "WORK_PLAN_PATH",
        ),
        (
            "ORCHESTRATOR_MODE: IMPLEMENT\nTARGET_BRANCH: main\n"
            "TASK_SCOPE: src/app.py\n",
            "feature/<name>",
        ),
        (
            "ORCHESTRATOR_MODE: IMPLEMENT\nTARGET_BRANCH: feature/x\n",
            "task requires TASK_SCOPE",
        ),
        (
            "ORCHESTRATOR_MODE: PLAN_ONLY\nWORK_PLAN_PATH: ../plan.md\n"
            "TARGET_BRANCH: feature/x\nTASK_SCOPE: docs/**\n",
            "repository-relative",
        ),
    ),
)
def test_invalid_task_contracts_fail_closed(text: str, message: str) -> None:
    with pytest.raises(TaskContractError, match=message):
        parse_task_contract(text)


def test_cli_overrides_must_not_conflict_with_task_markers() -> None:
    text = (
        "ORCHESTRATOR_MODE: PLAN_ONLY\n"
        "WORK_PLAN_PATH: docs/plan.md\n"
        "TARGET_BRANCH: feature/plan\n"
        "TASK_SCOPE: docs/plan.md\n"
    )

    with pytest.raises(TaskContractError, match="conflicts"):
        parse_task_contract(text, mode_override=False)
    with pytest.raises(TaskContractError, match="conflicts"):
        parse_task_contract(text, target_branch_override="feature/other")


def test_cli_branch_override_keeps_priority_over_free_text_reference() -> None:
    contract = parse_task_contract(
        "# Plan\nThe prose mentions feature/prose-only.\n",
        source_name="plan.md",
        target_branch_override="codex/cli-wins",
    )

    assert contract.target_branch == "codex/cli-wins"
    assert contract.target_branch_generated is False
