# Arbeitsplan: Menschenlesbarkeit der Auditprojektion verbessern

## Zielbild in eigenen Worten

Die aus der akzeptierten `structured-v2`-Recordkette erzeugte Markdown-Ansicht
soll den Lauf als lesbare Folge von Rollen, Runden, Entscheidungen, Findings und
Validierungen zeigen. Technische Kennungen bleiben vollständig nachweisbar,
dominieren aber nicht mehr die Ereignisbeschreibung: Im Fließtext erscheinen
stabile Kurzreferenzen aus zwölf Hexzeichen, während jeder unterschiedliche
vollständige Bindungswert genau einmal in einem zentralen Nachweis innerhalb des
bereits verwalteten Blocks `decision-table` steht. Reviewerprosa wird nur anhand
dokumentierter lexikalischer Grenzen gegliedert; Inhalt, Wortlaut und Reihenfolge
bleiben erhalten.

Die Änderung bleibt eine reine Darstellungstransformation. Recordmodelle,
Replay, semantische Fakten, Digestbildung, Abschnittsschlüssel, äußere
`audit:*`-Marker, innere `artifact-records:*`-Marker und die aufrufende
Orchestratorlogik werden nicht verändert.

## Branch-, Status- und Scope-Festlegung

- Zielbranch: `feature/human-readable-audit-projection`.
- Planungsbasis: `4a75ecfc3809209fdf6b2ab2729f371860c16265`.
- Der aktive Branch stimmt mit dem Zielbranch überein.
- Beim Planungsstart waren ausschließlich die beiden bereits geänderten
  Provider-Input-Baselines
  `tests/fixtures/provider_input_efficiency/native-only-cutover-baseline-v1.json`
  und
  `tests/fixtures/provider_input_efficiency/native-only-cutover-baseline-v1.lock.json`
  als fremde Worktree-Änderungen sichtbar. Sie werden weder zurückgesetzt noch
  Bestandteil dieser Arbeit.
- Produktiver Scope umfasst ausschließlich `src/artifact_projection.py` und
  `src/audit_trail.py`. Erstere Datei nimmt ein akzeptiertes Replay entgegen und
  rendert daraus die sieben Record-Unterblöcke. Letztere rendert die darüber
  liegende State-v3-Sicht einschließlich der Reviewevidenz und führt beide
  Darstellungen in den vorhandenen verwalteten Blöcken atomar zusammen. Keine
  der beiden Dateien schreibt Records oder trifft Workflowentscheidungen.
- `src/orchestrator.py` ruft die beiden Projektionsfunktionen lediglich auf. Die
  Aufrufstellen und sämtliche Workflow-, State-, Replay-, Record-, Schema- und
  Vertragsmodule bleiben außerhalb des Änderungspfads.

## Repository-basierte Bestandsaufnahme

### Präsentations- und Zusammenführungspfad

1. `src/artifact_projection.py:36-44` definiert mit `SECTION_KEYS` die sieben
   Recordprojektionsschlüssel. `ArtifactAuditProjection.render_sections()`
   reduziert ein bereits akzeptiertes Replay gegebenenfalls auf einen Slice und
   übergibt es an `render_replay_sections()`.
2. `render_replay_sections()` läuft in Recordreihenfolge über das Replay,
   erzeugt die sieben Markdownkörper und berechnet selbst keine Fakten. Der
   semantische Digest kommt unverändert aus `ArtifactReplayResult`.
3. `src/audit_trail.py:755-796` projiziert dieselben Körper in Slice- und
   Arbeitsplandokumente. `merge_structured_record_sections()` in Zeile 799 ff.
   verlangt exakt alle bekannten Schlüssel, erkennt fehlende oder doppelte
   innere Marker und ersetzt jeden inneren Recordblock. `_replace_managed_body()`
   erhält die äußeren Blockmarker. Der abschließende Dateischreibvorgang erfolgt
   weiterhin nur einmal und atomar.
4. `src/orchestrator.py` ruft diese Schnittstelle für Arbeitsplan, Slice und
   Gesamtaudit auf. Dort ist für die Zielform keine Änderung erforderlich.

Vor der Recordprojektion rendert `src/audit_trail.py:1159-1417` außerdem die
State-v3-Präsentation: Reviews mit Rolle, Runde, Freigabe,
Validierungsbindung, Testdateien, Stopgrund, den drei Reviewevidenzfeldern und
eigenen Findings; Validierungsattestierungen mit Fingerprints, Status,
Kurzresultat, Ausgabedigest und Matrixresultaten; Testfreigabe und Pre-Mortems;
Findingdetails, Codex-Antworten, Entscheidungstabelle und zusammengefassten
Freigabestatus. `merge_structured_record_sections()` bewahrt diese Körper und
ersetzt darin nur die geschachtelten Record-Unterblöcke. Die Zielform muss daher
beide Renderer und die abschließende dokumentweite Zusammenführung erfassen.

### Bestehende Abschnittsschlüssel und heute gerenderte Felder

| Schlüssel | Heutiger Inhalt aus `render_replay_sections()` |
|---|---|
| `claude-review` | Semantischer Digest; Sequenz und Record-ID; Reviewverdict, Work-Unit, Finding-IDs, Recordfingerprint; bei nativen Reviews Transport-Schema, Request-ID und Antwort-Hash. |
| `codex-responses` | Semantischer Digest; alle `responded`-Findingübergänge mit Sequenz, Record-ID, Finding-ID, Aktion, Akteur, Schwere, Status und vollständiger Begründung. |
| `validation-attestation` | Semantischer Digest; Validierungsanforderungen samt argv-Grenzen; Attestierungen samt Fingerprint, Ergebnis, Exitcode und Output-Hash; Providerinput-Messwerte samt Input-, Policy- und Übergangsdigest, Limits, Überhang und Komponenten; Finalreview-Preflight samt betroffenen Records/Pfaden, Abhilfe, Übergang und Messungs-ID; deterministische Providerattempt-Summen und Einzelattempts. |
| `test-approval-premortem` | Semantischer Digest; Gate-Records mit Sequenz, Record-ID, Gateart, Entscheidung, Autorität, Fingerprint und vollständiger Begründung. |
| `findings` | Semantischer Digest; sämtliche Findingübergänge wie oben; zusätzlich die nach Finding-ID sortierte Konvergenztabelle mit Work-Units, Runden, Fingerprints, Claude-Entscheidungen, Codex-Dispositionen und Endstatus. |
| `decision-table` | Semantischer Digest und vollständiges, recordgeordnetes Ledger mit Sequenz, Record-ID, Typ, Status, logischer ID, Revision und Fingerprint. |
| `approval-status` | Semantischer Digest; Agentresultate mit Rolle, Ergebnis, Work-Unit, Testdateien, Transportbindung und Fingerprint; Work-Units/Korrektur-Work-Units mit Slice, Runde, Pfaden und Findings; Bindings mit Art, Ziel, Attestierungs-ID und Approval-IDs. |

