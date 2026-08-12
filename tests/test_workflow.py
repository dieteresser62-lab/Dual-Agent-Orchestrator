from __future__ import annotations

from dataclasses import dataclass, field, replace

import pytest

from contracts import (
    AgentRole,
    AnchorRecord,
    ContractValidationError,
    ValidationAttestation,
    ValidationRecord,
    ValidationStatus,
)
from gates import PathClasses, StopRule, TestChangeEvidence as GateTestChangeEvidence
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
    WorkflowHistory,
    ValidationExecutionError,
    authorized_test_changes_from_state,
)
from workflow_state import (
    GateReason,
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


def _codex_stop(rule_id: str) -> str:
    return "\n".join(
        (
            f"STOP_REQUESTED: {rule_id} | domain semantics require user direction",
            "STATUS: DONE",
        )
    )


def _review_stop(role: AgentRole, rule_id: str) -> str:
    return "\n".join(
        (
            f"REVIEWER: {role.value}",
            f"STOP_REQUESTED: {rule_id} | review cannot resolve the domain choice",
            "STATUS: DONE",
        )
    )


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
    validation_unavailable: str | None = None
    test_evidence_by_fingerprint: dict[str, GateTestChangeEvidence] = field(
        default_factory=dict
    )
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

    def detect_test_changes(
        self, changes: WorkflowChanges, patterns: tuple[str, ...]
    ) -> GateTestChangeEvidence | None:
        assert patterns
        return self.test_evidence_by_fingerprint.get(changes.fingerprint)

    def validate(self, changes: WorkflowChanges) -> ValidationAttestation:
        self.validation_calls.append(changes.fingerprint)
        if self.validation_unavailable is not None:
            raise ValidationExecutionError(self.validation_unavailable)
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


def _slice_state(
    scope_paths: tuple[str, ...] = ("src/early.py", "src/latest.py", TEST_FILE),
    scope_change_groups: tuple[tuple[str, ...], ...] | None = None,
):
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
        scope_paths=scope_paths,
        scope_change_groups=scope_change_groups,
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

    result = WorkflowEngine(driver).run_current_work_unit(_slice_state(), _context())

    assert result.exit_code == 4
    assert result.state.current_work_unit.gate.reason is GateReason.UNEXPECTED_FILE
    assert result.state.current_work_unit.gate.paths == ("src/foreign.py",)
    assert driver.validation_calls == []
    assert driver.reviewer_calls == []


def test_productive_file_limit_halts_before_first_agent_and_rechecks_on_resume() -> None:
    scope = tuple(f"src/file_{index}.py" for index in range(11))
    driver = FakeDriver(snapshots=[], codex_outputs=[], reviewer_outputs=[])
    engine = WorkflowEngine(driver)

    halted = engine.run_current_work_unit(_slice_state(scope), _context())

    assert halted.exit_code == 4
    assert halted.state.current_work_unit.gate.reason is GateReason.STOP_REQUEST
    assert "PRODUCTIVE-FILE-LIMIT" in halted.state.current_work_unit.gate.detail
    assert "src/file_0.py=productive" in halted.state.current_work_unit.gate.detail
    assert driver.codex_calls == []
    resumed = halted.state.resume_after_user_decision()
    halted_again = engine.run_current_work_unit(resumed, _context(), halted.history)
    assert halted_again.exit_code == 4
    assert driver.codex_calls == []


def test_productive_file_limit_uses_persisted_rename_groups() -> None:
    rename_group = ("src/renamed_new.py", "src/renamed_old.py")
    nine_singletons = tuple((f"src/file_{index}.py",) for index in range(9))
    ten_groups = tuple(sorted((*nine_singletons, rename_group)))
    ten_paths = tuple(sorted(path for group in ten_groups for path in group))
    driver = FakeDriver(
        snapshots=[],
        codex_outputs=[_codex_stop("DOMAIN-001")],
        reviewer_outputs=[],
    )
    context = replace(
        _context(), stop_rules=(StopRule("DOMAIN-001", "stop after limit check"),)
    )

    allowed = WorkflowEngine(driver).run_current_work_unit(
        _slice_state(ten_paths, ten_groups), context
    )

    assert allowed.exit_code == 4
    assert allowed.state.current_work_unit.gate.detail.startswith("DOMAIN-001 |")
    assert len(driver.codex_calls) == 1

    eleven_groups = tuple(sorted((*ten_groups, ("src/file_9.py",))))
    eleven_paths = tuple(sorted(path for group in eleven_groups for path in group))
    blocked_driver = FakeDriver(snapshots=[], codex_outputs=[], reviewer_outputs=[])
    blocked = WorkflowEngine(blocked_driver).run_current_work_unit(
        _slice_state(eleven_paths, eleven_groups), _context()
    )

    assert blocked.exit_code == 4
    assert "11 productive change units" in blocked.state.current_work_unit.gate.detail
    assert "12 productive change units" not in blocked.state.current_work_unit.gate.detail
    assert blocked_driver.codex_calls == []


def test_branch_mismatch_halts_before_first_agent() -> None:
    driver = FakeDriver(snapshots=[], codex_outputs=[], reviewer_outputs=[])
    context = replace(_context(), current_branch="feature/other")

    result = WorkflowEngine(driver).run_current_work_unit(_slice_state(), context)

    assert result.exit_code == 4
    assert "BRANCH-MISMATCH" in result.state.current_work_unit.gate.detail
    assert driver.codex_calls == []


def test_declared_stop_rule_is_untruncated_in_codex_and_reviewer_prompts() -> None:
    changes = _changes("1", "src/early.py", TEST_FILE)
    description = "domain invariant " + "x" * 20_000
    context = replace(
        _context(), stop_rules=(StopRule("DOMAIN-001", description),)
    )
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[
            _review_approval(AgentRole.CLAUDE),
            _review_approval(AgentRole.ANTIGRAVITY),
        ],
    )

    result = WorkflowEngine(driver).run_current_work_unit(_slice_state(), context)

    assert result.completed
    expected = f"DOMAIN-001 | {description}"
    assert expected in driver.codex_calls[0].prompt
    assert expected in driver.reviewer_calls[0].prompt
    assert expected in driver.reviewer_calls[1].prompt


