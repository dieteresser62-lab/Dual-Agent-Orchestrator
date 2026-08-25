# Roadmap: Native strukturierte Agentenkommunikation und späterer Legacy-Abbau

**Status:** aktive Roadmap für die Folgephasen

**Ablage:** `docs/internal/ORCHESTRATOR_ROADMAP_PHASE_2_PLUS.md`

**Letzte Ergänzung:** 2026-08-19, Erkenntnisse aus den Phase-1-Abschlussrunden

**Voraussetzung:** Phase 1 (`feature/structured-agent-artifacts`) ist vollständig validiert, von Claude und Antigravity abschließend freigegeben und lokal committed.

**Wichtig:** Dieses Dokument beschreibt Folgeaufträge. Phase 1 ist abgeschlossen;
die Roadmap autorisiert für sich allein noch keine Implementierung einer
Folgephase.

## 1. Ausgangspunkt nach Phase 1

Phase 1 führt für neu gestartete Workflows den Protokollmodus `structured-v1` ein. Versionierte, kanonische und validierte JSON-Records werden zur technischen Source of Truth. State-v3 bleibt ein geprüfter Laufzeitspiegel; Markdown bleibt eine deterministisch erzeugte, menschenlesbare Auditansicht.

Die Agentengrenze ist danach jedoch noch textbasiert:

- Agenten erhalten überwiegend textuelle Review- und Implementierungspakete.
- Agenten antworten mit Zeilenmarkern wie `NEW_FINDING`, `FINDING_STATUS`, `SLICE_APPROVAL` und `STATUS`.
- Adapter parsen diese Texte und erzeugen daraus strukturierte Records.
- Claude liest größere Reviewpakete in mehreren einzelnen `Read`-Aufrufen.
- Geschlossene Findings, Audittexte und wiederholte Vertragsinformationen können erneut in den Kontext gelangen.

Damit ist die Persistenz strukturiert, der eigentliche Agententransport aber noch nicht vollständig strukturiert oder kosteneffizient.

## 2. Leitprinzipien für alle Folgephasen

1. Technische Entscheidungen werden ausschließlich aus validierten strukturierten Daten getroffen.
2. Menschliche Eingaben und Auditansichten dürfen weiterhin Markdown verwenden.
3. Ein Formatwechsel darf Revieweigentum, Fingerprintbindungen, Validierungsattestierungen oder Resume-Sicherheit nicht abschwächen.
4. Alte laufende Workflows werden nie still auf einen neuen Protokollmodus umgestellt.
5. Unbekannte Versionen, fehlende Records und semantische Abweichungen halten fail-closed und resumierbar an.
6. Der Kontext eines Reviews soll mit der tatsächlich geänderten semantischen Oberfläche skalieren, nicht mit der gesamten Laufhistorie.
7. Kompaktheit darf keine relevanten Diff-Hunks, offenen Findings oder autoritativen Validierungsnachweise verbergen.
8. Jede Phase erhält einen eigenen Arbeitsplan, eigene Reviews, einen eigenen Abschluss und eine dokumentierte Rückfallgrenze.

### 2.1 Verbindliche Erkenntnisse aus dem Phase-1-Abschluss

Die späten Phase-1-Korrekturrunden haben zusätzliche Anforderungen sichtbar
gemacht, die Phase 2/2B als explizite Sicherheits- und Betriebsverträge
behandeln muss:

- Das Größenbudget gilt für den vollständigen Providerauftrag einschließlich
  Rollenvertrag, Auftrag, Evidenz, Ausgabeschema und Adapterhülle. Ein Budget nur
  für den Diff verhindert keine providerseitige Ablehnung der Gesamteingabe.
- Kompakte Evidenz darf die branchweite Prüfung nicht still in eine Teilprüfung
  verwandeln. Jede geänderte Datei und jede sicherheitsrelevante Vertragskante
  muss entweder im Paket enthalten oder über einen vollständigen,
  hashgebundenen Snapshot gezielt lesbar sein. Der Reviewer weist die
  tatsächlich geprüfte Abdeckung strukturiert aus.
- Jede Record-ID ist eine typisierte referenzielle Kante. Existenz, erwarteter
  Payload-Typ, Run-Zugehörigkeit, Fingerprint, zulässige Vorgängerbeziehung und
  gegebenenfalls Work-Unit-/Rollenbindung werden beim Schreiben und Resume
  generisch fail-closed validiert. Das umfasst insbesondere terminale
  Referenzen wie `WorkflowCompletionPayload.final_binding_id`.
- Ein fachlich vollständiges Review darf nicht wegen deterministisch ableitbarer
  fehlender Hüllenfelder mit dem gesamten Reviewpaket erneut an denselben
  Provider gesendet werden. Lokal ableitbare Felder werden lokal ergänzt;
  reviewer-eigene Entscheidungen werden niemals erfunden. Ist eine fachliche
  Ergänzung erforderlich, erhält der Reviewer nur einen kleinen typisierten
  Ergänzungsauftrag.
- Vor dem agentischen Finalreview läuft ein deterministisches Preflight über
  Recordgraph, Mirror-Symmetrie, Findings, Attestierungs- und
  Completion-Bindings, Pfadgrenzen und Paketbudget. Mechanisch erkennbare
  Integritätslücken dürfen nicht erst in seriellen Finalreview-Runden
  erscheinen.
