# Arbeitsplan – Native-only-Transport, Textparser-Retirement und Evidenzeffizienz

> Archiviert nach Abschluss des Arbeitspakets; nichtautoritative historische Entwicklungsevidenz.

TARGET_BRANCH: feature/native-only-transport-and-efficiency

BASE_COMMIT: 9a7da9c66ee187a9044fbf3ff3e92a6b24e216cd

STATUS: REVISED_FOR_CLAUDE_PLAN_REVIEW_ROUND_4

## 1. Auftrag

Der Orchestrator wird nach dem abgeschlossenen Provider-Retirement auf einen
einzigen Agententransport festgelegt: Codex und Claude kommunizieren in jedem
neuen Planungs-, Implementierungs-, Korrektur- und Abschlusslauf ausschließlich
über ihre request-spezifischen nativen JSON-Verträge. Die derzeit noch
optionalen Native-Schalter, produktiv erreichbaren Textmarkerzweige,
Normalisierer und LLM-gestützten Vertragsreparaturen werden entfernt.

Auf diesem geschlossenen Transport wird anschließend die Eingabeevidenz
deterministisch verkleinert. Vollständige Pläne, alte Markerprompts, bereits
geprüfte Diffs und nicht betroffene Findings dürfen nicht erneut an einen
Provider übertragen werden, wenn ein aus denselben autoritativen Quellen
abgeleitetes, digestgebundenes Slice- oder Korrekturpaket ausreicht.

Das Paket verfolgt damit drei messbare Endzustände:

1. Kein neuer oder fortgesetzter Lauf kann einen textuellen Agentenresultat-
   oder Reviewtransport auswählen.
2. Kein produktiver Quellpfad parst Markertext als Codex- oder
   Claude-Entscheidung oder startet eine Textvertragsreparatur.
3. Native Requests enthalten nur die für die aktuelle Operation notwendige,
   content-addressierte Evidenz; strukturelle Regressionstests weisen die
   entfernten Duplikate und die resultierende Größenreduktion nach.

## 2. Ausgangsbefund

### 2.1 Bereits erreicht

- `structured-v2` und die append-only Recordkette sind die technische
  Autorität.
- Codex-Resultate und Claude-Reviews besitzen geschlossene, request-spezifische
  JSON-Verträge.
- Korrekturreviews erhalten bereits das Delta seit dem gebundenen
  Startfingerprint.
- Finalreview-Evidenz blendet die deterministische Auditprojektion aus und
  ersetzt sie durch eine digestgebundene Zusammenfassung.
- Reviewer-Snapshots sind selektiv; Validierungsattestierungen werden je
  relevantem Fingerprint wiederverwendet.
- Der kompakte Live-Output unterdrückt rohe Provider-JSON-Zeilen und rendert
  Fortschritt sowie Abschlussergebnisse lokal. Roadmap-Punkt 3.1 ist damit
  bereits umgesetzt und wird in diesem Paket nur gegen Regression geschützt.

### 2.2 Noch produktiv erreichbare Altarchitektur

- Ohne `--native-codex-results` und `--native-claude-reviews` beginnen neue
  Läufe weiterhin mit dem Legacy-Texttransport.
- `WorkflowEngine` besitzt parallele Zweige für native Resultate und
  Markertext, einschließlich `normalize_*`, `validate_*` und textuellem
  Dual-Write.
- `CodexAdapter` und `ClaudeAdapter` sind Textadapter, von denen die nativen
  Adapter derzeit erben und zu denen die Agentregistry zuerst auflöst.
- `prompts.py`, `contracts.py`, `agent_runtime.py` und der Produktionsdriver
  enthalten Markergrammatik, Textnormalisierung oder eine erneute
  Providerinvokation zur Vertragsreparatur.
- `ProtocolBinding` erlaubt native Transporte weiterhin einzeln oder gar nicht
  und modelliert nicht mehr unterstützte Protokollmodi.
- Historische Agentresultat- und Reviewrecords ohne native Request- und
  Antwortbindung bleiben im aktiven v2-Schema lesbar.

### 2.3 Noch vorhandene Eingabeduplikate

- Der native Codex-Request transportiert weiterhin den vollständigen alten
  Markerprompt als `workflow-prompt`-Evidenz.
- Implementierungs- und Korrekturaufrufe erhalten zusätzlich den vollständigen
  freigegebenen Arbeitsplan, obwohl Pfadgrenze, aktueller Slice und betroffene
  Findings bereits typisiert vorliegen.
- Größen- und Komponentenmessungen werden persistiert, es fehlen jedoch
  Regressionen, die operationenbezogen redundante Evidenz verhindern und die
  tatsächliche Reduktion als Byte-/Zeichenproxy nachweisen.
- Kosten-, Token- und Laufzeitdaten sind in Providerattempt-Records vorhanden,
  werden aber noch nicht als kompakte operationenbezogene Entwicklung in der
  Menschenansicht verdichtet.

## 3. Ausführungsmodus und Umfang

Dieses Paket verändert Transportauswahl, Agentadapter, Workflowentscheidung,
Persistenzbindung und Prompt-/Evidenzaufbau gleichzeitig. Es wird deshalb
nicht mit `run_task`, `run_task --watch` oder einem Resume-Self-Bootstrap
entwickelt.

Verbindlicher Ablauf:

1. Codex implementiert direkt im Repository Slice für Slice.
2. Nach jedem Slice läuft die vollständige Repositorymatrix genau einmal durch
   Codex; Claude startet sie im Review nicht erneut.
3. Claude prüft jeden Slice manuell mit Sonnet und Effort `high` auf Basis des
   Slice-Diffs und der dokumentierten Attestierung.
4. Ein Claude-Blocker wird im selben Slice korrigiert und erneut geprüft.
5. Nach den Slice-Freigaben erstellen Codex und Claude unabhängige
   branchweite Gesamtreviews.
6. Staging, Abschlusscommit, Archivierung und Merge erfolgen erst nach beiden
   positiven Gesamtreviews und ausdrücklichem Benutzerauftrag.

Es gibt genau drei Implementierungsslices. Eine maximale Dateianzahl pro
Slice gilt nicht. Atomare Laufzeit- und Vertragsgrenzen haben Vorrang; jeder
Slice muss für sich die vollständige Matrix bestehen.

Historische `legacy-state-v3`-, `structured-v1`- oder nichtnativ gebundene
`structured-v2`-Läufe müssen weder fortgesetzt noch durch aktive Reader
lesbar bleiben. Sie werden vor jedem Providerstart mit einem stabilen
`UNSUPPORTED-PROTOCOL`-Fehler abgewiesen. Archivierte Markdown- und
Corpusnachweise bleiben unveränderte Historie und werden nicht als
Runtimequelle verwendet.

Nicht entfernt werden allgemeine Parser, die weiterhin eine andere fachliche
Aufgabe besitzen: Inbox-/Taskverträge, Arbeitsplan-Handoffs, JSON-Decoding,
Schemasicherheit, Providerdiagnosen und Quota-Zeitangaben. Das Retirement
betrifft ausschließlich die textuelle Codex-/Claude-Ergebnisgrammatik.

## 4. Zielarchitektur

### 4.1 Einziger Transport

```text
Codex operation
  -> NativeCodexRequestBundle
  -> NativeCodexAdapter
  -> schema- und requestgebundenes NativeAgentCodexOutput
  -> Record ahead
  -> State-Spiegel

Claude review
  -> NativeReviewRequestBundle
  -> NativeClaudeReviewAdapter
  -> schema- und requestgebundenes NativeAgentReviewOutput
  -> Record ahead
  -> State-Spiegel
```

Es existieren weder ein Textfallback noch ein automatischer semantischer
Repair-Aufruf. Syntax-, Schema-, Request- oder Domänenfehler werden mit ihrem
stabilen Diagnosecode persistiert und folgen ausschließlich der begrenzten
technischen Retry-/Resume-Policy. Ein anderes LLM darf die abgelehnte Antwort
nicht umformulieren.

### 4.2 Native Pflichtbindung

`ProtocolBinding` besitzt für den einzigen unterstützten Modus genau eine
kanonische Form:

- `mode = structured-v2`;
- `schema_version = 2`;
- Codex-Transport = aktuelle native Codex-Version;
- Claude-Transport = aktuelle native Claude-Version;
- unveränderlich gebundene Codex- und Claude-Profile.

Transportflags werden aus CLI, Watch-Weitergabe, README und Tests entfernt.
Ein alter State mit fehlender, partieller oder anderer Bindung scheitert vor
Snapshot-, Validierungs- oder Providerarbeit. Es gibt keine Laufzeitmigration.

### 4.3 Minimale Evidenz

Die Request-ID bleibt der Digest über den vollständigen tatsächlich
transportierten Vertrag. Evidenz wird nicht durch ein LLM zusammengefasst,
sondern lokal aus autoritativen Quellen projiziert:

- Planung und Planrevision erhalten Auftrag, Zielbranch, zulässigen Planpfad
  und den aktuellen Planstand.
- Erstimplementierung erhält nur den aktuellen Slicevertrag mit Ziel,
  Akzeptanzkriterien, exakten Pfaden und erforderlichen Querverweisen.
- Codex-Korrektur erhält nur betroffene offene Findings, deren
  Akzeptanzkriterien, den Korrekturdelta und die aktuelle Pfadfreigabe.
- Claude-Erstreview erhält den kanonischen Reviewpacket-Inhalt genau einmal.
- Claude-Konvergenzreview erhält nur Korrekturdelta, betroffene Findings,
  aktuelle Attestierung und notwendige Bindungsdaten.
- Finalreviews behalten den vollständigen semantischen Branchdiff; generierte
  Auditansichten werden weiterhin content-addressiert kompaktiert.

Kein nativer Request enthält Markergrammar, den alten `workflow-prompt` oder
einen vollständigen Mehrslice-Plan, wenn nur ein einzelner Slice ausgeführt
wird. Content-gleiche Evidenzkomponenten innerhalb desselben Requests werden
vor Providerstart abgewiesen statt doppelt berechnet oder übertragen.

## 5. Umsetzungsslices

### Slice 1 - Native Pflichtbindung und fail-closed Protokollcutover

Dieser Slice entfernt die Transportwahl und schließt State, Recordmodell,
Agentregistry, CLI und Watchpfad atomar auf den nativen Codex-Claude-Verbund.
Textcode darf nach diesem Slice noch im Repository liegen, ist aber von keinem
neuen oder fortsetzbaren Lauf mehr erreichbar.

**Exakter Änderungspfad**

- `AGENTS.md`
- `CLAUDE.md`
- `CODEX.md`
- `README.md`
- `schemas/orchestrator-artifact-v2.schema.json`
- `src/agent_adapters.py`
- `src/artifact_bridge.py`
- `src/artifact_migration.py`
- `src/artifact_models.py`
- `src/artifact_replay.py`
- `src/cli.py`
- `src/dry_run_scenarios.py`
- `src/inbox_watcher.py`
- `src/orchestrator.py`
- `src/state_io.py`
- `src/workflow.py`
- `src/workflow_state.py`
- `tests/test_agent_adapters.py`
- `tests/test_artifact_bridge.py`
- `tests/test_artifact_migration.py`
- `tests/test_artifact_models.py`
- `tests/test_artifact_projection.py`
- `tests/test_artifact_replay.py`
- `tests/test_cli.py`
- `tests/test_dry_run_scenarios.py`
- `tests/test_final_review_preflight.py`
- `tests/test_inbox_watcher.py`
- `tests/test_language_consistency.py`
- `tests/test_native_transport_retirement.py`
- `tests/test_orchestrator_runtime.py`
- `tests/test_orchestrator_watch_cli.py`
- `tests/test_provider_input_efficiency.py`
- `tests/test_state_io.py`
- `tests/test_structured_artifact_regressions.py`
- `tests/test_workflow.py`
- `tests/test_workflow_state.py`
- `tests/fixtures/provider_input_efficiency/native-only-cutover-baseline-v1.json`
- `tests/fixtures/provider_input_efficiency/native-only-cutover-baseline-v1.lock.json`
- `docs/internal/native-only-transport-parser-retirement-und-evidenzeffizienz-slice-01-review.md`

**Umsetzung**

1. Entferne beide `--native-*`-/`--no-native-*`-Schalter und alle
   Watch-/Resume-Weitergaben. Die nativen Transporte sind keine Option mehr.
2. Erzeuge neue States nur noch mit der vollständigen kanonischen
   `structured-v2`-Bindung und beiden Agentprofilen.
3. Verwerfe fehlende, partielle, textuelle, v1- oder legacy Bindungen beim
   Laden beziehungsweise Resume vor allen externen Seiteneffekten mit
   `UNSUPPORTED-PROTOCOL`.
4. Registriere unmittelbar die nativen Adapter; ein Produktionsdriver darf
   keinen Textadapter anfordern oder nachträglich umwickeln.
5. Verschärfe v2-AgentResult- und Reviewrecords so, dass Transportversion,
   Request-ID und Rohantwortdigest verpflichtend zusammen vorliegen.
6. Entferne historische Reader-Erwartungen aus aktiven Tests. Archivierte
   Record- und Corpusbytes bleiben unverändert, werden aber nicht als
   fortsetzbare Runtimefixtures behandelt.
7. Inventarisiere providerfrei jede Konstruktionsstelle von `ReviewPayload`
   und `AgentResultPayload` unter `src/` und `tests/`. Direkte Testfixtures und
   produktive Builder müssen nach der Verschärfung ausnahmslos die
   vollständige native Transportversion, Request-ID und den Rohantwortdigest
   liefern. Die Inventur nennt bei einem Verstoß Pfad und Zeile.
8. Erzeuge vor dem Entfernen des alten `workflow-prompt`- und
   Vollplanaufbaus die kanonische Baseline
   `tests/fixtures/provider_input_efficiency/native-only-cutover-baseline-v1.json`.
   Sie enthält für jede später optimierte Operation die sortierte
   Komponentenliste mit Rollenname, Inhaltsdigest, Zeichen- und UTF-8-
   Bytezahl sowie Basiscommit, Generatorversion und Digest der synthetischen
   Eingaben. `tests/test_provider_input_efficiency.py` rekonstruiert die Datei
   in Slice 1 bytegleich aus der zu diesem Zeitpunkt aktiven Aufbaulogik.
   Zusätzlich bindet
   `tests/fixtures/provider_input_efficiency/native-only-cutover-baseline-v1.lock.json`
   den SHA-256 der gesamten Baseline, ihren Basiscommit, ihre Fixtureversion
   und die erwarteten entfernten Komponenten. Der Slice-1-Reviewbericht nennt
   denselben Baseline- und Lockdigest.
9. Synchronisiere Rootverträge und README mit dem verpflichtenden Transport.

**Akzeptanzkriterien**

- `run_task --help` enthält keinen Native-Transport-Schalter; eine alte Option
  scheitert deterministisch als unbekanntes Argument.
- Ein frischer Einzel- und Watchlauf bindet ohne Zusatzoptionen beide nativen
  Transporte und startet ausschließlich native Adapter.
- Ein Resume mit fehlender oder nichtnativ gebundener Transportidentität
  stoppt vor Providerattempt, Snapshot, Validierung und Commit.
- Kein v2-Review- oder Codex-Resultrecord ohne vollständige native Bindung
  validiert gegen Modell und JSON-Schema.
- Eine providerfreie, repositoryweite Konstruktionsinventur weist nach, dass
  keine `ReviewPayload`- oder `AgentResultPayload`-Instanz unter `src/` oder
  `tests/` ohne Transportversion, Request-ID und Rohantwortdigest verbleibt;
  insbesondere sind Finalreview-Preflight und Artifactprojektion enthalten.
