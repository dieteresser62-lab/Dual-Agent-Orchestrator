# Slice 2 von 4 – Laufgebundenes Archivmuster und frühe Zielprüfung

<!-- audit:status:begin -->
Freigegeben in Runde 1 · 0 Befunde, 0 geschlossen · Validierung grün
<!-- audit:status:end -->

## Ziel

<!-- audit:goal:begin -->
Jeder neue Lauf archiviert seine Dokumente in einem eigenen, konfigurierbaren und eindeutig benannten Unterordner; erkennbare Zielkonflikte stoppen vor dem ersten Umsetzungsslice.
<!-- audit:goal:end -->

## Akzeptanzkriterien

<!-- audit:acceptance:begin -->
- Gegen SOURCE akzeptiert `[workflow] archive_run_directory` nur die dokumentierten Platzhalter und einen kanonischen, relativen Unterpfad mit `{run_id}` als vollständigem Segment. Der neue Wert wird beim Laufstart gebunden; ein fehlender Wert in alten Records bedeutet beim Resume unverändertes flaches Archivverhalten.
- Gegen SOURCE wird der konkrete Zielordner aus gebundenem Muster und Laufidentität vor dem ersten Umsetzungsslice geprüft. Ein vorhandener Zielordner, eine Datei oder ein Symlink in einem Zielsegment wird mit dem betroffenen Pfad gemeldet, bevor Codex für Slice 1 startet. Beim Kettenende werden dieselben gebundenen Bytes und die bestehenden Datei- und Merge-Vorabprüfungen verwendet.
- Gegen SOURCE archivieren zwei unabhängige Läufe mit gleichem Auftrags- und Quelldateinamen ohne Konflikt in getrennte Unterordner. Der Archiv-Commit enthält weiterhin nur die vorgesehenen Umbenennungen und behält die bestehende Rollback- und Resume-Semantik.
- Gegen SOURCE prüfen fokussierte Konfigurations-, Record-Kompatibilitäts-, Frühstopp-, Archiv- und Crash-Grenztests auch ungültige Muster, Pfadflucht, Ziel-Symlinks und alte Profile. Der eigenständige vollständige Crashbeweis bleibt dem Operator-Gate auf dem Endstand vorbehalten.
<!-- audit:acceptance:end -->

## Umfang

<!-- audit:scope:begin -->
- `docs/internal/kettenende-nachbessern-implement-review-7cf1c8a7.md`
- `docs/internal/slice-kettenende-nachbessern-arbeitsplan-02-laufgebundenes-archivmuster-und-fruhe-zielprufung.md`
- `src/agent_runtime.py`
- `src/artifact_models.py`
- `src/cli.py`
- `src/orchestrator.py`
- `src/workflow_baseline.py`
- `src/workflow_completion.py`
- `src/workflow_production.py`
- `tests/test_artifact_models.py`
- `tests/test_cli.py`
- `tests/test_crash_harness.py`
- `tests/test_workflow_baseline.py`
- `tests/test_workflow_completion.py`
<!-- audit:scope:end -->

## Umsetzung

> Noch nicht dokumentiert.

## Abweichungen vom Plan

> Keine.

## Verlauf

<!-- audit:history:begin -->
- Runde 1: Umsetzung · Validierung grün · Prüfurteil freigegeben · 0 neu, 0 geschlossen.
- Runde 1: Umfang erweitert um schemas/orchestrator-artifact-v2.schema.json. Grund: Required paths: schemas/orchestrator-artifact-v2.schema.json Why required for current Slice: Das laufgebundene Archivmuster muss als neues Feld im RunProfile gespeichert werden. Das Record-Schema verbietet zusätzliche Felder; ohne Änderung dieser Datei würde ein neues Profil bei der Schema-Validierung abgewiesen. Der Pfad liegt außerhalb des freigegebenen Slice-Umfangs.
- Runde 1: Umfang erweitert um tests/fixtures/cli-argument-evaluation-corpus-v1.json, tests/test_legacy_verifier.py, tests/test_orchestrator_runtime.py, tests/test_payload_dispatch.py. Grund: Required paths: tests/fixtures/cli-argument-evaluation-corpus-v1.json, tests/test_legacy_verifier.py, tests/test_orchestrator_runtime.py, tests/test_payload_dispatch.py Why required for current Slice: Das neue laufgebundene RunProfile-Feld ändert die CLI-Korpusdaten und bestehende Profilvergleiche. Die Altprofil-Testhilfe muss das zusätzliche optionale Feld setzen. Diese vier Pfade liegen außerhalb des freigegebenen Slice-Umfangs. Die Standardvalidierung ist deshalb noch nicht grün; die übrigen dabei erkannten Fehler wurden innerhalb des Umfangs behoben.
<!-- audit:history:end -->

