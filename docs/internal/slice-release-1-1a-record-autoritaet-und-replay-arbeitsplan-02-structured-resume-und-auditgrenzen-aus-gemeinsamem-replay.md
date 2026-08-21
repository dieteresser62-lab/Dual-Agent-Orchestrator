# Slice 02 – Structured-Resume- und Auditgrenzen aus gemeinsamem Replay

**Feature-Branch:** `feature/orchestrator-stabilization-1-1a`
**GitHub-Status:** nur lokal

## Ziel des Slice

Structured-Resume- und Auditgrenzen aus gemeinsamem Replay

## Akzeptanzkriterien

Siehe freigegebenen Arbeitsplan.

## Scope und Nicht-Scope

Erlaubter Scope: `docs/internal/release-1-1a-record-autoritaet-und-replay-implement-review-d9c35a04.md`, `docs/internal/slice-release-1-1a-record-autoritaet-und-replay-arbeitsplan-02-structured-resume-und-auditgrenzen-aus-gemeinsamem-replay.md`, `src/artifact_migration.py`, `src/audit_trail.py`, `src/orchestrator.py`, `tests/test_artifact_migration.py`, `tests/test_audit_trail.py`, `tests/test_orchestrator_runtime.py`, `tests/test_structured_artifact_regressions.py`

## Diff-Risiko inklusive Branch- und Statuscheck

Der persistierte Zielbranch `feature/orchestrator-stabilization-1-1a` war vor
der Umsetzung aktiv. Der kanonische Diff blieb auf der Slice-Allowlist; der
bereits vom Orchestrator geänderte Gesamtauditbericht wurde nicht bearbeitet.
Das wesentliche Risiko ist eine zu frühe Mirrorreparatur. Diese Umsetzung
klassifiziert deshalb nur einen strikten, ausschließlich im State vorhandenen
Faktenüberhang als `MIRROR-AHEAD`; gemischte oder record-voraus liegende
Abweichungen bleiben `MIRROR-AMBIGUOUS` beziehungsweise behalten den konkreten
Record-Diagnosecode.

## Geplante Tests

Gemäß Arbeitsplan und Orchestrator-Validierungsmatrix.

## Durchgeführte Änderungen

- `resolve_resume_state()` zweigt Legacy weiterhin vor jedem Storezugriff ab
  und erzeugt für Structured-Runs genau ein gemeinsames, unveränderliches
  `ArtifactReplayResult`. Recorddiagnosen werden maschinenlesbar bis zur
  Resumegrenze weitergereicht; Mirrorüberhänge und mehrdeutige Divergenzen
  erhalten getrennte stabile Codes.
- Side-Effect-Prüfung und Auditprojektion verwenden Records, Head und Digest
  aus diesem einen Replayergebnis. Ein zweites Laden oder Reduzieren der Kette
  an denselben Grenzen entfällt.
- Die strukturierten Auditadapter akzeptieren das bereits geprüfte
  Replayergebnis, leiten daraus die vollständige Run- oder Sliceprojektion ab
  und behalten atomare sowie bytegleiche No-op-Writes bei.
- Structured-Crash-Replay-Fixtures enthalten nun die autoritativen
  Work-Unit-Records für referenzierte Finalreview-Aktivität und erfüllen damit
  dieselbe fail-closed Referenzordnung wie produktive Ketten.

## Ausgeführte Validierung mit Ergebnis

Fokussiert ausgeführt: Migration-, Audit-, Orchestrator-Runtime- und
Structured-Regressionstests. Der kombinierte Lauf erreichte 153 erfolgreiche
Tests und deckte eine unvollständige Finalreview-Fixture auf; nach Ergänzung des
fehlenden Work-Unit-Records bestand der zuvor betroffene Crash-Replay-Test sowie
alle neu hinzugefügten Replay-/Legacy-/Auditregressionen. Der abschließende
fokussierte Gesamtlauf bestand mit 154 Tests. Die autoritative Validierungsmatrix
wird ausschließlich durch den Orchestrator ausgeführt und in den verwalteten
Blöcken projiziert.

## Abweichungen vom Plan

Keine fachliche Abweichung. Es wurden keine Transition-IDs, Recordmodelle oder
Legacy-Semantiken erweitert.

## Offene Risiken

Der vollständige Abbau aller unabhängigen State-Schreibentscheidungen bleibt
wie geplant außerhalb von 1.1A1. Innerhalb dieses Slice bleiben nicht eindeutig
aus Records ableitbare Divergenzen fail-closed; weitere Reviewer-Risiken werden
im strukturierten Findings-Lebenszyklus geführt.

## Review-Feedback von Claude

<!-- audit:claude-review:begin -->
### Ereignis 2: Runde 1

- Reviewer: `claude`
- Freigabe: `YES`
- Validierungsbindung: `validation-a28d02e70bb5`
- Testdateien: `tests/test_artifact_migration.py`, `tests/test_audit_trail.py`, `tests/test_structured_artifact_regressions.py`
- Eigene Findings: `C-01`, `C-02`, `C-03`, `C-04`, `C-05`, `C-06`

<!-- artifact-records:claude-review:begin -->
Semantischer Record-Digest: `6d67cbcad83638a3a2e04cf7763d6fdbe1f43fed69f2ce91d6a84c30c435c7db`

- 27. `ar1-c4acbc7bec64bce735f9adf277491aa6df0475e4aeaece500b8568c9a9abd5e1`: `approved`; Work-Unit `3`; Findings `C-01`, `C-02`, `C-03`, `C-04`, `C-05`, `C-06`; Fingerprint `a28d02e70bb5b5b730dea1e4c6103dab7bff847208251f5b53c08e298fa62f9a`
<!-- artifact-records:claude-review:end -->
<!-- audit:claude-review:end -->

## Review-Feedback von Antigravity

<!-- audit:antigravity-review:begin -->
### Ereignis 3: Runde 1

- Reviewer: `antigravity`
- Freigabe: `YES`
- Validierungsbindung: `validation-a28d02e70bb5`
- Testdateien: `tests/test_artifact_migration.py`, `tests/test_audit_trail.py`, `tests/test_structured_artifact_regressions.py`
- Prüfdimensionen: single replay resolution during resume and external side-effect checks, structured mirror divergence classification (MIRROR_AHEAD vs MIRROR_AMBIGUOUS), projection adapter replay ingestion and byte-equal idempotency, strict reference ordering compliance in crash-recovery fixtures
- Größtes Restrisiko: unexercised MIRROR_AMBIGUOUS mismatch paths across non-gate resume branches allowing subtle classification divergence during future refactoring
- Realistische Bruchbedingung: a caller invoking resolve_resume_state with partially divergent mirror facts expecting MIRROR_AMBIGUOUS but receiving MIRROR_AHEAD due to asymmetric difference logic in non-gate branches
- Eigene Findings: keine

<!-- artifact-records:antigravity-review:begin -->
Semantischer Record-Digest: `6d67cbcad83638a3a2e04cf7763d6fdbe1f43fed69f2ce91d6a84c30c435c7db`

- 32. `ar1-7ff05c9b3f1d2e64198f9d55d8ee461e44359adb8f87a57f50bfc1983920c309`: `approved`; Work-Unit `3`; Findings `C-01`, `C-02`, `C-03`, `C-04`, `C-05`, `C-06`; Fingerprint `a28d02e70bb5b5b730dea1e4c6103dab7bff847208251f5b53c08e298fa62f9a`
<!-- artifact-records:antigravity-review:end -->
<!-- audit:antigravity-review:end -->

## Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
- `C-01` Antwort 1: **angenommen** — Work-Unit-Referenzen werden ab dem ersten persistierten Implementierungs-Work-Unit-Record verpflichtend geprüft; schema-konforme Planaktivitäten davor bleiben zulässig.
- `C-01` Antwort 2: **angenommen** — Planaktivitäten vor dem ersten Work-Unit-Record bleiben zulässig; danach werden Work-Unit-Referenzen strikt validiert.
- `C-02` Antwort 1: **angenommen** — subset() prüft nun Record-ID und vollständige Wertgleichheit statt Python-Objektidentität; re-deserialisierte identische Records werden akzeptiert, manipulierte abgelehnt.
- `C-02` Antwort 2: **angenommen** — subset() verwendet Record-ID und vollständige Wertgleichheit statt Objektidentität.
- `C-03` Antwort 1: **angenommen** — Der erwartete Cachefortschritt wird explizit als Parameter weitergegeben; transienter Instanzzustand wurde entfernt und die Single-Writer-Grenze dokumentiert.
- `C-03` Antwort 2: **angenommen** — Der erwartete Cachefortschritt wird ohne transienten Instanzzustand explizit übergeben.
- `C-04` Antwort 1: **angenommen** — Referenzierte Work-Unit-Records müssen nun vor dem verweisenden Record stehen; ein Vorwärtsreferenz-Regressionstest bestätigt das fail-closed Verhalten.
- `C-05` Antwort 1: **angenommen** — Die erste persistierte Revision begründet nun die Work-Unit-Autorität; spätere Revisionen können die Referenzprüfgrenze nicht mehr verschieben, und ein Regressionstest bestätigt das fail-closed Verhalten zwischen Revisionen.

<!-- artifact-records:codex-responses:begin -->
Semantischer Record-Digest: `6d67cbcad83638a3a2e04cf7763d6fdbe1f43fed69f2ce91d6a84c30c435c7db`

- 6. `ar1-66f78f83665232d8b6b117c46c3a902553a7b1809ab31976574d78444cfed481`: `C-01` `responded` durch `codex`; `BLOCKER` / `open` — ACCEPTED: Work-Unit-Referenzen werden ab dem ersten persistierten Implementierungs-Work-Unit-Record verpflichtend geprüft; schema-konforme Planaktivitäten davor bleiben zulässig.
- 7. `ar1-14b73dfb721961bd5085263050b092509dc537755345bbdb33b12c29919ff288`: `C-02` `responded` durch `codex`; `OBSERVATION` / `open` — ACCEPTED: subset() prüft nun Record-ID und vollständige Wertgleichheit statt Python-Objektidentität; re-deserialisierte identische Records werden akzeptiert, manipulierte abgelehnt.
- 8. `ar1-1e63714bfe1ccef4334fc216685e218e860c8b98c1f1a479a97a0114e8bbccd0`: `C-03` `responded` durch `codex`; `OBSERVATION` / `open` — ACCEPTED: Der erwartete Cachefortschritt wird explizit als Parameter weitergegeben; transienter Instanzzustand wurde entfernt und die Single-Writer-Grenze dokumentiert.
- 10. `ar1-fd74ab29d8938502b17d4e5806061f50e4f0aa779c0a36c7873db9efbdae97e3`: `C-01` `responded` durch `codex`; `BLOCKER` / `open` — ACCEPTED: Planaktivitäten vor dem ersten Work-Unit-Record bleiben zulässig; danach werden Work-Unit-Referenzen strikt validiert.
- 11. `ar1-dd38b1464da143b8f11abbff9fe0b850b982c01fc80f95e5b0a860f070fe144d`: `C-02` `responded` durch `codex`; `OBSERVATION` / `open` — ACCEPTED: subset() verwendet Record-ID und vollständige Wertgleichheit statt Objektidentität.
- 12. `ar1-b45516e21771680c9e3a68679bc4cfcfa46a704f77611976fff5bd0ec047b80b`: `C-03` `responded` durch `codex`; `OBSERVATION` / `open` — ACCEPTED: Der erwartete Cachefortschritt wird ohne transienten Instanzzustand explizit übergeben.
- 13. `ar1-2485808dcc6a723757d80ed4877bbe3630ba307d5692ef053834be2954a45e78`: `C-04` `responded` durch `codex`; `BLOCKER` / `open` — ACCEPTED: Referenzierte Work-Unit-Records müssen nun vor dem verweisenden Record stehen; ein Vorwärtsreferenz-Regressionstest bestätigt das fail-closed Verhalten.
- 19. `ar1-94d448e3348bec066292750fe2ffc88ffc391b8acb8de0a2a5f63541777ad771`: `C-05` `responded` durch `codex`; `BLOCKER` / `open` — ACCEPTED: Die erste persistierte Revision begründet nun die Work-Unit-Autorität; spätere Revisionen können die Referenzprüfgrenze nicht mehr verschieben, und ein Regressionstest bestätigt das fail-closed Verhalten zwischen Revisionen.
<!-- artifact-records:codex-responses:end -->
<!-- audit:codex-responses:end -->

## Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### Ereignis 1: `validation-a28d02e70bb5`

- Diff-Fingerprint: `a28d02e70bb5b5b730dea1e4c6103dab7bff847208251f5b53c08e298fa62f9a`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: 1 passed; 0 failed; 0 unavailable; 1 required
- Ausgabedigest: `9407a1336532d19fa37ab8515571848f14d3e91e29551f36934fa53725994e23`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| python3 -m pytest tests/ -v | PASS | 0 | ============================= test session starts ==============================<br>platform linux -- Python 3.12.3, pytest-7.4.4, pluggy-1.4.0 -- /usr/bin/python3<br>cachedir: .pytest_cache<br>rootdir: /mnt/c/Users/Diete/Sync/DE_Privat/Rente/ChatGPT CLI/Dual-Agent-Orchestrator<br>configfile: pyproject.toml<br>collecting ... collected 932 items<br><br>tests/test_agent_adapters.py::test_registry_contains_exact_role_identities PASSED [  0%]<br>tests/test_agent_adapters.py::test_codex_command_is_configured_workspace_write_jsonl_and_stdin PASSED [  0%]<br>tests/test_agent_adapters.py::test_prepared_codex_input_uses_the_exact_stdin_prompt PASSED [  0%]<br>tests/test_agent_adapters.py::test_codex_prefers_final_message_file_over_jsonl PASSED [  0%]<br>tests/test_agent_adapters.py::test_claude_defaults_are_quota_conscious_and_permissions_are_separate PASSED [  0%]<br>tests/test_agent_adapters.py::test_prepared_claude_input_is_lossless_and_includes_every_model_channel PASSED [  0%]<br>tests/test_agent_adapters.py::test_prepared_clau<br>...[106455 characters omitted]...<br>_pre_slice11_v3_gate_and_work_unit_shapes_load_with_empty_new_fields PASSED [ 99%]<br>tests/test_workflow_state.py::test_early_slice11_work_unit_shape_loads_without_active_test_evidence PASSED [ 99%]<br>tests/test_workflow_state.py::test_policy_gate_roundtrips_and_resumes_at_same_step PASSED [ 99%]<br>tests/test_workflow_state.py::test_managed_audit_path_roundtrips_and_rejects_unsafe_locations PASSED [ 99%]<br>tests/test_workflow_state.py::test_policy_gate_rejects_fingerprint_bound_reason_and_unsafe_path PASSED [ 99%]<br>tests/test_workflow_state.py::test_unexpected_file_user_gate_decision_roundtrips PASSED [ 99%]<br>tests/test_workflow_state.py::test_plan_time_slice_one_start_commit_cannot_be_rebound_after_resume PASSED [ 99%]<br>tests/test_workflow_state.py::test_in_progress_slice_can_extend_exact_remediation_scope PASSED [ 99%]<br>tests/test_workflow_state.py::test_state_scope_rejects_orchestrator_internal_paths PASSED [100%]<br><br>======================= 932 passed in 111.39s (0:01:51) ======================== |

<!-- artifact-records:validation-attestation:begin -->
Semantischer Record-Digest: `6d67cbcad83638a3a2e04cf7763d6fdbe1f43fed69f2ce91d6a84c30c435c7db`

- 22. `ar1-635fb202748e33bba1d0c4ca2858944bcf22d1ef90df49829b274b84b36df9f7`: Providerinput `codex/codex_implementation` = `allowed`; Zeichen `25178/4000000`, Bytes `25221/16000000`; Input `aaa053b8f867ae94fce9c38e8753d7f6ee6eb407a26ccb0f4d51c6d21a19b9e4`, Policy `8cff2b572a65674a0b31c2e2751cdc76fa839112b05e38b09bd021a02284bc83`, Übergang `cbf99d2b5985d26db30432bb1327a24155cec72c907baaeac8a87ee84c1d3d03`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, größte Komponente `stdin_prompt`; Komponenten `stdin_prompt=25178/25221`
- 24. `ar1-74f6614f2d7d9dbd487ea8008ecd2c3594a76827c2ab767d481cd19cdc51e646`: Anforderung durch `orchestrator`: `argv` [`python3`, `-m`, `pytest`, `tests/`, `-v`]
- 25. `ar1-cb0b228ccea2e1b230a2452d7f3d8b264687ea4d61fcf28293f5a5f08538595a`: Attestierung durch `orchestrator`; Fingerprint `a28d02e70bb5b5b730dea1e4c6103dab7bff847208251f5b53c08e298fa62f9a`
  - `pass` / Exit `0` / Output `fc7cf73878336c987214e3fff28b6148cc38581c056774534d7609eda11ff682`: `argv` [`python3`, `-m`, `pytest`, `tests/`, `-v`]
