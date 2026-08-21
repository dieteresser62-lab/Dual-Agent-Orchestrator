from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from artifact_bridge import (
    ArtifactBridge, ArtifactBridgeError, attestation_payload, command_payload,
    validation_request_payload,
)
from artifact_models import (
    ProviderAttemptPayload, ProviderInputComponentPayload,
    ProviderInputMeasurementPayload, ProviderUsagePayload, Role, WorkUnitPayload,
)
from artifact_store import ArtifactStore
from contracts import (
    ValidationAttestation, ValidationCommandSpec, ValidationRecord, ValidationStatus,
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
