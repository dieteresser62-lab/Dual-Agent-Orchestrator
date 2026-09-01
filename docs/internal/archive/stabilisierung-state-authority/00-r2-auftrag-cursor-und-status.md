# R2 — Implementierungsauftrag: Cursor, Work-Unit- und Slice-Status

Auftrag an: Codex
Review durch: Claude (read-only)
Branch: `feature/state-authority-consolidation`, Vorgänger: `2981322` (R1)
Übergeordneter Plan: `00-stabilisierung-arbeitsplan.md`
Grundlage: `docs/internal/stabilisierung-s2-uebergangsmatrix.md`, Bündel 2 des
S4a-Schnittvorschlags

**Ohne Orchestrator.** Testsuite unter WSL: `python3 -m pytest tests/ -v`.
Baseline nach R1 (`2981322`): **1236 passed**.

## Zwei geänderte Rahmenbedingungen

Beide gelten ab sofort für alle Recordslices und weichen von R1 ab.

**1. Keine Rückwärtskompatibilität.** Das Projekt ist in Entwicklung, es gibt
keinerlei Änderungsgrenzen, und alte Ketten sind irrelevant. Eine Kette ohne
die neuen Records wird **fail-closed abgewiesen**, nicht lesbar gehalten. Der
Mechanismus existiert: `legacy-state-v3` und `structured-v1` werden mit
`UNSUPPORTED-PROTOCOL` abgewiesen; Vor-R2-Ketten reihen sich ein.

R1 hält seine Records noch optional. Das wird in einem eigenen Zwischenlauf
nachgezogen und ist hier nicht zu reparieren.

**2. Die Providernamen-Ratsche wird nicht angehoben.** R1 hat
`tests/fixtures/provider-name-coupling-baseline-v1.json` um codex +20 /
claude +20 erhöht, weil `RunProfilePayload` Providernamen in Feldnamen trägt.
Der Guard vergleicht gegen genau diese Datei — sie anzuheben erfüllt die
Ratsche nicht, sondern umgeht sie.

Für R2 gilt deshalb: **Recordfelder werden rollenbasiert benannt, nicht
providerbasiert.** Konkret betrifft das die beiden Zählerfakten unten. Die
Abbildung auf die heutigen Mirrornamen ist eine benannte Projektion und
verschwindet mit S4b. Lässt sich ein Anstieg trotzdem nicht vermeiden, ist er
zu begründen und vorzulegen — nicht durch Anheben der Baseline zu erledigen.

## Umfang — acht Fakten der Gruppe A

| Statefeld | Zielrecord und Feld |
|---|---|
| `current_slice_id` | `WorkflowTransitionPayload.slice_id` |
| `current_work_unit_id` | `WorkflowTransitionPayload.work_unit_id` |
| `current_step` | `WorkflowTransitionPayload.step` |
| `slices[*].status` | `WorkflowTransitionPayload.slice_status` |
| `work_units[*].status` | `WorkflowTransitionPayload.work_unit_status` |
| `work_units[*].current_step` | `WorkflowTransitionPayload.step` |
| `work_units[*].codex_return_count` | `WorkflowPolicyPayload`, rollenbasiert benannt |
| `work_units[*].max_codex_returns` | `WorkflowPolicyPayload`, rollenbasiert benannt |

Schreiber laut Sortierung: `WorkflowEngine` an jeder Dispatch-, Gate-, Resume-
und Completionkante; die Policyfelder bei Work-Unit-Initialisierung,
`record_review_denial()` und der Iteration-limit-Fortsetzung.

## Zwei Stellen, die eine ausdrückliche Entscheidung brauchen

**`current_step` und `work_units[*].current_step` zeigen in der Matrix auf
dasselbe Feld.** Entweder sind es zwei Fakten, dann brauchen sie zwei Felder;
oder der globale Cursor ist die Projektion des Steps der aktuellen Work-Unit,
dann ist das zu benennen und nur einmal zu recorden. Beides ist vertretbar,
stillschweigend zusammenfallen zu lassen ist es nicht.

**Der Rücklaufzähler ist nicht aus denied Reviews ableitbar.** Die Matrix
begründet das: Die Iteration-limit-Fortsetzung erhöht die Obergrenze, ohne den
Zähler mitzuerhöhen. Er braucht deshalb einen eigenen Record und darf nicht
aus der Reviewhistorie rekonstruiert werden.

