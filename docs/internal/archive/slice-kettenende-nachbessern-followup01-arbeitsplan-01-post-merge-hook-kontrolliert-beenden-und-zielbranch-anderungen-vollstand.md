# Slice 1 von 1 – Post-Merge-Hook kontrolliert beenden und Zielbranch-Änderungen vollständig erkennen

<!-- audit:status:begin -->
Freigegeben in Runde 2 · 1 Befund, 1 geschlossen · Validierung grün
<!-- audit:status:end -->

## Ziel

<!-- audit:goal:begin -->
Ein abgelaufener Hook hinterlässt keine unbeachteten Prozesse seiner Prozessgruppe; ein durch den Zielbranch veränderter versionierter Hook wird unabhängig von der Git-Änderungsart ausgelassen.
<!-- audit:goal:end -->

## Akzeptanzkriterien

<!-- audit:acceptance:begin -->
- Gegen SOURCE startet `_run_hook()` den Hook in einer eigenen Session oder Prozessgruppe. Nach Timeout signalisiert der Orchestrator die gesamte gestartete Gruppe mit `SIGTERM`, danach nötigenfalls mit `SIGKILL`, wartet jeweils begrenzt und führt weder ein unbegrenztes `wait()` noch ein unbegrenztes Lesen aus. Ein fokussierter Test startet einen langlebigen Hook-Nachkommen, der nach seiner ursprünglichen Laufzeit einen Marker schreiben würde. Bei kleiner Timeout-Grenze weist er `status: timeout`, einen erhaltenen Merge und das Ausbleiben des Markers nach Ablauf jener Laufzeit nach.
- Gegen SOURCE enthält der terminale Timeout-Resultattext eine eindeutige Unsicherheitsangabe für nicht bestätigte Gruppenbeendigung. Fokussierte Tests belegen sowohl den bestätigten Bereinigungsfall als auch einen simulierten Fall, in dem Signalisierung oder Prüfung keinen sicheren Endzustand belegt; die Warnung verschweigt diesen Fall nicht. Der Hook läuft beim Resume nicht erneut und ein normaler Timeout macht den Merge nicht rückgängig.
- Gegen SOURCE behandelt `_hook_reason()` Hinzufügen sowie Inhalts-, Modus- und Typänderungen des versionierten Hook-Pfads zwischen Merge-Basis und Zielbranch-HEAD als `changed_by_target_branch` und verlangt Tracking im Basis-HEAD. Ein fokussierter Repositorytest versioniert im Basisbranch `.githooks/post-merge` als Symlink auf ein ausführbares Skript, setzt `core.hooksPath=.githooks` und ersetzt den Symlink im Zielbranch durch eine reguläre ausführbare Marker-Datei. Nach Completion existiert der Marker nicht, das Hook-Ergebnis ist `skipped` mit Grund `changed_by_target_branch`, und der Merge bleibt erhalten.
- Gegen SOURCE prüfen weitere kompakte Fälle die fehlende Basis-HEAD-Eintragung sowie mindestens eine reine Modusänderung; ein unveränderter zulässiger Hook und ein externer konfigurierter Hook bleiben ausführbar. Die vorhandenen Tests für Hook-Ausgabe, Fehler, Symlink-Pfade, Merge ohne Nachlauf und Resume bleiben grün. Der Orchestrator führt die Standardmatrix `python3 -m pytest tests/ -v -m "not crash_harness"` aus; der Operator führt den vollständigen Crashbeweis auf dem endgültigen Branch-HEAD vor dem Schlussreview aus. Agenten erzeugen keine Validierungsattestation.
<!-- audit:acceptance:end -->

## Umfang

<!-- audit:scope:begin -->
- `docs/internal/kettenende-nachbessern-followup01-implement-review-7d38491b.md`
- `docs/internal/slice-kettenende-nachbessern-followup01-arbeitsplan-01-post-merge-hook-kontrolliert-beenden-und-zielbranch-anderungen-vollstand.md`
- `src/workflow_completion.py`
- `tests/test_workflow_completion.py`
<!-- audit:scope:end -->

## Umsetzung

> Noch nicht dokumentiert.

## Abweichungen vom Plan

> Keine.

## Verlauf

<!-- audit:history:begin -->
- Runde 1: Umsetzung · Validierung grün · Prüfurteil abgelehnt · 1 neu, 0 geschlossen.
- Runde 2: Korrektur · Validierung grün · Prüfurteil freigegeben · 0 neu, 1 geschlossen.
<!-- audit:history:end -->

## Befunde

<!-- audit:findings:begin -->
### C-01 – `_hook_reason()` führt die neue Git-Prüfung jetzt vor der lstat-Schleife aus, die bisher `missing`,…

Klasse: Befund · Stand: geschlossen

Befund:
> `_hook_reason()` führt die neue Git-Prüfung jetzt vor der lstat-Schleife aus, die bisher `missing`, `not_regular` und `not_executable` bestimmt hat. Liegt `core.hooksPath` im Repository außerhalb von `.git` (z. B. `.githooks` oder `.husky`) und gibt es dort gar keinen `post-merge`-Hook, ist `ls-tree` für Basis-HEAD leer. Die Funktion liefert dann `changed_by_target_branch` statt `missing`. `_post_merge_effect()` gibt deshalb bei jedem Abschluss eine Warnung `skipped: changed_by_target_branch` aus. Außerdem hält das Hook-Ergebnis fälschlich fest, der Zielbranch habe den Hook verändert, obwohl er weder im Basis-HEAD noch in der Merge-Basis noch im Zielbranch existiert. Das verfälscht die Aussage im Audit-Ergebnis und erzeugt in Repositories mit versionierten Hook-Verzeichnissen ohne post-merge unnötige Warnungen. Die bisherige Unterscheidung „fehlt = keine Warnung“ geht damit für diese Konfiguration verloren.

