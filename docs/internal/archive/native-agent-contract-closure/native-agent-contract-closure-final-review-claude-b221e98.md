REVIEWER: claude

# Branchweites Abschlussreview – Native Agent Contract Closure (`b221e98`)

**Reviewer:** Claude (Sonnet, Effort `high`) · **Datum:** 25. August 2026 ·
**Modus:** manuell, read-only, adversarial, außerhalb von `run_task`

Dieses Dokument ist ein eigenständiger Bericht. Es hängt an keinem früheren
Review, verändert keines und ersetzt keines. Die vorangegangenen Gesamt- und
Slice-Reviews bleiben als historische Befundquellen unverändert erhalten:

| Dokument | Gegenstand | Ergebnis |
|---|---|---|
| `…-final-review-claude.md` | `7ba867d` und `286f5b2` | `YES` (später durch `CR-01` widerlegt), dann `NO` wegen `C-30` |
| `…-final-review-claude-06819bd.md` | `06819bd` | `YES` |
| `…-final-self-review-codex.md`, `…-codex-06819bd.md` | Codex-Selbstreviews | `CR-01` bis `CR-07` |
| `…-slice-06-review-claude.md` | Slice 06 | `SLICE_APPROVAL: 06 \| YES` |

Eine frühere Freigabe ersetzt dieses Review nicht. Sämtliche Aussagen unten
sind am aktuellen Code und am vollständigen Diff unabhängig nachgerechnet.

## 1. Prüfgegenstand

- Basis vor Contract Closure: `c410eef`
- Abschlussstand: `b221e98` (`test(native-contracts): bind live canary schema digests`)
- Branch-Diff: 39 Dateien, 12.793 hinzugefügte und 196 entfernte Zeilen
- Arbeitsbaum zu Beginn sauber; die Zieldatei dieses Berichts existierte nicht

**Kein Drift nach meiner Slice-06-Freigabe.** `06819bd..b221e98` umfasst exakt
die von mir geprüften Teständerungen (`test_native_contract_corpus.py` +56,
`test_native_contract_probe.py` +4), die Roadmapfortschreibung und die vier
Reviewdokumente. Es wurde kein Produktivcode, kein Schema, kein Fixture und
kein Manifestwert angefasst.

Die vollständige Suite wurde nicht erneut ausgeführt und kein externer Canary
wiederholt. Ausgeführt wurden ausschließlich providerfreie Gegenproben.

## 2. Vertragsgeschlossenheit — Breitensuche am Abschlussstand

Die tragende Aussage des Arbeitspakets habe ich nicht aus den Sliceprotokollen
übernommen, sondern neu gemessen: **5.985 Antwortkandidaten** über eine Matrix
aus neun Vorbefundmengen (leer, offener Blocker, offene Observation,
geschlossener Blocker, geschlossene Observation, gemischt geschlossen/offen,
zwei offene, fremdes `A-`Finding, gemischt fremd/eigen) × drei Approvalmarker ×
Initialrunde, Konvergenzrunde und Konvergenz mit gesetzter Observationpolicy ×
fünf Attestierungs-, Testfreigabe- und Anchorzustände. Mutiert wurde über
Statusänderungen nach `OPEN`/`CLOSED` und Reklassifizierungen nach
`BLOCKER`/`OBSERVATION` in Freigabe- **und** Ablehnungsast, neue Findings beider
Klassen, Anchors ohne Origin, Blank-Evidenz und NUL-Texte.

Ergebnis:

- hinter writer-validem JSON lokal erreichbar: ausschließlich
  `approval-invalid` — **kein unregistrierter Fehlercode**;
- **keine stille Überpermissivität** in vier gesondert geprüften Klassen:
  geschlossenes oder fremdes Findingziel, neue Observation in Final- oder
  Konvergenzrunde, Reklassifizierung zu Observation dort, Anchor ohne
  gebundene Origin;
- alle vier Claude-Writerformen (`plan`, `slice_initial`, `slice_convergence`,
  `final`) entstehen unverändert.

Damit gilt die Kernbehauptung in beide Richtungen: Writer-valides JSON scheitert
lokal nur an registrierten Ausnahmen, und fachlich unzulässiges JSON wird nicht
von beiden Schichten zugleich akzeptiert.

