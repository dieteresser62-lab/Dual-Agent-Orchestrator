# JSON-Statusausgabe zum Statusbefehl hinzufügen

> Formales Expertenbeispiel für einen bereits freigegebenen Implementierungsauftrag. Für den normalen Inbox-Ablauf genügt eine freie Ideenbeschreibung; `TARGET_BRANCH` ist eine optionale ausdrückliche Vorgabe. Der Orchestrator erstellt Planung, Handoff und Slices automatisch.

ORCHESTRATOR_MODE: IMPLEMENT
TARGET_BRANCH: feature/status-json
TASK_SCOPE: src/status_cli.py, tests/test_status_cli.py, README.md, docs/internal/orchestrator-work-plan.md, docs/internal/slice-status-json.md

## Kontext

Das Projekt besitzt bereits einen reinen Textbefehl `status`. Automatisierungen benötigen eine stabile JSON-Darstellung, ohne die bestehende Textausgabe zu verändern.

## Ziel

Ergänze den Statusbefehl um `--json`. Mit dieser Option wird genau ein JSON-Objekt mit `status`, `version` und `checked_at_utc` ausgegeben. Ohne die Option muss die bisherige Textausgabe exakt erhalten bleiben.

## Erlaubter Scope

- `src/status_cli.py`
- `tests/test_status_cli.py`
- `README.md`
- `docs/internal/orchestrator-work-plan.md`
- `docs/internal/slice-status-json.md`

Dateien außerhalb dieser Liste dürfen nicht bearbeitet werden. Wird ein weiterer Pfad benötigt, ist anzuhalten und eine Scope-Entscheidung anzufordern.

## Anforderungen

1. `--json` mit dem vorhandenen Argument-Parser des Befehls verarbeiten.
2. Gültiges UTF-8-JSON mit deterministischen Feldnamen serialisieren.
3. `checked_at_utc` als ISO-8601-UTC-Zeitstempel mit abschließendem `Z` formatieren.
4. Exitcode und Ausgabe des vorhandenen Textmodus bytegenau kompatibel halten.
5. Fokussierte Tests für JSON-Modus, Textmoduskompatibilität und eine ungültige Option ergänzen.
6. Das Befehlsbeispiel in der README und das vorbereitete Slice-Auditdokument aktualisieren.

## Akzeptanzkriterien

- `status --json` endet mit Code 0 und gibt genau ein JSON-Objekt aus.
- Das Objekt besitzt exakt die Schlüssel `checked_at_utc`, `status` und `version`.
- Der vorhandene Befehl ohne `--json` besteht unverändert seinen Snapshot-Test.
- Ungültige Optionen liefern weiterhin den von Argument-Parser erzeugten Usage-Fehler mit einem von null verschiedenen Exitcode.
- Die konfigurierte Validierungsmatrix ist für den geprüften Diff-Fingerprint erfolgreich.
- Keine Datei außerhalb des erlaubten Scope wird geändert oder commitet.

## Validierung

- `python3 -m pytest tests/test_status_cli.py -v`
- `python3 -m pytest tests/ -v`

## Nicht-Scope

- Keine Netzwerkzustandsprüfung.
- Keine neue Abhängigkeit.
- Keine Änderung der Versionsermittlung.
- Kein Push, Merge, Release oder Deployment.

## Stopbedingungen

- Anhalten, falls das JSON-Schema ein oben nicht aufgeführtes Feld benötigt.
- Anhalten, falls die Beibehaltung der Textausgabe eine Architekturänderung außerhalb des erlaubten Scope erfordert.
- Anhalten, falls Tests einen plattformspezifischen Zeitstempelvertrag zeigen, der nicht dokumentiert ist.
