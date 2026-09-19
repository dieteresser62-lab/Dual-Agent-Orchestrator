from __future__ import annotations

import pytest

from acceptance_criteria import (
    AcceptanceCriterion,
    MeasuredAgainst,
    acceptance_criteria_from_documents,
    acceptance_criteria_from_specs,
    acceptance_criteria_from_texts,
    acceptance_criterion_id,
    validate_measurement_support,
)


def test_criterion_identity_is_stable_when_order_changes() -> None:
    original = acceptance_criteria_from_texts(
        7,
        ("The route is replay-stable.", "The regression test passes."),
        measured_against=MeasuredAgainst.SOURCE,
    )
    reordered = acceptance_criteria_from_texts(
        7,
        ("The regression test passes.", "The route is replay-stable."),
        measured_against=MeasuredAgainst.SOURCE,
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
        acceptance_criteria_from_texts(
            7,
            ("Same condition", "Same condition"),
            measured_against=MeasuredAgainst.SOURCE,
        )

    with pytest.raises(ValueError, match="does not match"):
        acceptance_criteria_from_documents(
            7,
            (
                {
                    "criterion_id": acceptance_criterion_id(8, "Exact text"),
                    "text": "Exact text",
                    "measured_against": "SOURCE",
                },
            ),
        )


def test_criterion_value_is_immutable() -> None:
    criterion = AcceptanceCriterion(
        acceptance_criterion_id("7", "Exact text"),
        "Exact text",
        MeasuredAgainst.SOURCE,
    )

    with pytest.raises(AttributeError):
        criterion.text = "Changed"  # type: ignore[misc]


def test_measurement_basis_is_mandatory_typed_and_lossless() -> None:
    with pytest.raises(ValueError, match="exactly text and measured_against"):
        acceptance_criteria_from_specs(1, ({"text": "Missing basis"},))

    criteria = acceptance_criteria_from_specs(
        1,
        tuple(
            {"text": target, "measured_against": target}
            for target in ("SOURCE", "BUILD_OUTPUT", "RUNNING_PRODUCT")
        ),
    )
    documents = tuple(
        {
            "criterion_id": item.criterion_id,
            "text": item.text,
            "measured_against": item.measured_against.value,
        }
        for item in criteria
    )

    assert acceptance_criteria_from_documents(1, documents) == criteria


def test_product_measurements_require_their_declared_validation_stage() -> None:
    build = acceptance_criteria_from_specs(
        1, ({"text": "CSS is shipped.", "measured_against": "BUILD_OUTPUT"},)
    )
    running = acceptance_criteria_from_specs(
        1,
        ({"text": "A deep link loads.", "measured_against": "RUNNING_PRODUCT"},),
    )

    with pytest.raises(ValueError, match="required_artifacts is not declared"):
        validate_measurement_support(
            build,
            build_output_declared=False,
            running_product_declared=True,
        )
    with pytest.raises(ValueError, match="product_command is not declared"):
        validate_measurement_support(
            running,
            build_output_declared=True,
            running_product_declared=False,
        )
    validate_measurement_support(
        (*build, *running),
        build_output_declared=True,
        running_product_declared=True,
    )
