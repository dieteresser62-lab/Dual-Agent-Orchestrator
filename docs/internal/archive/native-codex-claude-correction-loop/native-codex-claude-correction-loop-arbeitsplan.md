# Phase 2 – Geschlossene native Codex–Claude-Korrekturschleife

TARGET_BRANCH: feature/native-codex-claude-correction-loop

BASE_COMMIT: 04487883d560f7082b96902263bb890c0ec83655

STATUS: IMPLEMENTED_SLICE_2_APPROVED_CODEX_FINAL_CHECK_COMPLETE

## 1. Auftrag und Abgrenzung

Dieses Arbeitspaket setzt Arbeitspaket 5 aus der
[`ORCHESTRATOR_ROADMAP_PHASE_2_PLUS.md`](../../ORCHESTRATOR_ROADMAP_PHASE_2_PLUS.md)
um. Die bereits getrennt verfügbaren Pilotpfade `native-claude-review-v1` und
`native-codex-v1` werden zu einer vollständig strukturierten, vom
Orchestrator vermittelten Korrekturschleife verbunden:

1. Codex liefert ein natives Planungs-, Implementierungs- oder
   Abschlussresultat.
2. Claude eröffnet oder aktualisiert ein Finding ausschließlich in einem
   nativen Reviewresultat.
3. Der Orchestrator persistiert diese Entscheidung vor jedem Folgeschritt und
   baut aus dem autoritativen Recordstand genau einen nativen
   Codex-Korrekturauftrag.
4. Codex beantwortet jedes offene Finding mit einer strukturierten
   Disposition, ohne Status, Klasse, Reporter oder Akzeptanztest verändern zu
   können.
5. Nach Validierung des korrigierten Fingerprints entscheidet Claude in einem
   neuen nativen Review über Schließung oder Eskalation.

Der Orchestrator bleibt die einzige Kommunikations- und Autoritätsgrenze.
Codex und Claude kommunizieren nicht direkt und übernehmen keine
orchestrator-eigenen Identitäten aus Modellantworten.

Der Textmarkerpfad bleibt für historische und nicht nativ gebundene Läufe
lesbar. Ein nativ gebundener Lauf fällt bei Schema-, Kontext-, Persistenz- oder
Recoveryfehlern niemals auf Markertext zurück. Die beiden vorhandenen
Pilotflags bleiben erhalten; dieses Paket führt noch keinen allgemeinen
`native-agent-json-v1`-Default und keinen Protokollwechsel bestehender Runs
ein.

Antigravity ist nicht Teil dieses Arbeitspakets und wird im manuellen
Bootstrapprozess nicht aufgerufen. Die reguläre Produktivregel, nach der eine
vollständige Workflowfreigabe weiterhin Antigravity benötigt, wird dadurch
nicht gelockert. Das Paket endet fachlich am nachweislich konvergierten
Codex–Claude-Übergang beziehungsweise am nächsten regulären
Antigravity-Schritt.

Dieser Plan wurde am 22. August 2026 außerhalb von `run_task` erstellt. Vor
jeder Implementierung ist ein direkter Claude-Planreview mit Sonnet und
Effort `high` erforderlich. Bis zur Wiederverfügbarkeit des Claude-Kontingents
bleibt der Plan ungeprüft; aus diesem Dokument folgt noch keine
Implementierungsfreigabe.

## 2. Repositorybefund

- `ProtocolBinding` kann `claude_review_transport=native-claude-review-v1`
  und `codex_result_transport=native-codex-v1` bereits gleichzeitig
  persistieren. Die beiden Bindungen werden bislang jedoch überwiegend als
  unabhängige Vertikalpfade getestet; ein vollständiger gemeinsamer
  Denial–Correction–Convergence-Durchstich fehlt.
- `WorkflowEngine._run_codex()` baut native Codex-Aufträge für Planung,
  Implementierung, reguläre Korrektur und Finalkorrektur. `_run_review()` baut
  unabhängig davon native Claude-Aufträge. Beide Wege konvertieren bereits in
  dieselben Domänentypen wie der Legacyworkflow.
- `native-agent-codex-request-v1` transportiert offene Findings typisiert und
  bindet sie über die kanonische Request-ID. `native-agent-review-request-v1`
  transportiert die vollständigen Findingdaten einschließlich vorhandener
  Codex-Antworten.
