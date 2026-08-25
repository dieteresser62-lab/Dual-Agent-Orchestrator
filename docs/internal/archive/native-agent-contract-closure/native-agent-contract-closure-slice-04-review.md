# Native Agent Contract Closure – Slice 04 Korrektur und Review

## Status

- Slice: `04 – Geschlossene Findingzustände und tiefer Claude-Kompositionsbeleg`
- Anlass:
  `docs/internal/native-agent-contract-closure-final-self-review-codex.md`
- zu disponierende Befunde: `CR-01` schließen; `CR-02` in der ursprünglichen
  Form verwerfen und durch den engeren Kompositionstiefen-Nachweis ersetzen
- Basiscommit: `eb38a31` (`feat(native-contracts): complete contract closure slice 03`)
- Implementierungsstatus: abgeschlossen; bereit für den Claude-Slice-Review
- Slice-Review: ausstehend
- Reviewer: Claude, direkt und außerhalb von `run_task`
- Antigravity: für diesen direkten Contract-Closure-Prozess nicht vorgesehen

Dieses Dokument ist Arbeitsbeschreibung, Implementierungsprotokoll und spätere
menschenlesbare Reviewingabe für den Korrekturslice. Es ist keine technische
Entscheidungs- oder Recoveryquelle und nimmt keine Freigabe vorweg.

## Ziel

Slice 04 verarbeitet die beiden technischen Befunde aus dem unabhängigen
Codex-Gesamtreview unter Berücksichtigung von Claudes unabhängiger
Nachrechnung:

1. Bereits geschlossene reviewer-eigene Findings dürfen weder vom konkreten
   Claude-Writerschema angeboten noch von der lokalen zweiten Schutzschicht
   wieder geöffnet oder reklassifiziert werden.
2. `CR-02` wird in seiner ursprünglichen Begründung verworfen: Die im
   freigegebenen Plan gleichberechtigte `request_bound`-Alternative bleibt
   gültig. Geschlossen wird stattdessen die darunterliegende echte
   Transportlücke: `status_changes.items.oneOf` wurde noch nie mit Claude
   übertragen. Genau der bereits implementierte Konvergenz-Canary belegt
   diese tiefere Kompositionsform unter dem aktuellen Writerschema.

Der Slice ändert weder die allgemeinen persistierten Leseschemas noch die
Findingzustandsmaschine außerhalb dieser beiden Vertragslücken.

## Exakter Änderungspfad

- `src/native_review_contract.py`
- `scripts/native_contract_probe.py`
- `tests/test_native_review_contract.py`
- `tests/test_native_contract_differential.py`
- `tests/test_native_contract_corpus.py`
- `tests/test_native_contract_probe.py`
- `tests/fixtures/native-contract-corpus/manifest.json`
- `docs/internal/ORCHESTRATOR_ROADMAP_PHASE_2_PLUS.md`
- `docs/internal/native-agent-contract-closure-slice-03-review.md`
- `docs/internal/native-agent-contract-closure-slice-04-review.md`

Neue Antwortfixtures sind nicht vorgesehen. Der erfolgreiche
Konvergenz-Canary wird wie die vorhandenen Live-Canaries durch Request-ID,
Writerschema-SHA-256, Antwort-SHA-256 und Status im Manifest gebunden.
Geheimnisse, absolute temporäre Pfade und reine Providertelemetrie dürfen
nicht persistiert werden.

## CR-01 – Geschlossene Findings unveränderlich halten

### Ausgangsdefekt

Die Writerschemaprojektion bildet `own_ids` aus allen Claude-eigenen Findings
und verwendet diese Menge im Ablehnungsast für `status_changes` und
`reclassifications`. Die lokale Ereignisprüfung kontrolliert Existenz und
Eigentum, jedoch nicht, ob das referenzierte Finding vor dem Update noch
`OPEN` war. Dadurch kann ein abgelehntes Review ein bereits geschlossenes
Finding erneut öffnen oder reklassifizieren.

Die schwerere Variante umgeht zusätzlich die resultierende Blocker-Invariante:
Enthält der Kontext nur einen bereits geschlossenen eigenen Blocker und keinen
offenen Blocker, kann ein Denial eine neue Observation melden und den
geschlossenen Blocker gleichzeitig wieder öffnen. Writerschema und lokale
Domäne akzeptieren damit den Denial erst durch den verbotenen Reopen; dasselbe
Dokument ohne Reopen scheitert korrekt an `approval-invalid`. Es handelt sich
nicht nur um eine unerlaubte Zustandsänderung, sondern um einen Bypass von
`claude-resulting-blocker-state`.

### Umsetzung

1. Alle Writeräste, die Statusänderungen oder Reklassifizierungen anbieten,
   werden ausschließlich aus den offenen reviewer-eigenen Finding-IDs
   konstruiert. `own_ids` darf nur noch dort verwendet werden, wo ausdrücklich
   alle historischen eigenen IDs gemeint sind.
