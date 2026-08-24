from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


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
