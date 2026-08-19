# Schnellstart

Diese Anleitung beschreibt den normalen, vollständig automatischen Inbox-Ablauf. [README.md](README.md) enthält Konfiguration und CLI-Referenz. Hintergründe stehen im [Architektur- und Fachkonzept](docs/reference/architecture-and-domain-concept.md); die Produktpositionierung erläutert der [Marktvergleich](docs/reference/market-comparison.md).

## 1. Voraussetzungen prüfen

Benötigt werden Python 3.11 oder neuer, Git und authentifizierte Installationen aller drei Rollen-CLIs:

```bash
python3 --version
git --version
codex --version
claude --version
agy --version
```

Der Orchestrator läuft unter Linux, macOS oder WSL2. Natives Windows wird derzeit nicht unterstützt. Unter WSL2 sollte der native Befehl `agy` verwendet oder `agy.exe` explizit konfiguriert werden.

## 2. Zielrepository prüfen

Wechsle in das Repository, in dem die Änderung entstehen soll:

```bash
cd /path/to/target-repository
git status --short
mkdir -p inbox
```

Den Zielbranch musst du im Watch-Modus weder vorher anlegen noch auschecken. Der Orchestrator liest ihn aus der Inbox-Datei und verhält sich beim ersten Start so:

- Fehlt der Zielbranch, wird er vom aktuellen `HEAD` angelegt und aktiviert.
- Existiert er, ist aber nicht aktiv, wird zu ihm gewechselt, sofern der Arbeitsbaum sicher wechselbar ist.
- Ist er bereits aktiv, wird die Aufgabe ab seinem aktuellen `HEAD` fortgeführt; vorhandene Branch-Commits bleiben erhalten.
- Erfordert die Aufgabe einen Branchwechsel, während nicht ignorierte Arbeitsbaum- oder Indexänderungen vorliegen, stoppt der Orchestrator ohne Stash, Bereinigung oder Übernahme dieser Änderungen.
- Bei einem Resume bleibt der persistierte Zielbranch bindend; ein abweichender aktiver Branch führt zum `BRANCH-MISMATCH`-Gate.

Agenten selbst führen keine Branchoperationen aus. Der Orchestrator erstellt ausschließlich lokale, pfadgenau geprüfte Commits. Er pusht, mergt oder force-pusht niemals und schreibt die Historie nicht um.

## 3. Eine Idee in die Inbox legen

Erstelle eine beschreibend benannte Markdown-Datei, beispielsweise:

```bash
nano inbox/meine-idee.md
```

Für den normalen Ablauf genügen eine menschlich formulierte Idee und der Zielbranch:

```markdown
# Meine Idee

TARGET_BRANCH: feature/mein-vorhaben

Ich möchte zwei Varianten verständlich miteinander vergleichen können.
Bitte untersuche zuerst die bestehende Anwendung. Frage nur nach, wenn eine
echte Produktentscheidung zu unterschiedlichen Ergebnissen führen würde.
```

Du musst keine Pfade, Slices, Akzeptanzkriterien, Risiken oder Tests vorgeben. Fehlen formale Ausführungsmarker und ein Scope-Abschnitt, leitet der Orchestrator sicher einen `PLAN_ONLY`-Auftrag ab. Aus `meine-idee.md` entsteht der Arbeitsplan `docs/internal/meine-idee-arbeitsplan.md`; zunächst ist nur dieser Planpfad beschreibbar. Codex übersetzt die Idee anhand des Repositorys in einen konkreten, von Claude und Antigravity geprüften Arbeitsplan. Direkter Implementierungsscope wird niemals aus freier Prosa geraten.

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
3. Codex erstellt den Arbeitsplan. Der Orchestrator prüft dessen Slice-/Pfadvertrag noch vor den Reviewern und gibt eine reparierbare Strukturabweichung automatisch genau einmal an Codex zurück. Erst der handoff-fähige Planfingerprint geht an Claude und Antigravity.
4. Nach beiden Planfreigaben commitet der Orchestrator den Plan lokal und erzeugt automatisch die zugehörige `-implement.md`-Aufgabe.
5. Derselbe Watch-Prozess übernimmt den Handoff unmittelbar und beginnt ohne zweite Planungsrunde mit Slice 1.
6. Zu Beginn jedes Slices entsteht dessen Auditdokument. Codex implementiert, der Orchestrator validiert, Claude und Antigravity reviewen und der Orchestrator erstellt den lokalen Slice-Commit.
7. Technische Korrekturen an bereits freigegebenen Vorgängerslices können über eine eng geprüfte `REMEDIATION_PATHS`-Erweiterung automatisch in den laufenden Slice aufgenommen werden.
8. Nach dem letzten Slice prüfen Codex, Claude und Antigravity den vollständigen Branch. Claude und Antigravity müssen dabei alle eigenen offenen Findings schließen oder als Blocker in einen automatischen, begrenzten Korrekturslice geben. Neue bloße Observations sind im Abschlussreview nicht zulässig; nicht umsetzungsrelevante Restrisiken gehören in die Reviewevidenz.
9. Erst wenn kein Finding mehr offen ist, endet der Lauf mit Exitcode 0 und die ursprüngliche Aufgabe wird nach `outbox/done/` verschoben.

Plan-, Teständerungs- und Slice-Commit-Gates sind standardmäßig aus. Echte Produktentscheidungen, unbekannte Pfade, Scopeverletzungen, nicht verfügbare Pflichtwerkzeuge, rote Pflichtvalidierungen und Provider-/Quota-Probleme können weiterhin sicher anhalten.

