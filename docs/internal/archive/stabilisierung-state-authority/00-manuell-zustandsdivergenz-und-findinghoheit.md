# 00 – Zustandsdivergenz und Findinghoheit beenden (manuelle Runden)

> **Dieses Paket wird NICHT über den Orchestrator abgewickelt.**
> Es besitzt bewusst keinen `ORCHESTRATOR_MODE` und keinen `TASK_SCOPE`.
> Umsetzung erfolgt in manuellen Runden: Codex implementiert, Claude reviewt,
> der Mensch committet. Begründung siehe „Warum manuell“.

Arbeitsbranch: `feature/state-authority-consolidation` (manuell angelegt)

## Warum manuell

Der Umbau betrifft `artifact_migration.py`, `artifact_bridge.py`,
`workflow.py` und `orchestrator.py` — genau die Module, mit denen der
Orchestrator während eines Laufs seinen eigenen Zustand führt und einen
Resume gegen die Recordkette prüft.

Zwei Gründe schließen die Eigenabwicklung aus:

1. **Empirisch.** Der Lauf `watch-20260829-102810.560490Z-c16fdae858d0` sollte
   genau diesen Problembereich bearbeiten und endete am 29.08. um 15:49 mit
   `MIRROR-AMBIGUOUS: latest work-unit finding state differs from state-v3`.
   Der Versuch scheiterte an dem Defekt, den er beheben sollte.
2. **Strukturell.** Ein Resume startet einen neuen Prozess mit geändertem Code,
   muss aber Records lesen, die der Code des vorherigen Slices geschrieben hat.
   Bei einem Umbau der Record-/Mirror-Semantik ist dieser Übergang zwangsläufig
   inkonsistent. Jeder Zwischenstand kann den laufenden Umbau blockieren.

Sobald die Runden abgeschlossen sind und die Divergenzprüfungen stabil stehen,
kehrt die Arbeit an diesen Modulen in den Orchestrator zurück.

## Befundlage

Erhoben am 29.08.2026 über 245 Commits, 9 Poison-Reports und die aktiven
Auditdokumente.

- 66 von 245 Commits sind Fixes (27 %). Die Rate sinkt nicht: 17 Fixes am
  23.08., 7 am 26.08., 7 am 29.08. Vier abgeschlossene Stabilisierungspakete
  (`orchestrator-stabilization-1-1a` bis `1-1d`) haben den Zustand nicht beendet.
- Von 9 Poison-Abbrüchen sind 6 Zustandsdivergenzen: dreimal weicht der
  Findingzustand zwischen Replay und Mirror ab, dreimal passt ein
  Record-ahead-Recovery-Artefakt nicht zu seinem Request.
- 57 der 96 Fix-Berührungen entfallen auf `workflow.py` (29) und
  `orchestrator.py` (28).
- Zwölf Fixes zwischen dem 23.08. und dem 29.08. betreffen dieselbe Invariante
  — den Findingzustand über Runden, Work-Units, Importe und Resumes hinweg.
- Der Folge-Hotfix vom 29.08. hält fest, dass der vorangegangene Hotfix
  `924a554` die Prüfung auf zwei Schleifen aufteilte und dabei eine Bedingung
  verlor. Ein Fix erzeugte den nächsten Abbruch.
- `artifact_migration.py` enthält rund zehn getrennte Prüfungen der Form
  „… differs from state-v3“.
- `audit_trail.py` beschreibt den heutigen Betrieb selbst als Übergang:
  „The surrounding State-v3 projection remains present during dual-write.“

## Ursachenthese

Die Abbrüche entstehen nicht aus einzelnen Fehlern, sondern aus drei
Struktureigenschaften:

1. **Zwei Wahrheiten.** Recordkette (`structured-v2`) und `state.json`
   (`state-v3`) tragen dieselben Fakten und werden an rund zehn Kanten einzeln
   verglichen. Jede neue Invariante verlangt einen neuen Abgleich; jeder
   vergessene Abgleich ist ein Abbruch.
2. **Kein normativer Ort für den Findingzustand.** Welche Findings offen sind,
   wird bei jedem Übergang neu hergeleitet statt an einer Stelle bestimmt.
3. **Retry-Klassifikation.** Ein deterministischer Vertragsfehler wird wie ein
   transienter behandelt, dreimal identisch wiederholt und dann vergiftet.

Die Runden sind so geordnet, dass zuerst die Poison-Erzeugung aufhört, dann
gemessen wird, und erst danach die Struktur geändert wird.

## Runde 1 — Deterministische Fehler von transienten trennen

