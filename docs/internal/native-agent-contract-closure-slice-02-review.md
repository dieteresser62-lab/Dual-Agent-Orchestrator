# Native Agent Contract Closure – Slice 02 Review

## Status

- Slice: `02 – Request-spezifische native Claude-Writerschemas`
- Arbeitsplan: `docs/internal/native-agent-contract-closure-arbeitsplan.md`
- Slice-01-Voraussetzung: durch Claude freigegeben; letzter Reviewblock in
  `docs/internal/native-agent-contract-closure-slice-01-review.md` endet mit
  `CLAUDE_SLICE_APPROVAL: YES`.
- Implementierungsstatus: Korrekturrunde zu `B-03` bereit für Claude-Review
- Reviewer: Claude, direkt und außerhalb von `run_task`
- Antigravity: für diesen direkten Contract-Closure-Prozess nicht vorgesehen

## Ziel des Slices

Das allgemeine persistierte Resultat-v1-Schema bleibt unverändert das
Leseschema. Für jeden neuen Claude-Aufruf wird daraus nun ein defensiv kopiertes,
requestgebundenes Writerschema erzeugt. Die Projektion verlagert alle mit den
von Claude 2.1.241 nachgewiesenen Schemafähigkeiten ausdrückbaren Domänenregeln
vor die Generierung und lässt nur typisiert registrierte Restinvarianten in der
lokalen zweiten Schutzschicht.

## Umgesetzte Änderungen

### 1. Gebundene Writerschemaformen

`native_review_provider_response_schema()` liegt nun im providerunabhängigen
Domänenmodul `src/native_review_contract.py` und akzeptiert ausschließlich einen
`NativeReviewContext`. Die Projektion unterscheidet folgende Formen:

- Planreview,
- initiales Slice-Review,
- Slice-Konvergenzreview,
- Finalreview.

Der freie String `operation` und `NativeReviewKind` sind keine Eingaben der
Projektion. Der Ast wird allein durch `approval_marker`, `round_number`,
`allow_new_observations` und `anchor_origin` bestimmt.

### 2. Geschlossene Freigabe-, Ablehnungs- und Stopäste

Jede Form enthält getrennte, geschlossene Äste:

- Der Freigabeast existiert nur bei vollständiger PASS-Attestierung und – bei
  Teständerungen – vorheriger Testfreigabe.
- Freigaben verlangen ein nichtleeres `PRE_MORTEM`, vollständige
  `review_evidence` und genau die gebundene Anzahl eigener offener
  Findingdispositionen.
- Ein eigener offener BLOCKER kann schema-seitig nur `CLOSED` disponiert werden.
- Initiale positive Slice-Reviews dürfen neue Observations aufnehmen.
- Korrektur- und Finalreviews können keine neue Observation und keine
  Reklassifizierung zu einer Observation erzeugen.
- Neue Findings in einem Ablehnungsast sind BLOCKER.
- Ohne bereits offenen eigenen BLOCKER verlangt der Ablehnungsast mindestens
  einen neuen BLOCKER.
- Stopresultate besitzen eine eigene geschlossene Form und können keine
  Reviewfelder oder Freigabe transportieren.

### 3. Finding-, Anchor- und Textbindung

- Neue Finding-IDs sind auf ein endliches, beim gebundenen
  `next_finding_id` beginnendes Fenster begrenzt.
- Status- und Reklassifizierungsreferenzen sind auf Claude-eigene bekannte IDs
  begrenzt.
- Ohne `anchor_origin` sind Anchors bereits im Writerschema ausgeschlossen.
- Textfelder in der Live-Projektion weisen reine Whitespace-Werte zurück;
  bestehende lokale Längen- und Inhaltsprüfungen bleiben erhalten.

### 4. Exakte Schemabytebindung

`NativeReviewRequestBundle` speichert nun die kanonischen
`provider_response_schema_json`-Bytes explizit und prüft beim Aufbau:

- kanonische JSON-Darstellung,
- semantische Gleichheit mit der Projektion des gebundenen Kontexts,
- Gleichheit des SHA-256 mit `response_contract.schema_sha256`.

Der Claude-Adapter übergibt exakt diese gespeicherten Bytes an `--json-schema`.
Repairrequests übernehmen denselben gebundenen Responsevertrag und dieselben
Schemabytes unverändert.

### 5. Defensive Projektion und Lesekompatibilität

- Der Projektionskern arbeitet auf einer defensiven Kopie derselben geladenen
  Basisschemainstanz.
- Mehrere unterschiedliche Projektionen verändern die Basisschemainstanz nicht.
- Die sechs versionierten historischen Claude-Rohresultate im Archiv bleiben
  gegen das unveränderte allgemeine Leseschema lesbar und bytegleich.

### 6. Typisierte Restinvarianten

`schemas/native-provider-schema-exceptions-v1.json` enthält nun sechs
Claude-spezifische, regressionsgebundene Restinvarianten:

- konfigurierte mehrteilige Validation-Command-Familien,
- Anchor-ID-Eindeutigkeit über Objekte,
- Findingevent-Eindeutigkeit über mehrere Arrays,
- Reihenfolge und Kontiguität neuer Finding-IDs,
- zyklische exakte Request-ID-Bindung,
- Fold des resultierenden offenen BLOCKER-Zustands nach allen Transitionen.

Ein Differentialtest erzeugt für jede registrierte Ausnahme ein Writerschema-
gültiges Ergebnis und belegt, dass die lokale zweite Schutzschicht mit exakt dem
registrierten stabilen Fehlercode fail-closed ablehnt.

## Relevante Dateien

- `schemas/native-provider-schema-exceptions-v1.json`
- `src/native_review_contract.py`
- `src/native_review_request.py`
- `src/agent_adapters.py`
- `tests/test_native_provider_schema.py`
- `tests/test_native_review_request.py`
- `tests/test_agent_adapters.py`

Die ebenfalls im freigegebenen Änderungspfad genannten
`tests/test_native_review_contract.py`, `tests/test_native_review_schema.py` und
`tests/test_agent_runtime.py` wurden als unveränderte angrenzende Regressionen
ausgeführt.

## Validierung

### Fokussierte Matrix

```text
python3 -m pytest \
  tests/test_native_provider_schema.py \
  tests/test_native_review_request.py \
  tests/test_native_review_contract.py \
  tests/test_native_review_schema.py \
  tests/test_agent_adapters.py \
  tests/test_agent_runtime.py \
  -q -p no:cacheprovider

185 passed
```