- 26. `ar1-e91cc2111129233cd1dd99fe908e9694d4cec5e7cd36dd54c765042ffae532cf`: Providerinput `claude/claude_slice_review` = `allowed`; Zeichen `117133/4000000`, Bytes `117419/16000000`; Input `e2781a072de29942c7bd54c5a3b555a07c811885db259c7cd06b1cdc857c2212`, Policy `8cff2b572a65674a0b31c2e2751cdc76fa839112b05e38b09bd021a02284bc83`, Übergang `9ee992a24e1af69336090c61c559e7b787d5b9de86ce3f2a78c2d680f31eaaa7`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, größte Komponente `packet_chunk_003`; Komponenten `packet_chunk_001=23887/23930, packet_chunk_002=20748/20819, packet_chunk_003=23967/23983, packet_chunk_004=23084/23159, packet_chunk_005=23444/23525, packet_manifest=855/855, system_policy=660/660, response_schema=146/146, start_directive=342/342`
- 29. `ar1-1dce23f86767309a0b406f137d2a61fe42196e20f90fc899ce27c40b1c73377a`: Providerinput `antigravity/antigravity_slice_review` = `allowed`; Zeichen `177800/4000000`, Bytes `178266/16000000`; Input `1e974aef1575c91b88b06bbf13a0bd13e0907621ce401f87cca3ddd1dc0c7574`, Policy `8cff2b572a65674a0b31c2e2751cdc76fa839112b05e38b09bd021a02284bc83`, Übergang `70ac7ee1607feefb0ec58b652f91f860f2541f01112af41749f2fb1cca972f86`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, größte Komponente `prompt_file`; Komponenten `prompt_file=177141/177607, response_schema=146/146, start_directive=513/513`
- 31. `ar1-32b6e91d89d20fae549d163e5dd1d070b4e977f143eda6f37fbb8400f60887fe`: Providerinput `antigravity/antigravity_slice_review` = `allowed`; Zeichen `177800/4000000`, Bytes `178266/16000000`; Input `1e974aef1575c91b88b06bbf13a0bd13e0907621ce401f87cca3ddd1dc0c7574`, Policy `8cff2b572a65674a0b31c2e2751cdc76fa839112b05e38b09bd021a02284bc83`, Übergang `08af135bcd35f705c18e59c3c4d6bdc63a2ad66ef27c15884b7d1c9fff0d67a1`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, größte Komponente `prompt_file`; Komponenten `prompt_file=177141/177607, response_schema=146/146, start_directive=513/513`
<!-- artifact-records:validation-attestation:end -->
<!-- audit:validation-attestation:end -->

## Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
- Teständerungsfreigabe: nicht erfasst.
- Pre-Mortems:
  - Ereignis 2: The most likely failure in three months is a new payload type or a new resume-mismatch branch being added without being routed through the now-shared &#96;resolve_resume_state&#96;/&#96;replay_artifacts&#96; single-computation path — i.e., someone reintroduces a second, raw &#96;ArtifactStore(...).load_chain()&#96; call (exactly the pre-existing bug this Slice fixed) for a new feature, silently bypassing cross-record validation again because enforcement is wired per call site rather than structurally guaranteed by the type system.
  - Ereignis 3: A future orchestrator refactoring or new payload type bypasses resolve_resume_state().replay_result by instantiating an ArtifactAuditProjection directly from an unvalidated store load, silently bypassing cross-record replay validation.

<!-- artifact-records:test-approval-premortem:begin -->
Semantischer Record-Digest: `6d67cbcad83638a3a2e04cf7763d6fdbe1f43fed69f2ce91d6a84c30c435c7db`

Keine strukturierten Gates.
<!-- artifact-records:test-approval-premortem:end -->
<!-- audit:test-approval-premortem:end -->

## Findings-Lebenszyklus

<!-- audit:findings:begin -->
### `C-01` — `CLOSED`

- Quelle: `claude`; Runde 1
- Klasse: `BLOCKER`
- Finding: The bound validation attestation for fingerprint 02b647bc6f8b329330c4e343536d7741755c23c3046e34c0ec3bd5abe40580f7 is FAIL: &#96;python3 -m pytest tests/ -v&#96; reports 5 failures in tests/test_orchestrator_runtime.py, all raised as RECORD-REFERENCE-MISSING ("work unit '&lt;id&gt;' is not present") from the new src/artifact_replay.py &#96;_validate_payload_references&#96; cross-record check. This check requires every work_unit_id referenced by AgentResultPayload/DiagnosticPayload/ReviewPayload/ProviderInputMeasurementPayload/FinalReviewPreflightPayload to resolve via logical_id.removeprefix("work-unit-") to an already-chained WorkUnitPayload/CorrectionWorkUnitPayload record. The orchestrator-built chains exercised by these tests do not satisfy that stricter shape, so the shared replay kernel now rejects data the orchestrator itself produces. src/orchestrator.py is outside this Slice's allowlist and no red-state follow-up Slice is authorized, so this is an unresolved regression, not pre-existing noise.
- Akzeptanztest: VALIDATE: ["python3","-m","pytest","tests/test_orchestrator_runtime.py::test_inbox_workflow_creates_slice_audit_at_implementation_start_and_finalizes_overall","tests/test_orchestrator_runtime.py::test_plan_only_uses_internal_plan_validation_and_commits_no_product_code","tests/test_orchestrator_runtime.py::test_plan_only_repairs_handoff_contract_before_review","tests/test_orchestrator_runtime.py::test_generated_implementation_handoff_skips_second_plan_review","tests/test_orchestrator_runtime.py::test_completed_plan_resume_retries_failed_handoff_without_agents","-v"]
- Statusbegründung: Fixed via the &#96;first_work_unit_position&#96; gate: &#96;_validate_payload_references&#96; now only enforces work-unit reference resolution for records positioned after the first WorkUnitPayload/CorrectionWorkUnitPayload record; plan-time activity before it is exempt (test_replay_allows_plan_activity_before_the_first_work_unit_record). The bound attestation validation-3f52c99e7930 shows all five previously-failing tests/test_orchestrator_runtime.py cases now PASS.

### `C-02` — `CLOSED`

- Quelle: `claude`; Runde 1
- Klasse: `OBSERVATION`
- Finding: ArtifactReplayResult.subset() matches records by Python object identity (id()) rather than value/record_id equality; any future caller passing a structurally-identical but distinct ArtifactRecord instance (e.g. re-deserialized) will incorrectly hit RECORD_UNKNOWN instead of being accepted as a valid subsequence.
- Akzeptanztest: Add a subset() unit test that reconstructs an equal-but-distinct ArtifactRecord (same field values, new object) and asserts either explicit rejection-by-design is documented or membership is defined by record_id rather than identity.
- Statusbegründung: &#96;ArtifactReplayResult.subset()&#96; now matches by &#96;record_id&#96; plus full value equality against the accepted replay, not object identity; test_subset_accepts_equal_redeserialized_records_but_rejects_same_id_tampering confirms a re-deserialized-but-equal record is accepted and a same-id tampered record is rejected with RECORD_UNKNOWN.

### `C-03` — `CLOSED`

- Quelle: `claude`; Runde 1
- Klasse: `OBSERVATION`
- Finding: ArtifactStore._refresh_cache_with_context threads &#96;expected_cache_chain&#96; through a transient instance attribute &#96;self._expected_cache_chain&#96; (set/deleted around the call) rather than an explicit parameter, so concurrent &#96;put()&#96; calls on a shared ArtifactStore instance could interleave and suppress a legitimate stale-cache warning.
- Akzeptanztest: Add a concurrency-oriented unit test (or explicit single-writer docstring guarantee) confirming _refresh_cache_with_context is safe only for a single in-flight put() per store instance, and document/enforce that invariant.
- Statusbegründung: &#96;expected_cache_chain&#96; is now an explicit parameter threaded through &#96;_load_chain&#96;/&#96;_refresh_cache_with_context&#96;/&#96;_refresh_head_cache&#96;, not a transient instance attribute. test_cache_refresh_passes_expected_progress_without_instance_state asserts &#96;not hasattr(store, "_expected_cache_chain")&#96;; the single-writer-per-run boundary is documented in &#96;put()&#96;'s docstring.

