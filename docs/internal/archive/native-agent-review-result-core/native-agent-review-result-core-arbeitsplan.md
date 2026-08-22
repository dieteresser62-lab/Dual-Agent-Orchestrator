# Phase 2 – Nativer Reviewresultat-Kern

TARGET_BRANCH: feature/native-agent-review-result

STATUS: MANUAL_BOOTSTRAP_PLAN_APPROVED_BY_CLAUDE

## 1. Auftrag und Abgrenzung

Dieses Arbeitspaket setzt den ersten kleinen, providerunabhängigen Kern aus
Arbeitspaket 2 der
[`ORCHESTRATOR_ROADMAP_PHASE_2_PLUS.md`](ORCHESTRATOR_ROADMAP_PHASE_2_PLUS.md)
um: einen versionierten nativen JSON-Ausgabevertrag für Reviewer und dessen
Domänenvalidierung. Es wird außerhalb von `run_task` nach dem
[`manuellen Bootstrapvertrag`](native-agent-json-manueller-bootstrap.md)
entwickelt.

Das Paket verdrahtet noch keinen Live-Provider. Claude und Antigravity bleiben
im Produktcode auf ihrem gebundenen historischen Textadapter. Der neue Kern ist
zunächst eine vollständig testbare, inaktive Protokolloberfläche, auf die der
native Claude-Adapter im nächsten eigenständigen Paket aufsetzt.

## 2. Repositorybefund

- `src/contracts.py` enthält mit `StepContract`, `FindingRecord`,
  `ReviewEvidence` und `ContractResult` bereits die fachlichen
  Reviewer-Invarianten. `validate_review_response()` gewinnt diese Fakten
  jedoch aus positionsabhängigen Textmarkern.
- `src/agent_adapters.py` erzwingt bei Claude und Antigravity bereits über
  `--json-schema` eine Providerhülle mit genau einem freien Stringfeld
  `response`. Erst dieser String wird anschließend wieder als Textvertrag
  geparst. Die Providerfähigkeit für ein echtes strukturiertes Resultat ist
  damit vorhanden, aber semantisch noch ungenutzt.
- `src/artifact_models.py` und
  `schemas/orchestrator-artifact-v1.schema.json` modellieren persistierte
  Entscheidungen und Findingtransitionen. Sie sind der nachgelagerte
  Recordvertrag, nicht das Transport-Ausgabeschema eines Agenten.
- `src/prompts.py::build_v3_review_contract()` beschreibt weiterhin die
  historische Textmarkergrammatik. Sie wird in diesem Paket nicht geändert.
- Für native Agentenantworten existieren derzeit weder ein eigenes
  versioniertes JSON-Schema noch ein geschlossenes Python-Domänenmodell.

## 3. Zielvertrag

1. Ein neues geschlossenes Schema `native-agent-review-result-v1` beschreibt
   ausschließlich native Reviewerantworten. Unbekannte Felder sind auf jeder
   Ebene verboten.
2. Das Rootobjekt ist eine diskriminierte Union aus `review_result` und
   `stop_request`. Provider- oder Laufzeitfehler sind keine Reviewerentscheidung
   und gehören nicht in diese Modellantwort.
3. Jede Antwort bindet mindestens `schema_version`, `result_type`,
   `request_id` und `reviewer`. `request_id` wird gegen einen unveränderlichen
   lokalen Kontext geprüft, der Run, Work Unit, Operation, Fingerprint,
   Reviewer, Approvalart, Slice-ID, einsbasierte Rundennummer und
   Findingbestand, `test_files`, `test_changes_approved`,
   `allow_new_observations` und `anchor_origin` bindet. Die deterministisch lokal bekannten
   Testdateien werden bei der Konvertierung wertgleich in
   `ContractResult.test_files` übernommen. Das Modell muss diese
   deterministischen Felder nicht erneut als freie Prosa ausgeben.
   Neue Findings übernehmen
   `FindingOrigin.slice_id` und `FindingOrigin.round_number` ausschließlich und
   unverändert aus diesem Kontext; weder Work-Unit-ID noch freie Modellprosa
   dürfen als Ersatz dienen.
