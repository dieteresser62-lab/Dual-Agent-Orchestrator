from __future__ import annotations

from pathlib import Path

import pytest

from contracts import PlannedSlice
from plan_handoff import (
    PlanHandoffError,
    extract_implementation_slices,
    extract_slice_requirements,
    followup_task_path,
    render_followup_task,
    render_implementation_task,
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


def test_render_implementation_task_binds_only_the_approved_plan() -> None:
    rendered = render_implementation_task(
        work_plan_path="docs/internal/plan.md",
        target_branch="feature/implementation",
        approved_plan_commit="a" * 40,
        slices=(PlannedSlice(1, "implement", ("src/core.py",)),),
    )

    assert "APPROVED_PLAN_COMMIT: " + "a" * 40 in rendered
    assert "SLICE_PLAN: 1 | implement | src/core.py" in rendered


def test_followup_task_is_an_ordinary_correlation_free_inbox_document() -> None:
    class Finding:
        summary = "Fix the complete-review defect."
        affected_paths = ("src/core.py", "tests/test_core.py")
        acceptance_test = "The regression is covered and passes."

    source = followup_task_path(Path("inbox/doing/change-implement.md"))
    rendered = render_followup_task(
        target_branch="feature/implementation",
        findings=(Finding(),),
    )

    assert source == Path("inbox/doing/change-implement-followup.md")
    assert "Fix the complete-review defect." in rendered
    assert "`src/core.py`" in rendered
    assert "The regression is covered and passes." in rendered
    assert "source-run" not in rendered
    assert "ar1-" not in rendered
