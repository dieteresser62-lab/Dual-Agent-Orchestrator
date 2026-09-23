# Slice B114 – Findingbilanz und Aufräumrunde

## Ziel und Randbedingungen

Der Orchestrator leitet die Findingbilanz je regulärem Slice aus der
strukturierten Recordkette ab und schiebt bei einem gemessenen Stau eine
reviewer-only Aufräumrunde ein. Recordschema, Recordtypen, Reducer-Version und
Findingmodell bleiben unverändert. Die Prüfstandkette im benachbarten
Cookbook-Repository wurde ausschließlich gelesen.

## Rückwirkende Rechnung

Gemessen wurde der dokumentierte Präfix von 3267 Records der Kette
`watch-20260909-043238.011217Z-51205a8861db`, nicht ihr später angewachsener
Head. Er enthält 109 Eröffnungstransitionen, 108 eindeutige Kennungen und 49
nach B113 eindeutige Signaturen. Außerdem enthält er neun reviewer-eigene
Schließungen in regulären Slice-Work-Units; daraus folgen 40 offene
Signaturrepräsentanten am Ende ohne Aufräumrunde.

Die Simulation verarbeitet Öffnungen und Schließungen in Recordreihenfolge.
Eine ausgelöste, erfolgreiche Aufräumrunde entfernt den zu diesem Zeitpunkt
offenen Satz; spätere Schließungen bereits aufgeräumter Repräsentanten werden
nicht ein zweites Mal abgezogen. Angegeben sind `(Slice: angebotene Menge)`.

| Schwelle | Aufräumrunden | Rest nach Slice 42 |
|---:|---|---:|
| 8 | 10:8, 17:8, 27:9, 35:9, 40:8 | 6 |
| 9 | 11:9, 22:10, 31:9, 37:9, 42:10 | 0 |
| 10 | 12:10, 23:10, 34:11, 40:10 | 6 |
| 11 | 14:11, 26:11, 35:11, 41:12 | 2 |
| 12 | 15:12, 27:12, 37:13 | 10 |
| 13 | 17:14, 31:13, 40:13 | 6 |
| 14 | 17:14, 32:14, 41:16 | 2 |
| 15 | 22:15, 36:16 | 13 |
| 16–17 | 27:17, 40:17 | 6 |
| **18** | **28:18, 41:20** | **2** |
| 19 | 30:19, 41:19 | 2 |
| 20–21 | 31:21 | 19 |
| 22 | 32:22 | 18 |
| 23–24 | 34:24 | 16 |
| 25–26 | 35:26 | 14 |
| 27–28 | 36:28 | 13 |
| 29–30 | 37:30 | 10 |
| 31 | 38:31 | 9 |
| 32 | 39:32 | 8 |
| 33–34 | 40:34 | 6 |
| 35–38 | 41:38 | 2 |
| 39–40 | 42:40 | 0 |

Schwelle 18 ist gewählt: 19 kostet ebenfalls zwei Runden und lässt ebenfalls
zwei Findings übrig, reagiert aber zwei Slices später. 14 erreicht denselben
Rest nur mit einer zusätzlichen Runde. 35 bis 40 sehen rechnerisch günstig
aus, lassen den Stau aber bis Slice 41 oder 42 mitwandern und verfehlen damit
den Zweck der Zwischenentlastung. Die größte gemessene 18er-Runde enthält 20
Findings und bleibt unter dem nativen Dispositionslimit von 32.

## Bauart und Geltungsbereich

Die Aufräumrunde ist rein prüfend. Sie startet direkt beim Reviewer; Codex
erhält keine Behebungseinheit und es entsteht kein Commit. Damit kann nur der
Reporting-Reviewer schließen oder reklassifizieren, und ein Review-Denial
hinterlässt keine unfreigegebenen Änderungen. Eine behebende Runde würde für
den geforderten Nie-halten-Pfad eine neue Rollback-/Commit-Sonderbehandlung
benötigen und ist deshalb nicht Bestandteil dieses Slices.

Die Einheit verwendet den mit B110 tragfähig gemachten slicelosen
Correction-Pfad: vorhandener `CorrectionWorkUnitPayload`, Finalreview-artige
Dispositionslieferung und kein neuer SliceRecord. Sie bleibt vom technischen
Finalreview-Correction-Slice unterscheidbar, weil sie die ID eines bereits
existierenden regulären Slice-Work-Units wiederverwendet.

