# Slice 15: Skriptbarer Dry-Run für alle Gates

**Status:** Implementierung, Abschlussmatrix und Reviews grün; Commit autorisiert
**Feature-Branch:** `feature/orchestrator-modernization`
**Branch-Basis des Slice:** `9246a51`
**GitHub-Status:** nur lokal; kein Upstream; Push und Merge sind nicht Bestandteil dieses Slice
**Arbeitsplan:** [orchestrator-modernization-work-plan.md](orchestrator-modernization-work-plan.md)

## Ziel des Slice

Die additive v3-State-Maschine ist ohne echte Agenten, API-Aufrufe, Kommandos oder Schlafzeiten deterministisch ausführbar. Ein strikt versioniertes JSON-Szenario liefert ausschließlich die externen Antworten und Beobachtungen; Gates, Reviews, Attestierungsreuse, Quota-Resume, Stateübergänge und Commitfreigabe bleiben Aufgabe derselben produktiven `WorkflowEngine`.

## Akzeptanzkriterien

- Agentenantworten und -ausfälle sind exakt an Rolle, Work Unit, Runde und Schritt gebunden. Fehlende oder falsch geordnete Events brechen fail-closed ab.
- Änderungen, Teständerungen, Validierungsattestierungen, Contract-Reparaturen, Commits, Providerfehler und Zeitfortschritt sind explizit skriptbar.
- Die Positiv-/Negativmatrix deckt Validierung, Red-State-Ausnahme, Testfreigabe und deren Fingerprintverfall, Stop-Regeln, Verdict-/Finding-/Pre-Mortem-Contract, erwartete Pfade, Ankeränderung und Iterationsgrenze ab.
- Quota und sonstige Instanzausfälle sind je Rolle testbar. Eine Fake Clock deckt Zeitformate, Resetbeweis, Sicherheitszuschlag, Maximalwartezeit, Heartbeats, Unterbrechung, Diffänderung und eine zweite Quota ab, ohne real zu schlafen.
- Szenario-Expectations prüfen Exitcode, Work-Unit-Status, Schritt, Gatereason, Commitzahl, verbleibende Events, Agentenreihenfolge, Validierungszähler und Auditinhalt.
- Claude und Antigravity teilen genau eine fingerprintgebundene Attestierung; eine Korrektur mit neuem Fingerprint erzeugt genau eine neue Ausführung. Fehlende, unvollständige oder fremde Attestierungen sowie agentenseitige `VALIDATION_RESULT`-Marker werden abgelehnt.
- Der positive Sessiontest durchläuft Planung und zwei Slices mit zwei Commits auf derselben Engine und demselben Scripted Backend.
- Impliziter v3-Dry-Run erfindet keine Freigabe. Ohne `--dry-run-scenario` weist die Runtime einen v3-Contract ausdrücklich zurück.
- Das branchweite Endreview wird nicht vorweggenommen: Die produktive Engine akzeptiert dessen Schritte erst mit Slice 16; dessen End-to-End-Test erweitert dann dieselbe Scripted Session.

## Scope und Nicht-Scope

### Scope

- Versioniertes, streng validiertes JSON-Szenariomodell in `src/dry_run_scenarios.py`.
- Scripted Workflow Driver, Fake Clock, wiederverwendbare Session, Erwartungsprüfung und JSON-Auditbericht.
- Rollen-/Work-Unit-/Schrittidentität in den produktiven Invocation-Hüllen.
- CLI-Aktivierung ausschließlich über `--development-mode --dry-run --dry-run-scenario`; optionaler atomarer Report.
- Fail-closed-Schutz vor bedingungslos zustimmenden v3-Dry-Run-Ausgaben.
- Vollständige Gate-, Failure-, Zeit-, Reihenfolge-, Audit- und Attestierungstests.

### Nicht-Scope

- Kein branchweites Endreview, keine finale Korrektur-Work-Unit und kein Branch-Diff; diese produktiven Zustandsübergänge gehören zu Slice 16.
- Keine Watch-/Poison-Pill-Integration; sie gehört zu Slice 17.
- Kein Default-Cutover auf State v3; er gehört zu Slice 18.
- Keine echten Agenten-, API-, Git-Commit- oder Validierungskommandos aus Szenarien.

## Diff-Risiko inklusive Branch- und Statuscheck

**Branch-Check:** `feature/orchestrator-modernization`; entspricht dem Arbeitsplan.

**Status vor Slice-Beginn:** sauber nach lokalem Slice-14-Commit `9246a51`. `Inbox/.lock` war nicht vorhanden.

