# Arbeitsplan: Kettenende nachbessern

## Ausgangslage und Entscheidungen

`src/workflow_completion.py` verschiebt neu angelegte, unmittelbar unter `docs/internal/` liegende Dateien derzeit flach nach `docs/internal/archive/`. Die Zielprüfung findet erst vor dem Archiv-Commit statt, nachdem die Umsetzung und das Schlussreview abgeschlossen sind. Archiv-Commit und lokaler `--no-ff`-Merge schalten Git-Hooks während der Transaktion aus. `src/prompts.py` fordert ein adversariales Review, verlangt aber keine Quellenprüfung für entscheidbare Restrisiken und keine tragende Assertion bei behaupteter Testabdeckung.

Für den Merge-Nachlauf wird der wirksame ausführbare `post-merge`-Hook des Zielrepositories gewählt. Das nutzt die vorhandene Repository-Konvention ohne einen zweiten, möglicherweise abweichenden Befehl in `orchestrator.toml`. Der Orchestrator ermittelt den Hook **nach dem bestätigten Merge-Ergebnisrecord** aus der wirksamen Git-Konfiguration ohne das transaktionsinterne `core.hooksPath=/dev/null`: Ein gesetztes `core.hooksPath` hat Vorrang, relative Pfade beziehen sich wie bei Git auf das Arbeitsverzeichnis des Repositories; sonst gilt `git rev-parse --git-path hooks/post-merge` auch für verknüpfte Worktrees. Ein absoluter Pfad außerhalb des Worktrees gilt nur, wenn er aus dieser Git-Konfiguration stammt. Alle Pfadsegmente und der Hook selbst müssen symlinkfrei sein; der Hook muss eine reguläre ausführbare Datei sein. Liegt er im versionierten Worktree, muss er bereits im Basis-HEAD getrackt sein und gegenüber dem Merge-Base im Zielbranch unverändert bleiben. Ein durch den Zielbranch hinzugefügter oder geänderter Hook wird **nicht** auf dem Host ausgeführt. Der Orchestrator meldet den vorhandenen, aber ausgelassenen Hook samt Grund; er meldet auch einen unter `.git/hooks/` vorhandenen Hook, den ein konfiguriertes `core.hooksPath` überschattet. Der Hook wird vom Worktree-Wurzelverzeichnis aus mit Git-Argument `0` höchstens einmal je Merge-Commit aufgerufen; während Archiv-Commit und Merge bleiben Hooks abgeschaltet.

Der Hook liegt außerhalb der Merge-Transaktion. Nach dem bestätigten `git_merge`-Ergebnisrecord schreibt der Orchestrator einen an Run-ID und Merge-Commit gebundenen Nachlauf-Intent, führt den Hook mit einer festen Obergrenze von 600 Sekunden aus und schreibt ein terminales Ergebnis. Erst dann liefert `complete_chain` Erfolg zurück; im Watch-Modus folgen Erfolgsevidenz und Queue-Move nach `outbox/done/`. Exitcode ungleich null und Zeitüberschreitung sind protokollierte Warnungen; der Merge bleibt bestehen und der Auftrag kann regulär nach `done/` gelangen. Der Orchestrator erfasst stdout und stderr getrennt bis je 1 MiB im Ergebnisrecord (kodiert ohne Steuerzeichen, mit Kürzungskennzeichen) und gibt eine begrenzte Fassung im Laufprotokoll aus. Bei Zeitüberschreitung beendet er die Prozessgruppe, wartet begrenzt auf deren Ende und protokolliert Timeout und gegebenenfalls verbleibende Unsicherheit.

