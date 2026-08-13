from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from agent_runtime import AgentInvocationError
from cli import parse_args
from contracts import AgentRole
from orchestrator import ProductionWorkflowDriver, run_pipeline, run_production_workflow
from workflow import CodexInvocation, ReviewerInvocation
from workflow_state import WorkflowStep
from workflow_state import AgentFailureKind


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _repository(tmp_path: Path, branch: str) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", "master")
    _git(repository, "config", "user.name", "Slice Test")
    _git(repository, "config", "user.email", "slice@example.invalid")
    (repository / "seed.txt").write_text("seed\n", encoding="utf-8")
    (repository / ".gitignore").write_text(".orchestrator/\n", encoding="utf-8")
    _git(repository, "add", "seed.txt", ".gitignore")
    _git(repository, "commit", "-m", "seed")
    _git(repository, "switch", "-c", branch)
    return repository


def _args(repository: Path, task: Path):
    return parse_args(
        [
            "--task-file", str(task),
            "--test-command", "python3 -c 'print(\"ok\")'",
            "--agent-output", "none",
            "--no-agent-live-stream",
        ],
        cwd=repository,
        environ={},
    )


def _review(role: AgentRole, marker: str) -> str:
    return "\n".join(
        (
            f"REVIEWER: {role.value}",
            "TEST_FILES_TOUCHED: NONE",
            "REVIEW_EVIDENCE: correctness, contracts, failure paths, security, resume | "
            "state drift | a provider changes its output",
            "PRE_MORTEM: a resumed step consumes stale evidence",
            marker,
            "STATUS: DONE",
        )
    )


