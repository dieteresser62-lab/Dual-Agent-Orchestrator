from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path

import pytest

import native_finding_decisions
import workflow_run_setup
from artifact_bridge import (
    ArtifactBridgeError,
    branch_discovery_handoff_export_payload,
    branch_discovery_handoff_import_payload,
    derive_family_acceptance,
    finding_handoff_export_payload,
    finding_handoff_import_payload,
    validate_plan_handoff_export_position,
)
from artifact_models import (
    ArtifactRecord,
    ArtifactValidationError,
    BranchDiscoveryCompletedPayload,
    BranchDiscoveryOccurrencePayload,
    BranchDiscoveryHandoffExportPayload,
    BranchDiscoveryHandoffImportPayload,
    BindingPayload,
    CommandSpec,
    FamilyBindingPayload,
    FindingHandoffImportPayload,
    FindingSeverity,
    FindingTransitionPayload,
    Fingerprint,
    FingerprintKind,
    ImportedFindingTransition,
    PlanPayload,
    RecordType,
    ReviewEvidencePayload,
    ReviewPayload,
    Role,
    RoleProfilePayload,
    RunIdentityPayload,
    RunProfilePayload,
    SideEffectPayload,
    SliceSpec,
    TaskPayload,
    ValidationAttestationPayload,
    ValidationResult,
    WorkUnitPayload,
    WorkflowCompletionPayload,
    _payload_from_dict,
    artifact_payload_document,
    canonical_json,
    finding_transition_sequence_sha256,
    stable_side_effect_key,
)
from artifact_replay import ArtifactReplayError, ArtifactReplayResult, replay_artifacts
from error_classification import classify_exception
from state_io import StateSchemaError
from task_contract import TaskContract, TaskMode


ROOT = Path(__file__).resolve().parents[1]
RUN_8_FIXTURE = ROOT / "tests/fixtures/run-8-finding-handoff-import-v1.json"
FINGERPRINT = Fingerprint(FingerprintKind.CONTRACT, "a" * 64)
BASE_COMMIT = "b" * 40
REVIEWED_COMMIT = "c" * 40


def _append(
    records: list[ArtifactRecord],
    run_id: str,
    logical_id: str,
    payload: object,
) -> ArtifactRecord:
    record = ArtifactRecord.create(
        run_id=run_id,
        logical_id=logical_id,
        revision=1,
        fingerprint=FINGERPRINT,
        predecessor_ids=((records[-1].record_id,) if records else ()),
        created_at=f"2026-09-19T08:00:{len(records):02d}+00:00",
        idempotency_key=f"{run_id}:{logical_id}",
        payload=payload,  # type: ignore[arg-type]
    )
    records.append(record)
    return record


def _replay(records: list[ArtifactRecord], run_id: str) -> ArtifactReplayResult:
    return replay_artifacts(
        tuple(records),
        run_id,
        require_content_authority=False,
        require_review_authority=False,
    )


def _family(
    cycle_number: int,
    *,
    predecessor_run_id: str | None = None,
    predecessor_head_record_id: str | None = None,
    plan_commit: str | None = None,
    implementation_commit: str | None = None,
) -> FamilyBindingPayload:
    return FamilyBindingPayload(
        "finding-family",
        BASE_COMMIT,
        ("src/fix.py", "tests/test_fix.py"),
        predecessor_run_id,
        predecessor_head_record_id,
        cycle_number,
        plan_commit,
        implementation_commit,
    )


def _profile(binding: FamilyBindingPayload) -> RunProfilePayload:
    return RunProfilePayload(
        RoleProfilePayload("implementer-model", "medium"),
        RoleProfilePayload("reviewer-model", "high"),
        family_binding=binding,
    )


def _identity(
    task_path: str,
    mode: str,
    approved_plan_commit: str | None,
    *,
    first_slice_start_commit: str = BASE_COMMIT,
) -> RunIdentityPayload:
    return RunIdentityPayload(
        task_path,
        "feature/finding-decision-in-slice",
        BASE_COMMIT,
        first_slice_start_commit,
        mode,
        approved_plan_commit,
    )


