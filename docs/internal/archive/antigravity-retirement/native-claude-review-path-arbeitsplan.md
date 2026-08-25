# Phase 2 – Nativer Claude-Reviewpfad

TARGET_BRANCH: feature/native-claude-review-path

STATUS: MANUAL_BOOTSTRAP_IMPLEMENTED_AND_SLICE_APPROVED_BY_CLAUDE

## 1. Auftrag und Abgrenzung

Dieses Arbeitspaket setzt Arbeitspaket 3 aus der
[`ORCHESTRATOR_ROADMAP_PHASE_2_PLUS.md`](ORCHESTRATOR_ROADMAP_PHASE_2_PLUS.md)
um: Claude erhält für Plan-, Slice- und Finalreviews einen versionierten,
vollständig gebundenen JSON-Auftrag und liefert unmittelbar ein Ergebnis nach
`native-agent-review-result-v1`. Der vorhandene freie `response`-String und
der State-v3-Textmarkerparser liegen auf diesem Pilotpfad nicht mehr zwischen
Providerantwort und `ContractResult`.

Die Entwicklung erfolgt außerhalb von `run_task` nach dem
[`manuellen Bootstrapvertrag`](native-agent-json-manueller-bootstrap.md).
Antigravity bleibt `NOT_RUN (manual bootstrap exception)`. Der historische
Claude-Textpfad bleibt Standard und für bereits gebundene Läufe unverändert.
Der native Pfad ist zunächst nur über eine ausdrückliche, beim Start dauerhaft
persistierte Claude-Transportbindung aktivierbar.

Das Paket führt noch keine nativen Codex-Ergebnisse und keinen allgemeinen
`native-agent-json-v1`-Cutover ein. Sein Ergebnis ist eine tragfähige vertikale
Claude-Pilotstrecke, auf der mehrere echte Plan-, Slice- und Finalreviews
anschließend kontrolliert erprobt werden können.

## 2. Repositorybefund

- `src/native_review_contract.py` validiert und konvertiert bereits das
  geschlossene native Reviewresultat. Seine `request_id` bindet derzeit jedoch
  nur den lokalen Reviewkontext. Zielbranch, Basiscommit, Allowlist und das
  tatsächlich übertragene Evidenzmanifest fehlen in dieser Bindung.
- `src/agent_adapters.py::ClaudeAdapter` verwendet bereits Claudes
  `--json-schema`, erzwingt aber nur ein Objekt mit einem freien Stringfeld
  `response`. `extract_output()` entnimmt daraus Markertext und schneidet ihn
  an `STATUS: DONE` ab.
- `src/agent_runtime.py::run_agent_checked()` setzt für alle Agenten einen
  textuellen Abschlussmarker voraus und baut bei Vertragsfehlern erneut einen
  textuellen Reparaturprompt. Diese Logik darf für native Antworten weder
  ausgeführt noch als Fallback verwendet werden.
- `src/workflow.py::WorkflowEngine._run_review()` besitzt bereits die richtige
  Autoritätsfolge:
  erst fachlich validieren, dann den strukturierten Reviewrecord persistieren,
  anschließend State-Spiegel und Checkpoint fortschreiben. Die Validierung und
  der Record-Replay sind derzeit aber ausschließlich auf Markertext ausgelegt.
- `src/orchestrator.py::ProductionWorkflowDriver` bewahrt Inputmessung,
  Finalreview-Preflight, Providerattempt-Lifecycle und read-only Snapshot vor
  jedem Reviewerstart. Diese Sicherheitsgrenzen müssen auch den nativen Pfad
  unverändert umschließen.
- `ReviewPayload` persistiert Urteil, Finding-IDs und Evidenz, bindet aber noch
  weder Transportschema noch native Request-ID oder den Digest der kanonischen
  Providerantwort. Der bestehende Recoverypfad gewinnt die Antwort deshalb aus
  einer hashgebundenen Logdatei und parst sie erneut als Markertext.
- `WorkflowState.protocol_binding` unterscheidet nur `legacy-state-v3` und
  `structured-v1`. Ein allgemeiner neuer Protokollmodus wäre vor nativen Codex-
  und Antigravity-Pfaden verfrüht. Für den Pilot wird stattdessen eine eigene,
  optionale und immutable Reviewer-Transportbindung innerhalb von
  `structured-v1` benötigt.
