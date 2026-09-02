from __future__ import annotations

from dataclasses import asdict, replace
import json

import artifact_models
import pytest

from artifact_models import (
    AgentResultPayload,
    ArtifactRecord,
    ArtifactValidationError,
    BindingPayload,
    CommandSpec,
    CorrectionWorkUnitPayload,
    DiagnosticPayload,
    FindingSeverity,
    FindingTransitionPayload,
    FindingHandoffExportPayload,
    FindingHandoffImportPayload,
    ImportedFindingTransition,
    finding_transition_sequence_sha256,
    Fingerprint,
    FingerprintKind,
    GateDecisionPayload,
    GatePayload,
    GateTransitionPayload,
    InvocationFailurePayload,
    PlanPayload,
    QuotaPausePayload,
    ResumeCheckPayload,
    ReviewPayload,
    ReviewEvidencePayload,
    Role,
    RoleProfilePayload,
    RunIdentityPayload,
    RunProfilePayload,
    SliceBoundaryPayload,
    SliceSpec,
    TaskPayload,
    TransientRetryPayload,
    ValidationAttestationPayload,
    ValidationRequestPayload,
    ValidationResult,
    WorkUnitPayload,
    WorkflowCompletionPayload,
    WorkflowPolicyPayload,
    WorkflowTransitionPayload,
    ProviderInputComponentPayload,
    ProviderInputMeasurementPayload,
    ProviderAttemptPayload,
    ProviderUsagePayload,
    FinalReviewPreflightPayload,
    canonical_json,
    load_schema,
    validate_artifact_document,
    provider_text_evidence,
    technical_text_evidence,
)
from workflow_state import (
    GateReason,
    GateStatus,
    SliceStatus,
    WorkflowStep,
    WorkUnitStatus,
)


DIGEST = "a" * 64
CREATED_AT = "2026-08-18T10:30:00+00:00"
CODEX_REQUEST_ID = "native-codex-request-" + "b" * 64
CLAUDE_REQUEST_ID = "native-review-request-" + "b" * 64
PROVIDER_MARKER, PROVIDER_DIGEST, PROVIDER_BYTES = provider_text_evidence(
    "provider diagnostic"
)
TECHNICAL_MARKER, TECHNICAL_DIGEST, TECHNICAL_BYTES = technical_text_evidence(
    "technical diagnostic"
)


def test_run_profile_record_fields_are_role_keyed() -> None:
    profile = RunProfilePayload(
        RoleProfilePayload("implementer-model", "medium"),
        RoleProfilePayload("reviewer-model", "high"),
    )

    assert asdict(profile) == {
        "implementer": {"model": "implementer-model", "effort": "medium"},
        "reviewer": {"model": "reviewer-model", "effort": "high"},
        "reducer_version": "structured-v2-schema-2-state-v3-v1",
    }
    assert not {"codex", "claude"} & set(asdict(profile))


def _agent_result(
    work_unit_id: str = "work-01",
    outcome: str = "ready",
    test_files: tuple[str, ...] = (),
) -> AgentResultPayload:
    return AgentResultPayload(
        Role.CODEX,
        work_unit_id,
        outcome,
        test_files,
        "native-codex-v2",
        CODEX_REQUEST_ID,
        "c" * 64,
    )


def _review(
    work_unit_id: str = "work-01",
    verdict: str = "approved",
    finding_ids: tuple[str, ...] = (),
    evidence: str | None = "contracts checked",
) -> ReviewPayload:
    return ReviewPayload(
        Role.CLAUDE,
        work_unit_id,
        verdict,
        finding_ids,
        evidence,
        "native-claude-review-v2",
        CLAUDE_REQUEST_ID,
        "c" * 64,
    )


def _record(payload, *, revision: int = 1) -> ArtifactRecord:  # type: ignore[no-untyped-def]
    return ArtifactRecord.create(
        run_id="run-01",
        logical_id=f"logical-{payload.record_type.value}",
        revision=revision,
        fingerprint=Fingerprint(FingerprintKind.IMPLEMENTATION, DIGEST),
        predecessor_ids=(),
        created_at=CREATED_AT,
        idempotency_key=f"key-{payload.record_type.value}",
        payload=payload,
    )


