from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from artifact_models import (
    ArtifactRecord, Fingerprint, FingerprintKind, RecordType, TaskPayload,
    WorkUnitPayload, WorkflowCompletionPayload, canonical_json,
)
from artifact_store import (
    ArtifactConflictError, ArtifactCorruptionError, ArtifactStore, ArtifactStoreError,
)


DIGEST = "a" * 64


def make_record(
    logical_id: str,
    payload=None,  # type: ignore[no-untyped-def]
    *,
    revision: int = 1,
    predecessors: tuple[str, ...] = (),
    idempotency_key: str | None = None,
    created_at: str = "2026-08-18T10:00:00+00:00",
) -> ArtifactRecord:
    payload = payload or WorkUnitPayload("1", revision, ("src/a.py",))
    return ArtifactRecord.create(
        run_id="run-1", logical_id=logical_id, revision=revision,
        fingerprint=Fingerprint(FingerprintKind.IMPLEMENTATION, DIGEST),
        predecessor_ids=predecessors, created_at=created_at,
        idempotency_key=idempotency_key or f"effect-{logical_id}-{revision}",
        payload=payload,
    )


def write_envelope(path: Path, record: ArtifactRecord) -> None:
    document = record.to_dict()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json({
        "content_sha256": hashlib.sha256(canonical_json(document)).hexdigest(),
        "record": document,
    }) + b"\n")


def test_put_is_append_only_idempotent_and_chain_ordered(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path, "run-1")
    first = make_record("one", idempotency_key="effect-one")
    persisted = store.put(first)
    retry = replace(first, created_at="2026-08-18T11:00:00+00:00")
    second = make_record("two", predecessors=(first.record_id,))

    assert store.put(retry) == persisted
    assert store.put(second) == second
    assert store.load_chain() == (first, second)
    assert len(tuple(store.records_dir.glob("*.json"))) == 2
    assert store.head == second


def test_idempotency_conflict_and_wrong_head_fail_closed(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path, "run-1")
    first = store.put(make_record("one", idempotency_key="effect"))
    conflict = make_record(
        "other", TaskPayload("feature/x", ("src/a.py",), DIGEST),
        idempotency_key="effect",
    )
    with pytest.raises(ArtifactConflictError, match="conflicting content"):
        store.put(conflict)
    with pytest.raises(ArtifactConflictError, match="current head"):
        store.put(make_record("two"))
    assert store.load_chain() == (first,)


def test_scan_rejects_digest_tampering_missing_predecessor_and_unknown_schema(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path, "run-1")
    record = store.put(make_record("one"))
    path = store.records_dir / f"{record.record_id}.json"
    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["record"]["logical_id"] = "tampered"
    path.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(ArtifactCorruptionError, match="digest mismatch"):
        store.load_chain()

    path.unlink()
    gap = make_record("gap", predecessors=("ar1-" + "f" * 64,))
    write_envelope(store.records_dir / f"{gap.record_id}.json", gap)
    with pytest.raises(ArtifactCorruptionError, match="missing predecessor"):
        store.load_chain()

    path = store.records_dir / f"{gap.record_id}.json"
    raw = gap.to_dict()
    raw["schema_version"] = "99"
    path.write_bytes(canonical_json({
        "content_sha256": hashlib.sha256(canonical_json(raw)).hexdigest(),
        "record": raw,
    }))
    with pytest.raises(ArtifactCorruptionError, match="schema_version"):
        store.load_chain()


def test_scan_ignores_temporary_files_and_reconstructs_stale_head(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path, "run-1")
    record = store.put(make_record("one"))
    (store.records_dir / ".interrupted.json.123.tmp").write_text("partial", encoding="utf-8")
    store.head_path.write_text('{"head_record_id":"stale"}', encoding="utf-8")

    assert store.load_chain() == (record,)
    head = json.loads(store.head_path.read_text(encoding="utf-8"))
    assert head["head_record_id"] == record.record_id
    assert head["record_count"] == 1


