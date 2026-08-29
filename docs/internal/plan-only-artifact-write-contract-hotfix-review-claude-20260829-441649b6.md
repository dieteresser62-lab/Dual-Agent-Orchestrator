# Claude-Review: PLAN_ONLY-Schreibvertrag-Hotfix

**Reviewer:** Claude (manuell, adversarial, read-only) · **Datum:** 29. August 2026
**Diff-SHA-256:** `441649b693601b95e13ed04e81df56cd541b27b34837cbd4fbca6c0db39d309b`

Dieses Review ist keine Orchestrator-Attestierung.

## 1. Ergebnis vorab

**`FINAL_APPROVAL: YES`** — kein ausführbarer Defekt gefunden.

Der Hotfix behebt die Ursache, die ich im gestrigen Lauf isoliert hatte: Die
PLAN_ONLY-Anweisung verlangte als Handlung nur das Emittieren eines Datensatzes;
die Schreibpflicht stand allenfalls als Objekt einer Nebenkonstruktion darin. Sie
ist jetzt der erste Imperativ, wird vor `ready: true` an eine Selbstprüfung
gebunden, und der Datensatz ist ausdrücklich als Quittung gekennzeichnet. Die
gesamte fail-closed Schutzschicht des Vorgängerhotfixes bleibt unverändert.

Ein nicht blockierender Befund (`C-01`): Eine leere, unlesbare oder unsicher
aufgelöste Plandatei erhält weiterhin **keine** Reparaturrunde, obwohl die neue
Anweisung genau diese Fälle ausdrücklich adressiert.

## 2. Selbst erhobener Stand

| Merkmal | Gemessen | Erwartet | |
|---|---|---|---|
| Branch | `feature/plan-only-artifact-write-contract-hotfix` | identisch | ✓ |
| Basis / `HEAD` | `c9de3e16938a6206892676864182027f7aa11189` | identisch | ✓ |
| Änderungspfade | `src/orchestrator.py`, `src/workflow.py`, `tests/test_orchestrator_runtime.py`, `docs/internal/plan-only-artifact-write-contract-hotfix-20260829.md` | identisch | ✓ |
| Weitere Pfade | keine | keine | ✓ |
| SHA-256 über `git diff HEAD --no-ext-diff --binary` | `441649b6…d309b` | identisch | ✓ |
| `git diff HEAD --check` | ohne Whitespacebefund (nur CRLF-Konvertierungswarnungen) | grün | ✓ |

Anmerkung zum Stand: Alle vier Pfade sind bereits **gestaged** (`A`/`M` in der
ersten Spalte). Ich habe daran nichts verändert.

**Reviewgrenze.** Kein Watcher-, Provider- oder Canarylauf, keine Netzzugriffe.
Weder Produktcode noch Tests, Git-Zustand, `.orchestrator/state.json`,
Checkpoints oder Records verändert. Einzige Schreiboperation ist dieses Dokument.
Die vollständige Validierungsmatrix habe ich nicht ausgeführt.

**Eigene Ausführung vs. Fremdangabe.** Selbst gefahren:
`tests/test_orchestrator_runtime.py -k plan_only` → **`4 passed, 73 deselected in 5.56s`**
und die gesamte Datei → **`77 passed in 22.62s`**. Die vollständige Suite
(`1134 passed in 148.53s`) bleibt eine übernommene Codex-Angabe, die ich nicht
reproduziert habe.

## 3. Bewertung der acht Korrekturzusagen

Ich habe den Anweisungsblock aus `_plan_only_step_boundary()` real gerendert:

```
- Create or update the file docs/internal/plan.md in the repository now. Its
  complete content is the deliverable of this step.
- Before returning `ready: true`, reread that file and verify that it exists,
  is non-empty, and contains the executable work plan.
- Then emit exactly one executable PLAN_ONLY SLICE_PLAN record for that artifact.
  The native JSON record is only a receipt for the written file; it does not
  contain or replace the work-plan document.
- If you cannot write and verify the work-plan file, do not return `ready: true`;
  return the typed stop result instead.
- Future product implementation Slices belong only as human-readable sections
  inside the work-plan document; do not emit them as executable SLICE_PLAN
  records in this run.
- Do not modify product code, tests, configuration, or generated artifacts. The
  declared work-plan document is the one repository file you must write.
```

