# Phase 2 – Native Agent Contract Closure

TARGET_BRANCH: feature/native-agent-contract-closure

BASE_COMMIT: c410eef95164d10b1ff0d97d2a9593ca5af18324

STATUS: CLAUDE_PLAN_APPROVED

## 1. Auftrag und Ausführungsvertrag

Dieses Arbeitspaket setzt ausschließlich den Abschnitt **Contract Closure** aus
`/tmp/orchestrator-stabilisierung-1-1-nachtraege.md` um. Gegenstand ist die
Vertragsgeschlossenheit der bereits vorhandenen nativen JSON-Schnittstellen
für Codex und Claude. Die ebenfalls in der temporären Nachtragsdatei genannte
menschenlesbare Live-Projektion ist nicht Bestandteil dieses Pakets.

Die Entwicklung erfolgt direkt im Entwicklungsdialog und ausdrücklich nicht
über `run_task`, `run_task --watch` oder einen anderen Bootstrap-Workflow. Der
Orchestrator darf seinen noch zu schließenden Providervertrag nicht zugleich
als Entwicklungsdriver ausführen und verändern.

Der verbindliche Ablauf lautet:

1. Dieser repository-grounded Arbeitsplan wird von Claude mit Sonnet und
   Effort `high` direkt und read-only geprüft.
2. Blocker des Planreviews werden vor der Implementierung in diesem Dokument
   korrigiert und erneut vorgelegt. Observations werden entweder einem Slice
   zugeordnet oder mit begründeter späterer Disposition dokumentiert.
3. Nach Planfreigabe werden die Slices strikt in ihrer Reihenfolge umgesetzt.
4. Nach jedem Slice laufen fokussierte lokale Tests und `git diff --check`.
   Anschließend prüft Claude den vollständigen Slice-Diff read-only gegen
   Plan, Akzeptanzkriterien und frühere eigene Findings.
5. Ein Slice mit offenem Claude-Blocker wird korrigiert und erneut geprüft.
   Erst eine positive Claude-Entscheidung gibt den nächsten Slice frei.
6. Nach allen Slice-Freigaben führt Codex ein adversariales Gesamtreview über
   den vollständigen Branch-Diff durch. Ein zusätzlicher Claude-Finalreview
   kann erfolgen, ist für dieses manuell geführte Paket aber nicht
   Voraussetzung, sofern Kontingent oder Laufzeit dagegen sprechen.
7. Nach Änderungen an Orchestrierung, Prompt, Adapter, Schema, Runtime oder
   State läuft abschließend `python3 -m pytest tests/ -v`.

Antigravity wird in diesem Arbeitspaket weder als Entwicklungsdriver noch als
Reviewer aufgerufen. Die bestehende Antigravity-Integration wird nicht
verändert. Die separat dokumentierte Option einer laufgebundenen Claude-only-
Reviewpolicy ist ebenfalls nicht Bestandteil dieses Pakets.

## 2. Problemdefinition

Die nativen Codex- und Claude-Pfade liefern bereits JSON, binden Requests an
kanonische Digests und prüfen Ergebnisse lokal gegen geschlossene
Domänenmodelle. Das an den Provider übergebene Schema bildet den konkreten
gebundenen Domänenvertrag jedoch noch nicht vollständig ab.

Ein Resultat kann deshalb provider-schema-valide sein und anschließend lokal
an einer Regel scheitern, die für genau diesen Request bereits im
Provider-Schema ausdrückbar gewesen wäre. Die beobachteten Folgen sind teure
Providerwiederholungen, formal erfolgreiche aber fachlich unbrauchbare
Antworten und schwer verständliche Abbrüche nach dem Providerlauf.

Contract Closure verfolgt die Invariante:

> Jede durch das konkrete Provider-Schema darstellbare Antwort bildet entweder
> erfolgreich den gebundenen lokalen Domänenvertrag oder scheitert
> ausschließlich an einer explizit benannten, im tatsächlich unterstützten
> Provider-Subset nicht sicher ausdrückbaren lokalen Invariante.

Die lokale Domänenvalidierung bleibt immer als zweite, fail-closed
Schutzschicht bestehen. Das Provider-Schema ersetzt weder Recordbindung noch
Replay-, Fingerprint-, Eigentums- oder Persistenzprüfungen.

## 3. Repositorybefund

### 3.1 Nativer Codex-Pfad

- `native_codex_provider_response_schema()` erzeugt derzeit eine allgemeine
  Union aus `plan_result`, `implementation_result`, `correction_result`,
  `final_report_result` und `stop_result`.
- Das Schema kennt beim Provideraufruf nicht die konkrete
  `NativeCodexRequestKind`. Eine Plananforderung kann deshalb schema-seitig
  auch eine Implementierungs- oder Finalantwort darstellen; erst
  `parse_native_codex_response()` meldet `RESULT_KIND_MISMATCH`.
- Die konkreten offenen Finding-IDs werden im Request transportiert, im
  Provider-Schema aber nur durch das allgemeine Muster `C-*|A-*` begrenzt.
  Vollständigkeit und Referenzgültigkeit der `finding_dispositions` prüft erst
  `_apply_dispositions()`.
- Der Response-Schema-Digest fließt bereits in den kanonischen Codex-Request
  ein. `NativeCodexRequestBundle` verifiziert diesen Digest jedoch nicht gegen
  ein aus seinem gebundenen Kontext erneut abgeleitetes exaktes Schema.
- `NativeCodexAdapter.prepare_native_provider_input()` berechnet das allgemeine
  Schema unabhängig vom Bundle neu. Damit ist heute nicht typseitig
  erzwungen, dass Requestdokument, Bundle-Selbstprüfung und tatsächlich an die
  Codex-CLI übergebenes Schema identisch sind.
- Der persistierte v1-Resultatvertrag muss historische Antworten weiterhin
  lesen. Die stärkere Writergrenze gehört daher in eine request-spezifische
  Providerprojektion und darf das allgemeine Leseschema nicht rückwirkend
  verengen.

### 3.2 Nativer Claude-Pfad

- `native_review_provider_response_schema()` spezialisiert das allgemeine
  Reviewresultat derzeit auf Reviewerpräfix und Anchor-Verfügbarkeit.
- Plan-, Slice- und Finalreview erhalten dieselbe große Resultatform. Nicht
  anwendbare Ereignisse bleiben modellseitig darstellbar und werden erst
  lokal abgewiesen.
- Bekannte eigene Finding-IDs, nächste neue Finding-ID, offene Blocker,
  erforderliche Statusdispositionen und erlaubte Reklassifizierungen stehen
  im Request, begrenzen das Provider-Schema aber noch nicht exakt.
- `allow_new_observations=False` und die Finalreviewregel verbieten neue oder
  neu reklassifizierte Observations erst in `_validate_decision()`.
- Eine positive Entscheidung verlangt lokal eine vollständige PASS-
  Attestierung, Testfreigabe, ein inhaltliches Pre-Mortem, vollständige eigene
  Findingzustände und keine offenen eigenen Blocker. Das allgemeine
  Provider-Schema verlangt davon nur Felder, aber nicht die konkrete
  entscheidungsabhängige Semantik.
- Anchors werden bereits ohne `anchor_origin` mit `maxItems=0` verhindert. Das
  ist das vorhandene Muster für weitere request-spezifische Schließungen.
- `NativeReviewRequestBundle` prüft bereits, dass `response_contract` und das
  aus dem Kontext abgeleitete Provider-Schema denselben Digest besitzen. Diese
  Invariante ist zu erhalten und auf den vollständigen Kontext auszudehnen.

### 3.3 Provider-Subset und nicht schema-ausdrückbare Regeln

- Die lokale Schemaimplementierung akzeptiert Draft 2020-12. Codex Structured
  Outputs und Claudes `--json-schema` unterstützen jeweils nur Teilmengen.
- OpenAI lehnt unter anderem Regex-Lookarounds und `uniqueItems` ab. Die
  Codex-Projektion entfernt diese Keywords bereits und wiederholt die
  betreffenden Regeln lokal.
- Der bestehende Repositorystand belegt noch nicht empirisch, ob beide
  Provider die für Contract Closure benötigten positionsgebundenen Tupel,
  `const`-Listen, `minItems`/`maxItems` und verschachtelten `oneOf`-Äste
  akzeptieren. Diese Kenntnis darf nicht erst nach der Implementierung beider
  Writerpfade gewonnen werden.
- Slice 1 beginnt deshalb mit einer direkten, ungefährlichen Subset-Sonde für
  Codex und Claude. Deren Ergebnis wird in
  `schemas/native-provider-schema-capabilities-v1.json` typisiert und
  versioniert festgehalten. Die Writerprojektionen dürfen ausschließlich die
  dort für den jeweiligen Provider positiv nachgewiesenen Konstruktionen
  verwenden.
- Ein exakter `request_id`-Const im Response-Schema würde einen zyklischen
  Digest erzeugen, weil der Schema-Digest selbst Bestandteil des Requests und
  damit der Request-ID ist. Die exakte Request-ID-Gleichheit bleibt deshalb
  bewusst eine dokumentierte lokale Bindungsinvariante.
- Jede weitere lokal verbleibende Regel muss mit Fehlercode, Begründung und
  Provider-Subset-Grenze in einer kleinen typisierten Ausnahmeliste benannt
  sein. Diese Liste entsteht bereits in Slice 1 als
  `schemas/native-provider-schema-exceptions-v1.json`, wird in Slice 2
  erweitert und in Slice 3 nur noch verifiziert. Eine pauschale Aussage „wird
  lokal geprüft“ oder eine Ausnahme ohne reproduzierbaren Providerbeleg und
  Differentialtest genügt nicht.

### 3.4 Verfügbares Antwortcorpus

- Unter `docs/internal/archive/native-codex-claude-correction-loop/` liegen
  mehrere vollständige rohe Claude-Reviewantworten.
- Unter
  `docs/internal/archive/phase-2-ap6a-semantic-evidence-bootstrap-exception/`
  liegt mindestens ein weiteres vollständiges natives Claude-Finalresultat.
- Unter `.orchestrator/artifacts/*/native-codex-responses/` liegen verfügbare
  Codex-Plan-, Implementierungs- und Korrekturantworten sowie technische
  Fehlerhüllen. Da `.orchestrator` nicht Teil eines frischen Klons ist, müssen
  relevante Rohbytes für reproduzierbare Tests als unveränderliche, manifest-
  und digestgebundene Testfixtures in das Repository übernommen werden.
- Eine native Codex-Finalantwort ist im derzeit auffindbaren Corpus nicht
  vorhanden. Die neue Final-Writerform benötigt daher zwingend einen eigenen
  direkten Live-Canary; sie darf nicht aus benachbarten Resultatarten als
  abgedeckt abgeleitet werden.
- Reviewrecords enthalten Request-ID und Response-Digest, aber nicht in jedem
  Fall die vollständigen Providerrohbytes. Nur tatsächlich verfügbare und
  verifizierbare Antworten werden Corpusfixture; fehlende Bytes werden nicht
  rekonstruiert oder erfunden.
- Unter `.orchestrator/logs/manual-*-review/request*.json` liegen vollständige
  kanonische Requestdokumente. Mindestens drei davon lassen sich über ihre
  `request_id` exakt mit versionierten Claude-Antwortbytes unter
  `docs/internal/archive/native-codex-claude-correction-loop/` paaren:
  `manual-package5-slice1-review/request.json` mit
  `native-codex-claude-correction-loop-slice1-review-round2.raw.json`,
  `manual-package5-slice2-c05-review/request.json` mit
  `native-codex-claude-correction-loop-slice2-c05-review.raw.json` sowie
  `manual-package5-slice2-review/request.json` mit
  `native-codex-claude-correction-loop-slice2-review.raw.json`. Slice 3 muss
  alle beim Slice-Start
  auffindbaren Requestdokumente inventarisieren, ihre Digestbindung prüfen und
  jedes vollständig belegte Paar als `request_bound` übernehmen.
- Antwortbytes ohne ein originales, digestverifiziertes Requestdokument sind
  nur `schema_only`: Sie belegen Byte-Digest und Lesbarkeit gegen das
  allgemeine Resultat-v1-Schema, aber weder gebundene Domänenkonvertierung noch
  einen lokalen Diagnosecode. Synthetische oder bloß vermutete historische
  Kontexte sind verboten.

## 4. Zielarchitektur und verbindliche Invarianten

### 4.1 Allgemeines Leseschema und konkretes Writerschema

1. Die versionierten Dateien
   `native-agent-codex-result-v1.schema.json` und
   `native-agent-review-result-v1.schema.json` bleiben das historische,
   providerunabhängige Leseschema.
2. Jeder neue Provideraufruf erhält eine aus seinem unveränderlichen Kontext
   erzeugte, kleinere Writerschema-Projektion.
3. Die Writerschema-Projektion ist eine reine, deterministische Funktion. Sie
   mutiert niemals das geladene allgemeine Schema und liefert bei identischem
   Kontext bytegleiches kanonisches JSON.
4. Requestdokument, persistierter `response_contract`, Bundle-Selbstprüfung,
   Providerinput-Messung und tatsächlich übergebenes Schema verwenden exakt
   dieselben kanonischen Bytes und denselben SHA-256-Digest.
5. Historische Antworten werden weiterhin ausschließlich mit dem allgemeinen
   Leseschema und ihrer gebundenen lokalen Semantik gelesen. Eine neue
   Writerschemaregel macht keinen historischen Record rückwirkend ungültig.
6. Beide Providerverträge behalten exakt eine geschlossene Transporthülle:
   Das einzige Top-Level-Feld ist `result`, also `{"result": <resultat>}`.
   Adapter und lokale Parser akzeptieren keine zweite oder alternative
   Hüllenform.
7. Die beiden Resultat-v1-Schemadateien sind in diesem Arbeitspaket
   unveränderliche Reader-Baselines und gehören ausdrücklich nicht zum
   Änderungspfad. Mehrfache Projektionen mit unterschiedlichen Kontexten
   müssen ihre kanonischen Bytes unverändert lassen.
8. Das empirisch bestätigte Providersubset und die begründeten lokalen
   Ausnahmen sind versionierte, maschinenlesbare Eingaben der Projektion. Eine
   Projektion darf kein nicht bestätigtes Schemafeature verwenden und keine
   Ausnahme implizit erfinden. Capability-Einträge sind zusätzlich an
   Providerbinärname, gemeldete CLI-Version, normalisiertes Transportprofil und
   Sondendatum gebunden. Das Transportprofil ist ein kanonisches JSON-Objekt
   mit genau `provider`, `binary_name`, `model`, `reasoning_or_effort`,
   `schema_transport` und `semantic_flags`. `schema_transport` bezeichnet den
   Mechanismus (`output-schema-file` für Codex, `json-schema-argument` für
   Claude), nicht den laufspezifischen Schemawert. Die kanonische
   `semantic_flags`-Liste ist abschließend:
   - Codex: `exec`, `--skip-git-repo-check`, `--ephemeral`, `--color=never`,
     `--json`, `--output-last-message=<runtime-file>` und `stdin=-`;
   - Claude: `-p`, `--output-format=json`, `--tools=Read`,
     `--allowedTools=Read`,
     `--disallowedTools=Bash,Edit,Write,NotebookEdit,Grep,Glob`,
     `--permission-mode=dontAsk`, `--setting-sources=user`, `--safe-mode`,
     `--strict-mcp-config`, `--prompt-suggestions=false`,
     `--no-session-persistence`, `--disable-slash-commands` und, falls
     konfiguriert, `--max-budget-usd=<configured-value>`.
   Modell- und Effortflags sowie das Schemaflag werden ausschließlich in ihren
   eigenen Profilfeldern repräsentiert und nicht doppelt in `semantic_flags`
   geführt. Ausdrücklich ausgeschlossen sind Codex-Sandboxmodus,
   Arbeitsverzeichnis, `--add-dir`, alle temporären Schema-/Last-Message-/
   Manifestpfade sowie Prompt-, Policy- und Directive-Inhalte. Pfadtragende
   Mechanismen werden nur durch ihren stabilen Flag-Namen mit dem genannten
   typisierten Platzhalter normalisiert. Jedes nicht klassifizierte neue
   Kommandoargument ist Profil-Drift und wird fail-closed abgewiesen, statt
   stillschweigend entfernt zu werden. Ein Versions- oder Profilunterschied
   erzwingt fail-closed eine neue Sonde.

### 4.2 Request-spezifischer Codex-Vertrag

1. Die Writerschema-Union enthält nur den zur konkreten
   `NativeCodexRequestKind` gehörenden Ergebnistyp und `stop_result`.
2. `finding_dispositions` erlaubt ausschließlich die im Kontext offenen
   Finding-IDs. Sind keine Findings offen, ist nur eine leere Liste
   darstellbar.
3. Jeder offene Findingbezug muss im neu erzeugten Resultat genau einmal und
   in kanonischer Reihenfolge erscheinen. Die lokale Ownership- und
   `apply_finding_response()`-Prüfung bleibt bestehen.
4. Vertragsabhängige Testdateiregeln werden soweit im Provider-Subset sicher
   möglich abgebildet: nicht erwartete Testdateien sind nicht darstellbar,
   eine exakt gebundene erwartete Liste wird als solche projiziert.
5. Ergebnisfelder, die für den konkreten Schritt nicht gelten, werden dem
   Provider nicht angeboten.
6. `stop_result` bleibt als eigener geschlossener Ast verfügbar. Stop und
   Readinessresultat können nicht gleichzeitig dargestellt werden.
