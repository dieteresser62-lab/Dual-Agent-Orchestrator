# Phase 2 – Native Codex-Ergebnisse

TARGET_BRANCH: feature/native-codex-result-path

STATUS: DRAFT_AWAITING_DIRECT_CLAUDE_PLAN_REVIEW

## 1. Auftrag und Abgrenzung

Dieses Arbeitspaket setzt Arbeitspaket 4 aus der
[`ORCHESTRATOR_ROADMAP_PHASE_2_PLUS.md`](ORCHESTRATOR_ROADMAP_PHASE_2_PLUS.md)
um. Codex erhält für Planung, Implementierung, Korrektur und branchweiten
Abschluss einen versionierten JSON-Auftrag und liefert unmittelbar ein
geschlossenes Ergebnis nach `native-agent-codex-result-v1`. Auf dem nativen
Pilotpfad liegen weder `PLAN_READY`, `IMPLEMENTATION_READY`,
`FINAL_REPORT_READY`, `FINDING_RESPONSE`, `STOP_REQUESTED` noch
`STATUS: DONE` zwischen Providerantwort und `CodexContractResult`.

Der vorhandene Codex-Textpfad bleibt Standard und für historische sowie
bereits gebundene Läufe unverändert. Der native Pfad wird ausschließlich durch
eine beim Start persistierte immutable Transportbindung aktiviert. Ein
Vertragsfehler fällt nicht auf den Textparser zurück.

Das Paket ändert noch nicht die fachliche Codex–Claude-Konvergenzschleife und
führt keinen nativen Antigravity-Pfad ein. Es macht lediglich jeden bereits
vorhandenen Codex-Schritt nativ transportierbar. Die vollständig native
Finding-Korrekturfolge bleibt Arbeitspaket 5.

Die Planung wurde am 22. August 2026 während der Claude-Quotapause erstellt.
Sie darf repository-grounded vervollständigt und lokal validiert werden; vor
der produktiven Implementierung ist der direkte Claude-Planreview der nächste
verbindliche Review-Eckpunkt.

## 2. Repositorybefund

- `src/agent_adapters.py::CodexAdapter` überträgt einen freien Textprompt über
  stdin und liest die letzte Modellnachricht aus `--output-last-message`.
  Die installierte Codex-CLI unterstützt bereits `--output-schema`; der
  Adapter nutzt diese Capability noch nicht.
- `src/agent_runtime.py::run_agent_checked()` verlangt für Codex weiterhin
  `STATUS: DONE`, sucht Textflags und erzeugt bei Formfehlern einen weiteren
  textuellen Reparaturprompt. Der native Pfad darf keine dieser Operationen
  ausführen.
- `src/contracts.py::CodexContractResult` bildet Readiness, Stop,
  Testdateien, Findingantworten und Sliceplan bereits als Domänentypen ab.
  `validate_codex_response()` gewinnt sie jedoch ausschließlich aus Markern.
- `src/workflow.py` erzeugt Codex-Aufträge als große Prompts und übergibt
  deren Ergebnis nach lokaler Normalisierung an den Textparser. Planung,
  Implementierung, Korrektur und Abschluss benutzen dieselbe fachliche
  Resultatverarbeitung, aber unterschiedliche `CodexStepContract`-Varianten.
- `src/orchestrator.py::ProductionWorkflowDriver.invoke_codex()` besitzt noch
  keinen nativen Request- oder Recoverypfad. Providerinput-Messung,
  Providerattempt-Lifecycle und dauerhafte Logs sind aber bereits gemeinsame
  Sicherheitsgrenzen.
- `AgentResultPayload` persistiert heute nur Rolle, Work Unit, Outcome und
  Testdateien. Transport, Request-ID und Antwortdigest fehlen. Deshalb kann
  ein vollständig empfangenes Codex-Ergebnis nach einem Crash vor dem
  State-Checkpoint nicht requestgebunden aus der Recordkette rekonstruiert
  werden.
- `ProtocolBinding` kann den nativen Claude-Transport immutable binden, aber
  noch keinen Codex-Transport. Fehlende Codex-Bindung muss weiterhin eindeutig
  den historischen Textpfad bedeuten.
- Der manuelle Claude-Bootstrap hat einen Audit-Gap sichtbar gemacht: Eine
  schema- und requestgültige Antwort wurde verarbeitet, ihre vollständigen
  kanonischen Responsebytes aber nicht zusätzlich dauerhaft abgelegt. Der
  native Codex-Livepfad muss deshalb die kanonische Rohantwort vor
  AgentResult-, Finding-, State- oder Checkpoint-Fortschreibung schreiben und
  ihren Digest im Record binden.

