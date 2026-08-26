# Slice 02 – Entfernung der Markerparser und Textvertragsreparatur

## Reviewgrenze

- Arbeitsplan: `docs/internal/native-only-transport-parser-retirement-und-evidenzeffizienz-arbeitsplan.md`
- freigegebener Slice-01-Commit: `fc978d8 feat(orchestrator): require native agent transports`
- Ziel: ausschließlich native, requestgebundene JSON-Resultate für Codex und Claude; kein produktiver Textparser, Textadapter oder LLM-Reparaturturn

## Implementierung

### Native Adapter und Runtime

- `NativeCodexAdapter` und `NativeClaudeReviewAdapter` erben direkt von der providerneutralen `_BaseAdapter`-Basis. Die Registry konstruiert ausschließlich diese beiden Klassen.
- Codex behält JSONL-Streamfilter, Hostanforderungen und Providerinput-Komponenten; Claude behält Reviewerkennzeichen, Workspacebindung, Capability-Smoke, Hostanforderungen und Providerinput-Komponenten.
- Textadapter, Markerabschneiden, Textvertragsextraktion, Diagnose-Recovery und alle `*_contract_repair`-Provideroperationen wurden entfernt.
- Ein ungültiges, ungebundenes oder domänenwidriges JSON-Resultat endet lokal fail-closed. Es wird nicht normalisiert und nicht einem zweiten LLM zur Reparatur übergeben.
- Live-Stdout wird ausschließlich aus validen nativen Resultatfeldern kompakt und menschenlesbar projiziert.

### Workflow und Requests

- Codex-Planung, Planrevision, Implementierung, Korrektur und Finalbericht akzeptieren nur native Ergebnisvarianten.
- Claude-Plan-, Slice-, Konvergenz- und Finalreviews akzeptieren nur den requestgebundenen nativen Reviewvertrag.
- Das Reviewrequest-Readerschema besitzt keinen Reparatur-Wurzelzweig und keine nur daraus erreichbaren Reparaturdefinitionen mehr.
- `workflow-prompt` wurde aus dem Codex-Evidenzmanifest entfernt. Die kleine `native-policy`-Evidenz ist für jede Operation vorhanden und wird mit Inhalt und Digest gebunden.
- Damit bleiben insbesondere `codex_plan` und `codex_plan_revision` atomar gültig: Ihre Evidenzmenge ist nach Entfernung des früher einzigen `workflow-prompt` weiterhin nicht leer. Dies schließt Claudes erste Slice-01-Observation.
- Dry-run-Szenarien verwenden native JSON-Dokumente und explizite Request-ID-Bindung. Der allgemeine Dry-run erfindet keine Freigabe.

### Unveränderliche Effizienzbaseline

- `tests/test_provider_input_efficiency.py` liest Baseline und Lock nur noch und prüft den reviewten SHA-256-Verbund sowie jede interne Komponenten- und Manifestbindung.
- Der Test weist zusätzlich nach, dass `workflow-prompt` im aktuellen Requestaufbau fehlt.
- Baseline und Lock wurden gegenüber dem Slice-01-Commit nicht verändert und bleiben damit historische Messgrundlage; ein Rebase oder gemeinsames Neuerzeugen gehört nicht zum zulässigen Wartungspfad. Dies schließt Claudes zweite Slice-01-Observation.
- Verifizierte Digests:
  - Baseline: `3f07b52b79badf1340998973c4145b20a93892ea0f174942cd21ab1331c7adfc`
  - Lock: `11bb60a6aed1ab78b7911d9a2eb9ea6a216b845a313c324df0558085ad9fabb9`

### Retirementguard und Dokumentation

- Die aktiven Rootverträge beschreiben semantische Rollen und verweisen für Maschinenresultate auf die versionierten nativen JSON-Schemata.
- Ein statischer Guard durchsucht den vollständigen Produktionsquellbaum nach den entfernten Parser-, Adapter-, Repair- und Evidenzsymbolen.
- Ein zweiter Guard verhindert aktive Ergebnismarkergrammar in `AGENTS.md`, `CLAUDE.md`, `CODEX.md` und `README.md`; formale Task- und Plan-Handoff-Marker bleiben davon gezielt unberührt.
- Tests der stillgelegten Textnormalisierung und Text-Recovery wurden entfernt, nicht übersprungen. Native Negativtests prüfen ungültiges JSON, falsche Request-ID, Writerschemaabweichung und Domänenverletzungen.

## Adressierte Claude-Observations aus Slice 01

