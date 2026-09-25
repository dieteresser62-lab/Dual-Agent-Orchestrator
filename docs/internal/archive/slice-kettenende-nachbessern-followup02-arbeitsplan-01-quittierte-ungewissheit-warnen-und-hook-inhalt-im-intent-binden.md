# Slice 1 von 1 – Quittierte Ungewissheit warnen und Hook-Inhalt im Intent binden

<!-- audit:status:begin -->
Freigegeben in Runde 1 · 0 Befunde, 0 geschlossen · Validierung grün
<!-- audit:status:end -->

## Ziel

<!-- audit:goal:begin -->
Ein quittierter ungewisser Hook-Ausgang bleibt beim Resume sichtbar; jeder neue Nachlauf-Intent bindet den vor dem Hook-Start ermittelten Inhaltsdigest, während alte offene Intents unverändert wiederaufgenommen werden.
<!-- audit:goal:end -->

## Akzeptanzkriterien

<!-- audit:acceptance:begin -->
- Gegen SOURCE enthält jeder neu erzeugte `post_merge_hook`-Intent die Operation mit Merge-Commit, aufgelöstem Hook-Pfad und vor der Ausführung gemessenem SHA-256-Digest der rohen Bytes. Fehlt ein regulärer Hook-Eintrag, steht im Digest-Feld exakt `unavailable`; der bestehende Grund für das Auslassen bleibt auswertbar. Ein fokussierter Test mit ausführbarem Standard-Hook prüft am Intent den SHA-256-Digest genau der ausgeführten Datei, den Merge-Commit und den Hook-Pfad.
- Gegen SOURCE weist `SideEffectPayload` eine Vierfeld-Operation mit ungültigem Digest zurück. Fokussierte Modelltests belegen gültige SHA-256- und `unavailable`-Werte sowie ungültiges Format; die bisherige Dreifeldform bleibt für alte Intent- und Ergebnisrecords gültig. Ein Resume-Test belegt, dass ein alter offener Intent samt Schlüssel bei Quittierung unverändert bleibt und keinen neuen Vierfeld-Intent erzeugt.
- Gegen SOURCE findet Resume ein abgeschlossenes Vierfeld-Intent/Ergebnis-Paar über Work-Unit-ID, Effektklasse und Merge-Commit, nachdem sich der Hook-Inhalt geändert hat. Ein fokussierter Test prüft den erfolgreichen Abschluss ohne neuen Intent, zweiten Hook-Lauf oder zweiten Ergebnisrecord; der gespeicherte Schlüssel und die gespeicherte Operation bleiben erhalten.
- Gegen SOURCE findet Resume ein abgeschlossenes Dreifeld-Altpaar über dieselben digestunabhängigen Merkmale. Ein fokussierter Test prüft den erfolgreichen Abschluss ohne neuen Vierfeld-Intent, zweiten Hook-Lauf oder zweiten Ergebnisrecord; Schlüssel und Operation des Altpaares bleiben erhalten.
- Gegen SOURCE vergleicht die Ausführung den aktuellen regulären Hook-Inhalt mit dem vor dem Intent gebundenen Digest und führt einen zwischenzeitlich geänderten Hook nicht als die gebundene Version aus. Ein fokussierter Test verändert die Datei nach dem Intent und vor der Ausführung, erwartet einen protokollierten Auslassungsgrund und keine Hook-Ausführung. Ein weiterer fokussierter Test verändert den Inhalt nach einem offenen Vierfeld-Intent und prüft beim Resume den fail-closed-Stopp wegen ungewissen physischen Ausgangs, ohne neuen Intent, neuen Ergebnisrecord oder erneuten Hook-Lauf; erst eine ausdrückliche Quittierung darf den Lauf fortsetzen.
- Gegen SOURCE gibt `_post_merge_effect()` beim Resume nach einer `acknowledged_unknown`-Quittierung eine sichtbare Warnung mit Status, Hook-Pfad, Merge-Commit und ausdrücklich ungewissem, nur quittiertem Ausgang aus. Ein fokussierter Test quittiert einen offenen Intent, führt Completion erneut aus und prüft die Warnung mit `caplog`, den unveränderten Hook-Zähler und den erfolgreichen Abschluss. Wiederholtes Resume erzeugt weder einen zweiten Hook-Lauf noch einen zweiten Ergebnisrecord; Merge und Abschluss bleiben erhalten.
- Gegen SOURCE bleiben die vorhandenen Nachlauf- und Crash-Fenster-Tests aussagekräftig. Die Orchestrator-Standardmatrix ist `python3 -m pytest tests/ -v -m "not crash_harness"`; der Operator führt den vollständigen Crashbeweis auf dem endgültigen Branch-HEAD vor dem Schlussreview aus. Die Agenten stellen keine Validierungsattestation aus.
<!-- audit:acceptance:end -->

