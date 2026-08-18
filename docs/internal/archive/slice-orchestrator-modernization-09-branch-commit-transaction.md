# Slice 09: Sichere Branch- und Committransaktion

**Status:** Korrekturstand vollständig validiert; Abschlussfreigaben und Commitstatus siehe verwaltete Auditabschnitte
**Feature-Branch:** `feature/orchestrator-modernization`
**Branch-Basis des Slice:** `1c383f0`
**GitHub-Status:** nur lokal; kein Upstream; Push und Merge sind nicht Bestandteil dieses Slice
**Arbeitsplan:** [orchestrator-modernization-work-plan.md](orchestrator-modernization-work-plan.md)

## Ziel des Slice

Eine additive Git-Service-Grenze prüft zulässige Feature-Branches, persistiert den exakten Slice-Start mit Scope und Fingerprint, unterscheidet saubere neue Läufe von scopegebundenem Resume und erstellt nach gebundenen Freigaben genau einen lokalen Slice-Commit. Der aktive v2-Defaultpfad bleibt unverändert; Slice 10 verdrahtet den Service später in die neue State-Maschine.

## Akzeptanzkriterien

- Branch-Namen entsprechen ausschließlich `feature/<kurzname>` oder `codex/<kurzname>`; eine Abweichung zwischen erwartetem und aktivem Branch blockiert vor einem Edit.
- Der Slice-Start persistiert Branch, HEAD, kanonischen Ausgangsfingerprint und eine normalisierte exakte Pfadallowlist.
- Ein neuer Lauf verlangt einen sauberen, nicht ignorierten Git-Status. Ein Resume erlaubt erwartete Änderungen nur innerhalb des persistierten Scopes und kann zusätzlich einen beim Halt persistierten Fingerprint exakt binden.
- Commitfreigabe erfordert positive, nicht gestoppte Claude- und Antigravity-Ergebnisse sowie dieselbe vollständige grüne Orchestrator-Attestierung für den aktuell neu berechneten Slice-Fingerprint.
- Scope-fremde Pfade, ein abweichender HEAD/Branch, ein nach Review geänderter Fingerprint, fremde Indexeinträge oder unvollständige/rote/fremde Freigaben blockieren vor dem Commit.
- Ausschließlich die exakten aktuellen Slice-Pfade werden gestaged; ein pauschales `git add -A` oder `git add .` ist ausgeschlossen.
- Das Ergebnis enthält Slice-ID, Commitmessage, geprüften Fingerprint, exakte Pfade und den resultierenden Commit-Hash zur späteren Auditprojektion und State-Persistenz.
- Die Komponente bietet weder Push noch Merge an.
- Vollsuite und aktiver v2-Dry-Run bleiben grün.

## Scope und Nicht-Scope

### Scope

- `src/git_service.py` (neu): Branchinspektion/-prüfung, Start-/Resume-Preflight, Review-/Attestierungsbindung, exaktes Staging und lokaler Commit.
- `src/workflow_state.py`: persistierbare Slice-Gitgrenze mit Scope und Ausgangsfingerprint sowie unveränderlicher Startbindung.
- `src/repo_changes.py`: unveränderte kanonische Diff- und Fingerprintquelle, die der Service wiederverwendet.
- `tests/test_git_service.py` (neu): temporäre Repositories für Branch, Status, Resume, Scope, Fingerprint, Freigaben, Staging und Commit.
- `tests/test_workflow_state.py`: Roundtrip und Unveränderlichkeit der persistierten Slice-Gitgrenze.
- Arbeitsplan, Übergabe und diese Slice-MD: Link, Status, Resultate und Prüfspur.

### Nicht-Scope

- Keine Verdrahtung der asymmetrischen Review-State-Maschine; dies folgt in Slice 10.
- Keine Teständerungs-, Stop-, Dateigrenzen- oder Anker-Gates aus Slice 11 und 12.
- Kein Push, Merge, Force-Push, History-Rewrite oder Remote-Branch-Management.
- Keine Änderung an Agentenadaptern, Prompts, Watcher, Root-Instruktionsdateien oder aktivem v2-State.
- Keine manuelle Bearbeitung von `.orchestrator/state.json` oder Checkpoints.

## Diff-Risiko inklusive Branch- und Statuscheck

**Branch-Check:** `feature/orchestrator-modernization`; entspricht dem Arbeitsplan.

