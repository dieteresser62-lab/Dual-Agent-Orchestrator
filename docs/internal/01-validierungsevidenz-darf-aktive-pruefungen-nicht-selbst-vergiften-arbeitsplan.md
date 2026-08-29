# Arbeitsplan – Validierungsevidenz ohne Selbstvergiftung und gebundene Recovery

TARGET_BRANCH: feature/audit-evidence-self-poisoning-guard

BASE_COMMIT: 78a1a3949b52

STATUS: BEREIT_ZUR_PLANPRUEFUNG

## 1. Zielbild und unverhandelbare Invarianten

Automatisch aus der strukturierten Recordkette erzeugte Markdown-Evidenz wird
als abgeleitete, nicht vertrauenswürdige Projektion behandelt. Eine Projektion
darf bei unverändertem fachlichem Repositoryinhalt weder den kanonischen
Fingerprint noch Inhaltsguards, Reviewpakete oder die nächste Validierung
verändern. Sichtbarer, von Menschen oder Fachlogik gepflegter Markdowntext
bleibt vollständig in allen zutreffenden Prüfungen.

Die zweite Invariante betrifft Fortsetzung: `--resume` bezeichnet immer den
bereits gebundenen Lauf. Fehlt nach einer Poison-Finalisierung die Queue-
Identität, darf weder eine Ersatz-Run-ID entstehen noch State, Recordkette,
Auditprojektion oder Arbeitsbaum eines neuen Laufs verändert werden. Recovery
ist eine explizite, lokale, providerfreie Transaktion über bereits vorhandene
Fakten; sie erfindet und verändert keine append-only Records.

Schließlich wird eine native Provideranfrage erst dann unter einer dauerhaften
Recovery-Identität veröffentlicht, wenn alle nachgelagerten lokalen
Startbarrieren für genau ihren Übergang bestanden sind. Ein
fingerprintgebundenes Pfadgate vor dem Providerstart darf daher keinen
operationsgebundenen Request-Slot blockieren.

## 2. Repositorybefund

### 2.1 Markdownprojektion, Fingerprint und Inhaltsguards

- `src/audit_trail.py` kennt sieben verwaltete Auditsektionen und kann deren
  Körper mit `strip_managed_audit_sections()` entfernen. Die aktuelle
  Hilfsfunktion akzeptiert jedoch auch einzelne vorhandene Markerpaare; die
  strengeren Vollständigkeits- und Headingprüfungen liegen in anderen
  Aufrufspfaden. Damit ist sie noch keine kanonische, kontextgebundene Sicht.
- `src/repo_changes.py` verwendet diese Entfernung für den Payloaddigest von
  ausgewählten `docs/internal`-Dateien. Die Auswahl entsteht teils aus
  Dateinamensmustern und teils aus vom Workflow übergebenen Pfaden. Der
  `diff_text` bleibt dagegen der rohe Git-Diff; Reviewpakete können deshalb
  andere Bytes sehen als der Fingerprint.
- `src/review_packets.py` kanonisiert Reihenfolge und Hunkabdeckung eines
  gelieferten Diffs, entfernt aber selbst keine verwalteten Projektionskörper.
  `src/orchestrator.py` kompaktierte beim Finalreview bislang nur den
  konsolidierten Auditpfad als besonderen Einzelfall.
- Die repositoryweiten Sprach- und Vertragsprüfungen in
  `tests/test_language_consistency.py` haben unterschiedliche Inventare. Die
  Runtime-Sprachprüfung liest `src`, `tests` und `run_task`; Rollen- und
  Nutzerdokumentprüfungen lesen feste fachliche Dateien; die Providernamen-
  Ratsche liest ausschließlich produktive Laufzeitdateien aus
  `orchestrator.toml`. Diese Inventare treffen derzeit keine verwalteten
  Auditdokumente. Der Retirement-Guard inventarisiert dagegen aktive
  Repositorydateien einschließlich `docs/internal` und besitzt seit dem
  lokalen Sofortfix als einziger Guard einen direkten Aufruf von
  `strip_managed_audit_sections()`.
- `src/audit_trail.py::_render_validations()` projiziert im älteren
  Auditmodell die kompakte Ausgabe jedes Matrixbefehls. Die recordnative
  Projektion in `src/artifact_projection.py` zeigt bereits Befehl, Status,
  Exitcode und Outputdigest statt eines vollständigen Transkripts. Beide
  Ansichten haben damit noch keine einheitliche Evidenzpolitik.

### 2.2 Validierungsfehler und lokale Diagnose

- `src/validation_matrix.py` behält pro Befehl höchstens 2.000 Zeichen und
  bildet zusätzlich einen Digest über die vollständigen Prozessausgaben.
  `ValidationAttestation` im Workflowverlauf und in Checkpoints enthält den
  kompakten Text; `ValidationAttestationPayload` in der autoritativen
  Recordkette enthält je Ergebnis nur Status, Exitcode und Digest des
  kompakten Texts. Es gibt kein ausdrücklich gebundenes lokales
  Diagnoseartefakt pro Matrixbefehl.
- `src/workflow.py::_attestation()` verwendet eine vorhandene rote
  Attestierung für denselben Fingerprint wieder. Mit
  `--retry-failed-validation` ist pro Prozess ein weiterer Versuch möglich.
  In Finalreviewpfaden wird eine rote Attestierung anschließend als
  `WorkflowExecutionError` behandelt. Der Watcher klassifiziert dies als
  technischen Fehler, startet denselben Zustand in neuen Prozessen erneut und
  kann so erst nach der allgemeinen Retrygrenze Poison erzeugen.
- `src/inbox_watcher.py::write_poison_failure_report()` bewahrt zwar die letzte
  technische Meldung, entfernt bei erfolgreichem Poison-Move aber Watch-
  Identität und Attempt-Sidecar. Das Fehlerdokument ist noch kein validierter
  Recovery-Vertrag.

### 2.3 Resume, Poison und native Requestpersistenz

- `load_or_create_watch_identity()` erzeugt bei fehlendem Sidecar sofort eine
  neue Run-ID mit `started=false`. `watch_inbox()` leitet daraus
  `resume=false` und `force_new=true` ab. Eine außen ausdrücklich gesetzte
  Resume-Absicht wird dabei durch die neue Identität überschrieben.
- Die vorhandenen strukturierten Loader in `src/artifact_store.py`,
  `src/artifact_replay.py`, `src/state_io.py` und die Checkpointlogik können
  Recordkette und State bereits streng validieren. Es fehlt eine Operation,
  die Taskdigest, Run-ID, Protokoll, Chain-Head, Checkpoint, Branch und
  Arbeitsbaum gemeinsam prüft und State-Mirror plus Watch-Identität als eine
  Recoverytransaktion wiederherstellt.
- `src/orchestrator.py::invoke_codex()` schreibt das native Requestbundle unter
  einem aus Work-Unit, Step und Runde gebildeten Pfad, bevor der in
  `agent_runtime.py` ausgeführte `pre_start_callback` den
  Final-Review-Preflight abschließt. Verändert eine anschließende
  Pfadgenehmigung den autoritativen Record-Head und damit den neu gebauten
  Request, kollidiert dieser mit dem vorzeitig belegten Operationspfad.

## 3. Verbindliche Architekturentscheidungen

