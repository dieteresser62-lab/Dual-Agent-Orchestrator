from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest
import validation_matrix

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
    ValidationRequest,
    ValidationRule,
    _flake_probe_run_count,
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


def test_declared_artifacts_and_product_stage_follow_the_build_command(
    tmp_path: Path,
) -> None:
    build = ValidationCommand(
        argv=(
            sys.executable,
            "-c",
            "from pathlib import Path; Path('dist').mkdir(exist_ok=True); "
            "Path('dist/app.css').write_text('body{}')",
        )
    )
    product = ValidationCommand(
        argv=(sys.executable, "-c", "print('product-ok')"),
        timeout_seconds=17,
    )
    request = select_validation_request(
        ValidationMatrix(
            default_command=build,
            required_artifacts=("dist/**/*.css",),
            product_command=product,
        ),
        diff_fingerprint=FINGERPRINT,
        changed_paths=("src/app.ts",),
    )

    attestation = ValidationMatrixRunner(tmp_path).run(request)

    assert request.commands[-2].artifact_pattern == "dist/**/*.css"
    assert request.commands[-1] is product
    assert attestation.passed
    artifact_fact = json.loads(attestation.content_captures[-2].stdout)
    assert artifact_fact == {
        "declaration": "dist/**/*.css",
        "empty_paths": [],
        "error": None,
        "matched_paths": ["dist/app.css"],
        "non_regular_paths": [],
        "status": "PASS",
        "unsafe_paths": [],
    }
    assert attestation.records[-1].command == product.display
    assert "product-ok" in attestation.content_captures[-1].stdout


@pytest.mark.parametrize("empty", (False, True))
def test_missing_or_empty_declared_artifact_is_a_failed_record_fact(
    tmp_path: Path,
    empty: bool,
) -> None:
    if empty:
        (tmp_path / "dist").mkdir()
        (tmp_path / "dist" / "app.css").write_bytes(b"")
    request = select_validation_request(
        ValidationMatrix(
            default_command=ValidationCommand(
                argv=(sys.executable, "-c", "print('build-complete')")
            ),
            required_artifacts=("dist/**/*.css",),
        ),
        diff_fingerprint=FINGERPRINT,
        changed_paths=("src/app.ts",),
    )

    attestation = ValidationMatrixRunner(tmp_path).run(request)

    assert attestation.complete
    assert attestation.status is ValidationAttestationStatus.FAIL
    artifact_record = next(
        record
        for record in attestation.records
        if record.command.startswith("internal:artifact-delivery")
    )
    assert artifact_record.status is ValidationStatus.FAIL
    assert ("empty_paths" if empty else "matched_paths") in artifact_record.output


def test_product_stage_has_no_embedded_browser_http_or_project_client() -> None:
    tree = ast.parse(Path(validation_matrix.__file__).read_text(encoding="utf-8"))
    imports = {
        name
        for node in ast.walk(tree)
        for name in (
            tuple(alias.name.split(".", 1)[0] for alias in node.names)
            if isinstance(node, ast.Import)
            else (str(node.module).split(".", 1)[0],)
            if isinstance(node, ast.ImportFrom)
            else ()
        )
    }

    assert imports.isdisjoint(
        {
            "http",
            "httpx",
            "playwright",
            "pyppeteer",
            "requests",
            "selenium",
            "urllib",
        }
    )


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


def test_simple_shell_default_authorizes_equivalent_finding_argv_once() -> None:
    finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="full validation must pass after correction",
        acceptance_test='VALIDATE: ["npm","test"]',
        origin=FindingOrigin("13", 1, AgentRole.CLAUDE),
    )

    request = select_validation_request(
        ValidationMatrix(
            default_command=ValidationCommand(shell_command="npm test")
        ),
        diff_fingerprint=FINGERPRINT,
        changed_paths=("src/app.js",),
        findings=(finding,),
    )

    assert request.expected_commands == ("shell: npm test",)


def test_legacy_shell_marker_from_reviewer_is_normalized_and_deduplicated() -> None:
    finding = FindingRecord(
        finding_id="C-05",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="full validation must pass after correction",
        acceptance_test='VALIDATE: ["shell","npm test"]',
        origin=FindingOrigin("04", 1, AgentRole.CLAUDE),
    )

    request = select_validation_request(
        ValidationMatrix(
            default_command=ValidationCommand(shell_command="npm test")
        ),
        diff_fingerprint=FINGERPRINT,
        changed_paths=("src/app.js",),
        findings=(finding,),
    )

    assert request.expected_commands == ("shell: npm test",)


