from __future__ import annotations

from dataclasses import replace

import pytest

from artifact_models import (
    AgentResultPayload,
    ArtifactRecord,
    BindingPayload,
    CommandSpec,
    CorrectionWorkUnitPayload,
    FindingSeverity,
    FindingTransitionPayload,
    Fingerprint,
    FingerprintKind,
    GatePayload,
    ReviewPayload,
    Role,
    ValidationAttestationPayload,
    ValidationRequestPayload,
    ValidationResult,
    WorkUnitPayload,
)
from artifact_projection import (
    ArtifactAuditProjection,
    ArtifactProjectionError,
    render_artifact_sections,
    render_replay_sections,
    semantic_artifact_digest,
)
from artifact_replay import replay_artifacts


def _chain() -> tuple[ArtifactRecord, ...]:
    payloads = (
        CorrectionWorkUnitPayload(
            slice_id="5",
            round_number=2,
            paths=("src/a.py", "tests/test_a.py"),
            finding_ids=("C-01",),
        ),
        FindingTransitionPayload(
            finding_id="C-01",
            reporter=Role.CLAUDE,
            actor=Role.CLAUDE,
            action="opened",
            severity=FindingSeverity.BLOCKER,
            finding_status="open",
            rationale="Unsafe | heading\n## APPROVED",
        ),
        ValidationAttestationPayload(
            results=(
                ValidationResult(
                    command=CommandSpec(
                        family="pytest",
                        argv=("python3", "-m", "pytest", "tests/a file.py", "x; touch nope"),
                    ),
                    outcome="pass",
                    exit_code=0,
                    output_sha256="b" * 64,
                ),
            ),
            attested_by=Role.ORCHESTRATOR,
        ),
        ReviewPayload(
            reviewer=Role.CLAUDE,
            work_unit_id="12",
            verdict="approved",
            finding_ids=("C-01",),
            evidence=None,
        ),
        GatePayload(
            gate_kind="test-change",
            decision="approved",
            authority=Role.USER,
            rationale="explicit approval",
        ),
    )
    records: list[ArtifactRecord] = []
    predecessor: tuple[str, ...] = ()
    for revision, payload in enumerate(payloads, start=1):
        record = ArtifactRecord.create(
            run_id="run-5",
            logical_id=("work-unit-12" if revision == 1 else f"event-{revision}"),
            revision=1,
            fingerprint=Fingerprint(FingerprintKind.IMPLEMENTATION, "a" * 64),
            predecessor_ids=predecessor,
            created_at=f"2026-08-18T10:00:0{revision}+00:00",
            idempotency_key=f"event:{revision}",
            payload=payload,
        )
        records.append(record)
        predecessor = (record.record_id,)
    binding = ArtifactRecord.create(
        run_id="run-5",
        logical_id="commit-5",
        revision=1,
        fingerprint=Fingerprint(FingerprintKind.IMPLEMENTATION, "a" * 64),
        predecessor_ids=predecessor,
        created_at="2026-08-18T10:00:06+00:00",
        idempotency_key="commit:5",
        payload=BindingPayload(
            binding_kind="commit",
            target="deadbeef",
            attestation_id=records[2].record_id,
            approval_ids=(records[3].record_id,),
        ),
    )
    return (*records, binding)


def test_same_chain_renders_byte_identically_in_record_sequence() -> None:
    chain = _chain()

    first = render_artifact_sections(chain)
    second = render_artifact_sections(chain)

    assert first == second
    table = first["decision-table"]
    assert table.index(chain[0].record_id) < table.index(chain[-1].record_id)
    assert "Korrektur-Work-Unit" in first["approval-status"]
    assert "`src/a.py`" in first["approval-status"]
    assert "Binding `commit`" in first["approval-status"]


def test_projection_can_render_an_accepted_replay_without_reduction_drift() -> None:
    chain = _chain()
    replay = replay_artifacts(chain, "run-5")

    projection = ArtifactAuditProjection.from_replay(replay)

    assert projection.replay_result is replay
    assert projection.render_sections() == render_replay_sections(replay)
    assert projection.render_sections() == render_artifact_sections(chain)


