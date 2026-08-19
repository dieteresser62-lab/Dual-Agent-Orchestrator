# Slice 03 – Textadapter, Dual-Write und semantischer Gleichheitsnachweis

**Feature-Branch:** `feature/structured-agent-artifacts`
**GitHub-Status:** nur lokal

## Ziel des Slice

Textadapter, Dual-Write und semantischer Gleichheitsnachweis

## Akzeptanzkriterien

Siehe freigegebenen Arbeitsplan.

## Scope und Nicht-Scope

Erlaubter Scope: `docs/internal/slice-strukturierte-agentenkommunikation-arbeitsplan-03-textadapter-dual-write-und-semantischer-gleichheitsnachweis.md`, `docs/internal/strukturierte-agentenkommunikation-implement-review-542ccc72.md`, `src/artifact_bridge.py`, `src/artifact_store.py`, `src/contracts.py`, `src/orchestrator.py`, `src/validation_matrix.py`, `src/workflow.py`, `tests/test_artifact_bridge.py`, `tests/test_artifact_store.py`, `tests/test_contracts.py`, `tests/test_orchestrator_runtime.py`, `tests/test_validation_matrix.py`, `tests/test_workflow.py`

## Diff-Risiko inklusive Branch- und Statuscheck

Branch `feature/structured-agent-artifacts` war aktiv. Vorbestehende Änderungen aus
früheren Slices und die orchestratorverwalteten Auditblöcke wurden nicht
überschrieben. Das größte Risiko ist ein Fehler zwischen atomarer Veröffentlichung
und Head-Cache-Aktualisierung; der Bridge-Retry behandelt einen Fehler deshalb als
„möglicherweise bereits persistiert“ und gleicht den erneut geladenen Record ab.

## Geplante Tests

Gemäß Arbeitsplan und Orchestrator-Validierungsmatrix.

## Durchgeführte Änderungen

- Verlustfreie Adapter für Codex-, Review-, Finding-, Attestierungs-, Gate- und
  Binding-Aussagen sowie Task-, Plan- und Work-Unit-Domänenobjekte ergänzt.
- Idempotentes Persistieren vor Workflowentscheidungen mit anschließendem Reload
  und semantischem Vergleich umgesetzt; Parser- und Repair-Ablehnungen erzeugen
  Diagnostic-Records.
- Validierungsattestierungen um typisierte Command-Spezifikationen erweitert.
  Strukturierte `argv`-Grenzen bleiben erhalten; historische Shell-Anzeigen bleiben
  explizit opak und werden nicht mit `shlex` rekonstruiert.
- C-03 behoben: Der Durable-but-reported-failed-Vertrag von `ArtifactStore.put()`
  ist dokumentiert und der `persisted=True`-Scanpfad besitzt direkte Negativtests.
- C-04 behoben: Work-Unit-Idempotenzschlüssel enthalten die Rundennummer. Ein
  erneutes Binden derselben Runde bleibt idempotent, während ein Rundenwechsel
  desselben Work-Units eine neue, geordnete Revision persistiert.

## Ausgeführte Validierung mit Ergebnis

Fokussiert lokal: 217 Tests für Bridge, Store, Contracts, Validation Matrix,
Workflow und Orchestrator-Runtime bestanden. Die autoritative Gesamtmatrix wird
entsprechend Rollenvertrag ausschließlich vom Orchestrator ausgeführt.
Für die C-04-Korrektur bestanden zusätzlich der exakte Regressionstest sowie 25
fokussierte Bind-, Bridge- und Store-Tests.

## Abweichungen vom Plan

Keine erfasst.

## Offene Risiken

Der Source-of-Truth-Cutover bleibt ausdrücklich Slice 06 vorbehalten. Slice 03
aktiviert Dual-Write nur für bereits `structured-v1`-gebundene Läufe; historische
ungebundene State-v3-Sitzungen werden nicht still migriert.

## Review-Feedback von Claude

<!-- audit:claude-review:begin -->
### Ereignis 2: Runde 1