- `correction_result` und `final_report_result` können Finding-Dispositionen
  liefern. `plan_result` enthält derzeit dagegen nur Readiness und Sliceplan.
  Eine native `CODEX_PLAN_REVISION` kann deshalb die offenen Findings aus einem
  ablehnenden Claude-Planreview nicht strukturiert beantworten, obwohl der
  Legacyvertrag genau diese Vollständigkeit verlangt. Das ist der erste
  konkrete Vertrags-Gap dieses Pakets.
- `ReviewPayload`, `AgentResultPayload` und `FindingTransitionPayload` bilden
  die notwendige append-only Semantik bereits ab: Claude eröffnet, schließt
  oder reklassifiziert eigene Findings; Codex darf ausschließlich eine
  Antwort auf ein offenes Finding ergänzen.
- Die nativen Providerpfade schreiben kanonische Rohantworten, binden
  Request-ID und Response-Digest und besitzen jeweils eine Record-ahead-
  Recovery. Getestet sind einzelne Claude- beziehungsweise Codexergebnisse,
  aber nicht alle Crashgrenzen einer zusammenhängenden Korrekturschleife.
- Die bestehenden Workflowtests beweisen die fachliche Textpfad-Konvergenz
  über mehrere Korrekturrunden. Die nativen Tests beweisen bislang vor allem,
  dass jeder einzelne Adapter den Markerparser umgeht. Ein Test, der beide
  nativen Transportbindungen gleichzeitig aktiviert und jeden Legacyparser
  als verbotenen Aufruf instrumentiert, fehlt.
- Planung, Slice-Konvergenz und branchweite Finalkorrektur besitzen
  unterschiedliche Stateübergänge. Arbeitspaket 5 darf deshalb nicht nur den
  einfachen Slice-Fall testen.
- Die Markdownprojektion zeigt native Transport-, Request- und
  Response-Digests bereits pro Record. Eine kompakte, allein aus der
  Recordkette erzeugte Darstellung der zusammengehörigen
  Review–Disposition–Konvergenzrunden fehlt noch.

## 3. Zielarchitektur und verbindliche Invarianten

### 3.1 Gemeinsame native Pilotbindung

1. Ein geschlossener Pilotlauf verwendet gleichzeitig
   `claude_review_transport=native-claude-review-v1` und
   `codex_result_transport=native-codex-v1` innerhalb von `structured-v1`.
2. Die vorhandenen Einzelpilotbindungen bleiben für historische Tests und
   kontrollierte Diagnosezwecke lesbar. Für einen als geschlossene
   Codex–Claude-Schleife gestarteten Akzeptanzlauf sind jedoch beide Bindungen
   verpflichtend und auf Resume unveränderlich.
3. Es wird kein dritter, semantisch überlappender Transportmodus eingeführt.
   Die Kombination der beiden existierenden immutable Bindungen ist die
   Pilotidentität dieses Pakets.
4. Ein widersprüchlicher Resumeaufruf, ein nur teilweise gebundener
   Akzeptanzlauf oder ein Versuch, einen nativen Transport nachträglich
   abzuschalten, hält vor dem nächsten Providerstart fail-closed an.

### 3.2 Reviewer-owned Findingautorität

1. Der für jeden Finding-beantwortenden Codex-Auftrag verwendete Findingstand
   entspricht exakt dem erfolgreich replayten Recordstand desselben Runs und
   Work Units. Das gilt ausdrücklich sowohl für
   `CODEX_PLAN_REVISION`/`NativeCodexRequestKind.PLAN` als auch für
   `CODEX_CORRECTION` und `CODEX_FINAL_CORRECTION`/
   `NativeCodexRequestKind.CORRECTION`. Ein bloß abweichender State-v3-Spiegel
   darf keinen Auftrag erzeugen.
2. Ein Claude-Finding ist erst folgewirksam, nachdem native Rohantwort,
   `ReviewPayload` und alle zugehörigen `FindingTransitionPayload`-Records
   vollständig und widerspruchsfrei persistiert sind.
3. Der native Codex-Auftrag enthält genau die für die aktuelle Runde offenen,
   korrekturelevanten Findings. Sortierung, Eindeutigkeit, Reporter,
   Findingklasse, Zusammenfassung und Akzeptanztest fließen in die Request-ID
   ein.
