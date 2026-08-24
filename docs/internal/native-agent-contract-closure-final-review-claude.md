REVIEWER: claude

# Branchweites Abschlussreview – Native Agent Contract Closure

Datum: 24. August 2026 · Reviewer: Claude · Modus: read-only, adversarial

## 1. Eingefrorener Prüfgegenstand

Basiscommit des Reviews: `7ba867d`
(`feat(native-contracts): complete contract closure slice 01 and slice 02`),
zugleich aktueller `HEAD`. Der Prüfgegenstand wurde einmalig aus
`git diff 7ba867d` und `git status --short --untracked-files=all` ermittelt und
ist danach eingefroren:

| Datei | Zustand |
|---|---|
| `docs/internal/ORCHESTRATOR_ROADMAP_PHASE_2_PLUS.md` | geändert |
| `docs/internal/native-agent-contract-closure-arbeitsplan.md` | geändert |
| `docs/internal/native-agent-contract-closure-review.md` | geändert |
| `docs/internal/native-agent-contract-closure-slice-03-review.md` | neu |
| `schemas/native-provider-schema-exceptions-v1.json` | geändert |
| `scripts/native_contract_probe.py` | geändert |
| `src/agent_adapters.py` | geändert |
| `src/agent_runtime.py` | geändert |
| `tests/fixtures/native-contract-corpus/corpus.jsonl` | neu |
| `tests/fixtures/native-contract-corpus/manifest.json` | neu |
| `tests/test_agent_adapters.py` | geändert |
| `tests/test_agent_runtime.py` | geändert |
| `tests/test_native_codex_contract.py` | geändert |
| `tests/test_native_contract_corpus.py` | neu |
| `tests/test_native_contract_differential.py` | neu |
| `tests/test_native_contract_probe.py` | neu |

Ausgeschlossen sind ausschließlich die beiden parallel erzeugten
Reviewausgaben `native-agent-contract-closure-final-review-claude.md` und
`native-agent-contract-closure-final-self-review-codex.md`; zum Zeitpunkt der
Ermittlung existierte keine von beiden. Eine später erscheinende
Codex-Reviewdatei gehört nicht zum Prüfgegenstand.

Da `7ba867d` bereits Slice 01 und Slice 02 enthält, habe ich für die
branchweiten Vertragsaussagen zusätzlich den vollständigen Paketstand gegen den
im Arbeitsplan deklarierten Paket-Basiscommit `c410eef` betrachtet: 1.860
hinzugefügte und 117 entfernte Zeilen über zehn Produkt-, Schema- und
Scriptdateien.

## 2. Vorgehen

Keine Tests, keine Provider-Canaries, keine ausführbare Validierung wurden
erneut gestartet. Grundlage sind die attestierten Läufe (`1268 passed`,
fokussiert `8 passed`, `git diff --check` sauber) sowie ein adversariales Code-,
Vertrags-, Corpus-, Failure-Path- und Konsistenzreview. Wo ich eine Behauptung
falsifizieren wollte, habe ich ausschließlich statisch gelesen und
Metadaten verglichen (Digest- und Mengenvergleiche über die Manifest- und
Corpusdateien, Diff- und Pfadanalysen), ohne Produkt- oder Testcode auszuführen.

## 3. Vertragsgeschlossenheit

**Reader- und Writergrenze ist im Code sauber getrennt.** Beide
Resultat-v1-Schemadateien sind über das gesamte Arbeitspaket hinweg
bytegleich unverändert: `git diff c410eef -- schemas/native-agent-codex-result-v1.schema.json
schemas/native-agent-review-result-v1.schema.json` ist leer. Die produktiven
Parser lesen ausschließlich diese Baselines
(`src/native_codex_contract.py:37`, `src/native_review_contract.py:49`); die
request-spezifische Projektion wird nirgends zum Lesen historischer Antworten
verwendet. Damit macht keine neue Writerregel einen historischen Record
rückwirkend ungültig — die tragende Rückwärtskompatibilitätsaussage des Pakets
hält auf Codeebene.

**Die Restinvarianten sind vollständig, typisiert und beidseitig gebunden.**
`schemas/native-provider-schema-exceptions-v1.json` führt 13 Einträge (sieben
Codex, sechs Claude). Beide Provider besitzen eine Mengengleichheitsregression,
die die hinter dem Writerschema tatsächlich erreichbaren Fehlercodes exakt gegen
die Registermenge bindet — `assert registered == encountered`
(`tests/test_native_codex_contract.py:613`) und
`assert encountered == {...registered_exceptions("claude")}`
(`tests/test_native_review_request.py:698`). Beide Richtungen sind erfasst: ein
unregistrierter erreichbarer Code und ein registrierter Code ohne erreichbares
Gegenbeispiel lassen die Matrix rot werden. Ich habe diese Mengengleichheit in
den Slice-Reviews unabhängig über breite Kontextmatrizen nachgerechnet (Codex:
alle vier Requestarten × Testscope × Freigabezustand × Findingzustände; Claude:
288 gebundene Kontexte im Erstreview, 10.200 Antwortvarianten in der
O-05/O-06-Runde) und dabei keinen weiteren erreichbaren Code gefunden.

**Provider-schemavalides JSON kann nicht mehr an einer schema-ausdrückbaren
lokalen Regel scheitern.** Für Resultatart, Findingmenge,
Dispositionskardinalität, Testdateimenge, Readiness, Entscheidungsast,
Observationpolicy, Anchorbindung, Evidenz- und Pre-Mortem-Pflicht ist das in
den Slice-Reviews 01 und 02 positiv nachgewiesen worden. Was lokal verbleibt,
ist ausschließlich: Sortier- und Kontiguitätsordnung generierter Arrays,
Lookaround-Pfadsicherheit, Nichtleere von Texten, Cross-Array-Eindeutigkeit,
der Fold des resultierenden Blockerzustands und die zyklische
Request-ID-Gleichheit. Jede dieser Klassen ist mit Provider, Operation,
Fehlercode, fehlendem Schemafeature, Providerbeleg und benanntem
Regressionstest registriert.

