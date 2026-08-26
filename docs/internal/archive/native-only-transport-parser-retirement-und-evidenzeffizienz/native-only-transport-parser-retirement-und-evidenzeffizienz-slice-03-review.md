# Slice 03 – Digestgebundene Evidenzminimierung und messbare Betriebswirkung

> Archiviert nach Abschluss des Arbeitspakets; nichtautoritative historische Entwicklungsevidenz.

## Gegenstand

Slice 03 ersetzt redundante Vollplan- und Korrekturevidenz durch kanonische,
digestgebundene Ausführungspakete und erweitert die aus der Recordkette
erzeugte Menschenprojektion um operationenbezogene Eingabe- und
Attemptmetriken.

Reviewbasis für Claude ist der vollständige Diff ab dem freigegebenen
Slice-02-Commit `2e31b41` bis zum aktuellen Arbeitsbaum.

## Implementierung

- `provider_input_efficiency` erzeugt ein kanonisches Slice-Paket ausschließlich
  aus Slice-ID, Ziel, Akzeptanzkriterien, autorisierten Pfaden, ausdrücklichen
  Querverweisen sowie Pfad und SHA-256 des Quellplans.
- Korrekturpakete enthalten ausschließlich den betroffenen offenen
  Finding-Snapshot, dessen Akzeptanztests, die aktuelle fingerprintgebundene
  Pfadgrenze und das aktuelle Diff. Geschlossene und nicht betroffene Findings
  werden nicht transportiert.
- Der Workflow verwendet das Slice-Paket für Implementierungen und das
  Korrekturpaket für Korrekturrunden. Plan und Planrevision behalten weiterhin
  den aktuellen Planstand. Die Systempolicy liegt nur noch als eigenständig
  digestgebundene Evidenz vor und wird nicht zusätzlich im Work-Context
  wiederholt.
- Bei einer Korrektur bindet der native Codex-Request den aktuellen
  Repositoryfingerprint. Ein bereits persistiertes Record-ahead-Ergebnis wird
  weiterhin vor einem neuen Providerstart wiederverwendet; ohne Recovery wird
  ein geänderter autoritativer Finding-Snapshot vor dem Providerstart neu
  gebunden.
- Native Codex- und Claude-Requests weisen doppelte Evidenz-Sourcepaths und
  Inhaltsdigests fail-closed ab. Der vorbereitete Providerinput weist außerdem
  content-gleiche Komponenten vor Capabilitycheck und Prozessstart zurück.
- Die Markdownprojektion weist je Provideroperation Inputzeichen, UTF-8-Bytes,
  Laufzeit, Attemptanzahl, offenen/retried/single-attempt-Status und bekannte
  beziehungsweise unbekannte Usagefelder aus. Fehlende Usage wird niemals als
  Nullwert erfunden.
- README und Phase-2-Roadmap dokumentieren den erreichten Native-only-Cutover,
  das Parser-Retirement, die strukturelle Eingabereduktion und die verbleibende
  Corpus-, Trend- und Bootstraparbeit.

## Baseline- und Effizienznachweis

Baseline und Lock wurden nur gelesen und bleiben bytegleich zu ihrem
reviewten Slice-01-Stand:

- Baseline-SHA-256:
  `3f07b52b79badf1340998973c4145b20a93892ea0f174942cd21ab1331c7adfc`
- Lock-SHA-256:
  `11bb60a6aed1ab78b7911d9a2eb9ea6a216b845a313c324df0558085ad9fabb9`

Die Werte wurden aus Fixture, Lock und aktuellem Builder unabhängig
berechnet; sie sind keine Eingabeerwartung für das Review:

| Operation | Baseline Zeichen/Bytes | Aktuell Zeichen/Bytes | Reduktion Zeichen/Bytes |
|---|---:|---:|---:|
| `codex_implementation` | 55.321 / 55.321 | 7.878 / 7.878 | 47.443 / 47.443 |
| `codex_correction` | 55.418 / 55.418 | 8.022 / 8.022 | 47.396 / 47.396 |

Die Gegenprobe berechnet die Differenz aus entfernten, hinzugefügten und
unveränderten kanonischen Komponenten. `response_schema` bleibt in beiden
Operationen namens- und digestgleich; geänderte gleichnamige Komponenten
werden als entfernt plus hinzugefügt bilanziert.