- `ReviewPacket` ist intern bereits kanonisches JSON. Plan- und Finalreview
  nutzen dagegen noch größere textuelle Evidenzblöcke. Der neue Requestvertrag
  muss gemeinsame Metadaten nativ modellieren, darf fachliche Quelltexte und
  Diffs aber als digestgebundene Evidenzbestandteile transportieren.

## 3. Zielarchitektur und Invarianten

### 3.1 Geschlossener Requestvertrag

1. `native-agent-review-request-v1` ist ein geschlossenes Draft-2020-12-Schema
   mit den diskriminierten Typen `review_request` und
   `review_contract_repair_request`.
2. Ein vollständiger Reviewauftrag bindet mindestens:
   - Schema- und Requestversion;
   - Request-ID, Run-ID, Work-Unit-ID, Operation, Reviewer und Reviewart;
   - `structured-v1` als Persistenzprotokoll und `native-claude-review-v1` als
     Reviewertransport;
   - Zielbranch, Basiscommit und aktuellen Diff-Fingerprint;
   - sortierte, eindeutige autorisierte Pfade und Akzeptanzkriterien;
   - Approvalart, Slice-ID, Rundennummer und Governance-Schalter;
   - vollständige passende Validierungsattestierung;
   - offene beziehungsweise für die Runde aktualisierungspflichtige Findings;
   - Testdateien, Ankerherkunft und erlaubte Validierungsbefehlsfamilien;
   - ein sortiertes Evidenzmanifest aus Typ, semantischem Digest, Größe und
     entweder eingebettetem Inhalt oder content-addressierter Snapshotreferenz;
   - Version und Digest des erwarteten Response-Schemas.
3. Repositorypfade, Fingerprints, Finding-IDs und Befehle sind native
   JSON-Werte. Sie werden nicht aus einem Markdownprompt zurückgewonnen.
4. Die Request-ID ist der SHA-256-Digest der kanonischen vollständigen
   Requestbindung ohne das Request-ID-Feld selbst. Damit ändern Branchbasis,
   Allowlist, Attestierung, Findingzustand, Evidenzinhalt oder Response-Schema
   zwingend die Request-ID. Der Livepfad darf nicht auf die ältere, engere
   Kontextableitung zurückfallen.
5. Der Requestbuilder liefert gemeinsam mit dem Request einen eigenen
   unveränderlichen `BoundNativeReviewContext`, der den bisherigen fachlichen
   `NativeReviewContext` und die vollständige Request-ID kapselt. Produktive
   Live-, Persistenz- und Recovery-APIs akzeptieren ausschließlich diesen
   gebundenen Typ; sie besitzen keinen optionalen Override und keinen Default
   auf die ältere lokale Kontext-ID. Direkte providerunabhängige Kerntests
   dürfen den bisherigen `NativeReviewContext` weiterhin verwenden. Ein
   eigener gebundener Parser-/Konvertereinstieg weist aber eine Antwort mit nur
   der älteren engeren Kontext-ID nachweislich ab.
6. Ein kompakter Reparaturauftrag enthält ausschließlich Parent-Request-ID,
   Digest der abgewiesenen Antwort, stabile Fehlercodes und die kanonische
   abgewiesene Antwort. Diff, Snapshot und historische Evidenz werden nicht
   erneut gesendet. Es gibt höchstens einen solchen Aufruf je Request. Er darf
   keine bereits gültige fachliche Entscheidung umdeuten und ist kein
   Textmarkerfallback.

### 3.2 Provider- und Runtimegrenze

1. Der native Claude-Adapter ist eine explizite, immutable Adaptervariante mit
   Provideridentität `claude`; er teilt weder versteckten Moduszustand noch
   Outputextraktion mit einem bereits laufenden Legacyaufruf.
2. Claude erhält den kanonischen Request über den bestehenden privaten,
   digestgemessenen Datei-/Manifesttransport. `--json-schema` verwendet eine
   geschlossene providerkompatible Projektion des echten
   `native-agent-review-result-v1`-Schemas statt `{response: string}`. Weil
   Claude am Schema-Root keine Union/Komposition akzeptiert, beschreibt diese
   Projektion einen einzigen Pflichtschlüssel `result`; die unveränderte
   Review-/Stop-Union liegt darunter. Der Adapter entnimmt ausschließlich
   dieses native Objekt und erzwingt direkt danach dasselbe vollständige
   lokale Ergebnisschema erneut. Die Projektion bindet außerdem die konkrete
   Reviewerrolle und deren Finding-ID-Präfix, damit ein Claude-Aufruf bereits
   providerseitig keine `A-*`-Befunde erzeugen kann.
