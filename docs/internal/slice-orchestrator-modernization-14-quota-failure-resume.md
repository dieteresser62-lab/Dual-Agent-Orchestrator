# Slice 14: Quota-Wartezustand, Instanzausfälle und Resume

**Status:** Implementierung und Abschlussmatrix grün; Reviewverlauf, Freigaben und Commitstatus stehen ausschließlich in den verwalteten Auditabschnitten
**Feature-Branch:** `feature/orchestrator-modernization`
**Branch-Basis des Slice:** `ebb3ee7`
**GitHub-Status:** nur lokal; kein Upstream; Push und Merge sind nicht Bestandteil dieses Slice
**Arbeitsplan:** [orchestrator-modernization-work-plan.md](orchestrator-modernization-work-plan.md)

## Ziel des Slice

Die additive v3-Engine unterscheidet Quota, fehlende Binary, Timeout, Authentifizierung, Netzwerk, Berechtigung, Prozess- und Ausgabefehler. Jeder fehlgeschlagene Rollenaufruf wird mit stabiler Invocation-ID am unveränderten Schritt persistiert. Eine eindeutig terminierte Quota darf innerhalb einer begrenzten Politik warten und exakt dieselbe Rolle fortsetzen; alle anderen Fälle bleiben manuell resumefähig.

## Akzeptanzkriterien

- Absolute UTC-/Offset-Zeitstempel, eindeutig bezogene relative Zeiten und strukturierte Providerfelder werden rollenbezogen und UTC-normalisiert geparst; unbekannte oder mehrdeutige Angaben aktivieren keine Automatik.
- Quota-State enthält Rolle, Slice, Work Unit, Schritt, Providertext, Empfangszeit, Parseweg, Quellzeitzone, Reset-/Resumezeit, Sicherheitszuschlag, Fortsetzungszähler, Idempotenzschlüssel und optional den wartenden Diff-Fingerprint.
- Automatisches Warten ist unterbrechbar, schläft höchstens bis zum nächsten Heartbeat und meldet Rolle, Task, Work Unit, lokalen/UTC-Fortsetzungszeitpunkt und Restzeit.
- Vor dem Wiederaufruf werden Branch-/Policybedingungen und der beim Ausfall gebundene Slice-Diff erneut geprüft. Nur dieselbe Rolle im selben Schritt wird fortgesetzt.
- Standard ist eine automatische Fortsetzung, 60 Sekunden Sicherheitszuschlag und höchstens 24 Stunden Wartezeit. CLI schlägt Umgebung und Standard; `--no-quota-auto-resume` deaktiviert Automatik.
- Nicht terminierbare oder ausgeschöpfte Quota bleibt mit Exitcode 2, sonstiger Instanzausfall mit Exitcode 3 und Policy-/Iterationshalt mit Exitcode 4 resumefähig.
- Andere Instanzausfälle werden nicht intern wiederholt. Teil-, Leer- und Parsefehler werden nicht als Erfolg gespeichert; es gibt niemals einen Ersatzagenten.
- Die vierte Reviewrückgabe bleibt der vorhandene, arbeitsbaumneutrale Iterationshalt mit `awaiting_user_decision`.
- Der additive v3-Pfad bleibt hinter dem unveränderten v2-Default; Watch-Queue-Integration folgt planmäßig in Slice 17, gemeinsamer CLI-Cutover in Slice 18.

## Scope und Nicht-Scope

### Scope

- Typisierte Quota-/Instanzausfallklassifikation und Resetparser in `src/agent_runtime.py`.
- Unveränderte Providerdiagnose aus strukturierten Claude-/Antigravity-Hüllen in `src/agent_adapters.py`.
- Persistierte Invocation-Failure-Records und Halt-/Resumeübergänge in `src/workflow_state.py`.
- Exaktrolliges Warten, Revalidierung und Exitcodeprojektion in `src/workflow.py`.
- CLI-/Umgebungsauflösung der Wartepolitik und v2-Exitcodeklassifikation.
- Deterministische Fake-Clock-, State-, Rollen-, CLI- und Runtime-Tests.

