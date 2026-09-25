# Gesamtaudit – kettenende-nachbessern-implement

<!-- audit:meta:begin -->
Aufgabe: kettenende-nachbessern-implement · Zielbranch: `feature/fehler-parameter-sweep-liefert-nie-ein-ergebnis-07e5c0fab2a5f4e3` · Lauf: `watch-20260925-173857.063332Z-042e93b2e5d0` · Stand: abgeschlossen
<!-- audit:meta:end -->

## Übersicht

<!-- audit:overview:begin -->
| Slice | Titel | Stand | Commit | Runden | Befunde |
|---:|---|---|---|---:|---:|
| 1 | Prüfer-Policy für entscheidbare Risiken und Testabdeckung | freigegeben | 9fee043b | 1 | 0 |
| 2 | Laufgebundenes Archivmuster und frühe Zielprüfung | freigegeben | 36563118 | 1 | 0 |
| 3 | Protokollierter post-merge-Nachlauf | freigegeben | f2d95a0e | 2 | 3 |
| 4 | Anwenderdokumentation und Endabnahme | freigegeben | 4ed27868 | 3 | 1 |
<!-- audit:overview:end -->

## Befunde

<!-- audit:findings:begin -->
| ID | Herkunft | Klasse | Stand | Titel |
|---|---|---|---|---|
| C-01 | Slice 3 | Befund | geschlossen | Der produktive Quittierungspfad ist ungetestet |
| C-02 | Slice 3 | Befund | geschlossen | `acknowledge_unknown_post_merge` prüft `if _base_head(root, profile.base_branch or "") != commit and… |
| C-03 | Slice 3 | Befund | geschlossen | Akzeptanzkriterium 4 verlangt einen Beleg, dass alte Run-Profile und Record-Ketten keinen neuen Nachlauf… |
| C-04 | Slice 4 | Befund | geschlossen | Die fingerprintgebundene Attestation `validation-[Hash ausgelassen]` ist FAIL |
| C-05 | Slice DISCOVERY | Befund | offen | Die Timeout-Behandlung des post-merge-Hooks beendet nicht die Prozessgruppe |
| C-06 | Slice DISCOVERY | Befund | offen | Die Erkennung eines vom Zielbranch geänderten versionierten Hooks lässt Typwechsel durch |
| C-05 | Abnahme | Befund | offen | Die Timeout-Behandlung des post-merge-Hooks beendet nicht die Prozessgruppe. `_run_hook` in `src/workflow_completion.py` ruft `subprocess.run((str(hook), "0"), ..., timeout=HOOK_TIMEOUT_SECONDS)` ohne `start_new_session`/`process_group` und ohne `os.killpg` auf. Bei `TimeoutExpired` tötet `subprocess.run` nur den direkten Kindprozess. Nachkommen laufen weiter, etwa das `sleep` im Test-Hook `#!/bin/sh\nsleep 1` oder ein vom Hook gestarteter Build- oder Deploy-Prozess. Sie haben weiterhin Zugriff auf Repository und Host. Der Lauf zeichnet trotzdem `status: timeout` auf, schließt ab und verschiebt den Auftrag nach `outbox/done/`. Der freigegebene Arbeitsplan legt ausdrücklich fest: „Bei Zeitüberschreitung beendet er die Prozessgruppe, wartet begrenzt auf deren Ende und protokolliert Timeout und gegebenenfalls verbleibende Unsicherheit.“ Das fehlt, ohne dass eine Abweichung dokumentiert ist. Der Record beschreibt den Endzustand deshalb falsch: Nach „timeout“ können weiter Hook-Seiteneffekte entstehen. |
| C-06 | Abnahme | Befund | offen | Die Erkennung eines vom Zielbranch geänderten versionierten Hooks lässt Typwechsel durch. `_hook_reason` in `src/workflow_completion.py` prüft `git diff --name-only -z --diff-filter=AM fork target_head -- relative`. Der Status `T` (Typwechsel, z. B. Symlink → reguläre Datei) ist damit ausgeschlossen. Ein realistisches Szenario: Der Basisbranch versioniert `.githooks/post-merge` als Symlink auf ein Skript, ein verbreitetes Muster. Der Zielbranch ersetzt ihn durch eine reguläre ausführbare Datei mit beliebigem Inhalt. Nach dem Merge ist der Arbeitsbaum-Hook eine reguläre Datei, die Symlinkprüfung greift also nicht. `ls-tree` meldet ihn als versioniert, und der gefilterte Diff ist leer, weil der Status `T` ist. Deshalb führt `_run_hook` vom Zielbranch verfassten Code auf dem Host aus. Das verletzt die Zusage aus Plan, Slice-3-Akzeptanzkriterium und `docs/reference/einrichtung.md`, einen gegenüber der Merge-Basis durch den Zielbranch geänderten Hook auszulassen. Die Plananforderung „muss bereits im Basis-HEAD getrackt sein“ ist ebenfalls nicht umgesetzt. |
<!-- audit:findings:end -->

