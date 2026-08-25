REVIEWER: claude

# Branchweites Abschlussreview – Native Agent Contract Closure (`06819bd`)

Datum: 24. August 2026 · Reviewer: Claude · Modus: read-only, adversarial

Dieses Dokument ist ein eigenständiges Gesamtreview des Abschlussstands
`06819bd`. Es ersetzt die früheren Gesamtreviews nicht und verändert sie nicht:

- `docs/internal/native-agent-contract-closure-final-review-claude.md` enthält
  mein Review gegen `7ba867d` (`FINAL_APPROVAL: YES`, später durch `CR-01`
  widerlegt) und mein erneutes Review gegen `286f5b2`
  (`FINAL_APPROVAL: NO` wegen `C-30`);
- `docs/internal/native-agent-contract-closure-final-self-review-codex.md`
  enthält die beiden Codex-Selbstreviews mit `CR-01` bis `CR-06`.

Beide bleiben als historische Befundquellen unverändert erhalten.

## 1. Prüfgegenstand

- Basis vor Contract Closure: `c410eef`
- Abschlusscommit: `06819bd` (`fix(native-contracts): close request-bound writer gaps`)
- Branch-Diff: 35 Dateien, 11.857 hinzugefügte und 196 entfernte Zeilen
- Arbeitsbaum zu Beginn sauber; keine unversionierten Dateien

**Kein Drift nach meiner Slice-05-Freigabe.** Der Diff `5ec8d0d..06819bd`
umfasst exakt die Dateien und Zeilenzahlen, die ich im Slice-05-Review geprüft
habe (`agent_runtime.py` 18, `native_codex_request.py` 123,
`native_review_contract.py` 5, `native_review_request.py` 152,
`orchestrator.py` 10, Manifest 7, Probe 22, dazu die Testdateien), zuzüglich
des Slice-05-Protokolls mit meinem Review. Der Commit enthält meine Freigabe
`SLICE_APPROVAL: 05 | YES`.

Die vollständige Suite wurde nicht erneut ausgeführt und kein externer Canary
wiederholt. Ausgeführt wurden ausschließlich providerfreie Gegenproben gegen
den Abschlusscommit.

## 2. Vertragsgeschlossenheit — Breitensuche

Die tragende Aussage des Arbeitspakets habe ich nicht aus den Sliceprotokollen
übernommen, sondern am Abschlussstand neu gemessen: **1.656 Antwortkandidaten
über eine Kontextmatrix** aus sieben Vorbefundmengen (leer, offener Blocker,
offene Observation, geschlossener Blocker, gemischt geschlossen/offen, zwei
offene, fremdes `A-`Finding) × drei Approvalmarker × Initial- und
Konvergenzrunde × drei Attestierungszustände, mutiert über Statusänderungen
nach `OPEN`/`CLOSED`, Reklassifizierungen nach `BLOCKER`/`OBSERVATION` in
Freigabe- und Ablehnungsast, neue Findings beider Klassen, Blank-Evidenz und
NUL-Texte.

Ergebnis:

- hinter writer-validem JSON lokal erreichbar: ausschließlich
  `approval-invalid` — **kein unregistrierter Fehlercode**;
- **keine stille Überpermissivität**: kein Kandidat, dessen Findingziel
  geschlossen, fremd oder unbekannt ist, und keine neue Observation in einer
  Final- oder Konvergenzrunde wird von beiden Schichten akzeptiert;
- alle vier Claude-Writerformen (`plan`, `slice_initial`, `slice_convergence`,
  `final`) entstehen unverändert.

Damit gilt die Kernbehauptung des Pakets in beide Richtungen: Writer-valides
JSON scheitert lokal nur an registrierten Ausnahmen, und fachlich unzulässiges
JSON wird nicht von beiden Schichten zugleich akzeptiert.

