from __future__ import annotations

import ast
import base64
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path

import pytest

import artifact_replay as artifact_replay_module
from acceptance_criteria import MeasuredAgainst, acceptance_criteria_from_texts
from artifact_bridge import ArtifactBridge
from artifact_resume import (
    ArtifactResumeError,
    ResumeResolution,
    require_workflow_event_prefix,
)
from artifact_models import (
    BindingPayload,
    CommandSpec,
    FingerprintKind,
    FindingSeverity,
    FindingTransitionPayload,
    GateDecisionPayload,
    GatePayload,
    GateTransitionPayload,
    InvocationFailurePayload,
    PlanPayload,
    ProviderContentPayload,
    ProviderInputComponentPayload,
    ProviderInputMeasurementPayload,
    ReviewPacketPayload,
    ReviewEvidencePayload,
    ReviewPayload,
    Role,
    RoleProfilePayload,
    RunIdentityPayload,
    RunProfilePayload,
    STATE_PROJECTION_REDUCER_VERSION,
    SliceBoundaryPayload,
    SliceSpec,
    TaskPayload,
    ValidationAttestationPayload,
    ValidationResult,
    WorkUnitPayload,
    WorkflowEventPayload,
    WorkflowPolicyPayload,
    WorkflowTransitionPayload,
    canonical_json,
    provider_text_evidence,
    technical_text_evidence,
)
from artifact_replay import (
    ArtifactReplayError,
    ReplayDiagnosticCode,
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
from finding_reducer import reduce_findings
from final_review_preflight import relevant_record_head, run_final_review_preflight
from state_io import write_workflow_state_projection
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
from workflow_audit_projection import (
    _attach_record_events,
    _hydrate_record_history,
    _overall_audit_entries,
)


RUN_ID = "r9-prefix-projection"
STATE_PROJECTION_BASELINE = (
    Path(__file__).parent / "fixtures/state-projection-baseline-v1.json"
)
STATE_PROJECTION_BASE_COMMIT = "c6d3963"
STATE_PROJECTION_FIXED_TIME = "2026-09-03T12:00:00+00:00"
STATE_PROJECTION_ANCHOR_CASES = (
    ("minimal-cursor", "standard-validation", 6),
    ("bounded-slice", "standard-validation", 16),
    ("halted-review-gate", "standard-validation", 25),
    ("resumed-review-gate", "standard-validation", 28),
    ("defined-correction", "standard-validation", 34),
    ("validated-correction", "standard-validation", 39),
    ("approved-correction", "standard-validation", 43),
    ("second-correction-round", "standard-validation", 44),
    ("carried-validation", "carried-validation", 44),
    ("gate-override", "gate-override", None),
    ("invocation-failure", "invocation-failure", None),
    ("commit-bound", "commit-bound", None),
    ("denied-slice-review", "denied-slice-review", None),
)
STATE_PROJECTION_HELPERS = (
    "_ensure_projection_event_prefix",
    "_index_projection_transitions",
    "_project_slice_documents",
    "_index_work_unit_projection_authority",
    "_project_work_unit_round_and_kind",
    "_project_work_unit_gate",
    "_project_work_unit_open_findings",
    "_project_work_unit_document",
    "_project_work_unit_documents",
    "_slice_acceptance_projection",
    "_assemble_workflow_state_document",
)
STATE_PROJECTION_SHARED_BOUNDARIES = (
    "_fail",
    "_project_bootstrap_fact",
    "_review_prefix_end",
    "project_runtime_history_references",
)


def _normalized_independent_mirror(
    mirror: WorkflowState,
    replay,
) -> dict[str, object]:
    """Normalize only volatile/cache fields in the independent R9 oracle."""
    document = mirror.to_dict()
    document["created_at"] = replay.records[0].created_at
    document["updated_at"] = replay.records[-1].created_at
    document["runtime_history"] = {
        unit.work_unit_id: {
            "finding_record_refs": tuple(
                record.record_id
                for record in replay.records
                if isinstance(record.payload, FindingTransitionPayload)
                and record.payload.work_unit_id == unit.work_unit_id
            ),
            "review_record_refs": tuple(
                record.record_id
                for record in replay.records
                if isinstance(record.payload, ReviewPayload)
                and record.payload.work_unit_id == unit.work_unit_id
            ),
            "attestation_record_refs": tuple(
                event.record_refs[0]
                for event in replay.workflow_events
                if event.event_kind == "validation"
                and event.work_unit_id == unit.work_unit_id
            ),
            "provider_content_record_refs": tuple(
                record.record_id
                for record in replay.records
                if isinstance(record.payload, ProviderContentPayload)
                and record.payload.work_unit_id == unit.work_unit_id
            ),
            "review_packet_record_refs": tuple(
                record.record_id
                for record in replay.records
                if isinstance(record.payload, ReviewPacketPayload)
                and record.payload.work_unit_id == unit.work_unit_id
            ),
            "workflow_event_record_refs": tuple(
                event.record_id
                for event in replay.workflow_events
                if event.work_unit_id == unit.work_unit_id
            ),
        }
        for unit in replay.work_unit_states
    }
    return WorkflowState.from_dict(document).to_dict()
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
        WorkflowPolicyPayload(work_unit_id, 0, 6),
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


def _journey(
    bridge: ArtifactBridge,
    *,
    carried_validation: bool = False,
):
    identity = bridge.append(
        RunIdentityPayload(
            "inbox/backlog/r9.md",
            "feature/state-authority-consolidation",
            "b" * 40,
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
        RunProfilePayload(
            RoleProfilePayload("gpt-5.6-sol", "medium"),
            RoleProfilePayload("opus", "max"),
        ),
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
            "3", "in_progress", "4", "codex_correction", "in_progress"
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
        WorkUnitPayload("3", 1, ("src/three.py",), ("C-01",)),
        logical_id="work-unit-4",
        idempotency_key="work-unit:4:round:1",
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
        operation="codex_correction",
    )
    assert review.payload.work_unit_id == "4"
    bridge.append(
        WorkUnitPayload("3", 2, ("src/three.py",), ("C-01",)),
        logical_id="work-unit-4",
        idempotency_key="work-unit:4:round:2",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    return bridge.store.load_chain()


def _state_projection_bridge(tmp_path: Path, chain_name: str) -> ArtifactBridge:
    chain_root = tmp_path / chain_name
    chain_root.mkdir()
    return ArtifactBridge(
        ArtifactStore(chain_root, RUN_ID),
        now=lambda: STATE_PROJECTION_FIXED_TIME,
    )


def _productively_checked_work_unit_kinds() -> frozenset[WorkUnitKind]:
    source_root = Path(__file__).resolve().parents[1] / "src"
    checked_names: set[str] = set()
    for path in sorted(source_root.glob("*.py")):
        if path.name in {"dry_run_scenarios.py", "workflow_dry_run.py"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for comparison in (
            node for node in ast.walk(tree) if isinstance(node, ast.Compare)
        ):
            checked_names.update(
                node.attr
                for node in ast.walk(comparison)
                if isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "WorkUnitKind"
            )
    return frozenset(WorkUnitKind[name] for name in checked_names)


def _append_projection_gate_override(bridge: ArtifactBridge) -> None:
    active_test_fingerprint = "e" * 64
    active_test_paths = ("tests/test_workflow_history_projection.py",)
    bridge.append(
        GateTransitionPayload(
            "4",
            "awaiting_user_decision",
            "stop_request",
            "operator confirmation is required before correction resumes",
            "d" * 64,
            ("src/three.py",),
            "codex_correction",
            active_test_fingerprint,
            active_test_paths,
        ),
        logical_id="gate-transition-4",
        idempotency_key="gate-transition:4:anchor-halt",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    gate = bridge.append(
        GatePayload(
            "test-change",
            "approved",
            Role.USER,
            "reviewed exact projection-anchor test delta",
        ),
        logical_id="gate-test-change-projection-anchor",
        idempotency_key="gate:test-change:projection-anchor",
        fingerprint_sha256=active_test_fingerprint,
        fingerprint_kind=FingerprintKind.IMPLEMENTATION,
    )
    bridge.append(
        GateDecisionPayload(
            "4",
            gate.record_id,
            active_test_paths,
            "codex_correction",
        ),
        logical_id="gate-decision-4-projection-anchor",
        idempotency_key="gate-decision:4:projection-anchor",
        fingerprint_sha256=active_test_fingerprint,
        fingerprint_kind=FingerprintKind.IMPLEMENTATION,
    )


def _append_projection_invocation_failure(bridge: ArtifactBridge) -> None:
    provider, provider_sha, provider_bytes = provider_text_evidence(
        "projection provider quota"
    )
    technical, technical_sha, technical_bytes = technical_text_evidence(
        "projection invocation diagnostic"
    )
    invocation_id = "projection-quota"
    bridge.append(
        InvocationFailurePayload(
            invocation_id,
            "invoke:projection-quota:attempt-1",
            Role.CODEX,
            "quota",
            "transient",
            "AGENT-INVOCATION",
            provider,
            provider_sha,
            provider_bytes,
            technical,
            technical_sha,
            technical_bytes,
            STATE_PROJECTION_FIXED_TIME,
            STATE_PROJECTION_FIXED_TIME,
            "codex_correction",
            "3",
            "4",
            2,
            None,
            "provider-reset",
            "UTC",
            STATE_PROJECTION_FIXED_TIME,
            "2026-09-03T12:01:00+00:00",
            60,
            60,
            1,
            True,
            FINGERPRINT,
        ),
        logical_id=f"invocation-failure-{invocation_id}",
        idempotency_key=f"invocation-failure:{invocation_id}",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )


def _append_projection_commit_binding(bridge: ArtifactBridge) -> None:
    chain = bridge.store.load_chain()
    attestation = next(
        record
        for record in reversed(chain)
        if isinstance(record.payload, ValidationAttestationPayload)
    )
    review = next(
        record
        for record in reversed(chain)
        if isinstance(record.payload, ReviewPayload)
        and record.payload.verdict == "approved"
    )
    bridge.append(
        BindingPayload(
            "commit",
            "b42-state-projection-commit",
            attestation.record_id,
            (review.record_id,),
        ),
        logical_id="commit-3-projection-anchor",
        idempotency_key="commit:3:projection-anchor",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.IMPLEMENTATION,
    )


def _append_projection_denied_slice_review(bridge: ArtifactBridge) -> None:
    _append_transition(
        bridge,
        revision=8,
        slice_id="3",
        slice_status="in_progress",
        work_unit_id="5",
        step="claude_slice_review",
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
    append_provider_decision_authority(
        bridge,
        ReviewPayload(
            Role.CLAUDE,
            "5",
            "denied",
            ("C-01",),
            None,
            "native-claude-review-v2",
            "native-review-request-" + "9" * 64,
            "a" * 64,
            review_evidence=ReviewEvidencePayload(
                "slice review found the carried blocker",
                "the open finding can survive into a later Slice review",
                "the Slice-review work unit loses its reviewer binding",
            ),
            pre_mortem="the Slice review could approve with an open blocker",
        ),
        logical_id="review-final-denied-anchor",
        idempotency_key="review:final:denied-anchor",
        fingerprint_sha256=FINGERPRINT,
        operation="claude_slice_review",
    )


def _state_projection_anchor_chains(tmp_path: Path) -> dict[str, tuple]:
    chains: dict[str, tuple] = {}
    for chain_name, carried_validation in (
        ("standard-validation", False),
        ("carried-validation", True),
    ):
        bridge = _state_projection_bridge(tmp_path, chain_name)
        chains[chain_name] = _journey(
            bridge,
            carried_validation=carried_validation,
        )

    for chain_name, append_variant in (
        ("gate-override", _append_projection_gate_override),
        ("invocation-failure", _append_projection_invocation_failure),
        ("commit-bound", _append_projection_commit_binding),
        ("denied-slice-review", _append_projection_denied_slice_review),
    ):
        bridge = _state_projection_bridge(tmp_path, chain_name)
        _journey(bridge, carried_validation=False)
        append_variant(bridge)
        chains[chain_name] = bridge.store.load_chain()
    return chains


def _state_projection_anchor_entries(tmp_path: Path) -> list[dict[str, object]]:
    chains = _state_projection_anchor_chains(tmp_path)

    entries: list[dict[str, object]] = []
    for case_id, chain_name, prefix_length in STATE_PROJECTION_ANCHOR_CASES:
        prefix = (
            chains[chain_name]
            if prefix_length is None
            else chains[chain_name][:prefix_length]
        )
        canonical_document = project_workflow_state(
            replay_artifacts(prefix, RUN_ID)
        ).canonical_document
        record_ids = tuple(record.record_id for record in prefix)
        entries.append(
            {
                "case_id": case_id,
                "chain_name": chain_name,
                "prefix_length": len(prefix),
                "prefix_head_record_id": record_ids[-1],
                "prefix_record_ids_sha256": hashlib.sha256(
                    canonical_json(record_ids)
                ).hexdigest(),
                "canonical_document_sha256": hashlib.sha256(
                    canonical_document
                ).hexdigest(),
                "canonical_document_base64": base64.b64encode(
                    canonical_document
                ).decode("ascii"),
            }
        )
    return entries


def test_first_slice_projection_uses_structural_start_even_when_equal_to_base(
    tmp_path: Path,
) -> None:
    bridge = _state_projection_bridge(tmp_path, "identity-start-equals-base")
    chain = _journey(bridge)
    boundary_index = next(
        index
        for index, record in enumerate(chain)
        if isinstance(record.payload, SliceBoundaryPayload)
    )
    replay = replay_artifacts(chain[:boundary_index], RUN_ID)

    assert replay.run_identity is not None
    assert replay.run_identity.first_slice_start_commit == "b" * 40
    assert replay.run_identity.first_slice_start_commit == replay.run_identity.branch_base
    assert project_workflow_state(replay).state.current_slice.start_commit == "b" * 40


def _load_state_projection_baseline() -> dict[str, object]:
    document = json.loads(STATE_PROJECTION_BASELINE.read_text(encoding="utf-8"))
    assert set(document) == {
        "schema_version",
        "source_commit",
        "reducer_version",
        "fixed_time",
        "entries",
    }
    assert document["schema_version"] == "state-projection-baseline-v1"
    assert document["source_commit"] == STATE_PROJECTION_BASE_COMMIT
    assert document["reducer_version"] == STATE_PROJECTION_REDUCER_VERSION
    assert document["fixed_time"] == STATE_PROJECTION_FIXED_TIME
    entries = document["entries"]
    assert isinstance(entries, list)
    assert len(entries) == len(STATE_PROJECTION_ANCHOR_CASES)
    assert len({entry["case_id"] for entry in entries}) == len(entries)
    for entry in entries:
        assert set(entry) == {
            "case_id",
            "chain_name",
            "prefix_length",
            "prefix_head_record_id",
            "prefix_record_ids_sha256",
            "canonical_document_sha256",
            "canonical_document_base64",
        }
        assert isinstance(entry["case_id"], str) and entry["case_id"]
        assert entry["chain_name"] in {
            chain_name for _case_id, chain_name, _prefix_length
            in STATE_PROJECTION_ANCHOR_CASES
        }
        assert isinstance(entry["prefix_length"], int) and entry["prefix_length"] > 0
        assert isinstance(entry["prefix_head_record_id"], str)
        assert isinstance(entry["prefix_record_ids_sha256"], str)
        canonical_document = base64.b64decode(
            entry["canonical_document_base64"], validate=True
        )
        assert hashlib.sha256(canonical_document).hexdigest() == (
            entry["canonical_document_sha256"]
        )
        assert canonical_json(json.loads(canonical_document)) == canonical_document
    return document


def _anchored_document(document: dict[str, object], case_id: str) -> bytes:
    entry = next(
        item for item in document["entries"] if item["case_id"] == case_id
    )
    return base64.b64decode(entry["canonical_document_base64"], validate=True)


def _assert_state_projection_anchor_case(tmp_path: Path, case_id: str) -> None:
    _case_id, chain_name, prefix_length = next(
        item for item in STATE_PROJECTION_ANCHOR_CASES if item[0] == case_id
    )
    chain = _state_projection_anchor_chains(tmp_path)[chain_name]
    prefix = chain if prefix_length is None else chain[:prefix_length]
    actual = project_workflow_state(replay_artifacts(prefix, RUN_ID)).canonical_document
    assert actual == _anchored_document(_load_state_projection_baseline(), case_id)


def _state_projection_local_call_closure(
    functions: dict[str, ast.FunctionDef],
) -> set[str]:
    closure: set[str] = set()
    pending = ["project_workflow_state"]
    boundaries = set(STATE_PROJECTION_SHARED_BOUNDARIES)
    while pending:
        function_name = pending.pop()
        callees = {
            node.func.id
            for node in ast.walk(functions[function_name])
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in functions
        }
        for callee in callees - closure:
            closure.add(callee)
            if callee not in boundaries:
                pending.append(callee)
    return closure


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
        WorkflowStep.CODEX_CORRECTION,
        open_findings=(),
    )
    state = replace(
        state,
        current_slice_id=3,
        current_work_unit_id=4,
        current_step=WorkflowStep.CODEX_CORRECTION,
        slices=(*state.slices, correction_slice),
        work_units=(*state.work_units, provisional_unit),
        updated_at="2026-08-31T10:00:11+00:00",
    )
    correction_history = WorkflowHistory(4)
    archive = (*archive, slice_two_history)
    snapshots[34] = _history_mirror(state, correction_history, archive)
    state = replace(
        state,
        work_units=(
            *state.work_units[:-1],
            replace(state.current_work_unit, open_findings=("C-01",)),
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
    snapshots[43] = _history_mirror(state, correction_history, archive)
    round_two = replace(
        state.current_work_unit, round_number=2, request_sequence=2
    )
    state = replace(state, work_units=(*state.work_units[:-1], round_two))
    snapshots[44] = _history_mirror(state, correction_history, archive)
    return snapshots


def test_state_projection_baseline_matches_pre_cut_bytes(tmp_path: Path) -> None:
    baseline = _load_state_projection_baseline()
    expected_entries = _state_projection_anchor_entries(tmp_path)
    assert baseline["entries"] == expected_entries

    documents = {
        entry["case_id"]: json.loads(
            base64.b64decode(entry["canonical_document_base64"], validate=True)
        )
        for entry in baseline["entries"]
    }
    assert documents["minimal-cursor"]["current_work_unit_id"] == 1
    assert documents["bounded-slice"]["slices"][0]["scope_paths"] == ["src/one.py"]
    assert documents["halted-review-gate"]["work_units"][-1]["status"] == (
        "awaiting_user_decision"
    )
    assert documents["resumed-review-gate"]["work_units"][-1]["status"] == (
        "in_progress"
    )
    assert documents["defined-correction"]["work_units"][-1]["kind"] == "slice"
    assert documents["validated-correction"]["runtime_history"]["4"][
        "attestation_record_refs"
    ]
    assert documents["approved-correction"]["runtime_history"]["4"][
        "review_record_refs"
    ]
    assert documents["second-correction-round"]["work_units"][-1][
        "round_number"
    ] == 2
    assert (
        documents["carried-validation"]["runtime_history"]["3"][
            "attestation_record_refs"
        ]
    )
    gate_override = documents["gate-override"]["work_units"][-1]
    assert gate_override["status"] == "awaiting_user_decision"
    assert gate_override["gate"]["resume_step"] == "codex_correction"
    assert gate_override["active_test_paths"] == [
        "tests/test_workflow_history_projection.py"
    ]
    failure = documents["invocation-failure"]["work_units"][-1][
        "invocation_failures"
    ][0]
    assert failure["idempotency_key"] == "invoke:projection-quota:attempt-1"
    assert failure["resume_at_utc"] == "2026-09-03T12:01:00+00:00"
    assert documents["commit-bound"]["slices"][-1]["commit_ref"] == (
        "b42-state-projection-commit"
    )
    denied_review = documents["denied-slice-review"]["work_units"][-1]
    assert denied_review["kind"] == "slice"
    assert denied_review["reviewer"] == "claude"
    assert denied_review["open_findings"] == ["C-01"]




def test_multi_slice_open_findings_match_authoritative_reduction_in_state_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = _state_projection_bridge(tmp_path, "multi-slice-open-findings")
    _journey(bridge)
    bridge.append(
        FindingTransitionPayload(
            "C-01",
            Role.CLAUDE,
            Role.CLAUDE,
            "status_changed",
            FindingSeverity.BLOCKER,
            "closed",
            "the earlier correction finding is resolved",
            "4",
        ),
        logical_id="finding-C-01",
        idempotency_key="finding:C-01:closed",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )

    _append_transition(
        bridge,
        revision=8,
        slice_id="4",
        slice_status="in_progress",
        work_unit_id="5",
        step="claude_slice_review",
        work_unit_status="in_progress",
    )
    _append_policy_gate(bridge, work_unit_id="5")
    _append_boundary(
        bridge, slice_id="4", path="src/four.py", fingerprint="4" * 64
    )
    bridge.append(
        WorkUnitPayload("4", 1, ("src/four.py",)),
        logical_id="work-unit-5",
        idempotency_key="work-unit:5:round:1",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    bridge.append(
        FindingTransitionPayload(
            "C-02",
            Role.CLAUDE,
            Role.CLAUDE,
            "opened",
            FindingSeverity.FINDING,
            "open",
            "the first Slice review records an observation",
            "5",
            "first Slice observation",
            "the observation remains visible in the state mirror",
            "04",
            1,
        ),
        logical_id="finding-C-02",
        idempotency_key="finding:C-02:opened",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    append_provider_decision_authority(
        bridge,
        ReviewPayload(
            Role.CLAUDE,
            "5",
            "approved",
            ("C-02",),
            None,
            "native-claude-review-v2",
            "native-review-request-" + "2" * 64,
            "3" * 64,
            review_evidence=ReviewEvidencePayload(
                "first Slice finding projection",
                "a later Slice could lose the open observation",
                "the state mirror becomes empty while the ledger remains open",
            ),
            pre_mortem="the next Slice could reconstruct findings from its empty definition",
        ),
        logical_id="review-slice-findings-1",
        idempotency_key="review:slice-findings:1",
        fingerprint_sha256=FINGERPRINT,
        operation="claude_slice_review",
    )

    _append_transition(
        bridge,
        revision=9,
        slice_id="5",
        slice_status="in_progress",
        work_unit_id="6",
        step="claude_slice_review",
        work_unit_status="in_progress",
    )
    _append_policy_gate(bridge, work_unit_id="6")
    _append_boundary(
        bridge, slice_id="5", path="src/five.py", fingerprint="5" * 64
    )
    bridge.append(
        WorkUnitPayload("5", 1, ("src/five.py",)),
        logical_id="work-unit-6",
        idempotency_key="work-unit:6:round:1",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    next_slice_replay = replay_artifacts(bridge.store.load_chain(), RUN_ID)
    next_slice_open = reduce_findings(next_slice_replay).open_set.finding_ids
    assert next_slice_open == ("C-02",)
    assert project_workflow_state(
        next_slice_replay
    ).state.current_work_unit.open_findings == next_slice_open
    bridge.append(
        FindingTransitionPayload(
            "C-02",
            Role.CLAUDE,
            Role.CODEX,
            "responded",
            FindingSeverity.FINDING,
            "open",
            "Codex accepts the observation",
            "6",
            response_decision="accepted",
        ),
        logical_id="finding-C-02",
        idempotency_key="finding:C-02:responded",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    bridge.append(
        FindingTransitionPayload(
            "C-02",
            Role.CLAUDE,
            Role.CLAUDE,
            "status_changed",
            FindingSeverity.FINDING,
            "closed",
            "the accepted observation is resolved",
            "6",
        ),
        logical_id="finding-C-02",
        idempotency_key="finding:C-02:closed",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    bridge.append(
        FindingTransitionPayload(
            "C-03",
            Role.CLAUDE,
            Role.CLAUDE,
            "opened",
            FindingSeverity.FINDING,
            "open",
            "the second Slice review records another observation",
            "6",
            "second Slice observation",
            "the remaining observation matches the reduced open set",
            "05",
            1,
        ),
        logical_id="finding-C-03",
        idempotency_key="finding:C-03:opened",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    append_provider_decision_authority(
        bridge,
        ReviewPayload(
            Role.CLAUDE,
            "6",
            "approved",
            ("C-02", "C-03"),
            None,
            "native-claude-review-v2",
            "native-review-request-" + "4" * 64,
            "5" * 64,
            review_evidence=ReviewEvidencePayload(
                "second Slice finding projection",
                "a closed finding could remain in the state mirror",
                "the projected set diverges from the authoritative reduction",
            ),
            pre_mortem="a status transition could be ignored during state replay",
        ),
        logical_id="review-slice-findings-2",
        idempotency_key="review:slice-findings:2",
        fingerprint_sha256=FINGERPRINT,
        operation="claude_slice_review",
    )

    original_result = artifact_replay_module._result
    result_sizes: list[int] = []

    def counted_result(*args, **kwargs):  # type: ignore[no-untyped-def]
        result_sizes.append(len(args[1]))
        return original_result(*args, **kwargs)

    monkeypatch.setattr(artifact_replay_module, "_result", counted_result)
    chain = bridge.store.load_chain()
    positions = {record.record_id: index for index, record in enumerate(chain)}
    reviews = tuple(
        record for record in chain if isinstance(record.payload, ReviewPayload)
    )
    assert len(reviews) >= 2
    for review_record in reviews:
        assert artifact_replay_module._review_prefix_finding_ids(
            chain, positions, review_record
        ) is not None
    assert result_sizes == []

    replay = replay_artifacts(chain, RUN_ID)
    reduction = reduce_findings(replay)
    projected = project_workflow_state(replay)
    assert reduction.open_set.finding_ids == ("C-03",)
    assert projected.state.work_units[-2].open_findings == ("C-02",)
    assert projected.state.current_work_unit.open_findings == (
        reduction.open_set.finding_ids
    )
    closed = next(item for item in reduction.ledger.findings if item.finding_id == "C-02")
    assert closed.responses[0].decision.value == "ACCEPTED"
    assert "C-02" not in projected.state.current_work_unit.open_findings

    state_file = tmp_path / ".orchestrator" / "state.json"
    write_workflow_state_projection(
        state_file,
        ResumeResolution(
            projected.state,
            ProtocolMode.STRUCTURED_V2,
            replay.records[-1].record_id,
            replay,
        ),
        allowed_roots=(tmp_path, Path.cwd()),
    )
    cached = json.loads(state_file.read_text(encoding="utf-8"))
    assert cached["reducer_version"] == (
        "structured-v2-schema-2-state-v3-target-class-round-exit-v1"
    )
    assert cached["state"]["work_units"][-1]["open_findings"] == ["C-03"]


def test_state_projection_anchor_detects_omitted_assembly_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = artifact_replay_module._assemble_workflow_state_document

    def omit_protocol_binding(*args, **kwargs):
        document = original(*args, **kwargs)
        document = dict(document)
        del document["protocol_binding"]
        return document

    monkeypatch.setattr(
        artifact_replay_module,
        "_assemble_workflow_state_document",
        omit_protocol_binding,
    )
    with pytest.raises(AssertionError):
        _assert_state_projection_anchor_case(tmp_path, "second-correction-round")


def test_state_projection_anchor_detects_schema_valid_value_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = artifact_replay_module._project_work_unit_document

    def change_return_count(*args, **kwargs):
        document = original(*args, **kwargs)
        if document["work_unit_id"] == 4:
            document = dict(document)
            document["codex_return_count"] = 1
        return document

    monkeypatch.setattr(
        artifact_replay_module,
        "_project_work_unit_document",
        change_return_count,
    )
    with pytest.raises(AssertionError):
        _assert_state_projection_anchor_case(tmp_path, "second-correction-round")


def test_state_projection_anchor_detects_reordered_work_unit_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = artifact_replay_module._project_work_unit_documents

    def reverse_work_units(*args, **kwargs):
        return list(reversed(original(*args, **kwargs)))

    monkeypatch.setattr(
        artifact_replay_module,
        "_project_work_unit_documents",
        reverse_work_units,
    )
    with pytest.raises(ArtifactReplayError) as caught:
        _assert_state_projection_anchor_case(tmp_path, "second-correction-round")
    assert caught.value.code is ReplayDiagnosticCode.RECORD_MISSING


def test_state_projection_reducer_is_split_into_named_bounded_helpers() -> None:
    source = Path(project_workflow_state.__code__.co_filename).read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {
        item.name: item
        for item in tree.body
        if isinstance(item, ast.FunctionDef)
    }
    assert _state_projection_local_call_closure(functions) == {
        *STATE_PROJECTION_HELPERS,
        *STATE_PROJECTION_SHARED_BOUNDARIES,
    }
    assert all("_part_" not in name for name in functions)
    for name in ("project_workflow_state", *STATE_PROJECTION_HELPERS):
        function = functions[name]
        assert function.end_lineno is not None
        assert function.end_lineno - function.lineno + 1 < 200

    projector_source = ast.get_source_segment(source, functions["project_workflow_state"])
    assert projector_source is not None
    assert tuple(
        projector_source.index(name)
        for name in (
            "_ensure_projection_event_prefix",
            "_index_projection_transitions",
            "_project_slice_documents",
            "_project_work_unit_documents",
            "_assemble_workflow_state_document",
        )
    ) == tuple(
        sorted(
            projector_source.index(name)
            for name in (
                "_ensure_projection_event_prefix",
                "_index_projection_transitions",
                "_project_slice_documents",
                "_project_work_unit_documents",
                "_assemble_workflow_state_document",
            )
        )
    )
    work_unit_calls = {
        node.func.id
        for node in ast.walk(functions["_project_work_unit_document"])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert {
        "_project_work_unit_round_and_kind",
        "_project_work_unit_gate",
        "_project_work_unit_open_findings",
    } <= work_unit_calls


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
        "run_binding": "requires exactly one run identity and run profile",
        "event_tail": "workflow event prefix",
        "identity_profile_task": "requires run identity, profile, and task",
        "cursor": "requires a current cursor",
        "slice_start": "a started slice requires start_commit",
        "gate_pair": "work-unit status and gate status must change together",
        "slice_start": "a started slice requires start_commit",
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
            json.dumps(_normalized_independent_mirror(mirrors[end], replay))
        ) == first.to_document(), end
        assert isinstance(first.state, WorkflowState)
        assert set(first.to_document()) == set(WorkflowState.__dataclass_fields__)
        projected_prefixes.append(first.to_document())
        accepted_ends.append(end)

    assert len(projected_prefixes) + len(rejected) == len(chain) == 44
    assert set(accepted_ends).isdisjoint(rejected)
    assert tuple(accepted_ends) == (
        6, 7, 8, 10, 12, 13, 14, 15, 16, 21, 22, 25, 28, 34, 35, 36,
        38, 39, 43, 44,
    )
    assert set(mirrors) == set(accepted_ends)
    assert rejected == {
        1: "run_binding",
        2: "run_binding",
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
        30: "slice_start",
        31: "slice_start",
        32: "slice_start",
        33: "slice_start",
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
    assert final["work_units"][-1]["kind"] == "slice"
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
    assert mirror.runtime_history != projected.state.runtime_history
    assert project_workflow_state(replay).canonical_document == projected.canonical_document

    hydrated = _hydrate_record_history(
        WorkflowHistory(4), replay, bridge.store.read_blob
    )
    assert hydrated.findings == reduce_findings(replay).request_subset(
        work_unit_id=4,
        finding_ids=("C-01",),
    ).findings
    assert hydrated.events == expected.events
    assert hydrated.attestations == expected.attestations
    assert hydrated.last_claude_fingerprint == expected.last_claude_fingerprint
    assert hydrated.latest_claude_review == expected.latest_claude_review
    assert hydrated.active_review_packet is None

    missing_attestation = replace(expected, attestations=())
    damaged = replace(
        projected.state,
        runtime_history={
            "archive": [],
            "current": missing_attestation.to_dict(),
        },
    )
    assert damaged.runtime_history != projected.state.runtime_history
    assert project_workflow_state(replay).canonical_document == projected.canonical_document


def test_slice_review_audit_reuses_carried_attestation(tmp_path: Path) -> None:
    bridge = ArtifactBridge(ArtifactStore(tmp_path, RUN_ID))
    _journey(bridge, carried_validation=True)
    _append_transition(
        bridge,
        revision=8,
        slice_id="3",
        slice_status="in_progress",
        work_unit_id="5",
        step="claude_slice_review",
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
    slice_review = append_provider_decision_authority(
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
        operation="claude_slice_review",
    )
    replay = replay_artifacts(bridge.store.load_chain(), RUN_ID)
    assert not any(
        event.event_kind == "validation" and event.work_unit_id == "5"
        for event in replay.workflow_events
    )

    review_contract = next(
        item
        for item in project_review_contracts(replay, bridge.store.read_blob)
        if item.record_id == slice_review.record_id
    )
    attestation = review_contract.result.validation
    assert attestation is not None
    mirror_history = WorkflowHistory(
        5,
        attestations=(attestation,),
        last_claude_fingerprint=slice_review.fingerprint.sha256,
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
    assert entries[0].label == "Arbeitseinheit 01 – Planung"
    assert final_entry.label == "Arbeitseinheit 05 – Slice 03"
    assert any(" – Slice " in item.label for item in entries)
    assert all("Work Unit" not in item.label for item in entries)
    assert final_entry.projection.events == (
        ValidationAuditEvent(1, 3, attestation),
        ReviewAuditEvent(
            2,
            3,
            1,
            review_contract.result,
            (),
            is_final_review=False,
        ),
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
        if event_record.payload.event_kind == "run":
            with pytest.raises(
                ArtifactReplayError,
                match="requires exactly one run identity and run profile",
            ) as caught:
                replay_artifacts(chain[:index], RUN_ID)
            assert caught.value.code is ReplayDiagnosticCode.RECORD_MISSING
            continue
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


def test_review_accepts_foreign_finding_origin_already_in_complete_ledger(
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
            "a run-wide finding omitted by the prior request subset",
            "3",
            "foreign origin",
            "accept origin 02 from the complete ledger",
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
        operation="codex_correction",
    )
    replay = replay_artifacts(bridge.store.load_chain(), RUN_ID)

    histories = _attach_record_events({}, replay, bridge.store.read_blob)

    event = histories[4].events[-1]
    assert isinstance(event, ReviewAuditEvent)
    assert event.allowed_finding_origins == ("02",)


def test_chain_without_workflow_events_is_rejected_fail_closed(tmp_path: Path) -> None:
    bridge = ArtifactBridge(ArtifactStore(tmp_path, "r9-pre-event-chain"))
    bridge.append(
        RunIdentityPayload("task.md", "feature/r9", "b" * 40, "b" * 40, "IMPLEMENT", None),
        logical_id="run-identity",
        idempotency_key="run-identity",
        fingerprint_sha256=FINGERPRINT,
        fingerprint_kind=FingerprintKind.CONTRACT,
    )
    bridge.append(
        RunProfilePayload(
            RoleProfilePayload("model-a", "medium"),
            RoleProfilePayload("model-b", "high"),
        ),
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
                "max_codex_returns": 6,
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
    all_functions = {
        item.name: item
        for item in tree.body
        if isinstance(item, ast.FunctionDef)
    }
    closure = _state_projection_local_call_closure(all_functions)
    functions = tuple(
        all_functions[name]
        for name in {"project_workflow_state", *closure}
    )
    assert {function.name for function in functions} == {
        "project_workflow_state",
        *STATE_PROJECTION_HELPERS,
        *STATE_PROJECTION_SHARED_BOUNDARIES,
    }
    calls = {
        node.func.id if isinstance(node.func, ast.Name) else node.func.attr
        for function in functions
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Name, ast.Attribute))
    }
    assert not calls & {
        "open", "read_text", "read_bytes", "write_text", "write_bytes",
        "load_chain", "now", "utcnow", "time",
    }