### Nicht-Scope

- Keine skriptbaren Dry-Run-Szenarien; sie gehören zu Slice 15.
- Keine Watch-Queue-/Poison-Pill-Integration; sie gehört zu Slice 17.
- Kein Default-Cutover auf v3 und keine Aktualisierung der finalen Nutzer-/Instruktionsdokumentation; beides gehört zu Slice 18.
- Kein externer Scheduler und kein unsichtbarer Neustart nach Prozessende.

## Diff-Risiko inklusive Branch- und Statuscheck

**Branch-Check:** `feature/orchestrator-modernization`; entspricht dem Arbeitsplan.

**Status vor Slice-Beginn:** sauber nach lokalem Slice-13-Commit `ebb3ee7`. `Inbox/.lock` war nicht vorhanden.

**Änderungstiefe:** hoch in Runtimeklassifikation und persistierten Zustandsinvarianten, mittel in v3-Schrittfortsetzung und CLI-Policy, niedrig im weiterhin aktiven v2-Normalpfad.

**Gefährdete bestehende Funktionen:** State-v3-Roundtrip/Migration, Reviewreihenfolge, Format-Reparatur, Lazy Capability Preflight und v2-Quota-Halt.

**Nicht anfassen:** `.orchestrator/state.json`, Checkpoints, Dry-Run-Szenariomatrix, Watch-Queue-Semantik, Push/Merge und Default-Cutover.

**Rollback-Strategie:** additive Failure-Records, Parser und Workflowübergänge per Gegenpatch entfernen; kein Reset, Checkout oder History-Rewrite.

## Geplante Tests

- Absolute UTC-/Offset-, relative Sekunden/Minuten-, strukturierte, unbekannte und mehrdeutige Resetangaben je Rolle.
- Fehlende Binary, Timeout, Auth, Netzwerk/Egress/Loopback, Runtime-Berechtigung, Prozessfehler und ungültige/leere Ausgabe.
- Automatisches Warten mit Fake Clock, Sicherheitszuschlag, Heartbeatintervall, bereits erreichtem Reset und kontrolliertem Abbruch.
- Deaktivierte, zu lange, nicht terminierte und nach einer Fortsetzung erneut auftretende Quota.
- Codex-, Claude- und Antigravity-Halt/Resume ohne Rollenvertretung oder Wiederholung vorheriger Rollen.
- Geänderter Fingerprint während des Wartens hält vor Wiederaufruf.
- State-Roundtrip/Migration, vierte Codex-Rückgabe, CLI-Präzedenz, Vollsuite, Compile, v2-Dry-Run, Diffcheck und Dokumentvalidator.

## Durchgeführte Änderungen

- `QuotaReset`, `AgentInvocationError`, `AgentFailureKind` und `QuotaWaitPolicy` bilden Resetbeweis, Fehlerklasse und Wartegrenzen typisiert ab. Der Parser bevorzugt eindeutige strukturierte Felder, akzeptiert timezone-behaftete ISO-Zeitstempel oder klar bezogene relative Angaben und fällt bei mehreren verschiedenen Kandidaten auf manuell zurück.
- `run_agent_checked` wiederholt technische Instanzausfälle nicht mehr. Jeder Fehlschlag erhält eine neue Invocation-ID und eine private JSON-Diagnose mit unverändertem Providertext; reine Contract-Formatkorrektur bleibt als semantisch unveränderte Reparatur zulässig.
- Adapterfehler transportieren strukturierte Providerhüllen weiter, sodass beispielsweise `retry_after_seconds` aus Claude- oder Antigravity-Fehlerantworten auswertbar bleibt. Leer-/JSON-/`is_error`-/Berechtigungs- und Prozessfehler werden getrennt klassifiziert.
- `InvocationFailureRecord` persistiert die vollständige Rollen-, Zeit-, Schritt-, Retry- und Diffbindung. `waiting_for_quota` sowie `awaiting_resume` sind echte Work-Unit-/Slice-/Gatezustände; der Roundtrip migriert ältere v3-Stände konservativ mit leerer Failure-Historie.
- Die v3-Engine checkpointet vor jedem Warten, emittiert Fake-Clock-testbare Heartbeats, revalidiert Branch, Scope und Fingerprint und ruft anschließend dieselbe Closure für dieselbe Rolle auf. Eine zweite Quota am selben Idempotenzschlüssel überschreitet den Standardzähler und bleibt manuell stehen.
- `WorkflowRunResult` projiziert Quota als 2, Instanzausfall als 3 und Nutzer-/Policyhalt als 4. Der v2-Übergang klassifiziert neue nicht-Quota-Invocationfehler ebenfalls als Exitcode 3; seine bisherige Quota-Freeze-Kompatibilität bleibt bestehen.
- CLI und Umgebung lösen Automatik, 60-Sekunden-Zuschlag, 24-Stunden-Grenze, eine automatische Fortsetzung und 30-Sekunden-Heartbeat mit Präzedenz CLI → Umgebung → Standard auf.