### `C-04` — `CLOSED`

- Quelle: `claude`; Runde 2
- Klasse: `BLOCKER`
- Finding: In &#96;_validate_payload_references&#96;, the work-unit-id reference check for &#96;AgentResultPayload&#96;/&#96;DiagnosticPayload&#96;/&#96;ReviewPayload&#96;/&#96;ProviderInputMeasurementPayload&#96;/&#96;FinalReviewPreflightPayload&#96; only tests &#96;payload.work_unit_id not in work_units&#96;, where &#96;work_units&#96; is built by scanning the *entire* chain regardless of position. Every other cross-record reference in the same function (&#96;BindingPayload&#96;→attestation/approvals, &#96;WorkflowCompletionPayload&#96;→final binding, &#96;FinalReviewPreflightPayload&#96;→measurement) explicitly requires &#96;positions[referenced] &lt; positions[record]&#96; ("already exists before this point"); this branch has no such ordering check. A corrupted, reordered, or maliciously constructed chain could contain a record at position N referencing work-unit "5" while the &#96;WorkUnitPayload&#96; for "5" only appears at position N+k, and replay would still accept it as valid — directly contradicting the stated design intent in this very fix ("resolve … to an already-chained WorkUnitPayload … record") and the repo's fail-closed record-authority policy (resume must reject invented/out-of-order facts, not just missing ones). This is a real gap in the newly hardened validator, not pre-existing noise, and is cheaply fixable by adding the same &#96;positions[work_units[...].record_id] &lt; positions[record.record_id]&#96; check used elsewhere in the function.
- Akzeptanztest: VALIDATE: ["python3","-m","pytest","tests/test_artifact_replay.py","-v"]
- Statusbegründung: The work-unit branch now requires &#96;positions[work_units[id].record_id] &lt; positions[record.record_id]&#96;, mirroring the ordering discipline used for BindingPayload/WorkflowCompletionPayload/FinalReviewPreflightPayload. test_replay_rejects_activity_that_references_a_later_work_unit is a direct forward-reference regression and passes under the bound attestation (tests/test_artifact_replay.py -v, 9/9 PASS).

### `C-05` — `CLOSED`

- Quelle: `claude`; Runde 3
- Klasse: `BLOCKER`
- Finding: In &#96;_validate_payload_references&#96;, &#96;work_units&#96; is built as &#96;{record.logical_id.removeprefix("work-unit-"): record for record in chain if isinstance(record.payload, (WorkUnitPayload, CorrectionWorkUnitPayload)) ...}&#96;. Because this is a plain dict comprehension iterated in chain (append) order, when the same logical work-unit id has more than one persisted revision (the normal case for a multi-round Slice — this evidence's own decision table shows &#96;work-unit-2&#96; at revisions 1/2/3, positions 3/15/26), the dict entry for that id ends up bound to the LAST occurrence in the chain, not the first. &#96;first_work_unit_position = min(positions[...] for record in work_units.values())&#96; therefore resolves to the position of the work unit's LAST revision, not the position of the true first WorkUnitPayload/CorrectionWorkUnitPayload record. Consequence: every checked-type record (AgentResultPayload/DiagnosticPayload/ReviewPayload/ProviderInputMeasurementPayload/FinalReviewPreflightPayload) positioned between the first and last revision of the referenced work unit is silently exempt from the reference-existence/ordering check (&#96;positions[record] &gt; first_work_unit_position&#96; is false for it), even though the C-01/C-04 design intent is "once the first implementation work unit appears, later work-unit-owned activity must resolve to an already-chained record." Worse, this reopens exactly the class of gap C-04 was meant to close: a record referencing a completely fabricated &#96;work_unit_id&#96; that never has any WorkUnitPayload anywhere in the chain would correctly fail the &#96;not in work_units&#96; branch only when it is positioned after &#96;first_work_unit_position&#96;; if it is positioned earlier (which, given the boundary is anchored to a later revision than the true first work-unit record, is now a much larger window than intended), the whole reference check for that record is skipped and the invented reference is silently accepted. None of the existing tests exercise a logical work-unit id with more than one revision plus an interleaved checked-type record, so this gap is real and untested, not pre-existing noise, and it directly undermines the fail-closed cross-record authority guarantee that this Slice's own stated design goal establishes. Minimal fix: track the position of the FIRST occurrence per logical id (only set on first insert, or track min per key instead of dict-overwrite last), and/or compute &#96;first_work_unit_position&#96; as the minimum position across all WorkUnitPayload/CorrectionWorkUnitPayload records in the chain directly (not via a per-logical-id-collapsed dict), and validate each reference against the specific work-unit record's position that is nearest-but-still-before the referencing record, not an arbitrary later revision.
- Akzeptanztest: VALIDATE: ["python3","-m","pytest","tests/test_artifact_replay.py","-v"]
- Statusbegründung: The &#96;work_units&#96; map now uses &#96;setdefault&#96; so each logical work-unit id binds to its first chained revision, making &#96;first_work_unit_position&#96; and the per-record ordering check anchor on the true first occurrence instead of the last. Hand-traced against the original exemption-window scenario and the multi-revision/forward-reference interaction; the new regression test &#96;test_replay_uses_first_work_unit_revision_as_reference_boundary&#96; reproduces and closes exactly the defect described. Bound attestation validation-fb3b46b6e357 shows the full suite (930/930) and the focused replay suite (10/10) passing.

### `C-06` — `OPEN`

- Quelle: `claude`; Runde 1
- Klasse: `OBSERVATION`
- Finding: &#96;_mirror_difference_code&#96; is exercised by real mismatch tests at only 1 of its 6 call sites (gate decisions, MIRROR_AHEAD case), and no test anywhere asserts a MIRROR_AMBIGUOUS outcome (mixed divergence or records-ahead-of-mirror) for any of the finding-transition/attestation/quota/transient/bootstrap resume-mismatch paths, so a future edit to the classifier's set logic or its wiring at those sites could silently misclassify without any test failing.
- Akzeptanztest: Add resume-mismatch parametrized cases mirroring the existing gate-decision test for finding-transition, attestation, quota-pause, transient-retry, and bootstrap-check divergence, each asserting &#96;error.value.code&#96;: one strict-superset case expecting &#96;MIRROR_AHEAD&#96; and one mixed or record-ahead-of-mirror case expecting &#96;MIRROR_AMBIGUOUS&#96;.
- Statusbegründung: –

<!-- artifact-records:findings:begin -->
Semantischer Record-Digest: `6d67cbcad83638a3a2e04cf7763d6fdbe1f43fed69f2ce91d6a84c30c435c7db`