| # | Zusage | Bewertung |
|---|---|---|
| 1 | Operative Schreibhandlung gefordert | **erfüllt** — erster Imperativ, „in the repository now" |
| 2 | Datei ist Lieferleistung, JSON nur Quittung | **erfüllt** — wörtlich, plus „does not contain or replace" |
| 3 | Selbstprüfung vor `ready: true`, sonst Stop-Datensatz | **erfüllt** — siehe 4.1 zur Vertragsverträglichkeit |
| 4 | Änderungsverbot mit Ausnahme genau der Plandatei | **erfüllt** — Verbot und Ausnahme im selben Satz, kein Konflikt |
| 5 | Initial-Summary fordert Artefakterstellung | **erfüllt** — ersetzt „Plan the requested work." |
| 6 | Summary-Änderung auf PLAN_ONLY + `WorkUnitKind.PLAN` begrenzt | **erfüllt** — siehe 4.3 |
| 7 | Revision unterscheidet fehlend / vorhanden-ungültig | **erfüllt** — siehe 4.2 |
| 8 | Einmalige Revision, fail-closed Gate, Claude erst nach gültigem Artefakt | **erfüllt, unverändert** — siehe 4.4 |

## 4. Geprüfte Dimensionen und Gegenproben

### 4.1 Ist der Stop-Pfad mit dem tatsächlichen Vertrag vereinbar?

Das war die schärfste technische Frage, denn eine Anweisung, die ein
schema-ungültiges Resultat provoziert, wäre schlimmer als das ursprüngliche
Problem. Sie ist beantwortet:

- `native_codex_contract.py:265-270` nimmt `stop_result` für eine
  PLAN-Anfrage in die Ergebnis-Union des Writerschemas auf.
- `parse_bound_native_codex_contract_result` behandelt `result_type ==
  "stop_result"` **vor** der Erwartungstypprüfung (`:356-370`), ein Stop bei
  einem Planrequest ist also kein `RESULT_KIND_MISMATCH`.
- Eine frei erfundene Regelkennung wird abgewiesen: `workflow.py:2819` prüft
  `stop_request.rule_id not in context.known_stop_rule_ids`.
- Das Vokabular steht Codex zur Verfügung; der Prompt rendert die vollständige
  Stop-Regelliste (`workflow.py:318`).

Der Stop-Pfad ist damit vertragskonform und in der Regelkennung fail-closed.

### 4.2 Ist die Fallunterscheidung durch Prosa steuerbar?

Nein. Ich habe die Klassifikation gegen alle acht Validatorfehler des
Planpfads gefahren:

| Validatorfehler | Klassifikation |
|---|---|
| `PLAN_ONLY Codex planning must create or update WORK_PLAN_PATH` | reparierbar / **fehlend** |
| `WORK_PLAN_PATH cannot produce an IMPLEMENT handoff: …` | reparierbar / **ungültig** |
| `PLAN_ONLY requires exactly one executable plan-artifact Slice` | nicht reparierbar |
| `PLAN_ONLY plan does not include WORK_PLAN_PATH` | nicht reparierbar |
| `WORK_PLAN_PATH must not be empty` | nicht reparierbar |
| `WORK_PLAN_PATH must be a regular non-symlink file` | nicht reparierbar |
| `WORK_PLAN_PATH is not readable UTF-8: …` | nicht reparierbar |
| `WORK_PLAN_PATH cannot be resolved safely: …` | nicht reparierbar |

Entscheidend für die Manipulationsfrage: Der „fehlend"-Zweig verlangt das
Präfix `PLAN_ONLY `, und diese Meldung ist ein fester Orchestratorstring ohne
Interpolation (`orchestrator.py:2388`). Der „ungültig"-Zweig bettet zwar
`{exc}` aus dem von Codex geschriebenen Dateiinhalt ein, behält aber das Präfix
`WORK_PLAN_PATH …`. Gegenprobe mit einem gezielt konstruierten Handofffehler,
dessen Text auf das Missing-Suffix endet: bleibt korrekt als **ungültig**
klassifiziert. Die beiden Zweige sind über Agenteninhalt nicht verwechselbar.

### 4.3 Leckt die geänderte Summary?

Die Bedingung ist `planned is None` **und** `execution_mode == PLAN_ONLY`
**und** `current_work_unit.kind is WorkUnitKind.PLAN`. Damit gilt:

- PLAN_ONLY + PLAN ohne gebundenen Slice → neue Summary
- PLAN_ONLY + SLICE / CORRECTION / FINAL_REVIEW → unveränderter Fallback
- normaler IMPLEMENT-Lauf mit Plan-Work-Unit → unveränderter Fallback
- jede Work-Unit mit gebundenem `planned` → dessen eigene Summary

