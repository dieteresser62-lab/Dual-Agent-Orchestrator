from __future__ import annotations

import json
import logging
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from contracts import (
    FindingClass,
    FindingRecord,
    SHA256_PATTERN,
    ValidationAttestation,
    ValidationCommandSpec,
    ValidationRecord,
    ValidationStatus,
)
from content_authority import ValidationCapture, validation_output_digest
from finding_reducer import project_open_set
from gates import matches_path_patterns, normalize_path_patterns


DEFAULT_VALIDATION_TIMEOUT_SECONDS = 300
VALIDATION_OUTPUT_LIMIT = 2_000
ARTIFACT_CHECK_COMMAND = "internal:artifact-delivery"
# A failed first run starts a bounded flake probe.  The run count is derived
# from measured matrix time, while this budget caps the ordinary extra cost and
# the sample cap prevents near-zero-duration commands from running unboundedly.
FLAKE_PROBE_BUDGET_SECONDS = 30.0
FLAKE_PROBE_MAX_RUNS = 5
FINDING_COMMAND_PREFIX = "VALIDATE:"
LOGGER = logging.getLogger(__name__)
SHELL_META_CHARACTERS = frozenset(";&|<>`$(){}[]*?!#~")
NPM_TEST_SCRIPT_PATTERN = re.compile(r"test(?::[A-Za-z0-9._-]+)*")


def _simple_shell_argv(command: str) -> tuple[str, ...] | None:
    """Return argv only when a shell command has no shell semantics."""
    try:
        argv = tuple(shlex.split(command, posix=True))
    except ValueError:
        return None
    if not argv:
        return None
    if "=" in argv[0] or any(
        any(character in SHELL_META_CHARACTERS for character in argument)
        for argument in argv
    ):
        return None
    return argv


def _canonical_argv(command: "ValidationCommand") -> tuple[str, ...] | None:
    if command.argv:
        return command.argv
    if command.artifact_pattern is not None:
        return (ARTIFACT_CHECK_COMMAND, command.artifact_pattern)
    assert command.shell_command is not None
    return _simple_shell_argv(command.shell_command)


def _finding_validation_command(value: list[str]) -> "ValidationCommand":
    """Normalize reviewer JSON, including the legacy ["shell", "..."] form."""
    if len(value) == 2 and value[0] == "shell":
        argv = _simple_shell_argv(value[1])
        if argv is None:
            raise ValidationMatrixError(
                "finding VALIDATE shell command must be a simple command without "
                "shell syntax"
            )
        return ValidationCommand(argv=argv)
    return ValidationCommand(argv=tuple(value))


def matches_validation_family(
    argv: tuple[str, ...], prefix: tuple[str, ...]
) -> bool:
    if argv[: len(prefix)] == prefix:
        return True
    # `npm test` is npm's canonical test family entry point. Permit one exact,
    # argument-free `npm run test:*` package script as a focused extension. This
    # intentionally excludes build/release scripts, npm exec, CLI arguments, and
    # every command that would require shell interpretation.
    return (
        prefix == ("npm", "test")
        and len(argv) == 3
        and argv[:2] == ("npm", "run")
        and NPM_TEST_SCRIPT_PATTERN.fullmatch(argv[2]) is not None
    )


class ValidationMatrixError(ValueError):
    """Raised when matrix selection or execution inputs are unsafe or ambiguous."""