**Status vor Slice-Beginn:** `git status --short` war leer. Die ignorierte Laufzeitdatei `Inbox/.lock` ist weiterhin vorhanden, gehört nicht zum Slice und wird weder gelesen, verändert, reviewed, gestaged noch committet.

**HEAD:** `1c383f0` (`Persist Claude review quota policy`).

**Voraussichtliche Änderungstiefe:** hoch an der Git-Seiteneffektgrenze, niedrig im aktiven Orchestrator. Hauptrisiken sind ein Commit mit scopefremden Dateien, eine Freigabe für einen veralteten Fingerprint, Indexverschmutzung nach Ablehnung und ein Resume auf falschem Branch oder HEAD.

**Gefährdete bestehende Funktionen:** kanonische Diffbildung, State-v3-Roundtrip, Work-Unit-Übergänge, Git-Index und lokaler Branch-HEAD.

**Nicht anfassen:** `Inbox/.lock`, `.orchestrator/state.json`, Checkpoints, Agentenadapter, Prompts, Watcher, Root-Instruktionsdateien und Remotes.

**Rollback-Strategie:** Änderungen werden dateiweise durch Gegenpatches zurückgeführt; neue Dateien werden nur nach ausdrücklicher Freigabe entfernt. Fehlgeschlagene Gates dürfen Git-Index und HEAD nicht verändern. Kein Hard Reset, pauschales Checkout oder History-Rewrite.

## Geplante Tests

- Zulässige/ungültige Branch-Namen, Branchabweichung, fehlender Branch und Remote-/lokaler Status.
- Sauberer neuer Lauf sowie Resume mit dirty In-Scope-, scopefremdem und seit Halt fingerprintfremdem Stand.
- Persistenter State-Roundtrip für Startcommit, Scope und Ausgangsfingerprint; nachträgliche Änderung der Startbindung wird abgelehnt.
- Blockade bei fehlender, negativer, gestoppter, roter, unvollständiger oder fingerprintfremder Claude-/Antigravity-Freigabe.
- Blockade unerwarteter Dateien, fremder Indexeinträge und nach Review geänderter Dateien ohne Mutation von HEAD oder Index.
- Exaktes Staging für neue, geänderte, gelöschte und umbenannte erlaubte Pfade sowie Commitmessage/-hash/-pfadzuordnung.
- Bereits committeter Vorgängerslice bleibt außerhalb des neuen Slice-Commits.
- Fokussierte Tests, Compile, `git diff --check`, vollständige Suite und aktiver v2-Dry-Run.

## Durchgeführte Änderungen

