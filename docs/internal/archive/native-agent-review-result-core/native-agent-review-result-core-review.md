# Review – Nativer Reviewresultat-Kern

## Bootstrapstatus

- Verfahren: manueller Bootstrap außerhalb von `run_task`
- Branch: `feature/native-agent-review-result`
- Plan:
  `docs/internal/native-agent-review-result-core-arbeitsplan.md`
- Plan-SHA-256:
  `56aa380479dfae5cbcf4cc135fcc75f54ff33c11244fa0c302a9174b2219dc3e`
- Claude-Planreview: **APPROVED – REVISION 5**
- Antigravity:
  **NOT_RUN (manual bootstrap exception)**
- Manuelles Agy-Advisory: vom Benutzer außerhalb des Bootstrapverfahrens
  angestoßen; wird als zusätzliche Befundevidenz, nicht als formale Freigabe,
  behandelt.

## Dauerhafte Benutzerfreigabe für notwendige Scopeerweiterungen

Am 22. August 2026 hat der Benutzer für den aktuellen und die folgenden
manuellen Bootstrap-Schnitte ausdrücklich erlaubt, bei fachlicher Notwendigkeit
die geplante Dateianzahl zu überschreiten und neue Dateien anzulegen. Diese
Freigabe ersetzt keine Reviewpflicht: Jede zusätzliche Datei wird begründet,
in die lokale Validierung aufgenommen und Claude im nächsten Review auf dem
eingefrorenen Gesamtstand vorgelegt.

## Implementierungsstand vor Claude-Slicereview

- Implementierungsdigest:
  `4050b2592f68b518ffe6367a7c83767a738913441bda2fda47d066463dee4d77`
- Digestumfang: natives JSON-Schema, providerunabhängiger Domänenkonverter,
  beide neuen Testdateien, freigegebener Arbeitsplan und manueller
  Bootstrapvertrag; das fortgeschriebene Reviewprotokoll selbst ist bewusst
  nicht Bestandteil seines eigenen Digests.
- Fokussierte Regression:
  `160 passed in 2.29s`
- Vollständige Repositorysuite:
  `1047 passed in 183.19s`
- `git diff --check`: bestanden; lediglich der vorhandene Git-Hinweis zur
  zukünftigen LF/CRLF-Normalisierung von `docs/internal/README.md` wurde
  ausgegeben.
- Antigravity:
  **NOT_RUN (manual bootstrap exception)**

## Claude-Implementierungsreview – Runde 1

- geprüfter Implementierungsdigest:
  `4050b2592f68b518ffe6367a7c83767a738913441bda2fda47d066463dee4d77`
- Entscheidung: `CHANGES_REQUIRED`
- Provider: Claude Sonnet, Effort `high`, read-only
- Struktur: ein schema-validiertes JSON-Objekt; keine Textmarkerreparatur und
  kein zweiter Modellaufruf
- Antigravity: `NOT_RUN (manual bootstrap exception)`

### C-01 – BLOCKER – Inhaltsleeres Review im direkten Domänenpfad

`native_response_to_contract_result()` konnte ein direkt konstruiertes
`NativeReviewResult` ohne Findingereignis und ohne Evidenz akzeptieren, weil
diese Invariante nur im vorgelagerten JSON-Schema erzwungen wurde. Damit hätte
ein späterer Adapter den Schutz durch direkte Domänenkonvertierung umgehen
können.

Disposition: behoben. `_validate_response_events()` erzwingt die Invariante
jetzt unabhängig vom Transportschema und liefert stabil
`review-content-missing`. Der direkte Regressionstest konstruiert exakt den von
Claude benannten Bypass und weist die Ablehnung nach.

### C-02 bis C-04 – Observations, lokal bereinigt

- Die generische geschlossene JSON-Schema-Untermenge liegt jetzt in der neuen
  öffentlichen, artifactunabhängigen Datei `src/schema_validation.py`.
  `artifact_models.py` und `native_review_contract.py` verwenden dieselben
  öffentlichen Funktionen und Fehlerklassen; private Querimporte entfallen.
