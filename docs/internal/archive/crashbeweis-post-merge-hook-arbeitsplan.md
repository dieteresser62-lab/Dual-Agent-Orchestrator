# Arbeitsplan: Crashbeweis für den post-merge-Hook

## Ausgangslage und Vertrag

Seit `e99e65b` enthält `SIDE_EFFECT_CLASSES` die Klasse `post_merge_hook`, während `LEDGER_ORDER` und `tests/fixtures/crash_harness/manifest-v1.json` sie nicht führen. `CrashHarnessManifest.load` verwirft deshalb das Manifest vor dem ersten Crashfall. Die bisherige Matrix prüft Seiteneffekte über `SideEffectExecutor`, den produktiven Nachlauf aber nicht über `workflow_completion.complete_chain`, `orchestrator.run_production_workflow` und die Quittierung. Maßgeblich für dessen Verhalten bleibt Abschnitt 2.10 von `docs/reference/einrichtung.md`.

Die bestehende Manifeststruktur mit `schema_version`, `scenario_version`, Klassen-, Grenz- und Laufgrenzeninventar bleibt unverändert; `HARNESS_SCHEMA_VERSION = provider-free-crash-harness-v1` bleibt daher gültig. Die erweiterte Topologie bekommt eine neue Datei `manifest-v2.json` und die Szenariokennung `post-merge-hook-boundaries-v2`. `manifest-v1.json` bleibt als historischer Stand unangetastet; der aktuelle Harness und seine Tests verwenden ausdrücklich v2. Weil das ausgegebene Ergebnis zusätzliche Hook- und Quittierungsevidenz enthält, wird `RESULT_SCHEMA_VERSION` auf `provider-free-crash-harness-result-v2` erhöht. Keine dieser Harness-Versionen ändert das Wire-Format von Run-Profilen oder Record-Ketten; deren bestehende Reader und Defaults bleiben erhalten.

## Umsetzung

### Slice 1 - Vollständige Hook-Grenzmatrix und produktives Resume

**Ziel**

Der eigenständige Crashbeweis durchläuft alle deklarierten `post_merge_hook`-Grenzen für beide Abschlussarten in echten, isolierten Git-Testrepositories und belegt das sichere Resume bis zum Auftragsabschluss.

**Exakter Änderungspfad**

- `scripts/crash_harness.py`
- `src/orchestrator.py`
- `src/workflow_completion.py`
- `src/workflow_production.py`
- `tests/fixtures/crash_harness/manifest-v2.json`
- `tests/test_crash_harness.py`
- `tests/test_workflow_completion.py`

**Umsetzungsschritte**

