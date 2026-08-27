# Slice 02 – Abschlusskorrektur

**Feature-Branch:** `feature/resume-success-outbox-finalization`
**GitHub-Status:** nur lokal

## Ziel des Slice

Abschlusskorrektur

## Akzeptanzkriterien

Siehe freigegebenen Arbeitsplan.

## Scope und Nicht-Scope

Erlaubter Scope: `docs/internal/resume-abschluss-verschiebt-task-in-outbox-implement-review-a9c0a538.md`, `docs/internal/slice-resume-abschluss-verschiebt-task-in-outbox-implement-02-abschlusskorrektur.md`, `src/cli.py`, `src/inbox_watcher.py`, `src/orchestrator.py`, `tests/test_cli.py`, `tests/test_inbox_watcher.py`, `tests/test_orchestrator_runtime.py`, `tests/test_orchestrator_watch_cli.py`

## Diff-Risiko inklusive Branch- und Statuscheck

Vor Umsetzung zu prüfen.

## Geplante Tests

Gemäß Arbeitsplan und Orchestrator-Validierungsmatrix.

## Durchgeführte Änderungen

Wird während der Umsetzung ergänzt.

## Ausgeführte Validierung mit Ergebnis

Wird durch den Orchestrator projiziert.

## Abweichungen vom Plan

Keine erfasst.

## Offene Risiken

Siehe Findings-Lebenszyklus.

## Review-Feedback von Claude

<!-- audit:claude-review:begin -->
### Claude · Runde 1 · approved (Ereignis 2)

- Reviewer: `claude`
- Freigabe: `YES`
- Validierungsbindung: `validation-a9fa36d46c24`
- Testdateien: `tests/test_inbox_watcher.py`, `tests/test_orchestrator_runtime.py`
- Prüfdimensionen: Correctness of the widened except clause in run_pipeline's bound-marker direct-resume branch versus sibling structured-v2 load/resume error handling in the same function; TOCTOU closure in inbox_watcher._task_digest via O_NOFOLLOW open plus lstat/fstat dev+inode identity comparison; TOCTOU closure at the filesystem mutation boundary in move_to_reserved_outbox via a pre-move regular-file re-check; presence, correctness, and pass status of the four regression/validation acceptance tests specified for C-01/C-03/C-04/C-05 inside the fingerprint-bound attestation (diff_fingerprint matches current_fingerprint); exact containment of every changed path within the authorized path set (src/inbox_watcher.py, src/orchestrator.py, tests/test_inbox_watcher.py, tests/test_orchestrator_runtime.py); the accompanying orchestrator.py mirror_findings filtering-by-correction-id change and its own new ledger-integrity assertion in test_combined_native_finding_authority_rejects_state_mirror_drift, verified against 1041 total passing tests with no unrelated regressions.
- Größtes Restrisiko: An extremely narrow TOCTOU window still exists between the final &#96;task_file.lstat()&#96; re-check and the &#96;shutil.move&#96; call inside move_to_reserved_outbox: a path swapped to a symlink in that precise instant could still land as a symlink entry in outbox/done, since shutil.move/os.rename operate on the link itself rather than dereferencing it.<br>Separately, on platforms without O_NOFOLLOW (the code falls back to flag value 0, e.g.<br>non-POSIX targets), _task_digest's protection degrades to the initial lstat check plus a post-open dev/inode comparison rather than kernel-enforced non-follow semantics, which is weaker than the POSIX path even though it still detects most swaps via the identity comparison.
- Realistische Bruchbedingung: If the pre-move lstat guard in move_to_reserved_outbox or the O_NOFOLLOW/dev-inode identity check in _task_digest is later removed, reordered, or bypassed (e.g., by refactoring finalize_queue_success to call shutil.move directly), the regression test test_bound_queue_success_rejects_source_swapped_to_symlink_before_move must start failing or silently pass a symlinked source through to outbox/done; likewise, if the run_pipeline except clause reverts to catching only ValueError, test_explicit_resume_with_bound_success_and_corrupt_state_returns_one must start raising ArtifactResumeError/StateSchemaError uncaught instead of returning<br>1.<br>Either regression, or any drop below the attested 1041/69/44 pass counts at a matching diff_fingerprint, breaks this approval.
- Eigene Findings: `C-01`, `C-03`, `C-04`, `C-05`

### Claude · Runde 2 · approved (Ereignis 4)

- Reviewer: `claude`
- Freigabe: `YES`
- Validierungsbindung: `validation-c52a7e8c9ab6`
- Testdateien: `tests/test_inbox_watcher.py`, `tests/test_orchestrator_runtime.py`, `tests/test_workflow.py`
- Prüfdimensionen: Verified C-01: run_pipeline's direct-queue bound-marker recovery branch now catches (ArtifactResumeError, StateSchemaError, ValueError) instead of ValueError alone, matching the sibling load_resumable_workflow_state call site; the new test_explicit_resume_with_bound_success_and_corrupt_state_returns_one leaves a bound .success marker, corrupts .orchestrator/state.json, drives run_pipeline via the literal --resume --task-file path, and asserts exit code 1 with 'Direct queue recovery rejected' logged.<br>Verified C-03: _task_digest now opens the task file with O_NOFOLLOW where available and independently cross-checks pre-open lstat dev/inode identity against the post-open fstat dev/inode (a platform-independent fallback that also catches symlink swaps when O_NOFOLLOW is unsupported), and move_to_reserved_outbox re-validates lstat immediately before shutil.move; the new test_bound_queue_success_rejects_source_swapped_to_symlink_before_move swaps the source to a symlink mid-finalization and asserts a FAILED disposition, an intact symlink, an untouched target, and an empty outbox/done.<br>Confirmed C-04/C-05 restate C-01/C-03 at the final gate and are resolved by the same code paths and tests.<br>Confirmed the fingerprint-bound validation_attestation (python3 -m pytest tests/ -v, exit 0, 1042 passed) matches the request's current_fingerprint c52a7e8c9ab6 exactly, subsuming both required VALIDATE commands for tests/test_orchestrator_runtime.py and tests/test_inbox_watcher.py.<br>Reviewed the accompanying orchestrator.py mirror_findings correction-id filtering and the new WorkflowCommitApprovalRequired HEAD-drift commit gate for coherence with their dedicated new tests (test_combined_native_finding_authority_rejects_state_mirror_drift extension and test_slice_head_drift_is_persisted_as_exact_resume_gate); both are internally consistent, exercised, and passing.<br>Confirmed the diff_coverage manifest touches only the authorized six production/test paths (src/inbox_watcher.py, src/orchestrator.py, src/workflow.py, tests/test_inbox_watcher.py, tests/test_orchestrator_runtime.py, tests/test_workflow.py), all within the bound authorized_paths allowlist, with no unexpected path.
- Größtes Restrisiko: move_to_reserved_outbox re-validates task_file.lstat() immediately before shutil.move, but an infinitesimal TOCTOU window remains between that lstat call and the move syscall itself; a process able to win that exact race could still substitute the path at the last instant.<br>This is inherent to check-then-act filesystem operations without an fd-anchored atomic rename/replace and is not fully eliminated by this Slice, though it is drastically narrowed from the prior single-upfront-check design and is no longer exploitable from the digest-read boundary onward.
- Realistische Bruchbedingung: If a future refactor removes either the O_NOFOLLOW/dev-inode fstat identity cross-check in _task_digest or the immediate pre-move lstat re-check in move_to_reserved_outbox (e.g.<br>while consolidating the two functions or optimizing the hot path), the TOCTOU protection for C-03 silently regresses; the existing test_bound_queue_success_rejects_source_swapped_to_symlink_before_move regression must keep failing against any such regression before it can be merged.<br>Likewise, if the ArtifactResumeError/StateSchemaError tuple in run_pipeline's bound-marker branch is narrowed again to plain ValueError, test_explicit_resume_with_bound_success_and_corrupt_state_returns_one must catch the reintroduced uncaught-exception path.
- Eigene Findings: `C-01`, `C-03`, `C-04`, `C-05`

<!-- artifact-records:claude-review:begin -->
Semantischer Record-Digest: `3253cc7779e8`

### Claude · Runde 1 · approved

| Seq/Record | Rolle | Runde | Status | Work-Unit | Findings | Fingerprint | Transport | Request | Response |
|---|---|---:|---|---|---|---|---|---|---|
| 16. `ar1-f82d0c97029f` | `claude` | `1` | `approved` | `4` | `C-01`, `C-03`, `C-04`, `C-05` | `a9fa36d46c24` | `native-claude-review-v2` | `native-review-request-604a19b1fe0d` | `4bd7846bbd67` |

### Claude · Runde 1 · approved

