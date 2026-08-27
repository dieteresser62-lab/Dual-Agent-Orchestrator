# Arbeitsplan: Erfolgreichen direkten Resume-Abschluss in die Watch-Outbox übernehmen

TARGET_BRANCH: feature/resume-success-outbox-finalization

## Ziel und Zuschnitt

Ein ausdrücklich mit `--resume --task-file ...` fortgesetzter Lauf darf einen
Watch-originären Inbox-Task nach echtem terminalem Workflow-Erfolg genauso
abschließen wie der Watcher selbst. Dafür wird die bestehende Queue-Semantik in
eine einzige wiederverwendbare Erfolgsabschlussroutine gezogen und sowohl vom
Watcher als auch vom direkten Resume verwendet. Die Routine bindet ihre
persistierte Erfolgsevidenz an Watch-Run, Taskdigest, Protokollmodus,
Quell-Inbox und das einmal reservierte Done-Ziel. Sie kann dadurch nach einem
Abbruch vor oder nach dem Move ausschließlich das Queue-Bookkeeping fortsetzen,
ohne Workflow, Agent oder Provider erneut aufzurufen.

Die Bestandsaufnahme ergibt einen fachlich geschlossenen Implementierungsslice.
Agentenverträge, Reviewregeln, Provideradapter, Workflowentscheidungen,
Recordketten und State-v3-Formate bleiben unverändert. Diese PLAN_ONLY-Runde
ändert ausschließlich dieses Dokument; Branch-, Stage- und Commitoperationen
bleiben beim Orchestrator beziehungsweise Nutzer.

## Repositorybefund und bestehende Integrationskanten

- `src/inbox_watcher.py::watch_inbox()` ist heute alleiniger Eigentümer des
  Queue-Erfolgsabschlusses. Nach einem `WatchTaskDisposition.COMPLETED` schreibt
  es `<task>.success`, ruft `move_to_outbox(task, outbox/done)` auf und löscht
  danach Attempt-, Success- und Watch-Identity-Sidecar. Bei vorhandenem
  Success-Marker überspringt es `process_task()` und versucht nur den Move
  erneut. Diese Reihenfolge ist die vorhandene Idempotenzkante, liegt aber als
  Inline-Ablauf im Watch-Loop und ist deshalb für einen direkten Resume nicht
  aufrufbar.
- `src/inbox_watcher.py::build_outbox_destination()` erzeugt bereits das
  UTC-Millisekunden-Schema und löst Namenskollisionen mit einem numerischen
  Suffix auf. `move_to_outbox()` ist der kanonische Move. Attempt-, Success- und
  Watch-Identity-Pfade sowie deren Löschfunktionen liegen ebenfalls in dieser
  Datei. Die neue Lösung muss diese Primitiven kapseln und darf weder Naming
  noch Move oder Bereinigung in CLI/Orchestrator nachbauen.
- Der heutige Success-Marker enthält nur einen Zeitstempel. Das reicht dem
  seriellen Watch-Loop als Re-Execution-Sperre, beweist für einen unabhängigen
  direkten Prozess aber weder Run-, Task- noch Zielidentität. Außerdem kennt er
  nach einem erfolgreichen Move den Zielpfad nicht. Damit kann ein Neustart an
  der Grenze „Move erfolgt, Sidecars noch vorhanden“ nicht sicher erkennen, ob
  er nur bereinigen darf. Für den neuen Pfad braucht der Marker eine streng
  validierte, atomar geschriebene Queue-Erfolgsevidenz; vorhandene einfache
  Marker dürfen dem direkten Resume keine Autorisierung geben.
- `WatchTaskIdentity` bindet bereits `run_id`, SHA-256 des unveränderten
  Taskdokuments, `started` und bei v2 den `structured-v2`-Protokollmodus. Der
  Watcher vergleicht ein typisiertes `WatchTaskResult` mit dieser Identität,
  bevor er Erfolg behandelt. Genau diese Bindung muss der direkte Resume
  fail-closed laden und zusätzlich mit `WorkflowRunResult.state.run_id`,
  `state.task_file`, `state.task_digest` und dem effektiven Protokollmodus
  abgleichen.
- `src/workflow.py::WorkflowRunResult.workflow_completed` ist der vorhandene
  strenge terminale Erfolgsnachweis: aktuelle Work-Unit abgeschlossen,
  `WorkflowStep.COMPLETED`, alle Slices abgeschlossen und entweder finales
  Review oder abgeschlossener `PLAN_ONLY`-Lauf. Ein bloßer Exitcode 0,
  Providererfolg oder abgeschlossene Zwischen-Work-Unit erfüllt ihn nicht.
- `src/orchestrator.py::run_pipeline()` erhält das vollständige
  `WorkflowRunResult`, reduziert einen normalen direkten Lauf anschließend aber
  auf einen Exitcode. Nur an dieser Kante kann ein erfolgreicher direkter Resume
  den terminalen Workflowzustand prüfen, bevor die gemeinsame Queue-Routine
  aufgerufen wird. `run_production_workflow()` erzwingt bei Resume bereits
  Taskpfad, Taskdigest, Taskvertrag und optional `watch_run_id`; ein direkter
  Resume setzt `watch_run_id` heute jedoch nicht aus dem Sidecar und erreicht
  deshalb die Watch-Abschlusslogik nicht.
- `src/cli.py::parse_args()` unterscheidet nach der Defaultauflösung nicht mehr
  zuverlässig, ob `--resume` und `--task-file` ausdrücklich angegeben wurden:
  `args.resume` kann aus dem vorhandenen State automatisch gesetzt werden und
  `args.task_file` erhält sonst `task.md`. Für die verbindliche Sicherheitsgrenze
  muss die CLI die explizite Herkunft beider Optionen vor der Normalisierung als
  interne Flags erhalten. Der positionale Kompatibilitätspfad oder Auto-Resume
  darf die Queue-Finalisierung nicht aktivieren.
