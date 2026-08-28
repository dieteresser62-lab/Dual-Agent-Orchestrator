# Slice 2 - Structured-Output-Druck beweisbar messen und sicher begrenzen

## Status und Reviewgrenze

- Arbeitsplan: `docs/internal/orchestrator-stabilisierung-uebergangsmatrix-structured-output-arbeitsplan.md`
- freigegebener Ausgangsstand: `13421f6 test(workflow): cover transition matrix boundaries`
- Reviewgegenstand: der noch nicht committete Slice-2-Diff gegen `13421f6`
- Betriebsart: manuelle Entwicklung außerhalb von `run_task`
- externer Provider-Canary: nicht ausgeführt

## Umgesetzte Arbeit

### 1. Reproduzierbare Druckinventur

`tests/fixtures/structured_output_pressure/manifest-v1.json` inventarisiert alle
13 heute begrenzten Claude-Freitextfelder. Die eingetragenen historischen
Messwerte werden nicht als Autorität übernommen: Der Test liest die sechs
Claude-Antworten aus dem 14-zeiligen, ausdrücklich als
`historical_schema_only` klassifizierten Corpus neu ein und verlangt für jeden
Pfad exakte Feldmenge, Anzahl und Maximallänge.

Die höchste beobachtete Grenzauslastung liegt bei
`review_evidence.dimensions` mit 2893 von 3000 Zeichen (96,4 Prozent), gefolgt
von `status_changes[].rationale` mit 2000 von 3000 Zeichen (66,7 Prozent).
Diese Messung belegt realen Felddruck. Sie belegt ausdrücklich nicht, dass ein
bestimmter Providerfehler durch diese Feldlängen verursacht wurde.

Für jedes inventarisierte Feld werden die Grenzfälle 50, 80, 95, 100 und 101
Prozent direkt aus dem tatsächlich erzeugten Writerschemafragment abgeleitet.
Bis einschließlich 100 Prozent muss die lokale Validierung annehmen; 101
Prozent muss sie mit einem gebundenen `SchemaMismatch` ablehnen.

### 2. Geschlossene lokale `maxLength`-Semantik

Die Messphase fand eine konkrete Vertragslücke: Die request-spezifischen
Writerschemas enthielten `maxLength`, während `src/schema_validation.py` das
Keyword weder als unterstützten Teil der lokalen Schemateilmenge anerkannte
noch bei Strings durchsetzte. Dadurch konnte ein providerseitig unzulässiger
String lokal als gültig erscheinen.

Der lokale Validator erkennt und erzwingt `maxLength` nun mit derselben
Zeichenlängensemantik wie die vorhandene `minLength`-Prüfung. Eine fokussierte
Regression bindet die exakte Grenze, den Ablehnungsfall und den Fehlerpfad.
Die bestehenden Differentialtests bleiben grün.

### 3. Weiches Zielbudget ohne Inhaltsverlust

Die produktive native Claude-Policy fordert vollständige, aber knappe
Freitextfelder und nennt 80 Prozent der jeweiligen Writerschema-Grenze als
weiches Ziel. Sie verbietet zugleich ausdrücklich, Findings, erforderliche
Dispositionen, Reviewevidenz oder das Pre-Mortem zur Einhaltung dieses Ziels
wegzulassen. Die harte Writerschemabegrenzung bleibt die technische
Schutzschicht.

### 4. Ursachen- und Retryabgrenzung

Die Fixturedeklaration klassifiziert
`error_max_structured_output_retries` ausschließlich als
Providererschöpfung mit lokal unbekannter Einzelursache. Sie behauptet weder
einen Längenverstoß noch einen sonstigen konkreten Schemaverstoß.

Der bekannte exakte Claude-Envelope wird für `AgentProcessError` und
`AgentOutputError` als begrenzt transient erkannt. Falscher Provider,
abweichender `type`, abweichender `subtype` und unvollständige Hüllen bleiben
fail-closed als Prozessfehler. Zusätzlich weist der Test nach, dass aus der
eingehenden Diagnose ausschließlich die zwei erlaubten Felder erhalten
bleiben; ein beigefügter verworfener Modellinhalt wird nicht persistiert.

### 5. Writerschemamessung und Bindungsentscheidung

Alle vier request-spezifischen Claude-Writerformen werden zweimal gebaut und
auf Bytegröße, Tiefe, Kompositionsanzahl, Definitionsanzahl und Digeststabilität
vermessen:

| Form | Bytes | Tiefe | Kompositionen | Definitionen | SHA-256 |
|---|---:|---:|---:|---:|---|
| Plan | 10286 | 10 | 11 | 18 | `3db7549f4ed7c45eb810980038f9fd1f04a738a71396ecfd719501866aa6261e` |
| erster Slice-Review | 10349 | 10 | 11 | 18 | `188229cab90f1061eba619da4e059b3e9feac494337460e794c07fa882a46e7e` |
| Konvergenzreview | 10585 | 11 | 12 | 18 | `e41b22c001edbdae297ce9e3be08896396c92cdcdf60ac1505d54c5525017e74` |
| Finalreview | 10274 | 10 | 11 | 18 | `a65e65ff18bf594ecb5d7fb4c622eb34b4c8a91e1809058b369f44a6aa04c790` |

Die Werte dienen dem Review als nachzurechnende Evidenz, nicht als erwartete
Autorität. Die Inventur rechtfertigt keine Lockerung der Writerschemas.
Deshalb wurden weder Schemaquellen noch Request- oder Digestbildung geändert;
es gibt keine absichtlich neu zu bindenden Writerschemadigests oder Request-IDs.

Der optionale Live-Canary bleibt deaktiviert. Seine erforderlichen
Nachweisfelder sind dokumentiert, aber weder Slice noch Tests führen einen
Provideraufruf aus.

## Änderungspfade

- `src/prompts.py`
- `src/schema_validation.py`
- `tests/fixtures/structured_output_pressure/manifest-v1.json`
- `tests/test_prompts.py`
- `tests/test_schema_validation.py`
- `tests/test_structured_output_pressure.py`
- `docs/internal/orchestrator-stabilisierung-uebergangsmatrix-structured-output-slice-02-review.md`

## Validierung

Fokussierte Matrix:

```text
python3 -m pytest tests/test_structured_output_pressure.py tests/test_schema_validation.py tests/test_prompts.py tests/test_native_contract_differential.py tests/test_agent_runtime.py -q -p no:cacheprovider
66 passed in 1.21s
```

Vollständige konfigurierte Matrix:

```text
python3 -m pytest tests/ -v -p no:cacheprovider
1065 passed in 118.37s
```

Zusätzlich ist `git diff --check` sauber. Es wurde kein externer Provider
aufgerufen.

## Reviewauftrag

Claude soll die Messwerte unabhängig aus Writerschemas und Corpus ableiten,
statt Zahlen oder Digests aus diesem Bericht als Erwartungswerte zu übernehmen.
Besonders zu falsifizieren sind die Vollständigkeit der Feldinventur, die
lokale `maxLength`-Semantik, eine mögliche falsche Ursachenbehauptung, die
Exaktheit der Retryhülle, die Nichtpersistenz verworfener Inhalte sowie eine
unbeabsichtigte Lockerung erforderlicher Reviewinhalte durch das weiche
Zielbudget.

TEST_FILES_TOUCHED: tests/test_prompts.py, tests/test_schema_validation.py, tests/test_structured_output_pressure.py
IMPLEMENTATION_READY: 2 | YES
STATUS: DONE

REVIEWER: claude

# Claude-Slicereview 02 (manuell, adversarial)

## W1. Reviewgrenze und Methodik

Manueller adversarialer Review außerhalb von `run_task`, ohne strukturierte
JSON-Ausgabe, ohne Orchestrator- oder Provideraufruf. Gelesen wurden
`AGENTS.md`, `CLAUDE.md`, der freigegebene Arbeitsplan, der
Implementierungsbericht oben, der vollständige Arbeitsbaum-Diff gegen
`13421f6` sowie die berührten Produktionsmodule und ihre Aufrufer:
`src/prompts.py`, `src/schema_validation.py`, `src/native_review_contract.py`,
`src/native_review_request.py`, `src/agent_adapters.py` und
`src/agent_runtime.py`.

Sämtliche Zahlen unten habe ich selbst erhoben. Die Feldinventur stammt aus
einem eigenen Schemadurchlauf über alle vier Writerformen, die Corpusmessung
aus einem eigenen generischen JSON-Walk ohne Wiederverwendung des
Testextraktors, und der Vergleich der `maxLength`-Semantik lief gegen die aus
`13421f6` rekonstruierte Fassung von `src/schema_validation.py`. Weder
Produktcode noch Tests wurden geändert.

Selbst ausgeführt:
`python3 -m pytest tests/test_structured_output_pressure.py tests/test_schema_validation.py tests/test_prompts.py tests/test_native_contract_differential.py -q -p no:cacheprovider`
→ `22 passed`.

## W2. Inventur und unabhängige Evidenz