## 3. Zielarchitektur und Invarianten

### 3.1 Geschlossener Codex-Request

1. `native-agent-codex-request-v1` ist ein geschlossenes Draft-2020-12-Schema
   mit den diskriminierten Auftragsarten `plan`, `implementation`,
   `correction` und `final_report`.
2. Jeder Auftrag bindet mindestens Schema- und Requestversion, Request-ID,
   Operation, Run und Work Unit, Zielbranch, Basiscommit, aktuellen
   Fingerprint, `CodexStepContract`, autorisierte Pfade, Assignment,
   Arbeitskontext, offene Findings sowie ein sortiertes Evidenzmanifest.
3. Orchestrator-eigene Tatsachen wie Work-Unit-ID, Slice-ID, Runde,
   Fingerprint, Testfreigabe und Attestierung werden ausschließlich im Request
   vorgegeben und beim Konvertieren aus dem gebundenen Kontext übernommen. Das
   Modell darf sie nicht als autoritative Metadaten neu deklarieren.
4. Die Request-ID ist der SHA-256-Digest der kanonischen vollständigen
   Requestbindung ohne das Request-ID-Feld. Eine Änderung an Kontext,
   Findings, Allowlist, Attestierung, Evidenz oder erwartetem Responseschema
   erzeugt eine andere Request-ID.
5. Textuelle Assignment-, Kontext-, Diff- und Berichtsinhalte bleiben als
   typisierte JSON-Felder oder digestgebundene Evidenzassets zulässig. Aus
   ihnen werden keine Kontrollmarker zurückgeparst.
6. Der Builder liefert zusammen mit dem Request einen immutable
   `BoundNativeCodexContext`. Parser, Persistenz und Recovery akzeptieren nur
   diesen gebundenen Kontext und keinen losen Request-ID-Override.

### 3.2 Geschlossenes Codex-Ergebnis

1. `native-agent-codex-result-v1` ist eine geschlossene, diskriminierte Union
   aus `plan_result`, `implementation_result`, `correction_result`,
   `final_report_result` und `stop_result`.
2. Gemeinsame Felder sind Schema- und Resultatversion, Request-ID und
   Resultatart. Es gibt keine Rollen-, Phasen-, Status- oder Legacy-Marker.
3. `plan_result` enthält typisierte Readiness und den Sliceplan.
   `implementation_result` und `correction_result` enthalten Readiness,
   sortierte eindeutige Testdateien und Finding-Dispositionen.
   `final_report_result` enthält Readiness, Finding-Dispositionen und einen
   nichtleeren branchweiten Selbstcheck. `stop_result` enthält ausschließlich
   Regel-ID, Begründung und gegebenenfalls exakte Remediationpfade.
4. Finding-Dispositionen referenzieren ausschließlich offene vorhandene
   Finding-IDs und verwenden `accepted` oder `rejected` mit nichtleerer
   Begründung. Sie dürfen Findingstatus, Klasse, Reporter oder Akzeptanztest
   nicht verändern.
5. Die native Konvertierung erzeugt direkt denselben `CodexContractResult`,
   den die heutige Workflowlogik verarbeitet. Vertragsregeln aus
   `CodexStepContract` – erwartete Readinessart, Testdateien, Sliceplan,
   Findingvollständigkeit und Stop-Ausschließlichkeit – bleiben vollständig
   erhalten.
6. Whitespace-only-Felder, unbekannte Eigenschaften, falsche Resultatart,
   doppelte Pfade/Findings, fremde Findings und unvollständige Antworten
   scheitern mit stabilen nativen Fehlercodes. Rohe `ValueError`-Ausnahmen
   dürfen die Runtimegrenze nicht verlassen.

### 3.3 Provider- und Runtimegrenze

1. Eine eigene immutable `NativeCodexAdapter`-Instanz verwendet `codex exec
   --output-schema` mit dem echten geschlossenen Responseschema. Der
   kanonische Request wird über stdin übertragen; das Responseschema ist eine
   eigene gemessene Providerinput-Komponente.
2. Die letzte Modellnachricht muss genau ein JSON-Objekt enthalten. Markdown,
   Prosa außerhalb des Objekts oder eine Text-in-JSON-Hülle werden abgewiesen.
3. Die lokale Extraktion validiert das Resultat nach dem Provider nochmals
   gegen dasselbe vollständige Schema und gegen die gebundene Request-ID.
