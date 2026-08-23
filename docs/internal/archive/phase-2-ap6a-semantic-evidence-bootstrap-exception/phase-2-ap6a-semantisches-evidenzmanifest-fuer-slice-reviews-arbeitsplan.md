# Phase 2 – Arbeitspaket 6A: Semantisches Evidenzmanifest für Slice-Reviews

TARGET_BRANCH: feature/native-semantic-evidence-manifest

## Auftrag und kleinster vertikaler Durchstich

Dieser Plan setzt den kleinsten produktiv nutzbaren Teil von Arbeitspaket 6 aus
`docs/internal/ORCHESTRATOR_ROADMAP_PHASE_2_PLUS.md` um. Genau ein zukünftiger
Implementierungsslice ergänzt die bereits kanonischen Slice- und
Korrekturreviewpakete um einen vollständigen, deterministischen
Abdeckungsnachweis für ihren schon gefilterten Diff. Der Nachweis wird Teil
desselben content-addressierten Reviewpakets und über dessen bestehende
Evidenzkante in den nativen Claude-Request gebunden. Die Recordkette,
Attestierungen, Findings und State-v3-Spiegel bleiben unverändert
autoritative beziehungsweise operativ; es entsteht kein weiterer
Evidence-Store.

Der Durchstich passt in vier produktive Änderungsdateien und einen Slice.
Zusätzliche Repositoryreads, ein Snapshot-/Leseplan, neue Recordfamilien oder
eine Providerintegration sind dafür nicht erforderlich. Antigravity erhält
weiterhin dasselbe kanonische Basispaket wie Claude über seinen bestehenden
Legacy-Reviewvertrag.

## Repositorybefund und belegte Integrationskanten

- `src/review_packets.py::build_review_packet()` ist der einzige Builder des
  role-neutralen Slice-/Korrekturpakets. Er konstruiert
  `ReviewPacketManifest`, filtert `review_diff` aktuell anhand von
  `manifest.paths`, schreibt den gefilterten Text als `diff` in die
  kanonischen Paketbytes und leitet `ReviewPacket.digest` aus genau diesen
  Bytes ab. `_filter_diff_to_manifest()` erkennt zwar `diff --git`-Sektionen,
  prüft derzeit aber weder eindeutige Vollabdeckung noch Änderungsart,
  Abschnittsdigest oder Hunkheader; bei markerlosen Eingaben reicht es den
  Text sogar unverändert durch.
- `src/workflow.py::WorkflowEngine._run_review()` ist der produktive Aufrufer
  von `build_review_packet()`. Er übergibt bereits ausschließlich den
  vorliegenden Full-Slice- beziehungsweise Correction-Delta-Diff und die aus
  `changes.paths` abgeleitete exakte Paketallowlist, speichert das Ergebnis als
  `WorkflowHistory.active_review_packet` und lässt Antigravity für denselben
  Fingerprint exakt Claudes Paket wiederverwenden. Derselbe Dateipfad enthält
  außerdem die `to_dict()`-/`from_dict()`-Roundtripkante dieses Pakets.
- `src/workflow.py::WorkflowEngine._native_review_request()` übergibt
  `review_packet.text` bereits als eine Evidenz mit der ID `review-packet` und
  dem Typ `canonical_review_packet`. Der Abdeckungsnachweis muss daher an
  dieser bestehenden Kante als Paketbestandteil und mit seinem semantischen
  Digest weitergereicht werden; eine zweite Request- oder Statequelle wäre
  unnötig.
- `src/native_review_request.py::NativeReviewEvidenceInput` ist der bestehende
  typisierte Eingang für diese Kante. `build_native_review_request()` bindet
  Evidenz-ID, Typ, Inhaltsdigest und Bytegröße in `evidence_manifest` und
  berechnet daraus die Request-ID. `NativeReviewRequestBundle.__post_init__()`
  validiert kanonische Requestbytes sowie Inline-/Content-Ref-Metadaten gegen
  den Inhalt. Eine kleine Erweiterung dieses vorhandenen Evidenztyps um den
  Paket-Manifestdigest macht die semantische Bindung explizit und ermöglicht
  die Konsistenzprüfung gegen das eingebettete kanonische Reviewpaket, ohne
  eine unabhängige Digestautorität einzuführen.
- `schemas/native-agent-review-request-v1.schema.json` ist die von
  `load_native_review_request_schema()` geladene geschlossene Autorität für
  Inline- und referenzierte Evidenzeinträge. Nur hier kann das zusätzliche
  Digestfeld für beide Deliveryformen geschlossen und schema-validiert
  zugelassen werden; ein neues Requestschema oder ein paralleles Manifest ist
  nicht nötig.
- `src/orchestrator.py::ProductionWorkflowDriver.invoke_reviewer()`
  materialisiert weiterhin ausschließlich `ReviewPacket.canonical_bytes`,
  reicht `review_packet.manifest.paths` als Sandboxallowlist weiter und startet
  danach den nativen oder Legacy-Reviewer. Diese Aufrufkante braucht keine
  Produktänderung: Fail-closed-Fehler des Builders werden bereits in
  `_run_review()` vor `invoke_reviewer()` in `WorkflowExecutionError`
  übersetzt.

## Zielinvarianten

1. Der Builder verwendet nur `review_diff` und die übergebene sortierte,
   eindeutige Pfadallowlist. Er öffnet keine Repositorydatei, startet keinen
   Git-Unterprozess und erzeugt keinen Explorationsplan.
2. Der Builder zerlegt einen echten Unified Git Diff an strukturellen
   `diff --git`-Grenzen in vollständige Sektionen. Freier Text, Markdownzeilen
   oder hunkinterne Inhalte dürfen keine Sektions- oder Typgrenze vortäuschen.
3. Jeder im gefilterten Diff enthaltene Pfad besitzt genau einen Eintrag. Der
   Eintrag enthält den kanonischen repository-relativen Pfad, eine geschlossene
   Änderungsart, SHA-256 über die vollständigen kanonischen Sektionsbytes und
   die in Auftretensreihenfolge übernommenen vollständigen `@@ ... @@`-
   Bereichsheader.
