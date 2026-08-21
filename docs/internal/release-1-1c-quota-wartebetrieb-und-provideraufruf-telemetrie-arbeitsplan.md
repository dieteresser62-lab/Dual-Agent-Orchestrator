# Release 1.1C – Quota-Wartebetrieb und Provideraufruf-Telemetrie

## Planstatus und Ausführungsgrenze

- Zielbranch: `feature/orchestrator-stabilization-1-1c`.
- Dieser Arbeitsplan ist das einzige Artefakt des `PLAN_ONLY`-Laufs. Produktcode,
  Konfiguration und Tests werden erst nach der plangebundenen Übergabe geändert.
- Der künftige Implementierungslauf besteht aus genau zwei zusammenhängenden
  Slices. Die produktive Obergrenze von sieben Dateien wird in beiden Slices
  eingehalten.
- Die vollständige Matrix `python3 -m pytest tests/ -v` wird ausschließlich vom
  Orchestrator nach einem implementierten Slice ausgeführt. Agenten führen nur
  die unten genannten fokussierten synthetischen Tests aus und emittieren kein
  `VALIDATION_RESULT`.

## Repositorybefund und verbindliche Leitplanken

### Quota-Wartebetrieb

- `src/agent_runtime.py` definiert `QuotaWaitPolicy` aktuell mit
  `heartbeat_interval_seconds=300`, `maximum_wait_seconds=86400` und einer
  Sicherheitsmarge von 60 Sekunden. `wait_until_quota_resume()` besitzt bereits
  injizierbare `now_fn`, `sleep_fn` und `heartbeat_fn`, normalisiert das Ziel auf
  UTC und schläft höchstens bis zum nächsten Heartbeat. Der bestehende Test
  `tests/test_quota_wait.py` bewegt eine Fake Clock im injizierten Sleep und
  wartet daher nicht real.
- `src/cli.py` bildet dieselbe Policy über
  `--quota-heartbeat-interval`, `--quota-max-wait`,
  `--quota-safety-margin` und die Umgebungsvariablen
  `RUN_TASK_QUOTA_HEARTBEAT_INTERVAL`, `RUN_TASK_QUOTA_MAX_WAIT` sowie
  `RUN_TASK_QUOTA_SAFETY_MARGIN` ab. CLI überschreibt Umgebung, Umgebung
  überschreibt den Runtime-Default. `tests/test_cli.py` belegt diese Rangfolge;
  `README.md` und `tests/test_language_consistency.py` koppeln die dokumentierten
  Defaults an `parse_args()`.
- `src/agent_runtime.py::parse_quota_reset()` lehnt fehlende und mehrere
  voneinander abweichende Resetkandidaten bereits ab. Zeitzonenbehaftete Werte
  werden in `QuotaReset` nach UTC normalisiert; mehrdeutige oder nicht existente
  lokale Uhrzeiten bleiben fail-closed. Diese Parsergrenzen sind in
  `tests/test_quota_wait.py` für strukturierte, absolute, lokale und relative
  Providerangaben verortet.
- Die eigentliche Auto-Wait-Entscheidung liegt in
  `src/workflow.py::_persist_invocation_failure()`. Sie berechnet derzeit zuerst
  `quota_resume_at = reset_at + safety_margin` und vergleicht anschließend diese
  Summe mit `maximum_wait_seconds`. Damit verkürzt die Sicherheitsmarge die
  zulässige Provider-Resetspanne unbemerkt. `WorkflowEngine` injiziert bereits
  Uhr, Sleep und Heartbeat und persistiert den Halt vor dem Warten.
- `src/workflow.py::_invoke_role()` gibt während des Wartens Heartbeats aus,
  setzt danach den persistierten Halt zurück, prüft Policy und Repositorydiff
  erneut und checkpointet vor einem weiteren Provideraufruf. Die vorhandenen
  Tests in `tests/test_orchestrator_runtime.py` und
  `tests/test_workflow_state.py` belegen Quota-Record, Rollenbindung,
  Retryzählung und Resume des exakt fehlgeschlagenen Schritts.

### Providerinput, Retry und Persistenz

- Jeder Adapter erzeugt in `src/agent_adapters.py` einen
  `PreparedProviderInput`. Claude zerlegt file-backed Reviews in Manifest,
  Paket-Chunks, Systempolicy, Antwortschema und Startdirektive; Antigravity
  erfasst Promptdatei, Schema und Direktive. Zufällige Transportpfade werden
  aus der kanonischen Messung entfernt.
- `src/provider_input_budget.py::measure_provider_input()` misst bereits lokal
  und deterministisch Zeichen, UTF-8-Bytes, benannte Komponenten, größte
  Komponente und `input_digest`. Technische Providerlimits bleiben ohne
  belastbare Quelle `None`. `tests/test_provider_input_budget.py` und
  `tests/test_agent_adapters.py` sichern Verlustfreiheit, Byte-/Zeichentrennung,
  Komponenten und stabile Digests.
