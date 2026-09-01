# R6 — Implementierungsauftrag: Failure- und Retry-Policy

Auftrag an: Codex
Review durch: Claude (read-only)
Branch: `feature/state-authority-consolidation`, Vorgänger: `0c1804b` (R5)
Übergeordneter Plan: `00-stabilisierung-arbeitsplan.md`
Grundlage: `docs/internal/stabilisierung-s2-uebergangsmatrix.md`, Bündel 6 des
S4a-Schnittvorschlags

**Ohne Orchestrator.** Testsuite unter WSL: `python3 -m pytest tests/ -v`.
Baseline nach R5 (`0c1804b`): **1308 passed in 159 s**.

## Stehende Regeln

Unverändert gültig seit R2:

1. **Keine Rückwärtskompatibilität.** Ketten ohne die neuen Records werden
   fail-closed abgewiesen.
2. **Die Providernamen-Ratsche wird nicht angehoben.** Recordfelder rollenbasiert.
3. **Kein Cutover.** Der Mirror wird weiter geschrieben.
4. **Kein Cache mit Autorität.**

## Korrektur an der Grundlage

Der Schnittvorschlag nennt als Modul `failure_classification.py`. Dieses Modul
existiert nicht. Die typisierte Fehlerklassifikation aus S1 liegt in
**`src/error_classification.py`**. Kein zweites Modul anlegen; die Matrix ist
an dieser Stelle mit zu korrigieren.

## Umfang — vier Gruppen desselben Fakts

Alle Felder von `invocation_failures[*]` gehen in `InvocationFailurePayload`:

| Gruppe | Felder |
|---|---|
| Identität | `invocation_id`, `idempotency_key` |
| Zuordnung und Diagnose | `provider_text`, `received_at`, `step`, `slice_id`, `work_unit_id`, `diagnostic_exit_code` |
| Quota-Scheduling | `parse_path`, `source_timezone`, `reset_at_utc`, `safety_margin_seconds` |
| Retry-Entscheidung | `auto_resume_count`, `automatic_resume`, `diff_fingerprint` |

Schreiber: der klassifizierte Providerfehlerpfad vor der Retry- oder
Pauseentscheidung; der Quota-Parser beziehungsweise Scheduler; die
Retry-/Resume-Policy.

## Drei Stellen, die eine ausdrückliche Entscheidung brauchen

### 1. `provider_text` in einer unveränderlichen Kette

`provider_text` ist heute ein unbegrenzter Rohtext aus der Providerantwort
(`workflow_state.py:289`). Ihn in die append-only Recordkette zu schreiben ist
etwas anderes als ihn im Mirror zu halten: **Ein Record lässt sich nicht mehr
löschen.** Was einmal drin ist, bleibt für die Laufzeit der Kette drin.

Das Repository verbietet an mehreren Stellen ausdrücklich, vollständige
Providerantworten, Prompts oder Secrets dauerhaft abzulegen. Für diesen Slice
ist deshalb zu entscheiden und zu begründen:

- Wird der Text begrenzt, und wenn ja, an welcher gemessenen Grenze?
- Wird er redigiert, und nach welcher Regel?
- Oder genügt für die Diagnose ein Digest plus die typisierte Fehlerklasse,
  und der Rohtext bleibt eine flüchtige Mirror-/Loggröße?

Eine unbegrenzte Rohübernahme ist kein Standardweg, sondern eine Entscheidung,
die begründet werden muss.

### 2. Die Zeitfelder sind hier normativ — anders als in R5

In R5 fielen `decided_by` und `decided_at` als reine Anzeige weg. Hier ist es
umgekehrt: `reset_at_utc`, `source_timezone` und `safety_margin_seconds`
steuern, wann der nächste Versuch überhaupt zulässig ist. Sie sind Gruppe A,
weil eine Entscheidung sie liest.

Daraus folgt eine Frage: Wird der **geparste Quellwert** recordet oder der
**berechnete Zielzeitpunkt** oder beides? Ein berechneter Zeitpunkt, der Tage
später aus dem Record gelesen wird, ist möglicherweise nicht mehr gültig, und
seine Herleitung ist dann nicht mehr nachvollziehbar. `parse_path` deutet
darauf hin, dass die Herkunft bereits mitgeführt wird — das ist zu nutzen, nicht
zu duplizieren.

### 3. Die Fehlerklasse wird nicht zweimal hergeleitet

S1 hat die typisierte Klassifikation in `error_classification.py` zentralisiert:
`TRANSIENT`, `RESUMABLE_HALT`, `TERMINAL_REJECTION`, tabellengetrieben nach
Ausnahmetyp. S3 hat dasselbe Prinzip für Findings durchgesetzt — genau eine
Stelle leitet her, alles andere projiziert.