1. Eine neue gemeinsame Markdownkomponente liefert eine typisierte
   `semantic`-Sicht und eine Projektionssicht. Sie erkennt Dokumentart und
   Markerstruktur aus kanonischer Struktur und explizitem Workflowkontext,
   nicht aus einer pauschalen Verzeichnisausnahme oder einer Liste konkreter
   Dateinamen.
2. Nur ein vollständiger Satz exakt geschriebener, außerhalb von Codefences
   stehender, korrekt geordneter und nicht verschachtelter Marker darf Körper
   verbergen. Unbekannte, doppelte, partielle, umgekehrte, überlappende oder
   am falschen Heading stehende Marker sind ein typisierter Fehler. Enthält
   ein Dokument lediglich markerähnlichen sichtbaren Text, bleibt dieser
   semantisch sichtbar; eine manipulierte Projektion wird niemals gutmütig
   teilbereinigt.
3. Auditprojektion, Repositoryfingerprint, semantischer Reviewdiff und jeder
   Inhaltsguard, dessen Inventar verwaltete Markdowndokumente einschließt,
   verwenden dieselbe Funktion. Guards mit nachweislich disjunktem Inventar
   bleiben unverändert, erhalten aber einen Inventartest, der eine spätere
   Ausweitung auf verwaltete Markdownpfade ohne semantische Sicht rot macht.
4. Menschenlesbare Auditansichten enthalten bei grünen Befehlen nur Befehl,
   Status, Exitcode und Digest. Bei roten oder nicht verfügbaren Befehlen kommt
   ein strikt begrenzter, für Markdown sicher gerenderter Fehlerauszug hinzu.
   Vollständige beziehungsweise kompakte Rohtexte werden nicht repariert oder
   umgeschrieben, sondern als untrusted, digestgebundene lokale
   Diagnoseartefakte gespeichert. Die Recordkette bleibt technische Autorität
   für Status und Bindung.
5. Eine vollständige rote Attestierung ist ein deterministischer lokaler
   Fortsetzungszustand, kein technischer Provider-/Watchfehler. Derselbe
   Fingerprint wird ohne geänderten Repositoryzustand nicht automatisch erneut
   validiert. Der Halt benennt Attestierungsrecord, Befehl, Exitcode,
   Diagnoseartefakt und zulässige Abhilfe.
6. Poison-Finalisierung bewahrt eine versionierte Recoverykapsel neben Task und
   Fehlerbericht. Die Kapsel bindet mindestens Taskdigest, Run-ID,
   `structured-v2`, autoritativen Chain-Head, geeigneten Checkpointdigest,
   Zielbranch, Branch-HEAD und Arbeitsbaumfingerprint. Ein normales
   Zurückkopieren nur der `.poison`-Datei ist keine Recovery.
7. Der Watch- und direkte Resume-Einstieg sucht bei ausdrücklicher
   Resume-Absicht vor Identitätserzeugung und vor jedem Provider-, Projektions-
   oder Branchschritt nach genau passenden alten Laufartefakten. Ein eindeutiger
   Kandidat wird in einer typisierten Recoverydiagnose genannt; null oder
   mehrere Kandidaten bleiben ebenfalls fail-closed.
8. Das Recoverywerkzeug prüft alle Bindungen read-only und veröffentlicht erst
   danach State-Mirror und Watch-Identität. Schlägt irgendeine Prüfung oder
   Veröffentlichung fehl, bleiben beide vorherigen Zielzustände erhalten.
   Append-only Records und Checkpoints werden nie verändert.
9. Native Requestbundles werden content-addressed mit ihrer Request-ID
   gespeichert. Eine kleine operationsbezogene Bindung darf erst nach
   erfolgreichem Provider-Startpreflight auf genau dieses Bundle zeigen.
   Preflightdenials erzeugen weder diese Bindung noch einen belegten
   Operationsslot. Idempotente Recovery akzeptiert nur request-, fingerprint-,
   record-head- und operationsidentische Artefakte.

## 4. Umsetzungsslices

### Slice 1 - Kanonische semantische Markdowngrenze für Fingerprint, Guards und Reviews

**Exakter Änderungspfad**

- `src/semantic_markdown.py`
- `src/audit_trail.py`
- `src/repo_changes.py`
- `src/review_packets.py`
- `src/orchestrator.py`
- `tests/test_semantic_markdown.py`
- `tests/test_audit_trail.py`
- `tests/test_repo_changes.py`
- `tests/test_review_packets.py`
- `tests/test_language_consistency.py`

#### Umsetzung

1. Implementiere in `src/semantic_markdown.py` den alleinigen Markerparser und
   die kanonische Dokumentklassifikation für Slice-Audit, konsolidiertes Audit
   und Workplan-Anhang. Liefere strukturierte Fehler mit Pfad, Markerart und
   Ursache. Codefence- und Blockquotebeispiele bleiben sichtbar; nur exakt
   top-level gesetzte Marker gehören zur Projektion.
2. Stelle Vorbereitung, Validierung, Ersetzung und semantisches Strippen in
   `src/audit_trail.py` auf denselben Parser um. Projektion muss ihre eigene
   Ausgabe erneut akzeptieren, byteidentisch wiederholen und vor jedem Schreiben
   eine bereits vorhandene manipulierte Struktur ablehnen.
3. Erzeuge in `src/repo_changes.py` Payloaddigest und Reviewdiff aus derselben
   semantischen Markdownansicht. Basis- und Arbeitsbaumseite werden jeweils
   kanonisiert; daraus entsteht ein valider, pfadabdeckender Textdiff. Rohe
   verwaltete Körper dürfen weder Fingerprinteintrag noch `diff_text`
   beeinflussen. Binär-, Symlink-, Rename- und Fehlerpfade bleiben fail-closed.
4. Lasse `src/review_packets.py` die semantische Diffabdeckung verifizieren und
   entferne den Finalreview-Dateisonderfall in `src/orchestrator.py`. Paketdigest
   und Repositoryfingerprint müssen dadurch dieselbe Inhaltsgrenze belegen.
5. Stelle den Retirement-Guard auf die neue API um. Erfasse für Sprach-,
   Rollen-, Providerkopplungs- und sonstige Inhaltsinventare explizit, ob sie
   verwaltete Markdownpfade erreichen. Disjunkte Inventare werden durch
   Negativkontrollen abgesichert; jedes künftige gemeinsame Inventar muss die
   semantische Sicht verwenden.

#### Akzeptanzkriterien

- Eine synthetische grüne pytest-Ausgabe mit allen Retirement-, Rollen-,
  Provider-, Altprotokoll- und Markerstichworten wird in Slice-, Workplan- und
  Gesamtaudit projiziert; Fingerprint, Reviewpaketdigest und unmittelbar danach
  ausgeführte Inhaltsguards bleiben unverändert grün.
- Derselbe String außerhalb des verwalteten Körpers wird weiterhin vom
  zuständigen Guard gemeldet.
- Fehlende Endmarker, einzelne Markerpaare, Duplikate, falsche Reihenfolge,
  Verschachtelung, falsches Heading, unbekannte Keys und Marker-Injektionen in
  Evidenz werden eindeutig abgelehnt und verbergen keinen sichtbaren Text.