- `src/orchestrator.py::_persist_provider_bootstrap()` schreibt die Messung als
  `ProviderInputMeasurementPayload` vor dem Providerprozess idempotent in die
  strukturierte Recordkette. Der Schlüssel basiert auf der Transition und
  dedupliziert identische Resumes absichtlich. Er ist deshalb ein Fakt über
  den logischen Input, aber kein Zähler physischer Providerstarts.
- `src/agent_runtime.py::run_agent()` protokolliert lokale Messwerte und nach
  Erfolg Provider-Metadaten. `_compact_usage_metadata()` vermischt aktuell die
  Providerfelder `input_tokens`/`output_tokens` sprachlich mit dem lokalen
  file-backed Input und gibt bei fehlender Usage nur `unavailable` aus.
  Erfolgreiche physische Aufrufe werden nicht persistiert; Fehlversuche sind
  nur indirekt als `InvocationFailureRecord`, Quota-/Transient-Record und
  private Failure-Datei erkennbar.
- `src/agent_runtime.py::run_agent_checked()` erzeugt je physischem Prozess einen
  neuen `invocation_id` und klassifiziert Fehler nach genau einem Versuch.
  Der Produktionsdriver setzt sein internes `max_retries` auf null; begrenzte
  Quota- und Network-Retries erfolgen ausschließlich in
  `src/workflow.py::_invoke_role()`. Das bestehende Network-Limit bleibt zwei
  automatische Wiederholungen. Der exakt erkannte entfernte Antigravity-Fehler
  `remote error: run bash: fork/exec /usr/bin/bash: no such file or directory`
  wird vor der allgemeinen lokalen `no such file`-Regel als `NETWORK`
  klassifiziert; ein lokaler `FileNotFoundError` bleibt `BINARY`.
- `src/artifact_models.py`, `src/artifact_bridge.py` und
  `src/artifact_replay.py` liefern typisierte append-only Records,
  fortlaufende Revisionen und idempotente Schlüssel. Neue Workflows sind an
  `structured-v1` gebunden; historische `legacy-state-v3`-Läufe werden laut
  Repositoryvertrag weder migriert noch mit erfundenen Records aufgefüllt.
  Dieser Befund macht einen kleinen Providerattempt-Record erforderlich: Aus
  den vorhandenen Strukturen lassen sich erfolgreiche Versuche, deren Usage
  und ein nach Start abgebrochener Prozess sonst nicht rekonstruieren.
- `src/artifact_projection.py` ist die bestehende deterministische, read-only
  Projektion einer validierten Recordkette. Sie ist der passende Ort für
  rekonstruierbare Summen; eine Datenbank, Exportpipeline oder allgemeine
  Metrikplattform ist nicht erforderlich.

## Sliceübergreifende Verträge

1. Lokal gemessene Eingabegrößen werden stets mit dem Präfix beziehungsweise
   Schema `local_input` geführt. Providerangaben werden ausschließlich als
   `provider_usage` geführt. Ein Provider-`input_tokens`-Wert ist nie die
   Darstellung der lokal gemessenen file-backed Eingabe.
2. Jedes nicht gelieferte Token-, Turn- oder Kostenfeld bleibt im typisierten
   Payload `None`/JSON-`null` und in kompakten Logs ausdrücklich `unknown`. Null
   ist nur zulässig, wenn der Provider selbst einen belastbaren numerischen
   Nullwert geliefert hat.
3. Telemetrie übernimmt nur explizit erlaubte numerische Usagefelder. Sie
   speichert oder loggt weder Prompttext noch Komponenteninhalt, Kommandozeile,
   Umgebung, Secrets, Conversation-Inhalt noch Pfade privater Laufzeitdateien.
   Komponenten werden ausschließlich durch stabilen Namen und Größe
   dargestellt.
4. Eine logische Operation wird stabil an Run, Work Unit, Rolle, Workflowstep,
   Operation und gebundenen Fingerprint gekoppelt. Ihr erster physischer Start
   bindet zusätzlich den bereits persistierten Input-Digest. Ein späterer Start
   in demselben Entscheidungsplatz mit abweichendem Digest hält vor dem
   Providerstart fail-closed; er wird weder als Retry noch als dieselbe
   Reviewerentscheidung verbucht.
5. Ein physischer Versuch erhält vor dem Prozessstart eine fortlaufende Nummer
   unter der logischen Operation. Der Start wird vor Ausführung append-only
   persistiert; Erfolg oder klassifizierter Fehler ergänzt denselben Versuch
   als nächste Revision. Ein Crash nach dem Start lässt einen sichtbaren
   Versuch mit unbekanntem Abschluss zurück und ein Resume beginnt mit der
   nächsten Nummer.
6. Idempotency-Keys unterscheiden Start und Abschluss eines Versuchs und sind
   aus logischer Operations-ID und Versuchszahl ableitbar. Wiederholtes Laden
   oder Projizieren erzeugt keine Records. Bereits persistierte
   Reviewerentscheidungen behalten ihre vorhandenen Keys und werden durch
   Telemetrie weder neu geparst noch erneut geschrieben.
