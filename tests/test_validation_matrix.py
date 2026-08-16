from __future__ import annotations

import sys
from pathlib import Path

import pytest

from contracts import (
    AgentRole,
    FindingClass,
    FindingOrigin,
    FindingRecord,
    FindingStatus,
    ValidationAttestationStatus,
    ValidationStatus,
)
from validation_matrix import (
    ValidationCommand,
    ValidationMatrix,
    ValidationMatrixError,
    ValidationMatrixRunner,
    ValidationRule,
    select_validation_request,
)


FINGERPRINT = "a" * 64


def _matrix() -> ValidationMatrix:
    return ValidationMatrix(
        default_command=ValidationCommand(argv=("npm", "test")),
        rules=(
            ValidationRule(
                ("engine/**",),
                ValidationCommand(argv=("npm", "run", "build:engine")),
            ),
            ValidationRule(
                ("docs/**", "engine/**"),
                ValidationCommand(argv=("npm", "run", "lint:links")),
            ),
        ),
    )


def test_selects_default_and_every_matching_path_rule_once() -> None:
    request = select_validation_request(
        _matrix(),
        diff_fingerprint=FINGERPRINT,
        changed_paths=("engine/core.ts", "docs/design.md"),
    )

    assert request.expected_commands == (
        "npm test",
        "npm run build:engine",
        "npm run lint:links",
    )


def test_no_path_match_selects_only_default_and_rename_source_can_match() -> None:
    default_only = select_validation_request(
        _matrix(), diff_fingerprint=FINGERPRINT, changed_paths=("src/app.py",)
    )
    renamed = select_validation_request(
        _matrix(),
        diff_fingerprint=FINGERPRINT,
        changed_paths=("archive/core.ts", "engine/core.ts"),
    )

    assert default_only.expected_commands == ("npm test",)
    assert "npm run build:engine" in renamed.expected_commands


def test_identical_commands_from_multiple_matching_rules_are_deduplicated() -> None:
    command = ValidationCommand(argv=("npm", "test"))
    request = select_validation_request(
        ValidationMatrix(
            default_command=command,
            rules=(ValidationRule(("src/**",), command),),
        ),
        diff_fingerprint=FINGERPRINT,
        changed_paths=("src/app.py",),
    )

    assert request.expected_commands == ("npm test",)


def test_identical_commands_with_conflicting_timeouts_fail_closed() -> None:
    with pytest.raises(ValidationMatrixError, match="conflicting timeouts"):
        ValidationMatrix(
            default_command=ValidationCommand(argv=("npm", "test"), timeout_seconds=10),
            rules=(
                ValidationRule(
                    ("src/**",),
                    ValidationCommand(argv=("npm", "test"), timeout_seconds=20),
                ),
            ),
        )


def test_open_finding_can_add_structured_acceptance_command() -> None:
    finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="focused regression required",
        acceptance_test='VALIDATE: ["python3","-m","pytest","tests/test_engine.py","-q"]',
        origin=FindingOrigin("13", 1, AgentRole.CLAUDE),
    )

    request = select_validation_request(
        ValidationMatrix(
            default_command=ValidationCommand(
                argv=("python3", "-m", "pytest", "tests/", "-v")
            )
        ),
        diff_fingerprint=FINGERPRINT,
        changed_paths=("src/app.py",),
        findings=(finding,),
    )

    assert request.expected_commands[-1] == (
        "python3 -m pytest tests/test_engine.py -q"
    )


@pytest.mark.parametrize(
    "acceptance_test",
    (
        'VALIDATE: ["node","tests/run-tests.mjs","--only","browser-smoke.test.mjs"]',
        'VALIDATE: ["npm","run","lint:links"]',
        "VALIDATE: not-json",
    ),
)
def test_observation_validate_directive_never_extends_or_blocks_matrix(
    acceptance_test: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.OBSERVATION,
        status=FindingStatus.OPEN,
        summary="non-blocking follow-up",
        acceptance_test=acceptance_test,
        origin=FindingOrigin("13", 1, AgentRole.CLAUDE),
    )

    request = select_validation_request(
        _matrix(),
        diff_fingerprint=FINGERPRINT,
        changed_paths=("src/app.py",),
        findings=(finding,),
    )

    assert request.expected_commands == ("npm test",)
    assert "Ignoring VALIDATE directive from non-blocking finding C-01" in caplog.text


