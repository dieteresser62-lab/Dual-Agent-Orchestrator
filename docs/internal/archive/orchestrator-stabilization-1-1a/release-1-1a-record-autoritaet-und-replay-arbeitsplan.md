# Arbeitsplan: Release 1.1A1 – Record-Autorität und deterministischer Replaykern

**Status:** zur Planprüfung vorgelegt  
**Anforderungsbasis:** `docs/internal/phase-2-arbeitspaket-1-erkenntnisse-und-stabilisierung-1-1.md`, P2-DEC-001 und P2-FU-002  
**Feature-Branch:** `feature/orchestrator-stabilization-1-1a`  
**Ausführungsgrenze:** höchstens zwei Implementierungsslices; dieses Dokument autorisiert den kleineren, eigenständig nutzbaren Teil 1.1A1  

## Zielbild in eigenen Worten

1.1A1 führt einen gemeinsamen reinen Replaykern ein, der eine bereits vom
`ArtifactStore` geladene append-only Kette genau einmal in einen unveränderlichen
fachlichen Projektionsstand und die daraus abgeleitete Auditsicht reduziert. Die
gleiche Kette liefert bei jedem Aufruf dasselbe Ergebnis; Replay schreibt weder
Records noch State, Audit oder Cache und startet keinen Provider.

Der Structured-Resume- und Auditpfad benutzt dieses Ergebnis an seinen heutigen
Projektionsgrenzen. Er akzeptiert ausschließlich Tatsachen, die durch Records
oder ausdrücklich als unveränderliche Laufstartdaten klassifiziert sind.
Fehlende, doppelte, unbekannte, typfalsche, run-fremde, referenz- oder
fingerprintfremde Tatsachen erhalten stabile Diagnosecodes und halten vor einem
externen Seiteneffekt fail-closed an. `legacy-state-v3` zweigt vor Store und
Replay ab. `head.json` bleibt ein ausschließlich aus der Kette rekonstruierbarer
Cache; der erwartete Fortschritt durch den eigenen Append ist kein Fremdzugriff.

Der aktuelle Bestand erlaubt innerhalb der harten Grenze noch keinen
vollständigen Neuaufbau jedes `WorkflowState`-Feldes: `src/workflow_state.py`
führt umfangreiche Laufzeit- und Historienfelder, während
`src/artifact_migration.py` Records heute gegen State vergleicht und mehrere
einzelfallbezogene record-voraus Lücken erkennt. Sämtliche Liveübergänge auf
record-first umzustellen würde außerdem die ausdrücklich ausgeschlossenen neuen
Transition-IDs berühren. Deshalb schneidet dieser Plan 1.1A1 am gemeinsamen
Replaykern und den vorhandenen Projektionsgrenzen ab. Der vollständige Abbau
aller unabhängigen State-Schreibentscheidungen ist 1.1A2 und wird hier weder
implizit autorisiert noch vorweggenommen.

## Branch-, Status- und Scope-Festlegung

- Der geforderte Branch `feature/orchestrator-stabilization-1-1a` war bei der
  Planung aktiv.
- In diesem Planungslauf wird ausschließlich dieses Arbeitsplandokument
  angelegt. Produktcode, Tests, Konfiguration, State, Checkpoints und generierte
  Artefakte bleiben unverändert.
- Die spätere Umsetzung ist auf die in den beiden Slice-Abschnitten genannten
  Pfade beschränkt. Pro Slice sind es drei produktive Dateien; Tests und das
  verwaltete Sliceprotokoll zählen nicht gegen die Acht-Dateien-Grenze.
- Unabhängige Änderungen zu Gates, Finalreview, Watch-Finalisierung,
  Provider-I/O, Kostenbudgets, Telemetrie, Textmarkern oder allgemeinem Logging
  sind ausgeschlossen.
- Vor jedem Slice werden Branch und kanonischer Diff geprüft. Fremde
  Arbeitsstände bleiben unangetastet; Push, Merge, Branchwechsel, Staging,
  Commit und manuelle Reparaturen unter `.orchestrator/` sind nicht Teil der
  Implementierung.

## Repositorybefund und Projektionsgrenzen

| Pfad | Heutige Verantwortung | Konkreter Integrationsgrund |
|---|---|---|
| `src/artifact_store.py` | atomare Recordveröffentlichung, vollständiger Kettenscan, Reihenfolge und `head.json` | liefert die autoritative validierte Eingabe; P2-FU-002 entsteht, weil `put()` nach Veröffentlichung über `load_chain()` einen erwartbar alten Cache sieht |
| `src/artifact_projection.py` | semantischer Digest und deterministische Markdown-Abschnitte | enthält bereits Auditreduktion, aber noch keinen gemeinsam nutzbaren fachlichen Replaystand |
| `src/artifact_migration.py` | Structured-/Legacy-Abzweig und State-v3-Gleichheitsprüfungen | ist die bestehende Resumegrenze und enthält die fallbezogenen record-voraus Ausnahmen, die durch typisierte Replaydiagnosen sichtbar und begrenzt werden müssen |
| `src/orchestrator.py` | Bindung des Stores, Pre-Side-Effect-Prüfung, Checkpoint- und Auditaufrufe | ist der kleinste zentrale Anschluss für Replay vor Provideraufruf und vor Projektion, ohne einzelne Fachtransitionen neu zu ordnen |
| `src/audit_trail.py` | atomare Projektion verwalteter Markdownbereiche | muss exakt die vom gemeinsamen Replaykern erzeugte Auditsicht schreiben und darf Markdown nie als Reparaturquelle lesen |