- `FINDING_COMMAND_PREFIX` und `matches_validation_family()` stammen jetzt aus
  der öffentlichen Quelle `validation_matrix.py`.
- Direkt konstruierte ungültige Findinginhalte werden vor dem
  `FindingRecord`-Bau als `finding-content-invalid` klassifiziert und nicht
  länger pauschal als ungültige Finding-ID gemeldet.

Die notwendige Erweiterung um `src/schema_validation.py`,
`tests/test_schema_validation.py`, `src/artifact_models.py` und
`src/validation_matrix.py` stützt sich auf die dokumentierte dauerhafte
Benutzerfreigabe für fachlich notwendige Scopeerweiterungen.

## Lokaler Stand nach C-01-Korrektur

- korrigierter Implementierungsdigest:
  `0d147377ce00d7cd91aef00cc0dba69ed5e834014fdd41091dc50cbb7cf6fdc7`
- native Kerntests: `32 passed in 0.55s`
- vollständige Repositorysuite: `1048 passed in 134.82s`
- `git diff --check`: bestanden; lediglich der vorhandene LF/CRLF-Hinweis für
  `docs/internal/README.md` blieb bestehen.
- erneuter Claude-Aufruf: bewusst ausgesetzt, da das Fünf-Stunden-Kontingent
  zu 98 Prozent verbraucht ist.
- Antigravity: `NOT_RUN (manual bootstrap exception)`

## Manuelles Agy-Advisory – zusätzliche Befunde

Der Benutzer hat außerhalb des vereinbarten Bootstrap-Reviewpfads selbst ein
Agy-Review angestoßen und dessen drei Befunde bereitgestellt. Sie gelten nicht
als Approval, wurden aber als adversarielle Evidenz vollständig geprüft.

### Agy-1 – rohe ValueErrors bei Whitespace-Inhalten

Schema-gültige Whitespace-Evidenz konnte beim Bau von `ReviewEvidence` einen
rohen `ValueError` auslösen; Stopinhalte hatten dieselbe unsaubere Grenze.

Disposition: behoben. Evidenzfehler werden als `review-content-missing`,
ungültige Stopinhalte als `stop-content-invalid` gemeldet. Der direkte
Konverter prüft zusätzlich Nicht-String-Werte, bevor bestehende Domänentypen
aufgerufen werden.

### Agy-2 – falscher Referenzfehler bei leerer Begründung

Eine leere Status- oder Reklassifizierungsbegründung konnte als
`finding-reference-unknown` erscheinen.

Disposition: behoben. Ereignisinhalte werden vor der Referenzanwendung geprüft
und erhalten `finding-content-invalid`; unbekannte oder fremde IDs behalten den
separaten Referenzfehler.

### Agy-3 – Whitespace-Pre-Mortem konnte freigeben

Die positive Entscheidung prüfte nur auf `None`, nicht auf inhaltliche Leere.

Disposition: behoben. Das Pre-Mortem wird als nichtleerer, NUL-freier Text mit
der gebundenen Größenobergrenze validiert; Whitespace und falsche Python-Typen
scheitern typisiert als `approval-invalid`.

## Eingefrorener Korrekturstand für den späteren Claude-Review

- Implementierungsdigest:
  `60b8916c6e834d373e36e11b68ab5353dec16db81289dffde57378c6e946f092`
- Digestumfang: Schema, nativer Domänenkonverter, öffentlicher gemeinsamer
  Schema-Validator, Artifact- und Validierungsmatrixintegration, drei neue
  Testdateien, Arbeitsplan und Bootstrapvertrag; dieses Reviewprotokoll ist
  bewusst ausgeschlossen.
- fokussierte Korrekturmatrix: `108 passed in 2.23s`
- vollständige Repositorysuite: `1061 passed in 132.32s`
- `git diff --check`: bestanden; nur bekannte LF/CRLF-Hinweise
- formaler Claude-Korrekturreview: nach Quotafreigabe auf exakt diesem Digest
  durchgeführt und im Abschlussabschnitt freigegeben
- formaler Antigravity-Review: `NOT_RUN (manual bootstrap exception)`

## Vorgesehener Claude-Reviewumfang