@pytest.mark.parametrize(
    "argv",
    (
        '["/bin/sh","-c","echo pwned"]',
        '["rm","-rf","tmp"]',
        '["python3","-c","print(1)"]',
    ),
)
def test_finding_command_must_stay_in_configured_validation_family(argv: str) -> None:
    finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="unsafe command requested",
        acceptance_test=f"VALIDATE: {argv}",
        origin=FindingOrigin("13", 1, AgentRole.CLAUDE),
    )
    matrix = ValidationMatrix(
        default_command=ValidationCommand(
            argv=("python3", "-m", "pytest", "tests/", "-v")
        )
    )

    with pytest.raises(ValidationMatrixError, match="outside configured"):
        select_validation_request(
            matrix,
            diff_fingerprint=FINGERPRINT,
            changed_paths=("src/app.py",),
            findings=(finding,),
        )


def test_invalid_finding_validate_directive_fails_closed() -> None:
    finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="focused regression required",
        acceptance_test="VALIDATE: pytest tests/test_engine.py",
        origin=FindingOrigin("13", 1, AgentRole.CLAUDE),
    )

    with pytest.raises(ValidationMatrixError, match="invalid VALIDATE JSON"):
        select_validation_request(
            _matrix(),
            diff_fingerprint=FINGERPRINT,
            changed_paths=("src/app.py",),
            findings=(finding,),
        )


def test_runner_captures_pass_failure_compact_output_and_digest(tmp_path: Path) -> None:
    commands = (
        ValidationCommand(
            argv=(sys.executable, "-c", "print('x' * 80)"), timeout_seconds=5
        ),
        ValidationCommand(
            argv=(sys.executable, "-c", "import sys; print('red'); sys.exit(3)"),
            timeout_seconds=5,
        ),
        ValidationCommand(
            shell_command=f"{sys.executable} -c \"print('shell-ok', end='')\"",
            timeout_seconds=5,
        ),
    )

    attestation = ValidationMatrixRunner(tmp_path, output_limit=20).run(
        select_validation_request(
            ValidationMatrix(default_command=commands[0], rules=(
                ValidationRule(("src/**",), commands[1]),
                ValidationRule(("src/**",), commands[2]),
            )),
            diff_fingerprint=FINGERPRINT,
            changed_paths=("src/app.py",),
        )
    )

    assert attestation.complete is True
    assert attestation.status is ValidationAttestationStatus.FAIL
    assert [record.status for record in attestation.records] == [
        ValidationStatus.PASS,
        ValidationStatus.FAIL,
        ValidationStatus.PASS,
    ]
    assert "characters omitted" in attestation.records[0].output
    assert attestation.records[1].exit_code == 3
    assert attestation.records[2].output == "shell-ok"
    assert len(attestation.output_digest) == 64


def test_missing_binary_is_incomplete_and_timeout_is_complete_failure(tmp_path: Path) -> None:
    missing_request = select_validation_request(
        ValidationMatrix(
            default_command=ValidationCommand(argv=("dao-command-that-does-not-exist",))
        ),
        diff_fingerprint=FINGERPRINT,
        changed_paths=("src/app.py",),
    )
    timeout_request = select_validation_request(
        ValidationMatrix(
            default_command=ValidationCommand(
                argv=(sys.executable, "-c", "import time; time.sleep(5)"),
                timeout_seconds=1,
            )
        ),
        diff_fingerprint="b" * 64,
        changed_paths=("src/app.py",),
    )

    missing = ValidationMatrixRunner(tmp_path).run(missing_request)
    timed_out = ValidationMatrixRunner(tmp_path).run(timeout_request)

    assert missing.status is ValidationAttestationStatus.INCOMPLETE
    assert missing.records == ()
    assert "1 unavailable" in missing.summary
    assert timed_out.complete is True
    assert timed_out.status is ValidationAttestationStatus.FAIL
    assert timed_out.records[0].exit_code == 124