4. Codex muss zu jedem im Vertrag verlangten offenen Finding genau eine
   `accepted`- oder `rejected`-Disposition liefern. Codex darf kein Finding
   eröffnen, schließen, reklassifizieren oder einem anderen Reporter
   zuordnen.
5. Eine Codex-Disposition ist erst folgewirksam, nachdem native Rohantwort,
   `AgentResultPayload` und die zugehörigen `responded`-Transitions vollständig
   persistiert sind.
6. Der folgende Claude-Auftrag enthält den korrigierten Fingerprint, die dazu
   passende autoritative Validierungsattestierung und exakt die persistierten
   Codex-Dispositionen. Claude darf nur eigene Findings schließen oder
   eskalieren.
7. Ein positives Konvergenzreview ist unzulässig, solange ein
   reviewer-eigener Blocker offen ist oder eine geforderte Codex-Disposition
   fehlt.

### 3.3 Plan-, Slice- und Finalkonvergenz

1. `plan_result` erhält eine strukturierte Finding-Dispositionenliste. Beim
   ersten Planlauf ist sie leer; bei `CODEX_PLAN_REVISION` ist sie für alle
   korrekturrelevanten offenen Claude-Findings vollständig.
2. Die Ergänzung ist Teil derselben Schema- und Resultatversion und muss für
   bereits persistierte native Planresultate rückwärtskompatibel lesbar sein.
   Ein historisches `plan_result` ohne `finding_dispositions` wird beim Lesen
   deterministisch als leere Dispositionenfolge interpretiert. Ein neu
   erzeugtes Resultat schreibt das Feld immer explizit; sobald der gebundene
   Kontext offene Findings enthält, bleibt vollständige Dispositionierung
   zwingend.
   Falls dies mit dem geschlossenen v1-Vertrag nicht ohne semantische
   Mehrdeutigkeit möglich ist, wird nicht stillschweigend gelockert, sondern
   vor der Implementierung eine explizite v2-Entscheidung verlangt.
   Die v1-Entscheidung ist nur zulässig, wenn vor der Schemaänderung drei
   Nachweise festgehalten werden: Alle vorhandenen Fixtures und historischen
   `plan_result`-Dokumente behalten ihr Validierungsergebnis; kein Codepfad
   deutet `schema_version=native-agent-codex-result-v1` als erschöpfende
   Feldmengenkennung; und die kontextabhängige Dispositionspflicht liegt in
   Domänenvertrag beziehungsweise Workflow, nicht in einem neuen
   schemaweiten `required`-Eintrag.
3. Slice-Korrekturen verwenden weiterhin `correction_result`; eine
   Codex-Disposition schließt das Finding nicht. Erst das native
   Claude-Konvergenzreview darf den Status ändern.
4. Nach einer ablehnenden branchweiten Claude-Entscheidung entsteht weiterhin
   eine reguläre, commitgebundene Finalkorrektur-Work-Unit. Nach deren
   Konvergenz wird der vollständige native Codex-Abschlussbericht und das
   vollständige native Claude-Finalreview für den neuen Fingerprint erneut
   ausgeführt.
5. Die drei Varianten verwenden denselben Finding-Lifecycle und dieselben
   Persistenzregeln; es entstehen keine plan-, slice- oder finalspezifischen
   Schattenmodelle.

### 3.4 Genau-einmal- und Recoveryvertrag

1. Request-ID und Response-Digest identifizieren jeden nativen Provideraufruf
   unveränderlich. Gleicher Request plus gleiche Antwort ist bei Wiederholung
   ein No-op; gleicher Request plus andere Antwort hält fail-closed an.
2. Ein Crash nach Rohantwort, nach Ergebnisrecord, zwischen mehreren
   Findingtransitions oder vor dem State-Checkpoint wird ausschließlich aus
   der Recordkette und dem gebundenen Rohartefakt repariert.
3. Resume startet weder Claude noch Codex erneut, wenn deren vollständiges
   requestgebundenes Ergebnis bereits autoritativ vorliegt.
4. Wiederholte Zustellung erzeugt weder doppelte Findings noch doppelte
   Dispositionen, Korrektur-Work-Units, Validierungen oder
   Reviewerentscheidungen.