| Seq/Record | Rolle | Runde | Status | Work-Unit | Findings | Fingerprint | Transport | Request | Response |
|---|---|---:|---|---|---|---|---|---|---|
| 31. `ar1-70605a1d06a2` | `claude` | `1` | `approved` | `4` | `C-01`, `C-03`, `C-04`, `C-05` | `c52a7e8c9ab6` | `native-claude-review-v2` | `native-review-request-6bd8f24607ac` | `8cf72880f495` |
<!-- artifact-records:claude-review:end -->
<!-- audit:claude-review:end -->

## Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
- `C-01` Antwort 1: **angenommen** — The bound-marker recovery branch catches only ValueError although load_resumable_workflow_state can raise ArtifactResumeError or StateSchemaError.<br>The finding correctly identifies an inconsistent fail-closed error path that should be covered by a direct-resume regression test.
- `C-01` Antwort 2: **angenommen** — The direct bound-marker resume branch now catches ArtifactResumeError and StateSchemaError and returns a logged exit code<br>1.<br>A literal --resume --task-file regression test covers corrupt state.
- `C-03` Antwort 1: **angenommen** — The source symlink check occurs before later digest and move operations, leaving a genuine TOCTOU window.<br>A test that swaps the source after initial validation would establish the required fail-closed behavior.
- `C-03` Antwort 2: **angenommen** — Queue digest reads now reject symlinks and path/descriptor identity changes, and the move boundary revalidates a regular non-symlink source.<br>A mid-finalization symlink-swap regression confirms FAILED disposition.
- `C-04` Antwort 1: **angenommen** — Resolved by the same controlled structured-resume exception handling and orchestrator runtime regression as C-01.
- `C-05` Antwort 1: **angenommen** — Resolved by the same no-follow queue-source hardening and symlink-swap regression as C-03.<br>The full suite passed with 1041 tests.

<!-- artifact-records:codex-responses:begin -->
Semantischer Record-Digest: `3253cc7779e8`

### Codex · Findingantworten

| Seq/Record | Finding | Rolle | Runde | Aktion | Klasse | Status | Begründung |
|---|---|---|---:|---|---|---|---|
| 7. `ar1-bcb051572d83` | `C-01` | `codex` | `1` | `responded` | `BLOCKER` | `open` | The direct bound-marker resume branch now catches ArtifactResumeError and StateSchemaError and returns a logged exit code<br>1.<br>A literal --resume --task-file regression test covers corrupt state. |
| 8. `ar1-593466bcccef` | `C-03` | `codex` | `1` | `responded` | `BLOCKER` | `open` | Queue digest reads now reject symlinks and path/descriptor identity changes, and the move boundary revalidates a regular non-symlink source.<br>A mid-finalization symlink-swap regression confirms FAILED disposition. |
| 9. `ar1-f151bffe46f2` | `C-04` | `codex` | `1` | `responded` | `BLOCKER` | `open` | Resolved by the same controlled structured-resume exception handling and orchestrator runtime regression as C-01. |
| 10. `ar1-152be52b64f6` | `C-05` | `codex` | `1` | `responded` | `BLOCKER` | `open` | Resolved by the same no-follow queue-source hardening and symlink-swap regression as C-03.<br>The full suite passed with 1041 tests. |
<!-- artifact-records:codex-responses:end -->
<!-- audit:codex-responses:end -->

## Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### Ereignis 1: `validation-a9fa36d46c24`

- Diff-Fingerprint: `a9fa36d46c24`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: 3 passed; 0 failed; 0 unavailable; 3 required
- Ausgabedigest: `835718911103`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| python3 -m pytest tests/ -v | PASS | 0 | ============================= test session starts ==============================<br>platform linux -- Python 3.12.3, pytest-7.4.4, pluggy-1.4.0 -- /usr/bin/python3<br>cachedir: .pytest_cache<br>rootdir: /mnt/c/Users/Diete/Sync/DE_Privat/Rente/ChatGPT CLI/Dual-Agent-Orchestrator<br>configfile: pyproject.toml<br>collecting ... collected 1041 items<br><br>tests/test_agent_adapters.py::test_registry_constructs_only_native_adapters PASSED [  0%]<br>tests/test_agent_adapters.py::test_native_adapter_api_and_mro_are_closed PASSED [  0%]<br>tests/test_agent_adapters.py::test_native_codex_prepares_schema_request_and_assets PASSED [  0%]<br>tests/test_agent_adapters.py::test_native_codex_extracts_only_bound_result PASSED [  0%]<br>tests/test_agent_adapters.py::test_native_claude_workspace_binding_and_capability_smoke PASSED [  0%]<br>tests/test_agent_adapters.py::test_native_claude_prepares_request_components_and_bound_output PASSED [  0%]<br>tests/test_agent_runtime.py::test_compute_retry_backoff_seconds_exponential PASSED [  0%]<br>tes<br>...[120172 characters omitted]...<br>_pre_slice11_v3_gate_and_work_unit_shapes_load_with_empty_new_fields PASSED [ 99%]<br>tests/test_workflow_state.py::test_early_slice11_work_unit_shape_loads_without_active_test_evidence PASSED [ 99%]<br>tests/test_workflow_state.py::test_policy_gate_roundtrips_and_resumes_at_same_step PASSED [ 99%]<br>tests/test_workflow_state.py::test_managed_audit_path_roundtrips_and_rejects_unsafe_locations PASSED [ 99%]<br>tests/test_workflow_state.py::test_policy_gate_rejects_fingerprint_bound_reason_and_unsafe_path PASSED [ 99%]<br>tests/test_workflow_state.py::test_unexpected_file_user_gate_decision_roundtrips PASSED [ 99%]<br>tests/test_workflow_state.py::test_plan_time_slice_one_start_commit_cannot_be_rebound_after_resume PASSED [ 99%]<br>tests/test_workflow_state.py::test_in_progress_slice_can_extend_exact_remediation_scope PASSED [ 99%]<br>tests/test_workflow_state.py::test_state_scope_rejects_orchestrator_internal_paths PASSED [100%]<br><br>======================= 1041 passed in 66.60s (0:01:06) ======================== |
| python3 -m pytest tests/test_orchestrator_runtime.py -v | PASS | 0 | ============================= test session starts ==============================<br>platform linux -- Python 3.12.3, pytest-7.4.4, pluggy-1.4.0 -- /usr/bin/python3<br>cachedir: .pytest_cache<br>rootdir: /mnt/c/Users/Diete/Sync/DE_Privat/Rente/ChatGPT CLI/Dual-Agent-Orchestrator<br>configfile: pyproject.toml<br>collecting ... collected 69 items<br><br>tests/test_orchestrator_runtime.py::test_production_correction_delta_preserves_unified_diff_boundary PASSED [  1%]<br>tests/test_orchestrator_runtime.py::test_new_watch_task_switches_to_existing_target_and_uses_its_head_as_baseline PASSED [  2%]<br>tests/test_orchestrator_runtime.py::test_resume_uses_persisted_profiles_and_rejects_explicit_drift_before_provider PASSED [  4%]<br>tests/test_orchestrator_runtime.py::test_fresh_workflow_is_immutably_bound_to_complete_native_transport PASSED [  5%]<br>tests/test_orchestrator_runtime.py::test_final_review_structured_records_use_branch_wide_fingerprint PASSED [  7%]<br>tests/test_orchestrator_runtime.py::test_review_packet_material<br>...[6956 characters omitted]...<br>ts/test_orchestrator_runtime.py::test_head_drift_after_plan_becomes_typed_persisted_halt PASSED [ 89%]<br>tests/test_orchestrator_runtime.py::test_empty_implementation_is_a_typed_halt_not_cli_crash PASSED [ 91%]<br>tests/test_orchestrator_runtime.py::test_internal_plan_validation_honors_exact_approved_hotfix_paths PASSED [ 92%]<br>tests/test_orchestrator_runtime.py::test_plan_only_retries_non_handoff_plan_once_then_halts_before_review PASSED [ 94%]<br>tests/test_orchestrator_runtime.py::test_plan_only_repairs_handoff_contract_before_review PASSED [ 95%]<br>tests/test_orchestrator_runtime.py::test_completed_plan_resume_retries_failed_handoff_without_agents PASSED [ 97%]<br>tests/test_orchestrator_runtime.py::test_explicit_resume_of_watch_origin_runs_terminal_workflow_once_then_finalizes_queue PASSED [ 98%]<br>tests/test_orchestrator_runtime.py::test_explicit_resume_with_bound_success_and_corrupt_state_returns_one PASSED [100%]<br><br>============================= 69 passed in 16.81s ============================== |
| python3 -m pytest tests/test_inbox_watcher.py -v | PASS | 0 | ============================= test session starts ==============================<br>platform linux -- Python 3.12.3, pytest-7.4.4, pluggy-1.4.0 -- /usr/bin/python3<br>cachedir: .pytest_cache<br>rootdir: /mnt/c/Users/Diete/Sync/DE_Privat/Rente/ChatGPT CLI/Dual-Agent-Orchestrator<br>configfile: pyproject.toml<br>collecting ... collected 44 items<br><br>tests/test_inbox_watcher.py::test_bound_queue_success_reserves_once_and_recovers_after_move PASSED [  2%]<br>tests/test_inbox_watcher.py::test_bound_queue_success_converges_after_each_interruption_boundary[before_move] PASSED [  4%]<br>tests/test_inbox_watcher.py::test_bound_queue_success_converges_after_each_interruption_boundary[after_move] PASSED [  6%]<br>tests/test_inbox_watcher.py::test_bound_queue_success_converges_after_each_interruption_boundary[attempt_cleanup] PASSED [  9%]<br>tests/test_inbox_watcher.py::test_bound_queue_success_converges_after_each_interruption_boundary[identity_cleanup] PASSED [ 11%]<br>tests/test_inbox_watcher.py::test_bound_queue_success_conv<br>...[3020 characters omitted]...<br>cher.py::test_watch_keeps_unchanged_bootstrap_denial_resumable_until_external_repair PASSED [ 81%]<br>tests/test_inbox_watcher.py::test_non_resumable_policy_halt_stops_once_without_retry_or_poison PASSED [ 84%]<br>tests/test_inbox_watcher.py::test_watch_processes_generated_implementation_handoff_without_restart PASSED [ 86%]<br>tests/test_inbox_watcher.py::test_process_interruption_preserves_identity_for_next_watch_process PASSED [ 88%]<br>tests/test_inbox_watcher.py::test_technical_retry_uses_stable_run_id_and_resume_context PASSED [ 90%]<br>tests/test_inbox_watcher.py::test_pre_state_technical_retry_restarts_fresh_with_same_run_id PASSED [ 93%]<br>tests/test_inbox_watcher.py::test_fifo_tasks_receive_distinct_isolated_run_ids PASSED [ 95%]<br>tests/test_inbox_watcher.py::test_changed_paused_task_halts_without_retry_or_poison PASSED [ 97%]<br>tests/test_inbox_watcher.py::test_watch_fails_fast_when_lock_already_held PASSED [100%]<br><br>============================== 44 passed in 0.71s ============================== |