- Zweimalige Projektion ist byteidentisch. Eine reine Projektionsänderung
  verändert weder semantischen Diff noch Fingerprint; fachlicher Text tut
  beides.

#### Fokussierte Validierung

- `python3 -m pytest tests/test_semantic_markdown.py tests/test_audit_trail.py tests/test_repo_changes.py tests/test_review_packets.py tests/test_language_consistency.py -v`
- `git diff --check`

### Slice 2 - Digestgebundene Validierungsdiagnostik und sichere Auditdarstellung

**Exakter Änderungspfad**

- `src/contracts.py`
- `src/validation_matrix.py`
- `src/artifact_models.py`
- `src/artifact_bridge.py`
- `src/artifact_projection.py`
- `src/audit_trail.py`
- `src/orchestrator.py`
- `schemas/orchestrator-artifact-v2.schema.json`
- `tests/test_validation_matrix.py`
- `tests/test_validation_output_tail.py`
- `tests/test_artifact_models.py`
- `tests/test_artifact_bridge.py`
- `tests/test_artifact_projection.py`
- `tests/test_audit_trail.py`
- `tests/test_orchestrator_runtime.py`

#### Umsetzung

1. Trenne im Matrixrunner die vollständigen Prozessbytes, den begrenzten
   Diagnoseauszug und die für Record/Anzeige nötigen Metadaten. Berechne Digests
   immer über die unveränderten Bytes; NUL, ungültiges UTF-8, sehr lange
   Zeilen und Markertexte dürfen weder Parser noch Projektion steuern.
2. Persistiere je ausgeführtem Befehl ein content-addressed Diagnoseartefakt
   unter dem gebundenen Run-Verzeichnis. Verifiziere nach atomarem Schreiben
   Digest und regulären, nicht symlinkenden Pfad. Das
   `ValidationAttestationPayload` bindet Ergebnis, Exitcode, Rohoutputdigest und
   begrenzten Fehlerauszug; die Record-Schemavalidierung bleibt zwingend.
3. Rendere in beiden Auditpfaden grüne Ergebnisse ohne Transkript. Rote und
   unavailable Ergebnisse zeigen nur den sicher escaped und hart begrenzten
   Auszug sowie den lokalisierbaren Artefaktbezug. In Recordprojektionen werden
   keine Rohbytes als Markdownsteuerzeichen interpretiert.
4. Belege, dass ein Poison-/Checkpoint-Diagnosepfad vom Attestierungsrecord bis
   zum konkreten fehlgeschlagenen Test, Exitcode und Fehlertext lokal
   nachvollziehbar ist, während eine Manipulation des Diagnosecaches am Digest
   scheitert und keine Recordfakten ändert.

#### Akzeptanzkriterien

- Grüne Ausgaben mit parametrisierten Negativtestnamen erscheinen nicht als
  Volltranskript in aktiven Auditdokumenten; die technische Attestierung bleibt
  vollständig an Befehl, Exitcode und Rohoutputdigest gebunden.
- Eine fehlgeschlagene Validierung ist nach Prozessneustart allein aus lokalen
  Records und digestgeprüftem Diagnoseartefakt bis zum Testnamen und Fehlertext
  erklärbar.
- Fehlende, vertauschte, manipulierte oder symlinkende Diagnoseartefakte werden
  gemeldet und nie als gültige Evidenz projiziert.

#### Fokussierte Validierung

- `python3 -m pytest tests/test_validation_matrix.py tests/test_validation_output_tail.py tests/test_artifact_models.py tests/test_artifact_bridge.py tests/test_artifact_projection.py tests/test_audit_trail.py tests/test_orchestrator_runtime.py -v`
- `git diff --check`

### Slice 3 - Deterministischer Rotzustand statt technischer Watch-Retryschleife

**Exakter Änderungspfad**

- `src/workflow_state.py`
- `src/workflow.py`
- `src/orchestrator.py`
- `src/inbox_watcher.py`
- `src/cli.py`
- `README.md`
- `Quickstart.md`
- `tests/test_workflow_state.py`
- `tests/test_workflow.py`
- `tests/test_inbox_watcher.py`
- `tests/test_orchestrator_runtime.py`
- `tests/test_cli.py`

#### Umsetzung

1. Führe einen typisierten, resumierbaren lokalen Validierungsfehlerzustand
   ein. Nach Persistierung einer vollständigen roten Attestierung checkpointet
   der Workflow diesen Zustand statt `WorkflowExecutionError` zu werfen oder
   einen Reviewer zu starten.
2. Binde die Fortsetzungsdiagnose an Fingerprint und Attestierungsrecord. Ein
   Resume mit identischem Fingerprint zeigt dieselbe konkrete Abhilfe und führt
   keine Matrix erneut aus. Erst ein neuer Repositoryfingerprint wählt eine
   neue Matrix; eine ausdrückliche Neuvalidierung desselben Fingerprints muss
   als eigener, auditierter Operatorentscheid modelliert werden und darf nicht
   aus einer Watch-Wiederholung entstehen.
3. Klassifiziere den Zustand im Watcher wie andere lokale Bootstraphalts als
   fortsetzbar und nicht retry-/poisonfähig. Entferne beziehungsweise schärfe
   die bisherige pro-Prozess-Semantik von `--retry-failed-validation`, sodass
   sie die neue Bindung nicht umgehen kann, und dokumentiere den
   Betriebsablauf.

#### Akzeptanzkriterien

- Eine rote Matrix erzeugt genau eine Request-/Attestierungsfolge. Drei
  aufeinanderfolgende Watch- oder direkte Resume-Aufrufe mit unverändertem
  Fingerprint starten weder Test noch Provider, erhöhen keinen technischen
  Attemptzähler und erzeugen kein Poison.
- Nach einer fachlichen Korrektur und neuem Fingerprint läuft genau eine neue
  Matrix. Eine grüne Attestierung des alten Fingerprints kann den neuen nicht
  freigeben.
- Log, State und Audit nennen Befehl, Exitcode, Attestierungsrecord,
  Diagnoseartefakt und erwartete Abhilfe.

#### Fokussierte Validierung

- `python3 -m pytest tests/test_workflow_state.py tests/test_workflow.py tests/test_inbox_watcher.py tests/test_orchestrator_runtime.py tests/test_cli.py -v`
- `git diff --check`

### Slice 4 - Expliziter Poison-Recovery-Vertrag vor jeder fachlichen Ausführung

**Exakter Änderungspfad**

- `src/inbox_watcher.py`
- `src/cli.py`
- `src/orchestrator.py`
- `src/artifact_store.py`
- `src/artifact_replay.py`
- `src/state_io.py`
- `src/git_service.py`
- `src/workflow_state.py`
- `README.md`
- `Quickstart.md`
- `AGENTS.md`
- `CLAUDE.md`
- `CODEX.md`
- `tests/test_inbox_watcher.py`
- `tests/test_orchestrator_watch_cli.py`
- `tests/test_cli.py`
- `tests/test_orchestrator_runtime.py`
- `tests/test_artifact_store.py`
- `tests/test_artifact_replay.py`
- `tests/test_state_io.py`
- `tests/test_git_service.py`
- `tests/test_language_consistency.py`

#### Umsetzung

