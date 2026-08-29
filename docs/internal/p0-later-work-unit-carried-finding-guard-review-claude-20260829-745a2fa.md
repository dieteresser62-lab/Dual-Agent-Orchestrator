# Review: P0-Folge-Hotfix „Carried Findings in späteren Work Units" (`745a2fa`)

Reviewrolle: unabhängiger Reviewer, read-only.
Datum: 29. August 2026.
Prüfgegenstand: lokaler Commit `745a2fa` gegen seinen direkten Vorgänger `6dbd03c`.

## 1. Selbst erhobener Stand

| Größe | Wert |
|---|---|
| Branch | `feature/audit-evidence-self-poisoning-guard` |
| HEAD | `745a2fa` — „fix: preserve carried findings after imported slice" |
| Vorgänger | `6dbd03c` — „Slice 01: Kanonische semantische Markdowngrenze für Fingerprint, Guards und Reviews" |
| Delta `6dbd03c..745a2fa` | 3 Dateien, 202 Zeilen, ausschließlich Ergänzungen |

```
docs/internal/p0-later-work-unit-carried-finding-guard-hotfix-20260829.md |  59 +
src/artifact_migration.py                                                |   1 +
tests/test_artifact_migration.py                                         | 142 +
```

Der gesamte Produktivanteil ist **eine Zeile** in `src/artifact_migration.py:274`:

```python
         if (
             isinstance(payload, WorkUnitPayload)
+            and payload.finding_import_record_id is not None
             and payload.open_finding_ids != tuple(sorted(unit.open_findings))
             and not pending_work_record
         ):
```

Kein Kollateralschaden: keine weitere Produktivdatei, kein Schemawechsel, keine Vertragsdatei, keine Änderung an Replay, Writer oder Recordmodell.

Arbeitsbaumstand zum Reviewzeitpunkt (nicht Teil des Prüfgegenstands, aber für die Wiederaufnahme relevant): `docs/internal/01-…-implement-review-e94f81e2.md` geändert (82 Einfügungen, die vom Orchestrator selbst projizierte Auditdatei), `docs/internal/slice-01-…-arb-02-digestgebundene-validierungsdiagnostik-und-sichere-auditdarstellung.md` neu und untracked (das managed Slice-Dokument für Slice 02), `src/orchestrator.py` in `git status` als geändert markiert, aber mit **leerem** `git diff` — ein reiner CRLF/LF-Artefakt des Windows-Mounts, keine inhaltliche Änderung. Alle drei Pfade liegen innerhalb der persistierten Scope von Slice 02.

Schreibgrenze eingehalten: kein Produktivcode, keine Tests, keine Git-Referenz, kein Index, kein State, kein Checkpoint, kein Record verändert; kein `run_task`, kein Watcher, kein Provider. Sämtliche Gegenproben liefen gegen einen `git archive`-Export von `745a2fa` in einem temporären Verzeichnis und gegen **Kopien** von `.orchestrator/artifacts` und `state.json`; das Original blieb unangetastet.

## 2. Eigene providerfreie Gegenproben

| Probe | Aufbau | Ergebnis |
|---|---|---|
| P1 | beide neuen Tests gegen `745a2fa` | `2 passed` |
| P2 | dieselben Tests, Guardzeile im Export **entfernt** (Vorzustand `924a554`) | `test_later_work_unit_can_carry_open_finding_without_import_snapshot` **fällt** mit `MIRROR-AMBIGUOUS … latest work-unit finding state differs from state-v3` |
| P3 | Gleichheitsprüfung im Export **vollständig gelöscht** | `test_import_bound_latest_work_unit_still_rejects_mirror_open_set_drift` **fällt**; der Drift wird aber weiterhin von der Findingledger-Spiegelung als `finding transitions differ from state-v3` gefangen |
| P4 | eigener Probetest in **Produktionsform**: importgebundene erste Implementierungs-Unit mit `C-01`, Schließung von `C-01`, Eröffnung von `C-04`, Slice-Commit, danach spätere Work Unit 3 mit carried `C-04` | gegen `745a2fa` **grün**, gegen den Vorzustand **rot** mit exakt der Produktionsdiagnose |
| P5 | `pytest tests/test_artifact_migration.py tests/test_artifact_replay.py -q -p no:cacheprovider` im isolierten Export | `73 passed` |
| P6 | `resolve_resume_state` gegen eine **Kopie** des realen Laufs unter `745a2fa` | `RESUME RESOLVES; head ar1-e12ffc3b1618; mode STRUCTURED_V2` |

