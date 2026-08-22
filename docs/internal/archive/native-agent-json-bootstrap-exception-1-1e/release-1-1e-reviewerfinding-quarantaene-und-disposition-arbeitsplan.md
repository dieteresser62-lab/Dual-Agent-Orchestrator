# Release 1.1E – Reviewerfinding-Quarantäne und Dispositionspflicht

## Planstatus und Ausführungsgrenze

- Zielbranch ist `feature/orchestrator-stabilization-1-1e`; der bei der Planung
  geprüfte Branch stimmt mit dieser Bindung überein.
- Dieser Lauf ist `PLAN_ONLY`. Er erstellt ausschließlich dieses
  Arbeitsplanartefakt. Produktcode, Produkttests, Konfiguration, Schemata und
  generierte Artefakte bleiben im Planungslauf unverändert.
- Maßgeblich ist ausschließlich P2-FU-033 aus
  `docs/internal/phase-2-arbeitspaket-1-erkenntnisse-und-stabilisierung-1-1.md`.
  Die archivierten 1.1D-Dokumente unter
  `docs/internal/archive/orchestrator-stabilization-1-1d/` sind reproduzierbare
  Evidenz für den Realfall, aber weder Live-State noch Erlaubnis für historische,
  heute nicht mehr aufgerufene Änderungspfade.
- Der künftige Implementierungslauf besteht aus genau zwei zusammenhängenden,
  einzeln implementier- und reviewbaren Slices. Slice 1 schließt Modell, Schema,
  Parser, Replay, Writer und Projektion der neuen Records synchron. Slice 2
  bindet diesen Vertrag an den aktuellen Review-, Paket- und Resume-Pfad.
- Slice 1 hat sechs und Slice 2 sechs produktive Änderungspfade. Testdateien und
  das später automatisch abgeleitete Sliceprotokoll zählen nicht als produktive
  Dateien. Ein neuer veränderlicher State-v3-Spiegel, ein zweites Ledger oder
  eine allgemeine Quarantäneplattform sind ausdrücklich nicht vorgesehen.
- Agenten führen im Implementierungslauf nur fokussierte Tests mit gespeicherten
  synthetischen Antworten, temporären Artifact-Stores, Fake-Adaptern und
  injizierten Crashgrenzen aus. Die vollständige Matrix
  `python3 -m pytest tests/ -v` führt ausschließlich der Orchestrator aus;
  Agenten emittieren kein `VALIDATION_RESULT`.

## Repositorybefund: heutige Provider-bis-Resume-Kette

### Providerinput, Providerattempt und vollständiger Output

- `src/workflow.py::_run_review()` baut den aktuellen `StepContract`, das
  fingerprintgebundene Reviewpaket und den `ReviewerInvocation`. Vor einem neuen
  Providerstart fragt die Methode bereits
  `driver.recover_failed_reviewer_output()` ab; nur wenn dort kein lokal
  wiederverwendbarer Output vorliegt, läuft `_invoke_role()` und anschließend
  `driver.invoke_reviewer()`.
- `src/orchestrator.py::invoke_reviewer()` materialisiert ein vorhandenes
  Reviewpaket content-addressiert und ruft `_agent()` mit Reviewer, Operation,
  Work Unit und Reviewfingerprint auf. `_agent()` übergibt den vollständigen
  Providerinput an `src/agent_runtime.py::run_agent_checked()` und bindet die
  bereits vorhandenen Provider-Lifecycle-Hooks ein.
- Vor dem physischen Prozess persistiert
  `src/orchestrator.py::_persist_provider_bootstrap()` über
  `ArtifactBridge.append()` einen `ProviderInputMeasurementPayload`. Dieser
  enthält Provider/Rolle, Operation, Work Unit, Transitionfingerprint,
  relevanten Record-Head, `input_digest`, Policy-Digest und ausschließlich
  Größen-/Budgetmetadaten. Der Prompt selbst gelangt nicht in diesen Record.
- `_start_provider_attempt()` und `_finish_provider_attempt()` persistieren über
  `src/artifact_bridge.py` je physischem Start eine Revision `started` und genau
  eine terminale Revision `succeeded` oder `failed`. Beide binden die logische
  Operation an Run, Work Unit, Provider/Rolle, Operation, Inputdigest,
  Binding-Fingerprint und den Messrecord. `src/artifact_replay.py` erzwingt
  lückenlose Attemptnummern, vorangehende Messung, höchstens eine Terminalrevision
  und unveränderliche Bindungen.
- Erst nach dem terminalen Lifecycle-Hook liefert `run_agent_checked()` den
  Agentenoutput zurück. `src/agent_runtime.py` schreibt ihn derzeit vollständig
  in `work-unit-…attempt-….log`; der Pfad ist attemptbezogen, aber weder
  content-addressiert noch selbst autoritative Workflowquelle. Für erfolgreich
  geparste Reviews steckt der SHA-256-Digest zusätzlich im Idempotenzschlüssel
  des `ReviewPayload`. Für verworfene Verträge bindet `DiagnosticPayload` den
  SHA-256-Digest und den konkreten Parsergrund, nicht aber Operation,
  Inputdigest oder den terminalen Providerattempt in einem einzigen
  replaybaren Vertrag.

### Vertragsprüfung und heutiger Evidenzverlust

- `src/workflow.py::_validate_or_repair_review()` normalisiert den Output lokal
  und ruft `src/contracts.py::validate_review_response()` auf. Bei der ersten
  `ContractValidationError` persistiert es über
  `persist_contract_diagnostic()` Diagnose und Outputdigest. Nur eng benannte
  Fehlerklassen dürfen den vorhandenen kompakten Provider-Reparaturaufruf
  auslösen; andernfalls bleibt der Gesamtvertrag eine Ablehnung.
- `src/contracts.py::_merge_review_findings()` parst `NEW_FINDING`,
  `FINDING_STATUS` und `FINDING_RECLASSIFIED` heute atomar als Teil des gesamten
  Reviewvertrags. Ein formal vollständiges `NEW_FINDING` wird zunächst lokal
  erkannt, doch jeder sachfremde ungültige Marker lässt
  `validate_review_response()` insgesamt scheitern. Das lokale Zwischenobjekt
  wird dann nicht persistiert.
- Nur nach vollständig erfolgreicher Vertragsprüfung ruft
  `src/workflow.py::_run_review()`
  `src/orchestrator.py::persist_review_contract()` auf. Diese Methode schreibt
  den autoritativen `ReviewPayload` und leitet aus dem validierten Gesamtergebnis
  `FindingTransitionPayload`-Records ab. Danach spiegelt `_record_review()` die
  Entscheidung in `WorkflowHistory`. Diese Reihenfolge verweigert einem
  ungültigen Vertrag zu Recht Approval und autoritative Findingtransition; sie
  besitzt jedoch noch keinen nicht autoritativen Kandidatenpfad.
- Die 1.1D-Projektion in
  `docs/internal/archive/orchestrator-stabilization-1-1d/release-1-1d-antigravity-runtime-retry-und-attempt-telemetrie-implement-review-17521525.md`
  belegt für Work Unit 2 einen terminal erfolgreichen Claude-Providerattempt und
  anschließend einen `diagnostic`-Record, aber keinen Review- oder
  Findingtransition-Record für diesen Output. P2-FU-033 dokumentiert dazu die
  gespeicherte Antwort mit gültigem `NEW_FINDING` und der ungültigen Zeile
  `FINDING_STATUS: none reported by claude previously in this packet.`. Nach dem
  Fingerprintwechsel konnte die reservierungslose ID sachfremd neu vergeben
  werden.

### Recordautorität, Idempotenz und Resume

- `src/artifact_store.py::ArtifactStore.put()` veröffentlicht Records atomar,
  prüft Idempotenzschlüssel semantisch und dokumentiert ausdrücklich den Fall
  `durable-but-reported-failed`. `src/artifact_bridge.py::ArtifactBridge.append()`
  sucht nach einem Appendfehler denselben Idempotenzschlüssel erneut in der
  Kette und akzeptiert ausschließlich semantisch identische Evidenz.
- `src/artifact_replay.py::replay_artifacts()` validiert die append-only Kette
  ohne Mutation. `src/artifact_projection.py` projiziert daraus deterministisch
  die menschliche Auditansicht. `.orchestrator/state.json`, Checkpoints und
  `WorkflowHistory` bleiben operative Spiegel; für neue Quarantäne- und
  Dispositionsfakten dürfen sie nicht zur Autorität werden.