- Ein vollständig empfangenes und validierbares Providerurteil wird vor
  Reparatur-, Quota- oder Resumeentscheidungen idempotent persistiert. Begriffe
  wie `quota`, `auth` oder HTTP-Zahlen in Reviewprosa dürfen niemals die
  technische Fehlerklassifikation des Provider-Envelopes bestimmen.

## 3. Phase 2 – Native strukturierte Agenten-I/O

### 3.1 Ziel

Codex, Claude und Antigravity erhalten typisierte JSON-Aufträge und liefern typisierte JSON-Entscheidungen. Textuelle Marker sind nur noch ein expliziter Kompatibilitätsadapter für ältere gebundene Workflows und kein Standardtransport neuer Sitzungen.

### 3.2 Strukturierte Eingabe

Für jede Agentenrolle wird ein versioniertes Auftragsschema definiert. Gemeinsame Felder sind mindestens:

- `schema_version`
- `request_id`
- `run_id`
- `work_unit_id`
- `role`
- `operation`
- `protocol_mode`
- `target_branch`
- `base_commit`
- `current_fingerprint`
- `authorized_paths`
- `acceptance_criteria`
- `validation_attestation`
- `open_findings`
- `evidence_manifest`
- `output_contract`

Rollenspezifische Payloads unterscheiden Planung, Implementierung, Korrektur, Slice-Review, Abschlussbericht und finales Review.

Repositorypfade, Befehle, Finding-IDs und Fingerprints sind native JSON-Werte. Sie werden nicht aus Prosa oder Markdown zurückgewonnen.

### 3.3 Strukturierte Ausgabe

Die Agentenausgabe verwendet diskriminierte Ergebnistypen, beispielsweise:

- `plan_result`
- `implementation_result`
- `final_report_result`
- `review_result`
- `stop_request`
- `instance_failure`

Ein Reviewresultat enthält typisierte Arrays für:

- neue Findings;
- Statusänderungen eigener Findings;
- Reklassifizierungen eigener Findings;
- Reviewevidenz;
- Pre-Mortem;
- Freigabeentscheidung.

Semantisch unvereinbare Kombinationen werden bereits durch Schema und Domänenmodell verhindert. Beispiele:

- Freigabe und Stop Request gleichzeitig;
- positive Finalfreigabe bei offenen Findings;
- Schließen eines Findings durch einen fremden Reviewer;
- `VALIDATE` an einer Observation als ausführbare Matrixerweiterung;
- Bereitschaft ohne erforderliche Testdateiangabe;
- fehlende Abschlussentscheidung.

Das Schema unterscheidet reviewer-eigene Entscheidungen von deterministisch
ableitbaren Metadaten. Die Liste geänderter Testdateien, der gebundene
Fingerprint und unveränderte Status bereits geschlossener Findings müssen nicht
vom Reviewer aus langer Prosa rekonstruiert werden. Ein Finalreview referenziert
unveränderte geschlossene Findings über ihre Record-IDs; nur neue oder geänderte
reviewer-eigene Aussagen werden als neue Entscheidungen persistiert.

### 3.4 Adapterstrategie

Für neue Workflows wird ein eigener Protokollmodus, beispielsweise `native-agent-json-v1`, beim Start unveränderlich gebunden.

- Native JSON-Antworten werden direkt validiert und als Records persistiert.
- Der bestehende Textmarkerparser bleibt ausschließlich für bereits gebundene `structured-v1`- und Legacy-Läufe erhalten.
- Kein automatischer Fallback von nativer JSON-Ausgabe auf Textmarker nach einem Parsefehler.
- Eine ungültige native Antwort wird als typisierte Vertragsverletzung behandelt.
- Rein syntaktisch reparierbare Providerhüllen dürfen deterministisch lokal normalisiert werden; fachliche Inhalte dürfen nicht lokal erfunden oder umgedeutet werden.

### 3.5 Akzeptanzkriterien Phase 2

- Ein vollständig neuer Workflow kann von Intake bis Finalfreigabe ohne Parsing textueller Rollenmarker laufen.
- Jede Agentenentscheidung ist vor ihrer Wirkung schema- und domänenvalidiert sowie fingerprintgebunden persistiert.
- Unterbrochene und wiederholte Aufrufe erzeugen keine doppelten Entscheidungen.
- Providerfehler, Quota und ungültige Hüllen bleiben von fachlichen Denials unterscheidbar.
- Historische Workflows bleiben über ihren gebundenen Adapter fortsetzbar.
- Markdown-Auditansichten bleiben vollständig und verständlich.
- Vollsuite, Fault-Injection-, Resume-, Quota- und End-to-End-Tests sind erfolgreich.
- Technische Providerfehler werden ausschließlich aus typisierten
  Envelope-/Prozessdaten klassifiziert, niemals aus frei formuliertem
  Reviewtext.
- Alle Record-Referenzen werden durch einen gemeinsamen Integritätsvalidator
  beim Append und Resume geprüft; unbekannte, typfalsche, run-fremde oder
  fingerprintfremde Ziele halten deterministisch an.