3. Die Extraktion liefert ausschließlich die kanonische vollständige
   `structured_output`-Antwort. Sie sucht weder `STATUS: DONE` noch
   `REVIEWER:`, `NEW_FINDING:` oder andere Textmarker.
4. Providerfehler, Quota, Authentifizierung, Prozessfehler und ungültige
   native Antworten bleiben verschiedene technische Fehlerklassen.
   Reviewprosa beeinflusst diese Klassifikation nicht.
5. Der native Runtimepfad verwendet die bestehenden Inputbudget-, Capability-,
   Snapshot- und Providerattempt-Grenzen. Er überspringt ausschließlich die
   Legacy-Markerprüfung und den textuellen Contract-Repair-Prompt.
6. Eine schema- oder domänenungültige Antwort wird als `output`-Fehler mit
   typisiertem Diagnosecode persistiert. Es gibt keinen automatischen
   Vollreview-Retry. Nur der eng gebundene native Reparaturauftrag darf einmal
   folgen.
7. Kleine Evidenz darf kanonisch im Request eingebettet werden. Evidenz oberhalb
   einer festen getesteten Grenze wird dagegen in content-addressierte Dateien
   ausgelagert; der Request bindet deren semantischen Digest und Größe, nicht
   zufällige Runtimepfade. Der native Claude-Adapter verwendet dafür denselben
   vollständig gemessenen Chunk-/Manifesttransport wie der Legacyadapter.
   Requestdatei, alle Evidenzdateien, Systemvertrag, Response-Schema und
   Startauftrag erscheinen einzeln in `ProviderInputMeasurement`; kein
   Evidenzpfad umgeht das Hard Limit.

### 3.3 Persistenz, State und Resume

1. `structured-v1` bleibt der Recordmodus. Eine optionale immutable Bindung
   `claude_review_transport=native-claude-review-v1` wird beim neuen Lauf
   ausdrücklich gesetzt, serialisiert und beim Resume wertgleich verlangt.
   Fehlende Bindung bedeutet historische Legacy-Textreviews.
2. Der generische `ReviewPayload` erhält rückwärtskompatible optionale Felder
   für Transportschema, Request-ID und SHA-256 der kanonischen Antwort. Bei
   nativen Reviews sind alle drei Pflicht und müssen zum Request, zum
   Responseinhalt, zur Rolle, Operation, Work Unit und zum Fingerprint passen.
   Historische Records ohne diese Felder bleiben lesbar.
3. Eine gültige native Antwort wird genau einmal in `ContractResult`
   konvertiert und vor State-Mirror, Checkpoint, Findingfolge,
   Quotaentscheidung oder weiterem Providerstart als Review- und
   Findingtransitionen persistiert.
4. Persistenz verwendet die kanonische native JSON-Antwort als Hashgrundlage.
   Wiederholung desselben Requests und Ergebnisses ist ein No-op; abweichender
   Inhalt unter derselben Request-ID hält fail-closed.
5. Recovery baut vor einem Providerstart denselben vollständigen Request und
   `BoundNativeReviewContext` erneut auf, findet genau einen request-, response- und
   fingerprintgebundenen Reviewrecord samt Log, validiert die kanonische
   Antwort erneut nativ und spiegelt das Resultat in State-v3. Dabei werden
   keine Textmarker geparst, keine Findings dupliziert und Claude nicht erneut
   gestartet.
6. Der Recoveryvertrag gilt für Plan-, Slice-, Korrektur- und Finalreviews,
   nicht nur für einen Correction-Slice-Sonderfall. Mehrdeutige Records,
   fehlende Logs, abweichende Requestbindungen oder eine unvollständige
   Attestierung halten resumierbar und fail-closed an.
7. Ein explizites CLI-Pilotflag darf nur bei neuem `structured-v1`-State die
   native Claude-Bindung setzen. Es ändert weder bestehende States noch den
   Watch-Default. Ein widersprüchliches Flag beim Resume wird abgewiesen.

