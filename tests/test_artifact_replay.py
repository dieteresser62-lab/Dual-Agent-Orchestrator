from __future__ import annotations

from dataclasses import replace

import pytest

from artifact_models import (
    AgentResultPayload,
    ArtifactRecord,
    BindingPayload,
    CommandSpec,
    Fingerprint,
    FingerprintKind,
    RecordType,
    ReviewPayload,
    Role,
    TaskPayload,
    ValidationAttestationPayload,
    ValidationResult,
    WorkUnitPayload,
    ProviderAttemptPayload,
    ProviderInputComponentPayload,
    ProviderInputMeasurementPayload,
    ProviderUsagePayload,
)
from artifact_replay import (
    ArtifactReplayError,
    ReplayDiagnosticCode,
    replay_artifacts,
)


FP = Fingerprint(FingerprintKind.IMPLEMENTATION, "a" * 64)


def _append(
    records: list[ArtifactRecord],
    logical_id: str,
    payload: object,
    *,
    fingerprint: Fingerprint = FP,
    revision: int = 1,
) -> ArtifactRecord:
    record = ArtifactRecord.create(
        run_id="run-replay",
        logical_id=logical_id,
        revision=revision,
        fingerprint=fingerprint,
        predecessor_ids=((records[-1].record_id,) if records else ()),
        created_at=f"2026-08-21T10:00:{len(records):02d}+00:00",
        idempotency_key=(
            f"replay:{logical_id}" if revision == 1 else f"replay:{logical_id}:{revision}"
        ),
        payload=payload,  # type: ignore[arg-type]
    )
    records.append(record)
    return record


def _chain() -> tuple[ArtifactRecord, ...]:
    records: list[ArtifactRecord] = []
    _append(records, "work-unit-1", WorkUnitPayload("1", 1, ("src/a.py",)))
    _append(
        records,
        "agent-1",
        AgentResultPayload(Role.CODEX, "1", "ready", ("tests/test_a.py",)),
    )
    attestation = _append(
        records,
        "validation-1",
        ValidationAttestationPayload(
            (
                ValidationResult(
                    CommandSpec("pytest", ("pytest", "tests/test_a.py")),
                    "pass",
                    0,
                    "b" * 64,
                ),
            ),
            Role.ORCHESTRATOR,
        ),
    )
    review = _append(
        records,
        "review-1",
        ReviewPayload(Role.CLAUDE, "1", "approved", (), "checked replay"),
    )
    _append(
        records,
        "binding-1",
        BindingPayload("commit", "deadbeef", attestation.record_id, (review.record_id,)),
    )
    return tuple(records)


def _assert_code(records: tuple[ArtifactRecord, ...], code: ReplayDiagnosticCode) -> None:
    with pytest.raises(ArtifactReplayError) as caught:
        replay_artifacts(records, "run-replay")
    assert caught.value.code is code


def test_replay_is_deterministic_and_does_not_mutate_input() -> None:
    chain = _chain()
    before = tuple(record.canonical_json() for record in chain)

    first = replay_artifacts(chain, "run-replay")
    second = replay_artifacts(chain, "run-replay")

    assert first == second
    assert first.semantic_digest == second.semantic_digest
    assert first.audit_events == second.audit_events
    assert tuple(record.canonical_json() for record in chain) == before


def test_replay_ignores_created_at_for_semantic_digest() -> None:
    chain = _chain()
    retimed = tuple(
        replace(record, created_at=f"2027-01-01T00:00:{index:02d}+00:00")
        for index, record in enumerate(chain)
    )

    assert replay_artifacts(chain, "run-replay").semantic_digest == replay_artifacts(
        retimed, "run-replay"
    ).semantic_digest


def test_replay_reports_missing_duplicate_unknown_and_run_mismatch() -> None:
    _assert_code((), ReplayDiagnosticCode.RECORD_MISSING)
    chain = _chain()
    _assert_code((chain[0], chain[0]), ReplayDiagnosticCode.RECORD_DUPLICATE)
    with pytest.raises(ArtifactReplayError) as unknown:
        replay_artifacts((object(),), "run-replay")  # type: ignore[arg-type]
    assert unknown.value.code is ReplayDiagnosticCode.RECORD_UNKNOWN
    with pytest.raises(ArtifactReplayError) as wrong_run:
        replay_artifacts(chain, "another-run")
    assert wrong_run.value.code is ReplayDiagnosticCode.RECORD_RUN_MISMATCH


def test_replay_rejects_two_immutable_task_contracts_as_duplicate() -> None:
    records: list[ArtifactRecord] = []
    _append(records, "task-a", TaskPayload("feature/a", ("src/a.py",), "a" * 64))
    _append(records, "task-b", TaskPayload("feature/b", ("src/b.py",), "b" * 64))

    _assert_code(tuple(records), ReplayDiagnosticCode.RECORD_DUPLICATE)