Claude läuft standardmäßig mit Sonnet und Effort `high`; Antigravity verwendet das konfigurierte Flash-Modell als unabhängiger zweiter Reviewer. Die Modelle ändern nichts an der Rollen- und Freigabereihenfolge.

Jede Logzeile trägt einen lokalen Zeitstempel. Während längerer Agentenaufrufe erscheint regelmäßig `<rolle> still running (elapsed: …)`; im Compact-Modus werden am Ende nur Findings, Entscheidungen, Status und eine kurze Nutzungssumme hervorgehoben.

## 6. Optionale manuelle und formale Betriebsarten

Eine menschliche Planabnahme ist opt-in:

```bash
run_task --watch --plan-gate
```

Entsprechend aktivieren `--test-change-gate` eine zusätzliche Abnahme für Teständerungen und `--manual-slice-gate` eine Abnahme vor jedem Slice-Commit. Ohne diese Optionen bleiben Validierung sowie Claude- und Antigravity-Reviews vollständig verpflichtend; nur der zusätzliche menschliche Halt entfällt.

Für bereits ausgearbeitete, maschinell erzeugte oder bewusst getrennt ausgeführte Aufträge bleiben formale Dateien unterstützt. [example-plan-task.md](example-plan-task.md) zeigt `ORCHESTRATOR_MODE: PLAN_ONLY`; [example-task.md](example-task.md) zeigt `ORCHESTRATOR_MODE: IMPLEMENT`, `TARGET_BRANCH`, `TASK_SCOPE`, Akzeptanzkriterien und Stopbedingungen.

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

Für neu gestartete Workflows liegt die technische Wahrheit der abgebildeten Entscheidungen unter `.orchestrator/artifacts/<run-id>/records/`. `state.json` und Checkpoints sind der geprüfte Betriebsspiegel, `head.json` ist nur ein rekonstruierbarer Cache, und die Markdown-Dateien unter `docs/internal/` sind menschenlesbare Auditansichten. Agentenmarker werden weiterhin als Eingabe geparst, steuern aber erst nach validierter Record-Persistenz und semantischem Vergleich eine Entscheidung.

Alte Läufe ohne Protokollbindung bleiben im Modus `legacy-state-v3`; sie werden weder still migriert noch benötigen sie nachträglich Records. Ein `structured-v1`-Lauf fällt dagegen niemals auf Legacy oder Markdown zurück. Meldet Resume eine fehlende, beschädigte oder zum Spiegel widersprüchliche Recordkette, notiere Lauf- und Record-ID, prüfe `.orchestrator/logs/` und stelle die zusammengehörigen Records oder den passenden Spiegel aus einer vertrauenswürdigen Sicherung wieder her. Bearbeite weder Records noch `state.json` manuell und erfinde keine Freigabe zur Umgehung des fail-closed Halts.

Einen formalen Einzelauftrag setzt du explizit fort:

```bash
run_task --resume --task-file task.md
```

Nur wenn der protokollierte Exitcode 4 tatsächlich eine menschliche Gate-Entscheidung verlangt, wird diese mit Akteur und Begründung erteilt:

```bash
run_task --watch --resume \
  --approve-gate \
  --gate-actor "Ihr Name" \
  --gate-rationale "Persistierten Gate-Grund und Fingerprint geprüft"
```

Ein agentenlokaler Port-Bind- oder Browser-Sandboxfehler wird einmal automatisch an die Orchestrator-Validierung übergeben. Ein `VALIDATE:`-Befehl an einer nicht blockierenden `OBSERVATION` erzeugt höchstens eine Warnung und keinen Benutzerhalt. Nur offene `BLOCKER` dürfen innerhalb einer konfigurierten Befehlsfamilie zusätzliche Validierung anfordern.

Die Exitcodes 2 und 3 kennzeichnen Quota- beziehungsweise Agentenfehler. Eindeutig belegte, transiente Netzwerkfehler werden standardmäßig höchstens zweimal nach 5 beziehungsweise 10 Sekunden im exakt gleichen Rollenschritt wiederholt. Fachliche Agentenantworten werden dabei nicht als technische Diagnose interpretiert. Auth-, Runtime-, Output- und Prozessfehler halten weiterhin fortsetzbar an. Nach Wiederherstellung des Providers oder Programms wird derselbe Schritt fortgesetzt; eine andere Rolle wird nicht als Ersatz verwendet. Eine bereits vollständig ausgeführte rote Matrix wird nur mit `--retry-failed-validation` erneut ausgeführt.

Nicht fortsetzbare technische Fehler werden begrenzt wiederholt. Ist das Retry-Limit ausgeschöpft, liegt die Aufgabe als `.poison` unter `outbox/failed/`; die benachbarte Datei `.poison.error.json` hält Lauf-ID, letzten Step und die konkrete technische Ursache für Diagnose und Korrektur fest.

## 8. Abschluss prüfen

Ein vollständiger Erfolg endet mit Exitcode 0. Prüfe anschließend die lokalen Commits und den Arbeitsbaum:

```bash
git log --oneline --decorate -n 15
git status --short
```

Der Zielbranch enthält einen lokalen Plancommit, die freigegebenen Slice- und gegebenenfalls Korrekturcommits sowie die abschließende Auditprojektion. Arbeitsplan, Slice-Auditdokumente und Gesamtreview liegen unter den erzeugten Pfaden in `docs/internal/`. Die abgearbeitete Inbox-Datei liegt mit UTC-Zeitstempel unter `outbox/done/`. Push, Pull Request, Merge, Release und Deployment bleiben bewusste nachgelagerte Benutzeraktionen.
