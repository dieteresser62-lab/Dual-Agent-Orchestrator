# Release 1.1C2 – Providerattempt-Record und Schema

## Planstatus und Ausführungsgrenze

- Zielbranch ist `feature/orchestrator-stabilization-1-1c`; der geprüfte HEAD ist
  `fac92b0` (`Slice 01: Quota-Wartepolitik, Sieben-Tage-Grenze und Fake Clock`).
- Dieser Lauf ist `PLAN_ONLY`. Er ändert ausschließlich dieses Arbeitsplanartefakt;
  Produktcode, Tests, Konfiguration und generierte Artefakte bleiben im Planungslauf
  unverändert.
- Der abgebrochene frühere Slice 02 hat keinen Produktcode hinterlassen. Seine
  Allowlist wird nicht übernommen. Der unten ausgewiesene künftige Slice ist aus dem
  aktuellen Repository neu hergeleitet und enthält das zuvor fehlende
  `schemas/orchestrator-artifact-v1.schema.json` ausdrücklich.
- Die beiden offenen Claude-Beobachtungen aus Slice 01 werden erhalten und im
  künftigen Slice ausschließlich durch `README.md` und
  `tests/test_quota_wait.py` abgeschlossen. Der aktuelle Produktcode enthält die
  notwendige Ordnungsprüfung und das beobachtete Margenverhalten bereits; deshalb
  wird Slice 01 weder erneut geplant noch verändert und es ist kein eigener
  Produktcodeslice erforderlich.
- Der künftige Implementierungslauf besteht aus genau einem zusammenhängenden
  Slice. Er hat genau sieben produktive Änderungspfade; Schema, Modell,
  Persistenzbridge, Replay, Runtimeintegration, Orchestratorintegration und die
  notwendige read-only Projektion bilden damit einen vollständigen Kern.
- Agenten führen nur die fokussierten Tests des Slices aus. Die vollständige Matrix
  `python3 -m pytest tests/ -v` wird ausschließlich vom Orchestrator ausgeführt;
  Agenten emittieren kein `VALIDATION_RESULT`.

## Repositorybefund: geschlossene Record- und Providerkette

### Modell und JSON-Schema

- `src/artifact_models.py::RecordType`, `ArtifactPayload`,
  `ArtifactRecord.from_dict()` und `_payload_from_dict()` bilden gemeinsam den
  geschlossenen Python-Parser. `ArtifactRecord.__post_init__()` bindet Recordtyp,
  Payloadtyp, Status, stabile Record-ID und das gebündelte Schema aneinander.
  Ein neuer Recordtyp ist daher ohne Änderung dieser Datei nicht lesbar.
- `schemas/orchestrator-artifact-v1.schema.json` führt dieselbe geschlossene
  `record_type`-Enum, typabhängige `allOf`-Verzweigungen und Payloaddefinitionen.
  `additionalProperties: false` schützt die Payloads vor stiller Erweiterung.
  Python-Modell und Schema müssen den neuen Typ deshalb atomar und mit denselben
  Null-/Enum-/Zahlgrenzen ergänzen. Die Schema-Version bleibt `1`: Der neue Typ ist
  additiv; bestehende gültige v1-Records werden nicht um neue Pflichtfelder ergänzt.
- `src/artifact_store.py` serialisiert und lädt ausschließlich
  `ArtifactRecord.to_dict()`/`from_dict()`. Seine append-only Reihenfolge,
  Idempotency-Konfliktprüfung und durable-but-reported-failed Wiederfindung sind
  bereits generisch. Für `provider_attempt` ist dort keine produktive Änderung
  erforderlich.

### Persistenz, Replay und Projektion

- `src/artifact_bridge.py::ArtifactBridge.append()` lädt vor jedem Append die
  autoritative Kette, bestimmt die nächste Revision pro Recordtyp und logischer ID,
  persistiert mit einem Idempotency-Key und liest einen möglicherweise durable
  geschriebenen Record nach gemeldetem Fehler erneut. Eine enge
  Providerattempt-Bridge kann Start und Terminalrevision auf diesem Mechanismus
  aufbauen, ohne einen zweiten Store oder State-v3-Spiegel einzuführen.
- `src/artifact_replay.py::replay_artifacts()` prüft Typgleichheit, globale
  Kettenreihenfolge, eindeutige Idempotency-Keys und lückenlose Revisionen. In
  `_validate_payload_references()` werden bereits Work-Unit- und Recordreferenzen
  typisiert aufgelöst. Dort muss die zusätzliche Attempt-Invariante liegen:
  Revision 1 ist `started`, Revision 2 ist genau ein Terminalzustand; die
  Messrecordreferenz liegt vorher, gehört zum selben Run/Fingerprint und trägt
  denselben Input-Digest. Eine dritte, terminale-ohne-Start- oder in unveränderlichen
  Bindungsfeldern abweichende Revision scheitert fail-closed.
