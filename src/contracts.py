from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import PurePosixPath
from typing import Iterable

from acceptance_criteria import AcceptanceCriterion, validate_acceptance_criteria

from content_authority import (
    VALIDATION_MATRIX_DIGEST_V1,
    ValidationCapture,
    validation_output_digest,
)
from finding_order import sorted_finding_ids
from orchestrator_diagnostics import OrchestratorDiagnostic


SOURCE_FINDING_ID_PATTERN = re.compile(r"^C-(0[1-9]|[1-9][0-9]*)$")
ANCHOR_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
VALIDATION_RECORD_OUTPUT_MAX_CHARS = 4_000


class AgentRole(str, Enum):
    CODEX = "codex"
    CLAUDE = "claude"


class ApprovalMarker(str, Enum):
    PLAN = "PLAN_APPROVAL"
    SLICE = "SLICE_APPROVAL"
    FINAL = "FINAL_APPROVAL"


class ReadinessMarker(str, Enum):
    PLAN = "PLAN_READY"
    IMPLEMENTATION = "IMPLEMENTATION_READY"
    FINAL_REPORT = "FINAL_REPORT_READY"


class FindingClass(str, Enum):
    BLOCKER = "BLOCKER"
    OBSERVATION = "OBSERVATION"


class FindingStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class FindingResponseDecision(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class ValidationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"


class ValidationAttestationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCOMPLETE = "INCOMPLETE"


@dataclass(frozen=True)
class ValidationCommandSpec:
    """Lossless validation command identity used by persisted attestations.

    ``legacy_shell`` is deliberately opaque.  It exists only for state written
    before argv was persisted and is never split or promoted to argv.
    """

    argv: tuple[str, ...] = ()
    legacy_shell: str | None = None

    def __post_init__(self) -> None:
        if bool(self.argv) == (self.legacy_shell is not None):
            raise ValueError("validation command spec requires argv or legacy_shell")
        if self.argv and any(
            not isinstance(part, str)
            or not part
            or any(character in part for character in ("\x00", "\r", "\n"))
            for part in self.argv
        ):
            raise ValueError("validation argv entries must be non-empty safe strings")
        if self.legacy_shell is not None and (
            not self.legacy_shell.strip()
            or any(character in self.legacy_shell for character in ("\x00", "\r", "\n"))
        ):
            raise ValueError("legacy shell command must be one non-empty line")

    @property
    def mode(self) -> str:
        return "argv" if self.argv else "legacy_shell"

    @property
    def display(self) -> str:
        if self.argv:
            return shlex.join(self.argv)
        assert self.legacy_shell is not None
        return self.legacy_shell


@dataclass(frozen=True)
class FindingOrigin:
    slice_id: str
    round_number: int
    reporter: AgentRole

    def __post_init__(self) -> None:
        if not self.slice_id.strip():
            raise ValueError("finding origin requires a slice id")
        if self.round_number < 1:
            raise ValueError("finding origin round must be 1-based")
        if self.reporter is not AgentRole.CLAUDE:
            raise ValueError("finding reporter must be claude")


@dataclass(frozen=True)
class FindingResponse:
    decision: FindingResponseDecision
    rationale: str

    def __post_init__(self) -> None:
        if not self.rationale.strip():
            raise ValueError("finding response requires a rationale")


@dataclass(frozen=True)
class FindingRecord:
    finding_id: str
    finding_class: FindingClass
    status: FindingStatus
    summary: str
    acceptance_test: str
    origin: FindingOrigin
    responses: tuple[FindingResponse, ...] = ()
    status_rationale: str | None = None
    class_history: tuple[FindingClass, ...] = ()

    def __post_init__(self) -> None:
        _validate_finding_id(self.finding_id, self.origin.reporter)
        if not self.summary.strip():
            raise ValueError("finding summary must not be empty")
        if not self.acceptance_test.strip():
            raise ValueError("finding acceptance test must not be empty")
        if self.status is FindingStatus.CLOSED and not (self.status_rationale or "").strip():
            raise ValueError("closed finding requires a status rationale")


@dataclass(frozen=True)
class ReviewEvidence:
    dimensions: str
    largest_residual_risk: str
    break_condition: str

    @property
    def finding_class(self) -> FindingClass:
        """Review evidence is the required non-blocking observation record."""
        return FindingClass.OBSERVATION

    def __post_init__(self) -> None:
        if not self.dimensions.strip():
            raise ValueError("review evidence requires checked dimensions")
        if not self.largest_residual_risk.strip():
            raise ValueError("review evidence requires the largest residual risk")
        if not self.break_condition.strip():
            raise ValueError("review evidence requires a realistic break condition")


@dataclass(frozen=True)
class ValidationRecord:
    status: ValidationStatus
    command: str
    exit_code: int
    output: str = ""

    def __post_init__(self) -> None:
        if not self.command.strip():
            raise ValueError("validation command must not be empty")
        if self.status is ValidationStatus.PASS and self.exit_code != 0:
            raise ValueError("PASS validation requires exit code 0")
        if self.status is ValidationStatus.FAIL and self.exit_code == 0:
            raise ValueError("FAIL validation requires a non-zero exit code")
        if "\x00" in self.output:
            raise ValueError("validation output must not contain NUL bytes")
        if len(self.output) > VALIDATION_RECORD_OUTPUT_MAX_CHARS:
            raise ValueError("validation output must be compact")


@dataclass(frozen=True)
class ValidationAttestation:
    """Orchestrator-owned validation evidence bound to one review fingerprint."""

    attestation_id: str
    diff_fingerprint: str
    expected_commands: tuple[str, ...]
    records: tuple[ValidationRecord, ...]
    output_digest: str
    summary: str
    command_specs: tuple[ValidationCommandSpec, ...] = ()
    content_captures: tuple[ValidationCapture, ...] = ()
    content_digest_format: str = VALIDATION_MATRIX_DIGEST_V1

    def __post_init__(self) -> None:
        if not self.attestation_id.strip():
            raise ValueError("validation attestation requires an id")
        if not SHA256_PATTERN.fullmatch(self.diff_fingerprint):
            raise ValueError("validation attestation requires a SHA-256 diff fingerprint")
        if not SHA256_PATTERN.fullmatch(self.output_digest):
            raise ValueError("validation attestation requires a SHA-256 output digest")
        if not self.expected_commands or any(
            not command.strip() for command in self.expected_commands
        ):
            raise ValueError("validation attestation requires expected commands")
        if len(set(self.expected_commands)) != len(self.expected_commands):
            raise ValueError("validation attestation expected commands must be unique")
        if self.content_captures:
            if tuple(item.command for item in self.content_captures) != self.expected_commands:
                raise ValueError(
                    "validation content must follow every expected command exactly"
                )
            if validation_output_digest(
                self.content_captures,
                self.content_digest_format,
            ) != self.output_digest:
                raise ValueError(
                    "validation content does not reproduce the attestation digest"
                )
        record_commands = tuple(record.command for record in self.records)
        if len(set(record_commands)) != len(record_commands):
            raise ValueError("validation attestation command records must be unique")
        if unexpected := set(record_commands) - set(self.expected_commands):
            raise ValueError(
                "validation attestation contains unexpected commands: "
                + ", ".join(sorted(unexpected))
            )
        expected_record_order = tuple(
            command for command in self.expected_commands if command in set(record_commands)
        )
        if record_commands != expected_record_order:
            raise ValueError(
                "validation attestation records must follow expected command order"
            )
        if not self.summary.strip():
            raise ValueError("validation attestation requires a summary")
        specs = self.command_specs or tuple(
            ValidationCommandSpec(legacy_shell=command)
            for command in self.expected_commands
        )
        if tuple(spec.display for spec in specs) != self.expected_commands:
            raise ValueError(
                "validation command specs must losslessly match expected command order"
            )
        object.__setattr__(self, "command_specs", specs)

    @property
    def missing_commands(self) -> tuple[str, ...]:
        recorded = {record.command for record in self.records}
        return tuple(
            command for command in self.expected_commands if command not in recorded
        )

    @property
    def complete(self) -> bool:
        return not self.missing_commands

    @property
    def status(self) -> ValidationAttestationStatus:
        if not self.complete:
            return ValidationAttestationStatus.INCOMPLETE
        if any(record.status is ValidationStatus.FAIL for record in self.records):
            return ValidationAttestationStatus.FAIL
        return ValidationAttestationStatus.PASS

    @property
    def passed(self) -> bool:
        return self.status is ValidationAttestationStatus.PASS


@dataclass(frozen=True)
class StopRequest:
    rule_id: str
    rationale: str
    remediation_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.rule_id.strip():
            raise ValueError("stop request requires a rule id")
        if not self.rationale.strip():
            raise ValueError("stop request requires a rationale")
        normalized = tuple(sorted(set(self.remediation_paths)))
        if normalized != self.remediation_paths:
            raise ValueError("remediation paths must be sorted and unique")
        for raw_path in normalized:
            path = PurePosixPath(raw_path)
            if (
                not raw_path.strip()
                or path.is_absolute()
                or "\\" in raw_path
                or ".." in path.parts
                or raw_path != path.as_posix()
                or path.parts[0] == ".orchestrator"
            ):
                raise ValueError(
                    "remediation paths must be canonical repository-relative POSIX paths "
                    "outside .orchestrator"
                )


@dataclass(frozen=True)
class AnchorRecord:
    anchor_id: str
    origin: str
    input_fixture: str
    expected: str
    tolerance: str

    def __post_init__(self) -> None:
        if not ANCHOR_ID_PATTERN.fullmatch(self.anchor_id):
            raise ValueError(f"invalid anchor id '{self.anchor_id}'")
        if not self.origin.strip():
            raise ValueError("anchor origin or rationale must not be empty")
        if not self.input_fixture.strip():
            raise ValueError("anchor input or fixture must not be empty")
        if not self.expected.strip():
            raise ValueError("anchor expected value must not be empty")
        if not self.tolerance.strip():
            raise ValueError("anchor tolerance or rounding rule must not be empty")


@dataclass(frozen=True)
class AnchorChanges:
    added: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[str, ...]

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.changed)


