# Arbeitsplan: Stabilisierung des Orchestrators (manuelle Slices)

Datum: 29. August 2026

Grundlagen:
- `inbox/backlog/00-manuell-zustandsdivergenz-und-findinghoheit.md` (Analyse)
- `inbox/backlog/01-codex-gegenanalyse-orchestrator-instabilitaet-20260829.md`
  (Gegenanalyse, deren Präzisierungen hier eingearbeitet sind)

> **Dieser Plan wird NICHT über den Orchestrator abgewickelt.**
> Codex implementiert, Claude reviewt, der Mensch führt die Testsuite aus und
> committet. Kein `ORCHESTRATOR_MODE`, kein `TASK_SCOPE`, keine Slice-Records.

Arbeitsbranch: `feature/state-authority-consolidation`

## Stand

Fortgeschrieben am 30. August 2026. Der Zuschnitt der offenen Slices ist seit
der Erstfassung geändert; die Begründung steht jeweils im Slice.

| Slice | Inhalt | Stand |
|---|---|---|
| S0 | Arbeitsbaum und Lauf konsolidieren | erledigt |
| S1 | Dreiwege-Fehlerklassifikation | `d839bfa` |
| S2 | Übergangs- und Divergenzmatrix | `1c96382` |
| S3 | Finding-Reducer und Regression-Corpus | `898f14e` |
| S4a | Sortierung, zwei dringende Lücken, Schnittvorschlag | `ac2f3d6` |
| R1 | Runidentität und Profilbindung | `2981322` |
| R2 | Cursor, Work-Unit- und Slice-Status | `30b51cf` |
| R3 | Slice-Startgrenze und Scopefingerprint | `5334248` |
| RP | Appendkosten linearisieren | `ac030fe` |
| R4 | Side-effect-Ledger | `3f3cf77` |
| R5 | Gate-Transitionen | `0c1804b` |
| R6 | Failure- und Retry-Policy | `ebabb00` |
| R8 | Blob- und Contentauthority | `ae580a8` |
| R7 | Review-Contractfelder | `ce94012` |
| R9 | Restliche Historyprojektion | `f7e66ac` |
| Zwischenlauf | Nachzug zu R1 | `7339f7a` |
| S4b | Cutover: Mirror wird abgeleitet | `187b4b8` |
| S4c | Treiberoberfläche vollständig deklarieren | `da81763` |
| S5 | Crash-Injection und Harness | `103afe0` |
| S5b | Konvergenz des Basispräfix | `ce7a51c` |
| S6 | Vertragsabgleich | beauftragt |
| S7 | Abschluss und Überführung nach `master` | offen, neu |

Baselines unter WSL: `3daab29` 1163, nach S1 1181, nach S2 1195, nach S3 1219,
nach S4a 1227, nach R1 1236, nach R2 1245, nach R3 1257, nach RP 1264,
nach R4 1297, nach R5 1308, nach R6 1315, nach R8 1327, nach R7 1333, nach R9 1344,
nach dem Zwischenlauf 1348, nach S4b 1368, nach S4c 1393, nach S5 1402, nach S5b 1408.

Die Suitelaufzeit stieg bis R3 auf 492 s und fiel mit RP auf **279 s**:
162 s nach S3, 167 s nach S4a, 190 s nach R1, 411 s nach R2, 492 s nach R3,
279 s nach RP. Der teuerste Einzeltest fiel von 76 s auf 26 s. In den 279 s
stecken 21 s für RPs eigenen Messtest; die bestehenden Tests fielen also von
492 s auf rund 258 s, das sind −47,5 %.

Der vollständige R6-Lauf ist mit **1315 passed in 155,98 s** grün.

**R4 aufgelöst.** Der Vorabwert von 445 s war ein Zwischenstand. Der innere
Reviewzyklus führte die Laufzeit selbst als Blocker; nach der Korrektur liegt
die Suite bei **147 s** — trotz dichterem Ledger und +33 Tests. Ursache ist ein
zweiter, von RP unberührter Kostentreiber: Das Schema wurde bei jeder
Validierung neu aufgebaut. `load_schema()` gibt jetzt eine Tiefkopie eines
einmal geprüften Schemas aus, die Validierung nutzt intern das gecachte Objekt.

Damit ist die Laufzeit von 492 s nach R3 auf 147 s gefallen, −70 %. Der neue
Spitzenreiter ist `test_gate_source_map_rejects_orphans_and_per_emission_prefix_moves`
mit 36 s, also einem Viertel der Gesamtzeit; er hat auf beide Optimierungen
kaum reagiert und ist damit ein eigener, noch unbetrachteter Kostenblock.

## Verifikationsstand

Vor Planerstellung im Code geprüft und bestätigt:

| Befund | Fundstelle | Stand |
|---|---|---|
| `State-v3 remains authoritative until the explicit cutover` | `src/artifact_bridge.py`, Modulkopf | bestätigt |
| Zwölf wörtliche `differs from state-v3` | `src/artifact_migration.py` | bestätigt (Analyse sagte „rund zehn“) |
| `_recoverable_*`-Sonderfälle | `src/artifact_migration.py:651, 732, 764, 798` | **vier**, nicht drei |
| Records werden vor dem Mirror geschrieben | `src/orchestrator.py`, `checkpoint()` | bestätigt |

Der Widerspruch zwischen `CLAUDE.md` (Recordkette ist technische Quelle) und
dem Modulkopf von `artifact_bridge.py` (State-v3 autoritativ bis zum Cutover)
ist die kompakteste Beschreibung des Problems: Der Cutover wurde begonnen,
vertraglich vorausgesetzt und technisch nie abgeschlossen.

## Übernommene Korrekturen der Gegenanalyse

1. Runde 1 braucht **drei** Fehlerklassen, nicht zwei.
2. „Mirror behalten, generisch vergleichen“ ist **keine** gleichwertige
   Alternative — sie beseitigt das Doppelschreiben nicht.
3. Das Nicht-Ziel „keine Protokollversion anheben“ wird zurückgenommen. Die
   Cutover-Entscheidung darf nicht durch eine Vorfestlegung präjudiziert werden.
4. „Resume mit geändertem Code ist zwangsläufig inkonsistent“ war zu absolut.
   Zwangsläufig ist nur der Semantikwechsel; mit gebundener Reducer-Version
   wäre er beherrschbar. Für den heutigen Stand bleibt die manuelle Konsequenz
   richtig.