4. Kanonische Einträge und Diffsektionen werden nach Pfad sortiert. Damit
   ändern weder die Reihenfolge der autorisierten Pfade noch die Reihenfolge
   unabhängiger Eingabesektionen Manifestbytes, Manifestdigest,
   Paketbytes oder Paketdigest. Eine einzelne inhaltlich geänderte Diffzeile
   ändert dagegen Abschnitts- und Paketdigest.
5. Fehlende Zuordnung, doppelte Sektionen, mehrere Pfade je Sektion,
   nichtkanonische oder nicht autorisierte Pfade sowie widersprüchliche
   Änderungsmetadaten scheitern. Rename-, Copy- und Binaryformen werden in
   diesem kleinen Paket nicht semantisch aufgelöst, sondern einschließlich
   mehrdeutiger Darstellungen explizit vor dem Providerstart abgewiesen.
6. Das neue Paketformat enthält genau eine kanonische
   Manifestrepräsentation und deren daraus berechneten Digest. Der
   Paketdigest bleibt SHA-256 der gesamten kanonischen Paketbytes. Neue Builder
   emittieren die erweiterte Paketversion; der History-Loader bleibt für schon
   persistierte `review-packet-v1`-Pakete lesekompatibel, darf sie aber nicht
   als neuen semantischen Abdeckungsnachweis ausgeben.
7. Slice- und Korrekturpakete benutzen denselben Builder und dasselbe
   Manifestformat. Die bestehende Korrekturlogik wählt weiterhin nur die
   `affected_finding_ids` und den gebundenen Korrekturdelta-Diff aus.
8. Der native Evidenzeintrag trägt für `canonical_review_packet` den aus den
   Paketbytes validierten Manifestdigest. Inhaltsdigest, Bytegröße,
   Manifestdigest und dadurch die Request-ID bleiben gemeinsam gebunden. Eine
   Manipulation scheitert entweder an Paket-/Evidenzvalidierung oder erzeugt
   beim legitimen Rebuild eine andere Request-ID.
9. Claude und der nachfolgende Legacy-Antigravity-Review konsumieren weiterhin
   dieselben kanonischen Paketbytes für denselben Fingerprint. Reviewerfolge,
   Findingeigentum, Attestierung, Recovery und Record-ahead-Persistenz werden
   nicht verändert.

### Slice 1 - Kanonisches Diff-Abdeckungsmanifest bis zur nativen Requestbindung

#### Ziel

Slice- und Korrekturreviews erhalten einen fail-closed gebauten,
maschinenlesbaren Abdeckungsnachweis für jede autorisierte Diffsektion. Der
Nachweis ist Teil des gemeinsamen Reviewpakets, wird zusammen mit diesem in
History und Legacytransport wiederverwendet und ist über die bestehende native
Evidenz vollständig an Claudes Request-ID gebunden.

**Exakter Änderungspfad**

- `src/review_packets.py`
- `src/workflow.py`
- `src/native_review_request.py`
- `schemas/native-agent-review-request-v1.schema.json`
- `tests/test_review_packets.py`
- `tests/test_workflow.py`
- `tests/test_native_review_request.py`
- `tests/test_orchestrator_runtime.py`

#### Umsetzung

1. Erweitere in `src/review_packets.py` die bestehenden unveränderlichen
   ReviewPacket-/Manifesttypen um kanonische Diff-Abdeckungseinträge und einen
   daraus berechneten Manifestdigest. Verwende eine einzige interne
   kanonische JSON-Darstellung als Hashgrundlage; `ReviewPacket.__post_init__`
   beziehungsweise ein eng gebundener Restorepfad validiert Paketdigest,
   Manifestdigest, Einträge und Paketinhalt gemeinsam.
2. Ersetze die permissive Filterheuristik durch einen kleinen, geschlossenen
   Parser für bereits vorliegende Unified-Git-Diffsektionen. Validiere
   Sektionsgrenzen, `a/`-/`b/`-Pfadpaar, Allowlist, Änderungsmetadaten und
   Hunkbereichsheader; lehne markerlose, doppelte, unsichere, fremde,
   Rename-/Copy- und Binarysektionen ab. Sortiere danach Einträge und die im
   Paket gespeicherten vollständigen Sektionen nach dem kanonischen Zielpfad.
3. Lasse `build_review_packet()` für `purpose=slice` und
   `purpose=correction` exakt denselben Parser verwenden. Bewahre die
   bestehende Auswahl von Zielen, Akzeptanzkriterien, offenen Findings,
   Closure-Referenzen und Attestierung unverändert; nur Diff,
   Abdeckungsmanifest und davon abhängige Digests ändern sich.
4. Passe in `src/workflow.py` den aktiven Paket-Roundtrip so an, dass neue
   Manifestdaten ausschließlich aus den kanonischen Paketbytes rekonstruiert
   und validiert werden. Behalte eine explizite, fail-closed lesende
   Kompatibilität für bestehende persistierte v1-Pakete. Übergib beim
   bestehenden `canonical_review_packet`-Evidenzeintrag zusätzlich den
   validierten Manifestdigest; ändere weder Paketbauzeitpunkt noch
   Claude-/Antigravity-Reihenfolge.
5. Erweitere `NativeReviewEvidenceInput`, Requestbuilder,
   Bundle-Konsistenzprüfung und beide Evidenzvarianten des geschlossenen
   Requestschemas um einen optionalen semantischen Digest. Für
   `canonical_review_packet` ist er Pflicht und muss dem im validierten
   Paketinhalt enthaltenen Manifestdigest entsprechen; für andere bestehende
   Evidenzarten bleibt er leer. Das Feld wird Teil der vorhandenen
   `evidence_manifest`-Bindung und damit ohne zweite Hashquelle Teil der
   Request-ID.
6. Halte `src/orchestrator.py`, Adapter, Runtime, Recordmodelle und
   Textparser unverändert. Der vorhandene Providerstart konsumiert erst das
   erfolgreich gebaute Paket; der Legacy-Antigravity-Pfad erhält weiterhin
   dessen byteidentische Basis.

#### Fokussierte synthetische Akzeptanztests

