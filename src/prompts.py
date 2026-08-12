from __future__ import annotations

import textwrap

from contracts import (
    ApprovalMarker,
    CodexStepContract,
    FindingRecord,
    ReadinessMarker,
    StepContract,
)


def _delimit_block(label: str, content: str) -> str:
    # Keep envelope format in sync with `DELIMITED_SECTION_PATTERN` in orchestrator.py.
    return f"<<<{label}_BEGIN>>>\n{content}\n<<<{label}_END>>>"


def build_v3_review_contract(contract: StepContract) -> str:
    """Render the state-v3 records required by one explicit review step."""
    if contract.approval_marker is ApprovalMarker.SLICE:
        approval = f"SLICE_APPROVAL: {contract.slice_id} | YES|NO"
    else:
        approval = f"{contract.approval_marker.value}: YES|NO"
    test_files = ",".join(contract.expected_test_files) or "NONE"
    attestation = contract.validation_attestation
    if attestation is None:
        validation = "MISSING"
    else:
        validation = (
            f"{attestation.attestation_id} | {attestation.diff_fingerprint} | "
            f"{attestation.status.value} | {attestation.summary}"
        )
    prefix = "C" if contract.reviewer.value == "claude" else "A"
    anchor_rule = ""
    if contract.anchor_origin is not None:
        anchor_rule = (
            "\n- Anchor values: ANCHOR: <id> | <input> | <expected> | <tolerance> "
            f"(origin is bound to {contract.anchor_origin})"
        )
    return textwrap.dedent(
        f"""
        STATE-V3 CONTRACT (mandatory for step {contract.name}):
        - First non-empty line: REVIEWER: {contract.reviewer.value}
        - Bound orchestrator validation attestation: {validation}
        - Do not rerun the full suite and do not emit VALIDATION_RESULT. Spend the review budget on implementation analysis. If additional focused validation is needed, require it in a finding acceptance test.
        - Test scope: TEST_FILES_TOUCHED: {test_files}
        - New finding: NEW_FINDING: {prefix}-01 | BLOCKER|OBSERVATION | <description> | <acceptance test>
        - Previous finding: FINDING_STATUS: <ID> | OPEN|CLOSED | <rationale>
        - Optional reclassification: FINDING_RECLASSIFIED: <ID> | BLOCKER|OBSERVATION | <rationale>
        - If there is no concrete finding: REVIEW_EVIDENCE: <checked dimensions> | <largest residual risk> | <realistic break condition>
        - Before a positive approval: PRE_MORTEM: <most likely failure cause in three months>
        - Decision: {approval}
        - A stop request replaces the decision: STOP_REQUESTED: <rule id> | <rationale>
        {anchor_rule}
        - Final non-empty line: STATUS: DONE
        - Phase and legacy approval markers are invalid in state-v3.
        """
    ).strip()


def build_v3_codex_contract(contract: CodexStepContract) -> str:
    """Render the state-v3 records required by one explicit Codex step."""
    if contract.readiness_marker is ReadinessMarker.IMPLEMENTATION:
        readiness = f"IMPLEMENTATION_READY: {contract.slice_id} | YES|NO"
    else:
        readiness = "PLAN_READY: YES|NO"
    lines = [
        f"STATE-V3 CONTRACT (mandatory for step {contract.name}):",
        f"- Readiness: {readiness}",
        "- Open finding response: FINDING_RESPONSE: <ID> | ACCEPTED|REJECTED | <rationale>",
    ]
    if contract.require_validation:
        command = contract.expected_validation_command or "<command>"
        lines.append(
            f"- Validation: VALIDATION_RESULT: PASS|FAIL | {command} | <exit code>"
        )
    if contract.require_test_files_record:
        test_files = ",".join(contract.expected_test_files) or "NONE"
        lines.append(f"- Test scope: TEST_FILES_TOUCHED: {test_files}")
    lines.extend(
        (
            "- A stop request replaces readiness: STOP_REQUESTED: <rule id> | <rationale>",
            "- Final non-empty line: STATUS: DONE",
            "- Review approvals and phase/legacy markers are invalid for this Codex step.",
        )
    )
    return "\n".join(lines)


