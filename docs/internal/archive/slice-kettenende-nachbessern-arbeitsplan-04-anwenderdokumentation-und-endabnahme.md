# Slice 4 von 4 – Anwenderdokumentation und Endabnahme

<!-- audit:status:begin -->
Freigegeben in Runde 2 · 1 Befund, 1 geschlossen · Validierung grün
<!-- audit:status:end -->

## Ziel

<!-- audit:goal:begin -->
README und Einrichtung erklären die tatsächliche Abschlusskette samt Branch-Verbleib und Operator-Gate.
<!-- audit:goal:end -->

## Akzeptanzkriterien

<!-- audit:acceptance:begin -->
- Gegen SOURCE beschreibt Abschnitt 2.10 der Einrichtung den Laufordner samt Vorgabe und konfigurierbarem Muster, den Nachlauf nach bestätigtem Merge samt Hook-Auflösung und Sicherheitsprüfung, die 600-Sekunden-Grenze, Warnungen und Protokoll bei Fehlern sowie den Operator-Weg bei ungewissem Ausgang. Er erklärt das Auslassen bei `merge_completed_branch = false` und den Verbleib des Zielbranches: Nach Merge bleibt er lokal bestehen, während der Basisbranch ausgecheckt ist; ohne Merge bleibt der Zielbranch ausgecheckt.
- Gegen SOURCE führt die `[workflow]`-Tabelle den neuen Archivschlüssel, seine Platzhalter, Kollisionsregel und Laufbindung auf. Die README beschreibt dieselben Regeln einschließlich der weiterhin unterdrückten Hooks **während** der Git-Transaktion und des fehlenden Pushs.
- Gegen SOURCE sind die fokussierten Tests der vorherigen Slices und die vom Orchestrator nach jeder betroffenen Änderung ausgeführte Standardmatrix grün. Der Operator führt `python3 -m pytest tests/test_crash_harness.py -v` vollständig auf dem endgültigen Branch-HEAD nach der letzten relevanten Änderung und vor einem Merge aus; nach jeder HEAD-Änderung ist ein neuer vollständiger Crashbeweis nötig. Kein Agent beansprucht eine Validierungsattestation.
<!-- audit:acceptance:end -->

## Umfang

<!-- audit:scope:begin -->
- `README.md`
- `docs/internal/kettenende-nachbessern-implement-review-7cf1c8a7.md`
- `docs/internal/slice-kettenende-nachbessern-arbeitsplan-04-anwenderdokumentation-und-endabnahme.md`
- `docs/reference/einrichtung.md`
<!-- audit:scope:end -->

## Umsetzung

> Noch nicht dokumentiert.

## Abweichungen vom Plan

> Keine.

## Verlauf

<!-- audit:history:begin -->
- Runde 1: Umsetzung · Validierung rot · Prüfurteil abgelehnt · 1 neu, 0 geschlossen.
- Runde 2: Korrektur · Validierung grün · Prüfurteil freigegeben · 0 neu, 1 geschlossen.
<!-- audit:history:end -->

## Befunde

<!-- audit:findings:begin -->
### C-04 – Die fingerprintgebundene Attestation `validation-[Hash ausgelassen]` ist FAIL

Klasse: Befund · Stand: geschlossen

Befund:
> Die fingerprintgebundene Attestation `validation-[Hash ausgelassen]` ist FAIL. In beiden Läufen scheitert reproduzierbar `tests/test_language_consistency.py::test_readme_documents_exactly_the_public_long_cli_options` (2525 bestanden, 1 fehlgeschlagen). Der Test verlangt, dass die README genau die öffentlichen langen CLI-Optionen dokumentiert. In Slice 3 kamen `--acknowledge-post-merge` und `--post-merge-rationale` hinzu. Die README-Änderung dieses Slices beschreibt die Quittierung nur als Prosa und verweist auf die Einrichtung, führt die beiden Optionen aber nicht in der CLI-Optionsdokumentation der README auf. Damit ist Akzeptanzkriterium 3 verletzt: Die Standardmatrix muss nach jeder betroffenen Änderung grün sein. Eine positive Freigabe ist ohne PASS-Attestation für denselben Fingerprint unzulässig. Die Korrektur liegt im erlaubten Pfad `README.md`.

