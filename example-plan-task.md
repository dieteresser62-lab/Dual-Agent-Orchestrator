# Arbeitsplan für einen begrenzten Gap erstellen

> Formales Beispiel für einen bewusst getrennt gestarteten Planlauf – für Fortgeschrittene und maschinell erzeugte Aufträge. Im normalen Betrieb genügt eine Idee in normaler Sprache; den Planlauf leitet der Orchestrator daraus selbst ab (siehe [Einrichtung](docs/reference/einrichtung.md)). Die maschinenlesbare Form des Arbeitsplans teilt er dem Planer ebenfalls selbst mit – sie gehört nicht in die Aufgabe. `TARGET_BRANCH` ist optional.

ORCHESTRATOR_MODE: PLAN_ONLY
WORK_PLAN_PATH: docs/internal/beispiel-arbeitsplan.md
TARGET_BRANCH: feature/beispiel-gap
TASK_SCOPE: docs/internal/beispiel-arbeitsplan.md

## Kontext

Beschreibe hier kurz und konkret, was fehlt und warum. Verweise auf vorhandene Anforderungen, Architektur- oder Referenzdokumente, sofern sie für die Planung maßgeblich sind.

## Ziel

Erstelle ausschließlich den Arbeitsplan unter `docs/internal/beispiel-arbeitsplan.md`. Produktivcode, Tests und Konfiguration bleiben in diesem Lauf unverändert.

Der Arbeitsplan beschreibt Ziel und Nicht-Scope, die relevante Ist-Architektur, fachliche Invarianten und Risiken sowie geordnete Arbeitspakete mit exakten Pfaden und Akzeptanzkriterien.

## Erlaubter Scope

- `docs/internal/beispiel-arbeitsplan.md`

## Akzeptanzkriterien

- Der Arbeitsplan ist vollständig, widerspruchsfrei und konkret umsetzbar.
- Claude hat denselben Planstand freigegeben.
- Keine Datei außerhalb des erlaubten Scope wurde geändert oder committet.

## Nicht-Scope

- Keine Umsetzung der geplanten Arbeitspakete.
- Keine Änderung von Produktivcode, Tests oder Konfiguration.
- Kein Push, Merge, Release oder Deployment.

## Stopbedingungen

- Anhalten, wenn ein vollständiger Arbeitsplan einen weiteren Pfad benötigt.
- Anhalten, wenn die fachliche Richtung mehrere wesentlich unterschiedliche Varianten zulässt.
