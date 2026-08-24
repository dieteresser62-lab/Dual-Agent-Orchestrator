# Slice-03-Review – Native Agent Contract Closure

## Status

**Implementierung:** abgeschlossen

**Claude-Slice-Review:** ausstehend

**Basiscommit:** `7ba867d` (`feat(native-contracts): complete contract closure slice 01 and slice 02`)

Dieses Dokument ist die menschenlesbare Reviewingabe für Slice 03. Es ist
keine technische Entscheidungs- oder Recoveryquelle.

## Gegenstand

Slice 03 friert die in Slice 01 und Slice 02 freigegebenen Writerschemas durch
ein versioniertes Corpus, eine Differentialmatrix und direkte isolierte
Provider-Canaries ein. Zusätzlich führt er eine typisierte Canary-
Ausführungsgrenze für den nativen Codex-Produktionspfad ein.

## Implementierter Stand

### Corpus und Requestbindung

- `tests/fixtures/native-contract-corpus/corpus.jsonl` enthält alle 14 im
  Korrekturstand auffindbaren erfolgreichen nativen Rohantworten bytegetreu.
- `manifest.json` bindet Herkunft und SHA-256 jedes Antworttexts.
- Zwölf vollständige kanonische Claude-Requestdokumente wurden inventarisiert.
  Exakt drei Request-IDs besitzen eine archivierte Antwort; alle drei Paare
  wurden als `request_bound` übernommen.
- Der Loader rechnet die Request-ID aus dem kanonischen Requestdokument ohne
  `request_id` nach, rekonstruiert sämtliche Felder des
  `NativeReviewContext`, erzeugt das aktuelle request-spezifische Writerschema,
  validiert die historischen Antwortbytes dagegen und führt danach die lokale
  gebundene Domänenkonvertierung aus.
- Elf übrige Antworten sind ausdrücklich `schema_only`. Sie belegen nur
  Digest und allgemeine v1-Lesbarkeit. Eine Hochstufung ohne Originalrequest
  ist nicht möglich.
- Drei technische Failure-Envelopes werden in der Inventurzählung separat
  katalogisiert und nicht als native Modellantworten behandelt.

### Differentialnachweis

- Die Positivmatrix konstruiert und bindet Codex Plan, Implementierung,
  Korrektur und Finalbericht sowie Claude Plan, initialen Slice,
  Konvergenzrunde und Finalreview.
- Die seit Slice 01 geführte Provider-Ausnahmeliste wird vollständig gelesen;
  jeder Eintrag muss eine reale Regressionstestfunktion und einen
  Providerbeleg benennen.
- Die bereits in den Codex- und Claude-Contracttests ausgeführten
  Pflichtmutationsklassen bleiben Teil der fokussierten und vollständigen
  Matrix.
- Die rote Kontrolle entfernt absichtlich die Nonblank-Regel für eine
  Reviewevidenz-Dimension. Das manipulierte Writerschema akzeptiert den Wert,
  die lokale Bindung lehnt ihn mit einem nicht registrierten Fehlercode ab;
  der Differentialtest wird dadurch gezielt rot.

### Canary-Ausführungsgrenze

- `NativeCodexExecutionBoundary` unterscheidet typisiert `production` und
  `canary`.
- Produktion bleibt unverändert auf Repository-CWD, `workspace-write` und
  Repository-Assetwurzel gebunden.
- Canary erzwingt `read-only`, ein existierendes wegwerfbares CWD und eine
  existierende Evidenzassetwurzel außerhalb des Repositorys.
- Adapter und Runtime nehmen dieselbe Boundaryinstanz entgegen. Das
  Probe-Werkzeug ruft `NativeCodexAdapter` und `run_native_codex_agent()` auf;
  es baut im Canarymodus keine zweite Codex-Kommandozeile.
- Die vorgelagerte Subset-Sonde bleibt davon getrennt und behält ihren bereits
  freigegebenen minimalen Transportpfad.

### Legacyparser-Sprengfallen

Die vorhandenen kombinierten Workflow-Durchstiche bleiben Bestandteil der
Slice-3-Matrix und ersetzen die Textnormalisierer, Markerparser und
Contract-Repair-Einstiege durch Fehlerstubs. Planrevision, Slice-Korrektur und
erneuter Finalreview konvergieren dennoch ausschließlich über native Codex-
und Claude-Resultate.

## Corpusbestand

| Klasse | Anzahl | Aussage |
|---|---:|---|
| Native Antworten gefunden/übernommen | 14 / 14 | Vollständiger erfolgreicher Bestand einschließlich AP6A-Finalresultat |
| Kanonische Claude-Requests gefunden | 12 | Vollständiger `manual-*-review/request*.json`-Bestand |
| `request_bound` gefunden/übernommen | 3 / 3 | Writer- und Domänenbeleg |
| `schema_only` | 11 | Digest und allgemeine Leserkompatibilität |
| Technische Failure-Envelopes | 3 | Nur Transportdiagnostik |

## Writerform-Nachweise

| Provider | Writerform | Nachweis | Schema-SHA-256 | Response-SHA-256 |
|---|---|---|---|---|
| Codex | Plan | Live-Canary `accepted` | `860182340b070a22294a6cb805822b97dee1ccad90a61de8e5e2fa5b7f761aae` | `7075c1ddf324d619148ad3595cab810f48738e64ed083446d9c928d36c4062b7` |
| Codex | Implementierung | Live-Canary `accepted` | `0413380b7777b33e635f17fc1b85c73f313e6be9bfba4e9f25bf0034e617c31b` | `6dab290d1f07aad7aaf511b794b0a93d8d97fa88e7ea42fc787cdb306dcde9d6` |
| Codex | Korrektur | Live-Canary `accepted` | `27fdb65cafdb024fb6a128bc40b41a28023866493c30af0ac29021a907863a6c` | `92a17c5da808554fe1c14d38c3156aab6534d790ae87f4666fa086e1a96bd740` |
| Codex | Finalbericht | Live-Canary `accepted` | `9456b7aef9f9f644c0c90f2447f3c714a5f026b0bc46d655da52b47891292ce3` | `ae088a3d28ccad758844b1335752dc1b8284b9ef845b477898575949b6612c66` |
| Claude | Plan | Live-Canary `approved` | `88e268b3d61afd18e86070cd86d8c480e537541e7a4c919b09d3844e0a593083` | `7127bbc70f18a8aba4d2aed81391e4c04f4373a2056b7a0824ae6fbc3ac18d6d` |
| Claude | initialer Slice | `request_bound` Slice-2-Runde 1 | `011f76845ed21f4f17d0090cf960f626f4277e00eb7c946ce562478a56b5c508` | `a3a749ce1829187b4988064106257ec84293c0b2f5c8547a7c7b737992054ebf` |
| Claude | Konvergenz | Live-Canary `approved`, einschließlich `status_changes.items.oneOf` | `a86ebb519fc2dcdf4936b01d00397e46452ba7bc8c2591e1523e021b1bf22e04` | `f6a2eff236b5a5b560657eb44b7ec5c932ca4782861e5857c400e932c965953e` |
| Claude | Finalreview | Live-Canary `approved` | `09049d1bfdc89897c949d2318a7ddad501a3a12cdcd6faf738cd4cdff6f16ffa` | `1c8316db07b57561cacb29e0039e578b4455da4efcac5e945e91e28647601a62` |

