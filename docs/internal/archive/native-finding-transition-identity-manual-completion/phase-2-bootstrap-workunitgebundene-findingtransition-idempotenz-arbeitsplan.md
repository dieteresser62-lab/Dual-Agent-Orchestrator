# Phase 2 – Work-Unit-gebundene Findingtransition-Idempotenz

TARGET_BRANCH: feature/native-finding-transition-identity

## Auftrag und Abgrenzung

Dieser Bootstrap-Härtungsschnitt bindet die Idempotenzschlüssel aller künftig
geschriebenen strukturierten Reviewer-Findingtransitionen an die autoritative
Work Unit. Er ändert weder Recordtyp noch Payloadschema und schreibt keine
historische Recordkette um. Der historische Textpfad behält seine bisherige
Schlüsselbildung; der neue Schlüsselraum gilt ausschließlich für
`structured-v1`-Transitionen, deren `work_unit_id` bereits vor dem Append aus
dem gebundenen `WorkflowState` feststeht.

Nicht Bestandteil sind neue Findingklassen oder Reklassifizierungsregeln,
Provideroperations-IDs, Reviewerreihenfolge, Approval- und Retrysemantik,
AP6A-Evidenz, Watch-/Quota-/Pfadgates, Antigravity-Transport sowie ein Umbau
von Replay, Projektion, State-v3 oder Recordschemas.

## Repositorybefund

- `src/orchestrator.py::ProductionWorkflowDriver.persist_review_contract()`
  ist der aktuelle Aufrufer für markerbasierte Reviews. Er persistiert zuerst
  den `ReviewPayload` und delegiert danach mit `structured=False` an
  `_persist_review_finding_transitions()`.
- `src/orchestrator.py::ProductionWorkflowDriver.persist_native_review_contract()`
  ist der aktuelle Aufrufer für den gebundenen nativen Claude-Pfad. Nach der
  Prüfung von `structured-v1` und `native-claude-review-v1` persistiert er den
  request- und responsegebundenen `ReviewPayload` und delegiert mit
  `structured=True` an denselben Transition-Writer.
- `src/orchestrator.py::ProductionWorkflowDriver._persist_review_finding_transitions()`
  ermittelt `opened`, `reclassified`, echte `status_changed`-Übergänge und
  reine Statusbegründungsrevisionen. Der Payload jeder strukturierten
  Transition enthält bereits
  `active_state.current_work_unit_id`. Im Idempotenzschlüssel ist diese Work
  Unit derzeit jedoch nur im Sonderfall der reinen Begründungsrevision über
  `status_rationale:<work-unit-id>` enthalten; die übrigen Transitionen
  verwenden Finding-ID, Transitionsidentität, Runde und Reviewer.
- Der Writer ist damit der einzige erforderliche produktive Änderungspfad:
  beide produktiven Review-Aufrufer laufen durch ihn, und die Work-Unit-Bindung
  liegt dort vor dem Append autoritativ vor. `ArtifactBridge.append()` prüft
  einen wiederholten Schlüssel gegen Payload, Logical-ID und Fingerprint und
  liefert bei identischer Bedeutung den vorhandenen Record zurück; bei
  abweichender Bedeutung hält es fail-closed an.
- `src/artifact_replay.py::replay_artifacts()` validiert die append-only Kette
  und behandelt Idempotenzschlüssel als opaque Eindeutigkeitswerte.
  `replay_findings()` projiziert die bereits im
  `FindingTransitionPayload.work_unit_id` gespeicherte Work-Unit-Zuordnung.
  Deshalb erfordert die Schlüsselhärtung weder Replay- noch Schemaänderung;
  alte Schlüssel bleiben ohne Migration lesbar.
- `tests/test_orchestrator_runtime.py` enthält bereits die nahen
  providerfreien Integrationsfixtures und Nachweise für native
  Findingpersistenz, autoritativen Replay, Mirror-Drift sowie Record-ahead-
  Recovery ohne erneuten Providerstart. Die Regressionen können dort mit
  temporären Git-Repositories, synthetischen Findings und Fake-Adaptern den
  vollständigen Writer-/Store-/Replay-/Resume-Pfad abdecken.

## Zielinvarianten

1. Der Writer leitet für jede neue strukturierte Reviewertransition vor dem
   Append einen stabilen, Work-Unit-gebundenen Schlüssel ab. Für `opened`,
   `reclassified` und echte Statuswechsel ergänzt er die bisherige Identität
   aus Finding-ID, fachlicher Transitionsidentität, Rundennummer und
   Reviewerrolle um die Work-Unit-ID. Für reine Begründungsrevisionen bleibt
   die bereits Work-Unit-gebundene Identität
   `status_rationale:<work-unit-id>` bytegenau erhalten.
2. Die fachliche Transitionsidentität bleibt ein eigener Schlüsselbestandteil,
   sodass etwa Begründungsrevision, Reklassifizierung und späterer Abschluss
   innerhalb derselben Work Unit nicht zusammenfallen. Die Schlüssel dürfen
   syntaktisch verschieden aufgebaut sein, solange ihre fachliche und
   Work-Unit-bezogene Eindeutigkeit stabil bleibt.
3. Eine byte- und bedeutungsgleiche Wiederholung in derselben Work Unit
   verwendet denselben Schlüssel und bleibt über `ArtifactBridge.append()` ein
   No-op. Dieselbe Finding-ID, Runde, Rolle und Transitionsart in einer anderen
   Work Unit verwendet dagegen einen anderen Schlüssel und darf als eigener
   autoritativer Record angehängt werden.
4. Der Payload, seine `work_unit_id`, die Recordkette, `replay_findings()` und
   der State-v3-Spiegel bleiben semantisch deckungsgleich. Ein Konflikt oder
   eine Mirror-Abweichung bleibt ein fail-closed Fehler; keine Invariante wird
   gelockert.
5. Der nicht strukturierte Aufruf behält exakt seine bisherige
   Schlüsselidentität und sein Verhalten. Vorhandene Records mit früheren
   strukturierten oder Legacy-Schlüsseln werden ausschließlich gelesen; es
   gibt weder Rewrite noch heuristische Migration oder Protokollwechsel. Beim
   Resume über die Writer-Upgrade-Grenze prüft der Writer vor einem neuen
   strukturierten Append ausschließlich den deterministisch berechneten
   bisherigen Schlüssel derselben Transition: Bezeichnet dieser bereits
   denselben Logical-ID-, Fingerprint- und Payloadinhalt einschließlich
   `work_unit_id`, wird der vorhandene Record wiederverwendet. Ist der alte
   Schlüssel durch eine andere Work Unit belegt, wird er nicht übernommen; der
   neue Work-Unit-gebundene Schlüssel bleibt frei von dieser historischen
   Kollision. Weicht die Bedeutung dagegen innerhalb derselben Work Unit ab,
   bleibt das ein fail-closed Identitätskonflikt.

### Slice 1 - Findingtransition-Schlüssel an die Work Unit binden

**Exakter Änderungspfad**

- `src/orchestrator.py`
- `tests/test_orchestrator_runtime.py`

#### Umsetzung

1. Zentralisiere in
   `ProductionWorkflowDriver._persist_review_finding_transitions()` die
   Idempotenzidentität für den bereits ermittelten Transitionseintrag. Ergänze
   bei `structured=True` für `opened`, `reclassified` und echte
   `status_changed`-Übergänge die aus
   `active_state.current_work_unit_id` gewonnene Work-Unit-ID als ausdrücklich
   abgegrenzten Schlüsselbestandteil. Verwende dieselbe gebundene ID für
   Payload und Schlüssel und halte an, falls sie auf dem strukturierten Pfad
   nicht vor dem Append verfügbar ist.
2. Bewahre die bestehenden fachlichen Identitäten `opened`, `reclassified`,
   `status_changed` und `status_rationale` getrennt. Behalte für eine reine
   `OPEN -> OPEN`-Begründungsrevision die bestehende Identität
   `status_rationale:<work-unit-id>` unverändert; sie erfüllt die
   Work-Unit-Bindung bereits und muss bei einem Resume über die
   Writer-Upgrade-Grenze denselben Schlüssel ergeben. Ändere weder
   Übergangsermittlung noch Payload, Findingeigentum, Runde, Reviewer oder
   Fingerprint.
3. Berechne für die drei umgestellten Transitionsarten zusätzlich den exakt
   bisherigen strukturierten Schlüssel als eng begrenzten
   Kompatibilitätskandidaten. Prüfe vor dem Append in der autoritativen Kette:
   Nur wenn dieser Kandidat bereits denselben Logical-ID-, Fingerprint- und
   vollständigen Payloadinhalt einschließlich `work_unit_id` bezeichnet, gilt
   die Transition als schon persistiert und es entsteht kein zweiter Record.
   Bei fehlendem Kandidaten wird mit der neuen Work-Unit-Identität angehängt.
   Bei Belegung durch eine andere Work Unit wird die alte Identität nicht
   wiederverwendet und der neue Schlüssel angehängt; eine semantische
   Abweichung innerhalb derselben Work Unit hält weiterhin fail-closed an.
   Verändere oder ersetze keinen vorhandenen Record.