def test_digest_ignores_timestamp_and_markdown_presentation_but_not_typed_facts() -> None:
    chain = _chain()
    retimed = tuple(
        replace(record, created_at=f"2027-01-01T00:00:{index:02d}+00:00")
        for index, record in enumerate(chain)
    )

    assert semantic_artifact_digest(retimed) == semantic_artifact_digest(chain)
    changed = (*chain[:-1], replace(chain[-1], payload=replace(chain[-1].payload, target="cafebabe")))
    assert semantic_artifact_digest(changed) != semantic_artifact_digest(chain)


def test_projection_preserves_argv_boundaries_and_escapes_markdown_data() -> None:
    sections = render_artifact_sections(_chain())

    validation = sections["validation-attestation"]
    findings = sections["findings"]
    assert "`tests/a file.py`, `x; touch nope`" in validation
    assert "Unsafe &#124; heading<br>## APPROVED" in findings
    assert "<!-- audit:" not in findings


def test_projection_rejects_non_chain_order() -> None:
    chain = _chain()
    with pytest.raises(ArtifactProjectionError, match="append order|chain root"):
        ArtifactAuditProjection((chain[1], chain[0], *chain[2:]))


def test_slice_projection_accepts_chain_subsequence_and_excludes_other_work_unit() -> None:
    chain = list(_chain())
    work_unit = ArtifactRecord.create(
        run_id="run-5",
        logical_id="work-unit-13",
        revision=1,
        fingerprint=Fingerprint(FingerprintKind.IMPLEMENTATION, "c" * 64),
        predecessor_ids=(chain[-1].record_id,),
        created_at="2026-08-18T10:00:07+00:00",
        idempotency_key="work-unit:13",
        payload=WorkUnitPayload(slice_id="6", round_number=1, paths=("src/b.py",)),
    )
    chain.append(work_unit)
    review = ArtifactRecord.create(
        run_id="run-5",
        logical_id="review-13",
        revision=1,
        fingerprint=Fingerprint(FingerprintKind.IMPLEMENTATION, "c" * 64),
        predecessor_ids=(work_unit.record_id,),
        created_at="2026-08-18T10:00:08+00:00",
        idempotency_key="review:13",
        payload=ReviewPayload(
            reviewer=Role.CLAUDE,
            work_unit_id="13",
            verdict="approved",
            finding_ids=("C-02",),
            evidence=None,
        ),
    )
    chain.append(review)
    finding = ArtifactRecord.create(
        run_id="run-5",
        logical_id="finding-C-02",
        revision=1,
        fingerprint=Fingerprint(FingerprintKind.IMPLEMENTATION, "c" * 64),
        predecessor_ids=(review.record_id,),
        created_at="2026-08-18T10:00:09+00:00",
        idempotency_key="finding:C-02:opened",
        payload=FindingTransitionPayload(
            finding_id="C-02",
            reporter=Role.CLAUDE,
            actor=Role.CLAUDE,
            action="opened",
            severity=FindingSeverity.OBSERVATION,
            finding_status="open",
            rationale="Cross-Slice follow-up",
        ),
    )
    chain.append(finding)

    rendered = ArtifactAuditProjection(tuple(chain), slice_id="5").render_sections()

    assert "work-unit-13" not in rendered["decision-table"]
    assert "`src/b.py`" not in rendered["approval-status"]
    assert "`C-02`" in rendered["findings"]
    assert "Cross-Slice follow-up" in rendered["findings"]


