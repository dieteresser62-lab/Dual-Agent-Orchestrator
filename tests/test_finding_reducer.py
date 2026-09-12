from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from artifact_models import (
    ArtifactRecord,
    CorrectionWorkUnitPayload,
    Fingerprint,
    FingerprintKind,
    FindingHandoffImportPayload,
    FindingSeverity,
    FindingTransitionPayload,
    ImportedFindingTransition,
    PlanPayload,
    ReviewPayload,
    Role,
    RoleProfilePayload,
    RunIdentityPayload,
    RunProfilePayload,
    SliceSpec,
    WorkUnitPayload,
    finding_transition_sequence_sha256,
)
from artifact_replay import (
    ArtifactReplayError,
    ArtifactReplayResult,
    replay_artifacts as replay_artifacts_checked,
)
from finding_reducer import (
    FindingReduction,
    project_final_review_dispositions,
    reduce_findings,
)


ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = ROOT / "tests" / "fixtures" / "finding-reducer-corpus.json"
RUN_ID = "finding-reducer-corpus"
FINGERPRINT = Fingerprint(FingerprintKind.IMPLEMENTATION, "a" * 64)
HISTORICAL_COMMITS = {
    "745a2fa",
    "924a554",
    "700b8c7",
    "f09ed8b",
    "47b4ddc",
    "24abbfd",
    "047934f",
    "5a654d3",
    "ade231f",
    "286f5b2",
    "17c39c2",
    "d24511e",
}


def replay_artifacts(
    records: tuple[ArtifactRecord, ...] | list[ArtifactRecord],
    run_id: str,
) -> ArtifactReplayResult:
    """Replay reducer-only fixtures without recreating later R7/R8 records."""
    return replay_artifacts_checked(
        records,
        run_id,
        require_content_authority=False,
        require_review_authority=False,
    )


def _corpus() -> tuple[dict[str, Any], ...]:
    document = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    assert document["schema"] == "finding-reducer-regression-corpus-v1"
    cases = tuple(document["cases"])
    assert {case["commit"] for case in cases} == HISTORICAL_COMMITS
    assert len(cases) == 12
    return cases


def _append(
    records: list[ArtifactRecord],
    revisions: dict[tuple[str, str], int],
    logical_id: str,
    payload: object,
) -> ArtifactRecord:
    key = (payload.record_type.value, logical_id)  # type: ignore[attr-defined]
    revision = revisions.get(key, 0) + 1
    revisions[key] = revision
    record = ArtifactRecord.create(
        run_id=RUN_ID,
        logical_id=logical_id,
        revision=revision,
        fingerprint=FINGERPRINT,
        predecessor_ids=((records[-1].record_id,) if records else ()),
        created_at=f"2026-08-30T10:{len(records):02d}:00+00:00",
        idempotency_key=(
            f"corpus:{payload.record_type.value}:{logical_id}:{revision}"  # type: ignore[attr-defined]
        ),
        payload=payload,  # type: ignore[arg-type]
    )
    records.append(record)
    return record


def _opening_payload(event: dict[str, Any]) -> FindingTransitionPayload:
    finding_id = event["finding_id"]
    work_unit = event["work_unit"]
    finding_class = event.get("class", "BLOCKER")
    summary = event.get("summary", f"Regression {finding_id}")
    return FindingTransitionPayload(
        finding_id=finding_id,
        reporter=Role.CLAUDE,
        actor=Role.CLAUDE,
        action="opened",
        severity=FindingSeverity(finding_class),
        finding_status="open",
        rationale=summary,
        work_unit_id=work_unit,
        summary=summary,
        acceptance_test=f"Acceptance for {finding_id}",
        origin_slice_id="01",
        origin_round_number=1,
    )


