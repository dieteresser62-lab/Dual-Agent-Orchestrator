# Arbeitsplan für einen begrenzten Gap erstellen

ORCHESTRATOR_MODE: PLAN_ONLY
WORK_PLAN_PATH: docs/internal/beispiel-work-plan.md
TARGET_BRANCH: feature/beispiel-gap
TASK_SCOPE: docs/internal/beispiel-work-plan.md

## Kontext

Beschreibe hier den festgestellten Gap kurz und konkret. Verweise auf vorhandene Anforderungen, Architektur- oder Referenzdokumente, sofern sie für die Analyse maßgeblich sind.

## Ziel

Erstelle ausschließlich den vollständigen Arbeitsplan unter `docs/internal/beispiel-work-plan.md`. Produktivcode, Tests, Konfigurationen und generierte Artefakte dürfen in diesem Lauf nicht verändert werden.

Der Arbeitsplan muss enthalten:

- Ziel und Nicht-Scope;
- relevante Ist-Architektur und fachliche Invarianten;
- konkrete Anforderungen und Akzeptanzkriterien;
- geordnete, 1-basierte zukünftige Implementierungsslices;
- je zukünftigem Slice Zweck, exakte Pfade, Abnahmekriterien und Validierung;
- Risiken, Abhängigkeiten und Stopbedingungen.

## Erlaubter Scope

- `docs/internal/beispiel-work-plan.md`

## Akzeptanzkriterien

- Das Arbeitsplan-MD ist vollständig, widerspruchsfrei und konkret umsetzbar.
- Die zukünftigen Slice-Überschriften beginnen bei Slice 1 und sind lückenlos nummeriert.
- Claude und Antigravity haben denselben Planfingerprint freigegeben.
- Vor dem Dokumentationscommit wurde das fingerprintgebundene Plangate explizit freigegeben.
- Keine Datei außerhalb des erlaubten Scope wurde geändert oder commitet.

## Nicht-Scope

- Keine Umsetzung der im Arbeitsplan beschriebenen zukünftigen Slices.
- Keine Änderung von Produktivcode, Tests oder Konfigurationen.
- Kein Branchwechsel durch einen Agenten.
- Kein Push, Merge, Release oder Deployment.

## Stopbedingungen

- Anhalten, wenn ein vollständiger Arbeitsplan einen weiteren Pfad benötigt.
- Anhalten, wenn die fachliche Sollrichtung mehrere wesentlich unterschiedliche Varianten zulässt.
- Anhalten, wenn der aktive Branch nicht exakt `feature/beispiel-gap` ist.
