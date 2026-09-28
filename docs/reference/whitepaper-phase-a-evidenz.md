# Evidenz zum Architektur-Whitepaper Phase A

Ausgabe **1.3**, Veröffentlichungsstand **28. September 2026**. Dieser Index ergänzt das [Whitepaper](architektur-whitepaper-phase-a.pdf). Er dokumentiert Quellen und Abgrenzungen; er ist keine Validierungsattestierung des Orchestrators.

## Versionbindung

| Bezug | Festgehaltener Wert |
| --- | --- |
| Abgenommener Orchestrator-HEAD | `a11f72ee3321fed0f45fb0874b148e71789b4012` |
| Anschließender Merge | `adf1411b2f7cba87a4edee52dfed7d29e657a24f` |
| Identischer Git-Dateibaum beider Commits | `b754620ec152dd0ea9f62095e648b23b10cb5992` |
| Abnahmelauf in der RuhestandsApp | `20260928-122307Z` |
| Unterstützter Betriebsrahmen | Linux / WSL 2; Fallstudie unter WSL 2 / Ubuntu |

Die Identität beider Dateibäume wurde mit `git rev-parse <commit>^{tree}` geprüft. Die späteren Temp- und Sprachtest-Nacharbeiten gehören nicht zu dieser eingefrorenen Ausgabe. Ihre höheren Testzahlen ersetzen die hier dokumentierten Originalzahlen nicht.

Die Commit-IDs stehen bewusst im Nachweisindex, nicht im lesenden Haupttext des Whitepapers.

## Versionsbegriffe und Profildigests

Im festgehaltenen `src/artifact_models.py` sind getrennt definiert:

- Protokollmodus: `structured-v2`.
- Artefaktschema: `schemas/orchestrator-artifact-v3.schema.json`.
- `SCHEMA_VERSION = "3"`.
- Reducer: `structured-v2-schema-2-state-v3-role-wire-v1`. Der historisch enthaltene Namensbestandteil `schema-2` ist kein Beleg für die aktuelle Artefaktschemaversion.
- Provider-Nachrichten: native v3-Verträge für Implementer und Reviewer.

`RoleProfilePayload` verlangt `capability_sha256`, `transport_sha256`, `rights_sha256`, `policy_sha256` und `certification_sha256`; hinzu kommt die separat gebundene Binary-Identität. Das Papier bezeichnet die fünf Felder als Capability-, Transport-, Rechte-, Policy- und Zulassungsdigest.

## Dauerhaft gesicherte Testprotokolle

| Nachweis | Öffentliche Datei | Gespeichertes Ergebnis |
| --- | --- | --- |
| Orchestrator Default-Suite | [task-a-default.log](whitepaper-phase-a-evidence/task-a-default.log) | 2.884 bestanden, 39 abgewählt, 330,07 s |
| Separater Crash-Harness-Lauf | [task-a-crash.log](whitepaper-phase-a-evidence/task-a-crash.log) | 39 bestanden, 2.884 abgewählt, 642,92 s |
| RuhestandsApp, abschließendes `npm test` | [ruhestandsapp-npm-test.log](whitepaper-phase-a-evidence/ruhestandsapp-npm-test.log) | 20.196 Assertions bestanden, 0 fehlgeschlagen, 0 fehlerhafte Dateien; 4 separate Gates, 0 fehlgeschlagen, 0 offene Handles |

Die ersten beiden Originaldateien lagen zunächst unter `/tmp/task-a-default.log` und `/tmp/task-a-crash.log`. Sie wurden vor der Veröffentlichung zusätzlich unverändert im privaten Abnahmepaket gesichert. Die öffentlichen Kopien liegen jetzt neben diesem Index im Repository; `/tmp` ist keine Voraussetzung mehr für das Lesen dieser Testnachweise.

**Anonymisierung:** Genau ein Projektname in einem Testbezeichner des Default-Logs wurde durch `anonymized_project` ersetzt. Ergebniszeilen, Reihenfolge und Testzahl blieben erhalten. Deshalb hat diese veröffentlichte Kopie eine andere Prüfsumme als ihr Original. Die anderen beiden Logs sind bytegleich.

| Datei | SHA-256 der veröffentlichten Datei |
| --- | --- |
| `task-a-default.log` | `d0aff07892248ec0d55f14558c16e80cdf6febc0f3c7712510e4e12da856ca07` |
| `task-a-crash.log` | `622e55f037d4958ce17cea72dd346e8d92742c738ffed35bdfa1e9ab54c000a3` |
| `ruhestandsapp-npm-test.log` | `f68139d95682f17eeaad30533fabdc7068021e9fcd6235f0fad39199e381b496` |

Das Original des Default-Logs hatte 359.782 Bytes und SHA-256 `645dc1c7b1a53bff972cc3f168ecac5e88677b6ae7ac2e58dcdeeb9b16a2e045`; die anonymisierte Kopie hat 359.791 Bytes. Die übrigen Größen betragen 128 Bytes und 185.797 Bytes.

