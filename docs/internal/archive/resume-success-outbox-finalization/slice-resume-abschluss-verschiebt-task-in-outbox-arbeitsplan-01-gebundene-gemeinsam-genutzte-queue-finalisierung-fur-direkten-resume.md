# Slice 01 – Gebundene, gemeinsam genutzte Queue-Finalisierung für direkten Resume

**Feature-Branch:** `feature/resume-success-outbox-finalization`
**GitHub-Status:** nur lokal

## Ziel des Slice

Gebundene, gemeinsam genutzte Queue-Finalisierung für direkten Resume

## Akzeptanzkriterien

Siehe freigegebenen Arbeitsplan.

## Scope und Nicht-Scope

Erlaubter Scope: `docs/internal/resume-abschluss-verschiebt-task-in-outbox-implement-review-a9c0a538.md`, `docs/internal/slice-resume-abschluss-verschiebt-task-in-outbox-arbeitsplan-01-gebundene-gemeinsam-genutzte-queue-finalisierung-fur-direkten-resume.md`, `src/cli.py`, `src/inbox_watcher.py`, `src/orchestrator.py`, `tests/test_cli.py`, `tests/test_inbox_watcher.py`, `tests/test_orchestrator_runtime.py`, `tests/test_orchestrator_watch_cli.py`

## Diff-Risiko inklusive Branch- und Statuscheck

Vor Umsetzung zu prüfen.

## Geplante Tests

Gemäß Arbeitsplan und Orchestrator-Validierungsmatrix.

## Durchgeführte Änderungen

- Der Queue-Erfolgsabschluss verwendet nun eine atomare, versionierte und über
  Run, Taskdigest, Protokoll, Inbox-Quelle und reserviertes Done-Ziel gebundene
  Erfolgsevidenz. Move und Sidecar-Bereinigung sind gemeinsam, idempotent und
  nach Teilabbrüchen wiederaufnehmbar.
- Die CLI hält die ausdrückliche Herkunft von `--resume` und `--task-file`
  getrennt von Auto-Resume, Default- und positionalen Pfaden fest. Nur diese
  Kombination aktiviert die Watch-originäre direkte Finalisierung.
- Der direkte Pipelinepfad lädt eine vorhandene Watch-Identität strikt,
  vergleicht terminales Workflowergebnis und State-v3-Bindungen und führt bei
  bereits erfolgtem Move ausschließlich verifiziertes Queue-Bookkeeping aus.
- Providerfreie Regressionstests decken Herkunftsflags, terminale und
  nichtterminale direkte Resumes, gebundene Evidenz, Manipulation und die
  bestehende Watch-Kompatibilität ab.

## Ausgeführte Validierung mit Ergebnis

Wird durch den Orchestrator projiziert.

## Abweichungen vom Plan

Keine erfasst.

## Offene Risiken

Siehe Findings-Lebenszyklus.

## Review-Feedback von Claude

<!-- audit:claude-review:begin -->
### Claude · Runde 1 · approved (Ereignis 3)

- Reviewer: `claude`
- Freigabe: `YES`
- Validierungsbindung: `validation-521f40758812`
- Testdateien: `tests/test_agent_runtime.py`, `tests/test_cli.py`, `tests/test_inbox_watcher.py`, `tests/test_orchestrator_runtime.py`, `tests/test_orchestrator_watch_cli.py`
- Prüfdimensionen: Checked: (1) correctness of finalize_queue_success/load_queue_success_evidence/load_watch_identity state machine and its interruption-boundary recovery (before move, after move, and each of the three sidecar deletions) against the parametrized tests; (2) contract adherence to the slice acceptance criteria (literal --resume --task-file gating via resume_explicit/task_file_explicit, watch vs.<br>direct-resume disposition typing via watch_invocation, exactly-once move to outbox/done, sidecar cleanup ordering); (3) failure paths for tampered/forged success evidence (per-field tamper test relies on the self-consistent evidence_digest plus the explicit inbox/outbox boundary checks); (4) security boundaries for path/symlink handling in _validate_queue_paths and destination containment under outbox/done; (5) resume/idempotency across repeated direct-resume invocations and a subsequent watch pass, including the CLI-level bypass of find_task_file_fn for missing-but-bound sources and its later re-validation via _validate_queue_paths.<br>All 1039 tests pass per the bound validation attestation for this fingerprint, and no path outside the authorized Slice scope is touched.
- Größtes Restrisiko: The narrow &#96;except ValueError&#96; around load_resumable_workflow_state in the new marker-recovery branch of run_pipeline may not cover ArtifactResumeError/StateSchemaError the way sibling code in the same function does, so a corrupt or unsupported-protocol state.json encountered mid bound-recovery could surface as an unhandled exception instead of the intended fail-closed logger.error<br>+ return 1 diagnostic.
- Realistische Bruchbedingung: This approval should be revisited if a follow-up run demonstrates run_pipeline raising an uncaught ArtifactResumeError/StateSchemaError (rather than returning exit code<br>1) during the bound-marker direct-resume recovery path, or if any test shows the queue marker/move/sidecar sequence producing more than one outbox/done copy, a lost source, or a finalize_queue_success success despite tampered/mismatched evidence.
- Eigene Findings: `C-01`, `C-02`, `C-03`

<!-- artifact-records:claude-review:begin -->
Semantischer Record-Digest: `4064a7df49d3`

### Claude · Runde 1 · approved