## 4. Codex-Writerschemas

Geprüft über Plan, Implementierung, Korrektur, Finalbericht und Stopast:

- **Transporthülle**: `tuple(document) != ("result",)` mit zusätzlicher
  `isinstance(..., dict)`-Prüfung (`src/agent_adapters.py:561`) — exakt ein
  Top-Level-Feld, fail-closed.
- **Requestgebundene Konstanten**: `ready=false` als `const` bei gebundenen,
  nicht freigegebenen Teständerungen; ausschließlich `stop_result` bei
  `require_slice_plan=False`; Resultatart je Requestart auf genau einen Ast plus
  Stop begrenzt. Alle vier Writerformen besitzen verschiedene kanonische
  Digests.
- **Findingdispositionen**: `enum` über die offenen Finding-IDs plus exakte
  `minItems`/`maxItems`-Kardinalität; fremde, geschlossene, fehlende und
  überzählige Referenzen sind nicht darstellbar. Reihenfolge und Eindeutigkeit
  bleiben registriert lokal (`codex-disposition-order-and-uniqueness`), weil
  Codex 0.147.0 `prefixItems` und Array-`const` nachweislich ablehnt.
- **Testdateibindung und Readiness**: Nach der O-02-Korrektur bildet die
  Projektion bei fehlender Testfreigabe zwei vollständige geschlossene
  Varianten ab. Ich habe die vollständige Matrix aus
  `enforce_expected_test_files`, leerer/gebundener Testerwartung,
  Freigabezustand, `ready` und leerer/nichtleerer Testdateiliste gegen
  `_validate_test_files()` gefahren: null schemavalide-aber-lokal-abgelehnte und
  null lokal-gültige-aber-schemaabgelehnte Fälle.
- **Kompositionsform**: Nach der B-02-Korrektur enthält jede der 28 erzeugbaren
  Writerformen genau eine Kompositionsstelle — das eine `anyOf` unterhalb von
  `result`, dessen Alternativen ausnahmslos reine `$ref` auf geschlossene
  Objektdefinitionen sind. Das ist bitgenau die von der Subset-Sonde positiv
  gemessene Form; keine unsondierte Kompositionstiefe wird verwendet.
- **Pfadregeln und Fehlercodes**: Sortierordnung von `scope_paths`,
  `test_files` und `remediation_paths` sowie die 1-basierte
  Slice-ID-Kontiguität bleiben lokal und sind je Fehlercode registriert und
  regressionsgebunden.

## 5. Claude-Writerschemas

Geprüft über Planreview, initialen Slice, Konvergenzrunde und Finalreview:

- **Astauswahl** erfolgt ausschließlich aus `approval_marker`, `round_number`,
  `allow_new_observations` und `anchor_origin`. Zwei Kontexte, die sich nur im
  freien `operation`-String unterscheiden, liefern bytegleiche Schemata;
  `NativeReviewKind` ist keine Eingabe der Projektion. Der in C-09/C-12
  benannte Diskriminatorfehler ist damit im Code beseitigt, nicht nur im Plan.
- **Attestierungsbindung**: Fehlende, unvollständige oder FAIL-Attestierung und
  nicht freigegebene Teständerungen erzeugen eine Union aus nur `denied` und
  `stop`; ein Freigabeast ist dort nicht darstellbar.
- **Findingpflichten**: Der Freigabeast bindet die Summe aus `status_changes`
  und `reclassifications` über eine `anyOf`-Partition exakt auf die Zahl eigener
  offener Findings; ein eigener offener BLOCKER kann schema-seitig nur `CLOSED`
  disponiert werden; ein Finalreview lässt kein eigenes Finding offen.
- **Observation- und Blockerregeln**: Nach O-05 und B-03 folgen Freigabe- und
  Ablehnungsast derselben `observations_allowed`-Ableitung. Neue Observations
  sind genau dann darstellbar, wenn kein Finalmarker vorliegt und
  `allow_new_observations` gilt — in Freigabe wie in Ablehnung. Konvergenz- und
  Finalrunden bleiben auf `const BLOCKER` beschränkt.
- **Reclassifications**: Nur eigene offene IDs, im positiven Ast nur Zieltyp
  `OBSERVATION`, in Konvergenz und Final ausgeschlossen.
- **Anchors**: Ohne `anchor_origin` erzwingt `maxItems=0`; die Origin stammt
  ausschließlich aus dem Requestkontext.
- **Evidenz und Pre-Mortem**: Freigabe verlangt vollständige `review_evidence`
  und nichtleeres Pre-Mortem; nach O-06 darf eine Ablehnung ein Pre-Mortem
  führen (`null` oder nichtleerer Text), ohne den Freigabeast erreichen zu
  können — die Äste bleiben über `decision` beziehungsweise `result_type` als
  `const` disjunkt.
- **Transporthülle**: `set(structured_output) != {"result"}` mit
  `isinstance(result, dict)` (`src/agent_adapters.py:1107`), zusätzlich
  gebundene `request_id`-Prüfung schon im Adapter (`:228`).

**Resultierende Blockerpflicht**: Eine Ablehnung ohne vorherigen eigenen
Blocker und ausschließlich mit neuen Observations ist writer-schemavalide, wird
aber vom lokalen Fold mit `approval-invalid` fail-closed abgewiesen und ist
vollständig durch die registrierte Restinvariante
`claude-resulting-blocker-state` abgedeckt. Keine unregistrierte Ausnahme.

## 6. Requestbindung und Recovery

**Bytekette geschlossen.** Für beide Provider bildet der Builder die
kanonischen Schemabytes einmal, leitet daraus `response_contract.schema_sha256`
ab und legt beides ins Bundle; die Bundle-Selbstprüfung rekonstruiert das Schema
deterministisch aus dem Bound Context und vergleicht Objektgleichheit,
kanonische Form und Digest; der Adapter serialisiert beziehungsweise übergibt
exakt diese Bytes und misst sie unverändert. Der Claude-Repairrequest übernimmt
`provider_response_schema_json` und `response_contract` des Elternbundles
unverändert.

