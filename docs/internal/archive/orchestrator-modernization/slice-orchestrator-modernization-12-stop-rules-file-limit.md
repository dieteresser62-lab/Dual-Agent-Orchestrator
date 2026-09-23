# Slice 12: Maschinelle Stop-Regeln und Dateigrenze

**Status:** Implementierung und Validierung grün; Abschlussfreigaben und Commitstatus siehe verwaltete Auditabschnitte
**Feature-Branch:** `feature/orchestrator-modernization`
**Branch-Basis des Slice:** `0b565b2`
**GitHub-Status:** nur lokal; kein Upstream; Push und Merge sind nicht Bestandteil dieses Slice
**Arbeitsplan:** [orchestrator-modernization-work-plan.md](orchestrator-modernization-work-plan.md)

## Ziel des Slice

Die additive v3-Engine wertet generische und zielrepospezifische Stop-Regeln als echte, persistierte Nutzer-Gates aus. Die produktive Zehn-Dateien-Grenze greift deterministisch vor dem ersten Agentenaufruf; Agenten können ausschließlich bekannte, vollständig in ihren Prompt übernommene Regeln über `STOP_REQUESTED` auslösen.

## Akzeptanzkriterien

- Konfigurierbare POSIX-Globs klassifizieren produktive, Test-, Dokumentations- und generierte Pfade; unbekannte Pfade zählen fail-closed als produktiv.
- Höchstens zehn produktive Programm- oder Konfigurationsänderungen laufen weiter; elf halten vor jedem Agentenaufruf mit Exitcode 4 in `awaiting_user_decision`.
- Reine Test-, Dokumentations- und generierte Pfade zählen nicht zur produktiven Grenze. Ein Rename bildet eine Änderungseinheit und berücksichtigt beide Pfadseiten.
- Zielrepo-Regeln besitzen stabile IDs und nichtleere Beschreibungen; alle relevanten Prompts enthalten die Regeln vollständig und referenzierbar.
- Ein gültiges `STOP_REQUESTED: <ID> | <Begründung>` von Codex oder einem Reviewer hält denselben Schritt ohne Contract-Reparatur oder Retry als Policy-Gate an.
- Unbekannte Stop-Regel-IDs werden nicht als autorisierte Zielrepo-Regel akzeptiert.
- Branchabweichung, nicht ausführbare Validierung und unerwartete Slice-Pfade verwenden denselben persistierten Haltmechanismus.
- Resume löscht nur den Halt. Besteht eine maschinell prüfbare Bedingung unverändert fort, hält der Lauf vor dem Agenten erneut an.
- Die Engine bleibt additiv; der aktive v2-Defaultpfad bleibt unverändert und grün.

## Scope und Nicht-Scope

### Scope

- `src/gates.py`: zentrale Pfadklassen, Stop-Regeldefinitionen und produktive Dateigrenze.
- `src/cli.py`: Wiederverwendung der zentralen typisierten Pfad-/Stop-Regelmodelle beim strikten TOML-Laden.
- `src/repo_changes.py`: kanonische Änderungsgruppen einschließlich beider Rename-Seiten.
- `src/workflow_state.py`: gemeinsamer nicht-fingerprintgebundener Policy-Gate-Übergang.
- `src/workflow.py`: maschinelle Vorabgates, Regelprompt, `STOP_REQUESTED`, Branch-, Validierungs- und Unexpected-Path-Halt.
- Fokussierte Tests in `tests/test_gates.py`, `tests/test_workflow.py`, `tests/test_workflow_state.py`, `tests/test_repo_changes.py` und `tests/test_cli.py`.
- Arbeitsplan, Übergabe und diese Slice-MD.

### Nicht-Scope

- Keine pfadabhängige Validierungsmatrix oder Befehlsattestierung aus Slice 13.
- Keine Quota-, Timeout-, Binary- oder Instanzausfallklassifikation aus Slice 14.
- Kein Default-Cutover, kein Watch-Modus und keine Push-/Mergeautomatik.
- Keine Änderung des aktiven v2-Phasenworkflows.

## Diff-Risiko inklusive Branch- und Statuscheck

**Branch-Check:** `feature/orchestrator-modernization`; entspricht dem Arbeitsplan.

**Status vor Slice-Beginn:** sauber nach lokalem Slice-11-Commit `0b565b2`. `Inbox/.lock` ist nicht vorhanden.

