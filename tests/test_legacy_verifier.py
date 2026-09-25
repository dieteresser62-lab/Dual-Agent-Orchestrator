from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Callable

import pytest

import legacy_verifier
from artifact_models import (
    ArtifactRecord,
    Fingerprint,
    FingerprintKind,
    RoleProfilePayload,
    RunIdentityPayload,
    RunProfilePayload,
    TaskPayload,
    WorkflowEventPayload,
    WorkflowTransitionPayload,
    canonical_json,
)


ROOT = Path(__file__).resolve().parents[1]
RUN_8_HANDOFF = ROOT / "tests/fixtures/run-8-finding-handoff-import-v1.json"
RUN_8_PROJECTION = ROOT / "tests/fixtures/run-8-state-projection-v1.json"
FINGERPRINT = Fingerprint(FingerprintKind.CONTRACT, "a" * 64)


def _legacy_run_profile() -> RunProfilePayload:
    """Build a verifier fixture without asking the current reducer to accept it."""

    payload = object.__new__(RunProfilePayload)
    object.__setattr__(
        payload, "implementer", RoleProfilePayload("implementer-model", "medium")
    )
    object.__setattr__(
        payload, "reviewer", RoleProfilePayload("reviewer-model", "high")
    )
    object.__setattr__(payload, "orchestrator_code_version", "0" * 64)
    object.__setattr__(payload, "reducer_version", legacy_verifier.LEGACY_REDUCER_VERSION)
    object.__setattr__(payload, "merge_completed_branch", False)
    object.__setattr__(payload, "base_branch", None)
    return payload


def _append(
    records: list[ArtifactRecord],
    logical_id: str,
    payload: object,
    *,
    revision: int = 1,
) -> ArtifactRecord:
    create_payload = payload
    if isinstance(payload, RunProfilePayload):
        create_payload = RunProfilePayload(payload.implementer, payload.reviewer)
    record = ArtifactRecord.create(
        run_id="legacy-fixture-run",
        logical_id=logical_id,
        revision=revision,
        fingerprint=FINGERPRINT,
        predecessor_ids=((records[-1].record_id,) if records else ()),
        created_at=f"2026-09-19T08:00:{len(records):02d}+00:00",
        idempotency_key=f"legacy-fixture:{logical_id}:{revision}",
        payload=create_payload,  # type: ignore[arg-type]
    )
    if create_payload is not payload:
        object.__setattr__(record, "payload", payload)
    records.append(record)
    return record


def _minimal_projectable_chain() -> tuple[ArtifactRecord, ...]:
    records: list[ArtifactRecord] = []
    identity = _append(
        records,
        "run-identity",
        RunIdentityPayload(
            "inbox/backlog/legacy-fixture.md",
            "feature/legacy-fixture",
            "b" * 40,
            "b" * 40,
            "IMPLEMENT",
            None,
        ),
    )
    _append(
        records,
        f"workflow-event-{identity.record_id}",
        WorkflowEventPayload("run", None, "1", None, (identity.record_id,)),
    )
    _append(
        records,
        "run-profile",
        _legacy_run_profile(),
    )
    _append(
        records,
        "task-contract",
        TaskPayload("feature/legacy-fixture", ("src/example.py",), "c" * 64),
    )
    transition = _append(
        records,
        "workflow-transition",
        WorkflowTransitionPayload(
            "1", "in_progress", "1", "codex_plan", "in_progress"
        ),
    )
    _append(
        records,
        f"workflow-event-{transition.record_id}",
        WorkflowEventPayload(
            "transition", "1", "1", None, (transition.record_id,)
        ),
    )
    return tuple(records)


def _write_chain(run_directory: Path) -> tuple[ArtifactRecord, ...]:
    records = _minimal_projectable_chain()
    records_directory = run_directory / "records"
    records_directory.mkdir(parents=True)
    for record in records:
        document = record.to_dict()
        envelope = {
            "content_sha256": hashlib.sha256(canonical_json(document)).hexdigest(),
            "record": document,
        }
        (records_directory / f"{record.record_id}.json").write_bytes(
            canonical_json(envelope)
        )
    return records


