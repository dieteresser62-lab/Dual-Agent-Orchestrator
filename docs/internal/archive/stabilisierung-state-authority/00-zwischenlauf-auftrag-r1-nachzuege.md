# Zwischenlauf — R1 auf den Standard der Folgebündel nachziehen

Auftrag an: Codex
Review durch: Claude (read-only)
Branch: `feature/state-authority-consolidation`, Vorgänger: `f7e66ac` (R9)
Übergeordneter Plan: `00-stabilisierung-arbeitsplan.md`

**Ohne Orchestrator.** Testsuite unter WSL: `python3 -m pytest tests/ -v`.
Baseline nach R9 (`f7e66ac`): **1344 passed in 200 s**.

## Warum dieser Slice existiert

R1 entstand, bevor zwei Regeln galten, die ab R2 für jedes Bündel bindend
waren. Er ist deshalb der einzige Ausreißer der gesamten Serie. Dieser Slice
bringt ihn auf denselben Stand — kein neues Verhalten, nur Konsistenz.

## Warum er vor S4b läuft

Beide Punkte ändern Recordformen. Solange der Mirror noch eigenständig
geschrieben wird, ist das ein kleiner Eingriff. Nach dem Cutover wird der
Mirror aus den Records abgeleitet, und jede Formänderung schlägt dann durch die
Projektion durch.

## Punkt 1 — Vor-R1-Ketten fail-closed abweisen

Stand geprüft am 31. August 2026:

```
test_pre_r1_chain_without_run_records_remains_readable      ← liest weiter
test_pre_r2_chain_without_complete_status_prefix_is_rejected
test_pre_r3_chain_without_slice_boundary_prefix_is_rejected
test_pre_r5_chain_without_gate_transition_prefix_is_rejected
```

R6, R8 und R9 weisen ebenfalls ab, unter anderen Testnamen. Nur R1 hält seine
Records optional, weil sein Auftrag das damals ausdrücklich verlangte.

**Zu tun:** `RunIdentityPayload` und `RunProfilePayload` werden wie alle
übrigen Recordtypen verpflichtend. Eine Kette ohne sie wird fail-closed
abgewiesen. Der Test wird entsprechend umgedreht, nicht gelöscht — der neue
Name soll die Abweisung benennen.

**Achtung Wechselwirkung:** R9 prüft in
`test_multi_slice_correction_gate_halt_resume_projects_every_accepted_prefix`
jedes Präfix darauf, dass es entweder projiziert oder mit **genau einem**
benannten Grund abgewiesen wird. Wird R1 verpflichtend, verschiebt sich die
Grenze zwischen akzeptierten und abgewiesenen Präfixen. Dieser Test ist
mitzuziehen, und seine Zusicherung „genau ein Ablehnungsgrund" muss weiter
halten.

## Punkt 2 — `RunProfilePayload` rollenschlüsseln

`RunProfilePayload` trägt die Providernamen in den Feldnamen:
`codex_model`, `codex_effort`, `claude_model`, `claude_effort`
(`artifact_models.py:161-163`). R1 hat dafür die Providernamen-Ratsche
angehoben: die Baseline für `src/artifact_models.py` steht seither bei
`{"codex": 22, "claude": 18}` gegenüber `{14, 10}` davor.

Ab R2 gilt die Regel, dass Recordfelder rollenbasiert benannt werden. R2 hat
sie mit `implementer_return_count` und `max_implementer_returns` vorgemacht,
und seither ist die Baseline in sieben Bündeln unverändert geblieben.

**Zu tun:** Die Felder werden rollenbasiert geführt, etwa als Abbildung Rolle
auf Modell und Effort. Die konkrete Form entscheidet der Slice; verlangt ist
nur, dass kein Providername mehr im Recordfeldnamen steht.

**Abnahme dieses Punktes ist die Baseline selbst:** Der Eintrag für
`src/artifact_models.py` fällt auf seinen Vor-R1-Stand zurück oder wird
begründet, warum ein Rest bleibt. Ein Anheben ist ausgeschlossen.

**Nutzen über die Konsistenz hinaus:** Backlog `07` (Modell und Effort je
Rolle und Operation) und `18` (Provider-Rollen-Entkopplung) müssten diese
Struktur sonst später aufbrechen. Jetzt kostet es einen Recordtyp.

## Anforderungen

1. Beide Änderungen additiv beziehungsweise umbenennend in Schema 2;
   `schema_version` bleibt `2`.
2. Kein Cutover. Der Mirror wird weiter geschrieben.
3. Die Präfixprojektion aus R9 bleibt vollständig gültig; die Grenze zwischen
   akzeptierten und abgewiesenen Präfixen darf sich verschieben, die
   Zusicherungen nicht.
4. Neue oder geänderte Record-/Mirror-Vergleiche werden in der Matrix
   inventarisiert und gezählt.
5. Die Matrix wird an den betroffenen Stellen fortgeschrieben.

## Providerfreie Akzeptanzfälle

- Eine Kette ohne `RunIdentityPayload` oder `RunProfilePayload` wird
  fail-closed abgewiesen, mit typisiertem Grund.
- Die R9-Präfixprojektion läuft weiterhin über jedes Präfix und liefert je
  abgewiesenem Präfix genau einen Grund.
- Ein Lauf bindet Modell und Effort je Rolle; Resume weist eine abweichende
  explizite Anforderung weiterhin fail-closed zurück.
- Die Providernamen-Baseline für `src/artifact_models.py` liegt nachweislich
  unter dem R1-Stand.

## Abnahme

- R1 folgt beiden ab R2 geltenden Regeln.
- Kein Providername mehr in Recordfeldnamen.
- Die Ratschen-Baseline ist gesunken, nicht angehoben.
- Die R9-Nachweise bleiben gültig.
- Keine Protokoll-, Schema- oder Registerversion angehoben.
- Keine neuen `_recoverable_*`-Sonderfälle.
- Suite grün gegen die Baseline nach R9: 1344 passed. Laufzeit festhalten.

## Nicht-Ziele

- Kein Cutover — das ist S4b und folgt unmittelbar.
- Keine operationsbezogenen Profile; das bleibt Backlog `07`.
- Keine Rollenentkopplung; das bleibt Backlog `18`.
- Keine weiteren Recordbündel; die Serie ist mit R9 abgeschlossen.

## Stopbedingungen

- Anhalten, wenn die Umbenennung die unveränderliche Laufbindung oder die
  Resumeprüfung aufweichen würde.
- Anhalten, wenn die R9-Präfixprojektion nach der Änderung nicht mehr für jedes
  Präfix genau einen Ablehnungsgrund liefert.

## Reviewfokus (Claude)

- Ob die Ratschen-Baseline wirklich gesunken ist und nicht nur anders verteilt.
- Ob die R9-Präfixprojektion nachgezogen wurde, statt ihre Zusicherungen
  aufzuweichen.
- Ob der umgedrehte R1-Test die Abweisung benennt statt sie nur nicht mehr zu
  prüfen.
- Ob die Rollenschlüsselung Resume und Laufbindung unverändert lässt.