**Keine stille Lockerung.** Die beiden Resultat-v1-Reader-Baselines sind über
den gesamten Paketstand `c410eef..b221e98` bytegleich unverändert. Das
Providerausnahmeregister führt unverändert 13 Einträge (sieben Codex, sechs
Claude), und **jeder** benennt eine tatsächlich existierende
Regressionstestfunktion — vollständig nachgeprüft, keine Lücke. Die in den
Korrekturslices eingeführten Codes `finding-reference-not-open`,
`request-invalid` und `evidence-invalid` sind hinter writer-validen Dokumenten
nicht erreichbar und daher zu Recht nicht registriert. Die Deckung von
Writerschema und lokaler Prüfung wurde also durch Verschärfung des Writers und
der zweiten Schicht erreicht, nicht durch Aufweichen einer Domänenregel.

## 3. Findings und Konvergenz

Aus der Breitensuche folgt unmittelbar: Ein Reviewer kann ausschließlich eigene
**offene** Findings schließen oder reklassifizieren. Geschlossene und fremde
Findings bleiben in Plan-, Slice-, Korrektur- und Finalreviewform
unveränderlich. Ein Denial kann seinen erforderlichen offenen Blocker weder
durch Wiederöffnen (`C-29`) noch durch Reklassifizieren (`C-31`) noch durch
Auslassen einer Disposition erschleichen. Korrektur- und Finalrunden erzeugen
keine neuen Observations.

Den regulären Finalreviewpfad habe ich über die **produktive** Regel
hergeleitet — `WorkUnitKind.FINAL_REVIEW is not WorkUnitKind.CORRECTION` ergibt
`allow_new_observations=True` — und nicht von Hand gesetzt:

| Antwort im regulären Finalreview | Writer | Lokal |
|---|---|---|
| `BLOCKER C-01 → OBSERVATION` | rejected | `approval-invalid` |
| neue `OBSERVATION` | rejected | `approval-invalid` |
| bestehende `OBSERVATION C-03` disponiert | VALID | ACCEPTED |

Beide Angriffe scheitern auf beiden Schichten; eine bereits bestehende
Observation wird nicht versehentlich mutiert. Eine Freigabe verlangt weiterhin
vollständige Dispositionen, echte Reviewevidenz, ein nichtleeres Pre-Mortem und
einen zulässigen resultierenden Findingzustand.

## 4. Request-, Schema- und Digestbindung

Alle acht direkt konstruierten Gegenbeispiele werden mit unterscheidbaren,
sachlich präzisen Fehlern abgewiesen — konstruiert über die Bundlekonstruktoren,
nicht über die stets konsistenten Builder:

| Konstruktion | Ergebnis |
|---|---|
| Claude-Kontextdrift bei unveränderten Requestbytes | `request-invalid: request document differs from its bound review context` |
| Claude-Bundle mit fremdem Writerschema | `request-invalid: bundle provider response schema differs …` |
| Repairbundle ohne Parent | `repair-invalid: repair request requires its immutable parent bundle` |
| Codex-gefälschte kanonische Bytes, originale `request_id` | `request-invalid: request content differs from its bound digest` |
| Codex-`content_ref` ohne Assets | `evidence-invalid: request evidence assets differ from content references` |
| Codex-Asset doppelt | `evidence-invalid: request evidence asset paths must be unique` |
| Codex-Asset inhaltlich verändert | `evidence-invalid: evidence asset digest differs from content` |
| Codex-Asset mit vertauschtem Pfad | `evidence-invalid: evidence asset path must use the native …` |

Historische Requests können nicht gegen einen fremden oder aktuellen Kontext
akzeptiert werden: Die drei `request_bound`-Paare durchlaufen weiterhin
Writerschema **vor** Domänenkonvertierung, reproduzieren ihren Manifestdigest
aus dem jeweils historischen Kontext und tragen sichtbar den alten
ursprünglichen `response_contract.schema_sha256` `31510b2d…`, der planmäßig
nicht als Live-Transportnachweis ausgegeben wird. `schema_only`,
`request_bound` und `live_canary` bleiben semantisch getrennt; `base_commit`
erscheint ausschließlich in `live_canary`-Zeilen. Historische Rohantworten und
Manifestwerte sind unverändert.

## 5. Corpus und Differentialnachweise

- Inventur: 14 gefundene und 14 übernommene Antworten, zwölf kanonische
  Requests, drei `request_bound`-Paare, elf `schema_only`, drei technische
  Failure-Envelopes.