Kein Leck in Implementierungs-, Korrektur- oder Abschluss-Work-Units.
`_plan_only_step_boundary` liefert für `execution_mode != PLAN_ONLY`
nachweislich den leeren String.

### 4.4 Kann `work_plan_path` hier `None` oder unsicher sein?

`None` ist ausgeschlossen: `workflow_state.py:1217-1220` weist einen
PLAN_ONLY-Zustand ohne `work_plan_path` bei der Konstruktion ab; ich habe das
mit einem Konstruktionsversuch bestätigt (`WorkflowStateValidationError:
PLAN_ONLY state requires work_plan_path`). Beide neuen Textstellen liegen hinter
einem PLAN_ONLY-Guard. Der Pfad selbst wird zusätzlich in `validate_plan` auf
sichere Auflösung, reguläre Nicht-Symlink-Datei und UTF-8 geprüft; er stammt
aus der orchestratorseitigen Ableitung, nicht aus Agenteninhalt.

### 4.5 Beweisen die Tests die transportierte Anweisung?

Ja, und das ist die stärkste Eigenschaft dieses Pakets. Beide Tests behaupten
gegen `invocation.native_request.canonical_json`, also gegen das real
transportierte Requestdokument, nicht gegen lokale Hilfsstrings — und sie
prüfen in beide Richtungen:

- positiv: „Create or update the file … now. Its complete content is the
  deliverable of this step.", „native JSON record is only a receipt",
  „one repository file you must write";
- negativ: `"Plan the requested work." not in request_json`;
- im Reparaturfall: „Create the missing file … now" vorhanden **und**
  `"Repair only the declared work-plan artifact" not in request_json`;
- im Ungültigfall: „Update the existing file … now" vorhanden **und**
  `"Create the missing file" not in request_json`.

Damit ist auch die Zweigtrennung aus Zusage 7 an der Transportgrenze belegt.

### 4.6 Near-Miss-Analyse

| Fall | Verhalten |
|---|---|
| Datei fehlt | Missing-Diagnose → genau eine Revision → sonst pfadloses Gate |
| Datei nur mit Auditmarkern / ohne Slice-Abschnitte | nicht leer → Handoffextraktion scheitert → **ungültig** → eine Revision |
| Datei leer | `must not be empty` → **keine** Revision, direkt Gate (siehe `C-01`) |
| Datei am falschen Pfad geschrieben | `WORK_PLAN_PATH` fehlt weiter; zusätzlich ist der fremde Pfad eine echte Repositoryänderung und läuft in das fingerprintgebundene `UNEXPECTED-PATH`-Nutzergate — die Grenzprüfung liegt vor der Vertragsprüfung |
| Symlink / Nicht-UTF-8 / unsicherer Pfad | fail-closed, keine Revision |

Kein Fall umgeht die Zusage; alle enden entweder in einer gebundenen Revision
oder in einem fail-closed Gate.

### 4.7 Kann Codex weiterhin plausibel `ready: true` ohne Datei liefern?

Ehrliche Antwort: ja, technisch schon — es bleibt ein Prompt, kein Vertrag. Die
Wahrscheinlichkeit sinkt deutlich, weil die Pflicht jetzt viermal und in
unterschiedlicher Form auftritt (Handlung, Selbstprüfung, Quittungsabgrenzung,
Stop-Alternative) und die früher irreführende Verbotsformulierung entschärft
ist. Entscheidend für die Freigabe ist jedoch nicht die Prompthoffnung, sondern
dass die dahinterliegende Schutzschicht unverändert greift: Der Vorgängerhotfix
erkennt genau diesen Fall, gewährt genau eine Revision und endet sonst pfadlos
fail-closed, ohne Reviewerbudget auszugeben. Beide Ebenen zusammen sind die
richtige Konstruktion.

## 5. Findings

### C-01 — Leere oder unlesbare Plandatei erhält keine Reparaturrunde (OBSERVATION)

Die neue Anweisung verlangt ausdrücklich, vor `ready: true` zu prüfen, dass die
Datei „exists, is non-empty, and contains the executable work plan". Schreibt
Codex dennoch eine leere Datei, meldet der Validator `WORK_PLAN_PATH must not be
empty` — und dieser Text fällt in keine der beiden reparierbaren Klassen. Der
Lauf endet sofort im `PLAN-CONTRACT-INVALID`-Gate, während der *fehlenden* Datei
eine gebundene Revision zusteht. Dasselbe gilt für Nicht-UTF-8, Symlink und
unsicher aufgelöste Pfade.