`src/artifact_models.py` bleibt unverändert: Die vorhandenen typisierten Payloads
und Referenzen reichen für den Kern. `src/workflow_state.py` bleibt ebenfalls
unverändert, weil 1.1A1 keine neue State-Semantik oder Transition-ID einführt.
Erweist ein Test, dass eine für 1.1A1 benötigte fachliche Aussage nur dort oder
nur im Audit existiert, greift die Stopbedingung statt einer Schemaerweiterung.

## Verbindliche Entscheidungen und Umsetzungskonsequenzen

1. `ArtifactStore.load_chain()` bleibt die einzige Quelle für veröffentlichte
   Structured-Records. Der Replaykern nimmt keine Pfade und keinen State als
   alternative Wahrheit an.
2. Der neue Kern ist rein: Eingabe sind Kette, erwartete Run-ID und explizit
   allowlistete unveränderliche Laufstartdaten; Ausgabe sind ein immutable
   Projektionsobjekt, Auditereignisse und gegebenenfalls eine typisierte
   Diagnose. Zeit, Dateisystem, Logger, Provider und globale Zustände sind keine
   Eingaben.
3. Diagnosecodes werden an der Replaygrenze stabilisiert. Mindestens
   `RECORD-MISSING`, `RECORD-DUPLICATE`, `RECORD-UNKNOWN`, `RECORD-TYPE-MISMATCH`,
   `RECORD-RUN-MISMATCH`, `RECORD-REFERENCE-MISSING`,
   `RECORD-FINGERPRINT-MISMATCH`, `MIRROR-AHEAD` und `MIRROR-AMBIGUOUS` sind
   maschinenlesbar testbar; Detailtext darf ergänzen, aber nicht als Code dienen.
4. Die existierende Storevalidierung bleibt erste Verteidigungslinie. Der
   Reducer validiert zusätzlich semantische Referenzen und Fingerprintbindungen,
   die erst über mehrere Records erkennbar sind. Keine Fail-closed-,
   Revieweigentums- oder Attestierungsgarantie wird gelockert.
5. Structured-State und Audit werden an den angeschlossenen Grenzen gegen
   dasselbe Replayergebnis geprüft beziehungsweise daraus projektiert. Ein
   eindeutig record-voraus liegender Spiegel darf nur für bereits vollständig
   repräsentierte Fakten deterministisch nachgezogen werden. State-voraus,
   Mehrdeutigkeit oder eine nicht repräsentierte Fachentscheidung halten an.
6. Legacy kehrt in `resolve_resume_state()` vor Storezugriff und Reducer zurück.
   Es entstehen weder Records noch eine Protokollbindung; bestehende
   Legacy-Fixtures bleiben unverändert.
7. `ArtifactStore.put()` übergibt beim unmittelbar folgenden Scan den zuvor
   gelesenen Cachezustand beziehungsweise den erwarteten eigenen Fortschritt.
   Nur genau dieser Übergang unterdrückt die Warnung. Cacheverlust wird still
   rekonstruiert; fremder, manipulierter oder anderweitig inkonsistenter Cache
   bleibt warnend sichtbar und wird ausschließlich aus validierten Records
   ersetzt.
8. Auditprojektion bleibt atomar und idempotent. Ein Crash vor dem Replace
   lässt die alte vollständige Sicht stehen; nach dem Replace liegt die neue
   vollständige Sicht vor. Ein erneuter Replay schreibt bei Bytegleichheit
   nicht erneut.

## Slice-Liste

### Slice 1 - Reiner Record-Replaykern und Cacheautorität

**Ziel**

Einen dateisystemfreien Reducer mit stabilen Diagnosen einführen, die bestehende
Auditreduktion darauf aufsetzen und P2-FU-002 an der Storegrenze korrigieren.
Der Slice ist allein nutzbar: Aufrufer können eine Store-validierte Kette
deterministisch prüfen und projizieren, ohne Workflow oder Provider zu starten.

**Exakter Änderungspfad**

- `src/artifact_replay.py`
- `src/artifact_projection.py`
- `src/artifact_store.py`
- `tests/test_artifact_replay.py`
- `tests/test_artifact_projection.py`
- `tests/test_artifact_store.py`
- `docs/internal/slice-release-1-1a-record-autoritaet-und-replay-arbeitsplan-01-reiner-record-replaykern-und-cacheautoritat.md`