1. Erweitere Poison-Finalisierung um eine versionierte Recoverykapsel, die
   Watch-Identität und die vor dem Move read-only erhobenen Bindungen erhält.
   Task, Fehlerreport und Kapsel werden als zusammengehörige Outboxgruppe
   veröffentlicht; Bereinigungs- oder Movefehler behalten genug Daten für einen
   idempotenten Bookkeeping-Retry.
2. Führe einen expliziten CLI-Recoverypfad ein. Er nimmt Poison-Task und
   Kapsel, validiert Taskdigest, `structured-v2`, vollständige Recordkette samt
   rekonstruiertem Head, Run-ID, Checkpoint und dessen Digest, Zielbranch,
   Branch-HEAD, kanonischen Arbeitsbaumfingerprint sowie zulässige Inbox- und
   Stateziele. Erst nach vollständigem Erfolg werden Task, Watch-Identität und
   State-Mirror atomar beziehungsweise über ein crash-sicheres
   Transaktionsjournal veröffentlicht.
3. Baue vor `load_or_create_watch_identity()`, vor Branchvorbereitung,
   Stateinitialisierung, Auditprojektion und Providerinput eine
   Resume-Absichtsbarriere ein. Bei fehlendem Sidecar sucht sie anhand des
   Taskdigests ausschließlich vorhandene strukturierte Runs und
   Recoverykapseln. Sie meldet den eindeutigen alten Lauf oder die Ambiguität
   und stoppt; sie ruft niemals den bisherigen Identitätserzeuger auf.
4. Die Recovery liest append-only Records und Checkpoints nur. Sie darf einen
   aus Records und validiertem Checkpoint belegten State-Mirror
   wiederveröffentlichen, aber keine Recorddatei, keinen Checkpoint und keine
   fachliche Auditdatei ändern. Bei jeder Abweichung werden temporäre Ziele
   verworfen und beide sichtbaren Zielzustände byteidentisch belassen.
5. Synchronisiere den neuen Resume-/Recoveryvertrag wortgleich in
   `AGENTS.md`, `CLAUDE.md` und `CODEX.md`; dokumentiere Bedienung, Diagnose und
   Grenzen in README und Quickstart.

#### Akzeptanzkriterien

- Eine Poison-Aufgabe wird ohne Watch-Sidecar in die Inbox kopiert und mit
  ausdrücklichem `--resume` gestartet. Der Lauf hält vor Codex, nennt die alte
  Run-ID und deren Chain-Head und erstellt weder neues Run-Verzeichnis noch
  Record, State, Auditprojektion oder Arbeitsbaumänderung.
- Das Recoverywerkzeug stellt bei exakt passenden Bindungen State-Mirror,
  Task und Watch-Identität wieder her; der anschließende Resume verwendet den
  alten Run und Checkpoint. Wiederholung ist idempotent.
- Falscher Taskdigest, fremde Run-ID, fehlender oder divergenter Record,
  falscher Headcache, ungeeigneter Checkpoint, Branch-/HEAD-/Worktree-Drift,
  Symlink, Pfadescape, mehrere Kandidaten und simulierte Unterbrechungen vor
  jeder Veröffentlichungsgrenze lassen State und Identität unverändert.
- Ein bereits versehentlich vorhandener unabhängiger Ersatzlauf wird gemeldet,
  aber weder gelöscht noch mit dem alten Lauf verschmolzen.

#### Fokussierte Validierung

- `python3 -m pytest tests/test_inbox_watcher.py tests/test_orchestrator_watch_cli.py tests/test_cli.py tests/test_orchestrator_runtime.py tests/test_artifact_store.py tests/test_artifact_replay.py tests/test_state_io.py tests/test_git_service.py tests/test_language_consistency.py -v`
- `git diff --check`

### Slice 5 - Preflightgebundene und kollisionsfreie native Final-Requests

**Exakter Änderungspfad**

- `src/orchestrator.py`
- `src/agent_runtime.py`
- `src/native_codex_request.py`
- `src/artifact_models.py`
- `src/artifact_bridge.py`
- `src/artifact_replay.py`
- `src/final_review_preflight.py`
- `schemas/orchestrator-artifact-v2.schema.json`
- `tests/test_orchestrator_runtime.py`
- `tests/test_agent_runtime.py`
- `tests/test_native_codex_request.py`
- `tests/test_artifact_models.py`
- `tests/test_artifact_bridge.py`
- `tests/test_artifact_replay.py`
- `tests/test_final_review_preflight.py`

#### Umsetzung

1. Teile die bisherige Requestpersistenz in content-addressed Bundleablage und
   operationsbezogene Autorisierungsbindung. Der Bundlepfad enthält die
   validierte Request-ID; vorhandene Bytes werden gegen Requestdigest,
   Fingerprint und Evidence-Manifest geprüft.
2. Ordne den Providerstart so, dass Eingabemessung und
   Final-Review-Preflight zuerst als autoritative Records persistiert und
   gespiegelt werden. Nur ein bestandener Preflight darf danach Bundle,
   Operationsbindung und `ProviderAttempt(started)` in dieser Reihenfolge
   veröffentlichen. Jeder Absturzpunkt ist anhand der Records eindeutig
   fortsetzbar.
3. Ein `UNAUTHORIZED-PATH`-Halt vor Providerstart persistiert das Gate und die
   konkrete Diagnose, aber keinen endgültigen Operationsrequest und keinen
   Providerattempt. Nach Genehmigung exakt desselben Fingerprints und Pfads wird
   der Request aus dem neuen autoritativen Head gebaut und genau einmal
   gestartet. Ein veraltetes ungebundenes Cachebundle ist unschädlich und darf
   weder als Autorität dienen noch eine Kollision auslösen.
4. Härte Recovery gegen Mischungen aus altem Request, neuer Gateentscheidung,
   fremdem Record-Head und bereits terminalem Attempt. Nur die vollständig
   identische Operationsbindung wird idempotent wiederverwendet.

#### Akzeptanzkriterien

- Ein vorbereiteter Codex-Final-Request wird durch den Preflight mit
  `UNAUTHORIZED-PATH` vor Providerstart abgelehnt. Nach exakter
  fingerprint-/pfadgebundener Genehmigung startet ein Resume genau einen
  Providerattempt mit dem danach autorisierten Request; es entsteht weder
  Requestkollision noch Poison.
- Parametrisierte Unterbrechungen nach Messung, nach bestandenem Preflight,
  nach Bundleablage, nach Operationsbindung und nach Attemptstart konvergieren
  ohne zweiten Providerstart oder widersprüchlichen Record.
- Manipulierte Requestbytes, fremder Fingerprint, fremder Record-Head,
  ungebundener Altrequest und eine Gateentscheidung für einen anderen Pfad
  scheitern vor Providerstart.

#### Fokussierte Validierung

- `python3 -m pytest tests/test_orchestrator_runtime.py tests/test_agent_runtime.py tests/test_native_codex_request.py tests/test_artifact_models.py tests/test_artifact_bridge.py tests/test_artifact_replay.py tests/test_final_review_preflight.py -v`
- `git diff --check`

## 5. Reihenfolge und Abhängigkeiten

Die Slices werden strikt in der Reihenfolge 1 bis 5 umgesetzt.