- `src/orchestrator.py::recover_failed_reviewer_output()` kann heute exakt einen
  diagnose- und fingerprintgebundenen Attempt-Log nach einer deterministischen
  lokalen Normalisierung wiederverwenden und prüft den vollständigen Vertrag
  erneut, ohne Providerstart. Die Suche ist aber auf denselben Fingerprint und
  den Korrekturfall zugeschnitten. `recover_pending_reviewer()` rekonstruiert
  dagegen einen bereits autoritativ persistierten Review bei einem
  Checkpointabbruch. Keiner der beiden Pfade kennt bislang offene
  Quarantänekandidaten.
- Vor externen Workflow-Seiteneffekten lädt
  `src/orchestrator.py::assert_structured_decision_context()` die Kette über
  `resolve_resume_state()` neu und vergleicht autoritative Reviewrecords mit dem
  operativen Mirror. Diese bestehende fail-closed Grenze ist der Ort, an dem eine
  aus Records projizierte offene Dispositionspflicht zusätzlich vor Approval,
  Commitbindung und nachgelagertem Reviewerstart wirksam werden muss; ein neuer
  State-Spiegel ist dafür nicht erforderlich.

### Reviewpaket, Findingnummer und Eigentümerschaft

- `src/review_packets.py::build_review_packet()` baut das aktuelle kompakte,
  fingerprintgebundene Paket aus Manifest, Diff, Plananforderungen,
  Attestierung, offenen Findings und Closure-Digests. Das Paket enthält weder
  Prompts noch Providerattempt-Logs. Quarantänekandidaten sind noch kein Input.
- `src/prompts.py::build_v3_review_contract()` berechnet die nächste Finding-ID
  ausschließlich aus den autoritativen bisherigen Findings und weist Claude
  `C-*`, Antigravity `A-*` zu. Weil Quarantäne-IDs fehlen, kann eine verlorene ID
  heute erneut angeboten werden.
- `src/contracts.py::FindingRecord`, `_validate_finding_id()` und
  `apply_reviewer_finding_update()` erzwingen bereits, dass nur Claude `C-*` und
  nur Antigravity `A-*` meldet beziehungsweise mutiert. Codex kann lediglich
  `FINDING_RESPONSE` schreiben und damit kein Finding schließen. Der neue
  Kandidatenvertrag muss dieselbe Eigentümerschaft übernehmen und darf Codex
  keine Dispositionsberechtigung geben.
- `AGENTS.md` ist die einzige Root-Rollendatei, die unter `## Output records`
  die Reviewer-Marker selbst kanonisch ausschreibt. `CLAUDE.md`, `CODEX.md` und
  `ANTIGRAVITY.md` duplizieren diese Grammatik bewusst nicht, sondern verpflichten
  ihre jeweilige Rolle ausdrücklich auf den gemeinsamen state-v3-Vertrag in
  `AGENTS.md`; ihre rollenspezifischen Rechte bleiben durch den neuen Marker
  unverändert. `README.md` führt dieselben aktiven Marker in der öffentlichen
  Tabelle `Agentenanweisungen und Ausgabevertrag`. Der bestehende
  `tests/test_language_consistency.py::test_readme_markers_match_the_active_root_contract`
  vergleicht diese beiden ausgeschriebenen Grammatikquellen bereits. Daher sind
  `AGENTS.md` und `README.md`, nicht aber die drei reinen Verweisdateien, die
  belegten produktiven Dokumentationspfade für die neue Syntax; der Driftguard
  wird um Syntax und Reservierungsregel erweitert.

## Zielvertrag und technische Festlegungen

1. Es werden zwei neue strukturierte Recordtypen eingeführt:
   `reviewer_output` erfasst die content-addressierte Reviewerantwort zuerst als
   `captured` und bindet eine spätere ungültige Vertragsdiagnose als
   idempotente Revision; `finding_candidate` erfasst genau einen eindeutig
   extrahierten Kandidaten zunächst als `quarantined` und später als genau eine
   terminale Disposition `confirmed`, `resolved` oder `rejected`. Beide Typen
   sind geschlossen modelliert, im JSON-Schema gespiegelt, replaybar und
   deterministisch projizierbar.
2. Unmittelbar nachdem der terminal erfolgreiche Providerattempt den Output
   geliefert hat und vor jeder Normalisierung oder Vertragsprüfung wird dessen
   exakter UTF-8-Inhalt unter
   `.orchestrator/artifacts/<run-id>/reviewer-outputs/<output-sha256>.txt`
   create-only materialisiert und bytegenau rückgelesen. Der autoritative
   `reviewer_output`-Record enthält keinen Volltext und keinen privaten
   Laufzeitpfad, sondern Run-indirekt Work Unit, Reviewer, Operation,
   Inputdigest, ursprünglichen Reviewfingerprint, Outputdigest sowie Record-IDs
   des Messrecords und terminalen Providerattempts. Die Blobdatei ist
   Quellevidenz wie der bestehende content-addressierte Reviewpaketcache, kein
   zweites Ledger.
3. Ein nicht erfolgreich abgeschlossener Providerattempt erzeugt keinen
   Reviewoutput-Record. Die Zuordnung des erfolgreichen Outputs zu Messung und
   Terminalattempt muss aus der aktuellen Kette eindeutig sein; fehlende oder
   mehrfache Treffer halten fail-closed. Nach einem Parserfehler verweist die
   nächste `reviewer_output`-Revision exakt auf den bestehenden
   `DiagnosticPayload` und dessen konkrete Diagnose. Der Gesamtvertrag bleibt
   abgelehnt und erzeugt weder `ReviewPayload`, Approval noch autoritative
   Findingtransition.
4. Ein neuer reiner Parser in `src/contracts.py` untersucht ausschließlich
   vollständige, nicht aus Delimiter-Evidenz stammende `NEW_FINDING`-Zeilen. Er
   akzeptiert nur vier nichtleere Pipefelder, die exakte Klasse
   `BLOCKER|OBSERVATION`, die dem aufrufenden Reviewer gehörende ID und sichere
   einzeilige Nutzdaten. Freie Prosa, fremde Präfixe, beschädigte Felder,
   unbekannte Markerformen, nur teilweise parsebare `NEW_FINDING`-Zeilen sowie
   doppelte oder widersprüchliche Definitionen derselben ID ergeben keinen
   Kandidaten. Die Extraktion interpretiert keine freie Modellprosa und fällt
   bei Mehrdeutigkeit für den betroffenen Output vollständig auf Diagnose
   zurück.
5. Der Kandidat speichert reservierte Finding-ID, Klasse, kompakte Summary und
   Akzeptanztest, Eigentümer, ursprünglichen Fingerprint, Outputdigest und
   Referenzen auf die invalidierte Outputrevision und Diagnose. Sein logischer
   Schlüssel wird deterministisch aus Run, Reviewer, Work Unit, Operation,
   Outputdigest und Finding-ID gebildet. Replay, erneute lokale Validierung,
   Direkt-Resume und Watcher-Neustart verwenden denselben Idempotenzschlüssel und
   erzeugen deshalb höchstens eine Quarantänerevision.
6. Kandidateninhalte übernehmen ausschließlich die vier validierten Markerfelder.
   Prompt, Providerinput, Providerdiagnose-Volltext, Secrets, absolute/private
   Laufzeitpfade und umliegende freie Prosa werden weder in Kandidatenrecord,
   Standardlog noch Reviewpaket kopiert. Das vollständige Outputblob bleibt nur
   über seinen Digest adressierbare Rohquelle. Standardlogs nennen höchstens
   Run-/Work-Unit-freie logische Kurzidentität, Reviewer, Status und Digests.
7. Alte `structured-v1`-Ketten ohne die neuen optionalen Recordtypen replayen und
   projizieren unverändert; es gibt keine Migration, keine rückwirkende
   Kandidatenableitung und keine Neubewertung alter Approvals. Historische
   `legacy-state-v3`-Läufe bleiben auf ihrem Protokoll. Kandidaten werden nur aus
   einem in einem neuen Lauf vor der Vertragsprüfung erfassten Output erzeugt.
8. Offene Kandidaten werden beim nächsten Review desselben Eigentümers aus der
   Recordkette ausgewählt, unabhängig vom aktuellen Repositoryfingerprint. Ein
   kompaktes Paket enthält Findingrecord, Candidate-/Output-/Diagnosedigests,
   ursprünglichen und aktuellen Fingerprint sowie eine eng begrenzte
   `NEW_FINDING`-Quellevidenz, nie historische Volloutputs. Kandidaten anderer
   Reviewer werden nicht als disponierbar angeboten.
