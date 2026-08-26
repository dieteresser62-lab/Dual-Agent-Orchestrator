from __future__ import annotations

import json

import pytest

from native_codex_contract import NativeCodexContractError, validate_native_codex_document
from native_review_contract import NativeReviewContractError, validate_native_review_document


@pytest.mark.parametrize(
    "validator,error",
    (
        (validate_native_codex_document, NativeCodexContractError),
        (validate_native_review_document, NativeReviewContractError),
    ),
)
def test_agent_result_decoders_reject_marker_text(validator, error) -> None:
    with pytest.raises(error):
        validator(json.loads('{"response":"STATUS: DONE"}'))


def test_invalid_json_never_reaches_a_result_decoder() -> None:
    with pytest.raises(json.JSONDecodeError):
        json.loads("REVIEWER: claude")
