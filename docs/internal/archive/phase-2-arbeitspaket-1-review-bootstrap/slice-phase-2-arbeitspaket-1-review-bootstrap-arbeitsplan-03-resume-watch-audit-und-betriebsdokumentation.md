# Slice 03 – Resume, Watch, Audit und Betriebsdokumentation

**Feature-Branch:** `feature/native-agent-json`
**GitHub-Status:** nur lokal

## Ziel des Slice

Resume, Watch, Audit und Betriebsdokumentation

## Akzeptanzkriterien

Siehe freigegebenen Arbeitsplan.

## Scope und Nicht-Scope

Erlaubter Scope: `Quickstart.md`, `README.md`, `docs/internal/phase-2-arbeitspaket-1-review-bootstrap-implement-review-cc745616.md`, `docs/internal/slice-phase-2-arbeitspaket-1-review-bootstrap-arbeitsplan-03-resume-watch-audit-und-betriebsdokumentation.md`, `src/artifact_projection.py`, `src/inbox_watcher.py`, `src/orchestrator.py`, `tests/test_dry_run_scenarios.py`, `tests/test_inbox_watcher.py`, `tests/test_language_consistency.py`, `tests/test_orchestrator_watch_cli.py`, `tests/test_structured_artifact_regressions.py`, `workflow.puml`

## Diff-Risiko inklusive Branch- und Statuscheck

Der aktive Branch wurde als `feature/native-agent-json` bestätigt. Vor der Umsetzung waren ausschließlich die vom Orchestrator verwaltete Gesamtauditdatei und dieses neu angelegte Slice-Dokument im Arbeitsbaum sichtbar. Das größte Slice-Risiko war eine Einstufung des lokalen Bootstrap-Denials als technischer Watchfehler; dies hätte Attempt-Zähler, automatische Wiederholung oder Poison-Verschiebung ausgelöst. Die Umsetzung bindet deshalb `bootstrap_check`, `AWAITING_RESUME`, Exitcode 4 und erhaltene Watch-Identität als gemeinsamen Invariant.

## Geplante Tests

Fokussierte Tests für Watch-Disposition und Wiederaufnahme, CLI-Ergebnisabbildung, strukturierte Messpersistenz/Checkpoint/Idempotenz, Auditprojektion ohne Promptinhalt, historische Protokollfixtures, Dry-run-Szenarien, Sprachkonsistenz und angrenzende Orchestratorregressionen. Die autoritative Vollmatrix bleibt beim Orchestrator.

## Durchgeführte Änderungen

- Bootstrap-Denials werden im formalen Einzelbetrieb und Watchbetrieb als resumierbarer Exit 4 am unveränderten Rollenstep ausgegeben.
- Der Watcher validiert `bootstrap_check` fail-closed als `AWAITING_RESUME`, ohne Attempt-Erhöhung, Providerwiederholung oder Poison-Datei; Wiederstarts behalten Run-ID und Resume-Kontext.
- Die strukturierte Auditprojektion zeigt Messkomponenten, Verletzungsdimensionen, Überhang, größte Komponente sowie Preflight-Kategorie, betroffene Records/Pfade und Abhilfe, aber keinen Eingabeinhalt.
- README, Quickstart und UML dokumentieren Budgettabelle, unbekannte technische Limits, lokale Finalreview-Barriere, Diagnose und Resumeablauf.
- Regressionstests erzwingen einen echten Budgetdenial durch `ProductionWorkflowDriver` und `WorkflowEngine` und prüfen duale Persistenz, Checkpoint, Gate, gleichen Step und record-idempotentes Resume.

## Ausgeführte Validierung mit Ergebnis

- `python3 -m pytest tests/test_inbox_watcher.py tests/test_orchestrator_watch_cli.py tests/test_structured_artifact_regressions.py tests/test_language_consistency.py -q -p no:cacheprovider` — 72 bestanden.
- `python3 -m pytest tests/test_dry_run_scenarios.py tests/test_artifact_projection.py tests/test_orchestrator_runtime.py -q -p no:cacheprovider` — 103 bestanden.
- Die vollständige konfigurierte Matrix wurde gemäß Rollenvertrag nicht im Agentenprozess ausgeführt; ihre Attestierung wird vom Orchestrator in den verwalteten Auditabschnitt projiziert.

## Abweichungen vom Plan

Keine erfasst.

## Offene Risiken

Die autoritative Vollmatrix und die Reviewerfreigaben stehen noch aus. C-02 betrifft die Providerregistrierung in einem abgeschlossenen Slice-01-Pfad; C-04 betrifft die Trennung zweier Tests in `tests/test_workflow_state.py` aus Slice 02. Beide Pfade liegen außerhalb der aktuellen persistierten Allowlist und wurden hier nicht verändert.

## Review-Feedback von Claude

<!-- audit:claude-review:begin -->
### Ereignis 3: Runde 1

- Reviewer: `claude`
- Freigabe: `YES`
- Validierungsbindung: `validation-66e1cbe92b12`
- Testdateien: `tests/test_agent_adapters.py`, `tests/test_dry_run_scenarios.py`, `tests/test_inbox_watcher.py`, `tests/test_orchestrator_watch_cli.py`, `tests/test_quota_wait.py`, `tests/test_structured_artifact_regressions.py`, `tests/test_workflow.py`, `tests/test_workflow_state.py`
- Eigene Findings: `C-01`, `C-02`, `C-03`, `C-04`

### Ereignis 6: Runde 2

- Reviewer: `claude`
- Freigabe: `YES`
- Validierungsbindung: `validation-98ee175b02a4`
- Testdateien: `tests/test_agent_adapters.py`, `tests/test_dry_run_scenarios.py`, `tests/test_git_service.py`, `tests/test_inbox_watcher.py`, `tests/test_orchestrator_watch_cli.py`, `tests/test_quota_wait.py`, `tests/test_structured_artifact_regressions.py`, `tests/test_workflow.py`, `tests/test_workflow_state.py`
- Prüfdimensionen: checked dimensions — resume/idempotency (watch bootstrap-denial loop, quota-resume-diff legacy migration), fail-closed posture (timezone/dated-reset parsing, gate ancestry checks), scope/authorization boundary correctness (git commit drift authorization, exact-set path matching), review-staleness handling, and documentation/UML synchronization with the new bootstrap barrier
- Größtes Restrisiko: largest residual risk: the untested negative paths of the new git commit-authorization drift mechanism (C-05)
- Realistische Bruchbedingung: realistic break condition: an approved external-path gate becomes stale relative to a later uncommitted edit and the missing negative-path tests let a scope violation or an incorrect denial pass unnoticed until a real slice commit misbehaves.
- Eigene Findings: `C-01`, `C-02`, `C-03`, `C-04`, `C-05`

<!-- artifact-records:claude-review:begin -->
Semantischer Record-Digest: `b733a57258a944f9f1b2e344a7868636be93f4de935595c345db455b71ad6f55`

- 22. `ar1-101a4359cecaeddf04eb64ae8041aa1b025fd671eb63928b9dc8e2c6a11d018b`: `approved`; Work-Unit `4`; Findings `C-01`, `C-02`, `C-03`, `C-04`; Fingerprint `66e1cbe92b121f0bcc88b1ac3b3069bed0f573e7ebe74cfc0b64c048e2fb02ab`
- 33. `ar1-32fe0d072b8420816b37d9304a4b2c8e52c6ee0108efdbf75196b921660c4463`: `approved`; Work-Unit `4`; Findings `C-01`, `C-02`, `C-03`, `C-04`, `C-05`; Fingerprint `98ee175b02a44bda45135650d1ce51465fae1e4c5f09c74f0e95c997015be25c`
<!-- artifact-records:claude-review:end -->
<!-- audit:claude-review:end -->

## Review-Feedback von Antigravity

<!-- audit:antigravity-review:begin -->
### Ereignis 4: Runde 1

- Reviewer: `antigravity`
- Freigabe: `YES`
- Validierungsbindung: `validation-66e1cbe92b12`
- Testdateien: `tests/test_agent_adapters.py`, `tests/test_dry_run_scenarios.py`, `tests/test_inbox_watcher.py`, `tests/test_orchestrator_watch_cli.py`, `tests/test_quota_wait.py`, `tests/test_structured_artifact_regressions.py`, `tests/test_workflow.py`, `tests/test_workflow_state.py`
- Prüfdimensionen: watch queue disposition and lifecycle invariants, bootstrap_check exit code 4 contract, quota-resume-diff gate validation and side-effect acknowledgements, structured audit projection field rendering and prompt data sanitation, documentation synchronization across README Quickstart and UML, idempotent resume and checkpoint verification on bootstrap denial
- Größtes Restrisiko: an edge-case environment where /etc/localtime and /etc/timezone are unreadable and TZ is unset while running on a non-standard Linux container, causing _system_local_timezone to return None and silently falling back to unparsed dated local reset strings
- Realistische Bruchbedingung: an external execution environment mounts /etc with restricted read permissions without exporting the TZ environment variable, causing Codex dated local quota strings to fail parsing and halting with manual exit 2 instead of automatic quota sleep
- Eigene Findings: keine

