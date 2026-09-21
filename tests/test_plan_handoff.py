from __future__ import annotations

from pathlib import Path

import pytest

from contracts import PlannedSlice
from plan_handoff import (
    PlanHandoffError,
    branch_discovery_task_path,
    extract_implementation_slices,
    extract_slice_requirements,
    remediation_plan_paths,
    render_branch_discovery_task,
    render_implementation_task,
    render_remediation_plan_task,
)


@pytest.mark.parametrize(
    "heading",
    (
        "**Exakter Änderungspfad**",
        "**Exakter Änderungspfad:**",
        "**Exakte Änderungspfade**",
        "**Exakte Änderungspfade:**",
    ),
)
def test_extract_implementation_slices_accepts_safe_path_heading_variants(
    heading: str,
) -> None:
    markdown = (
        "# Plan\n\n"
        "### Slice 01 - Contract\n\n"
        f"{heading}\n\n"
        "- `src/contract.py`\n"
        "\n#### \u0041kzeptanzkriterien\n\n"
        "- Contract is implemented.\n"
    )

    slices = extract_implementation_slices(markdown, plan_stem="work-plan")

    assert len(slices) == 1
    assert slices[0].scope_paths == (
        "docs/internal/slice-work-plan-01-contract.md",
        "src/contract.py",
    )


def test_extract_implementation_slices_still_rejects_free_form_path_heading() -> None:
    markdown = (
        "# Plan\n\n"
        "### Slice 01 - Contract\n\n"
        "Änderungspfade:\n\n"
        "- `src/contract.py`\n"
        "\n#### \u0041kzeptanzkriterien\n\n"
        "- Contract is implemented.\n"
    )

    with pytest.raises(PlanHandoffError, match="no exact change-path section"):
        extract_implementation_slices(markdown, plan_stem="work-plan")


def test_exact_path_section_stops_before_level_four_validation_heading() -> None:
    markdown = "\n".join(
        (
            "### Slice 1 - UI contract",
            "",
            "**Exakter Änderungspfad**",
            "",
            "- `Simulator.html`",
            "- `tests/browser-smoke.test.mjs`",
            "",
            "#### Fokussierte Validierung",
            "",
            "- `git diff --check`",
            "- `npm test`",
            "- `npm run test:browser`",
            "",
            "#### \u0041kzeptanzkriterien",
            "",
            "- Browser behavior is covered.",
        )
    )

    slices = extract_implementation_slices(markdown, plan_stem="work-plan")

    assert slices[0].scope_paths == (
        "Simulator.html",
        "docs/internal/slice-work-plan-01-ui-contract.md",
        "tests/browser-smoke.test.mjs",
    )


def test_handoff_accepts_generated_goal_and_focused_acceptance_test_section() -> None:
    markdown = """# Plan

### Slice 1 - Providerattempt core

#### Integrationspunkte und Umsetzung

- Implement the record lifecycle.

**Exakter Änderungspfad**

- `src/artifact_models.py`

#### Fokussierte synthetische Akzeptanztests

- `tests/test_artifact_models.py`: Roundtrip and reject malformed records.
"""

    slices = extract_implementation_slices(markdown, plan_stem="work-plan")

    assert slices[0].summary == "Providerattempt core"


def test_handoff_rejects_slice_without_acceptance_criteria() -> None:
    markdown = """### Slice 1 - Incomplete

**Exakter Änderungspfad**

- `src/core.py`
"""

    with pytest.raises(PlanHandoffError, match="acceptance-criteria"):
        extract_implementation_slices(markdown, plan_stem="work-plan")


def _acceptance_plan(records: str) -> str:
    return (
        "### Slice 1 - Equivalent list forms\n\n"
        "**Exakter Änderungspfad**\n\n"
        "- `src/core.py`\n\n"
        "**\u0041kzeptanzkriterien**\n\n"
        f"{records}\n"
    )


