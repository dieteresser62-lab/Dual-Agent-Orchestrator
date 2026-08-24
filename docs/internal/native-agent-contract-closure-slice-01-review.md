# Native Agent Contract Closure – Slice 01 Reviewprotokoll

STATUS: IMPLEMENTED_AWAITING_CLAUDE_REVIEW

Datum: 24. August 2026

Arbeitsplan:
`docs/internal/native-agent-contract-closure-arbeitsplan.md`

## 1. Gegenstand

Slice 01 implementiert request-spezifische native Codex-Writerschemas. Das
allgemeine `native-agent-codex-result-v1`-Schema bleibt unverändert das
historische Leseschema. Jeder neue Codex-Request bindet stattdessen eine aus
seinem unveränderlichen `NativeCodexContext` erzeugte kanonische
Writerschemaprojektion.

Diese Datei wurde auf ausdrücklichen Wunsch des Auftraggebers als separates
Slice- und Reviewprotokoll angelegt. Sie erweitert den im Arbeitsplan
aufgeführten Änderungspfad ausschließlich um dieses Dokument; Produktcode und
Tests bleiben innerhalb des freigegebenen Slice-01-Pfads.

## 2. Provider-Subset-Sonden

Vor der ersten produktiven Writeränderung wurde
`scripts/native_contract_probe.py` angelegt und direkt gegen beide Provider
ausgeführt. Alle Aufrufe liefen in separaten temporären Arbeitsverzeichnissen
mit restriktiver Sandbox. Der bytegenaue Git-Status des echten Repositorys war
vor und nach jedem Aufruf identisch.

Gemessene Versionen und Profile:

- Codex: `codex-cli 0.147.0`, Modell `gpt-5.6-sol`, Effort `medium`;
- Claude: `2.1.241 (Claude Code)`, Modell `sonnet`, Effort `high`.

Ergebnisse:

| Schemafeature | Codex | Claude |
|---|---:|---:|
| Geschlossenes Objekt | akzeptiert | akzeptiert |
| `minItems`/`maxItems` | akzeptiert | akzeptiert |
| Array-`const` | abgelehnt | akzeptiert |
| Positionsgebundenes Tupel über `prefixItems` | abgelehnt | abgelehnt |
| Verschachteltes `oneOf` | abgelehnt | akzeptiert |
| Verschachteltes `anyOf` | akzeptiert | akzeptiert |

Die Writerimplementierung benötigt nur die positiv belegten Codex-Features
`closed_object`, `min_max_items` und `nested_any_of`. Deshalb wurde keine
Stopbedingung ausgelöst. Die abgelehnten Features und ihre lokalen
Restinvarianten sind in den versionierten Capability- und Ausnahmetabellen
erfasst.

## 3. Implementierte Änderungen

### 3.1 Gemeinsamer Provider-Schemakern

`src/native_provider_schema.py` stellt bereit:

- defensive, mutationsfreie Projektion des Leseschemas;
- kanonische Serialisierung und SHA-256-Bildung;
- streng typisiertes Laden der Capability- und Ausnahmetabellen;
- fail-closed Prüfung nicht belegter Schemafeatures;
- Providerprofil-Normalisierung mit unbekannten Argumenten als harter Drift;
- Versions- und Profilvergleich gegen die tatsächlich gemessene Tabelle.

### 3.2 Request-spezifischer Codex-Writer

`native_codex_provider_response_schema(context)` projiziert nur noch:

- den zum `NativeCodexRequestKind` gehörenden Ergebnisast;
- den geschlossenen `stop_result`-Ast;
- ausschließlich offene, gebundene Finding-IDs;
- exakte Dispositionskardinalität;
- soweit kontextseitig gebunden die erwartete Testdateimenge;
- `ready=false` bei nicht freigegebenen gebundenen Teständerungen;
- ausschließlich `stop_result`, wenn ein Planvertrag keinen Sliceplan fordert.

Die Hülle bleibt exakt `{"result": ...}`.

### 3.3 Bundle- und Adapterbindung

`NativeCodexRequestBundle` trägt die kanonischen Writerschemabytes selbst und
verifiziert bei der Konstruktion:

- erneute deterministische Ableitung aus dem Bound Context;
- Gleichheit mit dem `response_contract.schema_sha256`;
- kanonische JSON-Serialisierung.

Der native Codex-Adapter serialisiert ausschließlich diese Bundlebytes. Eine
unabhängige Adapter-Neuberechnung wurde entfernt. Vor dem Providerstart wird
seine echte `PreparedProviderInput.command` normalisiert und gegen das
gemessene Profil geprüft.

### 3.4 Disposition von Claude-Observation C-25

`C-25` wurde **ACCEPTED**. `--max-budget-usd` ist ein reiner Kostenwächter und
ändert weder Schema noch JSON-Akzeptanz. Das Argument wird deshalb wie
Sandbox, Arbeitsverzeichnis und Laufzeitpfade ausdrücklich nicht in das
Capability-Profil aufgenommen. Ein Regressionstest beweist, dass Budgetwerte
profilneutral sind, während Modell-, Effort-, Schemamechanismus- und stabile
Flagänderungen weiterhin fail-closed Profil-Drift erzeugen.

## 4. Dokumentierte lokale Codex-Restinvarianten

Die Datei `schemas/native-provider-schema-exceptions-v1.json` enthält genau
drei providerbelegte Ausnahmen:

1. Dispositionsreihenfolge und -eindeutigkeit, weil Codex weder
   `prefixItems` noch Array-`const` akzeptiert;
2. relative, traversalfreie und eindeutige Pfade, weil Lookaround und
   `uniqueItems` im Codex-Subset nicht verfügbar sind;
3. inhaltlich nichtleere Texte, weil der positive Regex-Lookahead nicht
   verfügbar ist.

Mitgliedschaft und Kardinalität der Findingdispositionen werden bereits im
Writerschema geschlossen. Die drei Restregeln bleiben in der allgemeinen
Reader- und Domänenvalidierung fail-closed.

## 5. Geänderte Pfade

- `schemas/native-provider-schema-capabilities-v1.json`
- `schemas/native-provider-schema-exceptions-v1.json`
- `scripts/native_contract_probe.py`
- `src/native_provider_schema.py`
- `src/native_codex_contract.py`
- `src/native_codex_request.py`
- `src/agent_adapters.py`
- `tests/test_native_provider_schema.py`
- `tests/test_native_codex_contract.py`
- `tests/test_native_codex_request.py`
- `tests/test_agent_adapters.py`
- `docs/internal/native-agent-contract-closure-slice-01-review.md`

`tests/test_agent_runtime.py` wurde ausgeführt, benötigte aber keine Änderung:
der bestehende native Runtime-Durchstich verwendet bereits ausschließlich den
gebundenen JSON-Parser und keinen Textmarkerparser.

## 6. Validierung

Fokussierte Matrix:

```text
python3 -m pytest tests/test_native_provider_schema.py tests/test_native_codex_contract.py tests/test_native_codex_request.py tests/test_agent_adapters.py tests/test_agent_runtime.py -q -p no:cacheprovider
141 passed
```

