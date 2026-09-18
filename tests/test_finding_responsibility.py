from __future__ import annotations

from dataclasses import FrozenInstanceError, fields

import pytest

from artifact_models import (
    ArtifactRecord,
    ArtifactValidationError,
    Fingerprint,
    FingerprintKind,
    FindingSeverity,
    FindingTransitionPayload,
    PlanPayload,
    Role,
    RoleProfilePayload,
    RunIdentityPayload,
    RunProfilePayload,
    SliceSpec,
    TaskPayload,
    WorkUnitPayload,
    WorkflowTransitionPayload,
)
from artifact_replay import ArtifactReplayError
from finding_reducer import (
    reduce_finding_records,
    responsibility_projection_document,
)
from finding_responsibility import (
    BranchPlanningResponsibility,
    PlanRevisionResponsibility,
    SliceResponsibility,
    parse_responsibility,
    responsibility_document,
)


RUN_ID = "responsibility-run"
FINGERPRINT = Fingerprint(FingerprintKind.IMPLEMENTATION, "a" * 64)
PLAN_COMMIT = "b" * 40


@pytest.mark.parametrize(
    "responsibility",
    (
        SliceResponsibility(RUN_ID, PLAN_COMMIT, "1"),
        BranchPlanningResponsibility("family-1", 2),
        PlanRevisionResponsibility(
            RUN_ID, "docs/internal/plan.md", "c" * 64, 3
        ),
    ),
)
def test_responsibility_kinds_roundtrip_losslessly_and_are_immutable(
    responsibility,
) -> None:
    document = responsibility_document(responsibility)

    assert parse_responsibility(document) == responsibility
    assert responsibility_document(parse_responsibility(document)) == document
    with pytest.raises(FrozenInstanceError):
        setattr(responsibility, fields(responsibility)[0].name, "changed")


@pytest.mark.parametrize(
    ("document", "missing_field"),
    (
        (
            {
                "responsibility_kind": "SLICE",
                "approved_plan_commit": PLAN_COMMIT,
                "slice_id": "1",
            },
            "target_run_id",
        ),
        (
            {
                "responsibility_kind": "BRANCH_PLANNING",
                "family_id": "family-1",
            },
            "cycle_number",
        ),
        (
            {
                "responsibility_kind": "PLAN_REVISION",
                "run_id": RUN_ID,
                "plan_path": "docs/internal/plan.md",
                "plan_digest": "c" * 64,
            },
            "revision",
        ),
    ),
)
def test_each_kind_rejects_a_missing_required_field(
    document: dict[str, object], missing_field: str
) -> None:
    with pytest.raises(ValueError, match=missing_field):
        parse_responsibility(document)


def test_responsibility_parser_rejects_foreign_and_unknown_kinds() -> None:
    with pytest.raises(ValueError, match="foreign field family_id"):
        parse_responsibility(
            {
                "responsibility_kind": "SLICE",
                "target_run_id": RUN_ID,
                "approved_plan_commit": PLAN_COMMIT,
                "slice_id": "1",
                "family_id": "family-1",
            }
        )
    with pytest.raises(ValueError, match="unknown"):
        parse_responsibility({"responsibility_kind": "SOMETHING_ELSE"})


@pytest.mark.parametrize(
    ("document", "field"),
    (
        ({"responsibility_kind": "SLICE", "target_run_id": "", "approved_plan_commit": PLAN_COMMIT, "slice_id": "1"}, "target_run_id"),
        ({"responsibility_kind": "SLICE", "target_run_id": RUN_ID, "approved_plan_commit": "", "slice_id": "1"}, "approved_plan_commit"),
        ({"responsibility_kind": "SLICE", "target_run_id": RUN_ID, "approved_plan_commit": PLAN_COMMIT, "slice_id": ""}, "slice_id"),
        ({"responsibility_kind": "BRANCH_PLANNING", "family_id": "", "cycle_number": 1}, "family_id"),
        ({"responsibility_kind": "PLAN_REVISION", "run_id": "", "plan_path": "docs/internal/plan.md", "plan_digest": "c" * 64, "revision": 1}, "run_id"),
        ({"responsibility_kind": "PLAN_REVISION", "run_id": RUN_ID, "plan_path": "", "plan_digest": "c" * 64, "revision": 1}, "plan_path"),
        ({"responsibility_kind": "PLAN_REVISION", "run_id": RUN_ID, "plan_path": "docs/internal/plan.md", "plan_digest": "", "revision": 1}, "plan_digest"),
    ),
)
def test_required_string_fields_reject_empty_values(
    document: dict[str, object], field: str
) -> None:
    with pytest.raises(ValueError, match=field):
        parse_responsibility(document)


