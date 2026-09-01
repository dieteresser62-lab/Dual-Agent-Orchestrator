# R5 — Implementierungsauftrag: Gate-Transitionen

Auftrag an: Codex
Review durch: Claude (read-only)
Branch: `feature/state-authority-consolidation`, Vorgänger: `3f3cf77` (R4)
Übergeordneter Plan: `00-stabilisierung-arbeitsplan.md`
Grundlage: `docs/internal/stabilisierung-s2-uebergangsmatrix.md`, Bündel 5 des
S4a-Schnittvorschlags

**Ohne Orchestrator.** Testsuite unter WSL: `python3 -m pytest tests/ -v`.
Baseline nach R4 (`3f3cf77`): **1297 passed in 147 s**.

## Stehende Regeln

Unverändert gültig seit R2:

1. **Keine Rückwärtskompatibilität.** Ketten ohne die neuen Records werden
   fail-closed abgewiesen.
2. **Die Providernamen-Ratsche wird nicht angehoben.** Recordfelder rollenbasiert.
3. **Kein Cutover.** Der Mirror wird weiter geschrieben.
4. **Kein Cache mit Autorität.**

## Was R5 anders macht als alle bisherigen Bündel

R5 ist das **erste Bündel, das Fakten entfernt**, statt nur welche hinzuzufügen.
Zwei Statefelder sind als Gruppe B eingestuft und sollen wegfallen. Das ist der
Kern dieses Slice, nicht eine Randbemerkung — der additive Teil ist Routine
nach R2 und R3.

## Umfang — drei Fakten der Gruppe A

| Statefeld | Zielrecord und Feld |
|---|---|
| aktueller `gate.status/reason/detail/fingerprint/paths/resume_step` | `GateTransitionPayload.status/reason/detail/fingerprint/paths/resume_step` |
| `work_units[*].active_test_fingerprint`, `active_test_paths` | `GateTransitionPayload.active_test_fingerprint/paths` |
| `gate_decisions[*].paths`, `resume_step` | `GateDecisionPayload.paths/resume_step` |

Schreiber laut Sortierung: die Gatepolicy bei Pending, Clear und Resume; die
Testchange-Gate-Entscheidung nach exakter Scopeentscheidung; die User- und
Policyentscheidung in `persist_gate_decision()`.

Testscope und Resume lesen `active_test_fingerprint` und `active_test_paths`
**gemeinsam** — sie gehören in denselben Record, nicht in zwei.

## Zwei Fakten, die verschwinden

| Statefeld | Warum Gruppe B |
|---|---|
| `gate_decisions[*].decided_by` | Nur Anzeige. Die normative Autorität ist `GatePayload.authority`; `has_gate_approval()` prüft den freien String nicht. |
| `gate_decisions[*].decided_at` | Nur Anzeige. Keine Gate-, Commit- oder Resumeentscheidung liest die Zeit. |

Ich habe die Leser nachgezählt: **35 Referenzen** auf `approved_by`,
`approved_at`, `decided_by` und `decided_at`. Sie laufen alle in dieselbe
Richtung — `orchestrator.py` reicht `decision.decided_by/decided_at` in
`AuthorizedTestChanges` durch, und `audit_trail.py:1211-1212` rendert daraus
„Freigebende Stelle" und „Freigabezeitpunkt" in die Markdown-Auditprojektion.

**Daraus folgt die eigentliche Arbeit:**

1. Die Anzeige wird auf die normative Quelle umgehängt: `GatePayload.authority`
   statt des freien Statestrings. Der Auditinhalt darf sich dabei ändern —
   aber die Änderung ist zu zeigen, nicht zu behaupten.
2. Für den Zeitpunkt ist zu entscheiden und zu begründen, ob er aus
   `ArtifactRecord.created_at` gerendert wird oder ersatzlos entfällt. Die
   Matrix hält fest, dass `created_at` genügt, ohne Gleichheit mit dem alten
   Mirrorzeitpunkt zu behaupten — eine Gleichheitszusicherung wäre falsch.
3. **Die Auditprojektion ist eine geschützte Fläche.** Slice 1 hat mit der
   semantischen Markdowngrenze abgesichert, dass verwaltete Auditabschnitte
   weder Fingerprint noch Guards noch Reviews beeinflussen. Jede Änderung an
   `audit_trail.py` ist gegen diese Grenze zu prüfen, nicht nur gegen die
   Optik.

