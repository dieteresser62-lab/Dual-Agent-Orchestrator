from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from contracts import (
    AgentRole,
    ContractValidationError,
    ValidationAttestation,
    ValidationRecord,
    ValidationStatus,
)
from workflow import (
    CodexInvocation,
    ContractRepairInvocation,
    EvidenceKind,
    ReviewerInvocation,
    WorkflowChanges,
    WorkflowCommitRequest,
    WorkflowContext,
    WorkflowContractError,
    WorkflowEngine,
    WorkflowExecutionError,
)
from workflow_state import (
    WorkflowStep,
    WorkUnitKind,
    WorkUnitStatus,
    init_workflow_state,
)
from orchestrator import run_v3_work_unit


TEST_FILE = "tests/test_workflow.py"
START_COMMIT = "a" * 40


def _changes(token: str, *paths: str, full_diff: str | None = None) -> WorkflowChanges:
    return WorkflowChanges(
        start_commit=START_COMMIT,
        fingerprint=token * 64,
        paths=tuple(sorted(paths)),
        full_diff=full_diff or "\n".join(f"diff -- {path}" for path in paths),
    )


def _attestation(changes: WorkflowChanges) -> ValidationAttestation:
    command = "python3 -m pytest tests/ -v"
    return ValidationAttestation(
        attestation_id=f"validation-{changes.fingerprint[:8]}",
        diff_fingerprint=changes.fingerprint,
        expected_commands=(command,),
        records=(ValidationRecord(ValidationStatus.PASS, command, 0),),
        output_digest="f" * 64,
        summary="tests passed",
    )


def _codex_ready(*finding_ids: str, plan: bool = False) -> str:
    lines = [
        *(f"FINDING_RESPONSE: {finding_id} | ACCEPTED | fixed with regression" for finding_id in finding_ids),
    ]
    if plan:
        lines.append("PLAN_READY: YES")
    else:
        lines.extend((f"TEST_FILES_TOUCHED: {TEST_FILE}", "IMPLEMENTATION_READY: 01 | YES"))
    lines.append("STATUS: DONE")
    return "\n".join(lines)


def _review_approval(role: AgentRole, *, finding_status: str | None = None) -> str:
    lines = [f"REVIEWER: {role.value}", f"TEST_FILES_TOUCHED: {TEST_FILE}"]
    if finding_status is None:
        lines.append(
            "REVIEW_EVIDENCE: reviewed invariants | residual concurrency risk | parallel mutation"
        )
    else:
        lines.append(finding_status)
    lines.extend(
        (
            "PRE_MORTEM: a future transition could bypass the role order",
            "SLICE_APPROVAL: 01 | YES",
            "STATUS: DONE",
        )
    )
    return "\n".join(lines)


def _review_denial(role: AgentRole, finding_id: str) -> str:
    return "\n".join(
        (
            f"REVIEWER: {role.value}",
            f"TEST_FILES_TOUCHED: {TEST_FILE}",
            f"NEW_FINDING: {finding_id} | BLOCKER | unsafe transition | add regression test",
            "SLICE_APPROVAL: 01 | NO",
            "STATUS: DONE",
        )
    )


def _review_keeps_open(role: AgentRole, finding_id: str) -> str:
    return "\n".join(
        (
            f"REVIEWER: {role.value}",
            f"TEST_FILES_TOUCHED: {TEST_FILE}",
            f"FINDING_STATUS: {finding_id} | OPEN | regression is still incomplete",
            "SLICE_APPROVAL: 01 | NO",
            "STATUS: DONE",
        )
    )


def _review_closes(role: AgentRole, finding_id: str) -> str:
    return _review_approval(
        role,
        finding_status=f"FINDING_STATUS: {finding_id} | CLOSED | regression proves the fix",
    )


