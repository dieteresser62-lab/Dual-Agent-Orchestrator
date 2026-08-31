from __future__ import annotations

from dataclasses import replace
import shlex

from artifact_bridge import ArtifactBridge
from artifact_models import (
    AgentResultPayload,
    CommandSpec,
    ProviderContentPayload,
    RecordType,
    ReviewPayload,
    Role,
    ValidationAttestationPayload,
    ValidationContentPayload,
    ValidationOutputContent,
    ValidationResult,
    WorkUnitPayload,
    CorrectionWorkUnitPayload,
    WorkflowTransitionPayload,
    stable_record_id,
)
from content_authority import ValidationCapture, validation_output_digest


def validation_history_mirror(
    bridge: ArtifactBridge,
    attestation_record,
) -> dict[str, object]:
    """Project the complete state-v3 mirror for a test validation authority."""
    payload = attestation_record.payload
    if not isinstance(payload, ValidationAttestationPayload):
        raise AssertionError("test validation mirror requires an attestation record")
    content_record = next(
        record
        for record in bridge.store.load_chain()
        if record.record_id == payload.content_record_id
    )
    content = content_record.payload
    if not isinstance(content, ValidationContentPayload):
        raise AssertionError("test validation mirror requires its content record")
    expected_commands: list[str] = []
    records: list[dict[str, object]] = []
    command_specs: list[dict[str, object]] = []
    for result, output in zip(payload.results, content.outputs, strict=True):
        display = (
            result.command.argv[0]
            if result.command.mode == "legacy_shell"
            else shlex.join(result.command.argv)
        )
        expected_commands.append(display)
        command_specs.append(
            {
                "mode": result.command.mode,
                "argv": list(result.command.argv),
                "legacy_shell": (
                    result.command.argv[0]
                    if result.command.mode == "legacy_shell"
                    else None
                ),
            }
        )
        if result.outcome != "unavailable":
            records.append(
                {
                    "status": "PASS" if result.outcome == "pass" else "FAIL",
                    "command": display,
                    "exit_code": result.exit_code,
                    "output": bridge.store.read_blob(output.compact_output).decode(
                        "utf-8"
                    ),
                }
            )
    return {
        "attestation_id": attestation_record.logical_id,
        "diff_fingerprint": attestation_record.fingerprint.sha256,
        "expected_commands": expected_commands,
        "records": records,
        "output_digest": payload.output_digest,
        "summary": content.summary,
        "command_specs": command_specs,
    }


def append_validation_authority(
    bridge: ArtifactBridge,
    payload: ValidationAttestationPayload,
    *,
    logical_id: str,
    idempotency_key: str,
    fingerprint_sha256: str,
):
    captures: list[ValidationCapture] = []
    outputs: list[ValidationOutputContent] = []
    results: list[ValidationResult] = []
    store = bridge.store
    for result in payload.results:
        command = CommandSpec(
            "legacy-validation"
            if result.command.mode == "legacy_shell"
            else "validation",
            result.command.argv,
            result.command.mode,
        )
        display = (
            command.argv[0]
            if command.mode == "legacy_shell"
            else shlex.join(command.argv)
        )
        if result.outcome == "unavailable":
            stdout = b""
            stderr = b"unavailable"
            compact = b""
            digest_outcome = "unavailable"
        else:
            compact = f"{result.outcome}:{result.exit_code}".encode("utf-8")
            stdout = compact
            stderr = b""
            digest_outcome = result.outcome
        captures.append(
            ValidationCapture(
                display,
                digest_outcome,
                result.exit_code,
                stdout.decode("utf-8"),
                stderr.decode("utf-8"),
                compact.decode("utf-8"),
            )
        )
        compact_ref = store.put_blob(compact)
        outputs.append(
            ValidationOutputContent(
                command,
                digest_outcome,
                result.exit_code,
                store.put_blob(stdout),
                store.put_blob(stderr),
                compact_ref,
                len(stdout) + len(stderr),
            )
        )
        results.append(
            replace(
                result,
                command=command,
                output_sha256=compact_ref.sha256,
            )
        )
    output_digest = validation_output_digest(captures)
    context = store.append_context(
        record_type=RecordType.VALIDATION_ATTESTATION,
        logical_id=logical_id,
        idempotency_key=idempotency_key,
    )
    result_record_id = stable_record_id(
        store.run_id,
        RecordType.VALIDATION_ATTESTATION,
        logical_id,
        context.next_revision,
    )
    content = bridge.append(
        ValidationContentPayload(
            logical_id,
            result_record_id,
            "validation-matrix-v1",
            output_digest,
            "test validation authority",
            tuple(outputs),
        ),
        logical_id=f"validation-content-{logical_id}",
        idempotency_key=f"content:{idempotency_key}",
        fingerprint_sha256=fingerprint_sha256,
    )
    return bridge.append(
        ValidationAttestationPayload(
            tuple(results),
            payload.attested_by,
            output_digest,
            content.record_id,
        ),
        logical_id=logical_id,
        idempotency_key=idempotency_key,
        fingerprint_sha256=fingerprint_sha256,
    )


def append_provider_decision_authority(
    bridge: ArtifactBridge,
    payload: AgentResultPayload | ReviewPayload,
    *,
    logical_id: str,
    idempotency_key: str,
    fingerprint_sha256: str,
    operation: str,
):
    role = payload.role if isinstance(payload, AgentResultPayload) else payload.reviewer
    canonical = (
        f"request={payload.request_id};result={logical_id};role={role.value}"
    ).encode("utf-8")
    blob = bridge.store.put_blob(canonical)
    bound = replace(payload, response_sha256=blob.sha256)
    prior_operation = next(
        (
            record.payload.step
            for record in reversed(bridge.store.load_chain())
            if isinstance(record.payload, WorkflowTransitionPayload)
            and record.payload.work_unit_id == payload.work_unit_id
            and record.payload.step is not None
        ),
        operation,
    )
    round_number = next(
        (
            record.payload.round_number
            for record in reversed(bridge.store.load_chain())
            if isinstance(
                record.payload,
                (WorkUnitPayload, CorrectionWorkUnitPayload),
            )
            and record.logical_id == f"work-unit-{payload.work_unit_id}"
        ),
        1,
    )
    bridge.append(
        ProviderContentPayload(
            role,
            payload.work_unit_id,
            round_number,
            prior_operation,
            payload.request_id,
            blob.sha256,
            "review_result" if role is Role.CLAUDE else "agent_result",
            blob.bytes,
            blob,
        ),
        logical_id=f"provider-content-{role.value}-{payload.work_unit_id}-{blob.sha256[:12]}",
        idempotency_key=f"content:{idempotency_key}",
        fingerprint_sha256=fingerprint_sha256,
    )
    decision_logical_id = (
        f"agent-{payload.work_unit_id}-{operation}-{round_number}"
        if isinstance(payload, AgentResultPayload)
        else f"review-{role.value}-{payload.work_unit_id}-{round_number}"
    )
    return bridge.append(
        bound,
        logical_id=decision_logical_id,
        idempotency_key=idempotency_key,
        fingerprint_sha256=fingerprint_sha256,
    )