| Seq/Record | Rolle | Runde | Status | Work-Unit | Findings | Fingerprint | Transport | Request | Response |
|---|---|---:|---|---|---|---|---|---|---|
| 18. `ar1-5b4a83b1d138` | `claude` | `1` | `approved` | `2` | `C-01`, `C-02`, `C-03` | `521f40758812` | `native-claude-review-v2` | `native-review-request-a5fdc87f56ae` | `d5658e88b814` |
<!-- artifact-records:claude-review:end -->
<!-- audit:claude-review:end -->

## Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
Noch keine strukturierten Codex-Antworten.

<!-- artifact-records:codex-responses:begin -->
Semantischer Record-Digest: `4064a7df49d3`

Keine Codex-Findingantworten.
<!-- artifact-records:codex-responses:end -->
<!-- audit:codex-responses:end -->

## Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### Ereignis 1: `validation-d4c99c7095db`

- Diff-Fingerprint: `d4c99c7095db`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: 1 passed; 0 failed; 0 unavailable; 1 required
- Ausgabedigest: `c6bab1ac0e95`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| python3 -m pytest tests/ -v | PASS | 0 | ============================= test session starts ==============================<br>platform linux -- Python 3.12.3, pytest-7.4.4, pluggy-1.4.0 -- /usr/bin/python3<br>cachedir: .pytest_cache<br>rootdir: /mnt/c/Users/Diete/Sync/DE_Privat/Rente/ChatGPT CLI/Dual-Agent-Orchestrator<br>configfile: pyproject.toml<br>collecting ... collected 1038 items<br><br>tests/test_agent_adapters.py::test_registry_constructs_only_native_adapters PASSED [  0%]<br>tests/test_agent_adapters.py::test_native_adapter_api_and_mro_are_closed PASSED [  0%]<br>tests/test_agent_adapters.py::test_native_codex_prepares_schema_request_and_assets PASSED [  0%]<br>tests/test_agent_adapters.py::test_native_codex_extracts_only_bound_result PASSED [  0%]<br>tests/test_agent_adapters.py::test_native_claude_workspace_binding_and_capability_smoke PASSED [  0%]<br>tests/test_agent_adapters.py::test_native_claude_prepares_request_components_and_bound_output PASSED [  0%]<br>tests/test_agent_runtime.py::test_compute_retry_backoff_seconds_exponential PASSED [  0%]<br>tes<br>...[119819 characters omitted]...<br>_pre_slice11_v3_gate_and_work_unit_shapes_load_with_empty_new_fields PASSED [ 99%]<br>tests/test_workflow_state.py::test_early_slice11_work_unit_shape_loads_without_active_test_evidence PASSED [ 99%]<br>tests/test_workflow_state.py::test_policy_gate_roundtrips_and_resumes_at_same_step PASSED [ 99%]<br>tests/test_workflow_state.py::test_managed_audit_path_roundtrips_and_rejects_unsafe_locations PASSED [ 99%]<br>tests/test_workflow_state.py::test_policy_gate_rejects_fingerprint_bound_reason_and_unsafe_path PASSED [ 99%]<br>tests/test_workflow_state.py::test_unexpected_file_user_gate_decision_roundtrips PASSED [ 99%]<br>tests/test_workflow_state.py::test_plan_time_slice_one_start_commit_cannot_be_rebound_after_resume PASSED [ 99%]<br>tests/test_workflow_state.py::test_in_progress_slice_can_extend_exact_remediation_scope PASSED [ 99%]<br>tests/test_workflow_state.py::test_state_scope_rejects_orchestrator_internal_paths PASSED [100%]<br><br>======================= 1038 passed in 64.21s (0:01:04) ======================== |

### Ereignis 2: `validation-521f40758812`

- Diff-Fingerprint: `521f40758812`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: 1 passed; 0 failed; 0 unavailable; 1 required
- Ausgabedigest: `e64a51dc7d24`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| python3 -m pytest tests/ -v | PASS | 0 | ============================= test session starts ==============================<br>platform linux -- Python 3.12.3, pytest-7.4.4, pluggy-1.4.0 -- /usr/bin/python3<br>cachedir: .pytest_cache<br>rootdir: /mnt/c/Users/Diete/Sync/DE_Privat/Rente/ChatGPT CLI/Dual-Agent-Orchestrator<br>configfile: pyproject.toml<br>collecting ... collected 1039 items<br><br>tests/test_agent_adapters.py::test_registry_constructs_only_native_adapters PASSED [  0%]<br>tests/test_agent_adapters.py::test_native_adapter_api_and_mro_are_closed PASSED [  0%]<br>tests/test_agent_adapters.py::test_native_codex_prepares_schema_request_and_assets PASSED [  0%]<br>tests/test_agent_adapters.py::test_native_codex_extracts_only_bound_result PASSED [  0%]<br>tests/test_agent_adapters.py::test_native_claude_workspace_binding_and_capability_smoke PASSED [  0%]<br>tests/test_agent_adapters.py::test_native_claude_prepares_request_components_and_bound_output PASSED [  0%]<br>tests/test_agent_runtime.py::test_compute_retry_backoff_seconds_exponential PASSED [  0%]<br>tes<br>...[119938 characters omitted]...<br>_pre_slice11_v3_gate_and_work_unit_shapes_load_with_empty_new_fields PASSED [ 99%]<br>tests/test_workflow_state.py::test_early_slice11_work_unit_shape_loads_without_active_test_evidence PASSED [ 99%]<br>tests/test_workflow_state.py::test_policy_gate_roundtrips_and_resumes_at_same_step PASSED [ 99%]<br>tests/test_workflow_state.py::test_managed_audit_path_roundtrips_and_rejects_unsafe_locations PASSED [ 99%]<br>tests/test_workflow_state.py::test_policy_gate_rejects_fingerprint_bound_reason_and_unsafe_path PASSED [ 99%]<br>tests/test_workflow_state.py::test_unexpected_file_user_gate_decision_roundtrips PASSED [ 99%]<br>tests/test_workflow_state.py::test_plan_time_slice_one_start_commit_cannot_be_rebound_after_resume PASSED [ 99%]<br>tests/test_workflow_state.py::test_in_progress_slice_can_extend_exact_remediation_scope PASSED [ 99%]<br>tests/test_workflow_state.py::test_state_scope_rejects_orchestrator_internal_paths PASSED [100%]<br><br>======================= 1039 passed in 68.70s (0:01:08) ======================== |