@dataclass
class FakeDriver:
    snapshots: list[WorkflowChanges]
    codex_outputs: list[str]
    reviewer_outputs: list[str]
    repair_outputs: list[str] = field(default_factory=list)
    deltas: dict[tuple[str, str], str] = field(default_factory=dict)
    invalid_attestation: str | None = None
    fail_reviewer_once: bool = False
    codex_calls: list[CodexInvocation] = field(default_factory=list)
    reviewer_calls: list[ReviewerInvocation] = field(default_factory=list)
    repair_calls: list[ContractRepairInvocation] = field(default_factory=list)
    validation_calls: list[str] = field(default_factory=list)
    commit_calls: list[WorkflowCommitRequest] = field(default_factory=list)
    checkpoints: list = field(default_factory=list)
    checkpoint_histories: list = field(default_factory=list)
    snapshot_index: int = -1

    def invoke_codex(self, invocation: CodexInvocation) -> str:
        self.codex_calls.append(invocation)
        self.snapshot_index += 1
        return self.codex_outputs.pop(0)

    def collect_changes(self, start_commit: str) -> WorkflowChanges:
        assert start_commit == START_COMMIT
        return self.snapshots[self.snapshot_index]

    def collect_correction_delta(
        self, previous_fingerprint: str, current_fingerprint: str
    ) -> str:
        return self.deltas[(previous_fingerprint, current_fingerprint)]

    def validate(self, changes: WorkflowChanges) -> ValidationAttestation:
        self.validation_calls.append(changes.fingerprint)
        attestation = _attestation(changes)
        if self.invalid_attestation == "foreign":
            return ValidationAttestation(
                attestation.attestation_id,
                "e" * 64,
                attestation.expected_commands,
                attestation.records,
                attestation.output_digest,
                attestation.summary,
            )
        if self.invalid_attestation == "incomplete":
            return ValidationAttestation(
                attestation.attestation_id,
                attestation.diff_fingerprint,
                ("python3 -m pytest tests/ -v", "python3 -m compileall src"),
                attestation.records,
                attestation.output_digest,
                attestation.summary,
            )
        return attestation

    def invoke_reviewer(self, invocation: ReviewerInvocation) -> str:
        self.reviewer_calls.append(invocation)
        if self.fail_reviewer_once:
            self.fail_reviewer_once = False
            raise RuntimeError("simulated process interruption")
        return self.reviewer_outputs.pop(0)

    def repair_review_contract(self, invocation: ContractRepairInvocation) -> str:
        self.repair_calls.append(invocation)
        return self.repair_outputs.pop(0) if self.repair_outputs else invocation.rejected_output

    def commit_slice(self, request: WorkflowCommitRequest) -> str:
        self.commit_calls.append(request)
        return "b" * 40

    def checkpoint(self, state, history) -> None:
        self.checkpoints.append(state)
        self.checkpoint_histories.append(history)


def _slice_state():
    state = init_workflow_state(
        run_id="run-1",
        task_file="/repo/task.md",
        branch="feature/workflow",
        branch_base=START_COMMIT,
        slice_count=2,
        timestamp="2026-08-12T10:00:00+00:00",
    ).complete_current_work_unit()
    state = state.start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    )
    return state.bind_current_slice_git_boundary(
        start_commit=START_COMMIT,
        scope_paths=("src/early.py", "src/latest.py", TEST_FILE),
        start_fingerprint="0" * 64,
    )


def _context() -> WorkflowContext:
    return WorkflowContext(
        assignment="Implement Slice 10",
        distilled_plan="Codex implements; Claude reviews each round; Antigravity closes.",
        slice_summary="Asymmetric state-v3 workflow engine.",
        expected_test_files=(TEST_FILE,),
        test_changes_approved=True,
    )


def test_plan_chain_uses_codex_then_claude_and_never_antigravity() -> None:
    state = init_workflow_state(
        run_id="run-plan",
        task_file="/repo/task.md",
        branch="feature/workflow",
        branch_base=START_COMMIT,
        slice_count=1,
        timestamp="2026-08-12T10:00:00+00:00",
    )
    changes = _changes("1", "docs/internal/plan.md")
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[_codex_ready(plan=True)],
        reviewer_outputs=[
            "\n".join(
                (
                    "REVIEWER: claude",
                    "TEST_FILES_TOUCHED: NONE",
                    "REVIEW_EVIDENCE: plan roles | context drift | stale decision",
                    "PRE_MORTEM: plan loses a required transition",
                    "PLAN_APPROVAL: YES",
                    "STATUS: DONE",
                )
            )
        ],
    )

    result = run_v3_work_unit(WorkflowEngine(driver), state, _context())

    assert result.completed
    assert [call.step for call in driver.codex_calls] == [WorkflowStep.CODEX_PLAN]
    assert [call.reviewer for call in driver.reviewer_calls] == [AgentRole.CLAUDE]
    assert driver.commit_calls == []


