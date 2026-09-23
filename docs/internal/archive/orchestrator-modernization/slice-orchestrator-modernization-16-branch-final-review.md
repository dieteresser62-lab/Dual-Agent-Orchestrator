# Slice 16: Branchweites Endreview

**Status:** Implementierung und Abschlussmatrix grün; Reviewverlauf, Freigaben und Commitstatus stehen ausschließlich in den verwalteten Auditabschnitten
**Feature-Branch:** `feature/orchestrator-modernization`
**Branch-Basis des Slice:** `52bb6d3`
**GitHub-Status:** nur lokal; kein Upstream; Push und Merge sind nicht Bestandteil dieses Slice
**Arbeitsplan:** [orchestrator-modernization-work-plan.md](orchestrator-modernization-work-plan.md)

## Ziel des Slice

Die additive State-v3-Engine führt nach allen committierten Slices ein branchweites Komplettreview durch. Codex erstellt einen nicht freigabeberechtigten Vollständigkeitsbericht; Claude und Antigravity entscheiden auf demselben vollständigen Branch-Diff und derselben einmaligen Validierungsattestierung. Jedes finale Finding läuft durch eine reguläre, commitgestützte Korrektur-Work-Unit und startet anschließend das Endreview vollständig neu.

## Akzeptanzkriterien

- `CODEX_FINAL_REVIEW` liefert ausschließlich einen Vollständigkeits-/Selbstprüfbericht mit `FINAL_REPORT_READY`, niemals ein Approval oder eigenes `VALIDATION_RESULT`.
- Codex, Claude und Antigravity erhalten den kanonischen Gesamtdiff gegen die persistierte Branchbasis und dieselbe fingerprintgebundene Attestierung.
- Claude und Antigravity prüfen Architekturdrift, Schnittstellenkonsistenz, tote Übergänge, Dokumentations-Sync und R-1 bis R-18.
- Ein Blocker von Claude oder Antigravity erzeugt einen neuen Korrektur-Slice mit unveränderlicher Gitgrenze, normaler Claude-/Antigravity-Reviewkette und lokalem Commit.
- Nach dem Korrekturcommit entsteht ein neuer Final-Review-Anlauf mit frischer History, neuem Branchfingerprint und neuer einmaliger Attestierung.
- Ein vollständiger Scripted-Sessiontest durchläuft Plan, zwei Slices, zwei Commits, erstes Endreview, Korrekturcommit und zweites Endreview auf derselben produktiven Engine.
- Der bestehende v2-Defaultpfad bleibt bis Slice 18 unverändert.

## Scope und Nicht-Scope

### Scope

- Final-Review- und Correction-Work-Unit-Zustände einschließlich persistierbarer Übergänge.
- Branchweite Codex-, Claude- und Antigravity-Contracts, Evidenz und Attestierungsreuse.
- Rückweg von beiden finalen Reviewrollen in eine reguläre Korrektur- und Commitkette.
- Explizite `FINAL`-Findingherkunft im Audit-Trail.
- Erweiterung des Scripted Drivers und der Mehr-Slice-Session um Endreview, Korrektur und Neustart.
- Additiver Orchestrator-Einstieg für das branchweite v3-Endreview.

### Nicht-Scope

- Keine Watch-/Inbox-Integration; sie folgt in Slice 17.
- Kein Default-Cutover, keine Entfernung des v2-Pfads und keine gemeinsamen Instruktionsänderungen; sie folgen in Slice 18.
- Kein Push, Merge oder Remotezugriff.
- Keine manuelle Änderung von `.orchestrator/state.json` oder Checkpoints.

## Diff-Risiko inklusive Branch- und Statuscheck

**Branch-Check:** `feature/orchestrator-modernization`; entspricht dem Arbeitsplan.

**Status vor Slice-Beginn:** sauber nach lokalem Slice-15-Commit `52bb6d3`. `Inbox/.lock` war nicht vorhanden.

**Änderungstiefe:** hoch in der additiven v3-Zustandsmaschine und mittel in Contract, Audit und Scripted Backend. Der produktive v2-Default wird nicht umgeschaltet.