4. `review_result` enthält typisierte Arrays für neue Findings,
   Statusänderungen eigener Findings und Reklassifizierungen eigener Findings
   sowie reviewer-authored Anker, optionale strukturierte Reviewevidenz,
   Pre-Mortem und genau eine Entscheidung `approved|denied`. Ein Anker enthält
   geschlossen `anchor_id`, `input_fixture`, `expected` und `tolerance`; nur
   dessen `origin` stammt unverändert aus dem gebundenen Kontext.
5. Neue Findings verwenden ein geschlossenes Akzeptanztestobjekt:
   `prose` mit nichtleerem Text oder `validation_command` mit einem nichtleeren
   Argumentarray. Nur ein `BLOCKER` darf einen Validierungsbefehl verlangen;
   eine `OBSERVATION` bleibt prose-only. Bei der Konvertierung in
   `FindingRecord.acceptance_test` wird `validation_command.argv` im bereits
   produktiv ausgewerteten Format `VALIDATE: ` plus kanonischem JSON-Array
   dargestellt. Die Serialisierung verwendet
   `json.dumps(list(argv), separators=(",", ":"), ensure_ascii=False)`; eine
   Shellanzeige, eigene Shellquotierung oder ein freier Shellstring ist
   unzulässig.
6. Finding-IDs sind eindeutig, rollenrichtig (`C-*` beziehungsweise `A-*`) und
   dürfen nicht zugleich neu angelegt, geschlossen oder reklassifiziert
   werden. Status- und Klassenänderungen dürfen nur eigene, im gebundenen
   Kontext vorhandene Findings betreffen. Jedes zuvor offene, vom aktuellen
   Reviewer selbst gemeldete Finding muss in jeder Runde durch genau eine
   Statusänderung oder Reklassifizierung ausdrücklich aktualisiert werden; ein
   stilles Fallenlassen scheitert fail-closed.
7. Ein positives Review benötigt ein nichtleeres Pre-Mortem, eine passende
   erfolgreiche Validierungsattestierung im lokalen Kontext und keinen offenen
   reviewer-eigenen Blocker. Bei nichtleeren `test_files` ist zusätzlich
   `test_changes_approved=True` erforderlich. Wenn
   `allow_new_observations=False` gilt, darf ein Slice-Review keine Observation
   neu einführen oder zu ihr reklassifizieren. Eine negative Entscheidung
   benötigt mindestens einen offenen eigenen Blocker. Im Finalreview gelten die
   strengeren vorhandenen Eigentums- und Konvergenzregeln: Claude darf kein
   eigenes Finding offenlassen; Antigravity darf nur freigeben, wenn kein
   Finding irgendeines Reviewers offen ist.
8. Ein Review enthält mindestens ein konkretes Findingereignis oder eine
   vollständige Evidenz aus geprüften Dimensionen, größtem Restrisiko und
   realistischer Bruchbedingung.
9. `stop_request` enthält in diesem ersten Kern ausschließlich Regel-ID und
   Begründung und kann weder Freigabe noch Findingtransitionen mitführen.
   Die vorhandenen optionalen `StopRequest.remediation_paths` sind bewusst noch
   nicht Teil des nativen Transports; die Konvertierung setzt sie nachweislich
   auf das sichere Default `()`.
10. Schema- und Domänenvalidierung liefern stabile, typisierte Fehlercodes.
    Derselbe JSON-Wert und Kontext ergeben deterministisch dasselbe
    Domänenresultat und dieselbe kanonische JSON-Repräsentation.
11. Das Domänenmodell konvertiert eine gültige native Antwort in den bereits
    vorhandenen `ContractResult`, ohne Text zu rendern oder
    `validate_review_response()` aufzurufen. Dadurch können spätere Adapter
    denselben fachlichen Finding-Lifecycle wiederverwenden. Die pflichtigen
    `ContractResult.test_files` stammen ausschließlich aus dem gebundenen
    Kontext. `ContractResult.anchors` entstehen aus den strukturierten
    reviewer-authored Ankern, wobei `origin` ausschließlich aus dem Kontext
    stammt. Die Konvertierung erfindet weder Leerwerte noch Metadaten.
