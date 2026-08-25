# Arbeitsplan – Endgültige Entfernung von Antigravity aus dem Orchestrator

TARGET_BRANCH: feature/remove-antigravity-permanently

BASE_COMMIT: b3d49ff5ff2d94a8355000c2f79a7e5442fda16c

STATUS: PLAN_REVISED_FOR_CLAUDE_REVIEW

## 1. Auftrag

Antigravity wird dauerhaft aus dem ausführbaren Orchestratorprozess entfernt.
Die künftige feste Topologie lautet ausschließlich:

1. Codex plant und implementiert;
2. Claude prüft jeden Plan, jeden Implementierungs- beziehungsweise
   Korrekturstand und den vollständigen Branch;
3. der Orchestrator führt die deterministische Validierung aus, bindet deren
   Attestierung an Claudes Entscheidung und erstellt nach positiver
   Claude-Freigabe den lokalen Plan- beziehungsweise Slicecommit.

Die Entfernung ist keine temporäre Betriebsoption. Es entstehen kein
Featureflag, kein deaktivierter Adapter, kein optionaler dritter Reviewer und
kein später aktivierbarer Konfigurationshaken. Eine spätere Einführung eines
weiteren Reviewers wäre ein neues Architekturvorhaben.

## 2. Ausführungsmodus dieses Arbeitspakets

Dieses Arbeitspaket verändert den Orchestrator selbst und wird deshalb nicht
mit `run_task`, `run_task --watch`, Resume oder einem anderen
Self-Bootstrap-Lauf ausgeführt.

Verbindlicher manueller Ablauf:

1. Planung und Implementierung erfolgen direkt im Codex-Chat.
2. Claude arbeitet in einem separaten manuellen Terminal beziehungsweise Chat
   als unabhängiger Reviewer. Produktcode und Tests sind für Claude strikt
   read-only; schreiben darf Claude ausschließlich seinen vorgesehenen
   Reviewabschnitt in die jeweilige Plan-, Slice- oder Gesamtreview-MD.
3. Codex implementiert Slice für Slice und führt nur notwendige fokussierte
   Tests während der Bearbeitung aus.
4. Nach jedem Slice führt Codex hier einmal die vollständige Repositorymatrix
   außerhalb eines LLM-Reviewturns aus und dokumentiert Ergebnis, Laufzeit und
   `git diff --check` im Slice-Bericht.
5. Claude prüft den Diff und die dokumentierte Testattestierung, führt aber
   weder die Vollsuite noch Provider-Canaries erneut aus.
6. Vor Claudes vollständiger Gesamtfreigabe erfolgen kein Staging, kein
   lokaler Commit, kein Merge und kein Push.
7. Nach Claudes Gesamtfreigabe überträgt der Benutzer Archivierung, lokalen
   Abschlusscommit und Merge ausdrücklich an Codex.

Der manuelle Ausnahmeablauf ändert nicht den späteren Normalbetrieb. Nach
Abschluss dieses Pakets führt der Orchestrator die vollständige Matrix einmal
pro relevantem Fingerprint aus, persistiert die Attestierung und stößt nach
Claudes gebundener Freigabe den lokalen Commit selbst an. Codex und Claude
führen im normalen Reviewablauf keine redundante Vollsuite aus.

## 3. Verbindliche Umfangsgrenze

- Es gibt genau drei Implementierungsslices und keinesfalls mehr als drei.
- Eine maximale Dateianzahl pro Slice gilt ausdrücklich nicht. Die atomare,
  jederzeit testbare Umstellung hat Vorrang vor künstlicher Pfadarithmetik.
- Jeder Slice besitzt trotzdem eine vollständige exakte Pfadliste; neue
  Dateien, Löschungen, Verschiebungen und begründete Pfadüberschneidungen sind
  innerhalb dieser Liste erlaubt.
- Jeder Slice muss mit seiner dann gültigen Pfadmenge die vollständige
  Repositorymatrix bestehen.
- Entwicklungsspezifische Dokumente werden erst nach Claudes finaler
  Gesamtfreigabe im separaten Codex-Git-Handoff archiviert.

## 4. Kompatibilitätsentscheidung

Historische Läufe müssen nach diesem Cutover weder fortsetzbar noch lesbar
bleiben. Es werden daher keine Legacy-Reader, keine Migration und keine
historische Antigravity-Projektion neu erhalten oder gebaut.

Stattdessen gelten folgende Regeln:

- neue Läufe sind ausschließlich an `structured-v2` gebunden;
- bestehende `legacy-state-v3`- und `structured-v1`-States werden als nicht
  unterstützte Protokollversion abgewiesen;
- es gibt keinen Resume-, Import- oder Migrationspfad von v1 nach v2;
- es wird keine frühere Antigravity-Freigabe rekonstruiert oder erfunden;
- die bisherigen v1-Schemata und historischen Entwicklungsdokumente sind nach
  dem Cutover tote Artefakte und werden in den fachlich zuständigen Slices
  archiviert, nicht von der Runtime gelesen;
- der abschließende Git-Handoff archiviert danach nur noch die bei dieser
  Entwicklung neu entstandenen Plan-, Slice- und Reviewdokumente.

Ein klarer Fehler `UNSUPPORTED-PROTOCOL` ist ausreichend. Eine differenzierte
historische Audit- oder Replayfunktion gehört ausdrücklich nicht zum Umfang.

Die beiden aktiven Registeridentitäten
`native-provider-schema-capabilities-v1` und
`native-provider-schema-exceptions-v1` sind davon nicht betroffen. Sie
versionieren den separat probierten Provider-Subset-Vertrag, nicht einen
Workflow-, Request-, Resultat- oder Artifactzustand. Da ihr Datenmodell durch
den Cutover nicht geändert wird, bleiben sie bewusst auf v1; ihre
Regressionstestreferenzen werden jedoch im atomaren Slice 1 mitgeprüft und bei
notwendigen Funktionsumbenennungen aktualisiert.

## 5. Modell- und Denktiefenkonfiguration

### 5.1 Empfohlene Schnittstelle

Die bestehende rollenbezogene Konfiguration bleibt erhalten und wird als
einzige öffentliche Modellauswahl festgeschrieben:

```text
--codex-model <provider model id>
--codex-effort <effort>
--claude-model <provider model id>
--claude-effort <effort>
```

Entsprechende Umgebungsvariablen:

```text
RUN_TASK_CODEX_MODEL
RUN_TASK_CODEX_EFFORT
RUN_TASK_CLAUDE_MODEL
RUN_TASK_CLAUDE_EFFORT
```

Auflösungsreihenfolge:

```text
explizite CLI-Angabe > Umgebungsvariable > Rollen-Default
```

Verbindliche Defaults:

| Rolle | Modell | Effort |
|---|---|---|
| Codex | `gpt-5.6-sol` | `medium` |
| Claude | `sonnet` | `high` |

Ein generisches `--model` oder `--effort` wird nicht eingeführt, weil seine
Zuordnung bei zwei Providern mehrdeutig wäre. Modell-IDs bleiben sichere,
nichtleere Providerstrings; Effortwerte bleiben gegen die rollenbezogene
Allowlist validiert.

### 5.2 Laufbindung

Das nach CLI-/Umgebungsauflösung wirksame Paar aus Modell und Effort wird für
Codex und Claude bei der Erstellung eines neuen Laufs im `structured-v2`-
ProtocolBinding persistiert.

- Ein Resume ohne erneute Modelloption verwendet die gespeicherten Profile.
- Eine explizit abweichende Modell- oder Effortangabe hält vor Providerstart
  fail-closed mit `AGENT-PROFILE-DIFF` an.
- Providerattempts und menschliche Auditprojektion nennen das wirksame Modell
  und den wirksamen Effort.
- Ein neuer fachlicher Lauf darf andere Profile wählen.

Damit bleibt die Modellauswahl flexibel, ohne dass sich die semantische
Provideridentität innerhalb eines laufenden Workflows unbemerkt ändert.

### 5.3 Nicht gewählte Alternativen

- **Nur pro Prozess, nicht persistiert:** einfacher, aber Resume könnte mit
  anderem Modell oder Effort weiterlaufen.
- **Repositoryweite Modellwerte in `orchestrator.toml`:** reproduzierbar,
  würde aber lokale Providerwahl an den Zielbranch koppeln und
  Bootstrap-Diffs unnötig verändern.
- **Ein gemeinsames Modellflag:** bei Codex und Claude semantisch mehrdeutig.

## 6. Zielarchitektur

### 6.1 Planungsphase

```text
CODEX_PLAN
  -> lokale Planvertragsprüfung
  -> CLAUDE_PLAN_REVIEW
     -> denied: CODEX_PLAN_REVISION -> CLAUDE_PLAN_REVIEW
     -> approved: Orchestrator erstellt Plancommit und IMPLEMENT-Handoff
```

### 6.2 Implementierungsphase

```text
CODEX_IMPLEMENTATION / CODEX_CORRECTION
  -> Orchestrator führt vollständige Validierung aus
  -> fingerprintgebundene Attestierung
  -> CLAUDE_SLICE_REVIEW
     -> denied: CODEX_CORRECTION -> Validierung -> CLAUDE_SLICE_REVIEW
     -> approved: optionales Benutzergate -> Orchestrator erstellt SLICE_COMMIT
```

Claude darf die vollständige Matrix nicht erneut starten. Fokussierte
providerfreie Gegenproben sind nur zur Falsifikation eines konkreten Befunds
zulässig.

### 6.3 Abschlussphase

```text
CODEX_FINAL_REVIEW
  -> deterministisches Finalreview-Preflight
  -> CLAUDE_FINAL_REVIEW
     -> denied: CODEX_FINAL_CORRECTION -> Validierung -> CLAUDE_FINAL_REVIEW
     -> approved und keine offenen C-Findings: COMPLETED
```

Codex' Abschlussbericht bleibt ein read-only Selbstcheck und keine Freigabe.
Claude ist der einzige unabhängige Freigabereviewer.

### 6.4 Findings

- Neue Findings besitzen ausschließlich `C-*`-IDs.
- Nur Claude darf sie schließen oder reklassifizieren.
- Codex kann Findings beantworten, aber nicht schließen.
- Slice-Freigaben dürfen offene Claude-Observations tragen.
- Vor positiver Finalfreigabe muss Claude jedes offene Finding schließen oder
  als Blocker eskalieren und ablehnen.
- Der terminale Zustand verlangt null offene Findings.

## 7. Umsetzungsslices

Die drei Slices sind nach atomaren, grün validierbaren Zuständen geschnitten.
Insbesondere werden Schemaidentität, Writerschemadigests, Request-IDs und der
aktive Nachweisverbund gemeinsam umgestellt. Kein Slice darf seine
Repositorymatrix erst durch Arbeiten eines späteren Slice reparierbar machen.

### Slice 1 - Atomarer Structured-v2-, Vertrags- und Evidenzcutover

Ziel ist ein in sich geschlossener neuer Protokollstand: neue Läufe verwenden
den Codex-Claude-Workflow und ausschließlich v2-Request-/Resultat- und
Artifactverträge. Antigravity bleibt bis Slice 2 nur als unerreichbarer,
bestehender Adapterrest im Arbeitsbaum; kein v2-Workflow kann ihn starten oder
als Freigabeautorität verwenden.