Die äußeren, ebenfalls unverändert bleibenden Überschriften und
`audit:*`-Blöcke werden in `src/audit_trail.py` für dieselben Schlüssel den
Bereichen „Review-Feedback von Claude“, „Review-Antworten von Codex“,
„Validierungsattestierung“, „Testfreigabe und Pre-Mortem“,
„Findings-Lebenszyklus“, „Entscheidungstabelle“ und „Freigabestatus“ zugeordnet.
Beim Arbeitsplan teilen sich die letzten fünf Schlüssel den bestehenden Bereich
„Planstatus und formale Marker“; es entsteht trotzdem kein neuer Schlüssel.

### Entstehung vollständiger Hexwerte und Bindungsbedeutung

Vollständige technische Werte entstehen heute an folgenden Stellen der
Projektion:

- `record.record_id` ist die inhaltsadressierte Recordkennung und wird im
  Ereignispräfix, in Attemptzeilen und im Ledger wiederholt.
- `record.fingerprint.sha256` bindet Review, Agentresultat, Gate,
  Validierungsattestierung und Konvergenz an den geprüften Diff beziehungsweise
  Vertrag.
- `request_id` und `response_sha256` binden native Agentanfrage und Rohantwort;
  die Request-ID enthält selbst einen langen Hexanteil.
- `ValidationResult.output_sha256` bindet das kompakte Validierungsergebnis an
  den vollständigen Output.
- `ProviderInputMeasurementPayload.input_digest`, `policy_digest` und
  `transition_fingerprint` sowie der Übergangsfingerprint des
  Finalreview-Preflights binden Messung, Richtlinie und Zustandsübergang.
- `measurement_record_id`, `affected_record_ids`, `attestation_id` und
  `approval_ids` verweisen erneut auf inhaltsadressierte Records.
- `BindingPayload.target` kann ein Commit- oder Plancommit-Hash sein und muss als
  reproduzierbares Bindungsziel erhalten bleiben.
- `replay.semantic_digest` ist präsentationsunabhängig und heute in jedem der
  sieben Körper vollständig wiederholt. Er ist keine Resumeautorität, bleibt
  aber als nachprüfbare Information erhalten.
- Die darüberliegende State-v3-Projektion wiederholt außerdem
  `ValidationAttestation.attestation_id`, `diff_fingerprint`, `output_digest`
  und `AuthorizedTestChanges.diff_fingerprint`; Reviewerprosa kann weitere
  vollständige Bindungswerte wörtlich enthalten. Der Nachweis muss deshalb erst
  nach der Zusammenführung beider Sichten dokumentweit abgeschlossen werden.

Damit sind Record-IDs, Fingerprints, native Request-/Responsebindungen,
Validierungsoutput-, Input-, Policy- und Übergangsdigests sowie hexadezimale
Bindingziele echte reproduzierbare Nachweise. Gleiche Vollwerte, die in mehreren
Rollen auftreten (beispielsweise Record-ID als Attestierungsreferenz), sind nur
ein Nachweis und dürfen nicht dupliziert werden. Nichthexadezimale logische IDs,
Status, Rollen, Pfade, Befehlsargumente und Prosa bleiben normale Fakten im
jeweiligen Abschnitt.

### Bestehende formgebundene Testerwartungen

- `tests/test_artifact_projection.py` fixiert Recordreihenfolge, die
  semikolonverketteten Agent-/Reviewzeilen, native und historische
  Transportangaben, die vollständigen Fingerprints in der Konvergenztabelle,
  die Ledger-Spalten, Providerinput-/Attempttexte, Markdown-Escaping,
  Slice-Selektion, byteidentische Wiederholung und die Unabhängigkeit des
  semantischen Digests von Zeit und Darstellung.
- `tests/test_audit_trail.py` fixiert die sieben äußeren und inneren Marker,
  vollständige Schlüsselabdeckung, Wiederholungs-No-op, atomaren Ersatz,
  Ablehnung partieller oder doppelter innerer Marker und die kosmetische Natur
  aller verwalteten Körper für den Markdown-Fingerprint. Außerdem hängen
  konkrete Erwartungen an den heutigen ordinalen Review-/Validierungsköpfen,
  den einzeiligen Evidenzfeldern und den Finding- und Entscheidungstabellen.
  Diese State-v3-Erwartungen sind gemeinsam mit der Recordform anzupassen.
- `tests/test_structured_artifact_regressions.py` und
  `tests/test_orchestrator_runtime.py` prüfen ausgewählte Inhalte der Projektion
  integrationsnah, aber keine vollständige Ausgabeform. Ihre bestehenden
  Erwartungen müssen grün bleiben; ein Produktivpfad außerhalb der beiden
  Renderer-/Mergebausteine wird daraus nicht abgeleitet.

### Platzierung des Nachweises

`decision-table` ist der geeignete bestehende Block: Er enthält bereits die
State-v3-Findingentscheidung und im Record-Unterblock das vollständige
recordgeordnete technische Ledger. Er wird für Slice und Gesamtlauf projiziert
und liegt innerhalb derselben atomaren Sieben-Schlüssel-Aktualisierung. Nach dem
gekürzten Ledger erhält dieser Körper den Unterabschnitt
`### Nachweis vollständiger Bindungswerte`. Der Unterabschnitt ist gewöhnliches
Markdown innerhalb von
`<!-- artifact-records:decision-table:begin -->` und
`<!-- artifact-records:decision-table:end -->`; weder Abschnittsschlüssel noch
Marker oder Blocktyp ändern sich. Alle anderen Körper enthalten nur
Kurzreferenzen. Damit existiert je projiziertem Dokument genau ein Nachweis.

Die Bestandsaufnahme ergibt keine Stopbedingung: Alle Vollwerte und Prosafelder
liegen bereits im reinen Renderer vor, der Digest wird außerhalb der
Darstellungslogik geliefert, und der vorhandene Mergepfad kann die Zielform ohne
Strukturänderung aufnehmen.

## Verbindliche Entscheidungen und Umsetzungskonsequenzen