Alle erfolgreichen Canaries verwendeten den echten Adapter-/Runtimepfad und
bestätigten bytegleichen `git status --porcelain=v1 --untracked-files=all`
vor und nach dem Aufruf. Der Korrekturstand ergänzt einen separaten rekursiven
Integritätsabdruck für die gitignorierten Bereiche `.orchestrator/`, `inbox/`
und `outbox/`.

## Falsifikationsbefunde während der Canaries

1. Der erste Codex-Plan-Canary erzeugte für den einzigen geplanten Slice die
   numerische ID `03`. Das Writerschema konnte diese ohne providerseitig nicht
   unterstützte positional tuples nicht verhindern; die lokale Bindung hielt
   mit `slice-plan-invalid` fail-closed an. Der vorhandene typisierte
   Ausnahmeeintrag für diesen Fehlercode wurde daraufhin, ohne die
   Ausnahmemenge zu vergrößern, um die konkret nachgewiesene
   1-basierte Kontiguitätsinvariante und einen Regressionstest erweitert. Der
   präzisierte Auftrag verlangte explizit genau einen Slice mit ID `1` und
   bestand.
2. Der erste parallele Claude-Plan-Canary endete in einem technischen
   `is_error=true`-Envelope und erzeugte keine fachliche Entscheidung. Die
   serielle Wiederholung desselben Writerformulars bestand. Der technische
   Versuch zählt nicht als Contract-Nachweis.
3. Ein anfänglicher Codex-Finalversuch verwendete im Probe-Werkzeug den nicht
   registrierten Budgetschlüssel `codex_final_report` und stoppte vor dem
   Provider. Der korrigierte Produktionsschlüssel `codex_final_review` wurde
   anschließend erfolgreich belegt.

## Validierung

- Fokussierte Matrix: Corpus, Differentialnachweis, Adapter, Runtime,
  Workflow und Orchestrator-Runtime grün.
- Vollständige Matrix: `python3 -m pytest tests/ -v` mit `1266 passed` in
  `192.95s`.
- `git diff --check`: `PASS` (nur erwartete LF/CRLF-Hinweise).

## Reviewauftrag

Claude soll Slice 03 adversarial gegen den genehmigten Arbeitsplan und den
vollständigen Diff seit `7ba867d` prüfen. Besonders zu falsifizieren sind:

- Vollständigkeit und korrekte Klassifizierung des Corpus;
- verlustfreie Rekonstruktion der drei Originalrequest-Bindungen;
- tatsächliche Writer-vor-Domäne-Reihenfolge;
- Falsifizierbarkeit der Differentialmatrix und Vollständigkeit der
  Ausnahmelistenprüfung;
- unveränderte Produktionsdefaults der neuen Codex-Boundary;
- sichere, repositoryferne Canaryausführung über denselben Adapter-/Runtimepfad;
- acht und nur acht geschlossene Writerform-Nachweise;
- korrekte Einordnung der fehlgeschlagenen technischen Versuche und des
  initialen `slice-plan-invalid`-Befunds.

## Claude Implementierungsreview Slice 03

Datum: 24. August 2026 · Reviewer: Claude · Modus: read-only

Grundlage: `AGENTS.md`, der freigegebene Arbeitsplan (Slice 3, Abschnitte 3.4,
4.4, 4.5, 8, 10), die freigegebenen Slice-01- und Slice-02-Protokolle,
`git diff 7ba867d` sowie sämtliche untracked Dateien aus `git status --short`.
Die attestierte vollständige Matrix (`1266 passed`) wurde nicht erneut
ausgeführt; es liefen ausschließlich kleine providerfreie Prüfungen gegen den
Arbeitsbaum, ohne Produktcode, Tests oder Arbeitsplan zu verändern.

### Gezielt ausgeführte Zusatzprüfungen

Die Attestierung belegt grüne Tests, aber nicht die Vollständigkeit der
Inventur und nicht, dass die dokumentierten Digests die behaupteten Nachweise
tatsächlich tragen. Providerfrei nachgerechnet wurden deshalb:

1. Unabhängige Neuinventur aller auffindbaren erfolgreichen nativen
   Rohantworten (`.orchestrator/artifacts/*/native-codex-responses/*.json` ohne
   `*.failure.json`, alle `docs/internal/archive/**` mit
   `schema_version: native-agent-*-result-v1`) und aller kanonischen
   Requestdokumente (`.orchestrator/logs/manual-*review*/request*.json`).
2. Bytevergleich jedes gespeicherten `response_text` gegen seine Quelldatei.
3. Digestbindung aller zwölf Requestdokumente und vollständige Paarung
   Request-ID ↔ Antwort über den gesamten Antwortbestand.
4. Rekonstruktion der drei `request_bound`-Kontexte, Ableitung ihrer heutigen
   Writerschemainstanz, Vergleich mit dem Manifestdigest sowie Writer-vor-
   Domäne-Durchlauf.
5. Rebuild der acht Canary-Kontexte aus `scripts/native_contract_probe.py` und
   Vergleich der erzeugten Writerschemadigests gegen die im Manifest und im
   Reviewdokument dokumentierten Canarywerte.

### Was nachweislich geschlossen ist

**Requestbindung und Reihenfolge.** Alle zwölf kanonischen Requestdokumente
verifizieren ihre eigene Digestbindung (`request_id ==
native-review-request-<sha256(canonical ohne request_id)>`). Genau drei
Request-IDs besitzen eine archivierte Antwort, und genau diese drei sind als
`request_bound` übernommen; die neun `manual-native-codex-*`-Requests bleiben
korrekt ungepaart. Es wird keine fehlende Bindung erfunden.
`test_native_contract_corpus_reader_writer_and_domain_results`
(`tests/test_native_contract_corpus.py:166`) validiert nachweislich zuerst
gegen die konkrete Writerschemainstanz (`validate_schema_document` in Zeile
185) und erst danach gegen die gebundene Domänenkonvertierung
(`parse_bound_native_contract_result` in Zeile 186). Alle 13 gespeicherten
Antworttexte sind bytegleich mit ihrer Quelle; es gibt keine stille Redaktion.

