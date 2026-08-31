from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import statistics
import time

import pytest

from agent_runtime import OrchestratorConfig
from artifact_bridge import ArtifactBridge
from artifact_models import (
    AgentResultPayload,
    CommandSpec,
    ProviderContentPayload,
    ReviewPacketPayload,
    Role,
    RoleProfilePayload,
    RunIdentityPayload,
    RunProfilePayload,
    FingerprintKind,
    ValidationAttestationPayload,
    ValidationContentPayload,
    ValidationResult,
    provider_text_evidence,
)
from artifact_replay import ArtifactReplayError, replay_artifacts
from artifact_store import ArtifactCorruptionError, ArtifactStore
from content_authority import (
    VALIDATION_MATRIX_DIGEST_V1,
    ValidationCapture,
    validation_output_digest,
)
from contracts import (
    ValidationAttestation,
    ValidationCommandSpec,
    ValidationRecord,
    ValidationStatus,
)
from orchestrator import ProductionWorkflowDriver
from review_packets import build_review_packet
from workflow_state import init_workflow_state


FINGERPRINT = "a" * 64
PLAN = """# Plan

### Slice 1 - Content authority

**Ziel**

Bind large content outside the record envelope.

#### """ + "Akzep" + "tanzkriterien" + """

- The record binds the exact external bytes.
"""


