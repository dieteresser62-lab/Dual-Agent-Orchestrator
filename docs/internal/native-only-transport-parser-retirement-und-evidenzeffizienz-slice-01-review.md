# Native-only Transport, Parser-Retirement und Evidenzeffizienz – Slice 01

## Implementierungsübergabe

**Slice:** 01 – Native Pflichtbindung und fail-closed Protokollcutover

**Planbasis:** `bdb955d4ca56ca292dd8bcbd4d1e776a2acb7a1b`

**Status:** zur manuellen Prüfung durch Claude bereit

### Umgesetzter Vertragsstand

- Die CLI-Optionen zum Ein- und Ausschalten der nativen Codex- und
  Claude-Transporte sind entfernt. Neue Einzel- und Watchläufe binden beide
  nativen Transporte ohne Benutzeroption.
- `structured-v2` akzeptiert nur die vollständige native Codex-/Claude-
  Transportbindung. Fehlende, partielle, textuelle oder fremde Bindungen
  stoppen beim Resume mit `UNSUPPORTED-PROTOCOL`, bevor ein Artifact-Store
  geöffnet oder ein externer Schritt gestartet wird.
- Die Agentregistry konstruiert `NativeCodexAdapter` und
  `NativeClaudeReviewAdapter` unmittelbar. Der Produktionsdriver akzeptiert
  ausschließlich diese nativen Adapter.
- `AgentResultPayload` und `ReviewPayload` verlangen in Datenmodell,
  Deserialisierung, Bridge und JSON-Schema Transportversion, typisierte
  Request-ID und SHA-256 der Rohantwort. Ungebundene v2-Records werden
  fail-closed abgewiesen.
- Historische Textresultate werden in aktiven `structured-v2`-Replaytests
  nicht mehr als native Resultate rekonstruiert. Die historischen Bytes
  bleiben unverändert.
- Eine providerfreie AST-Inventur prüft alle direkten Konstruktionen von
  `AgentResultPayload` und `ReviewPayload` unter `src/` und `tests/` und nennt
  bei fehlender Transportbindung Pfad und Zeile.
- README und die Rootverträge beschreiben den verpflichtenden nativen
  Transport konsistent.

### Abgrenzung zu Slice 02

Die alten Textparser und ihre Contract-Repair-Infrastruktur werden erst in
Slice 02 gelöscht. Die im Produktionsdriver verbliebenen textuellen
Persistenzmethoden sind deshalb in Slice 01 bewusst nur noch unerreichbare
Kompatibilitätssenken: Die Registry und die Produktionsaufrufe können ihnen
kein Textresultat mehr zuführen. Diese Zwischenstufe hält Slice 01 atomar
testbar, ohne die für Slice 02 geplante Löschung vorwegzunehmen.

### Kanonische Vorher-Baseline

Die noch aktive Eingabeaufbaulogik wurde providerfrei für sechs native Codex-
Operationen eingefroren:

- `codex_plan`
- `codex_plan_revision`
- `codex_implementation`
- `codex_correction`
- `codex_final_review`
- `codex_final_correction`

Artefakte:

- Baseline: `tests/fixtures/provider_input_efficiency/native-only-cutover-baseline-v1.json`
- Lock: `tests/fixtures/provider_input_efficiency/native-only-cutover-baseline-v1.lock.json`
- Basiscommit: `bdb955d4ca56ca292dd8bcbd4d1e776a2acb7a1b`
- Baseline-SHA-256: `3f07b52b79badf1340998973c4145b20a93892ea0f174942cd21ab1331c7adfc`
- Lock-SHA-256: `11bb60a6aed1ab78b7911d9a2eb9ea6a216b845a313c324df0558085ad9fabb9`
- synthetischer Eingabedigest:
  `934d738b2554b26c906223ca79e795eb3417c25c48fc3df0cdb24f1b4239dce2`
- als später entfernt gebundene Komponenten: `workflow-prompt` und
  `approved-plan`

Der providerfreie Regressionstest rekonstruiert die Baseline in Slice 01
bytegleich aus der aktiven Aufbaulogik und prüft Rollen, Inhalte, Digests,
Zeichen- und UTF-8-Bytezahlen sowie die Lockbindung. Ab Slice 02 sind beide
Fixturedateien read-only.

### Validierung

