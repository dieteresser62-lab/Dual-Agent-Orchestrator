# Slice 01 – Verlustfreie Providerinput-Messung und Startbarriere

**Feature-Branch:** `feature/native-agent-json`
**GitHub-Status:** nur lokal

## Ziel des Slice

Verlustfreie Providerinput-Messung und Startbarriere

## Akzeptanzkriterien

Siehe freigegebenen Arbeitsplan.

## Scope und Nicht-Scope

Erlaubter Scope: `docs/internal/phase-2-arbeitspaket-1-review-bootstrap-implement-review-cc745616.md`, `docs/internal/slice-phase-2-arbeitspaket-1-review-bootstrap-arbeitsplan-01-verlustfreie-providerinput-messung-und-startbarriere.md`, `orchestrator.toml`, `src/agent_adapters.py`, `src/agent_runtime.py`, `src/cli.py`, `src/orchestrator.py`, `src/provider_input_budget.py`, `tests/test_agent_adapters.py`, `tests/test_agent_runtime.py`, `tests/test_cli.py`, `tests/test_provider_input_budget.py`

## Diff-Risiko inklusive Branch- und Statuscheck

Branch `feature/native-agent-json` und Slice-Allowlist wurden vor der Umsetzung geprüft. Die bereits vorhandenen unversionierten Audit-/Slice-Dokumente stammen vom Orchestrator; Änderungen außerhalb der Allowlist wurden nicht festgestellt.

## Geplante Tests

Gemäß Arbeitsplan und Orchestrator-Validierungsmatrix.

## Durchgeführte Änderungen

- Unveränderlichen vorbereiteten Providerauftrag mit geordneten, benannten Modelleingabekomponenten eingeführt.
- Codex-stdin, Claude-Chunks/Manifest/Systempolicy/Schema/Direktive und Antigravity-Promptdatei/Schema/Direktive verlustfrei erfasst.
- Geschlossene provider-, rollen- und operationsspezifische Zeichen-/Bytebudgets mit kompatiblen Defaults und strikten Repository-Overrides ergänzt.
- Messung und typisierten lokalen Budgetdenial vor Capabilityprüfung und Providerprozess verdrahtet; Prozessstart verwendet exakt Kommando und stdin des gemessenen Auftrags.
- Zufällige lokale Transportpfade aus modellwirksamen Direktiven entfernt, damit unveränderte Aufträge stabile Digests erhalten.

## Ausgeführte Validierung mit Ergebnis

Fokussiert: `python3 -m pytest tests/test_provider_input_budget.py tests/test_agent_adapters.py tests/test_agent_runtime.py tests/test_cli.py -q` – 134 Tests erfolgreich. Zusätzlich `git diff --check` ohne Whitespacefehler. Die autoritative Vollmatrix bleibt beim Orchestrator.

## Abweichungen vom Plan

Keine erfasst.

## Offene Risiken

Die strukturierte Persistenz und Resume-Gateabbildung der Messung folgen planmäßig erst in Slice 02; Slice 01 stellt dafür Callback, typisierte Messdaten und den unklassifizierten Bootstrapdenial bereit.

## Review-Feedback von Claude

<!-- audit:claude-review:begin -->
### Ereignis 2: Runde 1

- Reviewer: `claude`
- Freigabe: `YES`
- Validierungsbindung: `validation-c1dab0d2d3e8`
- Testdateien: `tests/test_agent_adapters.py`, `tests/test_agent_runtime.py`, `tests/test_cli.py`, `tests/test_provider_input_budget.py`
- Eigene Findings: `C-01`, `C-02`

<!-- artifact-records:claude-review:begin -->
Semantischer Record-Digest: `bae55aea7289d92c8b22d3784414ab504f67a3a644e686363dd6bf6b633f8f55`

- 7. `ar1-c6fbf8451880320a947477dba654898037d1f89dfdd753040d1475e5d533e557`: `approved`; Work-Unit `2`; Findings `C-01`, `C-02`; Fingerprint `c1dab0d2d3e8f99950f3dfcddf4158991f6237a78838e4129e3ccf9522baf8cd`
<!-- artifact-records:claude-review:end -->
<!-- audit:claude-review:end -->

## Review-Feedback von Antigravity

<!-- audit:antigravity-review:begin -->
### Ereignis 3: Runde 1