- `src/artifact_projection.py` nimmt ausschließlich ein erfolgreiches
  `ArtifactReplayResult` an. Sie selektiert Work-Unit-bezogene Records und rendert
  deterministisch aus der Kette. Damit kann sie Attempts nach logischer Operation
  und Run reduzieren, ohne Records zu erzeugen oder Reviewerentscheidungen neu zu
  interpretieren. Diese notwendige Projektion bleibt im Kern; eine Datenbank,
  Exportpipeline oder allgemeine Observability-Plattform ist nicht vorgesehen.

### Providerstart und vorhandene Aufrufargumente

- `src/orchestrator.py::_agent()` übergibt Rolle, konkrete Workflowoperation und
  gebundenen Fingerprint an `src/agent_runtime.py::run_agent_checked()` und
  `run_agent()`. `_persist_provider_bootstrap()` besitzt zusätzlich den aktiven
  `run_id`, die aktuelle Work-Unit, die strukturierte Bridge und den gerade
  persistierten `ProviderInputMeasurementPayload` samt Record-ID und Input-Digest.
- `run_agent()` bereitet den vollständigen Transportinput vor, misst ihn lokal,
  persistiert ihn über den vorhandenen Callback, prüft Budget sowie lokale
  Capability/Binärdatei und ruft erst danach `subprocess.Popen()` beziehungsweise
  `subprocess.run()` auf. Der neue durable Start-Hook wird nach diesen lokalen
  Preflights und unmittelbar vor dem Subprozess platziert. Ein fehlendes lokales
  `agy` scheitert damit weiterhin vor einem physischen Attempt als `BINARY`.
- `run_agent_checked()` erzeugt bereits pro realem Aufruf eine neue
  `invocation_id` und klassifiziert jede Exception genau einmal. Der künftige
  Lifecycle-Hook bekommt deshalb dort nach Erfolg oder Klassifikation genau einen
  Terminalabschluss. `src/agent_adapters.py` muss nicht geändert werden: Claude und
  Antigravity legen tatsächlich gelieferte Metadaten bereits in `adapter.metadata`
  ab; Auswahl, Normalisierung und sichere Ausgabe gehören zentral in
  `src/agent_runtime.py`.
- `src/workflow.py::_invoke_role()` wiederholt bei den bestehenden Quota- und
  Networkklassen dieselbe gebundene Callable. Der vorhandene Network-Pfad erkennt
  den exakten entfernten Antigravity-Fehler
  `remote error: run bash: fork/exec /usr/bin/bash: no such file or directory`
  bereits als `NETWORK`; ein echtes lokales `FileNotFoundError` bleibt `BINARY`.
  Weder Signatur noch Retryklasse oder Limit in `src/workflow.py` müssen geändert
  werden.

### Stabiler logischer Schlüssel und Versuchszahl

- Die logische Operations-ID wird vor dem ersten Providerstart deterministisch aus
  `run_id`, Work-Unit-ID, Rolle/Provider, konkreter Operation und dem gebundenen
  Record-Fingerprint gebildet. Der bereits vorhandene `relevant_record_head` oder
  `transition_fingerprint` ist absichtlich kein Teil dieses Schlüssels: Append-only
  Attemptrecords verändern den Head zwischen zwei physischen Versuchen.
- Der erste Attempt bindet an dieser logischen ID den vorher persistierten
  Messrecord und dessen Input-Digest. Vor jedem weiteren Start lädt die Bridge die
  akzeptierte Kette, vergleicht Rolle, Operation, Work Unit, Fingerprint und Digest
  mit dem ersten Attempt und leitet `attempt_number = max(existing) + 1` ab. Ein
  anderer Digest oder Fingerprint hält vor dem Lifecycle-Append und vor dem
  Subprozess fail-closed; er wird weder umetikettiert noch derselben
  Reviewerentscheidung zugerechnet.
- Die Schlüssel
  `provider-attempt:<logical-operation-id>:<attempt-number>:started` und
  `provider-attempt:<logical-operation-id>:<attempt-number>:terminal` unterscheiden
  die beiden Revisionen. Wird ein Append durable geschrieben, aber als fehlgeschlagen
  gemeldet, findet `ArtifactBridge.append()` exakt denselben Schlüssel wieder. Ein
  Crash nach dem bestätigten Startrecord lässt Versuch N offen; Resume beginnt N+1
  genau einmal. Laden, Replay und Projektion besitzen keine Schreibseite.

## Fachlicher Providerattempt-Vertrag

- `provider_attempt` ist ein enger additiver Betriebsrecord, keine fachliche
  Agenten- oder Reviewerentscheidung. Sein Payload enthält nur Provider/Rolle,
  Operation, Work-Unit-ID, logische Operations-ID, gebundenen Fingerprint,
  Messrecord-ID und Input-Digest, positive Versuchszahl, Phase, UTC-Start und
  optionales UTC-Ende, nichtnegative monotone Laufzeit, Ergebnis beziehungsweise
  vorhandene `AgentFailureKind`-Klasse sowie eine kleine optionale Usage-Struktur.