**Record- und Resumepfade sind vom Paket nicht angefasst.** Der
paketweite Diff gegen `c410eef` berührt ausschließlich
`src/agent_adapters.py`, `src/agent_runtime.py`, `src/native_codex_contract.py`,
`src/native_codex_request.py`, `src/native_provider_schema.py`,
`src/native_review_contract.py` und `src/native_review_request.py`.
`src/workflow.py`, `src/orchestrator.py` und `src/artifact_models.py` sind
unverändert; Raw-first-/Record-ahead-Recovery, Recordautorität, Mirror- und
Resume-Semantik bleiben damit strukturell auf dem bereits freigegebenen Stand.

**Resume bleibt fail-closed.** `recover_pending_native_codex()`
(`src/orchestrator.py:988`) verwendet das Bundle des laufenden Aufrufs, prüft
den Rohantwortdigest gegen den Record, lehnt mehrere Records für dieselbe
logische ID ab und parst gegen `bundle.bound_context`, wobei die
`request_id`-Gleichheit erzwungen wird. Eine Wiederaufnahme, deren persistierte
Antwort zu einem älteren Schemadigest gehört, scheitert damit laut und
diagnostizierbar statt still. State- oder Markdown-Inhalt kann die
Recordautorität nicht umgehen; die Prüfrichtung ist durchgehend Record gegen
Rohbytes.

**Kein Rückfall auf Textparser oder Contract-Repair.** Der native Reviewpfad
diskriminiert über den Ergebnistyp (`src/workflow.py:2629`) und erreicht
`_validate_or_repair_review()` nicht. Die Workflow-Durchstiche ersetzen
`normalize_codex_contract_output`, `validate_codex_response`,
`normalize_review_contract_output` und `validate_review_response` durch
werfende Stubs. Da der textuelle Reparaturpfad erst hinter
`validate_review_response` erreichbar ist, fängt dieselbe Sprengfalle auch
jeden Rückfall auf Contract-Repair ab. Die Absicherung ist ausreichend.

## 7. Corpus

- **Vollständigkeit**: 14 auffindbare erfolgreiche native Rohantworten, 14
  übernommen. Meine unabhängige Neuinventur (acht Codex-Antworten unter
  `.orchestrator/artifacts/*/native-codex-responses/` ohne `*.failure.json`,
  sechs Claude-Antworten unter `docs/internal/archive/**`) ergibt in beide
  Richtungen eine leere Differenz zum Manifest. Alle 14 gespeicherten
  `response_text`-Werte sind bytegleich mit ihrer Quelle und digestkorrekt.
- **AP6A-Direktfinalresultat**: als `claude-phase-2-ap6a-direct-final-review`
  mit SHA-256 `1322bbb6…` enthalten und korrekt `schema_only`, weil seine
  Platzhalter-Request-ID `native-review-request-ffff…ffff` keinen echten
  Originalrequest bindet und die Corpuszeile kein `request_document` trägt.
- **Ableitung statt Behauptung**: `_archive_native_response_paths()` enumeriert
  alle versionierten nativen Archivantworten aus dem realen Arbeitsbaum, und
  der Test verlangt exakte Mengengleichheit gegen die manifestierten
  Archivquellen sowie Bytegleichheit jedes archivstämmigen Fixtures.
  `native_responses_found` ist abgeleitet, nicht hartkodiert.
- **Keine erfundenen Kontexte**: Alle zwölf kanonischen Requestdokumente
  verifizieren ihre eigene Digestbindung; genau drei besitzen eine archivierte
  Antwort und genau diese drei sind `request_bound`. Die neun unpaarigen
  Requests bleiben korrekt ungepaart.
- **Writer vor Domäne**: Der Corpustest validiert für `request_bound`-Paare
  zuerst gegen die aus dem Originalrequest rekonstruierte Writerschemainstanz
  und erst danach über `parse_bound_native_contract_result()`.
- **Kein Missbrauch von `schema_only`**: `_validate_writer_form_evidence()`
  verlangt für `evidence.kind == "request_bound"` ein Fixture mit
  `binding == "request_bound"` und exakte Gleichheit von
  `writer_schema_sha256` zwischen Writerformzeile und Fixture. Zwei
  Negativkontrollen belegen beide Richtungen, und beide scheitern aus dem
  richtigen Grund — der `schema_only`-Identifier bricht an der
  Bindungsassertion, bevor der dort fehlende Digestschlüssel gelesen würde.
  Ein anderer Weg existiert nicht, weil `evidence.kind` auf
  `{"request_bound", "live_canary"}` beschränkt ist und der Canary-Ast kein
  Fixture referenziert.
- **Frischer Klon**: Kein Corpustest liest `.orchestrator/`; die einzige
  Erwähnung ist ein Präfixvergleich auf Manifestmetadaten.

## 8. Differentialmatrix

Genau acht Writerformen sind abgedeckt und über `len(digests) == 8` als
paarweise verschieden belegt. Jede Form trägt einen zulässigen Nachweis: sechs
erfolgreiche Live-Canaries und zwei echte `request_bound`-Paare;
`schema_only` ist als Nachweis ausgeschlossen. Die beiden requestgebundenen
Nachweise sind sachlich korrekt zugeordnet — `initial_slice` auf die Projektion
`Native Claude slice_initial writer projection`, `convergence` auf
`Native Claude slice_convergence writer projection` — und beide historischen
Antworten exerzieren reale Vertragskanten (eine neue `OBSERVATION` in einem
freigebenden Initialreview beziehungsweise die Dispositionskardinalität mit
erzwungenem `CLOSED` für den eigenen offenen BLOCKER).