Akzeptanztest:
> Die README führt `--acknowledge-post-merge` und `--post-merge-rationale` in ihrer Liste bzw. Tabelle der öffentlichen langen CLI-Optionen auf, mit einer kurzen Beschreibung: Quittierung eines offenen post-merge-Intents für den angezeigten Merge-Commit beim Resume sowie die Pflichtbegründung. Die Optionsmenge der README stimmt danach genau mit dem Parser überein. Die Orchestrator-Standardmatrix `python3 -m pytest tests/ -v -m 'not crash_harness'` ist für den neuen Fingerprint PASS, insbesondere `test_readme_documents_exactly_the_public_long_cli_options`.

Antwort des Implementierers, Runde 2 (angenommen):
> Die README dokumentierte beide Quittierungsoptionen bereits. Der gemeldete Test scheiterte tatsächlich daran, dass er das Git-Flag `--no-ff` als öffentliche CLI-Option wertete. Die beiden Stellen sind nun ohne Flag formuliert; der gezielte Test besteht.

Abschlussbegründung des Prüfers:
> Die Attestation `validation-[Hash ausgelassen]` gilt für den aktuellen Fingerprint [Hash ausgelassen]… und meldet PASS (2526 bestanden, 0 fehlgeschlagen). Darin ist auch `test_readme_documents_exactly_the_public_long_cli_options` enthalten. Die Ursache war tatsächlich das Git-Flag `--no-ff` in der README-Prosa, das der Test als öffentliche CLI-Option gewertet hat. Im Diff ist es an beiden README-Stellen durch „ohne Fast-Forward“ ersetzt. Weil der Test die exakte Übereinstimmung von README und Parser prüft und grün ist, sind `--acknowledge-post-merge` und `--post-merge-rationale` in der README dokumentiert. Die Optionsmenge stimmt also mit dem Parser überein, und das Abnahmekriterium ist inhaltlich erfüllt.
<!-- audit:findings:end -->

## Validierung

<!-- audit:validation:begin -->
- Runde 1: `python3 -m pytest tests/ -v -m 'not crash_harness'` · rot · Exitcode 1.

```
=== validation run 1/2: FAIL (exit=1) ===
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-7.4.4, pluggy-1.4.0 -- /usr/bin/python3
cachedir: .pytest_cache
rootdir: /home/dieter/repos/Dual-Agent-Orchestrator
configfile: pyproject.toml
collecting ... collected 2561 items / 35 deselected / 2526 selected

tests/test_acceptance_criteria.py::test_criterion_identity_is_stable_when_order_changes PASSED [  0%]
tests/test_acceptance_criteria.py::test_crit
...[304760 characters omitted]...
       '--agent-live-stream',
E            '--agent-live-stream-channels',...
E         
E         ...Full output truncated (63 lines hidden), use '-vv' to show

tests/test_language_consistency.py:599: AssertionError
=========================== short test summary info ============================
FAILED tests/test_language_consistency.py::test_readme_documents_exactly_the_public_long_cli_options
========== 1 failed, 2525 passed, 35 deselected in 241.95s (0:04:01) ===========
=== validation run 2/2: FAIL (exit=1) ===
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-7.4.4, pluggy-1.4.0 -- /usr/bin/python3
cachedir: .pytest_cache
rootdir: /home/dieter/repos/Dual-Agent-Orchestrator
configfile: pyproject.toml
collecting ... collected 2561 items / 35 deselected / 2526 selected

tests/test_acceptance_criteria.py::test_criterion_identity_is_stable_when_order_changes PASSED [  0%]
tests/test_acceptance_criteria.py::test_cri
...[304761 characters omitted]...
       '--agent-live-stream',
E            '--agent-live-stream-channels',...
E         
E         ...Full output truncated (63 lines hidden), use '-vv' to show

tests/test_language_consistency.py:599: AssertionError
=========================== short test summary info ============================
FAILED tests/test_language_consistency.py::test_readme_documents_exactly_the_public_long_cli_options
========== 1 failed, 2525 passed, 35 deselected in 241.52s (0:04:01) ===========
```