9. Der Reviewvertrag erhält genau einen expliziten Dispositionsmarker pro
   eigenem offenen Kandidaten:
   `FINDING_CANDIDATE: <ID> | CONFIRMED|RESOLVED|REJECTED | <rationale>`.
   `CONFIRMED` übernimmt exakt die gespeicherten Kandidatenfelder als
   autoritatives offenes Finding; `RESOLVED` hält dauerhaft fest, dass der
   Befund im aktuellen Stand behoben ist; `REJECTED` verlangt eine nichtleere
   fachliche Begründung. Fehlende, doppelte, widersprüchliche oder fremde
   Dispositionen machen den gesamten neuen Reviewvertrag ungültig. Codex besitzt
   für diesen Marker keine Rolle.
10. Solange ein Kandidat offen ist, bleibt seine ID reserviert. Der normale
    `NEW_FINDING`-Parser lehnt ihre Wiederverwendung ab, der Prompt bietet sie
    nicht als nächste ID an, und ein positiver Reviewvertrag ist ohne vollständige
    eigentümereigene Disposition ungültig. Claude muss seine offenen `C-*` vor
    Antigravity disponieren; Antigravity entsprechend seine `A-*` vor einer
    positiven eigenen Entscheidung. Damit überlebt die Pflicht sachfremde
    Fingerprintwechsel, Korrekturrunden und neue Reviewpakete.
11. Bei `CONFIRMED` persistiert der Writer mit stabilen Idempotenzschlüsseln
    zuerst genau eine autoritative `FindingTransitionPayload(action="opened")`
    aus den unveränderten Kandidatenfeldern und danach die terminale
    Kandidatenrevision mit Referenz auf diesen Transitionrecord. Ein Crash
    dazwischen lässt den Kandidaten weiter offen und Resume findet die bereits
    durable Transition wieder. `RESOLVED` und `REJECTED` schreiben nur die
    terminale Kandidatenrevision. Replay verweigert Eigentümerwechsel,
    widersprüchliche Terminalrevisionen, Bestätigung ohne passende vorangehende
    Opening-Transition und mehrfachen Transfer.
12. Approval- und Commitgrenzen laden die Kandidatenprojektion aus der
    autoritativen Kette neu. Ein offener oder halb bestätigter Kandidat blockiert
    positive Freigabe, Antigravity-Fortschritt, Binding und Completion
    fail-closed. Ein neuer Providerstart ist nur für den Eigentümer und mit einem
    fingerprintgebundenen Dispositionspaket zulässig. Ein sachfremder Review,
    eine Korrekturrunde oder ein Watcher-Neustart darf den Kandidaten nicht
    überschreiben.
13. Die lokale Wiederverwendung liest das Outputblob über den gespeicherten
    Digest statt über heuristische Logsuche, prüft Recordreferenzen und Bytes und
    führt denselben aktuellen lokalen Parser erneut aus. Sie startet keinen
    Provider. Wird der Gesamtvertrag nach einer deterministischen Parserkorrektur
    gültig, bleibt eine bereits erzeugte Quarantänepflicht bestehen, bis der
    Eigentümer sie explizit disponiert; eine gültige Erstauswertung erzeugt
    dagegen ausschließlich den normalen autoritativen Review-/Findingpfad und
    keine Quarantäne.

### Slice 1 - Geschlossener Record-, Parser- und Replayvertrag für Quarantänekandidaten

**Exakter Änderungspfad**

- `schemas/orchestrator-artifact-v1.schema.json`
- `src/artifact_bridge.py`
- `src/artifact_models.py`
- `src/artifact_projection.py`
- `src/artifact_replay.py`
- `src/contracts.py`
- `tests/test_artifact_bridge.py`
- `tests/test_artifact_models.py`
- `tests/test_artifact_projection.py`
- `tests/test_artifact_replay.py`
- `tests/test_contracts.py`
- `tests/test_structured_artifact_regressions.py`
- `docs/internal/slice-release-1-1e-reviewerfinding-quarantaene-und-disposition-arbeitsplan-01-geschlossener-record-parser-und-replayvertrag-fur-quarantanekandidaten.md`

#### Integrationsschritte

1. Ergänze in `src/artifact_models.py` die geschlossenen Payloads
   `ReviewerOutputPayload` und `FindingCandidatePayload` einschließlich der
   Phasen-/Dispositionsinvarianten, Reviewerpräfixe, SHA-256-Felder,
   Recordreferenzen und zulässigen Revisionen. Nimm sie in `RecordType`,
   `ArtifactPayload`, Serialisierung und Deserialisierung auf. Kandidaten tragen
   nur die vier Findingfelder und kompakte Provenienz; Volloutput und private
   Pfade bleiben außerhalb des Records.
2. Spiegle beide Typen atomar in
   `schemas/orchestrator-artifact-v1.schema.json`: geschlossene
   `additionalProperties: false`-Payloads, status-/phasenabhängige Pflichtfelder,
   Reviewer- und Dispositionsenum, SHA-256- und Identifiergrenzen sowie nullable
   Referenzen nur in der initialen Phase. Schema-Version und bestehende Typen
   bleiben unverändert, damit alte Dokumente weiterhin byte- und semantisch
   gleich gelesen werden.
3. Ergänze `src/contracts.py` um einen reinen
   `extract_unambiguous_new_finding_candidates()`-Pfad und den expliziten
   `FINDING_CANDIDATE`-Dispositionsparser. Verwende dieselbe Delimiterbereinigung,
   ID-/Ownerprüfung und `FindingRecord`-Validierung wie der strikte Vertrag,
   ohne dessen atomare Approvalsemantik zu lockern. Reservierte IDs zählen bei
   der normalen Findingvalidierung als belegt; nur vollständige, eindeutige
   eigentümereigene Marker liefern Kandidaten.
4. Ergänze in `src/artifact_bridge.py` kleine fachliche Writergrenzen für
   Output-Capture/Invalidierung, Kandidatenquarantäne und terminale Disposition.
   Leite logische IDs und Idempotenzschlüssel ausschließlich aus stabiler
   Provenienz ab, lade vor jedem Übergang die Kette neu und verwende die
   bestehende `append()`-Recovery für durable-but-reported-failed. Eine
   Bestätigung erzeugt genau eine vorhandene `finding_transition` und bindet
   deren Record-ID; Schließen und Verwerfen ändern das autoritative Ledger nicht.
5. Härte `src/artifact_replay.py` um Referenz- und Lifecycleprüfungen: Output
   verweist rückwärts auf passende Messung und terminal erfolgreichen Attempt;
   eine Invalidierungsrevision auf genau eine passende Diagnose; Kandidat auf
   invalidierten Output, Digest, Fingerprint und Eigentümer; terminale
   Disposition auf denselben unveränderlichen Kandidaten. Bestätigung erfordert
   genau die passende vorherige Opening-Transition. Mehrere Terminalzustände,
   ID-/Ownerwechsel, fremde Präfixe und vorwärts gerichtete Referenzen werden
   fail-closed verworfen.
6. Ergänze `src/artifact_projection.py` um eine read-only Projektion der
   Output-Provenienz und Kandidatenlebenszyklen. Zeige nur Digests, reservierte
   ID, Reviewer, Klasse, Status und kompakte Begründung; nie Volloutput, Prompt,
   Inputdigest im Standardabschnitt, private Blobpfade oder historische
   Quellevidenz. Die Projektion wird ausschließlich aus akzeptiertem Replay
   erzeugt und bleibt keine Reparaturquelle.

#### Fokussierte synthetische Akzeptanztests

- `tests/test_contracts.py` verwendet gespeicherte Claude- und
  Antigravity-Antworten. Der beobachtete Fall mit einem vollständigen
  `NEW_FINDING: C-* | BLOCKER | … | …` plus ungültigem
  `FINDING_STATUS: none reported …` bleibt als Gesamtvertrag ungültig, liefert
  aber genau einen strukturellen `C-*`-Kandidaten; die entsprechende
  Antigravity-Antwort liefert genau einen `A-*`-Kandidaten.
- Parametrisierte Parserfälle mit freier Prosa `C-01`/`A-01`, fremdem Präfix,
  fehlendem Pipefeld, leerem Summary/Test, beschädigtem Marker, doppelter ID,
  widersprüchlichen Mehrfachdefinitionen, teilweise parsebarer Markerzeile,
  Delimiterinhalt und ungültigem Output ganz ohne vollständiges Finding liefern
  keine Kandidaten. Sie ändern die bestehende Vertragsablehnung nicht.
