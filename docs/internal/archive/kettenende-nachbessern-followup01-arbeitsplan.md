# Arbeitsplan: Post-Merge-Hook gegen Nachläufer und Typwechsel absichern

TARGET_BRANCH: feature/fehler-parameter-sweep-liefert-nie-ein-ergebnis-07e5c0fab2a5f4e3

## Anlass und Repositorybefund

Der zweite Abnahmereview der Abschlusskette meldet zwei offene Sicherheitsmängel am optionalen `post-merge`-Nachlauf. `src/workflow_completion.py::_run_hook()` verwendet derzeit `subprocess.run()` mit Zeitgrenze. Dessen Timeout beendet nur den direkten Kindprozess; ein vom Hook gestarteter Nachkomme kann weiterarbeiten, obwohl der Ergebnisrecord bereits `timeout` meldet und der abgeschlossene Auftrag nach `outbox/done/` gelangt. Die vorhandene Timeout-Variante in `tests/test_workflow_completion.py` nutzt `sleep 1`, prüft aber keinen Nachkommen und keinen späteren Seiteneffekt.

`_hook_reason()` prüft einen versionierten Hook mit `git diff --diff-filter=AM` zwischen Merge-Basis und Zielbranch-HEAD. Ein Typwechsel mit Git-Status `T`, insbesondere Symlink zu regulärer ausführbarer Datei, bleibt dadurch unentdeckt. Der bestehende Test belegt nur einen hinzugefügten Hook. Der freigegebene Ursprungsplan verlangt zusätzlich, dass ein Hook im versionierten Arbeitsbaum bereits im Basis-HEAD getrackt und gegenüber der Merge-Basis im Zielbranch unverändert ist. `docs/reference/einrichtung.md` verspricht das Auslassen geänderter Hooks bereits; eine Dokumentationsänderung ist für diese Korrektur nicht erforderlich.

Die bestehende Reihenfolge aus bestätigtem Merge-Ergebnis, `post_merge_hook`-Intent, terminalem Ergebnisrecord und anschließendem Laufabschluss bleibt die Integrationskante. Der Hook-Resultattext ist bereits ein JSON-Objekt im vorhandenen Seiteneffekt-Resultat; eine zusätzliche Unsicherheitsangabe kann dort ohne Änderung des Record-Schemas stehen. Der Merge wird bei Hook-Fehler und Timeout nicht zurückgenommen. Diese PLAN_ONLY-Runde schreibt ausschließlich dieses Dokument; die Produktänderungen stehen im folgenden Umsetzungsslice.

## Umsetzung

### Slice 1 - Post-Merge-Hook kontrolliert beenden und Zielbranch-Änderungen vollständig erkennen

**Ziel**

Ein abgelaufener Hook hinterlässt keine unbeachteten Prozesse seiner Prozessgruppe; ein durch den Zielbranch veränderter versionierter Hook wird unabhängig von der Git-Änderungsart ausgelassen.

**Exakter Änderungspfad**

- `src/workflow_completion.py`
- `tests/test_workflow_completion.py`

**Umsetzungshinweise**

- `_run_hook()` startet den Hook mit Argument `0`, Repositoryroot als Arbeitsverzeichnis und eigener Session beziehungsweise Prozessgruppe. Nach Ablauf der bestehenden `HOOK_TIMEOUT_SECONDS` sendet die Bereinigung zuerst `SIGTERM` an die gesamte Gruppe, wartet nur eine kurze begrenzte Schonfrist und sendet bei weiter vorhandener Gruppe `SIGKILL`. Auch das anschließende Einsammeln des direkten Kindes und die Prüfung, ob die Gruppe noch lebt, sind zeitlich begrenzt. Das Gruppenkennzeichen stammt ausschließlich vom gestarteten Hook; die Orchestrator-Gruppe darf nie Ziel der Signale sein. Ausgaben bleiben in getrennten temporären Dateien und unter der bestehenden Längenbegrenzung.
- Ein Timeout bleibt `status: timeout` und behält den Merge. Der Ergebnisrecord erhält eine eindeutig auswertbare Angabe zur verbleibenden Unsicherheit, etwa `termination_uncertain: true`, falls Signalisierung, begrenztes Warten oder Gruppenprüfung nicht bestätigen können, dass die gestartete Gruppe beendet ist. Bei bestätigter Beendigung ist sie falsch. Der Warnpfad macht die Unsicherheit sichtbar. Ein offener Intent nach Abbruch bleibt weiterhin der vorhandene Resume-Halt; ein bloßer Timeout wird nicht automatisch erneut ausgeführt.
- `_hook_reason()` vergleicht für einen Hook im versionierten Arbeitsbaum den Git-Baumeintrag des Hook-Pfads in Merge-Basis und Zielbranch-HEAD einschließlich Typ/Modus und Objekt-ID, statt Änderungsarten per `--diff-filter=AM` auszuwählen. Eine fehlende oder abweichende Zieleintragung liefert `changed_by_target_branch`; auch fehlendes Tracking im Basis-HEAD verhindert die Ausführung eines solchen Hooks. Die vorhandenen Prüfungen auf reguläre Datei, Ausführbarkeit und Symlinks sowie die Behandlung eines externen Hooks bleiben wirksam.