4. Lasse bei `structured=False` die bisherige Schlüsselbildung unverändert.
   Füge keine Recordfamilie, kein Schemafeld, keinen zweiten Store und keinen
   parallelen Mirror hinzu; Replay und Projektion bleiben unverändert, weil
   sie die alten Schlüssel bereits opaque lesen und die Work Unit aus dem
   Payload beziehen.
5. Ergänze fokussierte providerfreie Runtime-Regressionen, die den echten
   Writer über temporäre Repositories und den bestehenden `ArtifactStore`
   ausführen. Nutze synthetische Claude-Findings und Fake-/zählende Adapter;
   kein Test darf Netzwerk, Quota oder einen echten Provider berühren.

#### Akzeptanzkriterien

- Zweimaliges Persistieren derselben strukturierten Transition mit derselben
  Work Unit, Runde, Rolle, Payloadbedeutung und demselben Fingerprint erzeugt
  genau einen `FindingTransitionPayload`-Record.
- Der fokussierte Test
  `status_rationale_key_resume_boundary` legt zunächst für eine laufende Work
  Unit eine strukturierte `OPEN -> OPEN`-Begründungsrevision mit dem bisherigen
  `status_rationale:<work-unit-id>`-Schlüssel an und persistiert nach
  simulierter Writer-Aktualisierung dieselbe Transition erneut. Schlüssel und
  Record-ID bleiben identisch, die Kette enthält genau einen fachlichen Record
  und Replay sowie Findingprojektion bleiben unverändert. Entsprechende
  Upgrade-Grenzfälle für `opened`, `reclassified` und echten Statuswechsel
  weisen nach, dass ein semantisch identischer vorhandener Alt-Record
  wiederverwendet, ein durch eine andere Work Unit belegter Alt-Schlüssel aber
  nicht fälschlich übernommen wird und eine abweichende Bedeutung innerhalb
  derselben Work Unit fail-closed bleibt.
- Zwei Work Units persistieren dieselbe Finding-ID, Runde, Reviewerrolle und
  Transitionsart unter getrennten Schlüsseln als zwei gültige Records, ohne
  `ArtifactBridgeError`; beide Records bleiben über den autoritativen Replay
  der Gesamtkette nachvollziehbar und work-unit-spezifisch projizierbar.
- Eine `OPEN -> OPEN`-Revision mit neuer Statusbegründung und ein späterer
  `OPEN -> CLOSED`-Statuswechsel desselben Findings innerhalb einer Work Unit
  erzeugen getrennte Records. Replay und State-v3-Spiegel stimmen anschließend
  über die letzte Begründung und den terminalen Status überein.
- Reklassifizierung und Statuswechsel in derselben Work Unit erhalten
  verschiedene Idempotenzschlüssel und werden beide in Append-Reihenfolge
  projiziert.
- Ein simulierter Prozessabbruch nach vollständig persistiertem nativen
  Reviewergebnis wird über den vorhandenen Record-ahead-Recoverypfad
  fortgesetzt: Replay rekonstruiert denselben Findingstand wie der
  State-v3-Spiegel, und der zählende Fake-Adapter weist null zusätzliche
  Providerstarts nach.
- Eine synthetische historische Kette mit bisherigen
  `finding:<id>:<transition>:<round>:<reviewer>`- beziehungsweise bisherigen
  `status_rationale:<work-unit-id>`-Schlüsseln wird unverändert geladen und
  projiziert. Eine explizite Legacy-/`structured=False`-Regression bestätigt
  die unveränderte bisherige Schlüsselbildung und Semantik.
- Konfligierende Wiederverwendung eines gebundenen Schlüssels sowie
  Record-/Mirror-Divergenz halten weiterhin fail-closed an. Findingeigentum,
  Reviewerreihenfolge, Approval, Retrylimits, Provideroperations-ID und
  Evidenzmanifest bleiben unberührt.

#### Validierung und Übergabe

- Während der Implementierung nur die fokussierten providerfreien Tests in
  `tests/test_orchestrator_runtime.py` ausführen; Agenten führen keine
  vollständige Repositorymatrix aus und emittieren kein
  `VALIDATION_RESULT`.
- Vor Übergabe `git diff --check` ausführen.
- Der Orchestrator führt anschließend die vollständige konfigurierte
  Repositorymatrix aus und erstellt die fingerprintgebundene Attestierung.

## Stopbedingungen

Der Slice ist nicht implementierungsbereit beziehungsweise hält kontrolliert
an, falls die Work-Unit-ID nicht stabil vor dem Append aus dem autoritativen
State gewonnen werden kann, ein identischer Resume eine andere Identität
erhielte, historische Records geändert werden müssten, mehr als zwei
produktive Dateien oder eine neue Recordfamilie nötig würden oder eine
Record-/Mirror-, Findingeigentums-, Fingerprint- oder Providerattempt-Invariante
gelockert werden müsste. Der kleinste sichere Folgeschnitt wäre dann eine
separate, erneut zu planende Härtung der fehlenden autoritativen
Work-Unit-Bindungsgrenze; dieser Slice erweitert seinen Scope nicht.

## Orchestrator-Prüfprotokoll

### Review-Feedback von Claude

<!-- audit:claude-review:begin -->
### Ereignis 3: Runde 1

- Reviewer: `claude`
- Freigabe: `NO`
- Validierungsbindung: `plan-validation-cc01df72911b`
- Testdateien: keine
- Eigene Findings: `C-01`

### Ereignis 5: Runde 2

