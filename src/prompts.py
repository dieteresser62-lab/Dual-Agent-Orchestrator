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
        validation_details = "MISSING"
    else:
        validation = (
            f"{attestation.attestation_id} | {attestation.diff_fingerprint} | "
            f"{attestation.status.value} | {attestation.summary}"
        )
        by_command = {record.command: record for record in attestation.records}
        validation_details = "\n".join(
            (
                f"{command} | MISSING | exit=NONE | output=(not executed)"
                if (record := by_command.get(command)) is None
                else f"{command} | {record.status.value} | exit={record.exit_code} | "
                f"output={record.output or '(no captured output)'}"
            )
            for command in attestation.expected_commands
        )
        validation_details += f"\noutput_digest={attestation.output_digest}"
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
        {_delimit_block("VALIDATION_ATTESTATION", validation_details)}
        - Do not rerun the full suite and do not emit VALIDATION_RESULT. Spend the review budget on implementation analysis. If additional focused validation is needed, require it in a finding acceptance test.
        - Test scope: TEST_FILES_TOUCHED: {test_files}
        - New finding: NEW_FINDING: {prefix}-01 | BLOCKER|OBSERVATION | <description> | <acceptance test>
        - If an acceptance test requires an extra command in the next orchestrator matrix, its entire field must be: VALIDATE: ["executable","arg",...]. Do not use a shell string.
        - Previous finding, only when an actual prior finding exists: FINDING_STATUS: <ID> | OPEN|CLOSED | <rationale>. Never emit FINDING_STATUS with NONE or prose in place of <ID>.
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
    elif contract.readiness_marker is ReadinessMarker.PLAN:
        readiness = "PLAN_READY: YES|NO"
    else:
        readiness = "FINAL_REPORT_READY: YES|NO"
    lines = [
        f"STATE-V3 CONTRACT (mandatory for step {contract.name}):",
        f"- Readiness: {readiness}",
        "- Open finding response: FINDING_RESPONSE: <ID> | ACCEPTED|REJECTED | <rationale>",
    ]
    if contract.require_slice_plan:
        if contract.plan_artifact_path is not None:
            lines.extend(
                (
                    "- PLAN_ONLY executable boundary (exactly one record): "
                    "SLICE_PLAN: 1 | <concise plan-artifact summary> | "
                    f"{contract.plan_artifact_path}",
                    "- This record authorizes only the work-plan artifact. Describe future "
                    "product implementation Slices as Markdown sections inside that artifact; "
                    "do not emit them as additional SLICE_PLAN records.",
                    "- Do not modify product code, tests, configuration, or generated artifacts.",
                )
            )
        else:
            lines.extend(
                (
                    "- Ordered implementation boundary (one or more records): "
                    "SLICE_PLAN: <1-based id> | <concise summary> | <comma-separated repository-relative paths>",
                    "- Slice ids must be contiguous from 1. Paths are exact commit allowlists; "
                    "include every source, test, configuration, and audit document the slice may change.",
                    "- Keep each slice within the configured productive-file limit.",
                )
            )
    if contract.validation_attestation is not None:
        attestation = contract.validation_attestation
        lines.extend(
            (
                "- This is a read-only completeness/self-check report, never an approval.",
                "- Review the entire supplied branch diff for architecture drift, interface "
                "consistency, dead transition states, documentation sync, and requirements R-1 through R-18.",
                "- Bound orchestrator validation attestation: "
                f"{attestation.attestation_id} | {attestation.diff_fingerprint} | "
                f"{attestation.status.value} | {attestation.summary}",
                "- Do not rerun validation and do not emit VALIDATION_RESULT.",
            )
        )
    if contract.require_validation:
        command = contract.expected_validation_command or "<command>"
        lines.append(
            f"- Validation: VALIDATION_RESULT: PASS|FAIL | {command} | <exit code>"
        )
    if contract.require_test_files_record:
        test_files = (
            ",".join(contract.expected_test_files) or "NONE"
            if contract.enforce_expected_test_files
            else "<actual comma-separated changed test paths or NONE>"
        )
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
    """Build a bounded state-v3 review prompt from explicit evidence."""
    review_focus = (
        "Concentrate on plan completeness, scope discipline, executable Slice ordering, "
        "acceptance criteria, validation strategy, dependencies, and realistic failure "
        "paths. Verify that the plan stays within the assignment and that each future "
        "Slice is independently implementable and reviewable."
        if contract.approval_marker is ApprovalMarker.PLAN
        else
        "Concentrate on implementation correctness, invariants, failure paths, security "
        "boundaries, resume/idempotency behavior, and missing tests."
    )
    return textwrap.dedent(
        f"""
        You are the {contract.reviewer.value} reviewer for {contract.name}.

        {review_focus} Deterministic validation has already been executed by the
        orchestrator for the bound fingerprint.

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