- Die Baseline-Fixture wird aus den gebundenen synthetischen Operationen
  bytegleich reproduziert und ist selbstkonsistent: Komponentendigest,
  Zeichen- und Bytezahl stimmen jeweils mit dem eingefrorenen Inhalt
  beziehungsweise Eingabedigest überein. Der separate Lock stimmt mit dem
  vollständigen Fixturedigest überein und nennt mindestens `workflow-prompt`
  beziehungsweise die Vollplan-Komponente als später zu entfernende Evidenz.
- Profilbindung, Quota-, Retry-, Record-ahead-, Commit- und
  Menschenprojektionssemantik bleiben unverändert grün.
- `python3 -m pytest tests/ -v` und `git diff --check` bestehen.

### Slice 2 - Entfernung der Markerparser und Textvertragsreparatur

Dieser Slice löscht die nach Slice 1 unerreichbare Ergebnisgrammatik aus der
Produktionsarchitektur. Gemeinsame typisierte Domänenobjekte bleiben in
`contracts.py`; nur Textdecoding, Markernormalisierung und ihre
Repair-Infrastruktur verschwinden.

**Exakter Änderungspfad**

- `AGENTS.md`
- `CLAUDE.md`
- `CODEX.md`
- `README.md`
- `schemas/native-agent-review-request-v2.schema.json`
- `src/agent_adapters.py`
- `src/agent_runtime.py`
- `src/contracts.py`
- `src/dry_run_scenarios.py`
- `src/native_codex_request.py`
- `src/native_review_request.py`
- `src/orchestrator.py`
- `src/prompts.py`
- `src/provider_input_budget.py`
- `src/workflow.py`
- `tests/test_agent_adapters.py`
- `tests/test_agent_runtime.py`
- `tests/test_contracts.py`
- `tests/test_dry_run_scenarios.py`
- `tests/test_language_consistency.py`
- `tests/test_native_transport_retirement.py`
- `tests/test_native_codex_contract.py`
- `tests/test_native_codex_request.py`
- `tests/test_native_review_contract.py`
- `tests/test_native_review_request.py`
- `tests/test_provider_input_efficiency.py`
- `tests/test_orchestrator_runtime.py`
- `tests/test_parsing.py`
- `tests/test_prompts.py`
- `tests/test_review_runtime_hardening.py`
- `tests/test_workflow.py`
- `docs/internal/native-only-transport-parser-retirement-und-evidenzeffizienz-slice-02-review.md`

**Umsetzung**

1. Entferne `validate_codex_response`, `validate_review_response`,
   `parse_finding_responses`, `parse_anchors` und ausschließlich von ihnen
   benötigte Markerhilfen. Behalte Dataclasses und native Domänenoperationen.
2. Entferne `normalize_codex_contract_output`,
   `normalize_review_contract_output`, Reparaturblockextraktion und alle
   textuellen Codex-/Reviewzweige aus `WorkflowEngine` und Produktionsdriver.
3. Entferne den Providerbetrieb `*_contract_repair`, seine Prompts,
   Retry-Sonderbehandlung, Diagnose-Recovery und Tests. Ein JSON-Vertragsfehler
   wird nie durch ein LLM umgeschrieben.
4. Refaktoriere native Adapter auf die vorhandene `_BaseAdapter`-Basis, ohne
   von `CodexAdapter` oder `ClaudeAdapter` zu erben. Verschiebe beziehungsweise
   erhalte dabei ausdrücklich die native-neutrale Fläche
   `_provider_input_components`, `required_hosts` und `stream_filter` für
   Codex sowie `_provider_input_components`, `bind_reviewer_workspace`,
   `build_capability_smoke_command`, `required_hosts` und `reviewer` für
   Claude. Die Workspacebindung an `src/review_harness.py` und der
   Capability-Smoke bleiben unverändert. Die Fabrikmethoden
   `native_codex_adapter` und `native_review_adapter` entfallen; die Registry
   konstruiert beide nativen Adapter direkt aus den aufgelösten
   `AgentSettings`. Nur wirklich providerneutrale Prozessmechanik wandert in
   `_BaseAdapter`. Der polymorphe Diskriminator `reviewer` und das
   Protocol-Member `bind_reviewer_workspace` verbleiben ausdrücklich dort:
   `NativeCodexAdapter` behält die neutralen Vorgaben `reviewer = False` und
   eine wirkungslose Workspacebindung, während `NativeClaudeReviewAdapter`
   `reviewer = True` und die tatsächliche Bindung an
   `src/review_harness.py` bereitstellt. Codex-spezifische Methoden bleiben
   auf `NativeCodexAdapter`; `build_capability_smoke_command` bleibt als
   tatsächlich Claude-spezifische API ausschließlich auf
   `NativeClaudeReviewAdapter`. Eine Negativkontrolle verhindert nur die
   versehentliche Vererbung dieser Claude-spezifischen Capability-Smoke-API
   an Codex, ohne die gemeinsame Protocol-Fläche zu entfernen.
5. Ersetze Markerprompts durch kleine native Systemrichtlinien und typisierte
   Requestfelder. Entferne `workflow-prompt` aus dem Codex-Request.
6. Entferne aus `schemas/native-agent-review-request-v2.schema.json` den
   Wurzel-`oneOf`-Zweig `review_contract_repair_request` sowie die nur von ihm
   erreichbaren Definitionen `review_contract_repair_request` und
   `repair_error`. Das aktive Readerschema akzeptiert danach ausschließlich
   reguläre native Reviewrequests; die vier requestgebundenen Reviewformen
   Plan, initialer Slice, Konvergenz und Final bleiben über ihre jeweiligen
   Writerschemaprojektionen erhalten.
7. Baue `tests/test_provider_input_efficiency.py` nach der Entfernung des
   alten Evidenzaufbaus von Rekonstruktion auf Unveränderlichkeitsprüfung um.
   Der Test liest Baseline und Lock nur, prüft den im Lock reviewten
   Baseline-SHA-256 sowie alle internen Komponentenbindungen und darf weder
   Fixture noch Lock regenerieren. Eine Gegenprobe verlangt, dass mindestens
   eine im Lock als entfernt gebundene Komponente – insbesondere
   `workflow-prompt` – im aktuellen Requestaufbau nicht mehr vorkommt. Fixture
   und Lock gehören ausdrücklich nicht zum Slice-2-Änderungspfad; der
   Scope-/Diffcheck muss beide gegenüber dem Slice-1-Commit als bytegleich
   ausweisen.
8. Entferne Markergrammar aus den aktiven Rootverträgen. Die Dokumente
   beschreiben die semantischen Rollenregeln und verweisen für Maschinenoutput
   auf die versionierten JSON-Schemata.
9. Führe einen statischen Retirementguard ein, der verbotene Symbole,
   Operationen und aktive Markergrammar unter `src/`, Rootverträgen und README
   erkennt. Markdown-Task- und Planmarker sowie archivierte Historie werden
   exakt ausgenommen.

**Akzeptanzkriterien**

- Unter `src/` existiert kein produktiver Codex-/Claude-Resultatparser für
  Markertext, kein Textadapter und kein Contract-Repair-Providerbetrieb.
- Das aktive native Reviewrequest-Schema lehnt ein handgebautes Dokument mit
  `request_type = review_contract_repair_request` fail-closed ab. Die vier
  regulären Writerschemaformen Plan, initialer Slice, Konvergenz und Final
  werden weiterhin aus ihrem gebundenen Kontext erzeugt und validiert.
- `NativeCodexAdapter` und `NativeClaudeReviewAdapter` führen keine
  Textadapterklasse mehr in ihrer MRO. Registry, Providerinput-Komponenten,
  Hostanforderungen, Streamfilter, Reviewer-Workspacebindung und
  Capability-Smoke verhalten sich in providerfreien Regressionen weiterhin
  vertragsgemäß. Die API-Regression verlangt ausdrücklich:
  `NativeCodexAdapter.reviewer is False`,
  `hasattr(NativeCodexAdapter, "build_capability_smoke_command") is False`
  und eine wirkungslose Ausführung von
  `NativeCodexAdapter().bind_reviewer_workspace(...)`. Für Claude verlangt
  sie `NativeClaudeReviewAdapter.reviewer is True`, einen vorhandenen
  Capability-Smoke und die wirksame Workspacebindung an
  `src/review_harness.py`. Beide nativen Adapter bleiben frei von
  Textadapterklassen in ihrer MRO.
- Baseline und Lock sind gegenüber dem freigegebenen Slice-1-Commit bytegleich
  und ihr SHA-256-Verbund ist gültig. Der umgebaute providerfreie Test
  rekonstruiert oder schreibt sie nicht mehr und weist nach, dass mindestens
  eine reviewte Altkomponente im aktuellen Requestaufbau fehlt. Eine
  einseitige Änderung von Baseline oder Lock scheitert an deren gegenseitiger
  Digestbindung. Eine gemeinsame konsistente Neuerzeugung auf dem
  Nach-Slice-2-Zustand wird dagegen ausdrücklich durch den Verbund aus
  Altkomponenten-Absenzprüfung, der in Slice 3 folgenden operationenbezogenen
  Differenzgleichung und dem Scope-/Diffcheck gegen den freigegebenen
  Slice-1-Commit erkannt; der Lockdigest allein ist dafür kein ausreichender
  Schutz.
- Native Plan-, Planrevision-, Implementierungs-, Korrektur-, Slice- und
  Finalpfade bestehen mit Sprengfallen, die jeden Aufruf einer entfernten
  Textschnittstelle erkennen würden.
- Ungültiges JSON, falsche Request-ID, Writerschemaabweichung und
  Domänenverletzung liefern den gebundenen lokalen Diagnosecode und lösen
  keinen semantischen Reparaturaufruf aus.
- Quota-/Fehlerdiagnoseparser, Taskvertrag, Plan-Handoff und JSON-Decoder
  bleiben ausdrücklich erhalten und getestet.
- Der statische Guard unterscheidet aktive Ergebnisgrammar von zulässigen
  Task-/Planmarkern und Archivhistorie.
- `python3 -m pytest tests/ -v` und `git diff --check` bestehen.

### Slice 3 - Digestgebundene Evidenzminimierung und messbare Betriebswirkung

Dieser Slice nutzt den geschlossenen nativen Pfad, um doppelte und fachlich
irrelevante Eingabe zu entfernen. Die Einsparung wird nicht aus geschätzten
Provider-Tokens behauptet, sondern zunächst strukturell sowie über kanonische
UTF-8-Byte- und Zeichenzahlen nachgewiesen; tatsächliche Providerusage wird
anschließend aus Records projiziert.

**Exakter Änderungspfad**

- `README.md`
- `docs/internal/ORCHESTRATOR_ROADMAP_PHASE_2_PLUS.md`
- `src/agent_adapters.py`
- `src/agent_runtime.py`
- `src/artifact_bridge.py`
- `src/artifact_models.py`
- `src/artifact_projection.py`
- `src/native_codex_request.py`
- `src/native_review_request.py`
- `src/provider_input_budget.py`
- `src/provider_input_efficiency.py`
- `src/review_packets.py`
- `src/workflow.py`
- `tests/test_agent_adapters.py`
- `tests/test_agent_runtime.py`
- `tests/test_artifact_bridge.py`
- `tests/test_artifact_models.py`
- `tests/test_artifact_projection.py`
- `tests/test_native_codex_request.py`
- `tests/test_native_review_request.py`
- `tests/test_provider_input_budget.py`
- `tests/test_provider_input_efficiency.py`
- `tests/test_review_packets.py`
- `tests/test_workflow.py`
- `docs/internal/native-only-transport-parser-retirement-und-evidenzeffizienz-slice-03-review.md`

**Umsetzung**

1. Erzeuge aus dem freigegebenen Arbeitsplan ein kanonisches
   Slice-Ausführungspaket mit nur Slice-ID, Ziel, Akzeptanzkriterien, exakten
   Pfaden und ausdrücklich benannten Querverweisen. Binde Inhalt und Quellplan
   per Digest; lies nicht aus projiziertem Audit-Markdown zurück.
2. Verwende bei Codex-Implementierung dieses Paket statt des vollständigen
   Mehrslice-Plans. Planrevisionen dürfen weiterhin den aktuellen Planstand
   erhalten.
3. Erzeuge für Korrekturen ein Paket ausschließlich aus betroffenem Finding-
   Snapshot, Akzeptanzkriterien, aktuellem Delta und fingerprintgebundener
   Pfadfreigabe. Nicht betroffene geschlossene Findings und frühere Volldiffs
   werden nicht übertragen.
4. Weise doppelte Evidenz-IDs, Sourcepaths, Digests und content-gleiche
   Komponenten innerhalb eines Requests fail-closed ab. Reihenfolge,
   Request-ID und Writerschemadigest bleiben deterministisch.
5. Ergänze operationenbezogene Effizienzprojektionen: Inputzeichen,
   Inputbytes, tatsächliche Input-/Outputtokens soweit vom Provider geliefert,
   Laufzeit, Attemptanzahl und Retrystatus. Die Recordkette bleibt Quelle;
   fehlende Usage wird als unbekannt und nie als null projiziert.
6. Lade die in Slice 1 reviewte Baseline-Fixture und ihren Lock nur als
   unveränderliche Read-only-Referenzen. Beide Pfade liegen absichtlich nicht
   im Slice-3-Änderungspfad. Die neue Aufbaulogik darf weder alte Builder
   importieren noch Baseline oder Lock während des Tests regenerieren. Vor der
   Differenzrechnung werden Lock- und Fixturedigest erneut geprüft. Für jede
   betroffene Operation
   wird aus den kanonischen Komponenten beider Seiten nachgewiesen:
   `baseline_chars - current_chars = Summe(chars der entfernten Komponenten)`
   und entsprechend für UTF-8-Bytes. Unveränderte Komponenten müssen
   namens- und digestgleich bleiben.
7. Aktualisiere die Roadmap: Live-Output wird als erreicht markiert;
   Parser-Retirement und strukturelle Eingabereduktion werden nach Abschluss
   als erreicht dokumentiert. Corpuspflege, weitergehende Trendanalyse und
   Bootstrap-Langläufe bleiben nachgelagert.

**Akzeptanzkriterien**

- Ein synthetischer großer Drei-Slice-Plan mit eindeutigen Sentineltexten
  zeigt: Implementierungsslice 2 enthält weder Slice 1 noch Slice 3 und nicht
  den vollständigen Plantext, besitzt aber Ziel, Kriterien, Pfade, Plandigest
  und alle benannten Querverweise.
- Ein Korrekturrequest enthält nur das Korrekturdelta und die betroffenen
  offenen Findings; Sentinel aus Volldiff, nicht betroffenem Finding und
  früherer Runde fehlen.
- Gegenüber
  `tests/fixtures/provider_input_efficiency/native-only-cutover-baseline-v1.json`
  sinken `local_input_chars` und `local_input_bytes` je betroffener
  Implementierungs- und Korrekturoperation exakt um die Summe der in der
  Fixture einzeln digest-, zeichen- und bytegebundenen entfernten
  Komponenten. Die Gegenprobe liest ausschließlich die eingefrorene Fixture
  und den neuen Builder; sie benötigt weder gelöschten Altcode noch eine
  hartkodierte Gesamtzahl.
- Baseline und Lock sind gegenüber dem freigegebenen Slice-1-Commit
  bytegleich. Der aktuelle Aufbau enthält mindestens eine im Lock gebundene
  entfernte Altkomponente nicht mehr; eine auf den Nach-Slice-2- oder
  Nach-Slice-3-Zustand regenerierte Fixture scheitert vor der
  Effizienzbehauptung.
- Kein Test behauptet aus Zeichen automatisch Provider-Tokens. Wo Usage
  vorhanden ist, stimmt die Menschenprojektion exakt mit den persistierten
  Providerattempt-Records überein; fehlende Werte erscheinen als `unknown`.