- Reviewer: `antigravity`
- Freigabe: `YES`
- Validierungsbindung: `validation-c1dab0d2d3e8`
- Testdateien: `tests/test_agent_adapters.py`, `tests/test_agent_runtime.py`, `tests/test_cli.py`, `tests/test_provider_input_budget.py`
- Prüfdimensionen: lossless provider-input component measurement across codex/claude/antigravity, independent UTF-8 character and byte limit enforcement, pre-execution denial barrier before capability check and subprocess execution, strict repository TOML configuration loading, and deterministic payload hashing
- Größtes Restrisiko: lack of structured artifact record persistence for budget denials prior to slice 02
- Realistische Bruchbedingung: a prompt exceeding byte or character limits raises ProviderInputBudgetExceeded before process launch but relies on Slice 02 for structured state persistence
- Eigene Findings: keine

<!-- artifact-records:antigravity-review:begin -->
Semantischer Record-Digest: `bae55aea7289d92c8b22d3784414ab504f67a3a644e686363dd6bf6b633f8f55`

- 10. `ar1-2d063435a8eedf494eba3eb1b8c89dfda086d739f5e207647df7318f33546eb1`: `approved`; Work-Unit `2`; Findings `C-01`, `C-02`; Fingerprint `c1dab0d2d3e8f99950f3dfcddf4158991f6237a78838e4129e3ccf9522baf8cd`
<!-- artifact-records:antigravity-review:end -->
<!-- audit:antigravity-review:end -->

## Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
Noch keine strukturierten Codex-Antworten.

<!-- artifact-records:codex-responses:begin -->
Semantischer Record-Digest: `bae55aea7289d92c8b22d3784414ab504f67a3a644e686363dd6bf6b633f8f55`

Keine Codex-Findingantworten.
<!-- artifact-records:codex-responses:end -->
<!-- audit:codex-responses:end -->

## Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### Ereignis 1: `validation-c1dab0d2d3e8`

- Diff-Fingerprint: `c1dab0d2d3e8f99950f3dfcddf4158991f6237a78838e4129e3ccf9522baf8cd`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: 1 passed; 0 failed; 0 unavailable; 1 required
- Ausgabedigest: `18b0fb68e8cd5bf7f90f8d012c74cdfdc3d409599f3d3bc553d9110b5f6b0bae`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| python3 -m pytest tests/ -v | PASS | 0 | ============================= test session starts ==============================<br>platform linux -- Python 3.12.3, pytest-7.4.4, pluggy-1.4.0 -- /usr/bin/python3<br>cachedir: .pytest_cache<br>rootdir: /mnt/c/Users/Diete/Sync/DE_Privat/Rente/ChatGPT CLI/Dual-Agent-Orchestrator<br>configfile: pyproject.toml<br>collecting ... collected 828 items<br><br>tests/test_agent_adapters.py::test_registry_contains_exact_role_identities PASSED [  0%]<br>tests/test_agent_adapters.py::test_codex_command_is_configured_workspace_write_jsonl_and_stdin PASSED [  0%]<br>tests/test_agent_adapters.py::test_prepared_codex_input_uses_the_exact_stdin_prompt PASSED [  0%]<br>tests/test_agent_adapters.py::test_codex_prefers_final_message_file_over_jsonl PASSED [  0%]<br>tests/test_agent_adapters.py::test_claude_defaults_are_quota_conscious_and_permissions_are_separate PASSED [  0%]<br>tests/test_agent_adapters.py::test_prepared_claude_input_is_lossless_and_includes_every_model_channel PASSED [  0%]<br>tests/test_agent_adapters.py::test_prepared_anti<br>...[92993 characters omitted]...<br>st_anchor_gate_persists_reset_and_resume_steps PASSED [ 99%]<br>tests/test_workflow_state.py::test_pre_slice11_v3_gate_and_work_unit_shapes_load_with_empty_new_fields PASSED [ 99%]<br>tests/test_workflow_state.py::test_early_slice11_work_unit_shape_loads_without_active_test_evidence PASSED [ 99%]<br>tests/test_workflow_state.py::test_policy_gate_roundtrips_and_resumes_at_same_step PASSED [ 99%]<br>tests/test_workflow_state.py::test_managed_audit_path_roundtrips_and_rejects_unsafe_locations PASSED [ 99%]<br>tests/test_workflow_state.py::test_policy_gate_rejects_fingerprint_bound_reason_and_unsafe_path PASSED [ 99%]<br>tests/test_workflow_state.py::test_plan_time_slice_one_start_commit_cannot_be_rebound_after_resume PASSED [ 99%]<br>tests/test_workflow_state.py::test_in_progress_slice_can_extend_exact_remediation_scope PASSED [ 99%]<br>tests/test_workflow_state.py::test_state_scope_rejects_orchestrator_internal_paths PASSED [100%]<br><br>======================== 828 passed in 94.54s (0:01:34) ======================== |

