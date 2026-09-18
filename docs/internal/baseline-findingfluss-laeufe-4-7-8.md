# Baseline: Findingfluss der Läufe 4, 7 und 8

Erhoben am 18.9.2026 als Vorleistung zu Backlogpunkt 68, dessen Review die
Rekonstruktion aus den Recordketten ausdrücklich vor jeder Erfolgsmessung
verlangt. Die bis dahin zitierten Zahlen 99 / 18 / 22 waren Betriebsevidenz aus
laufenden Beobachtungen, nicht aus den Ketten abgeleitet.

## Methode

`scripts/baseline_findingfluss.py` liest die Recordketten der drei Läufe aus
`.orchestrator/artifacts/<run-id>/records/`, ordnet sie nach Revision und wertet
aus. Die Ketten liegen im Cookbook-Repository und sind dort nicht versioniert;
für diese Erhebung wurde eine Kopie gesichert.

Arbeitseinheiten werden über ihre Schritte klassifiziert:

| Art | erkannt an |
|---|---|
| Implementierungsslice | `codex_implementation` oder `claude_slice_review` |
| Korrekturrunde | `codex_final_correction` |
| Abschlussreview | `claude_final_review` oder `codex_final_review` |

Die Auswertung liest die Kette, sie validiert sie nicht. Sie ist eine
Auswertung, keine autoritative Projektion.

### Grenzen

- Die Frage „hätte ein späterer Slice das Finding fachlich tragen können"
  ist nicht mechanisch entscheidbar. Ersatzweise wird geprüft, ob ein späterer
  **Planslice** mindestens einen der im Findingtext genannten Codepfade in
  seinem genehmigten Scope hatte. Korrekturrunden sind dabei ausgeschlossen:
  Ihr Scope umfasst faktisch die gesamte Anwendung und würde jedes Finding als
  „tragbar" erscheinen lassen. Genau diese Verwechslung hat die erste Fassung
  dieser Messung um den Faktor sechs verzerrt.
- Findings ohne erkennbaren Codepfad im Text fallen aus der Tragbarkeitsfrage
  heraus.
- Providerzeiten stammen aus `provider_attempt.duration_seconds` und enthalten
  keine Wartezeiten, keine Halte und keine Operatorzeit.

## Die drei Messfragen aus Punkt 68

| | Lauf 4 | Lauf 7 | Lauf 8 |
|---|---:|---:|---:|
| Planslices | 42 | 29 | 31 |
| Findings eröffnet | 110 | 20 | 35 |
| davon `BLOCKER` | 3 | 1 | 4 |
| davon `OBSERVATION` | 107 | 19 | 31 |
| **offen am Ende der Implementierung** | **99** | **18** | **32** |
| offen am Laufende | 2 | 18 | 2 |
| **in einem Slicereview eröffnet** | **108** | **20** | **31** |
| erstmals im branchweiten Review | 0 | 0 | 4 |
| in einer Korrekturrunde | 2 | 0 | 0 |

**Die zitierten Zahlen bestätigen sich.** Die 99 aus Lauf 4 und die 18 aus
Lauf 7 reproduzieren exakt. Die 22 aus Lauf 8 war eine Momentaufnahme bei
Slice 24 von 31; am Ende der Implementierung standen dort 32 offene Findings.

**Frage 1 — wie viele der offenen Findings stammen aus einem Slicereview?**
Praktisch alle. 108 von 110, 20 von 20, 31 von 35. Nur Lauf 8 hat überhaupt
Findings, die erstmals im branchweiten Review entstanden: vier.

**Frage 2 — wie viele hätte ein späterer Planslice tragen können?**

| | Lauf 4 | Lauf 7 | Lauf 8 |
|---|---:|---:|---:|
| ein späterer Planslice trug die Pfade im Scope | 1 | 3 | 2 |
| kein späterer Planslice | 107 | 17 | 29 |
| Anteil tragbar | 0 % | 15 % | 6 % |

**Frage 3 — wie viele entstanden erstmals im branchweiten Review?**
Null, null, vier.

## Die Entscheidung fällt nie dort, wo sie billig wäre

Von den in einem Slicereview eröffneten Findings wurden in der eigenen
Arbeitseinheit entschieden: **1 von 108** (Lauf 4), **1 von 20** (Lauf 7),
**0 von 31** (Lauf 8). Der Abstand bis zur Entscheidung betrug im Median 15
beziehungsweise 13 Arbeitseinheiten, im Maximum 41.