- Meine unabhängige Neuinventur ergibt **leere Differenz in beide Richtungen**;
  die Archivseite wird dabei aus dem realen Arbeitsbaum abgeleitet, nicht
  behauptet.
- Für jedes Fixture sind `fixture_id`, Bytes, Sourcepath, SHA-256,
  Bindungsklasse und Provider gekoppelt: **null Kopplungsfehler**, und jede
  Antwort ist gegen ihr allgemeines Leseschema lesbar.
- Die Differentialmatrix erkennt beide Richtungen: unerwartete lokale
  Ablehnungen über die beidseitige Mengengleichheit gegen das Register und
  gemeinsam akzeptierte unzulässige Dokumente über die seit Slice 04 geführten
  Verbotsklassen.
- Geschützte Workflowstores: Der rekursive Abdruck über `.orchestrator/`,
  `inbox/` und `outbox/` erfasst fehlende Wurzeln als eigenen Eintrag, verfolgt
  Symlinks wegen `lstat()` nicht, nimmt Linkziele explizit auf und wird in
  beiden `main()`-Ästen fail-closed vor und nach dem Aufruf verglichen.

## 6. Canary- und Transportevidenz

Acht Writerformen, davon sieben `live_canary` und ein `request_bound`
(`claude/initial_slice`). Jede `live_canary`-Zeile habe ich als **Tripel** aus
`base_commit`, Request-ID und Writerschemadigest rekonstruiert:

| Zeile | base_commit | ≠ HEAD | request_id | Schemadigest | `response_contract` |
|---|---|---|---|---|---|
| codex/plan | `7ba867dd24` | ja | reproduziert | reproduziert | == Digest |
| codex/implementation | `7ba867dd24` | ja | reproduziert | reproduziert | == Digest |
| codex/correction | `7ba867dd24` | ja | reproduziert | reproduziert | == Digest |
| codex/final_report | `7ba867dd24` | ja | reproduziert | reproduziert | == Digest |
| claude/plan | `7ba867dd24` | ja | reproduziert | reproduziert | == Digest |
| claude/convergence | `eb38a316a7` | ja | reproduziert | reproduziert | == Digest |
| claude/final | `7ba867dd24` | ja | reproduziert | reproduziert | == Digest |

Jeder eingefrorene Commit weicht vom lebenden `HEAD` (`b221e98`) ab — die
Reproduktion belegt HEAD-Unabhängigkeit und ist kein Zufall.

**Mutationsmatrix.** Ich habe den Writerschemadigest jeder der sieben Zeilen
einzeln auf einen syntaktisch gültigen 64-Hex-Wert gesetzt: **7 von 7** fallen
fail-closed auf. Zusätzlich habe ich die Digests zweier realer Formen
(`codex/plan` ↔ `claude/final`) gegeneinander getauscht, also **echte** Digests
an der falschen Zeile platziert — auch das wird abgewiesen. Es handelt sich
damit um echte Inhaltsgleichheit, nicht um eine Struktur- oder Längenprüfung.

**Rückfall auf den lebenden HEAD ausgeschlossen.** Das war meine wichtigste
Sorge, weil die Evidenzprüfung seit Slice 06 einen Bundlebuilder aufruft, der
ohne expliziten `base_commit` auf `_head()` zurückfiele. Ich habe `_head` im
Probe-Modul so ersetzt, dass jeder Aufruf sofort fehlschlägt, und die
vollständige Evidenzprüfung über das gesamte Manifest laufen lassen: Sie läuft
**ohne einen einzigen `_head`-Aufruf** durch. Der Kurzschluss
`base_commit or _head(...)` greift; `C-30` ist nicht regressiert.

**Zu `CR-07`.** Der Codex-Befund ist kein Claude-Finding und wird hier
folgerichtig nicht per `FINDING_STATUS` geschlossen. Sachlich ist er durch
Slice 06 vollständig beseitigt: Die zuvor nur längengeprüften sechs
Nicht-`convergence`-Digests sind jetzt in `_validate_writer_form_evidence()`
inhaltlich gebunden, zusätzlich im HEAD-Drifttest unter absichtlich falschem
`HEAD`, und die siebenfache Negativmatrix scheitert nachweislich an der
Digestassertion selbst, nicht an einem vorgelagerten Strukturcheck. Der Befund
entspricht dem Restrisiko, das ich in meinem Gesamtreview zu `06819bd` als
`REVIEW_EVIDENCE` dokumentiert und dessen Manipulationspfad ich dort
reproduziert hatte; die Differenz zwischen Codex und mir betraf die Schwere,
nicht den Sachverhalt. Die Härtung schließt exakt die dort benannte
Bruchbedingung.

