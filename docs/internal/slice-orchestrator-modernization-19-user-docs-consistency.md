# Slice 19: Nutzerdokumentation und Konsistenzabschluss

**Status:** Implementierung und Vollvalidierung abgeschlossen; externe Reviews laufen
**Feature-Branch:** `feature/orchestrator-modernization`
**Branch-Basis des Slice:** `a9e69b3`
**GitHub-Status:** nur lokal; kein Upstream; Push und Merge sind nicht Bestandteil dieses Slice
**Arbeitsplan:** [orchestrator-modernization-work-plan.md](orchestrator-modernization-work-plan.md)

## Ziel des Slice

Die in Slice 18 aktivierte State-v3-Pipeline wird vollständig und ausschließlich auf Nutzerebene dokumentiert. README, Workflowdiagramm und Beispieltask beschreiben denselben tatsächlichen Plan-, Slice-, Review-, Commit-, Gate-, Resume-, Watch- und Endreviewvertrag. Produktiver Workflowcode bleibt unverändert.

## Akzeptanzkriterien

- Die README dokumentiert Plattformen, Einstieg, Konfigurationspräzedenz, Rollenadapter, Read-only-Reviewerprofil, State/Artefakte, Gates, Exitcodes, Resume, Watch, Slice-MDs und Auditprojektion entsprechend dem Slice-18-Stand.
- README und CLI-Hilfe nennen denselben öffentlichen Optionsumfang und dieselben wesentlichen Defaults; entfernte Phase-v2-Optionen werden nicht mehr beschrieben.
- `workflow.puml` zeigt Codex-Planung, Claude-Planreview, beliebig viele Slices, gezielte Claude-Korrekturrunden, einmaliges Antigravity-Slicereview, exakte lokale Commits und branchweites Endreview.
- `example-task.md` ist ein konkretes, eng begrenztes State-v3-Auftragsmuster mit Scope, Akzeptanzkriterien, Validierung, Nicht-Scope und Stopbedingungen.
- Kein aktives Nutzerdokument beschreibt ein Zwei-Phasen-Modell, Legacy-Marker oder einen Gemini-Fallback.
- Dokument-, Link-, Sprach-, CLI- und Diagrammkonsistenz sind automatisiert geprüft; die vollständige Suite bleibt grün.
- Es wird kein produktiver Python-, Wrapper- oder Konfigurationspfad geändert.

## Scope und Nicht-Scope

### Scope

- `README.md`, `workflow.puml` und `example-task.md`.
- Dokumentkonsistenztests in `tests/test_language_consistency.py` und bei Bedarf einem fokussierten neuen Testpfad.
- Arbeitsplan- und Slice-19-Prüfspur.

### Nicht-Scope

- Keine Änderung von `src/**`, `run_task`, `orchestrator.toml`, Root-Rollenverträgen oder Workflowsemantik.
- Keine neuen CLI-Optionen, Marker, Gates, Plattformzusagen oder Laufartefakte.
- Kein Push, Merge, Remotezugriff oder manuelles Editieren von `.orchestrator/state.json` und Checkpoints.

## Diff-Risiko inklusive Branch- und Statuscheck

**Branch-Check:** `feature/orchestrator-modernization`; entspricht dem Arbeitsplan.

**Status vor Slice-Beginn:** sauber nach lokalem Slice-18-Commit `a9e69b3`. `Inbox/.lock` war nicht vorhanden.

**Änderungstiefe:** niedrig für Runtime, hoch für den Nutzervertrag: falsche Beispiele könnten korrekte CLI-Aufrufe verhindern oder Nutzer zu unsicheren Gate-/Resume-Entscheidungen verleiten.

**Gefährdete bestehende Funktionen:** keine produktive Funktion; betroffen sind ausschließlich Auffindbarkeit, Bedienbarkeit und Konsistenz der dokumentierten Schnittstelle.

**Dateigrenze:** keine produktive Programm- oder Konfigurationsdatei geplant. Dokumente, Tests und interne Prüfspur zählen nicht zur produktiven Zehn-Dateien-Grenze.

