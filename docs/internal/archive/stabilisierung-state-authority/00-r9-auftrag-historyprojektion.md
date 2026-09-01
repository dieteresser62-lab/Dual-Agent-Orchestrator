# R9 — Implementierungsauftrag: Restliche Historyprojektion

Auftrag an: Codex
Review durch: Claude (read-only)
Branch: `feature/state-authority-consolidation`, Vorgänger: `ce94012` (R7)
Übergeordneter Plan: `00-stabilisierung-arbeitsplan.md`
Grundlage: `docs/internal/stabilisierung-s2-uebergangsmatrix.md`, Bündel 9 des
S4a-Schnittvorschlags

**Ohne Orchestrator.** Testsuite unter WSL: `python3 -m pytest tests/ -v`.
Baseline nach R7 (`ce94012`): **1333 passed in 177 s**.

## Stehende Regeln

Unverändert gültig seit R2:

1. **Keine Rückwärtskompatibilität.** Ketten ohne die neuen Records werden
   fail-closed abgewiesen.
2. **Die Providernamen-Ratsche wird nicht angehoben.** Recordfelder rollenbasiert.
3. **Kein Cutover.** Der Mirror wird weiter geschrieben.
4. **Kein Cache mit Autorität.**

## Dieser Slice bestimmt seinen Umfang zuerst selbst

Bündel 9 ist als Restbündel definiert: „nur die nach Bündel 1 bis 8 noch
verbleibenden Event- und Auditfelder". Sein Umfang steht also nicht vorab fest,
sondern ergibt sich aus dem, was acht Bündel übriggelassen haben.

**Erster Arbeitsschritt ist deshalb eine Inventur**, in derselben Disziplin wie
S4a: Jeder in `runtime_history` noch geführte Schlüssel wird genau einer Gruppe
zugeordnet — *braucht Record*, *reine Darstellung, entfällt*, oder *bereits
durch ein früheres Bündel gedeckt*. Für „entfällt" ist der heutige Leser zu
benennen, nicht zu behaupten.

`runtime_history` ist ein untypisiertes `Mapping[str, Any]` ohne deklariertes
Schema (`workflow_state.py:1110`). Die Inventur ist deshalb aus dem Code zu
gewinnen, nicht aus einer Typdefinition.

Vorbefund, nicht als Ergebnis zu übernehmen: Gefunden wurden die Schlüssel
`findings`, `reviews`, `attestations`, `active_review_packet`, `events` und
`work_unit_id`. Davon sind `findings` durch S3, `reviews` durch R7 sowie
`attestations` und `active_review_packet` durch R8 gedeckt. Der wesentliche
Rest dürfte `events` sein — zu prüfen, nicht zu glauben.

## Umfang — der normative Rest

| Fakt | Zielrecord und Feld |
|---|---|
| sonstige `runtime_history`-Event- und Auditfelder | `WorkflowEventPayload.event_kind/work_unit_id/slice_id/round_number/record_refs` |

Schreiber laut Sortierung: `_record_review()`, die Validation- und
Transitionpfade.

`record_refs` ist der interessante Teil: Ein Ereignis, das auf bereits
vorhandene Records verweist, braucht deren Inhalt nicht zu wiederholen. Ein
Ereignis, das nur eine Anzeige war, braucht gar keinen Record.

## Was hier endet

Die Matrix hält fest, dass ein reiner Audit- oder Resume-Projektionsanteil erst
**nach** diesem Bündel als Gruppe B entfernt werden darf. Diese Entfernung
gehört also hierher, nach dem Muster von R5: Feld raus aus dem State, Leser auf
die normative Quelle umgehängt, Guard gegen die Rückkehr.

## Die eigentliche Abnahme: die S4b-Vorbedingung

Dieses Bündel „liefert die unmittelbare S4b-Vorbedingung". Das ist mehr als der
letzte Recordtyp — es ist der Nachweis, dass die gesamte R-Serie ihr Ziel
erreicht hat.