2. Kardinalitätsgrenzen für Statusänderungen und Reklassifizierungen werden
   aus der tatsächlich angebotenen offenen ID-Menge abgeleitet. Ein
   geschlossener Datensatz darf die zulässige Ereignisanzahl nicht erhöhen.
3. `_validate_response_events()` erhält unabhängig vom Writerschema eine
   fail-closed Prüfung des bisherigen Findingstatus. Statusänderung oder
   Reklassifizierung eines nicht offenen Findings wird mit dem neuen,
   sachlich eindeutigen stabilen Diagnosecode `finding-reference-not-open`
   abgewiesen; der bekannte Datensatz wird nicht irreführend als „unknown“
   bezeichnet. Es wird kein neuer Providerausnahmeeintrag angelegt.
4. Der bestehende legitime Übergang eines offenen Findings nach `CLOSED` und
   eine zulässige Reklassifizierung eines offenen eigenen Findings bleiben
   unverändert möglich.
5. Die Differentialmethodik wird um eine explizite Überpermissivitätsfläche
   ergänzt. Sie darf nicht nur Writerablehnungen mit lokalen Fehlercodes
   vergleichen, sondern muss fachlich verbotene Dokumente benennen und
   beweisen, dass weder Writer noch lokale Schutzschicht sie akzeptieren.

### Regressionen

- Ein Kontext enthält mindestens `C-01 CLOSED` und `C-02 OPEN`, beide von
  Claude, und beweist, dass nur `C-02` weiter disponierbar ist.
- Ein zweiter Kontext enthält ausschließlich `C-01 BLOCKER CLOSED` und keinen
  offenen eigenen Blocker. Ein Denial mit neuer Observation plus
  `C-01 -> OPEN` muss sowohl am Writer als auch lokal scheitern; die Kontrolle
  ohne Reopen muss weiterhin mit `approval-invalid` abgelehnt werden.
- Das konkrete Denial-Writerschema weist `C-01` sowohl in
  `status_changes` als auch in `reclassifications` zurück.
- Eine direkte lokale Konvertierung derselben unzulässigen Dokumente weist
  beide Varianten mit `finding-reference-not-open` zurück, auch wenn eine
  vorgelagerte Schemavalidierung bewusst umgangen wird.
- Ein Denial darf `C-02` weiter `OPEN` lassen oder zulässig bearbeiten, ohne
  `C-01` zu verändern.
- Die Differentialmatrix enthält die Mutationsklassen
  `update-or-reclassify-closed-own-finding` und
  `satisfy-denial-by-reopening-closed-blocker`. Beide Fälle sind schon im
  Writer rot und werden bei bewusst umgangener Schemavalidierung zusätzlich
  lokal abgewiesen. Sie benötigen keinen Eintrag in
  `schemas/native-provider-schema-exceptions-v1.json`.

## CR-02 – Korrigierter Befund: ungeprüftes `items.oneOf` im Konvergenzschema

### Korrektur des ursprünglichen Befunds

Der ursprüngliche CR-02 ist kein Implementierungsblocker. Abschnitt 4.5.6 des
freigegebenen Arbeitsplans lässt als Writerformbeleg ausdrücklich entweder ein
verifiziertes `request_bound`-Fixturepaar oder einen erfolgreichen
Live-Canary zu. Abschnitt 4.5.2 definiert dabei bewusst die retrospektive
Validierung gegen die aus dem originalen Requestkontext rekonstruierte heutige
Writerschemainstanz. Dass der historische Request noch den alten allgemeinen
Schemadigest
`31510b2d44b4d277a0dec7fda9b3baed4144a34ff5d5e67cbeceb65045acedd4`
trug, entwertet diesen planmäßig zugelassenen Nachweis nicht.

Slice 04 verschärft diese Regel daher nicht nachträglich und verlangt weder
zwei neue Canaries noch Digestgleichheit zwischen einem historischen Request
und einem erst später eingeführten request-spezifischen Writerschema. Die drei
historischen `request_bound`-Paare und ihre bisherige Klassifizierung bleiben
unverändert gültig.

### Tatsächliche Transportlücke

Im Claude-Freigabeast mit eigenen offenen Findings enthält
`status_changes.items` ein `oneOf`, das die erlaubten Finding-IDs und bei einem
offenen Blocker den erzwungenen Zielstatus bindet. Die Capability-Sonde hat
`nested_one_of` bisher nur eine Ebene direkt unter einer Objektproperty
gemessen. Die erfolgreichen Claude-Canaries für Plan und Finalreview liefen
ohne `previous_findings` und übertrugen diese tiefere Array-Item-Komposition
daher nicht.