def _opening() -> FindingTransitionPayload:
    return FindingTransitionPayload(
        "C-01",
        Role.CLAUDE,
        Role.CLAUDE,
        "opened",
        FindingSeverity.OBSERVATION,
        "open",
        "The discovered defect must survive every family edge.",
        "discovery",
        "C-01 was discovered in run A.",
        "Run C still contains the opening transition.",
        "discovery",
        1,
    )


def _approved_review(work_unit_id: str) -> ReviewPayload:
    return ReviewPayload(
        Role.CLAUDE,
        work_unit_id,
        "approved",
        ("C-01",),
        None,
        "native-claude-review-v2",
        "native-review-request-" + "d" * 64,
        "e" * 64,
        ReviewEvidencePayload(
            "Finding identity, source history, and target binding checked.",
            "A later family edge could drop imported history.",
            "Removing the opening transition breaks the three-run proof.",
        ),
        test_files=("tests/test_branch_discovery_handoff.py",),
        pre_mortem="A digest could cover only the current run's transitions.",
    )


def _attestation() -> ValidationAttestationPayload:
    command = CommandSpec(
        "pytest",
        ("python3", "-m", "pytest", "tests/", "-v"),
    )
    return ValidationAttestationPayload(
        (ValidationResult(command, "pass", 0, "f" * 64),),
        Role.ORCHESTRATOR,
        "f" * 64,
        "ar1-" + "0" * 64,
    )


@dataclass(frozen=True)
class _DiscoveryEdge:
    source_records: list[ArtifactRecord]
    source_replay: ArtifactReplayResult
    export_record: ArtifactRecord
    imported: BranchDiscoveryHandoffImportPayload
    target_family: FamilyBindingPayload
    target_path: str
    target_bytes: bytes


def _discovery_edge(monkeypatch: pytest.MonkeyPatch) -> _DiscoveryEdge:
    monkeypatch.setattr(
        native_finding_decisions,
        "JOINT_67_68_NATIVE_CONTRACT_CUTOVER",
        True,
    )
    run_id = "run-a-discovery"
    records: list[ArtifactRecord] = []
    _append(
        records,
        run_id,
        "run-identity",
        _identity(
            "inbox/doing/run-a.md",
            "BRANCH_DISCOVERY",
            None,
            first_slice_start_commit=REVIEWED_COMMIT,
        ),
    )
    _append(
        records,
        run_id,
        "run-profile",
        _profile(_family(1, implementation_commit=REVIEWED_COMMIT)),
    )
    opening = _append(records, run_id, "finding-C-01", _opening())
    attestation = _append(
        records,
        run_id,
        "discovery-validation",
        _attestation(),
    )
    review = _append(
        records,
        run_id,
        "discovery-review",
        BranchDiscoveryCompletedPayload(
            reviewer=Role.CLAUDE,
            work_unit_id="discovery",
            new_findings=(),
            occurrences=(
                BranchDiscoveryOccurrencePayload(
                    "C-01", "The earlier finding still occurs on the reviewed HEAD."
                ),
            ),
            review_evidence=ReviewEvidencePayload(
                "Finding identity, source history, and target binding checked.",
                "A later family edge could drop imported history.",
                "Removing the opening transition breaks the three-run proof.",
            ),
            pre_mortem="A digest could cover only the current run's transitions.",
            validation_attestation_record_id=attestation.record_id,
            reviewed_head_commit=REVIEWED_COMMIT,
            transport_schema="native-claude-review-v2",
            request_id="native-review-request-" + "d" * 64,
            response_sha256="e" * 64,
            scan_complete=True,
        ),
    )
    before_export = _replay(records, run_id)
    target_family = _family(
        2,
        predecessor_run_id=run_id,
        predecessor_head_record_id=before_export.head_record_id,
        implementation_commit=REVIEWED_COMMIT,
    )
    target_path = "inbox/doing/run-b-plan.md"
    target_bytes = b"TARGET_BRANCH: feature/finding-decision-in-slice\n"
    export = branch_discovery_handoff_export_payload(
        before_export,
        discovery_review_record_id=review.record_id,
        validation_attestation_record_id=attestation.record_id,
        reviewed_head_commit=REVIEWED_COMMIT,
        family_binding=target_family,
        target_task_path=target_path,
        target_task_bytes=target_bytes,
        target_run_identity="run-b-plan",
    )
    export_record = _append(
        records,
        run_id,
        "branch-discovery-export",
        export,
    )
    source_replay = _replay(records, run_id)
    imported = branch_discovery_handoff_import_payload(
        source_replay,
        export_record,
        target_run_id="run-b-plan",
        target_task_path=target_path,
        target_task_bytes=target_bytes,
        target_family_binding=target_family,
    )
    assert export.finding_transition_record_ids == (opening.record_id,)
    return _DiscoveryEdge(
        records,
        source_replay,
        export_record,
        imported,
        target_family,
        target_path,
        target_bytes,
    )