- Ein fachlich vollständiges Ergebnis mit lediglich lokal ableitbaren fehlenden
  Metadaten erzeugt keinen zweiten Agentenaufruf.

## 4. Phase 2B – Kompakte evidenzbasierte Reviewpakete

Diese Optimierung sollte zusammen mit Phase 2 oder unmittelbar danach erfolgen. Native JSON-Struktur allein reduziert keine Tokens, wenn weiterhin die vollständige Historie übertragen wird.

### 4.1 Semantisches Reviewpaket

Ein Reviewpaket enthält vollständig:

- aktuellen Auftrag und relevante Akzeptanzkriterien;
- aktuellen Diff beziehungsweise Korrekturdelta;
- alle offenen Findings;
- seit dem letzten Review veränderte Findingzustände;
- passende Validierungsattestierung;
- sicherheitsrelevante Verträge und Invarianten;
- notwendige Quellkontexte für die geänderten Hunks.

Nur kompakt referenziert werden:

- bereits geschlossene Findings;
- unveränderte frühere Sliceberichte;
- wiederholte Auditprojektionen;
- vollständige erfolgreiche Testausgaben;
- unveränderte allgemeine Rollenverträge.

Eine Referenz enthält mindestens Record-ID, Digest, Status und eine kurze kanonische Zusammenfassung. Bei Bedarf darf der Reviewer gezielt ein belegtes Detailartefakt lesen; unkontrollierte Repositoryexploration bleibt ausgeschlossen.

Für das branchweite Finalreview enthält das Paket zusätzlich ein vollständiges
Änderungsmanifest mit Pfad, Änderungsart, semantischem Digest und Größe. Ist
der vollständige Diff nicht innerhalb des Budgets transportierbar, wird nicht
blind die Mitte von Diffsektionen abgeschnitten. Stattdessen erhält der Reviewer
einen vollständigen read-only Snapshot und einen strukturierten Leseplan. Seine
Antwort weist pro Manifestgruppe aus, ob sie direkt aus dem Paket oder aus dem
Snapshot geprüft wurde. Eine positive Finalfreigabe ist ohne vollständige
Manifestabdeckung ungültig.

### 4.2 Paketbudget und Telemetrie

Pro Review werden protokolliert:

- Zeichen und Bytes des Auftrags;
- Anzahl und Größe der Evidenzteile;
- geänderte Produktiv- und Test-LoC;
- Anzahl offener, geschlossener und übertragener Findings;
- Anzahl der Agententurns und Toolaufrufe;
- Provider-Input-, Cache-Read-, Cache-Write-, Thinking- und Output-Tokens, soweit geliefert;
- geschätzte und tatsächliche Kosten;
- Grund einer eventuellen Budgetüberschreitung.

Normale Slice-Reviews erhalten ein konfigurierbares Paketbudget. Eine Überschreitung löst zuerst deterministische Kompaktierung aus, nicht das Abschneiden von Diff oder offenen Findings. Ist das Paket danach weiterhin zu groß, wird es fachlich begründet partitioniert.

Vor jedem Providerstart wird die tatsächlich serialisierte Gesamteingabe gegen
ein provider- und operationsspezifisches Hard Limit sowie ein kleineres
Sicherheitsbudget geprüft. Die Messung erfolgt nach Einbettung aller Rollen-,
Schema- und Adapteranteile. Eine Überschreitung startet keinen Agentenprozess,
sondern baut das Paket deterministisch neu oder partitioniert es. Pro Versuch
werden mindestens `serialized_input_chars`, `serialized_input_bytes`,
`provider_hard_limit`, `configured_budget` und die Größe jedes
Paketbestandteils protokolliert.

### 4.3 Vermeidung unnötiger Agentenaufrufe

- Eng begrenzte Formatfehler der Providerhülle werden lokal repariert, sofern die fachliche Antwort vollständig und eindeutig ist.
- Eine Vertragsreparatur erhält niemals erneut das vollständige Implementierungspaket.
- Unveränderte positive Reviewentscheidungen werden idempotent wiederverwendet, wenn Fingerprint, Auftrag und Reviewvertrag identisch sind.
- Quota-Resume führt nicht zu einer erneuten Reviewausführung, wenn bereits ein vollständig validiertes Ergebnis vorliegt.

- Ein Legacy-Review mit vollständiger fachlicher Entscheidung, aber fehlenden
  deterministisch ableitbaren Feldern, wird lokal vervollständigt. Fehlt eine
  reviewer-eigene Aussage, enthält der Reparaturauftrag ausschließlich die
  fehlenden Felder, deren zulässige Werte und die notwendigen Recordreferenzen;
  Diff, Snapshot und Findinghistorie werden nicht erneut übertragen.
- Bereits geschlossene Findings werden im Finalreview als unveränderliche
  Recordreferenzen übergeben. Der Reviewer gibt nur Reopen, Reklassifizierung
  oder neue Defekte aus und muss nicht jede alte Schließung erneut formulieren.