## Halte und Entscheidungen

<!-- audit:holds:begin -->
- Arbeitseinheit 3: Anlass Umfangserweiterung. Grund: Required paths: schemas/orchestrator-artifact-v2.schema.json Why required for current Slice: Das laufgebundene Archivmuster muss als neues Feld im RunProfile gespeichert werden. Das Record-Schema verbietet zusätzliche Felder; ohne Änderung dieser Datei würde ein neues Profil bei der Schema-Validierung abgewiesen. Der Pfad liegt außerhalb des freigegebenen Slice-Umfangs. Entscheidung: freigegeben.
- Arbeitseinheit 3: Anlass Umfangserweiterung. Grund: Required paths: tests/fixtures/cli-argument-evaluation-corpus-v1.json, tests/test_legacy_verifier.py, tests/test_orchestrator_runtime.py, tests/test_payload_dispatch.py Why required for current Slice: Das neue laufgebundene RunProfile-Feld ändert die CLI-Korpusdaten und bestehende Profilvergleiche. Die Altprofil-Testhilfe muss das zusätzliche optionale Feld setzen. Diese vier Pfade liegen außerhalb des freigegebenen Slice-Umfangs. Die Standardvalidierung ist deshalb noch nicht grün; die übrigen dabei erkannten Fehler wurden innerhalb des Umfangs behoben. Entscheidung: freigegeben.
- Arbeitseinheit 4: Anlass Umfangserweiterung. Grund: Required paths: schemas/orchestrator-artifact-v2.schema.json Why required for current Slice: Der Slice verlangt einen eigenen protokollierten post-merge-Seiteneffekt und eine laufgebundene Nachlauf-Policy. Das verbindliche Record-Schema erlaubt weder eine neue Seiteneffektklasse noch ein neues RunProfile-Feld. Ohne Schemaänderung würden die benötigten Records bei der Validierung abgewiesen. Die Datei liegt außerhalb des gebundenen Slice-Umfangs. Entscheidung: freigegeben.
- Arbeitseinheit 4: Anlass Umfangserweiterung. Grund: Required paths: README.md, tests/fixtures/cli-argument-evaluation-corpus-v1.json, tests/fixtures/function-size-baseline-v1.json, tests/test_cli_argument_evaluation_corpus.py, tests/test_legacy_verifier.py Why required for current Slice: Die neue CLI-Quittierung fügt öffentliche Optionen hinzu und verändert festgeschriebene Parser-, Laufzeit- und Funktionsgrößen-Nachweise. Der Altprofil-Test erzeugt ein RunProfile ohne das neue Feld und muss angepasst werden. Diese fünf Dateien liegen außerhalb des freigegebenen Slice-Umfangs. Die Standardvalidierung ergab 2498 bestandene und 23 fehlgeschlagene Tests; die verbleibenden Fehler betreffen diese Nachweise. Die Änderungen bleiben uncommittet. Entscheidung: freigegeben.
<!-- audit:holds:end -->

## Abnahmereview

<!-- audit:acceptance-review:begin -->
Abnahmereview abgeschlossen.

Geprüft:
> Geprüft am vollständigen Branch-Diff: Pfadgrenzen (alle geänderten Pfade liegen in authorized_paths); Archivmuster-Validierung (Segmente, Platzhalter, run_id- und Slug-Expansion, Pfadflucht); Laufbindung und Legacy-Wire-Kompatibilität des RunProfile (Reader-Defaults None/False, pop beim Schreiben, Schemaerweiterung); frühe Zielprüfung vor Slice 1; Rollback der verschachtelten Ordner; Reihenfolge Merge-Ergebnis → Hook-Intent → Ergebnis; Hook-Auflösung, Symlink-, Modus- und Tracked-Diff-Prüfung; Ausgabegrenze und Kürzungsvermerk; UNKNOWN-Reconcile ohne Wiederholung; Quittierungspfad mit Task-, Commit- und Intent-Bindung; CLI-Kombinationsregeln; README- und Einrichtungsdoku gegen Slice-4-Kriterien; Attestation validation-[Hash ausgelassen] PASS (2526/0) für den aktuellen Fingerprint.