### Ereignis 7: Runde 2

- Reviewer: `antigravity`
- Freigabe: `YES`
- Validierungsbindung: `validation-98ee175b02a4`
- Testdateien: `tests/test_agent_adapters.py`, `tests/test_dry_run_scenarios.py`, `tests/test_git_service.py`, `tests/test_inbox_watcher.py`, `tests/test_orchestrator_watch_cli.py`, `tests/test_quota_wait.py`, `tests/test_structured_artifact_regressions.py`, `tests/test_workflow.py`, `tests/test_workflow_state.py`
- Prüfdimensionen: resume/idempotency invariants under bootstrap denial and quota-resume-diff, host timezone resolution and dated-reset safety, remote tool runtime vs local binary failure classification, git commit HEAD-drift authorization and ancestry verification, structured audit projection escaping and field hygiene, documentation and UML alignment with bootstrap checks
- Größtes Restrisiko: external git repository manipulation creating an octopus or non-linear history on the feature branch that satisfies merge-base ancestry while having unreviewed intermediate merge commits
- Realistische Bruchbedingung: an operator manually merges an unreviewed branch into the feature branch while a slice is awaiting gate approval, satisfying is-ancestor but introducing untracked merged files that conflict with the transaction boundary during commit_slice
- Eigene Findings: keine

<!-- artifact-records:antigravity-review:begin -->
Semantischer Record-Digest: `b733a57258a944f9f1b2e344a7868636be93f4de935595c345db455b71ad6f55`

- 26. `ar1-3dd8de721e26b18be450301cfa22c0af82ef7e6099ee11bfec3746d7cc7a48da`: `approved`; Work-Unit `4`; Findings `C-01`, `C-02`, `C-03`, `C-04`; Fingerprint `66e1cbe92b121f0bcc88b1ac3b3069bed0f573e7ebe74cfc0b64c048e2fb02ab`
- 36. `ar1-c1cb61482e74d486e8bc4909d65fdec40d57e6b3dba30910a7d9c7bd8ae49a08`: `approved`; Work-Unit `4`; Findings `C-01`, `C-02`, `C-03`, `C-04`, `C-05`; Fingerprint `98ee175b02a44bda45135650d1ce51465fae1e4c5f09c74f0e95c997015be25c`
<!-- artifact-records:antigravity-review:end -->
<!-- audit:antigravity-review:end -->

## Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
- `C-01` Antwort 1: **angenommen** — Ein Vollständigkeitstest prüft nun jede produktiv verdrahtete WorkflowStep-/Provider-Kombination gegen die Budgetpolicy.
- `C-02` Antwort 1: **bestritten** — Die geforderte Fail-closed-Änderung betrifft src/agent_runtime.py beziehungsweise Slice-01-Tests und liegt außerhalb der persistierten Slice-02-Allowlist; dieser Slice erweitert keine Providerregistrierung.
- `C-02` Antwort 2: **bestritten** — Die Providerregistrierung in src/agent_runtime.py und deren Slice-01-Tests liegen außerhalb der aktuellen Slice-Allowlist; kein neuer Provider wurde eingeführt.
- `C-03` Antwort 1: **angenommen** — Ein Integrationstest erzwingt den Budgetdenial über ProductionWorkflowDriver und WorkflowEngine und prüft Gate, persistierten Checkpoint, identischen Resume-Step sowie idempotente Messrecords.
- `C-04` Antwort 1: **bestritten** — Die erforderliche Korrektur in tests/test_workflow_state.py liegt außerhalb der aktuellen Slice-Allowlist.

<!-- artifact-records:codex-responses:begin -->
Semantischer Record-Digest: `b733a57258a944f9f1b2e344a7868636be93f4de935595c345db455b71ad6f55`

- 5. `ar1-d3ba3985a408b266e552474d21d4e591b3305bfb306e250f17948a116f9362e7`: `C-01` `responded` durch `codex`; `OBSERVATION` / `open` — ACCEPTED: Ein Vollständigkeitstest prüft nun jede produktiv verdrahtete WorkflowStep-/Provider-Kombination gegen die Budgetpolicy.
- 6. `ar1-162724af8ce98256c84a4a83db5b563a8feda87d24ede9d13a82552234f07168`: `C-02` `responded` durch `codex`; `OBSERVATION` / `open` — REJECTED: Die geforderte Fail-closed-Änderung betrifft src/agent_runtime.py beziehungsweise Slice-01-Tests und liegt außerhalb der persistierten Slice-02-Allowlist; dieser Slice erweitert keine Providerregistrierung.
- 13. `ar1-b0a5c30c3ad8205f253270e8e57ccaf5dbc7fbda2e4e7e8eb10cdf35c8c8ff72`: `C-02` `responded` durch `codex`; `OBSERVATION` / `open` — REJECTED: Die Providerregistrierung in src/agent_runtime.py und deren Slice-01-Tests liegen außerhalb der aktuellen Slice-Allowlist; kein neuer Provider wurde eingeführt.
- 14. `ar1-ceb9db0640fe0a4f716858f4c220ce4f902c9be3a24f7105efb37ffc831b8e41`: `C-03` `responded` durch `codex`; `OBSERVATION` / `open` — ACCEPTED: Ein Integrationstest erzwingt den Budgetdenial über ProductionWorkflowDriver und WorkflowEngine und prüft Gate, persistierten Checkpoint, identischen Resume-Step sowie idempotente Messrecords.
- 15. `ar1-687554d9b72d9c2874899e699d325b1a00d078d44a463305191fdb84df075ffe`: `C-04` `responded` durch `codex`; `OBSERVATION` / `open` — REJECTED: Die erforderliche Korrektur in tests/test_workflow_state.py liegt außerhalb der aktuellen Slice-Allowlist.
<!-- artifact-records:codex-responses:end -->
<!-- audit:codex-responses:end -->

## Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### Ereignis 1: `validation-cec71b6c7ad9`

- Diff-Fingerprint: `cec71b6c7ad9553f184bb8af5bcd8bd01c1608f6ff9f25f90c95422be83858de`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: 1 passed; 0 failed; 0 unavailable; 1 required
- Ausgabedigest: `82920d591c90e317ef3c6e0a4d6c6f926a6d2aa7a6161f038b1da8892e5c3523`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| python3 -m pytest tests/ -v | PASS | 0 | ============================= test session starts ==============================<br>platform linux -- Python 3.12.3, pytest-7.4.4, pluggy-1.4.0 -- /usr/bin/python3<br>cachedir: .pytest_cache<br>rootdir: /mnt/c/Users/Diete/Sync/DE_Privat/Rente/ChatGPT CLI/Dual-Agent-Orchestrator<br>configfile: pyproject.toml<br>collecting ... collected 849 items<br><br>tests/test_agent_adapters.py::test_registry_contains_exact_role_identities PASSED [  0%]<br>tests/test_agent_adapters.py::test_codex_command_is_configured_workspace_write_jsonl_and_stdin PASSED [  0%]<br>tests/test_agent_adapters.py::test_prepared_codex_input_uses_the_exact_stdin_prompt PASSED [  0%]<br>tests/test_agent_adapters.py::test_codex_prefers_final_message_file_over_jsonl PASSED [  0%]<br>tests/test_agent_adapters.py::test_claude_defaults_are_quota_conscious_and_permissions_are_separate PASSED [  0%]<br>tests/test_agent_adapters.py::test_prepared_claude_input_is_lossless_and_includes_every_model_channel PASSED [  0%]<br>tests/test_agent_adapters.py::test_prepared_anti<br>...[95704 characters omitted]...<br>_pre_slice11_v3_gate_and_work_unit_shapes_load_with_empty_new_fields PASSED [ 99%]<br>tests/test_workflow_state.py::test_early_slice11_work_unit_shape_loads_without_active_test_evidence PASSED [ 99%]<br>tests/test_workflow_state.py::test_policy_gate_roundtrips_and_resumes_at_same_step PASSED [ 99%]<br>tests/test_workflow_state.py::test_managed_audit_path_roundtrips_and_rejects_unsafe_locations PASSED [ 99%]<br>tests/test_workflow_state.py::test_policy_gate_rejects_fingerprint_bound_reason_and_unsafe_path PASSED [ 99%]<br>tests/test_workflow_state.py::test_unexpected_file_user_gate_decision_roundtrips PASSED [ 99%]<br>tests/test_workflow_state.py::test_plan_time_slice_one_start_commit_cannot_be_rebound_after_resume PASSED [ 99%]<br>tests/test_workflow_state.py::test_in_progress_slice_can_extend_exact_remediation_scope PASSED [ 99%]<br>tests/test_workflow_state.py::test_state_scope_rejects_orchestrator_internal_paths PASSED [100%]<br><br>======================= 849 passed in 104.54s (0:01:44) ======================== |