- Revision 1 hat Phase/Status `started`; Ende, Laufzeit, Ergebnis, Fehlerklasse und
  Usage sind `None`. Revision 2 ist terminal (`succeeded` oder `failed`), wiederholt
  alle unveränderlichen Bindungsfelder und besitzt Ende und Laufzeit. Erfolg hat
  keine Fehlerklasse; Fehler hat genau die bereits klassifizierte Fehlerklasse. Es
  gibt keine freie Providerfehlermeldung im Attemptrecord.
- Die Usage-Struktur akzeptiert nur bekannte, nichtnegative Providerzahlen für
  initialen Input, Tool-/Read-Kontext, Cache-Read, Cache-Write/-Creation, Thinking,
  Output, Total, Turns und gemeldete Kosten. Provider-spezifische Schlüssel werden
  zentral auf diese Allowlist abgebildet. Ein nicht geliefertes Feld bleibt
  Python-`None`/JSON-`null` und wird als `unknown` projiziert; eine gemeldete Null
  bleibt von unbekannt unterscheidbar.
- Lokal gemessene `total_chars`, UTF-8-`total_bytes`, Komponentenanzahl, größte
  Komponente und Input-Digest werden ausschließlich als `local_input_*` aus dem
  referenzierten Messrecord angezeigt. Sie werden niemals als Tokens, Turns oder
  Kosten geschätzt. Full- und Compact-Logs serialisieren nicht länger das rohe
  `adapter.metadata`, sondern dieselbe numerische Allowlist.
- Die Projektion reduziert die neueste gültige Revision je physischem Versuch,
  zeigt offene Starts ausdrücklich und weist pro logischer Operation und Run
  Attemptanzahl, bekannte Laufzeitsumme, bekannte Usage-Summen sowie je Feld die
  Zahl bekannter und unbekannter Beiträge aus. Unbekannte Werte werden nicht als
  Null addiert. Reviewer-`ReviewPayload`s bleiben unabhängig und werden weder
  erzeugt noch dedupliziert oder neu geparst.
- Payload, Log und Projektion enthalten keine Prompts, Ausgaben, Secrets,
  Kommandozeilen, Umgebungen, privaten Dateipfade, Komponenteninhalte,
  Conversation-IDs oder rohe Provider-Metadaten. Digests, erlaubte IDs/Enums,
  Zeitpunkte, Größen und tatsächlich gemeldete numerische Usage sind die einzigen
  Betriebsdaten.

### Slice 1 - Providerattempt-Kern, sichere Projektion und Abschluss der Quota-Beobachtungen

#### Integrationspunkte und Umsetzung

- Ergänze in `src/artifact_models.py` Recordtyp, streng validierten Payload samt
  optionaler Usage-Unterstruktur, Python-Union, JSON-Rehydration und Statusbindung;
  ergänze synchron in `schemas/orchestrator-artifact-v1.schema.json` Enum,
  Statusverzweigung und dieselben geschlossenen Payloadregeln.
- Ergänze in `src/artifact_bridge.py` eng typisierte Start-/Terminal-Builder und
  einen Kettenleser, der die stabile logische Operations-ID prüft, den ersten Digest
  bindet und die nächste positive Versuchszahl ableitet. Idempotente Wiederfindung
  bleibt ausschließlich Aufgabe des vorhandenen `append()`.
- Ergänze in `src/artifact_replay.py` Work-Unit-/Messrecordreferenz, unveränderliche
  Bindungsfelder, zusammenhängende Versuchszahlen und exakt eine erlaubte
  Terminalrevision. Historische Ketten ohne `provider_attempt` durchlaufen denselben
  Replay unverändert.
- Ergänze in `src/agent_runtime.py` einen kleinen Lifecycle-Datenträger und Hooks:
  durable `started` nach Budget-/Capability-Preflight direkt vor dem Subprozess,
  terminaler Erfolg mit allowlist-normalisierter Usage oder terminaler Fehler nach
  der bestehenden Klassifikation. Stelle Compact- und Full-Usageausgabe auf die
  sichere Normalisierung um; lokale Messgrößen bleiben separat bezeichnet.
- Verdrahte die Hooks in `src/orchestrator.py` mit aktivem Run, Work Unit,
  Operation, Fingerprint und dem von `_persist_provider_bootstrap()` zurückgegebenen
  Messrecord. Die Bridge prüft vor jedem Start Digest/Fingerprint und persistiert
  beide Revisionen; State-v3 und Reviewpersistenz erhalten kein neues Mirrorfeld.
- Ergänze in `src/artifact_projection.py` die work-unit-gerechte Selektion und die
  deterministische Attemptreduktion/Summenansicht aus dem bereits akzeptierten
  Replay. Offene Starts und unbekannte Usagebeiträge bleiben explizit.
