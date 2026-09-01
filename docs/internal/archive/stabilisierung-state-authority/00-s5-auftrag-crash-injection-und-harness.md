# S5 — Implementierungsauftrag: Crash-Injection, Semantikbindung und Betriebsharness

Auftrag an: Codex
Review durch: Claude (read-only)
Branch: `feature/state-authority-consolidation`, Vorgänger: `da81763` (S4c)
Übergeordneter Plan: `00-stabilisierung-arbeitsplan.md`
Enthält den providerfreien Teil von Backlogaufgabe `11`

**Ohne Orchestrator.** Testsuite unter WSL: `python3 -m pytest tests/ -v`.
Baseline nach S4c (`da81763`): **1393 passed in 198 s**.

## Was dieser Slice beweisen soll

Alle bisherigen Slices haben Eigenschaften über **Records** gezeigt. Dieser
zeigt zum ersten Mal eine Eigenschaft über **Unterbrechung**: dass kein
normaler Crashpunkt einen übergangsspezifischen Sonderfall braucht.

Das ist die Gegenprobe zum Cutover. S4b hat die `_recoverable_*`-Prädikate von
fünf auf null gebracht — S5 muss zeigen, dass sie wirklich entbehrlich waren
und nicht bloß entfernt.

## Vorbedingungen, die bereits vorliegen

- **R4** liefert die Reconciliation der vier Side-effect-Klassen, dreiwertig
  mit `UNKNOWN` als fail-closed Rückfall.
- **R9** zeigt die deterministische Projektion an jedem Präfix einer
  44-Record-Journey.
- **S4b** macht die Records zur einzigen Wahrheit; ein gelöschter Cache wird
  neu projiziert.
- **S4c** garantiert, dass keine Pflichtsenke still ausfallen kann.

Es fehlt der Nachweis, dass das unter **realer Unterbrechung** hält.

## Teil 1 — Crash-Injection

**Vorgehen.** Vor und nach jedem dauerhaften Schreib- und Side-Effect-Rand
deterministisch unterbrechen und den Resume prüfen.

Die Ränder sind bekannt und müssen nicht gesucht werden: Das Side-effect-Ledger
aus R4 benennt sie als `git_commit`, `provider_start`, `file_write`,
`queue_move`, `internal` und `ledger`, jeweils mit Intent- und Resultatphase.
Genau dazwischen liegt das Fenster.

**Abnahme.** Jeder Resume konvergiert zum selben kanonischen Zustand. Kein
`_recoverable_*`-Sonderfall wird wieder eingeführt — die Null aus S4b bleibt.
Keine doppelte Ausführung: kein zweiter Commit, kein zweiter Providerstart,
keine zweite Queuebewegung.

## Teil 2 — Semantikbindung

Ein Lauf kann nur mit seiner gebundenen Reducer-Semantik fortgesetzt werden
oder über eine explizit getestete Migration. S4b hat die Bindung geschaffen
(`RunProfilePayload.reducer_version`) und die Abweisung bereits getestet.

**Hier fehlt der Nachweis unter Unterbrechung:** Ein Lauf, der mitten in einer
Journey unterbrochen und mit fremder Reducer-Version fortgesetzt wird, muss
fail-closed abgewiesen werden — nicht erst beim nächsten Append, sondern bevor
irgendein Zustand entsteht.

## Teil 3 — Der Harness

Der providerfreie Teil von Backlog `11` geht hier auf. S5 erzeugt den Harness,
statt seine Szenarien einmalig und wegwerfbar zu bauen.

**Szenarioklassen:**

- kleine vollständige PLAN_ONLY-IMPLEMENT-Finalreview-Journey
- mehrere kohärente Slices mit Korrektur und geschlossenem Finding
- Neustart an Provider-, Validation-, Commit-, Handoff- und
  Queuefinalisierungsgrenzen
- Record-ahead-Wiederaufnahme ohne zweiten Providerstart oder Commit
- typisierte Fortsetzung nach Quota-, Netz- und Prozessfehler

**Ergebnisartefakt je Lauf:** Repositorycommit, Szenarioversion, Recordheads,
Aufruf- und Commitzählung, Endzustand. Kanonisch und wiederholbar.

**Instrumentierung beweist null Providerstarts** im Standardmodus.

## Der Langlauf

Ein providerfreier Langlauf durchläuft PLAN_ONLY, Handoff, denied Review,
Correction, carried Observation, Finalreview und Resume **ohne manuellen
Stateeingriff** — reproduzierbar aus dem Harness, nicht als Einzelbeobachtung.

Das ist das vorletzte Abschlusskriterium des gesamten Plans.

## Anforderungen

1. Die Injektionspunkte werden aus dem Side-effect-Ledger abgeleitet, nicht
   frei gewählt. Ein Rand ohne Test ist ein Befund.
2. Der Harness ist versioniert und wiederholbar; sein Ergebnisartefakt ist
   kanonisch.
3. Keine neuen `_recoverable_*`-Sonderfälle. Die Null aus S4b ist eine
   Abnahmebedingung, kein Zwischenstand.
4. Die Nachweise aus R9, S4b und S4c bleiben gültig; insbesondere die
   AST-Nachweise über Cachelesestellen und Treiberoberfläche.
5. Kein realer Provideraufruf. Der Canary aus `11` bleibt im Backlog.

## Providerfreie Akzeptanzfälle

- Für jede Side-effect-Klasse: Abbruch vor und nach dem Rand; der Resume
  konvergiert zum selben kanonischen Zustand.
- Zweimaliger Abbruch an derselben Stelle erzeugt keine doppelte Ausführung.
- Ein Lauf mit fremder Reducer-Version wird abgewiesen, bevor Zustand entsteht.
- Der Langlauf oben, aus dem Harness reproduziert.
- Instrumentierung beweist null Providerstarts.
- Wiederholung erzeugt dasselbe Ergebnisartefakt.

## Abnahme

- Kein Crashpunkt braucht einen Sonderfall; die Zähler bleiben bei null.
- Der Langlauf läuft ohne manuellen Stateeingriff durch.
- Der Harness ist versioniert, nicht ad hoc.
- Suite grün gegen die Baseline nach S4c: 1393 passed. Laufzeit festhalten.

## Nicht-Ziele

- Kein realer Canary — bleibt Backlog `11`, gesondert zu entscheiden.
- Kein Vertragsabgleich — S6.
- Keine Strukturzerlegung — Backlog `12`, nach dem Merge.

## Stopbedingungen

- Anhalten, wenn ein Crashpunkt ohne Sonderfall nicht konvergiert. Das ist der
  Befund, für den dieser Slice existiert — er gehört benannt, nicht durch einen
  wiedereingeführten `_recoverable_*` geheilt.
- Anhalten, wenn ein Rand aus dem Ledger nicht deterministisch unterbrechbar
  ist.

## Reviewfokus (Claude)

- Ob die Injektionspunkte die realen Ränder treffen oder nur die bequemen. Das
  Ledger aus R4 ist die Referenz, nicht die Testbarkeit.
- Ob ein Sonderfall unter neuem Namen zurückkehrt.
- Ob der Harness echte Kombinationen erzeugt oder längere Einzelfälle
  aneinanderreiht.
- Ob der Langlauf wirklich ohne manuellen Eingriff durchläuft oder ob ein Test
  zwischendurch State setzt.
- Ob die Instrumentierung null Providerstarts tatsächlich beweist.
