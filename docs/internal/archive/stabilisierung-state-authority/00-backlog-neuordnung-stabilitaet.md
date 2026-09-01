# Backlog-Neuordnung unter der Prämisse Stabilität

Stand: 30. August 2026, nach dem S3-Commit `898f14e`, während S4a läuft.
Grundlage: alle 19 Backlogaufgaben `02` bis `20`, gelesen und gegen den
heutigen Code geprüft.

Dies ist ein Vorschlag zur Reihenfolge und Bündelung, keine Beauftragung.

## Teil 1 — Was in den laufenden Stabilisierungsplan gehört

### 04 — WorkflowDriver-Oberfläche: ja, als eigener Slice vor S5

`src/workflow.py` löst neun Treiberfähigkeiten über `getattr(..., None)` auf.
Drei davon sind genau die Kanten, die S3 gerade zur Reducerhoheit erklärt hat:

| Stelle | Kante |
|---|---|
| `workflow.py:867` | `sink = getattr(self.driver, method_name, None)` — die generische Recordsenke |
| `workflow.py:3027` | `authoritative_native_findings` |
| `workflow.py:3077` | `carry_forward_native_findings` |
| `workflow.py:1225`, `:1650` | `recover_pending_native_codex` |
| `workflow.py:3012` | `bind_work_unit` |

**Warum das in den Plan gehört und nicht dahinter:** S4b erklärt die Records
zur einzigen Wahrheit. Eine Pflichtsenke, die wegen eines Namensfehlers still
ausfällt, erzeugt eine Kette mit Loch — ohne Fehler, ohne Divergenzmeldung. Und
S5 würde das nicht finden: Crash-Injection prüft, ob jeder Resume zum selben
kanonischen Zustand konvergiert. Zwei Läufe mit demselben fehlenden Sink
konvergieren zum selben falschen Zustand. S5 wäre grün, das Loch bliebe.

Die Aufgabe selbst nennt als Aktivierungsbedingung, dass der
PLAN_ONLY-Finding-Handoff-Fix in `master` sein muss. Er liegt im Arbeitsbranch.

**Vorschlag:** als **S4c** nach S4a/S4b, vor S5. Nicht früher: S4a entscheidet
gerade, welche Fakten überhaupt Records brauchen — erst danach steht fest,
welche Senken verpflichtend sind.

### 20 — Rootverträge synchronisieren: ja, in den Abschluss des Plans

Der Plan trägt als Abschlusskriterium bereits: „`CLAUDE.md`, `AGENTS.md`,
`CODEX.md` und die Modulköpfe beschreiben dieselbe Autorität.“ Aufgabe 20
verlangt für die PLAN_ONLY-Schreibpflicht dasselbe an denselben drei Dateien.

S4a fasst `CLAUDE.md` ohnehin gerade an. Diese drei Dateien in zwei getrennten
Vorhaben zweimal zu öffnen, erzeugt genau die Divergenz, die der Plan schließen
soll. **Vorschlag:** 20 als Teil des Vertragsabgleichs am Planende erledigen,
nicht als eigenes Vorhaben.

### 03 — Partielle Arbeitsplan-Appendices: erst nachmessen, dann schrumpfen

Drei der beschriebenen Defekte sind heute weg:

| Beschwerde aus 03 | Stand heute |
|---|---|
| irreführende Diagnose `slice document …` | `audit_trail.py:501` meldet `work plan has a partial managed audit appendix: {missing}` |
| kein resumierbares Gate | `PLAN-CONTRACT-INVALID` in `workflow.py:1107`, `:1541` |
| dreifacher technischer Retry, dann Poison | S1 (`d839bfa`) klassifiziert deterministische Fehler als Halt statt Retry |

Offen bleibt vermutlich nur die auf genau eine Runde begrenzte automatische
Planrevision. **Vorschlag:** vor dem nächsten Zugriff nachmessen und die
Aufgabe entweder schließen oder auf ihren Rest kürzen. Sie in voller Größe
weiterzuführen, beschreibt einen Zustand, den es nicht mehr gibt.

### 02 — Final-Review-Kompaktierung: geschrumpft, aber offen, nicht in den Plan

Anforderung 5 („eine Änderung verwalteter Auditprojektionen darf weder
Diffwachstum noch wechselnde Fingerprints erzeugen“) ist durch die semantische
Markdowngrenze aus `6dbd03c` erledigt. Der Rest lebt: die Mehrfachkompaktierung
über `_final_review_audit_evidence_summary` (`orchestrator.py:3371`) und die
wiederholte INFO-Zeile (`:2197`).

Bemerkenswert: Slice 1 hatte diesen Block gelöscht, Blocker `C-02` erzwang die
Rückgabe. Die Aufgabe ist damit belegt notwendig, aber sie ist Laufzeit und
Lograuschen, keine Autorität. **Nicht in den Plan** — sie fasst eingefrorene
Dateien an und wartet.

### 19 — bleibt im Backlog

Ausdrücklich die nicht blockierende Observation `C-01` aus dem Hotfix-Review;
der produktive Pfad ist bereits fail-closed. Gehört thematisch zu 03 und wird
mit ihm zusammen entschieden.

