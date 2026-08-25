# Codex-Gesamtreview – Native Agent Contract Closure

Datum: 24. August 2026

Ergebnis: **nicht abschlussfähig; zwei technische Blocker offen**

Dieses Dokument ist ein adversariales Codex-Selbstreview und ausdrücklich
keine Freigabe der eigenen Implementierung. Nach `AGENTS.md` kann nur Claude
das branchweite fachliche Finalreview erteilen.

## 1. Eingefrorener Reviewgegenstand

- Arbeitsplan: `docs/internal/native-agent-contract-closure-arbeitsplan.md`
- Paketbasis: `c410eef95164d10b1ff0d97d2a9593ca5af18324`
- Commitgrenze nach Slice 02:
  `7ba867dd246ccb679c3f20ddfc747cf408862c14`
- Slice-03-Gegenstand: sämtliche aktuellen versionierten und unversionierten
  Änderungen aus dem exakten Slice-03-Pfad
- einbezogen: alle drei Slice-Reviewprotokolle, Capability- und
  Ausnahmeregister, Request-/Resultatverträge, Adapter-/Runtimegrenze,
  Corpusmanifest, Differentialmatrix, Probe-/Canarypfad und Roadmap
- ausgeschlossen: dieses Selbstreview und ein parallel erzeugtes
  Claude-Finalreview, damit beide Prüfungen unabhängig bleiben

`docs/internal/OPTION_CLAUDE_ONLY_REVIEW_POLICY.md` liegt zwar im Commitdiff
seit der Paketbasis, ist laut Arbeitsplan aber ausdrücklich kein Bestandteil
von Contract Closure. Es wurde als vorbestehender, separat autorisierter
Dokumentationspunkt nicht fachlich mitgeprüft.

Der aktuelle Git-Branch heißt `master`, obwohl der Arbeitsplan
`feature/native-agent-contract-closure` als Ziel nennt. Dieser Unterschied
ändert die technischen Befunde nicht, muss aber vor einem späteren
Merge-/Abschlusskommando bewusst berücksichtigt werden.

## 2. Prüfdimensionen

Geprüft wurden:

- Kontextbindung und Geschlossenheit der vier Codex- und vier
  Claude-Writerschemaformen;
- Bytegleichheit der Schemaquelle zwischen Requestbuilder, Bundle,
  Transportmessung und Adapter;
- lokale zweite Schutzschicht hinter dem Providerschema;
- Findingeigentum, offene/geschlossene Zustände, Dispositionsvollständigkeit,
  Observationpolicy, Freigabe-, Denial- und Stopäste;
- Capability- und Transportprofilbindung einschließlich CLI-Versionen;
- registrierte, nicht schema-ausdrückbare Restinvarianten;
- historisches Reader-Corpus, `request_bound`-Klassifizierung und
  Writerformnachweise;
- Codex-Canary-Ausführungsgrenze sowie Schutz von Arbeitsbaum und ignorierten
  Workflowstores;
- Slice-Findingverlauf bis einschließlich der geschlossenen Claude-Findings
  `C-26` bis `C-28`;
- Konsistenz von Arbeitsplan, Reviewprotokollen und Roadmap.

Es wurde vereinbarungsgemäß kein weiterer Test- oder Canarylauf gestartet.
Das Review verwendet die bereits dokumentierte vollständige Attestierung
`1268 passed in 194.82s`, die fokussierten Slice-Nachweise und die drei
positiven Claude-Sliceentscheidungen als vorhandene Evidenz, nicht als Ersatz
für die folgende Code- und Vertragsprüfung.

## 3. Blocker CR-01 – Geschlossene Claude-Findings sind im Denial-Ast erneut änderbar

Schweregrad: **BLOCKER**

Der Arbeitsplan verlangt in Abschnitt 4.3.3 ausdrücklich, dass
Statusänderungen und Reklassifizierungen nur offene reviewer-eigene Findings
referenzieren und geschlossene Finding-IDs nicht angeboten werden.