@dataclass(frozen=True)
class ValidationCommand:
    argv: tuple[str, ...] = ()
    shell_command: str | None = None
    artifact_pattern: str | None = None
    timeout_seconds: int = DEFAULT_VALIDATION_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        has_argv = bool(self.argv)
        has_shell = self.shell_command is not None
        has_artifact = self.artifact_pattern is not None
        if sum((has_argv, has_shell, has_artifact)) != 1:
            raise ValidationMatrixError(
                "validation command requires exactly one of argv, shell_command, "
                "or artifact_pattern"
            )
        if has_argv and any(
            not isinstance(part, str)
            or not part
            or any(character in part for character in ("\x00", "\r", "\n"))
            for part in self.argv
        ):
            raise ValidationMatrixError(
                "validation argv entries must be non-empty strings without NUL bytes"
            )
        if has_argv and self.argv[0] == ARTIFACT_CHECK_COMMAND:
            raise ValidationMatrixError(
                "validation argv must not use the reserved artifact-check command"
            )
        if has_shell and (
            not isinstance(self.shell_command, str)
            or not self.shell_command.strip()
            or any(
                character in self.shell_command
                for character in ("\x00", "\r", "\n")
            )
        ):
            raise ValidationMatrixError(
                "validation shell command must be non-empty and contain no NUL bytes"
            )
        if has_artifact:
            try:
                normalized = normalize_path_patterns(
                    (self.artifact_pattern,), "required artifact"
                )
            except ValueError as exc:
                raise ValidationMatrixError(str(exc)) from exc
            if normalized != (self.artifact_pattern,):
                raise ValidationMatrixError(
                    "required artifact pattern must be canonical"
                )
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, int)
            or self.timeout_seconds < 1
        ):
            raise ValidationMatrixError(
                "validation timeout_seconds must be a positive integer"
            )

    @property
    def display(self) -> str:
        if self.argv:
            return shlex.join(self.argv)
        if self.artifact_pattern is not None:
            return shlex.join((ARTIFACT_CHECK_COMMAND, self.artifact_pattern))
        assert self.shell_command is not None
        return f"shell: {self.shell_command.strip()}"

    @property
    def command_spec(self) -> ValidationCommandSpec:
        if self.argv:
            return ValidationCommandSpec(argv=self.argv)
        if self.artifact_pattern is not None:
            return ValidationCommandSpec(
                argv=(ARTIFACT_CHECK_COMMAND, self.artifact_pattern)
            )
        assert self.shell_command is not None
        # The display prefix is presentation only; the persisted legacy value
        # stays opaque and is never reconstructed with shlex.
        return ValidationCommandSpec(legacy_shell=self.display)


@dataclass(frozen=True)
class ValidationRule:
    patterns: tuple[str, ...]
    command: ValidationCommand

    def __post_init__(self) -> None:
        try:
            normalized = normalize_path_patterns(self.patterns)
        except ValueError as exc:
            raise ValidationMatrixError(str(exc)) from exc
        if not normalized:
            raise ValidationMatrixError(
                "validation rule requires at least one path pattern"
            )
        if normalized != self.patterns:
            raise ValidationMatrixError(
                "validation rule patterns must be normalized and unique"
            )


@dataclass(frozen=True)
class ValidationMatrix:
    default_command: ValidationCommand | None = None
    rules: tuple[ValidationRule, ...] = ()
    required_artifacts: tuple[str, ...] = ()
    product_command: ValidationCommand | None = None

    def __post_init__(self) -> None:
        try:
            normalized_artifacts = normalize_path_patterns(
                self.required_artifacts, "required artifact"
            )
        except ValueError as exc:
            raise ValidationMatrixError(str(exc)) from exc
        if normalized_artifacts != self.required_artifacts:
            raise ValidationMatrixError(
                "required artifact patterns must be canonical and unique"
            )
        by_display: dict[str, ValidationCommand] = {}
        commands = (
            *((self.default_command,) if self.default_command is not None else ()),
            *(rule.command for rule in self.rules),
            *((self.product_command,) if self.product_command is not None else ()),
        )
        for command in commands:
            previous = by_display.setdefault(command.display, command)
            if previous.timeout_seconds != command.timeout_seconds:
                raise ValidationMatrixError(
                    f"validation command {command.display!r} has conflicting timeouts"
                )
        if self.product_command is not None and any(
            command.display == self.product_command.display
            for command in (
                *((self.default_command,) if self.default_command is not None else ()),
                *(rule.command for rule in self.rules),
            )
        ):
            raise ValidationMatrixError(
                "product validation command must be distinct from matrix commands"
            )

    @property
    def finding_command_prefixes(self) -> tuple[tuple[str, ...], ...]:
        prefixes: list[tuple[str, ...]] = []
        commands = (
            *((self.default_command,) if self.default_command is not None else ()),
            *(rule.command for rule in self.rules),
        )
        for command in commands:
            argv = _canonical_argv(command)
            if argv is None:
                continue
            if len(argv) >= 3 and argv[1] in ("-m", "run"):
                prefix = argv[:3]
            else:
                prefix = argv[: min(2, len(argv))]
            if prefix not in prefixes:
                prefixes.append(prefix)
        return tuple(prefixes)

