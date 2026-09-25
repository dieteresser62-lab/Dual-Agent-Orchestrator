# Gesamtaudit – kettenende-nachbessern_followup04-implement

<!-- audit:meta:begin -->
Aufgabe: kettenende-nachbessern_followup04-implement · Zielbranch: `feature/fehler-parameter-sweep-liefert-nie-ein-ergebnis-07e5c0fab2a5f4e3` · Lauf: `watch-20260925-210027.162590Z-c78c3b414fca` · Stand: abgeschlossen
<!-- audit:meta:end -->

## Übersicht

<!-- audit:overview:begin -->
| Slice | Titel | Stand | Commit | Runden | Befunde |
|---:|---|---|---|---:|---:|
| 1 | Hook-Voraussetzungen und Auslassungsgründe in Abschnitt 2.10 ergänzen | freigegeben | 2a5537de | 2 | 0 |
<!-- audit:overview:end -->

## Befunde

<!-- audit:findings:begin -->
| ID | Herkunft | Klasse | Stand | Titel |
|---|---|---|---|---|
| – | – | – | – | Keine. |
<!-- audit:findings:end -->

## Halte und Entscheidungen

<!-- audit:holds:begin -->
Keine.
<!-- audit:holds:end -->

## Abnahmereview

<!-- audit:acceptance-review:begin -->
Abnahmereview abgeschlossen.

Geprüft:
> Vollständiger Branch-Diff gegen aef5dc3d, alle 12 Teile gelesen; alle Pfade liegen in authorized_paths. Aktueller Slice (followup04): Abschnitt 2.10 in docs/reference/einrichtung.md mit src/workflow_completion.py abgeglichen. `_hook_reason()` liefert `changed_by_target_branch` vor `missing`/`symlink:`/`not_regular`/`not_executable`, danach `not_tracked_in_base` nur für einen sonst zulässigen Hook. Das entspricht der Doku („vorhandener, sonst zulässiger, aber nicht im Basis-HEAD getrackter Hook“). Ebenfalls stimmig: `digest_unreadable` aus lstat/stat-OSError bzw. `unavailable` in perform(), `content_changed`, `missing`, Ausführung mit Argument `0` erst nach allen Prüfungen, HOOK_OUTPUT_LIMIT=8192 (Doku „je 8192 Bytes“) und die drei Ablageorte (getrackt, .git/hooks, extern). Damit ist followup03 C-01 inhaltlich erledigt. Code erneut geprüft: RunProfile-Wire-Kompatibilität (test_pre_hook_run_profile_keeps_its_original_wire_shape prüft `recovered.to_dict() == document`), Segmentvalidierung des Archivmusters (test_archive_pattern_rejects_unsafe_config), frühe Prüfung vor Slice 1 (test_first_slice_conflict_stops_before_implementer: `calls == ["archive"]`), Gruppenbeendigung (test_timed_out_hook_kills_descendant_and_is_not_repeated: `not marker.exists()`), Mehrfachtreffer-Stopp vor `_hook_path` und Quittierung mit Operationsbindung (test_production_acknowledgment_resumes_real_open_hook_intent: ein Ergebnisrecord mit gleichem effect_key, Queue-Move nach done/). Attestation validation-[Hash ausgelassen] PASS (2552/0) für den gebundenen Fingerprint.

Größtes Restrisiko:
> Nicht entscheidbar: Die Quittierung hasht die Auftragsdatei über `read_text().encode()`. Die Berechnung von `state.task_digest` liegt nicht im Snapshot, und alle Fixtures verwenden LF. Ob eine CRLF-Auftragsdatei die Quittierung blockiert, lässt sich deshalb nicht belegen. Zweitens: Ein Absturz zwischen `directory.mkdir()` des neuen Laufordners und der ersten Umbenennung hinterlässt einen leeren Ordner, an dem der erneute perform_archive fail-closed stoppt. Das gehört zur schon bestehenden Klasse von Abbrüchen mitten im Archiv-perform (nach Umbenennungen ist der Baum dirty). `reconcile_archive` liegt nicht im Snapshot; eine Regression gegenüber dem vorhandenen fail-closed-Verhalten lässt sich damit nicht belegen.

Bruchbedingung:
> Ein Betreiber legt unter WSL eine CRLF-Auftragsdatei ab, `state.task_digest` ist bytebasiert, und nach einem Abbruch während des Hooks scheitert jede Quittierung mit „task content changed“. Oder der Prozess stirbt genau zwischen dem Anlegen des Laufordners und der ersten Umbenennung, und Resume verlangt das manuelle Entfernen des leeren Ordners.

Vorab-Risikoanalyse:
> Scheitert die Abschlusskette nach der Freigabe, dann am wahrscheinlichsten im Betrieb am Operator-Pfad und nicht an der Hook-Sicherheit. Typwechsel, Basistracking, Digestbindung, Messfehler, Mehrfachtreffer und Gruppenbeendigung sind mit tragenden Assertions belegt. Denkbar ist eine blockierte Quittierung bei abweichender Zeilenend-Normalisierung des Auftrags. Ebenso denkbar ist ein manueller Eingriff nach einem sehr engen Absturzfenster beim Anlegen des Laufordners. Eine zweite Möglichkeit sind Umgebungen mit symlinkten Repository- oder Home-Pfaden: Dort lässt die strikte Symlinkprüfung aller Pfadsegmente jeden Hook aus. Das ist dokumentiert und sicher, verfehlt aber den Nutzen. Die Doku-Änderung des aktuellen Slices stimmt mit den tatsächlichen Gründen in `_hook_reason()` und `_post_merge_effect()` überein.
<!-- audit:acceptance-review:end -->
