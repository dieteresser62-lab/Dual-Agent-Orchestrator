# Orchestrated Agent Contract

## Runtime policy

- Start actionable planning, implementation, or review immediately.
- Ask only for material ambiguity, missing authority, secrets, or destructive action.
- Treat unrelated dirty-worktree changes as pre-existing context and never overwrite them.
- Do not edit `.orchestrator/state.json` or checkpoints manually.
- After orchestration, prompt, parser, watch, or state changes run the repository default validation tier: `python3 -m pytest tests/ -v -m "not crash_harness"`. Run the complete crash proof at the operator gate defined below.
- Keep `AGENTS.md`, `CLAUDE.md`, and `CODEX.md` synchronized.
- Never push, merge, force-push, rewrite history, or run destructive cleanup without explicit approval.

## State-v3 workflow

- Codex plans and implements. It never approves its own work.
- Claude reviews every plan and every implementation round. The full branch is reviewed only in its linked `BRANCH_DISCOVERY` run. Use Sonnet with effort `high`.
- A reviewed plan is committed and handed to implementation automatically by default. A fingerprint-bound user gate is an explicit opt-in policy.
- `PLAN_ONLY` runs emit exactly one executable Slice for the declared work-plan artifact. After Claude approves the plan, commit that reviewed artifact directly and create an `APPROVED_PLAN_COMMIT`-bound `IMPLEMENT` handoff; do not plan or review the committed plan a second time.
- Every future implementation section inside a `PLAN_ONLY` artifact uses a contiguous `### Slice N - title` heading and the standalone canonical heading `**Exakter Änderungspfad**`, followed only by bullet-listed exact repository-relative paths. The parser accepts `**Exakte Änderungspfade:**` only as a compatibility spelling.
- Validate the `PLAN_ONLY` handoff contract before invoking Claude. Return a structurally invalid handoff to Codex for exactly one bounded automatic plan revision; if it remains invalid, persist a resumable `PLAN-CONTRACT-INVALID` policy gate instead of treating it as a retryable technical failure.
- A formal task declares an exact path scope; its target feature branch is either explicit, uniquely recognizable in the task prose, or deterministically generated from the task subject and digest. An informal task may contain only prose; the orchestrator derives the target branch plus a safe `PLAN_ONLY` work-plan path and planning scope. Distinct free-text branch candidates are rejected as ambiguous, and an existing foreign generated branch is never adopted. The orchestrator never infers direct implementation scope from prose.
- Runs are linked, not extended with a second in-run planning epoch: `IMPLEMENT -> BRANCH_DISCOVERY`, `PLAN_ONLY` without code `-> BRANCH_DISCOVERY`, and `PLAN_ONLY` with code `-> IMPLEMENT -> BRANCH_DISCOVERY`. Every edge is the same typed, side-effect-bound family handoff; only the terminal `BRANCH_DISCOVERY` run may approve the family.
- Only the orchestrator runs deterministic validation and creates fingerprint-bound attestations.
- Agents must not run the full validation matrix or claim an orchestrator validation attestation.
- An agent-local port-bind or browser-launch restriction is not a user decision. Codex hands completed work back normally so the orchestrator can run the authoritative matrix outside the agent sandbox.
- Review the supplied change evidence adversarially across correctness, contracts, failure paths, security boundaries, and resume/idempotency behavior.
- A positive review requires either concrete `new_findings`/`status_changes` records or `review_evidence` containing checked dimensions, largest residual risk, and a realistic break condition.
- A positive review also requires a non-empty `pre_mortem`, a complete authorized attestation for the same fingerprint, and no reviewer-owned open blocker.
- A Finding belongs immutably to the Slice in which it was opened. Findings never cross Slice boundaries: Claude closes an implemented Finding or an accepted rejection, and any implementer rejection that Claude does not close becomes a `BLOCKER`.
- Codex cannot close, reject, or reclassify reviewer-owned Findings. Claude may reclassify `BLOCKER` to `OBSERVATION` only from fingerprint-bound evidence, but an open blocker must still be fixed before Slice commit.
- Slice approval and Slice commit are different gates. Immediately before commit, the record-derived Slice cohort must contain no open blocker.
- The first Slice review is discovery. Every denied later review is a convergence round and progresses only by closing a previously known Finding or by a new fingerprint with attested remedial effect. A stalled round ends the work unit negatively and gate-free without a Slice commit.
- Only the reporting reviewer may close or reclassify its Finding. Codex must provide exactly one reasoned `finding_dispositions` response for every open Finding during plan, implementation, and same-Slice correction work. If the implementer rejects a Finding and the reviewer does not close it, the canonical reviewer reduction emits a record-bound `escalated` transition to `BLOCKER`.
- Branch discovery only discovers new Findings or records occurrences of known signatures. It never disposes inherited Findings or creates a special final-correction or reviewer-only cleanup work unit; an open discovery snapshot starts a linked ordinary `PLAN_ONLY` remediation run.
- Every Finding carries a prose acceptance description only; Findings never add commands to or pause the orchestrator validation matrix. The independent matrix and its fingerprint-bound attestation remain mandatory for the reviewed Slice.
- A Finding is either closed by the reporting reviewer or remains an open blocker. The reviewer's `fixed` judgment closes it directly; there is no finding-specific measurement, `FAIL -> PASS` proof, or `partial` closure state.
- Missing, inconsistent, or unparsable verdicts are denials. A stop request replaces readiness or approval.
- Exhausted watch retries move a task to `outbox/failed/*.poison` and persist the final technical diagnosis beside it as `*.poison.error.json`.