5. Zählregeln werden offengelegt: 66 von 245 Commits sind Fixes (26,9 %);
   `workflow.py` und `orchestrator.py` teilen sich 56 Berührungen; die neun
   Poison-Reports verteilen sich auf sechs Laufkontexte; direkt belegt sind
   zwei Finding-/Mirror- und drei Recovery-Fälle.

## Verhältnis zum laufenden Arbeitsplan

Der Plan `docs/internal/01-validierungsevidenz-...-arbeitsplan.md` überschneidet
sich in drei von fünf Slices:

| Dessen Slice | Stand | Konsequenz |
|---|---|---|
| 1 Kanonische Markdowngrenze | committet `6dbd03c` | bleibt, unabhängig |
| 2 Validierungsdiagnostik | gesichert als `dc29d82` | Entscheidung in S7; kollidiert mit S3 in fünf Dateien |
| 3 Deterministischer Rotzustand | offen | **geht in S1 dieses Plans auf** |
| 4 Poison-Recovery-Vertrag | offen | **geht in S2/S5 auf** |
| 5 Native Final-Requests | offen | Entscheidung in S6, nach dem Cutover |

Nach Abschluss dieses Plans wird der alte Arbeitsplan neu geschnitten. Seine
Slices 3 bis 5 werden nicht zusätzlich umgesetzt.

## Aus dem Backlog integriert

Drei Backlogaufgaben gehören fachlich in diesen Plan und werden nicht
zusätzlich als eigenes Vorhaben geführt. Grundlage ist die Analyse in
`00-backlog-neuordnung-stabilitaet.md`.

| Backlog | geht auf in | Begründung |
|---|---|---|
| `04` WorkflowDriver-Oberfläche | **S4c** | Neun Treiberkanten werden über `getattr(..., None)` aufgelöst, darunter die generische Recordsenke und zwei Findingkanten, die S3 gerade zur Reducerhoheit erklärt hat. |
| `11` Smoke- und Langlauf-Harness | **S5**, providerfreier Teil | S5s Abnahme ist fast wörtlich `11`s Szenarioklassen. Getrennt gebaut entstünde derselbe Harness zweimal. |
| `20` PLAN_ONLY-Schreibpflicht in den Rootverträgen | **S6** | Der Plan verlangt ohnehin, dass `AGENTS.md`, `CLAUDE.md` und `CODEX.md` dieselbe Autorität beschreiben. |

Nicht integriert, aber nach Planabschluss gemeinsam nachzumessen: `02`, `03`
und `19`. `03` ist durch S1 und die präzise Appendixdiagnose in
`audit_trail.py:501` weitgehend erledigt, `02` durch die semantische
Markdowngrenze aus `6dbd03c` geschrumpft, `19` war von Anfang an die nicht
blockierende Observation `C-01`.

## Freeze

Während S1 bis S7 findet an `artifact_migration.py`, `artifact_bridge.py`,
`workflow.py`, `orchestrator.py` und `audit_trail.py` keine Featurearbeit statt.
Ausschließlich dieser Plan ändert sie. Andernfalls veraltet die Inventur aus S2
noch während des Umbaus.

---

# S0 — Arbeitsbaum und Lauf konsolidieren

**Kein Code.** Ausgangszustand herstellen, bevor irgendetwas geändert wird.

**Lage.** Der Lauf `watch-20260829-102810.560490Z-c16fdae858d0` steht auf
`awaiting_user_decision` (Work-Unit 3, Slice 2 von 5, Finding `C-07` offen).
Er ist nicht abgestürzt. Der Branch trägt sechs Commits über `master`, davon
drei Symptom-Hotfixes; vierzehn Dateien sind uncommitted.

**Schritte.**
1. Den uncommitteten Slice-02-Stand auf einem Sicherungsbranch committen
   (`wip/validierungsevidenz-slice-02`). Nichts verwerfen — der Stand ist
   fachlich unabhängig von der Stabilisierung.
2. Die Aufgabe aus `inbox/` entfernen, damit kein Watcher sie erneut annimmt.
   Der Lauf wird nicht fortgesetzt; sein Zustand bleibt als Evidenz erhalten.
3. `feature/state-authority-consolidation` von `3daab29` abzweigen — also
   **mit** den drei Hotfixes.
4. Testsuite auf dem neuen Branch grün nachweisen. Das ist die Baseline.

**Warum nicht zurückrollen.** Die drei Hotfixes sind zwar die Symptomfixes, die
dieser Plan ersetzen soll, halten den Code aber lauffähig. Sie zu entfernen
würde bekannte Defekte wieder öffnen, ohne den Umbau zu erleichtern. Sie
entfallen automatisch, wenn S3 ihre Invariante zentral definiert.

**Abnahme.** Sauberer Arbeitsbaum, grüne Suite, kein Watcher aktiv, gesicherter
Slice-02-Stand.

---

# S1 — Dreiwege-Fehlerklassifikation

**Ziel.** Kein deterministischer Fehler erzeugt mehr Poison.

**Klassen.**
1. **Transient** — Quota, Netz, vorübergehender Prozessfehler. Begrenzt
   wiederholen, Verhalten unverändert.
2. **Resumierbarer Eingriffshalt** — Record/Mirror-, Schema-, Fingerprint- oder
   Recoverykonflikt eines begonnenen Laufs. Task und Watch-Identität erhalten,
   Queue mit typisiertem Diagnosecode anhalten.
3. **Terminale Eingabeablehnung** — etwa unzulässiger Branchname vor Laufbeginn.
   Einmalig als abgelehnt ablegen; kein dreifacher Versuch, kein scheinbar
   resumierbarer Lauf.

**Vorgehen.** Klassifikation an typisierten Fehlern festmachen, nicht an
Textsuche und nicht erst nach dem zweiten identischen Versuch. Insbesondere
prüfen, wo `checkpoint()` und `_persist_structured()` innere Ursachen in
`WorkflowExecutionError` einpacken und dadurch als transient erscheinen lassen.

**Abnahme.** Jeder der neun historischen Poison-Reports wird einer Klasse
zugeordnet und als providerfreier Test reproduziert. Die beiden
Vertragsfehler landen in Klasse 3, die Persistenzkonflikte in Klasse 2.
Quota- und Netzverhalten bleiben unverändert.

