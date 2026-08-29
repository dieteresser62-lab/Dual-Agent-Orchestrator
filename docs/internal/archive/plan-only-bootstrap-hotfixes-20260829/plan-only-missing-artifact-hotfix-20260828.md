# Hotfix: fehlendes PLAN_ONLY-Artefakt korrekt nachbessern (Archiv)

Datum: 28. August 2026  
Branch: `feature/plan-only-missing-artifact-hotfix`  
Basis: `d3e1aecf0ec6d19b783cdb6957f1da048a842b7d`

## Anlass

Der PLAN_ONLY-Lauf `watch-20260828-200443.998580Z-77a8e9b3eb09` lieferte ein
gültiges natives `plan_result`, schrieb aber das deklarierte Arbeitsplandokument
`docs/internal/01-validierungsevidenz-darf-aktive-pruefungen-nicht-selbst-vergiften-arbeitsplan.md`
nicht ins Repository. Der Orchestrator stoppte daraufhin vor Claude mit einem
fingerprintgebundenen `UNEXPECTED-PATH`-Nutzergate für
`.orchestrator/plan-output.md`.

Diese Datei ist kein Repositoryartefakt. Sie ist eine virtuelle Evidenzdarstellung,
die `ProductionWorkflowDriver.collect_changes()` ausschließlich dann erzeugt, wenn
Codex zwar eine Planantwort geliefert, aber keine Repositoryänderung vorgenommen
hat. Eine Nutzerfreigabe dieses Pfads hätte den eigentlichen Vertragsbruch verdeckt.

## Ursache

`WorkflowEngine._validate_plan_before_review()` prüfte die Slice-Grenze vor dem
internen PLAN_ONLY-Vertrag. `_validate_change_boundary()` nahm die virtuelle
`.orchestrator/plan-output.md` nur bei normalen Planläufen, nicht aber bei
PLAN_ONLY, von der Pfadgrenze aus. Dadurch erreichte der Lauf nie
`ProductionWorkflowDriver.validate_plan()`, obwohl diese Funktion die virtuelle
Datei bereits herausfiltert und den zutreffenden Fehler meldet:

`PLAN_ONLY Codex planning must create or update WORK_PLAN_PATH`

Zusätzlich war dieser Fehler noch nicht als automatisch reparierbarer
Planvertragsfehler eingestuft. Nur ein bereits vorhandenes, aber syntaktisch nicht
handofffähiges Arbeitsplandokument löste bisher die einmalige Planrevision aus.

## Korrektur

- Die exakt alleinstehende virtuelle `.orchestrator/plan-output.md` wird auch bei
  PLAN_ONLY nicht als echte, freizugebende Pfadänderung behandelt.
- Die anschließende interne Vertragsprüfung bleibt die Autorität und erkennt das
  fehlende `WORK_PLAN_PATH`-Artefakt.
- Dieser konkrete Fehler darf genau den bereits vorhandenen, durch den persistenten
  Side-Effect-Key begrenzten automatischen Schritt `CODEX_PLAN_REVISION` auslösen.
- Schreibt Codex das Arbeitsplandokument auch bei der Revision nicht, pausiert der
  Lauf ohne Claude-Aufruf im vorgesehenen `PLAN-CONTRACT-INVALID`-Policygate.
- Echte geänderte Pfade außerhalb des Planungsscopes bleiben unverändert
  fingerprintgebundene `UNEXPECTED-PATH`-Nutzergates.

## Regressionstests

`tests/test_orchestrator_runtime.py` enthält zwei providerfreie Ablaufproben:

1. Ein initial fehlendes Arbeitsplandokument wird als Vertragsfehler an Codex
   zurückgegeben. Die Revision schreibt einen gültigen Plan; erst danach wird
   Claude genau einmal aufgerufen und der PLAN_ONLY-Handoff abgeschlossen.
2. Fehlt das Dokument auch nach der Revision, erfolgen exakt zwei Codex-Schritte,
   kein Claude-Aufruf und ein pfadloses `PLAN-CONTRACT-INVALID`-Gate. Es entsteht
   insbesondere kein Gate für `.orchestrator/plan-output.md`.

Die bereits vorhandenen Tests für einen inhaltlich nicht handofffähigen Plan bleiben
als Nachweis erhalten, dass der neue Fehlerpfad dieselbe begrenzte
Reparatursemantik verwendet.

Lokale Codex-Validierung, keine Orchestrator-Attestierung:

- Vier fokussierte PLAN_ONLY-Vertragsproben: `4 passed in 5.72s`
- Vollständiges Runtime-Modul: `77 passed in 27.44s`
- Provider-Kopplungsratchet plus beide neuen Proben: `3 passed in 5.06s`
- Vollständige Suite gemäß `AGENTS.md`: `1134 passed in 149.27s`
- `git diff --check`: keine Whitespacefehler; lediglich bestehende
  Zeilenende-Konvertierungswarnungen der Arbeitsumgebung

## Betriebliche Fortsetzung

Der abgebrochene Lauf und seine strukturierten Records bleiben bis zum Review als
Diagnoseevidenz unverändert. Nach Freigabe, lokalem Commit und Merge des Hotfixes
soll die Backlogaufgabe als neuer strukturierter Lauf von `master` gestartet
werden. Das bestehende `UNEXPECTED-PATH`-Gate soll nicht genehmigt und der alte
Lauf nicht durch manuelle Änderungen an State, Checkpoints oder Records repariert
werden.
