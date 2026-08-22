# Nativer Codex-Resultatpfad – Implementierungsreviews

## Reviewverfahren

- Reviewer: Claude Sonnet, Effort `high`
- Transport: nativer, schema-validierter JSON-Reviewvertrag
- Antigravity: gemäß ausdrücklicher Benutzerentscheidung nicht ausgeführt
- Validierung: lokale vollständige Suite durch den Codex-Hauptprozess; Claude führt keine Vollmatrix aus

## Slice 1 – Request-, Resultat- und Runtimevertrag

### Runde 1

Die erste Providerantwort enthielt nicht für jedes eigene offene Finding eine
Statusänderung und wurde deshalb vom nativen Reviewvertrag verworfen. Es wurde
keine fachliche Entscheidung übernommen.

### Runde 2

- Entscheidung: `denied`
- `C-02`: `OPEN` – symmetrische Markdownprojektion der nativen Transportfelder
  wurde ordnungsgemäß nach Slice 2 verschoben.
- `C-03`: `BLOCKER` – positive Korrektur- und Abschlussresultate sowie die
  Vollständigkeit der Findingdispositionen waren noch nicht ausreichend
  belegt.

### Runde 3

Der erste Providerattempt endete technisch mit
`error_max_structured_output_retries`; er erzeugte keine Reviewentscheidung.
Der einmalige Wiederholungsaufruf für denselben gebundenen Request wurde
akzeptiert.

- Request: `native-review-request-0e38cee4a79e84c810731cdb6c69fc74c406155a5e252d16b047cf43251d8bc5`
- Entscheidung: `approved`
- `C-02`: `OPEN` für Slice 2
- `C-03`: `CLOSED` – positive Korrektur- und Finalreport-Roundtrips,
  `self_check`-Erhalt sowie fehlende und fremde Finaldispositionen sind getestet.
- `C-04`: neue `OBSERVATION` – Slice 2 muss die Vollständigkeitsregel für offene
  Findingdispositionen zwischen nativem und historischem Codexpfad vergleichen.
- Pre-Mortem: Größtes Risiko ist eine versehentliche Aktivierung des nativen
  Pfads bei bestehenden oder nicht ausdrücklich pilotierten Läufen.

## Slice 2 – Workflowbindung, Persistenz und Resume

### Runde 1

- Request: `native-review-request-bceda17a6261e0d9454e78a226c51341608a6d851e4f872b0f037ff070e67853`
- Entscheidung: `approved`
- `C-02`: `CLOSED` – `AgentResultPayload` und `ReviewPayload` projizieren
  Transportschema, Request-ID und Antwortdigest ausschließlich aus akzeptierten
  Records; der Auditpfad übernimmt die Projektion ohne Markdown-Reparsing.
- `C-04`: `CLOSED` – ein Paritätstest weist nach, dass nativer und historischer
  Codexpfad dieselbe Vollständigkeit offener Findingdispositionen verlangen.
- `C-05`: neue `OBSERVATION` – der direkte Laufzeit- und Recoverybeweis war für
  Planung und Finalbericht noch schwächer als für Implementierung und Korrektur.
- Pre-Mortem: Größtes Risiko war ein späterer Sonderweg im separat implementierten
  Finalreportpfad, der ohne eigene Integrationsregression unentdeckt auf den
  Legacyparser zurückfallen könnte.

### Nacharbeit für Runde 2

Die fehlenden Plan- und Finalreport-Durchstiche wurden ergänzt. Dabei deckte der
neue Finalreporttest einen realen `TypeError` auf: Der native Pfad versuchte die
schreibgeschützte Property `WorkflowContext.distilled_context` mit
`dataclasses.replace()` zu setzen. Der Requestbuilder erhält den bereits
erzeugten branchweiten Kontext nun über einen expliziten Parameter. Zusätzlich
belegen parametrisierte Runtime-Tests Raw-ahead und Record-ahead Recovery ohne
zweiten Providerstart für `plan` und `final_report`.

### Runde 2

- Request: `native-review-request-c75b14b58b5e702241fa530cc4dba45cda7f76d331d6e16e9139c8985fe0b172`
- Entscheidung: `approved`
- `C-05`: `CLOSED` – Plan und Finalbericht umgehen den Legacyparser im echten
  Workflowdurchstich; Raw-ahead und Record-ahead Recovery sind für beide
  Resultatarten idempotent belegt.
- `C-06`: neue `OBSERVATION` – der Recovery-Paritätstest deckte als letzte
  Resultatart `correction` noch nicht ausdrücklich ab.
