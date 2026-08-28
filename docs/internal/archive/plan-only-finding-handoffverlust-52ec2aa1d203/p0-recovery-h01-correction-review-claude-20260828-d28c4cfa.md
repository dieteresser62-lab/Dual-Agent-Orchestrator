# Claude-Konvergenzreview: P0-Recovery nach H-01-Korrektur

**Reviewer:** Claude (manuell, adversarial, read-only) · **Datum:** 28. August 2026 ·
**Snapshot:** Arbeitsbaumdiff `d28c4cfaf0e6a3af2c19e187496c1dffb2d0cb51`
**Vorrunde:** `docs/internal/p0-correction-ledger-record-ahead-recovery-review-claude-20260828-b2e46eaf.md`

## 1. Ergebnis vorab

**Freigabe.** Kein offener Blocker.

`H-01` ist geschlossen, und zwar nicht nur im Test, sondern nachgerechnet am
echten Poison-Lauf. Die Korrektur greift an der richtigen Stelle: Statt den
Idempotenzschlüssel zu flicken, rekonstruiert die Recovery den
**requestzeitlichen** Findingstand aus dem Kettenpräfix unmittelbar vor dem
ursprünglichen `ProviderAttemptPayload(phase="started")`. Damit stimmt die
Zählbasis wieder mit der des Originalaufrufs überein, und der bestehende
indexbasierte Schlüssel wird von selbst wieder idempotent.

`H-02` ist geschlossen, `H-03` dokumentativ geschlossen, `H-04` bleibt
auftragsgemäß Restrisiko. Einen neuen ausführbaren Defekt habe ich nicht
gefunden.

| Befund | Disposition |
|---|---|
| `H-01` doppelte Codex-Disposition bei Recovery | **CLOSED** |
| `H-02` Evidenzassets umgehen ihre Digestprüfung | **CLOSED** |
| `H-03` Legacy-Prüftiefe im Dokument beschönigt | **CLOSED** |
| `H-04` Reviewer-Asymmetrie der Bundle-Persistenz | **RESIDUAL_RISK** |

## 2. Selbst erhobener Snapshot und Reviewgrenze

| Merkmal | Gemessen | Erwartet | |
|---|---|---|---|
| Branch | `feature/plan-only-finding-carry-forward` | identisch | ✓ |
| `HEAD` | `dd6ad0c3e13a9ceb3dbc3c73ce89f7bc1c4aa10a` | identisch | ✓ |
| Merge-Base zu `master` | `1acf1fd3abd153f3a3ccfa66e91ac1defa95f22a` | identisch | ✓ |
| Navigationshash `workflow.py` + `test_workflow.py` | `f317502f8c234ea3e8c84fe56e55c907ce7e7a79` | identisch | ✓ |
| Navigationshash `orchestrator.py` + `test_orchestrator_runtime.py` | `d6b9321fc33f03c1df35fb7fe04d0996ee068e46` | identisch | ✓ |
| Arbeitsbaum | 12 geändert, 3 ungetrackt | Slice-02 + Hotfix + Reviews | ✓ |

Der `workflow.py`-Navigationshash ist gegenüber meiner Vorrunde unverändert —
die H-01-Korrektur liegt vollständig in `src/orchestrator.py` und
`tests/test_orchestrator_runtime.py`. Eine isolierte Commitgrenze existiert
nicht und wird von mir nicht behauptet.

**Reviewgrenze.** Kein `run_task`, kein Watcher, kein Bootstrap, kein Provider-,
Canary- oder Netzaufruf. Nichts committet, gestaged oder verschoben. State,
Checkpoints, Recordkette, Inbox und Outbox wurden ausschließlich gelesen; die
Incident-Evidenz ist unverändert (37 Records, `state.json` und beide
Poison-Dateien mit unveränderten Zeitstempeln vom 28.08.). Mein erstes Review
ist unangetastet. Die einzige Schreiboperation ist dieses Dokument.

