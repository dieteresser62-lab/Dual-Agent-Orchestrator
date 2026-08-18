# Slice 11: Resumefähige Nutzer-Gates

**Status:** Implementierung und Validierung grün; Abschlussfreigaben und Commitstatus siehe verwaltete Auditabschnitte
**Feature-Branch:** `feature/orchestrator-modernization`
**Branch-Basis des Slice:** `683096d`
**GitHub-Status:** nur lokal; kein Upstream; Push und Merge sind nicht Bestandteil dieses Slice
**Arbeitsplan:** [orchestrator-modernization-work-plan.md](orchestrator-modernization-work-plan.md)

## Ziel des Slice

Die additive v3-Engine erkennt Teständerungen vor jedem Review und hält ohne eine exakt an den Testdiff gebundene Nutzerfreigabe resumefähig an. Derselbe Mechanismus trägt das optionale manuelle Slice-Gate und setzt bei geänderten, bereits planfreigegebenen Ankern die Planreviewkette kontrolliert zurück.

## Akzeptanzkriterien

- Konfigurierbare Testpfadmuster erkennen geänderte, neue, gelöschte und umbenannte Testdateien einschließlich unversionierter Dateien.
- Der Testfingerprint hängt nur vom kanonischen Test-Teildiff ab; reine Produktivänderungen invalidieren eine Testfreigabe nicht, weitere Teständerungen dagegen schon.
- Ohne passende Freigabe hält dieselbe Run-ID vor dem Reviewer in `awaiting_user_decision` mit Gategrund `test_change`; der Lauf meldet Exitcode 4 und ruft weder Reviewer noch Validierung auf.
- Eine ausdrückliche Entscheidung enthält Nutzer, Zeitpunkt, Begründung, Pfade und Fingerprint. Ablehnung bleibt pausiert; Zustimmung setzt denselben Schritt ohne Wiederholung bereits abgeschlossener Codex-Arbeit fort.
- `--resume --approve-gate` und die injizierbare Entscheidungs-API bilden den nichtinteraktiven beziehungsweise interaktiven Freigabepfad ab.
- Eine Zustimmung ist nur für den persistierten Gatefingerprint und exakt dieselben Pfade gültig.
- `--manual-slice-gate` verwendet vor dem Commit denselben resumefähigen Mechanismus.
- Eine Änderung persistierter planfreigegebener Anker hält mit Exitcode 4 in `awaiting_user_decision`, invalidiert vorherige Slice-Reviewfreigaben und setzt auf `codex_plan_revision` mit anschließendem Claude-Planreview zurück. Erst eine gebundene Nutzerzustimmung macht den neuen Ankerfingerprint zur Reviewbasis.
- Die Engine bleibt additiv; der aktive v2-Defaultpfad bleibt unverändert und grün.

## Scope und Nicht-Scope

### Scope

- `src/gates.py` (neu): Testpfadklassifikation, Test-Teildifffingerprint und Ankerfingerprint.
- `src/workflow_state.py`: strukturierte Gateevidenz, Nutzerentscheidungen und resumefähige Zustandsübergänge.
- `src/workflow.py`: Gateprüfung vor Review/Commit, Ankerreset, Freigabevalidierung und Exitcodeprojektion.
- `src/repo_changes.py`: kanonischer Fingerprint für eine ausgewählte Teilmenge geänderter Pfade.
- `src/cli.py`: additive explizite Gate-/Resume-Optionen und manueller Slice-Gate-Override.
- `src/audit_trail.py`: Zeitpunkt und Fingerprint in der Testfreigabeprojektion.
- Fokussierte Tests in `tests/test_gates.py`, `tests/test_workflow.py`, `tests/test_workflow_state.py`, `tests/test_repo_changes.py`, `tests/test_cli.py` und `tests/test_audit_trail.py`.
- Arbeitsplan, Übergabe und diese Slice-MD.

### Nicht-Scope