1. Slice 1 definiert die gemeinsame semantische Grenze, auf der alle weiteren
   Projektions- und Fingerprintnachweise beruhen.
2. Slice 2 macht rote Validierung lokal konkret diagnostizierbar und reduziert
   untrusted Ausgaben, bevor Slice 3 daraus einen Fortsetzungszustand macht.
3. Slice 3 verhindert die beobachtete identische Retry-/Poisonkette für neue
   rote Attestierungen.
4. Slice 4 sichert historische beziehungsweise bereits poisonierte Läufe und
   schließt den Neulaufpfad vor jeder Mutation.
5. Slice 5 korrigiert zuletzt die Request-Lebensdauer am Finalpreflight; seine
   Recoverytests verwenden die in Slice 4 gehärteten Resumegrenzen.

Eine parallele Umsetzung ist nicht zulässig, weil die Slices gemeinsame
Persistenz- und Recoveryinvarianten schrittweise erweitern.

## 6. Anforderungsabdeckung

| Anforderung | Umsetzung und Nachweis |
|---:|---|
| 1 | Slice 1 inventarisiert alle aktuellen Sprach-, Rollen-, Retirement-, Inhalts- und Kopplungsguards samt Pfadmenge. |
| 2 | Slice 1 führt genau eine kanonische semantische Markdownkomponente ein. |
| 3–4 | Slice 1 verbirgt nur vollständige kanonische Projektionskörper und lehnt jede Markerabweichung ab. |
| 5 | Slices 1 und 2 behandeln beliebige Validierungsausgabe als untrusted Daten und testen Angriffsstrings. |
| 6 | Slice 1 verbindet Projektion, Fingerprint, Reviewdiff und betroffene Guards mit derselben Sicht. |
| 7 | Slice 2 vereinheitlicht kompakte Auditmetadaten und begrenzte Fehlerauszüge ohne Schwächung der Records. |
| 8 | Slice 2 persistiert digestgebundene lokale Befehlsdiagnosen. |
| 9 | Slice 3 ersetzt identische technische Retries durch einen typisierten roten Fortsetzungszustand. |
| 10–12 | Slice 4 bewahrt beziehungsweise rekonstruiert Poisonbindungen streng und blockiert Neulauf, Projektion und Mutation vor Recovery. |
| 13 | Slice 5 verschiebt die endgültige Requestbindung hinter den Finalpreflight und macht Bundles content-addressed. |

## 7. Migration, Kompatibilität und Rollback

- Bestehende korrekt markierte Auditdokumente werden beim ersten Lesen gegen
  die neue kanonische Grammatik geprüft. Es gibt keine automatische Reparatur
  partieller Marker. Eine Ablehnung nennt Datei und Strukturfehler.
- Bestehende structured-v2-Records bleiben unverändert. Neue optionale
  Diagnosebindungen beziehungsweise Operationsbindungen werden als neue
  validierte Payloadrevisionen eingeführt; Replay akzeptiert nur die explizit
  unterstützten Formen und migriert keine historischen Protokolle still.
- Historische Validierungsrecords ohne Diagnoseartefakt bleiben als Status- und
  Digestfakt lesbar, werden aber ausdrücklich als `diagnostic unavailable`
  dargestellt. Es wird kein Fehlertext erfunden.
- Historische Poison-Aufgaben ohne Recoverykapsel werden nicht automatisch
  reaktiviert. Die Resume-Absichtsbarriere kann einen eindeutigen alten Lauf
  read-only diagnostizieren; eine Wiederherstellung benötigt einen vollständig
  validierbaren Checkpoint und alle übrigen Bindungen.
- Rollback bedeutet, den jeweiligen Produkt-Slice vor Commit zu verwerfen oder
  nach Commit durch einen normalen Gegencommit zurückzunehmen. Records,
  Checkpoints, State-v3-Mirrors und Recoverykapseln werden niemals manuell
  editiert, gelöscht oder umgeschrieben.

## 8. Gesamtvalidierung und Übergabe

Jeder implementierende Agent führt nur die im Slice genannten fokussierten,
providerfreien Tests und `git diff --check` aus. Nach jedem Slice prüft Claude
den Fingerprint und die angegebenen Fehler-, Sicherheits-, Resume- und
Idempotenzpfade mit Sonnet und Effort `high`.

Da alle Slices Orchestrierungs-, Watch-, Persistenz-, Projektions- oder
Statepfade berühren, führt ausschließlich der Orchestrator nach dem jeweiligen
Implementierungsreview die konfigurierte vollständige Matrix
`python3 -m pytest tests/ -v` aus und erstellt die fingerprintgebundene
Attestierung. Codex und Claude beanspruchen diese Attestierung nicht selbst.

Der branchweite Abschluss prüft zusätzlich:

- `git diff --check`;
- byteidentische Wiederholungsprojektion und unveränderten semantischen
  Fingerprint;
- eine vollständige grüne Matrix unmittelbar nach Projektion synthetischer
  Negativtestnamen;
- providerfreie Crashpoint-Tabellen für Poison-Recovery und
  Final-Request-Persistenz;
- keine offenen Claude-Findings und keine Änderung außerhalb der im jeweiligen
  Slice genehmigten Pfade.

## 9. Risiken, Pre-Mortem und Stopbedingungen

Das größte Risiko ist eine zu großzügige Dokumentklassifikation, durch die
sichtbarer fachlicher Text als Projektion behandelt wird. Realistische
Bruchbedingung ist ein Dokument mit fast korrekten Markern, das Fingerprint und
Guard passieren würde. Deshalb verlangen Parser und Negativkontrollen den
vollständigen kanonischen Aufbau und vergleichen dieselben Bytes über Guard,
Fingerprint und Reviewpaket.

Das zweite Risiko ist eine scheinbar atomare Recovery, bei der State und
Watch-Identität nach einem Prozessabbruch auf unterschiedliche Läufe zeigen.
Die Bruchbedingung ist jede injizierte Unterbrechung zwischen den
Veröffentlichungsschritten. Das Transaktionsjournal und die Wiederanlauftests
müssen vor fachlicher Ausführung entweder den alten oder den vollständig neuen
Zustand beweisen; Mischzustände werden nicht fortgesetzt.

Das dritte Risiko ist ein Request, der zwar content-addressed ist, aber unter
einem veralteten Record-Head gestartet wird. Die Bruchbedingung ist eine
Gateentscheidung zwischen Requestbau und Attemptstart. Deshalb bindet die
Operationsfreigabe Request-ID, Fingerprint, relevanten Record-Head und
Preflightrecord gemeinsam und wird unmittelbar vor `ProviderAttempt(started)`
erneut geprüft.

Die Umsetzung hält an und fordert eine neue Entscheidung an, wenn

- sichtbarer fachlicher Dokumentinhalt zur Erfüllung eines Guards ausgeblendet
  werden müsste;
- vorhandene append-only Records oder Checkpoints verändert, gelöscht oder
  erfunden werden müssten;
- Fingerprint, Reviewdiff und Inhaltsguard nicht nachweislich dieselbe
  kanonische Markdownfunktion verwenden können;
- Recovery ohne eindeutige Run-, Task-, Chain-, Checkpoint-, Branch- und
  Worktreebindung fortgesetzt werden müsste;