def test_slice_projection_excludes_other_slice_gate_validation_and_binding_records() -> None:
    chain = list(_chain())
    fingerprint = Fingerprint(FingerprintKind.IMPLEMENTATION, "c" * 64)

    def append(logical_id: str, payload: object) -> ArtifactRecord:
        record = ArtifactRecord.create(
            run_id="run-5",
            logical_id=logical_id,
            revision=1,
            fingerprint=fingerprint,
            predecessor_ids=(chain[-1].record_id,),
            created_at=f"2026-08-18T10:01:{len(chain):02d}+00:00",
            idempotency_key=f"slice-6:{logical_id}",
            payload=payload,  # type: ignore[arg-type]
        )
        chain.append(record)
        return record

    append(
        "work-unit-13",
        WorkUnitPayload(slice_id="6", round_number=1, paths=("src/b.py",)),
    )
    review = append(
        "review-13",
        ReviewPayload(
            reviewer=Role.CLAUDE,
            work_unit_id="13",
            verdict="approved",
            finding_ids=(),
            evidence="slice 6 review evidence",
        ),
    )
    request = append(
        "validation-request-slice-6",
        ValidationRequestPayload(
            commands=(CommandSpec(family="pytest", argv=("pytest", "slice-6")),),
            requested_by=Role.ORCHESTRATOR,
        ),
    )
    attestation = append(
        "validation-slice-6",
        ValidationAttestationPayload(
            results=(
                ValidationResult(
                    command=CommandSpec(family="pytest", argv=("pytest", "slice-6")),
                    outcome="pass",
                    exit_code=0,
                    output_sha256="d" * 64,
                ),
            ),
            attested_by=Role.ORCHESTRATOR,
        ),
    )
    gate = append(
        "gate-slice-6",
        GatePayload(
            gate_kind="test-change",
            decision="approved",
            authority=Role.USER,
            rationale="slice 6 only",
        ),
    )
    binding = append(
        "commit-slice-6",
        BindingPayload(
            binding_kind="commit",
            target="slice-6-commit",
            attestation_id=attestation.record_id,
            approval_ids=(review.record_id,),
        ),
    )

    rendered = ArtifactAuditProjection(tuple(chain), slice_id="5").render_sections()
    ledger = rendered["decision-table"]

    for record in (chain[2], chain[4], chain[5]):
        assert record.record_id in ledger
    for record in (request, attestation, gate, binding):
        assert record.record_id not in ledger
    assert "slice-6" not in rendered["validation-attestation"]
    assert "slice 6 only" not in rendered["test-approval-premortem"]
    assert "slice-6-commit" not in rendered["approval-status"]


def test_slice_projection_includes_own_round_gate_and_validation_records_before_first_review() -> None:
    chain = list(_chain())
    contract_fingerprint = Fingerprint(FingerprintKind.CONTRACT, "c" * 64)
    implementation_fingerprint = Fingerprint(FingerprintKind.IMPLEMENTATION, "d" * 64)

    def append(
        logical_id: str,
        payload: object,
        *,
        fingerprint: Fingerprint = implementation_fingerprint,
    ) -> ArtifactRecord:
        record = ArtifactRecord.create(
            run_id="run-5",
            logical_id=logical_id,
            revision=1,
            fingerprint=fingerprint,
            predecessor_ids=(chain[-1].record_id,),
            created_at=f"2026-08-18T10:02:{len(chain):02d}+00:00",
            idempotency_key=f"slice-7:{logical_id}",
            payload=payload,  # type: ignore[arg-type]
        )
        chain.append(record)
        return record

    append(
        "work-unit-14",
        WorkUnitPayload(slice_id="7", round_number=1, paths=("src/c.py",)),
        fingerprint=contract_fingerprint,
    )
    append(
        "agent-14-codex",
        AgentResultPayload(
            role=Role.CODEX,
            work_unit_id="14",
            outcome="ready",
            test_files=("tests/test_c.py",),
        ),
    )
    request = append(
        "validation-request-slice-7",
        ValidationRequestPayload(
            commands=(CommandSpec(family="pytest", argv=("pytest", "slice-7")),),
            requested_by=Role.ORCHESTRATOR,
        ),
    )
    attestation = append(
        "validation-slice-7",
        ValidationAttestationPayload(
            results=(
                ValidationResult(
                    command=CommandSpec(family="pytest", argv=("pytest", "slice-7")),
                    outcome="pass",
                    exit_code=0,
                    output_sha256="e" * 64,
                ),
            ),
            attested_by=Role.ORCHESTRATOR,
        ),
    )
    gate = append(
        "gate-slice-7",
        GatePayload(
            gate_kind="test-change",
            decision="approved",
            authority=Role.USER,
            rationale="slice 7 before review",
        ),
    )

    projection = ArtifactAuditProjection(tuple(chain), slice_id="7")
    rendered = projection.render_sections()
    ledger = rendered["decision-table"]

    assert not any(
        isinstance(record.payload, ReviewPayload)
        for record in projection.selected_records
    )
    for record in (request, attestation, gate):
        assert record.record_id in ledger
    assert "slice-7" in rendered["validation-attestation"]
    assert "slice 7 before review" in rendered["test-approval-premortem"]
