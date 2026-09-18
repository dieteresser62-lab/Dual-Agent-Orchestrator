from __future__ import annotations

from dataclasses import asdict, replace

import pytest

from artifact_models import (
    AgentResultPayload,
    ArtifactRecord,
    BindingPayload,
    canonical_json,
    CommandSpec,
    Fingerprint,
    FingerprintKind,
    FindingSeverity,
    FindingTransitionPayload,
    FindingHandoffImportPayload,
    FamilyBindingPayload,
    GateDecisionPayload,
    GatePayload,
    GateTransitionPayload,
    ImportedFindingTransition,
    finding_transition_sequence_sha256,
    RecordType,
    ReviewPayload,
    Role,
    RoleProfilePayload,
    RunIdentityPayload,
    RunProfilePayload,
    SliceBoundaryPayload,
    WorkflowPolicyPayload,
    WorkflowTransitionPayload,
    TaskPayload,
    ValidationAttestationPayload,
    ValidationResult,
    WorkUnitPayload,
    ProviderAttemptPayload,
    ProviderInputComponentPayload,
    ProviderInputMeasurementPayload,
    ProviderUsagePayload,
)
from artifact_replay import (
    ArtifactReplayError,
    ReplayedWorkUnitState,
    ReplayedWorkflowCursor,
    ReplayDiagnosticCode,
    project_work_unit_reviewers,
    replay_artifacts as replay_artifacts_checked,
    replay_findings,
)
from contracts import FindingResponseDecision, FindingStatus
from workflow_state import Reviewer, WorkflowStep, init_workflow_state


FP = Fingerprint(FingerprintKind.IMPLEMENTATION, "a" * 64)


def replay_artifacts(records, expected_run_id, **kwargs):  # type: ignore[no-untyped-def]
    """Exercise replay mechanics without duplicating the R7/R8 fixtures here."""
    kwargs.setdefault("require_content_authority", False)
    kwargs.setdefault("require_review_authority", False)
    return replay_artifacts_checked(records, expected_run_id, **kwargs)


def _agent_result(work_unit_id: str, test_files: tuple[str, ...] = ()) -> AgentResultPayload:
    return AgentResultPayload(
        Role.CODEX,
        work_unit_id,
        "ready",
        test_files,
        "native-codex-v2",
        "native-codex-request-" + "b" * 64,
        "c" * 64,
    )


def _review(work_unit_id: str, evidence: str) -> ReviewPayload:
    return ReviewPayload(
        Role.CLAUDE,
        work_unit_id,
        "approved",
        (),
        evidence,
        "native-claude-review-v2",
        "native-review-request-" + "b" * 64,
        "c" * 64,
    )


def _append(
    records: list[ArtifactRecord],
    logical_id: str,
    payload: object,
    *,
    fingerprint: Fingerprint = FP,
    revision: int = 1,
) -> ArtifactRecord:
    if not records and not isinstance(payload, (RunIdentityPayload, RunProfilePayload)):
        _append(
            records,
            "run-identity",
            RunIdentityPayload(
                "inbox/backlog/replay.md",
                "feature/replay",
                "b" * 40,
                "b" * 40,
                "IMPLEMENT",
                None,
            ),
        )
        _append(
            records,
            "run-profile",
            RunProfilePayload(
                RoleProfilePayload("implementer-model", "medium"),
                RoleProfilePayload("reviewer-model", "high"),
            ),
        )
    record = ArtifactRecord.create(
        run_id="run-replay",
        logical_id=logical_id,
        revision=revision,
        fingerprint=fingerprint,
        predecessor_ids=((records[-1].record_id,) if records else ()),
        created_at=f"2026-08-21T10:00:{len(records):02d}+00:00",
        idempotency_key=(
            f"replay:{logical_id}" if revision == 1 else f"replay:{logical_id}:{revision}"
        ),
        payload=payload,  # type: ignore[arg-type]
    )
    records.append(record)
    return record


def _chain() -> tuple[ArtifactRecord, ...]:
    records: list[ArtifactRecord] = []
    _append(
        records,
        "run-identity",
        RunIdentityPayload(
            "inbox/backlog/replay.md",
            "feature/replay",
            "b" * 40,
            "b" * 40,
            "IMPLEMENT",
            None,
        ),
    )
    _append(
        records,
        "run-profile",
        RunProfilePayload(
            RoleProfilePayload("implementer-model", "medium"),
            RoleProfilePayload("reviewer-model", "high"),
        ),
    )
    _append(records, "work-unit-1", WorkUnitPayload("1", 1, ("src/a.py",)))
    _append(
        records,
        "agent-1",
        _agent_result("1", ("tests/test_a.py",)),
    )
    attestation = _append(
        records,
        "validation-1",
        ValidationAttestationPayload(
            (
                ValidationResult(
                    CommandSpec("pytest", ("pytest", "tests/test_a.py")),
                    "pass",
                    0,
                    "b" * 64,
                ),
            ),
            Role.ORCHESTRATOR,
            "b" * 64,
            "ar1-" + "0" * 64,
        ),
    )
    review = _append(
        records,
        "review-1",
        _review("1", "checked replay"),
    )
    _append(
        records,
        "binding-1",
        BindingPayload("commit", "deadbeef", attestation.record_id, (review.record_id,)),
    )
    return tuple(records)