**Eigene Ausführung vs. Fremdangaben.** Selbst gefahren habe ich
`tests/test_workflow.py` und `tests/test_orchestrator_runtime.py` →
**`147 passed in 21.63s`**, dazu vier einzelne Knoten und mehrere read-only
In-Memory-Proben gegen die echte Recordkette. Die Codex-Angaben
(`185 passed in 33.16s` über drei Dateien, `1127 passed in 153.86s` vollständig)
sind Fremdangaben; ich habe die vollständige Suite nicht ausgeführt und
beanspruche für nichts davon eine Orchestrator-Attestierung.

## 3. Eigene Gegenproben

| # | Gegenprobe | Ergebnis |
|---|---|---|
| Q1 | Branch, HEAD, Merge-Base, Status, beide Navigationshashes | wie erwartet |
| Q2 | AgentResult für `agent-3-codex_correction-2` in der echten Kette | genau einer |
| Q3 | passende vorhergehende `started`-Attempts derselben Operation | genau einer, 12:24:09, `binding_fingerprint=0bd309ba…` |
| Q4 | **Requestzeitliches Replay des Präfixes vor diesem Attempt** | C-01/C-02 je 1 Response, **C-03 mit 0 Responses**, C-04 mit 0 |
| Q5 | heutiges Replay der vollständigen Kette | C-03 mit 1 Response |
| Q6 | Simulation der Response-Schleife am echten Fall | `prior_count=1`, Resultat mit 1 Response → **0 neue Records** |
| Q7 | Simulation einer legitimen zweiten Korrekturrunde | requestzeitlich 1, Resultat 2, `prior_count=1` → **1 neuer Record** |
| Q8 | Kontrollfluss `original_attempt_record` | wird für Bundle- und Legacy-Pfad gesetzt, fail-closed ohne Attempt |
| Q9 | fehlende angebotene ID im requestzeitlichen Replay | `native agent request-time finding subset is incomplete` |
| Q10 | nicht replaybares Präfix | `native agent request-time finding replay failed: …` |
| Q11 | aufgelöster Assettyp im Bundle-Loader | `NativeCodexEvidenceAsset` über `get_type_hints`/`get_args` |
| Q12 | Manipulation nur `content`, gleiche Länge | abgewiesen: „evidence asset digest differs from content" |
| Q13 | Manipulation `byte_count` bzw. `sha256` | je abgewiesen |
| Q14 | Exceptiontyp `NativeCodexRequestError` | Subklasse von `ValueError` → wird im Loader als `WorkflowExecutionError` fail-closed gewrappt |
| Q15 | Zentraltest `…_completes_finding_responses` | deckt beide Crashfenster ab, zweite Recovery, genau ein `responded`-Record |
| Q16 | Slice-Scope des Laufs gegen die Hotfixpfade | `src/workflow.py`, `tests/test_workflow.py` und die neuen Dokumente liegen außerhalb |
| Q17 | `_validate_change_boundary` für `SLICE` | prüft gegen `current_slice.scope_paths`, nicht gegen `task_scope_patterns` |
| Q18 | vier fokussierte Knoten und beide Testdateien | `4 passed`, `147 passed in 21.63s` |
| Q19 | Incident-Evidenz und erstes Review | unverändert |

## 4. H-01 — geschlossen

### Die Korrektur

`recover_pending_native_codex` bestimmt jetzt in beiden Pfaden — Bundle wie
Legacy — ein `original_attempt_record`: den letzten `started`-Attempt desselben
Providers, derselben Work-Unit und derselben Operation **vor** dem gebundenen
AgentResult. Liegt keiner vor, bricht die Recovery mit „native agent recovery
has no durable original request binding" ab.

