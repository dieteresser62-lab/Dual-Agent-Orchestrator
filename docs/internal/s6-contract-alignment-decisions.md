# S6 contract-alignment decisions

Date: 2026-09-01  
Branch baseline: `feature/state-authority-consolidation` at `ce7a51c`

## Record authority and resume semantics

The root contracts and record-related module headers now describe the behavior
implemented by the S4b–S5b code:

- `.orchestrator/artifacts/<run-id>/records/` is the only technical authority.
  `state.json` is a disposable projection used only to locate `run_id`;
  checkpoints and `head.json` are also reconstructable projections or caches.
- `RunProfile.reducer_version` binds a run to
  `structured-v2-schema-2-state-v3-v1`. Foreign cache or chain semantics are
  rejected instead of being interpreted by the installed reducer.
- `_persist_structured_baseline` may finish an exact cut of the canonical
  pre-work append sequence. `_matches_baseline_initialization_prefix` rejects
  a non-prefix fact and state evidence of runtime history, invocation failures,
  gate decisions, or completed side effects.
- Historical `legacy-state-v3` and `structured-v1` chains remain unsupported
  and fail closed. They are not migration or repair inputs.

No runtime behavior was changed to make these statements true.

## PLAN_ONLY effect and transport boundary

The repository effect and native receipt are separate obligations:

1. Codex creates or updates the exact file at `WORK_PLAN_PATH`. If the file is
   missing during bounded contract correction, “correct” means create it.
2. The request-bound result returns one `SLICE_PLAN` entry naming that file.
   This entry is only the receipt and cannot replace the repository artifact.

The provider-free transport proof covers both sides of the real boundary:

- `orchestrator._context` appends at most 12,000 characters from the configured
  `--agents-file` (root `AGENTS.md` by default) to the task text, and
  `WorkflowEngine._native_codex_request` preserves that exact value in the
  parsed `canonical_request.assignment` field.
- `NativeCodexAdapter.prepare_native_provider_input` sends that exact canonical
  JSON as stdin and as its measured `stdin_prompt` component.
- The orchestrator does not append `CLAUDE.md` or `CODEX.md` to that field.
  Codex CLI may additionally discover `AGENTS.md` from its working tree, but
  this implicit load is not relied on for the bound request.
- Native Claude reviews have no `assignment` field. All receive explicit system
  policy and canonical request files. Every review with a `ReviewPacket`
  receives the packet-manifest-selected read-only workspace; this is the normal
  path for plan-bound Slice/correction reviews. Every packetless review receives
  the full read-only Git snapshot selected by `git ls-files --cached --others
  --exclude-standard`, minus fixed generated/dependency roots. Packetless cases
  include plan/final reviews and Slice/correction reviews in direct `IMPLEMENT`
  flows without an approved work plan. Tracked `AGENTS.md`, `CLAUDE.md`, and
  `CODEX.md` are provider-visible in those full snapshots; visibility is
  manifest-dependent only when a packet exists.

## Crash-harness validation decision

Decision: **A — keep the complete harness, run it less frequently.**

- `tests/test_crash_harness.py` is marked in full with `crash_harness`.
- The repository default Slice/correction command excludes that marker.
- The complete standalone proof remains
  `python3 -m pytest tests/test_crash_harness.py -v` with no sampling or reduced
  scenario set.
- The operator runs it on the exact branch HEAD after the last relevant change
  and before branch-wide final review. The orchestrator neither selects nor
  enforces this standalone command. The operator permits merge only with green
  evidence for that same HEAD, or reruns the complete proof after a HEAD change.

This changes frequency only. All crash boundaries and convergence assertions
continue to run together in the standalone proof.

S6 development evidence on the completed working-tree change set:

- Standalone proof: `14 passed in 241.36s`.
- Full suite, including the complete harness: `1413 passed in 420.97s`.
  The S5b baseline was 1408 tests; the five additional tests are S6 guards.

This working-tree evidence is not the later HEAD-bound operator gate. After the
S6 changes are committed, the complete proof must run again on that candidate
HEAD before the branch-wide final review.

## Transition-matrix timing

Command:

`python3 -m pytest tests/test_workflow_transition_matrix.py::test_gate_source_map_rejects_orphans_and_per_emission_prefix_moves -q --durations=1`

| Measurement | Test call | Total |
|---|---:|---:|
| Before memoization | 38.85 s | 39.40 s |
| After memoization | 23.00 s | 23.66 s |