**Exakter Änderungspfad**

- `schemas/orchestrator-artifact-v2.schema.json`
- `schemas/native-agent-codex-request-v2.schema.json`
- `schemas/native-agent-codex-result-v2.schema.json`
- `schemas/native-agent-review-request-v2.schema.json`
- `schemas/native-agent-review-result-v2.schema.json`
- `src/contracts.py`
- `src/workflow_state.py`
- `src/workflow.py`
- `src/orchestrator.py`
- `src/audit_trail.py`
- `src/final_review_preflight.py`
- `src/git_service.py`
- `src/artifact_bridge.py`
- `src/artifact_models.py`
- `src/artifact_migration.py`
- `src/artifact_projection.py`
- `src/artifact_replay.py`
- `src/state_io.py`
- `src/native_codex_contract.py`
- `src/native_codex_request.py`
- `src/native_review_contract.py`
- `src/native_review_request.py`
- `src/review_packets.py`
- `src/prompts.py`
- `src/plan_handoff.py`
- `src/inbox_watcher.py`
- `src/dry_run_scenarios.py`
- `src/native_provider_schema.py`
- `scripts/native_contract_probe.py`
- `schemas/native-provider-schema-capabilities-v1.json`
- `schemas/native-provider-schema-exceptions-v1.json`
- `tests/fixtures/native-contract-corpus/corpus.jsonl`
- `tests/fixtures/native-contract-corpus/manifest.json`
- `tests/test_contracts.py`
- `tests/test_workflow_state.py`
- `tests/test_workflow.py`
- `tests/test_orchestrator_runtime.py`
- `tests/test_audit_trail.py`
- `tests/test_final_review_preflight.py`
- `tests/test_git_service.py`
- `tests/test_artifact_bridge.py`
- `tests/test_artifact_models.py`
- `tests/test_artifact_migration.py`
- `tests/test_artifact_projection.py`
- `tests/test_artifact_replay.py`
- `tests/test_state_io.py`
- `tests/test_dry_run_scenarios.py`
- `tests/test_native_codex_contract.py`
- `tests/test_native_codex_request.py`
- `tests/test_native_review_contract.py`
- `tests/test_native_review_request.py`
- `tests/test_native_review_schema.py`
- `tests/test_native_contract_corpus.py`
- `tests/test_native_contract_differential.py`
- `tests/test_native_contract_probe.py`
- `tests/test_native_provider_schema.py`
- `tests/test_review_packets.py`
- `tests/test_structured_artifact_regressions.py`
- `docs/internal/archive/native-contract-v1-evidence/README.md`
- `docs/internal/archive/native-contract-v1-evidence/corpus.jsonl`
- `docs/internal/archive/native-contract-v1-evidence/manifest.json`
- `docs/internal/archive/native-contract-v1-evidence/native_contract_probe.py`

#### Arbeitsschritte

1. `structured-v2` mit Codex als Erzeuger und Claude als einzigem
   unabhängigen Reviewer für neue Läufe verpflichtend machen.
2. Alle fünf v2-Schemata anlegen. Der Codex-Resultatvertrag gehört ausdrücklich
   dazu; `finding_dispositions[].finding_id` akzeptiert ausschließlich
   `^C-(0[1-9]|[1-9][0-9]*)$`.
3. `src/native_codex_contract.py` und die Reviewvertragsmodule vollständig
   auf ihre v2-Schemata umstellen. Request-ID, Writerschemadigest,
   Response-Schema und gebundener Kontext wechseln atomar.
4. V1- und Legacy-States beim Laden beziehungsweise Resume eindeutig als
   `UNSUPPORTED-PROTOCOL` abweisen. Es entsteht kein Migrations- oder
   historischer Readerpfad.
5. Plan-, Slice- und Finaltransitionen direkt nach Claudes Entscheidung an
   Revision, Korrektur, lokalen Orchestratorcommit oder Abschluss anschließen.
6. `WorkflowCommitRequest`, Historymirror, Auditautorisierung,
   Record-ahead-Recovery und Finalreview-Preflight auf genau eine
   Claude-Reviewreferenz reduzieren.
7. Den v1-Nachweisverbund bewusst außer Betrieb nehmen: Die sieben
   `live_canary`-Zeilen sind keine Aussage über den v2-Transportvertrag und
   dürfen nach dem Cutover weder Writerschemadigests noch Request-IDs für v2
   attestieren.
8. `corpus.jsonl`, `manifest.json` und das damalige Probeprogramm
   bytegleich unter
   `docs/internal/archive/native-contract-v1-evidence/` als historische
   Evidenz sichern; der neue README kennzeichnet sie ausdrücklich als
   nichtautoritative, nicht ausführbare v1-Auditquelle.
9. `tests/test_native_contract_corpus.py` und
   `tests/test_native_contract_probe.py` als aktive v1-Testbindung entfernen.
   `tests/test_native_contract_differential.py` auf einen rein
   providerfreien, dynamisch aus dem gebundenen v2-Kontext erzeugten
   Writerschema-/Domänenvergleich umstellen.
10. Durch einen Negativtest belegen, dass eine Codex-Disposition auf `A-01`
    bereits im v2-Resultatschema und vor der Domänenkonvertierung scheitert.
11. Die vollständige Repositorymatrix innerhalb dieses Slice grün herstellen;
    kein roter v1-Canary- oder Corpus-Test darf auf Slice 2 verschoben werden.
12. Den scripted Dry Run bereits gemeinsam mit der Workflowtopologie auf
    Codex-Claude und `structured-v2` umstellen. Nach Slice 1 darf er weder
    einen Antigravity-Step noch `ProtocolMode.STRUCTURED_V1` instanziieren;
    die erneute Listung in Slice 2 dient ausschließlich der dortigen
    Providerrestbereinigung.
13. Das Provider-Subset-Register bewusst unter den eigenständigen Identitäten
    `native-provider-schema-capabilities-v1` und
    `native-provider-schema-exceptions-v1` aktiv lassen. Alle 13
    `regression_test`-Verweise müssen nach den v2-bedingten Teständerungen auf
    tatsächlich existierende Testfunktionen zeigen.

#### Akzeptanzkriterien

- Neue Läufe sind `structured-v2`; v1 und Legacy werden fail-closed
  abgewiesen.
- Alle v2-Schemata und Domänenkonverter akzeptieren ausschließlich Codex,
  Claude und `C-*`-Findings.
- Der Codex-Resultatvertrag existiert als v2, wird tatsächlich geladen und
  weist `A-01` vor der Domänenkonvertierung ab.
- Claude-Plan-, Slice- und Finalfreigaben besitzen alleinige
  Freigabeautorität; Codex kann sich nicht selbst freigeben.
- Plan- und Slicecommits werden ausschließlich vom Orchestrator und genau
  einmal nach passender Claude-Freigabe ausgelöst.
- V1-Corpus, Manifest und Probe sind bytegleich archiviert, aber aus der
  aktiven Testsuite und jeder v2-Autoritätsbehauptung entfernt.
- Der providerfreie v2-Differentialtest erkennt sowohl unerwartete lokale
  Ablehnungen als auch stille Überpermissivität.
- Der vollständige scripted Dry Run durchläuft schon nach Slice 1 Planung,
  Korrektur und Abschluss ohne Antigravity-Stufe und ohne
  `ProtocolMode.STRUCTURED_V1`.
- Beide Provider-Subset-Register bleiben ladbar, und alle 13 registrierten
  Regressionstestverweise lassen sich auf vorhandene Funktionen auflösen.
- Die vollständige Repositorymatrix besteht bereits nach Slice 1.

### Slice 2 - Endgültige Providerentfernung und resumefeste Agentprofile

Ziel ist die vollständige Löschung der noch unerreichbaren
Antigravity-Implementierung und die explizite, unveränderlich gebundene
Modell-/Effortauswahl für Codex und Claude. Nach diesem Slice existiert
Antigravity nicht mehr in ausführbarem Produktcode, Konfiguration oder Tests.

**Exakter Änderungspfad**

- `src/agent_adapters.py`
- `src/agent_config.py`
- `src/agent_runtime.py`
- `src/cli.py`
- `src/provider_input_budget.py`
- `src/contracts.py`
- `src/workflow_state.py`
- `src/workflow.py`
- `src/orchestrator.py`
- `src/audit_trail.py`
- `src/artifact_bridge.py`
- `src/artifact_models.py`
- `src/artifact_projection.py`
- `src/dry_run_scenarios.py`
- `src/native_codex_request.py`
- `src/native_review_contract.py`
- `src/native_review_request.py`
- `src/prompts.py`
- `orchestrator.toml`
- `tests/test_agent_adapters.py`
- `tests/test_agent_runtime.py`
- `tests/test_cli.py`
- `tests/test_provider_input_budget.py`
- `tests/test_quota_wait.py`
- `tests/test_artifact_bridge.py`
- `tests/test_artifact_models.py`
- `tests/test_artifact_projection.py`
- `tests/test_dry_run_scenarios.py`
- `tests/test_native_codex_request.py`
- `tests/test_native_review_contract.py`
- `tests/test_native_review_request.py`
- `tests/test_orchestrator_runtime.py`
- `tests/test_orchestrator_watch_cli.py`
- `tests/test_inbox_watcher.py`
- `tests/test_parsing.py`
- `tests/test_prompts.py`
- `tests/test_review_runtime_hardening.py`
- `tests/test_state_io.py`
- `tests/test_structured_artifact_regressions.py`
- `tests/test_workflow.py`
- `tests/test_workflow_state.py`

#### Arbeitsschritte

1. `AntigravityAdapter`, Registryeintrag, Binärerkennung, Capabilityprobe,
   Reviewharness und alle `agy`-/`agy.exe`-Aufrufpfade löschen.
2. Antigravity-Rolle, Steps, Findingherkunft, Modell-, Effort-, Timeout-,
   Quota-, Budget- und Retrysonderregeln aus Produktcode und Konfiguration
   entfernen.
3. `antigravity_tool_schema`, den `LineNumber`-Sonderfall sowie alle
   Antigravity-spezifischen Providerattemptzweige löschen.
4. Rollenbezogene Modell-/Effortflags samt
   CLI > Environment > Rollen-Default regressionsfest machen. Defaults sind
   `gpt-5.6-sol`/`medium` und `sonnet`/`high`.
5. Die modulweite produktive Konstruktionsstelle `_fresh_state()` in
   `src/orchestrator.py` und ihr Aufrufer im Neulaufpfad übergeben die beiden
   tatsächlich nach CLI > Environment > Default aufgelösten Agentprofile
   explizit als Schlüsselwortparameter an `ProtocolBinding`. Dies ist
   ausdrücklich Teil dieses Slice und nicht nur eine Serialisierungsänderung
   in `src/workflow_state.py`; `_fresh_state()` darf keinen nicht vorhandenen
   Treiberzustand voraussetzen.
6. Resume ohne neue Optionen verwendet die persistierten Profile. Eine
   explizite Abweichung hält vor Providerstart mit
   `AGENT-PROFILE-DIFF` an.
7. Providerattempts und menschliche Auditprojektion weisen das wirksame Modell
   und den wirksamen Effort aus.