Der vorhandene Bundlebuilder für `convergence` erzeugt bereits genau den
benötigten Repräsentativkontext mit einem offenen eigenen Finding. Es fehlt
nur der reale Providerlauf dieses vorhandenen Canaries.

### Umsetzung

1. Ein providerfreier Strukturtest beweist am kanonischen
   Konvergenz-Requestbundle, dass dessen tatsächlich serialisierte
   Providerschemabytes den Pfad `status_changes.items.oneOf` enthalten und
   dass die Optionen ausschließlich die gebundenen offenen eigenen Finding-IDs
   anbieten.
2. Nach grüner providerfreier Validierung wird genau ein direkter Canary über
   den bestehenden Claude-Adapter-/Runtimepfad ausgeführt:

   ```text
   python3 scripts/native_contract_probe.py --provider claude --canary-form convergence
   ```

3. Der Canary verwendet die kanonischen aktuellen Writerschemabytes des
   gebundenen Requestbundles, bindet die Antwort anschließend lokal und
   bestätigt bytegleichen Repositorystatus sowie unveränderten rekursiven
   Integritätsabdruck der geschützten Workflowstores.
4. Die Writerformzeile `claude/convergence` in `manifest.json` wird erst nach
   erfolgreichem realem Lauf von `request_bound` auf `live_canary` umgestellt.
   Request-ID, Writerschema-SHA-256, Antwort-SHA-256 und Status werden aus dem
   tatsächlichen Canaryresultat übernommen, nicht vorausberechnet oder
   erfunden.
5. `claude/initial_slice` bleibt planmäßig durch sein verifiziertes
   `request_bound`-Paar belegt. Alle drei historischen Paare bleiben
   unverändert als Corpus-, Kompatibilitäts- und Domänenbelege erhalten.
6. Roadmap und Slice-03-Bericht erläutern präzise, dass der zusätzliche
   Konvergenz-Canary nicht die `request_bound`-Alternative widerruft, sondern
   gezielt die zuvor ungemessene Kompositionstiefe
   `status_changes.items.oneOf` am realen Claude-Transport schließt.

### Regressionen und Nachweise

- Der Strukturtest arbeitet auf den exakten serialisierten Schemabytes des
  Bundles und nicht auf einem vereinfachten Nachbau der Subset-Sonde.
- Er beweist zusätzlich, dass ein Konvergenzkontext ohne offene eigene
  Findings die Findingdispositionsform nicht erfindet.
- Die Manifestprüfung verlangt weiterhin genau acht eindeutige Writerformen:
  sieben `live_canary`-Belege und einen planmäßig zulässigen
  `request_bound`-Beleg für `initial_slice`.
- Ein technisches Provider- oder Quotaversagen ist kein Contract-Nachweis und
  darf das Manifest nicht aktualisieren.
- Eine Wiederholung ist nur bei einer als transient klassifizierten
  technischen Störung zulässig; eine fachlich ungültige Antwort bleibt
  fail-closed.

## Implementierter Stand

### Unveränderlichkeit geschlossener Findings

- `native_review_provider_response_schema()` leitet Statusänderungen,
  Reklassifizierungen und deren Kardinalitätsgrenzen ausschließlich aus
  `own_open_ids` ab. Geschlossene eigene IDs erscheinen damit in keinem
  zulässigen Writerzweig mehr.
- `_validate_response_events()` prüft denselben bisherigen Status unabhängig
  vom Writerschema. Direkte lokale Konvertierungen werden mit dem stabilen
  Diagnosecode `finding-reference-not-open` abgewiesen.
- Provider- und Domänentests decken sowohl das Wiederöffnen als auch das
  Reklassifizieren eines geschlossenen eigenen Findings ab.
- Eine separate rote Kontrolle beweist, dass ein Denial seinen erforderlichen
  offenen Blocker nicht durch Wiederöffnen eines abgeschlossenen Blockers
  erschleichen kann. Ohne den Reopen bleibt die vorhandene
  `approval-invalid`-Invariante wirksam.

### Überpermissivitätskontrollen

Die Differentialmatrix prüft nicht mehr nur Unterschiede zwischen
Writerablehnungen und lokalen Diagnosecodes. Zwei fachlich verbotene
Dokumentklassen werden ausdrücklich konstruiert:

1. `update-or-reclassify-closed-own-finding`;
2. `satisfy-denial-by-reopening-closed-blocker`.

Beide Klassen scheitern bereits am konkreten Writerschema. Wird diese erste
Schicht im Test bewusst umgangen, weist die lokale Domäne sie ebenfalls
fail-closed zurück. Dafür wurde keine Providerausnahme ergänzt.

### Reale Claude-Kompositionsevidenz