## 7. Live-, Record-Ahead- und Recoveryautorität

Die Reihenfolge ist für beide Provider identisch und vollständig:
Reader-Baseline → **konkretes requestgebundenes Writerschema** → Request-ID →
diagnostische Rohantwortsicherung → gebundene Domänenkonvertierung
(`src/agent_runtime.py:1308-1317` und `:1478-1487`).

Die Vollständigkeit habe ich über alle Aufrufstellen geprüft: Es existieren
genau **fünf** produktive `parse_bound_native_*`-Aufrufe, und jedem geht
unmittelbar eine Writerprüfung voraus — `agent_runtime.py:1309/1317` und
`:1479/1487`, `orchestrator.py:1051/1052`, `:1400/1401` und `:1611/1614`. Es
bleibt keine ungeschützte Domänenkonvertierung übrig.

Weil die Writerprüfung **vor**, die Domänenkonvertierung aber **nach** dem
`validated_response_callback` liegt, erreichen writer- oder request-invalide
Bytes weder ein autoritatives Resultat noch den Callback, während writer-valide
und lokal an einer registrierten Ausnahme scheiternde Bytes weiterhin
diagnostisch gesichert werden, ohne fachliche Autorität zu erhalten. Die
Raw-first-Crashsicherheit bleibt damit erhalten.

`src/workflow.py` und `src/artifact_models.py` sind vom gesamten Paket
unberührt; Record-Autorität, Replay, Resume und State-Mirror bleiben
strukturell auf dem freigegebenen Stand, ergänzt um die drei
Writerprüfungen in den Recoverypfaden. Die Legacyparser-Sprengfallen sind an
sieben Stellen aktiv; ein Rückfall auf Textmarkerparser oder textuelle
Contract-Repair ist im nativen Pfad ausgeschlossen.

## 8. Findingledger

Ich habe für jede ID den letzten gültigen Statusmarker über alle
Paketdokumente ausgewertet:

| Findings | Herkunft | Endstatus |
|---|---|---|
| `C-01` – `C-25` | Planreview, fünf Revisionen | alle CLOSED |
| `C-26`, `C-27`, `C-28` | Slice-03-Review | CLOSED |
| `C-29` | Slice-04-Review (entspricht `CR-01`) | CLOSED |
| `C-30`, `C-31`, `C-32` | Slice-05-Planreview | CLOSED |
| `B-01`, `B-02`, `B-03`, `O-01` – `O-06` | slicelokale Bezeichner | CLOSED in ihren Protokollen |

`C-29` trägt keinen `NEW_FINDING`-Marker, weil ich die ID während der Analyse
von Codex' `CR-01` vergeben und sie im Slice-04-Review unmittelbar als
geschlossen protokolliert habe; das ist dort begründet. Die drei historischen
`OPEN`-Marker (`C-09`, `C-21`, `C-30`) sind Zwischenstände und jeweils später
durch einen `CLOSED`-Marker abgelöst. `C-99` erscheint ausschließlich als
Beispiel-ID in einem Slice-02-Gegenbeispiel und ist kein Finding. `C-33` kommt
im Paket nur als meine eigene Prosanotiz „nächste freie ID" vor und ist frei.

Die Codex-Bezeichner sind disponiert: `CR-01` = `C-29`, `CR-02` begründet
verworfen, `CR-03` in Slice 05 dokumentarisch geschlossen, `CR-04` bis `CR-06`
durch Slice 05 implementiert, `CR-07` durch Slice 06.

**Es bleibt kein Finding irgendeines Reviewers offen.**

## 9. Plan-, Slice- und Dokumentationskonsistenz

Arbeitsplan, alle sechs Slice-Berichte, beide Codex-Selbstreviews, meine drei
Gesamt- und Slice-Reviews, Roadmap, Implementierung, Schemaquellen,
Corpusmanifest und Tests treffen dieselben Aussagen: 14 Antworten, zwölf
Requests, drei `request_bound`-Paare, elf `schema_only`, acht Writerformen,
sieben `live_canary` zu einem `request_bound`, 13 Ausnahmeeinträge, sechs
Korrekturslices. Historische Ablehnungen bleiben als Auditspur stehen; die
jeweils spätere Schließung ist eindeutig und maßgeblich.