Das ist die quantitative Fassung der Prämisse von Punkt 68: Der Reviewer hat
das Finding im günstigsten Moment gesehen — kleiner Diff, geladener Kontext,
gerade geprüfter Code — und die Entscheidung trotzdem im Schnitt vierzehn
Arbeitseinheiten später getroffen.

Findings je Slice im Mittel: 2,6 (Lauf 4), 0,7 (Lauf 7), 1,1 (Lauf 8). Ohne
jedes Finding blieben 9 von 42, 14 von 29 und 10 von 31 Slices. Die zusätzliche
Entscheidungslast aus Punkt 68 träfe also etwa zwei Drittel der Slices und läge
dort typischerweise bei einem bis drei Findings.

## Zwei Folgen, die Annahmen der Punkte 67 und 68 widersprechen

### Das Slice-Routing aus 68/E2 ist praktisch ein leerer Fall

Punkt 68 sieht als dritte zulässige Entscheidung das Routing vor, „entweder auf
einen benannten späteren Planslice — zulässig nur, wenn dessen bereits
genehmigter Scope die Behebung tatsächlich trägt — oder auf die Branchplanung".

Gemessen trägt ein späterer Planslice die Pfade in 0 bis 15 Prozent der Fälle.
In Lauf 4 war es ein einziges Finding von 108. Der Grund liegt im Slice-Schnitt
selbst: Die Pläne schneiden nach Zuständigkeit, jede Datei gehört im Kern genau
einem Slice, und spätere Slices berühren sie nicht mehr.

Damit landet unter Punkt 68 fast alles, was nicht sofort behoben oder begründet
abgelehnt wird, bei **`BRANCH_PLANNING`** — also bei Punkt 67.

### Die Entlastungsannahme von Punkt 67 trägt deshalb nicht

Punkt 67 begründet seine Chargengrenze mit der Erwartung, Punkt 68 reduziere
die Eingabemenge: „Mit 68 ist die Eingabemenge klein und die Grenze praktisch
unkritisch." Diese Erwartung stützt sich auf ein Slice-Routing, das es in der
Praxis kaum gibt.

Wie groß die Menge wirklich wird, hängt jetzt allein daran, wie viele der
`OBSERVATION`-Findings ein Slicereviewer sofort schließt oder begründet
ablehnt, statt sie zu routen — und das ist keine gemessene, sondern eine
erhoffte Größe. Bei einem Lauf im Format von Lauf 4 stünden im ungünstigen Fall
über hundert Findings vor der Chargengrenze von 32 aus 67/E5.

**Vor der Umsetzung zu entscheiden:** ob die Grenze steigt, ob die
Behebungsplanung über mehrere Chargen laufen darf, oder ob die
Ablehnungsbegründungen aus 68/E2 so billig gemacht werden, dass sie den
Hauptabfluss bilden. Die heutige Fassung beider Punkte lässt diese Frage offen,
weil sie von einem funktionierenden Slice-Routing ausgeht.

## Was der Kostenarm nicht hergibt

Das Review zu Punkt 68 nennt die Umstellung „operativ voraussichtlich
günstiger". Die Providerzeit stützt das nicht:

| | Providerzeit | Anteil Abschluss und Korrektur |
|---|---:|---:|
| Lauf 4 | 21,78 h | 6,6 % |
| Lauf 7 | 4,45 h | 5,9 % |
| Lauf 8 | 6,76 h | 15,0 % |

Der Abschluss- und Korrekturschwanz kostet also nur einen kleinen Teil der
Providerzeit; den Löwenanteil tragen Implementierung und Slicereview. Punkt 68
verschiebt Arbeit in genau diese teure Phase hinein.

Der eigentliche Aufwand des heutigen Korrekturpfads liegt nicht in Providerzeit,
sondern in **Halten und Operatorzeit** — Lauf 8 brauchte sieben. Das bleibt das
tragfähige Argument für beide Punkte; das Providerzeitargument sollte nicht
geführt werden.

## Reproduktion

```
python3 scripts/baseline_findingfluss.py <pfad>/artifacts/<run-id> [...]
```

Die Auswertung braucht nur die Recordkette. Nach dem gemeinsamen Reducer-Cutover
aus 67/E8 und 68/E7 ist sie nur noch über den dort vorgesehenen read-only
Legacy-Verifier möglich — die Erhebung musste deshalb vorher erfolgen.