Existiert dieses Record, wird die Kette bis genau vor diesen Attempt
zurückgespult und das Ledger daraus neu projiziert. Aus diesem
requestzeitlichen Replay werden **ausschließlich die im gebundenen Request
angebotenen IDs** übernommen; fehlt eine davon, bricht die Recovery mit „native
agent request-time finding subset is incomplete" ab, und ein nicht replaybares
Präfix endet in „native agent request-time finding replay failed".

Das ist die sachlich richtige Ebene: Der Fehler war nie der Schlüssel, sondern
die verschobene Zählbasis. Jetzt sieht die Recovery genau den Findingstand, den
der Provider damals gesehen hat.

### Nachrechnung am echten Poison-Lauf

Kein Testkonstrukt, sondern die reale Kette (Q2–Q6):

```
gewählter Attempt:            12:24:09, binding_fp 0bd309ba…
requestzeitliches Ledger:     C-01 resp=1 | C-02 resp=1 | C-03 resp=0 | C-04 resp=0
heutiges Ledger:              C-01 resp=1 | C-02 resp=1 | C-03 resp=1 | C-04 resp=0
Simulation: prior_count=1, Responses im Resultat=1, neu anzuhängen=0
=> zusätzlicher responded-Record: NEIN (idempotent)
```

Damit ist genau das ausgeschlossen, was ich in der Vorrunde als Blocker
belegt hatte: C-03 behält nach dem Wiedereinwerfen exakt seine eine Disposition
`finding-response:C-03:1`.

Bemerkenswert am Zusammenspiel: Die Recovery liefert ein Resultat mit *einer*
Response (weil `prior` requestzeitlich leer war), während
`persist_native_codex_contract` weiterhin mit `invocation.previous_findings`
(heute: eine Response) zählt. `responses[1:]` ist damit leer — nichts wird
angehängt. Im ersten Crashfenster ist es spiegelbildlich: heute null Responses,
Resultat eine → genau ein Record mit Index 1. Beide Fenster laufen über
dieselbe Logik, ohne Sonderfall.

### Keine globale Deduplizierung

Die Lösung macht nur *dieselbe requestgebundene* Recovery idempotent. Eine
legitime spätere Korrekturrunde hat ihren eigenen Attempt; ihr requestzeitliches
Präfix enthält den ersten `responded`-Record, das Resultat trägt dann zwei
Responses und `prior_count` ist eins — es entsteht korrekt ein neuer Record mit
Index 2 (Q7). Es wird also nichts über Runden hinweg weggedrückt.

### Regressionsabdeckung

`tests/test_orchestrator_runtime.py::test_native_codex_record_ahead_recovery_completes_finding_responses`
deckt jetzt beide Fenster ab. Zuerst der Absturz zwischen AgentResult und
`responded`-Record: Die Recovery ergänzt exakt eine Response und exakt einen
Record. Danach wird das replayte Finding — inklusive Response — in einen
resumierten Request gebaut, wobei der Test ausdrücklich behauptet, dass die
Request-ID identisch bleibt (die Kontextprojektion trägt keine Responses).
Die zweite Recovery liefert `recovered_after_response.result.findings ==
(replayed_finding,)`, also **keine** zweite Response, und `durable_responses`
ist ein Ein-Tupel. Genau die Situation des realen Laufs.

Der Auftrag verlangt zusätzlich eine dritte Ausführung oder eine Analyse bis
dorthin. Ich habe den Pfad analysiert: Ab der zweiten Recovery ist der Zustand
ein Fixpunkt — requestzeitliches Replay und heutiges Ledger ändern sich nicht
mehr, weil kein Record hinzukommt; jede weitere Recovery rechnet dieselben
Werte und hängt wieder nichts an. Meine Simulation Q6 ist genau diese
n-te Ausführung, gerechnet auf der echten Kette.

## 5. H-02 — geschlossen