7. Providerattempt-Records sind additive Betriebsfakten, keine neue fachliche
   Entscheidung und keine Änderung von Reviewerreihenfolge, Retryklasse,
   Retrylimit, Freigabesemantik oder Record-first-Migrationsumfang.

### Slice 1 - Quota-Wartepolitik, Sieben-Tage-Grenze und Fake Clock

#### Ausgangslage und Integrationspunkte

Die Policydefaults liegen in `QuotaWaitPolicy`, ihre CLI-/Umgebungsauflösung in
`parse_args()`, die Grenzentscheidung in `_persist_invocation_failure()` und die
Warteschleife in `wait_until_quota_resume()`. Dokumentation und Default-Drift
werden bereits automatisiert geprüft. Es ist weder ein neues Konfigurationsfile
noch eine neue Retryklasse erforderlich.

#### Fachlicher Vertrag und Fehlerverhalten

- Setze ausschließlich die Defaults für Heartbeat auf 3600 Sekunden und für
  maximale automatische Provider-Resetspanne auf 604800 Sekunden. Alle
  bestehenden CLI- und Umgebungsüberschreibungen bleiben erhalten.
- Trenne `reset_delay = max(0, reset_at_utc - now_utc)` von
  `resume_delay = reset_delay + safety_margin`. Für die inklusive
  Sieben-Tage-Grenze wird ausschließlich `reset_delay <= 604800` geprüft; die
  Sicherheitsmarge bestimmt nur den tatsächlichen Resumezeitpunkt. So ist ein
  eindeutig erkannter Reset exakt an Tag sieben automatisch zulässig, ohne die
  Spanne um die Marge zu verkürzen oder zu erweitern.
- Ein Reset oberhalb der Grenze, kein Reset oder ein mehrdeutiger Reset erzeugt
  wie bisher einen persistierten manuellen Resume-Halt. Der Workflow schläft in
  diesen Fällen nicht automatisch. Negative Zeitdifferenzen werden als bereits
  erreicht behandelt, ohne negative Sleeps.
- Erzeuge sofort getrennt erkennbare Statusereignisse für Eintritt in den
  Wartezustand, Erreichen des Resetzeitpunkts und Wiederaufnahme. Dazwischen
  emittiert die Schleife höchstens im konfigurierten Intervall. Der zusätzliche
  Margenabschnitt darf keinen stündlichen Spam erzeugen, muss aber den
  Reset-Grenzübertritt sofort melden.
- Sämtliche Zeitentscheidungen verwenden die injizierte zeitzonenbehaftete Uhr.
  Naive Uhrwerte bleiben ein Fehler. UTC ist die Entscheidungsbasis; lokale Zeit
  dient nur der lesbaren Statusanzeige.
- Unterbrechung durch `KeyboardInterrupt`, Rollen-/Fingerprintabweichung und
  bestehende Quota-Resume-Diff-Gates behalten ihr fail-closed Verhalten.

**Exakter Änderungspfad**

- `README.md`
- `src/agent_runtime.py`
- `src/cli.py`
- `src/workflow.py`
- `tests/test_cli.py`
- `tests/test_language_consistency.py`
- `tests/test_orchestrator_runtime.py`
- `tests/test_quota_wait.py`

#### Fokussierte synthetische Akzeptanztests

- `tests/test_cli.py`: Runtime-Default 3600/604800, explizite CLI-Werte und
  Umgebungswerte sowie die vorhandene Präzedenz CLI > Umgebung > Default.
- `tests/test_language_consistency.py`: README-Tabelle stimmt exakt mit den
  aufgelösten Runtimewerten überein und nennt die bestehenden
  Umgebungsvariablen.
- `tests/test_quota_wait.py`: Eine Fake Clock überspringt sieben Tage in
  einstündigen Schritten ohne reales Sleep; Status wird sofort bei Beginn,
  Reset und Resume ausgegeben, periodische Meldungen erscheinen höchstens
  stündlich. Der Test prüft auch Unterbrechung und bereits erreichten Reset.
- `tests/test_orchestrator_runtime.py`: exakt 604800 Sekunden Resetspanne plus
  Sicherheitsmarge wird automatisch akzeptiert; 604801 Sekunden, fehlender
  Reset und mehrdeutiger Reset bleiben manuell. Gegenproben mit Marge null und
  positiver Marge belegen beide Grenzseiten.
- `tests/test_quota_wait.py` und `tests/test_orchestrator_runtime.py`: Offsets
  werden vor dem Vergleich nach UTC normalisiert; eine DST-mehrdeutige lokale
  Uhrzeit bleibt ungeparst und führt nicht zum Auto-Wait.
- Agent-fokussierter Lauf:
  `python3 -m pytest tests/test_quota_wait.py tests/test_cli.py tests/test_language_consistency.py tests/test_orchestrator_runtime.py -q`.

#### Resume-, Retry-, Datenschutz- und Rückwärtskompatibilitätsgrenzen

- `maximum_auto_resumes`, Failureklassifikation, Quota-/Retry-Records,
  Checkpointreihenfolge und Diff-Revalidierung werden nicht erweitert.