## Finding-Reaktion

`C-08` wird akzeptiert. Die drei in Slice 02 zusätzlich benötigten
Regressionstestdateien `tests/test_quota_wait.py`, `tests/test_workflow_state.py`
und `tests/test_structured_artifact_regressions.py` sind im von Claude
freigegebenen Slice-02-Commit `2e31b41` enthalten. Sie waren für die
Signaturbereinigung der entfernten Reparatur- und Providerrollenpfade
erforderlich. Slice 03 verändert diese Dateien nicht und verschleiert die
Scopeabweichung nicht; Claude soll im Review entscheiden, ob damit die eigene
Observation geschlossen werden kann.

## Validierung

- Vollständige Repositorymatrix: `1007 passed in 73.48s`
- Fokussierte Paket-, Request-, Projektions- und Workflowmatrix: `149 passed`
- Zusätzliche Sprach-/Workflow-Gegenprobe nach Korrektur: `108 passed`
- `git diff --check`: sauber
- Externe Provider und Canaries: nicht aufgerufen

## Reviewauftrag

Claude prüft adversarial, ob die Reduktion sämtliche fachlich erforderlichen
Bindungen erhält, ob Recovery und autoritative Finding-Rekonstruktion
unverändert fail-closed bleiben, ob die Komponentenbilanz nicht zirkulär ist
und ob Usage-/Tokenaussagen ausschließlich aus Records stammen.

FINDING_RESPONSE: C-08 | ACCEPTED | Die drei zusätzlichen Slice-02-Testpfade sind im freigegebenen Commit transparent nachgewiesen; Slice 03 ändert sie nicht und bittet Claude um die abschließende Disposition.
TEST_FILES_TOUCHED: tests/test_artifact_projection.py, tests/test_native_codex_request.py, tests/test_native_review_request.py, tests/test_provider_input_budget.py, tests/test_provider_input_efficiency.py, tests/test_workflow.py
IMPLEMENTATION_READY: 03 | YES
STATUS: DONE

---

## Claude-Review zu Slice 03

Manueller Review außerhalb von `run_task`, ohne Provideraufruf. Produktcode und
Tests blieben unverändert; angehängt wurde ausschließlich dieser Abschnitt.
Reviewgrenze: `2e31b41 refactor(orchestrator): retire text result contracts`
gegen den Arbeitsbaum, einschließlich der ungetrackten Produktivdatei
`src/provider_input_efficiency.py` und dieses Berichts. Die vollständige Matrix
(`1007 passed`) habe ich nicht wiederholt. Sämtliche Zahlen und Digests unten
habe ich selbst aus dem Repository abgeleitet; aus Prompt und
Implementierungsbericht habe ich keinen Sollwert übernommen.

### 1. Kanonisches Slice-Paket und Geschwisterdichtheit

`build_slice_execution_package()` projiziert genau sechs Felder: Schemaname,
Quellplanpfad mit SHA-256, Slice-ID, Ziel, Akzeptanzkriterien, autorisierte
Pfade und ausdrückliche Querverweise. Der Plantext selbst wird nicht
transportiert, sondern nur digestgebunden referenziert.

Ich habe die Dichtheit nicht am Paket, sondern am **vollständigen Request**
geprüft — kanonisches JSON plus Writerschema plus alle Evidenzassets — mit dem
synthetischen Drei-Slice-Plan: `SENTINEL-SLICE-ONE`, `SENTINEL-SLICE-THREE`,
`SENTINEL-CRITERION-ONE` und `SENTINEL-CRITERION-THREE` kommen **nirgends** vor,
während Ziel, Kriterium und Querverweis des Zielslice vorhanden sind.

Die zweite mögliche Hintertür habe ich ebenfalls geschlossen: `work_context`
speist sich aus `context.distilled_context`, und dessen `distilled_plan` ist in
`src/orchestrator.py:2753-2755` eine feste einzeilige Anweisung, nicht der
Plantext. `context.approved_plan_text` wird seit diesem Slice ausschließlich
über die beiden Paketbauer verwendet; ein direktes `approved-plan`-Evidenzstück
existiert nicht mehr. Audit-Markdown wird nirgends zurückgelesen.

### 2. Paketform je Operation

