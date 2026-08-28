# Arbeitsplan – Projektionsgarantie und Kopplungsbremse

TARGET_BRANCH: feature/projection-guard-and-coupling-ratchet

BASE_COMMIT: 011510127c78

STATUS: BEREIT_ZUR_PLANPRUEFUNG

## 1. Ziel und Umfang

Dieses Paket ergänzt ausschließlich providerfreie Tests und statische
Prüfdaten. Es ändert weder `src/native_provider_schema.py` noch ein aktives
Schema oder Providerregister und damit auch keine Projektion, keinen Workflow,
keinen Vertrag, keine Recordkette und keine Providerkommunikation.

Die Umsetzung sichert zwei generationenunabhängige Eigenschaften:

1. Jede von `defensive_provider_projection()` tatsächlich entfernte
   Reader-Schemaeigenschaft muss durch eine bestehende, provideridentische
   Ausnahme samt lokalem, von Pytest einsammelbarem Regressionstest abgedeckt
   sein.
2. Die Zahl der nicht überlappenden, groß-/kleinschreibungsunabhängigen
   Teilzeichenketten `codex` und `claude` darf je produktiver Laufzeitdatei
   gegenüber einem eingecheckten Ausgangswert nicht steigen.

Nicht umgesetzt werden eine Dialektregeltabelle, ein neues
Merkmalsvokabular, Register- oder Schemaversionen, Rollenabstraktionen,
Provideradapter oder eine Bereinigung vorhandener Providernamen.

## 2. Repositorybefund und verbindliche Entscheidungen

### 2.1 Projektionsinventar

`src/native_provider_schema.py` erstellt vor jeder Abschwächung eine tiefe
Kopie. Für `claude` bleibt diese Kopie unverändert. Für `codex` entfernt die
Funktion rekursiv `uniqueItems` und jedes `pattern`, das `(?` enthält. Im
aktuellen Codex-Reader sind das genau sechs Eigenschaften:

- die Lookaround-Patterns von `$defs.safe_text` und `$defs.safe_path`;
- `uniqueItems` in `planned_slice.scope_paths`;
- `uniqueItems` in den `test_files` von Implementierungs- und
  Korrekturergebnissen;
- `uniqueItems` in `stop_result.remediation_paths`.

Der Nachweis bleibt vollständig in
`tests/test_native_contract_differential.py`. Eine dort statisch definierte,
exakte Zuordnung verbindet jeden erwarteten JSON-Pointer und Schlüsselworttyp
mit den heute zulässigen Begriffen aus `missing_schema_feature`. Damit wird
weder das Fähigkeits- noch das Ausnahmeregister um ein drittes Vokabular
erweitert. Ein entfernter Pointer ohne Zuordnung, ein falscher Provider, ein
nicht passender Merkmalsbegriff oder ein nicht einsammelbarer Test ist ein
Fehler.

Die Prüfung löst jeden `regression_test` als Pytest-Node-ID auf. Ein bloßer
Texttreffer `def test_...` genügt nicht; ein fokussierter
`pytest --collect-only`-Unterprozess muss die benannte Node-ID erfolgreich
einsammeln. Diese Unterprozesse verwenden nur lokale Dateien und erhalten
keinen Provider-, Netz- oder API-Zugriff.

Zusätzlich werden die kanonischen, an Requests gebundenen Writerschemabytes
aller acht bereits in `tests/test_native_contract_differential.py`
konstruierten Formen festgehalten: vier Codex-Ergebnisarten sowie Claudes
Plan-, Erst-Slice-, Konvergenz- und Finalreview. Die Hashfixture enthält
Provider/Form und SHA-256; der Test vergleicht den Hash der vorhandenen
kanonischen Serialisierung exakt. Dadurch ist nicht nur die Menge verschiedener
Schemas, sondern ihr konkreter bisheriger Byteinhalt eingefroren.

### 2.2 Laufzeitinventar und Zählsemantik

Der Namensguard orientiert sich an `_retirement_active_files()` und dessen
temporären Negativkontrollen in `tests/test_language_consistency.py`, liest aus
`orchestrator.toml` aber ausschließlich `[paths].productive` und zieht danach
alle Treffer von `[paths].generated` ab. Eingeschlossen werden nur aufgelöste,
reguläre UTF-8-Textdateien. Tests und Dokumentation werden nicht über ihre
eigenen Pfadklassen hinzugenommen; unbekannte Pfade werden für diese ausdrücklich
auf `[paths].productive` begrenzte Ratsche nicht erfunden.