P4 ist die wichtigste Probe: der positive Regressionstest des Hotfixes enthält **keinen** Finding-Import, die Produktionslage sehr wohl. P4 schließt diese Lücke und bestätigt, dass die Korrektur genau die reale Konstellation repariert.

## 3. Disposition der verbindlichen Prüfdimensionen

**(1) `open_finding_ids` ist nur der initiale Import-Snapshot — bestätigt.**
Writer `src/orchestrator.py:420-436`: `bound_import` ist ausschließlich dann der Importrecord, wenn `unit.work_unit_id == first_implementation_unit_id`; `open_finding_ids` wird über denselben Ausdruck gefüllt (`… if bound_import is not None else ()`) wie `finding_import_record_id`. Beide Felder bewegen sich also zwingend gemeinsam — genau darauf setzt der neue Guard auf, und deshalb ist er strukturell korrekt und nicht bloß symptomatisch. Replay `src/artifact_replay.py:498-531` leitet die Erwartung für importgebundene Records aus dem vollständigen autoritativen Präfix ab (Import plus vorausgehende Finding-Transitions), nicht aus dem Import allein. Migration `src/artifact_migration.py:640-647` dokumentiert dieselbe Semantik bereits im Code: „``open_findings`` records the immutable attribution carried into a correction work unit. It is not the live finding mirror." Writer, Replay und Migration sind nach dem Hotfix erstmals wieder auf dieselbe Aussage verpflichtet.

**(2) Spätere Work Units mit carried Findings zulässig, ohne Finding-Autorität zu verlieren — bestätigt.**
Die lebende Findinglinie wird unabhängig geprüft, in `src/artifact_migration.py:375-412`: Der Ledger des Mirrors (`_finding_statuses`) wird gegen die letzten Finding-Transitions der Kette plus die importierten Status verglichen — sowohl auf Identitätsmenge (`record_finding_ids != set(finding_statuses)`) als auch auf jeden einzelnen Status. P3 belegt das empirisch und nicht nur durch Codelektüre: nach vollständiger Löschung der Open-Mengen-Gleichheit wird derselbe manipulierte Zustand weiterhin fail-closed abgewiesen, nur mit anderer Diagnose. Der Guard nimmt also keiner Prüfung die Autorität; er entfernt eine Doppelprüfung dort, wo sie nachweislich falsch war.

**(3) Importgebundene erste Implementierungs-Work-Unit bleibt fail-closed — bestätigt.**
P1 und der negative Test zeigen die typisierte Ablehnung; P4 zeigt, dass der Pfad in Produktionsform erhalten bleibt. Zusätzlich prüft die erste Schleife (`src/artifact_migration.py:252-259`) unverändert für **jeden** Work-Unit-Record, dass die Importbindung exakt der erwarteten entspricht — eine später erfundene oder unterschlagene Bindung bleibt damit unabhängig von der Open-Menge fail-closed.

**(4) Testqualität — tragfähig, mit einer benannten Realismus-Lücke.**
Der positive Test besitzt echte Falsifikationskraft: unter P2 fällt er mit exakt der Produktionsdiagnose. Der negative Test pinnt Identität und Meldung der beibehaltenen Prüfung; unter P3 fällt er, sobald die Prüfung entfernt wird. Beide zusammen sichern beide Richtungen ab — Überbreite und Löschung. Die Lücke: der positive Test enthält gar keinen `FindingHandoffImportPayload`, weshalb bei ihm `expected_import_id` durchgehend `None` ist. Er beweist damit die Entkopplung, nicht das Zusammenspiel mit einem tatsächlich vorhandenen Import. Das ist der Fall des realen Laufs. Meine Probe P4 schließt die Lücke und bestätigt das Verhalten; die Testbasis selbst deckt sie nicht ab. Siehe RN-03.

