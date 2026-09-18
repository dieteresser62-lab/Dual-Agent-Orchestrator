from __future__ import annotations

import pytest

from acceptance_criteria import (
    AcceptanceCriterion,
    acceptance_criteria_from_documents,
    acceptance_criteria_from_texts,
    acceptance_criterion_id,
)


def test_criterion_identity_is_stable_when_order_changes() -> None:
    original = acceptance_criteria_from_texts(
        7, ("The route is replay-stable.", "The regression test passes.")
    )
    reordered = acceptance_criteria_from_texts(
        7, ("The regression test passes.", "The route is replay-stable.")
    )

    assert tuple(item.text for item in original) != tuple(
        item.text for item in reordered
    )
    assert {item.text: item.criterion_id for item in original} == {
        item.text: item.criterion_id for item in reordered
    }
    assert original[0].criterion_id == acceptance_criterion_id(7, original[0].text)


def test_criteria_reject_duplicates_and_non_derived_record_ids() -> None:
    with pytest.raises(ValueError, match="unique within their Slice"):
        acceptance_criteria_from_texts(7, ("Same condition", "Same condition"))

    with pytest.raises(ValueError, match="does not match"):
        acceptance_criteria_from_documents(
            7,
            (
                {
                    "criterion_id": acceptance_criterion_id(8, "Exact text"),
                    "text": "Exact text",
                },
            ),
        )


def test_criterion_value_is_immutable() -> None:
    criterion = AcceptanceCriterion(
        acceptance_criterion_id("7", "Exact text"), "Exact text"
    )

    with pytest.raises(AttributeError):
        criterion.text = "Changed"  # type: ignore[misc]