12. Größen- und Anzahllimits für Begründungen, Evidenz und Findingarrays
    verhindern unbeschränkte Resultate. NUL-Zeichen, leere Pflichttexte,
    Nicht-Strings in Befehlsarrays und doppelte IDs scheitern fail-closed.

## 4. Slice 1 – Versioniertes Schema und Domänenvalidator

### Ziel

Ein providerunabhängiger, vollständig getesteter nativer Reviewer-Ausgabekern,
der noch keine Liveaufrufe oder Persistenzpfade verändert.

### Exakter Änderungspfad

- `schemas/native-agent-review-result-v1.schema.json`
- `src/native_review_contract.py`
- `tests/test_native_review_contract.py`
- `tests/test_native_review_schema.py`
- `tests/test_validation_matrix.py`
- `docs/internal/native-agent-review-result-core-arbeitsplan.md`
- `docs/internal/native-agent-review-result-core-review.md`

### Umsetzung

1. Definiere das geschlossene Draft-2020-12-JSON-Schema mit `$defs` für
   Finding, Akzeptanztest, Statusänderung, Reklassifizierung, Anker, Evidenz,
   Reviewresultat und Stoprequest.
2. Implementiere unveränderliche Python-Datentypen für das native Ergebnis und
   einen ebenfalls unveränderlichen `NativeReviewContext`. Parsing validiert
   zuerst das JSON-Schema und anschließend alle kontextabhängigen
   Eigentums-, Approval-, Attestierungs- und Findinginvarianten. Kontextfelder
   für Testdateien und Governance-Schalter verwenden die vorhandene Semantik.
   `anchor_origin` bindet die Herkunft aller modellseitig gemeldeten Anker.
3. Verwende vorhandene Enums und Domänentypen aus `src/contracts.py`, wo ihre
   Semantik identisch ist. Ändere den historischen Textparser nicht und
   dupliziere keine konkurrierenden Findingregeln. Die bestehende
   Validierungsmatrix bleibt unverändert; der native Kern muss ihren
   produktiven `VALIDATE:`-Eingangsvertrag verwenden.
4. Stelle eine reine Konvertierung nach `ContractResult` und eine kanonische
   JSON-Serialisierung bereit. Die Konvertierung darf keine Records schreiben,
   Provider starten oder Entscheidungen lokal ergänzen. Herkunftsdaten neuer
   Findings stammen byte-/wertgleich aus `NativeReviewContext`; strukturierte
   Validierungsbefehle werden mit `VALIDATE: ` und einem kanonischen
   JSON-Argumentarray in den bestehenden Stringvertrag überführt.
5. Liefere stabile Fehlercodes mindestens für Schemafehler,
   Request-/Reviewerbindung, ID-Eigentum, unbekannte Findingreferenz,
   Doppel-/Widerspruchsereignis, ungültigen Akzeptanztest,
   Approval-vs.-Findingzustand und fehlende Evidenz beziehungsweise
   Pre-Mortem.

### Akzeptanztests

- Minimales gültiges Claude- und Antigravity-Review mit Evidenz und positiver
  Entscheidung validiert und konvertiert deterministisch.
- Review mit neuem Blocker und negativer Entscheidung validiert; das Finding
  erscheint genau einmal und offen im `ContractResult`.
- `FindingOrigin.slice_id` und `FindingOrigin.round_number` eines neuen
  Findings entsprechen exakt den gebundenen Kontextwerten; Work-Unit-ID,
  Standardwerte oder freie Prosa können sie nicht ersetzen.
