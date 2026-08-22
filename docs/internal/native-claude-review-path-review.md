# Review – Nativer Claude-Reviewpfad

## Bootstrapstatus

- Verfahren: manueller Bootstrap außerhalb von `run_task`
- Branch: `feature/native-claude-review-path`
- Plan: `docs/internal/native-claude-review-path-arbeitsplan.md`
- von Claude geprüfter Plan-SHA-256:
  `15f014160dbb0d8b8b3409e2971183b26447e1624396d6260ab52c9c599781e1`
- aktueller Plan-SHA-256 nach ausschließlich nichtblockierenden
  Akzeptanzpräzisierungen:
  `64fef1c6ef015e38396a15da66d05ed2e99e8fa99f8b759f49db91d21e46a699`
- Claude-Planreview: **APPROVED**
- Antigravity: `NOT_RUN (manual bootstrap exception)`

## Dauerhafte Benutzerfreigabe für notwendige Scopeerweiterungen

Der Benutzer hat am 22. August 2026 für den aktuellen und die folgenden
manuellen Bootstrap-Schnitte ausdrücklich erlaubt, bei fachlicher
Notwendigkeit die geplante Dateianzahl zu überschreiten und neue Dateien
anzulegen. Jede Erweiterung bleibt begründungspflichtig, wird vollständig
getestet und Claude im nächsten Review als Bestandteil desselben eingefrorenen
Stands vorgelegt.

## Repository-grounded Planungsbefund

- Der vorhandene native Resultatkern ist provider- und persistenzfrei und
  erfolgreich abgeschlossen.
- Seine bisherige Request-ID bindet nicht die konkrete Evidenz, Allowlist,
  Branchbasis oder das Response-Schema. Der Livepfad muss deshalb einen
  vollständigen kanonischen Request als neue Autoritätsgrenze einführen.
- Der bestehende Claude-Adapter nutzt strukturiertes Provideroutput nur als
  Hülle um Markertext. Runtime, Workflow und Recovery müssen den nativen Pfad
  ausdrücklich von dieser Legacylogik trennen.
- Ein crashsicherer Replay darf nicht allein aus einer untypisierten Logdatei
  erraten werden. Der generische Reviewrecord benötigt eine
  rückwärtskompatible native Request-/Responsebindung.
- `structured-v1` bleibt der Persistenzmodus; nur der Claude-Reviewertransport
  wird für neue Pilotläufe optional und unveränderlich nativ gebunden.

## Claude-Planreview – Freigabe

- geprüfter Plandigest:
  `15f014160dbb0d8b8b3409e2971183b26447e1624396d6260ab52c9c599781e1`
- Entscheidung: `APPROVED`
- Provider: Claude Sonnet, Effort `high`, read-only
- Laufzeit: rund 315 Sekunden
- Kosten laut Providerhülle: 2,3866 USD
- Antwort: genau ein gegen ein geschlossenes JSON-Schema validiertes Objekt;
  keine Textmarker und kein Reparaturaufruf
- Prompt-SHA-256:
  `b5319509793b40e39ff502102c36b28205a1e55590834a3ff5db131e6ff3421f`
- Output-Schema-SHA-256:
  `88eba73db173dd39f6d8fbed8c55282ba1eafb0e2d60e0552c0c4c1c9f960d70`
- Antigravity: `NOT_RUN (manual bootstrap exception)`

Claude fand keine Blocker und drei nichtblockierende Observations. Alle drei
wurden vor Implementierungsbeginn als eng begrenzte Präzisierungen übernommen;
Produktivscope, Slices und Architekturentscheidung blieben unverändert.

### C-01 – OBSERVATION – falscher Methodenanker

Der Plan nannte `WorkflowEngine._review()`, während die tatsächliche
Reviewsteuerung in `WorkflowEngine._run_review()` liegt.

Disposition: korrigiert. Repositorybefund und Slice-2-Umsetzung nennen jetzt
den existierenden Methodenanker.

### C-02 – OBSERVATION – produktive Requestbindung noch zu optional

Der Plan verlangte die vollständige Request-ID, legte aber nicht fest, wie der
produktive Pfad die ältere engere `NativeReviewContext.request_id` strukturell
ausschließt. Ein optionaler Override hätte einen stillen Rückfall ermöglicht.

