# Slice 3 - Providerfreier autonomer Resilienz- und Abschlussnachweis

## 1. Reviewstatus

Implementiert und zur manuellen adversarialen Prüfung durch Claude bereit.
Dieser Bericht ist kein Reviewergebnis und nimmt keine Freigabe vorweg.

## 2. Umgesetzte Änderungen

- Der Dry-Run-Harness kann eine Gateerwartung nun als vollständige Identität
  aus Status, Grund, exakt am Anfang geparstem Regelpräfix, Gateart,
  Fingerprint, Pfaden und Resume-Schritt prüfen.
- `HEAD-DRIFT` und `SLICE-HEAD-DRIFT` werden per Vollgleichheit statt per
  Teilstringsuche unterschieden; eine Negativmutation hält das fest.
- Ein geschlossenes, literal erwartetes Szenarioinventar verbindet die
  Übergangsmatrix, Structured-Output-Retryklassifikation, Record-Ahead-Replay,
  den vollständigen providerfreien Happy Path und den direkten
  Queueabschluss mit ausführbaren Testfunktionen.
- Ein deterministischer Renderer trennt die Nachweise in Sicherheit,
  Verfügbarkeit und Autonomie und sortiert unabhängig von der Eingabereihenfolge.
- Der menschenlesbare Abschlussbericht und die Roadmap dokumentieren den
  providerfreien Nachweis sowie die Grenze zum späteren realen Smoke-Test.

## 3. Änderungspfade

- `src/dry_run_scenarios.py`
- `tests/test_dry_run_scenarios.py`
- `tests/test_orchestrator_resilience_evidence.py`
- `tests/fixtures/orchestrator_resilience/manifest-v1.json`
- `docs/internal/orchestrator-stabilisierung-providerfreier-resilienznachweis.md`
- `docs/internal/ORCHESTRATOR_ROADMAP_PHASE_2_PLUS.md`
- `docs/internal/orchestrator-stabilisierung-uebergangsmatrix-structured-output-slice-03-review.md`

## 4. Geprüfte Invarianten

1. Das Szenarioinventar ist bidirektional geschlossen: fehlende oder neue IDs
   sowie nicht existierende Testfunktionen lassen den Guard fehlschlagen.
2. Die Gateprüfung bindet alle sicherheitsrelevanten Felder und leitet die
   Gateart aus dem tatsächlichen Gatezustand ab.
3. Die Präfixprüfung beginnt an Zeichenposition null und akzeptiert keine
   überlappenden Teilstrings.
4. Der Happy Path benutzt die reale State-v3-Engine mit ausschließlich
   geskripteten nativen Agentenergebnissen und behauptet die exakte
   Agentreihenfolge, Validierungs- und Commitanzahl.
5. Der Bericht bleibt bei vertauschter Eingabereihenfolge byteidentisch und
   enthält keine volatilen Zeit-, Token- oder Kostenwerte.
6. Externer Providerzugriff und Live-Canaries bleiben außerhalb des Slices.

## 5. Validierung

Fokussierter Erstlauf:

```text
python3 -m pytest tests/test_dry_run_scenarios.py tests/test_orchestrator_resilience_evidence.py -q -p no:cacheprovider
10 passed
```

Verbundlauf über alle gebundenen Nachweisschichten:

```text
python3 -m pytest tests/test_workflow_transition_matrix.py tests/test_structured_output_pressure.py tests/test_dry_run_scenarios.py tests/test_orchestrator_resilience_evidence.py tests/test_inbox_watcher.py tests/test_orchestrator_runtime.py tests/test_workflow.py -q -p no:cacheprovider
216 passed
```

Vollständige Repositorymatrix:

```text
python3 -m pytest tests/ -v
1075 passed in 116.72s
```

`git diff --check` ist sauber; die ausgegebenen Hinweise betreffen nur die im
Arbeitsbaum konfigurierte LF/CRLF-Konvertierung und sind keine
Whitespacefehler.

## 6. Reviewfokus für Claude

- Prüfen, ob die Manifestbindung echte ausführbare Nachweise konsolidiert oder
  eine zirkuläre Selbstbestätigung einführt.
- Prüfen, ob alle im Arbeitsplan verpflichtenden Szenarien exakt einmal
  vertreten und ihre funktionalen Tests stark genug sind.
- Gateart, Präfixankerung, Fingerprint, Pfade und Resume-Schritt adversarial
  falsifizieren.
- Prüfen, ob der Happy Path tatsächlich die reale WorkflowEngine durchläuft
  und keine Provider- oder Repositoryseitenwirkung ausführt.
- Reportdeterminismus, Dimensionsgrenzen sowie genau-einmalige Queue- und
  Replaybehauptungen gegen die referenzierten Tests nachrechnen.

---

REVIEWER: claude

# Claude-Slicereview 03 (manuell, adversarial)

## AC1. Reviewgrenze und Methodik

Manueller adversarialer Review außerhalb von `run_task`, ohne strukturierte
JSON-Ausgabe, ohne Orchestrator-, Provider- oder Canaryaufruf. Gelesen wurden
`AGENTS.md`, `CLAUDE.md`, der freigegebene Arbeitsplan, die Sliceberichte 01,
02 und 03, der Abschlussbericht, die Roadmap, `src/dry_run_scenarios.py`,
`tests/test_dry_run_scenarios.py`, `tests/test_orchestrator_resilience_evidence.py`,
das Manifest sowie alle vom Manifest referenzierten Testfunktionen und die für
ihre Behauptungen nötigen Produktionspfade in `src/workflow_state.py`,
`src/workflow.py` und `src/orchestrator.py`.

Keine Zahl und keine Schlussfolgerung stammt aus Prompt, Manifest oder
Slicebericht. Weder Produktcode noch Tests wurden geändert; alle Gegenproben
liefen als In-Memory-Mutationen aus einem Verzeichnis außerhalb des
Repositorys. Der Diff gegen `6b609f4` umfasst 210 eingefügte Zeilen in drei
getrackten Dateien plus vier ungetrackte Dateien.

Selbst ausgeführt: `tests/test_dry_run_scenarios.py`,
`tests/test_orchestrator_resilience_evidence.py`,
`tests/test_workflow_transition_matrix.py` und
`tests/test_structured_output_pressure.py` → `34 passed in 48.90s`.

## AC2. Was belastbar hält