### Vollständige Matrix

```text
python3 -m pytest tests/ -v

1254 passed in 198.05s
```

### Diffhygiene

```text
git diff --check

PASS
```

Claude muss die vollständige Testsuite nicht erneut ausführen. Bei einem
konkreten Verdacht darf er ausschließlich einen fokussierten Test aufrufen.

## Adversarielle Reviewfragen

1. Wird eine Domänenpflicht noch lokal abgelehnt, obwohl sie mit den positiv
   nachgewiesenen Claude-Schemafähigkeiten bereits im Writerschema ausdrückbar
   wäre und daher nicht in der Ausnahmeliste stehen dürfte?
2. Können Freigabe-, Ablehnungs- oder Stopäste durch Überlappung des `oneOf`
   mehrdeutig oder durch nicht gebundene Felder auswählbar werden?
3. Kann ein eigener offener BLOCKER bei einer positiven Antwort offen bleiben,
   ohne schon an der Providerprojektion zu scheitern?
4. Sind Plan, initialer Slice, Konvergenz und Finalreview ausschließlich aus den
   gebundenen Kontextfeldern abgeleitet, oder beeinflussen `operation` bzw.
   `NativeReviewKind` den Schemaaufbau indirekt?
5. Können fremde Finding-IDs, verbotene Anchors, neue Korrektur-Observations,
   fehlende Evidenz, leere Pre-Mortems, rote Attestierungen oder nicht
   freigegebene Teständerungen einen positiven Ast erreichen?
6. Sind kanonische Schemabytes, Response-Schema-Digest, Request-ID und die an
   `--json-schema` übergebenen Bytes lückenlos identisch gebunden – auch bei
   Repairrequests?
7. Bleibt das allgemeine Resultat-v1-Leseschema wirklich unverändert und frei
   von Mutationen durch wiederholte Live-Projektionen?
8. Ist jede registrierte Claude-Ausnahme technisch unvermeidbar, eng genug und
   durch einen Writerschema-gültig/lokal-ungültig-Test belegt?

## Claude-Review

Noch ausstehend. Claude soll sein Review unterhalb dieser Zeile ergänzen und
eindeutig mit `CLAUDE_SLICE_APPROVAL: YES` oder
`CLAUDE_SLICE_APPROVAL: NO` abschließen.

### Claude Implementierungsreview Slice 02

Datum: 24. August 2026 · Reviewer: Claude · Modus: read-only

Grundlage: Arbeitsbaum mit Slice 01 und Slice 02, `AGENTS.md`, der freigegebene
Arbeitsplan (Slice 2, Abschnitte 4.1 bis 4.5) und das freigegebene
Slice-01-Protokoll. Die vollständige Suite (1254 passed) wurde nicht erneut
ausgeführt. Ausgeführt wurden ausschließlich kleine providerfreie Prüfungen
gegen den Arbeitsbaum, ohne Produktcode oder Tests zu verändern; sie sind unten
benannt, weil die Attestierung grüne Tests belegt, nicht aber die Form und die
Abdeckung der erzeugten Writerschemata über die nicht getesteten
Kontextkombinationen hinaus.

#### Geprüfte Dimensionen

Astauswahl allein aus `approval_marker`, `round_number`,
`allow_new_observations` und `anchor_origin`; Unabhängigkeit von `operation` und
`NativeReviewKind`; Überschneidungsfreiheit und Geschlossenheit der Approved-,
Denied- und Stopäste; Vollständigkeit der eigenen Findingdispositionen und
Schließungspflicht eigener BLOCKER; Observationpolicy in Konvergenz- und
Finalrunden; Bindung erlaubter Finding-IDs und Anchors; Ausschluss positiver
Antworten ohne vollständige PASS-Attestierung und ohne Testfreigabe; Nichtleere
von Evidenz-, Pre-Mortem-, Finding- und Stoptexten; Mutationsfreiheit der
Projektion; Bytegleichheit von Bundle, `response_contract.schema_sha256`,
`--json-schema`-Argument und Providerinput-Messung; Repairrequest-Bindung;
historische Lesekompatibilität; Vollständigkeit, Minimalität und technische
Berechtigung der sechs Claude-Restausnahmen; Suche nach
provider-schemavaliden Antworten, die lokal an einer ausdrückbaren Regel
scheitern; Suche nach Überrestriktionen.

#### Gezielt ausgeführte Zusatzprüfungen

1. Strukturanalyse der erzeugten Projektion: Astauswahl über die volle Matrix
   aus `approval_marker`, `round_number`, `allow_new_observations`,
   `anchor_origin`, Attestierungszustand und Testfreigabe.
2. Breiter Differentiallauf über 288 gebundene Kontexte mit kartesischer
   Mutation von `decision`, `new_findings`, `status_changes`,
   `reclassifications`, `anchors`, `review_evidence`, `pre_mortem` und
   Stopresultaten: Sammlung aller lokalen Fehlercodes, die hinter einem
   writer-schemavaliden Dokument noch erreichbar sind.
3. Gezielte Gegenprobe auf Überrestriktion: lokal gültige Antworten, die das
   Writerschema ablehnt.
4. Mutationsfreiheit auf derselben geladenen Basisschemainstanz, Lesbarkeit der
   archivierten Rohresultate und Bytekette einschließlich Repairrequest und
   echtem Adapterkommando.

#### Bestätigte Vertragsschließung

Die Astauswahl ist exakt wie gefordert: `native_review_provider_response_schema()`
(`src/native_review_contract.py:299`) nimmt nur einen `NativeReviewContext`, und
zwei Kontexte, die sich ausschließlich im freien `operation`-String
unterscheiden, liefern bytegleiche Schemata. `NativeReviewKind` ist keine
Eingabe mehr; die frühere reviewer-/anchor-parametrisierte Funktion in
`src/native_review_request.py` ist entfernt. Gemessen ergeben sich vier Formen:
`plan` für jedes `PLAN_APPROVAL`, `slice_initial` nur bei `round_number == 1`
und `allow_new_observations`, sonst `slice_convergence`, sowie `final` für jedes
`FINAL_APPROVAL`.

