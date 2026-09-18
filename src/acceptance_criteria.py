"""Stable, record-safe identities for planned Slice acceptance criteria."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterable, Mapping


_CRITERION_ID_RE = re.compile(r"^ac-[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class AcceptanceCriterion:
    criterion_id: str
    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.criterion_id, str) or _CRITERION_ID_RE.fullmatch(
            self.criterion_id
        ) is None:
            raise ValueError(
                "acceptance criterion id must be ac- followed by a lowercase SHA-256 digest"
            )
        if not isinstance(self.text, str) or not self.text.strip() or "\x00" in self.text:
            raise ValueError("acceptance criterion text must be non-empty and NUL-free")


def acceptance_criterion_id(slice_id: int | str, text: str) -> str:
    """Derive an order-independent identifier from exact Slice and text facts."""

    canonical_slice_id = _canonical_slice_id(slice_id)
    if not isinstance(text, str) or not text.strip() or "\x00" in text:
        raise ValueError("acceptance criterion text must be non-empty and NUL-free")
    material = json.dumps(
        [canonical_slice_id, text],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "ac-" + hashlib.sha256(material).hexdigest()


def acceptance_criteria_from_texts(
    slice_id: int | str, texts: Iterable[str]
) -> tuple[AcceptanceCriterion, ...]:
    """Preserve criterion order while deriving stable content identities."""

    result = tuple(
        AcceptanceCriterion(acceptance_criterion_id(slice_id, text), text)
        for text in texts
    )
    validate_acceptance_criteria(slice_id, result)
    return result


def acceptance_criteria_from_documents(
    slice_id: int | str, documents: Iterable[Mapping[str, Any]]
) -> tuple[AcceptanceCriterion, ...]:
    """Rehydrate closed criterion documents and verify their derived identities."""

    criteria: list[AcceptanceCriterion] = []
    for document in documents:
        if not isinstance(document, Mapping) or set(document) != {
            "criterion_id",
            "text",
        }:
            raise ValueError(
                "acceptance criterion must contain exactly criterion_id and text"
            )
        criteria.append(
            AcceptanceCriterion(document["criterion_id"], document["text"])
        )
    result = tuple(criteria)
    validate_acceptance_criteria(slice_id, result)
    return result


def validate_acceptance_criteria(
    slice_id: int | str, criteria: tuple[AcceptanceCriterion, ...]
) -> None:
    """Require typed, unique criteria whose ids match their exact record facts."""

    if not isinstance(criteria, tuple) or any(
        not isinstance(item, AcceptanceCriterion) for item in criteria
    ):
        raise ValueError("acceptance criteria must be a tuple of typed criteria")
    criterion_ids = tuple(item.criterion_id for item in criteria)
    texts = tuple(item.text for item in criteria)
    if len(criterion_ids) != len(set(criterion_ids)) or len(texts) != len(set(texts)):
        raise ValueError("acceptance criteria must be unique within their Slice")
    for criterion in criteria:
        expected = acceptance_criterion_id(slice_id, criterion.text)
        if criterion.criterion_id != expected:
            raise ValueError(
                "acceptance criterion id does not match its Slice and exact text"
            )


def _canonical_slice_id(slice_id: int | str) -> str:
    if isinstance(slice_id, bool) or not isinstance(slice_id, (int, str)):
        raise ValueError("acceptance criterion slice id must be an integer or string")
    value = str(slice_id)
    if not value or value != value.strip():
        raise ValueError("acceptance criterion slice id must be canonical and non-empty")
    return value


__all__ = [
    "AcceptanceCriterion",
    "acceptance_criteria_from_documents",
    "acceptance_criteria_from_texts",
    "acceptance_criterion_id",
    "validate_acceptance_criteria",
]