**Integrationsschritte**

1. `src/artifact_replay.py` definiert das immutable Replayergebnis, reine
   Record-für-Record-Reduktion und stabile Diagnosetypen. Es prüft Recordtyp,
   Runbindung, Eindeutigkeit, Vorgänger-/Payloadreferenzen, zulässige Revisionen
   und Fingerprintbeziehungen ohne Mutationen.
2. `src/artifact_projection.py` rendert seine bestehenden Sections aus dem
   Replayergebnis. Bestehende öffentliche Funktionen bleiben als
   rückwärtskompatible Adapter erhalten und liefern byteidentische Ausgabe.
3. `src/artifact_store.py` unterscheidet den erwarteten Cachefortschritt direkt
   nach dem eigenen atomaren Append von einem unabhängig beobachteten stale oder
   manipulierten Cache. Records bleiben unter allen Fehlern autoritativ.

**Akzeptanzkriterien**

- Zwei oder mehr Reduktionen derselben synthetischen Kette sind strukturell und
  byteweise identisch; Zeitstempel beeinflussen nur dort Ergebnisse, wo sie
  ausdrücklich fachliche Payloaddaten sind.
- Wiederholter Replay verändert weder Eingabe noch Repository und erzeugt
  keinen Record, Cachewrite, Auditwrite oder Adapteraufruf.
- Fehlende, doppelte, unbekannte, typfalsche, run-fremde, referenz- und
  fingerprintfremde Records werden mit dem erwarteten Diagnosecode abgelehnt.
- Temporäre Recorddateien werden nicht zur Wahrheit; nach atomarer
  Veröffentlichung findet der Scan genau den einen Record wieder.
- Cacheverlust und eigener erwarteter Cachefortschritt erzeugen keine Warnung.
  Ein fremder/manipulierter Cache erzeugt genau die relevante Warnung und wird
  aus der validen Kette rekonstruiert.
- Die bestehende Auditprojektion und ihre Escape-/Slice-Filterregressionen
  bleiben semantisch unverändert.

**Geplante fokussierte Tests**

- `tests/test_artifact_replay.py`: identische Mehrfachläufe, Unveränderlichkeit,
  Ketten- und Referenzfehler, falsche Runs/Typen/Fingerprints sowie explizite
  Fake-Adapter-Zähler auf null.
- `tests/test_artifact_store.py`: Crash vor Append, liegen gebliebene temporäre
  Datei, Crash nach Veröffentlichung, Cacheverlust, eigener Cachefortschritt
  ohne Warnung und manipulierter Cache mit Warnung/Rekonstruktion.
- `tests/test_artifact_projection.py`: byteidentische bestehende Abschnitte,
  mehrfacher Replay, stabile Reihenfolge und keine Interpretation von Markdown
  als Fachfakt.

**Risiko und Rückfalloption**

Das größte Risiko ist eine überstrenge querschnittliche Fingerprintregel für
Recordtypen, deren Bindung absichtlich anders ist. Der Reducer verwendet daher
eine explizite typbezogene Referenz-/Fingerprintmatrix aus den vorhandenen
Payloadverträgen. Bei Unsicherheit wird fail-closed erweitert, nicht eine
bestehende Prüfung entfernt. Der Slice kann durch Entfernen des neuen Aufrufs
zur bisherigen Projektion zurückkehren; Recordformate werden nicht migriert.

### Slice 2 - Structured-Resume- und Auditgrenzen aus gemeinsamem Replay

**Ziel**

Den Replaykern vor Structured-Resume, externen Seiteneffekten und atomarer
Auditprojektion anschließen. Nur bereits vollständig durch Records
repräsentierte Mirrorfakten dürfen deterministisch nachgezogen werden; alle
anderen Divergenzen bleiben mit stabiler Diagnose gesperrt. Legacy wird vor
diesem Pfad nachweislich abgezweigt.

**Exakter Änderungspfad**

- `src/artifact_migration.py`
- `src/orchestrator.py`
- `src/audit_trail.py`
- `tests/test_artifact_migration.py`
- `tests/test_orchestrator_runtime.py`
- `tests/test_audit_trail.py`
- `tests/test_structured_artifact_regressions.py`
- `docs/internal/slice-release-1-1a-record-autoritaet-und-replay-arbeitsplan-02-structured-resume-und-auditgrenzen-aus-gemeinsamem-replay.md`

**Integrationsschritte**

1. `src/artifact_migration.py` ruft nach der bestehenden frühen Legacy-Abzweigung
   den Reducer auf. Die bisherigen verteilten Vergleichs- und Recoveryfälle
   werden nur soweit ersetzt, wie das Replayergebnis dieselbe Fachinformation
   vollständig trägt; nicht ableitbare Fälle liefern `MIRROR-AHEAD` oder
   `MIRROR-AMBIGUOUS` statt erfundener Reparatur.