- Deduplizierungs- und Manipulationsgegenproben scheitern vor Providerstart,
  ohne Request-ID, Record oder Stateentscheidung zu erfinden.
- Kompakter Live-Output, vollständiger `full`-Diagnosemodus,
  Rohantwortpersistenz, Replay und Auditprojektion bleiben semantisch
  unverändert.
- `python3 -m pytest tests/ -v` und `git diff --check` bestehen.

## 6. Gesamtvalidierung und adversariales Review

Nach Slice 3 prüfen Codex und Claude unabhängig den vollständigen Diff gegen
`BASE_COMMIT`. Das Review umfasst mindestens:

1. Erreichbarkeitsanalyse aller Agentenstarts und Recoverypfade;
2. Abwesenheit einer Textentscheidung, eines Textfallbacks und einer
   semantischen LLM-Reparatur;
3. vollständige Request-/Schema-/Profil-/Fingerprintbindung;
4. Record-ahead-, Resume-, Quota-, Retry- und Commit-Idempotenz;
5. Beweis, dass Evidenzreduktion keine erforderliche Anforderung, Finding-
   Historie, Attestierung oder Pfadgrenze verliert;
6. Vergleich der kanonischen Komponenten- und Bytezahlen vor und nach der
   Reduktion;
7. korrekte Trennung zwischen Byte-/Zeichenproxy und tatsächlicher
   Providerusage;
8. synchronisierte Rootverträge, README und Roadmap;
9. vollständige Repositorymatrix und sauberer `git diff --check`.

Ein ausführbarer Defekt ist ein Blocker. Nicht handlungsbedürftige
Zukunftsideen und Restrisiken gehören ausschließlich in die Reviewevidenz.

## 7. Risiken und Fail-closed-Grenzen

- **Zu frühes Parserlöschen:** Native Adapter dürfen nicht indirekt von
  Textadapterhilfen abhängig bleiben. Slice 1 macht native Adapter zuerst
  eigenständig erreichbar; Slice 2 entfernt erst danach Altcode.
- **Verlust von Plananforderungen:** Das Slice-Paket wird deterministisch aus
  dem gebundenen Plan erzeugt und enthält Querverweise. Sentinel- und
  Digesttests verhindern stilles Weglassen.
- **Falsche Tokenversprechen:** Zeichen und Bytes sind nur lokale Proxies.
  Tatsächliche Tokens werden ausschließlich aus Providerusage berichtet.
- **Ungewollte historische Migration:** Nichtnative States und Records werden
  abgewiesen, nicht umgeschrieben oder teilweise übernommen.
- **Verlust technischer Diagnose:** Rohantworten und Vollausgabemodus bleiben
  erhalten; nur ihre semantische Auswertung als Textvertrag entfällt.
- **Zu breiter Retirementguard:** Task-, Plan-, Quota- und JSON-Parser sind
  ausdrücklich außerhalb seiner Verbotsmenge und erhalten positive
  Kontrolltests.

## 8. Nicht-Ziele

- keine neue Provider- oder Reviewerrolle;
- keine Reaktivierung historischer Laufzustände;
- keine stille State- oder Recordmigration;
- keine LLM-Zusammenfassung autoritativer Evidenz;
- keine Änderung der fachlichen Finding-, Gate- oder Freigaberegeln;
- keine Push-, Remote-Merge- oder Deploymentautomatisierung;
- keine willkürliche pauschale Promptkürzung ohne Verlustfreiheitsnachweis;
- kein externer Kostenservice und keine geschätzten Tokenwerte.

---

## Claude-Planreview – Runde 1

Manuelles Planreview außerhalb von `run_task`, ohne Provideraufruf und ohne
strukturierte JSON-Ausgabe. Weder Produktcode noch Tests noch andere Dokumente
wurden verändert. Git-Bindung geprüft: Branch
`feature/native-only-transport-and-efficiency`, HEAD
`9a7da9c66ee187a9044fbf3ff3e92a6b24e216cd`, Arbeitsbaum enthält nur
Planungsdokumente. Ich habe den Plan gegen den tatsächlichen Repositorystand
geprüft und die Ausgangsbefunde nicht übernommen, sondern nachgemessen.

### Was der Plan richtig trifft

Der Ausgangsbefund §2.2 ist an jeder von mir geprüften Stelle korrekt.
`--native-claude-reviews` und `--native-codex-results` sind
`BooleanOptionalAction` mit `default=None` (`src/cli.py:543-560`), und die
Hilfetexte sagen selbst, dass Auslassung neue Läufe „on the legacy text
result transport" hält; `_fresh_state()` führt beide Schalter als
`bool = False` (`src/orchestrator.py:3621-3622`). Die Vererbung ist real:
`NativeCodexAdapter(CodexAdapter)` (`:438`) und
`NativeClaudeReviewAdapter(ClaudeAdapter)` (`:886`), während
`build_agent_registry()` (`:1130-1139`) ausschließlich die Textadapter
registriert. `ProtocolBinding` führt beide Transporte als `str | None = None`
und modelliert weiterhin `LEGACY_STATE_V3` und `STRUCTURED_V1`
(`src/workflow_state.py:138-217`). Der `workflow-prompt` existiert als
Evidenzkomponente in `src/workflow.py:3881`.

Die Reihenfolge ist fachlich richtig und nicht umkehrbar. Die Textzweige sind
in `src/workflow.py:1616`, `:2035` und `:2158` an
`protocol_binding.codex_result_transport` beziehungsweise
`claude_review_transport` gebunden; erst wenn Slice 1 diese Bindung
verpflichtend macht, wird der Textpfad unerreichbar, und erst danach darf
Slice 2 löschen. Die umgekehrte Reihenfolge wäre nicht atomar grün.

Die Substanz für Slice 3 existiert bereits: Das v2-Schema führt
`provider_input_measurement` mit `components`, `total_chars` und `total_bytes`,
`provider_usage` mit durchgängig nullbaren Tokenfeldern und `provider_attempt`
mit `attempt_number`, `phase`, `duration_seconds`, `failure_kind`, `usage`,
`model` und `effort`. Die Trennung zwischen lokalem Zeichen-/Byteproxy und
tatsächlicher Providerusage ist damit im Recordmodell bereits angelegt, und
„fehlende Usage als unbekannt" ist darstellbar. Die Abgrenzung in §3 gegen
Task-, Plan-, JSON-, Diagnose- und Quotaparser ist eindeutig genug: Die
betroffenen Symbole liegen in `contracts.py`, `workflow.py`, `agent_runtime.py`
und `prompts.py`, die erhaltenen in `task_contract.py`, `plan_handoff.py` und
`schema_validation.py`.

Ich habe außerdem die vollständige Referenzmenge der zu entfernenden Symbole
aufgelöst. `validate_codex_response`, `validate_review_response`,
`parse_finding_responses`, `parse_anchors`, `normalize_codex_contract_output`,
`normalize_review_contract_output` und `*contract_repair*` werden von genau den
Dateien referenziert, die Slice 2 bereits listet — hier fehlt nichts.

Zwei von mir erwartete Risiken haben sich **nicht** bestätigt und sind kein
Befund: `tests/test_native_contract_differential.py` prüft acht Writerformen
aus vier `NativeCodexRequestKind` und vier Reviewformen; die Repair-Anfrage
gehört nicht dazu, der Test bricht also nicht. Und die 13 `regression_test`-
Verweise der beiden Provider-Subset-Register werden von
`src/native_provider_schema.py:107` nur als Pflichtschlüssel geprüft, nicht als
auflösbare Funktionsnamen; eine Umbenennung in Slice 2 bricht sie nicht.

### Wo der Plan bricht

Drei konkrete Fehler verhindern, dass die Slices in der vorgelegten Form
atomar grün sind beziehungsweise ihr eigenes Akzeptanzkriterium erfüllen.

**Slice 1 kann die vollständige Matrix nicht bestehen.** Arbeitsschritt 5
verschärft AgentResult- und Reviewrecords auf verpflichtende Transportbindung.
Ich habe alle Konstruktionsstellen von `ReviewPayload(` und
`AgentResultPayload(` über `src/` und `tests/` aufgelöst: neun Dateien. Drei
davon fehlen im Slice-1-Pfad, und zwei konstruieren nachweislich **ohne**
Transportbindung. `tests/test_final_review_preflight.py` enthält null
`transport_schema`-Vorkommen und baut `ReviewPayload(Role.CLAUDE, "2",
"approved", (), "checked")` sowie `AgentResultPayload(Role.CODEX, …, "ready",
())` in den Zeilen 169, 212, 217 und 450; die Datei steht in **keinem** der
drei Pfadlisten. `tests/test_artifact_projection.py` baut in den Zeilen 72 und
352 ebenfalls ungebundene `ReviewPayload`-Instanzen und steht nur im Pfad von
Slice 3, also einen Slice zu spät. `tests/test_audit_trail.py` ist dagegen
sauber gebunden (`transport_schema="native-codex-v2"`, Zeile 363) und braucht
keine Aufnahme.

**Slice 2 lässt die Repair-Anfrage im aktiven Maschinenvertrag stehen.**
`build_native_review_repair_request()` (`src/native_review_request.py:515`)
erzeugt `"request_type": "review_contract_repair_request"`, und Requests werden
gegen `REQUEST_SCHEMA_PATH` validiert (`:377`, `:394`), also gegen
`schemas/native-agent-review-request-v2.schema.json`. Dieses Schema führt die
Repair-Anfrage als eigenen Zweig im Wurzel-`oneOf` (Zeile 7) sowie als
`$defs/review_contract_repair_request` (Zeile 312) und `$defs/repair_error`.
Die Datei steht in **keiner** der drei Pfadlisten. Löscht Slice 2 nur den
Builder und die Tests, bleibt der versionierte, aktiv geladene Requestvertrag
für einen zurückgezogenen Anfragetyp offen. Das Akzeptanzkriterium formuliert
die Grenze ausdrücklich nur als „Unter `src/` existiert … kein
Contract-Repair-Providerbetrieb" und übersieht diese Restpermissivität
deshalb systematisch — genau die stille Überpermissivität, die dieses Review
finden soll.

**Slice 3 hat ein nicht ausführbares Akzeptanzkriterium.** Kriterium 3
verlangt, dass `local_input_chars` und `local_input_bytes` „gegenüber der vor
Slice 2 festgehaltenen kanonischen Fixture" exakt um die entfernten
Komponenten sinken. Diese Fixture existiert heute nicht, und keiner der drei
Änderungspfade legt sie an: Weder Slice 1 noch Slice 2 noch Slice 3 nennt einen
Fixture- oder `tests/fixtures/`-Pfad. Zugleich entfernt Slice 2 Schritt 5 den
`workflow-prompt` aus dem Codex-Request, sodass der Vorzustand zum Zeitpunkt
von Slice 3 nicht mehr berechenbar ist. Der Test könnte die Bytegleichung dann
nur noch gegen eine gelöschte Altimplementierung oder gegen eine im Testcode
hartkodierte Zahl prüfen — beides widerspricht der Zusage, keinen
willkürlichen Wert zu verwenden.

Ergänzend, nicht blockierend: Slice 2 Schritt 4 verlangt die Umhängung der
nativen Adapter „auf eine gemeinsame binäre Basis", benennt aber nicht die
tatsächlich geerbte Fläche. Ich habe sie aufgelöst. `NativeCodexAdapter` erbt
von `CodexAdapter` die Methoden `_provider_input_components`,
`native_codex_adapter`, `required_hosts` und `stream_filter`;
`NativeClaudeReviewAdapter` erbt von `ClaudeAdapter`
`_provider_input_components`, `bind_reviewer_workspace`,
`build_capability_smoke_command`, `native_review_adapter`, `required_hosts` und
`reviewer`. Keine davon ist Markerlogik; `bind_reviewer_workspace` trägt
zusätzlich die Bindung an `src/review_harness.py` (`src/agent_adapters.py:615`,
`:625-629`, `:736`). Ein `_BaseAdapter` existiert bereits (`:242`) und ist der
naheliegende Zielort. Ohne diese Aufzählung ist der Refaktorierungsschritt
unterbestimmt.

### Antworten auf die weiteren Reviewfragen

Zur Fail-closed-Ordnung: Ein alter State wird nicht nach einem externen
Seiteneffekt erkannt. `prepare_new_watch_task_branch()`
(`src/orchestrator.py:3792`) ist die einzige Git-Wirkung vor dem Laden, läuft
aber nur unter `new_watch_task = watch_run and (force_new or not
state_file.exists())` — also genau dann, wenn kein fortzusetzender State
existiert. Slice 1 Schritt 3 verlangt die Ablehnung „beim Laden beziehungsweise
Resume vor allen externen Seiteneffekten", und `src/state_io.py` steht im Pfad;
das ist der richtige Ort, weil `driver.checkpoint(state, _history(state))`
(`:3937`) unmittelbar nach der Driverkonstruktion bereits schreibt. Ein
Hinweis für die Umsetzung ohne Findingcharakter: `UNSUPPORTED-PROTOCOL` ist
heute ein `ReplayDiagnosticCode` (`src/artifact_replay.py:48`, gehoben in
`src/artifact_migration.py:79`); der Ladepfad muss denselben stabilen Code
tragen, damit die Zusage „stabiler Fehler" nicht auf zwei Fehlerfamilien
zerfällt. Beide Dateien liegen im Slice-1-Pfad.

Zur Guardgrenze: Slice 2 Schritt 7 begrenzt den statischen Guard auf `src/`,
Rootverträge und README. Das ist eng genug gegen Fehlalarme auf Task-, Plan-
und Archivtext, lässt aber tote Markergrammatik unter `tests/` zu — etwa
`tests/fixtures/review_responses/claude-slice-review-em-dash-evidence.txt`,
das nach Slice 2 nur noch von der entfernten Normalisierung gebraucht würde.
Produktionswirkung entsteht daraus nicht, weil `src/` abgedeckt ist; ich
werte es als Restrisiko, nicht als Befund.

Zur Deduplizierung in Slice 3 Schritt 4: Die Ablehnung content-gleicher
Komponenten innerhalb **eines** Requests ist semantisch tragfähig, weil die
Komponentennamen (`stdin_prompt`, `packet_manifest`, `system_policy`,
`response_schema`, `start_directive` sowie indizierte Chunks) heute disjunkte
Rollen mit disjunktem Inhalt tragen. Zwei bewusst getrennte Evidenzrollen mit
bytegleichem Inhalt wären eine Modellierungsschwäche, kein legitimer Fall; die
Fail-closed-Ablehnung ist die richtige Wahl. Die Bindung an Reihenfolge,
Request-ID und Writerschemadigest bleibt davon unberührt.

Zur Verlustfreiheit des Slice-Pakets: Die geplante Extraktion aus dem
gebundenen Plan mit Ziel, Akzeptanzkriterien, exakten Pfaden und ausdrücklich
benannten Querverweisen deckt das ab, was Codex heute aus dem Volltext zieht.
Die Sentinelprüfung in Kriterium 1 ist ausführbar und adversarial. Die von der
Reviewfrage erwogene stärkere Planvertragsregel halte ich für unnötig, solange
die Extraktion fail-closed scheitert, wenn ein erwartetes Heading fehlt — das
sollte die Umsetzung sicherstellen, ist aber vom bestehenden Kriterium 1
abgedeckt, weil ein fehlendes Ziel oder Kriterium den Sentineltest bricht.

`src/provider_input_efficiency.py` als neue Datei ist fachlich sinnvoll:
`src/provider_input_budget.py` ist heute reine Policy- und Grenzwertlogik
(`PROVIDER_OPERATIONS`, `PROVIDER_INPUT_COMPONENT_NAMES`), während die geplante
operationenbezogene Effizienzprojektion Recordauswertung ist. Die Trennung
folgt der bestehenden Verantwortungsgrenze.