- `src/cli.py::run_cli()` löst das Taskdokument vor `run_pipeline()` mit
  `find_task_file()` auf. Nach einem Crash unmittelbar nach dem Move existiert
  der ausdrücklich angegebene Inbox-Pfad nicht mehr, obwohl die gebundene
  Erfolgsevidenz und die noch zu löschenden Sidecars dort liegen. Deshalb muss
  der CLI-/Queue-Einstieg den belegten Bookkeeping-only-Fall vor dem normalen
  Existenzcheck erkennen können; alle anderen fehlenden Dateien bleiben Fehler.
- `tests/test_inbox_watcher.py` deckt UTC-/Kollisionsnamen,
  Move-Wiederholung ohne Re-Execution, Retry/Poison/Move-Error und
  Sidecar-Löschung bereits isoliert ab. `tests/test_cli.py` ist die passende
  Grenze für explizite versus abgeleitete Flags und den fehlenden Quellpfad bei
  Bookkeeping-Recovery. `tests/test_orchestrator_watch_cli.py` prüft die
  Abbildung von `WorkflowRunResult` auf direkte und Watch-Ergebnisse.
  `tests/test_orchestrator_runtime.py` besitzt providerfreie temporäre
  Repositories, persistierte State-v3-Läufe und Fake-Agenten und ist damit der
  geeignete Ort für den realistischen Watch-Halt-zu-direktem-Resume-Durchstich.

## Zielinvarianten und Abschlussprotokoll

1. Queue-Autofinalisierung ist nur aktiv, wenn die CLI-Herkunftsflags ein
   ausdrücklich gesetztes `--resume` und ein ausdrücklich gesetztes
   `--task-file` belegen. `--watch`, Auto-Resume, `--no-resume`, der positionale
   Taskpfad und normale direkte Aufrufe bleiben unverändert.
2. Der konfigurierte Inbox-Pfad wird relativ zum Repositoryroot oder als
   absoluter Pfad genauso aufgelöst wie im Watchbetrieb. Der erwartete
   Taskpfad muss ein unmittelbarer regulärer `*.md`-Eintrag dieser Inbox sein;
   Symlinks, `..`-Ausbruch, gleichnamige Dateien außerhalb der Inbox und
   abweichende konfigurierte Verzeichnisse autorisieren nichts.
3. Vor einem Workflowaufruf lädt der direkte Resume die vorhandene
   Watch-Identität read-only und bindet daraus `watch_run_id`, aber nur wenn
   Sidecarschema, `started`, Taskdigest, Protokollmodus und persistierter
   State-v3-/Record-Resume eindeutig übereinstimmen. Fehlt oder widerspricht
   eine Bindung, läuft ein existierender gewöhnlicher direkter Task ohne
   Queue-Nebenwirkung weiter beziehungsweise hält ein behaupteter
   Watch-Recovery-Fall fail-closed; es wird nie eine neue Watch-Identität
   erzeugt.
4. Erst `WorkflowRunResult.workflow_completed` für exakt denselben Run darf die
   gebundene Queue-Erfolgsevidenz veröffentlichen. Nichtterminale Ergebnisse,
   Gates, Quota, Bootstrap-/Retry-Halte, Exceptions und technische Fehler
   schreiben keinen Erfolgsnachweis und bewegen nichts.
5. Die Queue-Erfolgsevidenz wird atomar vor dem Move geschrieben und enthält
   mindestens Formatversion, Run-ID, Taskdigest, Protokollmodus, kanonische
   Inbox-Quelle und den einmal über `build_outbox_destination()` reservierten
   Done-Zielpfad. Beim erneuten Laden werden geschlossenes Schema, kanonische
   Pfade, konfigurierte Inbox/Outbox, Watch-Identität und vorhandene
   Datei-Digests erneut geprüft. Der bisherige reine Zeitstempelmarker kann im
   Watch-Kompatibilitätspfad weiterbehandelt werden, berechtigt aber niemals
   den neuen direkten Resume.
6. Eine gemeinsame Routine besitzt die Reihenfolge Erfolgsevidenz
   veröffentlichen, kanonischen Move durchführen oder den bereits erfolgten
   Move am gebundenen Ziel verifizieren, danach Attempt-Sidecar, Success-Marker
   und Watch-Identity in der bestehenden sicheren Reihenfolge entfernen. Der
   Watcher ruft dieselbe Routine auf; `src/cli.py` und `src/orchestrator.py`
   enthalten weder eigene Zielnamensbildung noch eigene Move-/Unlink-Sequenz.
7. Nach „Workflow-Erfolg vor Marker“ darf ein erneuter Resume den bereits
   terminalen persistierten Lauf nur bis zur lokalen Ergebnisauswertung laden;
   kein Agent oder Provider wird erneut aufgerufen. Nach „Marker vor Move“ wird
   der Workflow vollständig übersprungen. Nach „Move vor Bereinigung“ belegt
   ausschließlich die Kombination aus gebundenem Marker, fehlender Quelle und
   digestgleichem reserviertem Ziel den Bookkeeping-only-Fortgang.
8. Existieren Quelle und reserviertes Ziel gleichzeitig, fehlen beide, weicht
   der Zieldigest ab oder passt irgendeine Run-/Task-/Protokoll-/Pfadbindung
   nicht, endet die Recovery fail-closed und behält alle noch vorhandene
   Evidenz. Ein Move- oder einzelner Bereinigungsfehler lässt genügend Marker-
   und Identity-Daten für einen idempotenten Retry stehen; fachliche Ausführung
   wird nach veröffentlichter Erfolgsevidenz nie wieder betreten.
