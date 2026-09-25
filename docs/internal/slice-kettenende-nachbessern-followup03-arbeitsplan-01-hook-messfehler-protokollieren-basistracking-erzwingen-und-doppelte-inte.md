# Slice 1 von 1 – Hook-Messfehler protokollieren, Basistracking erzwingen und doppelte Intents stoppen

<!-- audit:status:begin -->
Freigegeben in Runde 1 · 0 Befunde, 0 geschlossen · Validierung grün
<!-- audit:status:end -->

## Ziel

<!-- audit:goal:begin -->
Der Nachlauf erzeugt bei nicht lesbarem Hook ein terminales Auslassungsergebnis, führt vorhandene unversionierte Worktree-Hooks nicht aus und stoppt bei widersprüchlichen passenden Hook-Effekten vor jeder weiteren Hook-Arbeit.
<!-- audit:goal:end -->

## Akzeptanzkriterien

<!-- audit:acceptance:begin -->
- Gegen SOURCE führt ein `PermissionError` bei `lstat()` oder `os.open()` während der anfänglichen Digest-Messung weder bei `merge_completed_branch = true` noch bei `false` zu einer Ausnahme vor dem Hook-Intent. Parametrisierte fokussierte Tests prüfen einen neu geschriebenen Vierfeld-Intent mit exakt `unavailable`, ein terminales Ergebnis `status: skipped` und `reason: digest_unreadable`, eine sichtbare Warnung, einen erfolgreichen Abschluss sowie den erhaltenen Merge-HEAD beziehungsweise den unveränderten Basis-HEAD bei deaktiviertem Merge. Der Hook-Zähler bleibt null; erneutes Resume schreibt keinen zweiten Hook-Effekt und startet keinen Hook.
- Gegen SOURCE kann weder ein anfänglich noch ein nach dem Intent bei der Kontrollmessung auftretender Digest-Fehler einen Hook-Lauf freigeben. Ein fokussierter Test simuliert den Fehler nach dem Intent, prüft das terminale Ergebnis zum selben Schlüssel und dieselbe Auslassungsbegründung sowie das Ausbleiben einer Ausführung. Der Platzhalter `unavailable` reicht auch bei wiederholt gleichem Messwert nicht als Freigabe. Die bisherigen Gründe `missing` und `merge_disabled` bleiben für ihre fehlerfreien Fälle auswertbar.
- Gegen SOURCE überspringt `_hook_reason()` einen vorhandenen Hook-Pfad im Worktree außerhalb von `.git`, wenn der Basis-HEAD keinen Baumeintrag für diesen Pfad enthält, auch wenn Merge-Basis und Ziel-HEAD ebenfalls keinen enthalten. Ein fokussierter Test setzt `core.hooksPath=.githooks`, ignoriert `.githooks/post-merge` mit Git, legt dort eine unversionierte ausführbare Marker-Datei an und prüft nach Completion: kein Marker, `status: skipped`, `reason: not_tracked_in_base` und erhaltener Merge. Positive Regressionstests belegen die Ausführung eines unveränderten getrackten Hooks, eines Hooks unter `.git/hooks` und eines extern konfigurierten Hooks.
- Gegen SOURCE stoppt `_post_merge_effect()` bei zwei `post_merge_hook`-Effekten mit derselben Work-Unit-ID und demselben Merge-Commit mit eindeutiger Widerspruchsdiagnose vor Hook-Pfadauflösung, Digest-Messung, neuem Intent und Hook-Start. Ein fokussierter Resume-Test prüft unveränderte Record-Anzahl und unveränderten Hook-Zähler; der bestätigte Merge bleibt erhalten. Der Fall mit genau einem vorhandenen, abgeschlossenen Drei- oder Vierfeld-Effekt bleibt idempotent, und ein einzelner offener Effekt behält seine bisherige Unknown-Sperre.
- Gegen SOURCE bleiben die bestehenden Tests für Hook-Fehler, fehlende Hooks, geänderte getrackte Hooks, Quittierung und Resume aussagekräftig. Der Orchestrator führt nach der Produktänderung die Standardmatrix `python3 -m pytest tests/ -v -m "not crash_harness"` aus. Der Operator führt den vollständigen Crashbeweis mit `python3 -m pytest tests/test_crash_harness.py -v` auf dem endgültigen Branch-HEAD vor dem Schlussreview aus; Agenten stellen keine Validierungsattestation aus.
<!-- audit:acceptance:end -->

