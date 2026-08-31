from __future__ import annotations

import ast
from dataclasses import asdict, replace
import json
from pathlib import Path

import pytest

from artifact_bridge import ArtifactBridge
from artifact_migration import ArtifactResumeError, require_workflow_event_prefix
from artifact_models import (
    CommandSpec,
    CorrectionWorkUnitPayload,
    FingerprintKind,
    FindingSeverity,
    FindingTransitionPayload,
    GateTransitionPayload,
    ReviewEvidencePayload,
    ReviewPayload,
    Role,
    RunIdentityPayload,
    RunProfilePayload,
    SliceBoundaryPayload,
    TaskPayload,
    ValidationAttestationPayload,
    ValidationResult,
    WorkUnitPayload,
    WorkflowEventPayload,
    WorkflowPolicyPayload,
    WorkflowTransitionPayload,
)
from artifact_replay import (
    ArtifactReplayError,
    normalize_workflow_state_mirror,
    pending_workflow_event_payload,
    project_review_contracts,
    project_validation_attestations,
    project_workflow_state,
    replay_artifacts,
)
from artifact_store import ArtifactStore
from audit_trail import AuditTrailError, ReviewAuditEvent, ValidationAuditEvent
from content_authority_support import (
    append_provider_decision_authority,
    append_validation_authority,
)
from workflow import WorkflowHistory
from workflow_state import (
    AgentProfileBinding,
    GateReason,
    ProtocolBinding,
    ProtocolMode,
    SliceRecord,
    SliceStatus,
    WorkflowStep,
    WorkUnitKind,
    WorkUnitRecord,
    WorkUnitStatus,
    WorkflowState,
    WorkflowStateValidationError,
    init_workflow_state,
)
from orchestrator import _attach_record_events, _overall_audit_entries


RUN_ID = "r9-prefix-projection"
FINGERPRINT = "a" * 64


def _append_event(
    bridge: ArtifactBridge,
    domain_record,
    *,
    event_kind: str,
    work_unit_id: str | None,
    slice_id: str,
    round_number: int | None,
):
    return bridge.append(
        WorkflowEventPayload(
            event_kind,
            work_unit_id,
            slice_id,
            round_number,
            (domain_record.record_id,),
        ),
        logical_id=f"workflow-event-{domain_record.record_id}",
        idempotency_key=f"workflow-event:{domain_record.record_id}",
        fingerprint_sha256=domain_record.fingerprint.sha256,
        fingerprint_kind=domain_record.fingerprint.kind,
    )