Die rote Kontrolle ist falsifizierend: sie entfernt mit `.pop("pattern")` ohne
Default genau eine Writerregel, sodass ein Wegfall derselben Regel im
Produktivcode den Test mit `KeyError` rot färbt, und sie belegt, dass die
gelockerte Regel eine writer-schemavalide Antwort erzeugt, die lokal mit einem
nicht registrierten Code scheitert.

Die Ausnahmeliste kann weder unbemerkt wachsen noch auf nicht existierende
Regressionen verweisen: `len(registered_exceptions("codex")) == 7` und
`== 6` für Claude sind Bestandsschnappschüsse, jeder Eintrag wird gegen die
Existenz seiner benannten Testfunktion geprüft, und die beidseitige
Mengengleichheit bindet die Codes an tatsächliche Erreichbarkeit. Der
Slice-3-Eingriff hat die Menge verifizierbar nicht vergrößert — 13 Einträge vor
und nach dem Diff, geändert wurden nur drei Textfelder innerhalb eines
bestehenden Eintrags mit unverändertem `exception_id` und `error_code`.

## 9. Canary-Sicherheit

- **Produktionsdefaults unverändert**: `NativeCodexExecutionBoundary.__post_init__`
  erzwingt im Produktionsmodus `execution_root == evidence_asset_root ==
  repository_root` und `workspace-write`; beide Aufrufer defaulten auf
  `production(...)`, wenn keine Boundary übergeben wird.
- **Gleicher Adapter- und Runtimepfad**: Das Probe-Werkzeug ruft
  `NativeCodexAdapter` und `run_native_codex_agent()` beziehungsweise
  `run_native_review_agent()` auf und baut keine parallele Kommandozeile.
  Beide sind die nicht persistierenden Varianten — es wird keine Rohantwort in
  den Artefaktspeicher geschrieben.
- **Isolation**: Canary erzwingt `read-only` und lehnt jeden Pfad ab, der gleich
  `repository_root` oder `is_relative_to(repository_root)` ist; `run_agent`
  verwendet `execution_root` als `cwd`, schreibt selbst nichts ins Repository,
  und `_new_runtime_dir()` sowie Reviewer-Workspaces liegen in
  `tempfile`-Verzeichnissen. Der Sandboxmodus ist per
  `_discard_pair(values, "--sandbox")` ausdrücklich aus dem normalisierten
  Transportprofil ausgeschlossen, sodass die `read-only`-Grenze keine
  Profil-Drift erzeugt — die Slice-1-Profilbindung und die Slice-3-Boundary
  greifen konfliktfrei ineinander.
- **Zwei unabhängige Prüfungen**: `main()` vergleicht in beiden Ästen zuerst
  `git status --porcelain=v1 --untracked-files=all` und danach den rekursiven
  Abdruck von `.orchestrator/`, `inbox/` und `outbox/`; beide brechen
  fail-closed ab. Der Abdruck erfasst Pfad, Typ, Modus, Größe, `mtime_ns`, je
  Datei den Inhaltsdigest und je Symlink das Linkziel, nutzt `lstat()` gegen
  Symlinkverfolgung und markiert fehlende Wurzeln als eigenen Eintrag, sodass
  auch das erstmalige Anlegen eines Stores auffällt.
- **Nachgewiesen am Hauptpfad**: Die Regression treibt den realen
  `probe.main()`-Canarypfad bei konstant gestubbtem `_repository_status` — der
  Git-Vergleich kann das Ergebnis also nicht tragen — und belegt, dass eine
  Mutation an `.orchestrator/state.json` abgelehnt wird.

## 10. Findingledger

Vollständige Inventur aller Claude-Findings des Arbeitspakets:

| Finding | Klasse | Herkunft | Status |
|---|---|---|---|
| C-01 – C-08, C-10, C-11 | BLOCKER/OBSERVATION | Planreview R1 | CLOSED (R2, regressionsgeprüft in R3 und R5) |
| C-09 | OBSERVATION | Planreview R1 | CLOSED (R3) |
| C-12, C-13, C-14 | BLOCKER | Planreview R2 | CLOSED (R3) |
| C-15, C-16, C-17 | OBSERVATION | Planreview R2 | CLOSED (R3) |
| C-18, C-19, C-20 | BLOCKER | Planreview R3 | CLOSED (R4) |
| C-21 | OBSERVATION | Planreview R3 | CLOSED (R5) |
| C-22, C-23 | BLOCKER | Planreview R4 | CLOSED (R5) |
| C-24 | OBSERVATION | Planreview R4 | CLOSED (R5) |
| C-25 | OBSERVATION | Planreview R5 | in Slice 01 disponiert, hier formal geschlossen |
| B-01, B-02 | BLOCKER | Slice-01-Review | CLOSED |
| O-01 – O-04 | OBSERVATION | Slice-01-Review | CLOSED |
| O-05, O-06 | OBSERVATION | Slice-02-Review | CLOSED |
| B-03 | BLOCKER | Slice-02-Review | CLOSED |
| C-26 | BLOCKER | Slice-03-Review | CLOSED |
| C-27, C-28 | OBSERVATION | Slice-03-Review | CLOSED |

`C-99` erscheint ausschließlich als Beispiel-ID innerhalb eines
Slice-02-Gegenbeispiels und ist kein Finding.

`C-25` ist das einzige Finding ohne formalen Schließungsmarker: es wurde in
Slice 01 von Codex mit `ACCEPTED` disponiert und von mir dort als sachlich
korrekt und regressionsfest bewertet, aber nie mit `FINDING_STATUS`
abgeschlossen. Als ursprünglicher Reporter schließe ich es unten. Damit bleibt
kein Claude-Finding offen.

**Neue Defekte: keine.** Das Findingledger ist mit dieser Runde vollständig
geschlossen; ich melde kein neues `BLOCKER`-Finding.

