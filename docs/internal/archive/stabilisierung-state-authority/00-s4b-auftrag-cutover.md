# S4b — Implementierungsauftrag: Cutover, der Mirror wird abgeleitet

Auftrag an: Codex
Review durch: Claude (read-only)
Branch: `feature/state-authority-consolidation`, Vorgänger: `7339f7a` (Zwischenlauf)
Übergeordneter Plan: `00-stabilisierung-arbeitsplan.md`
Grundlage: `docs/internal/stabilisierung-s2-uebergangsmatrix.md`

**Ohne Orchestrator.** Testsuite unter WSL: `python3 -m pytest tests/ -v`.
Baseline nach dem Zwischenlauf (`7339f7a`): **1348 passed in 210 s**.

## Das ist der Slice, für den der Plan existiert

Der Ausgangsbefund steht wörtlich im Modulkopf von `src/artifact_bridge.py`:

> State-v3 remains authoritative until the explicit cutover.

Und in `CLAUDE.md` steht seit jeher das Gegenteil — die Recordkette sei die
technische Quelle. Der Cutover wurde begonnen, vertraglich vorausgesetzt und
technisch nie abgeschlossen. Dieser Slice schließt ihn.

## Die Vorbedingung ist erfüllt und bewiesen

R9 hat gezeigt, was S4b braucht:
`test_multi_slice_correction_gate_halt_resume_projects_every_accepted_prefix`
prüft über eine 44-Record-Journey mit mehreren Slices, Korrektur, Gate, Halt und
Resume **jedes** Präfix. Jedes wird entweder deterministisch projiziert —
zweimal, auf Gleichheit — oder mit genau einem benannten Grund abgewiesen. Die
Projektion deckt jedes Feld von `WorkflowState` ab.

Die Reconciliation der Crashfenster um Commit, Providerstart, Dateischreibung
und Queuebewegung liegt seit R4 vor, dreiwertig und fail-closed.

Es ist also nichts mehr zu bauen. Es ist umzuschalten.

## Messbares Ziel

Die Altmechanik ist während der R-Serie **gewachsen**, weil jeder neue
Recordtyp einen neuen Record-Mirror-Vergleich mitbrachte. Stand heute:

| | bei S2 | heute | nach S4b |
|---|---:|---:|---|
| `_recoverable_*`-Prädikate | 5 | 5 | **0** |
| `differs from state-v3` | 12 | 20 | **0 oder generisch** |
| `mismatch(...)` in `artifact_migration.py` | 32 | 44 | **stark reduziert** |

Diese Zahlen sind die Abnahme. Sie sind vor und nach dem Slice zu erheben und
festzuhalten. Ein Rest ist zulässig, aber jeder verbliebene Eintrag ist
einzeln zu begründen — als Record-interne Kausalität, unveränderliche
Providerbindung oder irreversibler externer Side Effect, gemäß der Kategorie
„bleibt bewusst" aus der S2-Matrix.

## Was der Mirror danach ist

`state.json` wird deterministisch aus dem Recordpräfix erzeugt und nicht mehr
unabhängig fortgeschrieben. Er darf als Betriebs-Snapshot bleiben, dann aber:

- gebunden an Record-Head, Reducer-Version und Projektdigest,
- jederzeit aus den Records rekonstruierbar,
- ohne jede Autorität.

Fehlt er, wird er projiziert. Weicht er ab, wird er verworfen und neu
projiziert — **nicht** der Record korrigiert. Das ist derselbe Maßstab, den RP
für den Appendindex und R8 für die Blobs gesetzt haben.

## Die eine Gefahr

Dies ist der Moment, in dem ein Cache Autorität behalten kann. Der Mirror war
zwanzig Slices lang die Wahrheit; jede Lesestelle, die ihn weiterhin bevorzugt,
macht den Cutover kosmetisch.

**Zu zeigen ist deshalb nicht, dass die Projektion existiert — das hat R9 schon
gezeigt —, sondern dass keine Entscheidung mehr den Mirror liest.** Ein
statischer Nachweis über die Lesestellen ist einem Beispieltest vorzuziehen.

## Versionsentscheidung

Der Plan verlangt sie ausdrücklich hier. Sie ist durch zwei Festlegungen
vorbereitet:

- **Keine Rückwärtskompatibilität.** Alte Ketten sind irrelevant und werden
  fail-closed abgewiesen; der Mechanismus existiert als `UNSUPPORTED-PROTOCOL`.
