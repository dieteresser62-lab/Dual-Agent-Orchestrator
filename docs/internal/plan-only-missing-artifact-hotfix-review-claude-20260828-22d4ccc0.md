# Claude-Review: Hotfix „fehlendes PLAN_ONLY-Artefakt"

**Reviewer:** Claude (manuell, adversarial, read-only) · **Datum:** 28. August 2026
**Kontrollhash des geprüften Pakets:** `22d4ccc05979aab2dcaaa1db3cfa42445583e1abae14833045f168fe56e56ed0`

Dieses Review ist keine Orchestrator-Attestierung.

## 1. Ergebnis vorab

**Freigabe.** Kein Blocker. Zwei nicht blockierende Beobachtungen.

Der Hotfix trifft die Ursache genau und bleibt dabei eng: Er entfernt eine
Sonderbedingung, die den virtuellen Evidenzpfad ausgerechnet im PLAN_ONLY-Fall
zum genehmigungsfähigen Fremdpfad machte, und stuft die dahinterliegende, bereits
vorhandene Vertragsdiagnose als einmalig reparierbar ein. Die Wirkung habe ich
nicht nur an den mitgelieferten Tests, sondern an sechs eigenen Grenzproben und
neun Klassifikationsproben nachgerechnet.

## 2. Selbst erhobener Stand

| Merkmal | Gemessen | Erwartet | |
|---|---|---|---|
| Branch | `feature/plan-only-missing-artifact-hotfix` | identisch | ✓ |
| `HEAD` / Basis | `d3e1aec0f2a14581a19c2f3f7f000661f8feb350` | identisch | ✓ |
| Geänderte Pfade | `src/workflow.py`, `tests/test_orchestrator_runtime.py` | identisch | ✓ |
| Ungetrackt | `docs/internal/plan-only-missing-artifact-hotfix-20260828.md` | identisch | ✓ |
| Weitere Pfade | keine | keine | ✓ |
| Kontrollhash | `22d4ccc05979aab2dcaaa1db3cfa42445583e1abae14833045f168fe56e56ed0` | identisch | ✓ |

Der Snapshot stimmt in Branch, Basis, Pfadmenge und Kontrollhash exakt mit dem
Auftrag überein.

**Reviewgrenze.** Kein Provider-, Watcher-, Bootstrap- oder Canaryaufruf, keine
Netzzugriffe. Nichts gestaged, committet, gemergt oder gepusht, kein
Branchwechsel. `.orchestrator/state.json`, Checkpoints, Records, Inbox, Outbox
und der fehlgeschlagene Lauf wurden nicht berührt. Die vollständige
Validierungsmatrix habe ich nicht ausgeführt. Einzige Schreiboperation ist dieses
Dokument.

## 3. Selbst ausgeführte Tests

Alle mit `-q -p no:cacheprovider`, providerfrei:

| Umfang | Ergebnis |
|---|---|
| die fünf im Auftrag benannten Knoten | `5 passed in 6.01s` |
| `tests/test_orchestrator_runtime.py` vollständig | `77 passed in 22.51s` |

Die Zahl `77 passed` deckt sich mit der Codex-Angabe für dieselbe Datei; ich
führe sie hier als **eigene** Ausführung. Die vollständige Suite
(`1134 passed in 149.27s`) und der saubere `git diff --check` bleiben
**übernommene Codex-Angaben**, die ich weder reproduziert noch attestiert habe.

## 4. Geprüfte Invarianten

### 4.1 Enge der Pfadausnahme (Anforderung 1 und 6)

Die Ausnahme ist eine **exakte Tupelgleichheit** auf ein Ein-Pfad-Tupel; die
Bedingung `not context.plan_only` entfällt. Ich habe die statische Methode
`WorkflowEngine._validate_change_boundary` direkt mit sechs Pfadmengen gefahren:

| Eingabe | `unexpected` |
|---|---|
| nur `.orchestrator/plan-output.md` | `()` — ausgenommen |
| Marker **plus** `src/foo.py` | `('.orchestrator/plan-output.md', 'src/foo.py')` |
| nur `src/foo.py` | `('src/foo.py',)` |
| nur ein Pfad im Scope | `()` |
| Scope-Pfad **plus** Marker | `('.orchestrator/plan-output.md',)` |
| `.orchestrator/plan-output.md.bak` | `('.orchestrator/plan-output.md.bak',)` |

