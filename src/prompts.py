from __future__ import annotations

import textwrap


def _delimit_block(label: str, content: str) -> str:
    # Delimited blocks let downstream parsers strip injected context reliably.
    return f"<<<{label}_BEGIN>>>\n{content}\n<<<{label}_END>>>"


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

        Output format (Markdown):
        - Sections: Plan Status, Work Packages, Acceptance Criteria, Risks, Test Strategy, Open Questions
        - Marker line: ADDRESSED_FINDINGS: <ID1,ID2,...> or NONE
        - Marker line: CLAUDE_APPROVAL: YES or CLAUDE_APPROVAL: NO
        - The final line MUST be exactly: STATUS: DONE
        """
    ).strip()


def build_phase1_codex_review_prompt(
    task_text: str,
    shared_text: str,
    cycle: int,
    previous_open_block: str,
) -> str:
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
          CODEX_APPROVAL: YES only when OPEN_FINDINGS: NONE
          CODEX_APPROVAL: NO only when OPEN_FINDINGS is not empty
        - Mandatory ID format: F-001, F-002, ...

        Output format (Markdown):
        - Sections: Findings, Required Adjustments, Consolidated Plan
        - CONTRACT lines as defined above
        - Marker line: CODEX_APPROVAL: YES or CODEX_APPROVAL: NO
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
        - CODEX_APPROVAL: {codex_approval}
        - OPEN_FINDINGS: {open_block}

        Tasks:
        1) Determine whether the current plan is implementation-ready.
        2) If not, list concise mandatory adjustments for the next cycle.
        3) If CODEX_APPROVAL=NO or OPEN_FINDINGS is not empty, CLAUDE_APPROVAL must be NO.

        Output format (Markdown):
        - Sections: Decision, Justification, Next Mandatory Adjustments
        - Marker line: CLAUDE_APPROVAL: YES or CLAUDE_APPROVAL: NO
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
          CLAUDE_APPROVAL: YES only when OPEN_FINDINGS: NONE
          CLAUDE_APPROVAL: NO only when OPEN_FINDINGS is not empty
        - Mandatory ID format: F-001, F-002, ...

        Output format (Markdown):
        - Sections: Findings, Mandatory Fixes, Approval
        - CONTRACT lines as defined above
        - Marker line: CLAUDE_APPROVAL: YES or CLAUDE_APPROVAL: NO
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
