# Slice 04 – Strukturierter Resume-Reader und Legacy-Kompatibilität

**Feature-Branch:** `feature/structured-agent-artifacts`
**GitHub-Status:** nur lokal

## Ziel des Slice

Strukturierter Resume-Reader und Legacy-Kompatibilität

## Akzeptanzkriterien

Siehe freigegebenen Arbeitsplan.

## Scope und Nicht-Scope

Erlaubter Scope: `docs/internal/slice-strukturierte-agentenkommunikation-arbeitsplan-04-strukturierter-resume-reader-und-legacy-kompatibilitat.md`, `docs/internal/strukturierte-agentenkommunikation-implement-review-542ccc72.md`, `src/artifact_migration.py`, `src/inbox_watcher.py`, `src/orchestrator.py`, `src/state_io.py`, `tests/test_artifact_migration.py`, `tests/test_inbox_watcher.py`, `tests/test_orchestrator_runtime.py`, `tests/test_orchestrator_watch_cli.py`, `tests/test_state_io.py`

## Diff-Risiko inklusive Branch- und Statuscheck

Branchcheck auf `feature/structured-agent-artifacts` war erfolgreich. Das zentrale
Risiko ist ein dauerhaft publizierter Record, dessen State-v3-Spiegel beim Absturz
noch nicht geschrieben wurde. Der Resolver behandelt fehlende, zusätzliche und
widersprüchliche spiegelbare Fakten deshalb als resumierbaren Reparaturhalt und
nennt Recordkopf beziehungsweise Record-ID. Bestehende Änderungen an
`pyproject.toml` und am konsolidierten Auditbericht wurden als Vorzustand erkannt
und nicht durch diesen Slice verändert.

## Geplante Tests

Gemäß Arbeitsplan und Orchestrator-Validierungsmatrix.

## Durchgeführte Änderungen

- Fail-closed Resume-Resolver für die unveränderliche Protokollbindung ergänzt.
- Task, Plan, Work-Unit/Runde, Findingstatus, Gates, Attestierungen,
  Quota-/Resume-Bindung, Commitbindung und Abschlussstatus semantisch gegen den
  State-v3-Spiegel geprüft.
- Ungebundene historische v3-Sitzungen bleiben ohne Recordimport im Legacy-Pfad.
- Watch-Sidecars der Version 2 binden den Protokollmodus; gelesene v1-Sidecars
  bleiben byte-semantisch im alten Format und werden nicht allein durch das Lesen
  migriert.
- Record-Widersprüche stoppen Watch resumierbar mit Exit 4 und werden nicht als
  technischer Retry bis zur Poison-Verschiebung behandelt; Poison-Diagnosen tragen
  den gebundenen Protokollmodus.
- Regressionstests für Legacy-Kompatibilität, vollständige strukturierte
  Rehydration, fehlende Records, Rundendrift, Sidecar-Versionierung und den
  resumierbaren Watch-Halt ergänzt.

## Ausgeführte Validierung mit Ergebnis

Wird durch den Orchestrator projiziert.

## Abweichungen vom Plan

Keine.

## Offene Risiken

Die strukturierte Kette bleibt absichtlich nicht automatisch aus einem alten
State-v3-Bestand rekonstruierbar. Operative Reparaturen müssen den passenden
Recordbestand oder den dazugehörigen State-Spiegel wiederherstellen; ein
mehrdeutiger Autoimport findet nicht statt.

## Review-Feedback von Claude

<!-- audit:claude-review:begin -->
### Ereignis 2: Runde 1

- Reviewer: `claude`
- Freigabe: `NO`
- Validierungsbindung: `validation-a0af689dcb19`
- Testdateien: `tests/test_artifact_migration.py`, `tests/test_inbox_watcher.py`, `tests/test_orchestrator_watch_cli.py`, `tests/test_state_io.py`
- Prüfdimensionen: checked dimensions = resume fail-closed symmetry per fact class (task/plan/work-unit/round/gate/finding/attestation/quota/commit-binding/completion), watch-sidecar v1→v2 compatibility and non-migration-on-read, protocol-mode mismatch handling in &#96;watch_inbox&#96;, exception routing from &#96;ArtifactResumeError&#96; into &#96;WatchTaskResult&#96; vs plain &#96;return 1&#96;
- Größtes Restrisiko: largest residual risk = the asymmetric gate/finding checks described in C-05
- Realistische Bruchbedingung: break condition = a production run where a gate decision or finding transition is durably written to state.json but the corresponding artifact-store append fails or is skipped, then the run is resumed under structured-v1 and the resolver accepts the divergent mirror without complaint.
- Eigene Findings: `C-01`, `C-02`, `C-03`, `C-04`, `C-05`

### Ereignis 4: Runde 2

- Reviewer: `claude`
- Freigabe: `NO`
- Validierungsbindung: `validation-62d5d73b64a3`
- Testdateien: `tests/test_artifact_migration.py`, `tests/test_inbox_watcher.py`, `tests/test_orchestrator_watch_cli.py`, `tests/test_state_io.py`
- Eigene Findings: `C-01`, `C-02`, `C-03`, `C-04`, `C-05`, `C-06`

### Ereignis 6: Runde 3

