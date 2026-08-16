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
    )

    with pytest.raises(PlanHandoffError, match="no exact change-path section"):
        extract_implementation_slices(markdown, plan_stem="work-plan")
