# Claude-Konvergenzreview: zweiter Poison nach freigegebener P0-Recovery

**Reviewer:** Claude (manuell, adversarial, read-only) · **Datum:** 28. August 2026 ·
**Snapshot:** Arbeitsbaumdiff `188965ea896c691aa6bc6cce3f54492b97117cce`
**Vorrunden:** `…-review-claude-20260828-b2e46eaf.md`, `…-review-claude-20260828-d28c4cfa.md`

## 1. Ergebnis vorab

**Freigabe.** Kein offener Blocker. Der zweite Poison-Lauf darf erneut
eingeworfen werden.

Vorweg das Unangenehme: Meine Freigabe `d28c4cfa` war zu eng. Ich hatte in
`persist_native_codex_contract` nur die Response-Schleife gegen Doppelschreiben
geprüft und ausdrücklich als Gegenprobe notiert, sie hänge „nur neue Responses
an". Den `agent_result_payload`-Append derselben Funktion habe ich nicht gegen
den **zweiten** Aufruf aus `WorkflowEngine._run_codex` geprüft — und genau dort
lag der Defekt. Die Kontrollflussanalyse über beide Aufrufe hinweg, die dieser
Auftrag nun ausdrücklich verlangt, hätte ihn in der Vorrunde gefunden.

Die jetzige Korrektur trifft die Ursache. Der reale, bereits verschmutzte Lauf
ist mit ihr wieder aufnehmbar, und ein dritter Record kann nicht entstehen.

## 2. Selbst erhobener Snapshot und Reviewgrenze

| Merkmal | Gemessen | Erwartet | |
|---|---|---|---|
| Branch | `feature/plan-only-finding-carry-forward` | identisch | ✓ |
| `HEAD` | `dd6ad0c3e13a9ceb3dbc3c73ce89f7bc1c4aa10a` | identisch | ✓ |
| Run | `watch-20260828-113929.322399Z-a1f9abaf4ade` | identisch | ✓ |
| Records in der Kette | 38 | 38 | ✓ |
| Persistierter Zustand | Work-Unit 3, Slice 2, Runde 2, `codex_correction` | identisch | ✓ |
| Navigationshash `orchestrator.py` + `test_orchestrator_runtime.py` | `53c980e345557df84812cf501b9cc634499742a7` | — | erhoben |
| Arbeitsbaum | 12 geändert, 4 ungetrackt | Slice-02 + Hotfix + Reviews | ✓ |

**Integrität der Incident-Evidenz.** Die Kette lädt und validiert vollständig
(38 Records), `head.json` verweist auf den letzten Record, `state.json` und
`head.json` tragen beide den Zeitstempel des fehlgeschlagenen Wiederanlaufs
(28.08. 17:49) und keinen späteren. Der State steht semantisch unverändert auf
Work-Unit 3, Runde 2, `codex_correction`. Die dokumentierte Aussage, dass nichts
manuell repariert wurde, halte ich damit für belegt: Jede spätere Handbearbeitung
hätte entweder die Predecessor-Verkettung oder den `chain_sha256` in `head.json`
gebrochen.

**Reviewgrenze.** Kein `run_task`, kein Watcher, kein Bootstrap, kein Provider-,
Canary- oder Netzaufruf. Nichts committet, gestaged oder verschoben; State,
Checkpoints, Records, Inbox und Outbox nur gelesen. Die einzige Schreiboperation
ist dieses Dokument. Selbst ausgeführt habe ich `tests/test_orchestrator_runtime.py`
und `tests/test_workflow.py` → **`147 passed in 21.68s`** sowie mehrere
read-only In-Memory-Proben gegen die echte Kette. Die Codex-Angaben
(`1127 passed in 140.91s`, `185 passed in 30.33s`) sind Fremdangaben; ich habe
die vollständige Suite nicht ausgeführt und beanspruche keine
Orchestrator-Attestierung.

## 3. Was im freigegebenen Wiederanlauf tatsächlich passierte

Die Kette belegt den Ablauf lückenlos:

| Zeit (UTC) | Record | Fingerprint | Idempotenzschlüssel |
|---|---|---|---|
| 12:31:36 | `ar1-2dfe0101…` | `e0e6ec33…` | `native:agent-3-codex_correction-2:61e4bd47…` |
| 15:48:59 | `ar1-648237bc…` | `c9eee074…` | `native:agent-3-codex_correction-2:11bafd16…` |

Beide Payloads sind **feldgleich** (identischer Payload-Digest `b336db2e…`,
identische Request-ID und Response-SHA), beide Idempotenzschlüssel sind
**korrekt** aus dem jeweils eigenen gespeicherten Fingerprint abgeleitet — ich
habe beide Schlüssel selbst nachgerechnet und bestätigt. Die Prompt-Behauptung
trifft also exakt zu.