7. Jeder Ergebniswert, der allein durch den unveränderlichen Requestkontext
   bereits entschieden ist, wird als `const` projiziert. Insbesondere gilt:
   `ready=false`, wenn gebundene erwartete Teständerungen nicht durch
   `test_changes_approved` freigegeben sind; und bei einem Planvertrag mit
   `require_slice_plan=False` ist ausschließlich `stop_result` darstellbar.
8. Die Projektion enthält keine bloß lokal nachgelagerte Alternative für
   einen bereits kontextseitig ausgeschlossenen Ergebniswert. Die lokale
   Prüfung bleibt als zweite Schutzschicht erhalten, darf aber nicht der erste
   Ort dieser Ablehnung sein.

### 4.3 Request-spezifischer Claude-Vertrag

1. Plan-, Slice-, Korrektur- und Finalreview verwenden getrennte
   Writerschemaformen. Primärer Astdiskriminator ist ausschließlich das im
   `NativeReviewContext` gebundene, typisierte `approval_marker`; innerhalb
   eines Slice-Reviews unterscheiden `allow_new_observations`, `round_number`
   und `anchor_origin` die Initial-, Konvergenz- und Anchorpolicy. Weder der nur
   im äußeren Requestspec vorhandene `NativeReviewKind` noch der freie String
   `operation` darf einen Schemaast auswählen. Beide bleiben audit- und
   Konsistenzdaten außerhalb der Projektionsentscheidung.
2. Neue Finding-IDs beginnen exakt bei `next_finding_id`, gehören dem Reviewer
   und bleiben innerhalb des pro Antwort erlaubten kontiguierlichen Fensters.
3. Statusänderungen und Reklassifizierungen dürfen nur bekannte,
   reviewer-eigene Finding-IDs referenzieren. Fremde oder geschlossene
   Finding-IDs werden nicht angeboten.
4. Eine positive Entscheidung enthält die für diesen Request erforderlichen
   eigenen Findingdispositionen vollständig. Eigene offene Blocker müssen
   geschlossen sein; ein Finalreview lässt kein eigenes Finding offen.
5. Ein positiver Reviewast verlangt ein inhaltlich nichtleeres Pre-Mortem und
   die erforderliche Reviewevidenz. Ein Stop- oder Denial-Ast kann diese
   Freigabefelder nicht als Ersatzentscheidung missbrauchen.
6. Neue Observations sind nur in Runden darstellbar, in denen
   `allow_new_observations=True` und keine Finalfreigabe vorliegt. Korrektur-
   und Finalrunden erlauben als neuen handlungsbedürftigen Defekt nur
   `BLOCKER` zusammen mit `decision=denied`.
7. Ohne `anchor_origin` bleibt die Anchorliste zwingend leer. Mit Origin
   bleiben nur geschlossene, sichere Anchordatensätze darstellbar; die Origin
   selbst stammt weiter ausschließlich aus dem Requestkontext.
8. Eine Ablehnung muss im resultierenden Zustand mindestens einen offenen
   eigenen Blocker besitzen. Soweit diese Abhängigkeit nicht ohne unsichere
   kombinatorische Schemaexplosion im Claude-Subset ausdrückbar ist, bleibt
   sie als explizit dokumentierte lokale Invariante erhalten.
9. Ist eine Freigabe aufgrund einer fehlenden, unvollständigen oder
   fehlgeschlagenen `validation_attestation` oder aufgrund nicht freigegebener
   Teständerungen bereits unmöglich, wird `decision` auf `denied` festgelegt.
   Der Provider erhält keinen darstellbaren, erst lokal scheiternden
   Freigabeast.
10. Jede Auswahl nach Nummer 9 wird aus dem gebundenen typisierten Kontext
    abgeleitet und durch einen Negativtest abgesichert, der die gegenteilige
    Entscheidung bereits am Writerschema scheitern lässt.

### 4.4 Differentialvertrag

1. Für jeden repräsentativen gebundenen Kontext erzeugt der Test eine Menge
   gültiger und gezielt mutierter Antworten.
2. Jede Antwort wird zuerst gegen genau die Providerprojektion und danach
   gegen Parser plus Domänenkonvertierung geprüft.
3. Provider-schema-valide Antworten dürfen lokal nur mit einem in der
   Ausnahmeliste registrierten Fehlercode scheitern.
4. Jede Ausnahme benennt Provider, Operation, lokale Invariante, nicht
   unterstütztes Schemafeature und einen Regressionstest.
5. Wird eine Ausnahme später schema-seitig ausdrückbar, muss ihr Test zuerst
   rot werden und die Ausnahme anschließend entfernt werden.
6. Die Pflichtmutationsklassen sind abschließend:
   - falscher Resultattyp;
   - fehlende, doppelte, fremde und nicht offene Findingdisposition;
   - Unter- und Überschreitung des erlaubten Finding-ID-Fensters;
   - Anchor ohne `anchor_origin`;
   - neue oder neu reklassifizierte Observation in Korrektur- und Finalrunde;
   - Freigabe ohne inhaltliches Pre-Mortem oder ohne erforderliche Evidenz;
   - Freigabe mit offenem reviewer-eigenem Blocker;
   - Ablehnung ohne offenen reviewer-eigenen Blocker;
   - Stopresultat mit Freigabe- oder Findingfeldern;
   - eine nach Abschnitt 4.2 oder 4.3 kontextseitig ausgeschlossene
     `ready`- beziehungsweise Freigabeentscheidung.
7. Jede Pflichtklasse besitzt mindestens einen Fall je betroffener Requestart.
   Eine Negativkontrolle lockert gezielt genau eine Writerregel und beweist,
   dass die Differentialmatrix dadurch rot wird.
8. Eine Ausnahme ist nur zulässig, wenn die Subset-Sonde das benötigte Feature
   für den konkreten Provider reproduzierbar ablehnt und ein benannter Test
   die lokale Restinvariante abdeckt. Die Ausnahmeliste darf ohne erneutes
   Planreview nicht wachsen.

### 4.5 Corpus- und Canaryvertrag

1. Jede verfügbare native Rohantwort wird mit Quellnachweis, SHA-256,
   Provider, Operation und erwartetem Ergebnis in einem Corpusmanifest
   registriert.
2. Historische `schema_only`-Resultate bleiben mit dem allgemeinen Leseschema
   lesbar und behalten ihren Byte-Digest. Gebundene Domänen-Lesergebnisse und
   stabile sachlich richtige Diagnosecodes werden ausschließlich für
   `request_bound`-Fixturepaare behauptet. Jedes `request_bound`-Paar wird
   zusätzlich gegen genau die kanonische Writerschemainstanz validiert, die
   aus seinem originalen gebundenen Requestkontext rekonstruiert wird. Nur bei
   erfolgreicher Writer- und Domänenvalidierung darf es als Writerform-
   Nachweis gelten.
3. Corpusfixtures werden bytegetreu gespeichert; Secrets, absolute temporäre
   Pfade oder reine Providertelemetrie werden nur dann entfernt, wenn eine
   separate kanonische Redaktionsbeschreibung und beide Digests dokumentiert
   sind.
   Das Manifest kennzeichnet jedes Fixture abschließend als `schema_only` oder
   `request_bound`. Für `request_bound` enthält es zusätzlich das originale
   kanonische Requestdokument und prüft dessen Request-ID/Digest-Bindung; eine
   rekonstruierte Kontextbeschreibung genügt nicht.
4. Nach grüner lokaler Matrix werden direkte, synthetische Live-Canaries für
   Codex-Plan, Codex-Implementierung, Codex-Korrektur, Codex-Finalbericht sowie
   Claude-Plan-, Slice-, Korrektur- und Finalreview ausgeführt. Sie laufen ohne
   `run_task`, verändern keine Workflowfreigabe und erzeugen keine fachliche
   Produktionsentscheidung.
5. Ein Canary ist nur Transportnachweis: Provider akzeptiert das konkrete
   Schema, Antwort wird lokal gebunden, und alte Textmarkerparser bleiben
   unberührt.
6. Jede erzeugte Writerform benötigt entweder mindestens ein verifiziertes
   `request_bound`-Fixturepaar oder einen erfolgreichen Live-Canary. Ein
   `schema_only`-Fixture ist ausdrücklich kein Writerform-Nachweis. Fehlen
   beide zulässigen Nachweistypen, darf das Arbeitspaket nicht abgeschlossen
   werden. Das Manifest weist je Writerform den tragenden Nachweistyp aus.
7. Subset-Sonden und Canaries laufen präventiv in einem separaten,
   wegwerfbaren Arbeitsverzeichnis mit der restriktivsten vom jeweiligen
   Provider akzeptierten Sandbox. Kein Pfad des echten Repositorys wird dem
   Provider schreibbar gemacht. Die Ausgabe von
   `git status --porcelain=v1 --untracked-files=all` im echten Arbeitsbaum
   muss vor und nach jedem Aufruf bytegleich sein.
8. „Writerform“ bezeichnet in diesem Arbeitspaket genau eine der acht
   provider- und policygebundenen Nachweisklassen: Codex-Plan,
   Codex-Implementierung, Codex-Korrektur, Codex-Finalbericht, Claude-Plan,
   initialer Claude-Slice, Claude-Slice-Konvergenz und Claude-Finalreview. Für
   jede Klasse wird mindestens eine kanonische Schemainstanz aus einem
   vollständigen gebundenen Repräsentativkontext erzeugt und nach Nummer 6
   belegt. Variierende Finding-IDs und Attestierungen werden durch die
   Differentialmatrix abgedeckt, erzeugen aber keine zusätzliche
   Nachweisklasse. Freigabe-, Ablehnungs- und Stopäste sind geschlossene Äste
   innerhalb einer Writerform und benötigen keinen separaten Transportbeleg.

## 5. Umsetzungsslices

### Slice 1 - Request-spezifische native Codex-Writerschemas

Ziel ist eine einzige kanonische Codex-Schemaprojektion pro gebundenem
Request, die von Builder, Bundle, Adapter und lokalem Differentialtest
identisch verwendet wird.

**Exakter Änderungspfad**

- `schemas/native-provider-schema-capabilities-v1.json` (Neuanlage)
- `schemas/native-provider-schema-exceptions-v1.json` (Neuanlage)
- `scripts/native_contract_probe.py` (Neuanlage; `scripts/` ist ein neues Wurzelverzeichnis)
- `src/native_provider_schema.py` (Neuanlage)
- `src/native_codex_contract.py`
- `src/native_codex_request.py`
- `src/agent_adapters.py`
- `tests/test_native_provider_schema.py` (Neuanlage)
- `tests/test_native_codex_contract.py`
- `tests/test_native_codex_request.py`
- `tests/test_agent_adapters.py`
- `tests/test_agent_runtime.py`

#### Arbeitsschritte

1. Zuerst einen minimalen direkten Subset-Probe-Harness erstellen und in
   einem wegwerfbaren, vom Repository getrennten Arbeitsverzeichnis für Codex
   und Claude ausführen. Positionsgebundene Tupel, `const`-Listen,
   `minItems`/`maxItems`, geschlossene Objekte und verschachtelte `oneOf`-Äste
   werden einzeln geprüft. Die bestätigten Fähigkeiten werden providerbezogen
   und versioniert in der Capability-Tabelle persistiert. Jeder Eintrag bindet
   Provider, Binärname, gemeldete CLI-Version, Modell beziehungsweise
   Transportprofil, Sondendatum und das Ergebnis je Schemafeature. Weicht die
   laufende CLI-Version oder das normalisierte Transportprofil von der
   gebundenen Sonde ab, wird die Tabelle fail-closed abgewiesen und muss vor
   der Writerverwendung neu erzeugt werden. Die Subset-Sonde verwendet
   notwendigerweise eine eigene minimale Invokation für beliebige
   Kandidatenschemata. Sonde und echter Produktionsaufruf werden mit derselben
   Normalisierungsfunktion auf den in Abschnitt 4.1.8 abschließend definierten
   Feldumfang projiziert; ihre normalisierten Profile müssen kanonisch
   bytegleich sein. Abweichende Sandbox, Arbeitsverzeichnisse und temporäre
   Pfade sind zulässig, weil sie ausdrücklich nicht zum Profil gehören.
   Lehnt ein Provider eine für die geplante Schließung unverzichtbare
   Konstruktion ab, greift die Stopbedingung vor der Writerimplementierung.
2. Einen kleinen gemeinsamen Schemaprojektionskern für defensive Kopie,
   kanonische Serialisierung, Digest und dokumentierte Provider-Subset-
   Normalisierung einführen. Er lädt und validiert Capability-Tabelle und
   Ausnahmeliste typisiert.
3. `native_codex_provider_response_schema()` an den konkreten
   `NativeCodexContext` beziehungsweise einen gleichwertigen unveränderlichen
   Projektionskontext binden.
4. Nur erwarteten Ergebnistyp plus Stopast projizieren; exakte offene
   Finding-IDs und Dispositionsvollständigkeit in die Writerschemaform
   übernehmen. Kontextseitig feststehende Werte werden als `const` gebunden:
   insbesondere `ready=false` bei nicht freigegebenen gebundenen
   Teständerungen und ausschließlich `stop_result`, wenn ein Planvertrag
   keinen Sliceplan verlangt.
5. `NativeCodexRequestBundle` um eine deterministisch aus dem Bound Context
   ableitbare `provider_response_schema`-Ansicht und vollständige
   `response_contract`-Selbstprüfung ergänzen.
6. Den Codex-Adapter ausschließlich das Schema des Bundles serialisieren und
   messen lassen; jede unabhängige Neuberechnung im Adapter entfernen.
7. Die geschlossene Hülle `{"result": ...}` im Schema und in der Extraktion
   festschreiben; alternative oder zusätzliche Top-Level-Felder ablehnen.
8. Nicht sicher ausdrückbare Codex-Invarianten sofort in der typisierten
   Ausnahmeliste registrieren. Jeder Eintrag benötigt Providerprobe,
   Fehlercode, Operation, fehlendes Feature und Regressionstest.
9. Historische Leseschemata unverändert validieren und Writer-/Readergrenze in
   Docstrings und Tests festschreiben.

#### Akzeptanzkriterien

- Plan-, Implementierungs-, Korrektur- und Finalrequests liefern
  unterschiedliche kanonische Schemadigests.
- Jede Writerschemaform enthält ausschließlich erwarteten Resultattyp und
  Stopast.
- Für einen Kontext mit nicht freigegebenen gebundenen Teständerungen scheitert
  `ready=true` bereits an der Providerprojektion; für
  `require_slice_plan=False` scheitert jedes `plan_result` dort ebenfalls.
- Fehlende, doppelte, fremde oder nicht offene Finding-Dispositionen sind im
  konkreten Writerschema nicht darstellbar, soweit das Codex-Subset dies
  unterstützt; verbleibende lokale Eindeutigkeitsregeln sind registriert.
- Builder, Bundle und Adapter verwenden bytegleiches Schema; eine absichtliche
  Abweichung wird vor Providerstart abgewiesen.
- Capability-Tabelle und Ausnahmeliste sind typisiert und versioniert; eine
  nicht belegte Ausnahme sowie ein nicht bestätigtes Schemafeature werden
  fail-closed abgewiesen.
- Ein Test simuliert eine von der Capability-Tabelle abweichende Codex- oder
  Claude-CLI-Version sowie ein abweichendes Transportprofil und weist nach,
  dass keine Projektion mit den veralteten Zusagen gebaut wird.
- Je Provider normalisiert ein Test eine echte produktive
  `PreparedProviderInput`-Kommandozeile und eine äquivalente
  Subset-Sondenkommandozeile auf dasselbe Profil. Unterschiede nur bei
  Sandbox, Arbeitsverzeichnis oder temporären Pfaden bleiben gleich;
  abweichendes Modell, Reasoning-/Effortwert, Schemaübergabemechanismus oder
  stabiles semantisches Flag erzeugt dagegen fail-closed ein anderes Profil.
- Ein zusätzliches unbekanntes CLI-Argument wird nicht herausgefiltert,
  sondern als nicht klassifizierte Profil-Drift fail-closed abgewiesen.
- Nach Projektionen für mehrere unterschiedliche Codex-Kontexte sind die
  kanonischen Bytes derselben an den Projektionskern übergebenen Instanz des
  Codex-Resultat-v1-Basisschemas unverändert.
- Codex-Schema und Adapter akzeptieren ausschließlich die Hülle
  `{"result": ...}`.
- Markerparser und textuelle Reparaturpfade werden in fokussierten nativen
  Tests als verbotene Aufrufe instrumentiert.
- Kein Slice-1-Test liest `.orchestrator/` oder hängt von einem lokalen,
  gitignorierten Corpus ab; die vollständige Slice-1-Matrix bleibt auch bei
  vollständig fehlendem `.orchestrator`-Verzeichnis grün.

### Slice 2 - Request-spezifische native Claude-Writerschemas

Ziel ist eine entscheidungs- und rundenspezifische Claude-Projektion, die
Findingeigentum, Dispositionspflicht, Observation-, Anchor-, Evidenz- und
Pre-Mortem-Regeln so weit wie möglich vor die Generierung verlagert.

**Exakter Änderungspfad**

- `schemas/native-provider-schema-exceptions-v1.json`
- `src/native_provider_schema.py`
- `src/native_review_contract.py`
- `src/native_review_request.py`
- `src/agent_adapters.py`
- `tests/test_native_provider_schema.py`
- `tests/test_native_review_contract.py`
- `tests/test_native_review_request.py`
- `tests/test_native_review_schema.py`
- `tests/test_agent_adapters.py`
- `tests/test_agent_runtime.py`

#### Arbeitsschritte