- Keine maschinellen Stop-Regeln oder produktive Zehn-Dateien-Grenze aus Slice 12.
- Kein Default-Cutover, keine Watch-Integration und keine Quota-/Instanzausfallbehandlung.
- Keine automatische Push-/Mergefreigabe.
- Keine Änderung des aktiven v2-Phasenworkflows.

## Diff-Risiko inklusive Branch- und Statuscheck

**Branch-Check:** `feature/orchestrator-modernization`; entspricht dem Arbeitsplan.

**Status vor Slice-Beginn:** sauber nach lokalem Slice-10-Commit `683096d`. `Inbox/.lock` war nach dem Windows-Neustart nicht vorhanden.

**Voraussichtliche Änderungstiefe:** hoch in State- und Workflow-Gatetransitionen, mittel in kanonischer Teilfingerprintbildung, niedrig im noch inaktiven CLI-Entwicklungsanschluss.

**Gefährdete bestehende Funktionen:** State-v3-Roundtrip/Resume, Slice-10-Aufrufreihenfolge, Attestierungscache und Review-/Commitfreigaben. Der aktive v2-Lauf wird nur durch Regressionstests berührt.

**Nicht anfassen:** `.orchestrator/state.json`, Checkpoints, Watcher, Stop-Regeln aus Slice 12, Quota- und Instanzpolitik, Default-Cutover sowie Remotes.

**Rollback-Strategie:** neue Gatekomponente dateiweise zurücknehmen und additive Felder/Transitionen per Gegenpatch entfernen. Kein Reset, Checkout oder History-Rewrite.

## Geplante Tests

- Bestehende, neue, gelöschte und in beide Richtungen umbenannte Testpfade.
- Test-Teildifffingerprint bleibt bei reiner Produktivänderung stabil und ändert sich bei Testinhalt/-pfad.
- Unfreigegebener Testdiff hält vor Validierung und Claude; Zustimmung setzt denselben Reviewschritt fort, Ablehnung bleibt pausiert.
- Fremder Fingerprint oder abweichende Pfade können ein Gate nicht freigeben; erneute Teständerung erzeugt ein neues Gate.
- Manueller Slice-Gate-Halt vor Commit und Resume ohne doppelten Reviewer-/Commitaufruf.
- Ankeränderung invalidiert Slice-Freigaben, setzt zur Codex-/Claude-Planreviewkette zurück und kehrt danach zum gespeicherten Slice-Schritt zurück.
- State-v3-Altform lädt weiterhin, neue Gateentscheidungen roundtrippen vollständig.
- CLI-Optionen verlangen eine explizite Resume-Entscheidung und einen Akteur.
- Auditprojektion enthält Nutzer, Zeitpunkt, Pfade und Fingerprint.
- Vollständige Suite, Compile, aktiver v2-Dry-Run und Diffcheck.

## Durchgeführte Änderungen

- `src/gates.py` klassifiziert Teständerungen mit konfigurierbaren POSIX-Globs. Bei Renames werden beide Pfade offengelegt; bestehende, neue, gelöschte, gestagte und unversionierte Änderungen fließen in denselben kanonischen Teildiff ein.
- `RepositoryChanges` bewahrt die beim Einsammeln erfassten Payload-Metadaten. `fingerprint_change_subset` bildet daraus einen stabilen Testfingerprint ohne TOCTOU-Nachlesen und ohne fachfremde Produktiv-/Dokumentationsänderungen; fehlen die erfassten Metadaten in einem handkonstruierten Objekt, hält die Funktion fail-closed.
- `GateRecord` trägt optionale Fingerprint-, Pfad- und Resumeevidenz. `GateDecisionRecord` persistiert Entscheidung, Grund, Nutzer, Offset-Zeitpunkt, Begründung und exakte Bindung; vor Slice 11 erzeugte v3-Records bleiben ladbar.
- Die v3-Engine hält unfreigegebene Teständerungen vor Attestierung und Reviewer, projiziert Exitcode 4, setzt nach einer gebundenen Zustimmung am gespeicherten Reviewschritt fort und verlangt bei geändertem Testfingerprint eine neue Entscheidung.
- Das manuelle Slice-Gate sitzt nach allen fachlichen Commitvoraussetzungen und unmittelbar vor dem mechanischen Commit. Resume wiederholt weder Codex noch Reviewer.
- Ankeränderungen persistieren zunächst nur Gate und ursprünglichen Fortsetzungskontext. Erst wenn der Nutzer zustimmt und der Ankerdiff weiterhin besteht, werden die wirksamen Slice-Reviewerfreigaben invalidiert und Codex-Planrevision sowie Claude-Planreview ausgelöst; ein vor der Zustimmung zurückgenommener Ankerdiff setzt den ursprünglichen Slice-Schritt ohne Planrevision fort. Alte und neue Anker stehen explizit im destillierten Promptkontext.
- `--manual-slice-gate`, `--approve-gate`, `--reject-gate`, `--gate-actor` und `--gate-rationale` bilden die additive spätere CLI-Verdrahtung ab; Entscheidungen verlangen ausdrücklich `--resume`. Der aktive v2-Lauf konsumiert diese v3-Optionen noch nicht.
- Die Auditprojektion übernimmt exakt die beim finalen Review aktive Testentscheidung statt der chronologisch letzten Entscheidung und zeigt Akteur, Zeitpunkt, Begründung, Pfade und Test-Diff-Fingerprint. Das manuelle Slice-Gate führt bei Renames Quell- und Zielpfad.