1. `SECTION_KEYS`, sämtliche `audit:*`- und `artifact-records:*`-Marker sowie
   deren Reihenfolge bleiben bytegenau unverändert.
2. `src/artifact_projection.py` stellt Recordereignisse und Ledger zunächst
   feldvollständig und in stabiler Reihenfolge bereit. `src/audit_trail.py`
   rendert die State-v3-Ereignisse und führt anschließend beide Sichten durch
   genau einen dokumentweiten, rein präsentationsbezogenen Renderkontext. Dieser
   sammelt technische Hexwerte in stabiler Erstauftretensreihenfolge, liefert
   für jeden Fließtextverweis genau die ersten zwölf Hexzeichen und rendert die
   deduplizierte Zuordnung nur im `decision-table`-Nachweis. Präfixe wie `ar1-`
   oder `native-review-request-` bleiben zur Typunterscheidung sichtbar; nur ihr
   Hexanteil wird gekürzt. Identische Vollwerte teilen sich eine Nachweiszeile.
3. Der dokumentweite Sammler erfasst alle von beiden Renderern ausgegebenen Digest-,
   Fingerprint-, Record-/Request- und Bindingfelder. Zusätzlich werden längere
   rein hexadezimale technische Bindingziele lexikalisch erkannt. Prosa wird
   nicht nach vermeintlichen fachlichen Bedeutungen interpretiert; enthält sie
   jedoch einen vollständigen 64-Hex-Digest, greift dieselbe rein lexikalische
   Kurzreferenzregel, damit die dokumentweite Hashpolitik nicht umgangen wird.
4. Vor dem atomaren Dateischreiben wird geprüft, dass jeder im Fließtext
   verwendete Kurzverweis
   genau einen Vollwert im Nachweis besitzt, jeder unterschiedliche Vollwert
   dort genau einmal erscheint und außerhalb des Nachweises kein vollständiger
   technischer Hexwert verbleibt. Eine Verletzung ist ein
   `AuditTrailError`, keine stillschweigende Kürzung oder Auslassung.
5. State-v3- und Recordereignisse werden weiterhin in ihrer jeweiligen
   Ereignis-/Recordreihenfolge ausgegeben, erhalten aber sprechende
   Überschriften beziehungsweise Tabellenzeilen. Für Reviews lautet
   der Kopf beispielsweise „Claude · Runde 2 · approved“, für Agentresultate
   „Codex · Runde 2 · ready“; die Runde wird deterministisch über die bereits
   vorhandene Work-Unit-Zuordnung ermittelt. Fehlt bei einem Record eine Runde,
   wird explizit „Runde –“ ausgegeben. Es wird keine Runde inferiert.
6. Technische Recorddetails werden in stabilen Markdowntabellen statt in
   Semikolonketten dargestellt. Tabellenzellen werden mit der bestehenden
   HTML-Escaping-Grenze geschützt. Alle bisher dargestellten Felder bleiben in
   derselben Recordreihenfolge enthalten; leere Werte werden explizit als `–`,
   `keine` oder `unknown` dargestellt wie bisher.
7. Prosastrukturierung erfolgt in beiden Renderern vor dem Escaping
   ausschließlich lexikalisch:
   vorhandene `CRLF`, `LF` und `CR` sind harte Grenzen; Zeilen mit
   `-`, `*`, `+`, `•` oder einer Dezimalzahl plus `.`/`)` werden in der
   vorgefundenen Reihenfolge als Listeneinträge dargestellt; gleichartige
   Marker innerhalb eines Absatzes werden nur an einem Marker getrennt, dem
   horizontaler Leerraum vorausgeht und nichtleerer Text folgt. Sonstige Prosa
   wird nur nach `.`, `!` oder `?` plus horizontalem Leerraum umbrochen. Marker,
   Satzzeichen und Text bleiben erhalten. Es gibt keine Abkürzungslogik,
   Sprachklassifikation, Übersetzung, Zusammenfassung oder Umformulierung.
8. Mehrfachleerraum an einer erkannten Umbruchgrenze wird ausschließlich durch
   den Markdown-Zeilenwechsel ersetzt; alle übrigen Zeichen bleiben erhalten.
   Die Regel wird in `README.md` dokumentiert und mit gemischtsprachiger,
   nummerierter, aufzählender und mehrzeiliger Reviewevidenz getestet.
9. `semantic_artifact_facts()`, `semantic_artifact_digest()`,
   Replay-/Selektionslogik und alle Payloadmodelle bleiben unverändert. Ein
   Vorher-/Nachher-Test hält den Digest derselben Recordkette fest, während die
   Projektionsform sich ändert.

## Slice-Liste

### Slice 1 - Recordnative Auditsicht atomar lesbar rendern

**Zweck**

Den einen geschlossenen State-v3-/Record-zu-Dokument-Pfad um sprechende Ereignisköpfe,
stabile Tabellen, lexikalisch gegliederte verlustfreie Prosa und den einmaligen
Bindungsnachweis erweitern. Renderer, Merge-Gegenprobe, Dokumentation und
Regressionstests werden gemeinsam geändert, weil erst ihr Zusammenspiel die
dokumentweite Einmaligkeit vollständiger Werte garantieren kann.

**Exakter Änderungspfad**

- `src/artifact_projection.py`
- `src/audit_trail.py`
- `tests/test_artifact_projection.py`
- `tests/test_audit_trail.py`
- `README.md`

**Warum jeder Produktivpfad zur Präsentationsschicht gehört**

- `src/artifact_projection.py` ist laut Modulvertrag die deterministische,
  read-only Markdownprojektion akzeptierter strukturierter Artefakte. Die Datei
  schreibt weder State noch Records und trifft keine Freigabeentscheidung.
- `src/audit_trail.py` rendert ausschließlich die menschenlesbaren State-v3-
  Auditkörper, validiert deren vorhandene Marker und ersetzt beziehungsweise
  verschachtelt die Recordkörper atomar. Die geplante lexikalische
  Prosagliederung, der dokumentweite Kurzverweis und der einmalige Nachweis
  liegen deshalb fachlich in genau dieser Präsentations- und Mergegrenze. Die
  Aufrufstellen sowie `atomic_write_file()` bleiben unverändert.

**Arbeitsschritte**

1. Einen privaten dokumentweiten Renderkontext für Kurzreferenzen, stabile
   Deduplizierung und den zentralen Nachweis in der vorhandenen
   Zusammenführungsgrenze einführen, ohne Projektionseingaben oder
   `SECTION_KEYS` zu verändern.