8. Den vollständigen providerfreien Dry Run auf Planung, Planrevision,
   Implementierung, Korrektur, mehrere Findings, lokalen Commit,
   Finalkorrektur und Crash-Recovery ausschließlich mit Codex und Claude
   umstellen.
9. Native v2-End-to-End-Tests mit Fehlerstubs auf den Textmarkerparsern
   versehen, sodass kein stiller Textfallback möglich ist.
10. Allgemeine Codex-/Claude-Quota- und Retrygrenzen unverändert halten und
    separat regressionsfest machen.

#### Akzeptanzkriterien

- `build_agent_registry()` liefert exakt `codex` und `claude`.
- Kein Produkt-, Konfigurations-, Preflight-, Diagnose- oder Retrypfad startet
  oder erkennt `agy`, `agy.exe` oder eine Antigravity-Rolle.
- CLI-Hilfe und Environmentauflösung kennen keine
  Antigravity-Einstellung.
- Codex verwendet ohne Override `gpt-5.6-sol` und `medium`; Claude
  `sonnet` und `high`.
- CLI überschreibt Environment; Environment überschreibt Defaults.
- Die produktive `ProtocolBinding`-Konstruktion persistiert exakt die
  aufgelösten Codex-/Claude-Profile; ein Test liest sie aus einem über den
  echten `_fresh_state()`-Neulaufpfad erzeugten Zustand wieder aus.
- Resume ohne Optionen übernimmt die persistierten Profile; explizite
  Abweichung stoppt vor Providerstart mit `AGENT-PROFILE-DIFF`.
- Der Dry Run endet nach Claudes Finalfreigabe ohne dritte Reviewstufe und
  ohne Textparserfallback.
- Allgemeine Codex-/Claude-Quota- und Retrygrenzen bleiben unverändert.
- Die vollständige Repositorymatrix besteht bereits nach Slice 2.

### Slice 3 - Dokumentation, Retirementguard und Gesamtabschluss

Ziel ist der sichtbare und statisch überprüfbare Abschluss. Alle aktiven
Betriebsverträge beschreiben nur noch Codex und Claude; tote v1-Dateien und
historische Übergangsdokumente werden aus aktiven Pfaden entfernt.

**Exakter Änderungspfad**

- `AGENTS.md`
- `CLAUDE.md`
- `CODEX.md`
- `ANTIGRAVITY.md`
- `README.md`
- `Quickstart.md`
- `docs/internal/README.md`
- `docs/internal/ORCHESTRATOR_ROADMAP_PHASE_2_PLUS.md`
- `docs/internal/OPTION_CLAUDE_ONLY_REVIEW_POLICY.md`
- `docs/internal/native-agent-json-manueller-bootstrap.md`
- `docs/internal/native-claude-review-path-arbeitsplan.md`
- `docs/internal/native-claude-review-path-review.md`
- `docs/internal/phase-2-arbeitspaket-1-erkenntnisse-und-stabilisierung-1-1.md`
- `docs/reference/architecture-and-domain-concept.md`
- `docs/reference/market-comparison.md`
- `example-plan-task.md`
- `workflow.puml`
- `schemas/orchestrator-artifact-v1.schema.json`
- `schemas/native-agent-codex-request-v1.schema.json`
- `schemas/native-agent-codex-result-v1.schema.json`
- `schemas/native-agent-review-request-v1.schema.json`
- `schemas/native-agent-review-result-v1.schema.json`
- `tests/test_language_consistency.py`
- `tests/test_final_review_preflight.py`
- `tests/test_git_service.py`
- `tests/test_parsing.py`
- `tests/test_plan_handoff.py`
- `tests/test_prompts.py`
- `tests/test_review_runtime_hardening.py`
- `docs/internal/archive/antigravity-retirement/README.md`
- `docs/internal/archive/antigravity-retirement/OPTION_CLAUDE_ONLY_REVIEW_POLICY.md`
- `docs/internal/archive/antigravity-retirement/native-agent-json-manueller-bootstrap.md`
- `docs/internal/archive/antigravity-retirement/native-claude-review-path-arbeitsplan.md`
- `docs/internal/archive/antigravity-retirement/native-claude-review-path-review.md`
- `docs/internal/archive/antigravity-retirement/phase-2-arbeitspaket-1-erkenntnisse-und-stabilisierung-1-1.md`
- `docs/internal/archive/antigravity-retirement/orchestrator-artifact-v1.schema.json`
- `docs/internal/archive/antigravity-retirement/native-agent-codex-request-v1.schema.json`
- `docs/internal/archive/antigravity-retirement/native-agent-codex-result-v1.schema.json`
- `docs/internal/archive/antigravity-retirement/native-agent-review-request-v1.schema.json`
- `docs/internal/archive/antigravity-retirement/native-agent-review-result-v1.schema.json`

#### Arbeitsschritte

1. `ANTIGRAVITY.md` löschen und die Synchronisationsregel aus `AGENTS.md`
   entfernen; AGENTS, CLAUDE und CODEX auf `structured-v2` abstimmen.
2. README, Quickstart, UML, Beispielauftrag, interne Übersicht, Roadmap und
   Referenzarchitektur vollständig auf Codex-Claude aktualisieren.
3. Normalbetrieb eindeutig dokumentieren: Orchestratorvolltest und
   fingerprintgebundene Attestierung vor Claude, automatischer lokaler Commit
   nach Claude, keine redundante LLM-Vollsuite.
4. Die fünf aktiven v1-Schemata und die fünf benannten
   Übergangsdokumente unter
   `docs/internal/archive/antigravity-retirement/` verschieben. Die Runtime
   und Tests dürfen diesen Archivbereich nicht lesen.
5. Marktvergleich korrigieren: Antigravity darf als externes Produkt genannt
   werden, aber nicht als Bestandteil der Projektarchitektur.
6. Einen statischen Retirementguard ergänzen. Außerhalb der ausdrücklich
   nichtautoritativen Archivbereiche darf kein produktiver Verweis auf
   `antigravity`, `agy`, `agy.exe`, `ANTIGRAVITY_*`,
   `A-*`-Findingregeln, `orchestrator-artifact-v1`,
   `native-agent-codex-*-v1`, `native-agent-review-*-v1` oder entfernte Steps
   existieren. Die exakt zwei weiterhin aktiven, unabhängig versionierten
   Registeridentitäten `native-provider-schema-capabilities-v1` und
   `native-provider-schema-exceptions-v1` sind die einzige benannte
   Nichtarchiv-Ausnahme; ein allgemeiner `*-v1`-Skip ist verboten.
7. Mit einer Negativkontrolle belegen, dass der Retirementguard bei einem
   absichtlich eingefügten aktiven Verweis scheitert.
8. Belegen, dass kein Skipflag, optionaler Reviewer, deaktivierter Adapterstub,
   Legacyreader oder aktiver v1-Schemaresolver zurückbleibt.

#### Akzeptanzkriterien

- `ANTIGRAVITY.md` und alle fünf aktiven v1-Schemata existieren nicht mehr
  an ihren bisherigen Laufzeitpfaden.
- Aktive Anleitung, Konfiguration, UML und Beispielaufträge nennen nur Codex
  und Claude als Prozessrollen.
- Der Marktvergleich nennt Antigravity ausschließlich als externes Produkt.
- Kein aktiver Code oder Test liest die beiden nichtautoritativen
  Archivbereiche.
- Der Retirementguard scheitert bei jedem neuen produktiven Antigravity-,
  `A-*`- oder Workflow-/Agent-v1-Verweis und ignoriert ausschließlich die
  expliziten Archivpfade sowie die beiden exakt benannten aktiven
  Provider-Subset-Registeridentitäten.
- Normaler PLAN_ONLY-, Slice- und Finalablauf validiert vollständig im
  Orchestrator und committet nach Claude-Freigabe automatisch lokal.
- Es existiert kein Skipflag, optionaler Reviewerparameter, Adapterstub oder
  Legacyreader.
- Vollständige Repositorymatrix und `git diff --check` sind sauber.

## 8. Validierung und Reviewgrenzen

Für jeden Slice gilt im manuellen Entwicklungsprozess:

1. Codex führt während der Implementierung nur fokussierte Tests aus.
2. Nach Abschluss führt Codex einmal
   `python3 -m pytest tests/ -v -p no:cacheprovider` aus.
3. Der Slice-Bericht dokumentiert exakten Befehl, Exitcode, Ergebnis,
   Laufzeit, Outputdigest und `git diff --check`.
4. Claude erhält Plan, Slice-Diff und diese Evidenz und führt die Vollsuite
   nicht erneut aus.
5. Claude meldet Blocker oder Observations; Codex korrigiert innerhalb
   desselben Slice.
6. Erst nach Claudes Slice-Freigabe beginnt der nächste Slice.

Im späteren Normalbetrieb führt ausschließlich der Orchestrator die
vollständige Matrix aus. Er persistiert eine fingerprintgebundene Attestierung
und übergibt sie den LLMs als Reviewevidenz. Ein positiver Claude-Review mit
passendem Fingerprint erlaubt dem Orchestrator den lokalen Commit. Weder Codex
noch Claude darf im normalen Reviewturn die vollständige Matrix wiederholen.

## 9. Pflichtszenarien

Mindestens folgende Szenarien sind providerfrei zu testen:

1. Neuer Lauf bindet `structured-v2` und beide Agentprofile.
2. V1- und Legacy-State werden als `UNSUPPORTED-PROTOCOL` abgewiesen.
3. Claude genehmigt Plan; Orchestrator erzeugt genau einen Plancommit und
   Handoff.
4. Claude lehnt Plan ab; genau eine Codex-Planrevision entsteht.
5. Orchestrator führt die vollständige Slicevalidierung vor Claude aus.
6. Claude erhält die Attestierung, startet aber keine Vollmatrix.
7. Claude genehmigt Slice; Orchestrator erzeugt genau einen Slicecommit.
8. Claude lehnt Slice ab; Codex korrigiert und Claude prüft erneut.
9. Offene Observation verhindert keinen Slicecommit.
10. Offenes Finding verhindert positive Finalfreigabe.
11. Codex-Finalbericht ohne Claude-Freigabe kann nicht abschließen.
12. Claude-Finalfreigabe mit null offenen Findings schließt genau einmal ab.
13. V2-Record mit Antigravity-Rolle wird abgewiesen.
14. V2-Finding mit `A-01` wird abgewiesen.
15. Registry und CLI kennen ausschließlich Codex und Claude.
16. Kein Pfad startet oder erkennt `agy` beziehungsweise `agy.exe`.
17. Codex-Default ist `gpt-5.6-sol`/`medium`.
18. Claude-Default ist `sonnet`/`high`.
19. CLI überschreibt Environment und Environment überschreibt Default.
20. Resume ohne Optionen übernimmt persistierte Agentprofile.
21. Resume mit abweichendem Profil hält als `AGENT-PROFILE-DIFF` an.
22. Codex-/Claude-Retries bleiben begrenzt und unverändert.
23. Record-ahead-Recovery verhindert doppelte Providerstarts und Commits.
24. Native v2-End-to-End-Läufe berühren keinen Textmarkerparser.
25. Markdown enthält keine Antigravity-Sektion.
26. Retirementguard erkennt einen absichtlich eingefügten aktiven
   Antigravity-Verweis.