## 4. Slice 1 – Requestvertrag und native Claude-Transportgrenze

### Ziel

Ein providerseitig noch nicht in den Workflow geschalteter, vollständig
getesteter Requestbuilder und eine native Claude-Adapter-/Runtimeoberfläche,
die echte strukturierte Ergebnisse liefert und keinerlei Markertext kennt.

### Exakter Änderungspfad

- `schemas/native-agent-review-request-v1.schema.json`
- `schemas/native-agent-review-result-v1.schema.json`
- `src/native_review_request.py`
- `src/native_review_contract.py`
- `src/agent_adapters.py`
- `src/agent_runtime.py`
- `tests/test_native_review_request.py`
- `tests/test_native_review_contract.py`
- `tests/test_agent_adapters.py`
- `tests/test_agent_runtime.py`
- `docs/internal/native-claude-review-path-arbeitsplan.md`
- `docs/internal/native-claude-review-path-review.md`

### Umsetzung

1. Definiere das geschlossene Requestschema und unveränderliche Request-,
   Evidenz- und Reparaturtypen. Verwende die gemeinsame
   `schema_validation.py`; keine zweite Schemaengine und keine Parserheuristik.
2. Baue aus lokalen Domänenwerten einen kanonischen Request und den exakt dazu
   passenden `BoundNativeReviewContext`. Der gebundene Typ kapselt den
   fachlichen `NativeReviewContext`, besitzt aber eine zwingende vollständige
   Request-ID und ist die einzige für Live-, Persistenz- und Recovery-APIs
   zulässige Kontextform. Berechne Request- und Evidenzdigests deterministisch
   und ohne zufällige Runtimepfade.
3. Ergänze eine eigenständige native Claude-Adapterinstanz. Kleine Evidenz
   bleibt im Request; größere Bestandteile werden an einer festen Grenze in
   content-addressierte Dateien ausgelagert und über das vorhandene
   Chunk-/Manifestverfahren gelesen. Messkomponenten müssen den echten
   Request, jede Evidenzdatei, das Manifest, Systemvertrag, Response-Schema und
   Startauftrag vollständig abbilden; zufällige Transportpfade werden nur in
   der Messrepräsentation stabilisiert.
4. Trenne in der Runtime allgemeine Providerausführung von der historischen
   Marker-/Reparaturlogik. Der bestehende Legacypfad bleibt byte- und
   verhaltenskompatibel; der native Pfad validiert genau einmal gegen Schema
   und Kontext und liefert ein typisiertes Ergebnis plus kanonisches JSON.
5. Implementiere den kompakten nativen Reparaturrequest als eigene Operation
   ohne Implementierungs- oder Reviewevidenz. Mehr als ein Reparaturversuch,
   Requestwechsel oder semantisch widersprüchliche Antwort wird abgewiesen.

### Akzeptanztests

- Derselbe vollständige Plan-, Slice- beziehungsweise Finalreviewauftrag
  erzeugt bytegleiches kanonisches JSON, denselben Evidenzdigest und dieselbe
  Request-ID.
- Änderungen an Branchbasis, Allowlist, einem Akzeptanzkriterium,
  Attestierung, Findingstatus, Evidenzbyte, Response-Schemadigest oder Runde
  ändern die Request-ID.
- Unsichere, doppelte oder unsortierte Pfade, absolute Pfade, `..`, unbekannte
  Felder, leere Pflichttexte und nichtkanonische SHA-Werte werden abgewiesen.
- Eingebettete Evidenz stimmt mit Größe und Digest überein. Eine
  Snapshotreferenz ist repositoryrelativ, content-addressiert und darf nicht
  zugleich eingebetteten Inhalt vortäuschen.
- Der erzeugte `BoundNativeReviewContext.request_id` entspricht exakt der
  vollständigen Requestbindung. Eine Antwort mit der alten engeren
  Kontextrequest-ID scheitert am gebundenen Parser-/Konvertereinstieg und in
  einem ProductionWorkflowDriver-nahen Test. Keine produktive API akzeptiert
  dafür einen ungebundenen `NativeReviewContext`.
- Eine große Evidenzfixture wird deterministisch in content-addressierte
  Dateien zerlegt. Request-ID und Providerinputdigest bleiben bei zufälligen
  Runtimepfaden stabil; alle Request-/Evidenzbytes werden von
  `measure_provider_input()` genau einmal erfasst und gegen das bestehende
  Limit geprüft.