- 3. `ar1-40a29312d6f969559a434e7420f110ad9856b153b0049bccecf05051bf3194aa`: `C-01` `opened` durch `claude`; `BLOCKER` / `open` — The bound validation attestation for fingerprint 02b647bc6f8b329330c4e343536d7741755c23c3046e34c0ec3bd5abe40580f7 is FAIL: &#96;python3 -m pytest tests/ -v&#96; reports 5 failures in tests/test_orchestrator_runtime.py, all raised as RECORD-REFERENCE-MISSING ("work unit '&lt;id&gt;' is not present") from the new src/artifact_replay.py &#96;_validate_payload_references&#96; cross-record check. This check requires every work_unit_id referenced by AgentResultPayload/DiagnosticPayload/ReviewPayload/ProviderInputMeasurementPayload/FinalReviewPreflightPayload to resolve via logical_id.removeprefix("work-unit-") to an already-chained WorkUnitPayload/CorrectionWorkUnitPayload record. The orchestrator-built chains exercised by these tests do not satisfy that stricter shape, so the shared replay kernel now rejects data the orchestrator itself produces. src/orchestrator.py is outside this Slice's allowlist and no red-state follow-up Slice is authorized, so this is an unresolved regression, not pre-existing noise.
- 4. `ar1-f165d517f90bd80596becce9d18b591b52d539419b8d80b2b6014418f2081a9d`: `C-02` `opened` durch `claude`; `OBSERVATION` / `open` — ArtifactReplayResult.subset() matches records by Python object identity (id()) rather than value/record_id equality; any future caller passing a structurally-identical but distinct ArtifactRecord instance (e.g. re-deserialized) will incorrectly hit RECORD_UNKNOWN instead of being accepted as a valid subsequence.
- 5. `ar1-7dc65c2dbf02cd301a138e6f61b21be8fa15577b8516894f832919d65cf8b88a`: `C-03` `opened` durch `claude`; `OBSERVATION` / `open` — ArtifactStore._refresh_cache_with_context threads &#96;expected_cache_chain&#96; through a transient instance attribute &#96;self._expected_cache_chain&#96; (set/deleted around the call) rather than an explicit parameter, so concurrent &#96;put()&#96; calls on a shared ArtifactStore instance could interleave and suppress a legitimate stale-cache warning.
- 6. `ar1-66f78f83665232d8b6b117c46c3a902553a7b1809ab31976574d78444cfed481`: `C-01` `responded` durch `codex`; `BLOCKER` / `open` — ACCEPTED: Work-Unit-Referenzen werden ab dem ersten persistierten Implementierungs-Work-Unit-Record verpflichtend geprüft; schema-konforme Planaktivitäten davor bleiben zulässig.
- 7. `ar1-14b73dfb721961bd5085263050b092509dc537755345bbdb33b12c29919ff288`: `C-02` `responded` durch `codex`; `OBSERVATION` / `open` — ACCEPTED: subset() prüft nun Record-ID und vollständige Wertgleichheit statt Python-Objektidentität; re-deserialisierte identische Records werden akzeptiert, manipulierte abgelehnt.
- 8. `ar1-1e63714bfe1ccef4334fc216685e218e860c8b98c1f1a479a97a0114e8bbccd0`: `C-03` `responded` durch `codex`; `OBSERVATION` / `open` — ACCEPTED: Der erwartete Cachefortschritt wird explizit als Parameter weitergegeben; transienter Instanzzustand wurde entfernt und die Single-Writer-Grenze dokumentiert.
- 9. `ar1-917f538fae731de237fd1ab07f887f7aab8adf1ecef7da8bff14952ebe9738ce`: `C-04` `opened` durch `claude`; `BLOCKER` / `open` — In &#96;_validate_payload_references&#96;, the work-unit-id reference check for &#96;AgentResultPayload&#96;/&#96;DiagnosticPayload&#96;/&#96;ReviewPayload&#96;/&#96;ProviderInputMeasurementPayload&#96;/&#96;FinalReviewPreflightPayload&#96; only tests &#96;payload.work_unit_id not in work_units&#96;, where &#96;work_units&#96; is built by scanning the *entire* chain regardless of position. Every other cross-record reference in the same function (&#96;BindingPayload&#96;→attestation/approvals, &#96;WorkflowCompletionPayload&#96;→final binding, &#96;FinalReviewPreflightPayload&#96;→measurement) explicitly requires &#96;positions[referenced] &lt; positions[record]&#96; ("already exists before this point"); this branch has no such ordering check. A corrupted, reordered, or maliciously constructed chain could contain a record at position N referencing work-unit "5" while the &#96;WorkUnitPayload&#96; for "5" only appears at position N+k, and replay would still accept it as valid — directly contradicting the stated design intent in this very fix ("resolve … to an already-chained WorkUnitPayload … record") and the repo's fail-closed record-authority policy (resume must reject invented/out-of-order facts, not just missing ones). This is a real gap in the newly hardened validator, not pre-existing noise, and is cheaply fixable by adding the same &#96;positions[work_units[...].record_id] &lt; positions[record.record_id]&#96; check used elsewhere in the function.
- 10. `ar1-fd74ab29d8938502b17d4e5806061f50e4f0aa779c0a36c7873db9efbdae97e3`: `C-01` `responded` durch `codex`; `BLOCKER` / `open` — ACCEPTED: Planaktivitäten vor dem ersten Work-Unit-Record bleiben zulässig; danach werden Work-Unit-Referenzen strikt validiert.
- 11. `ar1-dd38b1464da143b8f11abbff9fe0b850b982c01fc80f95e5b0a860f070fe144d`: `C-02` `responded` durch `codex`; `OBSERVATION` / `open` — ACCEPTED: subset() verwendet Record-ID und vollständige Wertgleichheit statt Objektidentität.
- 12. `ar1-b45516e21771680c9e3a68679bc4cfcfa46a704f77611976fff5bd0ec047b80b`: `C-03` `responded` durch `codex`; `OBSERVATION` / `open` — ACCEPTED: Der erwartete Cachefortschritt wird ohne transienten Instanzzustand explizit übergeben.
- 13. `ar1-2485808dcc6a723757d80ed4877bbe3630ba307d5692ef053834be2954a45e78`: `C-04` `responded` durch `codex`; `BLOCKER` / `open` — ACCEPTED: Referenzierte Work-Unit-Records müssen nun vor dem verweisenden Record stehen; ein Vorwärtsreferenz-Regressionstest bestätigt das fail-closed Verhalten.
- 14. `ar1-bbe6416317413787a3d79e338b5450a279391850e8f6740f522c7c3aa3b81fda`: `C-01` `status_changed` durch `claude`; `BLOCKER` / `closed` — Fixed via the &#96;first_work_unit_position&#96; gate: &#96;_validate_payload_references&#96; now only enforces work-unit reference resolution for records positioned after the first WorkUnitPayload/CorrectionWorkUnitPayload record; plan-time activity before it is exempt (test_replay_allows_plan_activity_before_the_first_work_unit_record). The bound attestation validation-3f52c99e7930 shows all five previously-failing tests/test_orchestrator_runtime.py cases now PASS.
- 15. `ar1-ea92525bb2cb4ce8dec3070051fa933ce92d6a6e2415cc8b0198763d5ebb16dc`: `C-02` `status_changed` durch `claude`; `OBSERVATION` / `closed` — &#96;ArtifactReplayResult.subset()&#96; now matches by &#96;record_id&#96; plus full value equality against the accepted replay, not object identity; test_subset_accepts_equal_redeserialized_records_but_rejects_same_id_tampering confirms a re-deserialized-but-equal record is accepted and a same-id tampered record is rejected with RECORD_UNKNOWN.
- 16. `ar1-d12a1d16a5464ae7d0c93052286a599bd035bb3166cd64f69cd752ad2f750c50`: `C-03` `status_changed` durch `claude`; `OBSERVATION` / `closed` — &#96;expected_cache_chain&#96; is now an explicit parameter threaded through &#96;_load_chain&#96;/&#96;_refresh_cache_with_context&#96;/&#96;_refresh_head_cache&#96;, not a transient instance attribute. test_cache_refresh_passes_expected_progress_without_instance_state asserts &#96;not hasattr(store, "_expected_cache_chain")&#96;; the single-writer-per-run boundary is documented in &#96;put()&#96;'s docstring.
- 17. `ar1-d86b522eeb07aea83f08de6f064f2a01f4b39524f93b005bd6befb2bb2c29e13`: `C-04` `status_changed` durch `claude`; `BLOCKER` / `closed` — The work-unit branch now requires &#96;positions[work_units[id].record_id] &lt; positions[record.record_id]&#96;, mirroring the ordering discipline used for BindingPayload/WorkflowCompletionPayload/FinalReviewPreflightPayload. test_replay_rejects_activity_that_references_a_later_work_unit is a direct forward-reference regression and passes under the bound attestation (tests/test_artifact_replay.py -v, 9/9 PASS).
- 18. `ar1-f042272679dbfc2fe1d98201a8bb3685382604bde827c107ab468362209648f9`: `C-05` `opened` durch `claude`; `BLOCKER` / `open` — In &#96;_validate_payload_references&#96;, &#96;work_units&#96; is built as &#96;{record.logical_id.removeprefix("work-unit-"): record for record in chain if isinstance(record.payload, (WorkUnitPayload, CorrectionWorkUnitPayload)) ...}&#96;. Because this is a plain dict comprehension iterated in chain (append) order, when the same logical work-unit id has more than one persisted revision (the normal case for a multi-round Slice — this evidence's own decision table shows &#96;work-unit-2&#96; at revisions 1/2/3, positions 3/15/26), the dict entry for that id ends up bound to the LAST occurrence in the chain, not the first. &#96;first_work_unit_position = min(positions[...] for record in work_units.values())&#96; therefore resolves to the position of the work unit's LAST revision, not the position of the true first WorkUnitPayload/CorrectionWorkUnitPayload record. Consequence: every checked-type record (AgentResultPayload/DiagnosticPayload/ReviewPayload/ProviderInputMeasurementPayload/FinalReviewPreflightPayload) positioned between the first and last revision of the referenced work unit is silently exempt from the reference-existence/ordering check (&#96;positions[record] &gt; first_work_unit_position&#96; is false for it), even though the C-01/C-04 design intent is "once the first implementation work unit appears, later work-unit-owned activity must resolve to an already-chained record." Worse, this reopens exactly the class of gap C-04 was meant to close: a record referencing a completely fabricated &#96;work_unit_id&#96; that never has any WorkUnitPayload anywhere in the chain would correctly fail the &#96;not in work_units&#96; branch only when it is positioned after &#96;first_work_unit_position&#96;; if it is positioned earlier (which, given the boundary is anchored to a later revision than the true first work-unit record, is now a much larger window than intended), the whole reference check for that record is skipped and the invented reference is silently accepted. None of the existing tests exercise a logical work-unit id with more than one revision plus an interleaved checked-type record, so this gap is real and untested, not pre-existing noise, and it directly undermines the fail-closed cross-record authority guarantee that this Slice's own stated design goal establishes. Minimal fix: track the position of the FIRST occurrence per logical id (only set on first insert, or track min per key instead of dict-overwrite last), and/or compute &#96;first_work_unit_position&#96; as the minimum position across all WorkUnitPayload/CorrectionWorkUnitPayload records in the chain directly (not via a per-logical-id-collapsed dict), and validate each reference against the specific work-unit record's position that is nearest-but-still-before the referencing record, not an arbitrary later revision.
- 19. `ar1-94d448e3348bec066292750fe2ffc88ffc391b8acb8de0a2a5f63541777ad771`: `C-05` `responded` durch `codex`; `BLOCKER` / `open` — ACCEPTED: Die erste persistierte Revision begründet nun die Work-Unit-Autorität; spätere Revisionen können die Referenzprüfgrenze nicht mehr verschieben, und ein Regressionstest bestätigt das fail-closed Verhalten zwischen Revisionen.
- 20. `ar1-2faaa0817050231d7d45c7c5a08bf2da78f26091c11f33974ee3523360e61dd7`: `C-05` `status_changed` durch `claude`; `BLOCKER` / `closed` — The &#96;work_units&#96; map now uses &#96;setdefault&#96; so each logical work-unit id binds to its first chained revision, making &#96;first_work_unit_position&#96; and the per-record ordering check anchor on the true first occurrence instead of the last. Hand-traced against the original exemption-window scenario and the multi-revision/forward-reference interaction; the new regression test &#96;test_replay_uses_first_work_unit_revision_as_reference_boundary&#96; reproduces and closes exactly the defect described. Bound attestation validation-fb3b46b6e357 shows the full suite (930/930) and the focused replay suite (10/10) passing.
- 28. `ar1-ba9d544f74f0343d7938083ee7209b92d7100ae071ec0560f38bd2df22768760`: `C-06` `opened` durch `claude`; `OBSERVATION` / `open` — &#96;_mirror_difference_code&#96; is exercised by real mismatch tests at only 1 of its 6 call sites (gate decisions, MIRROR_AHEAD case), and no test anywhere asserts a MIRROR_AMBIGUOUS outcome (mixed divergence or records-ahead-of-mirror) for any of the finding-transition/attestation/quota/transient/bootstrap resume-mismatch paths, so a future edit to the classifier's set logic or its wiring at those sites could silently misclassify without any test failing.
<!-- artifact-records:findings:end -->
<!-- audit:findings:end -->

