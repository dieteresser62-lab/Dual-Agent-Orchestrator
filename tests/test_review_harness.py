from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import review_harness
from review_harness import main, run_review_checks


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    readme = repo / "README.md"
    readme.write_text("review target\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Review Harness Test",
            "-c",
            "user.email=review-harness@example.invalid",
            "commit",
            "-qm",
            "initial",
        ],
        cwd=repo,
        check=True,
    )
    readme.chmod(0o444)
    return repo


def test_review_harness_runs_once_compacts_success_and_proves_read_only(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    rc, payload = run_review_checks(
        repo_root=repo,
        test_command=f'{sys.executable} -c "print(\'131 passed\')"',
        probe_path="README.md",
        timeout=30,
        environ={},
    )

    assert rc == 0
    assert payload["status"] == "PASS"
    assert payload["test"]["exit_code"] == 0  # type: ignore[index]
    assert "131 passed" in payload["test"]["output"]  # type: ignore[index]
    assert payload["write_probe"]["blocked"] is True  # type: ignore[index]


def test_review_harness_returns_bounded_failure_evidence(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    command = f'{sys.executable} -c "import sys; print(\'x\' * 10000); sys.exit(7)"'
    rc, payload = run_review_checks(
        repo_root=repo,
        test_command=command,
        probe_path="README.md",
        timeout=30,
        environ={},
        output_limit=200,
    )

    test_result = payload["test"]
    assert rc == 1
    assert payload["status"] == "FAIL"
    assert test_result["exit_code"] == 7  # type: ignore[index]
    assert len(test_result["output"]) < 250  # type: ignore[arg-type,index]
    assert "truncated" in test_result["output"]  # type: ignore[operator,index]


def test_review_harness_fails_when_git_diff_check_fails(
    monkeypatch, tmp_path: Path
) -> None:
    repo = _repo(tmp_path)
    results = iter([(0, "152 passed"), (2, "whitespace error"), (0, "")])
    monkeypatch.setattr(review_harness, "_run", lambda *args, **kwargs: next(results))

    rc, payload = run_review_checks(
        repo_root=repo,
        test_command="pytest",
        probe_path="README.md",
        timeout=30,
        environ={},
    )

    assert rc == 1
    assert payload["status"] == "FAIL"
    assert payload["git_diff_check"] == {
        "exit_code": 2,
        "output": "whitespace error",
    }


def test_review_harness_sets_its_own_cache_suppression_environment(
    monkeypatch, tmp_path: Path
) -> None:
    repo = _repo(tmp_path)
    environments: list[dict[str, str]] = []

    def record_environment(*args, **kwargs):  # type: ignore[no-untyped-def]
        environments.append(dict(kwargs["env"]))
        return 0, ""

    monkeypatch.setattr(review_harness, "_run", record_environment)
    rc, _ = run_review_checks(
        repo_root=repo,
        test_command="pytest",
        probe_path="README.md",
        timeout=30,
        environ={},
    )

    assert rc == 0
    assert environments
    assert all(env["PYTHONDONTWRITEBYTECODE"] == "1" for env in environments)
    assert all("-p no:cacheprovider" in env["PYTEST_ADDOPTS"] for env in environments)


def test_review_harness_cli_rejects_missing_test_command(
    monkeypatch, capsys
) -> None:
    monkeypatch.delenv("RUN_TASK_REVIEW_TEST_COMMAND", raising=False)

    rc = main([])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 2
    assert payload["status"] == "FAIL"
    assert "test command is empty" in payload["error"]