The public test helpers still return fresh mutable `set`/`dict` values. Only
their pure AST walks and immutable intermediate results are cached; no oracle,
expected identity, mutation case, or assertion changed.

The root-contract guard still scans the complete shared contract tail starting
at `Structured artifact authority`. It permits only the one exact operator
merge-gate sentence and continues to reject the previously forbidden claims in
all later shared sections.

## Superseded work-plan Slice 5

Decision: **re-slice it as a separate future implementation unit.**

It is not safe to declare the old Slice implemented or obsolete. Final-review
preflight and durable provider-attempt records exist, but native request bundle
paths are still operation-addressed (`work-unit-…-round-….json`) rather than
request-ID/content-addressed, and the old Slice's operations-binding and crash
matrix are not present as a complete contract. Implementing that design inside
S6 would exceed a documentation/test-alignment Slice and would require the
schema-bearing paths that S6 explicitly leaves unchanged. The follow-up must
therefore be cut from the current structured-v2 baseline with a new exact scope
and acceptance matrix; the old text is retained as source evidence, not treated
as an approved executable Slice. The resulting future assignment is recorded
in `inbox/backlog/00-nachfolgeauftrag-final-request-bindung.md`.

## Manual Claude role — operator decision

The available choices presented to the operator were:

1. Keep `CLAUDE.md` strictly read-only in orchestrated and manual tracks. This
   is the recommended default because it preserves one review authority and
   leaves suites, commits, and follow-up task creation with Codex/operator.
2. Add a narrowly delimited manual-track exception for an operator's explicit
   instruction to run a suite, commit, or create a follow-up task. The exception
   must remain unavailable to orchestrated reviews and must not allow push,
   merge, history rewriting, or self-approval.

Decision: **option 1, explicitly selected by the operator.** `CLAUDE.md` now
states that the read-only boundary also applies to manually launched sessions;
there is no exception for the full suite, edits, commits, or follow-up tasks.

## Manual Opus review correction round

The first read-only Claude CLI review used the `opus` model alias with effort
`max` and completed in 8:07 minutes. It denied approval with two contract-text
blockers and recorded two observations. The S6 correction round disposes them
as follows:

- `C-S6-01`: fixed. The standalone crash proof is now explicitly an operator
  gate; the contracts state that the orchestrator neither selects nor enforces
  it. Working-tree development evidence is distinguished from the later
  HEAD-bound gate.
- `C-S6-02`: fixed. The leading `AGENTS.md` runtime rule now invokes the default
  tier without `crash_harness` and points to the separate operator gate.
- `C-S6-03`: fixed. The forbidden-claim guard again covers the complete shared
  contract tail and permits only the exact new merge-gate sentence.
- `C-S6-04`: fixed. The re-sliced future work is recorded as
  `inbox/backlog/00-nachfolgeauftrag-final-request-bindung.md` with exact
  planning scope and acceptance matrix.

The focused post-correction guards passed: `45 passed in 7.78s`.

The second Opus/max review completed in 9:03 minutes. It closed `C-S6-01`
through `C-S6-04` and found `C-S6-05`: the Claude workspace description had
incorrectly generalized the manifest-selected Slice/correction behavior to
plan and final reviews. The contract now records the actual two-case boundary,
and the provider-free reviewer-dispatch test proves that `None` selects the full
snapshot path while a `ReviewPacket` supplies its exact manifest paths.

The post-`C-S6-05` provider-free scope passed: `91 passed in 8.25s`. The replay
module header now attributes reducer-version binding to models/loaders, and the
README shows both validation tiers explicitly.

The third Opus/max review completed in 7:13 minutes. It closed `C-S6-01`
through `C-S6-05` and found `C-S6-06`: a direct formal `IMPLEMENT` flow without
an approved work plan also produces packetless Slice/correction reviews. The
contract now binds workspace selection solely to the real condition — packet
present versus absent — and the dispatch test covers the packetless direct
Slice explicitly before proving the manifest-selected packet path.

The exact `C-S6-06` acceptance scope passed: `141 passed in 51.68s`.

The fourth read-only Claude CLI review used the `opus` alias with effort `max`
and completed in 4:57 minutes. It closed `C-S6-01` through `C-S6-06`, reported
no new actionable defect, and approved the converged S6 change set from the
reviewer's perspective.

## Version boundary

S6 does not change the artifact schema, native request/result transport,
reducer constant, or any registered protocol version. The edits are contracts,
provider-free guards, validation-tier configuration, and pure test memoization.
