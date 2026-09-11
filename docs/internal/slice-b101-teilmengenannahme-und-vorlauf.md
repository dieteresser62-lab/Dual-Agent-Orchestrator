# Slice B101 – Teilmengenannahme und Vorlauf

## Ziel und Randbedingungen

Die drei Teile des Auftrags wurden in der vorgegebenen Reihenfolge bearbeitet.
Die verkleinerte Reviewanfrage bleibt erhalten. Recordschema, Recordtypen und
Reducer-Version wurden nicht geändert. Die Cookbook-Kette wurde ausschließlich
gelesen; gemessen wurde auf einer Kopie unter `/tmp`.

## Teil 1 – Anfragegebundener Review und vollständiges Ledger

### Entscheidung

Der `ReviewPayload` bleibt die unveränderte, anfragegebundene Antwort des
Reviewers. Das vollständige Ledger ist eine nachgelagerte Projektion und wird
nicht als angeblicher Inhalt der Providerantwort persistiert. Dadurch bleiben
Recovery, Request-ID, Response-Digest und Reviewrecord beweisbar an dieselben
Providerbytes gebunden. Commit- und Red-State-Prüfungen projizieren den
anfragegebundenen Payload anhand seiner `finding_ids` aus dem vollständigen
Laufzeitergebnis und vergleichen anschließend weiterhin streng alle übrigen
Reviewfelder.

`ReviewFindingMerge` macht die beiden Größen im Typsystem sichtbar:
`request_bound` bezeichnet die validierte Reviewerantwort,
`complete_ledger` die daraus und aus dem autoritativen Bestand gebildete
Laufzeitprojektion. Die Engine verwendet ausschließlich `complete_ledger` für
History und Commitanforderung; die Persistenz verwendet weiterhin die
anfragegebundene Antwort.

### Systematisch geprüfte Stellen

- `artifact_bridge.review_payload()` und
  `review_payload_matches_result()`: korrekt anfragegebunden; unverändert.
- `artifact_bridge.review_payload_matches_complete_result()`: neu als explizite
  Brücke vom vollständigen Laufzeitergebnis zum anfragegebundenen Payload.
- `WorkflowEngine._dispatch_native_review()` und
  `_merge_review_request_subset()`: Persistenz erhält die anfragegebundene
  Antwort, History erhält ausschließlich das vollständige Ledger; korrigiert
  durch den typisierten gemeinsamen Merge.
- `WorkflowEngine._apply_review_result()`, `_record_review()` und `_commit()`:
  verwenden bewusst das vollständige Ledger für Statusentscheidung,
  offene Findings und Commitanforderung; kein weiterer Befund.
- `WorkflowPersistence.persist_native_review_contract()` und
  `_persist_review_finding_transitions()`: schreiben bewusst nur die
  anfragegebundene Antwort beziehungsweise deren Delta; vor dem Schreiben nun
  zusätzlich gegen das autoritative vollständige Ledger geprüft.
- `WorkflowGitCommit._resolve_structured_binding()`,
  `git_service._validate_authorization()` und die Commitautorisierungsprüfung
  in `AuditProjection`: verglichen vorher
  ungleiche Größen und verwenden jetzt den vollständigen Vergleichsadapter.
- `workflow_recovery` (record-ahead und provider-response recovery): der
  rekonstruierte Result stammt aus denselben kompakten Request-/Responsebytes;
  der strenge anfragegebundene Vergleich ist korrekt und blieb unverändert.
- `workflow_audit_projection.project_latest_review()`: rekonstruiert den
  Result direkt aus dem Reviewpayload; der strenge anfragegebundene Vergleich
  ist korrekt und blieb unverändert.
- `audit_trail`-Rendering und `workflow_audit_projection`-Historien:
  `result.findings` wird nur dargestellt beziehungsweise in eine
  Historyprojektion übernommen, nicht mit einem anders geschnittenen Payload
  gleichgesetzt; kein Befund.
- `dry_run_scenarios`: führt ein eigenes vollständiges dauerhaftes Ledger und
  mischt die anfragegebundene Antwort bewusst hinein; kein Befund.
- Codex-Implementiererpfade (`merge_request_result`,
  `persist_native_implementer_contract`): besitzen einen anderen,
  antwort-/dispositionsgebundenen Vertrag und waren vom Reviewerfehler nicht
  betroffen.

## Teil 2 – Prüfen vor Publizieren

`persist_native_review_contract()` rekonstruiert jetzt vor jeder Publikation
den autoritativen Findingbestand aus der validierten Recordkette und führt die
gleiche Teilmengen-/Kollisionsprüfung wie die Engine aus. Erst danach entstehen
ProviderContent, ReviewPayload, ReviewAnchor, ReviewValidationBinding,
FindingTransition und WorkflowEvent. Eine ungültige Antwort verändert die
Kette daher nicht.

Als zweite Verteidigungslinie weist die Persistenz jeden neuen `opened`-Übergang
auf eine bereits vergebene Finding-ID ab; die Fehlermeldung nennt die ID. Eine
exakt identische Wiederholung des aktuellen Übergangs bleibt für record-ahead
Recovery idempotent.

Die Korrekturrunde B102 trennt diese Schreibregel ausdrücklich vom Replay:
Eine bereits vorhandene erneute Eröffnung derselben logischen ID wird beim
Abspielen toleriert und als `DUPLICATE-FINDING-OPENING` im deterministischen
Reducerergebnis diagnostiziert. Die Diagnose bindet Kennung, beide Record-IDs,
beide Finding-Revisionen und beide Work-Units. Die erste Eröffnung bleibt die
Kopferöffnung; die spätere Konflikteröffnung kann die etablierte Identität damit
weder im Gesamtledger noch in einer Work-Unit-Teilprojektion ersetzen.