## Entscheidungstabelle

<!-- audit:decision-table:begin -->
| ID | Quelle | Finding | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|---|
| C-01 | claude | The bound validation attestation for fingerprint 02b647bc6f8b329330c4e343536d7741755c23c3046e34c0ec3bd5abe40580f7 is FAIL: &#96;python3 -m pytest tests/ -v&#96; reports 5 failures in tests/test_orchestrator_runtime.py, all raised as RECORD-REFERENCE-MISSING ("work unit '&lt;id&gt;' is not present") from the new src/artifact_replay.py &#96;_validate_payload_references&#96; cross-record check. This check requires every work_unit_id referenced by AgentResultPayload/DiagnosticPayload/ReviewPayload/ProviderInputMeasurementPayload/FinalReviewPreflightPayload to resolve via logical_id.removeprefix("work-unit-") to an already-chained WorkUnitPayload/CorrectionWorkUnitPayload record. The orchestrator-built chains exercised by these tests do not satisfy that stricter shape, so the shared replay kernel now rejects data the orchestrator itself produces. src/orchestrator.py is outside this Slice's allowlist and no red-state follow-up Slice is authorized, so this is an unresolved regression, not pre-existing noise. | BLOCKER | angenommen | erledigt: Fixed via the &#96;first_work_unit_position&#96; gate: &#96;_validate_payload_references&#96; now only enforces work-unit reference resolution for records positioned after the first WorkUnitPayload/CorrectionWorkUnitPayload record; plan-time activity before it is exempt (test_replay_allows_plan_activity_before_the_first_work_unit_record). The bound attestation validation-3f52c99e7930 shows all five previously-failing tests/test_orchestrator_runtime.py cases now PASS. |
| C-02 | claude | ArtifactReplayResult.subset() matches records by Python object identity (id()) rather than value/record_id equality; any future caller passing a structurally-identical but distinct ArtifactRecord instance (e.g. re-deserialized) will incorrectly hit RECORD_UNKNOWN instead of being accepted as a valid subsequence. | OBSERVATION | angenommen | erledigt: &#96;ArtifactReplayResult.subset()&#96; now matches by &#96;record_id&#96; plus full value equality against the accepted replay, not object identity; test_subset_accepts_equal_redeserialized_records_but_rejects_same_id_tampering confirms a re-deserialized-but-equal record is accepted and a same-id tampered record is rejected with RECORD_UNKNOWN. |
| C-03 | claude | ArtifactStore._refresh_cache_with_context threads &#96;expected_cache_chain&#96; through a transient instance attribute &#96;self._expected_cache_chain&#96; (set/deleted around the call) rather than an explicit parameter, so concurrent &#96;put()&#96; calls on a shared ArtifactStore instance could interleave and suppress a legitimate stale-cache warning. | OBSERVATION | angenommen | erledigt: &#96;expected_cache_chain&#96; is now an explicit parameter threaded through &#96;_load_chain&#96;/&#96;_refresh_cache_with_context&#96;/&#96;_refresh_head_cache&#96;, not a transient instance attribute. test_cache_refresh_passes_expected_progress_without_instance_state asserts &#96;not hasattr(store, "_expected_cache_chain")&#96;; the single-writer-per-run boundary is documented in &#96;put()&#96;'s docstring. |
| C-04 | claude | In &#96;_validate_payload_references&#96;, the work-unit-id reference check for &#96;AgentResultPayload&#96;/&#96;DiagnosticPayload&#96;/&#96;ReviewPayload&#96;/&#96;ProviderInputMeasurementPayload&#96;/&#96;FinalReviewPreflightPayload&#96; only tests &#96;payload.work_unit_id not in work_units&#96;, where &#96;work_units&#96; is built by scanning the *entire* chain regardless of position. Every other cross-record reference in the same function (&#96;BindingPayload&#96;→attestation/approvals, &#96;WorkflowCompletionPayload&#96;→final binding, &#96;FinalReviewPreflightPayload&#96;→measurement) explicitly requires &#96;positions[referenced] &lt; positions[record]&#96; ("already exists before this point"); this branch has no such ordering check. A corrupted, reordered, or maliciously constructed chain could contain a record at position N referencing work-unit "5" while the &#96;WorkUnitPayload&#96; for "5" only appears at position N+k, and replay would still accept it as valid — directly contradicting the stated design intent in this very fix ("resolve … to an already-chained WorkUnitPayload … record") and the repo's fail-closed record-authority policy (resume must reject invented/out-of-order facts, not just missing ones). This is a real gap in the newly hardened validator, not pre-existing noise, and is cheaply fixable by adding the same &#96;positions[work_units[...].record_id] &lt; positions[record.record_id]&#96; check used elsewhere in the function. | BLOCKER | angenommen | erledigt: The work-unit branch now requires &#96;positions[work_units[id].record_id] &lt; positions[record.record_id]&#96;, mirroring the ordering discipline used for BindingPayload/WorkflowCompletionPayload/FinalReviewPreflightPayload. test_replay_rejects_activity_that_references_a_later_work_unit is a direct forward-reference regression and passes under the bound attestation (tests/test_artifact_replay.py -v, 9/9 PASS). |
| C-05 | claude | In &#96;_validate_payload_references&#96;, &#96;work_units&#96; is built as &#96;{record.logical_id.removeprefix("work-unit-"): record for record in chain if isinstance(record.payload, (WorkUnitPayload, CorrectionWorkUnitPayload)) ...}&#96;. Because this is a plain dict comprehension iterated in chain (append) order, when the same logical work-unit id has more than one persisted revision (the normal case for a multi-round Slice — this evidence's own decision table shows &#96;work-unit-2&#96; at revisions 1/2/3, positions 3/15/26), the dict entry for that id ends up bound to the LAST occurrence in the chain, not the first. &#96;first_work_unit_position = min(positions[...] for record in work_units.values())&#96; therefore resolves to the position of the work unit's LAST revision, not the position of the true first WorkUnitPayload/CorrectionWorkUnitPayload record. Consequence: every checked-type record (AgentResultPayload/DiagnosticPayload/ReviewPayload/ProviderInputMeasurementPayload/FinalReviewPreflightPayload) positioned between the first and last revision of the referenced work unit is silently exempt from the reference-existence/ordering check (&#96;positions[record] &gt; first_work_unit_position&#96; is false for it), even though the C-01/C-04 design intent is "once the first implementation work unit appears, later work-unit-owned activity must resolve to an already-chained record." Worse, this reopens exactly the class of gap C-04 was meant to close: a record referencing a completely fabricated &#96;work_unit_id&#96; that never has any WorkUnitPayload anywhere in the chain would correctly fail the &#96;not in work_units&#96; branch only when it is positioned after &#96;first_work_unit_position&#96;; if it is positioned earlier (which, given the boundary is anchored to a later revision than the true first work-unit record, is now a much larger window than intended), the whole reference check for that record is skipped and the invented reference is silently accepted. None of the existing tests exercise a logical work-unit id with more than one revision plus an interleaved checked-type record, so this gap is real and untested, not pre-existing noise, and it directly undermines the fail-closed cross-record authority guarantee that this Slice's own stated design goal establishes. Minimal fix: track the position of the FIRST occurrence per logical id (only set on first insert, or track min per key instead of dict-overwrite last), and/or compute &#96;first_work_unit_position&#96; as the minimum position across all WorkUnitPayload/CorrectionWorkUnitPayload records in the chain directly (not via a per-logical-id-collapsed dict), and validate each reference against the specific work-unit record's position that is nearest-but-still-before the referencing record, not an arbitrary later revision. | BLOCKER | angenommen | erledigt: The &#96;work_units&#96; map now uses &#96;setdefault&#96; so each logical work-unit id binds to its first chained revision, making &#96;first_work_unit_position&#96; and the per-record ordering check anchor on the true first occurrence instead of the last. Hand-traced against the original exemption-window scenario and the multi-revision/forward-reference interaction; the new regression test &#96;test_replay_uses_first_work_unit_revision_as_reference_boundary&#96; reproduces and closes exactly the defect described. Bound attestation validation-fb3b46b6e357 shows the full suite (930/930) and the focused replay suite (10/10) passing. |
| C-06 | claude | &#96;_mirror_difference_code&#96; is exercised by real mismatch tests at only 1 of its 6 call sites (gate decisions, MIRROR_AHEAD case), and no test anywhere asserts a MIRROR_AMBIGUOUS outcome (mixed divergence or records-ahead-of-mirror) for any of the finding-transition/attestation/quota/transient/bootstrap resume-mismatch paths, so a future edit to the classifier's set logic or its wiring at those sites could silently misclassify without any test failing. | OBSERVATION | offen | offen |