### Befunde

REVIEWER: claude
NEW_FINDING: C-01 | BLOCKER | Slice 1 Arbeitsschritt 5 macht Transportversion, Request-ID und Rohantwortdigest in v2-AgentResult- und Reviewrecords verpflichtend, aber der Slice-1-Pfad erfasst nicht alle Konstruktionsstellen. Ich habe alle neun Dateien aufgelöst, die `ReviewPayload(` oder `AgentResultPayload(` konstruieren. `tests/test_final_review_preflight.py` enthält null `transport_schema`-Vorkommen und baut ungebundene Payloads in den Zeilen 169, 212, 217 und 450; die Datei steht in keiner der drei Pfadlisten. `tests/test_artifact_projection.py` baut in den Zeilen 72 und 352 ebenfalls ungebundene `ReviewPayload`-Instanzen und steht nur im Pfad von Slice 3. Damit scheitert Slice 1 an seinem eigenen Akzeptanzkriterium `python3 -m pytest tests/ -v`, und die Zusage aus §3, dass jeder Slice für sich die vollständige Matrix besteht, ist verletzt. `tests/test_audit_trail.py` ist dagegen bereits gebunden (Zeile 363) und muss nicht aufgenommen werden. | Der Plan nimmt `tests/test_final_review_preflight.py` und `tests/test_artifact_projection.py` in den exakten Änderungspfad von Slice 1 auf und ergänzt ein Akzeptanzkriterium, das verlangt, dass nach Slice 1 keine Konstruktionsstelle von `ReviewPayload` oder `AgentResultPayload` in `src/` oder `tests/` ohne vollständige native Transportbindung verbleibt; ein providerfreier Test inventarisiert diese Konstruktionsstellen und scheitert bei jeder ungebundenen Instanz.
NEW_FINDING: C-02 | BLOCKER | Slice 2 entfernt den Contract-Repair-Providerbetrieb, lässt den zugehörigen aktiven Maschinenvertrag aber offen. `build_native_review_repair_request()` (`src/native_review_request.py:515`) erzeugt `"request_type": "review_contract_repair_request"`, und jede Anfrage wird gegen `REQUEST_SCHEMA_PATH` validiert (`:377`, `:394`), also gegen `schemas/native-agent-review-request-v2.schema.json`. Dieses Schema führt die Repair-Anfrage als eigenen Zweig im Wurzel-`oneOf` (Zeile 7) und als `$defs/review_contract_repair_request` (Zeile 312) samt `$defs/repair_error`. Die Schemadatei steht in keiner der drei Pfadlisten. Nach Slice 2 bliebe damit ein versionierter, aktiv geladener Requestvertrag bestehen, der einen zurückgezogenen Anfragetyp weiterhin als gültig akzeptiert. Das Akzeptanzkriterium prüft die Grenze ausdrücklich nur „unter `src/`" und kann diese Restpermissivität deshalb nicht erkennen. | Der Plan nimmt `schemas/native-agent-review-request-v2.schema.json` in den exakten Änderungspfad von Slice 2 auf, entfernt dort den `oneOf`-Zweig sowie `$defs/review_contract_repair_request` und `$defs/repair_error`, und ergänzt ein Akzeptanzkriterium mit Gegenprobe: Ein handgebautes Dokument mit `"request_type": "review_contract_repair_request"` wird vom aktiven Requestschema fail-closed abgewiesen, während alle vier verbliebenen Reviewanfrageformen unverändert validieren.
NEW_FINDING: C-03 | BLOCKER | Das dritte Akzeptanzkriterium von Slice 3 ist nicht ausführbar. Es verlangt, dass `local_input_chars` und `local_input_bytes` „gegenüber der vor Slice 2 festgehaltenen kanonischen Fixture" exakt um die entfernten Komponenten sinken. Diese Fixture existiert im Repository nicht, und keiner der drei exakten Änderungspfade legt sie an; `tests/fixtures/` kommt im gesamten Plan nicht vor. Zugleich entfernt Slice 2 Arbeitsschritt 5 den `workflow-prompt` aus dem Codex-Request (heute `src/workflow.py:3881`), sodass der Vorzustand zum Zeitpunkt von Slice 3 nicht mehr berechenbar ist. Der Test könnte die Bytegleichung dann nur gegen eine gelöschte Altimplementierung oder gegen eine hartkodierte Zahl prüfen — beides widerspricht der ausdrücklichen Zusage, keinen willkürlichen Wert zu verwenden, und Nicht-Ziel 7. | Der Plan benennt einen exakten Fixturepfad, der die kanonische Komponentenliste mit Zeichen- und Bytezahlen je betroffener Operation vor der Entfernung einfriert, ordnet seine Erzeugung ausdrücklich Slice 1 oder Slice 2 zu und nimmt ihn in die betreffende Pfadliste auf. Das Akzeptanzkriterium von Slice 3 verweist dann auf diese eingefrorene Datei und verlangt, dass die Differenz je Operation exakt der Summe der entfernten Komponenten entspricht, ohne die alte Aufbaulogik zur Laufzeit zu benötigen.
NEW_FINDING: C-04 | OBSERVATION | Slice 2 Arbeitsschritt 4 verlangt die Umhängung der nativen Adapter „auf eine gemeinsame binäre Basis", benennt die geerbte Fläche aber nicht, obwohl sie nicht trivial ist. `NativeCodexAdapter` erbt von `CodexAdapter` die Methoden `_provider_input_components`, `native_codex_adapter`, `required_hosts` und `stream_filter`; `NativeClaudeReviewAdapter` erbt von `ClaudeAdapter` zusätzlich `bind_reviewer_workspace`, `build_capability_smoke_command` und `reviewer` neben `_provider_input_components`, `native_review_adapter` und `required_hosts`. Keine davon ist Markerlogik, und `bind_reviewer_workspace` trägt die Bindung an `src/review_harness.py` (`src/agent_adapters.py:615`, `:625-629`, `:736`). Ein `_BaseAdapter` existiert bereits (`:242`). Ohne diese Aufzählung ist offen, welche Methoden verschoben statt gelöscht werden, und die Fabrikmethoden `native_codex_adapter`/`native_review_adapter` haben nach dem Löschen der Textadapter keinen Ort mehr. | Der Plan zählt in Slice 2 Arbeitsschritt 4 die zu verschiebende Fläche ausdrücklich auf, benennt `_BaseAdapter` als Ziel und legt fest, wie die Registry die nativen Adapter nach dem Wegfall der Fabrikmethoden direkt konstruiert; ein Akzeptanzkriterium verlangt, dass `NativeCodexAdapter` und `NativeClaudeReviewAdapter` keine Textadapterklasse mehr in ihrer MRO führen und die Reviewer-Workspace- sowie Capability-Smoke-Bindung unverändert funktioniert.

PRE_MORTEM: In drei Monaten scheitert dieses Paket am wahrscheinlichsten nicht am Transportcutover, sondern an der Grenze zwischen Quelltext und Maschinenvertrag. Alle drei Slices sind darauf optimiert, Symbole in `src/` zu finden und zu löschen, und dort ist die Abdeckung nach meiner Referenzauflösung vollständig. Was zurückbleibt, sind die versionierten JSON-Verträge, die keine Pfadliste erfasst: Das Reviewrequest-Schema führt die Repair-Anfrage weiter, und das Artifact-Schema entscheidet, welche Records ohne native Bindung noch gültig sind. Wer Slice 2 implementiert, wird die Vollmatrix grün sehen, weil ein toter Schemazweig keinen Test bricht, und genau deshalb wird niemand ihn entfernen. Ein späteres Paket findet dann einen aktiven Vertrag, der einen Anfragetyp erlaubt, den es seit Monaten nicht mehr gibt, und muss neu entscheiden, ob das eine Lücke oder eine Absicht war. Die zweite, leisere Variante betrifft Slice 3: Die Bytegleichung ist der einzige Nachweis, dass die Evidenzreduktion wirklich stattgefunden hat. Fehlt die eingefrorene Vorher-Fixture, wird sie unter Termindruck durch eine im Test hartkodierte Zahl ersetzt, und ab diesem Moment misst der Test nur noch sich selbst.

PLAN_APPROVAL: NO

STATUS: DONE

## Codex-Planrevision nach Claude-Runde 1

Die drei Blocker und die Observation wurden gegen den aktuellen Code
nachvollzogen und in die normativen Sliceabschnitte integriert. Der historische
Claude-Reviewabschnitt bleibt unverändert als Befundquelle erhalten.

### C-01 – vollständige Payload-Konstruktionsgrenze

Slice 1 umfasst nun zusätzlich `tests/test_final_review_preflight.py`,
`tests/test_artifact_projection.py` und den neuen providerfreien
Retirementguard `tests/test_native_transport_retirement.py`. Neben der
vollständigen Matrix verlangt der Plan eine repositoryweite Inventur aller
direkten Konstruktionen von `ReviewPayload` und `AgentResultPayload`; jeder
Verstoß muss Pfad und Zeile melden. Damit hängt die Pflichtbindung nicht nur
indirekt an zufällig ausgeführten Tests.

### C-02 – geschlossener Requestmaschinenvertrag

`schemas/native-agent-review-request-v2.schema.json` gehört jetzt zum exakten
Slice-2-Pfad. Der Repair-Zweig und seine beiden exklusiven Definitionen werden
gemeinsam mit Builder, Provideroperation und Recovery entfernt. Eine explizite
Negativkontrolle weist die Ablehnung des alten Requesttyps nach; positive
Kontrollen erhalten die vier requestgebundenen Review-Writerschemaformen.

### C-03 – ausführbare Vorher-Nachher-Baseline

Die neue Fixture
`tests/fixtures/provider_input_efficiency/native-only-cutover-baseline-v1.json`
wird bereits in Slice 1 erzeugt, solange die alte Eingabeaufbaulogik noch
existiert, und dort bytegleich reproduziert. Sie bindet synthetische Eingaben,
Komponentenrollen, Inhaltsdigests, Zeichen- und UTF-8-Bytezahlen. Slice 3 liest
die reviewte Fixture nur noch als Referenz und beweist die Differenz als Summe
der explizit entfernten Komponenten, ohne Altbuilder oder willkürliche
Gesamtzahl.

### C-04 – bestimmte Adapterrefaktorierung

Slice 2 benennt `_BaseAdapter` als Ziel und zählt die zu erhaltenden geerbten
Methoden für beide nativen Adapter vollständig auf. Die beiden bisherigen
Native-Fabrikmethoden entfallen; die Registry konstruiert die nativen Adapter
direkt. MRO-, Workspace-, Capability-, Host-, Stream- und
Providerkomponentenregressionen sind nun explizite Abnahmekriterien.

FINDING_RESPONSE: C-01 | ACCEPTED | Slice 1 erfasst die fehlenden Finalreview-Preflight- und Artifactprojektionsfixtures sowie eine providerfreie repositoryweite Konstruktionsinventur für vollständig native ReviewPayload- und AgentResultPayload-Bindungen.
FINDING_RESPONSE: C-02 | ACCEPTED | Slice 2 entfernt den Repair-Anfragetyp atomar aus Builder, Runtime und aktivem JSON-Schema und prüft seine Ablehnung neben den vier weiterhin gültigen Review-Writerschemaformen.
FINDING_RESPONSE: C-03 | ACCEPTED | Die kanonische komponentenbezogene Vorher-Fixture wird in Slice 1 aus der noch aktiven Altlogik erzeugt und in Slice 3 ohne Altcode oder hartkodierte Gesamtsumme für die exakte Zeichen- und Bytegleichung verwendet.
FINDING_RESPONSE: C-04 | ACCEPTED | Die nach _BaseAdapter zu überführende native Fläche, der Wegfall der Fabrikmethoden und die direkte Registrykonstruktion sind vollständig spezifiziert und durch MRO- und Funktionsregressionen gebunden.

PLAN_READY: YES

STATUS: DONE

---

## Claude-Planreview – Runde 2

Manuelles Planreview außerhalb von `run_task`, ohne Provideraufruf, ohne
strukturierte JSON-Ausgabe und ohne vollständige Testsuite. Produktcode und
Tests blieben unverändert; ich habe ausschließlich diesen Abschnitt angehängt.
Git-Bindung geprüft: Branch `feature/native-only-transport-and-efficiency`,
HEAD `9a7da9c66ee187a9044fbf3ff3e92a6b24e216cd`, im Arbeitsbaum nur
Planungsdokumente. Codex' `FINDING_RESPONSE`-Zeilen habe ich nicht übernommen,
sondern jede Zusage gegen den Repositorycode nachgemessen.

### Nachprüfung der Befunde aus Runde 1

**C-01 – Payload-Konstruktionsstellen.** Beide fehlenden Dateien stehen jetzt
im Slice-1-Pfad, und `tests/test_native_transport_retirement.py` ist neu in
Slice 1 **und** Slice 2 gelistet — richtig platziert, weil Slice 2 den Guard um
die Markergrammatik erweitert. Wichtiger als die Inventur ist aber, dass die
eigentliche Grenze am Modell liegt, und die habe ich nachgemessen: Produktiv
existieren nur zwei Konstruktionswege, die Fabriken
`agent_result_payload()` (`src/artifact_bridge.py:113-131`) und
`review_payload()` (`:134-152`), beide mit
`transport_schema/request_id/response_sha256` als `None`-Defaults. Sie haben
fünf Aufrufstellen, alle in `src/orchestrator.py`: `:1094`, `:1841` und `:1939`
übergeben die native Trias, `:1783` und `:1893` nicht — das sind die
Textpfade, erkennbar an `persist_codex_contract(self, result, output: str, …)`
(`:1770-1775`). Nach der Verschärfung aus Arbeitsschritt 5 würde jede
Konstruktion ohne Trias bereits im `__post_init__` scheitern, unabhängig
davon, ob sie positional, über eine Hilfsfactory oder mehrzeilig erfolgt. Die
statische Inventur ist damit kein Ersatz, sondern ein Lokalisierungswerkzeug
für nicht ausgeführten Code — und dafür genügt die Formulierung „jede direkte
Konstruktion … nennt bei einem Verstoß Pfad und Zeile". `src/orchestrator.py`
liegt im Slice-1-Pfad, sodass die beiden Textaufrufstellen im selben Slice
entschärft werden können. Slice 1 bleibt auf dieser Achse atomar grün.
`tests/test_audit_trail.py` bindet bereits nativ (Zeile 363) und fehlt zu
Recht.

**C-02 – Repair-Maschinenvertrag.** `schemas/native-agent-review-request-v2.schema.json`
steht jetzt im Slice-2-Pfad, und Arbeitsschritt 6 benennt Wurzel-`oneOf`-Zweig
und beide Definitionen. Ich habe die Exklusivität geprüft: `repair_error` wird
im gesamten Schema ausschließlich aus
`review_contract_repair_request` referenziert (Zeile 328), der Zweig selbst nur
aus dem Wurzel-`oneOf` (Zeile 7). Beide Definitionen sind also wirklich
exklusiv und gemeinsam entfernbar. Builder (`src/native_review_request.py:515`),
Provideroperation (`src/provider_input_budget.py:26`), Runtimeprompt
(`src/agent_runtime.py`) und die zugehörigen Tests liegen sämtlich im
Slice-2-Pfad. Auf die Frage nach weiteren Registern: Eine Suche über
`schemas/` außerhalb des Requestschemas und über `src/native_provider_schema.py`
findet **null** Repair-Treffer; weder Capability- noch Exceptionregister noch
das Resultatschema behaupten den Typ als aktiv. Die Negativprobe ist
providerfrei ausführbar, weil Requests gegen `REQUEST_SCHEMA_PATH` validiert
werden (`src/native_review_request.py:377`, `:394`). Die positiven Kontrollen
sind fachlich korrekt benannt: Plan, initialer Slice, Konvergenz und Final sind
genau die vier Reviewformen, die `tests/test_native_contract_differential.py`
neben den vier `NativeCodexRequestKind` zu acht Writerdigests zusammenführt.

