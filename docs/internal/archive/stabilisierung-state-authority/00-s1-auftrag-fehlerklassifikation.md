# S1 — Implementierungsauftrag: Dreiwege-Fehlerklassifikation

Auftrag an: Codex (Implementierung)
Review durch: Claude (read-only, gegen den unmittelbaren Vorgängercommit)
Branch: `feature/state-authority-consolidation`, Basis `3daab29`
Übergeordneter Plan: `inbox/backlog/00-stabilisierung-arbeitsplan.md`

**Ohne Orchestrator.** Kein `ORCHESTRATOR_MODE`, keine Slice-Records, keine
Auditprojektion. Der Mensch führt `python3 -m pytest tests/ -v` aus und committet.

## Ziel

Kein deterministischer Fehler erzeugt mehr Poison. Ein Fehler, der bei
identischer Eingabe identisch wiederkehrt, hält beim **ersten** Auftreten an —
mit typisierter Diagnose, erhaltener Watch-Identität und intaktem Resume.

## Befundlage (im Code verifiziert, 29.08.2026)

Die Zielinfrastruktur existiert bereits und wird nur nicht erreicht:

- `WatchTaskDisposition.RESUMABLE_HALT` ist definiert
  (`src/inbox_watcher.py:34`) und wird mit Exit-Code 4 bedient.
- `ArtifactResumeError` und `StateSchemaError` werden am äußeren Rand korrekt
  in einen resumierbaren Halt überführt (`src/orchestrator.py:4477 ff.`).
- Der Watcher akzeptiert einen Halt aber nur unter zwei Bedingungen
  (`src/inbox_watcher.py:180` und `:199`): Der Status muss in einer festen
  Menge liegen **und** der Exit-Code muss 2, 3 oder 4 sein. Andernfalls greift
  `TECHNICAL_FAILURE`.
- **Alle neun `*.poison.error.json`-Reports tragen Exit-Code 1 oder null.**
  Keiner hat den vorhandenen Resumable-Pfad erreicht.

Ursache ist die Fehlerklasse, nicht die Watcherlogik: Persistenz-, Vertrags-
und Bindungsfehler werden als `WorkflowExecutionError`, `RepositoryChangeError`
oder `ArtifactBridgeError` geworfen und kommen als generischer Exit-Code 1 an.

**Kein einziger der neun Poison-Fälle war durch Wiederholung heilbar.** Sie
wurden dennoch je dreimal ausgeführt — 27 Läufe, deren Ausgang von vornherein
feststand, plus die manuelle Wiederherstellung danach.

## Die drei Klassen

1. **Transient** — Quota, Netz, vorübergehender Prozessfehler.
   Verhalten unverändert: begrenzt wiederholen, Retryzähler erhöhen.
2. **Resumierbarer Eingriffshalt** — Record-/Mirror-, Schema-, Fingerprint-,
   Bindungs- oder Recoverykonflikt eines **begonnenen** Laufs.
   Task und Watch-Identität erhalten, Queue mit typisiertem Diagnosecode
   anhalten, Retryzähler **nicht** erhöhen, kein Poison.
3. **Terminale Eingabeablehnung** — Vertragsfehler **vor** Laufbeginn, etwa ein
   unzulässiger Branchname. Einmalig als abgelehnt ablegen. Kein zweiter
   Versuch, kein scheinbar resumierbarer Lauf, keine blockierte Queue.

## Anforderungen

1. Die Klassifikation hängt an typisierten Fehlern beziehungsweise Resultaten.
   Keine Textsuche in Fehlermeldungen, keine Entscheidung erst nach dem
   zweiten identischen Versuch.
2. Jeder der 46 in `src/` definierten `*Error`-Typen wird genau einer Klasse
   zugeordnet. Die Zuordnung ist an einer Stelle ablesbar, nicht über
   `except`-Zweige verstreut.
3. Verschachtelte Ursachen dürfen ihre Klasse nicht verlieren. Wo
   `checkpoint()` oder `_persist_structured()` eine innere Ursache in
   `WorkflowExecutionError` einpacken, muss die Klasse der Ursache erhalten
   bleiben — über einen typisierten Träger, nicht über den Meldungstext.