**Konsistenz.** Arbeitsplan-Umsetzungsstand, Roadmap-Abschnitt 9.7a,
Corpusmanifest, Slice-03-Dokument und Code stimmen in den tragenden Zahlen
überein: 14 native Antworten, zwölf kanonische Requests, drei
`request_bound`-Paare, elf `schema_only`-Fixtures, drei technische
Failure-Envelopes, acht Writerformen, 13 Ausnahmeeinträge. Die historische
Ablehnung `CLAUDE_SLICE_APPROVAL: NO` in den Slice-Dokumenten bleibt als
Auditspur stehen; die jeweils spätere Schließung ist eindeutig und maßgeblich
(`CLAUDE_SLICE_APPROVAL: YES` für Slice 01 und 02,
`CLAUDE_CORRECTION_REVIEW: YES` und `SLICE_APPROVAL: 03 | YES` für Slice 03).

REVIEW_EVIDENCE: Vertragsgeschlossenheit beider Providerpfade über alle acht Writerformen; Trennung und Bytegleichheit der Reader-Baselines über den gesamten Paketstand gegen `c410eef`; beidseitige Mengengleichheit zwischen registrierten Ausnahmen und hinter dem Writerschema erreichbaren Fehlercodes; geschlossene Transporthülle beider Provider; Requestbindung von Builder, Bundle, `response_contract`, Providerinput und Repairrequest; Unberührtheit von Workflow-, Orchestrator- und Recordmodulen sowie Fail-closed-Verhalten der Raw-first-Recovery; Wirksamkeit der Legacyparser- und Contract-Repair-Sprengfallen; Corpusvollständigkeit gegen unabhängige Neuinventur mit leerer Differenz in beide Richtungen; Byte- und Digestfidelität aller 14 Fixtures; Writer-vor-Domäne-Reihenfolge und Typ-/Digestkopplung der Writerform-Nachweise; Falsifizierbarkeit der roten Kontrolle und Nichtwachstum der Ausnahmemenge; Produktionsdefaults, Isolation, doppelte Unversehrtheitsprüfung und Profilneutralität der Canary-Grenze; Vollständigkeit des Findingledgers und Konsistenz zwischen Plan, Roadmap, Manifest, Slice-Dokumentation und Code | Vier Restrisiken, keines handlungsbedürftig: (1) Die sechs Live-Canaries wurden ausgeführt, bevor der Integritätsabdruck aus C-28 existierte; für genau diese historischen Läufe ruht die Nichtmutation von `.orchestrator/`, `inbox/` und `outbox/` auf den von mir verifizierten Codeeigenschaften (nicht persistierende Runtimevarianten, schreibfreier `run_agent`, Tempverzeichnisse, `read-only`-Sandbox mit repositoryfernem CWD) statt auf einer Messung — künftige Läufe sind gemessen. (2) Der `.orchestrator`-stämmige Teil der Corpusinventur ist naturgemäß nicht aus einem frischen Klon ableitbar und wird aus dem Manifest selbst gezählt; ein gleichzeitiges Entfernen eines Codex-Fixtures aus Manifest und `corpus.jsonl` bliebe rechnerisch konsistent. (3) Der Slice-3-Änderungsumfang überschreitet den deklarierten Pfad um `schemas/native-provider-schema-exceptions-v1.json`, `tests/test_native_codex_contract.py`, `tests/test_native_contract_probe.py` und das Slice-03-Reviewdokument; alle vier sind substanziell begründet (Canary-Falsifikationsbefund, mein eigenes C-28-Akzeptanzkriterium, Reviewprotokoll-Konvention aus Slice 01) und die harte Regel aus Abschnitt 4.4.8 wurde verifizierbar nicht verletzt, sie sind aber nicht wie in Slice 01 ausdrücklich als Pfaderweiterung ausgewiesen. (4) Abschnitt 4.1.8 des Arbeitsplans führt `--max-budget-usd` weiterhin in der als „abschließend" bezeichneten Claude-`semantic_flags`-Liste, während `_normalize_claude()` es bewusst verwirft; der Text ist gegenüber der akzeptierten C-25-Disposition veraltet, die Abweichung ist rein dokumentarisch und durch `test_claude_budget_is_deliberately_profile_neutral_for_c25` gegen Rückabwicklung gesichert | Ein `.orchestrator`-stämmiges Fixture verschwindet aus dem eingefrorenen Corpus: weil seine Quelle nicht klonbar und seine Anzahl selbstreferenziell ist, bleibt die Matrix grün, der eingefrorene Referenzbestand schrumpft still, und die nächste Writerregeländerung wird gegen weniger reale Antwortformen geprüft, als Manifest und Roadmap behaupten

FINDING_STATUS: C-25 | CLOSED | Die Disposition aus Slice 01 ist sachlich korrekt und im Code umgesetzt: `_normalize_claude()` (`src/native_provider_schema.py:257-259`) verwirft `--max-budget-usd` vor der Flagpartition, sodass das normalisierte Transportprofil mit und ohne Budgetwert identisch ist und mit dem Capability-Tabelleneintrag übereinstimmt, während Modell, Effort, ein unbekanntes Argument und ein entferntes Semantikflag weiterhin Profil-Drift beziehungsweise einen harten `NativeProviderSchemaError` erzeugen. `test_claude_budget_is_deliberately_profile_neutral_for_c25` (`tests/test_native_provider_schema.py:122`) sichert genau das ab. Die Begründung trägt: `--max-budget-usd` begrenzt ausschließlich die Kosten und beeinflusst weder Schemaübergabe noch JSON-Akzeptanz; ein Budgetabbruch erscheint als `is_error=true` beziehungsweise fehlendes `structured_output` und wird im Adapter fail-closed abgewiesen, kann also kein scheinbar gültiges vertragsverletzendes Resultat erzeugen. Eine Aufnahme ins Profil hätte umgekehrt jede betriebliche Budgetänderung zu einem kostenpflichtigen Neusondierungszwang gemacht — genau der Schaden, den die Observation benannt hat. Verbleibend ist ausschließlich, dass Abschnitt 4.1.8 des Arbeitsplans das Flag weiterhin in seiner als abschließend bezeichneten Liste führt; das ist eine dokumentarische Altlast ohne Vertrags- oder Laufzeitwirkung und in `REVIEW_EVIDENCE` festgehalten.