1. `LEDGER_ORDER` um `post_merge_hook` ergänzen. Das neue Manifest enthält diese Klasse in `effect_classes`, ihre sechs externen Ledger-Phasen (`before_intent`, `after_intent`, `before_effect`, `after_effect`, `before_result`, `after_result`) in `boundary_matrix` und getrennte `runtime_boundaries` für Abschluss mit Merge und ohne Merge. Die zwei zusätzlichen benannten Prozessschnitte nach dem tatsächlichen Hook-Start und nach dessen Ende werden als eigene Hook-Laufgrenzen in `runtime_boundaries` deklariert und geprüft; sie werden nicht als Ledger-Phasen `before_effect` oder `after_effect` ausgegeben. Der Loader vergleicht weiter exakt mit `SIDE_EFFECT_CLASSES`, `LEDGER_ORDER` und der erweiterten Produktions-Topologie; ein fehlender Hook-Eintrag oder Prozessschnitt muss bereits beim Laden scheitern. Alle festen Inventar- und Ergebniszahlen werden aus der neuen Deklaration abgeleitet oder bewusst auf die neue Topologie angepasst.
2. Für beide Abschlussarten ein kleines, deterministisches Git-Repository samt echtem `ArtifactStore`, Run-Profil, ausführbarem Hook mit Aufrufzähler und unverändertem Auftragsdokument aufbauen. Der Fall mit `merge_completed_branch = true` bindet den Hook-Intent an den bestätigten Merge-Commit; bei `false` hängt er am Archiv-Commit und der Hook wird als `merge_disabled` ausgelassen. Ledger-Abstürze werden über den produktiven `side_effect_boundary_observer` am Hook-Seiteneffekt ausgelöst. Die zwei zusätzlichen Prozessschnitte werden ausschließlich testseitig in `scripts/crash_harness.py` eingerichtet, ohne Beobachtungsnaht in Produktionsdateien: In einem isolierten Worker wird `workflow_completion.subprocess.Popen` nur für den exakten Hook-Aufruf durch einen delegierenden Wrapper ersetzt. Dieser startet den echten Prozess, wartet auf dessen vom Hook geschriebenes Startsignal und löst vor dem produktiven `wait` den harten Worker-Absturz aus; ein Freigabesignal hält den Hook bis dahin am Leben, danach wartet der Testcontroller auf dessen Ende. Für den Endschnitt ersetzt der Worker nur `workflow_completion._run_hook` durch einen Wrapper, der zuerst die echte Funktion samt `wait` vollständig ausführt und unmittelbar vor der Rückgabe an `perform` hart abstürzt. Die Wrapper delegieren alle anderen Aufrufe unverändert und existieren nur im Harness-Worker; Hook-Aufrufzähler und Start-/Endsignale belegen die erreichte physische Grenze. Gestartete Kindprozesse werden kontrolliert freigegeben und abgewartet. Die bisherige Provider-Prozesssperre wird so eingegrenzt, dass echte lokale Git- und Hook-Prozesse möglich bleiben, echte Providerstarts aber weiterhin ausnahmslos verboten und gezählt werden. Keine Handkonstruktion von Ergebnisrecords und kein gefälschter Replay-Ersatz.
3. Nach jedem Absturz den Prozesszustand aus den Dateien und Records neu aufbauen und den produktiven Resume-Einstieg ausführen. Für `merge_completed_branch = true` pro Grenzfall den physischen Hook-Aufrufzähler und das terminale Resultat prüfen: Bei `before_intent` holt das gewöhnliche Resume den Hook genau einmal nach. Bei `after_intent` und `before_effect` bleibt der Zähler null; das gewöhnliche Resume hält am offenen Intent an und die anschließende Quittierung erzeugt genau einen `acknowledged_unknown`-Resultatrecord. Nach dem tatsächlichen Hook-Start, nach dessen Ende sowie bei `after_effect` und `before_result` bleibt der Zähler bei genau eins; das gewöhnliche Resume hält ohne zweiten Aufruf am offenen Intent an, danach erzeugt die Quittierung genau einen `acknowledged_unknown`-Resultatrecord. Bei `after_result` bleibt der Zähler bei genau eins und das gewöhnliche Resume konvergiert ohne Quittierung mit dem bekannten Resultat. Jede nötige Quittierung verwendet `--acknowledge-post-merge` mit richtigem Commit, unverändertem Task und Begründung. Ein Abschluss ohne physischen Hook-Aufruf und ohne expliziten Quittierungs- oder Auslassungsrecord ist ein Fehlschlag. Bei `merge_completed_branch = false` bleibt der Zähler in jeder Ledger-Phase null; der bekannte Auslassungsgrund muss nach Resume als `merge_disabled`-Resultat protokolliert sein. Falls ein offener Intent für diesen nachweislich rein logischen Auslassungsschritt derzeit fälschlich als physisch ungewiss behandelt wird, ist das ein gegenüber Abschnitt 2.10 nachgewiesener produktiver Resume-Fehler und wird nach Schritt 4 behoben. Zusätzliche Prozessschnitte werden nur bei aktiviertem Merge ausgeführt. Auch ein bereits geschriebenes bekanntes Resultat wird erneut resumiert. In allen Fällen bleiben Merge- beziehungsweise Archiv-Commit und korrekter Branch-HEAD erhalten; bei Watch-Abschluss erfolgt der Queue-Move erst nach terminalem Hook-Resultat.
4. Die produktiven Pfade `src/workflow_completion.py`, `src/workflow_production.py` und `src/orchestrator.py` nur ändern, wenn ein ausgeführter Grenzfall dort einen echten Fehler gegenüber Abschnitt 2.10 nachweist; die testseitigen Prozessschnitte aus Schritt 2 sind ausdrücklich kein Grund für eine Produktionsänderung. Den Fehler im Slice-Bericht mit auslösender Phase und korrigierter Resume- oder Abschlussbedingung benennen und durch einen fokussierten Regressionstest in `tests/test_workflow_completion.py` absichern. Vorhandene Record-Ketten und Profile müssen mit ihrem bisherigen Wire-Format lesbar bleiben.
5. Das Harness-Ergebnis um deterministische, pro Modus und Phase prüfbare Hook-Evidenz erweitern: beobachtete Abstürze, physische Hook-Aufrufe, offener beziehungsweise abgeschlossener Intent, Ablehnung eines gewöhnlichen Resume bei ungewissem Ausgang, Quittierungsrecord, Abschluss, Commit-Bindung und Queue-Zustand. Reale Record-Ketten zuerst unverändert validieren; arbeitsverzeichnisabhängige absolute Hook-Pfade und daraus abgeleitete Record-IDs nur für die exportierte Vergleichsevidenz nachvollziehbar kanonisieren. Stabile Sortierung, feste Zeitbasis, kanonische Serialisierung und Selbstbindung über `artifact_sha256` erhalten; zwei Läufe auf demselben Quellstand liefern bytegleiche Ergebnisse. Die gemessenen Quellen schließen das neue Manifest und alle wirksamen Python-Quellen ein.