4. Ein Fehler ohne zugeordnete Klasse ist ein Fehler: Er wird fail-closed als
   Klasse 2 behandelt (anhalten, nicht wiederholen) und nicht stillschweigend
   als transient eingestuft.
5. Klasse 3 darf nur greifen, bevor ein Lauf Records geschrieben hat. Sobald
   eine Recordkette existiert, ist Klasse 2 die einzige zulässige Ablehnung.

## Abnahme

**Corpus.** Jeder der neun historischen Poison-Reports in `outbox/failed/` wird
als providerfreier Test reproduziert und einer Klasse zugeordnet. Die Zuordnung
ist im Test begründet. Erwartete Verteilung nach heutigem Kenntnisstand:

| Fehlermeldung (gekürzt) | erwartete Klasse |
|---|---|
| `authoritative finding replay differs from the state-v3 mirror` | 2 |
| `TARGET_BRANCH must use feature/<name> or codex/<name>` | 3 |
| `slice document is missing managed sections` | 2 |
| `native Codex recovery response no longer validates: request-mismatch` | 2 |
| `native Codex recovery has multiple agent-result records` | 2 |
| `branch final review requires a complete passing attestation` | 2 |
| `native agent request differs from its persisted recovery artifact` | 2 |
| `finding export plan commit is not present in accepted replay` | 2 |
| `structured audit dual-write mismatch: MIRROR-AMBIGUOUS` | 2 |

Weicht die tatsächliche Zuordnung ab, ist das kein Fehler des Auftrags — die
Abweichung ist zu begründen und im Test festzuhalten.

**Weitere Kriterien.**
- Kein Test der Klassen 2 und 3 erhöht den Retryzähler.
- Quota-, Netz- und Prozessfehler verhalten sich unverändert; die vorhandenen
  Quota-Tests bleiben grün.
- Ein Klasse-3-Fehler blockiert die Queue nicht und erzeugt keinen
  Resume-Zustand.
- Ein Klasse-2-Fehler erhält Watch-Identität, Run-ID und Recordkette
  vollständig; ein anschließender Resume setzt exakt am selben Schritt an.
- Kein Test benötigt Provider, Netz oder API.

## Nicht-Ziele

- Keine Änderung an Recordsemantik, Reducerlogik oder Mirrorvergleichen — das
  ist S3 und S4.
- Keine neuen `_recoverable_*`-Sonderfälle.
- Keine Lockerung einer fail-closed Grenze, damit ein Fehler seltener auftritt.
- Keine Änderung an Reviewhoheit, Findingnamensraum oder Freigabelogik.
- Keine Umbenennung von `WatchTaskDisposition` oder seiner Exit-Codes, soweit
  nicht durch Klasse 3 zwingend erforderlich.

## Stopbedingungen

- Anhalten, wenn eine saubere Klassenzuordnung eine Änderung der
  Recordsemantik verlangen würde. Dann gehört der Fall nach S3/S4.
- Anhalten, wenn ein Fehlertyp je nach Aufrufort in zwei verschiedene Klassen
  fiele — das deutet auf einen zu grob geschnittenen Fehlertyp hin und ist zu
  melden, nicht durch eine Sonderregel zu überdecken.
- Anhalten, wenn Klasse 3 nicht zuverlässig von „Lauf hat bereits Records
  geschrieben“ abgegrenzt werden kann.

## Reviewfokus (Claude)

- Verschluckte Ursachen: Bleibt die Klasse durch alle Einpackstellen erhalten?
- Klassifikation, die doch am Meldungstext hängt.
- Transiente Fehler, die versehentlich zu Halten werden — das würde die Queue
  blockieren statt sie zu schützen.
- Klasse 3 auf einen Lauf angewandt, der bereits Records geschrieben hat.
- Ob die neun Corpus-Tests die realen Fehlerpfade treffen oder nur die
  Meldungstexte nachbauen.
