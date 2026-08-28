from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from artifact_bridge import (
    ArtifactBridge, ArtifactBridgeError, attestation_payload, command_payload,
    finding_payload, review_payload, validation_request_payload,
    finding_handoff_export_payload, finding_handoff_import_payload,
)
from artifact_models import (
    ArtifactRecord, ArtifactValidationError, FindingSeverity, FindingTransitionPayload,
    Fingerprint, FingerprintKind, PlanPayload, ReviewPayload, SliceSpec,
    ProviderAttemptPayload, ProviderInputComponentPayload,
    ProviderInputMeasurementPayload, ProviderUsagePayload, Role, WorkUnitPayload,
)
from artifact_store import ArtifactStore
from artifact_replay import replay_artifacts
from contracts import (
    AgentRole, ContractResult, FindingClass, FindingOrigin, FindingRecord,
    FindingStatus, ReviewEvidence, ValidationAttestation, ValidationCommandSpec,
    ValidationRecord, ValidationStatus,
)
from validation_matrix import ValidationCommand, ValidationRequest


DIGEST = "a" * 64


def test_bridge_constructs_lossless_handoff_only_from_accepted_replay() -> None:
    records: list[ArtifactRecord] = []
    def append(logical_id: str, payload: object) -> ArtifactRecord:
        record = ArtifactRecord.create(
            run_id="source-run", logical_id=logical_id, revision=1,
            fingerprint=Fingerprint(FingerprintKind.CONTRACT, DIGEST),
            predecessor_ids=((records[-1].record_id,) if records else ()),
            created_at=f"2026-08-28T10:00:0{len(records)}+00:00",
            idempotency_key=f"source:{logical_id}", payload=payload,  # type: ignore[arg-type]
        )
        records.append(record)
        return record
    append("plan", PlanPayload("docs/plan.md", "b" * 40, (SliceSpec("1", "one", ("src/a.py",)),)))
    append("finding-C-01", FindingTransitionPayload(
        "C-01", Role.CLAUDE, Role.CLAUDE, "opened", FindingSeverity.OBSERVATION,
        "open", "Carry it.", "plan-review", "Carry it.", "It remains visible.", "plan", 1,
    ))
    review = append("review", ReviewPayload(
        Role.CLAUDE, "plan-review", "approved", ("C-01",), None,
        "native-claude-review-v2", "native-review-request-" + "c" * 64, "d" * 64,
    ))
    before_export = replay_artifacts(records, "source-run")
    export_payload = finding_handoff_export_payload(
        before_export, approved_plan_commit="b" * 40,
        approval_review_record_id=review.record_id,
        target_task_path="inbox/implement.md", target_task_bytes=b"task",
    )
    export_record = append("finding-export", export_payload)
    source = replay_artifacts(records, "source-run")
    imported = finding_handoff_import_payload(
        source, export_record, target_run_id="target-run", target_task_bytes=b"task"
    )

    assert imported.transitions[0].payload == records[1].payload
    assert imported.export_record_id == export_record.record_id
    with pytest.raises(ArtifactBridgeError, match="task bytes differ"):
        finding_handoff_import_payload(
            source, export_record, target_run_id="target-run", target_task_bytes=b"tampered"
        )
    with pytest.raises(ArtifactBridgeError, match="not in the accepted source replay"):
        finding_handoff_import_payload(
            source, ArtifactRecord.create(
                run_id="source-run", logical_id="foreign-export", revision=1,
                fingerprint=export_record.fingerprint,
                predecessor_ids=export_record.predecessor_ids,
                created_at=export_record.created_at,
                idempotency_key="source:foreign-export", payload=export_record.payload,
            ),
            target_run_id="target-run", target_task_bytes=b"task",
        )


def test_finding_payload_preserves_legacy_shape_and_native_authority() -> None:
    finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="Persist the complete native finding snapshot.",
        acceptance_test="Replay rebuilds the request without state or Markdown.",
        origin=FindingOrigin("01", 2, AgentRole.CLAUDE),
    )

    legacy = finding_payload(finding)
    native = finding_payload(finding, work_unit_id=3)

    assert legacy.work_unit_id is None
    assert legacy.summary is None
    assert legacy.acceptance_test is None
    assert legacy.origin_slice_id is None
    assert legacy.origin_round_number is None
    assert native.work_unit_id == "3"
    assert native.summary == finding.summary
    assert native.acceptance_test == finding.acceptance_test
    assert native.origin_slice_id == "01"
    assert native.origin_round_number == 2