- Reviewer: `claude`
- Freigabe: `NO`
- Validierungsbindung: `validation-f321643cbad9`
- Testdateien: `tests/test_artifact_bridge.py`, `tests/test_artifact_store.py`, `tests/test_orchestrator_runtime.py`, `tests/test_validation_matrix.py`
- Prüfdimensionen: Checked dimensions — (1) ValidationCommandSpec/ValidationAttestation invariants (argv-vs-legacy_shell exclusivity, control-character rejection, lossless display-order equality with expected_commands — sound); (2) ArtifactBridge.append idempotency/dedup and its documented "durable-but-reported-failed" recovery path re-scanning &#96;load_chain()&#96; before re-raising (sound, matches closed C-03 contract, verified against &#96;test_bridge_is_idempotent_before_creating_volatile_metadata&#96; and &#96;test_bridge_rejects_idempotency_key_with_different_meaning&#96;); (3) &#96;WorkflowEngine._persist_structured&#96; fail-closed wrapper placement relative to state/history mutation at each call site (gate decision, Codex contract, review contract, diagnostics, validation request/attestation) — persistence consistently precedes the corresponding in-memory history update, so a structured-store failure blocks the step rather than silently diverging state-v3 from the structured mirror, at the cost of re-running expensive validation on structured-store hiccups (accepted, pre-mortemed risk, not re-raised here); (4) &#96;attestation_payload()&#96;'s &#96;records_by_display&#96; dict keyed by command display string — correct for the common case but silently collapses to the *last* matching record if two &#96;command_specs&#96; ever share an identical display string, mis-attributing exit code/outcome; left as a documented residual risk rather than a new finding because a matrix run producing two textually identical commands is not demonstrated as reachable from current callers and the mis-projection would only corrupt the mirrored audit view, not the state-v3 decision path that remains authoritative until Slice 06 cutover.
- Größtes Restrisiko: Largest residual risk: exactly the C-04 mechanism generalized — any future &#96;_persist_structured_baseline&#96;/bridge call site that reuses an idempotency key while allowing payload content to vary across repeated invocations will hard-crash &#96;bind_work_unit&#96;/the workflow step instead of failing closed with a clear diagnostic.
- Realistische Bruchbedingung: Realistic break condition: a Slice-03-bound run whose first implementation round is reviewed and rejected, triggering a second Codex round under the same &#96;work_unit_id&#96;; the very next &#96;bind_work_unit()&#96; call raises &#96;ArtifactBridgeError&#96; and halts the run with no actionable message tying the failure back to the round-number/idempotency-key mismatch.
- Eigene Findings: `C-01`, `C-02`, `C-03`, `C-04`

### Ereignis 4: Runde 2

- Reviewer: `claude`
- Freigabe: `YES`
- Validierungsbindung: `validation-43392b3ca365`
- Testdateien: `tests/test_artifact_bridge.py`, `tests/test_artifact_store.py`, `tests/test_orchestrator_runtime.py`, `tests/test_validation_matrix.py`
- Prüfdimensionen: Checked dimensions — (1) work-unit idempotency-key/round-number fix directly re-derived from source and cross-checked against both the new transition test and the pre-existing same-round idempotency test; (2) confirmed no other &#96;_persist_structured_baseline&#96; branch (task-contract, approved-plan) embeds a per-round-varying field under a round-invariant key, so the fixed mechanism is the only instance of the pattern; (3) scope/allowlist conformance — every touched path (artifact_bridge.py, contracts.py, orchestrator.py, validation_matrix.py, workflow.py, artifact_store.py docstring, and the five listed test files) is inside the exact current slice allowlist, no UNEXPECTED-PATH; (4) fingerprint binding — validation-43392b3ca365 matches CURRENT FINGERPRINT exactly, full suite 744/0
- Größtes Restrisiko: Largest residual risk: &#96;_persist_structured_baseline()&#96; and the commit-binding bridge call remain direct, unwrapped driver calls (not routed through &#96;WorkflowEngine._persist_structured&#96;'s fail-closed try/except), so any future semantic-mismatch bug in those specific call sites would still surface as a raw &#96;ArtifactBridgeError&#96;/&#96;WorkflowExecutionError&#96; out of &#96;bind_work_unit()&#96;/commit binding rather than a uniformly wrapped diagnostic
- Realistische Bruchbedingung: Realistic break condition: a later slice adds another field to &#96;WorkUnitPayload&#96; or the plan/task payloads that legitimately varies within a single logical_id/idempotency_key scope without updating the key accordingly, reintroducing the same class of hard crash on resume.
- Eigene Findings: `C-01`, `C-02`, `C-03`, `C-04`
<!-- audit:claude-review:end -->