## Ausgeführte Validierung mit Ergebnis

194 fokussierte Quota-, Runtime-, State-, Workflow-, CLI- und v2-Kompatibilitätstests sowie alle 458 Tests der Vollsuite sind bestanden. Compile der sechs produktiven Python-Dateien, aktiver v2-Dry-Run, Diffcheck und Slice-Dokumentvalidator sind auf demselben semantischen Stand grün; Befehle, Digests und Fingerprint werden in der verwalteten Abschlussattestierung gebunden.

## Abweichungen vom Plan

`src/agent_adapters.py`, `src/orchestrator.py`, `tests/test_cli.py`, `tests/test_workflow_state.py` und das neue `tests/test_quota_wait.py` kamen gegenüber der voraussichtlichen Liste hinzu. Adapter müssen strukturierte Providerfelder verlustfrei liefern; der aktive v2-Kompatibilitätspfad benötigt den definierten Exitcode 3; State- und CLI-Verträge brauchen eigene Negativtests. `src/inbox_watcher.py` bleibt unverändert, weil dessen Queue-/Poison-Pill-Semantik ausdrücklich Slice 17 zugeordnet ist. Der produktive Scope umfasst sechs Source-Dateien und bleibt unter der Grenze von zehn Änderungseinheiten.

## Offene Risiken

- Der additive v3-Workflow nutzt die Wartepolitik vollständig; der aktive v2-Default friert Quota bis zum Cutover weiterhin manuell ein.
- Provider können ihre Fehlerhüllen ändern. Unbekannte oder widersprüchliche Resetangaben aktivieren bewusst niemals automatisches Warten.
- Eine offene Terminal-/WSL-Instanz ist für automatisches Warten erforderlich; nach Prozessende bleibt nur bewusstes Resume.

## Review-Feedback von Claude

<!-- audit:claude-review:begin -->
Claude (Sonnet/High) prüfte in einem einzigen Aufruf den vollständigen 14-Pfade-Slice-Diff gegen `ebb3ee7` und die Attestierung `slice14-validation-9cde1408`. Claude erteilte die Slice-Freigabe `YES`; es gibt keine Blocker.

Geprüft wurden Resetparser und Fehlerklassifikation, State-Invarianten und Migration, unterbrechbares Warten, exaktrolliges Resume samt Idempotenzzähler, Revalidierung vor internem und externem Resume sowie CLI-Präzedenz. Claude bestätigte insbesondere, dass eine zweite Quota desselben Schritts manuell mit Exitcode 2 hält und keine nachfolgende Rolle vorgezogen wird.

Claude erfasste zwei nicht blockierende Beobachtungen:

- `C-01`: Die v2-Exitcodeprojektion für Instanzausfälle ist im Slice-Diff nicht durch einen neuen dedizierten Orchestrator-Test belegt; die unveränderte Kompatibilitätssuite und Vollsuite sind grün.
- `C-02`: Gesperrter Loopback und fehlender Provider-Egress teilen die Klasse `network` und bleiben nur durch den unveränderten Providertext unterscheidbar; explizite Wortlauttests fehlen.