5. Ein unvollständiger Übergang wird in deterministischer Reihenfolge
   vervollständigt. Mehrdeutige, fremde, vorwärtsreferenzierende oder
   fingerprintfremde Records halten fail-closed an.
6. Quota- und technische Providerpausen verändern weder Requestinhalt noch
   Findingeigentum. Nach Wiederaufnahme gilt derselbe genau-einmal-Vertrag.

### 3.5 Keine Textparser- oder Autoritätsabkürzung

1. Bei gleichzeitig nativer Bindung werden weder
   `validate_codex_response()`, `parse_contract_result()` noch eine
   markerbasierte Reparaturfunktion aufgerufen.
2. Fehlerprosa, Logs oder Markdownprojektionen sind niemals Eingabe für eine
   Workflowentscheidung.
3. Der Orchestrator übernimmt Run, Work Unit, Rolle, Operation, Fingerprint,
   Attestierung und autorisierte Pfade ausschließlich aus gebundenem Kontext
   und Recordkette.
4. Markdown bleibt eine deterministische, jederzeit neu erzeugbare Ansicht
   der JSON-Records. Es wird nicht zurückgelesen und nicht als Recoveryquelle
   verwendet.

## 4. Umsetzungsslices

### Slice 1 - Native Finding-Dispositionen und autoritative Übergabekette

Ziel ist die vollständige Domänen- und Requestkette für Planrevision,
Slice-Korrektur und Finalkorrektur. Der Slice schließt zuerst den fehlenden
`plan_result`-Vertrag und erzwingt anschließend, dass jeder Folgeauftrag aus
dem autoritativen strukturierten Findingstand gebaut wird.

**Exakter Änderungspfad**

- `schemas/native-agent-codex-result-v1.schema.json`
- `schemas/orchestrator-artifact-v1.schema.json`
- `src/artifact_models.py`
- `src/native_codex_contract.py`
- `src/native_codex_request.py`
- `src/native_review_request.py`
- `src/workflow.py`
- `src/workflow_state.py`
- `src/orchestrator.py`
- `src/artifact_bridge.py`
- `src/artifact_replay.py`
- `tests/test_native_codex_contract.py`
- `tests/test_artifact_models.py`
- `tests/test_native_codex_request.py`
- `tests/test_native_review_request.py`
- `tests/test_workflow.py`
- `tests/test_workflow_state.py`
- `tests/test_orchestrator_runtime.py`
- `tests/test_artifact_bridge.py`
- `tests/test_artifact_replay.py`

#### Arbeitsschritte

1. `plan_result` um die für den jeweiligen `CodexStepContract` gebundene
   Finding-Dispositionenliste ergänzen und alte native Planresultate eindeutig
   behandeln.
2. Parser und Domänenkonvertierung so vereinheitlichen, dass Planrevision,
   Implementierungskorrektur und Finalkorrektur dieselbe
   Vollständigkeitsprüfung für offene Findings verwenden.
3. Den gemeinsamen Pilotmodus aus beiden vorhandenen Transportbindungen
   ableiten und vor jedem Codex- oder Claude-Aufruf gegen State und Recordkette
   prüfen.
4. Vor dem Aufbau jedes Finding-beantwortenden nativen Codex-Auftrags den
   Findingstand aus dem strukturierten Replay abgleichen. Der Guard gilt auch
   für `CODEX_PLAN_REVISION`, obwohl diese Operation als
   `NativeCodexRequestKind.PLAN` transportiert wird. Abweichende
   State-Mirror-Fakten dürfen weder still repariert noch als Promptinhalt
   verwendet werden.
5. Native Codex-Dispositionen append-only als `responded`-Transitions
   persistieren und die folgende native Claude-Request-ID über den
   vollständigen aktualisierten Findingstand binden.
6. Plan-, Slice- und Finalübergänge auf dieselben Ownership-, Reihenfolge- und
   Fingerprintregeln führen; vorhandene Legacyübergänge bleiben lesbar.
7. Den bestehenden `FindingTransitionPayload` ohne neuen Recordtyp um optionale,
   strukturierte Opening- und Dispositionsfelder ergänzen. Der kombinierte
   native Pfad verlangt diese Felder; historische Records ohne diese Felder
   bleiben über den allgemeinen Replaypfad lesbar, dürfen aber keinen neuen
   nativen Auftrag autorisieren.