- `tests/test_review_packets.py`: Ein synthetischer Diff mit zwei
  autorisierten geänderten Dateien erzeugt exakt zwei nach Pfad sortierte
  Einträge, unterschiedliche vollständige Abschnittsdigests und die erwartete
  Änderungsart. Umgekehrte Allowlist- und Sektionsreihenfolge erzeugt
  byteidentische kanonische Manifest-/Paketbytes und identische Digests.
- `tests/test_review_packets.py`: Wird genau eine Diffzeile geändert, ändern
  sich Abschnitts- und Paketdigest. Normale Hunks bewahren ihre vollständigen
  Bereichsheader einschließlich optionalem Kontext; Zeilen, die Markdown oder
  diffähnlichen Text enthalten, werden nicht als neue Sektion oder
  Änderungsart fehlklassifiziert.
- `tests/test_review_packets.py`: Fehlende beziehungsweise markerlose
  Abdeckung, doppelte Sektion, Pfad außerhalb der Allowlist, absoluter oder
  `..`-Pfad, unpassendes `a/`-/`b/`-Paar, widersprüchliche Typmarker sowie
  Rename-, Copy- und mehrdeutige Binarydarstellungen lösen jeweils
  `ReviewPacketError` aus.
- `tests/test_review_packets.py`: Slice- und Korrekturpaket besitzen dasselbe
  Manifestformat. Das Korrekturpaket enthält weiterhin ausschließlich die
  betroffenen offenen Findings, passende Closure-Referenzen und die Sektionen
  des übergebenen Korrekturdeltas.
- `tests/test_workflow.py`: Ein kombinierter Lauf mit nativen Codex- und
  Claude-Ergebnissen konvergiert für Slice und Korrektur ohne Aufruf eines
  Legacy-Textparsers; alle synthetischen Deltas sind echte Unified Diffs und
  ein ungültiges Abdeckungsmanifest verhindert jeden Revieweraufruf.
- `tests/test_workflow.py`: Claude und Legacy-Antigravity erhalten für denselben
  Fingerprint weiterhin byteidentische Basispakete. Der
  `WorkflowHistory`-Roundtrip erhält und revalidiert das neue Manifest; eine
  vorhandene v1-History bleibt lesbar, ohne als v2-Abdeckung ausgegeben zu
  werden. Findingselektion und Resume erzeugen keine Duplikate.
- `tests/test_native_review_request.py`: Der native Claude-Request enthält den
  Paket-Manifestdigest in der bestehenden Evidenz. Paket-, Manifest- oder
  Digestmanipulation scheitert an Bundlevalidierung; ein legitimer Rebuild mit
  geändertem Manifest erzeugt eine andere Request-ID. Inline- und
  Content-Ref-Delivery bleiben gleichwertig gebunden.
- `tests/test_orchestrator_runtime.py`: Paketmaterialisierung verwendet
  weiterhin exakt die kanonischen Bytes und die autorisierten Manifestpfade;
  ein Cache- oder Digestwiderspruch scheitert vor dem Providerstart. Der
  Legacytransport benötigt keine neue Adapter- oder Runtimeform.
- Die bestehenden ReviewPacket-, Native-Request-, Finding-, Resume-,
  Record-ahead- und Legacy-Transporttests bleiben grün. Fixtures verwenden nur
  synthetische Diffs, Fake-Adapter und temporäre Verzeichnisse; sie führen
  weder Provideraufrufe noch Netz-, Credential- oder freie Repositoryzugriffe
  aus.

#### Fokussierte Ausführung und orchestratorische Validierung

- Während der Implementierung werden nur die unmittelbar betroffenen,
  providerfreien Testmodule fokussiert ausgeführt:
  `tests/test_review_packets.py`, `tests/test_native_review_request.py`, die
  einschlägigen Tests aus `tests/test_workflow.py` und
  `tests/test_orchestrator_runtime.py`.
- Ausschließlich der Orchestrator führt danach die konfigurierte vollständige
  Repositorymatrix aus und erzeugt die fingerprintgebundene Attestierung.
  Agenten emittieren kein `VALIDATION_RESULT`.

#### Stopbedingungen

- Stoppe, falls eine eindeutige Abdeckung nicht allein aus `review_diff` und
  der exakten Allowlist ableitbar ist oder irgendein zusätzlicher
  Repositoryread nötig wird.
- Stoppe, falls die kanonische Manifestrepräsentation und ihr Digest nicht als
  Bestandteil derselben kanonischen Paketbytes validiert werden können.
- Stoppe, falls der native Request den Manifestdigest nicht über seinen
  bestehenden Reviewpaket-Evidenzeintrag binden kann oder dafür eine zweite
  Persistenz-/Digestautorität nötig wäre.
- Stoppe, statt Rename-, Copy- oder Binarysemantik heuristisch zu erraten;
  diese Formen bleiben in diesem Slice explizite fail-closed Eingaben.
- Stoppe bei jeder notwendigen Lockerung von Record-, Fingerprint-,
  Attestierungs-, Finding-, Resume- oder Reviewerreihenfolge-Invarianten oder
  sobald mehr als die vier genannten produktiven Dateien beziehungsweise ein
  zweiter Slice erforderlich wäre.

## Nichtziele

Nicht enthalten sind Snapshotengine und modellgesteuerter Leseplan für große
Finalreviews, Dependency-/Callgraphanalyse, Finalreview-Kompaktierung und
P2-FU-025, native Antigravity-Reviews, neue Records, Providerattempts oder
Evidence-Stores, Parserabbau, Änderungen an Freigabe-/Retry-/Quota-Semantik
sowie Logging-, Kosten-, Telemetrie-, UI-, Push-, Merge- oder
History-Rewrite-Arbeiten.

## Orchestrator-Prüfprotokoll

### Review-Feedback von Claude

<!-- audit:claude-review:begin -->
### Ereignis 3: Runde 1