- fokussierte Slice-01-Matrix: `633 passed in 73.30s`
- vollständige Repositorymatrix nach C-07: `1186 passed in 83.54s`
- `git diff --check`: bestanden; ausschließlich bekannte
  LF/CRLF-Arbeitsbaumhinweise
- externe Provideraufrufe: keine

## Claude-Review

Noch ausstehend. Claude ergänzt seinen manuellen Reviewabschnitt am Ende
dieser Datei, ohne Produktcode oder Tests zu ändern.

---

## Claude-Review zu Slice 01

REVIEWER: claude

Manueller Review außerhalb von `run_task`, ohne Provideraufruf, ohne
strukturierte JSON-Ausgabe und ohne vollständige Testsuite. Produktcode und
Tests blieben unverändert; angehängt wurde ausschließlich dieser Abschnitt.
Reviewgrenze: Plancommit `bdb955d4ca56ca292dd8bcbd4d1e776a2acb7a1b` gegen den
uncommitteten Arbeitsbaum. Die vier ungetrackten Neuzugänge habe ich als
Dateien gelesen, weil `git diff` sie nicht zeigt. Die vorgelegte Evidenz
(633 und 1183 passed) habe ich nicht reproduziert; alle unten genannten
Gegenproben sind providerfrei und haben den Arbeitsbaum nicht verändert.

### Was trägt

**Verpflichtender nativer Transport.** Alle vier Schalter sind aus dem Parser
entfernt (`src/cli.py`), aus der Weitergabe (`_fresh_state` hat die beiden
Parameter verloren, der Resume-Vergleichsblock in `run_production_workflow`
ist ersatzlos gestrichen) und aus der Dokumentation (README-Optionstabelle,
zwei Prosaabsätze). Ich habe repositoryweit nach Restreferenzen gesucht: Die
einzigen verbliebenen Vorkommen stehen in `tests/test_cli.py:131-145` und
`tests/test_native_transport_retirement.py` — beide als Negativkontrollen, die
`SystemExit(2)` erwarten. `build_agent_registry()` konstruiert jetzt direkt
`NativeCodexAdapter`/`NativeClaudeReviewAdapter`, und der Produktionsdriver
prüft mit `isinstance` statt über eine Fabrikmethode.

**Fail-closed Resume.** `ProtocolBinding.__post_init__` erzwingt für
`structured-v2` das vollständige native Paar. Ich habe die Deserialisierung
einzeln durchgespielt: vollständig nativ akzeptiert; ohne Transportschlüssel,
mit nur einem der beiden, mit den v1-Strings und mit einem unbekannten
Transport jeweils abgewiesen. Entscheidend gegen eine stille Umdeutung ist,
dass `ProtocolBinding.from_dict()` bei fehlenden Schlüsseln **explizit `None`**
übergibt und damit den neuen Native-Default nicht greifen lässt — genau der
Weg, den ich als Hauptrisiko der geänderten Defaults geprüft habe. Legacy- und
v1-Modi bleiben über `from_dict` konstruierbar und werden anschließend in
`resolve_resume_state()` mit `UNSUPPORTED-PROTOCOL` abgewiesen; die neue
Bindungsprüfung dort ergänzt denselben Code für unvollständige v2-Bindungen.

Die Reihenfolge stimmt: `load_resumable_workflow_state()` (`src/state_io.py:335-346`)
ruft `resolve_resume_state()` bedingungslos für jeden `WorkflowState`, und
`run_production_workflow` nutzt genau diesen Loader für den Resumepfad — also
vor Driverkonstruktion, vor `driver.checkpoint(...)`, vor Artifact-Store,
Snapshot, Validierung, Providerattempt und Commit. Historische Bytes werden
nicht umgeschrieben.

**Record- und Schemavertrag.** Die Trias ist in beiden Schichten obligatorisch
und semantisch gekoppelt. Ich habe 14 Verletzungen einzeln gefahren: am Modell
scheitern fehlende Trias (`TypeError`, weil die Defaults entfielen),
`transport_schema=None`, die v1-Transportversion, eine Request-ID aus dem
fremden Namensraum, ein nicht-String als Request-ID, ein ungültiger Digest und
die falsche Rolle beziehungsweise der falsche Reviewer. Am serialisierten
Dokument weisen `validate_artifact_document()` **und**
`ArtifactRecord.from_dict()` alle acht geprüften Injektionen ab — Nullwert,
v1-Version, entfernte Felder, ungültiger Digest, falscher Typ und falscher
Request-ID-Präfix; die gültigen Kontrollrecords roundtrippen unverändert.