1. **Planrequests nach Entfernung von `workflow-prompt`:** Die typisierte und digestgebundene `native-policy`-Evidenz wird vor allen operationsspezifischen Evidenzen eingefügt. Die Nichtleerheitsinvariante gilt damit auch für Plan und Planrevision.
2. **Historische Baseline darf nicht rebaset werden:** Baseline und Lock sind bytegleich zum freigegebenen Slice-01-Stand, ihre erwarteten Digests stehen fest im read-only Regressionstest, und der aktuelle Requestaufbau wird separat auf Abwesenheit der Altkomponente geprüft.

## Validierung

- vollständige Repositorymatrix: `998 passed in 70.53s`
- gezielter Rootvertrag-/Retirementguard-Lauf: `45 passed`
- fokussierter Workflow-/Orchestrator-/Replay-Lauf nach Entfernung der Legacytests: `158 passed`
- `git diff --check`: sauber
- Baseline-/Lock-Diff gegen `fc978d8`: leer
- externe Provideraufrufe: keine

## Reviewhinweise

Claude soll insbesondere adversarial prüfen:

- ob ein produktiver Codex- oder Claude-Pfad noch Markertext akzeptieren oder einen Repairturn starten kann;
- ob die native Adapter-MRO und die providerbezogenen Methoden vollständig erhalten sind;
- ob Plan und Planrevision nach `workflow-prompt`-Entfernung garantiert eine gültige, nichtleere und requestgebundene Evidenz besitzen;
- ob Baseline und Lock tatsächlich bytegleich und ohne Regenerationspfad bleiben;
- ob der Retirementguard aktive Ergebnisgrammar zuverlässig erkennt, ohne Task-/Planverträge oder Archive fälschlich zu sperren;
- ob die entfernten Legacytests ausschließlich stillgelegtes Verhalten abdeckten und keine native Failure-, Resume- oder Idempotenzgrenze verloren ging.

TEST_FILES_TOUCHED: tests/test_agent_adapters.py, tests/test_agent_runtime.py, tests/test_contracts.py, tests/test_dry_run_scenarios.py, tests/test_language_consistency.py, tests/test_native_codex_contract.py, tests/test_native_review_request.py, tests/test_native_transport_retirement.py, tests/test_orchestrator_runtime.py, tests/test_parsing.py, tests/test_prompts.py, tests/test_provider_input_efficiency.py, tests/test_quota_wait.py, tests/test_review_runtime_hardening.py, tests/test_structured_artifact_regressions.py, tests/test_workflow.py, tests/test_workflow_state.py

IMPLEMENTATION_READY: 02 | YES

STATUS: DONE

---

## Claude-Review zu Slice 02

REVIEWER: claude

Manueller Review außerhalb von `run_task`, ohne Provideraufruf, ohne
strukturierte JSON-Ausgabe und ohne vollständige Testsuite. Produktcode und
Tests blieben unverändert; angehängt wurde ausschließlich dieser Abschnitt.
Reviewgrenze: `fc978d8 feat(orchestrator): require native agent transports`
gegen den uncommitteten Arbeitsbaum, einschließlich der ungetrackten Datei.
Der Slice löscht 9 316 Zeilen gegen 1 324 neue; ich habe deshalb weniger die
Ergänzungen als die Löschungen adversarial geprüft. Die vorgelegte Evidenz
(998, 45 und 158 passed) habe ich nicht reproduziert; alle Gegenproben unten
sind providerfrei.

### 1. Native-only Produktionspfade

Die Ergebnisgrammatik ist restlos aus `src/` verschwunden. Ich habe fünfzehn
Symbole einzeln gezählt — die vier Textvalidierer und -parser, beide
Normalisierer, die Repairoperation, beide Textpersistenzsenken, beide
Textadapterklassen und die vier Markerpromptbauer: **jeweils null Treffer**.
`src/prompts.py` besteht nur noch aus zwei Systemrichtlinienkonstanten.
`invoke_codex()` und `invoke_reviewer()` geben unbedingt
`NativeAgentCodexOutput` beziehungsweise `NativeAgentReviewOutput` zurück, und
die einzigen verbliebenen Transportreferenzen in `src/workflow.py` sind die
Fail-closed-Zusicherung in `:2908-2909`; die frühere Verzweigung zwischen
nativem und textuellem Zweig existiert nicht mehr.

Der Providerbetrieb `claude_contract_repair` ist aus
`PROVIDER_OPERATIONS` entfernt; übrig bleiben sechs Codex- und drei
Claude-Operationen. Damit kann ein Vertragsfehler keinen zweiten LLM-Turn
mehr anstoßen.