**Änderungstiefe:** mittel im neuen isolierten Backend und in der CLI-Verzweigung, niedrig in den zwei erweiterten Invocation-Dataclasses und im weiterhin aktiven v2-Normalpfad.

**Gefährdete bestehende Funktionen:** interne Workflow-Driver, CLI-Argumentauflösung und v2-`--dry-run`-Kompatibilität.

**Nicht anfassen:** `.orchestrator/state.json`, Checkpoints, produktives Endreview, Watch-Queue, Remotes und Default-Cutover.

**Rollback-Strategie:** neue Szenariodatei und CLI-Verzweigung per Gegenpatch entfernen und Invocation-Metadaten zurücknehmen; kein Reset, Checkout oder History-Rewrite.

## Geplante Tests

- Strict-JSON-Schema einschließlich unbekannter Felder und `test_changes`-Roundtrip.
- Happy Path, Plan und zwei Slices, Aufrufreihenfolge, Audit und Commitentscheidung.
- Positive und negative Varianten aller in §2.5 genannten harten Gates.
- Fehlende/unvollständige/fremde Validierungsattestierung und verbotener Reviewer-Validierungsmarker.
- Genau eine Validierung desselben Fingerprints für beide Reviewer und genau eine neue bei Korrektur.
- Quota-/Instanzausfall je Rolle sowie vollständige Fake-Clock-/Reset-/Resume-Matrix.
- CLI-Aktivierungsgrenzen, kein Rückfall in die v2-Pipeline und kein implizites v3-Approval.
- Fokussierte Tests, Vollsuite, Compile, aktiver v2-Dry-Run, Diffcheck und Dokumentvalidator.

## Durchgeführte Änderungen

- `DryRunScenario` liest ein exaktes Schema der Version 1. Unbekannte Schlüssel, falsche Typen, unsortierte Pfadmengen, widersprüchliche Events und fehlende Belege werden abgewiesen.
- `ScriptedWorkflowDriver` konsumiert Agentenevents streng in produktiver Reihenfolge und ersetzt nur Agenten-, Change-, Test-, Validierungs-, Repair- und Commitbackends. Contractvalidator, Gatecode, State-Maschine und Commitautorisierung bleiben unverändert produktiv.
- `ScriptedClock` macht Quota-Warten deterministisch, protokolliert Sleepintervalle und Heartbeats und kann einen kontrollierten Abbruch am konfigurierten Sleep auslösen.
- `ScriptedWorkflowSession` ist über Plan und mehrere Work Units wiederverwendbar. Der JSON-Auditbericht enthält State, Calls, Attestierungszähler, Reviews, Commits, Heartbeats, Sleeps und Commitentscheidung.
- `CodexInvocation` und `ReviewerInvocation` tragen Work-Unit-ID und Schritt, sodass ein Szenario nicht nur Rolle und Runde, sondern den exakten Workflowort bindet.
- Die CLI routet das neue Backend nur bei der dreifachen expliziten Aktivierung. Ein Report wird über den bestehenden atomaren Dateischreiber erzeugt; die v2-Pipeline wird dabei nicht aufgerufen.
- Der alte v2-Dry-Run bleibt kompatibel. Erkennt dessen Outputgenerator einen v3-Contract, verweigert er eine synthetische Zustimmung und verlangt ein explizites Szenario.

## Ausgeführte Validierung mit Ergebnis

179 fokussierte Runtime-, CLI-, Scenario-, Sprachkonsistenz- und Workflowtests sowie alle 507 Tests der Vollsuite sind bestanden. Compile der vier produktiven Python-Dateien, aktiver v2-Dry-Run, Diffcheck und Slice-Dokumentvalidator sind auf demselben semantischen Stand grün. Der v2-Smoke deckte einen False Positive der ersten v3-Erkennung auf: Ein im Repository-Diff zitierter v3-Contract darf den abschließenden v2-Contract nicht überstimmen; die Auswahl verwendet nun den letzten tatsächlichen Contractabschnitt.

## Abweichungen vom Plan

Statt Fixtures in vielen Dateien bündelt `src/dry_run_scenarios.py` das Schema und sämtliche Scripted Backends; `tests/test_dry_run_scenarios.py` hält die Gate-Matrix zusammen. `src/contracts.py` und `src/gates.py` mussten nicht geändert werden, weil der Dry-Run ausdrücklich deren produktive Implementierung nutzt. `tests/test_agent_runtime.py` belegt zusätzlich die v2/v3-Abgrenzung. `tests/test_language_consistency.py` normalisiert seinen bekannten Dateipfad nun einmal je Datei statt zweimal je Zeile; die Regel ist unverändert, die durch die zwei großen Szenariodateien auf 204 Sekunden gewachsene Vollsuite benötigt wieder rund elf Sekunden.