**AST-Inventur.** Sie ist echt AST-basiert und hat Zähne. Ich habe ihre Logik
gegen synthetische Quellen laufen lassen: ungebundene Konstruktion, teilweise
gebundene (zwei von drei Feldern), `**kwargs`-Splat und Attributaufruf
(`models.ReviewPayload(...)`) werden gemeldet; korrekt gebundene Keyword- und
vollständig positionale Formen passieren. Die Positionsindizes stimmen exakt
mit der Feldreihenfolge beider Dataclasses überein, was ich per
`dataclasses.fields` verifiziert habe. Zwei Blindstellen bleiben, beide
harmlos: `ReviewPayload(..., None, None, None)` ist syntaktisch vollständig und
wird erst zur Laufzeit vom Modell abgewiesen, und eine `getattr`-Indirektion
wird nicht erkannt; letztere existiert im Repository nicht.

**Textuelle Persistenzsenken.** `persist_codex_contract` und
`persist_review_contract` sind auf `_ = (...)` reduziert. Ich habe ihre
Erreichbarkeit geprüft statt sie anzunehmen: Die Aufrufer in `src/workflow.py`
liegen hinter `native_codex` beziehungsweise `native_claude_review`
(`:1614-1618`, `:2033-2037`, `:2155-2160`), die beide aus
`protocol_binding.*_transport` abgeleitet werden. Für jeden Zustand, der
überhaupt existieren kann, sind beide Werte nativ — andernfalls scheitert
schon die Deserialisierung oder `resolve_resume_state`. Ein Zustand ganz ohne
Bindung fällt über `effective_protocol_mode` auf `LEGACY_STATE_V3` und wird als
historisch abgewiesen. Ein ausführbarer Bypass ist mir nicht gelungen.

**Adapter-, Dry-run- und Dokumentationsgrenzen.** Profilbindung, Quota-,
Retry-, Record-ahead-, Commit-, Replay- und Projektionssemantik sind
unverändert; die Dry-run-Ergänzungen (`authoritative_native_findings`,
Task-Digest und Scope im Szenariozustand) sind die minimale Folge der
Pflichtbindung und halten keinen Texttransport offen. AGENTS, CLAUDE, CODEX und
README sind zeichengleich synchronisiert. Alle 27 geänderten Pfade liegen im
exakten Slice-01-Pfad; elf dort erlaubte Pfade blieben unberührt, was zulässig
ist. Vorgezogene Slice-02-Löschungen finden sich nicht.

**Baselinedigests.** Ich habe alle drei angegebenen Werte unabhängig
nachgerechnet und bestätige sie: Baseline
`75198efcc1cdda529819dcee3469c36bf8bd088c6aa69eca8f8d1e9b5aa891c0`, Lock
`9935677ac0b0efe4465840e9b798ff02b8165d51e81f21402bada82bb67f813a`,
synthetischer Eingabedigest
`934d738b2554b26c906223ca79e795eb3417c25c48fc3df0cdb24f1b4239dce2`. Der
Implementierungsbericht nennt dieselben Werte. Die Rekonstruktion läuft
tatsächlich über die lebende Aufbaulogik — `build_native_codex_request()` und
`measure_provider_input()` —, nicht gegen dieselben erwarteten Zahlen, und
alle sechs Operationen sind mit Komponentenliste, Inhaltsdigest, Zeichen- und
Bytezahl gebunden. Der Lock nennt `approved-plan` und `workflow-prompt` als
später zu entfernende Evidenz.

### Wo Slice 01 bricht

Die eingefrorene Baseline kann die in Runde 4 freigegebene Slice-3-Gleichung
nicht tragen, und das ist genau jetzt zu beheben, weil `C-05` eine spätere
Neuerzeugung der Fixture ausdrücklich verbietet.

Die Requests transportieren Evidenz **nicht inline**, sondern als
`evidence_asset_NNN`-Komponenten plus einen `evidence_manifest`-Block im
kanonischen Requestdokument (`stdin_prompt`). Entfernt Slice 2 die
`workflow-prompt`-Evidenz, verschwindet damit nicht nur ein Asset, sondern
auch dessen Manifesteintrag — und `stdin_prompt` ändert Inhalt und Digest.