2. Die State-v3- und Recordrenderer aller sieben Abschnitte auf sprechende
   Ereignisköpfe und Tabellen mit
   vollständiger Feldabdeckung umstellen; Recordreihenfolge und explizite
   Leerwertdarstellung beibehalten.
3. Die dokumentierte lexikalische Prosafunktion auf Reviewevidenz
   (`dimensions`, `largest_residual_risk`, `break_condition`) und alle weiteren
   heute gerenderten Begründungs-/Evidenzfelder anwenden. Vorhandene
   Markdown-/Marker-Injection bleibt escaped.
4. Das gekürzte Recordledger und genau einen deduplizierten
   Nachweis-Unterabschnitt im bestehenden `decision-table`-Körper ausgeben.
5. Tests auf die neue Form umstellen und um vollständige Positiv-, Negativ- und
   Reproduzierbarkeitsfälle ergänzen; die Regel und Autoritätsgrenze in
   `README.md` dokumentieren.

**Akzeptanzkriterien**

- Die sieben Schlüssel und sämtliche äußeren/inneren Marker entsprechen exakt
  dem heutigen Vertrag; ein partieller innerer Anhang bleibt ein Fehler.
- Für jeden Schlüssel gibt es eine positive Erwartung auf seine neue Tabelle
  oder Ereignisform. Die Gesamtprojektion zeigt Rolle, Runde, Ergebnis und
  offene/geschlossene Findings ohne Auswertung der Nachweistabelle.
- Außerhalb des einen `decision-table`-Nachweises erscheint kein vollständiger
  technischer Hexwert. Jeder unterschiedliche Vollwert erscheint im gesamten
  zusammengeführten Dokument höchstens einmal und ist dort eindeutig mit
  seiner zwölfstelligen Kurzreferenz und mindestens einer Feldart gekoppelt.
- Zwei identische Bindungswerte aus verschiedenen Feldern erzeugen eine
  Nachweiszeile; zwei verschiedene Werte erzeugen zwei Zeilen in stabiler
  Erstauftretensreihenfolge.
- Nummerierte, aufzählende, mehrzeilige und gewöhnliche gemischtsprachige Prosa
  ist nach Rücknahme ausschließlich der dokumentierten Zeilenwechsel
  zeichengetreu und in Originalreihenfolge wiederauffindbar. HTML- und
  Markerinjektion bleibt unschädlich.
- Dieselbe akzeptierte Recordkette erzeugt über direkte Records, akzeptiertes
  Replay, Sliceprojektion und wiederholtes Mergen byteidentische Körper.
- `semantic_artifact_digest()` liefert für die unveränderte Recordkette vor und
  nach der Rendererumstellung denselben erwarteten Wert; eine Änderung eines
  typisierten Fakts ändert ihn weiterhin.
- Slice-Selektion, Attempt-Aggregation, argv-Grenzen und unbekannte
  Providerwerte bleiben vollständig und korrekt sichtbar.
- Die im Planungsstart vorhandenen fremden Fixtureänderungen bleiben unberührt.

**Fokussierte Validierung**

- `python3 -m pytest tests/test_artifact_projection.py -v`
- `python3 -m pytest tests/test_audit_trail.py -v`
- `python3 -m pytest tests/test_structured_artifact_regressions.py -v`
- `python3 -m pytest tests/test_orchestrator_runtime.py -v`
- `git diff --check`

Die vollständige konfigurierte Matrix und ihre Fingerprintbindung führt
ausschließlich der Orchestrator nach der Implementierung aus.

## Reihenfolge und Abhängigkeitsgraph

Es gibt bewusst genau einen Implementierungsslice:

`State-v3-Ereignisse + akzeptiertes Replay` → `sieben kombinierte Körper` →
`ein dokumentweiter Renderkontext im bestehenden atomaren Merge` →
`ein Dokument mit einem Nachweis`

Eine Trennung von Hashsammler, Abschnittsformatierung und Merge-Gegenprobe würde
Zwischenstände erzeugen, die entweder Vollwerte verlieren oder die
dokumentweite Einmaligkeitsregel noch nicht erfüllen. Fünf Änderungsdateien
sind für diesen fachlich atomaren Schnitt moderat. Renderer und Merge bleiben
zusammen in der reinen Präsentationsschicht, sodass kein zweiter Laufzeit- oder
Integrationsslice nötig ist.

## Anforderungs- und Abdeckungsmatrix

| Anforderung | Umsetzung | Nachweis |
|---|---|---|
| Kein Informationsverlust | Feldvollständige Tabellen und rein lexikalische Prosagliederung | Parametrisierte Abschnittserwartungen plus Zeichen-/Reihenfolgenrekonstruktion |
| Zwölfstellige Kurzwerte | Zentraler Renderkontext ersetzt technische Vollwerte im Fließtext | Negativsuche nach langen Hexläufen außerhalb des Nachweises |
| Vollwert höchstens einmal | Deduplizierter Nachweis in `decision-table` | Dokumentweiter Zähltest mit mehrfach referenzierten identischen Werten |
| Reproduzierbare Bindungen | Kurzreferenz ↔ Vollwert ↔ Feldarten | Positivtest für Record, Fingerprint, Request, Response, Output, Policy, Übergang und Bindingziel |
| Byteidentische Ausgabe | Recordordnung, feste Feldordnung, stabile Erstauftretensordnung | Doppelte Projektion und doppelter Merge sind exakt gleich |
| Digest unverändert | Keine Änderung an Replay/facts/digest | Festwert-/Vorher-Nachher-Test derselben Chain |
| Blockgrenzen unverändert | Bestehende sieben Schlüssel und Marker | Vollständigkeits-, Partialmarker- und No-op-Tests in `test_audit_trail.py` |
| Rolle/Runde/Ergebnis erkennbar | Sprechende Köpfe und Statusspalten | Review-, Agentresultat- und Finding-Lebenszyklus-Erwartungen |
| Produktivscope Präsentation | Nur Recordrenderer und bestehender State-v3-Renderer/Merge in `src/artifact_projection.py` und `src/audit_trail.py` | Pfadprüfung des finalen Diffs |

## Migration und Rollback

Es gibt keine Datenmigration. Bei der nächsten Projektion werden alle sieben
inneren Recordblöcke aus derselben akzeptierten Kette neu erzeugt und im
bestehenden atomaren Dateischreibvorgang gemeinsam aktualisiert. Dadurch werden
auch ältere, vollständig erkennbare Ansichten selbstheilend auf die neue Form
gebracht; partielle Marker bleiben weiterhin ein harter Fehler.