Die Zuordnung stimmt. Für `WorkUnitKind.PLAN` wird `approved_plan_text` gar
nicht erst geladen (`src/orchestrator.py:2702-2704` beschränkt das Laden auf
`SLICE` und `CORRECTION`), sodass Planung und Planrevision unverändert aus
Auftrag, Zielbranch, Planpfad und dem Arbeitsbaumstand arbeiten — hier wurde
nichts entfernt. Der Finalbericht läuft unter `WorkUnitKind.FINAL_REVIEW`,
erhält also ebenfalls nie ein Planevidenzstück, dafür über den
`work_context`-Override weiterhin Basiscommit, Branchfingerprint, die
autorisierten Pfade aller abgeschlossenen Slices und den **vollständigen
Branchdiff** (`src/workflow.py:1552-1571`). Implementierung erhält das
Slice-Paket, Korrektur das Korrekturpaket.

### 3. Korrekturpaket

Die Auswahl ist doppelt fail-closed: `native_findings` filtert
`history.findings` auf `state.current_work_unit.open_findings` **und** auf
`FindingStatus.OPEN`, und weicht die resultierende ID-Menge von der erwarteten
ab, bricht der Lauf mit „lacks its exact affected open finding set" ab. Dieselbe
gefilterte Menge geht auch als `previous_findings` in den `NativeCodexContext`,
sodass weder der Vertrag noch das Paket ein fremdes oder geschlossenes Finding
sieht. `build_correction_execution_package()` weist zusätzlich leere Mengen,
doppelte IDs und jedes nicht offene Finding ab.

Als Delta dient `collect_changes(start_commit).full_diff`, also der aktuelle
kumulative Diff seit der persistierten Slicegrenze — kein früherer Volldiff und
keine zweite, unabhängig rekonstruierte Deltaquelle.

### 4. Fingerprint- und Snapshotbindung

`correction_fingerprint` stammt aus demselben `collect_changes()`-Aufruf, der
auch das Delta liefert, und wird sowohl als top-level `current_fingerprint` des
Requests als auch als `current_fingerprint` im Korrekturpaket verwendet. Beide
binden damit denselben aktuellen Repositoryzustand.

Der autoritative Finding-Snapshot wirkt vor jedem neuen Providerstart: Weicht
`_bind_authoritative_native_findings()` vom Spiegel ab, wird der gesamte Request
einschließlich Paket und Invocation **neu gebaut**, nicht nur die Historie
ersetzt. Wichtig für die Recovery: Dieser Zweig liegt hinter `if recovered is
None`, sodass eine Record-ahead-Wiederverwendung unberührt bleibt. Delta und
Fingerprint werden vor dem Rebuild einmal erhoben und wiederverwendet, sodass
der Rebuild denselben Zustand bindet.

### 5. Deduplizierung

Die Sperre greift auf drei Ebenen: Evidenz-ID (bereits vorher), Quellpfad und
Inhaltsdigest im Codex- wie im Claude-Request sowie Inhaltsdigest der
vorbereiteten Providerkomponenten. Ich habe die vom Reviewauftrag genannten
Umgehungen einzeln versucht:

| Versuch | Ergebnis |
|---|---|
| gleicher Inhalt unter anderer Evidenz-ID | abgewiesen |
| zweimal derselbe `source_path` | abgewiesen |
| umgekehrte Einfügereihenfolge derselben Dublette | abgewiesen |
| identischer Inhalt oberhalb der Inline-Grenze (Assetzustellung) | abgewiesen |
| inhaltsgleiche Providerkomponenten unter gültigen Namen | abgewiesen |
| Unicode NFC gegen NFD | zugelassen |
| tatsächlich verschiedener Inhalt (Kontrolle) | zugelassen |

Der Unicode-Fall ist kein Leck: NFC und NFD sind unterschiedliche Bytefolgen und
damit unterschiedlicher Inhalt; die Regel ist Inhaltsgleichheit, nicht optische
Gleichheit. Der Inline-/Asset-Wechsel kann nichts umgehen, weil der Digest über
den Rohinhalt vor der Zustellungsentscheidung gebildet wird.

### 6. Baseline, Lock und methodische Unabhängigkeit