- Reviewer: `claude`
- Freigabe: `YES`
- Validierungsbindung: `plan-validation-858ca8e12c2c`
- Testdateien: keine
- Prüfdimensionen: Checked: (a) PLAN_ONLY handoff contract shape - exactly one contiguous '### Slice 1 - ...' heading and a standalone '**Exakter Änderungspfad**' section with only bullet repo-relative paths (src/orchestrator.py, tests/test_orchestrator_runtime.py), within the 2-productive-file cap; (b) coverage of all 8 assignment-mandated provider-free test scenarios (double-persist idempotency, cross-work-unit non-collision, rationale-revision-vs-status-change separation, reclassification-vs-status-change identity separation, resume-without-reprovider, legacy-chain readability, unstructured-path stability, git diff --check/matrix); (c) the C-01 resume/upgrade-boundary gap - now closed via the named compatibility-candidate mechanism and the explicitly named status_rationale_key_resume_boundary test plus described analogous cases for opened/reclassified/status_changed; (d) fail-closed preservation for cross-work-unit collisions and same-work-unit semantic drift; (e) no historical rewrite, no new record family, no schema/replay change; (f) authorized_paths conformance of the bundled diff (docs plan file + src/agent_adapters.py + src/native_review_request.py + src/workflow.py + the three listed test files, matching the request's authorized_paths exactly); (g) the two bundled hotfixes (anchor-provenance restriction to allow_anchors=False when anchor_origin is None, and PLAN-CONTRACT-INVALID→UNEXPECTED_FILE gate reframing plus pre-review scope-drift short-circuit that avoids reinvoking Codex/driver.validate_plan) are internally consistent with their new regression tests and with the already user-approved gate records for this exact fingerprint (858ca8e12c2c...).
- Größtes Restrisiko: The plan describes, but does not individually name, separate regression tests for the opened/reclassified/status_changed upgrade-boundary cases (only status_rationale_key_resume_boundary is named); if Slice 1's implementation under-covers one of those three transition kinds' compatibility-candidate branch (especially the 'old key occupied by a different work unit' non-reuse branch), a mid-flight resume after the writer upgrade could append a duplicate record for that transition type, reproducing the exact identity failure C-01 originally raised, and it would only surface at branch-final review rather than at this plan stage.
- Realistische Bruchbedingung: If the shipped Slice 1 implementation omits the compatibility-candidate lookup (or its cross-work-unit non-reuse branch) for any of opened, reclassified, or status_changed, a work unit whose transition was appended under the pre-hardening key and is then resumed after the writer upgrade will compute a different new key, fail to recognize the prior record via ArtifactBridge.append(), and append a semantically duplicate structured finding_transition record — violating Zielinvariante 5 and the assignment's own stop condition on resume identity stability.
- Eigene Findings: `C-01`

<!-- artifact-records:claude-review:begin -->
Semantischer Record-Digest: `13be3c9c416c76a207c58b560eb45196b364bca8d788085e892de607299e8378`

- 14. `ar1-199109a4d4bf69ef99b7389d91950d43cd7df62f5c54d4646f4ddfadf12876b6`: `denied`; Work-Unit `1`; Findings `C-01`; Fingerprint `cc01df72911b4866fcddaa331b759294aa2c9ba05abb84de3478045a0501bd98`; Transport `native-claude-review-v1`; Request `native-review-request-57403aaae049bcaf2ccc23090a1428102e68170e1a890b2cd564f2fd06cb3f45`; Response `93635091f2a8d1752fc32222751f19cba4726785ed29036a1a40c2256757ff37`
- 26. `ar1-7ab7d7302656abbc4c01fc5d2eb6dff646cce63f5025973461f6245798b4ffd9`: `approved`; Work-Unit `1`; Findings `C-01`; Fingerprint `858ca8e12c2c4fb3764ce064ae4bec02d9d7d6f63154a72cd8c4b6270a0adf32`; Transport `native-claude-review-v1`; Request `native-review-request-e158967336448fb8aa3b8190f228979ebd7b06884a816e63b5df8ffb9a4b9ac5`; Response `227f5a24d10037a3046c84cdd63b02a7e09ecdee8ba0d4ca731c2b555dedc5ec`
<!-- artifact-records:claude-review:end -->
<!-- audit:claude-review:end -->

### Review-Feedback von Antigravity

<!-- audit:antigravity-review:begin -->
### Ereignis 6: Runde 1

- Reviewer: `antigravity`
- Freigabe: `YES`
- Validierungsbindung: `plan-validation-858ca8e12c2c`
- Testdateien: keine
- Prüfdimensionen: Checked PLAN_ONLY contract shape (single Slice 1, canonical exact path heading with bulleted repo-relative paths within productive limit), repository grounding in ProductionWorkflowDriver._persist_review_finding_transitions for structured vs unstructured paths, byte-exact preservation of status_rationale:&lt;work-unit-id&gt;, compatibility-candidate check for opened/reclassified/status_changed across writer upgrades, all 8 assignment-mandated provider-free integration test scenarios in tests/test_orchestrator_runtime.py, and fail-closed invariants for replay/projection without schema changes.
- Größtes Restrisiko: The largest residual risk is implementation drift where the compatibility-candidate check is applied only to status_changed transitions or mishandles the cross-work-unit non-reuse branch for opened and reclassified transitions, leading to duplicate transition records or false collisions during in-flight resume.
- Realistische Bruchbedingung: If a run is resumed across a writer upgrade with an existing pre-hardening opened or reclassified transition in the chain and the writer does not check the legacy candidate key before appending the work-unit-bound key, ArtifactBridge.append() will append a duplicate record instead of recognizing the existing transition, violating Zielinvariante 5 and resume idempotency.
- Eigene Findings: keine

<!-- artifact-records:antigravity-review:begin -->
Semantischer Record-Digest: `13be3c9c416c76a207c58b560eb45196b364bca8d788085e892de607299e8378`

- 35. `ar1-7c2950b717b0cd74e87a76cbba1a450d65bd91b89149aec0ba379b0ee85d4840`: `approved`; Work-Unit `1`; Findings `C-01`; Fingerprint `858ca8e12c2c4fb3764ce064ae4bec02d9d7d6f63154a72cd8c4b6270a0adf32`; Transport `legacy-text`
<!-- artifact-records:antigravity-review:end -->
<!-- audit:antigravity-review:end -->

### Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
- `C-01` Antwort 1: **angenommen** — Der Arbeitsplan behält den bereits Work-Unit-gebundenen status_rationale-Schlüssel bytegenau bei und plant den geforderten Resume-Grenztest. Für umgestellte Transitionen wird ein vorhandener Altschlüssel nur bei exakter Identität derselben Work Unit wiederverwendet; Cross-Work-Unit-Kollisionen nutzen den neuen Schlüssel, während semantische Konflikte weiterhin fail-closed bleiben.

<!-- artifact-records:codex-responses:begin -->
Semantischer Record-Digest: `13be3c9c416c76a207c58b560eb45196b364bca8d788085e892de607299e8378`

- 20. `ar1-64b0e267ccb82baba87b3f1534f7d13154d1fabdb01da226e12c90ac12e7c01b`: `C-01` `responded` durch `codex`; `BLOCKER` / `open` — Der Arbeitsplan behält den bereits Work-Unit-gebundenen status_rationale-Schlüssel bytegenau bei und plant den geforderten Resume-Grenztest. Für umgestellte Transitionen wird ein vorhandener Altschlüssel nur bei exakter Identität derselben Work Unit wiederverwendet; Cross-Work-Unit-Kollisionen nutzen den neuen Schlüssel, während semantische Konflikte weiterhin fail-closed bleiben.
<!-- artifact-records:codex-responses:end -->
<!-- audit:codex-responses:end -->

### Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### Ereignis 1: `plan-validation-7ffdce534f7d`

- Diff-Fingerprint: `7ffdce534f7d6eb0d30801e9624ead61ec82de8f5260196353eafca32a4f83b9`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: internal plan contract passed
- Ausgabedigest: `2b00842f8da93ba9ffb1ef15cc81977b03003e3fff3e830237e169ad857a4569`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| internal:work-plan-contract | PASS | 0 | slices=1; planned_paths=1; changed_paths=1; future_slices=1; work_plan=docs/internal/phase-2-bootstrap-workunitgebundene-findingtransition-idempotenz-arbeitsplan.md |

### Ereignis 2: `plan-validation-cc01df72911b`

- Diff-Fingerprint: `cc01df72911b4866fcddaa331b759294aa2c9ba05abb84de3478045a0501bd98`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: internal plan contract passed
- Ausgabedigest: `a62091beba7e371be9a8b304fcd12134f235e0ecb92a52fc88a8669bb8cf57d2`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| internal:work-plan-contract | PASS | 0 | slices=1; planned_paths=1; changed_paths=5; future_slices=1; work_plan=docs/internal/phase-2-bootstrap-workunitgebundene-findingtransition-idempotenz-arbeitsplan.md |

### Ereignis 4: `plan-validation-858ca8e12c2c`

- Diff-Fingerprint: `858ca8e12c2c4fb3764ce064ae4bec02d9d7d6f63154a72cd8c4b6270a0adf32`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: internal plan contract passed
- Ausgabedigest: `c3dbe01104c2734316946e65a07f4081502711047b4b6687714a66db44b49318`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| internal:work-plan-contract | PASS | 0 | slices=1; planned_paths=1; changed_paths=7; future_slices=1; work_plan=docs/internal/phase-2-bootstrap-workunitgebundene-findingtransition-idempotenz-arbeitsplan.md |

<!-- artifact-records:validation-attestation:begin -->
Semantischer Record-Digest: `13be3c9c416c76a207c58b560eb45196b364bca8d788085e892de607299e8378`

- 2. `ar1-1035ba83e77f3cc42d3d3f0203403124a350428d50ea32c1e5ce7c886afb516b`: Providerinput `codex/codex_plan` = `allowed`; local_input_chars `42217/4000000`, local_input_bytes `42229/16000000`; local_input_digest `042ce205cc7b9cff762df19e631673c98cba5e46c10c1ff2165091a8aaba8529`, Policy `603e75566ee1fad99cd271d52bc8c2814387f55bf526699188eead5156cd325e`, Übergang `6a6c6d8acfd158ee26a904c93b747262ea5d6e9318bb4b99d5cd2eab1bb73ac8`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, local_input_largest_component `stdin_prompt`; local_input_component_count `2`; Komponenten `stdin_prompt=38345/38357, response_schema=3872/3872`
- 6. `ar1-06699c9ed12d5234cd181c01ca7532f3fcf271fc5b93170a1b8da67bf863f03d`: Attestierung durch `orchestrator`; Fingerprint `7ffdce534f7d6eb0d30801e9624ead61ec82de8f5260196353eafca32a4f83b9`
  - `pass` / Exit `0` / Output `2b00842f8da93ba9ffb1ef15cc81977b03003e3fff3e830237e169ad857a4569`: `argv` [`internal:work-plan-contract`]
- 7. `ar1-26f54f83e490a6875851b44e6c68f039551afc12c90bfdd259774faac0f3364b`: Providerinput `claude/claude_plan_review` = `allowed`; local_input_chars `44020/4000000`, local_input_bytes `44165/16000000`; local_input_digest `ca05f13cb47ab36eab849d3919d3cd7fdc2162af288e7709c3337e92d6c0d11c`, Policy `603e75566ee1fad99cd271d52bc8c2814387f55bf526699188eead5156cd325e`, Übergang `ebe47f8c8d2a4bbd2cf1bf0b9fc00fe381b879391ab9c8e9b95c32add13c2496`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, local_input_largest_component `request_chunk_001`; local_input_component_count `6`; Komponenten `request_chunk_001=24000/24069, request_chunk_002=13896/13972, packet_manifest=596/596, system_policy=574/574, response_schema=4724/4724, start_directive=230/230`
- 11. `ar1-854b0feb545c1407f7655b3946ffa04c19b52888896a77e9d3449e0a97828e9a`: Attestierung durch `orchestrator`; Fingerprint `cc01df72911b4866fcddaa331b759294aa2c9ba05abb84de3478045a0501bd98`
  - `pass` / Exit `0` / Output `a62091beba7e371be9a8b304fcd12134f235e0ecb92a52fc88a8669bb8cf57d2`: `argv` [`internal:work-plan-contract`]
- 12. `ar1-4779946b6ff36462eaa7c1e4c7cd5e55ac57782a89adb33c0a320f28fca9feba`: Providerinput `claude/claude_plan_review` = `allowed`; local_input_chars `55960/4000000`, local_input_bytes `56116/16000000`; local_input_digest `9ab33924184df7c47bafa1dfc553665a29d98935895a368cb1b7a9ff05ad9bc6`, Policy `603e75566ee1fad99cd271d52bc8c2814387f55bf526699188eead5156cd325e`, Übergang `6265acc84c9cbee1cc939b649e7dfd5c9851a162cb8935353268b00a4be43024`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, local_input_largest_component `evidence_asset_001`; local_input_component_count `6`; Komponenten `request_chunk_001=20186/20193, evidence_asset_001=29637/29786, packet_manifest=610/610, system_policy=574/574, response_schema=4723/4723, start_directive=230/230`
- 17. `ar1-d003bab8417f86855e1307e97aa7af6a6368052a3eecdc632545b55f5effd8b5`: Providerinput `codex/codex_plan_revision` = `allowed`; local_input_chars `46495/4000000`, local_input_bytes `46509/16000000`; local_input_digest `3a535e1e4cbf5e29d59b8e0434042fb880000bb5774a9b03bc21f1b2268f795e`, Policy `603e75566ee1fad99cd271d52bc8c2814387f55bf526699188eead5156cd325e`, Übergang `bb640cc5ed7f7778b8dca3ab2773114cad0a68af8389978f6b2726e30752c53b`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, local_input_largest_component `stdin_prompt`; local_input_component_count `2`; Komponenten `stdin_prompt=42623/42637, response_schema=3872/3872`
- 23. `ar1-f649f7b561dbb553e4e9a1634a12304e1f966dc31ec0c801ebb98eb58ddeb20f`: Attestierung durch `orchestrator`; Fingerprint `858ca8e12c2c4fb3764ce064ae4bec02d9d7d6f63154a72cd8c4b6270a0adf32`
  - `pass` / Exit `0` / Output `c3dbe01104c2734316946e65a07f4081502711047b4b6687714a66db44b49318`: `argv` [`internal:work-plan-contract`]
- 24. `ar1-9d180efb333833f39c2d426e294971021368bff9389ecbdb1ef0b53621cc5509`: Providerinput `claude/claude_plan_review` = `allowed`; local_input_chars `86781/4000000`, local_input_bytes `87023/16000000`; local_input_digest `b16a668bf0086f4c2a0eacd9976129e474183a8b739387bfd444a4fcad753ae2`, Policy `603e75566ee1fad99cd271d52bc8c2814387f55bf526699188eead5156cd325e`, Übergang `312641053195b17a0b4bd4e48c8d483565230f55aa0b26ffeb5cc0646c42e933`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, local_input_largest_component `evidence_asset_001`; local_input_component_count `6`; Komponenten `request_chunk_001=23017/23035, evidence_asset_001=57627/57851, packet_manifest=610/610, system_policy=574/574, response_schema=4723/4723, start_directive=230/230`
- 29. `ar1-8d425dc16e7483262656352bef3f5cd1d54e50a84df39e0d08f18fc1673bf821`: Providerinput `antigravity/antigravity_plan_review` = `allowed`; local_input_chars `97402/4000000`, local_input_bytes `97663/16000000`; local_input_digest `02d556d4241ece3cf4b3058c0ee2aeb01a9fbe019cfe6d6d420edc354e0bc28b`, Policy `603e75566ee1fad99cd271d52bc8c2814387f55bf526699188eead5156cd325e`, Übergang `26d7dcebcabd903f0d35168c0cfc5caf1aa5e01a9603bb330bdf27d5f1167ac6`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, local_input_largest_component `prompt_file`; local_input_component_count `3`; Komponenten `prompt_file=96743/97004, response_schema=146/146, start_directive=513/513`
- 32. `ar1-99712cfca596c70302af9f169c15b66331cdcf94be19e678bfaea447db1d4885`: Providerinput `antigravity/antigravity_plan_review` = `allowed`; local_input_chars `97402/4000000`, local_input_bytes `97663/16000000`; local_input_digest `02d556d4241ece3cf4b3058c0ee2aeb01a9fbe019cfe6d6d420edc354e0bc28b`, Policy `603e75566ee1fad99cd271d52bc8c2814387f55bf526699188eead5156cd325e`, Übergang `c050cdf60751420cc16fe378739f8d996d3428353b30749619732540311ff4ed`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, local_input_largest_component `prompt_file`; local_input_component_count `3`; Komponenten `prompt_file=96743/97004, response_schema=146/146, start_directive=513/513`
- Providerattempt-Summe Run `watch-20260823-170253.999599Z-a7e77c1efad8` / Operation `provider-operation-22617d13648cbf6a8c52e8c557ab963764365aee5f9cd274843ffa240bd6bd6c` (`claude/claude_plan_review`): Attempts `1`, offen `0`, Duration `336.385932` (bekannt `1`, unbekannt `0`); input_tokens=sum:16,known:1,unknown:0; tool_input_tokens=sum:0,known:0,unknown:1; cache_read_input_tokens=sum:294063,known:1,unknown:0; cache_creation_input_tokens=sum:67837,known:1,unknown:0; thinking_tokens=sum:0,known:0,unknown:1; output_tokens=sum:33473,known:1,unknown:0; total_tokens=sum:0,known:0,unknown:1; turns=sum:10,known:1,unknown:0; cost_usd=sum:0.6659265999999999,known:1,unknown:0
  - 28. `ar1-89b0b4945f05446d3928fec9cb7746548c82a4b55a6b698a8a65f7ac1660dcea`: Attempt `1` = `succeeded`; Messung `ar1-9d180efb333833f39c2d426e294971021368bff9389ecbdb1ef0b53621cc5509`; Duration `336.38593160698656`; Fehler `none`; Usage `input_tokens=16, tool_input_tokens=unknown, cache_read_input_tokens=294063, cache_creation_input_tokens=67837, thinking_tokens=unknown, output_tokens=33473, total_tokens=unknown, turns=10, cost_usd=0.6659265999999999`
- Providerattempt-Summe Run `watch-20260823-170253.999599Z-a7e77c1efad8` / Operation `provider-operation-32e7698515ca8fe85efe2ea7a3098f2f9c66bb6b9f0fcec7bafaebfd8a95f3b6` (`claude/claude_plan_review`): Attempts `1`, offen `0`, Duration `184.446507` (bekannt `1`, unbekannt `0`); input_tokens=sum:8,known:1,unknown:0; tool_input_tokens=sum:0,known:0,unknown:1; cache_read_input_tokens=sum:22676,known:1,unknown:0; cache_creation_input_tokens=sum:29371,known:1,unknown:0; thinking_tokens=sum:0,known:0,unknown:1; output_tokens=sum:16663,known:1,unknown:0; total_tokens=sum:0,known:0,unknown:1; turns=sum:5,known:1,unknown:0; cost_usd=sum:0.28966919999999996,known:1,unknown:0
  - 16. `ar1-d9ceaf859e2944693abee3409aef394f96e40bc704258a6d89e18a40aa099334`: Attempt `1` = `succeeded`; Messung `ar1-4779946b6ff36462eaa7c1e4c7cd5e55ac57782a89adb33c0a320f28fca9feba`; Duration `184.44650681997882`; Fehler `none`; Usage `input_tokens=8, tool_input_tokens=unknown, cache_read_input_tokens=22676, cache_creation_input_tokens=29371, thinking_tokens=unknown, output_tokens=16663, total_tokens=unknown, turns=5, cost_usd=0.28966919999999996`
- Providerattempt-Summe Run `watch-20260823-170253.999599Z-a7e77c1efad8` / Operation `provider-operation-583d562faa3acb29cf6ee0c0f97c74265260449a331ae1a2aee8cb237d6d7b00` (`antigravity/antigravity_plan_review`): Attempts `2`, offen `0`, Duration `88.553782` (bekannt `2`, unbekannt `0`); input_tokens=sum:289820,known:2,unknown:0; tool_input_tokens=sum:0,known:0,unknown:2; cache_read_input_tokens=sum:0,known:0,unknown:2; cache_creation_input_tokens=sum:0,known:0,unknown:2; thinking_tokens=sum:11551,known:2,unknown:0; output_tokens=sum:14703,known:2,unknown:0; total_tokens=sum:304523,known:2,unknown:0; turns=sum:3,known:2,unknown:0; cost_usd=sum:0,known:0,unknown:2
  - 31. `ar1-5b0891d4658da202e86c2ae6ea6bfb5210d515841ca159c29a834ee5698af3a1`: Attempt `1` = `failed`; Messung `ar1-8d425dc16e7483262656352bef3f5cd1d54e50a84df39e0d08f18fc1673bf821`; Duration `48.99171997397207`; Fehler `network`; Usage `input_tokens=137384, tool_input_tokens=unknown, cache_read_input_tokens=unknown, cache_creation_input_tokens=unknown, thinking_tokens=6261, output_tokens=7848, total_tokens=145232, turns=1, cost_usd=unknown`
  - 34. `ar1-d1fa540ae0124df13499e1180229ef6f5d4bd5f50ae452d329c06c0019b09d60`: Attempt `2` = `succeeded`; Messung `ar1-99712cfca596c70302af9f169c15b66331cdcf94be19e678bfaea447db1d4885`; Duration `39.56206157198176`; Fehler `none`; Usage `input_tokens=152436, tool_input_tokens=unknown, cache_read_input_tokens=unknown, cache_creation_input_tokens=unknown, thinking_tokens=5290, output_tokens=6855, total_tokens=159291, turns=2, cost_usd=unknown`
- Providerattempt-Summe Run `watch-20260823-170253.999599Z-a7e77c1efad8` / Operation `provider-operation-5b7fbc0dc3e069b71aab4a41e524ac7f4e31d95fd05ad17901487b11003e7d46` (`codex/codex_plan_revision`): Attempts `1`, offen `0`, Duration `149.079733` (bekannt `1`, unbekannt `0`); input_tokens=sum:0,known:0,unknown:1; tool_input_tokens=sum:0,known:0,unknown:1; cache_read_input_tokens=sum:0,known:0,unknown:1; cache_creation_input_tokens=sum:0,known:0,unknown:1; thinking_tokens=sum:0,known:0,unknown:1; output_tokens=sum:0,known:0,unknown:1; total_tokens=sum:0,known:0,unknown:1; turns=sum:0,known:0,unknown:1; cost_usd=sum:0,known:0,unknown:1
  - 21. `ar1-b35d46171bf4b12868c4954d31388de83c3007168d4564f32527be0631b910ee`: Attempt `1` = `succeeded`; Messung `ar1-d003bab8417f86855e1307e97aa7af6a6368052a3eecdc632545b55f5effd8b5`; Duration `149.0797327220207`; Fehler `none`; Usage `unknown`
- Providerattempt-Summe Run `watch-20260823-170253.999599Z-a7e77c1efad8` / Operation `provider-operation-f4ab9c0dcb486af9aee6d8bcf638acf12e81b9b463374bfbe9d19ba0dd2de103` (`claude/claude_plan_review`): Attempts `1`, offen `0`, Duration `126.992836` (bekannt `1`, unbekannt `0`); input_tokens=sum:6,known:1,unknown:0; tool_input_tokens=sum:0,known:0,unknown:1; cache_read_input_tokens=sum:9027,known:1,unknown:0; cache_creation_input_tokens=sum:23079,known:1,unknown:0; thinking_tokens=sum:0,known:0,unknown:1; output_tokens=sum:11276,known:1,unknown:0; total_tokens=sum:0,known:0,unknown:1; turns=sum:5,known:1,unknown:0; cost_usd=sum:0.2078984,known:1,unknown:0
  - 9. `ar1-da1715a3a4bff86d5e8446f251f7544785267731ce1dc8291342e44d9b737884`: Attempt `1` = `failed`; Messung `ar1-26f54f83e490a6875851b44e6c68f039551afc12c90bfdd259774faac0f3364b`; Duration `126.99283633904997`; Fehler `output`; Usage `input_tokens=6, tool_input_tokens=unknown, cache_read_input_tokens=9027, cache_creation_input_tokens=23079, thinking_tokens=unknown, output_tokens=11276, total_tokens=unknown, turns=5, cost_usd=0.2078984`
- Providerattempt-Summe Run `watch-20260823-170253.999599Z-a7e77c1efad8` / Operation `provider-operation-f7d4d5b28c3466d9401498d8ec44b727ef9d98fb0e12620cf4771b4a1a88b373` (`codex/codex_plan`): Attempts `1`, offen `0`, Duration `141.429851` (bekannt `1`, unbekannt `0`); input_tokens=sum:0,known:0,unknown:1; tool_input_tokens=sum:0,known:0,unknown:1; cache_read_input_tokens=sum:0,known:0,unknown:1; cache_creation_input_tokens=sum:0,known:0,unknown:1; thinking_tokens=sum:0,known:0,unknown:1; output_tokens=sum:0,known:0,unknown:1; total_tokens=sum:0,known:0,unknown:1; turns=sum:0,known:0,unknown:1; cost_usd=sum:0,known:0,unknown:1
  - 5. `ar1-9af0a7f6b25302eb30504bb7468853724090b01783653cc777e79f60726d3792`: Attempt `1` = `succeeded`; Messung `ar1-1035ba83e77f3cc42d3d3f0203403124a350428d50ea32c1e5ce7c886afb516b`; Duration `141.42985056398902`; Fehler `none`; Usage `unknown`
<!-- artifact-records:validation-attestation:end -->
<!-- audit:validation-attestation:end -->

### Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
- Teständerungsfreigabe: nicht erfasst.
- Pre-Mortems:
  - Ereignis 5: The most likely failure in three months is an implementation drift in Slice 1: the compatibility-candidate check gets implemented only for status_changed (the most visible case) or the 'occupied by a different work unit' branch is simplified to unconditional reuse of the old key, silently reintroducing either duplicate records on resume or cross-work-unit key collisions for opened/reclassified — because those two cases are only descriptively required in the plan's acceptance criteria rather than each bound to an explicitly named test, unlike status_rationale_key_resume_boundary.
  - Ereignis 6: The most likely failure in three months is an implementation gap in Slice 1 where the compatibility-candidate check is omitted for reclassification transitions or where cross-work-unit non-reuse falls back to unconditional key reuse, leading to duplicate transition records upon mid-flight resume across a writer upgrade.

<!-- artifact-records:test-approval-premortem:begin -->
Semantischer Record-Digest: `13be3c9c416c76a207c58b560eb45196b364bca8d788085e892de607299e8378`

- 10. `ar1-9e0b45c5671099908ad87c70d40e2c730ece292e7e183d78fd5ecc1a4aab59e6`: `unexpected-file` = `approved` durch `user`; Fingerprint `cc01df72911b4866fcddaa331b759294aa2c9ba05abb84de3478045a0501bd98` — Geprüfter Native-Review-Anchor-Hotfix 7845615 für Fingerprint<br>    cc01df72911b4866fcddaa331b759294aa2c9ba05abb84de3478045a0501bd98: Ohne gebundene anchor_origin verbietet das<br>    Provider-Schema nichtleere Anchors; Request-Digest, Bundle-Prüfung und Claude-Adapter verwenden dieselbe<br>    Schemaprojektion. Exakt vier Zusatzpfade geprüft; vollständige Suite mit 1205 Tests bestanden; git diff --check<br>    sauber.
- 22. `ar1-d7fc97d71c82bd2cad3aa9b8a1175209ccbf8353540143acfe6fe585ca2991ff`: `unexpected-file` = `approved` durch `user`; Fingerprint `858ca8e12c2c4fb3764ce064ae4bec02d9d7d6f63154a72cd8c4b6270a0adf32` — Geprüfter kumulierter Bootstrap-Hotfixstand 7845615 und 8104c32 für Fingerprint<br>    858ca8e12c2c4fb3764ce064ae4bec02d9d7d6f63154a72cd8c4b6270a0adf32: Native Review Anchors sind an anchor_origin<br>    gebunden; Request-Digest, Bundle und Claude-Adapter verwenden dasselbe Schema. Autorisierte Planrevisionen erzeugen<br>    bei verändertem Gesamtfingerprint ein neues exaktes Pfadgate und werden anschließend ohne erneuten Codex-Aufruf<br>    direkt Claude vorgelegt. Exakt sechs Zusatzpfade geprüft; vollständige Suite mit 1207 Tests bestanden; git diff<br>    --check sauber.
<!-- artifact-records:test-approval-premortem:end -->
<!-- audit:test-approval-premortem:end -->

### Findings-Lebenszyklus

<!-- audit:findings:begin -->
### `C-01` — `CLOSED`

- Quelle: `claude`; Runde 1
- Klasse: `BLOCKER`
- Finding: Slice 1 unifies the status_rationale idempotency key into the general structured key rule (finding-id + transition-identity + work-unit + round + reviewer), replacing the legacy status_rationale:&lt;work-unit-id&gt; scheme for new writes, but the plan defines no regression covering the resume/upgrade boundary: a work unit whose OPEN-&gt;OPEN rationale transition was already appended under the legacy key format, then resumed after the writer is upgraded to the unified key rule, would recompute a different key for the semantically identical transition and could append a duplicate record instead of being recognized by ArtifactBridge.append() as the same record. This is exactly the case the assignment's own stop condition forbids ('derselbe fachliche Record bei Resume eine andere Identitaet erhalten wuerde') and it is required by acceptance item 2 ('Dieselbe Transition derselben Work-Unit bleibt bei Wiederholung idempotent und erzeugt keinen zweiten Record'). The plan's acceptance criteria only test (a) fresh double-persist idempotency under the new scheme and (b) read-only replay of a purely historical/closed legacy chain; it never tests re-persisting the same in-flight transition for a work unit whose prior record already used the legacy status_rationale key. Slice 1 must either add an explicit regression proving that a mid-flight legacy-keyed status_rationale record is still recognized as the same transition after the key-rule change (e.g. by deriving the new key deterministically from the same inputs the legacy key already encoded, or by falling back to a lookup against existing legacy keys before treating the transition as new), or the plan must document why this transition boundary cannot occur for any resumable run and keep the legacy key format for status_rationale unchanged.
- Akzeptanztest: VALIDATE: ["python3","-m","pytest","tests/test_orchestrator_runtime.py","-k","status_rationale_key_resume_boundary","-v"]
- Statusbegründung: Round 2 resolves the finding by changing approach rather than patching the round-1 design: status_rationale keeps its existing status_rationale:&lt;work-unit-id&gt; key bytegenau (Zielinvariante 1, Slice-1 Umsetzung Punkt 2) instead of being folded into the general structured rule, so no legacy/new key mismatch can occur across a writer upgrade for that transition. For the three transition kinds newly work-unit-bound (opened, reclassified, real status_changed), Slice-1 Umsetzung Punkt 3 now specifies exactly the missing resume/upgrade-boundary mechanism: before appending under the new key, the writer computes the exact pre-hardening key as a narrow compatibility candidate and checks the authoritative chain; if that candidate already denotes the same logical-id, fingerprint, and full payload including work_unit_id, the existing record is reused (no duplicate); if occupied by a different work unit, the old identity is not reused and the new key is appended fresh; a semantic mismatch inside the same work unit stays a fail-closed identity conflict (Zielinvariante 5). Akzeptanzkriterien names the exact regression required by C-01's own acceptance test, status_rationale_key_resume_boundary (append under legacy key, re-persist after simulated writer upgrade, assert unchanged key/record-id/chain cardinality/replay/projection), and additionally requires analogous upgrade-boundary coverage for opened/reclassified/status_changed proving reuse-when-identical, non-reuse-when-cross-work-unit, and fail-closed-on-semantic-drift. This satisfies the stop condition text C-01 cited ('derselbe fachliche Record bei Resume eine andere Identitaet erhalten wuerde') and acceptance item 2. As the reporting reviewer I close C-01; Codex's ACCEPTED response is consistent with this plan text.

<!-- artifact-records:findings:begin -->
Semantischer Record-Digest: `13be3c9c416c76a207c58b560eb45196b364bca8d788085e892de607299e8378`

- 15. `ar1-212db87a44a91df34018379ba59e886d9faeec955d44da18276c087301e80dc2`: `C-01` `opened` durch `claude`; `BLOCKER` / `open` — Slice 1 unifies the status_rationale idempotency key into the general structured key rule (finding-id + transition-identity + work-unit + round + reviewer), replacing the legacy status_rationale:&lt;work-unit-id&gt; scheme for new writes, but the plan defines no regression covering the resume/upgrade boundary: a work unit whose OPEN-&gt;OPEN rationale transition was already appended under the legacy key format, then resumed after the writer is upgraded to the unified key rule, would recompute a different key for the semantically identical transition and could append a duplicate record instead of being recognized by ArtifactBridge.append() as the same record. This is exactly the case the assignment's own stop condition forbids ('derselbe fachliche Record bei Resume eine andere Identitaet erhalten wuerde') and it is required by acceptance item 2 ('Dieselbe Transition derselben Work-Unit bleibt bei Wiederholung idempotent und erzeugt keinen zweiten Record'). The plan's acceptance criteria only test (a) fresh double-persist idempotency under the new scheme and (b) read-only replay of a purely historical/closed legacy chain; it never tests re-persisting the same in-flight transition for a work unit whose prior record already used the legacy status_rationale key. Slice 1 must either add an explicit regression proving that a mid-flight legacy-keyed status_rationale record is still recognized as the same transition after the key-rule change (e.g. by deriving the new key deterministically from the same inputs the legacy key already encoded, or by falling back to a lookup against existing legacy keys before treating the transition as new), or the plan must document why this transition boundary cannot occur for any resumable run and keep the legacy key format for status_rationale unchanged.
- 20. `ar1-64b0e267ccb82baba87b3f1534f7d13154d1fabdb01da226e12c90ac12e7c01b`: `C-01` `responded` durch `codex`; `BLOCKER` / `open` — Der Arbeitsplan behält den bereits Work-Unit-gebundenen status_rationale-Schlüssel bytegenau bei und plant den geforderten Resume-Grenztest. Für umgestellte Transitionen wird ein vorhandener Altschlüssel nur bei exakter Identität derselben Work Unit wiederverwendet; Cross-Work-Unit-Kollisionen nutzen den neuen Schlüssel, während semantische Konflikte weiterhin fail-closed bleiben.
- 27. `ar1-764d36e2678381392ea13517b35ac64280e217002a3b8631ae8efa919156cfd9`: `C-01` `status_changed` durch `claude`; `BLOCKER` / `closed` — Round 2 resolves the finding by changing approach rather than patching the round-1 design: status_rationale keeps its existing status_rationale:&lt;work-unit-id&gt; key bytegenau (Zielinvariante 1, Slice-1 Umsetzung Punkt 2) instead of being folded into the general structured rule, so no legacy/new key mismatch can occur across a writer upgrade for that transition. For the three transition kinds newly work-unit-bound (opened, reclassified, real status_changed), Slice-1 Umsetzung Punkt 3 now specifies exactly the missing resume/upgrade-boundary mechanism: before appending under the new key, the writer computes the exact pre-hardening key as a narrow compatibility candidate and checks the authoritative chain; if that candidate already denotes the same logical-id, fingerprint, and full payload including work_unit_id, the existing record is reused (no duplicate); if occupied by a different work unit, the old identity is not reused and the new key is appended fresh; a semantic mismatch inside the same work unit stays a fail-closed identity conflict (Zielinvariante 5). Akzeptanzkriterien names the exact regression required by C-01's own acceptance test, status_rationale_key_resume_boundary (append under legacy key, re-persist after simulated writer upgrade, assert unchanged key/record-id/chain cardinality/replay/projection), and additionally requires analogous upgrade-boundary coverage for opened/reclassified/status_changed proving reuse-when-identical, non-reuse-when-cross-work-unit, and fail-closed-on-semantic-drift. This satisfies the stop condition text C-01 cited ('derselbe fachliche Record bei Resume eine andere Identitaet erhalten wuerde') and acceptance item 2. As the reporting reviewer I close C-01; Codex's ACCEPTED response is consistent with this plan text.

### Native convergence summary

| Finding | Work units | Rounds | Fingerprints | Claude decisions | Codex dispositions | Final status |
|---|---|---|---|---|---|---|
| `C-01` | `1` | `1` | `cc01df72911b4866fcddaa331b759294aa2c9ba05abb84de3478045a0501bd98`<br>`309a4eca229ee4381283f630b97abbbc835b545d551babcd2c7c3c06aa98c27f`<br>`858ca8e12c2c4fb3764ce064ae4bec02d9d7d6f63154a72cd8c4b6270a0adf32` | `opened:open`<br>`status_changed:closed` | `accepted` | `closed` |
<!-- artifact-records:findings:end -->
<!-- audit:findings:end -->

### Entscheidungstabelle

<!-- audit:decision-table:begin -->
| ID | Quelle | Finding | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|---|
| C-01 | claude | Slice 1 unifies the status_rationale idempotency key into the general structured key rule (finding-id + transition-identity + work-unit + round + reviewer), replacing the legacy status_rationale:&lt;work-unit-id&gt; scheme for new writes, but the plan defines no regression covering the resume/upgrade boundary: a work unit whose OPEN-&gt;OPEN rationale transition was already appended under the legacy key format, then resumed after the writer is upgraded to the unified key rule, would recompute a different key for the semantically identical transition and could append a duplicate record instead of being recognized by ArtifactBridge.append() as the same record. This is exactly the case the assignment's own stop condition forbids ('derselbe fachliche Record bei Resume eine andere Identitaet erhalten wuerde') and it is required by acceptance item 2 ('Dieselbe Transition derselben Work-Unit bleibt bei Wiederholung idempotent und erzeugt keinen zweiten Record'). The plan's acceptance criteria only test (a) fresh double-persist idempotency under the new scheme and (b) read-only replay of a purely historical/closed legacy chain; it never tests re-persisting the same in-flight transition for a work unit whose prior record already used the legacy status_rationale key. Slice 1 must either add an explicit regression proving that a mid-flight legacy-keyed status_rationale record is still recognized as the same transition after the key-rule change (e.g. by deriving the new key deterministically from the same inputs the legacy key already encoded, or by falling back to a lookup against existing legacy keys before treating the transition as new), or the plan must document why this transition boundary cannot occur for any resumable run and keep the legacy key format for status_rationale unchanged. | BLOCKER | angenommen | erledigt: Round 2 resolves the finding by changing approach rather than patching the round-1 design: status_rationale keeps its existing status_rationale:&lt;work-unit-id&gt; key bytegenau (Zielinvariante 1, Slice-1 Umsetzung Punkt 2) instead of being folded into the general structured rule, so no legacy/new key mismatch can occur across a writer upgrade for that transition. For the three transition kinds newly work-unit-bound (opened, reclassified, real status_changed), Slice-1 Umsetzung Punkt 3 now specifies exactly the missing resume/upgrade-boundary mechanism: before appending under the new key, the writer computes the exact pre-hardening key as a narrow compatibility candidate and checks the authoritative chain; if that candidate already denotes the same logical-id, fingerprint, and full payload including work_unit_id, the existing record is reused (no duplicate); if occupied by a different work unit, the old identity is not reused and the new key is appended fresh; a semantic mismatch inside the same work unit stays a fail-closed identity conflict (Zielinvariante 5). Akzeptanzkriterien names the exact regression required by C-01's own acceptance test, status_rationale_key_resume_boundary (append under legacy key, re-persist after simulated writer upgrade, assert unchanged key/record-id/chain cardinality/replay/projection), and additionally requires analogous upgrade-boundary coverage for opened/reclassified/status_changed proving reuse-when-identical, non-reuse-when-cross-work-unit, and fail-closed-on-semantic-drift. This satisfies the stop condition text C-01 cited ('derselbe fachliche Record bei Resume eine andere Identitaet erhalten wuerde') and acceptance item 2. As the reporting reviewer I close C-01; Codex's ACCEPTED response is consistent with this plan text. |

<!-- artifact-records:decision-table:begin -->
Semantischer Record-Digest: `13be3c9c416c76a207c58b560eb45196b364bca8d788085e892de607299e8378`

| Seq | Record | Typ | Status | Logische ID | Revision | Fingerprint |
|---:|---|---|---|---|---:|---|
| 1 | `ar1-aec02ec6e1af82692fb3117fad59309765b864af0d35483152412ad6c6b824bc` | `task` | `accepted` | `task-contract` | 1 | `contract:309a4eca229ee4381283f630b97abbbc835b545d551babcd2c7c3c06aa98c27f` |
| 2 | `ar1-1035ba83e77f3cc42d3d3f0203403124a350428d50ea32c1e5ce7c886afb516b` | `provider_input_measurement` | `measured` | `provider-input-1-codex_plan` | 1 | `implementation:309a4eca229ee4381283f630b97abbbc835b545d551babcd2c7c3c06aa98c27f` |
| 3 | `ar1-e251990f4bded48738b45870bf1f51a7b2eaa5a5bb2e07e09158e963e7707d25` | `provider_attempt` | `started` | `provider-operation-f7d4d5b28c3466d9401498d8ec44b727ef9d98fb0e12620cf4771b4a1a88b373-1` | 1 | `implementation:309a4eca229ee4381283f630b97abbbc835b545d551babcd2c7c3c06aa98c27f` |
| 4 | `ar1-9bb6fa4509abed7db51c464fc388e85e37ff16e1b19bb29030a0d2e0310cb5ca` | `agent_result` | `ready` | `agent-1-codex_plan-1` | 1 | `contract:309a4eca229ee4381283f630b97abbbc835b545d551babcd2c7c3c06aa98c27f` |
| 5 | `ar1-9af0a7f6b25302eb30504bb7468853724090b01783653cc777e79f60726d3792` | `provider_attempt` | `succeeded` | `provider-operation-f7d4d5b28c3466d9401498d8ec44b727ef9d98fb0e12620cf4771b4a1a88b373-1` | 2 | `implementation:309a4eca229ee4381283f630b97abbbc835b545d551babcd2c7c3c06aa98c27f` |
| 6 | `ar1-06699c9ed12d5234cd181c01ca7532f3fcf271fc5b93170a1b8da67bf863f03d` | `validation_attestation` | `attested` | `plan-validation-7ffdce534f7d` | 1 | `implementation:7ffdce534f7d6eb0d30801e9624ead61ec82de8f5260196353eafca32a4f83b9` |
| 7 | `ar1-26f54f83e490a6875851b44e6c68f039551afc12c90bfdd259774faac0f3364b` | `provider_input_measurement` | `measured` | `provider-input-1-claude_plan_review` | 1 | `implementation:309a4eca229ee4381283f630b97abbbc835b545d551babcd2c7c3c06aa98c27f` |
| 8 | `ar1-c77468e69e89a41fae60b7dc83dec8b2c9908df45e34bf8c1a501b93458f9c30` | `provider_attempt` | `started` | `provider-operation-f4ab9c0dcb486af9aee6d8bcf638acf12e81b9b463374bfbe9d19ba0dd2de103-1` | 1 | `implementation:309a4eca229ee4381283f630b97abbbc835b545d551babcd2c7c3c06aa98c27f` |
| 9 | `ar1-da1715a3a4bff86d5e8446f251f7544785267731ce1dc8291342e44d9b737884` | `provider_attempt` | `failed` | `provider-operation-f4ab9c0dcb486af9aee6d8bcf638acf12e81b9b463374bfbe9d19ba0dd2de103-1` | 2 | `implementation:309a4eca229ee4381283f630b97abbbc835b545d551babcd2c7c3c06aa98c27f` |
| 10 | `ar1-9e0b45c5671099908ad87c70d40e2c730ece292e7e183d78fd5ecc1a4aab59e6` | `gate` | `decided` | `gate-unexpected_file-cc01df72911b` | 1 | `implementation:cc01df72911b4866fcddaa331b759294aa2c9ba05abb84de3478045a0501bd98` |
| 11 | `ar1-854b0feb545c1407f7655b3946ffa04c19b52888896a77e9d3449e0a97828e9a` | `validation_attestation` | `attested` | `plan-validation-cc01df72911b` | 1 | `implementation:cc01df72911b4866fcddaa331b759294aa2c9ba05abb84de3478045a0501bd98` |
| 12 | `ar1-4779946b6ff36462eaa7c1e4c7cd5e55ac57782a89adb33c0a320f28fca9feba` | `provider_input_measurement` | `measured` | `provider-input-1-claude_plan_review` | 2 | `implementation:309a4eca229ee4381283f630b97abbbc835b545d551babcd2c7c3c06aa98c27f` |
| 13 | `ar1-a453b515515d7408bc81bfc65dd1f7980ded986875d8ac6599345d8a28e4a6eb` | `provider_attempt` | `started` | `provider-operation-32e7698515ca8fe85efe2ea7a3098f2f9c66bb6b9f0fcec7bafaebfd8a95f3b6-1` | 1 | `implementation:309a4eca229ee4381283f630b97abbbc835b545d551babcd2c7c3c06aa98c27f` |
| 14 | `ar1-199109a4d4bf69ef99b7389d91950d43cd7df62f5c54d4646f4ddfadf12876b6` | `review` | `decided` | `review-claude-1-1` | 1 | `implementation:cc01df72911b4866fcddaa331b759294aa2c9ba05abb84de3478045a0501bd98` |
| 15 | `ar1-212db87a44a91df34018379ba59e886d9faeec955d44da18276c087301e80dc2` | `finding_transition` | `recorded` | `finding-C-01` | 1 | `implementation:cc01df72911b4866fcddaa331b759294aa2c9ba05abb84de3478045a0501bd98` |
| 16 | `ar1-d9ceaf859e2944693abee3409aef394f96e40bc704258a6d89e18a40aa099334` | `provider_attempt` | `succeeded` | `provider-operation-32e7698515ca8fe85efe2ea7a3098f2f9c66bb6b9f0fcec7bafaebfd8a95f3b6-1` | 2 | `implementation:309a4eca229ee4381283f630b97abbbc835b545d551babcd2c7c3c06aa98c27f` |
| 17 | `ar1-d003bab8417f86855e1307e97aa7af6a6368052a3eecdc632545b55f5effd8b5` | `provider_input_measurement` | `measured` | `provider-input-1-codex_plan_revision` | 1 | `implementation:309a4eca229ee4381283f630b97abbbc835b545d551babcd2c7c3c06aa98c27f` |
| 18 | `ar1-6f720f4d293f6c7b3945299f2ffa44d236e11a3ad0ddf77db698b51ee9853b9f` | `provider_attempt` | `started` | `provider-operation-5b7fbc0dc3e069b71aab4a41e524ac7f4e31d95fd05ad17901487b11003e7d46-1` | 1 | `implementation:309a4eca229ee4381283f630b97abbbc835b545d551babcd2c7c3c06aa98c27f` |
| 19 | `ar1-15d02fdf00a11cf0be15a658f0e986a9a3efafa3b0cd066c294b5053ed831a2c` | `agent_result` | `ready` | `agent-1-codex_plan_revision-2` | 1 | `contract:309a4eca229ee4381283f630b97abbbc835b545d551babcd2c7c3c06aa98c27f` |
| 20 | `ar1-64b0e267ccb82baba87b3f1534f7d13154d1fabdb01da226e12c90ac12e7c01b` | `finding_transition` | `recorded` | `finding-C-01` | 2 | `implementation:309a4eca229ee4381283f630b97abbbc835b545d551babcd2c7c3c06aa98c27f` |
| 21 | `ar1-b35d46171bf4b12868c4954d31388de83c3007168d4564f32527be0631b910ee` | `provider_attempt` | `succeeded` | `provider-operation-5b7fbc0dc3e069b71aab4a41e524ac7f4e31d95fd05ad17901487b11003e7d46-1` | 2 | `implementation:309a4eca229ee4381283f630b97abbbc835b545d551babcd2c7c3c06aa98c27f` |
| 22 | `ar1-d7fc97d71c82bd2cad3aa9b8a1175209ccbf8353540143acfe6fe585ca2991ff` | `gate` | `decided` | `gate-unexpected_file-858ca8e12c2c` | 1 | `implementation:858ca8e12c2c4fb3764ce064ae4bec02d9d7d6f63154a72cd8c4b6270a0adf32` |
| 23 | `ar1-f649f7b561dbb553e4e9a1634a12304e1f966dc31ec0c801ebb98eb58ddeb20f` | `validation_attestation` | `attested` | `plan-validation-858ca8e12c2c` | 1 | `implementation:858ca8e12c2c4fb3764ce064ae4bec02d9d7d6f63154a72cd8c4b6270a0adf32` |
| 24 | `ar1-9d180efb333833f39c2d426e294971021368bff9389ecbdb1ef0b53621cc5509` | `provider_input_measurement` | `measured` | `provider-input-1-claude_plan_review` | 3 | `implementation:309a4eca229ee4381283f630b97abbbc835b545d551babcd2c7c3c06aa98c27f` |
| 25 | `ar1-64e01b16096a83dada9faec4a6842fc0018affa436a1d7f50eebd60235b3e56b` | `provider_attempt` | `started` | `provider-operation-22617d13648cbf6a8c52e8c557ab963764365aee5f9cd274843ffa240bd6bd6c-1` | 1 | `implementation:309a4eca229ee4381283f630b97abbbc835b545d551babcd2c7c3c06aa98c27f` |
| 26 | `ar1-7ab7d7302656abbc4c01fc5d2eb6dff646cce63f5025973461f6245798b4ffd9` | `review` | `decided` | `review-claude-1-2` | 1 | `implementation:858ca8e12c2c4fb3764ce064ae4bec02d9d7d6f63154a72cd8c4b6270a0adf32` |
| 27 | `ar1-764d36e2678381392ea13517b35ac64280e217002a3b8631ae8efa919156cfd9` | `finding_transition` | `recorded` | `finding-C-01` | 3 | `implementation:858ca8e12c2c4fb3764ce064ae4bec02d9d7d6f63154a72cd8c4b6270a0adf32` |
| 28 | `ar1-89b0b4945f05446d3928fec9cb7746548c82a4b55a6b698a8a65f7ac1660dcea` | `provider_attempt` | `succeeded` | `provider-operation-22617d13648cbf6a8c52e8c557ab963764365aee5f9cd274843ffa240bd6bd6c-1` | 2 | `implementation:309a4eca229ee4381283f630b97abbbc835b545d551babcd2c7c3c06aa98c27f` |
| 29 | `ar1-8d425dc16e7483262656352bef3f5cd1d54e50a84df39e0d08f18fc1673bf821` | `provider_input_measurement` | `measured` | `provider-input-1-antigravity_plan_review` | 1 | `implementation:309a4eca229ee4381283f630b97abbbc835b545d551babcd2c7c3c06aa98c27f` |
| 30 | `ar1-04d7f04bc6ce9c5ef6c7ebe1a378373e83f3ad6d6b997e48fcbc659ce36b5034` | `provider_attempt` | `started` | `provider-operation-583d562faa3acb29cf6ee0c0f97c74265260449a331ae1a2aee8cb237d6d7b00-1` | 1 | `implementation:309a4eca229ee4381283f630b97abbbc835b545d551babcd2c7c3c06aa98c27f` |
| 31 | `ar1-5b0891d4658da202e86c2ae6ea6bfb5210d515841ca159c29a834ee5698af3a1` | `provider_attempt` | `failed` | `provider-operation-583d562faa3acb29cf6ee0c0f97c74265260449a331ae1a2aee8cb237d6d7b00-1` | 2 | `implementation:309a4eca229ee4381283f630b97abbbc835b545d551babcd2c7c3c06aa98c27f` |
| 32 | `ar1-99712cfca596c70302af9f169c15b66331cdcf94be19e678bfaea447db1d4885` | `provider_input_measurement` | `measured` | `provider-input-1-antigravity_plan_review` | 2 | `implementation:309a4eca229ee4381283f630b97abbbc835b545d551babcd2c7c3c06aa98c27f` |
| 33 | `ar1-4ed21c4f4ad14a6dcee3745f55f13454a6e5980b27ce09955b60c62140137cbc` | `provider_attempt` | `started` | `provider-operation-583d562faa3acb29cf6ee0c0f97c74265260449a331ae1a2aee8cb237d6d7b00-2` | 1 | `implementation:309a4eca229ee4381283f630b97abbbc835b545d551babcd2c7c3c06aa98c27f` |
| 34 | `ar1-d1fa540ae0124df13499e1180229ef6f5d4bd5f50ae452d329c06c0019b09d60` | `provider_attempt` | `succeeded` | `provider-operation-583d562faa3acb29cf6ee0c0f97c74265260449a331ae1a2aee8cb237d6d7b00-2` | 2 | `implementation:309a4eca229ee4381283f630b97abbbc835b545d551babcd2c7c3c06aa98c27f` |
| 35 | `ar1-7c2950b717b0cd74e87a76cbba1a450d65bd91b89149aec0ba379b0ee85d4840` | `review` | `decided` | `review-antigravity-1-1` | 1 | `implementation:858ca8e12c2c4fb3764ce064ae4bec02d9d7d6f63154a72cd8c4b6270a0adf32` |
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
Semantischer Record-Digest: `13be3c9c416c76a207c58b560eb45196b364bca8d788085e892de607299e8378`

- 4. `ar1-9bb6fa4509abed7db51c464fc388e85e37ff16e1b19bb29030a0d2e0310cb5ca`: Agentresult `codex` / `ready`; Work-Unit `1`; Tests keine; Transport `native-codex-v1`; Request `native-codex-request-c5efcf6b503d2e9e1d11f8354365df18dddaa7986be157889eb47a26788ac68f`; Response `a05ffb2715f4de107568228f4c9da916e631e28c8e7891c419b4a6337065c190`; Fingerprint `309a4eca229ee4381283f630b97abbbc835b545d551babcd2c7c3c06aa98c27f`
- 19. `ar1-15d02fdf00a11cf0be15a658f0e986a9a3efafa3b0cd066c294b5053ed831a2c`: Agentresult `codex` / `ready`; Work-Unit `1`; Tests keine; Transport `native-codex-v1`; Request `native-codex-request-bd89f8e6b27f8850a1af6fe8de435dc7b8fd7fb144eafe9ed78f88ae9a46e179`; Response `06962807cac07a8efd708717d6aed5ede7c6b53d817559582fc26e602f327840`; Fingerprint `309a4eca229ee4381283f630b97abbbc835b545d551babcd2c7c3c06aa98c27f`
<!-- artifact-records:approval-status:end -->
<!-- audit:approval-status:end -->