def _assert_code(records: tuple[ArtifactRecord, ...], code: ReplayDiagnosticCode) -> None:
    with pytest.raises(ArtifactReplayError) as caught:
        replay_artifacts(records, "run-replay")
    assert caught.value.code is code


def test_replay_is_deterministic_and_does_not_mutate_input() -> None:
    chain = _chain()
    before = tuple(record.canonical_json() for record in chain)

    first = replay_artifacts(chain, "run-replay")
    second = replay_artifacts(chain, "run-replay")

    assert first == second
    assert first.semantic_digest == second.semantic_digest
    assert first.audit_events == second.audit_events
    assert tuple(record.canonical_json() for record in chain) == before


def test_replay_projects_run_identity_and_profiles_without_external_state() -> None:
    records: list[ArtifactRecord] = []
    identity = RunIdentityPayload(
        task_file="C:\\workspace\\inbox\\r1.md",
        branch="feature/run-identity",
        branch_base="b" * 40,
        first_slice_start_commit="b" * 40,
        execution_mode="PLAN_ONLY",
        audit_report_path=None,
    )
    profile = RunProfilePayload(
        implementer=RoleProfilePayload("gpt-5.6-sol", "max"),
        reviewer=RoleProfilePayload("opus", "max"),
    )
    _append(records, "run-identity", identity)
    _append(records, "run-profile", profile)

    replay = replay_artifacts(tuple(records), "run-replay")

    assert replay.run_identity == identity
    assert replay.run_profile == profile
    assert canonical_json(asdict(replay.run_identity)) == canonical_json(asdict(identity))
    assert canonical_json(asdict(replay.run_profile)) == canonical_json(asdict(profile))


def test_second_family_binding_write_with_different_values_is_rejected() -> None:
    records: list[ArtifactRecord] = []
    _append(
        records,
        "run-identity",
        RunIdentityPayload(
            "inbox/backlog/replay.md",
            "feature/replay",
            "b" * 40,
            "c" * 40,
            "IMPLEMENT",
            None,
        ),
    )
    first = FamilyBindingPayload(
        "family-1", "b" * 40, ("src/a.py",), None, None, 1, None, None
    )
    _append(
        records,
        "run-profile",
        RunProfilePayload(
            RoleProfilePayload("implementer-model", "medium"),
            RoleProfilePayload("reviewer-model", "high"),
            family_binding=first,
        ),
    )
    _append(
        records,
        "run-profile",
        RunProfilePayload(
            RoleProfilePayload("implementer-model", "medium"),
            RoleProfilePayload("reviewer-model", "high"),
            family_binding=replace(first, cycle_number=2),
        ),
        revision=2,
    )

    _assert_code(tuple(records), ReplayDiagnosticCode.RECORD_DUPLICATE)


@pytest.mark.parametrize("missing", ("identity", "profile"))
def test_pre_r1_chain_without_complete_run_binding_is_rejected(missing: str) -> None:
    records: list[ArtifactRecord] = []
    if missing != "identity":
        _append(
            records,
            "run-identity",
            RunIdentityPayload(
                "inbox/backlog/replay.md",
                "feature/replay",
                "b" * 40,
                "b" * 40,
                "IMPLEMENT",
                None,
            ),
        )
    if missing != "profile":
        _append(
            records,
            "run-profile",
            RunProfilePayload(
                RoleProfilePayload("implementer-model", "medium"),
                RoleProfilePayload("reviewer-model", "high"),
            ),
        )

    with pytest.raises(ArtifactReplayError) as caught:
        replay_artifacts(tuple(records), "run-replay")

    assert caught.value.code is ReplayDiagnosticCode.RECORD_MISSING
    assert "requires exactly one run identity and run profile" in str(caught.value)
    assert f"missing run {missing}" in str(caught.value)