Vollständige Repositorymatrix:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -v -p no:cacheprovider
1233 passed in 136.12s
```

Diffprüfung:

```text
git diff --check
PASS
```

## 7. Reviewauftrag an Claude

Claude soll den vollständigen Slice-Diff adversarial gegen den freigegebenen
Arbeitsplan prüfen, insbesondere:

- ob jede request-spezifische Codex-Writerform tatsächlich nur erwarteten
  Resultattyp plus Stopast anbietet;
- ob Request, Bundle, `response_contract`, Adapterdatei und Providerinput
  dieselben kanonischen Schemabytes verwenden;
- ob Capability- und Profilbindung fail-closed und ohne versteckte
  Parallelberechnung sind;
- ob die drei registrierten Restinvarianten vollständig und eng genug sind;
- ob die Disposition von `C-25` sachlich korrekt und regressionsfest ist;
- ob historische Readerbytes und Textparserpfade unverändert bleiben.

CLAUDE_SLICE_REVIEW: PENDING

## Claude Implementierungsreview

Datum: 24. August 2026 · Reviewer: Claude · Modus: read-only

Grundlage: der uncommittete Slice-01-Diff gegen `c410eef`, der freigegebene
Arbeitsplan (`STATUS: CLAUDE_PLAN_APPROVED`, Abschnitte 4.1 bis 4.5, Slice 1),
`AGENTS.md` und die in Abschnitt 6 dieses Dokuments attestierten Testläufe
(141 fokussiert, 1233 vollständig, `git diff --check` sauber). Die vollständige
Suite wurde nicht erneut ausgeführt; zur Verifikation konkreter Verdachtsfälle
liefen ausschließlich kleine providerfreie Prüfungen gegen den Arbeitsbaum.

### Geprüfte Dimensionen

Request-Spezifität und Vertragsgeschlossenheit der Codex-Writerprojektion;
Ableitung von Resulttyp, Finding-IDs, Dispositionskardinalität, erwarteten
Testdateien und Readiness aus dem gebundenen Kontext; Byte- und Digestidentität
zwischen Builder, Bundle, `response_contract`, Adapterdatei und
Providerinput-Messung; Capability- und Ausnahmeregister auf Vollständigkeit,
Minimalität, Providerbeleg und Fail-closed-Verhalten; semantische
Vollständigkeit der Transportprofil-Normalisierung; Disposition von `C-25`;
Minimalität der lokalen Restprüfungen; Rückwärtskompatibilität von Leseschema,
Recordbindung und Resume; Isolation des Probe-Skripts; Aussagekraft der Tests.

Providerfrei nachgerechnet wurden: Profilgleichheit von Sonden- und
Produktionskommando für beide Provider gegen die Capability-Tabelle;
Budget-Neutralität; Drift bei Modell, Effort, unbekanntem Argument und
entferntem Semantikflag; Digest-Verschiedenheit der vier Writerformen; sowie
eine gezielte Differentialstichprobe aus schemavaliden Antworten gegen den
gebundenen Domänenparser.

### Was nachweislich geschlossen ist

Die providerfreie Stichprobe bestätigt die Kernaussagen des Slices. Je
Requestart bietet `result.anyOf` exakt den erwarteten Ergebnistyp plus
`stop_result`; die vier Writerformen besitzen verschiedene kanonische Digests;
ein `implementation_result` auf eine Plananforderung scheitert bereits am
Writerschema. Dispositionen sind auf die offenen Finding-IDs als `enum` und auf
exakte Kardinalität über `minItems`/`maxItems` eingeschränkt — geschlossene,
fremde und fehlende Referenzen sowie eine falsche Anzahl sind nicht
darstellbar. `ready=true` ist bei gebundenen, nicht freigegebenen Teständerungen
als `const false` ausgeschlossen; ein Planvertrag ohne Sliceplanpflicht bietet
ausschließlich `stop_result`. `RESULT_KIND_MISMATCH` ist damit als
nachgelagerter Fehlerpfad praktisch beseitigt.

Die Bytekette ist geschlossen: `build_native_codex_request()` bildet
`response_schema_json` einmal, leitet daraus `response_contract.schema_sha256`
ab und legt beides ins Bundle; `NativeCodexRequestBundle.__post_init__`
rekonstruiert das Schema deterministisch aus dem Bound Context, vergleicht
Objektgleichheit, kanonische Form und Digest; der Adapter serialisiert
ausschließlich `bundle.provider_response_schema_json` in die
`--output-schema`-Datei und misst dieselben Bytes. `_canonical_json()` in
`src/native_codex_request.py:395` und `canonical_schema_json()` in
`src/native_provider_schema.py:48` sind byteidentisch parametrisiert.

Die Transportprofil-Normalisierung ist semantisch tragfähig: Sonden- und
Produktionskommando normalisieren trotz abweichendem Binärpfad, Sandboxmodus,
Arbeitsverzeichnis, Schemawert, Systemprompt und temporären Pfaden auf
dasselbe Profil und auf den Tabelleneintrag; Modell-, Effort-, unbekannte
Argument- und Flagentfernungsabweichungen erzeugen Drift beziehungsweise einen
harten `NativeProviderSchemaError` statt stiller Filterung.

Rückwärtskompatibilität ist gewahrt: beide Resultat-v1-Schemadateien sind
unverändert, `defensive_provider_projection()` arbeitet auf einer Tiefenkopie,
historische Antworten werden weiterhin ausschließlich gegen das Leseschema
gelesen, das `request_id`-Format bleibt gleich, und kein Pfad rekonstruiert eine
historische Request-ID zum Vergleich — die geänderten Schemadigests brechen
daher weder Records noch Resume. Das Probe-Skript arbeitet ausschließlich in
`tempfile.TemporaryDirectory()`, ruft Codex mit `--sandbox read-only` und
`cwd=workdir` auf und vergleicht `git status --porcelain=v1
--untracked-files=all` vor und nach jedem Lauf.

### BLOCKER

**B-01 — Das Ausnahmeregister erfasst drei erreichbare lokale Ablehnungscodes
nicht.**

Pfad: `schemas/native-provider-schema-exceptions-v1.json`,
`src/native_codex_contract.py:270-296` (Writerprojektion),
`src/native_codex_contract.py:508-527` und `:413-424` (lokale Restprüfungen).

Ursache: Registriert sind ausschließlich die Fehlercodes
`finding-reference-invalid` und `schema-invalid`. Die Sortierordnung von
Pfadlisten ist in JSON Schema nicht ausdrückbar und über `prefixItems`
beziehungsweise Array-`const` im Codex-Subset nachweislich nicht ersetzbar; sie
bleibt deshalb zu Recht lokal — erzeugt aber drei andere Codes. Providerfrei
verifiziert:

- `enforce_expected_test_files=True`, erwartete Dateien `("tests/a.py",
  "tests/b.py")`, Antwort `["tests/b.py", "tests/a.py"]` → writer-schemavalide,
  lokal `test-files-invalid`;
- dynamischer Testscope (`enforce_expected_test_files=False`, produktiv über
  `src/workflow.py:1622` und `src/orchestrator.py:3266`), unsortierte
  `test_files` → writer-schemavalide, lokal `test-files-invalid`;
- unsortierte `scope_paths` in einem `plan_result` → writer-schemavalide, lokal
  `slice-plan-invalid`;
- unsortierte `remediation_paths` im `stop_result`-Ast, der in **jeder**
  Writerform enthalten ist → writer-schemavalide, lokal `stop-content-invalid`.

Der Eintrag `codex-safe-path-lookaround` nennt in `local_invariant` zwar
„sorted", registriert als `error_code` aber nur `schema-invalid`, und sein
benannter Regressionstest
(`test_writer_schema_keeps_safe_path_validation_fail_closed_locally`) belegt
ausschließlich die Traversal-Hälfte über `SCHEMA_INVALID`. Die Sortierhälfte ist
weder codiert noch getestet.

Auswirkung: Das Slice-1-Akzeptanzkriterium „verbleibende lokale
Eindeutigkeitsregeln sind registriert" und Arbeitsschritt 8 („Jeder Eintrag
benötigt Providerprobe, Fehlercode, Operation, fehlendes Feature und
Regressionstest") sind nicht erfüllt. Schwerer wiegt die Folgewirkung:
Abschnitt 4.4.3 erlaubt für provider-schemavalide Antworten ausschließlich
registrierte Fehlercodes, und Abschnitt 4.4.8 sowie die zugehörige
Stopbedingung verbieten ein Wachstum des Registers ohne erneutes Planreview.
Die Differentialmatrix in Slice 3 wird diese drei Codes zwangsläufig finden und
dann genau das nachträgliche Registerwachstum erzwingen, das der Plan
untersagt. Es handelt sich nicht um einen Entwurfsfehler der Projektion,
sondern um eine unvollständige Registrierung.

Akzeptanzkriterium: `schemas/native-provider-schema-exceptions-v1.json` deckt
`test-files-invalid`, `slice-plan-invalid` und `stop-content-invalid` mit je
eigenem Eintrag ab (Provider, Operationen, Fehlercode, lokale Invariante,
fehlendes Schemafeature, Providerbeleg, benannter Regressionstest) oder erweitert
`codex-safe-path-lookaround` um diese Codes; für jeden registrierten Code
existiert ein Test, der eine writer-schemavalide Antwort erzeugt und den exakten
`NativeCodexErrorCode` der lokalen Ablehnung behauptet — mindestens je einer für
unsortierte `test_files` unter `enforce_expected_test_files=True` und `False`,
für unsortierte `scope_paths` und für unsortierte `remediation_paths`. Ein
zusätzlicher Test weist nach, dass die Menge der in
`parse_bound_native_codex_contract_result()` für writer-schemavalide Eingaben
erreichbaren Fehlercodes vollständig in der Registermenge enthalten ist.

### OBSERVATIONS

**O-01 — Capability-Tabelle und Adapterversionsmuster sind zwei
handgespiegelte Literale.** `assert_provider_capabilities()` unterstützt
`cli_version=`, wird von beiden Adaptern aber ohne dieses Argument aufgerufen
(`src/agent_adapters.py:436-442`, `:950-956`). Die Versionsbindung wirkt
ausschließlich über `CapabilitySpec.supported_version_patterns`
(`src/agent_adapters.py:366`, `:809`), geprüft in
`agent_runtime.verify_agent_capabilities()`. Heute stimmen beide Quellen überein
(`codex-cli 0.147.0`, `2.1.241 (Claude Code)`), und eine abweichende
laufende CLI scheitert fail-closed. Es existiert jedoch kein Test, der
`capabilities-v1.json:cli_version` gegen das Adaptermuster prüft: eine
Musteranhebung ohne erneute Sonde würde die Fail-closed-Regel aus Abschnitt
4.1.8 genau für den Drift aushebeln, für den sie geschrieben wurde. Prüfbar
über einen Test, der beide Literale gegeneinander bindet; disponierbar in
Slice 2.

**O-02 — Die `ready`-Konstante ist an `contract.expected_test_files` statt an
die lokal entscheidende Bedingung gebunden.** `src/native_codex_contract.py:293`
setzt `ready: const false`, sobald erwartete Testdateien gebunden und nicht
freigegeben sind; die lokale Regel
(`src/native_codex_contract.py:523`) feuert dagegen auf `ready and test_files and
not test_changes_approved`. Daraus folgen zwei Randfälle: bei
`enforce_expected_test_files=False` mit gebundenen, nicht freigegebenen
Testdateien ist das lokal gültige `ready=true` mit leerer `test_files`-Liste
nicht darstellbar (Überrestriktion), und bei leerer `expected_test_files` mit
`require_test_files_record=True` und fehlender Freigabe bleibt `ready=true` mit
nichtleerer `test_files`-Liste schemavalide, obwohl es lokal mit
`test-files-invalid` scheitert (Unterrestriktion). Beide Konfigurationen sind
heute nicht produktiv erreichbar, weil `src/workflow.py:1615`
`test_changes_approved=True` fest verdrahtet. `CodexStepContract` ist aber ein
öffentlicher typisierter Vertrag mit `test_changes_approved: bool = False` als
Default. Prüfbar über zwei Tests für genau diese Kontexte; disponierbar in
Slice 3 zusammen mit der Differentialmatrix.

**O-03 — `request-mismatch` ist nicht im typisierten Register.** Abschnitt 3.3
des Plans dokumentiert die exakte Request-ID-Gleichheit bewusst als lokale
Bindungsinvariante wegen des zyklischen Digests, weshalb dies kein Blocker ist.
Abschnitt 4.4.3 lässt für writer-schemavalide Antworten jedoch ausschließlich
registrierte Codes zu, und eine Antwort mit formatgültiger, aber fremder
`request_id` ist writer-schemavalide und scheitert lokal mit `request-mismatch`.
Damit die Differentialmatrix in Slice 3 nicht an einer Formalie hängenbleibt,
sollte das Register einen Eintrag mit `missing_schema_feature:
"cyclic_request_id_const"` und Verweis auf Abschnitt 3.3 führen; disponierbar in
Slice 3.

**O-04 — Die Byteidentität der geschriebenen Schemadatei ist nicht getestet.**
`tests/test_agent_adapters.py` behauptet
`by_name["response_schema"] == bundle.provider_response_schema_json` für die
Messkomponente, prüft für die tatsächlich an `--output-schema` übergebene Datei
aber nur `json.loads(...)["type"] == "object"`. Die Identität gilt derzeit per
Konstruktion, ist aber der einzige Punkt der Kette ohne eigene Behauptung.
Prüfbar über eine Bytegleichheitsbehauptung auf dem Dateiinhalt; disponierbar
in Slice 2.

### Bewertung der Disposition von C-25

Die Disposition ist sachlich korrekt und regressionsfest; ein Blocker ergibt
sich daraus nicht. `_normalize_claude()` verwirft `--max-budget-usd` vor der
Flagpartition, und providerfrei nachgerechnet gilt: das normalisierte Profil ist
mit und ohne Budgetwert identisch und stimmt mit dem Tabelleneintrag überein,
während Modell, Effort, ein unbekanntes Argument und ein entferntes
Semantikflag weiterhin Drift beziehungsweise einen harten Fehler erzeugen.
`test_claude_budget_is_deliberately_profile_neutral_for_c25` sichert genau das
ab.

Eine fachlich relevante Vertragsabweichung kann sich dahinter nicht verbergen.
`--max-budget-usd` begrenzt ausschließlich die Kosten und beeinflusst weder die
Schemaübergabe noch die JSON-Akzeptanz des Providers. Ein Budgetabbruch
erscheint als `is_error=true` beziehungsweise als fehlendes
`structured_output` und wird in `NativeClaudeReviewAdapter.extract_output()`
fail-closed als `AgentOutputError` abgewiesen; er kann kein scheinbar gültiges,
vertragsverletzendes Resultat erzeugen. Die Aufnahme des Budgets ins Profil
hätte umgekehrt jede betriebliche Budgetänderung zu einem kostenpflichtigen
Neusondierungszwang gemacht.

### Bewertung der Tests

Die Tests erzählen die Implementierung überwiegend nicht nach, sondern weisen
Fehlverhalten nach: Drift bei Modell und Effort, unbekanntes Argument als harter
Fehler, Mutationsfreiheit des Leseschemas, nicht darstellbare Dispositionsmengen,
`ready=true` unter unfreigegebenen Teständerungen, nur `stop_result` ohne
Sliceplanvertrag, Bundle-Ablehnung bei nicht ableitbaren Schemabytes und der
echte Adapterbefehl gegen die Capability-Tabelle. Die drei
Ausnahme-Regressionstests belegen jeweils den Doppelschritt „writer-schemavalide,
lokal fail-closed" mit exaktem Fehlercode — die Sortierhälfte von
`codex-safe-path-lookaround` bleibt dabei ungetestet (B-01).
`test_capability_and_exception_tables_are_typed_and_versioned` behauptet mit
`len(registered_exceptions("codex")) == 3` eine reine Bestandszahl; diese
Behauptung ist erst dann aussagekräftig, wenn die Registervollständigkeit nach
B-01 gegen die tatsächlich erreichbaren Fehlercodes geprüft wird.

### Größtes Restrisiko

Das Register ist nach Abschnitt 4.4.3 die einzige erlaubte Ausfallfläche hinter
dem Providerschema und darf nach Abschnitt 4.4.8 später nicht wachsen. Solange
es unvollständig ist, verschiebt Slice 1 seinen eigentlichen Nachweis in die
Differentialmatrix von Slice 3 — dort trifft er dann auf eine Stopbedingung
statt auf eine Registrierung. Das Risiko ist nicht die Projektion, die inhaltlich
korrekt und eng ist, sondern die Buchführung über das, was bewusst lokal bleibt.

### Pre-Mortem

In drei Monaten ist der wahrscheinlichste Verlauf: Slice 3 baut die
Pflichtmutationsmatrix, findet `test-files-invalid` bei unsortierten Testpfaden
und `stop-content-invalid` im Stopast, und weil beide Codes nicht registriert
sind, greift die Stopbedingung mitten im letzten Slice. Unter Termindruck werden
die drei Einträge dann nachgetragen, ohne das vom Plan verlangte erneute
Planreview — die Registerdisziplin, die den gesamten Nachweis trägt, wird genau
an der Stelle aufgeweicht, an der sie zum ersten Mal wehtut. Die zweite,
leisere Variante: jemand hebt `supported_version_patterns` auf eine neue
Codex-Version an, ohne die Sonde zu wiederholen, weil kein Test beide Literale
bindet (O-01); die Projektion baut danach auf Fähigkeitszusagen, die für die
laufende CLI nie gemessen wurden.

### Abschlussurteil

B-01 ist offen. Die Begründung, warum provider-schemavalides JSON nicht
nachträglich an einer ebenfalls schema-ausdrückbaren Domänenregel scheitern
kann, wird bewusst nicht erteilt: für Resulttyp, Findingmenge,
Dispositionskardinalität, Testdateimenge und Readiness ist sie nachgewiesen,
aber die verbleibenden lokalen Ablehnungen sind nicht vollständig als
nicht-ausdrückbar registriert, und genau diese Registrierung ist der vom Plan
verlangte Nachweis.

CLAUDE_SLICE_APPROVAL: NO

## Korrekturrunde 1 nach Claude-Review

Status: `IMPLEMENTED_AWAITING_CLAUDE_CORRECTION_REVIEW`

### Disposition B-01

`B-01` wurde akzeptiert und behoben. Das typisierte Exception-Register enthält
nun zusätzlich eigenständige, operationsgebundene Einträge für:

- `request-mismatch` bei der zyklischen Request-ID-/Schemadigest-Bindung;
- `test-files-invalid` bei nicht kanonisch sortierten Testdateien in festem
  und dynamischem Testscope;
- `slice-plan-invalid` bei nicht kanonisch sortierten Slice-Pfaden;
- `stop-content-invalid` bei nicht kanonisch sortierten
  `remediation_paths`.

Der bestehende Safe-Path-Eintrag beschreibt danach nur noch die tatsächlich
von `regex_lookaround` und `uniqueItems` abhängigen Pfadinvarianten. Für jeden
registrierten Fehlercode existiert eine Regression, die zuerst die Gültigkeit
gegen das request-spezifische Writerschema und anschließend den exakten lokalen
Ablehnungscode nachweist. Eine zusätzliche Korporamatrix führt je einen
writer-schemavaliden Vertreter aller lokal erreichbaren Exception-Codes durch
`parse_bound_native_codex_contract_result()` und verlangt Mengengleichheit mit
dem Register.

### Disposition der Observations

- `O-01` wurde vorgezogen und geschlossen: Die exakten Versionsregexe von
  `NativeCodexAdapter` und `NativeClaudeReviewAdapter` werden nun direkt aus
  den sondierten `cli_version`-Werten der Capability-Tabelle erzeugt. Ein Test
  bindet beide Adapter an diese einzige Quelle.
- `O-02` wurde vorgezogen und geschlossen: Bei nicht freigegebenen
  Teständerungen bildet das Writerschema die lokale Readiness-Regel mit zwei
  vollständigen, geschlossenen Resultatvarianten ab. `ready=false` darf
  Testdateien melden; `ready=true` ist genau dann darstellbar, wenn
  `test_files` leer ist. Für festen nichtleeren Testscope bleibt ausschließlich
  `ready=false` möglich. Die beiden von Claude benannten Randkontexte besitzen
  eigene Regressionen.
- `O-03` wurde vorgezogen und geschlossen: Die wegen des zyklischen
  Request-Digests bewusst lokale Request-ID-Gleichheit ist jetzt als
  `cyclic_request_id_const` registriert und durch einen
  writer-schemavaliden Fremd-ID-Test belegt.
- `O-04` wurde geschlossen: Der Adaptertest vergleicht die Bytes der tatsächlich
  an `--output-schema` übergebenen Datei mit
  `bundle.provider_response_schema_json`, nicht mehr nur deren geparsten
  Objekttyp.

### Geänderte Pfade der Korrekturrunde

- `schemas/native-provider-schema-exceptions-v1.json`
- `src/agent_adapters.py`
- `src/native_codex_contract.py`
- `src/native_provider_schema.py`
- `tests/test_agent_adapters.py`
- `tests/test_native_codex_contract.py`
- `tests/test_native_provider_schema.py`
- `docs/internal/native-agent-contract-closure-slice-01-review.md`

### Korrekturvalidierung

- Fokussiert:
  `python3 -m pytest tests/test_native_provider_schema.py tests/test_native_codex_contract.py tests/test_native_codex_request.py tests/test_agent_adapters.py tests/test_agent_runtime.py -q -p no:cacheprovider`
  -> `150 passed in 6.58s`.
- Vollständige Matrix:
  `python3 -m pytest tests/ -v -p no:cacheprovider`
  -> `1242 passed in 138.52s`.
- `git diff --check`: `PASS`.

### Auftrag für das Korrekturreview

Claude prüft insbesondere, ob `B-01` vollständig geschlossen ist, die
Registermenge exakt den hinter dem Writerschema erreichbaren lokalen
Exception-Codes entspricht und die vorgezogenen Dispositionen `O-01` bis
`O-04` keine neue Über- oder Unterrestriktion erzeugen.

CLAUDE_CORRECTION_REVIEW: PENDING

## Claude Korrekturreview 1

Datum: 24. August 2026 · Reviewer: Claude · Modus: read-only

Konvergenzrunde nach `AGENTS.md`: es wird keine neue `OBSERVATION` eingeführt;
nicht handlungsbedürftige Restrisiken stehen unter „Größtes Restrisiko“, ein
neu entdeckter handlungsbedürftiger Defekt ist ein denyender `BLOCKER`.
Grundlage sind der uncommittete Diff, der freigegebene Arbeitsplan und die
attestierten Läufe (150 fokussiert, 1242 vollständig, `git diff --check` sauber).
Die vollständige Suite wurde nicht erneut ausgeführt; es liefen ausschließlich
kleine providerfreie Prüfungen zur Verifikation konkreter Verdachtsfälle.

### Geprüfte Dimensionen

Vollständigkeit und Feldqualität des Exception-Registers; Mengengleichheit
zwischen Register und den tatsächlich hinter dem Writerschema erreichbaren
lokalen Fehlercodes; Existenz und Beweiskraft der zitierten Regressionen;
Einzigkeit der Versionsquelle; exakte Deckungsgleichheit von Providerprojektion
und `_validate_test_files()` über die vollständige Kontextmatrix; sachliche
Richtigkeit der zyklischen Request-ID-Begründung; Byteidentität der
tatsächlich an `--output-schema` übergebenen Datei; Unveränderlichkeit der
Resultat-v1-Leseschemata und Mutationsfreiheit der Projektion; Verwendung
ausschließlich positiv sondierter Codex-Schemafähigkeiten.

### B-01 — CLOSED

`schemas/native-provider-schema-exceptions-v1.json` führt jetzt sieben
Einträge, die genau die sechs erreichbaren Fehlercodes abdecken:
`finding-reference-invalid`, `schema-invalid` (zweimal, für Safe-Text und
Safe-Path), `request-mismatch`, `slice-plan-invalid`, `stop-content-invalid`
und `test-files-invalid`. Provider, Operationen und Fehlercodes sind je Eintrag
korrekt: der Slice-Pfad-Eintrag ist zutreffend auf `plan` beschränkt, der
Testdatei-Eintrag auf `implementation` und `correction`, der Stop-Eintrag auf
alle vier Operationen, weil der `stop_result`-Ast in jeder Writerform enthalten
ist. Die neuen `array_lexicographic_order`-Belege benennen die tatsächliche
Ursache — Codex 0.147.0 lehnt `prefixItems` und Array-`const` ab, sodass JSON
Schema benachbarte generierte Pfadwerte nicht lexikographisch vergleichen kann;
das deckt sich mit `probe_evidence` in der Capability-Tabelle. Der
Safe-Path-Eintrag wurde korrekt auf die von `regex_lookaround` und
`uniqueItems` abhängigen Invarianten zurückgeschnitten, sodass er die
Sortierung nicht mehr fälschlich mitbehauptet.

Alle sieben zitierten `regression_test`-Pfade existieren als tatsächliche
Testfunktionen; jede weist den Doppelschritt „gegen das request-spezifische
Writerschema gültig, danach lokal mit exaktem Fehlercode abgewiesen“ nach,
einschließlich der von mir benannten Fälle für festen und dynamischen
Testscope, `scope_paths`, `remediation_paths` und die formatgültige fremde
`request_id`.

`test_registered_exception_codes_cover_writer_valid_local_rejections` erzwingt
Mengengleichheit zwischen Register und den durch konkrete
writer-schemavalide Gegenbeispiele ausgelösten Codes. Ich habe das unabhängig
nachgerechnet: eine eigene Matrix über alle vier Requestarten, beide
Testscope-Modi, leere und gebundene Testerwartung, freigegebene und nicht
freigegebene Teständerungen, mit und ohne offene Findings, mit fremder
Request-ID sowie mit blanken, überlangen, unsortierten, duplizierten und
traversierenden Werten erreicht hinter dem Writerschema exakt diese sechs Codes
und keinen weiteren. `result-kind-mismatch` ist durch die Union geschlossen,
`result-content-invalid` wird durchgängig vom Leseschema dominiert.

### O-01 bis O-04

- **O-01 — geschlossen.** `exact_cli_version_pattern()`
  (`src/native_provider_schema.py:127`) erzeugt das Muster per `re.escape()`
  aus dem sondierten `cli_version` der Capability-Tabelle; beide nativen
  Adapter beziehen es von dort (`src/agent_adapters.py:367`, `:810`). Eine
  zweite handgepflegte Versionsquelle existiert im nativen Pfad nicht mehr;
  das verbliebene `^codex-cli 0\.147\.\d+$` gehört zum textbasierten
  `CodexAdapter` und ist korrekt unberührt. Providerfrei geprüft: beide Muster
  treffen exakt die sondierte Version und keine Nachbarversion.
- **O-02 — geschlossen, ohne neue Über- oder Unterrestriktion.** Bei
  `test_changes_approved=False` bildet die Projektion zwei vollständige,
  geschlossene Varianten: `ready=false` mit den regulären Testdatei-Schranken
  und — nur wenn kein fester nichtleerer Testscope gebunden ist — `ready=true`
  mit `test_files` auf `minItems=maxItems=0`. Ich habe die vollständige Matrix
  aus `enforce_expected_test_files`, leerer/gebundener Testerwartung,
  Freigabezustand, `ready` und leerer/nichtleerer Testdateiliste providerfrei
  gegen `_validate_test_files()` gefahren: null schemavalide-aber-lokal-
  abgelehnte Fälle und null lokal-gültige-aber-schemaabgelehnte Fälle. Die
  frühere Unterrestriktion im dynamischen Scope und die frühere
  Überrestriktion sind beide beseitigt. Alle Objektvarianten bleiben durch
  `additionalProperties: false` geschlossen.
- **O-03 — geschlossen.** Der Eintrag `codex-request-id-binding` registriert
  `request-mismatch` mit `missing_schema_feature: cyclic_request_id_const`. Die
  Begründung trägt: `build_native_codex_request()` bildet den Schemadigest,
  legt ihn als `response_contract.schema_sha256` in das Binding und leitet
  daraus `request_digest` und `request_id` ab; nachgerechnet ist der
  Schemadigest im Request-Preimage enthalten. Eine `request_id`-Konstante im
  Schema würde also den Schemadigest ändern, der die Request-ID erst erzeugt —
  ein Fixpunktproblem, keine Subset-Lücke. Das Format bleibt schemagebunden,
  nur die exakte Gleichheit ist lokal.
- **O-04 — geschlossen.** `test_native_codex_adapter_uses_exact_request_and_output_schema`
  vergleicht `schema_path.read_bytes()` mit
  `bundle.provider_response_schema_json.encode("utf-8")`. Damit ist die letzte
  bisher nur indirekt geprüfte Stelle der Bytekette Builder → Bundle →
  `response_contract` → `--output-schema`-Datei → Messkomponente behauptet;
  die Digestgleichheit habe ich an einem echten Bundle nachgerechnet.

### Regressions- und Vertragsprüfung

Beide Resultat-v1-Schemadateien sind unverändert (Git meldet unter `schemas/`
ausschließlich die zwei neuen Providerdateien). Die Projektion mutiert das
geladene Leseschema nicht: nach Projektionen über alle vier Requestarten mit
beiden Freigabezuständen sind Instanzbytes und Dateibytes des Basisschemas
unverändert; die Readiness-Varianten entstehen als `copy.deepcopy` auf der
bereits defensiven Kopie. Bundle-Selbstprüfung, `response_contract`-Digest,
Adapterdatei und Providerinput-Messung verwenden dieselben Bytes. Eine neue
schema-ausdrückbare Domänenregel bleibt nicht unnötig lokal — die verbliebene
lokale Fläche ist Sortierordnung, Lookaround-Pfadsicherheit, Nichtleere von
Texten und die zyklische Request-ID-Gleichheit, alle mit Providerbeleg
registriert.

### BLOCKER

**B-02 — Die Readiness-Projektion verwendet eine Kompositionstiefe, die die
Subset-Sonde nie gemessen hat.**

Pfad: `src/native_codex_contract.py:315` (`result_refs[0] = {"anyOf":
readiness_refs}`), `schemas/native-provider-schema-capabilities-v1.json`,
`scripts/native_contract_probe.py:98`.

Ursache: Die Korrekturrunde ersetzt die direkte Ergebnisreferenz durch ein
verschachteltes `anyOf` und erzeugt damit `result: {"anyOf": [{"anyOf":
[...]}, {"$ref": stop}]}` — ein `anyOf` innerhalb eines `anyOf`. Die
Capability-Tabelle belegt für Codex ausschließlich `nested_any_of`, und das
zugehörige Sondenschema (`FEATURE_SCHEMAS["nested_any_of"]`) misst
`{"result": {"anyOf": [obj, obj]}}`, also genau eine Kompositionsebene
unterhalb einer Property. Eine zweite Ebene wurde nie sondiert. Dass Codex bei
exakt derselben Tiefe `oneOf` ablehnt (`probe_evidence.nested_one_of`), zeigt,
dass die Akzeptanz kompositionsformabhängig ist; die Tabelle wurde in dieser
Runde nicht erweitert. Verschärfend ist die Form bei festem nichtleerem
Testscope entartet: sie erzeugt `{"anyOf": [{"anyOf": [<eine Referenz>]}, ...]}`
und fügt damit Kompositionstiefe hinzu, ohne überhaupt etwas zu komponieren.

Auswirkung: Abschnitt 4.1.8 des freigegebenen Arbeitsplans ist normativ — „Eine
Projektion darf kein nicht bestätigtes Schemafeature verwenden“ — und das
Slice-1-Akzeptanzkriterium verlangt, dass ein nicht bestätigtes Schemafeature
fail-closed abgewiesen wird. Hier wird es stattdessen still verwendet.
Produktiv ist die Form heute nicht erreichbar, weil `src/workflow.py:1617` und
`:2238` `test_changes_approved=True` fest verdrahten; sie ist aber genau das
Schema, das jeder Kontext mit nicht freigegebenen Teständerungen an Codex
übergeben würde, und der Live-Canary in Slice 3 würde sie nur dann prüfen, wenn
er diesen Kontext gezielt erzeugt. `anyOf` ist assoziativ, die Verschachtelung
also ohne semantischen Zweck.

Akzeptanzkriterium: Entweder werden die Readiness-Referenzen in die
Top-Level-`result.anyOf`-Liste eingefügt statt verschachtelt, sodass jede
erzeugte Writerform höchstens die sondierte Kompositionstiefe verwendet und ein
Test für einen Kontext mit `test_changes_approved=False` in festem und in
dynamischem Testscope nachweist, dass kein `anyOf` innerhalb eines `anyOf`
auftritt; oder die Capability-Tabelle erhält einen eigenen, live sondierten
Eintrag für die tatsächlich verwendete Kompositionsform, `required_features` in
`native_codex_provider_response_schema()` fordert ihn an, und ein Test belegt,
dass die Projektion ohne diesen Eintrag fail-closed abgewiesen wird. In beiden
Fällen entfällt die entartete einelementige Verschachtelung.

### Größtes Restrisiko

Die lokale Ablehnungsfläche ist jetzt vollständig typisiert und
regressionsgesichert, aber ihre Vollständigkeit ruht auf einer handkuratierten
Gegenbeispielliste. Die Mengengleichheit gilt genau für die dort erzeugten
Fälle; ein künftig neu eingeführter lokaler Prüfpfad fiele erst dann auf, wenn
jemand ihn zusätzlich als writer-schemavalides Gegenbeispiel formuliert. Meine
unabhängige Breitensuche hat für den heutigen Stand keinen weiteren erreichbaren
Code gefunden, ersetzt aber keine erschöpfende Enumeration. Zweitens bleibt die
Capability-Tabelle eine Momentaufnahme einer einzelnen Sondierung: sie sagt
nichts darüber, welche Kompositionsformen jenseits der sechs gemessenen Muster
akzeptiert werden — genau daran hängt `B-02`.

### Pre-Mortem

In drei Monaten ist der wahrscheinlichste Verlauf, dass Slice 3 die
Live-Canaries mit `test_changes_approved=True` aufsetzt, weil die produktive
Workflowkonstruktion es so vorgibt. Die verschachtelte Readiness-Form wird dann
nie an einen echten Provider übergeben, `B-02` bleibt unbemerkt geschlossen, und
die Form schlägt erst dann zu, wenn jemand die R-10-Gate-Reihenfolge ändert und
`test_changes_approved` erstmals `False` erreicht — also in einem Moment, in dem
niemand mehr einen Schemafehler vermutet, sondern ein Providerproblem. Die
zweite Variante ist leiser: das Register bleibt korrekt, aber jemand fügt eine
lokale Prüfung hinzu, ohne ein Gegenbeispiel zu ergänzen; die Mengengleichheit
bleibt grün, weil sie nur die kuratierten Fälle kennt, und die Differentialmatrix
in Slice 3 findet die Lücke erst unter Termindruck.

### Urteil

`B-01` ist geschlossen, `O-01` bis `O-04` sind geschlossen, und die hinter dem
Writerschema verbleibende lokale Ablehnungsfläche ist vollständig typisiert,
registriert und regressionsgesichert. Freigegeben wird der Slice dennoch nicht:
`B-02` verletzt dieselbe Sonden-vor-Verwendung-Disziplin, die dieses
Arbeitspaket durchsetzen soll, ist konkret belegt und mit einer
semantikerhaltenden Umstellung behebbar.

CLAUDE_SLICE_APPROVAL: NO

## Korrekturrunde 2 nach Claude-Review

Status: `IMPLEMENTED_AWAITING_CLAUDE_CORRECTION_REVIEW_2`

### Disposition B-02

`B-02` wurde akzeptiert und behoben. Die Readiness-Varianten werden nicht mehr
als zweites `anyOf` innerhalb von `result.anyOf` eingebettet. Stattdessen
ersetzen die ein oder zwei gebundenen Readiness-Referenzen die ursprüngliche
direkte Ergebnisreferenz in derselben, bereits sondierten Top-Level-Union:

- fester nichtleerer Testscope ohne Testfreigabe: eine Referenz auf die
  `ready=false`-Variante plus `stop_result`;
- dynamischer Testscope ohne Testfreigabe: je eine Referenz auf
  `ready=false` und `ready=true` mit leerer Testdateiliste plus `stop_result`.

Damit entfällt sowohl das verschachtelte `anyOf` als auch die entartete
einelementige innere Union. Zwei Regressionen erzeugen die Writerprojektion
für festen und dynamischen Scope, prüfen die erwartete Zahl direkter
Referenzen und verlangen, dass jede Alternative ausschließlich aus `$ref`
besteht. Es wird kein über die vorhandene `nested_any_of`-Sonde hinausgehendes
Schemafeature verwendet.

Claude hat in der vorherigen Runde `B-01` sowie `O-01` bis `O-04` ausdrücklich
als geschlossen bestätigt. Korrekturrunde 2 ändert deren Implementierung oder
Disposition nicht.

### Geänderte Pfade der Korrekturrunde

- `src/native_codex_contract.py`
- `tests/test_native_codex_contract.py`
- `docs/internal/native-agent-contract-closure-slice-01-review.md`

### Korrekturvalidierung

- Fokussiert:
  `python3 -m pytest tests/test_native_provider_schema.py tests/test_native_codex_contract.py tests/test_native_codex_request.py tests/test_agent_adapters.py tests/test_agent_runtime.py -q -p no:cacheprovider`
  -> `152 passed in 6.86s`.
- Vollständige Matrix:
  `python3 -m pytest tests/ -v -p no:cacheprovider`
  -> `1244 passed in 144.24s`.
- `git diff --check`: `PASS`.

### Auftrag für das zweite Korrekturreview

Claude prüft, ob `B-02` durch die flache Union vollständig geschlossen ist,
alle erzeugten Writerformen ausschließlich die bereits positiv sondierte
Kompositionstiefe verwenden und die in Korrekturrunde 1 bestätigte exakte
Readiness-Semantik unverändert erhalten bleibt.

CLAUDE_CORRECTION_REVIEW_2: PENDING

## Claude Korrekturreview 2

Datum: 24. August 2026 · Reviewer: Claude · Modus: read-only

Konvergenzrunde nach `AGENTS.md`: keine neue `OBSERVATION`; nicht unmittelbar
handlungsbedürftige Restrisiken stehen unter „Größtes Restrisiko“, ein neu
entdeckter handlungsbedürftiger Defekt wäre ein denyender `BLOCKER`. Grundlage
sind der uncommittete Diff und die dokumentierte Attestierung (152 fokussiert,
1244 vollständig, `git diff --check` sauber). Weder die vollständige noch die
fokussierte Suite wurde erneut ausgeführt.

### Geprüfte Dimensionen

Form der Resultat-Union über alle erzeugbaren Writerformen; Abwesenheit jeder
zweiten Kompositionsebene und jeder entarteten einelementigen Union; Notwendigkeit
eines neuen Capability-Eintrags; Verfügbarkeit des Stopasts in jeder Writerform;
exakte Deckungsgleichheit von Providerprojektion und `_validate_test_files()`;
Regressionsfreiheit von `B-01`, `O-01` bis `O-04`, Exception-Register,
Request-ID-Bindung, Schemabyte-Identität, Versionsbindung und historischen
Leseschemata; Änderungsumfang der Korrekturrunde.

### Gezielt ausgeführte Zusatzprüfungen

Die Attestierung belegt, dass alle Tests grün sind, aber nicht, welche
Schemaform über die nicht getesteten Kontextkombinationen hinaus tatsächlich
entsteht — genau das war der Gegenstand von `B-02`. Ich habe deshalb zwei kleine
providerfreie Prüfungen gegen den Arbeitsbaum gefahren, ohne Produktcode oder
Tests zu berühren:

1. Erzeugung der Writerprojektion für 28 Kontextvarianten (alle vier
   Requestarten, beide Testscope-Modi, leere und gebundene Testerwartung,
   freigegebene und nicht freigegebene Teständerungen, Planvertrag mit und ohne
   Sliceplanpflicht) mit rekursiver Suche nach `anyOf`, `oneOf`, `allOf`, `not`,
   `if`, `then` und `else` innerhalb der Alternativen und innerhalb von `$defs`.
2. Erneuter Durchlauf meiner Readiness-Matrix aus Korrekturrunde 1 sowie meiner
   Erreichbarkeitssuche über die lokalen Fehlercodes, um Regressionen der
   bereits bestätigten Semantik auszuschließen.

### B-02 — CLOSED

`src/native_codex_contract.py:317` ersetzt die ursprüngliche Ergebnisreferenz
jetzt per `result_refs[0:1] = readiness_refs` innerhalb derselben flachen Liste,
statt sie in ein zweites `anyOf` zu verpacken. Die gemessenen Formen entsprechen
exakt der Vorgabe:

- fester nichtleerer Testscope ohne Freigabe:
  `{"anyOf": [{"$ref": ".../bound_implementation_result_ready_false"}, {"$ref": ".../stop_result"}]}`;
- dynamischer Testscope ohne Freigabe:
  `{"anyOf": [{"$ref": ".../bound_implementation_result_ready_false"}, {"$ref": ".../bound_implementation_result_ready_true_no_tests"}, {"$ref": ".../stop_result"}]}`;
- mit Testfreigabe: `{"anyOf": [{"$ref": ".../implementation_result"}, {"$ref": ".../stop_result"}]}`;
- Planvertrag ohne Sliceplanpflicht: `{"anyOf": [{"$ref": ".../stop_result"}]}`.

Über alle 28 geprüften Varianten ist jede Alternative ein reines `$ref` — keine
Alternative trägt ein weiteres Schlüsselwort neben `$ref`. Die rekursive Suche
findet weder innerhalb der Alternativen noch irgendwo in `$defs` ein
Kompositionsschlüsselwort. Die gesamte Writerprojektion enthält damit genau eine
Kompositionsstelle: das eine `anyOf` unterhalb der Property `result` aus
geschlossenen, direkt referenzierten Objektdefinitionen. Das ist bitgenau die
Form, die `FEATURE_SCHEMAS["nested_any_of"]` sondiert hat und die die
Capability-Tabelle für Codex mit `nested_any_of: true` belegt. Ein neues
Capability-Feature und eine Änderung der Tabelle sind deshalb zu Recht nicht
erforderlich; `required_features=("closed_object", "min_max_items",
"nested_any_of")` deckt die verwendete Konstruktion jetzt vollständig ab. Die
entartete einelementige innere Union tritt in keiner Variante mehr auf.

### Readiness-Semantik unverändert exakt

Die vollständige Matrix aus `enforce_expected_test_files`, leerer und gebundener
Testerwartung, Freigabezustand, `ready` sowie leerer und nichtleerer
Testdateiliste ergibt weiterhin null schemavalide-aber-lokal-abgelehnte und null
lokal-gültige-aber-schemaabgelehnte Fälle. Im Einzelnen bestätigt: `ready=false`
darf Testdateien melden; `ready=true` ist bei fehlender Testfreigabe nur mit
leerer `test_files`-Liste darstellbar; fester nichtleerer Testscope ohne Freigabe
lässt ausschließlich `ready=false` zu; dynamischer Testscope ohne Freigabe lässt
`ready=true` mit leerer Liste zu. `stop_result` ist in jeder der 28 Writerformen
als Alternative vorhanden, einschließlich der Planform ohne Sliceplanpflicht, wo
es die einzige Alternative ist.

### Bereits geschlossene Punkte bleiben geschlossen

`B-01`: Das Register führt unverändert sieben Einträge, und meine
Erreichbarkeitssuche findet hinter dem Writerschema exakt die sechs registrierten
Codes `finding-reference-invalid`, `schema-invalid`, `request-mismatch`,
`slice-plan-invalid`, `stop-content-invalid` und `test-files-invalid`, keinen
weiteren. `O-01`: `exact_cli_version_pattern()` bleibt die einzige Versionsquelle
beider nativer Adapter und liefert weiterhin exakt die sondierten Versionen.
`O-02`: siehe Readiness-Semantik. `O-03`: der Eintrag `codex-request-id-binding`
ist unverändert, und der Schemadigest steckt nachgerechnet weiterhin im
Request-Preimage. `O-04`: die Bytegleichheitsbehauptung auf der
`--output-schema`-Datei ist unverändert vorhanden; Bundlebytes und
`response_contract.schema_sha256` stimmen an einem echten Bundle überein. Die
vier Requestarten besitzen weiterhin verschiedene kanonische Schemadigests, das
geladene Resultat-v1-Leseschema wird nicht mutiert, und unter `schemas/` meldet
Git ausschließlich die beiden neuen Providerdateien — beide Reader-Baselines sind
unverändert. Der Änderungsumfang der Runde bleibt im deklarierten Pfad: gegenüber
Korrekturrunde 1 sind allein `src/native_codex_contract.py` und
`tests/test_native_codex_contract.py` gewachsen.

### Neue BLOCKER

Keine.

### Größtes Restrisiko

Unverändert gegenüber Korrekturrunde 1 und nicht unmittelbar handlungsbedürftig:
Die Vollständigkeit der lokalen Ablehnungsfläche ruht auf einer handkuratierten
Gegenbeispielliste; ein künftig neu eingeführter lokaler Prüfpfad fiele erst auf,
wenn ihn jemand zusätzlich als writer-schemavalides Gegenbeispiel formuliert.
Meine unabhängige Breitensuche findet für den heutigen Stand keinen weiteren
erreichbaren Code, ersetzt aber keine erschöpfende Enumeration. Zweitens bleibt
die Capability-Tabelle die Momentaufnahme einer einzelnen Sondierung: sie belegt
sechs Musterformen, nicht den gesamten akzeptierten Schemaraum. Die
Readiness-Varianten sind produktiv derzeit nicht erreichbar, weil
`src/workflow.py:1617` und `:2238` `test_changes_approved=True` fest verdrahten;
ihre praktische Providerakzeptanz wird deshalb erst dann empirisch belegt, wenn
ein Slice-3-Canary diesen Kontext gezielt erzeugt. Beides ist mit der jetzt
flachen, sondierten Kompositionsform kein Vertragsrisiko mehr, sondern eine
Nachweisgrenze.

### Pre-Mortem

In drei Monaten ist der wahrscheinlichste Verlauf, dass die R-10-Gate-Reihenfolge
geändert wird und `test_changes_approved` erstmals produktiv `False` erreicht.
Die gebundenen Readiness-Varianten gehen dann zum ersten Mal real an Codex.
Weil ihre Kompositionsform nachweislich der sondierten entspricht, ist der
Schemateil abgesichert; das verbleibende Risiko liegt darin, dass niemand mehr
weiß, dass diese Formen bis dahin nie live liefen, und ein dann auftretender
Providerfehler zuerst am falschen Ende gesucht wird. Die zweite, leisere Variante
ist unverändert: jemand fügt eine lokale Prüfung hinzu, ohne ein Gegenbeispiel
zu ergänzen; die Mengengleichheit bleibt grün, weil sie nur die kuratierten Fälle
kennt, und die Differentialmatrix in Slice 3 findet die Lücke erst unter
Termindruck.

### Urteil

`B-02` ist geschlossen, `B-01` sowie `O-01` bis `O-04` bleiben geschlossen, und
es entsteht kein neuer Blocker.

Die korrigierte Projektion verwendet keine unsondierte Schema-Komposition mehr:
über alle 28 erzeugbaren Writerformen enthält sie genau eine Kompositionsstelle,
nämlich das einzelne `anyOf` unterhalb der Property `result`, dessen Alternativen
ausnahmslos reine `$ref` auf geschlossene Objektdefinitionen sind; weder
innerhalb dieser Alternativen noch in `$defs` existiert ein weiteres
Kompositionsschlüsselwort. Genau diese Form hat die Subset-Sonde für Codex
positiv gemessen und die Capability-Tabelle als `nested_any_of` festgehalten,
weshalb kein neues Feature und keine erneute Sondierung nötig ist.

Die Readiness-Regel entspricht weiterhin exakt dem lokalen Domänenvertrag: über
die vollständige Kontextmatrix existiert kein provider-schemavalides Resultat,
das `_validate_test_files()` anschließend ablehnt, und kein lokal gültiges
Resultat, das die Projektion ausschließt. Damit gilt für die in Slice 01
geschlossenen Regeln — Resultatart, Findingmenge, Dispositionskardinalität,
Testdateimenge und Readiness — dass ein provider-schemavalides JSON nicht
nachträglich an einer ebenfalls schema-ausdrückbaren Domänenregel scheitern kann;
die verbleibende lokale Ablehnungsfläche besteht ausschließlich aus den sechs
typisierten, providerbelegten und regressionsgesicherten Registereinträgen.

CLAUDE_SLICE_APPROVAL: YES
