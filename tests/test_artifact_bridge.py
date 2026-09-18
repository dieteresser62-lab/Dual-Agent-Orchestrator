from __future__ import annotations

from dataclasses import replace
import math
from pathlib import Path
import time

import pytest

import artifact_store as artifact_store_module

from artifact_bridge import (
    ArtifactBridge, ArtifactBridgeError, attestation_payload, command_payload,
    finding_payload, review_payload, review_payload_matches_complete_result,
    review_payload_matches_result,
    validation_request_payload,
    finding_handoff_export_payload, finding_handoff_import_payload,
)
from artifact_models import (
    ArtifactRecord, ArtifactValidationError, CorrectionWorkUnitPayload,
    FindingSeverity, FindingTransitionPayload,
    Fingerprint, FingerprintKind, PlanPayload, ReviewPayload, SliceSpec,
    ProviderAttemptPayload, ProviderInputComponentPayload,
    ProviderInputMeasurementPayload, ProviderUsagePayload, Role, RoleProfilePayload,
    RunIdentityPayload, RunProfilePayload, WorkUnitPayload,
)
from artifact_store import ArtifactStore
from artifact_replay import ArtifactReplayError, replay_artifacts
from contracts import (
    AgentRole, ContractResult, FindingClass, FindingOrigin, FindingRecord,
    FindingStatus, ReviewEvidence, ValidationAttestation, ValidationCommandSpec,
    ValidationRecord, ValidationStatus,
)
from validation_matrix import ValidationCommand, ValidationRequest


DIGEST = "a" * 64


