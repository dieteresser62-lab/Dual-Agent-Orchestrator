# Orchestrated Agent Contract

## Runtime policy

- Start actionable planning, implementation, or review immediately.
- Ask only for material ambiguity, missing authority, secrets, or destructive action.
- Treat unrelated dirty-worktree changes as pre-existing context and never overwrite them.
- Do not edit `.orchestrator/state.json` or checkpoints manually.
- After orchestration, prompt, parser, watch, or state changes run `python3 -m pytest tests/ -v`.
- Keep `AGENTS.md`, `CLAUDE.md`, and `CODEX.md` synchronized.
- Never push, merge, force-push, rewrite history, or run destructive cleanup without explicit approval.

## State-v3 workflow

- Codex plans and implements. It never approves its own work.
- Claude reviews every plan, every implementation round, and the full branch. Use Sonnet with effort `high`.
- A reviewed plan is committed and handed to implementation automatically by default. A fingerprint-bound user gate is an explicit opt-in policy.
- `PLAN_ONLY` runs emit exactly one executable Slice for the declared work-plan artifact. After Claude approves the plan, commit that reviewed artifact directly and create an `APPROVED_PLAN_COMMIT`-bound `IMPLEMENT` handoff; do not plan or review the committed plan a second time.
- Every future implementation section inside a `PLAN_ONLY` artifact uses a contiguous `### Slice N - title` heading and the standalone canonical heading `**Exakter Änderungspfad**`, followed only by bullet-listed exact repository-relative paths. The parser accepts `**Exakte Änderungspfade:**` only as a compatibility spelling.
- Validate the `PLAN_ONLY` handoff contract before invoking Claude. Return a structurally invalid handoff to Codex for exactly one bounded automatic plan revision; if it remains invalid, persist a resumable `PLAN-CONTRACT-INVALID` policy gate instead of treating it as a retryable technical failure.
- A formal task declares an exact target feature branch and path scope. An informal task may contain only prose plus `TARGET_BRANCH`; the orchestrator derives a safe `PLAN_ONLY` work-plan path and planning scope. It never infers direct implementation scope from prose.
- Only the orchestrator runs deterministic validation and creates fingerprint-bound attestations.
- Agents must not run the full validation matrix or emit `VALIDATION_RESULT`.
- An agent-local port-bind or browser-launch restriction is not a user decision. Codex hands completed work back normally so the orchestrator can run the authoritative matrix outside the agent sandbox.
- Review the supplied change evidence adversarially across correctness, contracts, failure paths, security boundaries, and resume/idempotency behavior.
- A positive review requires either concrete finding records or `REVIEW_EVIDENCE` containing checked dimensions, largest residual risk, and a realistic break condition.
- A positive review also requires `PRE_MORTEM`, a complete authorized attestation for the same fingerprint, and no reviewer-owned open blocker.
- Slice reviews may approve with Claude-owned open `OBSERVATION` records so later Slices can address cross-cutting follow-up work. The branch-wide final review is stricter: Claude must close or escalate every open `C-*` finding before approving and may approve only when no finding remains open.
- A correction-Slice review is a convergence round: neither reviewer may introduce a new `OBSERVATION` or reclassify a finding into a new `OBSERVATION`. Put non-actionable future ideas and residual risks in `REVIEW_EVIDENCE`; report a newly discovered actionable defect as a `BLOCKER` and deny the correction Slice.
- A final reviewer must not create a new `OBSERVATION`. Record non-actionable future ideas and residual risks in `REVIEW_EVIDENCE`; report any defect that still requires work as a `BLOCKER` with `FINAL_APPROVAL: NO` so the orchestrator creates a bounded correction work unit.
- Only the reporting reviewer may close or reclassify its finding. Codex may answer it with `FINDING_RESPONSE`; rejection does not close it.
- Only an open `BLOCKER` may extend the orchestrator validation matrix with an exact `VALIDATE: ["executable",...]` acceptance test from a configured command family. An `OBSERVATION` uses a prose acceptance test; any `VALIDATE` directive on an observation is retained as finding text but ignored for matrix selection and cannot pause the workflow.
- Missing, inconsistent, or unparsable verdicts are denials. A stop request replaces readiness or approval.
- Exhausted watch retries move a task to `outbox/failed/*.poison` and persist the final technical diagnosis beside it as `*.poison.error.json`.

## Structured artifact authority

- New workflows are immutably bound to `structured-v2`. Native JSON results are validated against their request-specific writer schema and domain contract before the validated append-only records become the technical source of truth for every fact they represent. There is no text-parser fallback.
- Codex results and Claude reviews always use their native JSON transports. The former transport-selection CLI flags are retired and cannot weaken or alter this binding.
- The authoritative record chain lives in `.orchestrator/artifacts/<run-id>/records/`. `.orchestrator/state.json` and checkpoints are operational mirrors, `head.json` is a reconstructable cache, and projected Markdown is a human audit view rather than a repair source.
- Historical `legacy-state-v3` and `structured-v1` states are unsupported and rejected fail-closed with `UNSUPPORTED-PROTOCOL`; they are never silently migrated or used as a fallback.
- Resume is fail-closed. Missing, corrupt, unknown, or mirror-divergent structured records require restoring the matching chain or mirror before continuation; agents must never invent records, approvals, or migration facts.

## Output records

Codex planning:

- `SLICE_PLAN: <1-based id> | <summary> | <comma-separated repository-relative paths>`
- `PLAN_READY: YES|NO`

Codex never creates or switches branches and never stages or commits. Those Git transactions belong to the user and orchestrator.

Codex implementation/final report:

- `FINDING_RESPONSE: <C-01> | ACCEPTED|REJECTED | <rationale>` for every open finding
- `TEST_FILES_TOUCHED: NONE|<comma-separated paths>` for implementation
- `IMPLEMENTATION_READY: <slice id> | YES|NO` or `FINAL_REPORT_READY: YES|NO`

Reviewers:

- First non-empty line: `REVIEWER: claude`
- `NEW_FINDING: <C-01> | BLOCKER|OBSERVATION | <description> | <acceptance test>`
- `FINDING_STATUS: <id> | OPEN|CLOSED | <rationale>` only for findings originally reported by the current reviewer
- Optional: `FINDING_RECLASSIFIED: <id> | BLOCKER|OBSERVATION | <rationale>` only for findings originally reported by the current reviewer
- If no concrete finding: `REVIEW_EVIDENCE: <dimensions> | <largest residual risk> | <break condition>`
- Before approval: `PRE_MORTEM: <most likely failure cause in three months>`
- `PLAN_APPROVAL: YES|NO`, `SLICE_APPROVAL: <slice id> | YES|NO`, or `FINAL_APPROVAL: YES|NO`

All roles:

- Optional stop: `STOP_REQUESTED: <rule id> | <rationale>`; no readiness or approval marker may accompany it.
- When `VALIDATION-UNAVAILABLE` is caused only by a required correction outside the current Slice but inside an already approved earlier Slice, Codex also emits the minimal exact allowlist as `REMEDIATION_PATHS: <comma-separated repository-relative paths>`.
- The final non-empty line is exactly `STATUS: DONE`.
- Phase markers, legacy approval markers, `OPEN_FINDINGS`, and `VALIDATION_RESULT` are invalid.
