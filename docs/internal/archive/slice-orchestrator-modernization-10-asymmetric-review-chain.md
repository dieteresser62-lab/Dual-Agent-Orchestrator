# Slice 10: Asymmetrische Plan- und Slice-Reviewkette

**Status:** Implementierung und fokussierte Matrix grün; Abschlussfreigaben und Commitstatus siehe verwaltete Auditabschnitte
**Feature-Branch:** `feature/orchestrator-modernization`
**Branch-Basis des Slice:** `b356cb3`
**GitHub-Status:** nur lokal; kein Upstream; Push und Merge sind nicht Bestandteil dieses Slice
**Arbeitsplan:** [orchestrator-modernization-work-plan.md](orchestrator-modernization-work-plan.md)

## Ziel des Slice

Eine additive, injizierbare State-v3-Engine führt Plan- und Slice-Work-Units in der festen Rollenkette Codex→Claude↔Codex→Antigravity aus. Sie bindet Reviewer an den aktuellen Diff-Fingerprint und dieselbe Orchestrator-Attestierung, bewahrt Findings als typisierte Records und bleibt bis zum späteren Cutover außerhalb des aktiven v2-Defaultpfads.

## Akzeptanzkriterien

- Codex plant, implementiert oder korrigiert, erzeugt aber niemals ein Reviewverdikt über seine eigene Arbeit.
- Claude wird nach jedem inhaltlichen Codex-Stand aufgerufen; Runde 1 erhält den vollständigen Slice-Diff, Folgerunden ausschließlich das Delta seit dem letzten Claude-Fingerprint.
- Eine reine Contractreparatur erhält nur abgelehnte Antwort, Validierungsfehler und Contract, nicht erneut den Implementierungsdiff.
- Antigravity wird ausschließlich nach einer positiven Claude-Freigabe aufgerufen und erhält je Anlauf den vollständigen Slice-Diff seit dem persistierten Startcommit.
- Eine Antigravity-Ablehnung führt zu Codex-Korrektur, erneuter Claude-Prüfung und erst danach zu einem neuen Antigravity-Anlauf.
- Fehlende oder nach kompakter Reparatur weiterhin unparsbare Verdikte halten am Review-Schritt; sie überspringen keine Rolle.
- Validierung läuft einmal je Fingerprint. Claude und Antigravity konsumieren dieselbe vollständige PASS-Attestierung und dürfen kein eigenes `VALIDATION_RESULT` ausgeben.
- Reviewer können nur eigene Findings aktualisieren oder schließen. Ein fremder offener Blocker darf zur erneuten Prüfung durch seinen Eigentümer transportiert werden, blockiert aber den finalen Commit bis zur Schließung.
- Destillierte Plan-/Slice-Sicht und die vollständige typisierte Finding-Historie werden getrennt übergeben; alte Records werden nicht durch Zeichentrunkierung verloren.
- Resume am persistierten Schritt wiederholt keinen bereits abgeschlossenen Codex-Aufruf.
- Die Engine bleibt additiv; `run_pipeline` und der aktive v2-Default bleiben unverändert und grün.

## Scope und Nicht-Scope

### Scope

- `src/workflow.py` (neu): Workflow-Engine, Treibergrenze, Reviewpaketarten, Attestierungscache, Transitionen, Contractreparatur und Commitanforderung.
- `src/orchestrator.py`: schmaler additiver v3-Einstieg ohne Aufruf aus dem aktiven Defaultpfad.
- `src/prompts.py`: Codex-v3-Prompt aus destilliertem Kontext und vollständigen Finding-Records.
- `src/contracts.py`, `src/audit_trail.py`, `src/git_service.py`: reviewer-eigene Blockersemantik für asymmetrische Rückgaben bei unverändert global blockierendem Commitstand.
- `tests/test_workflow.py` (neu), `tests/test_contracts.py`, `tests/test_prompts.py`: deterministische Rollen-, Paket-, Contract-, Attestierungs- und Resume-Nachweise.
- Arbeitsplan, Übergabe und diese Slice-MD.

### Nicht-Scope