- Vertragsreparaturen sind vollwertige, persistierte Agenteninvocations. Quota,
  Auth-, Runtime- oder Providerfehler im Reparaturaufruf werden wie Fehler des
  primären Aufrufs resumierbar behandelt und dürfen niemals als ungefangener
  Traceback aus dem Workflow entweichen.
- Die vollständige Rohantwort des primären Reviews wird vor einer Reparatur
  unveränderlich gespeichert. Nach einem unterbrochenen Reparaturaufruf wird nur
  der kleine Reparaturauftrag fortgesetzt; das bereits abgeschlossene
  fachliche Review wird weder verworfen noch erneut ausgeführt.

### 4.4 Deterministisches Finalreview-Preflight

Vor Codex-Abschlussbericht und externen Finalreviews prüft der Orchestrator ohne
Agentenaufruf mindestens:

- Vollständigkeit und Typkorrektheit aller Recordreferenzen;
- Fingerprint-, Run-, Rollen- und Work-Unit-Bindungen;
- symmetrische Darstellung terminaler Fakten in Recordkette und Laufzeitspiegel;
- genau eine zulässige terminale Completion mit gültigem Final-Binding;
- keine offenen oder widersprüchlichen Findingzustände;
- vollständige und fingerprintgleiche Validierungsattestierung;
- erlaubte Pfade und produktive Dateigrenzen;
- Providerbudget des fertig serialisierten Abschlussauftrags;
- vollständige Manifestabdeckung des vorgesehenen Reviewpakets.

Der Preflight liefert einen typisierten Bericht und ersetzt keine fachliche
Freigabe. Ein Fehler erzeugt eine gezielte technische oder Korrektur-Work-Unit,
bevor teure Finalreview-Aufrufe beginnen.

### 4.5 Akzeptanzkriterien Phase 2B

- Reviewkosten skalieren messbar mit dem aktuellen Delta statt mit der Gesamthistorie.
- Ein typischer Slice-Review benötigt höchstens einen Evidenz-Lesevorgang oder eine begründete kleine Anzahl gezielter Reads.
- Offene Findings und relevante Diff-Hunks werden niemals aus Budgetgründen ausgelassen.
- Telemetrie macht Cache- und Thinking-Anteile sichtbar.
- Regressionstests sichern Paketinhalt, Kompaktierung, Referenzauflösung und Nichtverlust von Findings.
- Kein Provideraufruf überschreitet das vorab gemessene konfigurierte
  Gesamteingabebudget.
- Ein Finalreview weist für jeden Manifestpfad maschinenlesbar aus, auf welcher
  Evidenzbasis er geprüft wurde.
- Das Abschluss-Preflight erkennt fehlende oder falsche Completion-, Binding-,
  Attestierungs- und Approval-Referenzen vor dem ersten agentischen Finalreview.
- Eine syntaktische oder lokal ableitbare Reviewreparatur verursacht keinen
  vollständigen zweiten Reviewdurchlauf.
- Quota oder Providerfehler während einer notwendigen Vertragsreparatur
  erzeugen einen normalen `awaiting_resume`-Zustand ohne Traceback und ohne
  Verlust des ursprünglichen Reviewurteils.

## 5. Phase 3 – Stilllegung textueller Entscheidungsparser

### 5.1 Ziel

Nachdem native Agenten-I/O über mehrere reale Workflows stabil nachgewiesen wurde, werden Textmarker für neu gestartete Workflows vollständig deaktiviert. Der Parser bleibt nur in einem klar gekapselten Legacy-Modul für historische gebundene Sitzungen.

### 5.2 Voraussetzungen

- Mehrere abgeschlossene reale Multi-Slice-Workflows im nativen Modus;
- keine ungeklärten strukturellen Findings;
- belegte Resume-, Quota- und Prozessabbruchfestigkeit;
- dokumentierte Providerkompatibilität für alle drei Rollen;
- gesicherte Rückfallstrategie ohne Protokollwechsel eines laufenden Workflows.

### 5.3 Arbeiten

- Textmarkerparser aus dem Standardpfad entfernen;
- Legacyadapter isolieren und mit Enddatum beziehungsweise überprüfbarer Abschaltbedingung kennzeichnen;
- Promptverträge von Markerlisten bereinigen;
- alte Reparaturpfade für `REVIEWER must be first line`, fehlende Marker und ähnliche Textformatfehler stilllegen;
- Tests in native Vertrags- und Envelope-Tests überführen;
- Dokumentation und UML aktualisieren.

### 5.4 Akzeptanzkriterien Phase 3

- Neue Workflows enthalten keine technische Entscheidung, die aus Markdown oder Markerzeilen extrahiert wird.
- Legacy-Workflows bleiben reproduzierbar fortsetzbar.
- Ein versehentlicher Textfallback in einem nativen Lauf hält fail-closed an.
- Der Standardpfad besitzt keine doppelte fachliche Wahrheit.

## 6. Phase 4 – Kontrollierter Abbau von State-v3-Spiegel und Legacy-Fallback

Diese Phase ist optional und erfordert reale Betriebserfahrung. Markdown als menschliche Auditansicht soll grundsätzlich erhalten bleiben; zur Disposition stehen nur technische Rücklese- und Entscheidungsfallbacks.

### 6.1 Entscheidungsgrundlage