@dataclass(frozen=True)
class StepContract:
    name: str
    reviewer: AgentRole
    approval_marker: ApprovalMarker
    slice_id: str
    round_number: int
    review_fingerprint: str | None = None
    validation_attestation: ValidationAttestation | None = None
    expected_test_files: tuple[str, ...] = ()
    test_changes_approved: bool = False
    red_state_followup_slice: str | None = None
    anchor_origin: str | None = None
    existing_finding_ids: tuple[str, ...] = ()
    allow_new_observations: bool = True

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("step contract requires a name")
        if self.reviewer is not AgentRole.CLAUDE:
            raise ValueError("review step requires claude")
        if not self.slice_id.strip():
            raise ValueError("step contract requires a slice id")
        if self.round_number < 1:
            raise ValueError("step contract round must be 1-based")
        if self.approval_marker is ApprovalMarker.SLICE and not self.slice_id.strip():
            raise ValueError("slice approval requires a slice id")
        if self.review_fingerprint is not None:
            if not SHA256_PATTERN.fullmatch(self.review_fingerprint):
                raise ValueError("review contract requires a SHA-256 diff fingerprint")
        if self.validation_attestation is not None:
            if self.review_fingerprint is None:
                raise ValueError("validation attestation requires a review fingerprint")
            if self.validation_attestation.diff_fingerprint != self.review_fingerprint:
                raise ValueError("validation attestation fingerprint does not match review")
        normalized = tuple(sorted(set(path.strip() for path in self.expected_test_files if path.strip())))
        object.__setattr__(self, "expected_test_files", normalized)
        if self.red_state_followup_slice is not None and not self.red_state_followup_slice.strip():
            raise ValueError("red-state exception requires a named follow-up slice")
        if self.anchor_origin is not None and not self.anchor_origin.strip():
            raise ValueError("anchor origin must be a stable non-empty identity")
        normalized_finding_ids = sorted_finding_ids(self.existing_finding_ids)
        if normalized_finding_ids != self.existing_finding_ids:
            raise ValueError("existing finding ids must be sorted and unique")
        for finding_id in self.existing_finding_ids:
            if not SOURCE_FINDING_ID_PATTERN.fullmatch(finding_id):
                raise ValueError(f"invalid existing finding id {finding_id}")
        if not isinstance(self.allow_new_observations, bool):
            raise ValueError("new-observation policy must be boolean")