def test_replay_projects_r2_cursor_status_policy_and_reviewer_without_external_state() -> None:
    records: list[ArtifactRecord] = []
    _append(
        records,
        "workflow-transition",
        WorkflowTransitionPayload("2", "pending", None, None, None),
    )
    _append(
        records,
        "workflow-transition",
        WorkflowTransitionPayload("1", "in_progress", "1", "completed", "completed"),
        revision=2,
    )
    _append(records, "workflow-policy-1", WorkflowPolicyPayload("1", 0, 4))
    _append(
        records,
        "workflow-transition",
        WorkflowTransitionPayload(
            "1", "in_progress", "2", "codex_implementation", "in_progress"
        ),
        revision=3,
    )
    _append(records, "workflow-policy-2", WorkflowPolicyPayload("2", 0, 4))
    _append(records, "work-unit-2", WorkUnitPayload("1", 1, ("src/a.py",)))
    _append(
        records,
        "finding-C-01",
        FindingTransitionPayload(
            "C-01",
            Role.CLAUDE,
            Role.CLAUDE,
            "opened",
            FindingSeverity.BLOCKER,
            "open",
            "Correction required.",
            "2",
            "Correction required.",
            "The next round fixes it.",
            "1",
            1,
        ),
    )
    _append(
        records,
        "review-2",
        ReviewPayload(
            Role.CLAUDE,
            "2",
            "denied",
            ("C-01",),
            None,
            "native-claude-review-v2",
            "native-review-request-" + "d" * 64,
            "e" * 64,
        ),
    )
    _append(
        records,
        "workflow-transition",
        WorkflowTransitionPayload(
            "1", "in_progress", "2", "codex_correction", "in_progress"
        ),
        revision=4,
    )
    _append(
        records,
        "workflow-policy-2",
        WorkflowPolicyPayload("2", 1, 4),
        revision=2,
    )

    replay = replay_artifacts(tuple(records), "run-replay")
    projected = {
        "cursor": asdict(replay.workflow_cursor),
        "slice_statuses": replay.slice_statuses,
        "work_units": tuple(asdict(item) for item in replay.work_unit_states),
        "policies": tuple(asdict(item) for item in replay.workflow_policies),
        "reviewers": tuple(
            (work_unit_id, None if reviewer is None else reviewer.value)
            for work_unit_id, reviewer in replay.work_unit_reviewers
        ),
    }
    mirror = {
        "cursor": asdict(ReplayedWorkflowCursor("1", "2", "codex_correction")),
        "slice_statuses": (("1", "in_progress"), ("2", "pending")),
        "work_units": (
            asdict(ReplayedWorkUnitState("1", "1", "completed", "completed")),
            asdict(ReplayedWorkUnitState("2", "1", "in_progress", "codex_correction")),
        ),
        "policies": (
            asdict(WorkflowPolicyPayload("1", 0, 4)),
            asdict(WorkflowPolicyPayload("2", 1, 4)),
        ),
        "reviewers": (("1", None), ("2", "claude")),
    }

    assert canonical_json(projected) == canonical_json(mirror)
    assert project_work_unit_reviewers(tuple(records)) == (
        ("1", None),
        ("2", Role.CLAUDE),
    )


def test_replay_projects_r3_slice_boundaries_without_flattening_groups() -> None:
    records: list[ArtifactRecord] = []
    _append(
        records,
        "workflow-transition",
        WorkflowTransitionPayload(
            "1", "in_progress", "1", "codex_implementation", "in_progress"
        ),
    )
    grouped = SliceBoundaryPayload(
        "1",
        "b" * 40,
        (("src/new.py", "src/old.py"),),
        "c" * 64,
    )
    _append(records, "slice-boundary-1", grouped)
    _append(
        records,
        "workflow-transition",
        WorkflowTransitionPayload(
            "2", "in_progress", "2", "codex_implementation", "in_progress"
        ),
        revision=2,
    )
    flat = SliceBoundaryPayload(
        "2",
        "d" * 40,
        (("src/new.py",), ("src/old.py",)),
        "e" * 64,
    )
    _append(records, "slice-boundary-2", flat)

    replay = replay_artifacts(tuple(records), "run-replay")

    assert replay.slice_boundaries == (grouped, flat)
    assert set(grouped.scope_change_groups[0]) == {
        path for group in flat.scope_change_groups for path in group
    }
    assert grouped.scope_change_groups != flat.scope_change_groups


def test_slice_boundary_revision_preserves_start_and_monotonically_extends_groups() -> None:
    records: list[ArtifactRecord] = []
    _append(
        records,
        "workflow-transition",
        WorkflowTransitionPayload(
            "1", "in_progress", "1", "codex_implementation", "in_progress"
        ),
    )
    _append(
        records,
        "slice-boundary-1",
        SliceBoundaryPayload("1", "b" * 40, (("src/a.py",),), "c" * 64),
    )
    expanded = SliceBoundaryPayload(
        "1", "b" * 40, (("src/a.py",), ("tests/a.py",)), "c" * 64
    )
    _append(records, "slice-boundary-1", expanded, revision=2)

    assert replay_artifacts(tuple(records), "run-replay").slice_boundaries == (
        expanded,
    )

    changed = list(records[:-1])
    _append(
        changed,
        "slice-boundary-1",
        SliceBoundaryPayload("1", "b" * 40, (("src/a.py",),), "d" * 64),
        revision=2,
    )
    _assert_code(
        tuple(changed), ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH
    )


def test_reviewer_projection_matches_state_v3_before_and_after_denial() -> None:
    state = init_workflow_state(
        run_id="reviewer-projection",
        task_file="task.md",
        branch="feature/reviewer-projection",
        branch_base="a" * 40,
        first_slice_start_commit="a" * 40,
        slice_count=1,
    )
    records: list[ArtifactRecord] = []
    _append(
        records,
        "workflow-transition",
        WorkflowTransitionPayload(
            "1", "in_progress", "1", "codex_plan", "in_progress"  # allowlist:provider -- persisted step vocabulary
        ),
    )

    def state_reviewers() -> tuple[tuple[str, Role | None], ...]:
        return tuple(
            (
                str(unit.work_unit_id),
                None if unit.reviewer is None else Role(unit.reviewer.value),
            )
            for unit in state.work_units
        )

    assert project_work_unit_reviewers(tuple(records)) == state_reviewers()

    state = state.record_review_denial(
        reviewer=Reviewer.CLAUDE,  # allowlist:provider -- reviewer projection fixture
        open_findings=("C-01",),
        return_step=WorkflowStep.CODEX_PLAN_REVISION,
        progress_made=True,
    )
    _append(
        records,
        "review-1",
        ReviewPayload(
            Role.CLAUDE,  # allowlist:provider -- reviewer projection fixture
            "1",
            "denied",
            ("C-01",),
            None,
            "native-claude-review-v2",  # allowlist:provider -- closed transport fixture
            "native-review-request-" + "f" * 64,
            "d" * 64,
        ),
    )
    assert project_work_unit_reviewers(tuple(records)) == state_reviewers()


