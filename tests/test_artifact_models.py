from __future__ import annotations

from dataclasses import replace
import json

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
    Fingerprint,
    FingerprintKind,
    GatePayload,
    PlanPayload,
    QuotaPausePayload,
    ResumeCheckPayload,
    ReviewPayload,
    Role,
    SliceSpec,
    TaskPayload,
    TransientRetryPayload,
    ValidationAttestationPayload,
    ValidationRequestPayload,
    ValidationResult,
    WorkUnitPayload,
    WorkflowCompletionPayload,
    ProviderInputComponentPayload,
    ProviderInputMeasurementPayload,
    ProviderAttemptPayload,
    ProviderUsagePayload,
    FinalReviewPreflightPayload,
    canonical_json,
    load_schema,
    validate_artifact_document,
)


DIGEST = "a" * 64
CREATED_AT = "2026-08-18T10:30:00+00:00"


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
    TaskPayload("feature/records", ("src/a.py",), DIGEST),
    PlanPayload("docs/internal/plan.md", "b" * 40, (SliceSpec("1", "models", ("src/a.py",)),)),
    WorkUnitPayload("1", 1, ("src/a.py",)),
    CorrectionWorkUnitPayload("1", 2, ("src/a.py",), ("C-01",)),
    AgentResultPayload(Role.CODEX, "work-01", "ready", ("tests/test_a.py",)),
    DiagnosticPayload(Role.CLAUDE, "work-01", 1, DIGEST, "malformed verdict"),
    ReviewPayload(Role.CLAUDE, "work-01", "approved", (), "contracts checked"),
    FindingTransitionPayload("C-01", Role.CLAUDE, Role.CLAUDE, "opened", FindingSeverity.BLOCKER, "open", "broken"),
    ValidationRequestPayload((CommandSpec("pytest", ("python3", "-m", "pytest", "tests/a b.py")),), Role.ORCHESTRATOR),
    ValidationAttestationPayload((ValidationResult(CommandSpec("pytest", ("pytest", "-q")), "pass", 0, DIGEST),), Role.ORCHESTRATOR),
    GatePayload("manual-plan", "approved", Role.USER, "explicit approval"),
    BindingPayload("implementation_handoff", "ec40aa3", "attestation-01", ("review-claude", "review-antigravity")),
    QuotaPausePayload(Role.CLAUDE, DIGEST, "2026-08-18T11:30:00Z"),
    TransientRetryPayload(Role.ANTIGRAVITY, DIGEST, "2026-08-18T11:30:05Z", 1),
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


def test_schema_is_bundled_and_self_contained() -> None:
    schema = load_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert not any("http" in ref for ref in _references(schema))


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
    lambda raw: raw.update(schema_version="2"),
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
        FindingTransitionPayload("C-01", Role.CLAUDE, Role.ANTIGRAVITY, "status_changed", FindingSeverity.BLOCKER, "closed", "fixed")
    with pytest.raises(ArtifactValidationError, match="cannot close"):
        FindingTransitionPayload("C-01", Role.CLAUDE, Role.CODEX, "responded", FindingSeverity.BLOCKER, "closed", "fixed")


def test_approval_requires_fingerprint_and_positive_evidence() -> None:
    with pytest.raises(ArtifactValidationError, match="findings or review evidence"):
        ReviewPayload(Role.CLAUDE, "work-01", "approved", (), None)

    raw = _record(ReviewPayload(Role.CLAUDE, "work-01", "approved", (), "checked")).to_dict()
    raw.pop("fingerprint")
    with pytest.raises(ArtifactValidationError, match="fingerprint"):
        validate_artifact_document(raw)


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
            Role.ANTIGRAVITY, Role.ANTIGRAVITY, "antigravity_slice_review", "1",
            "provider-operation-schema", DIGEST, "measurement-schema", "b" * 64, 1,
            "failed", CREATED_AT, "2026-08-18T10:30:01+00:00", 1.0,
            "antigravity_tool_schema",
            ProviderUsagePayload(input_tokens=8, output_tokens=1, turns=1),
        )
    )
    validate_artifact_document(failed.to_dict())
    assert ArtifactRecord.from_dict(failed.to_dict()) == failed

    failed_without_usage = replace(failed.payload, usage=None)
    assert failed_without_usage.usage is None
    with pytest.raises(ArtifactValidationError, match="antigravity provider"):
        replace(failed.payload, provider=Role.CLAUDE, role=Role.CLAUDE)

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