Gezählt wird pro Datei und Name mit einer literal escapten Regex und `re.I`.
`finditer` liefert die geforderten nicht überlappenden Teilzeichenkettentreffer
auch in Bezeichnern, Strings, Kommentaren und Docstrings. Die Fixture führt nur
positive Werte; das Fehlen eines Pfades oder Namens bedeutet null. Für jede
aktuell aufgelöste Datei gilt `Istwert <= Baseline`. Baselinepfade, die nicht
mehr produktiv aufgelöst werden, sind als veraltete Prüfdaten ebenfalls ein
Fehler und müssen im selben sichtbaren Review entfernt werden. Bei einer
Verringerung darf der Test grün bleiben; die niedrigere Grenze wird erst durch
eine bewusste Fixtureänderung festgeschrieben. Neue Dateien beginnen ohne
Fixtureeintrag bei null.

Die reproduzierte Inventur am Basiscommit umfasst 48 reguläre UTF-8-Dateien,
davon 34 mit mindestens einem positiven Wert; insgesamt wurden 930
`codex`- und 406 `claude`-Treffer ermittelt. Die Umsetzung schreibt sämtliche
34 positiven Dateipaare als feste, sortierte JSON-Daten ein und berechnet diese
Fixture niemals während des Testlaufs oder durch einen Update-Modus neu.

## 3. Stop- und Abbruchregeln

- Falls einer der sechs heutigen Codex-Verluste nicht durch einen vorhandenen
  Codex-Ausnahmeeintrag mit passendem `missing_schema_feature` und tatsächlich
  einsammelbarer Regression abgedeckt ist, wird nicht an Register, Mapping
  oder Test passend korrigiert. Die Umsetzung hält als inhaltlicher Fund zur
  Auftraggeberentscheidung an.
- Falls der Nachweis eine Änderung an `src/native_provider_schema.py`, einem
  aktiven Schema oder einem der beiden Providerregister benötigt, hält die
  Umsetzung an.
- Falls die Pfadauflösung, UTF-8-Lesbarkeit oder vorgegebene Zählsemantik nicht
  deterministisch dieselben Ausgangswerte liefert, hält die Umsetzung an,
  statt eine laufzeitgenerierte Baseline einzuführen.
- Jede Änderung eines festgehaltenen Writerschemahashes oder jeder unerwartete
  zusätzliche Projektionsverlust ist ein roter Befund, keine Gelegenheit zur
  stillen Aktualisierung der Prüfdaten.

## 4. Umsetzung

### Slice 1 - Providerfreie Projektionsgarantie und Providernamen-Ratsche

**Exakter Änderungspfad**

- `tests/fixtures/native-provider-projection-baseline-v1.json`
- `tests/fixtures/provider-name-coupling-baseline-v1.json`
- `tests/test_language_consistency.py`
- `tests/test_native_contract_differential.py`

**Arbeitsschritte**

1. Die acht aktuellen kanonischen Writerschemahashes einmalig aus den bereits
   vorhandenen gebundenen Testkontexten ermitteln und als sortierte,
   reviewlesbare Fixture festhalten. Den bisherigen Mengenvergleich auf exakte
   provider-/formbezogene Hashvergleiche verschärfen, ohne Konstruktion oder
   Projektion zu verändern.
2. Im Differentialtest einen rekursiven, rein testseitigen Schema-Diff
   ergänzen. Er vergleicht jeden unveränderten Reader mit
   `defensive_provider_projection()`, erlaubt ausschließlich Entfernungen,
   inventarisiert Provider, JSON-Pointer, Schlüsselwort und ursprünglichen
   Wert und gleicht jeden Verlust gegen die testlokale exakte
   Pointer-zu-Merkmalsbegriff-Zuordnung ab.
3. Für jeden zugeordneten Verlust einen vorhandenen Eintrag desselben Providers
   aus `registered_exceptions()` verlangen und dessen `regression_test` über
   einen lokalen `pytest --collect-only`-Aufruf als echte Node-ID nachweisen.
   Für `claude` muss der Differenzsatz leer bleiben; beide registrierten
   Provider werden ausdrücklich durchlaufen.