Akzeptanztest:
> Gegen SOURCE liefert `_hook_reason()` für einen im Dateisystem fehlenden Hook unter einem repository-internen `core.hooksPath` weiterhin `missing`, wenn der Pfad weder in Merge-Basis noch im Zielbranch-HEAD versioniert ist, und `_post_merge_effect()` protokolliert dafür keine Warnung. Ein fokussierter Test setzt `core.hooksPath=.githooks` mit einem versionierten Nachbar-Hook, aber ohne `post-merge`, führt Completion aus und weist `status: skipped`, `reason: missing` und einen erhaltenen Merge nach. Hinzufügen, Inhalts-, Modus- und Typänderungen durch den Zielbranch sowie fehlendes Tracking im Basis-HEAD bei vorhandenem Hook bleiben `changed_by_target_branch`, und die neuen Tests bleiben grün.

Antwort des Implementierers, Runde 2 (angenommen):
> Die Einstufung `changed_by_target_branch` setzt nun einen vorhandenen Ziel-Eintrag ohne Basis-Tracking oder einen tatsächlichen Unterschied zur Merge-Basis voraus. Der neue Test bestätigt für einen fehlenden `post-merge`-Hook in einem versionierten Hook-Verzeichnis `skipped`, `missing`, keine Warnung und einen erhaltenen Merge. Die fokussierten Tests und die vollständige Standardvalidierung sind grün (2533 bestanden).

Abschlussbegründung des Prüfers:
> Behoben. `_hook_reason()` liefert `changed_by_target_branch` jetzt nur noch in zwei Fällen: wenn der Zielbranch einen Eintrag hat, der im Basis-HEAD fehlt, oder wenn sich der `ls-tree`-Eintrag (Modus, Typ, OID) der Merge-Basis vom Eintrag im Zielbranch unterscheidet. Ist der Pfad in keinem der drei Commits versioniert, sind alle Einträge leer und gleich. Die Prüfung fällt dann auf die lstat-Schleife durch und liefert `missing`. `_post_merge_effect()` gibt für `missing` weiterhin keine Warnung aus. `test_missing_hook_in_tracked_hook_directory_is_silent` deckt genau diese Konfiguration ab: `.githooks` mit versioniertem Nachbar-Hook `pre-push`, ohne `post-merge`. Geprüft werden `skipped`/`missing`, fehlende Warnung und der erhaltene Merge. Typ-, Inhalts- und Modusänderung sowie das fehlende Basis-Tracking bei vorhandenem Hook bleiben durch Tests als `changed_by_target_branch` abgedeckt. Ein unveränderter Hook läuft weiterhin. Die Attestation für diesen Fingerprint ist PASS (2533 bestanden).
<!-- audit:findings:end -->

## Validierung

<!-- audit:validation:begin -->
- Runde 1: `python3 -m pytest tests/ -v -m 'not crash_harness'` · grün · Exitcode 0.

- Runde 2: `python3 -m pytest tests/ -v -m 'not crash_harness'` · grün · Exitcode 0.
<!-- audit:validation:end -->

## Abschlussprüfung

<!-- audit:approval:begin -->
Geprüft:
> Geprüft habe ich die Korrektheit der Entscheidungsmatrix in `_hook_reason()` für die Kombinationen aus Basis-, Merge-Basis- und Zieleintrag: fehlt überall, vom Ziel hinzugefügt, vom Ziel gelöscht, nur in der Basis vorhanden, unverändert, sowie Änderung von Modus, Typ oder Inhalt. Weitere Prüfpunkte: der Ausschluss von `.git`-internen Hooks und externen Pfaden über `relative`, die Warnlogik in `_post_merge_effect()`, der Timeout-Pfad mit `start_new_session`, `killpg`-Eskalation, begrenztem Warten und `termination_uncertain`, außerdem Resume ohne erneuten Hook-Lauf. Pfadgrenze eingehalten; Attestation-Fingerprint stimmt überein.

Größtes Restrisiko:
> Löscht der Zielbranch einen in der Merge-Basis versionierten Hook, ergibt sich `changed_by_target_branch` mit Warnung statt `missing`. Das ist fachlich vertretbar und liegt außerhalb des Kriteriums von C-01. Außerdem können Hook-Nachkommen, die mit eigenem `setsid` aus der Prozessgruppe ausbrechen, nicht erfasst werden.

Bruchbedingung:
> Das Ergebnis wäre falsch, wenn `base_head` oder `target_head` beim Aufruf nach dem Merge nicht den Stand vor dem Merge bezeichnen würden. Dann würde die Merge-Basis den Zielbranch-HEAD enthalten, der Vergleich wäre immer gleich und Änderungen des Zielbranchs blieben unerkannt. Die Tests zu Typ- und Modusänderung widerlegen das allerdings für den aktuellen Aufrufpfad.

Vorab-Risikoanalyse:
> Am wahrscheinlichsten scheitert dieser Slice in der Praxis an unerwarteten Warnungen, wenn der Zielbranch einen Hook gelöscht hat, oder an einem `core.hooksPath` über einen Symlink ins Repository. `root.resolve()` und `os.path.abspath` können dort verschiedene Präfixe liefern. Dann ist `relative` None, der Git-Vergleich entfällt und ein vom Zielbranch geänderter Hook könnte laufen. Das betrifft seltene Konfigurationen und ist durch C-01 nicht verschlechtert.
<!-- audit:approval:end -->

<!-- audit:reference:begin -->
Technischer Bezug: Lauf `watch-20260925-194835.440484Z-73d1fa2bac0d`, Arbeitseinheit(en) 2; Nachweise in der Recordkette.
<!-- audit:reference:end -->
