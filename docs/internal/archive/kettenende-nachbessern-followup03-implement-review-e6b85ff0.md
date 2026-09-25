# Gesamtaudit – kettenende-nachbessern_followup03-implement

<!-- audit:meta:begin -->
Aufgabe: kettenende-nachbessern_followup03-implement · Zielbranch: `feature/fehler-parameter-sweep-liefert-nie-ein-ergebnis-07e5c0fab2a5f4e3` · Lauf: `watch-20260925-204011.221754Z-56cdd847d1f5` · Stand: abgeschlossen
<!-- audit:meta:end -->

## Übersicht

<!-- audit:overview:begin -->
| Slice | Titel | Stand | Commit | Runden | Befunde |
|---:|---|---|---|---:|---:|
| 1 | Hook-Messfehler protokollieren, Basistracking erzwingen und doppelte Intents stoppen | freigegeben | b607df40 | 2 | 0 |
<!-- audit:overview:end -->

## Befunde

<!-- audit:findings:begin -->
| ID | Herkunft | Klasse | Stand | Titel |
|---|---|---|---|---|
| C-01 | Slice DISCOVERY | Befund | offen | Die Anwenderdokumentation beschreibt, wann ein `post-merge`-Hook zulässig ist, unvollständig und damit… |
| C-01 | Abnahme | Befund | offen | Die Anwenderdokumentation beschreibt, wann ein `post-merge`-Hook zulässig ist, unvollständig und damit irreführend. `docs/reference/einrichtung.md` (Abschnitt 2.10) nennt diese Bedingungen: reguläre ausführbare Datei, kein Symlink in einem Pfadsegment, nicht vom Zielbranch hinzugefügt oder geändert. Danach heißt es: „Ein zulässiger Hook wird mit Argument `0` … ausgeführt.“ `_hook_reason()` in `src/workflow_completion.py` lässt aber jeden vorhandenen Hook im Worktree außerhalb von `.git` mit `not_tracked_in_base` aus, wenn der Basis-HEAD keinen Baumeintrag dafür hat (`untracked_in_base = not base_entry`). Diese Regel fehlt in der Doku, ebenso der neue Grund `digest_unreadable`. Betroffen ist ein verbreitetes Muster: Husky v9 setzt `core.hooksPath=.husky/_`, und die Hook-Stubs dort sind generiert und per `.gitignore` ausgeschlossen. Solche Hooks erfüllen alle dokumentierten Bedingungen, werden aber nach jedem Merge ausgelassen. Der Betreiber sieht das nur an einer Warnung im Laufprotokoll. Die Einrichtung erklärt es nicht und nennt keinen Ausweg, etwa einen getrackten Hook oder einen Hook unter `.git/hooks` bzw. außerhalb des Worktrees. |
<!-- audit:findings:end -->

## Halte und Entscheidungen

<!-- audit:holds:begin -->
Keine.
<!-- audit:holds:end -->

## Abnahmereview

<!-- audit:acceptance-review:begin -->
Abnahmereview abgeschlossen.

Geprüft:
> Geprüft habe ich den vollständigen Branch-Diff gegen aef5dc3d. Alle Pfade liegen in authorized_paths. Die offenen Befunde aus followup02 sind behoben: (1) `_hook_content_digest()` liefert bei jedem OSError `unavailable`. `perform()` erzwingt dann `digest_unreadable` mit Vorrang vor `merge_disabled`, und der Test `test_initial_hook_digest_denial_records_terminal_skip` (lstat/open × Merge an/aus × transient) prüft den Vierfeld-Intent, den Grund, caplog und ein Resume ohne neue Records. (2) `untracked_in_base` ergibt `not_tracked_in_base`, belegt durch `test_ignored_untracked_worktree_hook_is_skipped`. (3) Bei `len(matches) > 1` wird vor `_hook_path` abgebrochen, belegt durch `test_duplicate_hook_effects_stop_before_any_hook_work`. Außerdem geprüft: Archivmuster und frühe Zielprüfung, RunProfile-Wire-Kompatibilität, Gruppenbeendigung, ls-tree-Vergleich, Quittierungsbindung, CLI-Regeln und Watch-Halt. Die Attestation für diesen Fingerprint ist PASS (2552/0).

Größtes Restrisiko:
> Die Quittierung hasht die Auftragsdatei in `orchestrator.run_production_workflow` und in `acknowledge_unknown_post_merge` über `read_text().encode()`, also mit Newline-Normalisierung. Die Watch-Identität und die Testerwartung nutzen dagegen `read_bytes()`. Ob `state.task_digest` bytebasiert ist, lässt sich nicht entscheiden: Seine Berechnung liegt außerhalb des Diffs, und alle Test-Fixtures verwenden nur LF, wo beide Formen übereinstimmen. Hinzu kommt ein kleines Crash-Fenster zwischen `mkdir()` des Laufordners und der ersten Umbenennung, nach dem Resume fail-closed am vorhandenen leeren Ordner stoppt.