@pytest.fixture(params=[
    RunIdentityPayload(
        "C:\\workspace\\inbox\\task.md",
        "feature/records",
        "legacy-base-ref",
        "IMPLEMENT",
        "docs/internal/task-audit.md",
    ),
    RunProfilePayload(
        RoleProfilePayload("gpt-5.6-sol", "max"),
        RoleProfilePayload("opus", "high"),
    ),
    WorkflowTransitionPayload(
        "1", "in_progress", "2", "codex_implementation", "in_progress"
    ),
    WorkflowPolicyPayload("2", 1, 4),
    SliceBoundaryPayload(
        "1",
        "b" * 40,
        (("src/a.py", "src/old-a.py"), ("tests/test_a.py",)),
        "c" * 64,
    ),
    TaskPayload("feature/records", ("src/a.py",), DIGEST),
    PlanPayload("docs/internal/plan.md", "b" * 40, (SliceSpec("1", "models", ("src/a.py",)),)),
    WorkUnitPayload("1", 1, ("src/a.py",)),
    CorrectionWorkUnitPayload("1", 2, ("src/a.py",), ("C-01",)),
    _agent_result(test_files=("tests/test_a.py",)),
    DiagnosticPayload(Role.CLAUDE, "work-01", 1, DIGEST, "malformed verdict"),
    _review(),
    FindingTransitionPayload("C-01", Role.CLAUDE, Role.CLAUDE, "opened", FindingSeverity.BLOCKER, "open", "broken"),
    ValidationRequestPayload((CommandSpec("pytest", ("python3", "-m", "pytest", "tests/a b.py")),), Role.ORCHESTRATOR),
    ValidationAttestationPayload(
        (ValidationResult(CommandSpec("pytest", ("pytest", "-q")), "pass", 0, DIGEST),),
        Role.ORCHESTRATOR,
        DIGEST,
        "ar1-" + "0" * 64,
    ),
    GatePayload("manual-plan", "approved", Role.USER, "explicit approval"),
    GateTransitionPayload(
        "work-01",
        "awaiting_user_decision",
        "test_change",
        "test approval required",
        DIGEST,
        ("tests/test_gate.py",),
        "claude_slice_review",
        DIGEST,
        ("tests/test_gate.py",),
    ),
    GateDecisionPayload(
        "work-01", "gate-record-01", ("tests/test_gate.py",),
        "claude_slice_review",
    ),
    BindingPayload("implementation_handoff", "ec40aa3", "attestation-01", ("review-claude",)),
    InvocationFailurePayload(
        "invocation-01", "run-01:work-01:claude_slice_review:claude",
        Role.CLAUDE, "network", "transient", "AGENT-INVOCATION",
        PROVIDER_MARKER, PROVIDER_DIGEST, PROVIDER_BYTES,
        TECHNICAL_MARKER, TECHNICAL_DIGEST, TECHNICAL_BYTES,
        "2026-08-18T11:30:00+00:00", "2026-08-18T11:30:00+00:00",
        "claude_slice_review", "1", "work-01", 3, None,
        None, None, None, "2026-08-18T11:30:05+00:00", 0, 5, 1, True,
        DIGEST,
    ),
    QuotaPausePayload(Role.CLAUDE, DIGEST, "2026-08-18T11:30:00Z"),
    TransientRetryPayload(Role.CLAUDE, DIGEST, "2026-08-18T11:30:05Z", 1),
    ResumeCheckPayload("head-01", DIGEST, "matched"),
    WorkflowCompletionPayload("completed", "binding-final"),
    ProviderInputMeasurementPayload(
        Role.CODEX, Role.CODEX, "codex_final_review", "work-01", DIGEST, "b" * 64,
        "c" * 64, "d" * 64, (ProviderInputComponentPayload("stdin_prompt", 3, 3),),
        3, 3, 10, 10, None, None, None, 10, 10, True, (), 0, 0, "stdin_prompt",
    ),
    ProviderAttemptPayload(
        Role.CODEX, Role.CODEX, "codex_final_review", "work-01",
        "provider-operation-01", DIGEST, "measurement-01", "c" * 64, 1,
        "succeeded", CREATED_AT, "2026-08-18T10:30:01+00:00", 1.0, None,
        ProviderUsagePayload(input_tokens=0, output_tokens=7, turns=1),
    ),
    FinalReviewPreflightPayload(
        Role.CODEX, Role.CODEX, "codex_final_review", "work-01", DIGEST, "b" * 64,
        "measurement-01", "passed", None, None, (), (), None,
    ),
])
def payload(request):  # type: ignore[no-untyped-def]
    return request.param