def _tree_digest(root: Path) -> tuple[tuple[str, str, int, str], ...]:
    entries = []
    for path in (root, *sorted(root.rglob("*"))):
        relative = path.relative_to(root).as_posix() or "."
        metadata = path.lstat()
        if path.is_symlink():
            entries.append((relative, "symlink", metadata.st_size, os.readlink(path)))
        elif path.is_dir():
            entries.append((relative, "directory", 0, ""))
        else:
            entries.append(
                (
                    relative,
                    "file",
                    metadata.st_size,
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            )
    return tuple(entries)


def _rewrite_envelope(
    path: Path,
    transform: Callable[[dict[str, object]], None],
    *,
    refresh_digest: bool,
) -> None:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    transform(envelope["record"])
    if refresh_digest:
        envelope["content_sha256"] = hashlib.sha256(
            canonical_json(envelope["record"])
        ).hexdigest()
    path.write_bytes(canonical_json(envelope))


def test_verifier_projects_reproducibly_and_opens_files_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_directory = tmp_path / "run"
    records = _write_chain(run_directory)
    before = _tree_digest(run_directory)
    real_open = os.open
    observed_flags: list[int] = []

    def read_only_open(path, flags, *args, **kwargs):  # type: ignore[no-untyped-def]
        observed_flags.append(flags)
        assert flags & legacy_verifier._WRITE_FLAGS == 0
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(legacy_verifier.os, "open", read_only_open)
    first = legacy_verifier.verify_legacy_chain(run_directory)
    second = legacy_verifier.verify_legacy_chain(run_directory)

    assert first == second
    assert json.loads(first.canonical_projection)["run_id"] == "legacy-fixture-run"
    assert first.projection_sha256 == hashlib.sha256(
        first.canonical_projection
    ).hexdigest()
    assert observed_flags
    assert _tree_digest(run_directory) == before


def test_foreign_reducer_is_rejected_before_typed_interpretation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_directory = tmp_path / "run"
    records = _write_chain(run_directory)
    profile = next(record for record in records if record.record_type.value == "run_profile")
    profile_path = run_directory / "records" / f"{profile.record_id}.json"

    def replace_version(document: dict[str, object]) -> None:
        payload = document["payload"]
        assert isinstance(payload, dict)
        payload["reducer_version"] = "structured-v2-schema-2-state-v3-v999"

    _rewrite_envelope(profile_path, replace_version, refresh_digest=True)

    def forbidden_interpretation():
        raise AssertionError("foreign chain reached the typed reducer")

    monkeypatch.setattr(legacy_verifier, "_load_runtime", forbidden_interpretation)
    with pytest.raises(
        legacy_verifier.LegacyProtocolError,
        match=r"UNSUPPORTED-PROTOCOL.*v999.*not interpreted",
    ):
        legacy_verifier.verify_legacy_chain(run_directory)


def test_corrupt_chain_names_the_exact_record_file(tmp_path: Path) -> None:
    run_directory = tmp_path / "run"
    records = _write_chain(run_directory)
    target = run_directory / "records" / f"{records[-1].record_id}.json"

    def corrupt(document: dict[str, object]) -> None:
        document["status"] = "corrupt"

    _rewrite_envelope(target, corrupt, refresh_digest=False)
    with pytest.raises(
        legacy_verifier.LegacyCorruptionError,
        match=rf"content digest mismatch.*{re.escape(target.name)}",
    ):
        legacy_verifier.verify_legacy_chain(run_directory)


def test_non_verification_operations_fail_before_any_chain_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_verification(_path: object) -> object:
        raise AssertionError("forbidden operation reached verification")

    monkeypatch.setattr(
        legacy_verifier,
        "verify_legacy_chain",
        forbidden_verification,
    )
    for operation in ("resume", "dispatch", "provider"):
        with pytest.raises(
            legacy_verifier.LegacyCapabilityError,
            match="providers do not exist",
        ):
            legacy_verifier.run_legacy_operation(operation, tmp_path / "absent")


def test_no_production_module_imports_the_legacy_verifier() -> None:
    importers = []
    for path in sorted((ROOT / "src").glob("*.py")):
        if path.name == "legacy_verifier.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                alias.name == "legacy_verifier" for alias in node.names
            ):
                importers.append(path.name)
            if isinstance(node, ast.ImportFrom) and node.module == "legacy_verifier":
                importers.append(path.name)
    assert importers == []


def test_run_8_digests_are_recomputed_without_following_cookbook_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_read = legacy_verifier._read_regular_file

    def no_neighbor_access(path: Path, label: str) -> bytes:
        assert "Cookbook" not in os.fspath(path)
        return original_read(path, label)

    monkeypatch.setattr(legacy_verifier, "_read_regular_file", no_neighbor_access)
    result = legacy_verifier.verify_run_8_fixtures(
        RUN_8_HANDOFF,
        RUN_8_PROJECTION,
    )

    assert result.projection_sha256 == (
        "ac659a29af3cedda9153d18f4b0feb9d6f66e408b11db888a91ac793432a4bc1"
    )
    assert result.finding_transitions_sha256 == (
        "580371cb96592b7c6dca3a255dd21a88871eaaa7c544e660966978a356ed67d8"
    )


def test_legacy_artifact_is_hash_bound_and_contains_no_dispatch_module() -> None:
    manifest = legacy_verifier._load_manifest()
    sources = legacy_verifier._read_archived_sources(manifest)

    assert manifest["source_commit"] == legacy_verifier.LEGACY_SOURCE_COMMIT
    assert manifest["artifact_sha256"] == legacy_verifier.LEGACY_ARTIFACT_SHA256
    assert "src/orchestrator.py" not in sources
    assert "src/agent_runtime.py" not in sources
    assert "src/workflow_production.py" not in sources