- Reviewer: `claude`
- Freigabe: `YES`
- Validierungsbindung: `validation-d321fd17411b`
- Testdateien: `tests/test_artifact_migration.py`, `tests/test_inbox_watcher.py`, `tests/test_orchestrator_watch_cli.py`, `tests/test_state_io.py`
- Prüfdimensionen: checked dimensions = symmetric fail-closed verification for every mirrorable fact class in resolve_resume_state() (task, plan, work-unit/round, gate decision, finding transition, validation attestation, quota pause, commit binding, workflow completion); quota_facts/quota_records tuple-key alignment against InvocationFailureRecord and QuotaPausePayload field shapes; non-regression of the four previously-symmetric checks (untouched this round); isolation of the new regression test from the wait_automatically=True/automatic-resume path; confirmation no other slice-04 file changed in this delta.
- Größtes Restrisiko: largest residual risk = &#96;quota_facts&#96; is filtered only on &#96;diff_fingerprint is not None and resume_at_utc is not None&#96;, independent of the &#96;wait_automatically&#96;/automatic-resume flag; if a future caller sets both fields for a manually-gated (non-automatic) quota-adjacent wait that is not by design backed by a persisted &#96;QuotaPausePayload&#96; chain record, this now-symmetric check would incorrectly halt resume on an otherwise legitimate mirror state — the &#96;QuotaPausePayload&#96;-emission contract itself lives outside this slice's diff and could not be directly inspected here.
- Realistische Bruchbedingung: realistic break condition = a later slice (5/6 cutover/hardening work) extends &#96;InvocationFailureRecord&#96; usage to a manual or non-automatic wait scenario that legitimately sets &#96;diff_fingerprint&#96;/&#96;resume_at_utc&#96; without emitting a matching chain record, causing every resume of that run to raise &#96;ArtifactResumeError&#96; even though the mirror is legitimate, and this is unguarded because the only existing coverage exercises the &#96;wait_automatically=True&#96; path.
- Eigene Findings: `C-01`, `C-02`, `C-03`, `C-04`, `C-05`, `C-06`
<!-- audit:claude-review:end -->

## Review-Feedback von Antigravity

<!-- audit:antigravity-review:begin -->
### Ereignis 7: Runde 1

- Reviewer: `antigravity`
- Freigabe: `YES`
- Validierungsbindung: `validation-d321fd17411b`
- Testdateien: `tests/test_artifact_migration.py`, `tests/test_inbox_watcher.py`, `tests/test_orchestrator_watch_cli.py`, `tests/test_state_io.py`
- Prüfdimensionen: resume fail-closed symmetry across all domain fact classes (Task, WorkUnit, Round, Plan, Gate decisions, Finding transitions, Validation attestations, Quota pauses, Resume checks, Commit bindings, Workflow completion), legacy state-v3 non-migration invariant, watch-sidecar v1/v2 format separation and non-destructive reads, exit-4 resumable halt exception routing in watch mode
- Größtes Restrisiko: _finding_statuses() in artifact_migration.py relies on canonical finding ID uniqueness across historical units in runtime_history; if an uncoordinated future component introduces an unrenumbered duplicate ID across work units without passing through _carry_forward_findings(), status aggregation during resume could conflate transitions
- Realistische Bruchbedingung: A future workflow modification introduces duplicate finding IDs across archived work unit history without running deterministic renumbering, causing set(latest_findings) != set(finding_statuses) in resolve_resume_state() to fail-closed with ArtifactResumeError on resume
- Eigene Findings: keine
<!-- audit:antigravity-review:end -->

## Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
- `C-01` Antwort 1: **angenommen** — Zero-Dependency-Vertrag wiederhergestellt; der exakte Akzeptanztest besteht.
- `C-02` Antwort 1: **angenommen** — Wie vorgesehen auf Slice 02 und dessen Store-Invarianten vertagt.
- `C-02` Antwort 2: **angenommen** — Failed- und stopped-Completion-Records mit final_binding_id werden durch die Store-Invariante abgewiesen und durch Slice-02-Negativtests abgedeckt.
- `C-03` Antwort 1: **angenommen** — Durable-but-reported-failed-Vertrag dokumentiert und persisted=True-Scanpfad direkt getestet.
- `C-04` Antwort 1: **angenommen** — Work-Unit-Idempotenzschlüssel enthalten nun die Rundennummer; derselbe Round bleibt idempotent, Round-Wechsel erzeugen geordnete Revisionen. Der exakte Regressionstest und 25 fokussierte Tests bestehen.
- `C-05` Antwort 1: **angenommen** — Symmetrische Gate- und Finding-Prüfungen erkennen nun Mirror-Fakten ohne Chain-Gegenstück; beide geforderten Regressionstests bestehen.
- `C-06` Antwort 1: **angenommen** — Mirror-seitige Quota-Pausen ohne korrespondierenden Chain-Record lösen jetzt fail-closed einen ArtifactResumeError aus.
<!-- audit:codex-responses:end -->

## Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### Ereignis 1: `validation-a0af689dcb19`