27. Vollständiger Dry Run durchläuft Planung, drei Slices, Korrektur und
   Finalreview ausschließlich mit Codex und Claude.
28. Eine Codex-v2-Disposition auf `A-01` scheitert bereits an der
   Writerschema-Grenze.
29. Der archivierte v1-Canary-/Corpus-Verbund ist aus der aktiven Testsuite
   und jeder v2-Autoritätsbehauptung entfernt.
30. Die produktive `ProtocolBinding`-Konstruktionsstelle bindet die
   aufgelösten Agentprofile; eine reine Serialisierungsänderung genügt nicht.
31. Der vollständige scripted Dry Run durchläuft bereits unmittelbar nach
   Slice 1 ausschließlich Codex-Claude-Steps und verwendet
   `ProtocolMode.STRUCTURED_V2`.
32. Alle 13 `regression_test`-Einträge der beiden aktiven
   Provider-Subset-Register zeigen nach der v2-Umstellung auf vorhandene
   Testfunktionen.
33. Ein über den echten `_fresh_state()`-Neulaufpfad erzeugter Zustand enthält
   die beiden aufgelösten Agentprofile bytegleich im `ProtocolBinding`.

## 10. Stopbedingungen

Die Umsetzung hält an, wenn:

1. Plan-, Slice- oder Finalcommit ohne passende Claude-Freigabe möglich ist;
2. Codex sich direkt oder indirekt selbst freigeben kann;
3. ein Antigravity-Providerstart oder optionaler Reviewerpfad verbleibt;
4. v2-Schemata Antigravity oder `A-*` akzeptieren;
5. Agentprofile nicht vor Providerstart bindbar und beim Resume prüfbar sind;
6. allgemeine Codex-/Claude-Retry- oder Quotasemantik verändert wird;
7. die vollständige Matrix nach irgendeinem Slice nicht grün ist;
8. Claude einen ungelösten Blocker meldet.

## 11. Nichtziele

- Kein Ersatz für Antigravity durch einen anderen dritten Reviewer.
- Kein generischer Plugin- oder Reviewergraph.
- Keine parallelen Reviews.
- Keine historische Resume-, Replay-, Audit- oder Lesekompatibilität.
- Keine Migration alter Freigaben oder Findings.
- Keine Abschwächung von Claudes Reviewpflicht.
- Keine Verlagerung vollständiger Tests in LLM-Reviewturns.
- Kein automatischer Push oder Remote-Merge.
- Keine vollständige Stilllegung der textuellen Legacyparser; das bleibt ein
  separates Paket nach stabilen v2-Läufen.

## 12. Git- und Archivabschluss nach Gesamtfreigabe

Vor Claudes positivem Gesamtreview bleiben alle Änderungen ungestaged und
uncommitted. Nach `FINAL_APPROVAL: YES` erteilt der Benutzer Codex ausdrücklich
den Git-Handoff. Codex führt dann ausschließlich folgende Abschlussarbeiten
aus:

1. Entwicklungsspezifische Plan-, Slice- und Review-MDs in einen eindeutigen
   Archivordner verschieben; die v1-Vertrags- und Bootstrap-Unterlagen wurden
   bereits in den fachlich zuständigen Slices archiviert.
2. Exakte Zielpfade und Arbeitsbaum prüfen.
3. `git diff --check` und den dokumentierten finalen Teststand prüfen.
4. Nur den vollständig freigegebenen Stand stagen.
5. Einen lokalen Abschlusscommit erstellen.
6. Den freigegebenen Featurebranch lokal nach `master` mergen.
7. Sauberen Arbeitsbaum und Merge-Commit bestätigen.

Ein Push erfolgt nur nach einer weiteren ausdrücklichen Benutzeranweisung.
Der normale Orchestratorbetrieb bleibt davon unberührt: Dort werden Plan- und
Slicecommits weiterhin automatisch unmittelbar nach der gebundenen
Claude-Freigabe erzeugt.

## 13. Gesamt-Abnahmekriterien

Das Arbeitspaket ist erst abgeschlossen, wenn:

1. neue Läufe ausschließlich `structured-v2` verwenden;
2. kein aktiver Step, Adapter, Providerstart, Retrypfad, Konfigurationswert
   oder Budgetvertrag Antigravity enthält;
3. Claude alleinige unabhängige Freigabeautorität ist und Codex sich nicht
   selbst freigeben kann;
4. der Orchestrator die vollständige Matrix pro Fingerprint ausführt und die
   Attestierung an Claudes Review bindet;
5. der Orchestrator nach positiver Claude-Freigabe lokale Plan- und
   Slicecommits selbst auslöst;
6. Codex und Claude keine redundante Vollsuite im Reviewturn ausführen;
7. neue Findings ausschließlich `C-*`-IDs besitzen;
8. Registry, CLI, Konfiguration, Preflight und Dokumentation keine
   Antigravity-Laufzeit anbieten;
9. Modell und Effort pro Rolle wählbar und resumefest gebunden sind;
10. die Defaults exakt `gpt-5.6-sol`/`medium` und `sonnet`/`high` lauten;
11. `ANTIGRAVITY.md` und der produktive Adapter gelöscht sind;
12. der Retirementguard jede neue produktive Antigravity-Referenz verhindert;
13. alle 33 Pflichtszenarien und die vollständige Repositorymatrix bestehen;
14. genau drei Slices ohne künstliche Dateigrenze umgesetzt sind;
15. Claude jeden Slice und den vollständigen Branch freigibt;
16. Codex nach explizitem Git-Handoff archiviert, lokal committet und nach
    `master` mergt;
17. `git diff --check` und der finale Arbeitsbaum sauber sind.

## 14. Abschlusszustand

Der Orchestrator ist danach ein dauerhaftes Codex–Claude-System: Codex plant
und implementiert, Claude prüft und genehmigt, der Orchestrator validiert,
attestiert und committet. Modell und Denktiefe beider Agenten sind pro Lauf
explizit wählbar und unveränderlich gebunden. Antigravity ist weder pausiert
noch optional, sondern vollständig aus der ausführbaren Architektur entfernt.

---

## Claude Planreview – Endgültige Entfernung von Antigravity

REVIEWER: claude

**Reviewer:** Claude (Sonnet, Effort `high`) · **Datum:** 25. August 2026 ·
**Modus:** manuell, read-only, adversarial, außerhalb des Orchestrators

Basis `b3d49ff`. Es wurde nichts implementiert, kein Produktcode und kein Test
verändert, kein `run_task`, kein Resume, kein Provideraufruf. Ausgeführt wurden
ausschließlich read-only Repositoryabfragen und kleine providerfreie
Gegenproben zur Falsifikation konkreter Planbehauptungen.

### Was der Plan richtig trifft

Die Zielarchitektur ist konsistent: Codex kann sich nicht selbst freigeben
(§6.3), Claude ist einziger unabhängiger Reviewer, der Orchestrator führt die
Matrix einmal pro Fingerprint aus, bindet die Attestierung und stößt den
lokalen Commit an (§2, §8). Der bewusste Bruch mit historischen Läufen (§4) ist
in sich schlüssig und deutlich einfacher als eine Scheinkompatibilität: Ein
`UNSUPPORTED-PROTOCOL` ohne Migration hat weniger Angriffsfläche als ein
Legacy-Reader, den niemand mehr testet. Die Ablehnung eines generischen
`--model` ist bei zwei Providern sachlich richtig.

Ich habe den Antigravity-Fußabdruck unabhängig erhoben: 57 Dateien in
`src/`, `tests/`, `schemas/`, Wurzel und aktiven Dokumenten. Jeder der 21
Nutzer von `AgentRole.ANTIGRAVITY` liegt in einem Slice-Pfad — dort ist die
Abdeckung vollständig. Die vier Slices halten die Grenze exakt ein: 19, 19, 19,
19 explizite Pfade, 75 eindeutig, eine begründete Überschneidung
(`src/workflow_state.py` in Slice 1 und 2). Auch die Behauptung in §5.1, die
rollenbezogene Konfiguration bestehe bereits, habe ich geprüft und **bestätigt**
— `_add_role_arguments()` erzeugt `--<role>-model`/`--<role>-effort` dynamisch,
`RUN_TASK_<ROLE>_MODEL`/`_EFFORT` und die Reihenfolge CLI > Environment >
Default existieren in `src/agent_config.py:50-144`.

### Wo der Plan bricht

Genau darin liegt aber das Problem: Alle vier Slices sind bei exakt 19/19
Pfaden, also ohne jeden Spielraum — und mindestens acht notwendige Pfade
fehlen. Drei Lücken sind ausführbar belegt.

NEW_FINDING: C-01 | BLOCKER | Der Nachweisverbund des gerade abgeschlossenen Contract-Closure-Pakets steht in keinem Slice-Pfad, wird vom v1→v2-Cutover aber strukturell zerstört. `src/native_review_contract.py:41` definiert `SCHEMA_VERSION = "native-agent-review-result-v1"`, und dieser Wert wird in der Writerprojektion als `const` eingebettet (`:633`, `:657`). Ich habe die Folge empirisch gemessen: Setzt man `SCHEMA_VERSION` auf `native-agent-review-result-v2`, ändert sich der Writerschemadigest des Konvergenz-Canaries von `a86ebb519fc2dcdf…` auf `3cb7af8984c2cc1a…`, und weil der Schemadigest über `response_contract.schema_sha256` im Request-Preimage liegt, ändert sich zusätzlich dessen `request_id`. Damit scheitern für **alle sieben** `live_canary`-Zeilen sowohl `_validate_writer_form_evidence()` in `tests/test_native_contract_corpus.py` als auch `test_all_live_canaries_rebuild_from_frozen_base_not_current_head` in `tests/test_native_contract_probe.py`, die beide bitgenaue Gleichheit gegen `tests/fixtures/native-contract-corpus/manifest.json` verlangen. Die Manifestwerte lassen sich nicht nachziehen, weil die historischen Canaries unter v1 liefen und nach §2 kein Providerlauf vorgesehen ist. Zusätzlich kodiert `tests/test_native_contract_differential.py` die Strings `"native-agent-codex-result-v1"` (Zeile 114) und `"native-agent-review-result-v1"` (Zeile 206) fest, und `tests/test_native_contract_corpus.py` validiert 14 historische Fixtures gegen die v1-Readerbaselines, die §4 zu toten Artefakten erklärt. Diese Dateien sind Tests, keine archivierbaren Dokumente: Sie laufen in der Vollsuite **nach jedem Slice**, also lange vor dem Git-Handoff aus §12. Slice 3 kann sein Akzeptanzkriterium „vollständige Repositorymatrix grün" (§8.2) und Gesamt-Abnahmekriterium 13 damit nicht erfüllen; Stopbedingung 7 greift ohne Korrekturmöglichkeit, weil kein Slice noch einen freien Pfad hat. | Der Plan nimmt exakt diese fünf Pfade auf und beschreibt je Pfad, ob der Nachweis auf v2 umgestellt oder ersatzlos zurückgebaut wird: `tests/test_native_contract_corpus.py`, `tests/test_native_contract_differential.py`, `tests/test_native_contract_probe.py`, `tests/fixtures/native-contract-corpus/manifest.json` und `scripts/native_contract_probe.py`. Ergänzend hält der Plan fest, dass die sieben `live_canary`-Belege nach dem Cutover keine Aussage mehr über den v2-Transportvertrag treffen und deshalb entweder als ausdrücklich historische Auditzeilen ohne Testbindung geführt oder mit dem Nachweisverbund gemeinsam entfernt werden; ein Pflichtszenario belegt, dass die Vollsuite nach Slice 3 ohne diese Bindungen grün ist.