Ein Prozessabbruch zwischen Intent und Ergebnis erlaubt keine sichere Aussage, ob der Hook lief. Resume startet ihn daher **nicht** erneut, sondern hält per `SideEffectReconciliationError` mit der Diagnose `POST-MERGE-HOOK-OUTCOME-UNKNOWN` als wiederaufnehmbarer Operator-Halt. Watch zählt diesen Halt nicht als technischen Retry und verschiebt den bereits gemergten Auftrag keinesfalls nach `outbox/failed/*.poison`. Der Operator prüft den Hook-Zustand und quittiert den offenen Intent mit `--task-file <unveränderter-Auftrag> --resume --acknowledge-post-merge-unknown --hook-rationale <Begründung>`. Die CLI bindet diese Entscheidung an den offenen Intent und den unveränderten Merge-Commit und schreibt den begründeten Ergebnisrecord `acknowledged_unknown`, ohne den Hook auszuführen oder Erfolg zu behaupten; anschließend setzt Resume den Lauf fort, meldet die Ungewissheit als Warnung und lässt den Watch-Auftrag nach `done/` gelangen. Ein normaler Fehler oder Timeout benötigt keine solche Quittierung. Ein neues Run-Profile bindet die aktivierte Nachlauf-Policy: Fehlt nach dem Merge-Ergebnisrecord noch ein Intent, darf nur bei diesem neuen Profil der Nachlauf erstmals beginnen. Alten Run-Profilen fehlt diese Policy; Resume löst für sie rückwirkend keinen Hook aus. Alte Records bleiben unverändert.

Für neue Läufe wird `[workflow] archive_run_directory` als sicheres relatives Pfadmuster unter `docs/internal/archive/` gebunden. Vorgabe ist `{run_id}`; das Muster muss `{run_id}` als vollständiges Pfadsegment enthalten. Optional sind `{year}` (aus der Laufkennung) und `{branch_slug}` zulässig, etwa `{year}-feature/{run_id}` für die Konvention der RuhestandsApp. Der vollständige, unveränderliche `run_id` trennt gleichnamige Aufträge. Ungültige, absolute oder ausbrechende Pfade, Symlinks und vorhandene Zielordner werden **vor dem ersten Umsetzungsslice** abgewiesen. Die bestehende Prüfung der tatsächlichen Quelldateien und der Merge-Vorschau bleibt am Kettenende bestehen. Alte Run-Profile ohne neues Feld behalten beim Resume ihren bisherigen flachen Archivpfad; alte Records werden weder umgeschrieben noch migriert.

Die neue Prüferregel gehört in die native System-Policy. Ein zusätzliches Review-Ausgabefeld ist nicht erforderlich: vorhandene Findings und strukturierte Prüfevidenz können die Entscheidung tragen. Für Archivmuster und Nachlauf sind dagegen neue laufgebundene Metadaten beziehungsweise ein eigener protokollierter Prozess-Seiteneffekt begründet, damit Resume und Idempotenz nicht von später geänderter Konfiguration abhängen. Die Wire-Leser müssen fehlende neue Felder alter Records explizit kompatibel deuten. Der Nachlauf-Seiteneffekt bindet Run-ID, Merge-Commit, aufgelösten Hook-Pfad und Hook-Digest; ein Resultat kann Erfolg, Fehler, Timeout, Auslassen oder eine begründete Quittierung der Ungewissheit sein. Die Quittierung kodiert die Operator-Begründung im validierten, append-only Ergebnisrecord; ein zweiter Ergebnisrecord für denselben Intent ist unzulässig.

## Umsetzung

### Slice 1 - Prüfer-Policy für entscheidbare Risiken und Testabdeckung

**Ziel**

Die native Policy verpflichtet den Prüfer, im Snapshot entscheidbare Aussagen nachzumessen und einen bestätigten Mangel als Finding zu erfassen.

**Exakter Änderungspfad**

- `src/prompts.py`
- `tests/test_prompts.py`

**Akzeptanzkriterien**