def _build_case(case: dict[str, Any]) -> tuple[ArtifactRecord, ...]:
    records: list[ArtifactRecord] = []
    revisions: dict[tuple[str, str], int] = {}
    _append(
        records,
        revisions,
        "run-identity",
        RunIdentityPayload(
            "inbox/backlog/finding-reducer.md",
            "feature/finding-reducer",
            "b" * 40,
            "b" * 40,
            "IMPLEMENT",
            None,
        ),
    )
    _append(
        records,
        revisions,
        "run-profile",
        RunProfilePayload(
            RoleProfilePayload("implementer-model", "medium"),
            RoleProfilePayload("reviewer-model", "high"),
        ),
    )
    classes: dict[str, FindingSeverity] = {}
    imported: ArtifactRecord | None = None
    for index, event in enumerate(case["events"], start=1):
        operation = event["op"]
        finding_id = event.get("finding_id")
        if operation == "plan":
            _append(
                records,
                revisions,
                "approved-plan",
                PlanPayload(
                    "docs/internal/work-plan.md",
                    "c" * 40,
                    (SliceSpec("1", "Implement the reviewed plan.", ("src/finding.py",)),),
                ),
            )
        elif operation == "import_open":
            opening = _opening_payload(event)
            source = ImportedFindingTransition(
                "ar1-" + f"{index:064x}", opening
            )
            transitions = (source,)
            payload = FindingHandoffImportPayload(
                source_run_id="source-run",
                source_head_record_id="ar1-" + "b" * 64,
                approved_plan_commit="c" * 40,
                approval_review_record_id="ar1-" + "d" * 64,
                export_record_id="ar1-" + "e" * 64,
                target_run_id=RUN_ID,
                target_task_sha256="f" * 64,
                finding_transitions_sha256=(
                    finding_transition_sequence_sha256(transitions)
                ),
                transitions=transitions,
                authority=Role.ORCHESTRATOR,
            )
            imported = _append(
                records, revisions, "finding-handoff-import", payload
            )
            classes[finding_id] = opening.severity
        elif operation == "work":
            import_id = (
                imported.record_id
                if event.get("bind_import") and imported is not None
                else None
            )
            _append(
                records,
                revisions,
                f"work-unit-{event['work_unit']}",
                WorkUnitPayload(
                    "1",
                    event.get("round", 1),
                    ("src/finding.py",),
                    tuple(event.get("open_ids", ())),
                    import_id,
                ),
            )
        elif operation == "correction":
            _append(
                records,
                revisions,
                f"work-unit-{event['work_unit']}",
                CorrectionWorkUnitPayload(
                    "2",
                    event.get("round", 1),
                    ("src/finding.py",),
                    tuple(event["finding_ids"]),
                ),
            )
        elif operation == "review":
            round_number = event.get("round", 1)
            _append(
                records,
                revisions,
                f"review-claude-{event['work_unit']}-{round_number}",
                ReviewPayload(
                    reviewer=Role.CLAUDE,
                    work_unit_id=event["work_unit"],
                    verdict=event["verdict"],
                    finding_ids=tuple(event.get("finding_ids", ())),
                    evidence="review evidence | residual risk | break condition",
                    transport_schema="native-claude-review-v2",
                    request_id="native-review-request-" + f"{index:064x}",
                    response_sha256=f"{index + 100:064x}",
                ),
            )
        elif operation == "open":
            payload = _opening_payload(event)
            classes[finding_id] = payload.severity
            _append(records, revisions, f"finding-{finding_id}", payload)
        elif operation == "respond":
            _append(
                records,
                revisions,
                f"finding-{finding_id}",
                FindingTransitionPayload(
                    finding_id=finding_id,
                    reporter=Role.CLAUDE,
                    actor=Role.CODEX,
                    action="responded",
                    severity=classes[finding_id],
                    finding_status="open",
                    rationale=f"Response for {finding_id}",
                    work_unit_id=event["work_unit"],
                    response_decision="accepted",
                ),
            )
        elif operation == "close":
            severity = FindingSeverity(event.get("class", classes[finding_id].value))
            _append(
                records,
                revisions,
                f"finding-{finding_id}",
                FindingTransitionPayload(
                    finding_id=finding_id,
                    reporter=Role.CLAUDE,
                    actor=Role.CLAUDE,
                    action="status_changed",
                    severity=severity,
                    finding_status="closed",
                    rationale=f"Closed {finding_id}",
                    work_unit_id=event["work_unit"],
                ),
            )
        else:  # pragma: no cover - the schema inventory below closes this set
            raise AssertionError(f"unknown corpus operation {operation}")
    return tuple(records)