**Ein hängender Nebenfakt:** `orchestrator.py:4842` setzt
`decided_at=state.updated_at`, und `updated_at` ist selbst Gruppe B. Beide
gehören zusammen betrachtet, sonst bleibt einer als Waise stehen.

## Anforderungen

1. Beide Payloads additiv in Schema 2; `schema_version` bleibt `2`.
2. Der Gaterecord liegt vor seinem Leser — Ordnungsnachweis wie in R2 bis R4,
   gegen den tatsächlichen Leser.
3. Ein Replay des Präfixes rekonstruiert Gatezustand, Testbindung und
   Entscheidungsbindung ohne `state.json`, Checkpoint oder Dateisystem.
4. Die beiden B-Felder sind aus `WorkflowState` entfernt, nicht nur ungenutzt.
   Ein Test sichert ab, dass sie nicht wieder auftauchen.
5. Eine Kette ohne die neuen Records wird fail-closed abgewiesen.
6. Neue Record-/Mirror-Vergleiche werden in der Matrix inventarisiert und
   gezählt.
7. Die STOP-Einträge werden fortgeschrieben; die beiden B-Zeilen werden als
   entfallen markiert, mit Verweis auf ihren Ersatz.

## Providerfreie Akzeptanzfälle

- Ein Policygate durchläuft Pending, Entscheidung und Resume; je Übergang
  entsteht genau ein Record vor seinem Leser.
- Ein Replay liefert `paths` und `resume_step` der Entscheidung exakt; ein
  Resume auf dieser Grundlage trifft denselben Dispatch wie heute.
- `active_test_fingerprint` und `active_test_paths` kommen gemeinsam zurück;
  eine Kette mit nur einem der beiden wird abgewiesen.
- Die Auditprojektion nennt die freigebende Stelle weiterhin, jetzt aus
  `GatePayload.authority`. Ein Test vergleicht die erzeugte Projektion gegen
  einen erwarteten Text, sodass die Änderung sichtbar dokumentiert ist.
- Die semantische Markdowngrenze aus Slice 1 bleibt wirksam: Eine reine
  Neuprojektion des Auditabschnitts verändert weder Fingerprint noch
  Guardergebnis.
- `decided_by` und `decided_at` existieren nicht mehr im State; ein Guard
  verhindert ihre Rückkehr.

## Abnahme

- Alle drei Fakten sind aus dem Recordpräfix allein belegbar.
- Die beiden B-Fakten sind entfernt, ihre Leser umgehängt, die Entscheidung zum
  Zeitpunkt begründet.
- Die Auditprojektion ist gegen erwarteten Text geprüft, nicht nur gegen
  „läuft durch".
- Die Providernamen-Baseline ist unverändert.
- Keine Protokoll-, Schema- oder Registerversion angehoben.
- Keine neuen `_recoverable_*`-Sonderfälle.
- Suite grün gegen die Baseline nach R4: 1297 passed. Die Laufzeit wird
  festgehalten.

## Nicht-Ziele

- Kein Cutover — S4b.
- Keine Failure-/Retry-Policy — Bündel 6.
- Keine Reparatur der R1-Nachzüge.
- Keine Änderung an der Gatelogik selbst. Dieser Slice verschiebt die
  Autorität eines Fakts, nicht die Bedingung einer Entscheidung.

## Stopbedingungen

- Anhalten, wenn sich zeigt, dass `decided_by` oder `decided_at` doch eine
  Entscheidung steuern. Dann ist die S4a-Einstufung falsch und gehört
  korrigiert, nicht umgangen.
- Anhalten, wenn die Auditprojektion ohne die beiden Felder nicht
  aussagekräftig bleibt.
- Anhalten, wenn `GatePayload.authority` den Anzeigezweck nicht abdeckt.

## Reviewfokus (Claude)

- Ob die beiden B-Felder wirklich entfernt sind oder nur nicht mehr gelesen
  werden. Ein ungenutztes Feld im State ist genau die Doppelquelle, die dieser
  Plan beseitigt.
- Ob die Änderung der Auditprojektion gegen erwarteten Text belegt ist oder ob
  nur geprüft wird, dass etwas gerendert wird.
- Ob die semantische Markdowngrenze aus Slice 1 unberührt geblieben ist.
- Ob `active_test_fingerprint` und `active_test_paths` wirklich gemeinsam
  gebunden sind.
- Ob der Zeitpunkt aus `created_at` als Ersatz ausgegeben wird, ohne
  Gleichheit mit dem alten Wert zu behaupten.
- Ob `state.updated_at` als hängender B-Fakt mitbehandelt oder bewusst
  aufgeschoben wurde.