- Pre-Mortem: Größtes Risiko bleibt eine spätere Sonderbehandlung einer einzelnen
  Resultatart, die den gemeinsamen Recoverypfad unbemerkt verlässt.

### Nacharbeit vor dem Abschlussreview

Der parametrisierte Recoverytest umfasst nun auch `correction_result`. Damit sind
Planung, Implementierung, Korrektur und Finalbericht jeweils durch Raw-ahead- und
Record-ahead-Nachweise abgedeckt. `C-06` bleibt bis zur reviewer-owned Disposition
im branchweiten Claude-Abschlussreview offen.

## Branchweiter Abschlussreview

### Runde 1

- Request: `native-review-request-3a876014de74a0efa14e6c50eaeaadae11b94a76c2d149a388b6819de235bf39`
- Entscheidung: `denied`
- `C-06`: `CLOSED` – der Recovery-Paritätstest enthält nun ausdrücklich
  `correction_result`.
- `C-07`: neuer `BLOCKER` – ein Absturz nach dem nativen AgentResult-Record,
  aber vor dem letzten Findingantwort-Record konnte durch die Record-ahead-
  Recovery irrtümlich als vollständig behandelt werden.
- Pre-Mortem: Ein späterer Cold Resume könnte dadurch einen bereits beantworteten
  Befund wieder als unbeantwortet rekonstruieren.

### Korrektur für Runde 2

Die Record-ahead-Recovery führt die idempotente native Persistenz nach Prüfung des
AgentResult-Records erneut aus. Bereits vorhandene Records werden semantisch
verglichen, fehlende Findingantworten werden append-only ergänzt und abweichende
Idempotenzbindungen bleiben fail-closed. Ein Crash-Injection-Test unterbricht die
Persistenz exakt zwischen AgentResult und Findingantwort und weist anschließend
nach, dass Recovery genau einen dauerhaften Antwortrecord erzeugt und der
vollständig abgespielte Record-Chain dieselbe Codex-Disposition enthält.

### Runde 2

- Request: `native-review-request-add10b0f56024a7e418a45a1b1716cd2140acc6f64af2e46179872453b0fdc27`
- Entscheidung: `approved`
- `C-07`: `CLOSED` – Record-ahead Recovery vervollständigt nach einem
  Mid-Persistence-Crash die fehlenden Findingantworten idempotent, ohne einen
  zweiten Providerstart oder einen zweiten AgentResult-Record.
- Neue Findings: keine
- Pre-Mortem: Restrisiko wäre ein partieller Mehrfach-Finding-Crash mit
  abweichend rekonstruiertem Requestkontext. Die native Vertragsausnahme ist
  dabei jedoch eine `ValueError`-Unterklasse und wird von der bestehenden
  Recoverygrenze kontrolliert als `WorkflowExecutionError` behandelt.

Der branchweite Claude-Abschlussreview ist damit positiv; sämtliche Findings
`C-01` bis `C-07` sind geschlossen. Antigravity wurde gemäß Benutzerentscheidung
nicht ausgeführt.

## Abschließender Codex-Selbstcheck

Aufgrund der erneuten Claude-Quota wurde kein weiterer externer Providerreview
gestartet. Der letzte branchweite Vollständigkeitscheck wurde ausschließlich
lokal durch Codex und ohne Änderungen am geprüften Produktstand durchgeführt.
Er ist ein Abschluss-Selbstcheck und keine unabhängige Eigenfreigabe.

- Geprüft: geschlossene Request-/Response-Schemata, Request-ID- und
  Fingerprintbindung, Parserfreiheit des nativen Pfads, Raw-ahead- und
  Record-ahead-Recovery, idempotente Findingdispositionen, Legacy-Default,
  CLI-/Resume-Bindung sowie deterministische Markdownprojektion.
- Ergebnis: keine neuen konkreten Implementierungsdefekte und keine offenen
  Findings.
- Autoritative Validierung des unveränderten geprüften Stands:
  `python3 -m pytest tests/ -v` mit `1143 passed`.
- Diffhygiene: `git diff --check` erfolgreich; die ausgegebenen
  LF/CRLF-Hinweise sind keine Difffehler.
- Restrisiko: Der native Codex-Transport bleibt ein explizites Pilotflag. Ein
  produktiver Bootstrap-Lauf muss deshalb weiterhin bewusst mit
  `--native-codex-results` gestartet werden; bestehende Runs übernehmen nur
  ihre persistierte Protokollbindung.
