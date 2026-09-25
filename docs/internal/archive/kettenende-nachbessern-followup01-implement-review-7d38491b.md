# Gesamtaudit – kettenende-nachbessern_followup01-implement

<!-- audit:meta:begin -->
Aufgabe: kettenende-nachbessern_followup01-implement · Zielbranch: `feature/fehler-parameter-sweep-liefert-nie-ein-ergebnis-07e5c0fab2a5f4e3` · Lauf: `watch-20260925-194835.440484Z-73d1fa2bac0d` · Stand: abgeschlossen
<!-- audit:meta:end -->

## Übersicht

<!-- audit:overview:begin -->
| Slice | Titel | Stand | Commit | Runden | Befunde |
|---:|---|---|---|---:|---:|
| 1 | Post-Merge-Hook kontrolliert beenden und Zielbranch-Änderungen vollständig erkennen | freigegeben | f58d040d | 3 | 1 |
<!-- audit:overview:end -->

## Befunde

<!-- audit:findings:begin -->
| ID | Herkunft | Klasse | Stand | Titel |
|---|---|---|---|---|
| C-01 | Slice 1 | Befund | geschlossen | `_hook_reason()` führt die neue Git-Prüfung jetzt vor der lstat-Schleife aus, die bisher `missing`,… |
| C-02 | Slice DISCOVERY | Befund | offen | Nach der Quittierung eines ungewissen Hook-Ausgangs erscheint beim Resume keine Warnung |
| C-03 | Slice DISCOVERY | Befund | offen | Der Nachlauf-Intent bindet keinen Hook-Digest |
| C-02 | Abnahme | Befund | offen | Nach der Quittierung eines ungewissen Hook-Ausgangs erscheint beim Resume keine Warnung. Der freigegebene Arbeitsplan sagt: „anschließend setzt Resume den Lauf fort, meldet die Ungewissheit als Warnung“. `_post_merge_effect()` in `src/workflow_completion.py` warnt aber nur bei `status` in `{"failed", "timeout"}` oder bei `skipped` mit einem anderen Grund als `missing`. Den von `acknowledge_unknown_post_merge()` geschriebenen Status `acknowledged_unknown` erfasst keiner der beiden Zweige. Auch `acknowledge_unknown_post_merge()` selbst und der Quittierungspfad in `src/orchestrator.py::run_production_workflow` protokollieren nichts. Damit endet ein Lauf, dessen Hook-Ausgang nachweislich unbekannt ist, im Laufprotokoll genauso still wie ein erfolgreicher Hook. Kein Test prüft diese Warnung, und in den Slice-Dokumenten ist keine Abweichung vermerkt. |
| C-03 | Abnahme | Befund | offen | Der Nachlauf-Intent bindet keinen Hook-Digest. Das verlangen aber der freigegebene Arbeitsplan („Der Nachlauf-Seiteneffekt bindet Run-ID, Merge-Commit, aufgelösten Hook-Pfad und Hook-Digest“) und die Schließbegründung des Plan-Befunds C-02 („Hook-Pfad und Hook-Digest sind im Intent gebunden“). `_post_merge_effect()` baut die Operation nur als `("post_merge", commit, str(hook))`. `SideEffectPayload` in `src/artifact_models.py` verlangt sogar genau drei Elemente (`len(self.operation) != 3`) und schließt einen Digest damit aus. Folge: Die append-only Kette kann nicht belegen, welcher Hook-Inhalt ausgeführt wurde oder bei einem Abbruch möglicherweise lief. Bei einer Quittierung von `acknowledged_unknown` kann der Operator den aktuellen Hook nicht mit dem Stand zur Intent-Zeit vergleichen. Außerdem stützt sich die sicherheitsrelevante Plan-Schließung auf eine Bindung, die es im Code nicht gibt. Eine dokumentierte Abweichung fehlt. |
<!-- audit:findings:end -->

## Halte und Entscheidungen

<!-- audit:holds:begin -->
Keine.
<!-- audit:holds:end -->

## Abnahmereview

<!-- audit:acceptance-review:begin -->
Abnahmereview abgeschlossen.