@dataclass(frozen=True)
class ValidationRequest:
    diff_fingerprint: str
    commands: tuple[ValidationCommand, ...]
    attempt_number: int = 1

    def __post_init__(self) -> None:
        if not SHA256_PATTERN.fullmatch(self.diff_fingerprint):
            raise ValidationMatrixError(
                "validation request requires a SHA-256 diff fingerprint"
            )
        if not self.commands:
            raise ValidationMatrixError(
                "validation request requires at least one command"
            )
        displays = tuple(command.display for command in self.commands)
        if len(set(displays)) != len(displays):
            raise ValidationMatrixError(
                "validation request commands must be unique"
            )
        if (
            isinstance(self.attempt_number, bool)
            or not isinstance(self.attempt_number, int)
            or self.attempt_number < 1
        ):
            raise ValidationMatrixError(
                "validation request attempt_number must be a positive integer"
            )

    @property
    def expected_commands(self) -> tuple[str, ...]:
        return tuple(command.display for command in self.commands)


def select_validation_request(
    matrix: ValidationMatrix,
    *,
    diff_fingerprint: str,
    changed_paths: Iterable[str],
    findings: Iterable[FindingRecord] = (),
) -> ValidationRequest:
    paths = tuple(sorted(set(changed_paths)))
    commands: list[ValidationCommand] = []
    if matrix.default_command is not None:
        commands.append(matrix.default_command)
    for rule in matrix.rules:
        try:
            if any(matches_path_patterns(path, rule.patterns) for path in paths):
                commands.append(rule.command)
        except ValueError as exc:
            raise ValidationMatrixError(str(exc)) from exc
    commands.extend(
        _finding_validation_commands(
            findings, allowed_prefixes=matrix.finding_command_prefixes
        )
    )
    commands.extend(
        ValidationCommand(artifact_pattern=pattern)
        for pattern in matrix.required_artifacts
    )
    if matrix.product_command is not None:
        commands.append(matrix.product_command)
    unique: dict[tuple[str, ...], ValidationCommand] = {}
    for command in commands:
        canonical_argv = _canonical_argv(command)
        key = canonical_argv or ("shell", command.display)
        unique.setdefault(key, command)
    return ValidationRequest(diff_fingerprint, tuple(unique.values()))


def _finding_validation_commands(
    findings: Iterable[FindingRecord],
    *,
    allowed_prefixes: tuple[tuple[str, ...], ...],
) -> tuple[ValidationCommand, ...]:
    commands: list[ValidationCommand] = []
    for finding in project_open_set(tuple(findings)).findings:
        command = finding_validation_command(finding)
        if command is None:
            continue
        if not any(
            matches_validation_family(command.argv, prefix)
            for prefix in allowed_prefixes
        ):
            allowed = ", ".join(shlex.join(prefix) for prefix in allowed_prefixes)
            raise ValidationMatrixError(
                f"finding {finding.finding_id} VALIDATE command is outside configured "
                f"validation families: {allowed or 'NONE'}"
            )
        commands.append(command)
    return tuple(commands)


def finding_validation_command(
    finding: FindingRecord,
) -> ValidationCommand | None:
    """Return only the explicit typed directive; prose is deliberately opaque."""

    acceptance = finding.acceptance_test.strip()
    if not acceptance.startswith(FINDING_COMMAND_PREFIX):
        return None
    if finding.finding_class is not FindingClass.BLOCKER:
        LOGGER.warning(
            "Ignoring VALIDATE directive from non-blocking finding %s; "
            "only BLOCKER findings may extend the validation matrix",
            finding.finding_id,
        )
        return None
    payload = acceptance[len(FINDING_COMMAND_PREFIX) :].strip()
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValidationMatrixError(
            f"finding {finding.finding_id} has invalid VALIDATE JSON argv: {exc}"
        ) from exc
    if not isinstance(value, list) or not value or any(
        not isinstance(item, str) for item in value
    ):
        raise ValidationMatrixError(
            f"finding {finding.finding_id} VALIDATE command must be a non-empty JSON string array"
        )
    try:
        return _finding_validation_command(value)
    except ValidationMatrixError as exc:
        raise ValidationMatrixError(
            f"finding {finding.finding_id} has invalid VALIDATE command: {exc}"
        ) from exc