def test_expected_cache_progress_after_own_append_is_not_warned(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    store = ArtifactStore(tmp_path, "run-1")
    first = store.put(make_record("one"))
    caplog.clear()

    store.put(make_record("two", predecessors=(first.record_id,)))

    assert "Discarding stale artifact head cache" not in caplog.text


def test_missing_cache_is_silent_but_malformed_cache_warns_and_is_rebuilt(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    store = ArtifactStore(tmp_path, "run-1")
    record = store.put(make_record("one"))
    store.head_path.unlink()

    assert store.load_chain() == (record,)
    assert "Discarding stale artifact head cache" not in caplog.text

    store.head_path.write_text("not-json", encoding="utf-8")
    caplog.clear()
    assert store.load_chain() == (record,)
    assert caplog.text.count("Discarding stale artifact head cache") == 1
    assert json.loads(store.head_path.read_text(encoding="utf-8"))["head_record_id"] == record.record_id


def test_crash_before_publication_leaves_no_record_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = ArtifactStore(tmp_path, "run-1")

    def fail_publish(source: Path, target: Path) -> None:
        raise OSError("simulated publication failure")

    monkeypatch.setattr("artifact_store.os.replace", fail_publish)
    with pytest.raises(OSError, match="publication failure"):
        store.put(make_record("one"))

    assert tuple(store.records_dir.iterdir()) == ()


def test_crash_after_publication_is_recovered_by_scan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = ArtifactStore(tmp_path, "run-1")
    record = make_record("one")

    def fail_cache(index, prior_document) -> None:  # type: ignore[no-untyped-def]
        raise OSError("simulated cache failure")

    monkeypatch.setattr(store, "_refresh_append_head_cache", fail_cache)
    with pytest.raises(OSError, match="cache failure"):
        store.put(record)

    recovered = ArtifactStore(tmp_path, "run-1")
    assert recovered.load_chain() == (record,)
    assert recovered.head == record


def test_missing_and_manipulated_append_cache_is_rebuilt_from_records(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path, "run-1")
    first = store.put(make_record("one"))

    # Both the process-local lookup index and its discardable head proof may be
    # lost.  The next append must reconstruct them from authoritative records.
    store._append_index = None
    store.head_path.unlink()
    second = store.put(make_record("two", predecessors=(first.record_id,)))

    # A syntactically valid but false cache entry is no more authoritative.
    store.head_path.write_text(
        json.dumps({
            "head_record_id": "ar1-" + "f" * 64,
            "record_count": 99,
            "chain_sha256": "e" * 64,
        }),
        encoding="utf-8",
    )
    third = store.put(make_record("three", predecessors=(second.record_id,)))

    assert store.load_chain() == (first, second, third)
    head = json.loads(store.head_path.read_text(encoding="utf-8"))
    assert head["head_record_id"] == third.record_id
    assert head["record_count"] == 3


def test_records_win_when_append_cache_disagrees_with_authoritative_bytes(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path, "run-1")
    first = store.put(make_record("one"))
    path = store.records_dir / f"{first.record_id}.json"
    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["record"]["logical_id"] = "tampered"
    path.write_text(json.dumps(envelope), encoding="utf-8")

    # Losing the head proof forces an authoritative rebuild.  The stale
    # process lookup cannot make the corrupted record acceptable.
    store.head_path.unlink()
    with pytest.raises(ArtifactCorruptionError, match="digest mismatch"):
        store.append_context(
            record_type=RecordType.WORK_UNIT,
            logical_id="two",
            idempotency_key="effect-two-1",
        )


def test_external_chain_fork_invalidates_append_index_and_fails_closed(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path, "run-1")
    first = store.put(make_record("one"))
    left = make_record("left", predecessors=(first.record_id,))
    right = make_record("right", predecessors=(first.record_id,))
    write_envelope(store.records_dir / f"{left.record_id}.json", left)
    write_envelope(store.records_dir / f"{right.record_id}.json", right)

    # The directory proof changes even though the stale head cache still
    # matches the old in-memory index.  Rebuild must retain the full fork check.
    with pytest.raises(ArtifactCorruptionError, match="forks after predecessor"):
        store.append_context(
            record_type=RecordType.WORK_UNIT,
            logical_id="next",
            idempotency_key="effect-next-1",
        )


def test_append_index_observes_records_published_by_a_later_store_instance(
    tmp_path: Path,
) -> None:
    first_store = ArtifactStore(tmp_path, "run-1")
    first = first_store.put(make_record("one"))
    second_store = ArtifactStore(tmp_path, "run-1")
    second = second_store.put(make_record("two", predecessors=(first.record_id,)))

    third = first_store.put(make_record("three", predecessors=(second.record_id,)))

    assert first_store.load_chain() == (first, second, third)


def test_cache_refresh_passes_expected_progress_without_instance_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = ArtifactStore(tmp_path, "run-1")
    prior = (make_record("one"),)
    current = (*prior, make_record("two", predecessors=(prior[0].record_id,)))
    observed: list[tuple[ArtifactRecord, ...] | None] = []

    def capture(
        chain: tuple[ArtifactRecord, ...],
        *,
        expected_cache_chain: tuple[ArtifactRecord, ...] | None = None,
    ) -> None:
        assert chain == current
        observed.append(expected_cache_chain)

    monkeypatch.setattr(store, "_refresh_head_cache", capture)
    store._refresh_cache_with_context(current, prior)

    assert observed == [prior]
    assert not hasattr(store, "_expected_cache_chain")


def test_scan_detects_predecessor_cycle(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path, "run-1")
    one_seed = make_record("one")
    two_seed = make_record("two")
    one = replace(one_seed, predecessor_ids=(two_seed.record_id,))
    two = replace(two_seed, predecessor_ids=(one_seed.record_id,))
    write_envelope(store.records_dir / f"{one.record_id}.json", one)
    write_envelope(store.records_dir / f"{two.record_id}.json", two)
    with pytest.raises(ArtifactCorruptionError, match="cycle"):
        store.load_chain()


def test_select_filters_in_chain_order(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path, "run-1")
    first = store.put(make_record("shared"))
    second = store.put(make_record(
        "task", TaskPayload("feature/x", ("src/a.py",), DIGEST),
        predecessors=(first.record_id,),
    ))
    third = store.put(make_record("shared", revision=2, predecessors=(second.record_id,)))
    assert store.select(logical_id="shared") == (first, third)
    assert store.select(record_type=RecordType.TASK) == (second,)


@pytest.mark.parametrize("outcome", ["failed", "stopped"])
def test_store_rejects_non_success_completion_with_binding(
    tmp_path: Path, outcome: str
) -> None:
    store = ArtifactStore(tmp_path, "run-1")
    record = make_record("completion", WorkflowCompletionPayload(outcome, "binding-final"))
    with pytest.raises(ArtifactConflictError, match="cannot carry final_binding_id"):
        store.put(record)


@pytest.mark.parametrize("outcome", ["failed", "stopped"])
def test_scan_rejects_persisted_non_success_completion_with_binding(
    tmp_path: Path, outcome: str
) -> None:
    """Exercise the persisted invariant without passing through put()."""
    store = ArtifactStore(tmp_path, "run-1")
    record = make_record(
        "completion", WorkflowCompletionPayload(outcome, "binding-final")
    )
    write_envelope(store.records_dir / f"{record.record_id}.json", record)

    with pytest.raises(ArtifactCorruptionError, match="cannot carry final_binding_id"):
        store.load_chain()


@pytest.mark.parametrize("run_id", ["../escape", "/absolute", "bad/name", ""])
def test_store_rejects_run_id_path_escape(tmp_path: Path, run_id: str) -> None:
    with pytest.raises(ArtifactStoreError, match="safe artifact path"):
        ArtifactStore(tmp_path, run_id)