Als Pre-Mortem nennt Claude eine künftige Umbenennung von Providerfeldern oder Fehlermeldungen, die eine Quota fail-safe als generischen Instanzausfall klassifiziert und dadurch die automatische Fortsetzung verliert.

Der Modelloutput enthielt Verdict, Evidenz, Findings und Pre-Mortem vollständig. Der lokale Vorfilter verwendete irrtümlich die alte v2-Flag-Syntax und meldete deshalb fehlende Marker; ohne zweiten Modellaufruf wurden ausschließlich die bereits gebundene `TEST_FILES_TOUCHED`-Zeile ergänzt und die von Claude gelieferten IDs `S14-01`/`S14-02` deterministisch auf die im v3-Contract vorgeschriebenen IDs `C-01`/`C-02` normalisiert. Danach bestand der vollständige v3-Contractvalidator.

Runde 2 erhielt ausschließlich die A-01-/A-02-Korrekturausschnitte und die neue Attestierung `slice14-validation-c695752f`. Claude bestätigte beide Korrekturen, hielt C-01/C-02 als nicht blockierende Beobachtungen offen und erteilte erneut `YES`. Die erneut fehlende gebundene `TEST_FILES_TOUCHED`-Zeile wurde ohne weiteren Modellaufruf deterministisch ergänzt; danach bestand der v3-Contractvalidator.

Claude erfasste zusätzlich `C-03` als nicht blockierende Beobachtung: Ein als String geliefertes Unix-Epoch-Feld wird fail-safe ignoriert, weil Strings ausschließlich als ISO-8601 geparst werden. Native numerische Unix-Sekunden und ISO-Strings sind abgedeckt; unbekannte Formate aktivieren bewusst keine Automatik.
<!-- audit:claude-review:end -->

## Review-Feedback von Antigravity

<!-- audit:antigravity-review:begin -->
Antigravity (Gemini 3.1 Pro High/High) prüfte in Runde 1 den vollständigen, von Claude freigegebenen 14-Pfade-Diff und verweigerte die Freigabe mit einem Blocker und einer Beobachtung:

- `A-01` (BLOCKER): Eine Ausnahme aus `collect_changes` während der Revalidierung nach dem Warten konnte den laufenden Prozess verlassen, statt einen persistierten Policy-Halt zu erzeugen.
- `A-02` (OBSERVATION): Strukturierte absolute Resetfelder akzeptierten keine Unix-Zeitstempel.

Antigravity wiederholte zusätzlich Claudes `C-01`-/`C-02`-Statuszeilen. Diese fremden Marker wurden zur lokalen Contractauswertung entfernt; das negative Verdict und die Antigravity-Findings blieben unverändert. Es gab keinen zweiten Modellaufruf für die Normalisierung.

Codex fing den Revalidierungsfehler fail-closed ab und ergänzte den exakten Git-Lock-Akzeptanztest. Numerische, endliche und nicht negative Unix-Sekunden werden nun UTC-normalisiert; ein eigener Parser-Akzeptanztest bindet den Fall. Runde 2 erhält den vollständigen korrigierten finalen Diff erst nach einem gezielten Claude-Korrekturreview.

Antigravity prüfte in Runde 2 den vollständigen korrigierten 14-Pfade-Diff gegen `ebb3ee7` mit der Attestierung `slice14-validation-c695752f`. A-01 und A-02 wurden ausdrücklich geschlossen; Antigravity erteilte beim einzigen finalen Aufruf die Freigabe `YES` und erfasste keine neuen Findings.

Geprüft wurden die Exception-zu-Policy-Halt-Konvertierung, numerische Resetzeitnormalisierung, State-Invarianten beim Resume, rollen- und schrittgebundene Idempotenz sowie fail-safe Rückfälle. Als Pre-Mortem nennt Antigravity ebenfalls künftige Änderungen der Providerfehlerhüllen, die sicher auf manuelles Resume zurückfallen, aber unbeaufsichtigte Läufe operativ ausbremsen können.
<!-- audit:antigravity-review:end -->

