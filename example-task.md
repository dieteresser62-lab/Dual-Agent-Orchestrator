# JSON-Statusausgabe zum Statusbefehl hinzufügen

> Formales Beispiel für einen direkten Umsetzungsauftrag ohne vorherigen Planlauf – für Fortgeschrittene und maschinell erzeugte Aufträge. Im normalen Betrieb genügt eine Idee in normaler Sprache; Plan, Übergabe und Arbeitspakete erzeugt der Orchestrator selbst (siehe [Einrichtung](docs/reference/einrichtung.md)). `TARGET_BRANCH` ist optional. Die Datei dient außerdem dem Probelauf `run_task --dry-run --task-file example-task.md`.

ORCHESTRATOR_MODE: IMPLEMENT
TARGET_BRANCH: feature/status-json
TASK_SCOPE: src/status_cli.py, tests/test_status_cli.py, README.md

## Kontext

Das Projekt besitzt bereits einen reinen Textbefehl `status`. Automatisierungen benötigen eine stabile JSON-Darstellung, ohne die bestehende Textausgabe zu verändern.

## Ziel

Ergänze den Statusbefehl um `--json`. Mit dieser Option wird genau ein JSON-Objekt mit `status`, `version` und `checked_at_utc` ausgegeben. Ohne die Option muss die bisherige Textausgabe exakt erhalten bleiben.

## Erlaubter Scope

- `src/status_cli.py`
- `tests/test_status_cli.py`
- `README.md`

Dateien außerhalb dieser Liste dürfen nicht bearbeitet werden. Wird ein weiterer Pfad gebraucht, meldet Codex eine Scope-Erweiterung an; der Orchestrator genehmigt sie je nach Pfadart selbst oder legt sie zur Entscheidung vor.

## Anforderungen

1. `--json` mit dem vorhandenen Argument-Parser des Befehls verarbeiten.
2. Gültiges UTF-8-JSON mit deterministischen Feldnamen serialisieren.
3. `checked_at_utc` als ISO-8601-UTC-Zeitstempel mit abschließendem `Z` formatieren.
4. Exitcode und Ausgabe des vorhandenen Textmodus bytegenau kompatibel halten.
5. Fokussierte Tests für JSON-Modus, Textmoduskompatibilität und eine ungültige Option ergänzen.
6. Das Befehlsbeispiel in der README aktualisieren.

## Akzeptanzkriterien

- `status --json` endet mit Code 0 und gibt genau ein JSON-Objekt aus.
- Das Objekt besitzt exakt die Schlüssel `checked_at_utc`, `status` und `version`.
- Der vorhandene Befehl ohne `--json` besteht unverändert seinen Snapshot-Test.
- Ungültige Optionen liefern weiterhin den vom Argument-Parser erzeugten Usage-Fehler mit einem von null verschiedenen Exitcode.
- Die konfigurierte Validierungsmatrix ist für den geprüften Stand grün.
- Keine Datei außerhalb des erlaubten Scope wird geändert oder committet.

## Validierung

Dieser Auftrag legt keine eigenen Befehle fest. Der Orchestrator führt nach jedem Arbeitspaket die in `orchestrator.toml` konfigurierte Validierungsmatrix aus; Befehle in einer Aufgabendatei führt er nicht aus.

## Nicht-Scope

- Keine Netzwerkzustandsprüfung.
- Keine neue Abhängigkeit.
- Keine Änderung der Versionsermittlung.
- Kein Push, Merge, Release oder Deployment.

## Stopbedingungen

- Anhalten, falls das JSON-Schema ein oben nicht aufgeführtes Feld benötigt.
- Anhalten, falls die Beibehaltung der Textausgabe eine Architekturänderung außerhalb des erlaubten Scope erfordert.
- Anhalten, falls Tests einen plattformspezifischen Zeitstempelvertrag zeigen, der nicht dokumentiert ist.