- Der native Claude-Befehl enthält das echte Response-Schema; weder Schema noch
  Startdirektive verlangen ein `response`-Stringfeld oder State-v3-Marker.
- Eine gültige Claude-Envelope wird als vollständiges kanonisches
  `review_result` beziehungsweise `stop_request` extrahiert. Freie Präambel,
  Stringwrapper, Markdownfence, zusätzlicher Markertext und unbekannte Felder
  scheitern.
- Native Outputfehler werden als `output` klassifiziert, ohne technische
  Begriffe aus Finding- oder Evidenztext auszuwerten.
- Der kompakte Reparaturrequest enthält nachweislich weder Diff noch Snapshot
  noch vollständige historische Findingtexte, ist an Parent-Request und
  Response-Digest gebunden und kann höchstens einmal erzeugt werden.
- Alle bisherigen Claude-Textadapter- und Runtime-Regressionen bleiben
  unverändert grün.

## 5. Slice 2 – Opt-in Workflow, Persistenz und crashsicherer Replay

### Ziel

Ein ausdrücklich aktivierbarer nativer Claude-Pilotpfad durch Plan-, Slice-
und Finalreview, dessen autoritative Entscheidung vor dem State-Mirror
persistiert wird und nach jedem relevanten Crash ohne Textparser und ohne
zweiten Claude-Aufruf wiederhergestellt werden kann.

### Exakter Änderungspfad

- `schemas/orchestrator-artifact-v1.schema.json`
- `src/artifact_models.py`
- `src/artifact_bridge.py`
- `src/cli.py`
- `src/workflow_state.py`
- `src/workflow.py`
- `src/orchestrator.py`
- `tests/test_artifact_models.py`
- `tests/test_artifact_bridge.py`
- `tests/test_cli.py`
- `tests/test_workflow_state.py`
- `tests/test_workflow.py`
- `tests/test_orchestrator_runtime.py`
- `tests/test_structured_artifact_regressions.py`
- `docs/internal/native-claude-review-path-arbeitsplan.md`
- `docs/internal/native-claude-review-path-review.md`

### Umsetzung

1. Führe die optionale immutable Claude-Reviewertransportbindung im State ein.
   Alte State-Dokumente deserialisieren mit Legacy-Textdefault; ein neuer
   State erhält den nativen Wert nur durch das explizite Pilotflag.
2. Erweitere Reviewrecord und Artifact-Schema rückwärtskompatibel um die
   native Transportbindung. Append und Replay validieren die Kreuzfelder und
   verweigern gemischte oder teilweise native Records.
3. Erzeuge in `WorkflowEngine._run_review()` vor Recovery beziehungsweise
   Providerstart den vollständigen nativen Request aus `StepContract`,
   `WorkflowContext`, Änderungen, ReviewPacket/Evidenz, Attestierung und
   Findinghistorie. Der Legacy-Prompt darf nicht als Quelle für strukturierte
   Metadaten dienen.
4. Lass den Produktionsdriver bei gebundener Claude-Rolle den nativen Adapter
   und Runtimepfad verwenden und unmittelbar ein typisiertes Ergebnis samt
   kanonischer Antwort zurückgeben. Antigravity und nicht gebundene Claude-
   Läufe verwenden unverändert `invoke_reviewer() -> str`.
5. Persistiere native Review- und Findingrecords idempotent, bevor
   `_record_review()` State und Audit fortschreibt. Vermeide eine
   JSON-zu-Markertext-zu-JSON-Rundreise.
6. Verallgemeinere den record-ahead Recoverypfad auf alle Claude-Reviewarten.
   Er rekonstruiert Request und Kontext, prüft Record, Antwortdigest,
   Attestierung und Fingerprint und gibt das bereits konvertierte
   `ContractResult` zurück.
7. Halte den Default, Watchbetrieb und historische States auf dem Textpfad.
   Das Pilotflag ist bei `--resume` weder notwendig noch berechtigt, eine
   vorhandene Bindung zu verändern.

### Akzeptanztests

- State ohne neues Feld sowie expliziter Legacy-State roundtrippen
  unverändert; ein neuer nativer Pilotstate bindet exakt
  `native-claude-review-v1` und lässt sich beim Resume nicht umstellen.