Die drei zusätzlich aufgenommenen Pfade
`schemas/orchestrator-artifact-v1.schema.json`, `src/artifact_models.py` und
`tests/test_artifact_models.py` sind eine repository-grounded Scopeerweiterung:
Ohne sie enthält die autoritative Recordkette weder Akzeptanztest und Ursprung
eines Findings noch die Codex-Disposition als typisiertes Feld. Ein Aufbau des
nächsten Requests ausschließlich aus Records wäre sonst unmöglich und würde
weiterhin Freitextparsing oder den State-Spiegel benötigen.

#### Akzeptanzkriterien

- Ein natives `plan_result` ohne offene Findings verwendet eine leere
  Dispositionenliste. Ein historisches Resultat ohne das neue Feld wird als
  dieselbe leere Folge gelesen; jedes neu erzeugte Resultat enthält das Feld
  explizit. Eine Planrevision mit offenen Findings beantwortet jede geforderte
  ID genau einmal.
- Vor der v1-Schemaänderung belegen Fixture-/Historientests unveränderte
  Validierungsergebnisse, eine Repositorysuche schließt eine Verwendung der
  Schema-ID als erschöpfende Feldmengenkennung aus, und ein Domänentest zeigt,
  dass nur der gebundene offene Findingkontext die Dispositionspflicht
  aktiviert. Scheitert einer dieser Nachweise, wird Slice 1 vor einer
  Schemaänderung angehalten und auf eine explizite v2-Planentscheidung
  zurückgeführt.
- Fehlende, doppelte, fremde oder nicht offene Finding-Dispositionen sowie
  Versuche, Status oder Klasse über Codex zu verändern, scheitern mit stabilen
  nativen Fehlercodes.
- Ein nativer Claude-Denialrecord und seine Findingtransitions liegen
  vollständig vor, bevor der Codex-Korrekturrequest gebaut wird.
- Der Codex-Korrekturrequest enthält exakt den replayten offenen Findingstand;
  eine Abweichung zwischen State und Recordkette stoppt vor dem Providerstart.
- Derselbe Divergenztest stoppt eine `CODEX_PLAN_REVISION` und eine
  `CODEX_CORRECTION` jeweils vor dem Providerstart; die PLAN-Klassifikation der
  Revision darf den Replayguard nicht umgehen.
- Der folgende Claude-Request enthält exakt die persistierte
  Codex-Disposition und die Validierungsattestierung des korrigierten
  Fingerprints.
- Claude kann sein Finding schließen oder offen halten; Codex kann dies nicht.
- Gemischte historische Pilotbindungen bleiben lesbar, aber der geschlossene
  Akzeptanzpfad verlangt beide nativen Bindungen unverändert.
- Alle fokussierten Vertrags-, Replay-, Bridge-, State- und Workflowtests
  bestehen; `git diff --check` ist sauber.

### Slice 2 - End-to-End-Konvergenz, Fault-Injection und lesbare Projektion

Ziel ist der belastbare Nachweis des vollständigen nativen Nutzpfads über
Planung, Slice und branchweiten Abschluss. Der Slice ergänzt keine
Antigravity-Freigabe und ändert den Defaulttransport nicht.

**Exakter Änderungspfad**

- `README.md`
- `docs/internal/native-agent-json-manueller-bootstrap.md`
- `src/artifact_projection.py`
- `src/audit_trail.py`
- `src/orchestrator.py`
- `src/workflow.py`
- `tests/test_artifact_projection.py`
- `tests/test_audit_trail.py`
- `tests/test_cli.py`
- `tests/test_dry_run_scenarios.py`
- `tests/test_orchestrator_runtime.py`
- `tests/test_structured_artifact_regressions.py`
- `tests/test_workflow.py`

#### Arbeitsschritte

1. Einen kombinierten nativen Workflowtest aufbauen, der Textparser und
   textuelle Contract-Repairfunktionen durch absichtliche Fehlerstubs ersetzt
   und trotzdem die Folge
   `Codex → Claude-Denial → Codex-Korrektur → Claude-Schließung` durchläuft.
2. Dieselbe Konvergenz für eine abgelehnte Planrevision und für die
   branchweite Finalkorrektur abdecken. Nach der Finalkorrektur müssen
   Codex-Abschlussbericht und Claude-Finalreview für den neuen Fingerprint neu
   gebunden werden.