def test_structured_finding_projection_rebuilds_reviewer_owned_history() -> None:
    records: list[ArtifactRecord] = []
    _append(records, "work-unit-1", WorkUnitPayload("1", 1, ("src/a.py",)))
    _append(
        records,
        "finding-C-01",
        FindingTransitionPayload(
            finding_id="C-01",
            reporter=Role.CLAUDE,
            actor=Role.CLAUDE,
            action="opened",
            severity=FindingSeverity.BLOCKER,
            finding_status="open",
            rationale="The retry guard is incomplete.",
            work_unit_id="1",
            summary="The retry guard is incomplete.",
            acceptance_test="A third physical start is rejected.",
            origin_slice_id="01",
            origin_round_number=1,
        ),
    )
    _append(
        records,
        "finding-C-01",
        FindingTransitionPayload(
            finding_id="C-01",
            reporter=Role.CLAUDE,
            actor=Role.CODEX,
            action="responded",
            severity=FindingSeverity.BLOCKER,
            finding_status="open",
            rationale="The bridge now rejects the third start.",
            work_unit_id="1",
            response_decision="accepted",
        ),
        revision=2,
    )
    _append(
        records,
        "finding-C-01",
        FindingTransitionPayload(
            finding_id="C-01",
            reporter=Role.CLAUDE,
            actor=Role.CLAUDE,
            action="status_changed",
            severity=FindingSeverity.BLOCKER,
            finding_status="closed",
            rationale="The bound regression passes.",
            work_unit_id="1",
        ),
        revision=3,
    )

    findings = replay_findings(replay_artifacts(records, "run-replay"), "1")

    assert len(findings) == 1
    assert findings[0].status is FindingStatus.CLOSED
    assert findings[0].acceptance_test == "A third physical start is rejected."
    assert findings[0].responses[0].decision is FindingResponseDecision.ACCEPTED


def test_sparse_response_chain_preserves_the_verbose_chain_open_finding_set() -> None:
    sparse: list[ArtifactRecord] = []
    verbose: list[ArtifactRecord] = []
    for records in (sparse, verbose):
        for number in (1, 2):
            _append(
                records,
                f"finding-C-{number:02d}",
                FindingTransitionPayload(
                    finding_id=f"C-{number:02d}",
                    reporter=Role.CLAUDE,
                    actor=Role.CLAUDE,
                    action="opened",
                    severity=FindingSeverity.OBSERVATION,
                    finding_status="open",
                    rationale="Keep this observation open.",
                    work_unit_id="1",
                    summary=f"Observation {number}.",
                    acceptance_test="A later slice may address it.",
                    origin_slice_id="1",
                    origin_round_number=1,
                ),
            )
    _append(
        verbose,
        "finding-C-01",
        FindingTransitionPayload(
            finding_id="C-01",
            reporter=Role.CLAUDE,
            actor=Role.CODEX,
            action="responded",
            severity=FindingSeverity.OBSERVATION,
            finding_status="open",
            rationale="No new answer is needed in this slice.",
            work_unit_id="1",
            response_decision="rejected",
        ),
        revision=2,
    )

    sparse_findings = replay_findings(replay_artifacts(sparse, "run-replay"))
    verbose_findings = replay_findings(replay_artifacts(verbose, "run-replay"))

    assert tuple(
        item.finding_id
        for item in sparse_findings
        if item.status is FindingStatus.OPEN
    ) == tuple(
        item.finding_id
        for item in verbose_findings
        if item.status is FindingStatus.OPEN
    ) == ("C-01", "C-02")