@dataclass(frozen=True)
class ContractResult:
    reviewer: AgentRole
    approval: bool | None
    stopped: bool
    stop_request: StopRequest | None
    validation: ValidationAttestation | None
    test_files: tuple[str, ...]
    pre_mortem: str | None
    evidence: ReviewEvidence | None
    findings: tuple[FindingRecord, ...]
    anchors: tuple[AnchorRecord, ...]
    red_state_followup_slice: str | None = None

    def __post_init__(self) -> None:
        if (
            self.red_state_followup_slice is not None
            and not self.red_state_followup_slice.strip()
        ):
            raise ValueError(
                "review red-state follow-up slice must be non-empty when provided"
            )
        if self.red_state_followup_slice is not None and self.approval is not True:
            raise ValueError(
                "review red-state follow-up slice requires an approval"
            )

    @property
    def open_blockers(self) -> tuple[FindingRecord, ...]:
        from finding_reducer import project_open_set

        return tuple(
            finding
            for finding in project_open_set(self.findings).findings
            if finding.finding_class is FindingClass.BLOCKER
        )

    @property
    def own_open_blockers(self) -> tuple[FindingRecord, ...]:
        """Return blockers that this review role is authorized to resolve."""
        return tuple(
            finding
            for finding in self.open_blockers
            if finding.origin.reporter is self.reviewer
        )

    @property
    def open_findings(self) -> tuple[FindingRecord, ...]:
        from finding_reducer import project_open_set

        return project_open_set(self.findings).findings

    @property
    def own_open_findings(self) -> tuple[FindingRecord, ...]:
        """Return every open finding owned by the current review role."""
        return tuple(
            finding
            for finding in self.open_findings
            if finding.origin.reporter is self.reviewer
        )