def test_codex_stop_request_halts_same_step_without_retry_or_repair() -> None:
    driver = FakeDriver(
        snapshots=[],
        codex_outputs=[_codex_stop("DOMAIN-001")],
        reviewer_outputs=[],
    )
    context = replace(
        _context(),
        stop_rules=(StopRule("DOMAIN-001", "engine semantics changed"),),
    )

    result = WorkflowEngine(driver).run_current_work_unit(_slice_state(), context)

    assert result.exit_code == 4
    assert result.state.current_step is WorkflowStep.CODEX_IMPLEMENTATION
    assert result.state.current_work_unit.gate.detail == (
        "DOMAIN-001 | domain semantics require user direction"
    )
    assert len(driver.codex_calls) == 1
    assert driver.repair_calls == []


def test_reviewer_stop_request_halts_without_contract_repair() -> None:
    changes = _changes("1", "src/early.py", TEST_FILE)
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[_review_stop(AgentRole.CLAUDE, "DOMAIN-001")],
    )
    context = replace(
        _context(),
        stop_rules=(StopRule("DOMAIN-001", "engine semantics changed"),),
    )

    result = WorkflowEngine(driver).run_current_work_unit(_slice_state(), context)

    assert result.exit_code == 4
    assert result.state.current_step is WorkflowStep.CLAUDE_SLICE_REVIEW
    assert len(driver.reviewer_calls) == 1
    assert driver.repair_calls == []


def test_unknown_stop_rule_is_rejected_instead_of_becoming_a_gate() -> None:
    driver = FakeDriver(
        snapshots=[],
        codex_outputs=[_codex_stop("UNKNOWN-001")],
        reviewer_outputs=[],
    )

    with pytest.raises(WorkflowExecutionError, match="unknown rule"):
        WorkflowEngine(driver).run_current_work_unit(_slice_state(), _context())

    assert len(driver.codex_calls) == 1
    assert driver.repair_calls == []


def test_unavailable_validation_uses_policy_gate_before_reviewer() -> None:
    changes = _changes("1", "src/early.py", TEST_FILE)
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[],
        validation_unavailable="pytest executable is missing",
    )

    result = WorkflowEngine(driver).run_current_work_unit(_slice_state(), _context())

    assert result.exit_code == 4
    assert result.state.current_step is WorkflowStep.CLAUDE_SLICE_REVIEW
    assert "VALIDATION-UNAVAILABLE" in result.state.current_work_unit.gate.detail
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