- `src/git_service.py` führt Repositoryidentität, Slice-Gitgrenze, Commitautorisierung und Commitresultat als unveränderliche Typen ein. Es akzeptiert nur aktive `feature/<name>`- oder `codex/<name>`-Branches und dokumentiert HEAD, Upstream sowie Ahead-/Behind-Stand ohne Netzwerkzugriff.
- `begin_slice` verlangt einen sauberen nicht ignorierten Stand und erzeugt den Startcommit, den leeren kanonischen Ausgangsfingerprint und eine normalisierte exakte Scope-Allowlist. Ignorierte Runtimepfade wie `Inbox/.lock` werden von der kanonischen Diffquelle nicht als Slice-Änderung behandelt.
- `resume_slice` bindet Branch und HEAD erneut, erlaubt ausschließlich Änderungen innerhalb des persistierten Scopes und kann zusätzlich den beim Halt gespeicherten Fingerprint exakt verlangen.
- `commit_slice` berechnet den Slice-Diff unmittelbar neu und verlangt Claude- sowie Antigravity-Freigabe, identische vollständige PASS-Attestierung und denselben aktuellen Fingerprint. Negatives/gestopptes Review, Rollenvertauschung, Fremdfingerprint, fremder Pfad oder fremder Indexeintrag blockieren vor dem Staging.
- Neue/geänderte Inhalte und Lösch-/Rename-Seiten werden getrennt mit exakten top-level-literalen Pathspecs gestaged. Es gibt keine pauschale Add-Operation. Der Commit läuft mit deaktivierten Repository-Hooks und deaktivierter GPG-Autokonfiguration; danach werden Commit-Hash und Pfadliste gegen den geprüften Slice verifiziert.
- Vor dem ersten Staging wird der vollständige Index als Tree-ID gesichert. Jeder `GitTransactionError` bis zum erfolgreichen Commit stellt diesen Tree exakt wieder her, sofern HEAD unverändert ist. Fremde vorab gestagte Pfade werden gegen den persistierten Scope geprüft, bevor die allgemeine Diff-Scopeprüfung greift. Diese Vorprüfung betrachtet rohe Indexpfade ohne Rename-Erkennung; dadurch unterscheidet die Quelllöschungslogik einen vollständig von einem nur zielseitig vorgestagten Rename, während die abschließende kanonische Pfadprüfung Renames weiterhin zusammenfasst.
- Alle Git-Service-Precondition-Verstöße einschließlich ungültigem Slice-Scope verwenden einheitlich `GitTransactionError`, damit die Slice-10-Verdrahtung keine rohen `ValueError`-Pfade behandeln muss.
- `src/workflow_state.py` persistiert Scope und Ausgangsfingerprint gemeinsam am Slice, normalisiert sie fail-closed und verhindert Änderung oder Sliceabschluss ohne diese Grenze. Frühere additive v3-Slice-Records ohne die beiden Felder werden explizit gelesen und beim nächsten Serialisieren um leere Felder ergänzt.
- `tests/test_git_service.py` verwendet ausschließlich temporäre Repositories und deckt Branch/Remote, ignorierte Lockdatei, Start/Resume, Pausenfingerprint, Scope, direkte Fremdindex-Erkennung, exakte Indexwiederherstellung nach einem simulierten Post-Staging-Fingerprintwechsel, Review-/Attestierungsbindung, exakte Add/Delete/Rename-Commits einschließlich nur zielseitig vorgestagtem Rename und Vorgängerslice-Abgrenzung ab. `tests/test_workflow_state.py` deckt Persistenz, Migration, Pfadnormalisierung, Idempotenz und Unveränderlichkeit ab.

## Ausgeführte Validierung mit Ergebnis

Die fokussierte Matrix des Korrekturstands umfasst 70 bestandene Git-Service-, State-v3- und State-I/O-Tests. Die vollständige Suite umfasst 334 bestandene Tests. `src/git_service.py` und `src/workflow_state.py` kompilieren, der aktive v2-Dry-Run ist grün und `git diff --check` ist sauber. Alle vier Matrixschritte liefen nach den Korrekturen gemeinsam erfolgreich; Fingerprint, Lauf-ID und Ausgabedigest stehen ausschließlich im verwalteten Attestierungsblock.

## Abweichungen vom Plan

`src/git_service.py` wurde als getrennte Seiteneffektgrenze angelegt, damit `src/repo_changes.py` rein lesend und kanonisch bleibt. `tests/test_repo_changes.py` blieb deshalb ebenfalls unverändert; seine bestehende Suite wird durch die Git-Service-Integrationstests wiederverwendet. Die additive v3-Kompatibilität erforderte eine explizite Lesemigration für Slice-Records ohne die neuen Gitgrenzenfelder. Weitere Abweichungen: keine.

## Offene Risiken

- Zwischen finaler Fingerprintprüfung und Git-Commit bleibt ohne Repository-Lock ein lokales TOCTOU-Fenster. Exaktes Index-Staging, erneute Fingerprintprüfung danach und deaktivierte Hooks verkleinern es; ein böswilliger paralleler lokaler Prozess bleibt außerhalb des vorgesehenen Einzelprozessmodells.
- Die Integration in die State-Maschine folgt erst in Slice 10; dieser Slice liefert die typisierte und isoliert getestete Grenze.

## Review-Feedback von Claude

<!-- audit:claude-review:begin -->
### Runde 1 — Fingerprint `cb9b03da…965b4`

- Reviewer: `claude` (Sonnet/High)
- Freigabe: `YES`; keine Blocker
- Scope: ausschließlich sieben geänderte Slice-09-Dateien und -Hunks
- Findings: `C-01`, `C-02`, `C-03` als Observations
- Formhinweis: Der fachliche Reviewdiff wurde einmal übertragen. Die anschließend formal normalisierte Antwort bewahrt Verdikt, Findings, Klassen und Begründungen unverändert.

### Runde 2 — Korrekturdelta, Fingerprint `6b6711c1…ed28`