def build_v3_review_prompt(
    *,
    assignment: str,
    evidence: str,
    contract: StepContract,
) -> str:
    """Build a bounded additive v3 review prompt without changing legacy builders."""
    return textwrap.dedent(
        f"""
        You are the {contract.reviewer.value} reviewer for {contract.name}.

        Concentrate on implementation correctness, invariants, failure paths, security
        boundaries, resume/idempotency behavior, and missing tests. Deterministic validation
        has already been executed by the orchestrator for the bound fingerprint.

        Assignment:
        ---
        {_delimit_block("ASSIGNMENT", assignment)}
        ---

        Evidence:
        ---
        {_delimit_block("EVIDENCE", evidence)}
        ---

        {build_v3_review_contract(contract)}
        """
    ).strip()


def build_v3_codex_prompt(
    *,
    assignment: str,
    distilled_context: str,
    findings: tuple[FindingRecord, ...],
    contract: CodexStepContract,
) -> str:
    """Build a bounded implementer prompt from distilled context and typed findings."""
    finding_lines = tuple(_render_finding_record(finding) for finding in findings)
    finding_block = "\n".join(finding_lines) if finding_lines else "NONE"
    return textwrap.dedent(
        f"""
        You are Codex, the implementer for {contract.name}. You may plan or edit as
        required by the named step, but you never approve or review your own work.

        Assignment:
        ---
        {_delimit_block("ASSIGNMENT", assignment)}
        ---

        Distilled plan and current-slice context:
        ---
        {_delimit_block("CONTEXT", distilled_context)}
        ---

        Structured finding history (complete; do not infer status from prose):
        ---
        {_delimit_block("FINDINGS", finding_block)}
        ---

        {build_v3_codex_contract(contract)}
        """
    ).strip()


def _render_finding_record(finding: FindingRecord) -> str:
    responses = "; ".join(
        f"{response.decision.value}: {response.rationale}"
        for response in finding.responses
    ) or "NONE"
    closure = finding.status_rationale or "NONE"
    return (
        f"{finding.finding_id} | {finding.finding_class.value} | "
        f"{finding.status.value} | reporter={finding.origin.reporter.value} | "
        f"slice={finding.origin.slice_id} | round={finding.origin.round_number} | "
        f"summary={finding.summary} | acceptance={finding.acceptance_test} | "
        f"responses={responses} | closure={closure}"
    )


def build_phase1_claude_plan_prompt(
    task_text: str,
    shared_text: str,
    cycle: int,
    open_block: str,
) -> str:
    return textwrap.dedent(
        f"""
        You are Claude Code. We are in PHASE 1 (planning), cycle {cycle}.

        Task:
        ---
        {_delimit_block("TASK", task_text)}
        ---

        Shared planning file (history so far):
        ---
        {_delimit_block("SHARED", shared_text or '(empty)')}
        ---

        Open findings from the previous Codex review:
        ---
        {open_block}
        ---

        Goal:
        - Create or revise the implementation plan so all open findings are closed.
        - Do not add side work that is not necessary for closing findings or completing the task.
        - PLANNING ONLY: do not execute commands, do not read/edit files, do not call tools, and do not start implementation.

        Output format (Markdown):
        - Sections: Plan Status, Work Packages, Acceptance Criteria, Risks, Test Strategy, Open Questions
        - Marker line: ADDRESSED_FINDINGS: <ID1,ID2,...> or NONE
        - Marker line: PHASE1_APPROVAL: YES or PHASE1_APPROVAL: NO
        - Legacy compatibility marker (optional): CLAUDE_APPROVAL: YES or CLAUDE_APPROVAL: NO
        - The final line MUST be exactly: STATUS: DONE
        """
    ).strip()