PRE_MORTEM: In drei Monaten ist die wahrscheinlichste Fehlerursache, dass der Integritätsabdruck an seiner eigenen Laufzeit scheitert. Er hasht den vollständigen `.orchestrator/`-Artefaktspeicher und wird zweimal je Canary ausgeführt; der Aufwand wächst linear mit einem Speicher, der über jede Orchestrierung weiter zunimmt. Wer dann Canaries in Serie fährt, wird ihn überspringen, auf Sampling reduzieren oder `PROTECTED_REPOSITORY_PATHS` kürzen — und damit genau die Prüfung entfernen, die `git status` konstruktionsbedingt nicht leisten kann, weil `.orchestrator/`, `inbox/` und `outbox/` gitignoriert sind. Die Isolationszusage ruhte danach wieder allein auf der nicht regressionsgebundenen Eigenschaft, dass der Canarypfad die nicht persistierenden Runtimevarianten verwendet; ein späterer Wechsel auf `run_native_codex_agent_checked()`, etwa um Canaryrohantworten zu archivieren, bliebe dann unbemerkt und ließe den Canary in denselben Artefaktspeicher schreiben, dessen Unversehrtheit er beweisen soll. Die zweite, leisere Variante: jemand liest Abschnitt 4.1.8 als normativ, nimmt `--max-budget-usd` wieder ins Transportprofil auf, und jede betriebliche Budgetänderung erzwingt fortan eine kostenpflichtige Neusondierung.

FINAL_APPROVAL: YES

STATUS: DONE

---

# Erneutes branchweites Gesamtreview (Abschlussstand `286f5b2`)

Datum: 24. August 2026 · Reviewer: Claude · Modus: read-only, adversarial

Der vorstehende Abschnitt bleibt als historischer Befund unverändert erhalten.
Er wurde gegen `7ba867d` erstellt, endete mit `FINAL_APPROVAL: YES` und wurde
anschließend durch das Codex-Selbstreview (`CR-01`) widerlegt. Dieses erneute
Review prüft den vollständigen Paketstand `c410eef..286f5b2` einschließlich der
Korrekturslices 03 und 04.

## 1. Prüfgegenstand

Vollständiger Branch-Diff `c410eef..286f5b2`: drei Commits, 30 Dateien,
9.172 hinzugefügte und 143 entfernte Zeilen. Der Arbeitsbaum ist bis auf die
beiden Gesamtreviewdokumente sauber; der Abschlusscommit `286f5b2` enthält
exakt den Slice-04-Stand, den ich freigegeben habe, einschließlich meines
Slice-04-Reviews. Es gibt keinen Drift nach der Slice-04-Freigabe.

`docs/internal/OPTION_CLAUDE_ONLY_REVIEW_POLICY.md` liegt im Diff, ist laut
Arbeitsplan ausdrücklich nicht Bestandteil von Contract Closure und ist
verifiziert inert: reine Dokumentation ohne jeden Bezug aus `src/` oder
`scripts/`.

Die vollständige Suite wurde nicht erneut ausgeführt und kein externer Canary
wiederholt. Providerfrei ausgeführt wurden ausschließlich Gegenproben zur
Falsifikation konkreter Behauptungen; eine davon hat einen Defekt aufgedeckt
und wurde deshalb bis zum fokussierten Testlauf vertieft.

## 2. Was nachweislich geschlossen ist

**Vertragsgeschlossenheit (Claude).** Ich habe eine Breitensuche über
**384 gebundene Kontexte mit 5.760 Antwortkandidaten** gefahren: acht
Vorbefundmengen (leer, offener Blocker, offene Observation, geschlossener
Blocker, gemischt geschlossen/offen, zwei offene, fremdes A-Finding, gemischt
fremd/eigen) × drei Approvalmarker × Initial- und Konvergenzrunde ×
vier Attestierungs-/Testfreigabezustände × Anchorzustand, mutiert über
Statusänderungen auf `OPEN`/`CLOSED`, Reklassifizierungen nach `BLOCKER`/
`OBSERVATION`, neue Findings beider Klassen, Anchors ohne Origin, Blank-Evidenz
und NUL-Texte. Ergebnis:

- hinter writer-validem JSON erreichbar: ausschließlich `approval-invalid`,
  also **kein unregistrierter Code**;
- **keine stille Überpermissivität**: kein Kandidat, der ein geschlossenes,
  fremdes oder unbekanntes Finding als Ziel trägt, wird von beiden Schichten
  akzeptiert;
- alle vier Writerformen (`plan`, `slice_initial`, `slice_convergence`,
  `final`) entstehen unverändert.

Damit ist die zentrale Paketinvariante am Abschlussstand belegt — in beide
Richtungen, nicht nur gegen Ablehnungen.

**Findings und Konvergenz.** Ein Reviewer kann ausschließlich eigene offene
Findings disponieren. Geschlossene und fremde Findings sind in allen vier
Writerformen und in beiden Entscheidungsästen unveränderlich; der in Slice 04
geschlossene Bypass, bei dem ein Denial seinen erforderlichen offenen Blocker
durch Wiederöffnen eines abgeschlossenen Blockers erschleicht, ist auf beiden
Schichten beseitigt. Korrektur- und Finalrunden können keine neue Observation
erzeugen. Eine Freigabe verlangt vollständige Dispositionen, echte
Reviewevidenz, nichtleeres Pre-Mortem und einen zulässigen resultierenden
Findingzustand.

**Ausnahmeregister.** 13 Einträge (sieben Codex, sechs Claude). Jeder Eintrag
benennt eine tatsächlich existierende Regressionstestfunktion — vollständig
nachgeprüft, keine Lücke. `finding-reference-not-open` ist korrekt **nicht**
registriert, weil es hinter writer-validem JSON unerreichbar ist.

**Reader-Baselines.** Beide Resultat-v1-Schemadateien sind über den gesamten
Paketstand `c410eef..286f5b2` bytegleich unverändert.