- CLI-Neustart mit Pilotflag erzeugt die Bindung nur für einen neuen
  `structured-v1`-Lauf. Legacyprotokoll, bestehender State, widersprüchlicher
  Resume und Watch-Default werden sicher behandelt.
- Legacy-Reviewrecords ohne native Zusatzfelder bleiben schema- und
  replaygültig. Ein nativer Record verlangt Transportschema, Request-ID und
  passenden Response-Digest gemeinsam; Teilmengen scheitern.
- Plan-, Slice- und Finalreview werden im nativen Modus ohne Aufruf von
  `normalize_review_contract_output()`, `validate_review_response()` oder
  `_trim_after_done_marker()` in denselben `ContractResult`- und
  Finding-Lifecycle überführt.
- Eine gültige negative native Claude-Entscheidung erzeugt genau einen
  Reviewrecord und genau die geforderten Findingtransitionen. Eine positive
  Entscheidung verlangt weiterhin die vollständige fingerprintgleiche
  Attestierung, Pre-Mortem und Findingkonvergenz.
- Fault Injection unmittelbar nach Reviewrecord, nach Findingtransitionen,
  vor State-Mirror und vor Checkpoint zeigt: Resume startet Claude nicht neu,
  erzeugt keine doppelten Records und spiegelt exakt dasselbe Ergebnis.
- Derselbe Request mit abweichender Antwort, dieselbe Antwort unter fremdem
  Request, falscher Reviewer/Operation/Work Unit/Fingerprint, fehlender
  Response-Log oder mehrere passende Logs halten fail-closed an.
- Ein schema- oder domänenungültiges Ergebnis erzeugt keine fachliche
  Reviewentscheidung. Nach höchstens einem kleinen nativen Reparaturauftrag
  wird entweder genau ein gültiges Resultat persistiert oder resumierbar als
  Outputfehler angehalten.
- Quota vor vollständiger Antwort erzeugt keine Reviewentscheidung. Eine vor
  einem späteren technischen Fehler bereits vollständig empfangene und valide
  Antwort wird zuerst persistiert und bei Resume wiederverwendet.
- Providerinput-Messung, Attempt-Start/-Terminal, Finalreview-Preflight und
  read-only Snapshot laufen im nativen Pfad weiterhin genau einmal je
  physischem Start und bleiben request-/fingerprintgebunden.
- Ein repräsentativer programmatic End-to-End-Test durchläuft Plan-, Slice- und
  Finalreview im nativen Claude-Modus mit Fixture-Providerantworten vollständig
  ohne Textmarkerparser. Der entsprechende Legacy-Test bleibt unverändert
  grün.

## 6. Validierung

Fokussiert nach Slice 1:

```bash
python3 -m pytest tests/test_native_review_request.py tests/test_native_review_contract.py tests/test_agent_adapters.py tests/test_agent_runtime.py -v
```

Fokussiert nach Slice 2:

```bash
python3 -m pytest tests/test_artifact_models.py tests/test_artifact_bridge.py tests/test_cli.py tests/test_workflow_state.py tests/test_workflow.py tests/test_orchestrator_runtime.py tests/test_structured_artifact_regressions.py -v
```

Nach jedem Slice und vor dem Abschluss wegen Änderungen an Adapter, Runtime,
Workflow, CLI und State zwingend:

```bash
python3 -m pytest tests/ -v
git diff --check
```

Claude führt die vollständige Suite nicht selbst aus. Er erhält den
eingefrorenen Digest, relevante Diffs, lokale Attestierung und fokussierte
Testergebnisse.

## 7. Nichtziele

- Kein nativer Codex-Request oder Codex-Resultatvertrag.
- Kein nativer Antigravity-Pfad und keine Änderung seiner Reihenfolge.
- Kein allgemeiner Protokollmodus `native-agent-json-v1`.
- Keine Änderung des Defaulttransports für neue oder historische Läufe.
- Kein automatischer Textmarkerfallback nach einem nativen Fehler.
- Kein semantischer Evidence-Builder mit freier Snapshotnavigation; dieses
  Paket bindet und transportiert die vorhandene vollständige Evidenz nur
  strukturiert.