9. FIFO, technische Attemptzählung, Poison-/Move-Error-Verhalten,
   Watch-Resumable-Halts, Kollisionsschema und erzeugte PLAN_ONLY-Handoffs
   bleiben semantisch unverändert. Insbesondere darf der neue direkte Pfad
   keine Watch-Schleife starten und keine Retrygrenze eigenständig als Poison
   behandeln.

### Slice 1 - Gebundene, gemeinsam genutzte Queue-Finalisierung für direkten Resume

#### Ziel

Der Watcher und ein expliziter direkter Resume verwenden denselben
crash-sicheren Queue-Erfolgsabschluss. Der direkte Pfad wird ausschließlich
durch eine gültige Watch-Identität und einen terminalen Erfolg desselben
persistierten Laufs autorisiert; an allen Wiederanlaufgrenzen bleibt die
Ausführung providerfrei und idempotent.

**Exakter Änderungspfad**

- `src/inbox_watcher.py`
- `src/cli.py`
- `src/orchestrator.py`
- `tests/test_inbox_watcher.py`
- `tests/test_cli.py`
- `tests/test_orchestrator_watch_cli.py`
- `tests/test_orchestrator_runtime.py`

#### Umsetzung

1. Führe in `src/inbox_watcher.py` ein geschlossenes, versioniertes Modell für
   die gebundene Queue-Erfolgsevidenz sowie strikt lesende Hilfen für eine
   vorhandene Watch-Identität ein. Schreiben und Laden verwenden
   `atomic_write_file`, validieren Run, Digest, Protokoll und kanonische
   Source-/Destination-Grenzen und erzeugen niemals implizit eine Identität.
2. Extrahiere den erfolgreichen Done-Abschluss aus `watch_inbox()` in eine
   gemeinsame Routine. Sie reserviert ihr Ziel genau einmal mit
   `build_outbox_destination()`, verwendet den vorhandenen kanonischen Move,
   erkennt einen bereits am gebundenen Ziel liegenden digestgleichen Task und
   bereinigt Sidecars erst danach in der bestehenden Reihenfolge. Liefere ein
   typisiertes Ergebnis für „nicht anwendbar“, „vollständig abgeschlossen“ und
   „fail-closed/technischer Queue-Fehler“, damit Aufrufer weder einen Fehler als
   Erfolg noch Nichtanwendbarkeit als Watch-Herkunft deuten.
3. Stelle den Watch-Erfolg auf diese Routine um, einschließlich bestehender
   Marker-only-Retries und Move-Error-Semantik. Behalte Attempt-, Poison-,
   Retry-, FIFO- und Locklogik unverändert und ergänze Regressionen, dass der
   refaktorierte Watch-Pfad dieselben Namen, Kollisionen und Bereinigungen
   liefert.
4. Erfasse in `src/cli.py` vor der Argumentnormalisierung, ob `--resume` und
   `--task-file` ausdrücklich gesetzt wurden. Begrenze die neue Vorab-Recovery
   auf genau diese Kombination. Erlaube nur dann einen fehlenden Quellpfad, wenn
   die gemeinsame Routine anhand des gebundenen Success-Markers den bereits
   erfolgten Done-Move verifiziert und lediglich Sidecars abschließt; andernfalls
   bleibt `find_task_file()` unverändert streng.
5. Lasse `src/orchestrator.py::run_pipeline()` bei einem autorisierten
   Watch-originären direkten Resume vor dem Workflow einen vorhandenen
   gebundenen Success-Marker abarbeiten. Ist nur die Watch-Identität vorhanden,
   setze für die bestehende Resumevalidierung die gebundene `watch_run_id`,
   führe denselben persistierten Workflow aus und übergib ausschließlich bei
   `result.workflow_completed` die bereits validierten Run-/Taskdaten an die
   gemeinsame Abschlussroutine. Entferne die temporäre direkte Watch-Bindung
   nicht vor erfolgreichem Queueabschluss und bilde Queuefehler auf einen
   eindeutigen Nichtnull-Exit ab, ohne den fachlichen Erfolg zu leugnen oder
   einen Providerretry anzustoßen.
6. Halte State-v3 und Recordkette read-only an ihren vorhandenen Loadern und
   Completion-Prädikaten. Es entstehen keine neuen Workflowrecords,
   Checkpointmutationen, Providerflags oder fachlichen Erfolgsdefinitionen für
   Queuezwecke.

#### Akzeptanzkriterien

Die folgenden providerfreien Tests sind für die Abnahme dieses Slices
verbindlich:

- `tests/test_orchestrator_runtime.py`: Ein temporärer Watch-originärer
  Inbox-Task startet mit stabiler Watch-Identität, erreicht mit Fake-Agenten
  einen persistierten resumierbaren Halt und wird über den ausdrücklich
  geparsten direkten Aufruf `--resume --task-file` bis zu demselben terminalen
  Run fortgesetzt. Danach existiert sein Inhalt genau einmal unter dem
  konfigurierten `outbox/done`, und Attempt-, Success- sowie Identity-Sidecar
  fehlen.
- `tests/test_orchestrator_runtime.py`: Zähler um sämtliche Fake-Agent-/Provider-
  Einstiege beweisen für die Grenzen (a) terminaler Workflow vor Marker,
  (b) gebundener Marker vor Move und (c) Move vor Sidecar-Bereinigung, dass ein
  erneuter direkter Resume höchstens die noch offene Queuephase ausführt. Ein
  anschließender Watch-Start verarbeitet denselben Task ebenfalls nicht erneut.
- `tests/test_inbox_watcher.py`: Feste Zeit und vorbesetztes Ziel belegen die
  unveränderte UTC-/Kollisionsbenennung. Parametrisierte Unterbrechungen nach
  Marker, nach Move und zwischen den einzelnen Sidecar-Löschungen konvergieren
  auf genau eine Done-Datei. Move- und Unlink-Fehler behalten die zur sicheren
  Wiederholung nötige Evidenz; Quelle oder gebundenes Ziel gehen nie verloren.