- Ergänze in `README.md` bei `--quota-safety-margin`, dass eine groß konfigurierte
  Marge nach dem sofort geloggten Reset-Ereignis als ein erwarteter Abschnitt ohne
  periodischen Heartbeat wartet und deshalb die heartbeatlose Dauer vergrößert.
  Ergänze in `tests/test_quota_wait.py` den direkten C-01-Negativtest und eine
  große Fake-Clock-Marge, die vor dem Sleep bereits `quota reset reached` sieht,
  exakt bis Resume vorspult und nicht real wartet. Dafür wird
  `src/agent_runtime.py::wait_until_quota_resume()` nicht verändert.

**Exakter Änderungspfad**

- `README.md`
- `schemas/orchestrator-artifact-v1.schema.json`
- `src/agent_runtime.py`
- `src/artifact_bridge.py`
- `src/artifact_models.py`
- `src/artifact_projection.py`
- `src/artifact_replay.py`
- `src/orchestrator.py`
- `tests/test_agent_runtime.py`
- `tests/test_artifact_bridge.py`
- `tests/test_artifact_models.py`
- `tests/test_artifact_projection.py`
- `tests/test_artifact_replay.py`
- `tests/test_orchestrator_runtime.py`
- `tests/test_quota_wait.py`
- `tests/test_structured_artifact_regressions.py`

#### Fokussierte synthetische Akzeptanztests

- `tests/test_artifact_models.py`: Ein vollständiger `provider_attempt` wird vom
  Python-Payload und vom gebündelten JSON-Schema identisch akzeptiert und roundtrippt
  kanonisch. Falscher Status, zusätzliche/fehlende Payloadfelder, negative Usage,
  unbekannter Recordtyp und Python-/Schemaabweichung scheitern fail-closed.
- `tests/test_artifact_replay.py`: Eine historische strukturierte Kette ohne
  Attempts bleibt lesbar und fortsetzbar. `started` plus eine terminale Revision
  ergibt genau einen Versuch; Terminal ohne Start, Revision 3, zwei Terminale,
  Lücken, veränderte Bindungsfelder, falscher/vorwärts referenzierter Messrecord,
  Digest- oder Fingerprintabweichung werden abgewiesen.
- `tests/test_artifact_bridge.py`: Start und Terminal besitzen getrennte stabile
  Idempotency-Keys und fortlaufende Revisionen. Ein nach durablem Write gemeldeter
  Appendfehler findet denselben Start beziehungsweise Abschluss wieder. Ein offener
  Start bleibt erhalten; der nächste Resume leitet Versuch N+1 genau einmal ab.
- `tests/test_agent_runtime.py`: Lifecycle-Hooks laufen erst nach lokalem Budget-
  und Capability-Preflight, aber vor dem simulierten Subprozess, und terminalisieren
  Erfolg oder jede bestehende Fehlerklasse genau einmal. Provider-Usage ist einmal
  vollständig und einmal nicht vorhanden; `None` bleibt `unknown`. Full- und
  Compact-Logs enthalten nur erlaubte Felder und benennen lokale Zeichen, UTF-8-
  Bytes, Komponenten, größte Komponente und Digest niemals als Provider-Tokens oder
  Kosten.
- `tests/test_orchestrator_runtime.py`: Zwei sequenzielle Aufrufe werden allein aus
  den bereits vorhandenen Argumenten Run, Work Unit, Rolle, Operation, Fingerprint
  und Messung derselben logischen ID als Versuch 1 und 2 zugeordnet. Crash nach
  durablem Start und vor Terminal lässt Versuch 1 offen; Resume startet Versuch 2
  einmal. Geänderter Digest oder Fingerprint hält vor einem zweiten Fake-Prozess.
- `tests/test_orchestrator_runtime.py`: Der exakte entfernte Antigravity-
  `/usr/bin/bash`-Instanzfehler terminalisiert Versuch 1 als `NETWORK`; der
  bestehende bounded Retry startet mit identischem Digest Versuch 2 erfolgreich.
  Es entstehen eine logische Reviewoperation, zwei physische Attempts und genau
  eine persistierte Reviewerentscheidung. Ein lokales
  `FileNotFoundError("agy")` bleibt `BINARY`, schreibt keinen physischen Start nach
  gescheitertem Preflight und erzeugt keinen automatischen Retry.
- `tests/test_artifact_projection.py`: `started` plus terminal wird einmal
  reduziert; offener Start bleibt sichtbar. Bekannte Usagewerte werden pro logischer
  Operation und Run deterministisch summiert, unbekannte Beiträge separat gezählt.
  Zweimalige Projektion und zweimaliges Laden ändern weder Versuchszahlen,
  Laufzeiten, Summen noch Recordreihenfolge.
- `tests/test_structured_artifact_regressions.py`: Zweimaliges Laden, Resume und
  Replay lassen die vorhandene Reviewerentscheidung exakt einmal bestehen und
  erzeugen weder neue Attempts noch neue Usage. Prompt-, Secret- und private
  Laufzeitdatei-Sentinels erscheinen weder in Record-JSON, Logausgabe noch
  Projektion; rohe Provider-Metadaten und Conversation-IDs bleiben ausgeschlossen.