**(5) Realer persistierter Lauf — vollständig erhalten.**
Read-only gegen eine Kopie geprüft, 37 Records, Kette lückenlos:

```
work-unit-2 rev 1  round 1  open ('C-01',)          import ar1-fb4081cfbc
finding C-01 responded open (codex) → C-01 closed (claude) → C-02 opened → C-03 opened
work-unit-2 rev 2  round 2  open ('C-02','C-03')    import ar1-fb4081cfbc
finding C-02 responded → C-03 responded
review-claude-2-2  approved  wu 2  findings ('C-01','C-02','C-03','C-04')
finding C-02 closed → C-03 closed → C-04 opened
commit binding → 6dbd03c379d5700e6114619656845c9735ee5ae5
                 attestation ar1-c4ee34017a, approval ar1-8b3b96d317
work-unit-3 rev 1  round 1  open ()                 import –
```

Slice 01, Commit-Binding `6dbd03c`, Claude-Freigabe, die offene `OBSERVATION C-04` und Work Unit 3 sind vollständig vorhanden. Nichts wurde erfunden oder repariert. Seit dem Poison (`15:48:36Z`) sind der Kette **keine** Records hinzugefügt worden; State-Mirror und Kette sind konsistent. P6 bestätigt: unter `745a2fa` löst der reale Lauf sauber auf.

Zusätzlich belegt: der Vertragsdigest der unveränderten Poison-Bytes ist identisch mit `state.task_digest`, und der Kontrakt liest sich als `IMPLEMENT` mit `approved_plan_commit=ebb903f2…`. Die dokumentierte Wiederherstellung erfüllt damit nachweislich die Identitäts- und Digestprüfung von `--resume`.

**(6) Dokumentierte Wiederaufnahme — im Kern richtig, zwei Auflagen fehlen.**
Richtig und wichtig ist, dass das Hotfixdokument den Watcher-Weg ausschließt und den direkten Einstieg vorschreibt: der Poison-Übergang löscht das Watch-Sidecar, ein wieder eingelegter Task startet im Watcher mit `resume=False, force_new=True, force_overwrite_state=True` und damit einen neuen Lauf. Ebenso richtig ist die Auflage, den Watcher erst nach kontrollierter Entfernung der Aufgabe wieder zu starten — denn der direkte Weg ruft ohne Sidecar kein `finalize_queue_success` auf und lässt die Aufgabe in der Inbox liegen. Es fehlen RN-01 und RN-02.

**(7) Angrenzende Fälle — geprüft, kein weiterer ausführbarer Defekt.**
- *Spätere Slice-Runden:* Eine Denial-Runde derselben Unit schreibt über `return_to_codex_after_denial` (`src/workflow_state.py:2028/2057`) eine neue Runde und damit eine neue Recordrevision mit der dann gültigen Menge. Record und Mirror bewegen sich gemeinsam.
- *Correction-Work-Units:* `open_findings` wird bei Erzeugung gesetzt (`src/workflow_state.py:1509`) und der Record trägt genau diese Menge. Die Gleichheit für `CorrectionWorkUnitPayload` bleibt bewusst ungeguardet und ist korrekt, weil der Writer dieses Feld immer aus `unit.open_findings` füllt. Siehe RN-04 zur Asymmetrie.
- *Final-Review-Korrekturen:* Final-Review-Units sind nie die erste Implementierungs-Unit, tragen also `finding_import_record_id=None` und `open_finding_ids=()`. Vor dem Hotfix wären auch sie mit jeder offenen Finding gescheitert; der Guard deckt sie mit ab.
- *Geschlossene Findings:* Die Statusprüfung liegt vollständig in `src/artifact_migration.py:400-412` und ist vom Guard nicht berührt; der reale Lauf mit drei geschlossenen und einer offenen Finding löst auf (P6).
- *Mehrere carried Observations:* Der Guard kurzschließt vor jedem Mengenvergleich; die Anzahl ist ohne Einfluss.
- *Läufe ohne Finding-Import:* `expected_import_id` ist überall `None`, `finding_import_record_id` ebenfalls — Verhalten identisch zum Zustand vor `924a554`.