Damit ist belegt: Der Marker wird ausschließlich allein freigestellt. Mischfälle
werden nicht abgeschwächt — im Gegenteil, im Mischfall wird der Marker selbst
zusätzlich als unerwartet gemeldet. Ein ähnlich benannter Pfad greift nicht.
Fremdpfade behalten unverändert ihre fingerprint- und pfadgebundene Gatebildung.

Zwei strukturelle Gründe kommen hinzu, die die Ausnahme unausnutzbar machen:
`.orchestrator/` ist über `.gitignore:1` ignoriert, und die Änderungserfassung
sammelt ungetrackte Pfade mit `ls-files --others --exclude-standard`
(`repo_changes.py:494`). Eine real angelegte Datei unter diesem Namen erscheint
also gar nicht in `changes.paths`. Der Marker entsteht ausschließlich synthetisch
im `elif`-Zweig von `collect_changes` (`orchestrator.py:2257-2270`), und zwar nur,
wenn es überhaupt keine Repositoryänderung gibt — er kann konstruktiv nie
gemeinsam mit echten Pfaden auftreten.

Die Ausnahme liegt innerhalb des `if kind is WorkUnitKind.PLAN`-Zweiges; Slice-
und Final-Review-Grenzen sind unberührt. Für nicht-PLAN_ONLY-Planläufe ist das
Verhalten unverändert.

### 4.2 Erreichbarkeit der richtigen Diagnose (Anforderung 2)

Nach der Ausnahme läuft der Fluss in `_attestation(..., plan_contract=True)` und
damit in `ProductionWorkflowDriver.validate_plan()`, das den virtuellen Pfad
bereits herausfiltert (`orchestrator.py:2343-2347`) und die konkrete Diagnose
`PLAN_ONLY Codex planning must create or update WORK_PLAN_PATH`
(`orchestrator.py:2388`) erzeugt. Die Analyse im Hotfixdokument, dass der
Ergebnisnachweis nie fehlte, sondern nur unerreichbar war, deckt sich mit dem
Code.

### 4.3 Enge der Reparaturklassifikation (adversarialer Prüfpunkt)

Die neue Bedingung ist `startswith("PLAN_ONLY ")` **und**
`endswith(" planning must create or update WORK_PLAN_PATH")`. Ich habe sie gegen
alle im Quelltext auffindbaren `PLAN_ONLY`-Meldungen und drei fremde Fehler
gefahren:

| Meldung | Klassifikation |
|---|---|
| `PLAN_ONLY Codex planning must create or update WORK_PLAN_PATH` | reparierbar ✓ |
| `PLAN_ONLY requires exactly one executable plan-artifact Slice` | nicht |
| `PLAN_ONLY plan does not include WORK_PLAN_PATH` | nicht |
| `PLAN_ONLY requires exactly one executable Slice for the plan artifact` | nicht |
| `PLAN_ONLY Slice must include the declared WORK_PLAN_PATH` | nicht |
| `PLAN_ONLY requires WORK_PLAN_PATH` | nicht |
| `WORK_PLAN_PATH cannot produce an IMPLEMENT handoff: …` | reparierbar (bestehend) |
| `structured dual-write failed before workflow decision: …` | nicht |
| `native Codex recovery has divergent agent-result records` | nicht |

Kein fremder Fehler wird versehentlich als fehlendes Plandokument eingestuft.
Zur Brittleness der Methode siehe `C-01`.

### 4.4 Begrenzung auf genau einen Reparaturversuch (Anforderung 3 und 5)

Beide reparierbaren Klassen teilen sich den persistenten Schlüssel
`automatic-plan-contract-repair`. Entscheidend für die Resumefestigkeit ist die
Reihenfolge im Code: `mark_side_effect_completed(repair_key)` →
`with_current_step(CODEX_PLAN_REVISION)` → `driver.checkpoint(...)` → **erst
danach** `_run_codex(...)`. Der Verbrauch ist damit persistiert, bevor der
Providerlauf beginnt; ein Absturz während der Revision kann keinen zweiten
Versuch freischalten. `completed_side_effects` ist Teil des serialisierten
Work-Unit-Records.

