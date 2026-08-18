# Slice 08: Deterministische Plan- und Slice-Prüfspur

**Status:** implementiert; formale Abschlussdaten werden ausschließlich in der verwalteten Prüfspur geführt
**Feature-Branch:** `feature/orchestrator-modernization`
**Branch-Basis des Slice:** `fadebe3`
**GitHub-Status:** nur lokal; kein Upstream; Push und Merge sind nicht Bestandteil dieses Slice
**Arbeitsplan:** [orchestrator-modernization-work-plan.md](orchestrator-modernization-work-plan.md)

## Ziel des Slice

Eine additive Audit-Komponente validiert vor einem Slice dessen 1-basierten Zielpfad, den aktiven relativen Arbeitsplan-Link und die Pflichtstruktur der Slice-MD. Anschließend projiziert sie ausschließlich bereits vertragsvalidierte strukturierte Ereignisse deterministisch in geschützte Abschnitte, ohne freie Reviewertexte als zweite Datenquelle oder Schreibanweisung zu behandeln.

Der aktive v2-Defaultpfad bleibt bis Slice 18 unverändert. Slice 08 stellt die v3-Projektionsgrenze bereit, aktiviert aber noch keine vollständige Review-State-Maschine.

## Akzeptanzkriterien

- Slice-IDs sind 1-basiert; Zielpfad, tatsächliche Datei und aktiver relativer Markdown-Link aus §5.1 des Arbeitsplans müssen exakt zusammenpassen.
- Die Slice-Datei enthält alle Pflichtabschnitte aus §9.2 der Anforderungen, bevor eine Projektion zulässig ist.
- Nur typisierte, bereits vertragsvalidierte Records werden gerendert; rohe Agentenantworten und Logs sind kein Eingabeformat.
- Validierungsattestierung, Reviewevidenz, Pre-Mortem, Findings, Codex-Antworten, Entscheidungstabelle und Freigabestatus besitzen getrennte verwaltete Abschnitte.
- Freitext wird Markdown-sicher als Datenwert gerendert und kann weder Abschnittsmarker noch Tabellenstruktur oder andere Dateien beeinflussen.
- Eine Attestierung enthält ID, Diff-Fingerprint, erwartete Befehle, Exitcodes, Vollständigkeit, Status, Kurzresultat und Ausgabedigest. Reviewertext kann diese Felder nicht überschreiben.
- Unverwaltete Inhalte bleiben bytegetreu erhalten; Updates erfolgen atomisch und wiederholtes Rendern desselben Zustands ist idempotent.
- Verwaltete Auditabschnitte können vor der fachlichen Fingerprintbildung deterministisch entfernt werden, damit die Projektion sich nicht selbst invalidiert.
- Der aktive v2-Dry-Run und die vollständige Testsuite bleiben grün.

## Scope und Nicht-Scope

### Scope

- `src/audit_trail.py` (neu): Ziel-/Strukturvalidierung, sichere deterministische Projektion, Managed-Section- und Fingerprint-Helfer.
- `src/repo_changes.py`: kanonischer Fingerprint verwendet für interne Markdown-Prüfspuren semantische Größe und semantischen Digest; der vollständige sichtbare Reviewdiff bleibt unverändert.
- `src/contracts.py`: nur falls eine zusätzliche typisierte Auditgrenze erforderlich ist.
- `src/workflow_state.py`: nur falls persistierbare Ereignisidentität für Resume/Idempotenz erforderlich ist.
- `tests/test_audit_trail.py` (neu): Struktur-, Golden-, Idempotenz-, Injektions-, Pfad- und Finding-Lifecycle-Tests.
- `tests/test_repo_changes.py`: Integration der Managed-Section-Kanonisierung in die bestehende kanonische Diffquelle.
- `docs/internal/orchestrator-modernization-work-plan.md` und diese Slice-MD: Link, Status und Prüfspur.

### Nicht-Scope