## Review-Feedback von Antigravity

<!-- audit:antigravity-review:begin -->
### Ereignis 5: Runde 1

- Reviewer: `antigravity`
- Freigabe: `YES`
- Validierungsbindung: `validation-43392b3ca365`
- Testdateien: `tests/test_artifact_bridge.py`, `tests/test_artifact_store.py`, `tests/test_orchestrator_runtime.py`, `tests/test_validation_matrix.py`
- Prüfdimensionen: Checked dimensions — (1) lossless CommandSpec &amp; ValidationAttestation semantics (typed argv preservation vs opaque legacy shell, control character validation, display-order parity); (2) ArtifactBridge write-through dual-write &amp; idempotency handling (crash recovery across durable-but-reported-failed puts, reload and semantic digest equality check, stable logical IDs); (3) workflow engine integration &amp; fail-closed error boundaries (_persist_structured fail-closed wrapper before state transitions, diagnostic recording on contract errors); (4) verification of C-03 and C-04 resolution (ArtifactStore.put retry contract documentation and scan tests, WorkUnitPayload idempotency key round_number inclusion and round transition regression coverage); (5) strict allowlist and scope compliance.
- Größtes Restrisiko: Largest residual risk: In ProductionWorkflowDriver._persist_structured_baseline, plan_payload derives approved_plan_commit from state.current_slice.start_commit instead of pinning to the immutable initial plan approval commit (e.g., state.slices[0].start_commit); in multi-slice workflows under structured-v1, this will create redundant approved-plan revisions on each slice transition attributing the approved plan commit to the slice start boundary.
- Realistische Bruchbedingung: Realistic break condition: In a multi-slice workflow under structured-v1, a downstream component in Slice 04/05/06 expecting approved-plan to be immutable or to strictly match the initial task plan commit encounters multiple revisions with differing approved_plan_commit hashes corresponding to slice boundaries.
- Eigene Findings: keine
<!-- audit:antigravity-review:end -->

## Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
- `C-01` Antwort 1: **angenommen** — Zero-Dependency-Vertrag wiederhergestellt; der exakte Akzeptanztest besteht.
- `C-02` Antwort 1: **angenommen** — Wie vorgesehen auf Slice 02 und dessen Store-Invarianten vertagt.
- `C-02` Antwort 2: **angenommen** — Failed- und stopped-Completion-Records mit final_binding_id werden durch die Store-Invariante abgewiesen und durch Slice-02-Negativtests abgedeckt.
- `C-03` Antwort 1: **angenommen** — Durable-but-reported-failed-Vertrag dokumentiert und persisted=True-Scanpfad direkt getestet.
- `C-04` Antwort 1: **angenommen** — Work-Unit-Idempotenzschlüssel enthalten nun die Rundennummer; derselbe Round bleibt idempotent, Round-Wechsel erzeugen geordnete Revisionen. Der exakte Regressionstest und 25 fokussierte Tests bestehen.
<!-- audit:codex-responses:end -->

## Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### Ereignis 1: `validation-f321643cbad9`