<!-- artifact-records:validation-attestation:begin -->
Semantischer Record-Digest: `4064a7df49d3`

- 4. `ar1-aed901106565`: Providerinput `codex/codex_implementation` = `allowed`; local_input_chars `10686/4000000`, local_input_bytes `10726/16000000`; local_input_digest `7ecbbdb37604`, Policy `9edf600f09ac`, Übergang `d36379b52918`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, local_input_largest_component `stdin_prompt`; local_input_component_count `2`; Komponenten `stdin_prompt=6877/6917, response_schema=3809/3809`
### Validierungsanforderung

| Seq/Record | Rolle | Befehle mit argv-Grenzen |
|---|---|---|
| 8. `ar1-2c48f10d4c49` | `orchestrator` | `argv` [`python3`, `-m`, `pytest`, `tests/`, `-v`] |

### Validierungsattestierung

| Seq/Record | Rolle | Fingerprint |
|---|---|---|
| 9. `ar1-122e407836de` | `orchestrator` | `d4c99c7095db` |

| Status | Exit | Output-Digest | Befehl mit argv-Grenzen |
|---|---:|---|---|
| `pass` | `0` | `f50f911baa9a` | `argv` [`python3`, `-m`, `pytest`, `tests/`, `-v`] |
- 10. `ar1-361179babdbb`: Providerinput `claude/claude_slice_review` = `allowed`; local_input_chars `74030/4000000`, local_input_bytes `74080/16000000`; local_input_digest `7d29507476fd`, Policy `9edf600f09ac`, Übergang `a8ccb0d35e82`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, local_input_largest_component `evidence_asset_001`; local_input_component_count `6`; Komponenten `request_chunk_001=16009/16020, evidence_asset_001=46616/46655, packet_manifest=612/612, system_policy=217/217, response_schema=10346/10346, start_directive=230/230`
### Validierungsanforderung

| Seq/Record | Rolle | Befehle mit argv-Grenzen |
|---|---|---|
| 14. `ar1-461f39ad0a7b` | `orchestrator` | `argv` [`python3`, `-m`, `pytest`, `tests/`, `-v`] |

### Validierungsattestierung

| Seq/Record | Rolle | Fingerprint |
|---|---|---|
| 15. `ar1-48ab7a405df7` | `orchestrator` | `521f40758812` |

| Status | Exit | Output-Digest | Befehl mit argv-Grenzen |
|---|---:|---|---|
| `pass` | `0` | `cc63f12994f4` | `argv` [`python3`, `-m`, `pytest`, `tests/`, `-v`] |
- 16. `ar1-f1549b1eec82`: Providerinput `claude/claude_slice_review` = `allowed`; local_input_chars `76646/4000000`, local_input_bytes `76696/16000000`; local_input_digest `4871e6ea522f`, Policy `9edf600f09ac`, Übergang `896eb35e270a`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, local_input_largest_component `evidence_asset_001`; local_input_component_count `6`; Komponenten `request_chunk_001=16092/16103, evidence_asset_001=49149/49188, packet_manifest=612/612, system_policy=217/217, response_schema=10346/10346, start_directive=230/230`
- Providerattempt-Summe Run `watch-20260826-202634.812746Z-f4353362bc49` / Operation `provider-operation-2916abc6b521` (`claude/claude_slice_review`; Modell `sonnet`; Effort `high`): Attempts `1`, offen `0`, Duration `270.879164` (bekannt `1`, unbekannt `0`); Inputzeichen `76646`, Inputbytes `76696`; Retrystatus `single-attempt`; input_tokens=sum:8,known:1,unknown:0; tool_input_tokens=sum:unknown,known:0,unknown:1; cache_read_input_tokens=sum:36139,known:1,unknown:0; cache_creation_input_tokens=sum:29246,known:1,unknown:0; thinking_tokens=sum:unknown,known:0,unknown:1; output_tokens=sum:25260,known:1,unknown:0; total_tokens=sum:unknown,known:0,unknown:1; turns=sum:5,known:1,unknown:0; cost_usd=sum:0.37785179999999996,known:1,unknown:0
  - 22. `ar1-11b9a2fee4b7`: Attempt `1` = `succeeded`; Messung `ar1-f1549b1eec82`; Modell `sonnet`; Effort `high`; Inputzeichen `76646`; Inputbytes `76696`; Duration `270.87916397105437`; Fehler `none`; Usage `input_tokens=8, tool_input_tokens=unknown, cache_read_input_tokens=36139, cache_creation_input_tokens=29246, thinking_tokens=unknown, output_tokens=25260, total_tokens=unknown, turns=5, cost_usd=0.37785179999999996`