- `tests/test_contracts.py` belegt außerdem `CONFIRMED`, `RESOLVED` und begründet
  `REJECTED` nur durch den Eigentümer sowie Ablehnung bei Codex, fremdem
  Reviewer, leerer Begründung, fehlender, doppelter oder widersprüchlicher
  Disposition und Wiederverwendung einer reservierten ID.
- `tests/test_artifact_models.py` roundtrippt jede Output- und Kandidatenphase
  durch Pythonmodell und gebündeltes JSON-Schema. Unbekannte Felder,
  Reviewerpräfixwechsel, ungültige Digests, Volloutput-/Promptfelder, absolute
  Blobpfade, bestätigte Kandidaten ohne Transitionreferenz und mehrere
  Terminalzustände werden abgewiesen.
- `tests/test_artifact_bridge.py` injiziert Fehler unmittelbar nach durablem
  Output-Capture, Kandidatenappend, autoritativem Opening und terminaler
  Disposition. Der identische Wiederholungsaufruf findet jeweils denselben
  Record, erzeugt keine zweite Revision und verweigert semantisch
  widersprüchliche Wiederholungen.
- `tests/test_artifact_replay.py` und
  `tests/test_structured_artifact_regressions.py` decken fehlende/fremde
  Messungs-, Attempt-, Diagnose-, Output- und Transitionreferenzen,
  Eigentümerwechsel, widersprüchliche Kandidatenrevisionen sowie genau einmal
  bestätigte Übernahme ab. Eine historische synthetische Kette ohne neue Typen
  liefert vor und nach der Änderung identische semantische Fakten.
- `tests/test_artifact_projection.py` setzt Prompt-, Secret- und
  Windows-/POSIX-Privatpfad-Sentinels in die umliegende Antwort und Rohquelle.
  Kandidatenprojektion und Standardabschnitte enthalten ausschließlich den
  erlaubten Findingrecord und Digests; kein Sentinel und kein Volloutput wird
  projiziert.

### Slice 2 - Reviewintegration, fingerprintübergreifende Disposition und Crash-Resume

**Exakter Änderungspfad**

- `src/orchestrator.py`
- `src/prompts.py`
- `src/review_packets.py`
- `src/workflow.py`
- `AGENTS.md`
- `README.md`
- `tests/test_orchestrator_runtime.py`
- `tests/test_language_consistency.py`
- `tests/test_prompts.py`
- `tests/test_review_packets.py`
- `tests/test_workflow.py`
- `docs/internal/slice-release-1-1e-reviewerfinding-quarantaene-und-disposition-arbeitsplan-02-reviewintegration-fingerprintubergreifende-disposition-und-crash-resume.md`

#### Integrationsschritte

1. Erweitere den bestehenden Driververtrag in `src/workflow.py` und seine
   Produktion in `src/orchestrator.py` um drei record-backed Operationen:
   erfolgreichen Revieweroutput vor Parsing erfassen, einen ungültigen Output
   samt Diagnose deterministisch quarantänisieren und offene Kandidaten samt
   Blobbytes lokal wiederherstellen. Die Orchestratorimplementierung findet
   Messrecord und terminalen Attempt eindeutig über Run, Work Unit, Reviewer,
   Operation, Inputdigest und ursprünglichen Fingerprint, materialisiert
   `<output-sha256>.txt` create-only und prüft vorhandene Bytes vor Verwendung.
2. Ordne `src/workflow.py::_run_review()` neu, ohne die fachliche
   Reviewerreihenfolge zu ändern: Nach Rückkehr von `invoke_reviewer()` folgt
   sofort Output-Capture, dann Normalisierung und strikte Vertragsprüfung. Bei
   jedem endgültigen `ContractValidationError` wird zuerst die bestehende
   Diagnose gebunden und anschließend ausschließlich aus dem gespeicherten
   Output die enge Kandidatenextraktion ausgeführt. Review/Approval und
   autoritative Findingtransition bleiben in diesem Zweig vollständig aus.
3. Ersetze die attempt-logbasierte Quelle in
   `src/orchestrator.py::recover_failed_reviewer_output()` für neue Records durch
   den digestgeprüften Outputblob. Alte Ketten behalten ihren bisherigen engen
   Legacy-Recoverypfad und erzeugen keine Kandidaten. Lokale Neuvalidierung,
   Direkt-Resume und Watcher-Fortsetzung führen dieselbe Capture-/Quarantäne-
   Idempotenz aus und rufen den Provider-Fake nach durablem Output nicht erneut
   auf.
4. Projiziere vor jedem Review aus der Recordkette die offenen Kandidaten und
   übergib nur die des aktuellen Eigentümers an Contract, Prompt und Paket. Ein
   Claude-Kandidat erzwingt eine Claude-Dispositionsrunde vor Antigravity; ein
   Antigravity-Kandidat entsprechend eine Antigravity-Runde vor Bindung oder
   Abschluss. Fingerprintwechsel ändert nur den aktuellen Paketfingerprint,
   nicht Kandidatenidentität, reservierte ID oder ursprüngliche Provenienz.
5. Ergänze `src/review_packets.py::build_review_packet()` um einen kanonischen
   Abschnitt `finding_candidates`. Er enthält sortiert genau den gespeicherten
   Findingrecord, Candidate-/Output-/Diagnosedigests, ursprünglichen und aktuellen
   Fingerprint sowie eine hart begrenzte, digestgebundene Markerzeile. Das Paket
   lehnt fremde Reviewer, doppelte IDs, offene Kandidaten ohne Quellrecord und
   Sentinel-/Privatpfadverletzungen ab und übernimmt weder Prompt noch
   historischen Volloutput.
6. Ergänze `src/prompts.py` um reservierte IDs und den exakten
   `FINDING_CANDIDATE`-Marker. Die nächste neue Findingnummer überspringt
   autoritative und quarantänisierte IDs. Ein positiver Vertrag erfordert für
   jeden angebotenen eigenen Kandidaten genau eine gültige Disposition; der
   Reviewer darf weder Kandidaten der anderen Rolle noch Codex disponieren.
7. Wende eine validierte Disposition vor dem normalen Reviewappend über die
   Slice-1-Writer an. Bei `CONFIRMED` wird das gespeicherte Finding einmal
   geöffnet und fließt als normales eigenes offenes Finding in die bestehende
   Approvalregel; `RESOLVED` und `REJECTED` bleiben dauerhafte
   Kandidatenentscheidungen. Lade danach die Recordkette erneut und verweigere
   positive Reviewpersistenz, Antigravity-Fortschritt, Commitbindung und
   Completion, solange ein eigener offener oder halb übernommener Kandidat
   verbleibt.
8. Erweitere `src/orchestrator.py::assert_structured_decision_context()` und die
   bestehenden Pending-/Resume-Durchstiche nur um aus Records abgeleitete
   Kandidateninvarianten. Es entsteht kein Candidate-Feld in
   `WorkflowHistory`, `.orchestrator/state.json` oder Checkpoints. Ein Crash
   nach Outputpersistenz, Kandidatenpersistenz, Opening-Transition oder während
   der terminalen Disposition konvergiert über Record- und Idempotenzschlüssel
   auf denselben Zustand, bevor der nächste externe Seiteneffekt zugelassen
   wird.
9. Ergänze den aktiven Reviewer-Ausgabevertrag in `AGENTS.md` und die
   öffentliche Vertragstabelle in `README.md` synchron um exakt
   `FINDING_CANDIDATE: <ID> | CONFIRMED|RESOLVED|REJECTED | <rationale>`, die
   Eigentümerregel sowie den Hinweis, dass offene eigene Kandidaten-IDs bei der
   nächsten `NEW_FINDING`-ID übersprungen werden. `CLAUDE.md`, `CODEX.md` und
   `ANTIGRAVITY.md` bleiben unverändert, weil sie keine Markergrammatik
   ausschreiben, bereits normativ auf `AGENTS.md` verweisen und keine neue
   Rollenbefugnis erhalten. Sichere in `tests/test_language_consistency.py`,
   dass Marker, drei Dispositionswerte, Eigentümerschaft und Reserved-ID-Skip in
   `AGENTS.md`, `README.md` und dem von `build_v3_review_contract()` gerenderten
   Vertrag übereinstimmen; damit kann der Prompt-/Parserpfad nicht ohne die
   kanonischen Instruktionen aktiviert werden.