**Gefährdete bestehende Funktionen:** Work-Unit-Status bei Gates und Quota-Resume, Findingherkunft im Audit-Trail, Attestierungsreuse, Reviewreihenfolge und Commitautorisierung.

**Dateigrenze:** sieben produktive Python-Dateien; damit unter der Grenze von zehn. Test- und Dokumentationsdateien zählen nicht als produktive Dateien.

**Nicht anfassen:** Watch-Queue, Default-Cutover, Remotes, `.orchestrator/state.json` und Checkpoints.

**Rollback-Strategie:** additive Finalzustände, Enginezweige, Contractmarker und Scripted-Erweiterung durch gezielte Gegenpatches entfernen; kein Reset, Checkout oder History-Rewrite.

## Geplante Tests

- State-Roundtrip und Invarianten: Finalreview erst nach allen Commits, committierter Slice bleibt bei Gates unverändert, Korrektur-Slice wird angehängt.
- Codex-Finalcontract: gebundener Fingerprint und Attestierung, `FINAL_REPORT_READY`, kein Approval und kein agentenseitiges `VALIDATION_RESULT`.
- Branchreview: voller Branch-Diff statt letzter Slice, eine Validierung für alle drei Rollen, vollständige Prüfdimensionen.
- Claude- und Antigravity-Blocker: reguläre Korrektur, normale Review-/Commitkette und erneutes Endreview.
- Vollständige Scripted Session über Plan, mehrere Slices, Korrektur und wiederholtes Endreview.
- Audit-Trail mit explizitem `FINAL`-Findingursprung und fail-closed Fremdursprung.
- Vollsuite, Compile, aktiver v2-Dry-Run, Diffcheck und Dokumentvalidator.

## Durchgeführte Änderungen

- `WorkflowState` kennt eine eigene `FINAL_REVIEW`-Work-Unit und `CODEX_FINAL_REVIEW`. Sie referenziert den letzten committierten Slice, ohne dessen Status bei Gates, Quota oder Resume wieder zu öffnen.
- Ein abgelehntes Endreview wird als abgeschlossener Versuch persistiert und hängt einen neuen `CORRECTION`-Slice mit `CODEX_FINAL_CORRECTION` und unveränderlicher Gitgrenze an.
- Die produktive `WorkflowEngine` sammelt Endreviewänderungen ausschließlich gegen `branch_base`, validiert diesen Fingerprint einmal und reicht dieselbe Attestierung an Codex, Claude und Antigravity weiter.
- Codex erhält einen read-only Vollständigkeitscontract. Claude und Antigravity erhalten `FULL_BRANCH`-Evidenz mit den fünf verpflichtenden Prüfdimensionen und `FINAL_APPROVAL`.
- Nach einem Korrekturcommit startet dieselbe Engine automatisch eine frische Final-Review-Work-Unit. Histories bleiben je Work Unit getrennt; Findings werden typisiert weitergereicht.
- Der Audit-Trail erlaubt `FINAL` als einzige ausdrücklich freischaltbare Findingherkunft, damit finale Findings im Korrektur-Slice revisionssicher fortleben.
- Der Scripted Driver kann eine Korrekturgrenze aus dem nächsten expliziten Change-Event ableiten und protokolliert die tatsächlichen Invocation-Pakete für Branch-Evidenztests.
- `run_v3_final_review` stellt den additiven Entwicklungsmodus-Einstieg bereit; der aktive v2-CLI-Pfad bleibt unverändert.

## Ausgeführte Validierung mit Ergebnis

Alle 517 Tests der Vollsuite und 231 fokussierte Contract-, Prompt-, State-, Workflow-, Audit- und Scripted-Sessiontests sind grün. Compile der sieben produktiven Python-Dateien, aktiver v2-Dry-Run, Diffcheck und Slice-Dokumentvalidator sind ebenfalls bestanden. Der bewusst zusätzlich ausprobierte globale Workplan-Validator ist kein Abnahmeschritt dieses historischen Plans: Er erwartet die verwalteten Slice-Auditblöcke auch global im Arbeitsplan und lehnt daher dessen bestehende Struktur ab; der vorgesehene root-gebundene Slice-16-Validator ist grün.