Größtes Restrisiko:
> Nicht aus dem Snapshot entscheidbar, weil der Quelltext der Digest-Berechnung von `state.task_digest` nicht vorliegt: Die Quittierung hasht `read_text().encode()`, der Test vergleicht aber mit `read_bytes()`. Bei CRLF-Auftragsdateien könnten beide Werte auseinanderfallen, sodass die Quittierung dauerhaft mit „task content changed“ scheitert. Ebenfalls offen: Die Operation-Validierung für `post_merge_hook` akzeptiert nur 40-stellige Commit-IDs; bei SHA-256-Repositories würde der Intent erst nach dem Merge scheitern.

Bruchbedingung:
> Ein Zielbranch ersetzt einen versionierten Symlink-Hook durch eine reguläre Datei (C-05/C-06 betroffen), oder ein Hook startet Nachkommen, die das Timeout überleben. Ebenso: Ein Operator quittiert eine CRLF-Auftragsdatei, deren Digest anders berechnet wird als `state.task_digest`.

Vorab-Risikoanalyse:
> Scheitert der Rollout, dann am wahrscheinlichsten an Host-Seiteneffekten des neuen post-merge-Nachlaufs. Möglich sind ein vom Zielbranch per Typwechsel eingeschleuster Hook, der trotz Zusage ausgeführt wird, oder Nachkommen eines abgelaufenen Hooks, die nach dem Record „timeout“ und dem Queue-Move nach done/ weiterlaufen. Zweitrangig ist eine Quittierung, die in Randfällen (CRLF, SHA-256-Objektformat) blockiert und einen bereits gemergten Lauf ohne dokumentierten Ausweg hängen lässt. Archivmuster, frühe Zielprüfung und Legacy-Kompatibilität wirken durch Tests und Validierung solide belegt.

### C-05 – Die Timeout-Behandlung des post-merge-Hooks beendet nicht die Prozessgruppe. `_run_hook` in `src/workflow_completion.py` ruft `subprocess.run((str(hook), "0"), ..., timeout=HOOK_TIMEOUT_SECONDS)` ohne `start_new_session`/`process_group` und ohne `os.killpg` auf. Bei `TimeoutExpired` tötet `subprocess.run` nur den direkten Kindprozess. Nachkommen laufen weiter, etwa das `sleep` im Test-Hook `#!/bin/sh\nsleep 1` oder ein vom Hook gestarteter Build- oder Deploy-Prozess. Sie haben weiterhin Zugriff auf Repository und Host. Der Lauf zeichnet trotzdem `status: timeout` auf, schließt ab und verschiebt den Auftrag nach `outbox/done/`. Der freigegebene Arbeitsplan legt ausdrücklich fest: „Bei Zeitüberschreitung beendet er die Prozessgruppe, wartet begrenzt auf deren Ende und protokolliert Timeout und gegebenenfalls verbleibende Unsicherheit.“ Das fehlt, ohne dass eine Abweichung dokumentiert ist. Der Record beschreibt den Endzustand deshalb falsch: Nach „timeout“ können weiter Hook-Seiteneffekte entstehen.

Klasse: Befund · Stand: offen

Befund:
> Die Timeout-Behandlung des post-merge-Hooks beendet nicht die Prozessgruppe. `_run_hook` in `src/workflow_completion.py` ruft `subprocess.run((str(hook), "0"), ..., timeout=HOOK_TIMEOUT_SECONDS)` ohne `start_new_session`/`process_group` und ohne `os.killpg` auf. Bei `TimeoutExpired` tötet `subprocess.run` nur den direkten Kindprozess. Nachkommen laufen weiter, etwa das `sleep` im Test-Hook `#!/bin/sh\nsleep 1` oder ein vom Hook gestarteter Build- oder Deploy-Prozess. Sie haben weiterhin Zugriff auf Repository und Host. Der Lauf zeichnet trotzdem `status: timeout` auf, schließt ab und verschiebt den Auftrag nach `outbox/done/`. Der freigegebene Arbeitsplan legt ausdrücklich fest: „Bei Zeitüberschreitung beendet er die Prozessgruppe, wartet begrenzt auf deren Ende und protokolliert Timeout und gegebenenfalls verbleibende Unsicherheit.“ Das fehlt, ohne dass eine Abweichung dokumentiert ist. Der Record beschreibt den Endzustand deshalb falsch: Nach „timeout“ können weiter Hook-Seiteneffekte entstehen.