@dataclass(frozen=True)
class PlannedSlice:
    """One ordered, repository-relative implementation boundary from Codex planning."""

    slice_id: int
    summary: str
    scope_paths: tuple[str, ...]
    acceptance_criteria: tuple[AcceptanceCriterion, ...] = ()

    def __post_init__(self) -> None:
        if self.slice_id < 1:
            raise ValueError("planned slice id must be 1-based")
        if not self.summary.strip():
            raise ValueError("planned slice summary must not be empty")
        validate_acceptance_criteria(self.slice_id, self.acceptance_criteria)
        diagnostic = planned_slice_path_diagnostic(self.scope_paths)
        if diagnostic is not None:
            raise ValueError(diagnostic.detail)
        normalized = self.scope_paths
        for raw_path in normalized:
            path = PurePosixPath(raw_path)
            if (
                not raw_path.strip()
                or path.is_absolute()
                or "\\" in raw_path
                or ".." in path.parts
                or raw_path != path.as_posix()
                or path.parts[0] == ".orchestrator"
            ):
                raise ValueError(
                    "planned slice paths must be canonical repository-relative POSIX paths outside .orchestrator"
                )


def planned_slice_path_diagnostic(
    scope_paths: tuple[str, ...],
) -> OrchestratorDiagnostic | None:
    """Return a typed local diagnosis for the closed path-set invariant."""
    normalized = tuple(sorted(set(scope_paths)))
    if not normalized or normalized != scope_paths:
        return OrchestratorDiagnostic.SLICE_PLAN_PATHS_INVALID
    return None