- Persistierte ältere Zustände behalten ihren konkreten `resume_at_utc`; neue
  Defaults werden nicht rückwirkend in laufende Warteentscheidungen
  hineingerechnet. Ein manueller Resume bleibt möglich.
- Statusmeldungen enthalten weiterhin nur Rolle, Tasklabel, Work Unit,
  Zeitpunkte und Restdauer, nie Providerantwort oder Prompt.
- Falls die drei sofortigen Phasen nicht mit der bestehenden injizierten Uhr
  ohne eine neue globale Schedulerabstraktion ausdrückbar sind, stoppt der
  Slice. Eine allgemeine Zeit-/Schedulerplattform ist nicht autorisiert.

### Slice 2 - Providerattempt-Telemetrie und Antigravity-Erstfehler

#### Ausgangslage und Integrationspunkte

Die verlustfreie lokale Inputmessung und ihr strukturierter Record existieren
bereits. Es fehlt nur der persistierte Lebenszyklus jedes echten
Providerprozesses. Die Erweiterung wird deshalb an den vorhandenen
Adapter-Metadaten, `run_agent()`/`run_agent_checked()`, dem
`ArtifactBridge`-Append und der read-only Projektion angeschlossen. Der
Workflow-Retrypfad selbst und seine Limits bleiben unverändert.

#### Fachlicher Vertrag und Fehlerverhalten

- Ergänze einen eng begrenzten typisierten Providerattempt-Payload mit Rolle,
  Operation, Work Unit, stabiler logischer Operations-ID, gebundenem
  Fingerprint, Input-Messrecord/Input-Digest, physischer Versuchszahl,
  UTC-Start, optionalem UTC-Ende, Laufzeit, Ergebnis- oder vorhandener
  Fehlerklasse und normalisierter optionaler Provider-Usage.
- Persistiere `started` unmittelbar nach erfolgreichem lokalen Budget- und
  Capability-Preflight, aber vor `Popen`/`subprocess.run`. Ergänze danach genau
  eine terminale Revision für Erfolg oder die bereits bestehende klassifizierte
  Fehlerklasse. Ein Preflightabbruch ohne Providerprozess zählt nicht als
  physischer Versuch.
- Normalisiere nur bekannte numerische Providerfelder, darunter soweit
  geliefert Input, Output, Total, Cache-Read, Cache-Write/Creation, Thinking,
  Tool-/Read-Kontext, Turns und Kosten. Providerbezeichnungen dürfen intern auf
  dieses kleine Schema abgebildet werden; nicht vorhandene Werte bleiben
  `None`. Es gibt keine Token- oder Kostenschätzung aus Zeichen oder Bytes.
- Die kompakte Ausgabe zeigt `local_input_chars`, `local_input_utf8_bytes`,
  Komponentenzahl, größte Komponente und Digest getrennt von
  `provider_usage_*`. Auch der Full-Modus verwendet die sichere Allowlist und
  gibt nicht mehr das rohe Metadatenobjekt aus.
- Die Projektion reduziert pro physischem Versuch auf dessen neueste Revision
  und berechnet daraus Anzahl, bekannte Laufzeit sowie ausschließlich Summen
  tatsächlich gelieferter Usagefelder pro logischer Operation und Run.
  Unvollständige Starts und unbekannte Usage bleiben sichtbar und werden nicht
  als Null addiert.
- Der bestehende entfernte Antigravity-`/usr/bin/bash`-Fehler bleibt exakt
  `NETWORK`; der lokale fehlende `agy`-Prozess bleibt `BINARY`. Es wird weder
  eine neue Retryklasse noch ein dritter automatischer Retry eingeführt.
- Beim Regressionfall bindet Versuch 1 den Input-Digest und endet `NETWORK`;
  Versuch 2 verwendet denselben Digest und endet erfolgreich. Beide gehören zu
  einer logischen Reviewoperation. Der vorhandene Reviewpersistenzpfad schreibt
  genau einen `ReviewPayload`/eine Reviewerentscheidung.
- Ein Digestwechsel innerhalb desselben logischen Entscheidungsplatzes hält vor
  dem zweiten Providerstart mit einer typisierten, resumierbaren Diagnose. Er
  darf nicht als identischer Retry oder als Grundlage derselben Entscheidung
  erscheinen.

**Exakter Änderungspfad**

- `src/agent_adapters.py`
- `src/agent_runtime.py`
- `src/artifact_bridge.py`
- `src/artifact_models.py`
- `src/artifact_projection.py`
- `src/artifact_replay.py`
- `src/orchestrator.py`
- `tests/test_agent_adapters.py`
- `tests/test_agent_runtime.py`
- `tests/test_artifact_models.py`
- `tests/test_artifact_projection.py`
- `tests/test_artifact_replay.py`
- `tests/test_orchestrator_runtime.py`
- `tests/test_provider_input_budget.py`
- `tests/test_structured_artifact_regressions.py`

#### Fokussierte synthetische Akzeptanztests

