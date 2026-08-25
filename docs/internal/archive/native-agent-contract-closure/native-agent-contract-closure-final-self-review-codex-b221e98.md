# Abschließendes Codex-Selbstreview – Native Agent Contract Closure

**Reviewer:** Codex

**Reviewdatum:** 25. August 2026

**Reviewbasis:** `c410eef` (`merge: complete native finding transition identity`)

**Reviewstand:** `b221e98` (`test(native-contracts): bind live canary schema digests`)

**Reviewmodus:** Manuelles, adversariales und providerfreies Gesamtreview
außerhalb von `run_task`. Die vollständige Repositorysuite und externe
Provider-Canaries wurden nicht erneut ausgeführt. Dieses Dokument ist ein
eigenständiger Bericht und verändert oder ersetzt kein früheres Review.

## 1. Prüfgegenstand und Methodik

Geprüft wurde der vollständige Diff `c410eef..b221e98` mit 39 geänderten
Dateien. Er umfasst die request-spezifischen Codex- und Claude-Writerschemas,
Capability- und Ausnahmeregister, Schema-/Request-/Bundlebindung, native
Adapter- und Runtimegrenzen, Corpus- und Differentialnachweise, das isolierte
Canarywerkzeug sowie sechs dokumentierte Korrekturslices.

Die Prüfung erfolgte in fünf Schritten:

1. Abgleich von Arbeitsplan, sechs Sliceberichten, historischen Gesamt- und
   Slice-Reviews, Roadmap, Implementierung, Schemaquellen und Tests;
2. statische Prüfung aller produktiven Writer-, Request-, Adapter-, Runtime-
   und Recoveryaufrufe;
3. Gegenprüfung der lokalen Findingübergänge gegen die konkreten
   Writerschemaäste;
4. providerfreie Falsifikation der Corpus-, Differential- und
   Live-Canary-Bindungen;
5. erneute Disposition aller bisherigen Codex-Befunde `CR-01` bis `CR-07`.

Der Arbeitsbaum war zu Reviewbeginn auf `b221e98` sauber. Claudes parallel
erzeugte Datei
`native-agent-contract-closure-final-review-claude-b221e98.md` wurde erst nach
Abschluss meiner eigenen Code- und Gegenprüfungen als unabhängige
Vergleichsquelle gelesen und von mir nicht verändert.

## 2. Planerfüllung und Architekturgrenze

Der freigegebene Arbeitsplan ist vollständig umgesetzt:

- Die allgemeinen `native-agent-*-result-v1`-Schemas bleiben unveränderte
  Readerverträge für historische Resultate.
- Neue Provideraufrufe erhalten aus dem vollständigen typisierten Kontext
  abgeleitete Writerschemas.
- Codex besitzt getrennte Writerformen für Plan, Implementierung, Korrektur und
  Finalbericht; Claude besitzt Plan, initialen Slice, Konvergenz und
  Finalreview.
- Requestdokument, Writerschemabytes, `response_contract.schema_sha256`,
  Requestdigest, Request-ID und gebundener Kontext werden in beiden
  Bundletypen gegenseitig validiert.
- Capability- und Ausnahmeregister sind versioniert, fail-closed geladen und
  an exakte Transportprofile sowie benannte Regressionstests gebunden.
- Antigravity, Textmarkerreader, Workflowentscheidung und
  Structured-Artifact-Autorität wurden durch Contract Closure nicht
  umdefiniert.

Die Reader-Baselines wurden im Paket nicht verändert. Vertragsgeschlossenheit
wurde damit durch Verengung der Providerwriter und zusätzliche lokale
Bindungsprüfungen erreicht, nicht durch eine stille Lockerung historischer
Domänenregeln.

## 3. Writerschema und lokale Domäne

### 3.1 Codex

`native_codex_provider_response_schema()` wählt den Ergebnisast ausschließlich
aus `NativeCodexRequestKind`, bindet alle offenen Findings über exakte
Mitgliedschaft und Kardinalität, erzwingt neue Plan-Dispositionen und bildet
Testdatei- sowie Readinessregeln request-spezifisch ab. Das allgemeine
Leseschema bleibt davon durch defensive Kopie getrennt.

Writer-valid lokal erreichbar bleiben nur die im Provider-Subset nicht sicher
ausdrückbaren Invarianten: Reihenfolge und Eindeutigkeit von Dispositionen,
kanonische Pfad-/Arrayreihenfolge, Slice-ID-Kontiguität, NUL-/Blanktext und die
zyklische Request-ID-Gleichheit. Sie sind im Ausnahmeregister typisiert und
durch reale Regressionen belegt.

### 3.2 Claude