`_load_native_agent_request_bundle` erzeugt keine `SimpleNamespace`-Assets mehr.
Der Assettyp wird über `get_type_hints(type(rebuilt))["evidence_assets"]` und
`get_args(...)[0]` aufgelöst — aufgelöst zu `NativeCodexEvidenceAsset` (Q11) —
und mit Schlüsselwortargumenten konstruiert, sodass dessen `__post_init__`
läuft. Diese prüft Pfadnamensraum, SHA-256-Form, `byte_count` gegen die
tatsächliche Bytelänge und den Digest gegen den Inhalt.

Falsifiziert (Q12, Q13): Eine Manipulation, die **nur** `content` ändert und
dabei die Länge beibehält, wird mit „evidence asset digest differs from
content" abgewiesen; eine Längenänderung fällt schon am `byte_count` auf; eine
Digestmanipulation ebenfalls. Da `NativeCodexRequestError` von `ValueError`
erbt (Q14), fängt der Loader sie und bricht als
`WorkflowExecutionError: native agent request recovery artifact no longer
validates: …` fail-closed ab — der Fehler verschwindet also nicht, sondern
erscheint als Recovery-Abbruch.

## 6. H-03 — geschlossen

Das Hotfixdokument beschreibt die Prüftiefe jetzt korrekt und ohne
Beschönigung: Der Legacy-Fallback habe „absichtlich eine geringere Prüftiefe
als der neue Bundle-Normalweg", ein nie gespeichertes Requestdokument lasse
sich nicht nachträglich rekonstruieren, die bereits beim Originallauf
validierte Antwort werde über die append-only Kombination aus Request-ID,
Response-Digest, AgentResult-Payload und ursprünglichem Attempt-Binding
autorisiert, und er führe „keine erneute Writer-Schema-Validierung gegen ein
Originalrequestdokument" aus. Ergänzt um den Satz, dass neue Läufe diesen
reduzierten Pfad nicht verwenden dürfen. Das deckt sich mit dem Code
(`validate_against_bundle = False` genau in diesem Zweig) und ist die Aussage,
die in meiner Vorrunde gefehlt hat.

## 7. H-04 — Restrisiko, kein Blocker

Die Bundle-Persistenz ist providerneutral benannt, aber weiterhin nur im
Codex-Pfad verdrahtet. Für die sichere Wiederaufnahme **dieses** Laufs ist das
ohne Belang: Der offene Schritt ist `codex_correction`, nicht ein Reviewaufruf;
Reviewer schreiben den Arbeitsbaum nicht, weshalb der Fingerprint zwischen
Requestbau und Absturz eines Reviews nicht durch den Reviewer selbst driftet.
Ich habe keinen Pfad gefunden, auf dem die Asymmetrie diesen Poison-Lauf
gefährdet. Die Dokumentation stellt sie korrekt als eigenen, nicht realisierten
Änderungsscope dar und behauptet keine Behebung. Auftragsgemäß führe ich sie
ausschließlich als Restrisiko und eröffne keine neue Observation.

## 8. Unveränderte Sicherheitsgrenzen

**Kein zweiter Providerstart.** Die Recovery gibt ein `NativeAgentCodexOutput`
zurück, ohne den Adapter zu berühren; der Regressionstest
`…_reuses_raw_json_without_provider` hält das fest und ist in meinem Lauf grün.

**Kein zweites AgentResult bei Drift.** Der Record wird nur geschrieben, wenn
`candidates` leer ist; existiert er, wird er gegen ein exakt rekonstruiertes
`expected_payload` sowie gegen `request_id` und `response_sha256` verglichen.
Die frühere Fingerprintgleichheit ist zu Recht entfallen — der AgentResult-Record
trägt `e0e6ec33…`, also den Baum nach Codex' Schreiben.

**Vollständiges Ledger.** Der Merge aus der Vorrunde ist unverändert
(`workflow.py`-Diff bitgleich) und in meiner Vorrunde mit acht Negativproben
falsifiziert. C-01 bis C-04 bleiben erhalten, obwohl nur C-03 angeboten war.