- Adapter-/Runtime-Tests liefern einmal vollständige Provider-Usage und einmal
  keine Usage. Vollständige Werte bleiben providerbezogen; fehlende Felder sind
  `None`/`unknown`, niemals synthetische Nullwerte.
- File-backed Claude- und Antigravity-Inputs prüfen Zeichen, UTF-8-Bytes,
  Komponentenanzahl, größten Bestandteil und denselben stabilen Digest wie der
  bestehende Budgetpfad. Keine Ausgabe bezeichnet diese Größen als
  Provider-Inputtokens.
- Modell-, Replay- und Projektionstests prüfen `started` plus terminale Revision,
  einen nach Start abgebrochenen Versuch, exakte bekannte Summen und unbekannte
  Teilwerte. Zweimaliges Laden/Projizieren verändert weder Recordzahl noch
  Summen.
- Eine Crash-Injektion nach veröffentlichtem Startrecord und vor
  Abschlussrevision führt beim Resume zu Versuch 2; der offene erste Versuch
  bleibt einmal erhalten. Eine simulierte durable-but-reported-failed
  Veröffentlichung wird über den bestehenden Idempotency-Key wiedergefunden.
- Der Antigravity-Durchstichtest simuliert zuerst exakt den entfernten
  `/usr/bin/bash`-Instanzfehler und danach Erfolg mit identischem Input. Erwartet
  werden eine logische Reviewoperation, zwei physische Versuche, Versuchsklassen
  `NETWORK`/Erfolg, unverändertes bestehendes Retrylimit und genau eine
  persistierte Reviewerentscheidung.
- Eine Gegenprobe mit echtem lokalem `FileNotFoundError("agy")` startet keinen
  automatischen zweiten Versuch. Eine weitere Gegenprobe ändert den Digest und
  erwartet einen fail-closed Halt vor dem zweiten Providerprozess.
- Privacy-Tests verwenden markierte Prompt-, Secret- und private
  Laufzeitdateiinhalte und suchen sie in Logs, Payloads, Projektion und
  persistierten Diagnosen; nur Größen, erlaubte Usagewerte und Digests dürfen
  erscheinen.
- Agent-fokussierter Lauf:
  `python3 -m pytest tests/test_agent_adapters.py tests/test_agent_runtime.py tests/test_artifact_models.py tests/test_artifact_replay.py tests/test_artifact_projection.py tests/test_provider_input_budget.py tests/test_structured_artifact_regressions.py tests/test_orchestrator_runtime.py -q`.

#### Resume-, Retry-, Datenschutz- und Rückwärtskompatibilitätsgrenzen

- Structured-v1 verwendet die Recordkette als Autorität. State und bestehende
  Reviewrecords werden nicht zu einer zweiten Telemetriequelle erweitert.
  Historische Legacy-Läufe erhalten keine synthetischen Attemptrecords und keine
  rückwirkend geschätzte Usage; dort bleibt nicht vorhandene Historie unbekannt.
- Recordparser und Replay akzeptieren alte Ketten ohne Providerattempts. Bei
  neuen Ketten sind fehlende Referenzen, nicht fortlaufende Versuche,
  widersprüchliche Digests oder divergierende Revisionen fail-closed.
- Quota-, Network- und Output-Retries bleiben fachlich ein Aufruf desselben
  Workflowsteps, sofern Rolle, Fingerprint und Input-Digest identisch sind.
  Reviewrundenzähler und Findings werden durch Attemptrecords nicht verändert.
- Rohe Provider-Metadaten werden weder im Full- noch im Compact-Log
  serialisiert. Freitext aus Providerfehlern verbleibt in den bereits
  begrenzten/sanitierten Diagnosewegen und wird nicht in Usage-Summen kopiert.
- Falls die erforderliche Attempt-Idempotenz nur durch Änderungen an
  `src/workflow_state.py`, `src/state_io.py`, einer neuen Metrikplattform oder
  einem achten produktiven Pfad erreichbar wäre, stoppt der Slice. Der Scope
  wird nicht eigenmächtig erweitert.

## Abnahme- und Stopregeln

- Jeder Slice stoppt bei einem benötigten Pfad außerhalb seines exakten
  Änderungspfads. Eine Scopeerweiterung benötigt einen neu geprüften Plan.
- Mehr als zwei Slices oder mehr als sieben produktive Dateien in einem Slice
  führen zu `PLAN_READY: NO`; der kleinste Folgeschnitt wäre dann die
  read-only Summenprojektion nachgelagert zur weiterhin atomaren
  Attempt-Persistenz, nicht eine Ausweitung des laufenden Slices.
- Ist Provider-Usage nicht zuverlässig verfügbar, bleibt sie unbekannt. Lokale
  Größen werden nicht zur Schätzung von Tokens oder Kosten verwendet.
- Ist der entfernte Antigravity-Shellfehler nicht anhand des bestehenden
  strukturierten Envelope-/Fehlerpfads eindeutig von einem lokalen fehlenden
  `agy` unterscheidbar, wird kein automatischer Retry hinzugefügt; die
  bestehende fail-closed Klassifikation bleibt maßgeblich.
