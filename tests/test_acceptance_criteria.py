from __future__ import annotations

import acceptance_criteria
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


def _assert_nonrunning_measurement_stages_are_rejected(validator) -> None:
    build = acceptance_criteria_from_specs(
        1, ({"text": "CSS is shipped.", "measured_against": "BUILD_OUTPUT"},)
    )
    running = acceptance_criteria_from_specs(
        1,
        ({"text": "A deep link loads.", "measured_against": "RUNNING_PRODUCT"},),
    )

    cases = (
        (build, False, True, "required_artifacts is not declared"),
        (running, True, False, "product_command is not declared"),
    )
    for criteria, build_declared, running_declared, expected in cases:
        try:
            validator(
                criteria,
                build_output_declared=build_declared,
                running_product_declared=running_declared,
            )
        except ValueError as error:
            assert expected in str(error)
        else:
            raise AssertionError("the unsupported measurement stage was accepted")


def test_product_measurements_require_their_declared_validation_stage() -> None:
    _assert_nonrunning_measurement_stages_are_rejected(validate_measurement_support)
    build = acceptance_criteria_from_specs(
        1, ({"text": "CSS is shipped.", "measured_against": "BUILD_OUTPUT"},)
    )
    running = acceptance_criteria_from_specs(
        1,
        ({"text": "A deep link loads.", "measured_against": "RUNNING_PRODUCT"},),
    )

    validate_measurement_support(
        (*build, *running),
        build_output_declared=True,
        running_product_declared=True,
    )


@pytest.mark.parametrize(
    "mutation",
    ("accept_build_without_declaration", "accept_running_without_declaration"),
)
def test_canary_33_proof_kills_measurement_support_mutations(
    mutation: str,
) -> None:
    original = acceptance_criteria.validate_measurement_support

    def mutant(criteria, *, build_output_declared, running_product_declared):
        items = tuple(criteria)
        if mutation == "accept_build_without_declaration" and any(
            item.measured_against is MeasuredAgainst.BUILD_OUTPUT for item in items
        ):
            build_output_declared = True
        if mutation == "accept_running_without_declaration" and any(
            item.measured_against is MeasuredAgainst.RUNNING_PRODUCT for item in items
        ):
            running_product_declared = True
        return original(
            items,
            build_output_declared=build_output_declared,
            running_product_declared=running_product_declared,
        )

    with pytest.raises(AssertionError):
        _assert_nonrunning_measurement_stages_are_rejected(mutant)