### Ereignis 3: `validation-c52a7e8c9ab6`

- Diff-Fingerprint: `c52a7e8c9ab6`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: 1 passed; 0 failed; 0 unavailable; 1 required
- Ausgabedigest: `7cca8a99c212`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| python3 -m pytest tests/ -v | PASS | 0 | ============================= test session starts ==============================<br>platform linux -- Python 3.12.3, pytest-7.4.4, pluggy-1.4.0 -- /usr/bin/python3<br>cachedir: .pytest_cache<br>rootdir: /mnt/c/Users/Diete/Sync/DE_Privat/Rente/ChatGPT CLI/Dual-Agent-Orchestrator<br>configfile: pyproject.toml<br>collecting ... collected 1042 items<br><br>tests/test_agent_adapters.py::test_registry_constructs_only_native_adapters PASSED [  0%]<br>tests/test_agent_adapters.py::test_native_adapter_api_and_mro_are_closed PASSED [  0%]<br>tests/test_agent_adapters.py::test_native_codex_prepares_schema_request_and_assets PASSED [  0%]<br>tests/test_agent_adapters.py::test_native_codex_extracts_only_bound_result PASSED [  0%]<br>tests/test_agent_adapters.py::test_native_claude_workspace_binding_and_capability_smoke PASSED [  0%]<br>tests/test_agent_adapters.py::test_native_claude_prepares_request_components_and_bound_output PASSED [  0%]<br>tests/test_agent_runtime.py::test_compute_retry_backoff_seconds_exponential PASSED [  0%]<br>tes<br>...[120266 characters omitted]...<br>_pre_slice11_v3_gate_and_work_unit_shapes_load_with_empty_new_fields PASSED [ 99%]<br>tests/test_workflow_state.py::test_early_slice11_work_unit_shape_loads_without_active_test_evidence PASSED [ 99%]<br>tests/test_workflow_state.py::test_policy_gate_roundtrips_and_resumes_at_same_step PASSED [ 99%]<br>tests/test_workflow_state.py::test_managed_audit_path_roundtrips_and_rejects_unsafe_locations PASSED [ 99%]<br>tests/test_workflow_state.py::test_policy_gate_rejects_fingerprint_bound_reason_and_unsafe_path PASSED [ 99%]<br>tests/test_workflow_state.py::test_unexpected_file_user_gate_decision_roundtrips PASSED [ 99%]<br>tests/test_workflow_state.py::test_plan_time_slice_one_start_commit_cannot_be_rebound_after_resume PASSED [ 99%]<br>tests/test_workflow_state.py::test_in_progress_slice_can_extend_exact_remediation_scope PASSED [ 99%]<br>tests/test_workflow_state.py::test_state_scope_rejects_orchestrator_internal_paths PASSED [100%]<br><br>======================= 1042 passed in 65.15s (0:01:05) ======================== |

<!-- artifact-records:validation-attestation:begin -->
Semantischer Record-Digest: `3253cc7779e8`

- 4. `ar1-1d7c11cbca41`: Providerinput `codex/codex_final_correction` = `allowed`; local_input_chars `94516/4000000`, local_input_bytes `94658/16000000`; local_input_digest `620a27ee39a4`, Policy `9edf600f09ac`, Übergang `f19d72414655`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, local_input_largest_component `evidence_asset_001`; local_input_component_count `3`; Komponenten `stdin_prompt=9003/9005, response_schema=3801/3801, evidence_asset_001=81712/81852`
### Validierungsanforderung

| Seq/Record | Rolle | Befehle mit argv-Grenzen |
|---|---|---|
| 12. `ar1-967ca103017c` | `orchestrator` | `argv` [`python3`, `-m`, `pytest`, `tests/`, `-v`]; `argv` [`python3`, `-m`, `pytest`, `tests/test_orchestrator_runtime.py`, `-v`]; `argv` [`python3`, `-m`, `pytest`, `tests/test_inbox_watcher.py`, `-v`] |

### Validierungsattestierung

| Seq/Record | Rolle | Fingerprint |
|---|---|---|
| 13. `ar1-9fe4ce7e0f23` | `orchestrator` | `a9fa36d46c24` |

| Status | Exit | Output-Digest | Befehl mit argv-Grenzen |
|---|---:|---|---|
| `pass` | `0` | `125cd498c228` | `argv` [`python3`, `-m`, `pytest`, `tests/`, `-v`] |
| `pass` | `0` | `91bfd339f979` | `argv` [`python3`, `-m`, `pytest`, `tests/test_orchestrator_runtime.py`, `-v`] |
| `pass` | `0` | `342dc97ce18d` | `argv` [`python3`, `-m`, `pytest`, `tests/test_inbox_watcher.py`, `-v`] |
- 14. `ar1-c80aeb5c9f2e`: Providerinput `claude/claude_slice_review` = `allowed`; local_input_chars `60911/4000000`, local_input_bytes `60924/16000000`; local_input_digest `09d816e5ba06`, Policy `9edf600f09ac`, Übergang `3aaff746c5b2`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, local_input_largest_component `request_chunk_001`; local_input_component_count `7`; Komponenten `request_chunk_001=24000/24013, request_chunk_002=24000/24000, request_chunk_003=174/174, packet_manifest=778/778, system_policy=217/217, response_schema=11512/11512, start_directive=230/230`
### Validierungsanforderung

| Seq/Record | Rolle | Befehle mit argv-Grenzen |
|---|---|---|
| 23. `ar1-0e9e1ad30e3b` | `orchestrator` | `argv` [`python3`, `-m`, `pytest`, `tests/`, `-v`] |

### Validierungsattestierung

| Seq/Record | Rolle | Fingerprint |
|---|---|---|
| 24. `ar1-cb3ef35479c0` | `orchestrator` | `c52a7e8c9ab6` |