Der read-only Planreview verwendet Sonnet mit Effort `high` und ein
geschlossenes JSON-Ausgabeschema. Vorgesehen sind ausschließlich:

- der aktive Arbeitsplan;
- die Abschnitte 3 und 9.3 der Phase-2-Roadmap;
- der manuelle Bootstrapvertrag;
- die aktuellen Vertragsgrenzen in `src/contracts.py`;
- die JSON-Hüllengrenzen in `src/agent_adapters.py`;
- die persistierten Review-/Findingmodelle in `src/artifact_models.py` und
  `schemas/orchestrator-artifact-v1.schema.json`;
- die angrenzenden Tests `tests/test_contracts.py` und
  `tests/test_agent_adapters.py`.

Claude darf nicht schreiben und keine Tests ausführen. Die Antwort muss ein
einziges schema-validiertes JSON-Objekt mit Reviewer, Plandigest, Entscheidung,
typisierten Findings, Reviewevidenz und Pre-Mortem sein. Freie Präambeln und
textuelle State-v3-Marker sind nicht Bestandteil des Vertrags.

## Freigabestatus

Der erste Aufruf wurde vor Prozessstart von der externen Datenfreigabe
abgewiesen. Es wurde keine Repositoryevidenz an Claude übertragen und keine
Reviewentscheidung erzeugt. Der Aufruf wird nicht umgangen oder in reduzierter
Form wiederholt, bevor der Benutzer die Übertragung des oben exakt genannten
read-only Umfangs an den externen Claude-Dienst ausdrücklich freigegeben hat.

Der Benutzer erteilte diese Freigabe anschließend ausdrücklich. Die Claude-CLI
lehnte zunächst nur den lokal nicht unterstützten JSON-Schema-Metaverweis
`$schema` ab; dabei wurde kein Modellreview ausgeführt. Nach Entfernen dieses
reinen Metaschlüssels lief genau ein inhaltlicher Planreview mit unverändertem
semantischem Ausgabeschema erfolgreich.

## Claude-Planreview – Runde 1

- Plandigest:
  `56aa380479dfae5cbcf4cc135fcc75f54ff33c11244fa0c302a9174b2219dc3e`
- Entscheidung: `CHANGES_REQUIRED`
- Provider: Claude Sonnet, Effort `high`, read-only
- Struktur: ein schema-validiertes JSON-Objekt; keine Präambel, keine
  Textmarkerreparatur und kein zweiter Modellaufruf
- Laufzeit: rund 240 Sekunden
- Antigravity: `NOT_RUN (manual bootstrap exception)`

### C-01 – BLOCKER – Findingherkunft im Kontext unvollständig

`NativeReviewContext` band Slice-ID und Rundennummer nicht ausdrücklich,
obwohl jedes neue `FindingRecord` eine vollständige `FindingOrigin` benötigt.

Akzeptanz: Slice-ID und einsbasierte Rundennummer werden bindende Kontextwerte;
ein Test weist ihre exakte, nicht erfundene Übernahme in das konvertierte
Finding nach.

### C-02 – BLOCKER – Inhaltsleeres Review nicht negativ getestet

Der Zielvertrag verlangte Findingereignis oder Reviewevidenz, die Testmatrix
enthielt aber keinen ausdrücklichen Negativfall für drei leere Ereignisarrays
ohne Evidenz.

Akzeptanz: Der Fall wird durch Schema beziehungsweise Domänenvalidator mit
stabilem Fehlercode abgewiesen und direkt getestet.

### C-03 – OBSERVATION – Abbildung strukturierter Validierungsbefehle offen

Das native Akzeptanztestobjekt unterschied Prosa und Argumentarray, während
`FindingRecord.acceptance_test` weiterhin ein String ist.

Akzeptanz: Der Plan bindet die Darstellung an
`ValidationCommandSpec(argv=...).display` und verlangt einen bytegleichen
Roundtriptest.

### Disposition

Alle drei Punkte wurden in Planrevision 1 übernommen. Die Revision erhält vor
der Implementierung einen neuen Claude-Planreview mit neuem Plandigest.

## Claude-Planreview – Runde 2