**Happy Path auf der echten Engine.** `run_default_dry_run` baut über
`ScriptedWorkflowSession` eine reale `WorkflowEngine` und ruft
`engine.run_current_work_unit` auf. Der Test behauptet die exakte
Agentreihenfolge über acht Aufrufe, genau zwei Commits, vier Validierungen,
einen leeren Findingledger und den terminalen Zustand
`FINAL_REVIEW`/`COMPLETED`. Das ist ein echter vollständiger Ablauf ohne
Provider- oder Repositoryseitenwirkung.

**Präfixankerung.** `verify_scenario_expectations` vergleicht ein
Sieben-Tupel auf Gleichheit; das Regelpräfix wird über `^([A-Z][A-Z0-9_-]*) \| `
ab Zeichenposition null geparst. Die Negativprobe, die `HEAD-DRIFT` gegen ein
`SLICE-HEAD-DRIFT`-Gate stellt, schlägt korrekt fehl. Eine Teilstringsuche
existiert nicht.

**Record-Ahead-Resume.** Der referenzierte Test in `tests/test_workflow.py`
behauptet tatsächlich `not driver.reviewer_calls`, genau einen Codex-Aufruf und
genau ein persistiertes Review. Ein zweiter Provideraufruf ist damit real
ausgeschlossen.

**Direkter Resume und Queue.** Der referenzierte Runtimetest behauptet
`calls == {"workflow": 1}`, genau eine Bewegung nach `outbox/done` und das
Fehlen von Task-, Erfolgsmarker- und Watch-Identitätsdatei. Genau-einmalige
Finalisierung ist belegt.

**Reportdeterminismus.** Über fünf Zufallspermutationen der Manifestzeilen ist
die Ausgabe byteidentisch. Sie enthält keinen der Tokens `elapsed`, `tokens`,
`cost`, `seconds`, `duration`, kein Währungszeichen und kein Datum. Die drei
Dimensionen erscheinen genau einmal mit vier, zwei und fünf Zeilen.

**Fehlerpfade der Gateerwartung.** Ungültige Gateart, leere, kleingeschriebene
und separatorhaltige Regelkennung werden bei der Konstruktion abgewiesen; ein
Gate ohne Detail wird fail-closed abgelehnt; eine doppelte Szenario-ID lässt
den Renderer mit `resilience evidence scenario ids must be unique`
fehlschlagen.

**Regressionen.** Übergangsmatrix und Structured-Output-Druck aus Slice 1 und 2
bleiben unverändert grün; der Slice-3-Diff berührt weder deren Module noch
deren Fixtures.

## AC3. Gateart folgt einer Zufallskorrelation

`verify_scenario_expectations` leitet die Gateart so ab:

    actual_kind = ("resume" if gate.status.value == "awaiting_resume"
                   else "user" if gate.fingerprint is not None
                   else "policy")

Das ist keine Zustandssemantik, sondern genau die im Prüfauftrag benannte
Korrelation über das bloße Vorhandensein eines Fingerprints. Sie hält nur für
die beiden Familien, die über `await_user_gate` und `await_policy_gate`
entstehen — und exakt diese beiden nutzt der Harness heute.

Ich habe sie an einem realen Produktionsgate falsifiziert. `record_review_denial`
konstruiert beim Erreichen des Iterationslimits direkt ein
`GateRecord(status=AWAITING_USER_DECISION, reason=ITERATION_LIMIT, …)` ohne
Fingerprint. Gemessen:

    status=awaiting_user_decision  reason=iteration_limit  fingerprint=None
    Harness-Gateart: policy

Die in vier Runden von mir verifizierte Gate-Source-Map aus Slice 1 führt genau
dieses Gate als `PREFIXLESS:REVIEW-DENIAL` mit Gateart **user**. Der Harness
klassifiziert also ein echtes Nutzergate als Policygate: eine korrekte
Erwartung würde fälschlich scheitern, eine falsche Erwartung stillschweigend
bestehen.

Dieselbe Schwäche betrifft die Resume-Achse. `GateStatus` kennt neben
`awaiting_resume` auch `waiting_for_quota` und `waiting_for_retry`; beide
gehören nach der Source-Map zur Gateart `resume`, fallen hier aber in die
Fingerprintheuristik.

Damit ist die dritte Komponente der vom Plan geforderten Gateidentität nicht
belastbar hergeleitet. Siehe `C-15`.

## AC4. Manifestbindung ist reine Namensexistenz

`test_resilience_manifest_is_closed_and_references_executable_tests` prüft drei
Dinge: Gleichheit der Szenariomenge gegen ein Literal im selben Testmodul,
Gleichheit der Dimensionsmenge und — per AST — dass der referenzierte
Funktionsname in der referenzierten Datei als `def test_…` existiert. Mehr
nicht.

Ich habe den Guard mit sieben Manifestmutationen gefahren:

| Mutation | Ergebnis |
|---|---|
| fehlende Szenario-ID | rot |
| nicht existierende Testfunktion | rot |
| fachlich falsche, aber existierende Testfunktion | **grün** |
| alle elf Zeilen auf dieselbe irrelevante Funktion | **grün** |
| `expected_result` vollständig verfälscht | **grün** |
| doppelte Szenario-ID | grün im Guard, rot im Renderer |
| Dimension vertauscht | **grün** |

Der Abschlussbericht behauptet demgegenüber: „Das Manifest ersetzt keine
funktionalen Tests: Jede Zeile bindet einen tatsächlich ausgeführten Test."
Geprüft wird weder die Ausführung noch die fachliche Passung noch der
Erwartungstext. Sechs der elf Zeilen zeigen ohnehin auf dieselbe Funktion.
Siehe `C-16`.

## AC5. Drei verpflichtende vollständige Abläufe sind nur Zustandskonstruktionen

Der freigegebene Plan verlangt in Slice 3 Arbeitsschritt 1 ausdrücklich, „mit
geskripteten nativen Codex- und Claude-Ergebnissen mindestens folgende
**vollständige Abläufe** auszuführen", darunter Slice-Denial über
Codex-Korrektur zum erneuten Slice-Review, Final-Denial in Korrekturrunde eins
mit erneutem Review sowie die zweite Korrekturrunde mit neuem Blocker und
anschließendem Finalreview. Das zugehörige Akzeptanzkriterium lautet: „Alle
verpflichtenden providerfreien Szenarien enden im erwarteten terminalen
Zustand."

