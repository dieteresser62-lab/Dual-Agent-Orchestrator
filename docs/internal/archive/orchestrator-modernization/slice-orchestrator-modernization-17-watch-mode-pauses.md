# Slice 17: Watch-Modus und pausierte Läufe

**Status:** Implementierung und lokale Abschlussmatrix grün; Reviewverlauf, Freigaben und Commitstatus stehen ausschließlich in den verwalteten Auditabschnitten
**Feature-Branch:** `feature/orchestrator-modernization`
**Branch-Basis des Slice:** `e2bfcd4`
**GitHub-Status:** nur lokal; kein Upstream; Push und Merge sind nicht Bestandteil dieses Slice
**Arbeitsplan:** [orchestrator-modernization-work-plan.md](orchestrator-modernization-work-plan.md)

## Ziel des Slice

Der bestehende FIFO-Inbox-Watcher erhält eine additive, typisierte State-v3-Grenze. Jeder Task behält über technische Wiederholungen und Prozessneustarts eine stabile Run-ID; Nutzer-/Policy-Gates, nicht automatisch fortsetzbare Quota und Instanzausfälle halten Task und Queue kontrolliert, ohne Retry- oder Poison-Zähler zu erhöhen. Nur ein vollständig committierter v3-Lauf mit abgeschlossenem branchweitem Finalreview darf in die Done-Outbox verschoben werden.

## Akzeptanzkriterien

- Jeder Inbox-Task besitzt eine isolierte, atomar persistierte Watch-Identität mit stabiler Run-ID.
- Der erste Aufruf startet neu; jeder weitere Aufruf desselben Tasks setzt denselben Run mit `--resume` fort.
- Nutzer-/Policy-Gates liefern Exitcode 4, bleiben in der Inbox und erhöhen weder Retry- noch Poison-Zähler; die Queue verarbeitet keinen Folgetask.
- Terminierbare automatische Quota wartet in derselben WorkflowEngine mit dem bestehenden knappen Heartbeat und setzt exakt Rolle, Work Unit und Schritt fort.
- Nicht terminierbare Quota und Instanzausfälle behalten Exitcode 2 beziehungsweise 3, bleiben resumefähig und werden nicht als technische Watch-Fehler gezählt.
- Prozessunterbrechung bewahrt Taskidentität und State; ein neuer Watch-Prozess setzt mit derselben Run-ID fort.
- v3-Erfolg ist nur dann Done-fähig, wenn alle Slices committiert sind und die aktuelle Final-Review-Work-Unit abgeschlossen ist.
- Commitbedingte Änderungen werden beim Watch-Resume nicht durch einen falschen Dirty-Tree-Preflight blockiert.
- Bestehendes Locking, Success-Marker, FIFO, technische Retries, Poison-Pill und Move-Fehlerbehandlung bleiben regressionsgeschützt.
- Der aktive v2-Defaultpfad bleibt bis Slice 18 unverändert.

## Scope und Nicht-Scope

### Scope

- Typisierte Watch-Ergebnisse und WorkflowResult-Projektion.
- Persistente pro-Task-Run-Identität und Resume-Aufrufkontext.
- Kontrollierter Queue-Halt für v3-Gates, Quota und Instanzausfälle.
- Strenge v3-Done-Voraussetzung: Slice-Commits plus abgeschlossenes Finalreview.
- Watcher-, CLI- und Workflow-Regressionsprüfungen einschließlich Fake Clock.

### Nicht-Scope

- Kein Default-Cutover auf State v3; er folgt in Slice 18.
- Keine Entfernung von Phase-v2-Optionen oder Legacy-Markern.
- Keine Root-Instruktions- oder Nutzerdokumentationsumstellung.
- Kein Push, Merge oder Remotezugriff.
- Keine manuelle Änderung von `.orchestrator/state.json` oder Checkpoints.

## Diff-Risiko inklusive Branch- und Statuscheck

**Branch-Check:** `feature/orchestrator-modernization`; entspricht dem Arbeitsplan.

**Status vor Slice-Beginn:** sauber nach lokalem Slice-16-Commit `e2bfcd4`. `Inbox/.lock` war nicht vorhanden.

**Änderungstiefe:** hoch am langlebigen Watch-Loop und mittel an der additiven v3-Ergebnisgrenze. Der v2-Einzel- und Defaultpfad bleibt aktiv.

**Gefährdete bestehende Funktionen:** FIFO-Reihenfolge, technische Retry-/Poison-Zähler, Success-Marker, Move-only-Retry, Lockfreigabe, Run-State-Resume und Watch-CLI-Defaults.