- Providerattempt-Summe Run `watch-20260826-202634.812746Z-f4353362bc49` / Operation `provider-operation-bb529e8e6121` (`codex/codex_implementation`; Modell `gpt-5.6-sol`; Effort `medium`): Attempts `1`, offen `0`, Duration `810.851409` (bekannt `1`, unbekannt `0`); Inputzeichen `10686`, Inputbytes `10726`; Retrystatus `single-attempt`; input_tokens=sum:unknown,known:0,unknown:1; tool_input_tokens=sum:unknown,known:0,unknown:1; cache_read_input_tokens=sum:unknown,known:0,unknown:1; cache_creation_input_tokens=sum:unknown,known:0,unknown:1; thinking_tokens=sum:unknown,known:0,unknown:1; output_tokens=sum:unknown,known:0,unknown:1; total_tokens=sum:unknown,known:0,unknown:1; turns=sum:unknown,known:0,unknown:1; cost_usd=sum:unknown,known:0,unknown:1
  - 7. `ar1-ac36e4f6ddbd`: Attempt `1` = `succeeded`; Messung `ar1-aed901106565`; Modell `gpt-5.6-sol`; Effort `medium`; Inputzeichen `10686`; Inputbytes `10726`; Duration `810.8514091731049`; Fehler `none`; Usage `unknown`
- Providerattempt-Summe Run `watch-20260826-202634.812746Z-f4353362bc49` / Operation `provider-operation-cb5f636a87a8` (`claude/claude_slice_review`; Modell `sonnet`; Effort `high`): Attempts `1`, offen `0`, Duration `415.960792` (bekannt `1`, unbekannt `0`); Inputzeichen `74030`, Inputbytes `74080`; Retrystatus `single-attempt`; input_tokens=sum:12,known:1,unknown:0; tool_input_tokens=sum:unknown,known:0,unknown:1; cache_read_input_tokens=sum:178615,known:1,unknown:0; cache_creation_input_tokens=sum:66829,known:1,unknown:0; thinking_tokens=sum:unknown,known:0,unknown:1; output_tokens=sum:38882,known:1,unknown:0; total_tokens=sum:unknown,known:0,unknown:1; turns=sum:9,known:1,unknown:0; cost_usd=sum:0.6929029999999999,known:1,unknown:0
  - 12. `ar1-a48dfe144fc9`: Attempt `1` = `failed`; Messung `ar1-361179babdbb`; Modell `sonnet`; Effort `high`; Inputzeichen `74030`; Inputbytes `74080`; Duration `415.9607923340518`; Fehler `process`; Usage `input_tokens=12, tool_input_tokens=unknown, cache_read_input_tokens=178615, cache_creation_input_tokens=66829, thinking_tokens=unknown, output_tokens=38882, total_tokens=unknown, turns=9, cost_usd=0.6929029999999999`
<!-- artifact-records:validation-attestation:end -->
<!-- audit:validation-attestation:end -->

## Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
- Teständerungsfreigabe: nicht erfasst.
- Pre-Mortems:
  - Ereignis 3: If this approval turns out wrong, the most likely cause is the direct-resume marker-recovery branch in run_pipeline letting a corrupt or unsupported-protocol .orchestrator/state.json escape as an uncaught ArtifactResumeError/StateSchemaError instead of the controlled return-1 diagnostic used elsewhere in the same function, turning a resumable fail-closed condition into an unhandled crash during exactly the recovery flow this slice exists to make reliable.<br>A secondary risk is the cli.py sidecar-name duplication silently drifting from inbox_watcher.success_marker_path in a future change, causing missed or false-positive bound-recovery detection at the CLI boundary before the stricter orchestrator-side validation even runs.

<!-- artifact-records:test-approval-premortem:begin -->
Semantischer Record-Digest: `4064a7df49d3`

### Gate-Ereignisse

| Seq/Record | Gate | Status | Autorität | Fingerprint | Begründung |
|---|---|---|---|---|---|
| 13. `ar1-1740032da33d` | `quota-resume-diff` | `approved` | `user` | `521f40758812` | Geprüfter Claude-Retry-Hotfix b2d55a3 für Fingerprint<br>      521f40758812: Der exakte Provider-Envelope<br>      error_max_structured_output_retries wird auch beim vom aktuellen Claude-Adapter erzeugten AgentOutputError als<br>      begrenzter transienter Netzwerkfehler klassifiziert.<br>Nahe Diagnosevarianten bleiben fail-closed.<br>Exakte<br>      Zusatzpfade src/agent_runtime.py und tests/test_agent_runtime.py; 44 fokussierte Runtime-Tests und die<br>      vollständige Suite mit 1039 Tests bestanden; git diff --check sauber. |
<!-- artifact-records:test-approval-premortem:end -->
<!-- audit:test-approval-premortem:end -->

## Findings-Lebenszyklus

<!-- audit:findings:begin -->
### `C-01` — `OPEN`