**Akzeptanzkriterien**

- Gegen SOURCE startet `_run_hook()` den Hook in einer eigenen Session oder Prozessgruppe. Nach Timeout signalisiert der Orchestrator die gesamte gestartete Gruppe mit `SIGTERM`, danach nötigenfalls mit `SIGKILL`, wartet jeweils begrenzt und führt weder ein unbegrenztes `wait()` noch ein unbegrenztes Lesen aus. Ein fokussierter Test startet einen langlebigen Hook-Nachkommen, der nach seiner ursprünglichen Laufzeit einen Marker schreiben würde. Bei kleiner Timeout-Grenze weist er `status: timeout`, einen erhaltenen Merge und das Ausbleiben des Markers nach Ablauf jener Laufzeit nach.
- Gegen SOURCE enthält der terminale Timeout-Resultattext eine eindeutige Unsicherheitsangabe für nicht bestätigte Gruppenbeendigung. Fokussierte Tests belegen sowohl den bestätigten Bereinigungsfall als auch einen simulierten Fall, in dem Signalisierung oder Prüfung keinen sicheren Endzustand belegt; die Warnung verschweigt diesen Fall nicht. Der Hook läuft beim Resume nicht erneut und ein normaler Timeout macht den Merge nicht rückgängig.
- Gegen SOURCE behandelt `_hook_reason()` Hinzufügen sowie Inhalts-, Modus- und Typänderungen des versionierten Hook-Pfads zwischen Merge-Basis und Zielbranch-HEAD als `changed_by_target_branch` und verlangt Tracking im Basis-HEAD. Ein fokussierter Repositorytest versioniert im Basisbranch `.githooks/post-merge` als Symlink auf ein ausführbares Skript, setzt `core.hooksPath=.githooks` und ersetzt den Symlink im Zielbranch durch eine reguläre ausführbare Marker-Datei. Nach Completion existiert der Marker nicht, das Hook-Ergebnis ist `skipped` mit Grund `changed_by_target_branch`, und der Merge bleibt erhalten.
- Gegen SOURCE prüfen weitere kompakte Fälle die fehlende Basis-HEAD-Eintragung sowie mindestens eine reine Modusänderung; ein unveränderter zulässiger Hook und ein externer konfigurierter Hook bleiben ausführbar. Die vorhandenen Tests für Hook-Ausgabe, Fehler, Symlink-Pfade, Merge ohne Nachlauf und Resume bleiben grün. Der Orchestrator führt die Standardmatrix `python3 -m pytest tests/ -v -m "not crash_harness"` aus; der Operator führt den vollständigen Crashbeweis auf dem endgültigen Branch-HEAD vor dem Schlussreview aus. Agenten erzeugen keine Validierungsattestation.

## Risiken und Grenzen

Ein Hook kann absichtlich eine neue Session starten und damit seine Nachkommen aus der gestarteten Gruppe lösen. Der Orchestrator kann für diese Prozesse kein sicheres Ende behaupten; die Zusage und die Tests beziehen sich auf die vom Hook gestartete Prozessgruppe. Ein Absturz des Orchestrators während der Bereinigung fällt weiterhin unter den offenen Nachlauf-Intent und die vorhandene Quittierungsregel. Der Vergleich von Git-Baumeinträgen muss die tatsächlichen Commit-Objekte aus der Merge-Operation verwenden, damit ein später geänderter Branchname die Entscheidung nicht verschiebt.