## Befunde

<!-- audit:findings:begin -->
Keine.
<!-- audit:findings:end -->

## Validierung

<!-- audit:validation:begin -->
- Runde 1: `python3 -m pytest tests/ -v -m 'not crash_harness'` · grün · Exitcode 0.
<!-- audit:validation:end -->

## Abschlussprüfung

<!-- audit:approval:begin -->
Geprüft:
> Korrektheit: validate_archive_run_directory verlangt ein relatives Muster mit {run_id} als vollständigem Segment, weist leere Segmente, '.', '..', Backslash, Leerzeichen und unbekannte Platzhalter ab; die Expansion prüft run_id gegen [A-Za-z0-9._-]+ und bildet branch_slug nur aus [a-z0-9-], sodass keine Pfadflucht entsteht. Verträge: RunProfilePayload erhält ein optionales Feld, der Reader nutzt data.get, artifact_payload_document entfernt None, und das Schema ergänzt die Property. Alte Records bleiben damit wire-kompatibel und archivieren flach über ARCHIVE_RELATIVE. Der Baseline-Resume übernimmt das persistierte Muster; nur ohne gebundenes Profil wird die Konfiguration verwendet. Frühstopp: preflight_archive_directory liest das Profil aus der Record-Kette und prüft vor engine.run_current_work_unit für Slice 1 jedes Segment auf Datei oder Symlink sowie den Zielordner auf Existenz. Am Kettenende nutzen preflight_chain und complete_chain dasselbe Profil. Fehlerpfade: Beim Rollback werden die neu angelegten Verzeichnisse in umgekehrter Reihenfolge entfernt. Tests decken ungültige Muster, Konflikte, zwei Läufe, alte Profile, Rollback und die Boundary-Resumes ab. Die Attestierung mit PASS ist an den Fingerprint gebunden.

Größtes Restrisiko:
> Stürzt der Prozess zwischen directory.mkdir() des Laufordners und der ersten Umbenennung ab, bleibt ein leerer Laufordner zurück. Führt der Resume die offene Archiv-Absicht erneut aus, meldet check_archive_directory einen Konflikt und stoppt fail-closed, bis der Operator den Ordner entfernt. Zusätzlich überspringt der hasattr-Fallback in _run_production_transition_loop die Frühprüfung stillschweigend bei Drivern ohne diese Methode, und {year} wird heuristisch aus der run_id abgeleitet.

Bruchbedingung:
> Das Ergebnis ist falsch, wenn der SideEffectExecutor bei einer offenen, nachweislich nicht erfolgten archive_commit-Absicht den Execute-Callback erneut aufruft, während ein leerer Laufordner aus einem abgebrochenen Versuch existiert. Es ist ebenfalls falsch, wenn Slice 1 im Produktionspfad über einen anderen Dispatch-Zweig als den geprüften IN_PROGRESS-Zweig gestartet wird, sodass Codex vor der Zielprüfung startet.

Vorab-Risikoanalyse:
> Das wahrscheinlichste Scheitern dieses Slices betrifft den Resume: Nach einem harten Abbruch mitten in der Archivierung blockiert ein leerer Laufordner den Abschluss. Dieser Fall ist fail-closed und nennt den Pfad, daher akzeptabel. Ein zweites Risiko sind Run-IDs ohne achtstelliges Datum: Bei konfiguriertem {year} scheitert der Lauf erst mit der Frühprüfung vor Slice 1, also vor jeder Umsetzung und damit ohne Schaden. Das Profil wird gebunden, alte Profile archivieren flach, und die Konfliktprüfung läuft vor Codex. Deshalb ist kein weiterer entscheidbarer Mangel erkennbar.
<!-- audit:approval:end -->

<!-- audit:reference:begin -->
Technischer Bezug: Lauf `watch-20260925-173857.063332Z-042e93b2e5d0`, Arbeitseinheit(en) 3; Nachweise in der Recordkette.
<!-- audit:reference:end -->