def build_phase1_codex_review_prompt(
    task_text: str,
    shared_text: str,
    cycle: int,
    previous_open_block: str,
) -> str:
    # Contract lines are intentionally rigid so the orchestrator can parse results deterministically.
    return textwrap.dedent(
        f"""
        You are Codex Reviewer. We are in PHASE 1 (plan review), cycle {cycle}.

        Task:
        ---
        {_delimit_block("TASK", task_text)}
        ---

        Shared planning file (Claude + historical context):
        ---
        {_delimit_block("SHARED", shared_text)}
        ---

        Open findings from the PREVIOUS cycle:
        ---
        {previous_open_block}
        ---

        Tasks:
        1) Review the plan for gaps, implementability, and testability.
        2) Explicitly close previous findings or keep them open, with concise reasoning.
        3) Add new findings ONLY if they are blocker-level.
        4) Provide a clear approval decision according to the CONTRACT.
        5) REVIEW ONLY: do not execute commands, do not read/edit files, do not call tools, and do not start implementation.

        CONTRACT (mandatory):
        - For EACH previously open finding, one line:
          FINDING_STATUS: <ID> | OPEN|CLOSED | <short rationale>
        - For each NEW open finding:
          NEW_FINDING: <ID> | <short description> | <acceptance test>
        - Summary:
          OPEN_FINDINGS: NONE
          or
          OPEN_FINDINGS: <ID1,ID2,...>
        - Decision rule:
          PHASE1_APPROVAL: YES only when OPEN_FINDINGS: NONE
          PHASE1_APPROVAL: NO only when OPEN_FINDINGS is not empty
        - Legacy compatibility marker (optional):
          CODEX_APPROVAL: YES|NO
        - Mandatory ID format: F-001, F-002, ...

        Output format (Markdown):
        - Sections: Findings, Required Adjustments, Consolidated Plan
        - CONTRACT lines as defined above
        - Marker line: PHASE1_APPROVAL: YES or PHASE1_APPROVAL: NO
        - Legacy compatibility marker (optional): CODEX_APPROVAL: YES or CODEX_APPROVAL: NO
        - The final line MUST be exactly: STATUS: DONE
        """
    ).strip()


def build_phase1_claude_confirm_prompt(
    task_text: str,
    shared_text: str,
    cycle: int,
    open_block: str,
    codex_approval: str,
) -> str:
    return textwrap.dedent(
        f"""
        You are Claude Code. Final confirmation for PHASE 1, cycle {cycle}.

        Task:
        ---
        {_delimit_block("TASK", task_text)}
        ---

        Shared planning file including the current Codex review:
        ---
        {_delimit_block("SHARED", shared_text)}
        ---

        Codex contract in this cycle:
        - PHASE1_APPROVAL: {codex_approval}
        - OPEN_FINDINGS: {open_block}

        Tasks:
        1) Determine whether the current plan is implementation-ready.
        2) If not, list concise mandatory adjustments for the next cycle.
        3) If PHASE1_APPROVAL=NO or OPEN_FINDINGS is not empty, PHASE1_APPROVAL must be NO.
        4) CONFIRMATION ONLY: do not execute commands, do not read/edit files, do not call tools, and do not start implementation.

        Output format (Markdown):
        - Sections: Decision, Justification, Next Mandatory Adjustments
        - Marker line: PHASE1_APPROVAL: YES or PHASE1_APPROVAL: NO
        - Legacy compatibility marker (optional): CLAUDE_APPROVAL: YES or CLAUDE_APPROVAL: NO
        - The final line MUST be exactly: STATUS: DONE
        """
    ).strip()


def build_phase2_codex_implement_prompt(
    task_text: str,
    plan_text: str,
    shared_text: str,
    cycle: int,
    open_block: str,
    test_failure_context: str = "",
) -> str:
    test_failure_section = f"\n\n{test_failure_context.strip()}\n" if test_failure_context.strip() else ""
    return textwrap.dedent(
        f"""
        You are Codex Implementer in this repository. We are in PHASE 2, cycle {cycle}.

        Task:
        ---
        {_delimit_block("TASK", task_text)}
        ---

        Final aligned plan from PHASE 1:
        ---
        {_delimit_block("PLAN", plan_text)}
        ---

        Shared implementation file (history so far including Claude findings):
        ---
        {_delimit_block("SHARED", shared_text or '(empty)')}
        ---

        Open Claude findings from the PREVIOUS cycle:
        ---
        {open_block}
        ---
        {test_failure_section}

        Assignment:
        1) Implement/fix in the repository according to the plan and previous findings.
        2) Explicitly address all open Claude objections.
        3) Summarize implemented changes concisely.
        4) The repository can already contain unrelated local changes. Do NOT stop because of a dirty git worktree.
           Ignore unrelated diffs, edit only files relevant to this task, and do not request confirmation just for pre-existing changes.

        Output format (Markdown):
        - Sections: Summary, Changed Files, Implemented Fixes, Remaining Items
        - Marker line: IMPLEMENTATION_READY: YES or IMPLEMENTATION_READY: NO
        - The final line MUST be exactly: STATUS: DONE
        """
    ).strip()