Die Projektion bildet jedoch zunächst `own_ids` aus allen eigenen Findings und
verwendet genau diese Menge für `bound_status_change` und
`bound_reclassification` (`src/native_review_contract.py:361-370` und
`:427-434`). Der Ablehnungsast übernimmt diese Definitionen mit einer
Kardinalität bis `len(own_ids)` (`:533-550`). Nur der Freigabeast verengt seine
Einzeloptionen später auf `own_open`.

Die lokale zweite Schutzschicht schließt die Lücke nicht. In
`_validate_response_events()` wird für Status- und Klassenänderungen nur
Existenz und Eigentum geprüft (`src/native_review_contract.py:950-976`), nicht
der bisherige Status. `apply_reviewer_finding_update()` erlaubt anschließend
auch den Übergang eines bereits `CLOSED` Finding zurück nach `OPEN`.

Damit ist beispielsweise bei einem Kontext mit `C-01 CLOSED` und einem
weiterhin offenen eigenen Blocker `C-02` eine verweigernde Antwort mit
`status_changes=[C-01 -> OPEN]` sowohl im konkreten Writerschema darstellbar
als auch lokal akzeptierbar. Sie verändert einen abgeschlossenen Findingzustand
ohne neue Finding-ID. Dieser Pfad ist keine registrierte, technisch nicht
schema-ausdrückbare Ausnahme, sondern eine unmittelbar mit `own_open_ids`
schließbare Vertragslücke.

Akzeptanzkriterien:

1. Denial-, Approval- und Reklassifizierungsäste bieten ausschließlich die
   jeweils zulässigen offenen reviewer-eigenen Finding-IDs an.
2. Die lokale zweite Schutzschicht weist jede Status- oder
   Reklassifizierungsänderung eines bereits geschlossenen Findings mit einem
   stabilen Diagnosecode fail-closed ab.
3. Eine Regression verwendet mindestens einen geschlossenen und einen offenen
   eigenen Findingdatensatz und beweist, dass ein Denial den geschlossenen
   Datensatz weder wieder öffnen noch reklassifizieren kann.
4. Die Differentialfläche enthält den Fall „Update eines nicht offenen
   Findings“ ausdrücklich und benötigt dafür keinen neuen Ausnahmeeintrag.

## 4. Blocker CR-02 – Zwei Writerformnachweise belegen nicht den aktuellen Providervertrag

Schweregrad: **BLOCKER**

Das Manifest weist den initialen Claude-Slice und die Claude-Konvergenz als
durch historische `request_bound`-Fixtures belegt aus:

- initialer Slice: aktueller Writerschemadigest
  `fe6fc2ba4b16422c883905be4946baa572256c2d5ed385871fc53ac05999a060`;
- Konvergenz: aktueller Writerschemadigest
  `9a3db92e0aa6de8e893aae9cfb6888246e2976c583072218691be7c43fc2946c`.

Die originalen Requestdokumente aller drei `request_bound`-Fixtures binden
hingegen den älteren Provider-Schemadigest
`31510b2d44b4d277a0dec7fda9b3baed4144a34ff5d5e67cbeceb65045acedd4`.
Die historischen Antworten wurden somit nicht unter den heute behaupteten
request-spezifischen Writerschemas erzeugt.

`test_native_contract_corpus_reader_writer_and_domain_results()` rekonstruiert
aus dem alten Requestkontext das **heutige** Writerschema, prüft dessen Digest
gegen das Manifest und validiert die alte Antwort retrospektiv dagegen
(`tests/test_native_contract_corpus.py:200-221`). Das ist ein wertvoller
Kompatibilitätsnachweis, aber kein Nachweis, dass Claude das aktuelle konkrete
Schema als Transportvertrag akzeptiert und unter ihm eine gebundene Antwort
erzeugt hat. `_validate_writer_form_evidence()` prüft ebenfalls nur die
Manifestklassifizierung und den dort eingetragenen heutigen Digest
(`:224-254`); die abweichende `response_contract.schema_sha256` des originalen
Requests wird für die Writerformevidenz nicht ausgewertet.

