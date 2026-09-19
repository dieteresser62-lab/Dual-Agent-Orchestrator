from __future__ import annotations

import pytest
import native_finding_decisions

from task_contract import TaskContractError, TaskMode, parse_task_contract


BRANCH_DISCOVERY_TASK = """ORCHESTRATOR_MODE: BRANCH_DISCOVERY
FINDING_HANDOFF_SOURCE_RUN: predecessor-run
FINDING_HANDOFF_EXPORT: ar1-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
TARGET_BRANCH: feature/example
TASK_SCOPE: src/**, tests/**
"""


def test_branch_discovery_task_is_dormant_and_requires_its_family_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(TaskContractError, match="JOINT_67_68"):
        parse_task_contract(BRANCH_DISCOVERY_TASK)

    monkeypatch.setattr(
        native_finding_decisions,
        "JOINT_67_68_NATIVE_CONTRACT_CUTOVER",
        True,
    )
    contract = parse_task_contract(BRANCH_DISCOVERY_TASK)
    assert contract.mode is TaskMode.BRANCH_DISCOVERY
    assert contract.work_plan_path is None
    assert contract.finding_handoff_source_run_id == "predecessor-run"

    with pytest.raises(TaskContractError, match="family predecessor handoff"):
        parse_task_contract(
            "ORCHESTRATOR_MODE: BRANCH_DISCOVERY\n"
            "TARGET_BRANCH: feature/example\nTASK_SCOPE: src/**\n"
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
    assert contract.informal_intake is True


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


def test_approved_plan_contract_accepts_closed_finding_handoff_reference() -> None:
    text = """ORCHESTRATOR_MODE: IMPLEMENT
WORK_PLAN_PATH: docs/internal/plan.md
APPROVED_PLAN_COMMIT: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
FINDING_HANDOFF_SOURCE_RUN: plan-run
FINDING_HANDOFF_EXPORT: ar1-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
TARGET_BRANCH: feature/x
TASK_SCOPE: src/x.py
SLICE_PLAN: 1 | implementation | src/x.py
"""

    contract = parse_task_contract(text)

    assert contract.finding_handoff_source_run_id == "plan-run"
    assert contract.finding_handoff_export_record_id == "ar1-" + "b" * 64


def test_finding_handoff_reference_is_all_or_nothing() -> None:
    text = """ORCHESTRATOR_MODE: IMPLEMENT
WORK_PLAN_PATH: docs/internal/plan.md
APPROVED_PLAN_COMMIT: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
FINDING_HANDOFF_SOURCE_RUN: plan-run
TARGET_BRANCH: feature/x
TASK_SCOPE: src/x.py
SLICE_PLAN: 1 | implementation | src/x.py
"""

    with pytest.raises(TaskContractError, match="requires both"):
        parse_task_contract(text)


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