## Ausgeführte Validierung mit Ergebnis

153 fokussierte Gate-, Workflow-, State-, Repository-Diff-, CLI- und Audit-Tests sind bestanden. Die vollständige Suite umfasst 376 bestandene Tests. Compile, aktiver v2-Dry-Run und Diffcheck sind ebenfalls grün; die fingerprintgebundene Abschlussattestierung folgt im verwalteten Block.

## Abweichungen vom Plan

Der im Arbeitsplan noch veraltete Titel und Zielpfad wurden entsprechend dem bereits angenommenen Planfinding `C-15` auf den tatsächlichen Sammeltitel für resumefähige Nutzer-Gates korrigiert. Aus den Abschlussreviews kamen fünf angenommene Beobachtungen beziehungsweise Blocker hinzu: direkte Tests für den Korrektur-Resume- und den partiellen Fingerprint-Metadatenpfad sowie Korrekturen für aktive Testfreigabe, zurückgenommene Ankeränderung und Rename-Evidenz des manuellen Gates.

## Offene Risiken

- Die reale Default-CLI verwendet bis Slice 18 weiterhin v2; Slice 11 liefert die injizierbare Gate- und Entscheidungsmechanik für den späteren Anschluss.
- Ein Nutzerentscheidungs-Frontend darf die persistierten Fingerprint- und Pfadwerte später nur bestätigen, niemals aus frei eingegebenen Ersatzwerten rekonstruieren.

## Review-Feedback von Claude

<!-- audit:claude-review:begin -->
### Runde 1 — vollständiger Slice, Fingerprint `7cf3b355…`

- Reviewer: `claude` (Sonnet/High); Freigabe `YES`; Observation `C-01`.
- Scope: ausschließlich vollständiger Slice-11-Diff aller 15 Pfade seit `683096d`, keine unveränderten Repositorydateien.
- Ergebnis: direkter Test für die Rückkehr nach `CODEX_CORRECTION` angefordert. Ein kompakter formaler Reparaturaufruf und eine deterministische Evidenzmarker-Normalisierung änderten das fachliche Verdikt nicht.

### Runde 2 — C-01-Korrekturdelta, Fingerprint `bfc079e3…`

- Freigabe `YES`; `C-01` geschlossen; Observation `C-02` neu.
- Scope: ausschließlich neuer Korrektur-Resume-Test, fail-closed Metadatenhärtung und neue Attestierung.

### Runde 3 — C-02-Korrekturdelta, Fingerprint `deeb4bfa…`

- Freigabe `YES`; `C-01` und `C-02` geschlossen; keine neuen Findings.
- Scope: ausschließlich direkter Negativtest für nichtleere, aber unvollständige Fingerprint-Metadaten und neue Attestierung.