**Reviewfokus.** Verschluckte Ursachen; Klassifikation, die doch an Text hängt;
versehentliche Umdeutung transienter Fehler zu Halten.

---

# S2 — Übergangs- und Divergenzmatrix

**Ziel.** Eine belegte Matrix aller Stellen, an denen Recordkette und Mirror
verglichen werden. **Ergebnis ist ein Dokument, kein Code.**

**Je Übergang erfassen.**
- autoritativer Eingaberecord und erwarteter Folgerecord
- daraus abgeleitete State-/Cachefelder
- tatsächliche Schreibreihenfolge und mögliche Crashpunkte
- zulässige Wiederholung und Idempotenz
- heute vorhandener `_recoverable_*`-Sonderfall
- verwendete Semantik-/Protokollversion
- externe Side Effects vor dem nächsten dauerhaften Zustand

**Zusätzlich.** Welche heute im State geführten Fakten besitzen **keinen**
Record? Diese Liste entscheidet über S4.

**Abnahme.** Alle zwölf `differs from state-v3` und alle vier `_recoverable_*`
sind erfasst. Für jede Kante ist begründet, ob sie nach S4 entfällt, generisch
wird oder bewusst bleibt.

**Reviewfokus.** Vollständigkeit gegen den Code, nicht gegen die Meldungstexte;
übersehene Crashpunkte; Fakten ohne Record.

---

# S3 — Finding-Reducer und Regression-Corpus

**Ziel.** Ein deterministischer Reducer: Recordpräfix hinein, kanonisches
Finding-Ledger plus benannte Projektionen hinaus.

**Projektionen** bleiben ausdrücklich benannt und werden nirgends erneut
hergeleitet: Ledger, Open-Satz, Correction-Attribution, PLAN_ONLY-Import-
Snapshot, Request-Teilmenge, reviewerbeherrschte Statusübergänge.

**Corpus.** Die zwölf Finding-Fixes seit dem 23.08. werden als versioniertes
Regression-Corpus erfasst — je Eintrag Commit, Auslöser, Übergang und erwartete
Projektion. Ergänzend Sequenztests über kombinierte Übergänge: mehrere Slices,
denied Review, carried Observation, Correction, Resume.

**Abnahme.** Alle zwölf historischen Fälle sind abgedeckt, ohne sie einzeln
nachzubauen. Kein Modul außerhalb des Reducers leitet Findingzustand her.
`open_findings` ist entweder Projektion des Reducers oder entfällt.

**Reviewfokus.** Ob wirklich alle Herleitungen abgelöst sind oder nur die
sichtbaren; ob Sequenztests echte Kombinationen abdecken.

---

# S4a — Sortierung der Recordlücken und die zwei dringenden Fälle

**Zuschnitt geändert.** Der ursprüngliche Plan wollte alle rund 35
STOP-Einträge in einem Slice recorden. Das ergäbe einen Diff, der nicht mehr
unabhängig reviewbar ist — und niemand weiß vorher, wie viele Einträge
überhaupt einen Record brauchen.

**Ziel.** Drei Ergebnisse: die Sortierung aller STOP-Einträge in *braucht
Record*, *kann als nicht-normativ entfallen* und *bereits ableitbar, Matrix zu
streng*; die zwei unabhängig vom Cutover dringenden Lücken geschlossen; ein
Schnittvorschlag für den Rest.

**Die zwei dringenden Lücken.**

- `ContractResult.red_state_followup_slice` autorisiert einen Git-Commit im Red
  State und existiert nur im Mirror. Die Autorisierung gatet heute
  `git_service.py:717`.
- `ContractResult.evidence.dimensions/largest_residual_risk/break_condition`
  werden mit `" | "` verkettet, ohne das Trennzeichen im Inhalt auszuschließen.
  Der Schreibpfad in die Records ist `artifact_bridge.py:149`; die zweite
  Fundstelle in `audit_trail.py:1282` ist reine Projektion.

**Abnahme.** Siehe `00-s4a-auftrag-recordluecken.md`. Für Gruppe B ist der
heutige Leser zu benennen, nicht zu behaupten.

**Das Ergebnis steuert die Slicezahl.** Erst nach S4a steht fest, wie viele
R-Slices folgen. Jede frühere Zahl wäre geraten.

---

# R1 bis Rn — Recordlücken der Gruppe A schließen

**Neun Bündel.** S4as Schnittvorschlag in der Matrix legt sie fest:
Runidentität und Profilbindung; Cursor und Status; Slice-Startgrenze und
Scopefingerprint; Side-effect-Ledger; Gate-Transitionen; Failure- und
Retry-Policy; verbleibende Review-Contractfelder; Blob- und Contentauthority;
restliche Historyprojektion. Die Reihenfolge ist durch Abhängigkeiten
festgelegt, nicht frei wählbar.

**Die R-Serie ist vollständig abgeschlossen** (`2981322`, `30b51cf`,
`5334248`, `ac030fe`, `3f3cf77`, `0c1804b`, `ebabb00`, `ae580a8`, `ce94012`,
`f7e66ac`). Die Matrix führt keinen offenen Gruppe-A-Eintrag mehr.

**Die S4b-Vorbedingung ist mit R9 nachgewiesen**, nicht behauptet:
`test_multi_slice_correction_gate_halt_resume_projects_every_accepted_prefix`
prüft über eine 44-Record-Journey mit mehreren Slices, Korrektur, Gate, Halt
und Resume **jedes** Präfix. Jedes wird entweder deterministisch projiziert —
zweimal, auf Gleichheit — oder mit genau einem benannten Grund abgewiesen. Kein
Präfix fällt durchs Raster, und die Projektion deckt jedes Feld von
`WorkflowState` ab, nicht eine Teilmenge.

**Der Zwischenlauf ist erledigt.** R1 folgt jetzt beiden ab R2 geltenden
Regeln; die Providernamen-Ratsche fiel in vier Dateien um zusammen 18 je
Provider, `artifact_models.py` steht wieder auf dem Vor-R1-Wert `{14, 10}`.

**S4b ist erledigt** mit `187b4b8`. Der Cutover ist vollzogen:

| | vor S4b | nach S4b |
|---|---:|---:|
| `_recoverable_*`-Prädikate | 5 | **0** |
| `differs from state-v3` | 20 | **0** |
| `mismatch(...)` in der Migration | 44 | **0** |