def test_bridge_is_idempotent_before_creating_volatile_metadata(tmp_path: Path) -> None:
    calls = 0

    def now() -> str:
        nonlocal calls
        calls += 1
        return f"2026-08-18T10:00:0{calls}+00:00"

    bridge = ArtifactBridge(ArtifactStore(tmp_path, "run-1"), now=now)
    payload = WorkUnitPayload("03", 1, ("src/a file.py",))
    first = bridge.append(
        payload, logical_id="work-3", idempotency_key="work:3",
        fingerprint_sha256=DIGEST,
    )
    second = bridge.append(
        payload, logical_id="work-3", idempotency_key="work:3",
        fingerprint_sha256=DIGEST,
    )
    assert first == second
    assert calls == 1


def test_bridge_rejects_idempotency_key_with_different_meaning(tmp_path: Path) -> None:
    bridge = ArtifactBridge(ArtifactStore(tmp_path, "run-1"))
    original = WorkUnitPayload("03", 1, ("src/a.py",))
    bridge.append(
        original, logical_id="work-3", idempotency_key="work:3",
        fingerprint_sha256=DIGEST,
    )
    with pytest.raises(ArtifactBridgeError, match="differs semantically"):
        bridge.append(
            replace(original, paths=("src/b.py",)), logical_id="work-3",
            idempotency_key="work:3", fingerprint_sha256=DIGEST,
        )


def test_command_mapping_preserves_argv_boundaries_and_legacy_shell_verbatim() -> None:
    argv = ("python3", "tool with spaces.py", "Grüße", "$(not-a-shell)", "a'b")
    structured = command_payload(ValidationCommandSpec(argv=argv))
    legacy = command_payload(
        ValidationCommandSpec(legacy_shell="python3 'tool with spaces.py' && echo no")
    )
    assert structured.argv == argv
    assert structured.mode == "argv"
    assert legacy.argv == ("python3 'tool with spaces.py' && echo no",)
    assert legacy.mode == "legacy_shell"


def test_attestation_mapping_uses_command_specs_not_display_reparsing() -> None:
    spec = ValidationCommandSpec(argv=("python3", "a b.py", "--name=x;y"))
    attestation = ValidationAttestation(
        attestation_id="validation-a", diff_fingerprint=DIGEST,
        expected_commands=(spec.display,),
        records=(ValidationRecord(ValidationStatus.PASS, spec.display, 0, "ok"),),
        output_digest="b" * 64, summary="passed", command_specs=(spec,),
    )
    assert attestation_payload(attestation).results[0].command.argv == spec.argv


def test_legacy_attestation_does_not_guess_argv() -> None:
    display = "python3 -m pytest 'a b.py'"
    attestation = ValidationAttestation(
        attestation_id="legacy-a", diff_fingerprint=DIGEST,
        expected_commands=(display,),
        records=(ValidationRecord(ValidationStatus.PASS, display, 0, "ok"),),
        output_digest="b" * 64, summary="passed",
    )
    command = attestation_payload(attestation).results[0].command
    assert command.mode == "legacy_shell"
    assert command.argv == (display,)


def test_incomplete_attestation_preserves_missing_command_as_unavailable() -> None:
    first = ValidationCommandSpec(argv=("python3", "one.py"))
    missing = ValidationCommandSpec(argv=("python3", "two.py", "a b"))
    attestation = ValidationAttestation(
        attestation_id="incomplete-a", diff_fingerprint=DIGEST,
        expected_commands=(first.display, missing.display),
        records=(ValidationRecord(ValidationStatus.PASS, first.display, 0, "ok"),),
        output_digest="b" * 64, summary="one unavailable",
        command_specs=(first, missing),
    )

    payload = attestation_payload(attestation)
    assert payload.results[1].outcome == "unavailable"
    assert payload.results[1].command.argv == missing.argv


def test_validation_request_mapping_keeps_matrix_argv() -> None:
    request = ValidationRequest(
        diff_fingerprint=DIGEST,
        commands=(ValidationCommand(argv=("python3", "a b.py", "$literal")),),
    )

    payload = validation_request_payload(request)
    assert payload.commands[0].argv == request.commands[0].argv
    assert payload.commands[0].mode == "argv"