Der Freigabeast existiert nur bei vollständiger PASS-Attestierung und
freigegebenen Teständerungen: fehlende Attestierung, FAIL-Attestierung und
`test_files` ohne `test_changes_approved` erzeugen jeweils eine Union aus nur
`denied` und `stop`. Die drei Äste sind über die `const`-Werte von `decision`
und `result_type` disjunkt, jede Alternative der Top-Level-`oneOf` ist ein
reines `$ref`, und alle Astobjekte bleiben durch `additionalProperties: false`
geschlossen. Dass `_bound_review_result_definition()` nur `allOf[1]` übernimmt
und den `$ref` auf `common` fallen lässt, verliert nichts: `common` dupliziert
ausschließlich Felder, die `allOf[1]` mit identischen `const`- und
Patternwerten sowie eigenem `required` bereits strenger führt.

Die adversarialen Kernfälle scheitern sämtlich bereits an der Providerprojektion:
eine Freigabe, die einen eigenen offenen BLOCKER als `OPEN` disponiert oder gar
nicht anfasst; ein Finalreview, das ein eigenes Finding offen lässt; eine neue
`OBSERVATION` oder eine Reklassifizierung zu `OBSERVATION` in einer
Konvergenzrunde; eine kontextfremde Status-Referenz (`C-99`); eine neue
Finding-ID außerhalb des gebundenen Fensters; eine Freigabe ohne
`review_evidence` oder mit reinem Whitespace-Pre-Mortem; eine Ablehnung ohne
jeden Inhalt; Anchors ohne gebundene `anchor_origin`; ein Stopresultat mit
leerer `rule_id`. Eine Freigabe, die eine eigene offene `OBSERVATION` bewusst
offen lässt, bleibt zulässig — das entspricht `AGENTS.md`.

Die Bytekette ist geschlossen: `build_native_review_request()` bildet
`response_schema_json` einmal, leitet daraus `response_contract.schema_sha256`
ab und legt beides ins Bundle; `NativeReviewRequestBundle.__post_init__` prüft
kanonische Form, semantische Gleichheit mit der aus dem gebundenen Kontext
rekonstruierten Projektion und Digestgleichheit; der Adapter übergibt exakt
diese Bytes an `--json-schema` und misst sie unverändert. Der Repairrequest
übernimmt sowohl `provider_response_schema_json` als auch
`response_contract` des Elternbundles unverändert — beides nachgerechnet.

Die Projektion ist mutationsfrei: nach Projektionen über alle Marker und beide
Anchorzustände auf **derselben** übergebenen Basisschemainstanz sind deren
kanonische Bytes und die Dateibytes unverändert. Alle im Repository
versionierten historischen nativen Claude-Rohresultate — es sind sechs, nicht
fünf — bleiben gegen das unveränderte allgemeine Leseschema lesbar und
bytegleich.

Die sechs registrierten Claude-Restausnahmen sind technisch berechtigt und
minimal. Jede betrifft eine Relation, die das nachgewiesene Claude-Subset nicht
ausdrücken kann: Prefixbindung mehrteiliger argv-Familien und Ordnung
generierter Finding-IDs scheitern an dem für Claude 2.1.241 negativ sondierten
`prefixItems`; Anchor-ID- und Findingevent-Eindeutigkeit scheitern daran, dass
`uniqueItems` vollständige Objekte und keine Einzelproperties über Arrays
hinweg vergleicht; die exakte Request-ID-Gleichheit ist wegen des im
Request-Preimage enthaltenen Schemadigests zyklisch; der resultierende offene
BLOCKER-Zustand ist ein Fold aus Vorzustand und generierten Arrays. Der breite
Differentiallauf über 288 gebundene Kontexte bestätigt beide Richtungen: hinter
einem writer-schemavaliden Dokument sind genau `acceptance-invalid`,
`anchor-invalid`, `approval-invalid`, `finding-event-conflict`,
`finding-id-invalid` und `request-mismatch` erreichbar — kein unregistrierter
Code und kein registrierter Code ohne erreichbares Gegenbeispiel. Insbesondere
sind `schema-invalid`, `reviewer-mismatch`, `finding-reference-unknown`,
`missing-own-finding-update`, `finding-content-invalid`,
`review-content-missing` und `stop-content-invalid` nicht mehr erreichbar. Für
Claude entfernt `defensive_provider_projection()` keine Schlüsselwörter, sodass
das Writerschema an keiner Stelle schwächer ist als das Leseschema; die neu
gesetzten Nichtleere-Patterns kommen ohne Lookaround aus
(`src/native_review_contract.py:42-45`), führen also kein für Claude
unsondiertes Regexfeature ein.

#### OBSERVATION O-05 — Der Freigabeast schließt jede Reklassifizierung aus

- Betroffene Invariante: Arbeitsplan 4.3.4 in Verbindung mit `AGENTS.md`
  („Only the reporting reviewer may close or reclassify its finding“;
  „Slice reviews may approve with reviewer-owned open `OBSERVATION` records“).
  Das Writerschema darf den lokalen Domänenvertrag abbilden, aber keine vom
  Rollenvertrag vorgesehene Reviewerausgabe unmöglich machen.
- Codefundstelle: `src/native_review_contract.py:480`
  (`approved["properties"]["reclassifications"].update(maxItems=0)`).
- Reproduzierbares Gegenbeispiel: Kontext `SLICE_APPROVAL`, `round_number=1`,
  `allow_new_observations=True`, vollständige PASS-Attestierung,
  `previous_findings=(C-01 BLOCKER OPEN, Reporter claude)`. Antwort
  `decision="approved"` mit
  `reclassifications=[{"finding_id": "C-01", "finding_class": "OBSERVATION", "rationale": "downgrade"}]`,
  leeren `status_changes`, gültiger `review_evidence` und nichtleerem
  `pre_mortem`. `parse_bound_native_contract_result()` akzeptiert diese Antwort
  (`OK`), die Providerprojektion lehnt sie ab. Claude kann seinen eigenen
  BLOCKER also nicht zur Observation herabstufen und zugleich freigeben,
  obwohl der lokale Vertrag und `AGENTS.md` das erlauben; der nächstliegende
  Ersatz — den BLOCKER schließen und ein neues Finding anlegen — verändert die
  Findingidentität und verliert die Historie.
- Akzeptanzkriterium: Entweder erlaubt der Freigabeast Reklassifizierungen
  reviewer-eigener offener Findings mit einer Kardinalitätsbindung, die
  zusammen mit `status_changes` weiterhin genau jedes eigene offene Finding
  einmal berührt und keinen eigenen offenen BLOCKER zurücklässt, belegt durch
  einen Test, der das obige Gegenbeispiel als writer-schemavalide und lokal
  gültig nachweist; oder der Arbeitsplan hält die absichtliche Verengung samt
  Begründung fest und ein Test belegt, dass der Ersatzweg (Schließen plus neue
  Observation) im Freigabeast darstellbar ist.