<!-- artifact-records:validation-attestation:begin -->
Semantischer Record-Digest: `bae55aea7289d92c8b22d3784414ab504f67a3a644e686363dd6bf6b633f8f55`

- 5. `ar1-e405cfa3e875a88f1868d0fb38db790046ba2d6d9f8471707daf839b535086be`: Anforderung durch `orchestrator`: `argv` [`python3`, `-m`, `pytest`, `tests/`, `-v`]
- 6. `ar1-46e739dabadf8ea37984c9b1b8f0e295d21c2ba6ddfa478ae66e39203f0dbfa0`: Attestierung durch `orchestrator`; Fingerprint `c1dab0d2d3e8f99950f3dfcddf4158991f6237a78838e4129e3ccf9522baf8cd`
  - `pass` / Exit `0` / Output `f502814f376217585df1983e9157a665c0529723b89c4899a1288370401896a8`: `argv` [`python3`, `-m`, `pytest`, `tests/`, `-v`]
<!-- artifact-records:validation-attestation:end -->
<!-- audit:validation-attestation:end -->

## Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
- Teständerungsfreigabe: nicht erfasst.
- Pre-Mortems:
  - Ereignis 2: In three months, the most likely failure is a new or renamed &#96;WorkflowStep&#96;/adapter added without updating &#96;provider_input_budget.PROVIDER_OPERATIONS&#96; or the &#96;orchestrator.toml&#96; budget table in lockstep, so a specific step either crashes with an unclassified &#96;ProviderInputBudgetError&#96; deep in &#96;measure_provider_input&#96; (breaking that workflow step outright) or, for a differently-named adapter, silently bypasses the size barrier altogether — both are quiet regressions of the exact invariant this slice was built to guarantee, and neither is currently caught by an explicit completeness test.
  - Ereignis 3: In three months, the most likely failure mode is an adapter refactor or new CLI argument carrying prompt data that is omitted from _provider_input_components, resulting in undercounted provider input and subtle context window overflow at runtime.

<!-- artifact-records:test-approval-premortem:begin -->
Semantischer Record-Digest: `bae55aea7289d92c8b22d3784414ab504f67a3a644e686363dd6bf6b633f8f55`

Keine strukturierten Gates.
<!-- artifact-records:test-approval-premortem:end -->
<!-- audit:test-approval-premortem:end -->

## Findings-Lebenszyklus

<!-- audit:findings:begin -->
### `C-01` — `OPEN`

- Quelle: `claude`; Runde 1
- Klasse: `OBSERVATION`
- Finding: The &#96;operation&#96; strings passed from &#96;orchestrator.py&#96; (&#96;invocation.step.value&#96;) into &#96;run_agent&#96;/&#96;measure_provider_input&#96; are matched against &#96;provider_input_budget.PROVIDER_OPERATIONS&#96; only implicitly, via the fact that the full suite currently passes; there is no dedicated completeness test asserting that every &#96;WorkflowStep&#96; value used at each &#96;invoke_codex&#96;/&#96;invoke_reviewer&#96;/&#96;repair_review_contract&#96; call site is a member of &#96;PROVIDER_OPERATIONS[provider]&#96;. A future renamed or added &#96;WorkflowStep&#96; without a matching budget-table entry would make &#96;ProviderInputBudgetPolicy.select()&#96; raise an unclassified &#96;ProviderInputBudgetError&#96; (not the deterministic &#96;ProviderInputBudgetExceeded&#96; denial path), propagating as an unclassified crash instead of a clean, documented bootstrap denial.
- Akzeptanztest: Add a unit test (e.g. in tests/test_orchestrator_runtime.py or tests/test_provider_input_budget.py, whichever slice owns orchestrator wiring next) that iterates every WorkflowStep value used for codex/claude/antigravity invocations and asserts each resolves via &#96;default_provider_input_budget_policy().select(provider, provider, step.value)&#96; without raising.
- Statusbegründung: –

### `C-02` — `OPEN`

- Quelle: `claude`; Runde 1
- Klasse: `OBSERVATION`
- Finding: In &#96;run_agent&#96;, budget measurement/enforcement is gated by &#96;if agent_key in PROVIDER_OPERATIONS&#96;, i.e. by adapter &#96;.name&#96; string equality; an adapter whose &#96;.name&#96; is not literally "codex"/"claude"/"antigravity" (a future fourth provider, or a renamed adapter) silently skips the input-size barrier entirely rather than failing closed, which is inconsistent with the "resume is fail-closed" posture stated for structured artifacts elsewhere in the contract. Currently low risk since only these three adapters exist and are exercised.
- Akzeptanztest: When a new provider/adapter is introduced, require either its explicit inclusion in &#96;PROVIDER_OPERATIONS&#96; before it can run unmeasured, or an assertion in &#96;run_agent&#96;/its tests that every registered adapter name is a key of &#96;PROVIDER_OPERATIONS&#96;.
- Statusbegründung: –