- Keine Aktivierung der Engine im normalen CLI-, Watch- oder Dry-Run-Pfad; diese Verdrahtung folgt in späteren Slices.
- Keine Nutzer-/Teständerungs-, Stop-, Dateigrenzen-, Validierungsmatrix-, Quota- oder Watch-Gates aus Slice 11 bis 17.
- Kein branchweites Endreview aus Slice 16 und kein Default-Cutover aus Slice 18.
- Keine Änderung des State-v3-Schemas, weil die bestehenden Work-Unit-Schritte und Gates ausreichen.
- Kein Push, Merge oder Remotezugriff.

## Diff-Risiko inklusive Branch- und Statuscheck

**Branch-Check:** `feature/orchestrator-modernization`; entspricht dem Arbeitsplan.

**Status vor Slice-Beginn:** sauber nach lokalem Slice-09-Commit `b356cb3`. Die ignorierte Datei `Inbox/.lock` war vorhanden und bleibt außerhalb von Diff, Scope, Review und Commit.

**Voraussichtliche Änderungstiefe:** hoch in der neuen Ablaufsteuerung, niedrig im aktiven Produktpfad. Hauptrisiken sind vertauschte Rollen, ein Antigravity-Aufruf vor Claude, falsche Delta-/Gesamtdiffpakete, stale Attestierungen, verlorene Findings und doppelte Seiteneffekte beim Resume.

**Gefährdete bestehende Funktionen:** zentrale Contractvalidierung, Auditprojektion und Slice-09-Commitautorisierung. Der aktive v2-Lauf wird nur durch Regressionstests berührt.

**Nicht anfassen:** `.orchestrator/state.json`, Checkpoints, `Inbox/.lock`, CLI-Cutover, Watcher, Quota- und Gateimplementierungen sowie Remotes.

**Rollback-Strategie:** neue Engine und Tests dateiweise zurücknehmen; additive Einstiegsmethode und die drei reviewer-eigenen Blockerstellen per Gegenpatch entfernen. Kein Reset, Checkout oder History-Rewrite.

## Geplante Tests

- Plan: Codex→Claude, niemals Antigravity oder Commit.
- Slice-Sofortfreigabe: Codex→Claude→Antigravity→Commit mit identischer Attestierung.
- Zwei Codex-Nachbesserungen: exakt drei Claude- und ein Antigravity-Aufruf.
- Antigravity-Blocker: Codex→Claude vor erneutem Antigravity; nur Antigravity schließt sein Finding.
- Claude-Erstpaket vollständig, Folgepakete nur Delta; Antigravity-Paket enthält frühen und letzten Slice-Pfad vollständig.
- Contractreparatur ohne Implementierungsevidenz und ohne zweiten fachlichen Reviewaufruf.
- Fehlendes Verdikt, rote/unvollständige/fremde Attestierung und scopefremde Pfade halten fail-closed.
- Resume am Claude-Schritt wiederholt Codex nicht.
- Vollständige Suite, Compile, aktiver v2-Dry-Run und Diffcheck.

## Durchgeführte Änderungen