`artifact_migration.py` schrumpfte um rund 1.740 Zeilen; der Slice entfernt
netto etwa 1.450 Zeilen. Der Modulkopf von `artifact_bridge.py` sagt jetzt das
Gegenteil seines Ausgangssatzes: „The record chain is authoritative; state-v3
documents are disposable projections produced by the reducer."

`state.json` ist auf einen reinen Run-Locator reduziert — ein AST-Nachweis
sichert zu, dass `resolve_resume_state` vom Stateobjekt ausschließlich `run_id`
liest. Wird der Cache gelöscht, findet der Lauf sich über seine Taskbindung aus
den Records wieder; ist die Zuordnung mehrdeutig, wird fail-closed abgewiesen.
Die Versionsentscheidung wurde getroffen und begründet verneint: Da R9 bereits
`RunProfilePayload.reducer_version` eingeführt hat, ändert der Cutover keine
Recordbedeutung; `structured-v2` und `schema_version = 2` bleiben.

**S4c ist erledigt** mit `da81763`. Alle zwölf `getattr`-Aufrufe in
`workflow.py` sind entfallen; die Inventur ist AST-basiert und sichert
Gleichheit in beide Richtungen zu. `OPTIONAL_WORKFLOW_DRIVER_CAPABILITIES` ist
leer — keine der neun Kanten war wirklich optional. Zwei Mutationsproben
belegen, dass der Guard Drift erkennt.

**S5 hat das Instrument geliefert, nicht das Ergebnis.** Der Harness ist
vollständig — versioniert, aus dem Ledger abgeleitet, 36 Injektionspunkte, null
echte Providerstarts — und hat einen realen Defekt gefunden:

```
blocked_acceptance_cases = ["baseline-crash-convergence", "record-backed-long-run"]
end_state = "stopped"
stop_condition_id = "STRUCTURED-BASELINE-NONRESUMABLE"
stop_scope = "run-identity-through-workflow-status-gate-ledger-prefix"
```

Genau eine Randrolle konvergiert nicht: `baseline_initialization` auf der
Effektklasse `ledger`. Kein Datenverlust — `physical_execution_count == 0`,
typisierter Diagnosecode, fail-closed. Alle übrigen Randrollen konvergieren
ohne Doppelausführung.

`_recoverable_*` blieb dabei bei null; der Befund wurde benannt statt geheilt.

**S5b ist beauftragt:** `00-s5b-auftrag-baselinepraefix-konvergenz.md`. Erst
danach ist S5 abgenommen.

**Mit R7 ist die S4a-Lücke geschlossen:** `red_state_followup_slice` wird jetzt
durchgereicht, ein Red-State-Commit ist wieder erzeugbar. Seit S4a war er
fail-closed ohne Pfad.

**Korrektur zu meiner R8-Abnahme:** Ich hatte den geforderten Skalierungstest
als fehlend gemeldet. Er war vorhanden, in der mit R8 neu angelegten Datei
`tests/test_content_authority.py`. Ursache war mein Vorgehen — neue Tests wurden
per `git diff` aufgezählt, das unversionierte Dateien nicht zeigt. Betroffen
waren auch `tests/test_side_effect_ledger.py` aus R4 und
`src/content_authority.py` aus R8. Keine Abnahmeentscheidung hing daran; ab R7
wird vor dem Review gestaged.

**Laufzeit nach R8: 194 s**, also +34 s gegen R6 bei +12 Tests. Der Anstieg
verteilt sich gleichmäßig über die Journey-Tests und passt zu drei zusätzlichen
Recordtypen je Lauf. Blobkosten sind als Ursache ausgeschlossen: gemessen rund
1 ms je MB referenzierten Blobinhalts.

**Aus R8 nach R7 mitgenommen:** Der im R8-Auftrag verlangte Skalierungstest über
wachsende Inhaltsgrößen fehlt. Die Sache selbst ist unkritisch — ich habe
nachgemessen, siehe oben —, aber ohne Guard bliebe unbemerkt, wenn Bytes später
wieder in die Recorddateien wandern.

**Bündel 8 läuft vor Bündel 7.** Der Schnittvorschlag sagt selbst, dass die
Validationbindung in Bündel 7 auf Bündel 8 angewiesen ist und Bündel 8 vor dem
Abschluss von Bündel 7 liegen muss. Die Nummerierung dort ist nicht die
Ausführungsreihenfolge. Die Bündelnummern bleiben, nur die Reihenfolge dreht
sich — sonst müsste Bündel 7 für die Validationbindung ein zweites Mal
geöffnet werden.

**Für Bündel 7 vorgemerkt:** der aus S4a mitgeführte Folgepunkt, das
Durchreichen von `red_state_followup_slice` aus `StepContract` über
`NativeReviewContext` in `ContractResult`. Solange es fehlt, kann der native
Transport keinen Red-State-Review erzeugen.

**Aus R5 mitzunehmen:** `--gate-actor` ist aus der öffentlichen CLI entfernt,
weil die Option ausschließlich das entfallene `decided_by` speiste. README und
Quickstart sind nachgezogen, ein Guard prüft die Quickstart-Kommandos gegen die
dokumentierten Optionen.

**Mit R6 korrigiert:** Bündel 6 nannte irrtümlich
`failure_classification.py`. Die typisierte Klassifikation aus S1 liegt in
`src/error_classification.py`; Matrix und Implementierung verwenden
ausschließlich dieses Modul.

**Zwischenlauf zu R1 — zwei Nachzüge, einzuordnen vor S4b.**

Position begründet: Beide Punkte ändern Recordformen. Nach dem Cutover wird der
Mirror aus den Records abgeleitet, und jede spätere Formänderung schlägt dann
durch die Projektion durch. Vorher ist es ein kleiner Slice, nachher ein
Eingriff in die abgeleitete Wahrheit.

Stand am 31. August 2026 geprüft, beide Punkte weiterhin offen:
`test_pre_r1_chain_without_run_records_remains_readable` ist der einzige
Vor-Slice-Test, der noch liest statt abzuweisen; die Ratschen-Baseline für
`artifact_models.py` steht unverändert bei `{"codex": 22, "claude": 18}`
gegenüber `{14, 10}` vor R1.

