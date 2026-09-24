from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_runtime import create_read_only_reviewer_workspace
from agent_runtime import OrchestratorConfig
from contracts import (
    AgentRole, FindingClass, FindingOrigin, FindingRecord, FindingStatus,
    ValidationAttestation, ValidationRecord, ValidationStatus,
)
from repo_changes import collect_repository_changes
from orchestrator import ProductionWorkflowDriver
from review_packets import (
    BinaryFileMetadata, ReviewPacket, ReviewPacketError, build_review_packet,
)
from workflow import WorkflowChanges, WorkflowContext, WorkflowEngine, WorkflowHistory


PLAN = """# Plan

### Slice 1 - Assets

**Ziel**

Include asset changes in review.

**\u0041kzeptanzkriterien**

- Inspect the asset metadata.
"""


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _attestation(fingerprint: str) -> ValidationAttestation:
    return ValidationAttestation(
        "validation-asset", fingerprint, ("pytest",),
        (ValidationRecord(ValidationStatus.PASS, "pytest", 0),),
        "f" * 64, "passed",
    )


def _packet(diff: str, paths: tuple[str, ...], metadata: tuple[BinaryFileMetadata, ...],
            fingerprint: str = "a" * 64) -> ReviewPacket:
    return build_review_packet(
        purpose="slice", fingerprint=fingerprint, start_fingerprint="0" * 64,
        paths=paths, review_diff=diff, plan_text=PLAN, slice_id=1,
        attestation=_attestation(fingerprint), findings=(), binary_metadata=metadata,
    )


