from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import orchestrator
import pytest
from agent_runtime import AgentInvocationError
from cli import parse_args
from contracts import AgentRole
from orchestrator import ProductionWorkflowDriver, run_pipeline, run_production_workflow
from workflow import CodexInvocation, ReviewerInvocation
from workflow import WorkflowExecutionError
from plan_handoff import PlanHandoffError
from state_io import StateSchemaError, save_workflow_state
from task_contract import parse_task_contract
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
            "--no-plan-gate",
        ],
        cwd=repository,
        environ={},
    )


def _write_task(path: Path, branch: str, *scope: str) -> None:
    path.write_text(
        "\n".join(
            (
                "ORCHESTRATOR_MODE: IMPLEMENT",
                f"TARGET_BRANCH: {branch}",
                "TASK_SCOPE: " + ", ".join(scope),
                "",
                "Implement the bounded task.",
            )
        ),
        encoding="utf-8",
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


def test_new_watch_task_switches_to_existing_target_and_uses_its_head_as_baseline(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/inbox-target")
    (repository / "prior.txt").write_text("prior target work\n", encoding="utf-8")
    _git(repository, "add", "prior.txt")
    _git(repository, "commit", "-m", "prior target work")
    target_head = _git(repository, "rev-parse", "HEAD")
    _git(repository, "switch", "master")
    task = tmp_path / "inbox-task.md"
    _write_task(task, "feature/inbox-target", "src/new.py")
    args = _args(repository, task)
    args.watch_run_id = "watch-existing-target"
    captured: dict[str, object] = {}
    real_fresh_state = orchestrator._fresh_state

    class StateCaptured(RuntimeError):
        pass

    def capture_state(**kwargs):
        state = real_fresh_state(**kwargs)
        captured["state"] = state
        raise StateCaptured

    monkeypatch.setattr(orchestrator, "_fresh_state", capture_state)
    monkeypatch.chdir(repository)

    with pytest.raises(StateCaptured):
        run_production_workflow(task, args, force_new=True)

    state = captured["state"]
    assert state.branch == "feature/inbox-target"
    assert state.branch_base == target_head
    assert state.current_slice.start_commit == target_head
    assert _git(repository, "branch", "--show-current") == "feature/inbox-target"


def test_new_watch_task_does_not_require_a_conventional_base_branch(
    tmp_path: Path, monkeypatch
) -> None:
    repository = tmp_path / "trunk-repository"
    repository.mkdir()
    _git(repository, "init", "-b", "trunk")
    _git(repository, "config", "user.name", "Slice Test")
    _git(repository, "config", "user.email", "slice@example.invalid")
    (repository / "seed.txt").write_text("seed\n", encoding="utf-8")
    (repository / ".gitignore").write_text(".orchestrator/\n", encoding="utf-8")
    _git(repository, "add", "seed.txt", ".gitignore")
    _git(repository, "commit", "-m", "seed")
    task = tmp_path / "trunk-task.md"
    _write_task(task, "feature/from-trunk", "src/new.py")
    args = _args(repository, task)
    args.watch_run_id = "watch-from-trunk"
    captured = {}
    real_fresh_state = orchestrator._fresh_state

    class StateCaptured(RuntimeError):
        pass

    def capture_state(**kwargs):
        captured["state"] = real_fresh_state(**kwargs)
        raise StateCaptured

    monkeypatch.setattr(orchestrator, "_fresh_state", capture_state)
    monkeypatch.chdir(repository)

    with pytest.raises(StateCaptured):
        run_production_workflow(task, args, force_new=True)

    assert captured["state"].branch == "feature/from-trunk"
    assert captured["state"].branch_base == _git(repository, "rev-parse", "HEAD")


def test_watch_retry_before_first_state_is_treated_as_new_task(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/source")
    _git(repository, "switch", "master")
    task = tmp_path / "inbox-retry.md"
    _write_task(task, "feature/retried", "src/new.py")
    args = _args(repository, task)
    args.watch_run_id = "watch-retry-without-state"
    captured_states = []

    class StateCaptured(RuntimeError):
        pass

    real_fresh_state = orchestrator._fresh_state

    def capture_state(**kwargs):
        state = real_fresh_state(**kwargs)
        assert state.run_id == "watch-retry-without-state"
        captured_states.append(state)
        raise StateCaptured

    monkeypatch.setattr(orchestrator, "_fresh_state", capture_state)
    monkeypatch.chdir(repository)

    with pytest.raises(StateCaptured):
        run_production_workflow(task, args, force_new=True)

    created_head = _git(repository, "rev-parse", "HEAD")
    args.resume = True
    with pytest.raises(StateCaptured):
        run_production_workflow(task, args, force_new=False)

    assert _git(repository, "branch", "--show-current") == "feature/retried"
    assert len(captured_states) == 2
    assert captured_states[1].branch_base == created_head


def test_new_watch_task_can_switch_with_unignored_in_repository_control_files(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/inbox-control-target")
    _git(repository, "switch", "master")
    inbox = repository / "CustomInbox"
    inbox.mkdir()
    task = inbox / "bug.md"
    _write_task(task, "feature/inbox-control-target", "src/new.py")
    (inbox / ".bug.md.watch.json").write_text("identity\n", encoding="utf-8")
    args = _args(repository, task)
    args.watch_run_id = "watch-control-files"

    class StateCaptured(RuntimeError):
        pass

    real_fresh_state = orchestrator._fresh_state

    def capture_state(**kwargs):
        real_fresh_state(**kwargs)
        raise StateCaptured

    monkeypatch.setattr(orchestrator, "_fresh_state", capture_state)
    monkeypatch.chdir(repository)

    with pytest.raises(StateCaptured):
        run_production_workflow(task, args, force_new=True)

    assert _git(repository, "branch", "--show-current") == (
        "feature/inbox-control-target"
    )
    assert task.is_file()
    assert (inbox / ".bug.md.watch.json").is_file()


def test_watch_resume_does_not_switch_back_after_branch_drift(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/persisted-target")
    task = tmp_path / "persisted-task.md"
    _write_task(task, "feature/persisted-target", "src/new.py")
    contract = parse_task_contract(task.read_text(encoding="utf-8"))
    state = orchestrator._fresh_state(
        task_file=task,
        run_id="watch-persisted-run",
        repository_root=repository,
        task_contract=contract,
    )
    state_file = repository / ".orchestrator" / "state.json"
    save_workflow_state(
        state_file,
        state,
        allowed_roots=(repository, task.parent.resolve()),
    )
    _git(repository, "switch", "master")
    args = _args(repository, task)
    args.watch_run_id = "watch-persisted-run"
    args.resume = True

    def unexpected_prepare(*_args, **_kwargs):
        raise AssertionError("resume must not prepare or switch branches")

    monkeypatch.setattr(
        orchestrator,
        "prepare_new_watch_task_branch",
        unexpected_prepare,
    )
    monkeypatch.chdir(repository)

    result = run_production_workflow(task, args)

    assert result.exit_code == 4
    assert "BRANCH-MISMATCH" in result.state.current_work_unit.gate.detail
    assert _git(repository, "branch", "--show-current") == "master"


def test_prepared_watch_head_change_is_rejected_before_state_initialization(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "feature/head-race")
    task = tmp_path / "head-race.md"
    _write_task(task, "feature/head-race", "src/new.py")
    contract = parse_task_contract(task.read_text(encoding="utf-8"))
    prepared_head = _git(repository, "rev-parse", "HEAD")
    _git(repository, "commit", "--allow-empty", "-m", "concurrent head move")

    with pytest.raises(StateSchemaError, match="HEAD changed"):
        orchestrator._fresh_state(
            task_file=task,
            run_id="watch-head-race",
            repository_root=repository,
            task_contract=contract,
            branch_base_override=prepared_head,
        )


def test_force_new_watch_task_intentionally_replaces_unrelated_existing_state(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/old-watch-target")
    old_task = tmp_path / "old-task.md"
    _write_task(old_task, "feature/old-watch-target", "src/old.py")
    old_contract = parse_task_contract(old_task.read_text(encoding="utf-8"))
    old_state = orchestrator._fresh_state(
        task_file=old_task,
        run_id="old-watch-run",
        repository_root=repository,
        task_contract=old_contract,
    )
    save_workflow_state(
        repository / ".orchestrator" / "state.json",
        old_state,
        allowed_roots=(repository, old_task.parent.resolve()),
    )
    _git(repository, "switch", "master")
    new_task = tmp_path / "new-task.md"
    _write_task(new_task, "feature/new-watch-target", "src/new.py")
    args = _args(repository, new_task)
    args.watch_run_id = "new-watch-run"
    captured = {}
    real_fresh_state = orchestrator._fresh_state

    class StateCaptured(RuntimeError):
        pass

    def capture_state(**kwargs):
        captured["state"] = real_fresh_state(**kwargs)
        raise StateCaptured

    monkeypatch.setattr(orchestrator, "_fresh_state", capture_state)
    monkeypatch.chdir(repository)

    with pytest.raises(StateCaptured):
        run_production_workflow(new_task, args, force_new=True)

    assert captured["state"].run_id == "new-watch-run"
    assert captured["state"].branch == "feature/new-watch-target"
    assert _git(repository, "branch", "--show-current") == (
        "feature/new-watch-target"
    )


def test_direct_task_still_requires_target_branch_to_be_active(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/inactive-target")
    _git(repository, "switch", "master")
    task = tmp_path / "direct-task.md"
    _write_task(task, "feature/inactive-target", "src/new.py")
    monkeypatch.chdir(repository)

    with pytest.raises(StateSchemaError, match="TARGET_BRANCH mismatch"):
        run_production_workflow(task, _args(repository, task))

    assert _git(repository, "branch", "--show-current") == "master"


def test_production_session_plans_commits_two_slices_and_persists_final_state(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/runtime-test")
    task = tmp_path / "task.md"
    _write_task(task, "feature/runtime-test", "src/first.py", "src/second.py")
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
        if invocation.step in {
            WorkflowStep.CLAUDE_PLAN_REVIEW,
            WorkflowStep.ANTIGRAVITY_PLAN_REVIEW,
        }:
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
    _write_task(task, "feature/head-drift", "src/one.py")

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
    _write_task(task, "feature/empty-slice", "src/one.py")

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


def test_not_ready_gate_resumes_with_plain_resume_without_explicit_approval(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/not-ready-resume")
    task = tmp_path / "task.md"
    _write_task(task, "feature/not-ready-resume", "src/one.py")
    implementation_attempts = {"count": 0}

    def codex(driver: ProductionWorkflowDriver, invocation: CodexInvocation) -> str:
        if invocation.step is WorkflowStep.CODEX_PLAN:
            output = (
                "SLICE_PLAN: 1 | add file | src/one.py\n"
                "PLAN_READY: YES\nSTATUS: DONE"
            )
        elif invocation.step is WorkflowStep.CODEX_IMPLEMENTATION:
            implementation_attempts["count"] += 1
            if implementation_attempts["count"] == 1:
                output = (
                    "TEST_FILES_TOUCHED: NONE\n"
                    "IMPLEMENTATION_READY: 01 | NO\nSTATUS: DONE"
                )
            else:
                source = repository / "src"
                source.mkdir(exist_ok=True)
                (source / "one.py").write_text("VALUE = 1\n", encoding="utf-8")
                output = (
                    "TEST_FILES_TOUCHED: NONE\n"
                    "IMPLEMENTATION_READY: 01 | YES\nSTATUS: DONE"
                )
        else:
            output = "FINAL_REPORT_READY: YES\nSTATUS: DONE"
        driver.last_codex_output = output
        return output

    def reviewer(
        _driver: ProductionWorkflowDriver, invocation: ReviewerInvocation
    ) -> str:
        if invocation.step in {
            WorkflowStep.CLAUDE_PLAN_REVIEW,
            WorkflowStep.ANTIGRAVITY_PLAN_REVIEW,
        }:
            marker = "PLAN_APPROVAL: YES"
        elif invocation.step in {
            WorkflowStep.CLAUDE_FINAL_REVIEW,
            WorkflowStep.ANTIGRAVITY_FINAL_REVIEW,
        }:
            marker = "FINAL_APPROVAL: YES"
        else:
            marker = "SLICE_APPROVAL: 01 | YES"
        return _review(invocation.reviewer, marker)

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_reviewer", reviewer)
    monkeypatch.chdir(repository)
    args = _args(repository, task)

    halted = run_production_workflow(task, args)

    assert halted.exit_code == 4
    assert halted.state.current_step is WorkflowStep.CODEX_IMPLEMENTATION
    assert halted.state.current_work_unit.gate.reason.value == "stop_request"
    assert halted.state.current_work_unit.gate.fingerprint is None

    args.resume = True
    args.auto_resume = False
    resumed = run_production_workflow(task, args)

    assert resumed.workflow_completed
    assert implementation_attempts["count"] == 2


def test_plan_only_uses_internal_plan_validation_and_commits_no_product_code(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/plan-only")
    task = tmp_path / "task.md"
    task.write_text(
        "\n".join(
            (
                "ORCHESTRATOR_MODE: PLAN_ONLY",
                "WORK_PLAN_PATH: docs/internal/work-plan.md",
                "TARGET_BRANCH: feature/plan-only",
                "TASK_SCOPE: docs/internal/work-plan.md",
                "",
                "Create only the reviewed work plan.",
            )
        ),
        encoding="utf-8",
    )

    codex_steps: list[WorkflowStep] = []

    def codex(driver: ProductionWorkflowDriver, invocation: CodexInvocation) -> str:
        codex_steps.append(invocation.step)
        if invocation.step is WorkflowStep.CODEX_PLAN:
            plan = repository / "docs" / "internal" / "work-plan.md"
            plan.parent.mkdir(parents=True)
            plan.write_text(
                "# Work plan\n\n### Slice 1 – Future implementation\n\n"
                "**Exakter Änderungspfad**\n\n- `src/future.py`\n",
                encoding="utf-8",
            )
            output = (
                "SLICE_PLAN: 1 | create reviewed work plan | docs/internal/work-plan.md\n"
                "PLAN_READY: YES\nSTATUS: DONE"
            )
        elif invocation.step is WorkflowStep.CODEX_IMPLEMENTATION:
            output = (
                "TEST_FILES_TOUCHED: NONE\n"
                "IMPLEMENTATION_READY: 01 | YES\nSTATUS: DONE"
            )
        else:
            output = "FINAL_REPORT_READY: YES\nSTATUS: DONE"
        driver.last_codex_output = output
        return output

    def reviewer(
        _driver: ProductionWorkflowDriver, invocation: ReviewerInvocation
    ) -> str:
        if invocation.step in {
            WorkflowStep.CLAUDE_PLAN_REVIEW,
            WorkflowStep.ANTIGRAVITY_PLAN_REVIEW,
        }:
            marker = "PLAN_APPROVAL: YES"
        elif invocation.step in {
            WorkflowStep.CLAUDE_FINAL_REVIEW,
            WorkflowStep.ANTIGRAVITY_FINAL_REVIEW,
        }:
            marker = "FINAL_APPROVAL: YES"
        else:
            marker = "SLICE_APPROVAL: 01 | YES"
        return _review(invocation.reviewer, marker)

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_reviewer", reviewer)
    monkeypatch.chdir(repository)

    result = run_production_workflow(task, _args(repository, task))

    assert result.workflow_completed
    assert codex_steps == [WorkflowStep.CODEX_PLAN]
    assert [item.kind.value for item in result.state.work_units] == ["plan"]
    assert _git(repository, "show", "--pretty=", "--name-only", "HEAD") == (
        "docs/internal/work-plan.md"
    )
    handoff = task.with_name("task-implement.md")
    assert handoff.is_file()
    handoff_text = handoff.read_text(encoding="utf-8")
    assert "ORCHESTRATOR_MODE: IMPLEMENT" in handoff_text
    assert f"APPROVED_PLAN_COMMIT: {result.commit_ref}" in handoff_text
    assert "SLICE_PLAN: 1 | Future implementation |" in handoff_text
    assert "src/future.py" in handoff_text
    assert "docs/internal/slice-work-plan-01-future-implementation.md" in handoff_text
    histories = [
        result.state.runtime_history["current"],
        *result.state.runtime_history["archive"],
    ]
    attestations = [
        event["attestation"]
        for history in histories
        for event in history.get("events", [])
        if event.get("kind") == "validation"
    ]
    assert attestations
    assert all(
        item["expected_commands"] == ["internal:work-plan-contract"]
        for item in attestations
    )


def test_generated_implementation_handoff_skips_second_plan_review(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/handoff")
    task = tmp_path / "guide-plan.md"
    task.write_text(
        "\n".join(
            (
                "ORCHESTRATOR_MODE: PLAN_ONLY",
                "WORK_PLAN_PATH: docs/internal/guide.md",
                "TARGET_BRANCH: feature/handoff",
                "TASK_SCOPE: docs/internal/guide.md",
                "",
                "Create the reviewed guide plan.",
            )
        ),
        encoding="utf-8",
    )
    steps: list[WorkflowStep] = []

    def codex(driver: ProductionWorkflowDriver, invocation: CodexInvocation) -> str:
        steps.append(invocation.step)
        if invocation.step is WorkflowStep.CODEX_PLAN:
            plan = repository / "docs" / "internal" / "guide.md"
            plan.parent.mkdir(parents=True)
            plan.write_text(
                "# Guide plan\n\n### Slice 1 – Rewrite guide\n\n"
                "**Exakter Änderungspfad**\n\n- `Guide.html`\n",
                encoding="utf-8",
            )
            output = (
                "SLICE_PLAN: 1 | create guide plan | docs/internal/guide.md\n"
                "PLAN_READY: YES\nSTATUS: DONE"
            )
        elif invocation.step is WorkflowStep.CODEX_IMPLEMENTATION:
            (repository / "Guide.html").write_text("<h1>Guide</h1>\n", encoding="utf-8")
            output = "TEST_FILES_TOUCHED: NONE\nIMPLEMENTATION_READY: 01 | YES\nSTATUS: DONE"
        else:
            output = "FINAL_REPORT_READY: YES\nSTATUS: DONE"
        driver.last_codex_output = output
        return output

    def reviewer(
        _driver: ProductionWorkflowDriver, invocation: ReviewerInvocation
    ) -> str:
        if invocation.step in {
            WorkflowStep.CLAUDE_PLAN_REVIEW,
            WorkflowStep.ANTIGRAVITY_PLAN_REVIEW,
        }:
            marker = "PLAN_APPROVAL: YES"
        elif invocation.step in {
            WorkflowStep.CLAUDE_FINAL_REVIEW,
            WorkflowStep.ANTIGRAVITY_FINAL_REVIEW,
        }:
            marker = "FINAL_APPROVAL: YES"
        else:
            marker = "SLICE_APPROVAL: 01 | YES"
        return _review(invocation.reviewer, marker)

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_reviewer", reviewer)
    monkeypatch.chdir(repository)

    plan_result = run_production_workflow(task, _args(repository, task))
    assert plan_result.workflow_completed
    handoff = task.with_name("guide-implement.md")
    assert handoff.is_file()

    implementation_args = _args(repository, handoff)
    implementation_args.resume = False
    implementation_args.force_overwrite_state = True
    implementation = run_production_workflow(handoff, implementation_args)

    assert implementation.workflow_completed
    assert steps.count(WorkflowStep.CODEX_PLAN) == 1
    assert steps.count(WorkflowStep.CODEX_IMPLEMENTATION) == 1
    assert (repository / "docs/internal/slice-guide-01-rewrite-guide.md").is_file()
    assert _git(repository, "show", "--pretty=", "--name-only", "HEAD").splitlines() == [
        "Guide.html",
        "docs/internal/slice-guide-01-rewrite-guide.md",
    ]


def test_completed_plan_resume_retries_failed_handoff_without_agents(
    tmp_path: Path, monkeypatch
) -> None:
    repository = _repository(tmp_path, "feature/handoff-resume")
    task = tmp_path / "resume-plan.md"
    task.write_text(
        "\n".join(
            (
                "ORCHESTRATOR_MODE: PLAN_ONLY",
                "WORK_PLAN_PATH: docs/internal/resume.md",
                "TARGET_BRANCH: feature/handoff-resume",
                "TASK_SCOPE: docs/internal/resume.md",
                "",
                "Create the reviewed resume plan.",
            )
        ),
        encoding="utf-8",
    )
    agent_steps: list[WorkflowStep] = []

    def codex(driver: ProductionWorkflowDriver, invocation: CodexInvocation) -> str:
        agent_steps.append(invocation.step)
        plan = repository / "docs" / "internal" / "resume.md"
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text(
            "# Resume plan\n\n### Slice 1 – Implement resume\n\n"
            "**Exakter Änderungspfad**\n\n- `src/resume.py`\n",
            encoding="utf-8",
        )
        output = (
            "SLICE_PLAN: 1 | create resume plan | docs/internal/resume.md\n"
            "PLAN_READY: YES\nSTATUS: DONE"
        )
        driver.last_codex_output = output
        return output

    def reviewer(
        _driver: ProductionWorkflowDriver, invocation: ReviewerInvocation
    ) -> str:
        agent_steps.append(invocation.step)
        return _review(invocation.reviewer, "PLAN_APPROVAL: YES")

    real_handoff = orchestrator.write_implementation_handoff
    handoff_calls = 0

    def fail_once(**kwargs):
        nonlocal handoff_calls
        handoff_calls += 1
        if handoff_calls == 1:
            raise PlanHandoffError("simulated post-commit handoff failure")
        return real_handoff(**kwargs)

    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_codex", codex)
    monkeypatch.setattr(ProductionWorkflowDriver, "invoke_reviewer", reviewer)
    monkeypatch.setattr(orchestrator, "write_implementation_handoff", fail_once)
    monkeypatch.chdir(repository)

    with pytest.raises(
        WorkflowExecutionError,
        match="could not create IMPLEMENT handoff",
    ):
        run_production_workflow(task, _args(repository, task))

    steps_after_commit = tuple(agent_steps)
    resumed = run_production_workflow(task, _args(repository, task))

    assert resumed.workflow_completed
    assert tuple(agent_steps) == steps_after_commit
    assert handoff_calls == 2
    assert task.with_name("resume-implement.md").is_file()
