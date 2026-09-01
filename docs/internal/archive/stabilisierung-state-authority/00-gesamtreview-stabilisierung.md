# Gesamtreview — Stabilisierung des Orchestrators

Reviewer: Claude, read-only, mit selbst erhobenem Git-Stand
Datum: 1. September 2026
Umfang: `master..feature/state-authority-consolidation`, 27 Commits,
96 Dateien, +30.118/−4.081
Kopfcommit: `46034d8`

## Urteil

**Freigabe für den Merge nach `master`.** Kein Blocker.

Fünf der sechs Abschlusskriterien des Plans sind erfüllt und belegt. Das sechste
ist zur Hälfte offen, und zwar in seinem entscheidenden Teil — dazu unten unter
„Der eine offene Punkt".

## Abschlusskriterien

| | Kriterium | Beleg |
|---|---|---|
| ✅ | Jeder persistierte Fakt hat genau eine autoritative Recorddarstellung oder ist ausdrücklich als Cache benannt | S4b; die S2-Matrix führt keinen offenen Gruppe-A-Eintrag mehr |
| ✅ | Kein normaler Crashpunkt braucht einen `_recoverable_*`-Sonderfall | 0 Prädikate; S5b zeigt Konvergenz an **jeder** Randrolle |
| ✅ | Kein deterministischer Fehler erhöht den transienten Retryzähler | S1, tabellengetriebene Klassifikation nach Ausnahmetyp |
| ✅ | Das Finding-Corpus deckt alle historischen Defekte und kombinierte Sequenzen ab | S3, zwölf Fälle ohne Sonderregel je Fall |
| ✅ | Rootverträge und Modulköpfe beschreiben dieselbe Autorität | S6 und `46034d8` |
| ⬜ | Providerfreier Langlauf **und danach ein kleiner echter Canary** ohne manuellen Stateeingriff | Langlauf belegt; **Canary nie gelaufen** |

Zusätzlich aus dem neuen Zuschnitt:

| | | |
|---|---|---|
| ✅ | Keine Treiberfähigkeit wird dynamisch aufgelöst | S4c, AST-Inventur mit Gleichheit in beide Richtungen |
| ✅ | Der Langlaufnachweis ist ein versionierter Harness | S5, `manifest-v1.json`, gebundenes Ergebnisartefakt |

## Die messbare Spur

| | bei S2 | Höchststand | heute |
|---|---:|---:|---:|
| `_recoverable_*`-Prädikate | 5 | 5 | **0** |
| `differs from state-v3` | 12 | 20 | **0** |
| `mismatch(...)` in der Migration | 32 | 44 | **0** |

Bemerkenswert ist der Höchststand: Die Altmechanik **wuchs** während der
R-Serie, weil jeder neue Recordtyp einen neuen Vergleich mitbrachte. Erst der
Cutover löste sie auf. Wer das Vorhaben in der Mitte abgebrochen hätte, hätte
mehr Duplizierung hinterlassen als am Anfang.

Testsuite: 1.163 → **1.413** Tests. Laufzeit 162 s → 425 s im Volllauf, davon
235 s Crash-Harness; die Slice-Validierung liegt durch die Stufentrennung aus
S6 bei rund 190 s.

## Was die Arbeit trägt

Drei Nachweise sind stärker als üblich und tragen das Ergebnis:

**Die Präfixprojektion (R9).** Über eine 44-Record-Journey mit mehreren Slices,
Korrektur, Gate, Halt und Resume wird **jedes** Präfix geprüft — entweder
deterministisch projiziert, zweimal und auf Gleichheit, oder mit genau einem
benannten Grund abgewiesen. `set(to_document()) == set(WorkflowState.__dataclass_fields__)`
belegt, dass die Projektion jedes Feld abdeckt und keine bequeme Teilmenge.

**Der AST-Nachweis über die Cachelesestellen (S4b).** Nicht ein Beispieltest,
sondern eine Zusicherung über die vollständige Menge der Attributzugriffe:
`resolve_resume_state` liest vom Stateobjekt ausschließlich `run_id`.