Disposition: präzisiert. Der Builder liefert einen eigenen immutable
`BoundNativeReviewContext`; Live-, Persistenz- und Recovery-APIs akzeptieren
ausschließlich diesen Typ. Der alte providerunabhängige Kontext bleibt nur für
den isolierten Kern verfügbar. Ein ProductionWorkflowDriver-naher Negativtest
weist die alte engere ID im Livepfad ab.

### C-03 – OBSERVATION – große Evidenz und Inputbudget

Die Formulierung ließ offen, ob große Evidenz direkt in ein einzelnes
Requestobjekt eingebettet und dadurch der vorhandene gemessene Chunktransport
umgangen werden könnte.

Disposition: präzisiert. Kleine Evidenz bleibt eingebettet; große Evidenz wird
an einer festen Grenze content-addressiert ausgelagert. Request, jede
Evidenzdatei, Manifest, Systemvertrag, Response-Schema und Startdirektive sind
eigene `ProviderInputMeasurement`-Komponenten. Zufällige Runtimepfade gehen
nicht in die semantischen Digests ein.

### Schema-validierte Claude-Antwort (inhaltstreu dokumentiert)

```json
{
  "reviewer": "claude",
  "plan_digest": "15f014160dbb0d8b8b3409e2971183b26447e1624396d6260ab52c9c599781e1",
  "decision": "APPROVED",
  "findings": [
    {
      "id": "C-01",
      "severity": "OBSERVATION",
      "description": "Section 2 states 'src/workflow.py::_review() besitzt bereits die richtige Autoritätsfolge' but no method named `_review` exists in workflow.py; the actual method implementing plan/slice/final review orchestration and authority ordering (validate -> persist record -> mirror state/checkpoint) is `_run_review()` (confirmed at src/workflow.py:1897, calling `_record_review` at line 2001/2338). The exact-change-path correctly lists `src/workflow.py` and the described authority sequence is factually accurate for `_run_review`, so this does not change scope or invariants, but the misnamed anchor could cause Codex to search for a non-existent symbol during implementation.",
      "acceptance_test": "Before implementing Slice 2, Codex confirms in the frozen slice digest or an implementation note that the modified method is `WorkflowEngine._run_review()` (not `_review()`), and the review document references the correct symbol name so a reader can locate the authority sequence without ambiguity."
    },
    {
      "id": "C-02",
      "severity": "OBSERVATION",
      "description": "Section 3.1.5 requires that `NativeReviewContext` take its expected `request_id` 'ausschließlich aus dem validierten Requestbuilder' for a live call, while direct provider-independent core tests may keep using the existing narrower local-context id. The current `NativeReviewContext.request_id` is a pure property computed only from fields already present on the dataclass. The plan does not specify the mechanism by which the production path substitutes the full-request-derived id without silently permitting the narrower id to pass in a live call.",
      "acceptance_test": "Slice 1 introduces an explicit, unit-tested mechanism such that the production Claude adapter/runtime path cannot construct a usable live review context without the full-request-derived id, and a dedicated ProductionWorkflowDriver-near test rejects the legacy narrower-context id."
    },
    {
      "id": "C-03",
      "severity": "OBSERVATION",
      "description": "The request evidence manifest supports embedded content or a content-addressed snapshot reference, but the plan did not explicitly reconcile this with the existing provider-input-budget-measured, chunked file/manifest transport. Embedding a large final-review diff in one JSON request could breach the established hard budget.",
      "acceptance_test": "Native evidence reuses the existing measured chunked file transport above a bounded size, every component is counted by `measure_provider_input`, and no evidence path bypasses the provider input budget."
    }
  ],
  "review_evidence": {
    "checked_dimensions": [
      "full request and circular digest binding",
      "native adapter/runtime separation from text markers",
      "immutable opt-in state binding",
      "ReviewPayload persistence and record-ahead replay",
      "crash, quota, invalid output and exactly-once finding transitions",
      "input budget, snapshot, attempt telemetry and preflight preservation",
      "historical state, record, CLI, watch and text-adapter compatibility",
      "slice scope and productive path completeness",
      "no-parser and no-second-Claude-call acceptance coverage"
    ],
    "largest_residual_risk": "A live or recovery branch could accidentally construct only the older narrow context request id unless the production API structurally requires the fully bound request context.",
    "break_condition": "The approval would fail if the live path accepted the narrow context id, silently migrated protocol state, resent full evidence for repeated repair, re-invoked Claude during record-ahead recovery, parsed text markers, or omitted a necessary productive path."
  },
  "pre_mortem": "The most likely implementation failure is divergence between live request construction and recovery reconstruction, followed by a large-evidence path that escapes the established provider input budget."
}
```

