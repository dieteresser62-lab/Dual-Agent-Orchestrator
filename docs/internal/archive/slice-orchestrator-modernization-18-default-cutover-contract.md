# Slice 18: Defaultaktivierung und Contract-Cutover

**Status:** Implementierung und Vollvalidierung abgeschlossen; externe Reviews laufen
**Feature-Branch:** `feature/orchestrator-modernization`
**Branch-Basis des Slice:** `0db6273`
**GitHub-Status:** nur lokal; kein Upstream; Push und Merge sind nicht Bestandteil dieses Slice
**Arbeitsplan:** [orchestrator-modernization-work-plan.md](orchestrator-modernization-work-plan.md)

## Ziel des Slice

Der vollständig integrierte State-v3-Slice-Workflow wird zum einzigen produktiven CLI- und Watch-Pfad. Der alte Zwei-Phasen-Ablauf, sein Entwicklungsflag, phasenbezogene Optionen, Prompts, Marker und Parser werden atomar entfernt. Gleichzeitig erhalten AGENTS, Codex, Claude und Antigravity einen gemeinsamen schlanken Rollen-, Findings-, Validierungs- und Freigabevertrag.

## Akzeptanzkriterien

- State v3 ist ohne Entwicklungsflag der einzige Default für Einzel-, Resume- und Watch-Aufrufe.
- Der produktive Driver verbindet echte Agentenadapter, kanonische Diffs, Validierungsmatrix, Gates, Audit, lokale Git-Commits und branchweites Finalreview mit derselben `WorkflowEngine`.
- `--development-mode`, `--from-phase` und weitere reine Phase-v2-Optionen werden ausdrücklich als unbekannt abgelehnt.
- `run_phase1`, `run_phase2`, Phase-Prompts, Phase-Marker und Legacy-Parserpfade sind aus aktiver Produktion und Tests entfernt.
- Watch-Aufrufe treiben v3 bis zu terminalem Gesamterfolg oder einem typisierten resumefähigen Halt; eine zwischenabgeschlossene Slice-Work-Unit wird nicht als technischer Fehler zurückgegeben.
- Dry-Run, Resume, Watch und lokale Commitfolge funktionieren ohne Entwicklungsflag; aktive oder abgeschlossene v2-States werden nicht still migriert.
- `AGENTS.md`, `CLAUDE.md`, `CODEX.md` und die neue `ANTIGRAVITY.md` enthalten denselben State-v3-Contract; `GEMINI.md` entfällt.
- Nur der Orchestrator führt deterministische Validierung aus. Agentenantworten dürfen kein `VALIDATION_RESULT` enthalten und benötigen für Freigaben die fingerprintgebundene Attestierung.
- Adversariales Review, fünf Prüfdimensionen, Finding-/Prüfrecords, Pre-Mortem, Verdiktkonsistenz und Rollenrechte sind synchron dokumentiert und maschinell getestet.
- Der Stand ist nach dem atomaren Cutover startbar und vollständig bedienbar; Nutzerdokumentation und Diagramm folgen ohne Workflowänderung in Slice 19.

## Scope und Nicht-Scope

### Scope

- Produktive State-v3-CLI-/Watch-Verdrahtung und Real-Driver.
- Entfernung des aktiven Phase-v2-Ablaufs und seiner öffentlichen Optionen, Prompts und Parser.
- Gemeinsamer Root-Instruktions- und Markercontract für Codex, Claude und Antigravity.
- Default-End-to-End-, Resume-, Watch-, Commit-, Hilfe- und Sprach-/Contracttests.

### Nicht-Scope

- Keine vollständige README-, Diagramm- oder Beispieltask-Überarbeitung; sie folgt in Slice 19.
- Kein Push, Merge oder Remotezugriff.
- Keine manuelle Änderung von `.orchestrator/state.json` oder Checkpoints.
- Keine Migration unvollständiger v2-Läufe in v3.

## Diff-Risiko inklusive Branch- und Statuscheck

**Branch-Check:** `feature/orchestrator-modernization`; entspricht dem Arbeitsplan.

**Status vor Slice-Beginn:** sauber nach lokalem Slice-17-Commit `0db6273`. `Inbox/.lock` war nicht vorhanden.

**Änderungstiefe:** sehr hoch: atomarer Runtime-, CLI-, Prompt-, Parser- und Instruktionscutover. Der letzte grüne Rückfallpunkt ist der lokale Slice-17-Commit.

**Gefährdete bestehende Funktionen:** Einstiegspunkt, Resume, Watch, Stateklassifikation, Agentenreihenfolge, Validierung, lokale Commits, Finalreview, Hilfevertrag und Root-Instruktionskonsistenz.