1. R1 hält seine Records optional und lässt Vor-R1-Ketten lesbar. Das war
   auftragsgemäß, ist aber seit der Entscheidung „keine Rückwärtskompatibilität“
   überflüssig: Eine Kette ohne `RunIdentityPayload` gehört fail-closed
   abgewiesen.
2. R1 hat die Providernamen-Ratsche um codex +20 / claude +20 angehoben, weil
   `RunProfilePayload` die Providernamen in Feldnamen trägt. Der Guard
   vergleicht gegen die Baselinedatei — sie anzuheben umgeht die Ratsche,
   statt sie zu erfüllen. Ein rollengeschlüsseltes `profiles: {role: {model,
   effort}}` löst das und macht Backlog `07` und `18` billiger, die diese
   Struktur sonst später wieder aufbrechen müssten.

Ab R2 gelten beide Regeln von vornherein.

**Mitgeführter Folgepunkt aus S4a.** `native_review_contract.py:826` und `:844`
setzen `red_state_followup_slice` hart auf `None`. Zusammen mit dem neuen
recordgebundenen Gate ist ein Red-State-Commit derzeit nicht mehr möglich —
fail-closed ohne Pfad. Das Durchreichen aus `StepContract` über
`NativeReviewContext` in `ContractResult` gehört in **Bündel 7** und darf dort
nicht vergessen werden.

**Ziel.** Jeder als *braucht Record* eingestufte Fakt besitzt einen Record mit
benanntem Typ, Feld und Schreiber. Danach ist ein leerer State aus einem
beliebigen akzeptierten Präfix projizierbar — genau die Vorbedingung von S4b.

**Bündelung nach fachlicher Nähe, nicht nach Anzahl.** S4a schlägt als
Kandidaten vor: Cursor und Work-Unit-Status; Slice-Startgrenze und
Scopefingerprint; Side-effect-Ledger; Gate-Transitionen; Failure- und
Retry-Policy; Protokoll- und Profilbindung; Review-Contractfelder; Blob- und
Contentauthority.

**Je Slice.** Ein unabhängig grüner Zwischenstand, keine Mirrorableitung, keine
Protokollversion. Der Mirror wird weiter geschrieben — der Cutover ist S4b.

**Abnahme je Slice.** Die betroffenen STOP-Einträge sind in der Matrix von
offen auf gedeckt fortgeschrieben, ihr Vollständigkeitstest bleibt grün, und
die neuen Records sind aus einem Replay des Präfixes belegbar.

**Reviewfokus.** Records, die einen Fakt nur umbenennen statt ihn zu binden;
Fakten, die danach weiterhin zusätzlich im Mirror fortgeschrieben werden.

---

# RP — Appendkosten der Recordkette linearisieren

**Erledigt** mit `ac030fe`. Ergebnis: Suite von 492 s auf 279 s, teuerster
Einzeltest von 76 s auf 26 s. Beide Vollscans je Append sind entfallen — der in
`append()` für Idempotenz und Revision, der in `put()` zur Nachprüfung. Der
Index ist mit `mtime_ns`, `ctime_ns` und `record_count` gestempelt und wird bei
Abweichung verworfen statt geglaubt. Die Nichtquadratik ist über den
Skalierungsexponenten gemessen, mit Positivkontrolle, die den Vollscan
absichtlich wiederherstellt.

**Befund, gemessen am 30. August 2026 auf `30b51cf`.** `ArtifactBridge.append()`
ruft bei jedem Append `self.store.load_chain()`. `ArtifactStore._load_chain()`
scannt das Recordverzeichnis, liest jede Datei und validiert sie vollständig —
ohne Cache. Das Anlegen von N Records kostet damit N Verzeichnisscans und rund
N²/2 Dateilesungen mit voller Schemavalidierung.

