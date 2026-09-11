from finding_order import finding_id_sort_key, sorted_finding_ids


def test_finding_order_is_natural_across_two_three_and_four_digits() -> None:
    values = ("C-1000", "C-101", "C-71", "C-999", "C-62")

    assert sorted(values, key=finding_id_sort_key) == [
        "C-62",
        "C-71",
        "C-101",
        "C-999",
        "C-1000",
    ]
    assert sorted_finding_ids(values) == tuple(
        sorted(values, key=finding_id_sort_key)
    )


def test_equal_numeric_parts_have_a_deterministic_textual_tiebreaker() -> None:
    assert sorted_finding_ids(("C-1", "C-01")) == ("C-01", "C-1")