1. Ausschließlich aus dem gebundenen typisierten `approval_marker` sowie
   `allow_new_observations`, `round_number` und `anchor_origin` getrennte
   Writerschemaäste für Plan, initialen Slice, Slice-Konvergenz und Finalreview
   erzeugen. `NativeReviewKind` und der freie String `operation` dürfen keinen
   Ast auswählen.
2. Reviewer, erlaubte bekannte Finding-IDs, nächstes Findingfenster,
   zulässige Status-/Reklassifizierungsreferenzen und Anchorfähigkeit exakt
   projizieren.
3. Positive, negative und Stopentscheidungen als getrennte geschlossene Äste
   modellieren. Positive Äste verlangen echtes Pre-Mortem, Evidenz und die
   gebundenen Dispositionen. Fehlende/rote Attestierung oder nicht
   freigegebene Teständerungen binden `decision` bereits auf `denied`.
4. Neue Observations und Reklassifizierungen zu Observations in Korrektur- und
   Finalrunden schema-seitig unmöglich machen. Neue Defekte dieser Runden sind
   Blocker und erzwingen Denial.
5. Das Bundle weiterhin Digest und Schema selbst prüfen lassen; Repairrequests
   referenzieren unverändert denselben gebundenen Responsevertrag.
6. Lokale Domänenchecks beibehalten und ihre Restmenge gegen die typisierte
   Ausnahmeliste messen; zulässige Claude-Ausnahmen werden in der bereits aus
   Slice 1 vorhandenen konkreten Datei ergänzt.
7. Die unveränderte Hülle `{"result": ...}` und ihre exakte Extraktion für
   Claude mit positiven und negativen Hüllentests absichern.

#### Akzeptanzkriterien

- Kontextfremde Finding-IDs, nicht erlaubte Anchors und neue Observations in
  Korrektur-/Finalrunden scheitern bereits an der konkreten
  Providerprojektion.
- Eine positive Antwort ohne inhaltliches Pre-Mortem, erforderliche Evidenz
  oder vollständige eigene Findingzustände ist nicht darstellbar.
- Bei fehlender, unvollständiger oder FAIL-Attestierung sowie bei nicht
  freigegebenen Teständerungen scheitert `decision=approved` bereits an der
  Providerprojektion.
- Ein Stop kann keine Freigabe oder Findingevents zugleich enthalten.
- Plan-, Slice-, Korrektur- und Finalreview besitzen nachweislich verschiedene
  Schemadigests, wenn ihre Domänenpflichten verschieden sind.
- Zwei ansonsten gleiche gebundene Kontexte mit identischem
  `approval_marker` und identischen Rundenfeldern liefern trotz verschiedener
  freier `operation`-Strings bytegleich dasselbe Writerschema. Verschiedene
  `approval_marker` beziehungsweise Slice-Rundenpolicies liefern verschiedene
  Äste; `NativeReviewKind` ist keine Eingabe der Projektion.
- Historische `schema_only`-Claude-Resultate bleiben über das allgemeine
  Leseschema lesbar und behalten ihren Byte-Digest. Gebundene
  Domänen-Lesergebnisse und Diagnosecodes werden nur für `request_bound`-Paare
  verlangt.
- Die lokale zweite Schutzschicht lehnt jede absichtlich am Writerschema
  vorbeigeführte ungültige Antwort weiterhin fail-closed ab.
- Der Reviewprojektionskern arbeitet auf einer defensiven Kopie. Ein Test
  übergibt dieselbe geladene Review-Basisschemainstanz nacheinander an mehrere
  unterschiedliche Projektionen und weist ihre kanonische Bytegleichheit vor
  und nach jedem Aufruf nach; damit wird die heutige In-place-Mutation gezielt
  ausgeschlossen.
- Claude-Schema und Adapter akzeptieren ausschließlich die Hülle
  `{"result": ...}`.

### Slice 3 - Corpus, Differentialnachweis und direkte Live-Canaries

Ziel ist der reproduzierbare Nachweis, dass die Writerschemas die bekannten
realen Antwortformen abdecken und keine heute schema-ausdrückbare
Domänenlücke mehr hinter dem Provideraufruf verbleibt.

**Exakter Änderungspfad**

- `docs/internal/ORCHESTRATOR_ROADMAP_PHASE_2_PLUS.md`
- `docs/internal/native-agent-contract-closure-arbeitsplan.md`
- `docs/internal/native-agent-contract-closure-review.md`
- `scripts/native_contract_probe.py`
- `src/agent_adapters.py`
- `src/agent_runtime.py`
- `tests/fixtures/native-contract-corpus/manifest.json` (Neuanlage; Verzeichnis ist neu)
- `tests/fixtures/native-contract-corpus/corpus.jsonl` (Neuanlage)
- `tests/test_native_contract_corpus.py` (Neuanlage)
- `tests/test_native_contract_differential.py` (Neuanlage)
- `tests/test_agent_adapters.py`
- `tests/test_agent_runtime.py`
- `tests/test_workflow.py`
- `tests/test_orchestrator_runtime.py`

#### Arbeitsschritte

1. Alle im Repository und im aktuellen lokalen Artefaktbestand verfügbaren
   nativen Rohantworten sowie alle kanonischen Requestdokumente unter
   `.orchestrator/logs/manual-*-review/request*.json` inventarisieren.
   Request und Antwort werden über `request_id` gepaart, die kanonische
   Requestdigestbindung wird nachgerechnet und jedes vollständig belegte Paar
   als reproduzierbares `request_bound`-Fixture persistiert. Die drei in
   Abschnitt 3.4 namentlich belegten Paare sind die bekannte Untergrenze;
   weitere beim Slice-Start auffindbare Paare dürfen nicht ausgelassen werden.
   Übrige Antwortbytes werden digestgebunden als `schema_only` übernommen.
2. Corpusloader und Manifestvalidator implementieren. `schema_only` prüft
   Herkunft, Antwortdigest und allgemeine Schema-Lesbarkeit. `request_bound`
   prüft zusätzlich das originale kanonische Requestdokument, seine
   Request-ID-/Digestbindung, rekonstruiert daraus den vollständigen
   gebundenen Kontext, erzeugt dessen konkrete Writerschemainstanz und
   validiert die historischen Antwortbytes zuerst dagegen und anschließend
   gegen Domänen-Lesergebnis beziehungsweise stabilen Fehlercode. Nur ein auf
   beiden Ebenen erfolgreich geprüftes Paar darf im Manifest eine Writerform
   belegen. Fehlt das Originalrequest, ist eine Hochstufung zu
   `request_bound` verboten. Scheitert eine lokal gültige historische Antwort
   am Writerschema, ist dies ein Writerschemadefekt und Stopgrund; nur eine
   bereits typisiert registrierte Ausnahme mit Providerbeleg und eigenem
   Regressionstest darf diesen Befund anders klassifizieren.
3. Die in Abschnitt 4.4 abschließend benannten Pflichtmutationsklassen für
   alle betroffenen Codex- und Claude-Requestarten implementieren.
   Providerprojektion und lokale Domänenkonvertierung werden gegeneinander
   geprüft. Eine absichtlich gelockerte Writerregel dient als zwingend rote
   Negativkontrolle.
4. Die seit Slice 1 geführte typisierte Ausnahmeliste gegen Capability-Tabelle,
   Providerbeleg und jeden benannten Regressionstest prüfen; Slice 3 erfindet
   keine neue Ausnahme.
5. In Workflow-/Runtime-Durchstichen die Legacyparser mit Sprengfallen
   versehen und beweisen, dass native Writerresultate nie auf Textparser oder
   textuelle Contract-Repair zurückfallen.
6. In Adapter und Runtime eine explizite typisierte Ausführungsgrenze für
   native Codex-Aufrufe einführen. Sie parametrisiert Ausführungsverzeichnis,
   Sandboxmodus und Evidenzassetziel. Der Produktionsmodus behält seine
   bisherigen Defaults; der Canarymodus erzwingt wegwerfbares Verzeichnis,
   restriktivsten Sandboxmodus und ein Assetziel außerhalb des Repositorys.
   Im Canarymodus ruft das Probe-Werkzeug dieselbe Adapter- und
   Runtimefunktion auf und baut keine parallele Codex-Kommandozeile. Diese
   Gleichheitspflicht gilt ausdrücklich für Canaries; die vorgelagerte
   Subset-Sonde aus Slice 1 bleibt die dort beschriebene minimale Invokation
   für beliebige Kandidatenschemata und wird über ihr identisches
   Transportprofil gebunden.
7. Nach fokussierter und vollständiger lokaler Matrix die geplanten
   Live-Canaries ausführen und Ergebnis, Provider, Schema-Digest, Request-ID,
   Antwortdigest und lokale Entscheidung im Reviewdokument festhalten.
8. Den Contract-Closure-Stand und die erst danach wieder zulässige
   Bootstrap-Smoke-Test-Grenze in die Roadmap übertragen.

#### Akzeptanzkriterien

- Das Corpusmanifest ist vollständig gegenüber allen beim Slice-Start
  auffindbaren nativen Rohantworten und kanonischen Requestdokumenten und
  verifiziert jeden gespeicherten Digest. Es weist die Zahl aller
  auffindbaren und übernommenen `request_bound`-Paare aus; die übernommene
  Zahl darf nicht kleiner sein und umfasst mindestens die drei in Abschnitt
  3.4 namentlich belegten Paare.
- Historische `schema_only`-Antworten behalten Digest und allgemeine
  Schema-Lesbarkeit. Nur `request_bound`-Paare müssen ihr gebundenes
  Domänen-Lesergebnis und bei Ablehnung den stabilen Diagnosecode behalten;
  zusätzlich müssen ihre Antwortbytes gegen die aus dem Originalrequest
  rekonstruierte konkrete Writerschemainstanz validieren.
- Kein Differentialfall ist zugleich provider-schema-valide und lokal
  ungültig, sofern sein lokaler Fehlercode nicht explizit als technisch nicht
  schema-ausdrückbare Invariante registriert ist.
- Native Workflowtests bestehen mit explodierenden Legacyparser-Stubs.
- Direkte Codex- und Claude-Canaries akzeptieren die konkreten
  Writerschemaformen und passieren die lokale Bindungsprüfung.
- Der Codex-Finalbericht besitzt einen eigenen erfolgreichen Live-Canary. Für
  jede weitere erzeugte Writerform ist im Manifest entweder ein
  `request_bound`-Fixture oder ein erfolgreicher Canary nachgewiesen;
  `schema_only` zählt dafür nicht. Fehlt beides, stoppt der Slice.
- Das Manifest enthält genau acht Writerform-Zeilen gemäß Abschnitt 4.5.8 und
  nennt pro Zeile Schemasdigest, Repräsentativkontext sowie entweder das
  erfolgreiche `request_bound`-Fixturepaar oder den erfolgreichen Canary.
  Entscheidungs- und Stopäste werden nicht als zusätzliche Writerformen
  gezählt.
- Der Codex-Canary durchläuft dieselbe Adapter- und Runtimefunktion wie der
  Produktionspfad, jedoch mit der typisierten Canary-Ausführungsgrenze. Tests
  beweisen, dass Ausführungsverzeichnis, Sandboxmodus und Evidenzassetziel
  nicht auf einen Repositorypfad zeigen.
- Canaryausführung verändert weder `.orchestrator/state.json` noch
  Checkpoints, Recordketten, Inbox, Outbox oder Git-Index. Zusätzlich ist die
  Ausgabe von `git status --porcelain=v1 --untracked-files=all` vor und nach
  jedem Canary bytegleich.
- `python3 -m pytest tests/ -v` und `git diff --check` sind erfolgreich.

#### Umsetzungsstand Slice 3 vom 24. August 2026

- Inventur abgeschlossen und nach dem Slice-Review korrigiert: 14 erfolgreiche
  native Rohantworten, zwölf
  kanonische Claude-Requestdokumente, drei digestverifizierte und vollständig
  übernommene `request_bound`-Paare sowie elf `schema_only`-Antworten. Drei
  technische Failure-Envelopes sind separat katalogisiert und werden nicht als
  Modellantworten fehlklassifiziert.
- Alle drei `request_bound`-Paare bestehen das aus ihrem Originalrequest neu
  erzeugte Writerschema und anschließend die vollständige lokale
  Domänenbindung.
- Die Differentialmatrix umfasst alle acht Writerformen, prüft jeden
  registrierten Provider-Ausnahmeeintrag auf einen existierenden Regressionstest
  und besitzt eine nachweislich rote Kontrolle mit absichtlich entfernter
  Nonblank-Writerregel.
- Die native Codex-Runtime besitzt eine typisierte Ausführungsgrenze. Produktion
  bleibt bei Repository-CWD, `workspace-write` und Repository-Assetwurzel;
  Canaries erzwingen `read-only` sowie CWD und Assetwurzel außerhalb des
  Repositorys.
- Erfolgreiche Livebelege liegen für Codex Plan, Implementierung, Korrektur und
  Finalbericht sowie für Claude Plan und Finalreview vor. Initialer
  Claude-Slice und Konvergenz sind durch echte `request_bound`-Paare belegt.
- Ein erster Codex-Plan-Canary wurde lokal mit `slice-plan-invalid` abgewiesen,
  weil das Modell für den einzigen Slice die ID `03` gewählt hatte. Diese
  nicht über positional tuples ausdrückbare Restinvariante fällt unter den
  bereits registrierten Fehlercode. Der vorhandene Ausnahmeeintrag wurde um
  die konkret nachgewiesene 1-basierte Kontiguitätsinvariante und einen
  Regressionstest erweitert, ohne die Ausnahmemenge zu vergrößern; der
  präzisierte Canary mit der expliziten numerischen ID `1` bestand. Ein
  paralleler Claude-Plan-Canary lieferte ein
  technisches Provider-Envelope und bestand bei serieller, identischer
  Wiederholung.
- Alle Canaryaufrufe bestätigten bytegleichen
  `git status --porcelain=v1 --untracked-files=all` vor und nach dem Aufruf.
  Der Korrekturstand misst zusätzlich die gitignorierten Workflowstores
  `.orchestrator/`, `inbox/` und `outbox/` rekursiv und fail-closed.
  Die vollständige lokale Matrix und das Claude-Slice-Review bleiben die
  abschließenden Freigabegrenzen.

## 6. Slice-Reihenfolge und Reviewgrenzen

Die Slices sind absichtlich sequenziell:

1. Slice 1 schafft den gemeinsamen Projektionskern und schließt den Codex-
   Writerpfad.
2. Erst nach Claudes Freigabe von Slice 1 verwendet Slice 2 diesen Kern für
   die komplexere Reviewsemantik.
3. Erst nach Claudes Freigabe von Slice 2 friert Slice 3 beide Writerverträge
   als Corpus- und Differentialnachweis ein und führt Live-Canaries aus.

Ein Slice-Review erhält mindestens:

- diesen genehmigten Arbeitsplan;
- den vollständigen Diff seit der letzten freigegebenen Slice-Grenze;
- fokussierte Testresultate und `git diff --check`;
- alle offenen Claude-Findings mit ihren bisherigen Dispositionen;
- die Liste dokumentierter lokaler Schemaausnahmen.

Eine Observation darf eine Slice-Freigabe begleiten, muss aber spätestens in
Slice 3 disponiert oder als ausdrücklich nachgelagerter, nicht
Contract-Closure-blockierender Punkt begründet werden. Ein Blocker verhindert
den Beginn des nächsten Slices.

## 7. Validierungsmatrix

### 7.1 Nach Slice 1

```text
python3 -m pytest tests/test_native_provider_schema.py tests/test_native_codex_contract.py tests/test_native_codex_request.py tests/test_agent_adapters.py tests/test_agent_runtime.py -v
python3 -m pytest tests/ -v
git diff --check
```

### 7.2 Nach Slice 2

```text
python3 -m pytest tests/test_native_provider_schema.py tests/test_native_review_contract.py tests/test_native_review_request.py tests/test_native_review_schema.py tests/test_agent_adapters.py tests/test_agent_runtime.py -v
python3 -m pytest tests/ -v
git diff --check
```

### 7.3 Nach Slice 3

```text
python3 -m pytest tests/test_native_contract_corpus.py tests/test_native_contract_differential.py tests/test_agent_adapters.py tests/test_agent_runtime.py tests/test_workflow.py tests/test_orchestrator_runtime.py -v
python3 -m pytest tests/ -v
git diff --check
```

Live-Canaries werden erst nach der grünen vollständigen lokalen Matrix und mit
separat protokollierten direkten Provideraufrufen ausgeführt. Sie ersetzen
keine Tests und keine Reviewfreigabe.

## 8. Stopbedingungen

Die Umsetzung hält an und verlangt eine explizite Planrevision, wenn:

- ein erforderlicher Vertrag nur durch eine rückwärtsinkompatible Änderung
  des allgemeinen v1-Leseschemas geschlossen werden könnte;
- Codex- oder Claude-Provider das notwendige Writerschema-Subset nicht
  akzeptieren und keine engere semantisch gleichwertige Projektion möglich ist;
  diese Stopbedingung gilt bereits während der initialen Subset-Sonde in
  Slice 1 und vor jeder produktiven Writeränderung;
- die laufende Provider-CLI oder ihr normalisiertes Transportprofil nicht
  exakt zur versionierten Capability-Tabelle passt und die erforderliche
  erneute Sonde nicht erfolgreich abgeschlossen werden kann;
- die exakte Findingdisposition nur mit unbeherrschbarer kombinatorischer
  Schemaexplosion darstellbar wäre und eine Änderung des Resultatmodells nötig
  wird;
- verfügbare Rohantworten Secrets oder nicht reproduzierbare Daten enthalten,
  die nicht mit nachvollziehbarer Redaktionsbindung als Fixture übernommen
  werden können;