### Ereignis 2: `validation-66e1cbe92b12`

- Diff-Fingerprint: `66e1cbe92b121f0bcc88b1ac3b3069bed0f573e7ebe74cfc0b64c048e2fb02ab`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: 1 passed; 0 failed; 0 unavailable; 1 required
- Ausgabedigest: `b674b9c0d0df3f178e7830d60ec44132653d74fa10b5db1a6000169ddc545276`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| python3 -m pytest tests/ -v | PASS | 0 | ============================= test session starts ==============================<br>platform linux -- Python 3.12.3, pytest-7.4.4, pluggy-1.4.0 -- /usr/bin/python3<br>cachedir: .pytest_cache<br>rootdir: /mnt/c/Users/Diete/Sync/DE_Privat/Rente/ChatGPT CLI/Dual-Agent-Orchestrator<br>configfile: pyproject.toml<br>collecting ... collected 849 items<br><br>tests/test_agent_adapters.py::test_registry_contains_exact_role_identities PASSED [  0%]<br>tests/test_agent_adapters.py::test_codex_command_is_configured_workspace_write_jsonl_and_stdin PASSED [  0%]<br>tests/test_agent_adapters.py::test_prepared_codex_input_uses_the_exact_stdin_prompt PASSED [  0%]<br>tests/test_agent_adapters.py::test_codex_prefers_final_message_file_over_jsonl PASSED [  0%]<br>tests/test_agent_adapters.py::test_claude_defaults_are_quota_conscious_and_permissions_are_separate PASSED [  0%]<br>tests/test_agent_adapters.py::test_prepared_claude_input_is_lossless_and_includes_every_model_channel PASSED [  0%]<br>tests/test_agent_adapters.py::test_prepared_anti<br>...[95704 characters omitted]...<br>_pre_slice11_v3_gate_and_work_unit_shapes_load_with_empty_new_fields PASSED [ 99%]<br>tests/test_workflow_state.py::test_early_slice11_work_unit_shape_loads_without_active_test_evidence PASSED [ 99%]<br>tests/test_workflow_state.py::test_policy_gate_roundtrips_and_resumes_at_same_step PASSED [ 99%]<br>tests/test_workflow_state.py::test_managed_audit_path_roundtrips_and_rejects_unsafe_locations PASSED [ 99%]<br>tests/test_workflow_state.py::test_policy_gate_rejects_fingerprint_bound_reason_and_unsafe_path PASSED [ 99%]<br>tests/test_workflow_state.py::test_unexpected_file_user_gate_decision_roundtrips PASSED [ 99%]<br>tests/test_workflow_state.py::test_plan_time_slice_one_start_commit_cannot_be_rebound_after_resume PASSED [ 99%]<br>tests/test_workflow_state.py::test_in_progress_slice_can_extend_exact_remediation_scope PASSED [ 99%]<br>tests/test_workflow_state.py::test_state_scope_rejects_orchestrator_internal_paths PASSED [100%]<br><br>======================= 849 passed in 103.53s (0:01:43) ======================== |

### Ereignis 5: `validation-98ee175b02a4`

- Diff-Fingerprint: `98ee175b02a44bda45135650d1ce51465fae1e4c5f09c74f0e95c997015be25c`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: 1 passed; 0 failed; 0 unavailable; 1 required
- Ausgabedigest: `7638183c22f1c68a66bb70a44c23403d17805fa5ec20e221c71e6347d4926075`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| python3 -m pytest tests/ -v | PASS | 0 | ============================= test session starts ==============================<br>platform linux -- Python 3.12.3, pytest-7.4.4, pluggy-1.4.0 -- /usr/bin/python3<br>cachedir: .pytest_cache<br>rootdir: /mnt/c/Users/Diete/Sync/DE_Privat/Rente/ChatGPT CLI/Dual-Agent-Orchestrator<br>configfile: pyproject.toml<br>collecting ... collected 852 items<br><br>tests/test_agent_adapters.py::test_registry_contains_exact_role_identities PASSED [  0%]<br>tests/test_agent_adapters.py::test_codex_command_is_configured_workspace_write_jsonl_and_stdin PASSED [  0%]<br>tests/test_agent_adapters.py::test_prepared_codex_input_uses_the_exact_stdin_prompt PASSED [  0%]<br>tests/test_agent_adapters.py::test_codex_prefers_final_message_file_over_jsonl PASSED [  0%]<br>tests/test_agent_adapters.py::test_claude_defaults_are_quota_conscious_and_permissions_are_separate PASSED [  0%]<br>tests/test_agent_adapters.py::test_prepared_claude_input_is_lossless_and_includes_every_model_channel PASSED [  0%]<br>tests/test_agent_adapters.py::test_prepared_anti<br>...[96044 characters omitted]...<br>_pre_slice11_v3_gate_and_work_unit_shapes_load_with_empty_new_fields PASSED [ 99%]<br>tests/test_workflow_state.py::test_early_slice11_work_unit_shape_loads_without_active_test_evidence PASSED [ 99%]<br>tests/test_workflow_state.py::test_policy_gate_roundtrips_and_resumes_at_same_step PASSED [ 99%]<br>tests/test_workflow_state.py::test_managed_audit_path_roundtrips_and_rejects_unsafe_locations PASSED [ 99%]<br>tests/test_workflow_state.py::test_policy_gate_rejects_fingerprint_bound_reason_and_unsafe_path PASSED [ 99%]<br>tests/test_workflow_state.py::test_unexpected_file_user_gate_decision_roundtrips PASSED [ 99%]<br>tests/test_workflow_state.py::test_plan_time_slice_one_start_commit_cannot_be_rebound_after_resume PASSED [ 99%]<br>tests/test_workflow_state.py::test_in_progress_slice_can_extend_exact_remediation_scope PASSED [ 99%]<br>tests/test_workflow_state.py::test_state_scope_rejects_orchestrator_internal_paths PASSED [100%]<br><br>======================= 852 passed in 105.38s (0:01:45) ======================== |

<!-- artifact-records:validation-attestation:begin -->
Semantischer Record-Digest: `b733a57258a944f9f1b2e344a7868636be93f4de935595c345db455b71ad6f55`

- 11. `ar1-f3bc61129e11eaf4e3336a932cfbd92a5c288dd570ca8f05c153ba2128c7477b`: Providerinput `codex/codex_implementation` = `allowed`; Zeichen `21250/4000000`, Bytes `21272/16000000`; Input `90f2e37c3d657d498e0f91f2eae2e7500d6230f0ef175c090cf8738ec7ef9461`, Policy `8cff2b572a65674a0b31c2e2751cdc76fa839112b05e38b09bd021a02284bc83`, Übergang `4d7be5802df3048d572b448e637278477686dfbef3b8dffb4170a0b805a79df8`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, größte Komponente `stdin_prompt`; Komponenten `stdin_prompt=21250/21272`
- 16. `ar1-03b32fc95a6d9a5dff0d184bcb2845b6e28ee6195860aadfaa38054240f496f4`: Providerinput `claude/claude_slice_review` = `allowed`; Zeichen `130760/4000000`, Bytes `131084/16000000`; Input `54aae19190999f0ac9589e8b43ec98ba1511c87d1a935779101c084a8afa125c`, Policy `8cff2b572a65674a0b31c2e2751cdc76fa839112b05e38b09bd021a02284bc83`, Übergang `666eed5486748236a4571e34d10508b508c19e3fc3439c08b16b562181ae1f87`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, größte Komponente `packet_chunk_005`; Komponenten `packet_chunk_001=23894/23957, packet_chunk_002=23860/23985, packet_chunk_003=23998/24027, packet_chunk_004=23988/23991, packet_chunk_005=23986/24089, packet_chunk_006=9117/9118, packet_manifest=802/802, system_policy=660/660, response_schema=146/146, start_directive=309/309`
- 17. `ar1-76989ba591822d896caa7797a7c68f894a8bd2e1f9c5673081a1c4f36be68094`: Providerinput `claude/claude_slice_review` = `allowed`; Zeichen `138893/4000000`, Bytes `139227/16000000`; Input `7060e943063082ac197a7b99ead3c9691b94d26ba49152eb1207508fe281f133`, Policy `8cff2b572a65674a0b31c2e2751cdc76fa839112b05e38b09bd021a02284bc83`, Übergang `614a488bc11d90ea55d8198005d5d0c90246a1aebe066d9fffc2693c1ad58ec6`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, größte Komponente `packet_chunk_003`; Komponenten `packet_chunk_001=23894/23957, packet_chunk_002=23154/23253, packet_chunk_003=23988/24048, packet_chunk_004=23976/23976, packet_chunk_005=22508/22561, packet_chunk_006=19455/19514, packet_manifest=803/803, system_policy=660/660, response_schema=146/146, start_directive=309/309`
- 19. `ar1-165957d881833a55ef5599f3ec0eadaa16d4778f28486da656032d2636451125`: Anforderung durch `orchestrator`: `argv` [`python3`, `-m`, `pytest`, `tests/`, `-v`]
- 20. `ar1-2f27e83a4fbe4a944556265bf5c5b52bdb33bb3e1a475e3a438f14e850842cd1`: Attestierung durch `orchestrator`; Fingerprint `66e1cbe92b121f0bcc88b1ac3b3069bed0f573e7ebe74cfc0b64c048e2fb02ab`
  - `pass` / Exit `0` / Output `a984f3b8a81ff77893867f9a5333d52946a1b75d413d907f1b89f94b88475014`: `argv` [`python3`, `-m`, `pytest`, `tests/`, `-v`]