Bruchbedingung:
> Ein Betreiber unter WSL legt eine CRLF-Auftragsdatei ab, und `state.task_digest` ist bytebasiert. Nach einem Abbruch während des Hooks scheitert dann jede Quittierung mit „task content changed“, und der bereits gemergte Lauf bleibt ohne Ausweg stehen. Oder ein Husky-Repository erwartet seinen `post-merge`-Hook, der wegen `not_tracked_in_base` stets ausgelassen wird.

Vorab-Risikoanalyse:
> Scheitert die Abschlusskette im Betrieb, dann am wahrscheinlichsten nicht an der Hook-Sicherheit. Typwechsel, Basistracking, Digestbindung, Gruppenbeendigung und die Behandlung doppelter Intents sind jetzt mit tragenden Assertions belegt. Wahrscheinlicher ist ein Scheitern an der Erwartung des Betreibers: Generierte, ignorierte Hook-Verzeichnisse wie Husky werden still ausgelassen, und die Einrichtung erklärt das nicht (C-01). Zweitrangig sind Randfälle beim Operator-Pfad, etwa eine CRLF-Auftragsdatei bei der Quittierung, ein zwischenzeitlich bewegter Basisbranch oder ein Crash direkt nach dem Anlegen des Laufordners. Sie führen jeweils zu einem fail-closed-Halt, den der Betreiber nur manuell auflösen kann.

### C-01 – Die Anwenderdokumentation beschreibt, wann ein `post-merge`-Hook zulässig ist, unvollständig und damit irreführend. `docs/reference/einrichtung.md` (Abschnitt 2.10) nennt diese Bedingungen: reguläre ausführbare Datei, kein Symlink in einem Pfadsegment, nicht vom Zielbranch hinzugefügt oder geändert. Danach heißt es: „Ein zulässiger Hook wird mit Argument `0` … ausgeführt.“ `_hook_reason()` in `src/workflow_completion.py` lässt aber jeden vorhandenen Hook im Worktree außerhalb von `.git` mit `not_tracked_in_base` aus, wenn der Basis-HEAD keinen Baumeintrag dafür hat (`untracked_in_base = not base_entry`). Diese Regel fehlt in der Doku, ebenso der neue Grund `digest_unreadable`. Betroffen ist ein verbreitetes Muster: Husky v9 setzt `core.hooksPath=.husky/_`, und die Hook-Stubs dort sind generiert und per `.gitignore` ausgeschlossen. Solche Hooks erfüllen alle dokumentierten Bedingungen, werden aber nach jedem Merge ausgelassen. Der Betreiber sieht das nur an einer Warnung im Laufprotokoll. Die Einrichtung erklärt es nicht und nennt keinen Ausweg, etwa einen getrackten Hook oder einen Hook unter `.git/hooks` bzw. außerhalb des Worktrees.

Klasse: Befund · Stand: offen

Befund:
> Die Anwenderdokumentation beschreibt, wann ein `post-merge`-Hook zulässig ist, unvollständig und damit irreführend. `docs/reference/einrichtung.md` (Abschnitt 2.10) nennt diese Bedingungen: reguläre ausführbare Datei, kein Symlink in einem Pfadsegment, nicht vom Zielbranch hinzugefügt oder geändert. Danach heißt es: „Ein zulässiger Hook wird mit Argument `0` … ausgeführt.“ `_hook_reason()` in `src/workflow_completion.py` lässt aber jeden vorhandenen Hook im Worktree außerhalb von `.git` mit `not_tracked_in_base` aus, wenn der Basis-HEAD keinen Baumeintrag dafür hat (`untracked_in_base = not base_entry`). Diese Regel fehlt in der Doku, ebenso der neue Grund `digest_unreadable`. Betroffen ist ein verbreitetes Muster: Husky v9 setzt `core.hooksPath=.husky/_`, und die Hook-Stubs dort sind generiert und per `.gitignore` ausgeschlossen. Solche Hooks erfüllen alle dokumentierten Bedingungen, werden aber nach jedem Merge ausgelassen. Der Betreiber sieht das nur an einer Warnung im Laufprotokoll. Die Einrichtung erklärt es nicht und nennt keinen Ausweg, etwa einen getrackten Hook oder einen Hook unter `.git/hooks` bzw. außerhalb des Worktrees.

Akzeptanztest:
> Gegen SOURCE nennt Abschnitt 2.10 von `docs/reference/einrichtung.md` ausdrücklich zwei Regeln. Erstens: Ein Hook im Repository-Arbeitsbaum außerhalb von `.git` muss im Basis-HEAD getrackt sein, sonst wird er mit dem Grund `not_tracked_in_base` ausgelassen. Als Beispiel ist ein ignoriertes, generiertes Hook-Verzeichnis wie Husky `.husky/_` genannt. Zweitens: Ein nicht lesbarer oder nicht messbarer Hook wird mit `digest_unreadable` ausgelassen. Außerdem beschreibt die Doku, wie ein Betreiber einen solchen Hook zulässig macht: getrackt im Basisbranch, unter `.git/hooks` oder als externer Pfad. Die Beschreibung stimmt mit den tatsächlichen Gründen in `_hook_reason()` und `_post_merge_effect()` überein.
<!-- audit:acceptance-review:end -->