| Status | Exit | Output-Digest | Befehl mit argv-Grenzen |
|---|---:|---|---|
| `pass` | `0` | `83a1acbe8a23` | `argv` [`python3`, `-m`, `pytest`, `tests/`, `-v`] |
- 25. `ar1-ff13ddc0a17b`: Providerinput `claude/claude_slice_review` = `allowed`; local_input_chars `60648/4000000`, local_input_bytes `60669/16000000`; local_input_digest `c3cbdc3d4656`, Policy `9edf600f09ac`, Übergang `22ef47e1b114`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, local_input_largest_component `request_chunk_001`; local_input_component_count `7`; Komponenten `request_chunk_001=24000/24013, request_chunk_002=24000/24008, request_chunk_003=1067/1067, packet_manifest=779/779, system_policy=217/217, response_schema=10355/10355, start_directive=230/230`
- 29. `ar1-b69666e6676b`: Providerinput `claude/claude_slice_review` = `allowed`; local_input_chars `60648/4000000`, local_input_bytes `60669/16000000`; local_input_digest `c3cbdc3d4656`, Policy `9edf600f09ac`, Übergang `5ce5839c724e`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, local_input_largest_component `request_chunk_001`; local_input_component_count `7`; Komponenten `request_chunk_001=24000/24013, request_chunk_002=24000/24008, request_chunk_003=1067/1067, packet_manifest=779/779, system_policy=217/217, response_schema=10355/10355, start_directive=230/230`
- Providerattempt-Summe Run `watch-20260826-202634.812746Z-f4353362bc49` / Operation `provider-operation-28186d1d802f` (`codex/codex_final_correction`; Modell `gpt-5.6-sol`; Effort `medium`): Attempts `1`, offen `0`, Duration `286.282981` (bekannt `1`, unbekannt `0`); Inputzeichen `94516`, Inputbytes `94658`; Retrystatus `single-attempt`; input_tokens=sum:unknown,known:0,unknown:1; tool_input_tokens=sum:unknown,known:0,unknown:1; cache_read_input_tokens=sum:unknown,known:0,unknown:1; cache_creation_input_tokens=sum:unknown,known:0,unknown:1; thinking_tokens=sum:unknown,known:0,unknown:1; output_tokens=sum:unknown,known:0,unknown:1; total_tokens=sum:unknown,known:0,unknown:1; turns=sum:unknown,known:0,unknown:1; cost_usd=sum:unknown,known:0,unknown:1
  - 11. `ar1-79b89d4ee362`: Attempt `1` = `succeeded`; Messung `ar1-1d7c11cbca41`; Modell `gpt-5.6-sol`; Effort `medium`; Inputzeichen `94516`; Inputbytes `94658`; Duration `286.28298104705755`; Fehler `none`; Usage `unknown`
- Providerattempt-Summe Run `watch-20260826-202634.812746Z-f4353362bc49` / Operation `provider-operation-307f24054cb0` (`claude/claude_slice_review`; Modell `sonnet`; Effort `high`): Attempts `1`, offen `0`, Duration `161.258512` (bekannt `1`, unbekannt `0`); Inputzeichen `60911`, Inputbytes `60924`; Retrystatus `single-attempt`; input_tokens=sum:6,known:1,unknown:0; tool_input_tokens=sum:unknown,known:0,unknown:1; cache_read_input_tokens=sum:16048,known:1,unknown:0; cache_creation_input_tokens=sum:29349,known:1,unknown:0; thinking_tokens=sum:unknown,known:0,unknown:1; output_tokens=sum:11840,known:1,unknown:0; total_tokens=sum:unknown,known:0,unknown:1; turns=sum:6,known:1,unknown:0; cost_usd=sum:0.2400216,known:1,unknown:0
  - 21. `ar1-e2b939faae8a`: Attempt `1` = `succeeded`; Messung `ar1-c80aeb5c9f2e`; Modell `sonnet`; Effort `high`; Inputzeichen `60911`; Inputbytes `60924`; Duration `161.25851192197297`; Fehler `none`; Usage `input_tokens=6, tool_input_tokens=unknown, cache_read_input_tokens=16048, cache_creation_input_tokens=29349, thinking_tokens=unknown, output_tokens=11840, total_tokens=unknown, turns=6, cost_usd=0.2400216`
- Providerattempt-Summe Run `watch-20260826-202634.812746Z-f4353362bc49` / Operation `provider-operation-72d74c1b7970` (`claude/claude_slice_review`; Modell `sonnet`; Effort `high`): Attempts `2`, offen `0`, Duration `536.570940` (bekannt `2`, unbekannt `0`); Inputzeichen `60648`, Inputbytes `60669`; Retrystatus `retried`; input_tokens=sum:22,known:2,unknown:0; tool_input_tokens=sum:unknown,known:0,unknown:2; cache_read_input_tokens=sum:208030,known:2,unknown:0; cache_creation_input_tokens=sum:72211,known:2,unknown:0; thinking_tokens=sum:unknown,known:0,unknown:2; output_tokens=sum:50068,known:2,unknown:0; total_tokens=sum:unknown,known:0,unknown:2; turns=sum:16,known:2,unknown:0; cost_usd=sum:0.8331850000000001,known:2,unknown:0
  - 27. `ar1-e9fb04013e4a`: Attempt `1` = `failed`; Messung `ar1-ff13ddc0a17b`; Modell `sonnet`; Effort `high`; Inputzeichen `60648`; Inputbytes `60669`; Duration `291.6073314130772`; Fehler `network`; Usage `input_tokens=12, tool_input_tokens=unknown, cache_read_input_tokens=138971, cache_creation_input_tokens=49411, thinking_tokens=unknown, output_tokens=27376, total_tokens=unknown, turns=10, cost_usd=0.5002272`
  - 32. `ar1-a93ae3255c70`: Attempt `2` = `succeeded`; Messung `ar1-b69666e6676b`; Modell `sonnet`; Effort `high`; Inputzeichen `60648`; Inputbytes `60669`; Duration `244.96360820997506`; Fehler `none`; Usage `input_tokens=10, tool_input_tokens=unknown, cache_read_input_tokens=69059, cache_creation_input_tokens=22800, thinking_tokens=unknown, output_tokens=22692, total_tokens=unknown, turns=6, cost_usd=0.3329578`
<!-- artifact-records:validation-attestation:end -->
<!-- audit:validation-attestation:end -->

## Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
- Teständerungsfreigabe: nicht erfasst.
- Pre-Mortems:
  - Ereignis 2: This approval could prove wrong if: (1) the dev/inode identity comparison in _task_digest is defeated by inode reuse on a filesystem that recycles inode numbers within the attack window, letting a swapped symlink pass fstat verification undetected; (2) a platform lacking O_NOFOLLOW is in scope for production deployment and the reduced identity-only check is insufficient against a determined local attacker with inbox write access; (3) the still-open TOCTOU between move_to_reserved_outbox's final lstat and the actual shutil.move call is exploited by a sufficiently fast concurrent writer, since the mitigation narrows rather than eliminates the race; (4) the unrelated mirror_findings correction-id filtering change in orchestrator.py, while covered by one new assertion, has untested interactions with multi-round correction histories beyond the single scenario exercised.<br>All four reviewer-owned findings (C-01, C-03, C-04, C-05) have concrete regression tests matching their stated acceptance criteria and are recorded PASS inside a validation_attestation whose diff_fingerprint equals the current review fingerprint, every changed path is inside the authorized allowlist for this slice, and no reviewer-owned BLOCKER remains open, so approval is warranted despite the narrow residual risks documented above.
  - Ereignis 4: If this approval turns out wrong, the most likely cause is the narrow residual TOCTOU window between move_to_reserved_outbox's pre-move lstat check and the actual shutil.move call: a sufficiently fast concurrent writer with inbox filesystem access could still, in principle, swap the source in that instant and have a symlinked or substituted file moved into outbox/done, even though every currently-tested swap point (before digest, before move) is now rejected.<br>A second, lower-probability failure mode is the new HEAD-drift commit gate (WorkflowCommitApprovalRequired) or the mirror_findings correction-id filtering introducing a subtle resume/idempotency edge case outside the exact scenarios exercised by their new unit tests (e.g.<br>a Slice whose HEAD legitimately advances between rounds for unrelated administrative reasons, or a correction whose finding_ids scope inadvertently drops a still-relevant historical finding from a future authoritative comparison); both are well covered by dedicated new tests and the full 1042-test suite passes at the exact reviewed fingerprint, but neither was part of the specific findings this correction round was scoped to resolve, so their broader interaction surface has necessarily received less adversarial scrutiny here than C-01/C-03 themselves.

<!-- artifact-records:test-approval-premortem:begin -->
Semantischer Record-Digest: `3253cc7779e8`

### Gate-Ereignisse

| Seq/Record | Gate | Status | Autorität | Fingerprint | Begründung |
|---|---|---|---|---|---|
| 22. `ar1-2f96188b8d89` | `unexpected-file` | `approved` | `user` | `c52a7e8c9ab6` | Geprüfter HEAD-Drift-Gate-Hotfix 1143fca für Fingerprint<br>    c52a7e8c9ab6: Ein nach Slice-Start veränderter HEAD wird vor der<br>    Commit-Transaktion als exaktes fingerprint- und pfadgebundenes Nutzergate persistiert.<br>Freigegebene Zusatzpfade:<br>    src/workflow.py und tests/test_workflow.py.<br>Vollständige Suite mit 1042 Tests bestanden; git diff --check sauber. |
<!-- artifact-records:test-approval-premortem:end -->
<!-- audit:test-approval-premortem:end -->

## Findings-Lebenszyklus

<!-- audit:findings:begin -->
### `C-01` — `CLOSED`