- Differentialtests eine provider-schema-valide lokale Ablehnung finden, die
  weder geschlossen noch als tatsächlich nicht schema-ausdrückbar begründet
  werden kann;
- eine lokale Ausnahme ohne reproduzierbare Providerablehnung, ohne exakte
  Capability-Grenze oder ohne benannten Regressionstest benötigt wird, oder
  die Ausnahmeliste ohne erneutes Planreview wachsen müsste;
- für eine erzeugte Writerform weder ein verifiziertes `request_bound`-
  Fixturepaar noch ein erfolgreicher direkter Live-Canary verfügbar ist;
  `schema_only`-Fixtures erfüllen diese Stopbedingung ausdrücklich nicht;
- ein historisch lokal gültiges `request_bound`-Paar nicht gegen die aus
  seinem Originalrequest rekonstruierte Writerschemainstanz validiert oder
  daran scheitert und der Befund weder als Writerschemadefekt behoben noch
  durch eine bereits typisierte, providerbelegte und regressionsgetestete
  Ausnahme erklärt werden kann;
- eine Subset-Sonde oder ein Canary den echten Arbeitsbaum verändert oder
  nicht in einer vom Repository getrennten, restriktiven Sandbox ausführbar
  ist;
- ein Slice Pfade außerhalb seines dokumentierten Änderungspfads benötigt und
  diese Erweiterung nicht vor der Änderung im Reviewdokument begründet wird.

## 9. Nichtziele

- Keine menschenlesbare Live-Projektion nativer Zwischenresultate.
- Keine Antigravity-JSON-Integration und keine Änderung der Agy-Abläufe.
- Keine Einführung oder Änderung einer Claude-only-Reviewpolicy.
- Kein Bootstraplauf und keine Inbox-/Outbox-Verarbeitung.
- Keine Entfernung historischer Textmarkerparser.
- Keine Änderung vorhandener Recordtypen oder manuelle Reparatur von
  `.orchestrator/state.json` beziehungsweise Checkpoints.
- Keine Performance- oder Promptoptimierung, sofern sie nicht unmittelbar für
  ein providerkompatibles geschlossenes Writerschema erforderlich ist.

## 10. Gesamt-Abnahmekriterien

Das Arbeitspaket ist erst abgeschlossen, wenn:

1. Codex und Claude für jede native Operation ein request-spezifisches,
   digestgebundenes Writerschema erhalten.
2. Request, Bundle, Providerinput und lokale Selbstprüfung dasselbe kanonische
   Schema und denselben Digest verwenden.
3. Ergebnisart, zulässige Finding-IDs, erforderliche Dispositionen,
   Anchorfähigkeit, Observationpolicy und positive Reviewpflichten soweit im
   Provider-Subset möglich bereits schema-seitig geschlossen sind.
4. Alle allein aus dem gebundenen Kontext bereits entschiedenen Werte als
   `const` projiziert sind, einschließlich ausgeschlossener Readiness,
   ausgeschlossener Freigabe und `require_slice_plan=False`.
5. Jede verbleibende lokale-only Invariante typisiert, durch eine konkrete
   Provider-Subset-Grenze begründet und getestet ist.
6. Das vollständige verfügbare native Request- und Antwortcorpus
   reproduzierbar inventarisiert, digestgeprüft und korrekt als `schema_only`
   oder `request_bound` klassifiziert wird.
7. Differentialtests alle Pflichtmutationsklassen und die rote
   Negativkontrolle abdecken und keine undokumentierte Lücke zwischen
   Providerprojektion und Domänenvertrag finden.
8. Jede Writerform entweder durch mindestens ein `request_bound`-Fixturepaar
   oder einen sicheren Live-Canary belegt ist; `schema_only` ist kein
   Writerbeleg. Insbesondere akzeptieren gezielte direkte Codex- und
   Claude-Canaries die Writerschemas praktisch und liefern lokal gebundene
   Resultate.
9. Das Manifest genau die acht in Abschnitt 4.5.8 definierten Writerformen
   ausweist und jedes als Nachweis verwendete `request_bound`-Paar zusätzlich
   gegen seine aus dem Originalrequest rekonstruierte Writerschemainstanz
   erfolgreich validiert wurde.
10. Kein nativer Test einen Textmarkerparser oder textuellen Reparaturpfad
   verwendet.
11. Die allgemeinen Resultat-v1-Leseschemata bytegleich unverändert geblieben
    sind, die einzige Transporthülle `{"result": ...}` ist und sichere
    Canaries den echten Arbeitsbaum nicht verändern.
12. Die vollständige Repositorysuite grün und `git diff --check` sauber ist.
13. Codex' adversariales Gesamtreview keine offene Contract-Closure-Lücke
    feststellt und sämtliche Claude-Findings geschlossen oder begründet
    außerhalb dieses Pakets disponiert sind.

## 11. Disposition des Claude-Planreviews vom 24. August 2026

Das vollständige ursprüngliche Reviewergebnis bleibt in
`docs/internal/native-agent-contract-closure-review.md` erhalten. Sämtliche
Befunde wurden für diese Revision wie folgt in den normativen Arbeitsplan
übernommen:

Claudes Entscheidung zur geprüften Vorfassung lautete `PLAN_APPROVAL: NO`.
Als größtes Restrisiko benannte Claude eine beliebig dehnbare
„Provider-Subset“-Klausel; als realistische Bruchbedingung eine erst in Slice 3
erkannte Providerablehnung der Tupelprojektion, die ohne falsifizierbare
Differentialmatrix folgenlos zur lokalen Ausnahme geworden wäre. Die
vorgezogene Capability-Sonde, die streng gebundene Ausnahmeliste und die
Pflichtmutationsmatrix schließen genau diesen Nachweisspalt.

| Finding | Einstufung | Disposition im revidierten Plan |
|---|---|---|
| `C-01` | BLOCKER | **ACCEPTED.** Abschnitte 4.2 und 4.3 verlangen nun `const`-Werte für jede allein kontextseitig entschiedene Readiness-/Approval-Alternative. Slice 1 prüft `ready=false` und `require_slice_plan=False`; Slice 2 prüft kontextseitig erzwungenes `decision=denied`. |
| `C-02` | BLOCKER | **ACCEPTED.** Slice 1 beginnt vor der Writerimplementierung mit direkten providergebundenen Subset-Sonden. Das Ergebnis wird in einer typisierten, versionierten Capability-Tabelle persistiert und ist normative Eingabe beider Projektionen. |
| `C-03` | BLOCKER | **ACCEPTED.** Die typisierte Ausnahmeliste wird als konkrete Datei bereits in Slice 1 angelegt, in Slice 2 erweitert und in Slice 3 ausschließlich verifiziert. |
| `C-04` | BLOCKER | **ACCEPTED.** Beide allgemeinen Resultat-v1-Schemadateien wurden aus den Änderungspfaden entfernt und sind ausdrücklich unveränderliche Reader-Baselines. |
| `C-05` | BLOCKER | **ACCEPTED.** Abschnitt 4.4 enthält jetzt die abschließenden Pflichtmutationsklassen, Mindestabdeckung je betroffener Requestart und eine zwingend rote Negativkontrolle. |
| `C-06` | BLOCKER | **ACCEPTED.** Der fehlende Codex-Finalbericht wird mit einem eigenen Live-Canary belegt; jede Writerform benötigt Corpus oder Canary, andernfalls greift eine Stopbedingung. |
| `C-07` | BLOCKER | **ACCEPTED.** Sonden und Canaries laufen außerhalb des Repositorys in wegwerfbaren Arbeitsverzeichnissen und restriktiven Sandboxes. Der echte Arbeitsbaum muss vor und nach jedem Aufruf bytegleich unverändert sein. |
| `C-08` | OBSERVATION | **ACCEPTED.** Sämtliche Neuanlagen sind im exakten Änderungspfad markiert, das neue Wurzelverzeichnis `scripts/` ist benannt und `tests/test_native_review_schema.py` wurde Slice 2 zugeordnet. |
| `C-09` | OBSERVATION | **SUPERSEDED.** Die erste Disposition band irrtümlich den außerhalb des gebundenen Kontexts liegenden `NativeReviewKind` ein. Claudes Revision-2-Befund und die korrigierte Disposition stehen in den Abschnitten 12 und 13. |
| `C-10` | OBSERVATION | **ACCEPTED.** Die geschlossene Hülle `{"result": ...}` und ihre exakte Adapterextraktion sind jetzt allgemeine Invariante und Bestandteil beider Slices. |
| `C-11` | OBSERVATION | **ACCEPTED, SPÄTER VERSCHÄRFT.** Der ursprüngliche Bytegleichheitsnachweis wurde nach `C-17` auf dieselbe tatsächlich an den Projektionskern übergebene Basisschemainstanz präzisiert; siehe Abschnitt 13. |

Mit dieser Revision sind alle sieben Planblocker sachlich adressiert und alle
vier Observations konkreten Slices zugeordnet. Sie gelten erst dann als durch
Claude geschlossen, wenn Claude genau diese revidierte Planfassung erneut
read-only geprüft und freigegeben hat. Bis dahin beginnt keine
Implementierung.

## 12. Claude-Planreview – Revision 2 vom 24. August 2026

### Eingefrorener Reviewgegenstand

- Arbeitsplan: `docs/internal/native-agent-contract-closure-arbeitsplan.md`,
  Revision 2 (Abschnitte 1 bis 11)
- Basiscommit: `c410eef95164d10b1ff0d97d2a9593ca5af18324`
- Vorreview: `docs/internal/native-agent-contract-closure-review.md`,
  Abschnitt „Claude-Planreview vom 24. August 2026“
- Anforderungsquelle: `/tmp/orchestrator-stabilisierung-1-1-nachtraege.md`,
  ausschließlich der Abschnitt **Contract Closure**
- Reviewer: Claude
- Modus: direktes read-only Repositoryreview ohne Testausführung, ohne
  Bootstrap, ohne Änderung an Produktivcode, Tests oder bestehender Planung
- Zusätzlich geprüfter Repositorystand gegenüber Revision 1:
  `src/native_review_request.py` (`NativeReviewKind`, `NativeReviewRequestSpec`,
  `NativeReviewRequestBundle.provider_response_schema`),
  `src/native_review_contract.py` (`BoundNativeReviewContext`,
  `native_review_context_binding`, Findingkontiguität),
  `src/native_codex_request.py`, `src/agent_adapters.py`,
  `src/agent_runtime.py` (`execution_root`, Reviewer-Workspace),
  `src/provider_input_budget.py`, `src/workflow.py:3990-4080`,
  `src/artifact_models.py`, `.orchestrator/artifacts/*/records/`,
  `docs/internal/archive/**`

### Textuelles Reviewergebnis

REVIEWER: claude

FINDING_STATUS: C-01 | CLOSED | Abschnitt 4.2.7/4.2.8 und 4.3.9/4.3.10 verlangen jetzt `const`-Projektionen für jeden allein kontextseitig entschiedenen Ergebniswert; Slice 1 Schritt 4 und Slice 2 Schritt 3 setzen sie um, und beide Slices besitzen ein positives Akzeptanzkriterium, das die gegenteilige Antwort bereits an der Providerprojektion scheitern lässt. Alle drei im Vorreview belegten Instanzen sind namentlich adressiert: `ready=false` bei gebundenen, nicht freigegebenen Teständerungen (`src/native_codex_contract.py:523`), nur `stop_result` bei `require_slice_plan=False` (`src/native_codex_contract.py:403`) und erzwungenes `decision=denied` ohne vollständige PASS-Attestierung beziehungsweise ohne Testfreigabe (`src/native_review_contract.py:780-790`). Die Regel verschärft nichts gegenüber dem lokalen Vertrag: `contract.validation_attestation` erreicht den Kontext über `src/workflow.py:4019`, und historische Planreviews wurden nachweislich mit Attestierung freigegeben.

FINDING_STATUS: C-02 | CLOSED | Abschnitt 3.3 räumt die fehlende empirische Grundlage ausdrücklich ein, Slice 1 Schritt 1 zieht eine direkte Subset-Sonde vor die Writerimplementierung, das Ergebnis wird providerbezogen in `schemas/native-provider-schema-capabilities-v1.json` persistiert, Abschnitt 4.1.8 macht die Tabelle zur normativen Projektionseingabe, und Stopbedingung 2 gilt laut Abschnitt 8 ausdrücklich bereits während der Sonde und vor jeder produktiven Writeränderung.

FINDING_STATUS: C-03 | CLOSED | `schemas/native-provider-schema-exceptions-v1.json` steht als Neuanlage im Änderungspfad von Slice 1, im Änderungspfad von Slice 2 und wird in Slice 3 Schritt 4 nur noch verifiziert. Abschnitt 4.4.8 und Stopbedingung 6 verbieten ein Wachstum ohne Providerbeleg, Capability-Grenze, Regressionstest und erneutes Planreview. Die in Abschnitt 6 geforderte Reviewingabe existiert damit ab dem ersten Slice-Review.

FINDING_STATUS: C-04 | CLOSED | Beide Resultat-v1-Schemadateien sind aus allen drei Änderungspfaden entfernt; Abschnitt 4.1.7 erklärt sie zu unveränderlichen Reader-Baselines, Abnahmekriterium 10 verlangt ihre Bytegleichheit, und Slice 1 besitzt ein entsprechendes Akzeptanzkriterium. Auch die beiden Request-v1-Schemadateien wurden entfernt; das ist für den geplanten Umfang konsistent, kollidiert aber mit C-12.

FINDING_STATUS: C-05 | CLOSED | Abschnitt 4.4.6 benennt die Pflichtmutationsklassen abschließend und deckt alle im Vorreview geforderten Klassen ab, Abschnitt 4.4.7 verlangt mindestens einen Fall je betroffener Requestart sowie eine zwingend rote Negativkontrolle mit genau einer gelockerten Writerregel, und Slice 3 Schritt 3 setzt beides um. Eine trivial grüne Matrix ist damit nicht mehr abnahmefähig.

FINDING_STATUS: C-06 | CLOSED | Abschnitt 3.4 hält den fehlenden Codex-Finalbestand ausdrücklich fest und verbietet die Ableitung aus benachbarten Resultatarten, Abschnitt 4.5.4 nimmt den Codex-Finalbericht in die Canaryliste auf, Abschnitt 4.5.6 verlangt für jede Writerform Corpus oder Canary, Stopbedingung 7 greift bei fehlendem Nachweis, und Slice 3 besitzt ein dediziertes Akzeptanzkriterium. Der Repositorybefund stützt die Prämisse: unter `.orchestrator/artifacts/*/native-codex-responses/` existieren ausschließlich `plan_result`, `implementation_result`, `correction_result` und `stop_result`.

FINDING_STATUS: C-07 | CLOSED | Abschnitt 4.5.7 fordert präventiv ein wegwerfbares, vom Repository getrenntes Arbeitsverzeichnis, die restriktivste vom Provider akzeptierte Sandbox, keinen schreibbaren Repositorypfad sowie bytegleiche Ausgabe von `git status --porcelain=v1 --untracked-files=all` vor und nach jedem Aufruf; Slice 3 Schritt 6, das Slice-3-Akzeptanzkriterium und Stopbedingung 8 tragen die Regel. Die normative Forderung ist damit erfüllt; ihre Vereinbarkeit mit dem tatsächlichen Produktionstransport ist Gegenstand von C-14.

FINDING_STATUS: C-08 | CLOSED | Alle Neuanlagen sind markiert, das neue Wurzelverzeichnis `scripts/` ist ausdrücklich benannt, `tests/test_native_review_schema.py` steht im Änderungspfad von Slice 2 und zusätzlich in der Validierungsmatrix 7.2. Die verbleibende Lücke betrifft nicht die Änderungspfade, sondern die Validierungsmatrix und ist als C-15 erfasst.

FINDING_STATUS: C-09 | OPEN | Die Disposition verfehlt ihr eigenes Akzeptanzkriterium. Gefordert waren ausschließlich typisierte Felder des gebundenen Kontexts. Abschnitt 4.3.1 nennt jedoch an erster Stelle `NativeReviewKind` (`src/native_review_request.py:58`), und dieser Typ ist kein Feld von `NativeReviewContext`: er lebt auf `NativeReviewRequestSpec.review_kind` (`src/native_review_request.py:119`), während `BoundNativeReviewContext` nur `NativeReviewContext` trägt (`src/native_review_contract.py:252`) und `NativeReviewRequestBundle.provider_response_schema` ausschließlich `self.bound_context.context` verwendet (`src/native_review_request.py:280-285`). Die einzige heute vorhandene Brücke zwischen beiden ist die Gleichung `context.operation == {plan|slice|final}` aus `NativeReviewRequestSpec.__post_init__` (`src/native_review_request.py:161-172`), also genau der freie String, den derselbe Absatz als Selektor verbietet. Das korrekte und ausreichende typisierte Kontextfeld ist `approval_marker`, aus dem `src/workflow.py:4004-4008` den `NativeReviewKind` überhaupt erst ableitet.

FINDING_STATUS: C-10 | CLOSED | Abschnitt 4.1.6 erhebt die geschlossene Hülle `{"result": ...}` zur allgemeinen Invariante, Slice 1 Schritt 7 und Slice 2 Schritt 7 schreiben Schema und Extraktion fest, beide Slices besitzen ein Hüllen-Akzeptanzkriterium, und Abnahmekriterium 10 nennt sie als einzige zulässige Transporthülle. Das entspricht der heutigen Erzwingung in `src/agent_adapters.py:471` und `src/agent_adapters.py:1012`.