**Ziel.** Ein Vertrags- oder Zustandsfehler, der bei identischer Eingabe
identisch wieder auftritt, erzeugt kein Poison mehr, sondern einen
resumierbaren, benannten Halt.

**Vorgehen.** Fehlerklassen im Watch-Retrypfad inventarisieren und in
deterministisch und transient trennen. Nur transiente Fehler zählen in den
Retryzähler.

**Abnahme.** Ein reproduzierter Vertragsfehler hält beim ersten Auftreten mit
eindeutiger Diagnose an. Quota-, Netz- und Prozessfehler verhalten sich
unverändert. Kein Test benötigt einen Provider.

**Warum zuerst.** Diese Runde ändert keine Architektur, senkt aber sofort die
Poison-Rate und macht die folgenden Runden diagnostizierbar.

## Runde 2 — Divergenzkanten inventarisieren (nur lesen)

**Ziel.** Eine vollständige, belegte Liste aller Stellen, an denen Recordkette
und Mirror verglichen werden.

**Vorgehen.** Für jede Prüfung erfassen: welcher Fakt wird verglichen, wer
schreibt ihn auf beiden Seiten, welcher Abbruch entsteht bei Abweichung, und
ist die Prüfung redundant zu einer anderen.

**Abnahme.** Das Ergebnis ist ein Dokument, kein Code. Es benennt für jede
Kante, ob sie nach Runde 4 entfällt, generisch wird oder bewusst bleibt.

**Warum ohne Code.** Ohne diese Inventur wird Runde 4 wieder ein Symptomfix.

## Runde 3 — Findingzustand normativ definieren

**Ziel.** Genau eine Stelle beantwortet für jeden Übergang, welche Findings
offen, geschlossen oder importiert sind. Alle anderen Stellen fragen sie.

**Vorgehen.** Die zwölf Fixes seit dem 23.08. als Anforderungsliste lesen: Jeder
beschreibt einen Übergang, an dem die Herleitung abwich. Die neue Definition
muss alle zwölf Fälle abdecken, ohne sie einzeln nachzubauen.

**Abnahme.** Jeder der zwölf historischen Fälle ist ein providerfreier
Regressionstest. Kein Modul leitet den Findingzustand mehr selbst her.

## Runde 4 — Dual-Write beenden

**Ziel.** Es gibt nur noch eine Wahrheit.

**Vorgehen.** Die Recordkette bleibt autoritativ; `state.json` wird deterministisch
daraus abgeleitet statt parallel geschrieben. Was sich nicht ableiten lässt,
gehört entweder in die Recordkette oder ist kein Zustand.

**Abnahme.** Die in Runde 2 inventarisierten Kanten entfallen oder sind durch
einen einzigen generischen Abgleich ersetzt. Identische Szenarien erzeugen
dieselben kanonischen Zustände wie zuvor. Resume bleibt fail-closed.

**Alternative.** Soll der Mirror als redundante Prüfinstanz erhalten bleiben,
ist das zulässig — dann aber mit einem generischen Abgleich statt zehn
handgeschriebenen Einzelprüfungen. Diese Entscheidung fällt nach Runde 2.

## Arbeitsmodell je Runde

1. Codex implementiert die Runde vollständig, ohne Orchestrator.
2. Claude reviewt den Diff gegen den unmittelbaren Vorgängercommit,
   read-only, mit selbst erhobenem Git-Stand.
3. Offene Punkte gehen als benannte Findings zurück an Codex, bis geschlossen.
4. Der Mensch führt die vollständige Testsuite aus und committet.
5. Erst danach beginnt die nächste Runde.

Keine Runde wird begonnen, solange die vorherige nicht grün und committet ist.

## Nicht-Ziele

- Keine Abwicklung über den Orchestrator, solange die Runden laufen.
- Keine Änderung an Reviewhoheit, Findingnamensraum oder Freigabelogik.
- Keine Lockerung einer fail-closed Grenze, um eine Divergenz verschwinden zu
  lassen. Eine Divergenz wird beseitigt, nicht toleriert.
- Keine Protokollversion anheben, solange Runde 4 nicht entschieden ist.
- Kein Modulschnitt nach Größe; das bleibt Vorgang 09.

## Stopbedingungen

- Anhalten, wenn eine Runde eine persistierte Recordkette bestehender Läufe
  umdeuten müsste, ohne dies ausdrücklich zu entscheiden.
- Anhalten, wenn sich in Runde 2 zeigt, dass eine Kante einen Fakt prüft, der
  auf keiner Seite normativ definiert ist — dann gehört er zuerst definiert.
- Anhalten, wenn ein Fix nur durch Abschwächen einer Resumeprüfung gelingt.