Die Fehlergrenze habe ich am lebenden Vertrag gemessen statt sie anzunehmen.
`validate_native_review_document()` weist Markertext, ein in Markdown-Zäune
gehülltes JSON, ein leeres Objekt, eine abgelöste Schemaversion und eine
ungültige Request-ID-Form sämtlich mit dem stabilen lokalen Code
`schema-invalid` ab — lokal, fail-closed, ohne Reparaturpfad. Die
Fehlertaxonomien beider Verträge sind vollständig erhalten (neun Codex-, vierzehn
Reviewcodes einschließlich `request-mismatch`, `reviewer-mismatch`,
`finding-reference-not-open` und `stop-content-invalid`).

### 2. Adapter-Refactoring

Die MRO ist geschlossen: `NativeCodexAdapter` und
`NativeClaudeReviewAdapter` stehen beide direkt auf `_BaseAdapter`; keine
Textadapterklasse kommt darin vor. Die Registry liefert genau diese beiden
Typen.

Die Flächenaufteilung entspricht exakt dem in Planrunde 4 freigegebenen
Schnitt. Für Codex liegen `stream_filter`, `_provider_input_components` und
`required_hosts` (`chatgpt.com`, `api.openai.com`) auf der eigenen Klasse;
`reviewer` und `bind_reviewer_workspace` bleiben die neutralen
`_BaseAdapter`-Vorgaben. Ich habe `NativeCodexAdapter().bind_reviewer_workspace(...)`
tatsächlich ausgeführt: Der Rumpf ist ein echtes No-op und bleibt wirkungslos.
Für Claude liegen `reviewer = True`, die wirksame Workspacebindung, der
Capability-Smoke, `_provider_input_components` und `required_hosts`
(`api.anthropic.com`) auf der eigenen Klasse, und die Harness-Bindung an
`src/review_harness.py` ist als Konstruktordefault erhalten.
`hasattr(NativeCodexAdapter, "build_capability_smoke_command")` ist `False`,
für Claude `True` — die Claude-spezifische API wird nicht vererbt.

### 3. Meine Slice-01-Beobachtungen

**Nichtleere Evidenz für Plan und Planrevision.** Der Aufbau in
`src/workflow.py:3026-3038` setzt `native-policy` als erste Evidenz
bedingungslos für jede Codexoperation; die Plankomponente kommt nur hinzu,
wenn ein freigegebener Plan existiert. Ich habe alle sechs Operationen über
den echten `build_native_codex_request()` gebaut: Plan und Planrevision
tragen genau eine Evidenz, die übrigen vier genau zwei; alle Mengen sind
sortiert, eindeutig und vollständig digestgebunden. Die leere Evidenzmenge
bleibt mit `evidence-invalid` fail-closed abgewiesen. Damit ist die von mir in
Slice 01 gemeldete Gefahr beseitigt.

**Policy vor dem Requestdigest gebunden.** Ich habe denselben Planrequest
zweimal gebaut und dabei nur den Policytext verändert: Die `request_id`
ändert sich. Die Systemrichtlinie liegt also innerhalb des digestgebundenen
Vertrags und nicht als freier Prompttext daneben.

**Baseline und Lock unverändert.** `git diff fc978d8 -- tests/fixtures/` ist
leer, und die selbst berechneten Digests stimmen mit den erwarteten Werten
überein: Baseline `3f07b52b…adfc`, Lock `11bb60a6…abb9`. Der umgebaute
`tests/test_provider_input_efficiency.py` ist reine Leseprüfung: Er hält beide
reviewten Digests als Literale — eine gemeinsame Neuerzeugung von Fixture und
Lock bricht ihn deshalb —, prüft die interne Vollständigkeit der
Komponentenbindungen und weist unabhängig nach, dass `src/workflow.py` die
Altkomponente nicht mehr führt, während `native-policy` vorhanden ist. Ein
Rekonstruktions- oder Schreibpfad existiert nicht mehr.

### 4. Readerschema, Dry-run und Failure-Grenzen

Das Reviewrequest-Schema hat die Repairdefinitionen verloren und die Wurzel
von einem zweiästigen `oneOf` auf `{"$ref": "#/$defs/review_request"}`
umgestellt. Ich habe den Semantikdiff gefahren: entfernt wurden exakt die
beiden Repairdefinitionen, hinzugefügt keine, und alle fünfzehn verbliebenen
gemeinsamen Definitionen sind bytegleich — die +330/−131 Zeilen sind eine
Umformatierung. Weil eine Wurzel-`$ref` vom lokalen Offlinevalidator
unterstützt sein muss, damit sie nicht vakuum wirkt, habe ich das eigens
geprüft: Ein leeres Objekt, ein Fremdobjekt, ein handgebautes Repairdokument
und ein unvollständiger Reviewrequest werden alle mit `SchemaMismatch`
abgewiesen. Das Schema ist also wirksam und nicht bloß leer.