Das ist keine Regression — die Reparaturmenge ist unverändert gegenüber dem
Vorgängerhotfix. Es ist aber eine Asymmetrie in genau der Fallgruppe, die dieser
Hotfix adressiert: Der Sinn der bounded repair ist, eine Anweisung, die der
Planer beim ersten Mal missachtet hat, einmal nachdrücklich zu wiederholen. Für
das leere Artefakt fällt diese Chance ersatzlos aus.

**Disposition:** nicht blockierend. Das Verhalten bleibt in jedem Fall
fail-closed; es kostet nur eine Reparaturchance, nie Korrektheit.
**Akzeptanztest:** `WORK_PLAN_PATH must not be empty` wird in die reparierbare
„vorhanden, aber ungültig"-Klasse aufgenommen, die Revision erhält den
`Update the existing file …`-Text, und ein Test belegt genau eine Revision und
danach das pfadlose Gate — analog zum bestehenden Handoff-Fall.

## 6. Größtes Restrisiko und realistische Bruchbedingung

Größtes Restrisiko ist die **Bindung des Vertrags an Fließtext**. Der Hotfix
verschiebt die Zusage in die Formulierung: Ob die Datei entsteht, hängt weiterhin
davon ab, dass das Modell den Prompt so liest wie beabsichtigt. Nachweisbar ist
nur, was der Orchestrator danach prüft — und das ist die Existenz und
Handofffähigkeit, nicht die inhaltliche Güte des Plans. Ein Modell, das die
Anweisung befolgt, aber ein dünnes Alibidokument schreibt, passiert die
Vertragsprüfung.

Hinzu kommt die aus `C-01` folgende Lücke: Der Near-Miss, der bei einem
schlecht kooperierenden Planer am wahrscheinlichsten ist — irgendetwas
schreiben statt nichts — ist genau der, der keine zweite Chance bekommt.

**Realistische Bruchbedingung:** Der Planer schreibt beim ersten Versuch eine
Datei mit Überschrift und einem Satz. Die Handoffextraktion scheitert, die eine
Revision wird verbraucht, die zweite Fassung ist erneut zu dünn — und der Lauf
endet im Gate, nachdem zwei Providerläufe bezahlt wurden. Der Orchestrator hat
sich dabei korrekt verhalten; sichtbar wird das Problem nur als wiederholtes
Gate ohne erkennbaren Fortschritt.

## 7. Pre-Mortem

In drei Monaten fällt auf, dass PLAN_ONLY-Läufe zwar zuverlässig ein Dokument
erzeugen, dessen Qualität aber schwankt: Der Vertrag prüft Existenz, Nichtleere
und Handoffstruktur, nicht Substanz. Weil die Schutzschicht grün meldet, wandert
die Prüfung der Plangüte vollständig in das Claude-Planreview — das dafür
gedacht ist, aber jetzt Pläne bewertet, die formal korrekt und inhaltlich leer
sein können. Die Diskussion verschiebt sich von „warum entsteht keine Datei" zu
„warum sind die Pläne so dünn", und die Ursache — eine Formulierung, die
Schreiben verlangt, aber nur Struktur prüfbar macht — bleibt unbenannt.

Zweitwahrscheinlich wird jemand die neuen Anweisungssätze beim Aufräumen
sprachlich straffen und dabei den Satz über die Quittungsnatur des JSON oder
die Ausnahme im Änderungsverbot entfernen. Beides ist der Teil, der den
ursprünglichen Fehlschluss verhindert; die Tests würden es bemerken, weil sie
gegen `canonical_json` prüfen — das ist die eigentliche Absicherung dieses
Pakets und sollte nicht abgeschwächt werden.

## 8. Entscheidung, Übernahme und Wiederanlauf

Ich habe keinen ausführbaren Defekt gefunden. Alle acht Zusagen sind erfüllt und
einzeln nachgeprüft, der Stop-Pfad ist vertragskonform, die Fallunterscheidung
ist nicht über Agenteninhalt steuerbar, die Summary leckt nicht, und die Tests
belegen die tatsächlich transportierte Anweisung samt Negativabgrenzung.

**Übernahme:** Der Hotfix darf lokal committed und anschließend auf `master`
sowie auf den Zielbranch `feature/audit-evidence-self-poisoning-guard`
übernommen werden.