`native_review_provider_response_schema()` leitet die vier Writerformen aus
`approval_marker`, Rundenpolicy, Observationpolicy, Attestierung,
Testfreigabe, Anchororigin und dem vollständigen bisherigen Findingzustand ab.
Statusänderungen und Reklassifizierungen referenzieren ausschließlich eigene
offene Findings. Geschlossene, fremde und unbekannte IDs sind bereits im
Writer und nochmals lokal unzulässig.

Die Entscheidungsmatrix bleibt geschlossen:

- Freigaben benötigen konkrete Reviewevidenz, nichtleeres Pre-Mortem,
  vollständige eigene Findingdispositionen, eine vollständige grüne
  Attestierung und gegebenenfalls Testfreigabe.
- Ein Denial hinterlässt einen eigenen offenen Blocker.
- Korrektur- und Finalrunden können keine Observation neu erzeugen oder durch
  Reklassifizierung herstellen.
- Ein Denial kann seinen Pflichtblocker weder durch Wiederöffnen noch durch
  Reklassifizieren eines abgeschlossenen Findings erschleichen.
- Ein Finalreview lässt kein eigenes Finding offen; Antigravitys bestehende
  strengere Null-Findings-Regel bleibt erhalten.

Die lokale zweite Schutzschicht bleibt aktiv. Writer-valide Dokumente können
dort nur an explizit registrierten, vom Provider-Subset nicht ausdrückbaren
Restinvarianten scheitern. Die Negativkontrollen erkennen zusätzlich stille
gemeinsame Überpermissivität, sodass Mengengleichheit über Ablehnungscodes
nicht die einzige Prüfrichtung ist.

## 4. Request-, Bundle- und Runtimebindung

Beide Bundletypen prüfen fail-closed:

- kanonische Requestbytes und daraus berechneten Requestdigest;
- exakte Request-ID;
- vollständige Projektion des gebundenen Domänenkontexts;
- kanonische Writerschemabytes und deren erneute Ableitung aus dem Kontext;
- `response_contract.schema_sha256` gegen exakt diese Bytes;
- Inline-Evidenz sowie `content_ref`-Assets auf Pfad, Bytezahl, SHA-256,
  Eindeutigkeit und Vollständigkeit;
- beim Claude-Repair zusätzlich Parentrequest, Fingerprint, Kontext und
  unveränderten Responsevertrag.

Alle fünf produktiven `parse_bound_native_*`-Aufrufstellen besitzen unmittelbar
zuvor eine request-spezifische Writerprüfung. Die Runtime-Reihenfolge lautet:

1. allgemeines Leseschema;
2. konkretes Writerschema;
3. exakte Request-ID;
4. diagnostische Rohantwortsicherung;
5. gebundene Domänenkonvertierung;
6. erst danach autoritative Resultatpersistenz.

Writer- oder request-invalide Bytes erreichen weder den diagnostischen
Validiert-Callback noch ein autoritatives Resultat. Writer-valide, aber an
einer registrierten lokalen Restinvariante scheiternde Bytes bleiben dagegen
für Diagnose und Recovery verfügbar, ohne fachliche Autorität zu erhalten.
Recovery rekonstruiert Writer und Kontext erneut und akzeptiert keine bloßen
Readerresultate.

## 5. Finding- und Konvergenzinvarianten

Die vier adversarialen Klassen wurden in Writer und lokaler Domäne geprüft:

1. Mutation eines geschlossenen eigenen Findings;
2. Mutation eines fremden Findings;
3. Observationserzeugung oder -reklassifizierung in einer verbotenen Runde;
4. Blocker-Erschleichung durch Wiederöffnung, Reklassifizierung oder
   unvollständige Disposition.

Kein Kandidat wird von beiden Schichten akzeptiert. Die Korrektur aus Slice 04
verengt Status- und Reklassifizierungsziele auf `own_open_ids` und besitzt
zusätzlich den lokalen Vorzustandswächter
`FINDING_REFERENCE_NOT_OPEN`. Slice 05 erzwingt dieselben Writerregeln im Live-
und Recoverypfad. Es verbleibt kein ausführbarer Findingtransition-Defekt.

## 6. Corpus und Differentialnachweise

Der Corpus weist unverändert aus:

- 14 gefundene und 14 übernommene native Antworten;
- zwölf kanonische historische Requests;
- drei `request_bound`-Paare;
- elf `schema_only`-Antworten;
- acht Writerformen;
- sieben `live_canary`- und einen `request_bound`-Writerformnachweis;
- 13 registrierte Providerausnahmen.

Fixture-ID, Provider, Sourcepath, Antwortbytes, SHA-256 und Bindungsklasse sind
gekoppelt. Archivstämmige Bytes werden gegen den Arbeitsbaum geprüft. Die
Inventur leitet vorhandene Archivantworten aus dem realen Repository ab und
scheitert bei Differenzen in beide Richtungen. Der rekursive Canaryabdruck
deckt `.orchestrator/`, `inbox/` und `outbox/` einschließlich fehlender
Wurzeln, Symlinktypen und Symlinkzielen ab, ohne Links zu verfolgen.