**Voraussichtliche Änderungstiefe:** hoch in Workflow-Haltübergängen, mittel in Pfadklassifikation und Promptkontext, niedrig in der bereits vorhandenen TOML-Konfiguration.

**Gefährdete bestehende Funktionen:** v3-Aufrufreihenfolge, Resume am unveränderten Schritt, Review-Contract-Reparatur, Scopeprüfung und Slice-11-Gates. Der aktive v2-Lauf wird nur regressiv validiert.

**Nicht anfassen:** `.orchestrator/state.json`, Checkpoints, Validierungsmatrix aus Slice 13, Fehler-/Quotapolitik aus Slice 14, Watcher, Default-Cutover und Remotes.

**Rollback-Strategie:** additive Klassifikations- und Policy-Gate-Änderungen per Gegenpatch entfernen; kein Reset, Checkout oder History-Rewrite.

## Geplante Tests

- Produktive Grenze 10/11 sowie unbekannter Pfad als produktiv.
- Test-, Dokumentations- und generierte Pfade werden ausgeschlossen; Rename zählt einmal und klassifiziert beide Seiten.
- Deklarierte Regel mit langer Beschreibung erscheint ungekürzt in Codex- und Reviewerprompt.
- Codex- und Reviewer-`STOP_REQUESTED` halten ohne Retry; Freigabemarker plus Stop bleibt zentral ungültig.
- Unbekannte Regel-ID wird abgelehnt.
- Branchabweichung hält vor Agenten; unverändertes Resume hält erneut.
- Nicht ausführbare Validierung hält vor Reviewer.
- Unerwarteter Pfad wird als Policy-Gate statt Traceback persistiert.
- State-Roundtrip und Resume eines nicht-fingerprintgebundenen Policy-Gates.
- Vollständige Suite, Compile, aktiver v2-Dry-Run, Diffcheck und Slice-Dokumentvalidator.

## Durchgeführte Änderungen

- `PathClasses`, `StopRule` und die stabilen eingebauten Regel-IDs liegen zentral in `src/gates.py`; der strikte TOML-Lader in `src/cli.py` verwendet dieselben Typen und übersetzt ungültige Pfadmodelle weiterhin in laute Konfigurationsfehler.
- Die Klassifikation prüft generierte, Test-, Dokumentations- und produktive Muster in dieser Ausschlussreihenfolge. Kein Treffer fällt bewusst auf produktiv zurück. Jede Änderungseinheit bewahrt ihre Pfade, Klassen und das passende Muster beziehungsweise den Marker `unknown`.
- `evaluate_productive_file_limit` zählt Änderungseinheiten statt bloßer Pfadstrings. Renames können dadurch beide Seiten offenlegen, zählen aber einmal. Bei Überschreitung persistiert das Gate die vollständige Klassifikation ungekürzt in der Detailspur.
- `RepositoryChanges.change_groups` stellt je kanonischem Eintrag eine Gruppe bereit und erhält bei Renames Quell- und Zielpfad. Die Slice-Gitgrenze persistiert dieselbe Partition als `scope_change_groups`; ältere flache v3-Grenzen laden konservativ als Einzelgruppen.
- `WorkflowState.await_policy_gate` persistiert maschinelle und agentengemeldete Stopps ohne künstliche Fingerprintfreigabe. `resume_after_user_decision` setzt exakt denselben Schritt fort; maschinelle Bedingungen werden dort erneut bewertet.
- Vor dem ersten v3-Agentenaufruf prüft die Engine den persistierten Branch und die produktive Grenze des Slice-Scope. Zielrepo-Regeln und eingebaute Regeln erscheinen vollständig im destillierten Kontext jedes relevanten Prompts.
- Gültige bekannte `STOP_REQUESTED`-Records von Codex, Claude oder Antigravity werden nicht als Ablehnung oder Contractfehler behandelt. Unbekannte IDs scheitern laut. Branchabweichung und fehlende Validierung verwenden `stop_request`, Scope-Fremdpfade `unexpected_file`; alle liefern Exitcode 4.
- Die bestehende Scope-Fremdpfadprüfung erzeugt vor Validierung/Review beziehungsweise Commit nun einen persistierten Halt statt eines unstrukturierten Workflowfehlers.

## Ausgeführte Validierung mit Ergebnis