- Keine Aktivierung der asymmetrischen Reviewkette; dies folgt in Slice 10.
- Keine Git-Committransaktion; dies folgt in Slice 09.
- Kein Default-Cutover auf State v3; dies folgt in Slice 18.
- Keine Änderung an Root-Instruktionsdateien, Agentenadaptern, Prompts, Watcher oder bestehendem v2-State.
- Keine manuelle Bearbeitung von `.orchestrator/state.json` oder Checkpoints.
- Kein Push oder Merge.

## Diff-Risiko inklusive Branch- und Statuscheck

**Branch-Check:** `feature/orchestrator-modernization`; entspricht dem Arbeitsplan.

**Status vor Slice-Beginn:** Der Arbeitsbaum enthielt ausschließlich die unversionierten Editor-Lockdateien `docs/internal/.~lock.orchestrator-modernization-work-plan.md#` und `docs/internal/.~lock.slice-orchestrator-modernization-03-three-agent-adapters.md#`. Sie gehören nicht zu Slice 08 und werden weder gelesen noch verändert, reviewed, gestaged oder committet.

**HEAD:** `fadebe3`.

**Merge-Base mit `master`:** `0bd3baddbac307b90e85db4c9bdcb5a787558652`.

**Voraussichtliche Änderungstiefe:** hoch an der Dokumentgrenze, niedrig im aktiven Orchestrator. Hauptrisiken sind Dokumentverlust, Marker-/Markdown-Injektion, ein falscher Slice-Zielpfad und ein Auditupdate, das seinen eigenen Reviewfingerprint verändert.

**Gefährdete bestehende Funktionen:** atomare Writes, Pfadwurzelbindung, strikte Contract-Datentypen und der additive v3-/aktive v2-Kompatibilitätsrand.

**Nicht anfassen:** die beiden Editor-Lockdateien, `.orchestrator/state.json`, Checkpoints, Agentenadapter, Prompts, Watcher, Root-Instruktionsdateien und nicht verwaltete Dokumentabschnitte.

**Rollback-Strategie:** Änderungen werden dateiweise durch Gegenpatches zurückgeführt; die neue Datei wird nur nach ausdrücklicher Freigabe entfernt. Kein Hard Reset und kein pauschales Checkout.

## Geplante Tests

- Zielpfad, 1-basierte Slice-ID, aktiver relativer Link und vollständige Pflichtüberschriften.
- Ablehnung fremder/absoluter/traversierender Zielpfade und inaktiver oder falscher Links.
- Golden-Test für Validierungsattestierung, Reviewergebnisse und Entscheidungstabelle.
- Idempotentes Wiederholen und bytegetreues Erhalten unverwalteter Abschnitte.
- Abschnitts-/Markdown-/Tabelleninjektion sowie Sonderzeichen und mehrzeilige Werte.
- Vollständiger Finding-Lebenszyklus mit Antworten und Statuswechsel.
- Semantischer Dokumentinhalt ohne verwaltete Auditabschnitte.
- Fokussierte Tests, `git diff --check`, vollständige Suite und aktiver v2-Dry-Run.

## Durchgeführte Änderungen