Der vorhandene Produktions-Canary für `convergence` wurde ohne vereinfachten
Schemanachbau verwendet. Ein providerfreier Test bindet die exakt
serialisierten Writerschemabytes an den Request und weist darin
`status_changes.items.oneOf` mit ausschließlich `C-01 -> CLOSED` nach. Die
Kontrolle mit nur einem bereits geschlossenen eigenen Finding bietet keine
Findingdisposition an.

Der anschließende einzelne reale Claude-Lauf war erfolgreich:

- Writerform: `convergence`;
- Request-ID:
  `native-review-request-58e98da53bd078faa2e2e666d9a1549a51435c683709040dab0aa2d26a11d236`;
- Writerschema-SHA-256:
  `a86ebb519fc2dcdf4936b01d00397e46452ba7bc8c2591e1523e021b1bf22e04`;
- Antwort-SHA-256:
  `f6a2eff236b5a5b560657eb44b7ec5c932ca4782861e5857c400e932c965953e`;
- lokale Entscheidung: `approved`;
- Repositorystatus: unverändert;
- geschützte Workflowstores: rekursiv unverändert;
- Repositorypfade im Reviewerprofil: nicht schreibbar.

Das Corpusmanifest führt deshalb nun sieben `live_canary`-Writerformen und
weiterhin genau einen planmäßig zulässigen `request_bound`-Beleg für
`claude/initial_slice`. Die drei historischen Request-/Antwortpaare bleiben
unverändert erhalten. Lediglich ihre gegen den heutigen Kontext
rekonstruierten Writerschemadigests wurden nach der CR-01-Verengung
deterministisch aktualisiert.

## Nichtziele

- keine Änderung des allgemeinen nativen Claude-Leseschemas;
- keine neue Finding-ID, kein Schließen oder Erfinden fachlicher Findings;
- keine Lockerung der lokalen Domäneninvarianten;
- keine Erweiterung der Provider-Ausnahmeliste;
- keine Umstellung des Antigravity-Pfads;
- keine Korrektur der separaten Dokumentationsabweichung `CR-03` innerhalb
  dieses Slices;
- kein Bootstrap- oder `run_task`-Lauf.

## Verbindliche Akzeptanzkriterien

1. Ein geschlossenes eigenes Finding ist in keinem konkreten Claude-
   Writerschema als Status- oder Reklassifizierungsziel darstellbar.
2. Die lokale zweite Schutzschicht weist denselben Zustand auch ohne
   vorgelagerte Schemavalidierung stabil fail-closed ab.
3. Alle weiterhin erlaubten OPEN-Transitionen und alle vier Claude-
   Writerformen bleiben funktionsfähig.
4. Die Differentialmatrix führt beide geschlossene-Finding-Mutationen als
   Überpermissivitätskontrollen ohne neue Providerausnahme.
5. Der Konvergenz-Writerschema-Strukturtest weist in den tatsächlich
   serialisierten Bundlebytes `status_changes.items.oneOf` mit exakt den
   offenen gebundenen Finding-IDs nach.
6. Für `convergence` liegt ein erfolgreiches direktes Providerresultat mit
   dem aktuellen Writerschemadigest vor; `initial_slice` bleibt entsprechend
   dem freigegebenen Plan durch sein `request_bound`-Paar belegt.
7. Historische Requests und Antworten bleiben unverändert und ihre zulässigen
   Kompatibilitäts-, Writer- und Domänenaussagen werden nicht rückwirkend
   eingeschränkt.
8. Manifest, Roadmap und Slice-Bericht machen sowohl die Evidenzklasse als
   auch den zusätzlich belegten tiefen Schemapfad eindeutig sichtbar.
9. Providerfreie fokussierte Tests, die vollständige Testsuite und
   `git diff --check` sind grün. Der externe Canary läuft erst nach der grünen
   providerfreien Matrix.
10. Claude prüft den vollständigen Slice-04-Diff direkt und erteilt die
   Slice-Freigabe; Codex genehmigt seine eigene Korrektur nicht.

## Vorgesehene Validierung

### Providerfrei fokussiert

```text
python3 -m pytest \
  tests/test_native_review_contract.py \
  tests/test_native_contract_differential.py \
  tests/test_native_contract_corpus.py \
  tests/test_native_contract_probe.py \
  -v
```

### Vollständig

```text
python3 -m pytest tests/ -v
git diff --check
```

### Externe Transportnachweise

Erst nach der grünen lokalen Matrix und mit unverändertem Arbeitsbaum außer
den autorisierten Slice-04-Pfaden:

```text
python3 scripts/native_contract_probe.py --provider claude --canary-form convergence
```

## Ausgeführte Validierung

- fokussierte providerfreie Slice-Matrix:
  `42 passed in 1.23s`;
- vollständige Repositorymatrix nach der persistierten Canary-Evidenz:
  `1273 passed in 192.25s`;