### Runde 4 — A-01/A-02/A-03-Korrekturdelta, Fingerprint `5ae5734b…`

- Freigabe `YES`; Claude-eigene Findings bleiben geschlossen, Antigravity-eigene Findings unverändert offen für dessen Finalentscheidung.
- Scope: ausschließlich aktive Testfreigabebindung, verzögerter Ankerreset, Rename-Gatepfade, zugehörige State-Kompatibilitätstests und neue Attestierung; kein vollständiger Slice-Diff.

### Runde 5 — finaler Dokumentationsdelta, Fingerprint `6e254067…`

- Freigabe `YES`; alle fünf Findings bleiben geschlossen.
- Scope: ausschließlich konsistente Testzahlen, Review-/Findingprojektion, Arbeitsplan-/Übergabestatus und die nach den semantischen Dokumentänderungen frisch ausgeführte Attestierung; kein Source- oder Testcodediff.
<!-- audit:claude-review:end -->

## Review-Feedback von Antigravity

<!-- audit:antigravity-review:begin -->
### Runde 1 — vollständiger Slice, Fingerprint `deeb4bfa…`

- Reviewer: `antigravity` (Gemini 3.1 Pro High/High); Freigabe `NO`.
- Scope: vollständiger Slice-11-Diff aller 15 Pfade.
- Findings: Blocker `A-01` zur Auswahl der tatsächlich aktiven Testfreigabe sowie Observations `A-02` zum vorzeitigen Ankerreset und `A-03` zum fehlenden Rename-Quellpfad im manuellen Gate.

### Runde 2 — vollständiger korrigierter Slice, Fingerprint `5ae5734b…`

- Freigabe `YES`; `A-01`, `A-02` und `A-03` geschlossen; `C-01` und `C-02` bleiben geschlossen.
- Scope: erneut vollständiger finaler Slice-11-Diff aller 15 Pfade und neue Attestierung.
- Ergebnis: aktive Testentscheidung, revertierbarer Ankerhalt und vollständige Rename-Evidenz erfüllen die jeweiligen Akzeptanztests; keine neuen Findings.

### Runde 3 — vollständiger Commitkandidat, Fingerprint `6e254067…`

- Freigabe `YES`; alle fünf Findings bleiben geschlossen; keine neuen Findings.
- Scope: vollständiger Slice-11-Diff aller 15 Pfade einschließlich finaler Dokumentation und frisch gebundener Attestierung.
- Ergebnis: Gatezustände, Teilfingerprints, Renamegrenzen, Anker-Revert und Dokumentationskonsistenz sind commitbereit.
<!-- audit:antigravity-review:end -->

## Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
- `C-01`: **angenommen** — direkter Workflowtest beweist `CODEX_CORRECTION` als gespeicherten Fortsetzungsschritt und die exakte Aufrufkette Planrevision → Korrektur.
- `C-02`: **angenommen** — separater Negativtest erreicht mit nichtleeren, aber unvollständigen Capture-Metadaten gezielt den Coverage-Gap-Zweig von `fingerprint_change_subset`.
- `A-01`: **angenommen** — der Work-Unit-State bindet nun die beim Review tatsächlich aktive Testfreigabe; die Auditprojektion löst exakt diesen Fingerprint/Pfadsatz auf.
- `A-02`: **angenommen** — Gateerzeugung verändert Schritt und Freigaben noch nicht. Nur ein zugestimmter, weiterhin bestehender Ankerdiff löst den Planreset aus; ein Revert setzt den ursprünglichen Schritt fort.
- `A-03`: **angenommen** — `WorkflowChanges.user_gate_paths` führt für das manuelle Gate beide Rename-Seiten, ohne den kanonischen Review-/Scopepfad zu verändern.
<!-- audit:codex-responses:end -->

## Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### `slice11-validation-6e254067`

- Diff-Fingerprint: `6e2540673d47e52d7ea9a290d5c4e16b7fa78cfc66648e16bf7a2250a140be95`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: 376 passed; 153 focused passed; compile PASS; active v2 dry-run PASS; diff check PASS; slice document PASS
- Ausgabedigest: `1c352738daa2b234585fe98898740fe28670bcd45c50bb691c1c168886b79acd`

