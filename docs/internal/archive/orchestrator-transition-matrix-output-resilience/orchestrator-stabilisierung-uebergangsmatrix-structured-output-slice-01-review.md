# Slice 01: Behauptende Übergangsmatrix und Gate-Inventur

## 1. Einordnung

- Arbeitsplan: `docs/internal/orchestrator-stabilisierung-uebergangsmatrix-structured-output-arbeitsplan.md`
- Plancommit: `ff10967` (`docs: approve transition matrix resilience plan`)
- Zielbranch: `feature/orchestrator-transition-matrix-output-resilience`
- Ausführung: manuell außerhalb von `run_task`
- Slice: 01
- Ergebnis: Phase A abgeschlossen; Phase B entfällt

## 2. Änderungspfad

- `tests/test_workflow_transition_matrix.py`
- `docs/internal/orchestrator-stabilisierung-uebergangsmatrix-structured-output-arbeitsplan.md`
- `docs/internal/orchestrator-stabilisierung-uebergangsmatrix-structured-output-slice-01-review.md`

Der zweite Pfad enthält ausschließlich eine semantikgleiche Korrektur der
Nicht-Scope-Formulierung. Der freigegebene Plan nannte dort eine bereits
ausgemusterte Rolle beim alten Namen und verletzte deshalb den bestehenden
Retirement-Guard. Ziele, Invarianten, Slices und Akzeptanzkriterien wurden nicht
verändert.

## 3. Umgesetzte Phase-A-Matrix

### 3.1 Unabhängiges Zustands-Oracle

`TRANSITION_ORACLE` enthält literale, reviewbare Sollwerte. Die erwarteten
Ledger, angebotenen Findings, Work-Unit-Art, Schritt, Runde, Work-Unit-Anzahl,
Record-/Mirrorzustände, externe Aufrufzahl und Gateidentität werden nicht aus
einer Produktionsprojektion berechnet.

Die tatsächliche Seite wird über die produktiven Zustandsübergänge aufgebaut
und anschließend normalisiert. Abgedeckt sind insbesondere:

- Plan zu erstem Slice,
- Slice-Ablehnung und Korrekturrunde 2,
- Slice-Commit zu Final Review,
- Final-Review-Ablehnung zu Korrektur,
- spätere Korrekturrunde mit neuem Blocker,
- Korrektur-Commit zu erneutem Final Review,
- fingerprintgebundener `HEAD-DRIFT`-Nutzerentscheid,
- präfixgenauer `SLICE-HEAD-DRIFT`-Policystopp,
- präfixgenauer `UNEXPECTED-PATH`-Nutzerentscheid.

Die Reachability-Tabelle klassifiziert zulässige und konstruktiv unzulässige
Folgekanten separat. Ein negativer Präfixtausch beweist, dass `HEAD-DRIFT` und
`SLICE-HEAD-DRIFT` nicht über Teilstrings oder gemeinsame Gategründe
zusammenfallen.

### 3.2 Replay, Ledger und Unterbrechungsgrenzen

Ein echter `ProductionWorkflowDriver` mit temporärem Git-Repository und
append-only Recordstore bildet folgende Linie:

1. C-01 offen,
2. C-02 offen und anschließend geschlossen,
3. fremde historische C-99-Linie,
4. Final Review zu Korrektur,
5. spätere Korrekturrunde mit neuem C-03-Blocker.

Die Assertions vergleichen die tatsächliche vollständige Recordprojektion und
die korrekturgebundene Providerprojektion jeweils mit literalen Soll-Ledgern.
Geprüft werden außerdem:

- Mirror-Finding ohne Opening-Record: fail-closed,
- Record-Ahead bei fehlendem Mirror-Finding: fail-closed,
- Statusübergang ohne Opening-Record: stabiler
  `RECORD-REFERENCE-MISSING`-Fehler,
- Mirror gesetzt, Checkpoint noch nicht geschrieben: erneute Bindung verändert
  keinen Findingrecord und benötigt keinen Provideraufruf.

### 3.3 Falsifikation der Oracle-Unabhängigkeit

Beide vorgeschriebenen Mutationen wurden als dauerhaft grüne Negativtests
umgesetzt:

- `orchestrator.replay_findings` liefert für die nichtleere Linie absichtlich
  `()`; `authoritative_native_findings` stoppt wegen Mirrorabweichung.
- `carry_forward_native_findings` reicht absichtlich nur sein Eingabeargument
  durch; der erwartete vollständige Ledger einschließlich C-99 wird verfehlt.

Damit kann keine der beiden geprüften Produktionsprojektionen ihre eigenen
Sollwerte liefern und die Matrix zirkulär grün halten.

## 4. Gate-Source-Map und struktureller Guard

Der Guard parst alle produktiven Pythonmodule per AST und inventarisiert:

- sämtliche Aufrufe von `await_user_gate`, `await_policy_gate`,
  `record_review_denial`, `record_invocation_failure`,
  `await_bootstrap_resume` und
  `reopen_legacy_quota_resume_diff_gate`,
- alle direkten `GateRecord`-Konstruktionen,
- alle `replace(..., gate=...)`-Übergänge,
- alle verankerten Großbuchstabenpräfixe vor ` | `,
- `gates.BUILTIN_STOP_RULES`,
- lokale Großbuchstabenkennungen, die in derselben Funktion in ein
  f-String-Gatedetail interpoliert werden.

Die Source-Map bindet Gategrund, Regelidentität, Gateart, Producer,
Emissionsstelle und Matrixfall. Bewusst präfixlose Familien besitzen eine
vollständige `fullmatch`-Grammatik. Präfixförmige, aber gatefremde Diagnosen
werden mit ihrem nichtpersistierenden Kontrollfluss separat klassifiziert.

### 4.1 Erledigung von C-06

Die lokale Zuweisung `code = "PROVIDER-INPUT-BUDGET"` in
`workflow._invoke_role` wird durch die AST-Producerverfolgung erkannt und der
Emissionsstelle `await_bootstrap_resume` zugeordnet. Der Guard hängt nicht von
einem Registryeintrag oder einem bereits separierten Detailliteral ab.

Die drei getrennten Mutationspfade schlagen erwartungsgemäß fehl:

- statisches `ZZZ-PROBE | ...`,
- lokales `code = "ZZZ-DYNAMIC"` vor der bestehenden f-String-Emission,
- direktes `GateRecord(..., detail="ZZZ-DIRECT | ...")`.

## 5. Analysecheckpoint

### 5.1 Beobachtete Abweichungen

Keine Produktionsabweichung wurde reproduziert. Die literalen Matrixfälle, die
Record-/Mirror-Grenzen, die Gateinventur und alle Mutationskontrollen sind grün.

### 5.2 Ursachencluster

Mangels Abweichung existiert kein belastbarer gemeinsamer Ledger-, Replay-,
Gate-, Resume- oder Umfangsdefekt, der einen Produktcodeeingriff rechtfertigt.

### 5.3 Entscheidung zu Phase B

Phase B entfällt gemäß dem freigegebenen Arbeitsplan. Slice 01 bleibt bewusst
test-only. Ein Produktionscodeeingriff ohne roten Phase-A-Fall würde die
Checkpointregel verletzen.

## 6. Validierung

- neue Matrix: `9 passed`
- fokussiert mit State, Replay und Runtime:
  `153 passed in 27.15s` vor der letzten reinen Testerweiterung; die neue Matrix
  wurde danach erneut vollständig mit `9 passed` ausgeführt
- vollständige Repositorysuite nach Dokumentkorrektur:
  `1053 passed in 81.32s`
- `git diff --check`: sauber; ausschließlich bestehende
  LF/CRLF-Hinweise des Arbeitsbaums

Die vollständige Suite wurde einmal vor der Dokumentkorrektur ausgeführt und
fand ausschließlich die alte Rollenbezeichnung im neuen aktiven Plan. Nach der
semantikgleichen Korrektur wurde die Suite vollständig wiederholt und bestand.

## 7. Reviewauftrag

Claude soll insbesondere adversarial prüfen:

- ob das literale Oracle von allen geprüften Produktionsableitungen unabhängig
  bleibt,
- ob die beiden Replay-/Carry-Mutationen tatsächlich einen roten Matrixfall
  erzwingen,
- ob C-06 durch echte lokale Producerverfolgung und nicht nur durch eine
  Handliste geschlossen ist,
- ob Helper-, Direktkonstruktor- und `replace(gate=...)`-Inventur gemeinsam
  vollständig sind,
- ob die Präfix- und Präfixlos-Klassifikation fail-closed ist,
- ob der Analysecheckpoint Phase B zu Recht entfallen lässt.

---

# Claude-Slicereview 01 (manuell, adversarial)

## S1. Reviewgrenze und Methodik

Manuelles adversariales Slicereview außerhalb des Orchestrators. Keine
strukturierte JSON-Ausgabe, kein `run_task`, kein Watcher, kein Bootstrap, kein
Providerabruf. Gelesen und verbindlich ausgewertet wurden `AGENTS.md`,
`CLAUDE.md`, der freigegebene Arbeitsplan, dieser Slicebericht,
`tests/test_workflow_transition_matrix.py` sowie die von den Tests adressierten
Produktionspfade in `src/workflow.py`, `src/workflow_state.py`,
`src/orchestrator.py`, `src/gates.py` und `src/artifact_replay.py`.

Sollwerte, Kanteninventar, Gateidentitäten und Record-/Mirrorgrenzen habe ich
aus dem freigegebenen Plan und dem Produktivcode selbst abgeleitet und keine
Schlussfolgerung des Slice-Berichts ungeprüft übernommen. Ich habe weder
Produktcode noch Tests geändert. Alle Gegenproben liefen als
In-Memory-Mutationen über pytest-Plugins beziehungsweise `ast`-Bäume aus einem
Arbeitsverzeichnis außerhalb des Repositorys; der Arbeitsbaum ist nach dem
Review unverändert. Die vollständige Testsuite wurde nicht erneut ausgeführt.

## S2. Selbst erhobener Repositorystand

- Branch: `feature/orchestrator-transition-matrix-output-resilience`
- Plancommit: `ff10967`
- `git diff --stat ff10967`: genau eine geänderte Zeile, ausschließlich im
  Arbeitsplan. `src/` ist gegenüber `ff10967` unverändert.
- untrackt: `tests/test_workflow_transition_matrix.py` und dieser Bericht
- `git diff --check`: sauber, nur die bestehende LF/CRLF-Warnung
- selbst ausgeführt: `tests/test_workflow_transition_matrix.py` → `9 passed`
- selbst ausgeführt: `tests/test_language_consistency.py` → `33 passed`

## S3. Planerfüllung, Slicegrenze und Dokumentkorrektur

**Slicegrenze eingehalten.** Der Diff gegen `ff10967` berührt keinen
Produktivpfad. Die Phase-A-Regel „Produktivcode bleibt bis zum
Analysecheckpoint unverändert“ ist damit erfüllt.

**Dokumentkorrektur geprüft und in Ordnung.** Ich habe die Behauptung nicht
übernommen, sondern falsifiziert: mit der Formulierung des Plancommits meldet
`test_language_consistency._retirement_hits` für den Arbeitsplan genau einen
Treffer, mit der korrigierten Formulierung keinen; die Datei liegt
nachweislich im aktiven Inventar des Guards. Die Änderung steht ausschließlich
in §10 Nicht-Scope, ersetzt den Eigennamen einer ausgemusterten dritten
Reviewerrolle durch deren generische Bezeichnung und ist eher strenger als die
Vorfassung, keinesfalls schwächer. Ziele, Invarianten, Slice-Zuschnitt,
Akzeptanzkriterien und Stopbedingungen sind unverändert. Der Eingriff war
notwendig, minimal und verändert keine freigegebene Anforderung.

**Zum Entfallen von Phase B.** Die Checkpointregel lässt Phase B entfallen,
wenn die Matrix bereits grün ist. Die Matrix ist grün — aber in S4, S5 und S6
weise ich nach, dass mehrere ausdrücklich behauptete Dimensionen der Matrix
für keinen Produktionsdefekt rot werden **können**. Die Aussage „keine
Produktionsabweichung reproduziert“ ruht damit teilweise auf nicht
wirksamen Assertions statt auf negativer Evidenz.

Ausdrücklich festgehalten: Ich habe **keinen** Produktivcodefehler gefunden,
den eine korrekte Matrix hätte aufdecken müssen. Jede von mir unabhängig am
Produktivcode nachgefahrene Übergangs-, Gate- und Replaygrenze verhielt sich
korrekt. Es gibt deshalb keinen Blocker wegen eines unterlassenen
Produktionsfixes. Die Blocker unten betreffen die Vollständigkeit und
Wirksamkeit der Phase-A-Matrix selbst, also die Grundlage, auf der der
Checkpoint überhaupt entscheiden darf.

## S4. Unabhängigkeit des Übergangsoracles

**Literalität und Nichtzirkularität: bestätigt.** `TRANSITION_ORACLE`
(Zeile 69 ff.) enthält ausschließlich literale Tabellendaten. Weder
`replay_findings`, `carry_forward_native_findings`,
`authoritative_native_findings` noch `WorkflowState` werden zur Bildung eines
Sollwerts aufgerufen. Gemeinsame Helfer bauen nur Eingaben auf
(`_state`, `_slice_state`, `_finding`) oder normalisieren Ausgaben
(`_ledger_literal`). Eine indirekte Ableitung über Fixturebuilder oder aus
Ist-Ergebnissen habe ich nicht gefunden.

**Tatsächlich produktionsgetriebene Spalten.** Von den vierzehn Spalten der
Oraclezeile werden sieben aus produktiven Zustandsübergängen erzeugt:
`kind`, `step`, `round_number`, `provider_findings`, `work_unit_count`,
`gate_reason`, `gate_status`. Diese Spalten prüfen echte Grenzen, und die
Kanten Plan→Slice, Slice-Ablehnung mit Runde 2, Slice→Final Review,
Final Review→Korrektur, spätere Korrekturrunde mit neuem Blocker sowie
Korrektur→erneutes Final Review sind darüber real behauptet.

**Nicht wirksame Spalten.** Die übrigen Spalten tragen keine
Produktionsaussage; Details und Nachweis in Befund `C-09`.

**Präfixgenauigkeit: bestätigt.** `HEAD-DRIFT`, `SLICE-HEAD-DRIFT` und
`UNEXPECTED-PATH` werden über `PREFIX_PATTERN` ab Zeichenposition null
verankert geparst und als Tripel aus Gategrund, Regelidentität und Gateart
verglichen. Der eingebaute Vertauschungsnachweis ist echt. Ich habe zusätzlich
geprüft, dass die Quellenzuordnung sachlich stimmt: `HEAD-DRIFT` entsteht in
`orchestrator.commit_slice` (`src/orchestrator.py:2133`) und wird in
`workflow._commit` über `detail=exc.detail` (`src/workflow.py:2644`) emittiert;
`SLICE-HEAD-DRIFT` entsteht und emittiert in
`orchestrator.run_production_workflow` (`src/orchestrator.py:3509`).

**Reachability: unvollständig.** Der Kantenraum der vier Work-Unit-Arten
umfasst sechzehn Kombinationen; `EDGE_CLASSIFICATION` (Zeile 526 ff.)
klassifiziert elf. Eine der fünf fehlenden Kombinationen ist produktiv
erreichbar. Siehe Befund `C-07`.

## S5. Replay-, Ledger- und Mutationsevidenz

**Reale Recordkette: bestätigt.** `_ledger_case` baut über einen echten
`ProductionWorkflowDriver`, ein temporäres Git-Repository und einen
append-only `ArtifactStore` genau die geforderte Linie: `C-01` offen, `C-02`
offen und danach geschlossen, historische Fremdlinie `C-99`, Übergang Final
Review→Korrektur, spätere Korrekturrunde mit neuem `C-03`. Fünf
Findingtransitionsrecords, vollständiger Ledger und korrekturgebundene
Providerprojektion werden jeweils gegen unabhängig hingeschriebene Sollwerte
geprüft, nicht gegeneinander. Das ist die stärkste Stelle des Slices.