- eine rote Attestierung freigegeben oder Provider-/Testausgabe inhaltlich
  repariert werden müsste.

## 10. Offene Fragen

Keine offene Produktentscheidung. Die sichere Voreinstellung ist in allen
zweifelhaften Fällen fail-closed: sichtbarer Text bleibt geprüft, rote
Attestierungen bleiben rot, und eine unvollständige Recovery startet nichts.

## Orchestrator-Prüfprotokoll

### Review-Feedback von Claude

<!-- audit:claude-review:begin -->
### Claude · Runde 1 · approved (Ereignis 3)

- Reviewer: `claude`
- Freigabe: `YES`
- Validierungsbindung: `plan-validation-219134f218b1`
- Testdateien: keine
- Prüfdimensionen: Checked: diff touches exactly the one authorized path with no other file, staging, or branch transaction; PLAN_ONLY marker contract (contiguous '### Slice N<br>- title' headings and standalone '**Exakter Änderungspfad**' bullet lists) across Slices 1-5; coverage of all 13 assignment requirements via Section 6's requirement-to-slice table and the guard inventory in Section 2.1; fingerprint match between current_fingerprint and review_contract.validation_attestation.diff_fingerprint; resume/idempotency/recovery design against the two concrete incidents described (audit self-poisoning, silent new-run-on-resume) via Slices 3-5 and Sections 3, 7, 9; stop-condition and rollback completeness versus the assignment's own Stopbedingungen.
- Größtes Restrisiko: The plan's own generated audit-protocol section already exhibits the stale-fingerprint/reused-output-digest symptom (028a4a6e346a vs current 219134f218b1) the plan is designed to eliminate; until Slice 1 lands, any interim guard or fingerprint path that still treats this file's audit body as authored content could be misled by that embedded historical text.<br>Tracked as C-01 for Slice 1 test coverage.
- Realistische Bruchbedingung: If Slice 1's canonical semantic-markdown boundary does not provably keep this document's own audit-protocol section out of fingerprint/guard input, verified by a repeatable byte-identical re-projection test against this exact evidence, or if any guard, fingerprint, or review-diff caller outside the five listed Slices still parses docs/internal audit bodies as authored content, the self-poisoning defect this plan targets remains open.
- Eigene Findings: `C-01`

<!-- artifact-records:claude-review:begin -->
Semantischer Record-Digest: `033e63da4073`

### Claude · Runde – · approved

| Seq/Record | Rolle | Runde | Status | Work-Unit | Findings | Fingerprint | Transport | Request | Response |
|---|---|---:|---|---|---|---|---|---|---|
| 10. `ar1-9c9f2a026467` | `claude` | `–` | `approved` | `1` | `C-01` | `219134f218b1` | `native-claude-review-v2` | `native-review-request-e28694de94a0` | `ef45e997724b` |
<!-- artifact-records:claude-review:end -->
<!-- audit:claude-review:end -->

### Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
Noch keine strukturierten Codex-Antworten.

<!-- artifact-records:codex-responses:begin -->
Semantischer Record-Digest: `033e63da4073`

Keine Codex-Findingantworten.
<!-- artifact-records:codex-responses:end -->
<!-- audit:codex-responses:end -->

### Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### Ereignis 1: `plan-validation-028a4a6e346a`

- Diff-Fingerprint: `028a4a6e346a`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: internal plan contract passed
- Ausgabedigest: `3b1bb7f924c1`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| internal:work-plan-contract | PASS | 0 | slices=1; planned_paths=1; changed_paths=1; future_slices=5; work_plan=docs/internal/01-validierungsevidenz-darf-aktive-pruefungen-nicht-selbst-vergiften-arbeitsplan.md |

### Ereignis 2: `plan-validation-219134f218b1`

- Diff-Fingerprint: `219134f218b1`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: internal plan contract passed
- Ausgabedigest: `3b1bb7f924c1`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| internal:work-plan-contract | PASS | 0 | slices=1; planned_paths=1; changed_paths=1; future_slices=5; work_plan=docs/internal/01-validierungsevidenz-darf-aktive-pruefungen-nicht-selbst-vergiften-arbeitsplan.md |

<!-- artifact-records:validation-attestation:begin -->
Semantischer Record-Digest: `033e63da4073`

- 2. `ar1-3606a6990134`: Providerinput `codex/codex_plan` = `allowed`; local_input_chars `23573/4000000`, local_input_bytes `23663/16000000`; local_input_digest `ad4d8a663b46`, Policy `9edf600f09ac`, Übergang `000d159de16b`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, local_input_largest_component `stdin_prompt`; local_input_component_count `2`; Komponenten `stdin_prompt=19784/19874, response_schema=3789/3789`
### Validierungsattestierung

| Seq/Record | Rolle | Fingerprint |
|---|---|---|
| 6. `ar1-d0fc50cbf6d8` | `orchestrator` | `028a4a6e346a` |

| Status | Exit | Output-Digest | Befehl mit argv-Grenzen |
|---|---:|---|---|
| `pass` | `0` | `3b1bb7f924c1` | `argv` [`internal:work-plan-contract`] |
### Validierungsattestierung

| Seq/Record | Rolle | Fingerprint |
|---|---|---|
| 7. `ar1-f2d8c2b965d2` | `orchestrator` | `219134f218b1` |

| Status | Exit | Output-Digest | Befehl mit argv-Grenzen |
|---|---:|---|---|
| `pass` | `0` | `3b1bb7f924c1` | `argv` [`internal:work-plan-contract`] |
- 8. `ar1-74f3cf5dd816`: Providerinput `claude/claude_plan_review` = `allowed`; local_input_chars `72126/4000000`, local_input_bytes `72488/16000000`; local_input_digest `eda10406e6a3`, Policy `9edf600f09ac`, Übergang `5204c201b845`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, local_input_largest_component `evidence_asset_001`; local_input_component_count `6`; Komponenten `request_chunk_001=21094/21184, evidence_asset_001=39479/39751, packet_manifest=610/610, system_policy=430/430, response_schema=10283/10283, start_directive=230/230`
- Providerattempt-Summe Run `watch-20260829-093135.076835Z-f71c891be049` / Operation `provider-operation-9109aef8daf6` (`codex/codex_plan`; Modell `gpt-5.6-sol`; Effort `medium`): Attempts `1`, offen `0`, Duration `343.483899` (bekannt `1`, unbekannt `0`); Inputzeichen `23573`, Inputbytes `23663`; Retrystatus `single-attempt`; input_tokens=sum:unknown,known:0,unknown:1; tool_input_tokens=sum:unknown,known:0,unknown:1; cache_read_input_tokens=sum:unknown,known:0,unknown:1; cache_creation_input_tokens=sum:unknown,known:0,unknown:1; thinking_tokens=sum:unknown,known:0,unknown:1; output_tokens=sum:unknown,known:0,unknown:1; total_tokens=sum:unknown,known:0,unknown:1; turns=sum:unknown,known:0,unknown:1; cost_usd=sum:unknown,known:0,unknown:1
  - 5. `ar1-c12959ceeafe`: Attempt `1` = `succeeded`; Messung `ar1-3606a6990134`; Modell `gpt-5.6-sol`; Effort `medium`; Inputzeichen `23573`; Inputbytes `23663`; Duration `343.4838990069984`; Fehler `none`; Usage `unknown`