- Keine Tokenoptimierung durch Trunkierung oder Weglassen geänderter Pfade.
- Keine direkte Agent-zu-Agent-Kommunikation.
- Kein Push, Merge oder Abschlusscommit ohne erneute ausdrückliche
  Benutzerfreigabe.

## 8. Stopbedingungen

- Eine native Antwort kann nicht an den vollständigen Request einschließlich
  Evidenz und Response-Schema gebunden werden.
- Der Pilot müsste bestehende States still migrieren oder den Defaultpfad
  ändern.
- Record-ahead Recovery benötigt eine erneute Claude-Ausführung oder
  Textmarkerparsing.
- Ein fachlicher Reviewinhalt müsste lokal erfunden, aus Prosa heuristisch
  rekonstruiert oder durch eine technische Fehlerklassifikation ersetzt werden.
- Der native Pfad umgeht Inputbudget, Snapshot, Attestierung,
  Providerattempt-Telemetrie, Findingeigentum oder Finalreview-Preflight.
- Die Erweiterung des Reviewrecords macht historische Recordketten
  unlesbar oder mehrdeutig.
- Ein kompakter Reparaturpfad kann nicht ohne erneute vollständige Evidenz und
  ohne semantische Umdeutung implementiert werden. In diesem Fall hält der
  Pilot nach der ersten ungültigen Antwort fail-closed an; ein Vollreview-Retry
  wird nicht als Ersatz zugelassen.

## 9. Review- und Abschlussfolge

1. Direkter Claude-Planreview dieses vollständigen Plans mit Sonnet, Effort
   `high`, read-only und geschlossenem JSON-Ausgabeschema.
2. Umsetzung von Slice 1 durch Codex, fokussierte Tests und Vollsuite.
3. Direkter Claude-Slicereview auf dem eingefrorenen Slice-1-Digest.
4. Umsetzung von Slice 2, fokussierte Tests und Vollsuite.
5. Direkter Claude-Slicereview auf dem eingefrorenen Slice-2-Digest.
6. Gegebenenfalls jeweils kleinste Blockerkorrektur mit erneutem Claude-Review.
7. Mehrere kontrollierte native Claude-Pilotreviews für Plan, Slice und Final
   mit dokumentiertem Record-/Resume-Nachweis.
8. Direkter branchweiter Claude-Finalreview.
9. Dokumentierter Hinweis:
   `ANTIGRAVITY: NOT_RUN (manual bootstrap exception)`.
10. Abschlusscommit und Merge nur nach ausdrücklicher Benutzerfreigabe.

## 10. Tatsächlicher Durchführungsstatus

- Slice 1 und Slice 2 sind implementiert.
- Die letzte vollständige lokale Matrix einschließlich der nachgelagerten
  Reviewarten-Parametrisierung bestand mit 1.102 Tests in 182,17 Sekunden.
  `git diff --check` ist sauber.
- Claude genehmigte Slice 1 und den ersten Slice-2-Stand nativ. Die
  Observations C-03 und C-04 wurden geschlossen. Der im Korrekturreview neu
  gefundene Recovery-Blocker C-05 wurde korrigiert und von Claude nativ
  geschlossen. Es ist kein Claude-Finding offen.
- Plan-, Slice- und Finalrequest werden programmatisch als eigene native
  Reviewarten aufgebaut; der bestehende Slice-Durchstich beweist zusätzlich,
  dass der Claude-Pfad keinen Legacy-Markerparser aufruft.
- Der Benutzer entschied am 22. August 2026 aus Quotagründen ausdrücklich,
  den separaten branchweiten Claude-Finalreview dieses Pakets auszulassen und
  stattdessen einen Codex-Abschluss-Selbstcheck durchzuführen. Dieser
  Selbstcheck ist keine Eigenfreigabe. Die externe Abnahmebasis bilden die
  nativen Claude-Slice- und Korrekturreviews.
- Das orchestrierte Antigravity-Review blieb
  `NOT_RUN (manual bootstrap exception)`. Ein zusätzlich vom Benutzer direkt
  angestoßener, nicht State-v3-gebundener Antigravity-Selbstcheck bewertete den
  Branch positiv, bestätigte den nativen Codex-Pfad als nächsten Schritt und
  verlangte ebenfalls die persistente Roh-JSON-Antwort vor Verarbeitung.
- Commit und Merge bleiben bis zu einer ausdrücklichen Benutzerfreigabe
  unausgeführt.
