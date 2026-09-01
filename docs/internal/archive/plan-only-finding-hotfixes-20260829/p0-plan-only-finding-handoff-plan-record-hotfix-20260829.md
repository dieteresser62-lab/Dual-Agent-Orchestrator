# P0-Hotfix: PLAN_ONLY-Finding-Handoff nach freigegebenem Plan

Datum: 29. August 2026

## Anlass

Der Lauf `watch-20260829-093135.076835Z-f71c891be049` hatte den Arbeitsplan erfolgreich erzeugt, von Claude freigeben und als Commit `ebb903f212ea04db1ef49810cc84398d91a81d32` gebunden. Anschließend brach der automatische Übergang zum IMPLEMENT-Handoff ab:

`finding export plan commit is not present in accepted replay`

Die autoritative Record-Kette enthielt die positive Reviewentscheidung, das offene Finding `C-01`, die Commit-Bindung und den Abschlussrecord, aber keinen `PlanPayload`. Der State spiegelte denselben Widerspruch: Der Slice trug den Commit, `approved_plan_commit` war jedoch leer.

## Ursache

Der PLAN_ONLY-Commit wurde ausschließlich als `SliceRecord.commit_ref` gespeichert. Die strukturierte Projektion erzeugt den unveränderlichen `PlanPayload` dagegen nur aus `WorkflowState.approved_plan_commit`. Der Finding-Export verlangt diesen Planrecord zusätzlich zur Commit-Bindung, weil er Planpfad und Slice-Struktur an den freigegebenen Commit bindet.

Ohne Claude-Finding blieb der Fehler verborgen, weil kein Finding-Export notwendig war.

## Korrektur

- Ein abgeschlossener PLAN_ONLY-Plan bindet seinen freigegebenen Commit vor dem Abschluss-Checkpoint zusätzlich als `approved_plan_commit`.
- Die Bindung ist auf PLAN_ONLY, die abgeschlossene Plan-Work-Unit, den vorhandenen Planvertrag und den exakt gleichen Slice-Commit beschränkt. Sie ist idempotent und kann nicht auf einen anderen Commit umgebogen werden.
- Bereits abgeschlossene Läufe mit der alten Lücke werden beim Übergang streng aus ihrem persistierten Slice-Commit nachgezogen. Danach schreibt der normale Checkpoint den fehlenden `PlanPayload`; Codex und Claude werden nicht erneut aufgerufen.
- Fehler aus Artifact-Bridge und Artifact-Replay werden am Handoff-Rand als `WorkflowExecutionError` typisiert, statt als unbehandelte Ausnahme mit fremdem Fehlertyp auszutreten.
- Die bestehende `PlanPayload`-Prüfung bleibt erhalten; ein bloßes Commit-Binding ersetzt nicht die Bindung von Planpfad und Slice-Struktur.

## Regressionsevidenz

- Normalfall: positiver PLAN_ONLY-Review mit offener `OBSERVATION` persistiert `PlanPayload → workflow_completion → FindingHandoffExportPayload` und erzeugt einen gebundenen IMPLEMENT-Task.
- Recovery: ein simulierter Altzustand mit Review, Finding, Commit-Bindung und Abschlussrecord, aber ohne `PlanPayload`, wird ohne erneuten Agentenaufruf nachgezogen und erzeugt exakt einen Plan- und einen Exportrecord.
- State-Vertrag: die Commitbindung ist rundlaufstabil, idempotent und lehnt einen vom Slice abweichenden Commit ab.

## Wiederaufnahme des betroffenen Laufs

Nach erfolgreicher Validierung und Review dieses Hotfixes wird die Poison-Aufgabe mit unverändertem Inhalt sowie ihrer ursprünglichen Run-ID und ihrem ursprünglichen Task-Digest wieder in die Inbox gelegt. Der Watcher muss mit `--resume` den bereits abgeschlossenen Plan übernehmen und ausschließlich den fehlenden Plan-/Finding-Handoff fortsetzen.

State, Checkpoints und vorhandene Records werden nicht manuell verändert.