def _finding_states(findings: tuple[object, ...]) -> list[str]:
    return [f"{item.finding_id}:{item.status.value}" for item in findings]


@pytest.mark.parametrize("case", _corpus(), ids=lambda case: case["commit"])
def test_historical_finding_regression_corpus(case: dict[str, Any]) -> None:
    records = _build_case(case)
    replay = replay_artifacts(records, RUN_ID)
    if "error" in case["expected"]:
        with pytest.raises(ArtifactReplayError, match=case["expected"]["error"]):
            reduce_findings(replay)
        return
    reduction = reduce_findings(replay)
    expected = case["expected"]
    assert _finding_states(reduction.ledger.findings) == expected["ledger"]
    if "lineages" in expected:
        assert [
            f"{item.work_unit_id}:{item.finding.finding_id}:"
            f"{item.finding.status.value}"
            for item in reduction.ledger.lineages
        ] == expected["lineages"]
    assert list(reduction.open_set.finding_ids) == expected["open"]
    assert {
        item.work_unit_id: list(item.finding_ids)
        for item in reduction.correction_attribution
    } == expected["correction"]
    assert (
        []
        if reduction.import_snapshot is None
        else list(reduction.import_snapshot.open_finding_ids)
    ) == expected["import_open"]
    request = case["request"]
    projection = reduction.request_subset(
        work_unit_id=request.get("work_unit"),
        finding_ids=request.get("finding_ids"),
        open_only=request.get("open_only", False),
    )
    rendered_request = (
        _finding_states(projection.findings)
        if any(":" in item for item in expected["request"])
        else list(projection.finding_ids)
    )
    assert rendered_request == expected["request"]
    for work_unit_id, expected_states in expected.get(
        "request_by_work_unit", {}
    ).items():
        assert _finding_states(
            reduction.request_subset(work_unit_id=work_unit_id).findings
        ) == expected_states
    assert [
        f"{item.payload.finding_id}:{item.payload.action}"
        for item in reduction.status_transitions.transitions
    ] == expected["status_actions"]
    assert [item.finding_id for item in reduction.diagnostics] == expected.get(
        "diagnostics", []
    )


def test_six_named_projections_are_independent_and_immutable() -> None:
    case = next(item for item in _corpus() if item["commit"] == "924a554")
    reduction = reduce_findings(replay_artifacts(_build_case(case), RUN_ID))
    assert reduction.ledger.transitions
    assert reduction.open_set.finding_ids == ("C-02",)
    assert reduction.correction_attribution == ()
    assert reduction.import_snapshot is not None
    assert reduction.request_subset(work_unit_id="2").finding_ids == (
        "C-01",
        "C-02",
    )
    assert tuple(
        item.payload.action for item in reduction.status_transitions.transitions
    ) == ("opened", "status_changed", "opened")
    with pytest.raises((AttributeError, TypeError)):
        reduction.open_set.findings += ()  # type: ignore[misc]


def test_ledger_correction_subset_and_errors_share_natural_finding_order() -> None:
    records = _build_case(
        {
            "events": [
                {"op": "open", "finding_id": "C-1000", "work_unit": "2"},
                {"op": "open", "finding_id": "C-101", "work_unit": "2"},
                {"op": "open", "finding_id": "C-62", "work_unit": "2"},
                {
                    "op": "correction",
                    "work_unit": "3",
                    "finding_ids": ["C-1000", "C-101", "C-62"],
                },
            ]
        }
    )

    reduction = reduce_findings(replay_artifacts(records, RUN_ID))
    expected = ("C-62", "C-101", "C-1000")

    assert tuple(item.finding_id for item in reduction.ledger.findings) == expected
    assert reduction.open_set.finding_ids == expected
    assert reduction.correction_attribution[0].finding_ids == expected
    assert reduction.request_subset(
        finding_ids=("C-1000", "C-62", "C-101")
    ).finding_ids == expected

    with pytest.raises(ValueError) as raised:
        reduction.request_subset(finding_ids=("C-1001", "C-102"))
    assert str(raised.value).endswith("unknown id C-102")