- `git diff --check`: sauber;
- genau ein erfolgreicher externer Claude-Konvergenz-Canary; keine
  Wiederholung und kein produktiver `run_task`-/Bootstrap-Lauf.

Claude muss die vollständige Testsuite im Slice-Review nicht erneut
ausführen. Kleine providerfreie Gegenproben sind zulässig, wenn sie für eine
konkrete Falsifikation erforderlich sind. Der externe Canary darf im Review
nicht wiederholt werden.

## Reviewauftrag an Claude

Claude soll nach der Implementierung insbesondere falsifizieren:

- ob ein geschlossenes eigenes Finding über irgendeinen Denial-, Approval-
  oder Reklassifizierungsast wieder veränderbar ist;
- ob ein Denial seinen erforderlichen resultierenden offenen Blocker durch
  Wiederöffnen eines geschlossenen Blockers erschleichen kann;
- ob die lokale Schutzschicht denselben Angriff ohne Writerschemavalidierung
  abweist;
- ob die beiden neuen Überpermissivitätskontrollen tatsächlich schon am
  Writer scheitern und bei bewusst übersprungener Writerschemavalidierung
  auch lokal fail-closed bleiben;
- ob das kanonische Konvergenzbundle wirklich
  `status_changes.items.oneOf` enthält und genau diese Bytefolge dem Provider
  übergeben wurde;
- ob Request-, Writer- und Antwortdigests des Konvergenz-Canaries aus dem
  tatsächlichen Ergebnis nachrechenbar sind;
- ob die drei historischen Paare weiterhin unverändert und entsprechend der
  freigegebenen `request_bound`-Alternative behandelt werden;
- ob Canarygrenze und Produktivdefaults unverändert sicher bleiben.

Bis dieses Review positiv abgeschlossen ist, bleibt Contract Closure
insgesamt nicht abschlussfähig.

## Claude Slice-Review 04

Datum: 24. August 2026 · Reviewer: Claude · Modus: read-only, adversarial

Korrekturrunde nach `AGENTS.md`: keine neue `OBSERVATION`; ein neu entdeckter
ausführbarer Defekt wäre ein denyender `BLOCKER`, nicht ausführbare
Restrisiken stehen ausschließlich in `REVIEW_EVIDENCE`. Grundlage sind der
vollständige Diff ab `eb38a31`, das ungetrackte Slice-04-Dokument, beide
Gesamtreviews als Befundquellen sowie die attestierten Läufe (`1273 passed`,
fokussiert `42 passed`, `git diff --check` sauber). Weder die vollständige
Suite noch der externe Canary wurden wiederholt. Ausgeführt wurden
ausschließlich kleine providerfreie Gegenproben zur Falsifikation konkreter
Behauptungen.

### Ledgerhinweis

`CR-01` und `CR-02` sind Codex-eigene Bezeichner aus dem Selbstreview. Ich hatte
den unter `CR-01` gemeldeten Defekt vor diesem Slice unabhängig nachgerechnet
und ihm die reviewer-eigene ID `C-29` zugewiesen; da der Korrekturslice vor
einer Überarbeitung meines Gesamtreviews entstand, trägt das Ledger dafür
bisher keinen Claude-Record. Ich hole das hier nach und schließe ihn unten.

### 1. CR-01 — geschlossene Findings sind unveränderlich

Die Korrektur ist minimal und trifft genau die Ursache: `own_ids` ist entfernt,
`bound_status_change` und `bound_reclassification` werden aus `own_open_ids`
gebildet (`src/native_review_contract.py:426-434`), die Kardinalitätsgrenzen
des Ablehnungsasts lauten `maxItems=len(own_open_ids)` (`:544-551`), und
`_validate_response_events()` prüft nach Existenz und Eigentum zusätzlich
`finding.status is not FindingStatus.OPEN` und wirft den neuen stabilen Code
`finding-reference-not-open` (`:977-981`).

Ich habe meine vier ursprünglichen Gegenbeispiele erneut gefahren. Alle
scheitern jetzt auf **beiden** Schichten:

| Angriff | Writer | Lokal |
|---|---|---|
| Denial öffnet geschlossenes Finding | rejected | `finding-reference-not-open` |
| Denial reklassifiziert geschlossenes Finding | rejected | `finding-reference-not-open` |
| Denial erschleicht Pflichtblocker durch Reopen | rejected | `finding-reference-not-open` |
| Kontrolle: dasselbe Denial ohne Reopen | VALID | `approval-invalid` |

Die Kontrolle ist der entscheidende Punkt: Der Bypass von
`claude-resulting-blocker-state` ist beseitigt, und die ursprüngliche
Invariante greift unverändert weiter. Legitime Transitionen bleiben
funktionsfähig — ein Denial darf ein offenes Finding unberührt lassen, und eine
Freigabe schließt ihren eigenen offenen Blocker weiterhin korrekt.