Der Record trägt deshalb die **von S1 bestimmte Klasse**, und keine Stelle
leitet sie erneut aus `provider_text`, `diagnostic_exit_code` oder einer
Textsuche ab. Eine zweite Herleitung wäre derselbe Defekt, den S1 und S3
beseitigt haben.

## Anforderungen

1. `InvocationFailurePayload` additiv in Schema 2; `schema_version` bleibt `2`.
2. Der Record entsteht **vor** der Retry- oder Pauseentscheidung, nicht danach.
   Ordnungsnachweis gegen den tatsächlichen Leser.
3. Ein Replay des Präfixes rekonstruiert Identität, Zuordnung, Scheduling und
   Retry-Entscheidung ohne `state.json`, Checkpoint oder Dateisystem.
4. `invocation_id` und `idempotency_key` binden den Versuch eindeutig; ein
   Resume erzeugt keinen zweiten Providerstart für denselben Fehler.
5. Die Fehlerklasse stammt aus `error_classification.py` und wird nicht erneut
   hergeleitet.
6. Eine Kette ohne die neuen Records wird fail-closed abgewiesen.
7. Neue Record-/Mirror-Vergleiche werden in der Matrix inventarisiert und
   gezählt.
8. Die STOP-Einträge werden fortgeschrieben; der Modulname wird korrigiert.

## Providerfreie Akzeptanzfälle

- Quota-, Netz- und Prozessfehler erzeugen je einen Record mit ihrer typisierten
  Klasse; das Verhalten der drei Klassen aus S1 bleibt unverändert.
- Ein Quota-Halt mit recordetem Scheduling nimmt nach Resume denselben
  nächsten zulässigen Zeitpunkt an wie heute.
- Ein Resume nach einem transienten Fehler erhöht den Zähler genau einmal;
  ein wiederholter Resume desselben Fehlers erzeugt keinen zweiten Versuch.
- Ein deterministischer Fehler erhöht den transienten Zähler weiterhin nicht —
  die Abnahmebedingung aus S1 bleibt erfüllt.
- Der Umgang mit `provider_text` ist durch einen Test belegt, der die gewählte
  Grenze oder Redaktionsregel prüft, nicht durch eine Behauptung.
- Eine Kette ohne Failure-Records wird abgewiesen.

## Abnahme

- Alle Felder sind aus dem Recordpräfix allein belegbar.
- Die Entscheidung zu `provider_text` ist umgesetzt und begründet.
- Die Entscheidung zu den Zeitfeldern ist umgesetzt und begründet.
- Keine zweite Herleitung der Fehlerklasse.
- Die Providernamen-Baseline ist unverändert.
- Keine Protokoll-, Schema- oder Registerversion angehoben.
- Keine neuen `_recoverable_*`-Sonderfälle.
- Suite grün gegen die Baseline nach R5: 1308 passed. Laufzeit festhalten.

## Nicht-Ziele

- Kein Cutover — S4b.
- Keine Änderung am Verhalten der drei Fehlerklassen aus S1. Dieser Slice
  recordet die Policy, er ändert sie nicht.
- Keine neuen Retryklassen, keine geänderten Obergrenzen.
- Keine Reparatur der R1-Nachzüge.

## Stopbedingungen

- Anhalten, wenn `provider_text` nicht ohne Risiko dauerhaft ablegbar ist und
  auch kein tragfähiger Ersatz gefunden wird. Das ist eine Entscheidung.
- Anhalten, wenn das Quota-Scheduling nur durch Recorden eines berechneten
  Zeitpunkts ohne nachvollziehbare Herleitung möglich wäre.
- Anhalten, wenn die Fehlerklasse für den Record nicht aus S1 verfügbar ist,
  ohne sie erneut herzuleiten.

## Reviewfokus (Claude)

- Ob `provider_text` begrenzt, redigiert oder ersetzt wurde — und ob die Regel
  getestet ist statt nur beschrieben.
- Ob irgendwo die Fehlerklasse ein zweites Mal hergeleitet wird, besonders aus
  Text oder Exitcode.
- Ob der Record vor der Retry-Entscheidung liegt und nicht danach.
- Ob ein Resume denselben Providerversuch wiederholen kann.
- Ob die Zeitfelder ihre Herleitung mitführen oder ein berechneter Wert ohne
  Quelle recordet wird.
- Ob das Verhalten der drei S1-Klassen unverändert geblieben ist.