FINDING_STATUS: C-11 | CLOSED | Abschnitt 4.1.3 und 4.1.7 verlangen Mutationsfreiheit, und Slice 1 besitzt das im Vorreview geforderte Bytegleichheitskriterium nach mehreren Projektionen mit unterschiedlichen Kontexten. Das Akzeptanzkriterium ist wörtlich erfüllt; seine geringe Trennschärfe gegenüber der tatsächlichen In-place-Mutation ist als C-17 neu erfasst.

NEW_FINDING: C-12 | BLOCKER | Ein Akzeptanzkriterium von Slice 2 ist innerhalb der deklarierten Änderungspfade nicht erfüllbar. Es verlangt, dass „inkonsistente Kombinationen aus `NativeReviewKind` und typisierten Rundenfeldern vor dem Schemabau scheitern“. Der Schemabau erfolgt jedoch in `NativeReviewRequestBundle.provider_response_schema` allein aus `bound_context.context` (`src/native_review_request.py:280-285`), und `NativeReviewContext` (`src/native_review_contract.py:142-157`) enthält kein `review_kind`. Eine Aufnahme von `review_kind` in den Kontext verändert `native_review_context_binding()` (`src/native_review_contract.py:828-850`), damit das kanonische Requestdokument, damit `request_digest` und `request_id` sowie `schemas/native-agent-review-request-v1.schema.json` – und keine dieser Dateien steht in einem Änderungspfad. Der Plan lässt damit genau zwei Auswege offen, die er selbst verbietet: den freien `operation`-String als Selektor oder eine undeklarierte Bindungsänderung. | Abschnitt 4.3.1 und das betroffene Slice-2-Akzeptanzkriterium benennen `approval_marker` als normativen Astdiskriminator und streichen `NativeReviewKind` aus der Diskriminatorliste; alternativ wird `review_kind` ausdrücklich in `NativeReviewContext` aufgenommen, `schemas/native-agent-review-request-v1.schema.json` in den Änderungspfad von Slice 2 aufgenommen und die daraus folgende Änderung von Kontextbindung, `request_digest` und `request_id` mit einem Regressionstest über die persistierten `review`-Records begründet.

NEW_FINDING: C-13 | BLOCKER | Der Corpusnachweis kann die ihm zugewiesenen Aussagen technisch nicht erbringen. Abschnitt 4.5.2 verlangt für bekannte Vertragsablehnungen „einen stabilen sachlich richtigen Diagnosecode“, Slice 1 verlangt „alle historischen Codexfixtures behalten ihr bisheriges Lesergebnis“ und Slice 3 „stabile, sachlich passende Diagnosecodes“. Solche Codes entstehen ausschließlich in `parse_native_codex_response()` beziehungsweise im gebundenen Reviewparser, und beide verlangen einen `BoundNative*Context`. Im Repository ist jedoch kein einziges natives Requestdokument persistiert: die Recordkette kennt keinen Requesttyp, `review`- und `agent_result`-Records tragen nur `request_id`, `response_sha256` und `transport_schema`, und unter `.orchestrator/artifacts/*/` liegen ausschließlich Antwortbytes. Da `BoundNativeCodexContext` und `BoundNativeReviewContext` den `request_digest` nur gegen die `request_id` und nicht gegen den Kontext prüfen (`src/native_codex_contract.py:126-141`, `src/native_review_contract.py:256-273`), ließe sich zwar ein synthetischer Kontext mit historischer `request_id` konstruieren – dessen Übereinstimmung mit dem echten Request ist aber unverifizierbar und widerspricht der eigenen Regel aus Abschnitt 3.4, fehlende Bytes nicht zu rekonstruieren oder zu erfinden. Ohne Korrektur reduziert sich der gesamte Corpus auf reine Schemavalidierung, während drei Akzeptanzkriterien mehr behaupten. | Abschnitt 4.5 unterscheidet ausdrücklich zwischen schema-lesbaren und gebunden-parsbaren Fixtures; das Corpusmanifest führt für jede Fixture entweder das vollständige originale kanonische Requestdokument oder eine ausdrücklich als rekonstruiert gekennzeichnete Kontextbeschreibung mit Begründung ihrer Ableitbarkeit, und die Akzeptanzkriterien von Slice 1 und Slice 3 verlangen Domänen-Lesergebnisse und Diagnosecodes nur noch für Fixtures der zweiten Kategorie.

NEW_FINDING: C-14 | BLOCKER | Der sichere Canary aus Abschnitt 4.5.7 und der Transportnachweis aus Abschnitt 4.5.5 schließen einander im deklarierten Änderungsumfang aus. Der produktive native Codex-Transport läuft mit `execution_root = config.repo_root.resolve()` als Arbeitsverzeichnis (`src/agent_runtime.py:981` und `src/agent_runtime.py:1077`) und mit fest verdrahtetem `--sandbox workspace-write` (`src/agent_adapters.py:420-437`); zusätzlich schreibt `prepare_native_provider_input()` Evidenzassets nach `PROJECT_ROOT` (`src/agent_adapters.py:406-415`). Nur der Reviewerpfad erhält eine isolierte Workspace (`src/agent_runtime.py:987-996`), Codex nicht. Ein Canary, der Abschnitt 4.5.7 einhält, kann `NativeCodexAdapter` und `agent_runtime` daher nicht unverändert verwenden und weist eine parallel implementierte Kommandozeile statt des Produktionstransports nach; ein Canary, der den Produktionstransport verwendet, macht dem Provider den echten Arbeitsbaum schreibbar. Weder `src/agent_adapters.py` noch `src/agent_runtime.py` stehen im Änderungspfad von Slice 3. Betroffen ist insbesondere der durch C-06 verpflichtend gewordene Codex-Final-Canary. | Slice 3 nimmt `src/agent_adapters.py` und `src/agent_runtime.py` in den Änderungspfad auf und führt eine explizite, getestete Canary-Ausführungsgrenze ein, die Arbeitsverzeichnis, Sandboxmodus und Evidenzassetziel des nativen Codex-Pfads parametrisiert; ein Akzeptanzkriterium belegt, dass der Canary dieselbe Adapter- und Runtimefunktion wie der Produktionslauf durchläuft und dabei kein Repositorypfad schreibbar ist.

NEW_FINDING: C-15 | OBSERVATION | Die Validierungsmatrix widerspricht Abschnitt 1 Nummer 7. Dort läuft nach Änderungen an Adapter, Schema, Runtime oder State die vollständige Suite; Abschnitt 7.1 und 7.2 führen jedoch nur vier beziehungsweise fünf Testdateien aus, obwohl beide Slices `src/agent_adapters.py` ändern. `tests/test_agent_runtime.py` ist die einzige Testdatei außerhalb der nativen Contract-Tests, die native Codex- und Claude-Requestbundles vollständig baut (`tests/test_agent_runtime.py:107` und `tests/test_agent_runtime.py:402`), steht aber erst im Änderungspfad und in der Matrix von Slice 3. Eine durch Slice 1 oder Slice 2 verursachte Regression dort bliebe bis zum letzten Slice unsichtbar und würde eine bereits erteilte Slice-Freigabe nachträglich entwerten. | Abschnitt 7.1 und 7.2 ergänzen `tests/test_agent_runtime.py`; disponierbar zu Beginn von Slice 1.

NEW_FINDING: C-16 | OBSERVATION | Die Capability-Tabelle ist das Ergebnis eines Live-Laufs, wird aber nicht an die Providerversion gebunden, mit der sie gemessen wurde. Abschnitt 3.3, Abschnitt 4.1.8 und das Slice-1-Akzeptanzkriterium verlangen nur „typisiert und versioniert“, was sich auf die Dateiversion `v1` bezieht. Die Adapter kennen bereits versionsgebundene Fähigkeitsprofile (`CapabilitySpec.supported_version_patterns`, `src/agent_adapters.py:500-503`); ein Providerupgrade entwertet die Sonde stillschweigend, und die Projektion würde weiterhin auf einer nicht mehr belegten Zusage aufbauen. | Die Capability-Tabelle führt je Provider Binärname, gemeldete CLI-Version und Sondendatum; eine Abweichung zur laufenden Version wird fail-closed abgewiesen oder erzwingt eine erneute Sonde; disponierbar in Slice 1.

NEW_FINDING: C-17 | OBSERVATION | Das aus C-11 übernommene Slice-1-Kriterium ist wörtlich erfüllbar, ohne den tatsächlichen Defekt zu beheben. `native_review_provider_response_schema()` mutiert das Ergebnis von `load_native_review_schema()` in place (`src/native_review_request.py:344-364`); harmlos ist das heute nur, weil der Loader die Datei bei jedem Aufruf neu liest (`src/native_review_contract.py:277-291`). Ein Test, der ein separat geladenes Basisschema vor und nach mehreren Projektionen vergleicht, bleibt deshalb auch dann grün, wenn die In-place-Mutation unverändert bestehen bleibt. `src/native_review_request.py` steht zudem nur im Änderungspfad von Slice 2, und Slice 2 besitzt kein entsprechendes Kriterium. | Slice 2 erhält ein Akzeptanzkriterium, das die Mutationsfreiheit an genau derselben geladenen Schemainstanz nachweist, die der Projektion übergeben wurde; disponierbar in Slice 2.

PLAN_APPROVAL: NO

STATUS: DONE

### Entscheidung

Die Revision ist substanziell und trifft den Kern der Vorbefunde: C-01 bis
C-08, C-10 und C-11 sind sachlich geschlossen, die vorgezogene Subset-Sonde,
die ab Slice 1 geführte Ausnahmeliste, die abschließenden
Pflichtmutationsklassen und die unveränderlichen Reader-Baselines beseitigen
die im Vorreview benannte Nachweislücke.

Freigegeben wird der Plan dennoch nicht. C-09 ist nicht korrekt geschlossen,
und drei neue Blocker betreffen die Ausführbarkeit der Revision selbst: der
Claude-Astdiskriminator ist aus dem digestgebundenen Kontext nicht erreichbar
(C-12), der Corpusnachweis kann die ihm zugewiesenen Domänenaussagen mangels
persistierter Requestdokumente nicht erbringen (C-13), und der geforderte
sichere Canary ist mit dem tatsächlichen produktiven Codex-Transport innerhalb
der deklarierten Änderungspfade unvereinbar (C-14). C-15 bis C-17 sind
Observations und dürfen eine spätere Freigabe begleiten; sie sind Slice 1
beziehungsweise Slice 2 zugeordnet und dort prüfbar disponiert.

Die Implementierung beginnt erst nach Schließung von C-09 sowie C-12 bis C-14
und erneuter Vorlage dieser Planfassung.

## 13. Disposition des Claude-Planreviews – Revision 2

Die von Claude in Abschnitt 12 bestätigten Schließungen `C-01` bis `C-08`,
`C-10` und `C-11` bleiben unverändert maßgeblich. Die noch offene Observation
und die neuen Befunde werden für die dritte Planfassung wie folgt disponiert:

| Finding | Einstufung | Disposition in Revision 3 |
|---|---|---|
| `C-09` | OBSERVATION | **ACCEPTED.** Abschnitt 4.3.1 und Slice 2 verwenden nur noch `approval_marker`, `allow_new_observations`, `round_number` und `anchor_origin` aus `NativeReviewContext`. `NativeReviewKind` und `operation` sind ausdrücklich keine Projektionsselektoren. |
| `C-12` | BLOCKER | **ACCEPTED.** Die nicht erfüllbare Prüfung einer Kombination aus äußerem `NativeReviewKind` und gebundenem Kontext wurde gestrichen. Damit ist weder eine Änderung von `NativeReviewContext` noch des Review-Request-v1-Schemas oder bestehender Request-IDs nötig. Die Projektion wird vollständig aus den bereits gebundenen typisierten Kontextfeldern bestimmt. |
| `C-13` | BLOCKER | **ACCEPTED.** Abschnitt 3.4 und 4.5 unterscheiden nun `schema_only` und `request_bound`. Historische Antwortbytes ohne Originalrequest belegen ausschließlich Digest und allgemeine Schema-Lesbarkeit. Domänenresultate und Diagnosecodes werden nur für vollständig originale, digestverifizierte Request-/Response-Paare verlangt; synthetische historische Kontexte sind verboten. |
| `C-14` | BLOCKER | **ACCEPTED.** Slice 3 umfasst nun `src/agent_adapters.py`, `src/agent_runtime.py` und ihre Tests. Eine typisierte Ausführungsgrenze parametrisiert Arbeitsverzeichnis, Sandbox und Assetziel; der Canary nutzt dieselbe Adapter-/Runtimefunktion wie die Produktion, ohne dem Provider einen Repositorypfad schreibbar zu machen. |
| `C-15` | OBSERVATION | **ACCEPTED.** `tests/test_agent_runtime.py` gehört nun zu Slice 1 und Slice 2. Beide Slice-Matrizen führen außerdem unmittelbar vor dem jeweiligen Claude-Review die vollständige Repositorysuite aus. |
| `C-16` | OBSERVATION | **ACCEPTED.** Capability-Einträge binden Provider, Binärname, gemeldete CLI-Version, Transportprofil und Sondendatum. Eine Versionsabweichung verhindert fail-closed die Projektion und erzwingt eine erneute Sonde. |
| `C-17` | OBSERVATION | **ACCEPTED.** Slice 2 prüft jetzt gezielt dieselbe geladene Review-Basisschemainstanz vor, zwischen und nach mehreren Projektionen. Damit kann der Test die heutige In-place-Mutation tatsächlich erkennen. |

Diese Dispositionen ändern keine Produktivdatei und starten keine
Implementierung. `C-09` und `C-12` bis `C-17` gelten erst nach Claudes erneutem
read-only Review dieser dritten Planfassung als geschlossen.

## 14. Claude-Planreview – Revision 3 vom 24. August 2026

### Eingefrorener Reviewgegenstand

- Arbeitsplan: `docs/internal/native-agent-contract-closure-arbeitsplan.md`,
  Revision 3 (Abschnitte 1 bis 13)
- Basiscommit: `c410eef95164d10b1ff0d97d2a9593ca5af18324`
- Reviewhistorie: Abschnitt 12 (Revision-2-Review), Abschnitt 13
  (Dispositionen), `docs/internal/native-agent-contract-closure-review.md`
  (Erstreview)
- Anforderungsquelle: `/tmp/orchestrator-stabilisierung-1-1-nachtraege.md`,
  ausschließlich der Abschnitt **Contract Closure**
- Reviewer: Claude
- Modus: direktes read-only Repositoryreview ohne Testausführung, ohne
  Bootstrap, ohne Änderung an Produktivcode, Tests oder den normativen
  Abschnitten 1 bis 11 und 13
- Zusätzlich geprüfter Repositorystand gegenüber Revision 2:
  `.orchestrator/logs/manual-*-review/request*.json`,
  `.orchestrator/artifacts/*/native-codex-responses/`,
  `docs/internal/archive/native-codex-claude-correction-loop/*.raw.json`,
  `.gitignore`, `src/native_review_contract.py`
  (`native_review_context_binding`), `src/native_codex_request.py`
  (`_require_internal_asset_path`), `src/agent_runtime.py`,
  `src/agent_adapters.py`

### Textuelles Reviewergebnis

REVIEWER: claude

FINDING_STATUS: C-09 | CLOSED | Abschnitt 4.3.1 benennt `approval_marker` als einzigen primären Astdiskriminator und lässt `allow_new_observations`, `round_number` und `anchor_origin` die Slice-Rundenpolicy differenzieren; `NativeReviewKind` und der freie String `operation` sind ausdrücklich als Selektoren verboten. Alle vier Felder sind reale Felder von `NativeReviewContext` (`src/native_review_contract.py:148-156`) und zugleich Bestandteil von `native_review_context_binding()` (`src/native_review_contract.py:828-850`), also bereits digestgebunden. Slice 2 Schritt 1 setzt die Regel um, und das neue Akzeptanzkriterium ist prüfbar: zwei Kontexte, die sich nur im freien `operation`-String unterscheiden, müssen bytegleich dasselbe Writerschema liefern — konstruierbar, weil `NativeReviewContext` `operation` nur auf Nichtleere prüft.

FINDING_STATUS: C-12 | CLOSED | Das nicht erfüllbare Kriterium zur Kombination aus äußerem `NativeReviewKind` und gebundenem Kontext ist entfernt. Alle in Abschnitt 4.3 genannten Projektionsentscheidungen sind aus Feldern ableitbar, die `NativeReviewRequestBundle.provider_response_schema` über `self.bound_context.context` bereits erreicht (`src/native_review_request.py:280-285`): `approval_marker`, `allow_new_observations`, `round_number`, `anchor_origin`, `previous_findings`, `validation_attestation`, `test_files`, `test_changes_approved`. Weder `NativeReviewContext` noch `schemas/native-agent-review-request-v1.schema.json` noch die Digestbildung müssen geändert werden; keine der beiden Dateien steht folgerichtig in einem Änderungspfad. Bestehende Request-IDs bleiben unberührt: die Recordkette speichert `request_id` nur als opaken Wert (`review`- und `agent_result`-Payloads), und kein Pfad rekonstruiert eine historische Request-ID zum Vergleich.

FINDING_STATUS: C-13 | CLOSED | Abschnitt 3.4, 4.5.2, 4.5.3 und Slice 3 Schritt 2 führen die Klassen `schema_only` und `request_bound` sauber ein. `schema_only` belegt ausschließlich Byte-Digest und Lesbarkeit gegen das unveränderte Resultat-v1-Schema; gebundene Domänen-Lesergebnisse und stabile Diagnosecodes werden ausschließlich für `request_bound`-Paare mit originalem, digestverifiziertem Requestdokument behauptet; die Hochstufung ohne Originalrequest ist verboten und synthetische oder vermutete historische Kontexte sind ausdrücklich untersagt. Die Akzeptanzkriterien von Slice 1, Slice 2 und Slice 3 sind entsprechend abgeschwächt. Der Mechanismus ist damit korrekt und sicher; seine falsche Verfügbarkeitsannahme ist als C-18 neu erfasst.