<!-- artifact-records:findings:begin -->
Semantischer Record-Digest: `bae55aea7289d92c8b22d3784414ab504f67a3a644e686363dd6bf6b633f8f55`

- 8. `ar1-3121291e37bc1283b6c57f3629012135baa9d4516dc9d1fd44d19fb21da57da3`: `C-01` `opened` durch `claude`; `OBSERVATION` / `open` — The &#96;operation&#96; strings passed from &#96;orchestrator.py&#96; (&#96;invocation.step.value&#96;) into &#96;run_agent&#96;/&#96;measure_provider_input&#96; are matched against &#96;provider_input_budget.PROVIDER_OPERATIONS&#96; only implicitly, via the fact that the full suite currently passes; there is no dedicated completeness test asserting that every &#96;WorkflowStep&#96; value used at each &#96;invoke_codex&#96;/&#96;invoke_reviewer&#96;/&#96;repair_review_contract&#96; call site is a member of &#96;PROVIDER_OPERATIONS[provider]&#96;. A future renamed or added &#96;WorkflowStep&#96; without a matching budget-table entry would make &#96;ProviderInputBudgetPolicy.select()&#96; raise an unclassified &#96;ProviderInputBudgetError&#96; (not the deterministic &#96;ProviderInputBudgetExceeded&#96; denial path), propagating as an unclassified crash instead of a clean, documented bootstrap denial.
- 9. `ar1-2b574429ae4fea33bf33f3b8b976f5cd31d022531f08102bb522cda29f005407`: `C-02` `opened` durch `claude`; `OBSERVATION` / `open` — In &#96;run_agent&#96;, budget measurement/enforcement is gated by &#96;if agent_key in PROVIDER_OPERATIONS&#96;, i.e. by adapter &#96;.name&#96; string equality; an adapter whose &#96;.name&#96; is not literally "codex"/"claude"/"antigravity" (a future fourth provider, or a renamed adapter) silently skips the input-size barrier entirely rather than failing closed, which is inconsistent with the "resume is fail-closed" posture stated for structured artifacts elsewhere in the contract. Currently low risk since only these three adapters exist and are exercised.
<!-- artifact-records:findings:end -->
<!-- audit:findings:end -->

## Entscheidungstabelle

<!-- audit:decision-table:begin -->
| ID | Quelle | Finding | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|---|
| C-01 | claude | The &#96;operation&#96; strings passed from &#96;orchestrator.py&#96; (&#96;invocation.step.value&#96;) into &#96;run_agent&#96;/&#96;measure_provider_input&#96; are matched against &#96;provider_input_budget.PROVIDER_OPERATIONS&#96; only implicitly, via the fact that the full suite currently passes; there is no dedicated completeness test asserting that every &#96;WorkflowStep&#96; value used at each &#96;invoke_codex&#96;/&#96;invoke_reviewer&#96;/&#96;repair_review_contract&#96; call site is a member of &#96;PROVIDER_OPERATIONS[provider]&#96;. A future renamed or added &#96;WorkflowStep&#96; without a matching budget-table entry would make &#96;ProviderInputBudgetPolicy.select()&#96; raise an unclassified &#96;ProviderInputBudgetError&#96; (not the deterministic &#96;ProviderInputBudgetExceeded&#96; denial path), propagating as an unclassified crash instead of a clean, documented bootstrap denial. | OBSERVATION | offen | offen |
| C-02 | claude | In &#96;run_agent&#96;, budget measurement/enforcement is gated by &#96;if agent_key in PROVIDER_OPERATIONS&#96;, i.e. by adapter &#96;.name&#96; string equality; an adapter whose &#96;.name&#96; is not literally "codex"/"claude"/"antigravity" (a future fourth provider, or a renamed adapter) silently skips the input-size barrier entirely rather than failing closed, which is inconsistent with the "resume is fail-closed" posture stated for structured artifacts elsewhere in the contract. Currently low risk since only these three adapters exist and are exercised. | OBSERVATION | offen | offen |

<!-- artifact-records:decision-table:begin -->
Semantischer Record-Digest: `bae55aea7289d92c8b22d3784414ab504f67a3a644e686363dd6bf6b633f8f55`