Vor Beginn sind zu erheben:

- Anzahl noch existierender Legacy-Läufe;
- letzter erfolgreicher Legacy-Resume;
- Nutzung von State-v3 als Diagnosequelle;
- Vollständigkeit der Recordprojektion;
- Wiederherstellbarkeit nach beschädigtem Index, Head oder State-Spiegel;
- Anforderungen an Aufbewahrung und Nachvollziehbarkeit.

### 6.2 Mögliche Zielarchitektur

- JSON-Recordkette ist alleinige technische Wahrheit.
- Ein rekonstruierbarer Laufzeitindex ersetzt entscheidungsrelevante State-v3-Daten.
- Markdown wird ausschließlich aus Records erzeugt und niemals eingelesen.
- Historische Legacy-Läufe werden entweder mit ihrer eingefrorenen Runtime fortgeführt oder kontrolliert als nicht mehr ausführbar, aber vollständig lesbar archiviert.
- Es gibt keine heuristische Migration alter Freigaben oder Findings.

### 6.3 Stopbedingungen

Phase 4 darf nicht erfolgen, wenn:

- aktive Legacy-Läufe ohne sicheren Fortsetzungspfad existieren;
- strukturierte Records nicht alle entscheidungsrelevanten Tatsachen abbilden;
- eine Rekonstruktion nach Prozessabbruch nicht deterministisch ist;
- Audit- oder Findingeigentum verloren gehen könnte;
- ein Rollback einen bereits strukturiert gebundenen Lauf in den Legacy-Modus zwingen würde.

## 7. Phase 5 – Betrieb, Aufbewahrung und Diagnose

### 7.1 Ziel

Langfristig wachsende Recordbestände bleiben performant, prüfbar und verständlich, ohne die append-only Nachvollziehbarkeit zu verlieren.

### 7.2 Themen

- rekonstruierbare Indizes und Integritätsprüfung;
- sichere Archivierung abgeschlossener Läufe;
- Aufbewahrungs- und Löschregeln mit expliziter Benutzerautorität;
- Kompaktierungs-Snapshots, die historische Records referenzieren statt überschreiben;
- Diagnosekommando für Recordketten, Fingerprints, offene Findings und fehlende Vorgänger;
- Export eines portablen Auditpakets;
- Größen-, Laufzeit- und Kostenmetriken pro Workflow;
- Datenschutzprüfung für Agentenprompts, Logs und persistierte Evidenz.

### 7.3 Akzeptanzkriterien Phase 5

- Jeder Index kann vollständig aus unveränderten Records rekonstruiert werden.
- Archivierung verändert keine Freigabe- oder Findingsemantik.
- Materielle Löschungen erfolgen niemals automatisch und benötigen eine explizite, protokollierte Benutzerentscheidung.
- Diagnoseausgaben nennen konkrete Record-IDs und Reparaturhinweise.

## 8. Empfohlene Auftrags- und Branchfolge

1. Phase 1 abschließen und committen. Erledigt mit Commit `2ad6957`.
2. Abgeschlossene Phase-1-Unterlagen archivieren und die Roadmap separat in
   `docs/internal/` committen. Erledigt mit den Commits `3bee0f9` und
   `1f97e54`.
3. Das erste Phase-2/2B-Arbeitspaket auf `feature/native-agent-json`
   durchführen. Dieser Branch ist ausschließlich der Bootstrap-Härtung des
   Reviewwegs zugeordnet und nicht der gesamten Roadmap.
4. Jedes weitere Arbeitspaket beginnt nach Abschluss, Review und lokaler
   Übernahme des Vorgängers auf einem neuen, fachlich benannten Feature-Branch.
5. Nach mehreren realen erfolgreichen Workflows Phase 3 separat planen.
6. Phase 4 erst nach einer dokumentierten Legacy-Bestandsaufnahme autorisieren.
7. Phase 5 unabhängig priorisieren, sobald Recordwachstum und
   Betriebserfahrung belastbare Anforderungen liefern.

## 9. Eigenständige Arbeitspakete für Phase 2/2B

Die folgenden Punkte sind **keine Slices eines gemeinsamen Orchestratorlaufs**.
Jeder Punkt ist ein eigenständiger Auftrag mit eigenem Branch, eigenem
repository-grounded Arbeitsplan, eigenen Reviews und einem abgeschlossenen
Merge- beziehungsweise Commitcheckpoint.

### 9.1 Gemeinsamer Ausführungsvertrag

- Ein Arbeitspaket umfasst im Regelfall höchstens ein bis drei Slices.
- Der Plan autorisiert nur den kleinsten fachlich vollständigen Dateiscope des
  aktuellen Pakets.
- Frühere Audit-, Slice- und Reviewdokumente werden nicht als vollständige
  Evidenz in den nächsten Lauf übernommen.
- Ein Folgepaket beginnt erst, wenn der Vorgänger validiert, ohne offene
  Findings abgeschlossen und lokal in den vorgesehenen Basisbranch übernommen
  wurde.
- Branchweite Finalreviews prüfen nur das Delta des aktuellen Arbeitspakets
  gegen dessen festgeschriebene Basis.