**Corpusmessung: vollständig bestätigt.** Der Corpus enthält 14 Zeilen, davon
6 mit `provider == "claude"` und 8 mit `codex`. Mein generischer Walk über alle
Stringblätter der sechs Claude-Antworten reproduziert alle 13 Manifestwerte
exakt — Anzahl und Maximum je Pfad ohne eine einzige Abweichung. Die höchste
Grenzauslastung liegt bei `review_evidence.dimensions` mit 2893 von 3000
Zeichen, also 96,43 Prozent. Alle weiteren Stringpfade im Corpus
(`request_id`, `schema_version`, Enums, Findingkennungen) tragen im
Writerschema keine `maxLength` und fehlen daher zu Recht. Die
Bindungsklasse `historical_schema_only` wird nirgends in eine technische
Autorität oder Live-Transportbindung überführt; der Corpus liefert
ausschließlich Messwerte.

**Feldinventur: unvollständig, und die Vollständigkeit ist nicht
schemagebunden.** Mein eigener Durchlauf über die vier request-spezifischen
Writerformen findet 29 verschiedene `maxLength`-gebundene Stellen; das
Manifest deklariert 13. Der größte Teil der Differenz sind formspezifische
Duplikate bereits inventarisierter logischer Pfade mit identischer Grenze
(`pre_mortem` je Approved- und Denied-Definition aller vier Formen,
`stop_request.rule_id` und `stop_request.rationale` je Form). Zwei Stellen sind
jedoch keine Duplikate:

- `validation_acceptance.argv.items` mit `maxLength` 512 und `maxItems` 32.
  Das ist das modellgeschriebene Argumentfeld des typisierten
  Validierungskommando-Akzeptanztests, den nur ein `BLOCKER` verwenden darf. Es
  trägt die **engste Grenze des gesamten Vertrags** — die kleinste im Manifest
  geführte Grenze ist 200, die nächstkleinere Prosagrenze 1000 — und besitzt
  weder einen Manifesteintrag noch eine der geforderten Grenzfallproben bei 50,
  80, 95, 100 und 101 Prozent. Sein Gegenstück `prose_acceptance.text` ist
  inventarisiert; die Union der Akzeptanztestformen ist damit nur zur Hälfte
  erfasst.
- `bound_approved_finding.summary` mit `maxLength` 3000. Ein freigebender
  Review darf neue Findings tragen
  (`bound_plan_approved.new_findings.items → bound_approved_finding`); das
  Manifest bindet `new_findings[].summary` ausschließlich an
  `bound_denied_finding.summary`. Die Grenze ist identisch, die Messung
  deshalb sachlich richtig, die Definitionsbindung aber unvollständig.

**Die Mengengleichheit bricht nicht fail-closed.** Die Zusicherung lautet
`{item["path"] for item in fields} == set(observed)`, wobei `observed` aus dem
handgeschriebenen 13-Schlüssel-Wörterbuch in `_observed_claude_texts` stammt.
Beide Seiten sind Literale desselben Testmoduls; im ganzen Testmodul existiert
kein Schemadurchlauf, `maxLength` kommt genau einmal vor. Gegenprobe: Fügt man
dem Writerschema ein zusätzliches begrenztes Feld hinzu, bleiben der
Vollständigkeitstest und alle Grenzfalltests bei 50, 100 und 101 Prozent grün.
Ergänzend liest der Grenzfalltest ausschließlich die `$defs` der **Planform**;
formspezifische Definitionen werden nie an einer Grenze geprüft.

Damit ist das Akzeptanzkriterium „Für jedes begrenzte Freitextfeld liegt eine
reproduzierbare Inventur erfolgreicher und synthetischer Fixtures vor" nicht
erfüllt. Siehe `C-12`.

**Corpusquelle.** Die aktive Suite liest jetzt eine Datei unterhalb von
`docs/internal/archive/`. Der Pfad steht ausschließlich im JSON-Fixture; der
vorhandene Repositoryguard, der genau dieses Lesen verhindern soll, durchsucht
nur `*.py` und bleibt deshalb grün. Siehe `C-13`.

## W3. Writerschema und lokale Validierung

**Die Lücke war real und ist im Produktionspfad geschlossen.** Gegen die aus
`13421f6` rekonstruierte Fassung geprüft: ein
`review_evidence.dimensions` mit 3001 Zeichen wurde von
`validate_schema_document` gegen das echte Writerfragment **akzeptiert**; mit
dem Arbeitsbaumstand wird es abgelehnt, und die Ablehnung erreicht den
produktiven Eintrittspunkt
`validate_native_review_provider_response_for_context` als
`schema-invalid`. Zusätzlich scheiterte `check_schema` auf der Writerform
zuvor hart mit `unsupported schema keyword ... maxLength` und ist jetzt
selbstprüfbar. Die Behauptung des Berichts hält.

Die Grenzfälle habe ich an den wirklichen Writerfragmenten nachvollzogen: bis
einschließlich 100 Prozent akzeptiert die lokale Validierung, ab 101 Prozent
lehnt sie mit gebundenem `SchemaMismatch` ab. Die Einordnung im Validator
liegt korrekt zwischen `minLength` und `pattern`; `anyOf`- und
`oneOf`-Zweige laufen über `_schema_branch_matches` in dieselbe Knotenprüfung,
sodass die Grenze auch innerhalb von Kompositionen greift.

**Zwei ehrliche Einschränkungen.** Erstens ist die Zeichenlänge
`len(str)`, also Codepoints. Das entspricht der Schemadefinition, bedeutet aber,
dass 3000 Codepoints aus der astralen Ebene 6000 UTF-16-Einheiten und 12000
UTF-8-Bytes sind; eine anders zählende Providerseite bliebe unentdeckt, und
keine Probe verwendet Nicht-ASCII. Zweitens ist der präzise Fehlerpfad nur am
isolierten Fragment sichtbar: in der echten Dokumentform meldet die Validierung
`must match exactly one allowed schema` am Pfad `result`, weil das umschließende
`oneOf` die Einzelursache verdeckt. Das ist vorbestehendes Verhalten für jede
Einschränkung, bleibt fail-closed, macht die Berichtsformulierung „bindet den
Fehlerpfad" aber nur für Fragmente zutreffend.

Für die Richtung writer-ungültig/lokal-gültig ist `maxLength` damit
geschlossen. Die Gegenrichtung bleibt über die registrierte Ausnahmetabelle
sauber geführt; ein neuer Eintrag war nicht nötig.

## W4. Ursachenabgrenzung und Retry

`src/agent_runtime.py` ist gegenüber `13421f6` unverändert; der Slice ergänzt
hier ausschließlich Nachweise. Die Fixture klassifiziert
`error_max_structured_output_retries` als
`provider_exhaustion_unknown_local_cause` und behauptet weder einen Längen-
noch einen Schemaverstoß. Im gesamten Diff findet sich keine Stelle, die aus
dieser Hülle eine konkrete Ursache ableitet.

Ich habe die Hüllenexaktheit über die Berichtsfälle hinaus geprüft. Transient
klassifiziert werden ausschließlich `AgentProcessError` und `AgentOutputError`
mit `agent_key == "claude"` und exakt `type == "result"` und
`subtype == "error_max_structured_output_retries"`. Fail-closed als
Prozessfehler bleiben: falscher Provider, abweichender `type`, der Beinahe-Treffer
`error_max_structured_output_retry`, die unvollständige Hülle, die
Großschreibung `ERROR_MAX_STRUCTURED_OUTPUT_RETRIES`, ein angehängtes Leerzeichen
im `subtype` und der großgeschriebene Agentschlüssel `Claude`. Sieben von
sieben Beinahe-Treffern bleiben eng.

Zur Nichtpersistenz: ein beigefügtes `structured_output`-Feld mit verworfenem
Modellinhalt wird tatsächlich vollständig entfernt. Die Zusage gilt jedoch nur
für nicht allowlistete Schlüssel — `message` steht in
`_PROVIDER_DIAGNOSTIC_KEYS`, sodass Modelltext, den ein Provider dort ablegt,
erhalten bliebe, und `provider_text` übernimmt den Ausnahmetext wörtlich.
Beides ist vorbestehend und vom Slice weder eingeführt noch gelockert; die
Berichtsformulierung ist an dieser Stelle etwas weiter als der Nachweis.

Eine slice-verursachte Lockerung der Bindung physischer Wiederholungen an
Inputdigest, Request-ID und Policybindung habe ich nicht gefunden.
`native_review_request.py`, `agent_runtime.py` und
`provider_input_budget.py` sind unverändert. Die Policy ist als Komponente
`system_policy` Bestandteil des gemessenen Providerinputs; ihre Änderung wirkt
damit auf den Inputdigest neuer Aufrufe und damit in die fail-closed Richtung.

## W5. Promptbudget und Vertragsvollständigkeit