- Quelle: `claude`; Runde 1
- Klasse: `OBSERVATION`
- Finding: src/orchestrator.py run_pipeline: in the new direct-queue-resume branch that recovers a pending bound success marker, the call to load_resumable_workflow_state(...) is guarded only by &#96;except ValueError as exc:&#96;.<br>Every other call site in the same function that can fail while loading/resuming Structured-v2 state (the try block around run_default_dry_run_fn/run_production_workflow_fn a few lines below) explicitly treats ArtifactResumeError and StateSchemaError as the fail-closed signal and converts them into a logged diagnostic plus &#96;return 1&#96;.<br>If state.json is missing, corrupted, or bound to an unsupported protocol while a bound &#96;.success&#96; marker exists (a plausible resume scenario per the repo's fail-closed resume contract), this narrower except clause may let ArtifactResumeError/StateSchemaError propagate uncaught out of the bound-recovery path instead of producing the same controlled diagnostic, weakening the resume/idempotency and failure-path guarantees this slice is meant to add.
- Akzeptanztest: Add an orchestrator test that leaves a bound (dict) &#96;.success&#96; marker in place while making &#96;.orchestrator/state.json&#96; unreadable, corrupt, or bound to an unsupported/legacy protocol, then invokes run_pipeline through the literal &#96;--resume --task-file&#96; direct path; assert it returns exit code 1 with a logged error rather than raising ArtifactResumeError/StateSchemaError uncaught, matching the handling used for the same exception classes elsewhere in run_pipeline.
- Statusbegründung: –

### `C-02` — `OPEN`

- Quelle: `claude`; Runde 1
- Klasse: `OBSERVATION`
- Finding: src/cli.py run_cli computes the bound-recovery sidecar path manually as &#96;task_path.with_name(f"{task_path.name}.success")&#96; instead of importing and reusing inbox_watcher.success_marker_path()/has_success_marker().<br>This duplicates the marker-naming convention across two modules; if the convention in inbox_watcher.py ever changes, cli.py's &#96;missing_bound_recovery&#96; detection will silently diverge from the authoritative helper, either bypassing find_task_file_fn incorrectly or failing to recognize a legitimate post-move recovery case.
- Akzeptanztest: Add a regression test (or refactor) asserting cli.py's missing_bound_recovery marker-path computation stays identical to inbox_watcher.success_marker_path()/has_success_marker() for the same task file, so any future change to the marker naming convention in inbox_watcher.py is caught by a failing test rather than silently drifting.
- Statusbegründung: –

### `C-03` — `OPEN`

- Quelle: `claude`; Runde 1
- Klasse: `OBSERVATION`
- Finding: In inbox_watcher.py, _validate_queue_paths() checks &#96;task_file.is_symlink()&#96; exactly once before finalize_queue_success() later performs _task_digest(task_file) and move_to_reserved_outbox(task_file, destination).<br>Between that single check and the later filesystem operations there is a TOCTOU window in which the queue source path could be swapped for a symlink (e.g., by a concurrent process with inbox write access), causing the digest read and/or move to follow attacker- or bug-controlled content instead of the validated regular file.
- Akzeptanztest: Add a regression test that replaces the queue task source with a symlink between the initial _validate_queue_paths() symlink check and the subsequent digest/move step inside finalize_queue_success() (e.g., via a monkeypatched hook invoked mid-function), and assert finalize_queue_success() still returns a FAILED disposition instead of following the symlink.
- Statusbegründung: –

<!-- artifact-records:findings:begin -->
Semantischer Record-Digest: `4064a7df49d3`

### Finding-Ereignisse

| Seq/Record | Finding | Rolle | Runde | Aktion | Klasse | Status | Begründung |
|---|---|---|---:|---|---|---|---|
| 19. `ar1-b7a023b35309` | `C-01` | `claude` | `1` | `opened` | `OBSERVATION` | `open` | src/orchestrator.py run_pipeline: in the new direct-queue-resume branch that recovers a pending bound success marker, the call to load_resumable_workflow_state(...) is guarded only by &#96;except ValueError as exc:&#96;.<br>Every other call site in the same function that can fail while loading/resuming Structured-v2 state (the try block around run_default_dry_run_fn/run_production_workflow_fn a few lines below) explicitly treats ArtifactResumeError and StateSchemaError as the fail-closed signal and converts them into a logged diagnostic plus &#96;return 1&#96;.<br>If state.json is missing, corrupted, or bound to an unsupported protocol while a bound &#96;.success&#96; marker exists (a plausible resume scenario per the repo's fail-closed resume contract), this narrower except clause may let ArtifactResumeError/StateSchemaError propagate uncaught out of the bound-recovery path instead of producing the same controlled diagnostic, weakening the resume/idempotency and failure-path guarantees this slice is meant to add. |
| 20. `ar1-9e91d30657a8` | `C-02` | `claude` | `1` | `opened` | `OBSERVATION` | `open` | src/cli.py run_cli computes the bound-recovery sidecar path manually as &#96;task_path.with_name(f"{task_path.name}.success")&#96; instead of importing and reusing inbox_watcher.success_marker_path()/has_success_marker().<br>This duplicates the marker-naming convention across two modules; if the convention in inbox_watcher.py ever changes, cli.py's &#96;missing_bound_recovery&#96; detection will silently diverge from the authoritative helper, either bypassing find_task_file_fn incorrectly or failing to recognize a legitimate post-move recovery case. |
| 21. `ar1-d28a56f3e001` | `C-03` | `claude` | `1` | `opened` | `OBSERVATION` | `open` | In inbox_watcher.py, _validate_queue_paths() checks &#96;task_file.is_symlink()&#96; exactly once before finalize_queue_success() later performs _task_digest(task_file) and move_to_reserved_outbox(task_file, destination).<br>Between that single check and the later filesystem operations there is a TOCTOU window in which the queue source path could be swapped for a symlink (e.g., by a concurrent process with inbox write access), causing the digest read and/or move to follow attacker- or bug-controlled content instead of the validated regular file. |