- Wird ein Paket während der Planung größer als drei sinnvoll prüfbare Slices,
  muss es vor der Implementierung erneut fachlich geteilt werden.
- Zusammengehörige Pfade werden innerhalb eines Pakets bis zur zulässigen
  Dateigrenze gebündelt, ohne Prüfbarkeit oder sichere Korrekturgrenzen zu
  verlieren.

### 9.2 Arbeitspaket 1 – Review-Bootstrap und deterministisches Preflight

Dieses Paket läuft auf `feature/native-agent-json`. Es reduziert zuerst die
Kosten und seriellen Fehlerquellen des noch textbasierten Entwicklungswegs,
bevor die native Agentenschnittstelle selbst umgesetzt wird.

- vollständiges provider- und operationsspezifisches Gesamteingabebudget;
- deterministisches Finalreview-Preflight für Recordgraph, Bindings, Findings,
  Attestierung, Pfadgrenzen und Completion;
- fail-closed Verhalten vor dem Providerstart bei Budget- oder
  Integritätsverletzungen;
- fokussierte Telemetrie über Paketbestandteile und tatsächliche
  Provideraufträge;
- keine stille Trunkierung oder unvollständige Manifestabdeckung.

### 9.2a Stabilisierungspaket 1.1 – Record-Autorität, Recovery und Kostenbremsen

Vor Arbeitspaket 2 wird ein eigenständiges Konsolidierungspaket eingeschoben.
Es fügt keine neue Protokolloberfläche hinzu, sondern stabilisiert die in
Arbeitspaket 1 unter realer Last sichtbar gewordenen Record-/Mirror-,
Resume-, Finalreview-, Logging- und Kostenpfade. Verbindliche Detailbefunde,
Architekturziele, Crash-Matrix, Kostenbremsen und Abnahmekriterien stehen in
[Phase 2 – Erkenntnisse aus Arbeitspaket 1 und Stabilisierungspaket 1.1](phase-2-arbeitspaket-1-erkenntnisse-und-stabilisierung-1-1.md).

Arbeitspaket 2 darf erst beginnen, wenn Paket 1.1 mehrere repräsentative
End-to-End-Läufe ohne manuelle State-, Record- oder Auditkorrektur bestanden
hat.

### 9.3 Arbeitspaket 2 – Native Verträge und Referenzintegrität

- versionierte Request-/Response-Schemas und Domänenmodelle;
- gemeinsames rollenunabhängiges `review_result`-Basismodell für Findings,
  Statusänderungen, Reklassifizierungen, Reviewevidenz, Pre-Mortem und
  Freigabeentscheidung; Claude und Antigravity ergänzen dieses Modell in den
  Folgepaketen nur um ihre Rollen- und Reihenfolgeverträge;
- diskriminierte Ergebnis- und Fehlertypen;
- generischer Recordgraph-Integritätsvalidator;
- Validierung aller Run-, Fingerprint-, Rollen-, Work-Unit- und
  Vorgängerbindungen;
- Legacy-Lesbarkeit ohne stillen Protokollwechsel.

Stand 22. August 2026: Der erste providerunabhängige Teil dieses Pakets ist als
nativer `review_result`-/`stop_request`-Kern abgeschlossen und archiviert. Er
umfasst das geschlossene Response-Schema, das unveränderliche Domänenmodell,
die Kontext- und Findinginvarianten sowie die gemeinsame öffentliche
Schema-Validierung. Die rollenspezifische Requesthülle und ihre Livebindung
werden bewusst erst zusammen mit dem jeweiligen Providerpfad umgesetzt, damit
kein ungenutzter Transportvertrag vorweggenommen wird.

### 9.4 Priorisierungsentscheidung – Codex–Claude vor Antigravity

Ab 22. August 2026 wird die weitere Umsetzung nach ihrem unmittelbaren Nutzen
für die tägliche Planungs-, Implementierungs- und Korrekturschleife geordnet.
Nach dem nativen Claude-Reviewpfad folgen deshalb native Codex-Ergebnisse und
die vollständig strukturierte Codex–Claude-Konvergenz. Antigravity bleibt Teil
des Zielsystems, wird aber erst auf der dann stabilen gemeinsamen Transport-,
Persistenz- und Resume-Infrastruktur integriert.

Codex und Claude kommunizieren dabei nicht direkt miteinander. Der
Orchestrator bleibt die einzige Vermittlungs- und Autoritätsgrenze: Er baut
typisierte Aufträge, persistiert Ergebnisse und Findings, erzeugt daraus den
nächsten gebundenen Auftrag und erzwingt Reihenfolge, Eigentum, Fingerprint und
Idempotenz. Die vorgezogene Zweierkette darf daher keine Claude-spezifischen
Recordmodelle oder Abkürzungen einführen, die eine spätere Antigravity-
Integration erschweren.

Die verbindliche Ausführungsreihenfolge der noch offenen Pakete ist:

1. nativer Claude-Reviewpfad;
2. native Codex-Ergebnisse;
3. geschlossene strukturierte Codex–Claude-Korrekturschleife;
4. semantischer Evidence-Builder;
5. native Antigravity-Reviews;
6. End-to-End-Cutover.

### 9.5 Arbeitspaket 3 – Native Claude-Reviews