Akzeptanztest:
> Der Hook startet in einer eigenen Prozessgruppe bzw. Session. Bei Zeitüberschreitung beendet der Orchestrator die gesamte Gruppe (etwa SIGTERM, dann nach begrenzter Wartezeit SIGKILL auf die Gruppe) und wartet begrenzt auf ihr Ende. Bleibt danach ein Prozess übrig, vermerkt der Ergebnisrecord das als Unsicherheit. Ein fokussierter Test in `tests/test_workflow_completion.py` nutzt einen Hook, der einen langlebigen Nachkommen startet (z. B. `sleep 5 & wait` oder `sh -c 'sleep 5; touch marker'`), und eine kleine Timeout-Grenze. Er belegt: Status `timeout`, der Merge bleibt erhalten, und nach Ablauf der ursprünglichen Nachkommen-Laufzeit gibt es keinen Marker bzw. keinen überlebenden Prozess.

### C-06 – Die Erkennung eines vom Zielbranch geänderten versionierten Hooks lässt Typwechsel durch. `_hook_reason` in `src/workflow_completion.py` prüft `git diff --name-only -z --diff-filter=AM fork target_head -- relative`. Der Status `T` (Typwechsel, z. B. Symlink → reguläre Datei) ist damit ausgeschlossen. Ein realistisches Szenario: Der Basisbranch versioniert `.githooks/post-merge` als Symlink auf ein Skript, ein verbreitetes Muster. Der Zielbranch ersetzt ihn durch eine reguläre ausführbare Datei mit beliebigem Inhalt. Nach dem Merge ist der Arbeitsbaum-Hook eine reguläre Datei, die Symlinkprüfung greift also nicht. `ls-tree` meldet ihn als versioniert, und der gefilterte Diff ist leer, weil der Status `T` ist. Deshalb führt `_run_hook` vom Zielbranch verfassten Code auf dem Host aus. Das verletzt die Zusage aus Plan, Slice-3-Akzeptanzkriterium und `docs/reference/einrichtung.md`, einen gegenüber der Merge-Basis durch den Zielbranch geänderten Hook auszulassen. Die Plananforderung „muss bereits im Basis-HEAD getrackt sein“ ist ebenfalls nicht umgesetzt.

Klasse: Befund · Stand: offen

Befund:
> Die Erkennung eines vom Zielbranch geänderten versionierten Hooks lässt Typwechsel durch. `_hook_reason` in `src/workflow_completion.py` prüft `git diff --name-only -z --diff-filter=AM fork target_head -- relative`. Der Status `T` (Typwechsel, z. B. Symlink → reguläre Datei) ist damit ausgeschlossen. Ein realistisches Szenario: Der Basisbranch versioniert `.githooks/post-merge` als Symlink auf ein Skript, ein verbreitetes Muster. Der Zielbranch ersetzt ihn durch eine reguläre ausführbare Datei mit beliebigem Inhalt. Nach dem Merge ist der Arbeitsbaum-Hook eine reguläre Datei, die Symlinkprüfung greift also nicht. `ls-tree` meldet ihn als versioniert, und der gefilterte Diff ist leer, weil der Status `T` ist. Deshalb führt `_run_hook` vom Zielbranch verfassten Code auf dem Host aus. Das verletzt die Zusage aus Plan, Slice-3-Akzeptanzkriterium und `docs/reference/einrichtung.md`, einen gegenüber der Merge-Basis durch den Zielbranch geänderten Hook auszulassen. Die Plananforderung „muss bereits im Basis-HEAD getrackt sein“ ist ebenfalls nicht umgesetzt.

Akzeptanztest:
> `_hook_reason` wertet jede Änderung des Hook-Pfads zwischen Merge-Basis und Zielbranch-HEAD als `changed_by_target_branch`, also Hinzufügen, Inhalts-, Modus- und Typänderung. Dafür vergleicht es z. B. Modus und Objekt-ID aus `git ls-tree` für Fork und Ziel oder nutzt einen ungefilterten Diff außer Löschung. Ein Test in `tests/test_workflow_completion.py` legt im Basisbranch `.githooks/post-merge` als versionierten Symlink auf ein ausführbares Skript an und setzt `core.hooksPath=.githooks`. Im Zielbranch ersetzt er den Symlink durch eine reguläre ausführbare Datei mit Marker-Schreibzugriff. Nach der Completion existiert der Marker nicht, der Ergebnisrecord hat Status `skipped` mit Grund `changed_by_target_branch`, und der Merge bleibt erhalten.
<!-- audit:acceptance-review:end -->