**Zu zeigen ist:** Ein leerer `WorkflowState` lässt sich aus einem beliebigen
akzeptierten Recordpräfix deterministisch neu projizieren, und das Ergebnis ist
kanonisch gleich dem heute fortgeschriebenen Mirror.

Nicht an einem Beispiel, sondern über eine Journey mit mehreren Slices,
Korrekturrunden, Gates, einem Halt und einem Resume — und zwar **an jedem
Präfix dieser Journey**, nicht nur am Ende. Das ist derselbe Anspruch, den
`test_combined_multi_slice_correction_sequence_resumes_at_every_prefix` aus S3
für Findings erfüllt, jetzt für den ganzen State.

Fällt dieser Nachweis nicht, ist S4b nicht startbar — unabhängig davon, wie
vollständig die Records aussehen.

## Anforderungen

1. `WorkflowEventPayload` additiv in Schema 2; `schema_version` bleibt `2`.
2. Die Inventur ist als Abschnitt in der Matrix festgehalten, je Schlüssel mit
   Gruppe und Begründung.
3. Der normative Rest ist recordet, die reine Darstellung entfernt.
4. Ein Replay projiziert den vollständigen State aus dem Präfix, ohne
   `state.json`, Checkpoint oder Dateisystem.
5. Eine Kette ohne die neuen Records wird fail-closed abgewiesen.
6. Neue Record-/Mirror-Vergleiche werden in der Matrix inventarisiert und
   gezählt.
7. Alle verbliebenen STOP-Einträge sind geschlossen. Die Matrix weist danach
   keinen offenen Gruppe-A-Eintrag mehr aus.

## Providerfreie Akzeptanzfälle

- Die Präfixprojektion oben, über eine mehrstufige Journey an jedem Präfix.
- Ein Ereignis mit `record_refs` wiederholt keinen Inhalt, der bereits in einem
  fachlichen Record steht; ein Test weist die Dopplung nach, falls sie entsteht.
- Ein entferntes Darstellungsfeld kann nicht in den State zurückkehren.
- Eine Kette ohne Eventrecords wird abgewiesen.
- Der Vollständigkeitstest der Matrix bleibt grün und zeigt keine offenen
  Gruppe-A-Einträge mehr.

## Abnahme

- Die Inventur ist vollständig und je Schlüssel begründet.
- **Die S4b-Vorbedingung ist nachgewiesen, nicht behauptet.**
- Kein offener Gruppe-A-Eintrag mehr in der Matrix.
- Die Providernamen-Baseline ist unverändert.
- Keine Protokoll-, Schema- oder Registerversion angehoben.
- Keine neuen `_recoverable_*`-Sonderfälle.
- Suite grün gegen die Baseline nach R7: 1333 passed. Laufzeit festhalten.

## Nicht-Ziele

- Kein Cutover — das ist S4b und folgt unmittelbar danach.
- Keine Treiberoberfläche — S4c.
- Keine Reparatur der R1-Nachzüge; der Zwischenlauf läuft vor S4b als eigener
  Slice.

## Stopbedingungen

- Anhalten, wenn die Präfixprojektion an irgendeinem Präfix nicht konvergiert.
  Das ist der Befund, für den dieses Bündel existiert — er gehört benannt, nicht
  umgangen.
- Anhalten, wenn ein Schlüssel in keine der drei Gruppen passt.
- Anhalten, wenn ein Ereignis nur durch Wiederholen bereits recordeter Inhalte
  darstellbar wäre.

## Reviewfokus (Claude)

- Ob die Präfixprojektion wirklich an jedem Präfix geprüft wird oder nur am
  Ende der Journey.
- Ob die Journey der Akzeptanz breit genug ist — mehrere Slices, Korrektur,
  Gate, Halt, Resume — oder ein Spaziergang.
- Ob als „reine Darstellung" eingestufte Felder wirklich keinen Leser mehr
  haben.
- Ob `record_refs` verwendet wird oder Inhalte doch dupliziert werden.
- Ob die Matrix danach tatsächlich keinen offenen Gruppe-A-Eintrag mehr führt.