## Teil 2 — Der wichtigste Bündelungsbefund

### 11 und S5 sind dieselbe Arbeit

S5 verlangt: vor und nach jedem dauerhaften Schreib- und Side-Effect-Rand
deterministisch unterbrechen, und „ein providerfreier Langlauf durchläuft
PLAN_ONLY, Handoff, denied Review, Correction, carried Observation, Finalreview
und Resume ohne manuellen Stateeingriff“.

Aufgabe 11 verlangt fast wörtlich dieselben Szenarioklassen, zusätzlich einen
ausdrücklich aktivierten realen Canary.

Wird S5 ad hoc gebaut und 11 später separat, entsteht derselbe Harness zweimal.
**Vorschlag:** S5 erzeugt den providerfreien Harness, den 11 spezifiziert —
versioniert, mit gebundenem Ergebnisartefakt. 11 schrumpft auf den realen
Canary-Modus und dessen Abgrenzung.

## Teil 3 — Bündel für die Zeit nach dem Plan

### Bündel A — Struktur (`08` → `12`)

`12` sagt selbst, dass es nicht nach Dateizahl geschnitten werden darf, und ist
auf `04` vorbedingt. Ist `04` als S4c im Plan erledigt, bleibt: erst die starre
Grenze ersetzen, dann den Kern zerlegen.

**Belegender Nebenbefund:** Der S3-Commit `898f14e` umfasst 21 Dateien. Über den
Orchestrator wäre er an `max_productive_files = 10` gescheitert. Der
Stabilisierungsplan läuft auch deshalb manuell — das ist die konkreteste
Begründung für `08`, die es gibt.

### Bündel B — Betriebsschleife schließen (`05` → `10`, optional `14`)

`05` meldet den kontrollierten Halt, `10` erledigt den deterministischen
Abschluss danach automatisch. `05` nennt selbst als Aktivierungsbedingung
„möglichst vor dem automatischen lokalen Abschluss“.

`10` ist erst nach S4b sinnvoll: Es bindet Reviewrecord, Attestierungsrecord und
Branchfingerprint — das setzt voraus, dass die Records die Autorität sind.

`14` liest dieselben Recordketten und teilt die Projektionsschicht, ist aber
unabhängig terminierbar.

### Bündel C — Providervertrauen (`07` + `06` + `13` + `16`)

Diese vier hängen belegbar zusammen:

- `06` schreibt selbst: „Mit der Major-Release-Eignungsprobe abgleichen, damit
  deren Evidenzformat den Corpus referenzieren kann.“
- `07` hat als offene Vorentscheidung: „Klären, ob ein Modellwechsel innerhalb
  desselben Providers die Eignungsprobe auslöst“ — das ist eine Frage an `13`.
- `13` verlangt für den Canary Isolation ohne Schreibrechte; `16` misst genau,
  ob diese Isolation unter WSL überhaupt herstellbar ist.

Ein Bündel, ein Evidenzformat. `07` zuerst, weil es neue laufgebundene Records
einführt und deshalb nach S4b in die neue Recordwelt gehört, nicht in die alte.

### Bündel D — Einsatzreife nach außen (`09` → `15`), ganz am Schluss

`15` nennt `09` als Eingang und verlangt ausdrücklich, erst nach allen
Verhaltensänderungen zu laufen. Dokumentation, die den Endzustand einmal
beschreibt, statt jede Zwischenstufe dreimal.

### Geparkt lassen: `17`, `18`

Beide sind ausdrücklich ereignisgebunden und ihr Auslöser ist nicht eingetreten
— es gibt keinen dritten Provider und keine belegte bessere Rollenbesetzung.
Nicht bündeln, nicht planen, nicht wiedervorlegen ohne Auslöser.

## Teil 4 — Vorgeschlagene Gesamtreihenfolge

1. Stabilisierungsplan: S4a → S4b → **S4c (`04`)** → S5 (**erzeugt den Harness aus `11`**), Vertragsabgleich am Ende schließt **`20`** mit ein.
2. Nachmessen und entscheiden: **`03`**, **`19`**, **`02`** — drei kleine Reste, gemeinsam.
3. Bündel A: `08` → `12`.
4. Bündel B: `05` → `10`, optional `14`.
5. Bündel C: `07` → `06` → `13` → `16`.
6. Bündel D: `09` → `15`.
7. Geparkt: `17`, `18`.

## Teil 5 — Was diese Ordnung leitet

Drei Kriterien, in dieser Rangfolge:

1. **Autorität vor Komfort.** Alles, was betrifft, welcher Fakt wo verbindlich
   steht, kommt zuerst. Das ist der Plan plus `04`.
2. **Kein Doppelbau.** Wo zwei Aufgaben dieselbe Maschinerie brauchen, baut sie
   die frühere mit — `11`/S5, `06`/`13`, `09`/`15`.
3. **Eingefrorene Dateien nur einmal öffnen.** `02`, `08`, `12` fassen alle
   `orchestrator.py` und `workflow.py` an. Sie nacheinander und gebündelt zu
   fahren, spart Konflikte gegen genau die Module, die der Plan gerade umbaut.
