# Slice 03 – Abschlusskorrektur

**Feature-Branch:** `feature/orchestrator-stabilization-1-1b`
**GitHub-Status:** nur lokal

## Ziel des Slice

Abschlusskorrektur

## Akzeptanzkriterien

Siehe freigegebenen Arbeitsplan.

## Scope und Nicht-Scope

Erlaubter Scope: `docs/internal/release-1-1b-deterministische-reviewvertraege-und-schlanke-reviewpakete-implement-review-c80d392c.md`, `docs/internal/slice-release-1-1b-deterministische-reviewvertraege-und-schlanke-reviewpakete-implement-03-abschlusskorrektur.md`, `src/agent_runtime.py`, `src/contracts.py`, `src/orchestrator.py`, `src/prompts.py`, `src/provider_input_budget.py`, `src/review_packets.py`, `src/workflow.py`, `tests/fixtures/review_responses/claude-slice-review-em-dash-evidence.txt`, `tests/test_agent_runtime.py`, `tests/test_contracts.py`, `tests/test_orchestrator_runtime.py`, `tests/test_prompts.py`, `tests/test_provider_input_budget.py`, `tests/test_review_packets.py`, `tests/test_review_runtime_hardening.py`, `tests/test_structured_artifact_regressions.py`, `tests/test_workflow.py`

## Diff-Risiko inklusive Branch- und Statuscheck

Der aktive Branch entspricht `feature/orchestrator-stabilization-1-1b`. Die
Korrektur beschränkt sich auf die Findingauswahl kanonischer Korrekturpakete,
deren fail-closed Validierung und zwei fokussierte Regressionstests. Bereits
vorhandene orchestratorisch verwaltete Auditänderungen bleiben unangetastet.

## Geplante Tests

- Paketkerntests für explizite Korrektur-Findingauswahl.
- Gebundener C-03-Akzeptanztest `tests/test_workflow.py`.
- Sprachkonsistenztest für Runtime-Inhalte.

## Durchgeführte Änderungen

- Same-Slice-Korrekturrunden übergeben nun `unit.open_findings` an das
  kanonische Korrekturpaket, auch wenn die Work Unit weiterhin vom Typ `SLICE`
  ist.
- Der Paketkern lehnt Korrekturpakete ohne explizite betroffene Finding-IDs
  fail-closed ab, statt alle Findings zu übernehmen.
- Eine Workflowregression belegt, dass ein offenes Finding eines anderen
  Slice nicht in das Same-Slice-Korrekturpaket gelangt; ein Paketkerntest
  sichert die neue Pflichtauswahl.

## Ausgeführte Validierung mit Ergebnis

- `python3 -m pytest tests/test_review_packets.py tests/test_workflow.py::test_plan_bound_same_slice_correction_packet_excludes_unaffected_findings tests/test_workflow.py::test_plan_bound_correction_packet_uses_same_start_delta_for_both_reviewers -v`: 7 bestanden.
- `python3 -m pytest tests/test_workflow.py -v`: 78 bestanden.
- `python3 -m pytest tests/test_language_consistency.py::test_no_german_terms_in_runtime_content -v`: 1 bestanden.
- Die autoritative vollständige Validierungsmatrix bleibt beim Orchestrator.

## Abweichungen vom Plan

Keine erfasst.

## Offene Risiken

Keine aus der fokussierten Umsetzung bekannten offenen Produktrisiken. Die
branchweite Regressionserkennung bleibt Aufgabe der orchestratorischen
Validierung und der unabhängigen Reviewer.

## Review-Feedback von Claude

<!-- audit:claude-review:begin -->
### Ereignis 2: Runde 1

