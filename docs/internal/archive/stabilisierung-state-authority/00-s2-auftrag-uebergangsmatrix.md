# S2 — Implementierungsauftrag: Übergangs- und Divergenzmatrix

Auftrag an: Codex
Review durch: Claude (read-only)
Branch: `feature/state-authority-consolidation`, Vorgänger: der S1-Commit
Übergeordneter Plan: `inbox/backlog/00-stabilisierung-arbeitsplan.md`

**Ohne Orchestrator.** Testsuite unter WSL: `python3 -m pytest tests/ -v`.

## Ziel

Eine vollständige, belegte Matrix aller Stellen, an denen die append-only
Recordkette und der `state-v3`-Mirror verglichen werden — und aller Crashpunkte
zwischen ihnen.

**Das Ergebnis ist ein Dokument, kein Produktivcode.** Diese Runde ändert
bewusst kein Verhalten. Sie entscheidet, was S3 und S4 tun dürfen.

Ablageort: `docs/internal/stabilisierung-s2-uebergangsmatrix.md` (versioniert,
wird mit dieser Runde committet).

Zulässige Codeänderung: ausschließlich Tests, die die Vollständigkeit der
Matrix gegen den Code absichern (siehe Abnahme). Keine Änderung an
Produktivmodulen.

## Verifizierte Ankerpunkte

Diese Stellen sind bereits belegt und bilden den Ausgangspunkt — die Inventur
muss darüber hinausgehen, nicht dahinter zurückfallen:

| Anker | Fundstelle |
|---|---|
| 12 wörtliche `differs from state-v3` | `src/artifact_migration.py` |
| `_recoverable_pending_review_finding_gap` | `src/artifact_migration.py:651` |
| `_recoverable_pending_correction_record` | `src/artifact_migration.py:732` |
| `_recoverable_pending_slice_denial_record` | `src/artifact_migration.py:764` |
| `_recoverable_pending_work_record` | `src/artifact_migration.py:798` |
| Records werden vor dem Mirror geschrieben | `src/orchestrator.py`, `checkpoint()` |
| `State-v3 remains authoritative until the explicit cutover` | `src/artifact_bridge.py`, Modulkopf |

Zusätzlich zu erfassen sind mindestens: Setvergleiche für Gates, Findings,
Attestierungen, Quota, Retries, Bootstrapchecks, Commitbindungen und Abschluss;
`ArtifactBridgeError`-Vergleichspfade; sowie jede weitere Stelle, an der ein
Fakt aus Records **und** aus dem State hergeleitet und dann verglichen wird.

## Je Übergang zu erfassen

1. **Autoritativer Eingaberecord** und erwarteter Folgerecord
2. **Abgeleitete State-/Cachefelder** — welche Felder des Mirrors dieser
   Übergang schreibt
3. **Tatsächliche Schreibreihenfolge** und jeder Crashpunkt dazwischen
4. **Idempotenz** — ist eine Wiederholung zulässig, und woran wird sie erkannt
5. **Vorhandener `_recoverable_*`-Sonderfall**, falls einer greift
6. **Semantik-/Protokollversion**, unter der der Übergang ausgewertet wird
7. **Externe Side Effects**, die vor dem nächsten dauerhaften Zustand möglich
   sind (Git-Commit, Providerstart, Dateiverschiebung, Queuebewegung)

## Die entscheidende Zusatzfrage

**Welche heute im State geführten Fakten besitzen keinen Record?**

Diese Liste bestimmt, ob S4 den Mirror ableiten kann oder ob vorher Records
ergänzt werden müssen. Jeder Fakt ist einzeln aufzuführen mit: Feldname,
Schreiber, Leser, und ob er aus dem Recordpräfix rekonstruierbar wäre.

Ein Fakt, der weder Record hat noch rekonstruierbar ist, ist ein Stopgrund für
S4 und muss als solcher benannt werden — nicht überbrückt.

## Bewertung je Kante

Für jede inventarisierte Kante ist genau eine Einstufung zu begründen:

- **entfällt** — nach dem Cutover gibt es nichts mehr zu vergleichen
- **wird generisch** — geht in einen einzigen Cacheintegritätsabgleich auf
- **bleibt bewusst** — mit Begründung, warum diese Kante eine eigene Prüfung
  rechtfertigt

Eine Kante ohne Einstufung ist ein unvollständiges Ergebnis.

## Abnahme

- Alle 12 `differs from state-v3` und alle vier `_recoverable_*` sind erfasst
  und eingestuft.
- Für jeden Übergang sind die sieben Felder ausgefüllt oder ausdrücklich als
  nicht zutreffend markiert.
- Die Liste der Fakten ohne Record ist vollständig und je Eintrag bewertet.
- Ein providerfreier Test sichert die Vollständigkeit gegen den Code ab: Er
  zählt die Vergleichsstellen beziehungsweise `_recoverable_*`-Funktionen im
  Quelltext und schlägt fehl, sobald der Code eine Stelle enthält, die die
  Matrix nicht kennt. Damit veraltet die Inventur nicht unbemerkt.
- Kein Produktivmodul ist geändert.
- Suite bleibt grün (Baseline nach S1: 1181 passed unter WSL).

## Nicht-Ziele

- Keine Verhaltensänderung, keine Reparatur, keine Vereinfachung „im
  Vorbeigehen“.
- Keine Entscheidung über den Cutover — die fällt in S4 auf Basis dieser Matrix.
- Keine Bewertung nach Zeilenzahl oder Aufwand.
- Keine neuen `_recoverable_*`-Sonderfälle.

## Stopbedingungen

- Anhalten, wenn eine Kante einen Fakt vergleicht, der auf **keiner** Seite
  normativ definiert ist. Das ist ein eigener Befund und gehört in die Matrix,
  nicht in eine stille Annahme.
- Anhalten, wenn die Schreibreihenfolge eines Übergangs aus dem Code nicht
  eindeutig bestimmbar ist — dann ist die Stelle als unklar zu markieren statt
  zu raten.

## Reviewfokus (Claude)

- Vollständigkeit gegen den **Code**, nicht gegen die Meldungstexte: Eine
  Vergleichsstelle ohne eigene Fehlermeldung darf nicht fehlen.
- Übersehene Crashpunkte, besonders zwischen Recordschreibung und
  Mirroraktualisierung sowie vor und nach externen Side Effects.
- Fakten ohne Record, die als „rekonstruierbar“ eingestuft sind, ohne dass der
  Weg benannt wird.
- Einstufungen „bleibt bewusst“, die in Wahrheit nur schwer ablösbar sind.
- Ob der Vollständigkeitstest wirklich den Code prüft oder nur die Matrix mit
  sich selbst vergleicht.
