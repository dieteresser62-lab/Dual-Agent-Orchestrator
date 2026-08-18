from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import PurePosixPath
from typing import Iterable


DELIMITED_SECTION_PATTERN = re.compile(
    r"<<<\s*([A-Z_]+)_BEGIN\s*>>>.*?<<<\s*\1_END\s*>>>",
    re.IGNORECASE | re.DOTALL,
)
SOURCE_FINDING_ID_PATTERN = re.compile(r"^(C|A)-(0[1-9]|[1-9][0-9]*)$")
ANCHOR_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
VALIDATION_RECORD_OUTPUT_MAX_CHARS = 4_000


class ContractValidationError(ValueError):
    """Raised when a state-v3 response violates its explicit step contract."""


class AgentRole(str, Enum):
    CODEX = "codex"
    CLAUDE = "claude"
    ANTIGRAVITY = "antigravity"


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
        if self.reporter not in (AgentRole.CLAUDE, AgentRole.ANTIGRAVITY):
            raise ValueError("finding reporter must be claude or antigravity")


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
        if self.reviewer not in (AgentRole.CLAUDE, AgentRole.ANTIGRAVITY):
            raise ValueError("review step requires claude or antigravity")
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
        normalized_finding_ids = tuple(sorted(set(self.existing_finding_ids)))
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

    @property
    def open_blockers(self) -> tuple[FindingRecord, ...]:
        return tuple(
            finding
            for finding in self.findings
            if finding.status is FindingStatus.OPEN
            and finding.finding_class is FindingClass.BLOCKER
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
        return tuple(
            finding
            for finding in self.findings
            if finding.status is FindingStatus.OPEN
        )

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

    def __post_init__(self) -> None:
        if self.slice_id < 1:
            raise ValueError("planned slice id must be 1-based")
        if not self.summary.strip():
            raise ValueError("planned slice summary must not be empty")
        normalized = tuple(sorted(set(self.scope_paths)))
        if not normalized or normalized != self.scope_paths:
            raise ValueError("planned slice paths must be sorted, unique, and non-empty")
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


def strip_delimited_sections(text: str) -> str:
    return DELIMITED_SECTION_PATTERN.sub("", text or "")


def _validate_finding_id(finding_id: str, reporter: AgentRole) -> None:
    match = SOURCE_FINDING_ID_PATTERN.fullmatch(finding_id)
    if not match:
        raise ValueError(f"invalid finding id '{finding_id}' (expected C-01 or A-01)")
    expected_prefix = "C" if reporter is AgentRole.CLAUDE else "A"
    if match.group(1) != expected_prefix:
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


def parse_finding_responses(text: str) -> dict[str, FindingResponse]:
    contract_text = strip_delimited_sections(text)
    pattern = re.compile(
        r"^\s*FINDING_RESPONSE\s*:\s*([A-Za-z0-9_-]+)\s*\|\s*"
        r"(ACCEPTED|REJECTED)\s*\|\s*(.+?)\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    responses: dict[str, FindingResponse] = {}
    for match in pattern.finditer(contract_text):
        finding_id = match.group(1).upper()
        if finding_id in responses:
            raise ContractValidationError(f"duplicate FINDING_RESPONSE for {finding_id}")
        try:
            responses[finding_id] = FindingResponse(
                decision=FindingResponseDecision(match.group(2).upper()),
                rationale=match.group(3).strip(),
            )
        except ValueError as exc:
            raise ContractValidationError(str(exc)) from exc
    if len(responses) != _marker_count(contract_text, "FINDING_RESPONSE"):
        raise ContractValidationError("invalid FINDING_RESPONSE record")
    return responses


def apply_finding_responses(
    findings: Iterable[FindingRecord],
    text: str,
) -> tuple[FindingRecord, ...]:
    by_id: dict[str, FindingRecord] = {}
    for finding in findings:
        if finding.finding_id in by_id:
            raise ContractValidationError(f"duplicate previous finding {finding.finding_id}")
        by_id[finding.finding_id] = finding
    responses = parse_finding_responses(text)
    for finding_id, response in responses.items():
        if finding_id not in by_id:
            raise ContractValidationError(
                f"FINDING_RESPONSE references unknown finding {finding_id}"
            )
        by_id[finding_id] = apply_finding_response(
            by_id[finding_id], response.decision, response.rationale
        )
    return tuple(by_id[finding_id] for finding_id in sorted(by_id))


def validate_codex_response(
    output: str,
    contract: CodexStepContract,
    previous_findings: Iterable[FindingRecord] = (),
) -> CodexContractResult:
    text = strip_delimited_sections(output)
    _reject_v2_markers(text, allow_finding_response=True)
    _reject_codex_review_markers(text)
    _reject_unknown_contract_markers(text)
    _require_done_marker(text)

    stop_request, _ = _parse_stop_request(text)
    ready = _parse_readiness(text, contract)
    if stop_request is not None:
        if ready is not None:
            raise ContractValidationError(
                "STOP_REQUESTED and a readiness marker cannot appear together"
            )
        return CodexContractResult(
            ready=None,
            stopped=True,
            stop_request=stop_request,
            validation=None,
            test_files=(),
            findings=tuple(sorted(previous_findings, key=lambda item: item.finding_id)),
            slice_plan=(),
        )
    if ready is None:
        raise ContractValidationError(
            f"missing or invalid {contract.readiness_marker.value} marker"
        )

    validation = _parse_validation(text)
    if contract.validation_attestation is not None and validation is not None:
        raise ContractValidationError(
            "final Codex report cannot emit VALIDATION_RESULT; validation comes from the orchestrator attestation"
        )
    if contract.require_validation and validation is None:
        raise ContractValidationError("ready response requires VALIDATION_RESULT")
    if validation is not None:
        if (
            ready
            and validation.status is not ValidationStatus.PASS
            and contract.red_state_followup_slice is None
        ):
            raise ContractValidationError("ready response requires passing validation")
        if (
            contract.expected_validation_command is not None
            and validation.command != contract.expected_validation_command
        ):
            raise ContractValidationError(
                "VALIDATION_RESULT command does not match the step contract"
            )

    if contract.require_test_files_record:
        test_files = _parse_test_files(text)
        if contract.enforce_expected_test_files and test_files != contract.expected_test_files:
            raise ContractValidationError(
                "TEST_FILES_TOUCHED does not match the step contract"
            )
        if ready and test_files and not contract.test_changes_approved:
            raise ContractValidationError(
                "ready response with test changes requires prior TEST_CHANGE_APPROVAL"
            )
    else:
        if _marker_count(text, "TEST_FILES_TOUCHED"):
            raise ContractValidationError(
                "unexpected TEST_FILES_TOUCHED for this Codex step"
            )
        test_files = ()

    prior = tuple(previous_findings)
    responses = parse_finding_responses(text)
    open_ids = {
        finding.finding_id
        for finding in prior
        if finding.status is FindingStatus.OPEN
    }
    if set(responses) != open_ids:
        missing = sorted(open_ids - set(responses))
        unexpected = sorted(set(responses) - open_ids)
        if missing:
            raise ContractValidationError(
                f"missing FINDING_RESPONSE for open finding {missing[0]}"
            )
        raise ContractValidationError(
            f"FINDING_RESPONSE references non-open finding {unexpected[0]}"
        )
    findings = apply_finding_responses(prior, text)
    slice_plan = _parse_slice_plan(text)
    if contract.require_slice_plan and not slice_plan:
        raise ContractValidationError("ready plan requires at least one SLICE_PLAN record")
    if not contract.require_slice_plan and slice_plan:
        raise ContractValidationError("unexpected SLICE_PLAN for this Codex step")
    return CodexContractResult(
        ready=ready,
        stopped=False,
        stop_request=None,
        validation=validation,
        test_files=test_files,
        findings=findings,
        slice_plan=slice_plan,
    )


def validate_step_response(
    output: str,
    contract: StepContract | CodexStepContract,
    previous_findings: Iterable[FindingRecord] = (),
) -> ContractResult | CodexContractResult:
    """Dispatch one state-v3 agent response through its explicit step contract."""
    if isinstance(contract, StepContract):
        return validate_review_response(output, contract, previous_findings)
    if isinstance(contract, CodexStepContract):
        return validate_codex_response(output, contract, previous_findings)
    raise TypeError(f"unsupported step contract type: {type(contract).__name__}")


def parse_anchors(text: str, *, origin: str) -> tuple[AnchorRecord, ...]:
    contract_text = strip_delimited_sections(text)
    marker_count = _marker_count(contract_text, "ANCHOR")
    if marker_count and not origin.strip():
        raise ContractValidationError("anchor parsing requires an origin")
    pattern = re.compile(r"^\s*ANCHOR\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
    anchors: dict[str, AnchorRecord] = {}
    for match in pattern.finditer(contract_text):
        parts = [part.strip() for part in match.group(1).split("|", 3)]
        if len(parts) != 4:
            raise ContractValidationError(
                "ANCHOR requires <id> | <input> | <expected> | <tolerance>"
            )
        try:
            anchor = AnchorRecord(parts[0], origin, *parts[1:])
        except ValueError as exc:
            raise ContractValidationError(str(exc)) from exc
        if anchor.anchor_id in anchors:
            raise ContractValidationError(f"duplicate ANCHOR id {anchor.anchor_id}")
        anchors[anchor.anchor_id] = anchor
    if len(anchors) != marker_count:
        raise ContractValidationError("invalid ANCHOR record")
    return tuple(anchors[anchor_id] for anchor_id in sorted(anchors))


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


def validate_review_response(
    output: str,
    contract: StepContract,
    previous_findings: Iterable[FindingRecord] = (),
) -> ContractResult:
    text = strip_delimited_sections(output)
    _reject_v2_markers(text)
    if _marker_count(text, "VALIDATION_RESULT"):
        raise ContractValidationError(
            "reviewers cannot emit VALIDATION_RESULT; validation must come from the bound orchestrator attestation"
        )
    if any(_marker_count(text, marker.value) for marker in ReadinessMarker):
        raise ContractValidationError("review response cannot contain a readiness marker")
    _reject_unknown_contract_markers(text)
    _require_done_marker(text)
    reviewer = _parse_reviewer(text)
    if reviewer is not contract.reviewer:
        raise ContractValidationError(
            f"REVIEWER {reviewer.value} does not match step reviewer {contract.reviewer.value}"
        )

    stop_request, stop_position = _parse_stop_request(text)
    approval, approval_position = _parse_approval(text, contract)
    if stop_request is not None:
        if approval is not None:
            raise ContractValidationError(
                "STOP_REQUESTED and an approval marker cannot appear together"
            )
        return ContractResult(
            reviewer=reviewer,
            approval=None,
            stopped=True,
            stop_request=stop_request,
            validation=contract.validation_attestation,
            test_files=(),
            pre_mortem=None,
            evidence=None,
            findings=tuple(sorted(previous_findings, key=lambda item: item.finding_id)),
            anchors=parse_anchors(text, origin=contract.anchor_origin or ""),
            red_state_followup_slice=None,
        )
    if stop_position is not None:
        raise ContractValidationError("invalid STOP_REQUESTED marker")
    if approval is None or approval_position is None:
        raise ContractValidationError(
            f"missing or invalid {contract.approval_marker.value} marker"
        )

    validation = contract.validation_attestation
    test_files = _parse_test_files(text)
    if test_files != contract.expected_test_files:
        raise ContractValidationError(
            "TEST_FILES_TOUCHED does not match the step contract"
        )
    evidence = _parse_review_evidence(text)
    pre_mortem, pre_mortem_position = _parse_optional_single_value(text, "PRE_MORTEM")
    anchors = parse_anchors(text, origin=contract.anchor_origin or "")
    previous_findings = tuple(previous_findings)
    previous_by_id = {
        finding.finding_id: finding for finding in previous_findings
    }
    previous_finding_ids = {finding.finding_id for finding in previous_findings}
    findings, has_finding_record = _merge_review_findings(
        text, contract, previous_findings
    )

    if not has_finding_record and evidence is None:
        raise ContractValidationError(
            "review requires at least one finding or REVIEW_EVIDENCE record"
        )

    own_open_blockers = tuple(
        finding
        for finding in findings
        if finding.status is FindingStatus.OPEN
        and finding.finding_class is FindingClass.BLOCKER
        and finding.origin.reporter is contract.reviewer
    )
    if (
        contract.approval_marker is ApprovalMarker.FINAL
        or not contract.allow_new_observations
    ):
        new_disallowed_observations = tuple(
            finding
            for finding in findings
            if finding.finding_class is FindingClass.OBSERVATION
            and (
                finding.finding_id not in previous_finding_ids
                or (
                    not contract.allow_new_observations
                    and previous_by_id[finding.finding_id].finding_class
                    is not FindingClass.OBSERVATION
                )
            )
        )
        if new_disallowed_observations:
            finding_policy = (
                "final review cannot introduce a new OBSERVATION"
                if contract.approval_marker is ApprovalMarker.FINAL
                else "correction convergence review cannot introduce or reclassify "
                "to a new OBSERVATION"
            )
            raise ContractValidationError(
                f"{finding_policy}; record "
                "non-actionable residual risk in REVIEW_EVIDENCE or report an "
                "actionable BLOCKER"
            )
    if approval:
        if validation is None:
            raise ContractValidationError(
                "approval requires a bound orchestrator validation attestation"
            )
        if not validation.complete:
            raise ContractValidationError(
                "approval requires a complete validation attestation"
            )
        if not validation.passed and contract.red_state_followup_slice is None:
            raise ContractValidationError(
                "approval requires a complete passing validation attestation"
            )
        if test_files and not contract.test_changes_approved:
            raise ContractValidationError(
                "approval with test changes requires prior TEST_CHANGE_APPROVAL"
            )
        if pre_mortem is None or pre_mortem_position is None:
            raise ContractValidationError("approval requires PRE_MORTEM")
        if pre_mortem_position > approval_position:
            raise ContractValidationError("PRE_MORTEM must appear before approval")
        if own_open_blockers:
            raise ContractValidationError(
                "approval is invalid while a BLOCKER is open for this reviewer"
            )
        if contract.approval_marker is ApprovalMarker.FINAL:
            own_open_findings = tuple(
                finding
                for finding in findings
                if finding.status is FindingStatus.OPEN
                and finding.origin.reporter is contract.reviewer
            )
            if own_open_findings:
                raise ContractValidationError(
                    "final approval is invalid while a finding is open for this reviewer"
                )
            if contract.reviewer is AgentRole.ANTIGRAVITY:
                open_findings = tuple(
                    finding
                    for finding in findings
                    if finding.status is FindingStatus.OPEN
                )
                if open_findings:
                    raise ContractValidationError(
                        "Antigravity final approval requires zero open findings"
                    )
    elif not own_open_blockers:
        raise ContractValidationError(
            "negative approval requires an open BLOCKER owned by this reviewer"
        )

    return ContractResult(
        reviewer=reviewer,
        approval=approval,
        stopped=False,
        stop_request=None,
        validation=validation,
        test_files=test_files,
        pre_mortem=pre_mortem,
        evidence=evidence,
        findings=findings,
        anchors=anchors,
        red_state_followup_slice=contract.red_state_followup_slice,
    )


def _reject_v2_markers(text: str, *, allow_finding_response: bool = False) -> None:
    disallowed = (
        "PHASE1_APPROVAL",
        "PHASE2_APPROVAL",
        "CODEX_APPROVAL",
        "CLAUDE_APPROVAL",
        "OPEN_FINDINGS",
    )
    for marker in disallowed:
        if re.search(rf"^\s*{marker}\s*:", text, re.IGNORECASE | re.MULTILINE):
            raise ContractValidationError(f"state-v3 contract rejects {marker}")
    if not allow_finding_response and re.search(
        r"^\s*FINDING_RESPONSE\s*:", text, re.IGNORECASE | re.MULTILINE
    ):
        raise ContractValidationError("review response cannot contain FINDING_RESPONSE")


def _reject_codex_review_markers(text: str) -> None:
    disallowed = (
        "REVIEWER",
        "PLAN_APPROVAL",
        "SLICE_APPROVAL",
        "FINAL_APPROVAL",
        "NEW_FINDING",
        "FINDING_STATUS",
        "FINDING_RECLASSIFIED",
        "REVIEW_EVIDENCE",
        "PRE_MORTEM",
    )
    for marker in disallowed:
        if _marker_count(text, marker):
            raise ContractValidationError(
                f"Codex step response cannot contain {marker}"
            )


def _reject_unknown_contract_markers(text: str) -> None:
    known = {
        "ANCHOR",
        "FINAL_APPROVAL",
        "FINDING_RECLASSIFIED",
        "FINDING_RESPONSE",
        "FINDING_STATUS",
        "NEW_FINDING",
        "OPEN_FINDINGS",
        "PHASE1_APPROVAL",
        "PHASE2_APPROVAL",
        "PLAN_APPROVAL",
        "PLAN_READY",
        "FINAL_REPORT_READY",
        "PRE_MORTEM",
        "REVIEWER",
        "REVIEW_EVIDENCE",
        "REMEDIATION_PATHS",
        "SLICE_APPROVAL",
        "SLICE_PLAN",
        "STATUS",
        "STOP_REQUESTED",
        "TEST_FILES_TOUCHED",
        "VALIDATION_RESULT",
        "IMPLEMENTATION_READY",
        "CODEX_APPROVAL",
        "CLAUDE_APPROVAL",
    }
    marker_pattern = re.compile(r"^\s*([A-Z][A-Z0-9_]+)\s*:", re.MULTILINE)
    for match in marker_pattern.finditer(text):
        if match.group(1) not in known:
            raise ContractValidationError(
                f"unknown state-v3 contract marker {match.group(1)}"
            )


def _marker_count(text: str, marker: str) -> int:
    return len(
        re.findall(rf"^\s*{marker}\s*:", text, re.IGNORECASE | re.MULTILINE)
    )


def _require_done_marker(text: str) -> None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or lines[-1] != "STATUS: DONE":
        raise ContractValidationError("missing final STATUS: DONE marker")
    if sum(line == "STATUS: DONE" for line in lines) != 1:
        raise ContractValidationError("duplicate STATUS: DONE marker")


def _parse_reviewer(text: str) -> AgentRole:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    matches = re.findall(r"^\s*REVIEWER\s*:\s*(\w+)\s*$", text, re.IGNORECASE | re.MULTILINE)
    if len(matches) != 1:
        raise ContractValidationError("review requires exactly one REVIEWER marker")
    if not lines or not lines[0].upper().startswith("REVIEWER:"):
        raise ContractValidationError("REVIEWER must be the first non-empty line")
    try:
        reviewer = AgentRole(matches[0].lower())
    except ValueError as exc:
        raise ContractValidationError(f"unknown reviewer '{matches[0]}'") from exc
    if reviewer is AgentRole.CODEX:
        raise ContractValidationError("Codex cannot be a reviewer in state-v3")
    return reviewer


def _parse_approval(text: str, contract: StepContract) -> tuple[bool | None, int | None]:
    all_markers = tuple(marker.value for marker in ApprovalMarker)
    present = [
        marker
        for marker in all_markers
        if re.search(rf"^\s*{marker}\s*:", text, re.IGNORECASE | re.MULTILINE)
    ]
    expected = contract.approval_marker.value
    unexpected = [marker for marker in present if marker != expected]
    if unexpected:
        raise ContractValidationError(
            f"unexpected approval marker {unexpected[0]} for step {contract.name}"
        )
    if contract.approval_marker is ApprovalMarker.SLICE:
        pattern = re.compile(
            r"^\s*SLICE_APPROVAL\s*:\s*([^|]+?)\s*\|\s*(YES|NO)\s*$",
            re.IGNORECASE | re.MULTILINE,
        )
    else:
        pattern = re.compile(
            rf"^\s*{expected}\s*:\s*(YES|NO)\s*$",
            re.IGNORECASE | re.MULTILINE,
        )
    matches = list(pattern.finditer(text))
    if len(matches) != _marker_count(text, expected):
        raise ContractValidationError(f"invalid {expected} marker")
    if len(matches) > 1:
        raise ContractValidationError(f"duplicate {expected} marker")
    if not matches:
        return None, None
    match = matches[0]
    if contract.approval_marker is ApprovalMarker.SLICE:
        if match.group(1).strip() != contract.slice_id:
            raise ContractValidationError("SLICE_APPROVAL id does not match the step contract")
        decision = match.group(2)
    else:
        decision = match.group(1)
    return decision.upper() == "YES", match.start()


def _parse_readiness(text: str, contract: CodexStepContract) -> bool | None:
    all_markers = tuple(marker.value for marker in ReadinessMarker)
    expected = contract.readiness_marker.value
    for marker in all_markers:
        if marker != expected and _marker_count(text, marker):
            raise ContractValidationError(
                f"unexpected readiness marker {marker} for step {contract.name}"
            )
    if contract.readiness_marker is ReadinessMarker.IMPLEMENTATION:
        pattern = re.compile(
            r"^\s*IMPLEMENTATION_READY\s*:\s*([^|]+?)\s*\|\s*(YES|NO)\s*$",
            re.IGNORECASE | re.MULTILINE,
        )
    elif contract.readiness_marker is ReadinessMarker.PLAN:
        pattern = re.compile(
            r"^\s*PLAN_READY\s*:\s*(YES|NO)\s*$",
            re.IGNORECASE | re.MULTILINE,
        )
    else:
        pattern = re.compile(
            r"^\s*FINAL_REPORT_READY\s*:\s*(YES|NO)\s*$",
            re.IGNORECASE | re.MULTILINE,
        )
    matches = list(pattern.finditer(text))
    if len(matches) != _marker_count(text, expected):
        raise ContractValidationError(f"invalid {expected} marker")
    if len(matches) > 1:
        raise ContractValidationError(f"duplicate {expected} marker")
    if not matches:
        return None
    match = matches[0]
    if contract.readiness_marker is ReadinessMarker.IMPLEMENTATION:
        if match.group(1).strip() != contract.slice_id:
            raise ContractValidationError(
                "IMPLEMENTATION_READY id does not match the step contract"
            )
        decision = match.group(2)
    else:
        decision = match.group(1)
    return decision.upper() == "YES"


def _parse_stop_request(text: str) -> tuple[StopRequest | None, int | None]:
    raw, position = _parse_optional_single_value(text, "STOP_REQUESTED")
    remediation_raw, _ = _parse_optional_single_value(text, "REMEDIATION_PATHS")
    if raw is None:
        if remediation_raw is not None:
            raise ContractValidationError(
                "REMEDIATION_PATHS requires STOP_REQUESTED"
            )
        return None, position
    parts = [part.strip() for part in raw.split("|", 1)]
    if len(parts) != 2:
        raise ContractValidationError(
            "STOP_REQUESTED requires <rule id> | <rationale>"
        )
    remediation_paths: tuple[str, ...] = ()
    if remediation_raw is not None:
        remediation_paths = tuple(
            sorted(
                {
                    item.strip()
                    for item in remediation_raw.split(",")
                    if item.strip()
                }
            )
        )
        if not remediation_paths:
            raise ContractValidationError(
                "REMEDIATION_PATHS must list at least one path"
            )
    try:
        return StopRequest(parts[0], parts[1], remediation_paths), position
    except ValueError as exc:
        raise ContractValidationError(str(exc)) from exc


def _parse_optional_single_value(
    text: str, marker: str
) -> tuple[str | None, int | None]:
    pattern = re.compile(
        rf"^\s*{marker}\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE
    )
    matches = list(pattern.finditer(text))
    if len(matches) > 1:
        raise ContractValidationError(f"duplicate {marker} marker")
    if not matches:
        return None, None
    value = matches[0].group(1).strip()
    if not value:
        raise ContractValidationError(f"{marker} must not be empty")
    return value, matches[0].start()


def _parse_validation(text: str) -> ValidationRecord | None:
    raw, _ = _parse_optional_single_value(text, "VALIDATION_RESULT")
    if raw is None:
        return None
    parts = [part.strip() for part in raw.split("|")]
    if len(parts) < 3:
        raise ContractValidationError(
            "VALIDATION_RESULT requires <PASS|FAIL> | <command> | <exit code>"
        )
    status_text = parts[0].upper()
    command = "|".join(parts[1:-1]).strip()
    try:
        record = ValidationRecord(
            status=ValidationStatus(status_text),
            command=command,
            exit_code=int(parts[-1]),
        )
    except (ValueError, TypeError) as exc:
        raise ContractValidationError(f"invalid VALIDATION_RESULT: {exc}") from exc
    return record


def _parse_slice_plan(text: str) -> tuple[PlannedSlice, ...]:
    pattern = re.compile(
        r"^\s*SLICE_PLAN\s*:\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    records: list[PlannedSlice] = []
    for match in pattern.finditer(text):
        raw_paths = tuple(
            sorted(set(part.strip() for part in match.group(3).split(",") if part.strip()))
        )
        try:
            records.append(
                PlannedSlice(
                    slice_id=int(match.group(1)),
                    summary=match.group(2).strip(),
                    scope_paths=raw_paths,
                )
            )
        except ValueError as exc:
            raise ContractValidationError(str(exc)) from exc
    if len(records) != _marker_count(text, "SLICE_PLAN"):
        raise ContractValidationError(
            "SLICE_PLAN requires <1-based id> | <summary> | <comma-separated paths>"
        )
    ids = tuple(record.slice_id for record in records)
    if ids and ids != tuple(range(1, len(ids) + 1)):
        raise ContractValidationError(
            "SLICE_PLAN ids must be contiguous, ordered, and 1-based"
        )
    return tuple(records)


def _parse_test_files(text: str) -> tuple[str, ...]:
    raw, _ = _parse_optional_single_value(text, "TEST_FILES_TOUCHED")
    if raw is None:
        raise ContractValidationError("missing TEST_FILES_TOUCHED marker")
    if raw.upper() == "NONE":
        return ()
    paths = tuple(sorted(set(part.strip() for part in raw.split(",") if part.strip())))
    if not paths:
        raise ContractValidationError("TEST_FILES_TOUCHED must list paths or NONE")
    return paths


def _parse_review_evidence(text: str) -> ReviewEvidence | None:
    raw, _ = _parse_optional_single_value(text, "REVIEW_EVIDENCE")
    if raw is None:
        return None
    parts = [part.strip() for part in raw.split("|", 2)]
    if len(parts) != 3:
        raise ContractValidationError(
            "REVIEW_EVIDENCE requires <dimensions> | <largest risk> | <break condition>"
        )
    try:
        return ReviewEvidence(*parts)
    except ValueError as exc:
        raise ContractValidationError(str(exc)) from exc


def _merge_review_findings(
    text: str,
    contract: StepContract,
    previous_findings: Iterable[FindingRecord],
) -> tuple[tuple[FindingRecord, ...], bool]:
    findings: dict[str, FindingRecord] = {}
    for finding in previous_findings:
        if finding.finding_id in findings:
            raise ContractValidationError(f"duplicate previous finding {finding.finding_id}")
        findings[finding.finding_id] = finding

    new_pattern = re.compile(
        r"^\s*NEW_FINDING\s*:\s*([^|]+?)\s*\|\s*(BLOCKER|OBSERVATION)\s*"
        r"\|\s*(.+?)\s*\|\s*(.+?)\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    new_ids: set[str] = set()
    for match in new_pattern.finditer(text):
        finding_id = match.group(1).strip().upper()
        if finding_id in findings or finding_id in new_ids:
            raise ContractValidationError(f"duplicate or reused finding id {finding_id}")
        try:
            finding = FindingRecord(
                finding_id=finding_id,
                finding_class=FindingClass(match.group(2).upper()),
                status=FindingStatus.OPEN,
                summary=match.group(3).strip(),
                acceptance_test=match.group(4).strip(),
                origin=FindingOrigin(
                    slice_id=contract.slice_id,
                    round_number=contract.round_number,
                    reporter=contract.reviewer,
                ),
            )
        except ValueError as exc:
            raise ContractValidationError(str(exc)) from exc
        findings[finding_id] = finding
        new_ids.add(finding_id)
    if len(new_ids) != _marker_count(text, "NEW_FINDING"):
        raise ContractValidationError("invalid NEW_FINDING record")

    status_pattern = re.compile(
        r"^\s*FINDING_STATUS\s*:\s*([^|]+?)\s*\|\s*(OPEN|CLOSED)\s*"
        r"\|\s*(.+?)\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    status_ids: set[str] = set()
    for match in status_pattern.finditer(text):
        finding_id = match.group(1).strip().upper()
        if finding_id in status_ids:
            raise ContractValidationError(f"duplicate FINDING_STATUS for {finding_id}")
        if finding_id not in findings or finding_id in new_ids:
            raise ContractValidationError(
                f"FINDING_STATUS references unknown previous finding {finding_id}"
            )
        try:
            findings[finding_id] = apply_reviewer_finding_update(
                findings[finding_id],
                reviewer=contract.reviewer,
                status=FindingStatus(match.group(2).upper()),
                rationale=match.group(3).strip(),
            )
        except ValueError as exc:
            raise ContractValidationError(str(exc)) from exc
        status_ids.add(finding_id)
    if len(status_ids) != _marker_count(text, "FINDING_STATUS"):
        raise ContractValidationError("invalid FINDING_STATUS record")

    class_pattern = re.compile(
        r"^\s*FINDING_RECLASSIFIED\s*:\s*([^|]+?)\s*\|\s*"
        r"(BLOCKER|OBSERVATION)\s*\|\s*(.+?)\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    class_ids: set[str] = set()
    for match in class_pattern.finditer(text):
        finding_id = match.group(1).strip().upper()
        if finding_id in class_ids:
            raise ContractValidationError(
                f"duplicate FINDING_RECLASSIFIED for {finding_id}"
            )
        if finding_id not in findings or finding_id in new_ids:
            raise ContractValidationError(
                f"FINDING_RECLASSIFIED references unknown previous finding {finding_id}"
            )
        current = findings[finding_id]
        try:
            findings[finding_id] = apply_reviewer_finding_update(
                current,
                reviewer=contract.reviewer,
                status=current.status,
                rationale=match.group(3).strip(),
                finding_class=FindingClass(match.group(2).upper()),
            )
        except ValueError as exc:
            raise ContractValidationError(str(exc)) from exc
        class_ids.add(finding_id)
    if len(class_ids) != _marker_count(text, "FINDING_RECLASSIFIED"):
        raise ContractValidationError("invalid FINDING_RECLASSIFIED record")

    for finding_id, finding in findings.items():
        if (
            finding.status is FindingStatus.OPEN
            and finding.origin.reporter is contract.reviewer
            and finding_id not in new_ids
        ):
            if finding_id not in status_ids and finding_id not in class_ids:
                raise ContractValidationError(
                    f"missing review update for previous open finding {finding_id}"
                )

    has_record = bool(new_ids or status_ids or class_ids)
    return tuple(findings[finding_id] for finding_id in sorted(findings)), has_record
