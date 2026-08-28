# Arbeitsplan: Planreview-Findings verlustfrei in den Implementierungslauf übergeben

TARGET_BRANCH: feature/plan-only-finding-carry-forward

## Ziel und Zuschnitt

Ein positiv abgeschlossenes `PLAN_ONLY`-Review darf offene Claude-
`OBSERVATION`-Findings behalten. Der anschließende, commitgebundene Wechsel in
den automatisch erzeugten `IMPLEMENT`-Lauf muss deren vollständigen nativen
Lebenszyklus aus der append-only Recordkette des Planlaufs übernehmen. Der neue
Lauf importiert diese Vorgeschichte mit ausdrücklich fremder Herkunft, führt
sie in Record-Replay und State-v3-Mirror fort und stellt offene Findings Codex
und Claude unverändert zur Verfügung. Geschlossene Findings bleiben als
Historie sichtbar, werden aber weder erneut geöffnet noch Codex zur Disposition
angeboten.

Die Lösung führt keinen Markdown-Parser für Findings ein. Der Arbeitsplan ist
weiterhin die freigegebene fachliche Quelle für Slices; Findingevidenz stammt
ausschließlich aus validierten `structured-v2`-Records. Ebenso gibt es keine
Migration historischer Protokolle, keine automatische Findingentscheidung und
keine Änderung der Reviewerhoheit oder Finalreviewpolicy. Die allgemeine
Dateizahlpolicy bleibt außerhalb dieses Pakets.

Diese `PLAN_ONLY`-Runde ändert ausschließlich dieses Dokument. Branch-, Stage-
und Commitoperationen sowie die vollständige Validierungsattestierung bleiben
beim Orchestrator beziehungsweise Nutzer.

## Repositorybefund und Fehlerkante

- `src/orchestrator.py::run_production_workflow()` beendet einen abgeschlossenen
  `PLAN_ONLY`-Plan nach `write_implementation_handoff()` und
  `persist_implementation_handoff()` unmittelbar. Die nachfolgende normale
  Work-Unit-Kante mit `carry_forward_native_findings()` wird deshalb nicht
  erreicht. Der erzeugte Task startet später über `_fresh_state()` eine neue
  Run-ID und damit eine neue `ArtifactStore`-Kette.
- `src/plan_handoff.py::render_implementation_task()` bindet heute Planpfad,
  Zielbranch, `APPROVED_PLAN_COMMIT`, Gesamtscope und `SLICE_PLAN`-Zeilen. Das
  Dokument enthält weder Quell-Run noch Recordhead oder einen strukturierten
  Findingbezug. `src/task_contract.py` kann folglich nur Plan und Slices prüfen.
- `ProductionWorkflowDriver.persist_implementation_handoff()` schreibt im
  Planlauf lediglich einen allgemeinen `BindingPayload` mit Handoffpfad,
  Attestierung und Approval-IDs. Der Record bindet weder Taskbytes noch die
  Findingtransitionen. Ein bestehender Handoff ist nur über Textgleichheit
  idempotent.
- `src/artifact_models.py` und
  `schemas/orchestrator-artifact-v2.schema.json` kennen
  `finding_transition`, aber keinen runübergreifenden Export oder Import.
  `src/artifact_replay.py::replay_findings()` reduziert ausschließlich
  Transitionen derselben Run-ID. Das ist die richtige lokale Autoritätsgrenze;
  sie darf nicht durch scheinbar lokal neu eröffnete Claude-Findings umgangen
  werden.
- `_fresh_state()` bindet einen freigegebenen Plan, schließt die lokale
  Plan-Work-Unit und startet Slice 1 mit leerem `runtime_history` und leerem
  `WorkUnitRecord.open_findings`. Der erste Checkpoint schreibt daher Task,
  Plan und Work-Unit, aber keine Findingvorgeschichte. Der Resumeabgleich in
  `src/artifact_migration.py` kann nur die lokale Kette gegen diesen leeren
  Mirror vergleichen.
- `src/native_codex_request.py` transportiert alle offenen Einträge aus
  `NativeCodexContext.previous_findings`; `src/native_review_request.py`
  transportiert die vollständigen `previous_findings`. Diese Providerverträge
  benötigen kein Finding aus Markdown. Sobald der neue Lauf eine korrekt
  importierte Historie besitzt, können beide bestehenden Builder Identität,
  Klasse, Reporter und Akzeptanztest weiterreichen.
- Das minimierte normale Slice-Paket aus
  `src/provider_input_efficiency.py::build_slice_execution_package()` enthält
  bisher ausschließlich Planziel, Akzeptanzkriterien, Pfade und Querverweise.
  Für eine offene, aus dem Planreview geerbte Verpflichtung braucht auch dieses
  inhaltsadressierte Paket einen deterministisch sortierten Findingabschnitt;
  andernfalls widersprechen sich kompakter Ausführungsvertrag und oberer
  nativer Requestkontext.
- `src/artifact_projection.py` projiziert lokale Findingtransitionen und
  Bindings, kennt aber keine Importherkunft. Deshalb kann die
  Implementierungsprojektion derzeit weder den Quell-Run noch die
  Handoffbindung erklären. Die Projektion bleibt nach der Reparatur reiner
  Consumer des Replays.
- `src/inbox_watcher.py` verarbeitet generierte Inbox-Dateien in einem späteren
  Queueumlauf und besitzt bereits stabile Watch-Identitäten, Success-Marker und
  Wiederanlaufgrenzen. Die neue Handofflogik muss vor der ersten
  Providerinvokation abgeschlossen sein und darf weder Watcherretry noch
  Queuefinalisierung als fachlichen Importersatz verwenden.

## Verbindliches Übergabeprotokoll und Invarianten

1. Der Planlauf lädt vor der Handoff-Erzeugung seine physisch validierte Kette,
   replayt sie für exakt seine Run-ID und vergleicht sie erneut mit Review-,
   Finding- und State-v3-Fakten. Exportfähig ist nur ein positiv entschiedener
   Claude-Planreview, dessen Approval, vollständige Attestierung und
   Plancommitbindung in derselben Kette liegen.
