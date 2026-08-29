# P0-Folge-Hotfix: Carried Findings in späteren Work Units

Datum: 29. August 2026

Betroffener Lauf: `watch-20260829-102810.560490Z-c16fdae858d0`

Auslöser: Der Hotfix-Commit `924a554` teilte die Prüfung der
Finding-Importbindung und der aktuellen Open-Menge auf zwei Schleifen auf. Im
Latest-Record-Zweig ging dabei die Bedingung verloren, dass die Open-Menge nur
für eine tatsächlich importgebundene Work Unit mit dem Mirror verglichen wird.

## Beobachteter Zustand

Slice 01 wurde ordnungsgemäß abgeschlossen:

- Validierungsmatrix: vollständig grün, zwei von zwei Anforderungen bestanden.
- Claude-Review Runde 2: freigegeben.
- `C-02` und `C-03`: geschlossen.
- `C-04`: neue offene `OBSERVATION` für einen späteren Slice.
- Slice-Commit: `6dbd03c379d5700e6114619656845c9735ee5ae5`.

Der Lauf startete danach Work Unit 3 für Slice 02. Deren
`WorkUnitPayload.open_finding_ids` ist vertragsgemäß leer, weil nur die erste
Implementierungs-Work-Unit den PLAN_ONLY-Finding-Import bindet. Der lebende
State führte gleichzeitig `C-04` weiter. Der zu breite Mirrorvergleich deutete
diesen korrekten Zustand als `MIRROR-AMBIGUOUS` und der Watcher verschob den
Task nach drei technischen Fehlversuchen ins Poison-Outbox.

## Korrektur

Der Latest-Record-Vergleich der Open-Menge wird wieder an
`payload.finding_import_record_id is not None` gebunden. Damit gelten zwei
getrennte Verträge:

1. Importgebundene Work Units müssen ihre Open-Menge weiterhin exakt gegen den
   Mirror nachweisen.
2. Spätere Work Units besitzen keinen Import-Snapshot. Ihre lebenden Findings
   werden ausschließlich über die Finding-Transitions der Record-Kette und den
   State-Ledger geprüft.

## Regressionstests

- Eine spätere Work Unit bleibt mit einer carried `OBSERVATION` replay- und
  resumefähig, obwohl ihr Work-Unit-Record keine Importbindung und eine leere
  `open_finding_ids`-Menge besitzt.
- Eine abweichende Open-Menge der importgebundenen ersten Implementierungs-Work-
  Unit bleibt fail-closed und erzeugt weiterhin die typisierte Mirror-Diagnose.
- Die vorhandenen Record-ahead-, Import- und Replaytests bleiben unverändert
  grün.

## Wiederaufnahmegrenze

Weder State noch Checkpoints noch Records werden manuell verändert. Nach
Freigabe des Folge-Hotfixes wird der unveränderte Poison-Task unter seinem
Originalnamen wiederhergestellt. Weil der Poison-Übergang das Watch-Sidecar
gelöscht hat, erfolgt die erste Wiederaufnahme ohne laufenden Watcher direkt
über `run_task --resume --task-file ...`. Erst nach erfolgreicher Konvergenz und
kontrollierter Entfernung der alten Plan-/Implement-Aufgabe darf der Watcher
wieder gestartet werden.