- Reviewer: `claude`
- Freigabe: `YES`
- Validierungsbindung: `plan-validation-f001dc404481`
- Testdateien: keine
- Prüfdimensionen: Verified the PLAN_ONLY work-plan artifact docs/internal/phase-2-ap6a-semantisches-evidenzmanifest-fuer-slice-reviews-arbeitsplan.md satisfies the handoff contract: exactly one future '### Slice 1 - ...' heading, a standalone '**Exakter Änderungspfad**' section with only bullet-listed repository-relative paths, and &lt;=5 productive files (4). Confirmed every touched path in the diff (src/agent_runtime.py, src/orchestrator.py, src/workflow.py, src/workflow_state.py, matching tests, and the new plan doc) is an exact member of authorized_paths, and that the non-plan production/test edits are the bootstrap hotfixes already covered by prior fingerprint-bound UNEXPECTED_FILE user-gate approvals recorded in the plan's own audit trail, including one gate decision whose fingerprint (f001dc4044814e2d76b1bf4046a26c31ae1bc19ca92389aecfa72461bec8c055) exactly matches this request's current_fingerprint and the bound validation_attestation, which shows a PASS internal:work-plan-contract run. Reviewed correctness of the response-handling changes in agent_runtime.py: validated_response_callback now persists the raw canonical response after schema validation and request_id binding but before domain contract parsing, so schema-valid/domain-rejected responses are preserved for diagnosis (covered by two new targeted tests), classify_agent_failure now maps bound-contract violations to AgentFailureKind.OUTPUT, and failure.technical_text is now propagated into the persisted failure record. Reviewed the new idempotent gate-resume mechanism end to end: WorkflowState.record_user_gate_decision now reuses an existing matching approved GateDecisionRecord instead of appending a duplicate, WorkflowEngine.decide_current_gate only calls _persist_structured when a new decision was actually appended (avoiding dual-write), WorkflowEngine's plan/slice change-boundary checks and ProductionWorkflowDriver.validate_plan now treat an exact fingerprint+paths match against a historical UNEXPECTED_FILE/QUOTA_RESUME_DIFF gate approval as already covered, and orchestrator.run_production_workflow auto-replays an exact prior approval via the new _current_gate_approval helper on resume. All of these paths are covered by new unit tests exercising exact-match reuse, fingerprint-mismatch non-reuse, and no-dual-write assertions.
- Größtes Restrisiko: record_user_gate_decision's existing_approval reuse path is shown (via diff context) to only change gate_decisions population and the persistence guard; the unmodified surrounding side-effect/acknowledgement logic around 'completed_side_effects = (*completed_side_effects, acknowledgement)' still appears to run purely off the &#96;approved&#96; boolean rather than off &#96;existing_approval is None&#96;. None of the new tests assert that completed_side_effects (or any other side effect keyed on gate approval) stays stable when an already-approved gate is reopened and re-decided during a resume.
- Realistische Bruchbedingung: If a work unit's gate is legitimately reopened multiple times across resumes (e.g. repeated watch retries hitting the same AWAITING_USER_DECISION state), the immutable GateDecisionRecord and its structured persistence correctly stay singular, but any acknowledgement or other side effect appended unconditionally on the approved branch would be appended again on every reopen, producing duplicate acknowledgement entries or re-triggered side-effect behavior even though the audit-facing gate_decisions list and record chain remain correct — this would surface as unexplained repeated side-effect execution under real resume/watch load rather than in a synthetic single-resume test.
- Eigene Findings: keine

<!-- artifact-records:claude-review:begin -->
Semantischer Record-Digest: `fa8feb660c507b2095a5341e18a5cdb44b5b85133e2158da22a2762ab09b5fb5`

- 16. `ar1-0e42f9186d5f109b31cefd60d69bcaa4128f5015d740d24636ce3f29fd1cbdb9`: `approved`; Work-Unit `1`; Findings keine; Fingerprint `f001dc4044814e2d76b1bf4046a26c31ae1bc19ca92389aecfa72461bec8c055`; Transport `native-claude-review-v1`; Request `native-review-request-0175c0d38a685c00e75f03149991c5b1c8b66141e6bf217e2d5afec81582f775`; Response `8d7b84531678045b8fa2e81281c22a850f0cb86e5a9d4a73efb15f1f10221669`
<!-- artifact-records:claude-review:end -->
<!-- audit:claude-review:end -->

### Review-Feedback von Antigravity

<!-- audit:antigravity-review:begin -->
### Ereignis 4: Runde 1

- Reviewer: `antigravity`
- Freigabe: `YES`
- Validierungsbindung: `plan-validation-f001dc404481`
- Testdateien: keine
- Prüfdimensionen: Checked plan completeness, single-slice vertical boundary, path allowlist discipline (4 productive files), parser contract for Unified Diff section validation without extra repository reads, canonical sorting and SHA-256 section digest invariance, extension of ReviewPacketManifest/ReviewPacket, evidence-manifest binding in NativeReviewEvidenceInput/schema, and synthetic test isolation.
- Größtes Restrisiko: Strict diff parser edge-case handling for mode-only or non-standard Git headers leading to unexpected fail-closed rejections on valid changes.
- Realistische Bruchbedingung: A future commit includes a file permission mode change without hunks, causing the section parser to fail parsing header metadata and reject the review packet before provider invocation.
- Eigene Findings: keine

<!-- artifact-records:antigravity-review:begin -->
Semantischer Record-Digest: `fa8feb660c507b2095a5341e18a5cdb44b5b85133e2158da22a2762ab09b5fb5`

- 21. `ar1-0bd4c1d12dbbd1bb9b9bb55f649b6627276bc950e404450c1dc2852641ccaed5`: `approved`; Work-Unit `1`; Findings keine; Fingerprint `f001dc4044814e2d76b1bf4046a26c31ae1bc19ca92389aecfa72461bec8c055`; Transport `legacy-text`
<!-- artifact-records:antigravity-review:end -->
<!-- audit:antigravity-review:end -->

### Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
Noch keine strukturierten Codex-Antworten.

<!-- artifact-records:codex-responses:begin -->
Semantischer Record-Digest: `fa8feb660c507b2095a5341e18a5cdb44b5b85133e2158da22a2762ab09b5fb5`

Keine Codex-Findingantworten.
<!-- artifact-records:codex-responses:end -->
<!-- audit:codex-responses:end -->

### Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### Ereignis 1: `plan-validation-1ff90ec57d4b`