def test_npm_test_family_allows_focused_test_script_from_legacy_shell_marker() -> None:
    finding = FindingRecord(
        finding_id="C-08",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="browser gate must be attested",
        acceptance_test='VALIDATE: ["shell","npm run test:browser"]',
        origin=FindingOrigin("FINAL", 1, AgentRole.CLAUDE),
    )

    request = select_validation_request(
        ValidationMatrix(
            default_command=ValidationCommand(shell_command="npm test")
        ),
        diff_fingerprint=FINGERPRINT,
        changed_paths=("src/app.js",),
        findings=(finding,),
    )

    assert request.expected_commands == (
        "shell: npm test",
        "npm run test:browser",
    )


@pytest.mark.parametrize(
    "argv",
    (
        '["npm","run","build"]',
        '["npm","run","test:browser","--","--update"]',
        '["npm","exec","playwright","test"]',
    ),
)
def test_npm_test_family_rejects_non_test_scripts_and_extra_arguments(
    argv: str,
) -> None:
    finding = FindingRecord(
        finding_id="C-08",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="untrusted npm extension",
        acceptance_test=f"VALIDATE: {argv}",
        origin=FindingOrigin("FINAL", 1, AgentRole.CLAUDE),
    )

    with pytest.raises(ValidationMatrixError, match="outside configured"):
        select_validation_request(
            ValidationMatrix(
                default_command=ValidationCommand(shell_command="npm test")
            ),
            diff_fingerprint=FINGERPRINT,
            changed_paths=("src/app.js",),
            findings=(finding,),
        )


@pytest.mark.parametrize(
    "shell_command",
    (
        "npm test && echo unsafe",
        "npm test > result.log",
        "npm test $EXTRA_ARGS",
    ),
)
def test_legacy_shell_marker_rejects_shell_syntax(shell_command: str) -> None:
    finding = FindingRecord(
        finding_id="C-05",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="untrusted shell extension",
        acceptance_test=f"VALIDATE: {json.dumps(['shell', shell_command])}",
        origin=FindingOrigin("04", 1, AgentRole.CLAUDE),
    )

    with pytest.raises(
        ValidationMatrixError,
        match="simple command without shell syntax",
    ):
        select_validation_request(
            ValidationMatrix(
                default_command=ValidationCommand(shell_command="npm test")
            ),
            diff_fingerprint=FINGERPRINT,
            changed_paths=("src/app.js",),
            findings=(finding,),
        )


