"""Workflow validation and validation-attestation recovery.

The production driver remains the composition root.  This module owns the
validation decisions and depends one-way on persistence for completion of a
durable validation-content record; no lower workflow layer imports it.
"""

from __future__ import annotations

import hashlib
import logging
import shlex
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from agent_runtime import OrchestratorConfig, run_validation_matrix
from artifact_bridge import ArtifactBridge, attestation_payload
from artifact_models import ValidationAttestationPayload, ValidationContentPayload
from content_authority import RAW_OUTPUT_DIGEST_V1, ValidationCapture
from contracts import (
    ValidationAttestation,
    ValidationCommandSpec,
    ValidationRecord,
    ValidationStatus,
)
from gates import matches_path_patterns
from plan_handoff import extract_implementation_slices
from semantic_markdown import SemanticMarkdownError, canonical_semantic_markdown
from workflow import (
    PlanContractFailureKind,
    PlanContractValidationError,
    WorkflowChanges,
    WorkflowExecutionError,
)
from workflow_persistence import WorkflowPersistence
from workflow_state import GateReason, WorkflowState


logger = logging.getLogger(__name__)


class ActiveStateProvider(Protocol):
    def __call__(self) -> WorkflowState | None: ...


class ArtifactBridgeProvider(Protocol):
    def __call__(self) -> ArtifactBridge | None: ...


class PersistenceProvider(Protocol):
    def __call__(self) -> WorkflowPersistence: ...


class RootProvider(Protocol):
    def __call__(self) -> Path: ...


class ConfigProvider(Protocol):
    def __call__(self) -> OrchestratorConfig: ...


@dataclass(frozen=True)
class WorkflowValidationDependencies:
    """Driver-owned resources required by validation and its recovery path."""

    root: RootProvider
    active_state: ActiveStateProvider
    artifact_bridge: ArtifactBridgeProvider
    assert_structured_decision_context: Callable[[], None]
    config: ConfigProvider
    persistence: PersistenceProvider