@dataclass(frozen=True)
class CodexStepContract:
    name: str
    readiness_marker: ReadinessMarker
    slice_id: str
    round_number: int
    require_validation: bool = False
    expected_validation_command: str | None = None
    require_test_files_record: bool = False
    expected_test_files: tuple[str, ...] = ()
    test_changes_approved: bool = False
    red_state_followup_slice: str | None = None
    review_fingerprint: str | None = None
    validation_attestation: ValidationAttestation | None = None
    require_slice_plan: bool = False
    plan_artifact_path: str | None = None
    enforce_expected_test_files: bool = True

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Codex step contract requires a name")
        if not self.slice_id.strip():
            raise ValueError("Codex step contract requires a slice id")
        if self.round_number < 1:
            raise ValueError("Codex step contract round must be 1-based")
        normalized = tuple(sorted(set(path.strip() for path in self.expected_test_files if path.strip())))
        object.__setattr__(self, "expected_test_files", normalized)
        if normalized and not self.require_test_files_record:
            raise ValueError("expected test files require a TEST_FILES_TOUCHED record")
        if self.red_state_followup_slice is not None and not self.red_state_followup_slice.strip():
            raise ValueError("red-state exception requires a named follow-up slice")
        if self.review_fingerprint is not None and not SHA256_PATTERN.fullmatch(
            self.review_fingerprint
        ):
            raise ValueError("Codex contract requires a SHA-256 review fingerprint")
        if self.validation_attestation is not None:
            if self.readiness_marker is not ReadinessMarker.FINAL_REPORT:
                raise ValueError(
                    "orchestrator attestation is reserved for the final Codex report"
                )
            if self.review_fingerprint is None:
                raise ValueError(
                    "final Codex attestation requires a review fingerprint"
                )
            if self.validation_attestation.diff_fingerprint != self.review_fingerprint:
                raise ValueError(
                    "final Codex attestation fingerprint does not match review"
                )
        if self.require_slice_plan and self.readiness_marker is not ReadinessMarker.PLAN:
            raise ValueError("slice planning records are reserved for Codex plan steps")
        if self.plan_artifact_path is not None:
            path = PurePosixPath(self.plan_artifact_path)
            if (
                not self.require_slice_plan
                or not self.plan_artifact_path.strip()
                or path.is_absolute()
                or "\\" in self.plan_artifact_path
                or ".." in path.parts
                or self.plan_artifact_path != path.as_posix()
            ):
                raise ValueError(
                    "plan artifact path requires a canonical plan-step SLICE_PLAN"
                )
        if not isinstance(self.enforce_expected_test_files, bool):
            raise ValueError("test-file enforcement flag must be a boolean")


@dataclass(frozen=True)
class CodexContractResult:
    ready: bool | None
    stopped: bool
    stop_request: StopRequest | None
    validation: ValidationRecord | None
    test_files: tuple[str, ...]
    findings: tuple[FindingRecord, ...]
    slice_plan: tuple[PlannedSlice, ...] = ()
    self_check: str | None = None


def _validate_finding_id(finding_id: str, reporter: AgentRole) -> None:
    match = SOURCE_FINDING_ID_PATTERN.fullmatch(finding_id)
    if not match:
        raise ValueError(f"invalid finding id '{finding_id}' (expected C-01)")
    if reporter is not AgentRole.CLAUDE:
        raise ValueError(
            f"finding id '{finding_id}' does not match reporter {reporter.value}"
        )


def apply_finding_response(
    finding: FindingRecord,
    decision: FindingResponseDecision,
    rationale: str,
) -> FindingRecord:
    if finding.status is FindingStatus.CLOSED:
        raise ValueError("cannot respond to a closed finding")
    response = FindingResponse(decision=decision, rationale=rationale)
    return replace(finding, responses=(*finding.responses, response))


def apply_reviewer_finding_update(
    finding: FindingRecord,
    *,
    reviewer: AgentRole,
    status: FindingStatus,
    rationale: str,
    finding_class: FindingClass | None = None,
) -> FindingRecord:
    if reviewer is not finding.origin.reporter:
        raise ValueError("only the reporting reviewer may update or close a finding")
    if not rationale.strip():
        raise ValueError("finding update requires a rationale")
    next_class = finding_class or finding.finding_class
    class_history = finding.class_history
    if next_class is not finding.finding_class:
        class_history = (*class_history, finding.finding_class)
    return replace(
        finding,
        finding_class=next_class,
        status=status,
        status_rationale=rationale,
        class_history=class_history,
    )


def compare_anchors(
    approved: Iterable[AnchorRecord],
    current: Iterable[AnchorRecord],
) -> AnchorChanges:
    approved_map = _unique_anchor_map(approved)
    current_map = _unique_anchor_map(current)
    approved_ids = set(approved_map)
    current_ids = set(current_map)
    return AnchorChanges(
        added=tuple(sorted(current_ids - approved_ids)),
        removed=tuple(sorted(approved_ids - current_ids)),
        changed=tuple(
            sorted(
                anchor_id
                for anchor_id in approved_ids & current_ids
                if approved_map[anchor_id] != current_map[anchor_id]
            )
        ),
    )


def _unique_anchor_map(anchors: Iterable[AnchorRecord]) -> dict[str, AnchorRecord]:
    result: dict[str, AnchorRecord] = {}
    for anchor in anchors:
        if anchor.anchor_id in result:
            raise ValueError(f"duplicate anchor id {anchor.anchor_id}")
        result[anchor.anchor_id] = anchor
    return result