**Die Positivkontrolle im Skalierungstest (RP).** Der Test stellt den
entfernten Vollscan absichtlich wieder her und fordert, dass der Exponent dann
über der Schwelle liegt. Damit ist bewiesen, dass er eine Regression erkennen
**kann** — die Eigenschaft, die den meisten Performancetests fehlt.

## Prozessbeobachtung

Von siebzehn Slices ging keiner an Codex zurück. Das ist ungewöhnlich und hat
eine erkennbare Ursache: Die Aufträge benannten jeweils die Stelle, an der der
Slice scheitern würde, und machten sie zur Abnahme statt zur Hoffnung.

Zweimal griff das sichtbar. In S5 fand der Harness einen realen Defekt und
**meldete ihn, statt ihn mit einem Sonderfall zu heilen** — `_recoverable_*`
blieb bei null, `end_state` blieb `"stopped"`. In S5b wurde die Konvergenz dann
über die schwierigere Auflösung erreicht, mit einer fail-closed Ablehnungsregel
und vier eigenen Tests, darunter der Near-Miss-Fall.

Ein Fehler ging auf mich: Ich habe in R8 einen Skalierungstest als fehlend
gemeldet, der vorhanden war. Ursache war die Aufzählung neuer Tests per
`git diff`, das unversionierte Dateien nicht zeigt. Betroffen waren drei
Slices; keine Abnahmeentscheidung hing daran. Seit R7 prüfe ich
`git status --short`.

## Der eine offene Punkt

**Der echte Canary ist nie gelaufen.** Das gesamte Vorhaben wurde providerfrei
validiert. 1.413 Tests sagen aus, dass die Zustandsmaschine stimmig ist; sie
sagen nichts darüber, ob ein realer Lauf mit echten Providern, echtem
Quota-Ende, echter Dateisystemlatenz und echter Unterbrechung durchkommt.

Das ist relevant, weil der Ausgangsbefund genau von dort kam: neun
Poison-Reports, ein eingefrorener Lauf, sechs Symptom-Hotfixes — alle aus dem
Betrieb, keiner aus einer Testsuite.

**Das ist kein Mergehindernis.** Der Zustand auf `master` wäre nach dem Merge in
jeder messbaren Hinsicht besser als davor. Aber der Nachweis, dass die
Stabilisierung ihr Ziel im Betrieb erreicht, steht aus und sollte nicht als
erbracht gelten.

Die Startbarkeit ist geprüft: Beide Provider-CLIs sind in der
Claude-CLI-Sitzung verfügbar; ein PLAN_ONLY-Canary kostet rund zehn Minuten und
ein bis zwei Euro auf einem Wegwerfbranch.

## Weitere offene Entscheidungen

1. **`dc29d82`** — der gesicherte Slice 2 des abgelösten Arbeitsplans, 15
   Dateien, +1.561/−80. Kollidiert mit S3 in fünf Dateien; ein Rebase ist keine
   Option. Zu entscheiden: gegen die Record- und Reducerwelt neu umsetzen oder
   verwerfen.
2. **Nachfolgeauftrag Final-Request-Bindung** aus S6, bereits als eigener
   Backlogeintrag geschrieben.
3. **Repositoryumzug** nach `~`, aufgeschoben wegen der Fernsteuerung vom
   Telefon.
4. **`08` und `12`** — Dateigrenze und Strukturschnitt, manuell nach dem Merge.

## Auflagen für die Überführung

- Die Archivierung von
  `01-validierungsevidenz-…-arbeitsplan.md` muss
  `tests/test_semantic_markdown.py:146` **im selben Commit** mitziehen; der
  Test liest die realen Bytes dieser Datei.
- `docs/internal/stabilisierung-s2-uebergangsmatrix.md` bleibt aktiv und wird
  **nicht** archiviert; `tests/test_stabilisierung_s2_transition_matrix.py:11`
  bindet sie, und sie ist die lebende Inventur.
- Der Push nach `origin` bleibt beim Betreiber.