2. `src/orchestrator.py` verwendet dasselbe Replayergebnis beim Binden/Resume,
   vor Providerstarts und vor Checkpoint-/Auditprojektion. Ein nach
   Recordveröffentlichung unterbrochener, eindeutig repräsentierter Schritt
   wird ohne erneuten Agentenaufruf und ohne weiteren Record erkannt. Der Slice
   ordnet keine fachlichen Livewrites neu und führt keine Transition-ID ein.
3. `src/audit_trail.py` akzeptiert die aus dem Replayergebnis stammenden
   Sections und ersetzt weiterhin ausschließlich vollständige verwaltete
   Blöcke atomar. State- oder Markdowntext wird nicht zurück in Fachfakten
   geparst.

**Akzeptanzkriterien**

- Structured-Resume prüft State und Audit gegen denselben Record-Head und
  Replaydigest; ein eindeutig record-voraus liegender, vollständig
  repräsentierter Spiegel wird beim nächsten kontrollierten Checkpoint/Auditwrite
  nachgezogen, ohne zweiten Provideraufruf oder Record.
- State-voraus ohne passenden Record, unterschiedliche Heads/Digests und
  mehrdeutige Referenzen halten vor Seiteneffekten mit stabilem Code an.
- Crash nach Recordveröffentlichung, nach Stateprojektion sowie unmittelbar vor
  und nach Auditprojektion konvergiert beim wiederholten Resume auf denselben
  Stand. Crash vor Append oder bei temporärer Datei erzeugt keine Entscheidung.
- Structured- und Legacy-Fixtures benutzen getrennte Pfade. Ein Legacy-Resume
  öffnet keinen `ArtifactStore`, erzeugt keine Records und ergänzt keine
  Protokollbindung.
- Bestehende Resume-, Review-, Gate-, Commit-, Completion- und
  Korrektur-Regressionen ändern ihre Fachsemantik nicht.

**Geplante fokussierte Tests**

- `tests/test_artifact_migration.py`: record-voraus, mirror-voraus,
  Head-/Digestabweichung, fehlende und fremde Referenzen sowie frühe
  Legacy-Abzweigung mit einem Store-Fake, der bei Benutzung fehlschlägt.
- `tests/test_orchestrator_runtime.py`: Fake-Adapter und Call-Counter für Crash
  vor Append, nach Veröffentlichung, nach Stateprojektion und vor/nach
  Auditprojektion; wiederholter Resume bleibt bei genau einem fachlich bereits
  persistierten Aufruf und einem Record.
- `tests/test_audit_trail.py`: atomarer alter/neuer Gesamtstand, bytegleicher
  No-op-Replay und Ablehnung partieller oder fremder Projektionen.
- `tests/test_structured_artifact_regressions.py`: getrennte Structured-/Legacy-
  Fixtures sowie unveränderte Review-, Gate-, Commit-, Completion- und
  Korrekturabläufe.

**Risiko und Rückfalloption**

Das größte Risiko ist, eine operative State-Eigenschaft fälschlich als
record-repräsentierte Fachentscheidung einzustufen. Jede nachziehbare Eigenschaft
braucht deshalb einen positiven Recordherkunftstest und einen negativen
State-voraus-Test. Fehlt die eindeutige Herkunft, stoppt der Slice kontrolliert;
er erweitert weder Recordmodelle noch Legacy. Der Rückfall besteht darin, die
Integration am Resume-/Auditadapter zu entfernen; der reine Kern aus Slice 1
bleibt nutzbar.

## Reihenfolge und Abhängigkeitsgraph

`Slice 1 (Store-validierte Kette → reines Replayergebnis)` →
`Slice 2 (Replayergebnis → Structured-Resume/Auditgrenzen)`.

Slice 2 beginnt erst nach Commit und doppelter Reviewfreigabe von Slice 1.
Jeder Slice muss für sich fokussierte Tests, die vollständige vom Orchestrator
ausgeführte Repositorymatrix und `git diff --check` bestehen. Ein roter oder
mehrdeutiger Structured-Fall wird nicht durch Legacyfallback umgangen.

## Abdeckung der Zielzustände

| Ziel | 1.1A1-Abdeckung | Nicht autorisierter Rest |
|---|---|---|
| Recordkette als fachliche Autorität | Store und Replay akzeptieren keine andere Structured-Quelle | Umordnung noch state-first schreibender Liveübergänge in 1.1A2 |
| gemeinsamer deterministischer Reducer | vollständig in Slice 1; Audit und Resume nutzen ihn in Slice 2 | vollständige Materialisierung aller `WorkflowState`-Felder in 1.1A2 |
| keine unabhängigen Structured-Entscheidungen in Spiegeln | für angeschlossene, record-repräsentierte Projektionsgrenzen fail-closed | vollständiges Inventar und Entfernen aller verbleibenden Mirrorwrites in 1.1A2 |
| Crash/Resume ohne zweite Entscheidung | Matrix für Store, angeschlossene Projektionen und Fake-Provider | neue atomare Transition-IDs und sämtliche Liveübergänge ausdrücklich später |
| korrupte/mehrdeutige Ketten | Store plus stabile Replaydiagnosen | keine |
| Structured-/Legacy-Trennung | frühe Abzweigung und getrennte Fixtures | keine Migration vorgesehen |
| `head.json` als Cache | vollständig in Slice 1 | allgemeine Loggingbereinigung bleibt Nichtziel |

