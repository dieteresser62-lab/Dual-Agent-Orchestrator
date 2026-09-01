# S5b — Implementierungsauftrag: Konvergenz des Basispräfix

Auftrag an: Codex
Review durch: Claude (read-only)
Branch: `feature/state-authority-consolidation`, Vorgänger: `103afe0` (S5)
Übergeordneter Plan: `00-stabilisierung-arbeitsplan.md`

**Ohne Orchestrator.** Testsuite unter WSL: `python3 -m pytest tests/ -v`.
Baseline nach S5 (`103afe0`): **1402 passed in 275 s**.

## Warum es diesen Slice gibt

S5 hat den Harness gebaut und damit getan, wozu er gebaut wurde: einen realen
Defekt gefunden. Der Harness meldet ihn selbst:

```
blocked_acceptance_cases = ["baseline-crash-convergence", "record-backed-long-run"]
end_state = "stopped"
```

Das sind exakt die beiden Kernkriterien von S5 und zugleich das vorletzte
Abschlusskriterium des gesamten Plans. S5 ist damit **nicht abgeschlossen** —
das Instrument steht, das Ergebnis fehlt.

**Codex hat dabei richtig gehandelt:** angehalten, den Befund benannt,
maschinenlesbar hinterlegt — und `_recoverable_*` steht weiterhin bei null. Die
Versuchung, das eine widerspenstige Fenster mit einem Sonderfall zu schließen,
hätte den ganzen Cutover entwertet.

## Der Befund

Genau eine Randrolle konvergiert nicht: `baseline_initialization`, abgebildet
auf die Effektklasse `ledger`.

| | |
|---|---|
| `stop_condition_id` | `STRUCTURED-BASELINE-NONRESUMABLE` |
| `stop_scope` | `run-identity-through-workflow-status-gate-ledger-prefix` |

Ein Absturz im **Basispräfix** eines Laufs — der Recordfolge Runidentität,
Workflowstatus, Gate, Ledger, die R1, R2, R5 und R4 schreiben, bevor die
fachliche Arbeit beginnt — macht den Lauf nicht fortsetzbar.

**Drei entlastende Fakten aus dem Harness:**

- `physical_execution_count == 0` — es ist nichts Externes geschehen.
- Jeder Stop trägt einen typisierten `resume_diagnostic_code`.
- `production_resume_successes == 0` — durchgängig, nicht sporadisch.

Es ist also kein Datenverlust und keine Zustandskorruption, sondern ein
fail-closed Halt ohne Seiteneffekte. Alle übrigen Randrollen — Provider,
Validation, Commit, Handoff, Queue-Finalisierung — konvergieren, und
`record_ahead_evidence` weist für die vier externen Klassen je genau eine
physische Ausführung bei einer Resultatvollendung aus.

## Die Entscheidung, die dieser Slice treffen muss

Zwei Auflösungen sind denkbar, und sie sind **zu entscheiden und zu begründen**,
nicht stillschweigend zu wählen:

**A — Das Basispräfix wird fortsetzbar.** Ein unvollständiges Präfix wird beim
Resume vervollständigt und der Lauf fortgesetzt.

**B — Ein unvollständiges Basispräfix ist verwerfbar.** Weil nachweislich kein
Seiteneffekt stattgefunden hat, ist der Lauf sauber von vorn startbar statt
fortzusetzen. Das wäre kein Aufweichen, sondern die korrekte Einordnung eines
Zustands, in dem es nichts zu retten gibt.

**Zu B gehört zwingend eine Abgrenzung:** Sie darf ausschließlich greifen,
solange `physical_execution_count == 0` für das gesamte Präfix nachweisbar ist.
Sobald irgendein Seiteneffekt stattgefunden hat, bleibt es beim fail-closed
Halt. Diese Grenze ist zu testen, nicht zu behaupten.

## Die Gefahr dieses Slice

`end_state` von `"stopped"` auf `"converged"` zu bringen, ist auf zwei Wegen
möglich: die Produktion reparieren, oder den Harness aufweichen.

`tests/test_crash_harness.py::test_crash_matrix_uses_production_resume_and_names_ledger_stop`
sichert heute ausdrücklich zu, dass der Stop existiert. Dieser Test **muss**
angepasst werden — und genau dort ließe sich Strenge verlieren, ohne dass es
auffällt.

**Verbindlich:** Der Harness behält seine Strenge. Die Injektionsmatrix bleibt
vollständig, `all_injected_crashes_observed` bleibt für jede Rolle wahr, die
Zusicherungen zu `physical_execution_count` und `production_resume_successes`
bleiben. Geändert wird, was der Test **erwartet**, nicht wie genau er hinsieht.

## Anforderungen

1. Die gewählte Auflösung ist umgesetzt und im Slicebericht begründet.
2. `_recoverable_*` bleibt bei **null**. Kein übergangsspezifischer Sonderfall.
3. Der Harness läuft unverändert streng und meldet
   `end_state: "converged"` sowie `blocked_acceptance_cases: []`.
4. Der providerfreie Langlauf durchläuft beide Journeys ohne manuellen
   Stateeingriff.
5. Die Nachweise aus R9, S4b, S4c und S5 bleiben gültig.

## Providerfreie Akzeptanzfälle

- Jeder Crashpunkt des Basispräfix konvergiert; `all_cases_converged` ist für
  **alle** Randrollen wahr, einschließlich `baseline_initialization`.
- Bei Auflösung B: Ein unvollständiges Basispräfix mit auch nur einem
  stattgefundenen Seiteneffekt bleibt fail-closed abgewiesen.
- Der Langlauf läuft aus dem Harness reproduzierbar durch.
- `real_provider_starts == 0` bleibt zugesichert.
- Wiederholung erzeugt dasselbe Ergebnisartefakt.

## Abnahme

- `end_state: "converged"`, `blocked_acceptance_cases: []`.
- Die Zähler `_recoverable_*`, `differs from state-v3` und `mismatch(` bleiben
  bei null.
- Die Entscheidung A oder B ist begründet.
- Suite grün gegen die Baseline nach S5: 1402 passed. Laufzeit festhalten.

## Nicht-Ziele

- Kein Vertragsabgleich — S6.
- Kein realer Canary — Backlog `11`.
- Keine neuen Szenarioklassen im Harness; er ist vollständig.

## Stopbedingungen

- Anhalten, wenn Konvergenz nur durch einen wiedereingeführten
  `_recoverable_*`-Sonderfall erreichbar wäre.
- Anhalten, wenn Auflösung B nicht sauber von Fällen mit stattgefundenem
  Seiteneffekt abgrenzbar ist.
- Anhalten, wenn der Harness dafür weniger streng werden müsste.

## Reviewfokus (Claude)

- **Ob der Harness noch dieselbe Strenge hat.** Das ist der Hauptprüfpunkt: Ich
  vergleiche die Zusicherungen vorher und nachher, nicht nur das Ergebnis.
- Ob die Entscheidung A oder B begründet wurde oder implizit passierte.
- Bei B: ob die Abgrenzung gegen stattgefundene Seiteneffekte getestet ist.
- Ob `_recoverable_*` wirklich bei null bleibt, auch unter anderem Namen.
- Ob der Langlauf ohne manuellen Stateeingriff durchläuft.