2. Für einen Plan mit Findinghistorie wird ein eigener geschlossener
   Handoff-Exportrecord eingeführt. Er bindet mindestens Quell-Run,
   präzisen Quell-Recordhead, freigegebenen Plancommit, den zustimmenden
   Reviewrecord, die geordneten Quell-`finding_transition`-Record-IDs, deren
   kanonischen Inhaltsdigest, Zieltaskpfad und SHA-256 der exakt zu erzeugenden
   Taskbytes. Der bestehende Commit-/Attestierungsbezug bleibt Teil derselben
   Kette. Der Export wird nicht aus State oder Projektion aufgebaut.
3. Das erzeugte IMPLEMENT-Dokument trägt ausschließlich eine maschinenlesbare
   Referenz auf diesen Exportrecord samt Quell-Run; Findingtexte werden nicht
   als autoritative Marker dupliziert. Der Taskdigest schließt diese Referenz
   ein, und der Exportrecord bindet wiederum den Taskdigest. Die Implementierung
   löst den Konstruktionszyklus deterministisch über die vorhersagbare stabile
   Recordidentität beziehungsweise eine äquivalente inhaltsadressierte
   Vorbereitungsphase; nach dem ersten dauerhaften Schritt dürfen Retries nur
   noch dieselben Bytes und denselben Record bestätigen.
4. Der neue Lauf akzeptiert den Import vor seinem ersten Checkpoint und vor
   jedem Provideraufruf nur dann, wenn Taskbytes, Exportrecord, Quellkette,
   Recordhead, Plancommit, Approval und alle referenzierten Transitionen
   vollständig übereinstimmen. Er persistiert genau einen typisierten
   Importrecord, dessen Nutzdaten die geordnete, kanonische Quellhistorie und
   deren Herkunftsbezüge enthalten. Dieser Record ist keine lokale
   Reviewerentscheidung; Replay wendet seine eingebetteten Quelltransitionen
   mit ursprünglichem Reporter, Actor, Ursprungsslice, Runde, Klasse, Status,
   Antwort und Akzeptanztest an.
5. Export- und Importidentität werden aus stabilen semantischen Bindungen
   abgeleitet, nicht aus Zeit oder Queue-Reihenfolge. Existierende gleiche
   Records und gleiche Taskbytes sind Erfolg; dieselbe Identität mit anderen
   Bytes oder Nutzdaten ist ein fail-closed Fehler. Der Import ist atomar als
   ein semantischer Record, damit ein Abbruch keine halbe Findinghistorie
   hinterlassen kann.
6. Alle Quelltransitionen werden in ursprünglicher Recordreihenfolge
   übernommen. Die Reihenfolge der Findingeröffnungen und die lexikalische
   Sortierung dienen nicht als Ersatz für Kettenreihenfolge. Replay erzwingt je
   Finding die bestehenden erlaubten Zustandsübergänge. Eine Schließung bleibt
   geschlossen, eine Codexantwort bleibt eine Antwort und eine spätere lokale
   Claude-Transition darf nur nach den bestehenden Reporterhoheitsregeln
   erfolgen.
7. Der Initialzustand des Implementierungslaufs übernimmt die vollständige
   Historie in `runtime_history` und die IDs der zu diesem Zeitpunkt offenen
   Findings in die erste Slice-Work-Unit. Lokales Artifact-Replay, Mirror und
   Work-Unit müssen denselben Satz und dieselbe Semantik belegen. Spätere
   Slice- und Finalreview-Grenzen verwenden weiter den vollständigen Ledger;
   sie dürfen ihn nicht auf lokal entstandene IDs reduzieren.
8. Codex erhält die offenen importierten Findings sowohl im vorhandenen
   nativen `open_findings`-Vertrag als auch im gebundenen kompakten
   Slice-Ausführungspaket. Claude erhält den vollständigen Lebenszyklus in
   `previous_findings`. Ein Codex-`finding_disposition` wirkt auf dasselbe
   importierte Finding, und nur eine nachfolgende Claude-Transition darf es
   schließen oder reklassifizieren.
9. Der branchweite Finalreview bleibt unverändert streng: Replay und Mirror
   müssen vor dem Request dieselbe vollständige Historie liefern, und eine
   positive Entscheidung bei irgendeinem offenen importierten `C-*`-Finding
   wird abgewiesen. Neue IDs werden oberhalb aller importierten IDs vergeben;
   ein Import kann deshalb weder einen lokalen Zusammenstoß noch einen
   künstlich neuen Findingdatensatz erzeugen.
10. Auf Resume wird die runübergreifende Herkunft erneut read-only validiert,
    bevor Record-ahead-Recovery oder Providerwiederaufnahme zulässig ist. Fehlt
    die Quellkette oder divergieren Task, Export, Head, Commit, Review oder
    Transition, bleibt die lokale Kette unverändert und der Lauf hält mit einer
    stabilen technischen Diagnose. Checkpoint oder `.orchestrator/state.json`
    werden niemals als Reparaturquelle verwendet.
11. Ein Plan ohne `finding_transition`-Records nutzt den bestehenden
    PLAN_ONLY-zu-IMPLEMENT-Weg ohne Findingexport und ohne Importrecord. Es
    entstehen keine leeren Findingcontainer, synthetischen Übergänge oder
    Pseudofindings; vorhandene Handoffkompatibilität bleibt erhalten.
12. Die recordnative Auditprojektion zeigt Export beziehungsweise Import mit
    Quell-Run, Quellhead, Plancommit, Reviewbindung und Findingrecordbezügen und
    stellt anschließend Antworten, Reklassifikationen und Schließungen als
    einen durchgehenden Lebenszyklus dar. Sie liest dafür ausschließlich den
    akzeptierten Replay und bleibt nicht autoritativ.

## Stop- und Abbruchregeln

- Falls der Quelllauf nicht aus seiner append-only Kette bis zu einem
  fingerprintgleichen positiven Planreview und Plancommit replaybar ist, wird
  kein Handoff erzeugt.
- Falls der Import nur durch Lesen der Arbeitsplanprojektion, von
  `.orchestrator/state.json`, eines Checkpoints oder eines frei formulierten
  Findingfelds möglich wäre, hält die Umsetzung an.
- Falls eine crash-sichere Reihenfolge nicht gewährleisten kann, dass genau ein
  semantischer Export, genau ein Task und genau ein atomarer Import entstehen,
  hält die Umsetzung vor einer Queue- oder Providernebenwirkung an.
- Falls ein importiertes Finding als neue lokale Claudeentscheidung modelliert
  oder Reporterhoheit, Klassenhistorie, Statusautomat beziehungsweise
  Mirror-/Replayabgleich gelockert werden müsste, hält die Umsetzung an.