def test_replay_reports_missing_reference_and_fingerprint_mismatch() -> None:
    records: list[ArtifactRecord] = []
    _append(records, "work-unit-1", WorkUnitPayload("1", 1, ("src/a.py",)))
    _append(
        records,
        "agent-orphan",
        AgentResultPayload(Role.CODEX, "404", "ready", ()),
    )
    _assert_code(tuple(records), ReplayDiagnosticCode.RECORD_REFERENCE_MISSING)

    chain = list(_chain())
    mismatched = replace(
        chain[-1],
        fingerprint=Fingerprint(FingerprintKind.IMPLEMENTATION, "f" * 64),
    )
    _assert_code((*chain[:-1], mismatched), ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH)


def test_replay_allows_plan_activity_before_the_first_work_unit_record() -> None:
    records: list[ArtifactRecord] = []
    _append(
        records,
        "agent-plan",
        AgentResultPayload(Role.CODEX, "1", "ready", ()),
        fingerprint=Fingerprint(FingerprintKind.IMPLEMENTATION, "c" * 64),
    )

    assert replay_artifacts(records, "run-replay").records == tuple(records)


def test_replay_rejects_activity_that_references_a_later_work_unit() -> None:
    records: list[ArtifactRecord] = []
    _append(records, "work-unit-1", WorkUnitPayload("1", 1, ("src/a.py",)))
    _append(
        records,
        "agent-future-work-unit",
        AgentResultPayload(Role.CODEX, "2", "ready", ()),
    )
    _append(records, "work-unit-2", WorkUnitPayload("2", 2, ("src/b.py",)))

    _assert_code(tuple(records), ReplayDiagnosticCode.RECORD_REFERENCE_MISSING)


def test_replay_accepts_one_provider_attempt_and_rejects_terminal_without_start() -> None:
    records: list[ArtifactRecord] = []
    _append(records, "work-unit-1", WorkUnitPayload("1", 1, ("src/a.py",)))
    measurement = _append(
        records,
        "measurement-1",
        ProviderInputMeasurementPayload(
            Role.CLAUDE, Role.CLAUDE, "claude_slice_review", "1", "a" * 64,
            "b" * 64, "c" * 64, "d" * 64,
            (ProviderInputComponentPayload("prompt", 3, 3),),
            3, 3, 10, 10, None, None, None, 10, 10, True, (), 0, 0, "prompt",
        ),
    )
    started_payload = ProviderAttemptPayload(
        Role.CLAUDE, Role.CLAUDE, "claude_slice_review", "1",
        "provider-operation-01", "a" * 64, measurement.record_id, "c" * 64, 1,
        "started", "2026-08-21T10:00:01+00:00", None, None, None, None,
    )
    _append(records, "attempt-1", started_payload)
    _append(
        records, "attempt-1",
        replace(
            started_payload, phase="succeeded",
            ended_at="2026-08-21T10:00:02+00:00", duration_seconds=1.0,
            usage=ProviderUsagePayload(output_tokens=4),
        ),
        revision=2,
    )
    assert replay_artifacts(records, "run-replay").records == tuple(records)

    terminal_only = [records[0], records[1], records[3]]
    terminal_only[2] = replace(
        terminal_only[2], revision=1,
        record_id=records[2].record_id,
        predecessor_ids=(records[1].record_id,),
    )
    _assert_code(tuple(terminal_only), ReplayDiagnosticCode.RECORD_REFERENCE_MISSING)


def test_replay_uses_first_work_unit_revision_as_reference_boundary() -> None:
    records: list[ArtifactRecord] = []
    _append(records, "work-unit-1", WorkUnitPayload("1", 1, ("src/a.py",)))
    _append(
        records,
        "agent-orphan-between-revisions",
        AgentResultPayload(Role.CODEX, "404", "ready", ()),
    )
    _append(
        records,
        "work-unit-1",
        WorkUnitPayload("1", 2, ("src/a.py",)),
        revision=2,
    )

    _assert_code(tuple(records), ReplayDiagnosticCode.RECORD_REFERENCE_MISSING)


def test_subset_accepts_equal_redeserialized_records_but_rejects_same_id_tampering() -> None:
    replay = replay_artifacts(_chain(), "run-replay")
    reconstructed = ArtifactRecord.from_dict(replay.records[1].to_dict())

    assert reconstructed is not replay.records[1]
    assert replay.subset((reconstructed,)).records == (reconstructed,)

    tampered = ArtifactRecord.from_dict(reconstructed.to_dict())
    object.__setattr__(tampered, "logical_id", "tampered")
    with pytest.raises(ArtifactReplayError) as caught:
        replay.subset((tampered,))
    assert caught.value.code is ReplayDiagnosticCode.RECORD_UNKNOWN


def test_replay_reports_type_mismatch_with_stable_code() -> None:
    record = _chain()[0]
    # Persisted bytes cannot create this state through ArtifactRecord.__init__;
    # corrupting the already-created test object exercises the reducer's
    # independent typed boundary.
    object.__setattr__(record, "record_type", RecordType.PLAN)

    _assert_code((record,), ReplayDiagnosticCode.RECORD_TYPE_MISMATCH)