- Änderungen an P2-FU-018, P2-FU-020, P2-FU-021, Workflow-Completion,
  Inbox-/Outboxabschluss, Reviewerreihenfolge, Approvalsemantik, Warm-up,
  Session-Reuse, Provideraustausch oder allgemeiner Observability bleiben
  ausdrücklich außerhalb dieses Releases.
- Nach jedem implementierten Slice führt der Orchestrator zusätzlich zur
  fokussierten Matrix die vollständige bestehende Testsuite aus. Erst deren
  fingerprintgebundene Attestierung darf an Claude- und anschließend genau
  einen Antigravity-Review übergeben werden.

## Orchestrator-Prüfprotokoll

### Review-Feedback von Claude

<!-- audit:claude-review:begin -->
### Ereignis 2: Runde 1

- Reviewer: `claude`
- Freigabe: `YES`
- Validierungsbindung: `plan-validation-0f99985382bb`
- Testdateien: keine
- Eigene Findings: `C-01`

<!-- artifact-records:claude-review:begin -->
Semantischer Record-Digest: `38370744274b230fee637cd6eaa30cb109206af570515e9470ea3ceb720bb1ae`

- 6. `ar1-5305abfd7479322260bc6821a94fcee5e92c37958981516eda417d4c8a1c814b`: `approved`; Work-Unit `1`; Findings `C-01`; Fingerprint `0f99985382bb7b4a19fe8570577e593dcc32d324784f005efb2599373e217abb`
<!-- artifact-records:claude-review:end -->
<!-- audit:claude-review:end -->

### Review-Feedback von Antigravity

<!-- audit:antigravity-review:begin -->
### Ereignis 3: Runde 1

- Reviewer: `antigravity`
- Freigabe: `YES`
- Validierungsbindung: `plan-validation-0f99985382bb`
- Testdateien: keine
- Prüfdimensionen: Plan completeness and scope discipline across 2 slices and 7-file limit, quota wait policy separation of reset_delay and safety margin up to 604800s, fake clock regression coverage, local input measurement versus provider usage semantics, provider attempt lifecycle idempotency and crash resilience, Antigravity bash error network retry classification, privacy constraints on logs/records, read-only projection sums without external platform
- Größtes Restrisiko: Attempt sequence numbering and crash recovery derivation in Slice 2 without modifying workflow.py
- Realistische Bruchbedingung: A crashed provider attempt without a terminal record causes duplicate attempt numbering or replay validation failure during subsequent resume
- Eigene Findings: keine

<!-- artifact-records:antigravity-review:begin -->
Semantischer Record-Digest: `38370744274b230fee637cd6eaa30cb109206af570515e9470ea3ceb720bb1ae`

- 9. `ar1-21bd53485e85d2e9d5a7e9db1a42a844dcd4aaf8831bd67e07d5762672ed5756`: `approved`; Work-Unit `1`; Findings `C-01`; Fingerprint `0f99985382bb7b4a19fe8570577e593dcc32d324784f005efb2599373e217abb`
<!-- artifact-records:antigravity-review:end -->
<!-- audit:antigravity-review:end -->

### Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
Noch keine strukturierten Codex-Antworten.

<!-- artifact-records:codex-responses:begin -->
Semantischer Record-Digest: `38370744274b230fee637cd6eaa30cb109206af570515e9470ea3ceb720bb1ae`

Keine Codex-Findingantworten.
<!-- artifact-records:codex-responses:end -->
<!-- audit:codex-responses:end -->

### Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### Ereignis 1: `plan-validation-0f99985382bb`

- Diff-Fingerprint: `0f99985382bb7b4a19fe8570577e593dcc32d324784f005efb2599373e217abb`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: internal plan contract passed
- Ausgabedigest: `0c3d96b63f927bce40cd4e037fb2d39fd2c256173bd2c3f8e5596dc63d507724`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| internal:work-plan-contract | PASS | 0 | slices=1; planned_paths=1; changed_paths=1; future_slices=2; work_plan=docs/internal/release-1-1c-quota-wartebetrieb-und-provideraufruf-telemetrie-arbeitsplan.md |

<!-- artifact-records:validation-attestation:begin -->
Semantischer Record-Digest: `38370744274b230fee637cd6eaa30cb109206af570515e9470ea3ceb720bb1ae`

- 2. `ar1-075c327e957f0f294cb7cd72f12956774320470d2a740553002ea828c313f229`: Providerinput `codex/codex_plan` = `allowed`; Zeichen `20620/4000000`, Bytes `20716/16000000`; Input `973135ea301aa4cc5394182f7c841606422e4bbede31517c1934c0da376f6e5a`, Policy `8cff2b572a65674a0b31c2e2751cdc76fa839112b05e38b09bd021a02284bc83`, Übergang `c6cde0d6bc080b19939dc2be09784e6666de7ee66851235caee9065a194e426c`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, größte Komponente `stdin_prompt`; Komponenten `stdin_prompt=20620/20716`
- 4. `ar1-75976291bc127eee091eda22b93ddd6e7d4e5c15ad5b40c3a8888b5e6bbea918`: Attestierung durch `orchestrator`; Fingerprint `0f99985382bb7b4a19fe8570577e593dcc32d324784f005efb2599373e217abb`
  - `pass` / Exit `0` / Output `0c3d96b63f927bce40cd4e037fb2d39fd2c256173bd2c3f8e5596dc63d507724`: `argv` [`internal:work-plan-contract`]
