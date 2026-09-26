"""Versioned, provider-free crash and resume harness for structured-v2.

The harness drives the production ledger writers.  It does not mock the ledger
protocol: every case writes real immutable artifact records, raises at one
named production boundary, reconstructs the runtime from disk, and either
converges through normal reconciliation or emits the assignment's stop state.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Mapping

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from artifact_bridge import (
    ArtifactBridge,
    attestation_payload,
    provider_input_measurement_payload,
    review_payload_matches_complete_result,
)
import artifact_resume
from artifact_resume import ArtifactResumeError, resolve_resume_state
from artifact_models import (
    SIDE_EFFECT_CLASSES,
    BindingPayload,
    ReviewPayload,
    Role,
    ValidationAttestationPayload,
    canonical_json,
    technical_text_evidence,
)
from artifact_replay import replay_artifacts
from artifact_store import ArtifactStore
from dry_run_scenarios import (
    DryRunScenario,
    ScriptedInterruption as InjectedCrash,
    ScriptedWorkflowDriver,
)
from orchestrator import OrchestratorConfig, ProductionWorkflowDriver
from provider_input_budget import ProviderInputComponentSize, ProviderInputMeasurement
from side_effects import (
    Reconciliation,
    ReconciliationOutcome,
    SideEffectBoundary,
    SideEffectBoundaryPhase,
    SideEffectExecutor,
    SideEffectReconciliationError,
    SideEffectSpec,
    reconcile_file_write,
    reconcile_git_commit,
    reconcile_provider_start,
    reconcile_queue_move,
    sha256_bytes,
)
from workflow_state import (
    ProtocolBinding,
    ProtocolMode,
    WorkUnitKind,
    WorkflowState,
    WorkflowStep,
    init_workflow_state,
)


HARNESS_SCHEMA_VERSION = "provider-free-crash-harness-v1"
RESULT_SCHEMA_VERSION = "provider-free-crash-harness-result-v2"
FIXED_TIME = "2026-09-01T00:00:00+00:00"
FINGERPRINT = "a" * 64
FIRST_SLICE_START_COMMIT = "c" * 40
LEDGER_ORDER = (
    "git_commit",
    "git_merge",
    "provider_start",
    "file_write",
    "queue_move",
    "internal",
    "ledger",
    "post_merge_hook",
)
BOUNDARY_ORDER = tuple(item.value for item in SideEffectBoundaryPhase)
RUNTIME_BOUNDARY_EFFECTS = {
    "slice_commit": "git_commit",
    "slice_provider": "provider_start",
    "implementation_handoff": "file_write",
    "queue_finalization": "queue_move",
    "slice_validation": "internal",
    "baseline_initialization": "ledger",
    "final_review_provider": "provider_start",
    "final_review_validation": "internal",
    "followup_task": "file_write",
    "post_merge_with_merge": "post_merge_hook",
    "post_merge_without_merge": "post_merge_hook",
    "post_merge_after_process_start": "post_merge_hook",
    "post_merge_after_process_end": "post_merge_hook",
}

HOOK_RUNTIME_BOUNDARIES = frozenset({
    "post_merge_with_merge", "post_merge_without_merge",
    "post_merge_after_process_start", "post_merge_after_process_end",
})
HOOK_PROCESS_BOUNDARIES = frozenset({
    "post_merge_after_process_start", "post_merge_after_process_end",
})


class CrashHarnessError(RuntimeError):
    """Raised when the harness cannot produce trustworthy S5 evidence."""


def _require_measured_first_slice_start_commit(state: WorkflowState) -> None:
    """Prove replay retained the measured boundary instead of inferring the base."""

    if state.branch_base == FIRST_SLICE_START_COMMIT:
        raise CrashHarnessError("measured first Slice start commit equals branch base")
    if state.slices[0].start_commit != FIRST_SLICE_START_COMMIT:
        raise CrashHarnessError(
            "replayed first Slice start commit differs from the measured boundary"
        )


class RecordBackedScriptedWorkflowDriver(ScriptedWorkflowDriver):
    """Run scripted boundaries while production owns every durable record fact."""

    def __init__(self, scenario: DryRunScenario, root: Path) -> None:
        super().__init__(scenario)
        self._record_driver = ProductionWorkflowDriver(
            repository_root=root,
            state_file=root / ".orchestrator" / f"state-{scenario.name}.json",
            agents={},
            config=OrchestratorConfig(repo_root=root),
            allowed_roots=(root,),
        )

    def bind_work_unit(self, state: WorkflowState) -> None:
        super().bind_work_unit(state)
        self._record_driver._artifact_bridge = ArtifactBridge(  # noqa: SLF001
            ArtifactStore(self._record_driver.root, state.run_id),
            now=lambda: FIXED_TIME,
        )
        self._record_driver.bind_work_unit(state)
        self.active_state = self._record_driver.active_state

    def checkpoint(self, state, history) -> None:  # type: ignore[no-untyped-def]
        self._record_driver.checkpoint(state, history)
        projected = self._record_driver.active_state
        if projected is None:  # pragma: no cover - production invariant
            raise CrashHarnessError("record-backed checkpoint lost its projection")
        super().checkpoint(projected, history)

    def authoritative_native_findings(self, state, findings):  # type: ignore[no-untyped-def]
        return self._record_driver.authoritative_native_findings(state, findings)

    def carry_forward_native_findings(self, state, findings):  # type: ignore[no-untyped-def]
        return self._record_driver.carry_forward_native_findings(state, findings)

    def recover_pending_native_codex(self, invocation, contract, history):  # type: ignore[no-untyped-def]  # allowlist:provider
        recovered = self._record_driver.recover_pending_native_codex(  # allowlist:provider
            invocation, contract, history
        )
        return recovered or super().recover_pending_native_codex(  # allowlist:provider
            invocation, contract, history
        )

    def recover_pending_native_reviewer(self, invocation, contract, history):  # type: ignore[no-untyped-def]
        recovered = self._record_driver.recover_pending_native_reviewer(
            invocation, contract, history
        )
        return recovered or super().recover_pending_native_reviewer(
            invocation, contract, history
        )

    def recover_pending_native_reviewer_before_policy(  # type: ignore[no-untyped-def]
        self, state, context, history
    ):
        recovered = self._record_driver.recover_pending_native_reviewer_before_policy(
            state, context, history
        )
        return recovered or super().recover_pending_native_reviewer_before_policy(
            state, context, history
        )

    def recover_pending_validation_attestation(  # type: ignore[no-untyped-def]
        self, fingerprint, expected_commands, attestation_id
    ):
        recovered = self._record_driver.recover_pending_validation_attestation(
            fingerprint, expected_commands, attestation_id
        )
        return recovered or super().recover_pending_validation_attestation(
            fingerprint, expected_commands, attestation_id
        )

    def persist_native_codex_contract(self, output, request_sequence, previous_findings) -> None:  # type: ignore[no-untyped-def]  # allowlist:provider
        state = self._record_driver.active_state
        if state is None:
            raise CrashHarnessError("scripted implementer result has no active record state")
        fingerprint = next(
            item.fingerprint
            for item in self.scenario.changes
            if item.work_unit_id == state.current_work_unit_id
            and item.request_sequence == state.current_work_unit.request_sequence
        )
        self._record_driver.persist_native_codex_contract(  # allowlist:provider
            output,
            request_sequence,
            previous_findings,
            recovery_fingerprint=fingerprint,
        )
        super().persist_native_codex_contract(  # allowlist:provider
            output, request_sequence, previous_findings
        )

    def persist_native_review_contract(  # type: ignore[no-untyped-def]
        self, output, fingerprint, round_number, request_sequence, previous_findings
    ) -> None:
        self._record_driver.persist_native_review_contract(
            output, fingerprint, round_number, request_sequence, previous_findings
        )
        super().persist_native_review_contract(
            output, fingerprint, round_number, request_sequence, previous_findings
        )

    def persist_review_packet(self, packet) -> None:  # type: ignore[no-untyped-def]
        self._record_driver.persist_review_packet(packet)
        super().persist_review_packet(packet)

    def persist_validation_request(self, request) -> None:  # type: ignore[no-untyped-def]
        self._record_driver.persist_validation_request(request)
        super().persist_validation_request(request)

    def persist_validation_attestation(self, attestation) -> None:  # type: ignore[no-untyped-def]
        self._record_driver.persist_validation_attestation(attestation)
        super().persist_validation_attestation(attestation)

    def persist_gate_decision(self, work_unit_id, decision) -> None:  # type: ignore[no-untyped-def]
        self._record_driver.persist_gate_decision(work_unit_id, decision)
        super().persist_gate_decision(work_unit_id, decision)

    def persist_gate_transition(self, state) -> None:  # type: ignore[no-untyped-def]
        self._record_driver.persist_gate_transition(state)
        super().persist_gate_transition(state)

    def persist_invocation_failure(self, payload) -> None:  # type: ignore[no-untyped-def]
        self._record_driver.persist_invocation_failure(payload)
        super().persist_invocation_failure(payload)

    def commit_slice(self, request) -> str:  # type: ignore[no-untyped-def]
        """Persist the scripted commit as a fully bound record-only transaction."""

        self._record_driver.assert_structured_decision_context()
        state = self._record_driver.active_state
        bridge = self._record_driver._artifact_bridge  # noqa: SLF001
        if state is None or bridge is None:
            raise CrashHarnessError("scripted commit has no record authority")
        chain = bridge.store.load_chain()
        attestation_record = next(
            (
                record
                for record in chain
                if isinstance(record.payload, ValidationAttestationPayload)
                and record.logical_id == request.attestation.attestation_id
                and record.fingerprint.sha256 == request.fingerprint
            ),
            None,
        )
        approvals = tuple(
            record
            for record in chain
            if isinstance(record.payload, ReviewPayload)
            and record.payload.verdict == "approved"
            and record.fingerprint.sha256 == request.fingerprint
        )
        current_review = next(
            (
                record
                for record in reversed(approvals)
                if record.payload.work_unit_id == str(state.current_work_unit_id)
            ),
            None,
        )
        if (
            attestation_record is None
            or current_review is None
            or attestation_record.payload
            != attestation_payload(
                request.attestation,
                attestation_record.payload.content_record_id,
            )
            or not review_payload_matches_complete_result(
                current_review.payload, request.claude_review  # allowlist:provider
            )
        ):
            raise CrashHarnessError("scripted commit lacks its record-bound approval")
        if self._commit_index >= len(self.scenario.commits):  # noqa: SLF001
            raise CrashHarnessError("scripted commit has no declared physical result")
        declared_commit = self.scenario.commits[self._commit_index]  # noqa: SLF001
        if (declared_commit.slice_id, declared_commit.fingerprint) != (
            request.slice_id,
            request.fingerprint,
        ):
            raise CrashHarnessError("scripted commit declaration differs from authorization")
        commit_ref = declared_commit.commit_ref
        prior = state.current_slice.start_commit or state.branch_base
        operation = (
            "slice_commit",
            str(request.slice_id),
            prior,
            request.fingerprint[:40],
            request.fingerprint,
            hashlib.sha256(
                f"scripted slice {request.slice_id}".encode("utf-8")
            ).hexdigest(),
        )
        spec = SideEffectSpec(
            "git_commit",
            str(state.current_work_unit_id),
            operation,
            request.fingerprint,
        )
        executor = self._record_driver._side_effect_executor(bridge)  # noqa: SLF001
        should_commit = executor.begin(
            spec,
            reconcile=lambda: Reconciliation(ReconciliationOutcome.NOT_OCCURRED),
        )
        if not should_commit:
            raise CrashHarnessError("scripted commit was already completed before its call")
        performed_commit = super().commit_slice(request)
        if performed_commit != commit_ref:
            raise CrashHarnessError("scripted commit returned a foreign result")
        executor.complete(spec, commit_ref)
        self._record_driver._mark_completed_side_effect(spec.effect_key)  # noqa: SLF001
        bridge.append(
            BindingPayload(
                binding_kind="commit",
                target=commit_ref,
                attestation_id=attestation_record.record_id,
                approval_ids=tuple(record.record_id for record in approvals),
            ),
            logical_id=f"commit-{request.slice_id}-{commit_ref[:12]}",
            idempotency_key=f"commit:{request.slice_id}:{request.fingerprint}",
            fingerprint_sha256=request.fingerprint,
        )
        return commit_ref


@dataclass(frozen=True, slots=True)
class CrashHarnessManifest:
    scenario_version: str
    effect_classes: tuple[str, ...]
    boundary_matrix: Mapping[str, tuple[str, ...]]
    runtime_boundaries: Mapping[str, str]
    journeys: tuple[str, ...]
    retry_kinds: tuple[str, ...]

    @classmethod
    def load(cls, path: Path) -> "CrashHarnessManifest":
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CrashHarnessError(f"cannot load crash harness manifest: {exc}") from exc
        if not isinstance(document, dict) or set(document) != {
            "schema_version",
            "scenario_version",
            "effect_classes",
            "boundary_matrix",
            "runtime_boundaries",
            "journeys",
            "retry_kinds",
        }:
            raise CrashHarnessError("crash harness manifest has unknown or missing fields")
        if document["schema_version"] != HARNESS_SCHEMA_VERSION:
            raise CrashHarnessError("crash harness schema version is unsupported")
        values = {
            key: tuple(document[key])
            for key in ("effect_classes", "journeys", "retry_kinds")
            if isinstance(document[key], list)
            and all(isinstance(item, str) and item for item in document[key])
        }
        matrix_raw = document["boundary_matrix"]
        runtime_raw = document["runtime_boundaries"]
        if (
            len(values) != 3
            or not isinstance(matrix_raw, dict)
            or not isinstance(runtime_raw, dict)
            or not all(
                isinstance(boundary, str)
                and boundary
                and isinstance(effect_class, str)
                and effect_class
                for boundary, effect_class in runtime_raw.items()
            )
        ):
            raise CrashHarnessError("crash harness manifest lists must contain strings")
        version = document["scenario_version"]
        if not isinstance(version, str) or not version.strip():
            raise CrashHarnessError("crash harness scenario_version is invalid")
        boundary_matrix = {
            effect_class: tuple(boundaries)
            for effect_class, boundaries in matrix_raw.items()
            if isinstance(effect_class, str)
            and isinstance(boundaries, list)
            and all(isinstance(item, str) and item for item in boundaries)
        }
        if len(boundary_matrix) != len(matrix_raw):
            raise CrashHarnessError("crash harness boundary matrix is invalid")
        manifest = cls(
            version,
            boundary_matrix=boundary_matrix,
            runtime_boundaries=dict(runtime_raw),
            **values,
        )
        if manifest.effect_classes != LEDGER_ORDER or set(manifest.effect_classes) != set(
            SIDE_EFFECT_CLASSES
        ):
            raise CrashHarnessError("manifest must cover every ledger side-effect class")
        external_boundaries = BOUNDARY_ORDER
        record_only_boundaries = tuple(
            item
            for item in BOUNDARY_ORDER
            if item not in {"before_effect", "after_effect"}
        )
        expected_matrix = {
            effect_class: (
                record_only_boundaries
                if effect_class in {"internal", "ledger"}
                else external_boundaries
            )
            for effect_class in LEDGER_ORDER
        }
        if dict(manifest.boundary_matrix) != expected_matrix:
            raise CrashHarnessError(
                "manifest boundary matrix differs from the production ledger topology"
            )
        if dict(manifest.runtime_boundaries) != RUNTIME_BOUNDARY_EFFECTS:
            raise CrashHarnessError(
                "manifest runtime boundaries differ from the target run topology"
            )
        if any(
            manifest.boundary_matrix[name] != BOUNDARY_ORDER
            for name in ("post_merge_hook",)
        ):
            raise CrashHarnessError("post-merge hook boundary inventory is incomplete")
        if manifest.journeys != (
            "plan-implement-final-review",
            "multi-slice-correction-observation-resume",
            "final-review-followup-document",
        ):
            raise CrashHarnessError("manifest journey inventory is incomplete")
        if manifest.retry_kinds != ("quota", "network", "process"):
            raise CrashHarnessError("manifest retry inventory is incomplete")
        return manifest


@dataclass(slots=True)
class CrashInjector:
    effect_class: str
    phase: SideEffectBoundaryPhase
    remaining: int = 1
    crash_count: int = 0

    def __call__(self, boundary: SideEffectBoundary) -> None:
        if (
            boundary.effect_class == self.effect_class
            and boundary.phase is self.phase
            and self.remaining > 0
        ):
            self.remaining -= 1
            self.crash_count += 1
            raise InjectedCrash(
                f"injected crash at {boundary.effect_class}:{boundary.phase.value}"
            )


def _production_state(root: Path, run_id: str) -> WorkflowState:
    """Build the fixed state consumed by the real baseline writer."""

    task = root / "inbox" / "s5-harness.md"
    task.parent.mkdir(parents=True, exist_ok=True)
    task.write_text("provider-free S5 crash harness", encoding="utf-8")
    return init_workflow_state(
        run_id=run_id,
        task_file="inbox/s5-harness.md",
        branch="feature/state-authority-consolidation",
        branch_base="b" * 40,
        first_slice_start_commit=FIRST_SLICE_START_COMMIT,
        slice_count=1,
        task_digest=FINGERPRINT,
        task_scope_patterns=("src/harness.py",),
        target_branch="feature/state-authority-consolidation",
        protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
        timestamp=FIXED_TIME,
    ).complete_current_work_unit(updated_at=FIXED_TIME).start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,  # allowlist:provider -- persisted step
        updated_at=FIXED_TIME,
    ).bind_current_slice_git_boundary(
        start_commit=FIRST_SLICE_START_COMMIT,
        scope_paths=("src/harness.py",),
        start_fingerprint="c" * 64,
        updated_at=FIXED_TIME,
    )


def _production_driver(
    root: Path, boundary_observer: CrashInjector | None = None
) -> ProductionWorkflowDriver:
    """Construct the real driver with only its provider boundary disabled."""

    driver = ProductionWorkflowDriver(
        repository_root=root,
        state_file=root / ".orchestrator" / "state.json",
        agents={},
        config=OrchestratorConfig(repo_root=root),
        allowed_roots=(root,),
        side_effect_boundary_observer=boundary_observer,
    )
    return driver


def _production_baseline(
    root: Path,
    run_id: str,
    *,
    boundary_observer: CrashInjector | None = None,
) -> ArtifactBridge:
    """Create the same complete resumable baseline as the production driver."""

    state = _production_state(root, run_id)
    driver = _production_driver(root, boundary_observer)
    driver._artifact_bridge = ArtifactBridge(  # noqa: SLF001 - fixed harness clock
        ArtifactStore(root, run_id), now=lambda: FIXED_TIME
    )
    driver.bind_work_unit(state)
    resolution = resolve_resume_state(root, run_id)
    _require_measured_first_slice_start_commit(resolution.state)
    if resolution.state.current_step is not WorkflowStep.CODEX_IMPLEMENTATION:  # allowlist:provider
        raise CrashHarnessError("production baseline projected a foreign cursor")
    return ArtifactBridge(
        ArtifactStore(root, run_id), now=lambda: FIXED_TIME
    )


def _effect_spec(
    effect_class: str, runtime_boundary: str | None = None
) -> tuple[SideEffectSpec, str]:
    digest = sha256_bytes(b"s5-harness-content")
    if effect_class == "git_commit":
        operation = (
            "slice_commit",
            "1",
            "1" * 40,
            "2" * 40,
            digest,
            "d" * 64,
        )
        result = "3" * 40
        work_unit_id = "2"
    elif effect_class == "provider_start":
        final_review = runtime_boundary == "final_review_provider"
        operation = (
            (
                "claude"  # allowlist:provider -- persisted provider-start vocabulary
                if final_review
                else "codex"  # allowlist:provider -- persisted provider-start vocabulary
            ),
            "final_review" if final_review else "implementation",
            digest,
            "d" * 64,
            "attempt",
            "1",
            (
                ".orchestrator/provider-results/final-review-harness.json"
                if final_review
                else ".orchestrator/provider-results/harness.json"
            ),
        )
        result = sha256_bytes(b"s5-provider-response")
        work_unit_id = "2"
    elif effect_class == "file_write":
        operation = (
            (
                "inbox/s5-followup.md"
                if runtime_boundary == "followup_task"
                else "inbox/s5-implement.md"
            ),
            digest,
        )
        result = digest
        work_unit_id = "2"
    elif effect_class == "queue_move":
        operation = ("inbox/harness.md", "outbox/harness.md", digest)
        result = digest
        work_unit_id = "queue"
    elif effect_class == "internal":
        operation = (
            (
                "final-review-validation-attestation"
                if runtime_boundary == "final_review_validation"
                else "validation-attestation"
            ),
        )
        result = "completed"
        work_unit_id = "2"
    elif effect_class == "ledger":
        operation = ("structured-v2-side-effect-ledger",)
        result = "initialized"
        work_unit_id = "run"
    else:  # pragma: no cover - manifest validation owns the closed inventory
        raise CrashHarnessError(f"unknown side-effect class {effect_class!r}")
    return SideEffectSpec(effect_class, work_unit_id, operation, FINGERPRINT), result


def _provider_context(
    root: Path, bridge: ArtifactBridge, spec: SideEffectSpec
) -> tuple[ProductionWorkflowDriver, object, Path]:
    measurement = ProviderInputMeasurement(
        provider=spec.operation[0],
        role=spec.operation[0],
        operation=spec.operation[1],
        binding_fingerprint=spec.operation[3],
        input_digest=spec.operation[2],
        policy_digest="f" * 64,
        components=(ProviderInputComponentSize("stdin_prompt", 3, 3),),
        total_chars=3,
        total_bytes=3,
        safety_limit_chars=10,
        safety_limit_bytes=10,
        technical_limit_chars=None,
        technical_limit_bytes=None,
        technical_limit_source=None,
        effective_limit_chars=10,
        effective_limit_bytes=10,
        allowed=True,
        violated_dimensions=(),
        char_overage=0,
        byte_overage=0,
        largest_component="stdin_prompt",
    )
    measurement_record = bridge.append(
        provider_input_measurement_payload(
            measurement,
            work_unit_id=spec.work_unit_id,
            transition_fingerprint=FINGERPRINT,
            relevant_record_head="9" * 64,
        ),
        logical_id="provider-input-harness",
        idempotency_key="provider-input:harness",
        fingerprint_sha256=FINGERPRINT,
    )
    driver = _production_driver(root)
    driver._artifact_bridge = bridge
    response = root.joinpath(*Path(spec.operation[6]).parts)
    return driver, measurement_record, response


def _physical_paths(
    root: Path,
    effect_class: str,
    runtime_boundary: str,
    spec: SideEffectSpec,
) -> tuple[Path, Path]:
    physical = root / "physical"
    physical.mkdir(parents=True, exist_ok=True)
    counter = physical / f"{runtime_boundary}.count"
    if effect_class == "file_write":
        target = root.joinpath(*Path(spec.operation[0]).parts)
    elif effect_class == "queue_move":
        target = root / "outbox" / "harness.md"
    else:
        target = physical / f"{effect_class}.result"
    target.parent.mkdir(parents=True, exist_ok=True)
    return target, counter


def _physical_count(path: Path) -> int:
    return int(path.read_text(encoding="ascii")) if path.exists() else 0


def _run_baseline_stop_case(
    root: Path,
    phase: SideEffectBoundaryPhase,
    *,
    requested_crashes: int,
    runtime_boundary: str = "baseline_initialization",
) -> Mapping[str, object]:
    """Drive the real baseline writer until it converges or names the stop."""

    run_id = "s5-ledger"
    case_root = root / f"{run_id}-{phase.value}-{requested_crashes}"
    case_root.mkdir(parents=True, exist_ok=False)
    injector = CrashInjector("ledger", phase, requested_crashes)
    attempts = 0
    production_resume_attempts = 0
    production_resume_successes = 0
    diagnostic_code: str | None = None
    diagnostic = ""
    while attempts < requested_crashes + 4:
        attempts += 1
        if injector.crash_count:
            production_resume_attempts += 1
        state = _production_state(case_root, run_id)
        driver = _production_driver(case_root, injector)
        driver._artifact_bridge = ArtifactBridge(  # noqa: SLF001 - fixed clock
            ArtifactStore(case_root, run_id), now=lambda: FIXED_TIME
        )
        try:
            driver.bind_work_unit(state)
        except InjectedCrash:
            continue
        except ArtifactResumeError as exc:
            diagnostic_code = exc.code.value
            diagnostic = str(exc)
            break
        else:
            if injector.crash_count:
                production_resume_successes += 1
            resolution = resolve_resume_state(case_root, run_id)
            _require_measured_first_slice_start_commit(resolution.state)
            break
    else:
        raise CrashHarnessError("baseline crash case exceeded its retry bound")
    if injector.remaining != 0:
        raise CrashHarnessError(
            f"declared ledger boundary {phase.value!r} was never reached"
        )

    chain = ArtifactStore(case_root, run_id).load_chain()
    replayed = replay_artifacts(chain, run_id)
    completed = tuple(
        item
        for item in replayed.side_effects
        if item.effect_class == "ledger" and item.result is not None
    )
    stopped = diagnostic_code is not None
    if not stopped and len(completed) != 1:
        raise CrashHarnessError("resumed baseline has no initialized ledger")
    return {
        "runtime_boundary": runtime_boundary,
        "effect_class": "ledger",
        "phase": phase.value,
        "requested_crashes": requested_crashes,
        "observed_crashes": injector.crash_count,
        "resume_attempts": attempts,
        "production_resume_attempts": production_resume_attempts,
        "production_resume_successes": production_resume_successes,
        "production_resume_entry": "ProductionWorkflowDriver.bind_work_unit",
        "production_boundary_entry": "ProductionWorkflowDriver.bind_work_unit",
        "physical_execution_count": 0,
        "result_completion_count": len(completed),
        "record_count": len(chain),
        "record_head": chain[-1].record_id,
        "chain_semantic_sha256": hashlib.sha256(
            canonical_json([record.to_dict() for record in chain])
        ).hexdigest(),
        "side_effect_projection_sha256": hashlib.sha256(
            canonical_json(
                [
                    {
                        "effect_class": item.effect_class,
                        "effect_key": item.effect_key,
                        "operation": list(item.operation),
                        "result": item.result,
                        "work_unit_id": item.work_unit_id,
                    }
                    for item in replayed.side_effects
                ]
            )
        ).hexdigest(),
        "resume_diagnostic_code": diagnostic_code,
        "resume_diagnostic_sha256": (
            None
            if not diagnostic
            else hashlib.sha256(diagnostic.encode("utf-8")).hexdigest()
        ),
        "stop_condition_id": (
            "STRUCTURED-BASELINE-NONRESUMABLE" if stopped else None
        ),
        "stop_scope": (
            "run-identity-through-workflow-status-gate-ledger-prefix"
            if stopped
            else None
        ),
        "end_state": "stop_condition" if stopped else "converged",
    }


def _run_crash_case(
    root: Path,
    runtime_boundary: str,
    effect_class: str,
    phase: SideEffectBoundaryPhase,
    *,
    requested_crashes: int = 1,
) -> Mapping[str, object]:
    if effect_class == "ledger":
        return _run_baseline_stop_case(
            root,
            phase,
            requested_crashes=requested_crashes,
            runtime_boundary=runtime_boundary,
        )

    # The run identity stays constant across injection points.  Separate
    # physical directories model separate executions while allowing their
    # terminal record heads to be compared byte-for-byte.
    run_id = f"s5-{runtime_boundary}"
    case_root = root / f"{run_id}-{phase.value}-{requested_crashes}"
    case_root.mkdir(parents=True, exist_ok=False)
    _production_baseline(case_root, run_id)
    spec, expected_result = _effect_spec(effect_class, runtime_boundary)
    marker, counter = _physical_paths(
        case_root, effect_class, runtime_boundary, spec
    )
    source = (
        case_root / "inbox" / "harness.md"
        if effect_class == "queue_move"
        else marker.with_suffix(".source")
    )
    if effect_class == "queue_move":
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"s5-harness-content")
    injector = CrashInjector(effect_class, phase, requested_crashes)
    production_resume_attempts = 0
    production_resume_successes = 0

    def production_resume():
        nonlocal production_resume_attempts, production_resume_successes
        production_resume_attempts += 1
        resolution = resolve_resume_state(case_root, run_id)
        _require_measured_first_slice_start_commit(resolution.state)
        production_resume_successes += 1
        return resolution

    def reconcile_external() -> Reconciliation:
        if effect_class == "file_write":
            return reconcile_file_write(marker, expected_result)
        if effect_class == "queue_move":
            return reconcile_queue_move(source, marker, expected_result)
        if effect_class == "git_commit":
            if not marker.exists():
                current_head, current_parent, current_tree = "1" * 40, None, None
            else:
                physical_state = json.loads(marker.read_text(encoding="ascii"))
                current_head = physical_state["head"]
                current_parent = physical_state["parent"]
                current_tree = physical_state["tree"]
            return reconcile_git_commit(
                prior_head="1" * 40,
                current_head=current_head,
                current_parent=current_parent,
                expected_tree="2" * 40,
                current_tree=current_tree,
            )
        raise CrashHarnessError("record-only effects do not use external reconciliation")

    def perform_external() -> tuple[str, str]:
        if marker.exists() or (effect_class == "queue_move" and not source.exists()):
            raise CrashHarnessError(f"duplicate physical {effect_class} execution")
        count = _physical_count(counter) + 1
        counter.write_text(str(count), encoding="ascii")
        if effect_class == "file_write":
            marker.write_bytes(b"s5-harness-content")
        elif effect_class == "queue_move":
            source.replace(marker)
        elif effect_class == "git_commit":
            marker.write_text(
                json.dumps(
                    {"head": expected_result, "parent": "1" * 40, "tree": "2" * 40},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                encoding="ascii",
            )
        return expected_result, expected_result

    def run_once(
        active_bridge: ArtifactBridge,
        resume_state: object,
    ) -> str:
        executor = SideEffectExecutor(active_bridge, injector)
        if effect_class == "internal":
            existing = active_bridge.side_effect_result(
                effect_class=spec.effect_class,
                work_unit_id=spec.work_unit_id,
                operation=spec.operation,
            )
            if existing is not None:
                return existing
            if not isinstance(resume_state, WorkflowState):
                raise CrashHarnessError("internal retry has no projected workflow state")
            driver = _production_driver(case_root, injector)
            driver._artifact_bridge = active_bridge
            driver.bind_work_unit(
                resume_state.mark_side_effect_completed(
                    spec.operation[0], updated_at=FIXED_TIME
                )
            )
            result = active_bridge.side_effect_result(
                effect_class=spec.effect_class,
                work_unit_id=spec.work_unit_id,
                operation=spec.operation,
            )
            if result is None:
                raise CrashHarnessError("record-only reconciliation did not complete")
            return result
        if effect_class == "provider_start":
            driver, measurement_record, response = _provider_context(
                case_root, active_bridge, spec
            )
            may_start = executor.begin(
                spec,
                reconcile=lambda: driver._reconcile_provider_effect(spec, response),
            )
            if may_start:
                if response.exists():
                    raise CrashHarnessError("duplicate physical provider execution")
                counter.write_text(str(_physical_count(counter) + 1), encoding="ascii")
                started = active_bridge.start_provider_attempt(
                    measurement_record=measurement_record,
                    binding_fingerprint=spec.operation[3],
                    work_unit_id=spec.work_unit_id,
                    operation_instance=spec.operation[4],
                    model="provider-free",
                    effort="medium",
                )
                response.parent.mkdir(parents=True, exist_ok=True)
                response.write_bytes(b"s5-provider-response")
                active_bridge.finish_provider_attempt(
                    started,
                    duration_seconds=0.0,
                    failure_kind=None,
                    usage=None,
                )
                executor.complete(spec, expected_result)
            result = active_bridge.side_effect_result(
                effect_class=spec.effect_class,
                work_unit_id=spec.work_unit_id,
                operation=spec.operation,
            )
            if result is None:
                raise CrashHarnessError("provider split path did not complete")
            return result
        return str(
            executor.execute(
                spec,
                reconcile=reconcile_external,
                perform=perform_external,
            )
        )

    attempts = 0
    while attempts < requested_crashes + 4:
        attempts += 1
        # Rebuild both store and bridge on every retry.  Nothing process-local
        # participates in reconciliation or idempotency.
        bridge = ArtifactBridge(ArtifactStore(case_root, run_id), now=lambda: FIXED_TIME)
        try:
            resolution = production_resume()
            resume_state = resolution.state
            result = run_once(bridge, resolution.state)
        except InjectedCrash:
            continue
        except ArtifactResumeError as exc:
            raise CrashHarnessError(
                f"production resume rejected {effect_class}:{phase.value}"
            ) from exc
        if result != expected_result:
            raise CrashHarnessError("resumed side effect returned a foreign result")
        break
    else:
        raise CrashHarnessError("crash case did not converge within its bound")
    if injector.remaining != 0:
        raise CrashHarnessError(
            f"declared boundary {effect_class}:{phase.value} was never reached"
        )

    # A second ordinary resume must be a pure ledger read/reconciliation no-op.
    resolution = production_resume()
    result = run_once(
        ArtifactBridge(ArtifactStore(case_root, run_id), now=lambda: FIXED_TIME),
        resolution.state,
    )
    expected_physical_count = 0 if effect_class in {"internal", "ledger"} else 1
    if result != expected_result or _physical_count(counter) != expected_physical_count:
        raise CrashHarnessError(f"side effect {effect_class!r} executed more than once")
    chain = ArtifactStore(case_root, run_id).load_chain()
    replay = replay_artifacts(chain, run_id)
    completed = tuple(
        item
        for item in replay.side_effects
        if item.effect_class == effect_class and item.effect_key == spec.effect_key
    )
    if len(completed) != 1 or completed[0].result != expected_result:
        raise CrashHarnessError("crash case has no unique completed ledger effect")
    production_resume()
    return {
        "runtime_boundary": runtime_boundary,
        "effect_class": effect_class,
        "phase": phase.value,
        "requested_crashes": requested_crashes,
        "observed_crashes": injector.crash_count,
        "resume_attempts": attempts,
        "production_resume_attempts": production_resume_attempts,
        "production_resume_successes": production_resume_successes,
        "production_resume_entry": "resolve_resume_state",
        "production_boundary_entry": (
            "ProductionWorkflowDriver.bind_work_unit"
            if effect_class == "internal"
            else "SideEffectExecutor.begin"
            if effect_class == "provider_start"
            else "SideEffectExecutor.execute"
        ),
        "physical_execution_count": expected_physical_count,
        "result_completion_count": 1,
        "record_count": len(chain),
        "record_head": chain[-1].record_id,
        "chain_semantic_sha256": hashlib.sha256(
            canonical_json([record.to_dict() for record in chain])
        ).hexdigest(),
        "side_effect_projection_sha256": hashlib.sha256(
            canonical_json(
                [
                    {
                        "effect_class": item.effect_class,
                        "effect_key": item.effect_key,
                        "operation": list(item.operation),
                        "result": item.result,
                        "work_unit_id": item.work_unit_id,
                    }
                    for item in replay.side_effects
                ]
            )
        ).hexdigest(),
        "end_state": "converged",
    }


def _run_post_merge_case(
    root: Path, runtime_boundary: str, phase: str,
) -> Mapping[str, object]:
    """Crash a real completion run and resume from its durable record chain."""

    # These fixture builders create a small real Git repository and native,
    # provider-free answers. They are included in the measured source set.
    tests_root = Path(__file__).resolve().parents[1] / "tests"
    if str(tests_root) not in sys.path:
        sys.path.insert(0, str(tests_root))
    from test_orchestrator_runtime import (  # noqa: PLC0415
        _args, _native_final_review_output, _native_implementation_output,
        _native_plan_output, _native_review_approval, _repository, _write_task,
    )
    from inbox_watcher import WatchTaskIdentity, save_watch_identity
    from artifact_models import SideEffectPayload
    import orchestrator
    import workflow_completion
    from git_service import GitTransactionError

    merge = runtime_boundary != "post_merge_without_merge"
    label = f"{runtime_boundary}-{phase}"
    case_parent = root / label
    case_parent.mkdir(parents=True, exist_ok=False)
    branch = "feature/post-merge-crash-proof"
    repository = _repository(case_parent, branch)
    (repository / ".git/info/exclude").write_text("inbox/\noutbox/\n", encoding="utf-8")
    task = repository / "inbox/task.md"
    task.parent.mkdir()
    _write_task(task, branch, "src/one.py")
    task_bytes = task.read_bytes()
    task_digest = hashlib.sha256(task_bytes).hexdigest()
    audit_path = f"docs/internal/task-review-{task_digest[:8]}.md"
    run_id = "watch-post-merge-crash-proof"
    save_watch_identity(task, WatchTaskIdentity(
        run_id, task_digest, True, "structured-v2", 2,
    ))
    hook = repository / ".git/hooks/post-merge"
    counter = case_parent / "hook-count"
    started = case_parent / "hook-started"
    release = case_parent / "hook-release"
    ended = case_parent / "hook-ended"
    hook_pid = case_parent / "hook-pid"
    if phase != "after_process_start":
        release.write_text("go", encoding="ascii")
    hook.write_text(
        "#!/bin/sh\n"
        f"printf x >> '{counter}'\n"
        f"touch '{started}'\n"
        f"while [ ! -e '{release}' ]; do sleep 0.02; done\n"
        f"touch '{ended}'\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)

    def args_for_run():
        args = _args(repository, task)
        args.watch_run_id = run_id
        if not merge:
            from dataclasses import replace as dc_replace
            args.repo_config = dc_replace(
                args.repo_config,
                workflow=dc_replace(
                    args.repo_config.workflow, merge_completed_branch=False,
                ),
            )
        return args

    failure_file = case_parent / "worker-error"
    child = os.fork()
    if child == 0:
        try:
            os.chdir(repository)
            original_init = ProductionWorkflowDriver.__init__

            def injected_init(driver, *args, **kwargs):
                original_init(driver, *args, **kwargs)

                def crash_at_boundary(boundary):
                    if (runtime_boundary not in HOOK_PROCESS_BOUNDARIES
                        and boundary.effect_class == "post_merge_hook"
                        and boundary.phase.value == phase):
                        os._exit(77)

                driver._side_effect_boundary_observer = crash_at_boundary

            ProductionWorkflowDriver.__init__ = injected_init

            def codex(_driver, invocation):  # allowlist:provider -- scripted implementer
                target = repository / "src/one.py"
                target.parent.mkdir(parents=True, exist_ok=True)
                if invocation.step.value == "codex_plan":  # allowlist:provider -- persisted step
                    target.write_text("value = 0\n", encoding="utf-8")
                    return _native_plan_output(
                        invocation, summary="add value",
                        scope_paths=(audit_path, "src/one.py"),
                    )
                target.write_text("value = 1\n", encoding="utf-8")
                return _native_implementation_output(invocation)

            def review(driver, invocation):
                if invocation.step.value == "claude_final_review":  # allowlist:provider -- persisted step
                    return _native_final_review_output(
                        driver, invocation, finding_id=None,
                    )
                return _native_review_approval(invocation)

            ProductionWorkflowDriver.invoke_codex = codex  # allowlist:provider -- scripted adapter
            ProductionWorkflowDriver.invoke_reviewer = review
            if phase == "after_process_start":
                real_popen = workflow_completion.subprocess.Popen

                def started_popen(*args, **kwargs):
                    process = real_popen(*args, **kwargs)
                    if args and tuple(args[0]) == (str(hook), "0"):
                        hook_pid.write_text(str(process.pid), encoding="ascii")
                        import time
                        for _ in range(500):
                            if started.exists():
                                os._exit(77)
                            time.sleep(0.01)
                        raise CrashHarnessError("hook process did not signal start")
                    return process

                workflow_completion.subprocess.Popen = started_popen
            elif phase == "after_process_end":
                real_run_hook = workflow_completion._run_hook

                def finished_hook(*args, **kwargs):
                    real_run_hook(*args, **kwargs)
                    if not ended.exists():
                        raise CrashHarnessError("hook process did not signal end")
                    os._exit(77)

                workflow_completion._run_hook = finished_hook
            orchestrator.run_production_workflow(task, args_for_run())
            failure_file.write_text("worker completed without declared crash", encoding="utf-8")
        except BaseException as exc:
            failure_file.write_text(repr(exc), encoding="utf-8")
        os._exit(2)

    _, worker_status = os.waitpid(child, 0)
    if os.waitstatus_to_exitcode(worker_status) != 77:
        detail = failure_file.read_text(encoding="utf-8") if failure_file.exists() else "no diagnostic"
        raise CrashHarnessError(f"hook worker missed {label}: {detail}")
    if phase == "after_process_start":
        if not started.exists():
            raise CrashHarnessError("hook start boundary lacks physical signal")
        release.write_text("go", encoding="ascii")
        import time
        for _ in range(500):
            if ended.exists():
                break
            time.sleep(0.01)
        else:
            raise CrashHarnessError("orphan hook did not finish after release")
        pid = int(hook_pid.read_text(encoding="ascii"))
        for _ in range(500):
            process_status = Path(f"/proc/{pid}/stat")
            try:
                status = process_status.read_text().split()[2]
            except FileNotFoundError:
                break
            if status == "Z":
                break
            time.sleep(0.01)
        else:
            raise CrashHarnessError("orphan hook remained running after release")

    store = ArtifactStore(repository, run_id)
    before = replay_artifacts(store.load_chain(), run_id)
    open_hooks = [effect for effect in before.side_effects
                  if effect.effect_class == "post_merge_hook" and effect.result is None]
    known_hooks = [effect for effect in before.side_effects
                   if effect.effect_class == "post_merge_hook" and effect.result is not None]
    if len(open_hooks) + len(known_hooks) != (0 if phase == "before_intent" else 1):
        raise CrashHarnessError("hook crash produced an unexpected intent set")
    if phase != "before_intent" and not open_hooks and phase != "after_result":
        raise CrashHarnessError("hook result appeared before its declared boundary")
    commit = subprocess.run(
        ("git", "rev-parse", "master" if merge else branch),
        cwd=repository, check=True, capture_output=True, text=True,
    ).stdout.strip()
    if any(effect.operation[1] != commit for effect in (*open_hooks, *known_hooks)):
        raise CrashHarnessError("hook intent is not bound to the confirmed commit")
    if task.read_bytes() != task_bytes:
        raise CrashHarnessError("task changed before hook resume")
    if (repository / "outbox/done").exists():
        raise CrashHarnessError("watch queue moved before hook result")

    previous_cwd = Path.cwd()
    os.chdir(repository)
    ordinary_rejected = False
    acknowledgment = False
    production_resume_attempts = 0
    production_resume_successes = 0
    original_run_production_workflow = orchestrator.run_production_workflow

    def counted_production_resume(*args, **kwargs):
        nonlocal production_resume_attempts, production_resume_successes
        production_resume_attempts += 1
        result = original_run_production_workflow(*args, **kwargs)
        if result.workflow_completed:
            production_resume_successes += 1
        return result

    orchestrator.run_production_workflow = counted_production_resume
    try:
        args = args_for_run()
        args.resume = True
        try:
            first = orchestrator.run_production_workflow(task, args)
            if not first.workflow_completed:
                raise CrashHarnessError("ordinary hook resume did not finish")
        except Exception as exc:
            cause = exc
            while cause is not None and not isinstance(
                cause, SideEffectReconciliationError
            ):
                cause = cause.__cause__
            if cause is None:
                raise
            ordinary_rejected = True
        expected_rejection = merge and phase not in {"before_intent", "after_result"}
        if ordinary_rejected != expected_rejection:
            raise CrashHarnessError("ordinary resume disagreed with hook uncertainty")
        if ordinary_rejected:
            if not merge:
                raise CrashHarnessError("logical merge-disabled skip was treated as uncertain")
            pre_ack = replay_artifacts(store.load_chain(), run_id)
            if any(effect.effect_class == "post_merge_hook" and effect.result is not None
                   for effect in pre_ack.side_effects):
                raise CrashHarnessError("uncertain hook outcome gained a terminal result")
            if (repository / "outbox/done").exists():
                raise CrashHarnessError("watch queue moved before hook acknowledgment")
            record_count = len(store.load_chain())
            args.acknowledge_post_merge = "0" * 40
            args.post_merge_rationale = "Physischer Ausgang geprüft"
            try:
                orchestrator.run_production_workflow(task, args)
            except GitTransactionError:
                pass
            else:
                raise CrashHarnessError("foreign commit acknowledged an open hook")
            if len(store.load_chain()) != record_count:
                raise CrashHarnessError("foreign commit wrote an acknowledgment")
            task.write_text("changed task\n", encoding="utf-8")
            try:
                orchestrator.run_production_workflow(task, args)
            except Exception as exc:
                if not any(word in str(exc).lower() for word in (
                    "digest", "identity", "differ", "changed",
                )):
                    raise CrashHarnessError("changed task failed for an unrelated reason") from exc
            else:
                raise CrashHarnessError("changed task acknowledged an open hook")
            if len(store.load_chain()) != record_count:
                raise CrashHarnessError("changed task wrote an acknowledgment")
            task.write_bytes(task_bytes)
            args.acknowledge_post_merge = commit
            resumed = orchestrator.run_production_workflow(task, args)
            acknowledgment = True
            if not resumed.workflow_completed:
                raise CrashHarnessError("acknowledged hook resume did not finish")
        args.acknowledge_post_merge = None
        again = orchestrator.run_production_workflow(task, args)
        if not again.workflow_completed:
            raise CrashHarnessError("completed hook run did not resume idempotently")
        args.resume_explicit = True
        args.task_file_explicit = True
        args.inbox_dir = repository / "inbox"
        args.outbox_dir = repository / "outbox"
        if orchestrator.run_pipeline(task, args) != 0:
            raise CrashHarnessError("watch queue did not finalize")
    finally:
        orchestrator.run_production_workflow = original_run_production_workflow
        os.chdir(previous_cwd)

    chain = store.load_chain()
    replay = replay_artifacts(chain, run_id)
    hooks = [effect for effect in replay.side_effects
             if effect.effect_class == "post_merge_hook"]
    results = [record for record in chain
               if isinstance(record.payload, SideEffectPayload)
               and record.payload.effect_class == "post_merge_hook"
               and record.payload.phase == "result"]
    if len(hooks) != 1 or len(results) != 1 or hooks[0].result is None:
        raise CrashHarnessError("hook completion lacks one durable result")
    if results[0].payload.effect_key != hooks[0].effect_key:
        raise CrashHarnessError("hook result belongs to a foreign intent")
    outcome = json.loads(hooks[0].result)
    expected_status = (
        "skipped" if not merge else
        "acknowledged_unknown" if ordinary_rejected else "success"
    )
    if outcome["status"] != expected_status:
        raise CrashHarnessError("hook result has the wrong terminal status")
    if not merge and outcome.get("reason") != "merge_disabled":
        raise CrashHarnessError("merge-disabled hook has no skip record")
    expected_calls = (0 if not merge or phase in {"after_intent", "before_effect"}
                      else 1)
    calls = counter.read_text(encoding="ascii") if counter.exists() else ""
    if calls != "x" * expected_calls:
        raise CrashHarnessError("physical hook invocation count differs from boundary")
    if merge and calls == "" and not acknowledgment:
        raise CrashHarnessError("hook finished without a call or acknowledgment")
    queue_done = list((repository / "outbox/done").glob("*.md"))
    if len(queue_done) != 1 or queue_done[0].read_bytes() != task_bytes or task.exists():
        raise CrashHarnessError("watch queue moved before terminal hook result")
    head = subprocess.run(("git", "rev-parse", "HEAD"), cwd=repository,
                          check=True, capture_output=True, text=True).stdout.strip()
    branch_head = subprocess.run(
        ("git", "rev-parse", "master" if merge else branch), cwd=repository,
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    if head != commit or branch_head != commit:
        raise CrashHarnessError("confirmed completion commit was lost on resume")
    result_index = chain.index(results[0])
    queue_result_indices = [
        index for index, record in enumerate(chain)
        if isinstance(record.payload, SideEffectPayload)
        and record.payload.effect_class == "queue_move"
        and record.payload.phase == "result"
    ]
    queue_done_after_result = (
        len(queue_done) == 1 and len(queue_result_indices) == 1
        and result_index < queue_result_indices[0]
    )
    completed_intent_after_resume = (
        len(hooks) == 1 and hooks[0].result is not None
        and results[0].payload.effect_key == hooks[0].effect_key
    )
    if not queue_done_after_result or not completed_intent_after_resume:
        raise CrashHarnessError("hook result, intent, and queue move are not ordered")
    evidence = {
        "runtime_boundary": runtime_boundary,
        "effect_class": "post_merge_hook", "phase": phase,
        "requested_crashes": 1, "observed_crashes": 1,
        "physical_execution_count": len(calls),
        "process_started_observed": started.exists(),
        "process_ended_observed": ended.exists(),
        "result_completion_count": len(results),
        "ordinary_resume_rejected": ordinary_rejected,
        "acknowledgment_recorded": (
            acknowledgment and outcome["status"] == "acknowledged_unknown"
        ),
        "terminal_status": outcome["status"],
        "skip_reason": outcome.get("reason"),
        "commit_binding_verified": hooks[0].operation[1] == commit,
        "branch_head_verified": head == commit and branch_head == commit,
        "queue_done_after_result": queue_done_after_result,
        "open_intent_after_crash": len(open_hooks) == 1,
        "completed_intent_after_resume": completed_intent_after_resume,
        "production_resume_entry": "orchestrator.run_production_workflow",
        "production_resume_attempts": production_resume_attempts,
        "production_resume_successes": production_resume_successes,
        "production_boundary_entry": "workflow_completion._post_merge_effect",
        "resume_attempts": production_resume_attempts,
        "record_count": len(chain),
        "end_state": "converged",
    }
    return {**evidence, "hook_evidence_sha256": hashlib.sha256(
        canonical_json(evidence)
    ).hexdigest()}


def run_crash_matrix(root: Path, manifest: CrashHarnessManifest) -> tuple[Mapping[str, object], ...]:
    """Run every manifest-derived boundary plus a repeated worst-window crash."""

    matrix = [
        (_run_post_merge_case(root, runtime_boundary, phase)
         if runtime_boundary in HOOK_RUNTIME_BOUNDARIES
         else _run_crash_case(
             root, runtime_boundary, effect_class,
             SideEffectBoundaryPhase(phase),
         ))
        for runtime_boundary, effect_class in manifest.runtime_boundaries.items()
        if runtime_boundary not in HOOK_PROCESS_BOUNDARIES
        for phase in manifest.boundary_matrix[effect_class]
    ]
    matrix.extend(
        _run_post_merge_case(root, boundary, phase)
        for boundary, phase in (
            ("post_merge_after_process_start", "after_process_start"),
            ("post_merge_after_process_end", "after_process_end"),
        )
    )
    matrix.extend(
        _run_crash_case(
            root,
            runtime_boundary,
            effect_class,
            SideEffectBoundaryPhase.AFTER_EFFECT,
            requested_crashes=2,
        )
        for runtime_boundary, effect_class in manifest.runtime_boundaries.items()
        if runtime_boundary not in HOOK_RUNTIME_BOUNDARIES
        if "after_effect" in manifest.boundary_matrix[effect_class]
    )
    expected_single_cases = {
        (runtime_boundary, effect_class, phase, 1)
        for runtime_boundary, effect_class in manifest.runtime_boundaries.items()
        if runtime_boundary not in HOOK_PROCESS_BOUNDARIES
        for phase in manifest.boundary_matrix[effect_class]
    }
    expected_single_cases.update({
        (boundary, "post_merge_hook", phase, 1)
        for boundary, phase in (
            ("post_merge_after_process_start", "after_process_start"),
            ("post_merge_after_process_end", "after_process_end"),
        )
    })
    expected_repeated_cases = {
        (runtime_boundary, effect_class, "after_effect", 2)
        for runtime_boundary, effect_class in manifest.runtime_boundaries.items()
        if runtime_boundary not in HOOK_RUNTIME_BOUNDARIES
        if "after_effect" in manifest.boundary_matrix[effect_class]
    }
    observed_cases = {
        (
            str(row["runtime_boundary"]),
            str(row["effect_class"]),
            str(row["phase"]),
            int(row["requested_crashes"]),
        )
        for row in matrix
    }
    if observed_cases != expected_single_cases | expected_repeated_cases:
        raise CrashHarnessError("crash matrix did not execute its declared topology")
    if any(
        row["observed_crashes"] != row["requested_crashes"] for row in matrix
    ):
        raise CrashHarnessError("a declared crash boundary was not reached")
    canonical_by_boundary: dict[str, set[tuple[str, str]]] = {}
    for row in matrix:
        if row["end_state"] != "converged":
            continue
        if row["runtime_boundary"] in HOOK_RUNTIME_BOUNDARIES:
            continue
        canonical_by_boundary.setdefault(
            str(row["runtime_boundary"]), set()
        ).add(
            (
                str(row["chain_semantic_sha256"]),
                str(row["side_effect_projection_sha256"]),
            )
        )
    if any(len(values) != 1 for values in canonical_by_boundary.values()):
        raise CrashHarnessError("crash boundary changed chain or projected semantics")
    return tuple(matrix)


def prove_foreign_reducer_rejected_before_state(root: Path) -> Mapping[str, object]:
    """Corrupt a mid-journey reducer binding and prove projection never begins."""

    run_id = "s5-foreign-reducer"
    run_root = root / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    bridge = _production_baseline(run_root, run_id)
    spec, result = _effect_spec("internal")
    SideEffectExecutor(bridge).execute(
        spec,
        reconcile=lambda: Reconciliation(ReconciliationOutcome.NOT_OCCURRED),
        perform=lambda: (result, result),
    )
    store = ArtifactStore(run_root, run_id)
    valid_chain = store.load_chain()
    records_before_corruption = len(valid_chain)
    profile = next(
        record for record in valid_chain if record.logical_id == "run-profile"
    )
    path = store.records_dir / f"{profile.record_id}.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["record"]["payload"]["reducer_version"] = "foreign-reducer"
    document["content_sha256"] = hashlib.sha256(
        canonical_json(document["record"])
    ).hexdigest()
    path.write_bytes(canonical_json(document) + b"\n")
    projection_call_count = 0
    original_project = artifact_resume.project_workflow_state

    def count_projection(*args: object, **kwargs: object) -> object:
        nonlocal projection_call_count
        projection_call_count += 1
        return original_project(*args, **kwargs)

    artifact_resume.project_workflow_state = count_projection
    try:
        try:
            resolve_resume_state(run_root, run_id)
        except ArtifactResumeError as exc:
            if "reducer_version" not in str(exc):
                raise CrashHarnessError(
                    "foreign reducer failed for an unrelated reason"
                ) from exc
            diagnostic_code = exc.code.value
        else:  # pragma: no cover - fail-closed invariant
            raise CrashHarnessError("foreign reducer binding was accepted")
    finally:
        artifact_resume.project_workflow_state = original_project
    if projection_call_count != 0:
        raise CrashHarnessError("foreign reducer reached workflow-state projection")
    return {
        "record_count_before_corruption": records_before_corruption,
        "projection_call_count": projection_call_count,
        "rejected": True,
        "diagnostic_code": diagnostic_code,
    }


def prove_unknown_reconciliation_fails_closed(root: Path) -> Mapping[str, object]:
    """Prove an ambiguous durable outcome cannot re-enter the physical edge."""

    run_id = "s5-unknown-reconciliation"
    run_root = root / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    bridge = _production_baseline(run_root, run_id)
    spec, _ = _effect_spec("file_write")
    bridge.record_side_effect_intent(
        effect_class=spec.effect_class,
        work_unit_id=spec.work_unit_id,
        operation=spec.operation,
        fingerprint_sha256=spec.fingerprint_sha256,
    )
    physical_execution_count = 0

    def forbidden_perform() -> tuple[str, str]:
        nonlocal physical_execution_count
        physical_execution_count += 1
        return "unexpected", "unexpected"

    try:
        SideEffectExecutor(bridge).execute(
            spec,
            reconcile=lambda: Reconciliation(ReconciliationOutcome.UNKNOWN),
            perform=forbidden_perform,
        )
    except SideEffectReconciliationError as exc:
        diagnostic = str(exc)
    else:  # pragma: no cover - fail-closed invariant
        raise CrashHarnessError("UNKNOWN reconciliation crossed the physical edge")
    if physical_execution_count != 0:
        raise CrashHarnessError("UNKNOWN reconciliation executed a side effect")
    return {
        "outcome": ReconciliationOutcome.UNKNOWN.value,
        "rejected": True,
        "physical_execution_count": physical_execution_count,
        "diagnostic_sha256": hashlib.sha256(diagnostic.encode("utf-8")).hexdigest(),
    }


def _repository_commit(repository_root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _tracked_implementation_sources(
    repository_root: Path, manifest_path: Path
) -> tuple[Path, ...]:
    root = repository_root.resolve()
    manifest_relative = manifest_path.resolve().relative_to(root).as_posix()
    completed = subprocess.run(
        (
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            ":(glob)schemas/**/*.json",
            ":(glob)src/**/*.py",
            "scripts/crash_harness.py",
            "tests/test_orchestrator_runtime.py",
            manifest_relative,
        ),
        cwd=root,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise CrashHarnessError(
            "Cannot resolve tracked crash-harness implementation sources: "
            + os.fsdecode(completed.stderr).strip()
        )
    relative_paths = tuple(
        sorted(
            os.fsdecode(raw).replace("\\", "/")
            for raw in completed.stdout.split(b"\0")
            if raw and (root / os.fsdecode(raw)).is_file()
        )
    )
    required = {"scripts/crash_harness.py", manifest_relative}
    missing = sorted(required.difference(relative_paths))
    if missing:
        raise CrashHarnessError(
            "Crash-harness implementation source is not tracked: "
            + ", ".join(missing)
        )
    return tuple(root / relative for relative in relative_paths)


def canonical_result(document: Mapping[str, object]) -> bytes:
    """Return a self-bound canonical artifact with no volatile runtime fields."""

    body = dict(document)
    body.pop("artifact_sha256", None)
    digest = hashlib.sha256(canonical_json(body)).hexdigest()
    body["artifact_sha256"] = digest
    return canonical_json(body) + b"\n"


def _run_journeys(work_root: Path) -> tuple[Mapping[str, object], ...]:
    """Exercise independent runs, same-run final review, and Inbox follow-up."""

    from dry_run_scenarios import (
        build_s5_plan_only_scenario,
        build_s5_long_run_scenario,
        run_scripted_workflow_resumable,
    )
    from plan_handoff import write_implementation_handoff
    from task_contract import parse_task_contract
    from workflow_state import WorkflowStep

    journey_root = work_root / "journeys"
    journey_root.mkdir(parents=True, exist_ok=False)
    plan_task = journey_root / "s5-plan.md"
    plan_task.write_text("provider-free S5 PLAN_ONLY journey", encoding="utf-8")
    work_plan = journey_root / "docs" / "internal" / "s5-work-plan.md"
    work_plan.parent.mkdir(parents=True, exist_ok=True)
    work_plan.write_text(
        "# S5 work plan\n\n"
        "### Slice 1 - First implementation\n\n"
        "**Exakter Änderungspfad**\n\n- `src/first.py`\n\n"
        "#### Akzeptanzkriterien\n\n- First Slice is complete.\n\n"
        "### Slice 2 - Finding correction\n\n"
        "**Exakter Änderungspfad**\n\n- `src/second.py`\n\n"
        "#### Akzeptanzkriterien\n\n- Carried findings are closed.\n",
        encoding="utf-8",
    )
    driver_factory = lambda scenario: RecordBackedScriptedWorkflowDriver(  # noqa: E731
        scenario, journey_root
    )
    plan = run_scripted_workflow_resumable(
        scenario=build_s5_plan_only_scenario(),
        task_file=plan_task,
        driver_factory=driver_factory,
    )
    plan_commit = plan.result.state.approved_plan_commit
    if (
        not plan.result.workflow_completed
        or plan.result.state.execution_mode != "PLAN_ONLY"
        or plan_commit is None
    ):
        raise CrashHarnessError("PLAN_ONLY journey did not produce a reviewed commit")
    handoff = write_implementation_handoff(
        plan_task_path=plan_task,
        repository_root=journey_root,
        work_plan_path="docs/internal/s5-work-plan.md",
        target_branch=plan.result.state.branch,
        approved_plan_commit=plan_commit,
    )
    handoff_content = handoff.read_text(encoding="utf-8")
    repeated_handoff = write_implementation_handoff(
        plan_task_path=plan_task,
        repository_root=journey_root,
        work_plan_path="docs/internal/s5-work-plan.md",
        target_branch=plan.result.state.branch,
        approved_plan_commit=plan_commit,
    )
    if repeated_handoff != handoff or repeated_handoff.read_text(
        encoding="utf-8"
    ) != handoff_content:
        raise CrashHarnessError("IMPLEMENT handoff was not idempotent")
    handoff_contract = parse_task_contract(handoff_content)
    if handoff_contract.approved_plan_commit != plan_commit:
        raise CrashHarnessError("IMPLEMENT handoff lost its approved-plan binding")

    long_scenario = build_s5_long_run_scenario()
    independent_scenario = replace(
        build_s5_long_run_scenario(),
        name="s5-long-run-independent-v1",
    )
    long = run_scripted_workflow_resumable(
        scenario=long_scenario,
        task_file=handoff,
        driver_factory=driver_factory,
    )
    second_long = run_scripted_workflow_resumable(
        scenario=independent_scenario,
        task_file=handoff,
        driver_factory=driver_factory,
    )
    findings = long.result.history.findings
    second_findings = second_long.result.history.findings

    long_run_id = f"dry-{long_scenario.name}"
    long_store = ArtifactStore(journey_root, long_run_id)
    followup_publisher = _production_driver(journey_root)
    followup_publisher._artifact_bridge = ArtifactBridge(  # noqa: SLF001
        long_store, now=lambda: FIXED_TIME
    )
    followup_publisher.active_state = long.result.state
    followup_task = followup_publisher.publish_followup_task(long.result.state)
    if followup_task is None or not followup_task.is_file():
        raise CrashHarnessError("final review published no ordinary Inbox task")
    followup_content = followup_task.read_text(encoding="utf-8")
    if (
        "The final branch review found a follow-up defect." not in followup_content
        or "src/second.py" not in followup_content
        or "C-02" in followup_content
        or long_run_id in followup_content
    ):
        raise CrashHarnessError(
            "follow-up task is incomplete or exposes cross-run correlation"
        )

    journey_resolutions = {
        "plan": resolve_resume_state(journey_root, "dry-s5-plan-only-v1"),
        "long": resolve_resume_state(journey_root, long_run_id),
        "independent": resolve_resume_state(
            journey_root, f"dry-{independent_scenario.name}"
        ),
    }
    if (
        not long.result.workflow_completed
        or long.result.state.current_step is not WorkflowStep.COMPLETED
        or long.result.state.execution_mode != "IMPLEMENT"
        or long.result.state.approved_plan_commit != plan_commit
        or long.result.state.current_work_unit.kind is not WorkUnitKind.FINAL_REVIEW
        or tuple(item.finding_id for item in findings) != ("C-01", "C-02")
        or journey_resolutions["long"].state.current_step
        is not WorkflowStep.COMPLETED
    ):
        raise CrashHarnessError("combined long-run did not close its complete ledger")
    if (
        not second_long.result.workflow_completed
        or tuple(item.finding_id for item in second_findings) != ("C-01", "C-02")
        or journey_resolutions["independent"].state.current_step
        is not WorkflowStep.COMPLETED
    ):
        raise CrashHarnessError("independent multi-slice journey did not converge")
    plan_agents = tuple(call for call in plan.calls if call.startswith("agent:"))
    long_agents = tuple(call for call in long.calls if call.startswith("agent:"))
    second_long_agents = tuple(
        call for call in second_long.calls if call.startswith("agent:")
    )
    handoff_sha256 = hashlib.sha256(handoff_content.encode("utf-8")).hexdigest()
    followup_sha256 = hashlib.sha256(followup_content.encode("utf-8")).hexdigest()
    return (
        {
            "scenario_id": "plan-implement-final-review",
            "agent_invocation_count": len(plan_agents) + len(long_agents),
            "commit_count": sum(call.startswith("commit:") for call in plan.calls)
            + sum(call.startswith("commit:") for call in long.calls),
            "validation_count": sum(plan.validation_counts.values())
            + sum(long.validation_counts.values()),
            "resume_count": sum(call.startswith("interrupt:") for call in long.calls),
            "plan_only_execution_mode": plan.result.state.execution_mode,
            "implement_execution_mode": long.result.state.execution_mode,
            "final_review_work_unit_kind": long.result.state.current_work_unit.kind.value,
            "approved_plan_commit": plan_commit,
            "handoff_sha256": handoff_sha256,
            "handoff_idempotent": True,
            "durability_mode": "structured-v2-record-chain",
            "record_run_ids": [
                "dry-s5-plan-only-v1",
                long_run_id,
            ],
            "record_heads": [
                journey_resolutions["plan"].record_head_id,
                journey_resolutions["long"].record_head_id,
            ],
            "end_state": "completed",
        },
        {
            "scenario_id": "multi-slice-correction-observation-resume",
            "agent_invocation_count": len(second_long_agents),
            "commit_count": sum(
                call.startswith("commit:") for call in second_long.calls
            ),
            "validation_count": sum(second_long.validation_counts.values()),
            "resume_count": sum(
                call.startswith("interrupt:") for call in second_long.calls
            ),
            "finding_statuses": [
                f"{item.finding_id}:{item.status.value}" for item in second_findings
            ],
            "correction_round_count": sum(
                call.startswith("agent:codex:")  # allowlist:provider -- scripted role trace
                and call.endswith(":codex_correction")  # allowlist:provider -- typed step
                for call in second_long.calls
            ),
            "approved_plan_commit": second_long.result.state.approved_plan_commit,
            "handoff_sha256": handoff_sha256,
            "independent_execution": True,
            "durability_mode": "structured-v2-record-chain",
            "record_run_ids": [f"dry-{independent_scenario.name}"],
            "record_heads": [journey_resolutions["independent"].record_head_id],
            "end_state": "completed",
        },
        {
            "scenario_id": "final-review-followup-document",
            "agent_invocation_count": 0,
            "commit_count": 0,
            "validation_count": 0,
            "resume_count": 0,
            "source_execution_mode": long.result.state.execution_mode,
            "target_path": str(followup_task.relative_to(journey_root)),
            "finding_count": 1,
            "correlation_free": True,
            "handoff_sha256": followup_sha256,
            "durability_mode": "structured-v2-record-chain",
            "record_run_ids": [long_run_id],
            "record_heads": [journey_resolutions["long"].record_head_id],
            "end_state": "completed",
        },
    )


def prove_typed_failure_continuations() -> tuple[Mapping[str, object], ...]:
    """Exercise quota, network and process halts through typed state APIs."""

    from workflow_state import (
        AgentFailureKind,
        InvocationFailureRecord,
        ProtocolBinding,
        ProtocolMode,
        WorkUnitStatus,
        WorkflowStep,
        init_workflow_state,
    )

    rows: list[Mapping[str, object]] = []
    for kind in (
        AgentFailureKind.QUOTA,
        AgentFailureKind.NETWORK,
        AgentFailureKind.PROCESS,
    ):
        state = init_workflow_state(
            run_id=f"s5-{kind.value}-continuation",
            task_file="inbox/backlog/s5.md",
            branch="feature/state-authority-consolidation",
            branch_base="a" * 40,
            first_slice_start_commit=FIRST_SLICE_START_COMMIT,
            slice_count=1,
            protocol_binding=ProtocolBinding(ProtocolMode.STRUCTURED_V2, "2"),
            timestamp=FIXED_TIME,
        )
        automatic = kind in {AgentFailureKind.QUOTA, AgentFailureKind.NETWORK}
        failure = InvocationFailureRecord(
            invocation_id=f"s5-{kind.value}-1",
            idempotency_key=f"s5:1:codex_plan:{kind.value}",  # allowlist:provider
            role="codex",  # allowlist:provider -- typed role vocabulary
            failure_kind=kind,
            provider_text=f"redacted {kind.value} diagnostic",
            received_at=FIXED_TIME,
            step=WorkflowStep.CODEX_PLAN,  # allowlist:provider -- typed workflow step
            slice_id=1,
            work_unit_id=1,
            diagnostic_exit_code=2 if kind is AgentFailureKind.QUOTA else 3,
            process_exit_code=(137 if kind is AgentFailureKind.PROCESS else None),
            technical_text=technical_text_evidence(
                f"technical {kind.value} diagnostic"
            )[0],
            reset_at_utc=(
                "2026-09-01T00:00:04+00:00"
                if kind is AgentFailureKind.QUOTA
                else None
            ),
            resume_at_utc=(
                "2026-09-01T00:00:05+00:00" if automatic else None
            ),
            safety_margin_seconds=1 if kind is AgentFailureKind.QUOTA else 0,
            auto_resume_count=1 if automatic else 0,
            automatic_resume=automatic,
        )
        halted = state.record_invocation_failure(
            failure, wait_automatically=automatic, updated_at=FIXED_TIME
        )
        expected_halt = {
            AgentFailureKind.QUOTA: WorkUnitStatus.WAITING_FOR_QUOTA,
            AgentFailureKind.NETWORK: WorkUnitStatus.WAITING_FOR_RETRY,
            AgentFailureKind.PROCESS: WorkUnitStatus.AWAITING_RESUME,
        }[kind]
        if halted.current_work_unit.status is not expected_halt:
            raise CrashHarnessError(f"typed {kind.value} halt used a foreign status")
        resumed = halted.resume_after_invocation_halt(updated_at=FIXED_TIME)
        if (
            resumed.current_work_unit.status is not WorkUnitStatus.IN_PROGRESS
            or resumed.current_step is not WorkflowStep.CODEX_PLAN  # allowlist:provider
            or resumed.current_work_unit.invocation_failures != (failure,)
        ):
            raise CrashHarnessError(f"typed {kind.value} continuation lost evidence")
        rows.append(
            {
                "failure_kind": kind.value,
                "automatic": automatic,
                "halt_status": expected_halt.value,
                "resume_step": resumed.current_step.value,
                "evidence_count": len(resumed.current_work_unit.invocation_failures),
            }
        )
    return tuple(rows)


def run_provider_free_harness(
    *,
    repository_root: Path,
    work_root: Path,
    manifest_path: Path,
    repository_commit: str | None = None,
) -> bytes:
    """Execute S5's standard mode; no provider adapter is constructed or called."""

    import agent_runtime
    import readable_audit

    manifest = CrashHarnessManifest.load(manifest_path)
    work_root.mkdir(parents=True, exist_ok=True)
    real_provider_starts = 0
    real_provider_process_starts = 0
    original_run_agent = agent_runtime.run_agent
    original_provider_process = agent_runtime._run_agent_process
    original_read_plan = readable_audit.read_approved_plan

    def forbidden_provider_start(*args: object, **kwargs: object) -> str:
        nonlocal real_provider_starts
        real_provider_starts += 1
        raise CrashHarnessError("provider-free harness attempted a real provider start")

    def forbidden_provider_process(*args: object, **kwargs: object) -> object:
        nonlocal real_provider_process_starts
        real_provider_process_starts += 1
        raise CrashHarnessError("provider-free harness attempted a real provider process")

    def scripted_approved_plan(_root: Path, commit: str, path: str) -> str:
        if commit != "b" * 40 or path != "docs/internal/s5-work-plan.md":
            raise CrashHarnessError("unexpected approved plan binding in scripted journey")
        plan = work_root / "journeys" / path
        return plan.read_text(encoding="utf-8").split(
            "\n## Orchestrator-Prüfprotokoll\n", 1
        )[0]

    agent_runtime.run_agent = forbidden_provider_start
    agent_runtime._run_agent_process = forbidden_provider_process
    readable_audit.read_approved_plan = scripted_approved_plan
    try:
        matrix = run_crash_matrix(work_root / "matrix", manifest)
        semantic = prove_foreign_reducer_rejected_before_state(work_root / "semantic")
        unknown = prove_unknown_reconciliation_fails_closed(work_root / "unknown")
        journeys = _run_journeys(work_root)
        retries = prove_typed_failure_continuations()
    finally:
        agent_runtime.run_agent = original_run_agent
        agent_runtime._run_agent_process = original_provider_process
        readable_audit.read_approved_plan = original_read_plan
    if real_provider_starts != 0 or real_provider_process_starts != 0:
        raise CrashHarnessError("provider-free harness crossed the real provider boundary")
    heads = {
        runtime_boundary: next(
            str(row["record_head"])
            for row in matrix
            if row["runtime_boundary"] == runtime_boundary
            and row["requested_crashes"] == 1
        )
        for runtime_boundary in manifest.runtime_boundaries
        if runtime_boundary not in HOOK_RUNTIME_BOUNDARIES
    }
    semantic_heads = {
        runtime_boundary: next(
            str(row["chain_semantic_sha256"])
            for row in matrix
            if row["runtime_boundary"] == runtime_boundary
            and row["requested_crashes"] == 1
        )
        for runtime_boundary in manifest.runtime_boundaries
        if runtime_boundary not in HOOK_RUNTIME_BOUNDARIES
    }
    boundary_evidence = {
        runtime_boundary: {
            "ledger_class": effect_class,
            "tested_phases": (
                ["after_process_start"] if runtime_boundary == "post_merge_after_process_start"
                else ["after_process_end"] if runtime_boundary == "post_merge_after_process_end"
                else list(manifest.boundary_matrix[effect_class])
            ),
            "all_injected_crashes_observed": all(
                row["observed_crashes"] == row["requested_crashes"]
                for row in matrix
                if row["runtime_boundary"] == runtime_boundary
            ),
            "all_cases_converged": all(
                row["end_state"] == "converged"
                for row in matrix
                if row["runtime_boundary"] == runtime_boundary
            ),
            "end_states": sorted(
                {
                    str(row["end_state"])
                    for row in matrix
                    if row["runtime_boundary"] == runtime_boundary
                }
            ),
        }
        for runtime_boundary, effect_class in manifest.runtime_boundaries.items()
    }
    stop_conditions = tuple(
        {
            "effect_class": row["effect_class"],
            "phase": row["phase"],
            "diagnostic_code": row["resume_diagnostic_code"],
            "stop_condition_id": row["stop_condition_id"],
            "stop_scope": row["stop_scope"],
            "production_resume_attempts": row["production_resume_attempts"],
            "production_resume_successes": row["production_resume_successes"],
        }
        for row in matrix
        if row["end_state"] == "stop_condition"
    )
    measured_sources = _tracked_implementation_sources(
        repository_root, manifest_path
    )
    implementation_digest = hashlib.sha256()
    measured_source_paths: list[str] = []
    for path in measured_sources:
        relative = path.resolve().relative_to(repository_root.resolve()).as_posix()
        measured_source_paths.append(relative)
        implementation_digest.update(relative.encode("utf-8"))
        implementation_digest.update(b"\0")
        implementation_digest.update(path.read_bytes())
    result = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "repository_commit": repository_commit or _repository_commit(repository_root),
        "scenario_version": manifest.scenario_version,
        "harness_implementation_sha256": implementation_digest.hexdigest(),
        "measured_source_paths": measured_source_paths,
        "mode": "provider-free",
        "baseline_resolution": {
            "decision": "A",
            "strategy": "complete-canonical-append-prefix",
            "rationale": (
                "an interrupted initializer contains only idempotent record appends "
                "and no physical effect, so the exact canonical prefix is completed"
            ),
            "admission_rule": (
                "every exact cut of the canonical pre-work record sequence, "
                "including workflow, gate, task, work-unit, and plan facts"
            ),
            "rejection_rule": (
                "any non-prefix fact before baseline completion or prior non-ledger "
                "side effect remains fail-closed"
            ),
        },
        "record_heads": heads,
        "record_semantic_heads": semantic_heads,
        "invocation_counts": {
            "real_provider_starts": real_provider_starts,
            "real_provider_process_starts": real_provider_process_starts,
            "simulated_agent_starts": sum(
                int(item["agent_invocation_count"]) for item in journeys
            ),
            "simulated_side_effects": len(matrix),
        },
        "commit_count": sum(int(item["commit_count"]) for item in journeys),
        "queue_move_count": sum(
            row["physical_execution_count"]
            for row in matrix
            if row["effect_class"] == "queue_move"
        ),
        "crash_matrix": matrix,
        "post_merge_hook_evidence": [
            row for row in matrix
            if row["runtime_boundary"] in HOOK_RUNTIME_BOUNDARIES
        ],
        "workflow_boundary_evidence": boundary_evidence,
        "record_ahead_evidence": {
            runtime_boundary: {
                "physical_execution_count": next(
                    row["physical_execution_count"]
                    for row in matrix
                    if row["runtime_boundary"] == runtime_boundary
                    and row["phase"] == "after_effect"
                    and row["requested_crashes"] == 2
                ),
                "result_completion_count": next(
                    row["result_completion_count"]
                    for row in matrix
                    if row["runtime_boundary"] == runtime_boundary
                    and row["phase"] == "after_effect"
                    and row["requested_crashes"] == 2
                ),
            }
            for runtime_boundary, effect_class in manifest.runtime_boundaries.items()
            if runtime_boundary not in HOOK_RUNTIME_BOUNDARIES
            if "after_effect" in manifest.boundary_matrix[effect_class]
        },
        "semantic_binding": semantic,
        "unknown_reconciliation": unknown,
        "retry_continuations": retries,
        "journeys": journeys,
        "stop_conditions": stop_conditions,
        "blocked_acceptance_cases": (
            ["baseline-crash-convergence", "record-backed-long-run"]
            if stop_conditions
            else []
        ),
        "end_state": "stopped" if stop_conditions else "converged",
    }
    return canonical_result(result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    payload = run_provider_free_harness(
        repository_root=args.repository_root.resolve(),
        work_root=args.work_root.resolve(),
        manifest_path=args.manifest.resolve(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(payload)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as an operator command
    raise SystemExit(main())