Die längeren Originalformulierungen von C-02, C-03, Reviewevidenz und
Pre-Mortem wurden in der eingerückten Darstellung nur typografisch verdichtet;
Entscheidung, IDs, Klassen, Akzeptanzinhalt und Disposition sind vollständig
erhalten. Die unveränderte Providerantwort war während dieses Reviews die
schema-validierte `structured_output`-Quelle.

## Implementierungsreview Slice 1

### Transporthärtung vor der ersten gültigen Entscheidung

Die ersten drei Dogfood-Anläufe erzeugten keine gültige Reviewentscheidung:

1. Die Claude-API wies das Resultatschema vor dem Modellstart ab, weil am Root
   `type: object` fehlte.
2. Die API wies danach ein Root-`oneOf` ab. Daraus entstand der geschlossene,
   digestgebundene `{result: <native result>}`-Providerwrapper; die vollständige
   lokale Schema- und Domänenprüfung blieb unverändert.
3. Der erste schema-valide Modelloutput verwendete wegen eines zu allgemeinen
   Findingpatterns `A-01`. Die lokale Reviewerautorität wies ihn mit
   `finding-id-invalid` ab. Das Providerschema bindet seither Reviewerrolle und
   `C-*`-Präfix; der Request nennt zusätzlich die nächste Finding-ID und der
   Domänenkern erzwingt eine zusammenhängende Folge.

Diese drei Anläufe wurden nicht als Freigabe gewertet. Der anschließend neu
eingefrorene Stand wurde vollständig erneut validiert und nativ geprüft.

### Gültiger nativer Slice-Review

- Slice-Fingerprint:
  `b82f4ca24ef824151c0168a41e55b1462fdaf097301979824928e464bbe47371`
- Request-ID und Request-SHA-256:
  `native-review-request-184c077c0032c410fdd23834c41b6186e454d5b5f817fa7f565a6b80b31e8c30`
- Providerinput-SHA-256:
  `004333272be3f010ef10f58e4800cf30fb78dde2d2cb93b7eff221edd2f618c4`
- Providerinput: 125.987 Zeichen, 126.184 Bytes, 9 vollständig gemessene
  Komponenten; keine Budgetverletzung
- Validierung: 1.080 Tests bestanden in 123,87 Sekunden; fokussiert 130 Tests
  bestanden; `git diff --check` sauber
- Provider: Claude Sonnet, Effort `high`, read-only
- Laufzeit: 309,94 Sekunden
- Kosten laut Providerhülle: 0,8250 USD
- Entscheidung: `approved`
- Antigravity: `NOT_RUN (manual bootstrap exception)`

Claude meldete zwei nichtblockierende Observations. C-01 verlangt eine
explizite Fehlermeldung für einen ohne Settings konstruierten nativen Adapter;
C-02 verlangt, dass das immutable Bundle eingebettete Evidenzdigests und
-größen selbst nachprüft. Beide wurden unmittelbar implementiert und werden
im folgenden nativen Korrekturreview Claude zur Schließung vorgelegt.

### Rohe native Claude-Antwort