## Migration und Rollback

Es gibt keine Datenmigration und keine synthetischen Records. Persistierte
Protokollbindungen ändern sich nicht. Historical States ohne Binding bleiben
`legacy-state-v3`; ein Structured-Lauf ohne valide passende Kette bleibt
gesperrt. Weder State noch Audit noch Recorddateien werden manuell umgeschrieben.

Der Codeumbau ist additiv und adaptergebunden. Ein Rückfall erfolgt durch einen
normalen Folgecommit, der die Integration auf den vorherigen Reader/Renderer
zurückführt; kein Reset, keine Kettenänderung und kein Löschen von Artefakten ist
erforderlich. Da Slice 1 das Persistenzschema nicht ändert, bleiben seine
Records für alte und neue Leser gleich.

## Test- und Validierungsplan

Alle neuen Tests verwenden ausschließlich synthetische typisierte Records,
temporäre Repositories, Monkeypatches und Fake-Adapter. Echte Providerprozesse,
Netzwerkzugriff, Zugangsdaten und echte `.orchestrator/state.json`-Reparaturen
sind verboten.

Die kombinierte Matrix umfasst:

- identische Projektion derselben Kette über mehrere Prozesse/Aufrufe;
- wiederholten Replay ohne Mutation, Doppelrecord oder Provideraufruf;
- jeden geforderten Crashpunkt vor Append, bei temporärer Datei, nach atomarer
  Veröffentlichung, nach Stateprojektion sowie vor und nach Auditprojektion;
- eindeutig record-voraus liegende State-/Auditspiegel und State-voraus ohne
  Record als fail-closed Fall;
- fehlende, doppelte, unbekannte, typfalsche, run-fremde, referenz- und
  fingerprintfremde Records;
- Cacheverlust, erwarteten eigenen Fortschritt ohne Warnung und manipulierten
  Cache mit Warnung und Rekonstruktion;
- nachweislich getrennte Structured-/Legacy-Fixtures;
- bestehende Resume-, Review-, Gate-, Commit-, Completion- und
  Korrekturregressionen ohne semantische Änderung.

Codex führt während der Slices nur die betroffenen Testmodule und
`git diff --check` aus. Nach jedem Slice führt ausschließlich der Orchestrator
die konfigurierte vollständige Matrix aus, einschließlich
`python3 -m pytest tests/ -v`. Agenten emittieren kein `VALIDATION_RESULT`.

## Nichtziele und Folgepaket 1.1A2

1.1A2 muss separat geplant und autorisiert werden. Es enthält das vollständige
Inventar verbleibender fachlicher State-/Auditwrites, deren record-first
Neuordnung, gegebenenfalls neue Transition-IDs und die vollständige
`WorkflowState`-Rekonstruktion. Dieses Dokument autorisiert dafür keinen Pfad.

Ebenfalls ausgeschlossen bleiben pfaddigestgebundene Nutzergates, Änderungen am
Finalreviewvertrag, Codex-Defektkandidaten, Evidence-Deltas, Reviewerbudgets,
Usage-Telemetrie, weitere Kostenbremsen, Watch-/Direkt-Finalisierung, native
Agenten-JSON-I/O, Providerfunktionen, Textmarkerablösung und Loggingänderungen
außerhalb des eng begrenzten `head.json`-Falls. Diese Themen gehören zu 1.1B
oder 1.1C.

## Stopbedingungen

Die Implementierung hält kontrolliert und resumierbar an, wenn eine für den
jeweiligen Slice benötigte Fachentscheidung nicht eindeutig aus Records oder
allowlisteten unveränderlichen Laufstartdaten folgt, eine Reparatur manuelle
Artefaktänderungen verlangte, Legacy migriert werden müsste, eine bestehende
Fail-closed-/Fingerprint-/Revieweigentums-/Attestierungsgarantie schwächer
würde, ein Slice mehr als acht produktive Dateien benötigte oder eine
Projektionsgrenze nicht mit den exakten Pfaden dieses Plans erreichbar wäre.
Der Halt erweitert nicht automatisch den Scope; insbesondere werden
`src/artifact_models.py` und `src/workflow_state.py` nicht beiläufig aufgenommen.

## Offene Fragen

Keine für 1.1A1. Die bewusste Produktentscheidung ist bereits getroffen: Der
kleinste sichere Kern wird jetzt umgesetzt; der vollständige Live-Cutover wird
als 1.1A2 neu geplant.