def test_unapproved_test_change_halts_before_validation_and_review() -> None:
    changes = _changes("1", "src/early.py", TEST_FILE)
    evidence = GateTestChangeEvidence((TEST_FILE,), "9" * 64)
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[],
        test_evidence_by_fingerprint={changes.fingerprint: evidence},
    )
    context = replace(_context(), test_changes_approved=False)

    result = WorkflowEngine(driver).run_current_work_unit(_slice_state(), context)

    assert result.exit_code == 4
    assert result.state.current_step is WorkflowStep.CLAUDE_SLICE_REVIEW
    assert result.state.current_work_unit.gate.reason is GateReason.TEST_CHANGE
    assert result.state.current_work_unit.gate.fingerprint == evidence.fingerprint
    assert result.state.current_work_unit.gate.paths == (TEST_FILE,)
    assert len(driver.codex_calls) == 1
    assert driver.validation_calls == []
    assert driver.reviewer_calls == []


def test_rejected_then_approved_test_gate_resumes_same_review_without_repeating_codex() -> None:
    changes = _changes("1", "src/early.py", TEST_FILE)
    evidence = GateTestChangeEvidence((TEST_FILE,), "9" * 64)
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[
            _review_approval(AgentRole.CLAUDE),
            _review_approval(AgentRole.ANTIGRAVITY),
        ],
        test_evidence_by_fingerprint={changes.fingerprint: evidence},
    )
    engine = WorkflowEngine(driver)
    context = replace(_context(), test_changes_approved=False)
    halted = engine.run_current_work_unit(_slice_state(), context)

    rejected = engine.decide_current_gate(
        halted.state,
        halted.history,
        approved=False,
        decided_by="user",
        decided_at="2026-08-12T12:00:00+00:00",
        rationale="test assertion needs explanation",
    )
    assert rejected.exit_code == 4
    assert rejected.state.current_work_unit.gate.reason is GateReason.TEST_CHANGE

    approved = engine.decide_current_gate(
        rejected.state,
        rejected.history,
        approved=True,
        decided_by="user",
        decided_at="2026-08-12T12:01:00+00:00",
        rationale="test delta reviewed",
    )
    completed = engine.run_current_work_unit(
        approved.state, context, approved.history
    )

    assert completed.completed
    assert len(driver.codex_calls) == 1
    assert [call.reviewer for call in driver.reviewer_calls] == [
        AgentRole.CLAUDE,
        AgentRole.ANTIGRAVITY,
    ]
    decisions = completed.state.current_work_unit.gate_decisions
    assert [(item.approved, item.decided_by) for item in decisions] == [
        (False, "user"),
        (True, "user"),
    ]
    audit_record = authorized_test_changes_from_state(completed.state)
    assert audit_record is not None
    assert audit_record.approved_at == "2026-08-12T12:01:00+00:00"
    assert audit_record.diff_fingerprint == evidence.fingerprint


def test_test_audit_projection_uses_active_approval_not_latest_decision() -> None:
    state = _slice_state()
    paths = (TEST_FILE,)
    first_fingerprint = "8" * 64
    second_fingerprint = "9" * 64
    for fingerprint, minute in (
        (first_fingerprint, "00"),
        (second_fingerprint, "01"),
    ):
        state = state.await_user_gate(
            reason=GateReason.TEST_CHANGE,
            detail="test approval required",
            fingerprint=fingerprint,
            paths=paths,
        ).record_user_gate_decision(
            approved=True,
            fingerprint=fingerprint,
            paths=paths,
            decided_by="user",
            decided_at=f"2026-08-12T12:{minute}:00+00:00",
            rationale=f"approved {fingerprint[:1]}",
        )
    state = state.record_active_test_approval(first_fingerprint, paths)

    audit_record = authorized_test_changes_from_state(state)

    assert audit_record is not None
    assert audit_record.diff_fingerprint == first_fingerprint
    assert audit_record.rationale == "approved 8"


def test_changed_test_fingerprint_expires_previous_gate_approval() -> None:
    first = _changes("1", "src/early.py", TEST_FILE)
    changed = _changes("2", "src/early.py", TEST_FILE)
    first_evidence = GateTestChangeEvidence((TEST_FILE,), "8" * 64)
    changed_evidence = GateTestChangeEvidence((TEST_FILE,), "9" * 64)

    @dataclass
    class ChangedAfterApprovalDriver(FakeDriver):
        use_changed: bool = False

        def collect_changes(self, start_commit: str) -> WorkflowChanges:
            assert start_commit == START_COMMIT
            return changed if self.use_changed else first

    driver = ChangedAfterApprovalDriver(
        snapshots=[first],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[],
        test_evidence_by_fingerprint={
            first.fingerprint: first_evidence,
            changed.fingerprint: changed_evidence,
        },
    )
    engine = WorkflowEngine(driver)
    context = replace(_context(), test_changes_approved=False)
    halted = engine.run_current_work_unit(_slice_state(), context)
    approved = engine.decide_current_gate(
        halted.state,
        halted.history,
        approved=True,
        decided_by="user",
        decided_at="2026-08-12T12:00:00+00:00",
        rationale="first test diff reviewed",
    )
    driver.use_changed = True

    expired = engine.run_current_work_unit(
        approved.state, context, approved.history
    )

    assert expired.exit_code == 4
    assert expired.state.current_work_unit.gate.fingerprint == changed_evidence.fingerprint
    assert driver.reviewer_calls == []