Ein Rollback besteht ausschließlich aus dem Rücknehmen des Renderer-, Test- und
Dokumentationsdiffs. Records, State, Freigaben und Digests benötigen weder
Reparatur noch Rückmigration, weil sie nie aus der Markdownansicht rekonstruiert
werden.

## Test- und Validierungsplan

`tests/test_artifact_projection.py` wird zur formtragenden Testsuite der
Record-Unterblöcke ausgebaut:

- ein reichhaltiges Replay deckt jeden der sieben Schlüssel und jede heute
  ausgegebene Payloadfamilie ab;
- pro Schlüssel wird die konkrete neue Kopf-/Tabellenform positiv geprüft;
- Prosa-Fixtures kombinieren vorhandene Zeilenumbrüche, `1.`/`2)`,
  `-`/`*`/`+`/`•`, normale Sätze, Abkürzungen und gemischte Sprache; erwartet
  wird ausschließlich die dokumentierte lexikalische Behandlung;
- bestehende Tests für Replaygleichheit, Digestverhalten, Slicegrenzen,
  Providerattempts, argv und Escaping bleiben erhalten beziehungsweise werden
  formgerecht präzisiert.

`tests/test_audit_trail.py` prüft die komplette zusammengesetzte Datei:

- alle sieben Körper werden ersetzt, aber Schlüssel und Marker bleiben gleich;
- eine Hilfsassertion extrahiert den genau einmaligen Nachweis, prüft
  Vollwert-Einmaligkeit über State-v3- und Recordkörper gemeinsam und weist
  außerhalb des Nachweises lange technische Hexläufe zurück;
- Wiederholung ist ein bytegleicher No-op und ändert den semantischen
  Markdown-Fingerprint nicht;
- partielle oder doppelte `artifact-records:*`-Marker werden weiterhin
  abgelehnt.

Die integrationsnahen fokussierten Suites sichern ab, dass echte
Checkpoint-/Runtimeprojektionen die neue Darstellung aufnehmen, ohne Änderungen
an deren Produktionspfaden zu verlangen. Die autoritative Vollmatrix bleibt dem
Orchestrator vorbehalten.

## Risiken und Stopregeln

- Größtes Risiko ist ein Digest- oder Referenzfeld aus einer der beiden
  übereinanderliegenden Projektionen, das nicht durch den zentralen
  Renderkontext läuft und dadurch vollständig im Fließtext verbleibt. Der
  dokumentweite Negativtest und die fail-closed Endprüfung machen diesen Fehler
  sichtbar.
- Zweites Risiko ist eine zu großzügige Listenerkennung. Die Regel bleibt daher
  rein lexikalisch, sprachneutral und dokumentiert; Grenzfälle werden nicht
  „verbessert“, sondern mechanisch nach denselben Zeichenmustern behandelt.
- Anhalten, wenn ein vollständiger reproduzierbarer Wert nicht innerhalb des
  bestehenden `decision-table`-Nachweises abbildbar ist.
- Anhalten, wenn vollständige Reviewevidenz nur durch semantische Interpretation
  statt durch die festgelegten lexikalischen Grenzen strukturiert werden kann.
- Anhalten, wenn Implementierung oder Tests eine Änderung an Faktenextraktion,
  Digest, Replay, Record-/Vertragsmodell, Abschnittsschlüsseln, Markern,
  atomarem Ersatz oder aufrufender Laufzeitlogik erfordern.

## Offene Fragen

Keine. Die gesetzten Vorgaben lassen sich innerhalb der repository-basiert
ermittelten Präsentationsschicht erfüllen.

## Planstatus und formale Marker

- Status: bereit für Claude-Planreview.
- Der Plan enthält genau einen zukünftigen, ausführbaren Implementierungsslice.
- Dieser PLAN_ONLY-Lauf verändert ausschließlich dieses Arbeitsplan-Artefakt.

## Orchestrator-Prüfprotokoll

### Review-Feedback von Claude

<!-- audit:claude-review:begin -->
### Ereignis 2: Runde 1

- Reviewer: `claude`
- Freigabe: `YES`
- Validierungsbindung: `plan-validation-59b4027507a4`
- Testdateien: keine
- Prüfdimensionen: Correctness: repository-grounded inventory of the two presentation-layer renderers (src/artifact_projection.py, src/audit_trail.py) and the atomic merge path is accurate and specific (line-anchored), and the seven existing section keys/marker pairs are enumerated and left untouched. Contracts: all five client-mandated constraints (no information loss, 12-hex short references with a single dedup evidence subsection, deterministic reproducibility, unchanged block boundaries/section keys, production changes confined to the presentation layer with an explicit per-file justification) are addressed with concrete mechanisms, and the mandated 'Exakter Änderungspfad' heading plus a single contiguous 'Slice 1 - title' heading are formatted exactly per the orchestrator contract. Failure paths: a documented pre-write invariant (each short reference maps to exactly one full value, each distinct full value appears exactly once in the decision-table evidence, no full hex value remains outside it) fails closed with an AuditTrailError rather than silently truncating or dropping data, and existing partial/duplicate inner-marker rejection is explicitly preserved. Security: no secrets, credentials, network calls, branch/commit operations, or destructive actions are introduced; the diff is confined to the single authorized work-plan path; existing HTML/marker escaping is explicitly preserved for the new table cells and structured prose. Resume/idempotency: the plan reaffirms record chain immutability, unchanged semantic_artifact_digest computation, byte-identical re-render on repeated merge, and self-healing full re-render of all seven blocks on the next projection (no partially-migrated documents), with a before/after digest-equality test called out.
- Größtes Restrisiko: Two residual risks remain for the eventual Slice 1 implementation to close out: (1) the chosen hard-vs-soft Markdown line-break mechanism for lexically wrapped prose is not yet pinned down, so the readability goal could be textually satisfied but visually invisible in common Markdown renderers (tracked as C-01); (2) an astronomically unlikely 12-hex-character prefix collision between two distinct full values is only caught reactively via the pre-write uniqueness check (fail-closed AuditTrailError) rather than proactively documented as an expected, tested failure mode.
- Realistische Bruchbedingung: Approval of this plan would need to be revisited if Slice 1's actual diff touches any workflow/orchestrator/state/replay/record/contract module, adds or removes a managed section key or an audit:*/artifact-records:* marker, changes semantic_artifact_facts()/semantic_artifact_digest() output for an unchanged record chain, leaves any full 64-character hex value outside the single decision-table evidence subsection, silently truncates or reorders reviewer prose instead of failing closed, or produces non-byte-identical output across repeated merges of the same accepted record chain.
- Eigene Findings: `C-01`