## Review-Feedback von Claude

<!-- audit:claude-review:begin -->
### Ereignis 2: Runde 1

- Reviewer: `claude`
- Freigabe: `YES`
- Validierungsbindung: `plan-validation-5d3c284b1a4f`
- Testdateien: keine
- Prüfdimensionen: scope-limit arithmetic, Slice independence/ordering, non-goal fidelity, crash/resume matrix completeness against the assignment's mandatory test list, cache-authority (P2-FU-002) handling, legacy/structured branch separation
- Größtes Restrisiko: largest residual risk is that the reducer's type/reference/fingerprint matrix in Slice 1 is built against today's &#96;artifact_models.py&#96; payload set and could drift if that set changes before Slice 2 lands
- Realistische Bruchbedingung: break condition: a Slice 1 or Slice 2 implementation review finds a named repository hook (resolve_resume_state, put()/load_chain cache diff, audit_trail atomic replace) does not match its described current responsibility, or a productive file count exceeds the declared 3-per-Slice budget
- Eigene Findings: `C-01`

<!-- artifact-records:claude-review:begin -->
Semantischer Record-Digest: `333ea6c0b9e621c00f48fedf0daad2a0acf5fcb2ca2607d92c3cb5d4b04bba99`

- 6. `ar1-e50357433628b99d0e1af4e4bffb4f245306df6b9f83f86000b22039d57d4f84`: `approved`; Work-Unit `1`; Findings `C-01`; Fingerprint `5d3c284b1a4fb771949bb0a20a8c386250e3d0634c49bc27a3ab712612c75691`
<!-- artifact-records:claude-review:end -->
<!-- audit:claude-review:end -->

## Review-Feedback von Antigravity

<!-- audit:antigravity-review:begin -->
### Ereignis 3: Runde 1

- Reviewer: `antigravity`
- Freigabe: `YES`
- Validierungsbindung: `plan-validation-5d3c284b1a4f`
- Testdateien: keine
- Prüfdimensionen: scope-limit compliance (2 slices, 3 productive files each), slice ordering and decoupling, repository hook verification (resolve_resume_state, ArtifactStore.put cache progression, audit_trail managed block replacement), crash/recovery failure matrix across all projection boundaries, legacy/structured branch isolation without silent migration, non-goal discipline (deferred 1.1A2 cutover, no transition-IDs, frozen artifact_models.py)
- Größtes Restrisiko: latent semantic gaps between existing WorkflowState fields and pure record-derived state during Slice 2 integration, requiring unrepresented mirror mutations or premature schema expansion
- Realistische Bruchbedingung: Slice 2 resume integration discovers a required runtime state property that cannot be deterministically reconstructed from existing record payloads without altering workflow_state.py schema or adding new transition IDs
- Eigene Findings: keine

<!-- artifact-records:antigravity-review:begin -->
Semantischer Record-Digest: `333ea6c0b9e621c00f48fedf0daad2a0acf5fcb2ca2607d92c3cb5d4b04bba99`

- 9. `ar1-9f486d3a9dffa9e610e69c58abea713a0b3d78c165e7a1fa79d109e09c00af2f`: `approved`; Work-Unit `1`; Findings `C-01`; Fingerprint `5d3c284b1a4fb771949bb0a20a8c386250e3d0634c49bc27a3ab712612c75691`
<!-- artifact-records:antigravity-review:end -->
<!-- audit:antigravity-review:end -->

## Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
Noch keine strukturierten Codex-Antworten.

<!-- artifact-records:codex-responses:begin -->
Semantischer Record-Digest: `333ea6c0b9e621c00f48fedf0daad2a0acf5fcb2ca2607d92c3cb5d4b04bba99`

Keine Codex-Findingantworten.
<!-- artifact-records:codex-responses:end -->
<!-- audit:codex-responses:end -->

## Planstatus und formale Marker

<!-- audit:validation-attestation:begin -->
### Ereignis 1: `plan-validation-5d3c284b1a4f`

- Diff-Fingerprint: `5d3c284b1a4fb771949bb0a20a8c386250e3d0634c49bc27a3ab712612c75691`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: internal plan contract passed
- Ausgabedigest: `e5f33cdc2f72c8553eeecd9043b5543337197f21337199e8e3ed4653330ffd6b`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| internal:work-plan-contract | PASS | 0 | slices=1; planned_paths=1; changed_paths=1; future_slices=2; work_plan=docs/internal/release-1-1a-record-autoritaet-und-replay-arbeitsplan.md |

<!-- artifact-records:validation-attestation:begin -->
Semantischer Record-Digest: `333ea6c0b9e621c00f48fedf0daad2a0acf5fcb2ca2607d92c3cb5d4b04bba99`