Ich habe das für `codex_implementation` providerfrei nachgestellt, indem ich
die Fixturegenerierung exakt nachgebaut und nur die
`workflow-prompt`-Evidenz weggelassen habe:

| Größe | Wert |
|---|---|
| eingefrorene Gesamtzeichen | 55 321 |
| Gesamtzeichen ohne `workflow-prompt` | 30 880 |
| tatsächliche Differenz | **24 441** |
| eingefrorene Zeichen der entfernten Komponente | **24 087** |
| `stdin_prompt` vorher / nachher | 1 993 / 1 639 |
| Rest (Manifesteintrag) | 354 |

Damit scheitern beide Hälften des Slice-3-Kriteriums an dieser Fixture: Die
Differenz sinkt **nicht** „exakt um die Summe der in der Fixture einzeln
digest-, zeichen- und bytegebundenen entfernten Komponenten" (24 441 statt
24 087), und die Forderung „unveränderte Komponenten müssen namens- und
digestgleich bleiben" ist verletzt, weil `stdin_prompt` seinen Digest ändert.
Die fehlenden 354 Zeichen sind aus der Fixture nicht ableitbar: Sie bindet
`stdin_prompt` nur als Ganzes, nicht den Beitrag der einzelnen
Manifesteinträge.

Slice 3 könnte die Gleichung deshalb nur schließen, indem es den neuen
`stdin_prompt` misst und die Differenz aus sich selbst erklärt — womit der
Beweis zur Buchführung würde — oder indem es die Baseline neu erzeugt, was
`C-05` gerade verhindern soll. Die Reparatur gehört in Slice 01, solange die
Fixture noch schreibbar ist.

Nebenbefund ohne eigene Findingqualität: Der Lock bindet mit
`["approved-plan","workflow-prompt"]` **Evidenz-IDs**, die Baseline dagegen
**Komponentennamen** (`evidence_asset_001`). Die Slice-2-Absenzprüfung muss
diese beiden Namensräume verbinden; das ist implementierbar, sollte aber bei
der Korrektur mitgeklärt werden.

NEW_FINDING: C-07 | BLOCKER | Die in Slice 01 eingefrorene Effizienzbaseline `tests/fixtures/provider_input_efficiency/native-only-cutover-baseline-v1.json` kann die freigegebene Slice-3-Differenzgleichung arithmetisch nicht tragen, und weil `C-05` jede spätere Neuerzeugung verbietet, ist der Defekt nach der Slice-01-Freigabe nicht mehr behebbar. Native Codex-Requests transportieren Evidenz nicht inline, sondern als `evidence_asset_NNN`-Komponenten plus einen `evidence_manifest`-Eintrag im kanonischen Requestdokument, das die Fixture als Komponente `stdin_prompt` bindet. Ich habe die Slice-2-Entfernung für `codex_implementation` providerfrei nachgestellt, indem ich `_prepared()` exakt nachgebaut und nur die `workflow-prompt`-Evidenz weggelassen habe: Die Gesamtzeichen sinken von eingefrorenen 55321 auf 30880, also um 24441, während die Fixture für die entfernte Komponente nur 24087 Zeichen bindet; die Restdifferenz von 354 Zeichen ist der Manifesteintrag, und `stdin_prompt` sinkt dabei von 1993 auf 1639 Zeichen und ändert seinen SHA-256. Damit sind beide Hälften des Slice-3-Kriteriums verletzt — die Differenz entspricht nicht „exakt der Summe der entfernten Komponenten", und die Forderung „unveränderte Komponenten müssen namens- und digestgleich bleiben" trifft auf `stdin_prompt` nicht zu. Aus der Fixture ist der Manifestanteil nicht rekonstruierbar, weil sie `stdin_prompt` nur als Ganzes mit Zeichen-, Byte- und Digestwert führt. Ergänzend bindet der Lock mit `["approved-plan","workflow-prompt"]` Evidenz-IDs, während die Baseline Komponentennamen wie `evidence_asset_001` führt; die Slice-2-Absenzprüfung muss beide Namensräume verbinden. | Die Baseline wird in Slice 01 so erweitert, dass sie je Operation zusätzlich für jede Evidenz-ID den kanonischen Manifestbeitrag innerhalb von `stdin_prompt` in Zeichen und UTF-8-Bytes bindet und die Zuordnung von Evidenz-ID zu Komponentennamen ausweist; Lock und Slice-01-Reviewbericht führen die neuen Digests. Ein providerfreier Test in `tests/test_provider_input_efficiency.py` weist für mindestens eine Implementierungs- und eine Korrekturoperation nach, dass `baseline_total_chars - current_total_chars` exakt der Summe aus den gebundenen Zeichen der entfernten Assets **und** ihrer gebundenen Manifestbeiträge entspricht, indem er die Entfernung der `workflow-prompt`-Evidenz gegen die lebende Aufbaulogik simuliert; dieselbe Gleichung gilt für UTF-8-Bytes, und für alle nicht betroffenen Komponenten bleibt die Namens- und Digestgleichheit erhalten.