**Fail-closed-Fenster: bestätigt.** Mirror voraus (`carry_forward` mit
zusätzlichem Mirror-Finding), Record voraus (`authoritative` mit fehlendem
Mirror-Finding) und die beschädigte Linie (Statusübergang ohne Opening-Record
→ `RECORD-REFERENCE-MISSING`) treiben jeweils echten Produktionscode und
scheitern mit stabiler Diagnose.

**Fenster Mirror→Checkpoint: nur abgeschwächt geprüft.** `driver.checkpoint(`
kommt im gesamten Modul nicht vor. Geprüft wird das erneute `bind_work_unit`
mit dem literalen Mirror, nicht die Checkpointschreibung und nicht deren
Idempotenz. Der Testkommentar räumt das selbst ein und verweist auf die
Runtime-Suite. Für die Matrix bedeutet das: die Spalte `checkpoint_state`
behauptet einen Zustand, den kein Szenario dieses Moduls erzeugt.

**Mutation `replay_findings` → leer: wirksam und fachlich richtig.** Meine
Gegenprobe P2 hat die Mutation nicht im Probetest, sondern global in
`orchestrator` gesetzt. Ergebnis: genau ein roter Fall,
`test_record_replay_matrix_...`, mit
`WorkflowExecutionError: authoritative finding replay differs from the
state-v3 mirror` aus `src/orchestrator.py:760`. Das ist der fachlich richtige
Grund: die Recordlinie verliert ihren Ledger, der Mirror widerspricht, der
Lauf stoppt fail-closed.

**Mutation `carry_forward_native_findings` → Durchreichen: wirksam, aber am
falschen Ort belegt.** Gegenprobe P1 hat die Mutation im Produktivcode gesetzt.
Ergebnis: genau ein roter Fall, `test_record_replay_matrix_...` in
Zeile 1363, weil die historische Linie `C-99` aus dem Ledger verschwindet —
der fachlich richtige Grund. Die Schutzwirkung „keine der beiden Mutationen
darf die vollständige Matrix grün lassen“ ist damit real erfüllt.

Der dafür vorgesehene Probetest selbst blieb bei P1 jedoch **grün**. Er
patcht die Methode und prüft anschließend nur, dass der Patch sein Argument
zurückgibt. Details in Befund `C-10`.

**Beide Mutationen lassen die neun Matrixfälle unberührt.** In P1 wie in P2
blieb `test_literal_transition_oracle_...` grün. Damit ist unabhängig belegt,
dass die neun verpflichtenden Kanten keinerlei Ledger-, Replay- oder
Recordaussage tragen.

## S6. Gate-Inventur und C-06

**Strukturelle Inventur: bestätigt.** `_gate_call_inventory` vergleicht drei
Counter auf exakte Gleichheit — Helferaufrufe, direkte
`GateRecord`-Konstruktionen und `replace(..., gate=...)`-Übergänge. Die vier
benannten direkten Zustandsübergänge sind enthalten. Ein zusätzlicher direkter
Konstruktor verändert den Counter; ich habe das gegen den realen
`workflow_state`-Baum verifiziert.

**Die drei Guard-Mutationen: gegen den echten Baum verifiziert.** Der
vorhandene Test prüft sie an synthetischen Einzelmodulen. Ich habe sie
zusätzlich in die realen Quellbäume injiziert:

- `ZZZ-PROBE` als statisches Detail an der realen `await_user_gate`-Stelle in
  `workflow._commit` → `{'ZZZ-PROBE'}`, rot.
- `ZZZ-DYNAMIC` als zusätzlicher Zuweisungszweig unmittelbar an der realen
  Producerstelle `src/workflow.py:2149` → `{'ZZZ-DYNAMIC'}`, rot.
- `ZZZ-DIRECT` als zusätzlicher direkter `GateRecord` in `workflow_state` →
  `{'ZZZ-DIRECT'}`, rot, und zusätzlich Counterabweichung.

Ergänzend habe ich ein neues **upstream** erzeugtes Präfix durch die bereits
klassifizierte variable Stelle `detail=exc.detail` geschickt → rot. Klausel 12
hält für neue Präfixe.

**C-06, erste Hälfte: nachweislich echt geschlossen.** Ich habe die Inventur
direkt ausgewertet: `PROVIDER-INPUT-BUDGET` ist **nicht** in
`_literal_prefix_inventory` und **nicht** in `gates.BUILTIN_STOP_RULES`
enthalten. Die Kennung erscheint ausschließlich im Ergebnis von
`_assigned_prefixes_reaching_gate`, das die lokale Zuweisung in
`workflow._invoke_role` per AST verfolgt, und der Test bindet genau das über
`assert _assigned_prefixes_reaching_gate(trees) >= {"PROVIDER-INPUT-BUDGET"}`.
Ohne die Producerverfolgung ließe sich die Inventur nicht vollständig
erzeugen. Das ist keine nachträgliche Handliste. Die im Bericht behauptete
Zuordnung zur Emissionsstelle ist zudem sachlich korrekt.

**C-06, zweite Hälfte: nicht erfüllt.** Der Akzeptanztest verlangt, dass die
Mutation rot bleibt, „bis das neue Tripel samt Producer- und Emissionsstelle
klassifiziert **und durch einen Matrixfall emittiert** ist“. Genau diese
Schlussbedingung ist nicht durchsetzbar: Der Guard wird bereits dadurch wieder
grün, dass die Kennung in `REGISTERED_GATE_PREFIXES` aufgenommen wird. Eine
`GATE_SOURCE_MAP`-Zeile mit erfundener Producer- und Emissionsstelle passiert
den Guard unbemerkt, und ein Matrixfall wird nirgends erzwungen. `C-06` bleibt
deshalb offen; die zugrunde liegende Mechanik führe ich als eigenen Blocker
`C-08`.

**Gegenrichtung: nicht gebunden.** Details in Befund `C-08`.

**Gatefremde Familien: eng und nachvollziehbar.** `GATE_FOREIGN_PREFIXES`
enthält genau zwei Einträge mit jeweils begründetem, nicht persistierendem
Kontrollfluss, und der Test verlangt, dass beide im Literalinventar tatsächlich
vorkommen. Diese Klassifikation halte ich für korrekt und ausreichend eng.

## S7. Ausgeführte Gegenproben

| # | Gegenprobe | Erwartung des Berichts | Ergebnis |
|---|---|---|---|
| P0 | `pytest tests/test_workflow_transition_matrix.py` | 9 passed | `9 passed` — bestätigt |
| P1 | Produktives `carry_forward_native_findings` global auf Durchreichen mutiert | mindestens ein roter Matrixfall | 1 failed / 8 passed; rot wird `test_record_replay_matrix_...:1363` wegen fehlendem `C-99` — richtiger Grund. Der Probetest selbst bleibt grün |
| P2 | `orchestrator.replay_findings` global auf `()` mutiert | mindestens ein roter Matrixfall | 1 failed / 8 passed; `WorkflowExecutionError: authoritative finding replay differs from the state-v3 mirror` — richtiger Grund |
| P3 | Zusätzliche `GATE_SOURCE_MAP`-Zeile mit nicht existierender Emissions- und Producerstelle | Guard rot | `9 passed` — Lücke bestätigt |
| P4 | `ZZZ-PROBE`, `ZZZ-DYNAMIC`, `ZZZ-DIRECT` in die **realen** Quellbäume injiziert | je rot | je rot, zusätzlich Counterabweichung bei `ZZZ-DIRECT` |
| P5 | Neues upstream-Präfix durch `detail=exc.detail` | rot | rot |
| P6 | Bereits registriertes Präfix an eine **andere** Emissionsstelle verschoben | Bindung der exakten upstream-Menge je Stelle | grün — Klausel 12 bindet nur global, nicht je Stelle |
| P7 | `detail` der Emissionsstelle entfernt, Source-Map-Zeile verwaist | Guard rot | grün — verwaiste Zeile unentdeckt |
| P8 | Inventurherkunft von `PROVIDER-INPUT-BUDGET` ausgewertet | nur über Producerverfolgung auffindbar | bestätigt: nicht im Literalinventar, nicht in `BUILTIN_STOP_RULES` |
| P9 | Plan-Review-Ablehnung über die produktive Routing-Verzweigung nachgefahren | — | Work-Unit bleibt `plan`, Runde 2, Schritt `codex_plan_revision`; im Inventar nicht klassifiziert |
| P10 | Retirement-Guard gegen alte und neue Nicht-Scope-Formulierung | alt rot, neu grün | bestätigt; Plandatei liegt im aktiven Inventar |
| P11 | `pytest tests/test_language_consistency.py` | grün | `33 passed` |

## S8. Restrisiken ohne Befundstatus

- `_source_trees` liest `src/*.py` ohne Rekursion. `src/` ist heute flach, das
  Risiko ist latent, nicht aktuell.
- `_call_name` erkennt nur direkte Attribut- und Namensaufrufe. Eine über
  `getattr` oder ein Alias emittierte Gatetransition entginge der Inventur.
- Außerhalb von `src/` existiert heute keine Gateemission; `run_task` und
  `scripts/` habe ich geprüft und sind frei von präfixförmigen Gatedetails.

## S9. Befundübersicht

| Befund | Klasse | Kern |
|---|---|---|
| `C-06` | OBSERVATION | erste Hälfte nachweislich geschlossen, Schlussbedingung des Akzeptanztests nicht durchsetzbar → bleibt offen |
| `C-07` | BLOCKER | produktiv erreichbare Rundenkante der Plan-Work-Unit weder positiv noch als unzulässig klassifiziert |
| `C-08` | BLOCKER | `GATE_SOURCE_MAP` ist nicht bidirektional gebunden; verwaiste Zeilen und je-Stelle-Mengen bleiben unentdeckt |
| `C-09` | BLOCKER | Record-, Mirror-, Aufrufzahl- und Checkpointdimension der neun Matrixfälle tragen keine Produktionsaussage |
| `C-10` | OBSERVATION | der vorgeschriebene Carry-Forward-Probetest ist tautologisch und gegenüber der von ihm behaupteten Mutation unempfindlich |

REVIEWER: claude

NEW_FINDING: C-07 | BLOCKER | Die Rundenkante der Plan-Work-Unit ist produktiv erreichbar, aber im Kanteninventar weder positiv noch ausdruecklich als unzulaessig klassifiziert. workflow._apply_review_result waehlt bei einer abgelehnten Planpruefung return_step gleich WorkflowStep.CODEX_PLAN_REVISION, solange unit.kind gleich WorkUnitKind.PLAN ist; record_review_denial belaesst daraufhin Art und Identitaet der Work-Unit und erhoeht die Runde. Nachgefahren ueber die produktiven Eintrittspunkte ergibt das kind gleich plan, round_number gleich 2, current_step gleich codex_plan_revision. EDGE_CLASSIFICATION in Zeile 526 ff. enthaelt kein Paar plan/plan, und test_transition_reachability_inventory_is_unique_and_complete zementiert die Luecke, indem es die Zielmenge auf slice, final_review und correction festschreibt und plan als Ziel damit strukturell ausschliesst. Insgesamt sind fuenf der sechzehn Kombinationen unklassifiziert: plan/plan, final_review/final_review, slice/plan, final_review/plan und correction/plan; die erste ist erreichbar, die uebrigen vier sind konstruktiv unerreichbar und muessen laut Phase A Schritt 2 trotzdem ausdruecklich dokumentiert sein. Zusaetzlich fehlt der Plan-Work-Unit die von Phase A Schritt 4 geforderte Variante fuer Runde groesser gleich zwei; TRANSITION_ORACLE beginnt erst bei der bereits gestarteten Slice-Work-Unit. | EDGE_CLASSIFICATION klassifiziert alle sechzehn Kombinationen der vier Work-Unit-Arten genau einmal, plan/plan darunter als erreichbar mit Begruendung, und TRANSITION_ORACLE erhaelt eine literale Zeile fuer die abgelehnte Planpruefung mit kind plan, round_number 2 und step codex_plan_revision, deren Ist-Seite ueber with_current_step und record_review_denial erzeugt wird. Falsifikationstest: python3 -m pytest tests/test_workflow_transition_matrix.py -q muss rot werden, sobald die neue Oraclezeile auf round_number 1 oder auf eine andere Work-Unit-Art veraendert wird, und test_transition_reachability_inventory_is_unique_and_complete muss rot werden, sobald eine der sechzehn Kombinationen aus dem Inventar entfernt wird.

NEW_FINDING: C-08 | BLOCKER | GATE_SOURCE_MAP ist nicht bidirektional belegt; Emissionsstelle, Producermenge und Matrixfall jeder Zeile sind unverifizierter Freitext. test_gate_source_map_and_structural_inventory_are_bidirectionally_closed prueft von einer Zeile nur, dass rule_id, emission und producers nichtleere Strings sind und dass cases Teilmenge einer im Test selbst zusammengestellten Namensmenge ist; diese Menge wird in Zeile 998 ff. um siebzehn frei gewaehlte Bezeichner erweitert, denen kein ausfuehrbarer Fall gegenuebersteht. Von zwanzig Zeilen sind nur drei an einen echten Oraclefall gebunden, zwei weitere Identitaeten werden im Resume-Test beilaeufig emittiert, und fuenfzehn Zeilen emittiert das Modul nirgends. Gegenprobe: eine zusaetzliche Zeile mit emission workflow.this_function_does_not_exist und producers orchestrator.also_missing laesst die Datei vollstaendig gruen. Zweite Gegenprobe: wird das Detail einer klassifizierten Emissionsstelle entfernt, sodass die zugehoerige Zeile verwaist, bleibt der Guard gruen. Dritte Gegenprobe: wird ein bereits registriertes Praefix an eine andere Emissionsstelle unter einem anderen Gategrund verschoben, bleibt der Guard gruen, weil REGISTERED_GATE_PREFIXES eine flache globale Allowlist ist und die je Stelle erlaubte upstream-Menge nirgends durchgesetzt wird. Damit sind die Akzeptanzkriterien "Jede Source-Map-Zeile ist bidirektional belegt: Ihre Producer- und Emissionsstellen existieren und mindestens ein Matrixfall emittiert exakt diese Gateidentitaet. Verwaiste Zeilen lassen den Guard fehlschlagen" und die je-Stelle-Bindung aus Klausel 12 unerfuellt. | Der Guard leitet Emissions- und Producerstellen jeder GATE_SOURCE_MAP-Zeile gegen die AST-Inventur ab, sodass ein nicht existierender Modul- oder Funktionsname rot wird; jede Zeile weist mindestens einen ausfuehrbaren Matrixfall nach, der genau ihr Tripel aus Gategrund, Regelidentitaet und Gateart emittiert; und je variabler Emissionsstelle wird die exakte Menge erlaubter upstream-Regelidentitaeten gebunden. Falsifikationstest in drei Teilen, jeweils mit python3 -m pytest tests/test_workflow_transition_matrix.py -q: eine Zeile mit erfundener Emissions- oder Producerstelle muss rot werden; das Entfernen der Emission einer klassifizierten Zeile muss rot werden; und das Verschieben eines bereits registrierten Praefixes an eine andere klassifizierte Emissionsstelle muss rot werden, bis die dortige upstream-Menge ergaenzt ist.