- Historische `legacy-state-v3`- oder `structured-v1`-Runs werden nicht als
  Quelle nachgerüstet. Ein solcher Versuch bleibt `UNSUPPORTED-PROTOCOL`.

### Slice 1 - Typisiertes Export- und Importprotokoll im Artifact-Replay

#### Ziel

Die strukturierte Recordschicht kann einen Planreview-Findingexport und dessen
atomaren Import ausdrücken, schema- und domänenseitig validieren, ohne
Identitätsverlust replayen und verständlich projizieren. Dieser Slice verändert
noch keine Handoff- oder Workflowsteuerung.

**Exakter Änderungspfad**

- `schemas/orchestrator-artifact-v2.schema.json`
- `src/artifact_models.py`
- `src/artifact_bridge.py`
- `src/artifact_replay.py`
- `src/artifact_projection.py`
- `tests/test_artifact_models.py`
- `tests/test_artifact_bridge.py`
- `tests/test_artifact_replay.py`
- `tests/test_artifact_projection.py`

#### Umsetzung

1. Ergänze geschlossene v2-Recordtypen für den quellengebundenen
   Findinghandoff und den atomaren Findingimport. Definiere genaue Felder,
   Rollen, Digests, Gitobjekt- und Record-ID-Formate sowie Nichtleeregeln in
   Pythonmodell und JSON-Schema. Der Import trägt eine geordnete Folge
   kanonischer `FindingTransitionPayload`-Fakten plus deren ursprüngliche
   Record-IDs, nicht bloß aktuelle Finding-Snapshots.
2. Halte die Exportbindung auf Quell-Run, prä-Export-Head, Plancommit,
   zustimmenden Claude-Reviewrecord, Findingrecordfolge, Inhaltsdigest,
   Zieltaskpfad und Taskdigest fest. Halte im Import zusätzlich Exportrecord-ID,
   Ziel-Run/Taskdigest und dieselbe Quellenbindung fest. Unbekannte Felder,
   leere Folgen, doppelte Record- oder Findingtransitionidentitäten,
   unsortierte beziehungsweise digestfremde Nutzdaten und unzulässige Rollen
   werden vor Persistenz abgewiesen.
3. Ergänze im Bridge-Layer ausschließlich verlustfreie Konstruktoren aus einem
   bereits akzeptierten Replay. Sie müssen die kanonischen Quelldokumente
   hashen und dürfen keine Findingsemantik aus Text oder Mirror ableiten.
4. Erweitere `replay_artifacts()` und `replay_findings()` so, dass ein
   Importrecord seine Quellenfolge als fremde Vorgeschichte reduziert, während
   nachfolgende lokale `finding_transition`-Records denselben Automaten nutzen.
   Der Replay unterscheidet die Provenienz, erzwingt eindeutige Importe und
   verhindert Wiederöffnung, implizite Schließung, Reporterwechsel,
   Klassenhistorienverlust und doppelte Transitionen.
5. Erweitere den gewöhnlichen `WorkUnitPayload` im selben Modell-/Schemaschritt
   um den deterministisch sortierten, gegebenenfalls leeren Satz der beim
   Eintritt offenen Finding-IDs und eine optionale Importrecord-Referenz. Für
   bestehende Work-Units ohne Import bleiben die kanonischen Defaultwerte
   kompatibel; Replay kann damit im nächsten Slice die Attribution mit dem
   State-v3-`WorkUnitRecord` vergleichen, statt sie nur aus Runtimehistorie zu
   erraten.
6. Projiziere Export und Import als eigene Herkunftsereignisse. Zeige die
   Bindungswerte knapp und führe die importierten Findingtransitionen danach in
   der vorhandenen Findingtabelle fort; kein Projektionscode rekonstruiert oder
   mutiert Fakten.

#### Akzeptanzkriterien

- Schema-, Deserialisierungs- und Roundtriptests decken vollständige offene und
  geschlossene Lebenszyklen einschließlich Codexantwort und
  Beobachtung-Reklassifikation ab; alle Werte bleiben byte- beziehungsweise
  semantikgleich.
- Ein Import mehrerer Findings in anderer Eröffnungsreihenfolge replayt exakt
  in Quellrecordreihenfolge und erzeugt weder ID-Zusammenstoß noch eine zweite
  Eröffnung.
- Manipulierte Quell-Run-, Head-, Commit-, Review-, Record-ID-, Digest- oder
  Transitiondaten sowie doppelte und partielle Importe scheitern mit stabilem
  Replaydiagnosecode.
- Die Projektion nennt Quell-Run und Handoffbindung und zeigt einen importierten
  Lebenszyklus als Vorgeschichte; ihre Änderung hat keinerlei Wirkung auf den
  Replay.
- Ein lokaler Folgeübergang kann ein importiertes Finding nur nach den bereits
  geltenden Reporter- und Statusregeln verändern.

#### Fokussierte Validierung

- `python3 -m pytest tests/test_artifact_models.py tests/test_artifact_bridge.py tests/test_artifact_replay.py tests/test_artifact_projection.py -v`
- `git diff --check`

### Slice 2 - Crash-sicherer PLAN_ONLY-Handoff und fail-closed Importstart

#### Ziel

Der Planlauf erzeugt aus seinem validierten Replay genau einen gebundenen
Findingexport und genau einen IMPLEMENT-Task. Der neue Lauf prüft diese
Herkunft vor seinem ersten Provideraufruf, persistiert genau einen Import und
spiegelt die vollständige Historie in Initial-Work-Unit und State-v3.

**Exakter Änderungspfad**

- `src/plan_handoff.py`
- `src/task_contract.py`
- `src/orchestrator.py`
- `src/artifact_migration.py`
- `src/workflow_state.py`
- `tests/test_plan_handoff.py`
- `tests/test_task_contract.py`
- `tests/test_artifact_migration.py`
- `tests/test_workflow_state.py`
- `tests/test_orchestrator_runtime.py`

#### Umsetzung

1. Ersetze die lose Reihenfolge „Task schreiben, Binding ergänzen“ durch eine
   vorbereitete Handofftransaktion: validiere den Quellreplay und positiven
   Planreview, ermittle alle Findingtransitionen, berechne stabile Export-ID und
   exakte Taskbytes, binde deren Digest im Export und publiziere anschließend
   Export und Task atomar beziehungsweise record-ahead-wiederaufnehmbar. Der
   vorhandene allgemeine Implementation-Bindingrecord bleibt commit- und
   attestierungsgebunden und darf nicht zu einer zweiten Exportautorität werden.