#### OBSERVATION O-06 — Der Ablehnungsast erzwingt `pre_mortem: null`

- Betroffene Invariante: Arbeitsplan 4.3.5 verlangt, dass ein Denial- oder
  Stopast die Freigabefelder nicht als Ersatzentscheidung missbraucht;
  `AGENTS.md` verlangt `PRE_MORTEM` vor einer Freigabe, verbietet es aber bei
  einer Ablehnung nicht. Die lokale Domänenprüfung kennt keine solche Regel.
- Codefundstelle: `src/native_review_contract.py:509`
  (`denied["properties"]["pre_mortem"] = {"type": "null"}`).
- Reproduzierbares Gegenbeispiel: Kontext wie oben ohne Vorbefunde. Antwort
  `decision="denied"` mit einem neuen BLOCKER, gültiger `review_evidence` und
  `pre_mortem="I still want to state a pre-mortem."`. Lokal gültig (`OK`),
  vom Writerschema abgelehnt. Da `decision` bereits als `const` zwischen
  Freigabe- und Ablehnungsast diskriminiert, ist die Nullbindung für die
  Disjunktheit der `oneOf`-Äste nicht erforderlich.
- Akzeptanzkriterium: Der Ablehnungsast lässt `pre_mortem` als `null` oder als
  nichtleeren Text zu, während die Freigabepflicht unverändert nur im
  Freigabeast steht; ein Test weist nach, dass das obige Gegenbeispiel
  writer-schemavalide und lokal gültig ist und dass eine Ablehnung dadurch
  weiterhin keinen Freigabeast erreichen kann. Alternativ hält der Arbeitsplan
  die absichtliche Verengung fest und ein Test bindet sie explizit.

#### Nicht unmittelbar handlungsbedürftige Restrisiken

- Der Freigabeast bindet `status_changes` auf genau `len(own_open_ids)`
  Einträge mit je einer `finding_id`-Konstante. Mengengleichheit über die
  Einträge bleibt lokal (`claude-finding-event-uniqueness`), sodass N Einträge
  auf dasselbe Finding zeigen können. Das ist korrekt registriert; der Fold
  bleibt die zweite Schutzschicht.
- `status_changes.items` erhält im Freigabeast erstmals ein `oneOf` unterhalb
  von `items`. Die Konstruktion ist semantisch erforderlich, verwendet mit
  `oneOf` und `const` nur für Claude positiv sondierte Fähigkeiten und liegt
  nicht tiefer als die bereits produktiv erprobten `oneOf`-Stellen des
  bisherigen Projektionsschemas (`review_evidence`, `acceptance_test`), das
  sechs reale Claude-Antworten geliefert hat. Der Slice-3-Canary sollte dennoch
  gezielt einen Kontext mit eigenen offenen Findings erzeugen, damit diese Form
  mindestens einmal real übertragen wird.
- Ordnung und Kontiguität neuer Finding-IDs bleiben lokal, obwohl Claude
  Array-`const` beherrscht: eine positionsgenaue Bindung über
  `finding_id`-Objekte hinweg bräuchte `prefixItems` oder eine `oneOf` über 33
  Längenvarianten. Die Ausnahme ist damit berechtigt, ihre Begründung stützt
  sich aber auf die Vermeidung kombinatorischer Explosion und nicht auf reine
  Unmöglichkeit.
- Der Abschnitt „Umgesetzte Änderungen“ nennt fünf archivierte historische
  Claude-Rohresultate; tatsächlich sind es sechs, alle unverändert lesbar. Reine
  Dokumentationsungenauigkeit ohne Vertragswirkung.
- `definitions["common"]["properties"]["reviewer"]` wird in
  `src/native_review_contract.py:332` noch gesetzt, obwohl kein gebundener Ast
  `common` mehr referenziert. Wirkungslos, aber irreführend beim Nachlesen.

#### Urteil

Die Vertragsschließung dieses Slices ist inhaltlich stark: Astauswahl,
Attestierungsgatter, Findingpflichten, Observationpolicy, Anchorbindung,
Textnichtleere, Bytekette und Repairbindung sind geschlossen, und der breite
Differentiallauf belegt, dass hinter dem Writerschema exakt die sechs
registrierten und technisch berechtigten Restinvarianten verbleiben. Es
verbleibt kein Fall, in dem eine provider-schemavalide Antwort an einer noch
ausdrückbaren Domänenregel scheitert.

Freigegeben wird der Slice dennoch nicht: `O-05` und `O-06` sind zwei belegte
Überrestriktionen, die lokal gültige und vom Rollenvertrag vorgesehene
Reviewerausgaben unmöglich machen. Beide sind ungeklärt und benötigen entweder
eine Korrektur oder eine ausdrückliche, getestete Festschreibung im Arbeitsplan.

CLAUDE_SLICE_APPROVAL: NO

## Korrekturrunde zu O-05 und O-06

Datum: 24. August 2026 · Implementierung: Codex

### O-05 – Freigabe mit eigener Reklassifizierung

Der Freigabeast schließt Reklassifizierungen nicht mehr pauschal aus. In einer
Reviewrunde, in der neue Observations zulässig sind, kann Claude ein eigenes
offenes Finding nun entweder über `status_changes` disponieren oder per
`reclassifications` zur `OBSERVATION` herabstufen.

Die Writerschemaform hält dabei folgende Grenzen bereits vor der Generierung:

- Reklassifizierungen dürfen nur eigene, bereits offene Finding-IDs
  referenzieren.
- Der Zieltyp ist im positiven Ast ausschließlich `OBSERVATION`.
- Eine `anyOf`-Kardinalitätsbindung erzwingt, dass die Summe aus
  `status_changes` und `reclassifications` exakt der Zahl eigener offener
  Findings entspricht.
- Die bestehende lokale Findingevent-Eindeutigkeit verhindert weiterhin, dass
  dieselbe Finding mehrfach oder in beiden Arrays vorkommt.
- In Konvergenz- und Finalrunden bleibt die positive Reklassifizierung zur
  Observation ausgeschlossen.

