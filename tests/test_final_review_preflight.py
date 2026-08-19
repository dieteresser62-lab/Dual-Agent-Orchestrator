from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from artifact_bridge import ArtifactBridge
from artifact_models import (
    AgentResultPayload,
    ProviderInputComponentPayload,
    ProviderInputMeasurementPayload,
    ReviewPayload,
    Role,
    ValidationAttestationPayload,
    ValidationResult,
    CommandSpec,
    WorkflowCompletionPayload,
)
from artifact_store import ArtifactStore
from contracts import PlannedSlice
from final_review_preflight import relevant_record_head, run_final_review_preflight
from workflow_state import WorkflowStep, WorkUnitKind, init_workflow_state


FINGERPRINT = "d" * 64


def _state(step: WorkflowStep):
    state = init_workflow_state(
        run_id="preflight-run", task_file="/repo/task.md", branch="feature/preflight",
        branch_base="a" * 40, slice_count=1,
    ).bind_slice_plan(
        (PlannedSlice(1, "implementation", ("src/one.py",)),),
        first_start_commit="a" * 40,
    ).complete_current_work_unit().start_work_unit(
        slice_id=1, kind=WorkUnitKind.SLICE, step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit="a" * 40, scope_paths=("src/one.py",), start_fingerprint="1" * 64,
    ).complete_current_slice(commit_ref="b" * 40).start_final_review_work_unit()
    return state if step is WorkflowStep.CODEX_FINAL_REVIEW else state.with_current_step(step)


def _measurement(bridge: ArtifactBridge, state, operation: str):  # type: ignore[no-untyped-def]
    head = relevant_record_head(bridge.store.load_chain())
    payload = ProviderInputMeasurementPayload(
        Role(operation.split("_", 1)[0]), Role(operation.split("_", 1)[0]), operation,
        str(state.current_work_unit_id), "e" * 64, head, "f" * 64, "0" * 64,
        (ProviderInputComponentPayload("stdin_prompt", 10, 10),), 10, 10,
        100, 100, None, None, None, 100, 100, True, (), 0, 0, "stdin_prompt",
    )
    return bridge.append(
        payload, logical_id=f"measurement-{operation}", idempotency_key=f"measurement:{operation}",
        fingerprint_sha256=FINGERPRINT,
    )


def _attest(bridge: ArtifactBridge) -> None:
    bridge.append(
        ValidationAttestationPayload(
            (ValidationResult(CommandSpec("pytest", ("pytest",)), "pass", 0, "1" * 64),),
            Role.ORCHESTRATOR,
        ),
        logical_id="final-validation", idempotency_key="final-validation",
        fingerprint_sha256=FINGERPRINT,
    )


@pytest.mark.parametrize(
    ("step", "prior_kind"),
    [
        (WorkflowStep.CODEX_FINAL_REVIEW, None),
        (WorkflowStep.CLAUDE_FINAL_REVIEW, "codex"),
        (WorkflowStep.ANTIGRAVITY_FINAL_REVIEW, "claude"),
    ],
)
def test_each_final_transition_accepts_only_currently_available_facts(
    tmp_path: Path, step: WorkflowStep, prior_kind: str | None,
) -> None:
    state = _state(step)
    bridge = ArtifactBridge(ArtifactStore(tmp_path, state.run_id))
    _attest(bridge)
    if prior_kind == "codex":
        bridge.append(
            AgentResultPayload(Role.CODEX, str(state.current_work_unit_id), "ready", ()),
            logical_id="codex-final", idempotency_key="codex-final", fingerprint_sha256=FINGERPRINT,
        )
    elif prior_kind == "claude":
        bridge.append(
            ReviewPayload(Role.CLAUDE, str(state.current_work_unit_id), "approved", (), "checked"),
            logical_id="claude-final", idempotency_key="claude-final", fingerprint_sha256=FINGERPRINT,
        )
    measurement = _measurement(bridge, state, step.value)

    result = run_final_review_preflight(
        state=state, records=bridge.store.load_chain(), measurement_record=measurement,
        repository_paths=("src/one.py",),
    )

    assert result.passed


def test_preflight_denies_missing_attestation_unexpected_path_and_premature_completion(tmp_path: Path) -> None:
    state = _state(WorkflowStep.CODEX_FINAL_REVIEW)
    bridge = ArtifactBridge(ArtifactStore(tmp_path, state.run_id))
    measurement = _measurement(bridge, state, state.current_step.value)
    missing = run_final_review_preflight(
        state=state, records=bridge.store.load_chain(), measurement_record=measurement,
    )
    assert missing.error_code == "ATTESTATION-MISSING"

    _attest(bridge)
    unauthorized = run_final_review_preflight(
        state=state, records=bridge.store.load_chain(), measurement_record=measurement,
        repository_paths=("secrets.txt",),
    )
    assert unauthorized.error_code == "UNAUTHORIZED-PATH"

    bridge.append(
        WorkflowCompletionPayload("completed", "binding-placeholder"),
        logical_id="premature-completion", idempotency_key="premature-completion",
        fingerprint_sha256=FINGERPRINT,
    )
    premature = run_final_review_preflight(
        state=state, records=bridge.store.load_chain(), measurement_record=measurement,
    )
    assert premature.error_code == "PREMATURE-COMPLETION"


def test_relevant_record_head_excludes_bootstrap_records(tmp_path: Path) -> None:
    state = _state(WorkflowStep.CODEX_FINAL_REVIEW)
    bridge = ArtifactBridge(ArtifactStore(tmp_path, state.run_id))
    before = relevant_record_head(bridge.store.load_chain())
    _measurement(bridge, state, state.current_step.value)
    assert relevant_record_head(bridge.store.load_chain()) == before