- `src/workflow.py` definiert eine treiberinjizierte Engine für Plan-, Slice- und Korrektur-Work-Units. Geschlossene Transitionen erzwingen Codex→Claude, Claude-Ablehnung→Codex, Claude-Freigabe→Antigravity und Antigravity-Ablehnung→Codex→Claude. Erst die aktuelle Antigravity-Freigabe führt zur mechanischen Commitanforderung.
- `WorkflowChanges` bindet Startcommit, vollständigen Slice-Diff, exakte Pfade und SHA-256-Fingerprint. Claude erhält beim ersten Aufruf den vollständigen Diff und danach ausschließlich das vom Treiber zwischen altem und neuem Fingerprint erzeugte Korrekturdelta. Antigravity erhält bei jedem zulässigen Anlauf den vollständigen aktuellen Slice-Diff; ein Fingerprintwechsel nach Claude blockiert vor Validierung und Antigravity-Aufruf.
- Die Engine cached vollständige PASS-Attestierungen pro Fingerprint und projiziert Validierungs- und Reviewresultate als typisierte Auditereignisse. Fremde, rote oder unvollständige Attestierungen halten vor dem Reviewer. State und `WorkflowHistory` werden an jeder erfolgreichen Transition gemeinsam an die Checkpointgrenze übergeben.
- Reviewerantworten laufen durch den zentralen v3-Validator. Genau ein kompakter Format-Reparaturversuch erhält nur abgelehnte Antwort, Validierungsfehler und gerenderten Contract. Bleibt das Verdikt ungültig, bleibt der persistierte Review-Schritt erhalten und Antigravity wird nicht vorgezogen.
- `build_v3_codex_prompt` trennt Aufgabe, destillierte Plan-/Slice-Sicht und vollständige Finding-Records. Reviewerpakete enthalten ebenfalls Herkunft, Klasse, Status, Akzeptanztest, Codex-Antworten und Schließbegründung ohne blindes Abschneiden der Historie.
- `ContractResult.own_open_blockers` trennt das Freigaberecht eines Reviewers vom globalen Findingstand: Claude darf eine Antigravity-Korrektur freigeben, ohne A-Findings selbst zu schließen; Antigravity schließt sie anschließend. Audit prüft die Eigentümersemantik je Review. Engine und Git-Service erhalten zusätzlich den kanonischen finalen Findingstand und lehnen vor dem Commit weiterhin jeden global offenen Blocker ab.
- `src/orchestrator.py` exportiert ausschließlich einen additiven `run_v3_work_unit`-Einstieg. Der aktive `run_pipeline`-Pfad wird nicht umgeschaltet.
- `tests/test_workflow.py` verwendet einen vollständig deterministischen Fake-Treiber und prüft Aufrufreihenfolge, Paketart/-inhalt, Attestierungscache, Findings, Format-Reparatur, fehlendes Verdikt, Scope, Fingerprintdrift, Antigravity-Rückgabe, Checkpointhistorie und Resume. Contract- und Prompttests binden die reviewer-eigene Blockersemantik und vollständige Recorddarstellung; ein isolierter Git-Service-Test beweist die globale Blockade eines fremd gemeldeten offenen Blockers im kanonischen Commitfindingstand.

## Ausgeführte Validierung mit Ergebnis

202 fokussierte Workflow-, State-, I/O-, Contract-, Prompt-, Audit-, Git-Service- und Parser-Tests sind bestanden. Die vollständige Suite, Compile, aktiver v2-Dry-Run und Diffcheck werden gemeinsam fingerprintgebunden ausgeführt; ihre wechselnden Resultate stehen ausschließlich im verwalteten Attestierungsblock.

## Abweichungen vom Plan

`src/workflow_state.py` blieb unverändert, weil Slice 07 bereits alle benötigten Schritte, Rückgabezähler, Gates und Resume-Cursor bereitstellt. `src/git_service.py` wurde zusätzlich angepasst, damit Commitautorisierung in der asymmetrischen Kette nur reviewer-eigene offene Blocker je Reviewresultat bewertet; der kanonische Workflow-Findingstand bleibt global commitblockierend. Weitere Abweichungen: keine.

## Offene Risiken

- Die Engine besitzt in Slice 10 bewusst nur eine injizierbare Treibergrenze; reale CLI-, State-I/O-, Git- und Auditadapter werden in den vorgesehenen Folgeslices zusammengesetzt.
- Persistenz unmittelbar zwischen externem Commit und anschließendem State-Checkpoint bleibt Aufgabe der späteren Resume-/Fehlerverdrahtung in Slice 14 und 17.

## Review-Feedback von Claude

<!-- audit:claude-review:begin -->
### Runde 1 — Fingerprint `6977b436…7c60`

- Reviewer: `claude` (Sonnet/High)
- Freigabe: `YES`; Observation `C-01`
- Scope: ausschließlich vollständiger Slice-10-Diff seit `b356cb3`, keine unveränderten Repositorydateien
- Formhinweis: Ein kompakter Reparaturaufruf erhielt nur abgelehnte Antwort, Contractfehler und Contract. Anschließend wurde der vorhandene Reviewer-Marker deterministisch an den Anfang verschoben; kein weiterer fachlicher Review und keine erneute Diffevidenz.

### Runde 2 — C-01-Korrekturdelta, Fingerprint `5d53cd76…0bc1`