- Reviewer: `claude` (Sonnet/High)
- Freigabe: `YES`; `C-01`, `C-02` und `C-03` geschlossen; keine neuen Findings
- Scope: ausschließlich Korrekturhunks und Regressionstests zu den drei bestehenden Findings; kein erneuter Gesamtdiff und keine Repositorysuche
- Verbrauch: rund `0,10 USD`; ein Paket-Chunk, eine fachliche Antwort, formal direkt gültig
- Nachbindung: Die anschließend neutralisierten drei Dokumentationsstatuszeilen erhalten eine eigene kompakte Fingerprintbestätigung ohne erneute Übertragung des Implementierungsdiffs.

### Runde 3 — finale Fingerprintnachbindung `f3887d51…59a5`

- Reviewer: `claude` (Sonnet/High)
- Freigabe: `YES`; `C-01`, `C-02` und `C-03` bleiben geschlossen; keine neuen Findings
- Scope: ausschließlich vier prosebezogene Status-/Digestzeilen seit Runde 2; kein Quellcode-, Test- oder Implementierungsdiff erneut übertragen
- Verbrauch: rund `0,065 USD`; ein Paket-Chunk, eine fachliche Antwort
- Formhinweis: Zwei angehängte XML-Schlussschilder wurden deterministisch hinter dem bereits ausgegebenen `STATUS: DONE` abgeschnitten; Verdikt und Inhalt blieben unverändert. Kein weiterer Modellaufruf.

### Runde 4 — A-01-Korrekturdelta, Fingerprint `d89ae225…dfbe`

- Reviewer: `claude` (Sonnet/High)
- Freigabe: `YES`; `C-01`, `C-02` und `C-03` bleiben geschlossen; keine neuen Findings
- Scope: ausschließlich zwei Git-Service-Korrekturhunks, der neue Rename-Regressionsfall, stabile Dokumentationszeilen und die neue Attestierung; kein erneuter Gesamtdiff
- Verbrauch: rund `0,144 USD`; ein Paket-Chunk, eine fachliche Antwort, formal direkt gültig
<!-- audit:claude-review:end -->

## Review-Feedback von Antigravity

<!-- audit:antigravity-review:begin -->
### Runde 1 — vollständiger finaler Slice, Fingerprint `f3887d51…59a5`

- Reviewer: `antigravity` (Gemini 3.1 Pro High/High)
- Freigabe: `NO`; Blocker `A-01`
- Scope: vollständiger finaler Slice-09-Diff aller sieben Pfade (`63.770` Zeichen), einmalig übertragen
- Finding: Bei einem nur zielseitig vorgestagten Rename prüfte die Quelllöschungsentscheidung den Zielpfad und konnte deshalb die Löschung des alten Pfads überspringen.

### Runde 2 — A-01-Korrekturdelta, Fingerprint `d89ae225…dfbe`

- Freigabe: `YES`; `A-01` geschlossen; keine neuen Findings
- Scope: ausschließlich rohe Indexpfadprüfung, korrigierte Update-Pfadauswahl, beide Rename-Tests und die neue Attestierung; kein erneuter vollständiger Slice-Diff
- Begründung: Die Update-Seite und ihre Skip-Bedingung verwenden nun denselben tatsächlichen Quellpfad gegen einen rename-unabhängigen Indexstand. Der partielle und der vollständig vorgestagte Rename sind getrennt grün belegt.
<!-- audit:antigravity-review:end -->

## Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
- `C-01`: **angenommen** — Vor dem Staging wird der Index per `git write-tree` gesichert; bei Fehler und unverändertem HEAD stellt `git read-tree` ihn exakt wieder her. Ein simulierter Post-Staging-Fingerprintwechsel beweist leeren Ausgangsindex, unveränderten HEAD und erhaltenen Arbeitsbaum.
- `C-02`: **angenommen** — Die Fremdindex-Prüfung läuft nun vor der allgemeinen Scopeprüfung direkt gegen den persistierten Scope; der Test verlangt ausdrücklich die Meldung `foreign staged`.
- `C-03`: **angenommen** — `SliceGitBoundary` und Scope-Normalisierung melden Precondition-Verstöße einheitlich als `GitTransactionError`; ein API-Test bindet die Ausnahme.
- `A-01`: **angenommen** — Die Vorprüfung verwendet rohe Indexpfade ohne Rename-Erkennung und entscheidet die Update-Seite gegen den tatsächlichen Quellpfad. Ein Regressionstest staged ausschließlich das Rename-Ziel und beweist, dass der Commit anschließend nur den neuen Pfad enthält, die Quelle gelöscht ist und der Arbeitsbaum sauber bleibt; der bereits vollständig vorgestagte Rename-Test bleibt ebenfalls grün.
<!-- audit:codex-responses:end -->

## Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### `slice09-validation-d89ae225`

- Diff-Fingerprint: `d89ae2259369bb29d1e90b0509cd4515a5ed5ddff7326cd6a07b46833b05dfbe`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: 334 passed; compile PASS; active v2 dry-run PASS; diff check PASS
- Ausgabedigest: `2fed9bb0a810f7b0d8804bda5e6d094372460117972bdb30b8511d24177072a1`

| Matrixbefehl | Status | Exitcode |
|---|---|---:|
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -q -p no:cacheprovider` | PASS | 0 |
| `python3 -m py_compile src/git_service.py src/workflow_state.py` | PASS | 0 |
| `./run_task --dry-run --skip-git-check --test-command '' --agent-output none` | PASS | 0 |
| `git diff --check` | PASS | 0 |
<!-- audit:validation-attestation:end -->

## Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
Teständerungen sind durch die arbeitsplanweite Revision-8-Ausnahme autorisiert. Wahrscheinlichstes späteres Versagen: Eine neue Git-Statusart wird nicht in die exakte Staging-/Scopeprüfung aufgenommen und erzeugt eine unvollständige oder zu breite Transaktion.

Freigegebene Testpfade: `tests/test_git_service.py`, `tests/test_workflow_state.py`.
<!-- audit:test-approval-premortem:end -->

## Findings-Lebenszyklus

<!-- audit:findings:begin -->
### `C-01` — `CLOSED` (OBSERVATION)

Post-Staging-Fehler stellten den vorherigen Index nicht aktiv wieder her. Claude bestätigt die vollständige Index-Tree-Wiederherstellung und den gezielten Regressionstest als ausreichende Schließung.

### `C-02` — `CLOSED` (OBSERVATION)

Der Fremdindex-Guard wurde vom Scope-Guard kurzgeschlossen und nicht direkt getestet. Claude bestätigt die neue Reihenfolge und den spezifisch auf `foreign staged` gebundenen Test als ausreichende Schließung.

### `C-03` — `CLOSED` (OBSERVATION)

Ungültige Git-Service-Scopewerte nutzten `ValueError` statt der Domänenfehlerklasse. Claude bestätigt die einheitliche `GitTransactionError`-Grenze und den API-Test als ausreichende Schließung.

### `A-01` — `CLOSED` (BLOCKER)

Ein nur zielseitig vorgestagter Rename konnte die Quelllöschung überspringen. Antigravity bestätigt die korrigierte Update-Pfadauswahl gegen rohe Indexpfade und die getrennten Regressionstests als ausreichende Schließung.
<!-- audit:findings:end -->

## Entscheidungstabelle

<!-- audit:decision-table:begin -->
| ID | Quelle | Finding | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|---|
| C-01 | claude | Indexwiederherstellung nach Post-Staging-Fehler | OBSERVATION | angenommen | umgesetzt; in Runde 2 geschlossen |
| C-02 | claude | Fremdindex-Guard direkt erreichbar und getestet | OBSERVATION | angenommen | umgesetzt; in Runde 2 geschlossen |
| C-03 | claude | Einheitliche Git-Service-Fehlerklasse | OBSERVATION | angenommen | umgesetzt; in Runde 2 geschlossen |
| A-01 | antigravity | Quelllöschung bei nur zielseitig vorgestagtem Rename | BLOCKER | angenommen | umgesetzt; in Runde 2 geschlossen |
<!-- audit:decision-table:end -->

## Rückdokumentation in die Arbeitsplan-MD

Der Arbeitsplan verlinkt diese Slice-MD aus §5.1 und der Slice-09-Überschrift. Implementierungs-, Validierungs- und Reviewstatus werden nach Abschluss ergänzt.

## Freigabestatus

<!-- audit:approval-status:begin -->
- Implementierung bereit: `YES`
- Validierung: `PASS` (`slice09-validation-d89ae225`)
- Claude-Freigabe: `YES` (`d89ae225…dfbe`)
- Antigravity-Freigabe: `YES` (`d89ae225…dfbe`)
- Commit autorisiert: `YES`
<!-- audit:approval-status:end -->