Die vier Codex-Formen sowie Claude Plan und Final besitzen aktuelle
Live-Canaries. Ausgerechnet initialer Slice und Konvergenz – die komplexesten
Claude-Formen mit Findingfenstern und Dispositionsästen – stützen ihre
Transportaussage damit ausschließlich auf Antworten, die unter einem anderen
Schema erzeugt wurden. Die vorhandenen Fixtures belegen Readerkompatibilität
und Domänenparität, aber nicht die in Abschnitt 4.5.5 verlangte konkrete
Providerakzeptanz.

Akzeptanzkriterien:

1. Für initialen Claude-Slice und Claude-Konvergenz wird jeweils ein direkter
   Canary mit den aktuellen kanonischen Writerschemabytes ausgeführt und lokal
   gebunden, oder ein anderer Nachweis zeigt bytegleich, dass der ursprüngliche
   Providerrequest exakt diesen Writerschemadigest trug.
2. Historische Paare mit abweichendem ursprünglichem
   `response_contract.schema_sha256` bleiben `request_bound` für
   Kompatibilitäts- und Domänenaussagen, zählen aber nicht allein als
   Transportnachweis einer neuen Writerform.
3. Der Manifestvalidator vergleicht bei einem als Transportnachweis genutzten
   `request_bound`-Fixture zusätzlich den ursprünglichen
   `response_contract.schema_sha256` mit dem behaupteten Writerformdigest.
4. Die Roadmap- und Reviewaussage unterscheidet eindeutig zwischen
   retrospektiver Writerkompatibilität und realer Providerakzeptanz des
   aktuellen Schemas.

## 5. Dokumentationskorrektur CR-03 – C-25 ist im normativen Plan noch widersprüchlich

Schweregrad: **abschlussrelevante Dokumentationsabweichung**

Claude-Observation `C-25` wurde in Slice 01 sachlich nachvollziehbar akzeptiert:
`--max-budget-usd` ist ein Kostenwächter und wird in
`normalize_transport_profile()` bewusst verworfen. Capability-Tabelle und
Tests schreiben dieses Verhalten fest.

Der weiterhin normative Abschnitt 4.1.8 des Arbeitsplans zählt das Flag aber
noch immer ausdrücklich zu den abschließenden Claude-`semantic_flags`
(`docs/internal/native-agent-contract-closure-arbeitsplan.md:227-236`). Damit
behaupten Plan und Implementierung verschiedene Driftsemantiken.

Akzeptanzkriterium: Abschnitt 4.1.8 wird an die akzeptierte C-25-Disposition
angepasst und nennt `--max-budget-usd` ausdrücklich als profilfremden
Kostenparameter. Die historische Observation und ihre Disposition bleiben im
Reviewprotokoll unverändert erhalten.

## 6. Positiv bestätigte Architekturteile

Unabhängig von den Blockern sind folgende tragende Teile im geprüften Stand
konsistent:

- Die allgemeinen Resultat-v1-Schemata bleiben Reader-Baselines; die
  Writerschemas entstehen deterministisch aus defensiven Kopien.
- Codex erhält nur den gebundenen Resultattyp plus Stopast; offene
  Finding-Dispositionen, Testdateikardinalität und kontextabhängige
  Readiness werden weitgehend vor der Generierung geschlossen.
- Claude erhält getrennte Plan-, Initial-Slice-, Konvergenz- und Finaläste;
  Attestierung, Testfreigabe, Pre-Mortem, Evidenz, Observationpolicy und
  Anchorfähigkeit werden requestgebunden projiziert.
- Requestbuilder, Bundle und Adapter verwenden dieselben kanonischen
  Writerschemabytes; der Schemadigest ist Teil der Requestbindung.
- Capability- und Transportprofilabweichungen werden vor dem Providerstart
  fail-closed behandelt. Nicht klassifizierte Kommandoargumente werden nicht
  still entfernt.
- Die Codex-Canarygrenze erzwingt `read-only`, separates CWD und separates
  Evidenzassetziel. Der Slice-03-Korrekturstand schützt zusätzlich die von Git
  nicht sichtbaren Workflowstores rekursiv.
