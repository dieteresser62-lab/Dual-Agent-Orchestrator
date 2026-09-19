from __future__ import annotations

from dataclasses import replace
import re

import pytest

from artifact_models import (
    AgentResultPayload,
    ArtifactRecord,
    BindingPayload,
    CommandSpec,
    FindingSeverity,
    FindingTransitionPayload,
    FindingHandoffImportPayload,
    ImportedFindingTransition,
    finding_transition_sequence_sha256,
    Fingerprint,
    FingerprintKind,
    GateDecisionPayload,
    GatePayload,
    ReviewPayload,
    Role,
    RoleProfilePayload,
    RunIdentityPayload,
    RunProfilePayload,
    ValidationAttestationPayload,
    ValidationRequestPayload,
    ValidationResult,
    WorkUnitPayload,
    WorkflowTransitionPayload,
)
from artifact_projection import (
    ArtifactAuditProjection,
    ArtifactProjectionError,
    render_artifact_sections as render_artifact_sections_checked,
    render_replay_sections,
    semantic_artifact_digest as semantic_artifact_digest_checked,
)
from artifact_replay import replay_artifacts as replay_artifacts_checked
from artifact_bridge import ArtifactBridge
from artifact_store import ArtifactStore
from artifact_models import (
    ProviderInputComponentPayload, ProviderInputMeasurementPayload,
    ProviderUsagePayload,
)


def _bind_records(
    records: tuple[ArtifactRecord, ...] | list[ArtifactRecord],
    run_id: str,
    fingerprint: Fingerprint,
) -> tuple[ArtifactRecord, ...]:
    bound = list(records)
    for logical_id, payload in (
        (
            "run-identity",
            RunIdentityPayload("task.md", "feature/test", "b" * 40, "b" * 40, "IMPLEMENT", None),
        ),
        (
            "run-profile",
            RunProfilePayload(
                RoleProfilePayload("implementer-model", "medium"),
                RoleProfilePayload("reviewer-model", "high"),
            ),
        ),
    ):
        bound.append(
            ArtifactRecord.create(
                run_id=run_id,
                logical_id=logical_id,
                revision=1,
                fingerprint=fingerprint,
                predecessor_ids=((bound[-1].record_id,) if bound else ()),
                created_at=f"2026-08-18T10:59:{len(bound):02d}+00:00",
                idempotency_key=logical_id,
                payload=payload,
            )
        )
    return tuple(bound)


def _bind_bridge(bridge: ArtifactBridge) -> None:
    for logical_id, payload in (
        (
            "run-identity",
            RunIdentityPayload("task.md", "feature/test", "b" * 40, "b" * 40, "IMPLEMENT", None),
        ),
        (
            "run-profile",
            RunProfilePayload(
                RoleProfilePayload("implementer-model", "medium"),
                RoleProfilePayload("reviewer-model", "high"),
            ),
        ),
    ):
        bridge.append(
            payload,
            logical_id=logical_id,
            idempotency_key=logical_id,
            fingerprint_sha256="a" * 64,
        )


def replay_artifacts(records, expected_run_id):  # type: ignore[no-untyped-def]
    return replay_artifacts_checked(
        records,
        expected_run_id,
        require_content_authority=False,
        require_review_authority=False,
    )


def _render_fixture_sections(records):  # type: ignore[no-untyped-def]
    chain = tuple(records)
    run_id = chain[0].run_id if chain else "empty-projection"
    return render_replay_sections(replay_artifacts(chain, run_id))


def _semantic_fixture_digest(records):  # type: ignore[no-untyped-def]
    chain = tuple(records)
    run_id = chain[0].run_id if chain else "empty-projection"
    return replay_artifacts(chain, run_id).semantic_digest


