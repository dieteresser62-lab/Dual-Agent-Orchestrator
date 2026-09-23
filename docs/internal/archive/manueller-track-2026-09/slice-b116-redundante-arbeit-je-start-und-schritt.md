# Slice B116 – Redundante Arbeit je Start und je Schritt

## Ziel und Randbedingungen

Die drei gemessenen Wiederholungsquellen werden ohne Änderung an Recordschema,
Recordtypen oder Reducer-Version verkleinert. Die abgeschlossene Kette
`watch-20260909-043238.011217Z-51205a8861db` aus dem benachbarten
Cookbook-Repository wurde vor der ersten Codeänderung ausschließlich gelesen
und für reproduzierbare Laufzeitmessungen nach `/tmp` kopiert. Im Cookbook
wurde nichts verändert. Der vollständige Crash-Proof bleibt entsprechend der
Operator-Grenze unausgeführt.

## Teil 1 – Kettenvalidierung beim Start

Vor der Änderung benötigten drei unabhängige vollständige Ladevorgänge der
3661 Records auf der lokalen Prüfkopie 3,554358 s, 3,448527 s und 3,703298 s,
zusammen 10,706183 s. Jeder Vorgang trat in die Phase
`full-chain-validation` ein.

`ResumeResolution` reicht nun ausschließlich prozesslokal den tatsächlich
vollständig validierten `ArtifactStore` an die Startkomposition weiter.
Historienprojektion und Treiber übernehmen diese bereits validierte Ableitung;
bei abweichendem Repository oder Run wird fail-closed abgebrochen. Jede
explizite neue Load-/Resume-Anforderung ohne diese Startableitung führt
weiterhin eine vollständige Kettenvalidierung aus. Eine strukturelle Änderung
zwischen zwei Verbrauchern verwirft die Ableitung und erzwingt eine erneute
vollständige Prüfung.

Nach der Änderung dauerten derselbe vollständige Ladevorgang und zwei
unabhängige Anforderungen am weitergereichten Store 3,316465 s, 0,000515 s und
0,000343 s, zusammen 3,317323 s. Alle drei lieferten dieselben 3661 Records;
die vollständige Phase wurde genau einmal betreten.

## Teil 2 – Prozesslokale Codeversion

Vor der Änderung liefen acht Aufrufe über alle 73 Versionskomponenten. Der
erste dauerte 0,322900 s, die späteren Aufrufe im Median 0,313775 s; jeder
Aufruf las und hashte die Komponenten erneut.

Die Version wird nun unter einem Lock je aufgelöstem Installationsroot und
Prozesskennung einmal berechnet. Es gibt weder Datei- noch Recordcache. Nach
einem Prozesswechsel, einschließlich Fork-Vererbung, wird der Modulspeicher
verworfen. Damit erkennt ein neuer Prozess eine inzwischen geänderte
Installation weiterhin.

Nach der Änderung dauerte die erste Berechnung 0,249584 s und die sieben
Folgeaufrufe im Median 0,001475 s. Das ist für die wiederholten Aufrufe ein
Faktor 212,7 gegenüber dem vorherigen Median; alle acht Aufrufe lieferten
dieselbe Version. Ein providerfreier Subprozess-Test weist die Neuberechnung
und eine andere Version nach Änderung einer Quelldatei nach.

## Teil 3 – Wachstumscharakter des Rundenvorlaufs

Für die homogenen, nach B101 aufgezeichneten Slices 36 bis 42 beträgt der
historische Vorlauf von `SliceBoundary` bis zum ersten gestarteten
Codex-Implementierungsattempt 134,027594 s, 139,310116 s, 149,150331 s,
161,493608 s, 171,631041 s, 172,578197 s und 191,486483 s. Die lineare
Regression ergibt **+9,335484 s je Slice** bei einem Mittel von 159,953910 s.
Diese historische End-to-End-Reihe ist unveränderlich und behält daher ihren
Wachstumscharakter. Der Abstand vom Work-Unit-Record zur
Provider-Input-Messung stellte 88,4 % dieses Vorlaufs und wuchs selbst um
+8,232439 s je Slice.

