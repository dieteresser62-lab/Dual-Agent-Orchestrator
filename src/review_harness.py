#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Sequence


DEFAULT_OUTPUT_LIMIT = 6000


def _run(
    command: str | list[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: int,
    shell: bool = False,
) -> tuple[int, str]:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=shell,
        )
        combined = "\n".join(
            part.strip() for part in (result.stdout or "", result.stderr or "") if part.strip()
        )
        return result.returncode, combined
    except Exception as exc:
        return 1, str(exc)


def _tail(text: str, limit: int) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return "...[truncated]\n" + value[-limit:]


def _success_summary(text: str, max_lines: int = 6) -> str:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    return "\n".join(lines[-max_lines:])


def _write_probe(repo_root: Path, probe_path: str) -> tuple[bool, str]:
    candidate = (repo_root / probe_path).resolve()
    try:
        candidate.relative_to(repo_root)
    except ValueError:
        return False, "probe path escapes repository root"
    if not candidate.is_file():
        return False, f"probe file does not exist: {probe_path}"
    try:
        descriptor = os.open(candidate, os.O_WRONLY)
    except PermissionError:
        return True, "write access denied as required"
    except OSError as exc:
        return True, f"write access blocked: {exc}"
    else:
        os.close(descriptor)
        return False, "write access unexpectedly succeeded"


def run_review_checks(
    *,
    repo_root: Path,
    test_command: str,
    probe_path: str,
    timeout: int,
    environ: Mapping[str, str],
    output_limit: int = DEFAULT_OUTPUT_LIMIT,
) -> tuple[int, dict[str, object]]:
    root = repo_root.resolve()
    env = dict(environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    existing_pytest_options = env.get("PYTEST_ADDOPTS", "").strip()
    if "no:cacheprovider" not in existing_pytest_options:
        env["PYTEST_ADDOPTS"] = (
            f"{existing_pytest_options} -p no:cacheprovider".strip()
        )

    test_rc, test_output = _run(
        test_command,
        cwd=root,
        env=env,
        timeout=timeout,
        shell=True,
    )
    diff_rc, diff_output = _run(
        ["git", "diff", "--check", "HEAD", "--"],
        cwd=root,
        env=env,
        timeout=30,
    )
    status_rc, status_output = _run(
        ["git", "status", "--short"], cwd=root, env=env, timeout=30
    )
    probe_ok, probe_detail = _write_probe(root, probe_path)

    passed = test_rc == 0 and diff_rc == 0 and status_rc == 0 and probe_ok
    payload: dict[str, object] = {
        "status": "PASS" if passed else "FAIL",
        "test": {
            "command": test_command,
            "exit_code": test_rc,
            "output": _success_summary(test_output) if test_rc == 0 else _tail(test_output, output_limit),
        },
        "git_diff_check": {
            "exit_code": diff_rc,
            "output": _tail(diff_output, output_limit),
        },
        "git_status": {
            "exit_code": status_rc,
            "output": _tail(status_output, output_limit),
        },
        "write_probe": {
            "path": probe_path,
            "blocked": probe_ok,
            "detail": probe_detail,
        },
    }
    return (0 if passed else 1), payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the single bounded validation command allowed to read-only reviewers."
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root (default: current directory).",
    )
    parser.add_argument(
        "--test-command",
        default=None,
        help="Configured test command (default: RUN_TASK_REVIEW_TEST_COMMAND).",
    )
    parser.add_argument(
        "--probe-path",
        default=None,
        help="Tracked file used for the non-mutating write-open probe (default: RUN_TASK_REVIEW_PROBE_PATH or README.md).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Test timeout in seconds (default: RUN_TASK_REVIEW_TIMEOUT or 1800).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    test_command = args.test_command
    if test_command is None:
        test_command = os.environ.get("RUN_TASK_REVIEW_TEST_COMMAND", "").strip()
    if not test_command:
        print(json.dumps({"status": "FAIL", "error": "review test command is empty"}))
        return 2

    probe_path = args.probe_path or os.environ.get(
        "RUN_TASK_REVIEW_PROBE_PATH", "README.md"
    )
    timeout_value = args.timeout
    if timeout_value is None:
        try:
            timeout_value = int(os.environ.get("RUN_TASK_REVIEW_TIMEOUT", "1800"))
        except ValueError:
            timeout_value = 0
    if timeout_value <= 0:
        print(json.dumps({"status": "FAIL", "error": "review timeout must be positive"}))
        return 2

    rc, payload = run_review_checks(
        repo_root=Path(args.repo_root),
        test_command=test_command,
        probe_path=probe_path,
        timeout=timeout_value,
        environ=os.environ,
    )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
