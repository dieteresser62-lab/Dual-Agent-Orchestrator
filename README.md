# Dual-Agent Task Orchestrator

Eine fortsetzbare CLI für klar abgegrenzte Entwicklungsaufgaben mit Codex als Implementierer, Claude als primärem Reviewer und Antigravity als unabhängigem Abschlussreviewer.

## Überblick

Der Orchestrator überführt eine Markdown-Aufgabe in einen geordneten State-v3-Slice-Plan. Jeder Slice besitzt eine exakte Pfad-Allowlist, eine deterministische Validierung, asymmetrische Reviews und einen verifizierten lokalen Git-Commit. Nach dem letzten Slice prüfen alle drei Rollen die vollständige Branchänderung, bevor der Lauf abgeschlossen ist.

![State-v3-Workflow](https://www.plantuml.com/plantuml/proxy?cache=no&src=https://raw.githubusercontent.com/dieteresser62-lab/Dual-Agent-Orchestrator/master/workflow.puml)

Der normale Ablauf ist:

1. Repository, Branch, Aufgabe, Konfiguration und vorhandenen Zustand prüfen.
2. Codex geordnete `SLICE_PLAN`-Datensätze erstellen und Claude sowie Antigravity denselben Planfingerprint prüfen lassen.
3. Vor der Ausführung des freigegebenen Plans eine explizite, fingerprintgebundene Benutzerfreigabe verlangen.
4. Für jeden geplanten Slice:
   - Codex bearbeitet ausschließlich den persistierten Pfadumfang.
   - Der Orchestrator ermittelt den kanonischen Diff und führt die konfigurierte Validierungsmatrix einmal für diesen Fingerprint aus.
   - Claude prüft in der ersten Runde nur die Slice-Änderungen und in späteren Runden nur das Korrekturdelta.
   - Antigravity prüft den vollständigen freigegebenen Slice-Diff einmal, nachdem Claude denselben Fingerprint freigegeben hat.
   - Der Orchestrator staged ausschließlich die geprüften Pfade, erstellt einen lokalen Commit `Slice NN: ...` und verifiziert ihn.
5. Einen branchweiten Vollständigkeitsbericht sowie den abschließenden Claude-/Antigravity-Review gegen die Branchbasis ausführen.
6. Findet der Abschlussreview einen Blocker, wird er als weiterer begrenzter Korrekturslice bearbeitet und commitet; anschließend wird der vollständige Abschlussreview wiederholt.

Keine Rolle ersetzt eine andere. Codex gibt die eigene Arbeit niemals frei und commitet sie nicht selbst. Reviewer können weder den Quell-Worktree bearbeiten noch Validierungsergebnisse für sich beanspruchen.

## Referenzdokumentation

- [Architektur- und Fachkonzept](docs/reference/architecture-and-domain-concept.md) beschreibt Systemgrenze, Domänenmodell, Invarianten, Komponenten, Zustandsmaschine, Vertrauensgrenzen und betriebliche Eigenschaften.
- [Marktvergleich](docs/reference/market-comparison.md) ordnet den Orchestrator anhand aktueller offizieller Produktdokumentation gegenüber repräsentativen Coding-Agenten und Agentenplattformen ein.

## Voraussetzungen und unterstützte Plattformen

Erforderlich ist Python 3.11 oder neuer. Für das TOML-Parsing wird die Python-Standardbibliothek verwendet; das Projekt besitzt keine Python-Laufzeitabhängigkeiten.

Unterstützte Ausführungsumgebungen sind:

- Linux
- macOS
- WSL2

Natives Windows wird derzeit nicht unterstützt, weil der vollständige Workflow dort noch nicht verifiziert wurde. Unter WSL2 sollte nach Möglichkeit der native Befehl `agy` verwendet oder `agy.exe` explizit konfiguriert werden.

Alle drei Rollen-CLIs müssen installiert und authentifiziert sein. Anschließend müssen sie in `PATH` liegen oder über explizite Binärpfade konfiguriert werden:

- `codex`
- `claude`
- `agy` oder `agy.exe`

Die Laufzeit prüft jedes Programm und seine erforderlichen Fähigkeiten verzögert unmittelbar vor dem ersten Aufruf der jeweiligen Rolle.

## Schnellstart

Eine kurze vollständige Anleitung enthält [Quickstart.md](Quickstart.md).

Erstelle anhand von [example-task.md](example-task.md) eine begrenzte Aufgabe, speichere sie im Zielrepository als `task.md` und führe Folgendes aus:

```bash
./run_task
```

Die Positionsschreibweise ist gleichwertig:

```bash
./run_task path/to/my-task.md
```

Verwende entweder den Positionspfad oder `--task-file`, nicht beides:

```bash
./run_task --task-file path/to/my-task.md
```

Eine nicht abgeschlossene `.orchestrator/state.json` wird im Einzelaufgabenmodus automatisch fortgesetzt. Nach einem abgeschlossenen State-v3-Lauf beginnt ein neuer Lauf. Verwende `--resume` explizit, wenn ein Gate aufgelöst oder nach einem Prozessneustart fortgesetzt wird.

## Aufgabengrenzen und Zweischrittbetrieb

Jede produktive Aufgabe deklariert zusätzlich zu Ziel, Nicht-Scope und Akzeptanzkriterien diese maschinenlesbare Grenze:

```text
ORCHESTRATOR_MODE: PLAN_ONLY|IMPLEMENT
WORK_PLAN_PATH: docs/internal/<thema>-work-plan.md
APPROVED_PLAN_COMMIT: <automatisch erzeugter Git-Commit; nur im Handoff>
TARGET_BRANCH: feature/<name>|codex/<name>
TASK_SCOPE: <comma-separated repository-relative paths or globs>
```

`WORK_PLAN_PATH` ist bei `PLAN_ONLY` und im automatisch erzeugten Implementierungs-Handoff erforderlich. `APPROVED_PLAN_COMMIT` wird ausschließlich vom Handoff-Erzeuger zusammen mit den übernommenen `SLICE_PLAN`-Datensätzen geschrieben. Alternativ zu `TASK_SCOPE` wird ein Abschnitt `## Erlaubter Scope` oder `## Allowed Scope` mit Aufzählung akzeptiert. Der angegebene Zielbranch muss vor dem Start existieren und aktiv sein; Codex darf Branches weder erstellen noch wechseln.

`PLAN_ONLY` bildet den ersten Schritt des manuellen Prozesses ab; [example-plan-task.md](example-plan-task.md) ist eine direkt anpassbare Vorlage. Codex erstellt ausschließlich das deklarierte Arbeitsplan-MD. Die späteren Umsetzungsslices stehen als Überschriften im Dokument, während der ausführbare `SLICE_PLAN` dieses Laufs genau einen Dokumentationsslice enthält. Claude und Antigravity prüfen den Plan, danach wartet der Lauf am expliziten Plangate. Nach Freigabe wird ausschließlich das Arbeitsplandokument lokal commitet. Die Umsetzung startet später mit einer neuen Aufgabe im Modus `IMPLEMENT`, die auf den freigegebenen Arbeitsplan verweist.

Im Modus `IMPLEMENT` überführt Codex den Auftrag in einen oder mehrere persistierte Slices. Jeder ausführbare `SLICE_PLAN`-Datensatz enthält:

```text
SLICE_PLAN: <1-based id> | <summary> | <comma-separated repository-relative paths>
```

Die aufgeführten Pfade bilden exakte Commit-Allowlists. Ein Slice darf gemäß den konfigurierten Pfadklassen höchstens zehn produktive Dateigruppen enthalten. Tests und Dokumentation können separat klassifiziert werden; ein nicht klassifizierter Pfad gilt vorsichtshalber als produktiv.

Bereits bei der Planung werden alle `SLICE_PLAN`-Pfade gegen den Task-Scope geprüft. Unerwartete Pfade, ein geänderter Branch, ein geänderter Taskinhalt, ein geänderter Slice-Startcommit oder ein nach dem Review abweichender Fingerprint blockieren den Lauf.

## Zustand, Checkpoints, Logs und Auditdokumente

Laufzeitdaten werden unterhalb von `.orchestrator/` gespeichert:

| Pfad | Zweck |
|---|---|
| `.orchestrator/state.json` | Atomare, maschinenlesbare State-v3-Quelle des aktiven Laufs. |
| `.orchestrator/checkpoints/work-unit-####-slice-####-round-####.json` | Fortsetzungs-Checkpoints mit einsbasierten Arbeitsblock-, Slice- und Rundenidentitäten. |
| `.orchestrator/logs/` | Rohe temporäre Agentenaufruf- und Diagnoselogs. |
| `.orchestrator/runs/<run_id>/work-unit-####-codex.md` | Persistierte Codex-Ausgabe zur Wiederherstellung des Planungs- oder Implementierungskontexts. |

State und Checkpoints dürfen nicht manuell bearbeitet werden.

Menschenlesbare Plan- und Slice-Auditdateien im Markdown-Format gehören in das Zielrepository, üblicherweise unter `docs/internal/`, und werden mit ihrem Slice commitet. Bei einem regulär manuell definierten Lauf müssen sie vorbereitet, aus dem Arbeitsplan verlinkt, mit den erforderlichen verwalteten Auditabschnitten versehen und im Umfang des zugehörigen `SLICE_PLAN` enthalten sein. Ein commitgebundener Handoff erzeugt seine deklarierten Slice-Auditdateien dagegen automatisch vor dem jeweiligen Slice. Der Orchestrator projiziert strukturierte Findings, Reviews, Validierungsattestierungen und Autorisierungsstatus ausschließlich in diese verwalteten Abschnitte. Nach jedem lokalen Slice-Commit ist Git die historische Quelle der Wahrheit.

Im Zweischrittprozess commitet eine freigegebene `PLAN_ONLY`-Aufgabe den bereits
geprüften Arbeitsplan unmittelbar; es folgt kein künstlicher Implementierungs-
oder Abschlussreview des Planartefakts. Anschließend erzeugt der Orchestrator
eine `-implement.md`-Handoff-Aufgabe neben der Planaufgabe. Sie bindet den
exakten Plan-Commit über `APPROVED_PLAN_COMMIT`, enthält die übernommenen
`SLICE_PLAN`-Grenzen und startet als neuer `IMPLEMENT`-Lauf direkt mit Slice 1.
Die zugehörigen Slice-Auditdateien werden aus diesen Grenzen vorbereitet und
mit dem jeweiligen Slice geprüft und commitet.

Aktive oder eingefrorene Zustände der Version 2 werden unverändert abgelehnt. Ein abgeschlossener Zustand der Version 2 bleibt als historischer Abschluss erkennbar, wird aber weder fortgesetzt noch stillschweigend nach State v3 migriert.

## Validierung und Reviewisolation

Nur der Orchestrator führt deterministische Validierungen aus. Planreviews verwenden eine interne Vertragsprüfung für Scope, Arbeitsplanpfad und 1-basierte zukünftige Slice-Überschriften; sie führen nicht die Produkttestsuite aus. Implementierungsreviews verwenden die aus kanonisch geänderten Pfaden und offenen Findings ausgewählte Validierungsmatrix. Attestierungen werden anhand des Diff-Fingerprints zwischengespeichert, und beide Reviewer erhalten dieselbe gebundene Evidenz.

Codex arbeitet mit Schreibzugriff auf den Workspace. Claude und Antigravity erhalten temporäre schreibgeschützte Repositorykopien, während ihre privaten Laufzeit-, Prompt-, Cache- und Logpfade beschreibbar bleiben. Normale Reviews legen das Validierungssystem nicht offen und können den Ziel-Worktree nicht verändern.

Claude verwendet standardmäßig Sonnet mit Effort `high`. Der erste Slice-Review erhält die geänderten Pfade und Hunks des Slice, Akzeptanzkriterien, strukturierte Findings und die gebundene Attestierung. Ein Korrekturreview erhält ausschließlich das Delta seit Claudes zuletzt geprüftem Fingerprint. Eine rein formale Vertragsreparatur erhält die abgelehnte Antwort und den Marker-Vertrag, nicht erneut die Implementierungsevidenz.

Antigravity wird erst ausgeführt, nachdem Claude denselben Fingerprint freigegeben hat. Das gilt für Plan, Slice und Abschlussreview. Antigravity erhält jeweils den vollständigen aktuellen Plan-, Slice- oder Branch-Diff.

Die expliziten Befehlsbuilder des Review-Harness dienen der Diagnose bei Installation, CLI-Versionswechseln oder Fehlersuche. Sie weisen Testausführung und Schreibschutz nachverfolgter Dateien in der isolierten Kopie nach; sie sind nicht Teil eines normalen Reviews.

## Gates, Findings und Fortsetzung

Der Workflow persistiert seinen Zustand, bevor er aus einem fortsetzbaren Halt zurückkehrt. Behebe die zugrunde liegende Ursache und fahre dann mit `--resume` fort. Ein Gate mit Fingerprint erfordert eine explizit protokollierte Entscheidung:

```bash
./run_task --resume --approve-gate \
  --gate-actor "Dieter" \
  --gate-rationale "Exakten persistierten Fingerprint geprüft und Fortsetzung freigegeben"
```

Mit `--reject-gate` und denselben Anforderungen an Akteur und Begründung wird eine Ablehnung protokolliert.

Die wichtigsten Gates sind:

- geänderte Tests ohne vorherige Autorisierung;
- ein optionales manuelles Gate vor jedem Slice-Commit;
- mehr als zehn produktive Änderungsgruppen;
- eine repositorydefinierte Stopregel oder ein Agentendatensatz `STOP_REQUESTED`;
- Pfade außerhalb des persistierten Slice-Umfangs;
- Abweichungen bei Branch, HEAD, Diff-Fingerprint oder Validierungsattestierung;
- fehlende oder nicht verfügbare Validierung;
- geänderte strukturierte Ankerwerte;
- vier Rückgaben des Implementierers in einem Arbeitsblock;
- fehlende Implementierungsänderungen;
- fehlerhafte, fehlende oder widersprüchliche Reviewurteile;
- Quota-, Authentifizierungs-, Binärprogramm-, Berechtigungs-, Netzwerk-, Prozess- oder Timeoutfehler.

Ein freigebender Review erfordert eine vollständige erfolgreiche Attestierung für denselben Fingerprint, autorisierte Teständerungen, keinen reviewer-eigenen offenen Blocker, Reviewevidenz oder konkrete Findings sowie ein Pre-Mortem. Nur der Reviewer, der ein Finding gemeldet hat, darf es schließen oder neu klassifizieren.

Reviewer arbeiten in einem temporären schreibgeschützten Snapshot. Dieser enthält nur Git-sichtbare Quell- und Dokumentationsdateien; Metadaten, Abhängigkeiten und generierte Schwergewichte wie `.git`, `.orchestrator`, `node_modules`, `dist` und Releasearchive werden nicht kopiert. Reine Ausgabevertragskorrekturen erhalten ein leeres schreibgeschütztes Arbeitsverzeichnis. Eindeutig gebundene Formalmarker werden lokal ergänzt, ohne einen zweiten Modellreview auszulösen.

Die Quotabehandlung erfolgt rollenspezifisch. Bei aktivierter automatischer Quotafortsetzung wird ein eindeutiger Reset innerhalb der konfigurierten Wartegrenze persistiert, unter Ausgabe von Heartbeats abgewartet und am exakt fehlgeschlagenen Schritt einmal fortgesetzt. Andernfalls endet der Prozess mit Exitcode 2 und bleibt fortsetzbar. Es gibt keine Ersatzrolle.

## Lokale Commits und externe Git-Aktionen

Nachdem Claude und Antigravity denselben Slice-Fingerprint freigegeben haben, führt der Orchestrator folgende Schritte aus:

1. Repositorystatus und kanonischen Diff erneut ermitteln;
2. Branch, Slice-Grenze, erlaubte Pfade, Reviews, Findings und Validierungsattestierung prüfen;
3. ausschließlich die exakt geprüften Pfade stagen;
4. einen lokalen Commit `Slice NN: <planned summary>` erstellen, wobei Hooks und Signierung für die mechanische Transaktion deaktiviert sind;
5. Pfadliste und resultierenden Commit-Hash verifizieren.

Der Orchestrator pusht, mergt oder force-pusht niemals und schreibt die Historie nicht um. Diese Aktionen bleiben explizite Benutzervorgänge außerhalb des Workflows.

## Watch-Modus

Der Orchestrator kann als FIFO-Warteschlangenworker ausgeführt werden:

```bash
./run_task --watch
```

Der Watch-Modus:

- überwacht stabile `*.md`-Dateien in `inbox/`, älteste zuerst;
- hält eine Einzelprozesssperre `inbox/.lock`, sofern `fcntl` verfügbar ist;
- weist jeder Aufgabe eine persistierte Lauf-ID und einen Digest des Aufgabeninhalts zu;
- aktiviert standardmäßig `--skip-git-check`, weil geprüfte Slice-Commits den Worktree absichtlich verändern;
- streamt standardmäßig `stdout`;
- verschiebt abgeschlossene Aufgaben mit UTC-Zeitstempel nach `outbox/done/`;
- wiederholt technische Fehler und verschiebt ausgeschöpfte Aufgaben als Poison Tasks nach `outbox/failed/`;
- hält die Warteschlange bei Exitcode 2, 3 oder 4 an, damit die erste fortsetzbare Aufgabe ihre FIFO-Zuständigkeit behält;
- führt eine erfolgreich abgeschlossene Aufgabe nicht erneut aus, wenn nur das Verschieben in die Outbox wiederholt werden muss.

Verzeichnisse, Abfrageintervall oder Anzahl technischer Wiederholungen können überschrieben werden:

```bash
./run_task --watch \
  --inbox-dir /path/to/inbox \
  --outbox-dir /path/to/outbox \
  --poll-interval 2 \
  --watch-max-retries 3
```

Nach Behebung einer angehaltenen Watch-Aufgabe wird der Watcher neu gestartet. Die Identitäts-Sidecar-Datei der Aufgabe setzt denselben Lauf und Arbeitsblock fort.

## Probeläufe

Der integrierte Probelauf durchläuft Planfreigabe, zwei Slice-Commits und Abschlussreview ohne Agenten-/API-Aufrufe oder Repositoryschreibzugriffe:

```bash
./run_task --dry-run --task-file example-task.md --quiet
```

Für deterministische Negativ- und Fortsetzungsszenarien kann ein State-v3-JSON-Szenario übergeben und optional dessen Auditbericht geschrieben werden:

```bash
./run_task \
  --dry-run-scenario path/to/scenario.json \
  --dry-run-report path/to/report.json \
  --task-file example-task.md
```

## CLI-Referenz

`src/cli.py` ist die Quelle der Wahrheit für das Argument-Parsing. `run_task` ist ein Kompatibilitätsstarter, der diese Datei findet und alle Argumente weiterleitet.

### Kern- und Zustandsoptionen

| Schalter | Standard | Beschreibung |
|---|---|---|
| `[task-file]` | `task.md` | Kompatible Positionskurzform für die Aufgabendatei. |
| `--task-file <path>` | `task.md` | Expliziter Aufgabenpfad; nicht mit der Positionsform kombinierbar. |
| `--config <path>` | `RUN_TASK_CONFIG` oder `./orchestrator.toml` | Repositoryrichtlinien-Konfiguration. |
| `--agents-file <path>` | `AGENTS.md` des Repositorys | Gemeinsame Agentenanweisungen, die in Prompts eingefügt werden. |
| `--resume` / `--no-resume` | automatisch | Nicht abgeschlossenen Einzelaufgabenzustand automatisch fortsetzen; bei Bedarf explizit überschreiben. |
| `--force-overwrite-state` | automatisch bei abgeschlossenem Zustand | Trotz vorhandenen Zustands einen neuen Lauf beginnen; explizite Verwendung umgeht den normalen Zustandsschutz. |
| `--strict-preflight` | aus | Einen Fehler der Provider-DNS-Vorabprüfung als fatal behandeln. |
| `--skip-git-check` / `--no-skip-git-check` | aus; im Watch-Modus an | Prüfung auf einen sauberen Repositoryzustand überschreiben. |
| `--manual-slice-gate` / `--no-manual-slice-gate` | Repositorykonfiguration oder aus | Vor jedem Slice-Commit eine explizite Freigabe verlangen. |
| `--plan-gate` / `--no-plan-gate` | Repositorykonfiguration oder an | Nach Claude-/Antigravity-Planfreigabe eine explizite fingerprintgebundene Benutzerfreigabe verlangen. |
| `--plan-only` / `--no-plan-only` | Aufgabenmarker oder nicht gesetzt | Den Lauf auf das deklarierte Arbeitsplanartefakt begrenzen beziehungsweise explizit als Implementierung ausführen. |
| `--work-plan <path>` | Aufgabenmarker | Exakter repositoryrelativer `WORK_PLAN_PATH` für `PLAN_ONLY`; darf dem Marker nicht widersprechen. |
| `--target-branch <branch>` | Aufgabenmarker | Exakter erforderlicher Feature-Branch; darf dem Marker nicht widersprechen. |
| `--approve-gate` / `--reject-gate` | nicht gesetzt | Zusammen mit explizitem `--resume` über das exakt persistierte Benutzergate entscheiden. |
| `--gate-actor <name>` | nicht gesetzt | Erforderliche Identität für eine explizite Gate-Entscheidung. |
| `--gate-rationale <text>` | nicht gesetzt | Erforderliche Begründung für eine explizite Gate-Entscheidung. |

### Validierung, Probelauf und Quota

| Schalter | Standard | Beschreibung |
|---|---|---|
| `--test-command <cmd>` | Umgebung, Repositorymatrix oder Erkennung | Kompatibilitäts-Validierungsbefehl; eine explizite leere Zeichenfolge deaktiviert ihn. |
| `--retry-incomplete-validation` | aus | Eine zwischengespeicherte `INCOMPLETE`-Matrix für denselben Fingerprint nach Reparatur der Umgebung erneut ausführen. |
| `--dry-run` | aus | Das integrierte State-v3-Erfolgsszenario ohne API-Aufrufe oder Schreibzugriffe ausführen. |
| `--dry-run-scenario <path>` | nicht gesetzt | Ein deterministisches JSON-Szenario ausführen. |
| `--dry-run-report <path>` | nicht gesetzt | Den Auditbericht des skriptgesteuerten Szenarios schreiben. |
| `--quota-auto-resume` / `--no-quota-auto-resume` | an | Eine automatische Fortsetzung bei eindeutigem Reset aktivieren. |
| `--quota-safety-margin <seconds>` | `60` | Nach einem erkannten Reset zusätzlich zu wartende Zeit. |
| `--quota-max-wait <seconds>` | `86400` | Maximale automatische Wartezeit. |
| `--quota-max-auto-resumes <count>` | `1` | Automatische Fortsetzungen je blockiertem Rollenschritt. |
| `--quota-heartbeat-interval <seconds>` | `30` | Heartbeat-Intervall während des Quotawartens. |

### Agentenausgabe und Rollenkonfiguration

| Schalter | Standard | Beschreibung |
|---|---|---|
| `--agent-output <none\|summary\|full>` | `none` | Umfang der auszugebenden abgeschlossenen Agentenantworten. |
| `--agent-output-max-chars <count>` | `1800` | Maximale Zeichenanzahl abgeschlossener Antworten im Zusammenfassungsmodus. |
| `--agent-live-stream` / `--no-agent-live-stream` | an | Live-Prozessausgabe aktivieren oder deaktivieren. |
| `--agent-live-stream-mode <compact\|full>` | `compact` | Ausführlichkeit des Livestreams. |
| `--agent-live-stream-channels <both\|stdout\|stderr>` | Umgebung oder `stdout` | Auszugebende Live-Kanäle. |

Rolleneinstellungen verwenden zuerst CLI-Werte, dann `RUN_TASK_<ROLE>_*` und anschließend diese persistenten Standards:

| Rolle | CLI-Optionen | Standards |
|---|---|---|
| Codex | `--codex-binary`, `--codex-model`, `--codex-timeout`, `--codex-effort` | `codex`, `gpt-5.6-sol`, 1800s, `medium` |
| Claude | `--claude-binary`, `--claude-model`, `--claude-timeout`, `--claude-effort` | `claude`, `sonnet`, 1800s, `high` |
| Antigravity | `--antigravity-binary`, `--antigravity-model`, `--antigravity-timeout`, `--antigravity-effort` | erkanntes `agy`, `gemini-3.1-pro-high`, 1800s, `high` |

`--claude-max-budget-usd` oder `RUN_TASK_CLAUDE_MAX_BUDGET_USD` ergänzt eine optionale Budgetobergrenze für den Print-Modus. Opus ist nicht der Standard; `--claude-model opus` dient ausschließlich einer expliziten Eskalation.

Beispiele:

```bash
./run_task --claude-model sonnet --claude-effort high
RUN_TASK_ANTIGRAVITY_BINARY=agy.exe ./run_task
./run_task --codex-binary /opt/codex/bin/codex --codex-timeout 2400
```

### Watch- und Loggingoptionen

| Schalter | Standard | Beschreibung |
|---|---|---|
| `--watch` | aus | Markdown-Aufgaben aus der Inbox fortlaufend verarbeiten. |
| `--inbox-dir <path>` | `inbox` | Eingabeverzeichnis des Watch-Modus. |
| `--outbox-dir <path>` | `outbox` | Stammverzeichnis für abgeschlossene und fehlgeschlagene Aufgaben. |
| `--poll-interval <seconds>` | `5.0` | Abfrageintervall der Inbox. |
| `--watch-max-retries <count>` | `3` | Anzahl technischer Fehler vor der Poison-Task-Behandlung. |
| `--verbose` | aus | Debug-Logging aktivieren. |
| `--quiet` | aus | Nur Warnungen und Fehler anzeigen. |

`--verbose` und `--quiet` schließen einander aus.

## Konfiguration

Die Konfigurationspräzedenz lautet:

1. expliziter CLI-Wert;
2. passende Umgebungsvariable `RUN_TASK_*`;
3. Wert aus `orchestrator.toml` des Repositorys;
4. integrierter Standard oder automatische Erkennung des Testbefehls.

Werte für Agentenprogramm, Modell, Effort, Timeout und Claude-Budget umgehen bewusst das Repository-TOML und verwenden ausschließlich CLI, Umgebung und Rollenstandards.

Ein explizit leerer Testbefehl deaktiviert die Erkennung eines Validierungsbefehls:

```bash
./run_task --test-command ""
RUN_TASK_TEST_CMD="" ./run_task
```

Ohne deklarierten Validierungsbefehl prüft die Erkennung zunächst `pyproject.toml` mit pytest-Konfiguration, danach ein `package.json`-Testskript und schließlich ein `test`-Target im Makefile.

Das TOML-Schema des Repositorys enthält ausschließlich portable Richtlinien:

```toml
[paths]
productive = ["src/**/*.py", "run_task", "*.toml"]
tests = ["tests/**"]
documentation = ["docs/**", "*.md"]
generated = [".orchestrator/**", "**/__pycache__/**", ".pytest_cache/**"]

[[stop_rules]]
id = "DOMAIN-001"
description = "Stop when the named domain invariant changes."

[validation]
default_command = ["python3", "-m", "pytest", "tests/", "-v"]
default_timeout_seconds = 1800

[[validation.rules]]
patterns = ["frontend/**"]
command = ["npm", "test"]
timeout_seconds = 1200

[workflow]
manual_slice_gate = false
plan_gate = true
```

`default_shell_command` oder ein regelbezogener `shell_command` sollten nur verwendet werden, wenn Shell-Semantik erforderlich ist. Ein Validierungseintrag darf nicht sowohl einen Argumentvektorbefehl als auch einen Shell-Befehl enthalten. Muster sind repositoryrelativ, verwenden `/` und dürfen nicht mit `..` ausbrechen.

Nützliche Umgebungsüberschreibungen sind:

```bash
RUN_TASK_TEST_CMD="python3 -m pytest tests/ -v" ./run_task
RUN_TASK_SKIP_GIT_CHECK=0 ./run_task --watch
RUN_TASK_WATCH_STREAM_CHANNELS=both ./run_task --watch
RUN_TASK_QUOTA_AUTO_RESUME=0 ./run_task
```

## Agentenanweisungen und Ausgabevertrag

Die aktiven Anweisungsdateien des Repositorys sind:

| Datei | Verantwortung |
|---|---|
| `AGENTS.md` | Gemeinsamer Ausführungs-, Sicherheits-, Review- und Marker-Vertrag. |
| `CODEX.md` | Implementiererrolle und Bereitschaftsdatensätze. |
| `CLAUDE.md` | Primärer gezielter Reviewer mit persistentem Sonnet-/High-Profil. |
| `ANTIGRAVITY.md` | Unabhängiger zweiter Plan-, Slice- und Abschlussreviewer. |

Alle Agentenantworten enden mit `STATUS: DONE`. State-v3-Datensätze sind:

| Erzeuger oder Schritt | Erforderlicher Datensatz |
|---|---|
| Codex-Plan | `SLICE_PLAN: <id> \| <summary> \| <paths>` und `PLAN_READY: YES\|NO` |
| Codex-Implementierung | `TEST_FILES_TOUCHED: NONE\|<paths>` und `IMPLEMENTATION_READY: <slice-id> \| YES\|NO` |
| Codex-Abschlussbericht | `FINAL_REPORT_READY: YES\|NO` |
| Jeder Reviewer, erste Zeile | `REVIEWER: claude\|antigravity` |
| Claude-/Antigravity-Planreview | `PLAN_APPROVAL: YES\|NO` |
| Slice-Review | `SLICE_APPROVAL: <slice-id> \| YES\|NO` |
| Branchweiter Abschlussreview | `FINAL_APPROVAL: YES\|NO` |
| Neues Finding | `NEW_FINDING: C-01\|A-01 \| BLOCKER\|OBSERVATION \| <description> \| <acceptance test>` |
| Aktualisierung durch Finding-Eigentümer | `FINDING_STATUS: <id> \| OPEN\|CLOSED \| <rationale>` |
| Optionale Neuklassifizierung durch Eigentümer | `FINDING_RECLASSIFIED: <id> \| BLOCKER\|OBSERVATION \| <rationale>` |
| Finding-Antwort von Codex | `FINDING_RESPONSE: <id> \| ACCEPTED\|REJECTED \| <rationale>` |
| Review ohne konkrete Schwachstelle | `REVIEW_EVIDENCE: <dimensions> \| <largest residual risk> \| <break condition>` |
| Voraussetzung einer positiven Freigabe | `PRE_MORTEM: <most likely failure cause in three months>` |
| Stopp durch beliebige Rolle | `STOP_REQUESTED: <rule-id> \| <rationale>` anstelle von Bereitschaft oder Freigabe |

Der Orchestrator besitzt die Validierungsattestierungen; Agenten dürfen `VALIDATION_RESULT` nicht ausgeben. State-v2-Freigabe- und aggregierte Finding-Marker sind ungültig.

## Exitcodes

| Code | Bedeutung |
|---:|---|
| `0` | Der vollständige Workflow einschließlich aller Slice-Commits und des branchweiten Abschlussreviews wurde erfolgreich abgeschlossen. |
| `1` | Technischer, Konfigurations-, Zustandsschema-, Repository- oder interner Workflowfehler. |
| `2` | Die Quota kann nicht automatisch fortgesetzt werden oder die konfigurierte Wartepolitik ist ausgeschöpft. |
| `3` | Eine erforderliche Agenteninstanz ist fehlgeschlagen, hat ihr Timeout erreicht oder ist nicht verfügbar. |
| `4` | Eine Benutzerentscheidung oder ein Richtlinien-Gate ist erforderlich. |

Die Codes 2, 3 und 4 erhalten einen fortsetzbaren Zustand. Prüfe den protokollierten Gate-Grund, behebe oder entscheide ihn und setze denselben Lauf mit `--resume` fort.

## Optionaler globaler Befehl

Damit der Starter aus anderen Repositorys aufgerufen werden kann:

```bash
mkdir -p ~/.local/bin
ln -s /absolute/path/to/Dual-Agent-Orchestrator/run_task ~/.local/bin/run_task
chmod +x /absolute/path/to/Dual-Agent-Orchestrator/run_task
```

## Verifikation

```bash
./run_task --help
./run_task --dry-run --task-file example-task.md --quiet
python3 -m pytest tests/ -v
```