- Gegen SOURCE ist die Policy ausdrücklich auf Plan-, Slice- und Schlussreviews anwendbar: Ein im bereitgestellten Snapshot entscheidbares Restrisiko wird anhand der Quelle geprüft; bestätigt es sich, entsteht ein Finding statt einer bloßen Restrisiko-Notiz.
- Gegen SOURCE darf „nicht verifizierbar“ nur mit konkretem Grund verwendet werden, warum der tatsächlich bereitgestellte Snapshot die Entscheidung nicht erlaubt; der Prüfer prüft vorhandene Dateien und Diff, bevor er diese Grenze behauptet.
- Gegen SOURCE benennt der Prüfer bei behaupteter Abdeckung eines Akzeptanzkriteriums durch einen Test dessen tragende Assertion und den Bezug zum betroffenen Pfad oder eröffnet ein Finding. Ein fokussierter Test hält den verbindlichen Wortlaut der Policy fest; das native Review-Schema bleibt unverändert.

### Slice 2 - Laufgebundenes Archivmuster und frühe Zielprüfung

**Ziel**

Jeder neue Lauf archiviert seine Dokumente in einem eigenen, konfigurierbaren und eindeutig benannten Unterordner; erkennbare Zielkonflikte stoppen vor dem ersten Umsetzungsslice.

**Exakter Änderungspfad**

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

**Akzeptanzkriterien**

- Gegen SOURCE akzeptiert `[workflow] archive_run_directory` nur die dokumentierten Platzhalter und einen kanonischen, relativen Unterpfad mit `{run_id}` als vollständigem Segment. Der neue Wert wird beim Laufstart gebunden; ein fehlender Wert in alten Records bedeutet beim Resume unverändertes flaches Archivverhalten.
- Gegen SOURCE wird der konkrete Zielordner aus gebundenem Muster und Laufidentität vor dem ersten Umsetzungsslice geprüft. Ein vorhandener Zielordner, eine Datei oder ein Symlink in einem Zielsegment wird mit dem betroffenen Pfad gemeldet, bevor Codex für Slice 1 startet. Beim Kettenende werden dieselben gebundenen Bytes und die bestehenden Datei- und Merge-Vorabprüfungen verwendet.
- Gegen SOURCE archivieren zwei unabhängige Läufe mit gleichem Auftrags- und Quelldateinamen ohne Konflikt in getrennte Unterordner. Der Archiv-Commit enthält weiterhin nur die vorgesehenen Umbenennungen und behält die bestehende Rollback- und Resume-Semantik.
- Gegen SOURCE prüfen fokussierte Konfigurations-, Record-Kompatibilitäts-, Frühstopp-, Archiv- und Crash-Grenztests auch ungültige Muster, Pfadflucht, Ziel-Symlinks und alte Profile. Der eigenständige vollständige Crashbeweis bleibt dem Operator-Gate auf dem Endstand vorbehalten.

### Slice 3 - Protokollierter post-merge-Nachlauf

**Ziel**

Ein wirksamer `post-merge`-Hook läuft nach einem erfolgreichen lokalen Merge einmal und ein Fehler bleibt sichtbar, ohne den Merge rückgängig zu machen.

**Exakter Änderungspfad**

- `src/agent_runtime.py`
- `src/artifact_models.py`
- `src/cli.py`
- `src/inbox_watcher.py`
- `src/orchestrator.py`
- `src/workflow_baseline.py`
- `src/workflow_completion.py`
- `src/workflow_recovery.py`
- `tests/test_artifact_models.py`
- `tests/test_cli.py`
- `tests/test_crash_harness.py`
- `tests/test_inbox_watcher.py`
- `tests/test_workflow_baseline.py`
- `tests/test_workflow_completion.py`
- `tests/test_workflow_recovery.py`

**Akzeptanzkriterien**