**Reader-Baselines und Register.** `git diff c410eef..06819bd` auf beide
Resultat-v1-Schemadateien ist leer — sie sind über das gesamte Arbeitspaket
bytegleich unverändert. Das Providerausnahmeregister führt unverändert
13 Einträge (sieben Codex, sechs Claude). Keine Domänenregel wurde durch eine
breitere Readerform ersetzt oder in eine neue Ausnahme verschoben; die in
Slice 05 eingeführten Codes `finding-reference-not-open`, `request-invalid` und
`evidence-invalid` sind hinter writer-validen Dokumenten nicht erreichbar und
daher zu Recht nicht registriert.

## 3. Live-, Record-Ahead- und Recoveryautorität

Die Reihenfolge ist für beide Provider identisch und vollständig:
JSON dekodieren → Objektform → Reader-Baseline → **konkretes requestgebundenes
Writerschema** → Request-ID → diagnostischer Raw-Callback → lokale Domäne
(`src/agent_runtime.py:1305-1317`, `:1475-1489`).

Die Vollständigkeit habe ich über alle Aufrufstellen geprüft, nicht anhand der
Berichte: Es existieren genau **fünf** produktive `parse_bound_native_*`-Aufrufe
und jedem geht unmittelbar eine Writerprüfung voraus —
`agent_runtime.py:1309/1317` und `:1479/1487`, `orchestrator.py:1051/1052`,
`:1400/1401` und `:1611/1614`. Es bleibt keine ungeschützte
Domänenkonvertierung übrig.

Die Prüfung ist an die richtige Quelle gebunden:
`validate_native_review_provider_response()` verwendet
`bundle.provider_response_schema`, also exakt die dem Provider übergebenen
unveränderlichen Bytes, und rekonstruiert ausschließlich die festgelegte Hülle
`{"result": document}`.

Weil die Writerprüfung **vor**, die Domänenkonvertierung aber **nach** dem
`validated_response_callback` liegt, bleibt die Raw-first-Crashsicherheit
erhalten: Writer-invalide Bytes erreichen weder ein autoritatives Resultat noch
den Callback (`persisted == []` in
`test_native_codex_writer_invalid_bytes_never_reach_validated_callback`),
während writer-valide, lokal an einer registrierten Ausnahme scheiternde Bytes
weiterhin diagnostisch persistiert werden, ohne fachliche Autorität zu
erhalten.

Der vor der Current-Diff-Policy laufende Claude-Recoverypfad ist korrekt als
**aktueller** requestgebundener Pfad behandelt: Er rekonstruiert seinen
`NativeReviewContext` aus dem laufenden Workflowzustand
(`src/orchestrator.py:1586-1604`) und leitet über
`..._for_context()` das Writerschema deterministisch aus genau diesem Kontext
ab. Echte Legacy- und Corpusdaten laufen unverändert über ihren dokumentierten
`schema_only`- beziehungsweise `request_bound`-Nachweis. Eine erfolgreiche
Recovery startet keinen Provider erneut.

`src/workflow.py` und `src/artifact_models.py` sind vom gesamten Paket
unberührt; Record-Autorität, Replay und Mirror-Semantik bleiben strukturell auf
dem freigegebenen Stand. Die Legacyparser-Sprengfallen sind an sieben Stellen
aktiv.

## 4. Findingzustände und Konvergenz

Über die Breitensuche in Abschnitt 2 bestätigt: Ein Reviewer kann
ausschließlich eigene **offene** Findings schließen oder reklassifizieren.
Geschlossene und fremde Findings sind in allen vier Writerformen und in beiden
Entscheidungsästen unveränderlich. Ein Denial kann seinen erforderlichen
offenen Blocker weder durch Wiederöffnen (seit `C-29`) noch durch
Reklassifizieren (seit `C-31`) noch durch Auslassen einer Disposition
erschleichen.

Den regulären Finalreviewpfad habe ich über die produktive Regel hergeleitet —
`WorkUnitKind.FINAL_REVIEW is not WorkUnitKind.CORRECTION` ergibt
`allow_new_observations=True` — und nicht von Hand gesetzt:

| Antwort im regulären Finalreview | Writer | Lokal |
|---|---|---|
| `BLOCKER C-01 → OBSERVATION` | rejected | `approval-invalid` |
| neue `OBSERVATION` | rejected | `approval-invalid` |
| bestehende `OBSERVATION C-03` unverändert disponiert | VALID | ACCEPTED |

