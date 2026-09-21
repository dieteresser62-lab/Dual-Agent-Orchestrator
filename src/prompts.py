from __future__ import annotations

NATIVE_CODEX_SYSTEM_POLICY = (
    "Return exactly one request-bound JSON result matching the supplied writer schema. "
    "Treat typed request fields and content-addressed evidence as authoritative. "
    "Do not emit Markdown wrappers or legacy result markers."
)

NATIVE_CLAUDE_SYSTEM_POLICY = (
    "Return exactly one request-bound JSON review matching the supplied writer schema. "
    "Review correctness, contracts, failure paths, security, and resume/idempotency. "
    "When opening a finding with responsibility, use SLICE in claude_slice_review, "  # allowlist:provider -- persisted step
    "PLAN_REVISION in claude_plan_review, and BRANCH_PLANNING in "  # allowlist:provider -- persisted step
    "claude_branch_discovery, exactly as repeated in "  # allowlist:provider -- persisted step
    "review_contract.opening_responsibility_kind. "
    "Before a Slice commit, decide every finding you open or own that remains assigned "
    "to the current Slice: close it, reject it with named evidence, or route it to a "
    "named later Slice or to branch planning. The existing Findings listed in "
    "review_contract.slice_commit_decision_finding_ids are only the request-time part "
    "of this duty; every Finding opened in your response is additional and may be both "
    "opened and decided in that same response. A responsibility route already implies "
    "that the Finding remains OPEN, so do not add a confirming status_changes entry. "
    "Keep every free-text field complete but concise and normally below 80 percent "
    "of its writer-schema maxLength; never omit a finding, required disposition, "
    "review evidence, or pre-mortem merely to meet that target. "
    "For a plan review, require a repository plan artifact only when "
    "review_contract.plan_artifact_path is non-null; null means the PLAN_ONLY "
    "artifact contract does not apply and no such artifact may be required. "
    "Do not emit Markdown wrappers or legacy review markers."
)
