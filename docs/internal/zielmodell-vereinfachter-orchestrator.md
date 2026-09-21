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

### Die Züge

Der Reviewer hat je Finding genau **zwei** Züge:

1. **schließen** — weil es umgesetzt wurde, oder weil er die Ablehnung des
   Implementierers annimmt,
2. **zum Blocker machen**.

Offenlassen ist kein dritter Zug. Will der Reviewer ein Finding offen halten,
**ist** das die Eskalation zum Blocker.

### Der Vertrag des Implementierers

> **Blocker müssen gelöst werden.**
> **Findings können gelöst oder abgelehnt werden.**

Eine Ablehnung trägt eine Begründung. Ob sie gilt, entscheidet der Reviewer,
indem er das Finding schließt — oder es zum Blocker macht.

**Der Implementierer kann einen Blocker nicht bestreiten.** Das ist gewollt:
die Entscheidungshoheit über Befunde liegt beim Reviewer. Ein Blocker, der
sachlich falsch oder in sich widersprüchlich ist, würde damit aber eine
Endlosschleife erzeugen — der Implementierer kann ihn weder beheben noch
ablehnen.

Der Ausweg dafür ist kein Ablehnen, sondern ein **Stop**:

| Lage | Stopgrund |
|---|---|
| Der Blocker ist in sich widersprüchlich oder unverständlich | `CONTRACT-UNCLEAR` |
| Die Behebung braucht etwas, das der Implementierer nicht beschaffen kann | `OPERATOR-PREREQUISITE-MISSING` |
| Die Behebung braucht weitere Dateien | `SCOPE-EXTENSION-REQUESTED` |

Ein Stop holt den Operator, statt stumm zu kreisen. Das ist die einzige
Asymmetrie im Modell, und sie ist bewusst so gebaut.

```
                     ┌─────────────┐
   Reviewer öffnet ──┤   FINDING   │
                     └──────┬──────┘
                            │  Implementierer
               ┌────────────┴────────────┐
          umgesetzt                 abgelehnt
               │                         │
               ▼                         ▼
         Reviewer prüft          Reviewer entscheidet
               │                    ┌────┴─────┐
        ┌──────┴──────┐        schließt    eskaliert
   geschlossen    eskaliert        │           │
                     │             ▼           │
                     └────────► BLOCKER ◄──────┘
                                   │  Implementierer muss beheben
                                   ▼
                             Reviewer prüft
                                   │
                            ┌──────┴──────┐
                      geschlossen    bleibt BLOCKER
```

Damit endet jedes Finding binär. Es gibt keine Findings minderer Qualität und
keine, die unentschieden liegenbleiben. Am Ende eines Slices ist jeder Befund
behoben, abgelehnt-und-geschlossen, oder er hat den Slice blockiert.

**Findings wandern nicht über Slicegrenzen — ohne Ausnahme.**

Die Ausnahme „was im Slice nicht behandelbar ist" wurde am 21.9.2026 gestrichen,
nachdem sich an den Daten kein einziger Fall dafür finden ließ. Alle vier in
Canary 28 geöffneten Findings betrafen Pfade im Scope genau des Slices, in dem
sie gefunden wurden; der Reviewer hat sie trotzdem nach vorn geroutet und wurde
jedes Mal zu Recht abgewiesen.

Die denkbaren Fälle lösen sich anders auf:

| Fall | Gehört wohin |
|---|---|
| Operatorvorleistung fehlt (Testumgebung, Schriften) | Stop, `OPERATOR-PREREQUISITE-MISSING` — kein Finding |
| Querschnittlich, erst im Ganzen sichtbar | Abnahmereview — kein Slice-Finding |
| Behebung braucht Code eines späteren Slices | begründete Ablehnung; beim Review jenes Slices erneut prüfen |
| Der Plan selbst ist falsch | Planbefund in der Planungsphase |

Damit gibt es zwischen Slices **keinen** Übergabeweg mehr.

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

**Nach einem negativen Abnahmereview beginnt der gesamte Prozess von vorne,
mit dem Inhalt des Abnahmereviews als Arbeitsgrundlage.**

Das heißt: nicht ein Wiedereinstieg in die Implementierung, sondern der
vollständige Zyklus ab der Planungsphase. Die Befunde des Abnahmereviews sind
der Inhalt des neuen Arbeitsdokuments; der Planer schneidet daraus Slices wie
aus jeder anderen Anforderung.

Der Reviewer schreibt die Datei nicht selbst — er ist lesend. Der Orchestrator
erzeugt sie aus den Befunden.

### Die Form des Arbeitsdokuments ist frei