- 21. `ar1-ed7b0b995d26af1a04a8084c36be1aac12559c0a0bce3d3acfde5f3f76ddf969`: Providerinput `claude/claude_slice_review` = `allowed`; Zeichen `146700/4000000`, Bytes `147048/16000000`; Input `d4c864854e36e2cfc94f5f6ad3e4789e47d46a0a4e4fafa086e8e851c3d49b82`, Policy `8cff2b572a65674a0b31c2e2751cdc76fa839112b05e38b09bd021a02284bc83`, Übergang `b6f572330bd3b516fc31e049766af39f2f538b38a7ce669e523be255ae1cd691`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, größte Komponente `packet_chunk_003`; Komponenten `packet_chunk_001=23894/23957, packet_chunk_002=23612/23721, packet_chunk_003=23956/24011, packet_chunk_004=23924/23929, packet_chunk_005=23973/23994, packet_chunk_006=23742/23837, packet_chunk_007=1305/1305, packet_manifest=1146/1146, system_policy=660/660, response_schema=146/146, start_directive=342/342`
- 24. `ar1-d4c362b708a3a7952286f983753d23f478660754bd0e5d20c4b148cfeccc70ed`: Providerinput `antigravity/antigravity_slice_review` = `allowed`; Zeichen `178234/4000000`, Bytes `178688/16000000`; Input `527a5e196e6fcab4ae923cca85eee1f6d701ffd9b9940eabd0838486068b8d30`, Policy `8cff2b572a65674a0b31c2e2751cdc76fa839112b05e38b09bd021a02284bc83`, Übergang `f8505e4f17ac2540ba7cece66170ea8c904a8a8b88ad4dda3d823e2f03f77fce`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, größte Komponente `prompt_file`; Komponenten `prompt_file=177575/178029, response_schema=146/146, start_directive=513/513`
- 25. `ar1-ec34d50fb622fcd84b0b41711e6251d90d390d4e51cfeee56e5fc0f3d0a4a91f`: Providerinput `antigravity/antigravity_slice_review` = `allowed`; Zeichen `180012/4000000`, Bytes `180474/16000000`; Input `723f062f1e55bff4581567afc6a98ac68b62c0547e7c3cd0def6c770d087e671`, Policy `8cff2b572a65674a0b31c2e2751cdc76fa839112b05e38b09bd021a02284bc83`, Übergang `bf34180785508e3f88b3ef713b37d62cf63996fc06b04a256908b17c8ce7560e`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, größte Komponente `prompt_file`; Komponenten `prompt_file=179353/179815, response_schema=146/146, start_directive=513/513`
- 28. `ar1-3728bc162125096e3d7c1a9a966e7264d4b36ebd7cd4755cb8013cf631b890f7`: Anforderung durch `orchestrator`: `argv` [`python3`, `-m`, `pytest`, `tests/`, `-v`]
- 29. `ar1-277bf424147cb9015bf747146c85ad00d1282d6112c3e3851f5aac0607e52b51`: Attestierung durch `orchestrator`; Fingerprint `98ee175b02a44bda45135650d1ce51465fae1e4c5f09c74f0e95c997015be25c`
  - `pass` / Exit `0` / Output `7102fd1dc7e4e0c5d8a464961ec7ca8115b48112838e6f6b70034606cfe93609`: `argv` [`python3`, `-m`, `pytest`, `tests/`, `-v`]
- 30. `ar1-5e4fe26c84aab5691df0d67c6fa729b6006688aa69d73992ea10ce6f503bc665`: Providerinput `claude/claude_slice_review` = `allowed`; Zeichen `208302/4000000`, Bytes `208783/16000000`; Input `fefb51569db7de934c96540b6f4a9b3b5a6512976173a5fdafc95bd0bcf669e9`, Policy `8cff2b572a65674a0b31c2e2751cdc76fa839112b05e38b09bd021a02284bc83`, Übergang `710c6fa1d0ff9e4d048f4673e74dd6cae173b6f71085885470d11f52924456c0`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, größte Komponente `packet_chunk_007`; Komponenten `packet_chunk_001=23923/23986, packet_chunk_002=23912/24009, packet_chunk_003=23882/24003, packet_chunk_004=23924/23940, packet_chunk_005=23949/23950, packet_chunk_006=23997/23997, packet_chunk_007=23936/24011, packet_chunk_008=23781/23878, packet_chunk_009=14409/14420, packet_manifest=1439/1439, system_policy=661/661, response_schema=146/146, start_directive=343/343`
- 32. `ar1-4ffbc29beea8682f0c20b027194304f00446eec1d4126c73c2c52d593e771619`: Providerinput `claude/claude_slice_review` = `allowed`; Zeichen `14412/4000000`, Bytes `14424/16000000`; Input `596b8ad5393234e801058eee7dfc05acdc3bf44b8e80d0e6124721ad6b6c6571`, Policy `8cff2b572a65674a0b31c2e2751cdc76fa839112b05e38b09bd021a02284bc83`, Übergang `e27fa6429a1b7ab164b7a90bb1b2e1a25b6eb70ad35b7ce5e68709339e29a51e`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, größte Komponente `packet_chunk_001`; Komponenten `packet_chunk_001=12993/13005, packet_manifest=271/271, system_policy=660/660, response_schema=146/146, start_directive=342/342`
- 35. `ar1-1d9a239f496009bb09bbd7e2d430f9170e64efd6067f98c26324d6c20852a9b5`: Providerinput `antigravity/antigravity_slice_review` = `allowed`; Zeichen `232614/4000000`, Bytes `233142/16000000`; Input `6b296a34ce844e45a961b7cb9e6f6f6f59f53fe1fc17f2b619c6843a51b7fc93`, Policy `8cff2b572a65674a0b31c2e2751cdc76fa839112b05e38b09bd021a02284bc83`, Übergang `6a5ab00a9d4703349f596eb73c9c3f0989bfdedb83804aea5ecc27fbe4ebb75d`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, größte Komponente `prompt_file`; Komponenten `prompt_file=231955/232483, response_schema=146/146, start_directive=513/513`
<!-- artifact-records:validation-attestation:end -->
<!-- audit:validation-attestation:end -->

## Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
- Teständerungsfreigabe: nicht erfasst.
- Pre-Mortems:
  - Ereignis 3: In three months, the most likely failure is exactly the C-05 scenario — someone refactors &#96;quota_resume_diff_acknowledgement&#96; or the tuple-literal &#96;acknowledged&#96; expression in &#96;_revalidate_waiting_diff&#96; in isolation (they live in different files/functions with no shared type contract visible in either diff), silently breaking the write/read match; a QUOTA-RESUME-DIFF gate would then never clear even after correct human approval, forcing every quota-interrupted run with a coincidental repo change to loop on the same gate indefinitely until someone traces the mismatch by hand.
  - Ereignis 4: In three months, the most likely failure cause is an extension of the inbox watcher or queue management that introduces a new non-zero exit code or gate reason for automated checks, but neglects to explicitly handle the exit-4 / BOOTSTRAP_CHECK invariant in WatchTaskResult.__post_init__, triggering a ValueError during queue dispatch.
  - Ereignis 6: In three months, the most likely failure is that a future gated correction approves &#96;approved_external_paths&#96; for one repository state, but an additional unreviewed edit lands on one of those already-approved external paths (or a genuinely new external path) before the slice commit executes; because the only regression coverage for this authorization path is the single happy-path test, this silent divergence between "what was reviewed" and "what commit_slice is about to commit" is not caught by an explicit failing test today, and would surface only if a maintainer manually traces a GitTransactionError or, worse, an over-permissive path match.
  - Ereignis 7: In three months, the most likely failure cause is an update to the host OS or container environment that introduces a non-standard zoneinfo path or stripped timezone symlink format not recognized by _system_local_timezone, causing Codex dated local reset timestamps to fail parsing and fall back to manual exit 2 instead of automatic quota sleep.