- Gegen SOURCE ermittelt der Orchestrator den wirksamen `post-merge`-Hook nach dem bestätigten Merge-Ergebnisrecord aus `core.hooksPath` oder dem Git-Hook-Pfad, prüft reguläre Datei, Ausführbarkeit und jedes Pfadsegment auf Symlinks und führt ihn mit Argument `0` höchstens einmal je Merge-Commit aus. Ein im versionierten Baum liegender, gegenüber der Merge-Basis durch den Zielbranch geänderter oder erst hinzugefügter Hook wird mit Pfad und Grund ausgelassen. Testrepositories belegen den geänderten versionierten Hook, einen unveränderten `.git/hooks/post-merge`-Hook, einen extern konfigurierten Pfad und einen Symlink-Pfad.
- Gegen SOURCE liegt die Reihenfolge fest: bestätigter Merge und Ergebnisrecord, Nachlauf-Intent, Hook oder begründetes Auslassen, terminaler Nachlauf-Resultatrecord, Laufabschluss, im Watch-Modus Erfolgsevidenz und Queue-Move nach `outbox/done/`. `merge_completed_branch = false` führt keinen Hook aus und meldet einen vorhandenen Hook als ausgelassen; ein durch `core.hooksPath` überschriebener vorhandener Standard-Hook wird ebenfalls gemeldet. Die Git-Option `core.hooksPath=/dev/null` innerhalb von Archiv-Commit und Merge sowie deren Vorabprüfung und Rollback-Logik bleiben bestehen; Tests belegen diese Fälle.
- Gegen SOURCE erfasst das Protokoll stdout und stderr getrennt und begrenzt samt Kürzungskennzeichen. Ein Exitcode ungleich null oder eine Überschreitung von 600 Sekunden erzeugt eine sichtbare Warnung mit Status, ohne den Merge zurückzunehmen oder den Auftrag zum Poison-Fall zu machen. Fokussierte Tests decken Erfolg, Fehler, Timeout, fehlende Ausführbarkeit, Auslassen und die Ausgabegrenze ab.
- Gegen SOURCE ist ein offener Nachlauf-Intent nach Prozessabbruch als ungewisser Ausgang erkennbar. Automatisches Resume führt den Hook nicht erneut aus; Watch pausiert ohne Retry-Zähler oder Poison-Verschiebung. Die CLI-Quittierung mit unverändertem Task, Merge-Commit und Begründung schreibt einen gebundenen `acknowledged_unknown`-Resultatrecord und erlaubt anschließendes Resume bis `done/`, ohne Hook-Wiederholung. Tests decken die Crash-Fenster vor Intent, vor/nach Hook-Start und Ergebnisrecord, Quittierung und doppeltes Resume ab. Alte Run-Profile und Record-Ketten bleiben lesbar und lösen keinen neuen Nachlauf aus.

### Slice 4 - Anwenderdokumentation und Endabnahme

**Ziel**

README und Einrichtung erklären die tatsächliche Abschlusskette samt Branch-Verbleib und Operator-Gate.

**Exakter Änderungspfad**

- `README.md`
- `docs/reference/einrichtung.md`

**Akzeptanzkriterien**

- Gegen SOURCE beschreibt Abschnitt 2.10 der Einrichtung den Laufordner samt Vorgabe und konfigurierbarem Muster, den Nachlauf nach bestätigtem Merge samt Hook-Auflösung und Sicherheitsprüfung, die 600-Sekunden-Grenze, Warnungen und Protokoll bei Fehlern sowie den Operator-Weg bei ungewissem Ausgang. Er erklärt das Auslassen bei `merge_completed_branch = false` und den Verbleib des Zielbranches: Nach Merge bleibt er lokal bestehen, während der Basisbranch ausgecheckt ist; ohne Merge bleibt der Zielbranch ausgecheckt.
- Gegen SOURCE führt die `[workflow]`-Tabelle den neuen Archivschlüssel, seine Platzhalter, Kollisionsregel und Laufbindung auf. Die README beschreibt dieselben Regeln einschließlich der weiterhin unterdrückten Hooks **während** der Git-Transaktion und des fehlenden Pushs.
- Gegen SOURCE sind die fokussierten Tests der vorherigen Slices und die vom Orchestrator nach jeder betroffenen Änderung ausgeführte Standardmatrix grün. Der Operator führt `python3 -m pytest tests/test_crash_harness.py -v` vollständig auf dem endgültigen Branch-HEAD nach der letzten relevanten Änderung und vor einem Merge aus; nach jeder HEAD-Änderung ist ein neuer vollständiger Crashbeweis nötig. Kein Agent beansprucht eine Validierungsattestation.