4. Als rote Projektionskontrolle Reader und Register in ein `tmp_path`-Repository
   kopieren, in der kopierten Readerdatei an einem neuen JSON-Pointer eine
   weitere durch die Projektion entfernbare Eigenschaft ergänzen und zeigen,
   dass derselbe Prüfer wegen fehlender Zuordnung/Kompensation fehlschlägt.
   Keine Repositorydatei wird dabei geschrieben.
5. Die am Basiscommit ermittelten positiven Werte für `codex` und `claude`
   vollständig, pfadsortiert und explizit in
   `provider-name-coupling-baseline-v1.json` ablegen. Die Fixture besitzt keinen
   Generator, Lernmodus oder automatische Aktualisierung.
6. In `tests/test_language_consistency.py` die produktive Pfadauflösung aus
   `[paths].productive` samt `[paths].generated`-Ausschluss kapseln, reguläre
   UTF-8-Dateien deterministisch sortieren und die beiden Teilzeichenketten
   nicht überlappend und case-insensitive zählen. Überschreitungen werden mit
   Pfad, Providername, Baseline und Istwert gemeldet; Nullgrenzen für fehlende
   Einträge und neue Dateien sind ausdrücklich zu prüfen.
7. Als rote Ratschenkontrolle `orchestrator.toml`, Fixture und eine betroffene
   produktive Textdatei in ein `tmp_path`-Repository kopieren, dort einen
   zusätzlichen Treffer ergänzen und zeigen, dass der Guard den Zuwachs
   meldet. Die echte Arbeitsbaumdatei und die echte Fixture bleiben
   unverändert.
8. Sicherstellen, dass Verringerungen akzeptiert werden, Groß-/Kleinschreibung
   und eingebettete Teilzeichenketten zählen, Überlappungen nicht doppelt
   zählen, generierte Pfade ausgeschlossen bleiben und eine neue produktive
   Datei ohne Baselineeintrag bei ihrem ersten Treffer rot wird.

**Akzeptanzkriterien**

- Die Hashes aller vier Codex- und vier Claude-Writerschemaformen entsprechen
  bytegenau den festgehaltenen Ausgangsdaten.
- Der reale Projektionsdiff enthält nur die sechs bekannten Codex-Verluste und
  keinen Claude-Verlust; jeder Verlust besitzt eine passende registrierte
  Codex-Kompensation mit von Pytest einsammelbarer Regression.
- Die temporär eingeführte siebte Abschwächung wird zuverlässig als
  unkompensiert abgewiesen.
- Die produktive Inventur entspricht der eingecheckten Baseline oder liegt
  darunter; eine temporäre Erhöhung sowie der erste Treffer in einer neuen
  produktiven Datei schlagen zuverlässig an.
- Kein Test nimmt Provider-, Netz- oder API-Dienste in Anspruch und beide roten
  Kontrollen verändern ausschließlich ihre temporären Kopien.
- `git diff -- schemas src orchestrator.toml` bleibt für diesen Slice leer;
  insbesondere bleiben beide Providerregister und alle aktiven Schemas
  byteidentisch.

**Validierung**

- `python3 -m pytest tests/test_native_contract_differential.py -v`
- `python3 -m pytest tests/test_language_consistency.py -v`
- `git diff --check`

Nach dem fokussierten Nachweis übergibt Codex den fertigen Slice normal an den
Orchestrator. Nur der Orchestrator führt für den geprüften Diff-Fingerprint die
vollständige konfigurierte Matrix `python3 -m pytest tests/ -v` aus und bindet
deren Attestierung; der Agent beansprucht diese Freigabe nicht selbst.

## 5. Risiken und Bruchbedingungen

- Größtes Restrisiko ist eine unvollständige Pointerzuordnung, die zwar die
  heutigen sechs Verluste kennt, aber eine semantisch gleichartige neue Stelle
  zu großzügig unter einen alten Registereintrag fasst. Deshalb ist die
  Zuordnung pointergenau und die Negativkontrolle fügt bewusst einen neuen
  Pointer mit einem ansonsten bekannten Schlüsselwort hinzu.
- Die Ratsche könnte durch eine abweichende Globinterpretation Dateien
  auslassen. Deshalb übernimmt sie das bereits getestete
  `orchestrator.toml`-Inventarmuster und prüft neue produktive Dateien sowie
  Generated-Ausschlüsse separat.