## Abweichungen vom Plan

`src/repo_changes.py` musste nicht geändert werden: Die bestehende kanonische Change-Quelle akzeptiert bereits die persistierte Branchbasis und liefert den vollständigen Branch-Diff. Statt eines neuen Diffpfads erzwingt die Engine die richtige Basis am Final-Review-Rand. Zusätzlich war `src/audit_trail.py` erforderlich, weil finale Findings den typisierten Ursprung `FINAL` über die Korrektur-Work-Unit hinweg bewahren müssen. Der Scripted-End-to-End-Nachweis liegt in `tests/test_dry_run_scenarios.py` statt einer separaten Fixturedatei.

## Offene Risiken

- Der produktive Real-Driver für den Defaultpfad wird erst beim Cutover verdrahtet; Slice 16 beweist die komplette Semantik über den expliziten Entwicklungsmodus und das Scripted Backend.
- Final-Review-Histories sind absichtlich je Anlauf getrennt. Persistente Systemzustände und Findings bleiben erhalten; eine spätere UI-/Dokumentprojektion muss die Work Units zusammenführen, ohne Attestierungen zwischen Fingerprints zu vermischen.

## Review-Feedback von Claude

<!-- audit:claude-review:begin -->
Claude Sonnet mit Effort `high` prüfte im ersten und einzigen fachlichen Lauf ausschließlich den kanonischen Slice-16-Diff seit `52bb6d3`, die destillierten Akzeptanzkriterien und die gebundene Attestierung `slice16-validation-0c968c3c`. Offene Repositoryerkundung und ein erneuter Testlauf waren ausgeschlossen. Claude bestätigte den nicht freigabeberechtigten Codex-Finalcontract, die genau einmal erzeugte und von allen drei Rollen geteilte Branchattestierung, den Rückweg beider finaler Reviewer über Korrektur und Commit, die Unveränderlichkeit bereits committierter Slice-States bei Finalreview-Gates, die eng begrenzte `FINAL`-Findingherkunft sowie v2-Isolation und Resume/Idempotenz.

Claude erfasste keine Findings und erteilte `SLICE_APPROVAL: 16 | YES`. Als größtes Restrisiko nannte Claude den bewusst fail-closed als `WorkflowExecutionError` behandelten Fall einer vollständig ausgeführten, aber roten branchweiten Attestierung; Slice 16 ist ausdrücklich kein Red-State-Slice. Das Pre-Mortem warnt vor einer späteren Regression der `FINAL_REVIEW`-Sonderbehandlung in `_change_start_commit` oder `_slices_with_current_status`, wodurch ein alter Delta-Fingerprint oder ein wieder geöffneter Commitstatus entstehen könnte.
<!-- audit:claude-review:end -->

## Review-Feedback von Antigravity

<!-- audit:antigravity-review:begin -->
Antigravity prüfte mit `gemini-3.1-pro-high` und Effort `high` einmalig denselben vollständigen Slice-16-Diff, Claudes Freigabe und dieselbe Attestierung, ohne die Vollsuite erneut zu starten. Der erste Lauf meldete `A-01`: Antigravity nahm irrtümlich ein verpflichtendes Nutzergate `REVIEW_DENIAL` vor Beginn der finalen Korrektur an und verweigerte zunächst die Freigabe.

Codex wies A-01 mit der verbindlichen Spezifikation zurück: §2.4 und R-11 verlangen bei jedem Endreviewfehler unmittelbar den Rückweg in Phase B, einen regulären Korrektur-Slice und anschließend einen neuen Finalreview. §2.5 und `GateReason` kennen kein `REVIEW_DENIAL`; erst die allgemeine Vier-Rückgaben-Grenze führt zum vorhandenen `ITERATION_LIMIT`-Nutzergate. Antigravity erhielt im zweiten Lauf ausschließlich A-01, diese Begründung und die bereits vorhandenen Nachweise, keinen erneuten Voll-Diff. Antigravity schloss A-01 ausdrücklich, bestätigte die Anforderungsabbildung und erteilte `SLICE_APPROVAL: 16 | YES`.
<!-- audit:antigravity-review:end -->

## Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
- `A-01` zurückgewiesen: Ein zusätzliches `REVIEW_DENIAL`-Nutzergate widerspräche §2.4 und R-11. Der implementierte automatische Übergang `FINAL_REVIEW → CORRECTION → FINAL_REVIEW` ist das Abnahmekriterium; die normalen Test-, Stop-, Datei-, Quota-, Instanz-, manuellen Commit- und Iterationsgates bleiben innerhalb der regulären Korrekturkette wirksam.
<!-- audit:codex-responses:end -->

## Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### `slice16-validation-0c968c3c`

- Diff-Fingerprint: `0c968c3c10ff14171d542696d1f693d60e1d2c3fe06888ffb826f3d0279504da`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: 517 passed; 231 focused passed; compile PASS; active v2 dry-run PASS; diff check PASS; slice document PASS
- Ausgabedigest: `50ebb6205fa0e1f6c7ebe6272eb0305f13baf142c707177214121c237c5b5c14`

| Matrixbefehl | Status | Exitcode |
|---|---|---:|
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider` | PASS | 0 |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_audit_trail.py tests/test_contracts.py tests/test_dry_run_scenarios.py tests/test_prompts.py tests/test_workflow.py tests/test_workflow_state.py -q -p no:cacheprovider` | PASS | 0 |
| `python3 -m py_compile src/audit_trail.py src/contracts.py src/dry_run_scenarios.py src/orchestrator.py src/prompts.py src/workflow.py src/workflow_state.py` | PASS | 0 |
| `./run_task --dry-run --skip-git-check --test-command '' --agent-output none` | PASS | 0 |
| `git diff --check` | PASS | 0 |
| Slice-Dokumentvalidator | PASS | 0 |
<!-- audit:validation-attestation:end -->

## Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
Teständerungen sind durch die arbeitsplanweite Revision-8-Ausnahme autorisiert.

Vorgesehene Testpfade: `tests/test_audit_trail.py`, `tests/test_contracts.py`, `tests/test_dry_run_scenarios.py`, `tests/test_prompts.py`, `tests/test_workflow.py`, `tests/test_workflow_state.py`.

Pre-Mortem: Am wahrscheinlichsten würde ein späterer Umbau den Finalreview versehentlich auf den letzten Korrekturdelta begrenzen oder eine alte Attestierung nach dem Korrekturcommit wiederverwenden. Die erzwungene `branch_base`, `FULL_BRANCH`-Evidenz und exakt je Branchfingerprint gezählten Scripted-Validierungen machen beide Fehler sichtbar.
<!-- audit:test-approval-premortem:end -->

## Findings-Lebenszyklus

<!-- audit:findings:begin -->
| ID | Reporter | Klasse | Status | Kurzbegründung |
|---|---|---|---|---|
| A-01 | antigravity | BLOCKER | CLOSED | gefordertes `REVIEW_DENIAL`-Gate widerspricht §2.4/R-11 und existiert bewusst nicht |
<!-- audit:findings:end -->

## Entscheidungstabelle

<!-- audit:decision-table:begin -->
| Finding | Entscheidung | Umsetzung | Verifikation |
|---|---|---|---|
| A-01 | zurückgewiesen | keine Codeänderung; direkter regulärer Korrekturpfad beibehalten | §2.4, §2.5, R-11, GateReason und beide finalen Reviewer-Regressionspfade; Antigravity CLOSED/YES |
<!-- audit:decision-table:end -->

## Rückdokumentation in die Arbeitsplan-MD

Der Arbeitsplan verlinkt diese Slice-MD in §5.1 und aus der Slice-16-Überschrift. Tatsächlicher Scope, Validierungszahlen und Reviewstatus werden vor Commit final zurückdokumentiert.

## Freigabestatus

<!-- audit:approval-status:begin -->
- Implementierung bereit: `YES`
- Validierung: `PASS`
- Claude-Freigabe: `YES`
- Antigravity-Freigabe: `YES`
- Offene Blocker: `NONE`
- Commit autorisiert: `YES`
<!-- audit:approval-status:end -->