FINDING_STATUS: C-14 | CLOSED | Slice 3 führt `src/agent_adapters.py`, `src/agent_runtime.py`, `tests/test_agent_adapters.py` und `tests/test_agent_runtime.py` im exakten Änderungspfad. Schritt 6 führt eine typisierte Ausführungsgrenze ein, die Ausführungsverzeichnis, Sandboxmodus und Evidenzassetziel parametrisiert, den Produktionsmodus auf seinen bisherigen Defaults belässt und für den Canarymodus wegwerfbares Verzeichnis, restriktivsten Sandboxmodus und ein Assetziel außerhalb des Repositorys erzwingt; eine parallele Codex-Kommandozeile ist verboten. Das Akzeptanzkriterium verlangt denselben Adapter- und Runtimepfad wie in der Produktion und Tests, die belegen, dass keiner der drei Parameter auf einen Repositorypfad zeigt. Das ist mit dem Code vereinbar: nur `execution_root` (`src/agent_runtime.py:981`, `src/agent_runtime.py:1077`), das feste `--sandbox workspace-write` (`src/agent_adapters.py:420-437`) und die `PROJECT_ROOT`-Verknüpfung der Evidenzassets (`src/agent_adapters.py:406-415`) müssen parametrisiert werden; die Pfadregel `_require_internal_asset_path()` (`src/native_codex_request.py:428`) bleibt unberührt, weil nur die Wurzel und nicht der relative Assetpfad variiert.

FINDING_STATUS: C-15 | CLOSED | `tests/test_agent_runtime.py` steht jetzt im Änderungspfad von Slice 1 und Slice 2, und die Abschnitte 7.1, 7.2 und 7.3 führen jeweils zusätzlich `python3 -m pytest tests/ -v` vor dem jeweiligen Claude-Review aus. Der Widerspruch zu Abschnitt 1 Nummer 7 ist damit aufgelöst, und eine durch Slice 1 oder Slice 2 verursachte Regression in `tests/test_agent_runtime.py:107` beziehungsweise `tests/test_agent_runtime.py:402` wird vor der jeweiligen Slice-Freigabe sichtbar.

FINDING_STATUS: C-16 | CLOSED | Abschnitt 4.1.8 bindet Capability-Einträge zusätzlich an Providerbinärname, gemeldete CLI-Version, Transportprofil und Sondendatum und macht einen Versionsunterschied fail-closed. Slice 1 Schritt 1 konkretisiert dieselben Felder je Schemafeature, Stopbedingung 3 deckt die nicht abschließbare Neusonde ab, und das Slice-1-Akzeptanzkriterium ist ausführbar: ein Test simuliert eine abweichende CLI-Version und weist nach, dass keine Projektion mit veralteten Zusagen gebaut wird. Die dafür nötigen Dateien — `schemas/native-provider-schema-capabilities-v1.json`, `src/native_provider_schema.py` und `src/agent_adapters.py`, das die Versionserkennung über `CapabilitySpec` bereits besitzt — liegen alle im Änderungspfad von Slice 1. Die verbleibende Profilfrage ist als C-21 erfasst.

FINDING_STATUS: C-17 | CLOSED | Das Slice-2-Akzeptanzkriterium verlangt jetzt, dass ein Test dieselbe geladene Review-Basisschemainstanz nacheinander an mehrere unterschiedliche Projektionen übergibt und ihre kanonische Bytegleichheit vor und nach jedem Aufruf nachweist. Genau das erkennt die heutige In-place-Mutation in `native_review_provider_response_schema()` (`src/native_review_request.py:344-364`), die bisher nur deshalb folgenlos bleibt, weil `load_native_review_schema()` die Datei bei jedem Aufruf neu liest (`src/native_review_contract.py:277-291`). Das Kriterium ist innerhalb des Änderungspfads von Slice 2 ausführbar, weil `src/native_provider_schema.py` und `src/native_review_request.py` dort liegen und der Projektionskern die Basisschemainstanz als Eingabe entgegennehmen kann.

FINDING_STATUS: C-01 | CLOSED | Regressionsgeprüft. Abschnitt 4.2.7, 4.2.8, 4.3.9, 4.3.10, Slice 1 Schritt 4, Slice 2 Schritt 3 sowie die zugehörigen Akzeptanzkriterien und Abnahmekriterium 4 sind unverändert vorhanden.

FINDING_STATUS: C-02 | CLOSED | Regressionsgeprüft. Abschnitt 3.3, Slice 1 Schritt 1 und Stopbedingung 2 halten die vorgezogene Subset-Sonde unverändert; Revision 3 verstärkt sie zusätzlich um die Versionsbindung.

FINDING_STATUS: C-03 | CLOSED | Regressionsgeprüft. `schemas/native-provider-schema-exceptions-v1.json` bleibt Neuanlage in Slice 1, Bestandteil des Änderungspfads von Slice 2 und in Slice 3 nur Prüfgegenstand; Abschnitt 4.4.8 und Stopbedingung 6 sind unverändert.

FINDING_STATUS: C-04 | CLOSED | Regressionsgeprüft. Beide Resultat-v1-Schemadateien fehlen weiterhin in allen drei Änderungspfaden; Abschnitt 4.1.7 und Abnahmekriterium 10 sind unverändert. Slice 1 präzisiert das Bytegleichheitskriterium nun korrekt auf dieselbe übergebene Codex-Basisschemainstanz.

FINDING_STATUS: C-05 | CLOSED | Regressionsgeprüft. Abschnitt 4.4.6 bis 4.4.8 mit abschließenden Pflichtmutationsklassen, Mindestabdeckung je Requestart und roter Negativkontrolle ist unverändert; Slice 3 Schritt 3 setzt sie um.

FINDING_STATUS: C-06 | CLOSED | Regressionsgeprüft. Abschnitt 3.4 und 4.5.4 halten den eigenen Codex-Final-Canary fest, Slice 3 besitzt das dedizierte Akzeptanzkriterium, und Stopbedingung 7 bleibt bestehen. Die Nachweisführung „Corpus oder Canary“ ist jedoch durch die neue Fixtureklassifikation aufgeweicht; das ist als C-19 erfasst.

FINDING_STATUS: C-07 | CLOSED | Regressionsgeprüft. Abschnitt 4.5.7, Stopbedingung 8 und das Slice-3-Kriterium zur bytegleichen `git status --porcelain=v1 --untracked-files=all`-Ausgabe sind unverändert; Revision 3 ergänzt die konkrete typisierte Ausführungsgrenze.

FINDING_STATUS: C-08 | CLOSED | Regressionsgeprüft. Alle Neuanlagen bleiben markiert, `scripts/` bleibt als neues Wurzelverzeichnis benannt, und die Änderungspfade wurden für C-14 und C-15 korrekt erweitert. Eine verbleibende Pfadlücke betrifft die Codex-Corpusfixtures und ist als C-20 erfasst.

FINDING_STATUS: C-10 | CLOSED | Regressionsgeprüft. Abschnitt 4.1.6, Slice 1 Schritt 7, Slice 2 Schritt 7, beide Hüllen-Akzeptanzkriterien und Abnahmekriterium 10 sind unverändert.

FINDING_STATUS: C-11 | CLOSED | Regressionsgeprüft. Abschnitt 4.1.3 und 4.1.7 bleiben bestehen; Slice 1 prüft die Codex-Basisschemainstanz, Slice 2 zusätzlich die Review-Basisschemainstanz nach C-17.

NEW_FINDING: C-18 | BLOCKER | Die Verfügbarkeitsaussage von Abschnitt 3.4 ist am Repository widerlegt. Der Plan behauptet: „Im derzeitigen Artefaktbestand fehlen außerdem die vollständigen kanonischen Requestdokumente zu den historischen Antwortbytes“ und leitet daraus ab, dass Antwortfixtures „deshalb nur `schema_only`“ sind. Tatsächlich liegen unter `.orchestrator/logs/manual-*-review/request*.json` zwölf vollständige kanonische Requestdokumente, von denen drei über ihre `request_id` exakt zu archivierten Antwortbytes passen: `.orchestrator/logs/manual-package5-slice1-review/request.json` zu `docs/internal/archive/native-codex-claude-correction-loop/native-codex-claude-correction-loop-slice1-review-round2.raw.json`, `.orchestrator/logs/manual-package5-slice2-c05-review/request.json` zu `native-codex-claude-correction-loop-slice2-c05-review.raw.json` und `.orchestrator/logs/manual-package5-slice2-review/request.json` zu `native-codex-claude-correction-loop-slice2-review.raw.json`. Für das dritte Paar wurde die Bindung nachgerechnet: `schema_version=native-agent-review-request-v1`, und der SHA-256 über das kanonische Dokument ohne `request_id` ergibt exakt die gespeicherte `request_id`. Der `review_contract`-Block dieser Requests enthält `approval_marker`, `allow_new_observations`, `round_number`, `anchor_origin`, `previous_findings`, `slice_id`, `test_files`, `test_changes_approved`, `validation_attestation` und `validation_command_prefixes`, also den vollständigen `NativeReviewContext` zur Rekonstruktion eines echten `BoundNativeReviewContext`. Slice 3 Schritt 1 inventarisiert jedoch ausschließlich „Rohantworten“, und das Vollständigkeitskriterium bezieht sich ebenfalls nur auf Antworten. Der Plan verwirft damit genau die drei Fixturepaare, die als einzige gebundene Domänen-Lesergebnisse und stabile Diagnosecodes belegen könnten, und lässt die Forderung der Anforderungsquelle nach stabilen Diagnosecodes für bekannte Ablehnungsfälle vollständig unerfüllt. | Abschnitt 3.4 wird auf den belegten Bestand korrigiert; Slice 3 Schritt 1 und das Vollständigkeitskriterium inventarisieren zusätzlich alle auffindbaren kanonischen Requestdokumente, paaren sie über `request_id` mit vorhandenen Antwortbytes, verifizieren die Digestbindung und übernehmen jedes so entstandene Paar als `request_bound`-Fixture; die Zahl der übernommenen `request_bound`-Paare wird im Manifest ausgewiesen und darf die beim Slice-Start auffindbaren Paare nicht unterschreiten.

NEW_FINDING: C-19 | BLOCKER | Die durch C-13 eingeführte Fixtureklassifikation entwertet den Writerform-Nachweis, ohne dass Abschnitt 4.5.6, Abnahmekriterium 8, die entsprechende Stopbedingung und das Slice-3-Akzeptanzkriterium nachgezogen wurden. Alle vier sagen weiterhin „entweder mindestens ein verifiziertes Corpusfixture oder ein erfolgreicher Live-Canary“. Ein `schema_only`-Fixture belegt nach Abschnitt 4.5.2 aber ausdrücklich nur Byte-Digest und Lesbarkeit gegen das allgemeine Resultat-v1-Schema und kann gegen eine request-spezifische Writerform gar nicht validiert werden, weil ihm genau der Kontext fehlt, aus dem diese Writerform entsteht. Damit darf eine neu erzeugte Writerform formal als belegt gelten, obwohl kein einziger Nachweis existiert, dass ein Provider sie akzeptiert oder dass eine reale Antwort sie erfüllt — genau die Lücke, deren Schließung C-06 erzwungen hat. | Abschnitt 4.5.6, Abnahmekriterium 8, die Writerform-Stopbedingung und das Slice-3-Akzeptanzkriterium lassen als Writerform-Nachweis ausschließlich ein `request_bound`-Fixture oder einen erfolgreichen Live-Canary zu; `schema_only`-Fixtures sind dort ausdrücklich ausgeschlossen, und das Manifest weist je Writerform den tragenden Nachweistyp aus.

NEW_FINDING: C-20 | BLOCKER | Ein Akzeptanzkriterium von Slice 1 ist im deklarierten Änderungspfad nicht reproduzierbar ausführbar. Es verlangt: „Alle historischen `schema_only`-Codexfixtures bleiben gegen das unveränderte Resultat-v1-Schema lesbar und behalten ihren Byte-Digest.“ Native Codex-Rohantworten existieren jedoch ausschließlich unter `.orchestrator/artifacts/*/native-codex-responses/`, und `.orchestrator/` ist in `.gitignore` ausgeschlossen; die im Repository versionierten Rohantworten unter `docs/internal/archive/` sind ausnahmslos Claude-Reviewresultate. Die Corpusfixtures entstehen erst in Slice 3 (`tests/fixtures/native-contract-corpus/`), und Slice 1 führt weder diese Dateien noch einen Corpusloader im Änderungspfad. Ein in Slice 1 geschriebener Test wäre lokal grün und in jedem frischen Klon rot, obwohl Abschnitt 7.1 nach Slice 1 eine grüne vollständige Suite verlangt — und Abschnitt 3.4 fordert die Fixtureübernahme genau deshalb ausdrücklich. Für Slice 2 besteht das Problem nicht, weil die Claude-Rohantworten versioniert sind. | Entweder werden die benötigten Codex-`schema_only`-Fixtures samt Manifestausschnitt und Loader in den Änderungspfad von Slice 1 aufgenommen, oder das Kriterium wird ersatzlos nach Slice 3 verschoben; in beiden Fällen enthält Slice 1 keine Testaussage mehr, die von nicht versionierten `.orchestrator`-Pfaden abhängt, und ein Test belegt, dass die Slice-1-Suite ohne vorhandenes `.orchestrator`-Verzeichnis grün bleibt.

NEW_FINDING: C-21 | OBSERVATION | Die Regel aus Slice 3 Schritt 6 „Das Probe-Werkzeug ruft dieselbe Adapter- und Runtimefunktion auf und baut keine parallele Codex-Kommandozeile“ gilt unbeschränkt für `scripts/native_contract_probe.py`, kollidiert aber mit dem Zweck desselben Werkzeugs in Slice 1. Die Subset-Sonde muss beliebige Kandidatenschemata prüfen und kann deshalb `NativeCodexAdapter.prepare_native_provider_input()` nicht verwenden, das zwingend ein vollständiges `NativeCodexRequestBundle` verlangt; zudem existiert die typisierte Ausführungsgrenze erst in Slice 3, während `src/agent_runtime.py` nicht im Änderungspfad von Slice 1 steht. Die Sonde wird also notwendigerweise eine eigene minimale Kommandozeile bauen. Damit misst sie ein anderes Transportprofil als der Produktionsaufruf, der zusätzlich `--ephemeral`, `--skip-git-repo-check`, Modell und `model_reasoning_effort` setzt (`src/agent_adapters.py:420-437`); Abschnitt 4.1.8 bindet das Transportprofil zwar an den Capability-Eintrag, macht aber nur eine Versionsabweichung fail-closed, keine Profilabweichung. | Slice 3 Schritt 6 beschränkt die Regel ausdrücklich auf den Canarymodus; Slice 1 hält fest, dass die Subset-Sonde eine eigene minimale Invokation verwendet, dabei aber dasselbe Transportprofil wie der native Produktionsaufruf setzt, und Abschnitt 4.1.8 macht auch eine Profilabweichung fail-closed; disponierbar in Slice 1.

PLAN_APPROVAL: NO

STATUS: DONE

### Entscheidung

Revision 3 schließt alle sieben in Abschnitt 12 offenen beziehungsweise neuen
Befunde sachlich: `C-09` und `C-12` sind ohne jede Änderung an
`NativeReviewContext`, Review-Request-v1-Schema, Requestdigest oder
bestehenden Request-IDs gelöst, `C-13` führt eine korrekte und sichere
Trennung zwischen `schema_only` und `request_bound` ein, `C-14` erhält eine
konkrete typisierte Ausführungsgrenze mit vollständigem Änderungspfad, und
`C-15` bis `C-17` sind innerhalb ihrer Slices ausführbar disponiert. Die
Regressionsprüfung zeigt keine Beschädigung von `C-01` bis `C-08`, `C-10` und
`C-11`.

Freigegeben wird der Plan dennoch nicht. Drei neue Blocker betreffen den
Corpus- und Nachweisteil, den Revision 3 gerade neu gefasst hat: die
Verfügbarkeitsaussage in Abschnitt 3.4 ist am Repository widerlegt und verwirft
drei nachweisbar vorhandene `request_bound`-Paare (C-18); die
Writerform-Nachweisregel wurde nicht auf die neue Fixtureklassifikation
nachgezogen und lässt einen `schema_only`-Digest als Beleg genügen (C-19); und
ein Slice-1-Akzeptanzkriterium hängt an nicht versionierten
`.orchestrator`-Pfaden, deren Fixtureübernahme erst Slice 3 leistet (C-20).
C-21 ist eine Observation und darf eine spätere Freigabe begleiten; sie ist
Slice 1 zugeordnet und dort prüfbar disponiert.

Die Implementierung beginnt erst nach Schließung von C-18 bis C-20 und
erneuter Vorlage dieser Planfassung.

## 15. Disposition des Claude-Planreviews – Revision 3

Claude hat in Abschnitt 14 sämtliche früheren Befunde `C-01` bis `C-17`
geschlossen beziehungsweise regressionsgeprüft. Die drei neuen Corpusblocker
und die Probe-Observation werden für Revision 4 wie folgt disponiert:

| Finding | Einstufung | Disposition in Revision 4 |
|---|---|---|
| `C-18` | BLOCKER | **ACCEPTED.** Abschnitt 3.4 nennt den tatsächlich vorhandenen Requestbestand und die drei bereits belegten Request-/Response-Paare. Slice 3 inventarisiert jetzt Requests und Antworten, verifiziert die kanonische Requestdigestbindung, übernimmt jedes auffindbare Paar als `request_bound` und weist gefundene sowie übernommene Paarzahl im Manifest aus. |
| `C-19` | BLOCKER | **ACCEPTED.** Abschnitt 4.5.6, die Stopbedingung, Slice 3 und das Gesamt-Abnahmekriterium akzeptieren als Writerform-Nachweis ausschließlich ein `request_bound`-Fixturepaar oder einen erfolgreichen Live-Canary. `schema_only` ist überall ausdrücklich ausgeschlossen. |
| `C-20` | BLOCKER | **ACCEPTED.** Das nicht reproduzierbare Codex-Corpuskriterium wurde aus Slice 1 entfernt und nach Slice 3 verschoben. Slice 1 enthält zusätzlich den Negativnachweis, dass seine vollständige Matrix ohne `.orchestrator`-Verzeichnis grün bleibt. |
| `C-21` | OBSERVATION | **ACCEPTED.** Slice 1 dokumentiert die notwendige minimale Subset-Invokation und bindet sie an dasselbe normalisierte Produktions-Transportprofil. Die Pflicht zur identischen Adapter-/Runtimefunktion ist in Slice 3 ausdrücklich auf den Canarymodus beschränkt. Versions- und Profilabweichungen erzwingen gleichermaßen fail-closed eine neue Sonde. |

Diese Änderungen betreffen weiterhin ausschließlich den Arbeitsplan. Die
Implementierung beginnt erst, wenn Claude `C-18` bis `C-21` in einem erneuten
read-only Review geschlossen und den Plan freigegeben hat.

## 16. Claude-Planreview – Revision 4 vom 24. August 2026

### Eingefrorener Reviewgegenstand

- Arbeitsplan: `docs/internal/native-agent-contract-closure-arbeitsplan.md`,
  Revision 4 (Abschnitte 1 bis 15)
- Basiscommit: `c410eef95164d10b1ff0d97d2a9593ca5af18324`
- Reviewhistorie: Abschnitte 11 bis 15 sowie
  `docs/internal/native-agent-contract-closure-review.md`
- Anforderungsquelle: `/tmp/orchestrator-stabilisierung-1-1-nachtraege.md`,
  ausschließlich der Abschnitt **Contract Closure**
- Reviewer: Claude
- Modus: direktes read-only Repositoryreview ohne Testausführung, ohne
  Bootstrap, ohne Änderung an Produktivcode, Tests oder den normativen
  Abschnitten 1 bis 13 und 15
- Zusätzlich geprüfter Repositorystand gegenüber Revision 3:
  `.orchestrator/logs/*/request*.json` (Vollinventur, 12 Dokumente),
  `.orchestrator/artifacts/*/native-codex-responses/`,
  `docs/internal/archive/native-codex-claude-correction-loop/*.raw.json`,
  `src/agent_adapters.py:406-437` (native Codex-Kommandozeile),
  `src/agent_runtime.py:981-996`, `tests/*.py` auf Abhängigkeiten vom realen
  `.orchestrator`-Verzeichnis

### Textuelles Reviewergebnis

REVIEWER: claude

FINDING_STATUS: C-18 | CLOSED | Abschnitt 3.4 beschreibt den Bestand jetzt korrekt und nennt die drei Paare namentlich und richtig zugeordnet; die Zuordnung ist am Repository nachgeprüft. Eine Vollinventur aller `.orchestrator/**/*.json` bestätigt zusätzlich, dass genau zwölf kanonische Requestdokumente existieren, sämtlich `schema_version=native-agent-review-request-v1`, und dass kein einziges natives Codex-Requestdokument persistiert ist; der in Abschnitt 3.4 und Slice 3 verwendete Glob `.orchestrator/logs/manual-*-review/request*.json` erfasst damit den vollständigen vorhandenen Bestand. Slice 3 Schritt 1 inventarisiert Requests und Antworten, paart über `request_id`, rechnet die kanonische Requestdigestbindung nach und übernimmt jedes vollständig belegte Paar als `request_bound`; das Akzeptanzkriterium weist gefundene und übernommene Paarzahl aus, verbietet eine kleinere übernommene Zahl und setzt die drei belegten Paare als Untergrenze. Die Rekonstruierbarkeit ist gedeckt: der `review_contract`-Block der Requestdokumente führt `approval_marker`, `allow_new_observations`, `round_number`, `anchor_origin`, `previous_findings`, `slice_id`, `test_files`, `test_changes_approved`, `validation_attestation` und `validation_command_prefixes`, das Top-Level zusätzlich `run_id`, `work_unit_id`, `operation`, `current_fingerprint` und `reviewer` — also alle fünfzehn Felder von `native_review_context_binding()`.

FINDING_STATUS: C-19 | CLOSED | Der Ausschluss ist an allen vier normativen Stellen konsistent nachgezogen: Abschnitt 4.5.6 („Ein `schema_only`-Fixture ist ausdrücklich kein Writerform-Nachweis“), die Writerform-Stopbedingung in Abschnitt 8 („`schema_only`-Fixtures erfüllen diese Stopbedingung ausdrücklich nicht“), Gesamt-Abnahmekriterium 8 („`schema_only` ist kein Writerbeleg“) und das Slice-3-Akzeptanzkriterium („`schema_only` zählt dafür nicht“). Abschnitt 4.5.6 verlangt zusätzlich, dass das Manifest je Writerform den tragenden Nachweistyp ausweist. Kein verbliebener Textteil lässt `schema_only` als Writerform-Nachweis zu. Dass der als zulässig erklärte `request_bound`-Nachweis inhaltlich nicht abgesichert ist, ist als C-22 neu erfasst.

FINDING_STATUS: C-20 | CLOSED | Das von gitignorierten Codex-Artefakten abhängige Kriterium ist vollständig aus Slice 1 entfernt; die Codex-Corpusübernahme und die historische Antwortprüfung finden ausschließlich in Slice 3 Schritt 1 und 2 statt. Slice 1 trägt stattdessen den Negativnachweis „Kein Slice-1-Test liest `.orchestrator/` oder hängt von einem lokalen, gitignorierten Corpus ab; die vollständige Slice-1-Matrix bleibt auch bei vollständig fehlendem `.orchestrator`-Verzeichnis grün“. Dieses Kriterium ist heute erfüllbar: eine Durchsicht aller `tests/*.py` zeigt, dass jede `.orchestrator`-Erwähnung entweder ein String-Literal oder eine unter `tmp_path` erzeugte Struktur ist und kein Test das reale Repository-`.orchestrator` liest. Weder der exakte Änderungspfad noch die Validierungsmatrix 7.1 setzen ein lokales Artefaktverzeichnis voraus.

FINDING_STATUS: C-21 | OPEN | Die Trennung von Subset-Sonde und Canarymodus ist widerspruchsfrei gelöst: Slice 3 Schritt 6 beschränkt die Gleichheitspflicht für Adapter- und Runtimefunktion ausdrücklich auf Canaries, und Slice 1 Schritt 1 erlaubt der Sonde ausdrücklich eine eigene minimale Invokation für beliebige Kandidatenschemata; die Sonde hängt an keiner Stelle von der erst in Slice 3 eingeführten Canary-Ausführungsgrenze ab. Der zweite Teil des Schließungskriteriums ist jedoch nicht erfüllt: die beiden Transportprofile sind nicht prüfbar gebunden. Slice 1 Schritt 1 verlangt von der Sonde „Modell, Reasoning-/Effortprofil, Schemaübergabe und alle übrigen semantisch relevanten CLI-Flags bytegenau wie der jeweilige native Produktionsaufruf“, während Abschnitt 4.5.7 für dieselbe Sonde die restriktivste Sandbox und ein separates Arbeitsverzeichnis vorschreibt. Der Feldumfang des „normalisierten Transportprofils“ wird nirgends definiert, obwohl Abschnitt 4.1.8 und Abschnitt 8 einen Profilunterschied fail-closed machen. Die daraus folgende Verklemmung ist als C-23 erfasst.

NEW_FINDING: C-22 | BLOCKER | Der durch C-19 als einzig zulässiger Corpusnachweis eingesetzte `request_bound`-Typ wird nirgends verpflichtet, tatsächlich gegen die Writerform validiert zu werden, die er belegen soll. Slice 3 Schritt 2 prüft für `request_bound` nur „das originale kanonische Requestdokument, seine Request-ID-/Digestbindung, Domänen-Lesergebnis und gegebenenfalls den stabilen Fehlercode“; Abschnitt 4.5.2 und die Slice-3-Akzeptanzkriterien nennen ebenfalls ausschließlich Ergebnisse des lokalen Parsers. Keine Stelle verlangt, die historischen Antwortbytes gegen das aus dem rekonstruierten gebundenen Kontext erzeugte Writerschema zu validieren. Damit erfüllt ein `request_bound`-Paar Abschnitt 4.5.6, die Writerform-Stopbedingung und Gesamt-Abnahmekriterium 8 allein durch seine Existenz, ohne dass je geprüft wurde, ob die neue Writerform diese reale Antwort überhaupt darstellen kann — die Lücke, die C-19 eine Ebene höher schließen sollte, öffnet sich damit unverändert wieder. Zugleich fehlt die Behandlung des realistischen Gegenfalls: verletzt eine historisch lokal gültige Antwort die neue Writerform, ist das der eigentliche Falsifikationsbefund von Contract Closure, und der Plan kennt dafür weder Akzeptanzkriterium noch Ausnahmeeintrag noch Stopbedingung. | Abschnitt 4.5.2 und 4.5.6 sowie Slice 3 Schritt 2 verlangen, dass jedes `request_bound`-Paar zusätzlich gegen das aus seinem rekonstruierten gebundenen Kontext erzeugte Writerschema validiert wird und nur bei Erfolg als Writerform-Nachweis zählt; ein Fehlschlag ist entweder ein Writerschemadefekt und löst die Stopbedingung aus oder wird mit Providerbeleg und Regressionstest in der typisierten Ausnahmeliste registriert. Eine entsprechende Stopbedingung wird in Abschnitt 8 ergänzt.

NEW_FINDING: C-23 | BLOCKER | Das „normalisierte Transportprofil“ ist zugleich normativ scharf und inhaltlich undefiniert, und seine wörtliche Lesart verklemmt den Produktionsbetrieb. Abschnitt 4.1.8 und Abschnitt 8 machen einen Transportprofilunterschied fail-closed; Slice 1 Schritt 1 definiert das Profil der Sonde als „bytegenau wie der jeweilige native Produktionsaufruf“ in allen semantisch relevanten CLI-Flags; Abschnitt 4.5.7 verlangt für dieselbe Sonde zwingend eine andere Sandbox und ein anderes Arbeitsverzeichnis. Die produktive native Codex-Kommandozeile lautet `codex exec --model <modell> --config model_reasoning_effort="<effort>" --skip-git-repo-check --ephemeral --sandbox workspace-write --color never --json --output-schema <pfad> --output-last-message <pfad> -` (`src/agent_adapters.py:420-437`), wobei beide Pfadargumente aus einem je Aufruf neu erzeugten `tempfile.mkdtemp()`-Verzeichnis stammen (`src/agent_adapters.py:196-207`). Bytegenaue Gleichheit ist damit für mindestens drei Argumente prinzipiell unmöglich, und für `--sandbox` sogar ausdrücklich verboten. Wird das Profil naiv als vollständige Kommandozeile gebunden, weicht jeder Produktionsaufruf vom persistierten Sondenprofil ab und die Capability-Tabelle wird fail-closed abgewiesen — es ließe sich dann überhaupt keine Projektion mehr bauen. Wird es beliebig locker interpretiert, ist die Fail-closed-Regel wirkungslos. | Abschnitt 4.1.8 definiert den Feldumfang des normalisierten Transportprofils abschließend und maschinenlesbar — enthalten sind Providerbinärname, Modell, Reasoning-/Effortwert, Schemaübergabemechanismus und die semantisch relevanten Restflags; ausgenommen sind ausdrücklich Sandboxmodus, Arbeitsverzeichnis und alle laufspezifischen temporären Dateipfade. Slice 1 formuliert die Sondenpflicht auf genau diesen normalisierten Feldumfang statt auf bytegenaue Kommandozeilengleichheit, und ein Test weist nach, dass das Profil eines echten Produktionsaufrufs und das Profil einer Sonde bei identischer Konfiguration gleich normalisiert werden, bei abweichendem Modell oder Effort dagegen fail-closed auseinanderfallen.

NEW_FINDING: C-24 | OBSERVATION | Der Begriff „Writerform“ trägt nach Revision 4 eine Stopbedingung, Gesamt-Abnahmekriterium 8, Abschnitt 4.5.6 und eine Manifestspalte, ist aber nirgends normativ definiert. Aus Abschnitt 4.5.4 lässt sich die Lesart mit acht Formen ableiten (Codex-Plan, -Implementierung, -Korrektur, -Finalbericht sowie Claude-Plan-, Slice-, Korrektur- und Finalreview), doch Slice 2 Schritt 3 modelliert zusätzlich Freigabe-, Ablehnungs- und Stopäste als getrennte geschlossene Äste, und Abschnitt 4.2.1 führt für Codex je Form einen eigenen `stop_result`-Ast. Ob diese Äste eigene Writerformen mit eigener Nachweispflicht sind, entscheidet über Umfang und Zählbarkeit des gesamten Nachweisteils. | Abschnitt 4.5 definiert „Writerform“ abschließend als die je Provider und gebundenem Projektionskontext erzeugte kanonische Schemaprojektion, benennt die daraus folgende Gesamtzahl und legt fest, dass Entscheidungs- und Stopäste innerhalb einer Form keinen eigenen Nachweis benötigen; disponierbar in Slice 1.

PLAN_APPROVAL: NO

STATUS: DONE

### Regressionsprüfung C-01 bis C-17

Revision 4 hat keine frühere Schließung beschädigt. Die Trennung
`schema_only`/`request_bound` ist in Abschnitt 3.4, 4.5.2, 4.5.3 und Slice 3
konsistent und verbietet synthetische historische Kontexte weiterhin
ausdrücklich (C-13). Der eigene Codex-Final-Canary bleibt in Abschnitt 3.4,
4.5.4 und im Slice-3-Akzeptanzkriterium bestehen (C-06). Beide
Resultat-v1-Leseschemata fehlen unverändert in allen drei Änderungspfaden,
Abschnitt 4.1.7 und Abnahmekriterium 10 sind unverändert, und die
Bytegleichheitskriterien in Slice 1 und Slice 2 bestehen fort (C-04, C-11,
C-17). Die typisierte Canary-Ausführungsgrenze mit `src/agent_adapters.py`,
`src/agent_runtime.py` und ihren Tests im Änderungspfad von Slice 3 ist
unverändert und wurde durch die ausdrückliche Beschränkung auf den
Canarymodus präzisiert (C-07, C-14). `approval_marker` bleibt alleiniger
primärer Astdiskriminator ohne Änderung an `NativeReviewContext`, Review-
Request-v1-Schema oder Requestdigest (C-09, C-12). `tests/test_agent_runtime.py`
bleibt Slice 1 und Slice 2 zugeordnet, und alle drei Matrizen führen die
vollständige Suite (C-15). Die Versionsbindung der Capability-Tabelle bleibt
bestehen und wurde um die Profilbindung erweitert (C-16); deren
Ausgestaltung ist Gegenstand von C-23 und öffnet C-16 nicht.

### Entscheidung

Revision 4 schließt die drei Corpusblocker der dritten Fassung sauber und am
Repository überprüfbar. `C-18` ist durch eine korrekte, namentlich belegte und
gegen eine Vollinventur bestätigte Bestandsbeschreibung geschlossen, `C-19`
durch einen an allen vier normativen Stellen konsistenten Ausschluss von
`schema_only`, `C-20` durch die vollständige Verlagerung der Corpusarbeit nach
Slice 3 plus einen heute erfüllbaren Negativnachweis.

Freigegeben wird der Plan dennoch nicht. `C-21` bleibt offen, weil die beiden
Transportprofile nicht prüfbar gebunden sind. Hinzu kommen zwei neue Blocker:
der als einziger Corpusnachweis zugelassene `request_bound`-Typ wird nie gegen
die Writerform validiert, die er belegen soll, und kennt keinen Umgang mit dem
Gegenfall (C-22); und das fail-closed wirksame, aber undefinierte und
selbstwidersprüchliche Transportprofil kann in seiner wörtlichen Lesart jede
Projektion dauerhaft blockieren (C-23). `C-24` ist eine Observation, Slice 1
zugeordnet und dort prüfbar disponierbar.

Die Implementierung beginnt erst nach Schließung von `C-21` bis `C-23` und
erneuter Vorlage dieser Planfassung.

## 17. Disposition des Claude-Planreviews – Revision 4

Claude hat in Abschnitt 16 `C-18` bis `C-20` geschlossen und sämtliche
früheren Schließungen regressionsgeprüft. Der verbliebene Profilbefund sowie
die zwei neuen Blocker und die Writerform-Observation werden für Revision 5
wie folgt disponiert:

| Finding | Einstufung | Disposition in Revision 5 |
|---|---|---|
| `C-21` | OBSERVATION | **ACCEPTED.** Abschnitt 4.1.8 definiert das normalisierte Transportprofil jetzt als kanonisches JSON mit abschließendem Feldumfang und expliziten Ausschlüssen. Slice 1 verlangt dieselbe Normalisierungsfunktion für Produktions- und Sondenkommando; Tests belegen Gleichheit bei ausschließlich isolationsbedingten Unterschieden und Ungleichheit bei Modell-, Effort-, Schema- oder stabilen Flagabweichungen. |
| `C-22` | BLOCKER | **ACCEPTED.** Ein `request_bound`-Paar zählt nur noch, wenn seine Antwortbytes zusätzlich gegen die aus dem originalen Requestkontext rekonstruierte konkrete Writerschemainstanz validieren und anschließend die gebundene Domänenprüfung bestehen. Ein Gegenfall ist Stopgrund beziehungsweise nur durch eine bereits typisierte, providerbelegte und regressionsgetestete Ausnahme klassifizierbar. |
| `C-23` | BLOCKER | **ACCEPTED.** Das Profil enthält genau Provider, Binärname, Modell, Reasoning-/Effortwert, Schemaübergabemechanismus und kanonische stabile Restflags. Sandbox, CWD, `--add-dir`, temporäre Pfade und Prompt-/Policyinhalte sind ausdrücklich ausgeschlossen. Profilgleichheit wird semantisch normalisiert statt als unmögliche Bytegleichheit kompletter Kommandos geprüft. |
| `C-24` | OBSERVATION | **ACCEPTED.** Abschnitt 4.5.8 definiert genau acht Writerform-Nachweisklassen. Kontextvariationen deckt die Differentialmatrix ab; Entscheidungs- und Stopäste sind Bestandteile einer Form. Das Slice-3-Manifest muss exakt acht Zeilen mit Schema-Digest, Repräsentativkontext und tragendem Nachweis enthalten. |

Diese Änderungen betreffen weiterhin ausschließlich den Arbeitsplan. Die
Implementierung beginnt erst nach Claudes erneutem read-only Review und der
Schließung von `C-21` bis `C-24`.

## 18. Claude-Planreview – Revision 5 vom 24. August 2026

### Eingefrorener Reviewgegenstand

- Arbeitsplan: `docs/internal/native-agent-contract-closure-arbeitsplan.md`,
  Revision 5 (Abschnitte 1 bis 17)
- Basiscommit: `c410eef95164d10b1ff0d97d2a9593ca5af18324`
- Reviewhistorie: Abschnitte 11 bis 17 sowie
  `docs/internal/native-agent-contract-closure-review.md`
- Anforderungsquelle: `/tmp/orchestrator-stabilisierung-1-1-nachtraege.md`,
  ausschließlich der Abschnitt **Contract Closure**
- Reviewer: Claude
- Modus: direktes read-only Repositoryreview ohne Testausführung, ohne
  Bootstrap, ohne Änderung an Produktivcode, Tests oder den normativen
  Abschnitten 1 bis 15 und 17
- Zusätzlich geprüfter Repositorystand gegenüber Revision 4:
  `src/agent_adapters.py:420-437` (native Codex-Kommandozeile),
  `src/agent_adapters.py:902-935` (native Claude-Kommandozeile),
  `src/agent_config.py:24` und `:172` (`max_budget_usd`),
  `src/contracts.py:196-260` (`ValidationAttestation`),
  `src/native_review_contract.py:828-906` (Kontext- und
  Attestierungsbindung), sämtliche drei belegten Request-/Response-Paare

### Textuelles Reviewergebnis

REVIEWER: claude

FINDING_STATUS: C-21 | CLOSED | Beide Teile des Schließungskriteriums sind erfüllt. Die Trennung ist widerspruchsfrei: Slice 3 Schritt 6 beschränkt die Pflicht zur identischen Adapter-/Runtimefunktion ausdrücklich auf den Canarymodus, und Slice 1 Schritt 1 erlaubt der Subset-Sonde ausdrücklich eine eigene minimale Invokation für beliebige Kandidatenschemata; die Sonde hängt an keiner Stelle von der erst in Slice 3 eingeführten Canary-Ausführungsgrenze ab. Die Bindung ist jetzt prüfbar: Slice 1 Schritt 1 verlangt, dass Sonde und echter Produktionsaufruf mit derselben Normalisierungsfunktion auf den in Abschnitt 4.1.8 abschließend definierten Feldumfang projiziert werden und ihre normalisierten Profile kanonisch bytegleich sein müssen, während abweichende Sandbox, Arbeitsverzeichnisse und temporäre Pfade ausdrücklich zulässig bleiben. Das zugehörige Slice-1-Akzeptanzkriterium ist konkret ausführbar, weil es auf einer echten produktiven `PreparedProviderInput`-Kommandozeile arbeitet (`src/provider_input_budget.py:88-91`), die für Codex und Claude in `src/agent_adapters.py` erzeugt wird; beide Adapter liegen im Änderungspfad von Slice 1.

FINDING_STATUS: C-22 | CLOSED | Die Regel ist an allen vier geforderten Stellen konsistent verankert: Abschnitt 4.5.2 („Jedes `request_bound`-Paar wird zusätzlich gegen genau die kanonische Writerschemainstanz validiert, die aus seinem originalen gebundenen Requestkontext rekonstruiert wird. Nur bei erfolgreicher Writer- und Domänenvalidierung darf es als Writerform-Nachweis gelten“), Slice 3 Schritt 2 mit der vorgeschriebenen Reihenfolge Writerschema zuerst, danach Domänen-Lesergebnis beziehungsweise stabiler Fehlercode, die neue Stopbedingung in Abschnitt 8 für ein historisch lokal gültiges Paar, das am Writerschema scheitert, sowie Gesamt-Abnahmekriterium 9. Die abweichende Klassifizierung ist ausschließlich über eine bereits typisierte, providerbelegte und regressionsgetestete Ausnahme möglich. Die Regel ist am Repository ausführbar und misfiret nicht: die Attestierungsbindung `_attestation_binding()` (`src/native_review_contract.py:879-906`) ist gegenüber den Feldern von `ValidationAttestation` (`src/contracts.py:196-206`) verlustfrei, `complete` und `passed` sind daraus abgeleitete Properties, und für alle drei belegten Paare gilt nachgerechnet `expected_commands=1`, `records=1`, keine fehlenden Kommandos und Status `PASS`. Abschnitt 4.3.9 zwingt sie daher nicht auf `denied`, und ihre Antwortformen (Findingfenster ab `next_finding_id`, Anchors nur mit gebundener Origin, keine neue Observation in der Konvergenzrunde) sind mit Abschnitt 4.3.2, 4.3.6 und 4.3.7 vereinbar.

FINDING_STATUS: C-23 | CLOSED | Abschnitt 4.1.8 definiert das Transportprofil als kanonisches JSON-Objekt mit genau `provider`, `binary_name`, `model`, `reasoning_or_effort`, `schema_transport` und `semantic_flags`; `schema_transport` bezeichnet ausdrücklich den Mechanismus (`output-schema-file` für Codex, `json-schema-argument` für Claude) statt des laufspezifischen Schemawerts, Modell-, Effort- und Schemaflags werden nicht doppelt in `semantic_flags` geführt, und ausgeschlossen sind Codex-Sandboxmodus, Arbeitsverzeichnis, `--add-dir`, alle temporären Schema-/Last-Message-/Manifestpfade sowie Prompt-, Policy- und Directive-Inhalte. Ein nicht klassifiziertes neues Kommandoargument gilt als Profil-Drift und wird fail-closed abgewiesen statt stillschweigend entfernt. Beide `semantic_flags`-Listen wurden gegen den Code geprüft und partitionieren die realen Kommandozeilen vollständig und überschneidungsfrei: Codex `exec --model … --config model_reasoning_effort=… --skip-git-repo-check --ephemeral --sandbox workspace-write --color never --json --output-schema <datei> --output-last-message <datei> -` (`src/agent_adapters.py:420-437`) und Claude `-p --output-format json --model … --effort … --tools Read --allowedTools Read --disallowedTools Bash,Edit,Write,NotebookEdit,Grep,Glob --permission-mode dontAsk --setting-sources user --safe-mode --strict-mcp-config --prompt-suggestions false --add-dir <dir> --system-prompt <policy> --json-schema <schema> --no-session-persistence --disable-slash-commands [--max-budget-usd <wert>] <directive>` (`src/agent_adapters.py:902-935`). Die in Revision 4 gemeldete Verklemmung ist damit beseitigt: gefordert wird semantische Profilgleichheit statt unmöglicher Bytegleichheit vollständiger Kommandos, und genau die drei prinzipiell nie gleichen Argumente — Sandbox und die beiden `mkdtemp()`-Pfade aus `src/agent_adapters.py:196-207` — sind ausdrücklich ausgeschlossen beziehungsweise nur als typisierter Platzhalter repräsentiert.

FINDING_STATUS: C-24 | CLOSED | Abschnitt 4.5.8 definiert „Writerform“ abschließend als genau eine von acht Nachweisklassen in der geforderten Aufzählung, verlangt je Klasse mindestens eine kanonische Schemainstanz aus einem vollständigen gebundenen Repräsentativkontext, ordnet variierende Finding-IDs und Attestierungen der Differentialmatrix ohne zusätzliche Nachweisklasse zu und erklärt Freigabe-, Ablehnungs- und Stopäste zu geschlossenen Ästen innerhalb einer Form ohne separaten Transportbeleg. Das Slice-3-Akzeptanzkriterium fordert exakt acht Manifestzeilen mit Schemadigest, Repräsentativkontext und tragendem Nachweis und schließt Entscheidungs- und Stopäste als eigene Zeilen aus; Gesamt-Abnahmekriterium 9 wiederholt die Achtzahl. Der zulässige Nachweis ist über Abschnitt 4.5.6, die Writerform-Stopbedingung und Gesamt-Abnahmekriterium 8 auf ein erfolgreiches `request_bound`-Paar oder einen erfolgreichen Live-Canary begrenzt, `schema_only` ist überall ausgeschlossen. Die Zählung ist am Bestand konsistent: die drei belegten Paare sind sämtlich `SLICE_APPROVAL` und decken mit `allow_new_observations=True` beziehungsweise `False` die Klassen „initialer Claude-Slice“ und „Claude-Slice-Konvergenz“ ab; die übrigen sechs Klassen sind durch die Canaryliste in Abschnitt 4.5.4 vollständig abgedeckt.

NEW_FINDING: C-25 | OBSERVATION | `--max-budget-usd` gehört nach Abschnitt 4.1.8 zu den Claude-`semantic_flags`, ist aber ein reiner Kostenschalter ohne jeden Einfluss auf die JSON-Schema-Akzeptanz und pro Aufruf über die CLI konfigurierbar (`src/agent_config.py:24`, `src/agent_config.py:172`, `src/agent_adapters.py:171`). Ändert ein Betreiber das Budget, ändert sich das normalisierte Transportprofil, die Capability-Tabelle wird nach Abschnitt 4.1.8 fail-closed abgewiesen, und bis zu einer erneuten Live-Sonde lässt sich überhaupt keine Claude-Projektion mehr bauen. Der Ausfall ist sicher und die Abhilfe definiert, aber eine betrieblich folgenlose Budgetanpassung erzwingt damit einen kostenpflichtigen Providerlauf. | Abschnitt 4.1.8 nimmt `--max-budget-usd` aus den `semantic_flags` heraus und führt es wie Sandbox und Arbeitsverzeichnis als ausdrücklich profilfremdes Argument, oder der Plan hält fest, dass eine Budgetänderung eine erneute Subset-Sonde erzwingt und dokumentiert das als bewusste Betriebsfolge; ein Slice-1-Test belegt das gewählte Verhalten; disponierbar in Slice 1.

PRE_MORTEM: Die wahrscheinlichste Fehlerursache in drei Monaten ist eine Erosion der Capability-Tabelle durch Providerdrift. Codex- und Claude-CLI werden häufiger aktualisiert als dieses Arbeitspaket; jede Version, jede Modelländerung und nach Abschnitt 4.1.8 auch jede Änderung eines stabilen semantischen Flags entwertet die Tabelle fail-closed und verlangt eine erneute Live-Sonde. Wird diese Sonde unter Zeitdruck nicht sauber wiederholt, sondern die Tabelle von Hand nachgezogen oder die Fail-closed-Prüfung entschärft, verliert die Writerprojektion ihre empirische Grundlage: sie verwendet dann Schemakonstruktionen, die nur noch behauptet, nicht mehr belegt sind. Der Ausfall wäre nicht laut, sondern erschiene wieder als das ursprüngliche Symptom — provider-schema-valide Antworten, die erst lokal scheitern —, und die Ausnahmeliste würde still wachsen, weil jeder solche Fall am bequemsten als „im Subset nicht ausdrückbar“ abgelegt wird. Die Gegenmittel stehen im Plan (Stopbedingung bei nicht abschließbarer Neusonde, Verbot des Ausnahmenwachstums ohne erneutes Planreview, Pflicht zu Providerbeleg und Regressionstest je Ausnahme); sie schützen nur, solange niemand sie unter Termindruck umgeht.

PLAN_APPROVAL: YES

STATUS: DONE

### Regressionsprüfung C-01 bis C-20

Revision 5 hat keine frühere Schließung beschädigt.

- Kontextseitige `const`-Entscheidungen: Abschnitt 4.2.7, 4.2.8, 4.3.9, 4.3.10,
  Slice 1 Schritt 4, Slice 2 Schritt 3 und Gesamt-Abnahmekriterium 4
  unverändert (C-01).
- Empirische Subset-Sonde: Abschnitt 3.3, Slice 1 Schritt 1 und die
  vorgezogene Stopbedingung unverändert; die Sonde wurde nur um die
  Profilnormalisierung präzisiert (C-02).
- Capability- und Ausnahmeliste: beide Dateien weiterhin Neuanlage in Slice 1,
  `schemas/native-provider-schema-exceptions-v1.json` weiterhin im
  Änderungspfad von Slice 2, Verifikation in Slice 3 Schritt 4,
  Abschnitt 4.4.8 und Ausnahme-Stopbedingung unverändert (C-03).
- Resultat-v1-Leseschemata: beide Dateien erscheinen in keinem der drei
  Änderungspfade; Abschnitt 4.1.1, 4.1.7 und Gesamt-Abnahmekriterium 11 sowie
  die Bytegleichheitskriterien in Slice 1 und Slice 2 unverändert (C-04,
  C-11, C-17).
- Pflichtmutationsmatrix und Negativkontrolle: Abschnitt 4.4.6 bis 4.4.8 und
  Slice 3 Schritt 3 unverändert (C-05).
- Codex-Final-Canary: Abschnitt 3.4, 4.5.4 und das dedizierte
  Slice-3-Akzeptanzkriterium unverändert (C-06).
- Sichere Canary-Ausführungsgrenze: Abschnitt 4.5.7, Slice 3 Schritt 6 mit
  `src/agent_adapters.py`, `src/agent_runtime.py` und ihren Tests im
  Änderungspfad, die `git status`-Kriterien und die Sandbox-Stopbedingung
  unverändert (C-07, C-14).
- `approval_marker` als alleiniger primärer Review-Astdiskriminator ohne
  Änderung an `NativeReviewContext`, Review-Request-v1-Schema oder
  Requestdigest: Abschnitt 4.3.1, Slice 2 Schritt 1 und das
  `operation`-Invarianzkriterium unverändert (C-09, C-12).
- Trennung `schema_only`/`request_bound` inklusive Verbot synthetischer
  Kontexte: Abschnitt 3.4, 4.5.2, 4.5.3 und Slice 3 Schritt 2 unverändert und
  durch C-22 zusätzlich verschärft (C-13, C-19).
- Vollständige Inventur der drei bekannten Paare: Abschnitt 3.4 und Slice 3
  Schritt 1 mit Untergrenze und Manifestausweis unverändert (C-18).
- Ausschluss von `.orchestrator`-Abhängigkeiten aus Slice 1: das
  Negativkriterium steht unverändert am Ende der Slice-1-Akzeptanzkriterien;
  eine erneute Durchsicht aller `tests/*.py` bestätigt, dass kein Test das
  reale Repository-`.orchestrator` liest (C-20).
- Vollständige Testsuite nach jedem Slice: Abschnitt 7.1, 7.2 und 7.3 führen
  weiterhin je zusätzlich `python3 -m pytest tests/ -v`, und
  `tests/test_agent_runtime.py` bleibt Slice 1 und Slice 2 zugeordnet (C-15).
- Versions- und Profilbindung der Capability-Tabelle: Abschnitt 4.1.8, Slice 1
  Schritt 1 und die Capability-Stopbedingung unverändert und um die
  Profilnormalisierung erweitert (C-16); die verbleibende Frage zum
  Budgetflag ist als C-25 erfasst und öffnet C-16 nicht.

### Entscheidung

Revision 5 schließt die letzten offenen Blocker sauber und am Repository
überprüfbar. Das normalisierte Transportprofil ist abschließend, kollisionsfrei
und gegen beide realen Kommandozeilen validiert; die Verklemmung aus Revision 4
ist beseitigt, ohne die Fail-closed-Wirkung zu verlieren. Der
`request_bound`-Nachweis ist nicht mehr inhaltsleer, sondern verlangt die
Writervalidierung vor der Domänenprüfung und behandelt den Gegenfall als
Stopgrund. Die acht Writerform-Nachweisklassen sind abschließend definiert,
zählbar und mit konkreten Nachweisregeln unterlegt. Die Regressionsprüfung
zeigt keine Beschädigung von `C-01` bis `C-20`.

Der Plan ist damit freigegeben. `C-25` ist eine Observation, Slice 1
zugeordnet und dort prüfbar disponierbar; sie begleitet die Freigabe und
verhindert sie nicht.

Die Umsetzung darf mit Slice 1 beginnen. Es gilt unverändert: Slice 1 startet
mit der Subset-Sonde vor jeder produktiven Writeränderung, jeder Slice
durchläuft die vollständige Matrix und ein read-only Claude-Slice-Review, und
ein offener Blocker verhindert den Beginn des nächsten Slices.