- `src/audit_trail.py` führt typisierte, 1-basierte Validierungs- und Reviewereignisse zu einer deterministischen Projektion zusammen. Ereignisreihenfolge, Slice-Bindung, Approval-/Blocker-Konsistenz, Finding-Identität, Attestierungsbindung und Commitautorisierung werden fail-closed geprüft.
- Slice-Zielpfad, Dateiname/ID, §5.1-Tabellenlink, Link der Slice-Überschrift, Pflichtstruktur, Metadaten, Managed Marker und deren exakter Abschnitt werden vor jedem Write erneut validiert. Symlink-, Traversal-, absolute und fremde Pfade werden abgelehnt.
- Ein gesonderter Arbeitsplanpfad validiert die Pflichtstruktur aus §9.1 und verwendet denselben Projektionskern. Der Aufrufer ordnet einen Ereignissatz genau dem Plan oder der Slice-MD zu; rohe Logs und Agentenantworten sind keine API-Eingabe.
- Reviewerwerte werden HTML-/Markdown-sicher gerendert. Acht getrennte verwaltete Blöcke enthalten Reviews, Codex-Antworten, Validierungsattestierungen, Testfreigabe/Pre-Mortem, Finding-Lifecycle, Entscheidungstabelle und Freigabestatus.
- Markerähnlicher Beispieltext innerhalb von Markdown-Codefences bleibt normaler semantischer Dokumentinhalt und wird weder als Managed Block interpretiert noch als fehlerhafter Auditmarker abgelehnt. Die gemeinsame Fence-State-Maschine unterscheidet Backtick/Tilde und Öffnungslänge; nur eine Fence desselben Zeichens mit mindestens derselben Länge schließt den Block. Zusätzlich gelten ausschließlich alleinstehende Top-Level-Kommentarzeilen als Managed Marker. Kommentare in Blockquotes, Listen-/Codeeinrückungen oder Fließtext bleiben unabhängig von der Container-Fence-Erkennung semantischer Inhalt.
- Positive Slice-Reviews benötigen an der `project_slice_audit`-Grenze zwingend eine gebundene Validierungsattestierung. Der generische Arbeitsplanprojektor darf dagegen weiterhin attestierungslose Planereignisse darstellen; die Commitautorisierung bleibt zusätzlich an Antigravity plus PASS-Attestierung gebunden.
- Alle Blöcke werden aus einem unveränderlichen Projektionsstand in genau einem atomischen Dateiersatz aktualisiert. Ein identisches Wiederholen erzeugt weder neue Bytes noch einen zweiten Write; unverwaltete Inhalte bleiben erhalten.
- `strip_managed_audit_sections` und `semantic_audit_fingerprint` kanonisieren ausschließlich bekannte, korrekt gepaarte Managed Blöcke. Änderungen an fachlichem oder sonstigem unverwaltetem Inhalt verändern den Fingerprint weiterhin.
- Die Markerextraktion normalisiert ausschließlich die Zeilenendung für die Erkennung und behält die Originaloffsets. Dadurch funktionieren LF- und CRLF-Dokumente identisch, ohne die erhaltenen Bytes außerhalb verwalteter Abschnitte umzuschreiben.
- `src/repo_changes.py` verwendet diese Kanonisierung ausschließlich für den Modernisierungsarbeitsplan und Markdown-Dateien im verbindlichen Slice-Dateinamensschema. Projektionstext bleibt vollständig im Reviewdiff sichtbar, verändert aber weder semantische Payloadgröße noch -digest. Andere interne Markdown-Dateien können Markerpaare nicht nutzen, um Änderungen vor dem Fingerprint zu verbergen; fehlerhafte Marker in registrierten Auditdokumenten brechen die Fingerprintbildung fail-closed ab.
- `tests/test_audit_trail.py` deckt Slice- und Arbeitsplanziele, Pfad-/Link-/Strukturfehler, Markerbesitz, Idempotenz, Sonderzeichen/Injektion, Attestierungsbindung, Finding-Lifecycle, Testfreigabe und semantischen Fingerprint ab.

## Ausgeführte Validierung mit Ergebnis

Die fokussierte Entwicklungsvalidierung ist nach der letzten Korrektur mit 44 bestandenen Audit-/Repository-Diff-Tests grün. Die maßgebliche vollständige Matrix und ihre Fingerprintbindung werden ausschließlich als strukturierte Attestierung im verwalteten Abschnitt `Validierungsattestierung` geführt; dadurch verändern spätere Projektionen desselben geprüften Zustands den fachlichen Fingerprint nicht.

## Abweichungen vom Plan

- `src/contracts.py` bleibt unverändert: `ContractResult`, `ValidationAttestation` und die Finding-Records bilden bereits die benötigte validierte Eingabegrenze. Eine zweite Auditvariante derselben Typen hätte widersprüchliche Wahrheiten ermöglicht.
- `src/workflow_state.py` bleibt unverändert: Die in Slice 07 eingeführten persistierten `completed_side_effects` bilden die Projektions-Idempotenz für Resume bereits ab. Der Auditprojektor selbst vermeidet zusätzlich einen Dateischreibvorgang bei identischem Ergebnis.
- Zusätzlich zur Slice-MD-Projektion gibt es eine getrennte Arbeitsplan-Strukturprüfung und -Projektion, damit R-17 nicht erst in Slice 10 teilweise nachgeliefert werden muss.

