# Arbeitsplan: Warnung und Inhaltsbindung des Post-Merge-Hooks nachbessern

TARGET_BRANCH: feature/fehler-parameter-sweep-liefert-nie-ein-ergebnis-07e5c0fab2a5f4e3

## Anlass und Repositorybefund

Der dritte Abnahmereview meldet zwei Lücken im protokollierten `post-merge`-Nachlauf. `src/workflow_completion.py::_post_merge_effect()` verwendet den vorhandenen Ergebnisrecord beim Resume, warnt aber nur bei `failed`, `timeout` und bestimmten `skipped`-Ergebnissen. Die vom Operator geschriebene Quittierung `acknowledged_unknown` bleibt im Laufprotokoll unsichtbar. Der vorhandene Test `test_unknown_post_merge_can_be_acknowledged_once_without_rerun` belegt die einmalige Quittierung und das Ausbleiben einer Hook-Wiederholung, prüft jedoch keine Warnung.

Neue `post_merge_hook`-Intents verwenden derzeit die Operation `("post_merge", merge_commit, hook_path)`. `SideEffectPayload` lässt für diese Klasse ausschließlich drei Felder zu. Der freigegebene Ausgangsplan bindet zusätzlich einen Digest des Hook-Inhalts. Der Seiteneffekt-Schlüssel und der Fingerprint hängen bereits von der gesamten Operation ab; ein viertes Feld bindet damit den Digest ohne neues Record-Feld. Die JSON-Schema-Definition der Operation ist eine nichtleere Liste von Zeichenfolgen und erzwingt keine Länge, sodass die fachliche Formprüfung in `src/artifact_models.py` erfolgen kann. Bestehende Dreifeld-Records dürfen bei Resume und Quittierung nicht umgeschrieben oder durch neue Schlüssel ersetzt werden.

Diese PLAN_ONLY-Runde erstellt nur dieses Arbeitsplandokument. Die Produktänderungen bilden einen zusammenhängenden Umsetzungsslice, weil Operation, Validierung, Quittierung und Resume denselben Seiteneffekt betreffen.

## Umsetzung

### Slice 1 - Quittierte Ungewissheit warnen und Hook-Inhalt im Intent binden

**Ziel**

Ein quittierter ungewisser Hook-Ausgang bleibt beim Resume sichtbar; jeder neue Nachlauf-Intent bindet den vor dem Hook-Start ermittelten Inhaltsdigest, während alte offene Intents unverändert wiederaufgenommen werden.

**Exakter Änderungspfad**

- `src/artifact_models.py`
- `src/workflow_completion.py`
- `tests/test_artifact_models.py`
- `tests/test_workflow_completion.py`

**Umsetzungshinweise**

