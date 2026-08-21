from __future__ import annotations

import pytest

from plan_handoff import PlanHandoffError, extract_implementation_slices


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
