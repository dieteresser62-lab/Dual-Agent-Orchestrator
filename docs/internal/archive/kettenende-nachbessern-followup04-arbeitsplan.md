# Arbeitsplan: Zulässigkeit des Post-Merge-Hooks vollständig dokumentieren

TARGET_BRANCH: feature/fehler-parameter-sweep-liefert-nie-ein-ergebnis-07e5c0fab2a5f4e3

## Anlass und Repositorybefund

Abschnitt 2.10 von `docs/reference/einrichtung.md` nennt die Dateiform, Ausführbarkeit, Symlinkfreiheit und Änderungen durch den Zielbranch als Bedingungen für den `post-merge`-Hook. Die inzwischen geltenden Auslassungsgründe `not_tracked_in_base` und `digest_unreadable` fehlen. Dadurch erscheint etwa ein von Husky v9 unter `.husky/_` generierter, per `.gitignore` ausgeschlossener Hook laut Anleitung zulässig, obwohl `_hook_reason()` einen vorhandenen Hook im Repository-Arbeitsbaum außerhalb von `.git` ohne Baumeintrag im Basis-HEAD auslässt. `_hook_reason()` und `_post_merge_effect()` lassen auch einen nicht lesbaren beziehungsweise nicht zuverlässig messbaren Hook mit `digest_unreadable` aus. Die Warnung im Laufprotokoll allein erklärt dem Betreiber nicht, wie er einen zulässigen Hook bereitstellt.

Diese PLAN_ONLY-Runde schreibt ausschließlich dieses Arbeitsplandokument. Die spätere Produktänderung ist eine zusammenhängende Korrektur der Anwenderdokumentation; Quellcode und Tests sind nicht Gegenstand des Umsetzungsslices.

## Umsetzung

### Slice 1 - Hook-Voraussetzungen und Auslassungsgründe in Abschnitt 2.10 ergänzen

**Ziel**

Die Einrichtung beschreibt die tatsächlich durchgesetzten Voraussetzungen und zeigt dem Betreiber zulässige Ablageorte für einen `post-merge`-Hook.

**Exakter Änderungspfad**

- `docs/reference/einrichtung.md`

**Umsetzungshinweise**

- Den Absatz zum wirksamen `post-merge`-Hook in Abschnitt 2.10 erweitern: Ein vorhandener Hook innerhalb des Repository-Arbeitsbaums, aber außerhalb von `.git`, muss bereits im Basis-HEAD getrackt sein. Fehlt dort sein Baumeintrag, wird er mit `not_tracked_in_base` ausgelassen, sofern nicht bereits die Zielbranch-Änderungsprüfung den Grund `changed_by_target_branch` liefert. Ein ignoriertes, generiertes Verzeichnis wie Husky v9 `.husky/_` ausdrücklich als Beispiel nennen.
- Den praktischen Ausweg erklären: Den Hook auf dem Basisbranch tracken und unverändert durch den Zielbranch lassen; alternativ den wirksamen Hook unter `.git/hooks` oder über `core.hooksPath` an einem Pfad außerhalb des Repository-Arbeitsbaums bereitstellen. Die Trackingpflicht gilt für die beiden letzteren Orte nicht. Bei gesetztem `core.hooksPath` ist dessen Hook wirksam und der Standard-Hook wird übergangen. Die übrigen Bedingungen an Dateityp, Ausführbarkeit, Symlinkfreiheit und unveränderten Inhalt weiterhin korrekt darstellen.
- Erklären, dass ein nicht lesbarer Pfad oder ein nicht zuverlässig messbarer Hook-Inhalt zur protokollierten Auslassung mit `digest_unreadable` führt. Die Beschreibung mit `_hook_reason()` und der Digest-Prüfung in `_post_merge_effect()` abgleichen; einen tatsächlich fehlenden Hook nicht mit einem Messfehler verwechseln, denn dafür bleibt `missing` maßgeblich.
- Die Aussage „Ein zulässiger Hook wird mit Argument `0` … ausgeführt“ so einbetten, dass sie erst nach allen genannten Prüfungen gilt. Für diese reine Dokumentationsänderung keine spiegelnden Tests anlegen; der fokussierte Nachweis ist der Quellenabgleich des geänderten Abschnitts mit den beiden Funktionen.

**Akzeptanzkriterien**