- `tests/test_quota_wait.py`: Direkter Aufruf mit
  `resume_at_utc < reset_at_utc` erwartet deterministisch `ValueError` mit
  `cannot precede`. Eine ungewöhnlich große Sicherheitsmarge läuft mit Fake Clock
  ohne reale Wartezeit, sieht `quota reset reached` vor dem einzigen Margen-Sleep,
  keine periodischen Margen-Heartbeats und danach genau `quota wait resumed`; der
  Test bindet damit die eindeutige README-Warnung an das vorhandene Verhalten.
- Agent-fokussierter Lauf:
  `python3 -m pytest tests/test_agent_runtime.py tests/test_artifact_bridge.py tests/test_artifact_models.py tests/test_artifact_projection.py tests/test_artifact_replay.py tests/test_orchestrator_runtime.py tests/test_quota_wait.py tests/test_structured_artifact_regressions.py -q`.

#### Resume-, Retry-, Datenschutz-, Schema- und Rückwärtskompatibilitätsgrenzen

- Neue structured-v1-Ketten verwenden die Recordkette als einzige
  Attemptautorität. State-v3, Checkpoints, Failuredateien und Markdown sind weder
  Zähler noch Reparaturquelle. Historische structured-v1- und legacy-state-v3-Läufe
  ohne Attempts bleiben les- und fortsetzbar; es werden keine historischen Starts
  oder Usagewerte erfunden.
- Ein bestätigter Start wird bei Crash nicht terminalisiert oder wiederverwendet.
  Resume zählt ihn offen und erzeugt den nächsten physischen Versuch. Ein nur
  gemeldeter Appendfehler wird dagegen zuerst über denselben Idempotency-Key
  aufgelöst, bevor ein Prozess starten darf.
- Quota-, Network-, Output- und sonstige Klassifikation, bestehende automatische
  Retrylimits, Reviewerreihenfolge, Reviewrunde, Finding- und Freigabesemantik
  bleiben unverändert. Insbesondere wird kein dritter Versuch und keine neue
  Retryklasse eingeführt.
- Provider-Usage wird nur aus tatsächlich gelieferten numerischen Allowlistfeldern
  übernommen. Fehlende Usage bleibt unbekannt; Zeichen, Bytes, Komponenten oder
  Laufzeit werden nie zu Tokens oder Kosten umgerechnet.
- Ein unbekannter Recordtyp, unbekannte Payloadfelder, widersprüchliche Revisionen,
  fehlende/vorwärts gerichtete Referenzen, Digest-/Fingerprintwechsel und
  Mirror-Divergenz halten fail-closed. Das Schema bleibt offline gebündelt und
  synchron zum Python-Modell.

#### Produktivdateilimit und Stopbedingungen

- Produktiv gegen das Sieben-Dateien-Limit zählen genau
  `schemas/orchestrator-artifact-v1.schema.json`, `src/agent_runtime.py`,
  `src/artifact_bridge.py`, `src/artifact_models.py`,
  `src/artifact_projection.py`, `src/artifact_replay.py` und
  `src/orchestrator.py`. `README.md` und die Testdateien sind Dokumentations- und
  Teständerungen, keine zusätzlichen produktiven Änderungseinheiten.
- Stellt sich vor der ersten Produktänderung heraus, dass Modell und Schema nicht in
  diesen beiden gebundenen Pfaden synchron geschlossen werden können, stoppt der
  Slice mit nicht-bereiter Übergabe; kein Pfad wird still ergänzt.
- Ist die logische Operations-ID oder nächste Versuchszahl entgegen dem belegten
  Aufrufpfad nicht aus Run, Work Unit, Rolle, Operation, Fingerprint, Messrecord und
  autoritativer Kette vor dem Start ableitbar, stoppt der Slice. Insbesondere werden
  `src/workflow.py`, `src/workflow_state.py`, `src/state_io.py` oder
  `src/final_review_preflight.py` nicht informell ergänzt.
- Benötigt der persistente Lebenszyklus eine Änderung an `src/artifact_store.py`,
  wird nicht auf acht Produktivdateien erweitert. Die read-only Komfortprojektion
  in `src/artifact_projection.py` wird dann aus diesem Release entfernt und als
  ausdrücklich neuer Folgeauftrag **Release 1.1C3 – Providerattempt-Projektion**
  neu geplant; Modell, Schema, Persistenz und Replay haben Vorrang.
- Erfordert die reine Quota-Beobachtungsabnahme entgegen dem Repositorybefund eine
  Produktcodeänderung an `wait_until_quota_resume()`, wird sie nicht in die
  Providerattempt-Implementierung eingeschoben. Der Lauf stoppt und verlangt einen
  separat geschnittenen minimalen Quota-Abschlussslice.
- Ist der entfernte Antigravity-Instanzfehler nicht mehr eindeutig vom lokalen
  Binärfehler unterscheidbar, wird kein Retry ergänzt oder umklassifiziert. Ist
  Provider-Usage nicht zuverlässig numerisch verfügbar, bleibt das jeweilige Feld
  `None`/`unknown`; es gibt keine Schätzung und keinen Rohmetadaten-Fallback.