def test_import_replays_source_order_then_accepts_local_reviewer_transition() -> None:
    source = (
        ImportedFindingTransition(
            "ar1-" + "1" * 64,
            FindingTransitionPayload(
                "C-02", Role.CLAUDE, Role.CLAUDE, "opened",
                FindingSeverity.OBSERVATION, "open", "Second opened first.", "plan-review",
                "Second finding.", "It is preserved.", "plan", 1,
            ),
        ),
        ImportedFindingTransition(
            "ar1-" + "2" * 64,
            FindingTransitionPayload(
                "C-01", Role.CLAUDE, Role.CLAUDE, "opened",
                FindingSeverity.BLOCKER, "open", "First opened second.", "plan-review",
                "First finding.", "It is preserved too.", "plan", 1,
            ),
        ),
        ImportedFindingTransition(
            "ar1-" + "3" * 64,
            FindingTransitionPayload(
                "C-02", Role.CLAUDE, Role.CODEX, "responded",
                FindingSeverity.OBSERVATION, "open", "Handled.", "plan-review",
                response_decision="accepted",
            ),
        ),
        ImportedFindingTransition(
            "ar1-" + "4" * 64,
            FindingTransitionPayload(
                "C-02", Role.CLAUDE, Role.CLAUDE, "reclassified",
                FindingSeverity.BLOCKER, "open", "Now actionable.", "plan-review",
            ),
        ),
    )
    digest = finding_transition_sequence_sha256(source)
    records: list[ArtifactRecord] = []
    imported = _append(records, "finding-import", FindingHandoffImportPayload(
        "source-run", "ar1-" + "5" * 64, "6" * 40,
        "ar1-" + "7" * 64, "ar1-" + "8" * 64, "run-replay", "9" * 64,
        digest, source, Role.ORCHESTRATOR,
    ))
    _append(records, "work-unit-1", WorkUnitPayload(
        "1", 1, ("src/a.py",), ("C-01", "C-02"), imported.record_id,
    ))
    _append(records, "finding-C-02", FindingTransitionPayload(
        "C-02", Role.CLAUDE, Role.CLAUDE, "status_changed",
        FindingSeverity.BLOCKER, "closed", "Verified locally.", "1",
    ))

    findings = replay_findings(replay_artifacts(records, "run-replay"), finding_ids=("C-02",))
    assert findings[0].finding_class.value == "BLOCKER"
    assert findings[0].responses[0].decision is FindingResponseDecision.ACCEPTED
    assert findings[0].status is FindingStatus.CLOSED


def test_import_bound_work_unit_revisions_follow_authoritative_finding_prefix() -> None:
    opened = ImportedFindingTransition(
        "ar1-" + "1" * 64,
        FindingTransitionPayload(
            "C-01", Role.CLAUDE, Role.CLAUDE, "opened",
            FindingSeverity.OBSERVATION, "open", "Carry it.", "plan-review",
            "Imported observation.", "It remains visible.", "plan", 1,
        ),
    )
    digest = finding_transition_sequence_sha256((opened,))
    records: list[ArtifactRecord] = []
    imported = _append(records, "finding-import", FindingHandoffImportPayload(
        "source-run", "ar1-" + "2" * 64, "3" * 40,
        "ar1-" + "4" * 64, "ar1-" + "5" * 64, "run-replay", "6" * 64,
        digest, (opened,), Role.ORCHESTRATOR,
    ))
    _append(
        records,
        "work-unit-2",
        WorkUnitPayload("1", 1, ("src/a.py",), ("C-01",), imported.record_id),
    )
    _append(records, "finding-C-01", FindingTransitionPayload(
        "C-01", Role.CLAUDE, Role.CLAUDE, "status_changed",
        FindingSeverity.OBSERVATION, "closed", "Verified.", "2",
    ))
    _append(records, "finding-C-02", FindingTransitionPayload(
        "C-02", Role.CLAUDE, Role.CLAUDE, "opened",
        FindingSeverity.BLOCKER, "open", "Correction required.", "2",
        "New blocker.", "The regression test passes.", "1", 1,
    ))
    _append(
        records,
        "work-unit-2",
        WorkUnitPayload("1", 2, ("src/a.py",), ("C-02",), imported.record_id),
        revision=2,
    )

    replay = replay_artifacts(records, "run-replay")

    assert tuple(
        finding.finding_id
        for finding in replay_findings(replay)
        if finding.status is FindingStatus.OPEN
    ) == ("C-02",)


def test_finding_import_bootstrap_rejects_any_additional_record_without_identity() -> None:
    opened = ImportedFindingTransition(
        "ar1-" + "1" * 64,
        FindingTransitionPayload(
            "C-01", Role.CLAUDE, Role.CLAUDE, "opened",
            FindingSeverity.OBSERVATION, "open", "Carry it.", "plan-review",
            "Imported observation.", "It remains visible.", "plan", 1,
        ),
    )
    transitions = (opened,)
    imported = ArtifactRecord.create(
        run_id="run-replay",
        logical_id="finding-import",
        revision=1,
        fingerprint=FP,
        predecessor_ids=(),
        created_at="2026-08-21T10:00:00+00:00",
        idempotency_key="replay:finding-import",
        payload=FindingHandoffImportPayload(
            "source-run", "ar1-" + "2" * 64, "3" * 40,
            "ar1-" + "4" * 64, "ar1-" + "5" * 64, "run-replay", "6" * 64,
            finding_transition_sequence_sha256(transitions), transitions,
            Role.ORCHESTRATOR,
        ),
    )
    foreign = ArtifactRecord.create(
        run_id="run-replay",
        logical_id="work-unit-1",
        revision=1,
        fingerprint=FP,
        predecessor_ids=(imported.record_id,),
        created_at="2026-08-21T10:00:01+00:00",
        idempotency_key="replay:work-unit-1",
        payload=WorkUnitPayload("1", 1, ("src/a.py",)),
    )

    with pytest.raises(ArtifactReplayError) as caught:
        replay_artifacts(
            (imported, foreign),
            "run-replay",
            allow_finding_import_bootstrap=True,
        )

    assert caught.value.code is ReplayDiagnosticCode.RECORD_MISSING