def test_every_record_family_roundtrips_through_model_and_schema(payload) -> None:  # type: ignore[no-untyped-def]
    record = _record(payload)
    encoded = record.canonical_json()
    decoded = json.loads(encoded)

    validate_artifact_document(decoded)
    restored = ArtifactRecord.from_dict(decoded)

    assert restored == record
    assert restored.canonical_json() == encoded


def test_invocation_failure_technical_evidence_is_redacted_and_exit_null_is_distinct_from_zero() -> None:
    raw = "secret stderr details"
    marker, digest, byte_count = technical_text_evidence(raw)
    payload = InvocationFailurePayload(
        invocation_id="invocation-process-diagnostic",
        idempotency_key="run-01:work-01:claude_slice_review:claude",
        role=Role.CLAUDE,
        failure_kind="process",
        failure_class="transient",
        diagnostic_code="AGENT-PROCESS",
        provider_text=PROVIDER_MARKER,
        provider_text_sha256=PROVIDER_DIGEST,
        provider_text_bytes=PROVIDER_BYTES,
        technical_text=marker,
        technical_text_sha256=digest,
        technical_text_bytes=byte_count,
        received_at="2026-09-01T18:30:00+00:00",
        decision_at_utc="2026-09-01T18:30:01+00:00",
        step="claude_slice_review",
        slice_id="1",
        work_unit_id="work-01",
        diagnostic_exit_code=3,
        process_exit_code=None,
        parse_path=None,
        source_timezone=None,
        reset_at_utc=None,
        resume_at_utc=None,
        safety_margin_seconds=0,
        retry_delay_seconds=0,
        auto_resume_count=0,
        automatic_resume=False,
        diff_fingerprint=DIGEST,
    )

    null_document = json.loads(_record(payload).canonical_json())
    zero_document = json.loads(
        _record(replace(payload, process_exit_code=0)).canonical_json()
    )
    assert null_document["payload"]["process_exit_code"] is None
    assert zero_document["payload"]["process_exit_code"] == 0
    assert raw not in json.dumps(null_document, sort_keys=True)
    with pytest.raises(ArtifactValidationError, match="technical text evidence"):
        replace(payload, technical_text=raw)
    raw_document = json.loads(json.dumps(null_document))
    raw_document["payload"]["technical_text"] = raw
    with pytest.raises(ArtifactValidationError, match="schema validation failed"):
        validate_artifact_document(raw_document)
    for required_field in (
        "process_exit_code",
        "technical_text",
        "technical_text_sha256",
        "technical_text_bytes",
    ):
        legacy_document = json.loads(json.dumps(null_document))
        legacy_document["payload"].pop(required_field)
        with pytest.raises(ArtifactValidationError, match="schema validation failed"):
            validate_artifact_document(legacy_document)


def test_slice_boundary_rejects_lossy_or_noncanonical_grouping() -> None:
    with pytest.raises(ArtifactValidationError, match="sorted and unique"):
        SliceBoundaryPayload(
            "1", "b" * 40, (("src/a.py",), ("src/a.py",)), "c" * 64
        )
    with pytest.raises(ArtifactValidationError, match="entries must be sorted"):
        SliceBoundaryPayload(
            "1", "b" * 40, (("src/z.py", "src/a.py"),), "c" * 64
        )