## 4. Reviewnotizen

Dies ist ein Konvergenzreview des Folge-Hotfixes; ich eröffne daher **keine** neue `C-`-`OBSERVATION`. Die folgenden Notizen tragen eigene, stabile `RN-`-Kennungen, sind keine Findings der Findinglinie und keine davon verweigert die Freigabe.

**RN-01 — Wiederaufnahme läuft mit Sicherheit in ein `UNEXPECTED-PATH`-Gate; nicht dokumentiert.**
*Schweregrad:* operativ, nicht freigabeblockierend.
*Fundstelle:* `docs/internal/p0-later-work-unit-carried-finding-guard-hotfix-20260829.md`, Abschnitt „Wiederaufnahmegrenze".
*Sachverhalt:* Der Startpunkt von Slice 02 ist `6dbd03c`. Der Hotfix `745a2fa` liegt danach auf demselben Branch, und keine seiner drei Dateien steht in der Scope von Slice 02:

```
git diff --name-only 6dbd03c 745a2fa
docs/internal/p0-later-work-unit-carried-finding-guard-hotfix-20260829.md
src/artifact_migration.py
tests/test_artifact_migration.py
```

Der kanonische Slice-Diff wird ab `6dbd03c` gemessen, enthält diese drei Pfade und erzeugt deshalb dasselbe fingerprintgebundene Nutzergate, das bereits `924a554` ausgelöst hat. Dieses Reviewdokument selbst ist ein vierter solcher Pfad. Das ist kein Fehler, sondern die vorgesehene Sicherung — aber es gehört als erwartete Folgeauflage in die Wiederaufnahmeanleitung, damit es nicht erneut als Abbruch gelesen und nicht per State-Eingriff umgangen wird.
*Akzeptanztest:* `git diff --name-only 6dbd03c HEAD` liefert ausschließlich Pfade, die entweder in `state.slices[slice_id=2].scope_paths` enthalten sind, oder die Wiederaufnahmeanleitung benennt für jeden übrigen Pfad die erwartete Gate-Entscheidung samt Begründung.

**RN-02 — Originalpfad der Poison-Aufgabe nicht wörtlich benannt.**
*Schweregrad:* operativ, nicht freigabeblockierend.
*Fundstelle:* dasselbe Dokument, „unter seinem Originalnamen".
*Sachverhalt:* `--resume` prüft pfadexakt gegen `state.task_file`. Die Poison-Datei trägt zusätzlich den Zeitstempel-Präfix `20260829T154920.305Z_` und das Suffix `.poison`; der einzig gültige Zielpfad ist
`inbox/01-validierungsevidenz-darf-aktive-pruefungen-nicht-selbst-vergiften-implement.md`.
Jede Abweichung endet in einem harten `StateSchemaError`. Bei einer Anleitung, die genau eine Wiederherstellung überstehen muss, gehört dieser Pfad wörtlich hinein.
*Akzeptanztest:* Der Vertragsdigest der wiederhergestellten Datei ist gleich `state.task_digest` **und** ihr absoluter Pfad ist gleich `state.task_file`. Beides habe ich für die unveränderten Poison-Bytes bereits verifiziert (Digestgleichheit bestätigt).

