# Orchestrated Agent Contract

## Runtime policy

- Start actionable planning, implementation, or review immediately.
- Ask only for material ambiguity, missing authority, secrets, or destructive action.
- Treat unrelated dirty-worktree changes as pre-existing context and never overwrite them.
- Do not edit `.orchestrator/state.json` or checkpoints manually.
- After orchestration, prompt, parser, watch, or state changes run `python3 -m pytest tests/ -v`.
- Keep `AGENTS.md`, `CLAUDE.md`, `CODEX.md`, and `ANTIGRAVITY.md` synchronized.
- Never push, merge, force-push, rewrite history, or run destructive cleanup without explicit approval.

## State-v3 workflow

- Codex plans and implements. It never approves its own work.
- Claude reviews every plan, every implementation round, and the full branch. Use Sonnet with effort `high`.
- Antigravity reviews each slice and the final branch exactly once after Claude approves the same fingerprint; it does not review plans and never replaces Claude.
- Only the orchestrator runs deterministic validation and creates fingerprint-bound attestations.
- Agents must not run the full validation matrix or emit `VALIDATION_RESULT`.
- Review the supplied change evidence adversarially across correctness, contracts, failure paths, security boundaries, and resume/idempotency behavior.
- A positive review requires either concrete finding records or `REVIEW_EVIDENCE` containing checked dimensions, largest residual risk, and a realistic break condition.
- A positive review also requires `PRE_MORTEM`, a complete authorized attestation for the same fingerprint, and no reviewer-owned open blocker.
- Only the reporting reviewer may close or reclassify its finding. Codex may answer it with `FINDING_RESPONSE`; rejection does not close it.
- Missing, inconsistent, or unparsable verdicts are denials. A stop request replaces readiness or approval.

## Output records

Codex planning:

- `SLICE_PLAN: <1-based id> | <summary> | <comma-separated repository-relative paths>`
- `PLAN_READY: YES|NO`

Codex implementation/final report:

- `FINDING_RESPONSE: <C-01|A-01> | ACCEPTED|REJECTED | <rationale>` for every open finding
- `TEST_FILES_TOUCHED: NONE|<comma-separated paths>` for implementation
- `IMPLEMENTATION_READY: <slice id> | YES|NO` or `FINAL_REPORT_READY: YES|NO`

Reviewers:

- First non-empty line: `REVIEWER: claude|antigravity`
- `NEW_FINDING: <C-01|A-01> | BLOCKER|OBSERVATION | <description> | <acceptance test>`
- `FINDING_STATUS: <id> | OPEN|CLOSED | <rationale>`
- Optional: `FINDING_RECLASSIFIED: <id> | BLOCKER|OBSERVATION | <rationale>`
- If no concrete finding: `REVIEW_EVIDENCE: <dimensions> | <largest residual risk> | <break condition>`
- Before approval: `PRE_MORTEM: <most likely failure cause in three months>`
- `PLAN_APPROVAL: YES|NO`, `SLICE_APPROVAL: <slice id> | YES|NO`, or `FINAL_APPROVAL: YES|NO`

All roles:

- Optional stop: `STOP_REQUESTED: <rule id> | <rationale>`; no readiness or approval marker may accompany it.
- The final non-empty line is exactly `STATUS: DONE`.
- Phase markers, legacy approval markers, `OPEN_FINDINGS`, and `VALIDATION_RESULT` are invalid.