2. Erweitere Task-Rendering und `TaskContract` nur um die geschlossene
   Herkunftsreferenz. Der Parser akzeptiert keine Findingprosa, prüft
   Quell-Run-/Record-ID-Formate und verlangt die Referenz genau dann, wenn der
   Export Findings trägt. Taskdigest, Pfad, Branch, Plancommit und Slices werden
   weiterhin gemeinsam gebunden.
3. Implementiere alle Persistenzgrenzen idempotent: Export dauerhaft vor
   Taskpublish, Task vorhanden vor allgemeinem Binding, Binding vorhanden vor
   PLAN_ONLY-Rückgabe sowie Neustart nach jeder Grenze. Eine existierende
   gleiche Tatsache wird wiederverwendet; semantische Divergenz hält
   fail-closed. Der Retry erzeugt weder einen zweiten Dateinamen noch einen
   zweiten Implementierungstask.
4. Lade beim Neuaufbau eines IMPLEMENT-Laufs die referenzierte Quellkette über
   `ArtifactStore`, validiere vollständigen Replay und Exportbindung und
   persistiere vor dem ersten normalen Baseline-Checkpoint den atomaren
   Importrecord. Initialisiere `runtime_history.findings` aus dessen Replay und
   setze `WorkUnitRecord.open_findings` für Slice 1 auf exakt den offenen
   importierten Satz; geschlossene Findings bleiben nur in der Historie.
5. Ergänze die reguläre Work-Unit-Record-/Mirrorbindung so, dass der importierte
   offene Satz nicht nur im frei serialisierten Runtimeverlauf steckt. Der
   State- und Artifactabgleich muss Abweichungen in IDs oder Status vor dem
   Workflow ablehnen.
6. Erweitere den strukturierten Resume in `artifact_migration.py` um die
   Herkunftsprüfung. Jeder Resume lädt den Quellreplay erneut und vergleicht ihn
   mit Taskreferenz und lokalem Importrecord, bevor eine lokale
   Record-ahead-Lücke fortgesetzt wird. Die Quellkette wird nie verändert und
   fehlende Evidenz wird nicht aus Mirror oder Projektion ergänzt.

#### Akzeptanzkriterien

- Ein synthetischer positiver Planreview mit offenem `C-01` erzeugt einen
  commit-, review-, head-, records- und taskdigestgebundenen Export; der daraus
  gestartete IMPLEMENT-Lauf enthält exakt ein Importrecord, dasselbe `C-01` im
  Replay, im Runtime-Mirror und in der ersten Work-Unit.
- Parametrisierte Abbrüche vor und nach Exportappend, Taskpublish, allgemeinem
  Handoffbinding, Importappend und erstem Checkpoint konvergieren nach Resume
  auf je eine semantische Tatsache und einen Implementierungstask. Kein Fake-
  Agent wird während reiner Recovery aufgerufen.
- Eine direkte Queuefortsetzung und ein Watcher-Neustart entdecken denselben
  erzeugten Task nur einmal; bestehende Watch-Identity-, Success- und
  Outboxsemantik bleiben unverändert.
- Manipulation jedes einzelnen Bindungswerts, ein fehlender Quellrecord, ein
  nicht mehr passender Quellhead, ein anderer Plancommit, ein nicht positiver
  Review sowie ein Import/State-Mirror-Unterschied stoppen vor Codex und
  Claude.
- Ein Plan mit null Findingtransitionen erzeugt weder Export- noch Importrecord
  und folgt byte- und verhaltensgleich dem bestehenden Handoffpfad.

#### Fokussierte Validierung

- `python3 -m pytest tests/test_plan_handoff.py tests/test_task_contract.py tests/test_artifact_migration.py tests/test_workflow_state.py tests/test_orchestrator_runtime.py -v`
- `git diff --check`

### Slice 3 - Durchgehender Findingvertrag für Slice, Review, Finalreview und Watch-Resume

#### Ziel

Offene importierte Findings erreichen den minimierten Slicevertrag und Codex,
Claude kann denselben Lebenszyklus fortschreiben, und der Finalreview sowie die
Queue-/Resumewege können kein offenes Finding übersehen.

**Exakter Änderungspfad**

- `src/provider_input_efficiency.py`
- `src/workflow.py`
- `tests/test_provider_input_efficiency.py`
- `tests/test_native_codex_request.py`
- `tests/test_native_review_request.py`
- `tests/test_review_packets.py`
- `tests/test_workflow.py`
- `tests/test_workflow_transition_matrix.py`
- `tests/test_inbox_watcher.py`

#### Umsetzung

1. Erweitere das kanonische normale Slice-Ausführungspaket um den
   deterministisch nach Finding-ID sortierten offenen Findingabschnitt mit ID,
   Klasse, Reporter, Zusammenfassung und Akzeptanztest. Übergib ihm aus
   `WorkflowHistory` denselben Satz, der in
   `NativeCodexContext.previous_findings` liegt, und prüfe eine Abweichung der
   beiden Darstellungen vor Requestbau.
2. Trage beim Übergang zu jedem späteren normalen Slice die vollständige
   replayte Historie und den aktuell offenen ID-Satz in die neue Work-Unit. Der
   Finalreview erhält ebenfalls den kompletten Ledger. Beschränke nur echte
   Korrekturrequests weiterhin auf ihren ausdrücklich betroffenen offenen Satz.
3. Nutze die bestehenden nativen Codex- und Claude-Builder ohne
   Markdownfallback: Codex darf eine Disposition nur für ein tatsächlich
   offenes importiertes Finding liefern; Claude sieht offene und geschlossene
   Vorgeschichte und darf nur eigene Findings nach dem bestehenden Vertrag
   schließen oder reklassifizieren. Neue Claude-IDs folgen auf die höchste
   importierte `C-*`-ID.
4. Belege über die bestehende Reviewpacket-Projektion, dass der normale
   Slice-Review den vollständigen offenen Importbestand und
   Closure-Referenzen enthält. Ein lokaler Statuswechsel referenziert dieselbe
   Finding-ID; es entsteht kein zweiter Eröffnungsrecord und keine geänderte
   Origin. Falls dieser Nachweis eine Änderung an `src/review_packets.py`
   erfordert, ist das ein Scopefehler und vor Umsetzung dieses Slice in den
   Plan zurückzugeben, statt den exakten Änderungspfad still zu erweitern.