<!-- artifact-records:test-approval-premortem:begin -->
Semantischer Record-Digest: `b733a57258a944f9f1b2e344a7868636be93f4de935595c345db455b71ad6f55`

- 18. `ar1-57b1f7e036e2ba063a5be809f422e4426db0b978fd6829e8fdfa0e8a57fe6e70`: `quota-resume-diff` = `approved` durch `user`; Fingerprint `66e1cbe92b121f0bcc88b1ac3b3069bed0f573e7ebe74cfc0b64c048e2fb02ab` — Geprüfte Orchestrator-Korrekturen: Claude liest Review-Manifest und Chunks über absolute Pfade;<br>    die exakte Resume-Diff-Freigabe gilt auch für den unmittelbar folgenden Scope-Check; vollständige Suite mit 849<br>    Tests bestanden.
- 27. `ar1-2fc9147763e9559b459f51ce9405d25314bdd1d5f472354566898914833e0f4b`: `unexpected-file` = `approved` durch `user`; Fingerprint `98ee175b02a44bda45135650d1ce51465fae1e4c5f09c74f0e95c997015be25c` — Geprüfte Bootstrap-Korrekturen: freigegebene Zwischen-Commits werden sicher übernommen, veraltete<br>    Commit-Reviews automatisch neu validiert und entfernte Antigravity-Shellfehler begrenzt wiederholt; vollständige<br>    Suite mit 852 Tests bestanden.
<!-- artifact-records:test-approval-premortem:end -->
<!-- audit:test-approval-premortem:end -->

## Findings-Lebenszyklus

<!-- audit:findings:begin -->
### `C-01` — `CLOSED`

- Quelle: `claude`; Runde 1
- Klasse: `OBSERVATION`
- Finding: The &#96;operation&#96; strings passed from &#96;orchestrator.py&#96; (&#96;invocation.step.value&#96;) into &#96;run_agent&#96;/&#96;measure_provider_input&#96; are matched against &#96;provider_input_budget.PROVIDER_OPERATIONS&#96; only implicitly, via the fact that the full suite currently passes; there is no dedicated completeness test asserting that every &#96;WorkflowStep&#96; value used at each &#96;invoke_codex&#96;/&#96;invoke_reviewer&#96;/&#96;repair_review_contract&#96; call site is a member of &#96;PROVIDER_OPERATIONS[provider]&#96;. A future renamed or added &#96;WorkflowStep&#96; without a matching budget-table entry would make &#96;ProviderInputBudgetPolicy.select()&#96; raise an unclassified &#96;ProviderInputBudgetError&#96; (not the deterministic &#96;ProviderInputBudgetExceeded&#96; denial path), propagating as an unclassified crash instead of a clean, documented bootstrap denial.
- Akzeptanztest: Add a unit test (e.g. in tests/test_orchestrator_runtime.py or tests/test_provider_input_budget.py, whichever slice owns orchestrator wiring next) that iterates every WorkflowStep value used for codex/claude/antigravity invocations and asserts each resolves via &#96;default_provider_input_budget_policy().select(provider, provider, step.value)&#96; without raising.
- Statusbegründung: Acceptance test added exactly as specified and verified in the bound 837-test attestation; no remaining gap.

### `C-02` — `OPEN`

- Quelle: `claude`; Runde 1
- Klasse: `OBSERVATION`
- Finding: In &#96;run_agent&#96;, budget measurement/enforcement is gated by &#96;if agent_key in PROVIDER_OPERATIONS&#96;, i.e. by adapter &#96;.name&#96; string equality; an adapter whose &#96;.name&#96; is not literally "codex"/"claude"/"antigravity" (a future fourth provider, or a renamed adapter) silently skips the input-size barrier entirely rather than failing closed, which is inconsistent with the "resume is fail-closed" posture stated for structured artifacts elsewhere in the contract. Currently low risk since only these three adapters exist and are exercised.
- Akzeptanztest: When a new provider/adapter is introduced, require either its explicit inclusion in &#96;PROVIDER_OPERATIONS&#96; before it can run unmeasured, or an assertion in &#96;run_agent&#96;/its tests that every registered adapter name is a key of &#96;PROVIDER_OPERATIONS&#96;.
- Statusbegründung: Carried forward unchanged; the current review supplied no explicit status update.

### `C-03` — `CLOSED`