def _bind_store(store: ArtifactStore) -> None:
    if store.load_chain():
        return
    bridge = ArtifactBridge(store)
    bridge.append(
        RunIdentityPayload(
            "inbox/backlog/content-authority.md",
            "feature/content-authority",
            "b" * 40,
            "IMPLEMENT",
            None,
        ),
        logical_id="run-identity",
        idempotency_key="run-identity",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    bridge.append(
        RunProfilePayload(
            RoleProfilePayload("implementer-model", "medium"),
            RoleProfilePayload("reviewer-model", "high"),
        ),
        logical_id="run-profile",
        idempotency_key="run-profile",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )


def _validation_driver(store: ArtifactStore) -> ProductionWorkflowDriver:
    _bind_store(store)
    driver = ProductionWorkflowDriver(
        repository_root=store.repository_root,
        state_file=store.repository_root / ".orchestrator" / "state.json",
        agents={},
        config=OrchestratorConfig(repo_root=store.repository_root),
        allowed_roots=(store.repository_root,),
    )
    driver._artifact_bridge = ArtifactBridge(store)  # noqa: SLF001
    driver.active_state = init_workflow_state(
        run_id=store.run_id,
        task_file=str(store.repository_root / "task.md"),
        branch="feature/content-authority",
        branch_base="b" * 40,
        slice_count=1,
    )
    return driver


def _attestation_for_packet() -> ValidationAttestation:
    return ValidationAttestation(
        attestation_id="validation-packet",
        diff_fingerprint=FINGERPRINT,
        expected_commands=("pytest",),
        records=(ValidationRecord(ValidationStatus.PASS, "pytest", 0, "ok"),),
        output_digest="b" * 64,
        summary="validation passed",
    )


def _large_packet(target_bytes: int):
    line = "+" + ("x" * 62) + "\n"
    line_count = max(1, target_bytes // len(line.encode("utf-8")))
    diff = (
        "diff --git a/src/large.py b/src/large.py\n"
        "new file mode 100644\n"
        "index 0000000..1111111\n"
        "--- /dev/null\n"
        "+++ b/src/large.py\n"
        f"@@ -0,0 +1,{line_count} @@\n"
        + (line * line_count)
    )
    return build_review_packet(
        purpose="slice",
        fingerprint=FINGERPRINT,
        start_fingerprint="0" * 64,
        paths=("src/large.py",),
        review_diff=diff,
        plan_text=PLAN,
        slice_id=1,
        attestation=_attestation_for_packet(),
        findings=(),
    )


def _append_review_packet(store: ArtifactStore, packet) -> None:  # type: ignore[no-untyped-def]
    _bind_store(store)
    blob = store.put_blob(packet.canonical_bytes)
    ArtifactBridge(store).append(
        ReviewPacketPayload(
            work_unit_id="1",
            fingerprint=packet.fingerprint,
            purpose=packet.purpose,
            manifest=packet.manifest.paths,
            diff_coverage_sha256=packet.manifest.diff_coverage_digest,
            content_bytes=blob.bytes,
            blob=blob,
        ),
        logical_id=f"review-packet-1-{packet.fingerprint[:12]}",
        idempotency_key=f"review-packet:1:{packet.digest}",
        fingerprint_sha256=packet.fingerprint,
    )


def test_validation_content_preserves_exact_streams_and_pre_r8_digest(
    tmp_path: Path,
) -> None:
    command = "python3 -m pytest tests/ -v"
    stdout = "first line\n" + ("full validation evidence\n" * 32)
    stderr = "one warning on stderr\n"
    compact = "first line … one warning on stderr"
    capture = ValidationCapture(command, "pass", 0, stdout, stderr, compact)
    pre_r8_document = [
        {
            "command": command,
            "outcome": "pass",
            "exit_code": 0,
            "stdout": stdout,
            "stderr": stderr,
        }
    ]
    pre_r8_digest = hashlib.sha256(
        json.dumps(
            pre_r8_document,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert validation_output_digest((capture,)) == pre_r8_digest

    attestation = ValidationAttestation(
        attestation_id="validation-exact-content",
        diff_fingerprint=FINGERPRINT,
        expected_commands=(command,),
        records=(ValidationRecord(ValidationStatus.PASS, command, 0, compact),),
        output_digest=pre_r8_digest,
        summary="exact streams captured",
        command_specs=(
            ValidationCommandSpec(
                argv=("python3", "-m", "pytest", "tests/", "-v")
            ),
        ),
        content_captures=(capture,),
        content_digest_format=VALIDATION_MATRIX_DIGEST_V1,
    )
    store = ArtifactStore(tmp_path, "validation-content")
    driver = _validation_driver(store)
    driver.persist_validation_attestation(attestation)

    replay = replay_artifacts(store.load_chain(), store.run_id)
    assert len(replay.validation_contents) == 1
    content = replay.validation_contents[0]
    assert isinstance(content, ValidationContentPayload)
    output = content.outputs[0]
    assert store.read_blob(output.raw_stdout) == stdout.encode("utf-8")
    assert store.read_blob(output.raw_stderr) == stderr.encode("utf-8")
    assert store.read_blob(output.compact_output) == compact.encode("utf-8")
    assert output.output_bytes == len(stdout.encode()) + len(stderr.encode())
    assert content.output_digest == pre_r8_digest


def test_accepted_provider_content_is_exact_but_failure_text_remains_redacted(
    tmp_path: Path,
) -> None:
    canonical = json.dumps(
        {
            "schema_version": "native-agent-codex-result-v2",
            "result_type": "final_report_result",
            "request_id": "native-codex-request-" + ("b" * 64),
            "ready": True,
            "finding_dispositions": [],
            "self_check": "accepted provider bytes with a unique sentinel",
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    store = ArtifactStore(tmp_path, "provider-content")
    driver = object.__new__(ProductionWorkflowDriver)
    driver._artifact_bridge = ArtifactBridge(store)  # noqa: SLF001
    record = driver._persist_provider_content(  # noqa: SLF001
        role=Role.CODEX,
        work_unit_id=1,
        round_number=1,
        operation="codex_final_review",
        request_id="native-codex-request-" + ("b" * 64),
        canonical=canonical,
        content_kind="final_report",
        fingerprint=FINGERPRINT,
    )

    payload = record.payload
    assert isinstance(payload, ProviderContentPayload)
    assert store.read_blob(payload.blob) == canonical.encode("utf-8")
    record_envelope = next(store.records_dir.glob("*.json")).read_text("utf-8")
    assert "accepted provider bytes with a unique sentinel" not in record_envelope
    assert payload.content_bytes == len(canonical.encode("utf-8"))

    marker, digest, byte_count = provider_text_evidence(canonical)
    assert canonical not in marker
    assert digest == payload.response_sha256
    assert byte_count == payload.content_bytes
    assert marker.startswith("[provider text redacted;")


@pytest.mark.parametrize("mutation", ("missing", "tampered"))
def test_external_blob_absence_or_tampering_is_fail_closed(
    tmp_path: Path, mutation: str
) -> None:
    store = ArtifactStore(tmp_path, f"blob-{mutation}")
    packet = _large_packet(16 * 1024)
    _append_review_packet(store, packet)
    record = next(
        item for item in store.load_chain() if isinstance(item.payload, ReviewPacketPayload)
    )
    payload = record.payload
    assert isinstance(payload, ReviewPacketPayload)
    blob_path = store.blobs_dir / f"{payload.blob.sha256}.blob"
    if mutation == "missing":
        blob_path.unlink()
    else:
        blob_path.write_bytes(b"x" * payload.blob.bytes)

    with pytest.raises(ArtifactCorruptionError, match="blob"):
        ArtifactStore(tmp_path, store.run_id).load_chain()


def test_review_packet_record_size_stays_bounded_as_diff_content_grows(
    tmp_path: Path,
) -> None:
    targets = (64 * 1024, 256 * 1024, 1024 * 1024)
    packet_sizes: list[int] = []
    record_sizes: list[int] = []
    for target in targets:
        root = tmp_path / str(target)
        root.mkdir()
        store = ArtifactStore(root, f"review-packet-{target}")
        packet = _large_packet(target)
        _append_review_packet(store, packet)
        packet_sizes.append(len(packet.canonical_bytes))
        record_sizes.append(next(store.records_dir.glob("*.json")).stat().st_size)
        replay = replay_artifacts(
            ArtifactStore(root, store.run_id).load_chain(), store.run_id
        )
        assert replay.review_packets[0].content_bytes == packet_sizes[-1]

    def assert_externalized(sizes: list[int]) -> None:
        assert packet_sizes[-1] > packet_sizes[0] * 12
        assert max(sizes) - min(sizes) < 32
        assert max(sizes) < packet_sizes[0] // 20

    assert_externalized(record_sizes)
    with pytest.raises(AssertionError):
        assert_externalized(
            [record_size + packet_size for record_size, packet_size in zip(
                record_sizes, packet_sizes, strict=True
            )]
        )


def test_review_packet_fullscan_reads_each_blob_once_with_linear_byte_cost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = (64 * 1024, 256 * 1024, 1024 * 1024)
    stores: list[ArtifactStore] = []
    expected_bytes: dict[str, int] = {}
    for target in targets:
        root = tmp_path / f"scan-{target}"
        root.mkdir()
        store = ArtifactStore(root, f"review-scan-{target}")
        packet = _large_packet(target)
        _append_review_packet(store, packet)
        stores.append(store)
        expected_bytes[store.run_id] = len(packet.canonical_bytes)

    original_read_blob = ArtifactStore.read_blob
    bytes_read: dict[str, int] = {store.run_id: 0 for store in stores}

    def measured_read_blob(store: ArtifactStore, reference):  # type: ignore[no-untyped-def]
        content = original_read_blob(store, reference)
        bytes_read[store.run_id] += len(content)
        return content

    monkeypatch.setattr(ArtifactStore, "read_blob", measured_read_blob)
    durations: list[float] = []
    for store in stores:
        samples: list[float] = []
        for _ in range(5):
            bytes_read[store.run_id] = 0
            started = time.perf_counter()
            ArtifactStore(store.repository_root, store.run_id).load_chain()
            samples.append(time.perf_counter() - started)
            assert bytes_read[store.run_id] == expected_bytes[store.run_id]
        durations.append(statistics.median(samples))

    size_ratio = expected_bytes[stores[-1].run_id] / expected_bytes[stores[0].run_id]
    time_ratio = durations[-1] / durations[0]
    exponent = math.log(time_ratio) / math.log(size_ratio)
    assert exponent < 1.25


def test_provider_content_recovery_is_bound_to_the_exact_round_without_decision(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path, "provider-round-recovery")
    driver = object.__new__(ProductionWorkflowDriver)
    driver._artifact_bridge = ArtifactBridge(store)  # noqa: SLF001
    request_id = "native-codex-request-" + "b" * 64
    canonical = '{"request_id":"' + request_id + '","ready":true}'
    for round_number in (1, 2):
        driver._persist_provider_content(  # noqa: SLF001
            role=Role.CODEX,
            work_unit_id=7,
            round_number=round_number,
            operation="codex_implementation",
            request_id=request_id,
            canonical=canonical,
            content_kind="agent_result",
            fingerprint=FINGERPRINT,
        )

    recovered = driver._provider_content_text(  # noqa: SLF001
        role=Role.CODEX,
        work_unit_id=7,
        round_number=2,
        operation="codex_implementation",
    )

    assert recovered is not None
    assert recovered[0] == canonical
    assert isinstance(recovered[1].payload, ProviderContentPayload)
    assert recovered[1].payload.round_number == 2


def test_validation_recovery_never_reuses_an_earlier_attempt(
    tmp_path: Path,
) -> None:
    command = ValidationCommandSpec(argv=("pytest",))
    capture = ValidationCapture("pytest", "pass", 0, "ok", "", "ok")

    def attestation(attestation_id: str) -> ValidationAttestation:
        return ValidationAttestation(
            attestation_id=attestation_id,
            diff_fingerprint=FINGERPRINT,
            expected_commands=("pytest",),
            records=(
                ValidationRecord(ValidationStatus.PASS, "pytest", 0, "ok"),
            ),
            output_digest=validation_output_digest((capture,)),
            summary="one passed",
            command_specs=(command,),
            content_captures=(capture,),
            content_digest_format=VALIDATION_MATRIX_DIGEST_V1,
        )

    driver = _validation_driver(
        ArtifactStore(tmp_path, "validation-attempt-recovery")
    )
    first = attestation(f"validation-{FINGERPRINT[:12]}")
    second_id = f"validation-{FINGERPRINT[:12]}-retry-2"
    driver.persist_validation_attestation(first)

    assert driver.recover_pending_validation_attestation(
        FINGERPRINT, ("pytest",), second_id
    ) is None

    second = attestation(second_id)
    driver.persist_validation_attestation(second)
    assert driver.recover_pending_validation_attestation(
        FINGERPRINT, ("pytest",), second_id
    ) == second


def test_structured_replay_rejects_pre_r8_attestation_without_content(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path, "pre-r8-chain")
    _bind_store(store)
    ArtifactBridge(store).append(
        ValidationAttestationPayload(
            results=(
                ValidationResult(
                    CommandSpec("validation", ("pytest",)),
                    "pass",
                    0,
                    hashlib.sha256(b"ok").hexdigest(),
                ),
            ),
            attested_by=Role.ORCHESTRATOR,
            output_digest=hashlib.sha256(b"ok").hexdigest(),
            content_record_id="ar1-" + "0" * 64,
        ),
        logical_id="validation-pre-r8",
        idempotency_key="attestation:pre-r8",
        fingerprint_sha256=FINGERPRINT,
    )

    with pytest.raises(ArtifactReplayError, match="authoritative content"):
        replay_artifacts(store.load_chain(), store.run_id, require_content_authority=True)
