from __future__ import annotations

from dataclasses import replace
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from contracts import FindingStatus
from native_review_contract import native_review_provider_response_schema


ROOT = Path(__file__).resolve().parents[1]


def _probe_module():
    path = ROOT / "scripts" / "native_contract_probe.py"
    spec = importlib.util.spec_from_file_location("native_contract_probe", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_protected_repository_fingerprint_observes_gitignored_workflow_stores(
    tmp_path: Path,
) -> None:
    probe = _probe_module()
    for relative in probe.PROTECTED_REPOSITORY_PATHS:
        (tmp_path / relative).mkdir()
    state = tmp_path / ".orchestrator" / "state.json"
    state.write_text('{"status":"before"}', encoding="utf-8")
    inbox_task = tmp_path / "inbox" / "task.md"
    inbox_task.write_text("before", encoding="utf-8")

    before = probe._protected_repository_fingerprint(tmp_path)
    state.write_text('{"status":"after"}', encoding="utf-8")
    assert probe._protected_repository_fingerprint(tmp_path) != before

    state.write_text('{"status":"before"}', encoding="utf-8")
    restored = probe._protected_repository_fingerprint(tmp_path)
    (tmp_path / "outbox" / "result.md").write_text("new", encoding="utf-8")
    assert probe._protected_repository_fingerprint(tmp_path) != restored

    (tmp_path / "outbox" / "result.md").unlink()
    restored = probe._protected_repository_fingerprint(tmp_path)
    inbox_task.rename(tmp_path / "outbox" / "task.md")
    assert probe._protected_repository_fingerprint(tmp_path) != restored


def test_live_canary_main_fails_when_a_protected_store_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = _probe_module()
    for relative in probe.PROTECTED_REPOSITORY_PATHS:
        (tmp_path / relative).mkdir()
    monkeypatch.setattr(probe, "_repository_status", lambda _root: "unchanged")

    def mutating_canary(_provider: str, _form: str, *, repo_root: Path):
        (repo_root / ".orchestrator" / "state.json").write_text(
            "changed", encoding="utf-8"
        )
        return {}

    monkeypatch.setattr(probe, "_live_canary", mutating_canary)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "native_contract_probe.py",
            "--provider",
            "codex",
            "--canary-form",
            "plan",
            "--repo-root",
            str(tmp_path),
        ],
    )
    with pytest.raises(RuntimeError, match="protected workflow store"):
        probe.main()


def test_convergence_canary_serializes_status_change_items_one_of() -> None:
    probe = _probe_module()
    bundle = probe._claude_canary_bundle("convergence", repo_root=ROOT)
    schema_text = bundle.provider_response_schema_json
    schema = json.loads(schema_text)
    request = json.loads(bundle.canonical_json)
    manifest = json.loads(
        (
            ROOT
            / "tests"
            / "fixtures"
            / "native-contract-corpus"
            / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    evidence_row = next(
        item
        for item in manifest["writer_forms"]
        if item["provider"] == "claude" and item["writer_form"] == "convergence"
    )
    approved = schema["$defs"]["bound_slice_convergence_approved"]
    status_changes = approved["properties"]["status_changes"]

    schema_digest = hashlib.sha256(schema_text.encode("utf-8")).hexdigest()
    assert request["response_contract"]["schema_sha256"] == schema_digest
    assert evidence_row["writer_schema_sha256"] == schema_digest
    assert evidence_row["evidence"]["kind"] == "live_canary"
    assert evidence_row["evidence"]["status"] == "passed"
    assert evidence_row["evidence"]["request_id"] == bundle.bound_context.request_id
    assert len(evidence_row["evidence"]["response_sha256"]) == 64
    assert status_changes["minItems"] == 1
    assert status_changes["maxItems"] == 1
    options = status_changes["items"]["oneOf"]
    assert [item["properties"]["finding_id"] for item in options] == [
        {"type": "string", "const": "C-01"}
    ]
    assert [item["properties"]["status"] for item in options] == [
        {"type": "string", "const": "CLOSED"}
    ]

    prior = bundle.bound_context.context.previous_findings[0]
    no_open_context = replace(
        bundle.bound_context.context,
        previous_findings=(
            replace(
                prior,
                status=FindingStatus.CLOSED,
                status_rationale="Resolved before this round.",
            ),
        ),
    )
    no_open_schema = native_review_provider_response_schema(no_open_context)
    no_open_status = no_open_schema["$defs"][
        "bound_slice_convergence_approved"
    ]["properties"]["status_changes"]
    assert no_open_status["maxItems"] == 0
    assert "oneOf" not in no_open_status["items"]