**Tragfähigkeit der beiden `request_bound`-Writerform-Nachweise.** Beide sind
echt und nicht formal. Der Nachweis für `initial_slice`
(`manual-package5-slice2-review`, Runde 1, `allow_new_observations=True`)
erzeugt heute die Projektion mit dem Titel `Native Claude slice_initial writer
projection` und Digest `fe6fc2ba…`, exakt dem Manifestwert; die historische
Antwort trägt eine reale neue `C-05 OBSERVATION` in einem freigebenden
Initialreview — also genau den Ast, den Slice 02 über `O-05`/`B-03` geöffnet
hat. Der Nachweis für `convergence`
(`manual-package5-slice1-review`, Runde 2) erzeugt `Native Claude
slice_convergence writer projection` mit Digest `9a3db92e…`, ebenfalls exakt
dem Manifestwert, und seine historische Antwort disponiert mit `C-03 CLOSED`
und `C-04 CLOSED` genau die von Slice 02 eingeführte
Kardinalitätsbindung `len(own_open_ids)` inklusive erzwungenem `CLOSED` für den
eigenen offenen BLOCKER. Die Zuordnung Fixture → Writerform ist damit
sachlich korrekt und nicht vertauscht. Der historische
`response_contract.schema_sha256` ist bei allen drei Paaren `31510b2d…` (das
alte breite Schema) und unterscheidet sich erwartungsgemäß vom heutigen
rekonstruierten Writerdigest — die Prüfung erfolgt also gegen die aus dem
Originalkontext rekonstruierte Instanz, wie Abschnitt 4.5.2 es verlangt.

**Canary-Digests tragen ihre Behauptung.** Der Writerschemadigest hängt
ausschließlich vom gebundenen Kontext ab, nicht vom `base_commit`. Ich habe
alle acht Canary-Kontexte aus `_codex_canary_bundle()` und
`_claude_canary_bundle()` neu gebaut: die sechs als `live_canary` ausgewiesenen
Zeilen reproduzieren ihren dokumentierten `writer_schema_sha256` bitgenau
(`860182…`, `041338…`, `27fdb6…`, `9456b7…`, `88e268…`, `09049d…`). Die
Canarybelege binden damit nachweisbar an genau die Writerform, die sie
beanspruchen. Die beiden `request_bound`-Zeilen weichen erwartungsgemäß von den
synthetischen Canarykontexten ab, weil sie ihren Digest aus dem historischen
Kontext beziehen.

**Ausführungsgrenze.** `NativeCodexExecutionBoundary`
(`src/agent_adapters.py:79-155`) ist typisiert und fail-closed. Der
Produktionsmodus erzwingt in `__post_init__` `execution_root ==
evidence_asset_root == repository_root` und `workspace-write`; jede Abweichung
wirft „production native Codex boundary changed its defaults“. Der Canarymodus
erzwingt `read-only` und lehnt jeden Pfad ab, der gleich `repository_root` oder
`is_relative_to(repository_root)` ist. Beide Aufrufer defaulten auf
`production(...)`, wenn keine Boundary übergeben wird
(`src/agent_adapters.py:481`, `src/agent_runtime.py:1449`) — Produktionsdefaults
sind unverändert. `run_agent` verwendet `execution_root` als `cwd` des
Subprozesses (`src/agent_runtime.py:1087`, `:1188`) und schreibt selbst nichts
in das Repository; `_new_runtime_dir()` liegt in `tempfile.mkdtemp()`. Der
Codex-Canary läuft über `run_native_codex_agent()`, der Claude-Canary über
`run_native_review_agent()` — beide ohne die persistierende
`*_checked`-Variante, sodass keine Rohantwort in den Artefaktspeicher
geschrieben wird. Reviewer-Workspaces liegen ebenfalls im Tempverzeichnis, und
`run_agent` verbietet einen Execution-Root-Override für Reviewer explizit. Der
Canary verwendet damit denselben Adapter- und Runtimepfad wie die Produktion
und baut keine zweite Kommandozeile.

**Präzisierte Provider-Ausnahme.** Der Eintrag `codex-slice-plan-path-order`
wurde erweitert, nicht dupliziert: `exception_id`, `provider`, `operations` und
`error_code` (`slice-plan-invalid`) sind unverändert; nur `local_invariant`,
`missing_schema_feature` und `provider_evidence` benennen jetzt zusätzlich die
1-basierte Kontiguität mit dem realen Canarybefund. Die Ausnahmemenge ist
nachweislich nicht gewachsen (13 Einträge vor und nach dem Diff, keine
hinzugefügte oder entfernte Zeile im JSON-Array). Der benannte Regressionstest
`test_writer_schema_keeps_slice_path_order_fail_closed_locally`
(`tests/test_native_codex_contract.py:452-464`) deckt jetzt **beide** Hälften
je als Doppelschritt „writer-schemavalide, danach lokal `SLICE_PLAN_INVALID`“:
unsortierte `scope_paths` bei `slice_id: 1` und — neu — `slice_id: 3` bei
sortierten Pfaden. Die Legitimität der Erweiterung ist gegeben, weil sie
denselben Fehlercode und dieselbe nachgewiesene Subset-Grenze
(`prefixItems`/Array-`const` von Codex 0.147.0 abgelehnt) betrifft.

**Legacyfreiheit.** Der native Reviewpfad diskriminiert über den Ergebnistyp
(`src/workflow.py:2629`, `isinstance(output, NativeAgentReviewOutput)`) und
erreicht `_validate_or_repair_review()` nicht. Die vorhandenen
Workflow-Durchstiche ersetzen `normalize_codex_contract_output`,
`validate_codex_response`, `normalize_review_contract_output` und
`validate_review_response` durch werfende Stubs
(`tests/test_workflow.py:1203-1214` und vier weitere Stellen). Da der textuelle
Reparaturpfad erst hinter `validate_review_response` erreichbar ist, fängt die
Sprengfalle auch jeden Rückfall auf Contract-Repair ab. Die Absicherung ist
ausreichend.

**Differentialmatrix.** `test_all_eight_writer_forms_accept_their_local_domain_result`
deckt genau die acht in Abschnitt 4.5.8 definierten Formen ab und belegt über
`len(digests) == 8` deren paarweise Verschiedenheit. Die rote Kontrolle
(`tests/test_native_contract_differential.py:262`) ist wirksam: sie entfernt
mit `.pop("pattern")` — ohne Default — genau eine Writerregel, sodass ein
Wegfall derselben Regel im Produktivcode den Test mit `KeyError` rot färbt, und
sie beweist, dass die gelockerte Regel eine writer-schemavalide Antwort erzeugt,
die lokal mit einem **nicht registrierten** Code (`review-content-missing`)
scheitert — exakt die Fehlersignatur, auf die die Matrix ausgelegt ist.
`test_exception_table_is_closed_and_every_entry_names_a_real_regression` prüft
für jeden der 13 Einträge beider Provider die Existenz der benannten
Testfunktion sowie nichtleeren Providerbeleg und Feature.

### BLOCKER