- Diff-Fingerprint: `1ff90ec57d4b85ce9be2e72d1c3684d15a3289fefe90620228ea9289a76c6d59`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: internal plan contract passed
- Ausgabedigest: `7be6b05c3142d160e57c77b7fd76005d1c46e3ea18e0ae29e59d645f5a145141`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| internal:work-plan-contract | PASS | 0 | slices=1; planned_paths=1; changed_paths=1; future_slices=1; work_plan=docs/internal/phase-2-ap6a-semantisches-evidenzmanifest-fuer-slice-reviews-arbeitsplan.md |

### Ereignis 2: `plan-validation-f001dc404481`

- Diff-Fingerprint: `f001dc4044814e2d76b1bf4046a26c31ae1bc19ca92389aecfa72461bec8c055`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: internal plan contract passed
- Ausgabedigest: `6090909e24ca3a83bd47ab1451472ac38fdd7b00c6b9494405753e33f0f50d88`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| internal:work-plan-contract | PASS | 0 | slices=1; planned_paths=1; changed_paths=8; future_slices=1; work_plan=docs/internal/phase-2-ap6a-semantisches-evidenzmanifest-fuer-slice-reviews-arbeitsplan.md |

<!-- artifact-records:validation-attestation:begin -->
Semantischer Record-Digest: `fa8feb660c507b2095a5341e18a5cdb44b5b85133e2158da22a2762ab09b5fb5`

- 2. `ar1-8d25df0efdac8356c59a99940dc35551e37ca470a0ca37a1babfedfc40ca75a2`: Providerinput `codex/codex_plan` = `allowed`; local_input_chars `44572/4000000`, local_input_bytes `44740/16000000`; local_input_digest `8a2732822ca0e7b42d70d0eb3ba9eb24c231e1eb2c3757a11f5d8e20729cc2e2`, Policy `603e75566ee1fad99cd271d52bc8c2814387f55bf526699188eead5156cd325e`, Übergang `fedf75484aba8c03b0abfe5df712254df04a10936a6c5f580991ff04d30c5a08`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, local_input_largest_component `stdin_prompt`; local_input_component_count `2`; Komponenten `stdin_prompt=40700/40868, response_schema=3872/3872`
- 6. `ar1-f2866a7db4ed07288ed1052c3c7862ddc113aaa13e66608557dbe765cbd1dd03`: Attestierung durch `orchestrator`; Fingerprint `1ff90ec57d4b85ce9be2e72d1c3684d15a3289fefe90620228ea9289a76c6d59`
  - `pass` / Exit `0` / Output `7be6b05c3142d160e57c77b7fd76005d1c46e3ea18e0ae29e59d645f5a145141`: `argv` [`internal:work-plan-contract`]
- 7. `ar1-d020d23b831b8d3a448ff0322682686775e629021bc90fcee994f45e8ca7cb11`: Providerinput `claude/claude_plan_review` = `allowed`; local_input_chars `50769/4000000`, local_input_bytes `51014/16000000`; local_input_digest `458be38834be922a65979af1af8b31a299f345d35b8a694380581dbad7f4eae9`, Policy `603e75566ee1fad99cd271d52bc8c2814387f55bf526699188eead5156cd325e`, Übergang `ce9f0154cd4d0f0240ef23c641cd0de50b2efc7c299eb821b78526e18bfcd85c`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, local_input_largest_component `request_chunk_001`; local_input_component_count `6`; Komponenten `request_chunk_001=24000/24126, request_chunk_002=20645/20764, packet_manifest=596/596, system_policy=574/574, response_schema=4724/4724, start_directive=230/230`
- 13. `ar1-cf0b738e047e25c3e63c44a268c2dc40c862089d6143fe5c50a059d98733b696`: Attestierung durch `orchestrator`; Fingerprint `f001dc4044814e2d76b1bf4046a26c31ae1bc19ca92389aecfa72461bec8c055`
  - `pass` / Exit `0` / Output `6090909e24ca3a83bd47ab1451472ac38fdd7b00c6b9494405753e33f0f50d88`: `argv` [`internal:work-plan-contract`]
- 14. `ar1-79afe1579fd4054d0563789d6fcfd611683fae19097f327802c9e6e3654a921a`: Providerinput `claude/claude_plan_review` = `allowed`; local_input_chars `80457/4000000`, local_input_bytes `80727/16000000`; local_input_digest `f5a2c8f6737461d883e48c88c20f8f369206058268f6f5eec8019ce52e0e4cd8`, Policy `603e75566ee1fad99cd271d52bc8c2814387f55bf526699188eead5156cd325e`, Übergang `2c9c074feda3d7cc078b0b423299c5621b16a8a45022aa17b3d236759448dd64`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, local_input_largest_component `evidence_asset_001`; local_input_component_count `6`; Komponenten `request_chunk_001=21528/21615, evidence_asset_001=52791/52974, packet_manifest=610/610, system_policy=574/574, response_schema=4724/4724, start_directive=230/230`
- 18. `ar1-6cec3d4f092b2385faf3a5dfa9b506dd8d0d9cdc5c7cb292a6758ab28bbf73a8`: Providerinput `antigravity/antigravity_plan_review` = `allowed`; local_input_chars `83789/4000000`, local_input_bytes `84066/16000000`; local_input_digest `d1d8466d2f89694cae0fc774caaac31510751a7e4e0e22123b2810c122dae600`, Policy `603e75566ee1fad99cd271d52bc8c2814387f55bf526699188eead5156cd325e`, Übergang `63e46513f896bbdc9f5a8ed15de973d5da7b2b328e911ac3241b5384594c5601`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, local_input_largest_component `prompt_file`; local_input_component_count `3`; Komponenten `prompt_file=83130/83407, response_schema=146/146, start_directive=513/513`
- Providerattempt-Summe Run `watch-20260823-095929.706215Z-8450f2e108cc` / Operation `provider-operation-28c1365ad68a5d1db43c023556974d420b3b9cc1cd73253044e072d4170055e5` (`claude/claude_plan_review`): Attempts `1`, offen `0`, Duration `221.300450` (bekannt `1`, unbekannt `0`); input_tokens=sum:6,known:1,unknown:0; tool_input_tokens=sum:0,known:0,unknown:1; cache_read_input_tokens=sum:8996,known:1,unknown:0; cache_creation_input_tokens=sum:41337,known:1,unknown:0; thinking_tokens=sum:0,known:0,unknown:1; output_tokens=sum:21784,known:1,unknown:0; total_tokens=sum:0,known:0,unknown:1; turns=sum:5,known:1,unknown:0; cost_usd=sum:0.3860052,known:1,unknown:0
  - 17. `ar1-00884bfd6f3c915d3a6657569655d82e5e09b936f8846fd84cef3dac6af9cd05`: Attempt `1` = `succeeded`; Messung `ar1-79afe1579fd4054d0563789d6fcfd611683fae19097f327802c9e6e3654a921a`; Duration `221.30044956400525`; Fehler `none`; Usage `input_tokens=6, tool_input_tokens=unknown, cache_read_input_tokens=8996, cache_creation_input_tokens=41337, thinking_tokens=unknown, output_tokens=21784, total_tokens=unknown, turns=5, cost_usd=0.3860052`