**C-03 – Baseline-Fixture.** Die Fixture ist in Slice 1 (Zeile 229) und Slice 3
(Zeile 424) exakt gelistet, und Arbeitsschritt 8 bindet Basiscommit,
Generatorversion, Digest der synthetischen Eingaben, Rollennamen,
Inhaltsdigests, Zeichen- und UTF-8-Bytezahlen — das sind genug Anker, damit die
Datei nicht nur behauptete Zahlen trägt, sondern selbstkonsistent prüfbar ist,
wie es das Slice-1-Akzeptanzkriterium auch verlangt. Die Erzeugung in Slice 1
ist zeitlich korrekt platziert: Slice 1 ändert Transportbindung, Registry und
Recordmodell, lässt den Evidenzaufbau aber unberührt; der `workflow-prompt`
entsteht weiterhin in `src/workflow.py:3881` und der vollständige Plan als
Komponente `approved_work_plan` in `:3884-3890`. Die Baseline erfasst also
genau den nativen Vorzustand. Slice 3 Arbeitsschritt 6 verbietet ausdrücklich
Altbuilder-Import und Regeneration und verlangt die operationenbezogene
Summengleichung samt Namens- und Digestgleichheit der unveränderten
Komponenten. Damit ist jede Klausel meines ursprünglichen Abnahmetests
erfüllt.

**C-04 – Adapterfläche.** `_BaseAdapter` ist das richtige Ziel und existiert
bereits (`src/agent_adapters.py:242`); ich habe geprüft, dass seine
`validate_process_output()` nur Providerdiagnosemarker in stderr auswertet und
keine Ergebnisgrammatik enthält — eine Umhängung schleppt also keine
Markerlogik mit. Die Aufzählung in Arbeitsschritt 4 stimmt exakt mit der
gemessenen Vererbung überein: `NativeCodexAdapter` erbt von `CodexAdapter`
`_provider_input_components`, `required_hosts`, `stream_filter` und
`native_codex_adapter`; `NativeClaudeReviewAdapter` erbt von `ClaudeAdapter`
`_provider_input_components`, `bind_reviewer_workspace`,
`build_capability_smoke_command`, `required_hosts`, `reviewer` und
`native_review_adapter`. Der Plan nennt alle nicht-Fabrikmethoden beider Seiten
vollständig, erklärt den Wegfall beider Fabrikmethoden und die direkte
Registrykonstruktion aus den aufgelösten `AgentSettings`. MRO-, Workspace-,
Capability-, Host-, Stream- und Providerkomponentenregressionen sind
Abnahmekriterien. Vollständig.

### Folgedefekt der Revision

Die Revision hat an einer Stelle eine neue Lücke erzeugt, und zwar genau an der
Naht zwischen den beiden Slices, die C-03 verbindet.

Slice 1 Arbeitsschritt 8 verlangt einen providerfreien Test, der die Fixture
„in Slice 1 bytegleich aus der zu diesem Zeitpunkt aktiven Aufbaulogik"
rekonstruiert. Slice 2 Arbeitsschritt 5 entfernt `workflow-prompt` aus dem
Codex-Request. Ab diesem Moment kann die Rekonstruktion nicht mehr gelingen.
Ich habe den gesamten Plan nach einer Auflösung durchsucht: Die Zeichenketten
„rekonstru", „regenerier" und „Baseline" erscheinen ausschließlich in Slice 1
(Zeilen 229, 254-259, 277) und Slice 3 (Zeilen 424, 447-455, 469). Slice 2
erwähnt die Fixture und ihren Rekonstruktionstest an **keiner** Stelle — weder
in den acht Arbeitsschritten noch in den Akzeptanzkriterien.

Daraus folgen zwei Wirkungen. Erstens ist Slice 2 nicht mehr atomar grün: Wer
den Plan wörtlich umsetzt, entfernt `workflow-prompt` und lässt einen Test
zurück, der zwingend rot wird, während Slice 2 gleichzeitig
`python3 -m pytest tests/ -v` als Kriterium führt. Zweitens — und das wiegt
schwerer — ist die naheliegendste Reparatur unter Termindruck, die Fixture in
Slice 2 einfach neu zu erzeugen. Damit wäre die Baseline nachträglich auf den
Nach-Slice-2-Zustand gesetzt, die Differenz in Slice 3 fiele um genau den
`workflow-prompt`-Anteil kleiner aus, und das Akzeptanzkriterium bliebe grün,
ohne noch etwas zu messen. Der gesamte Nutzen von C-03 ginge lautlos verloren.

Verschärfend ist, dass der Plan den Hostpfad des Rekonstruktionstests nicht
benennt. Der semantisch naheliegende Ort `tests/test_provider_input_efficiency.py`
steht nur im Slice-3-Pfad und kann in Slice 1 gar nicht angelegt werden; die
einzige in Slice 1 und Slice 2 gemeinsam gelistete Datei ist
`tests/test_native_transport_retirement.py`, was für einen Baseline-Generator
kein offensichtlicher Ort ist. Die Zuordnung bleibt damit dem Zufall der
Umsetzung überlassen, und nur eine der plausiblen Wahlen ist überhaupt
reparierbar.

### Erneutes Gesamtreview des revidierten Plans

Die übrigen acht Prüffragen ergeben keinen weiteren Befund.

Reihenfolge und Atomizität bleiben richtig. Die Textzweige des Workflows sind
an `protocol_binding.codex_result_transport` beziehungsweise
`claude_review_transport` gebunden (`src/workflow.py:1616`, `:2035`, `:2158`);
Slice 1 macht sie durch die Pflichtbindung unerreichbar, ohne Slice 2
vorauszusetzen, und die dann toten Treiberpfade `persist_codex_contract`
(`src/orchestrator.py:1770`) und das Textreview-Pendant (`:1893`) liegen im
Slice-1-Pfad. Slice 2 setzt umgekehrt nichts aus Slice 3 voraus.

Die erhaltenswerten Parser sind sauber abgegrenzt: Task- und Planverträge
(`src/task_contract.py`, `src/plan_handoff.py`), JSON- und Schemasicherheit
(`src/schema_validation.py`) und die Providerdiagnose in
`_BaseAdapter.validate_process_output` liegen außerhalb der Verbotsmenge und
sind in Slice 2 ausdrücklich als erhaltene, getestete Fläche benannt.

Für Slice 3 habe ich geprüft, ob die Reduktion eine Zusicherung verliert: Die
Komponente `approved_work_plan` wird von keinem Test namentlich geprüft — die
einzige Fundstelle im gesamten Repository ist `src/workflow.py:3884-3890` —,
sodass ihre Ersetzung keine unlistete Datei bricht. Die Sentinelprüfungen
decken Ziel, Kriterien, Pfade, Plandigest und Querverweise ab; Request-ID,
Writerschemadigest und Evidenzdigests bleiben laut Arbeitsschritt 4
deterministisch gekoppelt. Die Trennung zwischen lokalem Zeichen-/Byteproxy und
tatsächlicher Providerusage ist im Recordmodell bereits angelegt
(`provider_input_measurement` mit `components`, `total_chars`, `total_bytes`;
`provider_usage` mit durchgängig nullbaren Tokenfeldern), sodass „fehlende
Usage als `unknown`" darstellbar ist.

Ein Randrisiko ohne Findingcharakter: `tests/test_language_consistency.py`
steht in Slice 1 und Slice 2, nicht aber in Slice 3, obwohl Slice 3 `README.md`
ändert. Die dortigen README-Prüfungen sind allerdings an den CLI-Optionssatz
und feste Überschriften gebunden, die Slice 3 nicht anfasst; eine reine
Ergänzung um die Effizienzprojektion bricht keine Zusicherung.

### Befunde

REVIEWER: claude
FINDING_STATUS: C-01 | CLOSED | `tests/test_final_review_preflight.py` und `tests/test_artifact_projection.py` stehen jetzt im exakten Slice-1-Pfad, ergänzt um den providerfreien `tests/test_native_transport_retirement.py`, der zusätzlich in Slice 2 gelistet ist. Die tragende Grenze ist dabei nicht die statische Inventur, sondern die Modellverschärfung: Produktiv existieren nur die beiden Fabriken `agent_result_payload()` (`src/artifact_bridge.py:113-131`) und `review_payload()` (`:134-152`) mit fünf Aufrufstellen in `src/orchestrator.py`; `:1094`, `:1841` und `:1939` übergeben die native Trias, die Textpfade `:1783` und `:1893` nicht, und `src/orchestrator.py` liegt im Slice-1-Pfad. Nach Arbeitsschritt 5 scheitert jede Konstruktion ohne Transportversion, Request-ID und Rohantwortdigest bereits im `__post_init__`, unabhängig von Positionsargumenten, Hilfsfactorys oder mehrzeiliger Schreibweise; die Inventur bleibt als Lokalisierung für nicht ausgeführten Code sinnvoll. `tests/test_audit_trail.py` ist bereits nativ gebunden (Zeile 363) und fehlt zu Recht.
FINDING_STATUS: C-02 | CLOSED | `schemas/native-agent-review-request-v2.schema.json` steht im exakten Slice-2-Pfad, und Arbeitsschritt 6 entfernt den Wurzel-`oneOf`-Zweig (Zeile 7) gemeinsam mit `$defs/review_contract_repair_request` (Zeile 312) und `$defs/repair_error` (Zeile 303). Ich habe die Exklusivität verifiziert: `repair_error` wird im Schema ausschließlich aus dem Repair-Zweig referenziert (Zeile 328), der Zweig ausschließlich aus dem Wurzel-`oneOf`. Builder (`src/native_review_request.py:515`), Provideroperation (`src/provider_input_budget.py:26`), Runtimeprompt und Tests liegen alle im Slice-2-Pfad. Eine Suche über `schemas/` außerhalb des Requestschemas und über `src/native_provider_schema.py` findet null Repair-Treffer, es gibt also kein weiteres Register, das den Typ als aktiv behauptet. Die Negativprobe ist providerfrei ausführbar, weil Requests gegen `REQUEST_SCHEMA_PATH` validiert werden (`:377`, `:394`), und die vier positiven Kontrollen Plan, initialer Slice, Konvergenz und Final entsprechen exakt den vier Reviewformen der bestehenden Writerdigest-Regression.
FINDING_STATUS: C-03 | CLOSED | Jede Klausel meines Abnahmetests ist erfüllt: Der exakte Fixturepfad `tests/fixtures/provider_input_efficiency/native-only-cutover-baseline-v1.json` ist benannt, seine Erzeugung ausdrücklich Slice 1 zugeordnet und in dessen Pfadliste aufgenommen (Zeile 229), erneut in Slice 3 (Zeile 424). Die Bindung an Basiscommit, Generatorversion, Digest der synthetischen Eingaben, Rollennamen, Inhaltsdigests, Zeichen- und UTF-8-Bytezahlen macht die Datei selbstkonsistent prüfbar statt hartkodiert. Die zeitliche Platzierung stimmt, weil Slice 1 den Evidenzaufbau unberührt lässt — `workflow-prompt` entsteht weiterhin in `src/workflow.py:3881`, der Vollplan als `approved_work_plan` in `:3884-3890`. Slice 3 Arbeitsschritt 6 verbietet Altbuilder-Import und Regeneration und verlangt die operationenbezogene Summengleichung samt Namens- und Digestgleichheit der unveränderten Komponenten, also ohne gelöschten Code und ohne Gesamtprozentwert. Die verbleibende Lücke betrifft nicht mehr die Konstruktion der Baseline, sondern ihre Erhaltung über Slice 2 hinweg und ist als `C-05` eröffnet.
FINDING_STATUS: C-04 | CLOSED | Slice 2 Arbeitsschritt 4 benennt `_BaseAdapter` (`src/agent_adapters.py:242`) als Ziel und zählt die zu erhaltende Fläche vollständig auf. Der Abgleich mit der gemessenen Vererbung ist exakt: `NativeCodexAdapter` erbt von `CodexAdapter` `_provider_input_components`, `required_hosts`, `stream_filter` und die Fabrik `native_codex_adapter`; `NativeClaudeReviewAdapter` erbt von `ClaudeAdapter` `_provider_input_components`, `bind_reviewer_workspace`, `build_capability_smoke_command`, `required_hosts`, `reviewer` und die Fabrik `native_review_adapter`. Der Plan nennt alle nicht-Fabrikmethoden beider Seiten, erhält die Workspacebindung an `src/review_harness.py` und den Capability-Smoke ausdrücklich, erklärt den Wegfall beider Fabrikmethoden und die direkte Registrykonstruktion aus den aufgelösten `AgentSettings`. Ich habe zusätzlich geprüft, dass `_BaseAdapter.validate_process_output()` nur Providerdiagnosemarker in stderr auswertet und keine Ergebnisgrammatik enthält, die Umhängung also keine Markerlogik mitschleppt. MRO- und Funktionsregressionen sind als Abnahmekriterien gebunden und reichen für beide Richtungen aus.
NEW_FINDING: C-05 | BLOCKER | Die Revision lässt die Naht zwischen Slice 1 und Slice 3 offen. Slice 1 Arbeitsschritt 8 fordert einen providerfreien Test, der die Baseline-Fixture „in Slice 1 bytegleich aus der zu diesem Zeitpunkt aktiven Aufbaulogik" rekonstruiert; Slice 2 Arbeitsschritt 5 entfernt `workflow-prompt` aus dem Codex-Request und macht diese Rekonstruktion damit unmöglich. Slice 2 erwähnt Fixture und Rekonstruktionstest an keiner Stelle: Die Zeichenketten „rekonstru", „regenerier" und „Baseline" erscheinen im gesamten Plan nur in Slice 1 (Zeilen 229, 254-259, 277) und Slice 3 (Zeilen 424, 447-455, 469). Erstens ist Slice 2 damit nicht atomar grün, obwohl es `python3 -m pytest tests/ -v` als Kriterium führt. Zweitens ist die naheliegendste Reparatur, die Fixture in Slice 2 neu zu erzeugen — dann läge die Baseline auf dem Nach-Slice-2-Zustand, die Differenz in Slice 3 fiele um genau den `workflow-prompt`-Anteil kleiner aus, und das Kriterium bliebe grün, ohne noch etwas zu messen; der Nutzen von C-03 ginge lautlos verloren. Verschärfend benennt der Plan den Hostpfad des Rekonstruktionstests nicht: `tests/test_provider_input_efficiency.py` steht nur im Slice-3-Pfad und kann in Slice 1 nicht angelegt werden, und die einzige in Slice 1 und Slice 2 gemeinsam gelistete Testdatei ist `tests/test_native_transport_retirement.py`. | Der Plan benennt in Slice 1 den exakten Hostpfad des Baseline-Rekonstruktionstests und nimmt ihn in die Pfadlisten von Slice 1 und Slice 2 auf. Slice 2 erhält einen ausdrücklichen Arbeitsschritt, der diesen Test auf eine reine Unveränderlichkeitsprüfung zurückbaut: Die Fixture wird nach Slice 1 nie wieder erzeugt, und ein Akzeptanzkriterium von Slice 2 und Slice 3 verlangt, dass ihr SHA-256 gegenüber dem in Slice 1 reviewten Stand unverändert ist. Ein providerfreier Test weist zusätzlich nach, dass die Baseline mindestens eine Komponente enthält, die im aktuellen Aufbau nicht mehr vorkommt, sodass eine stillschweigende Neuerzeugung auf den Nach-Slice-2-Zustand fail-closed auffällt.