class ValidationMatrixRunner:
    """Execute one selected matrix without a shell unless explicitly configured."""

    def __init__(
        self,
        repository_root: Path,
        *,
        output_limit: int = VALIDATION_OUTPUT_LIMIT,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.repository_root = repository_root.resolve()
        if not self.repository_root.is_dir():
            raise ValidationMatrixError("validation repository root must be a directory")
        if isinstance(output_limit, bool) or not isinstance(output_limit, int) or output_limit < 1:
            raise ValidationMatrixError("validation output_limit must be positive")
        if not callable(monotonic):
            raise ValidationMatrixError("validation monotonic clock must be callable")
        self.output_limit = output_limit
        self._monotonic = monotonic

    def run(self, request: ValidationRequest) -> ValidationAttestation:
        runs = [self._run_once(request.commands)]
        if runs[0].failed:
            total_runs = _flake_probe_run_count(runs[0].elapsed_seconds)
            runs.extend(
                self._run_once(request.commands)
                for _ in range(1, total_runs)
            )
        return self._attestation(request, tuple(runs))

    def _run_once(
        self,
        commands: tuple[ValidationCommand, ...],
    ) -> "_ValidationRun":
        started = self._monotonic()
        records: list[ValidationRecord] = []
        captures: list[ValidationCapture] = []
        unavailable = 0
        for command in commands:
            if command.artifact_pattern is not None:
                record, capture = self._check_artifact(command.artifact_pattern)
                records.append(record)
                captures.append(capture)
                continue
            try:
                result = subprocess.run(
                    command.shell_command if command.shell_command is not None else command.argv,
                    cwd=self.repository_root,
                    shell=command.shell_command is not None,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=command.timeout_seconds,
                    check=False,
                )
            except FileNotFoundError as exc:
                unavailable += 1
                captures.append(
                    ValidationCapture(
                        command.display,
                        "missing",
                        -1,
                        "",
                        str(exc),
                        "",
                    )
                )
                continue
            except subprocess.TimeoutExpired as exc:
                stdout = _timeout_text(exc.stdout)
                stderr = _timeout_text(exc.stderr)
                compact = _compact_output(stdout, stderr, self.output_limit)
                records.append(
                    ValidationRecord(
                        ValidationStatus.FAIL, command.display, 124, compact
                    )
                )
                captures.append(
                    ValidationCapture(
                        command.display,
                        "timeout",
                        124,
                        stdout,
                        stderr,
                        compact,
                    )
                )
                continue
            except OSError as exc:
                unavailable += 1
                captures.append(
                    ValidationCapture(
                        command.display,
                        "unavailable",
                        -1,
                        "",
                        str(exc),
                        "",
                    )
                )
                continue
            status = (
                ValidationStatus.PASS
                if result.returncode == 0
                else ValidationStatus.FAIL
            )
            compact = _compact_output(
                result.stdout, result.stderr, self.output_limit
            )
            records.append(
                ValidationRecord(
                    status, command.display, result.returncode, compact
                )
            )
            captures.append(
                ValidationCapture(
                    command.display,
                    status.value.lower(),
                    result.returncode,
                    result.stdout,
                    result.stderr,
                    compact,
                )
            )
        return _ValidationRun(
            records=tuple(records),
            captures=tuple(captures),
            unavailable=unavailable,
            elapsed_seconds=max(0.0, self._monotonic() - started),
        )

    def _check_artifact(
        self, pattern: str
    ) -> tuple[ValidationRecord, ValidationCapture]:
        """Measure one declared build-output glob without product knowledge."""

        matched: list[str] = []
        empty: list[str] = []
        non_regular: list[str] = []
        unsafe: list[str] = []
        error: str | None = None
        try:
            candidates = sorted(
                self.repository_root.glob(pattern),
                key=lambda item: item.as_posix(),
            )
            for candidate in candidates:
                relative = candidate.relative_to(self.repository_root).as_posix()
                try:
                    resolved = candidate.resolve(strict=True)
                    if (
                        not resolved.is_relative_to(self.repository_root)
                        or candidate.is_symlink()
                    ):
                        unsafe.append(relative)
                    elif not candidate.is_file():
                        non_regular.append(relative)
                    else:
                        matched.append(relative)
                        if candidate.stat().st_size == 0:
                            empty.append(relative)
                except OSError as exc:
                    unsafe.append(relative)
                    error = str(exc)
        except (OSError, RuntimeError, ValueError) as exc:
            error = str(exc)
        passed = bool(matched) and not empty and not non_regular and not unsafe and error is None
        fact = json.dumps(
            {
                "declaration": pattern,
                "empty_paths": empty,
                "error": error,
                "matched_paths": matched,
                "non_regular_paths": non_regular,
                "status": "PASS" if passed else "FAIL",
                "unsafe_paths": unsafe,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        command = shlex.join((ARTIFACT_CHECK_COMMAND, pattern))
        status = ValidationStatus.PASS if passed else ValidationStatus.FAIL
        exit_code = 0 if passed else 1
        compact = _compact_output(fact, "", self.output_limit)
        return (
            ValidationRecord(status, command, exit_code, compact),
            ValidationCapture(
                command, status.value.lower(), exit_code, fact, "", compact
            ),
        )

    def _attestation(
        self,
        request: ValidationRequest,
        runs: tuple["_ValidationRun", ...],
    ) -> ValidationAttestation:
        if len(runs) == 1:
            run = runs[0]
            failed = sum(
                record.status is ValidationStatus.FAIL for record in run.records
            )
            passed = sum(
                record.status is ValidationStatus.PASS for record in run.records
            )
            summary = (
                f"{passed} passed; {failed} failed; {run.unavailable} unavailable; "
                f"{len(request.commands)} required"
            )
            return ValidationAttestation(
                attestation_id=validation_attestation_id(request),
                diff_fingerprint=request.diff_fingerprint,
                expected_commands=request.expected_commands,
                records=run.records,
                output_digest=validation_output_digest(run.captures),
                summary=summary,
                command_specs=tuple(
                    command.command_spec for command in request.commands
                ),
                content_captures=run.captures,
            )

        records: list[ValidationRecord] = []
        captures: list[ValidationCapture] = []
        for command_index, command in enumerate(request.commands):
            attempts = tuple(run.captures[command_index] for run in runs)
            aggregate = _aggregate_capture(
                command.display,
                attempts,
                output_limit=self.output_limit,
            )
            captures.append(aggregate)
            failed_attempt = next(
                (
                    attempt
                    for attempt in attempts
                    if attempt.outcome in {"fail", "timeout"}
                ),
                None,
            )
            unavailable_attempt = next(
                (
                    attempt
                    for attempt in attempts
                    if attempt.outcome in {"missing", "unavailable"}
                ),
                None,
            )
            if failed_attempt is not None:
                records.append(
                    ValidationRecord(
                        ValidationStatus.FAIL,
                        command.display,
                        failed_attempt.exit_code,
                        aggregate.compact_output,
                    )
                )
            elif unavailable_attempt is not None:
                records.append(
                    ValidationRecord(
                        ValidationStatus.FAIL,
                        command.display,
                        unavailable_attempt.exit_code,
                        aggregate.compact_output,
                    )
                )
            else:
                records.append(
                    ValidationRecord(
                        ValidationStatus.PASS,
                        command.display,
                        0,
                        aggregate.compact_output,
                    )
                )

        failed_runs = sum(run.failed for run in runs)
        passed_runs = sum(run.passed for run in runs)
        incomplete_runs = len(runs) - failed_runs - passed_runs
        summary = (
            f"{failed_runs} of {len(runs)} validation runs failed; "
            f"{passed_runs} passed; {incomplete_runs} incomplete; "
            f"{len(request.commands)} required commands"
        )
        return ValidationAttestation(
            attestation_id=validation_attestation_id(request),
            diff_fingerprint=request.diff_fingerprint,
            expected_commands=request.expected_commands,
            records=tuple(records),
            output_digest=validation_output_digest(captures),
            summary=summary,
            command_specs=tuple(command.command_spec for command in request.commands),
            content_captures=tuple(captures),
        )


@dataclass(frozen=True)
class _ValidationRun:
    records: tuple[ValidationRecord, ...]
    captures: tuple[ValidationCapture, ...]
    unavailable: int
    elapsed_seconds: float

    @property
    def failed(self) -> bool:
        return any(
            capture.outcome in {"fail", "timeout"} for capture in self.captures
        )

    @property
    def passed(self) -> bool:
        return not self.failed and self.unavailable == 0


def _flake_probe_run_count(first_run_seconds: float) -> int:
    """Choose a fixed flake-probe size from the measured failed first run."""
    if first_run_seconds <= 0:
        return FLAKE_PROBE_MAX_RUNS
    affordable_runs = int(FLAKE_PROBE_BUDGET_SECONDS // first_run_seconds)
    return max(2, min(FLAKE_PROBE_MAX_RUNS, affordable_runs))


def _aggregate_capture(
    command: str,
    attempts: tuple[ValidationCapture, ...],
    *,
    output_limit: int,
) -> ValidationCapture:
    """Aggregate a bounded probe without losing any run's raw streams."""
    failed = tuple(
        attempt for attempt in attempts if attempt.outcome in {"fail", "timeout"}
    )
    unavailable = tuple(
        attempt
        for attempt in attempts
        if attempt.outcome in {"missing", "unavailable"}
    )
    if failed:
        outcome = "fail" if any(item.outcome == "fail" for item in failed) else "timeout"
        exit_code = failed[0].exit_code
    elif unavailable:
        # Once a failed first matrix run has opened a probe, later
        # unavailability must not project the already-proven failure as an
        # incomplete attestation.  The numbered raw headers retain the exact
        # missing/unavailable outcomes.
        outcome = "fail"
        exit_code = unavailable[0].exit_code
    else:
        outcome = "pass"
        exit_code = 0
    return ValidationCapture(
        command=command,
        outcome=outcome,
        exit_code=exit_code,
        stdout=_numbered_run_stream(attempts, stream="stdout"),
        stderr=_numbered_run_stream(attempts, stream="stderr"),
        compact_output=_numbered_compact_output(attempts, output_limit),
    )


def _run_header(
    index: int,
    total: int,
    attempt: ValidationCapture,
) -> str:
    return (
        f"=== validation run {index}/{total}: {attempt.outcome.upper()} "
        f"(exit={attempt.exit_code}) ==="
    )


def _numbered_run_stream(
    attempts: tuple[ValidationCapture, ...],
    *,
    stream: str,
) -> str:
    parts: list[str] = []
    for index, attempt in enumerate(attempts, start=1):
        value = getattr(attempt, stream)
        parts.append(
            _run_header(index, len(attempts), attempt)
            + "\n"
            + (value if value else f"(no {stream})")
        )
    return "\n".join(parts)


def _numbered_compact_output(
    attempts: tuple[ValidationCapture, ...],
    output_limit: int,
) -> str:
    headers = tuple(
        _run_header(index, len(attempts), attempt)
        for index, attempt in enumerate(attempts, start=1)
    )
    fixed_size = sum(len(header) for header in headers) + (2 * len(headers) - 1)
    if fixed_size >= output_limit:
        return _compact_output("\n".join(headers), "", output_limit)
    payload_budget = output_limit - fixed_size
    base, remainder = divmod(payload_budget, len(attempts))
    parts = []
    for index, (header, attempt) in enumerate(zip(headers, attempts, strict=True)):
        budget = base + (1 if index < remainder else 0)
        parts.append(
            header + "\n" + _compact_output(attempt.stdout, attempt.stderr, budget)
        )
    return "\n".join(parts)


def validation_attestation_id(request: ValidationRequest) -> str:
    """Return the immutable validation identity for one exact matrix attempt."""
    if request.attempt_number == 1:
        return f"validation-{request.diff_fingerprint[:12]}"
    return (
        f"validation-{request.diff_fingerprint[:12]}-retry-"
        f"{request.attempt_number}"
    )


def _timeout_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _compact_output(stdout: str, stderr: str, limit: int) -> str:
    combined = "\n".join(part.strip() for part in (stdout, stderr) if part.strip())
    combined = combined.replace("\x00", "\\0")
    if not combined:
        return "(no output)"
    if len(combined) <= limit:
        return combined
    omitted = len(combined) - limit
    head_length = max(1, limit // 2)
    tail_length = limit - head_length
    head = combined[:head_length]
    tail = combined[-tail_length:] if tail_length else ""
    return f"{head}\n...[{omitted} characters omitted]...\n{tail}"