Die Policy ist eindeutig weich formuliert („normally below 80 percent") und
enthält die ausdrückliche Gegenklausel „never omit a finding, required
disposition, review evidence, or pre-mortem merely to meet that target". Sie
wird an genau einer Stelle gesetzt, in
`NativeClaudeReviewAdapter.prepare_native_provider_input`; die beiden
Alternativpfade `build_command` und `prepare_provider_input` lösen jeweils einen
`RuntimeError` aus, und das zweite Claude-Kommando im Modul gehört zum
Fähigkeits-Smoketest, nicht zu einer Reviewoperation. Alle nativen
Claude-Reviewoperationen verwenden die Policy also. Eine Textmarker- oder
Parsergrammatik wird nicht reaktiviert; die ergänzten Wörter sind Feldnamen,
keine Marker.

Der von mir gesuchte Begünstigungsfall existiert in abgeschwächter Form: die
Klausel verbietet das **Weglassen**, nicht das inhaltliche **Ausdünnen**.
Genau das historisch am stärksten ausgelastete Feld,
`review_evidence.dimensions` mit 96,43 Prozent, ist zugleich das Feld, dessen
Substanz eine positive Freigabe trägt, und es besitzt keine untere
Inhaltsschranke außer einer Nichtleerheitsregel. Ein Modell könnte das
80-Prozent-Ziel also durch Verkürzung derselben Belegstelle erreichen, ohne die
Gegenklausel zu verletzen. Ohne Provideraufruf ist das nicht falsifizierbar und
bleibt deshalb ein benanntes Restrisiko, kein Befund.

## W6. Schema-, Request- und Canary-Grenze

Unabhängig aus dem aktuellen Code berechnet, kanonisch serialisiert mit
sortierten Schlüsseln:

| Form | Bytes | Tiefe | Kompositionen | Definitionen | SHA-256 |
|---|---:|---:|---:|---:|---|
| Plan | 10286 | 10 | 11 | 18 | `3db7549f4ed7c45eb810980038f9fd1f04a738a71396ecfd719501866aa6261e` |
| erster Slice-Review | 10349 | 10 | 11 | 18 | `188229cab90f1061eba619da4e059b3e9feac494337460e794c07fa882a46e7e` |
| Konvergenzreview | 10585 | 11 | 12 | 18 | `e41b22c001edbdae297ce9e3be08896396c92cdcdf60ac1505d54c5525017e74` |
| Finalreview | 10274 | 10 | 11 | 18 | `a65e65ff18bf594ecb5d7fb4c622eb34b4c8a91e1809058b369f44a6aa04c790` |

Alle vier Digests sind paarweise verschieden und über zwei Aufbauten stabil.
Meine Werte stimmen mit der Berichtstabelle überein; ich habe sie nicht von dort
übernommen.

Die Entscheidung, weder Writerschema noch Request-ID oder Digestbildung zu
ändern, halte ich für sachlich richtig. Die höchste belegte Auslastung liegt bei
96,43 Prozent und damit unter der Grenze; kein Corpuswert überschreitet seine
Grenze, und keine Evidenz verknüpft die Providererschöpfung mit einer
Feldlänge. Eine Anhebung wäre eine unbelegte Vertragslockerung und ist nach
§5.4 des Plans unzulässig. Die Nachweispflicht liegt damit korrekt bei einer
späteren Messung, nicht bei diesem Slice.

Der optionale Live-Canary ist dokumentiert, in der Suite über
`enabled_in_tests: false` deaktiviert und mit vier Nachweisfeldern beschrieben,
die Request, Writerschemadigest, Antwortdigest und Providerdiagnose umfassen.
Kein Test führt einen Provideraufruf aus.

## W7. Ausgeführte Gegenproben

| # | Gegenprobe | Ergebnis |
|---|---|---|
| X1 | eigener Schemadurchlauf über alle vier Writerformen | 29 begrenzte Stellen gegen 13 deklarierte |
| X2 | zusätzliches begrenztes Feld ins Writerschema injiziert | Vollständigkeits- und Grenzfalltests bleiben grün |
| X3 | eigener generischer Corpus-Walk | alle 13 Messwerte exakt reproduziert, 14 Zeilen, 6 Claude-Zeilen |
| X4 | 3001 Zeichen gegen die Fassung aus `13421f6` | akzeptiert — Lücke bestätigt |
| X5 | dieselben 3001 Zeichen gegen den Arbeitsbaum | abgelehnt, bis in den produktiven Eintrittspunkt |
| X6 | `check_schema` der Writerform, alt gegen neu | vorher `SchemaDefinitionError`, jetzt akzeptiert |
| X7 | Grenzfälle 50/80/95/100/101 Prozent an echten Fragmenten | bis 100 akzeptiert, ab 101 abgelehnt |
| X8 | Unicode bei exakt 3000 Codepoints | akzeptiert; 6000 UTF-16-Einheiten, 12000 UTF-8-Bytes |
| X9 | zwölf Retryhüllen einschließlich sieben Beinahe-Treffern | nur die exakte Hülle transient |
| X10 | verworfener Modellinhalt in `structured_output` | entfernt; `message` bleibt allowlistet |
| X11 | Writerschemametriken und Digests neu berechnet | deckungsgleich mit der Berichtstabelle |
| X12 | Aufrufpfade der Claude-Policy | genau ein Reviewpfad, Alternativpfade lösen `RuntimeError` aus |
| X13 | Archivpfadliteral in `*.py` gegen Fixture | nur im JSON, Guard bleibt grün |

NEW_FINDING: C-12 | BLOCKER | Die Vollstaendigkeit der Freitextfeldinventur ist nicht aus den Writerschemas abgeleitet, und mindestens ein begrenztes modellgeschriebenes Feld fehlt. Die Zusicherung im Drucktest vergleicht die Manifestpfade gegen das handgeschriebene Woerterbuch in _observed_claude_texts; beide Seiten sind Literale desselben Testmoduls, ein Schemadurchlauf existiert nicht, und das Schluesselwort maxLength kommt im Testmodul genau einmal vor. Gegenprobe: ein zusaetzlich in das Writerschema eingefuegtes begrenztes Feld laesst den Vollstaendigkeitstest und die Grenzfalltests bei 50, 100 und 101 Prozent vollstaendig gruen. Mein eigener Durchlauf ueber die vier request-spezifischen Writerformen findet 29 begrenzte Stellen gegenueber 13 deklarierten. Neben formspezifischen Duplikaten mit identischer Grenze fehlt inhaltlich vor allem validation_acceptance.argv.items mit maxLength 512 und maxItems 32, also das modellgeschriebene Argumentfeld des typisierten Validierungskommando-Akzeptanztests. Es traegt die engste Grenze des gesamten Vertrags, waehrend die kleinste im Manifest gefuehrte Grenze 200 und die naechste Prosagrenze 1000 betraegt, und es besitzt weder einen Manifesteintrag noch eine der geforderten Grenzfallproben. Ebenfalls nicht gebunden ist bound_approved_finding.summary, obwohl ein freigebender Review neue Findings tragen darf. Zusaetzlich liest der Grenzfalltest ausschliesslich die Definitionen der Planform, sodass formspezifische Definitionen nie an einer Grenze geprueft werden. Damit ist das Akzeptanzkriterium, wonach fuer jedes begrenzte Freitextfeld eine reproduzierbare Inventur erfolgreicher und synthetischer Fixtures vorliegt, nicht erfuellt. | Die Feldmenge wird zur Laufzeit aus den Writerschemas aller vier request-spezifischen Formen abgeleitet, jede dort begrenzte Stelle besitzt einen Manifesteintrag mit Definitionsbindung und Grenze, und jede wird bei 50, 80, 95, 100 und 101 Prozent gegen ihr echtes Fragment geprueft. Falsifikationstest: python3 -m pytest tests/test_structured_output_pressure.py -q -p no:cacheprovider muss rot werden, sobald einer Writerform ein zusaetzliches begrenztes Feld hinzugefuegt oder ein Manifesteintrag entfernt wird, und validation_acceptance.argv besitzt eine eigene Grenzfallprobe, die bei 513 Zeichen mit gebundenem SchemaMismatch ablehnt.

NEW_FINDING: C-13 | OBSERVATION | Die aktive Testsuite haengt jetzt lesend an einer Datei unterhalb von docs/internal/archive/, obwohl das Repository dafuer einen eigenen Guard fuehrt. Der Drucktest loest den Corpuspfad aus dem JSON-Fixture auf und liest die archivierte Datei bei jedem Lauf; der Guard test_runtime_and_tests_do_not_read_non_authoritative_archives durchsucht ausschliesslich Dateien mit der Endung py und sieht das Literal im Fixture deshalb nicht. Der Guard bleibt gruen, waehrend genau das eintritt, was er verhindern soll. Folge ist eine harte Abhaengigkeit der 1065 Tests umfassenden Suite von einem ausdruecklich nicht autoritativen und damit jederzeit kuerzbaren Archivbestand; ein Aufraeumen des Archivs bricht die Suite an einer Stelle, die mit dem Archivinhalt fachlich nichts zu tun hat. Funktional ist heute nichts falsch, und der Corpus wird korrekt nur als Messgroesse und nicht als Autoritaet verwendet, weshalb ich dies nicht als Blocker fuehre. | Prosa-Akzeptanztest: Entweder liegt die fuer die Messung benoetigte historische Evidenz als eigener, nicht autoritativer Schnappschuss unterhalb von tests/fixtures/ und der Drucktest liest kein Archiv mehr, oder der bestehende Guard wird ausdruecklich und begruendet auf Fixture- und Konfigurationsdateien ausgeweitet, sodass eine Archivabhaengigkeit der Suite sichtbar entschieden statt unbemerkt eingefuehrt wird.

PRE_MORTEM: In drei Monaten scheitert dieses Paket am wahrscheinlichsten daran, dass ein Writerschema ein neues begrenztes Freitextfeld erhaelt oder eine bestehende Grenze veraendert wird und die Druckinventur das nicht bemerkt, weil ihre Vollstaendigkeit gegen eine handgepflegte Liste im Testmodul statt gegen das Schema geprueft wird. Das neue Feld erhaelt weder Grenzfallevidenz noch eine Auslastungsmessung; die Suite bleibt gruen, der Bericht behauptet weiterhin eine vollstaendige Inventur, und der erste Ausfall zeigt sich als Providerablehnung an genau dem Feld, das nie vermessen wurde. Zweitwahrscheinlich ist ein Aufraeumen des nicht autoritativen Archivs, das die aktive Suite ueber die Fixture-Indirektion bricht.

SLICE_APPROVAL: 2 | NO

STATUS: DONE

## Implementer-Nacharbeit zu C-12 und C-13

### C-12 - Writerschema-abgeleitete Vollständigkeit

Die handgeschriebene Gegenliste wurde entfernt. Der Drucktest baut nun alle
vier request-spezifischen Writerformen und sammelt rekursiv jedes tatsächlich
vorhandene `maxLength` samt Form, exaktem Schemapointer und Grenze. Diese
Produktionsmenge wird bidirektional gegen das Fixturemanifest verglichen.

Der aktuelle Bestand umfasst:

- 14 logische modellgeschriebene Felder,
- 29 unterschiedliche begrenzte Schemapointer,
- 65 formgebundene Vorkommen über Plan, ersten Slice-Review,
  Konvergenzreview und Finalreview.

Neu inventarisiert sind insbesondere
`validation_acceptance.argv.items` mit 512 Zeichen und beide
`bound_approved_finding.summary`-/`bound_denied_finding.summary`-Bindungen.
Auch die im Konvergenzschema inline erzeugte
`status_changes.items.oneOf[0].rationale`-Kopie ist separat gebunden.

Jedes der 65 formgebundenen echten Fragmente wird bei 50, 80, 95, 100 und
101 Prozent geprüft. Damit besitzt auch `argv.items` seine direkte
512/513-Zeichen-Gegenprobe. Zwei Mutationskontrollen ergänzen die reale
Mengengleichheit: Ein neu begrenztes Feld erzeugt `unexpected bounded writer
sites`, das Entfernen der `argv.items`-Grenze erzeugt `missing bounded writer
sites`.

### C-13 - Archivunabhängiger historischer Snapshot

Der aktive Test liest nicht mehr aus `docs/internal/archive/`. Das Manifest
enthält stattdessen einen eigenen, ausdrücklich nichtautoritativen Snapshot
unter `tests/fixtures/`: Quellcorpusdigest, die sechs Quell-Fixture-IDs sowie
je logischem Feld die im Entwicklungszeitpunkt abgeleiteten Einzellängen und
einen Inhaltsdigest. Daraus bleiben Anzahl, Maximum und Grenzauslastung
reproduzierbar, ohne dass die Testsuite von der Verfügbarkeit oder späteren
Bereinigung des Dokumentationsarchivs abhängt.

Eine Regression verlangt zusätzlich, dass das Fixture kein aktives
Archivpfadliteral enthält. Der bestehende repositoryweite Guard bleibt grün.

### Validierung der Nacharbeit

Fokussierte Matrix einschließlich Archivguard:

```text
python3 -m pytest tests/test_structured_output_pressure.py tests/test_schema_validation.py tests/test_prompts.py tests/test_native_contract_differential.py tests/test_agent_runtime.py tests/test_language_consistency.py -q -p no:cacheprovider
101 passed in 6.64s
```

Vollständige konfigurierte Matrix:

```text
python3 -m pytest tests/ -v -p no:cacheprovider
1067 passed in 117.99s
```

`git diff --check` ist sauber. Ein Provider- oder Live-Canary wurde weiterhin
nicht ausgeführt.

FINDING_RESPONSE: C-12 | ACCEPTED | Die vollständige maxLength-Menge wird aus allen vier echten Writerschemas abgeleitet und bidirektional gegen 65 formgebundene Manifestbindungen geprüft. Approved- und Denied-Finding-Summary, validation_acceptance.argv.items und die formspezifischen Definitionen besitzen echte Grenzproben; neue und entfernte Grenzen machen die Mutationskontrollen rot.
FINDING_RESPONSE: C-13 | ACCEPTED | Die historische Druckmessung ist als eigener nichtautoritärer Längen- und Inhaltsdigest-Snapshot unter tests/fixtures gebunden. Der aktive Test liest kein Dokumentationsarchiv mehr, und eine Regression verbietet ein entsprechendes Pfadliteral im Fixture.
TEST_FILES_TOUCHED: tests/test_prompts.py, tests/test_schema_validation.py, tests/test_structured_output_pressure.py
IMPLEMENTATION_READY: 2 | YES
STATUS: DONE

REVIEWER: claude

# Claude-Korrekturreview Slice 02, zweite Runde (manuell, adversarial)

## Y1. Reviewgrenze und Methodik

Manueller adversarialer Konvergenzreview außerhalb von `run_task`, ohne
strukturierte JSON-Ausgabe, ohne Orchestrator- oder Provideraufruf. Gelesen
wurden `AGENTS.md`, `CLAUDE.md`, der freigegebene Arbeitsplan, der vollständige
bisherige Slicebericht einschließlich meiner ersten Runde und der
Implementer-Nacharbeit, der Arbeitsbaumdiff gegen `13421f6` sowie die berührten
Produktionsmodule und ihre Kontextaufbauer `src/native_review_contract.py`,
`src/native_review_request.py`, `src/workflow.py` und `src/orchestrator.py`.

Alle Zahlen unten stammen aus eigenen Erhebungen: einem selbst geschriebenen
rekursiven Schemadurchlauf über selbst gebaute Writerformen, einem eigenen
generischen Corpus-Walk gegen das Dokumentationsarchiv und einem
Laufzeit-Audithook, der jeden Dateizugriff der Drucktests protokolliert. Weder
Produktcode noch Tests wurden geändert.

Selbst ausgeführt: die fokussierte Matrix einschließlich Sprachguard
→ `101 passed in 6.59s`. Der Produktivdiff gegen `13421f6` umfasst unverändert
nur `src/prompts.py` und `src/schema_validation.py` mit acht eingefügten
Zeilen.

## Y2. C-12 — Vollständigkeit über die vier Writerformen: erfüllt

Die handgeschriebene Gegenliste ist entfernt. Ich habe die Inventur unabhängig
nachgezogen: eigener rekursiver Walker über selbst gebaute Plan-, Initial-Slice-,
Konvergenz- und Finalschemas, eigene Pointerbildung.

- 65 formgebundene `maxLength`-Vorkommen,
- 29 verschiedene Schemapointer,
- 14 logische Messfelder.

Meine Menge und die Manifestmenge sind **identisch in beide Richtungen**; weder
gibt es eine Schemastelle ohne Manifestbindung noch eine Manifestbindung ohne
Schemastelle. Kein Pointer trägt über die Formen hinweg unterschiedliche
Grenzen. Die im Auftrag ausdrücklich genannten Stellen sind vollständig
gebunden: beide Finding-Summary-Definitionen, `validation_acceptance.argv.items`,
alle acht formspezifischen Pre-Mortem-Definitionen, alle acht Stopdefinitionen
und die inline erzeugte Konvergenz-Statusbegründung.

Die Zuordnung ist sauber: kein Pointer verweist auf mehr als ein logisches Feld,
kein logisches Feld bleibt ohne Schemabindung, und keine Grenze weicht zwischen
Feld- und Inventareintrag ab.

Die Mutationskontrollen habe ich nicht über den Testfall, sondern direkt gegen
`_assert_schema_inventory` gefahren, und zwar je Writerform einzeln. Ein neu
begrenztes Feld wird in **allen vier** Formen als `unexpected bounded writer
sites` gemeldet, das Entfernen der `argv.items`-Grenze in allen vier Formen als
`missing bounded writer sites`. Zusätzlich habe ich eine dritte, im Bericht
nicht beanspruchte Mutation geprüft: eine bloß **veränderte** Grenze von 3000
auf 2999 wird ebenfalls rot. Der Guard ist also nicht nur gegen Zu- und Abgang,
sondern auch gegen Grenzverschiebung dicht.

Die Grenzfallproben laufen über alle 65 formgebundenen Fragmente bei 50, 80, 95,
100 und 101 Prozent. Für `validation_acceptance.argv.items` habe ich die exakte
Kante zusätzlich einzeln geprüft: 511 und 512 Zeichen werden akzeptiert, 513
wird mit `must contain at most 512 character(s)` abgelehnt. Die parametrisierte
Probe prüft an dieser Stelle 512 und 518; die exakte Kante bei 513 ist damit
nicht im Testfall selbst, wohl aber generisch in der
`maxLength`-Regression von `tests/test_schema_validation.py` gebunden.

Damit ist der von mir formulierte Akzeptanztest von `C-12` erfüllt. Ich halte
ausdrücklich fest, dass er zu eng formuliert war: er verankerte die
Vollständigkeit auf „alle vier request-spezifischen Formen", und genau diese
Verankerung lässt eine Lücke offen, die ich unten als `C-14` berichte.

## Y3. C-13 — Archivunabhängigkeit: erfüllt

**Keine Laufzeitabhängigkeit.** Eine Suche nach dem Archivpfad in `src/` und
`tests/` liefert über alle Dateitypen hinweg keinen Treffer mehr, weder in
Python noch in Fixture- oder Konfigurationsdaten. Entscheidender ist die
dynamische Probe: Ich habe alle elf Drucktests unter einem
`sys.addaudithook`-Zähler ausgeführt, der jeden `open`-Aufruf protokolliert.
Die einzigen geöffneten Repositoriumsdateien sind
`schemas/native-agent-review-result-v2.schema.json`,
`schemas/native-provider-schema-capabilities-v1.json` und das Fixturemanifest.
**Null Archivzugriffe.** Eine spätere Archivbereinigung kann die aktive Suite
damit nicht mehr brechen.

**Snapshot vollständig unter `tests/fixtures/` und provenienzgetrennt.** Der
Snapshot liegt als eigener Block im Manifest, ist mit
`historical_schema_only` und `field-lengths-and-content-digests` klassifiziert
und trägt keine Transport-, Request- oder Digestbindung an einen Livepfad.

**Provenienz ist überprüfbar und ehrlich.** Ich habe den hinterlegten
Quellcorpusdigest gegen die reale Archivdatei nachgerechnet: er stimmt
bytegenau. Zeilenzahl 14, Claude-Zeilen 6 und alle sechs Quell-Fixture-IDs
decken sich mit dem Archiv.

**Die eingefrorenen Längen sind treu und reproduzierend.** Ich habe alle
Einzellängen mit einem eigenen generischen Walk erneut aus dem Archiv abgeleitet
und gegen den Snapshot gestellt: **14 von 14 logischen Feldern stimmen exakt**.
Anzahl, Maximum und Grenzauslastung bleiben daraus ohne Archivzugriff
reproduzierbar; die höchste Auslastung liegt unverändert bei
`review_evidence.dimensions` mit 2893 von 3000 Zeichen, also 96,43 Prozent. Vier
Felder tragen korrekt null Beobachtungen, darunter das neu inventarisierte
`new_findings[].acceptance_test.argv[]`, weil der historische Bestand
ausschließlich Prosa-Akzeptanztests enthält.

## Y4. C-14 — die Inventur ist nicht über den erreichbaren Kontextraum geschlossen

Die Vollständigkeit ist jetzt schemagebunden, aber nur über **vier fest
verdrahtete Kontextparametrierungen**. `_writer_schemas` baut genau die vier
Kontexte aus `_context(form)`. Der Produktivcode leitet die Writerform jedoch
nicht aus einem Formnamen ab, sondern aus dem Kontext, und dieser Kontextraum
ist größer.

Maßgeblich sind zwei Produktionszeilen, die ich unabhängig gelesen habe:
`allow_new_observations=unit.kind is not WorkUnitKind.CORRECTION` in
`src/workflow.py:1787` und `src/orchestrator.py:1368`, jeweils zusammen mit
`previous_findings=history.findings`. Beobachtungen sind also für Plan-, Slice-
und Finalreview-Work-Units erlaubt und nur für Korrektur-Units verboten.
Im Schemabau entsteht die zusätzliche Definition unter der Bedingung
`observations_allowed and own_open_ids`
(`src/native_review_contract.py:488`), wobei `own_open_ids` die eigenen offenen
Claude-Findings der übergebenen Historie sind.

Genau diese Kombination fehlt in allen vier gebauten Formen: die Initial-Slice-
und Planform tragen keine Vorbefunde, die Konvergenzform trägt Vorbefunde, aber
`allow_new_observations=False`. Produktiv erreichbar ist die Kombination
dennoch, und zwar über eine in der Übergangsmatrix von Slice 1 bereits als
erreichbar belegte Kante: eine abgelehnte Slice-Prüfung bleibt in derselben
**Slice**-Work-Unit, erhöht die Runde auf zwei und trägt das offene Finding
weiter. Die Unitart ist damit nicht `CORRECTION`, `allow_new_observations`
bleibt wahr, und `own_open_ids` ist nichtleer.

Ich habe die drei produktiv geformten Kontexte gebaut und gegen das
Inventar gestellt. Nicht inventarisiert und ohne jede Grenzfallevidenz sind:

| erreichbarer Kontext | zusätzliche begrenzte Stelle | Grenze |
|---|---|---:|
| Slice-Review Runde 2 in einer Slice-Unit | `$defs/bound_approved_reclassification/properties/rationale` | 3000 |
| Plan-Review Runde 2 in einer Plan-Unit | dieselbe sowie `$defs/bound_plan_approved/properties/status_changes/items/oneOf/0/properties/rationale` | 3000 |
| Finalreview mit eigener offener Findinglinie | `$defs/bound_final_approved/properties/status_changes/items/oneOf/0/properties/rationale` | 3000 |

Alle drei sind modellgeschriebene Freitextfelder. Die inline erzeugte
Statusbegründung ist für die Konvergenzform bereits inventarisiert; ihre Plan-
und Finalentsprechungen entstehen erst bei vorhandenen eigenen offenen
Findings und fehlen deshalb. Die Reklassifikationsbegründung eines freigebenden
Reviews fehlt in jeder Form.

Der Guard kann das nicht sehen, weil die betreffenden Schemas nie gebaut
werden. Reicht man eine solche Form nachträglich ein, meldet
`_assert_schema_inventory` sie korrekt als unerwartet — der Mechanismus ist
also richtig, sein Eingaberaum ist zu klein.

Damit bleibt das Akzeptanzkriterium des Plans, wonach für **jedes** begrenzte
Freitextfeld eine reproduzierbare Inventur samt synthetischer Grenznachweise
vorliegt, unerfüllt. Das ist ein ausführbarer Defekt und in dieser
Konvergenzrunde ein `BLOCKER`.

## Y5. Regressionen der ursprünglichen Slice-2-Ziele

Alle unabhängig nachgeprüft und unverändert gültig:

- **Lokale `maxLength`-Durchsetzung**: ein `review_evidence.dimensions` mit 3001
  Zeichen wird über den produktiven Eintrittspunkt weiterhin als
  `schema-invalid` abgelehnt, ein gültiges Dokument akzeptiert.
- **Keine unbelegte Ursachenbehauptung**: die Diagnoseklassifikation bleibt
  `provider_exhaustion_unknown_local_cause`; nirgends im Diff wird aus der Hülle
  ein Längen- oder Schemaverstoß abgeleitet.
- **Exakte Retryhülle**: nur die exakte Hülle wird transient; falscher Provider,
  abweichender `type`, Beinahe-Treffer im `subtype`, unvollständige Hülle und
  Großschreibung bleiben sämtlich fail-closed.
- **Keine Persistenz verworfener Modellinhalte**: ein beigefügtes
  `structured_output`-Feld wird vollständig entfernt.
- **Weiches Zielbudget**: die 80-Prozent-Formulierung samt Gegenklausel gegen das
  Weglassen von Finding, Disposition, Reviewevidenz und Pre-Mortem ist
  unverändert vorhanden; keine Markergrammatik wird reaktiviert.
- **Keine Schemaänderung**: alle vier Writerdigests sind gegenüber meiner
  Messung der ersten Runde unverändert; Request- und Digestbildung sind nicht
  angefasst.
- **Canary**: weiterhin dokumentiert und deaktiviert.

## Y6. Ausgeführte Gegenproben

| # | Gegenprobe | Ergebnis |
|---|---|---|
| Z1 | eigener rekursiver Inventurdurchlauf über vier selbst gebaute Formen | 65 Vorkommen, 29 Pointer, 14 Felder; Mengen beidseitig identisch mit dem Manifest |
| Z2 | neues begrenztes Feld, je Form einzeln | vier von vier rot mit `unexpected bounded writer sites` |
| Z3 | `argv.items`-Grenze entfernt, je Form einzeln | vier von vier rot mit `missing bounded writer sites` |
| Z4 | Grenze 3000 auf 2999 verändert | rot |
| Z5 | `argv.items` an der exakten Kante | 512 akzeptiert, 513 abgelehnt |
| Z6 | Pointer-Feld-Zuordnung und Grenzgleichheit | kein Mehrfachbezug, kein ungebundenes Feld, keine Grenzabweichung |
| Z7 | Audithook über alle elf Drucktests | null Archivzugriffe, nur zwei Schemadateien und das Fixture |
| Z8 | Quellcorpusdigest gegen die reale Archivdatei | bytegenau gleich, 14 Zeilen, 6 Claude-Zeilen, sechs IDs deckungsgleich |
| Z9 | eingefrorene Einzellängen gegen eigene Archivmessung | 14 von 14 Feldern exakt, Spitzenauslastung 96,43 Prozent |
| Z10 | produktiv erreichbare Kontextvarianten | drei zusätzliche begrenzte Stellen ohne Inventar und ohne Grenzevidenz |
| Z11 | Regressionsproben zu Durchsetzung, Retry, Diagnose, Policy, Digests | sämtlich unverändert gültig |
| Z12 | fokussierte Matrix selbst ausgeführt | `101 passed` |

Nicht befundrelevante Restrisiken: die Zeichenlänge bleibt `len(str)` und damit
codepointbasiert, ohne Nicht-ASCII-Probe; der präzise Fehlerpfad wird in der
echten Dokumentform vom umschließenden `oneOf` verdeckt; der Schutz gegen ein
Archivpfadliteral gilt nur für dieses eine Fixture, nicht repositoryweit für
künftige Fixtures; und die 80-Prozent-Klausel verbietet weiterhin das Weglassen,
nicht das inhaltliche Ausdünnen.

FINDING_STATUS: C-12 | CLOSED | Die handgeschriebene Gegenliste ist entfernt und die Vollstaendigkeit wird zur Laufzeit aus den vier request-spezifischen Writerschemas abgeleitet. Ich habe die Inventur mit einem eigenen rekursiven Walker und eigener Pointerbildung unabhaengig nachgezogen und komme auf dieselben 65 formgebundenen Vorkommen, 29 Pointer und 14 logischen Felder; die Mengen sind in beide Richtungen identisch, kein Pointer traegt formuebergreifend abweichende Grenzen, kein Pointer verweist auf mehr als ein logisches Feld, kein logisches Feld bleibt ohne Schemabindung, und keine Grenze weicht zwischen Feld- und Inventareintrag ab. Die im Auftrag genannten Stellen sind vollstaendig gebunden: beide Finding-Summary-Definitionen, validation_acceptance.argv.items, alle acht formspezifischen Pre-Mortem- und alle acht Stopdefinitionen sowie die inline erzeugte Konvergenz-Statusbegruendung. Die Mutationskontrollen habe ich je Writerform einzeln direkt gegen die Inventurfunktion gefahren: ein neues begrenztes Feld und eine entfernte Grenze werden in allen vier Formen rot, und zusaetzlich faellt eine bloss veraenderte Grenze auf. Fuer validation_acceptance.argv.items habe ich die exakte Kante geprueft, 512 akzeptiert und 513 abgelehnt. Der von mir formulierte Akzeptanztest ist damit erfuellt; dass er die Vollstaendigkeit auf genau diese vier Formen verankerte, war zu eng und wird als C-14 separat gefuehrt.

FINDING_STATUS: C-13 | CLOSED | Der aktive Test liest kein Dokumentationsarchiv mehr. Ueber alle elf Drucktests habe ich mit einem Laufzeit-Audithook jeden Dateizugriff protokolliert; geoeffnet werden ausschliesslich zwei gebuendelte Schemadateien und das Fixturemanifest, also null Archivzugriffe. Ein Archivpfadliteral existiert weder in Python- noch in Fixture- oder Konfigurationsdateien unter src und tests. Der Snapshot liegt vollstaendig unter tests/fixtures, ist als historical_schema_only mit der Art field-lengths-and-content-digests klassifiziert und traegt keine Transport-, Request- oder Digestbindung an einen Livepfad. Seine Provenienz ist ueberpruefbar: der hinterlegte Quellcorpusdigest stimmt bytegenau mit der realen Archivdatei ueberein, Zeilenzahl vierzehn, Claude-Zeilen sechs und alle sechs Quell-Fixture-IDs decken sich. Die eingefrorenen Einzellaengen habe ich mit einem eigenen generischen Walk erneut aus dem Archiv abgeleitet; alle vierzehn logischen Felder stimmen exakt, sodass Anzahl, Maximum und Grenzauslastung einschliesslich der Spitze von 96,43 Prozent ohne Archivzugriff reproduzierbar bleiben. Eine spaetere Archivbereinigung kann die aktive Suite nicht mehr brechen.

NEW_FINDING: C-14 | BLOCKER | Die Feldinventur ist schemagebunden, aber nicht ueber den produktiv erreichbaren Kontextraum geschlossen. _writer_schemas baut genau vier fest verdrahtete Kontexte; der Produktivcode leitet die Writerform jedoch aus dem Kontext ab, und zwar mit allow_new_observations gleich unit.kind is not WorkUnitKind.CORRECTION in src/workflow.py Zeile 1787 und src/orchestrator.py Zeile 1368 zusammen mit previous_findings gleich history.findings. Die zusaetzliche Definition entsteht unter der Bedingung observations_allowed and own_open_ids in src/native_review_contract.py Zeile 488. Genau diese Kombination fehlt in allen vier gebauten Formen, ist aber produktiv erreichbar: eine abgelehnte Slice-Pruefung bleibt in derselben Slice-Work-Unit, erhoeht die Runde auf zwei und traegt das eigene offene Finding weiter, sodass die Unitart nicht CORRECTION ist und own_open_ids nichtleer bleibt. Ich habe die drei erreichbaren Kontexte gebaut und drei nicht inventarisierte begrenzte modellgeschriebene Felder mit je 3000 Zeichen gemessen: bound_approved_reclassification.rationale bei Slice-Runde zwei, zusaetzlich die inline erzeugte Statusbegruendung unter bound_plan_approved bei Plan-Runde zwei und unter bound_final_approved bei einem Finalreview mit eigener offener Findinglinie. Keines besitzt einen Manifesteintrag oder eine Grenzfallprobe. Der Guard meldet solche Formen korrekt als unerwartet, sobald man sie einreicht; sein Eingaberaum ist zu klein. Damit bleibt das Akzeptanzkriterium, wonach fuer jedes begrenzte Freitextfeld eine reproduzierbare Inventur samt synthetischer Grenznachweise vorliegt, unerfuellt. | Die Inventur deckt den erreichbaren Kontextraum ab statt vier fester Formen, mindestens um die Kombination aus erlaubten Beobachtungen und nichtleeren eigenen offenen Findings fuer Plan-, Slice- und Finalreview; jede dort begrenzte Stelle besitzt einen Manifesteintrag mit Pointer und Grenze sowie eine Grenzfallprobe bei 50, 80, 95, 100 und 101 Prozent. Falsifikationstest: python3 -m pytest tests/test_structured_output_pressure.py -q -p no:cacheprovider muss rot werden, sobald einer der drei genannten Pointer aus dem Manifest entfernt oder seine Grenze im Writerschema veraendert wird, und die Inventur muss bound_approved_reclassification.rationale mit 3000 Zeichen enthalten, dort 3000 akzeptieren und 3001 mit SchemaMismatch ablehnen.

PRE_MORTEM: In drei Monaten scheitert dieses Paket am wahrscheinlichsten daran, dass die Druckinventur weiterhin nur die vier fest verdrahteten Kontexte baut, waehrend der Produktivcode die Writerform aus einem groesseren Kontextraum ableitet. Ein Review, das eigene offene Findings traegt und zugleich Beobachtungen erlauben darf, erzeugt Definitionen, die nie vermessen wurden; die Suite bleibt gruen und der Bericht behauptet eine vollstaendige Inventur, waehrend der erste reale Ausfall als Providerablehnung an einer Reklassifikations- oder Statusbegruendung auftritt, fuer die es weder Auslastungsmessung noch Grenznachweis gibt.

SLICE_APPROVAL: 2 | NO

STATUS: DONE

## Implementer-Nacharbeit zu C-14

### Kontextzustandsraum statt vier Formetiketten

Die Inventur baut nicht mehr nur vier repräsentative Formen. Sie erzeugt
einen konservativen kartesischen Zustandsraum aus allen Parametern, die den
Writerschemabau beeinflussen:

- Approvalmarker Plan, Slice und Final,
- Runde 1 und Runde 2,
- Observation-Policy erlaubt und verboten,
- eigener offener Ledger leer, nur Blocker, nur Observation oder gemischt.

Das ergibt 48 Kontextvarianten. Der Raum ist absichtlich eine Obermenge der
heute produktiv erreichbaren Kombinationen. Damit bleibt auch eine künftig
erreichbar werdende Kombination innerhalb derselben vier Schemaachsen bereits
vermessen.

Arrayindizes in `anyOf` und `oneOf` werden als `*` normalisiert. Dadurch bindet
das Manifest die semantische Schemastelle unabhängig von der Anzahl offener
Findings; die Grenztests laufen dennoch über jedes tatsächlich erzeugte
Fragment, einschließlich beider Einträge des gemischten Ledgers.

### Vollständige kontextgebundene Inventur

Der aktuelle Zustandsraum erzeugt:

- 33 normalisierte begrenzte Schemapointer,
- 816 exakte Kontext-/Pointer-/Grenzen-Bindungen,
- 828 tatsächlich validierte Fragmente einschließlich mehrfacher
  `oneOf`-Elemente.

Manifest und Produktionsmenge sind in beide Richtungen identisch. Die
Manifestzeilen tragen dafür einen deklarativen Kontextscope wie `all`,
`plan_open`, `slice_initial_open`, `slice_convergence_open`, `final_open` oder
`observations_allowed_open`. Der Scope wird aus dem echten
`NativeReviewContext` abgeleitet, nicht aus einem Formnamen.

Neu gebunden sind insbesondere:

- `$defs/bound_approved_reclassification/properties/rationale` in jedem
  Kontext mit erlaubten Observations und eigenem offenen Ledger,
- die inline erzeugte Statusbegründung in `bound_plan_approved`,
- die inline erzeugte Statusbegründung in `bound_slice_initial_approved`,
- die bereits bekannte Entsprechung in `bound_slice_convergence_approved`,
- die inline erzeugte Statusbegründung in `bound_final_approved`.

Alle Fragmente durchlaufen weiterhin 50, 80, 95, 100 und 101 Prozent. Für den
von C-14 hervorgehobenen Reklassifikationspfad existiert zusätzlich eine
exakte Kantenregression: 3000 Zeichen werden angenommen, 3001 Zeichen mit
`SchemaMismatch` abgelehnt.

Die Mutationskontrollen greifen jetzt innerhalb eines konkreten offenen
Plan-Kontexts. Ein neues, entferntes oder in seiner Grenze verändertes
`maxLength` kann deshalb nicht dadurch verborgen bleiben, dass derselbe
Pointer in einem anderen Kontext weiterhin existiert.

### Validierung der zweiten Nacharbeit

Fokussierte Korrekturmatrix:

```text
python3 -m pytest tests/test_structured_output_pressure.py tests/test_schema_validation.py tests/test_prompts.py tests/test_native_contract_differential.py tests/test_agent_runtime.py tests/test_language_consistency.py -q -p no:cacheprovider
102 passed in 8.96s
```

Vollständige konfigurierte Matrix:

```text
python3 -m pytest tests/ -v -p no:cacheprovider
1068 passed in 121.71s
```

`git diff --check` ist sauber. Weder Orchestrator noch Provider oder Live-Canary
wurden aufgerufen.

FINDING_RESPONSE: C-14 | ACCEPTED | Die Inventur deckt einen konservativen 48-teiligen Kontextzustandsraum aus Marker, Runde, Observation-Policy und eigenem offenen Ledger ab. Manifest und Writerschemas stimmen für 816 Kontextbindungen und 33 normalisierte Pointer exakt überein; Approved-Reclassification sowie Plan-, Slice- und Final-Statusbegründungen besitzen echte Grenzproben. Der Reklassifikationspfad akzeptiert 3000 und verwirft 3001 Zeichen.
TEST_FILES_TOUCHED: tests/test_prompts.py, tests/test_schema_validation.py, tests/test_structured_output_pressure.py
IMPLEMENTATION_READY: 2 | YES
STATUS: DONE

REVIEWER: claude

# Claude-Korrekturreview Slice 02, dritte Runde (manuell, adversarial)

## AA1. Reviewgrenze und Methodik

Manueller adversarialer Konvergenzreview außerhalb von `run_task`, ohne
strukturierte JSON-Ausgabe, ohne Orchestrator- oder Provideraufruf. Gelesen
wurden `AGENTS.md`, `CLAUDE.md`, der freigegebene Arbeitsplan, der vollständige
bisherige Slicebericht einschließlich meiner beiden Vorrunden und der jüngsten
Nacharbeit, der Arbeitsbaumdiff gegen `13421f6` sowie
`src/native_review_contract.py`, `src/native_review_request.py`,
`src/workflow.py`, `src/orchestrator.py` und `src/contracts.py`.

Keine Zahl unten stammt aus Bericht oder Fixture. Ich habe die
schemaformenden Kontextmerkmale per AST-Analyse selbst bestimmt, den
Zustandsraum mit einem eigenen rekursiven Walker und eigener Normalisierung
inventarisiert, die Grenzproben über jedes real erzeugte Fragment selbst
gefahren und die Archivunabhängigkeit mit einem Laufzeit-Audithook gemessen.
Weder Produktcode noch Tests wurden geändert; der Produktivdiff gegen
`13421f6` umfasst unverändert nur `src/prompts.py` und
`src/schema_validation.py` mit acht eingefügten Zeilen.

Selbst ausgeführt: fokussierte Korrekturmatrix → `102 passed in 8.68s`.

## AA2. Schemaformende Kontextmerkmale, selbst bestimmt

Ich habe alle `context.<attribut>`-Zugriffe in `native_review_contract.py`
per AST gesammelt und die im Aufbaubereich gelesenen Attribute isoliert:
`reviewer`, `anchor_origin`, `previous_findings`, `round_number`,
`allow_new_observations`, `approval_marker`, `test_changes_approved`,
`test_files` und `validation_attestation`. Die Testdokumentation nennt nur
vier Achsen, also habe ich die übrigen Attribute nicht geglaubt, sondern
orthogonal variiert: über **alle 48 Kontextvarianten** habe ich
`anchor_origin=None`, `test_changes_approved=False`, ein gesetztes
`test_files`, leere `validation_command_prefixes`, fehlende
`validation_attestation` und eine geänderte `slice_id` durchgespielt.
Ergebnis: **null abweichende Kontexte** in jeder dieser sechs Variationen.
Keines dieser Attribute beeinflusst das Auftreten begrenzter Felder.

`reviewer` ist keine freie Achse: `src/contracts.py` erzwingt
„finding reporter must be claude", und Reviews werden ausschließlich von
Claude erstellt.

Auf der Rundenachse ist die relevante Produktionsbedingung
`context.round_number == 1 and context.allow_new_observations`
(`src/native_review_contract.py:381`), also ein binäres Prädikat auf Runde
eins. Ich habe das falsifiziert statt es zu unterstellen: für jede Runde-2-
Variante liefern die Runden 3, 5 und 11 **identische** Stellenmengen, und jede
Runde-3- und Runde-7-Menge ist Teilmenge der Gesamtunion. Runde 2 ist damit
repräsentativ für alle höheren Runden.

Zur Ledgerachse habe ich über die vier Fixtureformen hinaus geprüft: nur ein
geschlossenes eigenes Finding, geschlossen plus offen, drei offene und fünf
offene eigene Findings. Keine dieser Formen erzeugt eine Stelle außerhalb der
inventarisierten Union.

Die vier Achsen Approvalmarker, Runde, Observation-Policy und eigener offener
Ledger sind damit nach eigener Prüfung tatsächlich die schemaformenden, und der
kartesische Raum ist eine sichere Obermenge.

## AA3. Inventur und bidirektionale Bindung

Mit eigenem Walker über die selbst gebauten Kontexte erhalte ich:

- **48** Kontextvarianten,
- **33** normalisierte begrenzte Schemapointer,
- **816** exakte Kontext-/Pointer-/Grenzen-Bindungen,
- **828** tatsächlich erzeugte Fragmente einschließlich mehrfacher
  `oneOf`-Elemente.

Meine Menge und die aus dem Manifest über den deklarativen Kontextscope
expandierte Menge sind **in beide Richtungen identisch**: keine Schemastelle
ohne Manifestbindung, keine Manifestbindung ohne Schemastelle. Der Scope wird
aus dem echten `NativeReviewContext` abgeleitet, nicht aus einem Formnamen; ich
habe das an den Scopewerten der realen Kanten nachvollzogen.

## AA4. Normalisierung

Die `*`-Normalisierung ist sauber. Ich habe je Kontext die rohen, nicht
normalisierten Pfade erhoben und gegen ihre normalisierte Form gruppiert:

- **keine** normalisierte Stelle vereinigt unterschiedliche Grenzen,
- genau **12** normalisierte Stellen vereinigen mehrere Rohpfade, und zwar
  ausschließlich `oneOf/0` und `oneOf/1` derselben `status_changes`-Items
  unter einem gemischten Ledger, beide mit Grenze 3000.

Es werden also nur echte dynamische Arrayindizes zusammengefasst, keine
unterschiedlichen Objektpfade. Die Validierung bleibt davon unberührt:
`_bounded_fragments` sammelt pro Schlüssel eine Liste, sodass aus 816
Schlüsseln 828 real geprüfte Fragmente werden. Der gemischte Ledger validiert
damit weiterhin **jedes** reale `oneOf`-Fragment einzeln.

## AA5. Reale Kanten und geforderte Definitionen

Ich habe die fünf im Auftrag genannten Produktionskanten selbst als Kontext
gebaut und gegen den inventarisierten Raum gestellt:

| reale Kante | begrenzte Stellen | nicht inventarisiert |
|---|---:|---|
| Plan-Follow-up, Runde 2, eigenes offenes Finding | 18 | keine |
| erster Slice mit übernommenem Finding | 18 | keine |
| wiederholter Slice-Review in derselben Slice-Unit | 18 | keine |
| Correction-Slice, Observations verboten | 17 | keine |
| Finalreview mit offenem eigenen Finding | 17 | keine |

Alle fünf sind Teilmenge der Gesamtunion. Die im Auftrag hervorgehobenen
Definitionen sind mit echten Kontexten und Grenze 3000 gebunden:
`bound_approved_reclassification.rationale` in 12 Kontexten, die
Statusbegründungen unter `bound_plan_approved` in 12, unter
`bound_slice_initial_approved` in 3, unter `bound_slice_convergence_approved`
in 9 und unter `bound_final_approved` in 12 Kontexten.

## AA6. Falsifikationen

**Grenzproben.** Ich habe alle 828 real erzeugten Fragmente bei 50, 80, 95, 100
und 101 Prozent selbst validiert, insgesamt **4140 Fragment-Prozent-Paare**.
Kein einziges Fehlverhalten: bis einschließlich 100 Prozent akzeptiert, ab 101
Prozent abgelehnt. Jedes Fragment besteht zusätzlich `check_schema`.

**Reklassifikationspfad exakt.** 2999 und 3000 Zeichen werden akzeptiert, 3001
wird mit `must contain at most 3000 character(s)` abgelehnt.

**Mutation in genau einem Kontext.** Am Kontext
`plan-round-2-observations-1-blocker` habe ich einzeln mutiert, während
derselbe Pointer in allen anderen Kontexten unverändert blieb:

- neue Grenze an `anchor.anchor_id` → `unexpected bounded writer sites`,
- entfernte Grenze an `bound_approved_reclassification.rationale` →
  `missing bounded writer sites`,
- Grenze 3000 auf 2999 an `evidence.dimensions` →
  `unexpected bounded writer sites`.

Alle drei werden erkannt. Die Kontextbindung verhindert also, dass eine
Änderung sich hinter einem unveränderten Vorkommen in einem anderen Kontext
versteckt.

## AA7. Regressionen

- **C-12 bleibt geschlossen.** Die Inventur ist weiterhin schemaabgeleitet, jetzt
  über den Kontextraum statt über vier Etiketten, und beidseitig geschlossen.
- **C-13 bleibt geschlossen.** Der Audithook über alle Drucktests zeigt
  unverändert **null Archivzugriffe**; geöffnet werden nur zwei gebündelte
  Schemadateien und das Fixture. Der hinterlegte Quellcorpusdigest stimmt
  weiterhin bytegenau mit der realen Archivdatei, und alle **14 von 14**
  eingefrorenen Längenlisten habe ich erneut aus dem Archiv nachgerechnet; die
  Spitzenauslastung bleibt 96,43 Prozent.
- **Lokale `maxLength`-Durchsetzung**: 3001 Zeichen werden über den produktiven
  Eintrittspunkt weiterhin abgelehnt.
- **Retryhülle**: nur die exakte Hülle transient; falscher Provider, `type`,
  `subtype`-Beinahe-Treffer und unvollständige Hülle bleiben fail-closed.
- **Keine unbelegte Ursachenbehauptung**: Klassifikation unverändert
  `provider_exhaustion_unknown_local_cause`.
- **Keine Persistenz verworfener Modellinhalte**: beigefügtes
  `structured_output` wird vollständig entfernt.
- **Weiches Zielbudget**: 80-Prozent-Formulierung samt Gegenklausel unverändert,
  keine Markergrammatik.
- **Keine Schemaänderung**: alle vier Writerdigests sind gegenüber meiner
  Messung der ersten Runde unverändert.
- **Canary**: dokumentiert und deaktiviert.

## AA8. Ausgeführte Gegenproben

| # | Gegenprobe | Ergebnis |
|---|---|---|
| AB1 | AST-Erhebung der gelesenen Kontextattribute | neun Attribute im Aufbaubereich identifiziert |
| AB2 | orthogonale Variation von sechs weiteren Attributen über alle 48 Kontexte | null abweichende Kontexte |
| AB3 | Runden 3, 5, 11 gegen Runde 2 | identische Mengen, alle Teilmenge der Union |
| AB4 | vier weitere Ledgerformen einschließlich fünf offener Findings | keine Stelle außerhalb der Union |
| AB5 | eigener Inventurdurchlauf | 33 Pointer, 816 Bindungen, 828 Fragmente, beidseitig identisch |
| AB6 | Normalisierung auf kollabierte Grenzen und Objektpfade geprüft | keine Grenzkollision, 12 echte Arrayindexpaare |
| AB7 | Grenzproben über jedes reale Fragment | 4140 Paare, kein Fehlverhalten |
| AB8 | Reklassifikationspfad 2999/3000/3001 | akzeptiert, akzeptiert, abgelehnt |
| AB9 | Einzelkontextmutation neu, entfernt, verändert | dreimal rot |
| AB10 | fünf reale Produktionskanten | keine nicht inventarisierte Stelle |
| AB11 | Audithook über alle Drucktests | null Archivzugriffe |
| AB12 | Corpusdigest und 14 eingefrorene Längenlisten gegen das Archiv | bytegenau beziehungsweise exakt |
| AB13 | Regressionsproben Durchsetzung, Retry, Diagnose, Policy, Digests | sämtlich unverändert |
| AB14 | fokussierte Korrekturmatrix selbst ausgeführt | `102 passed` |

FINDING_STATUS: C-14 | CLOSED | Die Inventur ist nicht mehr an vier Formetiketten gebunden, sondern an einen Kontextzustandsraum, dessen schemaformende Achsen ich unabhaengig bestimmt und falsifiziert habe. Per AST habe ich die im Aufbaubereich gelesenen Kontextattribute erhoben und die nicht abgedeckten orthogonal ueber alle achtundvierzig Varianten variiert: anchor_origin, test_changes_approved, test_files, validation_command_prefixes, validation_attestation und slice_id erzeugen null abweichende Kontexte, und der Reporter ist vertraglich stets Claude. Auf der Rundenachse liefern die Runden drei, fuenf und elf identische Mengen wie Runde zwei, und vier weitere Ledgerformen bis zu fuenf offenen eigenen Findings erzeugen keine Stelle ausserhalb der Union; der Raum ist damit eine belegte sichere Obermenge. Mein eigener rekursiver Walker ergibt dreiunddreissig normalisierte Pointer, achthundertsechzehn Kontextbindungen und achthundertachtundzwanzig reale Fragmente, beidseitig identisch mit dem Manifest. Die Normalisierung fasst nachweislich nur dynamische Arrayindizes zusammen: keine normalisierte Stelle vereinigt unterschiedliche Grenzen, und die zwoelf Stellen mit mehreren Rohpfaden sind ausschliesslich die beiden oneOf-Eintraege derselben Statusliste unter gemischtem Ledger, die beide einzeln validiert werden. Alle vier tausend einhundertvierzig Fragment-Prozent-Paare verhalten sich korrekt, der Reklassifikationspfad akzeptiert dreitausend und lehnt dreitausendeins ab, und eine neue, entfernte oder von dreitausend auf zweitausendneunhundertneunundneunzig veraenderte Grenze wird auch dann erkannt, wenn sie nur in einem einzigen Kontext auftritt. Die fuenf realen Kanten Plan-Follow-up, erster Slice mit uebernommenem Finding, wiederholter Slice-Review, Correction-Slice und Finalreview mit offenem eigenen Finding enthalten keine nicht inventarisierte Stelle.

REVIEW_EVIDENCE: geprueft wurden die schemaformenden Kontextmerkmale per AST und orthogonaler Variation, die Obermengeneigenschaft auf Runden- und Ledgerachse, die bidirektionale Gleichheit von Kontext, normalisiertem Pointer und Grenze, die Soliditaet der Sternnormalisierung gegen kollabierte Grenzen und Objektpfade, Grenzproben ueber jedes real erzeugte Fragment einschliesslich mehrfacher oneOf-Eintraege, die exakte Kante des Reklassifikationspfads, Einzelkontextmutationen fuer neue, entfernte und veraenderte Grenzen, die fuenf realen Produktionskanten sowie die Regressionen von C-12 und C-13 und aller urspruenglichen Sliceziele | groesstes Restrisiko ist die Herleitung der vier Achsen selbst: dass kein weiteres Kontextattribut das Auftreten begrenzter Felder beeinflusst, habe ich heute empirisch belegt, es ist aber nirgends als Zusicherung verankert, sodass eine kuenftige Schemaverzweigung auf einem fuenften Attribut den Zustandsraum still unvollstaendig machen wuerde; nachgeordnet bleiben die codepointbasierte Laengensemantik ohne Nicht-ASCII-Probe, die Verdeckung des genauen Fehlerpfads durch das umschliessende oneOf und die Kuerzeklausel, die das Weglassen verbietet, das inhaltliche Ausduennen aber nicht | realistische Bruchbedingung ist eine neue bedingte Schemastelle, die auf einem bisher wirkungslosen Kontextattribut wie test_files oder anchor_origin verzweigt: der kartesische Raum wuerde sie nicht erzeugen, die Mengengleichheit bliebe gruen und das neue begrenzte Feld haette weder Auslastungsmessung noch Grenznachweis

PRE_MORTEM: In drei Monaten scheitert dieses Paket am wahrscheinlichsten daran, dass eine neue Schemaverzweigung auf einem Kontextattribut ausserhalb der vier heute vermessenen Achsen eingefuehrt wird. Der kartesische Zustandsraum erzeugt diese Kombination nicht, die bidirektionale Mengengleichheit bleibt deshalb gruen, und das neue begrenzte Freitextfeld erhaelt weder eine Auslastungsmessung noch eine Grenzprobe. Der erste reale Ausfall zeigt sich dann als Providerablehnung an genau diesem Feld, waehrend der Bericht weiterhin eine vollstaendige Inventur behauptet.

SLICE_APPROVAL: 2 | YES

STATUS: DONE
