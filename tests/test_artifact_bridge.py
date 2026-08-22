from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from artifact_bridge import (
    ArtifactBridge, ArtifactBridgeError, attestation_payload, command_payload,
    review_payload, validation_request_payload,
)
from artifact_models import (
    ProviderAttemptPayload, ProviderInputComponentPayload,
    ProviderInputMeasurementPayload, ProviderUsagePayload, Role, WorkUnitPayload,
)
from artifact_store import ArtifactStore
from contracts import (
    AgentRole, ContractResult, ReviewEvidence, ValidationAttestation,
    ValidationCommandSpec, ValidationRecord, ValidationStatus,
)
from validation_matrix import ValidationCommand, ValidationRequest


DIGEST = "a" * 64


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
        transport_schema="native-claude-review-v1",
        request_id=f"native-review-request-{'b' * 64}",
        response_sha256="c" * 64,
    )

    assert payload.transport_schema == "native-claude-review-v1"
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
        measurement_record=measurement, binding_fingerprint=DIGEST, work_unit_id="1"
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
        measurement_record=measurement, binding_fingerprint=DIGEST, work_unit_id="1"
    )

    assert isinstance(first.payload, ProviderAttemptPayload)
    assert first.revision == 1 and terminal.revision == 2
    assert recovered_terminal == terminal
    assert first.idempotency_key.endswith(":1:started")
    assert terminal.idempotency_key.endswith(":1:terminal")
    assert second.payload.attempt_number == 2
    assert second.payload.phase == "started"
    assert bridge.store.load_chain()[-1] == second


def test_antigravity_schema_failure_allows_only_one_bound_continuation(
    tmp_path: Path,
) -> None:
    bridge = ArtifactBridge(ArtifactStore(tmp_path, "run-schema"))
    measurement = bridge.append(
        replace(
            _measurement(),
            provider=Role.ANTIGRAVITY,
            role=Role.ANTIGRAVITY,
            operation="antigravity_slice_review",
        ),
        logical_id="measurement-schema",
        idempotency_key="measurement:schema",
        fingerprint_sha256=DIGEST,
    )
    first = bridge.start_provider_attempt(
        measurement_record=measurement, binding_fingerprint=DIGEST, work_unit_id="1"
    )
    first_terminal = bridge.finish_provider_attempt(
        first,
        duration_seconds=1.5,
        failure_kind="antigravity_tool_schema",
        usage=ProviderUsagePayload(input_tokens=12, output_tokens=1),
    )
    second = bridge.start_provider_attempt(
        measurement_record=measurement, binding_fingerprint=DIGEST, work_unit_id="1"
    )
    bridge.finish_provider_attempt(
        second,
        duration_seconds=2.0,
        failure_kind="antigravity_tool_schema",
        usage=None,
    )
    chain_before = bridge.store.load_chain()

    with pytest.raises(ArtifactBridgeError, match="only physical attempt 2"):
        bridge.start_provider_attempt(
            measurement_record=measurement,
            binding_fingerprint=DIGEST,
            work_unit_id="1",
        )

    assert bridge.store.load_chain() == chain_before
    assert first_terminal.payload.usage == ProviderUsagePayload(
        input_tokens=12, output_tokens=1
    )
    assert second.payload.logical_operation_id == first.payload.logical_operation_id
    assert second.payload.attempt_number == 2


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
