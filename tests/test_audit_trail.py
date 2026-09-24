from __future__ import annotations

from dataclasses import replace

import pytest

from audit_trail import (
    AuditTrailError, ValidationAuditEvent, managed_slice_document_path,
    semantic_audit_fingerprint, strip_managed_work_plan_audit_appendix,
)
from readable_audit import render_plan_appendix, render_slice
from semantic_markdown import SemanticMarkdownError, parse_semantic_markdown
from test_readable_audit import _facts


def test_managed_slice_document_path_is_stable_and_one_based() -> None:
    path = managed_slice_document_path("docs/internal/task-review-12345678.md", 5, "Reines Matching")
    assert path == "docs/internal/slice-task-05-reines-matching.md"
    with pytest.raises(AuditTrailError):
        managed_slice_document_path("docs/internal/task-review-12345678.md", 0, "Invalid")


def test_validation_event_rejects_invalid_identity() -> None:
    with pytest.raises(AuditTrailError):
        ValidationAuditEvent(0, 1, None)  # type: ignore[arg-type]


def test_projection_bodies_are_semantically_inert() -> None:
    first = render_slice(_facts(), 1)
    second = first.replace("Befund nur in Slice 1.", "A changed projected explanation.")
    assert semantic_audit_fingerprint(first) == semantic_audit_fingerprint(second)
    changed_author = first.replace("> Noch nicht dokumentiert.", "> An implementation change.")
    assert semantic_audit_fingerprint(first) != semantic_audit_fingerprint(changed_author)


def test_plan_appendix_can_be_removed_without_changing_authored_plan() -> None:
    authored = "# Arbeitsplan\n\nAuthored text.\n"
    complete = authored + "\n" + render_plan_appendix(_facts(plan_only=True))
    assert strip_managed_work_plan_audit_appendix(complete) == authored


def test_unknown_or_incomplete_markers_fail_closed() -> None:
    original = render_slice(_facts(), 1)
    for damaged in (
        original.replace("audit:findings:end", "audit:forged:end"),
        original.replace("<!-- audit:goal:end -->", ""),
        original.replace("<!-- audit:goal:begin -->", "<!-- audit:goal:begin -->\n<!-- audit:goal:begin -->"),
    ):
        with pytest.raises(SemanticMarkdownError):
            parse_semantic_markdown(damaged, require_managed=True)