```json
{"anchors":[],"decision":"approved","new_findings":[{"acceptance_test":{"kind":"prose","text":"Calling NativeClaudeReviewAdapter() with no settings (or settings=None) raises a clear, typed construction error instead of an AttributeError originating from _BaseAdapter.__init__ dereferencing settings.name."},"finding_class":"OBSERVATION","finding_id":"C-01","summary":"NativeClaudeReviewAdapter.__init__ declares settings: AgentSettings | None = None, but immediately forwards it to _BaseAdapter.__init__, which unconditionally reads settings.name/binary/model without a None check. Constructing the adapter without settings therefore raises an unhandled AttributeError instead of a typed, actionable error. Every current call site (ClaudeAdapter.native_review_adapter() and the tests) always supplies settings, so this is dormant today, not a live defect."},{"acceptance_test":{"kind":"prose","text":"A NativeReviewRequestBundle built from a canonical_json document containing an inline evidence entry whose declared sha256 or byte_count does not match its 'content' string is rejected at construction/validation time, mirroring the existing NativeReviewEvidenceAsset check applied to content_ref assets."},"finding_class":"OBSERVATION","finding_id":"C-02","summary":"NativeReviewRequestBundle.__post_init__ re-verifies content_ref evidence assets against the manifest via NativeReviewEvidenceAsset's own digest/byte_count check, but performs no equivalent semantic check that each inline evidence_manifest entry's declared sha256/byte_count actually matches its embedded 'content' string. Only build_native_review_request() guarantees this by construction. request_id itself still changes if the raw content bytes differ, so this is not currently exploitable through the sole production entry point, but a future non-builder construction path (e.g. a recovery/replay helper) could accept an inline entry with an internally inconsistent digest without rejection."}],"pre_mortem":"If this merges as-is, the request/response contract itself is sound: request_id is a full SHA-256 binding over branch, base, scope, attestation, evidence and the exact provider response schema; BoundNativeReviewContext demonstrably rejects the legacy narrow id; the provider projection pins reviewer=claude and C-* finding ids; extraction rejects any non-{'result'} envelope, is_error truthy/missing, permission denials, and request_id mismatches without touching marker text; and the full and focused suites are attested green (1080 and 130 passed) with a clean git diff --check. The residual risk is latent construction-time defensiveness rather than a live defect: nothing in Slice 1's authorized paths currently instantiates NativeClaudeReviewAdapter without settings or assembles a NativeReviewRequestBundle outside the builder, so today's behavior matches the plan's invariants. This would only surface if a later Slice 2 recovery/replay path reconstructs either type independently of the tested constructors, at which point the missing guard would either crash ungracefully or silently accept internally inconsistent inline-evidence metadata; the current test suite would not catch either because no test exercises those bypass paths.","reclassifications":[],"request_id":"native-review-request-184c077c0032c410fdd23834c41b6186e454d5b5f817fa7f565a6b80b31e8c30","result_type":"review_result","review_evidence":{"break_condition":"A future caller constructs NativeClaudeReviewAdapter() directly without settings (crashing with an unguarded AttributeError instead of a typed error), or assembles/replays a NativeReviewRequestBundle outside build_native_review_request() with an inline evidence entry whose declared sha256/byte_count is inconsistent with its content and has that inconsistency accepted silently.","dimensions":"Request-identity binding and digest determinism (native_review_request.py, schemas/native-agent-review-request-v1.schema.json); BoundNativeReviewContext enforcement rejecting the legacy narrow context id (native_review_contract.py, test_native_review_request.py); provider-side schema tightening to reviewer=claude and C-* finding ids plus removal of $schema/$id/response-string (native_review_provider_response_schema); content-addressed large-evidence chunking and stable provider-input measurement across all channels (agent_adapters.py NativeClaudeReviewAdapter, test_agent_adapters.py); structured_output extraction with no STATE-V3/marker parsing and strict {'result'} envelope enforcement plus is_error/permission_denials fail-closed handling; typed AgentOutputError classification in run_native_review_agent without a legacy marker/repair fallback; compact repair request omitting evidence/authorized_paths/acceptance_criteria; contiguous C-* new-finding numbering anchored to review_contract.next_finding_id.","largest_residual_risk":"Two invariants are only guaranteed by the sole intended production entry points rather than being independently re-verified inside the reusable types themselves: (1) NativeClaudeReviewAdapter silently assumes non-None settings instead of validating them, and (2) NativeReviewRequestBundle does not itself recompute sha256/byte_count for inline evidence content the way it does for content_ref assets. Both are currently unreachable because build_native_review_request() and native_review_adapter() always supply correct values, and tampered content still changes request_id end-to-end, so neither weakens the binding guarantees this slice actually promises."},"reviewer":"claude","schema_version":"native-agent-review-result-v1","status_changes":[]}
```

### Nativer Korrekturreview zu C-01 und C-02

- Slice-Fingerprint:
  `eddb2a45f785d845396c094e926ca4924c7e93452fbb0c2d76512b93820213a2`