### Native convergence summary

| Finding | Work units | Rounds | Fingerprints | Claude decisions | Codex dispositions | Final status |
|---|---|---|---|---|---|---|
| `C-01` | `2` | `1` | `521f40758812` | `opened:open` | – | `open` |
| `C-02` | `2` | `1` | `521f40758812` | `opened:open` | – | `open` |
| `C-03` | `2` | `1` | `521f40758812` | `opened:open` | – | `open` |
<!-- artifact-records:findings:end -->
<!-- audit:findings:end -->

## Entscheidungstabelle

<!-- audit:decision-table:begin -->
| ID | Quelle | Finding | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|---|
| C-01 | claude | src/orchestrator.py run_pipeline: in the new direct-queue-resume branch that recovers a pending bound success marker, the call to load_resumable_workflow_state(...) is guarded only by &#96;except ValueError as exc:&#96;. Every other call site in the same function that can fail while loading/resuming Structured-v2 state (the try block around run_default_dry_run_fn/run_production_workflow_fn a few lines below) explicitly treats ArtifactResumeError and StateSchemaError as the fail-closed signal and converts them into a logged diagnostic plus &#96;return 1&#96;. If state.json is missing, corrupted, or bound to an unsupported protocol while a bound &#96;.success&#96; marker exists (a plausible resume scenario per the repo's fail-closed resume contract), this narrower except clause may let ArtifactResumeError/StateSchemaError propagate uncaught out of the bound-recovery path instead of producing the same controlled diagnostic, weakening the resume/idempotency and failure-path guarantees this slice is meant to add. | OBSERVATION | offen | offen |
| C-02 | claude | src/cli.py run_cli computes the bound-recovery sidecar path manually as &#96;task_path.with_name(f"{task_path.name}.success")&#96; instead of importing and reusing inbox_watcher.success_marker_path()/has_success_marker(). This duplicates the marker-naming convention across two modules; if the convention in inbox_watcher.py ever changes, cli.py's &#96;missing_bound_recovery&#96; detection will silently diverge from the authoritative helper, either bypassing find_task_file_fn incorrectly or failing to recognize a legitimate post-move recovery case. | OBSERVATION | offen | offen |
| C-03 | claude | In inbox_watcher.py, _validate_queue_paths() checks &#96;task_file.is_symlink()&#96; exactly once before finalize_queue_success() later performs _task_digest(task_file) and move_to_reserved_outbox(task_file, destination). Between that single check and the later filesystem operations there is a TOCTOU window in which the queue source path could be swapped for a symlink (e.g., by a concurrent process with inbox write access), causing the digest read and/or move to follow attacker- or bug-controlled content instead of the validated regular file. | OBSERVATION | offen | offen |

<!-- artifact-records:decision-table:begin -->
Semantischer Record-Digest: `4064a7df49d3`

| Seq | Record | Typ | Status | Logische ID | Revision | Fingerprint |
|---:|---|---|---|---|---:|---|
| 1 | `ar1-0a52825429b1` | `task` | `accepted` | `task-contract` | 1 | `contract:a9c0a53867bf` |
| 2 | `ar1-0026180e240d` | `plan` | `approved` | `approved-plan` | 1 | `contract:a9c0a53867bf` |
| 3 | `ar1-cf5edcbfee6c` | `work_unit` | `active` | `work-unit-2` | 1 | `contract:a9c0a53867bf` |
| 4 | `ar1-aed901106565` | `provider_input_measurement` | `measured` | `provider-input-2-codex_implementation` | 1 | `implementation:39d00eeab1b5` |
| 5 | `ar1-988970376b43` | `provider_attempt` | `started` | `provider-operation-bb529e8e6121-1` | 1 | `implementation:39d00eeab1b5` |
| 6 | `ar1-3ed296f7a81e` | `agent_result` | `ready` | `agent-2-codex_implementation-1` | 1 | `implementation:d4c99c7095db` |
| 7 | `ar1-ac36e4f6ddbd` | `provider_attempt` | `succeeded` | `provider-operation-bb529e8e6121-1` | 2 | `implementation:39d00eeab1b5` |
| 8 | `ar1-2c48f10d4c49` | `validation_request` | `requested` | `validation-request-d4c99c7095db` | 1 | `implementation:d4c99c7095db` |
| 9 | `ar1-122e407836de` | `validation_attestation` | `attested` | `validation-d4c99c7095db` | 1 | `implementation:d4c99c7095db` |
| 10 | `ar1-361179babdbb` | `provider_input_measurement` | `measured` | `provider-input-2-claude_slice_review` | 1 | `implementation:d4c99c7095db` |
| 11 | `ar1-08de7c786b64` | `provider_attempt` | `started` | `provider-operation-cb5f636a87a8-1` | 1 | `implementation:d4c99c7095db` |
| 12 | `ar1-a48dfe144fc9` | `provider_attempt` | `failed` | `provider-operation-cb5f636a87a8-1` | 2 | `implementation:d4c99c7095db` |
| 13 | `ar1-1740032da33d` | `gate` | `decided` | `gate-quota_resume_diff-521f40758812` | 1 | `implementation:521f40758812` |
| 14 | `ar1-461f39ad0a7b` | `validation_request` | `requested` | `validation-request-521f40758812` | 1 | `implementation:521f40758812` |
| 15 | `ar1-48ab7a405df7` | `validation_attestation` | `attested` | `validation-521f40758812` | 1 | `implementation:521f40758812` |
| 16 | `ar1-f1549b1eec82` | `provider_input_measurement` | `measured` | `provider-input-2-claude_slice_review` | 2 | `implementation:521f40758812` |
| 17 | `ar1-0732be200af6` | `provider_attempt` | `started` | `provider-operation-2916abc6b521-1` | 1 | `implementation:521f40758812` |
| 18 | `ar1-5b4a83b1d138` | `review` | `decided` | `review-claude-2-1` | 1 | `implementation:521f40758812` |
| 19 | `ar1-b7a023b35309` | `finding_transition` | `recorded` | `finding-C-01` | 1 | `implementation:521f40758812` |
| 20 | `ar1-9e91d30657a8` | `finding_transition` | `recorded` | `finding-C-02` | 1 | `implementation:521f40758812` |
| 21 | `ar1-d28a56f3e001` | `finding_transition` | `recorded` | `finding-C-03` | 1 | `implementation:521f40758812` |
| 22 | `ar1-11b9a2fee4b7` | `provider_attempt` | `succeeded` | `provider-operation-2916abc6b521-1` | 2 | `implementation:521f40758812` |

