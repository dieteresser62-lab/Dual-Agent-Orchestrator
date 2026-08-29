# Hotfix: Expliziter Schreibvertrag für PLAN_ONLY-Artefakte

Datum: 29. August 2026

Branch: `feature/plan-only-artifact-write-contract-hotfix`

Basis: `c9de3e16938a6206892676864182027f7aa11189`

## Anlass

Der produktive Lauf `watch-20260829-025242.227030Z-58de5b52b10a` bestätigte die zuvor eingebaute Schutzschicht: Ein `plan_result` mit `ready: true`, aber ohne geschriebenes `WORK_PLAN_PATH`, wurde vor Claude erkannt, genau einmal an Codex zurückgegeben und anschließend als pfadloses `PLAN-CONTRACT-INVALID`-Policygate angehalten.

Beide Codex-Aufrufe erzeugten jedoch nur den strukturierten Ergebnisdatensatz und keine Plandatei. Die bisherige Anweisung verlangte ausdrücklich das Emittieren eines `SLICE_PLAN`-Datensatzes „for creating or updating“ des Plans, bezeichnete die Datei aber nicht eindeutig als sofort zu schreibende Lieferleistung. Auch die automatische Revision sprach bei einer fehlenden Datei nur allgemein vom Reparieren eines Artefakts.

## Änderung

- Die PLAN_ONLY-Planungsgrenze verlangt als erste operative Handlung ausdrücklich, `WORK_PLAN_PATH` jetzt im Repository anzulegen oder zu aktualisieren.
- Die Datei wird als eigentliche Lieferleistung definiert; der native JSON-Datensatz ist nur die Quittung und ersetzt die Datei nicht.
- Vor `ready: true` muss Codex die Datei erneut lesen und auf Existenz, Nichtleere und Planinhalt prüfen. Ist das nicht möglich, muss Codex den typisierten Stop-Datensatz liefern.
- Das Änderungsverbot stellt klar, dass Produktcode, Tests, Konfiguration und generierte Artefakte unverändert bleiben, die deklarierte Arbeitsplandatei aber zwingend geschrieben werden muss.
- Der generische PLAN_ONLY-Summary fordert die Erstellung des Artefakts statt nur „Plan the requested work“.
- Die einmalige automatische Revision unterscheidet:
  - fehlende Datei: `Create the missing file ...`;
  - vorhandene, aber ungültige Datei: `Update the existing file ...`.

Die bestehende Begrenzung auf genau eine automatische Revision, das fail-closed Policygate und sämtliche State-/Record-Bindungen bleiben unverändert.

## Geänderte Pfade

- `src/orchestrator.py`
- `src/workflow.py`
- `tests/test_orchestrator_runtime.py`
- `docs/internal/plan-only-artifact-write-contract-hotfix-20260829.md`

## Akzeptanznachweis

- Die initiale native PLAN_ONLY-Anfrage enthält die explizite Schreibpflicht, die Quittungsabgrenzung und die Ausnahme vom Änderungsverbot.
- Die alte mehrdeutige Formulierung `Plan the requested work.` fehlt im initialen PLAN_ONLY-Request.
- Eine fehlende Datei erzeugt in der Revision die konkrete Aufforderung `Create the missing file ...`.
- Ein vorhandener, strukturell ungültiger Plan erzeugt stattdessen `Update the existing file ...`.
- Der Reviewer wird weiterhin erst nach einem tatsächlich vorhandenen, handoff-fähigen Arbeitsplan aufgerufen.
- Gezielter Lauf: `4 passed, 73 deselected`.
- Vollständige Suite nach dem finalen Patch: `1134 passed in 148.53s`.

## Wiederanlauf

Der gestoppte Lauf wird nicht manuell verändert oder fortgesetzt. Nach Claude-Freigabe, lokalem Commit und Übernahme des Hotfixes auf den Zielbranch wird die ursprüngliche Aufgabe mit einer frischen Watch-Identität neu gestartet. Ein Gate für den alten Lauf ist nicht zu genehmigen, weil dort kein reviewbares Arbeitsplanartefakt existiert.