def test_native_review_mapping_preserves_request_and_response_binding() -> None:
    result = ContractResult(
        reviewer=AgentRole.CLAUDE,
        approval=True,
        stopped=False,
        stop_request=None,
        validation=None,
        test_files=(),
        pre_mortem="A replay implementation may accidentally invoke Claude twice.",
        evidence=ReviewEvidence("resume", "record drift", "a second provider call"),
        findings=(),
        anchors=(),
    )

    payload = review_payload(
        result,
        work_unit_id=1,
        transport_schema="native-claude-review-v2",
        request_id=f"native-review-request-{'b' * 64}",
        response_sha256="c" * 64,
    )

    assert payload.transport_schema == "native-claude-review-v2"
    assert payload.request_id == f"native-review-request-{'b' * 64}"
    assert payload.response_sha256 == "c" * 64


def _measurement() -> ProviderInputMeasurementPayload:
    return ProviderInputMeasurementPayload(
        Role.CLAUDE, Role.CLAUDE, "claude_slice_review", "1", DIGEST,
        "b" * 64, "c" * 64, "d" * 64,
        (ProviderInputComponentPayload("prompt", 3, 3),),
        3, 3, 10, 10, None, None, None, 10, 10, True, (), 0, 0, "prompt",
    )


def test_provider_attempt_start_terminal_and_resume_are_stable(tmp_path: Path) -> None:
    ticks = iter(
        (
            "2026-08-18T10:00:00+00:00", "2026-08-18T10:00:01+00:00",
            "2026-08-18T10:00:02+00:00", "2026-08-18T10:00:03+00:00",
            "2026-08-18T10:00:04+00:00", "2026-08-18T10:00:05+00:00",
            "2026-08-18T10:00:06+00:00",
        )
    )
    bridge = ArtifactBridge(ArtifactStore(tmp_path, "run-1"), now=lambda: next(ticks))
    measurement = bridge.append(
        _measurement(), logical_id="measurement-1", idempotency_key="measurement:1",
        fingerprint_sha256=DIGEST,
    )

    first = bridge.start_provider_attempt(
        measurement_record=measurement, binding_fingerprint=DIGEST, work_unit_id="1",
        model="sonnet", effort="high",
    )
    terminal = bridge.finish_provider_attempt(
        first, duration_seconds=1.5, failure_kind=None,
        usage=ProviderUsagePayload(input_tokens=0, output_tokens=9),
    )
    recovered_terminal = bridge.finish_provider_attempt(
        first, duration_seconds=99.0, failure_kind=None,
        usage=ProviderUsagePayload(input_tokens=0, output_tokens=9),
    )
    second = bridge.start_provider_attempt(
        measurement_record=measurement, binding_fingerprint=DIGEST, work_unit_id="1",
        model="sonnet", effort="high",
    )

    assert isinstance(first.payload, ProviderAttemptPayload)
    assert first.revision == 1 and terminal.revision == 2
    assert recovered_terminal == terminal
    assert first.idempotency_key.endswith(":1:started")
    assert terminal.idempotency_key.endswith(":1:terminal")
    assert second.payload.attempt_number == 2
    assert second.payload.phase == "started"
    assert first.payload.model == terminal.payload.model == "sonnet"
    assert first.payload.effort == terminal.payload.effort == "high"
    assert bridge.store.load_chain()[-1] == second


def test_provider_attempt_requires_terminal_direct_predecessor(tmp_path: Path) -> None:
    bridge = ArtifactBridge(ArtifactStore(tmp_path, "run-open"))
    measurement = bridge.append(
        _measurement(), logical_id="measurement-open",
        idempotency_key="measurement:open", fingerprint_sha256=DIGEST,
    )
    bridge.start_provider_attempt(
        measurement_record=measurement, binding_fingerprint=DIGEST, work_unit_id="1"
    )
    chain_before = bridge.store.load_chain()

    with pytest.raises(ArtifactBridgeError, match="terminal direct predecessor"):
        bridge.start_provider_attempt(
            measurement_record=measurement,
            binding_fingerprint=DIGEST,
            work_unit_id="1",
        )

    assert bridge.store.load_chain() == chain_before