def test_production_session_plans_commits_two_slices_and_persists_final_state(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/runtime-test")
    task = tmp_path / "task.md"
    task.write_text("Implement two bounded files", encoding="utf-8")
    interrupt_once = {"value": True}

    def codex(driver: ProductionWorkflowDriver, invocation: CodexInvocation) -> str:
        if invocation.step is WorkflowStep.CODEX_PLAN:
            output = "\n".join(
                (
                    "SLICE_PLAN: 1 | add first file | src/first.py",
                    "SLICE_PLAN: 2 | add second file | src/second.py",
                    "PLAN_READY: YES",
                    "STATUS: DONE",
                )
            )
        elif invocation.step is WorkflowStep.CODEX_IMPLEMENTATION:
            source = repository / "src"
            source.mkdir(exist_ok=True)
            slice_id = driver.active_state.current_slice_id
            (source / ("first.py" if slice_id == 1 else "second.py")).write_text(
                f"VALUE = {slice_id}\n", encoding="utf-8"
            )
            output = (
                f"TEST_FILES_TOUCHED: NONE\n"
                f"IMPLEMENTATION_READY: {slice_id:02d} | YES\nSTATUS: DONE"
            )
        else:
            output = "FINAL_REPORT_READY: YES\nSTATUS: DONE"
        driver.last_codex_output = output
        return output

    def reviewer(
        _driver: ProductionWorkflowDriver, invocation: ReviewerInvocation
    ) -> str:
        if (
            interrupt_once["value"]
            and invocation.step is WorkflowStep.ANTIGRAVITY_SLICE_REVIEW
        ):
            interrupt_once["value"] = False
            raise AgentInvocationError(
                agent_key="antigravity",
                kind=AgentFailureKind.NETWORK,
                invocation_id="resume-proof",
                provider_text="temporary provider outage",
                received_at=datetime.now(timezone.utc),
            )
        if invocation.step is WorkflowStep.CLAUDE_PLAN_REVIEW:
            marker = "PLAN_APPROVAL: YES"
        elif invocation.step in {
            WorkflowStep.CLAUDE_FINAL_REVIEW,
            WorkflowStep.ANTIGRAVITY_FINAL_REVIEW,
        }:
            marker = "FINAL_APPROVAL: YES"
        else:
            marker = f"SLICE_APPROVAL: {invocation.paths[0].split('/')[-1] == 'first.py' and '01' or '02'} | YES"
        return _review(invocation.reviewer, marker)

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_reviewer", reviewer)
    monkeypatch.chdir(repository)
    args = _args(repository, task)

    halted = run_production_workflow(task, args)
    assert halted.exit_code == 3
    assert halted.state.current_step is WorkflowStep.ANTIGRAVITY_SLICE_REVIEW
    assert not _git(repository, "log", "--format=%s", "master..HEAD")

    args.resume = True
    args.auto_resume = False
    result = run_production_workflow(task, args)

    assert result.workflow_completed
    assert [item.status.value for item in result.state.slices] == ["completed", "completed"]
    assert len(_git(repository, "log", "--format=%s", "master..HEAD").splitlines()) == 2
    state = json.loads(
        (repository / ".orchestrator" / "state.json").read_text(encoding="utf-8")
    )
    assert state["version"] == 3
    assert state["work_units"][-1]["kind"] == "final_review"
    assert state["work_units"][-1]["status"] == "completed"
    assert [item["work_unit_id"] for item in state["runtime_history"]["archive"]] == [1, 2, 3]
    assert state["runtime_history"]["current"]["work_unit_id"] == 4
    assert state["runtime_history"]["current"]["events"]


def test_head_drift_after_plan_becomes_typed_persisted_halt(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/head-drift")
    task = tmp_path / "task.md"
    task.write_text("Implement one bounded file", encoding="utf-8")

    def codex(driver: ProductionWorkflowDriver, invocation: CodexInvocation) -> str:
        _git(repository, "commit", "--allow-empty", "-m", "external drift")
        output = (
            "SLICE_PLAN: 1 | add file | src/one.py\n"
            "PLAN_READY: YES\nSTATUS: DONE"
        )
        driver.last_codex_output = output
        return output

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    monkeypatch.setattr(
        ProductionWorkflowDriver,
        "invoke_reviewer",
        lambda _driver, invocation: _review(
            invocation.reviewer, "PLAN_APPROVAL: YES"
        ),
    )
    monkeypatch.chdir(repository)
    result = run_production_workflow(task, _args(repository, task))

    assert result.exit_code == 4
    assert result.state.current_work_unit.gate.reason.value == "unexpected_file"
    assert "SLICE-HEAD-DRIFT" in result.state.current_work_unit.gate.detail
    persisted = json.loads(
        (repository / ".orchestrator" / "state.json").read_text(encoding="utf-8")
    )
    assert persisted["work_units"][-1]["status"] == "awaiting_user_decision"


def test_empty_implementation_is_a_typed_halt_not_cli_crash(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/empty-slice")
    task = tmp_path / "task.md"
    task.write_text("Implement one bounded file", encoding="utf-8")

    def codex(driver: ProductionWorkflowDriver, invocation: CodexInvocation) -> str:
        if invocation.step is WorkflowStep.CODEX_PLAN:
            output = (
                "SLICE_PLAN: 1 | add file | src/one.py\n"
                "PLAN_READY: YES\nSTATUS: DONE"
            )
        else:
            output = (
                "TEST_FILES_TOUCHED: NONE\n"
                "IMPLEMENTATION_READY: 01 | YES\nSTATUS: DONE"
            )
        driver.last_codex_output = output
        return output

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    monkeypatch.setattr(
        ProductionWorkflowDriver,
        "invoke_reviewer",
        lambda _driver, invocation: _review(
            invocation.reviewer, "PLAN_APPROVAL: YES"
        ),
    )
    monkeypatch.chdir(repository)
    args = _args(repository, task)
    result = run_production_workflow(task, args)

    assert result.exit_code == 4
    assert result.state.current_work_unit.gate.reason.value == "stop_request"
    assert "NO-IMPLEMENTATION-CHANGES" in result.state.current_work_unit.gate.detail
    args.force_overwrite_state = True
    args.resume = False
    assert run_pipeline(task, args, force_new=True) == 4