- Das Corpus enthält 14 bytegebundene Antworten, davon drei originale
  Request-/Responsepaare. Die Archivseite ist aus dem Arbeitsbaum abgeleitet,
  und die drei von Claude gemeldeten Slice-03-Blocker `C-26` bis `C-28` sind
  auf ihren Akzeptanzpfaden geschlossen.

## 7. Abschluss- und Reviewgrenzen

Der ursprünglich für Claude formulierte Gesamt-Reviewprompt verwendete
`7ba867dd246ccb679c3f20ddfc747cf408862c14` als Basis. Dieser Commit enthält
bereits Slice 01 und Slice 02; ein Review gegen diese Basis umfasst daher nur
Slice 03. Für ein echtes unabhängiges Gesamtpaketreview muss Claude den
Gegenstand erneut ab
`c410eef95164d10b1ff0d97d2a9593ca5af18324` prüfen und dabei alle aktuellen
Slice-03-Dateien einbeziehen. Ein Ergebnis auf Basis `7ba867d…` kann weiterhin
als Slice-03-Zweitreview dienen, aber nicht als branchweite Finalfreigabe.

Vor Abschluss sind daher mindestens erforderlich:

1. CR-01 implementieren und lokal absichern;
2. CR-02 durch echte aktuelle Transportnachweise oder eine gleich starke
   Digestbindung schließen;
3. CR-03 dokumentarisch bereinigen;
4. den korrigierten Gesamtstand vollständig validieren;
5. Claude mit der korrekten Paketbasis ein unabhängiges Finalreview erstellen
   lassen.

## 8. Selbstreviewurteil

Die Contract-Closure-Architektur ist substanziell weiter als der vorherige
allgemeine JSON-Vertrag und die drei Slices schließen einen großen Teil der
beobachteten Nachlaufabbrüche. Der jetzige Stand erfüllt die eigene
Abschlussbehauptung dennoch noch nicht: CR-01 ist eine konkrete
Findingzustandslücke im Writer **und** in der lokalen Schutzschicht; CR-02 lässt
zwei aktuelle Claude-Writerschemas ohne echten Providertransportnachweis.

CODEX_SELF_REVIEW_RESULT: CHANGES_REQUIRED

STATUS: DONE

---

# Erneutes adversariales Codex-Gesamtreview nach Slice 04

Datum: 24. August 2026

Reviewer: Codex

Modus: manuelles, providerfreies Gesamtreview außerhalb von `run_task`

## 1. Gegenstand und Prüfgrenze

Geprüft wurde das vollständige Arbeitspaket zwischen:

- Paketbasis: `c410eef95164d10b1ff0d97d2a9593ca5af18324`
- aktuellem Abschlussstand: `286f5b2c7d72ff014a2bcdc75943b7f849b64ff9`

Einbezogen wurden der vollständige Diff, der normative Arbeitsplan, alle vier
Sliceprotokolle, Capability- und Ausnahmeregister, Writerschema-Projektionen,
Requestbundles, Adapter- und Runtimegrenzen, Record-Ahead-Recovery,
Corpus-/Differentialnachweise, Canaryimplementierung und Roadmap. Das frühere
Codex-Selbstreview und Claudes Slice-04-Review dienten nur als historische
Befundquellen; ihre Aussagen wurden am aktuellen Code erneut geprüft.

Produktcode und Tests wurden in diesem Review nicht verändert. Die
vollständige Testsuite und externe Provider-Canaries wurden vereinbarungsgemäß
nicht wiederholt. Ausgeführt wurden ausschließlich kleine providerfreie
Gegenproben sowie genau der durch eine Gegenprobe bereits als rot erwiesene
Einzeltest. `git diff --check c410eef..286f5b2` ist sauber.

## 2. Disposition der bisherigen Codex-Befunde

### CR-01 – CLOSED

Slice 04 schließt die Veränderbarkeit geschlossener reviewer-eigener Findings
auf beiden Schutzschichten. Writerschema und Kardinalitäten leiten ihre Ziele
nun aus `own_open_ids` ab; die lokale Domäne weist geschlossene Ziele stabil
mit `finding-reference-not-open` ab. Claudes reviewer-eigene Entsprechung
`C-29` ist im Slice-04-Review nachvollziehbar geschlossen.

