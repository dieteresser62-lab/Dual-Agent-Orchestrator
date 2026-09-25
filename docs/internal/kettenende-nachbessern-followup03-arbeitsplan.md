# Arbeitsplan: Post-Merge-Hook bei Messfehlern und widersprüchlichen Effekten sicher abschließen

TARGET_BRANCH: feature/fehler-parameter-sweep-liefert-nie-ein-ergebnis-07e5c0fab2a5f4e3

## Anlass und Repositorybefund

Der vierte Abnahmereview meldet drei Lücken in `src/workflow_completion.py`. `_post_merge_effect()` misst für einen neuen Hook-Effekt den Inhaltsdigest vor `SideEffectExecutor.execute()`. `_hook_content_digest()` übersetzt Lese- und Inspektionsfehler wie `PermissionError` aus `lstat()`, `os.open()` oder `os.read()` in `GitTransactionError`. Dadurch entsteht nach einem bestätigten Merge beziehungsweise Archiv-Commit kein `post_merge_hook`-Intent und der Abschluss scheitert bei jedem Resume erneut. Auch bei deaktiviertem Merge wird der Digest derzeit vor dem protokollierten Auslassungsergebnis gemessen.

`_hook_reason()` vergleicht für Hook-Pfade im Arbeitsbaum die Baumeinträge aus Basis-HEAD, Ziel-HEAD und Merge-Basis. Sind alle drei Einträge leer, wird eine vorhandene ignorierte und unversionierte Datei nicht ausgeschlossen. Ein solcher Hook kann nach dem lokalen Merge vom Host ausgeführt werden, obwohl die freigegebenen Pläne für diesen Pfad Tracking im Basis-HEAD verlangen. Für `.git/hooks` und externe Hook-Pfade gilt diese Trackingpflicht nicht.

Beim Resume nimmt `_post_merge_effect()` mit `next(reversed(...))` den letzten passenden Effekt für Work-Unit-ID und Merge-Commit. Zwei passende Intents bleiben dadurch unbemerkt, auch wenn einer offen und der andere abgeschlossen ist. `acknowledge_unknown_post_merge()` verlangt bereits genau einen passenden offenen Effekt. Die Korrektur betrifft denselben Nachlauf und bleibt deshalb ein zusammenhängender Umsetzungsslice. Diese PLAN_ONLY-Runde schreibt ausschließlich dieses Dokument.

## Umsetzung

### Slice 1 - Hook-Messfehler protokollieren, Basistracking erzwingen und doppelte Intents stoppen

**Ziel**

Der Nachlauf erzeugt bei nicht lesbarem Hook ein terminales Auslassungsergebnis, führt vorhandene unversionierte Worktree-Hooks nicht aus und stoppt bei widersprüchlichen passenden Hook-Effekten vor jeder weiteren Hook-Arbeit.

**Exakter Änderungspfad**

- `src/workflow_completion.py`
- `tests/test_workflow_completion.py`

**Umsetzungshinweise**