**Dateigrenze:** geplant höchstens zehn produktive Programm- oder Konfigurationsdateien; Tests und interne Dokumentation zählen nicht als produktiv. Eine Überschreitung hält vor weiterer Umsetzung an.

**Nicht anfassen:** Remotes, Push/Merge, `.orchestrator/state.json`, Checkpoints sowie die für Slice 19 reservierte vollständige Nutzerdokumentation.

**Rollback-Strategie:** ausschließlich gezielte Gegenpatches auf den Cutover-Dateien oder Rückkehr zum bereits committierten Slice-17-Stand durch einen neuen Revert-Commit; kein Reset, Checkout oder History-Rewrite.

## Geplante Tests

- Default-End-to-End-Dry-Run einschließlich Plan, mindestens zwei Slice-Commits und branchweitem Finalreview ohne Entwicklungsflag.
- Resume von jedem persistierbaren v3-Schritt und fail-closed Ablehnung aktiver/inkompatibler v2-States.
- Watch-Erfolg, Gate-/Quota-/Instanzhalt, Prozessneustart und keine Zwischenzustands-Poisonisierung.
- Ablehnung von Entwicklungsflag, `--from-phase` und entfernten Phase-v2-Optionen sowie Hilfe-Snapshot ohne Altbegriffe.
- Root-Instruktions-, Prompt-, Parser-, Marker-, Rollen- und Sprachkonsistenz.
- Vollsuite, Compile, positiver Default-Dry-Run, Diffcheck und Dokumentvalidator.

## Durchgeführte Änderungen

- `src/orchestrator.py` führt Plan, beliebig viele persistierte Slices, lokale Commits, Resume und branchweites Finalreview über die bestehende `WorkflowEngine` als einzigen produktiven Pfad aus. Der Real-Driver bindet Agentenadapter, kanonische Diffs, dynamische Test-Gates, Validierungsmatrix, Auditprojektion, State/Checkpoints und Git-Transaktionen.
- `SLICE_PLAN` persistiert 1-basierte Zusammenfassungen und exakte Pfad-Allowlists. Die vollständige typisierte Work-Unit-Historie wird atomar im State gesichert und nach Prozessneustart rekonstruiert.
- Die CLI entfernt Entwicklungsmodus, Phasenoptionen, Altzyklen-/Retry-/Snapshotoptionen und aktiviert v3 für Einzel- und Watch-Aufrufe. Der eingebaute Dry-Run durchläuft Plan, zwei Slice-Commits und Finalreview ohne Agenten- oder Repositoryschreibzugriff.
- Legacy-Orchestrator, Phasenprompts und Legacy-Parsertests wurden entfernt. Reviewer-Validierung bleibt verboten; Testpfade und fingerprintgebundene Attestierungen werden vom Orchestrator bestimmt.
- `GEMINI.md` wurde durch `ANTIGRAVITY.md` ersetzt. Die vier Root-Instruktionsdateien dokumentieren denselben asymmetrischen Rollen-, Findings-, Evidenz-, Pre-Mortem- und Markercontract; Claude bleibt persistent Sonnet/High.
- Default-, Resume-, Real-Git-Commit-, Watch-, CLI-Ablehnungs-, Root-Contract-, Prompt- und Parsertests decken den atomaren Cutover ab.

## Ausgeführte Validierung mit Ergebnis

- `python3 -m compileall -q src tests` → PASS.
- `python3 -m pytest tests/ -v` → PASS, `500 passed`, Exitcode 0 (nach Antigravity-Runde 1).
- `./run_task --dry-run --task-file docs/internal/slice-orchestrator-modernization-18-default-cutover-contract.md --quiet` → PASS, Exitcode 0; Plan, zwei Commits und vier Validierungsattestierungen im speicherinternen Szenario.
- Produktiver Wegwerf-Repositorytest → PASS: Antigravity-Unterbrechung, State-v3-Resume, zwei echte lokale Git-Commits und terminales Finalreview.
- `git diff --check` → PASS; ausschließlich erwartete WSL-Zeilenendewarnungen.
- `validate_slice_document(...)` für Slice 18 → PASS.

## Abweichungen vom Plan

Die zunächst separat entworfene Runtime-Brücke wurde vor Review in `src/orchestrator.py` integriert. Damit bleibt der atomare Cutover innerhalb der vorgesehenen Produktivdateigrenze. Für produktives Resume waren zusätzlich additive State-v3-Felder für den Slice-Plan und die typisierte Review-Historie erforderlich; es entstand kein paralleler Ausführungspfad.

## Offene Risiken