@pytest.mark.parametrize(
    "shell_command",
    (
        "npm test && echo unsafe",
        "npm test > result.log",
        "VALIDATION_MODE=full npm test",
        "npm test $EXTRA_ARGS",
        "npm test # ignore the remainder",
        "npm test ~/fixture",
    ),
)
def test_shell_syntax_does_not_authorize_finding_commands(
    shell_command: str,
) -> None:
    finding = FindingRecord(
        finding_id="C-01",
        finding_class=FindingClass.BLOCKER,
        status=FindingStatus.OPEN,
        summary="untrusted extension",
        acceptance_test='VALIDATE: ["npm","test"]',
        origin=FindingOrigin("13", 1, AgentRole.CLAUDE),
    )

    with pytest.raises(
        ValidationMatrixError,
        match="outside configured validation families: NONE",
    ):
        select_validation_request(
            ValidationMatrix(
                default_command=ValidationCommand(shell_command=shell_command)
            ),
            diff_fingerprint=FINGERPRINT,
            changed_paths=("src/app.js",),
            findings=(finding,),
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
    assert "shell-ok" in attestation.content_captures[2].stdout
    assert attestation.command_specs[0].argv == commands[0].argv
    assert attestation.command_specs[1].argv == commands[1].argv
    assert attestation.command_specs[2].legacy_shell == commands[2].display
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


def _measured_clock(first_run_seconds: float, run_count: int):
    ticks = iter(
        (
            0.0,
            first_run_seconds,
            *(first_run_seconds for _ in range((run_count - 1) * 2)),
        )
    )
    return lambda: next(ticks)


def test_green_first_run_keeps_original_single_run_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_run(*args, **kwargs):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(args[0], 0, "green\n", "")

    monkeypatch.setattr(validation_matrix.subprocess, "run", fake_run)
    request = ValidationRequest(
        FINGERPRINT,
        (ValidationCommand(argv=("product-tests",)),),
    )

    attestation = ValidationMatrixRunner(
        tmp_path,
        monotonic=_measured_clock(5.0, 1),
    ).run(request)

    assert calls == 1
    assert attestation.passed
    assert attestation.summary == "1 passed; 0 failed; 0 unavailable; 1 required"
    assert attestation.records[0].output == "green"
    assert "validation run" not in attestation.content_captures[0].stdout


def test_failed_runs_use_runtime_bound_count_and_name_every_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outputs = iter(("red-one\n", "red-two\n", "red-three\n"))
    calls = 0

    def fake_run(*args, **kwargs):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(args[0], 7, next(outputs), "failure\n")

    monkeypatch.setattr(validation_matrix.subprocess, "run", fake_run)
    request = ValidationRequest(
        FINGERPRINT,
        (ValidationCommand(argv=("product-tests",)),),
    )

    attestation = ValidationMatrixRunner(
        tmp_path,
        monotonic=_measured_clock(8.0, 3),
    ).run(request)

    assert calls == 3
    assert attestation.status is ValidationAttestationStatus.FAIL
    assert "3 of 3 validation runs failed" in attestation.summary
    assert all(
        f"validation run {index}/3" in attestation.records[0].output
        for index in range(1, 4)
    )
    raw = attestation.content_captures[0]
    assert all(value in raw.stdout for value in ("red-one", "red-two", "red-three"))
    assert raw.stderr.count("failure") == 3


def test_mixed_probe_stays_failed_and_reports_failure_ratio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    return_codes = iter((1, 0, 0, 0, 0))
    calls = 0

    def fake_run(*args, **kwargs):
        nonlocal calls
        calls += 1
        return_code = next(return_codes)
        return subprocess.CompletedProcess(
            args[0],
            return_code,
            f"run-{calls}\n",
            "boom\n" if return_code else "",
        )

    monkeypatch.setattr(validation_matrix.subprocess, "run", fake_run)
    request = ValidationRequest(
        FINGERPRINT,
        (ValidationCommand(argv=("product-tests",)),),
    )

    attestation = ValidationMatrixRunner(
        tmp_path,
        monotonic=_measured_clock(5.0, 5),
    ).run(request)

    assert calls == 5
    assert attestation.status is ValidationAttestationStatus.FAIL
    assert "1 of 5 validation runs failed" in attestation.summary
    assert "4 passed" in attestation.summary
    assert attestation.records[0].status is ValidationStatus.FAIL
    assert all(
        f"validation run {index}/5" in attestation.records[0].output
        for index in range(1, 6)
    )
    assert all(
        f"run-{index}" in attestation.content_captures[0].stdout
        for index in range(1, 6)
    )


def test_probe_unavailability_cannot_displace_an_observed_matrix_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcomes = iter(
        (
            subprocess.CompletedProcess(("first",), 1, "", "first failed"),
            subprocess.CompletedProcess(("second",), 0, "ok", ""),
            subprocess.CompletedProcess(("first",), 0, "ok", ""),
            FileNotFoundError("second disappeared"),
        )
    )

    def fake_run(*args, **kwargs):
        outcome = next(outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(validation_matrix.subprocess, "run", fake_run)
    request = ValidationRequest(
        FINGERPRINT,
        (
            ValidationCommand(argv=("first",)),
            ValidationCommand(argv=("second",)),
        ),
    )

    attestation = ValidationMatrixRunner(
        tmp_path,
        monotonic=_measured_clock(420.0, 2),
    ).run(request)

    assert attestation.complete
    assert attestation.status is ValidationAttestationStatus.FAIL
    assert [record.status for record in attestation.records] == [
        ValidationStatus.FAIL,
        ValidationStatus.FAIL,
    ]
    assert "1 of 2 validation runs failed" in attestation.summary
    assert "1 incomplete" in attestation.summary
    assert "MISSING" in attestation.records[1].output


@pytest.mark.parametrize(
    ("first_run_seconds", "expected_runs"),
    ((5.0, 5), (8.0, 3), (420.0, 2)),
)
def test_flake_probe_size_is_derived_from_measured_matrix_runtime(
    first_run_seconds: float,
    expected_runs: int,
) -> None:
    assert _flake_probe_run_count(first_run_seconds) == expected_runs