- Quelle: `claude`; Runde 1
- Klasse: `BLOCKER`
- Finding: src/orchestrator.py run_pipeline: in the new direct-queue-resume branch that recovers a pending bound success marker, the call to load_resumable_workflow_state(...) is guarded only by &#96;except ValueError as exc:&#96;.<br>Every other call site in the same function that can fail while loading/resuming Structured-v2 state (the try block around run_default_dry_run_fn/run_production_workflow_fn a few lines below) explicitly treats ArtifactResumeError and StateSchemaError as the fail-closed signal and converts them into a logged diagnostic plus &#96;return 1&#96;.<br>If state.json is missing, corrupted, or bound to an unsupported protocol while a bound &#96;.success&#96; marker exists (a plausible resume scenario per the repo's fail-closed resume contract), this narrower except clause may let ArtifactResumeError/StateSchemaError propagate uncaught out of the bound-recovery path instead of producing the same controlled diagnostic, weakening the resume/idempotency and failure-path guarantees this slice is meant to add.
- Akzeptanztest: Add an orchestrator test that leaves a bound (dict) &#96;.success&#96; marker in place while making &#96;.orchestrator/state.json&#96; unreadable, corrupt, or bound to an unsupported/legacy protocol, then invokes run_pipeline through the literal &#96;--resume --task-file&#96; direct path; assert it returns exit code 1 with a logged error rather than raising ArtifactResumeError/StateSchemaError uncaught, matching the handling used for the same exception classes elsewhere in run_pipeline.
- Statusbegründung: src/orchestrator.py run_pipeline's direct-queue bound-marker recovery branch now catches &#96;(ArtifactResumeError, StateSchemaError, ValueError)&#96; instead of &#96;ValueError&#96; alone, matching the fail-closed handling used at the sibling load_resumable_workflow_state call site in the same function.<br>The new regression test test_explicit_resume_with_bound_success_and_corrupt_state_returns_one (tests/test_orchestrator_runtime.py) leaves a bound success marker, corrupts .orchestrator/state.json, invokes run_pipeline via the literal --resume --task-file path, and asserts exit code 1 with 'Direct queue recovery rejected' logged; it is present and PASSED in the fingerprint-bound attestation output.

### `C-03` — `CLOSED`

- Quelle: `claude`; Runde 1
- Klasse: `BLOCKER`
- Finding: In inbox_watcher.py, _validate_queue_paths() checks &#96;task_file.is_symlink()&#96; exactly once before finalize_queue_success() later performs _task_digest(task_file) and move_to_reserved_outbox(task_file, destination).<br>Between that single check and the later filesystem operations there is a TOCTOU window in which the queue source path could be swapped for a symlink (e.g., by a concurrent process with inbox write access), causing the digest read and/or move to follow attacker- or bug-controlled content instead of the validated regular file.
- Akzeptanztest: Add a regression test that replaces the queue task source with a symlink between the initial _validate_queue_paths() symlink check and the subsequent digest/move step inside finalize_queue_success() (e.g., via a monkeypatched hook invoked mid-function), and assert finalize_queue_success() still returns a FAILED disposition instead of following the symlink.
- Statusbegründung: inbox_watcher._task_digest now opens the task file with O_NOFOLLOW (where available) and compares the pre-open lstat dev/inode against the post-open fstat dev/inode, rejecting any path that was swapped to a symlink between validation and digesting.<br>move_to_reserved_outbox additionally re-validates task_file.lstat() immediately before shutil.move and raises if it is no longer a regular file.<br>The new regression test test_bound_queue_success_rejects_source_swapped_to_symlink_before_move swaps the source to a symlink inside move_to_reserved_outbox (mid-finalization) and asserts finalize_queue_success returns FAILED, the symlink is left untouched, the outside target content is unread/unmoved, and outbox/done stays empty.<br>It is present and PASSED in the fingerprint-bound attestation output.

### `C-04` — `CLOSED`

- Quelle: `claude`; Runde 1
- Klasse: `BLOCKER`
- Finding: src/orchestrator.py run_pipeline's new direct-queue-resume bound-marker recovery branch wraps load_resumable_workflow_state(...) in &#96;except ValueError as exc:&#96; only.<br>Every sibling call site in the same function that can fail loading/resuming structured-v2 state explicitly also catches ArtifactResumeError and StateSchemaError and converts them into a logged diagnostic plus &#96;return 1&#96;.<br>A bound &#96;.success&#96; marker can legitimately exist while &#96;.orchestrator/state.json&#96; is missing, corrupted, or bound to an unsupported/legacy protocol -- exactly the conditions load_resumable_workflow_state raises ArtifactResumeError/StateSchemaError for -- so this narrower except clause already merged in this diff lets a genuine fail-closed resume error propagate uncaught out of the very recovery path this slice exists to harden, instead of the controlled diagnostic used everywhere else.<br>This is a live, present defect in the merged code (not a hypothetical future regression) and directly weakens the resume/idempotency and failure-path guarantees this slice is required to deliver; it was already reported at round 1 as C-01, accepted by Codex, and remains unfixed at the current fingerprint under final review.
- Akzeptanztest: VALIDATE: ["python3","-m","pytest","tests/test_orchestrator_runtime.py","-v"]
- Statusbegründung: C-04 restates C-01 at the final gate.<br>The same except-clause widening in run_pipeline and the same test_explicit_resume_with_bound_success_and_corrupt_state_returns_one regression resolve it; the required VALIDATE command &#96;python3 -m pytest tests/test_orchestrator_runtime.py -v&#96; is recorded PASS (69 passed) in the diff-fingerprint-bound validation_attestation for this exact fingerprint a9fa36d46c24.

### `C-05` — `CLOSED`

- Quelle: `claude`; Runde 1
- Klasse: `BLOCKER`
- Finding: In src/inbox_watcher.py, _validate_queue_paths() checks task_file.is_symlink() exactly once before finalize_queue_success() later performs _task_digest(task_file) and move_to_reserved_outbox(task_file, destination).<br>This leaves a TOCTOU window, already present in the merged code, in which the queue source path can be swapped for a symlink between the single upfront check and the later digest/move filesystem operations (e.g.<br>by a concurrent process with inbox write access), causing the digest computation and/or the move-to-outbox/done step to follow attacker- or bug-controlled content instead of the validated regular file.<br>This is a genuine security-boundary gap in the queue-finalization path this slice introduces, not a documentation-only residual risk; it was already reported at round 1 as C-03, accepted by Codex, and remains unfixed at the current fingerprint under final review.
- Akzeptanztest: VALIDATE: ["python3","-m","pytest","tests/test_inbox_watcher.py","-v"]
- Statusbegründung: C-05 restates C-03 at the final gate.<br>The same O_NOFOLLOW/fstat-identity digest hardening plus pre-move lstat re-check, and the same symlink-swap regression test, resolve it; the required VALIDATE command &#96;python3 -m pytest tests/test_inbox_watcher.py -v&#96; is recorded PASS (44 passed) in the diff-fingerprint-bound validation_attestation for this exact fingerprint a9fa36d46c24, and the full suite command (&#96;python3 -m pytest tests/ -v&#96;, 1041 passed) also PASSED at the same fingerprint.

<!-- artifact-records:findings:begin -->
Semantischer Record-Digest: `3253cc7779e8`

### Finding-Ereignisse

| Seq/Record | Finding | Rolle | Runde | Aktion | Klasse | Status | Begründung |
|---|---|---|---:|---|---|---|---|
| 7. `ar1-bcb051572d83` | `C-01` | `codex` | `1` | `responded` | `BLOCKER` | `open` | The direct bound-marker resume branch now catches ArtifactResumeError and StateSchemaError and returns a logged exit code<br>1.<br>A literal --resume --task-file regression test covers corrupt state. |
| 8. `ar1-593466bcccef` | `C-03` | `codex` | `1` | `responded` | `BLOCKER` | `open` | Queue digest reads now reject symlinks and path/descriptor identity changes, and the move boundary revalidates a regular non-symlink source.<br>A mid-finalization symlink-swap regression confirms FAILED disposition. |
| 9. `ar1-f151bffe46f2` | `C-04` | `codex` | `1` | `responded` | `BLOCKER` | `open` | Resolved by the same controlled structured-resume exception handling and orchestrator runtime regression as C-01. |
| 10. `ar1-152be52b64f6` | `C-05` | `codex` | `1` | `responded` | `BLOCKER` | `open` | Resolved by the same no-follow queue-source hardening and symlink-swap regression as C-03.<br>The full suite passed with 1041 tests. |
| 17. `ar1-7a34b914b9ae` | `C-01` | `claude` | `1` | `status_changed` | `BLOCKER` | `closed` | src/orchestrator.py run_pipeline's direct-queue bound-marker recovery branch now catches &#96;(ArtifactResumeError, StateSchemaError, ValueError)&#96; instead of &#96;ValueError&#96; alone, matching the fail-closed handling used at the sibling load_resumable_workflow_state call site in the same function.<br>The new regression test test_explicit_resume_with_bound_success_and_corrupt_state_returns_one (tests/test_orchestrator_runtime.py) leaves a bound success marker, corrupts .orchestrator/state.json, invokes run_pipeline via the literal --resume --task-file path, and asserts exit code 1 with 'Direct queue recovery rejected' logged; it is present and PASSED in the fingerprint-bound attestation output. |
| 18. `ar1-b160dd911c7d` | `C-03` | `claude` | `1` | `status_changed` | `BLOCKER` | `closed` | inbox_watcher._task_digest now opens the task file with O_NOFOLLOW (where available) and compares the pre-open lstat dev/inode against the post-open fstat dev/inode, rejecting any path that was swapped to a symlink between validation and digesting.<br>move_to_reserved_outbox additionally re-validates task_file.lstat() immediately before shutil.move and raises if it is no longer a regular file.<br>The new regression test test_bound_queue_success_rejects_source_swapped_to_symlink_before_move swaps the source to a symlink inside move_to_reserved_outbox (mid-finalization) and asserts finalize_queue_success returns FAILED, the symlink is left untouched, the outside target content is unread/unmoved, and outbox/done stays empty.<br>It is present and PASSED in the fingerprint-bound attestation output. |
| 19. `ar1-4d27d472de4e` | `C-04` | `claude` | `1` | `status_changed` | `BLOCKER` | `closed` | C-04 restates C-01 at the final gate.<br>The same except-clause widening in run_pipeline and the same test_explicit_resume_with_bound_success_and_corrupt_state_returns_one regression resolve it; the required VALIDATE command &#96;python3 -m pytest tests/test_orchestrator_runtime.py -v&#96; is recorded PASS (69 passed) in the diff-fingerprint-bound validation_attestation for this exact fingerprint a9fa36d46c24. |
| 20. `ar1-1f9426eb01a4` | `C-05` | `claude` | `1` | `status_changed` | `BLOCKER` | `closed` | C-05 restates C-03 at the final gate.<br>The same O_NOFOLLOW/fstat-identity digest hardening plus pre-move lstat re-check, and the same symlink-swap regression test, resolve it; the required VALIDATE command &#96;python3 -m pytest tests/test_inbox_watcher.py -v&#96; is recorded PASS (44 passed) in the diff-fingerprint-bound validation_attestation for this exact fingerprint a9fa36d46c24, and the full suite command (&#96;python3 -m pytest tests/ -v&#96;, 1041 passed) also PASSED at the same fingerprint. |