- `_post_merge_effect()` ermittelt den wirksamen Hook-Pfad wie bisher nach dem bestätigten Merge. Für einen neuen Intent besteht die Operation aus `("post_merge", merge_commit, aufgelöster_hook_pfad, inhaltsdigest)`. Der Digest ist `hashlib.sha256` über die rohen Dateibytes einer regulären Hook-Datei und wird vor dem Seiteneffekt-Intent und vor jeder Ausführung gemessen. Für einen fehlenden Hook oder einen nicht regulären Eintrag, einschließlich eines Symlinks, gilt als einziger ausdrücklich definierter Platzhalter `unavailable`. Die Messung darf Sonderdateien nicht blockierend lesen; Pfad- und Dateitypprüfung sowie Fehlerbehandlung führen bei fehlender regulärer Datei zum Platzhalter und zum vorhandenen Auslassungspfad. Ein regulärer, aber nicht ausführbarer Hook kann einen echten Inhaltsdigest tragen, obwohl die Ausführung ausgelassen wird.
- `_post_merge_effect()` und `acknowledge_unknown_post_merge()` suchen in der validierten Replay-Sicht zuerst nach vorhandenen `post_merge_hook`-Effekten mit derselben Work-Unit-ID, Effektklasse und demselben Merge-Commit. Diese Suche benutzt weder den aktuellen Hook-Pfad noch einen neu berechneten Digest oder Seiteneffekt-Schlüssel; sie erfolgt vor jeder neuen Hook-Auflösung und Digest-Messung. Mehrere passende Intents sind ein Widerspruch und führen fail-closed zum Stopp, statt einen davon willkürlich zu wählen. Bei genau einem Treffer stammen Operation und daraus abgeleiteter Schlüssel ausschließlich aus dem gespeicherten Intent, unabhängig davon, ob er drei oder vier Felder hat und ob bereits ein Ergebnis vorliegt. Ein abgeschlossenes Intent/Ergebnis-Paar wird über diesen gespeicherten Schlüssel wiedererkannt; Resume legt keinen neuen Intent an, startet den Hook nicht erneut und schreibt keinen zweiten Ergebnisrecord. Nur wenn kein passender Effekt existiert, wird der aktuelle Hook aufgelöst, sein Digest gemessen und eine neue Vierfeld-Operation angelegt.
- Der beim neuen Intent gebundene Digest muss den für die Ausführung ausgewählten Inhalt beschreiben. Zwischen Messung und Start wird der Hook nochmals gegen die gebundene Operation geprüft; falls Pfad, Typ oder Inhalt gewechselt haben, wird der Hook mit einem erkennbaren Grund ausgelassen und ein Resultat zu **demselben** Intent geschrieben. Ein offener Intent wird bei Resume auch nach einer Änderung des Hook-Inhalts nicht automatisch ausgeführt; der ungewisse physische Ausgang bleibt fail-closed. Die Quittierung verwendet denselben Seiteneffekt-Schlüssel, dieselbe Operation und denselben Fingerprint wie der offene Intent. Ein alter Dreifeld-Intent wird dabei weder mit einem aktuellen Digest ergänzt noch neu angelegt.
- `SideEffectPayload` akzeptiert die bisherige Dreifeldform ausschließlich als ausdrückliche Kompatibilität für vorhandene Records und prüft bei der neuen Vierfeldform Marker `post_merge`, 40-stellige kleingeschriebene Merge-OID, nichtleeren Hook-Pfad sowie entweder 64 kleingeschriebene hexadezimale SHA-256-Zeichen oder exakt `unavailable`. Neue Intents werden nur in Vierfeldform erzeugt. Ungültige Längen, Marker, OIDs und Digests werden abgewiesen. Der bestehende Schlüssel bindet Run-ID über den Ledger-Kontext und Work-Unit-ID sowie die vollständige Operation; keine Migration alter Ketten findet statt.
- `_post_merge_effect()` warnt nach dem Lesen des terminalen Ergebnisses auch bei `status: acknowledged_unknown`. Die Warnung enthält `acknowledged_unknown`, den gebundenen Hook-Pfad, den Merge-Commit und eine eindeutige Aussage, dass der Hook-Ausgang ungewiss und nur quittiert ist. Sie wird bei der ersten Completion nach Quittierung sichtbar, ohne Erfolg zu behaupten, den Hook erneut zu starten oder Merge und Abschluss zurückzunehmen. Bereits protokollierte normale Ergebnisse behalten ihre bisherige Behandlung.

**Akzeptanzkriterien**

- Gegen SOURCE enthält jeder neu erzeugte `post_merge_hook`-Intent die Operation mit Merge-Commit, aufgelöstem Hook-Pfad und vor der Ausführung gemessenem SHA-256-Digest der rohen Bytes. Fehlt ein regulärer Hook-Eintrag, steht im Digest-Feld exakt `unavailable`; der bestehende Grund für das Auslassen bleibt auswertbar. Ein fokussierter Test mit ausführbarem Standard-Hook prüft am Intent den SHA-256-Digest genau der ausgeführten Datei, den Merge-Commit und den Hook-Pfad.
- Gegen SOURCE weist `SideEffectPayload` eine Vierfeld-Operation mit ungültigem Digest zurück. Fokussierte Modelltests belegen gültige SHA-256- und `unavailable`-Werte sowie ungültiges Format; die bisherige Dreifeldform bleibt für alte Intent- und Ergebnisrecords gültig. Ein Resume-Test belegt, dass ein alter offener Intent samt Schlüssel bei Quittierung unverändert bleibt und keinen neuen Vierfeld-Intent erzeugt.
- Gegen SOURCE findet Resume ein abgeschlossenes Vierfeld-Intent/Ergebnis-Paar über Work-Unit-ID, Effektklasse und Merge-Commit, nachdem sich der Hook-Inhalt geändert hat. Ein fokussierter Test prüft den erfolgreichen Abschluss ohne neuen Intent, zweiten Hook-Lauf oder zweiten Ergebnisrecord; der gespeicherte Schlüssel und die gespeicherte Operation bleiben erhalten.
- Gegen SOURCE findet Resume ein abgeschlossenes Dreifeld-Altpaar über dieselben digestunabhängigen Merkmale. Ein fokussierter Test prüft den erfolgreichen Abschluss ohne neuen Vierfeld-Intent, zweiten Hook-Lauf oder zweiten Ergebnisrecord; Schlüssel und Operation des Altpaares bleiben erhalten.
- Gegen SOURCE vergleicht die Ausführung den aktuellen regulären Hook-Inhalt mit dem vor dem Intent gebundenen Digest und führt einen zwischenzeitlich geänderten Hook nicht als die gebundene Version aus. Ein fokussierter Test verändert die Datei nach dem Intent und vor der Ausführung, erwartet einen protokollierten Auslassungsgrund und keine Hook-Ausführung. Ein weiterer fokussierter Test verändert den Inhalt nach einem offenen Vierfeld-Intent und prüft beim Resume den fail-closed-Stopp wegen ungewissen physischen Ausgangs, ohne neuen Intent, neuen Ergebnisrecord oder erneuten Hook-Lauf; erst eine ausdrückliche Quittierung darf den Lauf fortsetzen.
- Gegen SOURCE gibt `_post_merge_effect()` beim Resume nach einer `acknowledged_unknown`-Quittierung eine sichtbare Warnung mit Status, Hook-Pfad, Merge-Commit und ausdrücklich ungewissem, nur quittiertem Ausgang aus. Ein fokussierter Test quittiert einen offenen Intent, führt Completion erneut aus und prüft die Warnung mit `caplog`, den unveränderten Hook-Zähler und den erfolgreichen Abschluss. Wiederholtes Resume erzeugt weder einen zweiten Hook-Lauf noch einen zweiten Ergebnisrecord; Merge und Abschluss bleiben erhalten.
- Gegen SOURCE bleiben die vorhandenen Nachlauf- und Crash-Fenster-Tests aussagekräftig. Die Orchestrator-Standardmatrix ist `python3 -m pytest tests/ -v -m "not crash_harness"`; der Operator führt den vollständigen Crashbeweis auf dem endgültigen Branch-HEAD vor dem Schlussreview aus. Die Agenten stellen keine Validierungsattestation aus.