- `ContractResult.test_files` entspricht exakt dem im `NativeReviewContext`
  gebundenen Tupel. Reviewer-authored Anker werden mit Kontext-`anchor_origin`
  zu `AnchorRecord` konvertiert; ein Test weist mit
  `gates.detect_anchor_changes()` einen hinzugefügten und einen geänderten
  nativen Anker gegen die gebundene Baseline nach.
- Eine schema-gültige Antwort mit fremder `request_id` oder abweichendem
  `reviewer` wird gegen den gebundenen Kontext deterministisch mit stabilem
  Bindungsfehlercode abgewiesen; mindestens Run/Fingerprint/Runde fließen in
  die lokal erzeugte Requestbindung ein.
- Ein `review_result` mit drei leeren Findingereignisarrays und ohne
  Reviewevidenz wird deterministisch mit einem stabilen Fehlercode abgewiesen.
  Sowohl das JSON-Schema als auch der Domänenvalidator müssen exakt dieselbe
  Fixture verwerfen.
- Statusänderung oder Reklassifizierung eines fremden beziehungsweise
  unbekannten Findings scheitert mit stabilem Fehlercode.
- Ein zuvor offenes eigenes Finding, dessen ID in keiner Statusänderung oder
  Reklassifizierung der aktuellen Antwort vorkommt, wird mit stabilem
  `missing-own-finding-update`-Fehler abgewiesen; dieselbe Fixture mit genau
  einer passenden Aktualisierung konvertiert erfolgreich.
- `C-*` durch Antigravity, `A-*` durch Claude, doppelte IDs und ein Finding in
  mehreren Ereignisarrays scheitern fail-closed.
- Observation plus `validation_command`, leeres `argv`, Shellstring statt
  Argumentarray und nicht konfigurierte Befehlsfamilie werden abgewiesen.
- Ein erlaubter `validation_command` wird bytegleich als `VALIDATE: ` plus
  kanonischem JSON-Argumentarray in `FindingRecord.acceptance_test`
  überführt. Ein Integrationstest reicht das konvertierte `ContractResult`
  an `validation_matrix.select_validation_request()` weiter und weist nach,
  dass dessen `ValidationRequest` exakt dasselbe `argv` enthält.
- Positive Entscheidung ohne Pre-Mortem, ohne passende PASS-Attestierung oder
  bei offenem eigenen Blocker wird abgewiesen.
- Positive Entscheidung mit nichtleeren Kontext-`test_files` und
  `test_changes_approved=False` wird mit stabilem Fehlercode abgewiesen.
- Bei `allow_new_observations=False` werden eine neue Observation und eine
  Reklassifizierung zu Observation im Slice-Review mit stabilem Fehlercode
  abgewiesen.
- Eine negative Entscheidung ohne offenen eigenen Blocker wird mit stabilem
  Fehlercode abgewiesen.
- Finalreview mit neuem `OBSERVATION`-Finding oder offenen Findings innerhalb
  der vorhandenen Reviewerzuständigkeit wird abgewiesen.
- Ein positives Antigravity-Finalreview mit einem noch offenen Claude-Finding
  wird trotz leerer eigener Findingmenge mit stabilem Fehlercode abgewiesen;
  nach dessen Schließung ist dieselbe Reviewentscheidung zulässig.
- Stoprequest mit Zusatzfeldern, Findingdaten oder Entscheidungsfeld wird vom
  Schema abgewiesen.
- Ein gültiger nativer Stoprequest wird zu `StopRequest` mit
  `remediation_paths == ()` konvertiert; Remediation-Pfade sind als bewusstes
  Nichtziel dieses ersten Transportkerns dokumentiert.
- Derselbe Stoprequest erzeugt ein gestopptes `ContractResult` mit
  `anchors == ()`, `test_files == ()`, `evidence is None`,
  `pre_mortem is None` und der kontextgebundenen Validierungsattestierung.
- Ein Review mit mindestens einem nativen Anker und
  `NativeReviewContext.anchor_origin=None` ist schemagültiger Transport, wird
  aber vom kontextabhängigen Domänenvalidator mit stabilem Fehlercode
  abgewiesen; eine Herkunft wird niemals leer oder synthetisch erfunden.