**Corpus.** 14 Fixtures; Digest, Sourcepath, Provider, Bytes und Leseschema
sind für jedes Fixture gekoppelt und fehlerfrei. Meine unabhängige Neuinventur
findet 14 Antworten mit leerer Differenz in beide Richtungen. Bindungsklassen:
elf `schema_only`, drei `request_bound`. Die drei historischen Rohantworten
sind unverändert; ihre rekonstruierten Writerschemadigests leiten sich korrekt
aus dem jeweiligen historischen Kontext ab, und alle drei durchlaufen
Writerschema **vor** Domänenkonvertierung mit `approval=True`. Ihr
ursprünglicher `response_contract.schema_sha256` `31510b2d…` bleibt sichtbar
abweichend und wird planmäßig nicht als Live-Transportnachweis ausgegeben.

**Writerformen und Transport.** Acht Formen, sieben `live_canary` und genau ein
`request_bound` für `claude/initial_slice`. Der Konvergenz-Canary belegt die
zuvor nie übertragene Kompositionstiefe: In den tatsächlich serialisierten
Providerschemabytes enthält
`bound_slice_convergence_approved.properties.status_changes.items.oneOf` genau
eine Option `('C-01', 'CLOSED')` bei `minItems == maxItems == 1`, und
`response_contract.schema_sha256` stimmt mit dem Bundle-Digest überein. Der
Writerschema-SHA-256 `a86ebb51…` reproduziert bitgenau aus dem kanonischen
Bundlebuilder und stimmt mit Manifest, Slice-03-Tabelle und Slice-04-Protokoll
überein.

**Failure-, Replay- und Sicherheitsgrenzen.** `src/workflow.py`,
`src/orchestrator.py` und `src/artifact_models.py` sind vom gesamten Paket
unberührt; Record-Autorität, Raw-first-Recovery und Resume bleiben strukturell
auf dem freigegebenen Stand und fail-closed. Die Legacyparser-Sprengfallen sind
an sieben Stellen aktiv. Der Integritätsabdruck der gitignorierten
Workflowstores behandelt fehlende Wurzeln als eigenen Eintrag, verfolgt
Symlinks wegen `lstat()` nicht und erfasst Linkziele explizit.

## 3. Neuer Blocker

Bei der Verifikation der Canary-Evidenz reproduzierte der Writerschemadigest
bitgenau, die **Request-ID jedoch nicht**. Ich bin dem nachgegangen, weil ich
denselben Wert im Slice-04-Review noch als exakt reproduzierbar bestätigt
hatte.

Ursache: `_claude_canary_bundle()` und `_codex_canary_bundle()` binden
`base_commit=_head(repo_root)` (`scripts/native_contract_probe.py:385`, `:455`),
also den **lebenden HEAD**. Der Canary lief bei HEAD `eb38a31`; das Committen
der Arbeit als `286f5b2` hat den HEAD bewegt, und damit ändert sich die
rekonstruierte Request-ID von `58e98da5…` auf `6694324a…`. Der
Writerschemadigest ist davon korrekt unabhängig, weil er nur vom Reviewkontext
abhängt.

`tests/test_native_contract_probe.py:114` vergleicht die im Manifest
persistierte Canary-Request-ID gegen genau diese neu berechnete ID. Der
fokussierte Lauf am Abschlussstand ergibt:

```text
python3 -m pytest tests/test_native_review_contract.py \
  tests/test_native_contract_differential.py \
  tests/test_native_contract_corpus.py \
  tests/test_native_contract_probe.py -q

1 failed, 41 passed
FAILED tests/test_native_contract_probe.py::test_convergence_canary_serializes_status_change_items_one_of
```