Der Dry-run akzeptiert nur native JSON-Dokumente, weist Markerausgabe und den
abgelösten Repairkanal ausdrücklich zurück, verlangt je Ereignis genau eine
native Ausgabe oder ein Failure und bewahrt die Dokumentbytes semantisch. Er
erfindet keine Freigabe.

Die ausdrücklich zu erhaltenden Parser sind unversehrt: `parse_quota_reset`,
`classify_agent_failure`, `is_quota_or_rate_limit_error`, die vier
Reset-Muster, `_sanitize_provider_diagnostic` und die
`QuotaReset`/`QuotaWaitPolicy`-Typen stehen weiterhin in
`src/agent_runtime.py`; Taskvertrag, Plan-Handoff und JSON-Decoder sind
unberührt. `remediation_paths` bleibt als typisierte Domäneneigenschaft
erhalten (`src/contracts.py:266`), und die Stop-Variante existiert in beiden
nativen Verträgen — die aus `AGENTS.md` entfernten Marker sind also nur die
Textschreibweise, nicht der Begriff.

### 5. Retirementguard und Testumbau

Der neue Guard prüft zwei Flächen. `test_retired_result_grammar_and_repair_symbols_cannot_reenter_runtime`
liest den gesamten Produktionsquellbaum `src/**/*.py` und verlangt die
Abwesenheit von zwölf Symbolen, darunter beide Textadapterklassen, beide
Normalisierer, die Repairoperation und die Altkomponente.
`test_active_root_contracts_do_not_describe_retired_result_markers` prüft
`AGENTS.md`, `CLAUDE.md`, `CODEX.md` und `README.md` gegen elf
Ergebnismarker. Beide Prüfungen sind auf Ergebnisgrammatik beschränkt; die
formalen Task- und Plan-Handoff-Marker sowie die Archivhistorie bleiben
unberührt, und die Rootverträge behalten ihre semantischen Rollenregeln
vollständig — Findingeigentum, Konvergenzregel, strengere Abschlussregel,
Blocker-/Observation-Asymmetrie und die Git-Grenze stehen unverändert, nur
auf typisierte JSON-Felder umformuliert.

Den Testumbau habe ich stichprobenartig gegengeprüft statt ihn zu glauben.
Aus `tests/test_structured_artifact_regressions.py` verschwinden vier Tests;
zwei davon betreffen erkennbar Legacy-Textreplay, die beiden anderen prüften
`driver.recover_pending_reviewer()` gegen hashgebundene Provider-Logdateien.
Ich habe verifiziert, dass genau diese Produktionsmethode nicht mehr existiert
— `src/` kennt nur noch `recover_pending_native_codex`,
`recover_pending_native_reviewer` und
`recover_pending_native_reviewer_before_policy` —, und dass auch die zugehörige
Fehlermeldung repositoryweit verschwunden ist. Die Löschung betraf also
totes Verhalten. Die native Record-ahead-, Recovery- und Near-Miss-Abdeckung
ist erhalten: Zwölf Aufrufstellen der drei nativen Recoverymethoden verteilen
sich auf `tests/test_orchestrator_runtime.py` und `tests/test_workflow.py`,
und `tests/test_structured_artifact_regressions.py` behält fünfzehn Tests
einschließlich Mirror-Guard, Quota-Pause, Netzwerkretry und Resume-Idempotenz.
Die stark verkleinerten Dateien behalten jeweils den fachlichen Kern:
`tests/test_contracts.py` prüft weiter Findingeigentum,
Reklassifizierungshistorie und Ankerdeterminismus.

Meine fokussierte Gegenprobe über Retirementguard, Effizienzbaseline, Parser,
Prompts, Reviewhärtung, Dry-run, Adapter, Verträge und Sprachkonsistenz
meldet `69 passed in 7.44s`; `git diff --check` ist sauber.

### 6. Ein nicht blockierender Scopepunkt

Drei geänderte Testdateien stehen nicht in der Slice-2-Pfadliste des
Arbeitsplans: `tests/test_quota_wait.py`, `tests/test_workflow_state.py` und
`tests/test_structured_artifact_regressions.py`. Alle drei sind mechanisch
erzwungen — der Importwechsel auf den nativen Claude-Adapter, ein entfallenes
`prompt=`-Argument und die vier Löschungen toter Textrecoverytests — und ohne
sie bliebe die vom Slice geforderte grüne Vollmatrix unerreichbar. Der
Implementierungsbericht deklariert alle drei offen unter
`TEST_FILES_TOUCHED`; verdeckt ist hier nichts. Für die branchweite
Gesamtfreigabe sollte die Abweichung dennoch einmal ausdrücklich abgeglichen
werden, damit der Abschlussreview nicht über drei undeklarierte Pfade
stolpert.