### CR-02 – CLOSED als unzutreffende ursprüngliche Forderung

Der freigegebene Plan lässt einen gegen die aus dem historischen
Originalrequest rekonstruierten heutigen Writer validierten
`request_bound`-Nachweis ausdrücklich zu. Die frühere Forderung, ein
historischer Providerrequest müsse bereits den erst später eingeführten
Writerschemadigest getragen haben, war daher keine zulässige
Implementierungsanforderung.

Der darunterliegende echte Transportpunkt – Claudes tief verschachteltes
`status_changes.items.oneOf` in der Konvergenzform – wurde durch den
Slice-04-Canary praktisch belegt. Der aktuelle Builder erzeugt weiterhin den
dokumentierten Writerschemadigest
`a86ebb519fc2dcdf4936b01d00397e46452ba7bc8c2591e1523e021b1bf22e04`
und genau eine gebundene `C-01 -> CLOSED`-Option. Die Dauerhaftigkeit der dazu
persistierten Request-ID ist jedoch unabhängig davon defekt; das ist der neue
Blocker CR-06.

### CR-03 – OPEN

Die normative Planbeschreibung ist weiterhin nicht an die akzeptierte
Claude-Observation `C-25` angepasst. Abschnitt 4.1.8 führt
`--max-budget-usd=<configured-value>` noch als Claude-`semantic_flags`-Element
(`native-agent-contract-closure-arbeitsplan.md:227-236`), während
`normalize_transport_profile()` den Kostenwächter ausdrücklich verwirft
(`src/native_provider_schema.py:257-259`) und Slice 01 genau dieses Verhalten
als Lösung von `C-25` dokumentiert. Plan, Implementierung und Testaussage sind
damit weiterhin widersprüchlich. Die Korrektur ist dokumentarisch, aber vor
einer behaupteten vollständigen Planerfüllung erforderlich.

## 3. Blocker CR-04 – Der echte Writervertrag wird live und bei Recovery nicht lokal validiert

Schweregrad: **BLOCKER**

Der Arbeitsplan verlangt, dass Request, Bundle, Providerinput und lokale
Selbstprüfung dasselbe kanonische Writerschema verwenden
(`native-agent-contract-closure-arbeitsplan.md:844-852`). Der produktive Pfad
setzt das nicht um:

- `run_native_review_agent()` validiert die extrahierte Antwort nur gegen das
  allgemeine Reader-Schema und danach gegen die lokale Domäne
  (`src/agent_runtime.py:1297-1308`). Eine Validierung von
  `{"result": document}` gegen `bundle.provider_response_schema` fehlt.
- `run_native_codex_agent()` besitzt dieselbe Lücke
  (`src/agent_runtime.py:1465-1479`).
- Die Record-Ahead-Recovery für Codex (`src/orchestrator.py:1041-1048`), die
  Review-Recovery (`:1390-1396`) und die Pre-Policy-Spiegelung eines bereits
  gespeicherten Reviews (`:1600-1611`) wiederholen nur Reader-/Domänenprüfung,
  nicht die exakte Writerschemaprüfung.

Damit sind die request-spezifischen Writerregeln nur eine Erwartung an das
Providerprogramm. Ein Providerfehler, ein ungeprüft übernommenes Rohresultat
oder eine Recovery aus bereits persistierten Bytes kann eine Antwort
akzeptieren, die der gebundene Writer ausdrücklich verbietet.

Der Pfad ist nicht nur theoretisch. Der Finalreview-Writer verbietet korrekt
jede Reklassifizierung zu `OBSERVATION`, unabhängig von
`allow_new_observations` (`src/native_review_contract.py:397-445`). Die lokale
Prüfung in `_validate_decision()` verknüpft die Finalregel hingegen fehlerhaft
mit diesem Flag (`:1133-1146`). In einem realistisch konstruierbaren
Finalkontext mit `allow_new_observations=True`, einem offenen eigenen Blocker
`C-01` und einem zweiten offenen Blocker `C-02` wurde providerfrei
nachgewiesen:

```text
writer_rejected=True
local_accepted=True
resulting_findings=[C-01 OBSERVATION OPEN, C-02 BLOCKER OPEN]
```