Beide Angriffe scheitern auf beiden Schichten; eine bereits bestehende
Observation wird nicht versehentlich mutiert. Korrektur- und Finalrunden können
keine neuen Observations erzeugen. Eine Freigabe verlangt weiterhin
vollständige Dispositionen, echte Reviewevidenz, ein nichtleeres Pre-Mortem und
einen zulässigen resultierenden Findingzustand.

## 5. Request-, Bundle-, Kontext- und Assetbindung

Alle Gegenbeispiele werden am Abschlussstand mit unterscheidbaren, sachlich
präzisen Fehlern abgewiesen — konstruiert direkt über die Bundlekonstruktoren,
nicht über die stets konsistenten Builder:

| Konstruktion | Ergebnis |
|---|---|
| Claude-Kontextdrift bei unveränderten Requestbytes | `request-invalid: request document differs from its bound review context` |
| Codex-gefälschte kanonische Bytes, originale `request_id` | `request-invalid: request content differs from its bound digest` |
| Codex-`content_ref` ohne Assets | `evidence-invalid: request evidence assets differ from content references` |
| Codex-Asset doppelt | `evidence-invalid: request evidence asset paths must be unique` |
| Codex-Asset inhaltlich verändert | `evidence-invalid: evidence asset digest differs from content` |
| Repairbundle ohne Parent | `repair-invalid: repair request requires its immutable parent bundle` |

Das Claude-Repairbundle trägt sein echtes Parentbundle als Feld und vergleicht
Bound Context, `parent_request_id`, `current_fingerprint` und
`response_contract`.

Die freigegebene `C-32`-Grenze ist eingehalten und nicht kaschiert: Der
Codex-Request transportiert ausschließlich offene Findings; geschlossene werden
nicht in die Projektion erfunden. Ihre Autorität liegt in der
Workflow-Recordkette, und dort greift die Absicherung — der erweiterte Test
`test_combined_native_finding_authority_rejects_state_mirror_drift` führt ein
geschlossenes Finding und weist die fail-closed Erkennung eines manipulierten
Mirrorbestands mit `differs from the state-v3 mirror` nach.

## 6. Corpus- und Differentialnachweise

- Inventur: 14 gefundene und 14 übernommene Antworten, zwölf kanonische
  Requests, drei `request_bound`-Paare, elf `schema_only`, drei technische
  Failure-Envelopes.
- Meine unabhängige Neuinventur (`.orchestrator/artifacts/*/native-codex-responses/*.json`
  ohne `*.failure.json` plus die aus dem Arbeitsbaum abgeleiteten
  Archivantworten) ergibt **leere Differenz in beide Richtungen**.
- Für jedes Fixture sind `fixture_id`, Bytes, Sourcepath, SHA-256,
  Bindungsklasse und Provider gekoppelt; **null Kopplungsfehler**, und jede
  Antwort ist gegen ihr allgemeines Leseschema lesbar.
- Die drei historischen Rohantworten sind unverändert; `corpus.jsonl` ist seit
  Slice 03 bytegleich, und der gesamte Manifestdiff aus Slice 05 besteht
  ausschließlich aus sieben hinzugefügten `base_commit`-Zeilen.
- Alle drei `request_bound`-Paare durchlaufen weiterhin Writerschema **vor**
  Domänenkonvertierung und reproduzieren ihren Manifestdigest aus dem
  historischen Kontext.
- `schema_only`, `request_bound` und `live_canary` bleiben semantisch getrennt;
  `base_commit` erscheint ausschließlich in `live_canary`-Zeilen.
- Die Differentialmatrix erkennt beide Richtungen: unerwartete lokale
  Ablehnungen über die beidseitige Mengengleichheit gegen das Register und
  stille Überpermissivität über die seit Slice 04 geführten
  Verbotsdokumentklassen.

## 7. Commitstabile Canary- und Transportevidenz

Acht Writerformen, davon sieben `live_canary` und ein `request_bound`
(`claude/initial_slice`). Jede `live_canary`-Zeile trägt einen konkreten
historischen `base_commit`, und ich habe **jede einzelne** aus genau diesem
Commit rekonstruiert:

| Zeile | base_commit | ≠ HEAD | request_id | Writerdigest | `response_contract` == Digest |
|---|---|---|---|---|---|
| codex/plan | `7ba867dd24` | ja | reproduziert | reproduziert | ja |
| codex/implementation | `7ba867dd24` | ja | reproduziert | reproduziert | ja |
| codex/correction | `7ba867dd24` | ja | reproduziert | reproduziert | ja |
| codex/final_report | `7ba867dd24` | ja | reproduziert | reproduziert | ja |
| claude/plan | `7ba867dd24` | ja | reproduziert | reproduziert | ja |
| claude/convergence | `eb38a316a7` | ja | reproduziert | reproduziert | ja |
| claude/final | `7ba867dd24` | ja | reproduziert | reproduziert | ja |

Jeder eingefrorene Commit weicht vom lebenden `HEAD` (`06819bd`) ab — die
Reproduktion belegt HEAD-Unabhängigkeit und ist kein Zufall. Der
Konvergenz-Canary bestätigt die im Auftrag genannten Werte exakt: Request-ID
`native-review-request-58e98da5…d236`, Writerschema-SHA-256 `a86ebb51…2e04`,
Antwort-SHA-256 `f6a2eff2…953e`. Seine serialisierten Providerschemabytes
enthalten `bound_slice_convergence_approved.properties.status_changes.items.oneOf`
mit genau einer Option `('C-01', 'CLOSED')` bei `minItems == maxItems == 1` —
den gebundenen offenen eigenen Finding-IDs.

Kein Test trägt eine historische Gleichheitsbehauptung am lebenden `HEAD`. Die
Regression `test_all_live_canaries_rebuild_from_frozen_base_not_current_head`
monkeypatcht `_head` auf einen abweichenden Wert, iteriert alle sieben Zeilen,
verlangt volle Stringgleichheit der `request_id` statt Präfix oder Länge und
enthält eine Negativkontrolle, die beweist, dass der eingefrorene Wert tragend
ist. Damit kann ein Commit dieses Stands die Matrix nicht erneut brechen — die
Wurzel von `C-30` ist strukturell beseitigt.

**Zur ausdrücklich erbetenen Prüfung der Writerschemadigest-Bindung.** Ich habe
den Manipulationspfad reproduziert: Fälscht man `writer_schema_sha256` einer
Nicht-`convergence`-Zeile, akzeptiert `_validate_writer_form_evidence()` die
Fälschung, weil sie für `live_canary`-Zeilen nur `len(...) == 64` prüft. Von
den sieben Zeilen ist der Digest maschinell nur für `convergence` gebunden
(`tests/test_native_contract_probe.py:114`); für `request_bound`-Zeilen und
-Fixtures ist er vollständig gebunden
(`tests/test_native_contract_corpus.py:218`, `:250`, samt Negativkontrolle
`:306`).

Ich stufe das **nicht** als Blocker ein, und zwar aus vier zusammenwirkenden
Gründen: (1) Alle sieben Digests sind heute nachweislich korrekt — ich habe
jeden einzelnen reproduziert. (2) Die Wahrheit ist maschinell unabhängig vom
Manifestfeld wiederherstellbar: Die `request_id` reproduziert aus dem
eingefrorenen `base_commit`, und das damit gepinnte Requestdokument trägt in
`response_contract.schema_sha256` denselben Digest wie das neu gebaute Bundle —
verifiziert für alle sieben Zeilen. Eine Fälschung wäre also erkennbar, sie
wird nur nicht automatisch erkannt. (3) Kein Produktivpfad liest das Manifest;
es ist ein Evidenzartefakt für Tests und Audit. (4) Der freigegebene Plan
verlangt für eine `live_canary`-Zeile das **Nennen** von Schemadigest,
Repräsentativkontext und Canary (Abschnitt 4.5.8) und die maschinelle
Writerschemavalidierung ausdrücklich für `request_bound`-Paare
(Abschnitt 4.5.2, Gesamt-Abnahmekriterium 9) — und genau diese ist gebunden.
Es liegt damit kein verletztes Akzeptanzkriterium und keine aktuell falsche
Aussage vor, sondern ein Härtungspotenzial. Es steht vollständig in
`REVIEW_EVIDENCE` und ist die von mir benannte Bruchbedingung.