#### Fokussierte synthetische Akzeptanztests

- Alle Durchstiche verwenden gespeicherte synthetische Reviewerantworten,
  temporäre Artifact-Stores, Fake-Adapter/-Driver und injizierte Append-/
  Checkpointfehler. Kein Test startet einen echten Provider, verwendet Netz oder
  Zugangsdaten oder wartet real.
- `tests/test_workflow.py` reproduziert exakt den Claude-Realfall: erfolgreicher
  terminaler Fake-Attempt, gültiges `NEW_FINDING`, ungültiges
  `FINDING_STATUS`. Der Gesamtvertrag bleibt abgelehnt; genau ein `C-*`-Kandidat
  ist an Outputdigest, Inputdigest, ursprünglichen Fingerprint, Providerabschluss
  und Diagnose gebunden. Derselbe Durchstich für Antigravity erzeugt genau einen
  eigenen `A-*`-Kandidaten.
- Ein vollständig gültiger Reviewvertrag läuft weiterhin ausschließlich über
  `ReviewPayload` und normale `FindingTransitionPayload`-Records. Der vorher
  erfasste Output erzeugt weder Diagnose noch Kandidat noch doppelte
  Findingöffnung.
- Lokale Neuvalidierung desselben gespeicherten Outputs nach injizierter
  Parserkorrektur startet den Provider-Fake nullmal, erzeugt Capture und Kandidat
  höchstens einmal und kann weder Kandidat noch reservierte ID durch einen nun
  gültigen Vertrag umgehen.
- Parametrisierte Crashdurchstiche brechen nach durablem Output, nach
  Kandidatenpersistenz, nach bestätigender Opening-Transition und beim
  terminalen Dispositionsappend ab. Direkt-Resume und ein neuer
  Workflow-/Watcher-Driver finden jeweils genau einen Kandidaten und genau eine
  gegebenenfalls autoritative Findingöffnung; ein
  durable-but-reported-failed Append wird nicht dupliziert.
- Bei einem echten Fingerprintwechsel bleibt dieselbe Candidate-ID reserviert.
  Claude kann weder positiv freigeben noch `C-*` neu verwenden, bevor es seinen
  Kandidaten bestätigt, als behoben schließt oder begründet verwirft;
  Antigravity wird vorher nicht gestartet. Entsprechende `A-*`-Fälle blockieren
  Antigravity-Approval, Binding und Completion.
- Je ein Durchstich für `CONFIRMED`, `RESOLVED` und `REJECTED` belegt dauerhafte,
  idempotente und gegenseitig widerspruchsfreie Disposition. Bestätigung öffnet
  das unveränderte Finding genau einmal und verlangt danach dessen normalen
  Ledger-Lebenszyklus; Schließen und Verwerfen erzeugen keine
  Findingtransition.
- Codex, Claude für `A-*`, Antigravity für `C-*`, ein sachfremder neuer Review
  und eine Korrekturrunde können fremde Kandidaten weder disponieren noch mit
  neuen Nummern überschreiben. Fehlende oder mehrdeutige Disposition bleibt eine
  Vertragsablehnung.
- `tests/test_review_packets.py` und `tests/test_prompts.py` prüfen kanonische,
  byteidentische Kandidatenpakete bei gleichem aktuellen Fingerprint, explizite
  ursprüngliche Fingerprint-/Digestbindung und reservierte nächste IDs. Prompt-,
  Secret-, privater POSIX-/Windows-Pfad- und historischer Volloutput-Sentinel
  erscheint weder im Kandidatenrecord, Standardlog noch im kompakten Paket.
- `tests/test_language_consistency.py` prüft den exakten
  `FINDING_CANDIDATE`-Marker und die Reserved-ID-Skip-Regel gegen `AGENTS.md`,
  `README.md` und einen gerenderten Claude-/Antigravity-Vertrag. Zusätzlich
  belegt der Test, dass die drei rollenspezifischen Root-Dateien weiterhin auf
  `AGENTS.md` als gemeinsame Vertragsquelle verweisen, statt eine zweite
  Markergrammatik zu führen.
- `tests/test_orchestrator_runtime.py` lädt eine historische Kette ohne
  Kandidaten über Direkt-Resume und Watcherpfad byte-/semantisch stabil. Sie
  erfindet keine Kandidaten, bewertet keine alte Freigabe neu und behält den
  bestehenden loggebundenen Kompatibilitäts-Recoverypfad.

## Abgrenzung und Stopbedingungen der Umsetzung

- Nicht Bestandteil sind P2-FU-035, P2-FU-021, P2-FU-032, P2-FU-034,
  P2-FU-013, P2-FU-018, P2-FU-025 und P2-FU-020, neue Reviewer,
  Reviewerreihenfolge, allgemeine Event-/Logging-/Quarantäneplattform,
  Agenten-JSON als Ersatz des Reviewvertrags sowie Inbox-/Outboxabschluss,
  Archivierung, Push oder Merge.
- Die Umsetzung hält resumierbar an, wenn der explizite `NEW_FINDING`-Marker
  nicht ohne freie-Prosa-Heuristik isoliert werden kann, die eindeutige
  Messungs-/Attemptbindung am Capture-Punkt fehlt oder ein Kandidat nur durch
  eine autoritative Findingtransition aus dem ungültigen Vertrag erzeugt werden
  könnte.
- Sie hält ebenfalls an, wenn ein offener Kandidat nicht vor fremdem
  Providerstart, positiver Reviewpersistenz und Commitbindung fail-closed
  erzwungen werden kann, Modell/Schema/Replay/Writer des neuen Recordtyps nicht
  gemeinsam in Slice 1 geschlossen werden können, ein notwendiger produktiver
  Pfad nicht im exakten Slice-Scope steht oder die Grenze von sieben produktiven
  Dateien in einem Slice überschritten würde. Der kleinste nutzbare Folgeschnitt
  wäre dann Slice 1 allein als inert geschlossener Record-/Parservertrag ohne
  aktivierten Workflowpfad; es erfolgt keine informelle Scopeerweiterung.

## Orchestrator-Prüfprotokoll

### Review-Feedback von Claude

<!-- audit:claude-review:begin -->
### Ereignis 2: Runde 1

- Reviewer: `claude`
- Freigabe: `NO`
- Validierungsbindung: `plan-validation-59b63c6709cb`
- Testdateien: keine
- Eigene Findings: `C-01`

<!-- artifact-records:claude-review:begin -->
Semantischer Record-Digest: `46ed61c384040ffe22e3fdd2a96f09e24e8956c9a90e35b004013d9f6bb1b5b4`

- 10. `ar1-62ee0038d71907708dc0327488d655097c96deea985b97e9d43e9fe5caeeeb40`: `denied`; Work-Unit `1`; Findings `C-01`; Fingerprint `59b63c6709cb2e6d3bdbf73b57917c96baed1ed581bb1b5feda529dc09b7456c`
<!-- artifact-records:claude-review:end -->
<!-- audit:claude-review:end -->

### Review-Feedback von Antigravity

<!-- audit:antigravity-review:begin -->
Noch kein strukturiertes Reviewereignis.

<!-- artifact-records:antigravity-review:begin -->
Semantischer Record-Digest: `46ed61c384040ffe22e3fdd2a96f09e24e8956c9a90e35b004013d9f6bb1b5b4`

Keine Antigravity-Review-Records.
<!-- artifact-records:antigravity-review:end -->
<!-- audit:antigravity-review:end -->

### Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
Noch keine strukturierten Codex-Antworten.

<!-- artifact-records:codex-responses:begin -->
Semantischer Record-Digest: `46ed61c384040ffe22e3fdd2a96f09e24e8956c9a90e35b004013d9f6bb1b5b4`

- 16. `ar1-2fd8aa6966e0623d00d9530019d0035eea10bbc64619caae470c6bb70646ded4`: `C-01` `responded` durch `codex`; `BLOCKER` / `open` — ACCEPTED: Plan ergänzt AGENTS.md und README.md als tatsächliche Grammatikquellen sowie den Driftguard; CLAUDE.md, CODEX.md und ANTIGRAVITY.md verweisen lediglich auf AGENTS.md. Slice 2 bleibt mit sechs produktiven Dateien innerhalb der Grenze. Der Handoff-Parser und seine acht fokussierten Tests bestehen.
<!-- artifact-records:codex-responses:end -->
<!-- audit:codex-responses:end -->

### Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### Ereignis 1: `plan-validation-59b63c6709cb`