@pytest.mark.parametrize(
    "document,field",
    (
        (
            {
                "responsibility_kind": "BRANCH_PLANNING",
                "family_id": "family-1",
                "cycle_number": 0,
            },
            "cycle_number",
        ),
        (
            {
                "responsibility_kind": "PLAN_REVISION",
                "run_id": RUN_ID,
                "plan_path": "docs/internal/plan.md",
                "plan_digest": "c" * 64,
                "revision": 0,
            },
            "revision",
        ),
    ),
)
def test_one_based_responsibility_fields_reject_zero(
    document: dict[str, object], field: str
) -> None:
    with pytest.raises(ValueError, match=field):
        parse_responsibility(document)


def test_finding_transition_wire_roundtrip_preserves_typed_responsibility() -> None:
    payload = _opening(SliceResponsibility(RUN_ID, PLAN_COMMIT, "1"))
    record = _record(1, "finding-C-01", payload)

    restored = ArtifactRecord.from_dict(record.to_dict())

    assert restored == record
    assert restored.payload.responsibility == payload.responsibility  # type: ignore[attr-defined]


def test_legacy_opening_wire_shape_omits_optional_responsibility() -> None:
    payload = FindingTransitionPayload(
        finding_id="C-01",
        reporter=Role.CLAUDE,
        actor=Role.CLAUDE,
        action="opened",
        severity=FindingSeverity.OBSERVATION,
        finding_status="open",
        rationale="legacy opening",
    )

    assert "responsibility" not in _record(1, "finding-C-01", payload).to_dict()[
        "payload"
    ]


def test_responsibility_bearing_opening_requires_complete_context_metadata() -> None:
    with pytest.raises(ArtifactValidationError, match="context metadata"):
        FindingTransitionPayload(
            finding_id="C-01",
            reporter=Role.CLAUDE,
            actor=Role.CLAUDE,
            action="opened",
            severity=FindingSeverity.OBSERVATION,
            finding_status="open",
            rationale="opening",
            responsibility=SliceResponsibility(RUN_ID, PLAN_COMMIT, "1"),
        )


def test_opening_rejects_responsibility_kind_that_does_not_match_review_context() -> None:
    records = _slice_review_prefix()
    records.append(
        _record(
            len(records) + 1,
            "finding-C-01",
            _opening(
                PlanRevisionResponsibility(
                    RUN_ID, "docs/internal/plan.md", "c" * 64, 1
                )
            ),
            records,
        )
    )

    with pytest.raises(
        ArtifactReplayError,
        match="Slicereview opening requires responsibility kind SLICE",
    ):
        reduce_finding_records(records)


def test_plan_review_opening_accepts_complete_plan_revision_identity() -> None:
    records = _plan_review_prefix()
    responsibility = PlanRevisionResponsibility(
        RUN_ID, "docs/internal/plan.md", "c" * 64, 1
    )
    records.append(
        _record(
            len(records) + 1,
            "finding-C-01",
            _opening(responsibility, work_unit_id="1", origin_slice_id="plan"),
            records,
        )
    )

    reduction = reduce_finding_records(records)

    assert reduction.responsibilities[0].responsibility == responsibility


def test_codex_cannot_route_and_routing_requires_a_rationale() -> None:
    target = SliceResponsibility(RUN_ID, PLAN_COMMIT, "2")
    with pytest.raises(ArtifactValidationError, match="actor must be claude; got codex"):
        _routing(target, actor=Role.CODEX)
    with pytest.raises(ArtifactValidationError, match="rationale"):
        _routing(target, rationale="")
    with pytest.raises(ArtifactValidationError, match="requires a new responsibility"):
        FindingTransitionPayload(
            finding_id="C-01",
            reporter=Role.CLAUDE,
            actor=Role.CLAUDE,
            action="routed",
            severity=FindingSeverity.OBSERVATION,
            finding_status="open",
            rationale="route without a target",
            work_unit_id="2",
        )


def test_routing_changes_current_responsibility_without_changing_origin() -> None:
    records = _slice_review_prefix()
    records.append(
        _record(
            len(records) + 1,
            "finding-C-01",
            _opening(SliceResponsibility(RUN_ID, PLAN_COMMIT, "1")),
            records,
        )
    )
    records.append(
        _record(
            len(records) + 1,
            "finding-C-01",
            _routing(SliceResponsibility("follow-up-run", "d" * 40, "2")),
            records,
            revision=2,
        )
    )

    reduction = reduce_finding_records(records)
    lineage = reduction.ledger.lineages[0]

    assert lineage.finding.origin.slice_id == "1"
    assert lineage.responsibility == SliceResponsibility(
        "follow-up-run", "d" * 40, "2"
    )
    assert responsibility_projection_document(reduction) == {
        "C-01": {
            "responsibility_kind": "SLICE",
            "target_run_id": "follow-up-run",
            "approved_plan_commit": "d" * 40,
            "slice_id": "2",
        }
    }