- 5. `ar1-36c3b68bd0e85e5f5dc22dfff9c60c19917b47ad51438d6dac05122448bf6bea`: Providerinput `claude/claude_plan_review` = `allowed`; Zeichen `50849/4000000`, Bytes `51130/16000000`; Input `a384b7a8c27c42a54dff107f2f28983196c89c2f1078ed0ae1cf178c7b9b52b0`, Policy `8cff2b572a65674a0b31c2e2751cdc76fa839112b05e38b09bd021a02284bc83`, Übergang `df885e00bf82b1a009d7326a16acfc59764e569fbfe51ce39dad8242603a6d2d`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, größte Komponente `packet_chunk_001`; Komponenten `packet_chunk_001=23968/24093, packet_chunk_002=23872/24028, packet_chunk_003=1299/1299, packet_manifest=562/562, system_policy=660/660, response_schema=146/146, start_directive=342/342`
- 8. `ar1-0ca7e873c49504f1f009bc1b6fe9639e021a16c47be10ce5a509c770db7d6b09`: Providerinput `antigravity/antigravity_plan_review` = `allowed`; Zeichen `55531/4000000`, Bytes `55811/16000000`; Input `62f89f83d95e57385aa791b379d4b1fde4dde6faa5d1e8297635fba31df80671`, Policy `8cff2b572a65674a0b31c2e2751cdc76fa839112b05e38b09bd021a02284bc83`, Übergang `c018072506971f91ff4470dd5e848ffab863b30d9006c6d822d62626c6298ac2`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, größte Komponente `prompt_file`; Komponenten `prompt_file=54872/55152, response_schema=146/146, start_directive=513/513`
<!-- artifact-records:validation-attestation:end -->
<!-- audit:validation-attestation:end -->

### Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
- Teständerungsfreigabe: nicht erfasst.
- Pre-Mortems:
  - Ereignis 2: Most likely three-month failure cause is Slice 2 discovering mid-implementation that attempt-number continuity across workflow.py's quota/network retry loop cannot be reconstructed read-only from the artifact chain fast enough or unambiguously (e.g., a crashed first attempt with no terminal revision yet at the moment of the second call), forcing either a hidden extra parameter added to workflow.py (an undeclared path) or a race in "next attempt number" derivation that double-counts or skips a physical attempt during resume.
  - Ereignis 3: The most likely failure cause in three months is an unhandled edge case during crash-recovery where an in-flight provider attempt lacking a terminal record causes attempt-number derivation or replay projection to fail during resume, requiring strict validation of incomplete attempt lifecycles during Slice 2 implementation.

<!-- artifact-records:test-approval-premortem:begin -->
Semantischer Record-Digest: `38370744274b230fee637cd6eaa30cb109206af570515e9470ea3ceb720bb1ae`

Keine strukturierten Gates.
<!-- artifact-records:test-approval-premortem:end -->
<!-- audit:test-approval-premortem:end -->

### Findings-Lebenszyklus

<!-- audit:findings:begin -->
### `C-01` — `OPEN`

- Quelle: `claude`; Runde 1
- Klasse: `OBSERVATION`
- Finding: Slice 2 assumes attempt-sequence numbering is fully derivable inside agent_runtime.py/artifact_bridge.py without touching workflow.py, while sitting at the exact 7-production-file ceiling with no headroom; if the existing &#96;_invoke_role()&#96; call signature does not already carry enough identifiers to reconstruct the logical-operation key before Popen, the documented Slice stop condition (eighth-path/workflow_state.py trigger) must fire rather than silently widening scope.
- Akzeptanztest: During Slice 2 kickoff, before writing code, confirm the exact parameters already passed from &#96;workflow.py::_invoke_role()&#96; into &#96;run_agent()&#96;/&#96;run_agent_checked()&#96; are sufficient to compute the logical operation ID and next attempt number via ArtifactBridge alone; if not sufficient, the implementer must invoke the plan's own stop condition (persist a resumable diagnosis and halt) instead of adding workflow.py to the change path informally, and this must be demonstrated by a focused test that constructs two sequential attempts purely from existing call arguments with no workflow.py modification.
- Statusbegründung: –

<!-- artifact-records:findings:begin -->
Semantischer Record-Digest: `38370744274b230fee637cd6eaa30cb109206af570515e9470ea3ceb720bb1ae`

