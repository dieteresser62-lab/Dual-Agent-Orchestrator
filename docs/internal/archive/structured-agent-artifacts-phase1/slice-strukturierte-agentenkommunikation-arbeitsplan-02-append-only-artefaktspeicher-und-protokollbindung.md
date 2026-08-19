# Slice 02 – Append-only Artefaktspeicher und Protokollbindung

**Feature-Branch:** `feature/structured-agent-artifacts`
**GitHub-Status:** nur lokal

## Ziel des Slice

Append-only Artefaktspeicher und Protokollbindung

## Akzeptanzkriterien

Siehe freigegebenen Arbeitsplan.

## Scope und Nicht-Scope

Erlaubter Scope: `docs/internal/slice-strukturierte-agentenkommunikation-arbeitsplan-02-append-only-artefaktspeicher-und-protokollbindung.md`, `docs/internal/strukturierte-agentenkommunikation-implement-review-542ccc72.md`, `src/artifact_store.py`, `src/state_io.py`, `src/workflow_state.py`, `tests/test_artifact_store.py`, `tests/test_state_io.py`, `tests/test_workflow_state.py`

## Diff-Risiko inklusive Branch- und Statuscheck

Der Branch entspricht `feature/structured-agent-artifacts`. Vorbestehende Änderungen aus Slice 01 an `pyproject.toml` und am konsolidierten Auditbericht wurden nicht verändert. Das höchste Slice-Risiko liegt in einer stillen Umdeutung alter State-v3-Dateien; deshalb bleibt eine fehlende Bindung explizit Legacy und eine bereits persistierte Bindung darf beim Speichern oder Resume weder ergänzt noch gewechselt werden.

## Geplante Tests

Gemäß Arbeitsplan und Orchestrator-Validierungsmatrix.

## Durchgeführte Änderungen

- Root-gebundener Store unter `.orchestrator/artifacts/<run-id>/records/` mit atomarer Veröffentlichung, kanonischem Inhaltsdigest und rekonstruierbarem Head-Cache.
- Vollständiger Scan mit Prüfung von Schema, Digest, Run-Zugehörigkeit, Idempotenz, Revisionen, Vorgängerlücken, Forks, Zyklen und Store-spezifischen Completion-Invarianten.
- Idempotentes `put`, lineares `load_chain` sowie typ- und logische-ID-basierte Selektion; temporäre Dateien werden ignoriert und ein fehlender oder veralteter Cache aus den Records erneuert.
- Optionale unveränderliche `ProtocolBinding` in State-v3 mit den Modi `legacy-state-v3`/Schema 3 und `structured-v1`/Schema 1; historische Dokumente ohne Feld bleiben im effektiven Legacy-Modus.
- State- und Checkpoint-Resume können gegen eine erwartete Bindung geprüft werden; bestehende Dateien dürfen ihre Bindung nicht nachträglich ergänzen oder wechseln.
- Negativtest für C-02: `failed` und `stopped` mit nichtleerer `final_binding_id` werden an der Store-Grenze abgewiesen.

## Ausgeführte Validierung mit Ergebnis

Fokussiert ausgeführt: `python3 -m pytest tests/test_artifact_store.py tests/test_state_io.py tests/test_workflow_state.py -q` — 95 Tests bestanden. Zusätzlich bestanden `python3 -m py_compile src/artifact_store.py src/state_io.py src/workflow_state.py` und `git diff --check` für die produktiven und Testpfade des Slice. Die autoritative Validierungsattestierung wird weiterhin ausschließlich vom Orchestrator in den verwalteten Block projiziert.

## Abweichungen vom Plan

Keine erfasst.

## Offene Risiken

Der Store ist in diesem Slice noch nicht an Workflowentscheidungen angeschlossen; Dual-Write und semantischer Gleichheitsnachweis folgen planmäßig in Slice 03. Der Head-Cache ist absichtlich nicht autoritativ und kann jederzeit aus der vollständig validierten Recordkette rekonstruiert werden.

## Review-Feedback von Claude

<!-- audit:claude-review:begin -->
### Ereignis 2: Runde 1

- Reviewer: `claude`
- Freigabe: `YES`
- Validierungsbindung: `validation-0e57f0f38615`
- Testdateien: `tests/test_artifact_store.py`, `tests/test_state_io.py`, `tests/test_workflow_state.py`
- Eigene Findings: `C-01`, `C-02`, `C-03`
<!-- audit:claude-review:end -->

## Review-Feedback von Antigravity

<!-- audit:antigravity-review:begin -->
### Ereignis 3: Runde 1