- Diff-Fingerprint: `a0af689dcb19f68f116e289f42e8531a9d42f08f3a663a6175dbe8dd93170b40`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: 1 passed; 0 failed; 0 unavailable; 1 required
- Ausgabedigest: `0637def0d61d38f7f031e390dacbecf998f08f5ae63f83cb3b80fee850764599`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| python3 -m pytest tests/ -v | PASS | 0 | ============================= test session starts ==============================<br>platform linux -- Python 3.12.3, pytest-7.4.4, pluggy-1.4.0 -- /usr/bin/python3<br>cachedir: .pytest_cache<br>rootdir: /mnt/c/Users/Diete/Sync/DE_Privat/Rente/ChatGPT CLI/Dual-Agent-Orchestrator<br>configfile: pyproject.toml<br>collecting ... collected 752 items<br><br>tests/test_agent_adapters.py::test_registry_contains_exact_role_identities PASSED [  0%]<br>tests/test_agent_adapters.py::test_codex_command_is_configured_workspace_write_jsonl_and_stdin PASSED [  0%]<br>tests/test_agent_adapters.py::test_codex_prefers_final_message_file_over_jsonl PASSED [  0%]<br>tests/test_agent_adapters.py::test_claude_defaults_are_quota_conscious_and_permissions_are_separate PASSED [  0%]<br>tests/test_agent_adapters.py::test_claude_json_envelope_tracks_usage_and_rejects_permission_denials PASSED [  0%]<br>tests/test_agent_adapters.py::test_normal_claude_review_does_not_expose_bound_harness PASSED [  0%]<br>tests/test_agent_adapters.py::test_capability_sm<br>...[83006 characters omitted]...<br>st_anchor_gate_persists_reset_and_resume_steps PASSED [ 98%]<br>tests/test_workflow_state.py::test_pre_slice11_v3_gate_and_work_unit_shapes_load_with_empty_new_fields PASSED [ 99%]<br>tests/test_workflow_state.py::test_early_slice11_work_unit_shape_loads_without_active_test_evidence PASSED [ 99%]<br>tests/test_workflow_state.py::test_policy_gate_roundtrips_and_resumes_at_same_step PASSED [ 99%]<br>tests/test_workflow_state.py::test_managed_audit_path_roundtrips_and_rejects_unsafe_locations PASSED [ 99%]<br>tests/test_workflow_state.py::test_policy_gate_rejects_fingerprint_bound_reason_and_unsafe_path PASSED [ 99%]<br>tests/test_workflow_state.py::test_plan_time_slice_one_start_commit_cannot_be_rebound_after_resume PASSED [ 99%]<br>tests/test_workflow_state.py::test_in_progress_slice_can_extend_exact_remediation_scope PASSED [ 99%]<br>tests/test_workflow_state.py::test_state_scope_rejects_orchestrator_internal_paths PASSED [100%]<br><br>============================= 752 passed in 24.63s ============================= |

### Ereignis 3: `validation-62d5d73b64a3`

- Diff-Fingerprint: `62d5d73b64a38cd4dc76238de0739de27cee8dbd71d90f8a5f1e4106710894c8`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: 2 passed; 0 failed; 0 unavailable; 2 required
- Ausgabedigest: `53e85504476b9c5164ef999473b0d390278b36e3194e8ec8356b9616af70c58a`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| python3 -m pytest tests/ -v | PASS | 0 | ============================= test session starts ==============================<br>platform linux -- Python 3.12.3, pytest-7.4.4, pluggy-1.4.0 -- /usr/bin/python3<br>cachedir: .pytest_cache<br>rootdir: /mnt/c/Users/Diete/Sync/DE_Privat/Rente/ChatGPT CLI/Dual-Agent-Orchestrator<br>configfile: pyproject.toml<br>collecting ... collected 754 items<br><br>tests/test_agent_adapters.py::test_registry_contains_exact_role_identities PASSED [  0%]<br>tests/test_agent_adapters.py::test_codex_command_is_configured_workspace_write_jsonl_and_stdin PASSED [  0%]<br>tests/test_agent_adapters.py::test_codex_prefers_final_message_file_over_jsonl PASSED [  0%]<br>tests/test_agent_adapters.py::test_claude_defaults_are_quota_conscious_and_permissions_are_separate PASSED [  0%]<br>tests/test_agent_adapters.py::test_claude_json_envelope_tracks_usage_and_rejects_permission_denials PASSED [  0%]<br>tests/test_agent_adapters.py::test_normal_claude_review_does_not_expose_bound_harness PASSED [  0%]<br>tests/test_agent_adapters.py::test_capability_sm<br>...[83257 characters omitted]...<br>st_anchor_gate_persists_reset_and_resume_steps PASSED [ 98%]<br>tests/test_workflow_state.py::test_pre_slice11_v3_gate_and_work_unit_shapes_load_with_empty_new_fields PASSED [ 99%]<br>tests/test_workflow_state.py::test_early_slice11_work_unit_shape_loads_without_active_test_evidence PASSED [ 99%]<br>tests/test_workflow_state.py::test_policy_gate_roundtrips_and_resumes_at_same_step PASSED [ 99%]<br>tests/test_workflow_state.py::test_managed_audit_path_roundtrips_and_rejects_unsafe_locations PASSED [ 99%]<br>tests/test_workflow_state.py::test_policy_gate_rejects_fingerprint_bound_reason_and_unsafe_path PASSED [ 99%]<br>tests/test_workflow_state.py::test_plan_time_slice_one_start_commit_cannot_be_rebound_after_resume PASSED [ 99%]<br>tests/test_workflow_state.py::test_in_progress_slice_can_extend_exact_remediation_scope PASSED [ 99%]<br>tests/test_workflow_state.py::test_state_scope_rejects_orchestrator_internal_paths PASSED [100%]<br><br>============================= 754 passed in 24.39s ============================= |
| python3 -m pytest tests/test_artifact_migration.py::test_structured_resume_halts_when_mirror_gate_decision_has_no_chain_record tests/test_artifact_migration.py::test_structured_resume_halts_when_mirror_finding_transition_has_no_chain_record -v | PASS | 0 | ============================= test session starts ==============================<br>platform linux -- Python 3.12.3, pytest-7.4.4, pluggy-1.4.0 -- /usr/bin/python3<br>cachedir: .pytest_cache<br>rootdir: /mnt/c/Users/Diete/Sync/DE_Privat/Rente/ChatGPT CLI/Dual-Agent-Orchestrator<br>configfile: pyproject.toml<br>collecting ... collected 2 items<br><br>tests/test_artifact_migration.py::test_structured_resume_halts_when_mirror_gate_decision_has_no_chain_record PASSED [ 50%]<br>tests/test_artifact_migration.py::test_structured_resume_halts_when_mirror_finding_transition_has_no_chain_record PASSED [100%]<br><br>============================== 2 passed in 0.35s =============================== |