Dass sich beide Klassen einen Schlüssel teilen, bedeutet: ein Lauf erhält **eine**
Reparatur insgesamt, nicht eine je Fehlerklasse. Repariert ein Lauf zuerst das
fehlende Artefakt und liefert die Revision anschließend einen nicht
handofffähigen Plan, endet er sofort im Policygate. Das ist die konservative
Richtung und aus meiner Sicht korrekt.

### 4.5 Reviewerbudget und zweiter Fehlschlag (Anforderung 4 und 5)

`_validate_plan_before_review` wird in `_run_codex` aufgerufen, **bevor** der
Schritt auf `CLAUDE_PLAN_REVIEW` gesetzt wird (`workflow.py:1442-1453`); bei
`halted=True` kehrt die Funktion vorher zurück. Ein Reviewaufruf ist damit vor
einem existierenden, validierten Plan strukturell unerreichbar.

Der zweite Fehlschlag endet in `await_policy_gate(STOP_REQUEST,
"PLAN-CONTRACT-INVALID | …")` ohne Pfade und ohne Fingerprint — also fail-closed
und ohne autorisierbare Scheindatei. Der mitgelieferte Test behauptet genau das:
`exit_code == 4`, `AWAITING_USER_DECISION`, `GateReason.STOP_REQUEST`,
`"PLAN-CONTRACT-INVALID" in detail`, **`gate.paths == ()`**,
`codex_steps == [CODEX_PLAN, CODEX_PLAN_REVISION]` und **`reviewer_steps == []`**.

### 4.6 Testtiefe (adversarialer Prüfpunkt)

Beide neuen Tests fahren `run_production_workflow(task, _args(repository, task))`,
also den echten Ablauf, und behaupten die **Agentenschrittfolge** statt interner
Helfer. Der Erfolgsfall belegt zusätzlich `reviewer_steps == [CLAUDE_PLAN_REVIEW]`
(genau ein Reviewaufruf) und die Existenz der erzeugten Handoffdatei. Der
bestehende Fall für nicht handofffähige Pläne ist unverändert grün, die
bounded-repair-Semantik dort also nicht regressiert.

## 5. Findings

### C-01 — Reparaturklassifikation über Meldungstext statt typisierten Code (OBSERVATION)

Die neue Bedingung erkennt den Fehlerfall an Präfix und Suffix des
Ausnahmetextes. Heute ist das nachweislich eng genug (Abschnitt 4.3), aber es
koppelt Verhalten an Wortlaut: Eine spätere Umformulierung von
`orchestrator.py:2388` lässt die Reparatur stillschweigend ausfallen — der Lauf
ginge dann ohne Revision direkt ins `PLAN-CONTRACT-INVALID`-Gate, ohne dass ein
Test das zwingend bemerkt. Umgekehrt würde eine neue Meldung mit exakt diesem
Suffix automatisch als reparierbar gelten.

Der Hotfix erbt diese Schwäche von der bestehenden Schwesterbedingung
(`WORK_PLAN_PATH cannot produce an IMPLEMENT handoff:`) und verschlechtert nichts;
er verdoppelt sie aber.

**Disposition:** nicht blockierend. Der Fehlerpfad bleibt in jedem Fall
fail-closed; das schlimmste Ergebnis einer Wortlautänderung ist ein Gate statt
einer Reparatur, nicht ein durchgelassener ungültiger Plan.
**Akzeptanztest:** Ein typisierter Fehlercode oder eine geteilte Konstante trägt
die Diagnose; ein Test bindet Erzeuger (`validate_plan`) und Klassifikator
(`_validate_plan_before_review`) an dieselbe Konstante, sodass eine Umformulierung
an genau einer Stelle rot wird.

### C-02 — Falsche Basisangabe im Hotfixdokument (OBSERVATION)

`docs/internal/plan-only-missing-artifact-hotfix-20260828.md` nennt als Basis
`d3e1aecf0ec6d19b783cdb6957f1da048a842b7d`. Dieses Objekt existiert im
Repository nicht:

```
$ git cat-file -t d3e1aecf0ec6d19b783cdb6957f1da048a842b7d
fatal: git cat-file: could not get object info
```

Die tatsächliche Basis und der aktuelle HEAD sind
`d3e1aec0f2a14581a19c2f3f7f000661f8feb350`. Beide teilen das Kurzpräfix
`d3e1aec`, weshalb der Fehler auf den ersten Blick nicht auffällt.

**Disposition:** nicht blockierend. Es ist eine Herkunftsangabe, kein
Verifikationsergebnis: Die Korrektheit des Fixes habe ich unabhängig gegen den
tatsächlichen Snapshot geprüft, und der Kontrollhash bindet das Paket eindeutig.
Ich stufe das bewusst niedriger ein als eine frühere vergleichbare Beanstandung,
bei der ein Abschlussnachweis ein *Validierungsergebnis* für einen anderen Stand
behauptete — hier wird nichts Falsches über eine Prüfung ausgesagt.
**Akzeptanztest:** Die Basiszeile nennt
`d3e1aec0f2a14581a19c2f3f7f000661f8feb350`, und `git cat-file -t <SHA>` liefert
`commit`.

## 6. Größtes Restrisiko und realistische Bruchbedingung

Größtes Restrisiko ist die **Vertragsbindung über Zeichenketten** aus `C-01`.
Sie ist die einzige Stelle des Hotfixes, deren Wirkung nicht strukturell,
sondern textuell abgesichert ist.

Nachgeordnet bleibt eine Frage, die dieser Hotfix bewusst nicht beantwortet: Er
repariert die *Behandlung* eines Planers, der ohne Ergebnis zurückkehrt, nicht
dessen *Ursache*. Der auslösende Lauf meldete nach 15,89 s `ready: true` ohne
Datei. Wiederholt sich das, kostet nun jeder Lauf zusätzlich eine Planrevision,
bevor er im Policygate endet — das ist die richtige, aber teurere Reaktion.

**Realistische Bruchbedingung:** Jemand formuliert die Diagnose in
`orchestrator.py:2388` um — etwa auf „PLAN_ONLY planning did not create
WORK_PLAN_PATH" — weil sie sprachlich besser klingt. Die Klassifikation greift
nicht mehr, die bounded repair entfällt lautlos, und jeder betroffene Lauf hält
sofort im `PLAN-CONTRACT-INVALID`-Gate. Die vorhandenen Tests decken das nicht
ab, weil sie den Fehler über den echten Ablauf erzeugen und die Meldung
mitwandert.

## 7. Pre-Mortem

In drei Monaten fällt auf, dass PLAN_ONLY-Läufe mit fehlendem Artefakt nicht mehr
nachgebessert werden, sondern sofort im Policygate landen. Ursache ist eine
harmlose Umformulierung der Validatormeldung Wochen zuvor; die Klassifikation in
`_validate_plan_before_review` prüft weiterhin auf ein Suffix, das es nicht mehr
gibt. Weil das Ergebnis ein sauberes, fail-closed Gate ist, wirkt es wie
gewolltes Verhalten, und niemand sucht nach der verlorenen Reparaturrunde. Erst
ein Vergleich mit diesem Reviewdokument zeigt, dass es einmal eine gebundene
Revision gab.

Zweitwahrscheinlich wiederholt sich der auslösende Providerfehler häufiger als
angenommen. Dann zahlt jeder Lauf einen zusätzlichen Codex-Aufruf, bevor er
anhält, und die Diskussion verschiebt sich von „warum gatet der Orchestrator" zu
„warum liefert der Planer leer" — das ist die richtige Frage, aber sie stellt
sich dann teurer.

## 8. Entscheidung

Beide Kernwirkungen sind erfüllt und einzeln nachgerechnet: Der virtuelle Marker
ist ausschließlich als exaktes Ein-Pfad-Tupel ausgenommen, echte und gemischte
Fremdpfade bleiben unverändert gatepflichtig, die Vertragsdiagnose ist erreichbar,
die Reparatur ist über einen vor dem Providerlauf persistierten Schlüssel auf
genau einen Versuch begrenzt, der zweite Fehlschlag endet pfadlos und fail-closed,
und Reviewerbudget wird strukturell erst nach einem validierten Plan ausgegeben.
Die Tests belegen das über den echten `run_production_workflow`-Ablauf.