def test_native_codex_agent_result_roundtrips_with_closed_transport_binding() -> None:
    payload = AgentResultPayload(
        Role.CODEX,
        "work-01",
        "ready",
        ("tests/test_native_codex_contract.py",),
        transport_schema="native-codex-v2",
        request_id="native-codex-request-" + "b" * 64,
        response_sha256="c" * 64,
    )
    record = _record(payload)
    document = json.loads(record.canonical_json())

    validate_artifact_document(document)
    assert ArtifactRecord.from_dict(document) == record


def test_native_codex_agent_result_rejects_partial_or_foreign_bindings() -> None:
    payload = AgentResultPayload(
        Role.CODEX,
        "work-01",
        "ready",
        (),
        transport_schema="native-codex-v2",
        request_id="native-codex-request-" + "b" * 64,
        response_sha256="c" * 64,
    )

    with pytest.raises(ArtifactValidationError, match="response_sha256"):
        replace(payload, response_sha256=None)
    with pytest.raises(ArtifactValidationError, match="role=codex"):
        replace(payload, role=Role.CLAUDE)
    with pytest.raises(ArtifactValidationError, match="request_id"):
        replace(payload, request_id="native-codex-request-invalid")


def test_schema_is_bundled_and_self_contained() -> None:
    schema = load_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert not any("http" in ref for ref in _references(schema))


def test_schema_cache_never_exposes_mutable_authority() -> None:
    schema = load_schema()
    schema["title"] = "tampered caller copy"

    assert load_schema()["title"] != "tampered caller copy"


def test_record_vocabularies_stay_synced_with_state_v3() -> None:
    assert artifact_models._WORKFLOW_STEPS == {item.value for item in WorkflowStep}
    assert artifact_models._SLICE_STATUSES == {item.value for item in SliceStatus}
    assert artifact_models._WORK_UNIT_STATUSES == {
        item.value for item in WorkUnitStatus
    }
    assert artifact_models._GATE_STATUSES == {item.value for item in GateStatus}
    assert artifact_models._GATE_REASONS == {item.value for item in GateReason}


def test_gate_transition_schema_and_domain_reject_partial_active_test_binding() -> None:
    payload = GateTransitionPayload(
        "work-01", "clear", "none", None, None, (), None,
        DIGEST, ("tests/test_gate.py",),
    )
    raw = _record(payload).to_dict()
    raw["payload"]["active_test_fingerprint"] = None

    with pytest.raises(ArtifactValidationError, match="schema validation failed"):
        validate_artifact_document(raw)
    with pytest.raises(ArtifactValidationError, match="bound together"):
        replace(payload, active_test_fingerprint=None)


def _references(value):  # type: ignore[no-untyped-def]
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "$ref":
                yield child
            else:
                yield from _references(child)
    elif isinstance(value, list):
        for child in value:
            yield from _references(child)


@pytest.mark.parametrize("mutation", [
    lambda raw: raw.update(extra="unknown"),
    lambda raw: raw.pop("run_id"),
    lambda raw: raw.update(schema_version="1"),
    lambda raw: raw.update(record_type="invented"),
    lambda raw: raw["payload"].update(extra="unknown"),
    lambda raw: raw.update(status="approved"),
])
def test_closed_schema_rejects_unknown_missing_or_inconsistent_fields(mutation) -> None:  # type: ignore[no-untyped-def]
    raw = _record(TaskPayload("feature/records", ("src/a.py",), DIGEST)).to_dict()
    mutation(raw)
    with pytest.raises(ArtifactValidationError, match="schema validation failed"):
        validate_artifact_document(raw)


def test_paths_are_arrays_of_canonical_posix_paths() -> None:
    with pytest.raises(ArtifactValidationError, match="JSON-style list"):
        WorkUnitPayload("1", 1, "- src/a.py")  # type: ignore[arg-type]
    for invalid in ("/src/a.py", "src/../secret", "src\\a.py", "src//a.py", "./src/a.py"):
        with pytest.raises(ArtifactValidationError, match="path"):
            WorkUnitPayload("1", 1, (invalid,))