`artifact_replay` lässt spezifische `ArtifactReplayError`-Diagnosen des
Finding-Reducers unverändert passieren. Nur eine tatsächlich fehlende Finding-ID
im Reviewpräfix wird weiterhin als
`review finding transition set is incomplete` gemeldet.

Geprüfte weitere Records zwischen Providerantwort und Domänenannahme:

- `ProviderContentPayload`, `ReviewPayload`, `ReviewAnchorPayload`,
  `ReviewValidationBindingPayload`, `FindingTransitionPayload` und
  `WorkflowEventPayload` lagen im betroffenen Persistenzsink und wurden
  gemeinsam hinter die Vorprüfung verschoben.
- `ProviderAttemptPayload` und `ProviderInputMeasurementPayload` beschreiben
  Start, Messung und terminalen Zustand eines Provideraufrufs, nicht dessen
  Reviewentscheidung. Sie dürfen und müssen den Versuch auch bei verworfener
  Antwort dokumentieren; kein Befund.
- Die rohe Response-Datei wird als technische Recovery-Eingabe vor der
  Domänenannahme erfasst, ist aber kein Record und keine Reviewtatsache. Der
  autoritative `ProviderContentPayload` entsteht erst nach Annahme; kein
  weiterer Recordtyp war betroffen.
- Validation-, Gate-, Commit- und Bindingrecords entstehen in getrennten,
  vorgelagerten oder nachgelagerten Orchestratorgrenzen; kein Befund.

## Teil 3 – Messung und gezielte Optimierung

Gemessen wurde der vollständige In-Memory-Schritt
`merge_structured_record_sections()` mit der 6.033.299 Byte großen echten
Auditdatei und einem Replay der ersten 2716 Records. Die Baseline lief aus dem
unveränderten Commit `c978032`; die Kette lag nur als Kopie unter `/tmp` vor.

### Vorher

- Wanduhr: 283,418 s
- Ausgabe: 6.016.820 Byte
- `finalize_projection_document()`: 280,797 s, 99,08 % kumulativ
- `re.Pattern.sub` aus der Rehydrierungsschleife: 278,362 s, 98,22 % kumulativ
- `_managed_ranges()`/Markdown-Parsing: 2,451 s, 0,86 % kumulativ

Die Prozentwerte sind verschachtelte kumulative Zeiten. Der dominante Befund
waren 28.642 vollständige Regex-Umschreibungen des rund 6-MB-Dokuments.

### Änderung und Nachmessung

Die Rehydrierung erkennt registrierte zwölfstellige Kurzreferenzen nun mit
einem einzigen dokumentweiten Regex-Durchlauf und ersetzt sie über die bereits
vorhandene `short_to_full`-Abbildung. Erkennungsgrenzen, Kollisionsprüfung,
Erstreihenfolge und Evidenztabelle bleiben unverändert.

- Wanduhr nachher: 3,919 s
- Ausgabe nachher: 6.016.820 Byte
- Beschleunigung: 72,3-fach
- Reduktion: 98,62 %

Der Regressionstest vergleicht die neue Ein-Pass-Rehydrierung bytegenau mit
dem bisherigen Algorithmus über 256 Bindungen und erzwingt genau einen
Dokumentscan.

## Invarianzanker

Im ursprünglichen B101-Stand blieben `src/artifact_replay.py`,
`tests/fixtures/replay-rejection-corpus-v1.json`,
`tests/fixtures/native-provider-projection-baseline-v1.json` und
`tests/fixtures/engine-dispatch-static-pre-b51-v1.json` unverändert.
Bewusst nachgezogen wurden nur der Commit-Boundary-Korpus und der zugehörige
strikte Body-Digest, weil die Commitprüfung nun ausdrücklich den
Complete-Result-Adapter benennt, sowie der Finding-Reducer-Korpus für die neue
globale ID-Eindeutigkeit.

B102 ändert `src/artifact_replay.py` nun gezielt, um spezifische Reducerfehler
nicht länger als unvollständige Übergangsmenge zu melden; der zeilengebundene
Replay-Ablehnungskorpus selbst bleibt unverändert.

## Validierung

Die fokussierten Teil-1/2-Prüfungen liefen mit 289 bestandenen Tests; die
Commit-, Recovery-, statischen und Providerprojektions-Korpora anschließend
mit weiteren 111 bestandenen Tests. Die abschließende Slice-Stufe
`python3 -m pytest tests/ -v -m "not crash_harness"` war grün:

- 2025 bestanden
- 15 `crash_harness`-Fälle deselektiert
- pytest-Laufzeit: 383,01 s
- Wanduhr: 384,93 s

Der Crash-Harness wurde auftragsgemäß nicht ausgeführt.

## Korrekturrunde B102

Die echte Cookbook-Kette
`watch-20260909-043238.011217Z-51205a8861db` wurde ausschließlich auf einer
Kopie unter `/tmp` geprüft. Alle 2734 Records laden und spielen wieder ab; auch
alle 36 Reviewverträge werden projiziert. Die abgeleitete Diagnose weist C-01
Revision 1 / Work-Unit 2 als Kopferöffnung und Revision 34 / Work-Unit 34 als
Konflikteröffnung aus.

Die abschließende Slice-Stufe
`python3 -m pytest tests/ -v -m "not crash_harness"` war grün:

- 2026 bestanden
- 15 `crash_harness`-Fälle deselektiert
- pytest-Laufzeit: 422,69 s

Der Crash-Harness wurde auch in der Korrekturrunde nicht ausgeführt.