**Nicht anfassen:** `src/**`, `run_task`, Runtimekonfiguration, State/Checkpoints, Remotes sowie historische Slice-Dokumente.

**Rollback-Strategie:** gezielter Gegenpatch oder späterer Revert-Commit ausschließlich für diesen Dokumentationsslice; kein Reset, Checkout oder History-Rewrite.

## Geplante Tests

- Aktive Nutzerdokumente enthalten keine Phase-v2-, Legacy-Marker- oder Gemini-Fallback-Sprache.
- Jeder in der README dokumentierte lange CLI-Schalter existiert in `build_parser()`; alle öffentlichen Schalter erscheinen in der README.
- Wesentliche Defaults für Resume, Streaming, Gitcheck und Claude Sonnet/High stimmen mit dem Parser-/Adaptervertrag überein.
- Lokale README-Links und die Workflowbildquelle zeigen auf vorhandene Dateien.
- Diagramm enthält Plan, Slice-Schleife, Claude, Antigravity, Validierung, Commit, Gates und Endreview; grundlegende PlantUML-Struktur ist geschlossen.
- Beispieltask enthält Scope, Nicht-Scope, Akzeptanzkriterien, Validierung und Stopbedingungen.
- `python3 -m pytest tests/ -v`, Compile, Default-Dry-Run, `git diff --check` und Slice-Dokumentvalidator.

## Durchgeführte Änderungen

- `README.md` beschreibt den einzigen aktiven State-v3-Workflow von Codex-Planung über gezielte Claude-Reviews, vollständige Antigravity-Slicereviews, exakte lokale Commits und branchweites Endreview. State, Checkpoints, Logs, vorbereitete Audit-MDs, Gate-/Resumewege, Watch-Verhalten und Exitcodes 0 bis 4 sind auf den produktiven Slice-18-Stand abgeglichen.
- Die CLI-Referenz enthält jeden öffentlichen langen Parser-Schalter genau einmal im dokumentierten Bestand und keine entfernte Phase-v2-Option. Rollendefaults halten Claude persistent auf Sonnet/High; Plattformzusage bleibt Linux, macOS und WSL2 bei weiterhin unzugesichertem Native Windows.
- `workflow.puml` visualisiert Planreview, beliebig viele persistierte Slices, Claude- und Antigravity-Korrekturschleifen, Gate-Halt/Resume, exakte lokale Commits und das wiederholbare branchweite Endreview.
- `example-task.md` ist ein konkretes englisches Auftragsmuster mit exaktem Pfadscope, Anforderungen, Akzeptanzkriterien, Validierung, Nicht-Scope und Stopbedingungen.
- `tests/test_language_consistency.py` prüft aktive Altbegriffe, bidirektionale README/Parser-Schaltergleichheit, aufgelöste Kern-Defaults, dokumentierte `RUN_TASK_*`-Namen, lokale Links und beide Help-Einstiege, Markerabgleich, Diagrammstruktur sowie Beispieltask-Grenzen.
- Arbeitsplan und Slice-19-MD bilden Scope, Risiko, Validierung und den späteren Reviewlebenszyklus ab. Produktiver Workflowcode und Runtimekonfiguration blieben unverändert.

## Ausgeführte Validierung mit Ergebnis

- `python3 -m compileall -q src tests` → PASS.
- `python3 -m pytest tests/ -v` → PASS, `507 passed in 12.53s`, Exitcode 0.
- `python3 -m pytest tests/test_language_consistency.py -q` → PASS, `11 passed`, Exitcode 0.
- `./run_task --dry-run --task-file example-task.md --quiet` → PASS, Exitcode 0; Plan, zwei simulierte Slice-Commits und Finalreview ohne API- oder Repositoryschreibzugriff.
- Beide in der README dokumentierten Help-Einstiege werden im Konsistenztest ausgeführt und liefern Exitcode 0 mit State-v3-Hilfe.
- `validate_slice_document(...)` für Slice 19 → PASS.
- `git diff --check` → PASS; ausschließlich erwartete WSL-Zeilenendewarnungen.

