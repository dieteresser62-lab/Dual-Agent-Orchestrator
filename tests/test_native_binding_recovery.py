from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from artifact_models import canonical_json
from contracts import ReadinessMarker
from native_implementer_contract import NativeImplementerRequestKind
from native_implementer_request import build_native_implementer_request
from orchestrator import OrchestratorConfig, ProductionWorkflowDriver
from workflow_state import WorkflowStep
from workflow import WorkflowExecutionError

import test_native_implementer_request as implementer_support


@pytest.mark.parametrize("profile", (
    "codex", "claude-implementer",  # allowlist:provider -- profile configuration: persisted writer profiles
))
@pytest.mark.parametrize("step,kind", (
    (WorkflowStep.IMPLEMENTER_PLAN, NativeImplementerRequestKind.PLAN),
    (WorkflowStep.IMPLEMENTER_PLAN_REVISION, NativeImplementerRequestKind.PLAN),
    (WorkflowStep.IMPLEMENTER_IMPLEMENTATION, NativeImplementerRequestKind.IMPLEMENTATION),
    (WorkflowStep.IMPLEMENTER_CORRECTION, NativeImplementerRequestKind.CORRECTION),
))
def test_recovery_preserves_bound_writer_in_every_implementer_step(
    tmp_path: Path, profile: str, step: WorkflowStep, kind: NativeImplementerRequestKind,
) -> None:
    spec = implementer_support._spec()
    contract = spec.context.contract
    if kind is not NativeImplementerRequestKind.PLAN:
        contract = replace(contract, readiness_marker=ReadinessMarker.IMPLEMENTATION,
                           require_slice_plan=False, plan_artifact_path=None)
    spec = replace(spec, context=replace(spec.context, operation=step.value,
                                        request_kind=kind, contract=contract))
    original = build_native_implementer_request(spec, profile=profile)
    rebuilt = build_native_implementer_request(replace(spec, assignment="Rebuilt assignment"), profile=profile)
    driver = ProductionWorkflowDriver(repository_root=tmp_path,
        state_file=tmp_path / ".orchestrator/state.json", agents={},
        config=OrchestratorConfig(repo_root=tmp_path), allowed_roots=(tmp_path,))
    driver.active_state = SimpleNamespace(run_id=spec.context.run_id)
    invocation = SimpleNamespace(work_unit_id=1, step=step, request_sequence=1)
    path = driver._native_agent_request_path(invocation)
    path.parent.mkdir(parents=True)
    path.write_text(driver._native_agent_request_bundle_json(original))

    restored = driver._load_native_agent_request_bundle(invocation, rebuilt)

    assert restored.capability_profile == profile
    assert restored.canonical_json == original.canonical_json
    assert restored.provider_response_schema_json == original.provider_response_schema_json
    assert restored.bound_context.request_id == original.bound_context.request_id
    # A foreign profile and corrupt writer still fail the unchanged validation.
    other_profile = "codex" if profile != "codex" else "claude-implementer"  # allowlist:provider -- profile configuration: foreign writer rejection
    foreign = build_native_implementer_request(spec, profile=other_profile)
    with pytest.raises(WorkflowExecutionError, match="provider response schema differs from bound context"):
        driver._load_native_agent_request_bundle(invocation, foreign)
    document = json.loads(path.read_text())
    document["provider_response_schema"] = "{}"
    path.write_text(canonical_json(document).decode())
    with pytest.raises(WorkflowExecutionError, match="provider response schema differs from bound context"):
        driver._load_native_agent_request_bundle(invocation, rebuilt)