**Akzeptanzkriterien**

- Gegen SOURCE ist `post_merge_hook` in `LEDGER_ORDER`, im aktuellen Manifest, in dessen Grenzmatrix und als produktive Laufgrenze für `merge_completed_branch = true` und `false` enthalten. Entfernt man die Klasse oder eine deklarierte Hook-Grenze aus dem Manifest, scheitert der Crashbeweis geschlossen.
- Gegen SOURCE erreichen die Testfälle mindestens die Grenzen nach Hook-Intent vor dem Start, nach tatsächlichem Prozessstart ohne Resultat, nach tatsächlichem Prozessende vor Resultat und nach Resultat vor Auftragsende; zusätzlich decken sie die übrigen deklarierten Phasen ab. Jede Grenze wird aus echter Record-Kette über den produktiven Resume-Pfad fortgesetzt.
- Gegen SOURCE weist der physische Aufrufzähler für jede Grenze bei aktiviertem Merge den in Schritt 3 genannten exakten Sollwert nach: eins bei `before_intent` nach Resume und bei allen Grenzen nach physischem Start, null bei `after_intent` und `before_effect` mit anschließendem `acknowledged_unknown`-Record. Ein bekannter Hook-Ausgang wird nie ein zweites Mal ausgeführt; ein Abschluss ohne Hook-Aufruf und ohne Quittierungs- oder Auslassungsrecord scheitert.
- Gegen SOURCE stoppt jeder ungewisse physische Hook-Ausgang bei gewöhnlichem Resume sichtbar, schreibt vor Quittierung keinen terminalen Erfolg und läuft nach korrekter `--acknowledge-post-merge`-Quittierung ohne Hook-Wiederholung bis zum Abschluss. Falscher Commit oder geänderter Task erzeugt keinen Quittierungsrecord.
- Gegen SOURCE bleibt bei aktiviertem Merge der bestätigte Merge-Commit auf dem Basisbranch erhalten. Bei deaktiviertem Merge bleibt der Archiv-Commit auf dem Zielbranch erhalten, der Aufrufzähler ist bei jeder Grenze null und ein `merge_disabled`-Resultatrecord belegt die Auslassung. Der Watch-Queue-Move nach `outbox/done/` erfolgt nur nach terminalem Hook-Resultat.
- Gegen SOURCE enthält das Ergebnis prüfbare Hook-Evidenz für beide Abschlussarten, ist bei identischem Quellstand bytegleich und sein `artifact_sha256` bindet exakt die kanonischen Ergebnisbytes ohne das Digestfeld. Alte Run-Profile und Record-Ketten bleiben lesbar.
- Gegen SOURCE ist `python3 -m pytest tests/test_crash_harness.py -v` nach diesem Slice vollständig grün. Der Implementierer führt diesen gezielten Lauf in jeder Korrekturrunde aus und berichtet das Ergebnis; die unveränderte Standardmatrix `python3 -m pytest tests/ -v -m "not crash_harness"` läuft als orchestratorische Validierung. Auf dem endgültigen Branch-HEAD folgt der vollständige Betreiber-Crashbeweis vor dem Finalreview gemäß Abschnitt 2.10.

## Risiken und Prüfpunkte

- Ein bloß generischer `SideEffectExecutor`-Fall würde die Commit-Bindung und den produktiven Quittierungspfad nicht nachweisen; die Harness-Fälle müssen `complete_chain` und den produktiven Resume-Einstieg tatsächlich erreichen.
- Zwischen Hook-Start und Resultat ist der physische Ausgang prinzipiell ungewiss. Der Test darf diesen Zustand nicht als Erfolg umdeuten und muss den fehlenden zweiten Aufruf am Aufrufzähler belegen.
- Testfallnamen, temporäre Pfade, Git-Metadaten und Zeitstempel dürfen das Ergebnis nicht vom zufälligen Arbeitsverzeichnis oder der Ausführungsreihenfolge abhängig machen.