141 fokussierte Gate-, Workflow-, State-, Repository-Diff- und CLI-Tests sind bestanden. Die vollständige Suite umfasst 392 bestandene Tests. Compile, aktiver v2-Dry-Run, Diffcheck und Slice-Dokumentvalidator werden in der fingerprintgebundenen Abschlussattestierung ausgewiesen.

## Abweichungen vom Plan

`src/workflow_state.py`, `tests/test_workflow_state.py` und `tests/test_repo_changes.py` kamen gegenüber der voraussichtlichen Liste hinzu, weil ein gemeinsamer persistierter Policy-Gate-Übergang und der kanonische Rename-Gruppennachweis nicht sicher nur in Workflow beziehungsweise Gatecode abbildbar sind. `orchestrator.toml` bleibt unverändert, da Slice 2 das erforderliche Schema bereits bereitstellt. Claude fand zusätzlich, dass die erste Workflowverdrahtung Rename-Pfade trotz korrekter Hilfsfunktion flach gezählt hätte; `C-01` führte deshalb zur persistenten Gruppierung in der Slice-Gitgrenze und zum exakten 10/11-Workflowtest. Der produktive Scope bleibt mit fünf geänderten Source-Dateien unter der eigenen Grenze.

## Offene Risiken

- Die reale Default-CLI verwendet bis Slice 18 weiterhin v2; Slice 12 liefert die injizierbare v3-Policy-Gatemechanik.
- Fachliche Regeln sind absichtlich nicht autonom auswertbar. Ihre IDs und Beschreibungen werden maschinenlesbar bereitgestellt; die konkrete Auslösung bleibt eine validierte Agentenentscheidung.

## Review-Feedback von Claude

<!-- audit:claude-review:begin -->
### Runde 1 — vollständiger Slice, Fingerprint `aa5e5183…`

- Reviewer: `claude` (Sonnet/High); Freigabe `NO`; Blocker `C-01`.
- Scope: ausschließlich vollständiger Slice-12-Diff aller 13 Pfade seit `0b565b2`, keine unveränderten Repositorydateien.
- Finding: Das verdrahtete Vorabgate verwendete die flache Scope-Pfadliste und hätte beide Rename-Seiten als zwei Einheiten gezählt, obwohl die reine Hilfsfunktion korrekt gruppierte.
- Formhinweis: Der erste Output enthielt einen v2-`OPEN_FINDINGS`-Marker, die kompakte Reparatur einen proseartigen Statusmarker und einen nicht abgetrennten Akzeptanztest. Ausschließlich diese Marker wurden deterministisch normalisiert; Finding und negatives Verdikt blieben unverändert.

### Runde 2 — C-01-Korrekturdelta, Fingerprint `afc26ed0…`

- Freigabe `YES`; `C-01` geschlossen; keine neuen Findings.
- Scope: ausschließlich persistierte `scope_change_groups`, Zustandsmigration, reale Gateverdrahtung, exakter 10/11-Rename-Workflowtest und neue Attestierung; kein erneuter vollständiger Slice-Diff.
- Ergebnis: 10 produktive Einheiten mit 11 Pfaden erreichen Codex, 11 Einheiten mit 12 Pfaden halten als 11 vor Codex.

### Runde 3 — finales Dokumentations- und Attestierungsdelta, Fingerprint `582ef6cf…`

- Freigabe `YES`; `C-01` bleibt geschlossen; keine neuen Findings.
- Scope: ausschließlich semantische Abschlussdokumentation und Bindung an `slice12-validation-582ef6cf`; der unveränderte Implementierungsdiff wurde nicht erneut geprüft.
- Ergebnis: Scope-, Testzahl-, Finding- und v2-Cutover-Aussagen sind über Arbeitsplan, Übergabe und Slice-MD konsistent.
<!-- audit:claude-review:end -->

## Review-Feedback von Antigravity

<!-- audit:antigravity-review:begin -->
### Runde 1 — vollständiger korrigierter Slice, Fingerprint `afc26ed0…`

- Reviewer: `antigravity` (Gemini 3.1 Pro High/High); Freigabe `YES`.
- Scope: vollständiger korrigierter Slice-12-Diff aller 13 Pfade und neue Attestierung.
- Ergebnis: `C-01` bleibt geschlossen; Pfadklassifikation, Dateigrenze, Renamegruppen, Migration, Stop-Regelautorisierung und Policy-Gates sind freigegeben; keine neuen Findings.
- Restrisiko: Beim späteren CLI-Cutover muss eine Kollision eines Zielrepo-Regel-IDs mit einer künftig neuen Built-in-ID als sauberer Konfigurationsfehler statt als roher Kontextfehler ausgegeben werden.