- 2. `ar1-412e3ba17f5c6f6aabff3db48d869cc7b9d7fbcfcf1265527c70fa0f2ba3e62d`: Providerinput `codex/codex_plan` = `allowed`; Zeichen `18260/4000000`, Bytes `18327/16000000`; Input `a4b3c299be75c8042ba9f1938dcb7f2182a28bda082471b677e988b270089d13`, Policy `8cff2b572a65674a0b31c2e2751cdc76fa839112b05e38b09bd021a02284bc83`, Übergang `260d2b5f305af9ca50ca257acdc52b6a1dbf84c070f7c69bd498ff615446c254`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, größte Komponente `stdin_prompt`; Komponenten `stdin_prompt=18260/18327`
- 4. `ar1-0a78d798d4289561e5011a953450bf1a46c6882c3dd6d56c0481c3a08681db94`: Attestierung durch `orchestrator`; Fingerprint `5d3c284b1a4fb771949bb0a20a8c386250e3d0634c49bc27a3ab712612c75691`
  - `pass` / Exit `0` / Output `e5f33cdc2f72c8553eeecd9043b5543337197f21337199e8e3ed4653330ffd6b`: `argv` [`internal:work-plan-contract`]
- 5. `ar1-7b7db06ad9010f8adfd6260ec05275bd9bb82b3d583c5adb2629667d905be1cf`: Providerinput `claude/claude_plan_review` = `allowed`; Zeichen `47083/4000000`, Bytes `47377/16000000`; Input `d36fa010369126dda5525ce393bc727b056571eb0faa2bd170fd12718e026838`, Policy `8cff2b572a65674a0b31c2e2751cdc76fa839112b05e38b09bd021a02284bc83`, Übergang `91357a895255f6c485c187966b94fbd653fff98267467009937c7c7fe634f115`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, größte Komponente `packet_chunk_001`; Komponenten `packet_chunk_001=23994/24128, packet_chunk_002=21524/21684, packet_manifest=417/417, system_policy=660/660, response_schema=146/146, start_directive=342/342`
- 8. `ar1-23e6955e3d234f5a2cb554a2097105a7da2e6a163facaaa1a185bf6ce6a94332`: Providerinput `antigravity/antigravity_plan_review` = `allowed`; Zeichen `51030/4000000`, Bytes `51328/16000000`; Input `871ca7d08ec969945c759f52fabc603e182d9cf5427fe53158cc231f719ae594`, Policy `8cff2b572a65674a0b31c2e2751cdc76fa839112b05e38b09bd021a02284bc83`, Übergang `863fd6f9b82c5ddbdaba4b67f9ac9a5643f27c037ae670b4660948a133c14aa8`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, größte Komponente `prompt_file`; Komponenten `prompt_file=50371/50669, response_schema=146/146, start_directive=513/513`
<!-- artifact-records:validation-attestation:end -->
<!-- audit:validation-attestation:end -->

<!-- audit:test-approval-premortem:begin -->
- Teständerungsfreigabe: nicht erfasst.
- Pre-Mortems:
  - Ereignis 2: Most likely three-month failure cause is drift between the frozen &#96;artifact_models.py&#96; payload/reference set and the Slice-1 reducer's type/reference/fingerprint validation matrix — a later, still-unauthorized 1.1A2 or an unrelated fix adds/changes a record payload without updating the matrix, producing either false RECORD-TYPE-MISMATCH rejections on legitimate new records or a silently permissive validation gap, since the plan deliberately keeps &#96;artifact_models.py&#96; out of scope for 1.1A1.
  - Ereignis 3: In three months, uncoordinated additions of new record types or state transitions in follow-up work (such as 1.1A2 or 1.1B) update only one side of the dual-write boundary or introduce non-deterministic reducer logic (e.g. wall-clock timestamps or environment-dependent paths), triggering replay divergence or false MIRROR-AHEAD / RECORD-TYPE-MISMATCH rejections on valid runs.

<!-- artifact-records:test-approval-premortem:begin -->
Semantischer Record-Digest: `333ea6c0b9e621c00f48fedf0daad2a0acf5fcb2ca2607d92c3cb5d4b04bba99`

Keine strukturierten Gates.
<!-- artifact-records:test-approval-premortem:end -->
<!-- audit:test-approval-premortem:end -->

<!-- audit:findings:begin -->
### `C-01` — `OPEN`

- Quelle: `claude`; Runde 1
- Klasse: `OBSERVATION`
- Finding: Named integration hooks (resolve_resume_state() in artifact_migration.py; ArtifactStore.put()/load_chain() cache-diff point; audit_trail atomic managed-block replace) are asserted but not shown in this plan-only evidence packet.
- Akzeptanztest: During Slice 1 and Slice 2 implementation review, confirm each named hook exists with the described current responsibility before accepting the corresponding integration step; if a hook differs materially, the Slice must re-derive the integration point rather than force-fit this plan's assumption.
- Statusbegründung: –

<!-- artifact-records:findings:begin -->
Semantischer Record-Digest: `333ea6c0b9e621c00f48fedf0daad2a0acf5fcb2ca2607d92c3cb5d4b04bba99`

