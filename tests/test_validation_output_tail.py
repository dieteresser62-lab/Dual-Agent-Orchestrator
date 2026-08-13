from __future__ import annotations

from validation_matrix import _compact_output


def test_compact_validation_output_preserves_failure_tail() -> None:
    output = _compact_output(
        "BEGIN-OF-SUITE\n" + ("progress\n" * 100) + "FINAL-FAILURE-SUMMARY",
        "",
        80,
    )

    assert output.startswith("BEGIN-OF-SUITE")
    assert "characters omitted" in output
    assert output.endswith("FINAL-FAILURE-SUMMARY")