- Die vollständige Nutzerbeschreibung ist absichtlich Slice 19 vorbehalten. Bis dahin ist die CLI-Hilfe der aktuelle öffentliche Optionsvertrag; die noch v2-geprägten README-Abschnitte dürfen nicht als Runtimevertrag interpretiert werden.
- Echte Agenten werden für die Abschlussreviews gezielt auf den Slice-Diff angesetzt. Der kostenfreie Default-Dry-Run und der Wegwerf-Repositorytest belegen die Orchestrierung; ein kompletter kostenpflichtiger Live-End-to-End-Lauf ist nicht Bestandteil dieses Slice.

## Review-Feedback von Claude

<!-- audit:claude-review:begin -->
Claude Sonnet mit Effort `high` prüfte im ersten Lauf gezielt den fachlichen Slice-18-Diff. C-01 beanstandete, dass `SLICE_PLAN` interne `.orchestrator/`-Pfade nicht ausschloss; Codex ergänzte Parser-, State- und Regressionstestgrenzen. C-02 fragte nach einem möglichen stillen Rebinding bei Plan-/HEAD-Drift; die bestehende fail-closed State-Invariante blieb unverändert und erhielt einen ausdrücklichen Resume-/Drifttest. Claude schloss C-01 und C-02 und erteilte `SLICE_APPROVAL: 18 | YES`.

Nach den drei Antigravity-Korrekturen erhielt Claude ausschließlich deren Code- und Testdelta samt Attestierung, nicht erneut den gesamten Repositoryinhalt. Claude bestätigte `SLICE-HEAD-DRIFT`, `NO-IMPLEMENTATION-CHANGES` und das Work-Unit-Historienarchiv, sah keine Regression von C-01/C-02 und genehmigte den fachlichen Fingerprint `48b325bd2566116ab8e662ff35ed2440dbf07554d12e28bb0cacda6c62aa55ef`. Eine reine Markerreparatur ergänzte anschließend das zunächst fehlende `TEST_FILES_TOUCHED`, ohne Evidenz oder Verdikt erneut zu prüfen.
<!-- audit:claude-review:end -->

## Review-Feedback von Antigravity

<!-- audit:antigravity-review:begin -->
Antigravity prüfte nach Claudes Freigabe einmalig den vollständigen aktuellen Slice-18-Diff. Runde 1 öffnete drei Blocker: A-01 für einen untypisierten Plan-/HEAD-Drift, A-02 für eine leere Implementierung mit Ausnahme statt resumefähigem Halt und A-03 für verlorene Historien abgeschlossener Work Units.

Nach Umsetzung und Claudes gezielter Korrekturfreigabe schloss Antigravity A-01 bis A-03 ausdrücklich: HEAD-Drift erzeugt den persistierten `SLICE-HEAD-DRIFT`-Halt, leere Implementierungen den typisierten `NO-IMPLEMENTATION-CHANGES`-Halt, und abgeschlossene Historien bleiben neben der terminalen Finalreview-Historie auf Disk erhalten. Eine reine Markerreparatur ergänzte `TEST_FILES_TOUCHED`. Das maschinell validierte Endverdikt lautet `SLICE_APPROVAL: 18 | YES` für Fingerprint `48b325bd2566116ab8e662ff35ed2440dbf07554d12e28bb0cacda6c62aa55ef`.
<!-- audit:antigravity-review:end -->

## Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
FINDING_RESPONSE: C-01 | ACCEPTED | `.orchestrator/` wird jetzt sowohl im `SLICE_PLAN`-Parser als auch an der direkten State-Scope-Grenze abgelehnt; Parser- und State-Regressionstests sind grün.

FINDING_RESPONSE: C-02 | REJECTED | Ein stilles Rebinding war bereits ausgeschlossen: `bind_current_slice_git_boundary` verlangt den persistierten `start_commit` und wirft bei abweichendem HEAD fail-closed. Der fehlende explizite Regressionstest wurde ergänzt und belegt genau diese Resume-/HEAD-Drift-Sperre.

FINDING_RESPONSE: A-01 | ACCEPTED | HEAD-Drift zwischen Plan und Slice-Start erzeugt jetzt einen persistierten `SLICE-HEAD-DRIFT`-Gate-Halt mit Exitcode 4; der Wegwerf-Repositorytest belegt State und Resume-Identität.

FINDING_RESPONSE: A-02 | ACCEPTED | Eine leere, als bereit gemeldete Implementierung wird als `NO-IMPLEMENTATION-CHANGES`-Gate mit Exitcode 4 persistiert; direkter Session- und öffentlicher CLI-Pfad sind getestet.

FINDING_RESPONSE: A-03 | ACCEPTED | `runtime_history` enthält jetzt `current` plus ein Work-Unit-Archiv. Der produktive Test prüft archivierte Plan- und Slice-Historien sowie die vollständige terminale Finalreview-Historie und den Completed-State auf Disk.
<!-- audit:codex-responses:end -->

## Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### `validation-48b325bd2566`

- Fachlicher Diff-Fingerprint vor deterministischer Auditprojektion: `48b325bd2566116ab8e662ff35ed2440dbf07554d12e28bb0cacda6c62aa55ef`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: Compile PASS; 500 Tests PASS; Default-State-v3-Dry-Run PASS
- Ausgabedigest: `7586c3cb1f317c1ff098707b61447f4ee3d7d0228c637df3b2e692401865ff35`

| Matrixbefehl | Status | Exitcode |
|---|---|---:|
| `python3 -m compileall -q src tests` | PASS | 0 |
| `python3 -m pytest tests/ -v` | PASS | 0 |
| `./run_task --dry-run --task-file docs/internal/slice-orchestrator-modernization-18-default-cutover-contract.md --quiet` | PASS | 0 |

Die nach den Reviewentscheidungen eingetragenen verwalteten Auditblöcke sind eine deterministische Projektion der bereits validierten Antworten und gehören nicht zum fachlichen Reviewfingerprint.
<!-- audit:validation-attestation:end -->

## Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
Teständerungen sind durch die arbeitsplanweite Revision-8-Ausnahme autorisiert.

Vorgesehene Testpfade: `tests/test_cli.py`, `tests/test_language_consistency.py`, `tests/test_parsing.py`, `tests/test_prompts.py`, `tests/test_workflow.py`, `tests/test_inbox_watcher.py`, `tests/test_orchestrator_watch_cli.py` und erforderliche bestehende Runtime-/State-Regressionspfade.

Pre-Mortem: Am wahrscheinlichsten bleibt nach dem scheinbar erfolgreichen Cutover ein einzelner indirekter v2-Verweis in CLI, Hilfe, Root-Instruktionen oder Parser erhalten. Dann erzeugt derselbe Agentenoutput je Einstiegspfad unterschiedliche Semantik. Negativtests über öffentliche Optionen, Root-Dateien, Prompts und Quellreferenzen müssen deshalb Legacybegriffe und Phase-Marker als zusammenhängenden Altvertrag behandeln.
<!-- audit:test-approval-premortem:end -->

## Findings-Lebenszyklus

<!-- audit:findings:begin -->
| ID | Reporter | Klasse | Status | Kurzbegründung |
|---|---|---|---|---|
| C-01 | claude | OBSERVATION | CLOSED | `.orchestrator/` wird an Parser- und State-Grenze abgelehnt |
| C-02 | claude | OBSERVATION | CLOSED | persistierter Slice-Startcommit kann bei HEAD-Drift nicht still ersetzt werden |
| A-01 | antigravity | BLOCKER | CLOSED | Plan-/HEAD-Drift hält typisiert und persistiert |
| A-02 | antigravity | BLOCKER | CLOSED | leere Implementierung hält typisiert statt als CLI-Crash |
| A-03 | antigravity | BLOCKER | CLOSED | abgeschlossene Work-Unit-Historien werden dauerhaft archiviert |
<!-- audit:findings:end -->

## Entscheidungstabelle

<!-- audit:decision-table:begin -->
| Finding | Entscheidung | Umsetzung | Verifikation |
|---|---|---|---|
| C-01 | akzeptiert | interne Orchestratorpfade in Plan und State abgelehnt | Parser-/State-Regressionssuite; Claude `CLOSED` |
| C-02 | begründet zurückgewiesen, Testlücke geschlossen | vorhandene fail-closed Rebind-Invariante direkt getestet | Claude `CLOSED` |
| A-01 | akzeptiert | persistierter `SLICE-HEAD-DRIFT`-Gate-Halt | Wegwerf-Git-Repositorytest; Antigravity `CLOSED` |
| A-02 | akzeptiert | persistierter `NO-IMPLEMENTATION-CHANGES`-Gate-Halt | Session- und öffentlicher CLI-Test; Antigravity `CLOSED` |
| A-03 | akzeptiert | `runtime_history` mit `current` und dedupliziertem `archive` | terminaler Disk-State im Produktivtest; Antigravity `CLOSED` |
<!-- audit:decision-table:end -->

## Rückdokumentation in die Arbeitsplan-MD

Der Arbeitsplan verlinkt diese Slice-MD in §5.1 und aus der Slice-18-Überschrift. Tatsächlicher Scope, Validierungszahlen und Reviewstatus werden vor Commit final zurückdokumentiert.

## Freigabestatus

<!-- audit:approval-status:begin -->
- Implementierung bereit: `YES`
- Validierung: `PASS`
- Claude-Freigabe: `YES`
- Antigravity-Freigabe: `YES`
- Offene Blocker: `NONE`
- Commit autorisiert: `YES`
<!-- audit:approval-status:end -->