`git diff fc978d8 -- tests/fixtures/` ist leer: Fixture und Lock sind seit dem
freigegebenen Slice-01-Commit bytegleich. Ich habe beide Digests selbst
berechnet und zusätzlich nachgerechnet, dass der im Lock hinterlegte
Baselinedigest tatsächlich dem Dateiinhalt entspricht. Die Baseline enthält
sechs Operationen, deren `total_chars`/`total_bytes` jeweils exakt der Summe
ihrer Komponenten entsprechen; `compare_provider_input_components()` prüft
genau diese Konsistenz und verweigert eine Bilanz, wenn die eingefrorene Summe
nicht aufgeht.

Ein Hinweis zur Aussagekraft: Die Invariante in
`ProviderInputDelta.__post_init__` — Reduktion gleich Summe der entfernten
minus Summe der hinzugefügten Komponenten — ist bei einer vollständigen
Partition in entfernt/hinzugefügt/unverändert konstruktionsbedingt erfüllt. Sie
belegt Vollständigkeit der Bilanz, nicht die Einsparung selbst. Tragend sind
die eingefrorene Konsistenzprüfung, die Digestgleichheit der unveränderten
Komponenten und die tatsächliche Reduktionsgröße.

### 7. Die Gegenprobe misst nicht die produktive Zustellungsform

Hier liegt der einzige konkrete Befund. `_current_components()` in
`tests/test_provider_input_efficiency.py:235` ruft den Builder mit
`inline_evidence_chars=10` auf. Produktiv wird dieser Parameter nirgends
gesetzt; `src/workflow.py` und `src/agent_adapters.py` verwenden ausschließlich
den Standard `DEFAULT_INLINE_EVIDENCE_CHARS = 24_000`.

Ich habe die Größen selbst berechnet: Das Slice-Paket umfasst 509 Zeichen, das
Korrekturpaket 470, die Systemrichtlinie 213. Alle drei liegen weit unter der
produktiven Inline-Grenze und werden im echten Lauf also **inline** in
`stdin_prompt` geführt. Produktiv entstehen damit genau zwei Komponenten,
`stdin_prompt` und `response_schema`; die vom Test gemessenen vier Komponenten
mit zwei Evidenzassets kommen im Betrieb nicht vor.

Ich habe beide Formen durchgerechnet:

| Operation | Form | Komponenten | Aktuell | Reduktion |
|---|---|---|---|---|
| `codex_implementation` | Test (`inline=10`) | 4 | 7 878 | 47 443 |
| `codex_implementation` | produktiv (Standard) | 2 | 7 634 | **47 687** |
| `codex_correction` | Test (`inline=10`) | 4 | 8 022 | 47 396 |
| `codex_correction` | produktiv (Standard) | 2 | 7 781 | **47 637** |

Zwei Dinge sind wichtig. Erstens ist die Einsparung in beiden Formen real und
groß, und die produktive Form ist sogar **günstiger** — der Test untertreibt,
er beschönigt nicht. Zweitens funktioniert `compare_provider_input_components()`
in der produktiven Form einwandfrei: Die Bilanz schließt, `response_schema`
bleibt namens- und digestgleich, und die beiden Baseline-Assets erscheinen
korrekt als entfernt. Es liegt also kein Produktdefekt vor, sondern ein
Nachweis, der eine Zustellungsform vermisst, die der Betrieb nicht erzeugt.
Weil die reproduzierbare Messung der Kern dieses Slice ist, halte ich das für
konkrete Folgearbeit und melde es als Observation.

### 8. Markdownprojektion

Die Projektion leitet alles aus der Recordkette ab. `ProviderInputMeasurementPayload`
wird über `measurement_record_id` je Attempt aufgelöst; Inputzeichen und
-bytes erscheinen nur, wenn eine Messung vorliegt und über alle Attempts
konsistent ist, sonst `unknown`. Attemptanzahl, offene Attempts, Laufzeit mit
bekannt/unbekannt-Zählung und ein Retrystatus aus `open`, `retried` und
`single-attempt` sind vorhanden.

Besonders relevant: Die Usagesummen rendern jetzt `sum:unknown` statt wie zuvor
`sum:0`, wenn kein Attempt einen Wert liefert. Das korrigiert eine echte
Falschaussage — fehlende Usage erschien vorher als Null. Ein geschätzter
Tokenwert wird nirgends gebildet; Zeichen und Bytes werden als eigene Größen
ausgewiesen und nicht in Tokens umgedeutet.