- Plandigest:
  `daa116df7aacda145aff5c07201bfe494dd073434d4aada5dd79ea0e1162f2c2`
- Entscheidung: `CHANGES_REQUIRED`
- Provider: Claude Sonnet, Effort `high`, read-only
- Struktur: ein schema-validiertes JSON-Objekt; keine Präambel, keine
  Textmarkerreparatur und kein zweiter Modellaufruf
- Antigravity: `NOT_RUN (manual bootstrap exception)`

### C-01 – BLOCKER – Validierungsbefehl inkompatibel dargestellt

`ValidationCommandSpec.display` erzeugt eine Shellanzeige. Die bestehende
Validierungsmatrix erkennt Findingbefehle dagegen ausschließlich als
`VALIDATE:` gefolgt von einem JSON-Argumentarray. Der geplante String wäre
deshalb still ignoriert worden.

Akzeptanz: Der native Konverter erzeugt exakt das bestehende `VALIDATE:`-Format.
Ein Integrationstest führt das konvertierte Resultat durch
`select_validation_request()` und vergleicht das resultierende Argumentarray.

### C-02 – OBSERVATION – Leeres Review muss auch schemaseitig scheitern

Die Formulierung ließ offen, ob nur der Domänenvalidator die Kreuzfeldregel
erzwingt, obwohl sie im JSON-Schema ausdrückbar ist.

Akzeptanz: Dieselbe leere Reviewfixture muss sowohl am Schema als auch am
Domänenvalidator deterministisch scheitern.

### C-03 – OBSERVATION – Remediation-Pfade beim Stoprequest unbestimmt

Der vorhandene `StopRequest` besitzt optionale `remediation_paths`, während der
native Erstvertrag sie nicht enthielt und die Konvertierungssemantik offenließ.

Akzeptanz: Die Auslassung wird als bewusstes Nichtziel des ersten Kerns
festgeschrieben; ein Test belegt die Konvertierung mit `remediation_paths == ()`.

### Disposition

Alle drei Punkte wurden in Planrevision 2 übernommen. C-01 erweitert den
Testscope gezielt um `tests/test_validation_matrix.py`, ohne die produktive
Validierungsmatrix zu ändern. Die neue Revision wird erneut mit neuem
Plandigest durch Claude geprüft.

## Claude-Planreview – Runde 3

- Plandigest:
  `3d2a74fd98a1529a076a3d956ba6d0183d524907815250d43215534bfa3e57a5`
- Entscheidung: `CHANGES_REQUIRED`
- Provider: Claude Sonnet, Effort `high`, read-only
- Struktur: ein schema-validiertes JSON-Objekt; keine Präambel, keine
  Textmarkerreparatur und kein zweiter Modellaufruf
- Laufzeit: rund 316 Sekunden
- Antigravity: `NOT_RUN (manual bootstrap exception)`

### C-01 – BLOCKER – Pflichtfelder des ContractResult ohne Quelle

`ContractResult.test_files` und `ContractResult.anchors` sind pflichtige Felder
ohne Default. Der Plan band weder ihre Herkunft in den Kontext noch eine
bewusste Abweichung, obwohl die Konvertierung keine Werte erfinden darf.

Akzeptanz: Beide Tupel werden deterministisch in `NativeReviewContext` gebunden,
wertgleich in `ContractResult` übernommen und mit nichtleeren Fixtures getestet.

### C-02 – OBSERVATION – Kontextbindung ohne adversariellen Test

Request- und Reviewerbindung waren Zielinvarianten, aber eine schema-gültige
Antwort gegen den falschen Kontext fehlte in der Testmatrix.

Akzeptanz: Fremde Request-ID beziehungsweise Revieweridentität scheitert
domänenseitig deterministisch mit stabilem Fehlercode; Run, Fingerprint und
Rundennummer sind Bestandteil der lokalen Requestbindung.

### Disposition

Beide Punkte wurden in Planrevision 3 übernommen. Sie benötigen keine weitere
Produktivdatei und erweitern nur die bereits vorgesehene Domänentestmatrix.

## Claude-Planreview – Runde 4