Der Mechanismus: `recover_pending_native_codex` schreibt idempotent mit
`recovery_fingerprint` = Fingerprint des vorhandenen Records. Danach ruft
`WorkflowEngine._run_codex` in `workflow.py:1278` **erneut**
`persist_native_codex_contract` auf — ohne `recovery_fingerprint`. Der alte Code
leitete den Schlüssel dort aus `self._artifact_fingerprint()` ab, also aus dem
inzwischen durch Codex und den Hotfix veränderten Arbeitsbaum → neuer Schlüssel
→ zweiter Record.

Den anschließenden Abbruch habe ich an den Artefakten verifiziert: Der
bestehende `finding-response:C-03:1`-Record trägt Fingerprint `e0e6ec33…`, also
den kanonischen. Sein Idempotenzschlüssel ist bewusst **fingerprintfrei**
(`finding-response:<id>:<index>`). Ein erneuter Append mit `c9eee074…` findet
daher den bestehenden Record und läuft in `ArtifactBridge._assert_equal`, das
`record.fingerprint != fingerprint` prüft → `structured artifact differs
semantically from the state-v3 statement`. Retry 2 und 3 sahen dann zwei
AgentResult-Records und brachen an der damaligen Eindeutigkeitsprüfung ab. Die
Darstellung im Hotfixdokument deckt sich mit dieser Evidenz; ich habe keine
Überbehauptung gefunden.

## 4. Prüfung der Korrektur

### 4.1 Kontrollflussanalyse über beide Persistenzaufrufe

Der entscheidende Punkt ist nicht der Unit-Test, sondern dass **beide** Aufrufe
dieselbe Bindung wählen:

1. `recover_pending_native_codex` → `persist_native_codex_contract(output,
   invocation.previous_findings, recovery_fingerprint=record.fingerprint.sha256)`
   (`orchestrator.py:1337`).
2. `WorkflowEngine._run_codex` → `_persist_structured("persist_native_codex_contract",
   output, history.findings)` (`workflow.py:1278`) — **ohne** `recovery_fingerprint`.

In `persist_native_codex_contract` wird jetzt vor jedem Append die Kette nach
demselben `logical` durchsucht und über `_canonical_native_agent_result` ein
kanonischer Record bestimmt. Ist er vorhanden und stimmt seine Payload mit der
neu gebildeten überein, gilt:

```python
fingerprint = canonical.fingerprint.sha256
idempotency_key = canonical.idempotency_key
```

`recovery_fingerprint` und `self._artifact_fingerprint()` werden **nur im
else-Zweig** ausgewertet, also nur wenn gar kein Record existiert. Damit ist der
heutige Baumfingerprint aus beiden Aufrufen strukturell ausgeschlossen, sobald
ein durabler Fakt vorliegt. Entscheidend für den zweiten Fehler: Dasselbe
`fingerprint` wird auch an die Finding-Response-Appends weitergereicht
(`orchestrator.py:1823`), sodass der fingerprintfreie Schlüssel
`finding-response:C-03:1` nicht mehr mit einem abweichenden Fingerprint auf den
bestehenden Record treffen kann. Weicht die Payload ab, endet der Pfad
fail-closed mit „native agent result logical binding differs".

Auf der **realen** Kette nachgerechnet (Q-Probe): kanonisch ist
`ar1-2dfe0101…` mit `e0e6ec33…`, und der vorhandene `finding-response:C-03:1`
trägt exakt denselben Fingerprint. Beide Aufrufe konvergieren also auf die
bestehende Bindung; weder ein dritter AgentResult noch ein
`_assert_equal`-Konflikt kann entstehen.

### 4.2 Falsifikation der Altfallregel

`_canonical_native_agent_result` ist eine reine Funktion über die
Kandidatenliste; ich habe sie direkt mit Datensätzen aus der echten Kette
gefüttert:

| Probe | Ergebnis |
|---|---|
| realer Altfall, zwei identische Payloads, je korrekt gebunden | akzeptiert, kanonisch ist der **früheste** Record |
| divergierende Payload (`response_sha256` geändert) | `native agent recovery has divergent agent-result records` |
| falsche Idempotenzbindung (Schlüssel des ersten am zweiten Record) | `native agent recovery result idempotency binding differs` |
| Fingerprint manipuliert, Schlüssel unverändert | `…idempotency binding differs` |
| fremder `logical_id` | **konstruktiv unmöglich** — `record_id` ist stabil aus der logischen Identität abgeleitet, ein solcher Record kann unter diesem `logical` gar nicht existieren |
| divergierende Payload im Test | derselbe Fehler, im Regressionstest festgehalten |