Produktiv liegt `.orchestrator/artifacts/<run-id>/records/` im Repository, also
auf `C:\` über 9p. Dort multipliziert sich der quadratische Aufwand mit dem
langsamen Dateisystem. In Tests liegt der Store unter `/tmp` auf ext4; dort ist
nur die Quadratik wirksam, nicht die Dateisystemlangsamkeit.

**Belegkette.** Die Suitelaufzeit sprang nach R2 von 190 s auf 411 s, obwohl R2
nur rund 22 s eigene Tests mitbringt. R2 schreibt Transitionrecords an jeder
Dispatch-, Gate-, Resume- und Completionkante; teurer wurde also der Bestand,
nicht der Zuwachs. Der `--durations=25`-Lauf zeigt 37 % der Gesamtzeit in fünf
Tests von `test_workflow_transition_matrix.py`, die vollständige Journeys
mehrfach durchspielen.

**Warum vor R4.** Bündel 4 ist das Side-effect-Ledger mit Records an jeder Git-,
Provider-, Datei- und Queuegrenze — die dichteste Recordart des Plans. Sie
trifft ungebremst auf die quadratische Kurve.

**Warum es S4b betrifft.** Die Records zur einzigen Wahrheit zu erklären,
während jeder Append die gesamte Kette neu liest und validiert, ist die falsche
Grundlage. S5s providerfreier Langlauf ist genau das Szenario, das daran
zerbricht.

**Richtung, nicht Vorgabe.** `CLAUDE.md` führt `head.json` bereits als
rekonstruierbaren Cache. Ein Index über Idempotenzschlüssel und die höchste
Revision je `(record_type, logical_id)` würde beide Gründe für den Vollscan
beseitigen. Die Lösung gehört in den Slice, nicht in diesen Plan.

**Weckpunkt Repositoryumzug.** Produktiv liegt die Recordkette im Repository
auf `C:\` über 9p. Gemessen am 30. August 2026: 300 kleine Dateien schreiben
kostet dort 0,77 s gegen 0,018 s auf ext4, dreimal vollständig lesen 2,22 s
gegen 0,029 s — Faktor 43 bis 76. Ein Umzug nach `~` behebt nur diesen
Konstantfaktor, nicht die Quadratik, und wirkt ausschließlich bei echten
Orchestratorläufen; Tests liegen über `tmp_path` bereits auf ext4. Der Umzug
ist deshalb bis nach S7 verschoben. **Aufgeschoben, mit Vorrang für den Betrieb.** Der
Betreiber steuert Codex und Claude derzeit per Fernzugriff vom Telefon aus. Ein
Umzug nach `~` verschiebt den 9p-Faktor auf die Windows-Seite und damit auf
genau den Weg, über den heute gearbeitet wird. Der Umzug wartet deshalb, bis
diese Betriebsart nicht mehr die Arbeitsgrundlage ist — nicht bloß bis zum
ersten Orchestratorlauf. Windows-Apps
erreichen das Repo danach über `\\wsl.localhost\Ubuntu\home\...`; vorher
sind Push nach `origin`, `core.autocrlf` und Case-Sensitivität zu klären.

**Abnahme.** Das Anlegen von N Records ist nachweislich nicht mehr quadratisch —
belegt durch eine Messung über wachsende Kettenlängen, nicht durch eine
Behauptung. Die Kette bleibt vollständig validiert; kein Append akzeptiert
etwas, das der heutige Vollscan abgewiesen hätte. Resume bleibt fail-closed.

---

# S4b — Cutover: Mirror wird abgeleitet

**Ziel.** Es gibt nur noch eine Wahrheit. `state.json` wird deterministisch aus
dem Recordpräfix erzeugt, nicht mehr unabhängig fortgeschrieben.

**Vorbedingung.** S4a und die gesamte R-Serie sind abgeschlossen. Ein leerer
State lässt sich aus einem beliebigen akzeptierten Präfix deterministisch neu
projizieren, und die Crashfenster um Commit, Providerstart und Queuebewegung
haben eine explizite Reconciliation-Regel.

**Vorgehen.** Der Mirror darf als Cache oder Betriebs-Snapshot bleiben, dann
aber gebunden an Record-Head, Reducer-Version und Projektdigest und jederzeit
rekonstruierbar. Der Modulkopf von `artifact_bridge.py` wird auf den
tatsächlichen Zustand gebracht.

**Versionsentscheidung.** Ändert sich die Bedeutung bestehender Records oder
des Reducers, wird eine neue explizite Semantik- beziehungsweise
Protokollversion eingeführt. Diese Entscheidung fällt hier und wird begründet —
sie ist nicht vorab ausgeschlossen.

**Abnahme.** Die Kanten aus S2 entfallen oder sind durch einen generischen
Cacheintegritätsabgleich ersetzt. Identische Szenarien erzeugen dieselben
kanonischen Zustände wie vor dem Umbau. Resume bleibt fail-closed.

**Reviewfokus.** Ob wirklich nur noch abgeleitet wird; ob ein Fakt ohne Record
stillschweigend verlorengeht; ob fail-closed irgendwo aufgeweicht wurde, um
eine Divergenz verschwinden zu lassen.

---

# S4c — Treiberoberfläche vollständig und fail-closed deklarieren

**Herkunft.** Backlogaufgabe `04`, in diesen Plan gezogen.

**Lage.** `src/workflow.py` löst neun Treiberfähigkeiten über
`getattr(..., None)` auf:

| Stelle | Kante |
|---|---|
| `:867` | `sink = getattr(self.driver, method_name, None)` — die generische Recordsenke |
| `:1225`, `:1650` | `recover_pending_native_codex` |
| `:3012` | `bind_work_unit` |
| `:3027` | `authoritative_native_findings` |
| `:3077` | `carry_forward_native_findings` |

**Warum das in diesen Plan gehört.** Nach S4b sind die Records die einzige
Wahrheit. Eine Pflichtsenke, die wegen eines Namensfehlers still ausfällt,
erzeugt eine Kette mit Loch — ohne Fehler und ohne Divergenzmeldung. Zwei der
Kanten sind genau die, die S3 gerade zur Reducerhoheit erklärt hat.

**Warum S5 das nicht fände.** Crash-Injection prüft, ob jeder Resume zum selben
kanonischen Zustand konvergiert. Zwei Läufe mit demselben still fehlenden Sink
konvergieren zum selben falschen Zustand. S5 wäre grün, das Loch bliebe.

**Warum nach S4b und nicht davor.** Der Cutover ändert, welche Senken
verpflichtend sind. Vorher deklariert man eine Oberfläche, die S4b danach
wieder verschiebt.

**Ziel.** Jede von der Engine benötigte Fähigkeit ist statisch in einem
typisierten Protokoll deklariert. Wirklich optionale Fähigkeiten sind
ausdrücklich als Capability mit Fallbacksemantik modelliert.
`getattr(..., None)` vertritt keine Persistenz- oder Sicherheitsentscheidung
mehr.

**Abnahme.** Eine statische Inventur beweist, dass jede produktiv aufgerufene
Fähigkeit deklariert ist. Eine unvollständige Fake-Treiberimplementierung fällt
deterministisch beim Aufbau oder ersten gebundenen Gebrauch. Mutationsproben
gegen die Namen der Pflichtsenken werden rot. Plan-, Slice-, Korrektur-,
Finalreview- und Resumepfade bleiben record- und zustandsidentisch.

**Reviewfokus.** Als optional deklarierte Kanten, die in Wahrheit verpflichtend
sind; No-op-Implementierungen, die einen fehlenden Sink verdecken.

---

# S5 — Crash-Injection, Semantikbindung und Betriebsharness

**Erweitert.** Der providerfreie Teil der Backlogaufgabe `11` geht hier auf. S5
erzeugt den Harness, statt seine Szenarien einmalig und wegwerfbar zu bauen.

**Ziel.** Nachweis, dass kein normaler Crashpunkt mehr einen
übergangsspezifischen Sonderfall braucht — und dass dieser Nachweis
wiederholbar ist.

**Vorgehen.** Vor und nach jedem dauerhaften Schreib- und Side-Effect-Rand
deterministisch unterbrechen und den Resume prüfen. Zusätzlich: Ein Lauf kann
nur mit seiner gebundenen Reducer-Semantik oder über eine explizit getestete
Migration fortgesetzt werden.

**Harness.** Die Szenarien werden versioniert und wiederholbar abgelegt, mit
einem gebundenen Ergebnisartefakt je Lauf: Repositorycommit, Szenarioversion,
Recordheads, Aufruf- und Commitzählung, Endzustand. Der Standardmodus ist
vollständig providerfrei; Instrumentierung beweist null Providerstarts.

**Szenarioklassen.** Kleine vollständige
PLAN_ONLY-IMPLEMENT-Finalreview-Journey; mehrere kohärente Slices mit Korrektur
und geschlossenem Finding; Neustart an Provider-, Validation-, Commit-,
Handoff- und Queuefinalisierungsgrenzen; Record-ahead-Wiederaufnahme ohne
zweiten Providerstart oder Commit; typisierte Fortsetzung nach Quota-, Netz-
und Prozessfehler.

**Abnahme.** Jeder Resume konvergiert zum selben kanonischen Zustand. Kein
`_recoverable_*`-Sonderfall ist mehr nötig. Ein Lauf mit fremder
Reducer-Version wird fail-closed abgewiesen. Ein providerfreier Langlauf
durchläuft PLAN_ONLY, Handoff, denied Review, Correction, carried Observation,
Finalreview und Resume ohne manuellen Stateeingriff — reproduzierbar aus dem
Harness, nicht als Einzelbeobachtung.

**Startbarkeit geprüft am 31. August 2026.** Ein echter Canary lässt sich aus
der Claude-CLI-Sitzung heraus starten; beide Provider-CLIs sind dort verfügbar
(`codex` 0.150.1, `claude` 2.1.251). Die Betriebsart des Betreibers —
Fernsteuerung vom Telefon — verhindert einen realen Lauf also nicht. Aus den
Laufdaten vom 29. August: ein PLAN_ONLY-Canary liegt bei rund zehn Minuten und
ein bis zwei Euro. Empfohlener Ort: ein Wegwerfbranch, nicht der Arbeitsbranch.

**Nicht hier.** Der reale Canary aus `11` bleibt im Backlog. Er erteilt keine
Freigabe und läuft nie automatisch durch Watcher, Versionsfund oder Testmatrix.

**Reviewfokus.** Ob die Injektionspunkte die realen Ränder treffen; ob ein
Sonderfall nur umbenannt statt beseitigt wurde; ob der Harness echte
Kombinationen erzeugt oder längere Einzelfälle aneinanderreiht.

---

# S6 — Vertragsabgleich

**Herkunft.** Abschlusskriterium dieses Plans, zusammengelegt mit
Backlogaufgabe `20`.

**Lage.** Der Ausgangsbefund dieses Plans war eine Divergenz zwischen
dokumentiertem und gelebtem Vertrag: `CLAUDE.md` nannte die Recordkette
autoritativ, der Modulkopf von `artifact_bridge.py` den State-v3-Mirror. Nach
S4b ist die Frage entschieden — die Dokumente müssen das dann auch sagen.

**Umfang.**

- `AGENTS.md`, `CLAUDE.md`, `CODEX.md` und die Modulköpfe beschreiben dieselbe
  Autorität, dieselbe Reducer-Semantik und dieselbe Resumeregel.
- Die PLAN_ONLY-Schreibpflicht aus `20`: Repositorywirkung und strukturierter
  Ergebnisdatensatz werden in allen drei Rootverträgen getrennt benannt. Ein
  PLAN_ONLY-Lauf erstellt oder aktualisiert die Plandatei; der
  `SLICE_PLAN`-Datensatz ist die Quittung und ersetzt sie nicht.
- Prüfen, welche Rootdateien der Orchestrator selbst in `assignment` einbettet
  und welche der Provider eigenständig lädt; die tatsächliche Transportgrenze
  dokumentieren statt sie anzunehmen.
- Offene Entscheidung mitführen: Slice 5 des abgelösten Arbeitsplans
  (preflightgebundene, kollisionsfreie native Final-Requests) wird hier
  bewertet — umsetzen, neu schneiden oder verwerfen.

**Zusätzlich vorgemerkt: Memoisierung in `test_workflow_transition_matrix.py`.**
Gemessen am 1. September 2026 per `cProfile`: Der Test
`test_gate_source_map_rejects_orphans_and_per_emission_prefix_moves` kostet 39 s
und damit 14 % der Suite. Die Kosten liegen vollständig im Test, keine
`src/`-Funktion taucht im Profil auf. Ursache ist wiederholte
AST-Traversierung ohne Cache — `_final_review_preflight_error_codes` wird
16.071-mal aufgerufen, `_assigned_rule_ids` 98.436-mal, eine
Generator-Expression 257.136-mal, bei einer Handvoll verschiedener Eingaben.

Behebung: `functools.lru_cache` auf die Hilfsfunktionen oder das Parsen aus den
Schleifen ziehen. Reine Teständerung, Zusicherungen unverändert, erwartet rund
30 der 39 Sekunden. Relevant, weil der Orchestrator die volle Suite als
Validierung fährt — je Slice und je Korrekturrunde. Laufzeit vorher und nachher
festhalten.

**Abnahme.** Der Synchronitätsguard ist grün. Ein providerfreier Test prüft den
real transportierten `canonical_request`. Keine Vertragsaussage beschreibt
einen Zustand, den der Code nicht hat.

**Reviewfokus.** Vertragstext, der eine Absicht statt des Ist-Zustands
beschreibt; Formulierungen aus der Zeit vor dem Cutover, die stillschweigend
weiterleben.

---

# S7 — Abschluss und Überführung nach `master`

**Kein Produktcode.** Grundlage ist die Bestandsaufnahme in
`00-merge-bestandsaufnahme.md`.

**Schritte.**

1. Gesamtreview über `master..feature/state-authority-consolidation`.
2. Entscheidung zu `dc29d82`, dem gesicherten Slice 2 des abgelösten
   Arbeitsplans: gegen die Record- und Reducerwelt neu umsetzen oder verwerfen.
   Ein Rebase des WIP ist keine Option — er kollidiert mit S3 in fünf Dateien.
3. Die sieben Dokumente des abgebrochenen Orchestratorlaufs und der drei
   Hotfixes nach `docs/internal/archive/` überführen. Die S2-Matrix bleibt
   aktiv.
4. **Im selben Commit** die beiden testgebundenen Pfade mitziehen:
   `tests/test_semantic_markdown.py:146` und
   `tests/test_stabilisierung_s2_transition_matrix.py:11` lesen reale Bytes aus
   `docs/internal/`.
5. `docs/internal/README.md` um aktive Dokumente und neue Archivordner
   fortschreiben.
6. Die zu diesem Vorhaben gehörenden Backlogdokumente nach
   `docs/internal/archive/` überführen.
7. Vollständige Suite unter WSL grün, dann Merge nach `master`.
8. Die 26 erledigten `feature/*`-Branches löschen. Über den Push nach `origin`
   getrennt entscheiden — `master` liegt 17 Commits vor `origin/master`.

**Abnahme.** `master` enthält den vollständigen Stand, die Suite ist grün, und
im Wurzelbereich von `docs/internal` liegt kein Dokument mehr, das dorthin
nicht gehört.

# Nach dem Merge — zwei manuelle Folgevorhaben

Nicht Teil dieses Plans, aber unmittelbar danach und in derselben Arbeitsweise.
Grundlage ist die Backloganalyse in `00-backlog-neuordnung-stabilitaet.md`,
Bündel A.

## Reihenfolge: `08` vor `12`

**`08` — Qualitativer Slice-Schnitt ohne starres Dateilimit.** Zuerst, weil
`12` sich selbst verbietet, nach Dateizahl geschnitten zu werden. Ein
Strukturschnitt, der an einer Zehnergrenze hängt, wird künstlich zerlegt —
genau das, was `08` beseitigt.

Gemessen an den Slices dieses Plans, gezählt in produktiven Pfaden nach
`orchestrator.toml` (`src/**/*.py`, `schemas/**/*.json`, `run_task`, `*.toml`;
Tests und Dokumentation zählen nicht):

| Slice | produktive Dateien |
|---|---:|
| S3 | 16 |
| R4 | 13 |
| R5 | 8 |
| R2, R6 | 6 |
| R1, R3 | 4 |
| RP | 2 |

Zwei von acht Slices hätten `max_productive_files = 10` gerissen. Das Limit
blockiert also nicht jeden Slice, aber verlässlich die großen.

**`12` — Orchestrator-Kern strukturell zerlegen.** Danach. Seine
Aktivierungsbedingung, der Abschluss von `04`, ist mit S4c erfüllt.

## Warum beide manuell

`12` schreibt `orchestrator.py`, `workflow.py`, `artifact_migration.py` und
`audit_trail.py` um — den Kern des Orchestrators. Ihn durch den Orchestrator
laufen zu lassen hieße, dass er sich während des Laufs selbst umbaut.

## Warum erst nach dem Merge

`12` ist ein großer Umbau. Auf einem unfusionierten Branch mit rund zwanzig
Commits vergrößert er den Explosionsradius um die gesamte Stabilisierung. Nach
S7 steht auf `master` ein sinnvoller Meilenstein — Recordautorität vollständig
— und der Umbau beginnt auf sauberem Grund. Der Push nach `origin` gehört
dazu; `master` liegt derzeit 17 Commits zurück, mit dem Branch über 25.

## Was sonst noch zwischen S7 und dem Orchestratorbetrieb liegt

- **Der R1-Zwischenlauf** mit seinen zwei Nachzügen: Kompatibilitätsklauseln
  auf fail-closed drehen, `RunProfilePayload` rollengeschlüsselt machen.
- **Der Repositoryumzug**, aufgeschoben wegen der Fernsteuerung vom Telefon;
  siehe Slice RP.

Diese drei — `08`, der Zwischenlauf und der Umzug — sind die Bedingungen dafür,
dass der Orchestrator Arbeit in der Größe wieder selbst fahren kann, die hier
manuell gefahren wurde.

## Testumgebung (verbindlich)

Die Suite läuft **ausschließlich unter WSL** mit `python3 -m pytest tests/ -v`,
also in derselben Umgebung, in der der Orchestrator produktiv arbeitet.

Belegt am 29.08.2026 auf `3daab29`:

| Umgebung | Ergebnis |
|---|---|
| WSL, `python3` | **1163 passed**, 0 skipped, 0 failed |
| Windows-Python 3.14 | 1152 passed, 8 skipped, 3 failed |

Die drei Windows-Fehler sind Umgebungsartefakte — zwei Symlink-Tests, die unter
Windows erhöhte Rechte bräuchten, und ein `WinError 10106` beim asyncio-Import
in einem Subprozess. Unter Windows gemessene Ergebnisse sind als Abnahme
unzulässig: Sie würden echte Regressionen im Rauschen dieser drei Fehler
verstecken und Streit über Artefakte statt über Code erzeugen.

**Baseline für alle folgenden Slices: `3daab29`, 1163 passed unter WSL.**

## Arbeitsmodell je Slice

1. Codex implementiert vollständig, ohne Orchestrator.
2. Claude reviewt den Diff gegen den unmittelbaren Vorgängercommit, read-only,
   mit selbst erhobenem Git-Stand.
3. Offene Punkte gehen als benannte Findings zurück, bis geschlossen.
4. Der Mensch führt die vollständige Testsuite unter WSL aus und committet.
5. Erst danach beginnt der nächste Slice.

## Abschlusskriterien

- Jeder persistierte Fakt hat genau eine autoritative Recorddarstellung oder
  ist ausdrücklich als abgeleiteter Cache benannt.
- Kein normaler Crashpunkt braucht einen `_recoverable_*`-Sonderfall.
- Kein deterministischer Fehler erhöht den transienten Retryzähler.
- Das Finding-Corpus deckt alle historischen Defekte und kombinierte Sequenzen ab.
- `CLAUDE.md`, `AGENTS.md`, `CODEX.md` und die Modulköpfe beschreiben dieselbe
  Autorität.
- Ein providerfreier Langlauf und danach ein kleiner echter Canary laufen ohne
  manuellen Stateeingriff durch.
- Keine von der Workflowengine benötigte Treiberfähigkeit wird dynamisch
  aufgelöst; ein fehlender Pflichtsink fällt vor dem betroffenen Übergang auf.
- Der providerfreie Langlaufnachweis ist ein versionierter, wiederholbarer
  Harness und keine einmalige Beobachtung.
- Der Branch ist nach `master` überführt und die zugehörigen Dokumente sind
  archiviert.

## Stopbedingungen

- Anhalten, wenn eine persistierte Recordkette bestehender Läufe umgedeutet
  werden müsste, ohne dass die Versionsentscheidung aus S4 das ausdrücklich deckt.
- Anhalten, wenn S2 einen Fakt findet, der auf keiner Seite normativ definiert
  ist — dann gehört er zuerst definiert.
- Anhalten, wenn ein Slice nur durch Abschwächen einer Resumeprüfung gelingt.