def test_import_bound_work_unit_revision_rejects_unproven_open_finding() -> None:
    opened = ImportedFindingTransition(
        "ar1-" + "1" * 64,
        FindingTransitionPayload(
            "C-01", Role.CLAUDE, Role.CLAUDE, "opened",
            FindingSeverity.OBSERVATION, "open", "Carry it.", "plan-review",
            "Imported observation.", "It remains visible.", "plan", 1,
        ),
    )
    digest = finding_transition_sequence_sha256((opened,))
    records: list[ArtifactRecord] = []
    imported = _append(records, "finding-import", FindingHandoffImportPayload(
        "source-run", "ar1-" + "2" * 64, "3" * 40,
        "ar1-" + "4" * 64, "ar1-" + "5" * 64, "run-replay", "6" * 64,
        digest, (opened,), Role.ORCHESTRATOR,
    ))
    _append(
        records,
        "work-unit-2",
        WorkUnitPayload("1", 1, ("src/a.py",), ("C-01",), imported.record_id),
    )
    _append(
        records,
        "work-unit-2",
        WorkUnitPayload("1", 2, ("src/a.py",), ("C-02",), imported.record_id),
        revision=2,
    )

    _assert_code(tuple(records), ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH)


def test_import_tampering_and_partial_work_unit_binding_fail_with_stable_codes() -> None:
    opened = ImportedFindingTransition(
        "ar1-" + "1" * 64,
        FindingTransitionPayload(
            "C-01", Role.CLAUDE, Role.CLAUDE, "opened", FindingSeverity.OBSERVATION,
            "open", "Carry.", "plan-review", "Carry.", "Still open.", "plan", 1,
        ),
    )
    digest = finding_transition_sequence_sha256((opened,))
    records: list[ArtifactRecord] = []
    imported = _append(records, "finding-import", FindingHandoffImportPayload(
        "source-run", "ar1-" + "2" * 64, "3" * 40,
        "ar1-" + "4" * 64, "ar1-" + "5" * 64, "run-replay", "6" * 64,
        digest, (opened,), Role.ORCHESTRATOR,
    ))
    _append(records, "work-unit-1", WorkUnitPayload(
        "1", 1, ("src/a.py",), (), imported.record_id,
    ))
    _assert_code(tuple(records), ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH)

    wrong_target = replace(imported.payload, target_run_id="other-run")
    object.__setattr__(imported, "payload", wrong_target)
    _assert_code(tuple(records), ReplayDiagnosticCode.RECORD_RUN_MISMATCH)


def test_structured_finding_projection_rejects_legacy_incomplete_opening() -> None:
    records: list[ArtifactRecord] = []
    _append(records, "work-unit-1", WorkUnitPayload("1", 1, ("src/a.py",)))
    _append(
        records,
        "finding-C-01",
        FindingTransitionPayload(
            "C-01",
            Role.CLAUDE,
            Role.CLAUDE,
            "opened",
            FindingSeverity.BLOCKER,
            "open",
            "Legacy finding without structured snapshot.",
            work_unit_id="1",
        ),
    )

    replay = replay_artifacts(records, "run-replay")
    with pytest.raises(ArtifactReplayError) as caught:
        replay_findings(replay, "1")
    assert caught.value.code is ReplayDiagnosticCode.RECORD_TYPE_MISMATCH


def test_structured_finding_projection_carries_findings_across_work_units() -> None:
    records: list[ArtifactRecord] = []
    _append(records, "work-unit-1", WorkUnitPayload("1", 1, ("src/a.py",)))
    _append(
        records,
        "finding-C-01",
        FindingTransitionPayload(
            finding_id="C-01",
            reporter=Role.CLAUDE,
            actor=Role.CLAUDE,
            action="opened",
            severity=FindingSeverity.BLOCKER,
            finding_status="open",
            rationale="The first review opened the finding.",
            work_unit_id="1",
            summary="The first review opened the finding.",
            acceptance_test="The correction response is carried forward.",
            origin_slice_id="01",
            origin_round_number=1,
        ),
    )
    _append(records, "work-unit-2", WorkUnitPayload("1", 2, ("src/a.py",)))
    _append(
        records,
        "finding-C-01",
        FindingTransitionPayload(
            finding_id="C-01",
            reporter=Role.CLAUDE,
            actor=Role.CODEX,
            action="responded",
            severity=FindingSeverity.BLOCKER,
            finding_status="open",
            rationale="The later work unit implements the correction.",
            work_unit_id="2",
            response_decision="accepted",
        ),
        revision=2,
    )

    replay = replay_artifacts(records, "run-replay")

    assert replay_findings(replay, "1")[0].responses == ()
    with pytest.raises(ArtifactReplayError) as caught:
        replay_findings(replay, "2")
    assert caught.value.code is ReplayDiagnosticCode.RECORD_REFERENCE_MISSING
    assert len(replay_findings(replay, finding_ids=("C-01",))[0].responses) == 1
    assert len(replay_findings(replay)[0].responses) == 1