Der Geltungsbereich ist bei Planung, Anfragebau, Change-Boundary und
Persistenz jeweils die sortierte Vereinigung der Repositorypfade, die Summary
oder Akzeptanztest der ausgewählten offenen Findings nennen. Die Reviewansicht
filtert den Branchdiff auf genau diese Pfade. Leere, fehlende, fremde oder
unvollständige Aufräumbelege sowie Stop- und Nullfortschrittsresultate beenden
nur die Aufräum-Work-Unit; sie erzeugen kein Gate. Bereits angebotene offene
Findings lösen nach Nullfortschritt nicht unmittelbar dieselbe Runde erneut
aus.

## Wirklichkeitsgetreuer Prüfstand

`tests/test_finding_cleanup.py` eröffnet in einem echten Slice 18 eindeutige
Beobachtungen mit Repositorypfaden. Der providerfreie Journey-Runner ruft
anschließend `start_finding_cleanup_work_unit` auf. Der Test weist nach, dass
die resultierende Work-Unit `kind=correction`, `step=claude_final_review`, die
ID des abgeschlossenen Slice 1 und keinen eigenen SliceRecord besitzt. Er
prüft außerdem die 18 Findingpfade in `paths` und `authorized_paths`, das
Fehlen eines Reviewpakets und jedes Wartezustands sowie den anschließenden
Start des echten Plan-Slice 2. Ein separater Journey-Fall ohne Findings weist
nach, dass keine Correction-Einheit eingefügt wird.

## Bewusst nachgezogene Prüfstandannahmen

`src/dry_run_scenarios.py` führt dieselbe echte slicelose Einheit zwischen
regulären Slices aus und projiziert nur deren ausgewählte Findings in die
Finalreview-artige Dispositionsanfrage. Die Importinventare der Baseline-,
Auditprojektions- und Validation-Evidence-Tests wurden um die neuen
einseitigen Abhängigkeiten auf `finding_cleanup` beziehungsweise
`finding_reducer` erweitert; ihre übrigen Architekturannahmen bleiben
unverändert. Die quellgebundenen Engine-, Production-Transition-, Baseline-
und Gate-Inventare wurden auf die neuen, von ihren Ratchets vollständig
erfassten Kontrollflusskanten nachgezogen; ihre historischen Source-Anker und
providerfreien Ergebnisorakel bleiben unverändert.

## Exakter Änderungspfad

- `docs/internal/slice-b114-findingbilanz-und-aufraeumrunde.md`
- `src/dry_run_scenarios.py`
- `src/finding_cleanup.py`
- `src/finding_reducer.py`
- `src/orchestrator.py`
- `src/workflow.py`
- `src/workflow_audit_projection.py`
- `src/workflow_baseline.py`
- `src/workflow_persistence.py`
- `src/workflow_production.py`
- `src/workflow_requests.py`
- `src/workflow_state.py`
- `src/workflow_validation_evidence.py`
- `tests/fixtures/engine-dispatch-runtime-pre-b51-v1.json`
- `tests/fixtures/engine-dispatch-static-pre-b51-v1.json`
- `tests/fixtures/production-transition-pre-b48-v1.json`
- `tests/fixtures/workflow-baseline-static-pre-b55-v1.json`
- `tests/test_engine_dispatch_corpus.py`
- `tests/test_finding_cleanup.py`
- `tests/test_finding_reducer.py`
- `tests/test_production_transition_corpus.py`
- `tests/test_stabilisierung_s2_transition_matrix.py`
- `tests/test_workflow_audit_projection.py`
- `tests/test_workflow_baseline.py`
- `tests/test_workflow_baseline_corpus.py`
- `tests/test_workflow_transition_matrix.py`
- `tests/test_workflow_validation_evidence.py`

## Validierung

Die providerfreien Akzeptanzfälle decken Bilanz, Schwelle, exakten
Finding-Geltungsbereich, echte slicelose Einheit, Nullfortschritt ohne Halt,
Fortsetzung mit dem nächsten Slice und den unveränderten Lauf ohne Stau ab.

`python3 -m pytest tests/ -v -m "not crash_harness"`:
**2073 bestanden, 15 abgewählt, 444,32 Sekunden**. Der separate vollständige
Crash-Proof wurde entsprechend der Operator-Grenze und dieses Auftrags nicht
ausgeführt.