## 10. Urteil

Das Arbeitspaket erreicht seine Kerninvariante am Abschlussstand nachweisbar:
Jede durch das konkrete Provider-Schema darstellbare Antwort bildet entweder
erfolgreich den gebundenen lokalen Domänenvertrag oder scheitert ausschließlich
an einer der 13 typisierten, providerbelegten und regressionsgesicherten
Restinvarianten. Der Writer wird seit Slice 05 auch lokal als Autorität
durchgesetzt, die Requestbindung ist über Bytes, Digest, Kontext,
Responsevertrag und Evidenzassets geschlossen, und die Transportevidenz ist
seit Slice 06 als Tripel commitstabil und manipulationsfest gebunden.

REVIEW_EVIDENCE: Vertragsgeschlossenheit beider Providerpfade über 5.985 Antwortkandidaten in einer Matrix aus neun Vorbefundmengen, drei Approvalmarkern, drei Rundenpolicies und fünf Attestierungs-/Anchorzuständen, geprüft in beide Richtungen auf unregistrierte Fehlercodes und auf vier Klassen stiller Überpermissivität; Bytegleichheit beider Reader-Baselines über den gesamten Paketstand und Unverändertheit des 13-Einträge-Registers samt Existenznachweis jeder benannten Regression; Vollständigkeit der Writerautorität über alle fünf produktiven `parse_bound_native_*`-Aufrufstellen und Reihenfolge Reader → Writer → Request-ID → diagnostische Sicherung → Domäne; Unberührtheit von `workflow.py` und `artifact_models.py` sowie sieben aktive Legacyparser-Sprengfallen; Unveränderlichkeit geschlossener und fremder Findings und Unmöglichkeit des Blocker-Erschleichens durch Wiederöffnen, Reklassifizieren oder Auslassen; Finalreview-Invariante über die produktive Regel `unit.kind is not WorkUnitKind.CORRECTION`; Abweisung von acht direkt konstruierten Bundle-, Repair- und Assetgegenbeispielen; Corpusinventur mit leerer Differenz in beide Richtungen und null Kopplungsfehlern; Trennung von `schema_only`, `request_bound` und `live_canary`; Rekonstruktion aller sieben Canary-Zeilen als Tripel aus eingefrorenem `base_commit`, Request-ID und Schemadigest bei abweichendem HEAD; siebenfache Digestmutation und Cross-Form-Swap echter Digests jeweils fail-closed; Nachweis durch Sabotage von `_head`, dass die historische Rekonstruktion den lebenden HEAD nicht berührt; fail-closed Integritätsprüfung der gitignorierten Workflowstores einschließlich Symlinks und fehlender Wurzeln; vollständige Auswertung des Findingledgers über alle Paketdokumente | Zwei nicht ausführbare Restrisiken: (1) Die gesamte Evidenzlast der sieben Writerformnachweise hängt seit Slice 06 an zwei privaten Hilfsfunktionen eines Werkzeugskripts (`_codex_canary_bundle`, `_claude_canary_bundle`), die die Testsuite per `importlib` lädt; eine Signaturänderung oder ein Wegfall des `base_commit`-Parameters bei belassenem `_head()`-Fallback würde die Bindung entweder sichtbar brechen oder still auf den lebenden HEAD zurückführen. (2) Die Trennung zwischen diagnostischer Sicherung und autoritativem Resultat ruht auf der Reihenfolge dreier Zeilen in zwei Funktionen, die kein Test als Reihenfolge prüft; abgesichert ist nur das Ergebnis `persisted == []` für einen writer-invaliden Fall. Ergänzend bleiben die `response_sha256`-Werte der sieben Canaries lokal unverifizierbar und der `.orchestrator`-stämmige Teil der Corpusinventur nicht aus einem frischen Klon ableitbar | Jemand refaktoriert die Canary-Builder, entfernt oder benennt den `base_commit`-Parameter um und belässt den `_head()`-Fallback; die Evidenzprüfung rekonstruiert dann alle sieben Zeilen still gegen den lebenden HEAD, die Suite wird nach dem nächsten Commit rot, und unter Termindruck wird das mit einem Nachziehen der Manifestwerte statt mit einer Ursachenanalyse beantwortet — womit `C-30` und `CR-07` gemeinsam in genau der Prüfung zurückkehren, die sie beweisen sollte