- Diff-Fingerprint: `59b63c6709cb2e6d3bdbf73b57917c96baed1ed581bb1b5feda529dc09b7456c`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: internal plan contract passed
- Ausgabedigest: `a5dadc65141394a83216c8da348fb2f9901b55c7560515eb95c0eee5a4f8f7cc`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| internal:work-plan-contract | PASS | 0 | slices=1; planned_paths=1; changed_paths=1; future_slices=2; work_plan=docs/internal/release-1-1e-reviewerfinding-quarantaene-und-disposition-arbeitsplan.md |

### Ereignis 3: `plan-validation-417636129a5a`

- Diff-Fingerprint: `417636129a5a8d44f81401da82b6070478ea026f8d139bb4455d6f38a7224904`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: internal plan contract passed
- Ausgabedigest: `a5dadc65141394a83216c8da348fb2f9901b55c7560515eb95c0eee5a4f8f7cc`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| internal:work-plan-contract | PASS | 0 | slices=1; planned_paths=1; changed_paths=1; future_slices=2; work_plan=docs/internal/release-1-1e-reviewerfinding-quarantaene-und-disposition-arbeitsplan.md |

<!-- artifact-records:validation-attestation:begin -->
Semantischer Record-Digest: `46ed61c384040ffe22e3fdd2a96f09e24e8956c9a90e35b004013d9f6bb1b5b4`

- 2. `ar1-1f68e979ac76dbd14447c1ac8f1dc2ab3eb812134a4789fbd046ee33422446ad`: Providerinput `codex/codex_plan` = `allowed`; local_input_chars `22604/4000000`, local_input_bytes `22716/16000000`; local_input_digest `ef32cc9f6db92fa5628ca8b0210aac86344615f43706737315c5603eac95bec5`, Policy `603e75566ee1fad99cd271d52bc8c2814387f55bf526699188eead5156cd325e`, Übergang `e6967a71364caa196b8f73a7e732979e81008729803cc3366cb5470382926a8e`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, local_input_largest_component `stdin_prompt`; local_input_component_count `1`; Komponenten `stdin_prompt=22604/22716`
- 6. `ar1-903fbb82b56af465c64774390aa23692fc281829bea90708907f7a92f8e4cfbf`: Attestierung durch `orchestrator`; Fingerprint `59b63c6709cb2e6d3bdbf73b57917c96baed1ed581bb1b5feda529dc09b7456c`
  - `pass` / Exit `0` / Output `a5dadc65141394a83216c8da348fb2f9901b55c7560515eb95c0eee5a4f8f7cc`: `argv` [`internal:work-plan-contract`]
- 7. `ar1-84cf906164aab4cf73f91648ffb3cfe433d18bef5cc691a51f99236f43165eaa`: Providerinput `claude/claude_plan_review` = `allowed`; local_input_chars `64667/4000000`, local_input_bytes `65121/16000000`; local_input_digest `b755f795feb94cf251d6ee0bda1d56ef6ddb086d644b446ff5575dcc01f3b6ec`, Policy `603e75566ee1fad99cd271d52bc8c2814387f55bf526699188eead5156cd325e`, Übergang `2147c0616ba82e01201c16cd9a046f4d7fdef92b3bbd69c2212fe58f0e99c90e`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, local_input_largest_component `packet_chunk_002`; local_input_component_count `7`; Komponenten `packet_chunk_001=23942/24085, packet_chunk_002=23951/24196, packet_chunk_003=15063/15129, packet_manifest=563/563, system_policy=660/660, response_schema=146/146, start_directive=342/342`
- 12. `ar1-d03c015e53e1d2a9ce53b347e6eeae4f160380d39b385d0f4305e95bcbe117cd`: Providerinput `codex/codex_plan_revision` = `allowed`; local_input_chars `25390/4000000`, local_input_bytes `25510/16000000`; local_input_digest `678514789174e5402d47dd14cdabadbe2e34fd369459fede06fecdbf99c106b7`, Policy `603e75566ee1fad99cd271d52bc8c2814387f55bf526699188eead5156cd325e`, Übergang `05f7525f77cb68bcbb14078ccd42da419eb49c29c1f93aa86501f4d560dffbe1`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, local_input_largest_component `stdin_prompt`; local_input_component_count `1`; Komponenten `stdin_prompt=25390/25510`
- 17. `ar1-4f49c24eb6e18c555cfcd2da5a3b82042efc59370179edfce6a0020ea77c7c15`: Attestierung durch `orchestrator`; Fingerprint `417636129a5a8d44f81401da82b6070478ea026f8d139bb4455d6f38a7224904`
  - `pass` / Exit `0` / Output `a5dadc65141394a83216c8da348fb2f9901b55c7560515eb95c0eee5a4f8f7cc`: `argv` [`internal:work-plan-contract`]
- 18. `ar1-54b53994f889b300d8a1a1a55e0cab380463da4a2cd6cfee3db4e0b99d5ea8c1`: Providerinput `claude/claude_plan_review` = `allowed`; local_input_chars `81885/4000000`, local_input_bytes `82381/16000000`; local_input_digest `c1bdf87c08ed47c87d8f8b1346d1a570aa959714ac1fbdada77691531b78b91b`, Policy `603e75566ee1fad99cd271d52bc8c2814387f55bf526699188eead5156cd325e`, Übergang `cf2d147b69aa7b35cb0a23b6ffe0c70a1b1b226b3eacf00d13aceb86a26cc665`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, local_input_largest_component `packet_chunk_002`; local_input_component_count `8`; Komponenten `packet_chunk_001=23912/24032, packet_chunk_002=23959/24204, packet_chunk_003=23662/23789, packet_chunk_004=8496/8500, packet_manifest=708/708, system_policy=660/660, response_schema=146/146, start_directive=342/342`
- Providerattempt-Summe Run `watch-20260822-071740.673497Z-01b5424b510e` / Operation `provider-operation-0a3936b8cdc720caa6c55cbaa46cba8bc1e7411098cf798555dad237efa22f99` (`claude/claude_plan_review`): Attempts `1`, offen `0`, Duration `129.167077` (bekannt `1`, unbekannt `0`); input_tokens=sum:12,known:1,unknown:0; tool_input_tokens=sum:0,known:0,unknown:1; cache_read_input_tokens=sum:83593,known:1,unknown:0; cache_creation_input_tokens=sum:41898,known:1,unknown:0; thinking_tokens=sum:0,known:0,unknown:1; output_tokens=sum:11995,known:1,unknown:0; total_tokens=sum:0,known:0,unknown:1; turns=sum:7,known:1,unknown:0; cost_usd=sum:0.4570869,known:1,unknown:0
  - 20. `ar1-13d814dad491b56f47ca5febfe5db60d9e9ac53a4a22534b6a1407391cbb1049`: Attempt `1` = `succeeded`; Messung `ar1-54b53994f889b300d8a1a1a55e0cab380463da4a2cd6cfee3db4e0b99d5ea8c1`; Duration `129.16707665397553`; Fehler `none`; Usage `input_tokens=12, tool_input_tokens=unknown, cache_read_input_tokens=83593, cache_creation_input_tokens=41898, thinking_tokens=unknown, output_tokens=11995, total_tokens=unknown, turns=7, cost_usd=0.4570869`
- Providerattempt-Summe Run `watch-20260822-071740.673497Z-01b5424b510e` / Operation `provider-operation-15e31f9e202c77722ee62aed9cceda3b5255b043241696d0a7ac945122d07d1d` (`claude/claude_plan_review`): Attempts `1`, offen `0`, Duration `134.832139` (bekannt `1`, unbekannt `0`); input_tokens=sum:6,known:1,unknown:0; tool_input_tokens=sum:0,known:0,unknown:1; cache_read_input_tokens=sum:4197,known:1,unknown:0; cache_creation_input_tokens=sum:34378,known:1,unknown:0; thinking_tokens=sum:0,known:0,unknown:1; output_tokens=sum:12695,known:1,unknown:0; total_tokens=sum:0,known:0,unknown:1; turns=sum:6,known:1,unknown:0; cost_usd=sum:0.3986391,known:1,unknown:0
  - 9. `ar1-3f7e34689f50aa7c9a59ad72ac2ec54f154e0615cfa2d876366424b85d883631`: Attempt `1` = `succeeded`; Messung `ar1-84cf906164aab4cf73f91648ffb3cfe433d18bef5cc691a51f99236f43165eaa`; Duration `134.83213852497283`; Fehler `none`; Usage `input_tokens=6, tool_input_tokens=unknown, cache_read_input_tokens=4197, cache_creation_input_tokens=34378, thinking_tokens=unknown, output_tokens=12695, total_tokens=unknown, turns=6, cost_usd=0.3986391`