- Request-ID und Request-SHA-256:
  `native-review-request-b7678a0f7aa4ae90de2fee0c3632f9088b8ef3949eb11dc126a76d801c4b7def`
- Providerinput-SHA-256:
  `5e42175d5a0186505cc5bc937aad74548a612132e693c375e1405eda5541404d`
- Providerinput: 147.515 Zeichen, 147.810 Bytes, 10 vollständig gemessene
  Komponenten; keine Budgetverletzung
- Validierung: 1.082 Tests bestanden in 124,08 Sekunden; fokussiert 132 Tests
  bestanden; `git diff --check` sauber
- Provider: Claude Sonnet, Effort `high`, read-only
- Laufzeit: 359,92 Sekunden
- Kosten laut Providerhülle: 0,9274 USD
- Entscheidung: `approved`
- Findingstatus: C-01 `CLOSED`, C-02 `CLOSED`, keine neuen Findings
- Antigravity: `NOT_RUN (manual bootstrap exception)`

Ein erster Aufruf derselben Korrektur enthielt einen neuen
Validierungsbefehl, wurde aber ausschließlich wegen eines unvollständigen
manuellen Requestkontexts (`validation_command_prefixes` fehlte) lokal als
`acceptance-invalid` abgewiesen. Produktcode und Slice-Fingerprint änderten
sich nicht; der hier dokumentierte Wiederholungsrequest bindet die erlaubte
Pytest-Befehlsfamilie vollständig.

#### Rohe native Claude-Antwort

```json
{"anchors":[{"anchor_id":"native-claude-adapter-requires-settings","expected":"TypeError raised with message 'NativeClaudeReviewAdapter requires explicit Claude AgentSettings' before any AgentSettings attribute is dereferenced","input_fixture":"NativeClaudeReviewAdapter() and NativeClaudeReviewAdapter(None)","tolerance":"Exact exception type must be TypeError, not AttributeError; message prefix must be preserved"},{"anchor_id":"inline-evidence-metadata-self-check","expected":"NativeReviewRequestError with code evidence-invalid and message containing 'metadata differs from content' raised during __post_init__","input_fixture":"A NativeReviewRequestBundle reconstructed directly (bypassing build_native_review_request) from a canonical_json document whose evidence_manifest[0] has a tampered sha256 while its inline content is unchanged, with request_id/request_digest recomputed to stay internally consistent","tolerance":"Must trigger purely from bundle construction, independent of the builder, and before the bundle is otherwise usable"}],"decision":"approved","new_findings":[],"pre_mortem":"The most likely way this round's hardening later breaks is at the Slice 2 integration boundary: if WorkflowEngine, ProductionWorkflowDriver, or the record-ahead recovery path reconstructs a NativeReviewContext/BoundNativeReviewContext or a NativeReviewRequestBundle independently of build_native_review_request() and native_review_context_binding() (for example inside a bespoke recovery helper), it could bypass the very digest/identity and inline-evidence self-check guarantees C-01 and C-02 just hardened here, silently accepting a narrower or stale request id, or re-deriving evidence without the reconstructed-bundle checks now enforced in this file. Slice 2 review should explicitly re-exercise both regressions against whatever new construction paths recovery introduces.","reclassifications":[],"request_id":"native-review-request-b7678a0f7aa4ae90de2fee0c3632f9088b8ef3949eb11dc126a76d801c4b7def","result_type":"review_result","review_evidence":{"break_condition":"This approval would not hold if any productive path could construct or accept a plain NativeReviewContext.request_id (the narrower local id) instead of BoundNativeReviewContext.request_id; if extract_output accepted a structured_output envelope with keys beyond the sole 'result' object, a missing/mismatched request_id, or a truthy/missing is_error without raising; if a directly reconstructed NativeReviewRequestBundle could carry inline evidence whose sha256/byte_count disagreed with its content without being rejected; or if NativeClaudeReviewAdapter could be constructed without explicit AgentSettings and fail with an untyped AttributeError instead of the typed TypeError now enforced.","dimensions":"Full request/response identity binding via BoundNativeReviewContext and parse_bound_native_contract_result (rejects the legacy narrow NativeReviewContext.request_id); separation of the native adapter from legacy marker/repair logic (NativeClaudeReviewAdapter.build_command and prepare_provider_input hard-fail with RuntimeError, prepare_native_provider_input/extract_output are the sole native entry points); envelope fail-closed handling for is_error, permission_denials, and a strict {'result'} structured_output shape; request_id cross-checked both inside extract_output (against the request bound at prepare time) and again inside run_native_review_agent via parse_bound_native_contract_result; C-01 explicit-settings construction guard; C-02 reconstructed-bundle inline-evidence self-verification; contiguous C-*-only new-finding numbering enforced via next_native_finding_id/_validate_response_events; provider-input measurement covering every request chunk, evidence asset, manifest, system policy, response schema, and start directive, with digest stability proven against randomized runtime paths; authorized_paths/test_files scope conformance for this round's diff and new files; full-suite (1082 passed) and focused-suite (130 passed) green attestation with a clean git diff --check.","largest_residual_risk":"Slice 1's native adapter/runtime primitives (NativeClaudeReviewAdapter, run_native_review_agent, BoundNativeReviewContext) are exercised only at the adapter/runtime unit level in this round; they are not yet wired into WorkflowEngine._run_review(), ProductionWorkflowDriver, ReviewPayload persistence, or record-ahead recovery, which is explicitly Slice 2 scope. The demonstrated request-id/digest binding and marker-free extraction therefore have not yet been proven under real crash, quota, or resume fault injection against a persisted review record and State-v3 mirror."},"reviewer":"claude","schema_version":"native-agent-review-result-v1","status_changes":[{"finding_id":"C-01","rationale":"NativeClaudeReviewAdapter.__init__ now raises TypeError('NativeClaudeReviewAdapter requires explicit Claude AgentSettings') when settings is None, before delegating to _BaseAdapter.__init__ (which previously dereferenced settings.name unconditionally). _BaseAdapter.__init__ now also stores self.settings so native_review_adapter() can pass it forward. Verified by the direct-constructor regression test_native_claude_adapter_requires_explicit_settings covering both NativeClaudeReviewAdapter() and NativeClaudeReviewAdapter(None), and by the attested full suite (1082 passed) and focused suite (130 passed) with clean git diff --check.","status":"CLOSED"},{"finding_id":"C-02","rationale":"NativeReviewRequestBundle.__post_init__ now iterates every inline evidence_manifest entry and rejects any whose declared sha256/byte_count disagree with its embedded content, mirroring the existing content_ref NativeReviewEvidenceAsset digest check. This closes the reconstructed-bundle gap: a bundle assembled outside build_native_review_request() (e.g. by a future recovery/replay helper) can no longer carry an internally inconsistent inline evidence entry. Verified by the reconstructed-bundle regression test_request_bundle_rejects_inconsistent_inline_evidence_metadata, which constructs a NativeReviewRequestBundle directly from a tampered canonical document and asserts the exact 'metadata differs from content' rejection, and by the attested green full suite.","status":"CLOSED"}]}
```