| Seq | Record | Typ | Status | Logische ID | Revision | Fingerprint |
|---:|---|---|---|---|---:|---|
| 1 | `ar1-d0eb48a06ed2b09769b6622790f1ee6bcb2377b119856a855f2d5755f375394e` | `task` | `accepted` | `task-contract` | 1 | `contract:cc74561600c77a09b47c2bb242de11f06e124ac07bb2662254dbcc1dd85b2486` |
| 2 | `ar1-de990abacf5e5ae5c5f55896c880f4633c2395c5f7ce290acecd021c3dcf13ff` | `plan` | `approved` | `approved-plan` | 1 | `contract:cc74561600c77a09b47c2bb242de11f06e124ac07bb2662254dbcc1dd85b2486` |
| 3 | `ar1-6b4a72f1d4410ab56b704c70cf6ed9e1c7618c1ae38bbb078f42b5b75ba3df20` | `work_unit` | `active` | `work-unit-2` | 1 | `contract:cc74561600c77a09b47c2bb242de11f06e124ac07bb2662254dbcc1dd85b2486` |
| 4 | `ar1-da610cdfc120c4743356473756dce20bb7bf74b8745ec5ad0ac6c373df66a370` | `agent_result` | `ready` | `agent-2-codex_implementation-1` | 1 | `implementation:c1dab0d2d3e8f99950f3dfcddf4158991f6237a78838e4129e3ccf9522baf8cd` |
| 5 | `ar1-e405cfa3e875a88f1868d0fb38db790046ba2d6d9f8471707daf839b535086be` | `validation_request` | `requested` | `validation-request-c1dab0d2d3e8` | 1 | `implementation:c1dab0d2d3e8f99950f3dfcddf4158991f6237a78838e4129e3ccf9522baf8cd` |
| 6 | `ar1-46e739dabadf8ea37984c9b1b8f0e295d21c2ba6ddfa478ae66e39203f0dbfa0` | `validation_attestation` | `attested` | `validation-c1dab0d2d3e8` | 1 | `implementation:c1dab0d2d3e8f99950f3dfcddf4158991f6237a78838e4129e3ccf9522baf8cd` |
| 7 | `ar1-c6fbf8451880320a947477dba654898037d1f89dfdd753040d1475e5d533e557` | `review` | `decided` | `review-claude-2-1` | 1 | `implementation:c1dab0d2d3e8f99950f3dfcddf4158991f6237a78838e4129e3ccf9522baf8cd` |
| 8 | `ar1-3121291e37bc1283b6c57f3629012135baa9d4516dc9d1fd44d19fb21da57da3` | `finding_transition` | `recorded` | `finding-C-01` | 1 | `implementation:c1dab0d2d3e8f99950f3dfcddf4158991f6237a78838e4129e3ccf9522baf8cd` |
| 9 | `ar1-2b574429ae4fea33bf33f3b8b976f5cd31d022531f08102bb522cda29f005407` | `finding_transition` | `recorded` | `finding-C-02` | 1 | `implementation:c1dab0d2d3e8f99950f3dfcddf4158991f6237a78838e4129e3ccf9522baf8cd` |
| 10 | `ar1-2d063435a8eedf494eba3eb1b8c89dfda086d739f5e207647df7318f33546eb1` | `review` | `decided` | `review-antigravity-2-1` | 1 | `implementation:c1dab0d2d3e8f99950f3dfcddf4158991f6237a78838e4129e3ccf9522baf8cd` |
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
Semantischer Record-Digest: `bae55aea7289d92c8b22d3784414ab504f67a3a644e686363dd6bf6b633f8f55`

- 3. `ar1-6b4a72f1d4410ab56b704c70cf6ed9e1c7618c1ae38bbb078f42b5b75ba3df20`: Work-Unit Slice `1`, Runde `1`; Pfade `docs/internal/phase-2-arbeitspaket-1-review-bootstrap-implement-review-cc745616.md`, `docs/internal/slice-phase-2-arbeitspaket-1-review-bootstrap-arbeitsplan-01-verlustfreie-providerinput-messung-und-startbarriere.md`, `orchestrator.toml`, `src/agent_adapters.py`, `src/agent_runtime.py`, `src/cli.py`, `src/orchestrator.py`, `src/provider_input_budget.py`, `tests/test_agent_adapters.py`, `tests/test_agent_runtime.py`, `tests/test_cli.py`, `tests/test_provider_input_budget.py`
<!-- artifact-records:approval-status:end -->
<!-- audit:approval-status:end -->