- Providerattempt-Summe Run `watch-20260829-093135.076835Z-f71c891be049` / Operation `provider-operation-e8b27aa0c896` (`claude/claude_plan_review`; Modell `sonnet`; Effort `high`): Attempts `1`, offen `0`, Duration `235.026311` (bekannt `1`, unbekannt `0`); Inputzeichen `72126`, Inputbytes `72488`; Retrystatus `single-attempt`; input_tokens=sum:8,known:1,unknown:0; tool_input_tokens=sum:unknown,known:0,unknown:1; cache_read_input_tokens=sum:53733,known:1,unknown:0; cache_creation_input_tokens=sum:57543,known:1,unknown:0; thinking_tokens=sum:unknown,known:0,unknown:1; output_tokens=sum:22032,known:1,unknown:0; total_tokens=sum:unknown,known:0,unknown:1; turns=sum:6,known:1,unknown:0; cost_usd=sum:0.4622596,known:1,unknown:0
  - 12. `ar1-b99db3fdbfe7`: Attempt `1` = `succeeded`; Messung `ar1-74f3cf5dd816`; Modell `sonnet`; Effort `high`; Inputzeichen `72126`; Inputbytes `72488`; Duration `235.026310989997`; Fehler `none`; Usage `input_tokens=8, tool_input_tokens=unknown, cache_read_input_tokens=53733, cache_creation_input_tokens=57543, thinking_tokens=unknown, output_tokens=22032, total_tokens=unknown, turns=6, cost_usd=0.4622596`
<!-- artifact-records:validation-attestation:end -->
<!-- audit:validation-attestation:end -->

### Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
- Teständerungsfreigabe: nicht erfasst.
- Pre-Mortems:
  - Ereignis 3: Biggest failure mode: approving a plan that reads as complete but leaves some guard, the fingerprint computation, or the review-diff pipeline outside the new semantic_markdown boundary, so a managed audit body could still poison a later run exactly as it did before.<br>Checked requirement-to-slice coverage (Section 6), the explicit per-guard inventory in Section 2.1, and Slice 1's mandate to add inventory regression tests for any guard with disjoint scope; every guard touching docs/internal is named and routed through one canonical component.<br>Second failure mode: a resume/recovery design that still lets Poison finalization or --resume silently start a new run; Slices 3 and 4 add an explicit resume-intent barrier before identity creation and a read-only, atomically-published recovery capsule bound to task digest, run ID, chain head, checkpoint, branch, and worktree, directly matching the two incidents in the assignment.<br>Third failure mode: approving despite a fingerprint/attestation mismatch; current_fingerprint equals review_contract.validation_attestation.diff_fingerprint exactly, so the bound attestation is authoritative for this exact reviewed content.<br>No BLOCKER-level gap was found in the plan text, scope, or format; the one residual concern is tracked as open OBSERVATION C-01 for Slice 1's test design, and no destructive action, out-of-scope path, or premature Git transaction is present in the diff.

<!-- artifact-records:test-approval-premortem:begin -->
Semantischer Record-Digest: `033e63da4073`

Keine strukturierten Gates.
<!-- artifact-records:test-approval-premortem:end -->
<!-- audit:test-approval-premortem:end -->

### Findings-Lebenszyklus

<!-- audit:findings:begin -->
### `C-01` — `OPEN`

- Quelle: `claude`; Runde 1
- Klasse: `OBSERVATION`
- Finding: The reviewed plan file's own embedded 'Orchestrator-Prüfprotokoll'/'Validierungsattestierung' section shows a validation-attestation event bound to diff-fingerprint 028a4a6e346a..., while this review's current_fingerprint and review_contract.validation_attestation.diff_fingerprint are 219134f218b1..., yet both cite the identical output digest 3b1bb7f924c1....<br>That is a live instance of exactly the stale/cross-fingerprint audit-projection drift this plan exists to close.<br>It does not block approval here, since the review contract's own attestation does match current_fingerprint, but Slice 1's test design should explicitly cover this reproducible scenario using real evidence rather than only synthetic examples.
- Akzeptanztest: Slice 1's suite (tests/test_semantic_markdown.py, tests/test_audit_trail.py) includes a regression case seeded from this document's own current bytes: it re-renders the managed Orchestrator-Prüfprotokoll section after a validation attestation and asserts the resulting semantic fingerprint, review-diff, and every guard reading docs/internal stay unchanged, and that no stale attestation fingerprint or reused output digest from a prior render is treated as authoritative content.
- Statusbegründung: –

<!-- artifact-records:findings:begin -->
Semantischer Record-Digest: `033e63da4073`

### Finding-Ereignisse

| Seq/Record | Finding | Rolle | Runde | Aktion | Klasse | Status | Begründung |
|---|---|---|---:|---|---|---|---|
| 11. `ar1-d69e113a8cd7` | `C-01` | `claude` | `–` | `opened` | `OBSERVATION` | `open` | The reviewed plan file's own embedded 'Orchestrator-Prüfprotokoll'/'Validierungsattestierung' section shows a validation-attestation event bound to diff-fingerprint 028a4a6e346a..., while this review's current_fingerprint and review_contract.validation_attestation.diff_fingerprint are 219134f218b1..., yet both cite the identical output digest 3b1bb7f924c1....<br>That is a live instance of exactly the stale/cross-fingerprint audit-projection drift this plan exists to close.<br>It does not block approval here, since the review contract's own attestation does match current_fingerprint, but Slice 1's test design should explicitly cover this reproducible scenario using real evidence rather than only synthetic examples. |

### Native convergence summary

| Finding | Work units | Rounds | Fingerprints | Claude decisions | Codex dispositions | Final status |
|---|---|---|---|---|---|---|
| `C-01` | `1` | `1` | `219134f218b1` | `opened:open` | – | `open` |
<!-- artifact-records:findings:end -->
<!-- audit:findings:end -->

### Entscheidungstabelle

<!-- audit:decision-table:begin -->
| ID | Quelle | Finding | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|---|
| C-01 | claude | The reviewed plan file's own embedded 'Orchestrator-Prüfprotokoll'/'Validierungsattestierung' section shows a validation-attestation event bound to diff-fingerprint 028a4a6e346a..., while this review's current_fingerprint and review_contract.validation_attestation.diff_fingerprint are 219134f218b1..., yet both cite the identical output digest 3b1bb7f924c1....<br>That is a live instance of exactly the stale/cross-fingerprint audit-projection drift this plan exists to close.<br>It does not block approval here, since the review contract's own attestation does match current_fingerprint, but Slice 1's test design should explicitly cover this reproducible scenario using real evidence rather than only synthetic examples. | OBSERVATION | offen | offen |

<!-- artifact-records:decision-table:begin -->
Semantischer Record-Digest: `033e63da4073`