## Risiken und Grenzen

- Externe Hooks können beliebige Seiteneffekte auslösen. Zwischen Start und Ergebnisrecord kann ein Prozessabbruch nicht beweisen, ob der Hook seine Arbeit bereits erledigte; automatisches Wiederholen wäre unsicher. Die quittierte Ungewissheit bescheinigt keinen Hook-Erfolg. Pfad- und Diff-Prüfung verhindert insbesondere, dass ein vom Zielbranch veränderter Hook automatisch auf dem Host läuft.
- Ein konfigurierbares Archivmuster kann durch falsch gesetzte Platzhalter die Zielkonvention verfehlen. Die Pflicht zum vollständigen `{run_id}`-Segment und die frühe kanonische Pfadprüfung begrenzen Kollisionen und Pfadflucht.
- Alte Läufe behalten ihre bisherige gebundene Semantik. Tests müssen ausdrücklich zeigen, dass fehlende neue Profilfelder nicht als neue Vorgaben interpretiert werden.
- `AGENTS.md`, `CLAUDE.md` und `CODEX.md` benötigen keine Änderung: Ihre Aussagen zu Archiv-Commit, lokalem Merge, Hook-Unterdrückung **während** der Transaktion und Validierung bleiben mit dem beschriebenen Nachlauf vereinbar.

## Orchestrator-Prüfprotokoll

### Verlauf

<!-- audit:history:begin -->
- Runde 1: Planung · Validierung grün · Prüfurteil abgelehnt · 2 neu, 0 geschlossen.
- Runde 2: Planrevision · Validierung grün · Prüfurteil freigegeben · 0 neu, 2 geschlossen.
<!-- audit:history:end -->

### Befunde

<!-- audit:findings:begin -->
### C-01 – Slice 3 legt fest, dass der Lauf bei ungewissem Hook-Ausgang „nach dem Merge mit einer klaren Diagnose zur…

Klasse: Befund · Stand: geschlossen