def _with_export_trailing(
    edge: _DiscoveryEdge,
    *payloads: object,
) -> ArtifactReplayResult:
    records = list(edge.source_records)
    for index, payload in enumerate(payloads, start=1):
        _append(
            records,
            edge.export_record.run_id,
            f"export-trailing-{index}",
            payload,
        )
    return replace(
        edge.source_replay,
        records=tuple(records),
        head_record_id=records[-1].record_id,
    )


@pytest.mark.parametrize(
    "invalid_trailing",
    (
        "non_completion",
        "non_completed_outcome",
        "wrong_final_binding",
        "non_file_publication",
    ),
)
def test_plan_handoff_export_position_rejects_each_invalid_trailing_shape(
    monkeypatch: pytest.MonkeyPatch,
    invalid_trailing: str,
) -> None:
    edge = _discovery_edge(monkeypatch)
    export = edge.export_record.payload
    assert isinstance(export, BranchDiscoveryHandoffExportPayload)
    completion = WorkflowCompletionPayload(
        "completed",
        export.discovery_review_record_id,
    )
    if invalid_trailing == "non_completion":
        trailing = (WorkUnitPayload("tail", 1, ("src/fix.py",)),)
    elif invalid_trailing == "non_completed_outcome":
        trailing = (WorkflowCompletionPayload("stopped", None),)
    elif invalid_trailing == "wrong_final_binding":
        trailing = (
            WorkflowCompletionPayload("completed", edge.export_record.record_id),
        )
    else:
        trailing = (
            completion,
            WorkUnitPayload("after-completion", 1, ("src/fix.py",)),
        )

    with pytest.raises(ArtifactBridgeError, match="must be the accepted source replay head"):
        validate_plan_handoff_export_position(
            _with_export_trailing(edge, *trailing),
            edge.export_record,
        )


def test_plan_handoff_export_position_accepts_only_both_supported_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    edge = _discovery_edge(monkeypatch)
    export = edge.export_record.payload
    assert isinstance(export, BranchDiscoveryHandoffExportPayload)

    validate_plan_handoff_export_position(edge.source_replay, edge.export_record)

    content_digest = "7" * 64
    operation = (export.target_task_path, content_digest)
    publication = SideEffectPayload(
        stable_side_effect_key("file_write", "run", operation),
        "file_write",
        "run",
        operation,
        "result",
        content_digest,
    )
    completed_and_published = _with_export_trailing(
        edge,
        WorkflowCompletionPayload(
            "completed",
            export.discovery_review_record_id,
        ),
        publication,
    )

    validate_plan_handoff_export_position(
        completed_and_published,
        edge.export_record,
    )