Die betriebliche Empfehlung im Hotfixdokument ist korrekt: das alte
`UNEXPECTED-PATH`-Gate nicht genehmigen, keine strukturierten Records von Hand
reparieren und die Backlogaufgabe nach Freigabe und Integration als neuen Lauf
von `master` starten.

Ich gebe den Hotfix frei. Die beiden Beobachtungen erfordern keine Änderung vor
der Übernahme.

```text
REVIEWER: claude
REVIEW_EVIDENCE: selbst erhobener Stand mit Branch feature/plan-only-missing-artifact-hotfix, Basis und HEAD d3e1aec0f2a14581a19c2f3f7f000661f8feb350, exakt drei Paketpfaden und uebereinstimmendem Kontrollhash 22d4ccc05979aab2dcaaa1db3cfa42445583e1abae14833045f168fe56e56ed0; sechs eigene Grenzproben gegen _validate_change_boundary belegen die exakte Ein-Pfad-Ausnahme und dass Mischfaelle, echte Fremdpfade und aehnlich benannte Pfade unveraendert gemeldet werden, im Mischfall sogar zusaetzlich der Marker selbst; strukturell abgesichert durch .gitignore und ls-files --exclude-standard, sodass eine reale Datei dieses Namens gar nicht in changes.paths erscheinen kann, und durch den elif-Zweig von collect_changes, der den Marker nur bei voelliger Aenderungsfreiheit erzeugt; neun Klassifikationsproben zeigen, dass ausser der Zieldiagnose keine der uebrigen PLAN_ONLY-Meldungen und kein fremder Fehler als reparierbar gilt; Reihenfolge mark_side_effect_completed vor checkpoint vor _run_codex belegt die Resumefestigkeit der Einmalreparatur; _validate_plan_before_review laeuft vor dem Wechsel auf CLAUDE_PLAN_REVIEW, sodass Reviewerbudget erst nach validiertem Plan faellt; der zweite Fehlschlag endet pfadlos in PLAN-CONTRACT-INVALID; beide neuen Tests fahren den echten run_production_workflow und behaupten Agentenschrittfolge, reviewer_steps und gate.paths; eigene Laeufe 5 passed in 6.01s und 77 passed in 22.51s, waehrend die vollstaendige Suite 1134 passed und der Diffcheck ausdruecklich uebernommene Codex-Angaben bleiben | groesstes Restrisiko: die Reparaturklassifikation ist ueber Praefix und Suffix des Ausnahmetextes an einen Wortlaut gekoppelt statt an einen typisierten Code, und der Hotfix repariert die Behandlung eines ergebnislos zurueckkehrenden Planers, nicht dessen Ursache | realistische Bruchbedingung: die Diagnose in orchestrator.py:2388 wird sprachlich umformuliert, die Klassifikation greift nicht mehr, die gebundene Planrevision entfaellt lautlos und jeder betroffene Lauf haelt sofort im PLAN-CONTRACT-INVALID-Gate, was wie gewolltes Verhalten wirkt
PRE_MORTEM: In drei Monaten werden PLAN_ONLY-Laeufe mit fehlendem Artefakt nicht mehr nachgebessert, sondern halten sofort im Policygate. Ursache ist eine harmlose Umformulierung der Validatormeldung Wochen zuvor; die Suffixpruefung in _validate_plan_before_review trifft nicht mehr zu. Weil das Ergebnis ein sauberes fail-closed Gate ist, wirkt es wie gewolltes Verhalten, und die verlorene Reparaturrunde faellt niemandem auf; erst ein Vergleich mit diesem Reviewdokument zeigt, dass es sie einmal gab. Zweitwahrscheinlich wiederholt sich der ausloesende Providerfehler haeufiger als angenommen, sodass jeder betroffene Lauf zusaetzlich einen Codex-Aufruf bezahlt, bevor er anhaelt.
FINAL_APPROVAL: YES
STATUS: DONE
```