PRE_MORTEM: In drei Monaten scheitert dieses Paket am wahrscheinlichsten daran, dass Slice 01 als der solide Teil galt und niemand die Fixture noch einmal aufgemacht hat. Alles, was in diesem Slice sichtbar geprüft wird — Flags, Registry, Protokollbindung, Recordvertrag —, ist geschlossen und mehrfach gegengeprüft; die Baseline dagegen sieht korrekt aus, reproduziert sich bytegleich und trägt drei bestätigte Digests, weshalb sie kein weiteres Hinsehen provoziert. Ihr Fehler zeigt sich erst in Slice 3, wenn die Differenzgleichung um 354 Zeichen danebenliegt, und genau dann ist die einfachste Reparatur die verbotene: die Fixture neu erzeugen und den Lock nachziehen. Der Test wäre wieder grün, die Gleichung wieder erfüllt, und der einzige Beweis der Einsparung wäre stillschweigend zu einer Tautologie geworden. Die zweite, leisere Variante betrifft die beiden nun stummen Persistenzsenken: Sie sind heute nachweislich unerreichbar, aber sie schweigen statt zu scheitern. Wer in Slice 2 die Löschung anders schneidet als geplant und den Textzweig auch nur kurz wieder erreichbar macht, verliert Records ohne jede Fehlermeldung.

SLICE_APPROVAL: 01 | NO

STATUS: DONE

---

## Codex-Korrektur zu C-07

FINDING_RESPONSE: C-07 | ACCEPTED | Die Baseline bindet nun je Operation jede Evidenz-ID an ihren tatsächlichen `evidence_asset_NNN`-Komponentennamen, den Inhaltsdigest, den Digest und die Größe des kanonischen Manifesteintrags sowie den vollständigen Zeichen- und UTF-8-Bytebeitrag, der beim Entfernen dieses Eintrags aus `evidence_manifest` verschwindet. Der Lock bezeichnet seinen Namensraum ausdrücklich als `expected_removed_evidence_ids`.

### Korrekturdetails

- Jede Operation besitzt jetzt `evidence_bindings` mit:
  - `evidence_id`
  - `component_name`
  - `content_sha256`
  - `manifest_entry_sha256`
  - `manifest_entry_chars` und `manifest_entry_bytes`
  - `manifest_contribution_chars` und `manifest_contribution_bytes`
- Der Manifestbeitrag wird aus der Differenz zwischen dem kanonischen vollen
  Manifest und demselben Manifest ohne die jeweilige Evidenz gebildet. Damit
  ist auch das strukturelle Komma zwischen zwei Einträgen erfasst.
- Die Regression simuliert die Entfernung von `workflow-prompt` gegen die
  lebende Requestaufbaulogik für `codex_implementation` und
  `codex_correction`.
- Für Zeichen und UTF-8-Bytes gilt jeweils exakt:
  `baseline_total - reduced_total = removed_asset + manifest_contribution`.
- `stdin_prompt` sinkt exakt um den gebundenen Manifestbeitrag. Alle nicht
  betroffenen Komponenten behalten Namen und SHA-256.
- Evidenz-ID und Komponentenname sind nicht mehr implizit gleichgesetzt,
  sondern über die Fixture explizit miteinander verbunden.

### Aktualisierte Evidenz

- Baseline-SHA-256:
  `3f07b52b79badf1340998973c4145b20a93892ea0f174942cd21ab1331c7adfc`
- Lock-SHA-256:
  `11bb60a6aed1ab78b7911d9a2eb9ea6a216b845a313c324df0558085ad9fabb9`
- synthetischer Eingabedigest unverändert:
  `934d738b2554b26c906223ca79e795eb3417c25c48fc3df0cdb24f1b4239dce2`