- Reviewer: `antigravity`
- Freigabe: `YES`
- Validierungsbindung: `validation-0e57f0f38615`
- Testdateien: `tests/test_artifact_store.py`, `tests/test_state_io.py`, `tests/test_workflow_state.py`
- Prüfdimensionen: append-only chain invariants (head chaining, duplicate revision checks, idempotent retry deduplication), topological sort &amp; cycle/fork/gap detection in load_chain, atomic write &amp; directory fsync crash recovery, protocol binding immutability and resume validation, and path confinement
- Größtes Restrisiko: dual-write synchronization between legacy state checkpoints and structured artifact store in subsequent Slice 03 where partial failure during dual-write could cause divergent state
- Realistische Bruchbedingung: a workflow crash occurs in Slice 03 after ArtifactStore.put succeeds but before legacy state.json is persisted, causing a resumed step to attempt appending with a modified predecessor or regenerated idempotency key without handling idempotent store lookup
- Eigene Findings: keine
<!-- audit:antigravity-review:end -->

## Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
- `C-01` Antwort 1: **angenommen** — Zero-Dependency-Vertrag wiederhergestellt; der exakte Akzeptanztest besteht.
- `C-02` Antwort 1: **angenommen** — Wie vorgesehen auf Slice 02 und dessen Store-Invarianten vertagt.
- `C-02` Antwort 2: **angenommen** — Failed- und stopped-Completion-Records mit final_binding_id werden durch die Store-Invariante abgewiesen und durch Slice-02-Negativtests abgedeckt.
<!-- audit:codex-responses:end -->

## Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### Ereignis 1: `validation-0e57f0f38615`

- Diff-Fingerprint: `0e57f0f38615fd2c6cb0de4d8e50e5f7189287464351f7990ba2f9f5ba5f66b4`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: 1 passed; 0 failed; 0 unavailable; 1 required
- Ausgabedigest: `2ce8abe85fa2054249aa6e7e19b5179da15c31e3e57e344b941a0582363deb1a`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| python3 -m pytest tests/ -v | PASS | 0 | ============================= test session starts ==============================<br>platform linux -- Python 3.12.3, pytest-7.4.4, pluggy-1.4.0 -- /usr/bin/python3<br>cachedir: .pytest_cache<br>rootdir: /mnt/c/Users/Diete/Sync/DE_Privat/Rente/ChatGPT CLI/Dual-Agent-Orchestrator<br>configfile: pyproject.toml<br>collecting ... collected 733 items<br><br>tests/test_agent_adapters.py::test_registry_contains_exact_role_identities PASSED [  0%]<br>tests/test_agent_adapters.py::test_codex_command_is_configured_workspace_write_jsonl_and_stdin PASSED [  0%]<br>tests/test_agent_adapters.py::test_codex_prefers_final_message_file_over_jsonl PASSED [  0%]<br>tests/test_agent_adapters.py::test_claude_defaults_are_quota_conscious_and_permissions_are_separate PASSED [  0%]<br>tests/test_agent_adapters.py::test_claude_json_envelope_tracks_usage_and_rejects_permission_denials PASSED [  0%]<br>tests/test_agent_adapters.py::test_normal_claude_review_does_not_expose_bound_harness PASSED [  0%]<br>tests/test_agent_adapters.py::test_capability_sm<br>...[80932 characters omitted]...<br>st_anchor_gate_persists_reset_and_resume_steps PASSED [ 98%]<br>tests/test_workflow_state.py::test_pre_slice11_v3_gate_and_work_unit_shapes_load_with_empty_new_fields PASSED [ 99%]<br>tests/test_workflow_state.py::test_early_slice11_work_unit_shape_loads_without_active_test_evidence PASSED [ 99%]<br>tests/test_workflow_state.py::test_policy_gate_roundtrips_and_resumes_at_same_step PASSED [ 99%]<br>tests/test_workflow_state.py::test_managed_audit_path_roundtrips_and_rejects_unsafe_locations PASSED [ 99%]<br>tests/test_workflow_state.py::test_policy_gate_rejects_fingerprint_bound_reason_and_unsafe_path PASSED [ 99%]<br>tests/test_workflow_state.py::test_plan_time_slice_one_start_commit_cannot_be_rebound_after_resume PASSED [ 99%]<br>tests/test_workflow_state.py::test_in_progress_slice_can_extend_exact_remediation_scope PASSED [ 99%]<br>tests/test_workflow_state.py::test_state_scope_rejects_orchestrator_internal_paths PASSED [100%]<br><br>============================= 733 passed in 24.50s ============================= |
<!-- audit:validation-attestation:end -->

## Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
- Teständerungsfreigabe: nicht erfasst.
- Pre-Mortems:
  - Ereignis 2: In three months, the most likely failure is a Slice-03/04 caller misinterpreting a &#96;put()&#96; exception as "nothing was written" (retrying with a different idempotency key, or silently skipping the legacy-text mirror write), producing a divergent dual-write pair or a duplicate structured record — because the durable-but-possibly-erroring contract on &#96;put()&#96; is proven by test but not yet a documented API guarantee that later slices can rely on without re-deriving it from source.
  - Ereignis 3: In three months, the most likely failure cause is a future dual-write adapter or recovery routine in Slice 03/04 attempting to resume or retry an interrupted step under a freshly minted idempotency key or misaligned predecessor pointer, resulting in an unhandled ArtifactConflictError instead of leveraging the store's idempotent retry mechanism.
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