Beide vom Auftrag geforderten Falsifikationen enden also vor jeder
Workflowentscheidung fail-closed.

### 4.3 „Frühester Record" — Provenienz

`_canonical_native_agent_result` gibt `candidates[0]` zurück, also das erste
Element der übergebenen Liste. Beide Aufrufstellen bilden diese Liste durch
Iteration über `store.load_chain()`. Das ist tragfähig, weil die Kette
strukturell geordnet ist: `artifact_store` prüft beim Append
`record.predecessor_ids[0] == expected_head` gegen den aktuellen Kopf und
liefert beim Laden `_order_chain(records)`; `head.json` trägt zusätzlich
`chain_sha256` und `record_count`. Ein später erzeugter Record kann sich also
nicht vor einen früheren schieben.

Eine Einschränkung nenne ich ausdrücklich: Die Auswahl ist an die
**Kettenposition** gebunden, nicht an einen expliziten Vergleich mit dem
ursprünglichen `ProviderAttemptPayload`. Ein passend konstruierter späterer
Record mit identischer Payload kann nicht kanonisch werden, solange der frühere
existiert; existierte nur der spätere, würde er akzeptiert. Seine Provenienz
bleibt dann aber über `response_sha256` gegen die unveränderte Raw Response und
über die requestzeitliche Findingrekonstruktion am Attempt verankert. Ich habe
keinen Pfad gefunden, auf dem daraus ein ausführbarer Defekt entsteht.

### 4.4 Regressionstest

`test_native_codex_record_ahead_recovery_completes_finding_responses` deckt jetzt
drei Fenster nacheinander ab: Absturz vor dem Response-Record, danach eine
**Fingerprintänderung des Arbeitsbaums** mit anschließendem engine-seitigen
`persist_native_codex_contract`-Aufruf und der Behauptung
`sum(isinstance(..., AgentResultPayload)) == 1` — also kein zweiter Record trotz
gedriftetem Baum —, eine Falsifikation der divergierenden Payload, und
schließlich ein bewusst angelegter, korrekt gebundener Duplikatrecord, der die
reale verschmutzte Kette nachbildet. Danach bleibt es bei zwei Records (kein
dritter) und **genau einem** `responded`-Record. Das ist exakt der Zustand des
echten Laufs.

## 5. Nachwirkung der verbleibenden Dublette

Der Auftrag verlangt, technische Mehrdeutigkeit von einer bloß doppelten
menschenlesbaren Zeile zu trennen. Geprüft:

- **`replay_artifacts`:** Die Kette lädt und repliziert vollständig (38 von 38
  Records). Die Ordnungsregel für `AgentResultPayload` verlangt nur, dass die
  referenzierte Work-Unit früher steht; das gilt für beide Records.
- **Mirrorabgleich:** `artifact_migration.py` referenziert `AgentResultPayload`
  an keiner Stelle. Der Dual-Write-Vergleich betrachtet Gate-Entscheidungen und
  Finding-Transitionen — die Dublette erzeugt dort keine Divergenz.
- **Final-Review-Preflight:** `final_review_preflight.py:212` filtert
  AgentResults nach Rolle, Work-Unit und Fingerprint des Measurement-Records und
  prüft `codex[-1].payload.outcome != "ready"`. Beide Records tragen `ready`; die
  Auswahl kann das Ergebnis nicht ändern.
- **Auditprojektion:** `artifact_projection.py:198` rendert AgentResults; die
  Dublette erscheint dort als zusätzliche Zeile. Das ist eine
  Darstellungsredundanz in einer laut `AGENTS.md` ausdrücklich menschlichen
  Auditsicht, keine technische Autorität.
- **Scope-Gate und Commitbindung:** beide arbeiten über Pfade, Fingerprints und
  Bindings, nicht über die Zahl der AgentResult-Records.

Einen neuen ausführbaren Defekt aus der verbleibenden Dublette habe ich nicht
gefunden.

## 6. Disposition H-01 bis H-04 und des neuen Fensters