**Dateigrenze:** voraussichtlich höchstens vier produktive Python-Dateien; damit unter der Grenze von zehn. Tests und interne Dokumentation zählen nicht als produktive Dateien.

**Nicht anfassen:** Default-Cutover, Root-Agentenverträge, Remotes, `.orchestrator/state.json` und Checkpoints.

**Rollback-Strategie:** typisierte Ergebnis-/Identitätsgrenze und additive CLI-Weitergabe durch gezielte Gegenpatches entfernen; bestehende Integer-Ergebnisbehandlung bleibt als Rückfallpfad erhalten. Kein Reset, Checkout oder History-Rewrite.

## Geplante Tests

- Stabile Run-ID je Task, unterschiedliche Run-IDs für zwei Tasks und atomarer Sidecar-Roundtrip.
- Technischer Retry setzt denselben Task mit Resume fort und zählt weiterhin bis Poison.
- Nutzer-/Policy-Gate hält Queue ohne Attempts/Poison.
- Quota 2 und Instanzausfall 3 halten Queue ohne Attempts/Poison; Neustart setzt identische Run-ID fort.
- Terminierbare Quota mit Fake Clock, Heartbeat und Fortsetzung desselben Workflow-Schritts.
- Mehrere Slice-Commits plus Finalreview erlauben Done-Move; bloß abgeschlossener Slice oder unvollständiger Finalreview nicht.
- Commitbedingte Dirty-Tree-Änderungen bei Watch-Resume lösen keinen falschen Preflight aus.
- Bestehende Watcher- und CLI-Suite, Vollsuite, Compile, aktiver v2-Dry-Run, Diffcheck und Dokumentvalidator.

## Durchgeführte Änderungen

- `WatchTaskIdentity` bindet jeden Inbox-Task über SHA-256 an eine atomar geschriebene, versteckte Sidecar-Datei. Der erste Aufruf erzeugt eine eindeutige Run-ID; technische Wiederholungen, Watch-Neustarts und Gate-Resumes verwenden dieselbe Identität.
- Der Watcher übergibt pro Task eine isolierte Argumentkopie. Neu gestartete Tasks setzen `force_new`; jeder bereits gestartete Task setzt `resume`, behält seine Run-ID und verwendet den bestehenden Watch-Default zum Überspringen des Dirty-Tree-Preflights.
- `WatchTaskResult` trennt terminalen Workflow-Erfolg, resumefähigen Halt und technischen Fehler. Exitcodes 2, 3 und 4 halten Task und gesamte FIFO-Queue ohne Attempts oder Poison; technische Fehler behalten die bisherige Retry-, Poison- und Stuck-Semantik.
- Taskänderungen nach Beginn, beschädigte Sidecars und eine abweichende Ergebnis-Run-ID stoppen fail-closed, ohne den pausierten Task als technischen Retry zu verbrauchen.
- `WorkflowRunResult.workflow_completed` verlangt einen abgeschlossenen `FINAL_REVIEW`, Schritt `COMPLETED` und ausschließlich committierte Slices. Damit genügt ein bloß abgeschlossener Plan-, Slice- oder Korrekturschritt nicht für den Done-Move.
- Der aktive v2-Orchestrator übernimmt eine vom Watcher vorgegebene Run-ID in seine Artefakte und weist Resume gegen einen fremden Run oder Task zurück. Außerhalb des Watch-Kontexts bleibt seine Run-ID-Erzeugung unverändert.
- Nach dem ersten Claude-Review schließen zwei zusätzliche Orchestrator-Grenztests die frühen Crash-/Resume-Fenster ausdrücklich: `--resume` ohne vorhandenen State initialisiert sicher mit derselben Watch-Run-ID; vorhandener State eines fremden Runs oder Tasks wird ohne Ausführung abgelehnt.
- Success-Marker, Move-only-Retry, Poison-/Stuck-Aufräumen und erfolgreicher Done-Move entfernen nun auch die zugehörige Watch-Identität; bei Prozessunterbrechung bleibt sie ausdrücklich erhalten.

## Ausgeführte Validierung mit Ergebnis

Alle 530 Tests der Vollsuite und 89 fokussierte Watcher-, Watch-CLI- und Workflow-Tests sind grün. Compile der drei produktiven Python-Dateien, aktiver v2-Dry-Run, Diffcheck und root-gebundener Slice-17-Dokumentvalidator sind ebenfalls bestanden.

## Abweichungen vom Plan