## Umfang

<!-- audit:scope:begin -->
- `docs/internal/kettenende-nachbessern-followup03-implement-review-e6b85ff0.md`
- `docs/internal/slice-kettenende-nachbessern-followup03-arbeitsplan-01-hook-messfehler-protokollieren-basistracking-erzwingen-und-doppelte-inte.md`
- `src/workflow_completion.py`
- `tests/test_workflow_completion.py`
<!-- audit:scope:end -->

## Umsetzung

> Noch nicht dokumentiert.

## Abweichungen vom Plan

> Keine.

## Verlauf

<!-- audit:history:begin -->
- Runde 1: Umsetzung · Validierung grün · Prüfurteil freigegeben · 0 neu, 0 geschlossen.
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
> Korrektheit: `_hook_content_digest` liefert bei jedem OSError `unavailable` statt einer Ausnahme. Dadurch entsteht der Vierfeld-Intent mit `unavailable`. In `perform()` führt `"unavailable" in (operation[3], current_digest)` fail-closed zu `digest_unreadable`, auch wenn der Platzhalter vor und nach dem Intent gleich ist. Die Messung nach dem Intent hat einen eigenen Test. Die Prüfung auf `merge_disabled` läuft jetzt nach der Digest-Prüfung und lässt `missing` und `digest_unreadable` unverändert. `_hook_reason` trennt FileNotFoundError/NotADirectoryError (`missing`) von anderen OSErrors (`digest_unreadable`). `untracked_in_base` wird erst nach der Prüfung `changed_by_target_branch` ausgewertet. Hooks unter `.git` und externe Hooks bleiben ausführbar. Ein ignorierter unversionierter Hook wird mit `not_tracked_in_base` ausgelassen. Resume/Idempotenz: Doppelte passende Effekte lösen GitTransactionError aus, und zwar vor `_hook_path`, vor der Digest-Messung und vor einem neuen Intent. Der Test prüft, dass Record-Zahl, Marker und Merge-HEAD unverändert bleiben. Ein einzelner Effekt wird wie bisher wiederverwendet. Sicherheit: Alle neuen Fehlerpfade lassen den Hook aus. Scope und Attestation: Nur erlaubte Pfade wurden geändert, die Attestation mit PASS gehört zum selben Fingerprint.

Größtes Restrisiko:
> Die Duplikaterkennung setzt voraus, dass der echte Replay in `replay.side_effects` pro stabilem Schlüssel genau einen Eintrag liefert. Die fokussierten Tests nutzen nur die MemoryBridge. Der frühere `reversed`-Zugriff lässt offen, ob Intent und Ergebnis jemals getrennt erscheinen können. Außerdem greift `not_tracked_in_base` nur, wenn `base_head` und `target_head` übergeben werden.

Bruchbedingung:
> Die Annahme bricht, wenn der reale Ledger-Replay für einen abgeschlossenen Hook-Effekt zwei Einträge mit derselben Work-Unit-ID und demselben Commit liefert. Dann würde jeder Resume fälschlich mit `contradictory post-merge hook effects` stoppen. Sie bricht auch, wenn ein Aufrufer bei aktivem Merge `_post_merge_effect` ohne `base_head`/`target_head` aufruft. Dann würde ein unversionierter Worktree-Hook weiterhin ausgeführt.

Vorab-Risikoanalyse:
> Die wahrscheinlichste spätere Fehlerursache ist ein Crash-Harness-Lauf mit echtem Record-Replay, bei dem die neue Duplikatsperre einen legitimen, einzelnen abgeschlossenen Effekt doppelt sieht und den Abschluss blockiert. Dieser Lauf ist nicht Teil der Standardmatrix, sondern gehört zum Operator-Gate vor dem Schlussreview. Weniger wahrscheinlich ist ein Completion-Pfad ohne Basis-HEAD, der die Tracking-Prüfung umgeht. Beides würde der vom Operator geforderte vollständige Crashbeweis beziehungsweise das Schlussreview über den gesamten Branch sichtbar machen. Im geprüften Diff selbst finde ich keinen Mangel, der eine Ablehnung rechtfertigt.
<!-- audit:approval:end -->

<!-- audit:reference:begin -->
Technischer Bezug: Lauf `watch-20260925-204011.221754Z-56cdd847d1f5`, Arbeitseinheit(en) 2; Nachweise in der Recordkette.
<!-- audit:reference:end -->