def test_manual_slice_gate_halts_before_commit_and_resumes_without_repeating_reviews() -> None:
    changes = _changes("1", "src/early.py", TEST_FILE)
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[
            _review_approval(AgentRole.CLAUDE),
            _review_approval(AgentRole.ANTIGRAVITY),
        ],
    )
    engine = WorkflowEngine(driver)
    context = replace(_context(), manual_slice_gate=True)

    halted = engine.run_current_work_unit(_slice_state(), context)

    assert halted.exit_code == 4
    assert halted.state.current_step is WorkflowStep.SLICE_COMMIT
    assert halted.state.current_work_unit.gate.reason is GateReason.MANUAL_SLICE
    assert driver.commit_calls == []
    approved = engine.decide_current_gate(
        halted.state,
        halted.history,
        approved=True,
        decided_by="release-owner",
        decided_at="2026-08-12T12:00:00+00:00",
        rationale="manual slice approval granted",
    )
    completed = engine.run_current_work_unit(
        approved.state, context, approved.history
    )

    assert completed.completed
    assert len(driver.codex_calls) == 1
    assert len(driver.reviewer_calls) == 2
    assert len(driver.commit_calls) == 1


def test_manual_slice_gate_includes_both_rename_paths() -> None:
    changes = WorkflowChanges(
        start_commit=START_COMMIT,
        fingerprint="1" * 64,
        paths=("src/new_name.py",),
        gate_paths=("src/new_name.py", "src/old_name.py"),
        full_diff="rename from src/old_name.py\nrename to src/new_name.py",
    )
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[
            _review_approval(AgentRole.CLAUDE),
            _review_approval(AgentRole.ANTIGRAVITY),
        ],
    )
    state = init_workflow_state(
        run_id="run-rename",
        task_file="/repo/task.md",
        branch="feature/workflow",
        branch_base=START_COMMIT,
        slice_count=1,
        timestamp="2026-08-12T10:00:00+00:00",
    ).complete_current_work_unit().start_work_unit(
        slice_id=1,
        kind=WorkUnitKind.SLICE,
        step=WorkflowStep.CODEX_IMPLEMENTATION,
    ).bind_current_slice_git_boundary(
        start_commit=START_COMMIT,
        scope_paths=("src/new_name.py",),
        start_fingerprint="0" * 64,
    )

    halted = WorkflowEngine(driver).run_current_work_unit(
        state, replace(_context(), manual_slice_gate=True)
    )

    assert halted.state.current_work_unit.gate.paths == (
        "src/new_name.py",
        "src/old_name.py",
    )


def test_anchor_change_resets_plan_review_then_returns_to_saved_slice_step() -> None:
    changes = _changes("1", "src/early.py", TEST_FILE)
    approved_anchor = AnchorRecord("RATE", "approved plan", "1", "2", "exact")
    changed_anchor = AnchorRecord("RATE", "approved plan", "1", "3", "exact")
    plan_review = "\n".join(
        (
            "REVIEWER: claude",
            "TEST_FILES_TOUCHED: NONE",
            "REVIEW_EVIDENCE: anchor plan | stale approval | changed expectation",
            "PRE_MORTEM: an anchor change bypasses plan review",
            "PLAN_APPROVAL: YES",
            "STATUS: DONE",
        )
    )
    driver = FakeDriver(
        snapshots=[changes, changes],
        codex_outputs=[_codex_ready(plan=True), _codex_ready()],
        reviewer_outputs=[
            plan_review,
            _review_approval(AgentRole.CLAUDE),
            _review_approval(AgentRole.ANTIGRAVITY),
        ],
    )
    engine = WorkflowEngine(driver)
    context = replace(
        _context(),
        approved_anchors=(approved_anchor,),
        current_anchors=(changed_anchor,),
    )
    halted = engine.run_current_work_unit(
        _slice_state(), context, WorkflowHistory(2)
    )

    assert halted.exit_code == 4
    assert halted.state.current_step is WorkflowStep.CODEX_IMPLEMENTATION
    assert halted.state.current_work_unit.gate.reason is GateReason.ANCHOR_CHANGE
    assert halted.history.latest_claude_review is None
    approved = engine.decide_current_gate(
        halted.state,
        halted.history,
        approved=True,
        decided_by="domain-owner",
        decided_at="2026-08-12T12:00:00+00:00",
        rationale="new anchor may enter plan review",
    )
    completed = engine.run_current_work_unit(
        approved.state, context, approved.history
    )

    assert completed.completed
    assert [call.step for call in driver.codex_calls] == [
        WorkflowStep.CODEX_PLAN_REVISION,
        WorkflowStep.CODEX_IMPLEMENTATION,
    ]
    assert [call.reviewer for call in driver.reviewer_calls] == [
        AgentRole.CLAUDE,
        AgentRole.CLAUDE,
        AgentRole.ANTIGRAVITY,
    ]
    assert any(
        item.startswith("anchor-plan-reviewed:")
        for item in completed.state.current_work_unit.completed_side_effects
    )
    assert "RATE | approved plan | 1 | 2 | exact" in driver.codex_calls[0].prompt
    assert "RATE | approved plan | 1 | 3 | exact" in driver.codex_calls[0].prompt