- `tests/test_inbox_watcher.py`: Manipulierte Markerfelder, geänderter
  Taskinhalt, falsche Run-ID, falscher Protokollmodus, Quelle außerhalb der
  konfigurierten Inbox, Ziel außerhalb von `outbox/done`, Symlink sowie die
  Zustände „Quelle und Ziel vorhanden“, „beide fehlen“ und „Zieldigest falsch“
  scheitern vor Move und Bereinigung.
- `tests/test_cli.py`: Nur die wörtliche Kombination `--resume --task-file`
  setzt beide Herkunftsflags. Auto-Resume, Default-`task.md`, positionaler Pfad,
  `--no-resume` und Watchmodus aktivieren die Finalisierung nicht. Ein fehlender
  Inbox-Quellpfad passiert den CLI-Einstieg ausschließlich bei erfolgreich
  verifizierter Post-Move-Recovery.
- `tests/test_orchestrator_watch_cli.py`: Ein gewöhnlicher direkter Task ohne
  Watch-Identity bleibt am Ort; ein nichtterminaler `WorkflowRunResult`, Gate,
  Quota-/Bootstrap-Halt und technischer Fehler erzeugen weder Marker noch Move.
  Ein terminales Ergebnis mit abweichender Run-, Task- oder Protokollidentität
  endet fail-closed.
- Alle Tests verwenden temporäre Verzeichnisse, synthetische Zustände,
  Fake-Agenten und monkeypatchbare Queueprimitiven. Sie starten keine echten
  Provider, benötigen kein Netz und verändern weder echte State-v3-Dateien noch
  die Recordkette des laufenden Orchestrators.

#### Fokussierte Validierung und Übergabe

- Während der Implementierung:
  `python3 -m pytest tests/test_inbox_watcher.py tests/test_cli.py tests/test_orchestrator_watch_cli.py tests/test_orchestrator_runtime.py -v`
- Zusätzlich `git diff --check` für den Slice-Diff.
- Da Watch-, CLI- und Orchestrierungslogik geändert werden, führt ausschließlich
  der Orchestrator nach dem Implementierungsreview die konfigurierte vollständige
  Matrix `python3 -m pytest tests/ -v` aus und bindet deren Attestierung an den
  geprüften Diff-Fingerprint. Der implementierende Agent beansprucht diese
  Attestierung nicht.

## Risiken und Rückfallgrenzen

- Das größte Sicherheitsrisiko ist ein Marker, der als fachlicher
  Erfolgsnachweis missverstanden wird. Deshalb darf er nur nach dem vorhandenen
  terminalen `workflow_completed`-Prädikat entstehen und muss bei jeder
  Recovery erneut gegen Watch-Identity, State und Dateien validiert werden.
- Das größte Crashrisiko ist ein nicht rekonstruierbares, bei jedem Retry neu
  gewähltes Kollisionsziel. Die einmalige Zielreservierung im gebundenen Marker
  macht Quelle-versus-Ziel nach dem Move eindeutig, ohne ein zweites
  Namensschema einzuführen.
- Das größte Kompatibilitätsrisiko ist eine Verhaltensänderung des bestehenden
  Watch-Loops. Die gemeinsame Routine bleibt deshalb in `inbox_watcher.py`, und
  die vorhandenen Watch-Retry-/Poison-Tests werden als Regression beibehalten.
  Falls die strenge Bindung im Implementierungsbefund nicht ohne konkurrierende
  Queue-Semantik erreichbar ist, gilt die vorgegebene Stopbedingung; ein
  permissiver Fallback oder eine zweite Move-Implementierung ist keine
  Rückfalloption.

## Orchestrator-Prüfprotokoll

### Review-Feedback von Claude

<!-- audit:claude-review:begin -->
### Claude · Runde 1 · approved (Ereignis 2)

- Reviewer: `claude`
- Freigabe: `YES`
- Validierungsbindung: `plan-validation-0114ae07a318`
- Testdateien: keine
- Prüfdimensionen: Verified plan-document contract compliance: exactly one Slice with a contiguous '### Slice N<br>- title' heading, a standalone canonical '**Exakter Änderungspfad**' heading followed only by a bullet list of repository-relative paths, and PLAN_ONLY scope containing only future implementation prose (no executable SLICE_PLAN records beyond the plan artifact).<br>Verified the diff touches only the authorized path docs/internal/resume-abschluss-verschiebt-task-in-outbox-arbeitsplan.md and no product code, tests, or config.<br>Verified fingerprint binding: current_fingerprint equals validation_attestation.diff_fingerprint (0114ae07a318), the attestation record is PASS/complete, and the single expected command internal:work-plan-contract matches command_specs/expected_commands.<br>Assessed technical grounding of the described resume/idempotency design against the German assignment's mandatory security boundaries: explicit (non-derived) --resume/--task-file flags, read-only watch-identity binding to run/task-digest/protocol before any workflow call, gating success-evidence publication strictly on WorkflowRunResult.workflow_completed for the identical run, atomic bound evidence written before the canonical move, ordered sidecar cleanup only after a verified move, and fail-closed behavior on any identity/path/digest mismatch or when source and reserved destination states are ambiguous.<br>Confirmed non-scope items (agent contracts, review rules, provider adapters, workflow decisions, State-v3/record chain, archiving, push/merge/deploy) are explicitly preserved, and that stop conditions from the assignment are echoed in the plan's Risiken section as genuine fallback boundaries rather than being silently designed around.
- Größtes Restrisiko: The plan does not address mutual exclusion between an already-running watcher loop and a manually invoked direct resume targeting the same watch-originated task; without citing or requiring an existing single-writer guard, the implementation Slice could leave a narrow window where both paths pass identity validation and interleave move/cleanup operations before the strict repeated-recovery checks in item 8 of the Zielinvarianten section would catch the divergence.
- Realistische Bruchbedingung: If the Slice 1 implementation's shared finalization routine does not enforce or reuse a single-authoritative-writer guarantee (existing watch lock/instance guard or an equivalent explicit claim step) before publishing bound success evidence, a concurrent watcher and direct resume against the same task could race on the move or sidecar deletion, breaking the idempotency invariant this plan otherwise establishes for sequential crash points.
- Eigene Findings: `C-01`