- Providerattempt-Summe Run `watch-20260822-071740.673497Z-01b5424b510e` / Operation `provider-operation-219b6d59f0937d9a9024721317b10515c9bb85d0dc1d75e64de5f325e38f5df7` (`codex/codex_plan`): Attempts `1`, offen `0`, Duration `478.591821` (bekannt `1`, unbekannt `0`); input_tokens=sum:0,known:0,unknown:1; tool_input_tokens=sum:0,known:0,unknown:1; cache_read_input_tokens=sum:0,known:0,unknown:1; cache_creation_input_tokens=sum:0,known:0,unknown:1; thinking_tokens=sum:0,known:0,unknown:1; output_tokens=sum:0,known:0,unknown:1; total_tokens=sum:0,known:0,unknown:1; turns=sum:0,known:0,unknown:1; cost_usd=sum:0,known:0,unknown:1
  - 4. `ar1-dcb2a5f3c0bc1ae239f75a51e4d7e9b4a6018740073dc47aceb479acc116adb7`: Attempt `1` = `succeeded`; Messung `ar1-1f68e979ac76dbd14447c1ac8f1dc2ab3eb812134a4789fbd046ee33422446ad`; Duration `478.591820592992`; Fehler `none`; Usage `unknown`
- Providerattempt-Summe Run `watch-20260822-071740.673497Z-01b5424b510e` / Operation `provider-operation-bdafdb3caae0f0d2eaee45620ebeb743c795c007a70994f0d73b4e955083a860` (`codex/codex_plan_revision`): Attempts `1`, offen `0`, Duration `209.911475` (bekannt `1`, unbekannt `0`); input_tokens=sum:0,known:0,unknown:1; tool_input_tokens=sum:0,known:0,unknown:1; cache_read_input_tokens=sum:0,known:0,unknown:1; cache_creation_input_tokens=sum:0,known:0,unknown:1; thinking_tokens=sum:0,known:0,unknown:1; output_tokens=sum:0,known:0,unknown:1; total_tokens=sum:0,known:0,unknown:1; turns=sum:0,known:0,unknown:1; cost_usd=sum:0,known:0,unknown:1
  - 14. `ar1-0a6d85600d9c2ae21ffaf90fd310fdf744722be47e2912d289be42e4d072c43e`: Attempt `1` = `succeeded`; Messung `ar1-d03c015e53e1d2a9ce53b347e6eeae4f160380d39b385d0f4305e95bcbe117cd`; Duration `209.9114746460109`; Fehler `none`; Usage `unknown`
<!-- artifact-records:validation-attestation:end -->
<!-- audit:validation-attestation:end -->

### Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
- Teständerungsfreigabe: nicht erfasst.
- Pre-Mortems: keine erfasst.

<!-- artifact-records:test-approval-premortem:begin -->
Semantischer Record-Digest: `46ed61c384040ffe22e3fdd2a96f09e24e8956c9a90e35b004013d9f6bb1b5b4`

Keine strukturierten Gates.
<!-- artifact-records:test-approval-premortem:end -->
<!-- audit:test-approval-premortem:end -->

### Findings-Lebenszyklus

<!-- audit:findings:begin -->
### `C-01` — `OPEN`

- Quelle: `claude`; Runde 1
- Klasse: `BLOCKER`
- Finding: The plan introduces a new mandatory reviewer-facing contract marker &#96;FINDING_CANDIDATE: &lt;ID&gt;
- Akzeptanztest: CONFIRMED&#124;RESOLVED&#124;REJECTED &#124; &lt;rationale&gt;&#96; (plan §9) that is parsed by &#96;src/contracts.py&#96; and offered/required by &#96;src/prompts.py&#96;, and whose absence, duplication, or contradiction invalidates "den gesamten neuen Reviewvertrag" for any reviewer with an open own candidate. This is exactly the same reviewer output grammar reproduced verbatim in this task's own "Output records" section (&#96;REVIEWER:&#96;, &#96;NEW_FINDING:&#96;, &#96;FINDING_STATUS:&#96;, &#96;FINDING_RECLASSIFIED:&#96;, &#96;PLAN_APPROVAL:&#96; / &#96;SLICE_APPROVAL:&#96; / &#96;FINAL_APPROVAL:&#96;) — the documented grammar that &#96;AGENTS.md&#96;/&#96;CLAUDE.md&#96;/&#96;CODEX.md&#96;/&#96;ANTIGRAVITY.md&#96; embed and that the repo's binding runtime policy explicitly requires to stay synchronized whenever "orchestration, prompt, parser, watch, or state changes" occur. Yet neither Slice 1's nor Slice 2's exact change-path allowlist includes any of &#96;AGENTS.md&#96;, &#96;CLAUDE.md&#96;, &#96;CODEX.md&#96;, &#96;ANTIGRAVITY.md&#96;. If the parser/prompt gain a new mandatory marker while the four synchronized instruction files remain silent about it, every future reviewer invocation (including this very system's own future self-review runs) receives stale instructions that never mention &#96;FINDING_CANDIDATE&#96;, so an owning reviewer with a genuinely open candidate could unknowingly emit a contract that the plan itself defines as invalid — a self-inflicted, permanently-blocking contract-drift bug rather than a hypothetical risk, and it is directly checkable purely from the plan's own path lists without touching the repository. This is a scope-completeness defect in the plan, not an implementation detail deferrable to a later observation, because the "harte Umfangsgrenze" already requires every produced protocol change to be internally consistent within the declared two-slice contract, and the four files are load-bearing documentation of exactly the grammar being extended. &#124; Amend Slice 2's &#96;**Exakter Änderungspfad**&#96; to add &#96;AGENTS.md&#96;, &#96;CLAUDE.md&#96;, &#96;CODEX.md&#96;, &#96;ANTIGRAVITY.md&#96; (still ≤7 productive files: 4 existing + 4 docs = 8 would breach the cap, so the plan must either fold doc updates into Slice 1's parser/prompt-adjacent step, split the marker introduction so doc sync fits the 7-file ceiling, or provide an explicit, reasoned exception showing these four files already omit reviewer-contract grammar detail and therefore need no edit); then confirm via a repository grep/test that &#96;FINDING_CANDIDATE&#96; syntax and reserved-ID next-ID-skip behavior are documented identically wherever the canonical Output-records grammar is authored, before Slice 2 is marked implementable.
- Statusbegründung: –

<!-- artifact-records:findings:begin -->
Semantischer Record-Digest: `46ed61c384040ffe22e3fdd2a96f09e24e8956c9a90e35b004013d9f6bb1b5b4`

- 11. `ar1-afa9864b2dbacdb191c9aaf75b626c416cedb2867e2ea622de4474fc18d0464a`: `C-01` `opened` durch `claude`; `BLOCKER` / `open` — The plan introduces a new mandatory reviewer-facing contract marker &#96;FINDING_CANDIDATE: &lt;ID&gt;
- 16. `ar1-2fd8aa6966e0623d00d9530019d0035eea10bbc64619caae470c6bb70646ded4`: `C-01` `responded` durch `codex`; `BLOCKER` / `open` — ACCEPTED: Plan ergänzt AGENTS.md und README.md als tatsächliche Grammatikquellen sowie den Driftguard; CLAUDE.md, CODEX.md und ANTIGRAVITY.md verweisen lediglich auf AGENTS.md. Slice 2 bleibt mit sechs produktiven Dateien innerhalb der Grenze. Der Handoff-Parser und seine acht fokussierten Tests bestehen.
<!-- artifact-records:findings:end -->
<!-- audit:findings:end -->

### Entscheidungstabelle

<!-- audit:decision-table:begin -->
| ID | Quelle | Finding | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|---|
| C-01 | claude | The plan introduces a new mandatory reviewer-facing contract marker &#96;FINDING_CANDIDATE: &lt;ID&gt; | BLOCKER | offen | offen |

<!-- artifact-records:decision-table:begin -->
Semantischer Record-Digest: `46ed61c384040ffe22e3fdd2a96f09e24e8956c9a90e35b004013d9f6bb1b5b4`