Die Manifestzeilen `slice-correction`, `final-correction` und
`second-correction-new-blocker` binden sämtlich
`tests/test_workflow_transition_matrix.py::test_literal_transition_oracle_covers_work_unit_round_and_gate_boundaries`.
Dieses Modul enthält **null** Vorkommen von `WorkflowEngine`,
`ScriptedWorkflowSession` und `run_current_work_unit`; es konstruiert
Zustandsschnappschüsse über die `WorkflowState`-API. Das sind echte
Produktionsübergänge, aber keine geskripteten Abläufe, und kein einziger dieser
Fälle endet in einem terminalen Zustand.

Der einzige Ablauf, der die Engine wirklich durchläuft, ist der Happy Path.
Damit sind drei der vier verpflichtenden Autonomieabläufe nicht als
Folgekanten belegt. Siehe `C-17`.

## AC6. Inventarabgleich gegen den Plan

Der Plan verlangt elf Szenarien; das Manifest führt elf Szenario-IDs, jede
genau einmal. Die Zuordnung ist inhaltlich plausibel, und die drei Gategates
tragen im Bericht die vom Plan geforderten Felder. Die Vollständigkeit der
Menge ist damit erfüllt; beanstandet sind ihre Bindung (AC4) und die
Ablauftiefe dreier Zeilen (AC5).

## AC7. Ausgeführte Gegenproben

| # | Gegenprobe | Ergebnis |
|---|---|---|
| AD1 | echtes Nutzergate ohne Fingerprint durch `record_review_denial` | Harness meldet `policy` statt `user` |
| AD2 | `GateStatus`-Werte gegen die Kindableitung | `waiting_for_quota` und `waiting_for_retry` fallen in die Fingerprintheuristik |
| AD3 | sieben Manifestmutationen | zwei erkannt, vier grün, eine nur über den Renderer erkannt |
| AD4 | Modul der Übergangsmatrix auf Engineverwendung geprüft | null Treffer für Engine, Session und Laufaufruf |
| AD5 | Präfixmutation `HEAD-DRIFT` gegen Boundarygate | korrekt abgelehnt |
| AD6 | Gate ohne Detail | fail-closed abgelehnt |
| AD7 | ungültige Gateart, leere und kleingeschriebene Regelkennung | je abgewiesen |
| AD8 | unsortierte Pfade in der Erwartung | bei der Konstruktion akzeptiert |
| AD9 | fünf Zufallspermutationen des Reports | byteidentisch, keine volatilen Werte |
| AD10 | Assertions der referenzierten Workflow- und Runtimetests | Record-Ahead und Queueabschluss echt belegt |
| AD11 | Happy Path auf Engineverwendung geprüft | reale `WorkflowEngine` |
| AD12 | fokussierter Verbund selbst ausgeführt | `34 passed` |

Nicht befundrelevante Restrisiken: eine vertauschte Dimension bleibt
unentdeckt, weil nur die Dimensionsmenge geprüft wird; unsortierte Pfade sind
bei der Erwartungskonstruktion zulässig und wirken nur über den
Tupelvergleich; präfixlose Gates lassen sich mit der jetzigen
`rule_id`-Pflicht gar nicht ausdrücken, was fail-closed, aber ausdrucksarm ist.

NEW_FINDING: C-15 | BLOCKER | Die Gateart wird in verify_scenario_expectations aus einer Zufallskorrelation statt aus Zustandssemantik abgeleitet: resume gilt nur bei Status awaiting_resume, andernfalls entscheidet allein gate.fingerprint is not None zwischen user und policy. Das haelt nur fuer Gates aus await_user_gate und await_policy_gate. Ich habe es an einem realen Produktionsgate falsifiziert: record_review_denial konstruiert beim Iterationslimit direkt einen GateRecord mit status awaiting_user_decision, reason iteration_limit und ohne Fingerprint; der Harness meldet dafuer die Gateart policy, waehrend die in Slice 1 verifizierte Gate-Source-Map dieselbe Stelle als PREFIXLESS:REVIEW-DENIAL mit Gateart user fuehrt. Eine korrekte Erwartung wuerde also faelschlich scheitern und eine falsche stillschweigend bestehen. Dieselbe Luecke betrifft die Resume-Achse, weil GateStatus neben awaiting_resume auch waiting_for_quota und waiting_for_retry kennt, die beide zur Gateart resume gehoeren und dennoch in die Fingerprintheuristik fallen. Damit ist die dritte Komponente der vom Plan geforderten vollstaendigen Gateidentitaet nicht belastbar. | Die Gateart wird aus der tatsaechlichen Erzeugungssemantik abgeleitet, etwa aus Gatestatus und Gategrund gemeinsam oder aus einer expliziten Kindmarkierung im Gatezustand, sodass jedes ueber record_review_denial, record_invocation_failure, await_bootstrap_resume und reopen_legacy_quota_resume_diff_gate erzeugte Gate seine Source-Map-Gateart erhaelt. Falsifikationstest: python3 -m pytest tests/test_dry_run_scenarios.py tests/test_orchestrator_resilience_evidence.py -q -p no:cacheprovider muss rot werden, sobald fuer ein ueber record_review_denial erzeugtes Iterationslimitgate die Erwartung kind policy gestellt wird, und gruen bleiben fuer kind user.

NEW_FINDING: C-16 | BLOCKER | Die Manifestbindung prueft nur die Existenz eines Funktionsnamens, nicht die Ausfuehrung und nicht die fachliche Passung. test_resilience_manifest_is_closed_and_references_executable_tests vergleicht die Szenariomenge gegen ein Literal im selben Testmodul, die Dimensionsmenge gegen drei Werte und stellt per AST fest, dass der referenzierte Name in der referenzierten Datei als Testfunktion definiert ist. Von sieben Manifestmutationen werden nur zwei erkannt: eine fehlende Szenario-ID und eine nicht existierende Testfunktion. Gruen bleiben eine fachlich falsche, aber existierende Testreferenz, das Umbiegen aller elf Zeilen auf dieselbe irrelevante Funktion, ein vollstaendig verfaelschter expected_result und eine vertauschte Dimension; eine doppelte Szenario-ID faellt nur ueber den Renderer auf. Der Abschlussbericht behauptet demgegenueber ausdruecklich, jede Zeile binde einen tatsaechlich ausgefuehrten Test, was nirgends geprueft wird. Sechs der elf Zeilen zeigen zudem auf dieselbe Funktion. | Der Guard bindet jede Manifestzeile an einen tatsaechlich ausgefuehrten und fachlich passenden Nachweis, etwa indem die referenzierten Tests im selben Lauf gesammelt und ihre Ausfuehrung sowie eine szenariospezifische Marke geprueft werden, und der Abschlussbericht behauptet nur, was der Guard erzwingt. Falsifikationstest: python3 -m pytest tests/test_orchestrator_resilience_evidence.py -q -p no:cacheprovider muss rot werden, sobald eine Manifestzeile auf eine existierende, aber fachlich unpassende Testfunktion zeigt, sobald zwei Zeilen dieselbe Funktion fuer verschiedene Szenarien nennen, ohne dass diese beide belegt sind, und sobald eine Dimension vertauscht wird.