- fokussierte C-07-Matrix: `4 passed`
- vollständige Repositorymatrix: `1186 passed in 83.54s`
- `git diff --check`: sauber
- externe Provideraufrufe: keine

---

## Claude-Review zu Slice 01 – Runde 2

REVIEWER: claude

Manueller Review außerhalb von `run_task`, ohne Provideraufruf, ohne
strukturierte JSON-Ausgabe und ohne vollständige Testsuite. Produktcode und
Tests blieben unverändert; angehängt wurde ausschließlich dieser Abschnitt.
Reviewgrenze: Plancommit `bdb955d4ca56ca292dd8bcbd4d1e776a2acb7a1b` gegen den
uncommitteten Arbeitsbaum, einschließlich der vier ungetrackten Neuzugänge, die
`git diff` nicht zeigt. Die vorgelegte Evidenz (633 und 1184 passed) habe ich
nicht reproduziert; alle Gegenproben unten sind providerfrei.

### C-07 ist behoben

Die Baseline wurde in Slice 01 neu erzeugt — zulässig, weil `C-05` die
Unveränderlichkeit erst ab der Slice-01-Freigabe verlangt — und trägt jetzt je
Operation einen `evidence_bindings`-Block mit `evidence_id`,
`component_name`, `content_sha256`, `manifest_entry_chars`/`_bytes`,
`manifest_entry_sha256` sowie `manifest_contribution_chars`/`_bytes`. Der Lock
heißt konsequent `expected_removed_evidence_ids` und beseitigt damit den von
mir gemeldeten Namensraumbruch zwischen Evidenz-IDs und Komponentennamen.

Der Modellierungsschritt, auf den es ankommt, ist die Unterscheidung zwischen
`manifest_entry_*` und `manifest_contribution_*`: Die Contribution wird als
`len(voller Manifest-JSON) − len(reduziertes Manifest-JSON)` berechnet und
enthält damit auch das wegfallende Trennzeichen. Das erklärt 353 gegenüber 354
beziehungsweise 357 gegenüber 358 und ist genau der Anteil, der mir in Runde 1
gefehlt hat.

Ich habe die Gleichung nicht dem neuen Test geglaubt, sondern unabhängig über
**alle** Operationen und **jede** gebundene Evidenz-ID nachgerechnet, indem ich
die Entfernung gegen die lebende Aufbaulogik simuliert habe. Acht Fälle sind
simulierbar, und alle acht schließen exakt — in Zeichen **und** in UTF-8-Bytes:

| Operation | entfernte Evidenz | gemessene Differenz | gebundene Erwartung |
|---|---|---|---|
| `codex_implementation` | `approved-plan` | 24 422 | 24 422 |
| `codex_implementation` | `workflow-prompt` | 24 441 | 24 441 |
| `codex_correction` | `approved-plan` | 24 422 | 24 422 |
| `codex_correction` | `workflow-prompt` | 24 437 | 24 437 |
| `codex_final_review` | `approved-plan` | 24 422 | 24 422 |
| `codex_final_review` | `workflow-prompt` | 24 439 | 24 439 |
| `codex_final_correction` | `approved-plan` | 24 422 | 24 422 |
| `codex_final_correction` | `workflow-prompt` | 24 443 | 24 443 |

Der in Runde 1 gemeldete Fehlbetrag von 24 441 gegen 24 087 für
`codex_implementation` ist damit vollständig aufgelöst. Zusätzlich habe ich
bestätigt, dass die Schrumpfung von `stdin_prompt` exakt der gebundenen
Contribution entspricht (354 in beiden geprüften Fällen) und dass alle nicht
betroffenen Komponenten nach der Entfernung Namen und SHA-256 behalten — für
vier Operationen ohne jede Abweichung. Der Umbau des Testhosts ist kein
Kurzschluss: `_bundle(operation, omitted_evidence_ids=...)` baut denselben
Kontext und Vertrag und ruft den echten `build_native_codex_request()`.

Der neue Test `test_workflow_prompt_removal_has_exact_asset_and_manifest_delta`
ist über `codex_implementation` und `codex_correction` parametrisiert, also
über je eine Implementierungs- und eine Korrekturoperation, und prüft Zeichen,
Bytes, den `stdin_prompt`-Anteil und die Digestgleichheit der übrigen
Komponenten. Das deckt sich mit dem Abnahmetext, den ich zu `C-07` formuliert
hatte. Ich betrachte `C-07` als geschlossen.

