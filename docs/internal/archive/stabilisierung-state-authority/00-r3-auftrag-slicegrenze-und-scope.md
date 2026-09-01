# R3 — Implementierungsauftrag: Slice-Startgrenze und Scopefingerprint

Auftrag an: Codex
Review durch: Claude (read-only)
Branch: `feature/state-authority-consolidation`, Vorgänger: der R2-Commit
Übergeordneter Plan: `00-stabilisierung-arbeitsplan.md`
Grundlage: `docs/internal/stabilisierung-s2-uebergangsmatrix.md`, Bündel 3 des
S4a-Schnittvorschlags

**Ohne Orchestrator.** Testsuite unter WSL: `python3 -m pytest tests/ -v`.
Baseline nach R2 (`30b51cf`): **1245 passed**.

## Stehende Regeln ab R2

Unverändert gültig, hier nicht erneut zu begründen:

1. **Keine Rückwärtskompatibilität.** Eine Kette ohne die neuen Records wird
   fail-closed abgewiesen, nicht lesbar gehalten.
2. **Die Providernamen-Ratsche wird nicht angehoben.** Recordfelder werden
   rollenbasiert benannt. R2 hat das mit `implementer_return_count` und
   `max_implementer_returns` vorgemacht; die Baselinedatei blieb unverändert.
3. **Kein Cutover.** Der Mirror wird weiter geschrieben.

## Umfang — drei Fakten der Gruppe A

| Statefeld | Zielrecord und Feld |
|---|---|
| `slices[*].start_commit` | `SliceBoundaryPayload.start_commit` |
| `slices[*].scope_change_groups` | `SliceBoundaryPayload.scope_change_groups` |
| `slices[*].start_fingerprint` | `SliceBoundaryPayload.start_fingerprint` |

Schreiber laut Sortierung: `begin_slice()` beziehungsweise die
Sliceinitialisierung — **vor der Scopeprüfung und vor jedem Side Effect**. Der
Startfingerprint entsteht aus einer Repositorymessung beim Binden der
Slice-Git-Grenze; Change-, Correction- und Resumeprüfung lesen ihn.

## Zwei Stellen, an denen dieser Slice scheitern kann

**Der Startfingerprint muss der gemessene Wert zum Bindezeitpunkt sein.** Wird
er beim Lesen neu berechnet, liefert er nach der ersten Dateiänderung einen
anderen Wert. Ein solcher Fehler fällt in Tests nicht auf, die messen und
sofort wieder lesen — er zerstört aber genau die Resumesemantik, für die der
Fingerprint existiert. Der Nachweis gehört deshalb in einen Test, der zwischen
Messung und Lesen den Arbeitsbaum verändert.

**`scope_change_groups` ist nicht aus der flachen Pfadmenge rekonstruierbar.**
Die Matrix stellt das ausdrücklich fest: Die Gruppen sind Eingabe der
Scopevalidation. Wird die Gruppenstruktur beim Recorden flachgeklopft und beim
Lesen wieder geraten, ist der Fakt verloren, obwohl alle Pfade noch da sind.
Die Struktur ist zu erhalten, nicht zu rekonstruieren.

## Anforderungen

1. `SliceBoundaryPayload` additiv in Schema 2; `schema_version` bleibt `2`.
2. Ein Record je Slice-Startgrenze, geschrieben vor Scopeprüfung und vor jedem
   Side Effect. Ordnungsnachweis wie in R1 und R2 — gegen den tatsächlichen
   Leser, nicht als Existenztest.
3. Ein Replay des Präfixes rekonstruiert alle drei Fakten einschließlich der
   Gruppenstruktur, ohne Zugriff auf `state.json`, Checkpoint oder
   Dateisystem.
4. Eine Kette ohne die neuen Records wird fail-closed abgewiesen.
5. Neue Record-/Mirror-Vergleiche werden in der Matrix inventarisiert und im
   Vollständigkeitstest gezählt.
6. Die drei STOP-Einträge werden in der Matrix auf gedeckt fortgeschrieben.

## Ausdrücklich zu entscheiden

R2 hat `WORKFLOW_TRANSITION` und `WORKFLOW_POLICY` in die
Fingerprint-Ausschlussmenge des Final-Review-Preflights aufgenommen
(`src/final_review_preflight.py`, `_TRANSITION_FINGERPRINT_EXCLUDED_TYPES`),
begründet damit, dass Cursor- und Policyfakten sich bei jedem Halt und Resume
ändern und die deterministische Identität des Preflights sonst zerfällt.

Für `SliceBoundaryPayload` ist diese Frage neu zu beantworten und **nicht per
Analogie zu übernehmen**. Die Slicegrenze ändert sich gerade nicht bei jedem
Resume — sie ist genau die Konstante, gegen die geprüft wird. Wenn sie
ausgeschlossen wird, ist zu begründen wodurch der Ausschluss kompensiert ist.

## Providerfreie Akzeptanzfälle

- Ein Lauf über mehrere Slices erzeugt je Startgrenze genau einen Record vor
  der Scopeprüfung; ein Test beweist die Reihenfolge gegen den Leser.
- Zwischen Messung und Lesen wird der Arbeitsbaum verändert; der recordete
  Startfingerprint bleibt der Wert vom Bindezeitpunkt.
- Ein Replay liefert `scope_change_groups` mit identischer Gruppenstruktur,
  nicht nur mit identischer Pfadmenge.
- Zwei Slices mit derselben Pfadmenge, aber unterschiedlicher Gruppierung
  bleiben unterscheidbar.
- Eine Kette ohne Slicegrenzenrecord wird abgewiesen.
- Der Vollständigkeitstest der Matrix bleibt grün.

## Abnahme

- Alle drei Fakten sind aus dem Recordpräfix allein belegbar.
- Der Startfingerprint ist nachweislich gemessen und nicht nachgerechnet.
- Die Gruppenstruktur überlebt Schreiben und Lesen verlustfrei.
- Die Preflight-Ausschlussfrage ist entschieden und begründet.
- Die Providernamen-Baseline ist unverändert.
- Keine Protokoll-, Schema- oder Registerversion angehoben.
- Keine neuen `_recoverable_*`-Sonderfälle.
- Suite grün gegen die Baseline nach R2: 1245 passed.

## Nicht-Ziele

- Kein Side-effect-Ledger — Bündel 4 baut hierauf auf.
- Keine Gate-Records — Bündel 5.
- Kein Cutover, keine Reparatur der R1-Nachzüge.

## Stopbedingungen

- Anhalten, wenn der Startfingerprint nicht vor dem ersten Side Effect
  gemessen werden kann, ohne die Startreihenfolge zu ändern.
- Anhalten, wenn die Gruppenstruktur nur verlustbehaftet serialisierbar wäre.
- Anhalten, wenn die Ratsche nur durch Anheben der Baseline grün bliebe.

## Reviewfokus (Claude)

- Ob der Startfingerprint wirklich der gemessene Wert ist. Ein Test, der misst
  und sofort liest, beweist das nicht.
- Ob `scope_change_groups` die Gruppen erhält oder flachgeklopft und beim Lesen
  geraten wird.
- Ob der Record vor Scopeprüfung und Side Effect liegt, nicht nur vor dem
  Commit.
- Ob die Preflight-Ausschlussfrage entschieden oder aus R2 kopiert wurde.
- Verdeckte Cutover-Anteile: eine Stelle, die die Slicegrenze bereits aus dem
  Record statt aus dem Mirror liest.