- Reviewer: `claude`
- Freigabe: `YES`
- Validierungsbindung: `validation-5066f56369fe`
- Testdateien: `tests/test_review_packets.py`, `tests/test_workflow.py`
- Prüfdimensionen: Checked correctness of the packet_purpose-vs-WorkUnitKind gating change, fail-closed behavior on empty affected IDs, check-ordering in build_review_packet, non-regression of the pre-existing true-correction-work-unit path, regression-test fidelity to the exact reported cross-Slice/cross-reviewer scenario, and bound attestation test-count consistency (958→961, matching the two added tests)
- Größtes Restrisiko: Largest residual risk: the population/scoping logic for unit.open_findings itself (outside this diff's touched lines) is not re-verified here — if a future correction-triggering pathway ever computes packet_purpose == "correction" without a populated unit.open_findings, the new fail-closed guard has no fallback and will hard-stop the run instead of degrading gracefully
- Realistische Bruchbedingung: Break condition: a future correction round (e.g. a policy-gate- or state-repair-driven correction not tied to an open finding ID) computes packet_purpose == "correction" while unit.open_findings is legitimately empty, causing build_review_packet to raise ReviewPacketError and halt the workflow with no recovery path in this Slice's code.
- Eigene Findings: `C-01`, `C-02`, `C-03`

<!-- artifact-records:claude-review:begin -->
Semantischer Record-Digest: `394d3cf59b91590762f61afde80e67a11e5cee1e763b051ec2f57df2e4b93f16`

- 17. `ar1-1908972dece1e716e0d33eabd3b193b1d2b56f2c1622662a3a7b55da610e3026`: `approved`; Work-Unit `5`; Findings `C-01`, `C-02`, `C-03`; Fingerprint `5066f56369fe7926b366b1c632fc89abb7833811abcf2ae298fc1c0d9bd26f1e`
<!-- artifact-records:claude-review:end -->
<!-- audit:claude-review:end -->

## Review-Feedback von Antigravity

<!-- audit:antigravity-review:begin -->
### Ereignis 3: Runde 1

- Reviewer: `antigravity`
- Freigabe: `YES`
- Validierungsbindung: `validation-5066f56369fe`
- Testdateien: `tests/test_review_packets.py`, `tests/test_workflow.py`
- Prüfdimensionen: Verified packet_purpose-based affected finding scoping in workflow engine, fail-closed enforcement for correction packets without affected findings in review packet builder, deterministic sorting and closure reference filtering, regression test isolation for cross-slice finding exclusion, and attestation validity under 5066f56369fe
- Größtes Restrisiko: If an upstream orchestrator transition or external repair invokes a correction round without populating unit.open_findings, build_review_packet fail-closed enforcement will abort packet generation
- Realistische Bruchbedingung: A future synthetic scenario or custom recovery transition initiates a correction-purpose review step with empty unit.open_findings, triggering ReviewPacketError: correction packet requires affected findings and raising WorkflowExecutionError
- Eigene Findings: keine

<!-- artifact-records:antigravity-review:begin -->
Semantischer Record-Digest: `394d3cf59b91590762f61afde80e67a11e5cee1e763b051ec2f57df2e4b93f16`

- 20. `ar1-df6060acc5ca3c85db79b2aa41cd5f79d6226b1c350645393c67c965140770e3`: `approved`; Work-Unit `5`; Findings `C-01`, `C-02`, `C-03`; Fingerprint `5066f56369fe7926b366b1c632fc89abb7833811abcf2ae298fc1c0d9bd26f1e`
<!-- artifact-records:antigravity-review:end -->
<!-- audit:antigravity-review:end -->

## Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
- `C-01` Antwort 1: **angenommen** — Die Repair-Klassifikation berücksichtigt nun ein vollständig fehlendes REVIEW_EVIDENCE hinter dem zuerst gemeldeten Approvalfehler, ohne partielle Entscheidungen mit Evidence aber fehlendem Verdict für Repair freizugeben.
- `C-02` Antwort 1: **angenommen** — Kanonische deutschsprachige Planüberschriften sind nun explizit als sprachgebundene Parserkonstanten gekapselt; Testdaten vermeiden deutschsprachigen Runtime-Content. Der gebundene Sprachkonsistenz-Test besteht.
- `C-03` Antwort 1: **angenommen** — Same-Slice-Korrekturrunden übergeben die persistierten betroffenen Finding-IDs; der Paketkern lehnt Korrekturpakete ohne explizite Finding-Auswahl ab, und eine Regression schließt fremde offene Findings aus.

<!-- artifact-records:codex-responses:begin -->
Semantischer Record-Digest: `394d3cf59b91590762f61afde80e67a11e5cee1e763b051ec2f57df2e4b93f16`

- 4. `ar1-78c1b3b51d0b0dd162567e969f6452cc5fe5ac8471db09d4ef97c35f83d105c4`: `C-01` `responded` durch `codex`; `BLOCKER` / `open` — ACCEPTED: Die Repair-Klassifikation berücksichtigt nun ein vollständig fehlendes REVIEW_EVIDENCE hinter dem zuerst gemeldeten Approvalfehler, ohne partielle Entscheidungen mit Evidence aber fehlendem Verdict für Repair freizugeben.
- 7. `ar1-5b2662f05a932e082b8f2aed687974ced6d310b8df6a6822e93b5b3033659c18`: `C-02` `responded` durch `codex`; `BLOCKER` / `open` — ACCEPTED: Kanonische deutschsprachige Planüberschriften sind nun explizit als sprachgebundene Parserkonstanten gekapselt; Testdaten vermeiden deutschsprachigen Runtime-Content. Der gebundene Sprachkonsistenz-Test besteht.
- 13. `ar1-875ddb1e2131fdbc9291d3d0c48a3af45ba41a476f7c43701edb0d7ef488a006`: `C-03` `responded` durch `codex`; `BLOCKER` / `open` — ACCEPTED: Same-Slice-Korrekturrunden übergeben die persistierten betroffenen Finding-IDs; der Paketkern lehnt Korrekturpakete ohne explizite Finding-Auswahl ab, und eine Regression schließt fremde offene Findings aus.
<!-- artifact-records:codex-responses:end -->
<!-- audit:codex-responses:end -->

## Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### Ereignis 1: `validation-5066f56369fe`

- Diff-Fingerprint: `5066f56369fe7926b366b1c632fc89abb7833811abcf2ae298fc1c0d9bd26f1e`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: 2 passed; 0 failed; 0 unavailable; 2 required
- Ausgabedigest: `5f433239e0f869389a5c73fa478ecfa5f63f9b24c0f6719896159f188aebab77`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| python3 -m pytest tests/ -v | PASS | 0 | ============================= test session starts ==============================<br>platform linux -- Python 3.12.3, pytest-7.4.4, pluggy-1.4.0 -- /usr/bin/python3<br>cachedir: .pytest_cache<br>rootdir: /mnt/c/Users/Diete/Sync/DE_Privat/Rente/ChatGPT CLI/Dual-Agent-Orchestrator<br>configfile: pyproject.toml<br>collecting ... collected 961 items<br><br>tests/test_agent_adapters.py::test_registry_contains_exact_role_identities PASSED [  0%]<br>tests/test_agent_adapters.py::test_codex_command_is_configured_workspace_write_jsonl_and_stdin PASSED [  0%]<br>tests/test_agent_adapters.py::test_prepared_codex_input_uses_the_exact_stdin_prompt PASSED [  0%]<br>tests/test_agent_adapters.py::test_codex_prefers_final_message_file_over_jsonl PASSED [  0%]<br>tests/test_agent_adapters.py::test_claude_defaults_are_quota_conscious_and_permissions_are_separate PASSED [  0%]<br>tests/test_agent_adapters.py::test_prepared_claude_input_is_lossless_and_includes_every_model_channel PASSED [  0%]<br>tests/test_agent_adapters.py::test_prepared_clau<br>...[109806 characters omitted]...<br>_pre_slice11_v3_gate_and_work_unit_shapes_load_with_empty_new_fields PASSED [ 99%]<br>tests/test_workflow_state.py::test_early_slice11_work_unit_shape_loads_without_active_test_evidence PASSED [ 99%]<br>tests/test_workflow_state.py::test_policy_gate_roundtrips_and_resumes_at_same_step PASSED [ 99%]<br>tests/test_workflow_state.py::test_managed_audit_path_roundtrips_and_rejects_unsafe_locations PASSED [ 99%]<br>tests/test_workflow_state.py::test_policy_gate_rejects_fingerprint_bound_reason_and_unsafe_path PASSED [ 99%]<br>tests/test_workflow_state.py::test_unexpected_file_user_gate_decision_roundtrips PASSED [ 99%]<br>tests/test_workflow_state.py::test_plan_time_slice_one_start_commit_cannot_be_rebound_after_resume PASSED [ 99%]<br>tests/test_workflow_state.py::test_in_progress_slice_can_extend_exact_remediation_scope PASSED [ 99%]<br>tests/test_workflow_state.py::test_state_scope_rejects_orchestrator_internal_paths PASSED [100%]<br><br>======================= 961 passed in 106.80s (0:01:46) ======================== |
| python3 -m pytest tests/test_workflow.py -v | PASS | 0 | ============================= test session starts ==============================<br>platform linux -- Python 3.12.3, pytest-7.4.4, pluggy-1.4.0 -- /usr/bin/python3<br>cachedir: .pytest_cache<br>rootdir: /mnt/c/Users/Diete/Sync/DE_Privat/Rente/ChatGPT CLI/Dual-Agent-Orchestrator<br>configfile: pyproject.toml<br>collecting ... collected 78 items<br><br>tests/test_workflow.py::test_plan_chain_uses_codex_then_claude_then_antigravity PASSED [  1%]<br>tests/test_workflow.py::test_managed_audit_paths_are_added_after_codex_plan_only PASSED [  2%]<br>tests/test_workflow.py::test_approved_plan_waits_at_fingerprint_bound_user_gate PASSED [  3%]<br>tests/test_workflow.py::test_plan_only_rejects_future_product_slices_as_executable_records PASSED [  5%]<br>tests/test_workflow.py::test_two_codex_corrections_call_claude_three_times_and_antigravity_once PASSED [  6%]<br>tests/test_workflow.py::test_path_matrix_is_attested_once_and_reused_by_both_reviewers PASSED [  7%]<br>tests/test_workflow.py::test_finding_acceptance_command_enters_next_f<br>...[6523 characters omitted]...<br>sponse_to_open_observation PASSED [ 89%]<br>tests/test_workflow.py::test_final_review_entry_carries_previous_slice_observation PASSED [ 91%]<br>tests/test_workflow.py::test_codex_final_marker_normalization_is_exact_and_final_only PASSED [ 92%]<br>tests/test_workflow.py::test_workflow_history_roundtrips_final_report_and_loads_legacy_shape PASSED [ 93%]<br>tests/test_workflow.py::test_terminable_quota_resumes_same_final_review_for_watch_completion PASSED [ 94%]<br>tests/test_workflow.py::test_final_blocker_runs_regular_correction_commit_then_restarts_full_review[claude] PASSED [ 96%]<br>tests/test_workflow.py::test_final_blocker_runs_regular_correction_commit_then_restarts_full_review[antigravity] PASSED [ 97%]<br>tests/test_workflow.py::test_correction_resume_applies_file_limit_to_actual_diff_not_broad_allowlist PASSED [ 98%]<br>tests/test_workflow.py::test_correction_actual_diff_still_enforces_productive_file_limit PASSED [100%]<br><br>============================== 78 passed in 0.63s ============================== |

<!-- artifact-records:validation-attestation:begin -->
Semantischer Record-Digest: `394d3cf59b91590762f61afde80e67a11e5cee1e763b051ec2f57df2e4b93f16`

- 11. `ar1-2731af7b01bce306669eaf1d8cc73bff0333446b210749902d40f3c10361a51c`: Providerinput `codex/codex_final_correction` = `allowed`; Zeichen `20834/4000000`, Bytes `20854/16000000`; Input `a683e0ec9d9ff66a550a65f3c527260294c6e52fd4bc6c091e25f5aece68ee94`, Policy `8cff2b572a65674a0b31c2e2751cdc76fa839112b05e38b09bd021a02284bc83`, Übergang `077484565e0f5ca7cd5f122b6bd47665dfe8f1369728d72515860c9c12a0e7e8`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, größte Komponente `stdin_prompt`; Komponenten `stdin_prompt=20834/20854`
- 14. `ar1-6d3af2f6b5ccb46c35660afccec6a84dee6c4b6b05d8e2b34d6a85f481848e48`: Anforderung durch `orchestrator`: `argv` [`python3`, `-m`, `pytest`, `tests/`, `-v`]; `argv` [`python3`, `-m`, `pytest`, `tests/test_workflow.py`, `-v`]
- 15. `ar1-99cf49809a8e941b6f90857f0b80c1cc6844448fdd601bd911f71d8a69164d2f`: Attestierung durch `orchestrator`; Fingerprint `5066f56369fe7926b366b1c632fc89abb7833811abcf2ae298fc1c0d9bd26f1e`
  - `pass` / Exit `0` / Output `816cedcf85b8edfe9bd898879df68a017dfb357e9a833ee5431974e4eb4593f9`: `argv` [`python3`, `-m`, `pytest`, `tests/`, `-v`]
  - `pass` / Exit `0` / Output `c0d209db571943ee2fd5237bcb0d0020efcb2a6a85d7f6e5f1ef739dbb92496e`: `argv` [`python3`, `-m`, `pytest`, `tests/test_workflow.py`, `-v`]
- 16. `ar1-cba9c5c1421f339762b615c980db675e71c2894fb3c5fd2264907b3676c36869`: Providerinput `claude/claude_slice_review` = `allowed`; Zeichen `119941/4000000`, Bytes `120189/16000000`; Input `1e8ee1968ca164b8557b2db4c923bfd5d0fda130a6fe18764da5d413390c25d5`, Policy `8cff2b572a65674a0b31c2e2751cdc76fa839112b05e38b09bd021a02284bc83`, Übergang `4332ac7497e1b8f808c84b79d25f8e8fd53cbb57d58f2ca24949ff58c527d3d5`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, größte Komponente `packet_chunk_005`; Komponenten `packet_chunk_001=23802/23830, packet_chunk_002=23638/23712, packet_chunk_003=22153/22208, packet_chunk_004=23416/23440, packet_chunk_005=23943/24010, packet_chunk_006=842/842, packet_manifest=999/999, system_policy=660/660, response_schema=146/146, start_directive=342/342`
- 19. `ar1-a664533dbc319ccc5b87b9be9f2b6f6b05b4fe8eeb1c2455a71fdf6621d66a44`: Providerinput `antigravity/antigravity_slice_review` = `allowed`; Zeichen `168536/4000000`, Bytes `168872/16000000`; Input `6bec89eb229c1c4c5560f426768495458af569a90c709104fc8267a5224ce79a`, Policy `8cff2b572a65674a0b31c2e2751cdc76fa839112b05e38b09bd021a02284bc83`, Übergang `7977fef03354193340e96af898e0a1949862bb2e70c414d3f84178e2a525a437`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, größte Komponente `prompt_file`; Komponenten `prompt_file=167877/168213, response_schema=146/146, start_directive=513/513`
<!-- artifact-records:validation-attestation:end -->
<!-- audit:validation-attestation:end -->

## Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
- Teständerungsfreigabe: nicht erfasst.
- Pre-Mortems:
  - Ereignis 2: In three months, the most likely failure cause is a newly introduced correction-triggering pathway that sets packet_purpose == "correction" without a non-empty, finding-ID-backed unit.open_findings, tripping the new fail-closed guard and halting the workflow, because this correction hardens the consumption side of that invariant without adding contract-level verification that every "correction" purpose computation always carries at least one concrete finding ID.
  - Ereignis 3: In three months, the most likely failure cause is an upstream workflow extension introducing a correction-step trigger that does not populate unit.open_findings with concrete finding IDs, causing build_review_packet's strict fail-closed guard to halt the run instead of routing to an explicit policy gate.

<!-- artifact-records:test-approval-premortem:begin -->
Semantischer Record-Digest: `394d3cf59b91590762f61afde80e67a11e5cee1e763b051ec2f57df2e4b93f16`

Keine strukturierten Gates.
<!-- artifact-records:test-approval-premortem:end -->
<!-- audit:test-approval-premortem:end -->

## Findings-Lebenszyklus

<!-- audit:findings:begin -->
### `C-01` — `CLOSED`

- Quelle: `claude`; Runde 1
- Klasse: `BLOCKER`
- Finding: Bound orchestrator validation attestation for this fingerprint is FAIL (1 failed / 947 passed): tests/test_dry_run_scenarios.py::test_scripted_contract_repair_can_supply_the_only_valid_verdict fails because WorkflowEngine._validate_and_normalize_review's new repair-eligibility fragment allowlist (workflow.py) suppresses the compact contract-repair call the scripted dry-run scenario depends on, leaving the run stuck at status=awaiting_resume/step=claude_slice_review instead of reaching the scripted repaired verdict; no red-state follow-up Slice is authorized for this fingerprint.
- Akzeptanztest: VALIDATE: ["python3","-m","pytest","tests/test_dry_run_scenarios.py::test_scripted_contract_repair_can_supply_the_only_valid_verdict","-v"]
- Statusbegründung: Bound attestation for fingerprint a203f793a3d3... is PASS, including the exact bound acceptance test (&#96;test_scripted_contract_repair_can_supply_the_only_valid_verdict&#96;, isolated PASS) plus the full 948-test suite. Code review confirms the fix is narrowly scoped to the missing-evidence-behind-approval-error repair path and does not loosen the adjacent missing-verdict fail-closed behavior (test-locked both ways). No reintroduced regression found in the surrounding normalization rewrite; the removed carry-forward/foreign-status behaviors are a deliberate, tested hardening consistent with fail-closed, non-inventive normalization.

### `C-02` — `CLOSED`

- Quelle: `claude`; Runde 1
- Klasse: `BLOCKER`
- Finding: Bound orchestrator validation attestation for fingerprint 042d1c0f47f70fbc609882fd7a63360a891c328b92b531bde2d3e99eb3463f07 is FAIL (958 passed / 1 failed): tests/test_language_consistency.py::test_no_german_terms_in_runtime_content fails because this Slice's implementation introduces the German term "Akzeptanzkriterien" (and the surrounding German plan-heading vocabulary it is designed to parse, e.g. "**Ziel**") into runtime content, both in production code (src/review_packets.py:68, the Slice-heading/acceptance-criteria extraction logic in extract_slice_requirements) and in test sources (tests/test_orchestrator_runtime.py:2453, tests/test_review_packets.py:23/37, tests/test_workflow.py:1687/1748), which the project-wide English-only runtime-content gate rejects; no red-state follow-up Slice is authorized for this fingerprint.
- Akzeptanztest: acceptance=VALIDATE: ["python3","-m","pytest","tests/test_language_consistency.py::test_no_german_terms_in_runtime_content","-v"]
- Statusbegründung: Bound attestation for fingerprint 81f6163e5b79... is PASS with the full 959-test suite, including the exact bound acceptance test (&#96;test_no_german_terms_in_runtime_content&#96;, isolated within the full run). Code review confirms the fix is narrowly scoped: &#96;src/review_packets.py&#96;'s canonical plan-heading parser constants (&#96;_PLAN_GOAL_HEADING = "**Ziel**"&#96;, &#96;_PLAN_ACCEPTANCE_HEADING = "#### Akzeptanzkriterien"&#96;) are self-documenting, comment-annotated (&#96;# allowlist:german -- canonical plan contract&#96;) parser literals that must match the pre-existing, already-approved German plan-contract vocabulary — they do not introduce new German runtime-facing content, they encode a fixed external contract. Test fixtures avoid the same literal substring via a &#96;Akzeptanzkriterien&#96; escape (constructs an identical runtime string without a bare flagged literal in source), used because the fixture is embedded inside multi-line triple-quoted plan text where an inline allowlist comment is not syntactically attachable. No reintroduced regression found elsewhere in the round-2 diff.

### `C-03` — `CLOSED`

- Quelle: `claude`; Runde 1
- Klasse: `BLOCKER`
- Finding: &#96;build_review_packet&#96;'s correction-scoping only narrows &#96;open_findings&#96;/&#96;closure_references&#96; to &#96;affected_finding_ids&#96; when &#96;unit.kind is WorkUnitKind.CORRECTION&#96; (wiring inside &#96;WorkflowEngine&#96;'s Slice-review branch in workflow.py). A same-Slice round&gt;1 review — a normal Slice re-entering review after a Codex correction, the exact pattern this branch itself exercised twice in this run (Slice 1 round 2, Slice 2 round 2) — is still &#96;WorkUnitKind.SLICE&#96;, so &#96;affected_finding_ids&#96; is always passed as &#96;()&#96; even though &#96;evidence_kind&#96;/&#96;packet_purpose&#96; is already computed as &#96;"correction"&#96; for that same round. Inside &#96;build_review_packet&#96;, &#96;selected = tuple(finding_by_id[item] for item in affected) if purpose == "correction" and affected else tuple(findings)&#96;; with &#96;affected=()&#96; the else-branch fires and every OPEN/CLOSED finding across the branch's full findings tuple is embedded, not only the finding(s) that actually triggered this correction round. This defeats this Slice's own stated purpose (canonical packet minimization) for the single most common correction path in the workflow, and Codex's own nested final report independently flags the identical gap by file/line. No test exercises two Slices with simultaneously open findings, so a real cross-Slice scope-widening event has no failing-test tripwire today. This is a demonstrated, actionable defect in shipped code, not a speculative future risk, so per final-review convergence rules it cannot be carried forward as an observation.
- Akzeptanztest: VALIDATE: ["python3","-m","pytest","tests/test_workflow.py","-v"]
- Statusbegründung: Root cause (gating on WorkUnitKind.CORRECTION instead of the already-computed packet_purpose) is fixed by switching the affected_finding_ids condition to packet_purpose == "correction" in workflow.py, and review_packets.py now fail-closed-rejects any correction-purpose packet with empty affected IDs instead of falling back to embedding all findings. New regression test_plan_bound_same_slice_correction_packet_excludes_unaffected_findings directly reproduces the reported cross-Slice/cross-reviewer leakage scenario (unrelated open A-99 from Slice 02) and proves it is excluded from the same-Slice round-2 correction packet; test_correction_packet_rejects_missing_affected_finding_scope locks the fail-closed guard at the packet core. Bound attestation for fingerprint 5066f56369fe... is PASS: full suite 961/961 (958→961 net of the two new tests) and the isolated bound acceptance test tests/test_workflow.py 78/78. Code review confirms the true WorkUnitKind.CORRECTION path is unaffected (its packet_purpose is also "correction", so behavior there is unchanged) and check-ordering in build_review_packet guarantees a correction-purpose packet can never silently fall through to "all findings". No reintroduced regression found.

<!-- artifact-records:findings:begin -->
Semantischer Record-Digest: `394d3cf59b91590762f61afde80e67a11e5cee1e763b051ec2f57df2e4b93f16`

- 3. `ar1-b7e08f14704691298414247ca5b433bdf399708c32f82d5e60f7687b22b07421`: `C-01` `opened` durch `claude`; `BLOCKER` / `open` — Bound orchestrator validation attestation for this fingerprint is FAIL (1 failed / 947 passed): tests/test_dry_run_scenarios.py::test_scripted_contract_repair_can_supply_the_only_valid_verdict fails because WorkflowEngine._validate_and_normalize_review's new repair-eligibility fragment allowlist (workflow.py) suppresses the compact contract-repair call the scripted dry-run scenario depends on, leaving the run stuck at status=awaiting_resume/step=claude_slice_review instead of reaching the scripted repaired verdict; no red-state follow-up Slice is authorized for this fingerprint.
- 4. `ar1-78c1b3b51d0b0dd162567e969f6452cc5fe5ac8471db09d4ef97c35f83d105c4`: `C-01` `responded` durch `codex`; `BLOCKER` / `open` — ACCEPTED: Die Repair-Klassifikation berücksichtigt nun ein vollständig fehlendes REVIEW_EVIDENCE hinter dem zuerst gemeldeten Approvalfehler, ohne partielle Entscheidungen mit Evidence aber fehlendem Verdict für Repair freizugeben.
- 5. `ar1-6755e6ffa36c49adae780bbd890c3b64bf4751720e262fa3b6c48718c1e2a6ef`: `C-01` `status_changed` durch `claude`; `BLOCKER` / `closed` — Bound attestation for fingerprint a203f793a3d3... is PASS, including the exact bound acceptance test (&#96;test_scripted_contract_repair_can_supply_the_only_valid_verdict&#96;, isolated PASS) plus the full 948-test suite. Code review confirms the fix is narrowly scoped to the missing-evidence-behind-approval-error repair path and does not loosen the adjacent missing-verdict fail-closed behavior (test-locked both ways). No reintroduced regression found in the surrounding normalization rewrite; the removed carry-forward/foreign-status behaviors are a deliberate, tested hardening consistent with fail-closed, non-inventive normalization.
- 6. `ar1-3d6551865617109601913d3f41343179281c3194932534332f7c91b50742f073`: `C-02` `opened` durch `claude`; `BLOCKER` / `open` — Bound orchestrator validation attestation for fingerprint 042d1c0f47f70fbc609882fd7a63360a891c328b92b531bde2d3e99eb3463f07 is FAIL (958 passed / 1 failed): tests/test_language_consistency.py::test_no_german_terms_in_runtime_content fails because this Slice's implementation introduces the German term "Akzeptanzkriterien" (and the surrounding German plan-heading vocabulary it is designed to parse, e.g. "**Ziel**") into runtime content, both in production code (src/review_packets.py:68, the Slice-heading/acceptance-criteria extraction logic in extract_slice_requirements) and in test sources (tests/test_orchestrator_runtime.py:2453, tests/test_review_packets.py:23/37, tests/test_workflow.py:1687/1748), which the project-wide English-only runtime-content gate rejects; no red-state follow-up Slice is authorized for this fingerprint.
- 7. `ar1-5b2662f05a932e082b8f2aed687974ced6d310b8df6a6822e93b5b3033659c18`: `C-02` `responded` durch `codex`; `BLOCKER` / `open` — ACCEPTED: Kanonische deutschsprachige Planüberschriften sind nun explizit als sprachgebundene Parserkonstanten gekapselt; Testdaten vermeiden deutschsprachigen Runtime-Content. Der gebundene Sprachkonsistenz-Test besteht.
- 8. `ar1-f64c3ecd7bf12cc555031c3f27cd002918ce7cf8cbe050ccdfdd6f24b2f135aa`: `C-02` `status_changed` durch `claude`; `BLOCKER` / `closed` — Bound attestation for fingerprint 81f6163e5b79... is PASS with the full 959-test suite, including the exact bound acceptance test (&#96;test_no_german_terms_in_runtime_content&#96;, isolated within the full run). Code review confirms the fix is narrowly scoped: &#96;src/review_packets.py&#96;'s canonical plan-heading parser constants (&#96;_PLAN_GOAL_HEADING = "**Ziel**"&#96;, &#96;_PLAN_ACCEPTANCE_HEADING = "#### Akzeptanzkriterien"&#96;) are self-documenting, comment-annotated (&#96;# allowlist:german -- canonical plan contract&#96;) parser literals that must match the pre-existing, already-approved German plan-contract vocabulary — they do not introduce new German runtime-facing content, they encode a fixed external contract. Test fixtures avoid the same literal substring via a &#96;Akzeptanzkriterien&#96; escape (constructs an identical runtime string without a bare flagged literal in source), used because the fixture is embedded inside multi-line triple-quoted plan text where an inline allowlist comment is not syntactically attachable. No reintroduced regression found elsewhere in the round-2 diff.
- 9. `ar1-637c5ab781a065f3f4dfc641b3c2f43a6fb095cb0ff9fbb26ca1afc53f62e5e1`: `C-03` `opened` durch `claude`; `BLOCKER` / `open` — &#96;build_review_packet&#96;'s correction-scoping only narrows &#96;open_findings&#96;/&#96;closure_references&#96; to &#96;affected_finding_ids&#96; when &#96;unit.kind is WorkUnitKind.CORRECTION&#96; (wiring inside &#96;WorkflowEngine&#96;'s Slice-review branch in workflow.py). A same-Slice round&gt;1 review — a normal Slice re-entering review after a Codex correction, the exact pattern this branch itself exercised twice in this run (Slice 1 round 2, Slice 2 round 2) — is still &#96;WorkUnitKind.SLICE&#96;, so &#96;affected_finding_ids&#96; is always passed as &#96;()&#96; even though &#96;evidence_kind&#96;/&#96;packet_purpose&#96; is already computed as &#96;"correction"&#96; for that same round. Inside &#96;build_review_packet&#96;, &#96;selected = tuple(finding_by_id[item] for item in affected) if purpose == "correction" and affected else tuple(findings)&#96;; with &#96;affected=()&#96; the else-branch fires and every OPEN/CLOSED finding across the branch's full findings tuple is embedded, not only the finding(s) that actually triggered this correction round. This defeats this Slice's own stated purpose (canonical packet minimization) for the single most common correction path in the workflow, and Codex's own nested final report independently flags the identical gap by file/line. No test exercises two Slices with simultaneously open findings, so a real cross-Slice scope-widening event has no failing-test tripwire today. This is a demonstrated, actionable defect in shipped code, not a speculative future risk, so per final-review convergence rules it cannot be carried forward as an observation.
- 13. `ar1-875ddb1e2131fdbc9291d3d0c48a3af45ba41a476f7c43701edb0d7ef488a006`: `C-03` `responded` durch `codex`; `BLOCKER` / `open` — ACCEPTED: Same-Slice-Korrekturrunden übergeben die persistierten betroffenen Finding-IDs; der Paketkern lehnt Korrekturpakete ohne explizite Finding-Auswahl ab, und eine Regression schließt fremde offene Findings aus.
- 18. `ar1-a243a4aa77b63065529bbc9b636c85e99dcf9e83f30d7a46f105fde3de59cedf`: `C-03` `status_changed` durch `claude`; `BLOCKER` / `closed` — Root cause (gating on WorkUnitKind.CORRECTION instead of the already-computed packet_purpose) is fixed by switching the affected_finding_ids condition to packet_purpose == "correction" in workflow.py, and review_packets.py now fail-closed-rejects any correction-purpose packet with empty affected IDs instead of falling back to embedding all findings. New regression test_plan_bound_same_slice_correction_packet_excludes_unaffected_findings directly reproduces the reported cross-Slice/cross-reviewer leakage scenario (unrelated open A-99 from Slice 02) and proves it is excluded from the same-Slice round-2 correction packet; test_correction_packet_rejects_missing_affected_finding_scope locks the fail-closed guard at the packet core. Bound attestation for fingerprint 5066f56369fe... is PASS: full suite 961/961 (958→961 net of the two new tests) and the isolated bound acceptance test tests/test_workflow.py 78/78. Code review confirms the true WorkUnitKind.CORRECTION path is unaffected (its packet_purpose is also "correction", so behavior there is unchanged) and check-ordering in build_review_packet guarantees a correction-purpose packet can never silently fall through to "all findings". No reintroduced regression found.
<!-- artifact-records:findings:end -->
<!-- audit:findings:end -->

## Entscheidungstabelle

<!-- audit:decision-table:begin -->
| ID | Quelle | Finding | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|---|
| C-01 | claude | Bound orchestrator validation attestation for this fingerprint is FAIL (1 failed / 947 passed): tests/test_dry_run_scenarios.py::test_scripted_contract_repair_can_supply_the_only_valid_verdict fails because WorkflowEngine._validate_and_normalize_review's new repair-eligibility fragment allowlist (workflow.py) suppresses the compact contract-repair call the scripted dry-run scenario depends on, leaving the run stuck at status=awaiting_resume/step=claude_slice_review instead of reaching the scripted repaired verdict; no red-state follow-up Slice is authorized for this fingerprint. | BLOCKER | angenommen | erledigt: Bound attestation for fingerprint a203f793a3d3... is PASS, including the exact bound acceptance test (&#96;test_scripted_contract_repair_can_supply_the_only_valid_verdict&#96;, isolated PASS) plus the full 948-test suite. Code review confirms the fix is narrowly scoped to the missing-evidence-behind-approval-error repair path and does not loosen the adjacent missing-verdict fail-closed behavior (test-locked both ways). No reintroduced regression found in the surrounding normalization rewrite; the removed carry-forward/foreign-status behaviors are a deliberate, tested hardening consistent with fail-closed, non-inventive normalization. |
| C-02 | claude | Bound orchestrator validation attestation for fingerprint 042d1c0f47f70fbc609882fd7a63360a891c328b92b531bde2d3e99eb3463f07 is FAIL (958 passed / 1 failed): tests/test_language_consistency.py::test_no_german_terms_in_runtime_content fails because this Slice's implementation introduces the German term "Akzeptanzkriterien" (and the surrounding German plan-heading vocabulary it is designed to parse, e.g. "**Ziel**") into runtime content, both in production code (src/review_packets.py:68, the Slice-heading/acceptance-criteria extraction logic in extract_slice_requirements) and in test sources (tests/test_orchestrator_runtime.py:2453, tests/test_review_packets.py:23/37, tests/test_workflow.py:1687/1748), which the project-wide English-only runtime-content gate rejects; no red-state follow-up Slice is authorized for this fingerprint. | BLOCKER | angenommen | erledigt: Bound attestation for fingerprint 81f6163e5b79... is PASS with the full 959-test suite, including the exact bound acceptance test (&#96;test_no_german_terms_in_runtime_content&#96;, isolated within the full run). Code review confirms the fix is narrowly scoped: &#96;src/review_packets.py&#96;'s canonical plan-heading parser constants (&#96;_PLAN_GOAL_HEADING = "**Ziel**"&#96;, &#96;_PLAN_ACCEPTANCE_HEADING = "#### Akzeptanzkriterien"&#96;) are self-documenting, comment-annotated (&#96;# allowlist:german -- canonical plan contract&#96;) parser literals that must match the pre-existing, already-approved German plan-contract vocabulary — they do not introduce new German runtime-facing content, they encode a fixed external contract. Test fixtures avoid the same literal substring via a &#96;Akzeptanzkriterien&#96; escape (constructs an identical runtime string without a bare flagged literal in source), used because the fixture is embedded inside multi-line triple-quoted plan text where an inline allowlist comment is not syntactically attachable. No reintroduced regression found elsewhere in the round-2 diff. |
| C-03 | claude | &#96;build_review_packet&#96;'s correction-scoping only narrows &#96;open_findings&#96;/&#96;closure_references&#96; to &#96;affected_finding_ids&#96; when &#96;unit.kind is WorkUnitKind.CORRECTION&#96; (wiring inside &#96;WorkflowEngine&#96;'s Slice-review branch in workflow.py). A same-Slice round&gt;1 review — a normal Slice re-entering review after a Codex correction, the exact pattern this branch itself exercised twice in this run (Slice 1 round 2, Slice 2 round 2) — is still &#96;WorkUnitKind.SLICE&#96;, so &#96;affected_finding_ids&#96; is always passed as &#96;()&#96; even though &#96;evidence_kind&#96;/&#96;packet_purpose&#96; is already computed as &#96;"correction"&#96; for that same round. Inside &#96;build_review_packet&#96;, &#96;selected = tuple(finding_by_id[item] for item in affected) if purpose == "correction" and affected else tuple(findings)&#96;; with &#96;affected=()&#96; the else-branch fires and every OPEN/CLOSED finding across the branch's full findings tuple is embedded, not only the finding(s) that actually triggered this correction round. This defeats this Slice's own stated purpose (canonical packet minimization) for the single most common correction path in the workflow, and Codex's own nested final report independently flags the identical gap by file/line. No test exercises two Slices with simultaneously open findings, so a real cross-Slice scope-widening event has no failing-test tripwire today. This is a demonstrated, actionable defect in shipped code, not a speculative future risk, so per final-review convergence rules it cannot be carried forward as an observation. | BLOCKER | angenommen | erledigt: Root cause (gating on WorkUnitKind.CORRECTION instead of the already-computed packet_purpose) is fixed by switching the affected_finding_ids condition to packet_purpose == "correction" in workflow.py, and review_packets.py now fail-closed-rejects any correction-purpose packet with empty affected IDs instead of falling back to embedding all findings. New regression test_plan_bound_same_slice_correction_packet_excludes_unaffected_findings directly reproduces the reported cross-Slice/cross-reviewer leakage scenario (unrelated open A-99 from Slice 02) and proves it is excluded from the same-Slice round-2 correction packet; test_correction_packet_rejects_missing_affected_finding_scope locks the fail-closed guard at the packet core. Bound attestation for fingerprint 5066f56369fe... is PASS: full suite 961/961 (958→961 net of the two new tests) and the isolated bound acceptance test tests/test_workflow.py 78/78. Code review confirms the true WorkUnitKind.CORRECTION path is unaffected (its packet_purpose is also "correction", so behavior there is unchanged) and check-ordering in build_review_packet guarantees a correction-purpose packet can never silently fall through to "all findings". No reintroduced regression found. |

<!-- artifact-records:decision-table:begin -->
Semantischer Record-Digest: `394d3cf59b91590762f61afde80e67a11e5cee1e763b051ec2f57df2e4b93f16`

| Seq | Record | Typ | Status | Logische ID | Revision | Fingerprint |
|---:|---|---|---|---|---:|---|
| 1 | `ar1-ebecee416159c0136a9e3d6a1e84343051ed0d4c766892b82bc589bbd0cab668` | `task` | `accepted` | `task-contract` | 1 | `contract:c80d392c9594e636287bac2b676055a212caca1cb9ba300f1c22dc4f661314fc` |
| 2 | `ar1-b20ffb884421073f9a0f05cf769ddf8c3cfbbbed8b3df660e28eb0747aba0568` | `plan` | `approved` | `approved-plan` | 1 | `contract:c80d392c9594e636287bac2b676055a212caca1cb9ba300f1c22dc4f661314fc` |
| 3 | `ar1-b7e08f14704691298414247ca5b433bdf399708c32f82d5e60f7687b22b07421` | `finding_transition` | `recorded` | `finding-C-01` | 1 | `implementation:8c64cc0307d04f9eb126254253364202bb48f01786c1055348658d89cf0f00f5` |
| 4 | `ar1-78c1b3b51d0b0dd162567e969f6452cc5fe5ac8471db09d4ef97c35f83d105c4` | `finding_transition` | `recorded` | `finding-C-01` | 2 | `implementation:a203f793a3d3bb6c31e1bb54eef4b302396dac4410400d384eb30f47165e8602` |
| 5 | `ar1-6755e6ffa36c49adae780bbd890c3b64bf4751720e262fa3b6c48718c1e2a6ef` | `finding_transition` | `recorded` | `finding-C-01` | 3 | `implementation:a203f793a3d3bb6c31e1bb54eef4b302396dac4410400d384eb30f47165e8602` |
| 6 | `ar1-3d6551865617109601913d3f41343179281c3194932534332f7c91b50742f073` | `finding_transition` | `recorded` | `finding-C-02` | 1 | `implementation:042d1c0f47f70fbc609882fd7a63360a891c328b92b531bde2d3e99eb3463f07` |
| 7 | `ar1-5b2662f05a932e082b8f2aed687974ced6d310b8df6a6822e93b5b3033659c18` | `finding_transition` | `recorded` | `finding-C-02` | 2 | `implementation:81f6163e5b7926567e3a9cc0b5e21e132416118efd81e729db1af9ac6d442562` |
| 8 | `ar1-f64c3ecd7bf12cc555031c3f27cd002918ce7cf8cbe050ccdfdd6f24b2f135aa` | `finding_transition` | `recorded` | `finding-C-02` | 3 | `implementation:81f6163e5b7926567e3a9cc0b5e21e132416118efd81e729db1af9ac6d442562` |
| 9 | `ar1-637c5ab781a065f3f4dfc641b3c2f43a6fb095cb0ff9fbb26ca1afc53f62e5e1` | `finding_transition` | `recorded` | `finding-C-03` | 1 | `implementation:7768bd05e3fed9357f7f0d55aca6c09d5a0d5811ad083c391df4efbd9598d061` |
| 10 | `ar1-ab7fb996f143d89dfefd355872538c25ea181e4bd0cf11fbde814e2c1dfdb46d` | `correction_work_unit` | `active` | `work-unit-5` | 1 | `contract:c80d392c9594e636287bac2b676055a212caca1cb9ba300f1c22dc4f661314fc` |
| 11 | `ar1-2731af7b01bce306669eaf1d8cc73bff0333446b210749902d40f3c10361a51c` | `provider_input_measurement` | `measured` | `provider-input-5-codex_final_correction` | 1 | `implementation:a1b4bab6d4a8ee0dc442941427590ac54ee417e3188d44e85cd2645bffbb0a7c` |
| 12 | `ar1-91597798f7c7d69c84cc893b87ed18ba8ddeb2a43add64c63ca1411b036a7d60` | `agent_result` | `ready` | `agent-5-codex_final_correction-1` | 1 | `implementation:5066f56369fe7926b366b1c632fc89abb7833811abcf2ae298fc1c0d9bd26f1e` |
| 13 | `ar1-875ddb1e2131fdbc9291d3d0c48a3af45ba41a476f7c43701edb0d7ef488a006` | `finding_transition` | `recorded` | `finding-C-03` | 2 | `implementation:5066f56369fe7926b366b1c632fc89abb7833811abcf2ae298fc1c0d9bd26f1e` |
| 14 | `ar1-6d3af2f6b5ccb46c35660afccec6a84dee6c4b6b05d8e2b34d6a85f481848e48` | `validation_request` | `requested` | `validation-request-5066f56369fe` | 1 | `implementation:5066f56369fe7926b366b1c632fc89abb7833811abcf2ae298fc1c0d9bd26f1e` |
| 15 | `ar1-99cf49809a8e941b6f90857f0b80c1cc6844448fdd601bd911f71d8a69164d2f` | `validation_attestation` | `attested` | `validation-5066f56369fe` | 1 | `implementation:5066f56369fe7926b366b1c632fc89abb7833811abcf2ae298fc1c0d9bd26f1e` |
| 16 | `ar1-cba9c5c1421f339762b615c980db675e71c2894fb3c5fd2264907b3676c36869` | `provider_input_measurement` | `measured` | `provider-input-5-claude_slice_review` | 1 | `implementation:5066f56369fe7926b366b1c632fc89abb7833811abcf2ae298fc1c0d9bd26f1e` |
| 17 | `ar1-1908972dece1e716e0d33eabd3b193b1d2b56f2c1622662a3a7b55da610e3026` | `review` | `decided` | `review-claude-5-1` | 1 | `implementation:5066f56369fe7926b366b1c632fc89abb7833811abcf2ae298fc1c0d9bd26f1e` |
| 18 | `ar1-a243a4aa77b63065529bbc9b636c85e99dcf9e83f30d7a46f105fde3de59cedf` | `finding_transition` | `recorded` | `finding-C-03` | 3 | `implementation:5066f56369fe7926b366b1c632fc89abb7833811abcf2ae298fc1c0d9bd26f1e` |
| 19 | `ar1-a664533dbc319ccc5b87b9be9f2b6f6b05b4fe8eeb1c2455a71fdf6621d66a44` | `provider_input_measurement` | `measured` | `provider-input-5-antigravity_slice_review` | 1 | `implementation:5066f56369fe7926b366b1c632fc89abb7833811abcf2ae298fc1c0d9bd26f1e` |
| 20 | `ar1-df6060acc5ca3c85db79b2aa41cd5f79d6226b1c350645393c67c965140770e3` | `review` | `decided` | `review-antigravity-5-1` | 1 | `implementation:5066f56369fe7926b366b1c632fc89abb7833811abcf2ae298fc1c0d9bd26f1e` |
<!-- artifact-records:decision-table:end -->
<!-- audit:decision-table:end -->

## Rückdokumentation in die Arbeitsplan-MD

Arbeitsplan: `docs/internal/release-1-1b-deterministische-reviewvertraege-und-schlanke-reviewpakete-implement-review-c80d392c.md`

## Freigabestatus

<!-- audit:approval-status:begin -->
- Implementierung bereit: `YES`
- Validierung: `PASS`
- Claude-Freigabe: `YES`
- Antigravity-Freigabe: `YES`
- Red-State-Folgeslice: `NONE`
- Commit autorisiert: `YES`

<!-- artifact-records:approval-status:begin -->
Semantischer Record-Digest: `394d3cf59b91590762f61afde80e67a11e5cee1e763b051ec2f57df2e4b93f16`

- 10. `ar1-ab7fb996f143d89dfefd355872538c25ea181e4bd0cf11fbde814e2c1dfdb46d`: Korrektur-Work-Unit Slice `3`, Runde `1`; Pfade `docs/internal/release-1-1b-deterministische-reviewvertraege-und-schlanke-reviewpakete-implement-review-c80d392c.md`, `docs/internal/slice-release-1-1b-deterministische-reviewvertraege-und-schlanke-reviewpakete-implement-03-abschlusskorrektur.md`, `src/agent_runtime.py`, `src/contracts.py`, `src/orchestrator.py`, `src/prompts.py`, `src/provider_input_budget.py`, `src/review_packets.py`, `src/workflow.py`, `tests/fixtures/review_responses/claude-slice-review-em-dash-evidence.txt`, `tests/test_agent_runtime.py`, `tests/test_contracts.py`, `tests/test_orchestrator_runtime.py`, `tests/test_prompts.py`, `tests/test_provider_input_budget.py`, `tests/test_review_packets.py`, `tests/test_review_runtime_hardening.py`, `tests/test_structured_artifact_regressions.py`, `tests/test_workflow.py`; Findings `C-03`
<!-- artifact-records:approval-status:end -->
<!-- audit:approval-status:end -->