### Native convergence summary

| Finding | Work units | Rounds | Fingerprints | Claude decisions | Codex dispositions | Final status |
|---|---|---|---|---|---|---|
| `C-01` | `4` | `1` | `a9fa36d46c24` | `status_changed:closed` | `accepted` | `closed` |
| `C-03` | `4` | `1` | `a9fa36d46c24` | `status_changed:closed` | `accepted` | `closed` |
| `C-04` | `4` | `1` | `a9fa36d46c24` | `status_changed:closed` | `accepted` | `closed` |
| `C-05` | `4` | `1` | `a9fa36d46c24` | `status_changed:closed` | `accepted` | `closed` |
<!-- artifact-records:findings:end -->
<!-- audit:findings:end -->

## Entscheidungstabelle

<!-- audit:decision-table:begin -->
| ID | Quelle | Finding | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|---|
| C-01 | claude | src/orchestrator.py run_pipeline: in the new direct-queue-resume branch that recovers a pending bound success marker, the call to load_resumable_workflow_state(...) is guarded only by &#96;except ValueError as exc:&#96;. Every other call site in the same function that can fail while loading/resuming Structured-v2 state (the try block around run_default_dry_run_fn/run_production_workflow_fn a few lines below) explicitly treats ArtifactResumeError and StateSchemaError as the fail-closed signal and converts them into a logged diagnostic plus &#96;return 1&#96;. If state.json is missing, corrupted, or bound to an unsupported protocol while a bound &#96;.success&#96; marker exists (a plausible resume scenario per the repo's fail-closed resume contract), this narrower except clause may let ArtifactResumeError/StateSchemaError propagate uncaught out of the bound-recovery path instead of producing the same controlled diagnostic, weakening the resume/idempotency and failure-path guarantees this slice is meant to add. | BLOCKER | angenommen | erledigt: src/orchestrator.py run_pipeline's direct-queue bound-marker recovery branch now catches &#96;(ArtifactResumeError, StateSchemaError, ValueError)&#96; instead of &#96;ValueError&#96; alone, matching the fail-closed handling used at the sibling load_resumable_workflow_state call site in the same function. The new regression test test_explicit_resume_with_bound_success_and_corrupt_state_returns_one (tests/test_orchestrator_runtime.py) leaves a bound success marker, corrupts .orchestrator/state.json, invokes run_pipeline via the literal --resume --task-file path, and asserts exit code 1 with 'Direct queue recovery rejected' logged; it is present and PASSED in the fingerprint-bound attestation output. |
| C-03 | claude | In inbox_watcher.py, _validate_queue_paths() checks &#96;task_file.is_symlink()&#96; exactly once before finalize_queue_success() later performs _task_digest(task_file) and move_to_reserved_outbox(task_file, destination). Between that single check and the later filesystem operations there is a TOCTOU window in which the queue source path could be swapped for a symlink (e.g., by a concurrent process with inbox write access), causing the digest read and/or move to follow attacker- or bug-controlled content instead of the validated regular file. | BLOCKER | angenommen | erledigt: inbox_watcher._task_digest now opens the task file with O_NOFOLLOW (where available) and compares the pre-open lstat dev/inode against the post-open fstat dev/inode, rejecting any path that was swapped to a symlink between validation and digesting. move_to_reserved_outbox additionally re-validates task_file.lstat() immediately before shutil.move and raises if it is no longer a regular file. The new regression test test_bound_queue_success_rejects_source_swapped_to_symlink_before_move swaps the source to a symlink inside move_to_reserved_outbox (mid-finalization) and asserts finalize_queue_success returns FAILED, the symlink is left untouched, the outside target content is unread/unmoved, and outbox/done stays empty. It is present and PASSED in the fingerprint-bound attestation output. |
| C-04 | claude | src/orchestrator.py run_pipeline's new direct-queue-resume bound-marker recovery branch wraps load_resumable_workflow_state(...) in &#96;except ValueError as exc:&#96; only. Every sibling call site in the same function that can fail loading/resuming structured-v2 state explicitly also catches ArtifactResumeError and StateSchemaError and converts them into a logged diagnostic plus &#96;return 1&#96;. A bound &#96;.success&#96; marker can legitimately exist while &#96;.orchestrator/state.json&#96; is missing, corrupted, or bound to an unsupported/legacy protocol -- exactly the conditions load_resumable_workflow_state raises ArtifactResumeError/StateSchemaError for -- so this narrower except clause already merged in this diff lets a genuine fail-closed resume error propagate uncaught out of the very recovery path this slice exists to harden, instead of the controlled diagnostic used everywhere else. This is a live, present defect in the merged code (not a hypothetical future regression) and directly weakens the resume/idempotency and failure-path guarantees this slice is required to deliver; it was already reported at round 1 as C-01, accepted by Codex, and remains unfixed at the current fingerprint under final review. | BLOCKER | angenommen | erledigt: C-04 restates C-01 at the final gate. The same except-clause widening in run_pipeline and the same test_explicit_resume_with_bound_success_and_corrupt_state_returns_one regression resolve it; the required VALIDATE command &#96;python3 -m pytest tests/test_orchestrator_runtime.py -v&#96; is recorded PASS (69 passed) in the diff-fingerprint-bound validation_attestation for this exact fingerprint a9fa36d46c24. |
| C-05 | claude | In src/inbox_watcher.py, _validate_queue_paths() checks task_file.is_symlink() exactly once before finalize_queue_success() later performs _task_digest(task_file) and move_to_reserved_outbox(task_file, destination). This leaves a TOCTOU window, already present in the merged code, in which the queue source path can be swapped for a symlink between the single upfront check and the later digest/move filesystem operations (e.g. by a concurrent process with inbox write access), causing the digest computation and/or the move-to-outbox/done step to follow attacker- or bug-controlled content instead of the validated regular file. This is a genuine security-boundary gap in the queue-finalization path this slice introduces, not a documentation-only residual risk; it was already reported at round 1 as C-03, accepted by Codex, and remains unfixed at the current fingerprint under final review. | BLOCKER | angenommen | erledigt: C-05 restates C-03 at the final gate. The same O_NOFOLLOW/fstat-identity digest hardening plus pre-move lstat re-check, and the same symlink-swap regression test, resolve it; the required VALIDATE command &#96;python3 -m pytest tests/test_inbox_watcher.py -v&#96; is recorded PASS (44 passed) in the diff-fingerprint-bound validation_attestation for this exact fingerprint a9fa36d46c24, and the full suite command (&#96;python3 -m pytest tests/ -v&#96;, 1041 passed) also PASSED at the same fingerprint. |

<!-- artifact-records:decision-table:begin -->
Semantischer Record-Digest: `3253cc7779e8`