**Kardinalitätsgrenzen ausschließlich aus offenen Findings**: bestätigt. Ein
geschlossener Datensatz erhöht die zulässige Ereignisanzahl nirgends mehr.

**Breitensuche statt Stichprobe**: Ich habe 54 gebundene Kontexte erzeugt
(sechs Vorbefundmengen × drei Approvalmarker × drei Runden-/Policykombinationen)
und darin 252 Writerkandidaten geprüft, die jede eigene Finding-ID — offene wie
geschlossene — als Status- und als Reklassifizierungsziel in Freigabe- und
Ablehnungsast ansprechen. **Kein einziger Kandidat mit geschlossenem Ziel
übersteht das Writerschema.** Alle vier Writerformen (`plan`, `slice_initial`,
`slice_convergence`, `final`) entstehen dabei weiterhin.

### 2. Überpermissivitätskontrollen

Die neuen Tests prüfen tatsächlich stille Annahme und nicht nur bekannte
Ablehnungscodes. `test_closed_own_finding_mutations_are_rejected_by_writer_and_domain`
und `test_denial_cannot_satisfy_blocker_state_by_reopening_closed_blocker`
(`tests/test_native_contract_differential.py`) behaupten je Mutation zuerst
`pytest.raises(SchemaMismatch)` gegen das konkrete Writerschema und danach
`FINDING_REFERENCE_NOT_OPEN` über `parse_bound_native_contract_result()`. Da
dieser Pfad ausschließlich gegen das Leseschema und die Domäne validiert, ist
die bewusst umgangene Writerschicht dort realistisch modelliert. Der zweite
Test führt zusätzlich die Kontrolle ohne Reopen und verlangt `APPROVAL_INVALID`.
`tests/test_native_review_contract.py` spiegelt beide Klassen auf Domänenebene.

Das schließt genau die methodische Lücke, die ich im Gesamtreview benannt
hatte: Die bisherige Differentialmethodik band erreichbare *Fehlercodes* an
registrierte Ausnahmen und war damit für Überpermissivität blind, bei der
Writer und lokale Schicht übereinstimmen und beide zu weit sind.

**Keine unnötige Providerausnahme**: `schemas/native-provider-schema-exceptions-v1.json`
ist unverändert (13 Einträge, sieben Codex, sechs Claude);
`finding-reference-not-open` ist dort korrekt nicht registriert. Ich habe
verifiziert, warum das richtig ist: In meiner Breitensuche über 252 Kandidaten
ist der Code hinter einem writer-validen Dokument **nicht erreichbar**, und es
trat kein unregistrierter Code auf. Eine Registrierung hätte die bestehende
Mengengleichheitsregression sogar rot gefärbt.

### 3. CR-02 — korrigierte Disposition

**Der ursprüngliche CR-02 war kein Implementierungsblocker.** Das bestätige ich
ausdrücklich und aus derselben Begründung, die ich in meiner Analyse hergeleitet
habe: Abschnitt 4.5.6 des freigegebenen Plans stellt `request_bound`-Paar und
Live-Canary gleichberechtigt nebeneinander, und Abschnitt 4.5.2 definiert die
Stärke der ersten Alternative abschließend als Validierung gegen die aus dem
originalen gebundenen Requestkontext *rekonstruierte* Writerschemainstanz.
Dieser Wortlaut existiert, weil ich ihn mit meinem Planblocker `C-22` erzwungen
habe. CR-02s Forderung nach Digestgleichheit zwischen historischem Request und
heutigem Writerschema ist für jedes historische Fixture logisch unerfüllbar und
hätte die `request_bound`-Alternative vollständig entwertet. Slice 04 verwirft
sie zu Recht.

**Der engere echte Befund ist geschlossen.** Ich habe den Konvergenz-Canary
providerfrei nachgerechnet — nicht die Providerantwort, sondern die
Bindungskette:

- Request-ID aus `_claude_canary_bundle("convergence")` neu gebaut:
  `native-review-request-58e98da53bd078faa2e2e666d9a1549a51435c683709040dab0aa2d26a11d236`
  — **bitgenau identisch** mit dem dokumentierten Wert;
- Writerschema-SHA-256 der tatsächlich serialisierten Bundlebytes:
  `a86ebb519fc2dcdf4936b01d00397e46452ba7bc8c2591e1523e021b1bf22e04`
  — **bitgenau identisch** mit Manifest, Slice-03-Tabelle und Slice-04-Protokoll.

Damit tragen die dokumentierten Digests ihre Behauptung: Sie sind nicht von Hand
eingetragen, sondern aus dem kanonischen Bundlebuilder reproduzierbar.