<!-- artifact-records:claude-review:begin -->
Semantischer Record-Digest: `0d222050e2cb73ac45bb36434024a5eba7caa758f802f8e14f972f247fb0f73a`

- 9. `ar1-763dd9b2fadf0fb974f16fe759e5532b5deeaf66e5aeff5ce9ded059bb920e84`: `approved`; Work-Unit `1`; Findings `C-01`; Fingerprint `59b4027507a427afeef96a4a312ad6ec498ad917c5cf90878093a087389dea24`; Transport `native-claude-review-v2`; Request `native-review-request-18f57539eab54ad45f34899451125d0b2b340311736727ea3b63ddf9d989366d`; Response `08576d8fd62f5ec0b13b21b60ed0468b11db40578ca4fdc4dcccfb55064c2dbd`
<!-- artifact-records:claude-review:end -->
<!-- audit:claude-review:end -->

### Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
Noch keine strukturierten Codex-Antworten.

<!-- artifact-records:codex-responses:begin -->
Semantischer Record-Digest: `0d222050e2cb73ac45bb36434024a5eba7caa758f802f8e14f972f247fb0f73a`

Keine Codex-Findingantworten.
<!-- artifact-records:codex-responses:end -->
<!-- audit:codex-responses:end -->

### Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### Ereignis 1: `plan-validation-59b4027507a4`

- Diff-Fingerprint: `59b4027507a427afeef96a4a312ad6ec498ad917c5cf90878093a087389dea24`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: internal plan contract passed
- Ausgabedigest: `2b1305bfc8198ec29152611fde2b73f4af19055ff2bb86e5a028bdde36a58944`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| internal:work-plan-contract | PASS | 0 | slices=1; planned_paths=1; changed_paths=1; future_slices=1; work_plan=docs/internal/menschenlesbarkeit-der-auditprojektion-verbessern-arbeitsplan.md |

<!-- artifact-records:validation-attestation:begin -->
Semantischer Record-Digest: `0d222050e2cb73ac45bb36434024a5eba7caa758f802f8e14f972f247fb0f73a`

- 2. `ar1-aa3cc0f46b6d25a9abc2fa2b68faf2fc8508c34af698bbd2a6bbd1e04651b278`: Providerinput `codex/codex_plan` = `allowed`; local_input_chars `25993/4000000`, local_input_bytes `26161/16000000`; local_input_digest `2684797b75f74b5263121f9d5ced8d97fbc800702e82cbd898965895a33aa31f`, Policy `9edf600f09ac1b36a30675cc91b1f6769c4545f8f200a4fba413b4b9a4c9bc1e`, Übergang `9ea773ab9cfbe21aa6b3d2dbb1c1a3c8cabc2591b7943911b1ad8c383aa9f392`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, local_input_largest_component `stdin_prompt`; local_input_component_count `2`; Komponenten `stdin_prompt=22204/22372, response_schema=3789/3789`
- 6. `ar1-95f54a48b8e145e6e4f4ce60f8b4d66719e4903a6d62e931167de0461ca17fce`: Attestierung durch `orchestrator`; Fingerprint `59b4027507a427afeef96a4a312ad6ec498ad917c5cf90878093a087389dea24`
  - `pass` / Exit `0` / Output `2b1305bfc8198ec29152611fde2b73f4af19055ff2bb86e5a028bdde36a58944`: `argv` [`internal:work-plan-contract`]
- 7. `ar1-aaaacfa1daf7e486265351ee5a9c39948d9b6813141e4434526cda671d001ac5`: Providerinput `claude/claude_plan_review` = `allowed`; local_input_chars `68005/4000000`, local_input_bytes `68553/16000000`; local_input_digest `17cdb722e9b52734500c06f7eb90761beab73a133496ae304e4eb074b29506ba`, Policy `9edf600f09ac1b36a30675cc91b1f6769c4545f8f200a4fba413b4b9a4c9bc1e`, Übergang `2f4c6fb5ba827f4303f52314cc07e2a3553273bb5b5f1341393ce318b56247a9`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, local_input_largest_component `evidence_asset_001`; local_input_component_count `6`; Komponenten `request_chunk_001=23325/23493, evidence_asset_001=33340/33720, packet_manifest=610/610, system_policy=217/217, response_schema=10283/10283, start_directive=230/230`
- Providerattempt-Summe Run `watch-20260826-121454.856560Z-0e1318ebc4f5` / Operation `provider-operation-7e13fe3600424865b38ab326d6d9340601ff9978182dbfeabd93a45f7be0f2af` (`codex/codex_plan`; Modell `gpt-5.6-sol`; Effort `medium`): Attempts `1`, offen `0`, Duration `522.192410` (bekannt `1`, unbekannt `0`); Inputzeichen `25993`, Inputbytes `26161`; Retrystatus `single-attempt`; input_tokens=sum:unknown,known:0,unknown:1; tool_input_tokens=sum:unknown,known:0,unknown:1; cache_read_input_tokens=sum:unknown,known:0,unknown:1; cache_creation_input_tokens=sum:unknown,known:0,unknown:1; thinking_tokens=sum:unknown,known:0,unknown:1; output_tokens=sum:unknown,known:0,unknown:1; total_tokens=sum:unknown,known:0,unknown:1; turns=sum:unknown,known:0,unknown:1; cost_usd=sum:unknown,known:0,unknown:1
  - 5. `ar1-27d9be762956329b757e0a4cb92de162ce2aef235368dc813fd898eb314766e8`: Attempt `1` = `succeeded`; Messung `ar1-aa3cc0f46b6d25a9abc2fa2b68faf2fc8508c34af698bbd2a6bbd1e04651b278`; Modell `gpt-5.6-sol`; Effort `medium`; Inputzeichen `25993`; Inputbytes `26161`; Duration `522.1924095590366`; Fehler `none`; Usage `unknown`