## Umfang

<!-- audit:scope:begin -->
- `docs/internal/kettenende-nachbessern-followup02-implement-review-f89f9e49.md`
- `docs/internal/slice-kettenende-nachbessern-followup02-arbeitsplan-01-quittierte-ungewissheit-warnen-und-hook-inhalt-im-intent-binden.md`
- `src/artifact_models.py`
- `src/workflow_completion.py`
- `tests/test_artifact_models.py`
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
> Korrektheit: Neue Intents binden ("post_merge", commit, abspath(hook), Digest), wobei der Digest vor dem Intent über lstat, O_NOFOLLOW und einen dev/ino-Abgleich ermittelt wird; Nicht-Regulärdateien und fehlende Einträge ergeben "unavailable". Vertrag: SideEffectPayload akzeptiert drei oder vier Felder und prüft das vierte Feld auf SHA-256 oder "unavailable"; Tests decken gültige und ungültige Werte (Großbuchstaben-Hex) sowie Altrecords ab. Resume/Idempotenz: `previous` wird digestunabhängig über Effektklasse, Work-Unit und Commit gefunden, und der Bedingungsausdruck berechnet beim Resume keinen neuen Digest. Schlüssel und Operation bleiben für Vier- und Dreifeldpaare erhalten (parametrisierte Tests). Fehlerpfade: Wird der Inhalt nach dem Intent geändert, führt das zum Überspringen mit dem Grund content_changed. Ein offener Intent mit geändertem Inhalt stoppt fail-closed, ohne einen neuen Intent anzulegen oder den Hook auszuführen. acknowledged_unknown erzeugt eine Warnung mit Status, Pfad, Commit und dem Hinweis "uncertain"; bei wiederholtem Resume läuft der Hook kein zweites Mal. Die Attestation für den Fingerprint [Hash ausgelassen] steht auf PASS. Scope: alle Pfade liegen innerhalb der Allowlist.

Größtes Restrisiko:
> Zwischen dem Digestvergleich in perform und der Ausführung über den Pfad liegt ein TOCTOU-Fenster: Die ausgeführte Datei wird nicht über einen Deskriptor an den geprüften Inhalt gebunden. Außerdem bricht eine lesegeschützte, aber ausführbare Hook-Datei (PermissionError) die Completion nach dem Merge per GitTransactionError ab, statt ein Ergebnis zu protokollieren. Das ist fail-closed, erfordert aber einen Eingriff durch den Operator.

Bruchbedingung:
> Ein Prozess ersetzt den Hook genau zwischen `_hook_content_digest(hook) != operation[3]` und `_run_hook`. Dann läuft ungebundener Inhalt unter einem Intent, der einen anderen Digest trägt. Eine andere Möglichkeit: `_hook_path` liefert einen relativen Pfad und das Prozess-CWD ist nicht das Repository-Root, sodass `os.path.abspath` auf eine falsche Datei zeigt.

Vorab-Risikoanalyse:
> Wenn dieser Slice später scheitert, dann am wahrscheinlichsten, weil `os.path.abspath(hook)` einen relativen Hook-Pfad gegen das Prozess-CWD statt gegen das Repository-Root auflöst oder weil ein zwischen Prüfung und Ausführung getauschter Hook trotzdem läuft. Hinzu kommen Betriebsprobleme mit unlesbaren Hooks, die die Completion nach dem Merge dauerhaft anhalten. Die grünen Tests mit absolutem Root und mit core.hooksPath sprechen dagegen, dass der erste Fall aktuell auftritt.
<!-- audit:approval:end -->

<!-- audit:reference:begin -->
Technischer Bezug: Lauf `watch-20260925-201936.556814Z-94b9922493e9`, Arbeitseinheit(en) 2; Nachweise in der Recordkette.
<!-- audit:reference:end -->