**Scope-Gate vor Claude.** Der persistierte Slice-Scope enthält weder
`src/workflow.py` noch `tests/test_workflow.py` noch die neuen Dokumente, und
`_validate_change_boundary` prüft für eine Slice-Work-Unit exakt gegen
`current_slice.scope_paths` — nicht gegen die weiteren
`task_scope_patterns` (Q16, Q17). Ein fingerprintgebundenes
`UNEXPECTED-PATH`-Nutzergate ist damit vor dem nächsten Claude-Aufruf zu
erwarten und ist die zulässige, sichere Pause.

**State und Kette unverändert.** 37 Records, unveränderte Zeitstempel,
Poison-Artefakte intakt; nichts manuell repariert oder migriert.

## 9. Größtes Restrisiko und realistische Bruchbedingung

Größtes Restrisiko ist die neue **Abhängigkeit der Recovery vom Kettenpräfix**.
Die Rekonstruktion des requestzeitlichen Ledgers ist nur so gut wie die
Auffindbarkeit des richtigen `started`-Attempts und die Replaybarkeit des
Präfixes. Beides ist heute fail-closed abgesichert und auf dem realen Lauf
eindeutig, aber es ist eine zusätzliche Stelle, an der eine künftige Änderung
der Recordreihenfolge — etwa ein Attempt, der nach dem AgentResult geschrieben
wird — die Recovery still auf das falsche Präfix führen könnte. Die Ordnung
`started` → AgentResult → `succeeded` ist derzeit nur durch die Aufrufreihenfolge
garantiert, nicht durch eine Invariante der Kette selbst.

Nachgeordnet: der Legacy-Pfad vertraut weiterhin der Kette statt nachzurechnen
(bewusst, dokumentiert), und die Bundle-Persistenz fehlt im Reviewerpfad.

**Realistische Bruchbedingung:** Eine spätere Änderung schreibt den
Provider-Attempt-Start nicht mehr vor dem AgentResult oder fügt zwischen beiden
weitere Findingtransitionen ein. Dann liefert `chain[:index(attempt)]` ein
Präfix, das den damaligen Requeststand nicht mehr trifft, und die Recovery
rekonstruiert eine Findingbasis, die es so nie gab — ohne dass ein Guard
anschlägt, weil die angebotenen IDs weiterhin vorhanden sind.

## 10. Pre-Mortem

In drei Monaten scheitert dieser Pfad am wahrscheinlichsten daran, dass jemand
die Reihenfolge der Recordpublikation ändert — etwa den Attempt-Start
verzögert, um eine Messung anzureichern. Die Recovery rechnet dann still gegen
ein falsches Präfix: Sie findet die angebotenen IDs, meldet keinen Fehler, und
liefert eine Findingbasis mit einer Response zu viel oder zu wenig. Das Ergebnis
ist wieder ein doppelter oder fehlender `responded`-Record — dieselbe
Fehlerklasse wie H-01, nur ausgelöst von der anderen Seite. Ein Guard, der die
Ordnung `started` vor AgentResult als Ketteninvariante prüft statt sie
vorauszusetzen, würde das abfangen.

Zweitwahrscheinlich trifft der nächste Absturz einen Reviewaufruf nach einer
schreibenden Codex-Operation. Die providerneutral benannte, aber nur für Codex
verdrahtete Bundle-Persistenz fehlt dort, und die Diagnose führt erneut über
einen Fingerprintvergleich statt über die durable Bindung.

Drittwahrscheinlich wird der Legacy-Fallback irgendwann als „genauso streng wie
der Normalweg" zitiert, weil er in der Praxis nie auffällig geworden ist, und
eine spätere Lockerung stützt sich auf diese falsche Annahme — obwohl das
Dokument sie jetzt ausdrücklich ausschließt.

## 11. Entscheidung