## Review-Antworten von Codex

<!-- audit:codex-responses:begin -->
- `C-01` als nicht blockierendes Restrisiko dokumentiert: Der aktive v2-Pfad projiziert den neuen typisierten Instanzausfall auf Exitcode 3; `tests/test_orchestrator_quota.py` und die vollständige Suite bleiben grün. Der v3-Cutover erfolgt erst in Slice 18.
- `C-02` als nicht blockierendes Restrisiko dokumentiert: Die fachliche Klasse bleibt bewusst `network`; der vollständige Providertext bleibt persistiert und unterscheidet Loopback, DNS und Egress diagnostisch. Unbekannte Texte lösen keine Quota-Automatik aus.
- `C-03` als nicht blockierendes Restrisiko dokumentiert: Stringfelder bleiben auf eindeutig timezone-behaftete ISO-8601-Werte begrenzt; numerische Epochwerte benötigen einen numerischen Providerfeldtyp. Ein abweichender Typ fällt auf den manuellen Quota-Pfad zurück.
- `A-01` angenommen: Ausnahmen der Git-Differfassung werden bei der Post-Wait-Revalidierung in einen checkpointbaren `STOP_REQUEST` mit Exitcode 4 überführt; der fehlgeschlagene Rollenaufruf wird nicht wiederholt.
- `A-02` angenommen: Strukturierte absolute Resetfelder akzeptieren neben ISO-Zeitstempeln endliche, nicht negative Unix-Sekunden und normalisieren sie nach UTC.
<!-- audit:codex-responses:end -->

## Validierungsattestierung

<!-- audit:validation-attestation:begin -->
### `slice14-validation-9cde1408`

- Diff-Fingerprint: `9cde1408b4bd5a22bff8e8ff1cb541d9210676ee96c7d828db948ae90eeb04e6`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: 456 passed; 192 focused passed; compile PASS; active v2 dry-run PASS; diff check PASS; slice document PASS
- Ausgabedigest: `34e12c52659ef48f0e4268eb6c4acb9ff85fd80f11d0743fd8f6d5539ab5e49c`

| Matrixbefehl | Status | Exitcode |
|---|---|---:|
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -q -p no:cacheprovider` | PASS | 0 |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_agent_runtime.py tests/test_cli.py tests/test_orchestrator_quota.py tests/test_quota_wait.py tests/test_workflow.py tests/test_workflow_state.py -q -p no:cacheprovider` | PASS | 0 |
| `python3 -m py_compile src/agent_adapters.py src/agent_runtime.py src/cli.py src/orchestrator.py src/workflow.py src/workflow_state.py` | PASS | 0 |
| `./run_task --dry-run --skip-git-check --test-command '' --agent-output none` | PASS | 0 |
| `git diff --check` | PASS | 0 |
| Slice-Dokumentvalidator | PASS | 0 |

### `slice14-validation-c695752f`

- Diff-Fingerprint: `c695752fde5e02bf69f3607db9cdb97ddd17a8f4f92e3d7a1108e72451ed3295`
- Status: `PASS`
- Vollständig: `YES`
- Kurzresultat: 458 passed; 194 focused passed; A-01 acceptance PASS; A-02 acceptance PASS; compile PASS; active v2 dry-run PASS; diff check PASS; slice document PASS
- Ausgabedigest: `8bd744c37f907a1f1a41d221e08e95fd47605c18b455d879d5bd4fa2152296b1`

