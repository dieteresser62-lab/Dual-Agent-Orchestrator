from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from agent_runtime import ProviderRequestRoundRequired
from artifact_bridge import ArtifactBridge
from artifact_models import (
    _IDENTIFIER_RE,
    ArtifactRecord,
    BindingPayload,
    Fingerprint,
    FingerprintKind,
    FindingSeverity,
    FindingTransitionPayload,
    PlanPayload,
    ProviderContentPayload,
    Role,
    SliceSpec,
    WorkUnitPayload,
    WorkflowCompletionPayload,
)
from artifact_store import ArtifactStore
from finding_responsibility import SliceResponsibility
from workflow import WorkflowExecutionError
from workflow_persistence import (
    WorkflowPersistence,
    WorkflowPersistenceDependencies,
    provider_content_idempotency_key,
)
from workflow_state import WorkflowStep, WorkUnitKind


ROOT = Path(__file__).resolve().parents[1]
PERSISTENCE_PATH = ROOT / "src/workflow_persistence.py"
DRIVER_PATH = ROOT / "src/orchestrator.py"

EXPECTED_INTERNAL_IMPORTS = {
    "agent_runtime",
    "artifact_bridge",
    "artifact_models",
    "artifact_replay",
    "contracts",
    "finding_reducer",
    "orchestrator_diagnostics",
    "review_packets",
    "slice_exit",
    "task_contract",
    "workflow",
    "workflow_state",
}

EXPECTED_DEPENDENCY_EDGES = {
    "active_state",
    "append_gate_decision_binding",
    "append_workflow_event",
    "append_workflow_transition",
    "artifact_bridge",
    "artifact_fingerprint",
    "canonical_agent_result",
    "gate_transition_payload",
    "materialize_review_packet",
    "native_agent_request_bundle_json",
    "native_agent_request_path",
    "prepare_completion_finding_handoff",
}

EXPECTED_DRIVER_BINDINGS = {
    "active_state": "lambda: self.active_state",
    "append_gate_decision_binding": "self._append_gate_decision_binding",
    "append_workflow_event": "self._append_workflow_event",
    "append_workflow_transition": "self._append_workflow_transition",
    "artifact_bridge": "lambda: self._artifact_bridge",
    "artifact_fingerprint": "self._artifact_fingerprint",
    "canonical_agent_result": "self._canonical_native_agent_result",
    "gate_transition_payload": "self._gate_transition_payload",
    "materialize_review_packet": "self._materialize_review_packet",
    "native_agent_request_bundle_json": "self._native_agent_request_bundle_json",
    "native_agent_request_path": "self._native_agent_request_path",
    "prepare_completion_finding_handoff": "self._prepare_completion_finding_handoff",
}

DRIVER_FACADES = {
    "_persist_workflow_snapshot": "_persist_workflow_snapshot",
    "_persist_slice_boundaries": "_persist_slice_boundaries",
    "_persist_gate_snapshot": "_persist_gate_snapshot",
    "_persist_structured_tail": "_persist_structured_tail",
    "_persist_native_agent_request_bundle": "_persist_native_agent_request_bundle",
    "persist_review_packet": "persist_review_packet",
    "_persist_provider_content": "_persist_provider_content",
    "persist_native_codex_contract": "persist_native_implementer_contract",
    "persist_native_review_contract": "persist_native_review_contract",
    "_persist_review_finding_transitions": "_persist_review_finding_transitions",
    "persist_contract_diagnostic": "persist_contract_diagnostic",
    "persist_validation_attestation": "persist_validation_attestation",
    "persist_validation_request": "persist_validation_request",
    "persist_invocation_failure": "persist_invocation_failure",
    "persist_gate_decision": "persist_gate_decision",
    "persist_gate_transition": "persist_gate_transition",
    "persist_implementation_handoff": "persist_implementation_handoff",
}