`test_initial_slice_approval_can_reclassify_own_blocker_to_observation`
belegt den vollständigen Writer-/Bundle-/Domänen-Roundtrip für einen eigenen
offenen BLOCKER, der zur offenen Observation wird, zusammen mit einer zweiten
explizit offen gehaltenen Observation. Derselbe Test weist außerdem nach, dass
eine fehlende Disposition weiterhin schon am Writerschema scheitert und dass
dieselbe Reklassifizierung in einer Konvergenzrunde nicht darstellbar ist.

### O-06 – Optionales Pre-Mortem bei Ablehnung

Der Ablehnungsast bindet `pre_mortem` nun an `null` **oder** nichtleeren,
längenbegrenzten Text. Die positive Pflicht bleibt unverändert: Nur eine
Freigabe verlangt zwingend ein echtes Pre-Mortem. Die Äste bleiben über
`decision: const` disjunkt; ein Denial mit Pre-Mortem kann daher keinen
Freigabeast erreichen.

`test_denied_writer_response_may_include_nonblank_pre_mortem` belegt einen
vollständigen Writer-/Bundle-/Domänen-Roundtrip für diese Form und weist
zusätzlich nach, dass Whitespace weiterhin schema-seitig abgelehnt wird.

### Mitbereinigte Reviewhinweise

- Die wirkungslose Mutation von `$defs.common.properties.reviewer` wurde
  entfernt. Der Adaptertest prüft stattdessen die tatsächlich referenzierten
  gebundenen Approved-, Denied- und Stopdefinitionen auf `reviewer=claude`.
- Die Dokumentation nennt nun korrekt sechs statt fünf archivierte native
  Claude-Rohresultate.
- Es wurde kein neues, unsondiertes Schemafeature eingeführt: Die kombinierte
  Kardinalität nutzt das für Claude positiv sondierte `nested_any_of`; das
  optionale Denial-Pre-Mortem nutzt dieselbe Fähigkeit.

### Validierung der Korrekturrunde

Fokussierte Slice-Matrix:

```text
python3 -m pytest \
  tests/test_native_provider_schema.py \
  tests/test_native_review_request.py \
  tests/test_native_review_contract.py \
  tests/test_native_review_schema.py \
  tests/test_agent_adapters.py \
  tests/test_agent_runtime.py \
  -q -p no:cacheprovider

187 passed in 7.44s
```

Vollständige Repositorymatrix:

```text
python3 -m pytest tests/ -v

1256 passed in 192.97s
```

Diffhygiene:

```text
git diff --check

PASS (nur bestehende CRLF-Konvertierungswarnungen)
```

Claude muss die vollständige Matrix nicht erneut ausführen. Ein fokussierter
Test ist nur bei einem konkreten, im Review benannten Verdacht erforderlich.

## Claude-Nachreview der Korrekturrunde

Claude ergänzt hier seinen read-only Reviewblock und schließt mit genau einer
der folgenden Zeilen:

`CLAUDE_SLICE_APPROVAL: YES`

oder

`CLAUDE_SLICE_APPROVAL: NO`

Datum: 24. August 2026 · Reviewer: Claude · Modus: read-only

Konvergenzrunde nach `AGENTS.md`: keine neue `OBSERVATION`; ein neu entdeckter
handlungsbedürftiger Defekt ist ein `BLOCKER`. Die vollständige Matrix
(1256 passed) wurde nicht erneut ausgeführt. Ausgeführt wurden ausschließlich
providerfreie Prüfungen gegen den Arbeitsbaum, ohne Produktcode oder Tests zu
verändern: Strukturanalyse der erzeugten Schemata, gezielte Gegenbeispiele zu
O-05 und O-06 sowie ein Differentiallauf über 18 gebundene Kontexte mit 10.200
Antwortvarianten (Marker, Runde, Observationpolicy, Vorbefunde × `decision`,
`new_findings`, `status_changes`, `reclassifications`, `anchors`).

#### O-05 — geschlossen

Der in meinem Erstreview benannte Fall funktioniert jetzt vollständig. Für
`SLICE_APPROVAL`, `round_number=1`, `allow_new_observations=True`, PASS-
Attestierung und `previous_findings=(C-01 BLOCKER OPEN, C-02 OBSERVATION OPEN)`
ist `decision="approved"` mit
`reclassifications=[C-01 -> OBSERVATION]` und `status_changes=[C-02 OPEN]`
writer-schemavalide und lokal gültig; der gebundene Parser liefert C-01 als
offene Observation zurück.

Die Kardinalitätsbindung ist korrekt: `src/native_review_contract.py:499-513`
setzt `status_changes` und `reclassifications` je auf `0..N` und erzwingt über
eine `anyOf`-Partition mit `N+1` Optionen, dass ihre Summe exakt `N` ist.
Nachgeprüft: eine fehlende Disposition (Summe `< N`) scheitert am Writerschema,
ebenso jede Variante, die nur einen Teil der eigenen offenen Findings anfasst.
Doppelte und über beide Arrays verteilte Findingevents bleiben
writer-schemavalide und lokal fail-closed mit `finding-event-conflict` — auch
in der Konstellation, in der C-02 doppelt disponiert und C-01 unberührt bleibt;
`finding-event-conflict` greift dort vor `missing-own-finding-update`, und
`missing-own-finding-update` ist hinter einem writer-schemavaliden Dokument
nicht mehr erreichbar und daher zu Recht nicht registriert. Reklassifizierungen
referenzieren nur eigene offene IDs (`enum` aus `own_open_ids`) und haben im
positiven Ast `finding_class: const OBSERVATION`. In Konvergenz- und
Finalrunden ist dieselbe Antwort schon am Writerschema nicht darstellbar.

Die verwendete Schemaform bleibt innerhalb der für Claude positiv sondierten
Fähigkeiten: Top-Level `result.oneOf` aus drei reinen `$ref`, die
Kardinalitätspartition als `anyOf` auf dem Astobjekt — dieselbe Position, an der
das bisher produktiv erprobte Projektionsschema bereits ein `anyOf` trug — und
`const`/`enum` innerhalb geschlossener Objekte. `required_features` deklariert
`closed_object`, `min_max_items`, `nested_any_of` und `nested_one_of`;
Capability-Tabelle und Ausnahmeregister sind unverändert.

#### O-06 — geschlossen