PRE_MORTEM: In drei Monaten scheitert dieses Paket am wahrscheinlichsten nicht am Transport und nicht am Parserlöschen — beide Slices sind jetzt sauber geschnitten und an messbaren Grenzen verankert. Was kippt, ist der Nachweis der Einsparung. Die Baseline-Fixture ist die einzige Datei im Paket, deren Wert allein daraus stammt, dass sie einen Zustand konserviert, den es danach nicht mehr gibt, und sie ist zugleich die einzige, die kein Test gegen Veränderung schützt. Slice 2 macht ihren Rekonstruktionstest zwangsläufig rot, und die schnellste grüne Antwort ist ein Regenerationslauf. Danach misst Slice 3 die Differenz zwischen dem Nach-Slice-2-Zustand und sich selbst, das Akzeptanzkriterium bleibt grün, und niemand bemerkt, dass die eigentliche Reduktion — der vollständige alte Markerprompt — aus der Rechnung verschwunden ist. Die zweite, leisere Variante betrifft die Adapterumhängung: `_BaseAdapter` ist heute frei von Ergebnisgrammatik, aber `build_capability_smoke_command` existiert nur auf der Claude-Seite. Wer sie beim Refaktorieren nach oben zieht, gibt sie auch dem Codex-Adapter, und die MRO-Regression bleibt grün, weil sie nur prüft, was fehlt, nicht was zu viel da ist.

PLAN_APPROVAL: NO

STATUS: DONE

---

## Codex-Planrevision nach Claude-Runde 2

Claude hat C-01 bis C-04 geschlossen und mit C-05 einen Folgedefekt an der
Lebensdauer der Effizienzbaseline gefunden. Der normative Plan behandelt die
Baseline jetzt als unveränderlichen, in Slice 1 erzeugten Messanker statt als
über alle Slices regenerierbare Testausgabe.

### C-05 – Baseline-Lebenszyklus über alle Slices

`tests/test_provider_input_efficiency.py` ist nun der explizite Testhost und
gehört zu den Änderungspfaden aller drei Slices:

1. In Slice 1 erzeugt und rekonstruiert er die Baseline aus der noch aktiven
   Vorher-Aufbaulogik.
2. Ein separater Lock bindet vollständigen Baseline-SHA-256, Basiscommit,
   Fixtureversion und die erwarteten später entfernten Komponenten.
3. In Slice 2 wird derselbe Test auf reine Read-only-Integritätsprüfung
   umgebaut. Er darf Baseline und Lock nicht mehr erzeugen und muss mindestens
   eine im Lock benannte Altkomponente als im aktuellen Aufbau abwesend
   nachweisen.
4. Baseline und Lock fehlen absichtlich in den Änderungspfaden von Slice 2 und
   Slice 3. Scope-/Diffprüfung und der Lockdigest schützen sie gemeinsam gegen
   stilles Regenerieren.
5. In Slice 3 erfolgen Differenzrechnung und Usageprojektion erst nach erneuter
   Lock-/Fixtureprüfung.

Zusätzlich wurde Claudes Pre-Mortem zur Adapterfläche präzisiert: Nur
providerneutrale Prozessmechanik gehört nach `_BaseAdapter`.
Reviewer-Workspace, Reviewerrolle und Capability-Smoke bleiben ausschließlich
auf `NativeClaudeReviewAdapter`; eine negative API-Regression verhindert ihre
versehentliche Vererbung an Codex.

FINDING_RESPONSE: C-05 | ACCEPTED | Der Baseline-Testhost ist ab Slice 1 benannt, wird in Slice 2 ausdrücklich von Rekonstruktion auf unveränderliche Lockprüfung umgebaut und weist eine tatsächlich entfernte Altkomponente nach; Baseline und Lock sind in späteren Slices read-only und durch Slice-1-Digest plus Scopegrenze gebunden.

PLAN_READY: YES

STATUS: DONE

---

## Claude-Planreview – Runde 3

Manuelles Planreview außerhalb von `run_task`, ohne Provideraufruf, ohne
strukturierte JSON-Ausgabe und ohne vollständige Testsuite. Produktcode und
Tests blieben unverändert; angehängt wurde ausschließlich dieser Abschnitt.
Git-Bindung geprüft: Branch `feature/native-only-transport-and-efficiency`,
HEAD `9a7da9c66ee187a9044fbf3ff3e92a6b24e216cd`, im Arbeitsbaum nur
Planungsdokumente. Codex' `FINDING_RESPONSE` habe ich nicht übernommen.

### 1. Nachprüfung von C-05

Die Zuordnung stimmt exakt. `tests/test_provider_input_efficiency.py` steht in
allen drei Pfadlisten; `native-only-cutover-baseline-v1.json` und
`native-only-cutover-baseline-v1.lock.json` stehen ausschließlich im
Slice-1-Pfad und werden in Slice 2 Arbeitsschritt 7 und Slice 3
Arbeitsschritt 6 ausdrücklich als Read-only-Abhängigkeiten geführt. Die
zeitliche Platzierung bleibt korrekt, weil Slice 1 den Evidenzaufbau nicht
anfasst: `workflow-prompt` entsteht weiterhin in `src/workflow.py:3881`, die
Vollplan-Komponente `approved_work_plan` in `:3884-3890`.

Der Lock bindet Baseline-SHA-256, Basiscommit, Fixtureversion und die erwartet
entfernten Komponenten. Die synthetischen Eingabedigests liegen in der
Baseline selbst und sind über den Gesamtdigest transitiv mitgebunden — das
genügt. Der Slice-1-Reviewbericht nennt beide Digests, womit der externe Anker
außerhalb des veränderbaren Dateipaars liegt.

Zur Kernfrage, welche Grenze den in Slice 1 reviewten Digest trägt: Der
Lockdigest allein trägt sie **nicht**. Wer Fixture und Lock gemeinsam und
konsistent neu erzeugt, erfüllt die Digestprüfung trivial. Die Zusage im
Slice-2-Kriterium, eine Neuerzeugung scheitere „sowohl am Lockdigest als auch
am Scope-/Diffcheck", ist für diesen Fall in ihrer ersten Hälfte zu
optimistisch.

Tragend ist stattdessen ein anderer, tatsächlich wirksamer Verbund, den ich
durchgespielt habe. Eine gemeinsame Regeneration nach Slice 2 muss sich für
eine von drei Varianten entscheiden, und alle drei scheitern:

- Bleibt `workflow-prompt` in der Liste der entfernten Komponenten, so ist die
  neue Baseline bereits ohne diese Komponente. In Slice 3 wird
  `baseline_chars - current_chars` dann kleiner als
  `Summe(chars der entfernten Komponenten)`, die Gleichung bricht.
- Wird die Liste ehrlich auf den Nachzustand nachgezogen, ist sie leer, und
  die Slice-2-Gegenprobe „mindestens eine im Lock gebundene Komponente fehlt
  im aktuellen Aufbau" hat nichts mehr zu prüfen und scheitert.
- Enthält die Liste nur die Vollplan-Komponente, scheitert dieselbe
  Slice-2-Gegenprobe, weil diese Komponente in Slice 2 noch vorhanden ist und
  erst in Slice 3 entfällt.

Dieselbe Verriegelung greift für eine Regeneration in Slice 3: Bei leerer
Liste fällt das Kriterium „der aktuelle Aufbau enthält mindestens eine im Lock
gebundene entfernte Altkomponente nicht mehr" aus, bei kopierter Liste bricht
die Summengleichung. Die Absenzprüfung in Slice 2 und die Differenzgleichung
in Slice 3 verriegeln einander also gegenseitig, und der Scope-/Diffcheck
gegen den Slice-1-Commit ist die dritte, unabhängige Grenze. Damit ist die
gemeinsame Manipulation abgedeckt — nur eben durch den Verbund und nicht durch
den Lockdigest. Jeder Slice bleibt mit seinem jeweiligen Zustand grün, weil
Slice 2 den Testhost ausdrücklich von Rekonstruktion auf Integritätsprüfung
umbaut und der Host in allen drei Pfadlisten steht.

### 2. Negative Adapterflächenkontrolle – hier bricht die Revision

Die Antwort auf mein Runde-2-Pre-Mortem trifft die richtige Absicht, aber die
neue Negativkontrolle überschießt und widerspricht der bestehenden
Architektur.

`_BaseAdapter` führt heute `reviewer = False` als **Klassenattribut** und
`bind_reviewer_workspace()` als No-op-Methode
(`src/agent_adapters.py:242-262`). Beides ist keine Claude-Spezialität,
sondern gemeinsame Infrastruktur:

- `bind_reviewer_workspace` ist Bestandteil des `AgentAdapter`-Protocols
  (`src/agent_adapters.py:214`), gegen das die gesamte Runtime programmiert;
  `src/agent_runtime.py:1014` ruft es generisch auf `adapter` auf.
- `reviewer` ist der **polymorphe Diskriminator**, den `src/agent_runtime.py`
  an fünf Stellen auf jedem Adapter liest: `:429` für die Streambehandlung,
  `:784` für die Wahl zwischen `read-only-reviewer` und
  `workspace-write-implementer`, sowie `:996`, `:1005` und `:1072` für
  Execution-Root und Workspacebindung. Der Codex-Adapter **muss** dieses
  Attribut besitzen, damit dort der `else`-Zweig greift.

Das neue Slice-2-Akzeptanzkriterium „`NativeCodexAdapter` besitzt weder
`reviewer` noch `build_capability_smoke_command` oder eine
Reviewer-Workspacebindung" ist damit nicht implementierbar. Entfernt man
`reviewer` vom Codex-Adapter, scheitert `agent_runtime.py` an fünf Stellen mit
`AttributeError`; entfernt man `bind_reviewer_workspace`, verletzt der Adapter
das `AgentAdapter`-Protocol. Ebenso widerspricht die Anweisung in
Arbeitsschritt 4, `reviewer` und die Reviewer-Workspacebindung blieben
„ausschließlich auf `NativeClaudeReviewAdapter`", derselben Zeile 4 desselben
Arbeitsschritts, die diese Fläche für Claude als *geerbte* zu erhaltende
Fläche aufzählt — und beides zusammen widerspricht meinem in Runde 2
geschlossenen C-04-Abnahmetext, der verlangt, dass die
Reviewer-Workspacebindung „unverändert funktioniert".

Berechtigt ist die Kontrolle nur für `build_capability_smoke_command`: Diese
Methode existiert tatsächlich ausschließlich auf `ClaudeAdapter` und war der
konkrete Gegenstand meines Pre-Mortems. Für sie ist eine negative
API-Regression sinnvoll und ausführbar.

Positiv festzuhalten: `_BaseAdapter.validate_process_output()` wertet nur
Providerdiagnosemarker in stderr aus und enthält keine Ergebnisgrammatik; die
Basis bleibt also frei von Markerlogik, und Codex-spezifische Methoden
(`build_command`, `capability`, `extract_output`, `prepare_native_provider_input`)
liegen bereits auf `NativeCodexAdapter`. MRO- und Funktionsregressionen
reichen aus, sobald das Kriterium die drei Flächen korrekt unterscheidet.

### 3. Erhaltung von C-01 bis C-04

Die C-05-Revision hat die Pfadlisten nur additiv verändert. Slice 1 führt
weiterhin `tests/test_final_review_preflight.py` und
`tests/test_artifact_projection.py` sowie die Konstruktionsinventur
(Arbeitsschritt 7); Slice 2 führt weiterhin
`schemas/native-agent-review-request-v2.schema.json` und den unveränderten
Arbeitsschritt 6; die Vorher-Nachher-Messung ist durch Lock und Read-only-
Bindung stärker geworden. Die Adapteraufzählung in Arbeitsschritt 4 ist
inhaltlich unverändert vollständig — der Widerspruch entsteht erst durch die
neu angehängte Ausschließlichkeitsklausel und das neue Kriterium und wird
deshalb als eigener Befund geführt.

### 4. Erneutes Gesamtplanreview

Die übrigen zehn Prüffragen ergeben keinen weiteren Befund. Reihenfolge und
Atomizität bleiben korrekt: Die Textzweige hängen an
`protocol_binding.codex_result_transport` beziehungsweise
`claude_review_transport` (`src/workflow.py:1616`, `:2035`, `:2158`), Slice 1
macht sie ohne Slice-2-Voraussetzung unerreichbar, und die dann toten
Treiberpfade `persist_codex_contract` (`src/orchestrator.py:1770`) und das
Textreview-Pendant (`:1893`) liegen im Slice-1-Pfad. Die Ablehnung alter
States vor externen Seiteneffekten bleibt an `src/state_io.py` und
`src/artifact_migration.py` verankert, beide in Slice 1. Die erhaltenswerten
Parser — Taskvertrag, Plan-Handoff, JSON-/Schemasicherheit und die
Providerdiagnose in `_BaseAdapter.validate_process_output` — sind ausdrücklich
außerhalb der Verbotsmenge. Request-ID, Writerschemadigest und Evidenzdigests
bleiben laut Slice 3 Arbeitsschritt 4 deterministisch gekoppelt; die Trennung
zwischen lokalem Zeichen-/Byteproxy und tatsächlicher Providerusage ist im
Recordmodell bereits angelegt (`provider_input_measurement` mit `components`,
`total_chars`, `total_bytes`; `provider_usage` mit durchgängig nullbaren
Tokenfeldern). Alle Akzeptanzkriterien sind providerfrei ausführbar und ohne
gelöschte Altlogik oder willkürliche Kennzahlen formuliert — mit der einen
Ausnahme, die unten als `C-06` steht.

Ein Randrisiko ohne Findingcharakter bleibt unverändert:
`tests/test_language_consistency.py` steht in Slice 1 und Slice 2, nicht aber
in Slice 3, obwohl Slice 3 `README.md` ändert. Die dortigen README-Prüfungen
binden den CLI-Optionssatz und feste Überschriften, die Slice 3 nicht anfasst.

### Befunde