4. Der native Runtimepfad verwendet Capability-, Budget-, Sandbox-,
   Providerinput- und Providerattempt-Grenzen des bestehenden Pfads, aber
   weder Done-Markerprüfung noch Flagparser noch textuellen Contract-Repair.
5. Eine schema- oder domänenungültige Antwort ist genau ein typisierter
   `output`-Fehler. Es gibt in diesem Paket keinen automatischen nativen
   Vollauftrag-Retry und keinen Legacyfallback.
6. Die kanonische vollständige Response wird unmittelbar nach lokaler
   Schemavalidierung in das attemptgebundene Rohantwortartefakt geschrieben.
   Erst nach erfolgreichem Schreiben dürfen AgentResult-, Finding-, State-
   oder Checkpoint-Seiteneffekte folgen.

### 3.4 Persistenz, State und Recovery

1. `ProtocolBinding` erhält optional und rückwärtskompatibel
   `codex_transport=native-codex-v1`. Die Bindung wird nur bei einem neuen
   `structured-v1`-Lauf gesetzt und beim Resume wertgleich verlangt.
2. `AgentResultPayload` erhält optionale Felder für Transportschema,
   Request-ID und SHA-256 der kanonischen Antwort. Für native Codex-Ergebnisse
   sind alle drei gemeinsam Pflicht; historische Records ohne diese Felder
   bleiben lesbar.
3. Die persistierte Findingantwortfolge bleibt reviewer-owned: Codex darf nur
   eigene Dispositionen append-only ergänzen. Wiederholung desselben Requests
   und Ergebnisses ist ein No-op; abweichende Antwortbytes unter derselben
   Request-ID halten fail-closed.
4. Nach einem Crash zwischen Rohantwort-, Record- und Statefortschreibung baut
   Recovery denselben Request erneut, findet genau einen request-, response-
   und fingerprintgebundenen AgentResult-Record samt Rohantwortartefakt,
   validiert ihn nativ und spiegelt ihn ohne zweiten Providerstart.
5. Die Recoveryinvariante gilt für Plan, Implementierung, Korrektur und
   Abschluss. Mehrdeutige Records, fehlende Logs, abweichende Digests oder
   unpassende Kontextbindungen halten resumierbar und fail-closed an.
6. Der Watch-Default und historische Resumes bleiben auf dem Textadapter. Ein
   widersprüchliches Pilotflag bei einem bestehenden State wird abgewiesen.

## 4. Umsetzungsslices

### Slice 1 - Native Codex-Verträge und Providergrenze

Ziel ist ein vollständig providerunabhängig testbarer Request-/Resultatvertrag
sowie eine echte `--output-schema`-gebundene Codex-Adapter- und Runtimegrenze.
Der Slice ändert noch keine Workflowentscheidung und keine persistierte
Transportbindung.

**Exakter Änderungspfad**

- `schemas/native-agent-codex-request-v1.schema.json`
- `schemas/native-agent-codex-result-v1.schema.json`
- `src/native_codex_request.py`
- `src/native_codex_contract.py`
- `src/agent_adapters.py`
- `src/agent_runtime.py`
- `src/provider_input_budget.py`
- `tests/test_native_codex_request.py`
- `tests/test_native_codex_contract.py`
- `tests/test_agent_adapters.py`
- `tests/test_agent_runtime.py`
- `tests/test_provider_input_budget.py`

#### Akzeptanzkriterien

- Alle vier Auftragsarten erzeugen deterministisch dieselbe Request-ID bei
   semantisch identischem Input und unterschiedliche IDs bei jeder gebundenen
   Änderung.
- Alle fünf Resultatarten werden schema- und domänenvalidiert direkt in
   `CodexContractResult` konvertiert.
- Plan-, Implementierungs-, Korrektur- und Abschlussregeln des vorhandenen
   `CodexStepContract` sind durch positive und negative Tests abgedeckt.
- Fremde Request-ID, falsche Resultatart, Whitespace, Duplikate, unbekannte
   Felder und Finding-Manipulationen halten mit stabilen Codes fail-closed.
- Der native Adapter verwendet nachweislich `--output-schema`, misst Request
   und Schema getrennt und akzeptiert nur das native JSON-Objekt.
- Der native Runtimepfad ruft keinen Textmarkerparser und keinen textuellen
   Reparaturprompt auf.
