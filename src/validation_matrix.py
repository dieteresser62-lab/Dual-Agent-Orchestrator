from __future__ import annotations

import hashlib
import json
import logging
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from contracts import (
    FindingClass,
    FindingRecord,
    FindingStatus,
    SHA256_PATTERN,
    ValidationAttestation,
    ValidationRecord,
    ValidationStatus,
)
from gates import matches_path_patterns, normalize_path_patterns


DEFAULT_VALIDATION_TIMEOUT_SECONDS = 300
VALIDATION_OUTPUT_LIMIT = 2_000
FINDING_COMMAND_PREFIX = "VALIDATE:"
LOGGER = logging.getLogger(__name__)


class ValidationMatrixError(ValueError):
    """Raised when matrix selection or execution inputs are unsafe or ambiguous."""


@dataclass(frozen=True)
class ValidationCommand:
    argv: tuple[str, ...] = ()
    shell_command: str | None = None
    timeout_seconds: int = DEFAULT_VALIDATION_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        has_argv = bool(self.argv)
        has_shell = self.shell_command is not None
        if has_argv == has_shell:
            raise ValidationMatrixError(
                "validation command requires exactly one of argv or shell_command"
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
        assert self.shell_command is not None
        return f"shell: {self.shell_command.strip()}"


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

    def __post_init__(self) -> None:
        by_display: dict[str, ValidationCommand] = {}
        commands = (
            *((self.default_command,) if self.default_command is not None else ()),
            *(rule.command for rule in self.rules),
        )
        for command in commands:
            previous = by_display.setdefault(command.display, command)
            if previous.timeout_seconds != command.timeout_seconds:
                raise ValidationMatrixError(
                    f"validation command {command.display!r} has conflicting timeouts"
                )

    @property
    def finding_command_prefixes(self) -> tuple[tuple[str, ...], ...]:
        prefixes: list[tuple[str, ...]] = []
        commands = (
            *((self.default_command,) if self.default_command is not None else ()),
            *(rule.command for rule in self.rules),
        )
        for command in commands:
            if not command.argv:
                continue
            if len(command.argv) >= 3 and command.argv[1] in ("-m", "run"):
                prefix = command.argv[:3]
            else:
                prefix = command.argv[: min(2, len(command.argv))]
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
    unique: dict[str, ValidationCommand] = {}
    for command in commands:
        unique.setdefault(command.display, command)
    return ValidationRequest(diff_fingerprint, tuple(unique.values()))


def _finding_validation_commands(
    findings: Iterable[FindingRecord],
    *,
    allowed_prefixes: tuple[tuple[str, ...], ...],
) -> tuple[ValidationCommand, ...]:
    commands: list[ValidationCommand] = []
    for finding in sorted(findings, key=lambda item: item.finding_id):
        if finding.status is not FindingStatus.OPEN:
            continue
        acceptance = finding.acceptance_test.strip()
        if not acceptance.startswith(FINDING_COMMAND_PREFIX):
            continue
        if finding.finding_class is not FindingClass.BLOCKER:
            LOGGER.warning(
                "Ignoring VALIDATE directive from non-blocking finding %s; "
                "only BLOCKER findings may extend the validation matrix",
                finding.finding_id,
            )
            continue
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
        command = ValidationCommand(argv=tuple(value))
        if not any(
            command.argv[: len(prefix)] == prefix
            for prefix in allowed_prefixes
        ):
            allowed = ", ".join(shlex.join(prefix) for prefix in allowed_prefixes)
            raise ValidationMatrixError(
                f"finding {finding.finding_id} VALIDATE command is outside configured "
                f"validation families: {allowed or 'NONE'}"
            )
        commands.append(command)
    return tuple(commands)


class ValidationMatrixRunner:
    """Execute one selected matrix without a shell unless explicitly configured."""

    def __init__(self, repository_root: Path, *, output_limit: int = VALIDATION_OUTPUT_LIMIT):
        self.repository_root = repository_root.resolve()
        if not self.repository_root.is_dir():
            raise ValidationMatrixError("validation repository root must be a directory")
        if isinstance(output_limit, bool) or not isinstance(output_limit, int) or output_limit < 1:
            raise ValidationMatrixError("validation output_limit must be positive")
        self.output_limit = output_limit

    def run(self, request: ValidationRequest) -> ValidationAttestation:
        records: list[ValidationRecord] = []
        digest_items: list[dict[str, object]] = []
        unavailable = 0
        for command in request.commands:
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
                digest_items.append(
                    {"command": command.display, "outcome": "missing", "detail": str(exc)}
                )
                continue
            except subprocess.TimeoutExpired as exc:
                stdout = _timeout_text(exc.stdout)
                stderr = _timeout_text(exc.stderr)
                records.append(
                    ValidationRecord(
                        ValidationStatus.FAIL,
                        command.display,
                        124,
                        _compact_output(stdout, stderr, self.output_limit),
                    )
                )
                digest_items.append(
                    {
                        "command": command.display,
                        "outcome": "timeout",
                        "exit_code": 124,
                        "stdout": stdout,
                        "stderr": stderr,
                    }
                )
                continue
            except OSError as exc:
                unavailable += 1
                digest_items.append(
                    {"command": command.display, "outcome": "unavailable", "detail": str(exc)}
                )
                continue
            status = (
                ValidationStatus.PASS
                if result.returncode == 0
                else ValidationStatus.FAIL
            )
            records.append(
                ValidationRecord(
                    status,
                    command.display,
                    result.returncode,
                    _compact_output(result.stdout, result.stderr, self.output_limit),
                )
            )
            digest_items.append(
                {
                    "command": command.display,
                    "outcome": status.value.lower(),
                    "exit_code": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }
            )
        digest_payload = json.dumps(
            digest_items,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        failed = sum(record.status is ValidationStatus.FAIL for record in records)
        passed = sum(record.status is ValidationStatus.PASS for record in records)
        summary = (
            f"{passed} passed; {failed} failed; {unavailable} unavailable; "
            f"{len(request.commands)} required"
        )
        return ValidationAttestation(
            attestation_id=(
                f"validation-{request.diff_fingerprint[:12]}"
                if request.attempt_number == 1
                else f"validation-{request.diff_fingerprint[:12]}-retry-{request.attempt_number}"
            ),
            diff_fingerprint=request.diff_fingerprint,
            expected_commands=request.expected_commands,
            records=tuple(records),
            output_digest=hashlib.sha256(digest_payload).hexdigest(),
            summary=summary,
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