def test_branch_discovery_import_rejects_mispositioned_plan_export(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    edge = _discovery_edge(monkeypatch)
    malformed = _with_export_trailing(
        edge,
        WorkUnitPayload("tail", 1, ("src/fix.py",)),
    )

    with pytest.raises(ArtifactBridgeError, match="must be the accepted source replay head"):
        branch_discovery_handoff_import_payload(
            malformed,
            edge.export_record,
            target_run_id="run-b-plan",
            target_task_path=edge.target_path,
            target_task_bytes=edge.target_bytes,
            target_family_binding=edge.target_family,
        )


def test_run_setup_classifies_mispositioned_plan_export_as_state_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    edge = _discovery_edge(monkeypatch)
    malformed = _with_export_trailing(
        edge,
        WorkUnitPayload("tail", 1, ("src/fix.py",)),
    )
    task = tmp_path / edge.target_path
    task.parent.mkdir(parents=True)
    task.write_bytes(edge.target_bytes)
    contract = TaskContract(
        digest=hashlib.sha256(edge.target_bytes).hexdigest(),
        mode=TaskMode.PLAN_ONLY,
        scope_patterns=("docs/work-plan.md", "src/fix.py"),
        target_branch="feature/finding-decision-in-slice",
        work_plan_path="docs/work-plan.md",
        finding_handoff_source_run_id=edge.export_record.run_id,
        finding_handoff_export_record_id=edge.export_record.record_id,
    )
    monkeypatch.setattr(
        workflow_run_setup,
        "ArtifactStore",
        lambda *_args, **_kwargs: type(
            "SourceStore",
            (),
            {"load_chain": lambda _self: malformed.records},
        )(),
    )
    monkeypatch.setattr(
        workflow_run_setup,
        "replay_artifacts",
        lambda *_args, **_kwargs: malformed,
    )

    with pytest.raises(
        StateSchemaError,
        match="BRANCH-DISCOVERY-HANDOFF-INVALID.*accepted source replay head",
    ) as captured:
        workflow_run_setup._branch_discovery_family_binding(
            tmp_path,
            task,
            "run-b-plan",
            contract,
        )

    assert classify_exception(captured.value).diagnostic_code == "STATE-SCHEMA"


def test_complete_e9_discovery_handoff_round_trips_and_replays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    edge = _discovery_edge(monkeypatch)
    export_round_trip = ArtifactRecord.from_dict(edge.export_record.to_dict())
    assert export_round_trip == edge.export_record

    target: list[ArtifactRecord] = []
    import_record = _append(
        target,
        "run-b-plan",
        "branch-discovery-import",
        edge.imported,
    )
    _append(
        target,
        "run-b-plan",
        "run-identity",
        _identity(edge.target_path, "PLAN_ONLY", None),
    )
    _append(target, "run-b-plan", "run-profile", _profile(edge.target_family))
    _append(
        target,
        "run-b-plan",
        "task",
        TaskPayload(
            "feature/finding-decision-in-slice",
            ("docs/work-plan.md",),
            hashlib.sha256(edge.target_bytes).hexdigest(),
            "docs/work-plan.md",
        ),
    )

    accepted = _replay(target, "run-b-plan")

    assert ArtifactRecord.from_dict(import_record.to_dict()) == import_record
    assert accepted.records[0] == import_record
    assert tuple(item.finding_id for item in edge.imported.finding_snapshot) == (
        "C-01",
    )
    assert edge.imported.finding_snapshot[0].finding_status == "open"
    assert edge.imported.finding_snapshot[0].severity is FindingSeverity.OBSERVATION
    assert len(edge.imported.finding_snapshot[0].signature) == 64


E9_EXPORT_FIELDS = (
    "source_run_id",
    "source_head_record_id",
    "discovery_review_record_id",
    "validation_attestation_record_id",
    "reviewed_head_commit",
    "family_id",
    "family_base_commit",
    "cycle_number",
    "predecessor_run_id",
    "predecessor_head_record_id",
    "finding_transition_record_ids",
    "finding_transitions_sha256",
    "target_task_path",
    "target_task_sha256",
    "target_run_identity",
    "authority",
)
E9_IMPORT_FIELDS = tuple(
    field for field in E9_EXPORT_FIELDS if field != "finding_transition_record_ids"
) + (
    "export_record_id",
    "target_run_id",
    "transitions",
    "finding_snapshot",
)


@pytest.mark.parametrize("record_kind", ("export", "import"))
def test_every_e9_required_field_is_named_when_missing(
    monkeypatch: pytest.MonkeyPatch,
    record_kind: str,
) -> None:
    edge = _discovery_edge(monkeypatch)
    if record_kind == "export":
        record = edge.export_record
        fields = E9_EXPORT_FIELDS
    else:
        target: list[ArtifactRecord] = []
        record = _append(
            target,
            "run-b-plan",
            "branch-discovery-import",
            edge.imported,
        )
        fields = E9_IMPORT_FIELDS

    for field in fields:
        document = record.to_dict()
        del document["payload"][field]
        with pytest.raises(ArtifactValidationError) as captured:
            ArtifactRecord.from_dict(document)
        assert field in str(captured.value)


def test_second_discovery_import_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    edge = _discovery_edge(monkeypatch)
    records: list[ArtifactRecord] = []
    _append(
        records,
        "run-b-plan",
        "branch-discovery-import-1",
        edge.imported,
    )
    _append(
        records,
        "run-b-plan",
        "branch-discovery-import-2",
        edge.imported,
    )

    with pytest.raises(ArtifactReplayError, match="duplicated|more than once"):
        _replay(records, "run-b-plan")


def test_discovery_import_rejects_unresolved_export_task_and_family_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    edge = _discovery_edge(monkeypatch)
    foreign_export = ArtifactRecord.create(
        run_id=edge.export_record.run_id,
        logical_id="foreign-export",
        revision=1,
        fingerprint=edge.export_record.fingerprint,
        predecessor_ids=edge.export_record.predecessor_ids,
        created_at=edge.export_record.created_at,
        idempotency_key="foreign-export",
        payload=edge.export_record.payload,
    )
    common = {
        "source_replay": edge.source_replay,
        "target_run_id": "run-b-plan",
        "target_task_path": edge.target_path,
        "target_task_bytes": edge.target_bytes,
        "target_family_binding": edge.target_family,
    }
    with pytest.raises(ArtifactBridgeError, match="not resolvable"):
        branch_discovery_handoff_import_payload(
            export_record=foreign_export,
            **common,
        )
    with pytest.raises(ArtifactBridgeError, match="target_task_sha256"):
        branch_discovery_handoff_import_payload(
            export_record=edge.export_record,
            **{**common, "target_task_bytes": b"different bytes"},
        )
    with pytest.raises(ArtifactBridgeError, match="target_task_path"):
        branch_discovery_handoff_import_payload(
            export_record=edge.export_record,
            **{**common, "target_task_path": "inbox/doing/wrong.md"},
        )
    with pytest.raises(ArtifactBridgeError, match="family binding"):
        branch_discovery_handoff_import_payload(
            export_record=edge.export_record,
            **{
                **common,
                "target_family_binding": replace(
                    edge.target_family,
                    cycle_number=edge.target_family.cycle_number + 1,
                ),
            },
        )


def test_three_run_handoff_keeps_opening_and_local_status_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    edge = _discovery_edge(monkeypatch)

    run_b: list[ArtifactRecord] = []
    _append(
        run_b,
        "run-b-plan",
        "branch-discovery-import",
        edge.imported,
    )
    _append(
        run_b,
        "run-b-plan",
        "run-identity",
        _identity(edge.target_path, "PLAN_ONLY", None),
    )
    _append(run_b, "run-b-plan", "run-profile", _profile(edge.target_family))
    _append(
        run_b,
        "run-b-plan",
        "task",
        TaskPayload(
            "feature/finding-decision-in-slice",
            ("docs/work-plan.md",),
            hashlib.sha256(edge.target_bytes).hexdigest(),
            "docs/work-plan.md",
        ),
    )
    _append(
        run_b,
        "run-b-plan",
        "plan",
        PlanPayload(
            "docs/work-plan.md",
            "1" * 40,
            (SliceSpec("1", "Fix C-01", ("src/fix.py",)),),
        ),
    )
    local_status = _append(
        run_b,
        "run-b-plan",
        "finding-C-01-status",
        FindingTransitionPayload(
            "C-01",
            Role.CLAUDE,
            Role.CLAUDE,
            "status_changed",
            FindingSeverity.OBSERVATION,
            "closed",
            "The plan assigns C-01 to its implementation slice.",
            "plan-review",
        ),
    )
    review = _append(
        run_b,
        "run-b-plan",
        "plan-review",
        _approved_review("plan-review"),
    )
    before_export = _replay(run_b, "run-b-plan")
    target_bytes = b"APPROVED_PLAN_COMMIT: " + b"1" * 40 + b"\n"
    export = finding_handoff_export_payload(
        before_export,
        approved_plan_commit="1" * 40,
        approval_review_record_id=review.record_id,
        target_task_path="inbox/doing/run-c-implement.md",
        target_task_bytes=target_bytes,
    )
    export_record = _append(
        run_b,
        "run-b-plan",
        "finding-export",
        export,
    )
    accepted_b = _replay(run_b, "run-b-plan")
    imported_c = finding_handoff_import_payload(
        accepted_b,
        export_record,
        target_run_id="run-c-implement",
        target_task_bytes=target_bytes,
    )

    run_c: list[ArtifactRecord] = []
    _append(
        run_c,
        "run-c-implement",
        "finding-import",
        imported_c,
    )
    _append(
        run_c,
        "run-c-implement",
        "run-identity",
        _identity("inbox/doing/run-c-implement.md", "IMPLEMENT", "1" * 40),
    )
    _append(
        run_c,
        "run-c-implement",
        "run-profile",
        _profile(
            _family(
                2,
                predecessor_run_id="run-a-discovery",
                predecessor_head_record_id=edge.imported.source_head_record_id,
                plan_commit="1" * 40,
                implementation_commit=REVIEWED_COMMIT,
            )
        ),
    )
    _append(
        run_c,
        "run-c-implement",
        "task",
        TaskPayload(
            "feature/finding-decision-in-slice",
            ("src/fix.py",),
            hashlib.sha256(target_bytes).hexdigest(),
        ),
    )
    _replay(run_c, "run-c-implement")

    assert tuple(item.payload.action for item in imported_c.transitions) == (
        "opened",
        "status_changed",
    )
    assert tuple(item.source_run_id for item in imported_c.transitions) == (
        "run-a-discovery",
        "run-b-plan",
    )
    assert tuple(item.source_record_id for item in imported_c.transitions) == (
        edge.imported.transitions[0].source_record_id,
        local_status.record_id,
    )
    identities = tuple(
        (item.source_run_id, item.source_record_id)
        for item in imported_c.transitions
    )
    assert len(identities) == len(set(identities))
    assert export.finding_transitions_sha256 == finding_transition_sequence_sha256(
        imported_c.transitions
    )


@pytest.mark.parametrize(
    ("source_mode", "cycle_number", "plan_commit"),
    (
        ("IMPLEMENT", 2, None),
        ("PLAN_ONLY", 2, "1" * 40),
        ("IMPLEMENT", 3, "1" * 40),
    ),
)
def test_all_three_e1_edges_use_the_same_branch_discovery_handoff(
    monkeypatch: pytest.MonkeyPatch,
    source_mode: str,
    cycle_number: int,
    plan_commit: str | None,
) -> None:
    monkeypatch.setattr(
        native_finding_decisions,
        "JOINT_67_68_NATIVE_CONTRACT_CUTOVER",
        True,
    )
    run_id = f"source-{source_mode.lower()}-{cycle_number}"
    records: list[ArtifactRecord] = []
    _append(
        records,
        run_id,
        "run-identity",
        _identity("inbox/source.md", source_mode, None),
    )
    source_family = _family(
        cycle_number - 1,
        plan_commit=plan_commit,
        implementation_commit=(
            REVIEWED_COMMIT if source_mode == "IMPLEMENT" else None
        ),
    )
    _append(records, run_id, "run-profile", _profile(source_family))
    attestation = _append(records, run_id, "validation", _attestation())
    approval = _append(
        records,
        run_id,
        "source-approval",
        _approved_review("source"),
    )
    binding = _append(
        records,
        run_id,
        "final-binding",
        BindingPayload(
            "implementation_handoff",
            REVIEWED_COMMIT,
            attestation.record_id,
            (approval.record_id,),
        ),
    )
    completion = _append(
        records,
        run_id,
        "workflow-completion",
        WorkflowCompletionPayload("completed", binding.record_id),
    )
    before_export = _replay(records, run_id)
    target_family = _family(
        cycle_number,
        predecessor_run_id=run_id,
        predecessor_head_record_id=before_export.head_record_id,
        plan_commit=plan_commit,
        implementation_commit=(
            REVIEWED_COMMIT if source_mode == "IMPLEMENT" else None
        ),
    )
    arguments = {
        "discovery_review_record_id": None,
        "validation_attestation_record_id": attestation.record_id,
        "reviewed_head_commit": REVIEWED_COMMIT,
        "family_binding": target_family,
        "target_task_path": "inbox/doing/discovery.md",
        "target_task_bytes": b"ORCHESTRATOR_MODE: BRANCH_DISCOVERY\n",
        "target_run_identity": f"discovery-{cycle_number}",
        "target_execution_mode": "BRANCH_DISCOVERY",
        "source_completion_record_id": completion.record_id,
    }
    if source_mode == "PLAN_ONLY":
        with pytest.raises(
            ArtifactBridgeError,
            match="only after NO_IMPLEMENTATION_REQUIRED",
        ):
            branch_discovery_handoff_export_payload(before_export, **arguments)
        return
    payload = branch_discovery_handoff_export_payload(before_export, **arguments)

    assert isinstance(payload, BranchDiscoveryHandoffExportPayload)
    assert payload.target_execution_mode == "BRANCH_DISCOVERY"
    assert payload.discovery_review_record_id is None
    assert payload.source_completion_record_id == completion.record_id


def test_family_acceptance_is_local_to_terminal_discovery_and_open_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    edge = _discovery_edge(monkeypatch)
    completion_record = next(
        record
        for record in edge.source_records
        if isinstance(record.payload, BranchDiscoveryCompletedPayload)
    )
    _append(
        edge.source_records,
        "run-a-discovery",
        "workflow-completion",
        WorkflowCompletionPayload("completed", completion_record.record_id),
    )
    replay = _replay(edge.source_records, "run-a-discovery")
    assert derive_family_acceptance(replay) is False

    non_discovery = replace(
        replay,
        run_identity=_identity("inbox/source.md", "IMPLEMENT", None),
    )
    with pytest.raises(ArtifactBridgeError, match="only from its own"):
        derive_family_acceptance(non_discovery)


def test_joint_switch_is_active_and_export_digest_binds_transitive_provenance() -> None:
    assert native_finding_decisions.JOINT_67_68_NATIVE_CONTRACT_CUTOVER is True
    records: list[ArtifactRecord] = []
    run_id = "legacy-plan-run"
    _append(
        records,
        run_id,
        "run-identity",
        _identity("inbox/doing/legacy.md", "PLAN_ONLY", None),
    )
    _append(records, run_id, "run-profile", _profile(_family(1)))
    _append(
        records,
        run_id,
        "plan",
        PlanPayload(
            "docs/work-plan.md",
            "1" * 40,
            (SliceSpec("1", "Fix", ("src/fix.py",)),),
        ),
    )
    finding = _append(records, run_id, "finding-C-01", _opening())
    review = _append(
        records,
        run_id,
        "plan-review",
        _approved_review("plan-review"),
    )
    export = finding_handoff_export_payload(
        _replay(records, run_id),
        approved_plan_commit="1" * 40,
        approval_review_record_id=review.record_id,
        target_task_path="inbox/doing/implementation.md",
        target_task_bytes=b"legacy target",
    )
    expected = {
        "source_run_id": run_id,
        "source_head_record_id": records[-1].record_id,
        "approved_plan_commit": "1" * 40,
        "approval_review_record_id": review.record_id,
        "finding_transition_record_ids": [finding.record_id],
        "finding_transitions_sha256": finding_transition_sequence_sha256(
            (
                ImportedFindingTransition(
                    finding.record_id,
                    finding.payload,
                    run_id,
                    finding.record_id,
                ),
            )
        ),
        "target_task_path": "inbox/doing/implementation.md",
        "target_task_sha256": hashlib.sha256(b"legacy target").hexdigest(),
        "authority": "orchestrator",
    }
    assert canonical_json(artifact_payload_document(export)) == canonical_json(expected)


def test_real_run_8_legacy_digest_and_wire_shape_stay_unchanged() -> None:
    fixture = json.loads(RUN_8_FIXTURE.read_text(encoding="utf-8"))
    transitions = tuple(
        ImportedFindingTransition(
            item["record_id"],
            _payload_from_dict(RecordType.FINDING_TRANSITION, item["payload"]),
        )
        for item in fixture["transitions"]
    )

    assert fixture["finding_transitions_sha256"] == (
        "580371cb96592b7c6dca3a255dd21a88871eaaa7c544e660966978a356ed67d8"
    )
    assert finding_transition_sequence_sha256(transitions) == fixture[
        "finding_transitions_sha256"
    ]
    assert all(
        set(artifact_payload_document(FindingHandoffImportPayload(
            "source-run",
            "ar1-" + "1" * 64,
            "1" * 40,
            "ar1-" + "2" * 64,
            "ar1-" + "3" * 64,
            "target-run",
            "4" * 64,
            fixture["finding_transitions_sha256"],
            transitions,
            Role.ORCHESTRATOR,
        ))["transitions"][index]) == {"record_id", "payload"}
        for index in range(len(transitions))
    )