**Wiederanlauf:** Ein **frischer** Watch-Run auf der Backlogaufgabe. Das
bestehende `PLAN-CONTRACT-INVALID`-Gate des artefaktlosen Altlaufs
(`watch-20260829-025242.227030Z-58de5b52b10a`) **nicht** genehmigen — es ist ein
pfadloser Stopp ohne Fingerprint, an dem es nichts freizugeben gibt — und diesen
Lauf **nicht** resumen, weil seine eine Reparaturrunde bereits verbraucht ist
(`completed_side_effects: ['automatic-plan-contract-repair']`). Ein Resume liefe
unmittelbar wieder in dasselbe Gate. Keine Records, Checkpoints oder
State-Dateien von Hand anfassen.

```text
REVIEWER: claude
REVIEW_EVIDENCE: selbst erhobener Stand mit Branch feature/plan-only-artifact-write-contract-hotfix, Basis und HEAD c9de3e16938a6206892676864182027f7aa11189, exakt vier Aenderungspfaden und uebereinstimmendem Diff-SHA-256 441649b693601b95e13ed04e81df56cd541b27b34837cbd4fbca6c0db39d309b bei sauberem diff --check; gerenderter PLAN_ONLY-Anweisungsblock gelesen und auf Kohaerenz von Schreibpflicht, Selbstpruefung, Quittungsabgrenzung, Stop-Alternative und entschaerftem Aenderungsverbot geprueft; Stop-Pfad als vertragskonform belegt, weil stop_result fuer PLAN-Anfragen in der Writerschema-Union steht, vor der Erwartungstyppruefung geparst wird und eine unbekannte Regelkennung ueber known_stop_rule_ids abgewiesen wird; Reparaturklassifikation gegen alle acht Validatorfehler des Planpfads gefahren und mit einem konstruierten Handofffehler belegt, dass der fehlend-Zweig ueber Agenteninhalt nicht erreichbar ist, weil sein Praefix ein fester Orchestratorstring ist; Summary-Aenderung als auf PLAN_ONLY plus WorkUnitKind.PLAN ohne gebundenen Slice begrenzt nachgewiesen und _plan_only_step_boundary fuer nicht-PLAN_ONLY als leer bestaetigt; work_plan_path kann in diesem Zweig nicht None sein, per Konstruktionsversuch gegen die Zustandsvalidierung belegt; Near-Miss-Matrix fuer fehlende, markerbasierte, leere, falsch platzierte und nicht lesbare Artefakte durchgespielt, alle enden in gebundener Revision oder fail-closed Gate; Tests pruefen nachweislich das transportierte canonical_json mit positiver und negativer Abgrenzung beider Reparaturzweige; eigene Laeufe 4 passed 73 deselected in 5.56s und 77 passed in 22.62s, waehrend 1134 passed in 148.53s ausdruecklich uebernommene Codex-Angabe bleibt | groesstes Restrisiko: die Zusage haengt an Fliesstext, und geprueft wird nur Existenz, Nichtleere und Handoffstruktur, nicht die inhaltliche Guete des Plans; verstaerkt durch C-01, weil ausgerechnet der wahrscheinlichste Near-Miss eines schlecht kooperierenden Planers keine Reparaturrunde erhaelt | realistische Bruchbedingung: der Planer schreibt ein duennes Alibidokument, die Handoffextraktion scheitert, die eine Revision wird verbraucht, die zweite Fassung bleibt zu duenn, und der Lauf endet nach zwei bezahlten Providerlaeufen im Gate, ohne dass der Orchestrator sich falsch verhalten haette
PRE_MORTEM: In drei Monaten erzeugen PLAN_ONLY-Laeufe zuverlaessig ein Dokument, dessen Qualitaet aber schwankt, weil der Vertrag Existenz, Nichtleere und Handoffstruktur prueft und nicht Substanz. Die Schutzschicht meldet gruen, die Guetepruefung wandert vollstaendig ins Claude-Planreview, und die Diskussion verschiebt sich von warum entsteht keine Datei zu warum sind die Plaene so duenn, ohne die Ursache zu benennen. Zweitwahrscheinlich strafft jemand die neuen Anweisungssaetze sprachlich und entfernt dabei die Quittungsabgrenzung des JSON oder die Ausnahme im Aenderungsverbot; genau diese beiden Saetze verhindern den urspruenglichen Fehlschluss, und nur die canonical_json-Assertions der Tests wuerden es bemerken.
FINAL_APPROVAL: YES
STATUS: DONE
```
