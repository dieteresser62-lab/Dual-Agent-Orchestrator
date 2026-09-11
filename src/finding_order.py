"""Canonical natural ordering for finding identifiers."""

from __future__ import annotations

import re
from collections.abc import Iterable


_NUMERIC_SUFFIX = re.compile(r"^(.*?)([0-9]+)([^0-9]*)$")


def finding_id_sort_key(value: str) -> tuple[int, str, int, str, str]:
    """Order the numeric suffix naturally and retain a deterministic fallback."""
    match = _NUMERIC_SUFFIX.fullmatch(value)
    if match is None:
        return (1, value, 0, "", value)
    prefix, number, suffix = match.groups()
    return (0, prefix, int(number), suffix, value)


def sorted_finding_ids(values: Iterable[str]) -> tuple[str, ...]:
    """Return unique finding identifiers in their canonical natural order."""
    return tuple(sorted(set(values), key=finding_id_sort_key))


def replay_compatible_finding_ids(values: Iterable[str]) -> tuple[str, ...]:
    """Naturalize only canonical current or historical lexical record order."""
    original = tuple(values)
    natural = sorted_finding_ids(original)
    lexical = tuple(sorted(set(original)))
    return natural if original in {natural, lexical} else original