def test_replay_ignores_created_at_for_semantic_digest() -> None:
    chain = _chain()
    retimed = tuple(
        replace(record, created_at=f"2027-01-01T00:00:{index:02d}+00:00")
        for index, record in enumerate(chain)
    )

    assert replay_artifacts(chain, "run-replay").semantic_digest == replay_artifacts(
        retimed, "run-replay"
    ).semantic_digest


def test_replay_reports_missing_duplicate_unknown_and_run_mismatch() -> None:
    _assert_code((), ReplayDiagnosticCode.RECORD_MISSING)
    chain = _chain()
    _assert_code((chain[0], chain[0]), ReplayDiagnosticCode.RECORD_DUPLICATE)
    with pytest.raises(ArtifactReplayError) as unknown:
        replay_artifacts((object(),), "run-replay")  # type: ignore[arg-type]
    assert unknown.value.code is ReplayDiagnosticCode.RECORD_UNKNOWN
    with pytest.raises(ArtifactReplayError) as wrong_run:
        replay_artifacts(chain, "another-run")
    assert wrong_run.value.code is ReplayDiagnosticCode.RECORD_RUN_MISMATCH


def test_replay_rejects_two_immutable_task_contracts_as_duplicate() -> None:
    records: list[ArtifactRecord] = []
    _append(records, "task-a", TaskPayload("feature/a", ("src/a.py",), "a" * 64))
    _append(records, "task-b", TaskPayload("feature/b", ("src/b.py",), "b" * 64))

    _assert_code(tuple(records), ReplayDiagnosticCode.RECORD_DUPLICATE)


def test_replay_reports_missing_reference_and_fingerprint_mismatch() -> None:
    records: list[ArtifactRecord] = []
    _append(records, "work-unit-1", WorkUnitPayload("1", 1, ("src/a.py",)))
    _append(
        records,
        "agent-orphan",
        _agent_result("404"),
    )
    _assert_code(tuple(records), ReplayDiagnosticCode.RECORD_REFERENCE_MISSING)

    chain = list(_chain())
    mismatched = replace(
        chain[-1],
        fingerprint=Fingerprint(FingerprintKind.IMPLEMENTATION, "f" * 64),
    )
    _assert_code((*chain[:-1], mismatched), ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH)


def test_replay_allows_plan_activity_before_the_first_work_unit_record() -> None:
    records: list[ArtifactRecord] = []
    _append(
        records,
        "agent-plan",
        _agent_result("1"),
        fingerprint=Fingerprint(FingerprintKind.IMPLEMENTATION, "c" * 64),
    )

    assert replay_artifacts(records, "run-replay").records == tuple(records)


def test_replay_rejects_activity_that_references_a_later_work_unit() -> None:
    records: list[ArtifactRecord] = []
    _append(records, "work-unit-1", WorkUnitPayload("1", 1, ("src/a.py",)))
    _append(
        records,
        "agent-future-work-unit",
        _agent_result("2"),
    )
    _append(records, "work-unit-2", WorkUnitPayload("2", 2, ("src/b.py",)))

    _assert_code(tuple(records), ReplayDiagnosticCode.RECORD_REFERENCE_MISSING)


def test_replay_accepts_one_provider_attempt_and_rejects_terminal_without_start() -> None:
    records: list[ArtifactRecord] = []
    _append(records, "work-unit-1", WorkUnitPayload("1", 1, ("src/a.py",)))
    measurement = _append(
        records,
        "measurement-1",
        ProviderInputMeasurementPayload(
            Role.CLAUDE, Role.CLAUDE, "claude_slice_review", "1", "a" * 64,
            "b" * 64, "c" * 64, "d" * 64,
            (ProviderInputComponentPayload("prompt", 3, 3),),
            3, 3, 10, 10, None, None, None, 10, 10, True, (), 0, 0, "prompt",
        ),
    )
    started_payload = ProviderAttemptPayload(
        Role.CLAUDE, Role.CLAUDE, "claude_slice_review", "1",
        "provider-operation-01", "a" * 64, measurement.record_id, "c" * 64, 1,
        "started", "2026-08-21T10:00:01+00:00", None, None, None, None,
    )
    _append(records, "attempt-1", started_payload)
    _append(
        records, "attempt-1",
        replace(
            started_payload, phase="succeeded",
            ended_at="2026-08-21T10:00:02+00:00", duration_seconds=1.0,
            usage=ProviderUsagePayload(output_tokens=4),
        ),
        revision=2,
    )
    assert replay_artifacts(records, "run-replay").records == tuple(records)

    terminal_only = [records[0], records[1], records[2], records[3], records[5]]
    terminal_only[4] = replace(
        terminal_only[4], revision=1,
        record_id=records[4].record_id,
        predecessor_ids=(records[3].record_id,),
    )
    _assert_code(tuple(terminal_only), ReplayDiagnosticCode.RECORD_REFERENCE_MISSING)


def test_replay_uses_first_work_unit_revision_as_reference_boundary() -> None:
    records: list[ArtifactRecord] = []
    _append(records, "work-unit-1", WorkUnitPayload("1", 1, ("src/a.py",)))
    _append(
        records,
        "agent-orphan-between-revisions",
        _agent_result("404"),
    )
    _append(
        records,
        "work-unit-1",
        WorkUnitPayload("1", 2, ("src/a.py",)),
        revision=2,
    )

    _assert_code(tuple(records), ReplayDiagnosticCode.RECORD_REFERENCE_MISSING)