**RN-03 — Positiver Regressionstest bildet die Produktionsform nicht vollständig ab.**
*Schweregrad:* Testabdeckung, nicht freigabeblockierend.
*Fundstelle:* `tests/test_artifact_migration.py::test_later_work_unit_can_carry_open_finding_without_import_snapshot`.
*Sachverhalt:* Der Test enthält keinen `FindingHandoffImportPayload`; er beweist die Entkopplung, nicht das Zusammenspiel mit einer tatsächlich importgebundenen ersten Implementierungs-Work-Unit. Das Verhalten ist korrekt — ich habe es mit P4 nachgewiesen —, aber nicht regressionsgesichert. Wer künftig den Guard an die Unit-Identität statt an das Payloadfeld bindet, bricht die Produktionsform, ohne dass ein Test rot wird.
*Akzeptanztest:* Ein Test in Produktionsform — Import mit `C-01`, Schließung, Eröffnung von `C-04`, Slice-Commit, spätere Work Unit mit carried `C-04` — muss gegen `745a2fa` grün und nach Entfernen der Guardzeile rot sein. Meine Probe P4 ist dafür verwendbar; sie liegt außerhalb des Repositorys und wurde bewusst nicht eingecheckt.

**RN-04 — Ungeguardeter Zwilling für `CorrectionWorkUnitPayload`.**
*Schweregrad:* Restrisiko, kein aktueller Defekt.
*Fundstelle:* `src/artifact_migration.py:282-289`.
*Sachverhalt:* Für Correction-Records gilt `payload.finding_ids != unit.open_findings` weiterhin ohne jede Bindung an ein Payloadfeld. Heute ist das sicher, weil der Writer dieses Feld ausnahmslos aus `unit.open_findings` füllt. Die Sicherheit beruht damit auf einer Writer-Konvention, nicht auf einer im Vergleich sichtbaren Bedingung — dieselbe Konstruktion, deren Bruch den vorliegenden Vorfall verursacht hat.
*Akzeptanztest:* Ein Test, der eine Correction-Work-Unit mit leerer `finding_ids`-Menge und nichtleerer lebender Open-Menge persistiert, dokumentiert die erwartete Diagnose explizit.

## 5. Größtes Restrisiko und realistische Bruchbedingung

Größtes Restrisiko ist nicht der Guard, sondern die Bauart der verbleibenden Gleichheit: sie vergleicht ein **unveränderliches** Recordfeld mit einem **veränderlichen** Mirrorfeld. Sie trägt heute allein deshalb, weil jede Änderung von `unit.open_findings` mit einer neuen Recordrevision zusammenfällt — nachweisbar an den drei einzigen Zuweisungsstellen (`src/workflow_state.py:1509, 2028, 2057`), die jeweils eine neue Work Unit oder eine neue Runde erzeugen. Bestätigt wird das durch den realen Lauf: die abgeschlossene Work Unit 2 trägt bis heute `open_findings=('C-02','C-03')`, obwohl beide Findings geschlossen sind — der Abschluss räumt das Feld bewusst **nicht** auf, und genau deshalb passt der Record weiterhin.

Realistische Bruchbedingung: Sobald eine Änderung `unit.open_findings` der importgebundenen ersten Implementierungs-Work-Unit anfasst, ohne eine neue Recordrevision zu erzeugen — etwa ein naheliegendes „Aufräumen" geschlossener Findings beim Abschluss einer Unit, eine Neuberechnung beim Wiederaufsetzen nach Nutzerentscheidung, oder eine Übernahme geschlossener Findings in den Abschlusszustand —, entsteht sofort wieder eine deterministische `MIRROR-AMBIGUOUS`-Schleife. Diesmal auf der importgebundenen Unit, wo der neue Guard gerade **nicht** greift, und mit demselben Poison-Ausgang nach drei Versuchen. Die dauerhafte Absicherung wäre, auch diese Gleichheit gegen die aus der Kette abgeleiteten Fakten zu führen, so wie es `src/artifact_replay.py:511-531` seit `924a554` bereits tut, statt gegen den lebenden Mirror.

## 6. Entscheidung