- `_post_merge_effect()` sammelt zuerst aus der validierten Replay-Sicht **alle** `post_merge_hook`-Effekte mit derselben Work-Unit-ID und demselben Merge-Commit. Bei mehr als einem Treffer wirft es einen diagnostischen `GitTransactionError`, bevor `_hook_path()`, `_hook_content_digest()`, `make_spec()` oder `executor.execute()` aufgerufen werden. Bei genau einem Treffer verwendet es dessen gespeicherte Drei- oder Vierfeld-Operation und denselben Seiteneffekt-Schlüssel; ein abgeschlossenes Paar wird nur wiedererkannt und ein offener Intent bleibt nach der vorhandenen Unknown-Reconciliation gesperrt. Nur ohne Treffer wird ein neuer Hook-Pfad aufgelöst und ein neuer Vierfeld-Intent vorbereitet.
- Für einen neuen Effekt wird ein `OSError` bei der Digest-Messung, einschließlich der derzeit von `_hook_content_digest()` erzeugten `GitTransactionError`, als eindeutig erkennbarer Messfehler behandelt. Die Operation erhält den bereits im Modell zugelassenen Digest-Platzhalter `unavailable`; die Fehlerinformation bleibt bis zur Ausführung im aktuellen Aufruf erhalten, ohne die Operation oder das Record-Schema um ein fünftes Feld zu erweitern. `executor.execute()` schreibt zunächst den Vierfeld-Intent und dann ein terminales JSON-Ergebnis mit `status: skipped` und dem stabilen, auswertbaren Grund `digest_unreadable`. Die Warnung nennt diesen Grund. Die Regel gilt bei aktiviertem und deaktiviertem Merge; ein deaktivierter Merge startet weiterhin keinen Hook. Ein schon vorhandener Effekt wird ohne neue Messung wiederaufgenommen.
- Der Platzhalter `unavailable` ist niemals ein positiver Inhaltsnachweis. Auch wenn eine zweite Messung erneut `unavailable` ergäbe, darf der Hook nicht starten. Ein Messfehler bei der Prüfung innerhalb von `perform()` führt ebenfalls zum terminalen `skipped`-Ergebnis `digest_unreadable` für denselben Intent. Ein fehlender Hook behält den bisherigen Grund `missing`; ein lesbarer Hook bei deaktiviertem Merge behält `merge_disabled`. Die Auslassung wegen eines Messfehlers hat Vorrang vor diesen Gründen, damit der Befund auch ohne Merge aus dem Ergebnis erkennbar ist. Die bestehende Unknown-Regel für einen bereits offenen Intent bleibt bestehen: kein automatischer zweiter Versuch nach einem möglichen Prozessstart.
- `_hook_reason()` unterscheidet einen tatsächlich fehlenden Hook von einer vorhandenen, aber im Basis-HEAD nicht getrackten Worktree-Datei. Für den vorhandenen Pfad innerhalb des Repository-Worktrees und außerhalb von `.git` verhindert ein leerer `ls-tree`-Eintrag im Basis-HEAD stets die Ausführung. Ist der Pfad im Ziel-HEAD neu hinzugekommen, bleibt der bestehende Grund `changed_by_target_branch`; fehlt er auch im Ziel-HEAD, gilt der stabile Grund `not_tracked_in_base`, selbst wenn die Merge-Basis ebenfalls keinen Eintrag hat. Der bestehende Vergleich von Modus, Typ und Objekt-ID für getrackte Hooks bleibt wirksam. Standard-Hooks unter `.git/hooks`, externe Hook-Pfade und unveränderte, bereits im Basis-HEAD getrackte Worktree-Hooks bleiben zulässig. Fehlende Hooks in einem getrackten Hook-Verzeichnis bleiben still mit `missing` ausgelassen.
- Die Tests verwenden die vorhandenen Repository- und Completion-Helfer. Für Digest-Fehler werden `lstat()` und `os.open()` gezielt per Monkeypatch mit `PermissionError` simuliert, ohne auf Dateirechte des Testprozesses zu vertrauen. Für den Trackingfall wird `.githooks/post-merge` per Git-Ignore verborgen und als ausführbare Marker-Datei angelegt. Für den Mehrfachfall werden zwei passende Replay-Effekte mit derselben Work-Unit-ID und demselben Commit erzeugt, darunter nach Möglichkeit ein offenes Dreifeld- und ein abgeschlossenes Vierfeld-Paar. Die Tests prüfen den Stopp vor Hook-Auflösung sowie unveränderte Ledger- beziehungsweise Record-Anzahl und Hook-Zähler.

**Akzeptanzkriterien**

- Gegen SOURCE führt ein `PermissionError` bei `lstat()` oder `os.open()` während der anfänglichen Digest-Messung weder bei `merge_completed_branch = true` noch bei `false` zu einer Ausnahme vor dem Hook-Intent. Parametrisierte fokussierte Tests prüfen einen neu geschriebenen Vierfeld-Intent mit exakt `unavailable`, ein terminales Ergebnis `status: skipped` und `reason: digest_unreadable`, eine sichtbare Warnung, einen erfolgreichen Abschluss sowie den erhaltenen Merge-HEAD beziehungsweise den unveränderten Basis-HEAD bei deaktiviertem Merge. Der Hook-Zähler bleibt null; erneutes Resume schreibt keinen zweiten Hook-Effekt und startet keinen Hook.
- Gegen SOURCE kann weder ein anfänglich noch ein nach dem Intent bei der Kontrollmessung auftretender Digest-Fehler einen Hook-Lauf freigeben. Ein fokussierter Test simuliert den Fehler nach dem Intent, prüft das terminale Ergebnis zum selben Schlüssel und dieselbe Auslassungsbegründung sowie das Ausbleiben einer Ausführung. Der Platzhalter `unavailable` reicht auch bei wiederholt gleichem Messwert nicht als Freigabe. Die bisherigen Gründe `missing` und `merge_disabled` bleiben für ihre fehlerfreien Fälle auswertbar.
- Gegen SOURCE überspringt `_hook_reason()` einen vorhandenen Hook-Pfad im Worktree außerhalb von `.git`, wenn der Basis-HEAD keinen Baumeintrag für diesen Pfad enthält, auch wenn Merge-Basis und Ziel-HEAD ebenfalls keinen enthalten. Ein fokussierter Test setzt `core.hooksPath=.githooks`, ignoriert `.githooks/post-merge` mit Git, legt dort eine unversionierte ausführbare Marker-Datei an und prüft nach Completion: kein Marker, `status: skipped`, `reason: not_tracked_in_base` und erhaltener Merge. Positive Regressionstests belegen die Ausführung eines unveränderten getrackten Hooks, eines Hooks unter `.git/hooks` und eines extern konfigurierten Hooks.
- Gegen SOURCE stoppt `_post_merge_effect()` bei zwei `post_merge_hook`-Effekten mit derselben Work-Unit-ID und demselben Merge-Commit mit eindeutiger Widerspruchsdiagnose vor Hook-Pfadauflösung, Digest-Messung, neuem Intent und Hook-Start. Ein fokussierter Resume-Test prüft unveränderte Record-Anzahl und unveränderten Hook-Zähler; der bestätigte Merge bleibt erhalten. Der Fall mit genau einem vorhandenen, abgeschlossenen Drei- oder Vierfeld-Effekt bleibt idempotent, und ein einzelner offener Effekt behält seine bisherige Unknown-Sperre.
- Gegen SOURCE bleiben die bestehenden Tests für Hook-Fehler, fehlende Hooks, geänderte getrackte Hooks, Quittierung und Resume aussagekräftig. Der Orchestrator führt nach der Produktänderung die Standardmatrix `python3 -m pytest tests/ -v -m "not crash_harness"` aus. Der Operator führt den vollständigen Crashbeweis mit `python3 -m pytest tests/test_crash_harness.py -v` auf dem endgültigen Branch-HEAD vor dem Schlussreview aus; Agenten stellen keine Validierungsattestation aus.