Repository- und Workflowstore-Integrität bleiben fail-closed: `main()`
vergleicht in beiden Ästen zuerst `git status --porcelain=v1
--untracked-files=all` und danach den rekursiven Abdruck von `.orchestrator/`,
`inbox/` und `outbox/`, der fehlende Wurzeln als eigenen Eintrag erfasst,
Symlinks wegen `lstat()` nicht verfolgt und Linkziele explizit aufnimmt.

## 8. Findingledger

| Findings | Herkunft | Status |
|---|---|---|
| `C-01` – `C-24` | Planreview, fünf Revisionen | CLOSED, in Revision 5 regressionsgeprüft |
| `C-25` | Planreview R5 | CLOSED im Gesamtreview gegen `7ba867d`; `CR-03` ist mit der Planänderung in `06819bd` sachlich nachgezogen |
| `B-01`, `B-02`, `O-01` – `O-04` | Slice-01-Review | CLOSED |
| `O-05`, `O-06`, `B-03` | Slice-02-Review | CLOSED |
| `C-26`, `C-27`, `C-28` | Slice-03-Review | CLOSED |
| `C-29` | Slice-04-Review (entspricht `CR-01`) | CLOSED |
| `C-30`, `C-31`, `C-32` | Slice-05-Planreview | CLOSED im Slice-05-Implementierungsreview, hier am Abschlussstand erneut verifiziert |

`C-99` erscheint ausschließlich als Beispiel-ID in einem Slice-02-Gegenbeispiel
und ist kein Finding. Die Codex-Bezeichner `CR-01` bis `CR-06` sind
disponiert: `CR-01` = `C-29`, `CR-02` begründet verworfen, `CR-03` in Slice 05
dokumentarisch geschlossen (`--max-budget-usd` ist im Arbeitsplan jetzt
ausdrücklich profilfremd), `CR-04`, `CR-05` und `CR-06` durch Slice 05
implementiert.

**Es bleibt kein Claude-Finding offen.** Ein neues Finding entsteht nicht; die
nächste freie ID wäre `C-33`.

## 9. Plan-, Slice- und Dokumentationskonsistenz

Arbeitsplan, alle fünf Slice-Berichte, Roadmap, Implementierung, Schemas,
Corpusmanifest und Tests treffen dieselben Aussagen: 14 Antworten, zwölf
Requests, drei `request_bound`-Paare, elf `schema_only`, acht Writerformen,
sieben `live_canary` zu einem `request_bound`, 13 Ausnahmeeinträge. Die Roadmap
benennt zusätzlich die beiden von mir im Slice-05-Planreview verlangten
Präzisierungen wörtlich: dass der reguläre Finalreviewpfad
`allow_new_observations=True` setzt und dass geschlossene Codex-Findings
Autorität der Workflow-Recordkette bleiben. Historische Ablehnungen in den
Sliceprotokollen bleiben als Auditspur stehen; die jeweils spätere Schließung
ist eindeutig und maßgeblich.