**Die tiefe Kompositionsform ist in den echten Bytes enthalten.** In
`bundle.provider_response_schema_json` — also exakt den an `--json-schema`
übergebenen Bytes, nicht in einem Nachbau — enthält
`bound_slice_convergence_approved.properties.status_changes.items.oneOf` genau
eine Option mit `finding_id: const C-01` und `status: const CLOSED`, bei
`minItems == maxItems == 1`. Das ist die zuvor nie übertragene Ebene: Die
Capability-Sonde hat `nested_one_of` nur als `{"result": {"oneOf": [obj, obj]}}`
gemessen, und die Canaries für Plan und Finalreview liefen ohne
`previous_findings`. Die Gegenkontrolle bestätigt zusätzlich, dass ein
Konvergenzkontext ohne offene eigene Findings die Dispositionsform nicht
erfindet (`maxItems == 0`, kein `oneOf`).

`tests/test_native_contract_probe.py` bindet dieselbe Kette regressionsfest:
Bundle-Digest gegen `response_contract.schema_sha256`, gegen den
Manifest-Evidenzeintrag und gegen dessen `request_id`. Eine handgeänderte
Manifestzeile würde diesen Test rot färben — das ist stärker als die Bindung
der übrigen sechs Canaryzeilen.

**Manifest**: genau sieben `live_canary`-Belege und genau ein
`request_bound`-Beleg, dieser für `claude/initial_slice`. Verifiziert.

**Historischer Bestand**: `tests/fixtures/native-contract-corpus/corpus.jsonl`
ist gegenüber `eb38a31` unverändert; im Manifest sind alle `response_sha256`
unverändert, und ausschließlich die drei `writer_schema_sha256` der
`request_bound`-Fixtures wurden angepasst. Alle drei reproduzieren aus der
heutigen Projektion, und alle drei durchlaufen weiterhin Writerschema **vor**
Domänenkonvertierung mit `approval=True`. Die Änderung ist die zwingende Folge
der CR-01-Verengung, keine inhaltliche Umdeutung.

### 4. Dokumentation

Roadmap, Slice-03-Nachtrag, Manifest und Slice-04-Protokoll sind
widerspruchsfrei: sieben Live-Canaries, ein `request_bound`-Beleg für den
initialen Slice, dieselben drei Digests an allen Stellen. Der Slice-03-Bericht
schreibt mein historisches Urteil nicht um, sondern ergänzt einen ausdrücklich
gekennzeichneten Nachtrag — die Auditspur bleibt intakt. Die
`request_bound`-Alternative wird nirgends rückwirkend entwertet; Roadmap und
Nachtrag sagen ausdrücklich, dass der alte ursprüngliche Providerdigest kein
nachträglicher Implementierungsdefekt ist. `CR-03` steht unverändert unter den
Nichtzielen und bleibt eine separate, nicht blockierende
Dokumentationsabweichung.

### Akzeptanzkriterien

Alle zehn sind nachweislich erfüllt: (1) und (2) durch die 252-Kandidaten-Suche
und die vier Gegenbeispiele auf beiden Schichten; (3) durch die legitimen
Transitionen und vier weiterhin erzeugte Writerformen; (4) durch die zwei
Überpermissivitätsklassen bei unverändertem Ausnahmeregister; (5) durch den
Strukturnachweis auf den serialisierten Bundlebytes; (6) durch die
reproduzierten Canary-Digests und die 7/1-Evidenzverteilung; (7) durch
unveränderte `corpus.jsonl` und Antwortdigests bei deterministisch
nachgezogenen Writerdigests; (8) durch die konsistente Dokumentation; (9) durch
die attestierten Läufe; (10) durch dieses Review.

### Neue BLOCKER

Keine.

REVIEWER: claude

FINDING_STATUS: C-29 | CLOSED | Reviewer-eigene ID für den im Codex-Selbstreview als `CR-01` gemeldeten Defekt: geschlossene reviewer-eigene Findings waren im Ablehnungsast als Ziel von `status_changes` und `reclassifications` darstellbar und wurden lokal akzeptiert, weil `_validate_response_events()` nur Existenz und Eigentum prüfte und `apply_reviewer_finding_update()` keinen Übergangswächter besitzt; in der schweren Variante konnte ein Denial seinen erforderlichen offenen Blocker durch Wiederöffnen eines geschlossenen Blockers erschleichen und damit `claude-resulting-blocker-state` umgehen. Geschlossen: `own_ids` ist entfernt, alle Status- und Reklassifizierungsäste samt Kardinalitätsgrenzen leiten sich aus `own_open_ids` ab, und die lokale zweite Schutzschicht weist dieselben Dokumente unabhängig vom Writerschema mit dem neuen stabilen Code `finding-reference-not-open` ab. Providerfrei verifiziert: alle vier ursprünglichen Gegenbeispiele scheitern jetzt auf beiden Schichten, die Kontrolle ohne Reopen scheitert weiterhin korrekt an `approval-invalid`, legitime OPEN-Transitionen bleiben möglich, und über 54 gebundene Kontexte mit 252 Writerkandidaten übersteht kein Kandidat mit geschlossenem Ziel das Writerschema. Der neue Code ist hinter einem writer-validen Dokument nicht erreichbar; die Ausnahmeliste bleibt daher korrekt bei 13 Einträgen.