### Nachweis vollständiger Bindungswerte

| Kurzreferenz | Vollwert | Feldarten |
|---|---|---|
| `521f40758812` | `521f40758812ecaefe41644d286324f20565e03dd44a1e11af290afbe7dea0e1` | Technischer Wert, Fingerprint, Request-ID, Attestierungsreferenz, Record-ID |
| `4064a7df49d3` | `4064a7df49d3d8775ba7215a036d4af0e9a5952e928b9327bcdffe4c73764cfc` | Record-ID |
| `5b4a83b1d138` | `5b4a83b1d13806730b04ef13b2aa06f69277599a4b98c78a43868761cea793aa` | Request-ID, Technischer Wert |
| `a5fdc87f56ae` | `a5fdc87f56ae686f9a9c107374295b9e7fb72a4fd0474127ff73f271f82d24b3` | Request-ID |
| `d5658e88b814` | `d5658e88b8148e9b29293f156ac4bfb376ae9488e254cb90fec066d01aca30f1` | Request-ID |
| `d4c99c7095db` | `d4c99c7095db1b213f4cc69b6490c140a671162978ed31ffb7cb1818e376b93a` | Attestierungsreferenz, Fingerprint, Technischer Wert, Request-ID |
| `c6bab1ac0e95` | `c6bab1ac0e95947e81c50d45cae677e11e0f739bf03342a9e2ca450d2ed799b1` | Output-Digest |
| `e64a51dc7d24` | `e64a51dc7d24dc9544ddc63420935be6800d290578e1a32ef2d1f5b07583a479` | Output-Digest |
| `aed901106565` | `aed901106565128870b88dbbef27813d4a0f01c13e2b1a8fd5430d7faa1708b0` | Technischer Wert, Messungsreferenz |
| `7ecbbdb37604` | `7ecbbdb376048c4992b41fb3e752a643620f4b539670ce61c017a57bb56cb239` | Digest |
| `9edf600f09ac` | `9edf600f09ac1b36a30675cc91b1f6769c4545f8f200a4fba413b4b9a4c9bc1e` | Policy-Digest |
| `d36379b52918` | `d36379b529185c6ca29133d0a6b2f07d7d94d82eb6c91943fd56979178a734bb` | Übergangsfingerprint |
| `2c48f10d4c49` | `2c48f10d4c49b85151dbd866f62557657d9827ebcba1575835156e5904c28934` | Record-ID, Technischer Wert |
| `122e407836de` | `122e407836de48dc97955fba2de48d701c1e7b91a4ec77c541878980b34055b5` | Record-ID, Technischer Wert |
| `f50f911baa9a` | `f50f911baa9a95900892aebd0547e23191a78a6deebf29906aaa9296a52fa254` | Output-Digest |
| `361179babdbb` | `361179babdbb8e0dfb9e0d240f140073b94199f7b27558f5bae5b9817aee456f` | Technischer Wert, Messungsreferenz |
| `7d29507476fd` | `7d29507476fd2d1f44b26e341f2ebff61a643e79d71fedb17fedd137cb113f1a` | Digest |
| `a8ccb0d35e82` | `a8ccb0d35e8288a69e9b9b50f7e2687f1e49f51cbd9d2c8d2d1f89512db12b89` | Übergangsfingerprint |
| `461f39ad0a7b` | `461f39ad0a7bb635b081af43b3405347fc889a124c12a7633cfbf7ea9398c2e6` | Record-ID, Technischer Wert |
| `48ab7a405df7` | `48ab7a405df7771efa58a0929e9d7a2d4dd83e32e1fa95615d9feb353d4ba583` | Record-ID, Technischer Wert |
| `cc63f12994f4` | `cc63f12994f47c39a15f76502c8753190792178d8eec7b1e3243e04930f25189` | Output-Digest |
| `f1549b1eec82` | `f1549b1eec820ae018aa244c7c60a78fe710b93b3aee1b72245c0560ea9590d8` | Technischer Wert, Messungsreferenz |
| `4871e6ea522f` | `4871e6ea522f1bef2adafdd0771fee7934820d20d609075670173135b16ba036` | Digest |
| `896eb35e270a` | `896eb35e270a90e040ce0b7e83c2b2e8183eba59c37b868db5fe68d1dcb00628` | Übergangsfingerprint |
| `2916abc6b521` | `2916abc6b5212b7a63f0ca8b2c2b51125b2f9e27f0f8a65197ffd3fb54fb9d00` | Technischer Wert |
| `11b9a2fee4b7` | `11b9a2fee4b7512dfdd8f34509be59279c6c4bd7063278b61524206a5693f8d1` | Technischer Wert |
| `bb529e8e6121` | `bb529e8e612153d56d46cb6e8d1634a72aa251a15b9b09353d78df80ae28216b` | Technischer Wert |
| `ac36e4f6ddbd` | `ac36e4f6ddbdc9f26fd152971c23d35cad8f3e828358943a7216b640cf0a3d8b` | Technischer Wert |
| `cb5f636a87a8` | `cb5f636a87a8d60493b6cbc8f012f53fac50a33c83da76140050c371a72fca48` | Technischer Wert |
| `a48dfe144fc9` | `a48dfe144fc9fdbeae6dd40a35025f677417161d4edc5f58e9be71d367d61866` | Technischer Wert |
| `1740032da33d` | `1740032da33dbc9234b592348abbb937f436d8c7c9f1f6a07a60af01d2fcb270` | Fingerprint, Technischer Wert |
| `b7a023b35309` | `b7a023b35309f377aaa1a71542fe752076aa56081da1a16b7cf6f0725dcbd7d7` | Technischer Wert |
| `9e91d30657a8` | `9e91d30657a8c46d74ced4d116ba76b0e016790f42811e8e7898bfa21b5a6bed` | Technischer Wert |
| `d28a56f3e001` | `d28a56f3e001058782b20fba00b2f9ccdf839194d97baadb9258a17f9376b31a` | Technischer Wert |
| `0a52825429b1` | `0a52825429b1dfccb252df1df53ec126adf22dd628c637afa5eb86f6d711afb0` | Fingerprint |
| `a9c0a53867bf` | `a9c0a53867bf2185243badd6ab2f920e368c27ba6ac4966718ed3a9eb029eb6f` | Technischer Wert |
| `0026180e240d` | `0026180e240d778970d41600856c63da9e935b604509ec8c0c3049cff09a068c` | Technischer Wert |
| `cf5edcbfee6c` | `cf5edcbfee6c91ba1ab7bf47290ea51f6c3ff81da696a7ecb7c19f237cde8c80` | Technischer Wert |
| `39d00eeab1b5` | `39d00eeab1b5aaa4bb2d176c13f3dec74c21349a73720a510260e1d173703d3f` | Technischer Wert |
| `988970376b43` | `988970376b43604b6d8419310dbc156595243e0d2f416b0e272fdaf6475712db` | Technischer Wert |
| `3ed296f7a81e` | `3ed296f7a81e28484eb181a3acf5ddd0b2795ab6ee9fa5262c501b6c298108a8` | Technischer Wert, Response-Digest |
| `08de7c786b64` | `08de7c786b64588d00fdf25d85b536a9dbd1c2d9b997db7854b7832f079ccae5` | Technischer Wert |
| `0732be200af6` | `0732be200af655154fd8eee38deead3305b9cd9f49df971221ebb25088828d6f` | Technischer Wert |
| `259010c770bf` | `259010c770bfd50768bd5ac5d926f6fcf64910e7207920e8ba3a847f6a5f86e5` | Request-ID |
| `6507787e17a8` | `6507787e17a84f00880b17c6083abd4592eac4c6734309c34ec6e61652f6cb73` | Request-ID |
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
Semantischer Record-Digest: `4064a7df49d3`