def test_final_review_pending_dispositions_are_derived_from_chain_boundary() -> None:
    records = _build_case(
        {
            "events": [
                {"op": "work", "work_unit": "2", "open_ids": []},
                *(
                    {
                        "op": "open",
                        "finding_id": f"C-{number:02d}",
                        "work_unit": "2",
                        "class": "OBSERVATION",
                    }
                    for number in range(1, 6)
                ),
                # open_ids is deliberately empty: the projection must use the
                # accepted transition prefix, never this mirror-like field.
                {"op": "work", "work_unit": "3", "open_ids": []},
                {
                    "op": "review",
                    "work_unit": "3",
                    "round": 1,
                    "verdict": "denied",
                    "finding_ids": [f"C-{number:02d}" for number in range(1, 6)],
                },
                {"op": "close", "finding_id": "C-01", "work_unit": "3"},
                {"op": "close", "finding_id": "C-02", "work_unit": "3"},
            ]
        }
    )

    projection = project_final_review_dispositions(
        replay_artifacts(records, RUN_ID), "3"
    )

    assert projection.initial_finding_ids == (
        "C-01", "C-02", "C-03", "C-04", "C-05"
    )
    assert projection.dispositioned_finding_ids == ("C-01", "C-02")
    assert projection.pending.finding_ids == ("C-03", "C-04", "C-05")


def test_reducer_is_pure_deterministic_and_does_not_mutate_records() -> None:
    records = _build_case(_corpus()[6])
    replay = replay_artifacts(records, RUN_ID)
    before = tuple(record.canonical_json() for record in records)
    assert reduce_findings(replay) == reduce_findings(replay)
    assert tuple(record.canonical_json() for record in records) == before

    tree = ast.parse(
        (ROOT / "src" / "finding_reducer.py").read_text(encoding="utf-8")
    )
    forbidden_imports = {
        "datetime", "os", "pathlib", "random", "secrets", "time", "uuid"
    }
    imported = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not imported & forbidden_imports
    forbidden_calls = {"open", "read_text", "read_bytes", "write_text", "write_bytes"}
    assert not {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    } & forbidden_calls
    assert not {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    } & forbidden_calls


def test_combined_multi_slice_correction_sequence_resumes_at_every_prefix() -> None:
    case = {
        "events": [
            {"op": "work", "work_unit": "2", "open_ids": []},
            {"op": "open", "finding_id": "C-02", "work_unit": "2", "class": "OBSERVATION"},
            {"op": "review", "work_unit": "2", "verdict": "approved", "finding_ids": ["C-02"]},
            {"op": "work", "work_unit": "3", "open_ids": ["C-02"]},
            {"op": "open", "finding_id": "C-01", "work_unit": "3"},
            {"op": "review", "work_unit": "3", "verdict": "denied", "finding_ids": ["C-01", "C-02"]},
            {"op": "correction", "work_unit": "4", "round": 1, "finding_ids": ["C-01", "C-02"]},
            {"op": "respond", "finding_id": "C-01", "work_unit": "4"},
            {"op": "close", "finding_id": "C-01", "work_unit": "4"},
            {"op": "open", "finding_id": "C-03", "work_unit": "4"},
            {"op": "review", "work_unit": "4", "round": 1, "verdict": "denied", "finding_ids": ["C-02", "C-03"]},
            {"op": "correction", "work_unit": "4", "round": 2, "finding_ids": ["C-02", "C-03"]},
            {"op": "respond", "finding_id": "C-03", "work_unit": "4"},
            {"op": "close", "finding_id": "C-03", "work_unit": "4"},
            {"op": "close", "finding_id": "C-02", "work_unit": "4", "class": "OBSERVATION"},
            {"op": "review", "work_unit": "4", "round": 2, "verdict": "approved", "finding_ids": ["C-01", "C-02", "C-03"]}
        ]
    }
    records = _build_case(case)
    reductions: list[FindingReduction] = []
    for end in range(1, len(records) + 1):
        if end == 1:
            with pytest.raises(ArtifactReplayError, match="RECORD-MISSING"):
                replay_artifacts(records[:end], RUN_ID)
            continue
        replay = replay_artifacts(records[:end], RUN_ID)
        first = reduce_findings(replay)
        second = reduce_findings(replay)
        assert first == second
        reductions.append(first)
    final = reductions[-1]
    assert _finding_states(final.ledger.findings) == [
        "C-01:CLOSED", "C-02:CLOSED", "C-03:CLOSED"
    ]
    assert final.open_set.finding_ids == ()
    correction = final.correction_for("4")
    assert correction is not None
    assert correction.finding_ids == ("C-01", "C-02", "C-03")
    assert final.request_subset(
        finding_ids=correction.finding_ids
    ).finding_ids == ("C-01", "C-02", "C-03")