NEW_FINDING: C-02 | BLOCKER | Der Codex-Resultatvertrag akzeptiert nach dem Cutover weiterhin `A-*`-Findings, obwohl das Slice-1-Akzeptanzkriterium „Kein v2-Schema akzeptiert Antigravity oder ein `A-*`-Finding" und Gesamt-Abnahmekriterium 7 das ausschließen. `schemas/native-agent-codex-result-v1.schema.json` bindet `finding_dispositions[].finding_id` auf `"pattern": "^[CA]-(0[1-9]|[1-9][0-9]*)$"` (Zeile 30), lässt also Codex-Dispositionen zu fremden `A-*`-Findings zu. Slice 1 legt zwar `schemas/native-agent-codex-request-v2.schema.json` an — dort ist der Antigravity-Reporter in `native-agent-codex-request-v1.schema.json:148` korrekt adressiert —, aber kein `schemas/native-agent-codex-result-v2.schema.json`. Zugleich steht `src/native_codex_contract.py`, das `SCHEMA_VERSION = "native-agent-codex-result-v1"` führt und dieses Schema lädt, in keinem der vier Änderungspfade. Der Resultatvertrag bliebe damit auf v1 stehen, während Request und Reviewresultat auf v2 wechseln — inkonsistent zu §4 („neue Läufe sind ausschließlich an `structured-v2` gebunden") und zu Stopbedingung 4. Verschärfend ist `tests/test_native_codex_contract.py` in Slice 3 gelistet, obwohl die Datei nachweislich null Antigravity- oder `A-*`-Bezüge enthält; ihre Änderung wäre nur nötig, wenn das zugehörige Modul mitgeändert würde, was der Plan nicht vorsieht. | Der Plan nimmt `schemas/native-agent-codex-result-v2.schema.json` als Neuanlage und `src/native_codex_contract.py` als Änderungspfad auf, setzt das Dispositionsmuster dort auf `^C-(0[1-9]|[1-9][0-9]*)$` und begründet die Listung von `tests/test_native_codex_contract.py` über diese Modulwirkung; ein Pflichtszenario weist nach, dass eine Codex-Disposition auf `A-01` im v2-Resultatvertrag vor der Domänenkonvertierung fail-closed abgewiesen wird.

NEW_FINDING: C-03 | BLOCKER | Slice 2 kann seine eigene Kernzusage nicht erfüllen, weil die einzige produktive Konstruktionsstelle des `ProtocolBinding` nicht in seinem Änderungspfad liegt. Arbeitsschritt 7 verlangt, beide aufgelösten Agentprofile in `structured-v2` zu persistieren, und das Akzeptanzkriterium „Ein Resume ohne Optionen übernimmt beide persistierten Profile" hängt daran. `ProtocolBinding` wird produktiv ausschließlich in `src/orchestrator.py:3659` erzeugt (die zweite Stelle, `src/dry_run_scenarios.py:1082`, gehört zu Slice 3). `src/orchestrator.py` steht jedoch nur im Pfad von Slice 1, und Slice 1 nennt Agentprofile in keinem seiner acht Arbeitsschritte; die Profilauflösung selbst entsteht erst in Slice 2 über `src/agent_config.py`. Slice 1 kann die Bindung daher nicht vorwegnehmen, und Slice 2 darf die Konstruktionsstelle nicht anfassen. Die Serialisierung in `src/workflow_state.py:2224` und `:2351` ist zwar in Slice 2 verfügbar, läuft aber ins Leere, solange kein Aufrufer die Profile übergibt. | Der Plan nimmt `src/orchestrator.py` zusätzlich in den Änderungspfad von Slice 2 auf — analog zur bereits begründeten Doppelnennung von `src/workflow_state.py` — oder verlagert Arbeitsschritt 7 und das zugehörige Akzeptanzkriterium samt Pflichtszenario 20 und 21 nach Slice 1 und ergänzt dort `src/agent_config.py`; in beiden Fällen benennt der Plan die Konstruktionsstelle ausdrücklich, damit die Profilbindung nicht in einem Slice zugesagt und in einem anderen implementiert wird.

NEW_FINDING: C-04 | OBSERVATION | Die Pfadarithmetik geht nach den drei Blockern nicht mehr auf, und der Plan enthält keine Regel dafür. Bei vier Slices à höchstens 19 expliziten Pfaden stehen 76 Slots zur Verfügung; belegt sind bereits exakt 76 (75 eindeutig plus eine begründete Doppelnennung). Die aus `C-01` bis `C-03` folgenden acht zusätzlichen Pfade — fünf für den Nachweisverbund, zwei für den Codex-Resultatvertrag, einer für die Konstruktionsstelle — lassen sich ohne Umverteilung nicht unterbringen. Ich fordere ausdrücklich keinen fünften Slice; die Grenze ist einzuhalten. Der Plan trifft aber keine Aussage darüber, welche gelisteten Pfade verzichtbar sind, sodass die Umsetzung diese Entscheidung unter Zeitdruck improvisieren müsste. | Der Plan weist die acht zusätzlichen Pfade den vier Slices ausdrücklich zu und benennt im selben Zug, welche derzeit gelisteten Pfade entfallen oder in einen anderen Slice wandern, sodass jeder Slice nachvollziehbar bei höchstens 19 expliziten Pfaden bleibt; er hält zusätzlich fest, dass jeder Zwischenstand mit der dann gültigen Pfadmenge eine grüne Vollsuite erreichen muss.

### Weitere geprüfte Dimensionen ohne Befund