### Ereignis 5: `validation-d321fd17411b`

- Diff-Fingerprint: `d321fd17411b5d2fa0440f09781614671573f18618e4739f4d5b21f50c1b9f15`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: 2 passed; 0 failed; 0 unavailable; 2 required
- Ausgabedigest: `3e6a94ce38754d5d2479a9ac4d52d0bfa50662d6768a3ffb5afef80bfe93d8df`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| python3 -m pytest tests/ -v | PASS | 0 | ============================= test session starts ==============================<br>platform linux -- Python 3.12.3, pytest-7.4.4, pluggy-1.4.0 -- /usr/bin/python3<br>cachedir: .pytest_cache<br>rootdir: /mnt/c/Users/Diete/Sync/DE_Privat/Rente/ChatGPT CLI/Dual-Agent-Orchestrator<br>configfile: pyproject.toml<br>collecting ... collected 755 items<br><br>tests/test_agent_adapters.py::test_registry_contains_exact_role_identities PASSED [  0%]<br>tests/test_agent_adapters.py::test_codex_command_is_configured_workspace_write_jsonl_and_stdin PASSED [  0%]<br>tests/test_agent_adapters.py::test_codex_prefers_final_message_file_over_jsonl PASSED [  0%]<br>tests/test_agent_adapters.py::test_claude_defaults_are_quota_conscious_and_permissions_are_separate PASSED [  0%]<br>tests/test_agent_adapters.py::test_claude_json_envelope_tracks_usage_and_rejects_permission_denials PASSED [  0%]<br>tests/test_agent_adapters.py::test_normal_claude_review_does_not_expose_bound_harness PASSED [  0%]<br>tests/test_agent_adapters.py::test_capability_sm<br>...[83378 characters omitted]...<br>st_anchor_gate_persists_reset_and_resume_steps PASSED [ 98%]<br>tests/test_workflow_state.py::test_pre_slice11_v3_gate_and_work_unit_shapes_load_with_empty_new_fields PASSED [ 99%]<br>tests/test_workflow_state.py::test_early_slice11_work_unit_shape_loads_without_active_test_evidence PASSED [ 99%]<br>tests/test_workflow_state.py::test_policy_gate_roundtrips_and_resumes_at_same_step PASSED [ 99%]<br>tests/test_workflow_state.py::test_managed_audit_path_roundtrips_and_rejects_unsafe_locations PASSED [ 99%]<br>tests/test_workflow_state.py::test_policy_gate_rejects_fingerprint_bound_reason_and_unsafe_path PASSED [ 99%]<br>tests/test_workflow_state.py::test_plan_time_slice_one_start_commit_cannot_be_rebound_after_resume PASSED [ 99%]<br>tests/test_workflow_state.py::test_in_progress_slice_can_extend_exact_remediation_scope PASSED [ 99%]<br>tests/test_workflow_state.py::test_state_scope_rejects_orchestrator_internal_paths PASSED [100%]<br><br>============================= 755 passed in 24.44s ============================= |
| python3 -m pytest tests/test_artifact_migration.py::test_structured_resume_halts_when_mirror_quota_pause_has_no_chain_record -v | PASS | 0 | ============================= test session starts ==============================<br>platform linux -- Python 3.12.3, pytest-7.4.4, pluggy-1.4.0 -- /usr/bin/python3<br>cachedir: .pytest_cache<br>rootdir: /mnt/c/Users/Diete/Sync/DE_Privat/Rente/ChatGPT CLI/Dual-Agent-Orchestrator<br>configfile: pyproject.toml<br>collecting ... collected 1 item<br><br>tests/test_artifact_migration.py::test_structured_resume_halts_when_mirror_quota_pause_has_no_chain_record PASSED [100%]<br><br>============================== 1 passed in 0.27s =============================== |
<!-- audit:validation-attestation:end -->

## Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
- Teständerungsfreigabe: nicht erfasst.
- Pre-Mortems:
  - Ereignis 6: In three months, the most likely failure is exactly the risk above — a future slice reuses &#96;diff_fingerprint&#96;/&#96;resume_at_utc&#96; on &#96;InvocationFailureRecord&#96; for a wait scenario not backed by a persisted &#96;QuotaPausePayload&#96; record (e.g., a manual/user-triggered gate or a retried-without-persist path), and the newly-symmetric check turns what should be a silent, valid resume into a hard, non-obvious &#96;ArtifactResumeError&#96;, because the fix closes exactly the demonstrated instance without adding a structural invariant (e.g., an explicit contract note tying &#96;diff_fingerprint&#96;/&#96;resume_at_utc&#96; presence to mandatory &#96;QuotaPausePayload&#96; emission) that would prevent the general pattern from recurring.
  - Ereignis 7: In three months, the most likely failure cause will be an uncoordinated evolution between state mirror structures and record payload schemas during Slice 5/6 cutover (such as adding an optional metadata field to TaskPayload or WorkUnitPayload without updating both resolve_resume_state() and ArtifactBridge._assert_equal()), causing valid dual-write runs to abruptly halt with ArtifactResumeError on resume when encountering states generated across slightly differing build versions.