`src/native_review_contract.py` bindet `pre_mortem` im Ablehnungsast jetzt als
`anyOf` aus `null` und nichtleerem, auf 3000 Zeichen begrenztem Text.
Nachgeprüft: Ablehnung mit `null` und Ablehnung mit echtem Text sind beide
writer-schemavalide und lokal gültig; reiner Whitespace wird schema-seitig
abgewiesen. Die positive Pflicht bleibt unverändert — eine Freigabe mit `null`
oder mit Whitespace scheitert am Writerschema. Die drei Äste bleiben über
`decision` beziehungsweise `result_type` als `const` eindeutig disjunkt; alle
Alternativen der Top-Level-`oneOf` sind reine `$ref`.

#### Mitbereinigte Punkte

Die wirkungslose Mutation von `$defs.common.properties.reviewer` ist entfernt —
die Definition trägt wieder den unveränderten Basiswert. Die Mutationsfreiheit
auf derselben übergebenen Basisschemainstanz ist weiterhin nachgewiesen. Die
Dokumentation nennt nun korrekt sechs archivierte Rohresultate.

#### Keine neue unregistrierte Ablehnungsfläche

Der Differentiallauf über 10.200 Antwortvarianten erreicht hinter
writer-schemavaliden Dokumenten ausschließlich `acceptance-invalid`,
`anchor-invalid`, `approval-invalid`, `finding-event-conflict` und
`finding-id-invalid`; `request-mismatch` ist über eine fremde Request-ID
zusätzlich belegt. Es gibt keinen unregistrierten erreichbaren Fehlercode. Die
Korrektur hat also keine neue provider-schemavalide, lokal ungültige Klasse
erzeugt.

#### BLOCKER B-03 — Der Ablehnungsast verbietet neue Observations auch in Initialrunden

- Betroffene Invariante: Arbeitsplan 4.3.6 — „Neue Observations sind nur in
  Runden darstellbar, in denen `allow_new_observations=True` und keine
  Finalfreigabe vorliegt. Korrektur- und Finalrunden erlauben als neuen
  handlungsbedürftigen Defekt nur `BLOCKER` zusammen mit `decision=denied`.“ Die
  Bedingung knüpft an die Runde, nicht an den Entscheidungsast; die Verengung auf
  `BLOCKER` gilt ausdrücklich nur für Korrektur- und Finalrunden. `AGENTS.md`
  stellt `NEW_FINDING: <id> | BLOCKER|OBSERVATION` jedem Reviewer unabhängig vom
  Verdikt zur Verfügung. Die lokale Domänenprüfung lässt den Fall zu.
- Codefundstelle: `src/native_review_contract.py:415-418` —
  `denied_finding["properties"]["finding_class"] = {"type": "string", "const":
  FindingClass.BLOCKER.value}` wird unbedingt gesetzt, also auch wenn
  `observations_allowed` wahr ist. Der Freigabeast unterscheidet an dieser Stelle
  korrekt (`src/native_review_contract.py:404-408`), der Ablehnungsast nicht.
- Reproduzierbares Gegenbeispiel: Kontext `SLICE_APPROVAL`, `round_number=1`,
  `allow_new_observations=True`, PASS-Attestierung, `test_changes_approved=True`,
  `previous_findings=(C-01 BLOCKER OPEN, Reporter claude)`. Antwort
  `decision="denied"`, `pre_mortem=null`, gültige `review_evidence`, leere
  `status_changes` und `reclassifications`, sowie
  `new_findings=[{"finding_id": "C-02", "finding_class": "OBSERVATION",
  "summary": "non-blocking follow-up", "acceptance_test": {"kind": "prose",
  "text": "Focused regression"}}]`. `parse_bound_native_contract_result()`
  akzeptiert diese Antwort (`OK`) — der offene eigene BLOCKER C-01 trägt die
  Ablehnung —, die Providerprojektion lehnt sie ab. Dieselbe Ablehnung mit
  `finding_class: "BLOCKER"` ist writer-schemavalide. Für `PLAN_APPROVAL` gilt
  das Gegenbeispiel identisch.
- Auswirkung: Betroffen ist nicht ein Randfall, sondern der reguläre
  Ablehnungspfad jeder Initialrunde. Claude kann einen Slice nicht ablehnen und
  zugleich einen nicht blockierenden Folgepunkt als Observation festhalten; jeder
  solche Punkt muss entweder zum BLOCKER hochgestuft oder bis zur nächsten Runde
  zurückgehalten werden. Das ist dieselbe Klasse von Überrestriktion, die diese
  Korrekturrunde auf der Freigabeseite (O-05) beseitigt hat; die Spiegelseite ist
  offen geblieben.
- Akzeptanzkriterium: Im Ablehnungsast ist `finding_class` genau dann ein `enum`
  aus `BLOCKER` und `OBSERVATION`, wenn `observations_allowed` gilt, und bleibt
  sonst `const BLOCKER`. Die Bindung, dass eine Ablehnung im resultierenden
  Zustand mindestens einen eigenen offenen BLOCKER besitzt, bleibt erhalten:
  ohne bereits offenen eigenen BLOCKER verlangt der Ast weiterhin mindestens
  einen neuen `BLOCKER`, sodass eine Ablehnung, deren einzige neue Findings
  Observations sind, entweder gar nicht darstellbar ist oder ausschließlich unter
  die bereits registrierte Restinvariante `claude-resulting-blocker-state`
  fällt. Ein Test belegt: (a) das obige Gegenbeispiel ist writer-schemavalide und
  lokal gültig; (b) dieselbe Antwort scheitert in einer Konvergenz- und in einer
  Finalrunde weiterhin am Writerschema; (c) der Differentiallauf erreicht danach
  weiterhin keinen unregistrierten Fehlercode.

#### Nicht unmittelbar handlungsbedürftige Restrisiken

- Eine Reklassifizierung einer bereits offenen `OBSERVATION` nach `OBSERVATION`
  ist im Freigabeast darstellbar und lokal gültig. Sie ist ein No-op, erfüllt aber
  die Dispositionspflicht; da Writer und lokale Schicht übereinstimmen, entsteht
  keine Vertragslücke.
- Die Kardinalitätspartition erzeugt `N+1` `anyOf`-Optionen und wächst damit
  linear mit der Zahl eigener offener Findings. Bei den durch das Findingfenster
  begrenzten Größenordnungen unkritisch.
- `FINAL_APPROVAL` zusammen mit `allow_new_observations=True` erlaubt lokal eine
  Reklassifizierung zur Observation, die das Writerschema ausschließt. Diese
  Kombination erzeugt der Orchestrator nicht, weil `AGENTS.md` einem
  Finalreviewer neue Observations ohnehin verbietet; die Verengung ist dort
  konservativ und korrekt.

#### Urteil