- 7. `ar1-6eadfeb4df30b97f58b655011ddc476b56cb0b20b73f8751ad620f0c8caf4163`: `C-01` `opened` durch `claude`; `OBSERVATION` / `open` — Slice 2 assumes attempt-sequence numbering is fully derivable inside agent_runtime.py/artifact_bridge.py without touching workflow.py, while sitting at the exact 7-production-file ceiling with no headroom; if the existing &#96;_invoke_role()&#96; call signature does not already carry enough identifiers to reconstruct the logical-operation key before Popen, the documented Slice stop condition (eighth-path/workflow_state.py trigger) must fire rather than silently widening scope.
<!-- artifact-records:findings:end -->
<!-- audit:findings:end -->

### Entscheidungstabelle

<!-- audit:decision-table:begin -->
| ID | Quelle | Finding | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|---|
| C-01 | claude | Slice 2 assumes attempt-sequence numbering is fully derivable inside agent_runtime.py/artifact_bridge.py without touching workflow.py, while sitting at the exact 7-production-file ceiling with no headroom; if the existing &#96;_invoke_role()&#96; call signature does not already carry enough identifiers to reconstruct the logical-operation key before Popen, the documented Slice stop condition (eighth-path/workflow_state.py trigger) must fire rather than silently widening scope. | OBSERVATION | offen | offen |

<!-- artifact-records:decision-table:begin -->
Semantischer Record-Digest: `38370744274b230fee637cd6eaa30cb109206af570515e9470ea3ceb720bb1ae`

| Seq | Record | Typ | Status | Logische ID | Revision | Fingerprint |
|---:|---|---|---|---|---:|---|
| 1 | `ar1-1ccdac940a0598699cf1248101a27d36a756ca704e7cfe497a384292e7df42b9` | `task` | `accepted` | `task-contract` | 1 | `contract:b4c0ad0c9e34b33f8c1b49cf718da568a5356d6b59dc21b2421d0f0295b52fa9` |
| 2 | `ar1-075c327e957f0f294cb7cd72f12956774320470d2a740553002ea828c313f229` | `provider_input_measurement` | `measured` | `provider-input-1-codex_plan` | 1 | `implementation:b4c0ad0c9e34b33f8c1b49cf718da568a5356d6b59dc21b2421d0f0295b52fa9` |
| 3 | `ar1-dbefd0ed6f33a31c958ace36fb1c574ffd00a798c82ad9946336258cc72755a4` | `agent_result` | `ready` | `agent-1-codex_plan-1` | 1 | `contract:b4c0ad0c9e34b33f8c1b49cf718da568a5356d6b59dc21b2421d0f0295b52fa9` |
| 4 | `ar1-75976291bc127eee091eda22b93ddd6e7d4e5c15ad5b40c3a8888b5e6bbea918` | `validation_attestation` | `attested` | `plan-validation-0f99985382bb` | 1 | `implementation:0f99985382bb7b4a19fe8570577e593dcc32d324784f005efb2599373e217abb` |
| 5 | `ar1-36c3b68bd0e85e5f5dc22dfff9c60c19917b47ad51438d6dac05122448bf6bea` | `provider_input_measurement` | `measured` | `provider-input-1-claude_plan_review` | 1 | `implementation:b4c0ad0c9e34b33f8c1b49cf718da568a5356d6b59dc21b2421d0f0295b52fa9` |
| 6 | `ar1-5305abfd7479322260bc6821a94fcee5e92c37958981516eda417d4c8a1c814b` | `review` | `decided` | `review-claude-1-1` | 1 | `implementation:0f99985382bb7b4a19fe8570577e593dcc32d324784f005efb2599373e217abb` |
| 7 | `ar1-6eadfeb4df30b97f58b655011ddc476b56cb0b20b73f8751ad620f0c8caf4163` | `finding_transition` | `recorded` | `finding-C-01` | 1 | `implementation:0f99985382bb7b4a19fe8570577e593dcc32d324784f005efb2599373e217abb` |
| 8 | `ar1-0ca7e873c49504f1f009bc1b6fe9639e021a16c47be10ce5a509c770db7d6b09` | `provider_input_measurement` | `measured` | `provider-input-1-antigravity_plan_review` | 1 | `implementation:b4c0ad0c9e34b33f8c1b49cf718da568a5356d6b59dc21b2421d0f0295b52fa9` |
| 9 | `ar1-21bd53485e85d2e9d5a7e9db1a42a844dcd4aaf8831bd67e07d5762672ed5756` | `review` | `decided` | `review-antigravity-1-1` | 1 | `implementation:0f99985382bb7b4a19fe8570577e593dcc32d324784f005efb2599373e217abb` |
<!-- artifact-records:decision-table:end -->
<!-- audit:decision-table:end -->

### Freigabestatus

<!-- audit:approval-status:begin -->
- Implementierung bereit: `NOT_RECORDED`
- Validierung: `PASS`
- Claude-Freigabe: `YES`
- Antigravity-Freigabe: `YES`
- Red-State-Folgeslice: `NONE`
- Commit autorisiert: `NO`

<!-- artifact-records:approval-status:begin -->
Semantischer Record-Digest: `38370744274b230fee637cd6eaa30cb109206af570515e9470ea3ceb720bb1ae`

Keine Work-Unit- oder Binding-Records.
<!-- artifact-records:approval-status:end -->
<!-- audit:approval-status:end -->