### Work-Unit · Slice 1 · Runde 1

| Seq/Record | Typ | Slice | Runde | Pfade | Findings |
|---|---|---|---:|---|---|
| 3. `ar1-cf5edcbfee6c` | Work-Unit | `1` | `1` | `docs/internal/resume-abschluss-verschiebt-task-in-outbox-implement-review-a9c0a538.md`, `docs/internal/slice-resume-abschluss-verschiebt-task-in-outbox-arbeitsplan-01-gebundene-gemeinsam-genutzte-queue-finalisierung-fur-direkten-resume.md`, `src/cli.py`, `src/inbox_watcher.py`, `src/orchestrator.py`, `tests/test_cli.py`, `tests/test_inbox_watcher.py`, `tests/test_orchestrator_runtime.py`, `tests/test_orchestrator_watch_cli.py` | keine |

### Codex · Runde 1 · ready

| Seq/Record | Rolle | Runde | Status | Work-Unit | Tests | Transport | Request | Response | Fingerprint |
|---|---|---:|---|---|---|---|---|---|---|
| 6. `ar1-3ed296f7a81e` | `codex` | `1` | `ready` | `2` | `tests/test_cli.py`, `tests/test_inbox_watcher.py`, `tests/test_orchestrator_runtime.py`, `tests/test_orchestrator_watch_cli.py` | `native-codex-v2` | `native-codex-request-259010c770bf` | `6507787e17a8` | `d4c99c7095db` |
<!-- artifact-records:approval-status:end -->
<!-- audit:approval-status:end -->