Das Profil des aktuellen Codes vor der Korrektur ordnete den dominierenden
rechenbaren Anteil eindeutig zu: Von 13,592 s Replay-Validierung entfielen
13,548 s auf `_validate_required_review_authority`, davon 13,544 s auf
`_review_prefix_finding_ids`. Für jeden der 52 Reviews wurde ein vollständiges
Replay-Ergebnis für den anwachsenden Präfix aufgebaut; 102426 Aufrufe von
`canonical_json` verbrauchten dabei 7,852 s. Der aktuelle Präfixbenchmark über
Slices 36 bis 42 lag vor der Änderung bei 2,950 bis 3,957 s und wuchs um
+0,166035 s je Slice.

Die Korrektur reduziert für den bereits validierten Reviewpräfix nur noch die
Findingrecords, statt für jeden Review sämtliche Replay-Metadaten erneut
aufzubauen. Das fachliche Reduktionsergebnis bleibt identisch. Danach liegen
die Präfixmediane zwischen 0,351733 s und 0,453352 s; ihre Regression beträgt
noch **+0,016909 s je Slice**, also rund 9,8-mal weniger Zuwachs. Der
Wachstumscharakter bleibt im heutigen, mit aktuellem Code reproduzierten
Replay damit klein positiv; er ist nicht null, weil ein späterer Slice einen
größeren autoritativen Präfix enthält.

## Bewusst nachgezogene Fixture

In `tests/fixtures/production-entry-pre-b46-v1.json` wurde genau der
`protected_body_sha256` des Resume-Handlers nachgezogen. Der geschützte Zweig
reicht jetzt den vollständig validierten Start-Store über einen Observer an
die beiden nachfolgenden Verbraucher weiter. Kontrollfluss, Ergebnisorakel,
Exceptiontyp und Handlerdigest der Fixture bleiben unverändert.
`src/dry_run_scenarios.py` und seine Annahmen wurden nicht geändert.

## Exakter Änderungspfad

- `docs/internal/slice-b116-redundante-arbeit-je-start-und-schritt.md`
- `src/artifact_replay.py`
- `src/artifact_resume.py`
- `src/finding_reducer.py`
- `src/orchestrator.py`
- `src/orchestrator_version.py`
- `src/state_io.py`
- `src/workflow_production.py`
- `tests/fixtures/production-entry-pre-b46-v1.json`
- `tests/test_artifact_resume.py`
- `tests/test_orchestrator_version.py`
- `tests/test_stabilisierung_s2_transition_matrix.py`
- `tests/test_workflow_history_projection.py`

## Validierung

Die fokussierten providerfreien Akzeptanz- und Invarianzfälle decken den
einmaligen Phaseneintritt, beschädigte und zwischen Verbrauchern veränderte
Ketten, Prozessgrenzen des Versionscaches, geänderte Installationen und die
Abwesenheit des redundanten Replay-Ergebnisaufbaus ab. Die fokussierte Matrix
bestand mit 207 Tests; die nachfolgende Strukturkorrektur wurde zusätzlich mit
14 gezielten Tests bestätigt.

Die Slice-Stufe `python3 -m pytest tests/ -v -m "not crash_harness"` ist auf
dem vollständigen Endstand grün: **2077 bestanden, 15 abgewählt, 252,66
Sekunden**. Der Crash-Harness wurde nicht ausgeführt.

## Eigenreview

Der abschließende Diff-Review prüfte die normale und die direkte
Queue-Resume-Komposition, falsche Store-/Run-Bindungen, Kettendrift zwischen
Verbrauchern, explizite Reloads, parallele und neue Prozesse, die semantische
Gleichheit der Findingreduktion, Invarianz-Fixtures sowie die Grenzen für
Schema, Reducer und externe Seiteneffekte. Es blieb kein offener Befund.
