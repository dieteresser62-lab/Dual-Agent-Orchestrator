# S4c — Implementierungsauftrag: Treiberoberfläche vollständig und fail-closed deklarieren

Auftrag an: Codex
Review durch: Claude (read-only)
Branch: `feature/state-authority-consolidation`, Vorgänger: `187b4b8` (S4b)
Übergeordneter Plan: `00-stabilisierung-arbeitsplan.md`
Herkunft: Backlogaufgabe `04`, in den Plan gezogen

**Ohne Orchestrator.** Testsuite unter WSL: `python3 -m pytest tests/ -v`.
Baseline nach S4b (`187b4b8`): **1368 passed in 199 s**.

## Lage

`src/workflow.py` löst neun Treiberfähigkeiten dynamisch über
`getattr(..., None)` auf. Der Cutover hat daran nichts geändert:

| Stelle | Kante |
|---|---|
| `:874` | `sink = getattr(self.driver, method_name, None)` — die generische Recordsenke |
| `:1232`, `:1657` | `recover_pending_native_codex` |
| `:2144`, `:2181`, `:2204` | `active_state` |
| `:3087` | `bind_work_unit` |
| `:3102` | `authoritative_native_findings` |
| `:3152` | `carry_forward_native_findings` |

## Warum das jetzt dringender ist als vor S4b

Vor dem Cutover hätte eine still ausgefallene Pflichtsenke eine sichtbare Spur
hinterlassen: Der Record wäre nicht geschrieben worden, der Mirror aber schon —
und der Vergleich hätte es gemeldet. Genau dafür gab es zwanzig
`differs from state-v3`-Stellen.

**Diese zwanzig Stellen sind jetzt null.** Der Mirror wird aus den Records
abgeleitet. Fällt eine Senke still aus, fehlt der Record — und der daraus
projizierte State ist widerspruchsfrei, nur falsch. Es gibt nichts mehr, was
vergleichen könnte.

Der Cutover hat also das Sicherheitsnetz entfernt, das diesen Defekt bisher
aufgefangen hätte. Das ist kein Fehler von S4b, sondern der Grund, warum S4c
unmittelbar folgt.

**Und S5 fände es nicht:** Crash-Injection prüft, ob jeder Resume zum selben
kanonischen Zustand konvergiert. Zwei Läufe mit derselben fehlenden Senke
konvergieren zum selben falschen Zustand. S5 wäre grün, das Loch bliebe.

## Ziel

Jede von `WorkflowEngine` und der Produktionsschleife benötigte
Treiberfähigkeit ist statisch und vollständig in einem typisierten Protokoll
beziehungsweise in klar abgegrenzten Teilprotokollen deklariert. Verpflichtende
Persistenz-, Recovery- und Authority-Kanten werden direkt aufgerufen und können
nicht wegen eines fehlenden Attributs still ausfallen.

Wirklich optionale Fähigkeiten werden als bewusste, typisierte Capability mit
expliziter Fallbacksemantik modelliert. `getattr(..., None)` vertritt keine
Sicherheits- oder Persistenzentscheidung mehr.

## Anforderungen

1. Alle von `WorkflowEngine` und der Produktionsschleife erreichten
   Treibermethoden inventarisieren und gegen `WorkflowDriver` abgleichen.
2. Mandatory, optional und nur-orchestratorintern fachlich trennen.
3. Native Codex-/Claude-Persistenz, Validierungsrecords, Gateentscheidungen,
   Work-Unit-Bindung, Finding-Replay und Record-ahead-Recovery fail-closed
   binden.
4. Test-, Dry-Run- und Scripted-Treiber erfüllen denselben expliziten Vertrag
   oder ein bewusst kleineres, separat benanntes Protokoll.
5. Ein fehlender Pflichtsink wird **vor** dem betroffenen Provideraufruf oder
   Zustandsübergang mit typisierter Diagnose abgewiesen.
6. Keine Änderung an Recordsemantik, Reviewhoheit oder Workflowreihenfolge.

## Der Maßstab steht seit S4b

S4b hat gezeigt, wie ein statischer Nachweis aussieht:
`test_structured_decision_paths_do_not_read_the_state_cache` parst den Quelltext
per `ast`, sammelt **jeden** Attributzugriff auf das Stateobjekt und sichert die
vollständige Menge zu. Kein Beispieltest, keine Textsuche.

Dieselbe Strenge ist hier zu erreichen: Die Inventur der Treiberfähigkeiten ist
aus dem AST zu gewinnen und gegen die Protokolldeklaration zu prüfen, sodass
eine künftig hinzukommende undeklarierte Fähigkeit den Test rot macht.

## Providerfreie Akzeptanzfälle

- Eine statische Inventur beweist, dass jede produktiv aufgerufene
  Treiberfähigkeit deklariert ist — und wird rot, sobald eine undeklarierte
  hinzukommt.
- Eine unvollständige Fake-Treiberimplementierung fällt deterministisch beim
  Aufbau oder ersten gebundenen Gebrauch, nicht still weiter.
- Mutationsproben gegen die Namen verpflichtender Persistenz- und
  Recoverykanten werden rot.
- Plan-, Slice-, Korrektur-, Finalreview- und Resumepfade bleiben record- und
  zustandsidentisch.
- Die S4b-Nachweise bleiben gültig, insbesondere der AST-Nachweis über die
  Lesestellen des Caches.
- Kein Test benötigt Provider, Netz oder API.

## Abnahme

- Kein `getattr(..., None)` vertritt mehr eine Persistenz-, Recovery- oder
  Authority-Entscheidung.
- Optionale Fähigkeiten sind typisiert mit benannter Fallbacksemantik.
- Die Inventur ist statisch abgesichert, nicht als Momentaufnahme.
- Keine Protokoll-, Schema- oder Registerversion angehoben.
- Die Providernamen-Baseline ist unverändert oder gesunken.
- Suite grün gegen die Baseline nach S4b: 1368 passed. Laufzeit festhalten.

## Nicht-Ziele

- Keine Zerlegung großer Module — das ist Backlog `12`, nach dem Merge.
- Keine Umbenennung von `ProductionWorkflowDriver`.
- Keine neue Provider- oder Reviewerrolle.
- Keine Lockerung optionaler Pfade durch allgemeine No-op-Implementierungen.

## Stopbedingungen

- Anhalten, falls eine heute optionale Methode absichtlich mehrere
  Produktionsimplementierungen mit unterschiedlicher Semantik bedient und diese
  Produktentscheidung nicht aus dem Repository ableitbar ist.
- Anhalten, falls die vollständige Deklaration eine Protokollmigration
  persistierter Runs verlangen würde.
- Anhalten, falls eine Kante weder eindeutig verpflichtend noch eindeutig
  optional ist — das ist ein Befund, keine Ermessensfrage.

## Reviewfokus (Claude)

- Ob als optional deklarierte Kanten in Wahrheit verpflichtend sind. `active_state`
  an drei Stellen ist der erste Kandidat.
- Ob eine No-op-Implementierung einen fehlenden Sink verdeckt.
- Ob die Inventur statisch ist und bei einer neuen undeklarierten Fähigkeit rot
  wird, oder ob sie nur den heutigen Stand abbildet.
- Ob die generische Recordsenke bei `:874` weiterhin dynamisch aufgelöst wird —
  sie ist die wichtigste der neun.
- Ob die S4b-Nachweise unberührt geblieben sind.