def test_work_unit_revisions_extend_scope_only_within_the_same_round() -> None:
    records: list[ArtifactRecord] = []
    _append(records, "work-unit-1", WorkUnitPayload("1", 1, ("src/a.py",)))
    _append(
        records,
        "work-unit-1",
        WorkUnitPayload("1", 1, ("src/a.py", "tests/a.py")),
        revision=2,
    )
    assert replay_artifacts(tuple(records), "run-replay").records == tuple(records)

    shrunk = list(records[:3])
    _append(
        shrunk,
        "work-unit-1",
        WorkUnitPayload("1", 1, ("tests/a.py",)),
        revision=2,
    )
    _assert_code(tuple(shrunk), ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH)

    moved_round = list(records[:3])
    _append(
        moved_round,
        "work-unit-1",
        WorkUnitPayload("1", 2, ("src/a.py", "tests/a.py")),
        revision=2,
    )
    _assert_code(tuple(moved_round), ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH)


def test_subset_accepts_equal_redeserialized_records_but_rejects_same_id_tampering() -> None:
    replay = replay_artifacts(_chain(), "run-replay")
    reconstructed = ArtifactRecord.from_dict(replay.records[1].to_dict())

    assert reconstructed is not replay.records[1]
    assert replay.subset((reconstructed,)).records == (reconstructed,)

    tampered = ArtifactRecord.from_dict(reconstructed.to_dict())
    object.__setattr__(tampered, "logical_id", "tampered")
    with pytest.raises(ArtifactReplayError) as caught:
        replay.subset((tampered,))
    assert caught.value.code is ReplayDiagnosticCode.RECORD_UNKNOWN


def test_replay_reports_type_mismatch_with_stable_code() -> None:
    record = _chain()[0]
    # Persisted bytes cannot create this state through ArtifactRecord.__init__;
    # corrupting the already-created test object exercises the reducer's
    # independent typed boundary.
    object.__setattr__(record, "record_type", RecordType.PLAN)

    _assert_code((record,), ReplayDiagnosticCode.RECORD_TYPE_MISMATCH)


def test_gate_prefix_replays_transition_test_scope_and_decision_binding() -> None:
    records: list[ArtifactRecord] = []
    _append(
        records,
        "workflow-transition",
        WorkflowTransitionPayload("1", "in_progress", "1", "codex_plan", "in_progress"),
    )
    transition = GateTransitionPayload(
        work_unit_id="1",
        gate_status="clear",
        reason="none",
        detail=None,
        fingerprint=None,
        paths=(),
        resume_step=None,
        active_test_fingerprint="b" * 64,
        active_test_paths=("tests/test_gate.py",),
    )
    _append(records, "gate-transition-1", transition)
    gate = _append(
        records,
        "gate-test-change",
        GatePayload("test-change", "approved", Role.USER, "reviewed exact test delta"),
        fingerprint=Fingerprint(FingerprintKind.IMPLEMENTATION, "b" * 64),
    )
    decision = GateDecisionPayload(
        work_unit_id="1",
        gate_record_id=gate.record_id,
        paths=("tests/test_gate.py",),
        resume_step="claude_slice_review",
    )
    binding = _append(
        records,
        "gate-decision-1",
        decision,
        fingerprint=Fingerprint(FingerprintKind.IMPLEMENTATION, "b" * 64),
    )

    replay = replay_artifacts(records, "run-replay")

    assert replay.gate_transitions == (transition,)
    assert len(replay.gate_decisions) == 1
    projected = replay.gate_decisions[0]
    assert projected.work_unit_id == "1"
    assert projected.paths == ("tests/test_gate.py",)
    assert projected.resume_step == "claude_slice_review"
    assert projected.authority is Role.USER
    assert projected.gate_created_at == gate.created_at
    assert projected.gate_record_id == gate.record_id
    assert projected.decision_record_id == binding.record_id


def test_gate_replay_rejects_partial_test_binding_and_missing_decision_reference() -> None:
    records: list[ArtifactRecord] = []
    _append(
        records,
        "workflow-transition",
        WorkflowTransitionPayload("1", "in_progress", "1", "codex_plan", "in_progress"),
    )
    partial = GateTransitionPayload(
        "1", "clear", "none", None, None, (), None, None, ()
    )
    partial_record = _append(records, "gate-transition-1", partial)
    object.__setattr__(
        partial_record.payload, "active_test_paths", ("tests/test_gate.py",)
    )
    _assert_code(tuple(records), ReplayDiagnosticCode.RECORD_FINGERPRINT_MISMATCH)

    records = records[:3]
    _append(
        records,
        "gate-decision-1",
        GateDecisionPayload("1", "ar1-" + "d" * 64, (), "codex_plan"),
        fingerprint=Fingerprint(FingerprintKind.IMPLEMENTATION, "c" * 64),
    )
    _assert_code(tuple(records), ReplayDiagnosticCode.RECORD_REFERENCE_MISSING)