def test_provider_attempt_rejects_changed_digest_for_same_operation(tmp_path: Path) -> None:
    bridge = ArtifactBridge(ArtifactStore(tmp_path, "run-1"))
    first_measurement = bridge.append(
        _measurement(), logical_id="measurement-1", idempotency_key="measurement:1",
        fingerprint_sha256=DIGEST,
    )
    bridge.start_provider_attempt(
        measurement_record=first_measurement, binding_fingerprint=DIGEST, work_unit_id="1"
    )
    changed = replace(_measurement(), input_digest="e" * 64)
    changed_measurement = bridge.append(
        changed, logical_id="measurement-2", idempotency_key="measurement:2",
        fingerprint_sha256=DIGEST,
    )

    with pytest.raises(ArtifactBridgeError, match="immutable binding"):
        bridge.start_provider_attempt(
            measurement_record=changed_measurement,
            binding_fingerprint=DIGEST,
            work_unit_id="1",
        )


def test_provider_attempt_rounds_have_distinct_immutable_bindings(tmp_path: Path) -> None:
    bridge = ArtifactBridge(ArtifactStore(tmp_path, "run-rounds"))
    first_measurement = bridge.append(
        _measurement(), logical_id="measurement-round-2",
        idempotency_key="measurement:round:2", fingerprint_sha256=DIGEST,
    )
    first = bridge.start_provider_attempt(
        measurement_record=first_measurement,
        binding_fingerprint=DIGEST,
        work_unit_id="1",
        operation_instance="round:2",
    )
    bridge.finish_provider_attempt(
        first, duration_seconds=1.0, failure_kind=None, usage=None,
    )

    changed_measurement = bridge.append(
        replace(_measurement(), input_digest="e" * 64),
        logical_id="measurement-round-3",
        idempotency_key="measurement:round:3",
        fingerprint_sha256=DIGEST,
    )
    second_round = bridge.start_provider_attempt(
        measurement_record=changed_measurement,
        binding_fingerprint=DIGEST,
        work_unit_id="1",
        operation_instance="round:3",
    )

    assert second_round.payload.attempt_number == 1
    assert second_round.payload.logical_operation_id != first.payload.logical_operation_id
    with pytest.raises(ArtifactBridgeError, match="immutable binding"):
        bridge.start_provider_attempt(
            measurement_record=changed_measurement,
            binding_fingerprint=DIGEST,
            work_unit_id="1",
            operation_instance="round:2",
        )


def test_provider_attempt_round_scope_reuses_matching_legacy_operation(
    tmp_path: Path,
) -> None:
    bridge = ArtifactBridge(ArtifactStore(tmp_path, "run-legacy-round"))
    measurement = bridge.append(
        _measurement(), logical_id="measurement-legacy",
        idempotency_key="measurement:legacy", fingerprint_sha256=DIGEST,
    )
    first = bridge.start_provider_attempt(
        measurement_record=measurement,
        binding_fingerprint=DIGEST,
        work_unit_id="1",
    )
    bridge.finish_provider_attempt(
        first, duration_seconds=1.0, failure_kind="network", usage=None,
    )

    resumed = bridge.start_provider_attempt(
        measurement_record=measurement,
        binding_fingerprint=DIGEST,
        work_unit_id="1",
        operation_instance="round:2",
    )

    assert resumed.payload.logical_operation_id == first.payload.logical_operation_id
    assert resumed.payload.attempt_number == 2


def test_provider_attempt_finish_rejects_start_from_foreign_chain(
    tmp_path: Path,
) -> None:
    foreign_bridge = ArtifactBridge(ArtifactStore(tmp_path, "foreign-run"))
    foreign_measurement = foreign_bridge.append(
        _measurement(),
        logical_id="foreign-measurement",
        idempotency_key="foreign-measurement:1",
        fingerprint_sha256=DIGEST,
    )
    foreign_start = foreign_bridge.start_provider_attempt(
        measurement_record=foreign_measurement,
        binding_fingerprint=DIGEST,
        work_unit_id="1",
    )
    bridge = ArtifactBridge(ArtifactStore(tmp_path, "run-1"))
    chain_before = bridge.store.load_chain()

    with pytest.raises(
        ArtifactBridgeError,
        match="provider attempt start is not in the accepted chain",
    ):
        bridge.finish_provider_attempt(
            foreign_start,
            duration_seconds=1.0,
            failure_kind=None,
            usage=ProviderUsagePayload(input_tokens=1, output_tokens=1),
        )

    assert bridge.store.load_chain() == chain_before