Der Denial bleibt durch `C-02` formal begründet, während `C-01` entgegen der
Finalpolicy zu einer Observation umklassifiziert wird. Der Writer erkennt den
Angriff; Livepfad und Recovery wenden diesen Writer aber nicht als lokale
Schutzgrenze an.

Akzeptanzkriterien:

1. Eine gemeinsame Hilfsfunktion validiert jede native Claude- und
   Codex-Antwort vor Callback, Persistierung oder Domänenkonvertierung gegen
   exakt die kanonischen Writerschemabytes ihres Bundles; für das innere
   Resultat wird dabei die einzige Transporthülle `{"result": ...}`
   rekonstruiert.
2. Livepfad, Codex-Record-Ahead-Recovery, Claude-Record-Ahead-Recovery und
   Pre-Policy-Recovery verwenden dieselbe Prüfung und liefern bei Abweichung
   einen stabilen Output-/Recoveryfehler, ohne Findings oder State zu ändern.
3. `_validate_decision()` verbietet bei `ApprovalMarker.FINAL` eine neue oder
   neu reklassifizierte Observation unabhängig vom Wert von
   `allow_new_observations`; eine zusätzliche Kontextinvariante darf diese
   zweite Schutzschicht nicht ersetzen.
4. Eine Regression verwendet den oben beschriebenen writer-ungültigen, aber
   bislang reader- und lokal gültigen Final-Denial und beweist die Ablehnung im
   Live- sowie Recoverypfad vor jeder Persistierung.
5. Die entsprechende Codex-Regression beweist ebenfalls, dass eine Antwort,
   die das allgemeine Resultatschema erfüllt, aber die konkrete gebundene
   Ergebnisart oder Dispositionsform verletzt, live und bei Recovery
   fail-closed abgewiesen wird.

## 4. Blocker CR-05 – Requestbytes und gebundener Auswertungskontext sind nicht vollständig gekoppelt

Schweregrad: **BLOCKER**

Beide Bundletypen prüfen, ob die Request-ID zum gespeicherten
`request_digest` passt und ob das Writerschema aus dem gebundenen Kontext
ableitbar ist. Sie beweisen jedoch nicht vollständig, dass genau dieser
Kontext den kanonischen Request erzeugt hat.

Für Claude rechnet `NativeReviewRequestBundle` zwar den Digest der
Requestbytes nach (`src/native_review_request.py:194-202`), vergleicht aber den
im Dokument enthaltenen Lauf-, Work-Unit- und Reviewvertrag nicht mit
`bound_context.context`. Ein Bundle mit dem unveränderten Request für
`run-native-request` und einem gebundenen Kontext für `run-drifted` wurde
akzeptiert, weil beide Kontexte dieselbe Writerschemaform erzeugen.

Für Codex ist die Lücke breiter. `NativeCodexRequestBundle.__post_init__()`
prüft den Inhaltsdigest der kanonischen Requestbytes überhaupt nicht
(`src/native_codex_request.py:173-217`). Providerfrei wurden deshalb alle
folgenden inkonsistenten Bundles erfolgreich konstruiert:

```text
Claude: Request run_id=run-native-request, Bound Context run_id=run-drifted
Codex:  Request run_id=run-1,             Bound Context run_id=run-drifted
Codex:  kanonische Bytes auf run_id=forged-run geändert,
        ursprüngliche request_id und ursprünglicher Bound Context beibehalten
Codex:  content_ref im Requestmanifest vorhanden,
        evidence_assets im Bundle vollständig entfernt
```

Die letzte Variante ist möglich, weil das Codex-Bundle – anders als das
Claude-Bundle – die tatsächlichen `evidence_assets` nicht gegen das gebundene
Manifest prüft. Der Adapter materialisiert anschließend ausschließlich die
ungeprüfte Bundleliste (`src/agent_adapters.py:488-499`). Request-ID,
Providerauftrag, Evidenzbytes und lokaler Auswertungskontext können damit
auseinanderfallen, obwohl der Bundlekonstruktor Erfolg meldet.

Akzeptanzkriterien:

1. Beide Bundlekonstruktoren rechnen den Requestdigest aus den kanonischen
   Requestbytes nach und vergleichen jedes aus dem Domänenkontext abgeleitete
   Requestfeld mit einer einzigen kanonischen Kontextprojektion.
2. Änderungen an `run_id`, `work_unit_id`, Operation, Fingerprint,
   Requestart/Approvalmarker, Runde, Slice, Vertrag, offenen Findings,
   Attestierung, Testpolicy, Observationpolicy oder Anchors werden selbst dann
   abgewiesen, wenn sie zufällig dasselbe Writerschema erzeugen.
3. Das Codex-Bundle validiert Inlineevidenz und `content_ref`-Assets mit
   derselben Vollständigkeits-, Pfad-, Bytezahl- und Digestbindung wie das
   Claude-Bundle; fehlende, zusätzliche, doppelte oder vertauschte Assets
   scheitern vor dem Adapter.
4. Negative Tests konstruieren Bundles direkt – nicht nur über die stets
   konsistenten Builder – und reproduzieren alle vier Gegenbeispiele.
5. Repair- und Recoverykonstruktionen verwenden dieselbe vollständige
   Bundleinvariante und können keinen synthetisch passenden Bound Context zu
   fremden Requestbytes einsetzen.

## 5. Blocker CR-06 – Der Abschlusscommit invalidiert den eigenen Canarynachweis

Schweregrad: **BLOCKER**

Der Slice-04-Canary wurde erfolgreich mit der Request-ID
`native-review-request-58e98da53bd078faa2e2e666d9a1549a51435c683709040dab0aa2d26a11d236`
ausgeführt und so im Manifest persistiert. `_claude_canary_bundle()` setzt als
`base_commit` jedoch bei jeder Rekonstruktion den jeweils aktuellen
Repository-`HEAD` ein (`scripts/native_contract_probe.py:450-456`). Der Canary
lief, als Slice 04 noch uncommittet auf `eb38a31` lag. Der anschließende
Abschlusscommit `286f5b2` änderte deshalb den Requestdigest, obwohl
Writerschemabytes und fachlicher Repräsentativkontext unverändert blieben.

Am aktuellen Abschlussstand ergibt der kanonische Builder:

```text
persistierte Request-ID:
native-review-request-58e98da53bd078faa2e2e666d9a1549a51435c683709040dab0aa2d26a11d236

aktuell rekonstruierte Request-ID:
native-review-request-6694324a16597fe5be325a6e49bf5ec5f09a4a831e2a0e6f99bbb578f997c3b8

Writerschema-SHA-256 in beiden Fällen:
a86ebb519fc2dcdf4936b01d00397e46452ba7bc8c2591e1523e021b1bf22e04
```

Der dazu bestimmte Einzeltest ist nach dem Commit tatsächlich rot:

```text
tests/test_native_contract_probe.py::
test_convergence_canary_serializes_status_change_items_one_of FAILED
AssertionError: persistierte Request-ID 58e98d… != rekonstruierte 669432…
```

Damit ist die attestierte Aussage `1273 passed` nur für den uncommitteten
Vor-Commit-Zustand gültig. Der aktuelle Commit erfüllt Gesamt-Abnahmekriterium
12 nicht und reproduziert entgegen Slice-04-Protokoll und Finalreviewauftrag
die gebundene Canary-Request-ID nicht.

Akzeptanzkriterien:

1. Jeder Live-Canary persistiert den vollständigen für seine Request-ID
   maßgeblichen Repräsentativkontext, mindestens einschließlich des tatsächlich
   verwendeten `base_commit`, oder das vollständige kanonische Requestdokument.
2. Die providerfreie Rekonstruktion verwendet diesen eingefrorenen Wert statt
   den jeweils aktuellen `HEAD`; spätere Commits verändern keinen historischen
   Canarynachweis.
3. Der Live-Canarybuilder und der Manifesttest teilen dieselbe explizite
   Kontextfunktion. Ein neuer Providerlauf darf einen neuen Nachweis ergänzen,
   aber keinen alten still umdeuten.