`src/cli.py` musste nicht geändert werden: Der bestehende Watch-Aufruf reicht bereits dieselbe Namespace-Instanz und den Pipeline-Callback an `watch_inbox`; die isolierte Resume-/Run-ID-Anreicherung liegt enger und sicherer im Watcher. `src/workflow_state.py` blieb ebenfalls unverändert, weil seine vorhandenen Work-Unit-Status, Exitcodeklassen, Slice-Commitinvariante und Resume-Cursor den typisierten Watch-Rand vollständig tragen. Stattdessen war eine kleine additive Prüfung in `src/orchestrator.py` erforderlich, damit auch der bis Slice 18 aktive v2-Callback die stabile Watch-Run-ID in Artefakten persistiert und fremden State beim Resume ablehnt.

## Offene Risiken

- Die produktive v3-Defaultverdrahtung folgt erst in Slice 18. Slice 17 muss deshalb die Watch-Grenze additiv und mit einer realen `WorkflowRunResult`-Projektion beweisen, ohne den v2-Default vorzeitig umzuschalten.
- Die Watch-Identität liegt absichtlich neben dem Inbox-Task und nicht im globalen v2-State. Ein Nutzer darf einen bereits gestarteten Task nicht still editieren; die Digestabweichung verlangt eine bewusste manuelle Auflösung, damit kein fremder Auftrag unter derselben Run-ID fortgesetzt wird.

## Review-Feedback von Claude

<!-- audit:claude-review:begin -->
Claude Sonnet mit Effort `high` prüfte im ersten fachlichen Lauf ausschließlich den vollständigen kanonischen Slice-17-Diff seit `e2bfcd4`, die Akzeptanzkriterien und die Attestierung `slice17-validation-0d8e04c6`. Repositoryerkundung und erneute Tests waren ausgeschlossen. Claude bestätigte Taskidentität, Exitcode-/Dispositionstrennung, Queue-Halt, strenge Finalreview-Done-Bedingung, Unterbrechungsresume und Isolation des v2-Defaults und erteilte `SLICE_APPROVAL: 17 | YES`.

Claude erfasste vier Observations: C-01 und C-02 benannten fehlende direkte Tests für Resume ohne vorhandenen State sowie fremden Run-/Task-State. Codex ergänzte genau diese zwei Grenztests und erneuerte die Attestierung. Im zweiten Lauf sah Claude gemäß Reviewvertrag ausschließlich diesen Korrekturdelta, schloss C-01 und C-02 ausdrücklich und bestätigte den finalen Fingerprint `f8f57b52b6ea2c7f118631dced49465bae105255a5461c25e8121e594c8c6530` mit `SLICE_APPROVAL: 17 | YES`. C-03 hält als nicht blockierende Slice-18-Beobachtung fest, dass der künftige v3-Callback keine bloß zwischenabgeschlossene Slice-Work-Unit als terminales Watch-Ergebnis zurückgeben darf. C-04 bestätigt den spezifizierten Queue-Halt bei einem nach Start veränderten Task.
<!-- audit:claude-review:end -->

## Review-Feedback von Antigravity

<!-- audit:antigravity-review:begin -->
Antigravity prüfte einmalig den vollständigen finalen Slice-17-Diff, beide Claude-Entscheidungen und dieselbe Attestierung `slice17-validation-f8f57b52`, ohne die Validierung erneut auszuführen. Es bestätigte die atomare, digestgebundene Taskidentität, die fail-closed Identitätskonflikte, die typisierte Abbildung der Exitcodes 2/3/4, den kontrollierten FIFO-Halt, die technische Retry-/Poison-Trennung, die strenge Finalreview-Done-Bedingung und die v2-State-Isolation.

A-01 ist eine nicht blockierende Bestätigung der defensiven terminalen Invariante: Jeder nicht abgeschlossene Ergebniszustand wird bereits vor dem Success-Marker durch Return oder Continue behandelt. Antigravity erteilte `SLICE_APPROVAL: 17 | YES` und autorisierte damit den lokalen Commit.
<!-- audit:antigravity-review:end -->

## Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
- C-01 akzeptiert und umgesetzt: Ein direkter Orchestrator-Test belegt sichere Neuinitialisierung mit derselben Watch-Run-ID bei `--resume` ohne vorhandenen State. Claude schloss C-01 auf dem Korrekturdelta.
- C-02 akzeptiert und umgesetzt: Ein direkter Orchestrator-Test belegt die fail-closed Ablehnung sowohl einer fremden Run-ID als auch eines fremden Taskpfads. Claude schloss C-02 auf dem Korrekturdelta.
- C-03 für Slice 18 vorgemerkt: Der produktive v3-Watch-Callback entsteht erst beim Default-Cutover und muss eine Work-Unit-Kette bis zu terminalem Erfolg oder resumefähigem Halt treiben. Eine vorzeitige Verdrahtung würde dem Slice-17-Nicht-Scope widersprechen.
- C-04 bestätigt: Ein inhaltlich veränderter bereits gestarteter Task hält absichtlich Task und FIFO-Queue, ohne Attempts oder Poison zu verbrauchen; stilles Überspringen würde die Reihenfolge und Run-Bindung brechen.
- A-01 akzeptiert: Die zusätzliche terminale Invariante bleibt unverändert als Defense-in-Depth bestehen; keine Codeänderung erforderlich.
<!-- audit:codex-responses:end -->

## Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### `slice17-validation-f8f57b52`

- Diff-Fingerprint: `f8f57b52b6ea2c7f118631dced49465bae105255a5461c25e8121e594c8c6530`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: 530 passed; 89 focused passed; compile PASS; active v2 dry-run PASS; diff check PASS; slice document PASS
- Ausgabedigest: `40c7d65527636ac982927d1c7a1246becf35c82aeed860866b9b03f89247aa2d`

| Matrixbefehl | Status | Exitcode |
|---|---|---:|
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider` | PASS | 0 |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_orchestrator_watch_cli.py tests/test_inbox_watcher.py tests/test_workflow.py -q -p no:cacheprovider` | PASS | 0 |
| `python3 -m py_compile src/inbox_watcher.py src/orchestrator.py src/workflow.py` | PASS | 0 |
| `./run_task --dry-run --skip-git-check --test-command '' --agent-output none` | PASS | 0 |
| `git diff --check` | PASS | 0 |
| Slice-17-Dokumentvalidator | PASS | 0 |
<!-- audit:validation-attestation:end -->

## Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
Teständerungen sind durch die arbeitsplanweite Revision-8-Ausnahme autorisiert.

Vorgesehene Testpfade: `tests/test_inbox_watcher.py`, `tests/test_orchestrator_watch_cli.py`, `tests/test_workflow.py`.

Pre-Mortem: Am wahrscheinlichsten würde ein pausierter v3-Task später als gewöhnlicher Exitcode-Fehler in den alten Retryzweig fallen und nach drei unveränderten Resumes als Poison verschoben. Die typisierte Ergebnisgrenze und Negativtests für alle resumefähigen Exitklassen müssen verhindern, dass ein bloßer Integercode diese Zustandsinformation verliert.
<!-- audit:test-approval-premortem:end -->

## Findings-Lebenszyklus

<!-- audit:findings:begin -->
| ID | Reporter | Klasse | Status | Kurzbegründung |
|---|---|---|---|---|
| C-01 | claude | OBSERVATION | CLOSED | Resume ohne State wird nun direkt mit stabiler Watch-Run-ID getestet |
| C-02 | claude | OBSERVATION | CLOSED | fremde Run-ID und fremder Taskpfad werden nun direkt fail-closed getestet |
| C-03 | claude | OBSERVATION | OPEN | Abnahmepunkt für den erst in Slice 18 entstehenden v3-Callback |
| C-04 | claude | OBSERVATION | OPEN | spezifizierter FIFO-Halt bei geändertem begonnenem Task bestätigt |
| A-01 | antigravity | OBSERVATION | OPEN | defensive terminale Invariante ist korrekt und benötigt keine Änderung |
<!-- audit:findings:end -->

## Entscheidungstabelle

<!-- audit:decision-table:begin -->
| Finding | Entscheidung | Umsetzung | Verifikation |
|---|---|---|---|
| C-01 | akzeptiert | Resume-ohne-State-Test ergänzt | Claude `CLOSED`; Vollsuite 530/530 |
| C-02 | akzeptiert | beide State-Identitätskonflikte getestet | Claude `CLOSED`; fokussierte Suite 89/89 |
| C-03 | auf Slice 18 terminiert | kein vorzeitiger Default-/Callback-Cutover | v2-Dry-Run PASS; Slice-18-Akzeptanzpunkt |
| C-04 | bestätigt | kontrollierten Queue-Halt beibehalten | bestehender Digestabweichungs-/No-Poison-Test |
| A-01 | akzeptiert | Defense-in-Depth unverändert beibehalten | Antigravity `SLICE_APPROVAL: 17 | YES` |
<!-- audit:decision-table:end -->

## Rückdokumentation in die Arbeitsplan-MD

Der Arbeitsplan wird vor Commit in §5.1 und aus der Slice-17-Überschrift auf diese Datei verlinkt. Tatsächlicher Scope, Validierungszahlen und Reviewstatus werden final zurückdokumentiert.

## Freigabestatus

<!-- audit:approval-status:begin -->
- Implementierung bereit: `YES`
- Validierung: `PASS`
- Claude-Freigabe: `YES`
- Antigravity-Freigabe: `YES`
- Offene Blocker: `NONE`
- Commit autorisiert: `YES`
<!-- audit:approval-status:end -->