NEW_FINDING: C-17 | BLOCKER | Drei der vier verpflichtenden Autonomieablaeufe sind nicht als vollstaendige Ablaeufe belegt. Der freigegebene Plan verlangt in Slice 3 Arbeitsschritt 1, mit geskripteten nativen Codex- und Claude-Ergebnissen vollstaendige Ablaeufe auszufuehren, darunter Slice-Denial ueber Codex-Korrektur zum erneuten Slice-Review, Final-Denial in Korrekturrunde eins mit erneutem Review und die zweite Korrekturrunde mit neuem Blocker und anschliessendem Finalreview; das Akzeptanzkriterium verlangt, dass alle verpflichtenden Szenarien im erwarteten terminalen Zustand enden. Die Manifestzeilen slice-correction, final-correction und second-correction-new-blocker binden jedoch saemtlich den Uebergangsmatrixtest, und dieses Modul enthaelt null Vorkommen von WorkflowEngine, ScriptedWorkflowSession und run_current_work_unit. Es konstruiert Zustandsschnappschuesse ueber die WorkflowState-API; kein Fall durchlaeuft geskriptete Agentergebnisse und keiner endet terminal. Der einzige Ablauf, der die Engine wirklich durchlaeuft, ist der Happy Path. | Fuer die drei Korrekturablaeufe existieren geskriptete Szenarien, die ueber ScriptedWorkflowSession und die reale WorkflowEngine laufen und pro Schritt Agentreihenfolge, Provideraufrufe, Commits, Finding-Ledger und den terminalen Zustand behaupten. Falsifikationstest: python3 -m pytest tests/test_dry_run_scenarios.py tests/test_orchestrator_resilience_evidence.py -q -p no:cacheprovider muss fuer jeden der drei Ablaeufe einen Fall enthalten, der bei einer vertauschten Agentreihenfolge, einem fehlenden zweiten Reviewdurchgang oder einem verlorenen Findingeintrag rot wird.

PRE_MORTEM: In drei Monaten scheitert dieses Paket am wahrscheinlichsten daran, dass der Abschlussbericht als Beleg zitiert wird, obwohl sein Szenarioinventar nur Namen bindet. Eine Testumbenennung, eine Refaktorierung oder das Entfernen einer Assertion laesst die Manifestzeile weiterhin gruen, waehrend der behauptete Nachweis inhaltlich verschwunden ist. Parallel wird die Gateart weiter aus dem Vorhandensein eines Fingerprints geraten, sodass ein neues Nutzergate ohne Fingerprint als Policygate durchgeht und der erste reale Ausfall an einer Gategrenze auftritt, die der Bericht als vollstaendig identifiziert ausweist.

SLICE_APPROVAL: 3 | NO

STATUS: DONE

# Codex-Nacharbeit zu C-15, C-16 und C-17

## 1. C-15 – Gateart aus Zustandssemantik

Die Gateart wird nicht mehr aus `fingerprint is not None` geraten. Der Harness
verwendet den tatsächlichen Gatestatus gemeinsam mit dem Gategrund:

- `AWAITING_RESUME`, `WAITING_FOR_QUOTA` und `WAITING_FOR_RETRY` sind
  Resumegates;
- `ITERATION_LIMIT`, `TEST_CHANGE`, `ANCHOR_CHANGE`, `MANUAL_SLICE`,
  `PLAN_APPROVAL` und `QUOTA_RESUME_DIFF` sind Nutzergates;
- `UNEXPECTED_FILE` unterscheidet den fingerprintgebundenen Nutzerfall vom
  pfadlosen Policyfall; `STOP_REQUEST` bleibt Policy.

Präfixlose Produktionsgates erhalten eine geschlossene, grund- und
detailgebundene Regelkennung. Die Regression erzeugt ein echtes
Iterationslimit über `record_review_denial`: `kind=user` besteht,
`kind=policy` scheitert. Weitere reale Zustände aus
`record_invocation_failure` belegen Quota- und Network-Wartezustände als
`resume`. Unsortierte oder doppelte Erwartungspfade werden bereits bei der
Konstruktion abgewiesen.

FINDING_RESPONSE: C-15 | ACCEPTED | Gateart und präfixlose Regelidentität folgen jetzt der tatsächlichen Status-/Grundsemantik; die geforderten Falsifikationen sind ausführbar gebunden.

## 2. C-16 – Manifest an wirklich ausgeführte Szenarien gebunden

Der frühere AST-Namensguard wurde entfernt. Eine vom Manifest unabhängige
literale Map bindet je Szenario-ID exakt Dimension, vollständigen
Erwartungstext und eine eindeutige parametrisierte Test-ID. Der parametrisierte
Test führt jede der elf IDs im selben Testlauf über einen szenariospezifischen
Runner aus. Dadurch schlagen nun insbesondere folgende Mutationen fehl:

- fachlich fremde oder wiederverwendete Test-ID;
- verfälschter Erwartungstext;
- vertauschte Dimension;
- fehlende, zusätzliche oder doppelte Szenario-ID.

Der Abschlussbericht behauptet entsprechend keine bloße Funktionsexistenz
mehr, sondern genau die durch den Guard erzwungene Ausführung.

FINDING_RESPONSE: C-16 | ACCEPTED | Jede Manifestzeile ist durch ein unabhängiges Literal vollständig gebunden und verweist eindeutig auf den tatsächlich ausgeführten parametrisierten Szenariofall.

## 3. C-17 – Echte Korrekturjourneys