<!-- artifact-records:claude-review:begin -->
Semantischer Record-Digest: `23a011b7fc25`

### Claude · Runde – · approved

| Seq/Record | Rolle | Runde | Status | Work-Unit | Findings | Fingerprint | Transport | Request | Response |
|---|---|---:|---|---|---|---|---|---|---|
| 13. `ar1-7b0344e64ceb` | `claude` | `–` | `approved` | `1` | `C-01` | `0114ae07a318` | `native-claude-review-v2` | `native-review-request-002af41d7c2f` | `8c15612c710d` |
<!-- artifact-records:claude-review:end -->
<!-- audit:claude-review:end -->

### Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
Noch keine strukturierten Codex-Antworten.

<!-- artifact-records:codex-responses:begin -->
Semantischer Record-Digest: `23a011b7fc25`

Keine Codex-Findingantworten.
<!-- artifact-records:codex-responses:end -->
<!-- audit:codex-responses:end -->

### Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### Ereignis 1: `plan-validation-0114ae07a318`

- Diff-Fingerprint: `0114ae07a318`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: internal plan contract passed
- Ausgabedigest: `08d98711b560`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| internal:work-plan-contract | PASS | 0 | slices=1; planned_paths=1; changed_paths=1; future_slices=1; work_plan=docs/internal/resume-abschluss-verschiebt-task-in-outbox-arbeitsplan.md |

<!-- artifact-records:validation-attestation:begin -->
Semantischer Record-Digest: `23a011b7fc25`

- 2. `ar1-cdd4dd4cf359`: Providerinput `codex/codex_plan` = `allowed`; local_input_chars `21382/4000000`, local_input_bytes `21448/16000000`; local_input_digest `2f7090bd4fa9`, Policy `9edf600f09ac`, Übergang `ceee8e15ae76`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, local_input_largest_component `stdin_prompt`; local_input_component_count `2`; Komponenten `stdin_prompt=17593/17659, response_schema=3789/3789`
- 6. `ar1-d9b0e237cf27`: Providerinput `codex/codex_plan_revision` = `allowed`; local_input_chars `21950/4000000`, local_input_bytes `22017/16000000`; local_input_digest `28acad38454b`, Policy `9edf600f09ac`, Übergang `f7276497b354`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, local_input_largest_component `stdin_prompt`; local_input_component_count `2`; Komponenten `stdin_prompt=18161/18228, response_schema=3789/3789`
### Validierungsattestierung

| Seq/Record | Rolle | Fingerprint |
|---|---|---|
| 10. `ar1-0321fc362941` | `orchestrator` | `0114ae07a318` |

| Status | Exit | Output-Digest | Befehl mit argv-Grenzen |
|---|---:|---|---|
| `pass` | `0` | `08d98711b560` | `argv` [`internal:work-plan-contract`] |
- 11. `ar1-005626f62eda`: Providerinput `claude/claude_plan_review` = `allowed`; local_input_chars `58968/4000000`, local_input_bytes `59269/16000000`; local_input_digest `0ae4d25ba77a`, Policy `9edf600f09ac`, Übergang `df1ea161f6c1`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, local_input_largest_component `evidence_asset_001`; local_input_component_count `6`; Komponenten `request_chunk_001=18714/18780, evidence_asset_001=28914/29149, packet_manifest=610/610, system_policy=217/217, response_schema=10283/10283, start_directive=230/230`
- Providerattempt-Summe Run `watch-20260826-201852.565634Z-f9383964c762` / Operation `provider-operation-956621423860` (`codex/codex_plan`; Modell `gpt-5.6-sol`; Effort `medium`): Attempts `1`, offen `0`, Duration `226.869336` (bekannt `1`, unbekannt `0`); Inputzeichen `21382`, Inputbytes `21448`; Retrystatus `single-attempt`; input_tokens=sum:unknown,known:0,unknown:1; tool_input_tokens=sum:unknown,known:0,unknown:1; cache_read_input_tokens=sum:unknown,known:0,unknown:1; cache_creation_input_tokens=sum:unknown,known:0,unknown:1; thinking_tokens=sum:unknown,known:0,unknown:1; output_tokens=sum:unknown,known:0,unknown:1; total_tokens=sum:unknown,known:0,unknown:1; turns=sum:unknown,known:0,unknown:1; cost_usd=sum:unknown,known:0,unknown:1
  - 5. `ar1-d33f4feb40a8`: Attempt `1` = `succeeded`; Messung `ar1-cdd4dd4cf359`; Modell `gpt-5.6-sol`; Effort `medium`; Inputzeichen `21382`; Inputbytes `21448`; Duration `226.86933636898175`; Fehler `none`; Usage `unknown`