def test_two_codex_corrections_call_claude_three_times_and_antigravity_once() -> None:
    first = _changes("1", "src/early.py", TEST_FILE, full_diff="EARLY ONLY")
    second = _changes("2", "src/early.py", "src/latest.py", TEST_FILE, full_diff="EARLY SECOND")
    final = _changes(
        "3",
        "src/early.py",
        "src/latest.py",
        TEST_FILE,
        full_diff="EARLY ROUND ONE\nLATEST CORRECTION",
    )
    driver = FakeDriver(
        snapshots=[first, second, final],
        codex_outputs=[
            _codex_ready(),
            _codex_ready("C-01"),
            _codex_ready("C-01"),
        ],
        reviewer_outputs=[
            _review_denial(AgentRole.CLAUDE, "C-01"),
            _review_keeps_open(AgentRole.CLAUDE, "C-01"),
            _review_closes(AgentRole.CLAUDE, "C-01"),
            _review_approval(AgentRole.ANTIGRAVITY),
        ],
        deltas={
            (first.fingerprint, second.fingerprint): "SECOND DELTA",
            (second.fingerprint, final.fingerprint): "LATEST DELTA ONLY",
        },
    )

    result = WorkflowEngine(driver).run_current_work_unit(_slice_state(), _context())

    assert result.completed
    assert result.commit_ref == "b" * 40
    assert len(driver.codex_calls) == 3
    assert [call.reviewer for call in driver.reviewer_calls] == [
        AgentRole.CLAUDE,
        AgentRole.CLAUDE,
        AgentRole.CLAUDE,
        AgentRole.ANTIGRAVITY,
    ]
    assert [call.evidence_kind for call in driver.reviewer_calls] == [
        EvidenceKind.FULL_SLICE,
        EvidenceKind.CORRECTION_DELTA,
        EvidenceKind.CORRECTION_DELTA,
        EvidenceKind.FULL_SLICE,
    ]
    assert "LATEST DELTA ONLY" in driver.reviewer_calls[2].prompt
    assert "EARLY ROUND ONE" not in driver.reviewer_calls[2].prompt
    assert "EARLY ROUND ONE" in driver.reviewer_calls[3].prompt
    assert "LATEST CORRECTION" in driver.reviewer_calls[3].prompt
    assert driver.validation_calls == [first.fingerprint, second.fingerprint, final.fingerprint]
    assert driver.commit_calls[0].fingerprint == final.fingerprint


def test_antigravity_denial_returns_to_codex_then_claude_before_recheck() -> None:
    first = _changes("1", "src/early.py", TEST_FILE)
    corrected = _changes("2", "src/early.py", "src/latest.py", TEST_FILE)
    driver = FakeDriver(
        snapshots=[first, corrected],
        codex_outputs=[_codex_ready(), _codex_ready("A-01")],
        reviewer_outputs=[
            _review_approval(AgentRole.CLAUDE),
            _review_denial(AgentRole.ANTIGRAVITY, "A-01"),
            _review_approval(AgentRole.CLAUDE),
            _review_closes(AgentRole.ANTIGRAVITY, "A-01"),
        ],
        deltas={(first.fingerprint, corrected.fingerprint): "A-01 FIX ONLY"},
    )

    result = WorkflowEngine(driver).run_current_work_unit(_slice_state(), _context())

    assert result.completed
    assert [call.reviewer for call in driver.reviewer_calls] == [
        AgentRole.CLAUDE,
        AgentRole.ANTIGRAVITY,
        AgentRole.CLAUDE,
        AgentRole.ANTIGRAVITY,
    ]
    assert driver.reviewer_calls[2].evidence_kind is EvidenceKind.CORRECTION_DELTA
    assert driver.reviewer_calls[3].evidence_kind is EvidenceKind.FULL_SLICE
    assert all(not request.findings[-1].status.value == "OPEN" for request in driver.commit_calls)


def test_contract_only_repair_receives_no_implementation_evidence() -> None:
    changes = _changes("1", "src/early.py", TEST_FILE)
    valid = _review_approval(AgentRole.CLAUDE)
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[valid.removesuffix("\nSTATUS: DONE"), _review_approval(AgentRole.ANTIGRAVITY)],
        repair_outputs=[valid],
    )

    result = WorkflowEngine(driver).run_current_work_unit(_slice_state(), _context())

    assert result.completed
    assert len(driver.repair_calls) == 1
    repair = driver.repair_calls[0]
    assert "diff -- src/early.py" not in repair.contract
    assert repair.rejected_output == valid.removesuffix("\nSTATUS: DONE")
    assert len(driver.reviewer_calls) == 2


def test_missing_verdict_after_compact_repair_stops_at_claude_without_antigravity() -> None:
    changes = _changes("1", "src/early.py", TEST_FILE)
    missing = "\n".join(
        (
            "REVIEWER: claude",
            f"TEST_FILES_TOUCHED: {TEST_FILE}",
            "REVIEW_EVIDENCE: scope | risk | break",
            "STATUS: DONE",
        )
    )
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[missing],
        repair_outputs=[missing],
    )

    with pytest.raises(WorkflowContractError, match="verdict|SLICE_APPROVAL"):
        WorkflowEngine(driver).run_current_work_unit(_slice_state(), _context())

    assert [call.reviewer for call in driver.reviewer_calls] == [AgentRole.CLAUDE]
    assert len(driver.repair_calls) == 1
    assert driver.commit_calls == []
    assert driver.checkpoints[-1].current_step is WorkflowStep.CLAUDE_SLICE_REVIEW