FINDING_STATUS: C-29 | CLOSED | Am Abschlussstand erneut verifiziert. Über 5.985 Antwortkandidaten wird kein Kandidat, dessen Status- oder Reklassifizierungsziel ein geschlossenes, fremdes oder unbekanntes Finding ist, von beiden Schichten akzeptiert; die Verengung auf `own_open_ids` samt Kardinalitätsgrenzen und der lokale Vorzustandswächter mit `finding-reference-not-open` sind unverändert wirksam, und der Code bleibt hinter writer-validem JSON unerreichbar, weshalb das Register korrekt bei 13 Einträgen bleibt.

FINDING_STATUS: C-30 | CLOSED | Am Abschlussstand erneut und schärfer verifiziert. Alle sieben `live_canary`-Zeilen reproduzieren aus ihrem eingefrorenen `base_commit` sowohl Request-ID als auch Writerschemadigest, und jeder dieser Commits weicht vom lebenden HEAD `b221e98` ab. Zusätzlich habe ich `_head` im Probe-Modul sabotiert und die vollständige Evidenzprüfung durchlaufen lassen: kein einziger Aufruf. Die HEAD-Abhängigkeit ist strukturell beseitigt, nicht nur datenseitig korrigiert.

FINDING_STATUS: C-31 | CLOSED | Am Abschlussstand erneut verifiziert. Der Finalkontext wurde über die produktive Regel `WorkUnitKind.FINAL_REVIEW is not WorkUnitKind.CORRECTION` hergeleitet, ergibt `allow_new_observations=True`, und dennoch scheitern sowohl `BLOCKER → OBSERVATION` als auch eine neue Observation am Writerschema und unabhängig davon lokal mit `approval-invalid`; eine bereits bestehende Observation bleibt disponierbar. Roadmap und Arbeitsplan benennen den Pfad als regulär erreichbar.

FINDING_STATUS: C-32 | CLOSED | Am Abschlussstand erneut verifiziert. Der Codex-Request transportiert weiterhin ausschließlich offene Findings, geschlossene werden nicht erfunden, und ihre Autorität liegt in der Workflow-Recordkette, wo ein manipulierter geschlossener Mirrorbestand fail-closed mit `differs from the state-v3 mirror` abgewiesen wird. Die Bundlekopplung ist über die transportierte kanonische Projektion vollständig, und Arbeitsplan wie Roadmap benennen diese Grenze ausdrücklich, sodass keine unerfüllbare Vollständigkeitsbehauptung stehen bleibt.

PRE_MORTEM: In drei Monaten ist die wahrscheinlichste Fehlerursache ein Refactoring des Probe-Werkzeugs. `scripts/native_contract_probe.py` ist nach außen ein Hilfsskript, trägt aber seit Slice 06 über zwei private Funktionen die gesamte Evidenzlast von sieben Writerformnachweisen, die die Testsuite per `importlib` nachlädt. Wer diese Funktionen umbenennt, ihre Signatur ändert oder den `base_commit`-Parameter entfernt und den `base_commit or _head(...)`-Fallback stehen lässt, bekommt keinen Fehler an der Änderungsstelle, sondern eine Evidenzprüfung, die still gegen den lebenden HEAD rekonstruiert. Sichtbar wird das erst beim nächsten Commit, und zwar als roter Test ohne offensichtlichen Zusammenhang zur Ursache — die erfahrungsgemäß naheliegende Reaktion, die Manifestwerte nachzuziehen, würde `C-30` und `CR-07` gemeinsam zurückholen und die Evidenzklasse `live_canary` wieder zu Buchführung machen. Die zweite, leisere Variante betrifft die Reihenfolge im Livepfad: Verschiebt jemand den `validated_response_callback` beim Umbau vor die Writerprüfung, bleiben alle heutigen Tests grün, weil deren Ablehnung dann immer noch vor der Persistierung greift; der Unterschied fiele erst auf, wenn ein Provider erstmals writer-invalides JSON liefert und dessen Bytes als validierte Rohantwort im Artefaktspeicher stünden.

FINAL_APPROVAL: YES

STATUS: DONE