def test_reopening_same_finding_id_replays_with_first_opening_and_diagnostic() -> None:
    records = _build_case(
        {
            "events": [
                {
                    "op": "open", "finding_id": "C-01", "work_unit": "2",
                    "summary": "Original logical identity",
                },
                {
                    "op": "open", "finding_id": "C-01", "work_unit": "3",
                    "summary": "Conflicting later identity",
                },
                {"op": "close", "finding_id": "C-01", "work_unit": "2"},
            ]
        }
    )
    reduction = reduce_findings(replay_artifacts(records, RUN_ID))

    assert len(reduction.ledger.lineages) == 1
    assert reduction.ledger.findings[0].summary == "Original logical identity"
    assert reduction.ledger.findings[0].status.value == "CLOSED"
    assert len(reduction.diagnostics) == 1
    diagnostic = reduction.diagnostics[0]
    assert diagnostic.code == "DUPLICATE-FINDING-OPENING"
    assert diagnostic.finding_id == "C-01"
    assert diagnostic.head_opening_record_id == records[2].record_id
    assert diagnostic.head_opening_revision == 1
    assert diagnostic.head_work_unit_id == "2"
    assert diagnostic.conflicting_opening_record_id == records[3].record_id
    assert diagnostic.conflicting_opening_revision == 2
    assert diagnostic.conflicting_work_unit_id == "3"
    assert reduction.request_subset(work_unit_id="3").finding_ids == ()
    assert (
        reduce_findings(replay_artifacts(records, RUN_ID)).diagnostics
        == reduction.diagnostics
    )


def test_reopening_same_finding_id_within_one_work_unit_is_diagnosed() -> None:
    records = _build_case(
        {
            "events": [
                {"op": "open", "finding_id": "C-01", "work_unit": "2"},
                {"op": "open", "finding_id": "C-01", "work_unit": "2"},
            ]
        }
    )
    reduction = reduce_findings(replay_artifacts(records, RUN_ID))

    assert len(reduction.ledger.lineages) == 1
    assert tuple(item.finding_id for item in reduction.ledger.findings) == ("C-01",)
    assert len(reduction.diagnostics) == 1
    assert reduction.diagnostics[0].head_opening_revision == 1
    assert reduction.diagnostics[0].conflicting_opening_revision == 2


def _call_names(tree: ast.AST) -> set[str]:
    return {
        (
            node.func.id
            if isinstance(node.func, ast.Name)
            else node.func.attr
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Name, ast.Attribute))
    }


def _finding_state_comparisons(tree: ast.AST) -> tuple[ast.Compare, ...]:
    violations: list[ast.Compare] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        rendered = ast.dump(node, include_attributes=False)
        status_attributes = {
            child.attr
            for child in ast.walk(node)
            if isinstance(child, ast.Attribute)
        }
        names = {
            child.id for child in ast.walk(node) if isinstance(child, ast.Name)
        }
        status_literals = {
            child.value.casefold()
            for child in ast.walk(node)
            if isinstance(child, ast.Constant) and isinstance(child.value, str)
        } & {"open", "closed"}
        enum_comparison = (
            "FindingStatus" in rendered
            and ("OPEN" in rendered or "CLOSED" in rendered)
        )
        raw_status_comparison = bool(status_literals) and (
            "status" in status_attributes
            or "finding_status" in status_attributes
            or "finding_status" in names
        )
        if enum_comparison or raw_status_comparison:
            violations.append(node)
    return tuple(violations)