## Nichtziele

- Keine Neuimplementierung des mit `fac92b0` abgeschlossenen Quota-Slices und keine
  Änderung seiner Defaults, Grenzarithmetik oder Retrypolitik.
- Keine neuen Retryklassen oder -limits, kein dritter automatischer Versuch, kein
  Warm-up, keine Session-Reuse und kein Provider-Neustartmechanismus.
- Keine allgemeine Logging-, Abrechnungs-, Metrik- oder Observability-Plattform und
  keine Schätzung von Tokens oder Kosten aus lokalen Größen.
- Keine Workflow-Finalisierung, Inbox-/Outbox- oder Merge-Automatisierung, keine
  Reviewer- oder Approvalsemantikänderung und keine neuen Record-first-
  State-Transitionen außerhalb des Attempt-Lebenszyklus.
- Kein natives Agenten-JSON, keine Ablösung der Textmarker und keine Speicherung von
  Prompts, Secrets, privaten Dateiinhalten oder rohen Providerhüllen.

## Orchestrator-Prüfprotokoll

### Review-Feedback von Claude

<!-- audit:claude-review:begin -->
### Ereignis 2: Runde 1

- Reviewer: `claude`
- Freigabe: `YES`
- Validierungsbindung: `plan-validation-8831e17333b2`
- Testdateien: keine
- Prüfdimensionen: plan completeness, file-limit arithmetic, Slice-01 observation closure, lifecycle/idempotency/resume invariants, digest/fingerprint fail-closed paths, privacy/usage allowlist separation, backward-compatibility of legacy chains, stop-condition coverage for over-budget scenarios
- Größtes Restrisiko: largest residual risk is the uncited assumption that Slice-01 product code already satisfies both retained observations without any product change
- Realistische Bruchbedingung: breaks if implementation discovers &#96;wait_until_quota_resume()&#96; (or its caller) lacks the ordering guard or heartbeat suppression, forcing an undeclared product-code edit inside this Slice instead of the mandated separate minimal Slice
- Eigene Findings: `C-01`

<!-- artifact-records:claude-review:begin -->
Semantischer Record-Digest: `b51db1c653498ce414e09efb9e1e2c13fcba6620a483672135a44f9589ea3f50`

- 6. `ar1-4c2f6f5bf3968c53002490f4ae0635a69d209a22164237fb5a98386231655676`: `approved`; Work-Unit `1`; Findings `C-01`; Fingerprint `8831e17333b20a8bb7a32b1460775263021c4ba00c1530770f56c7d2011140f8`
<!-- artifact-records:claude-review:end -->
<!-- audit:claude-review:end -->

### Review-Feedback von Antigravity

<!-- audit:antigravity-review:begin -->
### Ereignis 3: Runde 1

- Reviewer: `antigravity`
- Freigabe: `YES`
- Validierungsbindung: `plan-validation-8831e17333b2`
- Testdateien: keine
- Prüfdimensionen: plan completeness, scope discipline, 7-file productive limit, schema-model parity, attempt lifecycle invariants, idempotency/replay recovery, digest/fingerprint fail-closed boundaries, privacy sentinels, backward compatibility of historical chains, Slice-01 observation closure
- Größtes Restrisiko: tight coupling between orchestrator lifecycle hooks and agent_runtime subprocess execution leading to missing terminalization on unexpected process exit
- Realistische Bruchbedingung: breaks if agent_runtime encounters an unhandled exception between Popen and classification that bypasses the terminal lifecycle hook and leaves an orphaned started attempt without proper diagnostic classification
- Eigene Findings: keine

<!-- artifact-records:antigravity-review:begin -->
Semantischer Record-Digest: `b51db1c653498ce414e09efb9e1e2c13fcba6620a483672135a44f9589ea3f50`

- 9. `ar1-97b045ff9f8b354e86248edc0d9fca911127119522b11f7502fc2dd5c3f21ed5`: `approved`; Work-Unit `1`; Findings `C-01`; Fingerprint `8831e17333b20a8bb7a32b1460775263021c4ba00c1530770f56c7d2011140f8`
<!-- artifact-records:antigravity-review:end -->
<!-- audit:antigravity-review:end -->

### Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
Noch keine strukturierten Codex-Antworten.

<!-- artifact-records:codex-responses:begin -->
Semantischer Record-Digest: `b51db1c653498ce414e09efb9e1e2c13fcba6620a483672135a44f9589ea3f50`

Keine Codex-Findingantworten.
<!-- artifact-records:codex-responses:end -->
<!-- audit:codex-responses:end -->

### Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### Ereignis 1: `plan-validation-8831e17333b2`

- Diff-Fingerprint: `8831e17333b20a8bb7a32b1460775263021c4ba00c1530770f56c7d2011140f8`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: internal plan contract passed
- Ausgabedigest: `03d6b02982e1ed4c503e710e3af1515661ec9fcb5d78916fc41c9353bf1f322c`