Ein Arbeitsdokument darf Prosa sein oder hochdetailliert. Das einzige
Kriterium ist, dass **Planer und Implementierer daraus ihren Arbeitsplan
erzeugen können**.

Daraus folgt: Das aus einem Abnahmereview erzeugte Dokument trägt so viel
technische Genauigkeit, wie der Befund braucht — Pfade, Abnahmebefehle,
Beobachtungen — und zwar **im Text**. Es braucht keine Verkettung in die
Vorrunde, weil die Genauigkeit im Dokument reist und nicht in einer
Recordbeziehung.

**Korrelation über Runden hinweg ist nicht nötig.** Jeder Lauf beweist seine
eigene Arbeit an seinen eigenen Fingerprints. Dass ein Befund in der nächsten
Runde eine andere Kennung trägt, ist folgenlos — die neue Runde misst gegen
ihren eigenen Ausgangsstand.

Das ist der Grund, warum Familienbindung, Zyklusnummern und Handoff-Records
ersatzlos entfallen können und nicht durch etwas Kleineres ersetzt werden
müssen.

## Die Commitbedingung

Nach jeder Reviewrunde ist per Konstruktion **kein Finding unentschieden** —
der Reviewer hat nur schließen oder eskalieren. Damit reduziert sich die
Bedingung für den Slice-Commit auf eine einzige:

> **Es gibt keinen offenen Blocker.**

Heute prüft `slice_exit.py` dafür sechs Bedingungen auf 664 Zeilen. Die
Prüfungen „kein unentschiedenes Finding" und „kein Finding, das noch dem
aktuellen Slice gehört" entfallen, weil beide Zustände nicht mehr entstehen
können.

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
| `responsibilit*` | 490 | ohne Übergabe gibt es nichts zu verantworten |
| `responsibility_routes` | 70 | Feld entfällt aus dem Reviewvertrag |

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

## Was der Reviewer jeweils liest

| Review | Umfang | Mechanismus heute |
|---|---|---|
| Planreview | Plandokument | packetlos |
| **Slicereview** | **nur die geänderten Bereiche** | `ReviewPacket`, manifestgewählt |
| **Abnahmereview** | **der gesamte Code** | packetloser Git-Schnappschuss |

Beides ist bereits so umgesetzt. `agent_runtime.py` `_review_snapshot_paths`
liest für einen packetlosen Review den vollen Arbeitsbaum über
`git ls-files --cached --others --exclude-standard`.

Der Abnahmereview ist damit das Netz unter den Slicereviews: Was ein
Slicereview übersieht oder unter Wiederholungsdruck fallenlässt, wird dort
gefunden.

## Verworfene Reviewergebnisse binden nicht

Ein Reviewlauf, der nicht korrekt endet, wird verworfen — samt allem, was er
gefunden oder nicht gefunden hat. Aus einer ungültigen Antwort entsteht keine
Pflicht.

Damit ist auch die Frage nach dem „Schweigen" beantwortet, die vorher hier
stand: Ein Finding aus einem verworfenen Versuch zur Pflicht zu machen, hieße
einem ungültigen Ergebnis Autorität zu geben. Der Schutz liegt eine Ebene
tiefer, im Abnahmereview über den gesamten Code.

## Offene Frage: der Bindungsanker des Abnahmereviews

`branch_review_base_commit` steuert nicht, *was* gelesen wird, sondern woran
das Ergebnis **gebunden** wird — `workflow_validation_evidence.py:40` prüft,
dass der Änderungsfingerprint zu genau diesem Ausgangspunkt gehört. Heute
liefert ihn die Familienbindung als Familienbasis-Commit.

Ohne Familienbindung bliebe der Branchbasis-Commit des jeweiligen Laufs. Der
Abnahmereview der zweiten Runde läse dann alles, bezeugte aber nur die
Änderungen seit Runde 1. Gelesen und bezeugt wären nicht dasselbe.

**Vorschlag:** der Abzweigpunkt der Zielbranch vom Hauptstrang,
`git merge-base master <zielbranch>`. Über alle Runden stabil, jederzeit aus
git ableitbar, kein laufübergreifender Zustand. Die Infrastruktur dafür
existiert — `merge-base` wird in `git_service.py` bereits verwendet, und
`RepositoryChanges` trägt ein Feld `merge_base`.

Damit entfällt auch `family_base_commit`, eines der drei Felder der
Familienbindung, ersatzlos.

## Vorgehen

Bevor Code angefasst wird, das Erreichbarkeitsmodell aus B184 gegen **dieses**
Modell als Referenz bauen. Es prüft dann nicht nur, ob die Regeln zueinander
passen, sondern ob sie diesen Fluss zulassen — und meldet Abweichungen in
beide Richtungen.
