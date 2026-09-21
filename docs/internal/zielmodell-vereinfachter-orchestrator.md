# Zielmodell: vereinfachter Orchestrator

Festgelegt vom Operator am 21.9.2026, nach einem Tag mit fünf Vertragsbefunden,
die alle aus dem Zusammenspiel von Regeln entstanden sind, nicht aus
Programmfehlern.

Dieses Dokument beschreibt den **Sollzustand**. Es ist noch nicht umgesetzt.

## Der Fluss

### Planungsphase

```
Mensch oder LLM legt ein Arbeitsdokument in die Inbox
        │
        ▼
    Planer ──────► Reviewer ──┬── Abnahme
        ▲                     │
        └─────────────────────┘ zurück zum Planer
```

Nach der Abnahme:

- das Arbeitsdokument geht nach `done`,
- ein Implement-Dokument entsteht.

**Nach der Planung existieren keine offenen Findings mehr.** Was der Reviewer
im Plan beanstandet, wird im Plan behoben.

### Implementierungsphase, je Slice 01 bis nn

```
    Implementierer ──────► Reviewer ──┬── Abnahme ──► Commit ──► nächster Slice
        ▲                             │
        └─────────────────────────────┘ zurück zum Implementierer
```

Der Reviewer unterscheidet **Blocker** und **Findings**. Beide gehen zurück
zum Implementierer.

- **Blocker** korrigiert der Implementierer. Keine Wahl.
- **Findings** entscheidet der Implementierer: umsetzen oder ablehnen, mit
  Begründung.

Die Eskalation schließt die Lücke:

> Setzt der Implementierer ein Finding nicht um und schließt der Reviewer es
> nicht, **wird es ein Blocker**.

Damit endet jedes Finding binär. Es gibt keine Findings minderer Qualität und
keine, die unentschieden liegenbleiben. Am Ende eines Slices ist jeder Befund
behoben, abgelehnt-und-geschlossen, oder er hat den Slice blockiert.

**Findings wandern nicht über Slicegrenzen.** Die einzige Ausnahme ist ein
Befund, der in diesem Slice nicht behandelbar ist; er geht nicht an einen
späteren Slice, sondern in das Arbeitsdokument der nächsten Runde.

### Abnahmephase

Kein eigener Lauf, sondern der letzte Schritt des Implementierungslaufs.

```
Reviewer prüft das Ganze
        │
        ├── keine Befunde ──► Implement-Dokument nach done ──► Ende
        │
        └── Befunde ────────► neues Arbeitsdokument in der Inbox
                                      │
                                      └──► Prozess startet von vorne
```

Der einzige Übergabeweg zwischen Runden ist eine **Datei in der Inbox** —
derselbe Weg, den auch ein Mensch benutzt.

## Was dadurch entfällt

Gemessen am Stand `2e1e9a5`:

| Maschinerie | Fundstellen in `src/` | Grund des Wegfalls |
|---|---|---|
| `BRANCH_DISCOVERY` als eigene Laufart | 175 | Abnahme ist ein Schritt, kein Lauf |
| `family_binding` | 158 | nur nötig, um verkettete Läufe zu binden |
| `reclassif` | 85 | keine Klassenwechsel mehr nötig |
| `cycle_number` | 66 | keine Läufefamilie mehr |
| `handoff_export` / `handoff_import` | 68 | Übergabe ist eine Inbox-Datei |
| `remediation_round` | 51 | keine Rundenzählung über Läufe |
| `branch_planning` | 17 | kein Routingziel mehr |
| `OBSERVATION` | 20 | ersetzt durch die Eskalationsregel |

Dateien, die es überwiegend wegen dieser Maschinerie gibt:

```
plan_handoff.py            540 Zeilen
finding_responsibility.py  339
finding_planning.py        352
finding_convergence.py     333
route_scope.py              23
```

Rund 640 direkte Fundstellen und etwa 1.600 Zeilen.

## Was an ihre Stelle tritt

Nur eine neue Regel, und die ist mechanisch:

```
Implementierer lehnt Finding ab  ∧  Reviewer schließt es nicht
        ⇒  Finding wird Blocker
```

Die Bausteine dafür existieren bereits:

- `FindingResponseDecision` mit `ACCEPTED` und `REJECTED`,
- `finding_dispositions` ist ein Pflichtfeld im Codex-Ergebnisschema.

Zu ändern ist ihre Verbindlichkeit. Heute gilt laut `CLAUDE.md`:

> Codex dispositions are intentionally sparse: omitted open Findings retain
> their state and require no response transition.

Künftig muss der Implementierer **jedes** offene Finding disponieren, und der
Reviewer muss auf jede Ablehnung antworten: schließen oder eskalieren.

## Offene Fragen

1. **Die Slicegrenze.** Ein Befund, der im Slice nicht behandelbar ist, geht
   in das Dokument der nächsten Runde. Wer stellt fest, dass er nicht
   behandelbar ist — Implementierer, Reviewer, oder beide übereinstimmend?
   Ohne Antwort wird das der neue Fluchtweg.

2. **Das Schweigen.** Punkt 105 hat gezeigt: unter Wiederholungsdruck nennt
   ein Reviewer einen Befund im nächsten Versuch einfach nicht mehr. Die
   Eskalationsregel deckt den Fall nicht ab, denn sie greift erst, wenn ein
   Finding genannt ist. Ein zurückgezogenes Finding eskaliert nie.

3. **Der Abnahmereview ohne eigenen Lauf.** Er braucht den vollen Branchstand,
   nicht nur den letzten Slice. Als Schritt innerhalb des Laufs ist das
   möglich; zu klären ist, woher er seinen Vergleichsstand nimmt.

4. **Bestehende Ketten.** Die Reducerversion wechselt. 41 archivierte
   Canary-Läufe bleiben lesbar, aber nicht fortsetzbar. Das ist nach der
   geltenden Regel zulässig und hier ohne Folgen.

## Vorgehen

Bevor Code angefasst wird, das Erreichbarkeitsmodell aus B184 gegen **dieses**
Modell als Referenz bauen. Es prüft dann nicht nur, ob die Regeln zueinander
passen, sondern ob sie diesen Fluss zulassen — und meldet Abweichungen in
beide Richtungen.