5. Führe die providerfreien Akzeptanzfälle durch den realen
   `PLAN_ONLY`-Abschluss, den generierten Inbox-Handoff, einen neuen
   IMPLEMENT-Run, Slice 1, Folgeslice und Finalreview. Verwende Fake-Agenten
   beziehungsweise native Testoutputs und Zähler an allen Providergrenzen;
   kein Test nimmt Netz-, API- oder Providerdienste in Anspruch.
6. Ergänze Watcher-/Resume-Szenarien für Wiederaufnahme vor Import, nach
   Importrecord und nach Claude-Schließung. Nach einem technischen
   Bindungsfehler bleibt die Aufgabe resumierbar, aber es findet kein
   Agentenretry mit unvollständigem Findingkontext statt.

#### Akzeptanzkriterien

- `C-01` erscheint semantikgleich im Importreplay, State-Mirror,
  Work-Unit-Attribut, kompakten Slice-Paket und nativen Codex-Request. Codex
  akzeptiert es per gebundener `finding_disposition`; der native Claude-
  Slicereview schließt es mit einem Statuswechsel. Replay und Projektion zeigen
  eine Eröffnung aus dem Quelllauf, eine Codexantwort und eine lokale
  Claude-Schließung, aber nur eine Findingidentität.
- Bleibt das importierte `C-01` offen, wird ein positiver nativer Finalreview
  domänenseitig abgewiesen. Wird es korrekt geschlossen, sieht der Finalreview
  den geschlossenen Gesamtlebenszyklus und kann ohne offenes Finding
  fortfahren.
- Ein Quellplan mit mehreren offenen und geschlossenen Findings übernimmt alle
  Transitionen deterministisch. Geschlossene Findings fehlen im Codex-
  `open_findings`-Satz, bleiben jedoch in Claude-Kontext, Replay und
  Closure-Referenzen erhalten; ein neues Finding kollidiert mit keiner
  importierten ID.
- Watcher-Neustart, direkte Resumefortsetzung und Record-ahead-Recovery an
  jeder Persistenzgrenze rufen keinen Provider doppelt auf und erzeugen weder
  Duplicate-Record noch zweite Taskdatei.
- Jede manipulierte Quell-Run-, Plancommit-, Review-, Recordhead-,
  Findingrecord- oder Taskbindung scheitert in den Durchstichtests vor dem
  ersten Fake-Agent-Aufruf.
- Der Kontrollfall ohne Findings behält denselben Request- und Workflowverlauf
  ohne leere Import- oder Pseudofindingrecords.

#### Fokussierte Validierung

- `python3 -m pytest tests/test_provider_input_efficiency.py tests/test_native_codex_request.py tests/test_native_review_request.py tests/test_review_packets.py tests/test_workflow.py tests/test_workflow_transition_matrix.py tests/test_inbox_watcher.py -v`
- `git diff --check`

Nach jedem Slice übergibt Codex die fokussiert geprüfte Änderung normal an den
Orchestrator. Wegen der Änderungen an Orchestrierung, Handoff, Artifact-Replay,
Watch-/Resume-Verhalten und Projektion führt ausschließlich der Orchestrator
für den jeweiligen geprüften Fingerprint zusätzlich die vollständige Matrix
`python3 -m pytest tests/ -v` aus und erstellt die gebundene Attestierung.

## Risiken und Bruchbedingungen

- Die wichtigste Konstruktionsgefahr ist ein Hashzyklus zwischen Exportrecord
  und Taskbytes. Er wird nicht durch ein ungebundenes Nachschreiben gelöst,
  sondern durch eine vorhersagbare stabile Record-ID und eine einmalige
  kanonische Vorbereitung. Ein Test muss die Bytes unabhängig neu berechnen
  und dieselbe Identität erhalten.
- Eine bloße Latest-Snapshot-Übernahme würde Antwort-, Klassen- oder
  Schließungshistorie verlieren. Deshalb ist die geordnete Folge der
  Quelltransitionen die importierte Einheit, während der resultierende aktuelle
  Zustand weiterhin ausschließlich durch Replay entsteht.
- Eine beim Resume nicht mehr vorhandene Quellkette macht einen bereits lokal
  importierten Lauf absichtlich nicht fortsetzbar. Das ist die geforderte
  fail-closed Herkunftsgarantie; betriebliche Abhilfe ist die Wiederherstellung
  der passenden append-only Quellkette, nicht ein Import aus der Projektion.
- Die zusätzliche Findingdarstellung im kompakten Slice-Paket könnte vom
  nativen Top-Level-Kontext abweichen. Der Requestbau vergleicht deshalb beide
  aus derselben `WorkflowHistory` abgeleiteten Mengen vor Providerstart.
- Die Recordtypen erweitern das aktive `structured-v2`-Schema. Es gibt keine
  stillschweigende Migration: ältere v2-Ketten ohne diese Recordtypen bleiben
  gültig, während unbekannte oder historisch andere Protokollbindungen weiter
  fail-closed bleiben.
- Der Agentenvertrag ändert sich fachlich nicht: Reporterhoheit,
  Reviewpflicht, offene `OBSERVATION` im Slicereview und strenger Finalreview
  sind bereits synchron in `AGENTS.md`, `CLAUDE.md` und `CODEX.md` festgelegt.
  Falls die Implementierung dennoch neue Agentenpflichten einführt, müssen die
  drei Dateien im selben betroffenen Slice synchron ergänzt werden; ohne eine
  solche Vertragsänderung bleiben sie unberührt.

## Orchestrator-Prüfprotokoll

### Review-Feedback von Claude

<!-- audit:claude-review:begin -->
### Claude · Runde 1 · approved (Ereignis 2)