- Providerattempt-Summe Run `watch-20260823-095929.706215Z-8450f2e108cc` / Operation `provider-operation-5a6983892276b95246f75c15fac0a1d7e55978a16b83b49d320bdee13ea52010` (`codex/codex_plan`): Attempts `1`, offen `0`, Duration `277.758720` (bekannt `1`, unbekannt `0`); input_tokens=sum:0,known:0,unknown:1; tool_input_tokens=sum:0,known:0,unknown:1; cache_read_input_tokens=sum:0,known:0,unknown:1; cache_creation_input_tokens=sum:0,known:0,unknown:1; thinking_tokens=sum:0,known:0,unknown:1; output_tokens=sum:0,known:0,unknown:1; total_tokens=sum:0,known:0,unknown:1; turns=sum:0,known:0,unknown:1; cost_usd=sum:0,known:0,unknown:1
  - 5. `ar1-33fe4ad98fa6c12b2bb7f35a92715875f66504087a3920c516b7045f362e56bd`: Attempt `1` = `succeeded`; Messung `ar1-8d25df0efdac8356c59a99940dc35551e37ca470a0ca37a1babfedfc40ca75a2`; Duration `277.75872042099945`; Fehler `none`; Usage `unknown`
- Providerattempt-Summe Run `watch-20260823-095929.706215Z-8450f2e108cc` / Operation `provider-operation-6cccfc0844c6267198d0aa745ff387982bebc22dec8549ca39fe9c59e648be5a` (`claude/claude_plan_review`): Attempts `1`, offen `0`, Duration `94.418222` (bekannt `1`, unbekannt `0`); input_tokens=sum:6,known:1,unknown:0; tool_input_tokens=sum:0,known:0,unknown:1; cache_read_input_tokens=sum:12878,known:1,unknown:0; cache_creation_input_tokens=sum:22785,known:1,unknown:0; thinking_tokens=sum:0,known:0,unknown:1; output_tokens=sum:8717,known:1,unknown:0; total_tokens=sum:0,known:0,unknown:1; turns=sum:5,known:1,unknown:0; cost_usd=sum:0.1819226,known:1,unknown:0
  - 9. `ar1-ef94eeda43ce065cdf584d41c40d0305dfaa440963fed6ec1cda7cff57c6f358`: Attempt `1` = `failed`; Messung `ar1-d020d23b831b8d3a448ff0322682686775e629021bc90fcee994f45e8ca7cb11`; Duration `94.41822229896206`; Fehler `runtime`; Usage `input_tokens=6, tool_input_tokens=unknown, cache_read_input_tokens=12878, cache_creation_input_tokens=22785, thinking_tokens=unknown, output_tokens=8717, total_tokens=unknown, turns=5, cost_usd=0.1819226`
- Providerattempt-Summe Run `watch-20260823-095929.706215Z-8450f2e108cc` / Operation `provider-operation-e9ad60f8ee786e17e3660666add823ae020d42b15f3b93da0112f6bf48cd4ca1` (`antigravity/antigravity_plan_review`): Attempts `1`, offen `0`, Duration `29.658358` (bekannt `1`, unbekannt `0`); input_tokens=sum:103181,known:1,unknown:0; tool_input_tokens=sum:0,known:0,unknown:1; cache_read_input_tokens=sum:0,known:0,unknown:1; cache_creation_input_tokens=sum:0,known:0,unknown:1; thinking_tokens=sum:3160,known:1,unknown:0; output_tokens=sum:4017,known:1,unknown:0; total_tokens=sum:107198,known:1,unknown:0; turns=sum:1,known:1,unknown:0; cost_usd=sum:0,known:0,unknown:1
  - 20. `ar1-275519738ba4068edacdc50b25c1692e8f4beff4b80ceda592ec078fd1cf9383`: Attempt `1` = `succeeded`; Messung `ar1-6cec3d4f092b2385faf3a5dfa9b506dd8d0d9cdc5c7cb292a6758ab28bbf73a8`; Duration `29.658357635024004`; Fehler `none`; Usage `input_tokens=103181, tool_input_tokens=unknown, cache_read_input_tokens=unknown, cache_creation_input_tokens=unknown, thinking_tokens=3160, output_tokens=4017, total_tokens=107198, turns=1, cost_usd=unknown`
<!-- artifact-records:validation-attestation:end -->
<!-- audit:validation-attestation:end -->

### Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
- Teständerungsfreigabe: nicht erfasst.
- Pre-Mortems:
  - Ereignis 3: Most likely failure in three months: a later change loosens has_gate_approval/_current_gate_approval matching (e.g. weakening the exact path-tuple or fingerprint equality used for gate-reuse and plan/slice change-boundary bypass) without recognizing that this same equality also gates whether validate_plan and _validate_change_boundary treat new diff paths as already-approved, silently letting genuinely new out-of-scope files bypass the user gate under a stale or coincidentally matching fingerprint.
  - Ereignis 4: A future extension adds partial rename/copy handling to the review packet builder without synchronizing the section digest formula with native review bundle validation, causing silent request_id verification mismatches.

<!-- artifact-records:test-approval-premortem:begin -->
Semantischer Record-Digest: `fa8feb660c507b2095a5341e18a5cdb44b5b85133e2158da22a2762ab09b5fb5`