Das npm-Protokoll stammt aus dem gespeicherten stdout-Blob des genannten Abnahmelaufs. Sein SHA-256 entspricht dem Blob-Namen. Es handelt sich um gespeicherte Ergebnisse, nicht um für diese Publikation neu ausgeführte Tests. Die Logs allein sind keine kryptografisch eigenständige HEAD-Attestierung; die Standzuordnung stammt aus dem eingesehenen Abnahmepaket und der darin festgehaltenen Versionbindung.

## Reale Fallstudie und Nachweisgrenzen

Aufgabe: Auto-Optimize auf einen eigenen Tab der RuhestandsApp verschieben; Engine, Worker und Optimierungslogik bleiben erhalten.

- Startbasis: `b91c3653527c899f8a3c323b728f42b8f7389843`.
- [Implementierungscommit](https://github.com/dieteresser62-lab/Ruhestand-App-Final/commit/1580308377fb55907efa18f6586b566eb67953c0): `1580308377fb55907efa18f6586b566eb67953c0`.
- Abschließender Branchstand: `e578749c69ce3a45b04d2894f2d3dfab5e41cf8c`.
- SIGINT am 28.09.2026 um 14:29:21; Resume ab 14:29:53; Abschluss um 14:38:27, jeweils Europe/Berlin.
- Unterbrechung während eines Reviews; der nächste Start war Versuch 2 derselben logischen Operation.
- R-01 beanstandete die rote Testsuite. Eine bestehende Prüfung erwartete noch vier Tabs; nach einer Scope-Erweiterung wurde sie korrigiert und der Befund geschlossen.

Gesichtet wurden Start-/Endmetadaten, Unterbrechungsprotokoll, Resume-Log und ausgewählte strukturierte Records des privaten Abnahmepakets. Das vollständige Rohpaket wird nicht veröffentlicht. Die Publikationsarbeit hat keine unabhängige vollständige Replay-Verifikation der Recordkette vorgenommen.

Der separate Browser-Smoke war kein Bestandteil der attestierten npm-test-Matrix. Insbesondere ersetzt die grüne Testsuite keine umfassende manuelle Prüfung aller Presets und Parameter des Auto-Optimize-Tabs in der EXE. Der Nachweis betrifft einen konkreten Abbruch-/Resume-Pfad, keine beliebigen Prozess- oder Infrastrukturfehler.

## Reproduzierbare Bestandszählung

Aus dem festgehaltenen Git-Baum werden mit `git archive` ausschließlich reguläre versionierte `*.py`-Dateien unter `src/`, `tests/` und `scripts/` gelesen. Gezählt wird `len(bytes.splitlines())`, einschließlich Leerzeilen, Kommentaren und Docstrings. Arbeitsbaumänderungen, Dokumente und generierte Dateien gehen nicht ein.

| Bereich | Dateien | Physische Zeilen |
| --- | ---: | ---: |
| `src/` | 80 | 61.441 |
| `tests/` | 113 | 86.755 |
| `scripts/` | 3 | 2.533 |
| Gesamt | 196 | 150.729 |

Testcode / `src`-Code: `86.755 / 61.441 = 1,412`, gerundet **1,41 : 1**. Mit den Skripten im Produktivnenner: `86.755 / (61.441 + 2.533) = 1,356`, gerundet **1,36 : 1**. Das Verhältnis ist keine Testabdeckung.

Das [Quellenpaket](whitepaper-phase-a-quellen.zip) enthält `source/measure_code.py`. Vom entpackten Paketverzeichnis lässt sich die Zählung ohne Checkout und ohne Provideraufruf wiederholen:

```text
python source/measure_code.py /pfad/zum/Dual-Agent-Orchestrator adf1411b2f7cba87a4edee52dfed7d29e657a24f
```

## Bearbeitbare Quelle und Quellenstatus

Das [Quellenpaket](whitepaper-phase-a-quellen.zip) enthält den vollständigen ReportLab-Generator, den automatisch erzeugten Textauszug, drei PlantUML-Dateien, maschinenlesbare Evidenzdaten, alle drei öffentlichen Testlogs und eine Bauanleitung. PDF-Text und Vektorgrafiken werden im Generator gepflegt. Die PlantUML-Dateien sind semantische Zusatzfassungen; ihre Änderung allein verändert die PDF nicht.

Die Quellen [1], [2] und [6] im PDF verweisen auf festgehaltene öffentliche Git-Stände. [3] und [7] werden durch diesen Index und die Logs ergänzt. Die Entwicklungs- und Planungsunterlagen zu [4] und [5] wurden lokal eingesehen; sie sind keine bereits umgesetzten späteren Phasen. Die anonymisierten historischen Einsätze [8] sind beschreibende Erfahrungen außerhalb der formalen Phase-A-Abnahme.

Quelle [11] lautet vollständig: [ESAA: Event Sourcing for Autonomous Agents in LLM-Based Software Engineering](https://arxiv.org/abs/2602.23193), Elzo Brito dos Santos Filho, 2026, arXiv:2602.23193. Das Papier wird als Arbeit mit Implementierung und Fallstudien eingeordnet, ohne einen gemessenen Qualitätsvorsprung des hier dokumentierten Orchestrators zu behaupten.