4. Der aktuell rote Einzeltest wird gegen den unveränderten historischen
   Nachweis grün und bleibt nach einem weiteren leeren oder dokumentarischen
   Commit grün.
5. Nach allen Korrekturen wird die vollständige Repositorymatrix auf dem
   tatsächlich zu reviewenden Commit erneut ausgeführt und ihre Attestierung
   an genau diesen Stand gebunden.

## 6. Positiv bestätigte Teile und verbleibende Reviewevidenz

Trotz der Blocker sind wesentliche Teile des Pakets konsistent:

- Die beiden allgemeinen Resultat-v1-Dateien bleiben historische
  Reader-Baselines; die Writerprojektionen arbeiten auf defensiven Kopien.
- Die vier Codex- und vier Claude-Writerformen sind im Manifest vollständig
  und derzeit im Verhältnis sieben `live_canary` zu einem `request_bound`
  belegt.
- Das Corpus enthält 14 digestgebundene Fixtures. Die Archivseite wird aus dem
  versionierten Arbeitsbaum inventarisiert; alle drei historischen
  Request-/Responsepaare werden Writer vor Domäne geprüft.
- Slice 04 schützt offene und geschlossene Findingzustände wesentlich besser;
  ein Denial kann seinen Pflichtblocker nicht mehr durch Wiederöffnen eines
  abgeschlossenen Findings erzeugen.
- Das serialisierte Claude-Konvergenzschema enthält
  `status_changes.items.oneOf` mit exakt der gebundenen offenen Finding-ID.
  Der Schema-Digest ist am aktuellen Commit reproduzierbar; nur die
  Request-ID-Evidenz ist wegen CR-06 nicht dauerhaft rekonstruiert.
- Capability- und Ausnahmeregister sind typisiert und fail-closed an Version
  und normalisiertes Profil gebunden. Das Ausnahmeregister wurde für Slice 04
  nicht unnötig erweitert.
- Repository- und Workflowstore-Abdrücke erfassen reguläre Dateien,
  Symlinkziele und fehlende Wurzeln und werden um Provider-Canaries herum
  fail-closed verglichen.

Das größte verbleibende Restrisiko außerhalb der ausführbaren Blocker ist die
bereits von Claude dokumentierte, aus einem frischen Klon nicht unabhängig
rekonstruierbare `.orchestrator`-Teilinventur. Sie wird im aktuellen Manifest
selbstreferenziell gezählt. Das ist kein Ersatz für CR-04 bis CR-06, aber eine
wichtige Grenze jeder Aussage, das lokale historische Corpus sei vollständig.

## 7. Pre-Mortem

Die wahrscheinlichste Fehlerursache in drei Monaten wäre eine falsche
Gleichsetzung von „der Provider erhielt ein enges Schema“ mit „das System hat
dieses Schema als Autorität durchgesetzt“. Heute kann die lokale Runtime eine
breitere Antwort akzeptieren, ein Bundle kann Request und Auswertungskontext
auseinanderfallen lassen, und ein späterer Commit kann einen Canarynachweis
rückwirkend rot machen. Solange diese drei Grenzen getrennt implementiert und
getestet bleiben, können grüne Providerläufe und historische Digests ein
Sicherheitsniveau suggerieren, das Recovery und lokale Verarbeitung nicht
tatsächlich besitzen.

## 8. Gesamturteil

Der aktuelle Stand `286f5b2` ist **nicht abschluss- oder mergefähig**. CR-01
und der sachlich engere Teil von CR-02 sind geschlossen, aber CR-03 bleibt
offen. CR-04 und CR-05 sind konkrete Bindungs- und Recoverydefekte; CR-06 macht
zusätzlich den aktuellen Commit nachweislich testrot und entwertet die
Vor-Commit-Gesamttestattestierung für den Abschlussstand.

Erforderliche nächste Korrektur: CR-03 bis CR-06 in einem neuen, eng
definierten Korrekturslice schließen, anschließend fokussiert validieren und
die vollständige Repositorymatrix auf dem finalen Commit erneut attestieren.

CODEX_SELF_REVIEW_RESULT: CHANGES_REQUIRED

STATUS: DONE