Beide P0-Kernursachen sind geschlossen, `H-01` nachweislich auf dem realen
Crashfenster, `H-02` durch die echte Typkonstruktion mit greifender
Digestprüfung, `H-03` durch eine ehrliche Dokumentation der reduzierten
Legacy-Prüftiefe. `H-04` bleibt ein sachlich zulässiges Restrisiko ohne Bezug
zur Wiederaufnahme dieses Laufs. Neue ausführbare Defekte habe ich nicht
gefunden.

Der Poison-Lauf kann nach dieser Freigabe wieder eingeworfen werden. Erwartet
wird: Übernahme der Codex-Korrektur aus der Record-ahead-Persistenz ohne neuen
Providerstart, Wiederherstellung aller vier Findinglinien im Mirror,
unverändert genau eine Disposition für C-03 — und anschließend ein Halt am
fingerprintgebundenen `UNEXPECTED-PATH`-Nutzergate für die Hotfixpfade, das
bewusst zu entscheiden und nicht zu umgehen ist.

```text
REVIEWER: claude
FINDING_STATUS: H-01 | CLOSED | Die Recovery rekonstruiert den requestzeitlichen Findingstand aus dem Kettenpraefix unmittelbar vor dem urspruenglichen ProviderAttemptPayload mit phase started und uebernimmt daraus ausschliesslich die im gebundenen Request angebotenen IDs; fehlender Attempt, fehlende angebotene ID und nicht replaybares Praefix brechen jeweils fail-closed ab. Am echten Poison-Lauf nachgerechnet: das Praefix vor dem Attempt von 12:24:09 mit binding_fingerprint 0bd309ba liefert C-03 mit null Responses, das heutige Ledger mit einer, das Resultat traegt damit genau eine, prior_count ist eins und es wird nichts angehaengt. C-03 behaelt exakt finding-response:C-03:1. Eine legitime spaetere Korrekturrunde wird nicht dedupliziert, weil ihr requestzeitliches Praefix den ersten responded-Record enthaelt und der neue Record Index zwei erhaelt. Der Zentraltest deckt jetzt beide Crashfenster ab und behauptet nach der zweiten Recovery genau einen responded-Record und eine Finding-Response; ab der zweiten Ausfuehrung ist der Zustand ein Fixpunkt.
FINDING_STATUS: H-02 | CLOSED | Der Bundle-Loader konstruiert keine SimpleNamespace-Assets mehr, sondern loest ueber get_type_hints und get_args den annotierten Typ NativeCodexEvidenceAsset auf und baut ihn mit Schluesselwortargumenten, sodass dessen __post_init__ Pfadnamensraum, SHA-256-Form, byte_count gegen die Bytelaenge und Digest gegen den Inhalt prueft. Eine Manipulation, die nur content bei gleicher Laenge aendert, wird mit evidence asset digest differs from content abgewiesen; byte_count- und sha256-Manipulationen ebenso. Da NativeCodexRequestError von ValueError erbt, wird der Fehler im Loader gefangen und als WorkflowExecutionError fail-closed gemeldet.
FINDING_STATUS: H-03 | CLOSED | Das Hotfixdokument benennt jetzt ausdruecklich die geringere Pruueftiefe des Legacy-Fallbacks, das Fehlen eines historisch persistierten Requestdokuments, die Autorisierung ueber die append-only Kombination aus Request-ID, Response-Digest, AgentResult-Payload und Attempt-Binding, den Verzicht auf eine erneute Writer-Schema-Validierung gegen ein Originalrequestdokument und die Regel, dass neue Laeufe diesen reduzierten Pfad nicht verwenden duerfen. Das deckt sich mit validate_against_bundle gleich False in genau diesem Zweig.
FINDING_STATUS: H-04 | RESIDUAL_RISK | Die Bundle-Persistenz ist providerneutral benannt, aber nur im Codex-Pfad verdrahtet. Fuer die Wiederaufnahme dieses Laufs ohne Belang, weil der offene Schritt codex_correction ist und Reviewer den Arbeitsbaum nicht schreiben. Die Dokumentation fuehrt die Asymmetrie als eigenen, nicht realisierten Aenderungsscope und behauptet keine Behebung.
REVIEW_EVIDENCE: selbst erhobener Branch-, HEAD-, Merge-Base- und Statusstand mit exakt uebereinstimmenden Navigationshashes f317502f und d6b9321f, Snapshot d28c4cfa; requestzeitliches Replay des Praefixes vor dem realen Attempt gemessen und gegen das heutige Ledger gestellt; Response-Schleife am echten Fall simuliert mit null anzuhaengenden Records; legitime zweite Korrekturrunde simuliert mit korrekt einem neuen Record; Kontrollfluss von original_attempt_record fuer Bundle- und Legacy-Pfad geprueft samt drei Fail-closed-Zweigen; aufgeloester Assettyp verifiziert und drei Manipulationsrichtungen einschliesslich gleichlanger content-Aenderung abgewiesen; Exceptiontyp als ValueError-Subklasse bestaetigt, sodass der Loader fail-closed wrappt; Zentraltest auf beide Crashfenster und zweite Recovery geprueft; Slice-Scope gegen Hotfixpfade und _validate_change_boundary auf current_slice.scope_paths geprueft; Incident-Evidenz mit 37 Records, State und Poison-Dateien unveraendert; eigener Lauf von tests/test_workflow.py und tests/test_orchestrator_runtime.py mit 147 passed in 21.63s sowie vier Einzelknoten gruen; vollstaendige Suite nicht ausgefuehrt, Fremdangaben nicht als Attestierung uebernommen | groesstes Restrisiko: die Recovery haengt jetzt am Kettenpraefix, und die Ordnung started vor AgentResult ist nur durch die Aufrufreihenfolge garantiert, nicht durch eine Ketteninvariante; nachgeordnet vertraut der Legacy-Pfad der Kette statt nachzurechnen und die Bundle-Persistenz fehlt im Reviewerpfad | realistische Bruchbedingung: eine spaetere Aenderung schreibt den Attempt-Start nicht mehr vor dem AgentResult oder fuegt dazwischen weitere Findingtransitionen ein, sodass chain bis index(attempt) ein Praefix liefert, das den damaligen Requeststand verfehlt, die angebotenen IDs aber weiterhin enthaelt und deshalb kein Guard anschlaegt
PRE_MORTEM: In drei Monaten scheitert der Pfad am wahrscheinlichsten daran, dass jemand die Reihenfolge der Recordpublikation aendert, etwa den Attempt-Start verzoegert, um eine Messung anzureichern. Die Recovery rechnet dann still gegen ein falsches Praefix, findet die angebotenen IDs, meldet keinen Fehler und liefert eine Findingbasis mit einer Response zu viel oder zu wenig; das Ergebnis ist wieder ein doppelter oder fehlender responded-Record, dieselbe Fehlerklasse wie H-01, nur von der anderen Seite ausgeloest. Ein Guard, der die Ordnung started vor AgentResult als Ketteninvariante prueft statt sie vorauszusetzen, wuerde das abfangen. Zweitwahrscheinlich trifft der naechste Absturz einen Reviewaufruf nach einer schreibenden Codex-Operation, wo die nur fuer Codex verdrahtete Bundle-Persistenz fehlt. Drittwahrscheinlich wird der Legacy-Fallback trotz der jetzt klaren Dokumentation irgendwann als gleich streng zitiert und traegt eine spaetere Lockerung.
FINAL_APPROVAL: YES
SAFE_TO_REQUEUE_POISON_TASK: YES
EXPECTED_NEW_CODEX_PROVIDER_START: NO
EXPECTED_SCOPE_GATE_BEFORE_CLAUDE: YES
STATUS: DONE
```