## Risiken und Grenzen

Ein Inhaltsdigest beschreibt die gelesenen Dateibytes, nicht automatisch eine gegen gleichzeitiges Überschreiben geschützte Ausführungsdatei. Die Umsetzung muss die Lücke zwischen Intent und Prozessstart bewusst behandeln und den Hook bei erkannter Änderung auslassen; ein verbleibendes Rennen bei externen Änderungen ist im Review als Restrisiko zu prüfen. Der Platzhalter `unavailable` belegt ausdrücklich keinen konkreten Inhalt und darf niemals als Freigabe zum Ausführen einer später auftauchenden Datei dienen. Bestehende Dreifeld-Intents besitzen rückwirkend keinen Inhaltsbeleg; ihre unveränderte Operation und das Verbot der automatischen Wiederholung sind die Kompatibilitätsgrenze.

## Orchestrator-Prüfprotokoll

### Verlauf

<!-- audit:history:begin -->
- Runde 1: Planung · Validierung grün · Prüfurteil abgelehnt · 1 neu, 0 geschlossen.
- Runde 2: Planrevision · Validierung grün · Prüfurteil freigegeben · 0 neu, 1 geschlossen.
<!-- audit:history:end -->

### Befunde

<!-- audit:findings:begin -->
### C-01 – Resume-Idempotenz bei digestabhängigem Seiteneffekt-Schlüssel ist im Plan nicht abgesichert

Klasse: Befund · Stand: geschlossen

Befund:
> Resume-Idempotenz bei digestabhängigem Seiteneffekt-Schlüssel ist im Plan nicht abgesichert. Der Plan hält selbst fest, dass Schlüssel und Fingerprint von der gesamten Operation abhängen. Mit dem Digest als viertem Feld liefert ein beim Resume neu berechneter Schlüssel einen anderen Wert, sobald sich der Hook-Inhalt geändert hat. Dasselbe gilt immer für alte Dreifeld-Records. Der Plan verlangt zwar, dass ein „bereits vorhandener Intent“ seine ursprüngliche Operation übernimmt. Er legt aber nicht fest, wie `_post_merge_effect()` und `acknowledge_unknown_post_merge()` einen vorhandenen Intent oder ein vorhandenes Ergebnis finden, ohne den Schlüssel aus dem aktuellen Digest zu bilden. Die Akzeptanzkriterien decken nur zwei Fälle ab: den alten offenen Dreifeld-Intent bei Quittierung und die Wiederholung nach Quittierung. Ungeprüft bleibt der Resume-Fall mit einem bereits abgeschlossenen Intent/Ergebnis-Paar, bei dem der Hook-Inhalt geändert wurde oder ein Dreifeld-Altrecord vorliegt. Dort könnte ein neuer Vierfeld-Intent entstehen und der Hook ein zweites Mal laufen.