REVIEWER: claude
FINDING_STATUS: C-01 | CLOSED | Unverändert geschlossen und durch die C-05-Revision nicht berührt. `tests/test_final_review_preflight.py` und `tests/test_artifact_projection.py` stehen weiterhin im exakten Slice-1-Pfad, ergänzt um `tests/test_native_transport_retirement.py` in Slice 1 und Slice 2. Die tragende Grenze bleibt die Modellverschärfung aus Arbeitsschritt 5: Produktiv existieren nur die Fabriken `agent_result_payload()` (`src/artifact_bridge.py:113-131`) und `review_payload()` (`:134-152`) mit fünf Aufrufstellen in `src/orchestrator.py`, davon `:1783` und `:1893` als Textpfade, und `src/orchestrator.py` liegt im Slice-1-Pfad. Jede Konstruktion ohne Transportversion, Request-ID und Rohantwortdigest scheitert danach im `__post_init__`, unabhängig von Positionsargumenten, Hilfsfactorys oder mehrzeiliger Schreibweise.
FINDING_STATUS: C-02 | CLOSED | Unverändert geschlossen. `schemas/native-agent-review-request-v2.schema.json` steht weiterhin im Slice-2-Pfad, und Arbeitsschritt 6 entfernt den Wurzel-`oneOf`-Zweig (Zeile 7) gemeinsam mit `$defs/review_contract_repair_request` (Zeile 312) und `$defs/repair_error` (Zeile 303). Die Exklusivität habe ich in Runde 2 verifiziert: `repair_error` wird ausschließlich aus dem Repair-Zweig referenziert (Zeile 328), und weder `schemas/` außerhalb des Requestschemas noch `src/native_provider_schema.py` enthält einen Repair-Treffer. Builder, Provideroperation, Runtimeprompt und Tests liegen sämtlich im Slice-2-Pfad; die Negativprobe ist providerfrei ausführbar, weil Requests gegen `REQUEST_SCHEMA_PATH` validiert werden.
FINDING_STATUS: C-03 | CLOSED | Durch die C-05-Revision zusätzlich gehärtet statt geschwächt. Die Baseline wird weiterhin in Slice 1 aus der noch aktiven Altlogik erzeugt und dort bytegleich rekonstruiert, ist an Basiscommit, Generatorversion, synthetische Eingabedigests, Rollennamen, Inhaltsdigests, Zeichen- und UTF-8-Bytezahlen gebunden und wird in Slice 3 ausschließlich als Read-only-Referenz gelesen. Arbeitsschritt 6 verbietet Altbuilder-Import und Regeneration und verlangt die operationenbezogene Summengleichung samt Namens- und Digestgleichheit der unveränderten Komponenten, also ohne gelöschten Code und ohne hartkodierte Gesamtzahl.
FINDING_STATUS: C-04 | CLOSED | Die Aufzählung der zu erhaltenden Adapterfläche in Slice 2 Arbeitsschritt 4 ist inhaltlich unverändert und deckt sich weiterhin exakt mit der gemessenen Vererbung: für Codex `_provider_input_components`, `required_hosts`, `stream_filter` plus Wegfall der Fabrik `native_codex_adapter`; für Claude zusätzlich `bind_reviewer_workspace`, `build_capability_smoke_command` und `reviewer` plus Wegfall von `native_review_adapter`. `_BaseAdapter` bleibt das richtige Ziel, und seine `validate_process_output()` enthält nachweislich nur Providerdiagnosemarker, keine Ergebnisgrammatik. Der in Runde 3 gefundene Widerspruch entsteht nicht aus dieser Aufzählung, sondern aus der neu angehängten Ausschließlichkeitsklausel und dem neuen Negativkriterium; er wird deshalb als eigener Befund `C-06` geführt und öffnet `C-04` nicht erneut.
FINDING_STATUS: C-05 | CLOSED | Der Baseline-Lebenszyklus ist über alle drei Slices geschlossen. `tests/test_provider_input_efficiency.py` steht in allen drei Pfadlisten; Fixture und Lock stehen ausschließlich im Slice-1-Pfad und werden in Slice 2 Arbeitsschritt 7 und Slice 3 Arbeitsschritt 6 als Read-only geführt. Slice 2 baut den Testhost ausdrücklich von Rekonstruktion auf Integritätsprüfung um, verbietet Regeneration und verlangt die Abwesenheit mindestens einer im Lock gebundenen Altkomponente, insbesondere `workflow-prompt`; der Slice-1-Reviewbericht trägt Baseline- und Lockdigest als externen Anker außerhalb des veränderbaren Dateipaars. Die gemeinsame Manipulation von Fixture und Lock habe ich in allen drei möglichen Varianten durchgespielt: Bleibt `workflow-prompt` in der Entferntliste, bricht die Slice-3-Summengleichung; wird die Liste ehrlich nachgezogen, ist sie leer und die Slice-2-Absenzprüfung scheitert; enthält sie nur die Vollplan-Komponente, scheitert dieselbe Prüfung, weil diese in Slice 2 noch vorhanden ist. Tragend ist damit nicht der Lockdigest, wie das Slice-2-Kriterium nahelegt, sondern die gegenseitige Verriegelung von Absenzprüfung und Differenzgleichung plus der Scope-/Diffcheck gegen den Slice-1-Commit; die Wirkung ist erreicht, die Begründung im Plantext ist nur ungenauer als der Mechanismus.
NEW_FINDING: C-06 | BLOCKER | Die in dieser Revision ergänzte negative Adapterflächenkontrolle widerspricht der bestehenden Architektur und ist nicht implementierbar. Das neue Slice-2-Akzeptanzkriterium verlangt „`NativeCodexAdapter` besitzt weder `reviewer` noch `build_capability_smoke_command` oder eine Reviewer-Workspacebindung", und Arbeitsschritt 4 verlangt, `reviewer` und die Workspacebindung blieben „ausschließlich auf `NativeClaudeReviewAdapter`". Beide Flächen sind jedoch gemeinsame Infrastruktur: `_BaseAdapter` führt `reviewer = False` als Klassenattribut und `bind_reviewer_workspace()` als No-op (`src/agent_adapters.py:242-262`), `bind_reviewer_workspace` ist Bestandteil des `AgentAdapter`-Protocols (`:214`) und wird in `src/agent_runtime.py:1014` generisch aufgerufen, und `reviewer` ist der polymorphe Diskriminator, den `src/agent_runtime.py` an fünf Stellen auf jedem Adapter liest — `:429` für die Streambehandlung, `:784` für die Wahl zwischen `read-only-reviewer` und `workspace-write-implementer` sowie `:996`, `:1005` und `:1072` für Execution-Root und Workspacebindung. Entfernt man `reviewer` vom Codex-Adapter, scheitert die Runtime an fünf Stellen mit `AttributeError`; entfernt man `bind_reviewer_workspace`, verletzt der Adapter das Protocol. Zusätzlich widerspricht die Ausschließlichkeitsklausel derselben Zeile 4 des Arbeitsschritts, die genau diese Fläche für Claude als zu erhaltende geerbte Fläche aufzählt, sowie meinem C-04-Abnahmetext, der die unveränderte Funktion der Reviewer-Workspacebindung verlangt. Berechtigt bleibt die Kontrolle allein für `build_capability_smoke_command`, das tatsächlich nur auf `ClaudeAdapter` existiert und Gegenstand meines Runde-2-Pre-Mortems war. | Der Plan trennt die drei Flächen ausdrücklich. Arbeitsschritt 4 hält fest, dass `reviewer` als polymorpher Diskriminator und `bind_reviewer_workspace` als Protocol-Member auf `_BaseAdapter` verbleiben und für `NativeCodexAdapter` die neutralen Vorgaben `reviewer = False` beziehungsweise das No-op behalten. Das Akzeptanzkriterium wird auf die Claude-spezifische Fläche verengt: Eine providerfreie API-Regression verlangt `NativeCodexAdapter.reviewer is False`, `hasattr(NativeCodexAdapter, "build_capability_smoke_command") is False` und dass `NativeCodexAdapter().bind_reviewer_workspace(...)` ohne Wirkung bleibt, während `NativeClaudeReviewAdapter.reviewer is True` gilt, der Capability-Smoke vorhanden ist und die Workspacebindung an `src/review_harness.py` unverändert wirkt; zusätzlich prüft sie, dass beide nativen Adapter keine Textadapterklasse mehr in ihrer MRO führen.

PRE_MORTEM: In drei Monaten ist die wahrscheinlichste Fehlerursache nicht mehr die Baseline, sondern die Adaptergrenze. Der Baseline-Lebenszyklus ist inzwischen dreifach verriegelt und wird bei jedem Slice erneut geprüft; die Adapterrefaktorierung dagegen hat jetzt ein Akzeptanzkriterium, das nur erfüllbar ist, wenn man den polymorphen Reviewer-Diskriminator anfasst, den fünf Stellen in `src/agent_runtime.py` unbesehen voraussetzen. Wer Slice 2 unter Termindruck implementiert, hat zwei Auswege, und beide sind schlecht: Entweder er entfernt `reviewer` vom Codex-Adapter und repariert die entstehenden `AttributeError` mit `getattr(adapter, "reviewer", False)`, womit der Diskriminator still zu einem optionalen Feld wird und ein künftiger Adapter ohne Attribut lautlos als Implementer läuft; oder er lässt das Kriterium unerfüllt und markiert es als „architekturbedingt nicht anwendbar", womit die einzige berechtigte Kontrolle — die gegen ein an Codex vererbtes `build_capability_smoke_command` — gleich mit entfällt. Die zweite, leisere Variante betrifft den Plantext selbst: Das Slice-2-Kriterium begründet den Manipulationsschutz der Baseline mit dem Lockdigest, obwohl tatsächlich die Verriegelung aus Absenzprüfung und Differenzgleichung trägt. Wer später eine der beiden Prüfungen für redundant hält und entfernt, glaubt sich weiterhin durch den Digest geschützt und ist es nicht.

PLAN_APPROVAL: NO

STATUS: DONE

---

## Codex-Planrevision nach Claude-Runde 3

Claude hat `C-01` bis `C-05` geschlossen und mit `C-06` einen Widerspruch in
der neu ergänzten Adapter-Negativkontrolle nachgewiesen. Der normative Plan
trennt deshalb jetzt drei zuvor vermischte Flächen:

1. `reviewer` bleibt als verpflichtender polymorpher Diskriminator auf
   `_BaseAdapter`. Codex erbt `False`, Claude setzt `True`.
2. `bind_reviewer_workspace` bleibt als verpflichtendes Protocol-Member auf
   `_BaseAdapter`. Codex behält das No-op; Claude bindet weiterhin wirksam an
   `src/review_harness.py`.
3. Nur `build_capability_smoke_command` ist Claude-spezifisch und darf nicht
   auf `NativeCodexAdapter` erscheinen.

Die providerfreie API-Regression bildet genau diese Positiv- und
Negativgrenzen ab und prüft zusätzlich, dass beide nativen Adapter keine
Textadapterklasse mehr in ihrer MRO führen. Damit bleibt die generische
Runtimefläche vollständig und der beabsichtigte Retirement-Schutz wird auf
die tatsächlich Claude-spezifische API verengt.

Außerdem ist die Begründung des Baseline-Manipulationsschutzes präzisiert. Der
Lockdigest erkennt einseitige Änderungen, schützt aber nicht allein gegen eine
gemeinsame konsistente Neuerzeugung von Fixture und Lock. Diese wird durch
drei unabhängige, sliceübergreifend verriegelte Grenzen erkannt:

- Slice 2 verlangt das Fehlen mindestens einer im Lock gebundenen
  Altkomponente im aktuellen Aufbau.
- Slice 3 verlangt die operationenbezogene Differenzgleichung zwischen der
  eingefrorenen Baseline und dem aktuellen Aufbau.
- Scope-/Diffprüfung bindet beide Dateien bytegleich an den freigegebenen
  Slice-1-Commit.

Die ältere Kurzform in der historischen Codex-Revision nach Runde 2 wird
durch diese normative Klarstellung ersetzt; die historischen Reviewabschnitte
bleiben unverändert erhalten.

FINDING_RESPONSE: C-06 | ACCEPTED | `reviewer` und `bind_reviewer_workspace` bleiben mit neutralen Codex-Vorgaben auf `_BaseAdapter`; ausschließlich der Capability-Smoke und die wirksame Harness-Bindung bleiben Claude-spezifisch, und die providerfreie API-Regression prüft diese Grenze exakt.

PLAN_READY: YES

STATUS: DONE

---

## Claude-Planreview – Runde 4

Manuelles Planreview außerhalb von `run_task`, ohne Provideraufruf, ohne
strukturierte JSON-Ausgabe und ohne vollständige Testsuite. Produktcode und
Tests blieben unverändert; angehängt wurde ausschließlich dieser Abschnitt.
Git-Bindung geprüft: Branch `feature/native-only-transport-and-efficiency`,
HEAD `9a7da9c66ee187a9044fbf3ff3e92a6b24e216cd`, im Arbeitsbaum nur
Planungsdokumente. Codex' `FINDING_RESPONSE` habe ich nicht übernommen,
sondern jede Zusage gegen `src/agent_adapters.py` und `src/agent_runtime.py`
nachgemessen.

### 1. Nachprüfung von C-06

Die Korrektur ist im **normativen** Slice-2-Text angekommen, nicht nur in der
angehängten Revisionsnotiz. Arbeitsschritt 4 hält jetzt ausdrücklich fest,
dass der polymorphe Diskriminator `reviewer` und das Protocol-Member
`bind_reviewer_workspace` auf `_BaseAdapter` verbleiben, dass
`NativeCodexAdapter` die neutralen Vorgaben behält und dass die
Negativkontrolle „nur die versehentliche Vererbung dieser Claude-spezifischen
Capability-Smoke-API an Codex" verhindert, „ohne die gemeinsame
Protocol-Fläche zu entfernen". Genau das war der Kern des Befunds.

Ich habe jede Klausel der geforderten API-Regression gegen den heutigen Code
ausgeführt, weil eine Regression nur dann ein Kriterium sein darf, wenn sie
auch als Ausdruck auswertbar ist:

| Klausel | Messung heute |
|---|---|
| `NativeCodexAdapter.reviewer is False` | `True` |
| `hasattr(NativeCodexAdapter, "build_capability_smoke_command") is False` | `True` |
| `NativeCodexAdapter().bind_reviewer_workspace(...)` wirkungslos | `_BaseAdapter`-No-op (`_ = source_root; _ = snapshot_root`) |
| `NativeClaudeReviewAdapter.reviewer is True` | `True` |
| Claude-Capability-Smoke vorhanden | auf `ClaudeAdapter`, in Schritt 4 zur Übernahme benannt |
| Claude-Workspacebindung wirkt | heute aus `ClaudeAdapter`, in Schritt 4 zur Übernahme benannt |
| beide MRO ohne Textadapterklasse | heute `[Native*, CodexAdapter/ClaudeAdapter, _BaseAdapter, object]`, nach Umhängung erfüllbar |

Entscheidend für die Auswertbarkeit auf **Klassenebene**: `reviewer` ist ein
schlichtes Klassenattribut — `_BaseAdapter.reviewer = False`,
`ClaudeAdapter.reviewer = True`, beide vom Typ `bool` — und keine Property.
Deshalb liefern `NativeCodexAdapter.reviewer` und
`NativeClaudeReviewAdapter.reviewer` tatsächlich `False` beziehungsweise
`True` und nicht ein Deskriptorobjekt. Die Kriterien sind also wörtlich so
ausführbar, wie sie im Plan stehen.

Die gemeinsame Runtimefläche bleibt vollständig: `src/agent_runtime.py` liest
`adapter.reviewer` weiterhin an fünf Stellen (`:429`, `:784`, `:996`, `:1005`,
`:1072`) und ruft `adapter.bind_reviewer_workspace(...)` generisch auf
(`:1014`) — beide Zugriffe bleiben durch den Verbleib auf `_BaseAdapter`
bedient. `build_capability_smoke_command` besitzt dagegen **keinen**
generischen Aufrufer: Die einzige Fundstelle im gesamten Repository ist
`tests/test_agent_adapters.py:825` auf einem Claude-Adapter, und diese Datei
steht im Slice-2-Pfad. Die `hasattr(...) is False`-Forderung bricht daher
nichts.

Die direkte Registrykonstruktion ist ebenfalls durchführbar: Ich habe die
Signaturen geprüft — `NativeCodexAdapter(settings: AgentSettings | None = None)`
und `NativeClaudeReviewAdapter(settings: AgentSettings | None = None, *,
review_harness: Path = REVIEW_HARNESS)`. Beide sind allein aus den
aufgelösten `AgentSettings` konstruierbar, weil die Harness-Bindung bereits
einen Modul-Default trägt (`src/agent_adapters.py:38`). Der Wegfall der beiden
Fabrikmethoden erzwingt also keinen zusätzlichen Parameter und verletzt weder
das `AgentAdapter`-Protocol noch einen generischen Runtimezugriff.

Ergänzend habe ich verifiziert, dass die Umhängung keine Markerlogik
mitschleppt: `_BaseAdapter.validate_process_output()` wertet ausschließlich
Providerdiagnosemarker in stderr aus, und die effektive
`bind_reviewer_workspace` von `NativeClaudeReviewAdapter` stammt heute aus
`ClaudeAdapter` — genau deshalb ist die in Schritt 4 verlangte Übernahme
notwendig und korrekt benannt.