- Reviewer: `claude`
- Freigabe: `YES`
- Validierungsbindung: `plan-validation-fb0b0b708107`
- Testdateien: keine
- Prüfdimensionen: Checked: (1) correctness/completeness of the plan against all 8 provider-free acceptance cases and the 12 protocol invariants from the assignment; (2) contract compliance — exact &#96;### Slice N<br>- title&#96; and standalone &#96;**Exakter Änderungspfad**&#96; headings, single-file PLAN_ONLY scope matching the authorized path and executed diff, fingerprint binding between current_fingerprint and the validation attestation; (3) failure paths — fail-closed rejection of manipulated source-run/head/commit/review/finding bindings before any Codex/Claude call, explicit stop rules; (4) security/authority boundaries — reporter hoisted authority preserved, no Markdown used as a findings source, no synthetic finding fabrication; (5) resume/idempotency — crash-safe multi-step publish ordering, watcher/queue duplicate-task prevention reusing existing Watch-Identity semantics, no-findings control path left byte- and behavior-identical.
- Größtes Restrisiko: The export-record/task-digest construction cycle is resolved only at the design-invariant level ('vorhersagbare stabile Recordidentität … inhaltsadressierte Vorbereitungsphase') without a concrete algorithm, and this review could not independently confirm that every referenced repository path (e.g.<br>src/workflow_state.py, src/artifact_migration.py, src/review_packets.py) exists with the assumed responsibilities, since only the plan diff and one evidence chunk were supplied, not full repository access.
- Realistische Bruchbedingung: If Slice 2's transactional export/task-publish/import sequence turns out to require a second export authority alongside the existing Implementation-Bindingrecord, or if satisfying Slice 3's review-packet acceptance criterion actually forces an undeclared change to src/review_packets.py (which the plan itself flags as an out-of-scope error requiring a plan return), the current Slice boundaries and file scope break and the work must return to planning before implementation proceeds.
- Eigene Findings: `C-01`

<!-- artifact-records:claude-review:begin -->
Semantischer Record-Digest: `b0a484bf4898`

### Claude · Runde – · approved

| Seq/Record | Rolle | Runde | Status | Work-Unit | Findings | Fingerprint | Transport | Request | Response |
|---|---|---:|---|---|---|---|---|---|---|
| 9. `ar1-3fe7fdf56450` | `claude` | `–` | `approved` | `1` | `C-01` | `fb0b0b708107` | `native-claude-review-v2` | `native-review-request-3bca1fbfce35` | `f7adb0f42130` |
<!-- artifact-records:claude-review:end -->
<!-- audit:claude-review:end -->

### Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
Noch keine strukturierten Codex-Antworten.

<!-- artifact-records:codex-responses:begin -->
Semantischer Record-Digest: `b0a484bf4898`

Keine Codex-Findingantworten.
<!-- artifact-records:codex-responses:end -->
<!-- audit:codex-responses:end -->

### Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### Ereignis 1: `plan-validation-fb0b0b708107`

- Diff-Fingerprint: `fb0b0b708107`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: internal plan contract passed
- Ausgabedigest: `0f777c67298d`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| internal:work-plan-contract | PASS | 0 | slices=1; planned_paths=1; changed_paths=1; future_slices=3; work_plan=docs/internal/plan-only-finding-handoffverlust-arbeitsplan.md |

<!-- artifact-records:validation-attestation:begin -->
Semantischer Record-Digest: `b0a484bf4898`

- 2. `ar1-2b991a66d322`: Providerinput `codex/codex_plan` = `allowed`; local_input_chars `23099/4000000`, local_input_bytes `23188/16000000`; local_input_digest `efc697895b39`, Policy `9edf600f09ac`, Übergang `7d7f889670f1`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, local_input_largest_component `stdin_prompt`; local_input_component_count `2`; Komponenten `stdin_prompt=19310/19399, response_schema=3789/3789`
### Validierungsattestierung

| Seq/Record | Rolle | Fingerprint |
|---|---|---|
| 6. `ar1-df590c62e8ea` | `orchestrator` | `fb0b0b708107` |

| Status | Exit | Output-Digest | Befehl mit argv-Grenzen |
|---|---:|---|---|
| `pass` | `0` | `0f777c67298d` | `argv` [`internal:work-plan-contract`] |
- 7. `ar1-4a3cba02cb8a`: Providerinput `claude/claude_plan_review` = `allowed`; local_input_chars `67105/4000000`, local_input_bytes `67459/16000000`; local_input_digest `76d684505bd4`, Policy `9edf600f09ac`, Übergang `708a8d9cabbd`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, local_input_largest_component `evidence_asset_001`; local_input_component_count `6`; Komponenten `request_chunk_001=20431/20520, evidence_asset_001=35121/35386, packet_manifest=610/610, system_policy=430/430, response_schema=10283/10283, start_directive=230/230`
- Providerattempt-Summe Run `watch-20260828-113340.148422Z-6a566f5af629` / Operation `provider-operation-6b472a057880` (`codex/codex_plan`; Modell `gpt-5.6-sol`; Effort `medium`): Attempts `1`, offen `0`, Duration `177.086374` (bekannt `1`, unbekannt `0`); Inputzeichen `23099`, Inputbytes `23188`; Retrystatus `single-attempt`; input_tokens=sum:unknown,known:0,unknown:1; tool_input_tokens=sum:unknown,known:0,unknown:1; cache_read_input_tokens=sum:unknown,known:0,unknown:1; cache_creation_input_tokens=sum:unknown,known:0,unknown:1; thinking_tokens=sum:unknown,known:0,unknown:1; output_tokens=sum:unknown,known:0,unknown:1; total_tokens=sum:unknown,known:0,unknown:1; turns=sum:unknown,known:0,unknown:1; cost_usd=sum:unknown,known:0,unknown:1
  - 5. `ar1-630b9a689b88`: Attempt `1` = `succeeded`; Messung `ar1-2b991a66d322`; Modell `gpt-5.6-sol`; Effort `medium`; Inputzeichen `23099`; Inputbytes `23188`; Duration `177.0863743259979`; Fehler `none`; Usage `unknown`
- Providerattempt-Summe Run `watch-20260828-113340.148422Z-6a566f5af629` / Operation `provider-operation-c100639caccc` (`claude/claude_plan_review`; Modell `sonnet`; Effort `high`): Attempts `1`, offen `0`, Duration `114.846513` (bekannt `1`, unbekannt `0`); Inputzeichen `67105`, Inputbytes `67459`; Retrystatus `single-attempt`; input_tokens=sum:6,known:1,unknown:0; tool_input_tokens=sum:unknown,known:0,unknown:1; cache_read_input_tokens=sum:14917,known:1,unknown:0; cache_creation_input_tokens=sum:35253,known:1,unknown:0; thinking_tokens=sum:unknown,known:0,unknown:1; output_tokens=sum:10052,known:1,unknown:0; total_tokens=sum:unknown,known:0,unknown:1; turns=sum:5,known:1,unknown:0; cost_usd=sum:0.2455304,known:1,unknown:0
  - 11. `ar1-480aa7344f9e`: Attempt `1` = `succeeded`; Messung `ar1-4a3cba02cb8a`; Modell `sonnet`; Effort `high`; Inputzeichen `67105`; Inputbytes `67459`; Duration `114.84651275000215`; Fehler `none`; Usage `input_tokens=6, tool_input_tokens=unknown, cache_read_input_tokens=14917, cache_creation_input_tokens=35253, thinking_tokens=unknown, output_tokens=10052, total_tokens=unknown, turns=5, cost_usd=0.2455304`