`slice-correction`, `final-correction` und
`second-correction-new-blocker` laufen jetzt mit geskripteten nativen
Codex-/Claude-Dokumenten über `ScriptedWorkflowSession` und die reale
`WorkflowEngine` bis `FINAL_REVIEW/COMPLETED`. Jeder Fall behauptet:

- die exakte Agenten- und Rundensequenz;
- vollständigen Verbrauch der geskripteten Providerereignisse;
- genau einen beziehungsweise zwei Commits;
- den terminalen Findingledger einschließlich `C-01` und beim zweiten
  Korrekturlauf zusätzlich `C-02`, jeweils `CLOSED`.

Der geskriptete Driver emuliert dafür die record-native Ledgersemantik:
narrowe Codex- oder Reviewrequests aktualisieren betroffene Linien, dürfen
aber andere bereits persistierte Linien nicht verwerfen. Eine vertauschte
Reihenfolge, ein fehlender zweiter Review oder ein verlorenes Finding macht
den jeweiligen Szenariofall rot.

FINDING_RESPONSE: C-17 | ACCEPTED | Alle drei verpflichtenden Korrekturpfade sind vollständige terminale Enginejourneys mit expliziter Sequenz-, Commit- und Ledgerassertion.

## 4. Validierung nach der Nacharbeit

```text
python3 -m pytest tests/test_dry_run_scenarios.py tests/test_orchestrator_resilience_evidence.py -q -p no:cacheprovider
28 passed

python3 -m pytest tests/test_dry_run_scenarios.py tests/test_orchestrator_resilience_evidence.py tests/test_workflow_transition_matrix.py tests/test_structured_output_pressure.py -q -p no:cacheprovider
PASS

python3 -m pytest tests/ -v
1091 passed in 120.17s
```

`git diff --check` ist sauber. Es wurden keine Provider oder Canaries
aufgerufen.

---

# Claude-Slicereview 03, zweite Runde (manuell, adversarial)

## AE1. Reviewgrenze und Methodik

Manueller adversarialer Konvergenzreview außerhalb von `run_task`, ohne
strukturierte JSON-Ausgabe, ohne Provider- oder Canaryaufruf. Gelesen wurden
`AGENTS.md`, `CLAUDE.md`, der freigegebene Arbeitsplan, die Sliceberichte 01
bis 03 einschließlich meines Vorreviews und der Nacharbeit, der
Abschlussbericht, die Roadmap sowie der vollständige Arbeitsbaumdiff gegen
`6b609f4` mit `src/dry_run_scenarios.py`, `tests/test_dry_run_scenarios.py`,
`tests/test_orchestrator_resilience_evidence.py` und dem Manifest.

Keine Zahl und keine Aussage stammt aus Prompt oder Nacharbeit. Gateart und
Regelidentität habe ich gegen echte Produktionszustände gemessen, die
Manifestbindung mit acht Mutationen falsifiziert, die Korrekturjourneys mit
Szenariomutationen gebrochen und die gesammelten pytest-Knoten gegen das
Manifest gestellt. Weder Produktcode noch Tests wurden geändert.

Selbst ausgeführt: `tests/test_dry_run_scenarios.py`,
`tests/test_orchestrator_resilience_evidence.py`,
`tests/test_workflow_transition_matrix.py`,
`tests/test_structured_output_pressure.py` → `50 passed in 52.83s`.

## AE2. C-15 — Gateart folgt jetzt der Zustandssemantik

`_gate_kind` rät nicht mehr über den Fingerprint. Resume folgt aus
`AWAITING_RESUME`, `WAITING_FOR_QUOTA` und `WAITING_FOR_RETRY`; die
Nutzergründe sind namentlich aufgeführt; `STOP_REQUEST` ist Policy; nur
`UNEXPECTED_FILE` unterscheidet weiter über den Fingerprint, und das ist dort
keine Zufallskorrelation, sondern strukturell: `await_user_gate` verlangt einen
Fingerprint, `await_policy_gate` kann keinen setzen.

Ich habe die Ableitung gegen **echte Produktionszustände** gemessen, nicht
gegen konstruierte Gates:

| erzeugender Produktionsübergang | Status / Grund | Fingerprint | Gateart | Regelkennung |
|---|---|---|---|---|
| `record_review_denial` am Iterationslimit | `awaiting_user_decision` / `iteration_limit` | nein | **user** | `PREFIXLESS:REVIEW-DENIAL` |
| `record_invocation_failure` Quota | `awaiting_resume` / `quota` | ja | **resume** | `PREFIXLESS:INVOCATION-FAILURE` |
| `record_invocation_failure` Netzwerk und Prozess | `awaiting_resume` / `instance_failure` | ja | **resume** | `PREFIXLESS:INVOCATION-FAILURE` |
| `await_bootstrap_resume` | `awaiting_resume` / `bootstrap_check` | ja | **resume** | `PROVIDER-INPUT-BUDGET` |
| `reopen_legacy_quota_resume_diff_gate` | `awaiting_resume` / `instance_failure` | ja | **resume** | `PREFIXLESS:LEGACY-QUOTA-REVALIDATION` |
| `await_user_gate` HEAD-DRIFT | `awaiting_user_decision` / `unexpected_file` | ja | **user** | `HEAD-DRIFT` |
| `await_policy_gate` SLICE-HEAD-DRIFT | `awaiting_user_decision` / `unexpected_file` | nein | **policy** | `SLICE-HEAD-DRIFT` |
| `await_user_gate` UNEXPECTED-PATH | `awaiting_user_decision` / `unexpected_file` | ja | **user** | `UNEXPECTED-PATH` |
| `await_policy_gate` STOP_REQUEST | `awaiting_user_decision` / `stop_request` | nein | **policy** | `CODEX-NOT-READY` |

Alle neun Ergebnisse decken sich mit der Gate-Source-Map aus Slice 01, die ich
über vier Runden verifiziert habe. Der von mir gemeldete Kernfall — das
Iterationslimit ohne Fingerprint — liefert jetzt `user` statt `policy`.
Zusätzlich habe ich alle neun Kombinationen aus den drei Wartestatus mit
Quota-, Instanzfehler- und Bootstrapgrund geprüft: durchgängig `resume`.