## Implementierungsreview Slice 2

### Erster nativer Slice-2-Review

- Slice-Fingerprint:
  `5e998958261cffb463ddc6ec189799e954d2e0b5bf20466e0ec7136f8292d592`
- Request-ID:
  `native-review-request-3f8760d00e3d1328739f113ab796d99a3986418a410c87052e7348b2d6837805`
- Providerinput-SHA-256:
  `c5d6bfbb895c3c8c4fd71c8511c7874fa4b5a7fbd13947652e0e1ac7bc6b3e03`
- Providerinput: 261.114 Zeichen, 261.727 Bytes, 12 vollständig
  gemessene Komponenten; keine Budgetverletzung
- Validierung: 1.092 Tests bestanden in 168,98 Sekunden; fokussiert
  190 Tests bestanden; `git diff --check` sauber
- Provider: Claude Sonnet, Effort `high`, read-only
- Laufzeit: 414,71 Sekunden; Kosten laut Providerhülle: 1,3816 USD
- Entscheidung: `approved`
- Findings: C-03 und C-04, beide `OBSERVATION`

C-03 beanstandete, dass gemessene Evidenzdateien und Request-Chunks denselben
Komponentennamen trugen. C-04 beanstandete den erfundenen Platzhalter
`MISSING` bei einem fehlenden Codex-Finalbericht. Beide wurden unmittelbar
behoben. Zusätzlich wurde Claudes im `REVIEW_EVIDENCE` benanntes Restrisiko
durch rundengebundene native Response-Logpfade gehärtet.