## Structured artifact authority

- New workflows are immutably bound to `structured-v2`. Native JSON results are validated against their request-specific writer schema and domain contract before the validated append-only records become the technical source of truth for every fact they represent. There is no text-parser fallback.
- Codex results and Claude reviews always use their native JSON transports. The former transport-selection CLI flags are retired and cannot weaken or alter this binding.
- The authoritative record chain lives in `.orchestrator/artifacts/<run-id>/records/`. `.orchestrator/state.json` is a disposable projection used only to locate its `run_id`; checkpoints are disposable operational projections, `head.json` is a reconstructable cache, and projected Markdown is a human audit view rather than a repair source.
- The run profile binds exactly one state-projection reducer version. A cache or record chain with foreign reducer semantics, including the pre-cutover reducer, is rejected fail-closed and points operators to `scripts/verify_legacy_chain.py`; it is never interpreted or resumed by the installed reducer.
- Append lookups use only a process-local derivative of a fully validated record prefix. A missing, stale, or divergent derivative or `head.json` proof is discarded and rebuilt from authoritative records; explicit load and resume paths always retain full-chain validation.
- Historical `legacy-state-v3` and `structured-v1` chains are unsupported and rejected fail-closed with `UNSUPPORTED-PROTOCOL`; they are never migrated, repaired, or used as a fallback.
- Resume may complete only an exact cut of the canonical pre-work baseline append sequence before any external effect. Any non-prefix fact or evidence of runtime history, invocation failure, gate decision, or completed side effect keeps resume fail-closed. Missing, corrupt, unknown, or otherwise inconsistent records require restoring the matching authoritative chain; agents must never invent records, approvals, or migration facts.
- Every Git commit, provider start, authoritative file write, and queue move is bracketed by one stable `SideEffectPayload` intent/result pair. Replay alone reconstructs the ledger; resume may inspect Git or files only to reconcile an open intent. Overwriting cache projections bind both the prior digest and the exact intended bytes so a proven-not-occurred write can be replayed exactly; non-regular files and symlinks are never accepted as durable file or queue results. Unknown outcomes stop fail-closed, and a structured-v2 chain without the initialized ledger is unsupported. A corrupt chain cannot safely accept another authoritative record: poison handling may move the source only to its digest-bound deterministic quarantine path after provider-free reconciliation, and must persist the corruption diagnosis instead of treating that move as a completed structured side effect.
- New Review records store the three review-evidence fields structurally. Pre-S4a `ReviewPayload.evidence` strings remain opaque legacy data and must never be split heuristically. A red-state commit or audit authorization requires the named follow-up Slice in the approved, fingerprint-bound Review record; the state mirror alone has no authority.
- A complete accepted baseline contains exactly one early `RunIdentity` and `RunProfile`, binding task path, branch identity, branch base, execution mode, audit path, both role-keyed agent profiles, and the reducer version. The initializer may finish only the exact canonical incomplete baseline prefix described above; every malformed, non-prefix, or already-active chain remains rejected fail-closed without synthesized facts.
- Outside that exact baseline-prefix completion, every resumable R2 chain must already contain role-named `WorkflowTransition` and `WorkflowPolicy` records before the next dispatch; a missing R2 status or policy fact is rejected fail-closed without backfill.
- Every bound Slice persists an exact `SliceBoundary` record before scope validation or a guarded side effect; the measured start commit/fingerprint are immutable, grouped scope is lossless, and a missing R3 boundary is rejected fail-closed.
- An operator resolves a `QUOTA-RESUME-DIFF` gate only after reviewing its fingerprint and paths, using `--task-file <unchanged-task> --resume --approve-gate --gate-rationale "<reviewed reason>"`. Without that explicit approval the run remains halted. The resulting user `Gate` and `GateDecision` records bind the decision time, confirmed fingerprint, affected paths, and originating invocation ID.