| Seq | Record | Typ | Status | Logische ID | Revision | Fingerprint |
|---:|---|---|---|---|---:|---|
| 1 | `ar1-0a52825429b1` | `task` | `accepted` | `task-contract` | 1 | `contract:a9c0a53867bf` |
| 2 | `ar1-0026180e240d` | `plan` | `approved` | `approved-plan` | 1 | `contract:a9c0a53867bf` |
| 3 | `ar1-72836ddc4aa9` | `correction_work_unit` | `active` | `work-unit-4` | 1 | `contract:a9c0a53867bf` |
| 4 | `ar1-1d7c11cbca41` | `provider_input_measurement` | `measured` | `provider-input-4-codex_final_correction` | 1 | `implementation:4ac654a4545b` |
| 5 | `ar1-d7459d09a2de` | `provider_attempt` | `started` | `provider-operation-28186d1d802f-1` | 1 | `implementation:4ac654a4545b` |
| 6 | `ar1-460ead36f754` | `agent_result` | `ready` | `agent-4-codex_final_correction-1` | 1 | `implementation:a9fa36d46c24` |
| 7 | `ar1-bcb051572d83` | `finding_transition` | `recorded` | `finding-C-01` | 4 | `implementation:a9fa36d46c24` |
| 8 | `ar1-593466bcccef` | `finding_transition` | `recorded` | `finding-C-03` | 4 | `implementation:a9fa36d46c24` |
| 9 | `ar1-f151bffe46f2` | `finding_transition` | `recorded` | `finding-C-04` | 2 | `implementation:a9fa36d46c24` |
| 10 | `ar1-152be52b64f6` | `finding_transition` | `recorded` | `finding-C-05` | 2 | `implementation:a9fa36d46c24` |
| 11 | `ar1-79b89d4ee362` | `provider_attempt` | `succeeded` | `provider-operation-28186d1d802f-1` | 2 | `implementation:4ac654a4545b` |
| 12 | `ar1-967ca103017c` | `validation_request` | `requested` | `validation-request-a9fa36d46c24` | 1 | `implementation:a9fa36d46c24` |
| 13 | `ar1-9fe4ce7e0f23` | `validation_attestation` | `attested` | `validation-a9fa36d46c24` | 1 | `implementation:a9fa36d46c24` |
| 14 | `ar1-c80aeb5c9f2e` | `provider_input_measurement` | `measured` | `provider-input-4-claude_slice_review` | 1 | `implementation:a9fa36d46c24` |
| 15 | `ar1-0d8f9307add0` | `provider_attempt` | `started` | `provider-operation-307f24054cb0-1` | 1 | `implementation:a9fa36d46c24` |
| 16 | `ar1-f82d0c97029f` | `review` | `decided` | `review-claude-4-1` | 1 | `implementation:a9fa36d46c24` |
| 17 | `ar1-7a34b914b9ae` | `finding_transition` | `recorded` | `finding-C-01` | 5 | `implementation:a9fa36d46c24` |
| 18 | `ar1-b160dd911c7d` | `finding_transition` | `recorded` | `finding-C-03` | 5 | `implementation:a9fa36d46c24` |
| 19 | `ar1-4d27d472de4e` | `finding_transition` | `recorded` | `finding-C-04` | 3 | `implementation:a9fa36d46c24` |
| 20 | `ar1-1f9426eb01a4` | `finding_transition` | `recorded` | `finding-C-05` | 3 | `implementation:a9fa36d46c24` |
| 21 | `ar1-e2b939faae8a` | `provider_attempt` | `succeeded` | `provider-operation-307f24054cb0-1` | 2 | `implementation:a9fa36d46c24` |
| 22 | `ar1-2f96188b8d89` | `gate` | `decided` | `gate-unexpected_file-c52a7e8c9ab6` | 1 | `implementation:c52a7e8c9ab6` |
| 23 | `ar1-0e9e1ad30e3b` | `validation_request` | `requested` | `validation-request-c52a7e8c9ab6` | 1 | `implementation:c52a7e8c9ab6` |
| 24 | `ar1-cb3ef35479c0` | `validation_attestation` | `attested` | `validation-c52a7e8c9ab6` | 1 | `implementation:c52a7e8c9ab6` |
| 25 | `ar1-ff13ddc0a17b` | `provider_input_measurement` | `measured` | `provider-input-4-claude_slice_review` | 2 | `implementation:c52a7e8c9ab6` |
| 26 | `ar1-f82cd471743e` | `provider_attempt` | `started` | `provider-operation-72d74c1b7970-1` | 1 | `implementation:c52a7e8c9ab6` |
| 27 | `ar1-e9fb04013e4a` | `provider_attempt` | `failed` | `provider-operation-72d74c1b7970-1` | 2 | `implementation:c52a7e8c9ab6` |
| 28 | `ar1-4fa5dc63793f` | `transient_retry` | `waiting` | `transient-retry-4c8f078c352a435cad4046d9ef7868dc` | 1 | `implementation:c52a7e8c9ab6` |
| 29 | `ar1-b69666e6676b` | `provider_input_measurement` | `measured` | `provider-input-4-claude_slice_review` | 3 | `implementation:c52a7e8c9ab6` |
| 30 | `ar1-a9b2ffe60eb4` | `provider_attempt` | `started` | `provider-operation-72d74c1b7970-2` | 1 | `implementation:c52a7e8c9ab6` |
| 31 | `ar1-70605a1d06a2` | `review` | `decided` | `review-claude-4-2` | 1 | `implementation:c52a7e8c9ab6` |
| 32 | `ar1-a93ae3255c70` | `provider_attempt` | `succeeded` | `provider-operation-72d74c1b7970-2` | 2 | `implementation:c52a7e8c9ab6` |

### Nachweis vollständiger Bindungswerte