## Ausdrücklich nicht recorden

`work_units[*].reviewer` ist in der Sortierung **Gruppe C** und aus dem Präfix
ableitbar: vor dem ersten Review `None`, danach die Rolle des ersten
beziehungsweise letzten denied `ReviewPayload` derselben Work-Unit, wobei
Schema 2 dafür ausschließlich `Role.CLAUDE` zulässt. Kein eigener Record. Wird
der Wert im Code gebraucht, ist er als benannte Projektion zu implementieren.

## Anforderungen

1. Beide Payloads additiv in Schema 2 ergänzen; `schema_version` bleibt `2`.
2. Jeder Übergang schreibt seinen Record, **bevor** der nächste Dispatch ihn
   liest — wie in R1 mit einem Ordnungsnachweis, nicht mit einem Existenztest.
3. Der Mirror wird unverändert weiter geschrieben. **Kein Cutover.**
4. Ein Replay des Präfixes rekonstruiert Cursor und beide Statusarten ohne
   Zugriff auf `state.json`, Checkpoint oder Dateisystem.
5. Eine Kette ohne die neuen Records wird fail-closed abgewiesen.
6. Neue Record-/Mirror-Vergleiche werden in der Matrix inventarisiert und im
   Vollständigkeitstest gezählt. Die Zusicherung steht nach R1 bei 14.
7. Die acht STOP-Einträge werden in der Matrix auf gedeckt fortgeschrieben.

## Providerfreie Akzeptanzfälle

- Ein Lauf über mehrere Slices und Runden erzeugt je Übergang genau einen
  Transitionrecord; ein Test beweist die Reihenfolge gegen den Dispatch.
- Ein Replay liefert Cursor, Slice- und Work-Unit-Status byteidentisch zum
  Mirror.
- Ein denied Review erhöht den Rücklaufzähler; eine Iteration-limit-
  Fortsetzung erhöht die Obergrenze **ohne** den Zähler. Beide Wege sind
  getrennt belegt.
- Eine Kette ohne Transition- oder Policyrecords wird abgewiesen, nicht
  stillschweigend akzeptiert.
- `work_units[*].reviewer` hat keinen Record und wird korrekt projiziert,
  einschließlich des `None`-Falls vor dem ersten Review.
- Der Vollständigkeitstest der Matrix bleibt grün.

## Abnahme

- Alle acht Fakten sind aus dem Recordpräfix allein belegbar.
- Die Doppeldeutigkeit bei `current_step` ist entschieden und begründet.
- `work_units[*].reviewer` hat keinen Record bekommen.
- Die Providernamen-Baseline ist **unverändert**.
- Keine Protokoll-, Schema- oder Registerversion angehoben.
- Keine neuen `_recoverable_*`-Sonderfälle.
- Suite grün gegen die Baseline nach R1: 1236 passed.

## Nicht-Ziele

- Kein Cutover — das bleibt S4b.
- Kein Side-effect-Ledger — das ist Bündel 4 und baut hierauf auf.
- Keine Reparatur der Kompatibilitätsklauseln aus R1 — eigener Zwischenlauf.
- Keine Änderung an Reviewhoheit, Findingnamensraum oder Freigabelogik.

## Stopbedingungen

- Anhalten, wenn ein Übergangsrecord nicht vor seinem Leser geschrieben werden
  kann, ohne die Dispatchreihenfolge zu ändern.
- Anhalten, wenn die Providernamen-Ratsche nur durch Anheben der Baseline grün
  zu halten wäre.
- Anhalten, wenn sich `current_step` weder eindeutig als ein Fakt noch als zwei
  Fakten begründen lässt.

## Reviewfokus (Claude)

- Der Ordnungsnachweis je Übergangsart, nicht nur für den ersten Dispatch.
- Ob der Rücklaufzähler wirklich einen eigenen Record hat und nicht doch aus
  denied Reviews gezählt wird.
- Ob `work_units[*].reviewer` versehentlich einen Record bekommen hat.
- Ob die Baseline der Providernamen-Ratsche unverändert ist.
- Ob die Abweisung alter Ketten wirklich fail-closed ist und nicht nur eine
  Warnung.
- Verdeckte Cutover-Anteile: eine Stelle, die den Cursor bereits aus dem Record
  statt aus dem Mirror liest.