### Korrekturreview zu C-03 und C-04

- Korrektur-Fingerprint:
  `9519b79c538f04ab72788b2aafa8bd9a95b0430defe69922ef5f5639787f4b29`
- Request-ID:
  `native-review-request-836d30c82db8411ddb003a489530b39e0b1b6f08444fed6bd1836e59571a6c7e`
- Providerinput-SHA-256:
  `769fa96c4a311fa1f2bda9a037137853b38e64241f3f7813b6f9bfb27d4076db`
- Providerinput: 76.232 Zeichen und Bytes, 6 Komponenten; keine
  Budgetverletzung
- Validierung: 1.098 Tests bestanden in 179,95 Sekunden; fokussiert
  193 Tests bestanden; `git diff --check` sauber
- Provider: Claude Sonnet, Effort `high`, read-only
- Laufzeit: 133,31 Sekunden; Kosten laut Providerhülle: 0,3968 USD
- Entscheidung: `denied`
- Findingstatus: C-03 `CLOSED`, C-04 `CLOSED`
- Neues Finding: C-05 `BLOCKER`

Claude fand im tatsächlichen Aufrufpfad einen Typfehler, den der isolierte
Driver-Test nicht erfasste: `_run_review()` übergab `history.findings` an eine
Recovery-API, die vollständige `WorkflowHistory` und deren `events` benötigt.
Der reale Resume hätte deshalb mit `AttributeError` abgebrochen.

### Schließungsreview zu C-05

- Korrektur-Fingerprint:
  `5e85496d2b8042a5a8f454619fca24664e4c2b17f6c3343e01be020abb0114a4`
- Request-ID:
  `native-review-request-834e0788a1a03dfdcf734910e3125005a190707e8b0d171aec79a72460cce270`
- Providerinput-SHA-256:
  `2f5763eb1ef7de18d55eaf5abd2caac56652c5c757834ce55632d1d5804e2b50`
- Providerinput: 33.775 Zeichen und Bytes, 6 Komponenten; keine
  Budgetverletzung
- Validierung: 1.099 Tests bestanden in 177,02 Sekunden; der direkte
  Workflow- und isolierte Driver-Recoverytest bestanden; `git diff --check`
  sauber
- Provider: Claude Sonnet, Effort `high`, read-only
- Laufzeit: 102,59 Sekunden; Kosten laut Providerhülle: 0,2307 USD
- Entscheidung: `approved`
- Findingstatus: C-05 `CLOSED`; keine neuen Findings

Der neue Regressionstest führt `WorkflowEngine._run_review()` selbst aus,
verlangt ein Objekt mit `.events`, verbietet jeden Provideraufruf und weist
nach, dass die wiederhergestellte Entscheidung genau einmal persistiert und
bis zum nächsten Reviewstep gespiegelt wird.

### Schema-validierte Claude-Entscheidungen

Die drei kanonischen Providerantworten wurden direkt gegen
`native-agent-review-result-v1` und die jeweilige gebundene Request-ID
validiert. Das Audit dokumentiert ihre vollständige fachliche Disposition;
die sehr langen `review_evidence`-, `pre_mortem`- und Anchortexte sind bereits
in den unmittelbar vorangehenden Abschnitten inhaltstreu wiedergegeben.

| Request-ID | Entscheidung | Findingdisposition |
|---|---|---|
| `native-review-request-3f8760d00e3d1328739f113ab796d99a3986418a410c87052e7348b2d6837805` | `approved` | C-03 und C-04 neu als `OBSERVATION` |
| `native-review-request-836d30c82db8411ddb003a489530b39e0b1b6f08444fed6bd1836e59571a6c7e` | `denied` | C-03 und C-04 `CLOSED`; C-05 neu als `BLOCKER` |
| `native-review-request-834e0788a1a03dfdcf734910e3125005a190707e8b0d171aec79a72460cce270` | `approved` | C-05 `CLOSED`; keine neuen Findings |