- Providerattempt-Summe Run `watch-20260826-201852.565634Z-f9383964c762` / Operation `provider-operation-d96561376f0c` (`claude/claude_plan_review`; Modell `sonnet`; Effort `high`): Attempts `1`, offen `0`, Duration `109.337287` (bekannt `1`, unbekannt `0`); Inputzeichen `58968`, Inputbytes `59269`; Retrystatus `single-attempt`; input_tokens=sum:8,known:1,unknown:0; tool_input_tokens=sum:unknown,known:0,unknown:1; cache_read_input_tokens=sum:30426,known:1,unknown:0; cache_creation_input_tokens=sum:31595,known:1,unknown:0; thinking_tokens=sum:unknown,known:0,unknown:1; output_tokens=sum:9236,known:1,unknown:0; total_tokens=sum:unknown,known:0,unknown:1; turns=sum:5,known:1,unknown:0; cost_usd=sum:0.22586620000000002,known:1,unknown:0
  - 15. `ar1-99e9f03c2d62`: Attempt `1` = `succeeded`; Messung `ar1-005626f62eda`; Modell `sonnet`; Effort `high`; Inputzeichen `58968`; Inputbytes `59269`; Duration `109.33728694100864`; Fehler `none`; Usage `input_tokens=8, tool_input_tokens=unknown, cache_read_input_tokens=30426, cache_creation_input_tokens=31595, thinking_tokens=unknown, output_tokens=9236, total_tokens=unknown, turns=5, cost_usd=0.22586620000000002`
- Providerattempt-Summe Run `watch-20260826-201852.565634Z-f9383964c762` / Operation `provider-operation-dfa56037ccc8` (`codex/codex_plan_revision`; Modell `gpt-5.6-sol`; Effort `medium`): Attempts `1`, offen `0`, Duration `51.846575` (bekannt `1`, unbekannt `0`); Inputzeichen `21950`, Inputbytes `22017`; Retrystatus `single-attempt`; input_tokens=sum:unknown,known:0,unknown:1; tool_input_tokens=sum:unknown,known:0,unknown:1; cache_read_input_tokens=sum:unknown,known:0,unknown:1; cache_creation_input_tokens=sum:unknown,known:0,unknown:1; thinking_tokens=sum:unknown,known:0,unknown:1; output_tokens=sum:unknown,known:0,unknown:1; total_tokens=sum:unknown,known:0,unknown:1; turns=sum:unknown,known:0,unknown:1; cost_usd=sum:unknown,known:0,unknown:1
  - 9. `ar1-4cf37521df8d`: Attempt `1` = `succeeded`; Messung `ar1-d9b0e237cf27`; Modell `gpt-5.6-sol`; Effort `medium`; Inputzeichen `21950`; Inputbytes `22017`; Duration `51.846574584022164`; Fehler `none`; Usage `unknown`
<!-- artifact-records:validation-attestation:end -->
<!-- audit:validation-attestation:end -->

### Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
- Teständerungsfreigabe: nicht erfasst.
- Pre-Mortems:
  - Ereignis 2: If this plan is approved and implemented literally, the most probable gap is the concurrency race described in C-01 going unaddressed, since the Repositorybefund section cites no existing cross-process lock and the acceptance criteria only test sequential crash points (workflow-before-marker, marker-before-move, move-before-cleanup) rather than two processes racing at the same instant.<br>A secondary risk is the coexistence of two success-marker shapes (the pre-existing plain timestamp marker used by the watch-compatibility path versus the new closed, versioned bound evidence required for direct resume): Umsetzung step 3 migrates the watch success path onto the same shared routine, which should unify this, but the plan leaves the exact reconciliation with already-persisted legacy markers from in-flight watch runs to the implementation Slice rather than specifying it here.<br>Neither gap blocks approving this planning artifact itself, since the plan correctly scopes a single Slice, stays within the authorized documentation path, binds to the reviewed fingerprint, and preserves every stated non-scope and stop condition; both points are recorded as an open OBSERVATION and residual risk for the implementation Slice and its review to close or escalate.

<!-- artifact-records:test-approval-premortem:begin -->
Semantischer Record-Digest: `23a011b7fc25`

Keine strukturierten Gates.
<!-- artifact-records:test-approval-premortem:end -->
<!-- audit:test-approval-premortem:end -->

### Findings-Lebenszyklus

<!-- audit:findings:begin -->
### `C-01` — `OPEN`

- Quelle: `claude`; Runde 1
- Klasse: `OBSERVATION`
- Finding: The plan's crash/idempotency protocol thoroughly covers sequential single-process failure boundaries (workflow success before marker, marker before move, move before sidecar cleanup, repeated resume/watch after each) but does not describe how the shared finalization routine prevents a race if a live watcher loop and a manually invoked direct &#96;--resume --task-file&#96; process target the same watch-originated task concurrently.<br>Collision-safe destination naming avoids duplicate done-file content, but nothing in the Repositorybefund or Slice 1 steps cites an existing single-writer guard (lock/instance check) that the direct path must respect before publishing bound success evidence or performing the move/sidecar cleanup.<br>Without an explicit guard, two processes could both observe the source present, both pass identity validation, and interleave move/unlink operations.
- Akzeptanztest: The Slice 1 implementation or its test suite documents/demonstrates that the shared queue-finalization routine cannot be entered twice concurrently for the same task: e.g.<br>a test where a simulated in-flight watcher (holding whatever existing single-instance guard the watch loop relies on) causes a concurrently invoked direct resume against the same watch-originated task to fail closed or safely no-op rather than racing on the move or sidecar cleanup.
- Statusbegründung: –

<!-- artifact-records:findings:begin -->
Semantischer Record-Digest: `23a011b7fc25`

### Finding-Ereignisse

| Seq/Record | Finding | Rolle | Runde | Aktion | Klasse | Status | Begründung |
|---|---|---|---:|---|---|---|---|
| 14. `ar1-4287e34c036e` | `C-01` | `claude` | `–` | `opened` | `OBSERVATION` | `open` | The plan's crash/idempotency protocol thoroughly covers sequential single-process failure boundaries (workflow success before marker, marker before move, move before sidecar cleanup, repeated resume/watch after each) but does not describe how the shared finalization routine prevents a race if a live watcher loop and a manually invoked direct &#96;--resume --task-file&#96; process target the same watch-originated task concurrently.<br>Collision-safe destination naming avoids duplicate done-file content, but nothing in the Repositorybefund or Slice 1 steps cites an existing single-writer guard (lock/instance check) that the direct path must respect before publishing bound success evidence or performing the move/sidecar cleanup.<br>Without an explicit guard, two processes could both observe the source present, both pass identity validation, and interleave move/unlink operations. |

