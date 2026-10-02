# Schnellstart

> [!TIP]
> **Noch nichts eingerichtet?** Die [Einrichtung](docs/reference/einrichtung.md)
> führt Schritt für Schritt durch Installation, Anschluss eines vorhandenen
> Projekts und den Start bei null mit nur einer Projektbeschreibung – oder
> lässt sich von Claude Code abarbeiten. Dieser Schnellstart setzt dort an, wo
> sie endet.

Diese Anleitung beschreibt den normalen, vollständig automatischen Inbox-Ablauf in einem bereits eingerichteten Projekt. [README.md](README.md) enthält Konfiguration und CLI-Referenz. Wer zuerst verstehen möchte, **was** dabei geschieht, liest [Wie der Orchestrator arbeitet](docs/reference/ablauf-des-orchestrators.md) — dort steht der vollständige Ablauf, beginnend ohne Fachbegriffe. Hintergründe stehen im [Architektur- und Fachkonzept](docs/reference/architecture-and-domain-concept.md); die Produktpositionierung erläutert der [Marktvergleich](docs/reference/market-comparison.md).

## 1. Voraussetzungen prüfen

Benötigt werden Python 3.11 oder neuer, Git und authentifizierte Installationen beider Rollen-CLIs:

```bash
python3 --version
git --version
codex --version
claude --version
```

Der Orchestrator läuft unter Linux oder WSL2 mit lesbarem `/proc`. Andere Plattformen werden nicht unterstützt. Fehlt etwas davon, hilft Teil 1 der [Einrichtung](docs/reference/einrichtung.md). Implementer (standardmäßig Codex), Reviewer und Final-Reviewer (standardmäßig Claude) werden über `[roles]` und `[agent_profiles]` in `orchestrator.toml` besetzt.

