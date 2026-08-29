# P0-Hotfix: Finding-Import nach verneintem Slice-Review fortsetzen

Datum: 29. August 2026

Betroffener Lauf: `watch-20260829-102810.560490Z-c16fdae858d0`

Freigabe: Dieter hat die unmittelbare Umsetzung und den lokalen Hotfix-Commit freigegeben.

## Fehlerbild

Der erste IMPLEMENT-Slice übernahm `C-01` aus dem freigegebenen PLAN_ONLY-Lauf.
Nach Runde 1 schloss Claude `C-01`, eröffnete `C-02` und `C-03` und verneinte
das Slice-Review. Der Orchestrator schrieb daraufhin korrekt den Work-Unit-Record
für Runde 2 mit den offenen Findings `C-02` und `C-03`. Replay und State-Mirror
verglichen diesen aktuellen Stand jedoch weiterhin ausschließlich mit dem
ursprünglichen Import `C-01`. Der Checkpoint stoppte deshalb mit
`RECORD-FINGERPRINT-MISMATCH`.

Der Runde-2-Record ist nicht beschädigt: Sein Findingstand entspricht exakt dem
Import plus allen davor persistierten Finding-Transitions. Der State-Mirror ist
lediglich um diesen bereits dauerhaft geschriebenen Reviewübergang zurück.

## Korrektur

1. Ein importgebundener Work-Unit-Record wird gegen den autoritativen
   Record-Präfix geprüft: Finding-Import plus alle davor liegenden
   Finding-Transitions. Ein erfundenes oder unterschlagenes offenes Finding
   bleibt damit ein `RECORD-FINGERPRINT-MISMATCH`.
2. Die lebende Open-Menge wird nur mit der neuesten Revision einer Work Unit
   verglichen. Historische Revisionen behalten ihren damals gültigen Stand;
   die unveränderliche Importprovenienz wird weiterhin an jeder Revision
   geprüft.
3. Die begrenzte Record-ahead-Recovery erkennt nun auch den Übergang einer
   normalen Slice-Work-Unit in die nächste Runde nach genau einem verneinten
   Claude-Review. Reviewidentität, Reihenfolge, passende
   Validierungsattestierung, Rundennummer und Findingmenge werden fail-closed
   gebunden.

## Sicherheitsgrenzen

- `.orchestrator/state.json`, Checkpoints und die append-only Record-Kette
  werden nicht verändert.
- Die Recovery akzeptiert nur Runde `mirror + 1` derselben Slice-Work-Unit.
- Der verneinende Claude-Review muss eindeutig sein und vor dem neuen
  Work-Unit-Record liegen.
- Eine Attestierung mit demselben Fingerprint muss vor dem Review liegen.
- Die offenen Findings der neuen Runde müssen aus den Finding-IDs des Reviews
  stammen und durch den Record-Präfix belegbar sein.

## Verifikation

- Replay akzeptiert `C-01` in Runde 1 und den daraus abgeleiteten Stand
  `C-02/C-03` in Runde 2.
- Eine nicht durch vorherige Transitions belegte Open-Menge wird weiterhin
  abgewiesen.
- Positive und adversariale Tests decken den Record-ahead-Übergang ab.
- `resolve_resume_state` wurde read-only gegen den realen pausierten Lauf
  ausgeführt und liefert dessen bestehenden Runde-2-Record als gültigen Head.
- Vor dem Commit wird die vollständige Repository-Testsuite ausgeführt.

## Wiederaufnahme

Nach dem Hotfix-Commit bleibt die bestehende Watch-Identität maßgeblich. Der
Task wird nicht neu erzeugt und nicht als neuer Lauf eingeworfen. Da der
Hotfix-Commit den Git-Stand während eines offenen Slices bewusst verändert,
kann der Orchestrator beim ersten Resume ein fingerprintgebundenes Drift- oder
Pfadgate verlangen. Dieses Gate darf ausschließlich für die hier dokumentierten
Hotfixpfade und den angezeigten Fingerprint genehmigt werden. Anschließend muss
der vorhandene Lauf in Runde 2 fortfahren; eine erneute Planung oder ein
erneuter Claude-Review der Runde 1 ist nicht erforderlich.