REVIEW_EVIDENCE: Unveränderlichkeit geschlossener eigener Findings über alle vier Claude-Writerformen und beide Entscheidungsäste, gemessen über 54 gebundene Kontexte und 252 Writerkandidaten; Ableitung sämtlicher Kardinalitätsgrenzen aus offenen Findings; Fail-closed-Verhalten der lokalen zweiten Schutzschicht bei umgangener Schemavalidierung; Fortbestand legitimer OPEN-Transitionen und aller vier Writerformen; Nichterreichbarkeit von `finding-reference-not-open` hinter writer-validen Dokumenten und dadurch begründete Unveränderlichkeit des Ausnahmeregisters; Echtheit der Überpermissivitätskontrollen als Beweis gegen stille Annahme statt als Fehlercodevergleich; planmäßige Zulässigkeit der `request_bound`-Alternative und korrekte Verwerfung des ursprünglichen CR-02; Reproduzierbarkeit von Request-ID und Writerschemadigest des Konvergenz-Canaries aus dem kanonischen Bundlebuilder; Vorhandensein von `status_changes.items.oneOf` in den tatsächlich serialisierten Providerschemabytes mit exakt den gebundenen offenen IDs; Unverändertheit von `corpus.jsonl` und aller Antwortdigests bei deterministisch nachgezogenen Writerdigests; Writer-vor-Domäne-Reihenfolge der drei historischen Paare; Evidenzverteilung 7 `live_canary` zu 1 `request_bound`; Widerspruchsfreiheit von Roadmap, Slice-03-Nachtrag, Manifest und Slice-04-Protokoll | Drei nicht ausführbare Restrisiken: (1) Die Abwehr geschlossener IDs ruht bei leerer `own_open_ids`-Menge allein auf `maxItems=0`, weil `_bound_review_definition()` das `finding_id`-Property nur bei nichtleerer ID-Liste verengt; heute ist das durch die Kardinalität nachweislich unerreichbar, eine spätere Lockerung der Grenze ohne gleichzeitige Verengung des Enums würde geschlossene und fremde IDs jedoch wieder darstellbar machen. (2) Der Antwortdigest `f6a2eff2…` des Konvergenz-Canaries bleibt wie bei allen sieben Live-Canaries mangels gespeicherter Bytes lokal unverifizierbar; verifizierbar sind nur Request-ID und Schemadigest. (3) Der `.orchestrator`-stämmige Teil der Corpusinventur bleibt nicht aus einem frischen Klon ableitbar und unverändert selbstreferenziell gezählt | Jemand hebt eine Kardinalitätsgrenze für `status_changes` oder `reclassifications` an — etwa um mehrfache Dispositionen zuzulassen — ohne die zugehörige ID-Menge weiterhin aus `own_open_ids` abzuleiten; die Enum-Verengung entfällt bei leerer offener Menge still, die Überpermissivitätskontrollen prüfen nur die beiden heute bekannten Dokumentklassen, und geschlossene Findings werden erneut veränderbar, ohne dass ein Test rot wird

PRE_MORTEM: In drei Monaten ist die wahrscheinlichste Ursache, dass die Findingzustandsprüfung als reine Writerschemaeigenschaft missverstanden wird. Die lokale Prüfung in `_validate_response_events()` ist heute die einzige Schicht, die den Vorzustand kennt; sie ist bewusst nicht als Providerausnahme registriert, weil sie hinter einem writer-validen Dokument unerreichbar ist. Wer später die Registermenge als vollständige Landkarte der lokalen Ablehnungsfläche liest, wird `finding-reference-not-open` dort nicht finden und die Prüfung für redundant halten — insbesondere wenn eine Refaktorierung die Enum-Verengung und die Kardinalitätsgrenze auseinanderzieht. Entfernt oder umgeht jemand die lokale Schicht, kehrt der Bypass von `claude-resulting-blocker-state` zurück, und er wird diesmal nicht auffallen, weil die beiden Überpermissivitätskontrollen genau die zwei heute bekannten Dokumentklassen prüfen und nicht die Klasse „Update eines nicht offenen Findings" generisch aufspannen. Die zweite, leisere Variante: der Konvergenz-Canary bleibt der einzige reale Beleg für `status_changes.items.oneOf`, und eine künftige Claude-CLI-Version verengt ihr Schemasubset an genau dieser Array-Item-Komposition, ohne dass die Capability-Sonde sie je als eigenes Feature führt.

SLICE_APPROVAL: 04 | YES

STATUS: DONE