### Runde 2 — vollständiger finaler Slice, Fingerprint `582ef6cf…`

- Reviewer: `antigravity` (Gemini 3.1 Pro High/High); Freigabe `YES` beim ersten Versuch.
- Scope: vollständiger finaler Slice-12-Diff aller 13 Pfade einschließlich Source, Tests und Abschlussdokumentation; Attestierung `slice12-validation-582ef6cf`.
- Ergebnis: Fail-closed-Pfadklassifikation, Renamegruppen und Migration, Resume-Neubewertung sowie Stop-Contract sind freigegeben; keine neuen Findings.
- Restrisiko: Eine künftig neu eingeführte Built-in-Regel-ID könnte mit einer bereits konfigurierten Zielrepo-Regel kollidieren und beim Laden laut stoppen.
<!-- audit:antigravity-review:end -->

## Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
- `C-01`: **angenommen** — `scope_change_groups` ist jetzt Bestandteil der unveränderlichen Slice-Gitgrenze, partitioniert exakt `scope_paths`, roundtrippt im State und migriert ältere flache Grenzen konservativ zu Einzelgruppen. Das Vorabgate konsumiert diese Persistenz direkt; ein Workflowtest belegt beide Rename-Grenzfälle.
<!-- audit:codex-responses:end -->

## Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### `slice12-validation-582ef6cf`

- Diff-Fingerprint: `582ef6cf111cd1f0bd51c5e5c2b18d8c51c203c7726eb24b55da32526c9adfe5`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: 392 passed; 141 focused passed; compile PASS; active v2 dry-run PASS; diff check PASS; slice document PASS
- Ausgabedigest: `0b9b3d2d5ea282abf0f4f2fb08b0f434a46763b0d8df7d00510dc3477691eaa9`

| Matrixbefehl | Status | Exitcode |
|---|---|---:|
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -q -p no:cacheprovider` | PASS | 0 |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_gates.py tests/test_workflow.py tests/test_workflow_state.py tests/test_repo_changes.py tests/test_cli.py -q -p no:cacheprovider` | PASS | 0 |
| `python3 -m py_compile src/gates.py src/cli.py src/repo_changes.py src/workflow_state.py src/workflow.py src/orchestrator.py` | PASS | 0 |
| `./run_task --dry-run --skip-git-check --test-command '' --agent-output none` | PASS | 0 |
| `git diff --check` | PASS | 0 |
| Slice-Dokumentvalidator | PASS | 0 |
<!-- audit:validation-attestation:end -->

## Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
Teständerungen sind durch die arbeitsplanweite Revision-8-Ausnahme autorisiert.

Vorgesehene Testpfade: `tests/test_gates.py`, `tests/test_workflow.py`, `tests/test_workflow_state.py`, `tests/test_repo_changes.py`, `tests/test_cli.py`.
<!-- audit:test-approval-premortem:end -->

## Findings-Lebenszyklus

<!-- audit:findings:begin -->
### `C-01` — `CLOSED` (BLOCKER)

Die reale Vorabprüfung zählt persistierte Änderungseinheiten statt flacher Pfade. Ein Rename bleibt über Resume und State-Roundtrip eine Einheit; der Regressionstest belegt 10/11 Einheiten trotz 11/12 Pfaden.
<!-- audit:findings:end -->

## Entscheidungstabelle

<!-- audit:decision-table:begin -->
| ID | Quelle | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|
| C-01 | claude | BLOCKER | angenommen/geschlossen | persistierte Scope-Gruppen, Altformmigration und realer 10/11-Rename-Workflowtest |
<!-- audit:decision-table:end -->

## Rückdokumentation in die Arbeitsplan-MD

Der Arbeitsplan verlinkt diese Slice-MD aus §5.1 und der Slice-12-Überschrift. Tatsächlicher Scope, Validierung, Findingabschluss und Freigabestatus sind ergänzt; die Übergabe enthält denselben Abschlussstand.

## Freigabestatus

<!-- audit:approval-status:begin -->
- Implementierung bereit: `YES`
- Validierung: `PASS` (`slice12-validation-582ef6cf`)
- Claude-Freigabe: `YES`
- Antigravity-Freigabe: `YES`
- Offene Blocker: `NONE`
- Commit autorisiert: `YES`
<!-- audit:approval-status:end -->
