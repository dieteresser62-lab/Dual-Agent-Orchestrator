from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

import pytest

from artifact_models import WorkUnitPayload
from scripts import render_readable_audit
from test_readable_audit import _facts


def test_renderer_keeps_overall_when_slice_report_is_not_in_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    output = tmp_path / "rendered"
    facts = _facts(slices=1)
    facts.plan = None
    facts.records = tuple(
        replace(record, payload=WorkUnitPayload("1", 1, ("src/one.py",)))
        if isinstance(record.payload, WorkUnitPayload) else record
        for record in facts.records
    )
    monkeypatch.setattr(render_readable_audit, "_load_read_only", lambda *_args: (facts, object()))
    monkeypatch.setattr(sys, "argv", [
        "render_readable_audit.py", "--repository", str(repository),
        "--run-id", "readable-test-run", "--output", str(output),
    ])

    assert render_readable_audit.main() == 0
    overall = (output / "docs/internal/overall.md").read_text(encoding="utf-8")
    assert "### Slice 1\n\nkein Slice-Bericht im Scope." in overall  # allowlist:german
    assert not list(output.glob("docs/internal/slice-*.md"))