- Diff-Fingerprint: `f321643cbad9ed549bbaa3f58a58c039ac8e7f50618e77cbf21be35338acbf9f`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: 1 passed; 0 failed; 0 unavailable; 1 required
- Ausgabedigest: `3fe969f56453877f9dead745b8029a74e1c2c1ed8a561a10fc9631f5055b77d5`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| python3 -m pytest tests/ -v | PASS | 0 | ============================= test session starts ==============================<br>platform linux -- Python 3.12.3, pytest-7.4.4, pluggy-1.4.0 -- /usr/bin/python3<br>cachedir: .pytest_cache<br>rootdir: /mnt/c/Users/Diete/Sync/DE_Privat/Rente/ChatGPT CLI/Dual-Agent-Orchestrator<br>configfile: pyproject.toml<br>collecting ... collected 743 items<br><br>tests/test_agent_adapters.py::test_registry_contains_exact_role_identities PASSED [  0%]<br>tests/test_agent_adapters.py::test_codex_command_is_configured_workspace_write_jsonl_and_stdin PASSED [  0%]<br>tests/test_agent_adapters.py::test_codex_prefers_final_message_file_over_jsonl PASSED [  0%]<br>tests/test_agent_adapters.py::test_claude_defaults_are_quota_conscious_and_permissions_are_separate PASSED [  0%]<br>tests/test_agent_adapters.py::test_claude_json_envelope_tracks_usage_and_rejects_permission_denials PASSED [  0%]<br>tests/test_agent_adapters.py::test_normal_claude_review_does_not_expose_bound_harness PASSED [  0%]<br>tests/test_agent_adapters.py::test_capability_sm<br>...[82016 characters omitted]...<br>st_anchor_gate_persists_reset_and_resume_steps PASSED [ 98%]<br>tests/test_workflow_state.py::test_pre_slice11_v3_gate_and_work_unit_shapes_load_with_empty_new_fields PASSED [ 99%]<br>tests/test_workflow_state.py::test_early_slice11_work_unit_shape_loads_without_active_test_evidence PASSED [ 99%]<br>tests/test_workflow_state.py::test_policy_gate_roundtrips_and_resumes_at_same_step PASSED [ 99%]<br>tests/test_workflow_state.py::test_managed_audit_path_roundtrips_and_rejects_unsafe_locations PASSED [ 99%]<br>tests/test_workflow_state.py::test_policy_gate_rejects_fingerprint_bound_reason_and_unsafe_path PASSED [ 99%]<br>tests/test_workflow_state.py::test_plan_time_slice_one_start_commit_cannot_be_rebound_after_resume PASSED [ 99%]<br>tests/test_workflow_state.py::test_in_progress_slice_can_extend_exact_remediation_scope PASSED [ 99%]<br>tests/test_workflow_state.py::test_state_scope_rejects_orchestrator_internal_paths PASSED [100%]<br><br>============================= 743 passed in 25.59s ============================= |

### Ereignis 3: `validation-43392b3ca365`

- Diff-Fingerprint: `43392b3ca365ecccf3358657e0f96122705feb8d42e0db1881dc620c7365c990`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: 1 passed; 0 failed; 0 unavailable; 1 required
- Ausgabedigest: `15c470bd1ae5d079ac50e61b642cf1bf218372a875748f9ef487f19251667252`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| python3 -m pytest tests/ -v | PASS | 0 | ============================= test session starts ==============================<br>platform linux -- Python 3.12.3, pytest-7.4.4, pluggy-1.4.0 -- /usr/bin/python3<br>cachedir: .pytest_cache<br>rootdir: /mnt/c/Users/Diete/Sync/DE_Privat/Rente/ChatGPT CLI/Dual-Agent-Orchestrator<br>configfile: pyproject.toml<br>collecting ... collected 744 items<br><br>tests/test_agent_adapters.py::test_registry_contains_exact_role_identities PASSED [  0%]<br>tests/test_agent_adapters.py::test_codex_command_is_configured_workspace_write_jsonl_and_stdin PASSED [  0%]<br>tests/test_agent_adapters.py::test_codex_prefers_final_message_file_over_jsonl PASSED [  0%]<br>tests/test_agent_adapters.py::test_claude_defaults_are_quota_conscious_and_permissions_are_separate PASSED [  0%]<br>tests/test_agent_adapters.py::test_claude_json_envelope_tracks_usage_and_rejects_permission_denials PASSED [  0%]<br>tests/test_agent_adapters.py::test_normal_claude_review_does_not_expose_bound_harness PASSED [  0%]<br>tests/test_agent_adapters.py::test_capability_sm<br>...[82140 characters omitted]...<br>st_anchor_gate_persists_reset_and_resume_steps PASSED [ 98%]<br>tests/test_workflow_state.py::test_pre_slice11_v3_gate_and_work_unit_shapes_load_with_empty_new_fields PASSED [ 99%]<br>tests/test_workflow_state.py::test_early_slice11_work_unit_shape_loads_without_active_test_evidence PASSED [ 99%]<br>tests/test_workflow_state.py::test_policy_gate_roundtrips_and_resumes_at_same_step PASSED [ 99%]<br>tests/test_workflow_state.py::test_managed_audit_path_roundtrips_and_rejects_unsafe_locations PASSED [ 99%]<br>tests/test_workflow_state.py::test_policy_gate_rejects_fingerprint_bound_reason_and_unsafe_path PASSED [ 99%]<br>tests/test_workflow_state.py::test_plan_time_slice_one_start_commit_cannot_be_rebound_after_resume PASSED [ 99%]<br>tests/test_workflow_state.py::test_in_progress_slice_can_extend_exact_remediation_scope PASSED [ 99%]<br>tests/test_workflow_state.py::test_state_scope_rejects_orchestrator_internal_paths PASSED [100%]<br><br>============================= 744 passed in 23.64s ============================= |
<!-- audit:validation-attestation:end -->

## Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
- Teständerungsfreigabe: nicht erfasst.
- Pre-Mortems:
  - Ereignis 4: In three months, the most likely failure is a later slice extending &#96;_persist_structured_baseline()&#96; (or an equivalent direct, unwrapped driver→bridge call site) with a new payload field that varies within the same logical scope without threading it into the idempotency key, reproducing the C-04 class of crash — because the fix addressed the one demonstrated instance precisely rather than establishing a structural guard (e.g., a shared helper or invariant test) that would catch the general pattern automatically.
  - Ereignis 5: In three months, the most likely failure cause is a future slice during the structured cutover introducing a new payload field that mutates across workflow lifecycle transitions (such as dynamic remediation scope extensions or user gate overrides) while retaining a static or partially-keyed idempotency key, causing ArtifactBridge._assert_equal to trip an unhandled ArtifactBridgeError upon rebind/resume.
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
<!-- audit:findings:end -->

## Entscheidungstabelle

<!-- audit:decision-table:begin -->
| ID | Quelle | Finding | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|---|
| C-01 | claude | Slice 01 adds a new runtime dependency (&#96;jsonschema&gt;=4.18,&lt;5&#96;) to &#96;pyproject.toml&#96; for offline JSON-Schema validation in &#96;src/artifact_models.py&#96;, but the pre-existing invariant test &#96;tests/test_cli.py::test_declared_python_floor_matches_standard_library_toml_requirement&#96; still asserts &#96;dependencies == []&#96;, so the bound orchestrator attestation for fingerprint f30b04cebf94756b25051f21bd5590a80075607da1c8e34685f17eb44b9ea028 is FAIL (1 failed / 713 passed). The slice must not merge an unreconciled dependency-policy break: either reconcile the "stdlib-only/portable" invariant explicitly (with the guard test updated in the same, reviewed change) or replace &#96;jsonschema&#96; with a stdlib-only Draft-2020-12 validator so the declared zero-dependency contract holds. | BLOCKER | angenommen | erledigt: Runtime &#96;jsonschema&#96; dependency removed; &#96;src/artifact_models.py&#96; now implements an offline, self-checking Draft-2020-12 subset validator with no third-party dependency; the bound attestation for fingerprint 6ce9f6f31673ac0333e7da8a4752ab18805ac64c803e298640eab9a2d0a60aba shows both the full suite (714 passed/0 failed) and the exact previously-failing guard test (&#96;tests/test_cli.py::test_declared_python_floor_matches_standard_library_toml_requirement&#96;) passing, restoring the zero-dependency invariant. |
| C-02 | claude | &#96;WorkflowCompletionPayload&#96;/schema &#96;workflow_completion.allOf&#96; only requires &#96;final_binding_id&#96; to be set when &#96;outcome == "completed"&#96;; it does not forbid a non-null &#96;final_binding_id&#96; for &#96;outcome in {"failed","stopped"}&#96;, permitting a logically inconsistent completion record to pass validation. This is acceptable to defer given cross-record invariants are explicitly scoped to Slice 02 (append-only store), but should be closed with an explicit negative-case test once that store exists. | OBSERVATION | angenommen | erledigt: Acceptance test satisfied: &#96;failed&#96;/&#96;stopped&#96; &#96;WorkflowCompletionPayload&#96; records carrying a non-null &#96;final_binding_id&#96; are now rejected at the store boundary (&#96;test_store_rejects_non_success_completion_with_binding&#96;, plus the symmetric scan-time invariant in &#96;_validate_store_invariants(persisted=True)&#96;), matching the negative-case acceptance criterion I set in Slice 01. |
| C-03 | claude | &#96;ArtifactStore.put()&#96; can raise an exception (e.g., a transient error during the mandatory post-publish re-scan/head-cache refresh) after the record bytes are already durably and atomically published to &#96;records_dir&#96;, and this "durable-but-reported-as-failed" retry contract is exercised by one crash-recovery test but is not documented as an explicit API guarantee on &#96;put()&#96;. Slice 03 (dual-write/semantic equality) will depend on exactly this guarantee to decide whether to retry vs. treat an exception as "nothing was written," so an undocumented/underspecified contract here risks a double-write or a lost-write assumption once callers are wired in. | OBSERVATION | angenommen | erledigt: Acceptance criteria fully met in this diff: &#96;ArtifactStore.put()&#96; now carries an explicit docstring contract ("An exception does not prove that nothing was written... callers must treat every exception as possibly already persisted and may safely retry the identical logical call"), and &#96;tests/test_artifact_store.py::test_scan_rejects_persisted_non_success_completion_with_binding&#96; directly exercises the &#96;persisted=True&#96; scan branch via a hand-written envelope that bypasses &#96;put()&#96;, matching my Slice-02 acceptance test verbatim. Closing as originally reported by me. |
| C-04 | claude | &#96;ProductionWorkflowDriver._persist_structured_baseline()&#96; (invoked unconditionally, uncaught, from every &#96;bind_work_unit()&#96; call — i.e. every rebind/resume within a work unit) derives the &#96;WorkUnitPayload&#96; idempotency key solely from &#96;unit.work_unit_id&#96; (&#96;idempotency_key=f"work-unit:{unit.work_unit_id}"&#96;), while the payload it constructs embeds &#96;round_number=unit.round_number&#96;. Elsewhere in the same diff (&#96;persist_codex_contract&#96;'s &#96;logical=f"agent-{unit.work_unit_id}-{state.current_step.value}-{unit.round_number}"&#96;, and &#96;persist_review_contract&#96;'s explicit &#96;round_number&#96; parameter used in its own idempotency key) the codebase treats &#96;round_number&#96; as a value that legitimately changes across multiple rounds within one &#96;work_unit_id&#96; (repair/retry/second-review cycles). The first &#96;bind_work_unit()&#96; call for round 1 durably publishes &#96;WorkUnitPayload(round_number=1,...)&#96; under key &#96;work-unit:&lt;id&gt;&#96;. As soon as the same work unit advances to round 2 and &#96;bind_work_unit()&#96; fires again, &#96;ArtifactBridge.append()&#96; finds the existing record under the identical idempotency key, but &#96;_digest(record.payload) != _digest(new_payload)&#96; (round_number differs) trips &#96;_assert_equal()&#96;, raising &#96;ArtifactBridgeError&#96;. &#96;_persist_structured_baseline()&#96; is called directly, not through &#96;WorkflowEngine._persist_structured&#96;'s try/except-to-&#96;WorkflowExecutionError&#96; wrapper, so this exception is not fail-closed gracefully — it propagates as a raw, undocumented crash out of &#96;bind_work_unit()&#96;, which is the very first thing the driver does on every resume. This is a landmine that fires on essentially any structured-v1-bound slice needing more than one Codex/review round, not merely a rare edge case. The only existing coverage (&#96;test_structured_bind_persists_contract_and_active_work_unit_once&#96;) calls &#96;bind_work_unit(state)&#96; twice with the SAME unchanged &#96;state&#96; object, so it cannot and does not exercise a round-number transition within one &#96;work_unit_id&#96;; the defect is unguarded by any test. | BLOCKER | angenommen | erledigt: Idempotency key for WorkUnitPayload now includes round_number (&#96;work-unit:&lt;id&gt;:round:&lt;n&gt;&#96;), so same-round re-binds stay idempotent and round transitions persist as a new ordered revision instead of raising ArtifactBridgeError. The exact acceptance test (test_structured_bind_survives_round_number_increase_within_same_work_unit) is present, exercises exactly the previously-broken transition, and passes in the bound 744-test attestation for the current fingerprint. Closing as originally reported by me. |
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