### 2. Korrigierter Baseline-Manipulationsschutz

Die falsche Zusage ist entfernt. Das Slice-2-Kriterium sagt jetzt ausdrücklich,
dass eine gemeinsame konsistente Neuerzeugung „durch den Verbund aus
Altkomponenten-Absenzprüfung, der in Slice 3 folgenden operationenbezogenen
Differenzgleichung und dem Scope-/Diffcheck" erkannt wird und dass „der
Lockdigest allein dafür kein ausreichender Schutz" ist. Das deckt sich exakt
mit meiner Runde-3-Analyse.

Ich bestätige alle vier Bestandteile:

1. Der Digestverbund erkennt einseitige Änderungen in beide Richtungen: Der
   Lock trägt den SHA-256 der gesamten Baseline, sodass eine geänderte
   Baseline den Lock bricht und ein geänderter Lock nicht mehr zur Baseline
   passt.
2. Slice 2 verlangt das Fehlen mindestens einer im Lock gebundenen
   Altkomponente, und Slice 1 stellt sicher, dass diese Liste nicht leer ist
   („nennt mindestens `workflow-prompt` beziehungsweise die
   Vollplan-Komponente").
3. Slice 3 verlangt die operationenbezogene Differenzgleichung samt Namens-
   und Digestgleichheit der unveränderten Komponenten.
4. Der Scope-/Diffcheck bindet beide Dateien bytegleich an den freigegebenen
   Slice-1-Commit; beide fehlen absichtlich in den Pfadlisten von Slice 2 und
   Slice 3.

Die gemeinsame konsistente Manipulation habe ich erneut vollständig
durchgespielt und bestätige das Ergebnis aus Runde 3: Bleibt `workflow-prompt`
in der Entferntliste, bricht die Slice-3-Summengleichung; wird die Liste
ehrlich nachgezogen, ist sie leer und die Slice-2-Absenzprüfung scheitert;
enthält sie nur die Vollplan-Komponente, scheitert dieselbe Prüfung, weil
diese Komponente in Slice 2 noch vorhanden ist. Für eine Regeneration erst in
Slice 3 gilt dasselbe Muster gegen das dortige Kriterium.

Zur Atomizität: Die Erwähnung der Slice-3-Gleichung im Slice-2-Kriterium ist
eine Beschreibung des Gesamtmechanismus und ausdrücklich als „in Slice 3
folgend" gekennzeichnet — sie ist kein in Slice 2 auszuführender Test. Die
tatsächlich in Slice 2 auswertbaren Teile (Bytegleichheit gegen den
Slice-1-Commit, gültiger Digestverbund, kein Schreiben, Absenz mindestens
einer reviewten Altkomponente) sind sämtlich im Slice-2-Zustand ausführbar.
Jeder Slice bleibt damit für sich grün.

### 3. Erhaltung von C-01 bis C-05

Die C-06-Korrektur ist rein textuell in Slice 2 Arbeitsschritt 4 und zwei
Akzeptanzkriterien erfolgt; keine Pfadliste wurde verändert. Ich habe die drei
Listen erneut ausgezählt und mit dem kanonischen Parser gegengeprüft:
`extract_implementation_slices()` aus `src/plan_handoff.py` liest genau drei
Slices mit 40, 33 und 26 Pfaden. Die Differenz zu den 39, 32 und 25 im
Dokument sichtbaren Aufzählungspunkten erklärt sich vollständig durch den je
Slice automatisch abgeleiteten Handoffpfad
`docs/internal/slice-<plan-stem>-0N-<slug>.md`, den der Parser selbst ergänzt —
also keine Auslassung im Plan. Alle in den Runden 1 bis 3 nachgeforderten
Pfade sind weiterhin enthalten.

### 4. Erneutes Gesamtplanreview

Die zehn Prüffragen ergeben keinen weiteren Befund. Reihenfolge und
Atomizität bleiben korrekt: Die Textzweige hängen an
`protocol_binding.codex_result_transport` beziehungsweise
`claude_review_transport` (`src/workflow.py:1616`, `:2035`, `:2158`), Slice 1
macht sie ohne Slice-2-Voraussetzung unerreichbar, und die dann toten
Treiberpfade `persist_codex_contract` (`src/orchestrator.py:1770`) und das
Textreview-Pendant (`:1893`) liegen im Slice-1-Pfad. Die Ablehnung alter
States vor externen Seiteneffekten bleibt an `src/state_io.py` und
`src/artifact_migration.py` verankert. Die erhaltenswerten Parser —
Taskvertrag, Plan-Handoff, JSON-/Schemasicherheit und die Providerdiagnose in
`_BaseAdapter.validate_process_output` — stehen ausdrücklich außerhalb der
Verbotsmenge. Request-ID, Writerschemadigest und Evidenzdigests bleiben laut
Slice 3 Arbeitsschritt 4 deterministisch gekoppelt; die Trennung zwischen
lokalem Zeichen-/Byteproxy und tatsächlicher Providerusage ist im Recordmodell
bereits angelegt (`provider_input_measurement` mit `components`,
`total_chars`, `total_bytes`; `provider_usage` mit durchgängig nullbaren
Tokenfeldern). Rohantwortpersistenz, Recordautorität, Full-Diagnosemodus,
kompakter Live-Output und Markdownprojektion bleiben als unveränderte
Semantik gefordert. Alle Akzeptanzkriterien sind providerfrei ausführbar, ohne
gelöschte Altlogik und ohne willkürliche Kennzahlen.

Der Plan ist damit aus meiner Sicht freigabereif.

### Befunde

REVIEWER: claude
FINDING_STATUS: C-01 | CLOSED | Unverändert geschlossen; die C-06-Korrektur hat keine Pfadliste angefasst. `tests/test_final_review_preflight.py` und `tests/test_artifact_projection.py` stehen weiterhin im Slice-1-Pfad, ergänzt um `tests/test_native_transport_retirement.py` in Slice 1 und Slice 2. Tragend bleibt die Modellverschärfung aus Arbeitsschritt 5: Produktiv existieren nur die Fabriken `agent_result_payload()` (`src/artifact_bridge.py:113-131`) und `review_payload()` (`:134-152`) mit fünf Aufrufstellen in `src/orchestrator.py`, davon `:1783` und `:1893` als Textpfade; diese Datei liegt im Slice-1-Pfad. Jede Konstruktion ohne Transportversion, Request-ID und Rohantwortdigest scheitert danach im `__post_init__`, unabhängig von Positionsargumenten, Hilfsfactorys oder mehrzeiliger Schreibweise.
FINDING_STATUS: C-02 | CLOSED | Unverändert geschlossen. `schemas/native-agent-review-request-v2.schema.json` steht weiterhin im Slice-2-Pfad, und Arbeitsschritt 6 entfernt den Wurzel-`oneOf`-Zweig gemeinsam mit `$defs/review_contract_repair_request` und `$defs/repair_error`. Die Exklusivität habe ich in Runde 2 verifiziert: `repair_error` wird ausschließlich aus dem Repair-Zweig referenziert, und weder `schemas/` außerhalb des Requestschemas noch `src/native_provider_schema.py` enthält einen Repair-Treffer. Builder, Provideroperation, Runtimeprompt und Tests liegen sämtlich im Slice-2-Pfad; die Negativprobe ist providerfrei ausführbar, weil Requests gegen `REQUEST_SCHEMA_PATH` validiert werden.
FINDING_STATUS: C-03 | CLOSED | Unverändert geschlossen und durch die Lock- und Read-only-Bindung weiterhin gehärtet. Die Baseline entsteht in Slice 1 aus der noch aktiven Altlogik, wird dort bytegleich rekonstruiert und ist an Basiscommit, Generatorversion, synthetische Eingabedigests, Rollennamen, Inhaltsdigests, Zeichen- und UTF-8-Bytezahlen gebunden. Slice 3 Arbeitsschritt 6 liest sie ausschließlich als Referenz, verbietet Altbuilder-Import und Regeneration und verlangt die operationenbezogene Summengleichung samt Namens- und Digestgleichheit der unveränderten Komponenten — also ohne gelöschten Code und ohne hartkodierte Gesamtzahl.
FINDING_STATUS: C-04 | CLOSED | Weiterhin geschlossen und durch die C-06-Korrektur nun widerspruchsfrei. Die Aufzählung der zu erhaltenden Fläche in Arbeitsschritt 4 deckt sich unverändert exakt mit der gemessenen Vererbung, und die zuvor kollidierende Ausschließlichkeitsklausel ist jetzt korrekt aufgeteilt: gemeinsame Protocol-Fläche auf `_BaseAdapter`, Claude-spezifische Capability-Smoke-API auf `NativeClaudeReviewAdapter`. Damit ist auch meine ursprüngliche C-04-Klausel, dass die Reviewer-Workspacebindung unverändert funktioniert, wieder erfüllbar: Für Codex bleibt sie das `_BaseAdapter`-No-op, für Claude die wirksame Bindung an `src/review_harness.py`, die Arbeitsschritt 4 ausdrücklich zur Übernahme benennt.
FINDING_STATUS: C-05 | CLOSED | Unverändert geschlossen und in der Begründung nun korrekt. `tests/test_provider_input_efficiency.py` steht in allen drei Pfadlisten; Fixture und Lock stehen ausschließlich im Slice-1-Pfad und werden in Slice 2 Arbeitsschritt 7 und Slice 3 Arbeitsschritt 6 als Read-only geführt. Slice 2 baut den Testhost von Rekonstruktion auf Integritätsprüfung um, verbietet Regeneration und verlangt die Abwesenheit mindestens einer im Lock gebundenen Altkomponente; Slice 1 garantiert, dass diese Liste nicht leer ist. Der Slice-1-Reviewbericht trägt Baseline- und Lockdigest als externen Anker außerhalb des veränderbaren Dateipaars. Die drei Varianten einer gemeinsamen konsistenten Neuerzeugung scheitern sämtlich an der Verriegelung aus Slice-2-Absenzprüfung und Slice-3-Differenzgleichung; der Scope-/Diffcheck gegen den Slice-1-Commit ist die dritte, unabhängige Grenze.
FINDING_STATUS: C-06 | CLOSED | Die Korrektur steht im normativen Slice-2-Text, nicht nur in der Revisionsnotiz. `reviewer` und `bind_reviewer_workspace` verbleiben ausdrücklich auf `_BaseAdapter`, `NativeCodexAdapter` behält `reviewer = False` und die wirkungslose Workspacebindung, `NativeClaudeReviewAdapter` erhält `reviewer = True` und die wirksame Bindung an `src/review_harness.py`, und die Negativkontrolle ist auf `build_capability_smoke_command` verengt. Ich habe jede Klausel der geforderten API-Regression gegen den heutigen Code ausgewertet: `NativeCodexAdapter.reviewer is False` → `True`, `hasattr(NativeCodexAdapter, "build_capability_smoke_command") is False` → `True`, `_BaseAdapter.bind_reviewer_workspace` ist ein echtes No-op, `NativeClaudeReviewAdapter.reviewer is True` → `True`. Die Auswertung auf Klassenebene funktioniert, weil `reviewer` ein schlichtes `bool`-Klassenattribut ist und keine Property. Die generische Runtimefläche bleibt bedient: `src/agent_runtime.py` liest `adapter.reviewer` an `:429`, `:784`, `:996`, `:1005`, `:1072` und ruft `bind_reviewer_workspace` an `:1014` generisch auf. `build_capability_smoke_command` besitzt keinen generischen Aufrufer — die einzige Fundstelle ist `tests/test_agent_adapters.py:825` auf einem Claude-Adapter, und diese Datei liegt im Slice-2-Pfad. Die direkte Registrykonstruktion ist möglich, weil `NativeCodexAdapter(settings=None)` und `NativeClaudeReviewAdapter(settings=None, *, review_harness=REVIEW_HARNESS)` allein aus den aufgelösten `AgentSettings` konstruierbar sind.

REVIEW_EVIDENCE: Adapterfläche gegen `src/agent_adapters.py` und `src/agent_runtime.py` gemessen — Klassenattributform von `reviewer`, No-op-Körper von `_BaseAdapter.bind_reviewer_workspace`, fehlende generische Aufrufer für `build_capability_smoke_command`, heutige MRO beider nativer Adapter, Konstruktorsignaturen für die direkte Registrykonstruktion und Herkunft der effektiven Claude-Workspacebindung; alle sieben Klauseln der geforderten API-Regression einzeln ausgewertet; Baseline-Manipulationsschutz in allen drei Varianten einer gemeinsamen konsistenten Neuerzeugung durchgespielt und die Verriegelung aus Slice-2-Absenzprüfung, Slice-3-Differenzgleichung und Scope-/Diffcheck bestätigt; Atomizität jedes Slice gegen die jeweils in seinem Zustand auswertbaren Kriterien geprüft; Pfadlisten mit dem kanonischen `extract_implementation_slices()` gegengeprüft (drei Slices, 40/33/26 Pfade, Differenz zu den sichtbaren Aufzählungspunkten vollständig durch den je Slice abgeleiteten Handoffpfad erklärt); Transportgatterung, Fail-closed-Ladepfad, erhaltene Task-/Plan-/JSON-/Quota-/Diagnoseparser und die Recordbasis für Zeichen-, Byte- und Usageprojektion erneut verifiziert | `tests/test_language_consistency.py` steht in Slice 1 und Slice 2, nicht aber in Slice 3, obwohl Slice 3 `README.md` ändert; die dortigen Prüfungen binden allerdings den CLI-Optionssatz und feste Überschriften, die Slice 3 nicht anfasst, sodass eine reine Ergänzung um die Effizienzprojektion keine Zusicherung bricht | Die historische Codex-Revision nach Runde 2 enthält weiterhin die verkürzte Aussage, Scope-/Diffprüfung und Lockdigest schützten die Baseline gemeinsam; sie ist durch die spätere normative Klarstellung ersetzt, bleibt aber als Text im Dokument stehen und könnte von einem späteren Leser als normativ missverstanden werden

PRE_MORTEM: In drei Monaten scheitert dieses Paket am wahrscheinlichsten nicht an einem der sechs geschlossenen Befunde, sondern an der Halbwertszeit ihrer Begründungen. Der Plan trägt inzwischen vier Reviewrunden und drei Revisionen; die normativen Sliceabschnitte sind korrekt, aber die historischen Abschnitte enthalten zwei überholte Kurzformeln — die Behauptung, der Lockdigest allein schütze die Baseline, und die zurückgenommene Ausschließlichkeitsklausel für `reviewer` und `bind_reviewer_workspace`. Wer bei der Umsetzung von Slice 2 nach der Adaptergrenze oder dem Baseline-Schutz sucht, findet mit hoher Wahrscheinlichkeit zuerst eine dieser älteren Stellen, weil sie kürzer und pointierter formuliert sind als der normative Text. Die konkrete Fehlerform ist dann nicht ein roter Test, sondern eine stille Vereinfachung: die Absenzprüfung wird als redundant zum Digest gestrichen, oder `reviewer` wird vom Codex-Adapter entfernt und die entstehenden `AttributeError` werden mit `getattr(adapter, "reviewer", False)` beruhigt — womit der polymorphe Diskriminator zu einem optionalen Feld wird und ein künftiger Adapter ohne Attribut lautlos als Implementer liefe. Die zweite, leisere Variante betrifft Slice 3: Die Differenzgleichung ist der einzige verbleibende Beweis der Einsparung, und sie ist die letzte Prüfung, die vor einer Freigabe steht; wer sie unter Termindruck auf eine einzelne Operation verengt, verliert die Verriegelung gegen eine regenerierte Baseline, ohne dass ein Test rot wird.

PLAN_APPROVAL: YES

STATUS: DONE