Die präfixlose Grammatik ist eng. `PREFIXLESS:REVIEW-DENIAL` entsteht nur bei
`ITERATION_LIMIT` **und** exakt passendem Detail; eine Rundenzahl null, ein
anderer Reviewer, ein Präfix- oder Suffixzeichen und derselbe Text unter einem
anderen Gategrund liefern jeweils `None`. Das Regelpräfix bleibt ab
Zeichenposition null verankert: ` HEAD-DRIFT | x` und `xHEAD-DRIFT | x`
ergeben `None`, `SLICE-HEAD-DRIFT | x` ergibt nie `HEAD-DRIFT`.

Fingerprint, Pfadsatz und Resume-Schritt sind Teil des verglichenen
Sieben-Tupels. Unsortierte und doppelte Erwartungspfade werden jetzt bereits
bei der Konstruktion abgewiesen — in meiner Vorrunde wurden sie noch
akzeptiert. Unbekannte Kombinationen scheitern fail-closed: `clear/none`,
`awaiting_user_decision/none` und `awaiting_user_decision/bootstrap_check`
lösen jeweils einen `DryRunScenarioError` aus.

## AE3. C-16 — Manifestbindung ist nichtzirkulär und ausgeführt

Der AST-Namensguard ist entfernt. Das Manifest wird gegen `EVIDENCE_ORACLE`
gestellt, eine vom Manifest unabhängige literale Map, und die Testidentität
wird über `_evidence_node` mechanisch aus der Szenario-ID abgeleitet, sodass
eine fremde oder wiederverwendete Identität strukturell unmöglich ist.

Ich habe acht Mutationen gefahren; in meiner Vorrunde blieben vier davon grün,
jetzt schlagen **alle acht** fehl:

| Mutation | Vorrunde | jetzt |
|---|---|---|
| fehlende Szenario-ID | rot | rot |
| zusätzliche Szenario-ID | — | rot |
| doppelte Szenario-ID | nur über den Renderer | rot |
| existierende, fachlich falsche Test-ID | **grün** | rot |
| eine Test-ID für alle elf Szenarien | **grün** | rot |
| verfälschter `expected_result` | **grün** | rot |
| vertauschte Dimension | **grün** | rot |
| Verweis auf einen Nicht-Szenariotest | — | rot |

Die Ausführung ist real: `pytest --collect-only` sammelt genau elf Knoten
`test_provider_free_resilience_scenario[<id>]`, und jeder stimmt mit dem
`evidence_test` seiner Manifestzeile überein. Der parametrisierte Test
verzweigt je Szenario-ID in einen eigenen Runner und wirft für eine
unbekannte ID einen `AssertionError`. Ich habe alle acht Nicht-Journey-Runner
zusätzlich einzeln ausgeführt; jeder besteht.

Der Abschlussbericht behauptet in §1 jetzt genau diesen Mechanismus und
ausdrücklich, dass bloße Namensexistenz nicht als Nachweis gilt. Die frühere
Überbehauptung ist beseitigt.

## AE4. C-17 — vollständige Korrekturjourneys auf der echten Engine

`_run_correction_journey` fährt alle drei Abläufe über `run_scripted_workflow`
und damit über `ScriptedWorkflowSession`, die eine reale `WorkflowEngine`
aufbaut und `run_current_work_unit` aufruft. Ich habe jeden Ablauf selbst
ausgeführt und die Behauptungen nachgerechnet:

- `slice-correction`: sechs Agentaufrufe mit Runde 2 in derselben
  Slice-Work-Unit, ein Commit, terminal `final_review/completed`;
- `final-correction`: acht Agentaufrufe über eine Korrektur-Work-Unit und ein
  zweites Finalreview, zwei Commits;
- `second-correction-new-blocker`: zehn Agentaufrufe, zwei Commits, terminal.

Für den zweiten Korrekturlauf habe ich den Ledgerverlauf einzeln erhoben:
Finalreview Runde 1 eröffnet `C-01`, die Korrekturrunde 1 schließt `C-01` und
eröffnet `C-02` als neuen Blocker, die Korrekturrunde 2 schließt `C-02`, und
das erneute Finalreview approbiert. Der terminale Ledger lautet
`C-01 CLOSED BLOCKER` und `C-02 CLOSED BLOCKER`, unverbrauchte Providerereignisse
null.

Falsifikationen am Szenario, nicht an den Erwartungen:

| Mutation | Ergebnis |
|---|---|
| vertauschte Agentreihenfolge | `scripted role order mismatch`, rot |
| fehlender zweiter Review | `scripted role order mismatch`, rot |
| fehlender letzter Review | `missing scripted response`, rot |
| Schließung von `C-01` entfernt | rot |
| Eröffnung von `C-01` entfernt | rot |
| Schließung von `C-02` entfernt | rot |

Bemerkenswert ist der Bruchort der drei Ledgermutationen: Sie scheitern nicht
an einer Testassertion, sondern an `NativeReviewRequestError` beziehungsweise
`NativeCodexRequestError` mit `schema-invalid`. Die geskripteten Dokumente
laufen also durch `validate_native_review_provider_response` und
`parse_bound_native_contract_result`, das heißt durch die echte
Writerschema- und Domänenvalidierung. Die permissive Wörterbuchaktualisierung
der Durable-Ledger-Emulation ist damit stromaufwärts gebunden und kann kein
fachlich unzulässiges Resultat still annehmen.

## AE5. Übrige Slice-03-Kriterien

Selbst ausgeführt und bestanden: Happy Path auf der echten Engine, exakter und
Near-Miss-Structured-Output, Record-Ahead-Resume, die drei Drift- und
Scopegates sowie der direkte Queueabschluss. Der Renderer bleibt über
Permutationen byteidentisch, führt jede Dimensionsüberschrift genau einmal und
enthält keine Laufzeit-, Token- oder Kostenwerte. Die Sliceschichten 01 und 02
sind unverändert wirksam; der Verbundlauf über vier Dateien besteht mit
`50 passed`.

Roadmap und Abschlussbericht behandeln den realen Smoke-Test korrekt als
nachgelagerte Betriebsbeobachtung und nicht als Voraussetzung der
Repositoryvalidierung.

## AE6. Ausgeführte Gegenproben