def test_command_arguments_preserve_boundaries_and_reject_shell_strings() -> None:
    command = CommandSpec("pytest", ("python3", "a b.py", 'x="quoted"', "ümlaut", "$(false)"))
    record = _record(ValidationRequestPayload((command,), Role.ORCHESTRATOR))
    assert json.loads(record.canonical_json())["payload"]["commands"][0]["argv"] == list(command.argv)

    with pytest.raises(ArtifactValidationError, match="argv"):
        CommandSpec("pytest", "python3 -m pytest")  # type: ignore[arg-type]

    legacy = CommandSpec("pytest", ("python3 -m pytest 'a b.py'",), "legacy_shell")
    assert legacy.mode == "legacy_shell"
    with pytest.raises(ArtifactValidationError, match="exactly one"):
        CommandSpec("pytest", ("pytest", "-q"), "legacy_shell")


def test_stable_id_binds_run_type_logical_identity_and_positive_revision() -> None:
    first = _record(WorkUnitPayload("1", 1, ("src/a.py",)))
    assert _record(WorkUnitPayload("1", 1, ("src/a.py",))).record_id == first.record_id
    assert _record(WorkUnitPayload("1", 1, ("src/a.py",)), revision=2).record_id != first.record_id
    with pytest.raises(ArtifactValidationError, match="positive integer"):
        _record(WorkUnitPayload("1", 1, ("src/a.py",)), revision=0)


def test_finding_ownership_and_codex_response_do_not_allow_foreign_closure() -> None:
    with pytest.raises(ArtifactValidationError, match="reporting reviewer"):
        FindingTransitionPayload("C-01", Role.CLAUDE, Role.CODEX, "status_changed", FindingSeverity.BLOCKER, "closed", "fixed")
    with pytest.raises(ArtifactValidationError, match="cannot close"):
        FindingTransitionPayload("C-01", Role.CLAUDE, Role.CODEX, "responded", FindingSeverity.BLOCKER, "closed", "fixed")


@pytest.mark.parametrize(
    "factory",
    (
        lambda: FindingTransitionPayload(
            "A-01",  # retirement-negative-control
            Role.CLAUDE,
            Role.CLAUDE,
            "opened",
            FindingSeverity.BLOCKER,
            "open",
            "retired namespace",
        ),
        lambda: _review(
            verdict="denied",
            finding_ids=("A-01",),  # retirement-negative-control
            evidence="retired namespace",
        ),
        lambda: CorrectionWorkUnitPayload(
            "01",
            2,
            ("src/a.py",),
            ("A-01",),  # retirement-negative-control
        ),
    ),
)
def test_v2_models_reject_retired_finding_namespace(factory) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ArtifactValidationError, match=r"canonical C-\* finding ID"):
        factory()


@pytest.mark.parametrize(
    ("record", "field"),
    (
        (
            _record(
                FindingTransitionPayload(
                    "C-01",
                    Role.CLAUDE,
                    Role.CLAUDE,
                    "opened",
                    FindingSeverity.BLOCKER,
                    "open",
                    "valid namespace",
                )
            ),
            "finding_id",
        ),
        (
            _record(
                _review(
                    verdict="denied",
                    finding_ids=("C-01",),
                    evidence="valid namespace",
                )
            ),
            "finding_ids",
        ),
        (
            _record(
                CorrectionWorkUnitPayload(
                    "01",
                    2,
                    ("src/a.py",),
                    ("C-01",),
                )
            ),
            "finding_ids",
        ),
    ),
)
def test_v2_schema_and_deserializer_reject_retired_finding_namespace(
    record: ArtifactRecord,
    field: str,
) -> None:
    assert ArtifactRecord.from_dict(record.to_dict()) == record
    raw = record.to_dict()
    raw["payload"][field] = "A-01" if field == "finding_id" else ["A-01"]  # retirement-negative-control

    with pytest.raises(ArtifactValidationError, match="schema validation failed"):
        validate_artifact_document(raw)
    with pytest.raises(ArtifactValidationError, match="schema validation failed"):
        ArtifactRecord.from_dict(raw)