def test_no_production_module_reimplements_finding_reduction() -> None:
    reducer = ROOT / "src" / "finding_reducer.py"
    reducer_calls = {
        "apply_finding_responses",
        "apply_reviewer_events",
        "merge_history_snapshots",
        "merge_request_result",
        "project_finding_response_delta",
        "project_finding_transition_ids",
        "project_latest_recorded_statuses",
        "project_open_set",
        "project_record_finding_statuses",
        "project_request_subset",
        "project_reviewer_persistence_transitions",
        "reduce_findings",
    }
    direct_transition_primitives = {
        "apply_finding_response", "apply_reviewer_finding_update"
    }
    expected_consumers = {
        "artifact_projection.py",
        "artifact_replay.py",
        "audit_trail.py",
        "contracts.py",
        "dry_run_scenarios.py",
        "finding_cleanup.py",
        "git_service.py",
        "native_codex_contract.py",
        "native_codex_request.py",
        "native_review_contract.py",
        "orchestrator.py",
        "provider_input_efficiency.py",
        "review_packets.py",
        "validation_matrix.py",
        "workflow.py",
        "workflow_audit_projection.py",
        "workflow_baseline.py",
        "workflow_persistence.py",
        "workflow_recovery.py",
        "workflow_requests.py",
        "workflow_run_setup.py",
    }
    actual_consumers: set[str] = set()
    for path in (ROOT / "src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        calls = _call_names(tree)
        if path == reducer:
            continue
        if calls & reducer_calls:
            actual_consumers.add(path.name)
        assert not calls & direct_transition_primitives, path.name
        if path.name not in {"artifact_models.py", "contracts.py"}:
            assert not _finding_state_comparisons(tree), (
                f"{path.name} derives Finding state outside finding_reducer.py"
            )
    assert actual_consumers == expected_consumers

    replay_tree = ast.parse(
        (ROOT / "src" / "artifact_replay.py").read_text(encoding="utf-8")
    )
    replay_wrapper = next(
        node
        for node in replay_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "replay_findings"
    )
    assert {
        node.func.id
        for node in ast.walk(replay_wrapper)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    } == {"reduce_findings"}


@pytest.mark.parametrize(
    "source",
    (
        'finding.status is FindingStatus.CLOSED',
        'finding.status.value == "OPEN"',
        'payload.finding_status != "closed"',
        'contracts.apply_finding_response(finding, decision, rationale)',
    ),
)
def test_finding_derivation_guard_rejects_known_bypass_shapes(source: str) -> None:
    tree = ast.parse(source)
    if "apply_finding_response" in source:
        assert "apply_finding_response" in _call_names(tree)
    else:
        assert _finding_state_comparisons(tree)


def test_corpus_operations_are_generic_not_commit_conditionals() -> None:
    operations = {
        event["op"] for case in _corpus() for event in case["events"]
    }
    assert operations == {
        "close",
        "correction",
        "import_open",
        "open",
        "plan",
        "respond",
        "review",
        "work",
    }
    source = (ROOT / "src" / "finding_reducer.py").read_text(encoding="utf-8")
    assert not any(commit in source for commit in HISTORICAL_COMMITS)


def test_corpus_cases_are_structurally_distinct_regressions() -> None:
    cases = _corpus()
    assert len({case["trigger"] for case in cases}) == len(cases)
    assert len({case["transition"] for case in cases}) == len(cases)

    signatures: set[str] = set()
    for case in cases:
        finding_names: dict[str, str] = {}
        work_units: dict[str, str] = {}

        def finding_name(value: str) -> str:
            return finding_names.setdefault(value, f"F{len(finding_names) + 1}")

        def work_unit_name(value: str) -> str:
            return work_units.setdefault(value, f"W{len(work_units) + 1}")

        normalized: list[dict[str, Any]] = []
        for event in case["events"]:
            item = dict(event)
            if "finding_id" in item:
                item["finding_id"] = finding_name(item["finding_id"])
            for key in ("finding_ids", "open_ids"):
                if key in item:
                    item[key] = [finding_name(value) for value in item[key]]
            if "work_unit" in item:
                item["work_unit"] = work_unit_name(item["work_unit"])
            item.pop("summary", None)
            normalized.append(item)
        signature = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
        assert signature not in signatures, case["commit"]
        signatures.add(signature)