- Das Rohantwortartefakt wird nach Schema-/Requestvalidierung, aber vor einem
   akzeptierten Outputcallback und vor dem erfolgreichen Providerattempt-
   Abschluss geschrieben. Ein Schreibfehler verhindert jede weitere
   Ergebnisfortschreibung.
- Der Legacy-Codex-Adapter und seine bisherigen Tests bleiben unverändert
   funktionsfähig.

### Slice 2 - Workflowbindung, Persistenz und Resume

Ziel ist der explizite Pilotpfad durch Planung, Implementierung, bestehende
Korrekturschritte und Codex-Abschlussbericht. Der bestehende fachliche
Workflow wird nicht neu geschnitten; nur Transport, native Konvertierung und
Recovery werden ergänzt.

**Exakter Änderungspfad**

- `README.md`
- `schemas/orchestrator-artifact-v1.schema.json`
- `src/artifact_bridge.py`
- `src/artifact_models.py`
- `src/cli.py`
- `src/orchestrator.py`
- `src/workflow.py`
- `src/workflow_state.py`
- `tests/test_artifact_bridge.py`
- `tests/test_artifact_models.py`
- `tests/test_cli.py`
- `tests/test_orchestrator_runtime.py`
- `tests/test_structured_artifact_regressions.py`
- `tests/test_workflow.py`
- `tests/test_workflow_state.py`

#### Akzeptanzkriterien

- Ein explizites CLI-Pilotflag setzt `native-codex-v1` nur bei einem neuen
   `structured-v1`-Lauf; Default, Watch und historische States bleiben legacy.
- Planung, Implementierung, Korrektur und Abschluss bauen jeweils einen
   vollständig gebundenen nativen Request und verarbeiten ausschließlich das
   native Resultat.
- Sliceplan, Readiness, Testdateien, Findingantworten und Stopdaten erzeugen
   dieselben fachlichen Stateübergänge wie der bisherige Textpfad.
- Der Orchestrator übernimmt Work Unit, Slice, Runde, Fingerprint und
   Attestierung aus seinem Kontext und nie aus ungebundenen Responsefeldern.
- Native AgentResult-Records binden Transport, Request-ID, Response-Digest,
   Operation, Work Unit und Fingerprint; historische Records bleiben gültig.
- Ein injizierter Crash nach Rohantwort beziehungsweise nach AgentResult-
   Persistenz und vor dem State-Checkpoint wird für jede Codex-Auftragsart
   ohne zweiten Providerstart wiederaufgenommen.
- Fehlende, doppelte oder widersprüchliche Responseartefakte und Records
   halten fail-closed; weder Findingantworten noch Sliceplan werden doppelt
   angewendet.
- Ein nativ gebundener Vertragsfehler fällt niemals auf Markertext zurück.
- README und CLI-Hilfe beschreiben Pilotstatus, Defaultverhalten,
   Resumebindung und den fehlenden Fallback eindeutig.
- `python3 -m pytest tests/ -v` und `git diff --check` bestehen.

## 5. Review- und Validierungsstrategie

1. Vor Slice 1 erfolgt ein direkter Claude-Planreview mit Sonnet und Effort
   `high`, sobald das Claude-Kontingent wieder verfügbar ist.
2. Nach jedem Slice erhält Claude den eingefrorenen Fingerprint, den exakten
   Diff, die relevanten Verträge und fokussierte Testergebnisse. Antigravity
   bleibt im manuellen Bootstrapprozess `NOT_RUN`.
3. Nur der Orchestrator beziehungsweise der lokale Codex-Hauptprozess führt
   die vollständige Suite aus. Claude führt keine Vollmatrix aus.
4. Codex dokumentiert eigene branchweite Prüfungen als Selbstcheck und nicht
   als Eigenfreigabe.
5. Die endgültige Cutover-Entscheidung gehört nicht in dieses Paket. Erst
   Arbeitspaket 5 verbindet native Codex-Resultate und native Claude-Reviews
   zu einer vollständig strukturierten Konvergenzschleife.

## 6. Nichtziele

- Keine Änderung der reviewer-owned Findingautorität.
- Keine neue Anzahl oder Reihenfolge von Korrekturrunden.
- Kein nativer Antigravity-Transport.
- Kein allgemeiner Defaultmodus `native-agent-json-v1`.
- Kein Entfernen der Legacyparser oder historischen Marker.
- Kein semantischer Evidence-Builder über die für Codex-Aufträge benötigte
  gebundene Evidenzhülle hinaus.
- Kein Commit, Merge oder Branchwechsel durch Codex in diesem Planungsschritt.