| Matrixbefehl | Status | Exitcode |
|---|---|---:|
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -q -p no:cacheprovider` | PASS | 0 |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_agent_runtime.py tests/test_cli.py tests/test_orchestrator_quota.py tests/test_quota_wait.py tests/test_workflow.py tests/test_workflow_state.py -q -p no:cacheprovider` | PASS | 0 |
| `python3 -m py_compile src/agent_adapters.py src/agent_runtime.py src/cli.py src/orchestrator.py src/workflow.py src/workflow_state.py` | PASS | 0 |
| `./run_task --dry-run --skip-git-check --test-command '' --agent-output none` | PASS | 0 |
| `git diff --check` | PASS | 0 |
| Slice-Dokumentvalidator | PASS | 0 |
| `python3 -m pytest tests/test_workflow.py -q -k collect_changes_exception_during_resume` | PASS | 0 |
| `python3 -m pytest tests/test_quota_wait.py -q -k unix_timestamp` | PASS | 0 |
<!-- audit:validation-attestation:end -->

## Testfreigabe und Pre-Mortem

<!-- audit:test-approval-premortem:begin -->
Teständerungen sind durch die arbeitsplanweite Revision-8-Ausnahme autorisiert.

Vorgesehene Testpfade: `tests/test_agent_runtime.py`, `tests/test_cli.py`, `tests/test_orchestrator_quota.py`, `tests/test_quota_wait.py`, `tests/test_workflow.py`, `tests/test_workflow_state.py`.
<!-- audit:test-approval-premortem:end -->

## Findings-Lebenszyklus

<!-- audit:findings:begin -->
| ID | Reporter | Klasse | Status | Kurzbegründung |
|---|---|---|---|---|
| C-01 | claude | OBSERVATION | OPEN, nicht blockierend | v2-Exitcodeänderung ohne neuen dedizierten Orchestrator-Test im Slice-Diff |
| C-02 | claude | OBSERVATION | OPEN, nicht blockierend | Loopback/Egress teilen `network` und werden über Providertext unterschieden |
| C-03 | claude | OBSERVATION | OPEN, nicht blockierend | Stringifizierte Unix-Zeit wird fail-safe nicht als absoluter Reset akzeptiert |
| A-01 | antigravity | BLOCKER | CLOSED | Ausnahme wird als Policy-Halt persistiert; kein Rollenretry |
| A-02 | antigravity | OBSERVATION | CLOSED | numerische Unix-Sekunden werden UTC-normalisiert |
<!-- audit:findings:end -->

## Entscheidungstabelle

<!-- audit:decision-table:begin -->
| Finding | Entscheidung | Umsetzung | Verifikation |
|---|---|---|---|
| C-01 | als Restrisiko dokumentiert | definierte v2-Projektion bleibt klein; Default-Cutover weiterhin Slice 18 | unveränderte `tests/test_orchestrator_quota.py` plus 458er Vollsuite grün |
| C-02 | als Restrisiko dokumentiert | gemeinsame Netzwerkklasse mit verlustfrei persistiertem Providertext | Klassifikationstests und fail-safe Quota-Erkennung grün |
| C-03 | als Restrisiko dokumentiert | ISO-Strings und native Unix-Zahlen bleiben die eindeutigen strukturierten Formate | unbekannter Stringtyp fällt ohne Automatik auf Exitcode 2 zurück |
| A-01 | angenommen | `collect_changes`-Ausnahme wird `STOP_REQUEST` | gezielter Git-Lock-Resume-Test PASS |
| A-02 | angenommen | endliche, nicht negative Unix-Sekunden in absoluten Providerfeldern | gezielter Unix-Timestamp-Test PASS |
<!-- audit:decision-table:end -->

## Rückdokumentation in die Arbeitsplan-MD

Der Arbeitsplan verlinkt diese Slice-MD in §5.1 und aus der Slice-14-Überschrift. Tatsächlicher Scope und finale Validierungszahlen sind zurückdokumentiert; Reviewverlauf und Freigaben bleiben ausschließlich in den verwalteten Auditabschnitten dieser Datei.

## Freigabestatus

<!-- audit:approval-status:begin -->
- Implementierung bereit: `YES`
- Validierung: `PASS`
- Claude-Freigabe: `YES`
- Antigravity-Freigabe: `YES`
- Offene Blocker: `NONE`
- Commit autorisiert: `YES`
<!-- audit:approval-status:end -->