## Orchestrator-Prüfprotokoll

### Verlauf

<!-- audit:history:begin -->
- Runde 1: Planung · Validierung grün · Prüfurteil abgelehnt · 2 neu, 0 geschlossen.
- Runde 2: Planrevision · Validierung grün · Prüfurteil freigegeben · 0 neu, 2 geschlossen.
<!-- audit:history:end -->

### Befunde

<!-- audit:findings:begin -->
### C-01 – Widerspruch im Plan zwischen Schritt 2 und Schritt 4: Schritt 2 verlangt zwei zusätzliche Prozessschnitte…

Klasse: Befund · Stand: geschlossen

Befund:
> Widerspruch im Plan zwischen Schritt 2 und Schritt 4: Schritt 2 verlangt zwei zusätzliche Prozessschnitte ("Startschnitt beobachtet einen wirklich gestarteten Hook vor `wait`" und Endschnitt vor dem Resultatrecord) über eine "eng begrenzte Hook-Prozess-Instrumentierung". Wo diese Instrumentierung liegt, legt der Plan nicht fest. Schritt 4 erlaubt Änderungen an `src/workflow_completion.py`, `src/workflow_production.py` und `src/orchestrator.py` nur zur Behebung eines nachgewiesenen Fehlers gegenüber Abschnitt 2.10. Braucht der Schnitt eine Beobachtungsnaht im produktiven Hook-Aufruf, zum Beispiel weil dort `subprocess.run` statt `Popen`/`wait` verwendet wird, muss der Implementierer entweder Schritt 4 verletzen oder die geforderte Grenze auslassen. Die Grenze "nach dem Start des Hooks, ohne Ergebnis-Record" ist ausdrücklich verlangt. Der Plan ist in diesem Punkt nicht eindeutig ausführbar und führt absehbar zu einem Scope-Konflikt in der Slice-Prüfung.

Akzeptanztest:
> Der Arbeitsplan legt eindeutig fest, wie die Prozessschnitte nach dem Hook-Start und nach dem Hook-Ende ausgelöst werden. Entweder geschieht das rein testseitig in `scripts/crash_harness.py` oder `tests/test_crash_harness.py`, etwa durch ein benanntes, auf den Hook-Aufruf begrenztes Patchen des Prozessstarts im Harness oder durch einen Hook, der den Schnitt selbst signalisiert. Oder der Plan erlaubt ausdrücklich eine minimale, produktiv wirkungslose Beobachtungsnaht in einer benannten Produktionsdatei als zulässige Ausnahme zu Schritt 4 und nennt deren Grenzen. Schritt 2 und Schritt 4 widersprechen sich danach nicht mehr.

Antwort des Implementierers, Runde 1 (angenommen):
> Der Plan legt beide Prozessschnitte als ausschließlich testseitige Instrumentierung in scripts/crash_harness.py fest: ein auf den echten Hook-Aufruf begrenzter Popen-Wrapper für den Startschnitt und ein Wrapper um _run_hook für den Endschnitt. Produktionsänderungen sind dafür nicht vorgesehen.

Abschlussbegründung des Prüfers:
> Schritt 2 legt beide Prozessschnitte jetzt ausschließlich testseitig in `scripts/crash_harness.py` fest. Für den Startschnitt ersetzt ein auf den Hook-Aufruf begrenzter, delegierender Wrapper `workflow_completion.subprocess.Popen` im isolierten Worker; Start- und Freigabesignal kommen vom Hook. Für den Endschnitt umhüllt ein Wrapper `_run_hook` und stürzt nach dem echten `wait` vor der Rückgabe ab. Schritt 4 schließt ausdrücklich aus, dass die testseitigen Schnitte eine Produktionsänderung begründen. Damit widersprechen sich Schritt 2 und Schritt 4 nicht mehr.

### C-02 – Schritt 3 verlangt für den Absturz vor dem Intent nur, dass der Hook "höchstens einmal nachgeholt" wird

Klasse: Befund · Stand: geschlossen

Befund:
> Schritt 3 verlangt für den Absturz vor dem Intent nur, dass der Hook "höchstens einmal nachgeholt" wird. Auch die Akzeptanzkriterien fordern nur, dass ein bekannter Ausgang nie zweimal läuft. Keine Stelle verlangt, dass der Hook bei `merge_completed_branch = true` auf jedem konvergierten Pfad tatsächlich genau einmal ausgeführt wurde. Ein Resume, das nach `before_intent` den Hook nie startet und den Auftrag trotzdem abschließt, würde alle Kriterien erfüllen. Das schwächt den verlangten Nachweis nach Abschnitt 2.10 und die Messung "kein Hook läuft zweimal, ein ungewisser Ausgang endet nie still als Erfolg".