- Providerattempt-Summe Run `watch-20260826-121454.856560Z-0e1318ebc4f5` / Operation `provider-operation-e7e87b486fb9c997c99e0ef06a2100cf6bbb3fa559c7b08e47f2da30139dea0c` (`claude/claude_plan_review`; Modell `sonnet`; Effort `high`): Attempts `1`, offen `0`, Duration `159.947496` (bekannt `1`, unbekannt `0`); Inputzeichen `68005`, Inputbytes `68553`; Retrystatus `single-attempt`; input_tokens=sum:8,known:1,unknown:0; tool_input_tokens=sum:unknown,known:0,unknown:1; cache_read_input_tokens=sum:32728,known:1,unknown:0; cache_creation_input_tokens=sum:35919,known:1,unknown:0; thinking_tokens=sum:unknown,known:0,unknown:1; output_tokens=sum:14130,known:1,unknown:0; total_tokens=sum:unknown,known:0,unknown:1; turns=sum:5,known:1,unknown:0; cost_usd=sum:0.2925426,known:1,unknown:0
  - 11. `ar1-4b59bd3508e91d76ba2e613c148a5f77890b9315701e510d82ed918b5ae64c2d`: Attempt `1` = `succeeded`; Messung `ar1-aaaacfa1daf7e486265351ee5a9c39948d9b6813141e4434526cda671d001ac5`; Modell `sonnet`; Effort `high`; Inputzeichen `68005`; Inputbytes `68553`; Duration `159.9474955489859`; Fehler `none`; Usage `input_tokens=8, tool_input_tokens=unknown, cache_read_input_tokens=32728, cache_creation_input_tokens=35919, thinking_tokens=unknown, output_tokens=14130, total_tokens=unknown, turns=5, cost_usd=0.2925426`
<!-- artifact-records:validation-attestation:end -->
<!-- audit:validation-attestation:end -->

### Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
- Teständerungsfreigabe: nicht erfasst.
- Pre-Mortems:
  - Ereignis 2: If the follow-up Slice 1 review is approved too quickly, the most likely ways this initiative could still fail after this plan approval are: (a) implementers discover mid-Slice that the single 'decision-table' evidence subsection cannot cleanly host every full value once State-v3 and record bodies are merged (e.g. a value only known late in the merge pipeline), and patch around it with a second ad-hoc evidence location or a relaxed uniqueness check, quietly reopening the hashing-policy violation this plan was meant to close; (b) the lexical sentence/list segmentation rule is implemented with plain '\n' characters that render as soft breaks in the tooling actually used to view the audit document, so the human-readability acceptance criterion looks satisfied in raw diffs and unit tests but is not actually achieved for a human reading the rendered file (C-01); (c) under schedule pressure, someone treats the 'no full hex value outside the nachweis' invariant as a best-effort formatting nicety rather than a hard fail-closed AuditTrailError, letting a stray fingerprint leak into prose from a not-yet-covered Reviewerprosa field. None of these require re-planning now, but the Slice 1 review must verify the fail-closed uniqueness check is actually exercised by a negative test and that the visual line-break behavior is explicitly asserted, or deny the Slice as a correction round.

<!-- artifact-records:test-approval-premortem:begin -->
Semantischer Record-Digest: `0d222050e2cb73ac45bb36434024a5eba7caa758f802f8e14f972f247fb0f73a`

Keine strukturierten Gates.
<!-- artifact-records:test-approval-premortem:end -->
<!-- audit:test-approval-premortem:end -->

### Findings-Lebenszyklus

<!-- audit:findings:begin -->
### `C-01` — `OPEN`

- Quelle: `claude`; Runde 1
- Klasse: `OBSERVATION`
- Finding: The plan's lexical prose line-break rule (Verbindliche Entscheidungen items 7-8) replaces a detected break point solely with a single Markdown newline character. In common CommonMark/GitHub-flavored Markdown rendering, a lone &#96;\n&#96; inside a paragraph is a soft break and is visually collapsed back into one line unless a hard-break marker (two trailing spaces, a backslash, or a blank line between blocks) is used. Because the stated purpose of this whole initiative is that 'ein Mensch den Verlauf ... nachvollziehen kann' by scanning sentence- and list-structured prose, Slice 1 should explicitly decide and test whether the projected document is consumed as raw text (where a bare &#96;\n&#96; is sufficient) or through a Markdown renderer (where a hard-break marker is required for the readability goal to actually manifest visually, not merely textually in the source). This does not violate any of the five fixed constraints, the digest, block markers, or reproducibility, so it does not block this plan, but it is a concrete risk to the plan's own primary acceptance criterion that Slice 1's test suite should close.
- Akzeptanztest: Slice 1's implementation and its test suite (tests/test_artifact_projection.py and/or tests/test_audit_trail.py) explicitly assert the concrete rendering behavior chosen for lexical line breaks in prose fields (e.g. that a hard break such as two trailing spaces or a blank-line paragraph split is emitted, or an explicit documented statement that the audit view is only ever consumed as raw text), so that the sentence/list segmentation is verifiably visible to a human reader in the actually used viewing context, not just present as a bare '\n' in the byte stream.
- Statusbegründung: –

<!-- artifact-records:findings:begin -->
Semantischer Record-Digest: `0d222050e2cb73ac45bb36434024a5eba7caa758f802f8e14f972f247fb0f73a`

- 10. `ar1-e4c01901eaaafd0a676e31110222f8cb0f89fc733601501794c413f10de48c04`: `C-01` `opened` durch `claude`; `OBSERVATION` / `open` — The plan's lexical prose line-break rule (Verbindliche Entscheidungen items 7-8) replaces a detected break point solely with a single Markdown newline character. In common CommonMark/GitHub-flavored Markdown rendering, a lone &#96;\n&#96; inside a paragraph is a soft break and is visually collapsed back into one line unless a hard-break marker (two trailing spaces, a backslash, or a blank line between blocks) is used. Because the stated purpose of this whole initiative is that 'ein Mensch den Verlauf ... nachvollziehen kann' by scanning sentence- and list-structured prose, Slice 1 should explicitly decide and test whether the projected document is consumed as raw text (where a bare &#96;\n&#96; is sufficient) or through a Markdown renderer (where a hard-break marker is required for the readability goal to actually manifest visually, not merely textually in the source). This does not violate any of the five fixed constraints, the digest, block markers, or reproducibility, so it does not block this plan, but it is a concrete risk to the plan's own primary acceptance criterion that Slice 1's test suite should close.

### Native convergence summary

| Finding | Work units | Rounds | Fingerprints | Claude decisions | Codex dispositions | Final status |
|---|---|---|---|---|---|---|
| `C-01` | `1` | `1` | `59b4027507a427afeef96a4a312ad6ec498ad917c5cf90878093a087389dea24` | `opened:open` | – | `open` |
<!-- artifact-records:findings:end -->
<!-- audit:findings:end -->

### Entscheidungstabelle