def _repository(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Asset Tests")
    _git(root, "config", "user.email", "assets@example.invalid")
    (root / "changed.bin").write_bytes(b"\x00older\x80")
    (root / "deleted.bin").write_bytes(b"\x00gone\xfe")
    (root / "note.txt").write_text("before\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "base")
    base = _git(root, "rev-parse", "HEAD")
    (root / "changed.bin").write_bytes(b"\x00new\xff")
    (root / "deleted.bin").unlink()
    (root / "added.bin").write_bytes(b"\x00added\x81")
    (root / "note.txt").write_text("after\n", encoding="utf-8")
    return root, base


def test_git_and_untracked_binary_changes_are_metadata_only(tmp_path: Path) -> None:
    root, base = _repository(tmp_path)
    changes = collect_repository_changes(root, base)
    workflow_changes = WorkflowChanges(
        start_commit=base, fingerprint=changes.fingerprint,
        paths=changes.paths, full_diff=changes.diff_text,
    )
    assert workflow_changes.binary_metadata == changes.binary_metadata
    packet = _packet(changes.diff_text, changes.paths, changes.binary_metadata,
                     changes.fingerprint)
    payload = json.loads(packet.canonical_bytes)
    coverage = {item["path"]: item for item in payload["manifest"]["diff_coverage"]}
    assert coverage["added.bin"]["change_type"] == "added"
    assert coverage["added.bin"]["old_size"] is None
    assert coverage["added.bin"]["new_size"] == 7
    assert coverage["added.bin"]["new_sha256"] == hashlib.sha256(b"\x00added\x81").hexdigest()
    assert coverage["changed.bin"]["change_type"] == "modified"
    assert coverage["changed.bin"]["old_size"] == len(b"\x00older\x80")
    assert coverage["changed.bin"]["old_sha256"] == hashlib.sha256(b"\x00older\x80").hexdigest()
    assert coverage["changed.bin"]["new_size"] == len(b"\x00new\xff")
    assert coverage["changed.bin"]["new_sha256"] == hashlib.sha256(b"\x00new\xff").hexdigest()
    assert coverage["deleted.bin"]["change_type"] == "deleted"
    assert coverage["deleted.bin"]["old_size"] == len(b"\x00gone\xfe")
    assert coverage["deleted.bin"]["old_sha256"] == hashlib.sha256(b"\x00gone\xfe").hexdigest()
    assert coverage["deleted.bin"]["new_size"] is None
    assert coverage["deleted.bin"]["new_sha256"] is None
    assert b"\x00" not in packet.canonical_bytes
    assert "GIT binary patch" not in packet.text
    assert "literal " not in payload["diff"]
    assert "delta " not in payload["diff"]
    assert "Binary file; size=" not in payload["diff"]
    assert ReviewPacket.restore(packet.canonical_bytes, packet.digest) == packet


def test_multiple_untracked_binaries_and_late_nul_are_covered(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Asset Tests")
    _git(root, "config", "user.email", "assets@example.invalid")
    (root / "base.txt").write_text("base\n", encoding="utf-8")
    _git(root, "add", "base.txt")
    _git(root, "commit", "-m", "base")
    base = _git(root, "rev-parse", "HEAD")
    (root / "a.ttf").write_bytes(b"A" * 256_001 + b"\x00")
    (root / "b.ttf").write_bytes(b"\x00B")
    changes = collect_repository_changes(root, base)
    packet = _packet(changes.diff_text, changes.paths, changes.binary_metadata,
                     changes.fingerprint)
    assert len(packet.manifest.diff_coverage) == 2
    assert all(item.is_binary for item in packet.manifest.diff_coverage)
    assert packet.manifest.diff_coverage[0].new_size == 256_002
    assert packet.manifest.snapshot_paths == ()
    workspace = create_read_only_reviewer_workspace(root, packet.manifest.snapshot_paths)
    try:
        assert list(workspace.root.iterdir()) == []
    finally:
        workspace.cleanup()


def test_tracked_late_nul_is_sanitized_even_when_git_renders_text(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Asset Tests")
    _git(root, "config", "user.email", "assets@example.invalid")
    old = b"A" * 9_000 + b"\x00old"
    new = b"A" * 9_000 + b"\x00new"
    (root / "late.bin").write_bytes(old)
    _git(root, "add", "late.bin")
    _git(root, "commit", "-m", "base")
    base = _git(root, "rev-parse", "HEAD")
    (root / "late.bin").write_bytes(new)
    changes = collect_repository_changes(root, base)
    assert "GIT binary patch" not in changes.diff_text
    assert "\x00" in changes.diff_text
    packet = _packet(changes.diff_text, changes.paths, changes.binary_metadata,
                     changes.fingerprint)
    entry = packet.manifest.diff_coverage[0]
    assert entry.is_binary
    assert entry.old_sha256 == hashlib.sha256(old).hexdigest()
    assert entry.new_sha256 == hashlib.sha256(new).hexdigest()
    assert "\x00" not in packet.text


def test_old_late_nul_is_metadata_only_after_replacement_with_text(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Asset Tests")
    _git(root, "config", "user.email", "assets@example.invalid")
    old = b"A" * 9_000 + b"\x00old"
    new = b"A" * 9_000 + b"new"
    (root / "late.bin").write_bytes(old)
    _git(root, "add", "late.bin")
    _git(root, "commit", "-m", "base")
    base = _git(root, "rev-parse", "HEAD")
    (root / "late.bin").write_bytes(new)

    changes = collect_repository_changes(root, base)
    assert "GIT binary patch" not in changes.diff_text
    assert "\x00" in changes.diff_text
    packet = _packet(changes.diff_text, changes.paths, changes.binary_metadata,
                     changes.fingerprint)
    entry = packet.manifest.diff_coverage[0]
    assert entry.is_binary
    assert (entry.old_size, entry.old_sha256) == (len(old), hashlib.sha256(old).hexdigest())
    assert (entry.new_size, entry.new_sha256) == (len(new), hashlib.sha256(new).hexdigest())
    assert packet.manifest.snapshot_paths == ()
    assert "\x00" not in packet.text


def test_production_collector_carries_untracked_binary_into_slice_packet(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Asset Tests")
    _git(root, "config", "user.email", "assets@example.invalid")
    (root / "base.txt").write_text("base\n", encoding="utf-8")
    _git(root, "add", "base.txt")
    _git(root, "commit", "-m", "base")
    base = _git(root, "rev-parse", "HEAD")
    content = b"\x00font-data\x81"
    (root / "font.ttf").write_bytes(content)

    driver = ProductionWorkflowDriver(
        repository_root=root,
        state_file=root / ".orchestrator" / "state.json",
        agents={},
        config=OrchestratorConfig(repo_root=root),
        allowed_roots=(root,),
    )
    changes = driver.collect_changes(base)
    packet = WorkflowEngine(driver)._build_review_dispatch_packet(
        WorkflowContext("Review asset", "Approved plan", "Asset slice",
                        approved_plan_text=PLAN),
        WorkflowHistory(1), SimpleNamespace(slice_id=1), changes,
        changes.full_diff, _attestation(changes.fingerprint), "0" * 64, "slice",
    )
    assert changes.paths == ("font.ttf",)
    assert len(changes.binary_metadata) == 1
    entry = packet.manifest.diff_coverage[0]
    assert entry.is_binary
    assert entry.new_size == len(content)
    assert entry.new_sha256 == hashlib.sha256(content).hexdigest()
    assert packet.manifest.snapshot_paths == ()


def test_text_section_and_coverage_entry_are_unchanged_by_binary_neighbor(tmp_path: Path) -> None:
    root, base = _repository(tmp_path)
    changes = collect_repository_changes(root, base)
    mixed = _packet(changes.diff_text, changes.paths, changes.binary_metadata,
                    changes.fingerprint)
    text_diff = changes.diff_text.split("diff --git a/note.txt b/note.txt\n", 1)[1].split("\ndiff --git ", 1)[0]
    text_only = _packet("diff --git a/note.txt b/note.txt\n" + text_diff,
                        ("note.txt",), (), changes.fingerprint)
    mixed_payload = json.loads(mixed.canonical_bytes)
    text_payload = json.loads(text_only.canonical_bytes)
    assert mixed_payload["diff"].split("diff --git a/note.txt b/note.txt\n", 1)[1] == text_payload["diff"].split("diff --git a/note.txt b/note.txt\n", 1)[1]
    assert mixed.manifest.diff_coverage[-1] == text_only.manifest.diff_coverage[0]
    assert mixed.manifest.snapshot_paths == ("note.txt",)
    workspace = create_read_only_reviewer_workspace(root, mixed.manifest.snapshot_paths)
    try:
        assert (workspace.root / "note.txt").read_text(encoding="utf-8") == "after\n"
        assert not (workspace.root / "added.bin").exists()
        assert not (workspace.root / "changed.bin").exists()
    finally:
        workspace.cleanup()


def test_text_only_packet_matches_pre_change_bytes_and_coverage_digest() -> None:
    diff = ("diff --git a/note.txt b/note.txt\n"
            "index 1111111..2222222 100644\n"
            "--- a/note.txt\n+++ b/note.txt\n"
            "@@ -1 +1 @@\n-before\n+after\n")
    packet = _packet(diff, ("note.txt",), ())
    assert packet.digest == "665f3d1562111de06621b351bdd33efc18986e8b681c86acde6e4c382a0da2da"
    assert packet.manifest.diff_coverage_digest == "2ff9d420df74e1563612bf3d482438322a15a82d482e5d6ab12ec6c3b4789498"


def test_missing_and_forged_untracked_binary_metadata_fail_closed() -> None:
    path = "font.ttf"
    raw = b"\x00font"
    digest = hashlib.sha256(raw).hexdigest()
    source = BinaryFileMetadata(path, "added", None, None, len(raw), digest)
    diff = (f"diff --git a/{path} b/{path}\nnew file mode 100644\n"
            f"--- /dev/null\n+++ b/{path}\n"
            f"Binary file; size={len(raw)}; sha256={digest}\n")
    assert _packet(diff, (path,), (source,)).manifest.diff_coverage[0].new_size == len(raw)
    for bad_diff, metadata in (
        (diff, ()),
        (diff.replace(f"size={len(raw)}; ", ""), (source,)),
        (diff.replace(f"; sha256={digest}", ""), (source,)),
        (diff.replace(digest, "0" * 64), (source,)),
        (diff.replace(f"size={len(raw)}", "size=999"), (source,)),
    ):
        with pytest.raises(ReviewPacketError):
            _packet(bad_diff, (path,), metadata)


def test_binary_metadata_without_diff_section_is_rejected() -> None:
    path = "font.ttf"
    raw = b"\x00font"
    digest = hashlib.sha256(raw).hexdigest()
    source = BinaryFileMetadata(path, "added", None, None, len(raw), digest)
    extra = BinaryFileMetadata("uncovered.bin", "added", None, None, 1,
                               hashlib.sha256(b"\x00").hexdigest())
    diff = (f"diff --git a/{path} b/{path}\nnew file mode 100644\n"
            f"--- /dev/null\n+++ b/{path}\n"
            f"Binary file; size={len(raw)}; sha256={digest}\n")
    with pytest.raises(ReviewPacketError, match="binary metadata does not match diff coverage"):
        _packet(diff, (path,), (source, extra))


@pytest.mark.parametrize("extra", (
    "rename from other.ttf", "copy from other.ttf",
    "new file mode 120000", "[untracked special file; sha256=" + "0" * 64 + "]",
))
def test_unsupported_binary_operations_fail_closed(extra: str) -> None:
    digest = hashlib.sha256(b"\x00").hexdigest()
    source = BinaryFileMetadata("font.ttf", "added", None, None, 1, digest)
    diff = ("diff --git a/font.ttf b/font.ttf\nnew file mode 100644\n"
            "--- /dev/null\n+++ b/font.ttf\n"
            f"{extra}\nBinary file; size=1; sha256={digest}\n")
    with pytest.raises(ReviewPacketError):
        _packet(diff, ("font.ttf",), (source,))


def test_binary_packet_rejects_restored_base85_or_raw_content() -> None:
    digest = hashlib.sha256(b"\x00").hexdigest()
    source = BinaryFileMetadata("font.ttf", "added", None, None, 1, digest)
    raw = ("diff --git a/font.ttf b/font.ttf\nnew file mode 100644\n"
           "index " + "0" * 40 + ".." + "1" * 40 + "\n"
           "GIT binary patch\nliteral 1\nAabc\n")
    packet = _packet(raw, ("font.ttf",), (source,))
    assert "Aabc" not in packet.text
    assert "GIT binary patch" not in packet.text
    tampered = json.loads(packet.canonical_bytes)
    tampered["diff"] += "GIT binary patch\nliteral 1\nAabc\n"
    altered = json.dumps(tampered, ensure_ascii=False, separators=(",", ":")).encode()
    with pytest.raises(ReviewPacketError):
        ReviewPacket.restore(altered, hashlib.sha256(altered).hexdigest())


def test_correction_packet_uses_same_binary_metadata_path() -> None:
    digest = hashlib.sha256(b"\x00").hexdigest()
    path = "font.ttf"
    diff = (f"diff --git a/{path} b/{path}\nnew file mode 100644\n"
            f"--- /dev/null\n+++ b/{path}\nBinary file; size=1; sha256={digest}\n")
    finding = FindingRecord(
        "C-01", FindingClass.FINDING, FindingStatus.OPEN,
        "Asset needs review", "Metadata is complete",
        FindingOrigin("01", 1, AgentRole.CLAUDE),
    )
    packet = build_review_packet(
        purpose="correction", fingerprint="a" * 64,
        start_fingerprint="0" * 64, paths=(path,), review_diff=diff,
        plan_text=PLAN, slice_id=1, attestation=_attestation("a" * 64),
        findings=(finding,), affected_finding_ids=(finding.finding_id,),
        binary_metadata=(BinaryFileMetadata(path, "added", None, None, 1, digest),),
    )
    assert packet.purpose == "correction"
    assert packet.manifest.snapshot_paths == ()
    assert packet.manifest.diff_coverage[0].new_sha256 == digest