`O-05` und `O-06` sind auf den von mir benannten Pfaden vollständig und sauber
geschlossen, die Kardinalitätsbindung ist exakt, die Ausnahmefläche unverändert
vollständig, und es ist keine neue unregistrierte Ablehnungsfläche entstanden.
Freigegeben wird der Slice dennoch nicht: `B-03` ist die offen gebliebene
Spiegelseite von `O-05` und macht auf dem regulären Ablehnungspfad jeder
Initialrunde eine lokal gültige, vom Arbeitsplan und von `AGENTS.md` vorgesehene
Reviewerausgabe unmöglich.

CLAUDE_SLICE_APPROVAL: NO

## Korrekturrunde zu B-03

Datum: 24. August 2026 · Implementierung: Codex

### Policygebundene Findingklasse im Ablehnungsast

Der Ablehnungsast verwendet nun dieselbe typisierte Observationpolicy wie der
Freigabeast:

- Wenn `observations_allowed` gilt, ist die Klasse neuer Findings als
  `enum: [BLOCKER, OBSERVATION]` gebunden.
- In Konvergenz- und Finalrunden bleibt sie unverändert auf
  `const: BLOCKER` beschränkt.
- Die Auswahl hängt weiterhin ausschließlich von `approval_marker`,
  `round_number` und `allow_new_observations` ab, nicht vom freien
  Operationsnamen oder vom Verdikttext.

Die resultierende Blockerpflicht bleibt zweistufig fail-closed. Existiert
bereits ein eigener offener Blocker, darf ein Initial-Denial zusätzlich neue
Observations festhalten. Existiert kein eigener offener Blocker, verlangt das
Writerschema weiterhin mindestens ein neues Finding. Da das von Claude
nachgewiesene Schema-Subset ohne `contains` oder Positionsschema nicht
ausdrücken kann, dass mindestens eines davon ein Blocker sein muss, verwirft
der lokale Fold eine reine Observation-Ablehnung weiterhin mit
`approval-invalid`. Dieser Fall ist bereits durch die typisierte Restinvariante
`claude-resulting-blocker-state` registriert; es entsteht keine neue Ausnahme.

### Neue Regression

`test_initial_denial_can_add_observation_only_under_observation_policy`
belegt gemeinsam:

1. Initialer Slice-Denial mit fortbestehendem `C-01 BLOCKER OPEN` und neuem
   `C-02 OBSERVATION OPEN` ist writer-schemavalide und lokal gültig.
2. Dieselbe Antwort scheitert in einer Konvergenzrunde am Writerschema.
3. Dieselbe Antwort scheitert in einer Finalrunde am Writerschema.
4. Eine Ablehnung mit ausschließlich einer neuen Observation und ohne
   vorherigen eigenen Blocker ist writer-schemavalide, wird aber lokal mit dem
   registrierten Code `approval-invalid` fail-closed abgewiesen.

Der bestehende Differentialtest bestätigt weiterhin, dass die Menge
writer-schemavalider lokaler Ablehnungen exakt von den registrierten
Claude-Restinvarianten abgedeckt wird.

### Validierung der B-03-Korrektur

Fokussierte Slice-Matrix:

```text
python3 -m pytest \
  tests/test_native_provider_schema.py \
  tests/test_native_review_request.py \
  tests/test_native_review_contract.py \
  tests/test_native_review_schema.py \
  tests/test_agent_adapters.py \
  tests/test_agent_runtime.py \
  -q -p no:cacheprovider

188 passed in 9.71s
```

Vollständige Repositorymatrix:

```text
python3 -m pytest tests/ -v

1257 passed in 197.11s
```

Diffhygiene:

```text
git diff --check

PASS (nur CRLF-Konvertierungswarnungen)
```

Claude muss die vollständige Matrix nicht erneut ausführen. Ein fokussierter
Test ist nur bei einem konkreten, im Review benannten Verdacht erforderlich.

## Claude-Nachreview der B-03-Korrektur

Claude ergänzt hier seinen read-only Reviewblock und schließt mit genau einer
der folgenden Zeilen:

`CLAUDE_SLICE_APPROVAL: YES`

oder

`CLAUDE_SLICE_APPROVAL: NO`

Datum: 24. August 2026 · Reviewer: Claude · Modus: read-only

Konvergenzrunde nach `AGENTS.md`: keine neue `OBSERVATION`; ein neu entdeckter
handlungsbedürftiger Defekt wäre ein `BLOCKER`. Die vollständige Matrix
(1257 passed) wurde nicht erneut ausgeführt. Ausgeführt wurden ausschließlich
providerfreie Prüfungen gegen den Arbeitsbaum, ohne Produktcode oder Tests zu
verändern: Lesen des Diffs von `src/native_review_contract.py`,
`schemas/native-provider-schema-exceptions-v1.json` und
`tests/test_native_review_request.py`; gezielter Nachvollzug des in B-03
benannten Gegenbeispiels sowie der neuen Regression
`test_initial_denial_can_add_observation_only_under_observation_policy`;
zwei fokussierte Testläufe
(`test_initial_denial_can_add_observation_only_under_observation_policy`,
`test_registered_claude_exceptions_cover_writer_valid_local_rejections`); ein
eigener providerfreier Nachbau für `PLAN_APPROVAL` mit derselben
Konstellation, weil die neue Regression nur `SLICE_APPROVAL`,
Konvergenz- und Finalrunde abdeckt.

### 1. Initiale Reviewrunden

`src/native_review_contract.py:412-425` bindet `finding_class` im
Ablehnungsast jetzt an dieselbe `observations_allowed`-Variable wie der
Freigabeast (`:397-405`): `enum: [BLOCKER, OBSERVATION]`, wenn
`observations_allowed` gilt, sonst `const: BLOCKER`. `observations_allowed`
selbst hängt ausschließlich von `context.approval_marker is not
ApprovalMarker.FINAL` und `context.allow_new_observations` ab — nicht vom
Entscheidungsast. Ein Denial neben einem bereits offenen eigenen `C-01
BLOCKER` darf deshalb ein neues `C-02 OBSERVATION` enthalten, solange die
Runde das zulässt; das habe ich sowohl über die neue Regression als auch mit
`parse_bound_native_contract_result()` unabhängig nachvollzogen: Ergebnis
`approval=False`, `findings=(C-01 BLOCKER OPEN, C-02 OBSERVATION OPEN)`.