### Native convergence summary

| Finding | Work units | Rounds | Fingerprints | Claude decisions | Codex dispositions | Final status |
|---|---|---|---|---|---|---|
| `C-01` | `1` | `1` | `0114ae07a318` | `opened:open` | – | `open` |
<!-- artifact-records:findings:end -->
<!-- audit:findings:end -->

### Entscheidungstabelle

<!-- audit:decision-table:begin -->
| ID | Quelle | Finding | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|---|
| C-01 | claude | The plan's crash/idempotency protocol thoroughly covers sequential single-process failure boundaries (workflow success before marker, marker before move, move before sidecar cleanup, repeated resume/watch after each) but does not describe how the shared finalization routine prevents a race if a live watcher loop and a manually invoked direct &#96;--resume --task-file&#96; process target the same watch-originated task concurrently. Collision-safe destination naming avoids duplicate done-file content, but nothing in the Repositorybefund or Slice 1 steps cites an existing single-writer guard (lock/instance check) that the direct path must respect before publishing bound success evidence or performing the move/sidecar cleanup. Without an explicit guard, two processes could both observe the source present, both pass identity validation, and interleave move/unlink operations. | OBSERVATION | offen | offen |

<!-- artifact-records:decision-table:begin -->
Semantischer Record-Digest: `23a011b7fc25`

| Seq | Record | Typ | Status | Logische ID | Revision | Fingerprint |
|---:|---|---|---|---|---:|---|
| 1 | `ar1-7a155ab6f6a1` | `task` | `accepted` | `task-contract` | 1 | `contract:7ffa87c912aa` |
| 2 | `ar1-cdd4dd4cf359` | `provider_input_measurement` | `measured` | `provider-input-1-codex_plan` | 1 | `implementation:7ffa87c912aa` |
| 3 | `ar1-ecc83eab934b` | `provider_attempt` | `started` | `provider-operation-956621423860-1` | 1 | `implementation:7ffa87c912aa` |
| 4 | `ar1-15957546778d` | `agent_result` | `ready` | `agent-1-codex_plan-1` | 1 | `contract:7ffa87c912aa` |
| 5 | `ar1-d33f4feb40a8` | `provider_attempt` | `succeeded` | `provider-operation-956621423860-1` | 2 | `implementation:7ffa87c912aa` |
| 6 | `ar1-d9b0e237cf27` | `provider_input_measurement` | `measured` | `provider-input-1-codex_plan_revision` | 1 | `implementation:7ffa87c912aa` |
| 7 | `ar1-705cae881194` | `provider_attempt` | `started` | `provider-operation-dfa56037ccc8-1` | 1 | `implementation:7ffa87c912aa` |
| 8 | `ar1-9f09c054c59b` | `agent_result` | `ready` | `agent-1-codex_plan_revision-1` | 1 | `contract:7ffa87c912aa` |
| 9 | `ar1-4cf37521df8d` | `provider_attempt` | `succeeded` | `provider-operation-dfa56037ccc8-1` | 2 | `implementation:7ffa87c912aa` |
| 10 | `ar1-0321fc362941` | `validation_attestation` | `attested` | `plan-validation-0114ae07a318` | 1 | `implementation:0114ae07a318` |
| 11 | `ar1-005626f62eda` | `provider_input_measurement` | `measured` | `provider-input-1-claude_plan_review` | 1 | `implementation:7ffa87c912aa` |
| 12 | `ar1-a74a3e8c8166` | `provider_attempt` | `started` | `provider-operation-d96561376f0c-1` | 1 | `implementation:7ffa87c912aa` |
| 13 | `ar1-7b0344e64ceb` | `review` | `decided` | `review-claude-1-1` | 1 | `implementation:0114ae07a318` |
| 14 | `ar1-4287e34c036e` | `finding_transition` | `recorded` | `finding-C-01` | 1 | `implementation:0114ae07a318` |
| 15 | `ar1-99e9f03c2d62` | `provider_attempt` | `succeeded` | `provider-operation-d96561376f0c-1` | 2 | `implementation:7ffa87c912aa` |

### Nachweis vollständiger Bindungswerte

