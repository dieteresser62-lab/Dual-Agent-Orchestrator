You are Claude acting as the sole read-only reviewer under the repository's
documented manual native-JSON bootstrap exception. Do not edit any file. Do not
invoke Antigravity. Review the current branch
feature/native-semantic-evidence-manifest adversarially.

This is not a resume of state-v3 run 20260823-111816Z. That run is abandoned
and preserved unchanged. Review the current repository state directly and
keep these two scopes separate:

1. Bootstrap/recovery hardening: committed range a897f70..c7778ab plus the
   current OPEN-to-OPEN rationale persistence fix in src/orchestrator.py and
   tests/test_orchestrator_runtime.py. These changes were individually
   fingerprint-authorized by the user during the pilot. Check especially that
   same-status rationale revisions enter the append-only finding chain, replay
   to the same FindingRecord as the state mirror, and cannot collide with a
   later real OPEN-to-CLOSED transition in another work unit.

2. AP6A feature scope: the current uncommitted semantic evidence-manifest
   implementation in schemas/native-agent-review-request-v1.schema.json,
   src/native_review_request.py, src/review_packets.py, src/workflow.py,
   tests/test_native_review_request.py, tests/test_orchestrator_runtime.py,
   tests/test_review_packets.py, and tests/test_workflow.py. Check canonical
   diff section coverage, digest recomputation, inline/content-ref request
   binding, v1 compatibility, resume/idempotency, and fail-closed behavior.

The controlled-abort documentation and moved historical artifacts live under
docs/internal/archive/phase-2-ap6a-semantic-evidence-bootstrap-exception/.
They are audit material, not runtime authority. The persisted .orchestrator
state and checkpoints were not manually edited.

Authoritative local validation after the final code change:
- focused record/replay tests: 15 passed
- C-01 focused workflow test: 1 passed
- full suite: 1202 passed in 145.68s
- git diff --check: clean

Historically open Claude findings to disposition in this direct review:

- C-01: _diff_for_paths had once truncated
  test_plan_change_boundary_honors_exact_fingerprint_bound_path_approval.
  Current evidence: helper is module-level; the test has ten reachable
  top-level statements and three assertions; focused test passes.
- C-03: the historical correction packet mixed AP6A paths with five
  separately authorized Bootstrap-hotfix paths.
- C-04: the next historical packet expanded that mixed scope to nine hotfix
  paths and a Codex response incorrectly claimed they were absent.

Do not pretend those historical packets were in scope. Close C-03/C-04 only if
the current direct-completion approach genuinely resolves the problem by
reviewing the Bootstrap commits and AP6A feature as separate, explicit scopes.
C-02 is already CLOSED and needs no new status change.

Inspect the actual diffs and code. You may use read-only Git, Read, Grep, Glob,
and focused pytest commands, but do not rerun the full suite. A positive final
review must close C-01, C-03, and C-04, include substantive review_evidence and
a realistic pre_mortem. A remaining defect must be a BLOCKER and decision
denied; do not create an OBSERVATION in this final review.

Return only one JSON object matching the supplied schema. Use exactly:
request_id = native-review-request-ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff
reviewer = claude