| Befund | Disposition | Begründung |
|---|---|---|
| `H-01` requestzeitliche Findingbasis | **CLOSED, ohne Regression** | Auf der realen Kette nachgemessen: Präfix vor dem Attempt 12:24:09 liefert C-03 mit 0 Responses, heute 1 → keine zweite Disposition. Ledger `C-01:1, C-02:1, C-03:1, C-04:0`. |
| `H-02` Evidenzasset-Invarianten | **CLOSED, ohne Regression** | Der annotierte Assettyp wird weiterhin konstruiert; die Digest-, Bytezahl- und Pfadprüfung greift. |
| `H-03` Legacy-Prüftiefe | **CLOSED** | Der Text benennt die geringere Prüftiefe unverändert und ergänzt die Altfallregel für mehrere Records ohne sie zu beschönigen. |
| `H-04` Reviewer-Asymmetrie | **RESIDUAL_RISK** | Für diesen Codex-Recoverylauf nicht ausführbar; als eigener, nicht realisierter Änderungsscope dokumentiert. |
| **neues Fenster** doppelter AgentResult | **CLOSED** | Beide Persistenzaufrufe adoptieren die kanonische Bindung; auf der realen Kette verifiziert. |

## 7. Größtes Restrisiko und realistische Bruchbedingung

Größtes Restrisiko ist, dass die Altfallregel **allgemein** und nicht auf diesen
einen historischen Fall begrenzt ist. `_canonical_native_agent_result` toleriert
beliebig viele semantisch identische Records, sofern jeder korrekt
fingerprintgebunden ist, und markiert das lediglich mit einem
`logger.warning`. Nach der Korrektur entstehen zwar keine neuen Dubletten mehr,
aber falls ein anderer Pfad künftig doch einen AgentResult mit frischem
Schlüssel schreibt, wächst die Kette stillschweigend weiter — die einzige Spur
wäre eine Logzeile, die in einem Watch-Lauf leicht untergeht.

Nachgeordnet: Die Kanonizität hängt an der Kettenposition statt an einem
expliziten Provenienzvergleich mit dem Provider-Attempt; und der aus der
Vorrunde bekannte Punkt bleibt, dass die Ordnung `started` → AgentResult nur
durch die Aufrufreihenfolge garantiert ist, nicht durch eine Ketteninvariante.

**Realistische Bruchbedingung:** Ein dritter Persistenzaufruf entsteht auf einem
Pfad, der `persist_native_codex_contract` nicht durchläuft — etwa eine künftige
Wiederholung im Watcher oder ein zusätzlicher Bridge-Append —, und schreibt
denselben Fakt mit einer neu berechneten Bindung. Die Altfallregel akzeptiert
ihn, die Kette wächst pro Anlauf um einen Record, und niemand bemerkt es, weil
alle Prüfungen weiterhin grün bleiben und nur eine Warnung fällt.

## 8. Pre-Mortem

In drei Monaten scheitert dieser Pfad am wahrscheinlichsten daran, dass die
Toleranz gegenüber identischen AgentResult-Records als Normalzustand gelesen
wird. Ein neuer Schreibpfad — etwa eine zusätzliche Persistenz beim
Watch-Resume — erzeugt pro Anlauf einen weiteren Record; jede Prüfung bleibt
grün, weil Payloads identisch und Bindungen korrekt sind. Nach einigen Monaten
enthält die Kette ein Dutzend Records für einen einzigen Providerlauf, die
Auditprojektion zeigt ihn ebenso oft, und die Frage, wie oft Codex hier
tatsächlich geantwortet hat, ist aus der Kette nicht mehr zu beantworten — obwohl
sie technisch weiterhin eindeutig ist.

Zweitwahrscheinlich verschiebt eine Änderung die Reihenfolge der
Recordpublikation, sodass die kettenpositionsbasierte Kanonizität und die
requestzeitliche Findingrekonstruktion auf ein anderes Präfix zeigen als
beabsichtigt — dieselbe Fehlerklasse wie H-01, nur von der Ordnungsseite her.

Drittwahrscheinlich wiederholt sich mein eigener Reviewfehler: Eine Korrektur
wird an der Funktion geprüft, in der sie steht, aber nicht über beide
Aufrufstellen hinweg. Genau das hat den zweiten Poison verursacht.

## 9. Entscheidung

Die Korrektur schließt das neue Crashfenster an der Ursache, hält die
Falsifikationen fail-closed, lässt H-01 bis H-03 unangetastet und erzeugt aus der
verbleibenden Dublette keinen ausführbaren Folgefehler. Der zweite Poison-Lauf
darf erneut eingeworfen werden.

Erwartet wird derselbe Ablauf wie zuvor, diesmal ohne den Doppelschreibfehler:
Übernahme der Codex-Korrektur aus der Record-ahead-Persistenz ohne neuen
Providerstart, kein dritter AgentResult, weiterhin genau eine C-03-Disposition,
vollständiges Ledger C-01 bis C-04 — und anschließend ein Halt am
fingerprintgebundenen `UNEXPECTED-PATH`-Nutzergate, weil `src/workflow.py`,
`tests/test_workflow.py` und die Reviewdokumente außerhalb des persistierten
Slice-Scopes liegen.