<!-- artifact-records:decision-table:begin -->
Semantischer Record-Digest: `6d67cbcad83638a3a2e04cf7763d6fdbe1f43fed69f2ce91d6a84c30c435c7db`

| Seq | Record | Typ | Status | Logische ID | Revision | Fingerprint |
|---:|---|---|---|---|---:|---|
| 1 | `ar1-5464be7fef4382b7a0ab4106969a2fc742bdca55edbbcf5f08f5f4a2872ab19a` | `task` | `accepted` | `task-contract` | 1 | `contract:d9c35a04774cdb0045007928d0b30f5919a732cf7246f95fc12cf3b7dc6988e5` |
| 2 | `ar1-d427b9ee39439dc3f07a5f5299ac2f9ad347846af94aa10b8e4b38c44afffec6` | `plan` | `approved` | `approved-plan` | 1 | `contract:d9c35a04774cdb0045007928d0b30f5919a732cf7246f95fc12cf3b7dc6988e5` |
| 3 | `ar1-40a29312d6f969559a434e7420f110ad9856b153b0049bccecf05051bf3194aa` | `finding_transition` | `recorded` | `finding-C-01` | 1 | `implementation:02b647bc6f8b329330c4e343536d7741755c23c3046e34c0ec3bd5abe40580f7` |
| 4 | `ar1-f165d517f90bd80596becce9d18b591b52d539419b8d80b2b6014418f2081a9d` | `finding_transition` | `recorded` | `finding-C-02` | 1 | `implementation:02b647bc6f8b329330c4e343536d7741755c23c3046e34c0ec3bd5abe40580f7` |
| 5 | `ar1-7dc65c2dbf02cd301a138e6f61b21be8fa15577b8516894f832919d65cf8b88a` | `finding_transition` | `recorded` | `finding-C-03` | 1 | `implementation:02b647bc6f8b329330c4e343536d7741755c23c3046e34c0ec3bd5abe40580f7` |
| 6 | `ar1-66f78f83665232d8b6b117c46c3a902553a7b1809ab31976574d78444cfed481` | `finding_transition` | `recorded` | `finding-C-01` | 2 | `implementation:2a96bf5ffe55b9489fff4056a6e6d4da47fe182ba018238ff8b9499afe4d101c` |
| 7 | `ar1-14b73dfb721961bd5085263050b092509dc537755345bbdb33b12c29919ff288` | `finding_transition` | `recorded` | `finding-C-02` | 2 | `implementation:2a96bf5ffe55b9489fff4056a6e6d4da47fe182ba018238ff8b9499afe4d101c` |
| 8 | `ar1-1e63714bfe1ccef4334fc216685e218e860c8b98c1f1a479a97a0114e8bbccd0` | `finding_transition` | `recorded` | `finding-C-03` | 2 | `implementation:2a96bf5ffe55b9489fff4056a6e6d4da47fe182ba018238ff8b9499afe4d101c` |
| 9 | `ar1-917f538fae731de237fd1ab07f887f7aab8adf1ecef7da8bff14952ebe9738ce` | `finding_transition` | `recorded` | `finding-C-04` | 1 | `implementation:2a96bf5ffe55b9489fff4056a6e6d4da47fe182ba018238ff8b9499afe4d101c` |
| 10 | `ar1-fd74ab29d8938502b17d4e5806061f50e4f0aa779c0a36c7873db9efbdae97e3` | `finding_transition` | `recorded` | `finding-C-01` | 3 | `implementation:3f52c99e7930e9b9909675042ddf88a02e91edc0377dd2eb5e893b578a1308ff` |
| 11 | `ar1-dd38b1464da143b8f11abbff9fe0b850b982c01fc80f95e5b0a860f070fe144d` | `finding_transition` | `recorded` | `finding-C-02` | 3 | `implementation:3f52c99e7930e9b9909675042ddf88a02e91edc0377dd2eb5e893b578a1308ff` |
| 12 | `ar1-b45516e21771680c9e3a68679bc4cfcfa46a704f77611976fff5bd0ec047b80b` | `finding_transition` | `recorded` | `finding-C-03` | 3 | `implementation:3f52c99e7930e9b9909675042ddf88a02e91edc0377dd2eb5e893b578a1308ff` |
| 13 | `ar1-2485808dcc6a723757d80ed4877bbe3630ba307d5692ef053834be2954a45e78` | `finding_transition` | `recorded` | `finding-C-04` | 2 | `implementation:3f52c99e7930e9b9909675042ddf88a02e91edc0377dd2eb5e893b578a1308ff` |
| 14 | `ar1-bbe6416317413787a3d79e338b5450a279391850e8f6740f522c7c3aa3b81fda` | `finding_transition` | `recorded` | `finding-C-01` | 4 | `implementation:3f52c99e7930e9b9909675042ddf88a02e91edc0377dd2eb5e893b578a1308ff` |
| 15 | `ar1-ea92525bb2cb4ce8dec3070051fa933ce92d6a6e2415cc8b0198763d5ebb16dc` | `finding_transition` | `recorded` | `finding-C-02` | 4 | `implementation:3f52c99e7930e9b9909675042ddf88a02e91edc0377dd2eb5e893b578a1308ff` |
| 16 | `ar1-d12a1d16a5464ae7d0c93052286a599bd035bb3166cd64f69cd752ad2f750c50` | `finding_transition` | `recorded` | `finding-C-03` | 4 | `implementation:3f52c99e7930e9b9909675042ddf88a02e91edc0377dd2eb5e893b578a1308ff` |
| 17 | `ar1-d86b522eeb07aea83f08de6f064f2a01f4b39524f93b005bd6befb2bb2c29e13` | `finding_transition` | `recorded` | `finding-C-04` | 3 | `implementation:3f52c99e7930e9b9909675042ddf88a02e91edc0377dd2eb5e893b578a1308ff` |
| 18 | `ar1-f042272679dbfc2fe1d98201a8bb3685382604bde827c107ab468362209648f9` | `finding_transition` | `recorded` | `finding-C-05` | 1 | `implementation:3f52c99e7930e9b9909675042ddf88a02e91edc0377dd2eb5e893b578a1308ff` |
| 19 | `ar1-94d448e3348bec066292750fe2ffc88ffc391b8acb8de0a2a5f63541777ad771` | `finding_transition` | `recorded` | `finding-C-05` | 2 | `implementation:fb3b46b6e3578cd3954a0d1b80d6a24c6d7c26e9e8723ea723b8f086172f25ce` |
| 20 | `ar1-2faaa0817050231d7d45c7c5a08bf2da78f26091c11f33974ee3523360e61dd7` | `finding_transition` | `recorded` | `finding-C-05` | 3 | `implementation:fb3b46b6e3578cd3954a0d1b80d6a24c6d7c26e9e8723ea723b8f086172f25ce` |
| 21 | `ar1-44b504ce52491b23df42973f6a281d222957b1f124f9692749f4676c1c9b5e01` | `work_unit` | `active` | `work-unit-3` | 1 | `contract:d9c35a04774cdb0045007928d0b30f5919a732cf7246f95fc12cf3b7dc6988e5` |
| 22 | `ar1-635fb202748e33bba1d0c4ca2858944bcf22d1ef90df49829b274b84b36df9f7` | `provider_input_measurement` | `measured` | `provider-input-3-codex_implementation` | 1 | `implementation:9931b7a3798ecc313177f679cc46c1be821f958ca14dd85e40752fa2fafd248f` |
| 23 | `ar1-fac0e48ab219eecc028b94f0857dd499ee3dea103cb169d23781c5f98a38caad` | `agent_result` | `ready` | `agent-3-codex_implementation-1` | 1 | `implementation:a28d02e70bb5b5b730dea1e4c6103dab7bff847208251f5b53c08e298fa62f9a` |
| 24 | `ar1-74f6614f2d7d9dbd487ea8008ecd2c3594a76827c2ab767d481cd19cdc51e646` | `validation_request` | `requested` | `validation-request-a28d02e70bb5` | 1 | `implementation:a28d02e70bb5b5b730dea1e4c6103dab7bff847208251f5b53c08e298fa62f9a` |
| 25 | `ar1-cb0b228ccea2e1b230a2452d7f3d8b264687ea4d61fcf28293f5a5f08538595a` | `validation_attestation` | `attested` | `validation-a28d02e70bb5` | 1 | `implementation:a28d02e70bb5b5b730dea1e4c6103dab7bff847208251f5b53c08e298fa62f9a` |
| 26 | `ar1-e91cc2111129233cd1dd99fe908e9694d4cec5e7cd36dd54c765042ffae532cf` | `provider_input_measurement` | `measured` | `provider-input-3-claude_slice_review` | 1 | `implementation:a28d02e70bb5b5b730dea1e4c6103dab7bff847208251f5b53c08e298fa62f9a` |
| 27 | `ar1-c4acbc7bec64bce735f9adf277491aa6df0475e4aeaece500b8568c9a9abd5e1` | `review` | `decided` | `review-claude-3-1` | 1 | `implementation:a28d02e70bb5b5b730dea1e4c6103dab7bff847208251f5b53c08e298fa62f9a` |
| 28 | `ar1-ba9d544f74f0343d7938083ee7209b92d7100ae071ec0560f38bd2df22768760` | `finding_transition` | `recorded` | `finding-C-06` | 1 | `implementation:a28d02e70bb5b5b730dea1e4c6103dab7bff847208251f5b53c08e298fa62f9a` |
| 29 | `ar1-1dce23f86767309a0b406f137d2a61fe42196e20f90fc899ce27c40b1c73377a` | `provider_input_measurement` | `measured` | `provider-input-3-antigravity_slice_review` | 1 | `implementation:a28d02e70bb5b5b730dea1e4c6103dab7bff847208251f5b53c08e298fa62f9a` |
| 30 | `ar1-6a5793b0df5e227a528430d6a16c3272e142d6cc7d0ea289559acc907213e5f6` | `transient_retry` | `waiting` | `transient-retry-5304501525ed4e80bac35c7d1f0308ba` | 1 | `implementation:a28d02e70bb5b5b730dea1e4c6103dab7bff847208251f5b53c08e298fa62f9a` |
| 31 | `ar1-32b6e91d89d20fae549d163e5dd1d070b4e977f143eda6f37fbb8400f60887fe` | `provider_input_measurement` | `measured` | `provider-input-3-antigravity_slice_review` | 2 | `implementation:a28d02e70bb5b5b730dea1e4c6103dab7bff847208251f5b53c08e298fa62f9a` |
| 32 | `ar1-7ff05c9b3f1d2e64198f9d55d8ee461e44359adb8f87a57f50bfc1983920c309` | `review` | `decided` | `review-antigravity-3-1` | 1 | `implementation:a28d02e70bb5b5b730dea1e4c6103dab7bff847208251f5b53c08e298fa62f9a` |
<!-- artifact-records:decision-table:end -->
<!-- audit:decision-table:end -->