| Matrixbefehl | Status | Exitcode | Kompaktausgabe |
|---|---|---:|---|
| internal:work-plan-contract | PASS | 0 | slices=1; planned_paths=1; changed_paths=1; future_slices=1; work_plan=docs/internal/release-1-1c2-providerattempt-record-und-schema-arbeitsplan.md |

<!-- artifact-records:validation-attestation:begin -->
Semantischer Record-Digest: `b51db1c653498ce414e09efb9e1e2c13fcba6620a483672135a44f9589ea3f50`

- 2. `ar1-1b4a6031962b3516f7c9a9a550585b1110c9b69c869d884ce32a4b29e3d7fb06`: Providerinput `codex/codex_plan` = `allowed`; Zeichen `22278/4000000`, Bytes `22380/16000000`; Input `bd649944c6254e889acbe6a5ab9b4942688bd631a66b0232899d3aa231bc4bde`, Policy `603e75566ee1fad99cd271d52bc8c2814387f55bf526699188eead5156cd325e`, Übergang `908a915c0d7ac7a53ded8b0b02c3b1f24289bbe2337bb9c725ee04f830d6cb26`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, größte Komponente `stdin_prompt`; Komponenten `stdin_prompt=22278/22380`
- 4. `ar1-67d38cef733e0b8d4b146e63403435644c3a848d550b7777c033ebc0ba35a23d`: Attestierung durch `orchestrator`; Fingerprint `8831e17333b20a8bb7a32b1460775263021c4ba00c1530770f56c7d2011140f8`
  - `pass` / Exit `0` / Output `03d6b02982e1ed4c503e710e3af1515661ec9fcb5d78916fc41c9353bf1f322c`: `argv` [`internal:work-plan-contract`]
- 5. `ar1-ca106ce99e37df4fdf9c14281f7303450ac33b5eae70f0c0e000ba5b233064ee`: Providerinput `claude/claude_plan_review` = `allowed`; Zeichen `52861/4000000`, Bytes `53161/16000000`; Input `cd013d4d6700e79773f43124f1ef799628c26d0ad4bfcdf10c4cf8c9e8b40ffb`, Policy `603e75566ee1fad99cd271d52bc8c2814387f55bf526699188eead5156cd325e`, Übergang `ae7fea95b1feffa6dddc01849dabb23e4d70118f6473d8a12846fba64ff8ebfb`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, größte Komponente `packet_chunk_001`; Komponenten `packet_chunk_001=23994/24135, packet_chunk_002=23774/23933, packet_chunk_003=3383/3383, packet_manifest=562/562, system_policy=660/660, response_schema=146/146, start_directive=342/342`
- 8. `ar1-109506ddff684578717f972b484ac2620847e55a62c9c5fbe79306d880aaef89`: Providerinput `antigravity/antigravity_plan_review` = `allowed`; Zeichen `56883/4000000`, Bytes `57185/16000000`; Input `198f3dd444e50e69ae763546b6f2fba92efd9a1ff5997719aa7a8dc27b6285d5`, Policy `603e75566ee1fad99cd271d52bc8c2814387f55bf526699188eead5156cd325e`, Übergang `84ec4b31d39a5e899e1c0c591c8f392239f4420db70c2b6b20eca04861dfe666`; technisches Limit `None/None` (Quelle `unknown`); Verletzung `none`, Überhang `0/0`, größte Komponente `prompt_file`; Komponenten `prompt_file=56224/56526, response_schema=146/146, start_directive=513/513`
<!-- artifact-records:validation-attestation:end -->
<!-- audit:validation-attestation:end -->

### Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
- Teständerungsfreigabe: nicht erfasst.
- Pre-Mortems:
  - Ereignis 2: Most likely three-month failure cause is a mismatch between the assumed-existing quota guard/heartbeat behavior and actual code, discovered only during implementation, tempting a scope-creep edit to &#96;wait_until_quota_resume()&#96; inside the provider-attempt Slice instead of stopping for a separate minimal Slice as the plan requires.
  - Ereignis 3: A new provider adapter is added with proprietary metadata fields that bypass the static usage allowlist, causing usage to be recorded as unknown or schema validation to reject records if mapping tables are not kept in sync.

<!-- artifact-records:test-approval-premortem:begin -->
Semantischer Record-Digest: `b51db1c653498ce414e09efb9e1e2c13fcba6620a483672135a44f9589ea3f50`

Keine strukturierten Gates.
<!-- artifact-records:test-approval-premortem:end -->
<!-- audit:test-approval-premortem:end -->

### Findings-Lebenszyklus

<!-- audit:findings:begin -->
### `C-01` — `OPEN`

- Quelle: `claude`; Runde 1
- Klasse: `OBSERVATION`
- Finding: The plan claims current product code already implements the resume-before-reset ordering guard and the quota-margin heartbeat suppression, but cites no specific function/line as proof; if either assumption is false, the "test-only" Slice-01-closure sub-scope would need product code after all.
- Akzeptanztest: Before writing the fake-clock C-01/C-02 tests, the implementer names the exact existing guard (file + function) that raises on &#96;resume_at_utc &lt; reset_at_utc&#96; and the exact code path that suppresses periodic heartbeats during a large safety margin; if no such guard exists, the Slice stops per its own documented stop condition rather than adding an undocumented code change.
- Statusbegründung: –