<!-- audit:decision-table:begin -->
| ID | Quelle | Finding | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|---|
| C-01 | claude | The plan's lexical prose line-break rule (Verbindliche Entscheidungen items 7-8) replaces a detected break point solely with a single Markdown newline character. In common CommonMark/GitHub-flavored Markdown rendering, a lone &#96;\n&#96; inside a paragraph is a soft break and is visually collapsed back into one line unless a hard-break marker (two trailing spaces, a backslash, or a blank line between blocks) is used. Because the stated purpose of this whole initiative is that 'ein Mensch den Verlauf ... nachvollziehen kann' by scanning sentence- and list-structured prose, Slice 1 should explicitly decide and test whether the projected document is consumed as raw text (where a bare &#96;\n&#96; is sufficient) or through a Markdown renderer (where a hard-break marker is required for the readability goal to actually manifest visually, not merely textually in the source). This does not violate any of the five fixed constraints, the digest, block markers, or reproducibility, so it does not block this plan, but it is a concrete risk to the plan's own primary acceptance criterion that Slice 1's test suite should close. | OBSERVATION | offen | offen |

<!-- artifact-records:decision-table:begin -->
Semantischer Record-Digest: `0d222050e2cb73ac45bb36434024a5eba7caa758f802f8e14f972f247fb0f73a`

| Seq | Record | Typ | Status | Logische ID | Revision | Fingerprint |
|---:|---|---|---|---|---:|---|
| 1 | `ar1-bbfedb29f86a601255043dd4d308c968737e5f37db5abe907b6f737287d73f40` | `task` | `accepted` | `task-contract` | 1 | `contract:70d6744e2ccbff78435f4d61851a326bd7f1cf5312ca8bfb0313be735008ba47` |
| 2 | `ar1-aa3cc0f46b6d25a9abc2fa2b68faf2fc8508c34af698bbd2a6bbd1e04651b278` | `provider_input_measurement` | `measured` | `provider-input-1-codex_plan` | 1 | `implementation:70d6744e2ccbff78435f4d61851a326bd7f1cf5312ca8bfb0313be735008ba47` |
| 3 | `ar1-28fb1ade836bb3ac726406b735f0cca3e7f991ee71505491b6693183b2782052` | `provider_attempt` | `started` | `provider-operation-7e13fe3600424865b38ab326d6d9340601ff9978182dbfeabd93a45f7be0f2af-1` | 1 | `implementation:70d6744e2ccbff78435f4d61851a326bd7f1cf5312ca8bfb0313be735008ba47` |
| 4 | `ar1-a211c69d70b81aa9f0385a4321ca8ec3dafdff29840b16522053e5954c675d8d` | `agent_result` | `ready` | `agent-1-codex_plan-1` | 1 | `contract:70d6744e2ccbff78435f4d61851a326bd7f1cf5312ca8bfb0313be735008ba47` |
| 5 | `ar1-27d9be762956329b757e0a4cb92de162ce2aef235368dc813fd898eb314766e8` | `provider_attempt` | `succeeded` | `provider-operation-7e13fe3600424865b38ab326d6d9340601ff9978182dbfeabd93a45f7be0f2af-1` | 2 | `implementation:70d6744e2ccbff78435f4d61851a326bd7f1cf5312ca8bfb0313be735008ba47` |
| 6 | `ar1-95f54a48b8e145e6e4f4ce60f8b4d66719e4903a6d62e931167de0461ca17fce` | `validation_attestation` | `attested` | `plan-validation-59b4027507a4` | 1 | `implementation:59b4027507a427afeef96a4a312ad6ec498ad917c5cf90878093a087389dea24` |
| 7 | `ar1-aaaacfa1daf7e486265351ee5a9c39948d9b6813141e4434526cda671d001ac5` | `provider_input_measurement` | `measured` | `provider-input-1-claude_plan_review` | 1 | `implementation:70d6744e2ccbff78435f4d61851a326bd7f1cf5312ca8bfb0313be735008ba47` |
| 8 | `ar1-a33b58897e552b8f5baef2111d737a33a195a5ce9dcc5688b798c9de4b82e3d2` | `provider_attempt` | `started` | `provider-operation-e7e87b486fb9c997c99e0ef06a2100cf6bbb3fa559c7b08e47f2da30139dea0c-1` | 1 | `implementation:70d6744e2ccbff78435f4d61851a326bd7f1cf5312ca8bfb0313be735008ba47` |
| 9 | `ar1-763dd9b2fadf0fb974f16fe759e5532b5deeaf66e5aeff5ce9ded059bb920e84` | `review` | `decided` | `review-claude-1-1` | 1 | `implementation:59b4027507a427afeef96a4a312ad6ec498ad917c5cf90878093a087389dea24` |
| 10 | `ar1-e4c01901eaaafd0a676e31110222f8cb0f89fc733601501794c413f10de48c04` | `finding_transition` | `recorded` | `finding-C-01` | 1 | `implementation:59b4027507a427afeef96a4a312ad6ec498ad917c5cf90878093a087389dea24` |
| 11 | `ar1-4b59bd3508e91d76ba2e613c148a5f77890b9315701e510d82ed918b5ae64c2d` | `provider_attempt` | `succeeded` | `provider-operation-e7e87b486fb9c997c99e0ef06a2100cf6bbb3fa559c7b08e47f2da30139dea0c-1` | 2 | `implementation:70d6744e2ccbff78435f4d61851a326bd7f1cf5312ca8bfb0313be735008ba47` |
<!-- artifact-records:decision-table:end -->
<!-- audit:decision-table:end -->

### Freigabestatus

<!-- audit:approval-status:begin -->
- Implementierung bereit: `NOT_RECORDED`
- Validierung: `PASS`
- Claude-Freigabe: `YES`
- Red-State-Folgeslice: `NONE`
- Commit autorisiert: `NO`

<!-- artifact-records:approval-status:begin -->
Semantischer Record-Digest: `0d222050e2cb73ac45bb36434024a5eba7caa758f802f8e14f972f247fb0f73a`

- 4. `ar1-a211c69d70b81aa9f0385a4321ca8ec3dafdff29840b16522053e5954c675d8d`: Agentresult `codex` / `ready`; Work-Unit `1`; Tests keine; Transport `native-codex-v2`; Request `native-codex-request-8d4d22cf869dfc11a19ea169c03ec73fe474ddf2ff1ea712627b64dca2914012`; Response `b7a3102b4ab0d43273c616049359e43c79ff5ba95dcf87a0fa3e5dc8cd611bd0`; Fingerprint `70d6744e2ccbff78435f4d61851a326bd7f1cf5312ca8bfb0313be735008ba47`
<!-- artifact-records:approval-status:end -->
<!-- audit:approval-status:end -->