Geprüft:
> Geprüft am vollständigen Branch-Diff. Pfadgrenzen: alle Pfade liegen in authorized_paths. Archivmuster: Segmentvalidierung, Platzhalter, Expansion von `{year}` und `{branch_slug}`, frühe Prüfung vor Slice 1 und verschachteltes Rollback. RunProfile-Wire-Kompatibilität: Reader-Defaults None/False, pop beim Schreiben, Schemaerweiterung. Hook-Auflösung über `core.hooksPath` bzw. `--git-common-dir`. `_hook_reason`: ls-tree-Vergleich von Fork und Ziel samt Basis-Tracking (C-01 bestätigt geschlossen) sowie die Fälle Löschen, basisseitiges Hinzufügen und Typwechsel. `_run_hook`: eigene Session, SIGTERM/SIGKILL auf die Gruppe, begrenztes Warten, `termination_uncertain`. Intent/Result-Reihenfolge, UNKNOWN-Reconcile ohne Wiederholung, Quittierungsbindung an Task, Commit und Intent, Watch-Halt ohne Poison, CLI-Kombinationsregeln, README und Einrichtung. Attestation validation-[Hash ausgelassen] ist PASS (2533/0) für den aktuellen Fingerprint.

Größtes Restrisiko:
> Nicht entscheidbar, weil die Berechnung von `state.task_digest` nicht im Snapshot liegt: Der Quittierungspfad hasht `read_text(encoding="utf-8").encode()`. Durch die Newline-Übersetzung ergibt das bei einer CRLF-Auftragsdatei einen anderen Wert als ein Byte-Digest. Ist `state.task_digest` bytebasiert, scheitert die einzige Operator-Quittierung dauerhaft. Außerdem führt jeder Abbruch zwischen Intent und Ergebnis zu einem Operator-Halt, auch bei rein deterministischem Auslassen (`merge_disabled`, `missing`). Das ist fail-closed, aber für den Betrieb unnötig.

Bruchbedingung:
> Ein Operator quittiert eine CRLF-Auftragsdatei, deren gespeicherter Digest bytebasiert ist, und bleibt mit einem bereits gemergten Lauf ohne Ausweg stehen. Oder ein quittierter ungewisser Hook-Ausgang schließt ohne jede Warnung ab, und der Operator hält den Nachlauf fälschlich für erfolgreich.

Vorab-Risikoanalyse:
> Scheitert die Abschlusskette im Betrieb, dann am wahrscheinlichsten am Operator-Pfad nach einem Abbruch während des Hooks. Die Quittierung hinterlässt weder Warnung noch einen Inhaltsnachweis des Hooks, sodass ein ungewisser Build- oder Deploy-Nachlauf im Protokoll wie ein Erfolg aussieht (C-02, C-03). Ein zweites Risiko ist eine Digest-Abweichung bei CRLF-Aufträgen, die die Quittierung dauerhaft blockiert. Hook-Sicherheit (Typwechsel, Basis-Tracking, Gruppenbeendigung), Archivmuster und Legacy-Kompatibilität sind durch fokussierte Tests mit tragenden Assertions belegt.

### C-02 – Nach der Quittierung eines ungewissen Hook-Ausgangs erscheint beim Resume keine Warnung. Der freigegebene Arbeitsplan sagt: „anschließend setzt Resume den Lauf fort, meldet die Ungewissheit als Warnung“. `_post_merge_effect()` in `src/workflow_completion.py` warnt aber nur bei `status` in `{"failed", "timeout"}` oder bei `skipped` mit einem anderen Grund als `missing`. Den von `acknowledge_unknown_post_merge()` geschriebenen Status `acknowledged_unknown` erfasst keiner der beiden Zweige. Auch `acknowledge_unknown_post_merge()` selbst und der Quittierungspfad in `src/orchestrator.py::run_production_workflow` protokollieren nichts. Damit endet ein Lauf, dessen Hook-Ausgang nachweislich unbekannt ist, im Laufprotokoll genauso still wie ein erfolgreicher Hook. Kein Test prüft diese Warnung, und in den Slice-Dokumenten ist keine Abweichung vermerkt.

Klasse: Befund · Stand: offen

Befund:
> Nach der Quittierung eines ungewissen Hook-Ausgangs erscheint beim Resume keine Warnung. Der freigegebene Arbeitsplan sagt: „anschließend setzt Resume den Lauf fort, meldet die Ungewissheit als Warnung“. `_post_merge_effect()` in `src/workflow_completion.py` warnt aber nur bei `status` in `{"failed", "timeout"}` oder bei `skipped` mit einem anderen Grund als `missing`. Den von `acknowledge_unknown_post_merge()` geschriebenen Status `acknowledged_unknown` erfasst keiner der beiden Zweige. Auch `acknowledge_unknown_post_merge()` selbst und der Quittierungspfad in `src/orchestrator.py::run_production_workflow` protokollieren nichts. Damit endet ein Lauf, dessen Hook-Ausgang nachweislich unbekannt ist, im Laufprotokoll genauso still wie ein erfolgreicher Hook. Kein Test prüft diese Warnung, und in den Slice-Dokumenten ist keine Abweichung vermerkt.