<!-- artifact-records:validation-attestation:end -->
<!-- audit:validation-attestation:end -->

### Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
- Teständerungsfreigabe: nicht erfasst.
- Pre-Mortems:
  - Ereignis 2: This approval could fail if the eventual implementation reproduces the exact original incident: C-01-style findings silently dropped at the PLAN_ONLY→IMPLEMENT boundary because the transactional export/import sequence in Slice 2 is only partially built, or because the byte-recomputation regression flagged in C-01 is skipped, letting a hash-cycle shortcut (e.g.<br>writing task bytes before the export record is durable) reintroduce non-idempotent retries.<br>It could also fail if the review-packet scope guard in Slice 3 is ignored under implementation pressure, silently expanding the exact change path.<br>These risks are mitigated by the plan's explicit fail-closed stop rules, its atomic-import and reporter-authority invariants, and by leaving C-01 open here so a later Slice/Slice-review must explicitly close it against the real carried-forward behavior rather than assuming success.

<!-- artifact-records:test-approval-premortem:begin -->
Semantischer Record-Digest: `b0a484bf4898`

Keine strukturierten Gates.
<!-- artifact-records:test-approval-premortem:end -->
<!-- audit:test-approval-premortem:end -->

### Findings-Lebenszyklus

<!-- audit:findings:begin -->
### `C-01` — `OPEN`

- Quelle: `claude`; Runde 1
- Klasse: `OBSERVATION`
- Finding: The Risiken section correctly identifies the export-record/task-digest construction cycle as the main implementation hazard and requires a test that independently recomputes the IMPLEMENT task bytes and confirms identical record identity across retries.<br>However, Slice 2's own Akzeptanzkriterien list does not explicitly enumerate this independent byte-recomputation/idempotent-identity check; it only covers crash points around export-append, task-publish, binding, import-append and first checkpoint.<br>Without an explicit acceptance criterion, the concrete regression proving the hash-cycle resolution is stable could be skipped or under-specified during Slice 2 implementation, weakening the very invariant the plan calls its top construction risk.
- Akzeptanztest: During Slice 2 implementation, a test (e.g.<br>in tests/test_plan_handoff.py or tests/test_orchestrator_runtime.py) independently recomputes the IMPLEMENT task bytes and export record identity from the persisted export record's bound fields (source run, head, commit, review, ordered finding-transition IDs) without reusing cached bytes, and asserts byte-for-byte task content and identical export/task digests across at least two separate invocations, including one that simulates a crash/retry between export-append and task-publish.
- Statusbegründung: –

<!-- artifact-records:findings:begin -->
Semantischer Record-Digest: `b0a484bf4898`

### Finding-Ereignisse

| Seq/Record | Finding | Rolle | Runde | Aktion | Klasse | Status | Begründung |
|---|---|---|---:|---|---|---|---|
| 10. `ar1-5f6a187b3997` | `C-01` | `claude` | `–` | `opened` | `OBSERVATION` | `open` | The Risiken section correctly identifies the export-record/task-digest construction cycle as the main implementation hazard and requires a test that independently recomputes the IMPLEMENT task bytes and confirms identical record identity across retries.<br>However, Slice 2's own Akzeptanzkriterien list does not explicitly enumerate this independent byte-recomputation/idempotent-identity check; it only covers crash points around export-append, task-publish, binding, import-append and first checkpoint.<br>Without an explicit acceptance criterion, the concrete regression proving the hash-cycle resolution is stable could be skipped or under-specified during Slice 2 implementation, weakening the very invariant the plan calls its top construction risk. |

### Native convergence summary

| Finding | Work units | Rounds | Fingerprints | Claude decisions | Codex dispositions | Final status |
|---|---|---|---|---|---|---|
| `C-01` | `1` | `1` | `fb0b0b708107` | `opened:open` | – | `open` |
<!-- artifact-records:findings:end -->
<!-- audit:findings:end -->

### Entscheidungstabelle

<!-- audit:decision-table:begin -->
| ID | Quelle | Finding | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|---|
| C-01 | claude | The Risiken section correctly identifies the export-record/task-digest construction cycle as the main implementation hazard and requires a test that independently recomputes the IMPLEMENT task bytes and confirms identical record identity across retries.<br>However, Slice 2's own Akzeptanzkriterien list does not explicitly enumerate this independent byte-recomputation/idempotent-identity check; it only covers crash points around export-append, task-publish, binding, import-append and first checkpoint.<br>Without an explicit acceptance criterion, the concrete regression proving the hash-cycle resolution is stable could be skipped or under-specified during Slice 2 implementation, weakening the very invariant the plan calls its top construction risk. | OBSERVATION | offen | offen |

<!-- artifact-records:decision-table:begin -->
Semantischer Record-Digest: `b0a484bf4898`

| Seq | Record | Typ | Status | Logische ID | Revision | Fingerprint |
|---:|---|---|---|---|---:|---|
| 1 | `ar1-759640744f7b` | `task` | `accepted` | `task-contract` | 1 | `contract:60b05567e629` |
| 2 | `ar1-2b991a66d322` | `provider_input_measurement` | `measured` | `provider-input-1-codex_plan` | 1 | `implementation:60b05567e629` |
| 3 | `ar1-4f8289ceb089` | `provider_attempt` | `started` | `provider-operation-6b472a057880-1` | 1 | `implementation:60b05567e629` |
| 4 | `ar1-2d1c78241c49` | `agent_result` | `ready` | `agent-1-codex_plan-1` | 1 | `contract:60b05567e629` |
| 5 | `ar1-630b9a689b88` | `provider_attempt` | `succeeded` | `provider-operation-6b472a057880-1` | 2 | `implementation:60b05567e629` |
| 6 | `ar1-df590c62e8ea` | `validation_attestation` | `attested` | `plan-validation-fb0b0b708107` | 1 | `implementation:fb0b0b708107` |
| 7 | `ar1-4a3cba02cb8a` | `provider_input_measurement` | `measured` | `provider-input-1-claude_plan_review` | 1 | `implementation:60b05567e629` |
| 8 | `ar1-89de46c3f93d` | `provider_attempt` | `started` | `provider-operation-c100639caccc-1` | 1 | `implementation:60b05567e629` |
| 9 | `ar1-3fe7fdf56450` | `review` | `decided` | `review-claude-1-1` | 1 | `implementation:fb0b0b708107` |
| 10 | `ar1-5f6a187b3997` | `finding_transition` | `recorded` | `finding-C-01` | 1 | `implementation:fb0b0b708107` |
| 11 | `ar1-480aa7344f9e` | `provider_attempt` | `succeeded` | `provider-operation-c100639caccc-1` | 2 | `implementation:60b05567e629` |