def test_ordered_acceptance_list_matches_equivalent_unordered_list() -> None:
    unordered = _acceptance_plan(
        "- Preserve the first behavior.\n- Preserve the second behavior."
    )
    ordered = _acceptance_plan(
        "1. Preserve the first behavior.\n2. Preserve the second behavior."
    )

    assert extract_slice_requirements(ordered, 1) == extract_slice_requirements(
        unordered, 1
    )
    assert extract_implementation_slices(ordered, plan_stem="work-plan") == (
        extract_implementation_slices(unordered, plan_stem="work-plan")
    )


def test_mixed_acceptance_list_markers_are_equivalent_records() -> None:
    mixed = _acceptance_plan(
        "- Preserve the first behavior.\n2. Preserve the second behavior.\n"
        "a) Preserve the third behavior."
    )

    _, criteria = extract_slice_requirements(mixed, 1)

    assert criteria == (
        "Preserve the first behavior.",
        "Preserve the second behavior.",
        "Preserve the third behavior.",
    )


def test_free_text_acceptance_paragraph_remains_invalid() -> None:
    markdown = _acceptance_plan("This paragraph has no list marker.")

    with pytest.raises(PlanHandoffError, match="must contain list records"):
        extract_implementation_slices(markdown, plan_stem="work-plan")


def test_render_implementation_task_binds_finding_export_without_finding_prose() -> None:
    rendered = render_implementation_task(
        work_plan_path="docs/internal/plan.md",
        target_branch="feature/finding-handoff",
        approved_plan_commit="a" * 40,
        slices=(PlannedSlice(1, "implement", ("src/core.py",)),),
        finding_handoff=("source-run", "ar1-" + "b" * 64),
    )

    assert "FINDING_HANDOFF_SOURCE_RUN: source-run\n" in rendered
    assert f"FINDING_HANDOFF_EXPORT: ar1-{'b' * 64}\n" in rendered
    assert "C-01" not in rendered


def test_render_branch_discovery_task_is_slice_free_and_uses_counterpart_path() -> None:
    source = branch_discovery_task_path(Path("inbox/doing/change-implement.md"))

    rendered = render_branch_discovery_task(
        target_branch="feature/finding-handoff",
        scope_paths=("src/core.py", "tests/test_core.py"),
        finding_handoff=("source-run", "ar1-" + "b" * 64),
    )

    assert source == Path("inbox/doing/change-branch-discovery.md")
    assert "ORCHESTRATOR_MODE: BRANCH_DISCOVERY\n" in rendered
    assert "FINDING_HANDOFF_SOURCE_RUN: source-run\n" in rendered
    assert f"FINDING_HANDOFF_EXPORT: ar1-{'b' * 64}\n" in rendered
    assert "TASK_SCOPE: src/core.py, tests/test_core.py\n" in rendered
    assert "SLICE_PLAN" not in rendered


def test_render_remediation_plan_task_advances_one_deterministic_cycle() -> None:
    task, work_plan = remediation_plan_paths(
        Path("inbox/doing/change-remediation-2-branch-discovery.md"),
        cycle_number=3,
    )

    rendered = render_remediation_plan_task(
        work_plan_path=work_plan,
        target_branch="feature/finding-handoff",
        scope_paths=("src/core.py",),
        finding_handoff=("discovery-run", "ar1-" + "c" * 64),
    )

    assert task == Path("inbox/doing/change-remediation-3-plan.md")
    assert work_plan == "docs/internal/change-remediation-3-plan-arbeitsplan.md"
    assert "ORCHESTRATOR_MODE: PLAN_ONLY\n" in rendered
    assert f"WORK_PLAN_PATH: {work_plan}\n" in rendered
    assert "FINDING_HANDOFF_SOURCE_RUN: discovery-run\n" in rendered
    assert f"FINDING_HANDOFF_EXPORT: ar1-{'c' * 64}\n" in rendered
    assert f"TASK_SCOPE: {work_plan}, src/core.py\n" in rendered