### `C-03` — `OPEN`

- Quelle: `claude`; Runde 1
- Klasse: `OBSERVATION`
- Finding: &#96;ArtifactStore.put()&#96; can raise an exception (e.g., a transient error during the mandatory post-publish re-scan/head-cache refresh) after the record bytes are already durably and atomically published to &#96;records_dir&#96;, and this "durable-but-reported-as-failed" retry contract is exercised by one crash-recovery test but is not documented as an explicit API guarantee on &#96;put()&#96;. Slice 03 (dual-write/semantic equality) will depend on exactly this guarantee to decide whether to retry vs. treat an exception as "nothing was written," so an undocumented/underspecified contract here risks a double-write or a lost-write assumption once callers are wired in.
- Akzeptanztest: Add a docstring/contract note on &#96;ArtifactStore.put()&#96; stating that any exception raised after bytes are written to &#96;records_dir&#96; must be treated as "possibly already persisted, safe to retry the identical call," and add a scan-time test that directly (via a hand-written envelope, bypassing &#96;put()&#96;) exercises the &#96;persisted=True&#96; branch of &#96;_validate_store_invariants&#96; so the &#96;load_chain()&#96;-side completion-invariant rejection path has direct coverage independent of &#96;put()&#96;.
- Statusbegründung: –
<!-- audit:findings:end -->

## Entscheidungstabelle

<!-- audit:decision-table:begin -->
| ID | Quelle | Finding | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|---|
| C-01 | claude | Slice 01 adds a new runtime dependency (&#96;jsonschema&gt;=4.18,&lt;5&#96;) to &#96;pyproject.toml&#96; for offline JSON-Schema validation in &#96;src/artifact_models.py&#96;, but the pre-existing invariant test &#96;tests/test_cli.py::test_declared_python_floor_matches_standard_library_toml_requirement&#96; still asserts &#96;dependencies == []&#96;, so the bound orchestrator attestation for fingerprint f30b04cebf94756b25051f21bd5590a80075607da1c8e34685f17eb44b9ea028 is FAIL (1 failed / 713 passed). The slice must not merge an unreconciled dependency-policy break: either reconcile the "stdlib-only/portable" invariant explicitly (with the guard test updated in the same, reviewed change) or replace &#96;jsonschema&#96; with a stdlib-only Draft-2020-12 validator so the declared zero-dependency contract holds. | BLOCKER | angenommen | erledigt: Runtime &#96;jsonschema&#96; dependency removed; &#96;src/artifact_models.py&#96; now implements an offline, self-checking Draft-2020-12 subset validator with no third-party dependency; the bound attestation for fingerprint 6ce9f6f31673ac0333e7da8a4752ab18805ac64c803e298640eab9a2d0a60aba shows both the full suite (714 passed/0 failed) and the exact previously-failing guard test (&#96;tests/test_cli.py::test_declared_python_floor_matches_standard_library_toml_requirement&#96;) passing, restoring the zero-dependency invariant. |
| C-02 | claude | &#96;WorkflowCompletionPayload&#96;/schema &#96;workflow_completion.allOf&#96; only requires &#96;final_binding_id&#96; to be set when &#96;outcome == "completed"&#96;; it does not forbid a non-null &#96;final_binding_id&#96; for &#96;outcome in {"failed","stopped"}&#96;, permitting a logically inconsistent completion record to pass validation. This is acceptable to defer given cross-record invariants are explicitly scoped to Slice 02 (append-only store), but should be closed with an explicit negative-case test once that store exists. | OBSERVATION | angenommen | erledigt: Acceptance test satisfied: &#96;failed&#96;/&#96;stopped&#96; &#96;WorkflowCompletionPayload&#96; records carrying a non-null &#96;final_binding_id&#96; are now rejected at the store boundary (&#96;test_store_rejects_non_success_completion_with_binding&#96;, plus the symmetric scan-time invariant in &#96;_validate_store_invariants(persisted=True)&#96;), matching the negative-case acceptance criterion I set in Slice 01. |
| C-03 | claude | &#96;ArtifactStore.put()&#96; can raise an exception (e.g., a transient error during the mandatory post-publish re-scan/head-cache refresh) after the record bytes are already durably and atomically published to &#96;records_dir&#96;, and this "durable-but-reported-as-failed" retry contract is exercised by one crash-recovery test but is not documented as an explicit API guarantee on &#96;put()&#96;. Slice 03 (dual-write/semantic equality) will depend on exactly this guarantee to decide whether to retry vs. treat an exception as "nothing was written," so an undocumented/underspecified contract here risks a double-write or a lost-write assumption once callers are wired in. | OBSERVATION | offen | offen |
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