## Risiken und Grenzen

Die Trennung zwischen fehlendem Hook und nicht lesbarem Hook muss auch bei einem Fehler in der zweiten Messung eindeutig bleiben; sonst könnte `unavailable` fälschlich eine Ausführung erlauben oder ein Fehler als stilles `missing` verschwinden. Eine unversionierte ignorierte Datei wird zwar nicht ausgeführt, ihre anfängliche Digest-Messung kann aber vor der Trackingentscheidung stattfinden. Der Review prüft deshalb insbesondere, dass daraus weder eine Freigabe noch ein nicht protokollierter Abbruch entsteht. Ein vorhandener offener Intent beweist weiterhin nicht, ob der Hook physisch gestartet wurde; sein Unknown-Stopp darf durch diese Änderung nicht umgangen werden. Der vollständige Schutz gegen einen externen Dateiaustausch im verbleibenden Zeitfenster zwischen Kontrollmessung und Prozessstart ist nicht Teil dieses Befunds.

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
> Geprüft: Artefaktvertrag (docs/internal/kettenende-nachbessern-followup03-arbeitsplan.md vorhanden, genau ein `### Slice 1 - ...` mit kanonischem `**Exakter Änderungspfad**` und reinen Bullet-Pfaden; Attestation `internal:work-plan-contract` PASS für denselben Fingerprint). Abdeckung der drei Abnahmebefunde: (1) Digest-Messfehler ergibt Vierfeld-Intent mit `unavailable` und terminales `skipped`/`digest_unreadable`, mit und ohne Merge, auch bei Kontrollmessung in `perform()`, Resume ohne Hookstart; (2) `_hook_reason()` verweigert vorhandene Worktree-Hooks ohne Basis-HEAD-Eintrag mit `not_tracked_in_base`, positive Regressionen für getrackte, `.git/hooks`- und externe Hooks; (3) Mehrfachtreffer stoppt fail-closed vor `_hook_path()`, Digest, `make_spec()` und `execute()`, Einzeltreffer idempotent, offener Intent behält Unknown-Sperre. Scope: nur das Plandokument geändert. Validierung korrekt Orchestrator bzw. Operator zugewiesen.

Größtes Restrisiko:
> Die Rangfolge zwischen `digest_unreadable` und den Tracking-Gründen (`not_tracked_in_base`, `changed_by_target_branch`) ist nur gegenüber `missing`/`merge_disabled` festgelegt; bei einer unlesbaren und zugleich ungetrackten Datei kann die Umsetzung einen der beiden Gründe wählen. Beide Pfade enden jedoch in `skipped` ohne Hookstart, und der Plan benennt das Risiko. Ein Absturz zwischen `unavailable`-Intent und Ergebnis bleibt konservativ in der Unknown-Sperre mit Quittierungspflicht.

Bruchbedingung:
> Der Plan scheitert, wenn die Umsetzung `unavailable` bei zwei gleichen Messwerten als Freigabe wertet, wenn der Mehrfachtreffer-Check erst nach `_hook_path()` oder `_hook_content_digest()` läuft oder wenn `not_tracked_in_base` auch fehlende Hooks in einem getrackten Verzeichnis erfasst und damit `missing` verdrängt; die Akzeptanzkriterien verlangen für jeden dieser Fälle fokussierte Tests.

Vorab-Risikoanalyse:
> Denkbares Scheitern nach Freigabe: Die Implementierung fängt `GitTransactionError` aus `_hook_content_digest()` zu breit ab und verschluckt dadurch echte Git-Fehler als `digest_unreadable`, oder sie misst im Resume-Pfad eines vorhandenen Effekts erneut und löst den alten Blockadefall wieder aus. Der Plan grenzt beides ein: nur Messfehler eines neuen Effekts werden umgewandelt, vorhandene Effekte werden ohne neue Messung wieder aufgenommen. Der Mehrfachfall-Test könnte nur mit zwei abgeschlossenen Paaren gebaut werden statt mit offenem Drei- und abgeschlossenem Vierfeld-Paar; das Kriterium verlangt aber in jedem Fall zwei passende Effekte, einen Stopp vor der Hook-Arbeit und unveränderte Zähler.
<!-- audit:approval:end -->