Nach einem Orchestrator-Update führen Sie `run_task --help` aus und ziehen das
Projekt nach [Abschnitt 2.11 der Einrichtung](docs/reference/einrichtung.md#211-den-orchestrator-aktualisieren-und-ein-projekt-nachziehen)
nach. Unfertige Läufe können bei geänderten Zertifizierungs- oder
Profildigests mit `AGENT-PROFILE-DIFF` anhalten; sie brauchen dann den
passenden alten Stand oder einen bewusst begonnenen neuen Lauf.
Das Codex-Registerminimum bleibt 0.156.1; die gehärtete
Codex-Implementer-Aufrufform (Stand Oktober 2026) wurde mit 0.159.2 gemessen.
Die Unterstützung durch 0.156.1 ist ungeprüft; verwenden Sie den
gemessenen oder einen neueren Stand und beachten Sie die Prüfgrenzen in Abschnitt 4.3.

Antigravity (AGY) ist eine **experimentelle, ausdrücklich per TOML wählbare** Reviewer-Option für private DIY-Nutzung unter WSL 2 / Ubuntu / ext4. Die beiden Live-Canaries sind seit dem 29.09.2026 bestanden; beide AGY-Slots sind `experimental`. Bei AGY wird der vollständige Review-Snapshot samt Anfrage und Evidenz an Google gesendet. Die [Antigravity-Anleitung](docs/reference/antigravity-reviewer.md) beschreibt Nachweise und Auswahl. Die Standardbelegung bleibt Codex / Claude / Claude.

Andere Belegungen sind nur mit gültiger `certified`- oder `experimental`-
Zertifizierung jedes Slots wählbar. Der Implementer-Hersteller muss sich von
beiden Reviewslot-Herstellern unterscheiden. Beispiel für Claude / Codex / Codex:
**Claude/Implementer und beide Codex-Reviewslots sind seit dem 01.10.2026
`experimental`; diese Belegung ist jetzt ausdrücklich per TOML wählbar.**
Die [Reviewer-Zertifizierung](docs/reference/reviewer-certification.md) dokumentiert
die akzeptierte 512-Befunde-Schwäche, die [Implementer-Zertifizierung](docs/reference/implementer-certification.md)
die Phase-0-Befunde. Andere `candidate`-Paare bleiben gesperrt.

```toml
[roles]
implementer = "implementation"
reviewer = "review"
final_reviewer = "final_review"

[agent_profiles.implementation]
provider = "claude"
model = "opus"
effort = "high"
timeout_seconds = 0

[agent_profiles.implementation.provider_options.claude]
toolchain_read_roots = ["/absolute/node-root"]

[agent_profiles.review]
provider = "codex"
model = "sol"
effort = "high"
timeout_seconds = 0
stall_timeout_seconds = 900
tool_timeout_seconds = 3600

[agent_profiles.final_review]
provider = "codex"
model = "sol"
effort = "high"
timeout_seconds = 0
stall_timeout_seconds = 900
tool_timeout_seconds = 3600
```

Die Zertifizierung gilt **je Slot**, ohne Allowlist kompletter Belegungen. Topologie-Evidenz liegt für Claude / Codex / Codex und die Standardbelegung vor; für andere Mischungen, etwa Claude / AGY / AGY oder Claude / Codex / AGY, wird ein eigener Probelauf empfohlen.

Das Beispiel begrenzt die **Modellstille auf 900 Sekunden**: stdout-Zeilen setzen die Uhr zurück, während gemeldeter Werkzeuge ruht sie. `stall_timeout_seconds = 0` schaltet diese Erkennung ab; `timeout_seconds` begrenzt zusätzlich die gesamte Laufzeit einschließlich Werkzeugen. Die Hänger von 20–60 Minuten werden damit als `provider stalled` über das Transportfehlerbudget wiederholt, während legitime lange Tests weiterlaufen. `tool_timeout_seconds = 3600` begrenzt zusätzlich jedes einzelne offene Werkzeug auf 60 Minuten; `0` schaltet diese Grenze ab. Kein Gesamtzeitlimit wird für Ereignisströme nur mit beiden aktivierten Netzen empfohlen. Details und die Print-Ausnahmen stehen in der [Einrichtung](docs/reference/einrichtung.md).


`/absolute/node-root` ist ein Platzhalter für ein existierendes absolutes
Verzeichnis, etwa `~/.nvm/versions/node/<version>`; `~` muss im TOML durch den
absoluten Pfad ersetzt werden. Die Wurzel bleibt schreibgeschützt und ihr `bin`
kommt beim Claude-Implementer vor den festen PATH `/usr/local/bin:/usr/bin:/bin`.
Codex behält dagegen den Eltern-PATH; ohne Freigabe einer Home-Installation
kann er still auf eine andere Version unter `/usr` zurückfallen. Starten Sie
die Wache für Aufrufe per Namen in einer Shell, in der `command -v node`
auf die freigegebene Installation zeigt und deren `bin`
im PATH vor `/usr/bin` steht; andernfalls nutzen Sie absolute Werkzeugpfade.
Codex fügt freigegebene Wurzeln nicht zum PATH hinzu. Die Identitätsbindung
speichert die ausgewählte CLI und deren Interpreter, nicht den gesamten
PATH; beachten Sie das beim Resume (Einrichtung 4.3). Liegen die
passenden Werkzeuge unter `/usr` oder im Repository, entfällt die zusätzliche
Wurzel; externe Symlink-Ziele von `node_modules` oder `.venv` brauchen sie
ebenfalls. Der Orchestrator-Testbefehl läuft außerhalb der Agenten-Sandbox
in der Startumgebung; dessen Erfolg beweist deren Toolchain-Sichtbarkeit nicht.
HOME und Credential-Verzeichnisse dürfen nicht freigegeben werden. Die Claude-Bash-
Sandbox braucht **socat und bubblewrap** und startet mit `failIfUnavailable`
bei fehlenden Voraussetzungen nicht. Details und Zertifizierungsnachweise:
[Einrichtung](docs/reference/einrichtung.md),
[Implementer-Zertifizierung](docs/reference/implementer-certification.md) und
[Reviewer-Zertifizierung](docs/reference/reviewer-certification.md).

Nach CLI-Updates führt der Operator aus dem Orchestrator-Checkout den
konto- und kontingentfreien Offline-Quicktest aus; echte CLIs arbeiten dabei
gegen lokale Fake-Server, ohne Modellaufruf:

```bash
python3 scripts/probe_reviewer.py boundary-check --pair all --out /tmp/boundary-update
```

Bei Bedarf ergänzt `--toolchain-root /absolute/node-root` die konkrete
Toolchain. Alternativ gilt `python3 scripts/qualification/offline_boundary.py
--pair all`. Neue CLI-Versionen brauchen nach der Operatorpolitik keine
Neuzertifizierung je Update; der Quicktest ersetzt weder Live-Phase 0 noch
die Pflichtprüfung der gebundenen Rechte je Aufruf.

## 2. Zielrepository prüfen

Wechsle in das Repository, in dem die Änderung entstehen soll:

```bash
cd /path/to/target-repository
git status --short
mkdir -p inbox
```

Den Zielbranch musst du im Watch-Modus weder benennen noch vorher anlegen oder auschecken. Der Orchestrator priorisiert eine ausdrückliche Angabe, übernimmt alternativ genau einen `feature/<name>`- oder `codex/<name>`-Namen aus dem Fließtext und erzeugt sonst deterministisch einen sprechenden Namen aus Gegenstand und Aufgabendigest. Mehrere verschiedene Textkandidaten oder ein vorhandener fremder automatisch erzeugter Branch führen zu einem sicheren Halt.

Beim ersten Start verhält er sich so:

- Fehlt der Zielbranch, wird er vom aktuellen `HEAD` angelegt und aktiviert.
- Existiert ein ausdrücklich angegebener, erkannter oder passend digestgebundener erzeugter Branch, wird zu ihm gewechselt, sofern der Arbeitsbaum sicher wechselbar ist.
- Ist ein solcher Branch bereits aktiv, wird die Aufgabe ab seinem aktuellen `HEAD` fortgeführt; vorhandene Branch-Commits bleiben erhalten.
- Erfordert die Aufgabe einen Branchwechsel, während nicht ignorierte Arbeitsbaum- oder Indexänderungen vorliegen, stoppt der Orchestrator ohne Stash, Bereinigung oder Übernahme dieser Änderungen.
- Bei einem Resume bleibt der persistierte Zielbranch bindend; ein abweichender aktiver Branch führt zum `BRANCH-MISMATCH`-Gate.

Agenten selbst führen keine Branchoperationen aus. Der Orchestrator erstellt
lokale, pfadgenau geprüfte Commits und nach befundfreier Abnahme standardmäßig
einen lokalen Merge mit `--no-ff` in den Basisbranch. Mit
`[workflow] merge_completed_branch = false` bleibt das Mergen beim Benutzer.
Er pusht und force-pusht niemals und schreibt die Historie nicht um.

Schreiben Sie `AGENTS.md` rollenneutral: Die ersten 12.000 Zeichen gelangen in den
Implementer-Auftrag und als dessen Evidenz auch zu beiden Prüfern.
Codex-Implementer laden die Datei zusätzlich nativ; Codex-Prüfer haben
`project_doc_max_bytes=0`. Vorhandene `CLAUDE.md`, `CODEX.md` und `GEMINI.md`
werden nicht automatisch als Rollenanweisungen geladen; kennzeichnen Sie sie als
Handbetrieb-Dateien. Regeln wie „kein Review ohne Findings“ widersprechen
dem Prüfvertrag und verhindern befundfreie Abschlüsse. Details und Vorlage
stehen in Abschnitt 2.4 der [Einrichtung](docs/reference/einrichtung.md).

## 3. Eine Idee in die Inbox legen

Erstelle eine beschreibend benannte Markdown-Datei, beispielsweise:

```bash
nano inbox/meine-idee.md
```

Für den normalen Ablauf genügt eine menschlich formulierte Idee:

```markdown
# Meine Idee

Ich möchte zwei Varianten verständlich miteinander vergleichen können.
Bitte untersuche zuerst die bestehende Anwendung. Frage nur nach, wenn eine
echte Produktentscheidung zu unterschiedlichen Ergebnissen führen würde.
```

Falls du den Namen festlegen möchtest, ergänze optional beispielsweise `TARGET_BRANCH: feature/mein-vorhaben`.

Du musst keine Pfade, Slices, Akzeptanzkriterien, Risiken oder Tests vorgeben. Fehlen formale Ausführungsmarker und ein Scope-Abschnitt, leitet der Orchestrator sicher einen `PLAN_ONLY`-Auftrag ab. Aus `meine-idee.md` entsteht der Arbeitsplan `docs/internal/meine-idee-arbeitsplan.md`; zunächst ist nur dieser Planpfad beschreibbar. Der Implementer übersetzt die Idee anhand des Repositorys in einen konkreten, vom Reviewer geprüften Arbeitsplan. Direkter Implementierungsscope wird niemals aus freier Prosa geraten.

Verwende pro Idee einen eindeutigen Dateinamen. Teilweise formale Mischformen werden fail-closed abgelehnt: Sobald beispielsweise `ORCHESTRATOR_MODE`, `WORK_PLAN_PATH`, `APPROVED_PLAN_COMMIT`, `TASK_SCOPE` oder ein Scope-Abschnitt vorkommt, muss der vollständige formale Vertrag stimmen. Geheimnisse oder Zugangsdaten gehören nicht in die Aufgabendatei.

## 4. Optionalen Probelauf ausführen

Die Installation lässt sich aus dem Orchestrator-Repository ohne Agentenaufrufe und ohne Repositoryänderung prüfen:

```bash
./run_task --dry-run --task-file example-task.md --quiet
```

Ein erfolgreicher Probelauf endet mit Exitcode 0. Für den normalen Inbox-Einsatz ist dieser Schritt nicht bei jeder Aufgabe erforderlich.

## 5. Automatischen Ablauf starten

Starte im Zielrepository genau diesen Befehl:

```bash
run_task --watch
```

Der Standardablauf benötigt keine Zwischenfreigabe:

1. Der Watcher übernimmt die älteste stabile Markdown-Datei aus `inbox/` und bereitet ihren Zielbranch vor.
2. Der informelle Intake erzeugt einen eng begrenzten `PLAN_ONLY`-Vertrag und ein digestgebundenes Gesamtaudit unter `docs/internal/`.
3. Der Implementer erstellt den Arbeitsplan. Der Orchestrator prüft dessen Slice-/Pfadvertrag noch vor dem Reviewer und gibt eine reparierbare Strukturabweichung automatisch genau einmal an den Implementer zurück. Erst der handoff-fähige Planfingerprint geht an den Reviewer.
4. Nach der Planfreigabe commitet der Orchestrator den Plan lokal und erzeugt automatisch die zugehörige `-implement.md`-Aufgabe.
5. Derselbe Watch-Prozess übernimmt den Handoff unmittelbar und beginnt ohne zweite Planungsrunde mit Slice 1.
6. Zu Beginn jedes Slices entsteht dessen Auditdokument. Der Implementer setzt um, der Orchestrator validiert, der Reviewer prüft und der Orchestrator erstellt den lokalen Slice-Commit.
7. Technische Korrekturen an bereits freigegebenen Vorgängerslices können über eine eng geprüfte `REMEDIATION_PATHS`-Erweiterung automatisch in den laufenden Slice aufgenommen werden.
8. Nach dem letzten Slice liest der Final-Reviewer den vollständigen Branch ab seinem Abzweigpunkt vom Hauptbranch des Repositorys. Er meldet dort nur neue Findings oder das erneute Auftreten bekannter Signaturen und fordert keine Sonderkorrektur an.
9. Findet der Abnahmereview Restarbeit, erzeugt der Orchestrator daraus eine gewöhnliche neue Inbox-Aufgabe, und der gesamte Prozess beginnt von vorn — Planung, Umsetzung, Prüfung, Commit. Findet er nichts mehr, endet der Lauf mit Exitcode 0 und die Aufgabe wird nach `outbox/done/` verschoben. Der Zyklus ist durch `max_acceptance_reviews` begrenzt (Vorgabe 6).

Plan-, Teständerungs-, Slice-Commit- und Umfangs-Gates sind standardmäßig aus; eine angemeldete Umfangserweiterung genehmigt der Orchestrator selbst. Echte Produktentscheidungen, unbekannte Pfade, Scopeverletzungen, nicht verfügbare Pflichtwerkzeuge, rote Pflichtvalidierungen und Provider-/Quota-Probleme können weiterhin sicher anhalten.

Der Implementer nutzt standardmäßig Codex mit Sol (`sol`), Reviewer und Final-Reviewer nutzen Claude mit Opus; der Standard-Effort ist `high`. Codex löst die Familie einmal beim Laufstart je gebundenem Binary über den lokalen Katalog `codex debug models` auf und bindet das Modell mit dem besten Rang im Laufprofil und Log. Das gilt für jede Codex-Rolle; beide Reviewslots teilen bei gleicher Binäridentität die Startabfrage. Resume prüft den gebundenen Slug erneut im lokalen Katalog, wählt kein neues Modell und stoppt bei fehlender Verfügbarkeit. Für eine besonders schwierige oder eine einfache Aufgabe lässt sich dies beim Start wählen, etwa `run_task --watch --implementer-effort xhigh --reviewer-effort max`; die möglichen Werte nennt die [Einrichtung](docs/reference/einrichtung.md).

Jede Logzeile trägt einen lokalen Zeitstempel. Während längerer Agentenaufrufe erscheint regelmäßig `<rolle> still running (elapsed: …)`; im Compact-Modus werden am Ende nur Findings, Entscheidungen, Status und eine kurze Nutzungssumme hervorgehoben.

## 6. Optionale manuelle und formale Betriebsarten

Eine menschliche Planabnahme ist opt-in:

```bash
run_task --watch --plan-gate
```

Entsprechend aktivieren `--test-change-gate` eine zusätzliche Abnahme für Teständerungen und `--manual-slice-gate` eine Abnahme vor jedem Slice-Commit. Ohne diese Optionen bleiben Validierung sowie Reviewer-Reviews vollständig verpflichtend; nur der zusätzliche menschliche Halt entfällt.

Für bereits ausgearbeitete, maschinell erzeugte oder bewusst getrennt ausgeführte Aufträge bleiben formale Dateien unterstützt. [example-plan-task.md](example-plan-task.md) zeigt `ORCHESTRATOR_MODE: PLAN_ONLY`; [example-task.md](example-task.md) zeigt `ORCHESTRATOR_MODE: IMPLEMENT`, einen optional ausdrücklich gesetzten `TARGET_BRANCH`, `TASK_SCOPE`, Akzeptanzkriterien und Stopbedingungen.

Ein formaler Einzelauftrag kann so gestartet werden:

```bash
run_task --task-file task.md
```

Ein außerhalb des Watchers bewusst getrennt gestarteter Implementierungs-Handoff verwendet beispielsweise:

```bash
run_task --no-resume --force-overwrite-state \
  --task-file inbox/mein-vorhaben-implement.md
```

Im normalen Watch-Modus ist keiner dieser zusätzlichen Starts erforderlich.

## 7. Angehaltenen Lauf fortsetzen

Nach einem Prozessneustart oder der Behebung eines technischen Problems genügt für eine Watch-Aufgabe erneut:

```bash
run_task --watch
```

Die Aufgabenidentität, `.orchestrator/state.json` und Checkpoints führen denselben Lauf am exakt persistierten Rollen- und Sliceschritt fort. Diese Dateien dürfen niemals manuell bearbeitet werden.

Direkt vor jedem Providerprozess vermisst der Orchestrator die vollständig serialisierte Eingabe gegen das für Provider, Rolle und Operation konfigurierte Zeichen- und UTF-8-Bytebudget. Vor den branchweiten Finalaufrufen folgt zusätzlich ein agentenfreies Preflight über Recordkette, State-Spiegel, Findings, Attestierung und autorisierte Pfade. Ein Halt mit Gategrund `bootstrap_check` und Exitcode 4 bedeutet deshalb keine menschliche Freigabe und keinen Providerfehler. Lies Fehlercode, Größen beziehungsweise betroffene Records/Pfade in Log und Auditansicht, behebe die Ursache und starte denselben Resume-Befehl erneut. Der Watcher behält dabei Run-ID und Rollenstep bei, erhöht keinen Attempt-Zähler und erzeugt keine `.poison`-Datei.

Neue Workflows sind unveränderlich an `structured-v2` gebunden. Für sie liegt die technische Wahrheit der abgebildeten Entscheidungen unter `.orchestrator/artifacts/<run-id>/records/`. `state.json` und Checkpoints sind der geprüfte Betriebsspiegel, `head.json` ist nur ein rekonstruierbarer Cache, und die Markdown-Dateien unter `docs/internal/` sind menschenlesbare Auditansichten. Native JSON-Resultate werden gegen das requestgebundene Writerschema und den Domänenvertrag geprüft, bevor ihre Records eine Entscheidung tragen.

Historische `legacy-state-v3`- und `structured-v1`-Läufe werden mit `UNSUPPORTED-PROTOCOL` fail-closed abgewiesen und weder migriert noch als Fallback verwendet. Meldet Resume eine fehlende, beschädigte oder zum Spiegel widersprüchliche Recordkette, notiere Lauf- und Record-ID, prüfe `.orchestrator/logs/` und stelle die zusammengehörigen Records oder den passenden Spiegel aus einer vertrauenswürdigen Sicherung wieder her. Bearbeite weder Records noch `state.json` manuell und erfinde keine Freigabe zur Umgehung des fail-closed Halts.

Einen formalen Einzelauftrag setzt du explizit fort:

```bash
run_task --resume --task-file task.md
```

Nur wenn der protokollierte Exitcode 4 tatsächlich eine menschliche Gate-Entscheidung verlangt — also nicht bei `bootstrap_check` — wird diese mit Begründung erteilt:

```bash
run_task --watch --resume \
  --approve-gate \
  --gate-rationale "Persistierten Gate-Grund und Fingerprint geprüft"
```

Liegen nach einem abgebrochenen Provideraufruf Teilergebnisse im Repository,
kann `QUOTA-RESUME-DIFF` anhalten. Prüfe Fingerprint und betroffene Pfade und
entscheide ausdrücklich mit `--approve-gate` oder `--reject-gate`; ein einfacher
Resume genügt dann nicht. Verwende die unveränderte Aufgabe im Einzelmodus,
da der Watch-Modus `--task-file` ignoriert:

```bash
run_task --task-file task.md --resume --approve-gate --gate-rationale "Fingerprint und Teilergebnisse geprüft"
```

Zum Ablehnen ersetze `--approve-gate` durch `--reject-gate` mit einer passenden
Begründung. Entferne Teilergebnisse nicht, um den gebundenen Fingerprint zu
umgehen, und bearbeite keinen State manuell.

Ein agentenlokaler Port-Bind- oder Browser-Sandboxfehler wird einmal automatisch an die Orchestrator-Validierung übergeben. Findings und Blocker verändern die konfigurierte Validierungsmatrix nicht — kein Befund kann sie erweitern oder anhalten.

Die Exitcodes 2 und 3 kennzeichnen Quota- beziehungsweise Agentenfehler. Eindeutig belegte, transiente Netzwerkfehler werden standardmäßig höchstens zweimal nach 5 beziehungsweise 10 Sekunden im exakt gleichen Rollenschritt wiederholt. Fachliche Agentenantworten werden dabei nicht als technische Diagnose interpretiert. Auth-, Runtime-, Output- und Prozessfehler halten weiterhin fortsetzbar an. Nach Wiederherstellung des Providers oder Programms wird derselbe Schritt fortgesetzt; eine andere Rolle wird nicht als Ersatz verwendet. Eine bereits vollständig ausgeführte rote Matrix wird nur mit `--retry-failed-validation` erneut ausgeführt.

Nicht fortsetzbare technische Fehler werden begrenzt wiederholt. Ist das Retry-Limit ausgeschöpft, liegt die Aufgabe als `.poison` unter `outbox/failed/`; die benachbarte Datei `.poison.error.json` hält Lauf-ID, letzten Step und die konkrete technische Ursache für Diagnose und Korrektur fest.

## 8. Abschluss prüfen

Ein vollständiger Erfolg endet mit Exitcode 0. Prüfe anschließend die lokalen Commits und den Arbeitsbaum:

```bash
git log --oneline --decorate -n 15
git status --short
```

Der Zielbranch enthält einen lokalen Plancommit, je einen Commit für jeden freigegebenen Slice und die abschließende Auditprojektion. Hat der Abnahmereview eine Folgeaufgabe erzeugt, wiederholt sich das für deren Slices auf demselben Branch. Arbeitsplan, Slice-Auditdokumente und Gesamtreview liegen unter den erzeugten Pfaden in `docs/internal/`. Die abgearbeitete Inbox-Datei liegt mit UTC-Zeitstempel unter `outbox/done/`. Nach befundfreier Abnahme ist standardmäßig der Basisbranch mit dem lokalen Merge ausgecheckt; der Zielbranch bleibt erhalten. Push, Pull Request, Release und Deployment bleiben bewusste nachgelagerte Benutzeraktionen.

Ein zulässiger `post-merge`-Hook läuft nach dem bestätigten Merge höchstens
600 Sekunden. Bei Timeout wird das Beenden seiner Prozessgruppe versucht;
Warnung und `termination_uncertain` werden protokolliert, der Merge bleibt bestehen.
Die Wirkung auf Windows-Kindprozesse über WSL-Interop oder `setsid`-Prozesse
außerhalb der Gruppe ist ungeprüft. Führen Sie längere Builds separat aus,
verwenden Sie einen kurzen Hook oder einen eigenen Überspringen-Schalter;
alternativ deaktivieren Sie den automatischen Merge. Regeln und Umgang mit
ungewissem Ausgang stehen in Abschnitt 2.10 der
[Einrichtung](docs/reference/einrichtung.md).