- Alle acht Recordbündel liefen additiv bei `schema_version = 2`.

Ändert der Cutover die Bedeutung bestehender Records oder des Reducers, wird
eine neue explizite Semantik- beziehungsweise Protokollversion eingeführt.
**Diese Entscheidung ist zu treffen und zu begründen, nicht zu umgehen.** Wird
sie verneint, ist zu zeigen, dass keine Recordbedeutung sich ändert.

Die gebundene Reducer-Version gehört an den Lauf; ein Lauf mit fremder
Reducer-Semantik wird abgewiesen. Der Nachweis dafür ist S5, hier genügt die
Bindung.

## Anforderungen

1. `state.json` entsteht ausschließlich als Projektion des Recordpräfixes.
2. Der Modulkopf von `src/artifact_bridge.py` beschreibt den tatsächlichen
   Zustand. Der zitierte Satz verschwindet.
3. Die spezialisierten Mirrorvergleiche entfallen oder werden durch **einen**
   generischen Cacheintegritätsabgleich ersetzt: Cache fehlt → projizieren;
   Digest weicht ab → verwerfen und projizieren.
4. Resume bleibt fail-closed. Eine unvollständige, unbekannte oder
   widersprüchliche Kette startet nichts.
5. Identische Szenarien erzeugen dieselben kanonischen Zustände wie vor dem
   Umbau.
6. Die Matrix wird fortgeschrieben: Je S2-Kante ist vermerkt, ob sie entfallen
   ist, generisch wurde oder bewusst bleibt.

## Providerfreie Akzeptanzfälle

- Ein gelöschter, veralteter oder manipulierter `state.json` führt zu einer
  Neuprojektion mit identischem Ergebnis — nie zu einer Korrektur der Records.
- Ein statischer Nachweis zeigt, dass keine Entscheidungslogik den Mirror als
  Quelle liest.
- Die R9-Präfixprojektion bleibt vollständig gültig.
- Journey-, Replay- und Resumetests erzeugen vor und nach dem Umbau dieselben
  kanonischen Zustände und Recordfolgen.
- Mutationsproben gegen die verbliebenen Prüfungen werden rot.
- Eine Kette mit fremder Reducer-Version wird abgewiesen.

## Abnahme

- `state.json` ist abgeleitet, nicht fortgeschrieben.
- Die drei Zähler sind erhoben und festgehalten; jeder Rest ist einzeln
  begründet.
- Der Modulkopf sagt die Wahrheit.
- Die Versionsentscheidung ist getroffen und begründet.
- Keine neuen `_recoverable_*`-Sonderfälle; die alten sind fort oder begründet.
- Suite grün gegen die Baseline nach dem Zwischenlauf: 1348 passed.

## Nicht-Ziele

- Keine Treiberoberfläche — S4c.
- Keine Crash-Injection — S5. Hier genügen die vorhandenen Reconciliationtests.
- Kein Vertragsabgleich der Rootdokumente — S6. Nur der Modulkopf, weil er der
  Ausgangsbefund ist.
- Keine Strukturzerlegung — `12`, nach dem Merge.

## Stopbedingungen

- Anhalten, wenn eine Entscheidung den Mirror weiterhin braucht. Dann fehlt ein
  Record, und das ist ein Befund für einen eigenen Slice, keine Ausnahme.
- Anhalten, wenn ein `_recoverable_*`-Sonderfall nur durch Umbenennen
  verschwände.
- Anhalten, wenn die Versionsfrage nur durch Vermeiden beantwortbar wäre.
- Anhalten, wenn fail-closed irgendwo aufgeweicht werden müsste, damit eine
  Divergenz verschwindet.

## Reviewfokus (Claude)

- Ob wirklich nur noch abgeleitet wird, oder ob eine Lesestelle den Mirror
  bevorzugt. Das ist der Kern; alles andere ist nachrangig.
- Ob ein Fakt ohne Record stillschweigend verlorengeht.
- Ob die drei Zähler tatsächlich gefallen sind und die Reste einzeln begründet
  wurden — oder ob ein Sonderfall nur den Namen gewechselt hat.
- Ob fail-closed irgendwo aufgeweicht wurde, um eine Divergenz zu beseitigen.
- Ob die Versionsentscheidung begründet oder umgangen wurde.
- Ob die R9-Präfixprojektion unverändert gültig ist.