@pytest.mark.parametrize(
    ("record", "field"),
    (
        (
            _record(
                FindingTransitionPayload(
                    "C-01",
                    Role.CLAUDE,
                    Role.CLAUDE,
                    "opened",
                    FindingSeverity.BLOCKER,
                    "open",
                    "valid namespace",
                )
            ),
            "finding_id",
        ),
        (
            _record(
                _review(
                    verdict="denied",
                    finding_ids=("C-01",),
                    evidence="valid namespace",
                )
            ),
            "finding_ids",
        ),
        (
            _record(
                CorrectionWorkUnitPayload(
                    "01",
                    2,
                    ("src/a.py",),
                    ("C-01",),
                )
            ),
            "finding_ids",
        ),
    ),
)
def test_v2_schema_and_deserializer_reject_finding_id_with_trailing_newline(
    record: ArtifactRecord,
    field: str,
) -> None:
    raw = record.to_dict()
    raw["payload"][field] = "C-01\n" if field == "finding_id" else ["C-01\n"]

    with pytest.raises(ArtifactValidationError, match="schema validation failed"):
        validate_artifact_document(raw)
    with pytest.raises(ArtifactValidationError, match="schema validation failed"):
        ArtifactRecord.from_dict(raw)


def test_structured_finding_transition_roundtrips_and_legacy_fields_stay_optional() -> None:
    structured = _record(
        FindingTransitionPayload(
            finding_id="C-01",
            reporter=Role.CLAUDE,
            actor=Role.CLAUDE,
            action="opened",
            severity=FindingSeverity.BLOCKER,
            finding_status="open",
            rationale="Replay must own the finding.",
            work_unit_id="7",
            summary="Replay must own the finding.",
            acceptance_test="The state mirror cannot change a native request.",
            origin_slice_id="01",
            origin_round_number=2,
        )
    )
    assert ArtifactRecord.from_dict(structured.to_dict()) == structured

    historical = _record(
        FindingTransitionPayload(
            "C-01",
            Role.CLAUDE,
            Role.CLAUDE,
            "opened",
            FindingSeverity.BLOCKER,
            "open",
            "Historical transition.",
        )
    ).to_dict()
    for key in (
        "work_unit_id",
        "summary",
        "acceptance_test",
        "origin_slice_id",
        "origin_round_number",
        "response_decision",
    ):
        historical["payload"].pop(key)
    assert ArtifactRecord.from_dict(historical).payload.work_unit_id is None


def test_finding_handoff_payloads_roundtrip_ordered_source_lifecycle() -> None:
    opened = ImportedFindingTransition(
        "ar1-" + "1" * 64,
        FindingTransitionPayload(
            "C-02", Role.CLAUDE, Role.CLAUDE, "opened",
            FindingSeverity.OBSERVATION, "open", "Observe it.", "plan-review",
            "Observe it.", "The follow-up preserves it.", "plan", 1,
        ),
    )
    response = ImportedFindingTransition(
        "ar1-" + "2" * 64,
        FindingTransitionPayload(
            "C-02", Role.CLAUDE, Role.CODEX, "responded",
            FindingSeverity.OBSERVATION, "open", "Addressed.", "plan-review",
            response_decision="accepted",
        ),
    )
    transitions = (opened, response)
    digest = finding_transition_sequence_sha256(transitions)
    export = _record(FindingHandoffExportPayload(
        "source-run", "ar1-" + "3" * 64, "4" * 40,
        "ar1-" + "5" * 64, tuple(item.record_id for item in transitions),
        digest, "inbox/implement.md", "6" * 64, Role.ORCHESTRATOR,
    ))
    imported = _record(FindingHandoffImportPayload(
        "source-run", "ar1-" + "3" * 64, "4" * 40,
        "ar1-" + "5" * 64, "ar1-" + "7" * 64, "run-01", "6" * 64,
        digest, transitions, Role.ORCHESTRATOR,
    ))

    assert ArtifactRecord.from_dict(export.to_dict()) == export
    assert ArtifactRecord.from_dict(imported.to_dict()) == imported
    with pytest.raises(ArtifactValidationError, match="digest does not match"):
        replace(imported.payload, transitions=tuple(reversed(transitions)))