- 10. `ar1-28cd10ad5b41f7d7d748cb1c7d1b66a09ecae349b2e38495cd27540cc3696c69`: `unexpected-file` = `approved` durch `user`; Fingerprint `a225d83ff89cff9bffa93ba30f90f2417a0ee84e617b3fe0a1b7315e5cd0a2f9` — Geprüfter Native-Review-Diagnosehotfix aus Commit 5b51ace: schema- und requestgültige Claude-<br>    Rohantworten werden vor der fachlichen Bindungsprüfung gesichert; Vertragsablehnungen erhalten stabile Diagnosecodes<br>    und werden als Outputfehler klassifiziert. Vollständige Suite mit 1162 Tests bestanden; freigegebene Zusatzpfade:<br>    src/agent_runtime.py und tests/test_agent_runtime.py.
- 11. `ar1-cd070863ed1c4dd0a046bedad91ae81c872b64fa60dc26623a7d18997ff1a16f`: `unexpected-file` = `approved` durch `user`; Fingerprint `1d034057cfaec0d6ef715402e5465f19f4816f4ab39265564b72ffaf6f062c38` — Geprüfte Bootstrap-Hotfixes für Native-Review-Diagnostik, PLAN_ONLY-Pfadfreigabe und idempotente<br>      Gate-Wiederaufnahme. Exakt gebundene Zusatzpfade geprüft; vollständige Suite mit 1166 Tests bestanden; git diff<br>      --check sauber.
- 12. `ar1-86d85654029f49fccda2570ecb0736b4c3fa1e538fbe4cb75c588daff34b46dc`: `unexpected-file` = `approved` durch `user`; Fingerprint `f001dc4044814e2d76b1bf4046a26c31ae1bc19ca92389aecfa72461bec8c055` — Geprüfte Bootstrap-Hotfixes für Native-Review-Diagnostik, PLAN_ONLY-Gateauswertung, idempotente<br>      Wiederaufnahme und interne Planvalidierung. Exakte Zusatzpfade geprüft; vollständige Suite mit 1167 Tests<br>      bestanden; git diff --check sauber.
<!-- artifact-records:test-approval-premortem:end -->
<!-- audit:test-approval-premortem:end -->

### Findings-Lebenszyklus

<!-- audit:findings:begin -->
Noch keine strukturierten Findings.

<!-- artifact-records:findings:begin -->
Semantischer Record-Digest: `fa8feb660c507b2095a5341e18a5cdb44b5b85133e2158da22a2762ab09b5fb5`

Keine Finding-Übergänge.
<!-- artifact-records:findings:end -->
<!-- audit:findings:end -->

### Entscheidungstabelle

<!-- audit:decision-table:begin -->
| ID | Quelle | Finding | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|---|
| – | – | Noch keine Findings | – | – | – |

<!-- artifact-records:decision-table:begin -->
Semantischer Record-Digest: `fa8feb660c507b2095a5341e18a5cdb44b5b85133e2158da22a2762ab09b5fb5`

