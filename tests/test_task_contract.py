from __future__ import annotations

import pytest

from task_contract import TaskContractError, TaskMode, parse_task_contract


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
            "ORCHESTRATOR_MODE: IMPLEMENT\nTASK_SCOPE: src/app.py\n",
            "TARGET_BRANCH",
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