def test_changed_request_with_same_binding_requests_a_new_round() -> None:
    def bundle(assignment: str, fingerprint: str) -> str:
        return json.dumps(
            {
                "canonical_request": json.dumps(
                    {
                        "assignment": assignment,
                        "current_fingerprint": fingerprint,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    fingerprint = "a" * 64
    previous = bundle("old composition", fingerprint)
    current = bundle("new composition", fingerprint)

    with pytest.raises(ProviderRequestRoundRequired) as raised:
        WorkflowPersistence._raise_request_binding_difference(previous, current)

    assert raised.value.binding_fingerprint == fingerprint
    assert raised.value.previous_input_digest == hashlib.sha256(
        previous.encode("utf-8")
    ).hexdigest()
    assert raised.value.current_input_digest == hashlib.sha256(
        current.encode("utf-8")
    ).hexdigest()


def _tree(path: Path = PERSISTENCE_PATH, source: str | None = None) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8") if source is None else source)


def _class(tree: ast.Module, name: str) -> ast.ClassDef:
    return next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == name
    )


def _dependency_edges(source: str | None = None) -> set[str]:
    persistence = _class(_tree(source=source), "WorkflowPersistence")
    return {
        node.attr
        for node in ast.walk(persistence)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "_dependencies"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "self"
    }


def _unexpected_dependency(*_args: object, **_kwargs: object) -> Any:
    raise AssertionError("an unavailable persistence dependency was invoked")


def _dependencies(
    *, bridge: ArtifactBridge | None = None,
    append_workflow_event: Any = _unexpected_dependency,
) -> WorkflowPersistenceDependencies:
    return WorkflowPersistenceDependencies(
        artifact_bridge=lambda: bridge,
        active_state=lambda: None,
        artifact_fingerprint=_unexpected_dependency,
        append_workflow_transition=_unexpected_dependency,
        gate_transition_payload=_unexpected_dependency,
        append_gate_decision_binding=_unexpected_dependency,
        append_workflow_event=append_workflow_event,
        native_agent_request_path=_unexpected_dependency,
        native_agent_request_bundle_json=_unexpected_dependency,
        materialize_review_packet=_unexpected_dependency,
        canonical_agent_result=_unexpected_dependency,
        prepare_completion_finding_handoff=lambda _state: None,
    )


def test_persistence_module_has_one_way_imports_and_complete_dependency_inventory() -> None:
    tree = _tree()
    known_modules = {path.stem for path in (ROOT / "src").glob("*.py")}
    internal_imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            module = node.module.split(".", 1)[0]
            if module in known_modules:
                internal_imports.add(module)
        elif isinstance(node, ast.Import):
            internal_imports.update(
                module
                for alias in node.names
                if (module := alias.name.split(".", 1)[0]) in known_modules
            )
    assert internal_imports == EXPECTED_INTERNAL_IMPORTS
    assert "orchestrator" not in internal_imports

    importers = []
    for path in sorted((ROOT / "src").glob("*.py")):
        if path == PERSISTENCE_PATH:
            continue
        candidate = _tree(path)
        if any(
            isinstance(node, ast.ImportFrom)
            and node.module == "workflow_persistence"
            for node in ast.walk(candidate)
        ):
            importers.append(path.relative_to(ROOT).as_posix())
    assert importers == [
        "src/orchestrator.py",
        "src/workflow_baseline.py",
        "src/workflow_validation.py",
    ]
    assert _dependency_edges() == EXPECTED_DEPENDENCY_EDGES


@pytest.mark.parametrize(
    "edge",
    (
        "append_workflow_event",
        "append_workflow_transition",
        "artifact_bridge",
    ),
)
def test_omitted_persistence_edge_turns_inventory_red(edge: str) -> None:
    source = PERSISTENCE_PATH.read_text(encoding="utf-8")
    marker = f"self._dependencies.{edge}"
    assert marker in source
    mutated = source.replace(marker, f"omitted_{edge}")
    assert edge not in _dependency_edges(mutated)
    assert _dependency_edges(mutated) != EXPECTED_DEPENDENCY_EDGES


def test_driver_surface_delegates_but_checkpoint_remains_composition_root() -> None:
    driver = _class(_tree(DRIVER_PATH), "ProductionWorkflowDriver")
    methods = {
        node.name: node
        for node in driver.body
        if isinstance(node, ast.FunctionDef)
    }
    for facade, target in DRIVER_FACADES.items():
        rendered = ast.dump(methods[facade], include_attributes=False)
        assert "_persistence_boundary" in rendered, facade
        assert target in rendered, facade
        assert not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            for node in ast.walk(methods[facade])
        ), facade

    matching = ast.dump(methods["_matching_gate_record"], include_attributes=False)
    assert "WorkflowPersistence" in matching
    assert "_matching_gate_record" in matching

    checkpoint = ast.dump(methods["checkpoint"], include_attributes=False)
    assert "_persist_structured_baseline" in checkpoint
    assert "_project_audit" in checkpoint
    persistence_methods = {
        node.name
        for node in _class(_tree(), "WorkflowPersistence").body
        if isinstance(node, ast.FunctionDef)
    }
    assert "checkpoint" not in persistence_methods


def test_driver_binds_every_persistence_edge_to_the_exact_expected_callback() -> None:
    driver = _class(_tree(DRIVER_PATH), "ProductionWorkflowDriver")
    boundary = next(
        node
        for node in driver.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_persistence_boundary"
    )
    dependency_call = next(
        node
        for node in ast.walk(boundary)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "WorkflowPersistenceDependencies"
    )
    actual = {
        keyword.arg: ast.unparse(keyword.value)
        for keyword in dependency_call.keywords
        if keyword.arg is not None
    }
    assert actual == EXPECTED_DRIVER_BINDINGS


def test_missing_provider_content_authority_fails_closed() -> None:
    persistence = WorkflowPersistence(_dependencies())
    with pytest.raises(
        WorkflowExecutionError,
        match="provider content has no artifact authority",
    ):
        persistence._persist_provider_content(
            role=Role.CODEX,
            work_unit_id=1,
            request_sequence=1,
            operation="implementation",
            request_id="request-1",
            canonical='{"result":"ok"}',
            content_kind="agent_result",
            fingerprint="a" * 64,
        )


def test_required_workflow_event_omission_fails_closed() -> None:
    persistence = WorkflowPersistence(
        _dependencies(append_workflow_event=lambda **_kwargs: None)
    )
    with pytest.raises(
        WorkflowExecutionError,
        match="omitted its required workflow event",
    ):
        persistence._append_workflow_event(
            event_kind="validation",
            work_unit_id="1",
            slice_id="1",
            round_number=1,
            domain_record=cast(Any, object()),
        )


def test_completed_workflow_rejects_canary_sequence_with_unowned_finding() -> None:
    run_id = "canary-unowned-finding"
    fingerprint = "b" * 64
    records: list[ArtifactRecord] = []

    def append_record(payload: object, logical_id: str) -> None:
        records.append(
            ArtifactRecord.create(
                run_id=run_id,
                logical_id=logical_id,
                revision=1,
                fingerprint=Fingerprint(
                    FingerprintKind.IMPLEMENTATION, fingerprint
                ),
                predecessor_ids=(records[-1].record_id,) if records else (),
                created_at=f"2026-09-20T04:0{len(records)}:00+00:00",
                idempotency_key=logical_id,
                payload=payload,
            )
        )

    append_record(
        FindingTransitionPayload(
            finding_id="C-01",
            reporter=Role.CLAUDE,
            actor=Role.CLAUDE,
            action="opened",
            severity=FindingSeverity.OBSERVATION,
            finding_status="open",
            rationale="Canary finding remained undecided",
            work_unit_id="2",
            summary="Canary finding remained undecided",
            acceptance_test="The canary finding receives a decision",
            origin_slice_id="2",
            origin_round_number=1,
            responsibility=None,
        ),
        "finding-c-01-opened",
    )
    for work_unit_id in ("2", "3", "4"):
        append_record(
            WorkUnitPayload(work_unit_id, 1, ("src/fix.py",)),
            f"work-unit-{work_unit_id}",
        )
    append_record(
        BindingPayload("commit", "c" * 40, "attestation", ("approval",)),
        "commit-4",
    )

    appended_payloads: list[object] = []
    bridge = SimpleNamespace(
        store=SimpleNamespace(current_chain=lambda: tuple(records)),
        append=lambda payload, **_kwargs: appended_payloads.append(payload),
    )
    persistence = WorkflowPersistence(_dependencies(bridge=cast(Any, bridge)))
    state = SimpleNamespace(
        task_digest="d" * 64,
        work_units=(),
        work_plan_path=None,
        planned_slices=(),
        approved_plan_commit=None,
        current_step=WorkflowStep.COMPLETED,
        slices=(SimpleNamespace(commit_ref="c" * 40),),
        execution_mode="IMPLEMENT",
        run_id=run_id,
    )

    with pytest.raises(
        WorkflowExecutionError,
        match=(
            "OPEN-FINDING-WITHOUT-RESPONSIBILITY: workflow completion rejected; "
            "open findings without valid responsibility: C-01"
        ),
    ):
        persistence._persist_structured_tail(cast(Any, state))

    assert not any(
        isinstance(payload, WorkflowCompletionPayload)
        for payload in appended_payloads
    )


def test_completed_workflow_rejects_route_to_unknown_plan_slice() -> None:
    run_id = "completion-unknown-slice"
    plan_commit = "a" * 40
    fingerprint = "b" * 64
    records: list[ArtifactRecord] = []

    def append_record(payload: object, logical_id: str) -> None:
        records.append(
            ArtifactRecord.create(
                run_id=run_id,
                logical_id=logical_id,
                revision=1,
                fingerprint=Fingerprint(
                    FingerprintKind.IMPLEMENTATION, fingerprint
                ),
                predecessor_ids=(records[-1].record_id,) if records else (),
                created_at=f"2026-09-20T04:{len(records):02d}:00+00:00",
                idempotency_key=logical_id,
                payload=payload,
            )
        )

    append_record(
        PlanPayload(
            "docs/internal/plan.md",
            plan_commit,
            tuple(
                SliceSpec(str(slice_id), f"Slice {slice_id}", ("src/fix.py",))
                for slice_id in range(1, 7)
            ),
        ),
        "approved-plan",
    )
    append_record(
        WorkUnitPayload("1", 1, ("src/fix.py",)),
        "work-unit-1",
    )
    append_record(
        FindingTransitionPayload(
            finding_id="C-01",
            reporter=Role.CLAUDE,
            actor=Role.CLAUDE,
            action="opened",
            severity=FindingSeverity.OBSERVATION,
            finding_status="open",
            rationale="The finding needs a real target Slice",
            work_unit_id="1",
            summary="Repair src/fix.py",
            acceptance_test="src/fix.py passes its regression test",
            origin_slice_id="1",
            origin_round_number=1,
            responsibility=None,
        ),
        "finding-c-01-opened",
    )
    append_record(
        FindingTransitionPayload(
            finding_id="C-01",
            reporter=Role.CLAUDE,
            actor=Role.CLAUDE,
            action="routed",
            severity=FindingSeverity.OBSERVATION,
            finding_status="open",
            rationale="Route to the misspelled target",
            work_unit_id="1",
            responsibility=SliceResponsibility(
                run_id, plan_commit, "01"
            ),
        ),
        "finding-c-01-routed",
    )
    append_record(
        BindingPayload("commit", "c" * 40, "attestation", ("approval",)),
        "commit-6",
    )

    appended_payloads: list[object] = []
    bridge = SimpleNamespace(
        store=SimpleNamespace(current_chain=lambda: tuple(records)),
        append=lambda payload, **_kwargs: appended_payloads.append(payload),
    )
    persistence = WorkflowPersistence(_dependencies(bridge=cast(Any, bridge)))
    state = SimpleNamespace(
        task_digest="d" * 64,
        work_units=(),
        work_plan_path=None,
        planned_slices=(),
        approved_plan_commit=None,
        current_step=WorkflowStep.COMPLETED,
        slices=(SimpleNamespace(commit_ref="c" * 40),),
        execution_mode="IMPLEMENT",
        run_id=run_id,
    )

    with pytest.raises(
        WorkflowExecutionError,
        match="findings without a valid terminal outcome: C-01",
    ):
        persistence._persist_structured_tail(cast(Any, state))

    assert not any(
        isinstance(payload, WorkflowCompletionPayload)
        for payload in appended_payloads
    )


def test_provider_content_sink_directly_externalizes_canonical_bytes(
    tmp_path: Path,
) -> None:
    bridge = ArtifactBridge(ArtifactStore(tmp_path, "workflow-persistence-module"))
    persistence = WorkflowPersistence(_dependencies(bridge=bridge))
    canonical = '{"result":"ok"}'

    record = persistence._persist_provider_content(
        role=Role.CODEX,
        work_unit_id=2,
        request_sequence=3,
        operation="review",
        request_id="request-direct",
        canonical=canonical,
        content_kind="review_result",
        fingerprint="b" * 64,
    )

    payload = record.payload
    assert isinstance(payload, ProviderContentPayload)
    assert payload.work_unit_id == "2"
    assert payload.round_number == 3
    assert bridge.store.read_blob(payload.blob) == canonical.encode("utf-8")


def test_final_correction_provider_content_key_is_canonical_and_idempotent(
    tmp_path: Path,
) -> None:
    fields = {
        "role": Role.CODEX,
        "work_unit_id": 45,
        "request_sequence": 1,
        "operation": "codex_final_correction",
        "request_id": (
            "native-codex-request-"
            "a18a0c6b0b7822fd97d5cf80b8e0fd57b18b84744ba8c2b4b656a45c3e8bd960"
        ),
        "response_sha256": (
            "24c7e12e84dc08bbcadc6096383a4068644e362545680cdce0713ddc12704464"
        ),
    }

    first = provider_content_idempotency_key(**fields)
    second = provider_content_idempotency_key(**fields)
    distinct_inputs = (
        fields,
        {**fields, "role": Role.CLAUDE},
        {**fields, "work_unit_id": 46},
        {**fields, "request_sequence": 2},
        {**fields, "operation": "claude_slice_review"},
        {**fields, "request_id": "native-review-request-" + "b" * 64},
        {**fields, "response_sha256": "c" * 64},
    )
    distinct_keys = {
        provider_content_idempotency_key(**item) for item in distinct_inputs
    }
    incident_key = (
        "provider-content:codex:45:1:codex_final_correction:"
        f"{fields['request_id']}:{fields['response_sha256']}"
    )

    assert first == second
    assert _IDENTIFIER_RE.fullmatch(first)
    assert len(first) < 200
    assert len(distinct_keys) == len(distinct_inputs)
    assert all(_IDENTIFIER_RE.fullmatch(key) for key in distinct_keys)
    assert len(incident_key) == 201
    assert _IDENTIFIER_RE.fullmatch(incident_key) is None

    bridge = ArtifactBridge(ArtifactStore(tmp_path, "final-correction-content-key"))
    persistence = WorkflowPersistence(_dependencies(bridge=bridge))
    sink_fields = {
        key: fields[key]
        for key in (
            "role", "work_unit_id", "request_sequence", "operation", "request_id"
        )
    }
    persisted = persistence._persist_provider_content(
        **sink_fields,
        canonical='{"result":"ok"}',
        content_kind="agent_result",
        fingerprint="d" * 64,
    )
    repeated = persistence._persist_provider_content(
        **sink_fields,
        canonical='{"result":"ok"}',
        content_kind="agent_result",
        fingerprint="d" * 64,
    )

    assert repeated.record_id == persisted.record_id
    assert repeated.idempotency_key == persisted.idempotency_key
    assert _IDENTIFIER_RE.fullmatch(persisted.idempotency_key)
    assert len(bridge.store.load_chain()) == 1