def _bound_bridge(
    root: Path,
    run_id: str,
    *,
    now=None,  # type: ignore[no-untyped-def]
) -> ArtifactBridge:
    store = ArtifactStore(root, run_id)
    setup = ArtifactBridge(store, now=lambda: "2026-08-18T09:00:00+00:00")
    setup.append(
        RunIdentityPayload("task.md", "feature/test", "b" * 40, "b" * 40, "IMPLEMENT", None),
        logical_id="run-identity",
        idempotency_key="run-identity",
        fingerprint_sha256=DIGEST,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    setup.append(
        RunProfilePayload(
            RoleProfilePayload("implementer-model", "medium"),
            RoleProfilePayload("reviewer-model", "high"),
        ),
        logical_id="run-profile",
        idempotency_key="run-profile",
        fingerprint_sha256=DIGEST,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    return ArtifactBridge(store) if now is None else ArtifactBridge(store, now=now)


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
    append(
        "run-identity",
        RunIdentityPayload("task.md", "feature/test", "b" * 40, "b" * 40, "PLAN_ONLY", None),
    )
    append(
        "run-profile",
        RunProfilePayload(
            RoleProfilePayload("implementer-model", "medium"),
            RoleProfilePayload("reviewer-model", "high"),
        ),
    )
    append("plan", PlanPayload("docs/plan.md", "b" * 40, (SliceSpec("1", "one", ("src/a.py",)),)))
    finding = append("finding-C-01", FindingTransitionPayload(
        "C-01", Role.CLAUDE, Role.CLAUDE, "opened", FindingSeverity.OBSERVATION,
        "open", "Carry it.", "plan-review", "Carry it.", "It remains visible.", "plan", 1,
    ))
    review = append("review", ReviewPayload(
        Role.CLAUDE, "plan-review", "approved", ("C-01",), None,
        "native-claude-review-v2", "native-review-request-" + "c" * 64, "d" * 64,
    ))
    before_export = replay_artifacts(
        records,
        "source-run",
        require_content_authority=False,
        require_review_authority=False,
    )
    export_payload = finding_handoff_export_payload(
        before_export, approved_plan_commit="b" * 40,
        approval_review_record_id=review.record_id,
        target_task_path="inbox/implement.md", target_task_bytes=b"task",
    )
    export_record = append("finding-export", export_payload)
    source = replay_artifacts(
        records,
        "source-run",
        require_content_authority=False,
        require_review_authority=False,
    )
    imported = finding_handoff_import_payload(
        source, export_record, target_run_id="target-run", target_task_bytes=b"task"
    )

    assert imported.transitions[0].payload == finding.payload
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


def test_bridge_idempotency_survives_complete_append_index_loss(tmp_path: Path) -> None:
    calls = 0

    def now() -> str:
        nonlocal calls
        calls += 1
        return "2026-08-18T10:00:00+00:00"

    store = ArtifactStore(tmp_path, "run-1")
    bridge = ArtifactBridge(store, now=now)
    payload = WorkUnitPayload("03", 1, ("src/a.py",))
    first = bridge.append(
        payload, logical_id="work-3", idempotency_key="work:3",
        fingerprint_sha256=DIGEST,
    )
    store._append_index = None
    store.head_path.unlink()

    retried = bridge.append(
        payload, logical_id="work-3", idempotency_key="work:3",
        fingerprint_sha256=DIGEST,
    )

    assert retried == first
    assert calls == 1


def test_bridge_recovers_record_after_durable_append_reports_cache_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = ArtifactStore(tmp_path, "run-1")
    bridge = ArtifactBridge(store, now=lambda: "2026-08-18T10:00:00+00:00")
    refresh = store._refresh_append_head_cache
    calls = 0

    def publish_cache_then_fail(index, prior_document) -> None:  # type: ignore[no-untyped-def]
        nonlocal calls
        refresh(index, prior_document)
        calls += 1
        if calls == 1:
            raise OSError("simulated reported failure after durable publication")

    monkeypatch.setattr(store, "_refresh_append_head_cache", publish_cache_then_fail)
    payload = WorkUnitPayload("03", 1, ("src/a.py",))

    persisted = bridge.append(
        payload, logical_id="work-3", idempotency_key="work:3",
        fingerprint_sha256=DIGEST,
    )

    assert calls == 1
    assert store.load_chain() == (persisted,)


def test_append_elapsed_cost_does_not_follow_quadratic_full_scan_curve(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Measure wall time and retain a quadratic full-scan control mutation."""
    original_read = artifact_store_module._read_record

    def validation_cost(path: Path):  # type: ignore[no-untyped-def]
        # Make the real record read/schema-validation work dominate scheduler
        # and fsync noise without replacing it with an invocation counter.
        time.sleep(0.002)
        return original_read(path)

    monkeypatch.setattr(artifact_store_module, "_read_record", validation_cost)
    lengths = (12, 24, 48)

    def measure(label: str, *, control_full_scan: bool) -> tuple[float, ...]:
        timings: list[float] = []
        for length in lengths:
            root = tmp_path / f"{label}-{length}"
            root.mkdir()
            bridge = ArtifactBridge(
                ArtifactStore(root, f"run-{label}-{length}"),
                now=lambda: "2026-08-18T10:00:00+00:00",
            )
            started = time.perf_counter()
            for number in range(length):
                if control_full_scan:
                    bridge.store.load_chain()
                bridge.append(
                    WorkUnitPayload(str(number), 1, ("src/a.py",)),
                    logical_id=f"work-{number}",
                    idempotency_key=f"work:{number}",
                    fingerprint_sha256=DIGEST,
                )
            timings.append(time.perf_counter() - started)
        return tuple(timings)

    optimized = measure("optimized", control_full_scan=False)
    quadratic_control = measure("quadratic-control", control_full_scan=True)
    scale = math.log(lengths[-1] / lengths[0])
    optimized_exponent = math.log(optimized[-1] / optimized[0]) / scale
    full_scan_cost = tuple(
        controlled - baseline
        for baseline, controlled in zip(optimized, quadratic_control, strict=True)
    )
    assert all(cost > 0 for cost in full_scan_cost), (
        "timing control did not add measurable full-scan cost: "
        f"optimized={dict(zip(lengths, optimized))}, "
        f"control={dict(zip(lengths, quadratic_control))}"
    )
    control_exponent = math.log(
        full_scan_cost[-1] / full_scan_cost[0]
    ) / scale

    assert optimized_exponent < 1.65, (
        f"append wall-time scaled too steeply: {dict(zip(lengths, optimized))}, "
        f"exponent={optimized_exponent:.2f}"
    )
    assert control_exponent > 1.65, (
        "timing control did not expose the deliberately restored full scan: "
        f"control={dict(zip(lengths, quadratic_control))}, "
        f"marginal={dict(zip(lengths, full_scan_cost))}, "
        f"exponent={control_exponent:.2f}"
    )


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


def test_bridge_rejects_scope_narrowing_before_record_publication(
    tmp_path: Path,
) -> None:
    bridge = _bound_bridge(tmp_path, "run-scope-narrowing")
    first = bridge.append(
        WorkUnitPayload("3", 1, ("src/a.py", "tests/a.py")),
        logical_id="work-unit-3",
        idempotency_key="work-unit:3:round:1",
        fingerprint_sha256=DIGEST,
    )
    before = bridge.store.load_chain()
    record_names = tuple(path.name for path in bridge.store.records_dir.glob("*.json"))

    with pytest.raises(ArtifactReplayError, match="does not extend scope"):
        bridge.append(
            WorkUnitPayload("3", 1, ("src/a.py",)),
            logical_id="work-unit-3",
            idempotency_key="work-unit:3:round:1:revision:2",
            fingerprint_sha256=DIGEST,
        )

    assert tuple(path.name for path in bridge.store.records_dir.glob("*.json")) == record_names
    assert bridge.store.load_chain() == before
    assert replay_artifacts(before, bridge.store.run_id).head_record_id == first.record_id


def test_bridge_accepts_scope_extension_within_the_same_round(tmp_path: Path) -> None:
    bridge = _bound_bridge(tmp_path, "run-scope-extension")
    bridge.append(
        WorkUnitPayload("3", 1, ("src/a.py",)),
        logical_id="work-unit-3",
        idempotency_key="work-unit:3:round:1",
        fingerprint_sha256=DIGEST,
    )

    extended = bridge.append(
        WorkUnitPayload("3", 1, ("src/a.py", "tests/a.py")),
        logical_id="work-unit-3",
        idempotency_key="work-unit:3:round:1:revision:2",
        fingerprint_sha256=DIGEST,
    )

    assert extended.revision == 2
    assert replay_artifacts(
        bridge.store.load_chain(), bridge.store.run_id
    ).head_record_id == extended.record_id


def test_bridge_rejects_scope_change_in_a_new_round_before_publication(
    tmp_path: Path,
) -> None:
    bridge = _bound_bridge(tmp_path, "run-cross-round-scope-change")
    bridge.append(
        CorrectionWorkUnitPayload("3", 1, ("src/a.py",), ("C-01",)),
        logical_id="work-unit-3",
        idempotency_key="correction-work-unit:3:round:1",
        fingerprint_sha256=DIGEST,
    )
    before = bridge.store.load_chain()
    record_names = tuple(path.name for path in bridge.store.records_dir.glob("*.json"))

    with pytest.raises(ArtifactReplayError, match="within the same round"):
        bridge.append(
            CorrectionWorkUnitPayload(
                "3", 2, ("src/a.py", "tests/a.py"), ("C-01",)
            ),
            logical_id="work-unit-3",
            idempotency_key="correction-work-unit:3:round:2",
            fingerprint_sha256=DIGEST,
        )

    assert tuple(path.name for path in bridge.store.records_dir.glob("*.json")) == record_names
    assert bridge.store.load_chain() == before
    replay_artifacts(before, bridge.store.run_id)


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
    assert attestation_payload(
        attestation, "ar1-" + "0" * 64
    ).results[0].command.argv == spec.argv


def test_legacy_attestation_does_not_guess_argv() -> None:
    display = "python3 -m pytest 'a b.py'"
    attestation = ValidationAttestation(
        attestation_id="legacy-a", diff_fingerprint=DIGEST,
        expected_commands=(display,),
        records=(ValidationRecord(ValidationStatus.PASS, display, 0, "ok"),),
        output_digest="b" * 64, summary="passed",
    )
    command = attestation_payload(attestation, "ar1-" + "0" * 64).results[0].command
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

    payload = attestation_payload(attestation, "ar1-" + "0" * 64)
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


def test_review_evidence_roundtrips_losslessly_through_the_record_store(
    tmp_path: Path,
) -> None:
    result = ContractResult(
        reviewer=AgentRole.CLAUDE,
        approval=True,
        stopped=False,
        stop_request=None,
        validation=None,
        test_files=(),
        pre_mortem="A replay implementation may accidentally invoke Claude twice.",
        evidence=ReviewEvidence(
            "correctness | failure paths",
            "a cache | mirror disagreement",
            "the record | state binding diverges",
        ),
        findings=(),
        anchors=(),
        red_state_followup_slice="Slice 12",
    )
    bridge = ArtifactBridge(
        ArtifactStore(tmp_path, "run-evidence"),
        now=lambda: "2026-08-30T10:00:00+00:00",
    )
    written = bridge.append(
        review_payload(
            result,
            work_unit_id=1,
            transport_schema="native-claude-review-v2",
            request_id=f"native-review-request-{'b' * 64}",
            response_sha256="c" * 64,
        ),
        logical_id="review-claude-1-1",
        idempotency_key="review-evidence-roundtrip",
        fingerprint_sha256=DIGEST,
    )

    loaded = bridge.store.load_chain()[0]

    assert loaded == written
    assert isinstance(loaded.payload, ReviewPayload)
    assert loaded.payload.evidence is None
    assert loaded.payload.review_evidence is not None
    assert loaded.payload.review_evidence.dimensions == "correctness | failure paths"
    assert (
        loaded.payload.review_evidence.largest_residual_risk
        == "a cache | mirror disagreement"
    )
    assert (
        loaded.payload.review_evidence.break_condition
        == "the record | state binding diverges"
    )
    assert loaded.payload.red_state_followup_slice == "Slice 12"


def test_legacy_review_comparison_keeps_ambiguous_evidence_opaque() -> None:
    result = ContractResult(
        reviewer=AgentRole.CLAUDE,
        approval=False,
        stopped=False,
        stop_request=None,
        validation=None,
        test_files=(),
        pre_mortem=None,
        evidence=ReviewEvidence(
            "first | embedded",
            "second",
            "third",
        ),
        findings=(),
        anchors=(),
    )
    legacy = ReviewPayload(
        Role.CLAUDE,
        "1",
        "denied",
        (),
        "first | embedded | second | third",
        "native-claude-review-v2",
        f"native-review-request-{'b' * 64}",
        "c" * 64,
    )

    assert review_payload_matches_result(legacy, result)
    assert not review_payload_matches_result(
        legacy,
        replace(
            result,
            evidence=ReviewEvidence(
                "first | embedded",
                "second",
                "changed",
            ),
        ),
    )


def test_request_bound_review_payload_matches_only_its_complete_ledger_projection() -> None:
    carried = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.OBSERVATION,
        status=FindingStatus.CLOSED,
        summary="A prior finding remains in the complete ledger.",
        acceptance_test="The compact review need not receive it again.",
        origin=FindingOrigin("01", 1, AgentRole.CLAUDE),
        status_rationale="Closed before this review.",
    )
    reviewed = FindingRecord(
        finding_id="C-79",
        finding_class=FindingClass.OBSERVATION,
        status=FindingStatus.OPEN,
        summary="The compact request includes this finding.",
        acceptance_test="The request-bound payload names C-79.",
        origin=FindingOrigin("35", 1, AgentRole.CLAUDE),
    )
    request_result = ContractResult(
        reviewer=AgentRole.CLAUDE,
        approval=True,
        stopped=False,
        stop_request=None,
        validation=None,
        test_files=(),
        pre_mortem="A complete-ledger comparison could mistake carried findings for drift.",
        evidence=ReviewEvidence(
            "request-bound review and complete-ledger projection",
            "a carried finding is compared as reviewer output",
            "the commit rejects an otherwise bound compact review",
        ),
        findings=(reviewed,),
        anchors=(),
    )
    payload = review_payload(
        request_result,
        work_unit_id=36,
        transport_schema="native-claude-review-v2",
        request_id=f"native-review-request-{'b' * 64}",
        response_sha256="c" * 64,
    )
    complete_result = replace(request_result, findings=(carried, reviewed))

    assert not review_payload_matches_result(payload, complete_result)
    assert review_payload_matches_complete_result(payload, complete_result)
    assert not review_payload_matches_complete_result(
        payload,
        replace(complete_result, findings=(carried,)),
    )
    assert not review_payload_matches_complete_result(
        payload,
        replace(complete_result, approval=False),
    )


def test_legacy_lexical_review_payload_matches_natural_result_projection() -> None:
    base = FindingRecord(
        finding_id="C-62",
        finding_class=FindingClass.OBSERVATION,
        status=FindingStatus.OPEN,
        summary="Historical finding.",
        acceptance_test="The chain remains replayable.",
        origin=FindingOrigin("42", 1, AgentRole.CLAUDE),
    )
    findings = tuple(
        replace(base, finding_id=finding_id)
        for finding_id in ("C-62", "C-71", "C-101", "C-105")
    )
    result = ContractResult(
        reviewer=AgentRole.CLAUDE,
        approval=False,
        stopped=False,
        stop_request=None,
        validation=None,
        test_files=(),
        pre_mortem="Legacy lexical order could block replay.",
        evidence=None,
        findings=findings,
        anchors=(),
    )
    payload = review_payload(
        result,
        work_unit_id=42,
        transport_schema="native-claude-review-v2",
        request_id=f"native-review-request-{'b' * 64}",
        response_sha256="c" * 64,
    )
    legacy = replace(
        payload,
        finding_ids=("C-101", "C-105", "C-62", "C-71"),
    )

    assert review_payload_matches_result(legacy, result)
    assert review_payload_matches_complete_result(legacy, result)
    assert not review_payload_matches_result(
        replace(legacy, finding_ids=("C-105", "C-101", "C-62", "C-71")),
        result,
    )


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
    bridge = _bound_bridge(tmp_path, "run-1", now=lambda: next(ticks))
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
    bridge = _bound_bridge(tmp_path, "run-open")
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
    bridge = _bound_bridge(tmp_path, "run-1")
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

    with pytest.raises(
        ArtifactBridgeError,
        match=(
            "field=input_digest first=cccccccccccc current=eeeeeeeeeeee"
        ),
    ):
        bridge.start_provider_attempt(
            measurement_record=changed_measurement,
            binding_fingerprint=DIGEST,
            work_unit_id="1",
        )


def test_provider_attempt_rejects_changed_binding_with_both_short_values(
    tmp_path: Path,
) -> None:
    bridge = _bound_bridge(tmp_path, "run-binding-change")
    measurement = bridge.append(
        _measurement(), logical_id="measurement-binding-1",
        idempotency_key="measurement:binding:1", fingerprint_sha256=DIGEST,
    )
    first = bridge.start_provider_attempt(
        measurement_record=measurement,
        binding_fingerprint=DIGEST,
        work_unit_id="1",
        operation_instance="round:1",
    )
    bridge.finish_provider_attempt(
        first, duration_seconds=1.0, failure_kind="runtime", usage=None,
    )

    with pytest.raises(
        ArtifactBridgeError,
        match=(
            "field=binding_fingerprint first=aaaaaaaaaaaa "
            "current=ffffffffffff"
        ),
    ):
        bridge.start_provider_attempt(
            measurement_record=measurement,
            binding_fingerprint="f" * 64,
            work_unit_id="1",
            operation_instance="round:1",
        )


def test_provider_attempt_rounds_have_distinct_immutable_bindings(tmp_path: Path) -> None:
    bridge = _bound_bridge(tmp_path, "run-rounds")
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
    bridge = _bound_bridge(tmp_path, "run-legacy-round")
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
    foreign_bridge = _bound_bridge(tmp_path, "foreign-run")
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
    bridge = _bound_bridge(tmp_path, "run-1")
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