### Nachweis vollständiger Bindungswerte

| Kurzreferenz | Vollwert | Feldarten |
|---|---|---|
| `fb0b0b708107` | `fb0b0b708107bd2e35fa5cfd64f1347854b03c2cba9485a75b372710faa34b6f` | Technischer Wert, Attestierungsreferenz, Fingerprint, Record-ID |
| `b0a484bf4898` | `b0a484bf4898cf7653c9d328e5f6e2549bee1a6a4a38769024ee1d913951b1cc` | Record-ID |
| `3fe7fdf56450` | `3fe7fdf564504efd05fa45e8f0c29226b2bec011f83d710c861351a6061b96cc` | Request-ID, Technischer Wert |
| `3bca1fbfce35` | `3bca1fbfce3506e863db70a73b4f1e1d65cd14e0ee6fe107865243277d77a5b5` | Request-ID |
| `f7adb0f42130` | `f7adb0f42130e8eaf7489b9596e0dbc5785cdd079bdf67863a390dd2cf0b69df` | Request-ID |
| `0f777c67298d` | `0f777c67298d3c698c23d4f63409bc41781e01566b8b287655351c15ec5a526e` | Output-Digest |
| `2b991a66d322` | `2b991a66d322b40d08dbb68015c15e575961c9497b3be8c72deda51b3f133328` | Technischer Wert, Messungsreferenz |
| `efc697895b39` | `efc697895b396b5b800ebf51c1ba6adfe08be19404fd3d0ff7ad907b0be2e186` | Digest |
| `9edf600f09ac` | `9edf600f09ac1b36a30675cc91b1f6769c4545f8f200a4fba413b4b9a4c9bc1e` | Policy-Digest |
| `7d7f889670f1` | `7d7f889670f1355f9723cb9a6ccd7f19727ab56874bea60f731591d76ce92d6e` | Übergangsfingerprint |
| `df590c62e8ea` | `df590c62e8eaf962b318407372ab5632c590a0c249443c6fe2526825c4b6583a` | Record-ID, Technischer Wert |
| `4a3cba02cb8a` | `4a3cba02cb8a2041cc29c42c705228d6df5a9dc18c8a85a1ef73465822763090` | Technischer Wert, Messungsreferenz |
| `76d684505bd4` | `76d684505bd4adf10aa1486520a20c3719574b3d1e082cae58e65096103e9dd9` | Digest |
| `708a8d9cabbd` | `708a8d9cabbd6b8a66d93b11b374a1db84bfd9ba914cd532569a0781bee77c7d` | Übergangsfingerprint |
| `6b472a057880` | `6b472a0578804cb0a3bae545cab5be3ae7053e7b0abfac6982ea628b38a9cf3c` | Technischer Wert |
| `630b9a689b88` | `630b9a689b8827352b6e426dde7ba76672978cb9eeab9bd5672e1585504eb348` | Technischer Wert |
| `c100639caccc` | `c100639caccc2bc53655a4d58ce38fe8264f33b7b924fd174205b67edfa223ee` | Technischer Wert |
| `480aa7344f9e` | `480aa7344f9e97522220f3fe8c45a4ac51f49d183a966d15a4aee8e1b53e86d8` | Technischer Wert |
| `5f6a187b3997` | `5f6a187b39972c7d0758bdc1bb4298c6997215547f191633cd74b4d0c9f4a4a4` | Technischer Wert |
| `759640744f7b` | `759640744f7ba2e8055d0fbe498941119021815250f78d844f40fa6f10b25975` | Fingerprint |
| `60b05567e629` | `60b05567e629500b9074f537b9871320b3f0270d35a0d594eff8a34ce46338ec` | Technischer Wert |
| `4f8289ceb089` | `4f8289ceb089436883e873d722d51504e8509a657b9e31c72311087427421a66` | Technischer Wert |
| `2d1c78241c49` | `2d1c78241c4972f87c7e956cc2a5f9ee98ec582cca304d59a4974c853c730d68` | Technischer Wert, Response-Digest |
| `89de46c3f93d` | `89de46c3f93d0e567336f4e19891f23c41c889e9b21e89216aa580af1ce7e318` | Technischer Wert |
| `ac4369714d5b` | `ac4369714d5bb78e2dc85a4589330e3950cccec7ab564891339c741ea133cdc6` | Request-ID |
| `a9b55532de80` | `a9b55532de80c57ad2a06022b5d5f81d34bb4a21a23e42f54938324a719f991d` | Request-ID |
<!-- artifact-records:decision-table:end -->
<!-- audit:decision-table:end -->

### Freigabestatus

<!-- audit:approval-status:begin -->
- Implementierung bereit: `NOT_RECORDED`
- Validierung: `PASS`
- Claude-Freigabe: `YES`
- Red-State-Folgeslice: `NONE`
- Commit autorisiert: `NO`

<!-- artifact-records:approval-status:begin -->
Semantischer Record-Digest: `b0a484bf4898`

### Codex · Runde – · ready

| Seq/Record | Rolle | Runde | Status | Work-Unit | Tests | Transport | Request | Response | Fingerprint |
|---|---|---:|---|---|---|---|---|---|---|
| 4. `ar1-2d1c78241c49` | `codex` | `–` | `ready` | `1` | keine | `native-codex-v2` | `native-codex-request-ac4369714d5b` | `a9b55532de80` | `60b05567e629` |
<!-- artifact-records:approval-status:end -->
<!-- audit:approval-status:end -->
