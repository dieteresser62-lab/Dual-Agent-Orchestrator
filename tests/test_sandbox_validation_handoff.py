from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_runtime import NativeAgentImplementerOutput
from artifact_bridge import ArtifactBridge
from artifact_models import AgentResultPayload, ProviderAttemptPayload, SideEffectPayload
from artifact_resume import resolve_resume_state
from artifact_store import ArtifactStore
from content_authority import ValidationCapture, validation_output_digest
from contracts import AgentRole, ValidationAttestation, ValidationRecord, ValidationStatus
from native_implementer_contract import (
    canonical_native_implementer_json, parse_bound_native_implementer_contract_result,
)
from orchestrator import OrchestratorConfig, ProductionWorkflowDriver
from provider_input_budget import (
    PreparedProviderInput, ProviderInputComponent, default_provider_input_budget_policy,
    measure_provider_input,
)
from test_orchestrator_runtime import _native_implementation_output, _native_review_approval
from test_workflow import _changes
from validation_matrix import validation_attestation_id
from workflow import WorkflowContext, WorkflowEngine, WorkflowHistory
from workflow_state import (
    AGENT_SANDBOX_VALIDATION_HANDOFF_KEY, GateReason, ProtocolBinding, ProtocolMode,
    WorkflowStep, WorkUnitKind, init_workflow_state,
)


CRASH_BOUNDARIES = (
    "before-handoff-batch", "after-handoff-batch", "after-checkpoint",
    "after-request", "after-raw-response", "after-agent-result", "after-response",
)
START_ERRORS = (
    "spawnSync /usr/bin/node EPERM",
    "local test server listen EPERM",
    "browser start Operation not permitted",
)


class HandoffCrash(BaseException):
    """Interrupt the real persistence path without provider exception handling."""