### 9. Unveränderte Betriebsgrenzen

Kompaktoutput, Vollmodus, Rohantwortpersistenz, Replay, Request-ID-Bildung und
Writerschemadigest sind unberührt; der Slice fasst weder `src/agent_runtime.py`
noch `src/agent_adapters.py` noch die Artefaktkette an. Die Reihenfolge bleibt
deterministisch: Evidenz wird vor dem Bau sortiert, Pfade sortiert und
dedupliziert, und die Paket-JSONs sind kanonisch mit sortierten Schlüsseln.
`git diff --check` ist sauber, und meine fokussierte Gegenprobe über
Effizienz-, Request-, Budget- und Projektionstests meldet `80 passed in 3.96s`.

### 10. Dokumentation

README beschreibt Paketprojektion, Deduplizierung und Projektionssemantik
zutreffend und hält ausdrücklich fest, dass fehlende Usage `unknown` bleibt und
Zeichen keine Tokens sind. Die Roadmap markiert 3.2 als erreicht — das deckt
sich mit dem tatsächlichen Stand — und verschiebt die Attempt-, Quota- und
Retrytelemetrie korrekt aus der offenen Liste. Corpuspflege (3.3), runübergreifende
Trends (3.4) und Bootstrap-Langläufe (3.5) bleiben als nachgelagert
ausgewiesen. Es wird nichts überbeansprucht.

REVIEWER: claude
NEW_FINDING: C-09 | OBSERVATION | Die Effizienz-Gegenprobe misst eine Zustellungsform, die der produktive Lauf nicht erzeugt. `_current_components()` in `tests/test_provider_input_efficiency.py:235` ruft `build_native_codex_request(...)` mit `inline_evidence_chars=10` auf, während `src/workflow.py` und `src/agent_adapters.py` den Parameter nie setzen und damit den Standard `DEFAULT_INLINE_EVIDENCE_CHARS = 24_000` verwenden. Ich habe die Größen selbst berechnet: Slice-Paket 509 Zeichen, Korrekturpaket 470 Zeichen, Systemrichtlinie 213 Zeichen — alle drei liegen unter der produktiven Inline-Grenze und werden im Betrieb inline in `stdin_prompt` geführt. Produktiv entstehen deshalb genau zwei Komponenten (`stdin_prompt`, `response_schema`), während der Test vier Komponenten mit zwei Evidenzassets bilanziert. Die zertifizierte Reduktion gehört damit zu einer nicht produzierten Requestform: `codex_implementation` ergibt im Test 47 443 gegenüber 47 687 produktiv, `codex_correction` 47 396 gegenüber 47 637. Es liegt kein Produktdefekt vor — die Einsparung ist in beiden Formen real, die produktive Form sogar größer, und `compare_provider_input_components()` bilanziert die produktive Zwei-Komponenten-Form korrekt mit digestgleichem `response_schema` —, aber der zentrale reproduzierbare Nachweis dieses Slice misst nicht den Betriebsfall. | `_current_components()` entfällt den Parameter `inline_evidence_chars` und verwendet den produktiven Standard; der Test verlangt zusätzlich, dass die aktuelle Komponentenmenge für `codex_implementation` und `codex_correction` exakt `{"stdin_prompt", "response_schema"}` ist, dass die Bilanz aus entfernten, hinzugefügten und unveränderten Komponenten weiterhin aufgeht, dass `response_schema` namens- und digestgleich bleibt und dass die Zeichen- und Bytereduktion gegenüber der eingefrorenen Baseline positiv ist. Ein zusätzlicher Fall darf die Assetzustellung weiterhin mit explizit gesetzter Inline-Grenze abdecken, muss sie aber als solche kennzeichnen und nicht als Betriebsnachweis führen.
FINDING_STATUS: C-08 | CLOSED | Die drei in Slice 02 zusätzlich berührten Testdateien `tests/test_quota_wait.py`, `tests/test_workflow_state.py` und `tests/test_structured_artifact_regressions.py` sind im von mir freigegebenen Commit `2e31b41` enthalten und dort als erzwungene Signaturfolgen der entfernten Text- und Reparaturpfade nachvollziehbar. Slice 03 fasst sie nachweislich nicht an — `git diff 2e31b41` zeigt für alle drei keine Änderung — und der Slice-03-Bericht weist die Abweichung ausdrücklich aus, statt sie zu verschleiern. Meine Observation zielte auf Sichtbarkeit vor der branchweiten Freigabe, nicht auf einen Korrektheitsmangel; diese Sichtbarkeit ist jetzt hergestellt.