- Ein Testname kann als Quelltext weiterexistieren, aber durch Parametrisierung,
  Importfehler oder Collection-Regeln nicht ausführbar sein. Der
  `--collect-only`-Nachweis prüft deshalb die volle registrierte Node-ID.
- Der Plan bricht, wenn die Umsetzung zur Erfüllung eines Kriteriums Produktcode,
  Register, aktive Schemas oder Konfiguration ändern müsste; dafür ist ein
  neuer, ausdrücklich autorisierter Auftrag erforderlich.

## Orchestrator-Prüfprotokoll

### Review-Feedback von Claude

<!-- audit:claude-review:begin -->
### Claude · Runde 1 · approved (Ereignis 3)

- Reviewer: `claude`
- Freigabe: `YES`
- Validierungsbindung: `plan-validation-469650e41463`
- Testdateien: keine
- Prüfdimensionen: Geprüft: Pfad-/Scope-Bindung (Diff ändert ausschließlich die autorisierte Plandatei), Übereinstimmung von TARGET_BRANCH und BASE_COMMIT mit Auftrag und Ausführungsgrenze, vollständige Abdeckung aller sechs SoW-Anforderungen (Byteidentität via Hashfixture, Garantie-Test Teil 1, Negativkontrolle Teil 1, Ratsche mit fester Baseline plus Negativkontrolle Teil 2, keine Provider-/Netz-/API-Abhängigkeit, statische statt laufzeitgenerierte Baseline), Einhaltung der Kopie-Regel für beide Negativkontrollen, Übernahme aller drei SoW-Stoppbedingungen als explizite Planregeln, Einhaltung des PLAN_ONLY-Formatvertrags (### Slice N, **Exakter Änderungspfad**, reine Pfad-Bullets), Nichtberührung von src/, Schemas, Registern und orchestrator.toml, sowie Fingerprint-Übereinstimmung und Vollständigkeit der gebundenen Validierungsattestierung.
- Größtes Restrisiko: Die im Plan genannten konkreten Zählwerte und der als abschließend behandelte Sechs-Verlust-Katalog sind Aussagen über den Repository-Ist-Zustand, die in diesem Planreview nicht unabhängig gegen den echten Quellbaum nachgerechnet wurden.
- Realistische Bruchbedingung: Weicht die in Slice 1 tatsächlich erzeugte Fixture oder der reale Projektionsdiff von den im Plan behaupteten Werten ab (zusätzlicher unkompensierter Verlust, abweichende Datei-/Treffersummen), ohne dass dies als Stopp beziehungsweise Fund behandelt wird, ist das ein Blocker für die Slice-1-Prüfung.
- Eigene Findings: `C-01`

<!-- artifact-records:claude-review:begin -->
Semantischer Record-Digest: `7f321a312d60`

### Claude · Runde – · approved

| Seq/Record | Rolle | Runde | Status | Work-Unit | Findings | Fingerprint | Transport | Request | Response |
|---|---|---:|---|---|---|---|---|---|---|
| 10. `ar1-3b8e8d5ac5c8` | `claude` | `–` | `approved` | `1` | `C-01` | `469650e41463` | `native-claude-review-v2` | `native-review-request-6eaa723036ab` | `be5fa5449a8d` |
<!-- artifact-records:claude-review:end -->
<!-- audit:claude-review:end -->

### Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
Noch keine strukturierten Codex-Antworten.

<!-- artifact-records:codex-responses:begin -->
Semantischer Record-Digest: `7f321a312d60`

Keine Codex-Findingantworten.
<!-- artifact-records:codex-responses:end -->
<!-- audit:codex-responses:end -->

### Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### Ereignis 1: `plan-validation-22810d927493`

- Diff-Fingerprint: `22810d927493`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: internal plan contract passed
- Ausgabedigest: `667f1b8d90a4`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| internal:work-plan-contract | PASS | 0 | slices=1; planned_paths=1; changed_paths=1; future_slices=1; work_plan=docs/internal/projektionsgarantie-und-kopplungsbremse-arbeitsplan.md |

### Ereignis 2: `plan-validation-469650e41463`

- Diff-Fingerprint: `469650e41463`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: internal plan contract passed
- Ausgabedigest: `667f1b8d90a4`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| internal:work-plan-contract | PASS | 0 | slices=1; planned_paths=1; changed_paths=1; future_slices=1; work_plan=docs/internal/projektionsgarantie-und-kopplungsbremse-arbeitsplan.md |

<!-- artifact-records:validation-attestation:begin -->
Semantischer Record-Digest: `7f321a312d60`

- 2. `ar1-b1ee797b1d21`: Providerinput `codex/codex_plan` = `allowed`; local_input_chars `23886/4000000`, local_input_bytes `24014/16000000`; local_input_digest `2ff127626b13`, Policy `9edf600f09ac`, Übergang `02c83103f575`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, local_input_largest_component `stdin_prompt`; local_input_component_count `2`; Komponenten `stdin_prompt=20097/20225, response_schema=3789/3789`
### Validierungsattestierung

| Seq/Record | Rolle | Fingerprint |
|---|---|---|
| 6. `ar1-66b6b921e3e9` | `orchestrator` | `22810d927493` |

| Status | Exit | Output-Digest | Befehl mit argv-Grenzen |
|---|---:|---|---|
| `pass` | `0` | `667f1b8d90a4` | `argv` [`internal:work-plan-contract`] |
### Validierungsattestierung

| Seq/Record | Rolle | Fingerprint |
|---|---|---|
| 7. `ar1-105a141d4c6b` | `orchestrator` | `469650e41463` |

| Status | Exit | Output-Digest | Befehl mit argv-Grenzen |
|---|---:|---|---|
| `pass` | `0` | `667f1b8d90a4` | `argv` [`internal:work-plan-contract`] |
- 8. `ar1-0a2ef47db00d`: Providerinput `claude/claude_plan_review` = `allowed`; local_input_chars `52707/4000000`, local_input_bytes `52988/16000000`; local_input_digest `68f8546ac0d7`, Policy `9edf600f09ac`, Übergang `3a222e46156d`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, local_input_largest_component `request_chunk_001`; local_input_component_count `6`; Komponenten `request_chunk_001=24000/24168, request_chunk_002=17168/17281, packet_manifest=596/596, system_policy=430/430, response_schema=10283/10283, start_directive=230/230`
- Providerattempt-Summe Run `watch-20260828-102800.382059Z-90bd9a0c201f` / Operation `provider-operation-65c4e27be669` (`claude/claude_plan_review`; Modell `sonnet`; Effort `high`): Attempts `1`, offen `0`, Duration `135.264093` (bekannt `1`, unbekannt `0`); Inputzeichen `52707`, Inputbytes `52988`; Retrystatus `single-attempt`; input_tokens=sum:6,known:1,unknown:0; tool_input_tokens=sum:unknown,known:0,unknown:1; cache_read_input_tokens=sum:14872,known:1,unknown:0; cache_creation_input_tokens=sum:27388,known:1,unknown:0; thinking_tokens=sum:unknown,known:0,unknown:1; output_tokens=sum:12511,known:1,unknown:0; total_tokens=sum:unknown,known:0,unknown:1; turns=sum:5,known:1,unknown:0; cost_usd=sum:0.2386524,known:1,unknown:0
  - 12. `ar1-a095bc6d3cb2`: Attempt `1` = `succeeded`; Messung `ar1-0a2ef47db00d`; Modell `sonnet`; Effort `high`; Inputzeichen `52707`; Inputbytes `52988`; Duration `135.26409285400223`; Fehler `none`; Usage `input_tokens=6, tool_input_tokens=unknown, cache_read_input_tokens=14872, cache_creation_input_tokens=27388, thinking_tokens=unknown, output_tokens=12511, total_tokens=unknown, turns=5, cost_usd=0.2386524`
- Providerattempt-Summe Run `watch-20260828-102800.382059Z-90bd9a0c201f` / Operation `provider-operation-dc6737267b75` (`codex/codex_plan`; Modell `gpt-5.6-sol`; Effort `medium`): Attempts `1`, offen `0`, Duration `255.154523` (bekannt `1`, unbekannt `0`); Inputzeichen `23886`, Inputbytes `24014`; Retrystatus `single-attempt`; input_tokens=sum:unknown,known:0,unknown:1; tool_input_tokens=sum:unknown,known:0,unknown:1; cache_read_input_tokens=sum:unknown,known:0,unknown:1; cache_creation_input_tokens=sum:unknown,known:0,unknown:1; thinking_tokens=sum:unknown,known:0,unknown:1; output_tokens=sum:unknown,known:0,unknown:1; total_tokens=sum:unknown,known:0,unknown:1; turns=sum:unknown,known:0,unknown:1; cost_usd=sum:unknown,known:0,unknown:1
  - 5. `ar1-824491ec3afe`: Attempt `1` = `succeeded`; Messung `ar1-b1ee797b1d21`; Modell `gpt-5.6-sol`; Effort `medium`; Inputzeichen `23886`; Inputbytes `24014`; Duration `255.15452288899905`; Fehler `none`; Usage `unknown`
<!-- artifact-records:validation-attestation:end -->
<!-- audit:validation-attestation:end -->

### Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
- Teständerungsfreigabe: nicht erfasst.
- Pre-Mortems:
  - Ereignis 3: Größtes Rückschlagrisiko: Die eingecheckte Baseline (930 codex-/406 claude-Treffer über 34 von 48 Dateien) erweist sich in Slice 1 als falsch ermittelt, etwa durch eine von orchestrator.toml [paths].productive/[paths].generated abweichende Pfadauflösung oder Encoding-Behandlung, sodass der Ratschentest entweder von Beginn an rot ist oder künftigen Zuwachs verdeckt.<br>Zusätzlich könnte sich ein bislang unbekannter siebter Projektionsverlust finden, für den keine Ausnahme registriert ist; laut Plan ist das explizit eine Stoppbedingung und keine Gelegenheit zur stillen Korrektur.<br>Ein Test-Node könnte zwar als Quelltext existieren, aber wegen Parametrisierung oder Importfehlern nicht einsammelbar sein; dafür sieht der Plan den harten --collect-only-Nachweis vor.<br>Alle drei Risiken sind durch die geplanten Negativkontrollen auf tmp_path-Kopien und die Stoppregeln in Abschnitt 3 strukturell abgedeckt, bleiben aber bis zur tatsächlichen Slice-1-Ausführung unverifiziert.

<!-- artifact-records:test-approval-premortem:begin -->
Semantischer Record-Digest: `7f321a312d60`

Keine strukturierten Gates.
<!-- artifact-records:test-approval-premortem:end -->
<!-- audit:test-approval-premortem:end -->

### Findings-Lebenszyklus

<!-- audit:findings:begin -->
### `C-01` — `OPEN`

- Quelle: `claude`; Runde 1
- Klasse: `OBSERVATION`
- Finding: Der Plan behauptet konkrete, am Basiscommit reproduzierte Zählwerte (48 reguläre Dateien, 34 mit positivem Wert, 930 codex-/406 claude-Treffer) und einen abschließenden Sechs-Verlust-Katalog für die Codex-Projektion.<br>Diese Zahlen sind in diesem reinen Planreview nicht gegen den echten Quellbaum nachrechenbar und werden erst mit der Slice-1-Fixture sichtbar.
- Akzeptanztest: In der Slice-1-Implementierungsprüfung nachweisen, dass jede Datei/Namen-Kombination in provider-name-coupling-baseline-v1.json sowie die Gesamtsummen (930 codex, 406 claude über 34 von 48 regulären UTF-8-Dateien) exakt der deterministischen Zählung gemäß den im Plan festgelegten Pfad- und Regexregeln entsprechen, und dass der reale Projektionsdiff genau die sechs benannten Codex-Verluste ohne unregistrierten Rest enthält.<br>Jede Abweichung ist als Fund zu melden, nicht durch stille Fixture-Korrektur zu beheben.
- Statusbegründung: –

<!-- artifact-records:findings:begin -->
Semantischer Record-Digest: `7f321a312d60`

### Finding-Ereignisse

| Seq/Record | Finding | Rolle | Runde | Aktion | Klasse | Status | Begründung |
|---|---|---|---:|---|---|---|---|
| 11. `ar1-fb6e58b09a01` | `C-01` | `claude` | `–` | `opened` | `OBSERVATION` | `open` | Der Plan behauptet konkrete, am Basiscommit reproduzierte Zählwerte (48 reguläre Dateien, 34 mit positivem Wert, 930 codex-/406 claude-Treffer) und einen abschließenden Sechs-Verlust-Katalog für die Codex-Projektion.<br>Diese Zahlen sind in diesem reinen Planreview nicht gegen den echten Quellbaum nachrechenbar und werden erst mit der Slice-1-Fixture sichtbar. |

### Native convergence summary

| Finding | Work units | Rounds | Fingerprints | Claude decisions | Codex dispositions | Final status |
|---|---|---|---|---|---|---|
| `C-01` | `1` | `1` | `469650e41463` | `opened:open` | – | `open` |
<!-- artifact-records:findings:end -->
<!-- audit:findings:end -->

### Entscheidungstabelle

<!-- audit:decision-table:begin -->
| ID | Quelle | Finding | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|---|
| C-01 | claude | Der Plan behauptet konkrete, am Basiscommit reproduzierte Zählwerte (48 reguläre Dateien, 34 mit positivem Wert, 930 codex-/406 claude-Treffer) und einen abschließenden Sechs-Verlust-Katalog für die Codex-Projektion.<br>Diese Zahlen sind in diesem reinen Planreview nicht gegen den echten Quellbaum nachrechenbar und werden erst mit der Slice-1-Fixture sichtbar. | OBSERVATION | offen | offen |

<!-- artifact-records:decision-table:begin -->
Semantischer Record-Digest: `7f321a312d60`

| Seq | Record | Typ | Status | Logische ID | Revision | Fingerprint |
|---:|---|---|---|---|---:|---|
| 1 | `ar1-c121182bb5e4` | `task` | `accepted` | `task-contract` | 1 | `contract:c08c9766f70b` |
| 2 | `ar1-b1ee797b1d21` | `provider_input_measurement` | `measured` | `provider-input-1-codex_plan` | 1 | `implementation:c08c9766f70b` |
| 3 | `ar1-4a7c4932e6d6` | `provider_attempt` | `started` | `provider-operation-dc6737267b75-1` | 1 | `implementation:c08c9766f70b` |
| 4 | `ar1-328896fb51cb` | `agent_result` | `ready` | `agent-1-codex_plan-1` | 1 | `contract:c08c9766f70b` |
| 5 | `ar1-824491ec3afe` | `provider_attempt` | `succeeded` | `provider-operation-dc6737267b75-1` | 2 | `implementation:c08c9766f70b` |
| 6 | `ar1-66b6b921e3e9` | `validation_attestation` | `attested` | `plan-validation-22810d927493` | 1 | `implementation:22810d927493` |
| 7 | `ar1-105a141d4c6b` | `validation_attestation` | `attested` | `plan-validation-469650e41463` | 1 | `implementation:469650e41463` |
| 8 | `ar1-0a2ef47db00d` | `provider_input_measurement` | `measured` | `provider-input-1-claude_plan_review` | 1 | `implementation:c08c9766f70b` |
| 9 | `ar1-4b2997f80344` | `provider_attempt` | `started` | `provider-operation-65c4e27be669-1` | 1 | `implementation:c08c9766f70b` |
| 10 | `ar1-3b8e8d5ac5c8` | `review` | `decided` | `review-claude-1-1` | 1 | `implementation:469650e41463` |
| 11 | `ar1-fb6e58b09a01` | `finding_transition` | `recorded` | `finding-C-01` | 1 | `implementation:469650e41463` |
| 12 | `ar1-a095bc6d3cb2` | `provider_attempt` | `succeeded` | `provider-operation-65c4e27be669-1` | 2 | `implementation:c08c9766f70b` |

### Nachweis vollständiger Bindungswerte

| Kurzreferenz | Vollwert | Feldarten |
|---|---|---|
| `469650e41463` | `469650e41463f1f46cf44903ed3f24e60e043c786923a2a67fc36e8516e79751` | Technischer Wert, Fingerprint, Attestierungsreferenz, Record-ID |
| `7f321a312d60` | `7f321a312d600c6c8d77ef4f62ecf5f0a5db78bd29287a1a34e99dafe1ecd7c2` | Record-ID |
| `3b8e8d5ac5c8` | `3b8e8d5ac5c8643bfb4146922073743bec4bd950e28767ed542c45585436142c` | Request-ID, Technischer Wert |
| `6eaa723036ab` | `6eaa723036abd16253645d98f88a10ccadddde78b71013cace7613d881845ed4` | Request-ID |
| `be5fa5449a8d` | `be5fa5449a8d5178078a7f39899379aad53011f4a14e506a9be42523cbccd9ab` | Request-ID |
| `22810d927493` | `22810d927493c5ba4d4e162ca88644424ce3b49e7ab38e131537e0032a396385` | Attestierungsreferenz, Fingerprint, Technischer Wert |
| `667f1b8d90a4` | `667f1b8d90a4dea0bd1d136d5cfc752460fb56c8868ee4d8c99ef5f13cd2fa29` | Output-Digest |
| `b1ee797b1d21` | `b1ee797b1d214748b75619fe1bb8b2af495dd42d217ecf4af28f26446ada5c96` | Technischer Wert, Messungsreferenz |
| `2ff127626b13` | `2ff127626b13fe982c95f8ced5c8fe4f833dc7dc58d76829752294f8212a7058` | Digest |
| `9edf600f09ac` | `9edf600f09ac1b36a30675cc91b1f6769c4545f8f200a4fba413b4b9a4c9bc1e` | Policy-Digest |
| `02c83103f575` | `02c83103f5758d8e6998ddfb078d528975596c96b99b1a314003707416b57ac5` | Übergangsfingerprint |
| `66b6b921e3e9` | `66b6b921e3e98e96ef78cd7c291906e56b7ffe9e37d792f83da33381ec3db488` | Record-ID, Technischer Wert |
| `105a141d4c6b` | `105a141d4c6ba08d17a9f193d99a81b3ad125ae79cf483dccefec4c0241410f6` | Record-ID, Technischer Wert |
| `0a2ef47db00d` | `0a2ef47db00dd7a0f17343b253468a944d41033106e2fe2927b3acaced60af98` | Technischer Wert, Messungsreferenz |
| `68f8546ac0d7` | `68f8546ac0d75c07104b160c0f55acd70f9e4942f2d8f83d9a282ad4bfbc210f` | Digest |
| `3a222e46156d` | `3a222e46156d43b94907425cd9fa713e0bceeb0ec4ee6b9d8d8c9808e7690616` | Übergangsfingerprint |
| `65c4e27be669` | `65c4e27be6698cc0ea852997cf9b111da62f182c877c3c11f8689171b818369b` | Technischer Wert |
| `a095bc6d3cb2` | `a095bc6d3cb2e963b4762d1831ddea47de39aaa5190bbce69702c6f4ab8f2d3b` | Technischer Wert |
| `dc6737267b75` | `dc6737267b759f2ac3a980e7cbcfcf3ac420fc5cbc4a450e59a83d57d7dffbfe` | Technischer Wert |
| `824491ec3afe` | `824491ec3afe024871ff73dcc6c936684b41e315084a87781251361b424dfbee` | Technischer Wert |
| `fb6e58b09a01` | `fb6e58b09a01a519d43ec562ede660d9fa384bd563f3b21aef3374ee97a2ca0e` | Technischer Wert |
| `c121182bb5e4` | `c121182bb5e4ffff0e928cc78635027984cf8b6825f5481783cec8d095ad3315` | Fingerprint |
| `c08c9766f70b` | `c08c9766f70b0c0abfae455a242acbe57430a359535fba38006851732fa2cf35` | Technischer Wert |
| `4a7c4932e6d6` | `4a7c4932e6d6f7e2065a0886f90b9805969df8b1542b774203fa78a3f4b48521` | Technischer Wert |
| `328896fb51cb` | `328896fb51cbaeabeb9db90a4a1c0496886c96c64c3f8910b3bd3779851a5ff9` | Technischer Wert, Response-Digest |
| `4b2997f80344` | `4b2997f803446e01a69362a0de406e33bd137beff843e5f60faaeadf93529acc` | Technischer Wert |
| `d5ac2a49075f` | `d5ac2a49075fd9367866eb5ae13cf05630bf18358a15366154f7955049d64b78` | Request-ID |
| `a7ef8f6783aa` | `a7ef8f6783aa2d54f6a44133b211859fbb056b327f6011cac0dc8e8b2afb9c24` | Request-ID |
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
Semantischer Record-Digest: `7f321a312d60`

### Codex · Runde – · ready

| Seq/Record | Rolle | Runde | Status | Work-Unit | Tests | Transport | Request | Response | Fingerprint |
|---|---|---:|---|---|---|---|---|---|---|
| 4. `ar1-328896fb51cb` | `codex` | `–` | `ready` | `1` | keine | `native-codex-v2` | `native-codex-request-d5ac2a49075f` | `a7ef8f6783aa` | `c08c9766f70b` |
<!-- artifact-records:approval-status:end -->
<!-- audit:approval-status:end -->