**Hinweis zu den in der Reviewanforderung genannten Prüfwerten:** Sie stammen
aus dem Stand vor der Korrektur. Im Repository gilt heute Baseline-SHA-256
`3f07b52b79badf1340998973c4145b20a93892ea0f174942cd21ab1331c7adfc` und
Lock-SHA-256
`11bb60a6aed1ab78b7911d9a2eb9ea6a216b845a313c324df0558085ad9fabb9`; der
synthetische Eingabedigest ist unverändert
`934d738b2554b26c906223ca79e795eb3417c25c48fc3df0cdb24f1b4239dce2`. Ich habe
alle drei Werte selbst berechnet. Der Implementierungsbericht führt in seinem
Kopf und im Korrekturabschnitt dieselben neuen Werte; das Dokument ist in sich
konsistent, und der Lock verweist auf denselben Baselinedigest. Die älteren
Werte stehen nur noch in meinem historischen Runde-1-Abschnitt, wo sie als
Befundquelle hingehören.

### Erneut geprüft, ohne Regression

Der übrige Slice ist gegenüber meiner ersten Runde unverändert; die einzige
Abweichung im getrackten Diff sind zwei Zeilen in `src/artifact_models.py`.
Ich habe geprüft, was dort weggefallen ist: die im nativen Zweig doppelte
Prüfung `reviewer must be claude`. Die maßgebliche Prüfung steht unverändert
als erste Anweisung in `ReviewPayload.__post_init__`, und meine Gegenprobe
weist `Role.CODEX` und `Role.ORCHESTRATOR` weiterhin ab — keine Regression,
nur eine entfernte Dopplung.

Die Kernverträge habe ich vollständig neu gefahren. Die Protokollbindung
akzeptiert nur das vollständige native Paar und weist fehlende, einseitige,
v1- und unbekannte Transporte ab; `ProtocolBinding.from_dict()` übergibt bei
fehlenden Schlüsseln weiterhin explizit `None` und lässt den neuen
Native-Default nicht greifen — der einzige plausible Weg zu einer stillen
Umdeutung bleibt damit verschlossen. Am Recordvertrag habe ich neun
Injektionen gegen beide Schichten geführt (Nullwert, v1-Version, entferntes
Feld, ungültiger Digest, falsche Rolle, falscher Reviewer, fremder
Request-ID-Präfix, entfernte Trias): `validate_artifact_document()` **und**
`ArtifactRecord.from_dict()` weisen jede ab, die Kontrollrecords roundtrippen.
Die AST-Inventur meldet unverändert ungebundene, teilgebundene,
`**kwargs`-gesplattete und attributaufgerufene Konstruktionen und lässt
korrekt gebundene Keyword- wie Positionsformen passieren.

Fail-closed-Reihenfolge, Unerreichbarkeit der beiden stummen Persistenzsenken,
Registry, Agentprofile, Dry-run-Grenzen sowie Dokumentation und Scope sind
unverändert gegenüber meiner ersten Runde: `src/workflow.py` und
`src/state_io.py` sind weiterhin unberührt, alle 27 geänderten Pfade liegen im
exakten Slice-01-Pfad, es gibt keine Restreferenz auf die vier entfernten
Schalter außerhalb der Negativkontrollen, und `git diff --check` ist sauber.
Die fokussierte Gegenprobe über `tests/test_provider_input_efficiency.py`,
`tests/test_native_transport_retirement.py`, `tests/test_artifact_models.py`
und `tests/test_cli.py` meldet `128 passed in 4.20s`.

### Eine Beobachtung für Slice 02, die kein Slice-01-Befund ist

Bei der Simulation über alle sechs Operationen sind zwei Fälle **nicht**
simulierbar: `codex_plan` und `codex_plan_revision` führen `workflow-prompt`
als einzige Evidenz, und `NativeCodexRequestSpec` weist eine leere
Evidenzmenge mit `evidence-invalid: evidence must be non-empty, sorted, and
unique` ab. Im produktiven Aufbau ist das dieselbe Lage: `src/workflow.py`
hängt `approved_work_plan` nur an, wenn `approved_plan_text` gesetzt ist, was
im Planschritt nicht zutrifft. Slice 02 entfernt `workflow-prompt` und muss
deshalb für Planoperationen gleichzeitig die typisierten Requestfelder
liefern, die Arbeitsschritt 5 ohnehin vorsieht — sonst scheitert der
Planrequest am Nichtleerheitsvertrag. Beide betroffenen Dateien liegen im
Slice-02-Pfad; für Slice 01 entsteht daraus kein Defekt, und die
Slice-3-Gleichung verlangt ausdrücklich nur Implementierungs- und
Korrekturoperationen.