Akzeptanztest:
> Der Arbeitsplan legt in den Umsetzungshinweisen fest, dass vorhandene `post_merge_hook`-Intents und -Ergebnisse über ein Merkmal gesucht werden, das nicht vom aktuellen Digest abhängt, etwa Work-Unit, Effektklasse und Merge-Commit. Diese Suche findet vor jeder neuen Digest-Messung statt. Den Schlüssel eines vorhandenen Intents übernimmt der Code aus dessen gespeicherter Operation. Der Plan enthält außerdem je ein fokussiertes Akzeptanzkriterium für zwei Resume-Fälle: (a) abgeschlossenes Vierfeld-Paar, danach geänderter Hook-Inhalt, und (b) abgeschlossenes Dreifeld-Altpaar. In beiden Fällen gibt es keinen neuen Intent, keinen zweiten Hook-Lauf und keinen zweiten Ergebnisrecord, und der Abschluss gelingt. Für den offenen Vierfeld-Intent mit geändertem Inhalt verlangt ein Kriterium ausdrücklich einen Test, der das fail-closed-Verhalten belegt.

Antwort des Implementierers, Runde 1 (angenommen):
> Der Arbeitsplan verlangt nun die digestunabhängige Suche nach vorhandenen Hook-Effekten vor jeder neuen Digest-Messung. Er bindet den Schlüssel an die gespeicherte Operation und ergänzt fokussierte Resume-Tests für abgeschlossene Drei- und Vierfeld-Paare sowie einen offenen Vierfeld-Intent mit geändertem Hook-Inhalt.

Abschlussbegründung des Prüfers:
> Der Plan erfüllt jetzt alle Punkte des Abnahmekriteriums. Laut den Umsetzungshinweisen suchen `_post_merge_effect()` und `acknowledge_unknown_post_merge()` in der validierten Replay-Sicht über Work-Unit-ID, Effektklasse und Merge-Commit. Diese Suche findet vor jeder Hook-Auflösung und Digest-Messung statt und verwendet weder Pfad, Digest noch einen neu berechneten Schlüssel. Bei genau einem Treffer kommen Operation und Schlüssel aus dem gespeicherten Intent, egal ob drei oder vier Felder. Mehrere Treffer führen fail-closed zum Stopp. Für jeden der beiden Resume-Fälle gibt es ein eigenes Akzeptanzkriterium: (a) abgeschlossenes Vierfeld-Paar nach Inhaltsänderung und (b) abgeschlossenes Dreifeld-Altpaar. Beide verlangen: kein neuer Intent, kein zweiter Hook-Lauf, kein zweiter Ergebnisrecord, und der Abschluss gelingt. Für den offenen Vierfeld-Intent mit geändertem Inhalt fordert ein eigener Test ausdrücklich den fail-closed-Stopp.
<!-- audit:findings:end -->

### Abschlussprüfung

<!-- audit:approval:begin -->
Geprüft:
> Geprüft habe ich fünf Dimensionen. (1) Korrektheit: die Vierfeld-Operation, der Platzhalter `unavailable` und die Prüfung der Formate für OID und Digest. (2) Vertrag: genau ein Slice mit kanonischer Überschrift `### Slice 1 - …` und `**Exakter Änderungspfad**`, dazu vier exakte Pfade, die zu den betroffenen Pfaden beider Abnahmebefunde passen. (3) Fehlerpfade: Symlink und Sonderdateien werden nicht blockierend gelesen, eine Änderung zwischen Messung und Start führt zum Auslassen mit Ergebnis zum selben Intent. (4) Sicherheit: `unavailable` gibt nie eine Ausführung frei. (5) Resume und Idempotenz: digestunabhängige Suche, gespeicherter Schlüssel, Dreifeld-Kompatibilität ohne Migration, Warnung bei `acknowledged_unknown` ohne erneuten Lauf.

Größtes Restrisiko:
> Zwischen der zweiten Digest-Prüfung und dem Prozessstart bleibt ein TOCTOU-Fenster. Ein externer Schreiber könnte den Hook genau dort austauschen. Der Plan benennt das Risiko ausdrücklich, schließt es aber nicht vollständig.

Bruchbedingung:
> Der Plan scheitert, wenn die Umsetzung den Seiteneffekt-Schlüssel beim Resume doch aus der aktuellen Operation bildet. Dasselbe gilt, wenn die Replay-Suche den Merge-Commit aus einer anderen Quelle bezieht als der ursprüngliche Intent. Dann entstünde nach einer Hook-Änderung ein zweiter Vierfeld-Intent mit erneutem Hook-Lauf.

Vorab-Risikoanalyse:
> Wahrscheinlichster Fehlschlag in der Umsetzung: Die Suche nach vorhandenen Effekten filtert zwar nach Merge-Commit, aber die Ergebnisrecords werden weiter über einen neu berechneten Schlüssel zugeordnet. Dann bliebe ein abgeschlossenes Altpaar unerkannt. Ebenso möglich: Die Warnung bei `acknowledged_unknown` erscheint nur im Quittierungspfad und nicht bei der Completion. Die geforderten Tests für Drei- und Vierfeld-Paare sowie der `caplog`-Test würden beide Fehler aufdecken.
<!-- audit:approval:end -->