def test_work_unit_finding_entry_binding_is_sorted_and_roundtrips() -> None:
    payload = WorkUnitPayload(
        "1", 1, ("src/a.py",), ("C-01", "C-02"), "ar1-" + "8" * 64
    )
    assert ArtifactRecord.from_dict(_record(payload).to_dict()).payload == payload
    with pytest.raises(ArtifactValidationError, match="must be sorted"):
        replace(payload, open_finding_ids=("C-02", "C-01"))


def test_approval_requires_fingerprint_and_positive_evidence() -> None:
    with pytest.raises(ArtifactValidationError, match="findings or review evidence"):
        _review(evidence=None)

    raw = _record(_review(evidence="checked")).to_dict()
    raw.pop("fingerprint")
    with pytest.raises(ArtifactValidationError, match="fingerprint"):
        validate_artifact_document(raw)


def test_native_review_transport_fields_roundtrip_together() -> None:
    payload = ReviewPayload(
        Role.CLAUDE,
        "work-01",
        "approved",
        (),
        "checked",
        "native-claude-review-v2",
        f"native-review-request-{'b' * 64}",
        "c" * 64,
    )
    record = _record(payload)

    assert ArtifactRecord.from_dict(record.to_dict()) == record


def test_pre_s4a_review_evidence_is_read_as_opaque_legacy_data() -> None:
    raw = _record(_review(evidence="one | embedded | two | three")).to_dict()
    assert "review_evidence" not in raw["payload"]
    assert "red_state_followup_slice" not in raw["payload"]

    loaded = ArtifactRecord.from_dict(raw)

    assert isinstance(loaded.payload, ReviewPayload)
    assert loaded.payload.evidence == "one | embedded | two | three"
    assert loaded.payload.review_evidence is None
    assert loaded.payload.red_state_followup_slice is None
    assert loaded.to_dict() == raw


def test_review_approval_requires_one_evidence_form_in_schema_and_domain() -> None:
    with pytest.raises(ArtifactValidationError, match="requires findings or review evidence"):
        ReviewPayload(
            Role.CLAUDE,
            "work-01",
            "approved",
            (),
            None,
            "native-claude-review-v2",
            CLAUDE_REQUEST_ID,
            "c" * 64,
        )

    raw = _record(_review()).to_dict()
    raw["payload"]["finding_ids"] = []
    raw["payload"]["evidence"] = None
    with pytest.raises(ArtifactValidationError, match="schema validation failed"):
        validate_artifact_document(raw)


def test_review_schema_rejects_simultaneous_legacy_and_structured_evidence() -> None:
    raw = _record(_review(evidence="legacy evidence")).to_dict()
    raw["payload"]["review_evidence"] = {
        "dimensions": "correctness",
        "largest_residual_risk": "mirror drift",
        "break_condition": "the record differs",
    }

    with pytest.raises(ArtifactValidationError, match="schema validation failed"):
        validate_artifact_document(raw)
    with pytest.raises(ArtifactValidationError, match="cannot combine"):
        replace(
            _review(evidence="legacy evidence"),
            review_evidence=ReviewEvidencePayload(
                "correctness",
                "mirror drift",
                "the record differs",
            ),
        )


def test_native_review_transport_rejects_foreign_reviewer() -> None:
    with pytest.raises(ArtifactValidationError, match="reviewer must be claude"):
        replace(_review(), reviewer=Role.CODEX)