- Unbekannte Root-/Nested-Felder, falsche Schema-Version, NUL, überlange Texte,
  leere Pflichttexte und zu große Arrays werden abgewiesen.
- Schema und Python-Modell akzeptieren beziehungsweise verwerfen dieselbe
  adversarielle Fixture-Matrix.
- Kanonische Serialisierung ist schlüsselgeordnet, UTF-8-stabil und bei
  wiederholtem Roundtrip bytegleich.
- Historische Textmarker- und Artifact-Tests bleiben unverändert grün.

## 5. Validierung

Fokussiert während der Implementierung:

```bash
python3 -m pytest tests/test_native_review_contract.py tests/test_native_review_schema.py tests/test_validation_matrix.py -v
python3 -m pytest tests/test_contracts.py tests/test_artifact_models.py -v
```

Nach Abschluss dieses Orchestrierungs-/Parserpakets zwingend:

```bash
python3 -m pytest tests/ -v
git diff --check
```

Die vollständige Suite wird lokal außerhalb des Claude-Reviewerprozesses
ausgeführt. Claude erhält nur Ergebnis, relevanten Diff und die fokussierten
Invarianten.

## 6. Nichtziele

- Keine Änderung an `ClaudeAdapter`, `AntigravityAdapter` oder deren
  Liveaufrufen.
- Keine Aktivierung eines neuen `protocol_mode`.
- Keine neuen Artifact-Recordtypen und keine Änderung am
  `orchestrator-artifact-v1`-Schema.
- Keine Persistenz, Replay- oder State-v3-Migration.
- Keine native Requesthülle; sie folgt in einem eigenen kleinen Paket.
- Keine nativen `stop_request.remediation_paths`; der erste Transportkern nutzt
  ausschließlich das bestehende sichere Default `()`.
- Keine Red-State-Freigabeausnahme: Der native Erstkern setzt
  `ContractResult.red_state_followup_slice=None` und verlangt für jede positive
  Entscheidung eine vollständige PASS-Attestierung. Die spätere Einführung
  eines nativen Red-State-Vertrags ist ein eigenes Arbeitspaket.
- Keine Findingquarantäne aus P2-FU-033 und keine neue Textmarkergrammatik.
- Keine native Codex-Ausgabe.
- Kein Antigravity-Review in diesem manuellen Bootstrapprozess.

## 7. Stopbedingungen

- Der Kern benötigt Änderungen am historischen Textparser oder am Liveadapter.
- Schema und Domänenmodell können eine vorhandene fachliche Invariante nicht
  ohne freie Prosa oder heuristische Interpretation ausdrücken.
- Eine gültige Antwort kann nicht eindeutig an genau einen lokalen
  `NativeReviewContext` gebunden werden.
- Die Konvertierung zu `ContractResult` würde bestehende Findingzustände
  mutieren, Records schreiben oder Approvaldaten erfinden.
- Ein produktiver Aufrufer müsste native Anker umgehen, statt
  `ContractResult.anchors` an den bestehenden Anchor-Driftguard zu reichen.
- Das Paket benötigt einen neuen Artifacttyp oder mehr als die beiden
  vorgesehenen produktiven Dateien.

## 8. Review- und Abschlussfolge

1. Direkter Claude-Planreview mit Sonnet, Effort `high`, read-only und
   schemaerzwungener JSON-Antwort.
2. Implementierung des einen Slices durch Codex.
3. Fokussierte Tests und vollständige lokale Suite.
4. Direkter Claude-Slice-Review desselben eingefrorenen Diff-Fingerprints.
5. Gegebenenfalls Blockerkorrektur mit genau einem erneuten Claude-Review.
6. Direkter branchweiter Claude-Finalreview.
7. Dokumentierter Hinweis:
   `ANTIGRAVITY: NOT_RUN (manual bootstrap exception)`.
8. Abschlusscommit und Merge nur nach ausdrücklicher Benutzerfreigabe.