| # | Gegenprobe | Ergebnis |
|---|---|---|
| AF1 | neun reale Produktionsgates gegen `_gate_kind` | alle neun stimmen mit der Source-Map aus Slice 01 |
| AF2 | drei Wartestatus mal drei Gründe | durchgängig `resume` |
| AF3 | neun Grammatikfälle für präfixlose Regeln | alle korrekt, nur exakte Grund-Detail-Paare binden |
| AF4 | vier Verankerungsfälle des Regelpräfixes | kein Teilstringtreffer |
| AF5 | unsortierte und doppelte Erwartungspfade | bei der Konstruktion abgewiesen |
| AF6 | drei unbekannte Status-Grund-Kombinationen | fail-closed |
| AF7 | acht Manifestmutationen | acht von acht rot |
| AF8 | gesammelte pytest-Knoten gegen Manifest | elf von elf deckungsgleich |
| AF9 | drei Korrekturjourneys ausgeführt | terminal, null unverbrauchte Ereignisse |
| AF10 | sechs Szenariomutationen an Reihenfolge und Ledger | sechs von sechs rot |
| AF11 | Bruchort der Ledgermutationen bestimmt | produktive Writerschema- und Domänenvalidierung |
| AF12 | acht Nicht-Journey-Runner einzeln ausgeführt | alle bestanden |
| AF13 | Reportdeterminismus und Dimensionsgrenzen | byteidentisch, keine volatilen Werte |
| AF14 | Verbundlauf über vier Nachweisdateien | `50 passed` |

REVIEWER: claude

FINDING_STATUS: C-15 | CLOSED | Die Gateart folgt jetzt der Status- und Grundsemantik statt dem Vorhandensein eines Fingerprints. Ich habe die Ableitung gegen neun echte Produktionsuebergaenge gemessen: record_review_denial am Iterationslimit liefert ohne Fingerprint korrekt user statt zuvor policy; record_invocation_failure fuer Quota, Netzwerk und Prozess, await_bootstrap_resume und reopen_legacy_quota_resume_diff_gate liefern resume; die drei Drift- und Scopegates sowie das Stop-Request-Policygate stimmen ebenfalls. Alle neun Ergebnisse decken sich mit der Gate-Source-Map aus Slice 01, die ich ueber vier Runden verifiziert habe. Alle neun Kombinationen aus den drei Wartestatus und den Gruenden Quota, Instanzfehler und Bootstrap ergeben resume. Die verbleibende Fingerprintunterscheidung betrifft ausschliesslich unexpected_file und ist dort strukturell, weil await_user_gate einen Fingerprint verlangt und await_policy_gate keinen setzen kann. Die praefixlose Grammatik bindet nur exakte Grund-Detail-Paare, das Regelpraefix bleibt ab Zeichenposition null verankert, ein Teilstringtreffer ist ausgeschlossen, Fingerprint, Pfadsatz und Resume-Schritt sind Teil des verglichenen Tupels, unsortierte und doppelte Erwartungspfade werden bereits bei der Konstruktion abgewiesen, und unbekannte Status-Grund-Kombinationen scheitern fail-closed.

FINDING_STATUS: C-16 | CLOSED | Der AST-Namensguard ist entfernt. Das Manifest wird gegen eine vom Manifest unabhaengige literale Map aus Dimension und vollstaendigem Erwartungstext gestellt, und die Testidentitaet wird mechanisch aus der Szenario-ID abgeleitet, sodass eine fremde oder wiederverwendete Identitaet strukturell unmoeglich ist. Von acht Mutationen blieben in meiner Vorrunde vier gruen; jetzt schlagen alle acht fehl, darunter die zuvor unentdeckten Faelle einer existierenden aber fachlich falschen Test-ID, der Wiederverwendung einer Test-ID fuer alle elf Szenarien, eines verfaelschten Erwartungstexts und einer vertauschten Dimension. Die Ausfuehrung ist real: pytest sammelt genau elf parametrisierte Knoten, jeder stimmt mit dem evidence_test seiner Manifestzeile ueberein, der parametrisierte Test verzweigt je Szenario-ID in einen eigenen Runner und wirft fuer eine unbekannte ID einen AssertionError. Ich habe alle acht Nicht-Journey-Runner zusaetzlich einzeln ausgefuehrt. Der Abschlussbericht behauptet nun genau diesen Mechanismus und schliesst blosse Namensexistenz ausdruecklich aus.

FINDING_STATUS: C-17 | CLOSED | Alle drei Korrekturablaeufe laufen ueber run_scripted_workflow und damit ueber ScriptedWorkflowSession, die eine reale WorkflowEngine aufbaut und run_current_work_unit aufruft. Ich habe jeden Ablauf selbst ausgefuehrt: slice-correction mit sechs Agentaufrufen und Runde zwei in derselben Slice-Work-Unit und einem Commit, final-correction mit acht Aufrufen ueber eine Korrektureinheit und ein zweites Finalreview mit zwei Commits, second-correction-new-blocker mit zehn Aufrufen und zwei Commits; alle drei enden in final_review und completed mit null unverbrauchten Providerereignissen. Der zweite Korrekturlauf eroeffnet C-01 im ersten Finalreview, schliesst C-01 und eroeffnet C-02 in Korrekturrunde eins, schliesst C-02 in Runde zwei und traegt beide Linien geschlossen in das terminale Finalreview. Sechs Szenariomutationen werden erkannt: vertauschte Agentreihenfolge, fehlender zweiter Review, fehlender letzter Review sowie das Entfernen der Eroeffnung oder der Schliessung von C-01 beziehungsweise C-02. Die drei Ledgermutationen scheitern nicht an einer Testassertion, sondern an der produktiven Writerschema- und Domaenenvalidierung, weshalb die permissive Durable-Ledger-Emulation stromaufwaerts gebunden ist und kein fachlich unzulaessiges Resultat still annehmen kann.