- versionierte native Review-Requesthülle und deterministischer
  `NativeReviewContext` für Plan-, Slice- und Finalreview;
- direktes Claude-Resultatschema statt einer JSON-Hülle mit freiem
  textuellem `response`-Feld;
- native Claude-Reviewantwort mit Finding-Lifecycle;
- idempotente Persistenz vor Reparatur-, Quota- oder Resumeentscheidungen;
- kleine typisierte Ergänzungsaufträge statt vollständiger Reviewwiederholung;
- keine technische Fehlerklassifikation aus Reviewprosa;
- kein Textmarkerfallback nach einem nativen Vertragsfehler;
- zunächst expliziter Pilotpfad ohne Änderung des Defaultprotokolls.

Das Paket ist abgeschlossen, wenn je mehrere repräsentative Plan-, Slice- und
Finalreviews schema- und domänenvalidiert ohne Textmarkerparser gelaufen sind,
ein vollständig empfangenes Ergebnis einen Crash vor dem State-Checkpoint
überlebt und Resume keinen zweiten Claude-Aufruf oder doppelte Findings
erzeugt.

### 9.6 Arbeitspaket 4 – Native Codex-Ergebnisse

- versionierte native Request- und Responseschemas für Codex;
- native Planungs-, Implementierungs-, Korrektur- und Abschlussresultate;
- typisierte Readiness-, Testdatei-, Finding-Response- und Stopdaten;
- deterministische Übernahme orchestrator-eigener Metadaten statt Echo in
  Modellprosa;
- kein Rückfall auf Textmarker bei Vertragsfehlern eines nativ gebundenen
  Laufs;
- historische Codex-Läufe bleiben über ihren gebundenen Textadapter lesbar und
  fortsetzbar.

### 9.7 Arbeitspaket 5 – Geschlossene Codex–Claude-Korrekturschleife

- Claude erzeugt und aktualisiert Findings ausschließlich als native,
  reviewer-eigene Entscheidungen;
- der Orchestrator persistiert das Claude-Ergebnis vor jeder Folgeentscheidung
  und baut daraus genau einen typisierten Codex-Korrekturauftrag;
- Codex beantwortet jedes offene Finding mit einer strukturierten
  Disposition, ohne es selbst schließen oder reklassifizieren zu können;
- Claude schließt oder eskaliert das eigene Finding in einem neuen, an den
  korrigierten Fingerprint gebundenen Review;
- Crash, Quota, Resume und wiederholte Zustellung erzeugen weder doppelte
  Findings noch doppelte Korrekturen oder Reviewerentscheidungen;
- dieselbe Mechanik trägt Planung, Slice-Konvergenz und branchweiten Abschluss,
  ohne direkte Agent-zu-Agent-Kommunikation.

Das Paket ist der erste vollständige native Nutzpfad: Plan beziehungsweise
Implementierung durch Codex, Review durch Claude, gegebenenfalls Korrektur und
erneute Claude-Entscheidung müssen ohne textuelle Rollenmarker konvergieren.

### 9.7a Contract Closure vor dem nächsten Bootstrap-Smoke-Test

Der native Nutzpfad wird erst wieder als Bootstrap-Smoke-Test eingesetzt,
nachdem seine allgemeinen Leseschemas und seine request-spezifischen
Writerschemas nachweislich dieselbe fachliche Ergebnismenge beschreiben. Das
manuelle Arbeitspaket `native-agent-contract-closure` schließt diese Grenze in
sechs direkt entwickelten und einzeln geprüften Slices.

Stand 24. August 2026:

- Slice 1 (Codex-Writerschemas) und Slice 2 (Claude-Writerschemas) sind von
  Claude freigegeben und lokal committed.
- Slice 3 hat 14 verfügbare native Rohantworten versioniert; zwölf vorhandene
  kanonische Claude-Requests ergeben exakt drei digestverifizierte
  `request_bound`-Paare. Nur diese Paare dürfen historische
  Writer-/Domänenaussagen belegen; elf weitere Antworten bleiben
  `schema_only`.
- Die Differentialmatrix deckt alle acht Writerformen ab, prüft die typisierte
  Provider-Ausnahmeliste und besitzt eine absichtlich gelockerte Writerregel
  als rote Kontrolle.
- Native Codex-Canaries laufen über dieselbe Adapter-/Runtimefunktion wie die
  Produktion, aber über eine typisierte `read-only`-Grenze mit CWD und
  Evidenzassetziel außerhalb des Repositorys. Erfolgreiche Canaries liegen für
  Plan, Implementierung, Korrektur und Finalbericht vor.
- Für Claude liegen erfolgreiche direkte Canaries für Plan, Konvergenz und
  Finalreview vor. Der Konvergenz-Canary bindet insbesondere die zuvor nur
  flacher sondierte Komposition `status_changes.items.oneOf` im tatsächlich
  serialisierten Requestschema. Der initiale Slice bleibt entsprechend dem
  freigegebenen Vertrag durch ein echtes `request_bound`-Paar belegt; die
  historischen Konvergenzpaare bleiben zusätzlich als Kompatibilitäts- und
  Domänennachweise erhalten.