- Quelle: `claude`; Runde 1
- Klasse: `OBSERVATION`
- Finding: The riskiest new logic — &#96;orchestrator.py::_persist_provider_bootstrap&#96;'s ordered dual-write (measurement → state mirror → preflight → state mirror) and &#96;workflow.py&#96;'s new &#96;except (ProviderInputBudgetExceeded, FinalReviewPreflightDenied)&#96; branch that sets &#96;GateReason.BOOTSTRAP_CHECK&#96; and checkpoints — is only exercised indirectly by pre-existing happy-path integration tests; no test forces an actual denial through the real driver/engine to confirm the gate, checkpoint, and idempotent resume/retry behavior.
- Akzeptanztest: Add an integration test (tests/test_orchestrator_runtime.py or tests/test_workflow.py) that forces &#96;ProviderInputBudgetExceeded&#96; or &#96;FinalReviewPreflightDenied&#96; through &#96;ProductionWorkflowDriver&#96;/&#96;WorkflowEngine&#96;'s real invocation path and asserts the resulting state has &#96;current_work_unit.gate.reason is GateReason.BOOTSTRAP_CHECK&#96;, a checkpoint was written, and resuming does not duplicate the already-persisted measurement/preflight records.
- Statusbegründung: Acceptance test delivered exactly as specified (&#96;test_budget_denial_persists_gate_checkpoint_and_resumes_idempotently&#96;): forces the real driver/engine denial path, asserts &#96;bootstrap_check&#96; gate, persisted checkpoint, and idempotent single measurement record across resume+retry. Verified in the bound 849-test attestation.

### `C-04` — `OPEN`

- Quelle: `claude`; Runde 1
- Klasse: `OBSERVATION`
- Finding: In tests/test_workflow_state.py, the new test &#96;test_bootstrap_facts_roundtrip_idempotently_and_use_a_resume_gate&#96; was spliced in without a blank line before the trailing three assertions of &#96;test_init_workflow_state_uses_v3_and_one_based_ids&#96;, so those assertions (&#96;max_codex_returns&#96;, &#96;gate.status&#96;, &#96;branch_base&#96;) now silently run inside the new function against its own unrelated &#96;state&#96; object; the original test lost that coverage and the new test carries confusing unrelated assertions.
- Akzeptanztest: Separate the two test bodies with a blank line so each function only asserts its own invariants, and confirm &#96;test_init_workflow_state_uses_v3_and_one_based_ids&#96; still independently asserts &#96;max_codex_returns&#96;, &#96;gate.status is CLEAR&#96;, and &#96;branch_base&#96;.
- Statusbegründung: Carried forward unchanged; the current review supplied no explicit status update.

### `C-05` — `OPEN`

- Quelle: `claude`; Runde 2
- Klasse: `OBSERVATION`
- Finding: The new HEAD-drift commit-authorization path in &#96;commit_slice&#96;/&#96;CommitAuthorization&#96; (approved_head_commit + approved_external_paths, ancestry check, exact-set path check) is a security-sensitive loosening of the prior absolute "HEAD must equal persisted start_commit" invariant, but only one happy-path integration test exists (&#96;test_commit_accepts_exactly_approved_descendant_head_and_commits_only_worktree&#96; in tests/test_git_service.py). No visible test forces the negative paths: (a) HEAD advanced but &#96;authorization.approved_head_commit&#96; is stale/mismatched, (b) &#96;start_commit&#96; is not an ancestor of HEAD (e.g. after a rewrite/reset), or (c) &#96;authorization.approved_external_paths&#96; under- or over-approves the actual unexpected paths in the reviewed diff. Any of these should still raise &#96;GitTransactionError&#96;, but that is currently only implied by code reading, not proven by a test.
- Akzeptanztest: Add negative-path tests in tests/test_git_service.py asserting &#96;commit_slice&#96; raises &#96;GitTransactionError&#96; for: a HEAD descendant with a stale/mismatched &#96;approved_head_commit&#96;; a &#96;start_commit&#96; that is not an ancestor of the current HEAD; and an &#96;approved_external_paths&#96; set that does not exactly equal the unexpected out-of-scope paths present in the reviewed diff.
- Statusbegründung: –

<!-- artifact-records:findings:begin -->
Semantischer Record-Digest: `b733a57258a944f9f1b2e344a7868636be93f4de935595c345db455b71ad6f55`

- 3. `ar1-3121291e37bc1283b6c57f3629012135baa9d4516dc9d1fd44d19fb21da57da3`: `C-01` `opened` durch `claude`; `OBSERVATION` / `open` — The &#96;operation&#96; strings passed from &#96;orchestrator.py&#96; (&#96;invocation.step.value&#96;) into &#96;run_agent&#96;/&#96;measure_provider_input&#96; are matched against &#96;provider_input_budget.PROVIDER_OPERATIONS&#96; only implicitly, via the fact that the full suite currently passes; there is no dedicated completeness test asserting that every &#96;WorkflowStep&#96; value used at each &#96;invoke_codex&#96;/&#96;invoke_reviewer&#96;/&#96;repair_review_contract&#96; call site is a member of &#96;PROVIDER_OPERATIONS[provider]&#96;. A future renamed or added &#96;WorkflowStep&#96; without a matching budget-table entry would make &#96;ProviderInputBudgetPolicy.select()&#96; raise an unclassified &#96;ProviderInputBudgetError&#96; (not the deterministic &#96;ProviderInputBudgetExceeded&#96; denial path), propagating as an unclassified crash instead of a clean, documented bootstrap denial.
- 4. `ar1-2b574429ae4fea33bf33f3b8b976f5cd31d022531f08102bb522cda29f005407`: `C-02` `opened` durch `claude`; `OBSERVATION` / `open` — In &#96;run_agent&#96;, budget measurement/enforcement is gated by &#96;if agent_key in PROVIDER_OPERATIONS&#96;, i.e. by adapter &#96;.name&#96; string equality; an adapter whose &#96;.name&#96; is not literally "codex"/"claude"/"antigravity" (a future fourth provider, or a renamed adapter) silently skips the input-size barrier entirely rather than failing closed, which is inconsistent with the "resume is fail-closed" posture stated for structured artifacts elsewhere in the contract. Currently low risk since only these three adapters exist and are exercised.
- 5. `ar1-d3ba3985a408b266e552474d21d4e591b3305bfb306e250f17948a116f9362e7`: `C-01` `responded` durch `codex`; `OBSERVATION` / `open` — ACCEPTED: Ein Vollständigkeitstest prüft nun jede produktiv verdrahtete WorkflowStep-/Provider-Kombination gegen die Budgetpolicy.
- 6. `ar1-162724af8ce98256c84a4a83db5b563a8feda87d24ede9d13a82552234f07168`: `C-02` `responded` durch `codex`; `OBSERVATION` / `open` — REJECTED: Die geforderte Fail-closed-Änderung betrifft src/agent_runtime.py beziehungsweise Slice-01-Tests und liegt außerhalb der persistierten Slice-02-Allowlist; dieser Slice erweitert keine Providerregistrierung.
- 7. `ar1-961986629c9ccb743fbfb70ea6557f11271adf9ff50571ba5c7fe29b3bde9850`: `C-01` `status_changed` durch `claude`; `OBSERVATION` / `closed` — Acceptance test added exactly as specified and verified in the bound 837-test attestation; no remaining gap.
- 8. `ar1-c6e141941263d09176d3a1c95a0614fb997bb7d0e3fa3f744d93956713c1624a`: `C-03` `opened` durch `claude`; `OBSERVATION` / `open` — The riskiest new logic — &#96;orchestrator.py::_persist_provider_bootstrap&#96;'s ordered dual-write (measurement → state mirror → preflight → state mirror) and &#96;workflow.py&#96;'s new &#96;except (ProviderInputBudgetExceeded, FinalReviewPreflightDenied)&#96; branch that sets &#96;GateReason.BOOTSTRAP_CHECK&#96; and checkpoints — is only exercised indirectly by pre-existing happy-path integration tests; no test forces an actual denial through the real driver/engine to confirm the gate, checkpoint, and idempotent resume/retry behavior.
- 9. `ar1-cdc9cc8c85099c5156ac6418872c891778d3b8d9c7595d61f90ccd8b7e181715`: `C-04` `opened` durch `claude`; `OBSERVATION` / `open` — In tests/test_workflow_state.py, the new test &#96;test_bootstrap_facts_roundtrip_idempotently_and_use_a_resume_gate&#96; was spliced in without a blank line before the trailing three assertions of &#96;test_init_workflow_state_uses_v3_and_one_based_ids&#96;, so those assertions (&#96;max_codex_returns&#96;, &#96;gate.status&#96;, &#96;branch_base&#96;) now silently run inside the new function against its own unrelated &#96;state&#96; object; the original test lost that coverage and the new test carries confusing unrelated assertions.
- 13. `ar1-b0a5c30c3ad8205f253270e8e57ccaf5dbc7fbda2e4e7e8eb10cdf35c8c8ff72`: `C-02` `responded` durch `codex`; `OBSERVATION` / `open` — REJECTED: Die Providerregistrierung in src/agent_runtime.py und deren Slice-01-Tests liegen außerhalb der aktuellen Slice-Allowlist; kein neuer Provider wurde eingeführt.
- 14. `ar1-ceb9db0640fe0a4f716858f4c220ce4f902c9be3a24f7105efb37ffc831b8e41`: `C-03` `responded` durch `codex`; `OBSERVATION` / `open` — ACCEPTED: Ein Integrationstest erzwingt den Budgetdenial über ProductionWorkflowDriver und WorkflowEngine und prüft Gate, persistierten Checkpoint, identischen Resume-Step sowie idempotente Messrecords.
- 15. `ar1-687554d9b72d9c2874899e699d325b1a00d078d44a463305191fdb84df075ffe`: `C-04` `responded` durch `codex`; `OBSERVATION` / `open` — REJECTED: Die erforderliche Korrektur in tests/test_workflow_state.py liegt außerhalb der aktuellen Slice-Allowlist.
- 23. `ar1-1070eb1f5d2222e41cff85f2d2b76c1954dadddcf0d9d9755a2b90f4be8e9085`: `C-03` `status_changed` durch `claude`; `OBSERVATION` / `closed` — Acceptance test delivered exactly as specified (&#96;test_budget_denial_persists_gate_checkpoint_and_resumes_idempotently&#96;): forces the real driver/engine denial path, asserts &#96;bootstrap_check&#96; gate, persisted checkpoint, and idempotent single measurement record across resume+retry. Verified in the bound 849-test attestation.
- 34. `ar1-1596965048407887bf205e6d66910e9f1b5492683d7f177665c8bd17f1bcb52e`: `C-05` `opened` durch `claude`; `OBSERVATION` / `open` — The new HEAD-drift commit-authorization path in &#96;commit_slice&#96;/&#96;CommitAuthorization&#96; (approved_head_commit + approved_external_paths, ancestry check, exact-set path check) is a security-sensitive loosening of the prior absolute "HEAD must equal persisted start_commit" invariant, but only one happy-path integration test exists (&#96;test_commit_accepts_exactly_approved_descendant_head_and_commits_only_worktree&#96; in tests/test_git_service.py). No visible test forces the negative paths: (a) HEAD advanced but &#96;authorization.approved_head_commit&#96; is stale/mismatched, (b) &#96;start_commit&#96; is not an ancestor of HEAD (e.g. after a rewrite/reset), or (c) &#96;authorization.approved_external_paths&#96; under- or over-approves the actual unexpected paths in the reviewed diff. Any of these should still raise &#96;GitTransactionError&#96;, but that is currently only implied by code reading, not proven by a test.
<!-- artifact-records:findings:end -->
<!-- audit:findings:end -->

## Entscheidungstabelle

<!-- audit:decision-table:begin -->
| ID | Quelle | Finding | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|---|
| C-01 | claude | The &#96;operation&#96; strings passed from &#96;orchestrator.py&#96; (&#96;invocation.step.value&#96;) into &#96;run_agent&#96;/&#96;measure_provider_input&#96; are matched against &#96;provider_input_budget.PROVIDER_OPERATIONS&#96; only implicitly, via the fact that the full suite currently passes; there is no dedicated completeness test asserting that every &#96;WorkflowStep&#96; value used at each &#96;invoke_codex&#96;/&#96;invoke_reviewer&#96;/&#96;repair_review_contract&#96; call site is a member of &#96;PROVIDER_OPERATIONS[provider]&#96;. A future renamed or added &#96;WorkflowStep&#96; without a matching budget-table entry would make &#96;ProviderInputBudgetPolicy.select()&#96; raise an unclassified &#96;ProviderInputBudgetError&#96; (not the deterministic &#96;ProviderInputBudgetExceeded&#96; denial path), propagating as an unclassified crash instead of a clean, documented bootstrap denial. | OBSERVATION | angenommen | erledigt: Acceptance test added exactly as specified and verified in the bound 837-test attestation; no remaining gap. |
| C-02 | claude | In &#96;run_agent&#96;, budget measurement/enforcement is gated by &#96;if agent_key in PROVIDER_OPERATIONS&#96;, i.e. by adapter &#96;.name&#96; string equality; an adapter whose &#96;.name&#96; is not literally "codex"/"claude"/"antigravity" (a future fourth provider, or a renamed adapter) silently skips the input-size barrier entirely rather than failing closed, which is inconsistent with the "resume is fail-closed" posture stated for structured artifacts elsewhere in the contract. Currently low risk since only these three adapters exist and are exercised. | OBSERVATION | bestritten | offen |
| C-03 | claude | The riskiest new logic — &#96;orchestrator.py::_persist_provider_bootstrap&#96;'s ordered dual-write (measurement → state mirror → preflight → state mirror) and &#96;workflow.py&#96;'s new &#96;except (ProviderInputBudgetExceeded, FinalReviewPreflightDenied)&#96; branch that sets &#96;GateReason.BOOTSTRAP_CHECK&#96; and checkpoints — is only exercised indirectly by pre-existing happy-path integration tests; no test forces an actual denial through the real driver/engine to confirm the gate, checkpoint, and idempotent resume/retry behavior. | OBSERVATION | angenommen | erledigt: Acceptance test delivered exactly as specified (&#96;test_budget_denial_persists_gate_checkpoint_and_resumes_idempotently&#96;): forces the real driver/engine denial path, asserts &#96;bootstrap_check&#96; gate, persisted checkpoint, and idempotent single measurement record across resume+retry. Verified in the bound 849-test attestation. |
| C-04 | claude | In tests/test_workflow_state.py, the new test &#96;test_bootstrap_facts_roundtrip_idempotently_and_use_a_resume_gate&#96; was spliced in without a blank line before the trailing three assertions of &#96;test_init_workflow_state_uses_v3_and_one_based_ids&#96;, so those assertions (&#96;max_codex_returns&#96;, &#96;gate.status&#96;, &#96;branch_base&#96;) now silently run inside the new function against its own unrelated &#96;state&#96; object; the original test lost that coverage and the new test carries confusing unrelated assertions. | OBSERVATION | bestritten | offen |
| C-05 | claude | The new HEAD-drift commit-authorization path in &#96;commit_slice&#96;/&#96;CommitAuthorization&#96; (approved_head_commit + approved_external_paths, ancestry check, exact-set path check) is a security-sensitive loosening of the prior absolute "HEAD must equal persisted start_commit" invariant, but only one happy-path integration test exists (&#96;test_commit_accepts_exactly_approved_descendant_head_and_commits_only_worktree&#96; in tests/test_git_service.py). No visible test forces the negative paths: (a) HEAD advanced but &#96;authorization.approved_head_commit&#96; is stale/mismatched, (b) &#96;start_commit&#96; is not an ancestor of HEAD (e.g. after a rewrite/reset), or (c) &#96;authorization.approved_external_paths&#96; under- or over-approves the actual unexpected paths in the reviewed diff. Any of these should still raise &#96;GitTransactionError&#96;, but that is currently only implied by code reading, not proven by a test. | OBSERVATION | offen | offen |

<!-- artifact-records:decision-table:begin -->
Semantischer Record-Digest: `b733a57258a944f9f1b2e344a7868636be93f4de935595c345db455b71ad6f55`

| Seq | Record | Typ | Status | Logische ID | Revision | Fingerprint |
|---:|---|---|---|---|---:|---|
| 1 | `ar1-d0eb48a06ed2b09769b6622790f1ee6bcb2377b119856a855f2d5755f375394e` | `task` | `accepted` | `task-contract` | 1 | `contract:cc74561600c77a09b47c2bb242de11f06e124ac07bb2662254dbcc1dd85b2486` |
| 2 | `ar1-de990abacf5e5ae5c5f55896c880f4633c2395c5f7ce290acecd021c3dcf13ff` | `plan` | `approved` | `approved-plan` | 1 | `contract:cc74561600c77a09b47c2bb242de11f06e124ac07bb2662254dbcc1dd85b2486` |
| 3 | `ar1-3121291e37bc1283b6c57f3629012135baa9d4516dc9d1fd44d19fb21da57da3` | `finding_transition` | `recorded` | `finding-C-01` | 1 | `implementation:c1dab0d2d3e8f99950f3dfcddf4158991f6237a78838e4129e3ccf9522baf8cd` |
| 4 | `ar1-2b574429ae4fea33bf33f3b8b976f5cd31d022531f08102bb522cda29f005407` | `finding_transition` | `recorded` | `finding-C-02` | 1 | `implementation:c1dab0d2d3e8f99950f3dfcddf4158991f6237a78838e4129e3ccf9522baf8cd` |
| 5 | `ar1-d3ba3985a408b266e552474d21d4e591b3305bfb306e250f17948a116f9362e7` | `finding_transition` | `recorded` | `finding-C-01` | 2 | `implementation:334c223f32dfe813ad4f706a9692841be88dbddecfe22cecca54155d1e66e78b` |
| 6 | `ar1-162724af8ce98256c84a4a83db5b563a8feda87d24ede9d13a82552234f07168` | `finding_transition` | `recorded` | `finding-C-02` | 2 | `implementation:334c223f32dfe813ad4f706a9692841be88dbddecfe22cecca54155d1e66e78b` |
| 7 | `ar1-961986629c9ccb743fbfb70ea6557f11271adf9ff50571ba5c7fe29b3bde9850` | `finding_transition` | `recorded` | `finding-C-01` | 3 | `implementation:334c223f32dfe813ad4f706a9692841be88dbddecfe22cecca54155d1e66e78b` |
| 8 | `ar1-c6e141941263d09176d3a1c95a0614fb997bb7d0e3fa3f744d93956713c1624a` | `finding_transition` | `recorded` | `finding-C-03` | 1 | `implementation:334c223f32dfe813ad4f706a9692841be88dbddecfe22cecca54155d1e66e78b` |
| 9 | `ar1-cdc9cc8c85099c5156ac6418872c891778d3b8d9c7595d61f90ccd8b7e181715` | `finding_transition` | `recorded` | `finding-C-04` | 1 | `implementation:334c223f32dfe813ad4f706a9692841be88dbddecfe22cecca54155d1e66e78b` |
| 10 | `ar1-af59902d7723050a97b9b032bcefb4e1f1c922211ab8aa9e52310118cc59be01` | `work_unit` | `active` | `work-unit-4` | 1 | `contract:cc74561600c77a09b47c2bb242de11f06e124ac07bb2662254dbcc1dd85b2486` |
| 11 | `ar1-f3bc61129e11eaf4e3336a932cfbd92a5c288dd570ca8f05c153ba2128c7477b` | `provider_input_measurement` | `measured` | `provider-input-4-codex_implementation` | 1 | `implementation:869d8de33dc226c2bfd5c6377f37e398efa6769953417b10c94c1c0c7f78be3a` |
| 12 | `ar1-b96ecc269c14d9b76124495a9bf270ce51cee607d116fe7db3ac5941a3bdc727` | `agent_result` | `ready` | `agent-4-codex_implementation-1` | 1 | `implementation:3555524cc932551a594b000a330380a2f9bcb0303857bf59cf1376822bac45e5` |
| 13 | `ar1-b0a5c30c3ad8205f253270e8e57ccaf5dbc7fbda2e4e7e8eb10cdf35c8c8ff72` | `finding_transition` | `recorded` | `finding-C-02` | 3 | `implementation:3555524cc932551a594b000a330380a2f9bcb0303857bf59cf1376822bac45e5` |
| 14 | `ar1-ceb9db0640fe0a4f716858f4c220ce4f902c9be3a24f7105efb37ffc831b8e41` | `finding_transition` | `recorded` | `finding-C-03` | 2 | `implementation:3555524cc932551a594b000a330380a2f9bcb0303857bf59cf1376822bac45e5` |
| 15 | `ar1-687554d9b72d9c2874899e699d325b1a00d078d44a463305191fdb84df075ffe` | `finding_transition` | `recorded` | `finding-C-04` | 2 | `implementation:3555524cc932551a594b000a330380a2f9bcb0303857bf59cf1376822bac45e5` |
| 16 | `ar1-03b32fc95a6d9a5dff0d184bcb2845b6e28ee6195860aadfaa38054240f496f4` | `provider_input_measurement` | `measured` | `provider-input-4-claude_slice_review` | 1 | `implementation:cec71b6c7ad9553f184bb8af5bcd8bd01c1608f6ff9f25f90c95422be83858de` |
| 17 | `ar1-76989ba591822d896caa7797a7c68f894a8bd2e1f9c5673081a1c4f36be68094` | `provider_input_measurement` | `measured` | `provider-input-4-claude_slice_review` | 2 | `implementation:cec71b6c7ad9553f184bb8af5bcd8bd01c1608f6ff9f25f90c95422be83858de` |
| 18 | `ar1-57b1f7e036e2ba063a5be809f422e4426db0b978fd6829e8fdfa0e8a57fe6e70` | `gate` | `decided` | `gate-quota_resume_diff-66e1cbe92b12` | 1 | `implementation:66e1cbe92b121f0bcc88b1ac3b3069bed0f573e7ebe74cfc0b64c048e2fb02ab` |
| 19 | `ar1-165957d881833a55ef5599f3ec0eadaa16d4778f28486da656032d2636451125` | `validation_request` | `requested` | `validation-request-66e1cbe92b12` | 1 | `implementation:66e1cbe92b121f0bcc88b1ac3b3069bed0f573e7ebe74cfc0b64c048e2fb02ab` |
| 20 | `ar1-2f27e83a4fbe4a944556265bf5c5b52bdb33bb3e1a475e3a438f14e850842cd1` | `validation_attestation` | `attested` | `validation-66e1cbe92b12` | 1 | `implementation:66e1cbe92b121f0bcc88b1ac3b3069bed0f573e7ebe74cfc0b64c048e2fb02ab` |
| 21 | `ar1-ed7b0b995d26af1a04a8084c36be1aac12559c0a0bce3d3acfde5f3f76ddf969` | `provider_input_measurement` | `measured` | `provider-input-4-claude_slice_review` | 3 | `implementation:66e1cbe92b121f0bcc88b1ac3b3069bed0f573e7ebe74cfc0b64c048e2fb02ab` |
| 22 | `ar1-101a4359cecaeddf04eb64ae8041aa1b025fd671eb63928b9dc8e2c6a11d018b` | `review` | `decided` | `review-claude-4-1` | 1 | `implementation:66e1cbe92b121f0bcc88b1ac3b3069bed0f573e7ebe74cfc0b64c048e2fb02ab` |
| 23 | `ar1-1070eb1f5d2222e41cff85f2d2b76c1954dadddcf0d9d9755a2b90f4be8e9085` | `finding_transition` | `recorded` | `finding-C-03` | 3 | `implementation:66e1cbe92b121f0bcc88b1ac3b3069bed0f573e7ebe74cfc0b64c048e2fb02ab` |
| 24 | `ar1-d4c362b708a3a7952286f983753d23f478660754bd0e5d20c4b148cfeccc70ed` | `provider_input_measurement` | `measured` | `provider-input-4-antigravity_slice_review` | 1 | `implementation:66e1cbe92b121f0bcc88b1ac3b3069bed0f573e7ebe74cfc0b64c048e2fb02ab` |
| 25 | `ar1-ec34d50fb622fcd84b0b41711e6251d90d390d4e51cfeee56e5fc0f3d0a4a91f` | `provider_input_measurement` | `measured` | `provider-input-4-antigravity_slice_review` | 2 | `implementation:66e1cbe92b121f0bcc88b1ac3b3069bed0f573e7ebe74cfc0b64c048e2fb02ab` |
| 26 | `ar1-3dd8de721e26b18be450301cfa22c0af82ef7e6099ee11bfec3746d7cc7a48da` | `review` | `decided` | `review-antigravity-4-1` | 1 | `implementation:66e1cbe92b121f0bcc88b1ac3b3069bed0f573e7ebe74cfc0b64c048e2fb02ab` |
| 27 | `ar1-2fc9147763e9559b459f51ce9405d25314bdd1d5f472354566898914833e0f4b` | `gate` | `decided` | `gate-unexpected_file-98ee175b02a4` | 1 | `implementation:98ee175b02a44bda45135650d1ce51465fae1e4c5f09c74f0e95c997015be25c` |
| 28 | `ar1-3728bc162125096e3d7c1a9a966e7264d4b36ebd7cd4755cb8013cf631b890f7` | `validation_request` | `requested` | `validation-request-98ee175b02a4` | 1 | `implementation:98ee175b02a44bda45135650d1ce51465fae1e4c5f09c74f0e95c997015be25c` |
| 29 | `ar1-277bf424147cb9015bf747146c85ad00d1282d6112c3e3851f5aac0607e52b51` | `validation_attestation` | `attested` | `validation-98ee175b02a4` | 1 | `implementation:98ee175b02a44bda45135650d1ce51465fae1e4c5f09c74f0e95c997015be25c` |
| 30 | `ar1-5e4fe26c84aab5691df0d67c6fa729b6006688aa69d73992ea10ce6f503bc665` | `provider_input_measurement` | `measured` | `provider-input-4-claude_slice_review` | 4 | `implementation:98ee175b02a44bda45135650d1ce51465fae1e4c5f09c74f0e95c997015be25c` |
| 31 | `ar1-6d212e90e3f79c2dfab8536bdd9792dcc397686f3832ec79e4e8a92f6a1707b8` | `diagnostic` | `failed` | `diagnostic-claude-4-1` | 1 | `implementation:98ee175b02a44bda45135650d1ce51465fae1e4c5f09c74f0e95c997015be25c` |
| 32 | `ar1-4ffbc29beea8682f0c20b027194304f00446eec1d4126c73c2c52d593e771619` | `provider_input_measurement` | `measured` | `provider-input-4-claude_slice_review` | 5 | `implementation:98ee175b02a44bda45135650d1ce51465fae1e4c5f09c74f0e95c997015be25c` |
| 33 | `ar1-32fe0d072b8420816b37d9304a4b2c8e52c6ee0108efdbf75196b921660c4463` | `review` | `decided` | `review-claude-4-2` | 1 | `implementation:98ee175b02a44bda45135650d1ce51465fae1e4c5f09c74f0e95c997015be25c` |
| 34 | `ar1-1596965048407887bf205e6d66910e9f1b5492683d7f177665c8bd17f1bcb52e` | `finding_transition` | `recorded` | `finding-C-05` | 1 | `implementation:98ee175b02a44bda45135650d1ce51465fae1e4c5f09c74f0e95c997015be25c` |
| 35 | `ar1-1d9a239f496009bb09bbd7e2d430f9170e64efd6067f98c26324d6c20852a9b5` | `provider_input_measurement` | `measured` | `provider-input-4-antigravity_slice_review` | 3 | `implementation:98ee175b02a44bda45135650d1ce51465fae1e4c5f09c74f0e95c997015be25c` |
| 36 | `ar1-c1cb61482e74d486e8bc4909d65fdec40d57e6b3dba30910a7d9c7bd8ae49a08` | `review` | `decided` | `review-antigravity-4-2` | 1 | `implementation:98ee175b02a44bda45135650d1ce51465fae1e4c5f09c74f0e95c997015be25c` |
<!-- artifact-records:decision-table:end -->
<!-- audit:decision-table:end -->

## Rückdokumentation in die Arbeitsplan-MD

Arbeitsplan: `docs/internal/phase-2-arbeitspaket-1-review-bootstrap-implement-review-cc745616.md`

## Freigabestatus

<!-- audit:approval-status:begin -->
- Implementierung bereit: `YES`
- Validierung: `PASS`
- Claude-Freigabe: `YES`
- Antigravity-Freigabe: `YES`
- Red-State-Folgeslice: `NONE`
- Commit autorisiert: `YES`

<!-- artifact-records:approval-status:begin -->
Semantischer Record-Digest: `b733a57258a944f9f1b2e344a7868636be93f4de935595c345db455b71ad6f55`

- 10. `ar1-af59902d7723050a97b9b032bcefb4e1f1c922211ab8aa9e52310118cc59be01`: Work-Unit Slice `3`, Runde `1`; Pfade `Quickstart.md`, `README.md`, `docs/internal/phase-2-arbeitspaket-1-review-bootstrap-implement-review-cc745616.md`, `docs/internal/slice-phase-2-arbeitspaket-1-review-bootstrap-arbeitsplan-03-resume-watch-audit-und-betriebsdokumentation.md`, `src/artifact_projection.py`, `src/inbox_watcher.py`, `src/orchestrator.py`, `tests/test_dry_run_scenarios.py`, `tests/test_inbox_watcher.py`, `tests/test_language_consistency.py`, `tests/test_orchestrator_watch_cli.py`, `tests/test_structured_artifact_regressions.py`, `workflow.puml`
<!-- artifact-records:approval-status:end -->
<!-- audit:approval-status:end -->