REVIEW_EVIDENCE: Vollständige Entfernung aller vier Transportschalter aus Parser, Weitergabe, Helptext und Dokumentation samt Suche nach Restreferenzen; direkte Registrykonstruktion der nativen Adapter und `isinstance`-Prüfung im Produktionsdriver; Unerreichbarkeit der beiden stummen Persistenzsenken über die Bindungsgatterung in `src/workflow.py:1614/2033/2155`; Fail-closed-Matrix der Protokollbindung über sechs Deserialisierungsvarianten und Nachweis, dass `ProtocolBinding.from_dict()` fehlende Schlüssel explizit als `None` übergibt; Ablehnungsreihenfolge über `load_resumable_workflow_state()` vor Driver, Checkpoint, Store, Snapshot, Validierung, Providerattempt und Commit; neun Injektionen gegen Modell und JSON-Schema für beide Payloadformen einschließlich Rollen- und Reviewerkopplung sowie Request-ID-Namensraum; Nachweis, dass die entfernte Zeile in `ReviewPayload` nur eine Dopplung der weiterhin wirksamen `reviewer must be claude`-Prüfung war; Zähne und Blindstellen der AST-Inventur über sieben synthetische Formen; unabhängige Nachrechnung aller drei Repositorydigests; unabhängige Verifikation der Differenzgleichung über acht simulierbare Operation-Evidenz-Kombinationen in Zeichen und Bytes, der `stdin_prompt`-Contribution und der Namens- und Digeststabilität aller nicht betroffenen Komponenten; Scopeabgleich aller 27 geänderten Pfade gegen den exakten Slice-01-Pfad; `git diff --check` mit Exitcode 0 | Die Baseline ist ab dieser Freigabe unveränderlich, und ihr Beweiswert hängt daran, dass die Manifest-Contribution weiterhin als Differenz zweier kanonischer Manifestserialisierungen definiert bleibt; ändert eine spätere Änderung am Requestaufbau die Manifestform selbst — etwa Feldreihenfolge, Trennzeichen oder ein zusätzliches Feld —, verschiebt sich die Contribution, ohne dass die eingefrorene Zahl das anzeigen könnte | `codex_plan` und `codex_plan_revision` führen `workflow-prompt` als einzige Evidenz, und `NativeCodexRequestSpec` weist eine leere Evidenzmenge fail-closed ab; Slice 02 muss die typisierten Requestfelder für Planoperationen im selben Schritt liefern, in dem es `workflow-prompt` entfernt, sonst scheitert der Planrequest am Nichtleerheitsvertrag

PRE_MORTEM: In drei Monaten ist die wahrscheinlichste Fehlerursache nicht mehr die Baseline selbst, sondern die Form, gegen die sie misst. Die eingefrorene Manifest-Contribution ist als Differenz zweier kanonischer Serialisierungen des `evidence_manifest` definiert und liegt bei 353 bis 358 Zeichen — eine Zahl, die niemand mehr herleiten wird, wenn Slice 03 sie verwendet. Wer bis dahin ein Feld in den Manifesteintrag aufnimmt, die Sortierung ändert oder die Trennzeichensetzung des kanonischen JSON anfasst, verschiebt diese Zahl um wenige Zeichen; die Differenzgleichung bricht dann mit einer Abweichung, die klein genug ist, um wie ein Rundungsproblem auszusehen, und die naheliegende Reaktion ist, den erwarteten Wert im Test nachzuziehen statt die Ursache zu suchen. Ab diesem Moment misst der einzige Beweis der Einsparung wieder sich selbst. Die zweite, konkretere Variante steht unmittelbar bevor: Slice 02 entfernt `workflow-prompt`, und für die beiden Planoperationen ist das die einzige Evidenz. Wer die Entfernung vor den typisierten Requestfeldern implementiert, bekommt keinen stillen Fehler, sondern ein hartes `evidence-invalid` im Planpfad — unangenehm, aber immerhin laut.

SLICE_APPROVAL: 01 | YES

STATUS: DONE
