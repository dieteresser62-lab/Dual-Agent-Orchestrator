# Slice B113 – Findingwiederholungen verhindern

## Ziel und Randbedingungen

Wiederholte offene Findings werden anhand vorhandener Findingfelder erkannt,
ohne Recordschema, Recordtypen oder Reducer-Version zu ändern. Die Signaturen
sind ausschließlich abgeleitete, an die Reviewanfrage gebundene Metadaten. Die
Prüfkette im benachbarten Cookbook-Repository wurde nur gelesen.

## Exakter Änderungspfad

- `docs/internal/slice-b113-findingwiederholungen-verhindern.md`
- `schemas/native-agent-review-request-v2.schema.json`
- `schemas/native-provider-schema-exceptions-v1.json`
- `src/finding_signature.py`
- `src/native_review_contract.py`
- `src/native_review_request.py`
- `src/workflow.py`
- `src/workflow_recovery.py`
- `src/workflow_requests.py`
- `tests/fixtures/engine-dispatch-runtime-pre-b51-v1.json`
- `tests/fixtures/workflow-recovery-runtime-pre-b53-v1.json`
- `tests/test_finding_signature.py`
- `tests/test_native_provider_schema.py`
- `tests/test_native_review_contract.py`
- `tests/test_native_review_request.py`
- `tests/test_workflow_recovery_corpus.py`
- `tests/test_workflow_requests.py`

## Signatur und sichtbare Wiederholung

Die Grundsignatur besteht aus dem NFKC-normalisierten, casefolded und
leerraumkanonisierten Akzeptanztest sowie den sortierten, im Finding genannten
Repositorypfaden; die kanonische JSON-Darstellung wird mit SHA-256 gehasht. Für
die beiden am Prüfstand nachgewiesenen Wiederholungsfamilien abstrahieren enge
Mehrfachprädikate die jeweils wechselnden Vorkommenspfade. Ein nicht vollständig
passender Text fällt auf die Grundsignatur zurück, damit im Zweifel ein Duplikat
statt eines echten Befunds verloren geht.

Die Reviewanfrage enthält die Signatur jeder bekannten offenen Kennung. Eine
neue Eröffnung mit bekannter Signatur wird lokal fail-closed abgewiesen und
nennt die vorhandene Kennung. Ein weiterer Fundort wird als OPEN-zu-OPEN-
Statusänderung mit neuer Begründung auf dieser Kennung ausgedrückt. Dadurch
entsteht mit den vorhandenen Recordtypen ein sichtbarer `status_changed`-
Übergang. Eine semantisch leere Wiederholung derselben Begründung wird
abgewiesen.

## Bewusst nachgezogene Fixtures

`tests/fixtures/engine-dispatch-runtime-pre-b51-v1.json` ändert ausschließlich
die zwei gespeicherten Reviewanfragen: Beide enthalten jetzt die leere
Signaturliste und die Revieweranweisung; Request-ID, Digest und Bytezahl ändern
sich daraus deterministisch. Fehler, Checkpoints und Aufrufzahlen bleiben
unverändert.

`tests/fixtures/workflow-recovery-runtime-pre-b53-v1.json` bindet entsprechend
die neue Request-ID und den daraus folgenden Response-Digest im erfolgreichen
Recovery-Fall. Das Recovery-Ergebnis, die adoptierte Record-ID und die
Providerfreiheit bleiben unverändert. Die geschützte Provider-Kopplungs-
Baseline wurde nicht geändert.

## Messung

Auf dem Präfix von 3267 Records der Kette
`watch-20260909-043238.011217Z-51205a8861db` wurden 109 Eröffnungen und 108
eindeutige Kennungen gemessen. Die Signaturen bilden eine Gruppe mit 40
Ausführbar-Bit-Befunden, eine Gruppe mit 21 fehlenden Dokumentationsnachweisen
und 47 einzelne inhaltliche Gruppen. `C-102` und `C-106` bleiben verschieden.
Unter der neuen Regel wären damit 49 Findings entstanden.

## Validierung

Die sechs providerfreien Akzeptanzfälle sind grün. Die Slice-Stufe
`python3 -m pytest tests/ -v -m "not crash_harness"` ist mit 2068 bestandenen
und 15 abgewählten Crash-Harness-Fällen grün. Der Crash-Harness wurde nicht
ausgeführt.