Befund:
> Slice 3 legt fest, dass der Lauf bei ungewissem Hook-Ausgang „nach dem Merge mit einer klaren Diagnose zur manuellen Klärung anhält“. Der Plan beschreibt aber keinen Weg, wie der Operator diesen Halt auflöst. Weil ein offener Intent Resume fail-closed hält, bleibt der Lauf mit gültigem Merge dauerhaft unvollständig. Im Watch-Modus kann der Auftrag dann nach erschöpften Retries nach outbox/failed/*.poison wandern statt nach outbox/done/. Das widerspricht der Vorgabe, dass ein Nachlauf-Fehlschlag den gültigen Merge nicht rückgängig machen darf. Außerdem fehlen drei Festlegungen: die Reihenfolge des Hooks gegenüber Queue-Move und Laufabschluss, eine Zeitgrenze für einen hängenden Hook (etwa den Tauri-Build-Abgleich der RuhestandsApp), der den Watch-Prozess sonst unbegrenzt blockiert, und die Behandlung von stdout/stderr für das Protokoll.

Akzeptanztest:
> Der Arbeitsplan legt in Slice 3 fest, an welcher Stelle der Hook relativ zu Merge-Ergebnisrecord, Queue-Move nach outbox/done/ und Laufabschluss läuft. Er nennt eine Zeitgrenze samt Verhalten bei Überschreitung (Warnung und Protokoll, Merge bleibt bestehen) und die Erfassung der Hook-Ausgabe. Er beschreibt einen konkreten, protokollierten Operator-Weg, der einen ungewissen Hook-Ausgang ohne erneute Ausführung abschließt. Und er stellt sicher, dass so ein Fall keinen gültig gemergten Auftrag zum Poison-Fall macht. Die Akzeptanzkriterien verlangen Tests für Zeitüberschreitung und Auflösung des ungewissen Ausgangs. Die exakten Änderungspfade decken die betroffenen Module ab.

Antwort des Implementierers, Runde 1 (angenommen):
> Slice 3 legt jetzt Reihenfolge, Zeitgrenze, Ausgabeerfassung und einen protokollierten Operator-Weg für ungewisse Hook-Ausgänge fest. Der Watch-Auftrag bleibt dabei wiederaufnehmbar und wird nicht zum Poison-Fall.

Abschlussbegründung des Prüfers:
> Slice 3 und der Entscheidungsabschnitt legen die Reihenfolge fest: bestätigter git_merge-Ergebnisrecord, Nachlauf-Intent, Hook oder begründetes Auslassen, terminaler Resultatrecord, complete_chain-Erfolg, im Watch-Modus Erfolgsevidenz und Queue-Move nach outbox/done/. Die Zeitgrenze beträgt 600 s. Bei Überschreitung wird die Prozessgruppe beendet und eine Warnung protokolliert; der Merge bleibt bestehen. stdout und stderr werden getrennt und auf je 1 MiB begrenzt mit Kürzungskennzeichen erfasst. Für den ungewissen Ausgang gibt es einen konkreten Operator-Weg: POST-MERGE-HOOK-OUTCOME-UNKNOWN und die Quittierung per --acknowledge-post-merge-unknown --hook-rationale. Das schreibt einen gebundenen acknowledged_unknown-Record ohne erneute Ausführung. Watch zählt den Halt nicht als Retry und verschiebt nichts nach poison. Die Akzeptanzkriterien verlangen Tests für Timeout, Crash-Fenster, Quittierung und doppeltes Resume. Die Pfade umfassen inbox_watcher, workflow_recovery, cli und workflow_completion.

### C-02 – Der Plan führt den „wirksamen ausführbaren post-merge-Hook“ nach dem automatischen Merge außerhalb jeder…

Klasse: Befund · Stand: geschlossen

Befund:
> Der Plan führt den „wirksamen ausführbaren post-merge-Hook“ nach dem automatischen Merge außerhalb jeder Agenten-Sandbox aus. Er legt aber nicht fest, wie dieser Hook aufgelöst wird und welcher Stand dabei gilt. Zeigt core.hooksPath auf ein versioniertes Verzeichnis, kann der gerade gemergte, von Codex geschriebene Branch den Hook-Inhalt geändert haben. Der Orchestrator würde dann agentenerzeugten Code ohne menschliche Freigabe auf dem Host ausführen. Das ist eine Überschreitung der Sicherheitsgrenze. Offen bleibt auch, ob die Auflösung vor oder nach dem Merge stattfindet und ob dafür die Repository-Konfiguration oder der Worktree-Stand maßgeblich ist.

Akzeptanztest:
> Der Arbeitsplan definiert in Slice 3 deterministisch, wie der Hook-Pfad aufgelöst wird: core.hooksPath gegenüber .git/hooks, Zeitpunkt vor oder nach dem Merge, Umgang mit Symlinks. Ein Hook, dessen Pfad im versionierten Baum liegt und durch den Diff des Zielbranches gegenüber der Merge-Basis verändert wurde, wird nicht ausgeführt. Stattdessen nennt die Endmeldung ihn mit Grund. Akzeptanzkriterien und Tests belegen diesen Fall sowie den Normalfall eines unveränderten Hooks unter .git/hooks.

Antwort des Implementierers, Runde 1 (angenommen):
> Slice 3 legt die Hook-Auflösung nach dem Merge, den Umgang mit Symlinks und die Prüfung versionierter Hooks gegen den Zielbranch fest. Geänderte Hooks werden ausgelassen und mit Grund gemeldet.

Abschlussbegründung des Prüfers:
> Die Hook-Auflösung ist jetzt deterministisch festgelegt. Sie findet nach dem Merge-Ergebnisrecord statt und nutzt die wirksame Git-Konfiguration ohne das transaktionsinterne /dev/null. core.hooksPath hat Vorrang, relative Pfade beziehen sich auf das Worktree-Verzeichnis; sonst gilt git rev-parse --git-path hooks/post-merge. Symlinks sind in allen Pfadsegmenten ausgeschlossen, verlangt wird eine reguläre ausführbare Datei. Ein Hook im versionierten Baum muss im Basis-HEAD getrackt und gegenüber der Merge-Basis unverändert sein. Ein hinzugefügter oder geänderter Hook wird mit Pfad und Grund ausgelassen und gemeldet. Die Akzeptanzkriterien verlangen Tests für geänderte versionierte Hooks, einen unveränderten .git/hooks-Hook, einen externen Pfad und einen Symlink-Pfad. Hook-Pfad und Hook-Digest sind im Intent gebunden.
<!-- audit:findings:end -->

### Abschlussprüfung

<!-- audit:approval:begin -->
Geprüft:
> Geprüft habe ich die Artefaktstruktur (vier fortlaufende Slices, kanonische Überschrift **Exakter Änderungspfad** mit Aufzählungspfaden), die Schließkriterien von C-01 und C-02 und die Auftragsabdeckung für Hook-Nachlauf, Archivunterordner, frühe Konfliktprüfung, Prüfer-Policy und Doku 2.10. Außerdem geprüft: die Sicherheitsgrenze bei der Ausführung von Hooks, die Resume- und Idempotenzsemantik (Intent/Result, Quittierung, alte Run-Profile ohne Rückwirkung), die Verträglichkeit mit AGENTS.md (Hooks während der Transaktion bleiben aus, kein Push) und die deterministische Attestation für denselben Fingerprint.

Größtes Restrisiko:
> Der Nachlauf-Seiteneffekt ist ein neuer Prozess-Seiteneffekt außerhalb der bisherigen SideEffectPayload-Klassen. Kritisch ist die Quittierung acknowledged_unknown: Sie muss wirklich nur an den offenen Intent und den unveränderten Merge-Commit gebunden sein. Sonst könnte ein zweiter Ergebnisrecord oder eine Quittierung ohne offenen Intent die fail-closed-Invariante aufweichen. Zweites Risiko: Zwischen Digest-Prüfung und Ausführung des Hooks liegt ein TOCTOU-Fenster.

Bruchbedingung:
> Der Plan scheitert in der Umsetzung, wenn Resume bei einem offenen Nachlauf-Intent den Hook doch erneut startet. Ebenso, wenn Watch den Halt POST-MERGE-HOOK-OUTCOME-UNKNOWN als technischen Retry zählt und den gemergten Auftrag nach outbox/failed/*.poison verschiebt. Oder wenn ein vom Zielbranch geänderter Hook unter einem versionierten core.hooksPath trotz Diff zur Merge-Basis ausgeführt wird.

Vorab-Risikoanalyse:
> Scheitert das Vorhaben später, dann am wahrscheinlichsten in Slice 3. Ein Grund wäre, dass die neue CLI-Quittierung oder der Watch-Pfad in Code läuft, der nicht in den exakten Änderungspfaden steht (etwa workflow_production.py). Dann wäre ein Scope-Extension-Stopp nötig. Ein anderer Grund wäre, dass die strikte Symlink-Freiheit aller Pfadsegmente legitime Hooks in Umgebungen mit symlinkten Home- oder Repo-Pfaden stets auslässt. Das bleibt sichtbar gemeldet und ist damit sicher, verfehlt aber den Nutzen. Slice 2 könnte an der Resume-Kompatibilität alter Profile ohne archive_run_directory scheitern, wenn fehlende Felder doch mit der neuen Vorgabe gedeutet werden.
<!-- audit:approval:end -->