| Matrixbefehl | Status | Exitcode |
|---|---|---:|
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -q -p no:cacheprovider` | PASS | 0 |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_gates.py tests/test_workflow.py tests/test_workflow_state.py tests/test_repo_changes.py tests/test_cli.py tests/test_audit_trail.py -q -p no:cacheprovider` | PASS | 0 |
| `python3 -m py_compile src/gates.py src/workflow.py src/workflow_state.py src/repo_changes.py src/cli.py src/audit_trail.py src/orchestrator.py` | PASS | 0 |
| `./run_task --dry-run --skip-git-check --test-command '' --agent-output none` | PASS | 0 |
| `git diff --check` | PASS | 0 |
| Slice-Dokumentvalidator | PASS | 0 |
<!-- audit:validation-attestation:end -->

## Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
Teständerungen sind durch die arbeitsplanweite Revision-8-Ausnahme autorisiert.

Vorgesehene Testpfade: `tests/test_gates.py`, `tests/test_workflow.py`, `tests/test_workflow_state.py`, `tests/test_repo_changes.py`, `tests/test_cli.py`, `tests/test_audit_trail.py`.
<!-- audit:test-approval-premortem:end -->

## Findings-Lebenszyklus

<!-- audit:findings:begin -->
### `C-01` — `CLOSED` (OBSERVATION)

Der direkte Regressionstest belegt den gespeicherten `CODEX_CORRECTION`-Fortsetzungsschritt nach Ankerzustimmung und Planreview.

### `C-02` — `CLOSED` (OBSERVATION)

Ein isolierter Test belegt den fail-closed Fehler bei nichtleeren, aber unvollständigen Fingerprint-Metadaten.

### `A-01` — `CLOSED` (BLOCKER)

Die Auditprojektion ist an die beim finalen Review aktive Testfreigabe gebunden und kann bei der Folge A → B → A nicht mehr fälschlich B ausweisen.

### `A-02` — `CLOSED` (OBSERVATION)

Der Ankerreset erfolgt erst nach Zustimmung und erneuter Feststellung desselben Ankerdiffs; ein zurückgenommener Diff setzt den ursprünglichen Schritt ohne Planrevision fort.

### `A-03` — `CLOSED` (OBSERVATION)

Das manuelle Slice-Gate persistiert bei Renames Quell- und Zielpfad.
<!-- audit:findings:end -->

## Entscheidungstabelle

<!-- audit:decision-table:begin -->
| ID | Quelle | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|
| C-01 | claude | OBSERVATION | angenommen/geschlossen | direkter `CODEX_CORRECTION`-Resume-Test |
| C-02 | claude | OBSERVATION | angenommen/geschlossen | partieller Capture-Metadaten-Negativtest |
| A-01 | antigravity | BLOCKER | angenommen/geschlossen | explizite aktive Testfreigabe im Work-Unit-State und exakte Auditauflösung |
| A-02 | antigravity | OBSERVATION | angenommen/geschlossen | Ankerreset erst bei zugestimmtem, weiterhin bestehendem Diff; Revert-Test |
| A-03 | antigravity | OBSERVATION | angenommen/geschlossen | beide Rename-Pfade im manuellen Gate |
<!-- audit:decision-table:end -->

## Rückdokumentation in die Arbeitsplan-MD

Der Arbeitsplan verlinkt diese Slice-MD aus §5.1 und der Slice-11-Überschrift. Tatsächlicher Scope, finale Testzahlen und Freigabestatus sind ergänzt; die Übergabe enthält denselben Abschlussstand.

## Freigabestatus

<!-- audit:approval-status:begin -->
- Implementierung bereit: `YES`
- Validierung: `PASS` (`slice11-validation-6e254067`)
- Claude-Freigabe: `YES`
- Antigravity-Freigabe: `YES`
- Offene Blocker: `NONE`
- Commit autorisiert: `YES`
<!-- audit:approval-status:end -->