## Rückdokumentation in die Arbeitsplan-MD

Arbeitsplan: `docs/internal/release-1-1a-record-autoritaet-und-replay-implement-review-d9c35a04.md`

## Freigabestatus

<!-- audit:approval-status:begin -->
- Implementierung bereit: `YES`
- Validierung: `PASS`
- Claude-Freigabe: `YES`
- Antigravity-Freigabe: `YES`
- Red-State-Folgeslice: `NONE`
- Commit autorisiert: `YES`

<!-- artifact-records:approval-status:begin -->
Semantischer Record-Digest: `6d67cbcad83638a3a2e04cf7763d6fdbe1f43fed69f2ce91d6a84c30c435c7db`

- 21. `ar1-44b504ce52491b23df42973f6a281d222957b1f124f9692749f4676c1c9b5e01`: Work-Unit Slice `2`, Runde `1`; Pfade `docs/internal/release-1-1a-record-autoritaet-und-replay-implement-review-d9c35a04.md`, `docs/internal/slice-release-1-1a-record-autoritaet-und-replay-arbeitsplan-02-structured-resume-und-auditgrenzen-aus-gemeinsamem-replay.md`, `src/artifact_migration.py`, `src/audit_trail.py`, `src/orchestrator.py`, `tests/test_artifact_migration.py`, `tests/test_audit_trail.py`, `tests/test_orchestrator_runtime.py`, `tests/test_structured_artifact_regressions.py`
<!-- artifact-records:approval-status:end -->
<!-- audit:approval-status:end -->