## Orchestrator-Prüfprotokoll

### Verlauf

<!-- audit:history:begin -->
- Runde 1: Planung · Validierung grün · Prüfurteil freigegeben · 0 neu, 0 geschlossen.
<!-- audit:history:end -->

### Befunde

<!-- audit:findings:begin -->
Keine.
<!-- audit:findings:end -->

### Abschlussprüfung

<!-- audit:approval:begin -->
Geprüft:
> Geprüft: Existenz und Inhalt des Pflichtartefakts docs/internal/kettenende-nachbessern-followup01-arbeitsplan.md, das allein im autorisierten Pfad liegt. Geprüft wurde außerdem die PLAN_ONLY-Struktur: genau ein zusammenhängendes '### Slice 1 - …' mit kanonischem '**Exakter Änderungspfad**' und reinen Pfad-Bullets für src/workflow_completion.py und tests/test_workflow_completion.py. Die Attestation internal:work-plan-contract hat denselben Fingerprint und steht auf PASS. Befund 1 ist vollständig abgebildet: eigene Session bzw. Prozessgruppe, SIGTERM und danach SIGKILL auf die Gruppe, begrenztes Warten und Reapen, keine Signale an die Orchestrator-Gruppe, termination_uncertain im bestehenden JSON-Resultat ohne Schemaänderung. Der Merge bleibt erhalten, kein erneuter Lauf beim Resume. Ein Test mit Nachkommen und Marker ist vorgesehen. Befund 2 ist vollständig abgebildet: Vergleich der ls-tree-Einträge (Modus/Typ plus OID) zwischen Merge-Basis und Ziel-HEAD statt --diff-filter=AM. Hinzufügen, Inhalt, Modus und Typwechsel werden erfasst, Tracking im Basis-HEAD ist Pflicht. Der Symlink→Datei-Test entspricht dem Abnahmekriterium. Positiv- und Negativfälle sowie Resume, Sicherheitsgrenzen und Idempotenz sind adressiert.

Größtes Restrisiko:
> Der Plan begrenzt die Gruppenbereinigung auf den Timeout-Pfad. Ein Hook, der regulär endet und einen Hintergrund-Nachkommen hinterlässt, sowie ein Hook, der per setsid seine eigene Session verlässt, bleiben unkontrolliert. Letzteres ist als Grenze dokumentiert. Die Lebendprüfung der Gruppe (killpg mit Signal 0) kann zudem durch PGID-Wiederverwendung oder Zombie-Zustände fehlinterpretiert werden. Zeitabhängige Tests können unter Last flaky werden.

Bruchbedingung:
> Das Ergebnis wäre falsch, wenn die Umsetzung erst nach dem Reapen des direkten Kindes und ohne gehaltene PGID signalisiert und dadurch eine fremde, wiederverwendete Gruppe trifft. Falsch wäre es auch, wenn der Test nur kürzer wartet als die ursprüngliche Laufzeit des Nachkommen und das Ausbleiben des Markers deshalb nicht beweist.

Vorab-Risikoanalyse:
> Scheitert die Umsetzung, dann am wahrscheinlichsten an drei Stellen. Erstens: Popen mit start_new_session wird nicht konsequent mit os.killpg auf die gespeicherte PGID kombiniert. Oder die Schonfrist bzw. das Warten nach SIGKILL ist unbegrenzt, sodass der Orchestrator bei nicht beendbaren Prozessen (D-State) hängt. Zweitens: _hook_reason nutzt symbolische Branchnamen statt der aufgezeichneten Commit-OIDs, sodass die Entscheidung driftet. Drittens: Ein fehlender Eintrag in der Merge-Basis wird nicht als Änderung gewertet. Die Akzeptanzkriterien des Plans verlangen genau diese Punkte ausdrücklich, deshalb sollte ein Review der Umsetzung sie direkt prüfen.
<!-- audit:approval:end -->