Die attestierten `1273 passed` und `42 passed` wurden vor dem Commit erzeugt,
als HEAD noch `eb38a31` war. Sie beschreiben damit nicht den Stand, der hier
freigegeben werden soll. Gesamt-Abnahmekriterium 12 („Die vollständige
Repositorysuite grün") ist an `286f5b2` nicht erfüllt.

Entscheidend ist, dass dies keine einmalige Verspätung ist: Die Behauptung
bindet einen **persistierten historischen Fakt** an einen aus **veränderlichem
Zustand** neu berechneten Wert. Der Test kann nur an genau dem einen Commit
grün sein, an dem der Canary zufällig lief; jeder weitere Commit — auch der,
der die Korrektur einträgt — bricht ihn erneut. Betroffen ist nur diese eine
Behauptung, aber alle sieben `live_canary`-Request-IDs im Manifest sind aus
demselben Grund nach jedem Commit nicht mehr reproduzierbar.

Ich benenne ausdrücklich meinen eigenen Anteil: Im Slice-04-Review habe ich
bestätigt, dass die Request-ID bitgenau reproduziert — das war in jenem Moment
richtig, weil HEAD noch dem Canarycommit entsprach. Ich habe einen **Wert**
verifiziert statt der Stabilität dessen, was ihn erzeugt. Das ist dieselbe
Fehlerklasse wie beim Übersehen von `C-29`: dort habe ich Ablehnungen
enumeriert statt Annahmen, hier einen Momentwert statt einer Invariante.

## 4. Findingledger

`C-01` bis `C-24` sind auf Planebene über fünf Revisionen geschlossen und in
Revision 5 regressionsgeprüft. `C-25` habe ich im vorstehenden Gesamtreview
geschlossen. `C-26` bis `C-28` sind in Slice 03 geschlossen, `C-29` in
Slice 04; beide Schließungen habe ich in diesem Review am Abschlussstand
erneut verifiziert und bestätige sie unten. Die slicelokalen Bezeichner
`B-01`, `B-02`, `B-03` und `O-01` bis `O-06` sind in ihren jeweiligen
Protokollen geschlossen. `CR-01` bis `CR-03` sind Codex-eigene Bezeichner;
`CR-01` entspricht meinem `C-29`, `CR-02` wurde begründet verworfen, `CR-03`
bleibt eine separate, nicht blockierende Dokumentationsabweichung.

Neu und offen: `C-30`.

REVIEWER: claude

NEW_FINDING: C-30 | BLOCKER | Der Abschlussstand `286f5b2` enthält einen fehlschlagenden Test, und die zugrunde liegende Bindung ist dauerhaft instabil. `_claude_canary_bundle()` und `_codex_canary_bundle()` setzen `base_commit=_head(repo_root)` (`scripts/native_contract_probe.py:385`, `:455`), binden die Canary-Request-ID also an den lebenden HEAD. Der Konvergenz-Canary lief bei HEAD `eb38a31` und ist im Manifest mit `native-review-request-58e98da53bd078faa2e2e666d9a1549a51435c683709040dab0aa2d26a11d236` persistiert. `tests/test_native_contract_probe.py:114` vergleicht diesen persistierten Wert gegen ein zur Laufzeit neu gebautes Bundle; nach dem Commit als `286f5b2` liefert der Builder `native-review-request-6694324a16597fe5be325a6e49bf5ec5f09a4a831e2a0e6f99bbb578f997c3b8`, und der Test schlägt fehl. Der fokussierte Slice-04-Lauf ergibt am Abschlussstand `1 failed, 41 passed`; die attestierten `1273 passed` und `42 passed` wurden vor dem Commit bei HEAD `eb38a31` erzeugt und beschreiben den freizugebenden Stand nicht. Gesamt-Abnahmekriterium 12 ist damit an `286f5b2` nicht erfüllt. Der Defekt ist nicht durch erneutes Ausführen behebbar: Die Behauptung koppelt einen persistierten historischen Fakt an einen aus veränderlichem Zustand abgeleiteten Wert und ist nur an genau dem Commit grün, an dem der Canary lief — jeder weitere Commit bricht sie erneut. Der Writerschemadigest `a86ebb51…` ist korrekt HEAD-unabhängig und reproduziert weiterhin bitgenau; betroffen ist ausschließlich die Request-ID-Bindung. | Die Manifest-Evidenzzeile eines `live_canary` führt den beim Lauf tatsächlich verwendeten `base_commit` (oder die vollständige unveränderliche Requestbindung), und der Test rekonstruiert das Bundle mit genau diesem Wert statt mit dem lebenden HEAD, sodass die Request-ID-Gleichheit deterministisch und HEAD-unabhängig gilt; die Bindung darf dabei nicht auf eine reine Längenprüfung zurückfallen. Eine Regression weist nach, dass die Prüfung auch nach einem beliebigen weiteren Commit grün bleibt, etwa indem sie einen abweichenden HEAD simuliert und die Gleichheit dennoch hält. Abschließend wird die vollständige Repositorymatrix an exakt dem Commit ausgeführt, der freigegeben werden soll, und ihr Ergebnis mit dieser Commit-ID attestiert.

FINDING_STATUS: C-29 | CLOSED | Am Abschlussstand `286f5b2` erneut verifiziert. Über 384 gebundene Kontexte mit 5.760 Antwortkandidaten wird kein Kandidat, dessen Status- oder Reklassifizierungsziel ein geschlossenes, fremdes oder unbekanntes Finding ist, von beiden Schichten akzeptiert; hinter writer-validem JSON ist ausschließlich `approval-invalid` erreichbar und damit kein unregistrierter Code. Die Verengung auf `own_open_ids` samt Kardinalitätsgrenzen und der lokale Vorzustandswächter mit `finding-reference-not-open` sind unverändert wirksam, alle vier Writerformen entstehen weiterhin, und das Ausnahmeregister bleibt korrekt bei 13 Einträgen.

FINDING_STATUS: C-26 | CLOSED | Unverändert geschlossen. Die unabhängige Neuinventur am Abschlussstand findet 14 native Rohantworten bei 14 manifestierten Quellen mit leerer Differenz in beide Richtungen; alle 14 Fixtures sind digest-, meta- und bytegetreu gekoppelt und gegen ihr Leseschema lesbar.

FINDING_STATUS: C-27 | CLOSED | Unverändert geschlossen. Das Manifest führt acht Writerformen mit sieben `live_canary`- und genau einem `request_bound`-Beleg für `claude/initial_slice`; Fixturetyp und Writerschemadigest bleiben typgebunden gekoppelt.

FINDING_STATUS: C-28 | CLOSED | Unverändert geschlossen. Der rekursive Integritätsabdruck der gitignorierten Workflowstores ist am Abschlussstand vorhanden, behandelt fehlende Wurzeln als eigenen Eintrag, verfolgt Symlinks wegen `lstat()` nicht und erfasst Linkziele explizit; beide `main()`-Äste prüfen fail-closed.

PRE_MORTEM: In drei Monaten ist die wahrscheinlichste Ursache, dass die Canary-Evidenz erneut an veränderlichen Zustand gebunden wird, weil die Ursache von C-30 als Terminproblem statt als Bindungsfehler verstanden wird. Wer den Test nur „grün macht", indem er die Request-ID im Manifest auf den dann aktuellen HEAD nachzieht, hat die Instabilität nicht beseitigt, sondern in den nächsten Commit verschoben — und wird beim übernächsten Mal versucht sein, die Gleichheitsprüfung ganz zu streichen. Damit fiele die einzige Bindung weg, die eine handgeschriebene Manifestzeile heute rot färbt, und die sieben Canaryzeilen wären wieder reine Buchführung, deren Response-Digests ohnehin lokal unverifizierbar sind. Die zweite, leisere Variante: Jemand hebt eine Kardinalitätsgrenze für `status_changes` oder `reclassifications` an, ohne die ID-Menge weiterhin aus `own_open_ids` abzuleiten; weil `_bound_review_definition()` das `finding_id`-Enum bei leerer offener Menge still nicht verengt, werden geschlossene Findings wieder darstellbar, und die beiden Überpermissivitätskontrollen prüfen nur die zwei heute bekannten Dokumentklassen.

FINAL_APPROVAL: NO

STATUS: DONE