REVIEW_EVIDENCE: geprueft wurden die Gateartableitung gegen neun echte Produktionsuebergaenge und alle Wartestatuskombinationen, die praefixlose Grammatik und die Praefixverankerung, Fingerprint, Pfadsortierung und Resume-Schritt, das Fail-closed-Verhalten unbekannter Kombinationen, acht Manifestmutationen samt Abgleich der gesammelten pytest-Knoten, die drei Korrekturjourneys auf der realen Engine mit sechs Szenariomutationen und der Bestimmung ihres Bruchorts, die uebrigen acht Szenariorunner, Reportdeterminismus und Dimensionsgrenzen sowie die Regressionen der Slices 01 und 02 | groesstes Restrisiko ist die handgepflegte Sollmenge: EXPECTED_SCENARIOS und EVIDENCE_ORACLE sind Literale, und nichts leitet die verpflichtende Szenariomenge aus dem freigegebenen Plan ab, sodass ein kuenftig ergaenztes Pflichtszenario nur durch menschliche Disziplin in das Inventar gelangt; nachgeordnet ist der erzaehlende Abschlussbericht nicht maschinell an das Manifest gebunden und weicht in neun von elf Zeilen im Wortlaut vom Erwartungstext ab, und die Durable-Ledger-Emulation waere fuer sich genommen permissiv, wenn die vorgelagerte Vertragsvalidierung je gelockert wuerde | realistische Bruchbedingung ist eine Planerweiterung um einen neuen verpflichtenden Ablauf, die das Inventar nicht mitfuehrt: alle elf bestehenden Bindungen blieben gruen, der Bericht behauptete weiterhin Vollstaendigkeit, und der neue Pfad bliebe unvermessen

PRE_MORTEM: In drei Monaten scheitert dieses Paket am wahrscheinlichsten daran, dass ein neuer verpflichtender Ablauf in den Arbeitsplan aufgenommen wird, ohne dass jemand EXPECTED_SCENARIOS, EVIDENCE_ORACLE und das Manifest ergaenzt. Die bidirektionale Bindung prueft nur die elf bereits eingetragenen Szenarien gegeneinander und kennt keine Ableitung aus dem Plan; die Suite bleibt gruen, der Abschlussbericht weist weiterhin drei sauber getrennte Dimensionen aus, und der ungemessene Pfad faellt erst im realen Betrieb auf. Zweitwahrscheinlich driftet der erzaehlende Bericht vom Manifest ab, weil ihn nichts maschinell daran bindet.

SLICE_APPROVAL: 3 | YES

STATUS: DONE

---

# Codex-Finalkorrektur zu C-18 und C-19

## 1. Reviewbindung

Diese Nacharbeit beantwortet ausschließlich die beiden Blocker aus Claudes
branchweitem Gesamtreview vom 28. August 2026. Sie ist eine
Konvergenzkorrektur: Es werden keine neuen Beobachtungen eröffnet und keine
Claude-Findings durch Codex geschlossen oder reklassifiziert.

## 2. C-18 – Scopeverletzung produktionsförmig gebunden

Das Resilienzfixture erzeugt `scope-violation` nun wie die Produktion über
`await_user_gate` mit `GateReason.UNEXPECTED_FILE`, Regelkennung
`UNEXPECTED-PATH`, Fingerprint `e…e` und exakt dem betroffenen Pfadsatz. Die
Erwartung verlangt entsprechend `kind=user` und denselben Fingerprint.

Eine eigene Negativkontrolle hält die Sicherheitsrichtung fest: Sowohl die
Rückstufung auf `kind=policy` als auch das Entfernen des Fingerprints machen
die vollständige Gateidentität rot. Manifest und narrativer
Resilienznachweis beschreiben jetzt ebenfalls das fingerprintgebundene
Nutzergate.

FINDING_RESPONSE: C-18 | ACCEPTED | Das Scope-Szenario verwendet und behauptet jetzt die produktive fingerprintgebundene Nutzerform; Policygate und fehlender Fingerprint werden separat falsifiziert.

## 3. C-19 – BoolOp- und error_code-Produzenten vollständig inventarisiert

Die Producerverfolgung verarbeitet nicht mehr nur einfache
Stringzuweisungen. `_assigned_rule_ids` liest alle regelidentitätsförmigen
Literale einer Zuweisung, also auch BoolOp-Fallbacks, und erweitert eine
Zuweisung aus `.error_code` um die exakt aus allen `_deny`-Aufrufen von
`run_final_review_preflight` abgeleitete typisierte Codemenge.

Damit werden neben `PROVIDER-INPUT-BUDGET` und dem Fallback
`FINAL-REVIEW-PREFLIGHT` auch alle vierzehn heute produzierbaren
Preflightcodes gefunden. Jede der fünfzehn neuen Identitäten besitzt eine
eigene Source-Map-Zeile, einen unabhängigen ausführbaren Fall, einen
Produktionsursprung und eine exakt gezählte Bootstrap-Resume-Emission.

Die Rückrichtung ist ebenfalls geschlossen: `_source_map_errors` meldet ein
direktes Produktionstripel nun auch dann als `unclassified-triplet`, wenn
seine Regelidentität noch vollständig in der Source-Map fehlt. Zwei
Negativkontrollen belegen die konkrete Fehlerklasse:

- `error.result.error_code or "ZZZ-ESCAPE"` erzeugt
  `unclassified-rule` und `unclassified-triplet`;
- das Entfernen der `FINAL-REVIEW-PREFLIGHT`-Zeile erzeugt dieselben beiden
  Fehler für den realen Fallback.

Die erwartete Vierzehnermenge der typisierten Preflightcodes ist als vom
Source-Map-Literal getrennte Sollmenge behauptet. Neue oder entfernte
`_deny`-Codes verändern damit die entdeckte Menge und erzwingen eine bewusste
Inventurentscheidung.

FINDING_RESPONSE: C-19 | ACCEPTED | BoolOp-Fallback und typisierte error_code-Menge erreichen jetzt Inventur, Source-Map und direkte Tripelprüfung; ZZZ-ESCAPE sowie eine fehlende Fallbackzeile machen den Guard aus den beabsichtigten Gründen rot.

## 4. Validierung

```text
python3 -m pytest \
  tests/test_orchestrator_resilience_evidence.py \
  tests/test_workflow_transition_matrix.py \
  -q -p no:cacheprovider

32 passed in 61.07s
```

Der breitere paketbezogene Verbund bestand anschließend mit
`59 passed in 64.41s`. Die nach den Orchestrierungs-, Prompt-, Parser-, Watch-
und State-Regeln vorgeschriebene vollständige Repositorysuite bestand mit
`1092 passed in 121.42s`.

Der erste identische Verbundlauf traf einmalig die bestehende echtzeitbasierte
Providerattempt-Fixturegrenze mit `provider attempt end cannot precede start`;
der unveränderte Wiederholungslauf bestand. Die vorgeschriebene vollständige
Suite bestand danach ebenfalls. Es wurden keine Provider oder Canaries
gestartet.

CODEX_READINESS: READY_FOR_CLAUDE_CORRECTION_REVIEW
STATUS: DONE