| Kurzreferenz | Vollwert | Feldarten |
|---|---|---|
| `a9fa36d46c24` | `a9fa36d46c2413b0eaed49041e347aac99263b4c761ffd7c7b338bfb806c3bf5` | Technischer Wert, Attestierungsreferenz, Fingerprint, Record-ID, Request-ID |
| `c52a7e8c9ab6` | `c52a7e8c9ab68f05513cca12deafac6366b23be9a2e6d3f13d957678f7f9b17f` | Technischer Wert, Request-ID, Fingerprint, Attestierungsreferenz |
| `3253cc7779e8` | `3253cc7779e8f637aa12e37a6403cecbc73ff20958c15c35cbce3e0a503ae919` | Record-ID |
| `f82d0c97029f` | `f82d0c97029fd032f42d26e4ebc927060b16132345c7ad7efdeac5c0c1c3b370` | Request-ID, Technischer Wert |
| `604a19b1fe0d` | `604a19b1fe0d34cd9084589970f5036b082202936ef83ecc290ed9cab380af71` | Request-ID |
| `4bd7846bbd67` | `4bd7846bbd672cd952e05248a7a611d373c41b3966010e0b83f565bcadc50d60` | Request-ID |
| `70605a1d06a2` | `70605a1d06a29847d44b483b0cff9831dd0e0e0e42e090fc90b7622710de2378` | Request-ID, Technischer Wert |
| `6bd8f24607ac` | `6bd8f24607ac3dcdba55d17db7c7efe96d36351a4eb8f661f27dff14efda9258` | Request-ID |
| `8cf72880f495` | `8cf72880f4957c1e6b0f4ef9da39f45479f153f10ff8428c410a4f1aeed6c24d` | Request-ID |
| `bcb051572d83` | `bcb051572d83770a2de69b5e8ba6c7fc90b007381c651daf0f39b8868531d3b3` | Technischer Wert |
| `593466bcccef` | `593466bcccef186c8a933cb26a0ce90aa6a60dbb456a90342d3239c7196c58ef` | Technischer Wert |
| `f151bffe46f2` | `f151bffe46f22d783469cb17ec83d64668931160ef4d9c62af33e383e77f2033` | Technischer Wert |
| `152be52b64f6` | `152be52b64f6a8336e4bd1e68065112211231f7f2007a14c4e3762ba22f9f331` | Technischer Wert |
| `835718911103` | `8357189111039ae8d8ef9e76e418329df9b70be3b3ac3d12b9b8894df41a27a4` | Output-Digest |
| `7cca8a99c212` | `7cca8a99c212597b44aa807e39815767c7ed992314ee87cc5b4798241eb6355b` | Output-Digest |
| `1d7c11cbca41` | `1d7c11cbca418ba3ad44a3f6af0bcb7ffc20e0222a2662ec7310860a035cb0df` | Technischer Wert, Messungsreferenz |
| `620a27ee39a4` | `620a27ee39a45b71dac5023bea8a72d30cc2912d79a3da0faf0d741c97148599` | Digest |
| `9edf600f09ac` | `9edf600f09ac1b36a30675cc91b1f6769c4545f8f200a4fba413b4b9a4c9bc1e` | Policy-Digest |
| `f19d72414655` | `f19d7241465575e96e7ed44d6d8fcb63e5890141e6b28f3d04d60ba1da55a39a` | Übergangsfingerprint |
| `967ca103017c` | `967ca103017c387781944925065d755bb0014207990f1b7f9ae195bf08c5c400` | Record-ID, Technischer Wert |
| `9fe4ce7e0f23` | `9fe4ce7e0f23b0a10076e9f2b2355727777f75533db6d39d9c9ee43f0ede1ba9` | Record-ID, Technischer Wert |
| `125cd498c228` | `125cd498c228c24fea3831418d6cdef92808f7a57992e778dafd8151c265a645` | Output-Digest |
| `91bfd339f979` | `91bfd339f979872f2d773e83e2c1ea478b917bb1212c896ae13bf3f73372f476` | Technischer Wert |
| `342dc97ce18d` | `342dc97ce18ddb5ccf2c98a2079e4d57c6c4af1f2f14fb0dc7ee8d773a2f1185` | Technischer Wert |
| `c80aeb5c9f2e` | `c80aeb5c9f2ee55fd5a073c2f2bb0b07beca03fcb91ace3f58443eba665cda9f` | Technischer Wert, Messungsreferenz |
| `09d816e5ba06` | `09d816e5ba0606bfb65c532adfca3762709f04e2230a3ef187cc7abbc22bd2a0` | Digest |
| `3aaff746c5b2` | `3aaff746c5b223c5806a2214ae0c792421439801799378c54f387c485bd7fb06` | Übergangsfingerprint |
| `0e9e1ad30e3b` | `0e9e1ad30e3b56ab2c03a70c992fad3938ada669901252d19bdac9b653ae49a0` | Record-ID, Technischer Wert |
| `cb3ef35479c0` | `cb3ef35479c0aaf00db5e8ad0b6a5fdd8d87361e483eaabc75477c44de3f0ab4` | Record-ID, Technischer Wert |
| `83a1acbe8a23` | `83a1acbe8a23d40eba7b596919a2e2f050162c4af07d4babf15a2661a8215e64` | Output-Digest |
| `ff13ddc0a17b` | `ff13ddc0a17bd4072638195299fc33d97171eb4b799032ca0894b6f937adb32e` | Technischer Wert, Messungsreferenz |
| `c3cbdc3d4656` | `c3cbdc3d4656caac56bc88e432474a469e62feab42f141a49557ba99398122a2` | Digest |
| `22ef47e1b114` | `22ef47e1b114c220e468ab8bc6acb22c0d30640bc6869c38ce829dcaf09e55f5` | Übergangsfingerprint |
| `b69666e6676b` | `b69666e6676b2b9be604e88a382c121fe14423be146e205a0534880bcaf421cd` | Response-Digest, Messungsreferenz, Technischer Wert |
| `5ce5839c724e` | `5ce5839c724e6e7aa7c99dfbaf6e654822d65a13c24f3ed1cfb7c52ccd63d0b0` | Übergangsfingerprint |
| `28186d1d802f` | `28186d1d802ffb7200496944769cf08a6bd87d2cd83a4c9176ea6de0758e42f0` | Technischer Wert |
| `79b89d4ee362` | `79b89d4ee3620e98daa77d6e454ea26415a149e837f445b6f7ba9a8c71bfa684` | Technischer Wert |
| `307f24054cb0` | `307f24054cb05e31c41794f27e3a891c3feb8bef8d7cc445caefb26927c2c12c` | Technischer Wert |
| `e2b939faae8a` | `e2b939faae8a89784ba8872b2ab88694d4531f41e48a22b29a5b385aca24c5bf` | Technischer Wert |
| `72d74c1b7970` | `72d74c1b79707f958693b88051ab2b0af73e5bf6ad20c974f5a1ee828ef5467d` | Technischer Wert |
| `e9fb04013e4a` | `e9fb04013e4aa839df311571dab49bc819ab9fa1571eb94da2c2cabc41933710` | Technischer Wert |
| `a93ae3255c70` | `a93ae3255c70915b46554b1235778d5e3cb82bec58ece0393c47d6fd96215f10` | Technischer Wert |
| `2f96188b8d89` | `2f96188b8d89b20ccd0e369f94d63893e57d42adec08ca36a077e839c04d9af9` | Fingerprint, Technischer Wert |
| `7a34b914b9ae` | `7a34b914b9ae12d275fc92ab7a705a2a930b125d1fbb37c1e508704369d2ec6c` | Technischer Wert |
| `b160dd911c7d` | `b160dd911c7d11ddb65cf10133bb7d4aa4a5c4dd84388c3f2c939356bc7b1079` | Output-Digest, Technischer Wert |
| `4d27d472de4e` | `4d27d472de4ed116c95d9d17dee416a894edff78944c8a550112336c29dc86bc` | Output-Digest, Technischer Wert |
| `1f9426eb01a4` | `1f9426eb01a4ef46a0613f57b5734e65515f0133e793251a8636eedc3c30f0de` | Technischer Wert |
| `0a52825429b1` | `0a52825429b1dfccb252df1df53ec126adf22dd628c637afa5eb86f6d711afb0` | Fingerprint |
| `a9c0a53867bf` | `a9c0a53867bf2185243badd6ab2f920e368c27ba6ac4966718ed3a9eb029eb6f` | Technischer Wert |
| `0026180e240d` | `0026180e240d778970d41600856c63da9e935b604509ec8c0c3049cff09a068c` | Technischer Wert |
| `72836ddc4aa9` | `72836ddc4aa949cd03310d3260eeb4e236ca4bec154a3c9edecabcacfd30c20b` | Technischer Wert |
| `4ac654a4545b` | `4ac654a4545b7d4a6ddf9b598ac3948771307665c2b7214c8a045d7eb91cfbaf` | Technischer Wert |
| `d7459d09a2de` | `d7459d09a2de67ad8f91fd6298988805481628bc27ad174611388923d78fbe25` | Technischer Wert |
| `460ead36f754` | `460ead36f754934aabb5451d999355e33f3a72428da3cfbd56f0067fa136e3e4` | Technischer Wert, Response-Digest |
| `0d8f9307add0` | `0d8f9307add066ccf13fa0bd66818f2180ced47d4d04b74f6efaf8f24ed64778` | Technischer Wert |
| `f82cd471743e` | `f82cd471743e3aabd423e64161affc7fd241fb590aa430ff79f0ea7f9f5bfe2a` | Technischer Wert |
| `4fa5dc63793f` | `4fa5dc63793f7433aa8028aec4a61f93d652c1310c7998dcf432955ca11934dd` | Technischer Wert |
| `a9b2ffe60eb4` | `a9b2ffe60eb4c20ee73dc20ef4dfce2bebf9442e22f2c66f5dc947ac23eb8a9f` | Technischer Wert |
| `4d75c85d4036` | `4d75c85d4036cca7ab1059cc1a3a404256edfb3a871066732640db7aa755a8ec` | Request-ID |
| `631553b89ccd` | `631553b89ccddc6e837ac068a6215380c9251f28cbcda818be107690d224e673` | Request-ID |
<!-- artifact-records:decision-table:end -->
<!-- audit:decision-table:end -->

## Rückdokumentation in die Arbeitsplan-MD

Arbeitsplan: `docs/internal/resume-abschluss-verschiebt-task-in-outbox-implement-review-a9c0a538.md`

## Freigabestatus

<!-- audit:approval-status:begin -->
- Implementierung bereit: `YES`
- Validierung: `PASS`
- Claude-Freigabe: `YES`
- Red-State-Folgeslice: `NONE`
- Commit autorisiert: `YES`

<!-- artifact-records:approval-status:begin -->
Semantischer Record-Digest: `3253cc7779e8`

### Korrektur-Work-Unit · Slice 2 · Runde 1

| Seq/Record | Typ | Slice | Runde | Pfade | Findings |
|---|---|---|---:|---|---|
| 3. `ar1-72836ddc4aa9` | Korrektur-Work-Unit | `2` | `1` | `docs/internal/resume-abschluss-verschiebt-task-in-outbox-implement-review-a9c0a538.md`, `docs/internal/slice-resume-abschluss-verschiebt-task-in-outbox-implement-02-abschlusskorrektur.md`, `src/cli.py`, `src/inbox_watcher.py`, `src/orchestrator.py`, `tests/test_cli.py`, `tests/test_inbox_watcher.py`, `tests/test_orchestrator_runtime.py`, `tests/test_orchestrator_watch_cli.py` | `C-01`, `C-03`, `C-04`, `C-05` |

### Codex · Runde 1 · ready

| Seq/Record | Rolle | Runde | Status | Work-Unit | Tests | Transport | Request | Response | Fingerprint |
|---|---|---:|---|---|---|---|---|---|---|
| 6. `ar1-460ead36f754` | `codex` | `1` | `ready` | `4` | `tests/test_inbox_watcher.py`, `tests/test_orchestrator_runtime.py` | `native-codex-v2` | `native-codex-request-4d75c85d4036` | `631553b89ccd` | `a9fa36d46c24` |
<!-- artifact-records:approval-status:end -->
<!-- audit:approval-status:end -->