def test_reverted_anchor_change_resumes_without_plan_revision() -> None:
    changes = _changes("1", "src/early.py", TEST_FILE)
    approved_anchor = AnchorRecord("RATE", "approved plan", "1", "2", "exact")
    changed_anchor = AnchorRecord("RATE", "approved plan", "1", "3", "exact")
    driver = FakeDriver(
        snapshots=[changes],
        codex_outputs=[_codex_ready()],
        reviewer_outputs=[
            _review_approval(AgentRole.CLAUDE),
            _review_approval(AgentRole.ANTIGRAVITY),
        ],
    )
    engine = WorkflowEngine(driver)
    changed_context = replace(
        _context(),
        approved_anchors=(approved_anchor,),
        current_anchors=(changed_anchor,),
    )
    halted = engine.run_current_work_unit(
        _slice_state(), changed_context, WorkflowHistory(2)
    )
    approved = engine.decide_current_gate(
        halted.state,
        halted.history,
        approved=True,
        decided_by="domain-owner",
        decided_at="2026-08-12T12:00:00+00:00",
        rationale="anchor delta may proceed if still present",
    )
    reverted_context = replace(
        _context(),
        approved_anchors=(approved_anchor,),
        current_anchors=(approved_anchor,),
    )

    completed = engine.run_current_work_unit(
        approved.state, reverted_context, approved.history
    )

    assert completed.completed
    assert [call.step for call in driver.codex_calls] == [
        WorkflowStep.CODEX_IMPLEMENTATION
    ]


def test_anchor_change_during_correction_returns_to_correction_after_plan_review() -> None:
    changes = _changes("1", "src/early.py", TEST_FILE)
    approved_anchor = AnchorRecord("RATE", "approved plan", "1", "2", "exact")
    changed_anchor = AnchorRecord("RATE", "approved plan", "1", "3", "exact")
    plan_review = "\n".join(
        (
            "REVIEWER: claude",
            "TEST_FILES_TOUCHED: NONE",
            "REVIEW_EVIDENCE: correction anchor | stale approval | changed expectation",
            "PRE_MORTEM: correction context is discarded after anchor review",
            "PLAN_APPROVAL: YES",
            "STATUS: DONE",
        )
    )
    driver = FakeDriver(
        snapshots=[changes, changes],
        codex_outputs=[_codex_ready(plan=True), _codex_ready()],
        reviewer_outputs=[
            plan_review,
            _review_approval(AgentRole.CLAUDE),
            _review_approval(AgentRole.ANTIGRAVITY),
        ],
    )
    engine = WorkflowEngine(driver)
    context = replace(
        _context(),
        approved_anchors=(approved_anchor,),
        current_anchors=(changed_anchor,),
    )
    correction_state = _slice_state().with_current_step(WorkflowStep.CODEX_CORRECTION)

    halted = engine.run_current_work_unit(
        correction_state, context, WorkflowHistory(2)
    )

    assert halted.state.current_work_unit.gate.resume_step is WorkflowStep.CODEX_CORRECTION
    approved = engine.decide_current_gate(
        halted.state,
        halted.history,
        approved=True,
        decided_by="domain-owner",
        decided_at="2026-08-12T12:00:00+00:00",
        rationale="new anchor may enter plan review",
    )
    completed = engine.run_current_work_unit(
        approved.state, context, approved.history
    )

    assert completed.completed
    assert [call.step for call in driver.codex_calls] == [
        WorkflowStep.CODEX_PLAN_REVISION,
        WorkflowStep.CODEX_CORRECTION,
    ]