- Jeder Canary hat den vollständigen Git-Status vor und nach dem Aufruf
  bytegleich gehalten. Kein Canary schreibt State, Checkpoints, Recordketten,
  Inbox, Outbox oder Index.
- Slice 5 setzt das request-spezifische Writerschema auch lokal vor Raw-
  Callback, aktueller Record-Ahead-Persistierung und Domänenkonvertierung
  durch. Die Bundle-Selbstprüfung bindet kanonische Requestbytes, Digest,
  transportierte Kontextprojektion, Responsevertrag und Evidenzassets;
  geschlossene Codex-Findings bleiben ausdrücklich Autorität der
  Workflow-Recordkette.
- Der reguläre Finalreviewpfad setzt derzeit
  `allow_new_observations=True`. Die lokale Finalregel verbietet deshalb
  unabhängig von diesem produktiven Kontextflag neue oder aus Blockern
  reklassifizierte Observations.
- Alle sieben `live_canary`-Nachweise tragen den bei ihrer Ausführung
  verwendeten Basiscommit. Ihre gespeicherten Request-IDs werden aus diesem
  eingefrorenen Commit rekonstruiert und hängen nicht mehr vom späteren
  Repository-`HEAD` ab.
- Slice 6 bindet für alle sieben `live_canary`-Zeilen zusätzlich den SHA-256
  der aus dem eingefrorenen Kontext rekonstruierten kanonischen
  Writerschemabytes bitgenau an `writer_schema_sha256`. Eine siebenfache
  Negativmatrix beweist, dass ein einzeln manipulierter Schemadigest jeder
  Writerform fail-closed auffällt; Präfix-, Längen- oder Zeichenmengenprüfungen
  genügen nicht.

Der nächste Bootstrap-Smoke-Test ist erst nach grünem vollständigem Testlauf
und unabhängiger Freigabe des letzten Korrekturslices zulässig. Die
Contract-Closure-Artefakte ersetzen
keine End-to-End-Freigabe; sie beseitigen ausschließlich die zuvor häufige
Klasse „provider-schema-valide, anschließend lokal wegen einer ebenfalls
schema-ausdrückbaren Regel ungültig“.

### 9.8 Arbeitspaket 6 – Semantischer Evidence-Builder

- offene Findings, Delta-Hunks und relevante Vertragskanten;
- vollständiges Änderungsmanifest mit semantischen Digests;
- hashgebundener Snapshot und gezielter Leseplan für große Finalreviews;
- ausschließlich allowlist-gebundene Reads auf vorab bekannte
  Repositorypfade; der Leseplan darf keine freie oder unkontrollierte
  Repositoryexploration auslösen;
- maschinenlesbarer Nachweis vollständiger Manifestabdeckung;
- kompakte Referenzen auf unveränderte geschlossene Findings und frühere
  Attestierungen.

### 9.9 Arbeitspaket 7 – Native Antigravity-Reviews

- native Antigravity-Reviewantwort auf demselben providerunabhängigen
  `review_result`-Kern;
- unveränderte Freigabereihenfolge nach Claude für denselben Fingerprint;
- genau einmalige Ausführung pro gebundener Reviewentscheidung;
- Resume-, Quota- und Providerfehlerverhalten analog zum bereits stabilisierten
  Claude-Pfad;
- keine Sonderbehandlung, die Findingeigentum, Persistenz oder
  Korrekturauftragsbildung der Codex–Claude-Kette umgeht.

Die zeitliche Vertagung ist keine Freigabe für einen dauerhaften
Zwei-Agenten-Produktivmodus. Antigravity bleibt Voraussetzung für den
regulären Drei-Reviewer-Orchestrator und den anschließenden Cutover.

### 9.10 Arbeitspaket 8 – End-to-End-Cutover

- unveränderlich gebundener Protokollmodus `native-agent-json-v1` für neue
  Workflows;
- Intake bis Finalfreigabe ohne Parsing textueller Rollenmarker;
- Fault-Injection gegen jede Referenzkante;
- Resume-, Quota-, Idempotenz-, Watch- und Prozessabbruchtests;
- Dokumentations- und UML-Abgleich;
- mehrere reale Pilotläufe vor Beginn von Phase 3.

## 10. Gesamtabnahme der Roadmapumsetzung

Der gewünschte Endzustand ist erreicht, wenn:

- Menschen weiterhin einfache Markdown-Aufgaben in die Inbox legen können;
- der Orchestrator daraus strukturierte Aufträge erzeugt;
- Agenten untereinander und mit dem Orchestrator technisch über validierte JSON-Verträge kommunizieren;
- Reviews nur relevante Evidenz und offene Findingketten erhalten;
- Markdown ausschließlich Eingabe- beziehungsweise Auditfunktion besitzt und keine versteckte technische Entscheidungsquelle mehr ist;
- Resume, Quota, Watch, Korrekturen, Commit und Abschlussfreigabe ohne manuelle Protokollreparatur funktionieren;
- abgeschlossene Workflows null offene Findings besitzen;
- Kosten und Kontextgröße transparent messbar und begrenzt sind;
- Legacy-Abbau nur nach gesonderter, belegter Freigabe erfolgt.
