"""Canonical reviewer finding identifiers shared by producers and validators."""

from __future__ import annotations

import re


FINDING_ID_PREFIX = "C-"
FINDING_ID_PATTERN_TEXT = rf"^{FINDING_ID_PREFIX}(0[1-9]|[1-9][0-9]*)$"
FINDING_ID_PATTERN = re.compile(FINDING_ID_PATTERN_TEXT)
FINDING_ID_EXAMPLE = f"{FINDING_ID_PREFIX}01"


def format_finding_id(number: int) -> str:
    """Format a positive reviewer finding number without truncating large IDs."""
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        raise ValueError("finding number must be a positive integer")
    return f"{FINDING_ID_PREFIX}{number:02d}"