Die Differentialtests belegen einerseits, dass alle acht Writerformen ein
zulässiges lokales Resultat darstellen können, und andererseits, dass eine
absichtlich gelockerte Writerregel als nicht registrierter lokaler Fehler
auffällt. Separate Verbotsmatrizen prüfen die Überpermissivitätsrichtung.

## 7. Slice 06 und Disposition von `CR-07`

`CR-07` ist **geschlossen**.

Für jede der sieben `live_canary`-Zeilen rekonstruiert
`_validate_writer_form_evidence()` mit dem kanonischen Codex- beziehungsweise
Claude-Bundlebuilder aus dem in genau dieser Zeile gespeicherten
`base_commit`:

1. die exakte Request-ID;
2. SHA-256 der exakt serialisierten `provider_response_schema_json`-Bytes.

Beide Werte müssen bitgenau mit dem Manifest übereinstimmen. Der lebende
Repository-`HEAD` wird für diese historische Gleichheitsbehauptung nicht
verwendet. Eine zweite positive Prüfung führt denselben Vergleich unter
absichtlich abweichendem `_head()` durch.

Die siebenfache Negativmatrix ändert pro Fall ausschließlich
`writer_schema_sha256`. Jeder syntaktisch gültige 64-Hex-Digest wird erst an
der neuen Inhaltsgleichheit abgewiesen. Damit kann weder ein Fantasiedigest
noch ein echter Digest einer anderen Writerform mit einer sachlich richtigen
Request-ID kombiniert und als geschlossene Live-Evidenz gezählt werden.

Slice 06 verändert keinen Produktiv-, Adapter-, Runtime-, Schema-, Fixture-
oder Manifestwert. Er verstärkt ausschließlich den providerfreien Nachweis.

## 8. Disposition früherer Codex-Befunde

| Befund | Endstatus | Begründung |
|---|---|---|
| `CR-01` | CLOSED | Geschlossene Findings sind in Writer und lokaler Domäne unveränderlich (`C-29`). |
| `CR-02` | CLOSED / ursprüngliche Forderung verworfen | Der freigegebene Plan unterscheidet korrekt zwischen rekonstruiertem `request_bound`-Nachweis und Live-Transportnachweis; der tatsächlich fehlende Konvergenz-Canary wurde ergänzt. |
| `CR-03` | CLOSED | Normative Plan- und Roadmaptexte sind mit der akzeptierten C-25-Transportprofilgrenze synchronisiert. |
| `CR-04` | CLOSED | Live-, Recovery- und Pre-Policy-Pfade erzwingen das konkrete Writerschema vor Domänenkonvertierung. |
| `CR-05` | CLOSED | Requestbytes, Kontext, Responsevertrag und Evidenzassets sind in beiden Bundletypen vollständig gekoppelt. |
| `CR-06` / `C-30` | CLOSED | Canary-Requests werden aus persistiertem `base_commit` statt lebendem HEAD rekonstruiert. |
| `CR-07` | CLOSED | Alle sieben Canary-Writerschemadigests sind bitgenau rekonstruiert und siebenfach negativ abgesichert. |

Alle Claude-Findings `C-01` bis `C-32` besitzen in den historischen
Reviewdokumenten einen späteren gültigen Schließungsnachweis. Die slicelokalen
`B-*`- und `O-*`-Befunde sind ebenfalls geschlossen. In diesem Selbstreview
wurde kein neuer ausführbarer Defekt gefunden.

## 9. Validierung

Vorliegende gebundene beziehungsweise dokumentierte Validierung:

- vollständige Repositorymatrix nach Slice 06:
  `1287 passed in 198.03s`;
- fokussierte Corpus-/Differential-/Canarymatrix dieses Reviews:
  `19 passed in 2.12s`;
- fokussierte Writer-, Ausnahmen-, Bundle- und Runtime-Reihenfolgematrix dieses
  Reviews:
  `10 passed in 1.01s`;
- fokussierte Slice-06-Matrix aus der Implementierung:
  `14 passed in 1.98s`;
- fokussierte Slice-06-Matrix aus Claudes Slicereview:
  `14 passed in 2.07s`;
- `git diff c410eef..b221e98 --check`: sauber;
- externe Providerstarts in diesem Selbstreview: keine.

Die vollständige Suite wurde nicht erneut ausgeführt. Die fokussierten Läufe
waren reine providerfreie Falsifikationen und veränderten den Arbeitsbaum
nicht.

## 10. Restrisiken ohne ausführbaren Befund