def prove_sandbox_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rationale: str,
    boundary: str | None = None, *, repeated: bool = False, legacy: bool = False,
) -> None:
    """Exercise production records, requests and recovery with scripted responses."""
    task = tmp_path / "task.md"
    task.write_text("Implement the bounded Slice.\n", encoding="utf-8")
    state = init_workflow_state(
        run_id="sandbox-handoff", task_file=str(task), branch="feature/handoff",
        branch_base="a" * 40, first_slice_start_commit="a" * 40, slice_count=1,
        task_digest=hashlib.sha256(task.read_bytes()).hexdigest(),
        task_scope_patterns=("src/runtime.py",), target_branch="feature/handoff",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "3"),
    ).complete_current_work_unit().start_work_unit(
        slice_id=1, kind=WorkUnitKind.SLICE,
        step=WorkflowStep.IMPLEMENTER_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit="a" * 40, scope_paths=("src/runtime.py",),
        start_fingerprint="b" * 64,
    )
    context = WorkflowContext(
        "Implement the bounded Slice.", "The plan is approved.", "Implement runtime.",
        test_changes_approved=True,
    )
    history = WorkflowHistory(state.current_work_unit_id)
    changes = _changes("b", "src/runtime.py")
    calls = []
    events = []
    interrupted = False
    followup_sequence = 3 if legacy else 2

    def interrupt(name: str) -> None:
        nonlocal interrupted
        if name == boundary and not interrupted:
            interrupted = True
            raise HandoffCrash(name)

    append_batch = ArtifactBridge.append_batch

    def batch(bridge, entries):
        handoff = any(
            isinstance(entry[0], SideEffectPayload)
            and entry[0].operation == (AGENT_SANDBOX_VALIDATION_HANDOFF_KEY,)
            for entry in entries
        )
        if handoff:
            interrupt("before-handoff-batch")
        result = append_batch(bridge, entries)
        if handoff:
            interrupt("after-handoff-batch")
        return result

    monkeypatch.setattr(ArtifactBridge, "append_batch", batch)

    def stop_output(invocation):
        bundle = invocation.native_request
        document = {
            "schema_version": "native-agent-implementer-result-v3",
            "result_type": "stop_result", "request_id": bundle.bound_context.request_id,
            "rule_id": "VALIDATION-UNAVAILABLE", "rationale": rationale,
            "remediation_paths": ["src/runtime.py"],
        }
        canonical = canonical_native_implementer_json(document)
        return NativeAgentImplementerOutput(
            parse_bound_native_implementer_contract_result(document, bundle.bound_context),
            canonical, bundle.bound_context.request_id,
            hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            context=bundle.bound_context.context,
        )

    def new_driver():
        driver = ProductionWorkflowDriver(
            repository_root=tmp_path, state_file=tmp_path / ".orchestrator/state.json",
            agents={}, config=OrchestratorConfig(repo_root=tmp_path),
            allowed_roots=(tmp_path,),
        )
        monkeypatch.setattr(driver, "collect_changes", lambda _start: changes)
        monkeypatch.setattr(driver, "_provider_repository_snapshot", lambda _start:
                            SimpleNamespace(fingerprint_entries=(), review_paths=changes.paths))
        persist = driver.persist_native_implementer_contract
        checkpoint = driver.checkpoint

        def persist_result(output, request_sequence, findings, **kwargs):
            persist(output, request_sequence, findings, **kwargs)
            if request_sequence == followup_sequence:
                interrupt("after-agent-result")

        def save(current, current_history):
            checkpoint(current, current_history)
            if current.current_work_unit.has_completed_side_effect(
                AGENT_SANDBOX_VALIDATION_HANDOFF_KEY
            ):
                interrupt("after-checkpoint")

        def implement(invocation):
            bundle = invocation.native_request
            assert bundle is not None
            driver._persist_native_agent_request_bundle(invocation)
            if invocation.request_sequence == followup_sequence:
                interrupt("after-request")
            calls.append(invocation)
            assert len(calls) <= followup_sequence, "recovery must not start an operation twice"
            profile = driver.active_state.protocol_binding.implementer_profile
            measurement = measure_provider_input(
                PreparedProviderInput(
                    command=("scripted-implementer",), stdin_text=bundle.canonical_json,
                    components=(ProviderInputComponent("stdin_prompt", bundle.canonical_json),),
                ),
                provider=profile.provider, role="implementer", operation=invocation.step.value,
                binding_fingerprint=bundle.bound_context.context.current_fingerprint,
                policy=default_provider_input_budget_policy(),
            )
            bootstrap = driver._persist_provider_bootstrap(measurement)
            attempt = driver._start_provider_attempt(
                measurement, bootstrap, operation_instance=f"request:{invocation.request_sequence}",
                durable_response_path=driver._native_implementer_response_path(invocation),
            )
            output = (
                stop_output(invocation) if invocation.request_sequence < followup_sequence or repeated
                else _native_implementation_output(invocation)
            )
            if invocation.request_sequence == followup_sequence:
                assert "AUTOMATIC ORCHESTRATOR VALIDATION HANDOFF" in bundle.canonical_json
                assert "Do not rerun the configured full validation matrix" in bundle.canonical_json
                events.append("stop" if repeated else "ready")
            driver._write_native_implementer_raw_response(attempt[2], output.canonical_json)
            if invocation.request_sequence == followup_sequence:
                interrupt("after-raw-response")
            driver.persist_native_implementer_contract(
                output, invocation.request_sequence, invocation.previous_findings,
            )
            driver._finish_provider_attempt(attempt, 0.0, None, None)
            if invocation.request_sequence == followup_sequence:
                interrupt("after-response")
            return output

        def validate(delta, request):
            events.append("validate")
            captures = tuple(
                ValidationCapture(command, "pass", 0, "scripted tests passed", "", "")
                for command in request.expected_commands
            )
            return ValidationAttestation(
                validation_attestation_id(request), delta.fingerprint, request.expected_commands,
                tuple(ValidationRecord(ValidationStatus.PASS, command, 0)
                      for command in request.expected_commands),
                validation_output_digest(captures), "Scripted validation passed.",
                command_specs=tuple(command.command_spec for command in request.commands),
                content_captures=captures,
            )

        def review(invocation):
            events.append("review")
            return _native_review_approval(invocation)

        monkeypatch.setattr(driver, "persist_native_implementer_contract", persist_result)
        monkeypatch.setattr(driver, "checkpoint", save)
        monkeypatch.setattr(driver, "invoke_implementer", implement)
        monkeypatch.setattr(driver, "validate", validate)
        monkeypatch.setattr(driver, "invoke_reviewer", review)
        return driver

    driver = new_driver()
    driver.bind_work_unit(state)
    driver.checkpoint(state, history)
    engine = WorkflowEngine(driver)
    if legacy:
        # Reproduce the measured history: the first stop predates F1b, then
        # resume dispatches request 2. F1b marks its handoff without advancing
        # that request; a new stop-gate resume must dispatch request 3.
        _, _, invocation, _, _ = engine._prepare_agent_dispatch(state, context, history)
        driver.invoke_implementer(invocation)
        state = state.await_policy_gate(
            reason=GateReason.STOP_REQUEST, detail=f"VALIDATION-UNAVAILABLE | {rationale}",
        )
        driver.checkpoint(state, history)
        state = state.resume_after_user_decision()
        driver.checkpoint(state, history)
        _, _, invocation, _, _ = engine._prepare_agent_dispatch(state, context, history)
        driver.invoke_implementer(invocation)
        state = state.mark_side_effect_completed(AGENT_SANDBOX_VALIDATION_HANDOFF_KEY)
        state = state.await_policy_gate(
            reason=GateReason.STOP_REQUEST, detail=f"VALIDATION-UNAVAILABLE | {rationale}",
        )
        driver.checkpoint(state, history)
        state = resolve_resume_state(tmp_path, state.run_id).state
        state = state.resume_after_user_decision()
        driver.checkpoint(state, history)
    try:
        advanced, history = engine._run_implementer(state, context, history)
    except HandoffCrash:
        # Throw away the state mirror and the engine's transient handoff
        # context. Only production replay may recover the durable cursor.
        driver.state_file.unlink(missing_ok=True)
        recovered = resolve_resume_state(tmp_path, state.run_id).state
        assert recovered.current_work_unit.round_number == 1
        assert recovered.current_work_unit.request_sequence == (
            1 if boundary == "before-handoff-batch" else 2
        )
        assert recovered.current_work_unit.has_completed_side_effect(
            AGENT_SANDBOX_VALIDATION_HANDOFF_KEY
        ) is (boundary != "before-handoff-batch")
        driver = new_driver()
        driver.bind_work_unit(recovered)
        engine = WorkflowEngine(driver)
        advanced, history = engine._run_implementer(recovered, context, history)
    assert interrupted is (boundary is not None)
    assert [invocation.request_sequence for invocation in calls] == list(
        range(1, followup_sequence + 1)
    )
    assert len({call.native_request.bound_context.request_id for call in calls}) == len(calls)
    assert advanced.current_work_unit.round_number == 1
    assert advanced.current_work_unit.request_sequence == followup_sequence
    assert events == (["stop"] if repeated else ["ready"])
    if repeated:
        assert advanced.current_work_unit.gate.reason is GateReason.STOP_REQUEST
    else:
        assert advanced.current_step is WorkflowStep.REVIEWER_SLICE_REVIEW
        engine._run_review(advanced, context, history, AgentRole.REVIEWER)
        assert events == ["ready", "validate", "review"]
    chain = ArtifactStore(tmp_path, state.run_id).load_chain()
    attempts = [record.payload for record in chain
                if isinstance(record.payload, ProviderAttemptPayload)
                and record.payload.phase == "started"]
    assert len(attempts) == followup_sequence
    assert len({attempt.logical_operation_id for attempt in attempts}) == len(attempts)
    results = [record for record in chain if isinstance(record.payload, AgentResultPayload)]
    assert [record.logical_id for record in results] == [
        f"agent-2-implementer_implementation-{sequence}"
        for sequence in range(1, followup_sequence + 1)
    ]
    assert sum(isinstance(record.payload, SideEffectPayload)
               and record.payload.operation == (AGENT_SANDBOX_VALIDATION_HANDOFF_KEY,)
               and record.payload.phase == "result" for record in chain) == 1


@pytest.mark.parametrize("rationale", START_ERRORS)
@pytest.mark.parametrize("boundary", (None, *CRASH_BOUNDARIES))
def test_sandbox_handoff_production_records_and_resume(tmp_path, monkeypatch, rationale, boundary):
    prove_sandbox_handoff(tmp_path, monkeypatch, rationale, boundary)


@pytest.mark.parametrize("rationale", START_ERRORS)
@pytest.mark.parametrize("boundary", (None, "after-checkpoint", "after-response"))
def test_second_sandbox_stop_does_not_start_a_third_operation(
    tmp_path, monkeypatch, rationale, boundary,
):
    prove_sandbox_handoff(tmp_path, monkeypatch, rationale, boundary, repeated=True)


@pytest.mark.parametrize("rationale", START_ERRORS)
def test_f1b_stop_gate_resumes_with_handoff_notice(tmp_path, monkeypatch, rationale):
    prove_sandbox_handoff(tmp_path, monkeypatch, rationale, legacy=True)