- 7. `ar1-722a413413c887b73625d41d17197a9465cdc104201ec3820fd29df3d48935ee`: `C-01` `opened` durch `claude`; `OBSERVATION` / `open` — Named integration hooks (resolve_resume_state() in artifact_migration.py; ArtifactStore.put()/load_chain() cache-diff point; audit_trail atomic managed-block replace) are asserted but not shown in this plan-only evidence packet.
<!-- artifact-records:findings:end -->
<!-- audit:findings:end -->

<!-- audit:decision-table:begin -->
| ID | Quelle | Finding | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|---|
| C-01 | claude | Named integration hooks (resolve_resume_state() in artifact_migration.py; ArtifactStore.put()/load_chain() cache-diff point; audit_trail atomic managed-block replace) are asserted but not shown in this plan-only evidence packet. | OBSERVATION | offen | offen |

<!-- artifact-records:decision-table:begin -->
Semantischer Record-Digest: `333ea6c0b9e621c00f48fedf0daad2a0acf5fcb2ca2607d92c3cb5d4b04bba99`

| Seq | Record | Typ | Status | Logische ID | Revision | Fingerprint |
|---:|---|---|---|---|---:|---|
| 1 | `ar1-caf5aeebb1f3fea4b4a85c8e79a690ad4a4655ce3b502fc6fe1188374b8dd4cf` | `task` | `accepted` | `task-contract` | 1 | `contract:ce52f1553688e46276ff0e2849b258e7c54344da604beea4c757f8c016c16a58` |
| 2 | `ar1-412e3ba17f5c6f6aabff3db48d869cc7b9d7fbcfcf1265527c70fa0f2ba3e62d` | `provider_input_measurement` | `measured` | `provider-input-1-codex_plan` | 1 | `implementation:ce52f1553688e46276ff0e2849b258e7c54344da604beea4c757f8c016c16a58` |
| 3 | `ar1-84ba8c6fdb9a46376f8b02b3f9484993b85eef82fadd1b764469b3422bf227b2` | `agent_result` | `ready` | `agent-1-codex_plan-1` | 1 | `contract:ce52f1553688e46276ff0e2849b258e7c54344da604beea4c757f8c016c16a58` |
| 4 | `ar1-0a78d798d4289561e5011a953450bf1a46c6882c3dd6d56c0481c3a08681db94` | `validation_attestation` | `attested` | `plan-validation-5d3c284b1a4f` | 1 | `implementation:5d3c284b1a4fb771949bb0a20a8c386250e3d0634c49bc27a3ab712612c75691` |
| 5 | `ar1-7b7db06ad9010f8adfd6260ec05275bd9bb82b3d583c5adb2629667d905be1cf` | `provider_input_measurement` | `measured` | `provider-input-1-claude_plan_review` | 1 | `implementation:ce52f1553688e46276ff0e2849b258e7c54344da604beea4c757f8c016c16a58` |
| 6 | `ar1-e50357433628b99d0e1af4e4bffb4f245306df6b9f83f86000b22039d57d4f84` | `review` | `decided` | `review-claude-1-1` | 1 | `implementation:5d3c284b1a4fb771949bb0a20a8c386250e3d0634c49bc27a3ab712612c75691` |
| 7 | `ar1-722a413413c887b73625d41d17197a9465cdc104201ec3820fd29df3d48935ee` | `finding_transition` | `recorded` | `finding-C-01` | 1 | `implementation:5d3c284b1a4fb771949bb0a20a8c386250e3d0634c49bc27a3ab712612c75691` |
| 8 | `ar1-23e6955e3d234f5a2cb554a2097105a7da2e6a163facaaa1a185bf6ce6a94332` | `provider_input_measurement` | `measured` | `provider-input-1-antigravity_plan_review` | 1 | `implementation:ce52f1553688e46276ff0e2849b258e7c54344da604beea4c757f8c016c16a58` |
| 9 | `ar1-9f486d3a9dffa9e610e69c58abea713a0b3d78c165e7a1fa79d109e09c00af2f` | `review` | `decided` | `review-antigravity-1-1` | 1 | `implementation:5d3c284b1a4fb771949bb0a20a8c386250e3d0634c49bc27a3ab712612c75691` |
<!-- artifact-records:decision-table:end -->
<!-- audit:decision-table:end -->

<!-- audit:approval-status:begin -->
- Implementierung bereit: `NOT_RECORDED`
- Validierung: `PASS`
- Claude-Freigabe: `YES`
- Antigravity-Freigabe: `YES`
- Red-State-Folgeslice: `NONE`
- Commit autorisiert: `NO`

<!-- artifact-records:approval-status:begin -->
Semantischer Record-Digest: `333ea6c0b9e621c00f48fedf0daad2a0acf5fcb2ca2607d92c3cb5d4b04bba99`

Keine Work-Unit- oder Binding-Records.
<!-- artifact-records:approval-status:end -->
<!-- audit:approval-status:end -->