def _append_transition(
    bridge: ArtifactBridge,
    *,
    revision: int,
    slice_id: str,
    slice_status: str,
    work_unit_id: str,
    step: str,
    work_unit_status: str,
):
    domain = bridge.append(
        WorkflowTransitionPayload(
            slice_id,
            slice_status,
            work_unit_id,
            step,
            work_unit_status,
        ),
        logical_id="workflow-transition",
        idempotency_key=f"workflow-transition:{revision}",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    return _append_event(
        bridge,
        domain,
        event_kind="transition",
        work_unit_id=work_unit_id,
        slice_id=slice_id,
        round_number=None,
    )


def _append_policy_gate(
    bridge: ArtifactBridge,
    *,
    work_unit_id: str,
    gate_revision: int = 1,
    gate_status: str = "clear",
    reason: str = "none",
    detail: str | None = None,
    resume_step: str | None = None,
):
    bridge.append(
        WorkflowPolicyPayload(work_unit_id, 0, 4),
        logical_id=f"workflow-policy-{work_unit_id}",
        idempotency_key=f"workflow-policy:{work_unit_id}:1",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    return bridge.append(
        GateTransitionPayload(
            work_unit_id,
            gate_status,
            reason,
            detail,
            None,
            (),
            resume_step,
            None,
            (),
        ),
        logical_id=f"gate-transition-{work_unit_id}",
        idempotency_key=f"gate-transition:{work_unit_id}:{gate_revision}",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )


def _append_boundary(
    bridge: ArtifactBridge, *, slice_id: str, path: str, fingerprint: str
):
    return bridge.append(
        SliceBoundaryPayload(
            slice_id,
            "b" * 40,
            ((path,),),
            fingerprint,
        ),
        logical_id=f"slice-boundary-{slice_id}",
        idempotency_key=f"slice-boundary:{slice_id}:1",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )


def _append_r9_validation(bridge: ArtifactBridge):
    return append_validation_authority(
        bridge,
        ValidationAttestationPayload(
            (
                ValidationResult(
                    CommandSpec("pytest", ("python3", "-m", "pytest")),
                    "pass",
                    0,
                    "4" * 64,
                ),
            ),
            Role.ORCHESTRATOR,
            "5" * 64,
            "ar1-" + "6" * 64,
        ),
        logical_id="validation-r9-prefix",
        idempotency_key="validation:r9-prefix",
        fingerprint_sha256=FINGERPRINT,
    )


def _journey(bridge: ArtifactBridge, *, carried_validation: bool = False):
    identity = bridge.append(
        RunIdentityPayload(
            "inbox/backlog/r9.md",
            "feature/state-authority-consolidation",
            "b" * 40,
            "IMPLEMENT",
            None,
        ),
        logical_id="run-identity",
        idempotency_key="run-identity",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    _append_event(
        bridge,
        identity,
        event_kind="run",
        work_unit_id=None,
        slice_id="1",
        round_number=None,
    )
    bridge.append(
        RunProfilePayload("gpt-5.6-sol", "medium", "opus", "max"),
        logical_id="run-profile",
        idempotency_key="run-profile",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    bridge.append(
        TaskPayload(
            "feature/state-authority-consolidation",
            ("src/one.py", "src/three.py", "src/two.py"),
            FINGERPRINT,
        ),
        logical_id="task-contract",
        idempotency_key="task-contract",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )

    _append_transition(
        bridge,
        revision=1,
        slice_id="1",
        slice_status="in_progress",
        work_unit_id="1",
        step="codex_plan",
        work_unit_status="in_progress",
    )
    _append_policy_gate(bridge, work_unit_id="1")

    _append_transition(
        bridge,
        revision=2,
        slice_id="1",
        slice_status="in_progress",
        work_unit_id="1",
        step="completed",
        work_unit_status="completed",
    )
    _append_transition(
        bridge,
        revision=3,
        slice_id="1",
        slice_status="in_progress",
        work_unit_id="2",
        step="codex_implementation",
        work_unit_status="in_progress",
    )
    _append_policy_gate(bridge, work_unit_id="2")
    _append_boundary(
        bridge, slice_id="1", path="src/one.py", fingerprint="1" * 64
    )
    bridge.append(
        WorkUnitPayload("1", 1, ("src/one.py",)),
        logical_id="work-unit-2",
        idempotency_key="work-unit:2:round:1",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )

    _append_transition(
        bridge,
        revision=4,
        slice_id="2",
        slice_status="in_progress",
        work_unit_id="3",
        step="codex_implementation",
        work_unit_status="in_progress",
    )
    _append_policy_gate(bridge, work_unit_id="3")
    _append_boundary(
        bridge, slice_id="2", path="src/two.py", fingerprint="2" * 64
    )
    bridge.append(
        WorkUnitPayload("2", 1, ("src/two.py",)),
        logical_id="work-unit-3",
        idempotency_key="work-unit:3:round:1",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )

    _append_transition(
        bridge,
        revision=5,
        slice_id="2",
        slice_status="awaiting_user_decision",
        work_unit_id="3",
        step="claude_slice_review",
        work_unit_status="awaiting_user_decision",
    )
    bridge.append(
        GateTransitionPayload(
            "3",
            "awaiting_user_decision",
            "stop_request",
            "reviewer requested an explicit halt",
            None,
            (),
            None,
            None,
            (),
        ),
        logical_id="gate-transition-3",
        idempotency_key="gate-transition:3:2",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    _append_transition(
        bridge,
        revision=6,
        slice_id="2",
        slice_status="in_progress",
        work_unit_id="3",
        step="claude_slice_review",
        work_unit_status="in_progress",
    )
    bridge.append(
        GateTransitionPayload(
            "3", "clear", "none", None, None, (), None, None, ()
        ),
        logical_id="gate-transition-3",
        idempotency_key="gate-transition:3:3",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )

    if carried_validation:
        _append_r9_validation(bridge)

    correction_transition = bridge.append(
        WorkflowTransitionPayload(
            "3", "in_progress", "4", "codex_final_correction", "in_progress"
        ),
        logical_id="workflow-transition",
        idempotency_key="workflow-transition:7",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    _append_event(
        bridge,
        correction_transition,
        event_kind="transition",
        work_unit_id="4",
        slice_id="3",
        round_number=None,
    )
    # The correction definition must refer to an already-authoritative finding.
    bridge.append(
        FindingTransitionPayload(
            "C-01",
            Role.CLAUDE,
            Role.CLAUDE,
            "opened",
            FindingSeverity.BLOCKER,
            "open",
            "the final correction must preserve authoritative history",
            "4",
            "history projection mismatch",
            "records and mirror must project identically",
            "03",
            1,
        ),
        logical_id="finding-C-01",
        idempotency_key="finding:C-01:opened",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    _append_policy_gate(bridge, work_unit_id="4")
    _append_boundary(
        bridge, slice_id="3", path="src/three.py", fingerprint="3" * 64
    )
    bridge.append(
        CorrectionWorkUnitPayload("3", 1, ("src/three.py",), ("C-01",)),
        logical_id="work-unit-4",
        idempotency_key="correction-work-unit:4:round:1",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )

    if not carried_validation:
        _append_r9_validation(bridge)
    review = append_provider_decision_authority(
        bridge,
        ReviewPayload(
            Role.CLAUDE,
            "4",
            "approved",
            (),
            None,
            "native-claude-review-v2",
            "native-review-request-" + "7" * 64,
            "8" * 64,
            review_evidence=ReviewEvidencePayload(
                "prefix projection",
                "correction round remains open",
                "resume loses the correction cursor",
            ),
            pre_mortem="a resumed correction could use stale state",
        ),
        logical_id="review-correction-1",
        idempotency_key="review:correction:1",
        fingerprint_sha256=FINGERPRINT,
        operation="codex_final_correction",
    )
    assert review.payload.work_unit_id == "4"
    bridge.append(
        CorrectionWorkUnitPayload("3", 2, ("src/three.py",), ("C-01",)),
        logical_id="work-unit-4",
        idempotency_key="correction-work-unit:4:round:2",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    return bridge.store.load_chain()


def _history_mirror(
    state: WorkflowState,
    current: WorkflowHistory,
    archive: tuple[WorkflowHistory, ...],
) -> WorkflowState:
    return replace(
        state,
        runtime_history={
            "archive": [history.to_dict() for history in archive],
            "current": current.to_dict(),
        },
    )


def _unit_four_history(
    replay,
    read_blob,
    *,
    include_validation: bool,
    include_review: bool,
) -> WorkflowHistory:
    validations = dict(project_validation_attestations(replay, read_blob))
    reviews = project_review_contracts(replay, read_blob)
    validation_event = next(
        event for event in replay.workflow_events
        if event.event_kind == "validation"
    )
    review_event = next(
        event for event in replay.workflow_events
        if event.event_kind == "review"
    )
    attestation = validations[validation_event.record_refs[0]]
    events = ()
    attestations = ()
    latest_fingerprint = None
    latest_review = None
    if include_validation:
        events = (
            ValidationAuditEvent(
                1, int(validation_event.slice_id), attestation
            ),
        )
        attestations = (attestation,)
    if include_review:
        review_contract = next(
            item for item in reviews
            if item.record_id == review_event.record_refs[0]
        )
        review_record = next(
            record for record in replay.records
            if record.record_id == review_contract.record_id
        )
        events = (
            *events,
            ReviewAuditEvent(
                len(events) + 1,
                int(review_event.slice_id),
                review_event.round_number or 1,
                review_contract.result,
                (),
            ),
        )
        latest_fingerprint = review_record.fingerprint.sha256
        latest_review = review_contract.result
    return WorkflowHistory(
        4,
        events=events,
        attestations=attestations,
        last_claude_fingerprint=latest_fingerprint,
        latest_claude_review=latest_review,
    )


def _independent_mirror_snapshots(
    replay,
    read_blob,
) -> dict[int, WorkflowState]:
    """Build the legacy mirror through WorkflowState transitions, not replay."""
    binding = ProtocolBinding(
        ProtocolMode.STRUCTURED_V2,
        "2",
        codex_profile=AgentProfileBinding("gpt-5.6-sol", "medium"),
        claude_profile=AgentProfileBinding("opus", "max"),
    )
    state = init_workflow_state(
        run_id=RUN_ID,
        task_file="inbox/backlog/r9.md",
        branch="feature/state-authority-consolidation",
        branch_base="b" * 40,
        first_slice_start_commit="b" * 40,
        slice_count=1,
        task_digest=FINGERPRINT,
        task_scope_patterns=("src/one.py", "src/three.py", "src/two.py"),
        target_branch="feature/state-authority-consolidation",
        protocol_binding=binding,
        timestamp="2026-08-31T10:00:00+00:00",
    )
    plan_history = WorkflowHistory(1)
    snapshots = {
        end: _history_mirror(state, plan_history, ())
        for end in (6, 7, 8)
    }

    state = state.complete_current_work_unit(
        updated_at="2026-08-31T10:00:01+00:00"
    )
    snapshots[10] = _history_mirror(state, plan_history, ())
    state = state.start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
        updated_at="2026-08-31T10:00:02+00:00",
    )
    slice_one_history = WorkflowHistory(2)
    for end in (12, 13, 14):
        snapshots[end] = _history_mirror(
            state, slice_one_history, (plan_history,)
        )
    state = state.bind_current_slice_git_boundary(
        start_commit="b" * 40,
        scope_paths=("src/one.py",),
        start_fingerprint="1" * 64,
        updated_at="2026-08-31T10:00:03+00:00",
    )
    for end in (15, 16):
        snapshots[end] = _history_mirror(
            state, slice_one_history, (plan_history,)
        )

    slice_two = SliceRecord(
        2,
        SliceStatus.IN_PROGRESS,
        "b" * 40,
        ("src/two.py",),
        (("src/two.py",),),
        "2" * 64,
    )
    slice_two_unit = WorkUnitRecord(
        3,
        2,
        WorkUnitKind.SLICE,
        WorkUnitStatus.IN_PROGRESS,
        WorkflowStep.CODEX_IMPLEMENTATION,
    )
    state = replace(
        state,
        current_slice_id=2,
        current_work_unit_id=3,
        current_step=WorkflowStep.CODEX_IMPLEMENTATION,
        slices=(*state.slices, slice_two),
        work_units=(*state.work_units, slice_two_unit),
        updated_at="2026-08-31T10:00:06+00:00",
    )
    slice_two_history = WorkflowHistory(3)
    archive = (plan_history, slice_one_history)
    for end in (21, 22):
        snapshots[end] = _history_mirror(state, slice_two_history, archive)
    state = state.with_current_step(
        WorkflowStep.CLAUDE_SLICE_REVIEW,
        updated_at="2026-08-31T10:00:07+00:00",
    ).await_policy_gate(
        reason=GateReason.STOP_REQUEST,
        detail="reviewer requested an explicit halt",
        updated_at="2026-08-31T10:00:08+00:00",
    )
    snapshots[25] = _history_mirror(state, slice_two_history, archive)
    state = state.resume_after_user_decision(
        updated_at="2026-08-31T10:00:09+00:00"
    )
    snapshots[28] = _history_mirror(state, slice_two_history, archive)

    correction_slice = SliceRecord(
        3,
        SliceStatus.IN_PROGRESS,
        "b" * 40,
        ("src/three.py",),
        (("src/three.py",),),
        "3" * 64,
    )
    provisional_unit = WorkUnitRecord(
        4,
        3,
        WorkUnitKind.SLICE,
        WorkUnitStatus.IN_PROGRESS,
        WorkflowStep.CODEX_FINAL_CORRECTION,
        open_findings=("C-01",),
    )
    state = replace(
        state,
        current_slice_id=3,
        current_work_unit_id=4,
        current_step=WorkflowStep.CODEX_FINAL_CORRECTION,
        slices=(*state.slices, correction_slice),
        work_units=(*state.work_units, provisional_unit),
        updated_at="2026-08-31T10:00:11+00:00",
    )
    correction_history = WorkflowHistory(4)
    archive = (*archive, slice_two_history)
    state = replace(
        state,
        work_units=(
            *state.work_units[:-1],
            replace(state.current_work_unit, kind=WorkUnitKind.CORRECTION),
        ),
    )
    for end in (35, 36):
        snapshots[end] = _history_mirror(state, correction_history, archive)
    correction_history = _unit_four_history(
        replay, read_blob, include_validation=True, include_review=False
    )
    for end in (38, 39):
        snapshots[end] = _history_mirror(state, correction_history, archive)
    correction_history = _unit_four_history(
        replay, read_blob, include_validation=True, include_review=True
    )
    # Approved reviews update history; the state mirror records a reviewer only
    # for denial/return transitions.
    reviewed_unit = replace(state.current_work_unit, open_findings=())
    state = replace(state, work_units=(*state.work_units[:-1], reviewed_unit))
    snapshots[43] = _history_mirror(state, correction_history, archive)
    round_two = replace(state.current_work_unit, round_number=2)
    state = replace(state, work_units=(*state.work_units[:-1], round_two))
    snapshots[44] = _history_mirror(state, correction_history, archive)
    return snapshots


def test_multi_slice_correction_gate_halt_resume_projects_every_accepted_prefix(
    tmp_path: Path,
) -> None:
    bridge = ArtifactBridge(ArtifactStore(tmp_path, RUN_ID))
    chain = _journey(bridge)
    mirrors = _independent_mirror_snapshots(
        replay_artifacts(chain, RUN_ID), bridge.store.read_blob
    )
    projected_prefixes = []
    accepted_ends = []
    rejected: dict[int, str] = {}
    rejection_markers = {
        "event_tail": "workflow event prefix",
        "identity_profile_task": "requires run identity, profile, and task",
        "cursor": "requires a current cursor",
        "slice_start": "a started slice requires start_commit",
        "gate_pair": "work-unit status and gate status must change together",
        "correction_definition": "requires its correction work-unit record",
        "review_anchor": "review has no authoritative anchor-list record",
        "review_validation": "review has no authoritative validation binding",
    }
    for end in range(1, len(chain) + 1):
        try:
            replay = replay_artifacts(chain[:end], RUN_ID)
            require_workflow_event_prefix(replay)
            first = project_workflow_state(replay)
        except (ArtifactResumeError, ArtifactReplayError) as exc:
            matches = tuple(
                name for name, marker in rejection_markers.items()
                if marker in str(exc)
            )
            assert len(matches) == 1, (end, exc)
            rejected[end] = matches[0]
            continue
        second = project_workflow_state(replay)
        assert first.canonical_document == second.canonical_document
        assert json.loads(
            normalize_workflow_state_mirror(mirrors[end], replay)
        ) == first.to_document(), end
        assert isinstance(first.state, WorkflowState)
        assert set(first.to_document()) == set(WorkflowState.__dataclass_fields__)
        projected_prefixes.append(first.to_document())
        accepted_ends.append(end)

    assert len(projected_prefixes) + len(rejected) == len(chain) == 44
    assert set(accepted_ends).isdisjoint(rejected)
    assert tuple(accepted_ends) == (
        6, 7, 8, 10, 12, 13, 14, 15, 16, 21, 22, 25, 28, 35, 36,
        38, 39, 43, 44,
    )
    assert set(mirrors) == set(accepted_ends)
    assert rejected == {
        1: "event_tail",
        2: "identity_profile_task",
        3: "identity_profile_task",
        4: "cursor",
        5: "event_tail",
        9: "event_tail",
        11: "event_tail",
        17: "event_tail",
        18: "slice_start",
        19: "slice_start",
        20: "slice_start",
        23: "event_tail",
        24: "gate_pair",
        26: "event_tail",
        27: "gate_pair",
        29: "event_tail",
        30: "correction_definition",
        31: "correction_definition",
        32: "correction_definition",
        33: "correction_definition",
        34: "correction_definition",
        37: "event_tail",
        40: "review_anchor",
        41: "review_validation",
        42: "event_tail",
    }
    assert any(len(item["slices"]) >= 2 for item in projected_prefixes)
    assert any(
        unit["status"] == "awaiting_user_decision"
        for item in projected_prefixes
        for unit in item["work_units"]
    )
    assert any(
        unit["status"] == "in_progress" and unit["current_step"] == "claude_slice_review"
        for item in projected_prefixes
        for unit in item["work_units"]
    )
    final = projected_prefixes[-1]
    assert len(final["slices"]) == 3
    assert final["work_units"][-1]["kind"] == "correction"
    assert final["work_units"][-1]["round_number"] == 2
    assert final["current_work_unit_id"] == 4


def test_workflow_event_references_exactly_one_domain_record_without_copying_content(
    tmp_path: Path,
) -> None:
    chain = _journey(ArtifactBridge(ArtifactStore(tmp_path, RUN_ID)))
    records_by_id = {record.record_id: record for record in chain}
    events = [record for record in chain if isinstance(record.payload, WorkflowEventPayload)]
    assert events
    for event_record in events:
        document = asdict(event_record.payload)
        assert set(document) == {
            "event_kind", "work_unit_id", "slice_id", "round_number", "record_refs"
        }
        assert len(document["record_refs"]) == 1
        referenced = records_by_id[document["record_refs"][0]]
        assert event_record.fingerprint == referenced.fingerprint
        assert not {
            "result", "results", "review_evidence", "evidence", "payload", "content"
        } & set(document)


def test_record_events_reconstruct_the_retired_audit_mirror_exactly(
    tmp_path: Path,
) -> None:
    bridge = ArtifactBridge(ArtifactStore(tmp_path, RUN_ID))
    replay = replay_artifacts(_journey(bridge), RUN_ID)
    validations = dict(
        project_validation_attestations(replay, bridge.store.read_blob)
    )
    reviews = project_review_contracts(replay, bridge.store.read_blob)
    validation_event = next(
        event for event in replay.workflow_events
        if event.event_kind == "validation"
    )
    review_event = next(
        event for event in replay.workflow_events
        if event.event_kind == "review"
    )
    review_contract = next(
        item for item in reviews if item.record_id == review_event.record_refs[0]
    )
    review_record = next(
        record for record in replay.records
        if record.record_id == review_contract.record_id
    )
    expected = WorkflowHistory(
        4,
        events=(
            ValidationAuditEvent(
                1,
                int(validation_event.slice_id),
                validations[validation_event.record_refs[0]],
            ),
            ReviewAuditEvent(
                2,
                int(review_event.slice_id),
                review_event.round_number or 1,
                review_contract.result,
                (),
            ),
        ),
        attestations=(validations[validation_event.record_refs[0]],),
        last_claude_fingerprint=review_record.fingerprint.sha256,
        latest_claude_review=review_contract.result,
    )

    reconstructed = _attach_record_events(
        {4: WorkflowHistory(4)}, replay, bridge.store.read_blob
    )
    assert reconstructed[4] == expected
    projected = project_workflow_state(replay)
    mirror = replace(
        projected.state,
        runtime_history={"archive": [], "current": expected.to_dict()},
    )
    assert normalize_workflow_state_mirror(mirror, replay) == projected.canonical_document

    missing_attestation = replace(expected, attestations=())
    damaged = replace(
        projected.state,
        runtime_history={
            "archive": [],
            "current": missing_attestation.to_dict(),
        },
    )
    with pytest.raises(
        ArtifactReplayError, match="omits a validation event binding"
    ):
        normalize_workflow_state_mirror(damaged, replay)


def test_final_review_audit_reuses_carried_attestation(tmp_path: Path) -> None:
    bridge = ArtifactBridge(ArtifactStore(tmp_path, RUN_ID))
    _journey(bridge, carried_validation=True)
    _append_transition(
        bridge,
        revision=8,
        slice_id="3",
        slice_status="in_progress",
        work_unit_id="5",
        step="claude_final_review",
        work_unit_status="in_progress",
    )
    _append_policy_gate(bridge, work_unit_id="5")
    bridge.append(
        WorkUnitPayload("3", 1, ("src/three.py",)),
        logical_id="work-unit-5",
        idempotency_key="work-unit:5:round:1",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    final_review = append_provider_decision_authority(
        bridge,
        ReviewPayload(
            Role.CLAUDE,
            "5",
            "approved",
            (),
            None,
            "native-claude-review-v2",
            "native-review-request-" + "9" * 64,
            "a" * 64,
            review_evidence=ReviewEvidencePayload(
                "carried validation audit",
                "a reused Slice attestation can be lost",
                "the final review audit lacks its validation event",
            ),
            pre_mortem="audit projection could discard carried validation",
        ),
        logical_id="review-final-carried-validation",
        idempotency_key="review:final:carried-validation",
        fingerprint_sha256=FINGERPRINT,
        operation="claude_final_review",
    )
    replay = replay_artifacts(bridge.store.load_chain(), RUN_ID)
    assert not any(
        event.event_kind == "validation" and event.work_unit_id == "5"
        for event in replay.workflow_events
    )

    review_contract = next(
        item
        for item in project_review_contracts(replay, bridge.store.read_blob)
        if item.record_id == final_review.record_id
    )
    attestation = review_contract.result.validation
    assert attestation is not None
    mirror_history = WorkflowHistory(
        5,
        attestations=(attestation,),
        last_claude_fingerprint=final_review.fingerprint.sha256,
        latest_claude_review=review_contract.result,
    )
    projected_state = project_workflow_state(replay).state
    assert isinstance(projected_state, WorkflowState)
    mirror_state = replace(
        projected_state,
        runtime_history={"archive": [], "current": mirror_history.to_dict()},
    )

    entries = _overall_audit_entries(
        mirror_state, replay, bridge.store.read_blob
    )
    final_entry = next(
        item for item in entries if item.projection.review_work_unit_id == "5"
    )
    assert final_entry.projection.events == (
        ValidationAuditEvent(1, 3, attestation),
        ReviewAuditEvent(2, 3, 1, review_contract.result, ("FINAL",)),
    )


def test_each_domain_event_crash_tail_is_bounded_and_reconstructable(
    tmp_path: Path,
) -> None:
    chain = _journey(ArtifactBridge(ArtifactStore(tmp_path, RUN_ID)))
    events = tuple(
        (index, record)
        for index, record in enumerate(chain)
        if isinstance(record.payload, WorkflowEventPayload)
    )
    assert {record.payload.event_kind for _, record in events} == {
        "run", "transition", "validation", "review"
    }

    for index, event_record in events:
        replay = replay_artifacts(chain[:index], RUN_ID)
        assert replay.pending_workflow_event_record_id == (
            event_record.payload.record_refs[0]
        )
        with pytest.raises(ArtifactResumeError, match="workflow event prefix"):
            require_workflow_event_prefix(replay)
        require_workflow_event_prefix(replay, allow_incomplete_tail=True)
        assert pending_workflow_event_payload(replay) == event_record.payload


def test_duplicate_workflow_event_reference_is_rejected_fail_closed(
    tmp_path: Path,
) -> None:
    bridge = ArtifactBridge(ArtifactStore(tmp_path, RUN_ID))
    chain = _journey(bridge)
    original = next(
        record for record in chain
        if isinstance(record.payload, WorkflowEventPayload)
        and record.payload.event_kind == "transition"
    )
    bridge.append(
        original.payload,
        logical_id="duplicate-workflow-event-reference",
        idempotency_key="duplicate-workflow-event-reference",
        fingerprint_sha256=original.fingerprint.sha256,
        fingerprint_kind=original.fingerprint.kind,
    )

    with pytest.raises(
        ArtifactReplayError, match="workflow event domain reference is duplicated"
    ):
        replay_artifacts(bridge.store.load_chain(), RUN_ID)


def test_review_cannot_authorize_its_own_foreign_finding_origin(
    tmp_path: Path,
) -> None:
    bridge = ArtifactBridge(ArtifactStore(tmp_path, RUN_ID))
    _journey(bridge)
    bridge.append(
        FindingTransitionPayload(
            "C-98",
            Role.CLAUDE,
            Role.CLAUDE,
            "opened",
            FindingSeverity.BLOCKER,
            "open",
            "a run-wide finding omitted by the prior work-unit review",
            "3",
            "foreign origin",
            "reject origin 02 in the later correction review",
            "02",
            1,
        ),
        logical_id="finding-C-98",
        idempotency_key="finding:C-98:opened",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    append_validation_authority(
        bridge,
        ValidationAttestationPayload(
            (
                ValidationResult(
                    CommandSpec("pytest", ("python3", "-m", "pytest")),
                    "pass",
                    0,
                    "9" * 64,
                ),
            ),
            Role.ORCHESTRATOR,
            "a" * 64,
            "ar1-" + "b" * 64,
        ),
        logical_id="validation-r9-malicious-origin",
        idempotency_key="validation:r9-malicious-origin",
        fingerprint_sha256=FINGERPRINT,
    )
    review = append_provider_decision_authority(
        bridge,
        ReviewPayload(
            Role.CLAUDE,
            "4",
            "denied",
            ("C-98",),
            None,
            "native-claude-review-v2",
            "native-review-request-" + "c" * 64,
            "d" * 64,
            review_evidence=ReviewEvidencePayload(
                "foreign finding origin",
                "the review could broaden its own origin set",
                "a finding from an unrelated Slice enters the audit",
            ),
            pre_mortem="self-authorized origins would corrupt the audit projection",
        ),
        logical_id="review-malicious-origin",
        idempotency_key="review:malicious-origin",
        fingerprint_sha256=FINGERPRINT,
        operation="codex_final_correction",
    )
    replay = replay_artifacts(bridge.store.load_chain(), RUN_ID)

    with pytest.raises(AuditTrailError, match="belongs to slice 02"):
        _attach_record_events({}, replay, bridge.store.read_blob)


def test_chain_without_workflow_events_is_rejected_fail_closed(tmp_path: Path) -> None:
    bridge = ArtifactBridge(ArtifactStore(tmp_path, "r9-pre-event-chain"))
    bridge.append(
        RunIdentityPayload("task.md", "feature/r9", "b" * 40, "IMPLEMENT", None),
        logical_id="run-identity",
        idempotency_key="run-identity",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    bridge.append(
        RunProfilePayload("model-a", "medium", "model-b", "high"),
        logical_id="run-profile",
        idempotency_key="run-profile",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    bridge.append(
        TaskPayload("feature/r9", ("src/r9.py",), FINGERPRINT),
        logical_id="task-contract",
        idempotency_key="task-contract",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    bridge.append(
        WorkflowTransitionPayload(
            "1", "in_progress", "1", "codex_plan", "in_progress"
        ),
        logical_id="workflow-transition",
        idempotency_key="workflow-transition:1",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    replay = replay_artifacts(bridge.store.load_chain(), bridge.store.run_id)
    with pytest.raises(ArtifactResumeError, match="workflow event prefix"):
        require_workflow_event_prefix(replay)
    with pytest.raises(ArtifactReplayError, match="complete workflow event prefix"):
        project_workflow_state(replay)


def test_retired_runtime_history_events_cannot_return_to_state() -> None:
    raw = {
        "version": 3,
        "run_id": "r9-events-guard",
        "task_file": "task.md",
        "branch": "feature/r9",
        "branch_base": "b" * 40,
        "created_at": "2026-08-31T10:00:00+00:00",
        "updated_at": "2026-08-31T10:00:00+00:00",
        "current_slice_id": 1,
        "current_work_unit_id": 1,
        "current_step": "codex_plan",
        "slices": [
            {
                "slice_id": 1,
                "status": "in_progress",
                "start_commit": "b" * 40,
                "scope_paths": [],
                "scope_change_groups": [],
                "start_fingerprint": None,
                "commit_ref": None,
            }
        ],
        "work_units": [
            {
                "work_unit_id": 1,
                "slice_id": 1,
                "kind": "plan",
                "status": "in_progress",
                "current_step": "codex_plan",
                "round_number": 1,
                "codex_return_count": 0,
                "max_codex_returns": 4,
                "gate": {
                    "status": "clear", "reason": "none", "detail": None,
                    "fingerprint": None, "paths": [], "resume_step": None,
                },
                "reviewer": None,
                "open_findings": [],
                "completed_side_effects": [],
                "gate_decisions": [],
                "active_test_fingerprint": None,
                "active_test_paths": [],
                "invocation_failures": [],
            }
        ],
        "planned_slices": [],
        "runtime_history": {"work_unit_id": 1, "events": []},
        "task_digest": None,
        "execution_mode": "IMPLEMENT",
        "task_scope_patterns": [],
        "work_plan_path": None,
        "approved_plan_commit": None,
        "finding_handoff_source_run_id": None,
        "finding_handoff_export_record_id": None,
        "audit_report_path": None,
        "target_branch": None,
        "protocol_binding": None,
        "bootstrap_checks": [],
    }
    with pytest.raises(WorkflowStateValidationError, match="events is retired"):
        WorkflowState.from_dict(raw)


def test_full_state_projector_has_no_filesystem_or_clock_dependency() -> None:
    source = Path(project_workflow_state.__code__.co_filename).read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        item for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == "project_workflow_state"
    )
    calls = {
        node.func.id if isinstance(node.func, ast.Name) else node.func.attr
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Name, ast.Attribute))
    }
    assert not calls & {
        "open", "read_text", "read_bytes", "write_text", "write_bytes",
        "load_chain", "now", "utcnow", "time",
    }