Abweichung vom Bootstrap-Auditvertrag: Die direkte Hilfsausführung schrieb
die drei vollständigen kanonischen Responsebytes nicht zusätzlich in eine
dauerhafte Repositorydatei, sondern nur in den kontrollierten Terminalkanal.
Sie wurden dort vollständig schema- und requestgebunden verarbeitet. Dieser
Auditverlust betrifft nicht die Entscheidung oder Implementierung, muss aber
im nächsten nativen Paket durch ein obligatorisches Responseartefakt vor dem
ersten weiteren Seiteneffekt geschlossen werden.

## Codex-Abschluss-Selbstcheck und Benutzerentscheidung

Der branchweite Codex-Selbstcheck prüfte Plananforderungen, vollständigen Diff,
CLI-/Statebindung, Providerinput, Persistenzreihenfolge, Reviewarten und
Recovery. Er fand nach C-05 keinen weiteren konkreten Defekt. Dieser
Selbstcheck ist ausdrücklich keine Eigenfreigabe.

Der Benutzer entschied am 22. August 2026 aus Quotagründen, den separaten
Claude-Finalreview dieses Pakets auszulassen und direkt mit dem nativen
Codex-Pfad fortzufahren. Die nach dem letzten Claude-Review ergänzte
programmatische Parametrisierung bindet Plan-, Slice- und Finalrequestart;
sie änderte keinen Produktivcode.

## Ergänzendes externes Antigravity-Abschlussreview

Der Benutzer ließ außerhalb des manuellen Bootstrapvertrags zusätzlich einen
branchweiten Antigravity-Selbstcheck ausführen. Dieses Review ist keine
State-v3-Reviewentscheidung, besitzt keine orchestratorseitig persistierte
Request-/Fingerprintbindung und ersetzt deshalb weder einen Claude-
Finalreview noch das im Bootstrap ausdrücklich ausgesetzte orchestrierte
Antigravity-Review. Es ist als ergänzende externe Abnahmemeinung dokumentiert.

Antigravity bewertete den nativen Claude-Pilotpfad positiv und hob vier Punkte
hervor:

- Der native `NativeClaudeReviewAdapter` verwendet das echte JSON-Schema und
  ruft auf diesem Pilotpfad keinen Markerparser für `STATUS`, `REVIEWER` oder
  Findingmarker auf. Der historische Claude-Textadapter bleibt bewusst
  erhalten; die Aussage gilt daher nicht als Entfernung aller Legacyparser.
- Request-ID und `BoundNativeReviewContext` schließen Branch, Basiscommit,
  Allowlist, Attestierung, Evidenzmanifest und Responseschema kryptografisch
  zusammen.
- Record-ahead Recovery kann eine persistierte native Reviewentscheidung nach
  einem Crash ohne zweiten Claude-Aufruf wiederherstellen.
- Der im direkten Claude-Korrekturreview gefundene C-05-Aufruftypfehler war ein
  realer Resume-Defekt und wurde vor der Pilotfreigabe geschlossen.

Als verbleibende Risiken beziehungsweise Folgeschritte bestätigte das externe
Review die bereits dokumentierte Agentenasymmetrie und den fehlenden separaten
Rohantwortnachweis des manuellen Hilfsaufrufs. Es empfiehlt deshalb als
nächsten Schnitt native Codex-Ergebnisse und verlangt für den Produktivpfad
ein dauerhaftes kanonisches Roh-JSON-Artefakt vor der Ergebnisverarbeitung.
Beide Punkte sind verbindliche Invarianten des nachfolgenden Arbeitsplans
`native-codex-result-path-arbeitsplan.md`.

Antigravity sprach eine Merge-Empfehlung aus. Merge und Commit bleiben davon
unberührt ausdrücklich benutzerautorisierte Git-Transaktionen.

## Abschlussstatus

- Claude-Planreview: `APPROVED`
- Claude-Slice- und Korrekturreviews: alle Findings C-01 bis C-05 `CLOSED`
- Separater Claude-Finalreview: `NOT_RUN (explicit user quota decision)`
- Codex-Abschluss-Selbstcheck: abgeschlossen, keine Eigenfreigabe
- Orchestriertes Antigravity-Review: `NOT_RUN (manual bootstrap exception)`
- Externer Antigravity-Selbstcheck: positiv, ergänzend und nicht State-v3-autorisierend
- letzte Vollsuite einschließlich Reviewarten-Parametrisierung: 1.102 Tests bestanden
- Commit/Merge: nicht autorisiert und nicht ausgeführt