| Kurzreferenz | Vollwert | Feldarten |
|---|---|---|
| `0114ae07a318` | `0114ae07a3185d99a8549a53f5ff4618ed42c3cd1109a161e27ca35345d73a3e` | Technischer Wert, Fingerprint, Attestierungsreferenz, Record-ID |
| `23a011b7fc25` | `23a011b7fc25b8534cad9318e00bf42cbff1df9c260a96caffd40b07b6c38aa7` | Record-ID |
| `7b0344e64ceb` | `7b0344e64ceb7ad938281dc1abababcd4cfceccbabf008a6cfc3c558ec6c94e4` | Request-ID, Technischer Wert |
| `002af41d7c2f` | `002af41d7c2fa8308faf5644cd90c4e07c8da804e71dd802c306b037090be744` | Request-ID |
| `8c15612c710d` | `8c15612c710d0b89c5af8b06a8c2f5ba53e7dcacac2f5df9219abdc23a12254a` | Request-ID |
| `08d98711b560` | `08d98711b5604a0857d3d1370f0c5fc4b143e61338d325891f96ff524d21162f` | Output-Digest |
| `cdd4dd4cf359` | `cdd4dd4cf3594fd0cbaefe61db553823d54a1c648394ec605899dc4450662339` | Technischer Wert, Messungsreferenz |
| `2f7090bd4fa9` | `2f7090bd4fa9155dea74abe9cd6016edc8ed14380088ed83af36cf711a0fe26e` | Digest |
| `9edf600f09ac` | `9edf600f09ac1b36a30675cc91b1f6769c4545f8f200a4fba413b4b9a4c9bc1e` | Policy-Digest |
| `ceee8e15ae76` | `ceee8e15ae7660efab545caa89664cce45c5944ea9d8b210dfc15aaa3384d99d` | Übergangsfingerprint |
| `d9b0e237cf27` | `d9b0e237cf27da47527aa002ecf3c9262d26651e9953fa30ed741cc6706d820a` | Response-Digest, Messungsreferenz, Technischer Wert |
| `28acad38454b` | `28acad38454baefdd3aa8dc7ddcd2b5d1623e51d575caa90bf204946b43c8eb6` | Digest |
| `f7276497b354` | `f7276497b354c5b3aea5db5d6097c1701d24d6d1415029c999384bb5e2b5661b` | Übergangsfingerprint |
| `0321fc362941` | `0321fc362941ca70359f42bde21c23f395dd4340ec7ce8878f10c842183092e4` | Record-ID, Technischer Wert |
| `005626f62eda` | `005626f62eda764f47d14195be0feabfa67d4ef5feb0f5675f54c4536c042df5` | Technischer Wert, Messungsreferenz |
| `0ae4d25ba77a` | `0ae4d25ba77a877cd72fd6eea8ace00b4c7dc12c94379cc00359fad6160d058f` | Digest |
| `df1ea161f6c1` | `df1ea161f6c18189d4086fd06f8293d23095282ff7d39d253fc603d62303f162` | Übergangsfingerprint |
| `956621423860` | `956621423860a216ae6110aa3706beec3f644d94b5ec8e02323509c899c0f3dd` | Technischer Wert |
| `d33f4feb40a8` | `d33f4feb40a82b8927dede78014bcdc75f98008b0b58a29b770a822ccb45889b` | Technischer Wert |
| `d96561376f0c` | `d96561376f0c81f0b113cf55524dde627075f9f08f97eb3abca4017f38b1ad64` | Technischer Wert |
| `99e9f03c2d62` | `99e9f03c2d62f824681d7bb478e7ac31b9c569b36dbf7b7813c7fd8025c30477` | Technischer Wert |
| `dfa56037ccc8` | `dfa56037ccc8e0520437e328ee7a898b2ea46fab911e9ef6b5496f897ce972b0` | Technischer Wert |
| `4cf37521df8d` | `4cf37521df8d8ca98434c72b2e23bba3a29474f7f69c6786702b4758b2c7eeee` | Technischer Wert |
| `4287e34c036e` | `4287e34c036e5fc8424aac6a9111c4801309d96a88ac8ec6d9f03ac95cfde7b0` | Technischer Wert |
| `7a155ab6f6a1` | `7a155ab6f6a1338cce26c24e3b440755998316f699adde493b7d85ebc69bfc04` | Fingerprint |
| `7ffa87c912aa` | `7ffa87c912aa72f38df263c78316247fa92ae047475dca643e63e1a6007165ff` | Technischer Wert |
| `ecc83eab934b` | `ecc83eab934bcaba4a7a639d06ed86ed2ed2041eec7e5f66575b1243ec1d2238` | Technischer Wert |
| `15957546778d` | `15957546778d8ed652628f7707bebae5c591b585face96c63394fdfc212edf86` | Technischer Wert, Response-Digest |
| `705cae881194` | `705cae88119482be5f952c3caaa492187dd9c2eb8d7d6b902833adfbfba53ab1` | Technischer Wert |
| `9f09c054c59b` | `9f09c054c59bda8618630a70e6db910b537976ccd330e20ab6d237e4fe794ac4` | Technischer Wert, Response-Digest |
| `a74a3e8c8166` | `a74a3e8c8166601b1182f881a6df130bf364f33adf5823a663f6fef64b642349` | Technischer Wert |
| `67a9ab6d1a6f` | `67a9ab6d1a6f44f5b45aa3deb58a1696b290918f5dc084a8c85b7e1213806f63` | Request-ID |
| `f58776880f0c` | `f58776880f0c2973c6ff2326f38f5d8e95a0af6e57d0c34d64074524794e61b3` | Request-ID |
| `9e68867633cc` | `9e68867633cc12bf87120e4b30601b6ef9ae2767d3ae6c1ecff55ce5157affd3` | Request-ID |
| `2b0dce337ce8` | `2b0dce337ce88e71bfa657a08dc13c96da01b3368b3d220383be59d26fe0e04e` | Request-ID |
<!-- artifact-records:decision-table:end -->
<!-- audit:decision-table:end -->

### Freigabestatus

<!-- audit:approval-status:begin -->
- Implementierung bereit: `NOT_RECORDED`
- Validierung: `PASS`
- Claude-Freigabe: `YES`
- Red-State-Folgeslice: `NONE`
- Commit autorisiert: `NO`

<!-- artifact-records:approval-status:begin -->
Semantischer Record-Digest: `23a011b7fc25`

### Codex · Runde – · ready

| Seq/Record | Rolle | Runde | Status | Work-Unit | Tests | Transport | Request | Response | Fingerprint |
|---|---|---:|---|---|---|---|---|---|---|
| 4. `ar1-15957546778d` | `codex` | `–` | `ready` | `1` | keine | `native-codex-v2` | `native-codex-request-67a9ab6d1a6f` | `f58776880f0c` | `7ffa87c912aa` |

### Codex · Runde – · ready

| Seq/Record | Rolle | Runde | Status | Work-Unit | Tests | Transport | Request | Response | Fingerprint |
|---|---|---:|---|---|---|---|---|---|---|
| 8. `ar1-9f09c054c59b` | `codex` | `–` | `ready` | `1` | keine | `native-codex-v2` | `native-codex-request-9e68867633cc` | `2b0dce337ce8` | `7ffa87c912aa` |
<!-- artifact-records:approval-status:end -->
<!-- audit:approval-status:end -->