def build_phase2_claude_review_prompt(
    task_text: str,
    plan_text: str,
    shared_text: str,
    file_snapshots: str,
    test_snapshot: str,
    cycle: int,
    previous_open_block: str,
    snapshot: str,
) -> str:
    # This prompt mirrors phase-1 contract semantics so finding lifecycle stays machine-checkable.
    return textwrap.dedent(
        f"""
        You are Claude Code Reviewer. We are in PHASE 2 review, cycle {cycle}.

        Task:
        ---
        {_delimit_block("TASK", task_text)}
        ---

        Aligned plan from PHASE 1:
        ---
        {_delimit_block("PLAN", plan_text)}
        ---

        Shared implementation file:
        ---
        {_delimit_block("SHARED", shared_text)}
        ---

        Changed file snapshots:
        ---
        {file_snapshots or _delimit_block("FILES", "(empty)")}
        ---

        Local test snapshot (configured test command):
        ---
        {_delimit_block("TEST_SNAPSHOT", test_snapshot)}
        ---

        Repository snapshot:
        ---
        {_delimit_block("SNAPSHOT", snapshot)}
        ---

        Open findings from the PREVIOUS cycle:
        ---
        {previous_open_block}
        ---

        Tasks:
        1) Verify task fulfillment and plan compliance.
        2) Find bugs, regressions, security/maintenance risks, and test gaps.
        3) If not approvable, provide concrete mandatory fixes for the next cycle.
        4) Treat this review packet as the complete evidence set. Do not explore unrelated repository files.
        5) REVIEW ONLY: do not edit files or implement code. Do not rerun the supplied validation; inspect its snapshot and spend the tool budget on implementation analysis.
        6) Read the supplied manifest and every numbered packet chunk exactly once; use no other tools and keep the response below 12000 characters.

        CONTRACT (mandatory):
        - For EACH previously open finding, one line:
          FINDING_STATUS: <ID> | OPEN|CLOSED | <short rationale>
        - For each NEW open finding:
          NEW_FINDING: <ID> | <short description> | <acceptance test>
        - Summary:
          OPEN_FINDINGS: NONE
          or
          OPEN_FINDINGS: <ID1,ID2,...>
        - Decision rule:
          PHASE2_APPROVAL: YES only when OPEN_FINDINGS: NONE
          PHASE2_APPROVAL: NO only when OPEN_FINDINGS is not empty
        - Legacy compatibility marker (optional):
          CLAUDE_APPROVAL: YES|NO
        - Mandatory ID format: F-001, F-002, ...

        Output format (Markdown):
        - Sections: Findings, Mandatory Fixes, Approval
        - CONTRACT lines as defined above
        - Marker line: PHASE2_APPROVAL: YES or PHASE2_APPROVAL: NO
        - Legacy compatibility marker (optional): CLAUDE_APPROVAL: YES or CLAUDE_APPROVAL: NO
        - The final line MUST be exactly: STATUS: DONE
        """
    ).strip()


def build_test_failure_block(test_snapshot: str, test_command: str, max_chars: int = 3000) -> str:
    snapshot = (test_snapshot or "").strip()
    if len(snapshot) > max_chars:
        # Keep prompts bounded so repeated failures do not bloat context windows.
        snapshot = snapshot[:max_chars] + "\n...[truncated]"
    command = (test_command or "").strip() or "(unset)"
    return textwrap.dedent(
        f"""
        <<<TEST_FAILURE_PRIORITY_BEGIN>>>
        Fix the failing tests before any other work.
        Re-run locally with: {command}

        Latest failing test output:
        {snapshot or "(empty)"}
        <<<TEST_FAILURE_PRIORITY_END>>>
        """
    ).strip()