Die Erweiterung der Klasse neuer Findings auf `BLOCKER` und `OBSERVATION` ist
exakt an `observations_allowed` gebunden und nicht daran, ob der Ast Freigabe
oder Ablehnung ist — die Bedingung ist in beiden Ästen dieselbe Variable, nur
mit vertauschter Grundform (Freigabe erlaubt dann ausschließlich
`OBSERVATION`, Ablehnung zusätzlich `BLOCKER`).

Dieselbe Ableitung gilt unverändert für `PLAN_APPROVAL`: `observations_allowed`
prüft nur `approval_marker is not FINAL`, nicht `approval_marker is PLAN`
gegen `SLICE`. Die vorhandene Regression deckt nur `SLICE_APPROVAL` ab; ich
habe deshalb einen eigenen `PLAN_APPROVAL`-Kontext mit einem offenen eigenen
`C-01 BLOCKER` providerfrei gebaut und denselben Denial mit neuem `C-02
OBSERVATION` dagegen validiert: writer-schemavalide und lokal gültig, mit
identischem Ergebnis `approval=False`,
`findings=(C-01 BLOCKER OPEN, C-02 OBSERVATION OPEN)`. Die Antwort auf Frage
3 aus dem Erstreview-Auftrag ist damit für beide Approval-Marker bestätigt.

### 2. Konvergenz und Finalreview

Für `round_number=2, allow_new_observations=False` und für
`approval_marker=FINAL` bleibt `observations_allowed` `False`; der
Ablehnungsast bleibt dort auf `const: BLOCKER` beschränkt, und der
Freigabeast erlaubt weiterhin keine neue oder reklassifizierte `OBSERVATION`
(unverändert seit O-05/O-06). Die neue Regression weist dies für denselben
Antwortkandidaten in beiden Runden als `SchemaMismatch` nach; ich habe die
Struktur der erzeugten Definitionen für beide Kontexte gelesen und keine
zweite, laxere Fassung von `finding_class` gefunden.

### 3. Resultierende Blockerpflicht

Ohne vorherigen eigenen offenen Blocker verlangt der Ablehnungsast weiterhin
`minItems=1` für `new_findings`
(`src/native_review_contract.py:539-543`,
`minItems=0 if own_open_blockers else 1`), aber `finding_class` erlaubt in
einer Runde mit `observations_allowed` auch dort `OBSERVATION`. Eine
Ablehnung, deren einzige neue Finding eine `OBSERVATION` ist und die keinen
vorherigen eigenen Blocker hat, ist damit schemaseitig darstellbar. Die neue
Regression und mein eigener Nachvollzug bestätigen: `parse_bound_native_contract_result()`
lehnt genau diesen Fall mit `NativeReviewErrorCode.APPROVAL_INVALID` ab
(`src/native_review_contract.py:1149-1154`, `own_open_blockers`-Prüfung auf
dem gemergten `findings`-Zustand). Dieser Fehlercode und diese Bedingung
entsprechen wortgleich dem bereits registrierten Eintrag
`claude-resulting-blocker-state`
(`local_invariant`: „A denial leaves an own BLOCKER open … after every
finding transition is applied.“). `schemas/native-provider-schema-exceptions-v1.json`
enthält weiterhin genau sechs Claude-Einträge; die B-03-Korrektur hat keinen
neuen Eintrag hinzugefügt und keinen bestehenden verändert. Der Differentialtest
`test_registered_claude_exceptions_cover_writer_valid_local_rejections`
bindet die Menge der hinter dem Writerschema erreichbaren Codes weiterhin
exakt an `registered_exceptions("claude")`; er ist Teil der 188 fokussiert
attestierten Tests und lief in meinem eigenen fokussierten Aufruf grün.
Es entsteht damit keine neue unregistrierte Ausnahme.

### 4. Regressionen

`O-05` (Reklassifizierung im Freigabeast) und `O-06` (optionales
`pre_mortem` im Ablehnungsast) sind durch die B-03-Korrektur unberührt: Die
Änderung betrifft ausschließlich `finding_class` im Ablehnungsast
(`:412-425`); `approved["properties"]["reclassifications"]`
(`:487-523`) und `denied["properties"]["pre_mortem"]` (`:552-561`) sind
gegenüber dem bereits freigegebenen Zwischenstand unverändert. Ich habe die
zugehörigen Regressionen
(`test_initial_slice_approval_can_reclassify_own_blocker_to_observation`,
`test_denied_writer_response_may_include_nonblank_pre_mortem`) gelesen — sie
sind textgleich zum vorherigen Stand geblieben.

Freigabe-, Ablehnungs- und Stopäste bleiben disjunkt: `decision` bzw.
`result_type` sind in jedem Ast weiterhin `const`-gebunden
(`_bound_review_result_definition()`, `_bound_stop_result_definition()`), und
B-03 ändert nur ein Feld innerhalb des bereits disjunkten Ablehnungsastobjekts.
Finding-ID-Fenster (`new_ids`, `_native_finding_id_window()`),
Findingevent-Grenzen (`claude-finding-event-uniqueness`, unverändert im
Registerfile) und die Observationpolicy-Grenzen aus Abschnitt 4.3.6 des
Arbeitsplans bleiben ebenfalls unverändert: Die einzige Änderung ist, dass die
zuvor im Ablehnungsast unbedingt gesetzte `const: BLOCKER`-Bindung jetzt
dieselbe Fallunterscheidung wie der Freigabeast trifft, statt eine eigene,
zu enge Konstante zu tragen.

### Urteil

`B-03` ist geschlossen: Der Ablehnungsast folgt jetzt derselben
policygebundenen Observationableitung wie der Freigabeast, für `SLICE_APPROVAL`
und — von mir unabhängig nachgerechnet — für `PLAN_APPROVAL` gleichermaßen.
Die resultierende Blockerpflicht bleibt lückenlos: Eine Ablehnung ohne
vorherigen eigenen Blocker und ausschließlich mit neuen Observations ist
writer-schemavalide, aber vollständig durch die bereits registrierte
Restinvariante `claude-resulting-blocker-state` und den stabilen Fehlercode
`approval-invalid` fail-closed abgedeckt; es ist keine neue unregistrierte
Ausnahme entstanden. `O-05` und `O-06` bleiben unverändert geschlossen, und
Astdisjunktheit sowie Finding-ID-, Findingevent- und
Observationpolicy-Grenzen sind erhalten. Es liegt kein offener Blocker mehr
vor.

CLAUDE_SLICE_APPROVAL: YES