| Seq | Record | Typ | Status | Logische ID | Revision | Fingerprint |
|---:|---|---|---|---|---:|---|
| 1 | `ar1-f02b81d0f62f` | `task` | `accepted` | `task-contract` | 1 | `contract:d2fe68d2b3de` |
| 2 | `ar1-3606a6990134` | `provider_input_measurement` | `measured` | `provider-input-1-codex_plan` | 1 | `implementation:d2fe68d2b3de` |
| 3 | `ar1-eedf973aadfd` | `provider_attempt` | `started` | `provider-operation-9109aef8daf6-1` | 1 | `implementation:d2fe68d2b3de` |
| 4 | `ar1-de4fd94465d8` | `agent_result` | `ready` | `agent-1-codex_plan-1` | 1 | `contract:d2fe68d2b3de` |
| 5 | `ar1-c12959ceeafe` | `provider_attempt` | `succeeded` | `provider-operation-9109aef8daf6-1` | 2 | `implementation:d2fe68d2b3de` |
| 6 | `ar1-d0fc50cbf6d8` | `validation_attestation` | `attested` | `plan-validation-028a4a6e346a` | 1 | `implementation:028a4a6e346a` |
| 7 | `ar1-f2d8c2b965d2` | `validation_attestation` | `attested` | `plan-validation-219134f218b1` | 1 | `implementation:219134f218b1` |
| 8 | `ar1-74f3cf5dd816` | `provider_input_measurement` | `measured` | `provider-input-1-claude_plan_review` | 1 | `implementation:d2fe68d2b3de` |
| 9 | `ar1-acff8ad357eb` | `provider_attempt` | `started` | `provider-operation-e8b27aa0c896-1` | 1 | `implementation:d2fe68d2b3de` |
| 10 | `ar1-9c9f2a026467` | `review` | `decided` | `review-claude-1-1` | 1 | `implementation:219134f218b1` |
| 11 | `ar1-d69e113a8cd7` | `finding_transition` | `recorded` | `finding-C-01` | 1 | `implementation:219134f218b1` |
| 12 | `ar1-b99db3fdbfe7` | `provider_attempt` | `succeeded` | `provider-operation-e8b27aa0c896-1` | 2 | `implementation:d2fe68d2b3de` |

### Nachweis vollständiger Bindungswerte

| Kurzreferenz | Vollwert | Feldarten |
|---|---|---|
| `219134f218b1` | `219134f218b1a47745746e947cbc2504f00ede5e85cc67c226c7e54384c82164` | Technischer Wert, Fingerprint, Attestierungsreferenz, Record-ID |
| `028a4a6e346a` | `028a4a6e346a0594f88c46632593bfaba17a58441a10cd2b3f2ccc474c92b6db` | Output-Digest, Attestierungsreferenz, Fingerprint, Technischer Wert |
| `033e63da4073` | `033e63da407381ac8841cdb2ed1f590f2dd0b5108bd8f9735f46089f1f115623` | Record-ID |
| `9c9f2a026467` | `9c9f2a026467d7c916d635cb93a6d377d22328fc0b9bb1e17a24033ee2ef99d7` | Request-ID, Technischer Wert |
| `e28694de94a0` | `e28694de94a0f882c114d18c6064b7a7fb69ea14efe53593f05d160fa9a16d39` | Request-ID |
| `ef45e997724b` | `ef45e997724b90297a07240f0450c22cb745d95b7b864fba5306350ad342cff6` | Request-ID |
| `3b1bb7f924c1` | `3b1bb7f924c197ad110ac35ecae6c116544eea553867b6615b6d1c6efe61b87d` | Output-Digest |
| `3606a6990134` | `3606a6990134c8baf9160ac3d66617eda813b01d58fb4d906520ff63acc9c168` | Technischer Wert, Messungsreferenz |
| `ad4d8a663b46` | `ad4d8a663b46775b876a2cef70b7a174e5e01259e543679571fb6406eaee5cee` | Digest |
| `9edf600f09ac` | `9edf600f09ac1b36a30675cc91b1f6769c4545f8f200a4fba413b4b9a4c9bc1e` | Policy-Digest |
| `000d159de16b` | `000d159de16b936f03db73b09c71533f2cc28ee06e19856db146ddc8b5eefea4` | Übergangsfingerprint |
| `d0fc50cbf6d8` | `d0fc50cbf6d81eb29cb91cce7aae770d2e58e1d7e0f88b89009e9cc0ffba5071` | Record-ID, Technischer Wert |
| `f2d8c2b965d2` | `f2d8c2b965d2a559ae02ad011e84ef2c0f47950128b5b7ebc7eedb297f4dc6cd` | Record-ID, Technischer Wert |
| `74f3cf5dd816` | `74f3cf5dd816cdda6e5d40428efdf088f67dc54261ac04275a28fce8acd20a04` | Technischer Wert, Messungsreferenz |
| `eda10406e6a3` | `eda10406e6a3be5cc2b43913cebcd690e79fb2c7e5f6a81bc334bdf323aa4bd5` | Digest |
| `5204c201b845` | `5204c201b84586fb01144ab04199310453ea0534c76069e2c8a7d5a1484ac731` | Übergangsfingerprint |
| `9109aef8daf6` | `9109aef8daf66956c5f5e81005e370d45eebc1983556d7217c677fe94049008a` | Technischer Wert |
| `c12959ceeafe` | `c12959ceeafe1bd1cd7ef8182c5dc8df56276c61f4378f29c347b933c62054bf` | Technischer Wert |
| `e8b27aa0c896` | `e8b27aa0c8962cc3a0c12e6471dc8d68533663e66b7a206cbf37d4b885a55170` | Technischer Wert |
| `b99db3fdbfe7` | `b99db3fdbfe7ca68ed202c09f07d5b78345f68e166ed385defeb154488639157` | Technischer Wert |
| `d69e113a8cd7` | `d69e113a8cd74811513b9ed25c587f2de3f5c8740b76f2bfdae16baed0501086` | Technischer Wert |
| `f02b81d0f62f` | `f02b81d0f62f7707d3a17c7b2e58c0feb7e8bd8979425cdeb96a470b82d36737` | Fingerprint |
| `d2fe68d2b3de` | `d2fe68d2b3de3779bde216597fec0f7d6edb2e3978282ef1ce51fb7d882515ba` | Technischer Wert |
| `eedf973aadfd` | `eedf973aadfdeabd9e252327f9ea80c08391b5e335ef4e19a893ca3503870dde` | Technischer Wert |
| `de4fd94465d8` | `de4fd94465d8579b6d4e13f6fd3f0fed5f036214831db690752630194d703d30` | Technischer Wert, Response-Digest |
| `acff8ad357eb` | `acff8ad357ebc66e45877a71ad424464840b368698a4808c0bd8e61eef6b25a4` | Technischer Wert |
| `cc464def4d7a` | `cc464def4d7a72a515967173ab6a6d70066f844e3de3eb9ba7669f48ca2d6334` | Request-ID |
| `c519316bb749` | `c519316bb749534ce16ccd833360e63d1ae0cd5637ca288b9c64215163ba990d` | Request-ID |
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
Semantischer Record-Digest: `033e63da4073`

### Codex · Runde – · ready

| Seq/Record | Rolle | Runde | Status | Work-Unit | Tests | Transport | Request | Response | Fingerprint |
|---|---|---:|---|---|---|---|---|---|---|
| 4. `ar1-de4fd94465d8` | `codex` | `–` | `ready` | `1` | keine | `native-codex-v2` | `native-codex-request-cc464def4d7a` | `c519316bb749` | `d2fe68d2b3de` |
<!-- artifact-records:approval-status:end -->
<!-- audit:approval-status:end -->