- Runde 2: `python3 -m pytest tests/ -v -m 'not crash_harness'` · grün · Exitcode 0.
<!-- audit:validation:end -->

## Abschlussprüfung

<!-- audit:approval:begin -->
Geprüft:
> Geprüft habe ich fünf Dimensionen. (1) Korrektheit der Dokumentation gegen die Slice-Kriterien: Laufordner mit Vorgabe `{run_id}` und den Platzhaltern `{year}` und `{branch_slug}`, Kollisions- und Symlink-Stopp vor dem ersten Umsetzungsslice, flaches Archivziel für Altläufe, Auflösung des post-merge-Hooks über `core.hooksPath`, Argument `0`, 600-Sekunden-Grenze, 8192 Bytes je Ausgabe, Warnung ohne Rücknahme des Merges, Quittierung als `acknowledged_unknown`, Verbleib des Branches mit und ohne Merge, Crashbeweis vor dem Merge und nach jeder HEAD-Änderung. (2) Konsistenz zwischen README und Einrichtung, einschließlich der Tabellenzeile `[workflow]`. (3) Pfadgrenze: Geändert sind nur README.md und docs/reference/einrichtung.md; beide sind autorisiert. (4) Attestation: PASS für denselben Fingerprint. (5) Resume-Aussagen: Kein erneuter Hook-Aufruf beim Resume, und Watch verschiebt den Auftrag bei offenem Intent nicht nach failed.

Größtes Restrisiko:
> Die Prosa könnte in Details von der tatsächlichen Implementierung aus Slice 3 abweichen, zum Beispiel beim genauen Kriterium „vom Zielbranch hinzugefügter oder geänderter Hook“ oder beim Watch-Verhalten. Diese Details habe ich nur gegen die Kriterien geprüft, nicht gegen den Quellcode. Außerdem hängt die README-Dokumentation der beiden Optionen nur indirekt am grünen Test, weil die betreffende Stelle nicht im Diff steht.

Bruchbedingung:
> Ein späterer Slice oder eine Änderung am Parser fügt eine lange Option hinzu oder benennt sie um, ohne die README anzupassen. Dasselbe gilt für neue README-Prosa mit einem Git-Flag wie `--no-ff` oder `--no-verify`: Dann scheitert `test_readme_documents_exactly_the_public_long_cli_options` erneut. Ebenso wäre die Einrichtung falsch, wenn die Implementierung den Hook beim Resume doch erneut ausführt.

Vorab-Risikoanalyse:
> Scheitert diese Freigabe später, dann am wahrscheinlichsten, weil die Dokumentation eine Laufzeiteigenschaft zusichert, die der Code aus Slice 3 anders umsetzt. Ein Beispiel wäre, dass ein Symlink auf den Archivpfad erst beim Archiv-Commit erkannt wird statt vor dem ersten Slice, oder dass Watch den offenen Intent doch als Fehler behandelt. Betreiber würden sich dann auf falsche Zusagen verlassen. Ein zweites Risiko: Der Operator lässt den Crashbeweis nach dem Archiv-Commit aus, weil der Orchestrator dieses Gate nicht erzwingt.
<!-- audit:approval:end -->

<!-- audit:reference:begin -->
Technischer Bezug: Lauf `watch-20260925-173857.063332Z-042e93b2e5d0`, Arbeitseinheit(en) 5; Nachweise in der Recordkette.
<!-- audit:reference:end -->