Der Hotfix behebt exakt den Defekt, den er behauptet, mit dem kleinstmöglichen Eingriff, an der strukturell richtigen Stelle, ohne Autoritätsverlust in der Findinglinie und ohne manuelle Eingriffe in State, Checkpoints oder Records. Der reale Lauf ist vollständig erhalten und unter diesem Commit nachweislich wiederaufnahmefähig. Ein ausführbarer Defekt ist nicht feststellbar; die vier Reviewnotizen betreffen Dokumentation, Testabdeckung und Restrisiko.

REVIEWER: claude
REVIEW_EVIDENCE: Geprüft wurden (a) der vollständige Delta-Umfang `6dbd03c..745a2fa` mit genau einer Produktivzeile und ohne Kollateraländerung; (b) die Kopplung von `finding_import_record_id` und `open_finding_ids` im Writer `src/orchestrator.py:420-436`, die den Guard strukturell und nicht bloß symptomatisch macht; (c) die Falsifikationskraft beider neuer Tests durch gezielte Mutation des Exports — ohne Guard fällt der positive Test mit exakt der Produktionsdiagnose, ohne die gesamte Prüfung fällt der negative Test, wobei derselbe Drift weiterhin von der Findingledger-Spiegelung `src/artifact_migration.py:375-412` gefangen wird; (d) eine eigene Probe in Produktionsform mit vorhandenem Finding-Import plus carried `C-04`, grün unter `745a2fa` und rot im Vorzustand; (e) `73 passed` für `tests/test_artifact_migration.py` und `tests/test_artifact_replay.py` im isolierten `git archive`-Export; (f) der reale Lauf `watch-20260829-102810.560490Z-c16fdae858d0` read-only über Kopien: 37 Records, Slice 01 mit Commit-Binding `6dbd03c`, Attestierung, Claude-Freigabe, `C-01/C-02/C-03` geschlossen, `C-04` offen, Work Unit 3 vorhanden, seit dem Poison keine weiteren Records, und `resolve_resume_state` löst unter `745a2fa` sauber auf; (g) die Wiederherstellbarkeit der Poison-Aufgabe über Vertragsdigestgleichheit mit `state.task_digest`; (h) die angrenzenden Pfade Denial-Runde, Correction-Unit, Final-Review-Korrektur, geschlossene Findings, mehrere Observations und importfreie Läufe. Größtes Restrisiko ist die verbleibende Gleichheit zwischen unveränderlichem Record und veränderlichem Mirrorfeld, die nur trägt, weil alle drei Zuweisungsstellen von `unit.open_findings` zwingend eine neue Recordrevision erzeugen; die Correction-Variante derselben Prüfung besitzt weiterhin keinen sichtbaren Guard und ist allein durch eine Writer-Konvention abgesichert.
PRE_MORTEM: Der wahrscheinlichste Schaden entsteht nicht im Code, sondern bei der Wiederaufnahme: der Lauf trifft unmittelbar nach der Codex-Implementierung von Slice 02 auf ein fingerprintgebundenes `UNEXPECTED-PATH`-Gate für `src/artifact_migration.py`, `tests/test_artifact_migration.py`, das Hotfixdokument und dieses Reviewdokument; wird das als Abbruch gelesen und mit einem neuen Watchlauf oder einem State-Eingriff beantwortet, gehen die vier Records der Slice-02-Vorbereitung und die bereits geleistete Arbeit verloren. Die zweite Variante: die Aufgabe wird nach dem direkten `--resume` in der Inbox liegen gelassen und der Watcher gestartet — ohne Sidecar erzwingt er einen neuen Lauf, überschreibt `.orchestrator/state.json` und lässt Codex und Claude die bereits freigegebene Slice 01 erneut bearbeiten. Die dritte, technisch gefährlichste Variante: eine spätere, gut gemeinte Aufräumung, die geschlossene Findings aus `unit.open_findings` der importgebundenen Work Unit entfernt, ohne eine neue Recordrevision zu schreiben, und damit dieselbe Poison-Schleife an der einen Stelle wiederherstellt, an der der neue Guard nicht greift.
FINAL_APPROVAL: YES
STATUS: DONE