3. Fault-Injection an jeder Record-ahead-Grenze ergänzen: native Rohantwort,
   Ergebnisrecord, einzelne Findingtransition, Validierungsattestierung und
   State-Checkpoint.
4. Resume- und Quota-Szenarien nachweisen, die vorhandene Ergebnisse
   wiederverwenden und keine zusätzlichen Providerstarts oder semantischen
   Doppelrecords erzeugen.
5. Die Markdownprojektion um eine kompakte Konvergenzübersicht aus
   Work Unit, Runde, Fingerprint, Finding-ID, Claude-Entscheidung,
   Codex-Disposition und abschließendem Findingstatus ergänzen. Request- und
   Response-Digests bleiben sichtbar; Rohinhalte und Geheimnisse werden nicht
   dupliziert.
6. README und manuellen Bootstrapvertrag um den gemeinsamen Pilotaufruf, seine
   unveränderliche Resume-Bindung, den fehlenden Textfallback und die weiterhin
   ausstehende Antigravity-Integration ergänzen.

#### Akzeptanzkriterien

- Ein kombinierter Pilotlauf aktiviert beide nativen Bindungen und ruft in
  keiner Plan-, Implementierungs-, Korrektur- oder Reviewphase einen
  Textmarkerparser auf.
- Ein einfacher Slice-Denial erzeugt genau ein Claude-Finding, genau eine
  Codex-Disposition und genau eine abschließende Claude-Statusentscheidung.
- Zwei aufeinanderfolgende Korrekturrunden bleiben eindeutig nummeriert und
  erzeugen weder eine zweite Finding-ID noch doppelte Antworten.
- Eine Planablehnung konvergiert über ein natives `plan_result` mit
  Dispositionen und einen neuen nativen Claude-Planreview.
- Eine Finalablehnung erzeugt eine reguläre commitgebundene Korrektur-Work-
  Unit; danach werden vollständiger Codex-Finalreport und vollständiges
  Claude-Finalreview für den neuen Fingerprint erneut ausgeführt.
- Jeder definierte Crashpunkt lässt sich ohne zweiten Aufruf des bereits
  erfolgreich antwortenden Providers fortsetzen.
- Quota-Warten und manuelles Resume verändern weder Request-ID noch
  Findingfolge; die Providerattempt-Zahl entspricht den tatsächlich gestarteten
  Prozessen.
- Fremde, doppelte, unvollständige oder anders gebundene Records stoppen
  reproduzierbar vor einer weiteren Workflowentscheidung.
- Die Markdownansicht lässt sich ausschließlich aus der Recordkette identisch
  neu erzeugen und wird in keinem Test als Entscheidungsquelle verwendet.
- Der kombinierte Pilot endet nach Claudes positiver Konvergenz am regulären
  nächsten Antigravity-Schritt; er erklärt den Gesamtworkflow nicht ohne
  Antigravity für produktiv freigegeben.
- `python3 -m pytest tests/ -v` und `git diff --check` bestehen.

## 5. Verbindliche Testszenarien

Mindestens folgende Szenarien müssen explizit und ohne freien Modelltext
abgedeckt sein:

1. Native Planung ohne Findings und positive Claude-Freigabe.
2. Native Planablehnung, Codex-Planrevision mit vollständigen Dispositionen
   und Claude-Schließung.
3. Native Sliceimplementierung, Claude-Blocker, Codex-Korrektur und
   Claude-Schließung.
4. Codex lehnt ein Finding begründet ab; Claude hält es offen und verweigert
   die Freigabe.
5. Zweite Korrekturrunde für dieselbe Finding-ID ohne doppelte
   Findingtransition.
6. Branchweites Claude-Finaldenial, commitgebundene Finalkorrektur,
   wiederholter nativer Codex-Finalreport und positive Claude-Neuentscheidung.
7. Crash nach Claude-Rohantwort, vor `ReviewPayload`.
8. Crash nach `ReviewPayload`, zwischen zwei Findingtransitions.
9. Crash nach Codex-Rohantwort, vor `AgentResultPayload`.
10. Crash nach `AgentResultPayload`, vor `responded`-Transition.
11. Crash nach vollständigen Transitions, vor State-Checkpoint.
12. Quota-Pause vor Claude beziehungsweise Codex und unveränderte
    Wiederaufnahme.