REVIEW_EVIDENCE: Geschwisterdichtheit des Slice-Pakets am vollständigen Request mit vier Sentineln gegengeprüft und `distilled_plan` als feste Anweisung statt Plantext verifiziert; Paketform je Operation gegen die Ladebedingung für `approved_plan_text` und den Finalbericht-Work-Context mit vollständigem Branchdiff geprüft; doppelte Fail-closed-Auswahl der betroffenen offenen Findings samt Übernahme in `previous_findings`; gemeinsame Herkunft von Korrekturdelta und Korrekturfingerprint aus einem `collect_changes()`-Aufruf und identische Bindung in Request und Paket; Rebuild des gesamten Requests nach abweichendem autoritativem Finding-Snapshot hinter der Record-ahead-Grenze; sechs Deduplizierungsumgehungen einzeln versucht, darunter Reihenfolge, fremde Evidenz-ID, doppelter Quellpfad, Assetzustellung, Unicode-Normalform und Providerkomponenten; Bytegleichheit von Fixture und Lock gegenüber `fc978d8` sowie selbst berechnete Digests und Lock-zu-Datei-Konsistenz; eigenständige Nachrechnung der Komponentenbilanz in Test- und Produktionsform für beide Operationen; Projektion von Inputzeichen, -bytes, Laufzeit, Attemptanzahl, Retrystatus und Usage einschließlich der neuen `unknown`-Semantik; Unversehrtheit von Rohantwortpersistenz, Replay, Request-ID und Writerschemadigest; Scopeabgleich aller dreizehn geänderten und zwei neuen Pfade gegen die Slice-3-Pfadliste ohne Abweichung; `git diff --check` sauber; fokussierte Gegenprobe `80 passed` | Die Bilanzinvariante ist konstruktionsbedingt erfüllt, sobald entfernt, hinzugefügt und unverändert eine vollständige Partition bilden; der eigentliche Beweiswert hängt deshalb allein an der eingefrorenen Baseline und an der Frage, ob die verglichene Requestform die produktive ist — genau dort setzt `C-09` an | Eine spätere Änderung an `DEFAULT_INLINE_EVIDENCE_CHARS` oder am Umfang eines Ausführungspakets verschiebt die Zustellungsform von inline auf Asset, ohne dass ein Test das bemerkt, weil die Gegenprobe die Grenze bereits fest vorgibt; die zertifizierte Reduktion und der Betriebsfall driften dann unbemerkt auseinander

PRE_MORTEM: In drei Monaten ist die wahrscheinlichste Fehlerursache die Inline-Grenze. Die Ausführungspakete sind heute mit rund fünfhundert Zeichen sehr klein und liegen zwei Größenordnungen unter `DEFAULT_INLINE_EVIDENCE_CHARS`; sie werden deshalb inline geführt, und die gesamte Betriebswirkung steckt in einem einzigen `stdin_prompt`. Sobald ein Paket wächst — ein Slice mit vielen Akzeptanzkriterien, ein Korrekturdelta über mehrere Dateien, ein zusätzliches Querverweisfeld —, kippt es ohne jede Codeänderung in die Assetzustellung. Die Komponentenmenge ändert sich dann von zwei auf drei oder vier, die Bilanz gegen die eingefrorene Baseline verschiebt sich, und weil die vorhandene Gegenprobe die Grenze fest auf zehn Zeichen setzt, misst sie diesen Umschlag nicht mit. Der Test bleibt grün, während die zertifizierte Zahl und der Betriebsfall auseinanderlaufen. Die zweite, leisere Variante betrifft die Korrekturauswahl: `state.current_work_unit.open_findings` ist die einzige Quelle der betroffenen Menge; wer diese Liste später um geschlossene oder fremde IDs erweitert, bekommt keinen stillen Fehler, sondern den harten Abbruch „lacks its exact affected open finding set" — laut genug, aber an einer Stelle, die niemand mit der Evidenzminimierung in Verbindung bringt.
SLICE_APPROVAL: 03 | YES
STATUS: DONE