- Freigabe: `YES`; `C-01` geschlossen; keine neuen Findings
- Scope: ausschließlich kanonischer Findingstand in `CommitAuthorization`, globaler Git-Service-Blockercheck, isolierter Regressionstest und neue Attestierung
- Verbrauch: rund `0,089 USD`; ein Paket-Chunk, formal direkt gültig

### Runde 3 — A-01-Widerspruch, unveränderter Fingerprint

- Freigabe: `YES`; `C-01` bleibt geschlossen, Antigravity-eigenes `A-01` wird unverändert offen weitergetragen
- Scope: ausschließlich Finding-Record, Codex-Zurückweisung, bestehende zentrale Eigentümerprüfung und bestehender Negativtest; kein Slice-Diff
- Verbrauch: rund `0,134 USD`; ein Paket-Chunk, formal direkt gültig

### Runde 4 — finaler Dokumentationsdelta, Fingerprint `db3454cd…e3e8`

- Freigabe: `YES`; `C-01` und das von Antigravity bereits geschlossene `A-01` bleiben geschlossen
- Scope: ausschließlich drei Zählwertkorrekturen `201`→`202`, der ergänzte Testpfad `tests/test_git_service.py` und die neue Attestierung; kein erneuter vollständiger Slice-Diff
- Formhinweis: Der Wrapper prüfte den vorhandenen v3-Slice-Marker zunächst fälschlich mit dem v2-Flagparser und löste dadurch einen redundanten kompakten Aufruf aus. Anschließend wurden ausschließlich der präfixierte Testmarker normalisiert und der unzulässige redundante Statusmarker für das fremde Finding `A-01` entfernt; Verdikt, Evidenz und Begründung blieben unverändert.
<!-- audit:claude-review:end -->

## Review-Feedback von Antigravity

<!-- audit:antigravity-review:begin -->
### Runde 1 — vollständiger Slice, Fingerprint `5d53cd76…0bc1`

- Reviewer: `antigravity` (Gemini 3.1 Pro High/High)
- Freigabe: `NO`; Blocker `A-01`
- Scope: vollständiger finaler Slice-10-Diff aller 13 Pfade (`79.680` Zeichen)
- Finding: Antigravity nahm an, ein Reviewer könne ein fremdes Finding schließen. Der bestehende zentrale Pfad `apply_reviewer_finding_update` und der bestehende Negativtest widersprechen dieser Annahme; Codex weist das Finding deshalb mit konkreter Evidenz zurück.
- Formhinweis: Der erste Output nannte falsche Testpfade. Die kompakte Reparatur erhielt nur Antwort, Contractfehler und Contract; Finding und Verdikt blieben unverändert.

### Runde 2 — vollständige A-01-Nachprüfung, Fingerprint `5d53cd76…0bc1`

- Freigabe: `YES`; `A-01` geschlossen; `C-01` bleibt geschlossen
- Scope: erneut vollständiger Slice-10-Diff aller 13 Pfade sowie hervorgehobene Codex-Evidenz zur zentralen Eigentümerprüfung
- Formhinweis: Die fachliche Antwort schloss `A-01`, ließ aber den Freigabemarker aus. Ein kompakter Reparaturaufruf erhielt nur Antwort, Contractfehler und Contract und ergänzte den unveränderten positiven Entscheid.

### Runde 3 — vollständiger finaler Slice, Fingerprint `db3454cd…e3e8`

- Freigabe: `YES`; keine neuen Findings; `A-01` und `C-01` bleiben geschlossen
- Scope: vollständiger finaler Slice-10-Diff aller 13 Pfade (`81.773` Zeichen fachlicher Diff vor der abschließenden Auditprojektion), neue Attestierung und Claudes positive Fingerprintbindung
- Ergebnis: Architektur, Transitionen, Finding-Eigentümerschaft, Resume/Idempotenz, globale Commitblockade, Tests und Dokumentationskonsistenz geprüft; formal im ersten Versuch gültig
<!-- audit:antigravity-review:end -->

## Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
- `C-01`: **angenommen mit präzisierter Umsetzung** — Reviewer-eigene Blocker bleiben für die asymmetrische Zwischenfreigabe erforderlich. Zusätzlich trägt `CommitAuthorization` jetzt den kanonischen finalen Findingstand, den der Git-Service unabhängig von der Engine auf jeden global offenen Blocker prüft. Der geforderte isolierte Test beweist Ablehnung ohne Index- oder HEAD-Mutation.
- `A-01`: **zurückgewiesen** — `_merge_review_findings` delegiert jede `FINDING_STATUS`- und `FINDING_RECLASSIFIED`-Änderung an `apply_reviewer_finding_update`; diese Funktion wirft bei `reviewer is not finding.origin.reporter` bereits `ValueError("only the reporting reviewer may update or close a finding")`, das als `ContractValidationError` propagiert. `test_other_reviewer_cannot_close_reported_finding` konstruiert exakt ein Claude-Update auf ein Antigravity-Finding und erwartet diese Ablehnung. Kein Code- oder Testdelta erforderlich.
<!-- audit:codex-responses:end -->

## Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### `slice10-validation-db3454cd`

- Diff-Fingerprint: `db3454cd586c693125f00264b987620c7d248f122eff0ec1acd175702b92e3e8`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: 348 passed; compile PASS; active v2 dry-run PASS; diff check PASS
- Ausgabedigest: `d195203c86265e347ab6c165aa6432cade0f5634a7955228082f02619030cbb0`

| Matrixbefehl | Status | Exitcode |
|---|---|---:|
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -q -p no:cacheprovider` | PASS | 0 |
| `python3 -m py_compile src/workflow.py src/orchestrator.py src/prompts.py src/contracts.py src/audit_trail.py src/git_service.py` | PASS | 0 |
| `./run_task --dry-run --skip-git-check --test-command '' --agent-output none` | PASS | 0 |
| `git diff --check` | PASS | 0 |
<!-- audit:validation-attestation:end -->

## Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
Teständerungen sind durch die arbeitsplanweite Revision-8-Ausnahme autorisiert.

Freigegebene Testpfade: `tests/test_workflow.py`, `tests/test_contracts.py`, `tests/test_git_service.py`, `tests/test_prompts.py`.
<!-- audit:test-approval-premortem:end -->

## Findings-Lebenszyklus

<!-- audit:findings:begin -->
### `C-01` — `CLOSED` (OBSERVATION)

Die Git-Service-Autorisierung besaß nach Einführung reviewer-eigener Blockersemantik zunächst keinen isolierten Nachweis für einen fremd gemeldeten global offenen Blocker. Claude bestätigt den neuen kanonischen Findingstand, die eigenständige globale Git-Grenze und den side-effect-freien Regressionstest als ausreichende Schließung.

### `A-01` — `CLOSED` (BLOCKER)

Antigravity akzeptiert Codex' Zurückweisung: Die zentrale Prüfung in `apply_reviewer_finding_update` erzwingt bereits die Eigentümerschaft des meldenden Reviewers, und der vorhandene Negativtest belegt den symmetrischen Pfad. Kein Code- oder Testdelta war erforderlich.
<!-- audit:findings:end -->

## Entscheidungstabelle

<!-- audit:decision-table:begin -->
| ID | Quelle | Finding | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|---|
| C-01 | claude | Globale Fremdblocker-Grenze im Git-Service isoliert absichern | OBSERVATION | angenommen/präzisiert | kanonischer finaler Findingstand, globale Blockade und Regressionstest; Runde 2 geschlossen |
| A-01 | antigravity | Eigentümerprüfung bei Findingupdates | BLOCKER | zurückgewiesen/geschlossen | Prüfung und exakter Negativtest bestehen bereits; Antigravity bestätigt die Evidenz in Runde 2 |
<!-- audit:decision-table:end -->

## Rückdokumentation in die Arbeitsplan-MD

Der Arbeitsplan verlinkt diese Slice-MD aus §5.1 und der Slice-10-Überschrift. Tatsächlicher Scope und Implementierungsstatus sind ergänzt; finale Validierung und Reviews folgen nach Abschluss.

## Freigabestatus

<!-- audit:approval-status:begin -->
- Implementierung bereit: `YES`
- Validierung: `PASS` (`slice10-validation-db3454cd`)
- Claude-Freigabe: `YES` (`db3454cd…e3e8`)
- Antigravity-Freigabe: `YES` (`db3454cd…e3e8`)
- Commit autorisiert: `YES`
<!-- audit:approval-status:end -->