class WorkflowValidation:
    """Own validation decisions without owning mutable driver state."""

    def __init__(self, dependencies: WorkflowValidationDependencies) -> None:
        self._dependencies = dependencies

    def recover_pending_validation_attestation(
        self,
        fingerprint: str,
        expected_commands: tuple[str, ...],
        attestation_id: str,
    ) -> ValidationAttestation | None:
        """Restore exact validation state from its authoritative content blobs."""
        bridge = self._dependencies.artifact_bridge()
        if bridge is None:
            return None
        chain = bridge.store.current_chain()
        candidates = tuple(
            record
            for record in chain
            if isinstance(record.payload, ValidationContentPayload)
            and record.fingerprint.sha256 == fingerprint
            and record.payload.attestation_id == attestation_id
            and tuple(
                (
                    item.command.argv[0]
                    if item.command.mode == "legacy_shell"
                    else shlex.join(item.command.argv)
                )
                for item in record.payload.outputs
            )
            == expected_commands
        )
        if not candidates:
            return None
        if len(candidates) != 1:
            raise WorkflowExecutionError(
                "validation recovery has multiple content records"
            )
        content_record = candidates[0]
        payload = content_record.payload
        assert isinstance(payload, ValidationContentPayload)
        specs = tuple(
            (
                ValidationCommandSpec(legacy_shell=item.command.argv[0])
                if item.command.mode == "legacy_shell"
                else ValidationCommandSpec(argv=item.command.argv)
            )
            for item in payload.outputs
        )
        captures: list[ValidationCapture] = []
        records: list[ValidationRecord] = []
        for item, spec in zip(payload.outputs, specs, strict=True):
            try:
                stdout = bridge.store.read_blob(item.raw_stdout).decode("utf-8")
                stderr = bridge.store.read_blob(item.raw_stderr).decode("utf-8")
                compact = bridge.store.read_blob(item.compact_output).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise WorkflowExecutionError(
                    "validation content is not canonical UTF-8"
                ) from exc
            capture = ValidationCapture(
                spec.display,
                item.digest_outcome,
                item.exit_code,
                stdout,
                stderr,
                compact,
            )
            captures.append(capture)
            if item.digest_outcome in {"pass", "fail", "timeout"}:
                records.append(
                    ValidationRecord(
                        ValidationStatus.PASS
                        if item.digest_outcome == "pass"
                        else ValidationStatus.FAIL,
                        spec.display,
                        item.exit_code,
                        compact,
                    )
                )
        attestation = ValidationAttestation(
            attestation_id=payload.attestation_id,
            diff_fingerprint=fingerprint,
            expected_commands=expected_commands,
            records=tuple(records),
            output_digest=payload.output_digest,
            summary=payload.summary,
            command_specs=specs,
            content_captures=tuple(captures),
            content_digest_format=payload.digest_format,
        )
        result = next(
            (
                record
                for record in chain
                if record.record_id == payload.result_record_id
            ),
            None,
        )
        if result is None:
            self._dependencies.persistence().persist_validation_attestation(attestation)
        elif (
            not isinstance(result.payload, ValidationAttestationPayload)
            or result.payload
            != attestation_payload(attestation, content_record.record_id)
        ):
            raise WorkflowExecutionError(
                "validation recovery result differs from its content"
            )
        return attestation

    def validate(self, changes: WorkflowChanges, request: object) -> object:
        # ``changes`` is part of the stable driver surface even though the matrix
        # request already carries the fingerprint-bound validation context.
        del changes
        self._dependencies.assert_structured_decision_context()
        started = time.monotonic()
        logger.info(
            "Validation matrix starting: commands=%s attempt=%s",
            len(request.commands),  # type: ignore[attr-defined]
            request.attempt_number,  # type: ignore[attr-defined]
        )
        attestation = run_validation_matrix(
            config=self._dependencies.config(),
            request=request,
        )
        logger.info(
            "Validation matrix finished: status=%s elapsed=%.2fs summary=%s",
            attestation.status.value,
            time.monotonic() - started,
            attestation.summary,
        )
        return attestation

    def validate_plan(
        self,
        changes: WorkflowChanges,
        *,
        work_plan_path: str | None,
        scope_patterns: tuple[str, ...],
        plan_only: bool,
    ) -> ValidationAttestation:
        state = self._dependencies.active_state()
        if state is None or not state.planned_slices:
            raise PlanContractValidationError(
                PlanContractFailureKind.PERSISTED_SLICE_PLAN_MISSING,
                "internal plan validation requires a persisted SLICE_PLAN",
            )
        planned_paths = tuple(
            sorted(
                {
                    path
                    for planned in state.planned_slices
                    for path in planned.scope_paths
                }
            )
        )
        unexpected_planned = tuple(
            path
            for path in planned_paths
            if scope_patterns and not matches_path_patterns(path, scope_patterns)
        )
        if unexpected_planned:
            raise PlanContractValidationError(
                PlanContractFailureKind.PLANNED_PATH_OUTSIDE_SCOPE,
                "internal plan validation found out-of-scope SLICE_PLAN paths: "
                + ", ".join(unexpected_planned),
            )
        actual_paths = tuple(
            path for path in changes.paths if path != ".orchestrator/plan-output.md"
        )
        unexpected_actual = tuple(
            path
            for path in actual_paths
            if scope_patterns and not matches_path_patterns(path, scope_patterns)
        )
        approved_actual = any(
            state.current_work_unit.has_gate_approval(
                reason,
                changes.fingerprint,
                unexpected_actual,
            )
            for reason in (
                GateReason.UNEXPECTED_FILE,
                GateReason.QUOTA_RESUME_DIFF,
            )
        )
        if unexpected_actual and not approved_actual:
            raise PlanContractValidationError(
                PlanContractFailureKind.CHANGED_PATH_OUTSIDE_SCOPE,
                "internal plan validation found out-of-scope planning changes: "
                + ", ".join(unexpected_actual),
            )

        command = "internal:slice-plan-contract"
        detail = (
            f"slices={len(state.planned_slices)}; "
            f"planned_paths={len(planned_paths)}; changed_paths={len(actual_paths)}"
        )
        if plan_only:
            command = "internal:work-plan-contract"
            if len(state.planned_slices) != 1:
                raise PlanContractValidationError(
                    PlanContractFailureKind.PLAN_ARTIFACT_SLICE_COUNT_INVALID,
                    "PLAN_ONLY requires exactly one executable plan-artifact Slice",
                )
            if work_plan_path is None or work_plan_path not in planned_paths:
                raise PlanContractValidationError(
                    PlanContractFailureKind.WORK_PLAN_PATH_NOT_PLANNED,
                    "PLAN_ONLY plan does not include WORK_PLAN_PATH",
                )
            if work_plan_path not in actual_paths:
                raise PlanContractValidationError(
                    PlanContractFailureKind.WORK_PLAN_PATH_NOT_CHANGED,
                    "PLAN_ONLY Codex planning must create or update WORK_PLAN_PATH",
                )
            root = self._dependencies.root()
            candidate = root / work_plan_path
            try:
                resolved_candidate = candidate.resolve()
            except (OSError, RuntimeError, ValueError) as exc:
                raise PlanContractValidationError(
                    PlanContractFailureKind.WORK_PLAN_PATH_UNSAFE_RESOLUTION,
                    f"WORK_PLAN_PATH cannot be resolved safely: {exc}",
                ) from exc
            if (
                not resolved_candidate.is_relative_to(root)
                or resolved_candidate != candidate.absolute()
                or candidate.is_symlink()
                or not candidate.is_file()
            ):
                raise PlanContractValidationError(
                    PlanContractFailureKind.WORK_PLAN_PATH_NOT_REGULAR,
                    "WORK_PLAN_PATH must be a regular non-symlink file",
                )
            try:
                content = candidate.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise PlanContractValidationError(
                    PlanContractFailureKind.WORK_PLAN_PATH_NOT_UTF8,
                    f"WORK_PLAN_PATH is not readable UTF-8: {exc}",
                ) from exc
            try:
                canonical_semantic_markdown(
                    content,
                    path=work_plan_path,
                    remove_appendix=True,
                )
            except SemanticMarkdownError as exc:
                raise PlanContractValidationError(
                    PlanContractFailureKind.WORK_PLAN_SEMANTIC_MARKDOWN_INVALID,
                    f"WORK_PLAN_PATH has invalid managed Markdown: {exc}",
                ) from exc
            if not content.strip():
                raise PlanContractValidationError(
                    PlanContractFailureKind.WORK_PLAN_PATH_EMPTY,
                    "WORK_PLAN_PATH must not be empty",
                )
            try:
                future_slices = extract_implementation_slices(
                    content,
                    plan_stem=Path(work_plan_path).stem,
                )
            except ValueError as exc:
                raise PlanContractValidationError(
                    PlanContractFailureKind.WORK_PLAN_HANDOFF_INVALID,
                    f"WORK_PLAN_PATH cannot produce an IMPLEMENT handoff: {exc}",
                ) from exc
            detail += (
                f"; future_slices={len(future_slices)}; work_plan={work_plan_path}"
            )

        digest = hashlib.sha256(detail.encode("utf-8")).hexdigest()
        return ValidationAttestation(
            attestation_id=f"plan-validation-{changes.fingerprint[:12]}",
            diff_fingerprint=changes.fingerprint,
            expected_commands=(command,),
            records=(ValidationRecord(ValidationStatus.PASS, command, 0, detail),),
            output_digest=digest,
            summary="internal plan contract passed",
            command_specs=(ValidationCommandSpec(argv=(command,)),),
            content_captures=(
                ValidationCapture(command, "pass", 0, detail, "", detail),
            ),
            content_digest_format=RAW_OUTPUT_DIGEST_V1,
        )
