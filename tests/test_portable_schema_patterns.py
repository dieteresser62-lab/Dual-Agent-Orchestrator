"""The v3 contract admits only checked portable patterns in every writer."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from artifact_models import _SCHEMA_PATH as ARTIFACT_SCHEMA_PATH
from contracts import AgentRole, ApprovalMarker, ImplementerStepContract, ReadinessMarker
from native_implementer_contract import (
    NativeImplementerContext,
    NativeImplementerRequestKind,
    SCHEMA_PATH as IMPLEMENTER_RESULT_SCHEMA_PATH,
    native_implementer_provider_response_schema,
)
from native_implementer_request import REQUEST_SCHEMA_PATH as IMPLEMENTER_REQUEST_SCHEMA_PATH
from native_review_contract import (
    NativeReviewContext,
    SCHEMA_PATH as REVIEW_RESULT_SCHEMA_PATH,
    native_review_provider_response_schema,
)
from native_review_request import REQUEST_SCHEMA_PATH as REVIEW_REQUEST_SCHEMA_PATH
from native_provider_schema import ANTHROPIC_PROVIDER, OPENAI_PROVIDER
from path_policy import is_canonical_repository_relative_path
from schema_patterns import (
    ANY_LINE_VISIBLE_PATTERN,
    FIRST_LINE_VISIBLE_PATTERN,
    has_visible_text,
    matches_portable_pattern,
    schema_pattern_violations,
)
from schema_validation import SchemaDefinitionError, check_schema


SCHEMAS = (
    IMPLEMENTER_REQUEST_SCHEMA_PATH,
    IMPLEMENTER_RESULT_SCHEMA_PATH,
    REVIEW_REQUEST_SCHEMA_PATH,
    REVIEW_RESULT_SCHEMA_PATH,
    ARTIFACT_SCHEMA_PATH,
)


def _writer_documents() -> dict[str, dict]:
    documents: dict[str, dict] = {}
    for profile in (OPENAI_PROVIDER, ANTHROPIC_PROVIDER):
        for kind in NativeImplementerRequestKind:
            readiness = (
                ReadinessMarker.PLAN if kind is NativeImplementerRequestKind.PLAN
                else ReadinessMarker.IMPLEMENTATION
            )
            context = NativeImplementerContext(
                "inventory", "1", f"implementer_{kind.value}", "a" * 64, kind,
                ImplementerStepContract(
                    name="inventory", readiness_marker=readiness, slice_id="01",
                    round_number=1, require_slice_plan=kind is NativeImplementerRequestKind.PLAN,
                    require_test_files_record=kind is not NativeImplementerRequestKind.PLAN,
                ),
            )
            documents[f"implementer:{profile}:{kind.value}"] = (
                native_implementer_provider_response_schema(context, profile)
            )
        for marker, operation, slice_id, round_number in (
            (ApprovalMarker.PLAN, "reviewer_plan_review", "PLAN", 1),
            (ApprovalMarker.SLICE, "reviewer_slice_review", "01", 1),
            (ApprovalMarker.SLICE, "reviewer_slice_review", "01", 2),
            (ApprovalMarker.FINAL_REVIEW, "reviewer_final_review", "FINAL", 1),
        ):
            context = NativeReviewContext(
                "inventory", "1", operation, "a" * 64, AgentRole.REVIEWER,
                marker, slice_id, round_number,
            )
            documents[f"reviewer:{profile}:{marker.value}:round-{round_number}"] = (
                native_review_provider_response_schema(context, profile)
            )
    return documents


def test_every_active_v3_schema_and_every_writer_profile_uses_portable_patterns() -> None:
    assert len(SCHEMAS) == 5
    documents = {path.name: json.loads(path.read_text(encoding="utf-8")) for path in SCHEMAS}
    documents.update(_writer_documents())
    assert len(documents) == 19
    assert all(path.name.endswith("v3.schema.json") for path in SCHEMAS)
    assert not list(Path(__file__).resolve().parents[1].joinpath("schemas").glob("*v2.schema.json"))
    for name, document in documents.items():
        assert schema_pattern_violations(document) == (), name
        check_schema(document, location=name)


def test_schema_loader_rejects_nonportable_pattern_but_skips_const_enum_data() -> None:
    schema = {
        "type": "string", "pattern": r"^good$",
        "const": {"pattern": r"(?=data)"},
        "enum": [{"pattern": r"\s"}],
    }
    assert schema_pattern_violations(schema) == ()
    check_schema(schema)
    for bad in (r"^(?=x)x$", r"^\s+$", r"^x.$", r"^x{1001}$", r"^x\u0000$"):
        with pytest.raises(SchemaDefinitionError, match="non-portable pattern"):
            check_schema({"type": "string", "pattern": bad})


@pytest.mark.parametrize("value", ("\na", "\r\na", "a\x00", "", "\u00a0", "\u3000", "\ufeff", "\u0085"))
def test_nonblank_text_compensation_rejects_legacy_counterprobes(value: str) -> None:
    assert not (matches_portable_pattern(FIRST_LINE_VISIBLE_PATTERN, value) and has_visible_text(value, first_line=True))
    if value in {"\u00a0", "\u3000", "\ufeff", "\u0085", ""}:
        assert not has_visible_text(value)
    if value == "a\x00":
        assert not matches_portable_pattern(ANY_LINE_VISIBLE_PATTERN, value)


def test_v2_ecmascript_nonblank_rule_and_v3_local_check_agree_on_boundary_values() -> None:
    # The v2 rule was ^(?=.*\S)[^\u0000]{1,3000}$ plus a local strip/NUL check.
    # ECMAScript treats FEFF as whitespace and dot stops at every JS line terminator.
    def legacy_ecma_accepts(value: str) -> bool:
        first_line = re.split("[\r\n\u2028\u2029]", value, maxsplit=1)[0]
        js_nonspace = any(
            not (char.isspace() and char != "\u0085") and char != "\ufeff"
            for char in first_line
        )
        return js_nonspace and bool(value.strip()) and "\x00" not in value and 1 <= len(value) <= 3000

    portable = FIRST_LINE_VISIBLE_PATTERN
    probes = (
        "Text", " Text ", "Text\n", "Text\r\n", "\nText", "\r\nText",
        "", " ", "\u00a0", "\u3000", "\ufeff", "\u0085", "Text\x00",
        "x" * 3000, "x" * 3001,
    )
    for value in probes:
        current = (
            len(value) <= 3000
            and matches_portable_pattern(portable, value)
            and has_visible_text(value, first_line=True)
        )
        assert current == legacy_ecma_accepts(value), repr(value[:32])


def test_portable_path_plus_local_check_preserves_legacy_safe_path_decisions() -> None:
    old = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[^\x00\r\n\\]{1,1000}$")
    new = r"^[^/\x00\x0A\x0D\\][^\x00\x0A\x0D\\]*$"
    valid = ("src/a.py", "docs/überblick.md", "a-b/c_d", "a.b")
    invalid = ("", " ", "\u00a0", "\u3000", "\ufeff", "\u0085", "/a", "../a", "a/../b", "a\x00b", "a\nb", "a\r\nb", "a\\b")
    for value in valid:
        assert old.fullmatch(value) is not None
        assert matches_portable_pattern(new, value)
        assert is_canonical_repository_relative_path(value)
    for value in invalid:
        if value not in {" ", "\u00a0", "\u3000", "\ufeff", "\u0085"}:
            assert old.fullmatch(value) is None
        assert not (matches_portable_pattern(new, value) and is_canonical_repository_relative_path(value))