| Seq | Record | Typ | Status | Logische ID | Revision | Fingerprint |
|---:|---|---|---|---|---:|---|
| 1 | `ar1-7890146116509498b7876fcecabf147fa6df52b09de6226d7d7614bb9c6ae6d3` | `task` | `accepted` | `task-contract` | 1 | `contract:8a67cdb39c0607b52f1231a2fdbd7ab46dd40825a8b33eeca2c315de33bbcb84` |
| 2 | `ar1-8d25df0efdac8356c59a99940dc35551e37ca470a0ca37a1babfedfc40ca75a2` | `provider_input_measurement` | `measured` | `provider-input-1-codex_plan` | 1 | `implementation:8a67cdb39c0607b52f1231a2fdbd7ab46dd40825a8b33eeca2c315de33bbcb84` |
| 3 | `ar1-2ce10cbbb60e4e15b6eeb22c12035579d411d864ba33614794f26cf69d6a2a05` | `provider_attempt` | `started` | `provider-operation-5a6983892276b95246f75c15fac0a1d7e55978a16b83b49d320bdee13ea52010-1` | 1 | `implementation:8a67cdb39c0607b52f1231a2fdbd7ab46dd40825a8b33eeca2c315de33bbcb84` |
| 4 | `ar1-8ca397e70eeba969b25bcc1fa0e62660191e28945b1e3a22fb75459768c9a485` | `agent_result` | `ready` | `agent-1-codex_plan-1` | 1 | `contract:8a67cdb39c0607b52f1231a2fdbd7ab46dd40825a8b33eeca2c315de33bbcb84` |
| 5 | `ar1-33fe4ad98fa6c12b2bb7f35a92715875f66504087a3920c516b7045f362e56bd` | `provider_attempt` | `succeeded` | `provider-operation-5a6983892276b95246f75c15fac0a1d7e55978a16b83b49d320bdee13ea52010-1` | 2 | `implementation:8a67cdb39c0607b52f1231a2fdbd7ab46dd40825a8b33eeca2c315de33bbcb84` |
| 6 | `ar1-f2866a7db4ed07288ed1052c3c7862ddc113aaa13e66608557dbe765cbd1dd03` | `validation_attestation` | `attested` | `plan-validation-1ff90ec57d4b` | 1 | `implementation:1ff90ec57d4b85ce9be2e72d1c3684d15a3289fefe90620228ea9289a76c6d59` |
| 7 | `ar1-d020d23b831b8d3a448ff0322682686775e629021bc90fcee994f45e8ca7cb11` | `provider_input_measurement` | `measured` | `provider-input-1-claude_plan_review` | 1 | `implementation:8a67cdb39c0607b52f1231a2fdbd7ab46dd40825a8b33eeca2c315de33bbcb84` |
| 8 | `ar1-ee5869e861ad229a0c9a82dc7862e0692b417b36d3c39003c881b1f19061fa64` | `provider_attempt` | `started` | `provider-operation-6cccfc0844c6267198d0aa745ff387982bebc22dec8549ca39fe9c59e648be5a-1` | 1 | `implementation:8a67cdb39c0607b52f1231a2fdbd7ab46dd40825a8b33eeca2c315de33bbcb84` |
| 9 | `ar1-ef94eeda43ce065cdf584d41c40d0305dfaa440963fed6ec1cda7cff57c6f358` | `provider_attempt` | `failed` | `provider-operation-6cccfc0844c6267198d0aa745ff387982bebc22dec8549ca39fe9c59e648be5a-1` | 2 | `implementation:8a67cdb39c0607b52f1231a2fdbd7ab46dd40825a8b33eeca2c315de33bbcb84` |
| 10 | `ar1-28cd10ad5b41f7d7d748cb1c7d1b66a09ecae349b2e38495cd27540cc3696c69` | `gate` | `decided` | `gate-unexpected_file-a225d83ff89c` | 1 | `implementation:a225d83ff89cff9bffa93ba30f90f2417a0ee84e617b3fe0a1b7315e5cd0a2f9` |
| 11 | `ar1-cd070863ed1c4dd0a046bedad91ae81c872b64fa60dc26623a7d18997ff1a16f` | `gate` | `decided` | `gate-unexpected_file-1d034057cfae` | 1 | `implementation:1d034057cfaec0d6ef715402e5465f19f4816f4ab39265564b72ffaf6f062c38` |
| 12 | `ar1-86d85654029f49fccda2570ecb0736b4c3fa1e538fbe4cb75c588daff34b46dc` | `gate` | `decided` | `gate-unexpected_file-f001dc404481` | 1 | `implementation:f001dc4044814e2d76b1bf4046a26c31ae1bc19ca92389aecfa72461bec8c055` |
| 13 | `ar1-cf0b738e047e25c3e63c44a268c2dc40c862089d6143fe5c50a059d98733b696` | `validation_attestation` | `attested` | `plan-validation-f001dc404481` | 1 | `implementation:f001dc4044814e2d76b1bf4046a26c31ae1bc19ca92389aecfa72461bec8c055` |
| 14 | `ar1-79afe1579fd4054d0563789d6fcfd611683fae19097f327802c9e6e3654a921a` | `provider_input_measurement` | `measured` | `provider-input-1-claude_plan_review` | 2 | `implementation:8a67cdb39c0607b52f1231a2fdbd7ab46dd40825a8b33eeca2c315de33bbcb84` |
| 15 | `ar1-3a2a6b78b121b763a5c6916170585ca2ac335a2c030e1386b2182880a17c70e5` | `provider_attempt` | `started` | `provider-operation-28c1365ad68a5d1db43c023556974d420b3b9cc1cd73253044e072d4170055e5-1` | 1 | `implementation:8a67cdb39c0607b52f1231a2fdbd7ab46dd40825a8b33eeca2c315de33bbcb84` |
| 16 | `ar1-0e42f9186d5f109b31cefd60d69bcaa4128f5015d740d24636ce3f29fd1cbdb9` | `review` | `decided` | `review-claude-1-1` | 1 | `implementation:f001dc4044814e2d76b1bf4046a26c31ae1bc19ca92389aecfa72461bec8c055` |
| 17 | `ar1-00884bfd6f3c915d3a6657569655d82e5e09b936f8846fd84cef3dac6af9cd05` | `provider_attempt` | `succeeded` | `provider-operation-28c1365ad68a5d1db43c023556974d420b3b9cc1cd73253044e072d4170055e5-1` | 2 | `implementation:8a67cdb39c0607b52f1231a2fdbd7ab46dd40825a8b33eeca2c315de33bbcb84` |
| 18 | `ar1-6cec3d4f092b2385faf3a5dfa9b506dd8d0d9cdc5c7cb292a6758ab28bbf73a8` | `provider_input_measurement` | `measured` | `provider-input-1-antigravity_plan_review` | 1 | `implementation:8a67cdb39c0607b52f1231a2fdbd7ab46dd40825a8b33eeca2c315de33bbcb84` |
| 19 | `ar1-a7d9dbe63e6a56cd9b58bad24aa9ec473938f55117b9554f629a79bd670d7ddb` | `provider_attempt` | `started` | `provider-operation-e9ad60f8ee786e17e3660666add823ae020d42b15f3b93da0112f6bf48cd4ca1-1` | 1 | `implementation:8a67cdb39c0607b52f1231a2fdbd7ab46dd40825a8b33eeca2c315de33bbcb84` |
| 20 | `ar1-275519738ba4068edacdc50b25c1692e8f4beff4b80ceda592ec078fd1cf9383` | `provider_attempt` | `succeeded` | `provider-operation-e9ad60f8ee786e17e3660666add823ae020d42b15f3b93da0112f6bf48cd4ca1-1` | 2 | `implementation:8a67cdb39c0607b52f1231a2fdbd7ab46dd40825a8b33eeca2c315de33bbcb84` |
| 21 | `ar1-0bd4c1d12dbbd1bb9b9bb55f649b6627276bc950e404450c1dc2852641ccaed5` | `review` | `decided` | `review-antigravity-1-1` | 1 | `implementation:f001dc4044814e2d76b1bf4046a26c31ae1bc19ca92389aecfa72461bec8c055` |
<!-- artifact-records:decision-table:end -->
<!-- audit:decision-table:end -->

### Freigabestatus

<!-- audit:approval-status:begin -->
- Implementierung bereit: `NOT_RECORDED`
- Validierung: `PASS`
- Claude-Freigabe: `YES`
- Antigravity-Freigabe: `YES`
- Red-State-Folgeslice: `NONE`
- Commit autorisiert: `NO`

<!-- artifact-records:approval-status:begin -->
Semantischer Record-Digest: `fa8feb660c507b2095a5341e18a5cdb44b5b85133e2158da22a2762ab09b5fb5`

- 4. `ar1-8ca397e70eeba969b25bcc1fa0e62660191e28945b1e3a22fb75459768c9a485`: Agentresult `codex` / `ready`; Work-Unit `1`; Tests keine; Transport `native-codex-v1`; Request `native-codex-request-ae3688acbd957f62de074cf9c7d2c0b8df0727caa8884e23c79a5b14cfe74165`; Response `20c5eba276ce810ded9e961961662c4050d9683adf1dd8725bf0ec203b315cee`; Fingerprint `8a67cdb39c0607b52f1231a2fdbd7ab46dd40825a8b33eeca2c315de33bbcb84`
<!-- artifact-records:approval-status:end -->
<!-- audit:approval-status:end -->