Das Akzeptanzkriterium „Plan, mehrere Slices und Endreview vollständig“ kreuzt die explizite Slice-16-Abhängigkeit: `CLAUDE_FINAL_REVIEW`, `ANTIGRAVITY_FINAL_REVIEW`, Branch-Diff und finale Korrektur werden laut Arbeitsplan erst dort in die produktive Engine integriert. Slice 15 implementiert und testet Plan plus zwei Slices auf derselben Engine; ein erfundener Dry-Run-Endreview wäre eine verbotene zweite State-Maschine. Slice 16 muss denselben Sessiontest um das reale Endreview erweitern.

## Offene Risiken

- Das JSON-Schema ist bewusst strikt; neue produktive Schritte benötigen eine explizite Schema-/Test-Erweiterung.
- Programmgesteuerte Negativtests prüfen einzelne Engine-Ausnahmen direkt. CLI-Szenarien sind primär für vollständige zustandsprojizierte Abläufe vorgesehen.
- Das vollständige Mehr-Slice-Endreview bleibt bis Slice 16 noch nicht ausführbar.

## Review-Feedback von Claude

<!-- audit:claude-review:begin -->
Claude (Sonnet/High) prüfte in Runde 1 ausschließlich den vollständigen Slice-15-Diff gegen `9246a51` und die gebundene Validierungsattestierung. Claude erteilte `SLICE_APPROVAL: 15 | YES` und bestätigte die Verwendung derselben produktiven `WorkflowEngine`, die exakte Eventbindung, die Gate-/Quota-Matrix, Attestierungsreuse, CLI-Aktivierungsgrenzen und die bewusst nicht duplizierte Slice-16-Endreview-Grenze.

Claude erfasste vier nicht blockierende Beobachtungen: C-01 verlangte einen Gegenrichtungstest für die neue „letzter Contractmarker gewinnt“-Semantik; C-02 wies auf nicht abgefangene produktive Workflow-/Contractfehler am CLI-Szenariorand hin; C-03 verlangte einen direkten Antigravity-Contract-Negativtest; C-04 verlangte den repo-weiten Nachweis, dass es keine weiteren Invocation-Konstruktoren gibt.

Runde 2 erhielt nur die Korrekturausschnitte und die neue fokussierte Attestierung. Claude schloss C-01 bis C-04 und erteilte erneut `YES`: Beide Markerreihenfolgen sind getestet, `WorkflowExecutionError` endet im Szenariopfad sauber mit Exitcode 1, ein fehlendes Antigravity-Verdikt scheitert auch nach der einzigen Reparatur, und die Suche zeigt exakt die zwei aktualisierten Produktionskonstruktoren. Der Modelloutput verwendete in Runde 2 eine abweichende `FINDING_STATUS`-Interpunktion und inferierte zwei Testpfade falsch; die vier eindeutigen CLOSED-Entscheidungen und die bereits gebundenen tatsächlichen Pfade wurden ohne weiteren Modellaufruf deterministisch normalisiert.
<!-- audit:claude-review:end -->

## Review-Feedback von Antigravity

<!-- audit:antigravity-review:begin -->
Antigravity (Gemini 3.1 Pro High/High) prüfte den vollständigen korrigierten Slice-15-Diff gegen `9246a51`, Claudes vier geschlossene Beobachtungen und die Attestierung `slice15-validation-a2cf22bc`. Antigravity bestätigte C-01 bis C-04 jeweils als geschlossen, erfasste keine neuen Findings und erteilte `SLICE_APPROVAL: 15 | YES`.

Die Prüfung bestätigte das strikte Szenarioschema, Fake-Clock- und Quota-/Instanzausfallpfade, die negative Gate-Matrix, die unveränderte v2-Kompatibilität und die Nutzung der produktiven Engine. Antigravity akzeptierte ausdrücklich, dass Slice 15 Plan und zwei Slices abdeckt, während die echte Finalreview-State-Maschine gemäß Arbeitsplan in Slice 16 ergänzt wird. Als Pre-Mortem bleibt, dass ein künftiger Produktionsschritt bis zur expliziten Erweiterung des Scripted Drivers fail-closed abbrechen wird.
<!-- audit:antigravity-review:end -->

## Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
- `C-01` angenommen: Gegenrichtungstest ergänzt; ein eingebetteter v2-Text vor dem echten v3-Contract und ein eingebetteter v3-Text vor dem echten v2-Contract wählen jeweils den letzten Contractabschnitt.
- `C-02` angenommen: Der CLI-Szenariopfad fängt `WorkflowExecutionError` einschließlich `WorkflowContractError`, protokolliert den Fehler und endet mit 1; der Grenztest belegt den Pfad ohne Traceback.
- `C-03` angenommen: Antigravity ohne Verdict bleibt auch nach genau einer weiterhin ungültigen Contract-Reparatur blockierend.
- `C-04` verifiziert: Repo-weite Suche findet Konstruktionen ausschließlich in `src/workflow.py:505` und `src/workflow.py:672`; alle Invocation-Dataclasses werden dort vollständig aufgebaut.
<!-- audit:codex-responses:end -->

## Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### `slice15-validation-a2cf22bc`

- Diff-Fingerprint: `a2cf22bcf08cfec40b05bc8ceb33693c75c602f569f82e3765f6c2814d472d1f`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: 507 passed; 179 focused passed; compile PASS; active v2 dry-run PASS; diff check PASS; slice document PASS
- Ausgabedigest: `5312a0fd825a402e73235227ee9a668f9dc1f79f10bc15d7727969488edfa17b`

| Matrixbefehl | Status | Exitcode |
|---|---|---:|
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider` | PASS | 0 |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_agent_runtime.py tests/test_cli.py tests/test_dry_run_scenarios.py tests/test_language_consistency.py tests/test_workflow.py -q -p no:cacheprovider` | PASS | 0 |
| `python3 -m py_compile src/agent_runtime.py src/cli.py src/dry_run_scenarios.py src/workflow.py` | PASS | 0 |
| `./run_task --dry-run --skip-git-check --test-command '' --agent-output none` | PASS | 0 |
| `git diff --check 9246a51` | PASS | 0 |
| Slice-Dokumentvalidator | PASS | 0 |
<!-- audit:validation-attestation:end -->

## Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
Teständerungen sind durch die arbeitsplanweite Revision-8-Ausnahme autorisiert.

Vorgesehene Testpfade: `tests/test_agent_runtime.py`, `tests/test_dry_run_scenarios.py`, `tests/test_language_consistency.py`.

Pre-Mortem: Am wahrscheinlichsten bricht der Dry-Run später dadurch, dass ein neuer produktiver Schritt im Scripted Backend stillschweigend mit einem alten Eventtyp bedient wird. Die exakte Bindung an Rolle, Work Unit, Runde und `WorkflowStep` sowie der fail-closed Fehlerevent verhindern diesen Drift.
<!-- audit:test-approval-premortem:end -->

## Findings-Lebenszyklus

<!-- audit:findings:begin -->
| ID | Reporter | Klasse | Status | Kurzbegründung |
|---|---|---|---|---|
| C-01 | claude | OBSERVATION | CLOSED | beide Reihenfolgen eingebetteter und echter Contractmarker getestet |
| C-02 | claude | OBSERVATION | CLOSED | Workflow-/Contractfehler enden am CLI-Szenariorand sauber mit 1 |
| C-03 | claude | OBSERVATION | CLOSED | Antigravity-Negativtest für fehlendes Verdict ergänzt |
| C-04 | claude | OBSERVATION | CLOSED | nur zwei Produktionskonstruktoren repo-weit vorhanden |
<!-- audit:findings:end -->

## Entscheidungstabelle

<!-- audit:decision-table:begin -->
| Finding | Entscheidung | Umsetzung | Verifikation |
|---|---|---|---|
| C-01 | angenommen | letzter tatsächlicher Contractmarker in beiden Richtungen getestet | Runtime-Regressionstests PASS |
| C-02 | angenommen | `WorkflowExecutionError` in CLI-Szenariofehlerprojektion aufgenommen | CLI-Grenztest PASS |
| C-03 | angenommen | Antigravity-Missing-Verdict plus ausgeschöpfte Reparatur | Scenario-Negativtest PASS |
| C-04 | verifiziert | keine Codeänderung erforderlich | `rg` zeigt exakt zwei Konstruktoren |
<!-- audit:decision-table:end -->

## Rückdokumentation in die Arbeitsplan-MD

Der Arbeitsplan verlinkt diese Slice-MD in §5.1 und aus der Slice-15-Überschrift. Tatsächlicher Scope und finale Validierungszahlen sind zurückdokumentiert; Reviewverlauf und Freigaben bleiben ausschließlich in den verwalteten Auditabschnitten dieser Datei.

## Freigabestatus

<!-- audit:approval-status:begin -->
- Implementierung bereit: `YES`
- Validierung: `PASS`
- Claude-Freigabe: `YES`
- Antigravity-Freigabe: `YES`
- Offene Blocker: `NONE`
- Commit autorisiert: `YES`
<!-- audit:approval-status:end -->