Akzeptanztest:
> Gegen SOURCE gibt `_post_merge_effect()` eine sichtbare Warnung aus, wenn das Hook-Ergebnis `status: acknowledged_unknown` lautet. Die Warnung nennt den Hook-Pfad und den Merge-Commit und sagt ausdrücklich, dass der Ausgang ungewiss und nur quittiert ist. Sie erscheint beim Resume nach der Quittierung und darf weder den Hook erneut ausführen noch den Merge oder den Abschluss verhindern. Ein fokussierter Test in `tests/test_workflow_completion.py` quittiert einen offenen Intent (etwa analog zu `test_unknown_post_merge_can_be_acknowledged_once_without_rerun`) und führt die Completion erneut aus. Er prüft mit `caplog`, dass die Warnung `acknowledged_unknown` enthält, dass der Hook-Zähler unverändert bleibt und dass der Lauf erfolgreich abschließt.

### C-03 – Der Nachlauf-Intent bindet keinen Hook-Digest. Das verlangen aber der freigegebene Arbeitsplan („Der Nachlauf-Seiteneffekt bindet Run-ID, Merge-Commit, aufgelösten Hook-Pfad und Hook-Digest“) und die Schließbegründung des Plan-Befunds C-02 („Hook-Pfad und Hook-Digest sind im Intent gebunden“). `_post_merge_effect()` baut die Operation nur als `("post_merge", commit, str(hook))`. `SideEffectPayload` in `src/artifact_models.py` verlangt sogar genau drei Elemente (`len(self.operation) != 3`) und schließt einen Digest damit aus. Folge: Die append-only Kette kann nicht belegen, welcher Hook-Inhalt ausgeführt wurde oder bei einem Abbruch möglicherweise lief. Bei einer Quittierung von `acknowledged_unknown` kann der Operator den aktuellen Hook nicht mit dem Stand zur Intent-Zeit vergleichen. Außerdem stützt sich die sicherheitsrelevante Plan-Schließung auf eine Bindung, die es im Code nicht gibt. Eine dokumentierte Abweichung fehlt.

Klasse: Befund · Stand: offen

Befund:
> Der Nachlauf-Intent bindet keinen Hook-Digest. Das verlangen aber der freigegebene Arbeitsplan („Der Nachlauf-Seiteneffekt bindet Run-ID, Merge-Commit, aufgelösten Hook-Pfad und Hook-Digest“) und die Schließbegründung des Plan-Befunds C-02 („Hook-Pfad und Hook-Digest sind im Intent gebunden“). `_post_merge_effect()` baut die Operation nur als `("post_merge", commit, str(hook))`. `SideEffectPayload` in `src/artifact_models.py` verlangt sogar genau drei Elemente (`len(self.operation) != 3`) und schließt einen Digest damit aus. Folge: Die append-only Kette kann nicht belegen, welcher Hook-Inhalt ausgeführt wurde oder bei einem Abbruch möglicherweise lief. Bei einer Quittierung von `acknowledged_unknown` kann der Operator den aktuellen Hook nicht mit dem Stand zur Intent-Zeit vergleichen. Außerdem stützt sich die sicherheitsrelevante Plan-Schließung auf eine Bindung, die es im Code nicht gibt. Eine dokumentierte Abweichung fehlt.

Akzeptanztest:
> Gegen SOURCE enthält die Operation eines neuen `post_merge_hook`-Intents neben Merge-Commit und aufgelöstem Hook-Pfad einen SHA-256-Digest des Hook-Inhalts, gemessen vor der Ausführung. Ist der Hook nicht vorhanden oder kein regulärer Eintrag, steht dort ein eindeutig definierter Platzhalter. Die Validierung in `SideEffectPayload` prüft Form und Digest-Format. Für Intents ohne Digest ist eine explizite Kompatibilitätsregel festgelegt und getestet. Ein fokussierter Test belegt, dass der Intent nach einem erfolgreichen Standard-Hook den Digest der tatsächlich ausgeführten Datei trägt. Ein zweiter Test belegt, dass eine Operation mit ungültigem Digest bei der Validierung abgewiesen wird. Resume und Quittierung behalten die gebundene Operation unverändert bei.
<!-- audit:acceptance-review:end -->