NEW_FINDING: C-26 | BLOCKER | Das Corpus ist gegenüber dem tatsächlich auffindbaren Bestand unvollständig und die Inventurzahl ist in einem grünen Test festgeschrieben. Auffindbar sind 14 erfolgreiche native Rohantworten, übernommen sind 13. Nicht erfasst ist `docs/internal/archive/phase-2-ap6a-semantic-evidence-bootstrap-exception/claude-direct-final-review-result.json` — ein vollständiges natives Claude-Reviewresultat (`schema_version: native-agent-review-result-v1`, `result_type: review_result`, `reviewer: claude`, `decision: approved`, drei `status_changes`, nichtleeres `pre_mortem`, SHA-256 `1322bbb6ec2f7352cdb1c1ccc92f2dfd9195b18bfa97e8b5314fe16fde1dc3ae`), das providerfrei geprüft sauber gegen das unveränderte allgemeine Leseschema validiert. Genau dieses Archiv benennt Abschnitt 3.4 des freigegebenen Arbeitsplans ausdrücklich („Unter `docs/internal/archive/phase-2-ap6a-semantic-evidence-bootstrap-exception/` liegt mindestens ein weiteres vollständiges natives Claude-Finalresultat"), und das Slice-02-Protokoll zählt bereits explizit sechs archivierte native Claude-Rohresultate — im Corpus stehen nur fünf. Verschärfend wirkt, dass `test_native_contract_corpus_manifest_and_digests_are_complete` (`tests/test_native_contract_corpus.py:148-155`) die Inventur nicht neu ableitet, sondern gegen ein hartkodiertes Dictionary mit `native_responses_found: 13` assertet; die Auslassung ist damit in einer dauerhaft grünen Behauptung eingefroren und kann durch keinen Test auffallen. Betroffen sind Slice-3-Arbeitsschritt 1 („Alle im Repository und im aktuellen lokalen Artefaktbestand verfügbaren nativen Rohantworten … inventarisieren"), das Slice-3-Akzeptanzkriterium („Das Corpusmanifest ist vollständig gegenüber allen beim Slice-Start auffindbaren nativen Rohantworten") und Gesamt-Abnahmekriterium 6. Da Slice 3 der Slice ist, der beide Writerverträge einfriert, ist die Vollständigkeit des Einfrierbestands seine tragende Aussage. | Das Manifest führt `docs/internal/archive/phase-2-ap6a-semantic-evidence-bootstrap-exception/claude-direct-final-review-result.json` als bytegetreues `schema_only`-Fixture mit verifiziertem `response_sha256`; die Inventurzahlen lauten `native_responses_found: 14` und `native_responses_adopted: 14`, und die Zahl der `schema_only`-Antworten in Reviewdokument und Corpusbestandstabelle steigt entsprechend von 10 auf 11. Eine Hochstufung zu `request_bound` unterbleibt, weil die `request_id` `native-review-request-ffff…ffff` ein Platzhalter ohne Originalrequest ist. Zusätzlich leitet `test_native_contract_corpus_manifest_and_digests_are_complete` den repositoryversionierten Teil der Inventur aus dem Arbeitsbaum ab, statt ihn zu behaupten: ein Test enumeriert alle Dateien unter `docs/internal/archive/**`, deren JSON `schema_version` mit `native-agent-` beginnt und die ein `result_type` tragen, und verlangt, dass jede davon im Manifest erfasst ist; die aus `.orchestrator/` stammenden Zahlen dürfen weiterhin hartkodiert bleiben, weil dieses Verzeichnis nicht Teil eines frischen Klons ist.

### OBSERVATIONS

NEW_FINDING: C-27 | OBSERVATION | Der Writerform-Nachweistest kann eine fehlattribuierte Nachweiszeile nicht erkennen. `test_writer_form_manifest_has_exactly_eight_closed_evidence_rows` (`tests/test_native_contract_corpus.py:210-211`) prüft für `evidence.kind == "request_bound"` nur `evidence["fixture_id"] in rows`. `rows` enthält aber alle 13 Fixtures einschließlich der zehn `schema_only`-Einträge, sodass ein `schema_only`-Fixture als Writerform-Beleg zitiert werden könnte — genau das, was Abschnitt 4.5.6 („Ein `schema_only`-Fixture ist ausdrücklich kein Writerform-Nachweis") und Gesamt-Abnahmekriterium 8 verbieten und was Stopbedingung 9 auslösen müsste. Zusätzlich wird `writer_forms[i].writer_schema_sha256` nirgends gegen `fixtures[fixture_id].writer_schema_sha256` gebunden, sodass eine Nachweiszeile einen beliebigen 64-Zeichen-Digest führen könnte. Der heutige Datenstand ist korrekt — ich habe beide Kopplungen providerfrei nachgerechnet und beide stimmen —, aber die vom Plan verlangte Eigenschaft ist nicht regressionsgesichert, sondern nur zufällig erfüllt. | Der Test verlangt für jede `request_bound`-Nachweiszeile zusätzlich, dass das zitierte Fixture im Manifest `binding == "request_bound"` trägt und dass sein `writer_schema_sha256` mit dem der Writerformzeile übereinstimmt; ein Negativfall mit einem `schema_only`-Fixture-Identifier beziehungsweise einem abweichenden Digest lässt den Test nachweislich rot werden.

NEW_FINDING: C-28 | OBSERVATION | Die Unversehrtheitsprüfung der Canaries ist für den vom Plan zuerst genannten Schutzbereich strukturell blind. `_live_canary()` (`scripts/native_contract_probe.py`) vergleicht ausschließlich `git status --porcelain=v1 --untracked-files=all` vor und nach dem Aufruf. `.gitignore` listet `.orchestrator/`, `inbox/` und `outbox/`, sodass diese Ausgabe per Konstruktion keine Änderung an `.orchestrator/state.json`, an Checkpoints, an der Recordkette, an Artefakten, an der Inbox oder an der Outbox anzeigen kann — also genau an den Objekten, die das Slice-3-Akzeptanzkriterium zuerst nennt („Canaryausführung verändert weder `.orchestrator/state.json` noch Checkpoints, Recordketten, Inbox, Outbox oder Git-Index. Zusätzlich ist die Ausgabe von `git status …` bytegleich"). Der Nachweis ruht damit vollständig auf der Codeeigenschaft, dass der Canary die nicht persistierende Runtimevariante verwendet. Diese Eigenschaft habe ich verifiziert — `run_native_codex_agent()` und `run_native_review_agent()` schreiben keine Rohantwort, `run_agent()` schreibt nichts in das Repository, `_new_runtime_dir()` und die Reviewer-Workspaces liegen in `tempfile`-Verzeichnissen —, sie ist aber weder dokumentiert noch durch eine Regression gebunden, sodass ein späterer Wechsel auf `run_native_codex_agent_checked()` die Isolationszusage still aufheben würde, ohne dass die Canaryprüfung anschlägt. | Der Canary erfasst zusätzlich zum Git-Status einen rekursiven Pfad-, Größen- und mtime-beziehungsweise-Digestabdruck von `.orchestrator/`, `inbox/` und `outbox/` vor und nach dem Aufruf und bricht bei jeder Abweichung ab; alternativ bindet eine Regression fest, dass der Canarypfad ausschließlich die nicht persistierenden Runtimefunktionen aufruft, und das Reviewdokument hält fest, dass der Git-Status-Vergleich diesen Bereich nicht abdeckt.

### Nicht unmittelbar handlungsbedürftige Restrisiken

- Die sechs `live_canary`-Zeilen können lokal nur über ihren Schemadigest
  verifiziert werden; `response_sha256` und `request_id` sind reine
  Fremdattestierungen ohne gespeicherte Bytes. Das ist der Natur eines
  Live-Canaries geschuldet und durch die reproduzierte Schemabindung so weit
  abgesichert, wie es ohne Fixture möglich ist.
- Die Pflichtmutationsklassen aus Abschnitt 4.4.6 werden nicht in
  `tests/test_native_contract_differential.py` neu implementiert, sondern
  bleiben in den bereits freigegebenen Codex- und Claude-Contracttests. Das ist
  eine vertretbare Lesart, macht die Differentialdatei aber zu einer reinen
  Positiv- und Registerprüfung; wer sie isoliert liest, überschätzt ihre
  Abdeckung.
- `test_exception_table_is_closed_and_every_entry_names_a_real_regression`
  prüft Eindeutigkeit und Feldqualität, aber keinen Bestandsschnappschuss. Ein
  stilles Wachstum der Ausnahmemenge fiele erst über die Mengengleichheitstests
  der Slices 01 und 02 auf.
- Der `convergence`-Nachweiskontext trägt `allow_new_observations=True` bei
  `round_number=2`. Astwahl (`slice_convergence`) und Observationpolicy
  (`observations_allowed`) laufen dort auseinander; Writer- und lokale Schicht
  stimmen jedoch überein, weil beide `allow_new_observations` auswerten. Das
  ist bereits in Slice 02 geprüfte und freigegebene Semantik.

REVIEWER: claude

REVIEW_EVIDENCE: Corpusvollständigkeit gegen unabhängige Neuinventur aller nativen Rohantworten und kanonischen Requestdokumente; Bytefidelität jedes Fixtures gegen seine Quelldatei; Digestbindung und vollständige Request-ID-Paarung; korrekte Trennung von `schema_only`, `request_bound` und technischen Failure-Envelopes; Writer-vor-Domäne-Reihenfolge und sachliche Richtigkeit der beiden `request_bound`-Writerformzuordnungen; Reproduzierbarkeit aller sechs Canary-Schemadigests aus den Probe-Kontexten; Abdeckung und Falsifizierbarkeit der Differentialmatrix und der roten Kontrolle; Vollständigkeit und Regressionsbindung des Ausnahmeregisters sowie Nichtwachstum der Ausnahmemenge; Produktionsdefaults, Fail-closed-Verhalten und Repositoryferne der neuen Codex-Ausführungsgrenze; tatsächliche Adapter-/Runtimegleichheit und Schreibfreiheit des Canarypfads; Wirksamkeit der Legacyparser- und Contract-Repair-Sprengfallen | Die Beweislast des Slices ruht an drei Stellen auf Buchführung statt auf abgeleiteten Prüfungen: die Inventurzahlen sind hartkodiert (C-26), die Writerform-Nachweiszeilen sind nicht typgebunden an ihre Fixtures (C-27), und die Canary-Unversehrtheit wird für `.orchestrator/`, `inbox/` und `outbox/` gar nicht gemessen (C-28). Solange das so bleibt, kann das Corpus formal vollständig und grün wirken, obwohl es einen realen Bestand verfehlt — was es heute nachweislich tut | Jemand legt eine weitere native Rohantwort im Archiv ab oder verschiebt eine bestehende, und weder die hartkodierte Inventur noch ein abgeleiteter Test bemerkt es; das eingefrorene Corpus dokumentiert danach einen Bestand, den es nicht mehr abbildet, und der nächste Vertragsbruch wird gegen einen unvollständigen Referenzbestand geprüft

PRE_MORTEM: In drei Monaten ist der wahrscheinlichste Verlauf, dass das Corpus als „eingefrorener vollständiger Bestand" zitiert wird, obwohl es das AP6A-Finalresultat nie enthielt. Wenn dann eine Writerregel angepasst wird und jemand fragt, ob alle bekannten realen Antwortformen sie noch erfüllen, lautet die Antwort „13 von 13 grün" — und genau die eine nicht erfasste reale Claude-Freigabe, die drei Findingzustände disponiert und aus einem anderen Arbeitspaket stammt, wird nicht mitgeprüft. Die zweite, leisere Variante: der Canarypfad wird auf `run_native_codex_agent_checked()` umgestellt, weil jemand die Rohantwort persistieren will; der Git-Status-Vergleich bleibt bytegleich, weil `.orchestrator/` gitignoriert ist, und der Canary schreibt ab dann unbemerkt in den Artefaktspeicher desselben Repositorys, dessen Unversehrtheit er beweisen soll.

SLICE_APPROVAL: 03 | NO

STATUS: DONE

CLAUDE_SLICE_APPROVAL: NO

## Korrekturstand nach Claude-Review

FINDING_RESPONSE: C-26 | ACCEPTED | Das AP6A-Direktfinalresultat ist als bytegetreues `schema_only`-Fixture mit SHA-256 `1322bbb6ec2f7352cdb1c1ccc92f2dfd9195b18bfa97e8b5314fe16fde1dc3ae` aufgenommen. Manifest und Dokumentation weisen nun 14 von 14 Antworten sowie elf `schema_only`-Fixtures aus. Der Corpus-Test leitet sämtliche versionierten nativen Archivantworten aus `docs/internal/archive/**` ab und verlangt exakte Mengengleichheit mit den manifestierten Archivquellen; nur der nicht klonbare `.orchestrator/`-Anteil bleibt manifestgebunden.

FINDING_RESPONSE: C-27 | ACCEPTED | Requestgebundene Writerformzeilen werden jetzt an ein Manifestfixture mit `binding == request_bound` und an dessen exakten `writer_schema_sha256` gebunden. Zwei Negativkontrollen beweisen, dass sowohl ein `schema_only`-Identifier als auch ein abweichender Digest die Evidenzprüfung rot machen.

FINDING_RESPONSE: C-28 | ACCEPTED | Die Probe berechnet neben `git status` einen rekursiven Pfad-, Typ-, Größen-, mtime- und Inhaltsabdruck von `.orchestrator/`, `inbox/` und `outbox/` und bricht bei jeder Abweichung ab. Eine providerfreie Regression verändert Stateinhalt, Outboxbestand und eine Inbox-/Outbox-Verschiebung und belegt, dass alle drei Klassen erkannt werden.

### Validierung des Korrekturstands

- Fokussiert: `python3 -m pytest tests/test_native_contract_corpus.py tests/test_native_contract_probe.py tests/test_native_contract_differential.py -v` mit `8 passed`.
- Vollständig: `python3 -m pytest tests/ -v` mit `1268 passed` in `194.82s`.
- `git diff --check`: `PASS` (nur erwartete LF/CRLF-Hinweise).

## Claude Korrekturreview Slice 03

Datum: 24. August 2026 · Reviewer: Claude · Modus: read-only

Konvergenzrunde nach `AGENTS.md`: es wird keine neue `OBSERVATION` eingeführt;
nicht unmittelbar handlungsbedürftige Restrisiken stehen ausschließlich in
`REVIEW_EVIDENCE`, ein neu entdeckter handlungsbedürftiger Defekt wäre ein
denyender `BLOCKER` mit der nächsten freien `C-*`-ID. Die attestierten Läufe
(`1268 passed`, fokussiert `8 passed`, `git diff --check` sauber) wurden nicht
erneut ausgeführt. Es liefen ausschließlich kleine providerfreie Prüfungen
gegen den Arbeitsbaum, ohne Produktcode, Tests oder Arbeitsplan zu verändern.

### Geprüfte Dimensionen

Unabhängige Neuinventur des vollständigen nativen Antwortbestands;
Bytefidelität und Digestrichtigkeit jedes Fixtures; Ableitbarkeit der
Archivinventur aus dem realen Arbeitsbaum und Mengengleichheit gegen das
Manifest; korrekte Beibehaltung der `schema_only`-Klassifizierung des
AP6A-Resultats; Typbindung und Digestbindung der Writerform-Nachweiszeilen
einschließlich der Wirksamkeit beider Negativkontrollen; Vollständigkeit,
Determinismus, Feldumfang und Fail-closed-Verhalten des neuen
Integritätsabdrucks für gitignorierte Stores; Abdeckung des realen
`main()`-Canarypfads durch die Regression; Fortbestand des bytegleichen
Git-Status-Vergleichs; Regressionsfreiheit von Writerform-Zuordnung,
Ausnahmeregister, Ausführungsgrenze und Konsistenz zwischen Manifest,
Reviewdokument und Arbeitsplan.

### C-26 — geschlossen

`docs/internal/archive/phase-2-ap6a-semantic-evidence-bootstrap-exception/claude-direct-final-review-result.json`
ist als Fixture `claude-phase-2-ap6a-direct-final-review` aufgenommen. Alle
sieben Teilfragen habe ich providerfrei nachgerechnet:

1. Der gespeicherte `response_text` ist **bytegleich** mit der Quelldatei im
   Arbeitsbaum.
2. `source_path` stimmt exakt, und der Manifestdigest ist identisch mit dem neu
   berechneten Digest sowie mit dem von mir im Erstreview genannten Wert
   `1322bbb6ec2f7352cdb1c1ccc92f2dfd9195b18bfa97e8b5314fe16fde1dc3ae`.
3. `corpus.jsonl` enthält jetzt exakt **14** Zeilen mit 14 eindeutigen
   `fixture_id`-Werten.
4. Das Manifest weist `native_responses_found: 14`,
   `native_responses_adopted: 14` und 14 Fixtures mit der Bindungsverteilung
   `schema_only: 11` / `request_bound: 3` aus; Corpusbestandstabelle und
   Arbeitsplan-Umsetzungsstand nennen übereinstimmend 14 beziehungsweise elf.
5. `_archive_native_response_paths()` (`tests/test_native_contract_corpus.py`)
   leitet die Archivinventur tatsächlich aus dem Arbeitsbaum ab: `rglob("*.json")`
   unter `docs/internal/archive`, JSON-parsebar, Objekt, `schema_version`
   beginnt mit `native-agent-`, nichtleeres `result_type`. Das ist genau die
   Klassifizierung, mit der ich den Fehlbestand im Erstreview gefunden habe.
6. `test_native_contract_corpus_manifest_and_digests_are_complete` verlangt
   `manifested_archive_paths == archive_paths` — exakte Mengengleichheit in
   beide Richtungen. Zusätzlich, und über mein Akzeptanzkriterium hinaus, wird
   `native_responses_found` nicht mehr behauptet, sondern als
   `artifact_fixture_count + len(archive_paths)` abgeleitet, und für jedes
   archivstämmige Fixture wird die Bytegleichheit gegen den Arbeitsbaum
   assertiert.
7. Das AP6A-Resultat bleibt korrekt `schema_only`: die Corpuszeile trägt kein
   `request_document`, und die Antwort-`request_id` ist der Platzhalter
   `native-review-request-ffff…ffff`, für den kein Originalrequest existiert.
   Es wird keine Bindung erfunden.

Meine unabhängige Neuinventur (`.orchestrator/artifacts/*/native-codex-responses/*.json`
ohne `*.failure.json`, plus alle Archivdateien nach obiger Klassifizierung)
findet jetzt 14 Antworten; die Differenz zum Manifest ist in beide Richtungen
leer, und alle 14 sind bytegleich und digestkorrekt sowie gegen ihr jeweiliges
allgemeines Leseschema lesbar.

### C-27 — geschlossen

`_validate_writer_form_evidence()` bindet requestgebundene Nachweiszeilen jetzt
typisiert:

1. `assert fixture["binding"] == "request_bound"` — eine Writerformzeile mit
   `evidence.kind == "request_bound"` darf ausschließlich ein Manifestfixture
   mit ebendieser Bindung referenzieren.
2. `assert fixture["writer_schema_sha256"] == item["writer_schema_sha256"]` —
   der Digest der Writerform muss exakt dem des referenzierten Fixtures
   entsprechen.
3. Beide Negativkontrollen sind wirksam und scheitern aus dem richtigen Grund:
   Beim Austausch gegen einen `schema_only`-Identifier greift die
   Bindungsassertion, bevor der bei `schema_only`-Fixtures gar nicht vorhandene
   Digestschlüssel gelesen wird — es entsteht also eine `AssertionError` und
   kein maskierender `KeyError`. Beim manipulierten Digest greift die
   Digestassertion. Die Kontrollen übergeben bewusst die unveränderten `rows`,
   sodass die zuvor allein wirksame Prüfung `fixture_id in rows` weiterhin
   durchläuft und exakt die von mir gemeldete Lücke exerziert wird.
4. Ein anderer Weg für `schema_only`-Fixtures existiert nicht:
   `evidence["kind"]` ist auf `{"request_bound", "live_canary"}` assertiert, und
   der `live_canary`-Ast referenziert kein Fixture, sondern verlangt
   `status == "passed"`, eine Request-ID und einen 64-stelligen Antwortdigest.

Der Datenstand bleibt korrekt: beide requestgebundenen Zeilen zeigen auf
`request_bound`-Fixtures mit übereinstimmendem Digest, und die sachliche
Zuordnung ist unverändert richtig — `claude/initial_slice` auf die Projektion
mit dem Titel `Native Claude slice_initial writer projection`,
`claude/convergence` auf `Native Claude slice_convergence writer projection`.

### C-28 — geschlossen

`_protected_repository_fingerprint()` (`scripts/native_contract_probe.py`)
schließt die von mir benannte strukturelle Blindheit:

1. Der Abdruck ist rekursiv und deterministisch: `PROTECTED_REPOSITORY_PATHS =
   (".orchestrator", "inbox", "outbox")`, je Wurzel `rglob("*")` nach
   POSIX-Pfad sortiert, kanonisches JSON mit `sort_keys` und abschließendes
   SHA-256. Zwei aufeinanderfolgende Läufe gegen den realen Arbeitsbaum liefern
   denselben Digest. Eine fehlende Wurzel wird als eigener
   `{"kind": "missing"}`-Eintrag erfasst, sodass auch das erstmalige Anlegen
   eines Stores auffällt.
2. Je Eintrag werden Pfad, Typ, Modus, Größe und `mtime_ns` erfasst, für
   Dateien zusätzlich der Inhaltsdigest und für Symlinks das Linkziel. `lstat()`
   verhindert, dass Symlinks verfolgt werden.
3. `main()` bildet `protected_before` vor der Verzweigung und vergleicht den
   Abdruck **sowohl** im Canary-Ast **als auch** im Subset-Sonden-Ast.
4. Beide Vergleiche brechen fail-closed ab
   (`"provider canary changed a protected workflow store"` beziehungsweise
   `"provider probe changed a protected workflow store"`).
5. `test_live_canary_main_fails_when_a_protected_store_changes` belegt nicht nur
   die Hilfsfunktion: Der Test treibt über `sys.argv` den realen
   `probe.main()`-Canarypfad, stubbt `_repository_status` auf einen konstanten
   Wert — womit der Git-Status-Vergleich das Ergebnis nachweislich nicht tragen
   kann — und lässt einen stubbenden `_live_canary` `.orchestrator/state.json`
   verändern. `main()` wirft daraufhin den erwarteten `RuntimeError`. Damit ist
   genau der Nachweis erbracht, den mein Akzeptanzkriterium verlangt hat.
   `test_protected_repository_fingerprint_observes_gitignored_workflow_stores`
   deckt zusätzlich Inhaltsänderung, Neuanlage und Inbox-nach-Outbox-Verschiebung
   ab.
6. Der bestehende bytegleiche Git-Status-Vergleich bleibt erhalten und wird in
   beiden Ästen zuerst ausgewertet; der Abdruck ergänzt ihn, ersetzt ihn nicht.
   Der Report weist beide Zusagen getrennt aus
   (`repository_status_unchanged`, `protected_workflow_stores_unchanged`).

### Regressions- und Konsistenzprüfung

Das Ausnahmeregister ist unverändert bei 13 Einträgen (sieben Codex, sechs
Claude); die Korrekturrunde hat es weder erweitert noch verändert. Der Diff an
`src/agent_adapters.py`, `src/agent_runtime.py`,
`schemas/native-provider-schema-exceptions-v1.json` und
`tests/test_native_codex_contract.py` ist gegenüber meinem Erstreview
unverändert, die Ausführungsgrenze also nicht angefasst. Alle 14 Fixtures
validieren gegen ihr jeweiliges allgemeines Leseschema; die drei
`request_bound`-Paare durchlaufen weiterhin zuerst die konkrete
Writerschemainstanz und danach die gebundene Domänenkonvertierung, alle drei
mit `approval=True` und übereinstimmendem Manifestdigest. Manifest,
Corpusbestandstabelle im Reviewdokument und Umsetzungsstand im Arbeitsplan
nennen konsistent 14 Antworten und elf `schema_only`-Fixtures.

### Neue BLOCKER

Keine.

### Urteil

`C-26`, `C-27` und `C-28` sind auf genau den von mir benannten Pfaden
geschlossen, und zwar durchweg durch abgeleitete Prüfungen statt durch
korrigierte Buchführung: die Archivinventur wird aus dem Arbeitsbaum abgeleitet
und mengengleich gebunden, die Writerform-Nachweise sind typ- und
digestgebunden mit zwei wirksamen Negativkontrollen, und die Canary-Isolation
wird für die gitignorierten Stores erstmals tatsächlich gemessen statt
angenommen. Der im Erstreview gemeldete reale Fehlbestand ist beseitigt und
kann durch dieselbe Klasse von Auslassung nicht erneut unbemerkt entstehen. Es
entsteht kein neuer Blocker und keine neue unregistrierte Ablehnungsfläche.

REVIEWER: claude

FINDING_STATUS: C-26 | CLOSED | Das AP6A-Direktfinalresultat ist bytegleich als `schema_only`-Fixture `claude-phase-2-ap6a-direct-final-review` aufgenommen; Sourcepath und SHA-256 `1322bbb6ec2f7352cdb1c1ccc92f2dfd9195b18bfa97e8b5314fe16fde1dc3ae` stimmen, `corpus.jsonl` führt exakt 14 Fixtures, das Manifest 14 gefundene und 14 übernommene Antworten bei elf `schema_only`-Bindungen. `_archive_native_response_paths()` leitet alle versionierten nativen JSON-Antworten unter `docs/internal/archive/**` aus dem realen Arbeitsbaum ab, und der Test verlangt exakte Mengengleichheit gegen die manifestierten Archivquellen sowie Bytegleichheit jedes archivstämmigen Fixtures; `native_responses_found` ist nicht mehr hartkodiert, sondern abgeleitet. Das AP6A-Resultat bleibt korrekt `schema_only`, weil seine Platzhalter-Request-ID `ffff…ffff` keinen Originalrequest bindet und die Corpuszeile kein `request_document` trägt. Meine unabhängige Neuinventur findet 14 Antworten mit leerer Differenz in beide Richtungen.

FINDING_STATUS: C-27 | CLOSED | `_validate_writer_form_evidence()` bindet jede Writerformzeile mit `evidence.kind == "request_bound"` jetzt an ein Manifestfixture mit `binding == "request_bound"` und erzwingt die exakte Gleichheit von `writer_schema_sha256` zwischen Writerformzeile und referenziertem Fixture. Beide Negativkontrollen sind wirksam und scheitern aus dem richtigen Grund: der `schema_only`-Identifier bricht an der Bindungsassertion, bevor der dort fehlende Digestschlüssel gelesen würde, der manipulierte Digest an der Digestassertion; beide übergeben bewusst die unveränderten `rows`, sodass die zuvor allein wirksame `fixture_id in rows`-Prüfung durchläuft und genau die gemeldete Lücke exerziert wird. Ein anderer Weg für `schema_only`-Fixtures existiert nicht, da `evidence.kind` auf `{"request_bound", "live_canary"}` beschränkt ist und der Canary-Ast kein Fixture referenziert.

FINDING_STATUS: C-28 | CLOSED | `_protected_repository_fingerprint()` erzeugt einen rekursiven, deterministischen Abdruck von `.orchestrator/`, `inbox/` und `outbox/` mit Pfad, Typ, Modus, Größe, `mtime_ns`, Inhaltsdigest je Datei und Linkziel je Symlink; fehlende Wurzeln werden als eigener Eintrag erfasst und `lstat()` verhindert Symlinkverfolgung. `main()` vergleicht den Abdruck vor und nach dem Aufruf sowohl im Canary- als auch im Subset-Sonden-Ast und bricht bei Abweichung fail-closed mit `RuntimeError` ab. `test_live_canary_main_fails_when_a_protected_store_changes` belegt den realen `main()`-Canarypfad, nicht nur die Hilfsfunktion: es stubbt `_repository_status` konstant — der Git-Status-Vergleich kann das Ergebnis also nicht tragen — und weist nach, dass eine Mutation an `.orchestrator/state.json` abgelehnt wird. Der bytegleiche Git-Status-Vergleich bleibt zusätzlich erhalten und wird zuerst ausgewertet.

REVIEW_EVIDENCE: Unabhängige Neuinventur des vollständigen nativen Antwortbestands mit leerer Differenz in beide Richtungen; Bytefidelität und Digestrichtigkeit aller 14 Fixtures; Ableitbarkeit der Archivinventur aus dem Arbeitsbaum und exakte Mengengleichheit; Beibehaltung der `schema_only`-Klassifizierung ohne erfundene Requestbindung; Typ- und Digestbindung der Writerform-Nachweiszeilen samt Wirksamkeit und korrekter Fehlerursache beider Negativkontrollen; Determinismus, Feldumfang, Vollständigkeit und Fail-closed-Verhalten des Integritätsabdrucks in beiden `main()`-Ästen; Abdeckung des realen Canary-Hauptpfads durch die Regression bei neutralisiertem Git-Status-Vergleich; Fortbestand von Writerform-Zuordnung, Ausnahmeregister (unverändert 13 Einträge), Ausführungsgrenze und Writer-vor-Domäne-Reihenfolge; Konsistenz von Manifest, Reviewdokument und Arbeitsplan | Der `.orchestrator/`-stämmige Teil der Inventur bleibt naturgemäß nicht aus einem frischen Klon ableitbar und wird als Anzahl aus dem Manifest selbst gezählt; das Entfernen eines dieser acht Codex-Fixtures aus Manifest und `corpus.jsonl` zugleich bliebe damit rechnerisch konsistent und grün, während die Bytes selbst nicht mehr rekonstruierbar sind. Zweitens bleiben die sechs `live_canary`-Zeilen Fremdattestierungen, deren `response_sha256` mangels gespeicherter Bytes lokal nicht prüfbar ist — verifizierbar ist nur ihr aus den Probe-Kontexten reproduzierbarer Schemadigest | Jemand entfernt oder ersetzt ein `.orchestrator`-stämmiges Fixture im eingefrorenen Corpus, und weil dessen Quelle nicht klonbar ist und die Zahl aus dem Manifest selbst stammt, bleibt die Matrix grün, während der eingefrorene Referenzbestand still schrumpft; die nächste Writerregeländerung wird dann gegen weniger reale Antwortformen geprüft, als das Dokument behauptet

PRE_MORTEM: In drei Monaten ist der wahrscheinlichste Verlauf, dass der Integritätsabdruck an seiner eigenen Laufzeit scheitert: er hasht den vollständigen `.orchestrator/`-Artefaktspeicher und braucht dafür auf dem heutigen Stand bereits rund 35 Sekunden je Durchlauf, also etwa 70 Sekunden Zusatzaufwand pro Canary — mit wachsendem Speicher steigt das linear. Wer dann Canaries in Serie ausführt, wird versucht sein, den Abdruck zu überspringen, auf ein Sampling zu reduzieren oder `PROTECTED_REPOSITORY_PATHS` zu kürzen; damit fiele genau die Prüfung weg, die `git status` konstruktionsbedingt nicht leisten kann, und die Isolationszusage ruhte wieder allein auf der nicht regressionsgebundenen Eigenschaft, dass der Canarypfad die nicht persistierenden Runtimefunktionen verwendet. Die zweite, leisere Variante: ein `.orchestrator`-stämmiges Fixture verschwindet aus dem eingefrorenen Corpus, und weil seine Quelle nicht klonbar und seine Anzahl selbstreferenziell ist, bemerkt es keine Prüfung.

SLICE_APPROVAL: 03 | YES

STATUS: DONE

CLAUDE_CORRECTION_REVIEW: YES

## Nachtrag aus Korrekturslice 04

Der unabhängige Gesamtcheck nach Slice 03 hat die Evidenzaussage präzisiert,
ohne Claudes vorstehendes historisches Slice-03-Urteil umzuschreiben:

- Der freigegebene Arbeitsplan lässt ein verifiziertes `request_bound`-Paar
  und einen Live-Canary als gleichberechtigte Writerformbelege zu. Die drei
  historischen Paare bleiben daher unverändert gültige Corpus-,
  Kompatibilitäts- und Domänennachweise; ihr alter ursprünglicher
  Provider-Schemadigest ist kein nachträglicher Implementierungsdefekt.
- Die historische Konvergenzantwort hatte jedoch nicht belegt, dass Claude die
  tiefere aktuelle Komposition `status_changes.items.oneOf` als
  Providervertrag akzeptiert. Der direkte Konvergenz-Canary aus Slice 04 hat
  genau diese Lücke geschlossen.
- Canaryresultat: `approved`; Request-ID
  `native-review-request-58e98da53bd078faa2e2e666d9a1549a51435c683709040dab0aa2d26a11d236`;
  Writerschema-SHA-256
  `a86ebb519fc2dcdf4936b01d00397e46452ba7bc8c2591e1523e021b1bf22e04`;
  Antwort-SHA-256
  `f6a2eff236b5a5b560657eb44b7ec5c932ca4782861e5857c400e932c965953e`.
- Git-Status und rekursiver Abdruck der geschützten Workflowstores waren vor
  und nach dem Canary bytegleich. Repositorypfade waren nicht schreibbar.

Die Writerformtabelle am Anfang dieses Dokuments zeigt den daraus folgenden
aktuellen Evidenzstand: sieben Live-Canaries und ein `request_bound`-Beleg für
den initialen Claude-Slice.