def test_branch_planning_is_fail_closed_until_point_67_binds_run_family() -> None:
    records = _slice_review_prefix()
    records.append(
        _record(
            len(records) + 1,
            "finding-C-01",
            _opening(SliceResponsibility(RUN_ID, PLAN_COMMIT, "1")),
            records,
        )
    )
    records.append(
        _record(
            len(records) + 1,
            "finding-C-01",
            _routing(BranchPlanningResponsibility("family-1", 1)),
            records,
            revision=2,
        )
    )

    with pytest.raises(
        ArtifactReplayError,
        match="no run-bound family_id and cycle_number before point 67",
    ):
        reduce_finding_records(records)


def _slice_review_prefix() -> list[ArtifactRecord]:
    records: list[ArtifactRecord] = []
    payloads = (
        ("run-identity", RunIdentityPayload(
            "inbox/task.md", "feature/responsibility", "e" * 40, "e" * 40,
            "IMPLEMENT", None,
        )),
        ("run-profile", RunProfilePayload(
            RoleProfilePayload("implementer", "medium"),
            RoleProfilePayload("reviewer", "high"),
        )),
        ("task", TaskPayload(
            "feature/responsibility", ("src/a.py",), "f" * 64,
            "docs/internal/plan.md",
        )),
        ("plan", PlanPayload(
            "docs/internal/plan.md", PLAN_COMMIT,
            (SliceSpec("1", "First Slice", ("src/a.py",)),),
        )),
        ("workflow-transition", WorkflowTransitionPayload(
            "1", "in_progress", "2", "claude_slice_review", "in_progress",
        )),
        ("work-unit-2", WorkUnitPayload("1", 1, ("src/a.py",))),
    )
    for logical_id, payload in payloads:
        records.append(
            _record(len(records) + 1, logical_id, payload, records)
        )
    return records


def _plan_review_prefix() -> list[ArtifactRecord]:
    records: list[ArtifactRecord] = []
    payloads = (
        ("run-identity", RunIdentityPayload(
            "inbox/task.md", "feature/responsibility", "e" * 40, "e" * 40,
            "PLAN_ONLY", None,
        )),
        ("run-profile", RunProfilePayload(
            RoleProfilePayload("implementer", "medium"),
            RoleProfilePayload("reviewer", "high"),
        )),
        ("task", TaskPayload(
            "feature/responsibility", ("docs/internal/plan.md",), "f" * 64,
            "docs/internal/plan.md",
        )),
        ("workflow-transition", WorkflowTransitionPayload(
            "1", "in_progress", "1", "claude_plan_review", "in_progress",
        )),
    )
    for logical_id, payload in payloads:
        records.append(
            _record(len(records) + 1, logical_id, payload, records)
        )
    return records


def _opening(
    responsibility,
    *,
    work_unit_id: str = "2",
    origin_slice_id: str = "1",
) -> FindingTransitionPayload:
    return FindingTransitionPayload(
        finding_id="C-01",
        reporter=Role.CLAUDE,
        actor=Role.CLAUDE,
        action="opened",
        severity=FindingSeverity.OBSERVATION,
        finding_status="open",
        rationale="review found a defect",
        work_unit_id=work_unit_id,
        summary="defect summary",
        acceptance_test="the defect no longer reproduces",
        origin_slice_id=origin_slice_id,
        origin_round_number=1,
        responsibility=responsibility,
    )


def _routing(
    responsibility,
    *,
    actor: Role = Role.CLAUDE,
    rationale: str = "route to the bound follow-up",
) -> FindingTransitionPayload:
    return FindingTransitionPayload(
        finding_id="C-01",
        reporter=Role.CLAUDE,
        actor=actor,
        action="routed",
        severity=FindingSeverity.OBSERVATION,
        finding_status="open",
        rationale=rationale,
        work_unit_id="2",
        responsibility=responsibility,
    )


def _record(
    sequence: int,
    logical_id: str,
    payload,
    records: list[ArtifactRecord] | None = None,
    *,
    revision: int = 1,
) -> ArtifactRecord:
    return ArtifactRecord.create(
        run_id=RUN_ID,
        logical_id=logical_id,
        revision=revision,
        fingerprint=FINGERPRINT,
        predecessor_ids=(
            () if not records else (records[-1].record_id,)
        ),
        created_at=f"2026-09-18T10:{sequence:02d}:00+00:00",
        idempotency_key=f"responsibility:{sequence}",
        payload=payload,
    )