<!-- artifact-records:findings:begin -->
Semantischer Record-Digest: `b51db1c653498ce414e09efb9e1e2c13fcba6620a483672135a44f9589ea3f50`

- 7. `ar1-7ff27bdeaa95cff9cae2e777771068391f1f173ffd6c17cd8e7057628f26a2ca`: `C-01` `opened` durch `claude`; `OBSERVATION` / `open` — The plan claims current product code already implements the resume-before-reset ordering guard and the quota-margin heartbeat suppression, but cites no specific function/line as proof; if either assumption is false, the "test-only" Slice-01-closure sub-scope would need product code after all.
<!-- artifact-records:findings:end -->
<!-- audit:findings:end -->

### Entscheidungstabelle

<!-- audit:decision-table:begin -->
| ID | Quelle | Finding | Klasse | Entscheidung | Umsetzung |
|---|---|---|---|---|---|
| C-01 | claude | The plan claims current product code already implements the resume-before-reset ordering guard and the quota-margin heartbeat suppression, but cites no specific function/line as proof; if either assumption is false, the "test-only" Slice-01-closure sub-scope would need product code after all. | OBSERVATION | offen | offen |

<!-- artifact-records:decision-table:begin -->
Semantischer Record-Digest: `b51db1c653498ce414e09efb9e1e2c13fcba6620a483672135a44f9589ea3f50`

| Seq | Record | Typ | Status | Logische ID | Revision | Fingerprint |
|---:|---|---|---|---|---:|---|
| 1 | `ar1-ce53312cbd0f77147dbf14654bd901400d75ccf2e1e0b508855cb2d6a3cfcbbd` | `task` | `accepted` | `task-contract` | 1 | `contract:683d66b7e0a8cff8ecb2658094871d83125c73a9d9b653b8112bd7535928aad7` |
| 2 | `ar1-1b4a6031962b3516f7c9a9a550585b1110c9b69c869d884ce32a4b29e3d7fb06` | `provider_input_measurement` | `measured` | `provider-input-1-codex_plan` | 1 | `implementation:683d66b7e0a8cff8ecb2658094871d83125c73a9d9b653b8112bd7535928aad7` |
| 3 | `ar1-8bb564c1e1d940b2083ec2ed33f61e82d7d4df3ee19b9dbccfadd019b72ec992` | `agent_result` | `ready` | `agent-1-codex_plan-1` | 1 | `contract:683d66b7e0a8cff8ecb2658094871d83125c73a9d9b653b8112bd7535928aad7` |
| 4 | `ar1-67d38cef733e0b8d4b146e63403435644c3a848d550b7777c033ebc0ba35a23d` | `validation_attestation` | `attested` | `plan-validation-8831e17333b2` | 1 | `implementation:8831e17333b20a8bb7a32b1460775263021c4ba00c1530770f56c7d2011140f8` |
| 5 | `ar1-ca106ce99e37df4fdf9c14281f7303450ac33b5eae70f0c0e000ba5b233064ee` | `provider_input_measurement` | `measured` | `provider-input-1-claude_plan_review` | 1 | `implementation:683d66b7e0a8cff8ecb2658094871d83125c73a9d9b653b8112bd7535928aad7` |
| 6 | `ar1-4c2f6f5bf3968c53002490f4ae0635a69d209a22164237fb5a98386231655676` | `review` | `decided` | `review-claude-1-1` | 1 | `implementation:8831e17333b20a8bb7a32b1460775263021c4ba00c1530770f56c7d2011140f8` |
| 7 | `ar1-7ff27bdeaa95cff9cae2e777771068391f1f173ffd6c17cd8e7057628f26a2ca` | `finding_transition` | `recorded` | `finding-C-01` | 1 | `implementation:8831e17333b20a8bb7a32b1460775263021c4ba00c1530770f56c7d2011140f8` |
| 8 | `ar1-109506ddff684578717f972b484ac2620847e55a62c9c5fbe79306d880aaef89` | `provider_input_measurement` | `measured` | `provider-input-1-antigravity_plan_review` | 1 | `implementation:683d66b7e0a8cff8ecb2658094871d83125c73a9d9b653b8112bd7535928aad7` |
| 9 | `ar1-97b045ff9f8b354e86248edc0d9fca911127119522b11f7502fc2dd5c3f21ed5` | `review` | `decided` | `review-antigravity-1-1` | 1 | `implementation:8831e17333b20a8bb7a32b1460775263021c4ba00c1530770f56c7d2011140f8` |
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
Semantischer Record-Digest: `b51db1c653498ce414e09efb9e1e2c13fcba6620a483672135a44f9589ea3f50`

Keine Work-Unit- oder Binding-Records.
<!-- artifact-records:approval-status:end -->
<!-- audit:approval-status:end -->