## Abweichungen vom Plan

Ein PlantUML-Binary ist lokal nicht installiert. Statt eines lokalen Render-Smokes prüft der neue Test Start-/Endmarker, ausgeglichene Partition-, While- und If-Struktur sowie alle vorgeschriebenen Workflowknoten. Es wurde keine neue Tool- oder Buildabhängigkeit eingeführt.

## Offene Risiken

- Eine externe Plattformmatrix wird in diesem lokalen Slice nicht neu ausgeführt; die README darf deshalb nur den bereits zugesicherten Kreis Linux, macOS und WSL2 nennen und Native Windows weiterhin nicht zusichern.
- Historische Dokumente unter `docs/internal/` behalten absichtlich ihre zeitgebundenen Altbegriffe. Die Konsistenzprüfung richtet sich auf aktive Nutzer- und Rollenunterlagen.

## Review-Feedback von Claude

<!-- audit:claude-review:begin -->
Claude Sonnet mit Effort `high` prüfte im ersten Lauf ausschließlich den kanonischen Slice-19-Diff, die Akzeptanzkriterien und die fingerprintgebundene Attestierung. Bestätigt wurden der reine Dokumentationsscope, Rollen- und Evidenzasymmetrie, orchestratoreigene Validierung/Commitmechanik, Gate-/Exitcode-/Resumevertrag, Watch- und Artefaktbeschreibung, Plattformgrenze, Markerbestand, Diagrammtopologie und Beispieltask-Grenzen. Claude erteilte `SLICE_APPROVAL: 19 | YES` und erfasste C-01 als nicht blockierende Observation: Schalternamen waren bidirektional gebunden, dokumentierte Defaults und Umgebungsvariablennamen aber noch nicht umfassend.

Codex ergänzte den fokussierten Test um aufgelöste Parser-, Quota- und Adapterdefaults sowie den Nachweis jedes konkreten README-`RUN_TASK_*`-Namens. Im zweiten Lauf erhielt Claude ausschließlich diesen C-01-Testdelta und die erneuerte Attestierung. Claude schloss C-01 ausdrücklich und bestätigte `SLICE_APPROVAL: 19 | YES` für Fingerprint `6d59f9090e9de0afe5d3366e8275d38cf394b60d8f473eb52a3472604ef2a0b8`. In beiden Runden ergänzte eine reine Markerreparatur das zunächst fehlende `TEST_FILES_TOUCHED`, ohne Implementierungsevidenz erneut zu übertragen.
<!-- audit:claude-review:end -->

## Review-Feedback von Antigravity

<!-- audit:antigravity-review:begin -->
Antigravity prüfte nach Claudes korrigierter Freigabe einmalig den vollständigen aktuellen Slice-19-Diff samt geschlossener C-01 und derselben Attestierung. Geprüft wurden README, Diagramm, Beispieltask, Arbeitsplan/Prüfspur, Konsistenztests, fehlende Produktivpfade, Altvertragsfreiheit, Rollenreihenfolge, Reviewgrenzen, Validierungs- und Commitownership, Gates, Exitcodes, Resume, Watch, Artefakte, Plattformen, Marker, CLI-Schalter/Defaults/Umgebungsnamen, Links und Scope.

Antigravity meldete kein neues Finding. Als Restrisiko verbleibt, dass künftig dynamisch außerhalb von `build_parser()._actions` erzeugte Optionen oder neue aktive Nutzerdateien außerhalb der expliziten Testliste Drift erzeugen könnten. Eine reine Markerreparatur ergänzte `TEST_FILES_TOUCHED`; das maschinell validierte Endverdikt lautet `SLICE_APPROVAL: 19 | YES` für Fingerprint `6d59f9090e9de0afe5d3366e8275d38cf394b60d8f473eb52a3472604ef2a0b8`.
<!-- audit:antigravity-review:end -->

## Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
FINDING_RESPONSE: C-01 | ACCEPTED | Der Konsistenztest bindet jetzt die dokumentierten numerischen Kern-Defaults an die tatsächlich aufgelösten Parser-/Quota-/Adapterwerte. Zusätzlich wird jeder konkrete `RUN_TASK_*`-Name aus der README entweder als aktives Quelltextliteral oder als zulässige dynamische Rollen-/Feldkombination nachgewiesen.
<!-- audit:codex-responses:end -->

## Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### `validation-6d59f9090e9d`

- Fachlicher Diff-Fingerprint vor deterministischer Auditprojektion: `6d59f9090e9de0afe5d3366e8275d38cf394b60d8f473eb52a3472604ef2a0b8`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: Compile PASS; 507 Tests PASS; 11 Dokumenttests PASS; Default-Dry-Run, Dokumentvalidator und Diffcheck PASS
- Ausgabedigest: `85b198b2ff5890690f2dfd530ea7332d290f0ca291defa893d0d13f3bd92fb37`

| Matrixbefehl | Status | Exitcode |
|---|---|---:|
| `python3 -m compileall -q src tests` | PASS | 0 |
| `python3 -m pytest tests/ -v` | PASS | 0 |
| `python3 -m pytest tests/test_language_consistency.py -q` | PASS | 0 |
| `./run_task --dry-run --task-file example-task.md --quiet` | PASS | 0 |
| `validate_slice_document(slice_id=19)` | PASS | 0 |
| `git diff --check` | PASS | 0 |

Die nach den Reviewentscheidungen eingetragenen verwalteten Auditblöcke sind eine deterministische Projektion der bereits validierten Antworten und gehören nicht zum fachlichen Reviewfingerprint.
<!-- audit:validation-attestation:end -->

## Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
Teständerungen sind durch die arbeitsplanweite Revision-8-Ausnahme autorisiert.

Vorgesehene Testpfade: `tests/test_language_consistency.py` und optional ein fokussierter Dokumentkonsistenztest.

Pre-Mortem: Am wahrscheinlichsten bleibt nach der Neufassung ein einzelnes Beispiel mit einer nicht mehr existierenden Option oder ein Diagrammknoten mit alter Rollenreihenfolge stehen. Ein automatischer Abgleich aller README-Schalter mit `build_parser()`, verbotener Altbegriffe in allen drei Nutzerartefakten und der zwingenden Plan-/Slice-/Commit-/Endreviewknoten soll diese Drift verhindern.
<!-- audit:test-approval-premortem:end -->

## Findings-Lebenszyklus

<!-- audit:findings:begin -->
| ID | Reporter | Klasse | Status | Kurzbegründung |
|---|---|---|---|---|
| C-01 | claude | OBSERVATION | CLOSED | README-Defaults und konkrete `RUN_TASK_*`-Namen sind nun an aktive Runtimewerte beziehungsweise Verbrauchspfade gebunden |
<!-- audit:findings:end -->

## Entscheidungstabelle

<!-- audit:decision-table:begin -->
| Finding | Entscheidung | Umsetzung | Verifikation |
|---|---|---|---|
| C-01 | akzeptiert | Defaultfragmente aus `parse_args()`/Quota-Policy sowie Quell-/Rollenmatrix für Umgebungsnamen ergänzt | 11/11 fokussierte Tests; Claude `CLOSED`; Vollsuite 507/507 |
<!-- audit:decision-table:end -->

## Rückdokumentation in die Arbeitsplan-MD

Der Arbeitsplan verlinkt diese Slice-MD in §5.1 und aus der Slice-19-Überschrift. Tatsächlicher Scope, Validierungszahlen und Reviewstatus werden vor Commit final zurückdokumentiert.

## Freigabestatus

<!-- audit:approval-status:begin -->
- Implementierung bereit: `YES`
- Validierung: `PASS`
- Claude-Freigabe: `YES`
- Antigravity-Freigabe: `YES`
- Offene Blocker: `NONE`
- Commit autorisiert: `YES`
<!-- audit:approval-status:end -->