NEW_FINDING: C-08 | OBSERVATION | Drei geänderte Testdateien liegen außerhalb der im Arbeitsplan deklarierten Slice-2-Pfadliste: `tests/test_quota_wait.py` (Importwechsel von der gelöschten Textadapterklasse auf `NativeClaudeReviewAdapter`), `tests/test_workflow_state.py` (ein entfallenes `prompt=`-Argument an der nativen Requestprojektion) und `tests/test_structured_artifact_regressions.py` (Entfernung von vier Tests, deren Produktionsmethode `recover_pending_reviewer` in diesem Slice gelöscht wurde). Alle drei Änderungen sind mechanisch erzwungene Folgen der im Scope liegenden Löschungen, enthalten kein neues Verhalten und sind im Implementierungsbericht unter `TEST_FILES_TOUCHED` offen deklariert; ohne sie wäre die vom Slice geforderte grüne Repositorymatrix nicht erreichbar. Es liegt damit kein verdeckter Eingriff vor, sondern eine Abweichung zwischen deklarierter Planpfadliste und tatsächlichem Änderungsumfang, die im branchweiten Abschlussreview sonst als undeklarierter Pfad auffällt. | Der Arbeitsplan ergänzt die drei Pfade in der Slice-2-Liste oder hält im Slice-03- beziehungsweise Abschlussbericht ausdrücklich fest, dass sie als erzwungene Folgeänderungen der Slice-2-Löschungen gelten; ein Abgleich im Gesamtreview weist nach, dass jede der drei Dateien ausschließlich Importe, Signaturen oder Tests gelöschter Produktionsmethoden betrifft und keine native Positiv-, Negativ-, Failure-, Resume- oder Idempotenzabdeckung verloren hat.

FINDING_STATUS: C-07 | CLOSED | In meiner zweiten Slice-01-Runde geschlossen und in diesem Slice nicht wieder geöffnet. Die eingefrorene Effizienzbaseline und ihr Lock sind gegenüber `fc978d8` bytegleich geblieben — der Fixturediff ist leer, und die selbst berechneten Digests entsprechen den reviewten Werten `3f07b52b…adfc` und `11bb60a6…abb9`. Der umgebaute `tests/test_provider_input_efficiency.py` besitzt keinen Rekonstruktions- oder Schreibpfad mehr, hält beide Digests als Literale und bindet zusätzlich unabhängig, dass die Altkomponente aus dem aktiven Requestaufbau verschwunden und `native-policy` an ihre Stelle getreten ist. Die in Slice 01 gebundenen Manifestbeiträge bleiben damit als Messanker für Slice 03 unverändert verfügbar.

PRE_MORTEM: In drei Monaten ist die wahrscheinlichste Fehlerursache nicht die Rückkehr eines Textparsers — dafür ist die Fläche jetzt zu klein und der Guard zu konkret —, sondern die stille Erosion der Systemrichtlinie. `NATIVE_CODEX_SYSTEM_POLICY` wird heute an zwei Stellen desselben Requests transportiert: als Präfix des typisierten `work_context` und zusätzlich als Evidenzkomponente `native-policy`. Genau diese Doppelung hält die Evidenzmenge von Plan und Planrevision nichtleer und ist damit die einzige Zusicherung, dass der Planrequest überhaupt baubar bleibt. Wer in Slice 03 die geplante Deduplizierung inhaltsgleicher Komponenten implementiert und dabei nur auf Inhaltsgleichheit statt auf Komponentenrolle prüft, entfernt für die beiden Planoperationen die letzte Evidenz und bekommt kein stilles Fehlverhalten, sondern ein hartes `evidence-invalid` — laut genug, aber an einer Stelle, die niemand mit der Dedupregel in Verbindung bringt. Die zweite, leisere Variante betrifft den Retirementguard: Seine Symbolliste ist eine Zeichenkettensuche über `src/`; eine spätere Wiedereinführung unter anderem Namen — etwa ein `normalize_agent_output` statt `normalize_review_contract_output` — passiert ihn unbemerkt, weil die tragende Sicherheit die Vertragsgrenze ist und nicht diese Liste.

SLICE_APPROVAL: 02 | YES

STATUS: DONE