NEW_FINDING: C-09 | BLOCKER | Vier der vierzehn Oraclespalten tragen fuer die neun verpflichtenden Kanten keine Produktionsaussage, obwohl der Plan sie ausdruecklich als Messgroessen fordert. In _snapshot ab Zeile 761 wird record_count in Zeile 785 als Literal null gesetzt, external_calls in Zeile 787 ebenfalls als Literal null, mirror_count in Zeile 786 aus len(findings) und ledger in Zeile 782 aus _ledger_literal(findings) gebildet, wobei findings das vom Test selbst hingeschriebene Tupel ist und nicht aus dem Zustand, der Recordkette oder dem Mirror stammt; checkpoint_state in Zeile 790 ist eine reine Umkodierung von gate_status und damit keine unabhaengige Aussage. Der Ledgervergleich dieser neun Faelle ist folglich literal gegen literal. Die Behauptung record_count gleich null waere fuer den realen Pfad zudem sachlich falsch, weil Plan-, Review- und Commitrecords dort tatsaechlich anfallen. Belegt durch zwei unabhaengige Gegenproben: sowohl die globale Mutation von orchestrator.replay_findings auf leer als auch die globale Mutation von carry_forward_native_findings auf Durchreichen lassen test_literal_transition_oracle_covers_work_unit_round_and_gate_boundaries vollstaendig gruen. Ergaenzend kommt driver.checkpoint( im gesamten Modul nicht vor, sodass das Fenster zwischen Mirror und Checkpoint nur ueber ein erneutes bind_work_unit geprueft wird. Damit ist das Akzeptanzkriterium "Die tatsaechliche Recordprojektion und der tatsaechliche Mirror werden nach jedem Szenarioschritt jeweils gegen diese unabhaengigen Sollwerte geprueft, nicht lediglich gegeneinander und nicht nur terminal" unerfuellt. | Die Ist-Seite jedes Matrixfalls leitet ledger und mirror_count aus der produktiven Projektion beziehungsweise dem State-v3-Mirror des jeweiligen Zustands ab, record_count aus der tatsaechlichen Laenge der Findingtransitionslinie des Recordstores und external_calls aus einem produktiv gefuehrten Aufrufzaehler; checkpoint_state wird aus einem tatsaechlich geschriebenen Checkpoint bestimmt und nicht aus gate_status. Falsifikationstest: mit python3 -m pytest tests/test_workflow_transition_matrix.py -q muss test_literal_transition_oracle_... rot werden, sobald orchestrator.replay_findings auf ein leeres Ergebnis oder carry_forward_native_findings auf reines Durchreichen mutiert wird, und ebenso, sobald eine der literalen record_count- oder external_calls-Erwartungen einer Kante um eins verschoben wird.

NEW_FINDING: C-10 | OBSERVATION | Der von Phase A Schritt 8 vorgeschriebene Probetest fuer carry_forward_native_findings ist tautologisch. test_replay_and_carry_forward_mutations_turn_matrix_cases_red ersetzt die Methode selbst durch eine Identitaetsfunktion, ruft anschliessend genau diese Ersetzung auf und stellt in pytest.raises(AssertionError) fest, dass deren Rueckgabe nicht dem vierelementigen Sollledger entspricht. Es wird kein Matrixfall erneut ausgefuehrt und kein Produktionscode durchlaufen; der Test bestaetigt nur das Verhalten seines eigenen Patches. Gegenprobe P1 zeigt das direkt: mit der Mutation im Produktivcode bleibt genau dieser Probetest gruen, waehrend test_record_replay_matrix_... aus dem richtigen fachlichen Grund rot wird. Die vom Plan geforderte Schutzwirkung ist heute also erfuellt, aber nur durch einen anderen Test; sie ist nicht ausfuehrbar defekt und deshalb keine Blockade, jedoch unbemerkt verlierbar, sobald die zufaellig deckende Assertion in Zeile 1363 umgebaut wird. | Der Probetest setzt die Mutation auf dem Produktionsobjekt und fuehrt danach denjenigen Matrixfall erneut aus, der den vollstaendigen Ledger behauptet, sodass die Rotfaerbung aus dem Verlust der historischen Findinglinie entsteht und nicht aus einem Vergleich mit dem eigenen Patch.

FINDING_STATUS: C-06 | OPEN | Die erste Haelfte des Akzeptanztests ist nachweislich erfuellt und keine Handliste: PROVIDER-INPUT-BUDGET liegt weder im Literalinventar noch in gates.BUILTIN_STOP_RULES und erscheint ausschliesslich im Ergebnis der AST-Producerverfolgung _assigned_prefixes_reaching_gate, die die lokale Zuweisung in workflow._invoke_role bis in das f-String-Detail von await_bootstrap_resume verfolgt; der Test bindet genau das, und die Zuordnung zur Emissionsstelle ist sachlich korrekt. Auch der geforderte zweite Zuweisungszweig macht den Guard rot, was ich zusaetzlich gegen den realen Quellbaum und nicht nur gegen das synthetische Modul verifiziert habe. Nicht erfuellt ist die Schlussbedingung desselben Akzeptanztests, wonach der Guard rot bleiben muss, bis das neue Tripel samt Producer- und Emissionsstelle klassifiziert und durch einen Matrixfall emittiert ist: der Guard wird bereits durch die blosse Aufnahme in REGISTERED_GATE_PREFIXES wieder gruen, eine Source-Map-Zeile mit erfundener Producer- und Emissionsstelle passiert unbemerkt, und ein emittierender Matrixfall wird nirgends erzwungen. Das Finding bleibt deshalb offen; die zugrunde liegende Mechanik ist als C-08 separat blockierend gefuehrt.

PRE_MORTEM: In drei Monaten scheitert dieses Paket am wahrscheinlichsten daran, dass die gruene Matrix als Vollstaendigkeitsnachweis zitiert wird, obwohl ihre Ledger-, Record-, Aufrufzahl- und Checkpointspalten fuer die verpflichtenden Kanten konstant sind und ihre Gate-Source-Map aus unverifiziertem Freitext besteht. Ein neuer Gatesachverhalt oder eine neue Rundenkante wird dann korrekt in eine Source-Map-Zeile eingetragen, ohne dass je ein Szenario ihn emittiert; der Guard bleibt gruen, die Zeile verwaist beim naechsten Refactoring, und der erste reale Ausfall zeigt sich wieder erst an der naechsten Systemgrenze — genau das Muster, das dieses Paket beenden sollte.

SLICE_APPROVAL: 1 | NO

STATUS: DONE

# Codex-Nacharbeit zu Claude-Slicereview 01

## N1. Disposition der Befunde

FINDING_RESPONSE: C-06 | ACCEPTED | Die Producerverfolgung bleibt erhalten und
ist nun in eine bidirektionale Source-Map-Prüfung eingebettet. Ein neues lokal
zugewiesenes Präfix genügt nicht mehr durch Aufnahme in eine globale Allowlist:
Sein tatsächlicher Ursprungsort muss einer existierenden Producer- oder
Emissionsstelle zugeordnet sein und ein separat literalsierter ausführbarer
Gatefall muss exakt dasselbe Tripel emittieren.

FINDING_RESPONSE: C-07 | ACCEPTED | `plan -> plan` ist als produktiv
erreichbare Ablehnungs-/Revisionskante ergänzt. Der Ist-Zustand wird über
`with_current_step(CLAUDE_PLAN_REVIEW)` und `record_review_denial(...)`
erzeugt und behauptet literal Plan-Work-Unit, Runde 2 und
`codex_plan_revision`. `EDGE_CLASSIFICATION` enthält nun alle 16 geordneten
Kombinationen der vier Work-Unit-Arten genau einmal; die übrigen vier zuvor
fehlenden Kombinationen sind begründet unerreichbar.

FINDING_RESPONSE: C-08 | ACCEPTED | Jede der 20 Source-Map-Zeilen besitzt nun
ein getrenntes Literal in `GATE_CASE_ORACLE` und ein tatsächlich ausgeführtes
Workflowstate-Ereignis, das Gategrund, Regelidentität und Gateart erzeugt. Der
Guard prüft Emissions- und Producerstellen gegen AST-Symbole sowie reale
Gateaufruf-, Direktkonstruktions- und `replace(gate=...)`-Stellen. Er bindet
konkrete Präfixursprünge an genau die erlaubten Stellen. Drei zusätzliche
Falsifikationen weisen nach, dass erfundene Stellen, eine verwaiste
Emissionsfunktion und das Verschieben eines registrierten Präfixes an eine
andere Emissionsstelle rot werden.

FINDING_RESPONSE: C-09 | ACCEPTED | `_snapshot` erhält keine vom Test
hingeschriebenen Findingdaten mehr, sondern ein `TransitionEvidence` aus
produktiven Speichern. Für jede Oraclezeile wird ein echtes temporäres
Git-Repository mit `ProductionWorkflowDriver` und append-only
`ArtifactStore` angelegt. Ledger und Findingrecordzahl stammen aus
`replay_artifacts`/`replay_findings`, der Mirror aus der serialisierten
`WorkflowHistory`, der Aufrufzähler aus tatsächlich vorhandenen
Providerattempt-Records und der Checkpointzustand aus real geschriebenem
State-v3-State und Work-Unit-Checkpoint. Die Recordzahlen sind deshalb nicht
mehr konstant; geschlossene Findings besitzen Opening- und Statusrecord. Die
Korrektur-zu-Final-Kante trägt zusätzlich die historische C-99-Linie über den
produktiven Carry-Forward.

FINDING_RESPONSE: C-10 | ACCEPTED | Der tautologische Patchvergleich wurde
entfernt. Beide Mutationsproben führen jetzt `_exercise_transition_oracle`
vollständig erneut aus. Leeres produktives Replay stoppt an der
Carry-Forward-/Mirror-Invariante; reines Durchreichen verliert C-99 und macht
die literalsierte Korrektur-zu-Final-Zeile rot.

## N2. Ergänzte Abdeckung

- Planreview-Ablehnung: Plan bleibt Plan, Runde 2,
  `codex_plan_revision`, C-01 offen.
- Vollständiger 4-mal-4-Kantenraum mit genau 16 Klassifikationen.
- Zehn ausführbare Übergangs-/Guardtests statt zuvor neun.
- Alle 20 Gate-Source-Map-Zeilen sind an ein unabhängiges Fallliteral und ein
  ausgeführtes Gateereignis gebunden.
- Präfixlose Reviewdenial-, Invocationfailure- und
  Legacy-Quota-Revalidation-Familien werden über ihre echten
  Workflowstate-Übergänge erzeugt.
- Der Checkpoint wird tatsächlich mit `save_workflow_state` und
  `write_workflow_checkpoint` geschrieben; `checkpoint_state` ist keine
  Umkodierung des Gatestatus mehr.
- Die Korrektur-zu-Final-Zeile liest den vollständigen Ledger einschließlich
  C-99 über `carry_forward_native_findings`.

## N3. Analysecheckpoint nach der Nacharbeit

Die Nacharbeit hat Defekte der Phase-A-Testarchitektur geschlossen, aber
weiterhin keinen reproduzierbaren Defekt im Produktivcode gefunden. Sämtliche
neuen roten Kontrollen reagieren auf absichtlich beschädigte Test-, Replay-,
Carry-Forward- oder Source-Map-Evidenz. Phase B bleibt daher gemäß dem
freigegebenen Analysecheckpoint entfallen.

## N4. Validierung

- `python3 -m pytest tests/test_workflow_transition_matrix.py -q -p no:cacheprovider`:
  `10 passed`
- angrenzende Workflowstate-, Replay- und Runtime-Matrix: vollständig grün
- `python3 -m pytest tests/ -v -p no:cacheprovider`:
  `1054 passed in 87.36s`
- externe Provider und `run_task`: nicht aufgerufen

## N5. Erneuter Reviewfokus

Claude soll die Befunde C-06 bis C-10 jeweils unabhängig nachprüfen. Besonders
zu falsifizieren sind:

- eine fehlende der 16 Kantenkombinationen,
- ein weiterhin literal statt record-/mirrorbasiert gewonnenes Evidenzfeld,
- eine Source-Map-Zeile ohne existierenden AST-Ort oder ohne ausgeführten Fall,
- ein bereits registriertes Präfix an einer nicht autorisierten Stelle,
- eine Replay-/Carry-Mutation, unter der das vollständige Oracle grün bleibt.

---

# Claude-Slicereview 01, zweite Runde (manuell, adversarial)

## T1. Reviewgrenze und Methodik

Manuelles adversariales Konvergenzreview außerhalb des Orchestrators. Keine
strukturierte JSON-Ausgabe, kein `run_task`, kein Watcher, kein Bootstrap, kein
Providerabruf. Gelesen wurden `AGENTS.md`, `CLAUDE.md`, der freigegebene
Arbeitsplan, dieser Bericht einschließlich der Codex-Nacharbeit,
`tests/test_workflow_transition_matrix.py` sowie die adressierten
Produktionsstellen in `src/workflow.py`, `src/workflow_state.py`,
`src/orchestrator.py`, `src/gates.py`, `src/state_io.py`,
`src/artifact_models.py`, `src/artifact_bridge.py` und `src/artifact_replay.py`.

Die Nacharbeitsabschnitte habe ich als Befundquelle, nicht als Autorität
behandelt. Sollwerte, Kanten, Gateursprünge und Record-/Mirrorgrenzen habe ich
erneut selbst aus Plan und Produktivcode abgeleitet. Weder Produktcode noch
Tests wurden geändert; alle Gegenproben liefen als In-Memory-Mutationen aus
einem Verzeichnis außerhalb des Repositorys. Die vollständige Suite wurde nicht
erneut ausgeführt.

## T2. Selbst erhobener Repositorystand

- `git diff --stat ff10967`: weiterhin genau eine Zeile im Arbeitsplan; `src/`
  ist gegenüber dem Plancommit unverändert.
- untrackt: `tests/test_workflow_transition_matrix.py` (1922 Zeilen) und dieser
  Bericht.
- selbst ausgeführt: `python3 -m pytest tests/test_workflow_transition_matrix.py -q -p no:cacheprovider`
  → `10 passed in 18.03s`.

## T3. C-07 — Kantenraum: geschlossen

`EDGE_CLASSIFICATION` enthält jetzt 16 Zeilen, alle paarweise verschieden, und
deckt die Menge aller geordneten Paare der vier `WorkUnitKind`-Werte exakt ab;
`len(pairs) == len(WorkUnitKind) ** 2` bindet das zusätzlich an das Enum.

`plan -> plan` habe ich unabhängig über die produktiven Eintrittspunkte
nachgefahren: nach `with_current_step(CLAUDE_PLAN_REVIEW)` und
`record_review_denial(return_step=CODEX_PLAN_REVISION)` — genau die Verzweigung,
die `workflow._apply_review_result` für `unit.kind is WorkUnitKind.PLAN` wählt —
bleibt die Work-Unit-Art `plan`, die Runde wird 2, der Schritt
`codex_plan_revision`, `open_findings` ist `('C-01',)` und die Work-Unit-Anzahl
bleibt 1. Die Oraclezeile `plan-denial-round-2` behauptet genau das, und ihre
Spalte `provider_findings` wird aus `current_work_unit.open_findings` gewonnen.

Falsifikation: Ich habe die literalen Sollwerte dieser Zeile einzeln verfälscht
— `kind` auf `slice`, `round_number` auf 1, `step` auf `codex_plan` und
`ledger` auf leer. Jede Verfälschung macht den Fall rot, jeweils genau an der
verfälschten Spalte.

Die neun als unerreichbar geführten Kombinationen habe ich gegen den
Produktivcode geprüft und bestätigt: jede produktive `start_work_unit`-Stelle
übergibt `kind=WorkUnitKind.SLICE`, sodass nach der Initialisierung keine
zweite Plan-Work-Unit entstehen kann; `start_correction_work_unit` besitzt
genau eine produktive Aufrufstelle in `workflow.py:2042` innerhalb des
Final-Review-Ablehnungszweigs, weshalb `plan -> correction` und
`slice -> correction` konstruktiv ausgeschlossen sind; und derselbe Zweig kehrt
vor jeder erneuten Denial-Aufzeichnung in eine Korrektur-Work-Unit zurück,
weshalb `final_review -> final_review` nicht konstruierbar ist.

## T4. C-09 — Evidenzspalten: bleibt offen

Vier der fünf Evidenzspalten sind echt nachgebessert, eine ist nachweislich
tot.

**Echt produktionsgebunden.** `_transition_evidence` legt je Fall ein reales
temporäres Git-Repository mit `ProductionWorkflowDriver` und append-only
`ArtifactStore` an, schreibt für jedes Finding einen Opening-Record und für
geschlossene zusätzlich einen Statusrecord und liest anschließend zurück:

- `ledger` aus `replay_findings(replay_artifacts(...))`,
- `record_count` aus den tatsächlichen `FindingTransitionPayload`-Records,
- `mirror_count` aus der über `to_dict`/`from_dict` serialisierten
  `WorkflowHistory`,
- `checkpoint_state` aus real geschriebenem State und Work-Unit-Checkpoint.

Die Opening-/Statusrecordzahlen habe ich unabhängig nachgerechnet und bestätigt:
1, 0, 1, 2, 3, 4 und 7 Records für die sieben Findingzeilen, zuletzt aus
dreimal zwei Records für geschlossene plus einem für die historische Linie. Die
Spalten variieren real über `{0, 1, 2, 3, 4, 7}` beziehungsweise
`{0, 1, 2, 3, 4}` und sind damit keine Konstanten mehr. Falsifikationen von
`record_count` und `mirror_count` machen jeweils die betroffene Zeile rot.

Dass `checkpoint_state` echte Persistenz bindet, habe ich einzeln geprüft:
Neutralisiert man `write_workflow_checkpoint` oder `save_workflow_state`,
wird die Matrix rot. Es ist keine Umkodierung des Gatestatus mehr.

**Tot: `external_calls`.** Die Spalte zählt

    getattr(record.payload.record_type, "value", "").startswith("provider_attempt_")

Der produktive Enumwert ist jedoch `RecordType.PROVIDER_ATTEMPT ==
"provider_attempt"` — ohne abschließenden Unterstrich. Kein einziger der
neunzehn `RecordType`-Werte beginnt mit `provider_attempt_`. Das Prädikat ist
strukturell unerfüllbar.

Gegenprobe: Ich habe über die produktive Bridge eine Messung angelegt, mit
`start_provider_attempt` und `finish_provider_attempt` zwei echte
Providerattempt-Records in die Kette geschrieben und danach exakt den Ausdruck
aus `_transition_evidence` ausgewertet. Ergebnis: zwei reale
`ProviderAttemptPayload`-Records in der Kette, gezählte `external_calls` gleich
null. Die Spalte kann für keine mögliche Recordkette einen anderen Wert als
null annehmen; ihre Rotfärbung bei verfälschtem Sollwert beweist deshalb nichts
über Produktionsverhalten. Damit ist die Zusage der Nacharbeit, der Aufrufzähler
stamme aus tatsächlich vorhandenen Providerattempt-Records, nicht erfüllt, und
die Messgröße „erneute Provideraufrufe nach Resume“ aus §6 des Arbeitsplans
bleibt unbelegt.

**Unterbrechungsfenster nach Mirror, vor Checkpoint.** `checkpoint_state` nimmt
über alle zehn Zeilen ausschließlich den Wert `persisted` an; der Wert `missing`
ist in keinem Szenario erreichbar, weil State und Checkpoint unmittelbar
nacheinander und bedingungslos geschrieben werden. Im
Record-Replay-Test belegt derselbe Abschnitt das Fenster weiterhin über
`driver.bind_work_unit(mirrored)`, und sein Kommentar verweist unverändert auf
die Runtime-Suite. Die von Phase A Schritt 5 geforderte *Simulation* der
Unterbrechung — Mirror geschrieben, Checkpoint noch nicht — existiert damit
nach wie vor in keinem Fall.

Record-Ahead, Mirror-Ahead und die beschädigte Linie sind unverändert korrekt
und fail-closed belegt: zusätzliches Mirror-Finding gegen
`carry_forward_native_findings`, fehlendes Mirror-Finding gegen
`authoritative_native_findings` und der Statusübergang ohne Opening-Record mit
stabilem `RECORD-REFERENCE-MISSING`.

## T5. C-10 — Mutationsproben: geschlossen

Beide Proben führen jetzt `_exercise_transition_oracle` vollständig erneut aus;
der tautologische Vergleich der Patchfunktion mit ihrer eigenen Rückgabe ist
entfernt. Ich habe beide Mutationen unabhängig außerhalb des Probetests gesetzt
und den Abbruchort bestimmt:

- Leeres produktives Replay stoppt in `_transition_evidence` an der
  Carry-Forward-Invariante mit
  `record-native finding carry-forward differs from the state-v3 mirror`. Das
  ist der fachlich richtige fail-closed-Grund.
- Reines Durchreichen des Carry-Forward scheitert nach sechs grünen Fällen
  exakt am Fall `correction-to-final`, und zwar an genau einer Spalte:
  `mirror_count` erwartet 4, tatsächlich 3. Das ist der Verlust der
  historischen `C-99`-Linie und damit der geforderte Grund.

Ich habe zusätzlich geprüft, dass die Ledgerspalte diesen Verlust *nicht*
verdeckt: Sie stammt aus dem Replay der Recordkette, der Mirror hingegen aus
`carry_forward_native_findings`. Der historische Ledger wird also tatsächlich
über die produktive Carry-Forward-Projektion gewonnen und nicht bloß zusätzlich
in den Erwartungswert geschrieben.

## T6. C-08 — Source-Map: bleibt offen

Der Fortschritt ist erheblich und in den entscheidenden Punkten echt.
`_source_map_errors` prüft jede Zeile gegen AST-Symbole und gegen die realen
Emissions-, Direktkonstruktions- und `replace(gate=...)`-Orte, verlangt für
jeden Producer ein existierendes Symbol, erzwingt über
`case_ids == {case_id for row in GATE_SOURCE_MAP for case_id in row.cases}` eine
Bijektion zwischen Source-Map und `GATE_CASE_ORACLE` und vergleicht je Fall das
vollständige Tripel. Nominelle Fallnamen ohne ausgeführten Fall sind damit
ausgeschlossen. Die drei präfixlosen Familien Reviewdenial,
Invocationfailure und Legacy-Quota-Revalidation werden über echte
Workflowstate-Übergänge erzeugt, und ihre Grammatiken habe ich gegen die
produktiven Detailliterale geprüft — alle vier passen wörtlich.

Meine beiden Gegenproben aus der ersten Runde sind jetzt teils geschlossen:
Das Verschieben eines registrierten Präfixes an eine fremde Emissionsstelle
wird erkannt und meldet gleichzeitig `unbound-origin` für die verwaiste und
`unexpected-origin` für die unautorisierte Regel.

Offen bleiben zwei Bindungen:

**Erstens, die fehlende Umkehrbindung der Präfixmenge.** `_source_map_errors`
iteriert ausschließlich über die vorhandenen Zeilen. Eine in `src/` entdeckte
konkrete Regelidentität *ohne* Source-Map-Zeile wird nur von
`_unknown_gate_prefixes` gesehen, und dort genügt die Aufnahme in
`REGISTERED_GATE_PREFIXES`, um sie verschwinden zu lassen. Nichts verknüpft
diese Allowlist mit der Source-Map. Heute stimmen beide Mengen zufällig
überein; die Gleichheit ist nirgends behauptet. Damit ist die vom Plan
geforderte Vollständigkeit weiterhin einseitig. Der Nachweis steht in T7.

**Zweitens, die Gateart ist nicht produktionsgebunden.** `_execute_gate_case`
verzweigt anhand des in `GATE_CASE_ORACLE` hinterlegten `kind` und
`_gate_identity` gibt genau dieses `kind` zurück. Die Duplikation der beiden
Literale fängt einseitige Änderungen ab, aber eine in beiden Literalen
konsistent geänderte Gateart bleibt unentdeckt. Gegenprobe: Für
`BRANCH-MISMATCH`, `CONTRACT-UNCLEAR`, `CODEX-NOT-READY` und
`PROVIDER-INPUT-BUDGET` bleibt der Guard vollständig grün, wenn ich sie in
Source-Map und Fallorakel gemeinsam von `policy` beziehungsweise `resume` auf
`user` umklassifiziere, obwohl der Produktivcode sie über `await_policy_gate`
beziehungsweise `await_bootstrap_resume` emittiert. Nur die Gegenrichtung ist
gedeckt, und das nicht durch den Guard, sondern durch die Produktionsprüfung
`policy gate reason must be stop_request or unexpected_file`. Die zweite
Komponente des Tripels ist damit belegt, die dritte nicht.

## T7. C-06 — bleibt offen

Die erste Hälfte des Akzeptanztests ist unverändert echt erfüllt:
`PROVIDER-INPUT-BUDGET` liegt weder im Literalinventar noch in
`gates.BUILTIN_STOP_RULES` und wird ausschließlich durch die
AST-Producerverfolgung aus der lokalen Zuweisung in `workflow._invoke_role`
gewonnen. Ein an dieser Stelle eingefügter Zweig `code = "ZZZ-DYNAMIC"` macht
den Guard gegen den realen Quellbaum rot.

Die zweite Hälfte verlangt ausdrücklich, dass der Guard rot *bleibt*, bis
Producer- und Emissionsbindung sowie ein eigener ausführbarer Fall vorliegen.
Genau diese Falsifikation habe ich durchgeführt, und sie schlägt fehl: Nach dem
Einfügen von `ZZZ-DYNAMIC` an der realen Producerstelle genügt es, die Kennung
in `REGISTERED_GATE_PREFIXES` aufzunehmen. Danach ist
`_unknown_gate_prefixes` leer *und* `_source_map_errors` leer — ohne
Source-Map-Zeile, ohne Producer-/Emissionsbindung, ohne
`GATE_CASE_ORACLE`-Eintrag und ohne einen einzigen ausführbaren Fall. Der
Guard ist vollständig grün.

Damit ist der ursprüngliche Akzeptanztest von C-06 nicht bestanden, und der
Befund trägt in dieser Runde blockierendes Gewicht. Die Ursache ist dieselbe
fehlende Umkehrbindung wie in T6 erster Punkt; beide Findings bleiben deshalb
offen und beschreiben denselben Mechanismus aus ihrer jeweiligen Richtung.

## T8. Checkpoint und Phase-B-Entscheidung

Die Checkpointgrenze wird jetzt durch die echten Persistenzfunktionen
`save_workflow_state` und `write_workflow_checkpoint` belegt und nicht mehr
durch `bind_work_unit` oder eine Umkodierung des Gatestatus; das ist der in
C-09 verlangte Fortschritt. Was fehlt, ist der Unterbrechungsfall selbst, siehe
T4.

Zur Phase-B-Frage: Ich habe erneut **keinen** reproduzierbaren Defekt im
Produktivcode gefunden. Jede von mir unabhängig nachgefahrene Übergangs-,
Runden-, Gate-, Record- und Mirrorgrenze verhielt sich korrekt, und sämtliche
roten Kontrollen reagieren auf absichtlich beschädigte Test-, Replay-,
Carry-Forward- oder Source-Map-Evidenz. Ein Produktivcodeeingriff ist
dementsprechend nicht gerechtfertigt und das Entfallen von Phase B bleibt
sachlich richtig. Slice 01 wird ausschließlich wegen der verbliebenen Defekte
der Phase-A-Matrix abgelehnt; diese Ablehnung fordert Testarbeit, keinen
Produktivcode.

## T9. Ausgeführte Gegenproben

| # | Gegenprobe | Erwartung | Ergebnis |
|---|---|---|---|
| Q0 | Matrix ausführen | 10 passed | `10 passed in 18.03s` — bestätigt |
| Q1 | `plan -> plan` produktiv nachfahren | Plan, Runde 2, `codex_plan_revision`, `C-01` angeboten | alle vier bestätigt |
| Q2 | 16 Kombinationen auf Eindeutigkeit und Vollständigkeit | vollständig | 16 Zeilen, 16 eindeutig, deckt alle Paare |
| Q3 | `kind`, `round_number`, `step`, `ledger` der Planzeile verfälscht | je rot | je rot an der verfälschten Spalte |
| Q4 | `record_count` und `mirror_count` verfälscht | je rot | je rot |
| Q5 | Zwei echte Providerattempt-Records erzeugt, `external_calls` ausgewertet | Zähler steigt | Zähler bleibt 0 — Spalte strukturell tot |
| Q6 | `write_workflow_checkpoint` bzw. `save_workflow_state` neutralisiert | rot | je rot — Checkpointspalte bindet echte Persistenz |
| Q7 | Wertebereiche der Evidenzspalten | variabel | `record_count` 0–7, `mirror_count` 0–4, `external_calls` nur 0, `checkpoint_state` nur `persisted` |
| Q8 | `orchestrator.replay_findings` auf leer, vollständiger Oraclelauf | fail-closed | `record-native finding carry-forward differs from the state-v3 mirror` |
| Q9 | `carry_forward_native_findings` auf Durchreichen, vollständiger Oraclelauf | Korrektur-zu-Final rot | rot genau bei `correction-to-final`, Spalte `mirror_count` 4 gegen 3 |
| Q10 | Registriertes Präfix an fremde Emissionsstelle verschoben | rot | `unbound-origin:HEAD-DRIFT` und `unexpected-origin:CONTRACT-UNCLEAR:orchestrator.commit_slice` |
| Q11 | Durchgereichtes Detail einer klassifizierten Stelle entfernt | rot | grün — Bindung ist funktions-, nicht aufrufstellengenau |
| Q12 | Gateart in Source-Map und Fallorakel gemeinsam umklassifiziert | rot | grün für vier Regeln; Gegenrichtung nur durch Produktionsvalidierung gedeckt |
| Q13 | `ZZZ-DYNAMIC` an realer Producerstelle eingefügt | rot | rot |
| Q14 | danach `ZZZ-DYNAMIC` nur in die globale Präfixmenge aufgenommen | weiterhin rot | vollständig grün — C-06 zweite Hälfte verfehlt |
| Q15 | Präfixlose Grammatiken gegen produktive Detailliterale | wörtliche Übereinstimmung | alle vier gefunden, Legacy-Detail zweizeilig konstruiert |
| Q16 | Produktive Erzeugbarkeit einer zweiten Plan-Work-Unit | ausgeschlossen | jede `start_work_unit`-Stelle übergibt `kind=SLICE` |

## T10. Restrisiken ohne Befundstatus

- Die Bindung von Source-Map-Zeilen an Emissionsorte ist funktionsgenau, nicht
  aufrufstellengenau. Verliert eine von mehreren Gateaufrufstellen derselben
  Funktion ihr Detail, bleibt die Zeile formal belegt (Q11).
- Beide Mutationsproben prüfen nur, *dass* eine Zusicherung bricht, nicht
  welche. Heute ist der Bruchort nachweislich der richtige, aber ein künftiger
  unabhängiger Fehlschlag in einem früheren Fall würde die Probe ebenfalls
  bestehen lassen.
- `_source_trees` liest `src/*.py` weiterhin ohne Rekursion; `src/` ist flach,
  das Risiko bleibt latent.
- `_prefix_origins` ordnet jedes modulweite Literal der Kennung
  `<modul>.BUILTIN_STOP_RULES` zu, auch in Modulen ohne diese Konstante. Für die
  heutigen Regeln trifft die Zuordnung zu, sie ist aber keine echte
  Symbolauflösung.

REVIEWER: claude

FINDING_STATUS: C-06 | OPEN | Die erste Haelfte des Akzeptanztests bleibt nachweislich erfuellt: PROVIDER-INPUT-BUDGET liegt weder im Literalinventar noch in gates.BUILTIN_STOP_RULES und wird ausschliesslich durch die AST-Producerverfolgung aus der lokalen Zuweisung in workflow._invoke_role gewonnen; ein an der realen Producerstelle eingefuegter Zweig code gleich ZZZ-DYNAMIC macht den Guard gegen den echten Quellbaum rot. Die zweite Haelfte ist verfehlt. Der Akzeptanztest verlangt, dass der Guard rot bleibt, bis Producer- und Emissionsbindung sowie ein eigener ausfuehrbarer Fall vorliegen. Tatsaechlich genuegt nach der Injektion die blosse Aufnahme der Kennung in REGISTERED_GATE_PREFIXES: danach sind sowohl _unknown_gate_prefixes als auch _source_map_errors leer, obwohl weder eine GATE_SOURCE_MAP-Zeile noch ein GATE_CASE_ORACLE-Eintrag noch eine Producer-/Emissionsbindung noch ein ausgefuehrter Fall existiert. Ursache ist die fehlende Umkehrbindung zwischen entdeckter Praefixmenge und Source-Map, die als C-08 gefuehrt wird. Solange eine neue Regelidentitaet allein durch einen Allowlisteintrag stillgelegt werden kann, ist der von C-06 geforderte Nachweis nicht gefuehrt.

FINDING_STATUS: C-07 | CLOSED | EDGE_CLASSIFICATION enthaelt 16 paarweise verschiedene Zeilen und deckt ueber len(pairs) gleich len(WorkUnitKind) hoch zwei alle geordneten Kombinationen der vier Work-Unit-Arten genau einmal ab. Die Kante plan zu plan ist als erreichbar gefuehrt und durch die Oraclezeile plan-denial-round-2 ausfuehrbar belegt; ich habe sie unabhaengig ueber with_current_step(CLAUDE_PLAN_REVIEW) und record_review_denial(return_step=CODEX_PLAN_REVISION) nachgefahren und Work-Unit-Art plan, Runde 2, Schritt codex_plan_revision, angebotene Findingmenge C-01 sowie unveraenderte Work-Unit-Anzahl bestaetigt. Einzelverfaelschungen von kind, round_number, step und ledger dieser Zeile machen den Fall jeweils an der verfaelschten Spalte rot. Die neun Unerreichbarkeitsaussagen habe ich gegen den Produktivcode geprueft: jede produktive start_work_unit-Stelle uebergibt kind gleich WorkUnitKind.SLICE, start_correction_work_unit besitzt genau eine Aufrufstelle im Final-Review-Ablehnungszweig in workflow.py Zeile 2042, und derselbe Zweig wechselt vor jeder erneuten Denial-Aufzeichnung in eine Korrektur-Work-Unit. Damit ist der Kantenraum vollstaendig und korrekt klassifiziert.

FINDING_STATUS: C-08 | OPEN | Die Bidirektionalitaet ist in den wesentlichen Punkten hergestellt: _source_map_errors prueft Emissionsstellen gegen AST-Symbole und gegen die realen Gateaufruf-, Direktkonstruktions- und replace(gate=...)-Orte, verlangt existierende Producer, erzwingt eine Bijektion zwischen GATE_SOURCE_MAP-Faellen und GATE_CASE_ORACLE und vergleicht je Fall das vollstaendige Tripel; nominelle Fallnamen ohne ausgefuehrten Fall sind ausgeschlossen, und das Verschieben eines registrierten Praefixes an eine fremde Emissionsstelle wird mit unbound-origin und unexpected-origin erkannt. Zwei Bindungen fehlen. Erstens iteriert die Pruefung ausschliesslich ueber vorhandene Zeilen; eine in src entdeckte konkrete Regelidentitaet ohne Source-Map-Zeile wird nur von _unknown_gate_prefixes gesehen, wo ein Eintrag in REGISTERED_GATE_PREFIXES genuegt, um sie stillzulegen. Die Gleichheit dieser Allowlist mit den konkreten Source-Map-Kennungen besteht heute zufaellig und ist nirgends behauptet. Zweitens ist die Gateart nicht produktionsgebunden: _execute_gate_case verzweigt anhand des im Fallorakel hinterlegten kind und _gate_identity gibt genau dieses kind zurueck, sodass eine in beiden Literalen konsistent geaenderte Gateart unentdeckt bleibt; fuer BRANCH-MISMATCH, CONTRACT-UNCLEAR, CODEX-NOT-READY und PROVIDER-INPUT-BUDGET bleibt der Guard gruen, wenn ich sie gemeinsam von policy beziehungsweise resume auf user umklassifiziere, obwohl der Produktivcode sie ueber await_policy_gate beziehungsweise await_bootstrap_resume emittiert.

FINDING_STATUS: C-09 | OPEN | Vier der fuenf Evidenzspalten sind echt nachgebessert und ueber ein reales temporaeres Repository mit ProductionWorkflowDriver und append-only ArtifactStore gewonnen: ledger aus replay_findings ueber replay_artifacts, record_count aus tatsaechlichen FindingTransitionPayload-Records, mirror_count aus der serialisierten WorkflowHistory und checkpoint_state aus real geschriebenem State und Checkpoint. Die Opening- und Statusrecordzahlen habe ich unabhaengig nachgerechnet, die Spalten variieren real, Verfaelschungen werden rot, und das Neutralisieren von save_workflow_state oder write_workflow_checkpoint macht die Matrix rot. Die fuenfte Spalte ist tot: external_calls zaehlt Records, deren record_type-Wert mit provider_attempt_ beginnt, waehrend der produktive Enumwert RecordType.PROVIDER_ATTEMPT exakt provider_attempt lautet und keiner der neunzehn RecordType-Werte mit provider_attempt_ beginnt. Ich habe ueber die produktive Bridge zwei echte Providerattempt-Records in eine Kette geschrieben und den Ausdruck aus _transition_evidence darauf ausgewertet: der Zaehler bleibt null. Die Spalte kann fuer keine moegliche Recordkette einen anderen Wert annehmen, sodass die Messgroesse erneuter Provideraufrufe unbelegt bleibt. Zusaetzlich nimmt checkpoint_state ueber alle zehn Zeilen ausschliesslich den Wert persisted an, weil State und Checkpoint bedingungslos nacheinander geschrieben werden; die von Phase A Schritt 5 geforderte Simulation der Unterbrechung nach Mirror und vor Checkpoint existiert weiterhin in keinem Fall, und der entsprechende Abschnitt des Record-Replay-Tests belegt sie unveraendert nur ueber bind_work_unit.

FINDING_STATUS: C-10 | CLOSED | Der tautologische Vergleich der Patchfunktion mit ihrer eigenen Rueckgabe ist entfernt; beide Proben fuehren _exercise_transition_oracle vollstaendig erneut aus. Ich habe beide Mutationen unabhaengig ausserhalb des Probetests gesetzt und den Abbruchort bestimmt: leeres produktives Replay stoppt in _transition_evidence an der Carry-Forward-Invariante mit der Meldung record-native finding carry-forward differs from the state-v3 mirror, also fail-closed aus dem fachlich richtigen Grund; reines Durchreichen des Carry-Forward laeuft durch sechs gruene Faelle und scheitert exakt am Fall correction-to-final an genau einer Spalte, naemlich mirror_count mit erwartet 4 gegen tatsaechlich 3, was dem Verlust der historischen C-99-Linie entspricht. Ich habe zusaetzlich geprueft, dass die Ledgerspalte diesen Verlust nicht verdeckt, weil sie aus dem Recordreplay stammt, waehrend der Mirror der Korrektur-zu-Final-Zeile tatsaechlich ueber carry_forward_native_findings gewonnen wird. Als Restrisiko bleibt, dass die Proben nur pruefen, dass eine Zusicherung bricht, und nicht welche; das ist nicht ausfuehrbar defekt und in T10 dokumentiert.

PRE_MORTEM: In drei Monaten scheitert dieses Paket am wahrscheinlichsten daran, dass eine neue Gateidentitaet oder ein neuer Providerpfad eingefuehrt und der Guard mit einem einzigen Eintrag in REGISTERED_GATE_PREFIXES beruhigt wird, weil nichts diese Allowlist an die Source-Map bindet. Die Matrix bleibt gruen, die neue Identitaet besitzt weder Producer- noch Emissions- noch Fallbindung, und parallel meldet der Aufrufzaehler weiterhin null Provideraufrufe, weil sein Praefixvergleich keinen existierenden Recordtyp trifft. Der erste reale Ausfall zeigt sich dann wieder an einer Systemgrenze, die die Matrix formal abzudecken behauptet.

SLICE_APPROVAL: 1 | NO

STATUS: DONE

# Codex-Nacharbeit zur zweiten Claude-Reviewrunde

## N1. Umfang

Die offenen Befunde C-06, C-08 und C-09 wurden ausschließlich in der
providerfreien Übergangsmatrix nachgearbeitet. Produktivcode blieb unverändert.
Die von Claude bereits geschlossenen Findings C-07 und C-10 wurden nicht erneut
geöffnet oder umgedeutet.

## N2. C-06 und C-08 — vollständige Source-Map- und Gateartbindung

Die zweite unabhängig editierbare Präfix-Allowlist ist entfallen. Die
registrierte konkrete Präfixmenge wird jetzt ausschließlich aus den vollständig
gebundenen Zeilen der `GATE_SOURCE_MAP` abgeleitet. Der Guard vergleicht die im
Produktivquelltext entdeckten konkreten Gateidentitäten in beide Richtungen mit
dieser Menge. Eine neue Kennung bleibt daher solange `unclassified-rule`, bis
sie eine Source-Map-Zeile mit Producer, Emissionsstelle und ausführbarem Fall
besitzt.

Die Gateart eines Source-Map-Eintrags wird nicht mehr nur zwischen zwei
Testliteralen verglichen. Eine AST-Auswertung ermittelt sie zusätzlich aus der
wirklich verwendeten Produktions-API (`await_user_gate`, `await_policy_gate`,
`await_bootstrap_resume` sowie den direkten Gate-Record-Übergängen). Eine
gemeinsame Umklassifizierung von Source-Map und Fallorakel bleibt dadurch nicht
mehr unentdeckt.

Neue Falsifikationen belegen beide Grenzen:

- Wird `PROVIDER-INPUT-BUDGET` an seiner echten variablen Producerstelle durch
  `ZZZ-DYNAMIC` ersetzt, melden sowohl Präfixinventur als auch Source-Map-Guard
  die nicht klassifizierte Identität. Es gibt keine eigenständige Allowlist
  mehr, die den Fehler ohne vollständige Bindung stilllegen könnte.
- Werden `BRANCH-MISMATCH` in Source-Map und Fallorakel gemeinsam von `policy`
  auf `user` geändert, meldet der Guard
  `kind-mismatch:BRANCH-MISMATCH:user:policy`, weil die Produktionsstelle
  weiterhin `await_policy_gate` verwendet.

## N3. C-09 — lebender Providerzähler und echtes Checkpointfenster

Die Messung externer Aufrufe zählt nun reale `ProviderAttemptPayload`-Records
mit Phase `started`. Die unmögliche Präfixprüfung auf `provider_attempt_` ist
entfernt. Für die entscheidungstragenden Übergangsfälle schreibt der Harness
über `ArtifactBridge.start_provider_attempt` und
`ArtifactBridge.finish_provider_attempt` einen echten, abgeschlossenen
Providerattempt. Die literale Sollspalte erwartet dort genau einen Aufruf und
bei reinen Gatefällen null. Eine Änderung des Sollwerts um eins wird damit
gegen variable Recordevidenz geprüft.

Der zusätzliche literale Fall `mirror-before-checkpoint` schreibt den echten
State-v3-Mirror mit `save_workflow_state`, unterlässt gezielt
`write_workflow_checkpoint` und erwartet deshalb `checkpoint_state=missing`.
Die übrigen Fälle erwarten nach beiden realen Schreiboperationen
`checkpoint_state=persisted`. Das Fenster nach Mirror und vor Checkpoint ist
damit erstmals tatsächlich simuliert und nicht mehr durch `bind_work_unit`
ersetzt.

## N4. Fokussierte Validierung

`python3 -m pytest tests/test_workflow_transition_matrix.py -q -p no:cacheprovider`
ergab nach der finalen AST-Namensauflösung `10 passed in 35.84s`.

Die anschließend einmal ausgeführte vollständige Repositorysuite
`python3 -m pytest tests/ -v -p no:cacheprovider` ergab
`1054 passed in 104.80s`.

FINDING_RESPONSE: C-06 | ACCEPTED | Die Präfixklassifikation besitzt keine unabhängig pflegbare Umgehungs- oder Allowlist mehr; eine neue dynamisch zugeführte Kennung bleibt bis zur vollständigen Source-Map- und Fallbindung rot.

FINDING_RESPONSE: C-08 | ACCEPTED | Präfixinventar und Source-Map sind beidseitig mengengebunden, und die Gateart wird zusätzlich aus der realen Produktions-API der Emissionsstelle abgeleitet und falsifiziert.

FINDING_RESPONSE: C-09 | ACCEPTED | Der Aufrufzähler misst echte gestartete Providerattempt-Records, und ein eigener Oraclefall simuliert den geschriebenen Mirror bei noch fehlendem Checkpoint.

TEST_FILES_TOUCHED: tests/test_workflow_transition_matrix.py

IMPLEMENTATION_READY: 1 | YES

STATUS: DONE

---

# Claude-Slicereview 01, dritte Runde (manuell, adversarial)

## U1. Reviewgrenze und Methodik

Manuelles adversariales Konvergenzreview außerhalb des Orchestrators, ohne
strukturierte JSON-Ausgabe, ohne `run_task`, Watcher, Bootstrap oder
Providerabruf. Gelesen wurden `AGENTS.md`, `CLAUDE.md`, der freigegebene
Arbeitsplan, dieser Bericht einschließlich der jüngsten Nacharbeit,
`tests/test_workflow_transition_matrix.py` (2235 Zeilen) sowie die adressierten
Produktionsstellen in `src/workflow.py`, `src/workflow_state.py`,
`src/orchestrator.py`, `src/gates.py`, `src/state_io.py`,
`src/artifact_models.py`, `src/artifact_bridge.py` und `src/artifact_replay.py`.

Kanten, Gatearten, Gategründe, Recordzahlen, Providerattempts, Mirrorinhalte und
Checkpointzustände habe ich erneut selbst erhoben, teils durch eigene
AST-Auswertung des Produktivquelltextes, teils durch real erzeugte
Testevidenz. Weder Produktcode noch Tests wurden geändert; alle Gegenproben
liefen als In-Memory-Mutationen aus einem Verzeichnis außerhalb des
Repositorys. Der Arbeitsbaum ist nach dem Review unverändert. Die vollständige
Suite wurde nicht erneut ausgeführt.

Selbst ausgeführt: `python3 -m pytest tests/test_workflow_transition_matrix.py -q -p no:cacheprovider`
→ `10 passed in 35.64s`. `src/` ist gegenüber `ff10967` unverändert.

## U2. C-06 — geschlossen

Die zweite, unabhängig editierbare Präfixliste ist tatsächlich entfallen:
`REGISTERED_GATE_PREFIXES` wird jetzt aus den konkreten `GATE_SOURCE_MAP`-Zeilen
abgeleitet, und `_source_map_errors` vergleicht die im Produktivquelltext
entdeckte Identitätsmenge in beide Richtungen mit dieser Menge
(`unclassified-rule` und `stale-rule`).

Ich habe die geforderte Falsifikation durchgeführt und zusätzlich jeden
Einzelweg geprüft, mit dem sich der Guard beruhigen ließe:

- `ZZZ-DYNAMIC` an der realen variablen Producerstelle in
  `workflow._invoke_role` → `_unknown_gate_prefixes` meldet `{'ZZZ-DYNAMIC'}`,
  `_source_map_errors` meldet `unclassified-rule:ZZZ-DYNAMIC`, der vollständige
  Guardtest wird rot.
- Ein zusätzlicher Eintrag in der Regelregistry `BUILTIN_STOP_RULES` beseitigt
  den Fehler nicht; die Kennung bleibt `unclassified-rule`, weil die Registry in
  die entdeckte Menge eingeht statt sie zu verkleinern.
- Ein Eintrag in `GATE_FOREIGN_PREFIXES` entfernt zwar den
  `unclassified-rule`-Fehler, lässt den Guardtest aber trotzdem rot, weil
  `set(GATE_FOREIGN_PREFIXES) <= _literal_prefix_inventory(trees)` scheitert:
  eine nur lokal zugewiesene Kennung ist kein präfixförmiges Literal.

Damit kann kein einzelner Registry- oder Allowlist-Eintrag die dynamisch
zugeführte Identität stilllegen; sie bleibt rot, bis Source-Map-Zeile,
Producer, Emissionsstelle und ausführbarer Fall vorliegen. Beide Hälften des
ursprünglichen Akzeptanztests sind erfüllt. C-06 ist geschlossen.

## U3. C-08 — bleibt offen, und C-11 als konkrete Folge

Zwei der drei Tripelkomponenten sind jetzt echt produktionsgebunden.

**Regelidentität: gebunden.** `_prefix_origins` zusammen mit
`unbound-origin` und `unexpected-origin` hält jede konkrete Kennung an ihren
autorisierten Emissions- und Producerstellen fest; das Verschieben eines
registrierten Präfixes an eine fremde Stelle wird erkannt.

**Gateart: neu gebunden.** `_production_gate_kinds` leitet die Gateart aus der
wirklich verwendeten Produktions-API ab — `await_user_gate`,
`await_policy_gate`, `await_bootstrap_resume`, den direkten
`GateRecord`-Übergängen sowie dem `status`-Feld — und löst importierte
Regelkonstanten gezielt über `_module_rule_ids` aus ihrem Ursprungsmodul auf.
Ich habe für alle 20 Zeilen nachgerechnet, dass die deklarierte Gateart in der
produktionsseitig abgeleiteten Menge liegt, und die geforderte gemeinsame
Mutation sowie vier weitere geprüft: `BRANCH-MISMATCH`, `CONTRACT-UNCLEAR`,
`CODEX-NOT-READY` und `PROVIDER-INPUT-BUDGET` erzeugen bei gemeinsamer
Umklassifizierung auf `user` jeweils `kind-mismatch:<regel>:user:<produktion>`,
`HEAD-DRIFT` auf `policy` ebenso. Die von mir in Runde zwei berichtete
zirkuläre Bestätigung der Gateart ist damit beseitigt.

**Gategrund: weiterhin zirkulär.** Der dritte Bestandteil des Tripels wird
nirgends aus der Produktion abgeleitet. `_source_map_errors` vergleicht
`row.reason` ausschließlich gegen `case_results`, und `_execute_gate_case`
erzeugt den Gategrund seinerseits aus dem im Fallorakel hinterlegten Wert über
`GateReason(reason)`. Eine in beiden Literalen gemeinsam geänderte
Gateursache bleibt deshalb unentdeckt. Gegenproben, jeweils vollständig grün:
`PLAN-APPROVAL` von `plan_approval` auf `stop_request`, `HEAD-DRIFT` und
`UNEXPECTED-PATH` von `unexpected_file` auf `stop_request`, `BRANCH-MISMATCH`
und `CODEX-NOT-READY` von `stop_request` auf `unexpected_file`,
`QUOTA-RESUME-DIFF` von `quota_resume_diff` auf `stop_request`. Gerade die
Drift- und Scopefälle, die der Plan ausdrücklich gegen Verwechslung schützen
will, teilen sich `GateReason.UNEXPECTED_FILE`; ihre Unterscheidung hängt an
genau dieser Komponente.

**Zweite Lücke: Mengenprüfung statt Tripelprüfung.** `_production_gate_kinds`
liefert eine *Menge*, und der Guard verlangt nur `row.kind in production_kinds`.
Emittiert dieselbe Regelidentität produktiv mehrere verschiedene Tripel, genügt
die Deklaration eines einzigen davon.

Beides zusammen hat einen realen, heute vorhandenen Inventurfehler verdeckt.
Ich habe die produktionsseitige Tripelmenge unabhängig per AST erhoben — je
Gateaufruf der tatsächliche `reason`, die tatsächliche API und die im
`detail` aufgelösten Regelidentitäten — und gegen `GATE_SOURCE_MAP`
abgeglichen. Genau ein produktiv emittiertes Tripel fehlt im Inventar:

    ('stop_request', 'QUOTA-RESUME-DIFF', 'policy')
      src/workflow.py:2343  await_policy_gate(reason=GateReason.STOP_REQUEST,
                              detail="QUOTA-RESUME-DIFF | no persisted Slice
                              fingerprint is available for safe resume")
      src/workflow.py:2359  await_policy_gate(reason=GateReason.STOP_REQUEST,
                              detail="QUOTA-RESUME-DIFF | could not revalidate
                              repository changes: ...")

Die Source-Map kennt für diese Kennung nur das Nutzergate
`('quota_resume_diff', 'QUOTA-RESUME-DIFF', 'user')` aus
`src/workflow.py:2376`. Der fachlich davon verschiedene Sachverhalt — ein
fail-closed Policystopp, wenn für einen sicheren Resume kein persistierter
Fingerprint vorliegt oder die Revalidierung scheitert — besitzt weder
Source-Map-Zeile noch `GATE_CASE_ORACLE`-Fall noch Matrixabdeckung, und der
Guard bleibt grün. Das führe ich als `C-11`; `C-08` bleibt als
Detektormechanismus offen.

Alle übrigen konkreten Identitäten habe ich mit derselben Methode geprüft und
korrekt zugeordnet gefunden. Für `HEAD-DRIFT`, `CONTRACT-UNCLEAR`,
`PRODUCTIVE-FILE-LIMIT` und `PROVIDER-INPUT-BUDGET` ist der Gategrund nicht am
Aufrufort sichtbar, weil er über `detail=exc.detail`, die Regelregistry oder
`await_bootstrap_resume` zugeführt wird; ich habe ihre deklarierten Gründe
einzeln am Quelltext bestätigt.

Die übrigen Prüfpunkte der Source-Map sind erfüllt: Emissionsstellen existieren
als AST-Symbol und tragen nachweislich eine inventarisierte Gateemission,
Direktkonstruktion oder `replace(gate=...)`; alle Producerstellen existieren;
die Zuordnung zu `GATE_CASE_ORACLE` ist bijektiv; präfixlose Familien besitzen
vollständige Grammatiken, deren Detailliterale ich am Produktivcode
wiedergefunden habe.

## U4. C-09 — geschlossen

**Aufrufzähler.** Die unerfüllbare Präfixprüfung ist ersetzt durch
`isinstance(record.payload, ProviderAttemptPayload) and payload.phase ==
"started"`. Ich habe unabhängig über `ArtifactBridge` eine Messung, einen
`start_provider_attempt` und einen `finish_provider_attempt` geschrieben: die
Kette enthält zwei Recordrevisionen mit den Phasen `started` und `succeeded`,
und der Zähler meldet genau einen externen Aufruf. Die sieben Übergangsfälle
und der neue Unterbrechungsfall erwarten literal genau einen Aufruf, die drei
reinen Gatefälle null; beide Werte habe ich am erzeugten Zustand bestätigt.
Verschiebungen des literalen Sollwerts um eins werden in beide Richtungen rot
(0, 2 statt 1 sowie 1 statt 0).

**Checkpointfenster.** Der neue Fall `mirror-before-checkpoint` schreibt den
Mirror mit `save_workflow_state` und unterlässt gezielt
`write_workflow_checkpoint`. Auf Dateisystemebene geprüft: `state.json`
existiert, ist lesbar und trägt eine nichtleere `runtime_history`, während im
Checkpointverzeichnis null Dateien liegen; alle anderen elf Fälle schreiben
beide Artefakte und ergeben `persisted`. `checkpoint_state` stammt damit
nachweislich aus diesen Dateien und nicht aus einem Gate- oder Oraclemuster.
Beide Richtungen der Falsifikation werden rot. Die vom Plan geforderte
Unterbrechung nach Mirror und vor Checkpoint ist damit erstmals wirklich
simuliert und nicht mehr durch `bind_work_unit` ersetzt.

Record-Ahead, Mirror-Ahead und die beschädigte Linie bleiben unverändert
fail-closed belegt. Einen ausführbaren Resume- oder Idempotenzdefekt konnte ich
an dieser Grenze nicht reproduzieren; die verbleibende Tiefe ist in
`REVIEW_EVIDENCE` als Restrisiko festgehalten. C-09 ist geschlossen.

## U5. C-07 und C-10 — keine Regression

`EDGE_CLASSIFICATION` enthält weiterhin genau 16 paarweise verschiedene Zeilen
über alle geordneten Kombinationen der vier Work-Unit-Arten, und `plan -> plan`
bleibt produktiv belegt: Art `plan`, Runde 2, Schritt `codex_plan_revision`,
angebotene Findingmenge `C-01`.

Beide Mutationsproben führen weiterhin den vollständigen Oraclelauf aus. Leeres
produktives Replay stoppt fail-closed an der Carry-Forward-Invariante mit
`record-native finding carry-forward differs from the state-v3 mirror`; reines
Durchreichen läuft durch sieben grüne Fälle und scheitert exakt bei
`correction-to-final` an der einen Spalte `mirror_count` mit 4 gegen 3, dem
Verlust der historischen Linie. Beide Findings bleiben geschlossen.

## U6. Phase-B-Entscheidung

Ich habe erneut keine Matrixabweichung gefunden, die einen Produktivcodefehler
belegt. `C-11` ist ein Inventurfehler der Matrix, kein Fehlverhalten des
Produktivcodes: die beiden Policygates in `workflow._revalidate_waiting_diff`
sind sachlich richtig und fail-closed; sie sind lediglich nicht inventarisiert.
Der test-only Ausgang von Phase A bleibt daher korrekt und Phase B entfällt.
Die Ablehnung von Slice 01 fordert Test- und Inventurarbeit, keinen
Produktivcodeeingriff.

## U7. Ausgeführte Gegenproben

| # | Gegenprobe | Erwartung | Ergebnis |
|---|---|---|---|
| R0 | Matrix ausführen | 10 passed | `10 passed in 35.64s` |
| R1 | `REGISTERED_GATE_PREFIXES` aus Source-Map abgeleitet | keine zweite Liste | bestätigt |
| R2 | `ZZZ-DYNAMIC` an realer Producerstelle | rot | `unclassified-rule:ZZZ-DYNAMIC`, Guardtest rot |
| R3 | zusätzlich Eintrag in `BUILTIN_STOP_RULES` | bleibt rot | bleibt rot |
| R4 | zusätzlich Eintrag in `GATE_FOREIGN_PREFIXES` | bleibt rot | bleibt rot, Literalprüfung scheitert |
| R5 | Gateart je Zeile gegen Produktions-API | 20 von 20 stimmig | 20 von 20 |
| R6 | gemeinsame Gateartmutation, 6 Regeln | je rot | je `kind-mismatch` bzw. Produktionsvalidierung |
| R7 | gemeinsame Gategrundmutation, 6 Regeln | je rot | je grün — Gategrund nicht produktionsgebunden |
| R8 | eigene AST-Tripelinventur gegen Source-Map | vollständig | ein unbelegtes Tripel: `stop_request/QUOTA-RESUME-DIFF/policy` |
| R9 | echter Start-/Terminal-Attempt über `ArtifactBridge` | 2 Revisionen, 1 Aufruf | 2 Revisionen (`started`, `succeeded`), Zähler 1 |
| R10 | `external_calls` literal um eins verschoben, beide Richtungen | je rot | je rot |
| R11 | Gatefälle ohne Attempt | null Aufrufe | null |
| R12 | Dateisystem je Fall geprüft | Mirror da, Checkpoint fehlt | `mirror-before-checkpoint`: `state.json` lesbar, 0 Checkpointdateien; 11 andere Fälle je 1 |
| R13 | `checkpoint_state` literal getauscht, beide Richtungen | je rot | je rot |
| R14 | 16 Kantenkombinationen und `plan -> plan` | unverändert | bestätigt |
| R15 | Replay- und Carry-Mutation, vollständiger Oraclelauf | fachlich richtiger Bruch | bestätigt, Bruchort und Spalte identifiziert |

REVIEWER: claude

NEW_FINDING: C-11 | BLOCKER | Ein produktiv emittiertes Gateidentitaetstripel fehlt vollstaendig im Inventar. workflow._revalidate_waiting_diff emittiert die Regelidentitaet QUOTA-RESUME-DIFF an drei Stellen unter zwei verschiedenen Tripeln: in src/workflow.py Zeile 2376 als Nutzergate mit GateReason.QUOTA_RESUME_DIFF, und in src/workflow.py Zeile 2343 sowie Zeile 2359 als Policygate mit GateReason.STOP_REQUEST, wenn kein persistierter Slice-Fingerprint fuer einen sicheren Resume vorliegt beziehungsweise die Revalidierung der Repositoryaenderungen scheitert. GATE_SOURCE_MAP kennt nur das Nutzergate. Das Policygate besitzt weder eine Source-Map-Zeile noch einen GATE_CASE_ORACLE-Fall noch eine Matrixabdeckung, und der Guard bleibt gruen, weil _production_gate_kinds eine Menge liefert und nur die Mitgliedschaft von row.kind geprueft wird, waehrend der Gategrund ueberhaupt nicht aus der Produktion abgeleitet wird. Damit ist das Akzeptanzkriterium verletzt, wonach die Source-Map jedes inventarisierte Tripel aus Gategrund, Regelidentitaet und Gateart abdeckt und jeder produktiv erreichbare Sachverhalt genau einmal klassifiziert ist; ein sicherheitsrelevanter fail-closed Resume-Stopp bleibt unbelegt. | GATE_SOURCE_MAP enthaelt eine eigene Zeile fuer das Tripel stop_request, QUOTA-RESUME-DIFF, policy mit Emissionsstelle workflow._revalidate_waiting_diff und beiden Producerstellen, GATE_CASE_ORACLE enthaelt einen zugehoerigen ausfuehrbaren Fall, der genau dieses Tripel ueber await_policy_gate erzeugt, und der Guard leitet die vollstaendige Tripelmenge je Regelidentitaet aus der Produktion ab, sodass ein nicht deklariertes Tripel als eigener Fehler gemeldet wird. Falsifikationstest: python3 -m pytest tests/test_workflow_transition_matrix.py -q -p no:cacheprovider muss rot werden, sobald die neue Zeile oder ihr ausfuehrbarer Fall entfernt wird, und ebenso, sobald eine der beiden Policygate-Emissionen in src/workflow.py auf eine andere Gateart oder einen anderen Gategrund geaendert wird.

FINDING_STATUS: C-06 | CLOSED | Die unabhaengig editierbare Praefix-Allowlist ist entfallen; REGISTERED_GATE_PREFIXES wird aus den konkreten GATE_SOURCE_MAP-Zeilen abgeleitet, und _source_map_errors vergleicht die im Produktivquelltext entdeckte Identitaetsmenge beidseitig ueber unclassified-rule und stale-rule. Die geforderte Falsifikation habe ich vollstaendig durchgefuehrt: ZZZ-DYNAMIC an der realen variablen Producerstelle in workflow._invoke_role macht Praefixinventur, Source-Map-Guard und den gesamten Guardtest rot. Kein einzelner Registry- oder Allowlist-Eintrag beseitigt das: ein Eintrag in BUILTIN_STOP_RULES vergroessert die entdeckte Menge und laesst unclassified-rule bestehen, und ein Eintrag in GATE_FOREIGN_PREFIXES scheitert an der Zusicherung, dass jede gatefremde Kennung ein praefixfoermiges Literal im Quelltext besitzt, was fuer eine nur lokal zugewiesene Kennung nicht gilt. Die Identitaet bleibt damit rot, bis Source-Map-Zeile, Producer, Emissionsstelle und ausfuehrbarer Fall gemeinsam vorliegen. Beide Haelften des urspruenglichen Akzeptanztests sind erfuellt.

FINDING_STATUS: C-07 | CLOSED | Unveraendert geschlossen und ohne Regression. EDGE_CLASSIFICATION enthaelt weiterhin genau 16 paarweise verschiedene Zeilen ueber alle geordneten Kombinationen der vier Work-Unit-Arten, an len(WorkUnitKind) hoch zwei gebunden. Die Kante plan zu plan bleibt produktiv belegt: nachgefahren ueber with_current_step(CLAUDE_PLAN_REVIEW) und record_review_denial(return_step=CODEX_PLAN_REVISION) ergeben sich Work-Unit-Art plan, Runde 2, Schritt codex_plan_revision und angebotene Findingmenge C-01, genau wie die Oraclezeile plan-denial-round-2 es literal behauptet.

FINDING_STATUS: C-08 | OPEN | Zwei der drei Tripelkomponenten sind jetzt echt gebunden. Die Regelidentitaet haelt ueber _prefix_origins mit unbound-origin und unexpected-origin an ihren autorisierten Stellen, und die Gateart wird durch _production_gate_kinds aus der wirklich verwendeten Produktions-API samt GateRecord-Status abgeleitet, wobei importierte Regelkonstanten ueber _module_rule_ids gezielt aus ihrem Ursprungsmodul aufgeloest werden; ich habe alle 20 Zeilen nachgerechnet und sechs gemeinsame Umklassifizierungen geprueft, die saemtlich als kind-mismatch oder durch Produktionsvalidierung rot werden. Offen bleibt der Gategrund: er wird nirgends aus der Produktion abgeleitet, sondern nur zwischen row.reason und dem vom Fallorakel erzeugten GateReason verglichen, sodass sechs von mir gepruefte gemeinsame Aenderungen der Gateursache vollstaendig gruen bleiben, darunter HEAD-DRIFT und UNEXPECTED-PATH von unexpected_file auf stop_request. Zweitens prueft der Guard nur die Mitgliedschaft von row.kind in einer Menge produktionsseitiger Gatearten statt der vollstaendigen Tripelmenge, sodass eine Regelidentitaet mit mehreren produktiven Tripeln durch Deklaration eines einzigen als abgedeckt gilt. Beide Luecken zusammen haben den als C-11 berichteten realen Inventurfehler verdeckt.

FINDING_STATUS: C-09 | CLOSED | Der Aufrufzaehler misst echte Evidenz: er prueft isinstance auf ProviderAttemptPayload und die Phase started statt der unerfuellbaren Zeichenkette. Unabhaengig ueber ArtifactBridge erzeugt, ergeben ein start_provider_attempt und ein finish_provider_attempt zwei Recordrevisionen mit den Phasen started und succeeded und genau einen gezaehlten externen Aufruf; die sieben Uebergangsfaelle und der Unterbrechungsfall erwarten literal eins, die drei reinen Gatefaelle null, und Verschiebungen des Sollwerts um eins werden in beide Richtungen rot. Der neue Fall mirror-before-checkpoint schreibt den Mirror mit save_workflow_state und unterlaesst write_workflow_checkpoint; auf Dateisystemebene geprueft existiert state.json mit lesbarer nichtleerer runtime_history, waehrend das Checkpointverzeichnis null Dateien enthaelt, und alle uebrigen elf Faelle schreiben beide Artefakte und ergeben persisted. checkpoint_state stammt damit aus diesen Dateien und nicht aus einem Gate- oder Oraclemuster. Die vom Plan geforderte Unterbrechung nach Mirror und vor Checkpoint ist erstmals wirklich simuliert; einen ausfuehrbaren Resume- oder Idempotenzdefekt an dieser Grenze konnte ich nicht reproduzieren.

FINDING_STATUS: C-10 | CLOSED | Unveraendert geschlossen und ohne Regression. Beide Mutationsproben fuehren den vollstaendigen _exercise_transition_oracle-Lauf erneut aus. Ausserhalb des Probetests gesetzt, stoppt leeres produktives Replay fail-closed an der Carry-Forward-Invariante mit der Meldung record-native finding carry-forward differs from the state-v3 mirror, und reines Durchreichen des Carry-Forward laeuft durch sieben gruene Faelle und scheitert exakt beim Fall correction-to-final an genau einer Spalte, mirror_count mit erwartet 4 gegen tatsaechlich 3, was dem Verlust der historischen C-99-Linie entspricht.

REVIEW_EVIDENCE: geprueft wurden Kantenraum und Rundenkanten, Ledger-, Record-, Mirror-, Aufruf- und Checkpointevidenz auf Dateisystemebene, Praefixinventur und Source-Map in beide Richtungen, produktionsseitige Gateart und eine eigene AST-Tripelinventur, praefixlose Grammatiken gegen die Produktionsliterale sowie beide Replay- und Carry-Mutationen | groesstes Restrisiko ist GATE_FOREIGN_PREFIXES als verbliebene, einzeln editierbare Ausnahmeliste: fuer eine statisch als praefixfoermiges Literal emittierte neue Kennung genuegt ein Eintrag, um Praefixinventur und Source-Map-Guard vollstaendig gruen zu machen, und geprueft wird nur die Existenz des Literals, nicht die behauptete Gatefremdheit; daneben bleibt die Bindung von Source-Map-Zeilen an Emissionsorte funktions- statt aufrufstellengenau, beide Mutationsproben pruefen nur dass eine Zusicherung bricht und nicht welche, und der Unterbrechungsfall haelt den Zustand fest, ohne aus ihm heraus fortzusetzen und Idempotenz von Record und Provideraufruf zu behaupten | realistische Bruchbedingung ist ein neuer, statisch emittierter Gate-Praefix, der unter Zeitdruck als gatefremd deklariert statt inventarisiert wird, oder eine Emissionsstelle, die eine bereits klassifizierte Kennung unter einem weiteren Gategrund ausgibt

PRE_MORTEM: In drei Monaten scheitert dieses Paket am wahrscheinlichsten daran, dass eine bereits inventarisierte Regelidentitaet an einer weiteren Stelle unter einem anderen Gategrund oder einer anderen Gateart emittiert wird. Weil der Guard den Gategrund nicht aus der Produktion ableitet und die Gateart nur auf Mitgliedschaft in einer Menge prueft, bleibt das zweite Tripel unsichtbar, genau wie heute beim Policygate der Quota-Resume-Revalidierung. Die Matrix meldet weiterhin vollstaendige Abdeckung, waehrend ein sicherheitsrelevanter fail-closed Stopp ununtersucht bleibt und erst im Betrieb an der naechsten Systemgrenze auffaellt.

SLICE_APPROVAL: 1 | NO

STATUS: DONE

# Codex-Nacharbeit zu Slice 01, vierte Runde

## Umgesetzte Korrekturen

Die Nacharbeit bleibt vollständig in der test-only Phase A. Produktcode wurde
nicht geändert.

- `GATE_SOURCE_MAP` enthält nun zwei getrennte Zeilen für die beiden produktiven
  Formen von `QUOTA-RESUME-DIFF`: das fingerprintgebundene Nutzergate
  `quota_resume_diff/user` und den fail-closed Policystopp
  `stop_request/policy`.
- `GATE_CASE_ORACLE` enthält für den Policystopp einen eigenen ausführbaren Fall.
- Der Guard leitet neben der Gateart nun auch den Gategrund aus dem wirklichen
  Produktionsaufruf ab und prüft das Paar `(reason, kind)` gegen jede
  Source-Map-Zeile. Gemeinsame Änderungen an Mapping und Fallorakel können den
  Produktionsgrund dadurch nicht mehr umdefinieren.
- Direkt am Gateaufruf sichtbare konkrete Regelidentitäten werden als
  vollständige Tripel `(reason, rule_id, kind)` inventarisiert. Jedes solche
  Produktionstripel muss in der Source-Map vorkommen; das Entfernen der neuen
  Policyzeile erzeugt deshalb `unclassified-triplet`.
- Die beiden Policy-Aufrufstellen in `_revalidate_waiting_diff` sind zusätzlich
  über eine erwartete direkte Emissionsanzahl von zwei gebunden. Eine Änderung
  von Gategrund oder Gate-API an nur einer der beiden Stellen erzeugt sowohl ein
  neues unklassifiziertes Tripel als auch eine Kardinalitätsabweichung.
- Der indirekt über `WorkflowCommitApprovalRequired.detail` transportierte
  `HEAD-DRIFT`-Fall ist nicht mehr nur an die Gesamtmenge der Gateaufrufe in
  `_commit` gebunden. Der Guard verfolgt den konkreten Exceptiontyp vom
  `orchestrator.commit_slice`-Producer bis zum passenden `except`-Handler und
  leitet dort `unexpected_file/user` ab.

## Falsifikationsnachweise

Die Matrix wird gezielt rot bei:

1. Entfernen der neuen Policy-Source-Map-Zeile;
2. Entfernen des ausführbaren Policyfalls;
3. gemeinsamer Umdefinition von `HEAD-DRIFT` in Source-Map und Fallorakel;
4. Änderung einer der zwei Policyemissionen von `STOP_REQUEST` auf
   `UNEXPECTED_FILE`;
5. Änderung einer der zwei Policyemissionen von `await_policy_gate` auf
   `await_user_gate`.

Die produktionsseitige Inventur bestätigt für `QUOTA-RESUME-DIFF` exakt zwei
Policyemissionen mit `stop_request/policy` und eine Nutzeremission mit
`quota_resume_diff/user` in `workflow._revalidate_waiting_diff`.

## Validierung

- Fokussiert: `10 passed in 48.16s`.
- Vollständig: `1054 passed in 114.58s`.
- `git diff --check`: sauber; ausschließlich der bereits vorhandene
  LF/CRLF-Hinweis am Arbeitsplan wurde ausgegeben.

FINDING_RESPONSE: C-08 | ACCEPTED | Gategrund und Gateart werden jetzt gemeinsam aus den produktiven Gateaufrufen abgeleitet und gegen jede Source-Map-Zeile geprüft. Direkt sichtbare Identitäten ergeben vollständige Produktionstripel; die Exceptionweiterleitung von HEAD-DRIFT ist zusätzlich producer- und handlergebunden. Gemeinsame Änderungen an Source-Map und Fallorakel bleiben dadurch nicht mehr grün.

FINDING_RESPONSE: C-11 | ACCEPTED | Das fehlende Tripel stop_request/QUOTA-RESUME-DIFF/policy besitzt nun eine eigene Source-Map-Zeile und einen ausführbaren Fall. Die zwei produktiven Policy-Aufrufstellen und die eine Nutzer-Aufrufstelle werden direkt aus dem AST gezählt; Entfernung oder Änderung einer einzelnen Stelle wird durch unclassified-triplet beziehungsweise emission-count-mismatch erkannt.

TEST_FILES_TOUCHED: tests/test_workflow_transition_matrix.py

IMPLEMENTATION_READY: 1 | YES

STATUS: DONE

---

# Claude-Slicereview 01, vierte Runde (manuell, adversarial)

## V1. Reviewgrenze und Methodik

Manuelles adversariales Konvergenzreview außerhalb des Orchestrators, ohne
`run_task`, Watcher, strukturierte JSON-Ausgabe oder Providerabruf. Verbindlich
gelesen wurden `AGENTS.md`, `CLAUDE.md`, der freigegebene Arbeitsplan, der
vollständige bisherige Slicebericht einschließlich meiner dritten Runde und der
darauf folgenden Nacharbeit, `tests/test_workflow_transition_matrix.py`
(2510 Zeilen) sowie die adressierten Produktionsmodule `src/workflow.py`,
`src/workflow_state.py`, `src/orchestrator.py`, `src/gates.py`,
`src/state_io.py`, `src/artifact_models.py`, `src/artifact_bridge.py` und
`src/artifact_replay.py`.

Die Tripelinventur für `QUOTA-RESUME-DIFF` habe ich **nicht** mit den Helfern
des Testmoduls erhoben, sondern mit einem eigenen, von Grund auf geschriebenen
AST-Walker über `src/*.py`, damit die Gegenprobe nicht dieselben blinden Flecken
erbt wie der geprüfte Guard. Weder Produktcode noch Tests wurden geändert; alle
Mutationen liefen im Speicher aus einem Verzeichnis außerhalb des Repositorys.
Der Arbeitsbaum ist nach dem Review unverändert, `src/` gegenüber `ff10967`
identisch. Die vollständige Suite wurde nicht erneut ausgeführt.

Selbst ausgeführt: `python3 -m pytest tests/test_workflow_transition_matrix.py -q -p no:cacheprovider`
→ `10 passed in 46.45s`.

## V2. Vollständige Tripelinventur für `QUOTA-RESUME-DIFF`

Mein unabhängiger Walker — eigene Auflösung von Gate-API, `reason`-Argument,
`GateRecord`-Status, lokalen Zuweisungen und f-String-Präfixen — findet für
diese Regelidentität genau drei produktive Emissionen, alle in
`workflow._revalidate_waiting_diff`:

| Aufrufstelle | API | Gategrund | Gateart |
|---|---|---|---|
| `src/workflow.py:2343` | `await_policy_gate` | `stop_request` | policy |
| `src/workflow.py:2359` | `await_policy_gate` | `stop_request` | policy |
| `src/workflow.py:2376` | `await_user_gate` | `quota_resume_diff` | user |

Damit existieren zwei fachlich verschiedene Formen: der fail-closed Policystopp,
wenn kein persistierter Slice-Fingerprint für einen sicheren Resume vorliegt
oder die Revalidierung der Repositoryänderungen scheitert, und das
fingerprintgebundene Nutzergate bei tatsächlich veränderter Arbeitskopie.

`GATE_SOURCE_MAP` enthält jetzt beide Formen als getrennte Zeilen mit
identischer Emissionsstelle und den Kardinalitäten `direct_emission_count=2`
beziehungsweise `1`. Beide Werte stimmen mit meiner unabhängigen Zählung
überein. `GATE_CASE_ORACLE` enthält für jede Form einen eigenen Fall
(`quota-resume-diff` und `quota-resume-diff-policy`), und beide erzeugen ihr
Gate über echte Zustandsübergänge — `await_user_gate` mit
`GateReason.QUOTA_RESUME_DIFF` und `resume_step` beziehungsweise
`await_policy_gate` mit `GateReason.STOP_REQUEST`. Die Zuordnung
Source-Map zu Fallorakel ist bijektiv.

Über alle konkreten Regelidentitäten hinweg habe ich die Differenz beider
Mengen gebildet: es gibt kein direkt emittiertes Produktionstripel, das nicht
deklariert wäre. Die drei deklarierten, aber nicht direkt emittierten Tripel
(`HEAD-DRIFT`, `CONTRACT-UNCLEAR`, `PRODUCTIVE-FILE-LIMIT`) sind genau die
indirekt zugeführten Fälle; ihre Bindung ist unten behandelt. Die
Präfixherkunft von `QUOTA-RESUME-DIFF` ist auf
`workflow._revalidate_waiting_diff` beschränkt; die Musterkonstante in
`workflow_state` erzeugt keine konkurrierende Herkunft.

## V3. Produktionsbindung statt Zirkularität

Der von mir in Runde drei berichtete zirkuläre Gategrund ist beseitigt.
`_gate_reason_from_call` liest das `reason`-Argument des echten
Produktionsaufrufs und fällt für die vier Zustands-APIs ohne explizites
Argument auf deren feste Ursache zurück; `_production_gate_pairs` prüft das
**Paar** `(reason, kind)` gegen jede Source-Map-Zeile. Ich habe für alle
21 Zeilen nachgerechnet, aus welcher Quelle das Paar stammt: fünfzehn über
direkten Regelidentitätstreffer am Aufruf, eine über den Exceptionhandler,
vier über eine eindeutige Einzelmenge der Emissionsfunktion. In keinem Fall
liegt die deklarierte Kombination außerhalb der produktionsseitig abgeleiteten
Menge.

`_direct_gate_triples` bildet zusätzlich eine unabhängige Menge vollständiger
Produktionstripel für alle am Aufruf sichtbaren Identitäten; ein nicht
deklariertes zweites Tripel erscheint als `unclassified-triplet`. Genau dieser
Mechanismus fängt den in Runde drei gefundenen Defekt.

Die indirekte Weiterleitung von `HEAD-DRIFT` ist nicht mehr an irgendein
Gatepaar derselben Funktion gebunden. `_forwarded_exception_gate_pairs`
verfolgt den konkreten Exceptiontyp `WorkflowCommitApprovalRequired` bis in den
zugehörigen `except`-Handler in `workflow._commit` und akzeptiert dort nur
Aufrufe, deren `detail` genau die gebundene Exceptionvariable dereferenziert;
`_forwarded_exception_has_rule_origin` verlangt zusätzlich, dass ein deklarierter
Producer diese Exception mit dieser Regelidentität wirklich auslöst. Ich habe
die Gegenprobe von der Produktionsseite gefahren: ändert man im echten Handler
den Gategrund auf `GateReason.MANUAL_SLICE`, meldet der Guard
`reason-kind-mismatch:HEAD-DRIFT:unexpected_file:user:manual_slice/user`. Die
Bindung greift also am richtigen Handler und nicht an dem im selben `_commit`
liegenden Manual-Slice-Gate.

**Kreuzkombinationen.** Ich habe gezielt nach gemeinsam geänderten
Source-Map- und Orakelpaaren gesucht, die trotz abweichender Produktion grün
bleiben:

- gemeinsame Gategrundänderung `CONTRACT-UNCLEAR` auf `unexpected_file` →
  `reason-kind-mismatch`, rot;
- gemeinsame Gategrundänderung `HEAD-DRIFT` auf `stop_request` →
  `reason-kind-mismatch`, rot;
- vollständiges Vertauschen der beiden `QUOTA-RESUME-DIFF`-Zeilen in Source-Map
  **und** Fallorakel → zweifacher `emission-count-mismatch`, rot, weil die
  Kardinalitäten an die realen Aufrufstellen gebunden sind.

Grün bleibt eine Kombination, die ich als Restrisiko und nicht als Defekt
führe: Verschiebt man die *deklarierte* Emissionsstelle einer registrygespeisten
Zeile auf eine andere Funktion mit demselben Paar — etwa `CONTRACT-UNCLEAR` von
`workflow._halt_for_stop_request` auf `workflow._run_codex` —, wird das nicht
erkannt. Die heutigen Deklarationen sind nachweislich korrekt: der generische
Stop-Rule-Sink liegt in `workflow._halt_for_stop_request` mit
`detail=f"{stop_request.rule_id} | {rationale}"`, und
`PRODUCTIVE-FILE-LIMIT` entsteht in `workflow._apply_pre_agent_policy_gates`
aus `evaluate_productive_file_limit`. Es handelt sich um eine mögliche
Fehlbeschreibung auf der Testseite, nicht um eine falsche Aussage über den
heutigen Produktivcode.

## V4. Geforderte Falsifikationen

Alle fünf wurden einzeln und isoliert ausgeführt. Entscheidend ist nicht nur
die Rotfärbung, sondern die auslösende Invariante; ich habe deshalb jeweils die
konkrete Fehlerkennung von `_source_map_errors` erhoben und zusätzlich
bestätigt, dass der fokussierte Guardtest selbst rot wird.

| Falsifikation | Fehlerkennung | Quelle der Invariante |
|---|---|---|
| Policy-Source-Map-Zeile entfernt | `unclassified-triplet:stop_request:QUOTA-RESUME-DIFF:policy` | aus dem AST erhobenes Produktionstripel |
| ausführbarer Policyfall entfernt | `case-mismatch:QUOTA-RESUME-DIFF:quota-resume-diff-policy` | Bindung Zeile zu ausgeführtem Fall |
| `HEAD-DRIFT` gemeinsam auf anderen Gategrund | `reason-kind-mismatch:HEAD-DRIFT:stop_request:user:unexpected_file/user` | Exceptionhandler der echten Emission |
| eine Policyemission `STOP_REQUEST` → `UNEXPECTED_FILE` | `emission-count-mismatch:...:1:2` und `unclassified-triplet:unexpected_file:QUOTA-RESUME-DIFF:policy` | Produktionstripel und Aufrufstellenzahl |
| eine Policyemission `await_policy_gate` → `await_user_gate` | `emission-count-mismatch:...:1:2` und `unclassified-triplet:stop_request:QUOTA-RESUME-DIFF:user` | Produktionstripel und Aufrufstellenzahl |

Keine dieser Meldungen stammt aus einem bloßen Literalvergleich zwischen den
beiden Testtabellen; alle fünf entstehen aus dem Produktivquelltext.

## V5. Regressionen der geschlossenen Befunde

- **C-06**: `REGISTERED_GATE_PREFIXES` bleibt aus den konkreten
  Source-Map-Zeilen abgeleitet. `ZZZ-DYNAMIC` an der realen Producerstelle in
  `workflow._invoke_role` macht den Guard rot, und weder ein zusätzlicher
  Eintrag in der Regelregistry noch einer in `GATE_FOREIGN_PREFIXES` beseitigt
  das. Geschlossen.
- **C-07**: `EDGE_CLASSIFICATION` enthält weiterhin genau 16 eindeutige Zeilen
  über alle geordneten Kombinationen, und `plan -> plan` bleibt produktiv
  belegt mit Art `plan`, Runde 2, Schritt `codex_plan_revision` und angebotener
  Findingmenge `C-01`. Geschlossen.
- **C-09**: `external_calls` bleibt an echte gestartete Providerattempts
  gebunden; Verschiebungen des Sollwerts sind in beide Richtungen rot, auch für
  die Gatefälle mit erwarteter Null. Auf Dateisystemebene erneut geprüft:
  `mirror-before-checkpoint` besitzt eine lesbare `state.json` mit genau einem
  gespiegelten Finding und null Checkpointdateien, die übrigen elf Fälle je
  eine. Beide Richtungen der Checkpointfalsifikation sind rot. Geschlossen.
- **C-10**: Beide Mutationsproben führen den vollständigen Oraclelauf aus.
  Leeres produktives Replay stoppt fail-closed mit `record-native finding
  carry-forward differs from the state-v3 mirror`; reines Durchreichen
  scheitert exakt bei `correction-to-final` an `mirror_count` mit 4 gegen 3.
  Geschlossen.

**Überanpassung.** Der Test enthält keine einzige Referenz auf eine konkrete
Zeilennummer des Produktivcodes. Die strukturellen Erwartungen sind `Counter`
und Kardinalitäten, also reihenfolgeunabhängig; der Reviewdenial-Fall zählt
nicht bis zu einer festen Rundenzahl, sondern läuft bis zum tatsächlichen
Gateeintritt. Eine Anpassung an eine einzelne heute bekannte Aufrufreihenfolge
habe ich nicht gefunden.

## V6. Phase-B-Grenze

Auch in dieser Runde belegt keine Matrixabweichung einen Produktivcodefehler.
Die beiden Policygates in `workflow._revalidate_waiting_diff` sind sachlich
richtig und fail-closed; `C-11` war ausschließlich eine Inventurlücke der
Matrix. Der test-only Ausgang von Phase A bleibt damit korrekt, und Phase B
entfällt gemäß dem freigegebenen Analysecheckpoint. Produktivcode ist
gegenüber `ff10967` unverändert.

## V7. Ausgeführte Gegenproben

| # | Gegenprobe | Ergebnis |
|---|---|---|
| S0 | fokussierte Matrix | `10 passed in 46.45s` |
| S1 | eigener AST-Walker, Tripel für `QUOTA-RESUME-DIFF` | 2 Policy- und 1 Nutzeremission, deckungsgleich mit den Kardinalitäten 2 und 1 |
| S2 | Differenz direkte Produktionstripel gegen Deklaration | kein undeklariertes Tripel |
| S3 | Herkunft des Präfixes `QUOTA-RESUME-DIFF` | ausschließlich `workflow._revalidate_waiting_diff` |
| S4 | Paarquelle je Source-Map-Zeile | 15 direkt, 1 weitergeleitet, 4 eindeutige Einzelmenge; keine Zeile unbestimmt |
| S5 | fünf geforderte Falsifikationen | je rot, je aus der beabsichtigten produktionsgebundenen Invariante |
| S6 | Kreuzkombination Gategrund `CONTRACT-UNCLEAR` | `reason-kind-mismatch`, rot |
| S7 | Kreuzkombination Vertauschen beider `QUOTA`-Zeilen | zweifacher `emission-count-mismatch`, rot |
| S8 | Kreuzkombination verschobene deklarierte Emissionsstelle | grün, als Restrisiko geführt |
| S9 | Produktionsseitige Änderung des `HEAD-DRIFT`-Handlers | `reason-kind-mismatch`, rot |
| S10 | `ZZZ-DYNAMIC` plus Registry- beziehungsweise Fremdpräfixeintrag | je rot |
| S11 | Evidenzspalten `external_calls` und `checkpoint_state` | beidseitig falsifizierbar, dateibasiert bestätigt |
| S12 | Replay- und Carry-Mutation | fachlich richtiger Bruchort und Spalte |
| S13 | Suche nach Zeilennummern- oder Reihenfolgeabhängigkeit | keine gefunden |

REVIEWER: claude

FINDING_STATUS: C-08 | CLOSED | Gategrund und Gateart werden jetzt gemeinsam aus dem echten Produktionsaufruf abgeleitet. _gate_reason_from_call liest das reason-Argument der Emission und faellt fuer die vier Zustands-APIs auf deren feste Ursache zurueck; _production_gate_pairs prueft das Paar gegen jede Zeile. Ich habe fuer alle 21 Zeilen die Quelle des Paares bestimmt: fuenfzehn ueber direkten Regelidentitaetstreffer am Aufruf, eine ueber den Exceptionhandler, vier ueber eine eindeutige Einzelmenge der Emissionsfunktion; keine Zeile bleibt unbestimmt. Die in Runde drei berichtete Zirkularitaet ist beseitigt: gemeinsame Aenderungen von Source-Map und Fallorakel werden erkannt, geprueft an CONTRACT-UNCLEAR und HEAD-DRIFT, und eine produktionsseitige Aenderung des HEAD-DRIFT-Handlers meldet reason-kind-mismatch. Die Weiterleitung ueber exc.detail ist an den konkreten Exceptiontyp, den zugehoerigen except-Handler und die Dereferenzierung genau der gebundenen Exceptionvariablen gebunden, zusaetzlich an einen Producer, der diese Exception mit dieser Regelidentitaet wirklich ausloest. Ergaenzend bildet _direct_gate_triples eine unabhaengige Menge vollstaendiger Produktionstripel, sodass ein nicht deklariertes zweites Tripel als unclassified-triplet erscheint.

FINDING_STATUS: C-11 | CLOSED | Das fehlende Tripel besitzt eine eigene Source-Map-Zeile und einen eigenen ausfuehrbaren Fall. Meine unabhaengige, nicht mit den Testhelfern erstellte AST-Inventur bestaetigt fuer QUOTA-RESUME-DIFF genau drei produktive Emissionen in workflow._revalidate_waiting_diff: zwei Policygates mit stop_request in Zeile 2343 und 2359 sowie ein Nutzergate mit quota_resume_diff in Zeile 2376. Beide fachlich verschiedenen Formen sind getrennt deklariert, ihre Aufrufstellenzahlen zwei und eins stimmen mit meiner Zaehlung ueberein, die Zuordnung zum Fallorakel ist bijektiv, und beide Faelle erzeugen ihr Gate ueber echte Zustandsuebergaenge. Alle fuenf geforderten Falsifikationen sind rot und stammen aus den beabsichtigten produktionsgebundenen Invarianten: unclassified-triplet beim Entfernen der Zeile, case-mismatch beim Entfernen des Falls, reason-kind-mismatch bei gemeinsamer Umdefinition von HEAD-DRIFT sowie emission-count-mismatch zusammen mit unclassified-triplet bei Aenderung von Gategrund oder Gate-API einer einzelnen Policyemission. Ueber alle konkreten Identitaeten hinweg existiert kein weiteres undeklariertes Produktionstripel.

REVIEW_EVIDENCE: geprueft wurden die vollstaendige Tripelinventur fuer QUOTA-RESUME-DIFF mit einem eigenen AST-Walker, die Deckungsgleichheit aller direkt emittierten Produktionstripel mit der Source-Map, die Herkunftsbindung der Praefixe, die Ableitung von Gategrund und Gateart je Zeile samt Exceptionhandler- und Producerbindung, die Bijektion zu ausgefuehrten Faellen, die fuenf geforderten Falsifikationen jeweils auf die ausloesende Invariante, drei Kreuzkombinationen aus gemeinsam geaenderter Zeile und geaendertem Fall, die Regressionen von C-06, C-07, C-09 und C-10 einschliesslich dateibasierter Checkpoint- und Providerattemptevidenz sowie die Suche nach Zeilennummern- und Reihenfolgeabhaengigkeit | groesstes Restrisiko sind drei ungleich starke Bindungsmechanismen fuer das Paar aus Gategrund und Gateart: die deklarierte Emissionsstelle einer registrygespeisten Zeile laesst sich auf eine andere Funktion mit demselben Paar verschieben, ohne dass der Guard es meldet; die Bindung ueber forwarded_exception ist ein optionales Feld, dessen Entfernen still bleibt und danach eine gemeinsame Umdefinition wieder gruen werden liesse; und die Rueckfallmenge all_pairs ist nur exakt, solange die betreffende Emissionsfunktion genau ein Gatepaar verwendet | realistische Bruchbedingung ist eine Emissionsfunktion, die spaeter ein zweites Gate mit abweichendem Gategrund erhaelt, wodurch die Rueckfallmenge einer registrygespeisten oder praefixlosen Zeile mehrdeutig wird und eine falsche Deklaration unbemerkt passiert

PRE_MORTEM: In drei Monaten scheitert dieses Paket am wahrscheinlichsten daran, dass eine der vier Zeilen, deren Paar heute nur ueber die eindeutige Einzelmenge ihrer Emissionsfunktion gebunden ist, ihre Eindeutigkeit verliert. Erhaelt etwa workflow._halt_for_stop_request oder workflow._apply_pre_agent_policy_gates ein zweites Gate mit anderem Gategrund, akzeptiert der Guard fuer diese Regel plotzlich beide Paare, ohne dass eine Zusicherung bricht. Eine spaetere Umklassifizierung wird dann nicht mehr erkannt, die Matrix meldet weiterhin vollstaendige Abdeckung, und der Fehler faellt erst auf, wenn ein Betreiber ein Policygate erwartet und ein Nutzergate erhaelt.

SLICE_APPROVAL: 1 | YES

STATUS: DONE