Endgültigkeit: Der Plan lässt kein Featureflag, keinen optionalen Reviewer und
keinen Adapterstub zu (§1, §7 Slice 4 Schritt 8, Akzeptanz „Es existiert kein
Skipflag…"). Der Retirementguard in Slice 4 Schritt 7 ist eng genug gefasst, um
tote Laufzeitpfade zu finden, und Pflichtszenario 26 falsifiziert ihn aktiv.
Der Sonderfall `choices=VALID_EFFORTS if role != "antigravity"` in
`src/agent_config.py:68` liegt korrekt im Pfad von Slice 2.

Freigabe- und Gitautorität: Record-ahead, Rewind, Korrekturrunden und
Commitidempotenz sind über Slice 1 Schritt 8 und die Szenarien 3, 7, 12 und 23
adressiert. Der manuelle Git-Handoff (§12) ist sauber als Ausnahme dieses
Selbstmodifikationspakets markiert und ersetzt die Produktsemantik nicht; §2
und §12 sagen das zweimal übereinstimmend. Ein Push bleibt ausgeschlossen.

Findings und native Verträge: Die Beschränkung auf `C-*`, Claudes
Schließungsmonopol, Codex' Antwortrecht ohne Eigentum, tragbare offene
Observations im Slice und null offene Findings im Terminalzustand sind in §6.4
widerspruchsfrei. Der Textfallback ist über Slice 3 Schritt 6 und Szenario 24
abgedeckt; §11 hält korrekt fest, dass die vollständige Stilllegung der
Legacyparser ein Folgepaket bleibt.

Testgrenze: Die Arbeitsteilung — fokussierte Tests während der Implementierung,
je Slice einmal die Vollsuite außerhalb des Reviewturns, Claude prüft nur Diff
und Evidenz — ist in §2, §6.2 und §8 dreifach konsistent und deckt sich mit dem
Reviewauftrag.

PRE_MORTEM: In drei Monaten ist die wahrscheinlichste Fehlerursache, dass der Cutover an einer Stelle scheitert, die der Plan gar nicht als Antigravity-Thema sieht. Alle vier Slices sind auf die Suche nach `antigravity`, `agy` und `A-*` optimiert, und in dieser Dimension ist die Abdeckung vollständig. Was den Cutover tatsächlich zu Fall bringt, ist die Versionsidentität: `SCHEMA_VERSION` steckt als `const` in jeder Writerprojektion, deren Digest wiederum in jeder Request-ID steckt, und daran hängt der gesamte gerade erst aufgebaute Nachweisverbund aus Corpus, Manifest, sieben eingefrorenen Canary-Belegen und Differentialmatrix. Wer Slice 3 implementiert, wird zuerst die Antigravity-Rollen entfernen, die Vollsuite starten und dann vor sieben roten Evidenzzeilen stehen, die sich nicht reparieren lassen, weil die zugehörigen Providerläufe unter v1 stattfanden und dieses Paket keine neuen Canaries vorsieht. Unter Termindruck ist die naheliegende Reaktion, die Manifestwerte auf die neuen v2-Digests zu überschreiben — womit exakt die Bindung verloren ginge, die `C-30` und `CR-07` des Vorgängerpakets mühsam hergestellt haben, und der Nachweis wieder zu Buchführung würde. Die zweite, leisere Variante: Slice 2 sagt die resumefeste Profilbindung zu, kann sie mangels Konstruktionsstelle aber nur halb umsetzen, und `AGENT-PROFILE-DIFF` greift erst, wenn ohnehin nichts mehr zu vergleichen ist.

PLAN_APPROVAL: NO

STATUS: DONE

## Planrevision 1 nach dem Claude-Review

Die vorstehenden Claude-Findings bleiben als historischer Reviewnachweis
unverändert. Die normative Planung in den Abschnitten 1 bis 14 wurde wie
folgt korrigiert:

FINDING_RESPONSE: C-01 | ACCEPTED | Slice 1 nimmt alle fünf geforderten Nachweispfade sowie das zugehörige corpus.jsonl auf. Corpus, Manifest und Probe werden bytegleich als nichtautoritative v1-Evidenz archiviert; die aktiven v1-Corpus-/Probe-Tests entfallen, und der Differentialnachweis wird dynamisch auf v2 umgestellt. Sieben historische Live-Canaries werden ausdrücklich nicht als v2-Transportbeleg umetikettiert.

FINDING_RESPONSE: C-02 | ACCEPTED | Slice 1 legt native-agent-codex-result-v2.schema.json an, stellt src/native_codex_contract.py darauf um und bindet Finding-Dispositionen an C-*; ein eigener Negativtest weist A-01 vor der Domänenkonvertierung ab.

FINDING_RESPONSE: C-03 | ACCEPTED | Slice 2 enthält src/orchestrator.py ausdrücklich und benennt ProductionWorkflowDriver._build_initial_state() als produktive ProtocolBinding-Konstruktionsstelle, die die aufgelösten Codex-/Claude-Profile persistiert und beim Resume gegen AGENT-PROFILE-DIFF absichert.

FINDING_RESPONSE: C-04 | ACCEPTED | Der Benutzer hat die Dateihöchstgrenze aufgehoben und höchstens drei Slices verlangt. Der Plan besitzt nun genau drei atomare Slices mit vollständigen Pfadlisten; jeder Zwischenstand muss mit seiner tatsächlichen Pfadmenge die vollständige Repositorymatrix bestehen.

PLAN_READY_FOR_REREVIEW: YES

---

## Claude Planreview 2 – Revidierter Plan (drei Slices)

REVIEWER: claude

**Reviewer:** Claude (Sonnet, Effort `high`) · **Datum:** 25. August 2026 ·
**Modus:** manuell, read-only, adversarial, separates Claude-Terminal

Basis `b3d49ff`. Nichts implementiert, kein Produktcode und kein Test
verändert, kein `run_task`, kein Resume, kein Provideraufruf. Der historische
Reviewtext oben ist unverändert. Ausgeführt wurden ausschließlich read-only
Repositoryabfragen zur Falsifikation konkreter Planbehauptungen.

### Gesamteindruck

Die Revision ist substanziell und trifft den Kern aller vier Vorbefunde. Der
Wegfall der Pfadobergrenze wurde nicht dazu benutzt, Pfade wahllos anzuhäufen,
sondern um den Schnitt an fachlicher Atomizität auszurichten: 56, 41 und 40
Pfade, und §7 formuliert die tragende Regel jetzt selbst — „Kein Slice darf
seine Repositorymatrix erst durch Arbeiten eines späteren Slice reparierbar
machen." Genau an dieser selbstgesetzten Regel scheitert der Plan noch an einer
Stelle.

### Disposition der Vorbefunde

`C-01` ist vollständig geschlossen. Alle fünf von mir benannten Pfade liegen in
Slice 1, und `corpus.jsonl` ist sinnvollerweise mit aufgenommen. Arbeitsschritt
8 verlangt die bytegleiche Sicherung von Corpus, Manifest und Probeprogramm
unter `docs/internal/archive/native-contract-v1-evidence/` mit einem README,
das sie ausdrücklich als nichtautoritative, nicht ausführbare v1-Auditquelle
kennzeichnet; die vier Archivziele stehen als eigene Pfade in der Liste.
Arbeitsschritt 7 stellt unmissverständlich fest, dass die sieben
`live_canary`-Zeilen keine Aussage über den v2-Transportvertrag treffen — eine
Umetikettierung ist damit ausgeschlossen. Arbeitsschritt 9 entfernt die beiden
aktiven v1-Testbindungen und stellt den Differentialnachweis auf einen
providerfrei und dynamisch aus dem gebundenen v2-Kontext erzeugten Vergleich
um; das zugehörige Akzeptanzkriterium nennt beide Richtungen — unerwartete
lokale Ablehnung **und** stille Überpermissivität —, also genau die
Beweisrichtungen, an denen der Vorgängerverbund gemessen wurde.

`C-02` ist vollständig geschlossen. `schemas/native-agent-codex-result-v2.schema.json`,
`src/native_codex_contract.py` und `tests/test_native_codex_contract.py` liegen
gemeinsam in Slice 1. Arbeitsschritt 2 schreibt
`^C-(0[1-9]|[1-9][0-9]*)$` für `finding_dispositions[].finding_id` fest,
Arbeitsschritt 3 lässt Schemaidentität, Loader, Requestbindung und
Writerschemadigest atomar wechseln, und Arbeitsschritt 10 verlangt den
Negativtest, der `A-01` bereits im Schema und vor der Domänenkonvertierung
scheitern lässt.

`C-03` ist inhaltlich geschlossen: `src/orchestrator.py` steht ausdrücklich in
Slice 2, und Arbeitsschritt 5 erklärt die Bindung der aufgelösten Profile an
`ProtocolBinding` ausdrücklich zum Bestandteil dieses Slice und nicht zu einer
bloßen Serialisierungsänderung. Persistenz, Resume ohne Optionen und
`AGENT-PROFILE-DIFF` vor Providerstart sind über die Schritte 5 bis 7 und drei
Akzeptanzkriterien abgedeckt. Der benannte Bezeichner stimmt allerdings nicht;
siehe `C-06`.

`C-04` ist durch die Benutzerentscheidung erledigt: genau drei Slices, keine
Dateiobergrenze, vollständige Pfadlisten, und §3 verlangt weiterhin je Slice
eine grüne Vollmatrix.

FINDING_STATUS: C-01 | CLOSED | Alle fünf von mir benannten Pfade liegen in Slice 1, ergänzt um `tests/fixtures/native-contract-corpus/corpus.jsonl`. Arbeitsschritt 8 sichert Corpus, Manifest und Probeprogramm bytegleich unter `docs/internal/archive/native-contract-v1-evidence/` und kennzeichnet sie per README als nichtautoritative, nicht ausführbare v1-Auditquelle; die vier Archivziele stehen als eigene Pfade in der Liste. Arbeitsschritt 7 schließt eine Umetikettierung der sieben `live_canary`-Zeilen zu v2-Transportnachweisen ausdrücklich aus. Arbeitsschritt 9 entfernt die beiden aktiven v1-Testbindungen und stellt den Differentialnachweis auf einen providerfrei und dynamisch aus dem gebundenen v2-Kontext erzeugten Vergleich um; das Akzeptanzkriterium verlangt beide Beweisrichtungen — unerwartete lokale Ablehnung und stille Überpermissivität. Arbeitsschritt 11 verbietet, einen roten v1-Test auf Slice 2 zu verschieben.

FINDING_STATUS: C-02 | CLOSED | `schemas/native-agent-codex-result-v2.schema.json`, `src/native_codex_contract.py` und `tests/test_native_codex_contract.py` liegen gemeinsam im atomaren Slice 1. Arbeitsschritt 2 begrenzt `finding_dispositions[].finding_id` auf `^C-(0[1-9]|[1-9][0-9]*)$`, Arbeitsschritt 3 lässt Schemaidentität, Loader, Requestbindung, Writerschemadigest und Response-Schema atomar auf v2 wechseln, und Arbeitsschritt 10 verlangt den Negativtest, der `A-01` bereits im v2-Resultatschema und vor jeder Domänenkonvertierung scheitern lässt. Das entspricht dem von mir geforderten Akzeptanzkriterium vollständig.

FINDING_STATUS: C-03 | CLOSED | `src/orchestrator.py` steht ausdrücklich im Änderungspfad von Slice 2, und Arbeitsschritt 5 erklärt die Übergabe der beiden aufgelösten Agentprofile an `ProtocolBinding` ausdrücklich zum Bestandteil dieses Slice statt zu einer bloßen Serialisierungsänderung in `src/workflow_state.py`. Persistenz, Resume ohne erneute Optionen und `AGENT-PROFILE-DIFF` vor Providerstart sind über die Arbeitsschritte 5 bis 7 und drei eigene Akzeptanzkriterien durchgängig abgedeckt. Die inhaltliche Lücke ist damit geschlossen; der im Plan genannte Bezeichner der Konstruktionsstelle ist jedoch falsch und wird als `C-06` separat geführt.

FINDING_STATUS: C-04 | CLOSED | Durch die geänderte Umfangsregel des Benutzers erledigt. Der revidierte Plan verwendet genau drei Slices, verzichtet ausdrücklich auf eine Dateiobergrenze und führt für jeden Slice weiterhin eine vollständige exakte Pfadliste (56, 41 und 40 Pfade). §3 und §7 verlangen unverändert, dass jeder Slice mit seiner dann gültigen Pfadmenge die vollständige Repositorymatrix besteht und kein Slice seine Matrix erst durch einen späteren reparierbar macht — die Anforderung hinter dem ursprünglichen Befund bleibt damit erhalten, ohne künstliche Pfadarithmetik.

### Verbleibender Befund

NEW_FINDING: C-05 | BLOCKER | Slice 1 kann seine eigene Vollmatrix nicht grün stellen, weil der scripted Dry Run die alte Dreier-Topologie durch die echte `WorkflowEngine` treibt und erst in Slice 2 umgestellt wird. `src/dry_run_scenarios.py` importiert und instanziiert `WorkflowEngine` (`:30`, `:1034`) und baut seinen Ausgangszustand mit `ProtocolBinding(ProtocolMode.STRUCTURED_V1, "1")` (`:1082`). Das zugehörige `tests/test_dry_run_scenarios.py` mit 25 Testfunktionen skriptet vollständige Antigravity-Reviewstufen: `WorkflowStep.ANTIGRAVITY_PLAN_REVIEW` (`:219`), `WorkflowStep.ANTIGRAVITY_SLICE_REVIEW` (`:149`, `:230`), `approval(AgentRole.ANTIGRAVITY)` (`:150`, `:231`) und sogar den Verdikttext `"REVIEWER: antigravity"` (`:220`). Slice 1 ändert mit `src/workflow.py`, `src/workflow_state.py` und `src/contracts.py` genau diese Topologie: Arbeitsschritt 5 schließt Plan-, Slice- und Finaltransitionen direkt an Claudes Entscheidung an, Arbeitsschritt 1 macht Claude zum einzigen unabhängigen Reviewer, und neue Läufe sind an `structured-v2` gebunden. Beide Dateien stehen jedoch ausschließlich im Pfad von Slice 2. Damit verletzt der Plan seine eigene Regel aus §7 („Kein Slice darf seine Repositorymatrix erst durch Arbeiten eines späteren Slice reparierbar machen"), das Slice-1-Akzeptanzkriterium „Die vollständige Repositorymatrix besteht bereits nach Slice 1" ist nicht erfüllbar, und Stopbedingung 7 greift. Anders als beim Vorgängerbefund ist die Korrektur wegen der entfallenen Pfadobergrenze kostenlos. | Der Plan nimmt `src/dry_run_scenarios.py` und `tests/test_dry_run_scenarios.py` zusätzlich in den Änderungspfad von Slice 1 auf und ergänzt dort einen Arbeitsschritt, der den scripted Dry Run gemeinsam mit der Topologie auf Codex-Claude und `structured-v2` umstellt; die Doppelnennung in Slice 2 bleibt für die dortige Providerbereinigung zulässig. Ein Pflichtszenario belegt, dass der vollständige Dry Run bereits nach Slice 1 ohne Antigravity-Stufe und ohne `ProtocolMode.STRUCTURED_V1` durchläuft.

NEW_FINDING: C-06 | OBSERVATION | Slice 2 Arbeitsschritt 5 benennt als produktive Konstruktionsstelle `ProductionWorkflowDriver._build_initial_state()`. Dieser Bezeichner existiert im Repository nicht; eine Suche über `src/` und `tests/` liefert keinen Treffer. Die tatsächliche produktive Konstruktionsstelle ist die **modulweite** Funktion `_fresh_state()` in `src/orchestrator.py:3617`, deren einziger Aufrufer `src/orchestrator.py:3864` ist und die `protocol_binding=ProtocolBinding(...)` in `:3659` erzeugt. Der Unterschied ist nicht nur kosmetisch: Als Modulfunktion mit ausschließlich Schlüsselwortparametern hat `_fresh_state()` keinen Zugriff auf Treiberzustand, sodass die beiden aufgelösten Agentprofile zusätzlich durch die Aufrufstelle gereicht werden müssen. Da ich in `C-03` ausdrücklich die namentliche Benennung der Konstruktionsstelle verlangt habe, wäre ein falscher Name die schlechteste Variante — er suggeriert Präzision, führt die Umsetzung aber ins Leere. Blockierend ist der Punkt nicht, weil `src/orchestrator.py` im Pfad steht und beide Stellen in derselben Datei liegen. | Arbeitsschritt 5 nennt `_fresh_state()` in `src/orchestrator.py:3617` als Konstruktionsstelle und deren Aufrufer `:3864` als Übergabepunkt der aufgelösten Profile; das zugehörige Akzeptanzkriterium verlangt, dass ein Test die persistierten Profile aus einem über diesen Pfad erzeugten Lauf ausliest.

NEW_FINDING: C-07 | OBSERVATION | Vier zusammengehörige Dateien des Provider-Subset-Registers liegen in keinem der drei Slices, tragen aber aktive `-v1`-Identitäten und werden produktiv geladen: `schemas/native-provider-schema-capabilities-v1.json` und `schemas/native-provider-schema-exceptions-v1.json` werden von `src/native_provider_schema.py:16-22` gelesen, das seinerseits von den in Slice 1 umgestellten Modulen `native_codex_contract.py` und `native_review_contract.py` importiert wird; `tests/test_native_provider_schema.py` prüft sie. Daraus folgen zwei Risiken. Erstens verweisen alle 13 Registereinträge über `regression_test` in genau die zwei Testdateien, die Slice 1 auf v2 umschreibt — siebenmal auf `tests/test_native_codex_contract.py`, sechsmal auf `tests/test_native_review_request.py`. Benennt die Umstellung eine dieser Regressionsfunktionen um, bricht die Registerprüfung, und die JSON-Datei ist nicht änderbar, weil sie in keiner Pfadliste steht. Zweitens verbietet der Retirementguard aus Slice 3 Arbeitsschritt 6 „v1-Schemaidentitäten" außerhalb der ausdrücklich nichtautoritativen Archivbereiche; beide Dateien führen `"schema_version": "native-provider-schema-…-v1"` und liegen weder in einem Archiv noch in einer benannten Ausnahme. Der Guard würde entweder auf sie anschlagen oder müsste sie stillschweigend ausnehmen. | Der Plan nimmt `src/native_provider_schema.py`, `tests/test_native_provider_schema.py`, `schemas/native-provider-schema-capabilities-v1.json` und `schemas/native-provider-schema-exceptions-v1.json` in die Pfadliste desjenigen Slice auf, der sie berührt, und entscheidet ausdrücklich, ob das Provider-Subset-Register unter seiner bestehenden `-v1`-Identität aktiv bleibt oder mitzieht; Slice 3 Arbeitsschritt 6 benennt die daraus folgende Guard-Regel explizit, statt sie der Umsetzung zu überlassen, und ein Akzeptanzkriterium verlangt, dass die 13 `regression_test`-Verweise nach der v2-Umstellung weiterhin auf existierende Funktionen zeigen.

### Weitere geprüfte Dimensionen ohne Befund

Slice 1 als atomarer Cutover ist bis auf `C-05` geschlossen: Schemaidentität,
Loader, Request-ID, Writerschemadigest und Nachweisverbund wechseln gemeinsam
(Schritte 2, 3, 7 bis 9), und Schritt 11 verbietet ausdrücklich, einen roten
v1-Test auf Slice 2 zu verschieben.

Der bis Slice 2 verbleibende Antigravity-Adapterrest ist nach Slice 1
tatsächlich unerreichbar: Die Rolle verliert mit der v2-Topologie jede
Freigabeautorität, und Slice 1 reduziert `WorkflowCommitRequest`,
Historymirror, Auditautorisierung und Finalreview-Preflight auf genau eine
Claude-Reviewreferenz. Slice 2 entfernt anschließend Adapter, Registryeintrag,
Binärerkennung, Rolle, Steps, Findingherkunft, Budgets, Retrysonderregeln,
`antigravity_tool_schema`, den `LineNumber`-Sonderfall und die
Konfigurationsoptionen; `src/agent_config.py` mit dem Sonderfall
`choices=VALID_EFFORTS if role != "antigravity"` (`:68`) liegt im Pfad.

Slice 3 verschiebt die fünf aktiven v1-Schemata und fünf Übergangsdokumente in
einen benannten Archivbereich und verlangt, dass weder Runtime noch Tests ihn
lesen; der Guard wird durch eine Negativkontrolle falsifiziert (Schritt 7).

Freigabe- und Gitautorität sind unverändert korrekt: Codex kann sich nicht
selbst freigeben (§6.3), Claude ist einziger unabhängiger Plan-, Slice- und
Finalreviewer, der Orchestrator führt die Vollmatrix einmal pro Fingerprint aus
und löst den lokalen Commit genau einmal nach gebundener Claude-Freigabe aus
(§2, §8, Akzeptanzkriterien in Slice 1 und 3). Der manuelle Ablauf ohne
`run_task` und die Git-Arbeiten erst nach vollständiger Claude-Freigabe und
ausdrücklichem Benutzerauftrag stehen in §2 und §12 übereinstimmend; ein Push
bleibt ausgeschlossen. §2.2 hält zusätzlich fest, dass Claude ausschließlich
seinen Reviewabschnitt schreiben darf — das entspricht meinem tatsächlichen
Vorgehen in diesem Termin.

PRE_MORTEM: In drei Monaten ist die wahrscheinlichste Fehlerursache, dass Slice 1 unter der Annahme begonnen wird, „Antigravity kommt erst in Slice 2 dran", und die Umsetzung dann mitten im Cutover von 25 roten Dry-Run-Tests überrascht wird, die vollständige Antigravity-Reviewstufen durch die echte `WorkflowEngine` skripten. Der naheliegende Ausweg unter Zeitdruck ist, diese Tests vorübergehend zu überspringen oder zu markieren und die Bereinigung auf Slice 2 zu vertagen — womit die Atomizitätsregel aus §7 genau an der Stelle fällt, an der sie geschrieben wurde, und Slice 1 mit einer nicht grünen Matrix zur Reviewfreigabe käme. Die zweite, leisere Variante betrifft das Provider-Subset-Register: Es steht in keiner Pfadliste, wird aber produktiv geladen und bindet 13 Regressionsnamen an genau die zwei Testdateien, die Slice 1 umschreibt; wer dort eine Testfunktion umbenennt, bricht eine Registerprüfung, deren Datenquelle er laut Plan gar nicht anfassen darf.

PLAN_APPROVAL: NO

STATUS: DONE

---

## Planrevision 2 nach dem zweiten Claude-Review

Der zweite Claude-Reviewabschnitt bleibt als historischer Nachweis
unverändert. Die normative Planung in den Abschnitten 1 bis 14 wurde um die
folgenden Korrekturen ergänzt:

FINDING_RESPONSE: C-05 | ACCEPTED | src/dry_run_scenarios.py und tests/test_dry_run_scenarios.py stehen nun zusätzlich im atomaren Slice 1. Der scripted Dry Run wechselt dort gemeinsam mit Workflow und ProtocolBinding auf structured-v2 und Codex-Claude; ein eigenes Akzeptanzkriterium und Pflichtszenario verlangen eine bereits nach Slice 1 grüne Ausführung ohne Antigravity-Step oder STRUCTURED_V1.

FINDING_RESPONSE: C-06 | ACCEPTED | Slice 2 benennt jetzt die tatsächlich vorhandene modulweite Funktion _fresh_state() und ihren Neulauf-Aufrufer als produktive Übergabestellen. Ein Test muss die aufgelösten Profile aus einem über genau diesen Pfad erzeugten Zustand wieder auslesen.

FINDING_RESPONSE: C-07 | ACCEPTED | src/native_provider_schema.py, tests/test_native_provider_schema.py und beide Provider-Subset-JSON-Dateien liegen nun in Slice 1. Ihre unabhängigen v1-Registeridentitäten bleiben bewusst aktiv, weil sie kein Workflowprotokoll versionieren. Der Retirementguard erlaubt exakt diese beiden Identitäten, keinen allgemeinen v1-Skip; alle 13 regression_test-Verweise müssen auf existierende Funktionen zeigen.

PLAN_READY_FOR_REREVIEW: YES

---

## Claude Planreview 3 – Revidierter Plan nach Planrevision 2

REVIEWER: claude

**Reviewer:** Claude (Sonnet, Effort `high`) · **Datum:** 25. August 2026 ·
**Modus:** manuell, read-only, adversarial, separates Claude-Terminal

Basis `b3d49ff`. Nichts implementiert, kein Produktcode und kein Test
verändert, kein `run_task`, kein Resume, kein Provideraufruf. Beide
historischen Reviewabschnitte und beide Planrevisionen sind unverändert.
Ausgeführt wurden ausschließlich read-only Repositoryabfragen zur
Falsifikation konkreter Planbehauptungen.

### Keine Regression der bereits geschlossenen Befunde

Ich habe die Pfadzuordnung programmatisch gegen den normativen Planteil neu
erhoben. `C-01` bis `C-04` sind unverändert geschlossen: Das
Codex-Resultat-v2-Schema, `src/native_codex_contract.py` und dessen Test liegen
weiterhin gemeinsam in Slice 1; `src/orchestrator.py` steht in Slice 1 und 2;
der gesamte v1-Nachweisverbund samt `corpus.jsonl`, `manifest.json`,
Probeprogramm und den vier Archivzielen bleibt in Slice 1. Die Slices sind
weiterhin genau drei (62, 41, 40 Pfade, 113 eindeutig).

Der Abgleich gegen den realen Antigravity-Fußabdruck ist jetzt lückenlos: Alle
Quell-, Test-, Schema-, Konfigurations- und Dokumentdateien mit
`antigravity`/`agy`-Bezug sowie alle 21 Nutzer von `AgentRole.ANTIGRAVITY`
liegen in einem Änderungspfad. Die einzigen verbleibenden Treffer sind
`__pycache__`-Bytecodeartefakte, also generierte Dateien ohne Planrelevanz.

### Disposition der offenen Befunde

`C-05` ist geschlossen. `src/dry_run_scenarios.py` und
`tests/test_dry_run_scenarios.py` stehen jetzt in Slice 1 **und** Slice 2.
Arbeitsschritt 12 verlangt die gemeinsame Umstellung des scripted Dry Runs mit
der Workflowtopologie auf Codex-Claude und `structured-v2`, verbietet nach
Slice 1 ausdrücklich jeden Antigravity-Step und jedes
`ProtocolMode.STRUCTURED_V1` und begrenzt die Doppelnennung in Slice 2
ausdrücklich auf die dortige Providerrestbereinigung. Akzeptanzkriterium und
Pflichtszenario 31 verlangen die bereits nach Slice 1 grüne Ausführung. Damit
ist die von §7 selbst gesetzte Atomizitätsregel für den kritischsten Pfad
eingelöst — der Dry Run treibt die echte `WorkflowEngine` (`:30`, `:1034`) und
war der einzige Ort, an dem die alte Dreier-Topologie samt
`WorkflowStep.ANTIGRAVITY_*`, `approval(AgentRole.ANTIGRAVITY)` und dem
Verdikttext `"REVIEWER: antigravity"` noch aktiv skriptet wurde.

`C-06` ist geschlossen. Slice 2 Arbeitsschritt 5 benennt jetzt die tatsächlich
vorhandene modulweite Funktion `_fresh_state()` in `src/orchestrator.py`
zusammen mit ihrem Neulauf-Aufrufer, verlangt die explizite Übergabe der nach
CLI > Environment > Default aufgelösten Profile als Schlüsselwortparameter und
hält ausdrücklich fest, dass `_fresh_state()` keinen nicht vorhandenen
Treiberzustand voraussetzen darf. Das trifft die reale Struktur: Die Funktion
ist modulweit definiert, besitzt ausschließlich Schlüsselwortparameter und
erzeugt die `ProtocolBinding` in `:3659`; ihr einziger Aufrufer ist `:3864`.
Das Akzeptanzkriterium und Pflichtszenario 33 verlangen, die persistierten
Profile aus einem über genau diesen Pfad erzeugten Zustand wieder auszulesen,
und Resume ohne Optionen sowie `AGENT-PROFILE-DIFF` vor Providerstart bleiben
über Arbeitsschritt 6 und zwei Akzeptanzkriterien konsistent gebunden.

`C-07` ist geschlossen. Alle vier Dateien des Provider-Subset-Registers liegen
in Slice 1. Die Entscheidung, die beiden Registeridentitäten aktiv auf v1 zu
belassen, ist fachlich sauber: Sie versionieren den eigenständigen
Provider-Subset-Vertrag — welche Schemakonstruktionen die jeweilige Provider-CLI
nachweislich akzeptiert und welche lokalen Restinvarianten deshalb registriert
bleiben —, nicht das Workflow- oder Agentprotokoll. Ein Mitziehen auf v2 wäre
eine Versionsbehauptung ohne Inhalt. Der Retirementguard ist jetzt exakt
zugeschnitten: Er nennt die ausgemusterten Identitäten einzeln
(`orchestrator-artifact-v1`, `native-agent-codex-*-v1`,
`native-agent-review-*-v1`), erlaubt ausschließlich die zwei vollständig
ausgeschriebenen Registeridentitäten als Nichtarchiv-Ausnahme und verbietet
einen allgemeinen `*-v1`-Skip ausdrücklich. Pflichtszenario 32 verlangt, dass
alle 13 `regression_test`-Verweise nach der v2-Umstellung auf vorhandene
Funktionen zeigen — genau die Kopplung, die ich als Driftrisiko benannt hatte
(siebenmal `tests/test_native_codex_contract.py`, sechsmal
`tests/test_native_review_request.py`, beide in Slice 1).

### Verbleibender Punkt

NEW_FINDING: C-08 | OBSERVATION | Die beiden nativen Transportidentitäten bleiben im gesamten Plan unerwähnt, obwohl sie hart an das alte Protokoll gebunden sind. `src/workflow_state.py:147-148` definiert `NATIVE_CLAUDE_REVIEW_TRANSPORT = "native-claude-review-v1"` und `NATIVE_CODEX_RESULT_TRANSPORT = "native-codex-v1"`, und `ProtocolBinding.__post_init__` weist jede gesetzte Transportangabe ab, solange der Modus nicht `STRUCTURED_V1` ist („native Claude review transport requires structured-v1", „native Codex result transport requires structured-v1"). Dieselben Strings stehen in `src/artifact_models.py:213` und `:283`, in `src/native_codex_request.py:30`, `src/native_review_request.py:32`, in `src/orchestrator.py:3663` und `:3833` sowie als `const` in den v1-Requestschemata. Der Plan erwähnt weder die Konstanten noch die Modusbindung an irgendeiner Stelle; die Zeichenketten `native-codex-v1` und `native-claude-review-v1` kommen im gesamten Dokument nicht vor. Mechanisch ist das nicht blockierend: Slice 1 kann seine Matrix nicht grün stellen, ohne diese Validierung für `structured-v2` zu öffnen, und alle betroffenen Dateien liegen in Slice 1. Latent bleibt jedoch die Benennung — behält der v2-Modus die `-v1`-Transportstrings, trägt der neue Vertrag eine alte Identität, und der Retirementguard schlägt nicht an, weil seine aufgezählten Muster `native-agent-codex-*-v1` und `native-agent-review-*-v1` weder `native-codex-v1` noch `native-claude-review-v1` treffen. Damit entstünde ausgerechnet im v2-Kern die Art von v1-Restidentität, die der Guard andernorts verhindern soll. | Slice 1 ergänzt einen Arbeitsschritt, der die beiden nativen Transportidentitäten gemeinsam mit dem Protokoll auf v2 hebt und die Modusbindung in `ProtocolBinding.__post_init__` entsprechend auf `structured-v2` umstellt; der Retirementguard in Slice 3 nimmt die abgelösten Werte `native-codex-v1` und `native-claude-review-v1` in seine aufgezählten verbotenen Identitäten auf. Entscheidet sich der Plan bewusst dagegen, hält er die Beibehaltung samt Begründung ausdrücklich fest und nimmt beide Strings — wie die zwei Registeridentitäten — als vollständig ausgeschriebene, benannte Ausnahme in den Guard auf, damit die Entscheidung nicht stillschweigend getroffen wird.

### Weitere geprüfte Dimensionen ohne Befund

Slice 1 ist als atomarer Cutover jetzt geschlossen: Schemaidentität, Loader,
Writerschemadigest, Request-ID, v1-Evidenzstilllegung, dynamischer
v2-Differentialnachweis und der echte Dry Run wechseln gemeinsam, und die
Arbeitsschritte 11 und 12 verbieten ausdrücklich, einen roten Test auf Slice 2
zu verschieben. Slice 2 entfernt anschließend Adapter, Registryeintrag,
Binärerkennung, Rolle, Steps, Findingherkunft, Budgets, Quota- und
Retrysonderregeln, `antigravity_tool_schema` und den `LineNumber`-Sonderfall;
`src/agent_config.py` mit dem Sonderfall
`choices=VALID_EFFORTS if role != "antigravity"` liegt im Pfad. Slice 3
archiviert die fünf aktiven Workflow-/Agent-v1-Schemata und fünf
Übergangsdokumente, verlangt, dass weder Runtime noch Tests die beiden
Archivbereiche lesen, und falsifiziert den Guard über eine Negativkontrolle.

Freigabe- und Gitautorität sind unverändert korrekt: Codex kann sich nicht
selbst freigeben, Claude ist einziger unabhängiger Plan-, Slice- und
Finalreviewer, der Orchestrator führt die Vollmatrix einmal pro Fingerprint aus
und löst den lokalen Commit genau einmal nach gebundener Claude-Freigabe aus.
Das Self-Modification-Paket bleibt vollständig außerhalb von `run_task`;
Git-Arbeiten erfolgen erst nach finaler Claude-Freigabe und ausdrücklichem
Benutzerauftrag, ein Push bleibt ausgeschlossen.

FINDING_STATUS: C-05 | CLOSED | `src/dry_run_scenarios.py` und `tests/test_dry_run_scenarios.py` stehen jetzt zusätzlich im exakten Änderungspfad von Slice 1. Arbeitsschritt 12 verlangt die gemeinsame Umstellung des echten `WorkflowEngine`-Dry-Runs auf Codex-Claude und `structured-v2`, schließt nach Slice 1 jeden `ANTIGRAVITY_*`-Step, jedes Antigravity-Verdikt und jedes `ProtocolMode.STRUCTURED_V1` aus und begrenzt die Doppelnennung in Slice 2 ausdrücklich auf die Providerrestbereinigung. Ein eigenes Akzeptanzkriterium und Pflichtszenario 31 verlangen die bereits nach Slice 1 grüne Dry-Run-Ausführung. Die von §7 gesetzte Atomizitätsregel ist damit für den kritischsten Pfad eingelöst.

FINDING_STATUS: C-06 | CLOSED | Slice 2 Arbeitsschritt 5 nennt korrekt die modulweite Funktion `_fresh_state()` in `src/orchestrator.py` samt ihrem Neulauf-Aufrufer, verlangt die explizite Übergabe der nach CLI > Environment > Default aufgelösten Codex- und Claude-Profile als Schlüsselwortparameter bis zur `ProtocolBinding` und hält ausdrücklich fest, dass die Funktion keinen nicht vorhandenen Treiberzustand voraussetzen darf. Das deckt sich mit dem Repositorybefund: modulweite Definition in `:3617`, ausschließlich Schlüsselwortparameter, `ProtocolBinding`-Erzeugung in `:3659`, einziger Aufrufer `:3864`. Akzeptanzkriterium und Pflichtszenario 33 verlangen das Wiederauslesen der persistierten Profile aus einem über genau diesen Pfad erzeugten Zustand; Resume ohne Optionen und `AGENT-PROFILE-DIFF` vor Providerstart bleiben konsistent gebunden.

FINDING_STATUS: C-07 | CLOSED | `src/native_provider_schema.py`, `tests/test_native_provider_schema.py` sowie beide Register-JSON-Dateien liegen gemeinsam in Slice 1. Die Beibehaltung der zwei v1-Registeridentitäten ist fachlich begründet, weil sie den eigenständigen Provider-Subset-Vertrag versionieren — nachgewiesene Schemafähigkeiten der Provider-CLIs und die daraus folgenden registrierten Restinvarianten — und kein Workflow- oder Agentprotokoll; ein Mitziehen auf v2 wäre eine inhaltsleere Versionsbehauptung. Der Retirementguard zählt die ausgemusterten Workflow-/Agent-v1-Identitäten einzeln auf, erlaubt ausschließlich die beiden vollständig ausgeschriebenen Registeridentitäten und verbietet einen allgemeinen `*-v1`-Skip. Pflichtszenario 32 sichert, dass alle 13 `regression_test`-Verweise nach den v2-bedingten Umbenennungen auf vorhandene Funktionen zeigen; damit kann keine Registerdatei unbemerkt vom Cutover wegdriften.

PRE_MORTEM: In drei Monaten ist die wahrscheinlichste Fehlerursache, dass Slice 1 an seiner eigenen Größe scheitert. Der Slice umfasst jetzt 62 Pfade und stellt Protokoll, fünf Schemata, beide nativen Vertragsmodule, Artifactkette, Recovery, den gesamten Nachweisverbund und den scripted Dry Run in einem einzigen atomaren Schritt um — fachlich richtig geschnitten, aber ohne jeden Zwischenhalt. Wer dort zwei Tage später vor einer roten Matrix steht, kann nicht mehr unterscheiden, ob die Ursache im Schemawechsel, in der Topologie, im Dry Run oder im Evidenzrückbau liegt, und die naheliegende Reaktion ist, einzelne Tests vorübergehend zu überspringen und „später" aufzuräumen. Genau dann fällt die Atomizitätsregel aus §7, die dieser Slice erst durchsetzen soll. Die zweite, leisere Variante betrifft die Transportidentitäten aus `C-08`: Sie sind der einzige v1-Rest im v2-Kern, den der Retirementguard konstruktionsbedingt nicht sieht — bleibt er unbemerkt stehen, trägt der neue Vertrag dauerhaft eine alte Identität, und niemand wird sie später vermissen, weil kein Test und kein Guard sie einfordert.

PLAN_APPROVAL: YES

STATUS: DONE
