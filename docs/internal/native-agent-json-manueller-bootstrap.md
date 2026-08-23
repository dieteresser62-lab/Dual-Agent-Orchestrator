# Manueller Bootstrap für native Agenten-JSON-Verträge

## Zweck und Geltung

Dieser befristete Ausführungsvertrag gilt für die Entwicklung der nativen
Agentenkommunikation, solange der textmarkerbasierte Orchestrator seine eigene
Reparatur nicht zuverlässig reviewen kann. Er ersetzt nicht den regulären
State-v3-Vertrag für spätere Produktivläufe und ändert keine historischen
Protokollbindungen.

## Ausführung

1. Für diese Entwicklung werden weder `run_task` noch `run_task --watch`
   verwendet. Der pausierte 1.1E-State wird nicht manuell verändert und nicht
   fortgesetzt.
2. Codex erstellt den repository-grounded Plan und implementiert dessen
   freigegebenen manuellen Scope. Eine im Verlauf nachweislich notwendige
   Erweiterung der Dateianzahl oder das Anlegen zusätzlicher Dateien ist durch
   die dauerhafte Benutzerfreigabe vom 22. August 2026 zulässig. Sie muss im
   aktiven Reviewdokument fachlich begründet, vollständig getestet und Claude
   im nächsten Review als Bestandteil desselben eingefrorenen Stands vorgelegt
   werden. Die Dateianzahl ist damit keine Abbruchbedingung; fachliche
   Geschlossenheit und nachvollziehbare Scopekontrolle bleiben verpflichtend.
   Codex genehmigt die eigene Arbeit nicht.
3. Claude prüft mit Sonnet und Effort `high`:
   - den vollständigen Arbeitsplan vor der Implementierung;
   - jeden fachlich geschlossenen Implementierungsslice;
   - jede Blockerkorrektur;
   - den vollständigen Branch vor dem Abschlusscommit.
4. Jeder Claude-Aufruf ist read-only, verwendet einen eingefrorenen
   Commit-/Diff-Fingerprint und erzwingt seine semantische Antwort mit
   `--json-schema`. Claude erhält nur Auftrag, Akzeptanzkriterien, relevante
   Hunks und Schnittstellen, fokussierte Testergebnisse sowie eigene offene
   Findings.
5. Ungültiges JSON oder ein semantisch ungültiges Ergebnis ist keine Freigabe.
   Es gibt höchstens einen kompakten schema-only Reparaturaufruf; danach wird
   die Ursache lokal behoben oder der Schritt kontrolliert angehalten.
6. Claudes rohe JSON-Antwort, das verwendete Schema, Eingabedigest,
   Diff-Fingerprint, Befundstatus und Entscheidung werden im aktiven
   Reviewdokument festgehalten.
7. Antigravity wird in diesem Ausnahmeprozess nicht aufgerufen. Jedes
   Reviewdokument weist deshalb ausdrücklich
   `ANTIGRAVITY: NOT_RUN (manual bootstrap exception)` aus. Eine
   Antigravity-Freigabe wird weder angenommen noch simuliert.
8. Nach Änderungen an Orchestrierung, Prompt, Parser, Watch oder State läuft
   `python3 -m pytest tests/ -v`. Agenten führen diese Gesamtmatrix nicht selbst
   aus; die lokale Entwicklung führt sie deterministisch außerhalb des
   Reviewerprozesses aus.
9. Staging, Commit und Merge erfolgen weiterhin nur nach ausdrücklicher
   Benutzerfreigabe. Push, Rebase und History-Rewrite bleiben ausgeschlossen,
   solange sie nicht einzeln autorisiert wurden.

## Kombinierter Codex–Claude-Pilot

Ein neuer Pilotlauf bindet beide bereits vorhandenen nativen Transporte
gemeinsam mit `--native-codex-results --native-claude-reviews`. Diese
Kombination ist keine nachträgliche Umschaltung: Sie wird beim Laufstart in
State und Recordkette festgeschrieben und muss beim Resume unverändert bleiben.
Planung, Planrevision, Implementierung, Korrektur, Codex-Abschlussbericht und
Claude-Review verwenden dann ausschließlich schema- und domänenvalidierte
JSON-Verträge. Ein Fehler im nativen Pfad fällt niemals auf Textmarkerparser
oder Contract-Repair-Prosa zurück.

Der Findingstand für einen Codex-Folgeauftrag und die Codex-Dispositionen für
das anschließende Claude-Review stammen allein aus dem akzeptierten Replay der
append-only Recordkette. Vor einem frischen Providerstart wird diese Autorität
symmetrisch gegen den State-v3-Spiegel geprüft. Liegt ein vollständiges natives
Ergebnis record-ahead vor, wird es zuerst wiederhergestellt; derselbe Provider
wird dafür nicht erneut gestartet. Markdown wird deterministisch aus diesen
Records projiziert und zeigt die Konvergenz nach Work-Unit, Runde, Fingerprint,
Finding, Claude-Entscheidung, Codex-Disposition und Endstatus. Es wird weder
zur Entscheidung noch zur Wiederherstellung zurückgelesen.

Der Pilot ist bewusst nur ein Codex–Claude-Durchstich. Nach Claudes positiver
Konvergenz hält der reguläre Ablauf am nächsten Antigravity-Schritt an. Bis zur
nativen Antigravity-Integration ist damit weder eine Antigravity-Freigabe noch
eine produktive Gesamtfreigabe erteilt oder simuliert.

## Rückkehr zum regulären Orchestrator

Der derzeitige Ausnahmeprozess bleibt mindestens so lange aktiv, bis der
gemeinsame native Reviewresultatvertrag und der native Claude-Reviewpfad
schema- und domänenvalidiert mehrere repräsentative Plan-, Slice- und
Finalreviews ohne Textmarkerparser durchlaufen haben. Danach werden unter
demselben manuellen Verfahren zunächst die nativen Codex-Ergebnisse und die
vollständig strukturierte, orchestratorvermittelte Codex–Claude-
Korrekturschleife stabilisiert. Vor dem späteren Antigravity-Paket wird der
Ausnahmevertrag ausdrücklich neu bewertet; bis dahin bleibt Antigravity in
diesem Verfahren `NOT_RUN`. Die Rückkehr zum regulären Drei-Reviewer-
Orchestrator und der allgemeine `native-agent-json-v1`-Cutover bleiben bis zur
nativen Antigravity-Integration und bestandenen End-to-End-Läufen gesperrt.
