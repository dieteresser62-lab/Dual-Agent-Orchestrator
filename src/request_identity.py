"""Encode and recover physical request identity without new record payloads."""

from __future__ import annotations

import re
from typing import Iterable

from artifact_models import ArtifactRecord, WorkflowPolicyPayload


_POLICY_IDENTITY_SUFFIX = re.compile(
    r":round:(?P<round>[1-9][0-9]*):"
    r"request-sequence:(?P<sequence>[1-9][0-9]*)$"
)
_RECOMPOSED_REQUEST_SUFFIX = re.compile(
    r":recomposed-request:(?P<sequence>[1-9][0-9]*):"
    r"round:(?P<round>[1-9][0-9]*)$"
)
_LEGACY_RECOMPOSED_ROUND_SUFFIX = re.compile(
    r":recomposed-round:(?P<round>[1-9][0-9]*)$"
)


def workflow_policy_counter_candidates(
    records: Iterable[ArtifactRecord], work_unit_id: str
) -> tuple[tuple[int, int], ...]:
    """Return durable ``(semantic round, request sequence)`` observations."""

    candidates: list[tuple[int, int]] = []
    for record in records:
        payload = record.payload
        if not isinstance(payload, WorkflowPolicyPayload) or payload.work_unit_id != work_unit_id:
            continue
        match = _POLICY_IDENTITY_SUFFIX.search(record.idempotency_key)
        if match is None:
            match = _RECOMPOSED_REQUEST_SUFFIX.search(record.idempotency_key)
        if match is not None:
            candidates.append((int(match.group("round")), int(match.group("sequence"))))
            continue
        legacy = _LEGACY_RECOMPOSED_ROUND_SUFFIX.search(record.idempotency_key)
        if legacy is not None:
            conflated = int(legacy.group("round"))
            candidates.append((conflated, conflated))
    return tuple(candidates)