## PLAN_ONLY repository artifact

- A plan review applies this artifact contract only when its native request binds a non-null `review_contract.plan_artifact_path`; a null value means that no repository plan artifact exists for that review and the reviewer must not require PLAN_ONLY artifact structure.
- In `PLAN_ONLY`, Codex creates or updates the exact repository file at `WORK_PLAN_PATH`. The native result contains exactly one `SLICE_PLAN` record for that path as a receipt; the result record never substitutes for the file.
- Automatic plan-contract correction has the same repository duty. If `WORK_PLAN_PATH` is missing, “correct” explicitly means create the missing file before returning the receipt.
- Every exact repository-relative path is written as a bullet with the path enclosed in backticks, and every future Slice contains the standalone line `**Akzeptanzkriterien**`.
- For Codex, the orchestrator appends at most 12,000 characters from the configured `--agents-file` (root `AGENTS.md` by default) to `canonical_request.assignment`; it does not append `CLAUDE.md` or `CODEX.md`. Codex CLI may also discover `AGENTS.md` in its working tree, but the request binding does not rely on that implicit load. The native Claude reviewer has no `assignment` field and receives an explicit system policy plus its canonical request files. Every review with a `ReviewPacket` uses its manifest-selected read-only workspace; this is the normal path for plan-bound Slice and same-Slice correction reviews. Every review without a packet uses the full read-only Git snapshot selected by `git ls-files --cached --others --exclude-standard`, minus the fixed generated/dependency roots. Packetless reviews include plan and branch-discovery reviews plus Slice or same-Slice correction reviews in direct `IMPLEMENT` flows without an approved work plan; tracked root role files are provider-visible in all of those full-snapshot cases. A root role file is manifest-dependent only when a packet exists. Native system policy and request schemas remain the enforceable transport contract.

## Validation tiers

- The repository default validation command excludes tests marked `crash_harness`; it remains the per-Slice and same-Slice convergence-round matrix.
- The operator runs the complete provider-free crash proof with `python3 -m pytest tests/test_crash_harness.py -v` on the exact branch HEAD after the last relevant change and before the linked branch-discovery review. The orchestrator does not select or enforce this standalone command. The operator treats a merge as permitted only when that same green HEAD-bound evidence exists, or runs a fresh complete proof after HEAD changes.
- The standalone crash proof is never sampled or reduced. Only its frequency changes; its crash-boundary and convergence guarantees remain complete.

## Native JSON results

- Codex receives `native-agent-codex-request-v2` documents and returns only request-bound `native-agent-codex-result-v2` JSON. Planning, implementation, same-Slice correction, and stop results use their distinct typed variants; the legacy final-report variant is removed.
- Claude receives `native-agent-review-request-v2` documents and returns only request-specific `native-agent-review-result-v2` JSON. The writer schema binds the decision, findings, status changes, reclassifications, evidence, pre-mortem, and stop variant to the current review context.
- The provider-facing writer schema and the local domain validator are both mandatory. A schema-valid result is not authoritative until request binding and domain validation also succeed.
- Codex never creates or switches branches and never stages or commits. Those Git transactions belong to the user and orchestrator.
- Agents do not emit validation attestations. The orchestrator creates and binds deterministic validation evidence.
- Plain-text result markers, Markdown fences, parser normalization, and LLM contract repair are unsupported. Missing or invalid native JSON is a fail-closed provider output error.