<!-- audit:test-approval-premortem:end -->

## Findings-Lebenszyklus

<!-- audit:findings:begin -->
### `C-01` — `CLOSED`

- Quelle: `claude`; Runde 1
- Klasse: `BLOCKER`
- Finding: Slice 01 adds a new runtime dependency (&#96;jsonschema&gt;=4.18,&lt;5&#96;) to &#96;pyproject.toml&#96; for offline JSON-Schema validation in &#96;src/artifact_models.py&#96;, but the pre-existing invariant test &#96;tests/test_cli.py::test_declared_python_floor_matches_standard_library_toml_requirement&#96; still asserts &#96;dependencies == []&#96;, so the bound orchestrator attestation for fingerprint f30b04cebf94756b25051f21bd5590a80075607da1c8e34685f17eb44b9ea028 is FAIL (1 failed / 713 passed). The slice must not merge an unreconciled dependency-policy break: either reconcile the "stdlib-only/portable" invariant explicitly (with the guard test updated in the same, reviewed change) or replace &#96;jsonschema&#96; with a stdlib-only Draft-2020-12 validator so the declared zero-dependency contract holds.
- Akzeptanztest: VALIDATE: ["python3","-m","pytest","tests/test_cli.py::test_declared_python_floor_matches_standard_library_toml_requirement","-v"]
- Statusbegründung: Runtime &#96;jsonschema&#96; dependency removed; &#96;src/artifact_models.py&#96; now implements an offline, self-checking Draft-2020-12 subset validator with no third-party dependency; the bound attestation for fingerprint 6ce9f6f31673ac0333e7da8a4752ab18805ac64c803e298640eab9a2d0a60aba shows both the full suite (714 passed/0 failed) and the exact previously-failing guard test (&#96;tests/test_cli.py::test_declared_python_floor_matches_standard_library_toml_requirement&#96;) passing, restoring the zero-dependency invariant.

### `C-02` — `CLOSED`

- Quelle: `claude`; Runde 1
- Klasse: `OBSERVATION`
- Finding: &#96;WorkflowCompletionPayload&#96;/schema &#96;workflow_completion.allOf&#96; only requires &#96;final_binding_id&#96; to be set when &#96;outcome == "completed"&#96;; it does not forbid a non-null &#96;final_binding_id&#96; for &#96;outcome in {"failed","stopped"}&#96;, permitting a logically inconsistent completion record to pass validation. This is acceptable to defer given cross-record invariants are explicitly scoped to Slice 02 (append-only store), but should be closed with an explicit negative-case test once that store exists.
- Akzeptanztest: Add a Slice-02-era test asserting a &#96;workflow_completion&#96; record with &#96;outcome&#96; in &#96;{"failed","stopped"}&#96; and a non-null &#96;final_binding_id&#96; is rejected by either the model or the store-level invariant check.
- Statusbegründung: Acceptance test satisfied: &#96;failed&#96;/&#96;stopped&#96; &#96;WorkflowCompletionPayload&#96; records carrying a non-null &#96;final_binding_id&#96; are now rejected at the store boundary (&#96;test_store_rejects_non_success_completion_with_binding&#96;, plus the symmetric scan-time invariant in &#96;_validate_store_invariants(persisted=True)&#96;), matching the negative-case acceptance criterion I set in Slice 01.

### `C-03` — `CLOSED`

- Quelle: `claude`; Runde 1
- Klasse: `OBSERVATION`
- Finding: &#96;ArtifactStore.put()&#96; can raise an exception (e.g., a transient error during the mandatory post-publish re-scan/head-cache refresh) after the record bytes are already durably and atomically published to &#96;records_dir&#96;, and this "durable-but-reported-as-failed" retry contract is exercised by one crash-recovery test but is not documented as an explicit API guarantee on &#96;put()&#96;. Slice 03 (dual-write/semantic equality) will depend on exactly this guarantee to decide whether to retry vs. treat an exception as "nothing was written," so an undocumented/underspecified contract here risks a double-write or a lost-write assumption once callers are wired in.
- Akzeptanztest: Add a docstring/contract note on &#96;ArtifactStore.put()&#96; stating that any exception raised after bytes are written to &#96;records_dir&#96; must be treated as "possibly already persisted, safe to retry the identical call," and add a scan-time test that directly (via a hand-written envelope, bypassing &#96;put()&#96;) exercises the &#96;persisted=True&#96; branch of &#96;_validate_store_invariants&#96; so the &#96;load_chain()&#96;-side completion-invariant rejection path has direct coverage independent of &#96;put()&#96;.
- Statusbegründung: Acceptance criteria fully met in this diff: &#96;ArtifactStore.put()&#96; now carries an explicit docstring contract ("An exception does not prove that nothing was written... callers must treat every exception as possibly already persisted and may safely retry the identical logical call"), and &#96;tests/test_artifact_store.py::test_scan_rejects_persisted_non_success_completion_with_binding&#96; directly exercises the &#96;persisted=True&#96; scan branch via a hand-written envelope that bypasses &#96;put()&#96;, matching my Slice-02 acceptance test verbatim. Closing as originally reported by me.

### `C-04` — `CLOSED`

- Quelle: `claude`; Runde 1
- Klasse: `BLOCKER`
- Finding: &#96;ProductionWorkflowDriver._persist_structured_baseline()&#96; (invoked unconditionally, uncaught, from every &#96;bind_work_unit()&#96; call — i.e. every rebind/resume within a work unit) derives the &#96;WorkUnitPayload&#96; idempotency key solely from &#96;unit.work_unit_id&#96; (&#96;idempotency_key=f"work-unit:{unit.work_unit_id}"&#96;), while the payload it constructs embeds &#96;round_number=unit.round_number&#96;. Elsewhere in the same diff (&#96;persist_codex_contract&#96;'s &#96;logical=f"agent-{unit.work_unit_id}-{state.current_step.value}-{unit.round_number}"&#96;, and &#96;persist_review_contract&#96;'s explicit &#96;round_number&#96; parameter used in its own idempotency key) the codebase treats &#96;round_number&#96; as a value that legitimately changes across multiple rounds within one &#96;work_unit_id&#96; (repair/retry/second-review cycles). The first &#96;bind_work_unit()&#96; call for round 1 durably publishes &#96;WorkUnitPayload(round_number=1,...)&#96; under key &#96;work-unit:&lt;id&gt;&#96;. As soon as the same work unit advances to round 2 and &#96;bind_work_unit()&#96; fires again, &#96;ArtifactBridge.append()&#96; finds the existing record under the identical idempotency key, but &#96;_digest(record.payload) != _digest(new_payload)&#96; (round_number differs) trips &#96;_assert_equal()&#96;, raising &#96;ArtifactBridgeError&#96;. &#96;_persist_structured_baseline()&#96; is called directly, not through &#96;WorkflowEngine._persist_structured&#96;'s try/except-to-&#96;WorkflowExecutionError&#96; wrapper, so this exception is not fail-closed gracefully — it propagates as a raw, undocumented crash out of &#96;bind_work_unit()&#96;, which is the very first thing the driver does on every resume. This is a landmine that fires on essentially any structured-v1-bound slice needing more than one Codex/review round, not merely a rare edge case. The only existing coverage (&#96;test_structured_bind_persists_contract_and_active_work_unit_once&#96;) calls &#96;bind_work_unit(state)&#96; twice with the SAME unchanged &#96;state&#96; object, so it cannot and does not exercise a round-number transition within one &#96;work_unit_id&#96;; the defect is unguarded by any test.
- Akzeptanztest: acceptance=VALIDATE: ["python3","-m","pytest","tests/test_orchestrator_runtime.py::test_structured_bind_survives_round_number_increase_within_same_work_unit","-v"]
- Statusbegründung: Idempotency key for WorkUnitPayload now includes round_number (&#96;work-unit:&lt;id&gt;:round:&lt;n&gt;&#96;), so same-round re-binds stay idempotent and round transitions persist as a new ordered revision instead of raising ArtifactBridgeError. The exact acceptance test (test_structured_bind_survives_round_number_increase_within_same_work_unit) is present, exercises exactly the previously-broken transition, and passes in the bound 744-test attestation for the current fingerprint. Closing as originally reported by me.

### `C-05` — `CLOSED`

- Quelle: `claude`; Runde 1
- Klasse: `BLOCKER`
- Finding: &#96;resolve_resume_state()&#96; in &#96;src/artifact_migration.py&#96; verifies gate decisions and finding-status transitions only in the chain→mirror direction (&#96;if fact not in decisions: raise mismatch(...)&#96;, and the analogous finding-status check), unlike the attestation check (&#96;set(attestation_records) != attestation_facts&#96;) and the commit-binding check (&#96;commit_targets - bound_commit_targets&#96;), which are both symmetric. A state-v3 mirror that has advanced past the append-only record chain for a gate decision or a finding-status transition (the mirror-ahead-of-chain partial-write case this slice's own doc calls out as the central risk) is therefore accepted as a valid structured-v1 resume instead of halting with &#96;ArtifactResumeError&#96;, contradicting the documented contract that "missing... spiegelbare Fakten" must produce a resumable repair halt. No test in &#96;tests/test_artifact_migration.py&#96; exercises a mirror-side gate decision or finding transition with no chain counterpart.
- Akzeptanztest: VALIDATE: ["python3","-m","pytest","tests/test_artifact_migration.py::test_structured_resume_halts_when_mirror_gate_decision_has_no_chain_record","tests/test_artifact_migration.py::test_structured_resume_halts_when_mirror_finding_transition_has_no_chain_record","-v"]
- Statusbegründung: Exact acceptance test (two named regression tests) present and passing in the bound attestation; fix applies the same symmetric-set pattern already used for attestations/commit-bindings to gate decisions and finding transitions, correctly closing the mirror-ahead-of-chain gap I reported.

### `C-06` — `CLOSED`

- Quelle: `claude`; Runde 2
- Klasse: `BLOCKER`
- Finding: &#96;resolve_resume_state()&#96;'s quota-pause verification in &#96;src/artifact_migration.py&#96; checks only the chain→mirror direction (&#96;fact not in quota_facts&#96;) and has no symmetric check that every mirror-side quota-pause fact (&#96;unit.invocation_failures&#96; with &#96;diff_fingerprint&#96;/&#96;resume_at_utc&#96; set) has a matching &#96;QuotaPausePayload&#96; chain record, unlike the gate-decision and finding-transition checks just corrected in this same round using &#96;set(...) != set(...)&#96;. A quota pause durably written to state.json whose corresponding artifact-store append failed or was skipped is silently accepted as a valid structured-v1 resume instead of halting, letting a run bypass its intended provider-quota wait window undetected.
- Akzeptanztest: VALIDATE: ["python3","-m","pytest","tests/test_artifact_migration.py::test_structured_resume_halts_when_mirror_quota_pause_has_no_chain_record","-v"]
- Statusbegründung: The asymmetric quota-pause check is now symmetric (&#96;set(quota_records) != quota_facts&#96;), matching the pattern used for gate/finding/attestation/commit-binding checks. The exact acceptance test (&#96;test_structured_resume_halts_when_mirror_quota_pause_has_no_chain_record&#96;) is present and passes in the bound attestation for fingerprint d321fd17411b5d2fa0440f09781614671573f18618e4739f4d5b21f50c1b9f15 (755 passed full suite + isolated 1-test run, both PASS). Closing as originally reported by me.
<!-- audit:findings:end -->

## Entscheidungstabelle

<!-- audit:decision-table:begin -->
| ID | Quelle | Finding | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|---|
| C-01 | claude | Slice 01 adds a new runtime dependency (&#96;jsonschema&gt;=4.18,&lt;5&#96;) to &#96;pyproject.toml&#96; for offline JSON-Schema validation in &#96;src/artifact_models.py&#96;, but the pre-existing invariant test &#96;tests/test_cli.py::test_declared_python_floor_matches_standard_library_toml_requirement&#96; still asserts &#96;dependencies == []&#96;, so the bound orchestrator attestation for fingerprint f30b04cebf94756b25051f21bd5590a80075607da1c8e34685f17eb44b9ea028 is FAIL (1 failed / 713 passed). The slice must not merge an unreconciled dependency-policy break: either reconcile the "stdlib-only/portable" invariant explicitly (with the guard test updated in the same, reviewed change) or replace &#96;jsonschema&#96; with a stdlib-only Draft-2020-12 validator so the declared zero-dependency contract holds. | BLOCKER | angenommen | erledigt: Runtime &#96;jsonschema&#96; dependency removed; &#96;src/artifact_models.py&#96; now implements an offline, self-checking Draft-2020-12 subset validator with no third-party dependency; the bound attestation for fingerprint 6ce9f6f31673ac0333e7da8a4752ab18805ac64c803e298640eab9a2d0a60aba shows both the full suite (714 passed/0 failed) and the exact previously-failing guard test (&#96;tests/test_cli.py::test_declared_python_floor_matches_standard_library_toml_requirement&#96;) passing, restoring the zero-dependency invariant. |
| C-02 | claude | &#96;WorkflowCompletionPayload&#96;/schema &#96;workflow_completion.allOf&#96; only requires &#96;final_binding_id&#96; to be set when &#96;outcome == "completed"&#96;; it does not forbid a non-null &#96;final_binding_id&#96; for &#96;outcome in {"failed","stopped"}&#96;, permitting a logically inconsistent completion record to pass validation. This is acceptable to defer given cross-record invariants are explicitly scoped to Slice 02 (append-only store), but should be closed with an explicit negative-case test once that store exists. | OBSERVATION | angenommen | erledigt: Acceptance test satisfied: &#96;failed&#96;/&#96;stopped&#96; &#96;WorkflowCompletionPayload&#96; records carrying a non-null &#96;final_binding_id&#96; are now rejected at the store boundary (&#96;test_store_rejects_non_success_completion_with_binding&#96;, plus the symmetric scan-time invariant in &#96;_validate_store_invariants(persisted=True)&#96;), matching the negative-case acceptance criterion I set in Slice 01. |
| C-03 | claude | &#96;ArtifactStore.put()&#96; can raise an exception (e.g., a transient error during the mandatory post-publish re-scan/head-cache refresh) after the record bytes are already durably and atomically published to &#96;records_dir&#96;, and this "durable-but-reported-as-failed" retry contract is exercised by one crash-recovery test but is not documented as an explicit API guarantee on &#96;put()&#96;. Slice 03 (dual-write/semantic equality) will depend on exactly this guarantee to decide whether to retry vs. treat an exception as "nothing was written," so an undocumented/underspecified contract here risks a double-write or a lost-write assumption once callers are wired in. | OBSERVATION | angenommen | erledigt: Acceptance criteria fully met in this diff: &#96;ArtifactStore.put()&#96; now carries an explicit docstring contract ("An exception does not prove that nothing was written... callers must treat every exception as possibly already persisted and may safely retry the identical logical call"), and &#96;tests/test_artifact_store.py::test_scan_rejects_persisted_non_success_completion_with_binding&#96; directly exercises the &#96;persisted=True&#96; scan branch via a hand-written envelope that bypasses &#96;put()&#96;, matching my Slice-02 acceptance test verbatim. Closing as originally reported by me. |
| C-04 | claude | &#96;ProductionWorkflowDriver._persist_structured_baseline()&#96; (invoked unconditionally, uncaught, from every &#96;bind_work_unit()&#96; call — i.e. every rebind/resume within a work unit) derives the &#96;WorkUnitPayload&#96; idempotency key solely from &#96;unit.work_unit_id&#96; (&#96;idempotency_key=f"work-unit:{unit.work_unit_id}"&#96;), while the payload it constructs embeds &#96;round_number=unit.round_number&#96;. Elsewhere in the same diff (&#96;persist_codex_contract&#96;'s &#96;logical=f"agent-{unit.work_unit_id}-{state.current_step.value}-{unit.round_number}"&#96;, and &#96;persist_review_contract&#96;'s explicit &#96;round_number&#96; parameter used in its own idempotency key) the codebase treats &#96;round_number&#96; as a value that legitimately changes across multiple rounds within one &#96;work_unit_id&#96; (repair/retry/second-review cycles). The first &#96;bind_work_unit()&#96; call for round 1 durably publishes &#96;WorkUnitPayload(round_number=1,...)&#96; under key &#96;work-unit:&lt;id&gt;&#96;. As soon as the same work unit advances to round 2 and &#96;bind_work_unit()&#96; fires again, &#96;ArtifactBridge.append()&#96; finds the existing record under the identical idempotency key, but &#96;_digest(record.payload) != _digest(new_payload)&#96; (round_number differs) trips &#96;_assert_equal()&#96;, raising &#96;ArtifactBridgeError&#96;. &#96;_persist_structured_baseline()&#96; is called directly, not through &#96;WorkflowEngine._persist_structured&#96;'s try/except-to-&#96;WorkflowExecutionError&#96; wrapper, so this exception is not fail-closed gracefully — it propagates as a raw, undocumented crash out of &#96;bind_work_unit()&#96;, which is the very first thing the driver does on every resume. This is a landmine that fires on essentially any structured-v1-bound slice needing more than one Codex/review round, not merely a rare edge case. The only existing coverage (&#96;test_structured_bind_persists_contract_and_active_work_unit_once&#96;) calls &#96;bind_work_unit(state)&#96; twice with the SAME unchanged &#96;state&#96; object, so it cannot and does not exercise a round-number transition within one &#96;work_unit_id&#96;; the defect is unguarded by any test. | BLOCKER | angenommen | erledigt: Idempotency key for WorkUnitPayload now includes round_number (&#96;work-unit:&lt;id&gt;:round:&lt;n&gt;&#96;), so same-round re-binds stay idempotent and round transitions persist as a new ordered revision instead of raising ArtifactBridgeError. The exact acceptance test (test_structured_bind_survives_round_number_increase_within_same_work_unit) is present, exercises exactly the previously-broken transition, and passes in the bound 744-test attestation for the current fingerprint. Closing as originally reported by me. |
| C-05 | claude | &#96;resolve_resume_state()&#96; in &#96;src/artifact_migration.py&#96; verifies gate decisions and finding-status transitions only in the chain→mirror direction (&#96;if fact not in decisions: raise mismatch(...)&#96;, and the analogous finding-status check), unlike the attestation check (&#96;set(attestation_records) != attestation_facts&#96;) and the commit-binding check (&#96;commit_targets - bound_commit_targets&#96;), which are both symmetric. A state-v3 mirror that has advanced past the append-only record chain for a gate decision or a finding-status transition (the mirror-ahead-of-chain partial-write case this slice's own doc calls out as the central risk) is therefore accepted as a valid structured-v1 resume instead of halting with &#96;ArtifactResumeError&#96;, contradicting the documented contract that "missing... spiegelbare Fakten" must produce a resumable repair halt. No test in &#96;tests/test_artifact_migration.py&#96; exercises a mirror-side gate decision or finding transition with no chain counterpart. | BLOCKER | angenommen | erledigt: Exact acceptance test (two named regression tests) present and passing in the bound attestation; fix applies the same symmetric-set pattern already used for attestations/commit-bindings to gate decisions and finding transitions, correctly closing the mirror-ahead-of-chain gap I reported. |
| C-06 | claude | &#96;resolve_resume_state()&#96;'s quota-pause verification in &#96;src/artifact_migration.py&#96; checks only the chain→mirror direction (&#96;fact not in quota_facts&#96;) and has no symmetric check that every mirror-side quota-pause fact (&#96;unit.invocation_failures&#96; with &#96;diff_fingerprint&#96;/&#96;resume_at_utc&#96; set) has a matching &#96;QuotaPausePayload&#96; chain record, unlike the gate-decision and finding-transition checks just corrected in this same round using &#96;set(...) != set(...)&#96;. A quota pause durably written to state.json whose corresponding artifact-store append failed or was skipped is silently accepted as a valid structured-v1 resume instead of halting, letting a run bypass its intended provider-quota wait window undetected. | BLOCKER | angenommen | erledigt: The asymmetric quota-pause check is now symmetric (&#96;set(quota_records) != quota_facts&#96;), matching the pattern used for gate/finding/attestation/commit-binding checks. The exact acceptance test (&#96;test_structured_resume_halts_when_mirror_quota_pause_has_no_chain_record&#96;) is present and passes in the bound attestation for fingerprint d321fd17411b5d2fa0440f09781614671573f18618e4739f4d5b21f50c1b9f15 (755 passed full suite + isolated 1-test run, both PASS). Closing as originally reported by me. |
<!-- audit:decision-table:end -->

## Rückdokumentation in die Arbeitsplan-MD

Arbeitsplan: `docs/internal/strukturierte-agentenkommunikation-implement-review-542ccc72.md`

## Freigabestatus

<!-- audit:approval-status:begin -->
- Implementierung bereit: `YES`
- Validierung: `PASS`
- Claude-Freigabe: `YES`
- Antigravity-Freigabe: `YES`
- Red-State-Folgeslice: `NONE`
- Commit autorisiert: `YES`
<!-- audit:approval-status:end -->