REVIEW_EVIDENCE: Vertragsgeschlossenheit beider Providerpfade über 1.656 Antwortkandidaten in einer Kontextmatrix aus Vorbefundmengen, Approvalmarkern, Runden und Attestierungszuständen, geprüft in beide Richtungen auf unregistrierte Fehlercodes und stille Überpermissivität; Bytegleichheit beider Reader-Baselines über den gesamten Paketstand und Unverändertheit des 13-Einträge-Registers; Vollständigkeit der Writerautorität über alle fünf produktiven `parse_bound_native_*`-Aufrufstellen samt Reihenfolge Reader → Writer → Request-ID → diagnostischer Raw-Callback → Domäne und Erhalt der Raw-first-Crashsicherheit; Einordnung des Pre-Current-Diff-Policy-Pfads als aktueller requestgebundener Pfad; Unberührtheit von `workflow.py` und `artifact_models.py` sowie sieben aktive Legacyparser-Sprengfallen; Unveränderlichkeit geschlossener und fremder Findings und Unmöglichkeit des Blocker-Erschleichens durch Wiederöffnen, Reklassifizieren oder Auslassen; Finalreview-Invariante über die produktive Regel `unit.kind is not WorkUnitKind.CORRECTION`; Abweisung aller sechs Bundle-, Repair- und Assetgegenbeispiele über direkte Konstruktion; Einhaltung der `C-32`-Transportgrenze mit fail-closed Mirrorabsicherung; Corpusinventur mit leerer Differenz in beide Richtungen und null Kopplungsfehlern; Reproduktion aller sieben Canary-Zeilen aus eingefrorenem `base_commit` bei abweichendem HEAD; `status_changes.items.oneOf` in den serialisierten Konvergenzbytes; fail-closed Integritätsprüfung von Arbeitsbaum und gitignorierten Workflowstores einschließlich Symlinks und fehlender Wurzeln | Drei nicht ausführbare Restrisiken: (1) Der `writer_schema_sha256` ist für sechs der sieben `live_canary`-Zeilen maschinell nur längengeprüft; ich habe den Manipulationspfad reproduziert und zugleich verifiziert, dass alle sieben Digests korrekt sind und über die aus dem eingefrorenen `base_commit` reproduzierte `request_id` samt `response_contract.schema_sha256` unabhängig vom Manifestfeld wiederherstellbar bleiben — die Bindung fehlt maschinell, die Aussage ist nicht falsch, und der freigegebene Plan verlangt die maschinelle Validierung ausdrücklich nur für `request_bound`-Paare. (2) Die Trennung zwischen diagnostischer Quarantäne und autoritativem Resultat hängt an der Reihenfolge dreier Zeilen in zwei Funktionen, die kein Test als Reihenfolge festhält; abgesichert ist nur das Ergebnis `persisted == []` für einen writer-invaliden Fall. (3) Der `response_sha256` der sieben Canaries bleibt mangels gespeicherter Antwortbytes lokal unverifizierbar, und der `.orchestrator`-stämmige Teil der Corpusinventur ist naturgemäß nicht aus einem frischen Klon ableitbar | Jemand ergänzt eine achte Canaryzeile oder ersetzt eine bestehende und trägt `base_commit`, `request_id` und `writer_schema_sha256` von Hand ein; weil die Rekonstruktionsregression nur die `request_id` vergleicht und der Manifestvalidator für sechs Zeilen nur Feldlängen prüft, bliebe ein falscher Writerschemadigest unbemerkt, und die Evidenzklasse `live_canary` verlöre genau die Bindung, die Slice 05 ihr gegeben hat

PRE_MORTEM: In drei Monaten ist die wahrscheinlichste Fehlerursache, dass die Reihenfolge im nativen Livepfad bei einer Umstrukturierung unbemerkt kippt. Die gesamte Trennung zwischen diagnostischer Quarantäne und autoritativem Resultat ruht darauf, dass `validate_native_*_provider_response()` vor und `parse_bound_native_*()` nach dem `validated_response_callback` steht — drei Zeilen in zwei Funktionen, deren Reihenfolge kein Test als solche prüft. Verschiebt jemand den Callback beim Refactoring nach vorn, um „früher zu persistieren", bleiben alle Tests grün, die auf writer-invalide Bytes zielen, weil deren Ablehnung dann immer noch vor der Persistierung greift — der Unterschied fiele erst auf, wenn ein Provider erstmals writer-invalides JSON liefert und dessen Bytes als validierte Rohantwort im Artefaktspeicher stünden. Die zweite, leisere Variante betrifft die Canary-Evidenz: Wird eine Zeile künftig ergänzt oder ersetzt, deckt die Rekonstruktionsregression nur die `request_id` ab, sodass ein von Hand eingetragener Writerschemadigest für sechs der sieben Zeilen unbemerkt falsch bleiben kann und das Manifest wieder Buchführung statt Bindung wäre.

FINAL_APPROVAL: YES

STATUS: DONE