13. Gleiche Request-ID mit abweichenden Responsebytes.
14. State-Mirror hinter oder vor der autoritativen Recordkette, separat für
    `CODEX_PLAN_REVISION` (`NativeCodexRequestKind.PLAN`) und
    `CODEX_CORRECTION` (`NativeCodexRequestKind.CORRECTION`).
15. Historischer Textlauf sowie beide bisherigen Einzelpilotbindungen bleiben
    lesbar und resumierbar.
16. Harte Negativinstrumentierung: jeder Aufruf eines Legacyparsers lässt den
    kombinierten nativen Test sofort fehlschlagen.

## 6. Review- und Validierungsstrategie

1. Der nächste Schritt nach Fertigstellung dieses Dokuments ist ein direkter
   Claude-Planreview mit Sonnet und Effort `high`. Dieser Aufruf wird wegen des
   derzeit erschöpften beziehungsweise geschonten Claude-Kontingents nicht in
   diesem Arbeitsschritt ausgeführt.
2. Ein Claude-Planfinding wird zuerst in diesen Plan eingearbeitet und danach
   in einem begrenzten direkten Konvergenzreview erneut vorgelegt. Erst eine
   ausdrückliche Planfreigabe autorisiert Slice 1.
   Der direkte Review vom 23. August 2026 hat den Plan freigegeben und C-01
   sowie C-02 als Observations eröffnet; beide sind in Abschnitt 3, Slice 1
   und Testszenario 14 verbindlich eingearbeitet und müssen im Slice-1-Review
   geschlossen oder eskaliert werden.
3. Nach Slice 1 prüft Claude Vertragsvollständigkeit, Findingeigentum,
   Recordreihenfolge, Fingerprintbindung und fehlenden Textfallback.
4. Nach Slice 2 prüft Claude die vollständigen Fault-Injection- und
   End-to-End-Nachweise. Antigravity bleibt im manuellen Bootstrapprozess
   `NOT_RUN`.
5. Nur der lokale Hauptprozess führt `python3 -m pytest tests/ -v` aus.
   Claude erhält die vollständige Attestierung, startet aber nicht selbst die
   Gesamtmatrix.
6. Codex implementiert und dokumentiert, genehmigt aber seine eigene Arbeit
   nicht. Bis zur direkten Claude-Freigabe bleibt jeder Slice ungeprüft.

## 7. Stopbedingungen und Nichtziele

Die Arbeit hält an und verlangt eine neue Architekturentscheidung, wenn:

- die Ergänzung von `plan_result` eine inkompatible Umdeutung bereits
  persistierter v1-Antworten erfordern würde;
- ein neuer Recordtyp statt der vorhandenen Review-, AgentResult- und
  FindingTransition-Kette notwendig erscheint;
- ein Folgeauftrag nur aus State oder Markdown statt aus der autoritativen
  Recordkette gebaut werden könnte;
- ein nativer Vertragsfehler nur durch Textfallback oder freie
  Modellreparatur fortsetzbar wäre;
- der Codex–Claude-Pilot eine produktive Freigabe ohne Antigravity vortäuschen
  müsste;
- der allgemeine Defaultcutover, der semantische Evidence-Builder oder die
  native Antigravity-Anbindung vorgezogen werden müsste.

Nicht Teil dieses Pakets sind:

- native Antigravity-Requests oder -Resultate;
- Änderung der regulären Reviewerreihenfolge;
- allgemeiner `native-agent-json-v1`-Default;
- Abschaffung historischer Textadapter;
- der vollständige semantische Evidence-Builder aus Arbeitspaket 6;
- freie Agent-zu-Agent-Kommunikation;
- Push, Merge oder Releaseerstellung.

## 8. Gesamtabnahme

Arbeitspaket 5 ist abgeschlossen, wenn ein gemeinsam nativ gebundener Lauf für
Planung, Slice und Finalkorrektur nachweislich ohne Textmarker konvergiert,
alle Entscheidungen append-only und requestgebunden persistiert sind, jeder
definierte Crash- und Resumepunkt genau einmal fortgesetzt wird und die
Recordprojektion den vollständigen Korrekturverlauf deterministisch für
Menschen lesbar macht.

Erst danach ist Arbeitspaket 6 – der semantische Evidence-Builder – der nächste
offene Umsetzungsschritt.