- Plandigest:
  `37b490e41db281a413439281d37d0ac927d718e25b1233edea1bd0b7937e732e`
- Entscheidung: `CHANGES_REQUIRED`
- Provider: Claude Sonnet, Effort `high`, read-only
- Struktur: ein schema-validiertes JSON-Objekt; keine Präambel, keine
  Textmarkerreparatur und kein zweiter Modellaufruf
- Laufzeit: rund 298 Sekunden
- Antigravity: `NOT_RUN (manual bootstrap exception)`

### C-01 – BLOCKER – Anker fälschlich als lokale Metadaten behandelt

Anker sind reviewer-authored Fachinhalt und werden vom bestehenden Driftguard
gegen die freigegebene Baseline verglichen. Ein unverändertes Echo aus dem
Kontext würde diesen Gatepfad bei nativen Reviews still deaktivieren.

Akzeptanz: Das Schema erhält einen geschlossenen nativen Ankertyp. Nur `origin`
kommt aus dem Kontext; ein Integrationstest weist hinzugefügte und geänderte
Anker durch `detect_anchor_changes()` nach.

### C-02 – BLOCKER – Drei bestehende Reviewinvarianten fehlten

Der Plan bildete `test_changes_approved`, `allow_new_observations` und die Regel
"negative Entscheidung benötigt offenen eigenen Blocker" noch nicht ab.

Akzeptanz: Die beiden Governance-Schalter werden Kontextwerte. Separate
Negativtests decken Testfreigabe, Observation-Konvergenz und blockerlose
Ablehnung mit stabilen Fehlercodes ab.

### Disposition

Beide Punkte wurden in Planrevision 4 vollständig übernommen. Der seltene
Red-State-Ausnahmepfad wird dagegen ausdrücklich fail-closed vertagt: Der native
Erstkern verlangt stets PASS und liefert `red_state_followup_slice=None`.

## Claude-Planreview – Runde 5

- Plandigest:
  `92990d7f69351205311a7007db53be5377a667d053b3c487c0ef865baac59c13`
- Entscheidung: `CHANGES_REQUIRED`
- Provider: Claude Sonnet, Effort `high`, read-only
- Struktur: ein schema-validiertes JSON-Objekt; keine Präambel, keine
  Textmarkerreparatur und kein zweiter Modellaufruf
- Laufzeit: rund 289 Sekunden
- Antigravity: `NOT_RUN (manual bootstrap exception)`

### C-01 – BLOCKER – Offene eigene Findings durften still fehlen

Der historische Vertrag verlangt in jeder Runde eine ausdrückliche
Statusänderung oder Reklassifizierung für jedes zuvor offene eigene Finding.
Diese Accountability-Regel fehlte im nativen Zielvertrag.

Akzeptanz: Fehlende Aktualisierung scheitert mit stabilem Fehlercode; dieselbe
Fixture mit genau einer Aktualisierung konvertiert erfolgreich.

### C-02 – OBSERVATION – Antigravity-Finalregel nicht explizit getestet

Antigravity darf im Finalreview nur bei global leerer offener Findingmenge
freigeben, während Claude nur die eigenen Findings schließen muss.

Akzeptanz: Ein positives Antigravity-Finalreview mit offenem Claude-Finding
scheitert; nach dessen Schließung ist es zulässig.

### C-03 – OBSERVATION – Nativer Anker ohne gebundene Herkunft

Bei `anchor_origin=None` darf ein nativer Anker keine leere oder erfundene
Herkunft erhalten.

Akzeptanz: Das geschlossene Transportschema akzeptiert den Anker, die
kontextabhängige Domänenvalidierung lehnt ihn jedoch mit stabilem Fehlercode ab.

### Disposition

Alle drei Punkte wurden in Planrevision 5 übernommen. Damit sind die im
historischen Reviewerpfad vorhandenen Finding-, Approval-, Teständerungs- und
Anchor-Gate-Invarianten im nativen Kern explizit spezifiziert und getestet.

## Claude-Planreview – Runde 6 (Freigabe)

- freigegebener Plandigest:
  `81cfabe7dcdb8baa9432a5b505ed35d6d284a065cc2e88ad61ed9bc30158036c`