| Seq | Record | Typ | Status | Logische ID | Revision | Fingerprint |
|---:|---|---|---|---|---:|---|
| 1 | `ar1-efc21755d5814e6ad9121bcfbad6623038d3c5a490b8a95647b38103f88763cc` | `task` | `accepted` | `task-contract` | 1 | `contract:a244a1004c418293af616eebabf793bf38cdcc5c637dfdc4c58abd8885b8efc2` |
| 2 | `ar1-1f68e979ac76dbd14447c1ac8f1dc2ab3eb812134a4789fbd046ee33422446ad` | `provider_input_measurement` | `measured` | `provider-input-1-codex_plan` | 1 | `implementation:a244a1004c418293af616eebabf793bf38cdcc5c637dfdc4c58abd8885b8efc2` |
| 3 | `ar1-28f2bcc3e9b62796fb686afcad4388398dfcffbb5e7b2bee6c2be01e2d1d2980` | `provider_attempt` | `started` | `provider-operation-219b6d59f0937d9a9024721317b10515c9bb85d0dc1d75e64de5f325e38f5df7-1` | 1 | `implementation:a244a1004c418293af616eebabf793bf38cdcc5c637dfdc4c58abd8885b8efc2` |
| 4 | `ar1-dcb2a5f3c0bc1ae239f75a51e4d7e9b4a6018740073dc47aceb479acc116adb7` | `provider_attempt` | `succeeded` | `provider-operation-219b6d59f0937d9a9024721317b10515c9bb85d0dc1d75e64de5f325e38f5df7-1` | 2 | `implementation:a244a1004c418293af616eebabf793bf38cdcc5c637dfdc4c58abd8885b8efc2` |
| 5 | `ar1-9cb5c86a0eb0e6ec3e730c695de682dad6249cc3d994c47803532d52c5e58039` | `agent_result` | `ready` | `agent-1-codex_plan-1` | 1 | `contract:a244a1004c418293af616eebabf793bf38cdcc5c637dfdc4c58abd8885b8efc2` |
| 6 | `ar1-903fbb82b56af465c64774390aa23692fc281829bea90708907f7a92f8e4cfbf` | `validation_attestation` | `attested` | `plan-validation-59b63c6709cb` | 1 | `implementation:59b63c6709cb2e6d3bdbf73b57917c96baed1ed581bb1b5feda529dc09b7456c` |
| 7 | `ar1-84cf906164aab4cf73f91648ffb3cfe433d18bef5cc691a51f99236f43165eaa` | `provider_input_measurement` | `measured` | `provider-input-1-claude_plan_review` | 1 | `implementation:a244a1004c418293af616eebabf793bf38cdcc5c637dfdc4c58abd8885b8efc2` |
| 8 | `ar1-3bd25c34db01b3969c8cd00a428d03b948a982144accd2b55062e6d849a70e4b` | `provider_attempt` | `started` | `provider-operation-15e31f9e202c77722ee62aed9cceda3b5255b043241696d0a7ac945122d07d1d-1` | 1 | `implementation:a244a1004c418293af616eebabf793bf38cdcc5c637dfdc4c58abd8885b8efc2` |
| 9 | `ar1-3f7e34689f50aa7c9a59ad72ac2ec54f154e0615cfa2d876366424b85d883631` | `provider_attempt` | `succeeded` | `provider-operation-15e31f9e202c77722ee62aed9cceda3b5255b043241696d0a7ac945122d07d1d-1` | 2 | `implementation:a244a1004c418293af616eebabf793bf38cdcc5c637dfdc4c58abd8885b8efc2` |
| 10 | `ar1-62ee0038d71907708dc0327488d655097c96deea985b97e9d43e9fe5caeeeb40` | `review` | `decided` | `review-claude-1-1` | 1 | `implementation:59b63c6709cb2e6d3bdbf73b57917c96baed1ed581bb1b5feda529dc09b7456c` |
| 11 | `ar1-afa9864b2dbacdb191c9aaf75b626c416cedb2867e2ea622de4474fc18d0464a` | `finding_transition` | `recorded` | `finding-C-01` | 1 | `implementation:59b63c6709cb2e6d3bdbf73b57917c96baed1ed581bb1b5feda529dc09b7456c` |
| 12 | `ar1-d03c015e53e1d2a9ce53b347e6eeae4f160380d39b385d0f4305e95bcbe117cd` | `provider_input_measurement` | `measured` | `provider-input-1-codex_plan_revision` | 1 | `implementation:a244a1004c418293af616eebabf793bf38cdcc5c637dfdc4c58abd8885b8efc2` |
| 13 | `ar1-7b3c79f7910d5387575816b77979f976d2e8137c1e6bc0ff880c9cb08d1c9a00` | `provider_attempt` | `started` | `provider-operation-bdafdb3caae0f0d2eaee45620ebeb743c795c007a70994f0d73b4e955083a860-1` | 1 | `implementation:a244a1004c418293af616eebabf793bf38cdcc5c637dfdc4c58abd8885b8efc2` |
| 14 | `ar1-0a6d85600d9c2ae21ffaf90fd310fdf744722be47e2912d289be42e4d072c43e` | `provider_attempt` | `succeeded` | `provider-operation-bdafdb3caae0f0d2eaee45620ebeb743c795c007a70994f0d73b4e955083a860-1` | 2 | `implementation:a244a1004c418293af616eebabf793bf38cdcc5c637dfdc4c58abd8885b8efc2` |
| 15 | `ar1-5d9f6fc0f39940b7b884d5911f92ebc973c7c58984e617af47202456e185a170` | `agent_result` | `ready` | `agent-1-codex_plan_revision-2` | 1 | `contract:a244a1004c418293af616eebabf793bf38cdcc5c637dfdc4c58abd8885b8efc2` |
| 16 | `ar1-2fd8aa6966e0623d00d9530019d0035eea10bbc64619caae470c6bb70646ded4` | `finding_transition` | `recorded` | `finding-C-01` | 2 | `implementation:a244a1004c418293af616eebabf793bf38cdcc5c637dfdc4c58abd8885b8efc2` |
| 17 | `ar1-4f49c24eb6e18c555cfcd2da5a3b82042efc59370179edfce6a0020ea77c7c15` | `validation_attestation` | `attested` | `plan-validation-417636129a5a` | 1 | `implementation:417636129a5a8d44f81401da82b6070478ea026f8d139bb4455d6f38a7224904` |
| 18 | `ar1-54b53994f889b300d8a1a1a55e0cab380463da4a2cd6cfee3db4e0b99d5ea8c1` | `provider_input_measurement` | `measured` | `provider-input-1-claude_plan_review` | 2 | `implementation:a244a1004c418293af616eebabf793bf38cdcc5c637dfdc4c58abd8885b8efc2` |
| 19 | `ar1-ee660e7a5f0fc7247348d40e5d71a08fdd28c932a5c3b4fc59b04705b4535000` | `provider_attempt` | `started` | `provider-operation-0a3936b8cdc720caa6c55cbaa46cba8bc1e7411098cf798555dad237efa22f99-1` | 1 | `implementation:a244a1004c418293af616eebabf793bf38cdcc5c637dfdc4c58abd8885b8efc2` |
| 20 | `ar1-13d814dad491b56f47ca5febfe5db60d9e9ac53a4a22534b6a1407391cbb1049` | `provider_attempt` | `succeeded` | `provider-operation-0a3936b8cdc720caa6c55cbaa46cba8bc1e7411098cf798555dad237efa22f99-1` | 2 | `implementation:a244a1004c418293af616eebabf793bf38cdcc5c637dfdc4c58abd8885b8efc2` |
| 21 | `ar1-b8e325025ed20ce8f48645f552ebc53e4b015304edde24df9b1e49d226007473` | `diagnostic` | `failed` | `diagnostic-claude-1-1` | 1 | `implementation:a244a1004c418293af616eebabf793bf38cdcc5c637dfdc4c58abd8885b8efc2` |
<!-- artifact-records:decision-table:end -->
<!-- audit:decision-table:end -->

### Freigabestatus

<!-- audit:approval-status:begin -->
- Implementierung bereit: `NOT_RECORDED`
- Validierung: `PASS`
- Claude-Freigabe: `NO`
- Antigravity-Freigabe: `NOT_RECORDED`
- Red-State-Folgeslice: `NONE`
- Commit autorisiert: `NO`

<!-- artifact-records:approval-status:begin -->
Semantischer Record-Digest: `46ed61c384040ffe22e3fdd2a96f09e24e8956c9a90e35b004013d9f6bb1b5b4`

Keine Work-Unit- oder Binding-Records.
<!-- artifact-records:approval-status:end -->
<!-- audit:approval-status:end -->