- Gegen SOURCE nennt Abschnitt 2.10 ausdrücklich: Ein vorhandener `post-merge`-Hook im Repository-Arbeitsbaum außerhalb von `.git` muss im Basis-HEAD getrackt sein; andernfalls wird er mit `not_tracked_in_base` ausgelassen. Ein ignorierter, generierter Hook unter Husky v9 `.husky/_` dient als konkretes Beispiel. Die Beschreibung unterscheidet diesen Fall vom bereits vorhandenen Grund `changed_by_target_branch`.
- Gegen SOURCE nennt Abschnitt 2.10 `digest_unreadable` als Auslassungsgrund für einen nicht lesbaren Pfad oder einen nicht zuverlässig messbaren Hook-Inhalt. Die Aussage stimmt mit `_hook_reason()` und `_post_merge_effect()` überein und verwechselt den Messfehler nicht mit dem Grund `missing` für einen fehlenden Hook.
- Gegen SOURCE erklärt Abschnitt 2.10 drei praktikable Ablagen: ein im Basisbranch getrackter und vom Zielbranch nicht geänderter Worktree-Hook, ein wirksamer Hook unter `.git/hooks` sowie ein per `core.hooksPath` konfigurierter externer Pfad. Er macht deutlich, dass `core.hooksPath` den Standard-Hook übergeht und dass Dateiform, Ausführbarkeit und Symlinkfreiheit weiterhin gelten.
- Gegen SOURCE gilt die Aussage über die Ausführung mit Argument `0` erst für einen Hook, der sämtliche dokumentierten Prüfungen bestanden hat. Der fokussierte Quellenabgleich der geänderten Passage gegen `_hook_reason()` und `_post_merge_effect()` bestätigt die Begründungen; außerhalb von `docs/reference/einrichtung.md` entstehen im Umsetzungsslice keine Produktänderungen.

## Risiken und Grenzen

Die Reihenfolge der Prüfungen entscheidet über den protokollierten Grund: Ein vom Zielbranch neu hinzugefügter Hook kann `changed_by_target_branch` erhalten, bevor `not_tracked_in_base` geprüft wird; ein fehlender Hook behält `missing`. Die Dokumentation darf keine pauschale Vorrangregel für `digest_unreadable` behaupten. Ein Pfad unter `.git/hooks` hilft nur, wenn er nach der aktuellen `core.hooksPath`-Konfiguration tatsächlich wirksam ist. Der Review prüft diese Aussagen anhand des Quellcodes und des überarbeiteten Abschnitts.

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
> Geprüft wurden: das Artefakt liegt unter dem exakten Pfad docs/internal/kettenende-nachbessern-followup04-arbeitsplan.md und ist der einzige geänderte Pfad. Es gibt genau einen künftigen Slice mit Überschrift `### Slice 1 - ...` und kanonischem `**Exakter Änderungspfad**`, darunter nur `docs/reference/einrichtung.md` als Aufzählungspunkt. Alle Teile des Abnahmekriteriums aus Befund 1 sind abgedeckt: not_tracked_in_base samt Husky-Beispiel `.husky/_`, digest_unreadable, die drei Auswege (getrackt im Basisbranch, .git/hooks, externer Pfad über core.hooksPath), Abgleich mit _hook_reason() und _post_merge_effect() sowie die Aussage zu Argument `0` erst nach allen Prüfungen. Die Validierungsattestierung internal:work-plan-contract ist PASS für denselben Fingerprint. Es werden keine Produkt- oder Testdateien geändert.

Größtes Restrisiko:
> Der Plan legt sich bei der Prüfreihenfolge fest: changed_by_target_branch vor not_tracked_in_base, missing vorrangig, keine pauschale Vorrangregel für digest_unreadable. Diese Angaben ließen sich in dieser Planprüfung nicht direkt gegen src/workflow_completion.py verifizieren. Weicht die tatsächliche Reihenfolge ab, könnte die Doku falsche Gründe zuordnen.

Bruchbedingung:
> Das Planziel scheitert, wenn _hook_reason() oder _post_merge_effect() die Gründe in anderer Reihenfolge vergeben als beschrieben. Es scheitert ebenso, wenn die Trackingpflicht auch für .git/hooks oder externe core.hooksPath-Ziele gilt, etwa bei einem verlinkten Worktree mit abweichendem Common-Dir. Dann würde die umgesetzte Doku einen unwirksamen Ausweg empfehlen. Die Implementierungsprüfung muss das gegen den Quellcode abgleichen.

Vorab-Risikoanalyse:
> Wahrscheinlichster Fehlschlag der späteren Umsetzung: Der Implementierer übernimmt die Formulierungen des Plans zur Prüfreihenfolge und zu den Auswegen, ohne sie gegen _hook_reason() und _post_merge_effect() zu prüfen. Dann dokumentiert Abschnitt 2.10 eine Vorrangregel oder einen Ausweg, den der Code nicht einhält. Die Akzeptanzkriterien verlangen ausdrücklich den Quellenabgleich gegen SOURCE. Die Implementierungsprüfung muss genau diese Übereinstimmung verifizieren.
<!-- audit:approval:end -->