def _chain() -> tuple[ArtifactRecord, ...]:
    payloads = (
        WorkUnitPayload(
            slice_id="5",
            round_number=2,
            paths=("src/a.py", "tests/test_a.py"),
            open_finding_ids=("C-01",),
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
            output_digest="b" * 64,
            content_record_id="ar1-" + "0" * 64,
        ),
        ReviewPayload(
            reviewer=Role.CLAUDE,
            work_unit_id="12",
            verdict="approved",
            finding_ids=("C-01",),
            evidence=None,
            transport_schema="native-claude-review-v2",
            request_id="native-review-request-" + "b" * 64,
            response_sha256="c" * 64,
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
    return _bind_records((*records, binding), "run-5", binding.fingerprint)


def test_public_projection_helpers_use_production_strict_replay() -> None:
    fingerprint = Fingerprint(FingerprintKind.IMPLEMENTATION, "a" * 64)
    bound = list(_bind_records((), "strict-projection", fingerprint))

    accepted = replay_artifacts_checked(tuple(bound), "strict-projection")
    assert semantic_artifact_digest_checked(tuple(bound)) == accepted.semantic_digest
    assert render_artifact_sections_checked(tuple(bound)) == render_replay_sections(
        accepted
    )

    for logical_id, payload in (
        ("work-unit-1", WorkUnitPayload("1", 1, ("src/a.py",))),
        (
            "agent-1-codex_implementation-1",
            AgentResultPayload(
                Role.CODEX,
                "1",
                "ready",
                (),
                transport_schema="native-codex-v2",
                request_id="native-codex-request-" + "b" * 64,
                response_sha256="c" * 64,
            ),
        ),
    ):
        bound.append(
            ArtifactRecord.create(
                run_id="strict-projection",
                logical_id=logical_id,
                revision=1,
                fingerprint=fingerprint,
                predecessor_ids=(bound[-1].record_id,),
                created_at=f"2026-08-18T11:00:0{len(bound)}+00:00",
                idempotency_key=logical_id,
                payload=payload,
            )
        )

    for projector in (
        render_artifact_sections_checked,
        semantic_artifact_digest_checked,
    ):
        with pytest.raises(
            ArtifactProjectionError,
            match="native decision has no unique earlier provider-content record",
        ):
            projector(tuple(bound))


def test_same_chain_renders_byte_identically_in_record_sequence() -> None:
    chain = _chain()

    first = _render_fixture_sections(chain)
    second = _render_fixture_sections(chain)

    assert first == second
    table = first["decision-table"]
    assert table.index(chain[0].record_id[:16]) < table.index(chain[-1].record_id[:16])
    assert "Work-Unit" in first["approval-status"]
    assert "`src/a.py`" in first["approval-status"]
    assert "### Binding · commit" in first["approval-status"]
    assert "### Nachweis vollständiger Bindungswerte" in table


def test_projection_shows_import_origin_and_source_lifecycle_as_history() -> None:
    source = ImportedFindingTransition(
        "ar1-" + "1" * 64,
        FindingTransitionPayload(
            "C-01", Role.CLAUDE, Role.CLAUDE, "opened", FindingSeverity.OBSERVATION,
            "open", "Plan review history.", "plan-review", "Carry it.",
            "Implementation sees it.", "plan", 1,
        ),
    )
    imported_payload = FindingHandoffImportPayload(
        "source-run", "ar1-" + "2" * 64, "3" * 40,
        "ar1-" + "4" * 64, "ar1-" + "5" * 64, "target-run", "6" * 64,
        finding_transition_sequence_sha256((source,)), (source,), Role.ORCHESTRATOR,
    )
    imported = ArtifactRecord.create(
        run_id="target-run", logical_id="finding-import", revision=1,
        fingerprint=Fingerprint(FingerprintKind.CONTRACT, "a" * 64),
        predecessor_ids=(), created_at="2026-08-28T10:00:00+00:00",
        idempotency_key="finding-import", payload=imported_payload,
    )
    unit = ArtifactRecord.create(
        run_id="target-run", logical_id="work-unit-1", revision=1,
        fingerprint=Fingerprint(FingerprintKind.CONTRACT, "a" * 64),
        predecessor_ids=(imported.record_id,), created_at="2026-08-28T10:00:01+00:00",
        idempotency_key="work-unit-1",
        payload=WorkUnitPayload("1", 1, ("src/a.py",), ("C-01",), imported.record_id),
    )

    sections = _render_fixture_sections(
        _bind_records((imported, unit), "target-run", imported.fingerprint)
    )
    assert "Finding-Import · fremde Vorgeschichte" in sections["approval-status"]
    assert "source-run" in sections["approval-status"]
    assert "imported:opened" in sections["findings"]
    assert "Plan review history." in sections["findings"]


def test_projection_has_one_deduplicated_full_value_evidence_table() -> None:
    sections = _render_fixture_sections(_chain())
    document = "\n".join(sections[key] for key in sections)
    heading = "### Nachweis vollständiger Bindungswerte"
    assert document.count(heading) == 1
    before, evidence = document.split(heading, 1)
    assert re.search(r"(?<![0-9a-f])[0-9a-f]{40,64}(?![0-9a-f])", before) is None
    full_values = re.findall(
        r"\| `[0-9a-f]{12}` \| `([0-9a-f]{40}|[0-9a-f]{64})` \|",
        evidence,
    )
    assert full_values
    assert len(full_values) == len(set(full_values))
    assert "Record-ID" in evidence
    assert "Fingerprint" in evidence


@pytest.mark.parametrize(
    ("key", "human_readable_form"),
    (
        ("claude-review", "### Claude · Runde 2 · approved"),
        ("codex-responses", "Keine Codex-Findingantworten."),
        ("validation-attestation", "| Status | Exit | Output-Digest |"),
        ("test-approval-premortem", "| Seq/Record | Gate | Status |"),
        ("findings", "| Seq/Record | Finding | Rolle | Runde |"),
        ("decision-table", "### Nachweis vollständiger Bindungswerte"),
        ("approval-status", "| Seq/Record | Art | Ziel | Attestierung |"),
    ),
)
def test_every_projection_section_has_a_human_readable_event_or_table_form(
    key: str, human_readable_form: str
) -> None:
    assert human_readable_form in _render_fixture_sections(_chain())[key]


def test_projection_renders_native_finding_convergence_from_records(tmp_path) -> None:  # type: ignore[no-untyped-def]
    bridge = ArtifactBridge(ArtifactStore(tmp_path, "run-convergence"))
    _bind_bridge(bridge)
    bridge.append(
        WorkUnitPayload(
            slice_id="1",
            round_number=2,
            paths=("src/a.py",),
            open_finding_ids=("C-01",),
        ),
        logical_id="work-unit-7",
        idempotency_key="work-unit:7:round:2",
        fingerprint_sha256="a" * 64,
    )
    bridge.append(
        FindingTransitionPayload(
            finding_id="C-01",
            reporter=Role.CLAUDE,
            actor=Role.CLAUDE,
            action="opened",
            severity=FindingSeverity.BLOCKER,
            finding_status="open",
            rationale="The native correction is required.",
            work_unit_id="7",
            summary="The native correction is required.",
            acceptance_test="The following review closes the replayed finding.",
            origin_slice_id="01",
            origin_round_number=1,
        ),
        logical_id="finding-C-01",
        idempotency_key="finding:C-01:opened:1:claude",
        fingerprint_sha256="a" * 64,
    )
    bridge.append(
        FindingTransitionPayload(
            finding_id="C-01",
            reporter=Role.CLAUDE,
            actor=Role.CODEX,
            action="responded",
            severity=FindingSeverity.BLOCKER,
            finding_status="open",
            rationale="The correction is complete.",
            work_unit_id="7",
            response_decision="accepted",
        ),
        logical_id="finding-C-01",
        idempotency_key="finding-response:C-01:1",
        fingerprint_sha256="b" * 64,
    )
    bridge.append(
        FindingTransitionPayload(
            finding_id="C-01",
            reporter=Role.CLAUDE,
            actor=Role.CLAUDE,
            action="status_changed",
            severity=FindingSeverity.BLOCKER,
            finding_status="closed",
            rationale="The corrected fingerprint proves convergence.",
            work_unit_id="7",
        ),
        logical_id="finding-C-01",
        idempotency_key="finding:C-01:status_changed:2:claude",
        fingerprint_sha256="b" * 64,
    )

    rendered = _render_fixture_sections(bridge.store.load_chain())["findings"]

    assert "### Native convergence summary" in rendered
    assert "| `C-01` | `7` | `2` |" in rendered
    assert "`opened:open`<br>`status_changed:closed`" in rendered
    assert "`accepted` | `closed` |" in rendered
    assert "a" * 64 not in rendered
    assert "b" * 64 not in rendered


def test_projection_renders_native_and_legacy_transport_bindings_symmetrically() -> None:
    payloads = (
        WorkUnitPayload("1", 1, ("src/a.py",)),
        AgentResultPayload(
            Role.CODEX,
            "12",
            "ready",
            ("tests/test_a.py",),
            transport_schema="native-codex-v2",
            request_id="native-codex-request-" + "b" * 64,
            response_sha256="c" * 64,
        ),
        ReviewPayload(
            Role.CLAUDE,
            "12",
            "approved",
            (),
            "contracts checked",
            transport_schema="native-claude-review-v2",
            request_id="native-review-request-" + "d" * 64,
            response_sha256="e" * 64,
        ),
    )
    records: list[ArtifactRecord] = []
    predecessor: tuple[str, ...] = ()
    for sequence, payload in enumerate(payloads, start=1):
        record = ArtifactRecord.create(
            run_id="run-native",
            logical_id="work-unit-12" if sequence == 1 else f"native-{sequence}",
            revision=1,
            fingerprint=Fingerprint(FingerprintKind.IMPLEMENTATION, "a" * 64),
            predecessor_ids=predecessor,
            created_at=f"2026-08-18T10:01:0{sequence}+00:00",
            idempotency_key=f"native:{sequence}",
            payload=payload,
        )
        records.append(record)
        predecessor = (record.record_id,)

    sections = _render_fixture_sections(
        _bind_records(tuple(records), "run-native", records[0].fingerprint)
    )

    assert "`native-codex-v2`" in sections["approval-status"]
    assert "`native-codex-request-" in sections["approval-status"]
    assert "`native-claude-review-v2`" in sections["claude-review"]
    assert "`native-review-request-" in sections["claude-review"]


def test_projection_reduces_attempts_and_keeps_unknown_usage_explicit(tmp_path) -> None:  # type: ignore[no-untyped-def]
    bridge = ArtifactBridge(ArtifactStore(tmp_path, "run-attempts"))
    _bind_bridge(bridge)
    bridge.append(
        WorkUnitPayload("1", 1, ("src/a.py",)), logical_id="work-unit-1",
        idempotency_key="work-unit:1", fingerprint_sha256="a" * 64,
    )
    measurement = bridge.append(
        ProviderInputMeasurementPayload(
            Role.CLAUDE, Role.CLAUDE, "claude_slice_review", "1", "a" * 64,
            "b" * 64, "c" * 64, "d" * 64,
            (ProviderInputComponentPayload("prompt", 3, 3),),
            3, 3, 10, 10, None, None, None, 10, 10, True, (), 0, 0, "prompt",
        ),
        logical_id="measurement-1", idempotency_key="measurement:1",
        fingerprint_sha256="a" * 64,
    )
    first = bridge.start_provider_attempt(
        measurement_record=measurement, binding_fingerprint="a" * 64, work_unit_id="1",
        model="sonnet", effort="high",
    )
    bridge.finish_provider_attempt(
        first, duration_seconds=2.0, failure_kind="network",
        usage=ProviderUsagePayload(input_tokens=0, output_tokens=5),
    )
    bridge.start_provider_attempt(
        measurement_record=measurement, binding_fingerprint="a" * 64, work_unit_id="1",
        model="sonnet", effort="high",
    )

    rendered = _render_fixture_sections(bridge.store.load_chain())["validation-attestation"]
    assert "Attempts `2`, offen `1`" in rendered
    assert "input_tokens=sum:0,known:1,unknown:1" in rendered
    assert "output_tokens=sum:5,known:1,unknown:1" in rendered
    assert "Fehler `network`" in rendered
    assert "Modell `sonnet`; Effort `high`" in rendered
    assert "local_input_chars" in rendered and "local_input_bytes" in rendered
    assert "Inputzeichen `3`" in rendered
    assert "Inputbytes `3`" in rendered
    assert "Duration `2.000000`" in rendered
    assert "Retrystatus `open`" in rendered


def test_projection_can_render_an_accepted_replay_without_reduction_drift() -> None:
    chain = _chain()
    replay = replay_artifacts(chain, "run-5")

    projection = ArtifactAuditProjection.from_replay(replay)

    assert projection.replay_result is replay
    assert projection.render_sections() == render_replay_sections(replay)
    assert projection.render_sections() == _render_fixture_sections(chain)


def test_digest_ignores_timestamp_and_markdown_presentation_but_not_typed_facts() -> None:
    chain = _chain()
    retimed = tuple(
        replace(record, created_at=f"2027-01-01T00:00:{index:02d}+00:00")
        for index, record in enumerate(chain)
    )

    assert _semantic_fixture_digest(retimed) == _semantic_fixture_digest(chain)
    changed = list(chain)
    binding_index = next(
        index for index, record in enumerate(changed)
        if isinstance(record.payload, BindingPayload)
    )
    changed[binding_index] = replace(
        changed[binding_index],
        payload=replace(changed[binding_index].payload, target="cafebabe"),
    )
    assert _semantic_fixture_digest(tuple(changed)) != _semantic_fixture_digest(chain)


def test_projection_preserves_argv_boundaries_and_escapes_markdown_data() -> None:
    sections = _render_fixture_sections(_chain())

    validation = sections["validation-attestation"]
    findings = sections["findings"]
    assert "`tests/a file.py`, `x; touch nope`" in validation
    assert "Unsafe &#124; heading<br>## APPROVED" in findings
    assert "<!-- audit:" not in findings


def test_projection_keeps_structured_prose_inside_finding_response_and_gate_rows(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    bridge = ArtifactBridge(ArtifactStore(tmp_path, "run-table-prose"))
    _bind_bridge(bridge)
    bridge.append(
        WorkUnitPayload(
            slice_id="2",
            round_number=1,
            paths=("src/a.py",),
            open_finding_ids=("C-02",),
        ),
        logical_id="work-unit-4",
        idempotency_key="work-unit:4",
        fingerprint_sha256="a" * 64,
    )
    rationale = "First sentence. Second sentence! Third question? - list item"
    bridge.append(
        FindingTransitionPayload(
            finding_id="C-02",
            reporter=Role.CLAUDE,
            actor=Role.CODEX,
            action="responded",
            severity=FindingSeverity.BLOCKER,
            finding_status="open",
            rationale=rationale,
            work_unit_id="4",
            response_decision="accepted",
        ),
        logical_id="finding-C-02",
        idempotency_key="finding-response:C-02:1",
        fingerprint_sha256="a" * 64,
    )
    bridge.append(
        GatePayload(
            gate_kind="unexpected-file",
            decision="approved",
            authority=Role.USER,
            rationale=rationale,
        ),
        logical_id="gate-unexpected-file",
        idempotency_key="gate:unexpected-file:1",
        fingerprint_sha256="a" * 64,
    )

    sections = _render_fixture_sections(bridge.store.load_chain())
    expected_prose = (
        "First sentence.<br>Second sentence!<br>Third question?<br>- list item"
    )
    assert "### Gate-Ereignisse" in sections["test-approval-premortem"]
    assert "`unexpected-file`" in sections["test-approval-premortem"]
    for section_key in ("findings", "codex-responses", "test-approval-premortem"):
        matching_rows = [
            line
            for line in sections[section_key].splitlines()
            if "First sentence." in line
        ]
        assert len(matching_rows) == 1
        assert expected_prose in matching_rows[0]
        assert matching_rows[0].startswith("|")
        assert matching_rows[0].endswith("|")


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
            transport_schema="native-claude-review-v2",
            request_id="native-review-request-" + "b" * 64,
            response_sha256="c" * 64,
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

    rendered = ArtifactAuditProjection.from_replay(
        replay_artifacts(tuple(chain), "run-5"), slice_id="5"
    ).render_sections()

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
            transport_schema="native-claude-review-v2",
            request_id="native-review-request-" + "b" * 64,
            response_sha256="c" * 64,
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
            output_digest="d" * 64,
            content_record_id="ar1-" + "0" * 64,
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

    rendered = ArtifactAuditProjection.from_replay(
        replay_artifacts(tuple(chain), "run-5"), slice_id="5"
    ).render_sections()
    ledger = rendered["decision-table"]

    for record in (chain[2], chain[4], chain[5]):
        assert record.record_id[:16] in ledger
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
            transport_schema="native-codex-v2",
            request_id="native-codex-request-" + "b" * 64,
            response_sha256="c" * 64,
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
            output_digest="e" * 64,
            content_record_id="ar1-" + "0" * 64,
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

    projection = ArtifactAuditProjection.from_replay(
        replay_artifacts(tuple(chain), "run-5"), slice_id="7"
    )
    rendered = projection.render_sections()
    ledger = rendered["decision-table"]

    assert not any(
        isinstance(record.payload, ReviewPayload)
        for record in projection.selected_records
    )
    for record in (request, attestation, gate):
        assert record.record_id[:16] in ledger
    assert "slice-7" in rendered["validation-attestation"]
    assert "slice 7 before review" in rendered["test-approval-premortem"]


def test_slice_projection_resolves_selected_gate_decision_from_unselected_gate_record() -> None:
    chain = list(_chain())

    def append(
        logical_id: str,
        payload: object,
        *,
        fingerprint: str,
        kind: FingerprintKind = FingerprintKind.IMPLEMENTATION,
    ) -> ArtifactRecord:
        record = ArtifactRecord.create(
            run_id="run-5",
            logical_id=logical_id,
            revision=1,
            fingerprint=Fingerprint(kind, fingerprint),
            predecessor_ids=(chain[-1].record_id,),
            created_at=f"2026-08-18T10:03:{len(chain):02d}+00:00",
            idempotency_key=f"slice-8:{logical_id}",
            payload=payload,  # type: ignore[arg-type]
        )
        chain.append(record)
        return record

    append(
        "work-unit-15",
        WorkUnitPayload(slice_id="8", round_number=1, paths=("src/d.py",)),
        fingerprint="c" * 64,
        kind=FingerprintKind.CONTRACT,
    )
    append(
        "workflow-transition-slice-8",
        WorkflowTransitionPayload(
            "8", "in_progress", "15", "codex_implementation", "in_progress"
        ),
        fingerprint="c" * 64,
        kind=FingerprintKind.CONTRACT,
    )
    append(
        "agent-15-codex",
        AgentResultPayload(
            role=Role.CODEX,
            work_unit_id="15",
            outcome="ready",
            test_files=("tests/test_d.py",),
            transport_schema="native-codex-v2",
            request_id="native-codex-request-" + "d" * 64,
            response_sha256="e" * 64,
        ),
        fingerprint="d" * 64,
    )
    gate = append(
        "gate-slice-8",
        GatePayload(
            gate_kind="test-change",
            decision="approved",
            authority=Role.USER,
            rationale="separate test fingerprint",
        ),
        fingerprint="e" * 64,
    )
    decision = append(
        "gate-decision-15",
        GateDecisionPayload(
            work_unit_id="15",
            gate_record_id=gate.record_id,
            paths=("tests/test_d.py",),
            resume_step="claude_slice_review",
        ),
        fingerprint="e" * 64,
    )

    projection = ArtifactAuditProjection.from_replay(
        replay_artifacts(tuple(chain), "run-5"), slice_id="8"
    )
    selected = projection.selected_records
    rendered = projection.render_sections()

    assert decision in selected
    assert gate not in selected
    assert decision.record_id[:16] in rendered["decision-table"]
    assert projection.replay_result.subset(selected).gate_decisions[0].authority is Role.USER