```text
REVIEWER: claude
REVIEW_EVIDENCE: selbst erhobener Stand mit Branch feature/plan-only-finding-carry-forward, HEAD dd6ad0c3, 38 Records, Snapshot 188965ea; beide AgentResult-Records aus der echten Kette rekonstruiert und als feldgleich mit identischem Payload-Digest b336db2e, identischer Request-ID und Response-SHA bestaetigt, beide Idempotenzschluessel selbst nachgerechnet und je korrekt an den eigenen gespeicherten Fingerprint gebunden; Kontrollfluss ueber beide Persistenzaufrufe geprueft, wobei recovery_fingerprint und _artifact_fingerprint nur im else-Zweig ausgewertet werden und beide Aufrufe auf der realen Kette auf den fruehesten Record ar1-2dfe0101 mit Fingerprint e0e6ec33 konvergieren, der identisch mit dem Fingerprint des bestehenden finding-response:C-03:1 ist, sodass weder ein dritter AgentResult noch ein _assert_equal-Konflikt entstehen kann; vier Falsifikationen von _canonical_native_agent_result gefahren mit divergierender Payload, falscher Idempotenzbindung, manipuliertem Fingerprint und fremder logischer Identitaet, wobei letztere wegen der stabilen record_id konstruktiv unmoeglich ist; Kanonizitaet an die strukturell gesicherte Kettenordnung gebunden ueber predecessor_ids, _order_chain und chain_sha256 in head.json; Nachwirkung der Dublette in replay_artifacts, artifact_migration, final_review_preflight, Auditprojektion, Scope-Gate und Commitbindung einzeln geprueft ohne ausfuehrbaren Folgefehler; H-01 auf der realen Kette nachgemessen mit requestzeitlich null und heute einer C-03-Response und vollstaendigem Ledger C-01 bis C-04; Integritaet von state.json, head.json und Kette bestaetigt; eigener Lauf tests/test_orchestrator_runtime.py und tests/test_workflow.py mit 147 passed in 21.68s, vollstaendige Suite nicht ausgefuehrt und Fremdangaben nicht als Attestierung uebernommen | groesstes Restrisiko: die Altfallregel ist allgemein statt auf diesen historischen Fall begrenzt und toleriert beliebig viele semantisch identische AgentResult-Records mit lediglich einer Logwarnung, sodass ein kuenftiger zusaetzlicher Schreibpfad die Kette still wachsen liesse; nachgeordnet haengt die Kanonizitaet an der Kettenposition statt an einem expliziten Provenienzvergleich mit dem Provider-Attempt, und die Ordnung started vor AgentResult ist weiterhin nur durch die Aufrufreihenfolge garantiert | realistische Bruchbedingung: ein dritter Persistenzaufruf auf einem Pfad, der persist_native_codex_contract nicht durchlaeuft, schreibt denselben Fakt mit neu berechneter Bindung; die Altfallregel akzeptiert ihn, die Kette waechst pro Anlauf um einen Record, und alle Pruefungen bleiben gruen
PRE_MORTEM: In drei Monaten wird die Toleranz gegenueber identischen AgentResult-Records als Normalzustand gelesen. Ein neuer Schreibpfad, etwa eine zusaetzliche Persistenz beim Watch-Resume, erzeugt pro Anlauf einen weiteren Record; jede Pruefung bleibt gruen, weil Payloads identisch und Bindungen korrekt sind, und nur eine Logwarnung faellt. Nach einigen Monaten enthaelt die Kette ein Dutzend Records fuer einen einzigen Providerlauf, die Auditprojektion zeigt ihn ebenso oft, und wie oft Codex tatsaechlich geantwortet hat, ist aus der Kette nicht mehr abzulesen. Zweitwahrscheinlich verschiebt eine Aenderung die Reihenfolge der Recordpublikation, sodass kettenpositionsbasierte Kanonizitaet und requestzeitliche Findingrekonstruktion auf ein anderes Praefix zeigen als beabsichtigt. Drittwahrscheinlich wiederholt sich mein eigener Reviewfehler aus der Vorrunde: eine Korrektur wird an der Funktion geprueft, in der sie steht, aber nicht ueber beide Aufrufstellen hinweg.
FINAL_APPROVAL: YES
SAFE_TO_REQUEUE_POISON_TASK: YES
EXPECTED_NEW_CODEX_PROVIDER_START: NO
EXPECTED_SCOPE_GATE_BEFORE_CLAUDE: YES
STATUS: DONE
```