def test_review_record_without_native_fields_is_rejected() -> None:
    raw = _record(_review(evidence="checked")).to_dict()
    for field_name in ("transport_schema", "request_id", "response_sha256"):
        raw["payload"].pop(field_name)

    with pytest.raises(ArtifactValidationError, match="schema validation"):
        validate_artifact_document(raw)


@pytest.mark.parametrize(
    "native_fields",
    (
        ("native-claude-review-v2", None, None),
        (None, f"native-review-request-{'b' * 64}", None),
    ),
)
def test_native_review_transport_rejects_partial_binding(
    native_fields: tuple[str | None, str | None, str | None],
) -> None:
    with pytest.raises(
        ArtifactValidationError,
        match="unsupported|request_id",
    ):
        ReviewPayload(
            Role.CLAUDE,
            "work-01",
            "approved",
            (),
            "checked",
            *native_fields,
        )


def test_canonical_json_is_utf8_sorted_compact_and_rejects_nan() -> None:
    assert canonical_json({"z": "ä", "a": ["x y", "x/y"]}) == b'{"a":["x y","x/y"],"z":"\xc3\xa4"}'
    with pytest.raises(ValueError):
        canonical_json({"bad": float("nan")})


def test_provider_attempt_phase_and_usage_are_fail_closed() -> None:
    with pytest.raises(ArtifactValidationError, match="started provider attempt"):
        ProviderAttemptPayload(
            Role.CLAUDE, Role.CLAUDE, "claude_slice_review", "1",
            "provider-operation-01", DIGEST, "measurement-01", "b" * 64, 1,
            "started", CREATED_AT, None, None, None,
            ProviderUsagePayload(input_tokens=0),
        )
    with pytest.raises(ArtifactValidationError, match="failure_kind"):
        ProviderAttemptPayload(
            Role.CLAUDE, Role.CLAUDE, "claude_slice_review", "1",
            "provider-operation-01", DIGEST, "measurement-01", "b" * 64, 1,
            "failed", CREATED_AT, "2026-08-18T10:30:01+00:00", 1.0, None, None,
        )
    with pytest.raises(ArtifactValidationError, match="non-negative"):
        ProviderUsagePayload(output_tokens=-1)

    failed = _record(
        ProviderAttemptPayload(
            Role.CLAUDE, Role.CLAUDE, "claude_slice_review", "1",
            "provider-operation-failed", DIGEST, "measurement-failed", "b" * 64, 1,
            "failed", CREATED_AT, "2026-08-18T10:30:01+00:00", 1.0,
            "network",
            ProviderUsagePayload(input_tokens=8, output_tokens=1, turns=1),
        )
    )
    validate_artifact_document(failed.to_dict())
    assert ArtifactRecord.from_dict(failed.to_dict()) == failed

    failed_without_usage = replace(failed.payload, usage=None)
    assert failed_without_usage.usage is None
    succeeded = _record(
        ProviderAttemptPayload(
            Role.CODEX, Role.CODEX, "codex_final_review", "work-01",
            "provider-operation-01", DIGEST, "measurement-01", "c" * 64, 1,
            "succeeded", CREATED_AT, "2026-08-18T10:30:01+00:00", 1.0,
            None, ProviderUsagePayload(output_tokens=1),
        )
    ).to_dict()
    succeeded["status"] = "started"
    with pytest.raises(ArtifactValidationError, match="schema validation failed"):
        validate_artifact_document(succeeded)


def test_failed_network_attempt_with_usage_roundtrips_model_and_schema() -> None:
    failed = _record(
        ProviderAttemptPayload(
            Role.CLAUDE, Role.CLAUDE, "claude_slice_review", "1",
            "provider-operation-network", DIGEST, "measurement-network",
            "b" * 64, 1, "failed", CREATED_AT,
            "2026-08-18T10:30:01+00:00", 1.0, "network",
            ProviderUsagePayload(input_tokens=8, output_tokens=1, turns=1),
        )
    )

    encoded = failed.to_dict()
    validate_artifact_document(encoded)

    assert ArtifactRecord.from_dict(encoded) == failed