## Offene Risiken

- Atomare Dateiersetzung schützt nicht gegen zwei gleichzeitig schreibende Prozesse; prozessübergreifendes Locking bleibt außerhalb dieses Slice.
- Die Audit-Komponente ist zunächst additiv. Ihre vollständige Orchestrierung und Resume-Reihenfolge werden erst in den Folgeslices verdrahtet.
- Zwischen letzter Pfad-/Markerprüfung und atomarem Ersetzen besteht für einen böswilligen parallelen lokalen Prozess ein TOCTOU-Fenster. Der vorgesehene lokale Einzelprozessbetrieb akzeptiert dieses Restrisiko; die Sicherheitsrichtung aller erkannten Abweichungen bleibt fail-closed.

## Review-Feedback von Claude

<!-- audit:claude-review:begin -->
### Ereignis 2: Runde 5

- Reviewer: `claude`
- Freigabe: `YES`
- Validierungsbindung: `slice08-validation-776f45d9`
- Testdateien: `tests/test_audit_trail.py`, `tests/test_repo_changes.py`
- Prüfdimensionen: path ownership, injection, CRLF offsets, atomic idempotency, validation binding, semantic scope, Git root classification
- Größtes Restrisiko: filename-plus-known-marker stripping is intentionally lighter than the projection write boundary
- Realistische Bruchbedingung: a future consumer mistakes filename registration for full document ownership validation
- Eigene Findings: `C-02`, `C-03`, `C-04`, `C-05`, `C-06`
<!-- audit:claude-review:end -->

## Review-Feedback von Antigravity

<!-- audit:antigravity-review:begin -->
### Ereignis 3: Runde 2

- Reviewer: `antigravity`
- Freigabe: `YES`
- Validierungsbindung: `slice08-validation-776f45d9`
- Testdateien: `tests/test_audit_trail.py`, `tests/test_repo_changes.py`
- Prüfdimensionen: code fences, marker state tracking, audit idempotency, path verification, validation binding, semantic fingerprint logic
- Größtes Restrisiko: TOCTOU gap between document validation and atomic replacement in the local orchestrator model
- Realistische Bruchbedingung: adding a managed section without updating its ownership heading dictionary causes fail-closed projection rejection
- Eigene Findings: keine
<!-- audit:antigravity-review:end -->

## Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
- `C-02` Antwort 1: **angenommen** — A direct indented-marker semantic-content regression test was added.
- `C-03` Antwort 1: **bestritten** — A valid parent repository owns its child; the malformed-ancestor case is the relevant boundary.
- `C-04` Antwort 1: **angenommen** — Stripping was restricted to registered work-plan and Slice-MD names.
- `C-05` Antwort 1: **angenommen** — The exact malformed-ancestor regression test was added.
<!-- audit:codex-responses:end -->

## Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### Ereignis 1: `slice08-validation-776f45d9`

- Diff-Fingerprint: `776f45d94172e6c6f9c2aebdf0787e1cca61a3c72ad439dfdfaa3e37bc1ba691`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: 316 passed; compile PASS; active v2 dry-run PASS; diff check PASS
- Ausgabedigest: `3dcee3e41edc167fbda350188c411730d78cf9ba2b8bd6e326fb81ffd88bfe03`

| Matrixbefehl | Status | Exitcode |
|---|---|---:|
| PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider | PASS | 0 |
| python3 -m py_compile src/audit_trail.py src/repo_changes.py | PASS | 0 |
| ./run_task --dry-run --skip-git-check --test-command '' | PASS | 0 |
| git diff --check | PASS | 0 |
<!-- audit:validation-attestation:end -->

## Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
- Teständerungsfreigabe: `YES`
- Freigebende Stelle: user
- Begründung: work-plan-wide test-change exception for the modernization plan
- Pfade: `tests/test_audit_trail.py`, `tests/test_repo_changes.py`
- Pre-Mortems:
  - Ereignis 2: A future managed section is added to one path without updating the matching ownership and fingerprint tables.
  - Ereignis 3: A new audit event type or managed section is introduced without updating strict marker validation and fingerprint stripping in tandem.
<!-- audit:test-approval-premortem:end -->

## Findings-Lebenszyklus

<!-- audit:findings:begin -->
### `C-02` — `CLOSED`

- Quelle: `claude`; Runde 1
- Klasse: `OBSERVATION`
- Finding: Indented marker examples might be parsed as live audit markup
- Akzeptanztest: Prove indented marker examples remain semantic content
- Statusbegründung: Direct indented-marker test proves the content remains semantic

### `C-03` — `CLOSED`

- Quelle: `claude`; Runde 2
- Klasse: `OBSERVATION`
- Finding: The original foreign-parent test proposal contradicted valid Git worktree semantics
- Akzeptanztest: Distinguish valid parent worktrees from malformed foreign ancestor metadata
- Statusbegründung: Correct Git semantics retained and malformed-ancestor case covered

### `C-04` — `CLOSED`

- Quelle: `claude`; Runde 3
- Klasse: `OBSERVATION`
- Finding: Semantic stripping affected every docs/internal Markdown file
- Akzeptanztest: Restrict stripping or prove unrelated internal documents remain significant
- Statusbegründung: Stripping scope narrowed and unrelated internal docs remain significant

### `C-05` — `CLOSED`

- Quelle: `claude`; Runde 4
- Klasse: `OBSERVATION`
- Finding: Malformed foreign ancestor Git metadata lacked deterministic coverage
- Akzeptanztest: Add the exact malformed-ancestor regression test
- Statusbegründung: Deterministic malformed-ancestor regression test is green

### `C-06` — `OPEN`

- Quelle: `claude`; Runde 5
- Klasse: `OBSERVATION`
- Finding: Registered Slice-MD stripping uses the filename and known paired-marker trust boundary rather than the full projection heading structure
- Akzeptanztest: Retain this lighter boundary explicitly or require full structural validation before stripping
- Statusbegründung: –
<!-- audit:findings:end -->

## Entscheidungstabelle

<!-- audit:decision-table:begin -->
| ID | Quelle | Finding | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|---|
| C-02 | claude | Indented marker examples might be parsed as live audit markup | OBSERVATION | angenommen | erledigt: Direct indented-marker test proves the content remains semantic |
| C-03 | claude | The original foreign-parent test proposal contradicted valid Git worktree semantics | OBSERVATION | bestritten | erledigt: Correct Git semantics retained and malformed-ancestor case covered |
| C-04 | claude | Semantic stripping affected every docs/internal Markdown file | OBSERVATION | angenommen | erledigt: Stripping scope narrowed and unrelated internal docs remain significant |
| C-05 | claude | Malformed foreign ancestor Git metadata lacked deterministic coverage | OBSERVATION | angenommen | erledigt: Deterministic malformed-ancestor regression test is green |
| C-06 | claude | Registered Slice-MD stripping uses the filename and known paired-marker trust boundary rather than the full projection heading structure | OBSERVATION | offen | offen |
<!-- audit:decision-table:end -->

## Rückdokumentation in die Arbeitsplan-MD

Der Arbeitsplan verlinkt diese Slice-MD aus §5.1 und der Slice-08-Überschrift. Implementierungs-, Validierungs- und Reviewstatus werden nach Abschluss ergänzt.

## Freigabestatus

<!-- audit:approval-status:begin -->
- Implementierung bereit: `YES`
- Validierung: `PASS`
- Claude-Freigabe: `YES`
- Antigravity-Freigabe: `YES`
- Commit autorisiert: `YES`
<!-- audit:approval-status:end -->