@pytest.mark.parametrize("invalid", ["foreign", "incomplete"])
def test_invalid_attestation_stops_before_any_reviewer(invalid: str) -> None:
    changes = _changes("1", "src/early.py", TEST_FILE)
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[],
        invalid_attestation=invalid,
    )

    with pytest.raises(WorkflowExecutionError, match="attestation"):
        WorkflowEngine(driver).run_current_work_unit(_slice_state(), _context())

    assert driver.reviewer_calls == []
    assert driver.commit_calls == []


def test_resume_from_persisted_reviewer_step_does_not_repeat_codex() -> None:
    changes = _changes("1", "src/early.py", TEST_FILE)
    interrupted = FakeDriver(
        snapshots=[changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[_review_approval(AgentRole.CLAUDE)],
        fail_reviewer_once=True,
    )
    engine = WorkflowEngine(interrupted)
    with pytest.raises(RuntimeError, match="interruption"):
        engine.run_current_work_unit(_slice_state(), _context())
    persisted = interrupted.checkpoints[-1]
    assert persisted.current_step is WorkflowStep.CLAUDE_SLICE_REVIEW

    resumed = FakeDriver(
        snapshots=[changes],
        codex_outputs=[],
        reviewer_outputs=[
            _review_approval(AgentRole.CLAUDE),
            _review_approval(AgentRole.ANTIGRAVITY),
        ],
        snapshot_index=0,
    )
    result = WorkflowEngine(resumed).run_current_work_unit(
        persisted, _context(), interrupted.checkpoint_histories[-1]
    )

    assert result.completed
    assert resumed.codex_calls == []
    assert [call.reviewer for call in resumed.reviewer_calls] == [
        AgentRole.CLAUDE,
        AgentRole.ANTIGRAVITY,
    ]


def test_resume_after_review_denial_preserves_typed_finding_history() -> None:
    first = _changes("1", "src/early.py", TEST_FILE)
    corrected = _changes("2", "src/early.py", "src/latest.py", TEST_FILE)

    @dataclass
    class StopBeforeCorrectionDriver(FakeDriver):
        def invoke_codex(self, invocation: CodexInvocation) -> str:
            if self.codex_calls:
                raise RuntimeError("stop before correction")
            return super().invoke_codex(invocation)

    interrupted = StopBeforeCorrectionDriver(
        snapshots=[first],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[_review_denial(AgentRole.CLAUDE, "C-01")],
    )
    with pytest.raises(RuntimeError, match="before correction"):
        WorkflowEngine(interrupted).run_current_work_unit(_slice_state(), _context())
    persisted = interrupted.checkpoints[-1]
    persisted_history = interrupted.checkpoint_histories[-1]
    assert persisted.current_step is WorkflowStep.CODEX_CORRECTION
    assert persisted_history.findings[0].finding_id == "C-01"

    resumed = FakeDriver(
        snapshots=[corrected],
        codex_outputs=[_codex_ready("C-01")],
        reviewer_outputs=[
            _review_closes(AgentRole.CLAUDE, "C-01"),
            _review_approval(AgentRole.ANTIGRAVITY),
        ],
        deltas={(first.fingerprint, corrected.fingerprint): "RESUMED FIX DELTA"},
    )
    result = WorkflowEngine(resumed).run_current_work_unit(
        persisted, _context(), persisted_history
    )

    assert result.completed
    assert "C-01 | BLOCKER | OPEN | reporter=claude" in resumed.codex_calls[0].prompt
    assert "responses=ACCEPTED: fixed with regression" in resumed.reviewer_calls[0].prompt


def test_scope_foreign_change_stops_before_validation_and_review() -> None:
    changes = _changes("1", "src/foreign.py", TEST_FILE)
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[],
    )

    with pytest.raises(WorkflowExecutionError, match="outside"):
        WorkflowEngine(driver).run_current_work_unit(_slice_state(), _context())

    assert driver.validation_calls == []
    assert driver.reviewer_calls == []


def test_antigravity_is_not_called_when_fingerprint_changes_after_claude() -> None:
    approved = _changes("1", "src/early.py", TEST_FILE)
    mutated = _changes("2", "src/early.py", "src/latest.py", TEST_FILE)

    @dataclass
    class MutatingDriver(FakeDriver):
        collect_count: int = 0

        def collect_changes(self, start_commit: str) -> WorkflowChanges:
            assert start_commit == START_COMMIT
            self.collect_count += 1
            return approved if self.collect_count == 1 else mutated

    driver = MutatingDriver(
        snapshots=[approved],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[_review_approval(AgentRole.CLAUDE)],
    )

    with pytest.raises(WorkflowExecutionError, match="current fingerprint"):
        WorkflowEngine(driver).run_current_work_unit(_slice_state(), _context())

    assert [call.reviewer for call in driver.reviewer_calls] == [AgentRole.CLAUDE]
    assert driver.validation_calls == [approved.fingerprint]
    assert driver.commit_calls == []