1. Die sieben Live-Canary-Nachweise laden zwei private Builderfunktionen aus
   `scripts/native_contract_probe.py` per `importlib`. Eine spätere
   Signaturänderung muss fail-closed bleiben und darf den expliziten
   `base_commit` nicht durch den `_head()`-Fallback ersetzen.
2. Die sieben `response_sha256`-Werte der Live-Canaries sind ohne zusätzlich
   gespeicherte Antwortbytes lokal nicht neu berechenbar. Sie sind daher
   Transportprotokoll, während Request-ID und Writerschemadigest die lokal
   rekonstruierbare Vertragsbindung tragen.
3. Der `.orchestrator`-stämmige Teil der historischen Corpusinventur ist nicht
   aus einem frischen Klon ableitbar. Das versionierte Corpus bleibt lesbar und
   digestgebunden, seine Vollständigkeitsbehauptung bezieht sich jedoch auf den
   inventarisierten Arbeitsbaum.
4. Provider-CLI-, Modell- oder Transportprofiländerungen entwerten die
   Capability-Tabelle absichtlich fail-closed und erfordern neue isolierte
   Sonden. Unter Zeitdruck darf diese Grenze nicht durch manuelles Nachziehen
   der Tabelle umgangen werden.

Keines dieser Risiken beschreibt einen gegenwärtig ausführbaren Defekt oder
eine unerfüllte Akzeptanzanforderung.

## 11. Pre-Mortem

Die wahrscheinlichste Ursache eines Fehlers in drei Monaten ist ein scheinbar
harmloses Refactoring des Canarywerkzeugs. Werden die privaten Bundlebuilder
umbenannt oder ihr `base_commit`-Parameter entfernt, während der `_head()`-
Fallback bestehen bleibt, kann die historische Rekonstruktion wieder an den
lebenden Repositoryzustand gekoppelt werden. Der gute Fehlerfall ist ein
sofort roter Test; der gefährliche organisatorische Fehler wäre, daraufhin die
Manifestwerte auf den aktuellen HEAD nachzuziehen. Damit würden `C-30` und
`CR-07` gemeinsam zurückkehren und `live_canary` wieder von einer Bindung zu
reiner Buchführung degradieren.

Die zweite plausible Ursache ist eine neue Provider-CLI-Version, deren
Structured-Output-Subset vom gespeicherten Profil abweicht. Wird die
Capability-Prüfung dann aus Bequemlichkeit gelockert statt die isolierten
Sonden erneut auszuführen, können erneut provider-schema-valide Antworten erst
lokal scheitern und kostspielige Wiederholungsschleifen auslösen.

## 12. Gesamturteil

Am Abschlussstand `b221e98` ist kein ausführbarer Defekt im freigegebenen
Contract-Closure-Umfang verblieben. `CR-07` ist vollständig geschlossen; die
Vertragsgrenzen, Corpusnachweise und Recoverypfade bleiben nach Slice 06
konsistent und fail-closed.

Codex erteilt gemäß Projektregel keine Freigabe für die eigene Implementierung.
Dieses Selbstreview liefert daher kein `FINAL_APPROVAL`. Es stellt fest, dass
aus Codex-Sicht keine weitere Korrekturrunde erforderlich ist und der Stand
für die unabhängige Abschlussentscheidung bereit ist.

REVIEW_EVIDENCE: Vollständiger Diff `c410eef..b221e98`; Plan- und Dokumentationsabgleich über sechs Slices; request-spezifische Writerschemas und lokale Domäne beider Provider in beide Richtungen geprüft; alle fünf produktiven gebundenen Parserpfade mit Writer-vor-Domäne-Reihenfolge; Bundlebindung von Kontext, Requestbytes, Responsevertrag und Assets; Findingtransitionen einschließlich geschlossener und fremder Ziele sowie Blocker-Erschleichung; Corpusinventur, Ausnahmeregister und acht Writerformen; sieben Live-Canary-Tripel aus eingefrorenem Basiscommit, Request-ID und Writerschemadigest; siebenfache Digestmutation; providerfreie fokussierte Matrizen mit 19 und 10 bestandenen Tests | Private Probe-Builder bleiben eine testseitige API, Live-Canary-Antwortbytes sind nicht versioniert und der lokale `.orchestrator`-Bestand ist nicht klonbar | Ein Refactoring entfernt den expliziten Basiscommit aus der Canaryrekonstruktion und ein nachfolgendes Nachziehen der Manifestwerte kaschiert den dadurch wieder eingeführten HEAD-Drift

PRE_MORTEM: In drei Monaten wird am ehesten das Canarywerkzeug refaktoriert, der explizite historische `base_commit` geht verloren, und ein roter Evidenztest wird fälschlich durch Aktualisieren des Manifests statt durch Wiederherstellen der Bindung repariert.

CODEX_SELF_REVIEW_RESULT: NO_EXECUTABLE_DEFECT_FOUND

STATUS: DONE
