from finding_order import finding_id_sort_key, sorted_finding_ids


def test_finding_order_is_natural_across_two_three_and_four_digits() -> None:
    values = ("R-1000", "R-101", "R-71", "R-999", "R-62")

    assert sorted(values, key=finding_id_sort_key) == [
        "R-62",
        "R-71",
        "R-101",
        "R-999",
        "R-1000",
    ]
    assert sorted_finding_ids(values) == tuple(
        sorted(values, key=finding_id_sort_key)
    )


def test_equal_numeric_parts_have_a_deterministic_textual_tiebreaker() -> None:
    assert sorted_finding_ids(("R-1", "R-01")) == ("R-01", "R-1")