Akzeptanztest:
> Der Arbeitsplan verlangt für `merge_completed_branch = true` pro Grenzfall einen am Aufrufzähler belegten Sollwert. Bei Grenzen vor einem physischen Start (`before_intent`, `after_intent`, `before_effect`) wird der Hook insgesamt genau einmal ausgeführt, wenn das Resume ihn nachholt; hält das Resume wegen eines offenen Intents an, bleibt es bei null Aufrufen samt `acknowledged_unknown`-Record, wie Abschnitt 2.10 es vorsieht. Bei Grenzen nach dem physischen Start bleibt es bei genau einem Aufruf. Ein Abschluss ohne Hook-Aufruf und ohne Quittierungs- oder Auslassungsrecord gilt ausdrücklich als Fehlschlag. Für `false` bleibt die Aufrufzahl null, und es gibt einen `merge_disabled`-Record.

Antwort des Implementierers, Runde 1 (angenommen):
> Der Plan nennt für jede Grenze bei aktiviertem Merge den exakten physischen Hook-Aufrufzähler und das erforderliche Resultatrecord. Bei deaktiviertem Merge verlangt er null Aufrufe und ein merge_disabled-Resultat. Ein Abschluss ohne Aufruf und ohne explizites Quittierungs- oder Auslassungsrecord gilt als Fehlschlag.

Abschlussbegründung des Prüfers:
> Schritt 3 und die Akzeptanzkriterien nennen für `merge_completed_branch = true` jetzt pro Grenze einen exakten Sollwert. `before_intent`: Resume holt den Hook genau einmal nach. `after_intent` und `before_effect`: null Aufrufe, Anhalten und genau ein `acknowledged_unknown`-Record. Start, Ende, `after_effect` und `before_result`: genau ein Aufruf, dann Quittierung. `after_result`: genau ein Aufruf, Konvergenz ohne Quittierung. Ein Abschluss ohne Aufruf und ohne Quittierungs- oder Auslassungsrecord gilt ausdrücklich als Fehlschlag. Für `false` gelten null Aufrufe und ein `merge_disabled`-Record. Das erfüllt das Abnahmekriterium.
<!-- audit:findings:end -->

### Abschlussprüfung

<!-- audit:approval:begin -->
Geprüft:
> Geprüft habe ich: Auflösung von C-01 und C-02 im Diff; PLAN_ONLY-Struktur (genau ein `### Slice 1 - ...` mit `**Exakter Änderungspfad**` und reiner Pfadliste); gebundene Attestierung `internal:work-plan-contract` PASS auf Fingerprint [Hash ausgelassen]…; Geltungsbereich (nur die Plandatei geändert); Versionsvertrag (manifest-v2, Szenario- und Ergebnisversion, v1 unangetastet); Resume- und Idempotenzsollwerte gegen Abschnitt 2.10; Providersperre; Determinismus und Selbstbindung des Ergebnisses.

Größtes Restrisiko:
> Der Plan nimmt an, dass der produktive Hook-Aufruf in `workflow_completion` über `subprocess.Popen` mit anschließendem `wait` in `_run_hook` läuft. Verwendet die Produktion `subprocess.run` oder `communicate`, muss der Implementierer den Wrapper-Ansatz testseitig anpassen. Das bleibt im erlaubten Pfad `scripts/crash_harness.py` möglich.

Bruchbedingung:
> Der Plan scheitert, wenn der Hook-Aufruf in `workflow_completion` nicht über einen patchbaren Modulbezeichner wie `subprocess.Popen` oder `_run_hook` erreichbar ist. Dann ließe sich der Start- oder Endschnitt nur mit einer Produktionsnaht erzwingen, und Schritt 4 schließt eine solche Naht aus.

Vorab-Risikoanalyse:
> Scheitert die Umsetzung, dann am wahrscheinlichsten, weil der Popen-Wrapper im Worker den Hook-Aufruf nicht eindeutig von Git-Aufrufen trennt. Oder der harte Worker-Absturz hinterlässt verwaiste Hook-Kindprozesse, die das bytegleiche Ergebnis oder den Aufrufzähler verfälschen. Ebenso möglich: Der `merge_disabled`-Pfad mit offenem Intent wird im Resume fälschlich als ungewiss behandelt und muss nach Schritt 4 produktiv korrigiert werden.
<!-- audit:approval:end -->