- Entscheidung: `APPROVED`
- Provider: Claude Sonnet, Effort `high`, read-only
- Struktur: ein schema-validiertes JSON-Objekt; keine Präambel, keine
  Textmarkerreparatur und kein zweiter Modellaufruf
- Laufzeit: rund 255 Sekunden
- Antigravity: `NOT_RUN (manual bootstrap exception)`

### C-01 – OBSERVATION – Pflichtfelder des Stop-ContractResult

Auch die Stop-Konvertierung muss konkrete Werte für alle pflichtigen
`ContractResult`-Felder liefern.

Disposition: Die Implementierung muss neben `remediation_paths == ()` auch
`anchors == ()`, `test_files == ()`, `evidence is None`,
`pre_mortem is None` und die unveränderte Kontextattestierung testen. Diese
nicht-blockierende Ergänzung wurde nach Freigabe als Akzeptanzpräzisierung in
den Plan aufgenommen; Produktivscope und Architektur bleiben unverändert.

## Claude-Korrektur- und Finalreview – Freigabe

- geprüfter Implementierungsdigest:
  `60b8916c6e834d373e36e11b68ab5353dec16db81289dffde57378c6e946f092`
- Entscheidung: `APPROVED`
- Provider: Claude Sonnet, Effort `high`, read-only
- Struktur: genau ein gegen ein geschlossenes JSON-Schema validiertes
  Ergebnis; keine Textmarkerreparatur und kein zweiter Modellaufruf
- Laufzeit: rund 200 Sekunden
- Claude-Befunde: `C-01` bis `C-04` jeweils `CLOSED`
- manuelle Agy-Befunde: `AGY-01` bis `AGY-03` jeweils durch Claude geprüft und
  `CLOSED`
- neue Blocker: keine
- Antigravity: `NOT_RUN (manual bootstrap exception)`; das manuelle
  Agy-Advisory war Befundevidenz, keine formale Freigabe

### Geprüfte Dimensionen

Claude prüfte insbesondere die geschlossene Transportstruktur, die zusätzliche
Domänenvalidierung für direkt konstruierte Objekte, die öffentliche gemeinsame
Schema-Validierung, die typisierte Abbildung aller relevanten `ValueError`-
Grenzen, Whitespace-/NUL-/Typprüfungen, Findingeigentum und -lebenszyklus,
Approval- und Finalkonvergenz, Validierungsbefehle, Ankerkonvertierung,
Stopdefaults, Immutabilität, Requestbindung und idempotentes erneutes Parsen.
Die vollständige Repositorysuite mit `1061 passed` und die fokussierten
Regressionen wurden als gebundene lokale Validierung berücksichtigt.

### Größtes Restrisiko und Abbruchbedingung

Der neue Kern ist absichtlich noch nicht an einen Live-Provider oder den
Persistenzpfad angeschlossen. Das größte verbleibende Risiko liegt daher im
späteren Integrationspaket: Ein Adapter könnte einen veralteten oder nicht zum
autoritativen Orchestratorzustand passenden `NativeReviewContext` verwenden.
Als konkrete Abbruchbedingung gilt, wenn ein künftiger Integrationspfad trotz
abweichender `previous_findings`, Testdateien oder Validierungsattestierung ein
positives `ContractResult` oder eine Findingtransition akzeptiert.

### Pre-Mortem

Der wahrscheinlichste spätere Fehler wäre eine falsche Kontextbindung über
Retry- oder Resume-Grenzen, nicht ein Defekt des hier freigegebenen,
seiteneffektfreien Kerns. Deshalb bleibt die Live-Adapter- und
Persistenzintegration ein separates, erneut vollständig zu reviewendes
Arbeitspaket.

## Abschlussstatus

Der native Reviewresultat-Kern ist für den oben gebundenen Digest durch Claude
final freigegeben. Alle bekannten Befunde sind geschlossen. Für diesen
manuellen Bootstrap-Schnitt sind keine Implementierungs- oder Reviewarbeiten
mehr offen; Commit, Archivierung und spätere Live-Integration sind getrennte
Folgeschritte.
